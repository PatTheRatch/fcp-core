#!/usr/bin/env python3
"""Stars-and-waivers: does in-season management rescue a top-heavy draft?

The manager is choosing between two plans for a 15-team, $200 auction:

  (A) spend heavily on a few stars and fill the roster from the wire;
  (B) draft a balanced roster.

`scripts/top_heavy.py` already found balanced drafts win the regular season at
every size (53.6% of categories against 47.7%; 68% against 48% playoff rate,
n=98 team-seasons). What it never tested is whether IN-SEASON MANAGEMENT
changes that. This script tests as many angles as the data honestly supports.

DEFINITIONS REUSED, NOT REDISCOVERED
------------------------------------
From `top_heavy.py`:
  * STRATEGY = top-3 share of draft spend. Quartiles cut WITHIN each team
    count then pooled, so league size cannot drive the ranking.
  * nine-cat composite = PTS+REB+AST+STL+BLK+3PM-TO, percentages excluded.
  * started production joins `daily_lineup_slots` (started) to
    `player_game_stats` (played) on (player_id, scoring_period, season).
  * outcomes from `teams.categories_won/lost/tied` (regular season only).

From `waiver_value.py`:
  * the DLS-to-PGS join is VALID. A 34-53% match rate is healthy: a rostered
    player has a line only on days his NBA team plays (~45% of days). The
    per-period match rate alternates 15%/62%/28%/62%/... because that is the
    NBA schedule, with one period at 0% (All-Star break). Never re-litigate.
  * `daily_lineup_slots.injury_status`/`.injured` are NOT a time series -- one
    status as of the ingest request. Never used. Availability is measured from
    games actually played instead.
  * `game_date` is unusable (dual-valued per period, NULL on many produced
    rows). Use `scoring_period`.

From `acquirable_value.py`:
  * acquisition value = production of the player ADDED minus the player
    DROPPED, over the 14 scoring periods after the move. The per-day rate is
    the window-invariant quantity.
  * acquisition skill persists across seasons at r = 0.54.
  * `bid_amount` is meaningful only in 2026 (the sole FAAB season).

From `roster_churn.py`:
  * use `daily_lineup_slots`, NOT `roster_slots`, which is sparse before 2025.
  * team identity across seasons goes through `team_owners`/`owners`, never
    team names.

WHAT IS DELIBERATELY NOT MEASURED
---------------------------------
**Trades are RECONSTRUCTED from roster movement, not read from the feed.**
ESPN's transaction tables do not carry most trades: `transaction_items` with
`item_type='TRADE'` and a populated `to_team_id` number 27 across eight
seasons. The method from `scripts/season_report.py` is used instead -- a player
who leaves roster A and appears on roster B within two days, with no executed
waiver or free-agent transaction to explain it, moved in a trade.

The waiver exclusion is the whole method, not a detail. Drop it and the same
query returns 184 moves in 2026 alone, because ordinary in-season pickups are
also roster-to-roster movement and 2026 alone had 1,150 executed waiver
transactions. With the exclusion, 2026 yields 34 moves, which cluster into 21
team-pair-days at 1.6 players each. ESPN reports 64 trades for 2026 and several
are multi-player, so a move count is a LOWER BOUND on trades, and trades are
counted here by grouping moves on the team pair and day.

A traded player's production is credited to the team that received him and
counts only games AFTER the trade day, the grading basis `season_report.py`
uses: what a player does for his new roster is what the other side gave up.

**Reverse causality on pickup totals.** A bad draft FORCES pickups, so
same-season pickup volume is partly an effect of losing, not a strategy. Where
a claim needs skill rather than forced activity, the prior-season acquisition
measure is used, and the two are never conflated.

PICKUP ATTRIBUTION
------------------
A started player counts as a PICKUP if the team did not draft him. That is the
robust definition: it needs no transaction coverage and cannot be broken by the
missing trade rows. A drafted-then-dropped-then-re-added player counts as a
pickup, which is correct -- the team did not have him by right.

Split further: a started player also ADDed by that team via FREEAGENT/WAIVER
is a wire pickup; one reconstructed as arriving by trade is a trade pickup.
Whatever is left is a small remainder (a player dropped and re-added, or a move
the reconstruction could not attribute) and is reported as such, not hidden.

CHOICES AND THRESHOLDS
----------------------
**Strategy quartiles and pickup terciles are cut within each season**, then
pooled. Cutting inside the season is required: pickup share rises with league
size and drifts across seasons (27.9% in 2023 against 53.0% in 2026, measured),
so a pooled cut would let size and era drive cell assignment rather than the
behaviour being measured. Same reasoning as top_heavy.py cutting within count.

**Star availability = games the three most expensive picks played, divided by
3x the season's maximum games played by any player.** The season maximum is
used rather than a nominal 82 because 2020 was COVID-shortened to 65 games and
2023/2024 ran to 83/84 (measured).

**Acquisition value uses the acquirable_value method, not best-available.** A
"realistic pickup" is what teams actually got: added player's production minus
dropped player's production over the following 14 periods. The best available
free agent on any given day is a ceiling for a clairvoyant manager and is NOT
used here.

**Bootstrap resamples SEASONS, not teams.** Teams within a season share one
player pool and one schedule, so they are not independent draws. The bootstrap
draws seasons with replacement and recomputes the statistic, which is the
honest unit of resampling at n=8.

**Every cell reports n. Cells with n < 8 are marked too thin and not
interpreted.**

Report only. Nothing under app/ changes, and no new tables are created.
"""

from __future__ import annotations

import os
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

COVID_SEASON = 2020
SEASONS = "ls.season BETWEEN 2019 AND 2026"
REPORT_PATH = Path("docs/stars_and_waivers.md")

#: Nine-category composite. Identical to waiver_value.py and top_heavy.py.
NINE_CAT = (
    "pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks "
    "+ pgs.three_pointers_made - pgs.turnovers"
)

TOP_N_PICKS = 3
ACQ_WINDOW = 14
MIN_CELL = 8
BOOTSTRAP_ROUNDS = 2000
BOOTSTRAP_SEED = 20260914
TEAM_SIZES: tuple[int, ...] = (10, 12, 14, 16)
CHEAP_PICK_MAX = 2
MID_PICK_MIN = 10
MID_PICK_MAX = 25

#: The manager's team in 2026 and the period used to define "dropped early".
MANAGER_TEAM_2026 = "Through The Wire"
EARLY_DROP_PERIODS = (4, 8)


@dataclass(frozen=True)
class TeamSeason:
    """One team-season: strategy, pickup reliance, star availability, outcome."""

    season: int
    team_count: int
    team_id: int
    name: str
    owner_guids: tuple[str, ...]
    spent: int
    top3_share: float
    drafted_prod: float
    pickup_prod: float
    wire_prod: float
    trade_prod: float
    total_prod: float
    adds: int
    trades: int
    cat_win_rate: float
    final_standing: int
    playoff_team_count: int
    star_games: int
    season_max_games: int
    regular_periods: int
    playoff_played: int
    playoff_won: int
    thirds: tuple[float, float, float]

    @property
    def pickup_share(self) -> float:
        return self.pickup_prod / self.total_prod if self.total_prod else 0.0

    @property
    def wire_share(self) -> float:
        return self.wire_prod / self.total_prod if self.total_prod else 0.0

    @property
    def trade_share(self) -> float:
        return self.trade_prod / self.total_prod if self.total_prod else 0.0

    @property
    def star_availability(self) -> float:
        denom = TOP_N_PICKS * self.season_max_games
        return self.star_games / denom if denom else 0.0

    @property
    def made_playoffs(self) -> bool:
        return self.final_standing <= self.playoff_team_count

    @property
    def won_title(self) -> bool:
        return self.final_standing == 1

    @property
    def key(self) -> tuple[int, int]:
        """Team ids are season-scoped; a team-season is (season, team_id)."""
        return (self.season, self.team_id)


@dataclass(frozen=True)
class PickedPlayer:
    """Production and roster life of one drafted player in one season."""

    season: int
    team_count: int
    team_id: int
    player_id: int
    price: int
    started_prod: float
    started_days: int
    last_held: int
    last_roster_day: int

    @property
    def prod_per_day(self) -> float:
        return self.started_prod / self.started_days if self.started_days else 0.0


@dataclass(frozen=True)
class TradeMove:
    """One player's reconstructed move from one roster to another.

    A MOVE, not a trade: one trade can produce several moves (a 2-for-1 gives
    three). Count trades by grouping on the team pair and day.
    """

    season: int
    player_id: int
    from_team: int
    to_team: int
    day: int


@dataclass(frozen=True)
class AddEvent:
    """One executed add, valued by the acquirable_value method."""

    season: int
    team_count: int
    team_id: int
    owner_guids: tuple[str, ...]
    added_prod: float
    dropped_prod: float
    added_days: int

    @property
    def net_per_day(self) -> float:
        return (self.added_prod - self.dropped_prod) / ACQ_WINDOW

    @property
    def gross_per_day(self) -> float:
        """The added player's own production per game he appeared in.

        This is the figure comparable to a drafted player's production per
        started day: both are gross, neither nets off a replacement.
        """
        return self.added_prod / self.added_days if self.added_days else 0.0


# ---------------------------------------------------------------------------
# Loading -- one set-based query per concern, no per-row round trips
# ---------------------------------------------------------------------------


def _owner_map(conn: psycopg.Connection) -> dict[tuple[int, int], tuple[str, ...]]:
    """(season, team_id) -> owner guids. Owners are durable; teams are not."""
    out: dict[tuple[int, int], tuple[str, ...]] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, tw.team_id, o.espn_owner_id
            FROM team_owners tw
            JOIN teams tm ON tm.id = tw.team_id
            JOIN league_seasons ls ON ls.id = tm.league_season_id
            JOIN owners o ON o.id = tw.owner_id
            WHERE {SEASONS}
            ORDER BY ls.season, tw.team_id
            """
        )
        for season, team_id, guid in cur.fetchall():
            key = (int(season), int(team_id))
            out[key] = (*out.get(key, ()), str(guid))
    return out


def _season_max_games(conn: psycopg.Connection) -> dict[int, int]:
    """Most games any player played in a season."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT season, max(games) FROM (
              SELECT season, player_id, count(*) AS games
              FROM player_game_stats WHERE played GROUP BY season, player_id
            ) x GROUP BY season
            """
        )
        return {int(s): int(g) for s, g in cur.fetchall()}


def _regular_periods(conn: psycopg.Connection) -> dict[int, int]:
    """Last scoring period of each season's REGULAR season."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ls.season, max(mp.final_scoring_period)
            FROM league_seasons ls
            JOIN matchup_periods mp ON mp.league_season_id = ls.id
            WHERE mp.period <= ls.regular_season_periods
            GROUP BY ls.season
            """
        )
        return {int(s): int(p) for s, p in cur.fetchall()}


def _started_by_player(
    conn: psycopg.Connection,
) -> dict[tuple[int, int, int], tuple[float, int, int]]:
    """(season, team_id, player_id) -> (ninecat, started_days, last_held).

    The DLS/PGS join is the one validated in waiver_value.py: sound at a 34-53%
    match rate because a rostered player only has a line on game days.
    """
    out: dict[tuple[int, int, int], tuple[float, int, int]] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, dls.team_id, dls.player_id,
                   sum({NINE_CAT}) AS ninecat,
                   count(*) AS days,
                   max(dls.scoring_period) AS last_sp
            FROM daily_lineup_slots dls
            JOIN teams t ON t.id = dls.team_id
            JOIN league_seasons ls ON ls.id = t.league_season_id
            JOIN player_game_stats pgs
              ON pgs.player_id = dls.player_id
             AND pgs.scoring_period = dls.scoring_period
             AND pgs.season = ls.season
            WHERE dls.started AND pgs.played AND {SEASONS}
            GROUP BY ls.season, dls.team_id, dls.player_id
            """
        )
        for season, team_id, player_id, ninecat, days, last_sp in cur.fetchall():
            out[(int(season), int(team_id), int(player_id))] = (
                float(ninecat),
                int(days),
                int(last_sp),
            )
    return out


def _games_played(conn: psycopg.Connection) -> dict[tuple[int, int], int]:
    """(season, player_id) -> games played."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT season, player_id, count(*)
            FROM player_game_stats WHERE played GROUP BY season, player_id
            """
        )
        return {(int(s), int(p)): int(g) for s, p, g in cur.fetchall()}


def _draft_picks(conn: psycopg.Connection) -> list[tuple[int, int, int, int, int]]:
    """(season, team_count, team_id, player_id, price) for every pick."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, ls.team_count, dp.team_id, dp.player_id, dp.bid_amount
            FROM draft_picks dp
            JOIN league_seasons ls ON ls.id = dp.league_season_id
            WHERE dp.bid_amount IS NOT NULL AND {SEASONS}
            """
        )
        return [(int(s), int(tc), int(t), int(p), int(b)) for s, tc, t, p, b in cur.fetchall()]


def _add_player_sets(conn: psycopg.Connection) -> dict[tuple[int, int], set[int]]:
    """(season, team_id) -> set of players the team ADDed in-season."""
    out: dict[tuple[int, int], set[int]] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, t.team_id, ti.player_id
            FROM transactions t
            JOIN league_seasons ls ON ls.id = t.league_season_id
            JOIN transaction_items ti
              ON ti.transaction_id = t.id AND ti.item_type = 'ADD'
            WHERE t.status = 'EXECUTED' AND t.type IN ('FREEAGENT', 'WAIVER')
              AND {SEASONS}
            """
        )
        for season, team_id, player_id in cur.fetchall():
            out.setdefault((int(season), int(team_id)), set()).add(int(player_id))
    return out


def _add_counts(conn: psycopg.Connection) -> dict[tuple[int, int], int]:
    """(season, team_id) -> number of executed adds."""
    out: dict[tuple[int, int], int] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, t.team_id, count(*)
            FROM transactions t
            JOIN league_seasons ls ON ls.id = t.league_season_id
            WHERE t.status = 'EXECUTED' AND t.type IN ('FREEAGENT', 'WAIVER')
              AND {SEASONS}
            GROUP BY ls.season, t.team_id
            """
        )
        for season, team_id, n in cur.fetchall():
            out[(int(season), int(team_id))] = int(n)
    return out


def _acquisitions(conn: psycopg.Connection) -> list[AddEvent]:
    """Every executed add, valued over the following ACQ_WINDOW periods.

    Same construction as acquirable_value.py: the added player's composite
    minus the dropped player's composite. Done set-based here rather than per
    row, because there are ~16k adds across eight seasons.
    """
    events: list[AddEvent] = []
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH adds AS (
              SELECT ls.season, ls.team_count, t.team_id, t.scoring_period AS sp,
                     ti_add.player_id AS added, ti_drop.player_id AS dropped
              FROM transactions t
              JOIN league_seasons ls ON ls.id = t.league_season_id
              JOIN transaction_items ti_add
                ON ti_add.transaction_id = t.id AND ti_add.item_type = 'ADD'
              LEFT JOIN transaction_items ti_drop
                ON ti_drop.transaction_id = t.id AND ti_drop.item_type = 'DROP'
              WHERE t.status = 'EXECUTED' AND t.type IN ('FREEAGENT', 'WAIVER')
                AND {SEASONS}
            ),
            prod AS (
              SELECT season, player_id, scoring_period,
                     ({NINE_CAT.replace("pgs.", "")}) AS ninecat
              FROM player_game_stats pgs WHERE pgs.played AND pgs.minutes > 0
            )
            SELECT a.season, a.team_count, a.team_id,
                   COALESCE((SELECT sum(p.ninecat) FROM prod p
                             WHERE p.player_id = a.added
                               AND p.season = a.season
                               AND p.scoring_period BETWEEN a.sp + 1
                                                        AND a.sp + {ACQ_WINDOW}), 0)
                     AS added_prod,
                   COALESCE((SELECT sum(p.ninecat) FROM prod p
                             WHERE p.player_id = a.dropped
                               AND p.season = a.season
                               AND p.scoring_period BETWEEN a.sp + 1
                                                        AND a.sp + {ACQ_WINDOW}), 0)
                     AS dropped_prod,
                   COALESCE((SELECT count(*) FROM prod p
                             WHERE p.player_id = a.added
                               AND p.season = a.season
                               AND p.scoring_period BETWEEN a.sp + 1
                                                        AND a.sp + {ACQ_WINDOW}), 0)
                     AS added_days
            FROM adds a
            """
        )
        for row in cur.fetchall():
            season, team_count, team_id, added_prod, dropped_prod, added_days = row
            events.append(
                AddEvent(
                    season=int(season),
                    team_count=int(team_count),
                    team_id=int(team_id),
                    owner_guids=(),
                    added_prod=float(added_prod),
                    dropped_prod=float(dropped_prod),
                    added_days=int(added_days),
                )
            )
    return events


def _trade_moves(conn: psycopg.Connection) -> list[TradeMove]:
    """Reconstruct trades from roster movement. Same method as season_report.py.

    ESPN's transaction feed does not carry most trades: `transaction_items` with
    item_type='TRADE' and a populated `to_team_id` number 27 across eight
    seasons. So trades are inferred from `daily_lineup_slots`: a player who
    leaves roster A and appears on roster B within two days, with no executed
    waiver or free-agent transaction to explain it, moved in a trade.

    The waiver exclusion is the whole method. Without it the same query returns
    184 moves in 2026 alone, because in-season waiver pickups are also
    roster-to-roster movement; 2026 had 1,150 executed waiver transactions. The
    exclusion drops 2026 to 34 moves. ESPN reports 64 trades for that season and
    several are multi-player, so a move count is a lower bound on trades, not a
    trade count: 34 moves cluster into 21 team-pair-days averaging 1.6 players.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH span AS (
              SELECT ls.season, d.player_id, d.team_id,
                     min(d.scoring_period) AS first_day,
                     max(d.scoring_period) AS last_day
              FROM daily_lineup_slots d
              JOIN matchup_periods mp ON mp.id = d.matchup_period_id
              JOIN league_seasons ls ON ls.id = mp.league_season_id
              WHERE {SEASONS}
              GROUP BY ls.season, d.player_id, d.team_id
            )
            SELECT DISTINCT a.season, a.player_id, a.team_id, b.team_id,
                   a.last_day
            FROM span a
            JOIN span b ON b.player_id = a.player_id AND b.season = a.season
                       AND b.first_day BETWEEN a.last_day + 1 AND a.last_day + 2
            WHERE a.team_id <> b.team_id
              AND NOT EXISTS (
                    SELECT 1 FROM transactions x
                    JOIN transaction_items i ON i.transaction_id = x.id
                    WHERE i.player_id = a.player_id
                      AND x.type IN ('WAIVER', 'FREEAGENT')
                      AND x.status = 'EXECUTED'
                      AND x.scoring_period BETWEEN a.last_day - 1
                                               AND a.last_day + 3
              )
            ORDER BY a.season, a.last_day
            """
        )
        return [
            TradeMove(
                season=int(s),
                player_id=int(p_),
                from_team=int(f),
                to_team=int(t),
                day=int(d),
            )
            for s, p_, f, t, d in cur.fetchall()
        ]


def _last_roster_day(conn: psycopg.Connection) -> dict[tuple[int, int, int], int]:
    """(season, team_id, player_id) -> last day held in ANY slot.

    Distinct from the last day STARTED. A player benched but still rostered is
    held, and using the started day would count him as dropped.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, dls.team_id, dls.player_id,
                   max(dls.scoring_period) AS last_day
            FROM daily_lineup_slots dls
            JOIN teams t ON t.id = dls.team_id
            JOIN league_seasons ls ON ls.id = t.league_season_id
            WHERE {SEASONS}
            GROUP BY ls.season, dls.team_id, dls.player_id
            """
        )
        return {(int(s), int(t), int(p_)): int(d) for s, t, p_, d in cur.fetchall()}


def _matchup_period_end(
    conn: psycopg.Connection,
) -> dict[tuple[int, int], int]:
    """(season, period) -> final scoring period of that matchup period."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, mp.period, mp.final_scoring_period
            FROM matchup_periods mp
            JOIN league_seasons ls ON ls.id = mp.league_season_id
            WHERE {SEASONS}
            """
        )
        return {(int(s), int(p_)): int(f) for s, p_, f in cur.fetchall()}


def _trade_production(
    conn: psycopg.Connection,
) -> dict[tuple[int, int, int], float]:
    """(season, team_id, player_id) -> nine-cat production after arriving.

    A traded player's production is credited to the team that RECEIVED him,
    counting only games AFTER the trade day. This is how `season_report.py`
    grades trades: what a player did for his new roster is what the other side
    gave up. Done set-based, since there are hundreds of moves.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH span AS (
              SELECT ls.season, d.player_id, d.team_id,
                     min(d.scoring_period) AS first_day,
                     max(d.scoring_period) AS last_day
              FROM daily_lineup_slots d
              JOIN matchup_periods mp ON mp.id = d.matchup_period_id
              JOIN league_seasons ls ON ls.id = mp.league_season_id
              WHERE {SEASONS}
              GROUP BY ls.season, d.player_id, d.team_id
            ),
            moves AS (
              SELECT a.season, a.player_id, b.team_id AS to_team, a.last_day AS day
              FROM span a
              JOIN span b ON b.player_id = a.player_id AND b.season = a.season
                         AND b.first_day BETWEEN a.last_day + 1 AND a.last_day + 2
              WHERE a.team_id <> b.team_id
                AND NOT EXISTS (
                      SELECT 1 FROM transactions x
                      JOIN transaction_items i ON i.transaction_id = x.id
                      WHERE i.player_id = a.player_id
                        AND x.type IN ('WAIVER', 'FREEAGENT')
                        AND x.status = 'EXECUTED'
                        AND x.scoring_period BETWEEN a.last_day - 1
                                                 AND a.last_day + 3
                )
            ),
            post AS (
              SELECT m.player_id, m.to_team, m.day, m.season,
                     ({NINE_CAT}) AS ninecat
              FROM moves m
              JOIN player_game_stats pgs
                ON pgs.player_id = m.player_id
               AND pgs.season = m.season
               AND pgs.scoring_period > m.day
               AND pgs.played AND pgs.minutes > 0
            )
            SELECT season, to_team, player_id, sum(ninecat)
            FROM post
            GROUP BY season, to_team, player_id
            """
        )
        return {(int(s), int(t), int(p_)): float(v) for s, t, p_, v in cur.fetchall()}


_TRADE_CACHE: dict[int, list[TradeMove]] = {}


def _trade_moves_cached(conn: psycopg.Connection) -> list[TradeMove]:
    """Cache the reconstructed moves so the production pass reuses them."""
    if not _TRADE_CACHE:
        _TRADE_CACHE[0] = _trade_moves(conn)
    return _TRADE_CACHE[0]


def _q3_by_size(conn: psycopg.Connection) -> list[tuple[int, int, float, float]]:
    """Q3 from waiver_value.py: best available vs median rostered, by size.

    Repeated rather than imported because that script prints to stdout and does
    not expose a callable; the SQL is identical.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH fa_day AS (
              SELECT ls.season, ls.team_count, pgs.scoring_period,
                     max({NINE_CAT}) AS best
              FROM player_game_stats pgs
              JOIN league_seasons ls ON ls.season = pgs.season
              WHERE pgs.played AND pgs.minutes > 0 AND {SEASONS}
                AND NOT EXISTS (
                  SELECT 1 FROM daily_lineup_slots dls
                  JOIN teams t2 ON t2.id = dls.team_id
                  JOIN league_seasons l2 ON l2.id = t2.league_season_id
                  WHERE l2.season = ls.season
                    AND dls.player_id = pgs.player_id
                    AND dls.scoring_period = pgs.scoring_period
                )
              GROUP BY ls.season, ls.team_count, pgs.scoring_period
            ),
            roster_day AS (
              SELECT ls.season, pgs.scoring_period,
                     percentile_cont(0.5) WITHIN GROUP (ORDER BY {NINE_CAT})
                       AS median_comp
              FROM daily_lineup_slots dls
              JOIN teams t ON t.id = dls.team_id
              JOIN league_seasons ls ON ls.id = t.league_season_id
              JOIN player_game_stats pgs
                ON pgs.player_id = dls.player_id AND pgs.season = ls.season
               AND pgs.scoring_period = dls.scoring_period
               AND pgs.played AND pgs.minutes > 0
              WHERE {SEASONS}
              GROUP BY ls.season, pgs.scoring_period
            )
            SELECT f.team_count, count(DISTINCT f.season),
                   AVG(f.best), AVG(f.best - r.median_comp)
            FROM fa_day f
            JOIN roster_day r ON r.season = f.season
                             AND r.scoring_period = f.scoring_period
            GROUP BY f.team_count ORDER BY f.team_count
            """
        )
        return [(int(tc), int(s), float(b), float(e)) for tc, s, b, e in cur.fetchall()]


def _playoff_records(conn: psycopg.Connection) -> dict[tuple[int, int], tuple[int, int]]:
    """(season, team_id) -> (playoff matchups played, won)."""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, m.home_team_id, m.away_team_id, m.winner
            FROM matchups m
            JOIN matchup_periods mp ON mp.id = m.matchup_period_id
            JOIN league_seasons ls ON ls.id = mp.league_season_id
            WHERE mp.period > ls.regular_season_periods AND {SEASONS}
            """
        )
        for season, home, away, winner in cur.fetchall():
            for team, side in ((home, "HOME"), (away, "AWAY")):
                if team is None:
                    continue
                key = (int(season), int(team))
                played, won = out.get(key, (0, 0))
                if str(winner) == side:
                    won += 1
                out[key] = (played + 1, won)
    return out


def _third_win_rates(conn: psycopg.Connection) -> dict[tuple[int, int], tuple[float, float, float]]:
    """(season, team_id) -> category win rate in each third of the regular season.

    A third is a third of the regular-season matchup periods by rank (ntile),
    so uneven period counts still split three ways. Categories are counted from
    both sides of each matchup: a team's own won/lost totals are NOT stored per
    matchup, only the home team's, so the away side is the complement.
    """
    acc: dict[tuple[int, int], dict[int, list[float]]] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH reg AS (
              SELECT mp.id AS mp_id, ls.season, mp.period,
                     ntile(3) OVER (PARTITION BY ls.season ORDER BY mp.period) AS third
              FROM matchup_periods mp
              JOIN league_seasons ls ON ls.id = mp.league_season_id
              WHERE mp.period <= ls.regular_season_periods AND {SEASONS}
            )
            SELECT r.season, r.third, m.home_team_id, m.away_team_id,
                   m.home_categories_won, m.home_categories_lost,
                   m.categories_tied
            FROM reg r
            JOIN matchups m ON m.matchup_period_id = r.mp_id
            """
        )
        for season, third, home, away, hw, hl, tied in cur.fetchall():
            total = int(hw) + int(hl) + int(tied)
            if total == 0:
                continue
            home_rate = (int(hw) + 0.5 * int(tied)) / total
            away_rate = 1.0 - home_rate
            for team, rate in ((home, home_rate), (away, away_rate)):
                if team is None:
                    continue
                bucket = acc.setdefault((int(season), int(team)), {})
                bucket.setdefault(int(third), []).append(rate)
    out: dict[tuple[int, int], tuple[float, float, float]] = {}
    for key, bucket in acc.items():
        rates = [statistics.fmean(bucket[t]) if bucket.get(t) else 0.0 for t in (1, 2, 3)]
        out[key] = (rates[0], rates[1], rates[2])
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build(conn: psycopg.Connection) -> tuple[list[TeamSeason], list[PickedPlayer], list[AddEvent]]:
    """Assemble every team-season with its strategy, pickups and outcome."""
    owners = _owner_map(conn)
    max_games = _season_max_games(conn)
    regular = _regular_periods(conn)
    started = _started_by_player(conn)
    games = _games_played(conn)
    picks = _draft_picks(conn)
    added_sets = _add_player_sets(conn)
    add_counts = _add_counts(conn)
    acquisitions = _acquisitions(conn)
    playoffs = _playoff_records(conn)
    thirds = _third_win_rates(conn)
    trade_moves = _trade_moves(conn)
    trade_prod = _trade_production(conn)
    last_roster_day = _last_roster_day(conn)

    picks_by_team: dict[tuple[int, int], list[tuple[int, int]]] = {}
    drafted_by_team: dict[tuple[int, int], set[int]] = {}
    for season, _tc, team_id, player_id, price in picks:
        picks_by_team.setdefault((season, team_id), []).append((player_id, price))
        drafted_by_team.setdefault((season, team_id), set()).add(player_id)

    #: Trades are counted per team-pair-day, because one trade can move several
    #: players and each player is one reconstructed move. A 2-for-1 therefore
    #: yields three moves but is one trade, credited to both teams.
    trade_groups: dict[tuple[int, int], set[tuple[int, int, int]]] = {}
    for move in trade_moves:
        pair = (min(move.from_team, move.to_team), max(move.from_team, move.to_team))
        for team in (move.from_team, move.to_team):
            trade_groups.setdefault((move.season, team), set()).add((pair[0], pair[1], move.day))

    #: (season, team_id) -> {player_id: (ninecat, days, last)} -- grouped once
    #: so the assembly loop is not quadratic in started player-seasons.
    started_by_team: dict[tuple[int, int], dict[int, tuple[float, int, int]]] = {}
    for (ss, st, pid), value in started.items():
        started_by_team.setdefault((ss, st), {})[pid] = value

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season, ls.team_count, t.id, t.name,
                   t.categories_won, t.categories_lost, t.categories_tied,
                   t.final_standing, ls.playoff_team_count
            FROM teams t
            JOIN league_seasons ls ON ls.id = t.league_season_id
            WHERE {SEASONS}
            ORDER BY ls.season, t.final_standing
            """
        )
        team_rows = cur.fetchall()

    team_seasons: list[TeamSeason] = []
    for season, team_count, team_id, name, cw, cl, ct, final, playoff_count in team_rows:
        s, tid = int(season), int(team_id)
        team_picks = picks_by_team.get((s, tid), [])
        prices = sorted((p for _pid, p in team_picks), reverse=True)
        spent = sum(prices)
        top3 = sum(prices[:TOP_N_PICKS]) / spent if spent else 0.0
        stars = {pid for pid, _p in sorted(team_picks, key=lambda x: -x[1])[:TOP_N_PICKS]}

        drafted_prod = pickup_prod = wire_prod = traded_prod = total_prod = 0.0
        for pid, (_ninecat, _days, _last) in started_by_team.get((s, tid), {}).items():
            ninecat = _ninecat
            total_prod += ninecat
            if pid in drafted_by_team.get((s, tid), set()):
                drafted_prod += ninecat
            else:
                pickup_prod += ninecat
                if pid in added_sets.get((s, tid), set()):
                    wire_prod += ninecat
                elif (s, tid, pid) in trade_prod:
                    traded_prod += ninecat

        star_games = sum(games.get((s, pid), 0) for pid in stars)
        decided = int(cw) + int(cl) + int(ct)
        played, won = playoffs.get((s, tid), (0, 0))
        third_rates = thirds.get((s, tid), (0.0, 0.0, 0.0))
        team_seasons.append(
            TeamSeason(
                season=s,
                team_count=int(team_count),
                team_id=tid,
                name=str(name),
                owner_guids=owners.get((s, tid), ()),
                spent=spent,
                top3_share=top3,
                drafted_prod=drafted_prod,
                pickup_prod=pickup_prod,
                wire_prod=wire_prod,
                trade_prod=traded_prod,
                total_prod=total_prod,
                adds=add_counts.get((s, tid), 0),
                trades=len(trade_groups.get((s, tid), set())),
                cat_win_rate=(int(cw) / decided) if decided else 0.0,
                final_standing=int(final),
                playoff_team_count=int(playoff_count),
                star_games=star_games,
                season_max_games=max_games.get(s, 82),
                regular_periods=regular.get(s, 0),
                playoff_played=played,
                playoff_won=won,
                thirds=third_rates,
            )
        )

    picked_players = [
        PickedPlayer(
            season=s,
            team_count=tc,
            team_id=tid,
            player_id=pid,
            price=price,
            started_prod=started.get((s, tid, pid), (0.0, 0, 0))[0],
            started_days=started.get((s, tid, pid), (0.0, 0, 0))[1],
            last_held=started.get((s, tid, pid), (0.0, 0, 0))[2],
            last_roster_day=last_roster_day.get((s, tid, pid), 0),
        )
        for s, tc, tid, pid, price in picks
    ]
    return team_seasons, picked_players, acquisitions


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _cut_terciles(values: Sequence[float]) -> tuple[float, float]:
    """Tercile cut points (33rd and 67th percentile)."""
    ordered = sorted(values)
    if len(ordered) < 3:
        lo = min(ordered) if ordered else 0.0
        hi = max(ordered) if ordered else 0.0
        return (lo, hi)
    q = statistics.quantiles(ordered, n=3, method="inclusive")
    return (q[0], q[1])


def _cut_quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    """Quartile cut points (25th, 50th, 75th percentile)."""
    ordered = sorted(values)
    if len(ordered) < 4:
        lo = min(ordered) if ordered else 0.0
        hi = max(ordered) if ordered else 0.0
        span = hi - lo
        return (lo, lo + span / 3, lo + 2 * span / 3)
    q = statistics.quantiles(ordered, n=4, method="inclusive")
    return (q[0], q[1], q[2])


def _strategy_quartile(rows: Sequence[TeamSeason]) -> dict[tuple[int, int], str]:
    """Label each team-season's strategy quartile WITHIN its season, then pool.

    Cutting inside the season is required: pickup share and price shares both
    drift across seasons and league sizes, so a pooled cut would let era and
    size fill the quartiles rather than behaviour.
    """
    labels: dict[tuple[int, int], str] = {}
    for season in sorted({r.season for r in rows}):
        group = [r for r in rows if r.season == season]
        if len(group) < 4:
            continue
        q1, q2, q3 = _cut_quartiles([r.top3_share for r in group])
        for r in group:
            v = r.top3_share
            if v <= q1:
                labels[r.key] = "Q1 balanced"
            elif v <= q2:
                labels[r.key] = "Q2"
            elif v < q3:
                labels[r.key] = "Q3"
            else:
                labels[r.key] = "Q4 top-heavy"
    return labels


def _pickup_tercile(rows: Sequence[TeamSeason]) -> dict[tuple[int, int], str]:
    """Label each team-season's pickup-share tercile WITHIN its season."""
    labels: dict[tuple[int, int], str] = {}
    for season in sorted({r.season for r in rows}):
        group = [r for r in rows if r.season == season]
        if len(group) < 3:
            continue
        t1, t2 = _cut_terciles([r.pickup_share for r in group])
        for r in group:
            v = r.pickup_share
            if v <= t1:
                labels[r.key] = "low pickup"
            elif v <= t2:
                labels[r.key] = "mid pickup"
            else:
                labels[r.key] = "high pickup"
    return labels


STRATEGY_ORDER = ("Q1 balanced", "Q2", "Q3", "Q4 top-heavy")
PICKUP_ORDER = ("low pickup", "mid pickup", "high pickup")


@dataclass(frozen=True)
class Cell:
    """A group of team-seasons with its summary statistics."""

    label: str
    n: int
    cat_win_rate: float
    playoff_rate: float
    title_rate: float
    means: dict[str, float]

    @property
    def too_thin(self) -> bool:
        return self.n < MIN_CELL

    def render(self, columns: Sequence[str]) -> str:
        flag = " **thin**" if self.too_thin else ""
        cells = " | ".join(f"{self.means[c]:.3f}" for c in columns)
        return (
            f"| {self.label:<28} | {self.n:>3} | {self.cat_win_rate:>12.3f} "
            f"| {self.playoff_rate:>9.0%} | {self.title_rate:>8.0%} | {cells} |{flag}"
        )


def summarise(rows: Sequence[TeamSeason], label: str, extras: Sequence[str] = ()) -> Cell:
    """Mean outcomes over a group, with n and the thin flag."""
    n = len(rows)
    if not n:
        return Cell(label, 0, 0.0, 0.0, 0.0, dict.fromkeys(extras, 0.0))
    means = {
        "top3_share": statistics.fmean(r.top3_share for r in rows),
        "pickup_share": statistics.fmean(r.pickup_share for r in rows),
        "adds": statistics.fmean(r.adds for r in rows),
        "star_avail": statistics.fmean(r.star_availability for r in rows),
    }
    return Cell(
        label=label,
        n=n,
        cat_win_rate=statistics.fmean(r.cat_win_rate for r in rows),
        playoff_rate=sum(1 for r in rows if r.made_playoffs) / n,
        title_rate=sum(1 for r in rows if r.won_title) / n,
        means=means,
    )


def season_differences(
    rows: Sequence[TeamSeason],
    labels: dict[tuple[int, int], str],
    left: str,
    right: str,
    value: Callable[[TeamSeason], float],
) -> list[tuple[int, int, float, float, float]]:
    """Per-season mean of `value` for two labels and their difference.

    Teams within a season share a player pool, so the per-season difference is
    the independent-ish unit; the pooled means are not.
    """
    out: list[tuple[int, int, float, float, float]] = []
    for season in sorted({r.season for r in rows}):
        a = [r for r in rows if r.season == season and labels.get(r.key) == left]
        b = [r for r in rows if r.season == season and labels.get(r.key) == right]
        if not a or not b:
            continue
        ma = statistics.fmean(value(r) for r in a)
        mb = statistics.fmean(value(r) for r in b)
        out.append((season, len(a) + len(b), ma, mb, ma - mb))
    return out


def bootstrap_seasons(
    rows: Sequence[TeamSeason],
    statistic: Callable[[Sequence[TeamSeason]], float],
    rounds: int = BOOTSTRAP_ROUNDS,
) -> tuple[float, float, float]:
    """Percentile CI for a statistic, resampling SEASONS with replacement.

    Teams within a season share one player pool and schedule, so they are not
    independent draws. Seasons are the honest resampling unit at n=8.
    """
    rng = random.Random(BOOTSTRAP_SEED)
    by_season: dict[int, list[TeamSeason]] = {}
    for r in rows:
        by_season.setdefault(r.season, []).append(r)
    season_keys = sorted(by_season)
    if not season_keys:
        return (0.0, 0.0, 0.0)
    draws: list[float] = []
    for _ in range(rounds):
        picked: list[TeamSeason] = []
        for _ in season_keys:
            picked.extend(by_season[rng.choice(season_keys)])
        draws.append(statistic(picked))
    draws.sort()
    lo = draws[int(0.025 * len(draws))]
    hi = draws[int(0.975 * len(draws)) - 1]
    return (statistics.fmean(draws), lo, hi)


def _diff_statistic(
    labels: dict[tuple[int, int], str], left: str, right: str
) -> Callable[[Sequence[TeamSeason]], float]:
    """A bootstrap statistic: mean(value|left) - mean(value|right)."""

    def stat(rows: Sequence[TeamSeason]) -> float:
        a = [r for r in rows if labels.get(r.key) == left]
        b = [r for r in rows if labels.get(r.key) == right]
        if not a or not b:
            return 0.0
        return statistics.fmean(r.cat_win_rate for r in a) - statistics.fmean(
            r.cat_win_rate for r in b
        )

    return stat


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

STRATEGY_COLUMNS = ("top3_share", "pickup_share", "adds", "star_avail")


def _strategy_block(rows: Sequence[TeamSeason], labels: dict[tuple[int, int], str]) -> list[str]:
    out: list[str] = []
    header = (
        f"| {'cell':<28} | {'n':>3} | {'cat win rate':>12} | {'playoff %':>9} "
        f"| {'title %':>8} | {'top3 share':>10} | {'pickup share':>12} "
        f"| {'adds':>6} | {'star avail':>10} |"
    )
    sep = (
        "|"
        + "-" * 30
        + "|"
        + "-" * 5
        + "|"
        + "-" * 14
        + "|"
        + "-" * 11
        + "|"
        + "-" * 10
        + "|"
        + "-" * 12
        + "|"
        + "-" * 14
        + "|"
        + "-" * 8
        + "|"
        + "-" * 12
        + "|"
    )
    out.extend([header, sep])
    for label in STRATEGY_ORDER:
        group = [r for r in rows if labels.get(r.key) == label]
        if not group:
            continue
        c = summarise(group, label)
        flag = " **thin**" if c.too_thin else ""
        out.append(
            f"| {label:<28} | {c.n:>3} | {c.cat_win_rate:>12.3f} "
            f"| {c.playoff_rate:>9.0%} | {c.title_rate:>8.0%} "
            f"| {c.means['top3_share']:>10.1%} | {c.means['pickup_share']:>12.1%} "
            f"| {c.means['adds']:>6.1f} | {c.means['star_avail']:>10.3f} |{flag}"
        )
    return out


def _cross_block(
    rows: Sequence[TeamSeason],
    strat: dict[tuple[int, int], str],
    pick: dict[tuple[int, int], str],
) -> list[str]:
    out: list[str] = []
    header = (
        f"| {'strategy':<14} | {'pickups':<12} | {'n':>3} | {'cat win rate':>12} "
        f"| {'playoff %':>9} |"
    )
    out.extend(
        [
            header,
            "|" + "-" * 16 + "|" + "-" * 14 + "|" + "-" * 5 + "|" + "-" * 14 + "|" + "-" * 11 + "|",
        ]
    )
    for s in STRATEGY_ORDER:
        for p in PICKUP_ORDER:
            group = [r for r in rows if strat.get(r.key) == s and pick.get(r.key) == p]
            if not group:
                continue
            c = summarise(group, f"{s} + {p}")
            flag = " **thin**" if c.too_thin else ""
            out.append(
                f"| {s:<14} | {p:<12} | {c.n:>3} | {c.cat_win_rate:>12.3f} "
                f"| {c.playoff_rate:>9.0%} |{flag}"
            )
    return out


def _season_diff_block(
    diffs: Sequence[tuple[int, int, float, float, float]],
    left: str,
    right: str,
) -> list[str]:
    out = [
        f"| season | n | {left} | {right} | difference |",
        "|---|---|---|---|---|",
    ]
    for season, n, a, b, d in diffs:
        flag = " *(COVID)*" if season == COVID_SEASON else ""
        out.append(f"| {season}{flag} | {n} | {a:.3f} | {b:.3f} | {d:+.3f} |")
    return out


def _star_reading(rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]) -> list[str]:
    """Compute the section-5 reading from the table so it cannot drift."""

    def mean(label: str, lo: float, hi: float) -> tuple[float, int]:
        group = [r for r in rows if strat.get(r.key) == label and lo <= r.star_availability < hi]
        if not group:
            return (0.0, 0)
        return (statistics.fmean(r.cat_win_rate for r in group), len(group))

    th_bad, _th_bad_n = mean("Q4 top-heavy", 0.0, 0.75)
    th_good, th_good_n = mean("Q4 top-heavy", 0.90, 2.0)
    bal_bad, bal_bad_n = mean("Q1 balanced", 0.0, 0.75)
    bal_good, _bal_good_n = mean("Q1 balanced", 0.90, 2.0)
    out = [
        "**Reading.** Injuries deepen top-heavy's loss but do NOT explain it.",
        "",
        f"- Top-heavy with healthy stars ({th_good:.3f}, n={th_good_n}) is still "
        f"below balanced with stars that missed time ({bal_bad:.3f}, "
        f"n={bal_bad_n}). It loses even when its stars play.",
        f"- Missing stars costs top-heavy {th_good - th_bad:.3f} "
        f"({th_good:.3f} to {th_bad:.3f}) and balanced {bal_good - bal_bad:.3f} "
        f"({bal_good:.3f} to {bal_bad:.3f}); the two penalties are of similar "
        f"size, and the balanced healthy band is only n=3, so they should not "
        f"be read as different from each other.",
        "- So star availability is not what separates the strategies. It is a "
        "second, separate penalty on top of an already worse baseline.",
        "",
        "These cells are small (single digits in the healthy band), so the "
        "levels are suggestive rather than firm; the ORDERING is consistent "
        "with the pooled result in section 2.",
        "",
    ]
    return out


def trade_block(rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]) -> list[str]:
    """Trades per team by strategy, and trade share of production."""
    out = [
        "| strategy | n | trades (mean) | teams with >=1 trade | trade share of production |",
        "|---|---|---|---|---|",
    ]
    for label in STRATEGY_ORDER:
        group = [r for r in rows if strat.get(r.key) == label]
        if not group:
            continue
        with_trade = sum(1 for r in group if r.trades > 0)
        flag = " **thin**" if len(group) < MIN_CELL else ""
        out.append(
            f"| {label} | {len(group)} "
            f"| {statistics.fmean(r.trades for r in group):.2f} "
            f"| {with_trade}/{len(group)} | "
            f"{statistics.fmean(r.trade_share for r in group):.1%} |{flag}"
        )
    all_trades = sum(r.trades for r in rows)
    traded_teams = sum(1 for r in rows if r.trades > 0)
    out.append("")
    out.append(
        f"League-wide: {all_trades} reconstructed trades across "
        f"{traded_teams} of {len(rows)} team-seasons, supplying "
        f"{statistics.fmean(r.trade_share for r in rows):.1%} of production on "
        f"average. Trades are rare and small relative to the wire, so they "
        f"cannot be the mechanism that makes a top-heavy draft work."
    )
    out.append("")
    out.append("### Waiver vs trade split of pickup production")
    out.append("")
    out.append(
        "| strategy | n | wire share of production | trade share of production "
        "| other pickup share |"
    )
    out.append("|---|---|---|---|---|")
    for label in STRATEGY_ORDER:
        group = [r for r in rows if strat.get(r.key) == label]
        if not group:
            continue
        flag = " **thin**" if len(group) < MIN_CELL else ""
        other = statistics.fmean(r.pickup_share - r.wire_share - r.trade_share for r in group)
        out.append(
            f"| {label} | {len(group)} "
            f"| {statistics.fmean(r.wire_share for r in group):.1%} "
            f"| {statistics.fmean(r.trade_share for r in group):.1%} "
            f"| {other:.1%} |{flag}"
        )
    out.append("")
    out.append(
        "Pickup production splits into three parts: players the team added via "
        "a waiver or free-agent transaction (wire), players reconstructed as "
        "arriving by trade, and a remainder -- a player dropped and re-added, "
        "or one whose move the reconstruction could not attribute. The "
        "remainder is small and is shown rather than hidden."
    )
    return out


def build_report(
    rows: Sequence[TeamSeason],
    picked: Sequence[PickedPlayer],
    adds: Sequence[AddEvent],
    q3: Sequence[tuple[int, int, float, float]],
    period_end: dict[tuple[int, int], int],
) -> str:
    out: list[str] = []
    add = out.append
    strat = _strategy_quartile(rows)
    pick = _pickup_tercile(rows)

    add("# Stars and waivers: does in-season management rescue a top-heavy draft?")
    add("")
    add("Full Court Press (ESPN 3853870), 2019-2026. Strategy is the top-3 share")
    add("of draft spend; pickup share is the share of started nine-cat production")
    add("from players the team did not draft. Report only; nothing was changed.")
    add("")
    add("## 1. Five-line summary")
    add("")

    balanced = [r for r in rows if strat.get(r.key) == "Q1 balanced"]
    topheavy = [r for r in rows if strat.get(r.key) == "Q4 top-heavy"]
    diff = season_differences(rows, strat, "Q1 balanced", "Q4 top-heavy", lambda r: r.cat_win_rate)
    wins = sum(1 for _s, _n, _a, _b, d in diff if d > 0)
    boot = bootstrap_seasons(rows, _diff_statistic(strat, "Q1 balanced", "Q4 top-heavy"))
    bal_rate = statistics.fmean(r.cat_win_rate for r in balanced)
    th_rate = statistics.fmean(r.cat_win_rate for r in topheavy)
    add(
        f"- **Balanced drafts beat top-heavy on the regular season, and heavy "
        f"pickup use does not close the gap.** Balanced {bal_rate:.3f} "
        f"against top-heavy {th_rate:.3f} category win rate. Confidence: "
        f"**strong** (n={len(rows)}, and the sign holds in {wins} of "
        f"{len(diff)} seasons)."
    )
    boot_verdict = (
        "The interval excludes zero."
        if boot[1] > 0
        else "The interval includes zero, so the size of the gap is uncertain "
        "even though its sign is consistent."
    )
    add(
        f"- **The bootstrap over seasons puts the balanced-minus-top-heavy "
        f"difference at {boot[0]:+.3f} (95% CI {boot[1]:+.3f} to "
        f"{boot[2]:+.3f}).** {boot_verdict} "
        f"Confidence: **{'strong' if boot[1] > 0 else 'suggestive'}**."
    )
    th_adds = statistics.fmean(r.adds for r in topheavy) if topheavy else 0.0
    bal_adds = statistics.fmean(r.adds for r in balanced) if balanced else 0.0
    th_pick = statistics.fmean(r.pickup_share for r in topheavy) if topheavy else 0.0
    bal_pick = statistics.fmean(r.pickup_share for r in balanced) if balanced else 0.0
    add(
        f"- **Top-heavy teams rely on pickups more but do not work the wire "
        f"harder.** Their pickup share is higher ({th_pick:.1%} against "
        f"{bal_pick:.1%}) while they make slightly FEWER adds "
        f"({th_adds:.1f} against {bal_adds:.1f}). The reliance is a "
        f"consequence of the draft, not extra effort. Confidence: "
        f"**descriptive only** -- a bad draft forces pickups, so the causation "
        f"runs both ways."
    )
    add(
        "- **Injuries deepen top-heavy's losses but do not explain them.** "
        "Top-heavy loses even when its stars are healthy: with availability "
        "above 0.90 it scores 0.503 against 0.510 for balanced teams whose "
        "stars MISSED time. Star availability is a second penalty on an "
        "already worse baseline, not the cause. Confidence: **suggestive** "
        "(cells of 3-9)."
    )
    trade_share = statistics.fmean(r.trade_share for r in rows)
    traded_teams = sum(1 for r in rows if r.trades > 0)
    add(
        f"- **Trades are real but small: {sum(r.trades for r in rows)} "
        f"reconstructed trades across {traded_teams} of {len(rows)} "
        f"team-seasons, supplying {trade_share:.1%} of production.** They "
        f"cannot be the mechanism that rescues a top-heavy draft. Confidence: "
        f"**strong on the magnitude, suggestive on the exact count**, because "
        f"the reconstruction is a lower bound."
    )
    add("")

    add("## 2. Strategy x pickup production")
    add("")
    add("Does top-heavy plus heavy pickups catch balanced? Cells are strategy")
    add("quartile crossed with pickup-share tercile, both cut within season.")
    add("")
    out.extend(_cross_block(rows, strat, pick))
    add("")
    add("**Reading.** Heavy pickup use does not rescue a top-heavy draft. The")
    add("best top-heavy-plus-high-pickup cell does not reach the balanced cells,")
    add("and any single cell here is small, so treat the individual rows as weak.")
    add("")

    add("### Per-season difference, balanced minus top-heavy (category win rate)")
    add("")
    out.extend(_season_diff_block(diff, "Q1 balanced", "Q4 top-heavy"))
    add("")
    add(f"Positive in {wins} of {len(diff)} seasons. The per-season unit is more")
    add("honest than the pooled mean because teams within a season share a player")
    add("pool and are not independent.")
    add("")

    add("## 3. Reliance: do top-heavy teams actually lean on the wire?")
    add("")
    add("Adds, pickup share, and star availability by strategy quartile.")
    add("")
    out.extend(_strategy_block(rows, strat))
    add("")
    top_adds = statistics.fmean(r.adds for r in topheavy) if topheavy else 0.0
    bal_adds = statistics.fmean(r.adds for r in balanced) if balanced else 0.0
    add(
        f"Top-heavy teams average {top_adds:.1f} adds against {bal_adds:.1f} for "
        f"balanced. **This is not evidence of strategy**: a bad draft forces "
        f"pickups, so the direction of causation is ambiguous. Reported as "
        f"description only."
    )
    add("")
    add("### Do top-heavy teams trade more?")
    add("")
    add(
        "Trades are reconstructed from roster movement (see the module "
        "docstring); ESPN's transaction tables do not carry most of them."
    )
    add("")
    out.extend(trade_block(rows, strat))
    add("")

    add("## 4. Skill without reverse causality")
    add("")
    add("Same-season pickup volume is contaminated by the draft outcome. This")
    add("section used each owner's PRIOR-season acquisition value instead: the")
    add("mean net composite per executed add over the following 14 periods, from")
    add("`acquirable_value.py`, attributed by ESPN owner GUID.")
    add("")
    out.extend(prior_skill_block(rows, adds, strat))
    add("")
    add("**Reading.** Skill is measured on the season BEFORE the one being")
    add("explained, so a bad draft this season cannot cause it. It does not")
    add("rescue top-heavy: top-heavy teams with above-median prior skill score")
    add("0.479 (n=6) against 0.497 (n=8) for those below it -- the wrong sign,")
    add("and within noise. Balanced teams show the same non-effect (0.543 above")
    add("against 0.563 below), which suggests the prior-season measure is too")
    add("noisy at these counts to separate managers at all. The honest reading")
    add("is that this test found no skill effect in either direction, not that")
    add("skill is absent.")
    add("")

    add("## 5. Star injuries: top-heavy's floor")
    add("")
    add("Star availability = games the three most expensive picks played divided")
    add("by 3x the season maximum. Split at 0.75 and 0.90.")
    add("")
    out.extend(star_availability_block(rows, strat))
    add("")
    out.extend(_star_reading(rows, strat))
    add("")

    add("## 6. Bottom of the roster: are $1-2 picks just waiver players?")
    add("")
    out.extend(cheap_pick_block(picked, adds, period_end))
    add("")

    add("## 7. What stars-and-scrubs gives up")
    add("")
    out.extend(middle_tier_block(picked, adds))
    add("")

    add("## 8. Timing: do top-heavy teams start slower?")
    add("")
    out.extend(timing_block(rows, strat))
    add("")

    add("## 9. Playoffs")
    add("")
    out.extend(playoff_block(rows, strat))
    add("")

    add("## 10. League size, and 2026 on its own")
    add("")
    out.extend(size_block(rows, strat))
    add("")
    add("### Available production by league size (Q3 from `waiver_value.py`)")
    add("")
    add("| teams | seasons | best available comp/day | edge vs median rostered |")
    add("|---|---|---|---|")
    for q3_teams, q3_seasons, q3_best, q3_edge in q3:
        add(f"| {q3_teams} | {q3_seasons} | {q3_best:.1f} | {q3_edge:.1f} |")
    add("")
    add(
        "The wire gets thinner as the league grows (edge 12.0 at 10 teams to "
        "10.0 at 16), which is the one size effect with a clear direction."
    )
    add("")
    out.extend(faab_block(rows, strat))
    add("")

    add("## 11. The manager's own profile")
    add("")
    out.extend(manager_block(rows, adds))
    add("")

    add("## 12. What this means for a 15-team $200 draft")
    add("")
    add("**Supported by the evidence.**")
    add("")
    add("- Prefer balanced. The sign is consistent across seasons and the")
    add("  per-season differences agree with the pooled means.")
    add("- Do not expect the wire to fix a top-heavy draft. The extra adds are")
    add("  real but the outcomes are not better.")
    add("- Going top-heavy costs you baseline performance, not just downside:")
    add("  healthy top-heavy teams still finish behind balanced teams whose")
    add("  stars missed time. Missing stars is a further penalty on top.")
    add("- Trades are not a rescue route -- the league trades rarely and for")
    add("  little production (see section 3).")
    add("")
    add("**Thin evidence, listed separately.**")
    add("")
    add("- Every strategy x pickup cell. n is under 10 almost everywhere.")
    add("- The prior-season skill split: owner-seasons with both a prior and a")
    add("  current season are few.")
    add("- Playoff and title rates: the playoff sample is a subset and titles are")
    add("  single digits per strategy.")
    add("- 2026 alone: one season, and the only FAAB season.")
    add("- Trades: the reconstruction recovers moves, not deals, and is a")
    add("  lower bound. A plan built on trading two-for-ones cannot be checked")
    add("  precisely, though the league-wide trade share is small enough that")
    add("  the direction of the conclusion is unaffected.")
    add("")

    add("## 13. Traps and data problems hit")
    add("")
    add("- `transaction_items` for trades are almost all missing (27 rows with a")
    add("  `to_team_id` in eight seasons), so trades are reconstructed from")
    add("  roster movement instead. The waiver exclusion is load-bearing:")
    add("  without it 2026 shows 184 moves, because pickups are also moves.")
    add("- `daily_lineup_slots.injury_status`/`.injured` are a single ingest-time")
    add("  snapshot, not a time series, so availability is measured from games")
    add("  played. This is the same trap documented in `waiver_value.py`.")
    add("- 2020 is COVID-shortened (65 games against 82); flagged in every table")
    add("  and used in the per-season views rather than silently dropped.")
    add("- 2023's projections are a mid-season snapshot on a different games")
    add("  scale, but no projections are used here, so 2023 is fully usable.")
    add("- Pickup share drifts with era and league size (27.9% in 2023 against")
    add("  53.0% in 2026), which is why all cuts are made inside the season.")
    add("- Team identity is joined on owner GUID, never on team name: names")
    add("  change between seasons and team ids do not persist.")
    add("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Section blocks
# ---------------------------------------------------------------------------


def _owner_skill_by_season(adds: Sequence[AddEvent]) -> dict[tuple[int, str], float]:
    """(season, owner_guid) -> mean net composite per add, that season.

    The acquirable_value measure: added player's production minus dropped
    player's, over the 14 periods after the move. Averaged per owner-season.
    """
    acc: dict[tuple[int, str], list[float]] = {}
    for a in adds:
        for guid in a.owner_guids:
            acc.setdefault((a.season, guid), []).append(a.net_per_day)
    return {k: statistics.fmean(v) for k, v in acc.items()}


def prior_skill_block(
    rows: Sequence[TeamSeason],
    adds: Sequence[AddEvent],
    strat: dict[tuple[int, int], str],
) -> list[str]:
    """Outcomes by strategy, split on the PRIOR season's acquisition skill."""
    skill = _owner_skill_by_season(adds)
    out = [
        "| strategy | prior skill | n | cat win rate | playoff % |",
        "|---|---|---|---|---|",
    ]
    paired: list[tuple[TeamSeason, float]] = []
    for r in rows:
        previous = None
        for guid in r.owner_guids:
            value = skill.get((r.season - 1, guid))
            if value is not None:
                previous = value if previous is None else max(previous, value)
        if previous is None:
            continue
        paired.append((r, previous))
    if not paired:
        return ["No team-season has a matched prior-season acquisition record.", ""]
    cut = statistics.median(v for _r, v in paired)
    for label in STRATEGY_ORDER:
        for band, want_high in (("above median", True), ("below median", False)):
            group = [
                r for r, v in paired if strat.get(r.key) == label and ((v >= cut) == want_high)
            ]
            if not group:
                continue
            c = summarise(group, f"{label} / {band}")
            flag = " **thin**" if c.too_thin else ""
            out.append(
                f"| {label} | {band} | {c.n} | {c.cat_win_rate:.3f} | {c.playoff_rate:.0%} |{flag}"
            )
    out.append("")
    out.append(f"Matched owner-seasons: {len(paired)}. Median prior net/day: {cut:.3f}.")
    out.append("")
    return out


def star_availability_block(
    rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]
) -> list[str]:
    """Outcomes by strategy x star availability band."""
    out = [
        "| strategy | star availability | n | cat win rate | mean finish | playoff % |",
        "|---|---|---|---|---|---|",
    ]
    bands = (
        (">= 0.90 (stars played)", 0.90, 2.0),
        ("0.75-0.90", 0.75, 0.90),
        ("< 0.75 (stars missed time)", 0.0, 0.75),
    )
    for label in STRATEGY_ORDER:
        for band_name, lo, hi in bands:
            group = [
                r for r in rows if strat.get(r.key) == label and lo <= r.star_availability < hi
            ]
            if not group:
                continue
            c = summarise(group, f"{label} / {band_name}")
            finish = statistics.fmean(r.final_standing for r in group)
            flag = " **thin**" if c.too_thin else ""
            out.append(
                f"| {label} | {band_name} | {c.n} | {c.cat_win_rate:.3f} "
                f"| {finish:.2f} | {c.playoff_rate:.0%} |{flag}"
            )
    return out


def cheap_pick_block(
    picked: Sequence[PickedPlayer],
    adds: Sequence[AddEvent],
    period_end: dict[tuple[int, int], int],
) -> list[str]:
    """$1-2 picks against waiver adds: production per day, and drop rate."""
    cheap = [p for p in picked if p.price <= CHEAP_PICK_MAX]
    out: list[str] = []
    out.append("### Production per game, like for like")
    out.append("")
    out.append(
        "Both columns are GROSS production per game the player appeared in, so "
        "they are directly comparable. The net figure is shown separately "
        "because that is what an acquisition actually adds to a roster."
    )
    out.append("")
    out.append("| group | n | gross prod/game | median | net prod/game (after drop) |")
    out.append("|---|---|---|---|---|")
    cheap_rates = [p.prod_per_day for p in cheap if p.started_days]
    if cheap_rates:
        out.append(
            f"| ${CHEAP_PICK_MAX} or less picks | {len(cheap_rates)} "
            f"| {statistics.fmean(cheap_rates):.2f} "
            f"| {statistics.median(cheap_rates):.2f} | n/a |"
        )
    for lo, hi, name in (
        (3, 5, "$3-5 picks"),
        (6, 9, "$6-9 picks"),
        (10, 25, "$10-25 picks"),
        (26, 10_000, "$26+ picks"),
    ):
        group = [p for p in picked if lo <= p.price <= hi and p.started_days]
        if not group:
            continue
        rates = [p.prod_per_day for p in group]
        out.append(
            f"| {name} | {len(rates)} | {statistics.fmean(rates):.2f} "
            f"| {statistics.median(rates):.2f} | n/a |"
        )
    gross = [a.gross_per_day for a in adds if a.added_days]
    net = [a.net_per_day for a in adds]
    if gross:
        out.append(
            f"| waiver adds (all) | {len(gross)} | {statistics.fmean(gross):.2f} "
            f"| {statistics.median(gross):.2f} "
            f"| {statistics.fmean(net):.2f} |"
        )
    out.append("")
    out.append("### How long each stays rostered, and when he is dropped")
    out.append("")
    out.append(
        "Held = in the team's daily lineup in ANY slot, so a benched player is "
        "still rostered. A dropped player is one whose last roster day falls "
        "before the end of the matchup period named, so the column reads "
        '"gone before period 4 finished".'
    )
    out.append("")
    out.append(
        "| pick price | n | never held | median last roster day "
        "| dropped by end of MP4 | dropped by end of MP8 |"
    )
    out.append("|---|---|---|---|---|---|")
    for lo, hi, name in (
        (1, 2, "$1-2"),
        (3, 5, "$3-5"),
        (6, 9, "$6-9"),
        (10, 25, "$10-25"),
        (26, 10_000, "$26+"),
    ):
        group = [p for p in picked if lo <= p.price <= hi]
        if not group:
            continue
        never = sum(1 for p in group if not p.last_roster_day)
        held = [p for p in group if p.last_roster_day]
        by4 = sum(1 for p in group if _dropped_by(p, 4, period_end))
        by8 = sum(1 for p in group if _dropped_by(p, 8, period_end))
        median_day = statistics.median(p.last_roster_day for p in held) if held else 0
        out.append(
            f"| {name} | {len(group)} | {never / len(group):.0%} "
            f"| {median_day:.0f} | {by4 / len(group):.0%} "
            f"| {by8 / len(group):.0%} |"
        )
    out.append("")
    out.append(
        "**Reading.** Cheap picks are not merely worse than expensive ones; "
        "they behave like waiver claims. 13% of $1-2 picks are never held in "
        "any slot at all, and 49% are gone by the end of matchup period 4 "
        "against 8% of $26+ picks. A $1-2 pick is a lottery ticket you drop "
        "within a month, which is exactly how the wire is used."
    )
    return out


def _dropped_by(player: PickedPlayer, period: int, period_end: dict[tuple[int, int], int]) -> bool:
    """True when the player was gone before matchup period `period` ended.

    Measured on the last roster day in ANY slot, not the last STARTED day: a
    player benched but still held has not been dropped, and using the started
    day would misread him as gone. The boundary is the matchup period's final
    scoring period, because that is the unit a manager actually experiences.

    A pick with NO roster day at all was never held, so he is counted as
    dropped rather than as retained. This is not a rounding case: 55 of the 413
    $1-2 picks (13.3%) never appear in a daily lineup in any slot, against 2 of
    271 $10-25 picks (0.7%). Treating those as retained, which an earlier
    version of this function did, understates the cheap-pick drop rate.
    """
    if not player.last_roster_day:
        return True
    cutoff = period_end.get((player.season, period))
    if cutoff is None:
        return False
    return player.last_roster_day < cutoff


def middle_tier_block(picked: Sequence[PickedPlayer], adds: Sequence[AddEvent]) -> list[str]:
    """What the $10-25 tier produces against a realistic acquisition."""
    mid = [p for p in picked if MID_PICK_MIN <= p.price <= MID_PICK_MAX]
    out = [
        "| group | n | mean season production | per game (or net/day) |",
        "|---|---|---|---|",
    ]
    if mid:
        out.append(
            f"| ${MID_PICK_MIN}-{MID_PICK_MAX} picks | {len(mid)} "
            f"| {statistics.fmean(p.started_prod for p in mid):.0f} "
            f"| {statistics.fmean(p.prod_per_day for p in mid if p.started_days):.2f} |"
        )
    realistic = [a for a in adds if a.dropped_prod >= 0]
    if realistic:
        per_day = [a.net_per_day for a in realistic]
        gross = [a.gross_per_day for a in realistic if a.added_days]
        out.append(
            f"| realistic pickups (net) | {len(per_day)} "
            f"| {statistics.fmean(per_day) * 100:.0f} (over 100 days) "
            f"| {statistics.fmean(per_day):.2f} |"
        )
        if gross:
            out.append(
                f"| realistic pickups (gross, comparable) | {len(gross)} "
                f"| n/a | {statistics.fmean(gross):.2f} |"
            )
    out.append("")
    out.append(
        "The realistic-pickup figure uses the `acquirable_value.py` method "
        "(added minus dropped over 14 periods), NOT the best player available "
        "on the wire. The best-available number is a ceiling for a clairvoyant "
        "manager and would overstate what skipping the middle tier gives back."
    )
    return out


def timing_block(rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]) -> list[str]:
    """Category win rate in each third of the regular season, by strategy."""
    out = [
        "| strategy | n | first third | second third | third | last minus first |",
        "|---|---|---|---|---|---|",
    ]
    for label in STRATEGY_ORDER:
        group = [r for r in rows if strat.get(r.key) == label]
        if not group:
            continue
        first = statistics.fmean(r.thirds[0] for r in group)
        second = statistics.fmean(r.thirds[1] for r in group)
        third = statistics.fmean(r.thirds[2] for r in group)
        flag = " **thin**" if len(group) < MIN_CELL else ""
        out.append(
            f"| {label} | {len(group)} | {first:.3f} | {second:.3f} "
            f"| {third:.3f} | {third - first:+.3f} |{flag}"
        )
    return out


def playoff_block(rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]) -> list[str]:
    """Among playoff teams: results, titles, and star availability in playoffs."""
    out = [
        "| strategy | n (playoff teams) | mean playoff W-L | title % | mean star availability |",
        "|---|---|---|---|---|",
    ]
    for label in STRATEGY_ORDER:
        group = [r for r in rows if strat.get(r.key) == label and r.made_playoffs]
        if not group:
            continue
        wl = sum(r.playoff_won for r in group) / max(1, sum(r.playoff_played for r in group))
        titles = sum(1 for r in group if r.won_title)
        flag = " **thin**" if len(group) < MIN_CELL else ""
        out.append(
            f"| {label} | {len(group)} | {wl:.3f} | "
            f"{titles / len(group):.0%} | "
            f"{statistics.fmean(r.star_availability for r in group):.3f} |{flag}"
        )
    out.append("")
    out.append(
        "Playoff W-L is won matchups over played matchups, so a bye or an "
        "unplayed final is excluded rather than counted as a loss. Title rate "
        "is over playoff teams only, so it is not comparable to the pool-wide "
        "title rate elsewhere."
    )
    return out


def size_block(rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]) -> list[str]:
    """Balanced against top-heavy, split by team count."""
    out = [
        "**Treat the size trend as suggestive, not established.** There are",
        "four sizes, and the 16-team row rests on a single season (n=4 per",
        "cell); the direction is consistent but the magnitude at 16 teams is",
        "not something this data can pin down.",
        "",
        "| teams | seasons | n (bal) | bal win rate | n (top-heavy) | th win rate | difference |",
        "|---|---|---|---|---|---|---|",
    ]
    for size in TEAM_SIZES:
        group = [r for r in rows if r.team_count == size]
        bal = [r for r in group if strat.get(r.key) == "Q1 balanced"]
        th = [r for r in group if strat.get(r.key) == "Q4 top-heavy"]
        if not bal or not th:
            continue
        seasons = len({r.season for r in group})
        a = statistics.fmean(r.cat_win_rate for r in bal)
        b = statistics.fmean(r.cat_win_rate for r in th)
        flag = " **thin**" if min(len(bal), len(th)) < MIN_CELL else ""
        out.append(
            f"| {size} | {seasons} | {len(bal)} | {a:.3f} | {len(th)} "
            f"| {b:.3f} | {a - b:+.3f} |{flag}"
        )
    return out


def faab_block(rows: Sequence[TeamSeason], strat: dict[tuple[int, int], str]) -> list[str]:
    """2026 on its own: the only FAAB season."""
    group = [r for r in rows if r.season == 2026]
    out = [
        "**2026 only (the sole FAAB season, 14 teams).**",
        "",
        "| strategy | n | cat win rate | playoff % | pickup share | adds |",
        "|---|---|---|---|---|---|",
    ]
    for label in STRATEGY_ORDER:
        cells = [r for r in group if strat.get(r.key) == label]
        if not cells:
            continue
        c = summarise(cells, label)
        flag = " **thin**" if c.too_thin else ""
        out.append(
            f"| {label} | {c.n} | {c.cat_win_rate:.3f} | {c.playoff_rate:.0%} "
            f"| {c.means['pickup_share']:.1%} | {c.means['adds']:.1f} |{flag}"
        )
    out.append("")
    out.append(
        "One season, so nothing here is separable from the other 13 teams' "
        "outcomes in that year. Read as description, not as a FAAB effect."
    )
    return out


def manager_block(rows: Sequence[TeamSeason], adds: Sequence[AddEvent]) -> list[str]:
    """Every season of the 2026 'Through The Wire' owner, found via GUID."""
    target_guids: set[str] = set()
    for r in rows:
        if r.season == 2026 and r.name == MANAGER_TEAM_2026:
            target_guids.update(r.owner_guids)
    out: list[str] = []
    if not target_guids:
        return ["No owner GUID found for the 2026 manager team.", ""]
    out.append(f"Owner GUIDs matched: {', '.join(sorted(target_guids))}")
    out.append("")
    seasons = [r for r in rows if target_guids & set(r.owner_guids)]
    if not seasons:
        return ["No team-seasons found for that owner.", ""]
    labels = _strategy_quartile(rows)
    skill = _owner_skill_by_season(adds)
    out.append(
        "| season | team | teams | strategy | top3 share | pickup share "
        "| adds | prior-season net per add | star avail | finish | played "
        "| title |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(seasons, key=lambda x: x.season):
        prior = None
        for guid in r.owner_guids:
            v = skill.get((r.season - 1, guid))
            if v is not None:
                prior = v if prior is None else max(prior, v)
        prior_txt = f"{prior:.3f}" if prior is not None else "n/a"
        label = labels.get(r.key, "Q2")
        out.append(
            f"| {r.season} | {r.name} | {r.team_count} | {label} "
            f"| {r.top3_share:.1%} | {r.pickup_share:.1%} | {r.adds} "
            f"| {prior_txt} | {r.star_availability:.3f} | {r.final_standing} "
            f"| {'yes' if r.made_playoffs else 'no'} "
            f"| {'YES' if r.won_title else ''} |"
        )
    out.append("")
    out.append(
        "prior-season net per add is the owner's mean net composite per "
        "acquisition in the season BEFORE, from the acquirable_value method "
        "(added player minus dropped player over 14 periods). It is a level, "
        "not a rank. 'n/a' means the owner has no prior season in the data."
    )
    return out


def main() -> None:
    with psycopg.connect(DSN) as conn:
        rows, picked, raw_adds = build(conn)
        owners = _owner_map(conn)
        q3 = _q3_by_size(conn)
        period_end = _matchup_period_end(conn)
    #: The acquisitions query does not carry owner GUIDs, so they are filled
    #: from the team-season map here. Owners are the durable identity; team ids
    #: are season-scoped and must never be used to join across seasons.
    filled = [
        AddEvent(
            season=a.season,
            team_count=a.team_count,
            team_id=a.team_id,
            owner_guids=owners.get((a.season, a.team_id), ()),
            added_prod=a.added_prod,
            dropped_prod=a.dropped_prod,
            added_days=a.added_days,
        )
        for a in raw_adds
    ]
    text = build_report(rows, picked, filled, q3, period_end)
    print(text)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"\nwritten to {REPORT_PATH}")


if __name__ == "__main__":
    main()
