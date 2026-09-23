"""Who is asking, and whether they may: the checks every route declares.

Step 1 of docs/product.md. Every route in the API declares exactly one of
these (docs/accounts.md has the table, and tests/test_access.py fails if a
route is added without one):

* `current_user` (`CurrentUser`): signed in. The account routes, the
  projection sets (each readable only by its owner), ingest health, and the
  NBA-wide player routes.
* `require_league_member`: a member of this league (`memberships`: whoever
  connected it, or accepted an invite into it). The league pages:
  standings, matchups, narratives, the draft, transactions, every team's
  scorecard, the listener's events; and making a claim on a team.
* `require_league_owner`: an `owner` member of this league. Its invites and
  the approval of its members' team claims (docs/accounts.md).
* `require_team_plan`: `require_team_manager` (a verified manager of this
  very team) and then `require_entitlement` (the paid tier). The team layer:
  the pickup reports and the week and season pages.
* The `*_page` twins of the above, for the HTML pages: the same answers, but
  a signed-out browser is sent to /sign-in and a refusal is a line of HTML.

THE TWO MODES

`FCP_AUTH_MODE=single`, the default, is the tailnet API as it has always
been. Every request is the owner (`FCP_OWNER_EMAIL`), no cookie is needed,
and every check answers yes: the owner is an operator who may read any
team's plan, as Patrick has always been able to. The owner's rows are
written all the same (`app.accounts.ensure_owner`), so /auth/me tells the
truth and the switch to accounts mode finds them already there.

`FCP_AUTH_MODE=accounts` enforces everything. A request is signed in by the
`fcp_session` cookie, by `Authorization: Bearer <a manager's own token>`
(`app/api_tokens.py`: minted on the account page, prefixed `bo_`, and
carrying no scope of its own -- it is that manager, through these same
checks), or by `Authorization: Bearer <FCP_SERVICE_TOKEN>`, which is the
owner for the scheduled scripts. In this mode the owner is an ordinary user
with the owner's claims: his own team's plan, not everyone's.

THE ENTITLEMENT

`BILLING_ENABLED` is False until step 7, and while it is,
`require_entitlement` answers yes for everyone: the same seam
`viewer_owns_source` is for the projection gate. Turning the paywall on is
this constant and a payment provider writing `entitlements`, not a change
to any route.
"""

import logging
from dataclasses import dataclass
from html import escape
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app import accounts, api_tokens, brand
from app.api.deps import LeagueIdPath, SeasonPath, SessionDep, TeamIdPath
from app.config import Settings, get_settings
from app.db.models import User

log = logging.getLogger("fcp.access")

#: The paywall. False until billing exists (docs/product.md step 7): while it
#: is, `require_entitlement` lets everyone through. Read at call time, so a
#: test can flip it.
BILLING_ENABLED = False

#: The session cookie's name.
COOKIE = "fcp_session"

SIGN_IN_FIRST = "sign in first"
NOT_A_MEMBER = "not a member of this league"
NOT_LEAGUE_OWNER = "only the league's owner may do that"
TEAM_REFUSED = "This team's plan is its manager's."
NOT_ENTITLED = "The team layer is part of the paid plan."

SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class Viewer:
    """Whoever this request is.

    `all_access` is single mode's owner, for whom nothing is checked.
    `user_id` is None only in single mode on a database the accounts
    migration has not reached yet, which is served as before rather than
    refused (docs/accounts.md, "Deploying").
    """

    user_id: int | None
    email: str
    is_owner: bool
    all_access: bool
    via: str


# ---------------------------------------------------------------------------
# resolving the viewer
# ---------------------------------------------------------------------------


def owner_email(settings: Settings) -> str:
    configured = accounts.normalise_email(settings.fcp_owner_email or "")
    return configured or accounts.OWNER_FALLBACK_EMAIL


def _owner(session: Session, settings: Settings) -> User:
    return accounts.ensure_owner(
        session, owner_email(settings), settings.espn_league_id, settings.fcp_tracked_team_id
    )


def _single(session: Session, settings: Settings) -> Viewer:
    email = owner_email(settings)
    try:
        user = _owner(session, settings)
    except ProgrammingError:
        # The accounts tables are not there: code deployed ahead of its
        # migration. Single mode checks nothing, so serve as before.
        session.rollback()
        log.warning("accounts tables missing; single mode serving without an owner row")
        return Viewer(None, email, is_owner=True, all_access=True, via="single")
    return Viewer(user.id, user.email, is_owner=True, all_access=True, via="single")


def resolve_viewer(request: Request, session: Session, settings: Settings) -> Viewer | None:
    """The viewer, or None when the request is not signed in."""
    if settings.fcp_auth_mode == "single":
        return _single(session, settings)

    authorization = request.headers.get("authorization", "")
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() == "bearer":
        # A bearer that is neither a live token of a member's nor the service
        # token is refused outright rather than falling back to the cookie: a
        # script with a stale token should hear so, not quietly act as
        # whoever last used the browser.
        presented = presented.strip()
        if api_tokens.looks_like_one(presented):
            # A manager's own machine token. It carries no scope: it is him,
            # through the same checks below (app/api_tokens.py, docs/mcp.md).
            holder = api_tokens.user_for_token(session, presented)
            if holder is None:
                return None
            owns = settings.fcp_owner_email is not None and holder.email == owner_email(settings)
            return Viewer(holder.id, holder.email, is_owner=owns, all_access=False, via="token")
        expected = settings.fcp_service_token
        if expected and presented and accounts.same_secret(presented, expected):
            owner = _owner(session, settings)
            return Viewer(owner.id, owner.email, is_owner=True, all_access=False, via="service")
        return None

    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return None
    user = accounts.session_user(session, cookie)
    if user is None:
        return None
    is_owner = settings.fcp_owner_email is not None and user.email == owner_email(settings)
    return Viewer(user.id, user.email, is_owner=is_owner, all_access=False, via="session")


class SignInRequiredError(Exception):
    """A page asked for by someone signed out: send the browser to sign in."""

    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


class PageRefusedError(Exception):
    """A page this viewer may not open: one plain line, as HTML."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def current_user(request: Request, session: SessionDep, settings: SettingsDep) -> Viewer:
    """Signed in, or 401."""
    viewer = resolve_viewer(request, session, settings)
    if viewer is None:
        raise HTTPException(
            status_code=401, detail=SIGN_IN_FIRST, headers={"WWW-Authenticate": "Bearer"}
        )
    return viewer


def current_page_viewer(request: Request, session: SessionDep, settings: SettingsDep) -> Viewer:
    """Signed in, or a redirect to /sign-in that comes back here afterwards."""
    viewer = resolve_viewer(request, session, settings)
    if viewer is None:
        here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise SignInRequiredError(here)
    return viewer


CurrentUser = Annotated[Viewer, Depends(current_user)]
PageViewer = Annotated[Viewer, Depends(current_page_viewer)]


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------


def is_league_member(session: Session, viewer: Viewer, league_id: int) -> bool:
    """A member of this league (`memberships`, either role). A verified team
    claim alone is not membership: that comes from connecting the league or
    from an accepted invite."""
    if viewer.all_access:
        return True
    if viewer.user_id is None:
        return False
    return accounts.is_member(session, viewer.user_id, league_id)


def is_league_owner(session: Session, viewer: Viewer, league_id: int) -> bool:
    """An `owner` of this league: whoever connected it, or the configured
    owner in the tracked league."""
    if viewer.all_access:
        return True
    if viewer.user_id is None:
        return False
    return accounts.is_league_owner(session, viewer.user_id, league_id)


def is_team_manager(
    session: Session, viewer: Viewer, league_id: int, season: int, team_id: int
) -> bool:
    """Verified manager of this team this season. An unknown team is simply not
    one of his, so it is a 403 like any other and says nothing about whether
    the team exists."""
    if viewer.all_access:
        return True
    if viewer.user_id is None:
        return False
    return accounts.manages_team(session, viewer.user_id, league_id, season, team_id)


def is_entitled(session: Session, viewer: Viewer) -> bool:
    if not BILLING_ENABLED or viewer.all_access:
        return True
    if viewer.user_id is None:
        return False
    return accounts.active_entitlement(session, viewer.user_id) is not None


def require_league_member(
    league_id: LeagueIdPath, viewer: CurrentUser, session: SessionDep
) -> Viewer:
    """A member of this league; else 403."""
    if not is_league_member(session, viewer, league_id):
        raise HTTPException(status_code=403, detail=NOT_A_MEMBER)
    return viewer


def require_league_owner(
    league_id: LeagueIdPath, viewer: CurrentUser, session: SessionDep
) -> Viewer:
    """An owner of this league (its invites, its claims); else 403. A member
    who is not an owner hears the same 403 as a stranger."""
    if not is_league_owner(session, viewer, league_id):
        raise HTTPException(status_code=403, detail=NOT_LEAGUE_OWNER)
    return viewer


def require_team_manager(
    league_id: LeagueIdPath,
    season: SeasonPath,
    team_id: TeamIdPath,
    viewer: CurrentUser,
    session: SessionDep,
) -> Viewer:
    """A verified manager of this team in this season; else 403."""
    if not is_team_manager(session, viewer, league_id, season, team_id):
        raise HTTPException(status_code=403, detail=TEAM_REFUSED)
    return viewer


def require_entitlement(viewer: CurrentUser, session: SessionDep) -> Viewer:
    """Entitled to the paid tier; else 402. Yes for everyone while
    `BILLING_ENABLED` is False."""
    if not is_entitled(session, viewer):
        raise HTTPException(status_code=402, detail=NOT_ENTITLED)
    return viewer


def require_team_plan(
    manager: Annotated[Viewer, Depends(require_team_manager)],
    entitled: Annotated[Viewer, Depends(require_entitlement)],
) -> Viewer:
    """The team layer: this team's manager, and entitled. The one check the
    pickup routes declare; the manager is asked first, so a stranger hears
    403 about the team rather than 402 about his plan."""
    return manager


def require_listened_league_member(
    viewer: CurrentUser, session: SessionDep, settings: SettingsDep
) -> Viewer:
    """A member of the league the listener follows (`ESPN_LEAGUE_ID`).

    For the one route without a league in its path that still carries a
    league's facts: a player's status history says which team in that league
    held him (`on_team_id`). With no league configured, only the owner.
    """
    league_id = settings.espn_league_id
    allowed = viewer.is_owner if league_id is None else is_league_member(session, viewer, league_id)
    if not allowed:
        raise HTTPException(status_code=403, detail=NOT_A_MEMBER)
    return viewer


def require_league_member_page(
    league_id: LeagueIdPath, viewer: PageViewer, session: SessionDep
) -> Viewer:
    if not is_league_member(session, viewer, league_id):
        raise PageRefusedError(403, "This league's pages are its members'.")
    return viewer


def require_team_plan_page(
    league_id: LeagueIdPath,
    season: SeasonPath,
    team_id: TeamIdPath,
    viewer: PageViewer,
    session: SessionDep,
) -> Viewer:
    if not is_team_manager(session, viewer, league_id, season, team_id):
        raise PageRefusedError(403, TEAM_REFUSED)
    if not is_entitled(session, viewer):
        raise PageRefusedError(402, NOT_ENTITLED)
    return viewer


#: Every dependency that counts as a route's access check, for the test that
#: no route goes without one.
CHECKS = (
    current_user,
    current_page_viewer,
    require_league_member,
    require_league_owner,
    require_team_manager,
    require_entitlement,
    require_team_plan,
    require_listened_league_member,
    require_league_member_page,
    require_team_plan_page,
)

LEAGUE_MEMBER = Depends(require_league_member)
LEAGUE_OWNER = Depends(require_league_owner)
#: The same two checks as a parameter, for a route that wants the viewer
#: they let through: declaring `CurrentUser` beside them would be two checks.
LeagueMember = Annotated[Viewer, Depends(require_league_member)]
LeagueOwner = Annotated[Viewer, Depends(require_league_owner)]
SIGNED_IN_PAGE = Depends(current_page_viewer)
TEAM_PLAN = Depends(require_team_plan)
#: The manager check alone, without the paid tier: the free glance at his
#: own week (docs/product.md, "Free and paid").
TEAM_MANAGER = Depends(require_team_manager)
SIGNED_IN = Depends(current_user)
LISTENED_LEAGUE_MEMBER = Depends(require_listened_league_member)
LEAGUE_MEMBER_PAGE = Depends(require_league_member_page)
TEAM_PLAN_PAGE = Depends(require_team_plan_page)


# ---------------------------------------------------------------------------
# the page exceptions, as responses
# ---------------------------------------------------------------------------

REFUSED_PAGE = """<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{brand}</title><link rel="stylesheet" href="/pages/static/pages.css"></head>
<body><main class="page"><header class="mast"><p class="eyebrow">{brand}</p>
<p class="sub">{message}</p><p class="sub"><a href="/">Your leagues</a></p></header></main>
</body></html>
"""


def install(app: FastAPI) -> None:
    """Register the two page exceptions' responses on the app."""

    async def to_sign_in(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, SignInRequiredError)
        return RedirectResponse(f"/sign-in?next={quote(exc.next_path, safe='/')}", 303)

    async def refused(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, PageRefusedError)
        return HTMLResponse(
            REFUSED_PAGE.format(brand=escape(brand.BRAND), message=escape(exc.message)),
            status_code=exc.status_code,
        )

    app.add_exception_handler(SignInRequiredError, to_sign_in)
    app.add_exception_handler(PageRefusedError, refused)
