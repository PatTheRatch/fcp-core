"""The backtest's category replay, on a period built to move one category.

The replay is the whole measurement: everything the write-up says rests on
"put the added man's real box scores where the dropped man's started lines
were, and count the categories again". So one period is constructed where
the team loses blocks by three and a free agent has five of them on a day the
spare man started, and the swap has to score exactly +1.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from scripts.pickups_backtest import Replay
from tests.pickups_db import clear_schedule, played
from tests.scoring_db import held, league_season, matchup, player

#: What the home team's one real starter posts over the period: ahead of the
#: opponent everywhere except blocks.
STARTER: Mapping[str, float] = {
    "PTS": 600.0,
    "REB": 250.0,
    "AST": 120.0,
    "STL": 40.0,
    "BLK": 5.0,
    "3PM": 60.0,
    "TO": 40.0,
    "FGM": 250.0,
    "FGA": 500.0,
    "FTM": 90.0,
    "FTA": 100.0,
}

#: The opponent's final totals, as `matchup_team_stats` holds them: behind in
#: everything but blocks, where it leads 8 to 5.
OPPONENT: Mapping[str, float] = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 8.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 200.0,
    "FGA": 500.0,
    "FTM": 70.0,
    "FTA": 100.0,
}

#: A man who plays and produces nothing, so removing him moves no category.
NOTHING: Mapping[str, float] = dict.fromkeys(STARTER, 0.0)

#: Five blocks and not one other count, so the swap can only move blocks.
FIVE_BLOCKS: Mapping[str, float] = {**NOTHING, "BLK": 5.0}


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


@dataclass(frozen=True)
class Built:
    """The constructed period, and the ids the cases name."""

    book: Replay
    team: int
    spare: int
    blocker: int


def build(session: Session) -> Built:
    """A period the home team loses on blocks, and a blocker on the wire."""
    ls, (home, away), (first, _second) = league_season(session, days_per_period=7)
    matchup(session, first, home, away, {away: OPPONENT})

    starter = player(session, "Starter")
    spare = player(session, "Spare")
    blocker = player(session, "Blocker")
    held(session, home, first, starter, 1, stats=STARTER)
    held(session, home, first, spare, 5, stats=NOTHING)
    # The blocker is on the wire: a box score on day 5, and no lineup row at
    # all, which is what a free agent looks like historically.
    played(session, blocker, 5, 30.0, FIVE_BLOCKS)

    return Built(Replay.load(session, ls), int(home.id), spare.id, blocker.id)


def test_a_swap_that_flips_one_category_scores_one(session: Session) -> None:
    built = build(session)
    window = built.book.periods[0]

    delta = built.book.delta(built.team, window, 5, window.final, [built.spare], [built.blocker])

    assert delta == pytest.approx(1.0), "blocks go from 5 against 8 to 10 against 8"


def test_a_swap_that_changes_nothing_scores_nothing(session: Session) -> None:
    """The spare man produced nothing, so losing him moves no category."""
    built = build(session)
    window = built.book.periods[0]

    assert built.book.delta(built.team, window, 5, window.final, [built.spare], []) == (
        pytest.approx(0.0)
    )


def test_the_added_man_is_capped_at_the_starts_the_place_had(session: Session) -> None:
    """A free agent with games on days the dropped man did not start gets
    only as many of them as the place itself had."""
    built = build(session)
    window = built.book.periods[0]
    # Two more blocking nights the place never had: days 6 and 7.
    for day in (6, 7):
        built.book.games[(built.blocker, day)] = built.book.games[(built.blocker, 5)]

    capped = built.book.delta(built.team, window, 5, window.final, [built.spare], [built.blocker])

    assert capped == pytest.approx(1.0), "one start taken, not three"
    swapped = (
        built.book.team_line(built.team, window) - built.book.started[(built.team, 5, built.spare)]
    )
    assert swapped.get("BLK") == 5.0, "and the five blocks are the ones he had that day"


def test_the_replay_counts_a_tie_as_half_a_category(session: Session) -> None:
    built = build(session)
    window = built.book.periods[0]
    mine = built.book.team_line(built.team, window)
    other = built.book.opponent[(window.period, built.team)]
    theirs = built.book.totals[(window.period, other)]

    assert built.book.categories_won(mine, theirs) == pytest.approx(8.0), "all but blocks"
    assert built.book.categories_won(mine, mine) == pytest.approx(4.5), "level is half of each"


def test_a_period_with_no_opponent_scores_nothing(session: Session) -> None:
    """A bye has no categories to win, so no move can be worth any."""
    built = build(session)
    missing = built.book.periods[1]

    assert built.book.delta(built.team, missing, 8, missing.final, [], [built.blocker]) == 0.0
