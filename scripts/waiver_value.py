#!/usr/bin/env python3
"""Measure what the waiver wire is worth in the FCP league (ESPN 3853870).

Tests the draft optimizer's core assumption: that replacement level -- "the
last man rostered" -- is adequately derived from the draft board. If good
production sits unrostered all season, replacement level is higher than the
draft board implies, auction values are inflated, and FAAB should be hoarded.

Four questions:
  Q1  Best production available unrostered, per scoring period.
  Q2  Best available versus the weakest player actually rostered that day.
  Q3  Does that gap move with league size (10/12/14/16 teams)?
  Q4  What share of a season's team production came from post-draft pickups?

DATA MODEL -- the one thing that must be right
----------------------------------------------
`daily_lineup_slots` (DLS) and `player_game_stats` (PGS) both key on
`scoring_period`, and the join between them is VALID but easy to misread as
broken. The match rate is only 34-53%, which looks like a failure and is not:
a rostered player records a line only on days his NBA team plays, roughly 45%
of days.

The check that distinguishes "healthy partial match" from "misaligned join" is
the SHAPE of the per-period match rate. Measured for 2025 it runs
15% / 62% / 28% / 62% / 61% / 34% / 71% ... -- alternating heavy and light,
which is the NBA schedule (Mon-Wed-Fri slates against Tue-Thu), with one period
at exactly 0% (the All-Star break). A misaligned join scrambles uniformly
instead. Verified with a per-player control too: 87-100% of Shai
Gilgeous-Alexander's games land on days he was held.

`game_date` is NOT a usable alternative anchor: it is a timestamp spanning two
calendar dates per period, and 770-4,996 rows per season have it NULL while
still carrying real production.

Traps handled, all verified rather than assumed:
  * `daily_lineup_slots.injury_status`/`.injured` are NOT a time series. ESPN
    returns one status as of the request. Trae Young carries exactly 2 distinct
    values across 244 roster-days; Julius Randle is flagged on 557 days and
    played 557 with 18,447 minutes. Never used here.
  * `roster_slots` (weekly) aggregates a whole matchup period and includes
    players added and dropped inside it. Not used; DLS is point-in-time.
  * Period lengths differ (6-day opener, 14-day All-Star, and in 2020 a
    98-period COVID block covering 10 real days). All reporting is per scoring
    period, so lengths are never pooled.
  * Counting stats fall as league size rises (shared pool). Nothing is pooled
    across team counts without splitting by team count.
  * 2020 is the suspended COVID season; flagged in every table.
  * `statistics.median`, never index arithmetic: every season has an even team
    count, which biases `values[len//2]` upward.
"""

from __future__ import annotations

import os
import statistics

import psycopg

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

COVID_SEASON = 2020
SEASONS = "ls.season BETWEEN 2019 AND 2026"

#: The nine-category composite. Equal weight on each counting category,
#: turnovers subtracted because fewer is better, percentages excluded because
#: adding a ratio to a count is a category error.
#:
#: This is NOT the league's scoring. The league awards nine separate category
#: wins, so a low-turnover 12/5/3 can beat a high-turnover 30/8/8. The
#: composite is a ranking aid; raw points are reported alongside it everywhere.
COMPOSITE = (
    "pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks "
    "+ pgs.three_pointers_made - pgs.turnovers"
)

#: A free agent on day N: recorded a line that day and appears in no
#: `daily_lineup_slots` row for the same season and scoring period.
FREE_AGENT_PREDICATE = """
    NOT EXISTS (
      SELECT 1 FROM daily_lineup_slots dls
      JOIN teams t ON t.id = dls.team_id
      JOIN league_seasons l2 ON l2.id = t.league_season_id
      WHERE l2.season = pgs.season
        AND dls.scoring_period = pgs.scoring_period
        AND dls.player_id = pgs.player_id
    )
"""


def join_shape(conn: psycopg.Connection) -> None:
    """Show the per-period match rate, the evidence the join is sound."""
    print("=" * 78)
    print("JOIN AUDIT: IS THE DLS/PGS scoring_period ALIGNMENT SOUND?")
    print("=" * 78)
    print()
    print("A rostered player has a line only on days his team plays (~45%).")
    print("So a partial match is expected. A MISALIGNED join would scramble")
    print("uniformly; a sound one tracks the NBA schedule as alternating")
    print("heavy/light days. That alternation is the test.")
    print()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ls.season,
                   count(*) AS dls_rows,
                   count(*) FILTER (WHERE pgs.id IS NOT NULL) AS has_line
            FROM daily_lineup_slots dls
            JOIN teams t ON t.id = dls.team_id
            JOIN league_seasons ls ON ls.id = t.league_season_id
            LEFT JOIN player_game_stats pgs
                   ON pgs.player_id = dls.player_id
                  AND pgs.season = ls.season
                  AND pgs.scoring_period = dls.scoring_period
                  AND pgs.played AND pgs.minutes > 0
            WHERE {SEASONS}
            GROUP BY ls.season ORDER BY ls.season
            """
        )
        rows = cur.fetchall()

    print(f"{'season':>7} {'dls rows':>10} {'matched':>9} {'match%':>8}")
    print("-" * 37)
    for season, total, matched in rows:
        total_i, matched_i = int(total), int(matched)
        pct = 100.0 * matched_i / total_i if total_i else 0.0
        print(f"{season:>7} {total_i:>10} {matched_i:>9} {pct:>7.1f}%")

    #: The shape test, on the most recent completed season.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT dls.scoring_period,
                   count(DISTINCT dls.player_id) AS held,
                   count(DISTINCT pgs.player_id) AS held_and_played
            FROM daily_lineup_slots dls
            JOIN teams t ON t.id = dls.team_id
            JOIN league_seasons ls ON ls.id = t.league_season_id
            LEFT JOIN player_game_stats pgs
                   ON pgs.player_id = dls.player_id
                  AND pgs.season = ls.season
                  AND pgs.scoring_period = dls.scoring_period
                  AND pgs.played AND pgs.minutes > 0
            WHERE ls.season = 2025
            GROUP BY dls.scoring_period
            ORDER BY dls.scoring_period
            LIMIT 16
            """
        )
        shape = cur.fetchall()

    print()
    print("Per-period match rate, 2025, first 16 periods. Expect alternating")
    print("heavy and light days, not a flat scramble:")
    rates = []
    for _period, held, matched in shape:
        held_i = int(held)
        pct = 100.0 * int(matched) / held_i if held_i else 0.0
        rates.append(pct)
    print("  " + "  ".join(f"{r:5.1f}%" for r in rates))

    #: A sound join alternates: successive periods differ a lot, and the
    #: spread is wide. A scrambled join clusters tightly around the mean.
    if len(rates) > 3:
        spread = statistics.pstdev(rates)
        deltas = [abs(rates[i] - rates[i - 1]) for i in range(1, len(rates))]
        print(f"  std dev {spread:.1f}, mean successive change {statistics.mean(deltas):.1f}")
        print("  A wide spread with large successive changes indicates the join")
        print("  tracks the schedule. A narrow spread would indicate drift.")


def q1_best_available(conn: psycopg.Connection) -> None:
    """Q1: best production available unrostered, per scoring period."""
    print()
    print("=" * 78)
    print("Q1. BEST PRODUCTION AVAILABLE UNROSTERED, PER SCORING PERIOD")
    print("=" * 78)
    print()
    print("The single best free agent each day. Per-day values averaged over")
    print("the season. Uses the median as well as the mean, because a handful")
    print("of monster lines can drag a mean.")
    print()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH fa AS (
              SELECT ls.season, ls.team_count, pgs.scoring_period,
                     pgs.points, {COMPOSITE} AS comp, pgs.minutes
              FROM player_game_stats pgs
              JOIN league_seasons ls ON ls.season = pgs.season
              WHERE pgs.played AND pgs.minutes > 0 AND {SEASONS}
                AND {FREE_AGENT_PREDICATE}
            ),
            per_day AS (
              SELECT season, team_count, scoring_period,
                     count(*) AS n_fa, max(points) AS best_pts,
                     max(comp) AS best_comp
              FROM fa GROUP BY season, team_count, scoring_period
            )
            SELECT season, team_count, count(*) AS days,
                   AVG(n_fa) AS avg_fa,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY best_pts) AS med_pts,
                   AVG(best_pts) AS avg_pts,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY best_comp) AS med_comp,
                   AVG(best_comp) AS avg_comp,
                   max(best_pts) AS max_pts
            FROM per_day GROUP BY season, team_count ORDER BY season
            """
        )
        rows = cur.fetchall()

    header = (
        f"{'season':>7} {'teams':>6} {'days':>6} {'FA/day':>7} "
        f"{'best PTS':>9} {'med PTS':>8} {'best COMP':>10} "
        f"{'med COMP':>9} {'max PTS':>8}"
    )
    print(header)
    print("-" * len(header))
    for season, teams, days, avg_fa, med_pts, avg_pts, med_comp, avg_comp, max_pts in rows:
        flag = "  (COVID)" if season == COVID_SEASON else ""
        print(
            f"{season:>7} {teams:>6} {int(days):>6} {float(avg_fa):>7.0f} "
            f"{float(avg_pts):>9.1f} {float(med_pts):>8.1f} "
            f"{float(avg_comp):>10.1f} {float(med_comp):>9.1f} "
            f"{int(max_pts):>8}{flag}"
        )


def q2_replacement_gap(conn: psycopg.Connection) -> None:
    """Q2: best available versus the weakest rostered player.

    Three cuts, because 'weakest rostered' is ambiguous and the choice drives
    the answer more than the data does:
      worst      -- lowest composite among rostered players who recorded a line
      low minutes-- the least-used rostered player (who should not hold a spot)
      median     -- the typical rostered player, which is the fairest bar for
                    a replacement-level threshold
    """
    print()
    print("=" * 78)
    print("Q2. BEST AVAILABLE VERSUS THE WEAKEST PLAYER ACTUALLY ROSTERED")
    print("=" * 78)
    print()
    print("Three baselines, because the answer depends on which one is meant:")
    print("  worst   - lowest composite of any rostered player who played")
    print("  low-min - the least-used rostered player, the man whose roster spot")
    print("            a pickup would actually take")
    print("  median  - the typical rostered player. This is the honest bar for a")
    print("            replacement-level threshold.")
    print()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH fa_day AS (
              SELECT ls.season, pgs.scoring_period, max({COMPOSITE}) AS best_fa
              FROM player_game_stats pgs
              JOIN league_seasons ls ON ls.season = pgs.season
              WHERE pgs.played AND pgs.minutes > 0 AND {SEASONS}
                AND {FREE_AGENT_PREDICATE}
              GROUP BY ls.season, pgs.scoring_period
            ),
            roster AS (
              SELECT ls.season, ls.team_count, dls.scoring_period,
                     {COMPOSITE} AS comp, pgs.minutes
              FROM daily_lineup_slots dls
              JOIN teams t ON t.id = dls.team_id
              JOIN league_seasons ls ON ls.id = t.league_season_id
              JOIN player_game_stats pgs
                ON pgs.player_id = dls.player_id AND pgs.season = ls.season
               AND pgs.scoring_period = dls.scoring_period
               AND pgs.played AND pgs.minutes > 0
              WHERE {SEASONS}
            ),
            low_min AS (
              SELECT DISTINCT ON (season, scoring_period)
                     season, scoring_period, comp AS low_min_comp
              FROM roster
              ORDER BY season, scoring_period, minutes ASC, comp ASC
            ),
            roster_day AS (
              SELECT r.season, r.team_count, r.scoring_period,
                     min(r.comp) AS worst_comp,
                     percentile_cont(0.5) WITHIN GROUP (ORDER BY r.comp)
                       AS median_comp,
                     max(l.low_min_comp) AS low_min_comp
              FROM roster r
              JOIN low_min l ON l.season = r.season
                            AND l.scoring_period = r.scoring_period
              GROUP BY r.season, r.team_count, r.scoring_period
            )
            SELECT f.season, r.team_count, count(*) AS days,
                   AVG(f.best_fa - r.worst_comp) AS edge_worst,
                   AVG(f.best_fa - r.low_min_comp) AS edge_lowmin,
                   AVG(f.best_fa - r.median_comp) AS edge_median,
                   AVG(f.best_fa) AS best_fa,
                   AVG(r.median_comp) AS median_rostered
            FROM fa_day f
            JOIN roster_day r ON r.season = f.season
                             AND r.scoring_period = f.scoring_period
            GROUP BY f.season, r.team_count ORDER BY f.season
            """
        )
        rows = cur.fetchall()

    header = (
        f"{'season':>7} {'teams':>6} {'days':>6} {'best FA':>8} "
        f"{'median rost':>12} {'vs worst':>9} {'vs low-min':>11} "
        f"{'vs median':>10}"
    )
    print(header)
    print("-" * len(header))
    for season, teams, days, e_worst, e_lowmin, e_median, best_fa, median_rost in rows:
        flag = "  (COVID)" if season == COVID_SEASON else ""
        print(
            f"{season:>7} {teams:>6} {int(days):>6} {float(best_fa):>8.1f} "
            f"{float(median_rost):>12.1f} {float(e_worst):>9.1f} "
            f"{float(e_lowmin):>11.1f} {float(e_median):>10.1f}{flag}"
        )


def q3_by_league_size(conn: psycopg.Connection) -> None:
    """Q3: does the available production change with league size?

    Counting stats fall as team count rises because more rosters share one
    player pool, so a decline is expected and is NOT evidence about the wire.
    What matters is whether the gap to the replacement bar survives.
    """
    print()
    print("=" * 78)
    print("Q3. DOES THE GAP CHANGE WITH LEAGUE SIZE?")
    print("=" * 78)
    print()
    print("Grouped by team count. A falling best-available is EXPECTED as pools")
    print("are shared wider; the question is whether the edge over replacement")
    print("survives. Per-team normalised columns are included for that reason.")
    print()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH fa_day AS (
              SELECT ls.season, ls.team_count, pgs.scoring_period,
                     max({COMPOSITE}) AS best_comp,
                     max(pgs.points) AS best_pts
              FROM player_game_stats pgs
              JOIN league_seasons ls ON ls.season = pgs.season
              WHERE pgs.played AND pgs.minutes > 0 AND {SEASONS}
                AND {FREE_AGENT_PREDICATE}
              GROUP BY ls.season, ls.team_count, pgs.scoring_period
            ),
            roster_day AS (
              SELECT ls.season, ls.team_count, dls.scoring_period,
                     percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY {COMPOSITE}
                     ) AS median_comp,
                     count(DISTINCT dls.team_id) AS teams_seen
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
                   AVG(f.best_pts) AS best_pts,
                   AVG(f.best_comp - r.median_comp) AS edge
            FROM fa_day f
            JOIN roster_day r ON r.season = f.season
                             AND r.scoring_period = f.scoring_period
            GROUP BY f.team_count ORDER BY f.team_count
            """
        )
        rows = cur.fetchall()

    header = (
        f"{'teams':>6} {'seasons':>8} {'best PTS/day':>13} "
        f"{'best COMP/day':>14} {'edge vs median':>15}"
    )
    print(header)
    print("-" * len(header))
    for teams, seasons, best_comp, best_pts, edge in rows:
        print(
            f"{int(teams):>6} {int(seasons):>8} {float(best_pts):>13.1f} "
            f"{float(best_comp):>14.1f} {float(edge):>15.1f}"
        )


def q4_post_draft_share(conn: psycopg.Connection) -> None:
    """Q4: share of roster-days held by drafted versus acquired players."""
    print()
    print("=" * 78)
    print("Q4. SHARE OF ROSTER-DAYS FROM POST-DRAFT ACQUISITION")
    print("=" * 78)
    print()
    print("One roster-day is one (team, player, day) a team held. This measures")
    print("OCCUPANCY, not production: a drafted star and a waiver fill-in each")
    print("count one. So it understates the drafted share of production and")
    print("overstates the acquired share. It is exact on what it claims, and")
    print("needs only daily_lineup_slots and draft_picks.")
    print()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH held AS (
              SELECT ls.season, ls.team_count, dls.team_id, dls.player_id,
                     count(DISTINCT dls.scoring_period) AS days_held,
                     count(DISTINCT dls.team_id) AS n_teams
              FROM daily_lineup_slots dls
              JOIN teams t ON t.id = dls.team_id
              JOIN league_seasons ls ON ls.id = t.league_season_id
              WHERE {SEASONS}
              GROUP BY ls.season, ls.team_count, dls.team_id, dls.player_id
            )
            SELECT h.season, h.team_count,
                   SUM(h.days_held) AS slot_days,
                   SUM(h.days_held) FILTER (WHERE dp.player_id IS NOT NULL)
                     AS drafted_days
            FROM held h
            JOIN league_seasons ls ON ls.season = h.season
            LEFT JOIN draft_picks dp
                   ON dp.league_season_id = ls.id
                  AND dp.team_id = h.team_id
                  AND dp.player_id = h.player_id
            GROUP BY h.season, h.team_count ORDER BY h.season
            """
        )
        rows = cur.fetchall()

    print(
        f"{'season':>7} {'teams':>6} {'slot-days':>10} {'drafted':>9} "
        f"{'drafted%':>9} {'acquired%':>10}"
    )
    print("-" * 62)
    for season, teams, slot_days, drafted in rows:
        sd, dr = int(slot_days), int(drafted)
        pct = 100.0 * dr / sd if sd else 0.0
        flag = "  (COVID)" if season == COVID_SEASON else ""
        print(f"{season:>7} {teams:>6} {sd:>10} {dr:>9} {pct:>8.1f}% {100 - pct:>9.1f}%{flag}")


def roster_consumption(conn: psycopg.Connection) -> None:
    """How many distinct players a team consumes, and how many were drafted."""
    print()
    print("=" * 78)
    print("PLAYERS CONSUMED PER TEAM")
    print("=" * 78)
    print()
    print("Distinct players a team held for at least one day. A 13-round draft")
    print("supplies 13; the rest arrived during the season. Medians use")
    print("statistics.median; every season has an even team count, which biases")
    print("index arithmetic upward.")
    print()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH held AS (
              SELECT ls.season, ls.team_count, dls.team_id, dls.player_id
              FROM daily_lineup_slots dls
              JOIN teams t ON t.id = dls.team_id
              JOIN league_seasons ls ON ls.id = t.league_season_id
              WHERE {SEASONS}
              GROUP BY ls.season, ls.team_count, dls.team_id, dls.player_id
            ),
            per_team AS (
              SELECT h.season, h.team_count, h.team_id,
                     count(*) AS players_used,
                     count(*) FILTER (WHERE dp.player_id IS NOT NULL)
                       AS drafted_used
              FROM held h
              JOIN league_seasons ls ON ls.season = h.season
              LEFT JOIN draft_picks dp
                     ON dp.league_season_id = ls.id
                    AND dp.team_id = h.team_id
                    AND dp.player_id = h.player_id
              GROUP BY h.season, h.team_count, h.team_id
            )
            SELECT season, team_count, players_used, drafted_used
            FROM per_team ORDER BY season
            """
        )
        raw = cur.fetchall()

    buckets: dict[int, dict[str, list[float]]] = {}
    teams_by_season: dict[int, int] = {}
    for season, team_count, used, drafted in raw:
        s = int(season)
        b = buckets.setdefault(s, {"used": [], "drafted": []})
        b["used"].append(float(used))
        b["drafted"].append(float(drafted))
        teams_by_season[s] = int(team_count)

    print(
        f"{'season':>7} {'teams':>6} {'median used':>12} {'median drafted':>15} "
        f"{'median added':>13}"
    )
    print("-" * 56)
    for season in sorted(buckets):
        used = buckets[season]["used"]
        drafted = buckets[season]["drafted"]
        print(
            f"{season:>7} {teams_by_season[season]:>6} "
            f"{statistics.median(used):>12.1f} "
            f"{statistics.median(drafted):>15.1f} "
            f"{statistics.median(used) - statistics.median(drafted):>13.1f}"
        )


def main() -> None:
    with psycopg.connect(DSN) as conn:
        join_shape(conn)
        q1_best_available(conn)
        q2_replacement_gap(conn)
        q3_by_league_size(conn)
        q4_post_draft_share(conn)
        roster_consumption(conn)

    print()
    print("2020 is the suspended COVID season: flagged wherever it appears and")
    print("excluded from every headline figure in docs/waiver_value.md.")


if __name__ == "__main__":
    main()
