"""per category matchup detail

What each team posted in each matchup, per statistic: the categories the
league scores, plus the component stats (FGM, FGA, FTM, FTA) that sit behind
FG% and FT%, so a percentage can be recomputed rather than trusted.

A row is a scored category when `league_season_category_id` is set. That is
the reliable test, not `result`, because a bye reports real values with a
null result on every statistic.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matchup_team_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("matchup_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("abbreviation", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("league_season_category_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["league_season_category_id"], ["league_season_categories.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["matchup_id"], ["matchups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "matchup_id", "team_id", "abbreviation", name="uq_matchup_team_stats_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("matchup_team_stats")
