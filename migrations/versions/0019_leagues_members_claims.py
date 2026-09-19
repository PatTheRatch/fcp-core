"""leagues, members and claims

Step 2 of docs/product.md: the ESPN login that reads a league (sealed with
FCP_SECRETS_KEY, app/secrets_box.py), who is a member of which league,
invites into a league, a member's own SWID for verifying his claims, and
the claims themselves grown on step 1's `team_managers` (who decided a
claim, when, and a constraint on how it was verified).

The one backfill: every user with a verified team claim becomes a `member`
of that team's league, because the league check now reads `memberships`
and a claim alone no longer opens a league. On the VPS that is the owner's
own claims only, and `app.accounts.ensure_owner` then makes him the league's
`owner`.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-19
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created(name: str = "created_at") -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column("team_managers", sa.Column("decided_by", sa.Integer(), nullable=True))
    op.add_column(
        "team_managers", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "team_managers_decided_by_fkey",
        "team_managers",
        "users",
        ["decided_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_team_managers_how",
        "team_managers",
        "how IS NULL OR how IN ('owner_guid', 'approved', 'owner')",
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        _created("joined_at"),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "league_id", name="uq_memberships_user_league"),
    )
    op.create_index("ix_memberships_league_id", "memberships", ["league_id"])

    op.create_table(
        "league_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(), server_default="espn", nullable=False),
        sa.Column("sealed_credentials", sa.Text(), nullable=True),
        sa.Column("league_name", sa.String(), nullable=True),
        _created(),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("ingest_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("platform IN ('espn')", name="ck_league_connections_platform"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_league_connections_user_id", "league_connections", ["user_id"])
    op.create_index(
        "uq_league_connections_one_active",
        "league_connections",
        ["league_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("token_hash", sa.String(), nullable=False),
        _created(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_invites_league_id", "invites", ["league_id"])

    op.create_table(
        "user_espn_identities",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sealed_swid", sa.Text(), nullable=False),
        sa.Column("swid_hash", sa.String(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("swid_hash"),
    )

    op.execute(
        """
        INSERT INTO memberships (user_id, league_id, role)
        SELECT DISTINCT tm.user_id, ls.league_id, 'member'
        FROM team_managers tm
        JOIN teams t ON t.id = tm.team_id
        JOIN league_seasons ls ON ls.id = t.league_season_id
        WHERE tm.state = 'verified'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("user_espn_identities")
    op.drop_index("ix_invites_league_id", table_name="invites")
    op.drop_table("invites")
    op.drop_index("uq_league_connections_one_active", table_name="league_connections")
    op.drop_index("ix_league_connections_user_id", table_name="league_connections")
    op.drop_table("league_connections")
    op.drop_index("ix_memberships_league_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_constraint("ck_team_managers_how", "team_managers", type_="check")
    op.drop_constraint("team_managers_decided_by_fkey", "team_managers", type_="foreignkey")
    op.drop_column("team_managers", "decided_at")
    op.drop_column("team_managers", "decided_by")
