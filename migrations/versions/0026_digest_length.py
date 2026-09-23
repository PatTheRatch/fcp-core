"""how long a member wants his digest

One column on `digest_subscriptions`: "compact" (the default) or "full".

**Compact by default, for everybody, including the rows that already exist.**
The morning email's job is "is there anything to do today?", answered in ten
seconds with a link for the rest (docs/jobs.md, "The two forms"); the long
form is now the option. The server default and the backfill are both
`compact`, so a member who has never had an opinion gets the short one and a
member who had a row before this does too.

A column rather than a key in `topics`, because it is not a topic: it says
how the message he asked for is written, not whether a section is in it.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_digest_subscriptions_length"


def upgrade() -> None:
    op.add_column(
        "digest_subscriptions",
        sa.Column("length", sa.String(), server_default=sa.text("'compact'"), nullable=False),
    )
    # The two this version has, so a length nobody recognises cannot be
    # written even by hand; `app.subscriptions.clean_length` reads it back.
    op.create_check_constraint(_CHECK, "digest_subscriptions", "length IN ('compact', 'full')")


def downgrade() -> None:
    op.drop_constraint(_CHECK, "digest_subscriptions", type_="check")
    op.drop_column("digest_subscriptions", "length")
