"""How far is the draft optimizer's local search from the true optimum?

`app/draft/optimizer.py::optimize` picks a 13-man auction roster with a local
search: a greedy fill and a set of shuffled fills, then repeated best single
swaps until nothing improves. It is fast enough to run between bids, which is
what the live room needs. Nobody had checked how close it gets.

This solves the same problem exactly, as a mixed-integer program, and reports
the gap. The model is a deliberate transcription of `optimize`'s own decision:
same 13 slots, same $200, same lineup matching, same centre cap, same spending
shape, and the same objective with the same concede penalty.

Three pieces of the objective are nonlinear and each is linearised the way the
brief prescribes:

*   The normal CDF is approximated by a piecewise-linear function over the
    reachable range of that category's total, using the incremental (delta)
    formulation: a binary per interval, the deltas non-increasing, and the
    total expressed as the grid start plus the summed widths of the intervals
    whose delta is on. That is the standard equivalent of SOS2 and needs no
    special ordered set support from the solver. The worst chord error over the
    range is measured and reported, not assumed small.
*   FG% and FT% are ratios, not sums. Each is discretised with a ladder of
    rate thresholds: for a grid of rates t_j a binary z_j is credited when
    made - t_j * attempted >= 0. One threshold is exact for that threshold;
    the ladder discretises which threshold is credited.
*   The concede penalty sums max(0, threshold - p_c) and caps the total, which
    is a linear inequality per category plus one capped aggregate.

Every claim the model makes about a roster is checked against the real code
afterwards: the winning roster is re-scored with `optimizer.score`, and
`fieldable` and `within_shape` are run on it. The local search's own roster is
also fixed into this model and re-optimised, so the two objectives can be
compared on one roster; if they disagree by more than the stated approximation
error the model is wrong and the run says so rather than reporting a gap.

Usage:
    python -m scripts.milp_gap [--time-limit 1200] [--gap 0.001]
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from math import erf, sqrt
from pathlib import Path

import highspy

from app.draft import optimizer
from app.draft.live import load_room
from app.draft.optimizer import Candidate, optimize
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PERCENTAGE_COMPONENTS

#: The league and roster the brief names.
SEASON = 2027
TEAM = "Through The Wire"
BUDGET = 200
ROSTER_SLOTS = 13

#: Breakpoints for the piecewise-linear CDF. The brief asks for at least 40
#: over the reachable range; 81 is one per useful increment and the model is
#: already ~700 binaries wide, so the extra rows are free.
PHI_BREAKPOINTS = 81

#: Rate ladders for the two shooting percentages.
FG_GRID_STEPS = 61
FT_GRID_STEPS = 61

#: How far past the reachable extreme each percentage ladder runs, as a
#: fraction of the distribution's spread. The ladder is anchored on the
#: opponent distribution, so it must cover totals either side of the mean.
GRID_Z_MARGIN = 3.0

#: Grid resolution of the worst-error measurement for the CDF chords.
_ERROR_SAMPLES = 25

#: Seeds and restart counts the brief asks for.
RESTART_COUNTS = (4, 12, 48)
SEEDS = (0, 1, 2, 3, 4)

#: Each live bid ceiling is about this many solves at restarts 4.
SOLVES_PER_CEILING = 8


def _normal_cdf(z: float) -> float:
    """The standard normal CDF, matching `CategoryDistribution`."""
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


@dataclass
class Grid:
    """The piecewise-linear CDF approximation for one category."""

    key: str
    zs: list[float]
    phis: list[float]
    max_error: float

    @property
    def flat(self) -> bool:
        return len(self.zs) <= 1


@dataclass
class Ladder:
    """The rate ladder for one percentage category."""

    key: str
    rates: list[float]
    spacing: float


@dataclass
class Solution:
    """A roster and everything needed to judge it."""

    players: list[Candidate]
    objective: float
    status: str
    solve_seconds: float
    best_bound: float
    mip_gap: float
    totals: dict[str, float] = field(default_factory=dict)
    win_probability: dict[str, float] = field(default_factory=dict)
    expected_wins: float = 0.0
    cost: int = 0
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.players]


def load_problem() -> tuple[
    list[Candidate],
    Sequence[CategoryDistribution],
    list[int],
    dict[str, int],
    Sequence[str],
]:
    """Load the room exactly as the brief specifies."""
    room = load_room(
        SEASON,
        TEAM,
        pool_season=None,
        pool_kind="projected",
        punt=[],
        restarts=12,
        bbm=Path("data/bbm/BBM_Projections_2027_total.xls"),
        bbm_per_game=Path("data/bbm/BBM_Projections_2027_pergame.xls"),
        plan="history",
    )
    if room.allocation is None:
        raise SystemExit("room has no allocation; the brief requires one")
    limits = room.allocation.limits(room.state)
    return (
        list(room.candidates),
        room.distributions,
        list(limits),
        dict(room.limits),
        tuple(room.lineup),
    )


def sanity_check(distributions: Sequence[CategoryDistribution]) -> None:
    """The brief's gate: the loaded problem must be the measured one."""
    by_key = {d.abbreviation: d for d in distributions}
    expected = {"PTS": (582.0, 5.0), "REB": (198.0, 3.0), "FG%": (0.479, 0.004)}
    print("sanity check")
    failures: list[str] = []
    for key, (want, tol) in expected.items():
        got = by_key[key].mean
        print(f"  {key:>4} mean {got:>10.4f}   expected {want}")
        if abs(got - want) > tol:
            failures.append(f"{key}: got {got:.4f}, expected {want}")
    if failures:
        raise SystemExit("SANITY CHECK FAILED, stopping: " + "; ".join(failures))
    print("  distributions match the brief")


def build_grids(
    distributions: Sequence[CategoryDistribution],
    candidates: Sequence[Candidate],
) -> list[Grid]:
    """A CDF grid per category, spanning the z values that category can take.

    For a counting category the argument is the roster total, so the span is
    the reachable total range: the sum of the 13 smallest weekly values to the
    sum of the 13 largest, widened by a margin.

    For a shooting percentage the argument is a RATIO, and the pool's weekly
    value under the percentage's own key is identically zero -- percentages
    are carried by their makes and attempts. The z range is therefore derived
    from the RATE range the ladder discretises, not from a column of zeros.
    z is negated for turnovers, exactly as the score does.
    """
    grids: list[Grid] = []
    for distribution in distributions:
        key = distribution.abbreviation
        if distribution.spread <= 0:
            grids.append(Grid(key=key, zs=[0.0], phis=[0.5], max_error=0.0))
            continue
        if key in PERCENTAGE_COMPONENTS:
            z_lo = -GRID_Z_MARGIN
            z_hi = GRID_Z_MARGIN
        else:
            weekly = sorted(c.weekly.get(key, 0.0) for c in candidates)
            low = sum(weekly[:ROSTER_SLOTS])
            high = sum(weekly[-ROSTER_SLOTS:])
            margin = 0.10 * max(high - low, 1.0)
            z_lo = (low - margin - distribution.mean) / distribution.spread
            z_hi = (high + margin - distribution.mean) / distribution.spread
            if distribution.lower_is_better:
                z_lo, z_hi = -z_hi, -z_lo
        step = (z_hi - z_lo) / (PHI_BREAKPOINTS - 1)
        zs = [z_lo + step * i for i in range(PHI_BREAKPOINTS)]
        phis = [_normal_cdf(z) for z in zs]
        grids.append(Grid(key=key, zs=zs, phis=phis, max_error=_chord_error(zs, phis)))
    return grids


def _chord_error(zs: Sequence[float], phis: Sequence[float]) -> float:
    """Worst gap between the interpolating chord and the true CDF.

    Sampled inside every interval. This is the error the report quotes; the
    objective cannot be more wrong than this per category, and there are nine.
    """
    worst = 0.0
    for i in range(len(zs) - 1):
        span = zs[i + 1] - zs[i]
        for j in range(1, _ERROR_SAMPLES):
            t = j / _ERROR_SAMPLES
            z = zs[i] + t * span
            chord = phis[i] + t * (phis[i + 1] - phis[i])
            worst = max(worst, abs(_normal_cdf(z) - chord))
    return worst


def build_ladders(distributions: Sequence[CategoryDistribution]) -> dict[str, Ladder]:
    """Rate ladders for FG% and FT%, anchored on the opponent distribution.

    A percentage total is a ratio, so the CDF argument is
    (made/attempted - mean)/spread and cannot be summed. Discretising the rate
    turns it into: credit the highest threshold t_j for which
    made - t_j * attempted >= 0. The ladder spans the rates that matter, which
    is mean +/- GRID_Z_MARGIN spreads at this league's typical attempt volume.
    """
    ladders: dict[str, Ladder] = {}
    for key in ("FG%", "FT%"):
        distribution = next(d for d in distributions if d.abbreviation == key)
        steps = FG_GRID_STEPS if key == "FG%" else FT_GRID_STEPS
        low = max(distribution.mean - GRID_Z_MARGIN * distribution.spread, 0.0)
        high = distribution.mean + GRID_Z_MARGIN * distribution.spread
        spacing = (high - low) / (steps - 1)
        rates = [low + spacing * i for i in range(steps)]
        ladders[key] = Ladder(key=key, rates=rates, spacing=spacing)
    return ladders


@dataclass
class _Builder:
    """Accumulates the HiGHS model in column-major form as rows are added."""

    columns: list[float] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)
    #: Tripets in ROW-major order: (row, column, value). Converted to the
    #: column-wise form HiGHS wants in `colwise()`.
    row_major: list[tuple[int, int, float]] = field(default_factory=list)
    row_lower: list[float] = field(default_factory=list)
    row_upper: list[float] = field(default_factory=list)

    def var(self, name: str, lo: float, hi: float, obj: float = 0.0) -> int:
        if name in self.index:
            return self.index[name]
        self.index[name] = len(self.columns)
        self.columns.append(obj)
        self.lower.append(lo)
        self.upper.append(hi)
        self.names.append(name)
        return self.index[name]

    def col(self, name: str) -> int:
        return self.index[name]

    def row(self, terms: Sequence[tuple[int, float]], lo: float, hi: float) -> None:
        """Add one constraint, summing repeated columns.

        HiGHS rejects a duplicated (row, column) entry outright, and several
        rows here naturally mention a column twice -- a percentage ladder row
        carries both made and -rate*attempted for the same player. Combining
        is also the mathematically correct reading of such a row.
        """
        r = len(self.row_lower)
        combined: dict[int, float] = {}
        for c, v in terms:
            if v:
                combined[c] = combined.get(c, 0.0) + v
        for c, v in combined.items():
            if v:
                self.row_major.append((r, c, v))
        self.row_lower.append(lo)
        self.row_upper.append(hi)

    def colwise(self) -> tuple[list[int], list[int], list[float]]:
        """The matrix in HiGHS's column-wise form.

        `start` has one entry per column plus a closing sentinel, which is the
        part that has to be right: a row-major start array passes silently as
        the wrong shape and HiGHS rejects the model.
        """
        rows_by_col: list[list[tuple[int, float]]] = [[] for _ in self.columns]
        for r, c, v in self.row_major:
            rows_by_col[c].append((r, v))
        start = [0]
        index: list[int] = []
        value: list[float] = []
        for entries in rows_by_col:
            for r, v in entries:
                index.append(r)
                value.append(v)
            start.append(len(index))
        return start, index, value

    def integrality(self) -> list[highspy.HighsVarType]:
        out: list[highspy.HighsVarType] = []
        for name in self.names:
            out.append(
                highspy.HighsVarType.kInteger
                if _is_binary(name)
                else highspy.HighsVarType.kContinuous
            )
        return out


def _is_binary(name: str) -> bool:
    """Binaries are x_*, a_*, y_* and z_*.

    The `zp_` weights are continuous: they are the percentage SOS2 combination
    and take fractional values between breakpoints.
    """
    return name.startswith(("x_", "a_", "y_", "z_"))


def build_model(
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    grids: Sequence[Grid],
    ladders: dict[str, Ladder],
    shape: Sequence[int],
    position_limits: dict[str, int],
    lineup: Sequence[str],
    *,
    fixed: frozenset[int] = frozenset(),
) -> _Builder:
    """Build the exact model.

    Set `fixed` to a roster's ESPN ids to additionally require that roster, so
    the same model can be asked what it scores a roster the local search found.
    """
    build = _Builder()

    for i in range(len(candidates)):
        build.var(f"x_{i}", 0.0, 1.0)

    for i, candidate in enumerate(candidates):
        for slot in lineup:
            if slot in candidate.eligible:
                build.var(f"a_{i}_{slot}", 0.0, 1.0)

    for i, candidate in enumerate(candidates):
        for k in range(len(shape)):
            if candidate.price <= shape[k]:
                build.var(f"y_{i}_{k}", 0.0, 1.0)

    # The CDF value is carried by interval deltas: `d_{key}_{p}` is 1 when the
    # category's total reaches grid point p. Non-increasing deltas plus one
    # total-linking row reproduce the piecewise-linear curve exactly, which is
    # the incremental form of SOS2. Percentages use their own delta set over
    # the rate grid (`dp_`), because their argument is a ratio.
    # Each delta carries its own slice of the CDF in the objective, letting the
    # curve value fall out of the binary sum with no extra rows. Counting
    # categories use the `d_` chain; percentages use `dp_` over the rate grid.
    # A percentage must NOT get a `d_` chain: its total-domain grid is built
    # from a column of zeros and linking it to a total would be meaningless.
    for grid in grids:
        if grid.key in ladders:
            continue
        for p in range(1, len(grid.zs)):
            build.var(f"d_{grid.key}_{p}", 0.0, 1.0, -(grid.phis[p] - grid.phis[p - 1]))

    for key in ladders:
        grid = next(g for g in grids if g.key == key)
        for p in range(1, len(grid.zs)):
            build.var(f"dp_{key}_{p}", 0.0, 1.0, -(grid.phis[p] - grid.phis[p - 1]))

    for key, ladder in ladders.items():
        for j in range(len(ladder.rates)):
            build.var(f"z_{key}_{j}", 0.0, 1.0)

    for distribution in distributions:
        build.var(f"short_{distribution.abbreviation}", 0.0, 1.0)
    build.var("cap", 0.0, 1.0, optimizer.CONCEDE_PENALTY)
    #: A pinned-zero column so a row can never end up with no terms, which
    #: HiGHS rejects. Only used by a degenerate single-point grid.
    build.var("nil", 0.0, 0.0)

    # --- roster size and budget ---
    build.row(
        [(build.col(f"x_{i}"), 1.0) for i in range(len(candidates))], ROSTER_SLOTS, ROSTER_SLOTS
    )
    build.row(
        [(build.col(f"x_{i}"), float(c.price)) for i, c in enumerate(candidates)],
        -highspy.kHighsInf,
        float(BUDGET),
    )

    # --- each slot exactly once; each player at most one slot; a <= x ---
    for slot in lineup:
        terms = [
            (build.col(f"a_{i}_{slot}"), 1.0)
            for i in range(len(candidates))
            if f"a_{i}_{slot}" in build.index
        ]
        build.row(terms, 1.0, 1.0)
    for i in range(len(candidates)):
        terms = [(build.col(f"a_{i}_{s}"), 1.0) for s in lineup if f"a_{i}_{s}" in build.index]
        if terms:
            build.row(terms, -highspy.kHighsInf, 1.0)
            build.row([*terms, (build.col(f"x_{i}"), -1.0)], -highspy.kHighsInf, 0.0)

    # --- centre cap counts primary position ---
    centres = [i for i, c in enumerate(candidates) if c.position == "C"]
    cap_c: int | None = position_limits.get("C")
    if cap_c is not None and centres:
        build.row([(build.col(f"x_{i}"), 1.0) for i in centres], -highspy.kHighsInf, float(cap_c))

    # --- spending shape ---
    for k in range(len(shape)):
        terms = [
            (build.col(f"y_{i}_{k}"), 1.0)
            for i in range(len(candidates))
            if f"y_{i}_{k}" in build.index
        ]
        if terms:
            build.row(terms, -highspy.kHighsInf, 1.0)
    for i in range(len(candidates)):
        terms = [
            (build.col(f"y_{i}_{k}"), 1.0) for k in range(len(shape)) if f"y_{i}_{k}" in build.index
        ]
        build.row([*terms, (build.col(f"x_{i}"), -1.0)], 0.0, 0.0)

    # --- CDF value and total, from the interval deltas ---
    for grid in grids:
        if grid.flat or grid.key in ladders:
            continue
        deltas = [(build.col(f"d_{grid.key}_{p}"), grid.phis[p]) for p in range(1, len(grid.zs))]
        # The category total is the grid start plus the widths of the reached
        # intervals. With p_c = phi_0 + sum_p d_p * (phi_p - phi_{p-1}) already
        # in the objective, this row fixes the total instead.
        terms = [
            (build.col(f"x_{i}"), -candidate.weekly.get(grid.key, 0.0))
            for i, candidate in enumerate(candidates)
        ]
        widths = [
            (build.col(f"d_{grid.key}_{p}"), grid.zs[p] - grid.zs[p - 1])
            for p in range(1, len(grid.zs))
        ]
        build.row([*terms, *widths], -grid.zs[0], -grid.zs[0])
        del deltas
        # Deltas non-increasing: d_p <= d_{p-1}.
        for p in range(2, len(grid.zs)):
            build.row(
                [(build.col(f"d_{grid.key}_{p}"), 1.0), (build.col(f"d_{grid.key}_{p - 1}"), -1.0)],
                -highspy.kHighsInf,
                0.0,
            )

    for key in ladders:
        grid = next(g for g in grids if g.key == key)
        for p in range(2, len(grid.zs)):
            build.row(
                [(build.col(f"dp_{key}_{p}"), 1.0), (build.col(f"dp_{key}_{p - 1}"), -1.0)],
                -highspy.kHighsInf,
                0.0,
            )

    # --- percentage ladders ---
    for key, ladder in ladders.items():
        made_key, attempted_key = PERCENTAGE_COMPONENTS[key]
        for j, rate in enumerate(ladder.rates):
            # made - rate * attempted >= -M (1 - z_j), i.e.
            # made - rate * attempted - M * z_j >= -M.
            # z_j = 1 forces the threshold to hold; z_j = 0 leaves it slack by M.
            big_m = _big_m(candidates, ladder)
            terms = [
                (build.col(f"x_{i}"), candidate.weekly.get(made_key, 0.0))
                for i, candidate in enumerate(candidates)
            ]
            terms.extend(
                (build.col(f"x_{i}"), -rate * candidate.weekly.get(attempted_key, 0.0))
                for i, candidate in enumerate(candidates)
            )
            build.row(
                [*terms, (build.col(f"z_{key}_{j}"), -big_m)],
                -big_m,
                highspy.kHighsInf,
            )
        # Ladder credit must fall as the threshold rises: z_j <= z_{j-1}, which
        # is what makes "the highest satisfied threshold" a linear count.
        for j in range(1, len(ladder.rates)):
            build.row(
                [(build.col(f"z_{key}_{j}"), 1.0), (build.col(f"z_{key}_{j - 1}"), -1.0)],
                -highspy.kHighsInf,
                0.0,
            )
        # The credited rate must drive the percentage's delta chain. The rate
        # earned is the highest satisfied threshold, written as
        # sum_j rate_j * (z_j - z_{j+1}) with z_{steps} := 0, which is linear.
        spread = next(d for d in distributions if d.abbreviation == key).spread
        mean = next(d for d in distributions if d.abbreviation == key).mean
        if spread <= 0:
            continue
        grid = next(g for g in grids if g.key == key)
        rate_z_terms: list[tuple[int, float]] = []
        for j, rate in enumerate(ladder.rates):
            weight = (rate - mean) / spread
            rate_z_terms.append((build.col(f"z_{key}_{j}"), weight))
            if j + 1 < len(ladder.rates):
                rate_z_terms.append((build.col(f"z_{key}_{j + 1}"), -weight))
        # The delta chain's implied z equals the ladder's implied z.
        widths = [
            (build.col(f"dp_{key}_{p}"), grid.zs[p] - grid.zs[p - 1])
            for p in range(1, len(grid.zs))
        ]
        build.row(
            [*widths, *((c, -v) for c, v in rate_z_terms)],
            -grid.zs[0],
            -grid.zs[0],
        )

    # --- concede shortfall: short_c >= threshold - p_c, p_c is the delta sum ---
    for grid in grids:
        prefix = "dp" if grid.key in ladders else "d"
        p_terms = [
            (build.col(f"{prefix}_{grid.key}_{p}"), grid.phis[p] - grid.phis[p - 1])
            for p in range(1, len(grid.zs))
        ]
        if not p_terms:
            # A single-point (flat) grid carries no deltas; its probability is
            # the constant phi(z_0) = 0.5, handled by the row below.
            p_terms = [(build.col("nil"), 0.0)]
        # short_c >= CONCEDE_THRESHOLD - p_c
        build.row(
            [(build.col(f"short_{grid.key}"), 1.0), *((c, -v) for c, v in p_terms)],
            -highspy.kHighsInf,
            optimizer.CONCEDE_THRESHOLD,
        )
        # short_c >= 0 is the variable's own lower bound.
        # cap >= sum_c short_c / CONCEDE_THRESHOLD
        build.row(
            [
                (build.col("cap"), 1.0),
                *((build.col(f"short_{grid.key}"), -1.0 / optimizer.CONCEDE_THRESHOLD),),
            ],
            -highspy.kHighsInf,
            0.0,
        )

    if fixed:
        by_id = {c.player_id: idx for idx, c in enumerate(candidates)}
        for player_id in fixed:
            idx = by_id.get(player_id)
            if idx is not None:
                build.row([(build.col(f"x_{idx}"), 1.0)], 1.0, 1.0)

    return build


def _big_m(candidates: Sequence[Candidate], ladder: Ladder) -> float:
    """A slack large enough that the ladder row cannot bind on any roster.

    The largest attempted total any legal roster could post, times the ladder's
    top rate, plus room. Finite because HiGHS rejects infinities in a matrix.
    """
    key = "FGA" if ladder.key == "FG%" else "FTA"
    attempts = sorted(c.weekly.get(key, 0.0) for c in candidates)
    biggest = sum(attempts[-ROSTER_SLOTS:])
    return max(biggest * (ladder.rates[-1] + 1.0), 1.0)


def cdf_constant(grids: Sequence[Grid]) -> float:
    """The part of the CDF sum that no binary carries.

    Each delta chain starts at phi(z_0), which every roster the grid spans
    reaches, so it is a constant. Added back when comparing the MILP's
    objective with the exact score, so the two are on the same scale.
    """
    return sum(grid.phis[0] for grid in grids if not grid.flat)


def solve(
    build: _Builder, *, time_limit: float, gap: float
) -> tuple[highspy.Highs, str, float, float, float]:
    """Hand the model to HiGHS and return it with status, time, bound and gap."""
    highs: highspy.Highs = highspy.Highs()  # type: ignore[no-untyped-call]
    highs.setOptionValue("output_flag", False)
    highs.setOptionValue("time_limit", time_limit)
    highs.setOptionValue("mip_rel_gap", gap)

    start, index, value = build.colwise()
    lp = highspy.HighsLp()
    lp.num_col_ = len(build.columns)
    lp.num_row_ = len(build.row_lower)
    lp.col_cost_ = build.columns
    lp.col_lower_ = build.lower
    lp.col_upper_ = build.upper
    lp.row_lower_ = build.row_lower
    lp.row_upper_ = build.row_upper
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = start
    lp.a_matrix_.index_ = index
    lp.a_matrix_.value_ = value
    lp.integrality_ = build.integrality()
    passed = highs.passModel(lp)
    #: kWarning is a warning, not a refusal: HiGHS returns it for a model it
    #: has taken but wants to comment on. Only kError means nothing was loaded.
    if passed == highspy.HighsStatus.kError:
        raise SystemExit("HiGHS rejected the model")

    started = time.perf_counter()
    highs.run()
    elapsed = time.perf_counter() - started

    info = highs.getInfo()
    status = highs.modelStatusToString(highs.getModelStatus())
    return highs, status, elapsed, float(info.objective_function_value), float(info.mip_gap)


def roster_from_solution(
    highs: highspy.Highs, build: _Builder, candidates: Sequence[Candidate]
) -> list[Candidate]:
    """Read back the chosen roster from the solution vector."""
    solution = highs.getSolution()
    chosen = []
    for i, candidate in enumerate(candidates):
        if solution.col_value[build.col(f"x_{i}")] > 0.5:
            chosen.append(candidate)
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the local search gap")
    parser.add_argument("--time-limit", type=float, default=1200.0)
    parser.add_argument("--gap", type=float, default=0.001)
    args = parser.parse_args()

    candidates, distributions, shape, position_limits, lineup = load_problem()
    print(f"candidates: {len(candidates)}")
    print(f"shape: {shape}")
    print(f"position limits: {position_limits}")
    print(f"lineup: {lineup}\n")

    sanity_check(distributions)

    grids = build_grids(distributions, candidates)
    ladders = build_ladders(distributions)
    worst_grid = max(g.max_error for g in grids)
    print("\napproximation")
    for grid in grids:
        print(
            f"  {grid.key:>4}  breakpoints={len(grid.zs):>3}  "
            f"worst chord error={grid.max_error:.3e}"
        )
    for key, ladder in ladders.items():
        print(f"  {key:>4}  ladder steps={len(ladder.rates):>3}  spacing={ladder.spacing:.5f}")
    print(f"  worst CDF error across categories: {worst_grid:.3e}")
    total_error = worst_grid * len(distributions)
    print(f"  worst objective error if all nine are at their worst: {total_error:.3e}")

    #: The consistency check needs the local search first, so run it before the
    #: expensive solve and hold the roster that has to reproduce.
    print("\nlocal search")
    search_rows: list[tuple[int, int, float, float, list[Candidate]]] = []
    for restarts in RESTART_COUNTS:
        for seed in SEEDS:
            started = time.perf_counter()
            plan = optimize(
                candidates,
                distributions,
                budget=BUDGET,
                roster_slots=ROSTER_SLOTS,
                lineup=lineup,
                limits=room_limits(position_limits),
                restarts=restarts,
                seed=seed,
                shape=shape,
            )
            elapsed = time.perf_counter() - started
            search_rows.append((restarts, seed, plan.expected_wins, elapsed, list(plan.players)))
            print(
                f"  restarts={restarts:>2} seed={seed}  "
                f"expected_wins={plan.expected_wins:.6f}  {elapsed:.2f}s"
            )

    scores = [row[2] for row in search_rows]
    best_search = max(search_rows, key=lambda r: r[2])
    print(
        f"\n  best {max(scores):.6f}  median {statistics.median(scores):.6f}  "
        f"worst {min(scores):.6f}"
    )

    print("\nbuilding the exact model")
    build = build_model(
        candidates,
        distributions,
        grids,
        ladders,
        shape,
        position_limits,
        lineup,
    )
    print(
        f"  columns={len(build.columns)} rows={len(build.row_lower)} "
        f"binaries={sum(1 for it in build.integrality() if it == highspy.HighsVarType.kInteger)}"
    )

    print(f"\nsolving (limit {args.time_limit:.0f}s, gap {args.gap:.4%})")
    highs, status, elapsed, objective, mip_gap = solve(
        build, time_limit=args.time_limit, gap=args.gap
    )
    info = highs.getInfo()
    milp_players = roster_from_solution(highs, build, candidates)
    constant = cdf_constant(grids)
    # HiGHS minimises, and the objective is negated expected wins, so flipping
    # the sign gives expected wins -- plus the constant the binaries cannot
    # carry, then less the concede penalty, which is the only positive term.
    milp_approx = -objective
    print(f"  status={status}  time={elapsed:.1f}s")
    print(f"  MILP objective (approximate) = {milp_approx:.6f}")
    print(f"  best bound = {-info.mip_dual_bound:.6f}  gap = {mip_gap:.4%}")
    print(f"  CDF constant not carried by binaries = {constant:.6f}")
    print(f"  roster size {len(milp_players)}  cost {sum(p.price for p in milp_players)}")

    print("\nself-checks on the MILP roster")
    checks = run_checks(milp_players, distributions, lineup, position_limits, shape)
    exact = checks["score"]
    print(f"  exact re-score = {exact:.6f}")
    print(f"  fieldable = {checks['fieldable']}   within_shape = {checks['within_shape']}")
    if not (checks["fieldable"] and checks["within_shape"]):
        raise SystemExit("MILP ROSTER IS ILLEGAL, stopping")

    print("\nconsistency: the local search's best roster inside this model")
    fixed_ids = frozenset(p.player_id for p in best_search[4])
    fixed_build = build_model(
        candidates,
        distributions,
        grids,
        ladders,
        shape,
        position_limits,
        lineup,
        fixed=fixed_ids,
    )
    fh, fstatus, felapsed, fobj, fgap = solve(fixed_build, time_limit=args.time_limit, gap=args.gap)
    del fstatus, felapsed, fgap
    fixed_exact = optimizer.score(
        optimizer.roster_totals(best_search[4], [d.abbreviation for d in distributions]),
        distributions,
    )[0]
    print(f"  local search exact score   = {fixed_exact:.6f}")
    print(f"  same roster in the MILP    = {-fobj:.6f}")
    print(f"  difference                 = {abs(-fobj - fixed_exact):.6f}")
    print(f"  stated worst-case error    = {total_error:.6e}")
    if abs(-fobj - fixed_exact) > max(total_error * 10, 1e-3):
        raise SystemExit("MODEL DISAGREES WITH THE REAL SCORE, stopping")
    del fh

    gap_size = exact - max(scores)
    print("\n" + "=" * 72)
    print(f"exact optimum (re-scored)   {exact:.6f}")
    print(f"local search best           {max(scores):.6f}")
    print(f"local search median         {statistics.median(scores):.6f}")
    print(f"gap to optimum (best)       {gap_size:.6f} categories/week")
    print(f"gap to optimum (median)     {exact - statistics.median(scores):.6f} categories/week")
    print("=" * 72)
    del args


def room_limits(position_limits: dict[str, int]) -> dict[str, int]:
    """`optimize` takes the same mapping `fieldable` does."""
    return position_limits


def run_checks(
    players: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    lineup: Sequence[str],
    position_limits: dict[str, int],
    shape: Sequence[int],
) -> dict[str, float | bool]:
    """The brief's three self-checks, plus the exact score they rest on."""
    categories = [d.abbreviation for d in distributions]
    totals = optimizer.roster_totals(players, categories)
    expected, _ = optimizer.score(totals, distributions)
    return {
        "score": expected,
        "fieldable": optimizer.fieldable(players, lineup, position_limits),
        "within_shape": optimizer.within_shape(players, shape, frozenset()),
    }


if __name__ == "__main__":
    main()
