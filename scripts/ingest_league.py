#!/usr/bin/env python3
"""Fetch one ESPN league season and persist its structure.

Usage:
    python scripts/ingest_league.py

Reads the same ESPN_* variables as scripts/espn_probe.py and writes to
DATABASE_URL. Safe to re-run: a season already stored is updated in place,
and a season not yet stored is inserted alongside the others.
"""

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.espn import fetch_league, get_espn_settings
from app.ingest import ingest_league_structure


def main() -> None:
    espn_settings = get_espn_settings()
    league = fetch_league(espn_settings)

    engine = make_engine(get_settings().database_url)
    try:
        session_factory = make_session_factory(engine)
        with session_factory() as session:
            stored = ingest_league_structure(session, league)
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
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
