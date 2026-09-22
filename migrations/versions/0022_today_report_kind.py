"""a third stored report kind: today's lineup

`team_reports.kind` was constrained to 'stream' and 'season'. The day's
lineup (`app.pickups.today`, docs/pickups.md section 4.3) is a third report
the morning precompute builds and the week page and the digest read, so the
CHECK is widened to admit it.

Nothing else changes: no column, no index, no row. Downgrade deletes the
'today' rows before narrowing the CHECK again, because a constraint cannot
be added to a table that already breaks it; they are a cache the precompute
rebuilds the next morning, so nothing is lost by deleting them.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_team_reports_kind"
_WAS = "kind IN ('stream', 'season')"
_NOW = "kind IN ('stream', 'season', 'today')"


def upgrade() -> None:
    op.drop_constraint(_NAME, "team_reports", type_="check")
    op.create_check_constraint(_NAME, "team_reports", _NOW)


def downgrade() -> None:
    op.execute("DELETE FROM team_reports WHERE kind = 'today'")
    op.drop_constraint(_NAME, "team_reports", type_="check")
    op.create_check_constraint(_NAME, "team_reports", _WAS)
