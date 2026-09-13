"""roster rules and primary position

The league's own roster rules, per season, and the primary position that
position limits count against.

Both were previously missing and both move between seasons. The starting
lineup had been inferred by counting slots in box scores, which happened to
be right. The centre cap was missed entirely: three in 2025 and 2026, four
in 2027. Injured reserve appears for the first time in 2027.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "league_seasons",
        sa.Column(
            "lineup_slots",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "league_seasons", sa.Column("bench_slots", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "league_seasons",
        sa.Column("injured_reserve_slots", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "league_seasons",
        sa.Column(
            "position_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column("player_season_stats", sa.Column("primary_position", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("player_season_stats", "primary_position")
    op.drop_column("league_seasons", "position_limits")
    op.drop_column("league_seasons", "injured_reserve_slots")
    op.drop_column("league_seasons", "bench_slots")
    op.drop_column("league_seasons", "lineup_slots")
