"""Valuation tests.

The arithmetic is standard; what is worth pinning is the handling of the
three things that make nine-category valuation wrong when done naively:
turnovers being inverted, percentages needing volume, and the pool being
the drafted players rather than everyone with a projection.
"""

import pytest

from app.draft.valuation import (
    PlayerProjection,
    value_players,
)

CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")


def projection(player_id: int, name: str, **totals: float) -> PlayerProjection:
    base = {
        "PTS": 1000.0,
        "REB": 400.0,
        "AST": 300.0,
        "STL": 80.0,
        "BLK": 40.0,
        "3PM": 120.0,
        "TO": 180.0,
        "FGM": 380.0,
        "FGA": 800.0,
        "FTM": 180.0,
        "FTA": 220.0,
    }
    base.update(totals)
    return PlayerProjection(player_id=player_id, name=name, games=70.0, totals=base)


def test_more_of_a_counting_category_is_worth_more() -> None:
    players = [
        projection(1, "Scorer", PTS=2000.0),
        projection(2, "Average"),
        projection(3, "Quiet", PTS=400.0),
    ]

    values = {v.name: v for v in value_players(players, ["PTS"], refine=False)}

    assert values["Scorer"].total > values["Average"].total > values["Quiet"].total


def test_fewer_turnovers_is_worth_more() -> None:
    """ESPN's isReverseItem says false for turnovers. The box scores disagree."""
    players = [
        projection(1, "Careless", TO=400.0),
        projection(2, "Average"),
        projection(3, "Careful", TO=60.0),
    ]

    values = {v.name: v for v in value_players(players, ["TO"], refine=False)}

    assert values["Careful"].total > values["Careless"].total
    careful = values["Careful"].category("TO")
    assert careful is not None
    assert careful.value > 0, "a low turnover count is a positive contribution"
    assert careful.raw == 60.0, "the raw figure stays in its own units"


def test_a_percentage_counts_for_nothing_without_volume() -> None:
    """The classic trap: a perfect shooter on two attempts is not an asset."""
    players = [
        projection(1, "High volume good", FGM=600.0, FGA=1000.0),  # 60% on 1000
        projection(2, "Tiny sample perfect", FGM=10.0, FGA=10.0),  # 100% on 10
        projection(3, "High volume poor", FGM=300.0, FGA=1000.0),  # 30% on 1000
    ]

    values = {v.name: v for v in value_players(players, ["FG%"], refine=False)}

    assert values["High volume good"].total > values["Tiny sample perfect"].total
    assert values["High volume poor"].total < values["Tiny sample perfect"].total
    perfect = values["Tiny sample perfect"].category("FG%")
    assert perfect is not None
    assert perfect.raw == pytest.approx(1.0)


def test_a_high_volume_poor_shooter_is_a_negative() -> None:
    players = [
        projection(1, "Good", FGM=600.0, FGA=1000.0),
        projection(2, "Poor", FGM=300.0, FGA=1000.0),
    ]

    values = {v.name: v for v in value_players(players, ["FG%"], refine=False)}

    assert values["Poor"].total < 0, "shooting badly on volume actively costs"


def test_a_player_with_no_attempts_is_neutral_not_penalised() -> None:
    players = [
        projection(1, "Shoots", FTM=180.0, FTA=200.0),
        projection(2, "Never shoots", FTM=0.0, FTA=0.0),
        projection(3, "Also shoots", FTM=150.0, FTA=200.0),
    ]

    values = {v.name: v for v in value_players(players, ["FT%"], refine=False)}
    never = values["Never shoots"].category("FT%")

    assert never is not None
    assert never.raw == 0.0
    # Zero impact, which lands between the good and bad shooter rather than last.
    assert values["Shoots"].total > never.value > values["Also shoots"].total


def test_total_value_sums_every_category() -> None:
    players = [projection(i, f"P{i}", PTS=500.0 * i) for i in (1, 2, 3)]

    values = value_players(players, CATEGORIES, refine=False)

    for value in values:
        assert len(value.categories) == len(CATEGORIES)
        assert value.total == pytest.approx(sum(c.value for c in value.categories))


def test_the_board_is_ordered_best_first() -> None:
    players = [
        projection(1, "Third", PTS=600.0),
        projection(2, "First", PTS=2000.0),
        projection(3, "Second", PTS=1200.0),
    ]

    names = [v.name for v in value_players(players, ["PTS"], refine=False)]

    assert names == ["First", "Second", "Third"]


def test_narrowing_the_pool_changes_what_counts_as_average() -> None:
    """Deep bench players drag the mean down and flatter everyone above it."""
    good = [projection(i, f"Good{i}", PTS=1500.0 + i) for i in range(1, 4)]
    fringe = [projection(100 + i, f"Fringe{i}", PTS=100.0) for i in range(1, 20)]
    players = [*good, *fringe]

    wide = {v.name: v.total for v in value_players(players, ["PTS"], refine=False)}
    narrow = {v.name: v.total for v in value_players(players, ["PTS"], pool_size=3, refine=True)}

    assert wide["Good1"] > 1.0, "against everyone, a good player looks extraordinary"
    assert narrow["Good1"] < wide["Good1"], "against the real pool, far less so"


def test_players_outside_the_pool_are_still_ranked() -> None:
    """A fringe player must stay on the board, measured on the pool's scale."""
    players = [projection(i, f"P{i}", PTS=2000.0 - i * 100) for i in range(1, 11)]

    values = value_players(players, ["PTS"], pool_size=3, refine=True)

    assert len(values) == 10, "nobody is dropped for being outside the pool"
    assert values[0].name == "P1"
    assert values[-1].name == "P10"
    assert values[-1].total < values[0].total


def test_an_empty_pool_is_handled() -> None:
    assert value_players([], CATEGORIES) == []
    assert value_players([projection(1, "Solo")], []) == []


def test_a_category_where_everyone_is_identical_scores_zero() -> None:
    players = [projection(i, f"P{i}") for i in (1, 2, 3)]

    values = value_players(players, ["PTS"], refine=False)

    assert all(v.total == 0.0 for v in values), "no spread means no advantage"
