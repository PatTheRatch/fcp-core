"""bbm projections

Basketball Monster's projection exports, captured daily: a capture log per
export per day, and each player's row stored as versions (a new row only when
his line changed, `last_seen` extended otherwise). See `app.draft.bbm_store`.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bbm_captures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("league", sa.String(), nullable=True),
        sa.Column("players", sa.Integer(), nullable=False),
        sa.Column("changed", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season", "value_type", "captured_on", name="uq_bbm_captures_season_type_day"
        ),
    )
    op.create_table(
        "bbm_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_key", sa.String(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("first_seen", sa.Date(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
        sa.Column("row_hash", sa.String(), nullable=False),
        sa.Column("row", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bbm_projections_lookup",
        "bbm_projections",
        ["season", "value_type", "name_key", "last_seen"],
    )


def downgrade() -> None:
    op.drop_index("ix_bbm_projections_lookup", table_name="bbm_projections")
    op.drop_table("bbm_projections")
    op.drop_table("bbm_captures")
