"""What the league has paid, fitted, and what the fit then recommends.

The fit runs over a constructed set of claims: eight men on the wire with
descending production, one claimed at the top of it and one well down, so
each lands in a different rank bucket. The recommendation is tested against
a fit built by hand, because the choice between the median and the 75th
percentile and the two caps are arithmetic, not a query.

The ladder is tested the same way: a curve built by hand, because what a
dollar wins is a question about a field of bids and not about a database.
The two payloads a bid goes out through -- the route's `BidOut` and the MCP
trim -- are checked here too, against that same hand-built ladder, so the
numbers a page draws and the numbers a model reads cannot drift from the
numbers the module computed.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.api.pickups import _bid_out
from app.db.models import Player
from app.mcp import trim
from app.pickups.bids import (
    CAP_FAAB,
    CAP_SHARE,
    LADDER_RUNGS,
    BidFit,
    Bucket,
    ClaimEvent,
    CurveBucket,
    WinCurve,
    bid_fit,
    clear_cache,
    free_agents_on,
    recommend_bid,
    shadow_price,
    share_cap,
    value_rank,
    win_curve,
)
from app.scoring.replacement import TYPICAL_PICKUP
from tests.pickups_db import WEEK, losing_bid, played, winning_bid
from tests.scoring_db import held, league_season, player

#: A claim lands in matchup period 2 (days 8-14), and the men on the wire
#: have played days 8 and 9, so the knowable line has something to read.
CLAIM_DAY = 10

#: One night's line, scaled per player so the wire ranks in name order.
NIGHT: Mapping[str, float] = {
    "PTS": 20.0,
    "REB": 8.0,
    "AST": 4.0,
    "STL": 1.2,
    "BLK": 0.8,
    "3PM": 2.0,
    "TO": 2.4,
    "FGM": 8.0,
    "FGA": 17.0,
    "FTM": 4.0,
    "FTA": 5.0,
}

#: A fit built by hand: one bucket, a median of ten and a 75th of twenty.
BY_HAND = BidFit(
    league_season_id=1,
    seasons=(2026, 2027),
    buckets=(
        Bucket(
            label="1-5",
            first_rank=1,
            last_rank=5,
            median=10.0,
            upper_quartile=20.0,
            low=2,
            high=41,
            n=9,
        ),
    ),
    claims=9,
)

EMPTY = BidFit(league_season_id=1, seasons=(), buckets=(), claims=0)

#: A curve built by hand over the one bucket `BY_HAND` fits: $0 never wins,
#: and the chance climbs a step a dollar to certainty at $4.
CURVE = WinCurve(
    buckets=(
        CurveBucket(
            label="1-5",
            n=20,
            contested=20,
            points=((0, 0.0), (1, 0.25), (2, 0.50), (3, 0.80), (4, 1.0)),
        ),
    ),
    events=20,
    uncontested=0,
)

#: The same fit, with a curve to read the ladder off.
LADDERED = BidFit(
    league_season_id=BY_HAND.league_season_id,
    seasons=BY_HAND.seasons,
    buckets=BY_HAND.buckets,
    claims=BY_HAND.claims,
    curve=CURVE,
)


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_cache()
    yield scoring_session


def wire_player(session: Session, name: str, factor: float) -> Player:
    """A man nobody holds, with two nights on record before the claim."""
    who = player(session, name)
    for day in (8, 9):
        played(session, who, day, 30.0, {k: v * factor for k, v in NIGHT.items()})
    return who


def test_the_wire_of_a_day_is_who_played_and_nobody_held(session: Session) -> None:
    ls, (home, _), (_, second) = league_season(session, days_per_period=7)
    free = wire_player(session, "Free", 1.0)
    owned = player(session, "Owned")
    played(session, owned, 8, 30.0, NIGHT)
    held(session, home, second, owned, 8)

    wire = free_agents_on(session, ls, CLAIM_DAY)

    assert wire == {free.id}, "a lineup row in the period takes a man off the wire"


def test_the_fit_buckets_a_claim_by_the_claimed_players_rank(session: Session) -> None:
    ls, (home, _), _ = league_season(session, days_per_period=7)
    wire = [wire_player(session, f"W{index}", 1.0 - index / 20) for index in range(8)]
    winning_bid(session, home, CLAIM_DAY, 40, wire[0])
    winning_bid(session, home, CLAIM_DAY, 3, wire[6])

    fit = bid_fit(session, ls, use_cache=False, distributions=WEEK)

    assert fit.claims == 2
    assert fit.seasons == (2026,)
    assert fit.thin is True
    assert "thin" in fit.note
    labels = {bucket.label: bucket for bucket in fit.buckets}
    assert sorted(labels) == ["1-5", "6-15"]
    assert labels["1-5"].median == 40.0
    assert labels["1-5"].n == 1
    assert labels["6-15"].median == 3.0
    assert fit.bucket_for(2) is labels["1-5"]
    assert fit.bucket_for(7) is labels["6-15"]
    assert fit.bucket_for(99) is None, "no claim was ever made that far down the wire"


def test_a_players_rank_is_his_place_among_the_days_free_agents() -> None:
    weights = {10: 0.9, 11: 0.5, 12: 1.4}
    assert value_rank(12, weights) == 1
    assert value_rank(10, weights) == 2
    assert value_rank(11, weights) == 3
    assert value_rank(99, weights) == 4, "a man nobody weighed ranks last"


def test_the_seventy_fifth_percentile_is_paid_when_the_move_is_worth_double() -> None:
    generous = recommend_bid(0.11, 0.05, 3, 100, 10.0, 10.0, BY_HAND)
    ordinary = recommend_bid(0.06, 0.05, 3, 100, 10.0, 10.0, BY_HAND)

    assert generous.amount == 20
    assert generous.basis == "75th percentile"
    assert ordinary.amount == 10
    assert ordinary.basis == "median"
    assert ordinary.bucket == "1-5"
    assert (ordinary.low, ordinary.high, ordinary.sample) == (2, 41, 9)
    assert ordinary.capped_by is None


def test_the_bid_is_capped_by_the_budget_left() -> None:
    bid = recommend_bid(0.2, 0.05, 1, 7, 10.0, 10.0, BY_HAND)

    assert bid.uncapped == 20.0
    assert bid.amount == 7
    assert bid.capped_by == CAP_FAAB


def test_the_bid_is_capped_by_the_share_of_the_pot_the_weeks_left_deserve() -> None:
    bid = recommend_bid(0.2, 0.05, 1, 100, 2.0, 20.0, BY_HAND)

    assert bid.amount == 10, "a tenth of the season left, a tenth of the money"
    assert bid.capped_by == CAP_SHARE
    assert share_cap(100, 2.0, 20.0) == 10
    assert share_cap(100, 0.5, 20.0) == 3, "rounded up, so a week can still buy something"
    assert share_cap(9, 20.0, 20.0) == 9


def test_a_rank_the_league_never_claimed_is_priced_at_nothing() -> None:
    nothing = recommend_bid(0.2, 0.05, 80, 100, 10.0, 10.0, BY_HAND)
    unfitted = recommend_bid(0.2, 0.05, 1, 100, 10.0, 10.0, EMPTY)

    assert nothing.amount == 0 and nothing.bucket == "none"
    assert unfitted.amount == 0
    assert "no winning FAAB bids on record" in unfitted.note


def test_the_curve_reads_the_winner_against_every_losing_bid() -> None:
    curve = win_curve([ClaimEvent(day=1, player_id=7, winning_bid=5, losing_bids=(2, 5), rank=3)])
    bucket = curve.bucket_for("1-5")

    assert bucket is not None
    assert bucket.n == 1 and bucket.contested == 1
    assert bucket.chance(6) == 1.0, "above the top bid it wins outright"
    assert bucket.chance(4) == 0.0, "below it, nothing"
    assert bucket.chance(5) == pytest.approx(1 / 3), "two already at $5, so one share of three"


def test_a_claim_nobody_else_bid_on_is_uncontested() -> None:
    curve = win_curve(
        [
            ClaimEvent(day=1, player_id=7, winning_bid=0, losing_bids=(), rank=2),
            ClaimEvent(day=2, player_id=8, winning_bid=3, losing_bids=(1,), rank=2),
        ]
    )

    assert curve.events == 2
    assert curve.uncontested == 1
    assert curve.uncontested_share == 0.5


def test_the_fit_reads_the_losing_bids_off_the_same_claims(session: Session) -> None:
    ls, (home, away), _ = league_season(session, days_per_period=7)
    wire = [wire_player(session, f"W{index}", 1.0 - index / 20) for index in range(8)]
    winning_bid(session, home, CLAIM_DAY, 4, wire[0])
    losing_bid(session, away, CLAIM_DAY, 1, wire[0])
    losing_bid(session, away, CLAIM_DAY, 4, wire[0])

    fit = bid_fit(session, ls, use_cache=False, distributions=WEEK)
    bucket = fit.curve.bucket_for("1-5")

    assert bucket is not None
    assert (bucket.n, bucket.contested) == (1, 1)
    assert bucket.chance(5) == 1.0
    assert bucket.chance(4) == pytest.approx(1 / 3), "the winner and one loser were both at $4"
    assert "claim events" in fit.note


def test_what_he_is_worth_is_the_budget_a_week_in_typical_pickups() -> None:
    bid = recommend_bid(0.30, 0.20, 3, 120, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0)

    assert bid.rate == pytest.approx(TYPICAL_PICKUP * 10.0 / 120)
    assert bid.worth_dollars == 60, "$12 a week of budget, and the move is worth five pickups"
    assert "$120 left over 10.0 weeks" in bid.rate_note


def test_the_ceiling_is_the_dollar_the_bar_stops_at() -> None:
    bid = recommend_bid(0.30, 0.20, 3, 120, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0)
    rate = shadow_price(120, 10.0, TYPICAL_PICKUP)

    assert bid.ceiling == 20
    assert 0.30 - bid.ceiling * rate == pytest.approx(0.20), "at the ceiling he sits on the bar"
    assert 0.30 - (bid.ceiling + 1) * rate < 0.20, "a dollar above it he is under the bar"


def test_the_ladder_never_asks_more_and_promises_less() -> None:
    bid = recommend_bid(0.30, 0.20, 3, 120, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0)

    assert [rung.asked for rung in bid.ladder] == list(LADDER_RUNGS)
    assert [rung.amount for rung in bid.ladder] == [2, 3, 4]
    assert [rung.win_chance for rung in bid.ladder] == [0.50, 0.80, 1.0]
    amounts = [rung.amount for rung in bid.ladder]
    chances = [rung.win_chance for rung in bid.ladder]
    assert amounts == sorted(amounts)
    assert chances == sorted(chances)


def test_the_ladder_is_capped_by_the_ceiling_and_says_what_that_buys() -> None:
    bid = recommend_bid(0.30, 0.20, 3, 6, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0)

    assert bid.worth_dollars == 3
    assert bid.ceiling == 1, "sixty cents a week of budget buys one dollar over the bar"
    assert [rung.amount for rung in bid.ladder] == [1, 1, 1]
    assert [rung.win_chance for rung in bid.ladder] == [0.25, 0.25, 0.25], (
        "a rung a cap pulls down reports the chance at the dollar actually offered"
    )
    assert bid.amount == 6, "and the market number is still the market number, capped by the pot"
    assert bid.capped_by == CAP_FAAB


def test_a_manager_with_no_money_has_no_ceiling_and_no_ladder() -> None:
    bid = recommend_bid(0.30, 0.20, 3, 0, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0)

    assert (bid.rate, bid.worth_dollars, bid.ceiling) == (0.0, 0, 0)
    assert [rung.amount for rung in bid.ladder] == [0, 0, 0]
    assert "no budget left" in bid.rate_note


def test_a_move_that_only_just_clears_the_bar_is_worth_nothing_in_dollars() -> None:
    bid = recommend_bid(0.20, 0.20, 3, 120, 9.0, 10.0, LADDERED, per_week=0.20, weeks_covered=10.0)

    assert bid.worth_dollars == 40
    assert bid.ceiling == 0, "every dollar paid for it puts it back under the bar"
    assert [rung.amount for rung in bid.ladder] == [0, 0, 0]


def test_both_payloads_carry_the_ladder_and_what_he_is_worth() -> None:
    bid = recommend_bid(0.30, 0.20, 3, 120, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0)
    out = _bid_out(bid)

    assert out is not None
    payload = out.model_dump(mode="json")
    assert (payload["amount"], payload["worth_dollars"], payload["ceiling"]) == (10, 60, 20)
    assert [(rung["amount"], rung["win_chance"]) for rung in payload["ladder"]] == [
        (2, 0.50),
        (3, 0.80),
        (4, 1.0),
    ]
    assert "competition" not in payload, "docs/faab.md section 5: the count does not ship"

    trimmed = trim.bid(payload)
    assert trimmed is not None
    assert trimmed["ladder"] == [[2, 0.5, 20], [3, 0.8, 20], [4, 1.0, 20]]
    assert (trimmed["worth_dollars"], trimmed["ceiling"]) == (60, 20)
    assert "dollars" in trimmed["ladder_reads"]
    assert "competition" not in trimmed


def test_a_caller_whose_bar_is_on_the_net_gets_that_bar_in_dollars() -> None:
    """The week report's test is `net >= hurdle`, which over ten weeks is
    `per_week >= hurdle / 10`. The ceiling has to mean that bar and not a
    tenfold stricter one, or the page's "clears the bar" and the ceiling
    beside it would be saying different things about the same move."""
    strict = recommend_bid(
        3.0, 0.20, 3, 120, 9.0, 10.0, LADDERED, per_week=0.30, weeks_covered=10.0
    )
    translated = recommend_bid(
        3.0,
        0.20,
        3,
        120,
        9.0,
        10.0,
        LADDERED,
        per_week=0.30,
        weeks_covered=10.0,
        bar=0.20 / 10.0,
    )

    assert strict.ceiling == 20, "the season report's bar, read on the week's number"
    assert translated.ceiling == 56, "the week report's own bar, in the budget's units"
    assert strict.worth_dollars == translated.worth_dollars == 60, "the worth knows no bar"
