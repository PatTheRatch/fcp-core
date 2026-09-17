"""Ingest run history and freshness.

The point of these two routes is to make the schedule answerable. Without
them, "is the current season up to date" is a question you can only answer by
poking at the data and guessing.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import SessionDep
from app.api.schemas import IngestHealthOut, IngestRunOut, Page
from app.db.models import IngestRun
from app.espn import current_season
from app.ingest_runs import SUCCEEDED

router = APIRouter(tags=["ingest"])

#: How long the current season may go without a successful ingest before it
#: counts as stale. The scheduled job runs nightly, so a day and a half
#: tolerates one missed run without crying wolf.
STALENESS_THRESHOLD_HOURS = 36

RUN_PAGE_LIMIT = 200

MODE_HELP = "Count only runs of this mode: 'full', 'recent', 'settings' or 'status'"


@router.get("/ingest-runs", summary="Ingest history, newest first")
def list_ingest_runs(
    session: SessionDep,
    season: int | None = Query(default=None, description="Restrict to one season"),
    status: str | None = Query(default=None, description="'running', 'succeeded' or 'failed'"),
    limit: int = Query(default=25, ge=1, le=RUN_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[IngestRunOut]:
    base = select(IngestRun)
    if season is not None:
        base = base.where(IngestRun.season == season)
    if status is not None:
        base = base.where(IngestRun.status == status)

    total = len(session.scalars(base).all())
    runs = session.scalars(
        base.order_by(IngestRun.started_at.desc(), IngestRun.id.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[
            IngestRunOut(
                id=run.id,
                season=run.season,
                mode=run.mode,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_seconds=run.duration_seconds,
                error=run.error,
                detail=run.detail,
            )
            for run in runs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/ingest-runs/health",
    summary="Whether the season now running is being kept current",
)
def get_current_ingest_health(
    session: SessionDep,
    mode: str | None = Query(default=None, description=MODE_HELP),
) -> IngestHealthOut:
    """Freshness of whichever season is running now.

    Deliberately takes no season: asking about a fixed year is how a stale
    schedule hides, since a finished season refreshed nightly looks perfectly
    healthy while the live one goes unrecorded.
    """
    return get_ingest_health(current_season(), session, mode)


@router.get(
    "/ingest-runs/health/{season}",
    summary="Whether a given season's data is still being kept current",
)
def get_ingest_health(
    season: int,
    session: SessionDep,
    mode: str | None = Query(default=None, description=MODE_HELP),
) -> IngestHealthOut:
    """Age of the last successful run, and whether that counts as stale.

    Reports the most recent run's status separately, so a season that is
    fresh but failing right now is visible rather than hidden behind the
    last success.

    With `mode`, only runs of that mode count. The listener's status passes
    are the reason: three a day in season, and a listener that has gone
    quiet would otherwise hide behind a healthy nightly ingest.
    """
    successes = select(IngestRun).where(IngestRun.season == season, IngestRun.status == SUCCEEDED)
    everything = select(IngestRun).where(IngestRun.season == season)
    if mode is not None:
        successes = successes.where(IngestRun.mode == mode)
        everything = everything.where(IngestRun.mode == mode)
    last_success = session.scalar(successes.order_by(IngestRun.started_at.desc()).limit(1))
    latest = session.scalar(everything.order_by(IngestRun.started_at.desc()).limit(1))

    hours: float | None = None
    if last_success is not None:
        age = datetime.now(UTC) - last_success.started_at
        hours = age.total_seconds() / 3600

    return IngestHealthOut(
        season=season,
        mode=mode,
        last_success_at=last_success.started_at if last_success else None,
        hours_since_last_success=hours,
        last_status=latest.status if latest else None,
        stale=hours is None or hours > STALENESS_THRESHOLD_HOURS,
        staleness_threshold_hours=STALENESS_THRESHOLD_HOURS,
    )
