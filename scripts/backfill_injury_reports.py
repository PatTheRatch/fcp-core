#!/usr/bin/env python3
"""Load the NBA's official injury reports for a season, snapshot by snapshot.

Usage:
    python scripts/backfill_injury_reports.py --season 2026
    python scripts/backfill_injury_reports.py --season 2026 --snapshots morning
    python scripts/backfill_injury_reports.py --season 2026 --from 2025-11-01 --to 2025-11-30
    python scripts/backfill_injury_reports.py --season 2026 --snapshots all --dry-run

Walks every date the stored NBA schedule (`pro_team_games`) has a game on,
fetches that date's report snapshots from the league's public CDN, reads
them, places each name on one of our players and writes the lines
(docs/injuries.md).

WHAT IT COSTS. `--snapshots all` is every published snapshot: twenty-four a
day while the league published hourly (through 19 December 2025) and
ninety-six a day since. A full season is roughly 170 game dates, so an
hourly season is about 4,100 requests and 2025-26, which straddles the
change, about 9,700. At the throttle below that is two to five hours and
several hundred megabytes over the wire, for roughly 400k to 1M stored rows
-- the same order as `player_status_snapshots`, which is 400k a season.
`--snapshots morning` is one request a date, about 170 for a season and five
minutes, and is all the morning-of-day backtest rule needs.

THROTTLE AND RESUME. One request at a time with `--delay` seconds between
them (1.5 by default), and a failed fetch is retried with a widening wait.
The unique key makes the whole thing idempotent: a second run over the same
dates inserts nothing, so an interrupted backfill is resumed by running it
again. `--skip-loaded` goes further and does not even fetch a snapshot whose
`reported_at` is already stored.

DRY RUN: `--dry-run` fetches, parses and counts, and writes no rows. The run
itself is always recorded in `injury_report_runs`.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import injury_reports
from app.config import get_settings
from app.db.models import InjuryReport, InjuryReportRun, ProTeamGame
from app.db.session import make_engine, make_session_factory
from app.player_names import name_index

BACKFILL = "backfill"
PASS = "pass"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

#: Seconds between requests to the league's CDN. It is a static file on a
#: content network, but this is someone else's server and the job has all
#: night, so it goes one at a time and slowly.
DEFAULT_DELAY = 1.5
#: Tries before a snapshot is given up on, and the wait before each retry.
MAX_TRIES = 3
BACKOFF = (5.0, 20.0)
ERROR_LIMIT = 500


@dataclass
class Counts:
    """What a run came to, as it goes."""

    dates: int = 0
    snapshots_fetched: int = 0
    snapshots_missing: int = 0
    snapshots_skipped: int = 0
    lines: int = 0
    inserted: int = 0
    matched: int = 0
    unmatched: int = 0
    orphan_lines: int = 0
    retries: int = 0
    unmatched_names: Counter[str] = field(default_factory=Counter)

    def as_detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            key: value for key, value in self.__dict__.items() if key != "unmatched_names"
        }
        placed = self.matched + self.unmatched
        out["match_rate"] = round(self.matched / placed, 4) if placed else None
        out["unmatched_distinct"] = len(self.unmatched_names)
        out["unmatched_top"] = self.unmatched_names.most_common(40)
        return out


@contextmanager
def record_run(
    factory: sessionmaker[Session], *, season: int, mode: str
) -> Iterator[dict[str, Any]]:
    """Open a run row, yield its detail dict, close the row on the way out.

    The same shape as `app.ingest_runs.record_run`, on its own table because
    `ingest_runs` is keyed on an ESPN league and these reports belong to
    none. Its own session throughout, so a rolled-back write cannot erase
    the record of having tried.
    """
    started = datetime.now(UTC)
    detail: dict[str, Any] = {}
    with factory() as session:
        run = InjuryReportRun(
            season=season, mode=mode, status=RUNNING, started_at=started, detail={}
        )
        session.add(run)
        session.commit()
        run_id = int(run.id)
    try:
        yield detail
    except BaseException as error:
        _close(factory, run_id, FAILED, started, detail, _first_line(error))
        raise
    else:
        _close(factory, run_id, SUCCEEDED, started, detail, None)


def _first_line(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}".strip().splitlines()
    return (text[0] if text else type(error).__name__)[:ERROR_LIMIT]


def _close(
    factory: sessionmaker[Session],
    run_id: int,
    status: str,
    started: datetime,
    detail: dict[str, Any],
    error: str | None,
) -> None:
    finished = datetime.now(UTC)
    with factory() as session:
        run = session.get(InjuryReportRun, run_id)
        if run is None:  # pragma: no cover - it was just written
            return
        run.status = status
        run.finished_at = finished
        run.duration_seconds = (finished - started).total_seconds()
        run.detail = dict(detail)
        run.error = error
        session.commit()


def game_dates(session: Session, season: int) -> list[date]:
    """Every date the stored NBA schedule has a game on, in order.

    `pro_team_games.game_at` is the tip-off in UTC, so a 10pm Eastern game is
    the next day there; the Eastern date is the one the report is filed
    under, and is what the league's URLs name.
    """
    rows = session.scalars(select(ProTeamGame.game_at).where(ProTeamGame.season == season)).all()
    return sorted({at.astimezone(injury_reports.ET).date() for at in rows})


def loaded_stamps(session: Session, day: date) -> set[datetime]:
    """The `reported_at` values already stored that name this game date."""
    rows = session.scalars(
        select(InjuryReport.reported_at).where(InjuryReport.game_date == day).distinct()
    ).all()
    return {at.astimezone(UTC) for at in rows}


def fetch_with_retry(
    at: datetime, http: Any, counts: Counts, *, delay: float
) -> injury_reports.Report | None:
    """One snapshot, retried with a widening wait. None when there is none."""
    for attempt in range(MAX_TRIES):
        try:
            return injury_reports.fetch_report(at, session=http)
        except injury_reports.ReportLayoutError:
            # The file is there but is not a report we can read. Retrying
            # will not change that; say so and move on.
            print(f"    ! {at:%Y-%m-%d %H:%M} unreadable, skipped", flush=True)
            return None
        except Exception as error:
            if attempt == MAX_TRIES - 1:
                raise
            counts.retries += 1
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            print(f"    ! {at:%Y-%m-%d %H:%M} {type(error).__name__}, retrying in {wait}s")
            time.sleep(wait)
    return None  # pragma: no cover - the loop either returns or raises


def run_backfill(
    factory: sessionmaker[Session],
    *,
    season: int,
    days: list[date],
    which: str,
    delay: float,
    dry_run: bool,
    skip_loaded: bool,
    counts: Counts,
) -> None:
    import requests

    http = requests.Session()
    with factory() as session:
        index = name_index(session)
    print(f"{len(index.known)} players known, {len(days)} game dates", flush=True)

    for day in days:
        times = injury_reports.snapshot_times(day, which=which)
        with factory() as session:
            already = loaded_stamps(session, day) if skip_loaded else set()
        fetched = missing = 0
        for at in times:
            report = fetch_with_retry(at, http, counts, delay=delay)
            time.sleep(delay)
            if report is None:
                counts.snapshots_missing += 1
                missing += 1
                continue
            if skip_loaded and report.reported_at.astimezone(UTC) in already:
                counts.snapshots_skipped += 1
                continue
            counts.snapshots_fetched += 1
            counts.orphan_lines += report.orphan_lines
            fetched += 1
            with factory() as session:
                stored = injury_reports.store_report(session, report, index)
                if dry_run:
                    session.rollback()
                else:
                    session.commit()
            counts.lines += stored.lines
            counts.inserted += stored.inserted
            counts.matched += stored.matched
            counts.unmatched += stored.unmatched
            counts.unmatched_names.update(stored.unmatched_names)
            already.add(report.reported_at.astimezone(UTC))
        counts.dates += 1
        print(
            f"  {day} {fetched} snapshots, {missing} missing, {counts.inserted} rows so far",
            flush=True,
        )


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
        help="do not re-store a snapshot whose timestamp is already in",
    )
    ap.add_argument("--limit-dates", type=int, help="stop after this many dates")
    args = ap.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)

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
        f"(~{per_date} a date), {args.delay}s apart -> "
        f"about {timedelta(seconds=int(len(days) * per_date * (args.delay + 0.6)))}"
    )

    counts = Counts()
    started = time.monotonic()
    with record_run(factory, season=args.season, mode=BACKFILL) as detail:
        detail["snapshots"] = args.snapshots
        detail["dry_run"] = bool(args.dry_run)
        detail["first_date"] = days[0].isoformat()
        detail["last_date"] = days[-1].isoformat()
        try:
            run_backfill(
                factory,
                season=args.season,
                days=days,
                which=args.snapshots,
                delay=args.delay,
                dry_run=bool(args.dry_run),
                skip_loaded=bool(args.skip_loaded),
                counts=counts,
            )
        except KeyboardInterrupt:
            print("\ninterrupted; what was stored stays stored, run again to resume")
        detail.update(counts.as_detail())
        detail["seconds"] = round(time.monotonic() - started, 1)

    placed = counts.matched + counts.unmatched
    print(
        f"\n{counts.dates} dates, {counts.snapshots_fetched} snapshots, "
        f"{counts.lines} lines, {counts.inserted} rows written"
    )
    if placed:
        print(
            f"matched {counts.matched}/{placed} names ({counts.matched / placed:.1%}), "
            f"{len(counts.unmatched_names)} distinct misses"
        )
        for name, seen in counts.unmatched_names.most_common(15):
            print(f"    {name} ({seen})")
    if counts.orphan_lines:
        print(f"! {counts.orphan_lines} lines could not be placed on a row")
    print(f"{round(time.monotonic() - started, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
