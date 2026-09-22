"""Walking a season's injury reports: the loop, and the record of the run.

Separate from `app.injury_reports`, which fetches, reads and stores one
snapshot, because this is the part that decides *which* snapshots and keeps
count. `scripts/backfill_injury_reports.py` is a CLI over it and the
`injury_backfill` and `injury_pass` job kinds call it directly, so a run from
the queue and a run from the terminal do the same thing.

WHAT A FULL SEASON COSTS

`all` is every snapshot the league published: twenty-four a day while it
published hourly (through 19 December 2025) and ninety-six a day since. A
season is about 165 game dates, so an hourly season is roughly 4,000
requests and 2025-26, which straddles the change, roughly 9,700. `morning`
is one a date -- about 165, five minutes -- and is what the morning-of-day
backtest rule reads.

THROTTLE AND RESUME

One request at a time, `delay` seconds apart, retried with a widening wait.
The unique key on `injury_reports` makes the whole thing idempotent, so an
interrupted run is resumed by running it again; `skip_loaded` goes further
and does not re-fetch a snapshot whose timestamp is already stored.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import injury_reports
from app.db.models import InjuryReport, InjuryReportRun, ProTeamGame
from app.player_names import name_index

#: `injury_report_runs.mode`: a season walk, or the live season's daily one.
BACKFILL = "backfill"
PASS = "pass"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

#: Seconds between requests to the league's CDN. It is a static file on a
#: content network, but it is someone else's server and this job has all
#: night, so it goes one at a time and slowly.
DEFAULT_DELAY = 1.5
#: Tries before a snapshot is given up on, and the wait before each retry.
MAX_TRIES = 3
BACKOFF = (5.0, 20.0)
ERROR_LIMIT = 500


@dataclass
class Counts:
    """What a run came to, filled in as it goes."""

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

    @property
    def placed(self) -> int:
        return self.matched + self.unmatched

    @property
    def match_rate(self) -> float | None:
        return self.matched / self.placed if self.placed else None

    def as_detail(self) -> dict[str, Any]:
        """The counts as they are stored on the run row."""
        out: dict[str, Any] = {
            key: value for key, value in self.__dict__.items() if key != "unmatched_names"
        }
        out["match_rate"] = round(self.match_rate, 4) if self.match_rate is not None else None
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
    none. Its own session throughout, so a rolled-back write cannot erase the
    record of having tried, and the row is closed whatever happens, so a
    crashed process leaves "running" rather than no trace.
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
        # Caught broadly on purpose: the row is closed, then the error
        # re-raised untouched, so a Ctrl-C still leaves a record.
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

    `pro_team_games.game_at` is the tip-off in UTC, so a ten o'clock Eastern
    game falls on the next day there. The Eastern date is the one the league
    files the report under and names in the URL, so that is the one used.
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
    at: datetime, http: Any, counts: Counts, *, log: Any = None
) -> injury_reports.Report | None:
    """One snapshot, retried with a widening wait. None when there is none."""
    for attempt in range(MAX_TRIES):
        try:
            return injury_reports.fetch_report(at, session=http)
        except injury_reports.ReportLayoutError:
            # The file is there but is not a report we can read. Another try
            # will not change that; say so and move on.
            if log:
                log(f"    ! {at:%Y-%m-%d %H:%M} unreadable, skipped")
            return None
        except Exception as error:
            if attempt == MAX_TRIES - 1:
                raise
            counts.retries += 1
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            if log:
                log(f"    ! {at:%Y-%m-%d %H:%M} {type(error).__name__}, retrying in {wait}s")
            time.sleep(wait)
    return None  # pragma: no cover - the loop either returns or raises


def load_days(
    factory: sessionmaker[Session],
    days: list[date],
    *,
    which: str = "all",
    delay: float = DEFAULT_DELAY,
    dry_run: bool = False,
    skip_loaded: bool = False,
    counts: Counts | None = None,
    log: Any = None,
) -> Counts:
    """Fetch, read and store every chosen snapshot of these dates.

    The caller may pass the `Counts` in rather than take it back, so that a
    run which dies halfway still records what it managed before it died.
    """
    import requests

    counts = counts if counts is not None else Counts()
    http = requests.Session()
    with factory() as session:
        index = name_index(session)
    if log:
        log(f"{len(index.known)} players known, {len(days)} game dates")

    for day in days:
        times = injury_reports.snapshot_times(day, which=which)
        with factory() as session:
            already = loaded_stamps(session, day) if skip_loaded else set()
        fetched = missing = 0
        for at in times:
            report = fetch_with_retry(at, http, counts, log=log)
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
        if log:
            log(f"  {day} {fetched} snapshots, {missing} missing, {counts.inserted} rows so far")
    return counts


def run_backfill(
    factory: sessionmaker[Session],
    *,
    season: int,
    days: list[date],
    which: str = "all",
    delay: float = DEFAULT_DELAY,
    dry_run: bool = False,
    skip_loaded: bool = False,
    mode: str = BACKFILL,
    log: Any = None,
) -> Counts:
    """A recorded load: the loop above, with a row in `injury_report_runs`."""
    started = time.monotonic()
    counts = Counts()
    with record_run(factory, season=season, mode=mode) as detail:
        detail["snapshots"] = which
        detail["dry_run"] = bool(dry_run)
        if days:
            detail["first_date"] = days[0].isoformat()
            detail["last_date"] = days[-1].isoformat()
        try:
            load_days(
                factory,
                days,
                which=which,
                delay=delay,
                dry_run=dry_run,
                skip_loaded=skip_loaded,
                counts=counts,
                log=log,
            )
        finally:
            detail.update(counts.as_detail())
            detail["seconds"] = round(time.monotonic() - started, 1)
    return counts


def todays_dates(session: Session, season: int, today: date) -> list[date]:
    """The game dates the daily pass covers: today, if the league plays.

    Only today. The reports a snapshot carries already include the next day's
    games, so fetching today's snapshot is enough to learn about tomorrow.
    """
    return [day for day in game_dates(session, season) if day == today]
