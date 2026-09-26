"""comp codes: a season pass the owner hands out, and the pass a payment will write

docs/accounts.md, "The pass" and "Codes". An entitlement is now a season
pass: one `team` row per user covering every team he manages, until its
`valid_until`, never renewing itself. This job writes passes from codes; the
next one writes them from a one-time payment. So:

`entitlements.source` gains `purchase` now, so the payment job is a webhook
and not a migration. `entitlements.note` is the owner's words for a pass he
grants by hand (`scripts/comp_code.py grant`).

`comp_codes` is a code the owner makes: kept in clear (he has to read it back
to send it), `uses_total` and `uses_left`, `valid_until` (when the pass it
writes ends), `redeem_by` (when the code itself stops working, null: never)
and `revoked_at`. The unique constraint on `code` is its index.

`comp_code_redemptions` is one user spending one use, and the pass it wrote.
Unique on (code, user), so a multi-use code cannot be burnt twice by one man.

Downgrade drops both tables and the column, and puts the old check back --
which fails, as it should, while any `purchase` row exists: those are paid
passes, and a downgrade that deleted them would be a refund nobody made.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SOURCES = "source IN ('owner', 'subscription', 'trial', 'comp')"
NEW_SOURCES = "source IN ('owner', 'subscription', 'trial', 'comp', 'purchase')"


def upgrade() -> None:
    op.drop_constraint("ck_entitlements_source", "entitlements", type_="check")
    op.create_check_constraint("ck_entitlements_source", "entitlements", NEW_SOURCES)
    op.add_column("entitlements", sa.Column("note", sa.String(), nullable=True))

    op.create_table(
        "comp_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("note", sa.String(), server_default="", nullable=False),
        sa.Column("uses_total", sa.Integer(), nullable=False),
        sa.Column("uses_left", sa.Integer(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeem_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("uses_total >= 1", name="ck_comp_codes_uses_total"),
        sa.CheckConstraint(
            "uses_left >= 0 AND uses_left <= uses_total", name="ck_comp_codes_uses_left"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_comp_codes_code"),
    )
    op.create_table(
        "comp_code_redemptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), nullable=True),
        sa.Column(
            "redeemed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["code_id"], ["comp_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entitlement_id"], ["entitlements.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_id", "user_id", name="uq_comp_code_redemptions_code_user"),
    )
    op.create_index(
        "ix_comp_code_redemptions_user_id", "comp_code_redemptions", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_comp_code_redemptions_user_id", table_name="comp_code_redemptions")
    op.drop_table("comp_code_redemptions")
    op.drop_table("comp_codes")
    op.drop_column("entitlements", "note")
    op.drop_constraint("ck_entitlements_source", "entitlements", type_="check")
    op.create_check_constraint("ck_entitlements_source", "entitlements", OLD_SOURCES)
