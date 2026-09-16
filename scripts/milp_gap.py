#!/usr/bin/env python3
"""How far is the draft optimizer's local search from the true optimum?

Usage:
    python scripts/milp_gap.py [--breakpoints 60] [--rate-step 0.002] [--time-limit 1200]
                               [--out docs/milp_gap.md]

`app/draft/optimizer.optimize` builds a roster by local search: greedy and
shuffled starts, then best single swaps. It is fast and has no guarantee.
This solves the same problem exactly, as a mixed-integer program with HiGHS
(`app/draft/exact.py` holds the model), on the 2027 room built from the BBM
exports, and compares.

The model's objective approximates the real score, so every roster it returns
is re-scored with `optimizer.score` and checked with `fieldable` and
`within_shape`, and the local search's roster is fixed inside the model to show
the approximation agrees with the real scorer.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from app.draft.exact import Built, build_model
from app.draft.live import load_room
from app.draft.optimizer import (
    Candidate,
    fieldable,
    optimize,
    roster_totals,
    score,
    within_shape,
)
from app.draft.targets import CategoryDistribution


def exact(players: list[Candidate], distributions: list[CategoryDistribution]) -> float:
    cats = [d.abbreviation for d in distributions]
    return score(roster_totals(players, cats), distributions)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--breakpoints", type=int, default=60)
    ap.add_argument("--rate-step", type=float, default=0.002)
    ap.add_argument("--time-limit", type=float, default=1200.0)
    ap.add_argument("--gap", type=float, default=0.001)
    ap.add_argument("--out", type=Path, default=Path("docs/milp_gap.md"))
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
        return build_model(
            candidates,
            dists,
            budget=state.budget,
            roster_slots=state.roster_slots,
            lineup=room.lineup,
            limits=room.limits,
            shape=shape,
            breakpoints=args.breakpoints,
            rate_step=args.rate_step,
            locked=fixed or (),
            excluded=()
            if fixed is None
            else [c.player_id for c in candidates if c.player_id not in fixed],
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
                (
                    restarts,
                    seed,
                    exact(list(plan.players), dists),
                    time.time() - started,
                    plan.player_ids,
                )
            )
            print(f"local restarts={restarts} seed={seed} {local[-1][2]:.4f}", flush=True)
    best_local = max(local, key=lambda row: row[2])

    # Consistency: the local roster fixed inside the model.
    fixed = build_for(best_local[4]).model.solve(120.0, 0.0)
    print(
        f"fixed local roster inside the model: {fixed.objective:.4f} ({fixed.status})", flush=True
    )

    built = build_for()
    print(
        f"model: {len(built.model.lb)} columns, {len(built.model.rows)} rows; "
        f"pwl error {built.breakpoint_error:.4f}, rate step error {built.rate_error:.4f}",
        flush=True,
    )
    solved = built.model.solve(args.time_limit, args.gap)
    if solved.x is None:
        print(f"no solution: {solved.status}")
        return 1
    chosen = [candidates[i] for i, var in enumerate(built.x) if solved.x[var] > 0.5]
    milp_exact = exact(chosen, dists)
    fits = fieldable(chosen, room.lineup, room.limits)
    shaped = within_shape(chosen, shape, frozenset())
    print(
        f"MILP: {solved.status} in {solved.seconds:.0f}s; model {solved.objective:.4f}; "
        f"exact {milp_exact:.4f}; fieldable {fits}; within shape {shaped}; "
        f"cost ${sum(c.price for c in chosen)}",
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
        f"({len(candidates)} candidates, {len(state.teams)} teams, ${state.budget}, centre limit "
        f"{room.limits.get('C')}, spending shape {list(shape)}).",
        "",
        "## Summary",
        "",
        f"- **Exact optimum (MILP, re-scored with the real scorer):** {milp_exact:.4f} "
        f"categories a week. Solver: {solved.status} in {solved.seconds:.0f}s.",
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
        f"`*` in both rosters ({len(shared)} of {state.roster_slots}).",
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
        f"- Model objective {solved.objective:.4f} against exact {milp_exact:.4f} "
        "for the MILP roster.",
        f"- Local roster fixed inside the model: {fixed.objective:.4f} against exact "
        f"{best_local[2]:.4f} ({fixed.status}).",
        f"- Piecewise-linear error per category at most {built.breakpoint_error:.4f} "
        f"({args.breakpoints} segments); percentage grid step {args.rate_step} "
        f"(at most {built.rate_error:.4f} of probability floored away per category).",
        f"- Model size: {len(built.model.lb)} columns, {len(built.model.rows)} rows.",
        "",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
