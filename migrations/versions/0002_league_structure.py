"""league structure

Leagues, their per-season settings, and the categories each season scored.
Season-scoped by design: ESPN settings change between years, so every mutable
fact hangs off `league_seasons` rather than `leagues`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leagues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("espn_league_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_league_id"),
    )
    op.create_table(
        "league_seasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("scoring_type", sa.String(), nullable=False),
        sa.Column("team_count", sa.Integer(), nullable=False),
        sa.Column("regular_season_periods", sa.Integer(), nullable=False),
        sa.Column("total_matchup_periods", sa.Integer(), nullable=False),
        sa.Column("playoff_team_count", sa.Integer(), nullable=False),
        sa.Column("playoff_matchup_period_length", sa.Integer(), nullable=False),
        sa.Column("keeper_count", sa.Integer(), nullable=False),
        sa.Column("uses_faab", sa.Boolean(), nullable=False),
        sa.Column("acquisition_budget", sa.Integer(), nullable=False),
        sa.Column("median_scoring", sa.Boolean(), nullable=False),
        sa.Column("trade_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("league_id", "season", name="uq_league_seasons_league_id_season"),
    )
    op.create_table(
        "league_season_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("stat_id", sa.Integer(), nullable=False),
        sa.Column("abbreviation", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_reverse", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "league_season_id", "stat_id", name="uq_league_season_categories_season_stat"
        ),
    )


def downgrade() -> None:
    op.drop_table("league_season_categories")
    op.drop_table("league_seasons")
    op.drop_table("leagues")
