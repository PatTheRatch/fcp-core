"""Signing in by email link, and signing out.

    POST /auth/sign-in  {email, next?}  mail a one-time link (202, whatever happens)
    GET  /auth/callback?token=          spend it: a session cookie, then a redirect
    POST /auth/sign-out                 revoke this browser's session
    GET  /auth/me                       who this is, their leagues, their entitlement
    GET  /sign-in                       the form

`GET /`, the landing page and the signed-in home, is the site's (app/api/site.py).

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
from sqlalchemy.orm import Session

from app import accounts, brand
from app.api import access
from app.api.access import (
    COOKIE,
    CurrentUser,
    SettingsDep,
    Viewer,
    owner_email,
)
from app.api.deps import SessionDep
from app.config import Settings
from app.db.models import User
from app.mail import SIGN_IN_SUBJECT, sign_in_mail
from app.notify import send_email

log = logging.getLogger("fcp.auth")

router = APIRouter(tags=["accounts"])

STATIC = Path(__file__).parent / "static"

#: What every sign-in request hears, sent or not, known address or new: the
#: response says nothing about who has an account.
ASKED = "If that address can sign in, a link is on its way. It works once, for 15 minutes."
BAD_LINK = "That link has expired or was already used."
#: The subject the mail carries. This module has always named it; the mail
#: itself, both its parts, is `app.mail` now.
SUBJECT = SIGN_IN_SUBJECT


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
        mail = sign_in_mail(link, public_url=settings.fcp_public_url)
        try:
            send_email(
                mail.text,
                html=mail.html,
                host=str(settings.fcp_smtp_host),
                port=settings.fcp_smtp_port,
                sender=str(settings.fcp_email_from),
                recipients=[email],
                subject=mail.subject,
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
    #: `owner` or `member`; null for a league he has a claim in and is no
    #: longer a member of.
    role: str | None
    teams: list[ManagedTeamOut]


class EntitlementOut(BaseModel):
    tier: str
    source: str
    valid_until: str | None
    #: Whether the paywall is on at all (`FCP_BILLING_ENABLED`).
    billing_enabled: bool


class MeOut(BaseModel):
    email: str
    mode: str
    via: str
    owner: bool
    leagues: list[MemberLeagueOut]
    entitlement: EntitlementOut | None


def _leagues(session: Session, viewer: Viewer) -> list[MemberLeagueOut]:
    """His memberships, each with his claims in it, then any league he has a
    claim in without being a member."""
    if viewer.user_id is None:
        return []
    roles = dict(accounts.member_leagues(session, viewer.user_id))
    out: dict[int, list[ManagedTeamOut]] = {league: [] for league in roles}
    for team in accounts.managed_teams(session, viewer.user_id):
        out.setdefault(team.espn_league_id, []).append(
            ManagedTeamOut(
                season=team.season,
                espn_team_id=team.espn_team_id,
                name=team.name,
                state=team.state,
            )
        )
    return [
        MemberLeagueOut(espn_league_id=lid, role=roles.get(lid), teams=teams)
        for lid, teams in out.items()
    ]


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
            billing_enabled=access.billing_enabled(settings),
        )
        if held is not None
        else None,
    )


# ---------------------------------------------------------------------------
# the sign-in page, and the one line a bad link hears
# ---------------------------------------------------------------------------

SHELL = """<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{brand}</title><link rel="stylesheet" href="/pages/static/pages.css">
<link rel="icon" href="/pages/static/favicon.svg" type="image/svg+xml"></head>
<body><main class="page"><header class="mast"><p class="eyebrow">{brand}</p>
<h1>Sign in</h1>{body}</header></main></body></html>
"""


def _shell(body: str) -> str:
    return SHELL.format(brand=escape(brand.BRAND), body=body)


@router.get("/sign-in", include_in_schema=False, response_class=HTMLResponse)
def sign_in_page() -> HTMLResponse:
    """The form. Read per request, like the other pages, so an edit shows on a refresh."""
    return HTMLResponse(brand.fill((STATIC / "sign-in.html").read_text()))
