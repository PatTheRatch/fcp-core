"""Signing in by email link, and signing out.

    POST /auth/sign-in  {email, next?}  mail a one-time link (202, whatever happens)
    GET  /auth/callback?token=          spend it: a session cookie, then a redirect
    POST /auth/sign-out                 revoke this browser's session
    GET  /auth/me                       who this is, their leagues, their entitlement
    GET  /sign-in                       the form
    GET  /                              where a signed-in browser lands

No password is stored anywhere (docs/product.md, "Decided"). The link is
mailed by `app.notify.send_email` when SMTP is configured; when it is not,
which is development and the tests, it is logged at INFO and nothing else.
It is never in a response: whoever asks for a link has to be able to read
that mailbox, or the link proves nothing.

The link is built on `FCP_PUBLIC_URL`, never on the request's Host header,
which the caller writes: a link built on it could be pointed at a host of
the caller's choosing and mailed to somebody else. Without `FCP_PUBLIC_URL`
no link is mailed at all, only logged (dev).

The cookie is `fcp_session`: HttpOnly (no script reads it), SameSite=Lax
(no cross-site POST carries it, which is the CSRF defence for the few
routes that write), Secure whenever the site is served over https.
"""

import logging
import smtplib
import threading
import time
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import accounts
from app.api import access
from app.api.access import (
    COOKIE,
    CurrentUser,
    PageViewer,
    SettingsDep,
    Viewer,
    owner_email,
)
from app.api.deps import SessionDep
from app.config import Settings
from app.db.models import League, User
from app.notify import send_email

log = logging.getLogger("fcp.auth")

router = APIRouter(tags=["accounts"])

STATIC = Path(__file__).parent / "static"

#: What every sign-in request hears, sent or not, known address or new: the
#: response says nothing about who has an account.
ASKED = "If that address can sign in, a link is on its way. It works once, for 15 minutes."
BAD_LINK = "That link has expired or was already used."
SUBJECT = "Your FCP sign-in link"


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------


class TokenBucket:
    """A per-key token bucket, in this process's memory.

    Enough for one API process behind one proxy, which is what there is: the
    limits reset on a restart and are not shared between processes, and that
    is acceptable for slowing down someone hammering the sign-in form. If the
    API ever runs as several processes, this moves to the database or Redis.
    """

    def __init__(
        self, capacity: float, per_second: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.capacity = capacity
        self.per_second = per_second
        self.clock = clock
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            at = self.clock()
            tokens, then = self._state.get(key, (self.capacity, at))
            tokens = min(self.capacity, tokens + (at - then) * self.per_second)
            allowed = tokens >= 1
            self._state[key] = (tokens - 1 if allowed else tokens, at)
            if len(self._state) > 10_000:
                self._prune(at)
            return allowed

    def _prune(self, at: float) -> None:
        """Forget every key whose bucket has refilled: it would start full anyway."""
        full = [
            key
            for key, (tokens, then) in self._state.items()
            if tokens + (at - then) * self.per_second >= self.capacity
        ]
        for key in full:
            del self._state[key]


class SignInLimits:
    """Per address: five links, then one a minute. Per client address: twenty,
    then one every ten seconds. Both have to allow a request."""

    def __init__(self) -> None:
        self.by_email = TokenBucket(capacity=5, per_second=1 / 60)
        self.by_ip = TokenBucket(capacity=20, per_second=1 / 10)

    def allow(self, email: str, ip: str) -> bool:
        # Both are charged, so a burst on one address still costs its IP.
        email_ok = self.by_email.allow(email)
        ip_ok = self.by_ip.allow(ip)
        return email_ok and ip_ok


def limits(request: Request) -> SignInLimits:
    """The app's limiter, made on first use so each app (and test) has its own."""
    state = request.app.state
    if not hasattr(state, "sign_in_limits"):
        state.sign_in_limits = SignInLimits()
    found: SignInLimits = state.sign_in_limits
    return found


# ---------------------------------------------------------------------------
# signing in
# ---------------------------------------------------------------------------


class SignInIn(BaseModel):
    email: str = Field(max_length=accounts.MAX_EMAIL + 20)
    next: str | None = Field(default=None, max_length=2000)


class SignInOut(BaseModel):
    detail: str


def _link(settings: Settings, request: Request, token: str) -> str:
    base = settings.fcp_public_url or str(request.base_url)
    return f"{base.rstrip('/')}/auth/callback?token={token}"


def _secure(settings: Settings, request: Request) -> bool:
    return request.url.scheme == "https" or (settings.fcp_public_url or "").startswith("https://")


@router.post("/auth/sign-in", status_code=202, summary="Mail a one-time sign-in link")
def sign_in(
    body: SignInIn, request: Request, session: SessionDep, settings: SettingsDep
) -> SignInOut:
    email = accounts.normalise_email(body.email)
    if email is None:
        raise HTTPException(status_code=422, detail="that is not an email address")
    ip = request.client.host if request.client else "unknown"
    if not limits(request).allow(email, ip):
        raise HTTPException(status_code=429, detail="too many sign-in links; wait a minute")

    user = accounts.get_or_create_user(session, email)
    token = accounts.issue_sign_in_token(session, user, body.next)
    session.commit()
    link = _link(settings, request, token)

    if settings.smtp_configured and settings.fcp_public_url:
        try:
            send_email(
                "Sign in to FCP:\n\n"
                f"{link}\n\n"
                "The link works once, for 15 minutes. If you did not ask for it, "
                "ignore this email and nothing happens.\n",
                host=str(settings.fcp_smtp_host),
                port=settings.fcp_smtp_port,
                sender=str(settings.fcp_email_from),
                recipients=[email],
                subject=SUBJECT,
                user=settings.fcp_smtp_user,
                password=settings.fcp_smtp_password,
            )
        except (smtplib.SMTPException, OSError) as error:
            # The exception's class only: its text can quote the server's
            # reply, and nothing about the link or the login belongs in a log.
            log.error("sign-in link for %s not sent: %s", email, type(error).__name__)
            raise HTTPException(
                status_code=503, detail="the sign-in email could not be sent"
            ) from None
    elif settings.smtp_configured:
        log.error("FCP_PUBLIC_URL is not set, so no sign-in link is mailed")
        raise HTTPException(status_code=503, detail="sign-in by email is not set up")
    else:
        # Development and tests only: SMTP is not configured, so the link is
        # logged for whoever runs the server. The one place a token is written
        # anywhere but the database's hash column (docs/accounts.md).
        log.info("sign-in link for %s (SMTP not configured): %s", email, link)
    return SignInOut(detail=ASKED)


def _bad_link() -> HTMLResponse:
    return HTMLResponse(
        _shell(
            f"<p class='sub'>{escape(BAD_LINK)}</p>"
            "<p class='sub'><a href='/sign-in'>Ask for a new one</a></p>"
        ),
        status_code=400,
    )


@router.get("/auth/callback", summary="Spend a sign-in link: a session, then a redirect")
def callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    token: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    redeemed = accounts.redeem_sign_in_token(session, token) if token else None
    if redeemed is None:
        session.rollback()
        return _bad_link()
    cookie = accounts.start_session(session, redeemed.user_id)
    session.commit()
    user = session.get(User, redeemed.user_id)
    if user is not None and settings.fcp_owner_email and user.email == owner_email(settings):
        # The owner's claims catch up with any season ingested since.
        accounts.ensure_owner(
            session, user.email, settings.espn_league_id, settings.fcp_tracked_team_id
        )
    response = RedirectResponse(redeemed.next_path or "/", status_code=303)
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=int(accounts.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_secure(settings, request),
        path="/",
    )
    return response


@router.post("/auth/sign-out", summary="Revoke this browser's session")
def sign_out(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    """Open to anyone: it acts only on the caller's own cookie, if there is one."""
    cookie = request.cookies.get(COOKIE)
    revoked = accounts.revoke_session(session, cookie) if cookie else False
    session.commit()
    wants_page = "text/html" in request.headers.get("accept", "")
    response: Response = (
        RedirectResponse("/sign-in", status_code=303)
        if wants_page
        else JSONResponse({"signed_out": revoked})
    )
    response.delete_cookie(
        COOKIE, path="/", httponly=True, samesite="lax", secure=_secure(settings, request)
    )
    return response


# ---------------------------------------------------------------------------
# who this is
# ---------------------------------------------------------------------------


class ManagedTeamOut(BaseModel):
    season: int
    espn_team_id: int
    name: str
    state: str


class MemberLeagueOut(BaseModel):
    espn_league_id: int
    teams: list[ManagedTeamOut]


class EntitlementOut(BaseModel):
    tier: str
    source: str
    valid_until: str | None
    #: Whether the paywall is on at all (`app.api.access.BILLING_ENABLED`).
    billing_enabled: bool


class MeOut(BaseModel):
    email: str
    mode: str
    via: str
    owner: bool
    leagues: list[MemberLeagueOut]
    entitlement: EntitlementOut | None


def _leagues(session: Session, viewer: Viewer) -> list[MemberLeagueOut]:
    if viewer.user_id is None:
        return []
    out: dict[int, list[ManagedTeamOut]] = {}
    for team in accounts.managed_teams(session, viewer.user_id):
        out.setdefault(team.espn_league_id, []).append(
            ManagedTeamOut(
                season=team.season,
                espn_team_id=team.espn_team_id,
                name=team.name,
                state=team.state,
            )
        )
    return [MemberLeagueOut(espn_league_id=lid, teams=teams) for lid, teams in out.items()]


@router.get("/auth/me", summary="Who is signed in, their leagues and their entitlement")
def me(viewer: CurrentUser, session: SessionDep, settings: SettingsDep) -> MeOut:
    held = (
        accounts.active_entitlement(session, viewer.user_id) if viewer.user_id is not None else None
    )
    return MeOut(
        email=viewer.email,
        mode=settings.fcp_auth_mode,
        via=viewer.via,
        owner=viewer.is_owner,
        leagues=_leagues(session, viewer),
        entitlement=EntitlementOut(
            tier=held.tier,
            source=held.source,
            valid_until=held.valid_until.isoformat() if held.valid_until else None,
            billing_enabled=access.BILLING_ENABLED,
        )
        if held is not None
        else None,
    )


# ---------------------------------------------------------------------------
# the two pages
# ---------------------------------------------------------------------------

SHELL = """<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FCP</title><link rel="stylesheet" href="/pages/static/pages.css"></head>
<body><main class="page"><header class="mast"><p class="eyebrow">Full Court Press</p>
<h1>FCP</h1>{body}</header></main></body></html>
"""


def _shell(body: str) -> str:
    return SHELL.format(body=body)


@router.get("/sign-in", include_in_schema=False, response_class=HTMLResponse)
def sign_in_page() -> HTMLResponse:
    """The form. Read per request, like the other pages, so an edit shows on a refresh."""
    return HTMLResponse((STATIC / "sign-in.html").read_text())


@router.get("/", include_in_schema=False, response_class=HTMLResponse)
def home(viewer: PageViewer, session: SessionDep) -> HTMLResponse:
    """Where a signed-in browser lands: its leagues, and its own team's pages.

    A placeholder for step 3's shell (docs/product.md, "Navigation"): enough
    that the redirect after signing in lands somewhere true.
    """
    rows: list[str] = []
    if viewer.all_access:
        leagues = session.scalars(
            select(League).options(selectinload(League.seasons)).order_by(League.espn_league_id)
        ).all()
        for league in leagues:
            if league.seasons:
                latest = max(s.season for s in league.seasons)
                rows.append(_league_line(int(league.espn_league_id), latest, None))
    elif viewer.user_id is not None:
        seen: set[int] = set()
        for team in accounts.managed_teams(session, viewer.user_id):
            if team.state == accounts.VERIFIED and team.espn_league_id not in seen:
                seen.add(team.espn_league_id)
                rows.append(_league_line(team.espn_league_id, team.season, team))
    listing = (
        "".join(rows)
        if rows
        else "<p class='sub'>No league yet. A league's pages open once your team in it "
        "is verified.</p>"
    )
    return HTMLResponse(
        _shell(
            f"<p class='sub'>Signed in as <b>{escape(viewer.email)}</b>.</p>{listing}"
            "<form method='post' action='/auth/sign-out' class='tools'>"
            "<button class='btn' type='submit'>Sign out</button></form>"
        )
    )


def _league_line(league_id: int, season: int, team: accounts.ManagedTeam | None) -> str:
    base = f"/pages/teams/{league_id}/{season}"
    line = f"<p class='sub'><a href='{base}'>League {league_id}, {season}</a>"
    if team is not None:
        mine = f"{base}/{team.espn_team_id}"
        line += (
            f" · {escape(team.name)}: <a href='{mine}/week'>this week</a>, "
            f"<a href='{mine}/season'>the season</a>"
        )
    return line + "</p>"
