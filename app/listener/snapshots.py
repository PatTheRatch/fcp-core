"""Finding the most recent status snapshot for each player.

One query, shared. The pass needs the rows to diff against, the API needs
them as a subquery to filter events by roster, and the digest needs both.
Three copies of a group-by-max is how they drift apart.
"""

from datetime import datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.db.models import PlayerStatusSnapshot


def _latest_pairs(season: int) -> Select[tuple[int, datetime]]:
    return (
        select(
            PlayerStatusSnapshot.player_id,
            func.max(PlayerStatusSnapshot.observed_at).label("observed_at"),
        )
        .where(PlayerStatusSnapshot.season == season)
        .group_by(PlayerStatusSnapshot.player_id)
    )


def latest_snapshot_ids(season: int) -> Select[tuple[int]]:
    """Ids of each player's most recent snapshot this season, as a subquery."""
    latest = _latest_pairs(season).subquery()
    return select(PlayerStatusSnapshot.id).join(
        latest,
        and_(
            PlayerStatusSnapshot.player_id == latest.c.player_id,
            PlayerStatusSnapshot.observed_at == latest.c.observed_at,
        ),
    )


def latest_snapshots(session: Session, season: int) -> dict[int, PlayerStatusSnapshot]:
    """Each player's most recent snapshot this season, keyed on our player id."""
    rows = session.scalars(
        select(PlayerStatusSnapshot).where(PlayerStatusSnapshot.id.in_(latest_snapshot_ids(season)))
    ).all()
    return {row.player_id: row for row in rows}
