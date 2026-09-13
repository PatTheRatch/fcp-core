"""Optimizer tests.

Pure function tests over hand-built candidates. What matters is that the
objective is expected category wins rather than raw value, that the budget
and roster size bind, that punting drops a category from the sum, and that
locked and excluded players behave as a live draft room needs them to.
"""

from collections.abc import Mapping, Sequence

import pytest

from app.draft.lineup import DEFAULT_LINEUP
from app.draft.optimizer import (
    Candidate,
    RosterPlan,
    _plan,
    fieldable,
    optimize,
    roster_totals,
    score,
)
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


#: Eligible everywhere, so the arithmetic tests are not about positions.
#: Those tests also pass a lineup of N utility slots for an N-man roster:
#: the default lineup has ten starting slots, and a two-man roster can never
#: fill it, which the constraint correctly refuses.
ANY = frozenset({"PG", "SG", "SF", "PF", "C", "G", "F", "UT"})


def cand(player_id: int, price: int, eligible: frozenset[str] = ANY, **weekly: float) -> Candidate:
    line = {"PTS": 0.0, "REB": 0.0, "TO": 0.0, "FGM": 0.0, "FGA": 0.0}
    line.update(weekly)
    return Candidate(
        player_id=player_id, name=f"P{player_id}", price=price, weekly=line, eligible=eligible
    )


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

    plan = optimize(pool, [PTS], budget=170, roster_slots=2, lineup=("UT",) * 2)

    assert plan.cost <= 170
    assert len(plan.players) == 2


def test_the_roster_size_binds() -> None:
    pool = [cand(i, 1, PTS=10.0 * i) for i in range(1, 8)]

    plan = optimize(pool, [PTS], budget=100, roster_slots=3, lineup=("UT",) * 3)

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

    plan = optimize(pool, [PTS, REB], budget=20, roster_slots=2, lineup=("UT",) * 2)

    assert plan.player_ids == {2, 3}, "balanced beats one dominant category"
    assert plan.expected_wins > score({"PTS": 260.0, "REB": 30.0}, [PTS, REB])[0]


def test_locked_players_are_kept_even_when_worse() -> None:
    pool = [cand(1, 5, PTS=1.0), cand(2, 5, PTS=100.0), cand(3, 5, PTS=100.0)]

    plan = optimize(pool, [PTS], budget=20, roster_slots=2, locked=[1], lineup=("UT",) * 2)

    assert 1 in plan.player_ids


def test_excluded_players_are_never_chosen() -> None:
    pool = [cand(1, 5, PTS=100.0), cand(2, 5, PTS=50.0), cand(3, 5, PTS=40.0)]

    plan = optimize(pool, [PTS], budget=20, roster_slots=2, excluded=[1], lineup=("UT",) * 2)

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

    plan = optimize(pool, [PTS], budget=100, roster_slots=3, lineup=("UT",) * 3)

    assert len(plan.players) == 3
    assert plan.cost <= 100


def test_an_empty_pool_yields_an_empty_plan() -> None:
    plan = optimize([], [PTS], budget=100, roster_slots=3, lineup=("UT",) * 3)

    assert plan.players == ()
    # Zero points is five sd below the mean, so this is a near-certain loss,
    # which is the honest answer for an empty roster.
    assert plan.expected_wins == pytest.approx(0.0, abs=1e-4)


CENTRE = frozenset({"C", "UT"})
GUARD = frozenset({"PG", "SG", "G", "UT"})
WING = frozenset({"SF", "SG", "F", "UT"})
BIG = frozenset({"PF", "C", "F", "UT"})


def test_a_roster_that_cannot_be_fielded_is_not_a_roster() -> None:
    """Thirteen centres score wonderfully on blocks and cannot start a game."""
    assert fieldable([cand(i, 1, CENTRE) for i in range(13)]) is False
    assert (
        fieldable(
            [
                cand(1, 1, GUARD),
                cand(2, 1, GUARD),
                cand(3, 1, GUARD),
                cand(4, 1, WING),
                cand(5, 1, WING),
                cand(6, 1, WING),
                cand(7, 1, BIG),
                cand(8, 1, BIG),
                cand(9, 1, CENTRE),
                cand(10, 1, CENTRE),
            ]
        )
        is True
    )


def test_the_optimizer_refuses_to_build_thirteen_centres() -> None:
    """Even when centres are the only players worth anything.

    Ten of the eleven players are centres who dominate on blocks. The
    optimizer must still reach for the guard, because without one the
    roster cannot fill PG, SG or G and is not a roster at all.
    """
    blocks = dist("BLK", mean=10.0, spread=3.0)
    pool = [cand(i, 5, CENTRE, BLK=20.0) for i in range(1, 11)]
    pool.append(cand(99, 5, GUARD, BLK=0.0))

    plan = optimize(pool, [blocks], budget=100, roster_slots=10, lineup=("PG", "C", "UT"))

    assert 99 in plan.player_ids, "the guard is forced in to fill PG"
    assert fieldable(plan.players, ("PG", "C", "UT"))


def test_a_swap_that_breaks_the_lineup_is_not_taken() -> None:
    """The only guard is the worst player; the optimizer still keeps him."""
    points = dist("PTS", mean=50.0, spread=10.0)
    guard = cand(1, 5, GUARD, PTS=1.0)
    centres = [cand(i, 5, CENTRE, PTS=100.0) for i in range(2, 6)]

    plan = optimize(
        [guard, *centres], [points], budget=100, roster_slots=3, lineup=("PG", "C", "UT")
    )

    assert 1 in plan.player_ids, "dropping the guard for a better centre is illegal"
    assert fieldable(plan.players, ("PG", "C", "UT"))


def test_an_unknown_eligibility_cannot_start_anywhere() -> None:
    """Empty eligibility is unknown, and unknown is treated as unable."""
    assert fieldable([cand(1, 1, frozenset())] * 13) is False


def test_the_optimizer_respects_the_centre_cap() -> None:
    """Centres are the only players worth anything, and only three may be rostered."""
    blocks = dist("BLK", mean=10.0, spread=3.0)
    centres = [
        Candidate(
            player_id=i, name=f"C{i}", price=5, weekly={"BLK": 30.0}, eligible=CENTRE, position="C"
        )
        for i in range(1, 8)
    ]
    guards = [
        Candidate(
            player_id=100 + i,
            name=f"G{i}",
            price=5,
            weekly={"BLK": 1.0},
            eligible=GUARD,
            position="PG",
        )
        for i in range(1, 8)
    ]

    plan = optimize(
        [*centres, *guards],
        [blocks],
        budget=100,
        roster_slots=5,
        lineup=("PG", "C", "UT", "UT", "UT"),
        limits={"C": 3},
    )

    rostered_centres = sum(1 for p in plan.players if p.position == "C")
    assert rostered_centres <= 3, "the cap binds even when centres are all that score"
    assert len(plan.players) == 5


def test_a_swap_that_would_break_the_centre_cap_is_refused() -> None:
    points = dist("PTS", mean=50.0, spread=10.0)
    roster_pool = [
        Candidate(
            player_id=1, name="C1", price=5, weekly={"PTS": 90.0}, eligible=CENTRE, position="C"
        ),
        Candidate(
            player_id=2, name="C2", price=5, weekly={"PTS": 90.0}, eligible=CENTRE, position="C"
        ),
        Candidate(
            player_id=3, name="C3", price=5, weekly={"PTS": 90.0}, eligible=CENTRE, position="C"
        ),
        Candidate(
            player_id=4, name="G1", price=5, weekly={"PTS": 1.0}, eligible=GUARD, position="PG"
        ),
    ]

    plan = optimize(
        roster_pool,
        [points],
        budget=100,
        roster_slots=3,
        lineup=("PG", "C", "UT"),
        limits={"C": 2},
    )

    assert sum(1 for p in plan.players if p.position == "C") <= 2


def _reference_swap_improve(
    roster: Sequence[Candidate],
    pool: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    punt: frozenset[str],
    keep: frozenset[int],
    budget: int,
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
) -> RosterPlan:
    """The obvious swap loop, written for clarity and nothing else.

    Every trial is rebuilt from scratch and its fieldability checked before
    it is scored. That is roughly twenty-five times slower than the real one
    on a full pool, and it is the definition the fast version has to match.
    """
    best = _plan(roster, distributions, punt)
    chosen = set(best.player_ids)
    while True:
        improved: RosterPlan | None = None
        for index, outgoing in enumerate(best.players):
            if outgoing.player_id in keep:
                continue
            budget_left = budget - (best.cost - outgoing.price)
            for incoming in pool:
                if incoming.player_id in chosen or incoming.price > budget_left:
                    continue
                trial = list(best.players)
                trial[index] = incoming
                if not fieldable(trial, lineup, limits):
                    continue
                plan = _plan(trial, distributions, punt)
                if plan.expected_wins > (improved or best).expected_wins + 1e-9:
                    improved = plan
        if improved is None:
            return best
        best = improved
        chosen = set(best.player_ids)


@pytest.mark.parametrize("punt", [(), ("TO",), ("FG%", "TO")])
@pytest.mark.parametrize("restarts", [0, 3])
def test_the_fast_search_reaches_the_same_roster_as_the_obvious_one(
    monkeypatch: pytest.MonkeyPatch, punt: tuple[str, ...], restarts: int
) -> None:
    """The speed work must not have changed a single answer.

    `_swap_improve` carries the roster's line across trials instead of
    re-summing it, and checks fieldability only for a trial that has already
    beaten the incumbent. Both are meant to be pure savings. This pins that:
    the same pool, seed and punt set must give the same players and the same
    expected wins, to the float.

    Local search is chaotic -- one different tie-break early leads somewhere
    else entirely -- so an equality here is a real signal rather than a
    coincidence.
    """
    pool = [
        cand(
            i,
            price=1 + (i * 7) % 40,
            PTS=40.0 + (i * 13) % 60,
            REB=10.0 + (i * 5) % 30,
            TO=2.0 + (i % 7),
            FGM=5.0 + (i % 9),
            FGA=12.0 + (i % 11),
        )
        for i in range(1, 41)
    ]
    lineup = ("UT",) * 5
    kwargs = dict(budget=100, roster_slots=5, lineup=lineup, restarts=restarts, punt=punt, seed=7)

    fast = optimize(pool, [PTS, REB, TO, FG], **kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr("app.draft.optimizer._swap_improve", _reference_swap_improve)
    slow = optimize(pool, [PTS, REB, TO, FG], **kwargs)  # type: ignore[arg-type]

    assert fast.player_ids == slow.player_ids
    assert fast.expected_wins == pytest.approx(slow.expected_wins, abs=1e-12)
    assert fast.cost == slow.cost


def test_carrying_the_roster_line_across_swaps_does_not_drift() -> None:
    """The running total must still equal a total summed from scratch.

    The search subtracts the outgoing player's line and adds the incoming
    one rather than re-summing thirteen players. Repeated across a long
    search that is an invitation to floating-point drift, and a drifting
    total would quietly score the wrong roster highest.
    """
    pool = [
        cand(
            i,
            price=1 + (i * 3) % 25,
            PTS=30.0 + (i * 17) % 70,
            REB=8.0 + (i * 11) % 25,
            TO=1.5 + (i % 5),
            FGM=4.0 + (i % 8),
            FGA=9.0 + (i % 13),
        )
        for i in range(1, 36)
    ]

    plan = optimize(
        pool, [PTS, REB, TO, FG], budget=90, roster_slots=6, lineup=("UT",) * 6, restarts=4
    )

    from_scratch = roster_totals(plan.players, ["PTS", "REB", "TO", "FG%"])
    for category, value in from_scratch.items():
        assert plan.totals[category] == pytest.approx(value, rel=1e-12)
