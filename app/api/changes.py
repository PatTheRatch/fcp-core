"""What changed: one league's news since a moment, and a team's own of it.

A thin route over `app.inseason.changes`, which holds the whole derivation.

THE SCOPE, AND WHY `team_id` DOES NOT NARROW IT

League member, and `team_id` adds no check of its own. Everything in the
feed is the league's own record -- who is hurt, who was claimed and for how
much, who was traded for whom -- and docs/accounts.md already puts each of
those at league scope: `/transactions`, `/events`, every team's `/lineups`
and every team's `/scorecard` are a member's to read. `team_id` only sets
the `mine` and `opponent` flags on facts the caller could already see, and
who a team plays this period is on the Standings page. Nothing here is a
plan, which is the thing the team layer exists to keep private.

THE WINDOW

Both ends, always (`app.inseason.changes`). Left out, it is the last
twenty-four hours -- or, with a `team_id` the caller manages, everything
since his own last morning digest went out, so the page picks up exactly
where the message left off (docs/jobs.md, "The digest job").
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs
from app.api.access import LeagueMember, Viewer
from app.api.deps import LeagueSeasonDep, SessionDep
from app.api.schemas import ChangeOut, ChangePlayerOut, ChangesOut, ChangeTeamOut
from app.db.models import LeagueSeason, Team
from app.inseason.changes import KINDS, Change, changes, opponent_of
from app.pickups.state import season_calendar

router = APIRouter(tags=["changes"])

CHANGE_PAGE_LIMIT = 500
#: With no window asked for: what has happened since this time yesterday.
DEFAULT_WINDOW = timedelta(hours=24)
#: The longest window a single request may ask for. A season's worth of
#: moves is not a morning's reading, and the digest's own windows are days.
MAX_WINDOW = timedelta(days=90)
KINDS_HELP = "Any of " + ", ".join(KINDS)


@router.get(
    "/leagues/{league_id}/seasons/{season}/changes",
    summary="What changed in this league, newest first",
)
def list_changes(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    viewer: LeagueMember,
    since: Annotated[
        datetime | None, Query(description="From this moment; default 24 hours before `until`")
    ] = None,
    until: Annotated[datetime | None, Query(description="To this moment; default now")] = None,
    team_id: Annotated[
        int | None, Query(description="ESPN team id, for the `mine` and `opponent` flags")
    ] = None,
    kinds: Annotated[list[str] | None, Query(description=KINDS_HELP)] = None,
    limit: int = Query(default=200, ge=1, le=CHANGE_PAGE_LIMIT),
) -> ChangesOut:
    unknown = sorted(set(kinds or []) - set(KINDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown kinds: {', '.join(unknown)}")

    team = _team(session, league_season, team_id)
    far = _aware(until) or datetime.now(UTC)
    near = _aware(since) or _last_digest(session, viewer, team) or (far - DEFAULT_WINDOW)
    if near > far:
        raise HTTPException(status_code=422, detail="`since` is after `until`")
    if far - near > MAX_WINDOW:
        raise HTTPException(
            status_code=422, detail=f"a window may not be longer than {MAX_WINDOW.days} days"
        )

    found = changes(
        session,
        league_season,
        since=near,
        until=far,
        team_id=team_id,
        kinds_wanted=kinds or None,
    )
    calendar = season_calendar(session, int(league_season.season))
    day = calendar.scoring_period_on(far.date()) if calendar is not None else None
    other = opponent_of(session, league_season, team, day) if team is not None else None
    return ChangesOut(
        league_id=int(league_season.league.espn_league_id),
        season=int(league_season.season),
        since=near,
        until=far,
        team_id=team_id,
        opponent_team_id=int(other.espn_team_id) if other is not None else None,
        items=[_out(change) for change in found[:limit]],
        total=len(found),
        limit=limit,
    )


def _out(change: Change) -> ChangeOut:
    return ChangeOut(
        at=change.at,
        kind=change.kind,
        players=[
            ChangePlayerOut(espn_player_id=person.espn_player_id, name=person.name)
            for person in change.players
        ],
        teams=[
            ChangeTeamOut(espn_team_id=side.espn_team_id, name=side.name) for side in change.teams
        ],
        text=change.text,
        mine=change.mine,
        opponent=change.opponent,
        severity=change.severity,
    )


def _aware(moment: datetime | None) -> datetime | None:
    """A moment with no zone is UTC: a query string cannot always carry one."""
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _team(session: Session, league_season: LeagueSeason, team_id: int | None) -> Team | None:
    if team_id is None:
        return None
    team = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if team is None:
        raise HTTPException(status_code=404, detail=f"team {team_id} not found in this season")
    return team


def _last_digest(session: Session, viewer: Viewer, team: Team | None) -> datetime | None:
    """When this reader's last morning digest about this team went out.

    The page then picks up exactly where the message left off, which is what
    "since you last looked" means for anyone who reads the digest. Nothing
    for a reader who has never had one, or who asked about someone else's
    team, and for those the default window stands.
    """
    if team is None or viewer.user_id is None:
        return None
    return jobs.last_done(
        session, jobs.DIGEST, user_id=viewer.user_id, team_id=team.id, mode="morning"
    )
