"""The in-season pages: the week, the rest of the season, and an index.

Three pages, served as files from `app/api/static/` by the read-only API, in
the light skin the season report is written in. They are the CLI reports
(`scripts/stream.py`, `scripts/season.py`) as a page: everything those two
print, the page shows, in the same words. Plain HTML, CSS and JavaScript,
no framework and no build step, for the reason the draft screen has none --
a page that has to come up on a tailnet from a file on disk should not need
a toolchain -- and the files are read per request, so an edit shows on a
refresh (`docs/in_season_pages.md`).

WHAT THE PAGES FETCH

The two reports come from the routes that already exist, unchanged:

    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season

and everything else from one small route added here, `pages/context`: the
day being reported on as a date, the days of its matchup period for the
schedule strip, every team's name, which team is ours, and the line naming
where the numbers came from. The index also reads `/standings`, which
already derives a record from the stored matchups; it is not repeated here.

WHO MAY OPEN THEM

The week and season pages are the team layer: their manager's, and paid
once billing exists (`app.api.access.require_team_plan_page`). The index is
the league's (`require_league_member_page`). Signed out, a page redirects to
/sign-in and comes back; the script does the same on a 401 from a fetch and
says one plain line on a 403 (docs/accounts.md). In single mode, the
default, nothing is refused. The stylesheet and script are open: no data.

THE LANGUAGE

The tool generates ideas and the manager decides, so the pages say "worth a
look" and "nothing clears the bar" and never "recommended" or "do this".
A bar that nothing cleared is an answer, not an empty section.

THE PROJECTION GATE

Nothing on these pages is gated today, and the pages carry no per-player
projection number at all. Every figure beside a player is a count of games,
a start, or a probability derived from this season's own box scores and
ESPN's lines through `app.pickups.projection`, and ESPN's are ours to show
(`app.projections.sources.may_show`, `docs/projection_sources.md`). If a
page ever grows a per-player number from a gated source -- Basketball
Monster's, the only one -- it has to ask `may_show` first, exactly as the
draft screen's pool does, and degrade in place rather than withhold the
page. The seam is named here so the next person finds it.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import LEAGUE_MEMBER, LEAGUE_MEMBER_PAGE, TEAM_PLAN_PAGE
from app.api.deps import LeagueIdPath, LeagueSeasonDep, SessionDep
from app.api.schemas import PageContextOut, PageDayOut, PagePeriodOut, PageTeamOut
from app.db.models import LeagueSeason, MatchupPeriod, Team
from app.pickups.state import SeasonCalendar, period_for_day, season_calendar
from app.projections.sources import ESPN, describe

router = APIRouter(tags=["pages"])

#: Where the three pages and their shared stylesheet and script live.
STATIC = Path(__file__).parent / "static"

#: The manager's own team, so the index can put it first and the masthead can
#: say which side of a matchup is ours. There is no account system and no
#: setting for this yet, so the name is a constant here as it is in
#: `scripts/stars_and_waivers.py`; `?me=` overrides it for anyone else, and a
#: season with no team of this name simply has no team of ours.
MANAGER_TEAM = "Through The Wire"

#: The files this router will serve, by name. A fixed set rather than a path
#: joined onto `STATIC`, so no request can ask for a file outside it.
ASSETS = {
    "pages.css": "text/css; charset=utf-8",
    "pages.js": "text/javascript; charset=utf-8",
}

TodayQuery = Annotated[
    int | None,
    Query(ge=1, description="Scoring period to report on; defaults to today's"),
]
MeQuery = Annotated[
    str | None,
    Query(description=f"Whose team is ours, by name; defaults to {MANAGER_TEAM!r}"),
]


def _page(name: str) -> HTMLResponse:
    """One page, read from disk per request so an edit shows on a refresh."""
    return HTMLResponse((STATIC / name).read_text())


@router.get(
    "/pages/teams/{league_id}/{season}/{team_id}/week",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def week_page() -> HTMLResponse:
    """The streaming report for one team, as a page."""
    return _page("week.html")


@router.get(
    "/pages/teams/{league_id}/{season}/{team_id}/season",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[TEAM_PLAN_PAGE],
)
def season_page() -> HTMLResponse:
    """The rest-of-season report for one team, as a page."""
    return _page("season.html")


@router.get(
    "/pages/teams/{league_id}/{season}",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def index_page() -> HTMLResponse:
    """Every team in the league, with its record and both of its pages."""
    return _page("index.html")


@router.get("/pages/static/{name}", include_in_schema=False)
def asset(name: str) -> Response:
    """The pages' shared stylesheet and script."""
    media_type = ASSETS.get(name)
    if media_type is None:
        raise HTTPException(status_code=404, detail=f"no page asset {name!r}")
    return Response((STATIC / name).read_text(), media_type=media_type)


@router.get(
    "/leagues/{league_id}/seasons/{season}/pages/context",
    summary="Names, the day, its matchup period and the source line, for the pages",
    dependencies=[LEAGUE_MEMBER],
)
def page_context(
    league_id: LeagueIdPath,
    league_season: LeagueSeasonDep,
    session: SessionDep,
    today: TodayQuery = None,
    me: MeQuery = None,
) -> PageContextOut:
    """Everything the in-season pages need that is not one of the two reports.

    Never a 409: a season with no stored schedule still has teams and names,
    and the page says what is missing rather than failing to draw. `today`
    is resolved the same way the pickup routes resolve it, so the page and
    the report it draws are always talking about the same day.
    """
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    day = _day(calendar, today)
    period = period_for_day(session, league_season, day) if day is not None else None
    teams = _teams(session, league_season, me or MANAGER_TEAM)
    ours = next((team for team in teams if team.ours), None)
    return PageContextOut(
        league_id=league_id,
        season=season,
        today=day if day is not None else 1,
        today_date=calendar.date_of(day) if calendar is not None and day is not None else None,
        first_scoring_period=calendar.first_scoring_period if calendar else None,
        last_scoring_period=calendar.last_scoring_period if calendar else None,
        period=_period_out(period, calendar, day) if period is not None and day else None,
        teams=teams,
        our_espn_team_id=ours.espn_team_id if ours is not None else None,
        # Nothing on these pages is per-player projection data, so nothing is
        # gated; the line says where the numbers came from all the same.
        source_note=describe(ESPN, f"{season} season, from the stored box scores"),
        generated_at=datetime.now(UTC),
    )


def _day(calendar: SeasonCalendar | None, today: int | None) -> int | None:
    if today is not None:
        return today
    if calendar is None:
        return None
    return calendar.scoring_period_on(date.today())


def _teams(session: Session, league_season: LeagueSeason, me: str) -> list[PageTeamOut]:
    """Every team of the season, ours first, then the rest by name.

    The match on `me` is case-insensitive and on the whole name; a season in
    which no team is called that -- a year before the manager joined, or a
    renamed team -- simply has no team of ours, and the index is then in
    plain order.
    """
    rows = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id).order_by(Team.name)
    ).all()
    wanted = me.strip().casefold()
    out = [
        PageTeamOut(
            espn_team_id=int(team.espn_team_id),
            name=str(team.name),
            abbreviation=team.abbreviation,
            ours=str(team.name).strip().casefold() == wanted,
        )
        for team in rows
    ]
    return sorted(out, key=lambda team: (not team.ours, team.name))


def _period_out(
    period: MatchupPeriod, calendar: SeasonCalendar | None, today: int
) -> PagePeriodOut | None:
    """The period's days, dated from the schedule when one is stored."""
    if period.first_scoring_period is None or period.final_scoring_period is None:
        return None
    first, final = int(period.first_scoring_period), int(period.final_scoring_period)
    return PagePeriodOut(
        period=int(period.period),
        first_scoring_period=first,
        final_scoring_period=final,
        days=[
            PageDayOut(
                scoring_period=day,
                calendar_date=calendar.date_of(day) if calendar is not None else None,
                played=day < today,
                today=day == today,
            )
            for day in range(first, final + 1)
        ],
    )
