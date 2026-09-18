#!/usr/bin/env python3
"""Store a past season's NBA schedule, so that season can be replayed live.

Usage:
    python scripts/backfill_pro_schedule.py --season 2026
    python scripts/backfill_pro_schedule.py --all
    python scripts/backfill_pro_schedule.py --season 2026 --commit

`pro_team_games` is written by the listener for the season in progress
(`app.listener.status.rewrite_pro_schedule`), which is why only 2027 had any
rows and why every recommender and every report refused to run on a played
season ("No NBA schedule stored for 2026"); the backtest had to rebuild the
schedule from box scores in memory. ESPN still serves the full pro schedule
for a past season, home and away and tip-off times included (checked
2026-09-18: 2,468 team-games for 2026), so this fetches it through the same
parser the listener uses and stores it with the same writer.

DRY RUN BY DEFAULT: it fetches and counts, and writes nothing until
--commit. A season that already has rows is left alone unless --force, since
the listener owns the season in progress. Back the database up first
(scripts/backup_db.sh on the VPS).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ProTeamGame
from app.db.session import make_engine, make_session_factory
from app.espn import fetch_league, get_espn_settings, pro_schedule
from app.listener.pool import parse_pro_schedule
from app.listener.status import rewrite_pro_schedule

#: Seasons the league has played and stored box scores for.
PLAYED_SEASONS = tuple(range(2019, 2027))


def stored_games(session: Session, season: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ProTeamGame).where(ProTeamGame.season == season)
        )
        or 0
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, action="append", help="a season; repeatable")
    ap.add_argument(
        "--all",
        action="store_true",
        help=f"every played season {PLAYED_SEASONS[0]}-{PLAYED_SEASONS[-1]}",
    )
    ap.add_argument("--commit", action="store_true", help="write; the default is a dry run")
    ap.add_argument("--force", action="store_true", help="replace a season that already has rows")
    args = ap.parse_args()
    seasons = list(PLAYED_SEASONS) if args.all else (args.season or [])
    if not seasons:
        raise SystemExit("pass --season YYYY (repeatable) or --all")

    factory = make_session_factory(make_engine(get_settings().database_url))
    espn = get_espn_settings()
    with factory() as session:
        for season in seasons:
            before = stored_games(session, season)
            if before and not args.force:
                print(f"{season}: {before} team-games already stored; left alone (use --force)")
                continue
            league = fetch_league(espn, season=season)
            if not args.commit:
                games = parse_pro_schedule(pro_schedule(league))
                periods = sorted({g.scoring_period for g in games})
                print(
                    f"{season}: would store {len(games)} team-games over periods "
                    f"{periods[0]}-{periods[-1]} (dry run; {before} stored now)"
                )
                continue
            written = rewrite_pro_schedule(session, league, season)
            session.commit()
            print(f"{season}: stored {written} team-games (had {before})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
