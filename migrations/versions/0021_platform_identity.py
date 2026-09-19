"""platform-neutral identity

Step 6 of docs/product.md (docs/platforms.md): every ESPN id gets a
platform-neutral twin, so a second platform (Yahoo, Fantrax, Sleeper) has
somewhere to land in the same tables.

* `leagues` and `owners` gain `platform` (default 'espn') and a text
  `platform_*_id`, unique per platform.
* `teams` and `transactions` gain a text `platform_*_id`, unique within their
  league season. Their platform is their league's, so it is not repeated.
* `player_platform_ids` maps one canonical `players` row to its id on each
  platform.

Every new id is backfilled from the ESPN one as text. A CHECK on each table
says that a row carrying an ESPN id carries the same id as its platform id
(and, where the row has one, the platform 'espn'), so the two can never
drift. The ESPN columns stay NOT NULL and stay the keys of every URL; a
non-ESPN row will need them relaxed first, which the CHECKs already allow
(a NULL ESPN id passes them).

Additive only: nothing existing is altered or dropped, and downgrade drops
exactly what upgrade adds.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, espn column, platform column, unique columns, has its own platform column)
_TWINS = (
    ("leagues", "espn_league_id", "platform_league_id", ("platform", "platform_league_id"), True),
    ("owners", "espn_owner_id", "platform_owner_id", ("platform", "platform_owner_id"), True),
    (
        "teams",
        "espn_team_id",
        "platform_team_id",
        ("league_season_id", "platform_team_id"),
        False,
    ),
    (
        "transactions",
        "espn_transaction_id",
        "platform_transaction_id",
        ("league_season_id", "platform_transaction_id"),
        False,
    ),
)


def upgrade() -> None:
    for table, espn_column, platform_column, unique, has_platform in _TWINS:
        if has_platform:
            op.add_column(
                table,
                sa.Column("platform", sa.String(), server_default="espn", nullable=False),
            )
            op.create_check_constraint(f"ck_{table}_platform", table, "platform IN ('espn')")
        op.add_column(table, sa.Column(platform_column, sa.String(), nullable=True))
        op.execute(f"UPDATE {table} SET {platform_column} = {espn_column}::text")
        op.alter_column(table, platform_column, nullable=False)
        op.create_unique_constraint(f"uq_{table}_{platform_column}", table, list(unique))
        same = f"{platform_column} = {espn_column}::text"
        if has_platform:
            same = f"{espn_column} IS NULL OR (platform = 'espn' AND {same})"
        op.create_check_constraint(f"ck_{table}_espn_id", table, same)

    op.create_table(
        "player_platform_ids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("platform_player_id", sa.String(), nullable=False),
        sa.CheckConstraint("platform IN ('espn')", name="ck_player_platform_ids_platform"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform", "platform_player_id", name="uq_player_platform_ids_platform_id"
        ),
        sa.UniqueConstraint("player_id", "platform", name="uq_player_platform_ids_player"),
    )
    op.execute(
        "INSERT INTO player_platform_ids (player_id, platform, platform_player_id) "
        "SELECT id, 'espn', espn_player_id::text FROM players ORDER BY id"
    )


def downgrade() -> None:
    op.drop_table("player_platform_ids")
    for table, _espn_column, platform_column, _unique, has_platform in reversed(_TWINS):
        op.drop_constraint(f"ck_{table}_espn_id", table, type_="check")
        op.drop_constraint(f"uq_{table}_{platform_column}", table, type_="unique")
        op.drop_column(table, platform_column)
        if has_platform:
            op.drop_constraint(f"ck_{table}_platform", table, type_="check")
            op.drop_column(table, "platform")
