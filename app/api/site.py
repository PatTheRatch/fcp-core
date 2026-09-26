"""The site: one URL map, one shell over every page (step 3 of docs/product.md).

    /                                         the landing page signed out; signed in,
                                              his team's Overview in his default
                                              league, else that league's This week
    /l/{league_id}/{season}/week              This week: every matchup, the score so far
    /l/{league_id}/{season}/standings         records, category records, streaks
    /l/{league_id}/{season}/draft             the board in pick order, and its value
    /l/{league_id}/{season}/history           the narratives, this season and all of them
    /l/{league_id}/{season}/team/{team_id}          the Overview: the morning's briefing
    /l/{league_id}/{season}/team/{team_id}/week     the week plan
    /l/{league_id}/{season}/team/{team_id}/season   the season plan
    /l/{league_id}/{season}/team/{team_id}/moves    the scorecard of this team's own moves
    /l/{league_id}/{season}/team/{team_id}/trades   what a proposed trade would do to both
    /l/{league_id}/{season}/team/{team_id}/draft/plan   the pre-auction plan and his marks
    /account/connections | /projections | /alerts   the viewer's own account
    /design                                   the design language, open, no data in it
    GET /me/alerts                            the one JSON route the pages needed

Every page is a file in `app/api/static/`, read per request like the in-season
pages always were, and every page draws the same shell (`shell.js`: the rail
with the team's and the league's pages, the scenario, the league switcher and
the account; the scenario bar; the inspection drawer), fed by `/auth/me` and
`/leagues`. docs/site.md has the whole map and what each page reads, and
docs/design_system.md the language the shell is drawn in.

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

from app import brand
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
    """One page, read from disk per request so an edit shows on a refresh.

    The product's name is a token in the file and is filled in here, so the
    pages have one source for it (`app/brand.py`).
    """
    return HTMLResponse(brand.fill((STATIC / name).read_text()))


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
    at, else his first) and goes to his team's Overview there -- or, with no
    team claimed in it, to its This week -- or says he has none yet.

    Open, so it declares no check; it asks who is there and serves one of two
    files, neither of which carries any data.
    """
    viewer = resolve_viewer(request, session, settings)
    return _page("landing.html" if viewer is None else "home.html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    """The address a browser tries on its own before it has read a page's
    `<link rel="icon">`: sent to the one icon the pages declare."""
    return RedirectResponse("/pages/static/favicon.svg", status_code=MOVED)


@router.get("/design", include_in_schema=False, response_class=HTMLResponse)
def design_page() -> HTMLResponse:
    """The workstation's design language, drawn on a league's stored season.

    Open, so it declares no check, and the file carries no data: the tokens,
    the voices and the controls are drawn from the stylesheet, and every
    specimen that shows a number reads it from a league route behind that
    route's own check. Signed out, the page draws the language and says the
    specimens need a league (docs/design_system.md).
    """
    return _page("design.html")


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
    "/l/{league_id}/{season}/team/{team_id}",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def team_overview_page() -> HTMLResponse:
    """The team's home: the morning's briefing, drawn from routes that exist.

    The matchup, what asks for a decision today, the wire, a cut of the
    standings, tonight's lineup and the league's latest, each read from the
    route the team's other pages already read (docs/site.md, "Overview").
    """
    return _page("overview.html")


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


@router.get(
    "/l/{league_id}/{season}/team/{team_id}/trades",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def team_trades_page() -> HTMLResponse:
    """Build a trade and see what it does to both rosters' nine categories."""
    return _page("trades.html")


@router.get(
    "/l/{league_id}/{season}/team/{team_id}/draft/plan",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def team_draft_plan_page() -> HTMLResponse:
    """The pre-auction plan: the model's ladder, board and lists, and the
    manager's own figures, tags and notes beside them (docs/draft_plan.md).
    Its data route asks the projection source's gate as well as this one."""
    return _page("draft-plan.html")


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
    kind: str = Field(description="'email'; there is no other channel")
    detail: str = Field(description="Where it goes, in words")


class AlertsOut(BaseModel):
    """The configured channels, for the one person they belong to."""

    yours: bool = Field(description="Whether the digest's channels are this viewer's")
    channels: list[AlertChannelOut]
    per_member: bool = Field(
        description="True since step 4: each member also sets his own channels, /me/channels"
    )


@router.get("/me/alerts", summary="Where the digest and its alerts are delivered, read-only")
def my_alerts(viewer: CurrentUser, settings: SettingsDep) -> AlertsOut:
    """The server's own digest recipients, shown only to the owner they are for.

    The owner's digest goes to the addresses configured in the server's
    environment (`FCP_EMAIL_TO`, `app/notify.py`), as it always has, and
    those are his own so he is shown them in full. Anyone else has none of
    these. Every member, the owner too, also has his own channels
    (`/me/channels`, step 4), which the Alerts page lists beside these.
    """
    if not viewer.is_owner:
        return AlertsOut(yours=False, channels=[], per_member=True)
    channels: list[AlertChannelOut] = []
    if settings.email_configured:
        channels.append(AlertChannelOut(kind="email", detail=", ".join(settings.email_recipients)))
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
