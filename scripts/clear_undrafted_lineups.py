#!/usr/bin/env python3
"""Delete the lineup rows stored for a season that has not been drafted.

Usage:
    python scripts/clear_undrafted_lineups.py --league 3853870 --season 2027           # dry run
    python scripts/clear_undrafted_lineups.py --league 3853870 --season 2027 --apply   # delete

WHY

ESPN's roster feed for a season before its draft shows every team holding
last season's final roster, projected across every future day. The ingest of
2026-09-23 stored all of it for 2027 -- 23,892 `daily_lineup_slots` rows,
fourteen teams, scoring periods 1 to 132 -- and every page then projected a
season nobody had a roster in. The ingest no longer writes them
(`app.ingest.ingest_daily_lineups`) and every reader now asks whether the
season has been drafted (`app.inseason.drafted`); this removes the rows
already there.

WHAT IT TOUCHES, AND WHAT IT REFUSES

`daily_lineup_slots` rows of the named league's named season, and nothing
else: no other table, no other season, no other league. It **refuses** a
season the predicate calls drafted, whatever `--apply` says, because a
drafted season's lineup rows are its record and there is no undoing a delete.

The dry run prints what it would delete -- the count, the teams, the scoring
periods, and the season it would touch -- and writes nothing. `--apply`
deletes them in one transaction and prints how many went; the count is read
again inside the same transaction first, so a refusal or a changed count
leaves everything where it was.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import DailyLineupSlot, League, LeagueSeason, Team
from app.db.session import make_engine, make_session_factory
from app.inseason.drafted import season_is_drafted


class RefusedError(Exception):
    """The season cannot be cleared: it is not stored, or it has been drafted."""


@dataclass(frozen=True)
class Found:
    """What a clear-up would touch, read before anything is deleted."""

    espn_league_id: int
    season: int
    reason: str
    rows: int
    teams: int
    first_day: int | None
    last_day: int | None

    def lines(self) -> list[str]:
        days = (
            f"scoring periods {self.first_day}-{self.last_day}"
            if self.first_day is not None
            else "no scoring periods"
        )
        return [
            f"league {self.espn_league_id}, season {self.season}: not drafted.",
            f"  {self.reason}",
            f"  daily_lineup_slots: {self.rows:,} rows, {self.teams} teams, {days}",
            f"  seasons it would touch: {self.season}",
        ]


def _league_season(session: Session, espn_league_id: int, season: int) -> LeagueSeason:
    found = session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == espn_league_id, LeagueSeason.season == season)
    )
    if found is None:
        raise RefusedError(f"league {espn_league_id} has no stored season {season}")
    return found


def survey(session: Session, espn_league_id: int, season: int) -> Found:
    """What would be deleted, or `RefusedError` when the season may not be cleared."""
    league_season = _league_season(session, espn_league_id, season)
    drafted = season_is_drafted(session, league_season)
    if drafted.drafted:
        raise RefusedError(
            f"league {espn_league_id}, season {season} has been drafted ({drafted.reason}): "
            "its lineup rows are its record, and this script will not touch them"
        )
    rows, teams, first, last = session.execute(
        select(
            func.count(DailyLineupSlot.id),
            func.count(func.distinct(DailyLineupSlot.team_id)),
            func.min(DailyLineupSlot.scoring_period),
            func.max(DailyLineupSlot.scoring_period),
        )
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .where(Team.league_season_id == league_season.id)
    ).one()
    return Found(
        espn_league_id=espn_league_id,
        season=season,
        reason=drafted.reason,
        rows=int(rows),
        teams=int(teams),
        first_day=first,
        last_day=last,
    )


def clear(session: Session, espn_league_id: int, season: int) -> int:
    """Delete the season's lineup rows and return how many went. Does not
    commit: the caller owns the one transaction the survey and the delete
    share."""
    found = survey(session, espn_league_id, season)
    league_season = _league_season(session, espn_league_id, season)
    teams = select(Team.id).where(Team.league_season_id == league_season.id)
    result = session.execute(
        delete(DailyLineupSlot)
        .where(DailyLineupSlot.team_id.in_(teams))
        .execution_options(synchronize_session=False)
    )
    deleted = int(result.rowcount or 0)  # type: ignore[attr-defined]
    if deleted != found.rows:
        raise RefusedError(f"expected to delete {found.rows:,} rows and deleted {deleted:,}")
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league", type=int, required=True, help="ESPN league id")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Delete them; without it, only say what would go"
    )
    args = parser.parse_args(argv)

    engine = make_engine(get_settings().database_url)
    try:
        with make_session_factory(engine)() as session:
            try:
                found = survey(session, args.league, args.season)
                print("\n".join(found.lines()))
                if not args.apply:
                    print(f"Dry run: nothing deleted. --apply deletes these {found.rows:,} rows.")
                    return 0
                deleted = clear(session, args.league, args.season)
                session.commit()
            except RefusedError as refused:
                session.rollback()
                print(f"Refused: {refused}", file=sys.stderr)
                return 2
            print(f"Deleted {deleted:,} daily_lineup_slots rows for season {args.season}.")
            left = survey(session, args.league, args.season).rows
            print(f"Left for season {args.season}: {left:,}.")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
