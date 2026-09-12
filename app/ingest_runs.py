"""Recording ingest executions.

Separate from `app.ingest` because it is bookkeeping rather than ingestion,
and because it has to keep working when the ingest itself fails. A run is
opened before ESPN is touched and closed whatever happens, so a crashed
process leaves a row saying "running" rather than leaving no trace.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IngestRun

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

#: Failure text is truncated so a listing stays readable. The full traceback
#: belongs in the job's log, not in a column someone reads at a glance.
ERROR_LIMIT = 500


def _first_line(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}".strip().splitlines()
    return (text[0] if text else type(error).__name__)[:ERROR_LIMIT]


@contextmanager
def record_run(
    factory: sessionmaker[Session], *, espn_league_id: int, season: int, mode: str
) -> Iterator[dict[str, Any]]:
    """Open a run row, yield its detail dict, and close the row on the way out.

    The detail dict is yielded so the caller can fill in counts as it goes.
    Its own session is used throughout, independent of the one doing the
    ingest, so a rolled-back ingest transaction cannot erase the record of
    having tried.
    """
    started = datetime.now(UTC)
    detail: dict[str, Any] = {}

    with factory() as session:
        run = IngestRun(
            espn_league_id=espn_league_id,
            season=season,
            mode=mode,
            status=RUNNING,
            started_at=started,
            detail={},
        )
        session.add(run)
        session.commit()
        run_id = run.id

    try:
        yield detail
    except BaseException as error:
        # Caught broadly on purpose: the row is closed, then the error re-raised
        # untouched, so a KeyboardInterrupt or a crash still leaves a record.
        _close(factory, run_id, FAILED, started, detail, _first_line(error))
        raise
    else:
        _close(factory, run_id, SUCCEEDED, started, detail, None)


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
        run = session.scalar(select(IngestRun).where(IngestRun.id == run_id))
        if run is None:
            return
        run.status = status
        run.finished_at = finished
        run.duration_seconds = (finished - started).total_seconds()
        run.detail = dict(detail)
        run.error = error
        session.commit()


def last_successful_run(session: Session, season: int) -> IngestRun | None:
    return session.scalar(
        select(IngestRun)
        .where(IngestRun.season == season, IngestRun.status == SUCCEEDED)
        .order_by(IngestRun.started_at.desc())
        .limit(1)
    )
