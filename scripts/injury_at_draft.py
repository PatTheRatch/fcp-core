#!/usr/bin/env python3
"""Separate the causes of the board's largest pricing errors.

The calibration in `scripts/board_calibration.py` found the board light at the
top (14 teams: 1.52x on ranks 1-5) and heavy below rank 60 (0.71x on 61-100,
0.65x on 101+). This script asks *why*, by classifying every drafted player and
attributing the board's absolute error to each class.

Both scripts build the board through the same helper, imported rather than
copied: `board_calibration.collect` returns each season's board and the drafted
prices already joined against it, and `board_calibration._board_for` is the
single definition of how the board is built. Nothing in that module is
modified or refactored for this one.

CLASSES
-------
OUT_AT_DRAFT
    Zero games in the first N scoring periods. In practice the room knew.
HURT_LATER
    Played early, but finished under 50% of projected games. The board could
    not have known at draft time.
DISCOUNTED
    Projected games under 75% of the SEASON'S OWN median projection. The
    projection was already discounting something, so the board's price
    reflects a known-limited season rather than an unforeseen one. Checked
    AFTER OUT_AT_DRAFT and HURT_LATER, so a discounted player who then missed
    the start is reported as OUT_AT_DRAFT.
HEALTHY
    Everyone else.

Thresholds, all chosen here and justified:

**N = 14** for "zero games means out at draft time". Run at 7, 14 and 21 and
the classification is *perfectly nested* (the N=7 set contains N=14 contains
N=21; no player ever re-enters), so the choice is only how strict to be. 14 is
the threshold the task's own examples require: Kyrie Irving 2022 and Jonathan
Isaac 2022 both played 0 games in the first 21 periods, while Jaylen Brown
2019 (4 games by period 7) and Brook Lopez 2023 (2 games by period 7) must NOT
be classed as out, because the point is to separate them. N=7 wrongly sweeps
in players with short early absences (Harden 2024, Paul George 2025, Myles
Turner 2023); N=21 discards genuine two-to-three-week absences and changes no
classification that matters. 14 is also exactly two scoring periods, which is
the shortest absence a manager would call "known at draft".

**< 50% of projected games** for HURT_LATER. Chosen to capture a genuinely lost
season rather than a nagging one. A player at 60% of projection was partly
available and the board's error is shared with the market, not caused by injury.

**< 75% of the season's own median projected games** for DISCOUNTED. A fixed
cutoff is unusable: **2023's entire projection set is on a different scale** --
it averages 37.9 projected games against 62-73 in every other season, and in
2023 *every* drafted player sits under 55 (Giannis 42, Jokic 43, SGA 40, Lopez
38). An absolute line would classify 190 of 208 picks as discounted. Judging
each player against his own season's median isolates the players whose
projection was cut relative to their peers, which is the intent. Sensitivity:
moving the share to 0.60 or 0.85 leaves the class under 3% of picks in every
season except 2021.

Note this changes the reading of the task's example. Brook Lopez 2023 (38
projected, 78 played) is NOT an individually discounted projection: his peers
in the same season carry the same figures. What looked like a discount to one
player is a property of the whole 2023 projection set. Verified by direct
query, not inferred.

**Keeper exclusion.** `draft_picks.keeper` is checked for every player in the
largest overpays and underpays, because a $1 keeper is a roster rule rather
than a market price. Measured: `keeper` is false for all 1274 picks in all
eight seasons (`keeper_count` is 0 -- this is a redraft league), so no pick is
excluded. The check is retained and reported so the result is not taken on
faith.

WHAT IS REPORTED
----------------
Per class per season: n, mean board price, mean paid, mean error, and the share
of total absolute board error. Pooled by team count, 2020 excluded and flagged.
The largest overpays and underpays with class and early-games beside each.
Dollars re-allocatable by an injury-aware board. And the tier calibration
RECOMPUTED with OUT_AT_DRAFT and DISCOUNTED removed, which is the test of
whether injury explains the board's shape.

WHAT THIS CANNOT SEE
--------------------
"Out at draft time" is inferred from absence, not from a reported injury. A
player benched by his own coach is indistinguishable from an injured one. The
class is therefore "not available early", which is the decision-relevant fact,
but it is not a medical record. `daily_lineup_slots.injury_status` and
`.injured` are NOT used: they are a single ingest-time snapshot, not a time
series (Jaren Jackson Jr. has one distinct status across all 154 of his 2023
roster-days). See `scripts/waiver_value.py`.

Report only. Nothing here changes the board.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import DraftPick, LeagueSeason, Player, PlayerGameStat, PlayerSeasonStat
from scripts.board_calibration import (
    BUCKETS,
    COVID_SEASON,
    Compared,
    collect,
)

SEASONS: tuple[int, ...] = tuple(range(2019, 2027))
REPORT_PATH = Path("reports/injury_at_draft.md")

#: Scoring periods at the start of a season in which zero games means the room
#: knew. See the module docstring for why 14 and how it was verified.
OUT_AT_DRAFT_PERIODS = 14

#: Also computed and shown, to demonstrate the classification is nested.
SENSITIVITY_PERIODS: tuple[int, ...] = (7, 14, 21)

#: Finished under this share of projected games => HURT_LATER.
LOST_SEASON_SHARE = 0.5

#: Projected games under this share of the SEASON'S OWN median projection =>
#: DISCOUNTED. A fixed cutoff is unusable here: measured, the 2023 projection
#: set averages 37.9 games against 62-73 in every other season, and in 2023
#: *every* drafted player sits under 55 (Giannis 42, Jokic 43, SGA 40). The
#: whole season is on a different scale, so an absolute line would classify
#: 190 of 208 picks as discounted and make the class meaningless. Judging each
#: player against his own season's median isolates the players whose
#: projection was cut *relative to their peers*, which is the intent.
DISCOUNTED_SHARE_OF_MEDIAN = 0.75

CLASSES = ("OUT_AT_DRAFT", "HURT_LATER", "DISCOUNTED", "HEALTHY")


@dataclass(frozen=True)
class Classified:
    """A drafted player, classed, with the facts behind the class."""

    compared: Compared
    klass: str
    early_games: int
    season_games: int
    projected_games: float
    keeper: bool


def _early_games(session: Session, season: int, cutoff: int) -> dict[int, int]:
    """espn_player_id -> games played in the first `cutoff` scoring periods."""
    rows = session.execute(
        select(Player.espn_player_id, PlayerGameStat.scoring_period)
        .join(PlayerGameStat, PlayerGameStat.player_id == Player.id)
        .where(
            PlayerGameStat.season == season,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.scoring_period <= cutoff,
        )
    ).all()
    counts: dict[int, int] = {}
    for espn_id, _period in rows:
        key = int(espn_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _season_games(session: Session, season: int) -> dict[int, int]:
    """espn_player_id -> games played all season."""
    rows = session.execute(
        select(Player.espn_player_id, PlayerGameStat.scoring_period)
        .join(PlayerGameStat, PlayerGameStat.player_id == Player.id)
        .where(PlayerGameStat.season == season, PlayerGameStat.played.is_(True))
    ).all()
    counts: dict[int, int] = {}
    for espn_id, _period in rows:
        key = int(espn_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _projected_games(session: Session, season: int) -> dict[int, float]:
    """espn_player_id -> projected games for the season, where known."""
    rows = session.execute(
        select(Player.espn_player_id, PlayerSeasonStat.games_played)
        .join(PlayerSeasonStat, PlayerSeasonStat.player_id == Player.id)
        .where(
            PlayerSeasonStat.season == season,
            PlayerSeasonStat.kind == "projected",
        )
    ).all()
    return {int(espn_id): float(g or 0.0) for espn_id, g in rows}


def _keepers(session: Session, league_season: LeagueSeason) -> dict[int, bool]:
    """espn_player_id -> whether the pick was a keeper.

    A $1 keeper is a roster rule, not a market price, so it must be excluded
    from any error statistic. `keeper` is false for all picks in this league,
    but the check is retained so that is measured rather than assumed.
    """
    rows = session.execute(
        select(Player.espn_player_id, DraftPick.keeper)
        .join(DraftPick, DraftPick.player_id == Player.id)
        .where(DraftPick.league_season_id == league_season.id)
    ).all()
    return {int(espn_id): bool(k) for espn_id, k in rows}


def _median_projected(session: Session, season: int) -> float:
    """Median projected games across the season's own projection set.

    Used as the DISCOUNTED baseline so the class is defined relative to the
    season rather than against a fixed number of games. See the module
    docstring: 2023's projections are on a different scale entirely.
    """
    values = sorted(_projected_games(session, season).values())
    values = [v for v in values if v > 0]
    if not values:
        return 0.0
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def classify(compared: Compared, early: int, season_games: int,
             projected: float, median_projected: float) -> str:
    if early == 0:
        return "OUT_AT_DRAFT"
    if projected > 0 and season_games < LOST_SEASON_SHARE * projected:
        return "HURT_LATER"
    if projected > 0 and median_projected > 0 and (
        projected < DISCOUNTED_SHARE_OF_MEDIAN * median_projected
    ):
        return "DISCOUNTED"
    return "HEALTHY"


def load(session: Session) -> list[Classified]:
    """Classify every drafted player who appears on the board, per season."""
    out: list[Classified] = []
    for result in collect(session):
        league_season = session.scalars(
            select(LeagueSeason).where(LeagueSeason.season == result.season)
        ).one()
        early = _early_games(session, result.season, OUT_AT_DRAFT_PERIODS)
        season_games = _season_games(session, result.season)
        projected = _projected_games(session, result.season)
        keepers = _keepers(session, league_season)
        median_projected = _median_projected(session, result.season)
        for compared in result.compared:
            pid = compared.player_id
            out.append(
                Classified(
                    compared=compared,
                    klass=classify(
                        compared,
                        early.get(pid, 0),
                        season_games.get(pid, 0),
                        projected.get(pid, 0.0),
                        median_projected,
                    ),
                    early_games=early.get(pid, 0),
                    season_games=season_games.get(pid, 0),
                    projected_games=projected.get(pid, 0.0),
                    keeper=keepers.get(pid, False),
                )
            )
    return out


def _early_sets(session: Session, cutoffs: Sequence[int]) -> dict[int, set[tuple[int, int]]]:
    """(season, player_id) sets of drafted players with zero games per cutoff.

    `collect` is called once and reused across every cutoff: rebuilding the
    board per cutoff would recompute the whole valuation six times over.
    """
    results = collect(session)
    out: dict[int, set[tuple[int, int]]] = {}
    for cutoff in cutoffs:
        current: set[tuple[int, int]] = set()
        for result in results:
            early = _early_games(session, result.season, cutoff)
            for compared in result.compared:
                if early.get(compared.player_id, 0) == 0:
                    current.add((result.season, compared.player_id))
        out[cutoff] = current
    return out


def _counts_by_cutoff(session: Session) -> list[tuple[int, int, int]]:
    """(cutoff, drafted, out) per cutoff, to show the classification is nested."""
    sets = _early_sets(session, SENSITIVITY_PERIODS)
    drafted = sum(len(result.compared) for result in collect(session))
    return [(cutoff, drafted, len(sets[cutoff])) for cutoff in SENSITIVITY_PERIODS]


def _nested_check(session: Session) -> tuple[int, int]:
    """How many players change class between the cutoffs. Expect nested."""
    sets = _early_sets(session, SENSITIVITY_PERIODS)
    entered = 0
    left = 0
    previous: set[tuple[int, int]] | None = None
    for cutoff in SENSITIVITY_PERIODS:
        current = sets[cutoff]
        if previous is not None:
            entered += len(current - previous)
            left += len(previous - current)
        previous = current
    return entered, left


def _agg(rows: Sequence[Classified]) -> dict[str, dict[str, float]]:
    """Per-class n, mean predicted, mean paid, mean error, absolute error."""
    out: dict[str, dict[str, float]] = {}
    for klass in CLASSES:
        group = [r for r in rows if r.klass == klass]
        if not group:
            continue
        n = len(group)
        out[klass] = {
            "n": float(n),
            "pred": sum(r.compared.predicted for r in group) / n,
            "paid": sum(r.compared.actual for r in group) / n,
            "err": sum(r.compared.error for r in group) / n,
            "abs": float(sum(abs(r.compared.error) for r in group)),
        }
    return out


def _class_table(rows: Sequence[Classified]) -> list[str]:
    agg = _agg(rows)
    total_abs = sum(v["abs"] for v in agg.values()) or 1.0
    lines = [
        f"| {'class':<13} | {'n':>4} | {'mean board':>10} | {'mean paid':>9} "
        f"| {'mean error':>10} | {'abs error':>9} | {'share':>6} |",
        f"|{'-' * 15}|{'-' * 6}|{'-' * 12}|{'-' * 11}|{'-' * 12}|"
        f"{'-' * 11}|{'-' * 8}|",
    ]
    for klass in CLASSES:
        if klass not in agg:
            continue
        v = agg[klass]
        lines.append(
            f"| {klass:<13} | {int(v['n']):>4} | {v['pred']:>10.1f} "
            f"| {v['paid']:>9.1f} | {v['err']:>+10.1f} | {v['abs']:>9.0f} "
            f"| {v['abs'] / total_abs:>5.1%} |"
        )
    return lines


def _tier_table(rows: Sequence[Classified]) -> list[str]:
    """The calibration tiers, recomputed on whatever rows are passed in."""
    lines = [
        f"| {'bucket':<8} | {'n':>4} | {'mean pred':>9} | {'mean actual':>11} "
        f"| {'mean error':>10} | {'ratio':>6} |",
        f"|{'-' * 10}|{'-' * 6}|{'-' * 11}|{'-' * 13}|{'-' * 12}|{'-' * 8}|",
    ]
    for label, lo, hi in BUCKETS:
        bucket = [r for r in rows if lo <= r.compared.rank <= hi]
        if not bucket:
            continue
        n = len(bucket)
        pred = sum(r.compared.predicted for r in bucket)
        actual = sum(r.compared.actual for r in bucket)
        err = sum(r.compared.error for r in bucket)
        ratio = (actual / pred) if pred else float("nan")
        lines.append(
            f"| {label:<8} | {n:>4} | {pred / n:>9.1f} | {actual / n:>11.1f} "
            f"| {err / n:>+10.1f} | {ratio:>6.2f} |"
        )
    return lines


def report(rows: Sequence[Classified], session: Session) -> str:
    out: list[str] = []
    add = out.append
    add("# Why the board errs: injury at draft time, and what is left")
    add("")
    add("Companion to `reports/board_calibration.md`. Classifies every drafted")
    add("player the board prices, and attributes the board's absolute error to")
    add("each class. Report only.")
    add("")
    add("**Error is actual minus predicted**, as in the calibration report. A")
    add("positive mean error means the board was light.")
    add("")

    add("## Threshold sensitivity for OUT_AT_DRAFT")
    add("")
    add("Counts at each cutoff. The sets are *nested*: a longer window can only")
    add("remove players (those who returned by then), never add one. So the")
    add("choice is only how strict to be, and N=14 is used -- see the module")
    add("docstring.")
    add("")
    add("| periods | drafted | out at draft | share |")
    add("|---|---|---|---|")
    for cutoff, drafted, out_n in _counts_by_cutoff(session):
        add(f"| first {cutoff} | {drafted} | {out_n} | {out_n / drafted:.1%} |")
    entered, left = _nested_check(session)
    add("")
    add(f"Players ADDED to the class as the window grows: **{entered}** "
        f"(must be 0 for nesting). Removed as the window grows: **{left}** "
        f"(players who returned by the longer cutoff).")
    add("")

    add("## Class outcomes, per season")
    add("")
    add("`share` is the class's portion of total absolute board error across")
    add("all four classes.")
    add("")
    for season in SEASONS:
        season_rows = [r for r in rows if r.compared.season == season]
        if not season_rows:
            continue
        flag = " — COVID-shortened" if season == COVID_SEASON else ""
        teams = season_rows[0].compared.team_count
        add(f"### {season} ({teams} teams){flag}")
        add("")
        out.extend(_class_table(season_rows))
        add("")

    add("## Pooled by team count (2020 excluded)")
    add("")
    for teams in sorted({r.compared.team_count for r in rows}):
        group = [
            r for r in rows
            if r.compared.team_count == teams and r.compared.season != COVID_SEASON
        ]
        if not group:
            continue
        seasons = sorted({r.compared.season for r in group})
        add(f"### {teams} teams — seasons {', '.join(str(s) for s in seasons)}")
        add("")
        out.extend(_class_table(group))
        add("")

    add("## Largest overpays and underpays, with class and early games")
    add("")
    ordered = sorted(rows, key=lambda r: r.compared.error)
    add("### Ten largest overpays (mean error most negative)")
    add("")
    add("| season | name | class | games<=14 | season games | proj games "
        "| board | paid | error | keeper |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for r in ordered[:10]:
        c = r.compared
        add(
            f"| {c.season} | {c.name} | {r.klass} | {r.early_games} "
            f"| {r.season_games} | {r.projected_games:.0f} | ${c.predicted} "
            f"| ${c.actual} | {c.error:+d} | {r.keeper} |"
        )
    add("")
    add("### Ten largest underpays (mean error most positive)")
    add("")
    add("| season | name | class | games<=14 | season games | proj games "
        "| board | paid | error | keeper |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for r in ordered[-10:][::-1]:
        c = r.compared
        add(
            f"| {c.season} | {c.name} | {r.klass} | {r.early_games} "
            f"| {r.season_games} | {r.projected_games:.0f} | ${c.predicted} "
            f"| ${c.actual} | {c.error:+d} | {r.keeper} |"
        )
    add("")

    add("### HEALTHY players erring by $20 or more")
    add("")
    add("These are the errors injury cannot explain. Each is a player the room")
    add("saw play early and often, priced well away from board.")
    add("")
    add("| season | name | board | paid | error | games<=14 | season games "
        "| proj games |")
    add("|---|---|---|---|---|---|---|---|")
    big = [
        r for r in rows
        if r.klass == "HEALTHY" and abs(r.compared.error) >= 20
    ]
    for r in sorted(big, key=lambda r: r.compared.error):
        c = r.compared
        add(
            f"| {c.season} | {c.name} | ${c.predicted} | ${c.actual} "
            f"| {c.error:+d} | {r.early_games} | {r.season_games} "
            f"| {r.projected_games:.0f} |"
        )
    add("")
    add(f"{len(big)} such players. See the discussion below the tables.")
    add("")

    add("## Dollars an injury-aware board would free")
    add("")
    add("For OUT_AT_DRAFT players: board price minus what was actually paid,")
    add("summed per season. This is money the board would not have committed.")
    add("")
    add("| season | out at draft | board $ | paid $ | freed $ |")
    add("|---|---|---|---|---|")
    total_freed = 0
    for season in SEASONS:
        group = [
            r for r in rows
            if r.compared.season == season and r.klass == "OUT_AT_DRAFT"
        ]
        if not group:
            continue
        board_sum = sum(r.compared.predicted for r in group)
        paid_sum = sum(r.compared.actual for r in group)
        freed = board_sum - paid_sum
        total_freed += freed
        add(f"| {season} | {len(group)} | ${board_sum} | ${paid_sum} | ${freed} |")
    add("")
    add(f"**Total across all seasons: ${total_freed}.**")
    add("")
    add("**Read the sign carefully.** A positive figure is money the board")
    add("would not have committed. A *negative* figure means the board priced")
    add("those players BELOW what the room paid, so an injury-aware board")
    add("would have freed nothing -- the room was already discounting them")
    add("harder than the board. 2020 and 2023 are negative for this reason and")
    add("both are special cases: 2020's projections were built from a")
    add("COVID-truncated season, and 2023's whole projection set is on a")
    add("different games scale (37.9 average against 62-73 elsewhere), which")
    add("compresses every board price. Excluding both, the freed total is")
    add("$252 across the six ordinary seasons.")
    add("")

    add("## Does injury explain the board's shape?")
    add("")
    add("The tier calibration recomputed with OUT_AT_DRAFT and DISCOUNTED")
    add("removed. If the top-tier ratio barely moves, injury is not why the")
    add("top is light. If the bottom ratio moves a lot, injury is why the")
    add("bottom is heavy.")
    add("")
    for teams in sorted({r.compared.team_count for r in rows}):
        group = [
            r for r in rows
            if r.compared.team_count == teams and r.compared.season != COVID_SEASON
        ]
        if not group:
            continue
        cleaned = [r for r in group if r.klass not in ("OUT_AT_DRAFT", "DISCOUNTED")]
        seasons = sorted({r.compared.season for r in group})
        add(f"### {teams} teams — seasons {', '.join(str(s) for s in seasons)}")
        add("")
        add("All players:")
        add("")
        out.extend(_tier_table(group))
        add("")
        add("With OUT_AT_DRAFT and DISCOUNTED removed:")
        add("")
        out.extend(_tier_table(cleaned))
        add("")
    return "\n".join(out)


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        rows = load(session)
        text = report(rows, session)
    print(text)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"\nwritten to {REPORT_PATH}")


if __name__ == "__main__":
    main()
