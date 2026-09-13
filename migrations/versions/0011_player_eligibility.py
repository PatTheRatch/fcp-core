"""player eligibility

The lineup slots each player may occupy, per season, from the player card.
Needed so the optimizer can refuse a roster nobody could actually field: a
roster of thirteen centres passed it before this.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player_season_stats",
        # Existing rows get an empty list. Without a server default a NOT NULL
        # column cannot be added to a populated table, and the first attempt at
        # this migration failed exactly that way while reporting success.
        sa.Column(
            "eligible_slots",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("player_season_stats", "eligible_slots")
