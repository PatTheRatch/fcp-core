#!/usr/bin/env python3
"""One status pass: snapshot the player pool, diff it, fetch the news.

Usage:
    python scripts/status_pass.py                 # label from the clock
    python scripts/status_pass.py --label morning
    python scripts/status_pass.py --force         # snapshot even off-season

Reads the ESPN_* variables and FCP_TRACKED_TEAM_ID (optional) from the
environment or .env, and writes to DATABASE_URL. The season is the newest
one ESPN serves, so in September the pass already follows the league being
drafted rather than the one that finished in April. Every execution is
recorded in `ingest_runs` with mode "status", succeeded or not, so a silent
listener shows up in `GET /ingest-runs/health?mode=status`.

Runs in seconds: four to five requests for the pool, one per player worth
asking for news. The rest is the database. See app/listener/status.py for
what a pass does and docs/pickups.md for why.
"""

import argparse
import time
from datetime import UTC, datetime

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.espn import fetch_newest_league, get_espn_settings
from app.ingest_runs import record_run
from app.listener.status import (
    PASS_SCHEDULE,
    label_for,
    next_pass_after,
    run_status_pass,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        choices=[*PASS_SCHEDULE, "adhoc"],
        help="Which pass this is. Defaults to the nearest scheduled slot, else 'adhoc'.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Snapshot even if the off-season rule would skip this pass.",
    )
    args = parser.parse_args()

    started = time.monotonic()
    now = datetime.now(UTC)
    label = args.label or label_for(now)
    espn_settings = get_espn_settings()
    league = fetch_newest_league(espn_settings)
    season = int(league.year)
    print(f"Status pass '{label}' for season {season} at {now:%Y-%m-%d %H:%M:%S} UTC")

    engine = make_engine(get_settings().database_url)
    try:
        factory = make_session_factory(engine)
        with (
            record_run(
                factory, espn_league_id=espn_settings.espn_league_id, season=season, mode="status"
            ) as detail,
            factory() as session,
        ):
            result = run_status_pass(
                session,
                league,
                label=label,
                now=now,
                tracked_team_id=espn_settings.fcp_tracked_team_id,
                next_pass_at=next_pass_after(now),
                force=args.force,
            )
            session.commit()
            detail.update(result.describe())
    finally:
        engine.dispose()

    if result.skipped:
        print(f"  skipped: {result.skipped}")
    else:
        print(
            f"  players {result.players}  free agents {result.free_agents}"
            f"  pro games {result.pro_games}  events {result.events}"
            f"  news {result.news_items} items for {result.news_players} players"
        )
        if result.events_by_kind:
            kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(result.events_by_kind.items()))
            print(f"  events: {kinds}")
    print(
        f"  {'in season' if result.in_season else 'off-season'}, {result.requests} requests,"
        f" done in {time.monotonic() - started:.0f}s"
    )


if __name__ == "__main__":
    main()
