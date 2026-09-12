"""daily lineups

Where every player sat, for every team, on every day: the grain that makes
narratives possible, since it distinguishes what a team started from what it
merely held.

Additive. `roster_slots` keeps the weekly picture of who was on a roster
during a matchup period; `daily_lineup_slots` records what the team did with
them day by day. Both are retained deliberately.

Also adds `matchup_periods.first_scoring_period`, completing the true window
for each period now that `League.matchup_ids` is known to be the
authoritative mapping.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_lineup_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("matchup_period_id", sa.Integer(), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("started", sa.Boolean(), nullable=False),
        sa.Column("injured", sa.Boolean(), nullable=False),
        sa.Column("injury_status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["matchup_period_id"], ["matchup_periods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "team_id", "scoring_period", "player_id", name="uq_daily_lineup_slots_key"
        ),
    )
    op.add_column("matchup_periods", sa.Column("first_scoring_period", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("matchup_periods", "first_scoring_period")
    op.drop_table("daily_lineup_slots")
