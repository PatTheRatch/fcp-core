#!/usr/bin/env python3
"""Fetch one ESPN league season and persist its structure.

Usage:
    python scripts/ingest_league.py

Reads the same ESPN_* variables as scripts/espn_probe.py and writes to
DATABASE_URL. Persists the season's structure, its teams and owners, its
matchup periods and matchups, and a roster snapshot per team per period.

Safe to re-run: a season already stored is updated in place, and a season not
yet stored is inserted alongside the others. Makes one ESPN call per matchup
period, so a full season is a couple of dozen requests.
"""

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import DailyLineupSlot, MatchupPeriod, PlayerGameStat
from app.db.session import make_engine, make_session_factory
from app.espn import fetch_league, get_espn_settings
from app.ingest import ingest_season


def main() -> None:
    espn_settings = get_espn_settings()
    league = fetch_league(espn_settings)

    engine = make_engine(get_settings().database_url)
    try:
        session_factory = make_session_factory(engine)
        with session_factory() as session:
            stored = ingest_season(session, league)
            session.commit()

            print(f"Stored {stored.name!r} season {stored.season}")
            print(f"  league_season id: {stored.id}")
            print(f"  teams: {stored.team_count}  scoring: {stored.scoring_type}")
            print(
                f"  periods: {stored.regular_season_periods} regular season, "
                f"{stored.total_matchup_periods} including playoffs"
            )
            print(f"  trade deadline: {stored.trade_deadline}")
            categories = ", ".join(c.abbreviation for c in stored.categories)
            print(f"  categories ({len(stored.categories)}): {categories}")

            matchups = sum(len(p.matchups) for p in stored.matchup_periods)
            rosters = sum(len(m.roster_slots) for p in stored.matchup_periods for m in p.matchups)
            print(f"  teams stored: {len(stored.teams)}")
            print(f"  matchup periods: {len(stored.matchup_periods)}  matchups: {matchups}")
            print(f"  roster snapshots: {rosters}")
            stats = sum(len(m.team_stats) for p in stored.matchup_periods for m in p.matchups)
            scored = sum(
                1
                for p in stored.matchup_periods
                for m in p.matchups
                for s in m.team_stats
                if s.league_season_category_id is not None
            )
            print(f"  matchup statistics: {stats} ({scored} scored categories)")

            games = session.scalar(
                select(func.count())
                .select_from(PlayerGameStat)
                .where(PlayerGameStat.season == stored.season)
            )
            played = session.scalar(
                select(func.count())
                .select_from(PlayerGameStat)
                .where(PlayerGameStat.season == stored.season, PlayerGameStat.played)
            )
            print(f"  player game lines: {games} ({played} with a stat line)")

            lineups = session.scalar(
                select(func.count())
                .select_from(DailyLineupSlot)
                .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
                .where(MatchupPeriod.league_season_id == stored.id)
            )
            benched = session.scalar(
                select(func.count())
                .select_from(DailyLineupSlot)
                .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
                .where(MatchupPeriod.league_season_id == stored.id, ~DailyLineupSlot.started)
            )
            print(f"  daily lineup slots: {lineups} ({benched} not started)")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
