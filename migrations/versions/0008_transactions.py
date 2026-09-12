"""transactions

Waiver claims, free agent pickups and trades, with the players each moved.

Failed and cancelled moves are kept, not filtered. A losing waiver bid
records who wanted a player and what they offered, which no successful claim
reveals on its own.

ESPN's FUTURE_ROSTER type is excluded: it is daily lineup shuffling, the bulk
of what the endpoint returns, and already recorded properly in
`daily_lineup_slots`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("espn_transaction_id", sa.String(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bid_amount", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_transaction_id", name="uq_transactions_espn_id"),
    )
    op.create_index(
        "ix_transactions_season_period",
        "transactions",
        ["league_season_id", "scoring_period"],
        unique=False,
    )
    op.create_table(
        "transaction_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(), nullable=False),
        sa.Column("from_team_id", sa.Integer(), nullable=True),
        sa.Column("to_team_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["from_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_id", "player_id", "item_type", name="uq_transaction_items_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("transaction_items")
    op.drop_index("ix_transactions_season_period", table_name="transactions")
    op.drop_table("transactions")
