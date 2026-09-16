"""player projection snapshots

ESPN's projection for each player, one row per day the ingest reads the
card. `player_season_stats` overwrites its projection on every ingest, so the
projection a manager saw on the day of a move is otherwise lost; the scoring
work's decision lens needs it (docs/scoring/SPEC.md, ticket S10).

Nothing to backfill: the past days' projections were never kept.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_projection_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("games_played", sa.Float(), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_id", "season", "captured_on", "kind", name="uq_player_projection_snapshots_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("player_projection_snapshots")
