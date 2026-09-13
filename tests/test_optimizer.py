"""Optimizer tests.

Pure function tests over hand-built candidates. What matters is that the
objective is expected category wins rather than raw value, that the budget
and roster size bind, that punting drops a category from the sum, and that
locked and excluded players behave as a live draft room needs them to.
"""

import pytest

from app.draft.optimizer import Candidate, optimize, roster_totals, score
from app.draft.targets import CategoryDistribution


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


def cand(player_id: int, price: int, **weekly: float) -> Candidate:
    line = {"PTS": 0.0, "REB": 0.0, "TO": 0.0, "FGM": 0.0, "FGA": 0.0}
    line.update(weekly)
    return Candidate(player_id=player_id, name=f"P{player_id}", price=price, weekly=line)


PTS = dist("PTS", mean=100.0, spread=20.0)
REB = dist("REB", mean=50.0, spread=10.0)
TO = dist("TO", mean=30.0, spread=6.0, lower=True)
FG = dist("FG%", mean=0.48, spread=0.03)


def test_roster_totals_sum_counts_and_rebuild_percentages() -> None:
    players = [cand(1, 10, PTS=40.0, FGM=10.0, FGA=20.0), cand(2, 10, PTS=60.0, FGM=20.0, FGA=30.0)]

    totals = roster_totals(players, ["PTS", "FG%"])

    assert totals["PTS"] == 100.0
    assert totals["FG%"] == pytest.approx(30.0 / 50.0), "a rate is rebuilt, not averaged"


def test_a_roster_with_no_attempts_has_a_zero_rate_not_a_crash() -> None:
    assert roster_totals([cand(1, 1)], ["FG%"])["FG%"] == 0.0


def test_score_is_expected_categories_won() -> None:
    expected, probabilities = score({"PTS": 100.0, "REB": 60.0}, [PTS, REB])

    assert probabilities["PTS"] == pytest.approx(0.5), "at the mean, a coin flip"
    assert probabilities["REB"] == pytest.approx(0.841, abs=0.01), "one sd above"
    assert expected == pytest.approx(0.5 + 0.841, abs=0.01)


def test_fewer_turnovers_is_the_win() -> None:
    _, low = score({"TO": 24.0}, [TO])
    _, high = score({"TO": 36.0}, [TO])

    assert low["TO"] > 0.8
    assert high["TO"] < 0.2


def test_a_punted_category_is_dropped_from_the_sum_but_still_reported() -> None:
    expected, probabilities = score(
        {"PTS": 100.0, "REB": 60.0}, [PTS, REB], punt=frozenset({"REB"})
    )

    assert expected == pytest.approx(0.5), "only points count"
    assert "REB" in probabilities, "still visible, so a punt is a choice you can see"


def test_the_budget_binds() -> None:
    pool = [
        cand(1, 150, PTS=80.0),
        cand(2, 150, PTS=80.0),
        cand(3, 10, PTS=20.0),
        cand(4, 10, PTS=20.0),
    ]

    plan = optimize(pool, [PTS], budget=170, roster_slots=2)

    assert plan.cost <= 170
    assert len(plan.players) == 2


def test_the_roster_size_binds() -> None:
    pool = [cand(i, 1, PTS=10.0 * i) for i in range(1, 8)]

    plan = optimize(pool, [PTS], budget=100, roster_slots=3)

    assert len(plan.players) == 3


def test_the_objective_is_wins_not_raw_value() -> None:
    """Piling points into a category already won is worth less than
    lifting a second category to even."""
    # Two slots, budget 20. Player A adds 200 points and nothing else, so any
    # pair with A wins points outright and loses rebounds outright, about 1.0
    # expected wins. B and C together sit a full sd above the mean on both,
    # about 0.84 each, for roughly 1.68.
    pool = [
        cand(1, 10, PTS=200.0),
        cand(2, 10, PTS=60.0, REB=30.0),
        cand(3, 10, PTS=60.0, REB=30.0),
    ]

    plan = optimize(pool, [PTS, REB], budget=20, roster_slots=2)

    assert plan.player_ids == {2, 3}, "balanced beats one dominant category"
    assert plan.expected_wins > score({"PTS": 260.0, "REB": 30.0}, [PTS, REB])[0]


def test_locked_players_are_kept_even_when_worse() -> None:
    pool = [cand(1, 5, PTS=1.0), cand(2, 5, PTS=100.0), cand(3, 5, PTS=100.0)]

    plan = optimize(pool, [PTS], budget=20, roster_slots=2, locked=[1])

    assert 1 in plan.player_ids


def test_excluded_players_are_never_chosen() -> None:
    pool = [cand(1, 5, PTS=100.0), cand(2, 5, PTS=50.0), cand(3, 5, PTS=40.0)]

    plan = optimize(pool, [PTS], budget=20, roster_slots=2, excluded=[1])

    assert 1 not in plan.player_ids
    assert plan.player_ids == {2, 3}


def test_the_floor_is_always_affordable_for_every_slot() -> None:
    """Greedy must not spend so much early that a slot cannot be filled."""
    pool = [
        cand(1, 95, PTS=100.0),
        cand(2, 3, PTS=10.0),
        cand(3, 3, PTS=10.0),
        cand(4, 3, PTS=10.0),
    ]

    plan = optimize(pool, [PTS], budget=100, roster_slots=3)

    assert len(plan.players) == 3
    assert plan.cost <= 100


def test_an_empty_pool_yields_an_empty_plan() -> None:
    plan = optimize([], [PTS], budget=100, roster_slots=3)

    assert plan.players == ()
    # Zero points is five sd below the mean, so this is a near-certain loss,
    # which is the honest answer for an empty roster.
    assert plan.expected_wins == pytest.approx(0.0, abs=1e-4)
