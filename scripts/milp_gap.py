#!/usr/bin/env python3
"""How far is the draft optimizer's local search from the true optimum?

Usage:
    python scripts/milp_gap.py [--breakpoints 60] [--rate-step 0.002] [--time-limit 1200]

`app/draft/optimizer.optimize` builds a roster by local search: greedy and
shuffled starts, then best single swaps. It is fast and has no guarantee. This
solves the same problem exactly, as a mixed-integer program with HiGHS (via
scipy), on the 2027 room built from the BBM exports, and compares.

THE MODEL

  x_i         buy player i (binary)
  roster      sum x = 13; sum price * x <= budget
  lineup      a_{i,t} for each eligible (player, slot type); slot types PG SG
              SF PF C G F take one starter each and UT takes three;
              sum_t a_{i,t} <= x_i
  centres     at most the league's limit of players whose primary position is C
  spending    the plan's places sorted most expensive first, L_1 >= ... >= L_13.
              A roster fits them -- each player a distinct place covering his
              price, which is `within_shape` -- exactly when, for every k, at
              most k - 1 chosen players cost more than L_k (Hall's condition on
              nested sets). Thirteen rows, no assignment variables.
  counting    T_c = sum weekly_c * x. Win probability Phi((T_c - mean)/spread),
              negated for turnovers, as a piecewise-linear function of T_c over
              the range a 13-man roster can reach, incremental formulation with
              one binary per segment.
  rates       FG% and FT% are ratios. For a grid of rates t_j, binary z_j means
              the roster's rate is at least t_j: makes - t_j * attempts >= 0,
              relaxed by a big M when z_j = 0, with z_j >= z_{j+1}. The model
              is credited Phi at the highest t_j reached, so it floors the rate
              to the grid.
  penalty     CONCEDE_PENALTY * min(1, sum_c max(0, 0.25 - p_c) / 0.25), the
              min(1, .) through one binary.

The model's objective approximates the real score, so every roster it returns
is re-scored with `optimizer.score` and checked with `fieldable` and
`within_shape`, and the local search's roster is fixed inside the model to show
the approximation agrees with the real scorer.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp  # type: ignore[import-untyped]
from scipy.sparse import coo_matrix  # type: ignore[import-untyped]

from app.draft.live import load_room
from app.draft.optimizer import (
    CONCEDE_PENALTY,
    CONCEDE_THRESHOLD,
    Candidate,
    fieldable,
    optimize,
    roster_totals,
    score,
    within_shape,
)
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PERCENTAGE_COMPONENTS

REPORT = Path("docs/milp_gap.md")


def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class Model:
    """Columns and rows accumulated before handing the whole thing to HiGHS."""

    lb: list[float] = field(default_factory=list)
    ub: list[float] = field(default_factory=list)
    integer: list[int] = field(default_factory=list)
    cost: list[float] = field(default_factory=list)
    rows: list[tuple[dict[int, float], float, float]] = field(default_factory=list)

    def var(
        self, lo: float = 0.0, hi: float = 1.0, *, binary: bool = False, obj: float = 0.0
    ) -> int:
        self.lb.append(lo)
        self.ub.append(hi)
        self.integer.append(1 if binary else 0)
        self.cost.append(obj)
        return len(self.lb) - 1

    def row(self, coefs: dict[int, float], lo: float = -np.inf, hi: float = np.inf) -> None:
        self.rows.append((coefs, lo, hi))

    def solve(self, time_limit: float, gap: float) -> tuple[np.ndarray | None, str, float, float]:
        data, rr, cc, rlo, rhi = [], [], [], [], []
        for r, (coefs, lo, hi) in enumerate(self.rows):
            for c, v in coefs.items():
                if v != 0.0:
                    rr.append(r)
                    cc.append(c)
                    data.append(v)
            rlo.append(lo)
            rhi.append(hi)
        matrix = coo_matrix((data, (rr, cc)), shape=(len(self.rows), len(self.lb))).tocsr()
        started = time.time()
        result = milp(
            c=-np.array(self.cost),  # scipy minimises
            constraints=LinearConstraint(matrix, np.array(rlo), np.array(rhi)),
            integrality=np.array(self.integer),
            bounds=Bounds(np.array(self.lb), np.array(self.ub)),
            options={"time_limit": time_limit, "mip_rel_gap": gap, "disp": False},
        )
        elapsed = time.time() - started
        objective = -float(result.fun) if result.x is not None else float("nan")
        return result.x, str(result.message), elapsed, objective


@dataclass
class Built:
    model: Model
    x: list[int]
    breakpoint_error: float
    rate_error: float


def build(
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    lineup: Sequence[str],
    limits: dict[str, int],
    shape: Sequence[int],
    budget: int,
    roster_slots: int,
    breakpoints: int,
    rate_step: float,
    fixed: frozenset[int] | None = None,
) -> Built:
    m = Model()
    n = len(candidates)
    x = [
        m.var(binary=True)
        if fixed is None
        else m.var(
            1.0 if c.player_id in fixed else 0.0,
            1.0 if c.player_id in fixed else 0.0,
            binary=True,
        )
        for c in candidates
    ]
    m.row(dict.fromkeys(x, 1.0), roster_slots, roster_slots)
    m.row({x[i]: float(c.price) for i, c in enumerate(candidates)}, hi=budget)

    # Lineup: a starter per slot type, UT three times.
    capacity: dict[str, int] = {}
    for slot in lineup:
        capacity[slot] = capacity.get(slot, 0) + 1
    by_slot: dict[str, list[int]] = {slot: [] for slot in capacity}
    for i, c in enumerate(candidates):
        uses: dict[int, float] = {x[i]: -1.0}
        for slot in capacity:
            if slot in c.eligible:
                a = m.var(binary=True)
                by_slot[slot].append(a)
                uses[a] = 1.0
        m.row(uses, hi=0.0)
    for slot, cap in capacity.items():
        m.row(dict.fromkeys(by_slot[slot], 1.0), cap, cap)

    # Position limits by primary position.
    for position, limit in limits.items():
        m.row({x[i]: 1.0 for i, c in enumerate(candidates) if c.position == position}, hi=limit)

    # Spending shape, Hall's condition on the sorted places.
    places = sorted(shape, reverse=True)
    for k, cap in enumerate(places, start=1):
        m.row({x[i]: 1.0 for i, c in enumerate(candidates) if c.price > cap}, hi=k - 1)

    probability: dict[str, dict[int, float]] = {}
    constant: dict[str, float] = {}
    worst_pwl = 0.0
    worst_rate = 0.0
    for d in distributions:
        cat = d.abbreviation
        sign = -1.0 if d.lower_is_better else 1.0
        spread = d.spread if d.spread > 0 else 1.0
        if cat in PERCENTAGE_COMPONENTS:
            made_key, attempted_key = PERCENTAGE_COMPONENTS[cat]
            made = [c.weekly.get(made_key, 0.0) for c in candidates]
            attempted = [c.weekly.get(attempted_key, 0.0) for c in candidates]
            most_attempts = sum(sorted(attempted, reverse=True)[:roster_slots])
            rates = sorted(c_m / c_a for c_m, c_a in zip(made, attempted, strict=True) if c_a > 0)
            lo = math.floor(rates[len(rates) // 50] / rate_step) * rate_step
            hi = math.ceil(rates[-1] / rate_step) * rate_step
            grid = [lo + j * rate_step for j in range(round((hi - lo) / rate_step) + 1)]
            base = phi(sign * (lo - rate_step - d.mean) / spread)
            coefs: dict[int, float] = {}
            previous_value = base
            previous_z: int | None = None
            for t in grid:
                z = m.var(binary=True)
                big = t * most_attempts + 1.0
                # z = 1 forces made - t * attempted >= 0; z = 0 leaves it free.
                row = {x[i]: made[i] - t * attempted[i] for i in range(n)}
                row[z] = -big
                m.row(row, lo=-big)
                if previous_z is not None:
                    m.row({previous_z: 1.0, z: -1.0}, lo=0.0)
                value = phi(sign * (t - d.mean) / spread)
                coefs[z] = value - previous_value
                worst_rate = max(worst_rate, abs(value - previous_value))
                previous_value = value
                previous_z = z
            probability[cat] = coefs
            constant[cat] = base
            continue

        weekly = [c.weekly.get(cat, 0.0) for c in candidates]
        low = sum(sorted(weekly)[:roster_slots])
        high = sum(sorted(weekly, reverse=True)[:roster_slots])
        step = (high - low) / breakpoints

        def f(total: float, d: CategoryDistribution = d, sign: float = sign) -> float:
            return phi(sign * (total - d.mean) / (d.spread if d.spread > 0 else 1.0))

        points = [low + k * step for k in range(breakpoints + 1)]
        deltas = [m.var() for _ in range(breakpoints)]
        switches = [m.var(binary=True) for _ in range(breakpoints - 1)]
        for k, u in enumerate(switches):
            m.row({deltas[k + 1]: 1.0, u: -1.0}, hi=0.0)
            m.row({u: 1.0, deltas[k]: -1.0}, hi=0.0)
        total_row = {x[i]: weekly[i] for i in range(n)}
        for delta in deltas:
            total_row[delta] = -step
        m.row(total_row, low, low)
        probability[cat] = {
            delta: f(points[k + 1]) - f(points[k]) for k, delta in enumerate(deltas)
        }
        constant[cat] = f(low)
        for k in range(breakpoints):
            for frac in (0.25, 0.5, 0.75):
                t = points[k] + frac * step
                linear = f(points[k]) + frac * (f(points[k + 1]) - f(points[k]))
                worst_pwl = max(worst_pwl, abs(f(t) - linear))

    # Objective: sum of probabilities, less the concede penalty.
    shortfall_total: dict[int, float] = {}
    offset = 0.0
    for cat, coefs in probability.items():
        for var, coef in coefs.items():
            m.cost[var] += coef
        offset += constant[cat]
        s = m.var(0.0, CONCEDE_THRESHOLD)
        # s >= 0.25 - p  <=>  s + p >= 0.25
        row = {s: 1.0, **coefs}
        m.row(row, lo=CONCEDE_THRESHOLD - constant[cat])
        shortfall_total[s] = 1.0 / CONCEDE_THRESHOLD
    # penalty = lambda * (S - e), e = max(0, S - 1) through binary b.
    total_s = m.var(0.0, 36.0)
    m.row({total_s: -1.0, **shortfall_total}, 0.0, 0.0)
    excess = m.var(0.0, 36.0)
    b = m.var(binary=True)
    m.cost[total_s] -= CONCEDE_PENALTY
    m.cost[excess] += CONCEDE_PENALTY
    m.row({excess: 1.0, b: -36.0}, hi=0.0)
    m.row({excess: 1.0, total_s: -1.0, b: 36.0}, hi=36.0 - 1.0)
    # The constant part of the probabilities is added back after solving.
    m.cost.append(0.0)
    m.lb.append(offset)
    m.ub.append(offset)
    m.integer.append(0)
    m.cost[-1] = 1.0
    return Built(m, x, worst_pwl, worst_rate)


def exact(players: Sequence[Candidate], distributions: Sequence[CategoryDistribution]) -> float:
    cats = [d.abbreviation for d in distributions]
    return score(roster_totals(players, cats), distributions)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--breakpoints", type=int, default=60)
    ap.add_argument("--rate-step", type=float, default=0.002)
    ap.add_argument("--time-limit", type=float, default=1200.0)
    ap.add_argument("--gap", type=float, default=0.001)
    args = ap.parse_args()

    room = load_room(
        2027,
        "Through The Wire",
        pool_season=None,
        pool_kind="projected",
        punt=[],
        restarts=12,
        bbm=Path("data/bbm/BBM_Projections_2027_total.xls"),
        bbm_per_game=Path("data/bbm/BBM_Projections_2027_pergame.xls"),
        plan="history",
    )
    state = room.state
    candidates = room.candidates
    dists = room.distributions
    shape = room.allocation.limits(state) if room.allocation else ()
    by_id = {c.player_id: c for c in candidates}
    print(f"{len(candidates)} candidates; shape {list(shape)}", flush=True)

    def build_for(fixed: frozenset[int] | None = None) -> Built:
        return build(
            candidates,
            dists,
            lineup=room.lineup,
            limits=room.limits,
            shape=shape,
            budget=state.budget,
            roster_slots=state.roster_slots,
            breakpoints=args.breakpoints,
            rate_step=args.rate_step,
            fixed=fixed,
        )

    # Local search across restarts and seeds.
    local: list[tuple[int, int, float, float, frozenset[int]]] = []
    for restarts in (4, 12, 48):
        for seed in range(5):
            started = time.time()
            plan = optimize(
                candidates,
                dists,
                budget=state.budget,
                roster_slots=state.roster_slots,
                lineup=room.lineup,
                limits=room.limits,
                restarts=restarts,
                seed=seed,
                shape=shape,
            )
            local.append(
                (restarts, seed, exact(plan.players, dists), time.time() - started, plan.player_ids)
            )
            print(f"local restarts={restarts} seed={seed} {local[-1][2]:.4f}", flush=True)
    best_local = max(local, key=lambda row: row[2])

    # Consistency: the local roster fixed inside the model.
    fixed = build_for(best_local[4])
    _, fstatus, _, fobj = fixed.model.solve(120.0, 0.0)
    print(f"fixed local roster inside the model: {fobj:.4f} ({fstatus})", flush=True)

    built = build_for()
    print(
        f"model: {len(built.model.lb)} columns, {len(built.model.rows)} rows; "
        f"pwl error {built.breakpoint_error:.4f}, rate step error {built.rate_error:.4f}",
        flush=True,
    )
    xs, status, elapsed, objective = built.model.solve(args.time_limit, args.gap)
    if xs is None:
        print(f"no solution: {status}")
        return 1
    chosen = [candidates[i] for i, var in enumerate(built.x) if xs[var] > 0.5]
    milp_exact = exact(chosen, dists)
    fits = fieldable(chosen, room.lineup, room.limits)
    shaped = within_shape(chosen, shape, frozenset())
    print(
        f"MILP: {status} in {elapsed:.0f}s; model {objective:.4f}; exact {milp_exact:.4f}; "
        f"fieldable {fits}; within shape {shaped}; cost ${sum(c.price for c in chosen)}",
        flush=True,
    )

    cats = [d.abbreviation for d in dists]
    local_players = [by_id[i] for i in best_local[4]]
    _, milp_probs = score(roster_totals(chosen, cats), dists)
    _, local_probs = score(roster_totals(local_players, cats), dists)
    shared = {c.player_id for c in chosen} & best_local[4]
    scores = [row[2] for row in local]
    lines = [
        "# How far is the local search from the optimum?",
        "",
        "Generated by `scripts/milp_gap.py` on the 2027 room built from the BBM exports "
        f"({len(candidates)} candidates, 15 teams, ${state.budget}, centre limit "
        f"{room.limits.get('C')}, spending shape {list(shape)}).",
        "",
        "## Summary",
        "",
        f"- **Exact optimum (MILP, re-scored with the real scorer):** {milp_exact:.4f} "
        f"categories a week. Solver: {status} in {elapsed:.0f}s.",
        f"- **Local search:** best {max(scores):.4f}, "
        f"median {sorted(scores)[len(scores) // 2]:.4f} "
        f"across {len(scores)} runs.",
        f"- **Gap:** {milp_exact - max(scores):+.4f} against the best run, "
        f"{milp_exact - sorted(scores)[len(scores) // 2]:+.4f} against the median.",
        "",
        "## Local search runs",
        "",
        "| restarts | seed | exact score | seconds |",
        "|---|---|---|---|",
        *(f"| {r} | {s} | {v:.4f} | {t:.1f} |" for r, s, v, t, _ in local),
        "",
        "## Rosters",
        "",
        "| MILP | price | | local search | price |",
        "|---|---|---|---|---|",
    ]
    left = sorted(chosen, key=lambda c: -c.price)
    right = sorted(local_players, key=lambda c: -c.price)
    for a, b in zip(left, right, strict=False):
        lines.append(
            f"| {a.name}{' *' if a.player_id in shared else ''} | ${a.price} | | "
            f"{b.name}{' *' if b.player_id in shared else ''} | ${b.price} |"
        )
    lines += [
        "",
        f"`*` in both rosters ({len(shared)} of 13).",
        "",
        "## Win probability by category",
        "",
        "| category | MILP | local |",
        "|---|---|---|",
        *(f"| {c} | {milp_probs[c]:.3f} | {local_probs[c]:.3f} |" for c in cats),
        "",
        "## Checks",
        "",
        f"- MILP roster fieldable: {fits}; within the spending shape: {shaped}; "
        f"cost ${sum(c.price for c in chosen)}.",
        f"- Model objective {objective:.4f} against exact {milp_exact:.4f} for the MILP roster.",
        f"- Local roster fixed inside the model: {fobj:.4f} against exact {best_local[2]:.4f} "
        f"({fstatus}).",
        f"- Piecewise-linear error per category at most {built.breakpoint_error:.4f} "
        f"({args.breakpoints} segments); percentage grid step {args.rate_step} "
        f"(at most {built.rate_error:.4f} of probability floored away per category).",
        f"- Model size: {len(built.model.lb)} columns, {len(built.model.rows)} rows.",
        "",
    ]
    REPORT.write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
