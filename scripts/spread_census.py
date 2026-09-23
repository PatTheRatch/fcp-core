#!/usr/bin/env python3
"""What the widened spread does to the week, on the same 2026 replay.

Usage:
    python scripts/spread_census.py --out docs/runs/2026-09-23-spread-census.json
    python scripts/spread_census.py --limit 2          # a quick smoke run

Read-only, and **not a calibration**: it scores nothing against what happened,
so no constant may be chosen from it. It answers the one question
`docs/pickups_backtest.md` cannot, because that document reports a hurdle
sweep and not a distribution: after `app.pickups.stream.SPREAD_SCALE` went
from 1.0 to 2.0 on 2026-09-23, how much smaller is what a manager sees on the
page?

Three things, over exactly the decision points `scripts/pickups_backtest.py`
replays -- fourteen teams, the first and fourth day of every 2026 matchup
period:

* how many of the five ranked moves clear the 0.20 bar, per team-week, and
  how often none of them does;
* the size of a move's `net` -- the mean, the median and the ninetieth
  percentile over every ranked move;
* the week's own `expected_wins`, which is what the MCP tools report as
  `expected_categories_won`. A wider spread pulls every week toward 4.5, and
  the mean distance from 4.5 is how far.

HOW THE TWO PASSES ARE KEPT COMPARABLE

Only `SPREAD_SCALE` moves between them. The distributions handed to the
recommender are the league's own measured ones in both passes, so the season
half of every judgement -- `app.pickups.judge`, which values men against those
same spreads through `app.scoring.value.marginal` -- is identical on both
sides and the whole difference is the head-to-head. Scaling the distributions
instead would have re-priced every player as well, and measured two things at
once.

It borrows the backtest's reconstructed 2026 schedule (`patched_state`), for
the reason that file's docstring gives: `pro_team_games` holds no 2026 rows,
and without a schedule every move is worth exactly zero.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Run by path, so `scripts/` is on sys.path and the repo root is not -- and so
# `pickups_backtest` below is this checkout's, not another one's.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pickups_backtest as backtest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.pickups import stream

#: The owner's streaming bar, which this run reports against and never moves.
HURDLE = stream.STREAM_HURDLE

#: The two scales compared: the model before 2026-09-23 and the one since.
SCALES = (1.0, 2.0)


def percentile(values: Sequence[float], share: float) -> float:
    """The `share` quantile, nearest rank. Small samples need nothing cleverer."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))]


def census(
    scale: float,
    session: Session,
    league_season: LeagueSeason,
    teams: Sequence[int],
    points: Sequence[tuple[int, int]],
    *,
    progress: bool = True,
) -> dict[str, Any]:
    """One pass over every decision point at one spread scale."""
    stream.SPREAD_SCALE = scale
    nets: list[float] = []
    expected: list[float] = []
    cleared_per_point: list[int] = []
    no_move = 0
    seen = 0
    for team_id in teams:
        for _period, day in points:
            pool = backtest.free_agent_pool(session, league_season, day)
            if not pool:
                continue
            try:
                report = stream.stream_recommendations(
                    session, league_season, team_id, day, pool=pool, tilt=True, bids=False
                )
            except Exception as exc:  # one bad day is not a run
                print(f"  !! {team_id} day {day}: {type(exc).__name__}: {exc}", flush=True)
                continue
            seen += 1
            expected.append(report.expected_wins)
            moves = report.moves[: backtest.TOP_N]
            nets.extend(move.net for move in moves)
            cleared = sum(1 for move in moves if move.clears(HURDLE))
            cleared_per_point.append(cleared)
            no_move += cleared == 0
        if progress:
            print(f"  team {team_id} done ({seen} decision points)", flush=True)
    gaps = [abs(value - 4.5) for value in expected]
    return {
        "scale": scale,
        "decision_points": seen,
        "ranked_moves": len(nets),
        "cleared_total": sum(cleared_per_point),
        "cleared_per_point_mean": statistics.fmean(cleared_per_point) if cleared_per_point else 0.0,
        "no_move_points": no_move,
        "no_move_rate": no_move / seen if seen else 0.0,
        "net_mean": statistics.fmean(nets) if nets else 0.0,
        "net_median": statistics.median(nets) if nets else 0.0,
        "net_p90": percentile(nets, 0.90),
        "net_max": max(nets) if nets else 0.0,
        "expected_mean": statistics.fmean(expected) if expected else 0.0,
        "expected_p10": percentile(expected, 0.10),
        "expected_p90": percentile(expected, 0.90),
        "expected_sd": statistics.pstdev(expected) if len(expected) > 1 else 0.0,
        "expected_gap_mean": statistics.fmean(gaps) if gaps else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Teams to run, for a smoke test")
    parser.add_argument("--out", type=Path, default=None, help="Write the JSON here")
    args = parser.parse_args()

    started = time.time()
    factory = make_session_factory(make_engine(get_settings().database_url))
    shipped = stream.SPREAD_SCALE
    runs: list[dict[str, Any]] = []
    try:
        with factory() as session:
            league_season = backtest.load_season(session)
            points = backtest.decision_points(session, league_season)
            teams = backtest.team_ids(session, league_season)
            if args.limit:
                teams = teams[: args.limit]
            print(f"{len(teams)} teams x {len(points)} decision points", flush=True)
            with backtest.patched_state():
                for scale in SCALES:
                    print(f"-- SPREAD_SCALE = {scale}", flush=True)
                    runs.append(census(scale, session, league_season, teams, points))
                    print(json.dumps(runs[-1], indent=1), flush=True)
    finally:
        # The constant is the product's; a run that died half way through must
        # not leave this process holding a different recommender.
        stream.SPREAD_SCALE = shipped

    payload = {"seconds": time.time() - started, "hurdle": HURDLE, "runs": runs}
    print(json.dumps(payload, indent=1))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=1) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
