"""draft settings

The auction budget, which is NOT the acquisition budget already stored.
`acquisitionSettings.acquisitionBudget` is the FAAB pot and reads 100 in
every season; `draftSettings.auctionBudget` is what a manager spends at the
draft and reads 200. Optimising against the wrong one halves the budget and
silently produces a roster nobody could have bought.

Also the draft type, the seconds allowed per selection and the nomination
order, all of which a live draft room needs and none of which `espn_api`
exposes: they come from the raw mSettings payload.

The backfill takes the budget from what teams actually spent, which is 200
in all eight drafted seasons. A season with no draft yet stays at zero until
it is ingested from ESPN, and the draft code treats zero as unknown rather
than as a budget.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "league_seasons",
        sa.Column("auction_budget", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("league_seasons", sa.Column("draft_type", sa.String(), nullable=True))
    op.add_column("league_seasons", sa.Column("seconds_per_pick", sa.Integer(), nullable=True))
    op.add_column("league_seasons", sa.Column("drafted_at", sa.DateTime(timezone=True)))
    op.add_column(
        "league_seasons",
        sa.Column(
            "draft_order",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )

    # Every drafted season spent the same amount per team, so the maximum is
    # the budget. Re-ingesting replaces this with what ESPN reports.
    op.execute(
        """
        UPDATE league_seasons ls
        SET auction_budget = spend.budget
        FROM (
            SELECT league_season_id, max(paid) AS budget
            FROM (
                SELECT league_season_id, team_id, sum(bid_amount) AS paid
                FROM draft_picks
                WHERE team_id IS NOT NULL AND bid_amount IS NOT NULL
                GROUP BY league_season_id, team_id
            ) per_team
            GROUP BY league_season_id
        ) spend
        WHERE spend.league_season_id = ls.id AND spend.budget > 0
        """
    )


def downgrade() -> None:
    op.drop_column("league_seasons", "draft_order")
    op.drop_column("league_seasons", "drafted_at")
    op.drop_column("league_seasons", "seconds_per_pick")
    op.drop_column("league_seasons", "draft_type")
    op.drop_column("league_seasons", "auction_budget")
