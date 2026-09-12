"""player season stats

A player's whole season, as ESPN forecast it and as it happened. Free: both
arrive on the player cards already fetched for the daily lines.

The actual totals are partly redundant with summing player_game_stats, and
kept anyway. ESPN omits days from its own cards for some seasons, so its
total and our sum can disagree, and storing both makes that visible.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_season_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("games_played", sa.Float(), nullable=True),
        sa.Column("minutes", sa.Float(), nullable=True),
        sa.Column("points", sa.Float(), nullable=True),
        sa.Column("rebounds", sa.Float(), nullable=True),
        sa.Column("assists", sa.Float(), nullable=True),
        sa.Column("steals", sa.Float(), nullable=True),
        sa.Column("blocks", sa.Float(), nullable=True),
        sa.Column("turnovers", sa.Float(), nullable=True),
        sa.Column("three_pointers_made", sa.Float(), nullable=True),
        sa.Column("field_goals_made", sa.Float(), nullable=True),
        sa.Column("field_goals_attempted", sa.Float(), nullable=True),
        sa.Column("free_throws_made", sa.Float(), nullable=True),
        sa.Column("free_throws_attempted", sa.Float(), nullable=True),
        sa.Column("raw_totals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "season", "kind", name="uq_player_season_stats_key"),
    )


def downgrade() -> None:
    op.drop_table("player_season_stats")
