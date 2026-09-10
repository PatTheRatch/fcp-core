"""initial

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Intentionally empty: the schema starts from nothing. Domain tables arrive
    # with the code that needs them.
    pass


def downgrade() -> None:
    pass
