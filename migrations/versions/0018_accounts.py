"""accounts

Step 1 of docs/product.md: people who can sign in (by a magic link, no
password), their one-time links and their sessions, what they are entitled
to, and which teams they manage (docs/accounts.md).

Tokens are stored only as sha256 hashes, so neither table signs anybody in
if it is read. `team_managers` is the minimal form of step 2's team claims:
the team check reads its verified rows today, and invites and claims will
grow on it (its `state` already has `pending` and `rejected`).

Nothing to backfill. In single mode the owner's user, entitlement and team
rows are written on first use (`app.accounts.ensure_owner`).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-19
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        _created(),
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "sign_in_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        _created(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_path", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_sign_in_tokens_user_id", "sign_in_tokens", ["user_id"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        _created(),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "entitlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.CheckConstraint("tier IN ('team')", name="ck_entitlements_tier"),
        sa.CheckConstraint(
            "source IN ('owner', 'subscription', 'trial', 'comp')",
            name="ck_entitlements_source",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entitlements_user_id", "entitlements", ["user_id"])
    op.create_index(
        "uq_entitlements_one_owner",
        "entitlements",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("source = 'owner'"),
    )
    op.create_table(
        "team_managers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), server_default="pending", nullable=False),
        sa.Column("how", sa.String(), nullable=True),
        _created(),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'verified', 'rejected')", name="ck_team_managers_state"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "team_id", name="uq_team_managers_user_team"),
    )
    op.create_index("ix_team_managers_team_id", "team_managers", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_team_managers_team_id", table_name="team_managers")
    op.drop_table("team_managers")
    op.drop_index("uq_entitlements_one_owner", table_name="entitlements")
    op.drop_index("ix_entitlements_user_id", table_name="entitlements")
    op.drop_table("entitlements")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_sign_in_tokens_user_id", table_name="sign_in_tokens")
    op.drop_table("sign_in_tokens")
    op.drop_table("users")
