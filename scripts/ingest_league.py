#!/usr/bin/env python3
"""Fetch ESPN league seasons and persist them.

Usage:
    python scripts/ingest_league.py                  # the season running now
    python scripts/ingest_league.py --season 2023    # one prior season
    python scripts/ingest_league.py --all-seasons    # every season ESPN holds
    python scripts/ingest_league.py --recent         # trailing days only

Reads the same ESPN_* variables as scripts/espn_probe.py and writes to
DATABASE_URL. The season is derived from the date, not configured: ESPN
labels a season by the year it ends in and turns over in October. Setting
ESPN_SEASON pins every run to one year, which is useful for a one-off
backfill and dangerous for a schedule. Each season persists its structure, teams and owners, matchup
periods and matchups, per-category matchup detail, a box score line per
player per day, and where every player sat each day.

Safe to re-run: a season already stored is updated in place, and a season not
yet stored is inserted alongside the others. Earlier seasons are never
modified by a later one.

Roughly one ESPN call per matchup period and one per scoring period, plus a
few batched player-card calls, so a full season takes a couple of minutes.

`--recent` narrows that to the trailing days, which is what the scheduled job
runs: seconds rather than minutes, and nothing outside the window is touched.
Every execution is recorded in `ingest_runs` whether it succeeds or not.
"""

import argparse
import time
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    MatchupPeriod,
    PlayerGameStat,
    Transaction,
)
from app.db.session import make_engine, make_session_factory
from app.espn import (
    fetch_current_league,
    fetch_league,
    get_espn_settings,
    prior_seasons,
)
from app.ingest import FULL_SCOPE, ingest_season, recent_scope
from app.ingest_runs import record_run

#: Trailing days a scheduled run refreshes. A matchup period is about seven
#: days, so this covers the current one and the tail of the last.
DEFAULT_RECENT_DAYS = 10


def _report(session: Session, stored: LeagueSeason, *, scope_note: str = "full") -> None:
    matchups = sum(len(p.matchups) for p in stored.matchup_periods)
    rosters = sum(len(m.roster_slots) for p in stored.matchup_periods for m in p.matchups)
    stats = sum(len(m.team_stats) for p in stored.matchup_periods for m in p.matchups)
    lineups = session.scalar(
        select(func.count())
        .select_from(DailyLineupSlot)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(MatchupPeriod.league_season_id == stored.id)
    )
    games = session.scalar(
        select(func.count())
        .select_from(PlayerGameStat)
        .where(PlayerGameStat.season == stored.season)
    )
    window = [
        (p.period, p.first_scoring_period, p.final_scoring_period) for p in stored.matchup_periods
    ]
    print(f"  {stored.season}: {stored.name!r} ({scope_note})")
    print(
        f"    teams {len(stored.teams)}  periods {len(stored.matchup_periods)}"
        f"  matchups {matchups}  categories {len(stored.categories)}"
    )
    print(f"    roster snapshots {rosters}  matchup statistics {stats}")
    transactions = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.league_season_id == stored.id)
    )
    print(f"    player game lines {games}  daily lineup slots {lineups}")
    print(f"    transactions {transactions}")
    if window:
        first, last = window[0], window[-1]
        print(
            f"    day windows: period {first[0]} = {first[1]}-{first[2]}, "
            f"period {last[0]} = {last[1]}-{last[2]}"
        )


def _counts(session: Session, stored: LeagueSeason) -> dict[str, int]:
    """Row counts worth keeping on the run record."""
    return {
        "teams": len(stored.teams),
        "matchup_periods": len(stored.matchup_periods),
        "matchups": sum(len(p.matchups) for p in stored.matchup_periods),
        "player_game_lines": session.scalar(
            select(func.count())
            .select_from(PlayerGameStat)
            .where(PlayerGameStat.season == stored.season)
        )
        or 0,
        "transactions": session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.league_season_id == stored.id)
        )
        or 0,
        "daily_lineup_slots": session.scalar(
            select(func.count())
            .select_from(DailyLineupSlot)
            .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
            .where(MatchupPeriod.league_season_id == stored.id)
        )
        or 0,
    }


def _ingest_one(factory: sessionmaker[Session], season: int, *, recent_days: int | None) -> None:
    started = time.monotonic()
    espn_settings = get_espn_settings()
    mode = "full" if recent_days is None else "recent"

    with record_run(
        factory, espn_league_id=espn_settings.espn_league_id, season=season, mode=mode
    ) as detail:
        league = fetch_league(espn_settings, season=season)
        with factory() as session:
            scope = FULL_SCOPE
            if recent_days is not None:
                # The season has to exist before it can be narrowed against.
                existing = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
                if existing is not None:
                    scope = recent_scope(session, existing, league, recent_days)

            stored = ingest_season(session, league, scope)
            session.commit()
            detail.update(scope.describe())
            detail["counts"] = _counts(session, stored)
            if scope.is_full and recent_days is not None:
                detail["note"] = "fell back to a full pass: nothing stored to narrow against"
            _report(session, stored, scope_note=detail.get("mode", "full"))

    print(f"    done in {time.monotonic() - started:.0f}s")


def _seasons_to_ingest(args: argparse.Namespace) -> Sequence[int]:
    """Which seasons to write.

    The default is whichever season is running now, derived from the date
    rather than read from configuration. `ESPN_SEASON` still pins it if set,
    but leaving it unset is what keeps a schedule honest across a rollover.
    """
    if args.season is not None:
        return [args.season]

    settings = get_espn_settings()
    if not args.all_seasons:
        # Ask ESPN rather than trusting the calendar alone: in early October
        # the new season may not exist yet, and the fetch settles it.
        return [int(fetch_current_league(settings).year)]

    current = fetch_current_league(settings)
    return [*prior_seasons(current), int(current.year)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--season", type=int, help="Ingest one specific season")
    group.add_argument(
        "--all-seasons",
        action="store_true",
        help="Ingest every season ESPN holds for the league, oldest first",
    )
    parser.add_argument(
        "--recent",
        nargs="?",
        type=int,
        const=DEFAULT_RECENT_DAYS,
        default=None,
        metavar="DAYS",
        help=(
            "Refresh only the trailing DAYS scoring periods "
            f"(default {DEFAULT_RECENT_DAYS}). What the scheduled job runs."
        ),
    )
    args = parser.parse_args()
    if args.recent is not None and args.recent < 1:
        parser.error("--recent DAYS must be at least 1")

    seasons = _seasons_to_ingest(args)
    scope_label = "full" if args.recent is None else f"last {args.recent} days"
    pinned = get_espn_settings().espn_season
    if pinned:
        print(f"NOTE: ESPN_SEASON pins every run to {pinned}. Unset it to follow the calendar.")
    print(
        f"Ingesting {len(seasons)} season(s) [{scope_label}]: {', '.join(str(s) for s in seasons)}"
    )

    engine = make_engine(get_settings().database_url)
    try:
        factory = make_session_factory(engine)
        for season in seasons:
            _ingest_one(factory, season, recent_days=args.recent)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
