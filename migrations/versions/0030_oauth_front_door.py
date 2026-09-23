"""the OAuth front door: the apps that asked, and the codes they spend

docs/mcp.md, "Adding it to your own Claude or ChatGPT". A league member
cannot be asked to paste a token into a config file, so an app registers
itself (RFC 7591), sends the manager here to sign in and say yes, and spends
a one-time code for a token. The token it receives is an ordinary `bo_` row
in `api_tokens` -- there is no second kind of token and no second answer to
"who may open what".

Two tables, and nothing else:

`oauth_clients` is the app that asked. No secret: a public client proves
itself with PKCE, so there is nothing here to steal. The redirect URIs are
matched exactly, and only `https://` or a loopback address is stored.

`oauth_codes` is the ten minutes between "Allow" and the token. Only the
code's sha256 is kept, as a session cookie's and a sign-in link's are, and
`used_at` makes it single-use: the spend is one UPDATE that matches only an
unused, unexpired row, so two clients racing on one code cannot both win.

Downgrade drops both. Every app then has to register again, and every token
already minted keeps working -- which is right: the tokens are the manager's,
listed and revocable on his Connections page, and they do not belong to this
table.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("client_uri", sa.String(), nullable=True),
        sa.Column(
            "redirect_uris",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_oauth_clients_client_id"),
    )
    op.create_table(
        "oauth_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("client_row_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_row_id"], ["oauth_clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_oauth_codes_code_hash"),
    )
    op.create_index("ix_oauth_codes_expires_at", "oauth_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_codes_expires_at", table_name="oauth_codes")
    op.drop_table("oauth_codes")
    op.drop_table("oauth_clients")
