"""The site: one URL map, one shell over every page (step 3 of docs/product.md).

    /                                         the landing page signed out; signed in,
                                              the viewer's default league's This week
    /l/{league_id}/{season}/week              This week: every matchup, the score so far
    /l/{league_id}/{season}/standings         records, category records, streaks
    /l/{league_id}/{season}/draft             the board in pick order, and its value
    /l/{league_id}/{season}/history           the narratives, this season and all of them
    /l/{league_id}/{season}/team/{team_id}/week     the week plan
    /l/{league_id}/{season}/team/{team_id}/season   the season plan
    /l/{league_id}/{season}/team/{team_id}/moves    the scorecard of this team's own moves
    /account/connections | /projections | /alerts   the viewer's own account
    GET /me/alerts                            the one JSON route the pages needed

Every page is a file in `app/api/static/`, read per request like the in-season
pages always were, and every page draws the same shell (`shell.js`: the league
switcher, the sections, My team, the account menu), fed by `/auth/me` and
`/leagues`. docs/site.md has the whole map and what each page reads.

WHO MAY OPEN WHAT

The league pages are the free tier: a member of this league
(`require_league_member_page`). The team pages are the paid team layer: this
team's verified manager, and entitled (`require_team_plan_page`), which
answers yes for everyone until billing (docs/accounts.md). The account pages
are anyone signed in. `/` is open, because it is the landing page for someone
who is not signed in; it asks who is there itself and answers with one page or
the other, never with anybody's data.

The old URLs (`/pages/teams/...`, `/pages/connections`) redirect here, keeping
the query (`?today=`, `?me=`), and keep the check they always had, so a
stranger is refused at the old address exactly as at the new one.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api.access import (
    LEAGUE_MEMBER_PAGE,
    SIGNED_IN_PAGE,
    TEAM_PLAN_PAGE,
    CurrentUser,
    SettingsDep,
    resolve_viewer,
)
from app.api.deps import LeagueIdPath, SeasonPath, SessionDep, TeamIdPath

router = APIRouter(tags=["site"])

STATIC = Path(__file__).parent / "static"

#: A moved page answers 308: permanent, and the method is kept (they are all GETs).
MOVED = 308


def _page(name: str) -> HTMLResponse:
    """One page, read from disk per request so an edit shows on a refresh."""
    return HTMLResponse((STATIC / name).read_text())


def _moved(to: str, request: Request) -> RedirectResponse:
    """A redirect to the page's new address, with the query it came with."""
    query = request.url.query
    return RedirectResponse(f"{to}?{query}" if query else to, status_code=MOVED)


# ---------------------------------------------------------------------------
# the front door
# ---------------------------------------------------------------------------


@router.get("/", include_in_schema=False, response_class=HTMLResponse)
def home(request: Request, session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    """Signed out, what the product is and a way in. Signed in, a page whose
    shell finds the viewer's default league (the one this browser last looked
    at, else his first) and goes to its This week, or says he has none yet.

    Open, so it declares no check; it asks who is there and serves one of two
    files, neither of which carries any data.
    """
    viewer = resolve_viewer(request, session, settings)
    return _page("landing.html" if viewer is None else "home.html")


# ---------------------------------------------------------------------------
# the league pages: the free tier, every member of the league
# ---------------------------------------------------------------------------


@router.get(
    "/l/{league_id}/{season}/week",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def league_week_page() -> HTMLResponse:
    """Every matchup of the period, its nine categories so far, days left."""
    return _page("league-week.html")


@router.get(
    "/l/{league_id}/{season}/standings",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def league_standings_page() -> HTMLResponse:
    """Matchup records, category records and streaks."""
    return _page("league-standings.html")


@router.get(
    "/l/{league_id}/{season}/draft",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def league_draft_page() -> HTMLResponse:
    """The draft board in pick order with prices, and its value."""
    return _page("league-draft.html")


@router.get(
    "/l/{league_id}/{season}/history",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def league_history_page() -> HTMLResponse:
    """Category profiles, notable matchups, owners' records, head to head."""
    return _page("league-history.html")


# ---------------------------------------------------------------------------
# the team pages: the paid team layer, this team's manager
# ---------------------------------------------------------------------------


@router.get(
    "/l/{league_id}/{season}/team/{team_id}/week",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def team_week_page() -> HTMLResponse:
    """The streaming report for one team, as a page."""
    return _page("week.html")


@router.get(
    "/l/{league_id}/{season}/team/{team_id}/season",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def team_season_page() -> HTMLResponse:
    """The rest-of-season report for one team, as a page."""
    return _page("season.html")


@router.get(
    "/l/{league_id}/{season}/team/{team_id}/moves",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def team_moves_page() -> HTMLResponse:
    """The scorecard's view of this team's own moves: the wire, trades, the draft."""
    return _page("moves.html")


# ---------------------------------------------------------------------------
# the account pages: anyone signed in, about himself
# ---------------------------------------------------------------------------


@router.get(
    "/account/connections",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[SIGNED_IN_PAGE],
)
def connections_page() -> HTMLResponse:
    """Connect a league; your connections; your leagues' invites and claims."""
    return _page("connections.html")


@router.get(
    "/account/projections",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[SIGNED_IN_PAGE],
)
def projections_page() -> HTMLResponse:
    """Your uploaded projection sets, and how to upload one."""
    return _page("projections.html")


@router.get(
    "/account/alerts",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[SIGNED_IN_PAGE],
)
def alerts_page() -> HTMLResponse:
    """Where the digest and its alerts go: the server's own channels (the
    owner's), and the member's own, which he adds, verifies and disables here."""
    return _page("alerts.html")


class AlertChannelOut(BaseModel):
    kind: str = Field(description="'email', 'telegram' or 'ntfy'")
    detail: str = Field(description="Where it goes, in words; never a URL, token or chat id")


class AlertsOut(BaseModel):
    """The configured channels, for the one person they belong to."""

    yours: bool = Field(description="Whether the digest's channels are this viewer's")
    channels: list[AlertChannelOut]
    per_member: bool = Field(
        description="True since step 4: each member also sets his own channels, /me/channels"
    )


@router.get("/me/alerts", summary="Where the digest and its alerts are delivered, read-only")
def my_alerts(viewer: CurrentUser, settings: SettingsDep) -> AlertsOut:
    """The server's own digest channels, shown only to the owner they are for.

    The owner's digest goes to the channels configured in the server's
    environment (`app/notify.py`), as it always has. The owner sees which
    channels those are and the addresses it is mailed to, which are his own;
    the URL channel is named by its kind only, because its URL (an ntfy
    topic, a Telegram bot token) is the credential. Anyone else has none of
    these. Every member, the owner too, also has his own channels
    (`/me/channels`, step 4), which the Alerts page lists beside these.
    """
    if not viewer.is_owner:
        return AlertsOut(yours=False, channels=[], per_member=True)
    channels: list[AlertChannelOut] = []
    if settings.email_configured:
        channels.append(AlertChannelOut(kind="email", detail=", ".join(settings.email_recipients)))
    if settings.fcp_digest_url:
        if settings.fcp_digest_chat_id:
            channels.append(AlertChannelOut(kind="telegram", detail="a Telegram chat"))
        else:
            channels.append(AlertChannelOut(kind="ntfy", detail="an ntfy topic"))
    return AlertsOut(yours=True, channels=channels, per_member=True)


# ---------------------------------------------------------------------------
# the old addresses, kept as redirects with the check they always had
# ---------------------------------------------------------------------------


@router.get(
    "/pages/teams/{league_id}/{season}",
    include_in_schema=False,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def old_index(league_id: LeagueIdPath, season: SeasonPath, request: Request) -> RedirectResponse:
    """The old every-team index is the standings now."""
    return _moved(f"/l/{league_id}/{season}/standings", request)


@router.get(
    "/pages/teams/{league_id}/{season}/{team_id}/week",
    include_in_schema=False,
    dependencies=[TEAM_PLAN_PAGE],
)
def old_week(
    league_id: LeagueIdPath, season: SeasonPath, team_id: TeamIdPath, request: Request
) -> RedirectResponse:
    return _moved(f"/l/{league_id}/{season}/team/{team_id}/week", request)


@router.get(
    "/pages/teams/{league_id}/{season}/{team_id}/season",
    include_in_schema=False,
    dependencies=[TEAM_PLAN_PAGE],
)
def old_season(
    league_id: LeagueIdPath, season: SeasonPath, team_id: TeamIdPath, request: Request
) -> RedirectResponse:
    return _moved(f"/l/{league_id}/{season}/team/{team_id}/season", request)


@router.get("/pages/connections", include_in_schema=False, dependencies=[SIGNED_IN_PAGE])
def old_connections(request: Request) -> RedirectResponse:
    return _moved("/account/connections", request)
