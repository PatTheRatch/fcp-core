"""Shared API dependencies.

The engine is built once per process and the session per request. Tests
override `get_session` to point at the disposable test database, which is
why nothing here reads configuration at import time.

Dependencies are exposed as `Annotated` aliases so routes stay readable and
no callable ends up in an argument default.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import League, LeagueSeason, Team
from app.db.session import make_engine, make_session_factory


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return make_session_factory(make_engine(get_settings().database_url))


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
LeagueIdPath = Annotated[int, Path(description="ESPN league id")]
SeasonPath = Annotated[int, Path(description="Season, e.g. 2026")]
TeamIdPath = Annotated[int, Path(description="ESPN team id")]


def get_league_season(
    league_id: LeagueIdPath,
    season: SeasonPath,
    session: SessionDep,
) -> LeagueSeason:
    """Resolve the (ESPN league id, season) pair every nested route hangs off."""
    league_season = session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == league_id, LeagueSeason.season == season)
    )
    if league_season is None:
        raise HTTPException(status_code=404, detail=f"league {league_id} season {season} not found")
    return league_season


LeagueSeasonDep = Annotated[LeagueSeason, Depends(get_league_season)]


def get_team(team_id: TeamIdPath, league_season: LeagueSeasonDep, session: SessionDep) -> Team:
    """Resolve a team within its season. ESPN team ids repeat across seasons."""
    team = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if team is None:
        raise HTTPException(status_code=404, detail=f"team {team_id} not found in this season")
    return team


TeamDep = Annotated[Team, Depends(get_team)]
