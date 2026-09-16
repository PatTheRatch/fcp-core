#!/usr/bin/env python3
"""Render an end-of-season report for one team, or every team in a season.

Usage:
    python scripts/season_report.py --season 2026 --team "Foxes ShutUpNDribble"
    python scripts/season_report.py --season 2026 --all --out reports/

Every figure on the page comes from a query in this file. Nothing is written by
hand per team: the prose is fixed labels, and the only sentences that vary are
assembled from the numbers themselves. Adding a team means passing a name.

THREE MEASURES, THREE DIFFERENT THINGS
--------------------------------------
They are easy to confuse and the page labels each one:

  banked      nine-category production a player generated WHILE IN THIS TEAM'S
              STARTING LINEUP. This is what the team actually scored.
  full season the same player's production for anyone, all year. The gap
              between the two is what a team gave away.
  games       days the player was started AND his NBA team played. A started
              slot on a rest day is not a game and is never counted as one.

Nine-category production is PTS + REB + AST + STL + BLK + 3PM - TO. It is a
rough single number for comparing players inside one category set, not a
scoring system: this league is head-to-head nine-cat, and the category tables
are the real record.

TRADES ARE RECONSTRUCTED, NOT READ
----------------------------------
ESPN's transaction feed is not complete for trades. In 2026 it carries player
movement for 4 of the league's 64 team-trades; `teams.trades` counts 64 of them.
So trades are reconstructed from `daily_lineup_slots` by
`app.scoring.trades.reconstruct_trades`: a trade is two teams moving players
both ways on the same day (within a day). A one-way move is a waiver claim
unless the transaction ledger confirms a trade, and moves the wire explains are
never trades at all. Measured against ESPN, that recovers 52 of the Foxes'
season's 64 sides and 157 of 204 league-wide -- a lower bound, never an
over-count. The page says so, and the count ESPN reports is shown next to the
count we recovered.

A trade is graded on what each player did AFTER the trade date, for anyone.
That is the only fair basis -- production a player gives another roster still
counts as production the trading team gave up.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

# The script is run by path (`python scripts/season_report.py`), which puts
# `scripts/` on sys.path rather than the repo root, so `app` is not importable
# until the root is added. Same reason the other entry points here import late.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import make_engine, make_session_factory
from app.scoring.trades import reconstruct_trades

#: psycopg's dict_row gives back column name to value, with the value type
#: fixed by the query rather than the schema, so Any is honest here.
Row = dict[str, Any]
Cur = psycopg.Cursor[Row]

#: What a numeric column can arrive as: psycopg returns numeric as Decimal,
#: double precision as float, and an outer join can leave any of them null.
Number = int | float | Decimal | None

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

#: SQLAlchemy needs the driver named in the scheme, the opposite of what psycopg
#: wants, so the one `.env` value is spelled two ways rather than duplicated.
SA_DSN = (
    DSN
    if DSN.startswith("postgresql+psycopg://")
    else DSN.replace("postgresql://", "postgresql+psycopg://", 1)
)

#: Column order for the week-by-week grid. Percentages first, then counting
#: stats, then turnovers, so a team's shape reads left to right.
CATS = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")

#: Short labels for the grid header, which is 22px wide per column.
CAT_SHORT = {"FG%": "FG", "FT%": "FT", "3PM": "3P"}

#: Nine-category production, as a SQL expression over player_game_stats `g`.
NINE_CAT = (
    "g.points + g.rebounds + g.assists + g.steals + g.blocks + g.three_pointers_made - g.turnovers"
)

#: Categories where a lower total is better, so the rank has to be inverted.
LOWER_IS_BETTER = ("TO",)


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------


def one(cur: Cur) -> Row:
    """The single row an aggregate query returns. Raises if the query changed."""
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("expected one row and got none")
    return row


def _season_id(cur: Cur, season: int) -> int:
    cur.execute("SELECT id FROM league_seasons WHERE season = %s", (season,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"season {season} is not in the database")
    return int(row["id"])


def _team(cur: Cur, season_id: int, name: str) -> Row:
    cur.execute(
        """
        SELECT t.*, ls.season, ls.team_count, ls.acquisition_budget,
               ls.regular_season_periods, ls.uses_faab
        FROM teams t
        JOIN league_seasons ls ON ls.id = t.league_season_id
        WHERE t.league_season_id = %s AND t.name = %s
        """,
        (season_id, name),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "SELECT name FROM teams WHERE league_season_id = %s ORDER BY name", (season_id,)
        )
        known = ", ".join(r["name"] for r in cur.fetchall())
        raise SystemExit(f"no team named {name!r}. Teams that season: {known}")
    return row


def _team_names(cur: Cur, season_id: int) -> list[str]:
    cur.execute(
        "SELECT name FROM teams WHERE league_season_id = %s ORDER BY final_standing NULLS LAST",
        (season_id,),
    )
    return [r["name"] for r in cur.fetchall()]


def _manager(cur: Cur, team_id: int) -> str | None:
    cur.execute(
        """
        SELECT o.first_name, o.last_name, o.display_name
        FROM owners o JOIN team_owners tw ON tw.owner_id = o.id
        WHERE tw.team_id = %s
        """,
        (team_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    full = " ".join(p for p in (row["first_name"], row["last_name"]) if p).strip()
    return full or row["display_name"]


def _weeks(cur: Cur, season_id: int, team_id: int) -> list[Row]:
    """Every matchup, with the per-category result string for the grid."""
    cur.execute(
        """
        WITH sides AS (
            SELECT mp.period, mp.is_playoff, m.id AS matchup_id,
                   m.home_team_id AS me, m.away_team_id AS opp,
                   m.home_categories_won AS w, m.home_categories_lost AS l,
                   m.categories_tied AS t
            FROM matchups m
            JOIN matchup_periods mp ON mp.id = m.matchup_period_id
            WHERE mp.league_season_id = %(s)s
            UNION ALL
            SELECT mp.period, mp.is_playoff, m.id,
                   m.away_team_id, m.home_team_id,
                   m.home_categories_lost, m.home_categories_won, m.categories_tied
            FROM matchups m
            JOIN matchup_periods mp ON mp.id = m.matchup_period_id
            WHERE mp.league_season_id = %(s)s AND m.away_team_id IS NOT NULL
        )
        SELECT s.period, s.is_playoff, s.matchup_id, s.w, s.l, s.t, o.name AS opponent
        FROM sides s
        LEFT JOIN teams o ON o.id = s.opp
        WHERE s.me = %(t)s
        ORDER BY s.period
        """,
        {"s": season_id, "t": team_id},
    )
    weeks = cur.fetchall()

    cur.execute(
        """
        SELECT m.id AS matchup_id, ts.abbreviation AS cat, ts.result
        FROM matchup_team_stats ts
        JOIN matchups m ON m.id = ts.matchup_id
        JOIN matchup_periods mp ON mp.id = m.matchup_period_id
        WHERE mp.league_season_id = %s AND ts.team_id = %s AND ts.result IS NOT NULL
        """,
        (season_id, team_id),
    )
    grid: dict[int, dict[str, str]] = {}
    for row in cur.fetchall():
        grid.setdefault(row["matchup_id"], {})[row["cat"]] = row["result"][0]

    for week in weeks:
        cells = grid.get(week["matchup_id"], {})
        week["cells"] = [cells.get(c, "-") for c in CATS]
        week["result"] = "W" if week["w"] > week["l"] else "L" if week["w"] < week["l"] else "T"
        parts = [week["w"], week["l"]] + ([week["t"]] if week["t"] else [])
        week["score"] = "–".join(str(p) for p in parts)
    return weeks


def _category_rates(cur: Cur, season_id: int, team_id: int) -> list[Row]:
    """How often this team won each category, across every matchup."""
    cur.execute(
        """
        SELECT ts.abbreviation AS cat,
               count(*) FILTER (WHERE ts.result = 'WIN') AS w,
               count(*) FILTER (WHERE ts.result = 'LOSS') AS l,
               count(*) FILTER (WHERE ts.result = 'TIE') AS t,
               round(100.0 * count(*) FILTER (WHERE ts.result = 'WIN') / count(*)) AS pct
        FROM matchup_team_stats ts
        JOIN matchups m ON m.id = ts.matchup_id
        JOIN matchup_periods mp ON mp.id = m.matchup_period_id
        WHERE mp.league_season_id = %s AND ts.team_id = %s
          AND ts.result IS NOT NULL AND ts.abbreviation = ANY(%s)
        GROUP BY 1
        ORDER BY pct DESC, cat
        """,
        (season_id, team_id, list(CATS)),
    )
    rows = cur.fetchall()
    for row in rows:
        parts = [row["w"], row["l"]] + ([row["t"]] if row["t"] else [])
        row["record"] = "–".join(str(p) for p in parts)
        row["pct"] = int(row["pct"])
    return rows


def _volume_ranks(cur: Cur, season_id: int, team_id: int) -> list[Row]:
    """Season totals and this team's rank, regular season only.

    Percentages are averaged across matchups and everything else summed, so
    the two kinds of category are never added together.
    """
    cur.execute(
        """
        WITH totals AS (
            SELECT ts.team_id, ts.abbreviation AS cat,
                   CASE WHEN ts.abbreviation IN ('FG%%', 'FT%%')
                        THEN avg(ts.value) ELSE sum(ts.value) END AS total
            FROM matchup_team_stats ts
            JOIN matchups m ON m.id = ts.matchup_id
            JOIN matchup_periods mp ON mp.id = m.matchup_period_id
            WHERE mp.league_season_id = %(s)s AND NOT mp.is_playoff
              AND ts.abbreviation = ANY(%(c)s)
            GROUP BY 1, 2
        ), ranked AS (
            SELECT *, rank() OVER (
                PARTITION BY cat
                ORDER BY CASE WHEN cat = ANY(%(inv)s) THEN -total ELSE total END DESC
            ) AS rk
            FROM totals
        )
        SELECT cat,
               max(total) FILTER (WHERE team_id = %(t)s) AS mine,
               avg(total) AS league_avg,
               max(rk) FILTER (WHERE team_id = %(t)s) AS rank,
               count(*) AS of
        FROM ranked GROUP BY cat ORDER BY rank
        """,
        {"s": season_id, "t": team_id, "c": list(CATS), "inv": list(LOWER_IS_BETTER)},
    )
    return cur.fetchall()


def _starters(
    cur: Cur, season_id: int, team_id: int, season: int, limit: int = 12
) -> tuple[list[Row], float, float]:
    cur.execute(
        f"""
        WITH banked AS (
            SELECT d.player_id, count(*) AS games, sum({NINE_CAT}) AS comp
            FROM daily_lineup_slots d
            JOIN matchup_periods mp ON mp.id = d.matchup_period_id
            JOIN player_game_stats g
              ON g.player_id = d.player_id AND g.scoring_period = d.scoring_period
             AND g.season = %(yr)s AND g.played
            WHERE mp.league_season_id = %(s)s AND d.team_id = %(t)s AND d.started
            GROUP BY 1
        )
        SELECT p.name, b.games, b.comp, dp.bid_amount AS cost
        FROM banked b
        JOIN players p ON p.id = b.player_id
        LEFT JOIN draft_picks dp
          ON dp.player_id = b.player_id AND dp.team_id = %(t)s
         AND dp.league_season_id = %(s)s
        ORDER BY b.comp DESC
        """,
        {"s": season_id, "t": team_id, "yr": season},
    )
    rows = cur.fetchall()
    drafted = sum(r["comp"] for r in rows if r["cost"] is not None)
    acquired = sum(r["comp"] for r in rows if r["cost"] is None)
    return rows[:limit], drafted, acquired


def _trades(cur: Cur, session: Session, season_id: int, team_id: int, season: int) -> list[Row]:
    """Trades for the page, reconstructed by `app.scoring.trades`.

    The reconstruction itself lives in the module, because it is a scoring
    concern rather than a presentation one. What stays here is the part the
    page needs and the module deliberately does not know: the nine-category
    production each player posted after the trade, which the report grades on.
    """
    trades = reconstruct_trades(session, season, team_id)
    if not trades:
        return []

    #: Production after a trade is per player *and* per day, and the module's
    #: `Party` deliberately does not carry it: the nine-cat composite is this
    #: report's business, not the scoring package's.
    after: dict[tuple[int, int], tuple[int, float]] = {}

    by_day: dict[int, Row] = {}
    for trade in trades:
        day = by_day.setdefault(
            trade.day,
            {
                "day": trade.day,
                "in": [],
                "out": [],
                "counterparties": list(trade.counterparty_names),
                "from_ledger": trade.from_ledger,
            },
        )
        for side, parties in (("in", trade.players_in), ("out", trade.players_out)):
            known = {entry["player_id"] for entry in day[side]}
            for party in parties:
                if party.player_id not in known:
                    day[side].append({"player_id": party.player_id, "name": party.name})
        if not trade.gradeable:
            day["gradeable"] = False

    for day in by_day.values():
        for side in ("in", "out"):
            for entry in day[side]:
                key = (entry["player_id"], day["day"])
                if key not in after:
                    cur.execute(
                        f"""
                        SELECT count(*) FILTER (WHERE g.played) AS games_after,
                               COALESCE(sum({NINE_CAT}) FILTER (WHERE g.played), 0) AS comp_after
                        FROM player_game_stats g
                        WHERE g.player_id = %(p)s AND g.season = %(yr)s
                          AND g.scoring_period > %(d)s
                        """,
                        {"p": entry["player_id"], "yr": season, "d": day["day"]},
                    )
                    row = one(cur)
                    after[key] = (row["games_after"], row["comp_after"])
                games_after, comp_after = after[key]
                entry["games_after"] = games_after
                entry["comp_after"] = comp_after
        for side in ("in", "out"):
            day[side].sort(key=lambda entry: entry["comp_after"], reverse=True)
        got = sum(entry["comp_after"] for entry in day["in"])
        gave = sum(entry["comp_after"] for entry in day["out"])
        day["net"] = got - gave
        #: A side with nothing on it means the other half of the deal was not
        #: recoverable from roster movement -- a three-way, or a player who
        #: never appeared in a daily lineup. Not gradeable.
        day.setdefault("gradeable", bool(day["in"] and day["out"]))
    return sorted(by_day.values(), key=lambda d: d["day"])


def _waivers(cur: Cur, season_id: int, team_id: int) -> Row:
    cur.execute(
        """
        SELECT COALESCE(status, 'PENDING') AS status, count(*) AS n,
               COALESCE(sum(bid_amount), 0) AS spent
        FROM transactions
        WHERE league_season_id = %s AND team_id = %s AND type = 'WAIVER'
        GROUP BY 1 ORDER BY n DESC
        """,
        (season_id, team_id),
    )
    by_status = cur.fetchall()
    executed = next((r for r in by_status if r["status"] == "EXECUTED"), None)
    failed = sum(r["n"] for r in by_status if r["status"].startswith("FAILED"))
    cur.execute(
        """
        SELECT max(bid_amount) AS top
        FROM transactions
        WHERE league_season_id = %s AND team_id = %s AND type = 'WAIVER' AND status = 'EXECUTED'
        """,
        (season_id, team_id),
    )
    return {
        "by_status": by_status,
        "won": executed["n"] if executed else 0,
        "spent": executed["spent"] if executed else 0,
        "failed": failed,
        "top_bid": one(cur)["top"],
    }


def _waiver_value(cur: Cur, season_id: int, season: int) -> list[Row]:
    """Net production per day over the fortnight after each one-for-one swap.

    Only add/drop pairs from the same transaction are measured: a bare add has
    nothing to compare against. The window is fixed at 14 days so a move in
    October and one in March are judged on the same horizon.
    """
    cur.execute(
        f"""
        WITH swaps AS (
            SELECT x.team_id, x.id, x.scoring_period AS day,
                   max(CASE WHEN i.item_type = 'ADD'  THEN i.player_id END) AS added,
                   max(CASE WHEN i.item_type = 'DROP' THEN i.player_id END) AS dropped
            FROM transactions x
            JOIN transaction_items i ON i.transaction_id = x.id
            WHERE x.league_season_id = %(s)s AND x.status = 'EXECUTED'
              AND x.team_id IS NOT NULL
            GROUP BY 1, 2, 3
            HAVING count(*) FILTER (WHERE i.item_type = 'ADD') = 1
               AND count(*) FILTER (WHERE i.item_type = 'DROP') = 1
        ), scored AS (
            SELECT s.team_id,
                   (COALESCE((SELECT sum({NINE_CAT}) FROM player_game_stats g
                               WHERE g.player_id = s.added AND g.season = %(yr)s AND g.played
                                 AND g.scoring_period BETWEEN s.day + 1 AND s.day + 14), 0)
                  - COALESCE((SELECT sum({NINE_CAT}) FROM player_game_stats g
                               WHERE g.player_id = s.dropped AND g.season = %(yr)s AND g.played
                                 AND g.scoring_period BETWEEN s.day + 1 AND s.day + 14), 0)
                   ) / 14.0 AS value
            FROM swaps s
        )
        SELECT t.name, count(*) AS moves,
               round(avg(sc.value)::numeric, 2) AS per_day,
               round(100.0 * count(*) FILTER (WHERE sc.value > 0) / count(*)) AS hit_rate
        FROM scored sc JOIN teams t ON t.id = sc.team_id
        GROUP BY t.name ORDER BY per_day DESC
        """,
        {"s": season_id, "yr": season},
    )
    return cur.fetchall()


def _swaps(cur: Cur, season_id: int, team_id: int, season: int, best: bool) -> list[Row]:
    cur.execute(
        f"""
        WITH swaps AS (
            SELECT x.id, x.scoring_period AS day,
                   max(CASE WHEN i.item_type = 'ADD'  THEN i.player_id END) AS added,
                   max(CASE WHEN i.item_type = 'DROP' THEN i.player_id END) AS dropped
            FROM transactions x
            JOIN transaction_items i ON i.transaction_id = x.id
            WHERE x.league_season_id = %(s)s AND x.team_id = %(t)s AND x.status = 'EXECUTED'
            GROUP BY 1, 2
            HAVING count(*) FILTER (WHERE i.item_type = 'ADD') = 1
               AND count(*) FILTER (WHERE i.item_type = 'DROP') = 1
        )
        SELECT pa.name AS added, pd.name AS dropped, s.day,
               round(((COALESCE((SELECT sum({NINE_CAT}) FROM player_game_stats g
                        WHERE g.player_id = s.added AND g.season = %(yr)s AND g.played
                          AND g.scoring_period BETWEEN s.day + 1 AND s.day + 14), 0)
                     - COALESCE((SELECT sum({NINE_CAT}) FROM player_game_stats g
                        WHERE g.player_id = s.dropped AND g.season = %(yr)s AND g.played
                          AND g.scoring_period BETWEEN s.day + 1 AND s.day + 14), 0)
                     ) / 14.0)::numeric, 1) AS net
        FROM swaps s
        JOIN players pa ON pa.id = s.added
        JOIN players pd ON pd.id = s.dropped
        ORDER BY net {"DESC" if best else "ASC"}
        LIMIT 3
        """,
        {"s": season_id, "t": team_id, "yr": season},
    )
    return cur.fetchall()


def _bench(cur: Cur, season_id: int, team_id: int, season: int) -> Row:
    """Production left on the bench, and where that sits in the league.

    Only BE counts. An IR slot is not a decision the manager got wrong, and a
    bench day for a player whose NBA team did not play costs nothing.

    Only periods in which the team had a matchup count either: a team knocked
    out in week 20 still sets lineups in weeks 21 and 22, and nothing it
    benches there costs it anything. Counting those would charge eliminated
    teams for weeks they could not lose. ESPN gives such a team a one-sided
    matchup row with category stats attached, so the test is for an opponent,
    not for a matchup.
    """
    cur.execute(
        f"""
        SELECT d.team_id, count(*) AS days, sum({NINE_CAT}) AS comp
        FROM daily_lineup_slots d
        JOIN matchup_periods mp ON mp.id = d.matchup_period_id
        JOIN player_game_stats g
          ON g.player_id = d.player_id AND g.scoring_period = d.scoring_period
         AND g.season = %s AND g.played
        WHERE mp.league_season_id = %s AND NOT d.started AND d.slot = 'BE'
          AND EXISTS (
                SELECT 1 FROM matchups m
                WHERE m.matchup_period_id = mp.id
                  AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
                  AND d.team_id IN (m.home_team_id, m.away_team_id)
          )
        GROUP BY 1 ORDER BY comp DESC
        """,
        (season, season_id),
    )
    league = cur.fetchall()
    rank = next((i + 1 for i, r in enumerate(league) if r["team_id"] == team_id), None)
    mine = next((r for r in league if r["team_id"] == team_id), {"days": 0, "comp": 0})

    cur.execute(
        f"""
        SELECT p.name, d.scoring_period AS day, mp.period AS week,
               g.points, g.rebounds, g.assists, g.three_pointers_made,
               {NINE_CAT} AS comp
        FROM daily_lineup_slots d
        JOIN matchup_periods mp ON mp.id = d.matchup_period_id
        JOIN player_game_stats g
          ON g.player_id = d.player_id AND g.scoring_period = d.scoring_period
         AND g.season = %s AND g.played
        JOIN players p ON p.id = d.player_id
        WHERE mp.league_season_id = %s AND d.team_id = %s AND NOT d.started AND d.slot = 'BE'
          AND EXISTS (
                SELECT 1 FROM matchups m
                WHERE m.matchup_period_id = mp.id
                  AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
                  AND d.team_id IN (m.home_team_id, m.away_team_id)
          )
        ORDER BY comp DESC LIMIT 6
        """,
        (season, season_id, team_id),
    )
    return {
        "days": mine["days"],
        "comp": mine["comp"],
        "rank": rank,
        "of": len(league),
        "worst": cur.fetchall(),
    }


def _last_week(cur: Cur, season_id: int, team_id: int, season: int, weeks: list[Row]) -> Row | None:
    """The final matchup: who was in the lineup each day, and the box score."""
    played = [w for w in weeks if w["opponent"]]
    if not played:
        return None
    final = played[-1]

    cur.execute(
        """
        SELECT ts.team_id, ts.abbreviation AS cat, ts.value, ts.result
        FROM matchup_team_stats ts
        WHERE ts.matchup_id = %s AND ts.abbreviation = ANY(%s)
        """,
        (final["matchup_id"], list(CATS)),
    )
    box: dict[str, Row] = {}
    for row in cur.fetchall():
        side = "mine" if row["team_id"] == team_id else "theirs"
        box.setdefault(row["cat"], {})[side] = row["value"]
        if side == "mine":
            box[row["cat"]]["result"] = row["result"]

    cur.execute(
        """
        SELECT d.team_id, d.scoring_period AS day,
               count(*) FILTER (WHERE d.started) AS started,
               count(*) FILTER (WHERE d.started AND g.played) AS played
        FROM daily_lineup_slots d
        JOIN matchup_periods mp ON mp.id = d.matchup_period_id
        LEFT JOIN player_game_stats g
          ON g.player_id = d.player_id AND g.scoring_period = d.scoring_period
         AND g.season = %s AND g.played
        WHERE mp.league_season_id = %s AND mp.period = %s
          AND d.team_id IN (
              SELECT team_id FROM matchup_team_stats WHERE matchup_id = %s
          )
        GROUP BY 1, 2 ORDER BY 2
        """,
        (season, season_id, final["period"], final["matchup_id"]),
    )
    days: dict[int, Row] = {}
    for row in cur.fetchall():
        side = "mine" if row["team_id"] == team_id else "theirs"
        days.setdefault(row["day"], {"day": row["day"]})[side] = row

    cur.execute(
        """
        SELECT COALESCE(sum(v.value::int), 0) AS n
        FROM league_seasons ls, jsonb_each_text(ls.lineup_slots) AS v(slot, value)
        WHERE ls.id = %s
        """,
        (season_id,),
    )
    slots = one(cur)["n"]

    ordered = [days[k] for k in sorted(days)]
    return {
        "week": final,
        "box": [dict(cat=c, **box[c]) for c in CATS if c in box],
        "days": ordered,
        "slots": slots,
        "mine_started": sum(d.get("mine", {}).get("started", 0) for d in ordered),
        "mine_played": sum(d.get("mine", {}).get("played", 0) for d in ordered),
        "theirs_started": sum(d.get("theirs", {}).get("started", 0) for d in ordered),
        "theirs_played": sum(d.get("theirs", {}).get("played", 0) for d in ordered),
    }


def gather(
    conn: psycopg.Connection[Any],
    session: Session,
    season: int,
    team_name: str,
) -> Row:
    with conn.cursor(row_factory=dict_row) as cur:
        season_id = _season_id(cur, season)
        team = _team(cur, season_id, team_name)
        tid = team["id"]

        weeks = _weeks(cur, season_id, tid)
        starters, drafted_comp, acquired_comp = _starters(cur, season_id, tid, season)

        regular = [w for w in weeks if not w["is_playoff"] and w["opponent"]]
        wins = sum(1 for w in regular if w["result"] == "W")
        losses = sum(1 for w in regular if w["result"] == "L")
        ties = sum(1 for w in regular if w["result"] == "T")
        playoffs = [w for w in weeks if w["is_playoff"]]

        return {
            "season": season,
            "team": team,
            "manager": _manager(cur, tid),
            "weeks": weeks,
            "record": (wins, losses, ties),
            "regular_matchups": len(regular),
            "playoffs": playoffs,
            "rates": _category_rates(cur, season_id, tid),
            "volume": _volume_ranks(cur, season_id, tid),
            "draft": _draft_for(cur, season_id, tid, season),
            "starters": starters,
            "drafted_comp": drafted_comp,
            "acquired_comp": acquired_comp,
            "trades": _trades(cur, session, season_id, tid, season),
            "waivers": _waivers(cur, season_id, tid),
            "waiver_value": _waiver_value(cur, season_id, season),
            "best_swaps": _swaps(cur, season_id, tid, season, best=True),
            "worst_swaps": _swaps(cur, season_id, tid, season, best=False),
            "bench": _bench(cur, season_id, tid, season),
            "last_week": _last_week(cur, season_id, tid, season, weeks),
        }


def _draft_for(cur: Cur, season_id: int, team_id: int, season: int) -> list[Row]:
    cur.execute(
        f"""
        WITH banked AS (
            SELECT d.player_id, count(*) AS games, sum({NINE_CAT}) AS comp
            FROM daily_lineup_slots d
            JOIN matchup_periods mp ON mp.id = d.matchup_period_id
            JOIN player_game_stats g
              ON g.player_id = d.player_id AND g.scoring_period = d.scoring_period
             AND g.season = %(yr)s AND g.played
            WHERE mp.league_season_id = %(s)s AND d.team_id = %(t)s AND d.started
            GROUP BY 1
        ), tenure AS (
            SELECT d.player_id, max(d.scoring_period) AS last_day
            FROM daily_lineup_slots d
            JOIN matchup_periods mp ON mp.id = d.matchup_period_id
            WHERE mp.league_season_id = %(s)s AND d.team_id = %(t)s
            GROUP BY 1
        ), everyone AS (
            SELECT g.player_id, count(*) AS games, sum({NINE_CAT}) AS comp
            FROM player_game_stats g WHERE g.season = %(yr)s AND g.played GROUP BY 1
        ), season_days AS (
            SELECT max(d.scoring_period) AS last FROM daily_lineup_slots d
            JOIN matchup_periods mp ON mp.id = d.matchup_period_id
            WHERE mp.league_season_id = %(s)s
        )
        SELECT p.name, dp.bid_amount AS cost, dp.keeper,
               COALESCE(b.games, 0) AS games, COALESCE(b.comp, 0) AS banked,
               e.games AS full_games, e.comp AS full_comp,
               tenure.last_day,
               (SELECT last FROM season_days) AS season_last_day
        FROM draft_picks dp
        JOIN players p ON p.id = dp.player_id
        LEFT JOIN banked b ON b.player_id = dp.player_id
        LEFT JOIN tenure ON tenure.player_id = dp.player_id
        LEFT JOIN everyone e ON e.player_id = dp.player_id
        WHERE dp.league_season_id = %(s)s AND dp.team_id = %(t)s
        ORDER BY dp.bid_amount DESC NULLS LAST, dp.round_num, dp.round_pick
        """,
        {"s": season_id, "t": team_id, "yr": season},
    )
    rows = cur.fetchall()
    for row in rows:
        row["per_dollar"] = round(row["banked"] / row["cost"]) if row["cost"] else None
        row["kept"] = row["last_day"] is not None and row["last_day"] >= row["season_last_day"]
    return rows


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def num(value: Number, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.{digits}f}"


def pct3(value: Number) -> str:
    """A shooting percentage the way a box score writes it: .462, no zero."""
    if value is None:
        return "—"
    return f"{float(value):.3f}".lstrip("0")


def ordinal(n: int | None) -> str:
    if n is None:
        return "—"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def record_line(record: tuple[int, int, int]) -> str:
    """A win-loss record, carrying the ties only when there are some."""
    wins, losses, ties = record
    return f"{wins}–{losses}" + (f"–{ties}" if ties else "")


def category_line(team: Row) -> str:
    """The category record: what this league is actually decided on."""
    won, lost, tied = team["categories_won"], team["categories_lost"], team["categories_tied"]
    return f"{won}–{lost}" + (f"–{tied}" if tied else "")


def per_week_line(d: Row) -> str:
    """Categories won per matchup, out of nine. Empty when it cannot be computed.

    `teams.categories_won` counts the regular season only, so it is divided by
    the regular-season matchup count and never by the full schedule.
    """
    matchups = d["regular_matchups"]
    if not matchups:
        return ""
    per = d["team"]["categories_won"] / matchups
    return f", {per:.1f} of {len(CATS)} a week"


def _headline(d: Row) -> str:
    """One line of fact, assembled from the record. No adjectives."""
    team = d["team"]
    bits = [
        f"{category_line(team)} in the categories{per_week_line(d)}",
        f"{record_line(d['record'])} in matchups",
        f"Seeded {ordinal(team['standing'])}",
        f"Finished {ordinal(team['final_standing'])}",
    ]
    lost = [m for m in d["playoffs"] if m["result"] == "L" and m["opponent"]]
    won = [m for m in d["playoffs"] if m["result"] == "W" and m["opponent"]]
    if lost:
        # A team that won a round and then lost was beaten in its last game;
        # a team that lost every round went out in its first.
        final = lost[-1] if won else lost[0]
        verb = "Lost to" if won else "Knocked out by"
        bits.append(f"{verb} {final['opponent']} {final['score']} in week {final['period']}")
    elif won:
        bits.append(f"Beat {won[-1]['opponent']} {won[-1]['score']} in week {won[-1]['period']}")
    return ". ".join(bits) + "."


def _identity(d: Row) -> tuple[list[str], list[str]]:
    top = [r["cat"] for r in d["volume"] if r["rank"] and r["rank"] <= 3]
    bottom = [
        r["cat"] for r in d["volume"] if r["rank"] and r["rank"] >= d["team"]["team_count"] - 2
    ]
    return top, bottom


def _findings(d: Row) -> list[tuple[str, str]]:
    """Observations that follow from the numbers, chosen by rule.

    Each one states a comparison the tables already contain. Nothing here is
    an opinion about the manager; it is the largest gap of its kind.
    """
    out: list[tuple[str, str]] = []
    picks = [p for p in d["draft"] if p["cost"]]

    gone = [
        p for p in picks if not p["kept"] and p["full_comp"] and p["full_comp"] - p["banked"] > 0
    ]
    if gone:
        worst = max(gone, key=lambda p: p["full_comp"] - p["banked"])
        left = (
            f"left the roster on day {worst['last_day']}"
            if worst["last_day"]
            else "never appeared in a daily lineup"
        )
        out.append(
            (
                "Largest gap between what a pick cost and what it returned",
                f"{worst['name']}, ${worst['cost']}, {worst['games']} games started, {left}. "
                f"Produced {num(worst['full_comp'])} for the season, of which "
                f"{num(worst['banked'])} landed in this lineup.",
            )
        )

    if picks:
        best = max(picks, key=lambda p: p["per_dollar"] or 0)
        if best["per_dollar"]:
            out.append(
                (
                    "Best return per auction dollar",
                    f"{best['name']}, ${best['cost']}, {num(best['banked'])} banked across "
                    f"{best['games']} games. {num(best['per_dollar'])} per dollar.",
                )
            )

    rates = d["rates"]
    if rates:
        best_cat, worst_cat = rates[0], rates[-1]
        out.append(
            (
                "Widest category spread",
                f"Won {best_cat['cat']} in {best_cat['pct']}% of matchups "
                f"({best_cat['record']}) and {worst_cat['cat']} in "
                f"{worst_cat['pct']}% ({worst_cat['record']}). "
                f"A {best_cat['pct'] - worst_cat['pct']}-point spread across the nine.",
            )
        )

    lw = d["last_week"]
    if lw and lw["days"]:
        full = sum(1 for day in lw["days"] if day.get("mine", {}).get("started", 0) >= lw["slots"])
        out.append(
            (
                f"Final matchup, week {lw['week']['period']}",
                f"Filled all {lw['slots']} starting slots on {full} of {len(lw['days'])} days. "
                f"{lw['mine_started']} starts produced {lw['mine_played']} games; the opponent's "
                f"{lw['theirs_started']} produced {lw['theirs_played']}.",
            )
        )
    return out


#: Where a written note may be placed. A note file keyed by anything else is
#: rejected at load rather than silently dropped.
NOTE_SLOTS = (
    "lede",
    "profile",
    "ledger",
    "draft",
    "production",
    "trades",
    "wire",
    "bench",
    "last",
    "closing",
)


def load_notes(path: Path) -> dict[str, str]:
    """Read written commentary: a JSON object of slot name to one or more paragraphs.

    Notes are optional and per team. Without them a report is the tables and
    the rule-derived findings, which is what most teams want; with them the
    same page carries prose in the named slots. Paragraphs are split on a
    blank line. The text is trusted as HTML so a note can carry emphasis.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{path}: expected a JSON object of slot name to text")
    unknown = sorted(set(raw) - set(NOTE_SLOTS))
    if unknown:
        raise SystemExit(
            f"{path}: unknown slot(s) {', '.join(unknown)}. Valid: {', '.join(NOTE_SLOTS)}"
        )
    return {k: str(v) for k, v in raw.items()}


def render(d: Row, notes: dict[str, str] | None = None) -> str:
    team = d["team"]
    record = record_line(d["record"])
    cats = f"{team['categories_won']}–{team['categories_lost']}"
    if team["categories_tied"]:
        cats += f"–{team['categories_tied']}"
    top, bottom = _identity(d)
    wv = d["waiver_value"]
    wv_rank = next((i + 1 for i, r in enumerate(wv) if r["name"] == team["name"]), None)
    wv_mine = next((r for r in wv if r["name"] == team["name"]), None)

    written = notes or {}
    parts: list[str] = []
    add = parts.append

    def note(slot: str, *, lede: bool = False) -> None:
        """Drop written commentary into a slot, if any was supplied."""
        text = written.get(slot)
        if not text:
            return
        cls = "read lede" if lede else "read"
        body = "".join(f"<p>{para.strip()}</p>" for para in text.split("\n\n") if para.strip())
        add(f'<div class="{cls}">{body}</div>')

    add(f"<title>{e(team['name'])}, {d['season']}</title>")
    add(HEAD)

    # -- masthead ---------------------------------------------------------
    title_lines = (
        e(team["name"]).replace(" ", "<br>", 1) if len(team["name"]) > 14 else e(team["name"])
    )
    add('<div class="page"><header class="mast">')
    add(
        f'<div class="eyebrow">{d["season"] - 1}–{str(d["season"])[2:]} Season Report'
        f"&nbsp;·&nbsp;{e(team['team_count'])} teams</div>"
    )
    add(f"<h1>{title_lines}</h1>")
    manager = f"Manager <b>{e(d['manager'])}</b>. " if d["manager"] else ""
    add(f'<p class="sub">{manager}{e(_headline(d))}</p>')
    add("</header>")

    # -- season line ------------------------------------------------------
    per_matchup = (
        f"{team['categories_won'] / d['regular_matchups']:.1f}<small> of {len(CATS)}</small>"
        if d["regular_matchups"]
        else "—"
    )
    line = [
        ("Categories won", cats, "hi wide"),
        ("Per matchup", per_matchup, "hi"),
        ("Matchups", record, ""),
        ("Seed", f"{ordinal(team['standing'])}", ""),
        ("Finish", f"{ordinal(team['final_standing'])}", ""),
        (
            "Adds / drops",
            f"{team['acquisitions'] or 0}<small> / {team['drops'] or 0}</small>",
            "",
        ),
        ("Trades", str(team["trades"] or 0), ""),
        (
            "FAAB spent",
            f"${team['acquisition_budget_spent'] or 0}"
            f"<small> / {team['acquisition_budget']}</small>",
            "",
        ),
    ]
    add('<dl class="line">')
    for label, value, style in line:
        cls = f' class="{style}"' if style else ""
        add(f"<div{cls}><dt>{e(label)}</dt><dd>{value}</dd></div>")
    add("</dl>")
    add(
        f'<p class="note strip-note">Categories and matchups are the '
        f"{d['regular_matchups']} regular-season weeks. The category profile and "
        "week-by-week ledger below cover every matchup, playoffs included.</p>"
    )
    note("lede", lede=True)

    # -- identity ---------------------------------------------------------
    add('<section><div class="shead"><h2>Category profile</h2>')
    add('<span class="tag">Nine categories, every matchup</span></div>')
    note("profile")
    add('<div class="cats">')
    for r in d["rates"]:
        under = " under" if r["pct"] < 50 else ""
        add(
            f'<div class="cat{under}"><div class="nm">{e(r["cat"])}</div>'
            f'<div class="track"><div class="fill" style="width:{r["pct"]}%"></div>'
            f'<div class="mid"></div></div>'
            f'<div class="val">{r["pct"]}% · {e(r["record"])}</div></div>'
        )
    add("</div>")
    add(
        '<div class="axis"><span></span><span class="ticks">'
        "<span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>"
        "</span><span></span></div>"
    )

    if top or bottom:
        caption = []
        if top:
            caption.append(f"Top three in the league for {_and(top)}.")
        if bottom:
            caption.append(f"Bottom three for {_and(bottom)}.")
        add(f'<p class="note caption">{e(" ".join(caption))}</p>')

    add(
        '<div class="wrap"><table><thead><tr>'
        "<th>Category</th><th>Season total</th><th>League average</th><th>Rank</th>"
        "</tr></thead><tbody>"
    )
    for r in d["volume"]:
        is_pct = r["cat"] in ("FG%", "FT%")
        fmt = pct3 if is_pct else num
        good = r["rank"] and r["rank"] <= 3
        bad = r["rank"] and r["rank"] >= team["team_count"] - 2
        cls = "pos" if good else "neg" if bad else ""
        add(
            f"<tr><td>{e(r['cat'])}</td><td>{fmt(r['mine'])}</td>"
            f'<td class="dim">{fmt(r["league_avg"])}</td>'
            f'<td class="{cls}">{ordinal(r["rank"])} of {r["of"]}</td></tr>'
        )
    add("</tbody></table></div>")
    add(
        '<p class="note">Regular season only. Percentages are averaged across matchups; '
        "everything else is summed.</p></section>"
    )

    # -- ledger -----------------------------------------------------------
    add('<section><div class="shead"><h2>Week by week</h2>')
    cat_cells = sum(1 for w in d["weeks"] for c in w["cells"] if c != "-")
    cat_won = sum(1 for w in d["weeks"] for c in w["cells"] if c == "W")
    add(f'<span class="tag">{cat_won} of {cat_cells} categories won</span></div>')
    note("ledger")
    add('<div class="ledger"><div class="lg">')
    add('<div class="hd"></div><div class="hd l">Opponent</div>')
    for c in CATS:
        add(f'<div class="hd">{e(CAT_SHORT.get(c, c))}</div>')
    add('<div class="hd">Cats</div>')
    seen_playoff = False
    for week in d["weeks"]:
        if week["is_playoff"] and not seen_playoff:
            seen_playoff = True
            add('<div class="sep"></div>')
        if not week["opponent"]:
            # A gap with a matchup still to come is a bye; a gap with nothing
            # after it is elimination. ESPN reports both as an absent opponent.
            later = any(x["period"] > week["period"] and x["opponent"] for x in d["weeks"])
            add(
                f'<div class="wk">{week["period"]}</div>'
                f'<div class="bye">{"Bye" if later else "Eliminated"}</div>'
            )
            continue
        add(f'<div class="wk">{week["period"]}</div><div class="opp">{e(week["opponent"])}</div>')
        for cell in week["cells"]:
            add(f'<div class="cell {e(cell)}"></div>')
        add(f'<div class="res {week["result"].lower()}">{e(week["score"])}</div>')
    add("</div></div>")
    add(
        '<div class="key">'
        '<span><i class="swatch cell W"></i> won</span>'
        '<span><i class="swatch cell L"></i> lost</span>'
        '<span><i class="swatch cell T"></i> tied</span>'
        "</div></section>"
    )

    # -- draft ------------------------------------------------------------
    if d["draft"]:
        spend = sum(p["cost"] or 0 for p in d["draft"])
        add('<section><div class="shead"><h2>The draft</h2>')
        add(f'<span class="tag">${spend} · {len(d["draft"])} players</span></div>')
        note("draft")
        add(
            '<div class="wrap"><table><thead><tr>'
            "<th>Player</th><th>Cost</th><th>Games</th><th>Banked</th><th>Per $</th>"
            "<th>Full season</th><th>Left roster</th></tr></thead><tbody>"
        )
        for p in d["draft"]:
            pd = p["per_dollar"]
            cls = "pos" if pd and pd >= 100 else "neg" if pd is not None and pd < 40 else ""
            if p["kept"]:
                left = '<span class="dim">kept</span>'
            elif p["last_day"]:
                left = f"day {p['last_day']}"
            else:
                left = '<span class="dim">never rostered</span>'
            add(
                f'<tr><td>{e(p["name"])}</td><td class="cost">${p["cost"] or 0}</td>'
                f"<td>{p['games']}</td><td>{num(p['banked'])}</td>"
                f'<td class="{cls}">{num(pd) if pd is not None else "—"}</td>'
                f'<td class="dim">{num(p["full_comp"])} in {p["full_games"] or 0} g</td>'
                f"<td>{left}</td></tr>"
            )
        add("</tbody></table></div>")
        add(
            '<p class="note"><b>Banked</b> is nine-category production while in this starting '
            "lineup. <b>Full season</b> is what the player produced for anyone, all year. "
            "<b>Games</b> counts started days his NBA team actually played.</p></section>"
        )

    # -- who actually played ---------------------------------------------
    if d["starters"]:
        total = d["drafted_comp"] + d["acquired_comp"]
        share = 100 * d["acquired_comp"] / total if total else 0
        add('<section><div class="shead"><h2>Where the production came from</h2>')
        add(f'<span class="tag">{share:.0f}% acquired after the draft</span></div>')
        note("production")
        add(
            '<div class="wrap"><table><thead><tr>'
            "<th>Player</th><th>Games</th><th>Banked</th><th>Origin</th>"
            "</tr></thead><tbody>"
        )
        for s in d["starters"]:
            origin = f"draft ${s['cost']}" if s["cost"] is not None else "acquired"
            add(
                f"<tr><td>{e(s['name'])}</td><td>{s['games']}</td>"
                f'<td>{num(s["comp"])}</td><td class="dim">{e(origin)}</td></tr>'
            )
        add("</tbody></table></div></section>")

    # -- trades -----------------------------------------------------------
    if d["trades"]:
        reported = team["trades"] or 0
        add('<section><div class="shead"><h2>Trades</h2>')
        add(
            f'<span class="tag">{len(d["trades"])} reconstructed · '
            f"ESPN counts {reported}</span></div>"
        )
        note("trades")
        add('<div class="trades">')
        for tr in d["trades"]:
            got = ", ".join(e(r["name"]) for r in tr["in"]) or "—"
            gave = ", ".join(e(r["name"]) for r in tr["out"]) or "—"
            if tr["gradeable"]:
                grade = "good" if tr["net"] > 150 else "bad" if tr["net"] < -150 else "flat"
                verdict = f"{'+' if tr['net'] > 0 else ''}{num(tr['net'])}"
            else:
                grade, verdict = "flat", "part missing"
            detail = "; ".join(
                f"{e(r['name'])} {num(r['comp_after'])} in {r['games_after']} g"
                for r in tr["in"] + tr["out"]
            )
            add(
                f'<div class="trade"><div class="day">Day {tr["day"]}</div>'
                f'<div class="flow"><span class="out">{gave}</span>'
                f'<span class="arrow">→</span><span class="in">{got}</span>'
                f'<span class="why">With {e(", ".join(tr["counterparties"]))}. '
                f"After the trade: {detail}.</span></div>"
                f'<div class="grade {grade}">{verdict}</div></div>'
            )
        add("</div>")
        add(
            '<p class="note">Reconstructed from roster movement, because ESPN’s transaction '
            "feed does not carry player detail for most trades. The grade is production after the "
            "trade date, for anyone: what arrived less what left. A deal marked "
            "<b>part missing</b> had one side that could not be recovered.</p></section>"
        )

    # -- wire -------------------------------------------------------------
    wv_line = ""
    if wv_mine and wv_rank:
        wv_line = (
            f"{num(wv_mine['per_day'], 2)} net production per day over the fortnight after each "
            f"swap, across {wv_mine['moves']} one-for-one moves — {ordinal(wv_rank)} of "
            f"{len(wv)}. Hit rate {int(wv_mine['hit_rate'])}%."
        )
    add('<section><div class="shead"><h2>The wire</h2>')
    add(
        f'<span class="tag">{d["waivers"]["won"]} claims won · '
        f"{d['waivers']['failed']} failed</span></div>"
    )
    note("wire")
    add(
        f"<p>${d['waivers']['spent']} of FAAB across {d['waivers']['won']} winning claims. "
        f"Top bid ${d['waivers']['top_bid'] or 0}. {e(wv_line)}</p>"
    )
    if d["best_swaps"] or d["worst_swaps"]:
        add(
            '<div class="wrap"><table><thead><tr>'
            "<th>Swap</th><th>Day</th><th>Net per day, next 14</th></tr></thead><tbody>"
        )
        for s in d["best_swaps"]:
            add(
                f'<tr><td>{e(s["added"])} <span class="dim">for</span> {e(s["dropped"])}</td>'
                f'<td>{s["day"]}</td><td class="pos">+{num(s["net"], 1)}</td></tr>'
            )
        for s in d["worst_swaps"][:2]:
            add(
                f'<tr><td>{e(s["added"])} <span class="dim">for</span> {e(s["dropped"])}</td>'
                f'<td>{s["day"]}</td><td class="neg">{num(s["net"], 1)}</td></tr>'
            )
        add("</tbody></table></div>")
    add("</section>")

    # -- bench ------------------------------------------------------------
    b = d["bench"]
    add('<section><div class="shead"><h2>The bench</h2>')
    add(f'<span class="tag">{b["days"]} wasted player-days</span></div>')
    note("bench")
    add(
        f"<p>{num(b['comp'])} of production sat on the bench across {b['days']} player-days — "
        f"{ordinal(b['rank'])} most of {b['of']} teams.</p>"
    )
    if b["worst"]:
        add(
            '<div class="wrap"><table><thead><tr>'
            "<th>Benched</th><th>Day</th><th>Week</th><th>PTS</th><th>REB</th><th>AST</th>"
            "<th>3PM</th><th>Nine-cat</th></tr></thead><tbody>"
        )
        for r in b["worst"]:
            add(
                f"<tr><td>{e(r['name'])}</td><td>{r['day']}</td><td>{r['week']}</td>"
                f"<td>{num(r['points'])}</td><td>{num(r['rebounds'])}</td>"
                f"<td>{num(r['assists'])}</td><td>{num(r['three_pointers_made'])}</td>"
                f'<td class="neg">{num(r["comp"])}</td></tr>'
            )
        add("</tbody></table></div>")
    add(
        '<p class="note">Bench slots only. An IR day is not a decision, and a bench day for a '
        "player whose team did not play costs nothing.</p></section>"
    )

    # -- last week --------------------------------------------------------
    lw = d["last_week"]
    if lw:
        week = lw["week"]
        add('<section><div class="shead"><h2>Last matchup</h2>')
        add(
            f'<span class="tag">Week {week["period"]} · {e(week["opponent"])} · '
            f"{e(week['score'])}</span></div>"
        )
        note("last")
        add(
            f"<p>{lw['mine_started']} starting slots set against {lw['theirs_started']}. "
            f"{lw['mine_played']} of yours produced a game; "
            f"{lw['theirs_played']} of theirs did.</p>"
        )
        if lw["days"]:
            add('<div class="fin">')
            for day in lw["days"]:
                mine = day.get("mine", {}).get("started", 0)
                theirs = day.get("theirs", {}).get("started", 0)
                cap = max(lw["slots"], mine, theirs) or 1
                add(
                    f'<div class="fday"><div class="bars">'
                    f'<div class="bar me" style="height:{mine / cap * 110:.0f}px"></div>'
                    f'<div class="bar them" style="height:{theirs / cap * 110:.0f}px"></div>'
                    f'</div><div class="n">{mine} / {theirs}</div>'
                    f'<div class="d">Day {day["day"]}</div></div>'
                )
            add("</div>")
            add(
                '<div class="key" style="margin-top:14px">'
                f'<span><i class="swatch" style="background:var(--accent)"></i> '
                f"{e(team['name'])}, starters set</span>"
                f'<span><i class="swatch" style="background:var(--tie);opacity:.55"></i> '
                f"{e(week['opponent'])}</span></div>"
            )
        if lw["box"]:
            add(
                '<div class="wrap"><table><thead><tr><th>Category</th>'
                f"<th>{e(team['name'])}</th><th>{e(week['opponent'])}</th>"
                "<th>Result</th></tr></thead><tbody>"
            )
            for row in lw["box"]:
                fmt = pct3 if row["cat"] in ("FG%", "FT%") else num
                res = row.get("result", "")
                cls = "pos" if res == "WIN" else "neg" if res == "LOSS" else "dim"
                label = {"WIN": "won", "LOSS": "lost", "TIE": "tied"}.get(res, "—")
                add(
                    f"<tr><td>{e(row['cat'])}</td><td>{fmt(row.get('mine'))}</td>"
                    f'<td>{fmt(row.get("theirs"))}</td><td class="{cls}">{label}</td></tr>'
                )
            add("</tbody></table></div>")
        add("</section>")

    # -- findings ---------------------------------------------------------
    findings = _findings(d)
    if findings:
        add('<section class="narrow"><div class="shead"><h2>The numbers that stand out</h2>')
        add(f'<span class="tag">{len(findings)} of them</span></div><ol class="fix">')
        for heading, body in findings:
            add(f"<li><div><h3>{e(heading)}</h3><p>{e(body)}</p></div></li>")
        add("</ol></section>")

    if written.get("closing"):
        add('<section class="narrow"><div class="shead"><h2>Last word</h2>')
        add('<span class="tag">Written</span></div>')
        note("closing")
        add("</section>")

    add(
        "<footer>Full Court Press · compiled from daily lineups, per-category matchup detail "
        f"and every transaction of the {d['season'] - 1}–{str(d['season'])[2:]} season."
        "<br>Nine-cat production = PTS + REB + AST + STL + BLK + 3PM − TO. It compares "
        "players inside one category set; the category tables are the actual record."
        "<br>Trades are reconstructed from roster movement, not read from ESPN’s transaction "
        "feed, which is incomplete for trades.</footer></div>"
    )
    return "\n".join(parts)


def _and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


HEAD = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --ground:#EDEFF2; --surface:#FFFFFF; --sunk:#E3E7EC;
  --ink:#11151C; --muted:#5B6474; --faint:#8992A1; --rule:#D2D7DE;
  --accent:#C4551F;
  --win:#1E7A4C; --win-soft:#CFE4D8;
  --loss:#B33A30; --loss-soft:#F0D5D2;
  --tie:#8A93A3; --tie-soft:#DEE2E8;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0E1218; --surface:#161B23; --sunk:#1B222C;
    --ink:#E4E8EE; --muted:#8E9AAC; --faint:#6B7686; --rule:#242C37;
    --accent:#E8763B;
    --win:#4CAE7B; --win-soft:#17301F;
    --loss:#E0685B; --loss-soft:#331A18;
    --tie:#6B7686; --tie-soft:#232A34;
  }
}
:root[data-theme="dark"]{
  --ground:#0E1218; --surface:#161B23; --sunk:#1B222C;
  --ink:#E4E8EE; --muted:#8E9AAC; --faint:#6B7686; --rule:#242C37;
  --accent:#E8763B;
  --win:#4CAE7B; --win-soft:#17301F;
  --loss:#E0685B; --loss-soft:#331A18;
  --tie:#6B7686; --tie-soft:#232A34;
}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);
  font-family:"Source Serif 4",Georgia,serif;font-size:17px;line-height:1.62;
  -webkit-font-smoothing:antialiased}
.page{max-width:1060px;margin:0 auto;padding:0 24px 96px}
h1,h2,h3,.disp{font-family:Oswald,"Arial Narrow",sans-serif;font-weight:600;
  text-wrap:balance;letter-spacing:.01em}
.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.mast{padding:56px 0 28px;border-bottom:3px solid var(--ink)}
.eyebrow{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted)}
.mast h1{font-size:clamp(40px,7.5vw,84px);line-height:.94;margin:14px 0 0;
  text-transform:uppercase;letter-spacing:-.005em}
.mast .sub{margin:12px 0 0;color:var(--muted);font-size:17px;max-width:62ch}
.mast .sub b{color:var(--ink);font-weight:600}
.line{display:grid;grid-template-columns:repeat(auto-fit,minmax(108px,1fr));
  border-bottom:1px solid var(--rule)}
.line div{padding:18px 16px 16px;border-right:1px solid var(--rule)}
.line div.wide{grid-column:span 2}
.line div:last-child{border-right:0}
.line dt{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--faint);margin:0}
.line dd{font-family:Oswald,sans-serif;font-weight:600;font-size:29px;line-height:1.1;
  margin:4px 0 0;font-variant-numeric:tabular-nums;white-space:nowrap}
.line dd small{font-size:14px;color:var(--muted);font-weight:400}
.line .hi dd{color:var(--accent)}
section{margin-top:64px}
section.narrow{max-width:700px}
.shead{display:flex;align-items:baseline;gap:14px;border-bottom:1px solid var(--ink);
  padding-bottom:8px;margin-bottom:22px}
.shead h2{font-size:26px;margin:0;text-transform:uppercase;letter-spacing:.02em}
.shead .tag{margin-left:auto;font-family:"IBM Plex Mono",monospace;font-size:11px;
  letter-spacing:.12em;text-transform:uppercase;color:var(--muted);white-space:nowrap}
p{margin:0 0 16px;max-width:66ch}
.note{color:var(--muted);font-size:15px}
.strip-note{margin:12px 0 0;font-size:13px}
.read{max-width:66ch;margin:0 0 4px}
.read p{margin:0 0 16px}
.read p:last-child{margin-bottom:0}
.read.lede{margin:34px 0 0;border-left:3px solid var(--accent);padding-left:22px}
.read.lede p{font-size:20px;line-height:1.55}
.read.lede p:first-child::first-line{font-weight:600}
.note.caption{margin-top:18px}
.wrap{overflow-x:auto;margin:22px 0 10px}
table{border-collapse:collapse;width:100%;min-width:560px;
  font-family:"IBM Plex Mono",monospace;font-size:13px;font-variant-numeric:tabular-nums}
th{font-weight:500;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--faint);text-align:right;padding:0 10px 8px;border-bottom:1px solid var(--rule);
  white-space:nowrap}
th:first-child,td:first-child{text-align:left;padding-left:0}
td{text-align:right;padding:9px 10px;border-bottom:1px solid var(--rule)}
td:first-child{font-family:"Source Serif 4",serif;font-size:15px}
tbody tr:hover{background:var(--sunk)}
.pos{color:var(--win)}.neg{color:var(--loss)}.dim{color:var(--faint)}
.cost{color:var(--accent);font-weight:600}
.ledger{margin:22px 0 4px;overflow-x:auto}
.lg{min-width:600px;display:grid;grid-template-columns:34px 1fr repeat(9,22px) 52px;
  gap:3px;align-items:center}
.lg .hd{font-family:"IBM Plex Mono",monospace;font-size:9px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--faint);text-align:center;padding-bottom:6px}
.lg .hd.l{text-align:left}
.lg .wk{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--faint);
  text-align:right;padding-right:4px}
.lg .opp{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  padding-right:10px}
.cell{height:22px;border-radius:2px;background:var(--tie-soft)}
.cell.W{background:var(--win-soft);
  box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--win) 30%,transparent)}
.cell.L{background:var(--loss-soft)}
.cell.T{background:var(--tie-soft);box-shadow:inset 0 0 0 1px var(--tie)}
.res{font-family:Oswald,sans-serif;font-weight:600;font-size:12px;text-align:right;
  letter-spacing:.04em}
.res.w{color:var(--win)}.res.l{color:var(--loss)}
.lg .bye{grid-column:2/-1;font-family:"IBM Plex Mono",monospace;font-size:11px;
  color:var(--faint);letter-spacing:.1em;text-transform:uppercase}
.lg .sep{grid-column:1/-1;height:1px;background:var(--ink);margin:6px 0 4px;opacity:.7}
.key{display:flex;flex-wrap:wrap;gap:16px;font-family:"IBM Plex Mono",monospace;
  font-size:11px;color:var(--muted);margin-top:10px}
.key span{display:flex;align-items:center;gap:6px}
.swatch{width:12px;height:12px;border-radius:2px;display:inline-block}
.cats{margin:22px 0 0;display:grid;gap:7px}
.cat{display:grid;grid-template-columns:46px 1fr 92px;align-items:center;gap:12px}
.cat .nm{font-family:"IBM Plex Mono",monospace;font-size:12px;font-weight:500;
  letter-spacing:.04em}
.track{position:relative;height:20px;background:var(--sunk);border-radius:2px;overflow:hidden}
.fill{height:100%;background:var(--win);opacity:.8}
.cat.under .fill{background:var(--loss)}
.mid{position:absolute;top:-3px;bottom:-3px;left:50%;width:1px;background:var(--ink);opacity:.45}
.cat .val{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--muted);
  text-align:right;white-space:nowrap}
.axis{display:grid;grid-template-columns:46px 1fr 92px;gap:12px;margin-top:8px;
  font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--faint)}
.axis .ticks{display:flex;justify-content:space-between}
.trades{display:grid;gap:1px;background:var(--rule);border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule);margin:22px 0 0}
.trade{background:var(--ground);padding:16px 0;display:grid;
  grid-template-columns:64px 1fr 104px;gap:16px;align-items:start}
.trade .day{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--faint);
  letter-spacing:.08em;text-transform:uppercase;padding-top:3px}
.trade .flow{font-size:15px}
.trade .flow .out{color:var(--muted)}
.trade .flow .arrow{color:var(--accent);font-family:"IBM Plex Mono",monospace;padding:0 6px}
.trade .flow .in{font-weight:600}
.trade .flow .why{display:block;color:var(--muted);font-size:14px;margin-top:4px}
.grade{font-family:"IBM Plex Mono",monospace;font-size:12px;font-weight:600;
  letter-spacing:.04em;text-align:right;padding-top:4px;font-variant-numeric:tabular-nums}
.grade.good{color:var(--win)}.grade.bad{color:var(--loss)}.grade.flat{color:var(--muted)}
.fin{display:grid;grid-template-columns:repeat(auto-fit,minmax(56px,1fr));gap:8px;
  margin:22px 0 0;align-items:end}
.fday{display:grid;gap:5px;justify-items:center}
.bars{display:flex;gap:4px;align-items:flex-end;height:110px}
.bar{width:16px;border-radius:1px 1px 0 0}
.bar.me{background:var(--accent)}
.bar.them{background:var(--tie);opacity:.55}
.fday .d{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--faint)}
.fday .n{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--muted)}
ol.fix{list-style:none;counter-reset:f;padding:0;margin:22px 0 0;display:grid;gap:18px}
ol.fix li{counter-increment:f;display:grid;grid-template-columns:30px 1fr;gap:14px}
ol.fix li::before{content:counter(f);font-family:Oswald,sans-serif;font-weight:600;
  font-size:20px;color:var(--accent);line-height:1.4}
ol.fix h3{margin:0 0 4px;font-size:17px;text-transform:none;letter-spacing:0}
ol.fix p{margin:0;color:var(--muted);font-size:16px}
footer{margin-top:72px;padding-top:20px;border-top:1px solid var(--rule);
  font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--faint);line-height:1.9}
@media (max-width:640px){
  body{font-size:16px}
  .trade{grid-template-columns:1fr;gap:6px}
  .grade{text-align:left}
  .bar{width:11px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
/* Printing to PDF: keep backgrounds, keep a heading with what follows it,
   and never split a table row, a trade or a finding across two pages. */
@page { margin: 10mm; }
@media print {
  html, body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h1, h2, h3 { break-after: avoid; page-break-after: avoid; }
  tr, li, figure, blockquote, p { break-inside: avoid; page-break-inside: avoid; }
}
</style>
""".strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--team", help="team name, exactly as ESPN holds it")
    ap.add_argument("--all", action="store_true", help="every team in the season")
    ap.add_argument("--out", default="reports", help="directory for the HTML (default: reports/)")
    ap.add_argument(
        "--notes",
        type=Path,
        help="JSON file of written commentary for the team, keyed by slot "
        f"({', '.join(NOTE_SLOTS)}). Only valid with --team.",
    )
    args = ap.parse_args()

    if not args.team and not args.all:
        ap.error("pass --team NAME or --all")
    if args.notes and args.all:
        ap.error("--notes is per team, so pass it with --team rather than --all")

    notes = load_notes(args.notes) if args.notes else None

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    engine = make_engine(SA_DSN)
    factory: sessionmaker[Session] = make_session_factory(engine)
    with psycopg.connect(DSN) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            season_id = _season_id(cur, args.season)
            names = _team_names(cur, season_id) if args.all else [args.team]

        # Trade reconstruction is a scoring-package query, so it reads through a
        # SQLAlchemy session while the rest of the report keeps its psycopg
        # cursor. One session for the run: the two layers see the same database.
        with factory() as session:
            for name in names:
                data = gather(conn, session, args.season, name)
                path = out / f"{slug(name)}-{args.season}.html"
                path.write_text(render(data, notes), encoding="utf-8")
                print(f"{name}: {path}")
    engine.dispose()


if __name__ == "__main__":
    main()
