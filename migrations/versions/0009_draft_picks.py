"""draft picks

The draft that started each season, with auction prices.

Free to collect: ESPN sends the full draft alongside the league itself, so
this costs no request of its own, on any run.

`bid_amount` is the draft budget, a separate pot from the in-season
acquisition budget that `transactions.bid_amount` records.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_picks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("nominating_team_id", sa.Integer(), nullable=True),
        sa.Column("round_num", sa.Integer(), nullable=False),
        sa.Column("round_pick", sa.Integer(), nullable=False),
        sa.Column("bid_amount", sa.Integer(), nullable=True),
        sa.Column("keeper", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["nominating_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "league_season_id", "round_num", "round_pick", name="uq_draft_picks_slot"
        ),
    )


def downgrade() -> None:
    op.drop_table("draft_picks")
