"""what a member wants to hear about, per league

One row per (member, league): the named topics he has switched on
(`app.subscriptions`), and whether he wants the morning digest and the alerts
between digests at all.

`topics` is a JSONB map of topic name to on, rather than a column each. The
topics change as the product grows -- the trade block and the projected
finish are named in the list and are not built yet -- and a topic should not
need a migration to appear or a backfill to default. `morning` and `alerts`
are columns because they are not topics: they say whether a message is sent
at all.

**Nothing is backfilled.** No row means the defaults (lineup, my moves, my
team, my opponent, trades and standings on; the league-wide transaction and
injury feeds off), so every member who existed before this already has a
sensible subscription and a row appears the first time he changes one.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "digest_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("morning", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("alerts", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "league_id", name="uq_digest_subscriptions_user_league"),
    )
    op.create_index("ix_digest_subscriptions_user_id", "digest_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_digest_subscriptions_user_id", table_name="digest_subscriptions")
    op.drop_table("digest_subscriptions")
