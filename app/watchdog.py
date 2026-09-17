"""Whether the scheduled jobs are still running, and saying so when they stop.

Every job on the VPS records what it did: the ingest and the listener write an
`IngestRun`, the BBM pull writes a `BBMCapture`. A job that stops has no row
to write, which is exactly why nothing complains. This reads those rows, and
the backup directory, and reports what has gone quiet.

Silence is the failure mode that matters. A failed run is visible in
`systemctl --failed` and in the run's own row; a timer that was never enabled,
a unit disabled by hand, a host that stayed down, or a pass that exits 0
without doing anything, all look exactly like a healthy weekend.

WHAT COUNTS AS QUIET

Each check carries its own window, at about three times the interval the job
runs at, so one missed run is not an alarm and two are:

| check | runs | quiet after |
|---|---|---|
| ingest | 09:00 daily | 36 hours |
| listener | 4 passes a day | 12 hours |
| BBM | 09:30 daily | 36 hours |
| backup | 10:00 daily | 36 hours |

The listener's window is the tightest because it is the one job whose missed
hours cannot be recovered: ESPN serves a player's status as of the request,
so an hour not observed is gone (`docs/pickups.md`).

Out of season the listener deliberately runs once a day, so its window widens
to the ingest's when no NBA game is within `IN_SEASON_DAYS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BBMCapture, IngestRun, ProTeamGame
from app.ingest_runs import SUCCEEDED

#: Hours without a successful run before a job counts as quiet.
INGEST_QUIET_HOURS = 36
LISTENER_QUIET_HOURS = 12
BBM_QUIET_HOURS = 36
BACKUP_QUIET_HOURS = 36

#: No NBA game inside this many days means the off season, when the listener
#: snapshots once a day on purpose.
IN_SEASON_DAYS = 14

#: The modes the ingest records under; the listener uses "status".
INGEST_MODES = ("recent", "full", "settings")
LISTENER_MODE = "status"


@dataclass(frozen=True)
class Check:
    name: str
    quiet: bool
    #: What was last seen, and when, in words for the message.
    detail: str


def _age_hours(when: datetime | None, now: datetime) -> float | None:
    if when is None:
        return None
    return (now - when).total_seconds() / 3600


def in_season(session: Session, now: datetime) -> bool:
    """Whether an NBA game falls inside the next `IN_SEASON_DAYS`."""
    horizon = now + timedelta(days=IN_SEASON_DAYS)
    next_game = session.scalar(
        select(func.min(ProTeamGame.game_at)).where(ProTeamGame.game_at >= now)
    )
    return next_game is not None and next_game <= horizon


def _last_run(
    session: Session, modes: tuple[str, ...] | str, now: datetime
) -> tuple[datetime | None, str | None]:
    wanted = (modes,) if isinstance(modes, str) else modes
    last = session.scalar(
        select(IngestRun)
        .where(IngestRun.mode.in_(wanted), IngestRun.status == SUCCEEDED)
        .order_by(IngestRun.started_at.desc())
        .limit(1)
    )
    latest = session.scalar(
        select(IngestRun)
        .where(IngestRun.mode.in_(wanted))
        .order_by(IngestRun.started_at.desc())
        .limit(1)
    )
    return (
        last.started_at if last else None,
        None if latest is None else str(latest.status),
    )


def _said(name: str, when: datetime | None, status: str | None, now: datetime) -> str:
    if when is None:
        return f"{name}: never succeeded"
    hours = _age_hours(when, now) or 0.0
    tail = f", last run {status}" if status and status != SUCCEEDED else ""
    return f"{name}: last succeeded {hours:.0f}h ago ({when:%Y-%m-%d %H:%M} UTC){tail}"


def checks(
    session: Session, *, now: datetime | None = None, backups: Path | None = None
) -> list[Check]:
    """One `Check` per scheduled job, quiet ones included."""
    now = now or datetime.now(UTC)
    out: list[Check] = []

    when, status = _last_run(session, INGEST_MODES, now)
    hours = _age_hours(when, now)
    out.append(
        Check(
            "ingest",
            hours is None or hours > INGEST_QUIET_HOURS,
            _said("ingest", when, status, now),
        )
    )

    when, status = _last_run(session, LISTENER_MODE, now)
    hours = _age_hours(when, now)
    window = LISTENER_QUIET_HOURS if in_season(session, now) else INGEST_QUIET_HOURS
    out.append(
        Check("listener", hours is None or hours > window, _said("listener", when, status, now))
    )

    day = session.scalar(select(func.max(BBMCapture.captured_on)))
    bbm_hours = None if day is None else (now.date() - day).days * 24.0
    out.append(
        Check(
            "bbm",
            bbm_hours is None or bbm_hours > BBM_QUIET_HOURS,
            "bbm: never captured" if day is None else f"bbm: last export {day:%Y-%m-%d}",
        )
    )

    if backups is not None:
        newest = max(
            (p for p in backups.glob("*.dump")), key=lambda p: p.stat().st_mtime, default=None
        )
        age = (
            None
            if newest is None
            else _age_hours(datetime.fromtimestamp(newest.stat().st_mtime, UTC), now)
        )
        out.append(
            Check(
                "backup",
                age is None or age > BACKUP_QUIET_HOURS,
                "backup: none found"
                if newest is None or age is None
                else f"backup: newest {newest.name}, {age:.0f}h old",
            )
        )
    return out


def message(results: list[Check], *, today: date | None = None) -> str | None:
    """What to send, or None when every job is running."""
    quiet = [c for c in results if c.quiet]
    if not quiet:
        return None
    day = today or datetime.now(UTC).date()
    lines = [f"fcp-core: {len(quiet)} job(s) quiet, {day:%a %d %b}", ""]
    lines += [f"  {c.detail}" for c in quiet]
    running = [c for c in results if not c.quiet]
    if running:
        lines += ["", "still running:"] + [f"  {c.detail}" for c in running]
    return "\n".join(lines)
