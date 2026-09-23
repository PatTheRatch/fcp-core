"""each league's own measured numbers, and a priority on the queue

docs/intake.md. Until now every number the recommenders lean on was a
constant, measured once on Full Court Press and typed into the module that
used it: `TYPICAL_PICKUP`, `OPENED_PLACE`, the three hurdles and the trade
calibration's published record. A second league needs its own, so they become
rows.

Three things happen here, all additive:

1. `league_calibrations` -- one row per (league, key), `league_id` null being
   a pooled row over the leagues measured so far. `app.calibration` is the
   only thing that reads or writes it.
2. `jobs.priority` -- smaller runs first, before `run_after` is compared. The
   intake's hurdle sweep is an hour and a half of replay and must never stand
   in front of a morning's precomputes, so it is enqueued at 100 and
   everything else stays at 0. And `ck_jobs_kind` is widened to the eight
   intake kinds -- and, while it is being rewritten anyway, to the two injury
   kinds that `app.jobs` has listed since migration 0023 and that this CHECK
   would have refused if anything had ever enqueued one.
3. The data step: **Full Court Press keeps exactly the numbers it had.**
   `owner` rows for the three bars Patrick chose, with the dates and the
   reasons from the "Applied" records in docs/pickups_backtest.md, and
   `measured` rows for the two wire measurements and the trade record. Every
   value is the constant it replaces, to the digit, so that league's pages
   print what they printed yesterday; `tests/test_calibration.py` holds
   `SEED_ROWS` against the constants themselves.

The seed is an INSERT ... SELECT on `leagues`, so it writes nothing on a
database where that league has never been ingested (a fresh test database,
another operator's server), and `ON CONFLICT DO NOTHING` so re-running it
after a downgrade does not fight a row the account page has since written.

Downgrade drops the table, the column and the widened CHECK, and narrows the
CHECK back only after deleting any job of a kind it no longer admits.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-22
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = "ck_jobs_kind"
_KIND_WAS = "kind IN ('ingest', 'status_pass', 'precompute', 'digest')"
_KIND_NOW = (
    "kind IN ('ingest', 'status_pass', 'precompute', 'digest', "
    "'injury_backfill', 'injury_pass', "
    "'intake_ingest', 'intake_schedule', 'intake_replacement', 'intake_lane', "
    "'intake_hurdles', 'intake_trades', 'intake_pool', 'intake_done')"
)

#: The league every one of these numbers was measured on.
SEED_LEAGUE = 3853870

#: What a typical pickup returned, season by season, from the table in
#: `app/scoring/replacement.py`: median, mean, quartiles, adds. The value
#: taken is the lowest recent season's median, which is 2025's.
_PICKUP_BY_SEASON = {
    "2019": {"median": 0.113, "mean": 0.176, "iqr": [0.022, 0.280], "adds": 599},
    "2020": {"median": 0.128, "mean": 0.186, "iqr": [0.034, 0.298], "adds": 543},
    "2021": {"median": 0.096, "mean": 0.160, "iqr": [0.032, 0.239], "adds": 758},
    "2022": {"median": 0.086, "mean": 0.146, "iqr": [0.027, 0.219], "adds": 648},
    "2023": {"median": 0.091, "mean": 0.157, "iqr": [0.028, 0.242], "adds": 475},
    "2024": {"median": 0.068, "mean": 0.137, "iqr": [0.017, 0.207], "adds": 789},
    "2025": {"median": 0.062, "mean": 0.124, "iqr": [0.012, 0.173], "adds": 924},
    "2026": {"median": 0.072, "mean": 0.135, "iqr": [0.013, 0.195], "adds": 973},
}

#: The 2x2 of `app.trades.calibration.PUBLISHED`, as the payload keeps it.
_TRADE_CELLS = [
    {
        "headline": "the roster with-and-without",
        "horizon": "next 30 days",
        "picked": 25,
        "spearman": -0.02,
        "mean_error": 0.008,
        "yardstick": "the streamed lane",
    },
    {
        "headline": "the roster with-and-without",
        "horizon": "rest of season",
        "picked": 31,
        "spearman": 0.09,
        "mean_error": 0.013,
        "yardstick": "the streamed lane",
    },
    {
        "headline": "the per-man number",
        "horizon": "next 30 days",
        "picked": 19,
        "spearman": -0.19,
        "mean_error": -0.065,
        "yardstick": "the streamed lane",
    },
    {
        "headline": "the per-man number",
        "horizon": "rest of season",
        "picked": 29,
        "spearman": 0.02,
        "mean_error": -0.060,
        "yardstick": "the streamed lane",
    },
    {
        "headline": "the roster with-and-without",
        "horizon": "next 30 days",
        "picked": 25,
        "spearman": 0.00,
        "mean_error": 0.077,
        "yardstick": "the old flat level",
    },
    {
        "headline": "the roster with-and-without",
        "horizon": "rest of season",
        "picked": 30,
        "spearman": 0.09,
        "mean_error": 0.082,
        "yardstick": "the old flat level",
    },
]

#: The sentence the trade page prints, exactly as `CALIBRATION_NOTE` had it
#: and exactly as `app.trades.calibration.trade_note` rebuilds it from the
#: figures above. Written out here rather than imported: a migration that
#: imports application code writes whatever that code says next year, and
#: this is a record of what was measured in September 2026.
_TRADE_NOTE = (
    "This number is a forecast, and here is its record. Over the 55 trades in this "
    "league's history that can be replayed, it pointed at the side that did better in "
    "25 of them, judged on the thirty days after the deal; a coin lands between 20 and "
    "35 of 55 nineteen times in twenty, so on past evidence this number is not better "
    "than a coin at picking the winner of a trade. It is better at players than at "
    "deals: what it says a man is worth a week lines up reasonably well with what he "
    "goes on to do, and most of that agreement is lost when one side of a deal is "
    "subtracted from the other. One thing did get better. On deals that send two men "
    "for one, this number used to run about four tenths of a category a week above "
    "what those deals really did; most of that turned out to be the empty roster "
    "place being priced as an ordinary waiver pickup at both ends, and now that it is "
    "priced at what a streamed place really returns the gap is about a tenth. The "
    "category table beside it is a different matter: it is what your roster posts in a "
    "week with the deal and without it, laid out one category at a time, and it does "
    "not depend on this number being right. Read the number as one input to a "
    "conversation."
)

#: (key, value, n, measured_at, run_seconds, source, note, payload).
#:
#: The three `owner` rows are Patrick's own decisions, with the reason he
#: gave, and a re-measurement never replaces one. The three `measured` rows
#: are runs of the scripts named in their payloads.
SEED_ROWS: tuple[tuple[str, float | None, int, str, float | None, str, str, dict], ...] = (
    (
        "stream_hurdle",
        0.20,
        0,
        "2026-09-18",
        None,
        "owner",
        "your choice, 2026-09-18: the least churn for no loss, with a finite add "
        "budget and finite FAAB",
        {
            "reason": "the least churn for no loss, with a finite add budget and finite FAAB",
            "was": 0.10,
            "document": "docs/pickups_backtest.md",
        },
    ),
    (
        "season_hurdle_paid",
        0.20,
        0,
        "2026-09-21",
        None,
        "owner",
        "your choice, 2026-09-21: fewer and better moves, because seven adds a "
        "matchup period are shared with streaming",
        {
            "reason": "fewer and better moves, because seven adds a matchup period "
            "are shared with streaming",
            "was": 0.10,
            "document": "docs/pickups_backtest.md",
        },
    ),
    (
        "season_hurdle_free",
        0.10,
        0,
        "2026-09-21",
        None,
        "owner",
        "your choice, 2026-09-21: half the paid bar, which is the pair the sweep measured",
        {
            "reason": "half the paid bar, which is the pair the sweep measured",
            "was": 0.05,
            "document": "docs/pickups_backtest.md",
        },
    ),
    (
        "typical_pickup",
        0.06,
        924,
        "2026-09-16",
        None,
        "measured",
        "measured on this league, 924 adds",
        {
            "rule": "the lowest recent season's median",
            "from_season": 2025,
            "window_days": 14,
            "by_season": _PICKUP_BY_SEASON,
            "script": "app/scoring/replacement.py::replacement_value",
        },
    ),
    (
        "opened_place",
        0.38,
        1536,
        "2026-09-22",
        None,
        "measured",
        "measured on this league, 1,536 team-periods",
        {
            "median": 0.38,
            "iqr": [0.23, 0.53],
            "mean": 0.39,
            "held_13th_median": 0.00,
            "held_place_median": 0.43,
            "started_games_a_week": 4.67,
            "seasons": [2019, 2021, 2022, 2023, 2024, 2025, 2026],
            "script": "scripts/streaming_lane.py",
            "document": "docs/streaming_lane.md",
        },
    ),
    (
        "trade_record",
        None,
        55,
        "2026-09-22",
        61.0,
        "measured",
        _TRADE_NOTE,
        {
            "cells": _TRADE_CELLS,
            "deals": 55,
            "picked": 25,
            "sides": 110,
            "seasons": 6,
            "window_days": 30,
            "coin_range": [20, 35],
            "uneven_sides": 25,
            "uneven_error": {
                "R1, the old yardstick": 0.389,
                "R2, the old yardstick": 0.404,
                "R2, the streamed lane": 0.103,
            },
            "player_level_sample": 174,
            "player_level_spearman": 0.39,
            "script": "scripts/trade_calibration.py",
            "document": "docs/trades.md",
        },
    ),
)


def upgrade() -> None:
    op.create_table(
        "league_calibrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("n", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "measured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("run_seconds", sa.Float(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint(
            "source IN ('measured', 'pooled', 'default', 'owner')",
            name="ck_league_calibrations_source",
        ),
        sa.CheckConstraint("n >= 0", name="ck_league_calibrations_n"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("league_id", "key", name="uq_league_calibrations_league_key"),
    )
    # A pooled row belongs to no league, and the unique constraint above does
    # not bind on a null, so the one pooled row per key is a partial index.
    op.create_index(
        "uq_league_calibrations_pooled_key",
        "league_calibrations",
        ["key"],
        unique=True,
        postgresql_where=sa.text("league_id IS NULL"),
    )

    op.add_column("jobs", sa.Column("priority", sa.Integer(), server_default="0", nullable=False))
    op.drop_constraint(_KIND, "jobs", type_="check")
    op.create_check_constraint(_KIND, "jobs", _KIND_NOW)

    for key, value, n, measured_at, seconds, source, note, payload in SEED_ROWS:
        op.execute(
            sa.text(
                """
                INSERT INTO league_calibrations
                    (league_id, key, value, payload, n, measured_at, run_seconds,
                     source, note)
                SELECT l.id, :key, :value, CAST(:payload AS jsonb), :n,
                       CAST(:measured_at AS timestamptz), :seconds, :source, :note
                FROM leagues l
                WHERE l.espn_league_id = :league
                ON CONFLICT ON CONSTRAINT uq_league_calibrations_league_key DO NOTHING
                """
            ).bindparams(
                key=key,
                value=value,
                payload=json.dumps(payload),
                n=n,
                measured_at=f"{measured_at}T00:00:00+00:00",
                seconds=seconds,
                source=source,
                note=note,
                league=SEED_LEAGUE,
            )
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM jobs WHERE kind LIKE 'intake\\_%' OR kind LIKE 'injury\\_%'"))
    op.drop_constraint(_KIND, "jobs", type_="check")
    op.create_check_constraint(_KIND, "jobs", _KIND_WAS)
    op.drop_column("jobs", "priority")
    op.drop_index("uq_league_calibrations_pooled_key", table_name="league_calibrations")
    op.drop_table("league_calibrations")
