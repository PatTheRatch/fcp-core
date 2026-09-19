#!/usr/bin/env python3
"""The job worker: take due jobs one at a time and run them. Long-running.

Usage:
    python scripts/worker.py            # run until stopped (SIGTERM or Ctrl-C)
    python scripts/worker.py --once     # run every job due now, then exit
    python scripts/worker.py --list     # print the queue's recent jobs, run nothing

The queue is the `jobs` table (app/jobs.py); what each kind does is
app/job_kinds.py; docs/jobs.md is the whole story. Two workers never take
the same job (`FOR UPDATE SKIP LOCKED`), so a second one is safe, though one
is enough at this size.

A stop signal lets the job in hand finish (an ingest is seconds, a first
backfill a few minutes) and then exits 0. The database going away is waited
out, not crashed on: the service would only restart into the same outage.

It is a long-running process, so like the API it keeps running whatever
code it started with: restart it after a deploy
(`sudo systemctl restart fcp-core-worker`).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import jobs
from app.config import get_settings
from app.db.models import Job
from app.db.session import make_engine, make_session_factory
from app.job_kinds import handlers
from app.schedule import jobs_table_exists

#: How long an idle worker sleeps between looks at the queue.
POLL_SECONDS = 15
#: How long it waits before trying a database that went away.
OUTAGE_SECONDS = 30

log = logging.getLogger("fcp.worker")


class Stop:
    """Set by SIGTERM or SIGINT; the loop finishes the job in hand and exits."""

    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        self.requested = True
        log.info("stop requested; finishing the job in hand")

    def stopped(self) -> bool:
        """Asked each time round, because a signal can set it at any moment."""
        return self.requested


def _listing(limit: int = 30) -> int:
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        rows = session.scalars(select(Job).order_by(Job.id.desc()).limit(limit)).all()
        for job in reversed(rows):
            print(
                f"{job.id:>6}  {job.kind:<11} {job.state:<7} "
                f"due {job.run_after:%Y-%m-%d %H:%M:%S}  tries {job.attempts}  "
                f"{job.payload.get('label', '')}  {job.last_error or ''}".rstrip()
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run what is due now, then exit")
    parser.add_argument("--list", action="store_true", help="print recent jobs and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.list:
        return _listing()

    settings = get_settings()
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as session:
        if not jobs_table_exists(session):
            log.error("no jobs table: run '.venv/bin/alembic upgrade head' first")
            return 1

    stop = Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker = jobs.worker_name()
    kinds = handlers()
    log.info("worker %s taking jobs", worker)
    while not stop.stopped():
        try:
            outcome = jobs.run_next(factory, kinds, worker=worker)
        except OperationalError:
            log.warning("the database is unreachable; trying again in %ss", OUTAGE_SECONDS)
            if args.once:
                return 69
            time.sleep(OUTAGE_SECONDS)
            continue
        if outcome is not None:
            continue
        if args.once:
            break
        for _ in range(POLL_SECONDS):
            if stop.stopped():
                break
            time.sleep(1)
    log.info("worker %s stopped", worker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
