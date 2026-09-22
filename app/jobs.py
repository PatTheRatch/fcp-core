"""The job queue: scheduled work as rows, taken one at a time by a worker.

Step 4 of docs/product.md; docs/jobs.md is the whole story. Postgres is the
queue (`SELECT ... FOR UPDATE SKIP LOCKED`), which is enough at this size and
needs no broker.

A job is one of six kinds, each about one thing:

* `ingest`: one league, the trailing days and next season's settings.
* `status_pass`: the listener, for one league.
* `precompute`: one team's day, week and season reports, stored for the day.
* `digest`: one member's morning digest, or an alert between digests.
* `injury_backfill`: one season of the NBA's own injury reports.
* `injury_pass`: the same for today (docs/injuries.md).

What each does is `app.job_kinds`; when they are enqueued is `app.schedule`.
This module only moves rows.

THE LIFE OF A JOB

`queued` -> `running` -> `done`, or back to `queued` with a later
`run_after` after a failure (`BACKOFF`), until `MAX_ATTEMPTS`, then
`failed`. A job with `depends_on` waits until that job is `done`, and fails
without running when that one fails: the digest does not go out when the
morning pass could not run, which is what `scheduled_status.sh` does today.
A job left `running` past `LEASE` (the worker died under it) counts as a
failed attempt and is retried.

DE-DUPLICATION

`dedupe_key` is unique: the kind, the league, the team, the member, the UTC
day of `run_after` and the schedule's label. Enqueueing the same schedule
twice, or two enqueue timers firing, adds nothing the second time.

SECRETS

`last_error` is one of this code's own sentences (`JobError`), or an
exception's class name, never its text: ESPN's and the SMTP server's
messages can quote a URL, a bot token or an address. The worker's log lines
carry the same, and nothing else.

TAKING ONLY SOME JOBS

`claim` and `run_next` take an optional `only`: a condition on `Job` that
narrows what this worker will touch, claim, orphan-fail and reap. The
production worker passes none and takes everything. It is there so a worker
that is not the production one -- the rehearsal's
(`scripts/rehearse_week.py`), which replays a played season against this
same queue -- cannot claim, fail or reap a real job that happens to be
sitting beside its own.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, exists, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.db.models import Job

log = logging.getLogger("fcp.jobs")

INGEST = "ingest"
STATUS_PASS = "status_pass"
PRECOMPUTE = "precompute"
DIGEST = "digest"
#: The NBA's official injury reports (docs/injuries.md). Both belong to no
#: league -- the reports are the league's own, not ESPN's -- so they carry a
#: season in their payload and no `league_id`.
INJURY_BACKFILL = "injury_backfill"
INJURY_PASS = "injury_pass"
KINDS = (INGEST, STATUS_PASS, PRECOMPUTE, DIGEST, INJURY_BACKFILL, INJURY_PASS)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

#: Tries before a job is given up on.
MAX_ATTEMPTS = 3
#: The wait before the second and the third try.
BACKOFF = (timedelta(minutes=5), timedelta(minutes=20))
#: A job `running` for longer than this has lost its worker. A first ingest
#: of a new league backfills every season, a couple of minutes each, so this
#: is generous.
LEASE = timedelta(hours=2)
#: `last_error` is a short sentence, never a traceback.
ERROR_LIMIT = 200

GAVE_UP_ON_PREREQUISITE = "not run: the job it waits on failed"
LOST_WORKER = "the worker stopped while running it"


class JobError(Exception):
    """A failure put into our own words, safe to store and log.

    `retry=False` for a failure another try cannot fix (no login for the
    league, no secrets key): the job fails at once rather than three times.
    """

    def __init__(self, message: str, *, retry: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retry = retry


def safe_error(error: BaseException) -> str:
    """What may be stored and logged about a failure: our sentence, or the
    exception's class alone."""
    if isinstance(error, JobError):
        return error.message[:ERROR_LIMIT]
    return f"failed ({type(error).__name__})"[:ERROR_LIMIT]


def worker_name() -> str:
    """Who holds a job: host and process, for `locked_by`."""
    return f"{socket.gethostname()}:{os.getpid()}"


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# enqueueing
# ---------------------------------------------------------------------------


def dedupe_key(
    kind: str,
    *,
    run_after: datetime,
    label: str,
    league_id: int | None = None,
    team_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """The one job this schedule may hold for this subject on this UTC day."""

    def part(value: int | None) -> str:
        return "-" if value is None else str(value)

    day = run_after.astimezone(UTC).date().isoformat()
    return f"{kind}|l{part(league_id)}|t{part(team_id)}|u{part(user_id)}|{day}|{label}"


@dataclass(frozen=True)
class Enqueued:
    id: int
    kind: str
    #: False when the schedule had already enqueued it.
    created: bool


def enqueue(
    session: Session,
    kind: str,
    *,
    run_after: datetime,
    label: str,
    league_id: int | None = None,
    team_id: int | None = None,
    user_id: int | None = None,
    depends_on: int | None = None,
    payload: Mapping[str, Any] | None = None,
) -> Enqueued:
    """Add a job unless this schedule already holds it; either way, its id.

    `league_id`, `team_id` and `user_id` are this database's own keys
    (`leagues.id`, `teams.id`, `users.id`). Does not commit.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    key = dedupe_key(
        kind,
        run_after=run_after,
        label=label,
        league_id=league_id,
        team_id=team_id,
        user_id=user_id,
    )
    body = dict(payload or {})
    body.setdefault("label", label)
    added = session.execute(
        insert(Job)
        .values(
            kind=kind,
            league_id=league_id,
            team_id=team_id,
            user_id=user_id,
            depends_on=depends_on,
            dedupe_key=key,
            run_after=run_after,
            payload=body,
        )
        .on_conflict_do_nothing(constraint="uq_jobs_dedupe_key")
        .returning(Job.id)
    ).first()
    if added is not None:
        return Enqueued(int(added[0]), kind, True)
    existing = session.scalar(select(Job.id).where(Job.dedupe_key == key))
    if existing is None:  # pragma: no cover - the conflict above says it exists
        raise RuntimeError("job vanished after a conflict")
    return Enqueued(int(existing), kind, False)


# ---------------------------------------------------------------------------
# taking a job
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRef:
    """What a handler is told about its job, read before the claim commits."""

    id: int
    kind: str
    league_id: int | None
    team_id: int | None
    user_id: int | None
    attempts: int
    payload: dict[str, Any] = field(default_factory=dict)


def _ready(at: datetime) -> Any:
    """Queued, due, and not waiting on a job that has not finished."""
    prerequisite = aliased(Job)
    waiting = exists().where(prerequisite.id == Job.depends_on, prerequisite.state != DONE)
    return (Job.state == QUEUED) & (Job.run_after <= at) & ~waiting


def _mine(only: ColumnElement[bool] | None) -> Any:
    """The narrowing a worker asked for, or nothing at all."""
    return true() if only is None else only


def lock_next(
    session: Session, at: datetime | None = None, *, only: ColumnElement[bool] | None = None
) -> Job | None:
    """The next due job, locked for this transaction; jobs another worker has
    locked are skipped, not waited on. Commits nothing: `claim` does."""
    at = at or now()
    return session.scalar(
        select(Job)
        .where(_ready(at), _mine(only))
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=Job)
    )


def fail_orphans(
    session: Session, at: datetime | None = None, *, only: ColumnElement[bool] | None = None
) -> int:
    """Fail every queued job whose prerequisite failed. Does not commit."""
    at = at or now()
    prerequisite = aliased(Job)
    orphans = session.scalars(
        select(Job.id)
        .where(
            Job.state == QUEUED,
            exists().where(prerequisite.id == Job.depends_on, prerequisite.state == FAILED),
            _mine(only),
        )
        .with_for_update(skip_locked=True, of=Job)
    ).all()
    if orphans:
        session.execute(
            update(Job)
            .where(Job.id.in_(orphans))
            .values(state=FAILED, finished_at=at, last_error=GAVE_UP_ON_PREREQUISITE)
        )
    return len(orphans)


def reap(
    session: Session, at: datetime | None = None, *, only: ColumnElement[bool] | None = None
) -> int:
    """Jobs whose worker vanished (`running` past `LEASE`): one failed
    attempt each, retried or given up as any failure is. Does not commit."""
    at = at or now()
    stale = session.scalars(
        select(Job)
        .where(Job.state == RUNNING, Job.started_at < at - LEASE, _mine(only))
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        _failed(job, LOST_WORKER, at, retry=True)
    return len(stale)


def claim(
    session: Session,
    worker: str,
    at: datetime | None = None,
    *,
    only: ColumnElement[bool] | None = None,
) -> JobRef | None:
    """Take the next due job for this worker, or None. Commits the claim, so
    the row says `running` and whose it is before any work starts."""
    at = at or now()
    fail_orphans(session, at, only=only)
    reap(session, at, only=only)
    session.commit()
    job = lock_next(session, at, only=only)
    if job is None:
        session.commit()
        return None
    job.state = RUNNING
    job.started_at = at
    job.finished_at = None
    job.attempts = int(job.attempts) + 1
    job.locked_by = worker
    ref = JobRef(
        id=job.id,
        kind=job.kind,
        league_id=job.league_id,
        team_id=job.team_id,
        user_id=job.user_id,
        attempts=job.attempts,
        payload=dict(job.payload or {}),
    )
    session.commit()
    return ref


# ---------------------------------------------------------------------------
# finishing
# ---------------------------------------------------------------------------


def _failed(job: Job, message: str, at: datetime, *, retry: bool) -> None:
    job.last_error = message[:ERROR_LIMIT]
    job.locked_by = None
    if retry and int(job.attempts) < MAX_ATTEMPTS:
        job.state = QUEUED
        job.run_after = at + BACKOFF[min(int(job.attempts), len(BACKOFF)) - 1]
        job.started_at = None
    else:
        job.state = FAILED
        job.finished_at = at


def finish(
    session: Session,
    job_id: int,
    *,
    error: str | None = None,
    note: str | None = None,
    retry: bool = True,
    at: datetime | None = None,
) -> str:
    """Record how a job went; its state afterwards. `error` is a failure,
    already safe to store; `note` is a success's remark (a partial delivery,
    nothing to build), kept in `last_error` so it is seen. Commits."""
    at = at or now()
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        session.commit()
        return FAILED
    if error is None:
        job.state = DONE
        job.finished_at = at
        job.locked_by = None
        job.last_error = note[:ERROR_LIMIT] if note else None
    else:
        _failed(job, error, at, retry=retry)
    state = job.state
    session.commit()
    return state


#: A handler does one job. It returns a note for a success worth remarking
#: on, or None, and raises to fail (a `JobError` in its own words).
Handler = Callable[[sessionmaker[Session], JobRef], str | None]


@dataclass(frozen=True)
class Outcome:
    job: JobRef
    state: str
    message: str | None


def run_next(
    factory: sessionmaker[Session],
    handlers: Mapping[str, Handler],
    *,
    worker: str | None = None,
    at: datetime | None = None,
    only: ColumnElement[bool] | None = None,
) -> Outcome | None:
    """Claim one due job, run it, record the outcome. None when nothing is due."""
    worker = worker or worker_name()
    with factory() as session:
        job = claim(session, worker, at, only=only)
    if job is None:
        return None
    handler = handlers.get(job.kind)
    note: str | None = None
    error: str | None = None
    retry = True
    try:
        if handler is None:
            raise JobError(f"no handler for {job.kind}", retry=False)
        note = handler(factory, job)
    except Exception as failure:  # the job's failure is recorded, never the worker's
        error = safe_error(failure)
        retry = failure.retry if isinstance(failure, JobError) else True
        log.warning("job %s (%s) attempt %s failed: %s", job.id, job.kind, job.attempts, error)
    with factory() as session:
        state = finish(session, job.id, error=error, note=note, retry=retry, at=at)
    if error is None:
        log.info("job %s (%s) done%s", job.id, job.kind, f": {note}" if note else "")
    return Outcome(job, state, error or note)


def last_done(
    session: Session, kind: str, *, user_id: int | None, team_id: int | None, mode: str
) -> datetime | None:
    """When the latest job of this kind for this member and team finished
    well, in this mode: where a member's next digest picks up."""
    return session.scalar(
        select(Job.finished_at)
        .where(
            Job.kind == kind,
            Job.state == DONE,
            Job.user_id == user_id if user_id is not None else Job.user_id.is_(None),
            Job.team_id == team_id if team_id is not None else Job.team_id.is_(None),
            Job.payload["mode"].astext == mode,
        )
        .order_by(Job.finished_at.desc())
        .limit(1)
    )
