#!/usr/bin/env python3
"""Fetch ESPN league seasons and persist them.

Usage:
    python scripts/ingest_league.py                  # the season running now
    python scripts/ingest_league.py --season 2023    # one prior season
    python scripts/ingest_league.py --all-seasons    # every season ESPN holds
    python scripts/ingest_league.py --recent         # trailing days only
    python scripts/ingest_league.py --recent --upcoming  # and next season's settings

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
`--upcoming` also refreshes the settings and teams of the season after the
current one, if ESPN has it and its draft is still ahead. September is still
the old season by the calendar, but the draft that matters is the new one's.
Every execution is recorded in `ingest_runs` whether it succeeds or not.
"""

import argparse
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from espn_api.basketball import League as ESPNLeague
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
from app.ingest import (
    FULL_SCOPE,
    draft_is_pending,
    ingest_season,
    ingest_season_settings,
    recent_scope,
    scheduled_draft,
)
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


def refresh_upcoming_season(
    factory: sessionmaker[Session],
    *,
    espn_league_id: int,
    season: int,
    fetch: Callable[[int], ESPNLeague],
    now: datetime | None = None,
) -> str:
    """Refresh next season's settings and teams while its draft is still ahead.

    The nightly run follows the season ESPN is playing, which until October
    is the old one. The draft is prepared in September against the new one,
    so without this its row keeps whatever it held when first ingested: on
    2026-09-15 the stored 2027 row still said 16 teams and a $0 budget, when
    ESPN had 15 teams and $200.

    Skipped, with no run recorded, when ESPN does not have the season yet,
    which is most of the year, or when its draft has already happened, after
    which the regular ingest owns the season. Returns a line for the log.
    """
    try:
        league = fetch(season)
    except Exception as error:
        # A missing season is the usual reason, and not a failure. Anything
        # more serious (credentials, ESPN down) has already failed the
        # current season's run, which goes first.
        return f"  {season}: not on ESPN yet, settings not refreshed ({type(error).__name__})"

    drafted_at = scheduled_draft(league)
    if not draft_is_pending(drafted_at, now):
        return f"  {season}: drafted {drafted_at:%Y-%m-%d}, settings left to the regular ingest"

    with (
        record_run(
            factory, espn_league_id=espn_league_id, season=season, mode="settings"
        ) as detail,
        factory() as session,
    ):
        stored = ingest_season_settings(session, league)
        summary = (
            f"  {season}: settings refreshed: teams {stored.team_count}"
            f"  budget ${stored.auction_budget}  position limits {stored.position_limits}"
            f"  draft {drafted_at.isoformat() if drafted_at else 'unscheduled'}"
        )
        detail["counts"] = {"teams": len(stored.teams)}
        detail["drafted_at"] = drafted_at.isoformat() if drafted_at else None
        session.commit()
    return summary


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
    parser.add_argument(
        "--upcoming",
        action="store_true",
        help=(
            "Also refresh next season's settings and teams if ESPN has it and its "
            "draft is still ahead. What the scheduled job runs."
        ),
    )
    args = parser.parse_args()
    if args.recent is not None and args.recent < 1:
        parser.error("--recent DAYS must be at least 1")
    if args.upcoming and (args.season is not None or args.all_seasons):
        parser.error("--upcoming follows the current season; drop --season and --all-seasons")

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
        if args.upcoming:
            espn_settings = get_espn_settings()
            print(
                refresh_upcoming_season(
                    factory,
                    espn_league_id=espn_settings.espn_league_id,
                    season=max(seasons) + 1,
                    fetch=lambda year: fetch_league(espn_settings, season=year),
                )
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
