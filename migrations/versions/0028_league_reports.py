"""a report that belongs to the league rather than to any team

docs/projected_record.md section 3. `team_reports` holds a team's day, week
and season plans, keyed on `team_id` with a foreign key to `teams`. The
projected standings belong to no team: they are every team's remaining weeks
played against each other, built once a morning and read by the Standings
page, the This week page, every Week page and every digest.

Putting them in `team_reports` would mean either fourteen copies of the same
payload or a nullable foreign key on a table whose whole shape says
otherwise, so they get a table of theirs: one row per (league season, kind,
scoring period), with the same freshness rule `app.reports.fresh` uses -- a
row counts as today's only when it is for today's scoring period and was
built on today's date.

`ck_jobs_kind` is widened for the job that writes it, `project_standings`,
which the morning runs after the status pass and before the per-team
precomputes.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = "ck_jobs_kind"
_KIND_WAS = (
    "kind IN ('ingest', 'status_pass', 'precompute', 'digest', "
    "'injury_backfill', 'injury_pass', "
    "'intake_ingest', 'intake_schedule', 'intake_replacement', 'intake_lane', "
    "'intake_hurdles', 'intake_trades', 'intake_pool', 'intake_done')"
)
_KIND_NOW = (
    "kind IN ('ingest', 'status_pass', 'precompute', 'project_standings', 'digest', "
    "'injury_backfill', 'injury_pass', "
    "'intake_ingest', 'intake_schedule', 'intake_replacement', 'intake_lane', "
    "'intake_hurdles', 'intake_trades', 'intake_pool', 'intake_done')"
)


def upgrade() -> None:
    op.create_table(
        "league_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "league_season_id",
            sa.Integer(),
            sa.ForeignKey("league_seasons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("kind IN ('projected')", name="ck_league_reports_kind"),
        sa.UniqueConstraint(
            "league_season_id",
            "kind",
            "scoring_period",
            name="uq_league_reports_season_kind_period",
        ),
    )
    op.drop_constraint(_KIND, "jobs", type_="check")
    op.create_check_constraint(_KIND, "jobs", sa.text(_KIND_NOW))


def downgrade() -> None:
    # A job of the kind the narrowed CHECK no longer admits would refuse the
    # constraint, so it goes first; the same shape migration 0027 uses.
    op.execute("DELETE FROM jobs WHERE kind = 'project_standings'")
    op.drop_constraint(_KIND, "jobs", type_="check")
    op.create_check_constraint(_KIND, "jobs", sa.text(_KIND_WAS))
    op.drop_table("league_reports")
