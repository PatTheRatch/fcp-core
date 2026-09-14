"""The tier curve.

It must never create or destroy money, never push anyone under the floor,
recover a known shape from data, and change nothing when flat. Provenance
of the league's curve is checked too, so a refit that forgets to say what
it was fitted on fails here.
"""

import pytest

from app.draft.market import PriceBoard, PricedPlayer
from app.draft.projections import usable
from app.draft.tiers import (
    DEFAULT_BUCKETS,
    FLAT_CURVE,
    LEAGUE_TIER_CURVE,
    Observation,
    TierCurve,
    apply_tier_curve,
    fit_tier_curve,
    rank_players,
)


def board(prices: list[int], *, teams: int = 2, slots: int = 3) -> PriceBoard:
    players = tuple(
        PricedPlayer(
            player_id=i, name=f"P{i}", value=float(p), surplus=float(p - 1), expected_price=p
        )
        for i, p in enumerate(prices, 1)
    )
    return PriceBoard(
        players=players,
        replacement_value=0.0,
        teams=teams,
        budget_per_team=sum(prices) // teams,
        roster_slots=slots,
    )


def test_a_flat_curve_changes_nothing() -> None:
    original = board([20, 10, 6, 4, 3, 1])
    assert [p.expected_price for p in apply_tier_curve(original, FLAT_CURVE).players] == [
        20,
        10,
        6,
        4,
        3,
        1,
    ]


def test_the_curve_moves_money_from_the_bottom_to_the_top_and_keeps_the_total() -> None:
    original = board([20, 10, 6, 4, 3, 1])
    steep = TierCurve(
        ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 10**9)), (2.0, 1.0, 1.0, 0.5, 0.5, 1.0), ()
    )
    curved = apply_tier_curve(original, steep)
    prices = [p.expected_price for p in curved.players]
    assert prices[0] > 20, "the top rises"
    assert prices[3] < 4, "the bottom falls"
    rostered = original.rostered
    pot = original.total_budget - rostered
    above_floor = sorted((p - 1 for p in prices), reverse=True)[:rostered]
    assert sum(above_floor) == pytest.approx(pot, abs=rostered), "rounding is the only slack"
    assert min(prices) >= 1, "nobody goes below the floor"


def test_fit_recovers_a_known_shape() -> None:
    """Board says 10 in tier one and the room pays 19 (2x above floor); tier
    two the room pays exactly the board. The fitted multipliers say so."""
    two_tier = ((1, 2), (3, 10**9))
    observations = [
        Observation(rank=1, predicted=10, actual=19),
        Observation(rank=2, predicted=10, actual=19),
        Observation(rank=3, predicted=5, actual=5),
        Observation(rank=4, predicted=5, actual=5),
    ]
    curve = fit_tier_curve(observations, buckets=two_tier, fit_seasons=(2024,))
    assert curve.multipliers == pytest.approx((2.0, 1.0))
    assert curve.fit_seasons == (2024,)


def test_an_empty_tier_keeps_a_multiplier_of_one() -> None:
    curve = fit_tier_curve([Observation(1, 10, 15)], buckets=((1, 1), (2, 10**9)))
    assert curve.multipliers == pytest.approx((14 / 9, 1.0))


def test_ranks_are_deterministic_under_ties() -> None:
    ranked = rank_players(board([5, 5, 5]))
    assert [p.player_id for p in ranked] == [1, 2, 3]


def test_the_league_curve_is_fitted_on_usable_seasons_only() -> None:
    assert len(LEAGUE_TIER_CURVE.multipliers) == len(DEFAULT_BUCKETS)
    assert LEAGUE_TIER_CURVE.fit_seasons, "a curve with no provenance is a guess"
    assert all(usable(y) for y in LEAGUE_TIER_CURVE.fit_seasons)
    assert LEAGUE_TIER_CURVE.multipliers[0] > 1 > LEAGUE_TIER_CURVE.multipliers[4], (
        "top up, bottom down: the measured shape"
    )


def test_a_curve_needs_one_multiplier_per_bucket() -> None:
    with pytest.raises(ValueError):
        TierCurve(DEFAULT_BUCKETS, (1.0,), ())
