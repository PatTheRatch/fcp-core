"""jobs, stored reports and members' channels

Step 4 of docs/product.md (docs/jobs.md): the job table the worker reads,
the reports the morning precompute stores for each claimed team, and each
member's own notification channels, their targets sealed with
FCP_SECRETS_KEY.

Additive only: three new tables, nothing existing is altered, so the
scheduled scripts that run today read exactly what they read before.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("depends_on", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("state", sa.String(), server_default="queued", nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('ingest', 'status_pass', 'precompute', 'digest')", name="ck_jobs_kind"
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed')", name="ck_jobs_state"
        ),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
    )
    op.create_index("ix_jobs_state_run_after", "jobs", ["state", "run_after"])

    op.create_table(
        "team_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("kind IN ('stream', 'season')", name="ck_team_reports_kind"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "team_id", "kind", "scoring_period", name="uq_team_reports_team_kind_period"
        ),
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("sealed_target", sa.Text(), nullable=True),
        sa.Column("masked_target", sa.String(), nullable=False),
        sa.Column("verify_hash", sa.String(), nullable=True),
        sa.Column("verify_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('email', 'telegram', 'ntfy')", name="ck_notification_channels_kind"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("verify_hash"),
    )
    op.create_index("ix_notification_channels_user_id", "notification_channels", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notification_channels_user_id", table_name="notification_channels")
    op.drop_table("notification_channels")
    op.drop_table("team_reports")
    op.drop_index("ix_jobs_state_run_after", table_name="jobs")
    op.drop_table("jobs")
