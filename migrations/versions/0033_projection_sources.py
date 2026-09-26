"""projection sources: the mapping a set was stored with, composites, and where a mark was made

docs/projection_sources.md. The draft plan page gains SOURCES: every pool a
manager may plan on, an upload with a mapping he can fix, and a composite
built from other sources with weights.

`projection_sets` gains:

- `kind`: `upload` (a file he brought) or `composite` (rows worked out from
  other sources by `app.projections.composite`). Every existing set is an
  upload.
- `mapping`: the mapping the set was stored with -- which column each field
  was read from, and the per-game / totals basis if he forced one -- so a
  re-upload under the same name next week is offered it first, "as last
  time". `column_map` stays what it was: how the file was actually read.
- `recipe`: a composite's inputs and weights, `[{source, weight}]` with
  `source` "bbm", "espn" or a set id. Null for an upload.
- `built_from`: what a composite's rows were worked out from (the BBM
  capture date, each input set's upload time, a fingerprint of ESPN's
  projections) and when, so it is rebuilt when an input changes and not
  otherwise. Null for an upload.

and a set's name is unique per owner and season: a source is named, and a
re-upload under its name replaces its rows in place (the id is kept, so a
composite that uses it rebuilds). Any duplicate names already stored are
renamed "name (2)", "name (3)" by age first, so the index can be made.

`projection_rows` gains the optional fields a file may carry and the
composite averages: `minutes` (per game), `value` (the source's own dollar
value) and `injury` (its note).

`draft_plan_marks` gains `source`: the pool in view when the mark was last
written ("bbm", "espn", "upload:7", "composite:9"). The marks are per team,
not per source -- a going price is his opinion of the room, whatever pool the
model reads -- but the drawer says when one was made on another pool.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = "kind IN ('upload', 'composite')"


def upgrade() -> None:
    op.add_column(
        "projection_sets",
        sa.Column("kind", sa.String(), server_default="upload", nullable=False),
    )
    op.create_check_constraint("ck_projection_sets_kind", "projection_sets", _KIND)
    op.add_column(
        "projection_sets",
        sa.Column("mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "projection_sets",
        sa.Column("recipe", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "projection_sets",
        sa.Column("built_from", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # A name is the source: make any stored twins distinct before the index.
    op.execute(
        """
        UPDATE projection_sets AS s
           SET name = s.name || ' (' || d.n || ')'
          FROM (
                SELECT id, row_number() OVER (
                         PARTITION BY owner, season, name ORDER BY uploaded_at, id
                       ) AS n
                  FROM projection_sets
               ) AS d
         WHERE d.id = s.id AND d.n > 1
        """
    )
    op.create_unique_constraint(
        "uq_projection_sets_owner_season_name", "projection_sets", ["owner", "season", "name"]
    )
    op.add_column("projection_rows", sa.Column("minutes", sa.Float(), nullable=True))
    op.add_column("projection_rows", sa.Column("value", sa.Float(), nullable=True))
    op.add_column("projection_rows", sa.Column("injury", sa.String(), nullable=True))
    op.add_column("draft_plan_marks", sa.Column("source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("draft_plan_marks", "source")
    op.drop_column("projection_rows", "injury")
    op.drop_column("projection_rows", "value")
    op.drop_column("projection_rows", "minutes")
    op.drop_constraint("uq_projection_sets_owner_season_name", "projection_sets", type_="unique")
    op.execute("DELETE FROM projection_sets WHERE kind = 'composite'")
    op.drop_column("projection_sets", "built_from")
    op.drop_column("projection_sets", "recipe")
    op.drop_column("projection_sets", "mapping")
    op.drop_constraint("ck_projection_sets_kind", "projection_sets", type_="check")
    op.drop_column("projection_sets", "kind")
