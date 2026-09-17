"""What the league has paid, fitted, and what the fit then recommends.

The fit runs over a constructed set of claims: eight men on the wire with
descending production, one claimed at the top of it and one well down, so
each lands in a different rank bucket. The recommendation is tested against
a fit built by hand, because the choice between the median and the 75th
percentile and the two caps are arithmetic, not a query.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.db.models import Player
from app.pickups.bids import (
    CAP_FAAB,
    CAP_SHARE,
    BidFit,
    Bucket,
    bid_fit,
    clear_cache,
    free_agents_on,
    recommend_bid,
    share_cap,
    value_rank,
)
from tests.pickups_db import WEEK, played, winning_bid
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
