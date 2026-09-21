"""One currency for a move, on arithmetic small enough to check by hand.

The season charge and the record projection are pure functions of a handful
of numbers, so most of these build a `SpotBook` directly rather than a
league: the cases the brief names are about the formula, and a formula tested
through a database is tested twice and read once.

The two that do need rows are the ones the formula cannot answer on its own:
what the league standard says a player is worth, and what the season has
banked so far.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.pickups.judge import (
    TYPICAL_PICKUP,
    SpotBook,
    Standard,
    banked_record,
    judge,
    load_spots,
    places_cost,
    season_cost,
    standard_lens,
    weeks_after_this_period,
    wire_replacement,
)
from app.scoring.lines import CategoryLine
from tests.pickups_db import WEEK, clear_schedule, configure
from tests.scoring_db import league_season, matchup

HOME = 1

#: A roster place's worth, categories a week, for the constructed cases: a
#: man the wire would replace tomorrow, an ordinary starter, a keeper.
FRINGE = 0.07
STREAMER = 0.09
GOOD = 0.50
KEEPER = 0.80

#: Both sides level in every category, so nothing is decided before the swap.
EVEN = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 20.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 235.0,
    "FGA": 500.0,
    "FTM": 78.0,
    "FTA": 100.0,
    "FG%": 235 / 500,
    "FT%": 0.78,
}


def book(
    values: Mapping[int, float],
    wire: frozenset[int],
    *,
    weeks: float = 15.0,
    banked: tuple[float, float] = (0.0, 0.0),
    expected_per_week: float = 4.5,
    measured: bool = True,
) -> SpotBook:
    """A book with the values already read, for the arithmetic cases."""
    lens = Standard(
        average=CategoryLine(EVEN) if measured else CategoryLine(),
        distributions=WEEK,
    )
    return SpotBook(
        lens=lens,
        values=dict(values),
        wire=wire,
        weeks_remaining=weeks,
        banked=banked,
        expected_per_week=expected_per_week,
    )


def test_dropping_a_fringe_player_for_a_streamer_costs_about_nothing() -> None:
    """Principle 2: the place never goes empty, so a man worth what the wire
    is worth is free to drop."""
    assert season_cost(FRINGE, STREAMER, TYPICAL_PICKUP) == pytest.approx(-0.02)
    assert abs(season_cost(FRINGE, FRINGE, TYPICAL_PICKUP)) < 0.02


def test_dropping_a_good_player_costs_his_gap_to_the_wire() -> None:
    """His whole value is not the charge; what the wire gives back is."""
    assert season_cost(GOOD, STREAMER, TYPICAL_PICKUP) == pytest.approx(GOOD - STREAMER)
    # And with a better man still on the wire, the charge falls by that much.
    assert season_cost(GOOD, STREAMER, 0.30) == pytest.approx(GOOD - 0.30)


def test_a_keeper_pickup_counts_for_more_than_a_streamer() -> None:
    """Principle 3: the place is worth the better of holding him and
    streaming it again, so the keeper is credited and the streamer is not
    punished for being one."""
    keeper = season_cost(FRINGE, KEEPER, TYPICAL_PICKUP)
    streamer = season_cost(FRINGE, STREAMER, TYPICAL_PICKUP)
    assert keeper < streamer, "a lower cost is a better move"
    assert -keeper == pytest.approx(KEEPER - FRINGE), "the place gains the whole gap"
    assert -streamer == pytest.approx(STREAMER - FRINGE)


def test_a_streamer_is_never_charged_below_what_the_wire_returns() -> None:
    """A man worth less than an ordinary pickup still leaves a place worth
    one, because it can be streamed again next week."""
    assert season_cost(FRINGE, 0.0, TYPICAL_PICKUP) == pytest.approx(FRINGE - TYPICAL_PICKUP)


def test_the_net_refuses_a_category_now_that_costs_three_tenths_a_week() -> None:
    """The brief's case, verbatim: +1 this week for -0.3 a week over fifteen
    weeks is -3.5, and no hurdle recommends it."""
    spots = book({1: GOOD, 2: 0.2}, wire=frozenset({2}), weeks=15.0)
    judgement = judge(spots, delta_week=1.0, dropped=[1], added=[2])

    assert judgement.delta_season_per_week == pytest.approx(-0.3)
    assert judgement.delta_total == pytest.approx(1.0 - 0.3 * 15)
    assert judgement.delta_total == pytest.approx(-3.5)
    assert judgement.delta_total < 0.0, "refused at every hurdle on the grid"


def test_the_same_move_in_the_last_week_of_the_season_is_only_the_week() -> None:
    """With no weeks after this one there is nothing to charge, which is why
    a deadline stream is a different question from a November one."""
    spots = book({1: GOOD, 2: 0.2}, wire=frozenset({2}), weeks=0.0)
    judgement = judge(spots, delta_week=1.0, dropped=[1], added=[2])

    assert judgement.delta_total == pytest.approx(1.0)
    assert judgement.per_week == pytest.approx(1.0), "one week covered"


def test_an_add_into_an_empty_place_is_credited_and_charged_nothing() -> None:
    spots = book({2: STREAMER}, wire=frozenset({2}), weeks=10.0)
    judgement = judge(spots, delta_week=0.05, added=[2])

    assert judgement.delta_season_per_week == pytest.approx(STREAMER)
    assert judgement.delta_total == pytest.approx(0.05 + STREAMER * 10)


def test_the_wire_replacement_excludes_the_man_being_added() -> None:
    """The place cannot be refilled with the player already taking it."""
    spots = book({1: GOOD, 2: 0.40, 3: 0.35}, wire=frozenset({2, 3}), weeks=1.0)

    assert spots.replacement(exclude=[2]) == pytest.approx(0.35), "the next man down"
    assert spots.replacement(exclude=[2, 3]) == pytest.approx(TYPICAL_PICKUP), "the floor"
    judgement = judge(spots, delta_week=0.0, dropped=[1], added=[2])
    assert judgement.replacement == pytest.approx(0.35)
    assert judgement.delta_season_per_week == pytest.approx(0.40 - GOOD)


def test_the_projected_record_is_banked_plus_the_weeks_left() -> None:
    """Six categories a week for two more weeks, on a 30-24 record so far."""
    spots = book(
        {1: GOOD, 2: 0.2},
        wire=frozenset({2}),
        weeks=2.0,
        banked=(30.0, 24.0),
        expected_per_week=6.0,
    )
    judgement = judge(spots, delta_week=0.0, delta_season_per_week=0.0)

    assert judgement.record_without == (42.0, 30.0), "30 + 12 won, 24 + 6 lost"
    assert judgement.record_with == (42.0, 30.0), "a move worth nothing changes nothing"

    better = judge(spots, delta_week=0.5, delta_season_per_week=0.25)
    assert better.delta_total == pytest.approx(1.0)
    assert better.record_with == (43.0, 29.0), "the net is the change in the record"
    assert better.record_without == (42.0, 30.0)


def test_nothing_is_charged_for_the_season_when_no_standard_is_measurable() -> None:
    """A season that has posted nothing has no league average to value a line
    against, and a guess would be worse than the week alone."""
    spots = book({1: GOOD, 2: 0.2}, wire=frozenset({2}), measured=False)
    judgement = judge(spots, delta_week=0.4, dropped=[1], added=[2])

    assert judgement.measured is False
    assert judgement.delta_season_per_week == 0.0
    assert judgement.delta_total == pytest.approx(0.4)


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


def test_the_league_standard_values_a_line_inside_the_average_team(session: Session) -> None:
    """The lens `app/scoring/players.py` grades a season with: the average
    team with his line in it, against the average team without it."""
    ls, (home, away), (first, _second) = league_season(session, days_per_period=7)
    configure(ls)
    matchup(session, first, home, away, {home: EVEN, away: EVEN})

    lens = standard_lens(session, ls, 1, WEEK)

    assert lens.measured is True
    assert lens.average.get("PTS") == pytest.approx(500.0)
    nobody = lens.value(CategoryLine())
    a_scorer = lens.value(CategoryLine({"PTS": 60.0, "FGM": 25.0, "FGA": 50.0}))
    assert nobody == 0.0
    assert a_scorer > 0.0, "sixty points a week is worth something to a place"
    assert a_scorer < 9.0


def test_the_wire_replacement_is_the_best_free_agent_over_the_floor(session: Session) -> None:
    ls, (home, away), (first, _second) = league_season(session, days_per_period=7)
    configure(ls)
    matchup(session, first, home, away, {home: EVEN, away: EVEN})
    pool = {
        10: CategoryLine({"PTS": 60.0, "REB": 20.0, "FGM": 25.0, "FGA": 50.0}),
        11: CategoryLine({"PTS": 20.0, "FGM": 8.0, "FGA": 20.0}),
    }

    best = wire_replacement(session, ls, 1, pool, distributions=WEEK)
    without_him = wire_replacement(session, ls, 1, pool, exclude=[10], distributions=WEEK)
    empty = wire_replacement(session, ls, 1, {}, distributions=WEEK)

    assert best > without_him, "the best man on the wire, not the second"
    assert empty == pytest.approx(TYPICAL_PICKUP)
    assert wire_replacement(session, ls, 1, pool, exclude=[10, 11], distributions=WEEK) == (
        pytest.approx(TYPICAL_PICKUP)
    ), "a bare wire still leaves a place worth an ordinary pickup"


def test_the_banked_record_counts_only_matchups_already_finished(session: Session) -> None:
    """A week still being played is not banked, however it looks today."""
    ls, (home, away), (first, second) = league_season(session, days_per_period=7)
    configure(ls)
    matchup(session, first, home, away, {home: EVEN, away: EVEN})
    matchup(session, second, home, away, {home: EVEN, away: EVEN})

    #  `scoring_db.matchup` marks every category a WIN for both sides, so the
    #  count is what matters here, not who won it.
    after_one = banked_record(session, ls, HOME, today=9)
    during_the_first = banked_record(session, ls, HOME, today=3)

    assert during_the_first == (0.0, 0.0), "period 1 ends on day 7"
    assert sum(after_one) == 9.0, "one finished matchup, nine categories"


def test_the_weeks_charged_exclude_the_week_being_played(session: Session) -> None:
    """`delta_week` owns this matchup, so the season term must not."""
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=3)
    configure(ls)

    assert weeks_after_this_period(session, ls, 1) == pytest.approx(2.0), "periods 2 and 3"
    assert weeks_after_this_period(session, ls, 8) == pytest.approx(1.0)
    assert weeks_after_this_period(session, ls, 15) == pytest.approx(0.0), "the last one"


def test_the_book_separates_the_roster_from_the_wire(session: Session) -> None:
    ls, (home, away), (first, _second) = league_season(session, days_per_period=7)
    configure(ls)
    matchup(session, first, home, away, {home: EVEN, away: EVEN})
    weekly = {
        1: CategoryLine({"PTS": 100.0, "FGM": 40.0, "FGA": 80.0}),
        2: CategoryLine({"PTS": 60.0, "FGM": 25.0, "FGA": 50.0}),
        3: CategoryLine({"PTS": 10.0, "FGM": 4.0, "FGA": 10.0}),
    }

    spots = load_spots(
        session, ls, HOME, 1, roster=[1], wire=[2, 3], weekly=weekly, distributions=WEEK
    )

    assert spots.measured is True
    assert spots.value(1) > spots.value(2) > spots.value(3)
    assert spots.replacement() == pytest.approx(spots.value(2)), "the best free agent"
    assert spots.replacement(exclude=[2]) == pytest.approx(max(spots.value(3), TYPICAL_PICKUP))
    assert spots.expected_per_week > 0.0, "the roster wins some categories in an ordinary week"


def test_a_swap_between_two_men_below_the_wire_is_worth_nothing() -> None:
    """The floor sits under both sides of the place. A pre-season run with
    nobody projected once recommended churn at +0.06 a week because the
    first cut floored the added side only; two nobodies swapped leave the
    place worth the wire either way."""
    assert season_cost(0.0, 0.0, TYPICAL_PICKUP) == 0.0
    assert season_cost(0.03, 0.05, TYPICAL_PICKUP) == 0.0
    assert season_cost(0.05, 0.03, TYPICAL_PICKUP) == 0.0
    # An empty place is the exception: it yields nothing, so filling it with a
    # man at the floor gains the floor.
    assert season_cost(0.0, 0.05, TYPICAL_PICKUP, empty=True) == pytest.approx(-TYPICAL_PICKUP)


def test_one_place_is_the_case_of_many_and_not_a_second_formula() -> None:
    """`season_cost` is `places_cost` with one man a side. A pickup and a
    trade have to be charged by the same rule, or the two reports cannot be
    compared on a page, so the one-place version is written in terms of the
    general one rather than beside it."""
    assert places_cost([GOOD], [STREAMER], TYPICAL_PICKUP) == pytest.approx(
        season_cost(GOOD, STREAMER, TYPICAL_PICKUP)
    )
    assert places_cost([], [STREAMER], TYPICAL_PICKUP) == pytest.approx(
        season_cost(0.0, STREAMER, TYPICAL_PICKUP, empty=True)
    )


def test_two_men_out_for_one_leaves_a_place_worth_the_wire() -> None:
    """The brief's uneven trade, from the side that consolidates. Both men
    leaving are charged; one place is refilled by the man arriving and the
    other by whoever the wire offers, because it will not stay empty."""
    cost = places_cost([GOOD, STREAMER], [KEEPER], TYPICAL_PICKUP)

    assert cost == pytest.approx(
        max(GOOD, TYPICAL_PICKUP)
        + max(STREAMER, TYPICAL_PICKUP)
        - max(KEEPER, TYPICAL_PICKUP)
        - TYPICAL_PICKUP
    )
    # And with a real free agent to be had, the opened place is worth more and
    # the deal costs less.
    assert places_cost([GOOD, STREAMER], [KEEPER], 0.30) < cost


def test_two_men_in_for_one_fills_a_place_that_was_worth_nothing() -> None:
    """The other end of the same deal. The extra man takes an empty place, so
    he is credited in full rather than against a wire replacement that was
    never there: the whole of what he brings counts, and nothing is deducted
    for a man who was not in the place."""
    assert places_cost([GOOD], [GOOD, STREAMER], TYPICAL_PICKUP) == pytest.approx(-STREAMER)
    # Whether the deal is an improvement is then just the arithmetic: one
    # keeper for two ordinary men costs the places what he was worth over them.
    assert places_cost([KEEPER], [GOOD, STREAMER], TYPICAL_PICKUP) == pytest.approx(
        KEEPER - GOOD - STREAMER
    )
    assert places_cost([KEEPER], [GOOD, GOOD], TYPICAL_PICKUP) < 0.0


def test_the_judgement_charges_every_place_a_trade_touches() -> None:
    """A three-for-two through `judge`: the same net whichever way it is
    written down, and the wire replacement is charged once for the place the
    deal leaves open."""
    spots = book({1: KEEPER, 2: GOOD, 3: STREAMER, 4: GOOD, 5: GOOD}, wire=frozenset(), weeks=10.0)

    judgement = judge(spots, delta_week=0.0, dropped=[1, 2, 3], added=[4, 5])

    assert judgement.replacement == pytest.approx(TYPICAL_PICKUP), "a bare wire is the floor"
    assert judgement.delta_season_per_week == pytest.approx(
        -(KEEPER + GOOD + STREAMER - GOOD - GOOD - TYPICAL_PICKUP)
    )
    assert judgement.delta_total == pytest.approx(judgement.delta_season_per_week * 10.0)
