"""the NBA's official injury reports, kept point-in-time

docs/injuries.md. ESPN serves a player's status only as of the request, so
for a played season the tool cannot say who was Out on a given morning. The
league publishes its own team-submitted reports as timestamped PDF snapshots
from 2021-22 on, and this is where they land.

* `injury_reports` -- one row per (snapshot, player line). Rows are inserted
  and never updated: an evening report correcting a morning one is a second
  row, not an edit, which is the whole point. Unique on (reported_at,
  game_date, team, player_name_raw), so re-running a backfill inserts
  nothing.
* `injury_report_runs` -- a sibling of `ingest_runs`, which cannot be used
  because it is keyed on an ESPN league and these reports belong to none.

Additive only: nothing existing is touched, and downgrade drops exactly what
upgrade adds.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "injury_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("game_time", sa.String(), nullable=True),
        sa.Column("matchup", sa.String(), nullable=True),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("pro_team_id", sa.Integer(), nullable=True),
        sa.Column("player_name_raw", sa.String(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), server_default="nba_official", nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reported_at", "game_date", "team", "player_name_raw", name="uq_injury_reports_key"
        ),
    )
    op.create_index(
        "ix_injury_reports_player_reported", "injury_reports", ["player_id", "reported_at"]
    )
    op.create_index(
        "ix_injury_reports_game_date_reported", "injury_reports", ["game_date", "reported_at"]
    )

    op.create_table(
        "injury_report_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_injury_report_runs_season_started", "injury_report_runs", ["season", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_injury_report_runs_season_started", table_name="injury_report_runs")
    op.drop_table("injury_report_runs")
    op.drop_index("ix_injury_reports_game_date_reported", table_name="injury_reports")
    op.drop_index("ix_injury_reports_player_reported", table_name="injury_reports")
    op.drop_table("injury_reports")
