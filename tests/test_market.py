"""Market model tests.

The model's whole claim is that an auction is a fixed pot shared out, not a
price per player. So the tests are mostly about the pot: that it is exactly
exhausted, that replacement level is where value stops being free, and that
nobody is paid for being worse than the last man rostered.
"""

import pytest

from app.draft.availability import (
    DEFAULT_AVAILABILITY,
    Availability,
    apply_availability,
)
from app.draft.market import PriceBoard, calibrate, price_board, replacement_value
from app.draft.valuation import PlayerProjection, PlayerValue


def value(player_id: int, name: str, total: float) -> PlayerValue:
    return PlayerValue(player_id=player_id, name=name, total=total, categories=())


def board_of(totals: list[float], **kwargs: int) -> PriceBoard:
    settings = {"teams": 2, "budget_per_team": 100, "roster_slots": 3}
    settings.update(kwargs)
    values = [value(i, f"P{i}", total) for i, total in enumerate(totals, start=1)]
    return price_board(values, **settings)


def test_the_whole_budget_is_spent_and_no_more() -> None:
    """An auction hands out a fixed pot. A model that invents money is wrong."""
    board = board_of([5.0, 3.0, 1.0, 0.0, -1.0, -2.0], teams=2, roster_slots=3)

    rostered = sorted(board.players, key=lambda p: -p.value)[: board.rostered]
    assert sum(p.expected_price for p in rostered) == board.total_budget == 200


def test_replacement_is_the_last_player_who_gets_rostered() -> None:
    values = [value(i, f"P{i}", total) for i, total in enumerate([9.0, 5.0, 2.0, -4.0], 1)]

    assert replacement_value(values, rostered=3) == 2.0
    assert replacement_value(values, rostered=1) == 9.0


def test_nobody_pays_above_the_floor_for_replacement_level() -> None:
    board = board_of([10.0, 1.0, 1.0, 1.0], teams=2, roster_slots=2)

    fringe = [p for p in board.players if p.value <= board.replacement_value]
    assert fringe, "the fixture has players at or below replacement"
    assert all(p.expected_price == 1 for p in fringe)
    assert all(p.surplus == 0.0 for p in fringe)


def test_price_follows_value_above_replacement_not_raw_value() -> None:
    """Two players a point apart cost the same extra, wherever they sit."""
    board = board_of([8.0, 6.0, 4.0, 2.0, 0.0, 0.0], teams=2, roster_slots=3)
    prices = {p.name: p.expected_price for p in board.players}

    assert prices["P1"] > prices["P2"] > prices["P3"]
    assert (prices["P1"] - prices["P2"]) == pytest.approx(prices["P2"] - prices["P3"], abs=1)


def test_a_bigger_budget_raises_every_price() -> None:
    lean = board_of([5.0, 3.0, 1.0, 0.0], budget_per_team=50)
    rich = board_of([5.0, 3.0, 1.0, 0.0], budget_per_team=200)

    assert rich.players[0].expected_price > lean.players[0].expected_price


def test_a_deeper_league_lowers_replacement_and_spreads_the_pot() -> None:
    """More rosters reach further down, so the last man rostered is worse."""
    shallow = board_of([9.0, 7.0, 5.0, 3.0, 1.0, -1.0], teams=2, roster_slots=2)
    deep = board_of([9.0, 7.0, 5.0, 3.0, 1.0, -1.0], teams=2, roster_slots=3)

    assert deep.replacement_value < shallow.replacement_value


def test_an_empty_board_is_handled() -> None:
    empty = price_board([], teams=2, budget_per_team=100, roster_slots=3)

    assert empty.players == ()
    assert empty.replacement_value == 0.0


def test_calibration_finds_where_the_room_disagreed() -> None:
    board = board_of([8.0, 6.0, 4.0, 2.0, 0.0, 0.0], teams=2, roster_slots=3)
    model = {p.name: p.expected_price for p in board.players}

    # The room paid far over for P3 and got P1 cheap.
    actual = {
        p.player_id: (
            model[p.name] + 40
            if p.name == "P3"
            else model[p.name] - 30
            if p.name == "P1"
            else model[p.name]
        )
        for p in board.players
    }
    result = calibrate(board, actual)

    assert result.compared == len(board.players)
    assert result.mean_absolute_error > 0
    assert result.worst_overpaid[0][0] == "P3"
    assert result.worst_underpaid[0][0] == "P1"


def test_calibration_with_nothing_to_compare_is_empty() -> None:
    board = board_of([5.0, 3.0, 1.0])

    assert calibrate(board, {}).compared == 0


def projection(player_id: int, games: float = 70.0, points: float = 1000.0) -> PlayerProjection:
    return PlayerProjection(
        player_id=player_id,
        name=f"P{player_id}",
        games=games,
        totals={"PTS": points, "FGM": 400.0, "FGA": 800.0},
    )


def test_a_league_wide_availability_factor_changes_no_price() -> None:
    """The finding that decides the injury model.

    Availability does not persist between seasons and does not vary by
    player quality, so the honest default is one number for everyone. One
    number is scale invariant in z-space, so it must move nothing at all.
    """
    raw = [projection(i, points=1000.0 * i) for i in (1, 2, 3, 4)]
    adjusted = apply_availability(raw, DEFAULT_AVAILABILITY)

    assert adjusted[0].totals["PTS"] == pytest.approx(870.0)
    assert adjusted[0].games == pytest.approx(70.0 * DEFAULT_AVAILABILITY)

    from app.draft.valuation import value_players

    before = price_board(
        value_players(raw, ["PTS"], refine=False), teams=2, budget_per_team=100, roster_slots=2
    )
    after = price_board(
        value_players(adjusted, ["PTS"], refine=False),
        teams=2,
        budget_per_team=100,
        roster_slots=2,
    )

    assert [p.expected_price for p in before.players] == [p.expected_price for p in after.players]


def test_a_known_injury_does_move_a_price() -> None:
    """Which is the case worth modelling: someone already ruled out."""
    from app.draft.valuation import value_players

    # Real spread, or every z-score is zero and every price is the floor.
    raw = [projection(i, points=1200.0 - 200.0 * i) for i in (1, 2, 3, 4)]
    hurt = apply_availability(raw, lambda p: 0.4 if p.player_id == 1 else 1.0)

    healthy_board = price_board(
        value_players(raw, ["PTS"], refine=False), teams=2, budget_per_team=100, roster_slots=2
    )
    hurt_board = price_board(
        value_players(hurt, ["PTS"], refine=False), teams=2, budget_per_team=100, roster_slots=2
    )

    healthy_price = healthy_board.price_of(1)
    hurt_price = hurt_board.price_of(1)
    assert healthy_price is not None and hurt_price is not None
    assert healthy_price > 1, "the fixture has to price him above the floor when fit"
    assert hurt_price < healthy_price, "ruled out for most of a season, worth less"


def test_the_shortfall_is_what_a_roster_actually_loses() -> None:
    measured = Availability(factor=0.87, seasons=(2024, 2025, 2026), players=800)

    assert measured.shortfall == pytest.approx(0.13)
