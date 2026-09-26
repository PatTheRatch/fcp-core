"""draft plans: what a manager keeps of his pre-auction plan

docs/draft_plan.md. The plan page proposes -- a spending ladder, a ceiling
per man, the model's lists -- and the manager edits: his own bid-up-to per
man, a tag per man, the ladder's amounts, a note. What he keeps is what the
draft room shows him on the night, so it lives in the database, per team.

`draft_plans` is one row per team (a `teams` row is already one team in one
season, so unique on `team_id` is unique per team-season): the ladder he set
(JSON, the amount per place largest first, or null for the model's), his
notes, his fan team (an NBA abbreviation, or null for none: the plan's FAN
section), and who last wrote it.

`draft_plan_marks` is one row per man he marked: his two figures, both
null for the model's -- `going_price` (what he thinks the room will pay,
which replaces the model's going price for that man in the build) and
`bid_up_to` (his own ceiling: it caps what the plan pays for him, and it is
what the room holds up on the night) -- a `tag` (`target`, `let_go`,
`nominate`, `ir`, `must` or `none`) and a note. `player_id` is the room's
own id -- an ESPN player id, or the negative synthetic id a BBM rookie is
boarded under (`app.player_names.synthetic_id`) -- so it is not a foreign
key.

`team_reports.kind` gains `draft_plan`: the built plan is kept there the
way the week report is, keyed on its pool (docs/draft_plan.md, "The cache").

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = "ck_team_reports_kind"
_KIND_WAS = "kind IN ('stream', 'season', 'today')"
_KIND_NOW = "kind IN ('stream', 'season', 'today', 'draft_plan')"
TAGS = "tag IN ('target', 'let_go', 'nominate', 'ir', 'must', 'none')"


def upgrade() -> None:
    op.create_table(
        "draft_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("ladder", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("fan_team", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", name="uq_draft_plans_team"),
    )
    op.create_table(
        "draft_plan_marks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("going_price", sa.Integer(), nullable=True),
        sa.Column("bid_up_to", sa.Integer(), nullable=True),
        sa.Column("tag", sa.String(), server_default="none", nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(TAGS, name="ck_draft_plan_marks_tag"),
        sa.CheckConstraint("bid_up_to IS NULL OR bid_up_to >= 1", name="ck_draft_plan_marks_bid"),
        sa.CheckConstraint(
            "going_price IS NULL OR going_price >= 1", name="ck_draft_plan_marks_going"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["draft_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "player_id", name="uq_draft_plan_marks_plan_player"),
    )
    op.drop_constraint(_KIND, "team_reports", type_="check")
    op.create_check_constraint(_KIND, "team_reports", _KIND_NOW)


def downgrade() -> None:
    op.execute("DELETE FROM team_reports WHERE kind = 'draft_plan'")
    op.drop_constraint(_KIND, "team_reports", type_="check")
    op.create_check_constraint(_KIND, "team_reports", _KIND_WAS)
    op.drop_table("draft_plan_marks")
    op.drop_table("draft_plans")
