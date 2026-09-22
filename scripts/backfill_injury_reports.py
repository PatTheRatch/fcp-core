#!/usr/bin/env python3
"""Load the NBA's official injury reports for a season, snapshot by snapshot.

Usage:
    python scripts/backfill_injury_reports.py --season 2026
    python scripts/backfill_injury_reports.py --season 2026 --snapshots morning
    python scripts/backfill_injury_reports.py --season 2026 --from 2025-11-01 --to 2025-11-30
    python scripts/backfill_injury_reports.py --season 2026 --snapshots all --dry-run

Walks every date the stored NBA schedule (`pro_team_games`) has a game on,
fetches that date's report snapshots from the league's public CDN, reads
them, places each name on one of our players and writes the lines. The work
itself is `app.injury_backfill`, which the `injury_backfill` job kind calls
too; this is the terminal's way in. docs/injuries.md is the whole story.

WHAT IT COSTS. `--snapshots all`, the default, is every published snapshot:
twenty-four a day while the league published hourly (through 19 December
2025) and ninety-six a day since. A season is about 165 game dates, so an
hourly season is roughly 4,000 requests and 2025-26, which straddles the
change, roughly 9,700 -- two to five hours at the throttle below, and of the
order of a million rows. `--snapshots morning` is one request a date, about
five minutes, and is all the morning-of-day backtest rule reads.

THROTTLE AND RESUME. One request at a time with `--delay` seconds between
them (1.5 by default), retried with a widening wait. The unique key makes
the whole thing idempotent, so an interrupted backfill is resumed by running
it again; `--skip-loaded` does not even fetch a snapshot already stored.

DRY RUN: `--dry-run` fetches, parses and counts, and writes no report rows.
The run itself is always recorded in `injury_report_runs`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import injury_reports
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.injury_backfill import DEFAULT_DELAY, game_dates, run_backfill


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--season", type=int, required=True, help="e.g. 2026 for 2025-26")
    ap.add_argument("--from", dest="start", help="first game date, YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="last game date, YYYY-MM-DD")
    ap.add_argument(
        "--snapshots",
        choices=("all", "morning", "last"),
        default="all",
        help="which of a date's snapshots to fetch (default: all)",
    )
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="seconds between requests")
    ap.add_argument("--dry-run", action="store_true", help="fetch and count, write nothing")
    ap.add_argument(
        "--skip-loaded",
        action="store_true",
        help="do not re-fetch a snapshot whose timestamp is already in",
    )
    ap.add_argument("--limit-dates", type=int, help="stop after this many dates")
    args = ap.parse_args()

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        days = game_dates(session, args.season)
    if args.start:
        days = [day for day in days if day >= date.fromisoformat(args.start)]
    if args.end:
        days = [day for day in days if day <= date.fromisoformat(args.end)]
    if args.limit_dates:
        days = days[: args.limit_dates]
    if not days:
        print(
            f"No game dates stored for {args.season}. Run scripts/backfill_pro_schedule.py first."
        )
        return 1

    per_date = len(injury_reports.snapshot_times(days[0], which=args.snapshots))
    print(
        f"season {args.season}: {len(days)} dates, {args.snapshots} snapshots "
        f"(~{per_date} a date), {args.delay}s apart -> about "
        f"{timedelta(seconds=int(len(days) * per_date * (args.delay + 0.6)))}",
        flush=True,
    )

    def say(message: str) -> None:
        print(message, flush=True)

    try:
        counts = run_backfill(
            factory,
            season=args.season,
            days=days,
            which=args.snapshots,
            delay=args.delay,
            dry_run=bool(args.dry_run),
            skip_loaded=bool(args.skip_loaded),
            log=say,
        )
    except KeyboardInterrupt:
        print("\ninterrupted; what was stored stays stored, run again to resume")
        return 1

    print(
        f"\n{counts.dates} dates, {counts.snapshots_fetched} snapshots, "
        f"{counts.lines} lines, {counts.inserted} rows written"
    )
    if counts.match_rate is not None:
        print(
            f"matched {counts.matched}/{counts.placed} names ({counts.match_rate:.1%}), "
            f"{len(counts.unmatched_names)} distinct misses"
        )
        for name, seen in counts.unmatched_names.most_common(15):
            print(f"    {name} ({seen})")
    if counts.orphan_lines:
        print(f"! {counts.orphan_lines} lines could not be placed on a row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
