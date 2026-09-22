"""Stored reports: a team's day, week and season plans, built in the morning.

A cold report takes twenty to forty seconds. The morning precompute (the
`precompute` job, app/job_kinds.py) builds each claimed team's three reports
for the day and keeps them here; the pickup routes and the pages read the
stored row when it is fresh and build live only when it is not
(app/api/pickups.py). This is `scripts/warm_pages.py` grown up: the answer
lives in the database, not in one API process's memory, so it survives a
restart and is there for the digest too.

FRESH

A row is for one scoring period, and is fresh only when it is for the
scoring period being asked about **and** was built on today's date. The
second half matters before opening night and after the last day, when every
calendar day maps to the same scoring period: a row built last week for
"day 1" is last week's roster and wire.

The payload is exactly what the route would have answered, as JSON, so a
stored answer and a live one are the same shape.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import TeamReport

STREAM = "stream"
SEASON = "season"
#: Who starts today (`app.pickups.today`). A third kind beside the two, and
#: the cheapest of them -- one day rather than a whole wire -- but it is read
#: on every load of the week page and by the morning digest, so it is built
#: once with the others rather than on each reader's clock.
TODAY = "today"
KINDS = (STREAM, SEASON, TODAY)


def store(
    session: Session,
    team_pk: int,
    kind: str,
    scoring_period: int,
    payload: dict[str, Any],
    *,
    built_at: datetime | None = None,
) -> None:
    """Keep a built report, replacing any earlier one for the same period.
    Does not commit."""
    if kind not in KINDS:
        raise ValueError(f"unknown report kind {kind!r}")
    at = built_at or datetime.now(UTC)
    written = insert(TeamReport).values(
        team_id=team_pk, kind=kind, scoring_period=scoring_period, built_at=at, payload=payload
    )
    session.execute(
        written.on_conflict_do_update(
            constraint="uq_team_reports_team_kind_period",
            set_={"built_at": written.excluded.built_at, "payload": written.excluded.payload},
        )
    )


def fresh(
    session: Session, team_pk: int, kind: str, scoring_period: int, *, on: date
) -> TeamReport | None:
    """The stored report for this period, if it was built on `on` (a date,
    in the same clock the caller turned into `scoring_period`)."""
    row = session.scalar(
        select(TeamReport).where(
            TeamReport.team_id == team_pk,
            TeamReport.kind == kind,
            TeamReport.scoring_period == scoring_period,
        )
    )
    if row is None:
        return None
    built = row.built_at if row.built_at.tzinfo else row.built_at.replace(tzinfo=UTC)
    # The VPS runs on UTC, so its `date.today()` and this are the same day.
    return row if built.astimezone(UTC).date() >= on else None
