"""The four measurements the intake runs on a league's own history.

docs/intake.md. Each one is the measurement a script already made on Full
Court Press, run on one league and returned as data rather than printed as a
document:

| here | the script it is |
|---|---|
| `measure_replacement` | `app.scoring.replacement.pickup_values` per season |
| `measure_lane` | `scripts/streaming_lane.py`, the per-place headline |
| `measure_hurdles` | `scripts/pickups_backtest.py`, the sweep and its tuning rule |
| `measure_trades` | `scripts/trade_calibration.py`, the 2x2 |

**The scripts are imported, not copied.** They are where the measurement was
argued out, reviewed and written up; a second implementation of a measurement
is a second measurement, and the two would disagree within a month. What this
module adds is a league to narrow by, a season loop, and a `Measurement` the
job can store.

**Every one of them is read-only** and opens no connection to ESPN. They read
box scores, lineup days and transactions that the ingest has already stored.

WHAT COMES BACK

A `Measurement` is the row `app.calibration.write` takes: a value (or none,
for the trade record), the sample it rests on, the payload the page and the
next run want to see, and how long it took. The note is not in it: the note
is `app.calibration.measured_note`, written in one place so every key's
sentence reads the same way, except the trade record's, which is the
evaluator's own account of itself (`app.trades.calibration.trade_note`).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.scoring.replacement import WINDOW, pickup_values
from app.scoring.season import SeasonBook

#: Seasons back over which the typical pickup is read. The rule in
#: `app/scoring/replacement.py` is "the lowest recent season's median",
#: because a floor that is too high charges nothing for dropping an ordinary
#: player; three is what "recent" means, and on this league it picks 2025's
#: 0.062, which is the 0.06 the constant held.
RECENT_SEASONS = 3


@dataclass(frozen=True)
class Measurement:
    """One key measured on one league: the row, and what it cost to make."""

    key: str
    #: Categories a week, or None where the key is a table.
    value: float | None
    #: The sample, in this key's own unit (`app.calibration.unit`).
    n: int
    payload: dict[str, Any] = field(default_factory=dict)
    run_seconds: float = 0.0
    #: The seasons it could actually read. Empty means nothing was measured,
    #: which is not a failure: a league with one unplayed season has nothing
    #: to measure yet and is told so.
    seasons: tuple[int, ...] = ()


def stored_seasons(session: Session, league_id: int) -> list[int]:
    """Every season of this league the database holds, oldest first."""
    return [
        int(season)
        for season in session.scalars(
            select(LeagueSeason.season)
            .where(LeagueSeason.league_id == league_id)
            .order_by(LeagueSeason.season)
        ).all()
    ]


def season_rows(session: Session, league_id: int) -> list[LeagueSeason]:
    return list(
        session.scalars(
            select(LeagueSeason)
            .where(LeagueSeason.league_id == league_id)
            .order_by(LeagueSeason.season)
        ).all()
    )


def _quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    """(median, lower quartile, upper quartile), as `streaming_lane` reads them."""
    if not values:
        return 0.0, 0.0, 0.0
    if len(values) == 1:
        only = float(values[0])
        return only, only, only
    lower, _mid, upper = statistics.quantiles(values, n=4, method="inclusive")
    return statistics.median(values), lower, upper


# ---------------------------------------------------------------------------
# 1. what a pickup is worth
# ---------------------------------------------------------------------------


def measure_replacement(
    session: Session, league_id: int, *, seasons: Iterable[int] | None = None
) -> Measurement:
    """`typical_pickup`: the lowest recent season's median executed add.

    Every executed add of every season this league holds is valued exactly as
    `app.scoring.replacement` values it -- the added man's started lines for
    the adding team over the next fourteen days, in categories a week -- and
    the season medians go into the payload as the table in that module's
    docstring. The value is the lowest of the last `RECENT_SEASONS`, rounded
    to the two decimals the constant was written in, and `n` is the adds of
    the season it came from: that is the sample the number rests on.
    """
    started = time.monotonic()
    wanted = list(seasons) if seasons is not None else stored_seasons(session, league_id)
    by_season: dict[str, dict[str, Any]] = {}
    read: list[int] = []
    for season in wanted:
        row = session.scalar(
            select(LeagueSeason).where(
                LeagueSeason.league_id == league_id, LeagueSeason.season == season
            )
        )
        if row is None:
            continue
        values = pickup_values(SeasonBook.load(session, season, league_season=row))
        if not values:
            continue
        median, lower, upper = _quartiles(values)
        by_season[str(season)] = {
            "median": round(median, 3),
            "mean": round(statistics.fmean(values), 3),
            "iqr": [round(lower, 3), round(upper, 3)],
            "adds": len(values),
        }
        read.append(season)

    recent = sorted(read)[-RECENT_SEASONS:]
    if not recent:
        return Measurement(
            key="typical_pickup",
            value=None,
            n=0,
            payload={"by_season": by_season, "rule": "the lowest recent season's median"},
            run_seconds=time.monotonic() - started,
        )
    leanest = min(recent, key=lambda season: by_season[str(season)]["median"])
    return Measurement(
        key="typical_pickup",
        value=round(float(by_season[str(leanest)]["median"]), 2),
        n=int(by_season[str(leanest)]["adds"]),
        payload={
            "rule": "the lowest recent season's median",
            "recent_seasons": RECENT_SEASONS,
            "from_season": leanest,
            "window_days": WINDOW,
            "by_season": by_season,
            "script": "app/scoring/replacement.py::pickup_values",
        },
        run_seconds=time.monotonic() - started,
        seasons=tuple(read),
    )


# ---------------------------------------------------------------------------
# 2. what an open place is worth
# ---------------------------------------------------------------------------


def measure_lane(
    session: Session, league_id: int, *, seasons: Iterable[int] | None = None
) -> Measurement:
    """`opened_place`: the pooled per-place median of a streamed roster lane.

    `scripts/streaming_lane.py`'s headline, on this league: every team-period
    of every stored season contributes what one *place* of its streaming lane
    returned, the samples are pooled across seasons, and the value is their
    median with the interquartile range beside it. The held 13th man and the
    ordinary held place go into the payload too, because the caution the
    document draws is from the comparison and not from the number: an opened
    place must never be priced above a man.
    """
    from scripts.streaming_lane import SeasonMeasure, measure_season, pooled

    started = time.monotonic()
    wanted = list(seasons) if seasons is not None else stored_seasons(session, league_id)
    measured: list[SeasonMeasure] = []
    read: list[int] = []
    for season in wanted:
        found = measure_season(session, season, league_id)
        if not found.team_periods:
            continue
        measured.append(found)
        read.append(season)

    per_place = pooled(measured, "lane_place_value")
    median, lower, upper = _quartiles(per_place)
    held_13th, _, _ = _quartiles(pooled(measured, "held_13th"))
    held_all, _, _ = _quartiles(pooled(measured, "held_all"))
    games, _, _ = _quartiles(pooled(measured, "lane_place_games"))
    payload: dict[str, Any] = {
        "median": round(median, 3),
        "iqr": [round(lower, 3), round(upper, 3)],
        "mean": round(statistics.fmean(per_place), 3) if per_place else 0.0,
        "held_13th_median": round(held_13th, 3),
        "held_place_median": round(held_all, 3),
        "started_games_a_week": round(games, 2),
        "by_season": {
            str(found.season): {
                "median": round(_quartiles(found.lane_place_value)[0], 3),
                "team_periods": len(found.lane_place_value),
            }
            for found in measured
        },
        "seasons": read,
        "script": "scripts/streaming_lane.py",
        "document": "docs/streaming_lane.md",
    }
    return Measurement(
        key="opened_place",
        value=round(median, 2) if per_place else None,
        n=len(per_place),
        payload=payload,
        run_seconds=time.monotonic() - started,
        seasons=tuple(read),
    )


# ---------------------------------------------------------------------------
# 3. the three bars
# ---------------------------------------------------------------------------


@dataclass
class SweptSeason:
    """One season's grid, kept so the sweep can be resumed where it stopped."""

    season: int
    #: "s|p|f" -> the cell's numbers, both sides.
    cells: dict[str, dict[str, float]]
    decisions: int
    teams: int
    baseline_week: float
    baseline_season: float
    baseline_n: int
    run_seconds: float


def _cell_name(key: tuple[float, float, float]) -> str:
    return f"{key[0]:.2f}|{key[1]:.2f}|{key[2]:.2f}"


def sweep_one_season(session: Session, league_id: int, season: int) -> SweptSeason | None:
    """`scripts/pickups_backtest.py`'s whole sweep, for one season.

    The expensive one: about ninety minutes for fourteen teams over eight
    seasons on this league, most of it in the day-by-day category replay. It
    is one season at a time so the job can be resumed -- a season already in
    the payload is skipped on the next attempt -- and so a worker that dies
    in season five does not throw away the four before it.

    Returns None for a season with nothing to replay (no matchup periods, no
    teams, no lineup days), which is the ordinary case for a season that was
    ingested for its settings before it was played.
    """
    from scripts.pickups_backtest import (
        Replay,
        decision_points,
        league_baseline,
        patched_state,
        sweep,
        team_ids,
        team_rows,
    )

    started = time.monotonic()
    league_season = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.league_id == league_id, LeagueSeason.season == season
        )
    )
    if league_season is None:
        return None
    points = decision_points(session, league_season)
    teams = team_ids(session, league_season)
    if not points or not teams:
        return None
    book = Replay.load(session, league_season)
    if not book.started:
        return None
    rows = team_rows(session, league_season)
    with patched_state():
        grid = sweep(session, league_season, teams, points, book=book, team_rows=rows, tilt=True)
        base = league_baseline(session, league_season, book)
    cells = {
        _cell_name(key): {
            "stream_n": stream.n,
            "stream_mean": stream.mean,
            "stream_win": stream.win_rate,
            "stream_no_move": stream.no_move_rate,
            "stream_claimed": stream.claimed,
            "stream_decisions": stream.decisions,
            "season_n": season_side.n,
            "season_mean": season_side.mean,
            "season_win": season_side.win_rate,
            "season_no_move": season_side.no_move_rate,
            "season_claimed": season_side.claimed,
            "season_decisions": season_side.decisions,
        }
        for key, (stream, season_side) in sorted(grid.items())
    }
    return SweptSeason(
        season=season,
        cells=cells,
        decisions=len(points) * len(teams),
        teams=len(teams),
        baseline_week=statistics.fmean([row.week for row in base]) if base else 0.0,
        baseline_season=statistics.fmean([row.season for row in base]) if base else 0.0,
        baseline_n=len(base),
        run_seconds=time.monotonic() - started,
    )


def _merge(swept: Sequence[SweptSeason]) -> dict[str, dict[str, float]]:
    """Every season's grid added up, decision-weighted where it must be.

    A count adds; a mean is weighted by the moves it is a mean of; a rate is
    weighted by the decisions it is a rate over. Pooling the seasons rather
    than measuring each one is the point: one season of one league is 616
    decision points, and the tuning rule wants a no-move rate it can believe.
    """
    names = sorted({name for season in swept for name in season.cells})
    out: dict[str, dict[str, float]] = {}
    for name in names:
        rows = [season.cells[name] for season in swept if name in season.cells]
        merged: dict[str, float] = {}
        for side in ("stream", "season"):
            moves = sum(int(row[f"{side}_n"]) for row in rows)
            decisions = sum(int(row[f"{side}_decisions"]) for row in rows)
            merged[f"{side}_n"] = moves
            merged[f"{side}_decisions"] = decisions
            for figure in ("mean", "win", "claimed"):
                merged[f"{side}_{figure}"] = (
                    sum(row[f"{side}_{figure}"] * int(row[f"{side}_n"]) for row in rows) / moves
                    if moves
                    else 0.0
                )
            merged[f"{side}_no_move"] = (
                sum(row[f"{side}_no_move"] * int(row[f"{side}_decisions"]) for row in rows)
                / decisions
                if decisions
                else 1.0
            )
        out[name] = merged
    return out


#: The rule of `docs/pickups_backtest.md` section 4, restated over the merged
#: grid: a setting qualifies when it still says "no move" on more than a
#: fifth of decisions and beats the league's own moves.
def _picks(
    merged: dict[str, dict[str, float]], *, base_week: float, base_season: float
) -> dict[str, Any]:
    from scripts.pickups_backtest import MIN_NO_MOVE

    def qualifying(side: str, baseline: float) -> list[tuple[str, dict[str, float]]]:
        return [
            (name, cell)
            for name, cell in sorted(merged.items())
            if cell[f"{side}_no_move"] > MIN_NO_MOVE and cell[f"{side}_mean"] > baseline
        ]

    picked: dict[str, Any] = {"min_no_move": MIN_NO_MOVE}
    stream = qualifying("stream", base_week)
    season = qualifying("season", base_season)
    if stream:
        name, cell = max(stream, key=lambda row: row[1]["stream_mean"])
        picked["stream"] = {
            "hurdle": float(name.split("|")[0]),
            "mean": cell["stream_mean"],
            "n": int(cell["stream_n"]),
            "no_move": cell["stream_no_move"],
        }
    if season:
        name, cell = max(season, key=lambda row: row[1]["season_mean"])
        parts = name.split("|")
        picked["season"] = {
            "paid": float(parts[1]),
            "free": float(parts[2]),
            "mean": cell["season_mean"],
            "n": int(cell["season_n"]),
            "no_move": cell["season_no_move"],
        }
    return picked


def hurdles_from(swept: Sequence[SweptSeason]) -> dict[str, Measurement]:
    """The three bars, from the seasons swept so far.

    The whole grid goes into every one of the three payloads, because a
    manager looking at his bar is owed the other cells beside it: the bar
    labels, it never hides, and "0.10 names 450 moves at +1.05" is exactly
    the trade the page has to be able to show him.

    Where the tuning rule picks nothing -- which is what it does on the
    streaming side of this league, every time it has been run, because a move
    that fills an empty day is recommended whatever the bar -- the key is
    measured with no value: the row records the grid and the sample, the page
    says the sweep chose nothing, and the fallback carries on to the default.
    """
    decisions = sum(season.decisions for season in swept)
    seconds = sum(season.run_seconds for season in swept)
    merged = _merge(swept)
    weight = sum(season.baseline_n for season in swept)
    base_week = (
        sum(season.baseline_week * season.baseline_n for season in swept) / weight
        if weight
        else 0.0
    )
    base_season = (
        sum(season.baseline_season * season.baseline_n for season in swept) / weight
        if weight
        else 0.0
    )
    picked = _picks(merged, base_week=base_week, base_season=base_season)
    shared: dict[str, Any] = {
        "grid": merged,
        "picked": picked,
        "baseline": {"week": base_week, "season": base_season, "n": weight},
        "seasons": [season.season for season in swept],
        "teams": max((season.teams for season in swept), default=0),
        "script": "scripts/pickups_backtest.py",
        "document": "docs/pickups_backtest.md",
    }
    stream = picked.get("stream")
    season_pick = picked.get("season")
    return {
        "stream_hurdle": Measurement(
            key="stream_hurdle",
            value=None if stream is None else float(stream["hurdle"]),
            n=decisions,
            payload=shared | {"chose": stream},
            run_seconds=seconds,
            seasons=tuple(season.season for season in swept),
        ),
        "season_hurdle_paid": Measurement(
            key="season_hurdle_paid",
            value=None if season_pick is None else float(season_pick["paid"]),
            n=decisions,
            payload=shared | {"chose": season_pick},
            run_seconds=seconds,
            seasons=tuple(season.season for season in swept),
        ),
        "season_hurdle_free": Measurement(
            key="season_hurdle_free",
            value=None if season_pick is None else float(season_pick["free"]),
            n=decisions,
            payload=shared | {"chose": season_pick},
            run_seconds=seconds,
            seasons=tuple(season.season for season in swept),
        ),
    }


# ---------------------------------------------------------------------------
# 4. the trade number's record
# ---------------------------------------------------------------------------


#: Keys of `uneven_error` that name an **earlier revision of this code**
#: rather than the run being made. They are carried from one measurement of a
#: league to the next: "this number used to run about four tenths above what
#: those deals really did" is a fact about revision R1, and a league that has
#: it does not stop having it because it was measured again. A league
#: measured for the first time has none, and its note says only where the
#: number stands.
REVISION_ERRORS = ("R1, the old yardstick",)


def measure_trades(
    session: Session,
    league_id: int,
    *,
    seasons: Iterable[int] | None = None,
    review_days: int = 1,
    carry: Mapping[str, float] | None = None,
) -> Measurement:
    """`trade_record`: the 2x2 of `scripts/trade_calibration.py`, on this league.

    Every trade the database can both evaluate forward and grade in hindsight
    is replayed, both sides of it, and read against the two horizons and the
    two headlines the run declared before it was made. The primary cell --
    the roster with-and-without, over the thirty days after the deal -- is
    what the note is written from, with the exact binomial interval a fair
    coin would give over the same number of tosses beside it.

    `carry` is what an earlier measurement of this league recorded about a
    revision of the evaluator (`REVISION_ERRORS`), so a re-run keeps the
    league's own history and its note does not lose a sentence it had
    yesterday. A league measured once has nothing to carry.
    """
    from scripts.trade_calibration import (
        CELLS,
        Skipped,
        _mean_error,
        coin_interval,
        primary,
        rows_for_season,
        spearman,
    )

    started = time.monotonic()
    wanted = list(seasons) if seasons is not None else stored_seasons(session, league_id)
    skipped = Skipped()
    players: list[Any] = []
    rows: list[Any] = []
    read: list[int] = []
    for season in wanted:
        found = rows_for_season(
            session, season, skipped, players, review_days=review_days, league_id=league_id
        )
        if found:
            rows.extend(found)
            read.append(season)

    cells: list[dict[str, Any]] = []
    for cell in CELLS:
        scored = cell.scored(rows)
        deals = cell.deals(rows)
        cells.append(
            {
                "headline": cell.headline,
                "horizon": cell.horizon,
                "deals": len(deals),
                "picked": sum(1 for deal in deals if deal.right),
                "spearman": round(
                    spearman([s.predicted for s in scored], [s.delivered for s in scored]), 3
                )
                if len(scored) > 1
                else 0.0,
                "mean_error": round(statistics.fmean([s.error for s in scored]), 3)
                if scored
                else 0.0,
                "sides": len(scored),
            }
        )

    head = primary()
    deals = head.deals(rows)
    picked = sum(1 for deal in deals if deal.right)
    uneven = [row for row in rows if row.uneven]
    uneven_now, uneven_sides = _mean_error(head, uneven)
    low, high = coin_interval(len(deals)) if deals else (0, 0)
    payload: dict[str, Any] = {
        "cells": cells,
        "deals": len(deals),
        "picked": picked,
        "sides": len(head.scored(rows)),
        "seasons": read,
        "window_days": 30,
        "coin_range": [low, high],
        "uneven_sides": uneven_sides,
        "uneven_error": {
            **{
                name: float(value)
                for name, value in (carry or {}).items()
                if name in REVISION_ERRORS
            },
            "this run": round(uneven_now, 3),
        },
        "player_level_sample": len(players),
        "player_level_spearman": round(
            spearman([p.predicted for p in players], [p.delivered for p in players]), 3
        )
        if len(players) > 1
        else 0.0,
        "skipped": vars(skipped),
        "review_days": review_days,
        "script": "scripts/trade_calibration.py",
        "document": "docs/trades.md",
    }
    return Measurement(
        key="trade_record",
        value=None,
        n=len(deals),
        payload=payload,
        run_seconds=time.monotonic() - started,
        seasons=tuple(read),
    )


def trade_note_for(payload: dict[str, Any]) -> str:
    """The sentence a `trade_record` payload prints, from its own figures."""
    from app.trades.calibration import trade_note

    low, high = payload.get("coin_range", [0, 0])
    errors = payload.get("uneven_error") or {}
    now = float(errors.get("this run", errors.get("R2, the streamed lane", 0.0)))
    before = errors.get("R1, the old yardstick")
    return trade_note(
        deals=int(payload.get("deals", 0)),
        picked=int(payload.get("picked", 0)),
        coin_range=(int(low), int(high)),
        uneven_now=now,
        window_days=int(payload.get("window_days", 30)),
        uneven_before=None if before is None else float(before),
    )
