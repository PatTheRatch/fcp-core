"""Draft room tests.

Pure tests over a hand-built room. The state machine has to refuse what the
rules refuse, keep every team's money and places straight, and price the
remaining board for the money left. The ceiling has to say yes to a player
who is clearly worth it, no to one who is not, and show how much is at stake
either way. None of this touches the database or ESPN: the whole point of
the room is that picks come from outside and everything else follows.
"""

import pytest

from app.draft.optimizer import Candidate
from app.draft.room import (
    Allocation,
    DraftError,
    DraftState,
    Pick,
    TeamState,
    bid_ceiling,
    inflation,
    reprice,
    resolve,
)
from app.draft.targets import CategoryDistribution

ANY = frozenset({"UT"})


def cand(player_id: int, price: int, pts: float) -> Candidate:
    return Candidate(
        player_id=player_id,
        name=f"P{player_id}",
        price=price,
        weekly={"PTS": pts, "FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0},
        eligible=ANY,
        position="PG",
    )


PTS = CategoryDistribution(
    abbreviation="PTS",
    mean=100.0,
    spread=20.0,
    lower_is_better=False,
    sample=100,
    basis_seasons=(2026,),
    period_days=7,
    era_scale=1.0,
)
LINEUP = ("UT", "UT")


def room(**overrides: object) -> DraftState:
    """Two teams, $10 each, two places each. Small enough to reason about."""
    args: dict[str, object] = dict(
        budget=10, roster_slots=2, teams={1: "Us", 2: "Them"}, me=1, nomination_order=(2, 1)
    )
    args.update(overrides)
    return DraftState.open(**args)  # type: ignore[arg-type]


# -- the rules ---------------------------------------------------------------


def test_a_fresh_room_has_nothing_spent_and_everything_open() -> None:
    state = room()
    assert state.mine.remaining == 10
    assert state.mine.open_slots == 2
    assert state.taken == frozenset()
    assert state.dollars_left == 20
    assert state.open_slots == 4
    assert not state.complete


def test_max_bid_leaves_a_floor_bid_for_every_other_place() -> None:
    team = TeamState(1, "Us", budget=10, roster_slots=2)
    assert team.max_bid() == 9, "$10 with two to fill: one must stay back for the second"
    team = TeamState(1, "Us", budget=10, roster_slots=2, picks=(Pick(7, 1, 4),))
    assert team.max_bid() == 6, "$6 left and one place: all of it"
    full = TeamState(1, "Us", budget=10, roster_slots=2, picks=(Pick(7, 1, 4), Pick(8, 1, 5)))
    assert full.max_bid() == 0, "no place left means no bid, whatever the money"


def test_a_pick_moves_money_and_a_place_and_nothing_else() -> None:
    state = room().apply(Pick(player_id=7, team_id=1, price=4))
    assert state.mine.spent == 4
    assert state.mine.remaining == 6
    assert state.mine.open_slots == 1
    assert state.taken == {7}
    assert state.taken_by_others == frozenset()
    assert state.teams[2].remaining == 10, "the other team is untouched"


def test_the_room_refuses_what_the_rules_refuse() -> None:
    state = room()
    with pytest.raises(DraftError, match="not in this room"):
        state.apply(Pick(7, team_id=9, price=1))
    with pytest.raises(DraftError, match="below"):
        state.apply(Pick(7, 1, price=0))
    with pytest.raises(DraftError, match="cannot pay"):
        state.apply(Pick(7, 1, price=10))
    taken = state.apply(Pick(7, 1, 4))
    with pytest.raises(DraftError, match="already been drafted"):
        taken.apply(Pick(7, 2, 3))
    full = taken.apply(Pick(8, 1, 6))
    with pytest.raises(DraftError, match="no roster place"):
        full.apply(Pick(9, 1, 1))


def test_a_pick_is_refused_when_it_would_strand_a_later_place() -> None:
    """$10, two places, a $10 bid: the second place could never be filled."""
    with pytest.raises(DraftError, match=r"caps a bid at \$9"):
        room().apply(Pick(7, 1, 10))


def test_undo_restores_the_previous_room_exactly() -> None:
    before = room().apply(Pick(7, 2, 3))
    after = before.apply(Pick(8, 1, 4)).undo()
    assert after == before
    assert room().undo() == room(), "nothing to undo is not an error"


def test_the_room_is_complete_when_every_place_is_filled() -> None:
    state = room()
    for pick in (Pick(1, 1, 3), Pick(2, 1, 3), Pick(3, 2, 3), Pick(4, 2, 3)):
        state = state.apply(pick)
    assert state.complete
    assert state.open_slots == 0
    assert state.field_ceiling() == 0


def test_the_field_ceiling_is_the_most_any_other_team_can_pay() -> None:
    state = room().apply(Pick(7, 2, 6))
    assert state.field_ceiling() == 4, "they have $4 and one place; we do not count"
    assert state.mine.max_bid() == 9


def test_nomination_order_repeats() -> None:
    state = room()
    assert state.to_nominate() == 2
    assert state.apply(Pick(7, 2, 1)).to_nominate() == 1
    assert state.apply(Pick(7, 2, 1)).apply(Pick(8, 1, 1)).to_nominate() == 2
    assert room(nomination_order=()).to_nominate() is None


def test_a_room_with_no_budget_is_refused_rather_than_planned_against() -> None:
    with pytest.raises(DraftError, match="not been ingested"):
        room(budget=0)
    with pytest.raises(DraftError, match="not in this room"):
        room(me=9)


# -- prices ------------------------------------------------------------------

#: Priced so the board value above the floor equals the discretionary money:
#: two teams hold 2 x ($10 - 2 floors) = $16, and the four players who will
#: be rostered carry 6 + 4 + 3 + 3 = $16 above the floor.
BOARD = [cand(1, 7, 90.0), cand(2, 5, 70.0), cand(3, 4, 60.0), cand(4, 4, 55.0), cand(5, 1, 10.0)]


def test_inflation_is_one_before_the_first_pick() -> None:
    assert inflation(room(), BOARD) == pytest.approx(1.0)


def test_overpaying_deflates_the_rest_of_the_board() -> None:
    state = room().apply(Pick(1, 2, 9))
    # They have $1 left and one place, so nothing above the floor. We hold
    # $8 above ours. The three remaining rostered players carry $10.
    assert inflation(state, BOARD) == pytest.approx(0.8)


def test_underpaying_inflates_it() -> None:
    state = room().apply(Pick(1, 2, 2))
    assert inflation(state, BOARD) == pytest.approx(1.5)


def test_reprice_carries_what_we_paid_and_drops_what_others_own() -> None:
    state = room().apply(Pick(1, 2, 9)).apply(Pick(2, 1, 2))
    pool = {c.player_id: c for c in reprice(state, BOARD)}
    assert 1 not in pool, "theirs is gone"
    assert pool[2].price == 2, "ours is what we paid, not what the board said"
    # Inflation: we hold $8 - 1 floor = $7 above the floor... they hold none.
    # Remaining rostered: players 3 and 4 at $3 each above the floor = $6.
    assert inflation(state, BOARD) == pytest.approx(7 / 6)
    assert pool[3].price == 1 + round(3 * 7 / 6)
    assert pool[5].price == 1, "never below the floor"


# -- solving -----------------------------------------------------------------


def test_resolve_keeps_ours_and_never_picks_theirs() -> None:
    state = room().apply(Pick(1, 2, 9)).apply(Pick(5, 1, 1))
    plan = resolve(state, BOARD, [PTS], lineup=LINEUP, restarts=2)
    assert 5 in plan.player_ids, "the player we own is on every plan"
    assert 1 not in plan.player_ids, "the player they own is on none"
    assert len(plan.players) == 2
    assert plan.cost <= state.budget


def test_the_ceiling_is_where_the_rest_of_the_roster_stops_being_affordable() -> None:
    """Player 1 scores 90 a week. Without him the best pair is 2 and 3: 130
    points for $9. With him at $6 there is $4 left for player 3, and 150 beats
    130. At $7 there is $3 left, which buys nobody but the 10-point scrub, and
    100 does not. So the most he is worth is $6, three short of the legal
    maximum -- not because of what he scores but because of what the second
    place could still buy. That is the whole idea of the number."""
    ceiling = bid_ceiling(room(), 1, BOARD, [PTS], lineup=LINEUP, restarts=2)
    assert ceiling.price == 6
    assert ceiling.max_bid == 9, "the rules would have let us go to $9"
    assert ceiling.marginal_at_floor > 0
    assert ceiling.with_him is not None and ceiling.with_him >= ceiling.without
    assert ceiling.field == 9


def test_a_player_not_worth_the_floor_gets_no_price_but_a_magnitude() -> None:
    """Player 5 scores 10 a week. Forcing him in displaces a real player, so
    even at $1 the roster is worse. The answer is no, and the marginal says
    by how much, so a reader can see it is not a near miss."""
    ceiling = bid_ceiling(room(), 5, BOARD, [PTS], lineup=LINEUP, restarts=2)
    assert ceiling.price is None
    assert ceiling.with_him is None
    assert ceiling.marginal_at_floor < 0


def test_the_ceiling_moves_with_the_money_and_the_places_left() -> None:
    """Once we own player 2 for $6, one place and $4 remain. Player 1 beside
    player 2 is 160 against a best alternative of 130, so he is worth every
    dollar we can still legally bid, and the ceiling rises to the maximum
    even though the maximum itself has fallen from $9 to $4."""
    open_room = bid_ceiling(room(), 1, BOARD, [PTS], lineup=LINEUP, restarts=2)
    later = room().apply(Pick(2, 1, 6))
    tight = bid_ceiling(later, 1, BOARD, [PTS], lineup=LINEUP, restarts=2)
    assert open_room.price == 6
    assert tight.price == 4
    assert tight.max_bid == 4


def test_a_drafted_or_unknown_player_has_no_ceiling() -> None:
    state = room().apply(Pick(1, 2, 5))
    with pytest.raises(DraftError, match="already been drafted"):
        bid_ceiling(state, 1, BOARD, [PTS], lineup=LINEUP)
    with pytest.raises(DraftError, match="not on the board"):
        bid_ceiling(state, 99, BOARD, [PTS], lineup=LINEUP)


def test_a_warm_start_is_never_worse_than_the_roster_it_started_from() -> None:
    """A seeded roster is reconstructed and improved, never discarded.

    The greedy fill by value-per-dollar and a couple of shuffles can miss a
    roster that a warm start hands them directly. Whatever the search does
    from there, the answer cannot score below the seed itself, because the
    seed is one of the starts and the best start is kept.
    """
    from app.draft.optimizer import _plan, optimize

    pool = [cand(i, price=1 + (i * 7) % 20, pts=20.0 + (i * 13) % 80) for i in range(1, 25)]
    by_id = {c.player_id: c for c in pool}
    # A deliberately unusual but legal roster the greedy would not build.
    seed_ids = (3, 11, 17)
    seed_score = _plan([by_id[i] for i in seed_ids], [PTS], frozenset()).expected_wins

    plan = optimize(
        pool, [PTS], budget=40, roster_slots=3, lineup=("UT",) * 3, restarts=0, starts=[seed_ids]
    )

    assert plan.expected_wins >= seed_score - 1e-9
    assert len(plan.players) == 3 and plan.cost <= 40


def test_the_ceiling_is_warm_started_from_the_baseline() -> None:
    """With the with-him search seeded from the without-him roster, adding a
    player for the floor bid to an empty room can never read as harmful: the
    seed plus him is one of the candidates, and it is at least the baseline
    with a real player in a place that held one."""
    ceiling = bid_ceiling(room(), 2, BOARD, [PTS], lineup=LINEUP, restarts=0)
    assert ceiling.marginal_at_floor >= -1e-9


# -- the plan ------------------------------------------------------------------


def test_an_allocation_describes_the_whole_budget_largest_first() -> None:
    state = room(budget=20, roster_slots=3)
    allocation = Allocation.from_prices([2, 9], state)
    assert allocation.places == tuple(sorted(allocation.places, reverse=True))
    assert sum(allocation.places) == 20, "unspent money and an unfilled place are spread back"
    assert len(allocation.places) == 3


def test_a_purchase_uses_the_cheapest_place_that_covers_it_and_the_rest_refits() -> None:
    state = room(budget=20, roster_slots=3)
    allocation = Allocation.from_prices([12, 6, 2], state, slack=0.0)
    assert allocation.places == (12, 6, 2)
    bought = state.apply(Pick(7, 1, 5))
    # $5 uses the $6 place, not the $12 one; the $1 saved goes back above
    # the floor of what is left, $15 over two places.
    assert sum(allocation.open_places(bought)) == 15
    assert allocation.open_places(bought)[0] >= 12
    assert len(allocation.open_places(bought)) == 2


def test_the_plan_caps_what_one_player_may_take() -> None:
    state = room()
    star = cand(1, 8, 200.0)
    pool = [star, cand(2, 1, 60.0), cand(3, 1, 55.0), cand(4, 1, 50.0)]
    free = bid_ceiling(state, 1, pool, [PTS], lineup=LINEUP, restarts=2)
    assert free.price == 9 and not free.capped

    allocation = Allocation.from_prices([5, 5], state, slack=0.0)
    capped = bid_ceiling(state, 1, pool, [PTS], lineup=LINEUP, restarts=2, allocation=allocation)
    assert capped.plan_cap == 5
    assert capped.price == 5
    assert capped.capped, "he is worth more against the board; the plan is what stopped it"


def test_a_plan_holds_both_rosters_to_its_places() -> None:
    state = room()
    pool = [cand(1, 8, 200.0), cand(2, 1, 60.0), cand(3, 1, 55.0), cand(4, 4, 58.0)]
    allocation = Allocation.from_prices([5, 5], state, slack=0.0)
    plan = resolve(state, pool, [PTS], lineup=LINEUP, restarts=2, allocation=allocation)
    assert 1 not in plan.player_ids, "an $8 player does not fit a $5 place"
