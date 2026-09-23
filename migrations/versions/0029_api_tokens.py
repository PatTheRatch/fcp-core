"""a manager's own machine tokens

docs/mcp.md. A co-manager that talks to a model reads the site's routes over
MCP, and it has to be somebody when it does. A cookie is a browser's; the
service token in `.env` is the owner's machine and is configured rather than
minted. So a manager makes his own, on the account page: a name, a token
shown once, and a row that keeps only its sha256.

The row carries no scope. A token acts as its owner through the same checks
every route declares (`app.api.access.resolve_viewer`), so it sees his
leagues and his teams' plans and nothing else. A scope nobody can see is a
promise nobody can check.

Downgrade drops the table, and every token minted stops working -- which is
what a revoked token does anyway.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
