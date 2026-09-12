"""ingest runs

A record of every ingest execution, so a schedule can be checked rather than
assumed. Intentionally free of foreign keys: a run that fails before writing
anything, or before reaching ESPN at all, still has to be recordable.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("espn_league_id", sa.BigInteger(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # The listing this table exists for: newest runs, per season.
    op.create_index(
        "ix_ingest_runs_season_started",
        "ingest_runs",
        ["season", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_runs_season_started", table_name="ingest_runs")
    op.drop_table("ingest_runs")
