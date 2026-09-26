"""The pages' shared files and the one route they need besides the reports.

The pages themselves -- the league's four, a team's three, the account pages
-- are routed in `app/api/site.py` (the URL map, docs/site.md) and served as
files from `app/api/static/`, in the light skin the season report is written
in. The week and season pages are the CLI reports (`scripts/stream.py`,
`scripts/season.py`) as a page: everything those two print, the page shows,
in the same words. Plain HTML, CSS and JavaScript, no framework and no build
step, for the reason the draft screen has none -- a page that has to come up
on a tailnet from a file on disk should not need a toolchain -- and the files
are read per request, so an edit shows on a refresh
(`docs/in_season_pages.md`).

WHAT THE PAGES FETCH

The two reports come from the routes that already exist, unchanged:

    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season

and everything else from one small route added here, `pages/context`: the
day being reported on as a date, the days of its matchup period for the
schedule strip, every team's name, which team is ours, and the line naming
where the numbers came from. Records come from `/standings`, which already
derives them from the stored matchups; they are not repeated here.

WHO MAY OPEN THEM

Who may open each page is in `app/api/site.py`. Here: the stylesheet and the
three scripts (the pages' helpers, the scenario seam, the shell) are open (no
data), and the context route is the league's. The
pages' script sends a signed-out reader to /sign-in and back on a 401 from a
fetch, and says one plain line on a 403 (docs/accounts.md).

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
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import brand
from app.api.access import LEAGUE_MEMBER
from app.api.deps import LeagueIdPath, LeagueSeasonDep, SessionDep
from app.api.schemas import (
    InjuriesOut,
    PageContextOut,
    PageDayOut,
    PagePeriodOut,
    PageTeamOut,
)
from app.db.models import IngestRun, LeagueSeason, MatchupPeriod, Team
from app.ingest_runs import SUCCEEDED
from app.mcp.provenance import NO_REPORT, USED_NOTE
from app.pickups.state import SeasonCalendar, period_for_day, season_calendar, status_on
from app.projections.sources import ESPN, describe

router = APIRouter(tags=["pages"])

#: Where the pages and their shared stylesheet and scripts live.
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
    "shell.js": "text/javascript; charset=utf-8",
    "scenario.js": "text/javascript; charset=utf-8",
    "sources.js": "text/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}

#: The icons that are bytes, not text: served as they are, no brand token in
#: them. `favicon.svg` is text and sits above, though it names nothing either.
#: Both are copies of `brand/box-out-icon.svg`; that folder is the source.
ICONS = {
    "apple-touch-icon.png": "image/png",
}

TodayQuery = Annotated[
    int | None,
    Query(ge=1, description="Scoring period to report on; defaults to today's"),
]
MeQuery = Annotated[
    str | None,
    Query(description=f"Whose team is ours, by name; defaults to {MANAGER_TEAM!r}"),
]


@router.get("/pages/static/{name}", include_in_schema=False)
def asset(name: str) -> Response:
    """The pages' shared stylesheet and scripts.

    Through `brand.fill` like a page, because the shell draws the product's
    name and the shell is one of these files.
    """
    media_type = ICONS.get(name)
    if media_type is not None:
        return Response((STATIC / name).read_bytes(), media_type=media_type)
    media_type = ASSETS.get(name)
    if media_type is None:
        raise HTTPException(status_code=404, detail=f"no page asset {name!r}")
    return Response(brand.fill((STATIC / name).read_text()), media_type=media_type)


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
        injuries=_injuries_out(session, season, day),
        box_scores_as_of=_last_ingest(session, season),
        generated_at=datetime.now(UTC),
    )


def _injuries_out(session: Session, season: int, day: int | None) -> InjuriesOut | None:
    """Where this day's statuses came from, for "how this is worked out".

    The engine's own answer rather than a second guess at it: `status_on` is
    what `build_players` read, and it is memoized on the session, so naming
    the source costs the page nothing. Null with no day, which is a season
    with no stored schedule -- nothing was read, and the page says so by
    leaving the line off.
    """
    if day is None:
        return None
    read = status_on(session, season, day)
    return InjuriesOut(
        used=read.source,
        used_note=USED_NOTE.get(read.source, NO_REPORT),
        read_as_of=read.read_as_of,
        reported_at=read.reported_at,
        placed=read.placed,
        unmatched=read.unmatched,
    )


def _last_ingest(session: Session, season: int) -> datetime | None:
    """When the last successful ingest of this season finished.

    The honest limit on every stored box score a page shows. They arrive
    with the nightly pass (`scripts/ingest_league.py --recent`, docs/jobs.md),
    so tonight's line is last night's until an in-game refresh exists, and
    the page says so rather than letting a reader take a stale line for a
    live one. Off `ingest_runs`, which is the table that makes the schedule
    observable at all (`app/api/ingest_runs.py`).
    """
    return session.scalar(
        select(func.max(IngestRun.finished_at)).where(
            IngestRun.season == season,
            IngestRun.status == SUCCEEDED,
            IngestRun.finished_at.is_not(None),
        )
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
