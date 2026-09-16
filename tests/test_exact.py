"""Exact solver tests.

Small pools, brute-forced. What matters is that the model reaches the roster
the real scorer says is best, that a target is hit when the board allows it
and missed by the least when it does not -- never "infeasible" -- and that
locks, exclusions, punts and the spending shape mean what they mean in the
local search.
"""

from collections.abc import Callable
from itertools import combinations
from typing import Any

import pytest

from app.draft.optimizer import Candidate, fieldable, roster_totals, score, within_shape
from app.draft.targets import CategoryDistribution

pytest.importorskip("scipy")

from app.draft.exact import ExactError, ExactPlan, Target, build_model, solve_exact


def dist(
    abbreviation: str, mean: float, spread: float, *, lower: bool = False
) -> CategoryDistribution:
    return CategoryDistribution(
        abbreviation=abbreviation,
        mean=mean,
        spread=spread,
        lower_is_better=lower,
        sample=100,
        basis_seasons=(2026,),
        period_days=7,
        era_scale=1.0,
    )


ANY = frozenset({"UT"})
LINEUP = ("UT", "UT", "UT")
SLOTS = 3
BUDGET = 30

PTS = dist("PTS", mean=150.0, spread=25.0)
REB = dist("REB", mean=60.0, spread=12.0)
TO = dist("TO", mean=30.0, spread=6.0, lower=True)
FG = dist("FG%", mean=0.47, spread=0.03)


def cand(player_id: int, price: int, **weekly: float) -> Candidate:
    line = {"PTS": 0.0, "REB": 0.0, "TO": 0.0, "FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0}
    line.update(weekly)
    return Candidate(
        player_id=player_id, name=f"P{player_id}", price=price, weekly=line, eligible=ANY
    )


#: Twelve players with deliberately different shapes: scorers, rebounders,
#: a turnover machine, a cheap efficient big, some fillers.
POOL = [
    cand(1, 14, PTS=90.0, REB=15.0, TO=14.0, FGM=30.0, FGA=65.0),
    cand(2, 12, PTS=70.0, REB=30.0, TO=9.0, FGM=28.0, FGA=52.0),
    cand(3, 11, PTS=40.0, REB=45.0, TO=6.0, FGM=17.0, FGA=28.0),
    cand(4, 9, PTS=60.0, REB=12.0, TO=11.0, FGM=22.0, FGA=50.0),
    cand(5, 8, PTS=35.0, REB=35.0, TO=5.0, FGM=15.0, FGA=26.0),
    cand(6, 6, PTS=45.0, REB=10.0, TO=8.0, FGM=16.0, FGA=38.0),
    cand(7, 5, PTS=25.0, REB=28.0, TO=4.0, FGM=11.0, FGA=18.0),
    cand(8, 4, PTS=38.0, REB=8.0, TO=7.0, FGM=14.0, FGA=33.0),
    cand(9, 3, PTS=20.0, REB=20.0, TO=3.0, FGM=8.0, FGA=15.0),
    cand(10, 2, PTS=22.0, REB=9.0, TO=5.0, FGM=8.0, FGA=20.0),
    cand(11, 1, PTS=15.0, REB=12.0, TO=3.0, FGM=6.0, FGA=12.0),
    cand(12, 1, PTS=12.0, REB=6.0, TO=2.0, FGM=5.0, FGA=11.0),
]


#: A roster's worth to a test: its per-category win probabilities and its
#: expected wins by the real scorer (the concede charge already taken).
Worth = Callable[[dict[str, float], float], float]


def brute_force(
    distributions: list[CategoryDistribution],
    *,
    value: Worth | None = None,
    budget: int = BUDGET,
    shape: tuple[int, ...] | None = None,
    must: frozenset[int] = frozenset(),
    never: frozenset[int] = frozenset(),
    punt: frozenset[str] = frozenset(),
) -> tuple[frozenset[int], float]:
    """Every legal roster, scored by the real scorer (or `value`)."""
    cats = [d.abbreviation for d in distributions]
    best: tuple[frozenset[int], float] | None = None
    for players in combinations(POOL, SLOTS):
        ids = frozenset(p.player_id for p in players)
        if not must <= ids or ids & never:
            continue
        if sum(p.price for p in players) > budget:
            continue
        if not fieldable(players, LINEUP) or not within_shape(players, shape, frozenset()):
            continue
        totals = roster_totals(players, cats)
        expected, probabilities = score(totals, distributions, punt)
        worth = expected if value is None else value(probabilities, expected)
        if best is None or worth > best[1] + 1e-9:
            best = (ids, worth)
    assert best is not None
    return best


def solve(distributions: list[CategoryDistribution], **kwargs: Any) -> ExactPlan:
    args: dict[str, Any] = dict(
        budget=BUDGET,
        roster_slots=SLOTS,
        lineup=LINEUP,
        breakpoints=40,
        rate_step=0.002,
        time_limit=60.0,
        gap=0.0,
    )
    args.update(kwargs)
    return solve_exact(POOL, distributions, **args)


def test_the_exact_solver_reaches_the_brute_force_optimum_on_counting_categories() -> None:
    want, best = brute_force([PTS, REB, TO])
    plan = solve([PTS, REB, TO])
    assert plan.optimal
    assert plan.player_ids == want
    assert plan.expected_wins == pytest.approx(best)
    assert plan.targets_met, "no targets is every target met"


def test_a_shooting_percentage_is_scored_within_the_grid_it_is_floored_to() -> None:
    """The rate ladder credits the highest grid rate reached, so the model can
    under-credit a percentage by one step; the roster it picks is within
    that of the best."""
    _, best = brute_force([PTS, REB, TO, FG])
    plan = solve([PTS, REB, TO, FG])
    assert plan.optimal
    assert plan.expected_wins >= best - plan.rate_error - 4 * plan.breakpoint_error


def test_a_target_the_board_can_meet_is_met_at_the_least_cost() -> None:
    free = solve([PTS, REB, TO])
    floor = free.win_probability["PTS"] + 0.12
    plan = solve([PTS, REB, TO], targets=[Target("PTS", floor)])
    assert plan.targets_met
    assert plan.win_probability["PTS"] >= floor
    assert plan.expected_wins <= free.expected_wins + 1e-9, "a floor can only cost"
    # And it is the best roster by the stated objective: expected wins less
    # ten a week per unit of probability the floor is missed by.
    want, _ = brute_force(
        [PTS, REB, TO],
        value=lambda p, expected: expected - 10.0 * max(0.0, floor - p["PTS"]),
    )
    assert plan.player_ids == want


def test_an_impossible_target_comes_back_short_rather_than_infeasible() -> None:
    plan = solve([PTS, REB, TO], targets=[Target("REB", 0.999)])
    assert len(plan.players) == SLOTS, "a roster came back"
    assert not plan.targets_met
    assert plan.shortfall["REB"] > 0.0
    # Missed by the least, weighed against what chasing it costs elsewhere.
    want, _ = brute_force(
        [PTS, REB, TO],
        value=lambda p, expected: expected - 10.0 * max(0.0, 0.999 - p["REB"]),
    )
    assert plan.player_ids == want
    assert plan.win_probability["REB"] >= 0.98, "it still bought nearly all the rebounds it could"


def test_locked_and_excluded_players_are_honoured() -> None:
    plan = solve([PTS, REB, TO], locked=[12], excluded=[1, 2])
    assert 12 in plan.player_ids
    assert not plan.player_ids & {1, 2}
    want, _ = brute_force([PTS, REB, TO], must=frozenset({12}), never=frozenset({1, 2}))
    assert plan.player_ids == want


def test_a_locked_and_excluded_player_is_refused() -> None:
    with pytest.raises(ExactError):
        build_model(POOL, [PTS], budget=BUDGET, roster_slots=SLOTS, locked=[1], excluded=[1])


def test_the_spending_shape_binds_and_is_dropped_when_the_locks_break_it() -> None:
    shape = (9, 9, 9)
    plan = solve([PTS, REB, TO], shape=shape)
    assert plan.shaped
    assert all(p.price <= 9 for p in plan.players)
    want, _ = brute_force([PTS, REB, TO], shape=shape)
    assert plan.player_ids == want

    broken = solve([PTS, REB, TO], shape=shape, locked=[1])
    assert not broken.shaped, "a $14 lock cannot fit a $9 place, so the shape goes"
    assert 1 in broken.player_ids


def test_maximising_named_categories_counts_only_those() -> None:
    """Only rebounds count, but conceding another category is still charged."""
    plan = solve([PTS, REB, TO], maximize=["REB"])
    want, _ = brute_force(
        [PTS, REB, TO],
        value=lambda p, expected: p["REB"] - (p["PTS"] + p["REB"] + p["TO"] - expected),
    )
    assert plan.player_ids == want
    unconstrained = solve([PTS, REB, TO])
    assert plan.win_probability["REB"] >= unconstrained.win_probability["REB"]


def test_a_punted_category_is_dropped_from_the_objective_and_the_charge() -> None:
    plan = solve([PTS, REB, TO], punt=["TO"])
    want, best = brute_force([PTS, REB, TO], punt=frozenset({"TO"}))
    assert plan.player_ids == want
    assert plan.expected_wins == pytest.approx(best)
