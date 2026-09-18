"""projection sets

A manager's own projections, uploaded (docs/projection_sources.md). BBM's
numbers are paid and stay with the member who fetched them, so anybody else
who uses this brings his own set and the room is loaded from that.

One row per set and one per player in it, per game, with makes and attempts
for both percentages because a percentage alone cannot be rebuilt into a
roster's percentage. Nothing to backfill: there are no uploads yet.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projection_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("source_note", sa.String(), nullable=False),
        sa.Column("column_map", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "projection_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("name_key", sa.String(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("games", sa.Float(), nullable=False),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("rebounds", sa.Float(), nullable=False),
        sa.Column("assists", sa.Float(), nullable=False),
        sa.Column("steals", sa.Float(), nullable=False),
        sa.Column("blocks", sa.Float(), nullable=False),
        sa.Column("three_pointers_made", sa.Float(), nullable=False),
        sa.Column("turnovers", sa.Float(), nullable=False),
        sa.Column("field_goals_made", sa.Float(), nullable=False),
        sa.Column("field_goals_attempted", sa.Float(), nullable=False),
        sa.Column("free_throws_made", sa.Float(), nullable=False),
        sa.Column("free_throws_attempted", sa.Float(), nullable=False),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("team", sa.String(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["set_id"], ["projection_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "name_key", name="uq_projection_rows_name"),
    )
    op.create_index("ix_projection_rows_set_player", "projection_rows", ["set_id", "player_id"])


def downgrade() -> None:
    op.drop_index("ix_projection_rows_set_player", table_name="projection_rows")
    op.drop_table("projection_rows")
    op.drop_table("projection_sets")
