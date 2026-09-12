"""player stats per scoring period

A box score line per player per scoring period, a scoring period being a
single day. Global rather than league-scoped, like `players`: two leagues
holding the same player share these rows. `season` is part of the key
because scoring period numbers restart each year.

Rows exist for days a player\'s team played and they did not, with `played`
false, so availability is answerable rather than inferred from absence.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_game_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opponent", sa.String(), nullable=True),
        sa.Column("played", sa.Boolean(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=True),
        sa.Column("points", sa.Float(), nullable=True),
        sa.Column("rebounds", sa.Float(), nullable=True),
        sa.Column("offensive_rebounds", sa.Float(), nullable=True),
        sa.Column("defensive_rebounds", sa.Float(), nullable=True),
        sa.Column("assists", sa.Float(), nullable=True),
        sa.Column("steals", sa.Float(), nullable=True),
        sa.Column("blocks", sa.Float(), nullable=True),
        sa.Column("turnovers", sa.Float(), nullable=True),
        sa.Column("personal_fouls", sa.Float(), nullable=True),
        sa.Column("field_goals_made", sa.Float(), nullable=True),
        sa.Column("field_goals_attempted", sa.Float(), nullable=True),
        sa.Column("three_pointers_made", sa.Float(), nullable=True),
        sa.Column("three_pointers_attempted", sa.Float(), nullable=True),
        sa.Column("free_throws_made", sa.Float(), nullable=True),
        sa.Column("free_throws_attempted", sa.Float(), nullable=True),
        sa.Column("raw_totals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_id", "season", "scoring_period", name="uq_player_game_stats_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("player_game_stats")
