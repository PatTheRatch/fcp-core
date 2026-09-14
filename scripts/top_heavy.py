#!/usr/bin/env python3
"""Does a top-heavy draft pay off in this league, and does league size change it?

The draft model now says this room overpays for stars, so the optimiser should
let stars go and buy depth. That is the model's OBJECTIVE talking. This script
asks what the league's own HISTORY says, because if teams that concentrated
money at the top actually won more -- especially at 14 and 16 teams, where the
player pool is thinner -- the objective is wrong and the room is right.

2027 is a 15-team season, so the answer has to hold at sizes we have not run.

WHY 2023 IS INCLUDED HERE
-------------------------
Earlier analyses excluded 2023 because its `player_season_stats` projections are
a mid-season snapshot on a different games scale (measured elsewhere: 37.9
average projected games against 62-73 in every other season). This analysis uses
NO projections. Every input is a draft price or a realised outcome, so 2023 is
fully usable -- and it is the only 16-team season, which is the size the
question turns on.

TWO MEASURES, KEPT SEPARATE
---------------------------
The whole point is not to conflate what a manager CHOSE with what HAPPENED.

STRATEGY -- how the money was spent at the draft. Chosen, in advance:
    top1_share     largest single price / total spent
    top3_share     three largest prices / total spent   <- the ranking variable
    hhi            Herfindahl index over the 13 prices, sum of squared shares

EXECUTION -- how concentrated the production turned out. Not chosen:
    exec_share     share of the team's STARTED nine-category production that
                   came from its three most expensive draft picks

A team can choose top-heavy and get burned by an injury, or choose balanced and
luck into a star. Strategy and execution therefore diverge, and the divergence
is the measured injury/bust rate (see "STRATEGY VS EXECUTION").

OUTCOMES, per team-season:
    cat_win_rate   categories_won / (won + lost + tied), regular season
    final_standing 1 is best
    made_playoffs  final_standing <= playoff_team_count
    won_title      final_standing == 1

Defined from `teams.categories_won/lost/tied`, which are REGULAR-SEASON tallies
only (verified: they sum to the regular-season matchup count, not the playoff
one). Playoffs are separate, so a team can win categories and lose the title.

CHOICES AND THRESHOLDS
----------------------
**Quartiles are computed within each team count**, then pooled. Cutting the
pooled distribution instead would let league size drive the ranking: a
16-team season spreads the same $200 over more players, so its price shares are
systematically lower and would fill the "balanced" quartile. Cutting within
size removes that. Pooled tables then average across sizes with n shown.

**A team's total spent is the sum of its pick prices**, not a fixed $200. Both
are defensible; measured, mean spend is $199.6 of $200, so the difference is
immaterial, and using the observed sum keeps the shares internally consistent.

**Top-3 share is the ranking variable, not HHI.** They measure nearly the same
thing (both are concentration) and correlate strongly, but top-3 share is the
number a manager can act on before the draft -- "put 55% of my money in three
players" -- where HHI is not something anyone reasons in. HHI is reported
alongside as a robustness check.

**Replacement level is the 25th percentile of started-player season production
within a season.** The natural definition, "the team_count x 13th best player",
was measured and rejected: it swings from 446 to 746 across seasons because it
depends on how many players were ever started, which churn drives. The 25th
percentile of started players is stable in an 85-146 band across all eight
seasons and is what "a marginal starter" means. It is computed per season and
never pooled across sizes.

**STAR SURPLUS = (production of the season's top-3 drafted players by price)
- 3 x (that season's replacement level).** Note this is the top 3 by PRICE, i.e.
what the league thought was a star, not the top 3 by production. That is the
right instrument for the question: it prices the stars the room bid on.

CONFOUNDS -- stated, not solved
-------------------------------
Good managers may both concentrate and win for other reasons; 98 team-seasons
across four league sizes is thin; a strategy that works at 12 teams may not at
15. Nothing here is fitted. Quartiles and means, with n beside every number.

Report only. Nothing under app/ changes.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

COVID_SEASON = 2020
SEASONS = "ls.season BETWEEN 2019 AND 2026"
REPORT_PATH = Path("reports/top_heavy.md")

#: Nine-category composite, identical to the one in waiver_value.py and
#: season_report.py. Turnovers subtracted, percentages excluded.
NINE_CAT = (
    "pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks "
    "+ pgs.three_pointers_made - pgs.turnovers"
)

#: How many of a team's most expensive picks define its "stars".
TOP_N_PICKS = 3

#: Percentile of started-player production used as replacement level.
REPLACEMENT_PERCENTILE = 0.25

TEAM_SIZES: tuple[int, ...] = (10, 12, 14, 16)


@dataclass(frozen=True)
class TeamSeason:
    """One team's draft and outcome in one season."""

    season: int
    team_count: int
    team_id: int
    name: str
    spent: int
    top1_share: float
    top3_share: float
    hhi: float
    exec_share: float
    cat_win_rate: float
    final_standing: int
    made_playoffs: bool
    won_title: bool


@dataclass(frozen=True)
class Replacement:
    season: int
    team_count: int
    value: float


#: Every team-season's draft spend, concentration, and realised outcome.
#: Production is from STARTED lineup rows only, joined to the game line so a
#: started player who did not play contributes nothing.
TEAM_SEASONS_SQL = f"""
WITH pick_totals AS (
  SELECT dp.league_season_id, dp.team_id,
         sum(dp.bid_amount) AS spent,
         count(*) AS picks,
         array_agg(dp.player_id ORDER BY dp.bid_amount DESC, dp.player_id) AS by_price
  FROM draft_picks dp
  WHERE dp.bid_amount IS NOT NULL
  GROUP BY dp.league_season_id, dp.team_id
),
started AS (
  SELECT ls.season, dls.team_id, dls.player_id,
         sum({NINE_CAT}) AS ninecat
  FROM daily_lineup_slots dls
  JOIN teams t ON t.id = dls.team_id
  JOIN league_seasons ls ON ls.id = t.league_season_id
  JOIN player_game_stats pgs
    ON pgs.player_id = dls.player_id
   AND pgs.scoring_period = dls.scoring_period
   AND pgs.season = ls.season
  WHERE dls.started AND pgs.played AND {SEASONS}
  GROUP BY ls.season, dls.team_id, dls.player_id
),
team_prod AS (
  SELECT season, team_id, sum(ninecat) AS total_ninecat
  FROM started GROUP BY season, team_id
),
star_prod AS (
  SELECT ls.season, s.team_id, sum(s.ninecat) AS star_ninecat
  FROM started s
  JOIN league_seasons ls ON ls.season = s.season
  JOIN pick_totals pt ON pt.team_id = s.team_id
   AND pt.league_season_id = ls.id
  WHERE s.player_id = ANY(pt.by_price[1:{TOP_N_PICKS}])
  GROUP BY ls.season, s.team_id
)
SELECT ls.season, ls.team_count, t.id AS team_id, t.name,
       pt.spent, pt.by_price,
       COALESCE(tp.total_ninecat, 0) AS total_ninecat,
       COALESCE(sp.star_ninecat, 0) AS star_ninecat,
       t.categories_won, t.categories_lost, t.categories_tied,
       t.final_standing, ls.playoff_team_count
FROM teams t
JOIN league_seasons ls ON ls.id = t.league_season_id
JOIN pick_totals pt ON pt.team_id = t.id AND pt.league_season_id = ls.id
LEFT JOIN team_prod tp ON tp.season = ls.season AND tp.team_id = t.id
LEFT JOIN star_prod sp ON sp.season = ls.season AND sp.team_id = t.id
WHERE {SEASONS}
ORDER BY ls.season, t.final_standing
"""

#: Replacement level per season: a percentile of started-player production.
REPLACEMENT_SQL = f"""
WITH started AS (
  SELECT ls.season, ls.team_count, dls.player_id,
         sum({NINE_CAT}) AS ninecat
  FROM daily_lineup_slots dls
  JOIN teams t ON t.id = dls.team_id
  JOIN league_seasons ls ON ls.id = t.league_season_id
  JOIN player_game_stats pgs
    ON pgs.player_id = dls.player_id
   AND pgs.scoring_period = dls.scoring_period
   AND pgs.season = ls.season
  WHERE dls.started AND pgs.played AND {SEASONS}
  GROUP BY ls.season, ls.team_count, dls.player_id
)
SELECT season, team_count,
       percentile_cont({REPLACEMENT_PERCENTILE}) WITHIN GROUP (
         ORDER BY ninecat
       ) AS replacement
FROM started GROUP BY season, team_count ORDER BY season
"""

#: Production of the season's top-3-most-expensive picks, and of a whole team.
STAR_PRODUCTION_SQL = f"""
WITH pick_totals AS (
  SELECT dp.league_season_id, dp.team_id,
         array_agg(dp.player_id ORDER BY dp.bid_amount DESC, dp.player_id) AS by_price
  FROM draft_picks dp WHERE dp.bid_amount IS NOT NULL
  GROUP BY dp.league_season_id, dp.team_id
),
started AS (
  SELECT ls.season, dls.player_id, sum({NINE_CAT}) AS ninecat
  FROM daily_lineup_slots dls
  JOIN teams t ON t.id = dls.team_id
  JOIN league_seasons ls ON ls.id = t.league_season_id
  JOIN player_game_stats pgs
    ON pgs.player_id = dls.player_id
   AND pgs.scoring_period = dls.scoring_period
   AND pgs.season = ls.season
  WHERE dls.started AND pgs.played AND {SEASONS}
  GROUP BY ls.season, dls.player_id
)
SELECT ls.season, ls.team_count, pt.team_id,
       sum(s.ninecat) AS top3_ninecat
FROM pick_totals pt
JOIN league_seasons ls ON ls.id = pt.league_season_id
JOIN started s ON s.season = ls.season
  AND s.player_id = ANY(pt.by_price[1:{TOP_N_PICKS}])
GROUP BY ls.season, ls.team_count, pt.team_id
ORDER BY ls.season, ls.team_count
"""

#: Q3 from waiver_value.py, re-used: best available vs the median rostered.
Q3_SQL = f"""
WITH fa_day AS (
  SELECT ls.season, ls.team_count, pgs.scoring_period,
         max({NINE_CAT}) AS best_comp
  FROM player_game_stats pgs
  JOIN league_seasons ls ON ls.season = pgs.season
  WHERE pgs.played AND pgs.minutes > 0 AND {SEASONS}
    AND NOT EXISTS (
      SELECT 1 FROM daily_lineup_slots dls
      WHERE dls.player_id = pgs.player_id
        AND dls.scoring_period = pgs.scoring_period
        AND dls.team_id IN (
          SELECT t2.id FROM teams t2
          JOIN league_seasons l2 ON l2.id = t2.league_season_id
          WHERE l2.season = ls.season
        )
    )
  GROUP BY ls.season, ls.team_count, pgs.scoring_period
),
roster_day AS (
  SELECT ls.season, ls.team_count, dls.scoring_period,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY {NINE_CAT}) AS median_comp
  FROM daily_lineup_slots dls
  JOIN teams t ON t.id = dls.team_id
  JOIN league_seasons ls ON ls.id = t.league_season_id
  JOIN player_game_stats pgs
    ON pgs.player_id = dls.player_id AND pgs.season = ls.season
   AND pgs.scoring_period = dls.scoring_period
   AND pgs.played AND pgs.minutes > 0
  WHERE {SEASONS}
  GROUP BY ls.season, ls.team_count, dls.scoring_period
)
SELECT f.team_count, count(DISTINCT f.season) AS seasons,
       AVG(f.best_comp) AS best_comp,
       AVG(f.best_comp - r.median_comp) AS edge
FROM fa_day f
JOIN roster_day r ON r.season = f.season
                 AND r.scoring_period = f.scoring_period
GROUP BY f.team_count ORDER BY f.team_count
"""


def _quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    """Cut points at the 25th and 75th percentile (linear interpolation)."""
    ordered = sorted(values)
    if len(ordered) < 4:
        span = max(ordered) - min(ordered) if ordered else 0.0
        return (min(ordered), min(ordered) + span / 3, min(ordered) + 2 * span / 3)
    return (
        statistics.quantiles(ordered, n=4, method="inclusive")[0],
        statistics.quantiles(ordered, n=4, method="inclusive")[1],
        statistics.quantiles(ordered, n=4, method="inclusive")[2],
    )


def _key(r: TeamSeason) -> tuple[int, int]:
    """Team ids are season-scoped, so a team-season is (season, team_id).

    Measured: all 98 teams currently have distinct ids, so this makes no
    difference today. It is keyed this way so it stays correct if that changes.
    """
    return (r.season, r.team_id)


def _label_quartiles(
    rows: Sequence[TeamSeason], key: str
) -> dict[tuple[int, int], str]:
    """Label each team-season's quartile PER TEAM COUNT, then pool.

    Cutting inside each team count keeps league size from driving the ranking:
    a 16-team season spreads $200 over more players, so its shares are
    systematically lower and would otherwise fill the balanced quartile.
    """
    labels: dict[tuple[int, int], str] = {}
    for size in TEAM_SIZES:
        group = [r for r in rows if r.team_count == size]
        if len(group) < 4:
            continue
        values = [getattr(r, key) for r in group]
        q1, q2, q3 = _quartiles(values)
        for r in group:
            v = getattr(r, key)
            if v <= q1:
                labels[_key(r)] = "Q1 balanced"
            elif v <= q2:
                labels[_key(r)] = "Q2"
            elif v < q3:
                labels[_key(r)] = "Q3"
            else:
                labels[_key(r)] = "Q4 top-heavy"
    return labels


QUARTILE_ORDER = ("Q1 balanced", "Q2", "Q3", "Q4 top-heavy")


def _quartile_table(
    rows: Sequence[TeamSeason],
    labels: dict[tuple[int, int], str],
    label_name: str,
) -> list[str]:
    """Mean outcome per quartile, with n."""
    lines = [
        f"| {label_name:<13} | {'n':>3} | {'cat win rate':>12} "
        f"| {'mean finish':>11} | {'playoff %':>9} | {'title %':>8} "
        f"| {'mean top3 share':>15} |",
        f"|{'-' * 15}|{'-' * 5}|{'-' * 14}|{'-' * 13}|{'-' * 11}|{'-' * 10}"
        f"|{'-' * 17}|",
    ]
    for label in QUARTILE_ORDER:
        group = [r for r in rows if labels.get(_key(r)) == label]
        if not group:
            continue
        n = len(group)
        win = statistics.fmean(r.cat_win_rate for r in group)
        fin = statistics.fmean(r.final_standing for r in group)
        play = 100.0 * sum(1 for r in group if r.made_playoffs) / n
        title = 100.0 * sum(1 for r in group if r.won_title) / n
        top3 = statistics.fmean(r.top3_share for r in group)
        lines.append(
            f"| {label:<13} | {n:>3} | {win:>12.3f} | {fin:>11.2f} "
            f"| {play:>8.0f}% | {title:>7.0f}% | {top3:>15.1%} |"
        )
    return lines


def _spread_table(
    rows: Sequence[TeamSeason], labels: dict[tuple[int, int], str]
) -> list[str]:
    """Spread of category win rate within each quartile, not just the mean."""
    lines = [
        f"| {'quartile':<13} | {'n':>3} | {'mean':>6} | {'min':>6} | {'max':>6} "
        f"| {'range':>6} | {'stdev':>6} |",
        f"|{'-' * 15}|{'-' * 5}|{'-' * 8}|{'-' * 8}|{'-' * 8}|{'-' * 8}"
        f"|{'-' * 8}|",
    ]
    for label in QUARTILE_ORDER:
        group = [r for r in rows if labels.get(_key(r)) == label]
        if len(group) < 2:
            continue
        values = [r.cat_win_rate for r in group]
        lines.append(
            f"| {label:<13} | {len(group):>3} | {statistics.fmean(values):>6.3f} "
            f"| {min(values):>6.3f} | {max(values):>6.3f} "
            f"| {max(values) - min(values):>6.3f} "
            f"| {statistics.stdev(values):>6.3f} |"
        )
    return lines


def load_team_seasons(conn: psycopg.Connection) -> list[TeamSeason]:
    with conn.cursor() as cur:
        cur.execute(TEAM_SEASONS_SQL)
        raw = cur.fetchall()

    out: list[TeamSeason] = []
    for (
        season, team_count, team_id, name, spent, by_price,
        total_ninecat, star_ninecat,
        cw, cl, ct, final_standing, playoff_count,
    ) in raw:
        prices = _prices_from(conn, season, team_id)
        spent_int = int(spent)
        ordered = sorted(prices, reverse=True)
        top1 = ordered[0] / spent_int if spent_int and ordered else 0.0
        top3 = sum(ordered[:TOP_N_PICKS]) / spent_int if spent_int else 0.0
        hhi = sum((p / spent_int) ** 2 for p in prices) if spent_int else 0.0
        total_prod = float(total_ninecat or 0.0)
        exec_share = (
            float(star_ninecat or 0.0) / total_prod if total_prod > 0 else 0.0
        )
        decided = int(cw) + int(cl) + int(ct)
        out.append(
            TeamSeason(
                season=int(season),
                team_count=int(team_count),
                team_id=int(team_id),
                name=str(name),
                spent=spent_int,
                top1_share=top1,
                top3_share=top3,
                hhi=hhi,
                exec_share=exec_share,
                cat_win_rate=(int(cw) / decided) if decided else 0.0,
                final_standing=int(final_standing),
                made_playoffs=int(final_standing) <= int(playoff_count),
                won_title=int(final_standing) == 1,
            )
        )
        _ = by_price
    return out


_PRICE_CACHE: dict[tuple[int, int], list[int]] = {}


def _prices_from(conn: psycopg.Connection, season: int, team_id: int) -> list[int]:
    """All draft prices for one team-season, in one round trip.

    The whole table is fetched once and cached, rather than issuing a query per
    team-season: there are 98 of them and the table is 1,274 rows.
    """
    if not _PRICE_CACHE:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ls.season, dp.team_id, dp.bid_amount
                FROM draft_picks dp
                JOIN league_seasons ls ON ls.id = dp.league_season_id
                WHERE dp.bid_amount IS NOT NULL
                """
            )
            for row_season, row_team, amount in cur.fetchall():
                _PRICE_CACHE.setdefault(
                    (int(row_season), int(row_team)), []
                ).append(int(amount))
    return _PRICE_CACHE.get((season, team_id), [])


def load_replacements(conn: psycopg.Connection) -> list[Replacement]:
    with conn.cursor() as cur:
        cur.execute(REPLACEMENT_SQL)
        return [
            Replacement(int(s), int(tc), float(v)) for s, tc, v in cur.fetchall()
        ]


def load_star_surplus(conn: psycopg.Connection) -> dict[tuple[int, int], float]:
    """Star surplus per (team_count, season): top-3 production - 3 x replacement."""
    replacements = {(r.season, r.team_count): r.value for r in load_replacements(conn)}
    with conn.cursor() as cur:
        cur.execute(STAR_PRODUCTION_SQL)
        rows = cur.fetchall()
    out: dict[tuple[int, int], float] = {}
    for season, team_count, _team_id, top3 in rows:
        rep = replacements.get((int(season), int(team_count)))
        if rep is None:
            continue
        out[(int(team_count), int(season))] = float(top3) - TOP_N_PICKS * rep
    return out


def report(
    rows: Sequence[TeamSeason],
    replacements: Sequence[Replacement],
    surplus: dict[tuple[int, int], float],
    q3: Sequence[tuple[int, int, float, float]],
) -> str:
    out: list[str] = []
    add = out.append
    strat = _label_quartiles(rows, "top3_share")
    execq = _label_quartiles(rows, "exec_share")

    add("# Does a top-heavy draft pay off?")
    add("")
    add("What the league's own history says about concentrating draft money")
    add("in a few players, and whether league size changes it. Report only.")
    add("")
    add("**STRATEGY** is how money was spent at the draft (chosen in advance).")
    add("**EXECUTION** is how concentrated production turned out (not chosen).")
    add("They are deliberately kept apart.")
    add("")
    n_total = len(rows)
    add(f"Team-seasons: **{n_total}**. Quartiles are cut WITHIN each team count")
    add("and then pooled, so league size cannot drive the ranking.")
    add("")

    add("## 1. STRATEGY quartiles by top-3 share of draft spend")
    add("")
    add("### Pooled, all team counts")
    add("")
    out.extend(_quartile_table(rows, strat, "strategy"))
    add("")
    add("### Split by team count")
    add("")
    for size in TEAM_SIZES:
        group = [r for r in rows if r.team_count == size]
        if not group:
            continue
        seasons = sorted({r.season for r in group})
        local = _label_quartiles(group, "top3_share")
        add(f"**{size} teams** — seasons {', '.join(str(s) for s in seasons)}, "
            f"n={len(group)}")
        add("")
        out.extend(_quartile_table(group, local, "strategy"))
        add("")

    add("## 2. EXECUTION quartiles by share of production from the top 3 picks")
    add("")
    add("### Pooled, all team counts")
    add("")
    out.extend(_quartile_table(rows, execq, "execution"))
    add("")
    add("### Split by team count")
    add("")
    for size in TEAM_SIZES:
        group = [r for r in rows if r.team_count == size]
        if not group:
            continue
        seasons = sorted({r.season for r in group})
        local = _label_quartiles(group, "exec_share")
        add(f"**{size} teams** — seasons {', '.join(str(s) for s in seasons)}, "
            f"n={len(group)}")
        add("")
        out.extend(_quartile_table(group, local, "execution"))
        add("")

    add("## 3. STRATEGY vs EXECUTION: the bust rate of going top-heavy")
    add("")
    add("Of the teams that CHOSE to be in the top strategy quartile, how many")
    add("ended up in the top execution quartile? The gap is the injury and bust")
    add("rate -- teams that concentrated money and did not get concentrated")
    add("production out of it.")
    add("")
    top_strat = [r for r in rows if strat.get(_key(r)) == "Q4 top-heavy"]
    top_exec = {_key(r) for r in rows if execq.get(_key(r)) == "Q4 top-heavy"}
    if top_strat:
        landed = [r for r in top_strat if _key(r) in top_exec]
        add(f"- Chose top-heavy: **{len(top_strat)}**")
        add(f"- Of those, landed in top execution: **{len(landed)}** "
            f"({100.0 * len(landed) / len(top_strat):.0f}%)")
        add(f"- **Bust rate (chose top-heavy, did not get it): "
            f"{100.0 * (len(top_strat) - len(landed)) / len(top_strat):.0f}%**")
        add("")
        missed_playoffs = [r for r in top_strat if not r.made_playoffs]
        add(f"Of the {len(top_strat)} top-heavy choosers, "
            f"{len(missed_playoffs)} missed the playoffs.")
        titles = [r for r in top_strat if r.won_title]
        add(f"Titles won by top-heavy choosers: {len(titles)}.")
        if landed:
            lw = statistics.fmean(r.cat_win_rate for r in landed)
            mw = statistics.fmean(r.cat_win_rate for r in top_strat
                                  if _key(r) not in top_exec)
            add(f"Mean category win rate when the plan worked: **{lw:.3f}**")
            add(f"Mean category win rate when it did not: **{mw:.3f}**")
    add("")

    add("## 4. VARIANCE: spread of category win rate within each quartile")
    add("")
    add("A strategy can have the same average and a much wider range. A title")
    add("needs the top of the range; a wooden spoon is the bottom. Strategy")
    add("quartiles first, then execution.")
    add("")
    add("### By strategy quartile")
    add("")
    out.extend(_spread_table(rows, strat))
    add("")
    add("### By execution quartile")
    add("")
    out.extend(_spread_table(rows, execq))
    add("")

    add("## 5. Replacement level and star surplus by team count")
    add("")
    add(f"Replacement level is the {REPLACEMENT_PERCENTILE:.0%} percentile of")
    add("started-player season production within each season -- a marginal")
    add("starter. Star surplus is the season's top-3-most-expensive picks'")
    add(f"production minus {TOP_N_PICKS} x that replacement level. If stars are")
    add("worth more in a thinner pool, surplus should rise with team count.")
    add("")
    add("| teams | seasons | replacement (mean) | star surplus (mean) "
        "| surplus per star |")
    add("|---|---|---|---|---|")
    for size in TEAM_SIZES:
        reps = [r.value for r in replacements if r.team_count == size]
        sur = [v for (tc, _s), v in surplus.items() if tc == size]
        if not reps or not sur:
            continue
        n_seasons = len({r.season for r in replacements if r.team_count == size})
        mean_rep = statistics.fmean(reps)
        mean_sur = statistics.fmean(sur)
        add(f"| {size} | {n_seasons} | {mean_rep:>0.0f} | {mean_sur:>0.0f} "
            f"| {mean_sur / TOP_N_PICKS:>0.0f} |")
    add("")

    add("## 6. Named tables: 2023 (16 teams) and 2026 (14 teams)")
    add("")
    add("Every team, so the numbers can be checked against memory.")
    add("")
    for season in (2023, 2026):
        group = sorted(
            (r for r in rows if r.season == season),
            key=lambda r: r.final_standing,
        )
        if not group:
            continue
        add(f"### {season} — {group[0].team_count} teams")
        add("")
        add("| finish | team | top1 share | top3 share | HHI | exec share "
            "| cat win rate | played | title |")
        add("|---|---|---|---|---|---|---|---|---|")
        for r in group:
            add(
                f"| {r.final_standing} | {r.name} | {r.top1_share:.1%} "
                f"| {r.top3_share:.1%} | {r.hhi:.3f} | {r.exec_share:.1%} "
                f"| {r.cat_win_rate:.3f} | {'yes' if r.made_playoffs else 'no'} "
                f"| {'YES' if r.won_title else ''} |"
            )
        add("")

    add("## 7. Q3 re-used: available production by league size")
    add("")
    add("From `scripts/waiver_value.py`, repeated here so the star-surplus")
    add("reading has its context: the best unrostered player against the median")
    add("rostered player, per scoring period.")
    add("")
    add("| teams | seasons | best comp/day | edge vs median |")
    add("|---|---|---|---|")
    for q3_teams, q3_seasons, q3_best, q3_edge in q3:
        add(f"| {q3_teams} | {q3_seasons} | {q3_best:.1f} | {q3_edge:.1f} |")
    add("")
    return "\n".join(out)


def main() -> None:
    with psycopg.connect(DSN) as conn:
        rows = load_team_seasons(conn)
        replacements = load_replacements(conn)
        surplus = load_star_surplus(conn)
        with conn.cursor() as cur:
            cur.execute(Q3_SQL)
            q3 = [(int(a), int(b), float(c), float(d)) for a, b, c, d in cur.fetchall()]
        text = report(rows, replacements, surplus, q3)
    print(text)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"\nwritten to {REPORT_PATH}")


if __name__ == "__main__":
    main()
