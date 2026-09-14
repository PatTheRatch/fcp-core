#!/usr/bin/env python3
"""Refit the board's tier curve and test it on a season held out.

Usage:
    python scripts/fit_tier_curve.py                # fit before 2026, test on 2026
    python scripts/fit_tier_curve.py --test 2027    # once 2027 has a draft

Fits on every usable drafted season before --test (2020 and 2023 are
excluded by app.draft.projections), applies the curve to the held-out
season's board, and reports per-bucket ratio and mean absolute error for
the raw board and the curved one. Then leave-one-season-out across all
usable seasons, which is the test of whether the curve generalises rather
than memorising a year. Finishes by printing the fitted curve in the form
`app/draft/tiers.py` holds it, so refitting is a paste and a commit with
the numbers beside it.
"""

from __future__ import annotations

import argparse
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft import pool
from app.draft.market import PriceBoard
from app.draft.projections import usable
from app.draft.tiers import (
    DEFAULT_BUCKETS,
    Observation,
    TierCurve,
    apply_tier_curve,
    fit_tier_curve,
    rank_players,
)
from scripts.board_calibration import _board_for


def _seasons(session: Session) -> dict[int, tuple[PriceBoard, dict[int, int]]]:
    out: dict[int, tuple[PriceBoard, dict[int, int]]] = {}
    for ls in session.scalars(select(LeagueSeason).order_by(LeagueSeason.season)):
        if not usable(ls.season) or ls.auction_budget <= 0:
            continue
        actual = pool.drafted_prices(session, ls)
        if not actual:
            continue  # no draft yet
        out[ls.season] = (_board_for(session, ls), actual)
    return out


def observations(board: PriceBoard, actual: dict[int, int]) -> list[Observation]:
    return [
        Observation(rank, p.expected_price, actual[p.player_id])
        for rank, p in enumerate(rank_players(board), 1)
        if p.player_id in actual
    ]


def score(board: PriceBoard, actual: dict[int, int]) -> list[tuple[float | None, float | None]]:
    """(ratio, MAE) per bucket over drafted players."""
    obs = observations(board, actual)
    probe = TierCurve(DEFAULT_BUCKETS, (1.0,) * len(DEFAULT_BUCKETS), ())
    out: list[tuple[float | None, float | None]] = []
    for index in range(len(DEFAULT_BUCKETS)):
        rows = [o for o in obs if probe.bucket_of(o.rank) == index]
        if not rows:
            out.append((None, None))
            continue
        ratio = sum(o.actual for o in rows) / max(1, sum(o.predicted for o in rows))
        out.append((ratio, mean(abs(o.actual - o.predicted) for o in rows)))
    return out


def _mean_mae(scored: list[tuple[float | None, float | None]]) -> float:
    return mean(m for _, m in scored if m is not None)


def _row(label: str, scored: list[tuple[float | None, float | None]]) -> str:
    cells = "  ".join(f"{r:4.2f}/{m:4.1f}" if r is not None else "   -    " for r, m in scored)
    return f"  {label:24s} {cells}   mean MAE {_mean_mae(scored):4.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test", type=int, default=2026, help="season held out for testing")
    args = ap.parse_args()

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        seasons = _seasons(session)
    if args.test not in seasons:
        raise SystemExit(f"{args.test} has no usable draft to test on; have {sorted(seasons)}")
    train = [y for y in seasons if y < args.test]

    fitted = fit_tier_curve(
        (o for y in train for o in observations(*seasons[y])), fit_seasons=train
    )
    board, actual = seasons[args.test]
    header = "  ".join(f"{a}-{b if b < 10**9 else '+'}" for a, b in DEFAULT_BUCKETS)
    print(f"fit on {train}, held out {args.test}; ratio/MAE per bucket: {header}")
    print(_row("raw board", score(board, actual)))
    print(_row("tier curve", score(apply_tier_curve(board, fitted), actual)))

    print("\nleave-one-season-out: mean MAE, raw vs curved, and top-5 ratio")
    for year, (b, a) in seasons.items():
        others = [o for o in seasons if o != year]
        curve = fit_tier_curve(o for y in others for o in observations(*seasons[y]))
        raw, curved = score(b, a), score(apply_tier_curve(b, curve), a)
        print(
            f"  {year}  raw {_mean_mae(raw):5.1f}  curved {_mean_mae(curved):5.1f}   "
            f"top-5 {raw[0][0]:.2f} -> {curved[0][0]:.2f}"
        )

    print("\nLEAGUE_TIER_CURVE = TierCurve(")
    print("    buckets=DEFAULT_BUCKETS,")
    print(f"    multipliers=({', '.join(f'{m:.2f}' for m in fitted.multipliers)}),")
    print(f"    fit_seasons={tuple(train)},")
    print(")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
