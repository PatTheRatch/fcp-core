#!/usr/bin/env python3
"""Put a schedule's jobs on the queue for the worker. Intended for a scheduler.

Usage:
    python scripts/enqueue.py --schedule nightly   # each league's ingest, spread after 09:00
    python scripts/enqueue.py --schedule morning   # passes, then reports and digests
    python scripts/enqueue.py --schedule report    # passes, then alerts
    python scripts/enqueue.py --schedule late      # the same
    python scripts/enqueue.py --schedule auto      # the label for the time now (the timer)
    python scripts/enqueue.py --ingest 3853870     # one league's ingest, now
    python scripts/enqueue.py --intake 3853870     # the whole intake chain
    python scripts/enqueue.py --intake-email 3853870   # print that email, send nothing

`--intake` is docs/intake.md's chain: every season ESPN will give us, the
NBA schedules behind them, each of the four measurements on this league's
own history, the pooled rows, and the email. It refuses when one is already
running and when one was started today; `--force` skips the second check,
which is for an operator who has just fixed something, never for a timer.
`--intake-email` renders the message that league would get from the numbers
it has now, prints it, and opens no connection to anything.

What each label enqueues is app/schedule.py; the worker (scripts/worker.py)
runs them. Enqueueing the same schedule twice on one day adds nothing the
second time, so a timer that fires twice, or a person who runs this by
hand, is harmless. Prints one line per job, new or already there.

`--schedule auto` names the label from the clock, the way the listener's
pass does (`label_for`): the slot within an hour of now. At no slot it
enqueues nothing and exits 0.

Exit codes: 0 enqueued (or nothing to do), 1 refused (no jobs table: the
migration has not run).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import intake, jobs, memberships
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.intake.summary import preview
from app.schedule import LABELS, REQUESTED, enqueue_schedule, jobs_table_exists, label_now


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--schedule", choices=[*LABELS, "auto"])
    which.add_argument("--ingest", type=int, metavar="ESPN_LEAGUE_ID")
    which.add_argument("--intake", type=int, metavar="ESPN_LEAGUE_ID")
    which.add_argument("--intake-email", type=int, metavar="ESPN_LEAGUE_ID")
    parser.add_argument(
        "--force", action="store_true", help="with --intake: ignore the once-a-day limit"
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            if not jobs_table_exists(session):
                print("REFUSED: no jobs table; run '.venv/bin/alembic upgrade head' first")
                return 1
            if args.intake_email is not None:
                league = memberships.league_by_espn_id(session, args.intake_email)
                if league is None:
                    print(f"league {args.intake_email} is not stored; connect it first")
                    return 1
                mail = preview(session, league, at=jobs.now(), settings=settings)
                print(f"Subject: {mail.subject}\n")
                print(mail.text)
                return 0
            if args.intake is not None:
                league = memberships.league_by_espn_id(session, args.intake)
                if league is None:
                    print(f"league {args.intake} is not stored; connect it first")
                    return 1
                refused = intake.refusal(session, league.id)
                if refused is not None:
                    print(f"REFUSED ({refused.scoring}): {refused.reason}")
                    return 1
                try:
                    added = intake.enqueue_intake(session, league.id, force=args.force)
                except intake.IntakeRefusedError as no:
                    print(f"REFUSED: {no}")
                    return 1
                session.commit()
                print(f"intake for league {args.intake}:")
            elif args.ingest is not None:
                league = memberships.league_by_espn_id(session, args.ingest)
                if league is None:
                    print(f"league {args.ingest} is not stored; connect it first")
                    return 1
                added = [
                    jobs.enqueue(
                        session,
                        jobs.INGEST,
                        run_after=jobs.now(),
                        label=REQUESTED,
                        league_id=league.id,
                    )
                ]
                session.commit()
            else:
                label = label_now() if args.schedule == "auto" else args.schedule
                if label is None:
                    print("no schedule slot is within an hour of now; nothing enqueued")
                    return 0
                added = enqueue_schedule(session, settings, label)
                print(f"schedule {label}:")
            for job in added:
                print(f"  {'added' if job.created else 'already queued'}  {job.kind}  job {job.id}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
