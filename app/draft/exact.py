"""Exact roster selection: the optimizer's problem as a mixed-integer program.

`app.draft.optimizer.optimize` builds a roster by local search: greedy and
shuffled starts, then the best single swap until none helps. It is fast and
has no guarantee. This solves the same problem exactly, with HiGHS through
scipy, which is how the local search was shown to be at the optimum
(docs/milp_gap.md). It also takes what the local search cannot: TARGETS.

A target is a floor on one category's win probability. It is soft. The
model may miss it, and pays `TARGET_WEIGHT` per unit of probability it
misses by, so a set of targets that cannot all be hit comes back as the
roster that gets closest, with the shortfall reported, rather than as
"infeasible". The only hard constraints are the rules of a roster -- size,
budget, lineup, position limits -- and the spending shape, which is dropped
when the locked players alone break it, because no roster could keep it.

THE MODEL

  x_i         buy player i (binary). Locked players are fixed at one,
              excluded at zero.
  roster      sum x = roster_slots; sum price * x <= budget
  lineup      a_{i,t} for each eligible (player, slot type); each slot type
              takes as many starters as the lineup has of it;
              sum_t a_{i,t} <= x_i
  limits      at most the league's cap of players whose primary position is
              the capped one
  spending    the plan's places sorted most expensive first, L_1 >= ... A
              roster fits them -- each player a distinct place covering his
              price, which is `within_shape` -- exactly when, for every k,
              at most k - 1 chosen players cost more than L_k (Hall's
              condition on nested sets). One row per place, no assignment
              variables. Exempt players (what we already own) do not count.
  counting    T_c = sum weekly_c * x. Win probability Phi((T_c - mean) /
              spread), negated for turnovers, as a piecewise-linear function
              of T_c over the range a roster can reach: an incremental
              formulation with one binary per segment.
  rates       FG% and FT% are ratios. For a grid of rates t_j, binary z_j
              means the roster's rate is at least t_j: makes - t_j * attempts
              >= 0, relaxed by a big M when z_j = 0, with z_j >= z_{j+1}. The
              model is credited Phi at the highest t_j reached, so it floors
              the rate to the grid.
  concede     CONCEDE_PENALTY * min(1, sum_c max(0, 0.25 - p_c) / 0.25) over
              the categories not punted, the min(1, .) through one binary.
              The same charge as `optimizer.score`.
  targets     for each target, s >= p_target - p_c and s >= 0, at
              -TARGET_WEIGHT each in the objective.
  objective   sum_c w_c p_c - concede - TARGET_WEIGHT * sum s. Every
              category counted weighs one, which is all of them unless
              `maximize` names some; a punted category weighs nothing and is
              exempt from the concede charge, as in `optimizer.score`.

The objective approximates the real score, so every roster that comes back
is re-scored with `optimizer.score`, and the shortfall against each target
is reported from that re-score, not from the model.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp  # type: ignore[import-untyped]
from scipy.sparse import coo_matrix  # type: ignore[import-untyped]

from app.draft.lineup import DEFAULT_LINEUP
from app.draft.optimizer import (
    CONCEDE_PENALTY,
    CONCEDE_THRESHOLD,
    Candidate,
    fieldable,
    roster_totals,
    score,
    within_shape,
)
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PERCENTAGE_COMPONENTS

#: What missing a target costs, in categories a week per unit of win
#: probability short. A whole category is worth at most one a week, so at
#: ten a hundredth of a target is worth a tenth of a category elsewhere:
#: the targets are hit whenever the board allows it at any reasonable cost,
#: and missed by the least when it does not. Soft, deliberately: a roster
#: that misses a target by a hair to avoid conceding a category outright
#: is the better roster, and a hard floor would refuse it.
TARGET_WEIGHT = 10.0

#: Piecewise-linear segments per counting category. Sixty holds the
#: interpolation error under 0.001 of probability.
DEFAULT_BREAKPOINTS = 60

#: Grid step for the shooting rates. The model floors a rate to the grid, so
#: this is the most it can under-credit a percentage category.
DEFAULT_RATE_STEP = 0.002


class ExactError(RuntimeError):
    """The solver returned no roster: out of time before any, or the hard
    constraints contradict each other."""


@dataclass(frozen=True)
class Target:
    """A floor on one category's win probability."""

    abbreviation: str
    win_probability: float


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

    def solve(self, time_limit: float, gap: float) -> Solved:
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
        x = None if result.x is None else np.asarray(result.x, dtype=float)
        objective = -float(result.fun) if x is not None else float("nan")
        mip_gap = getattr(result, "mip_gap", None)
        return Solved(
            x=x,
            status=str(result.message),
            optimal=int(result.status) == 0,
            seconds=elapsed,
            objective=objective,
            gap=None if mip_gap is None else float(mip_gap),
        )


@dataclass(frozen=True)
class Solved:
    x: np.ndarray | None
    status: str
    optimal: bool
    seconds: float
    objective: float
    gap: float | None


@dataclass
class Built:
    model: Model
    #: Column of each candidate's x_i, in candidate order.
    x: list[int]
    #: The largest interpolation error of any counting category's curve.
    breakpoint_error: float
    #: The most probability the rate grid can floor away in one category.
    rate_error: float
    #: Whether the spending shape was kept.
    shaped: bool


def build_model(
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    budget: int,
    roster_slots: int,
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    shape: Sequence[int] | None = None,
    exempt: Iterable[int] = (),
    punt: Iterable[str] = (),
    locked: Iterable[int] = (),
    excluded: Iterable[int] = (),
    targets: Iterable[Target] = (),
    maximize: Iterable[str] | None = None,
    target_weight: float = TARGET_WEIGHT,
    breakpoints: int = DEFAULT_BREAKPOINTS,
    rate_step: float = DEFAULT_RATE_STEP,
) -> Built:
    """The whole problem as columns and rows, ready to solve."""
    keep = frozenset(locked)
    gone = frozenset(excluded)
    if keep & gone:
        raise ExactError(f"players both locked and excluded: {sorted(keep & gone)}")
    spared = frozenset(exempt)
    punted = frozenset(punt)
    counted = frozenset(maximize) if maximize is not None else None
    limits = limits or {}

    m = Model()
    x = [
        m.var(
            1.0 if c.player_id in keep else 0.0,
            0.0 if c.player_id in gone else 1.0,
            binary=True,
        )
        for c in candidates
    ]
    m.row(dict.fromkeys(x, 1.0), roster_slots, roster_slots)
    m.row({x[i]: float(c.price) for i, c in enumerate(candidates)}, hi=budget)

    # Lineup: each slot type takes as many starters as the lineup has of it.
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

    # Spending shape, Hall's condition on the sorted places. Dropped when the
    # locked players alone break it, as the local search drops it.
    shaped = False
    if shape:
        fixed = [c for c in candidates if c.player_id in keep]
        if within_shape(fixed, shape, spared):
            shaped = True
            places = sorted(shape, reverse=True)
            for k, cap in enumerate(places, start=1):
                m.row(
                    {
                        x[i]: 1.0
                        for i, c in enumerate(candidates)
                        if c.price > cap and c.player_id not in spared
                    },
                    hi=k - 1,
                )

    probability: dict[str, dict[int, float]] = {}
    constant: dict[str, float] = {}
    worst_pwl = 0.0
    worst_rate = 0.0
    for d in distributions:
        cat = d.abbreviation
        if cat in PERCENTAGE_COMPONENTS:
            coefs, base, error = _rate_ladder(m, x, candidates, d, roster_slots, rate_step)
            worst_rate = max(worst_rate, error)
        else:
            coefs, base, error = _counting_curve(m, x, candidates, d, roster_slots, breakpoints)
            worst_pwl = max(worst_pwl, error)
        probability[cat] = coefs
        constant[cat] = base

    # Objective: the weighted probabilities, less the concede charge, less
    # the target shortfalls. The constant parts of the probabilities ride on
    # one fixed column so the model's objective is the whole value.
    offset = 0.0
    shortfall_total: dict[int, float] = {}
    for cat, coefs in probability.items():
        if cat in punted:
            continue
        weight = 1.0 if counted is None or cat in counted else 0.0
        if weight:
            for var, coef in coefs.items():
                m.cost[var] += weight * coef
            offset += weight * constant[cat]
        s = m.var(0.0, CONCEDE_THRESHOLD)
        # s >= 0.25 - p  <=>  s + p >= 0.25
        m.row({s: 1.0, **coefs}, lo=CONCEDE_THRESHOLD - constant[cat])
        shortfall_total[s] = 1.0 / CONCEDE_THRESHOLD
    if shortfall_total:
        most = float(len(shortfall_total))
        total_s = m.var(0.0, most)
        m.row({total_s: -1.0, **shortfall_total}, 0.0, 0.0)
        # penalty = lambda * (S - e), e = max(0, S - 1) through binary b.
        excess = m.var(0.0, most)
        b = m.var(binary=True)
        m.cost[total_s] -= CONCEDE_PENALTY
        m.cost[excess] += CONCEDE_PENALTY
        m.row({excess: 1.0, b: -most}, hi=0.0)
        m.row({excess: 1.0, total_s: -1.0, b: most}, hi=most - 1.0)
    for target in targets:
        if target.abbreviation not in probability:
            raise ExactError(f"no distribution for target category {target.abbreviation!r}")
        s = m.var(0.0, 1.0, obj=-target_weight)
        # s >= p_target - p  <=>  s + p >= p_target
        m.row(
            {s: 1.0, **probability[target.abbreviation]},
            lo=target.win_probability - constant[target.abbreviation],
        )
    m.var(offset, offset, obj=1.0)
    return Built(m, x, worst_pwl, worst_rate, shaped)


def _counting_curve(
    m: Model,
    x: Sequence[int],
    candidates: Sequence[Candidate],
    d: CategoryDistribution,
    roster_slots: int,
    breakpoints: int,
) -> tuple[dict[int, float], float, float]:
    """Win probability as a piecewise-linear function of the roster's total."""
    cat = d.abbreviation
    weekly = [c.weekly.get(cat, 0.0) for c in candidates]
    low = sum(sorted(weekly)[:roster_slots])
    high = sum(sorted(weekly, reverse=True)[:roster_slots])
    step = (high - low) / breakpoints if high > low else 1.0
    points = [low + k * step for k in range(breakpoints + 1)]
    deltas = [m.var() for _ in range(breakpoints)]
    switches = [m.var(binary=True) for _ in range(breakpoints - 1)]
    for k, u in enumerate(switches):
        m.row({deltas[k + 1]: 1.0, u: -1.0}, hi=0.0)
        m.row({u: 1.0, deltas[k]: -1.0}, hi=0.0)
    total_row = {x[i]: weekly[i] for i in range(len(candidates))}
    for delta in deltas:
        total_row[delta] = -step
    m.row(total_row, low, low)
    f = d.win_probability
    coefs = {delta: f(points[k + 1]) - f(points[k]) for k, delta in enumerate(deltas)}
    worst = 0.0
    for k in range(breakpoints):
        for frac in (0.25, 0.5, 0.75):
            t = points[k] + frac * step
            linear = f(points[k]) + frac * (f(points[k + 1]) - f(points[k]))
            worst = max(worst, abs(f(t) - linear))
    return coefs, f(low), worst


def _rate_ladder(
    m: Model,
    x: Sequence[int],
    candidates: Sequence[Candidate],
    d: CategoryDistribution,
    roster_slots: int,
    rate_step: float,
) -> tuple[dict[int, float], float, float]:
    """Win probability of a shooting percentage, floored to a grid of rates."""
    made_key, attempted_key = PERCENTAGE_COMPONENTS[d.abbreviation]
    made = [c.weekly.get(made_key, 0.0) for c in candidates]
    attempted = [c.weekly.get(attempted_key, 0.0) for c in candidates]
    most_attempts = sum(sorted(attempted, reverse=True)[:roster_slots])
    rates = sorted(c_m / c_a for c_m, c_a in zip(made, attempted, strict=True) if c_a > 0)
    if not rates:
        return {}, d.win_probability(0.0), 0.0
    lo = (rates[len(rates) // 50] // rate_step) * rate_step
    hi = -(-rates[-1] // rate_step) * rate_step
    grid = [lo + j * rate_step for j in range(round((hi - lo) / rate_step) + 1)]
    base = d.win_probability(lo - rate_step)
    coefs: dict[int, float] = {}
    previous_value = base
    previous_z: int | None = None
    worst = 0.0
    for t in grid:
        z = m.var(binary=True)
        big = t * most_attempts + 1.0
        # z = 1 forces made - t * attempted >= 0; z = 0 leaves it free.
        row = {x[i]: made[i] - t * attempted[i] for i in range(len(candidates))}
        row[z] = -big
        m.row(row, lo=-big)
        if previous_z is not None:
            m.row({previous_z: 1.0, z: -1.0}, lo=0.0)
        value = d.win_probability(t)
        coefs[z] = value - previous_value
        worst = max(worst, abs(value - previous_value))
        previous_value = value
        previous_z = z
    return coefs, base, worst


@dataclass(frozen=True)
class ExactPlan:
    """A roster from the exact solver, re-scored with the real scorer."""

    players: tuple[Candidate, ...]
    cost: int
    totals: dict[str, float]
    win_probability: dict[str, float]
    #: Expected categories won a week by `optimizer.score`, punts honoured.
    expected_wins: float
    #: Per target, how far under it the roster's real probability sits; zero
    #: when the target is met.
    shortfall: dict[str, float]
    #: The model's own objective, which includes the target penalties.
    objective: float
    status: str
    optimal: bool
    gap: float | None
    seconds: float
    shaped: bool
    columns: int
    rows: int
    breakpoint_error: float
    rate_error: float

    @property
    def player_ids(self) -> frozenset[int]:
        return frozenset(p.player_id for p in self.players)

    @property
    def targets_met(self) -> bool:
        return all(v <= 1e-9 for v in self.shortfall.values())


def solve_exact(
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    budget: int,
    roster_slots: int,
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    shape: Sequence[int] | None = None,
    exempt: Iterable[int] = (),
    punt: Iterable[str] = (),
    locked: Iterable[int] = (),
    excluded: Iterable[int] = (),
    targets: Iterable[Target] = (),
    maximize: Iterable[str] | None = None,
    target_weight: float = TARGET_WEIGHT,
    breakpoints: int = DEFAULT_BREAKPOINTS,
    rate_step: float = DEFAULT_RATE_STEP,
    time_limit: float = 600.0,
    gap: float = 0.002,
) -> ExactPlan:
    """The roster that maximises the objective, targets included.

    `time_limit` is in seconds and `gap` the relative optimality gap HiGHS
    may stop at. Within the limit the answer is proven; past it the best
    roster found so far comes back with `optimal` false, and if none was
    found at all `ExactError` is raised, which is the caller's cue to fall
    back to the local search.
    """
    wanted = tuple(targets)
    punted = frozenset(punt)
    built = build_model(
        candidates,
        distributions,
        budget=budget,
        roster_slots=roster_slots,
        lineup=lineup,
        limits=limits,
        shape=shape,
        exempt=exempt,
        punt=punted,
        locked=locked,
        excluded=excluded,
        targets=wanted,
        maximize=maximize,
        target_weight=target_weight,
        breakpoints=breakpoints,
        rate_step=rate_step,
    )
    solved = built.model.solve(time_limit, gap)
    if solved.x is None:
        raise ExactError(f"no roster from the exact solver: {solved.status}")
    chosen = tuple(candidates[i] for i, var in enumerate(built.x) if solved.x[var] > 0.5)
    if not fieldable(chosen, lineup, limits):
        raise ExactError("the exact solver returned a roster the lineup cannot field")
    categories = [d.abbreviation for d in distributions]
    totals = roster_totals(chosen, categories)
    expected, probabilities = score(totals, distributions, punted)
    return ExactPlan(
        players=chosen,
        cost=sum(c.price for c in chosen),
        totals=totals,
        win_probability=probabilities,
        expected_wins=expected,
        shortfall={
            t.abbreviation: max(0.0, t.win_probability - probabilities[t.abbreviation])
            for t in wanted
        },
        objective=solved.objective,
        status=solved.status,
        optimal=solved.optimal,
        gap=solved.gap,
        seconds=solved.seconds,
        shaped=built.shaped,
        columns=len(built.model.lb),
        rows=len(built.model.rows),
        breakpoint_error=built.breakpoint_error,
        rate_error=built.rate_error,
    )
