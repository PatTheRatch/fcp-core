"""The backtest's category replay, on periods built to move one category.

The replay is the whole measurement, and what it does is re-solve the lineup:
day by day, seat the roster the team really held -- once with the swap in it
and once without -- and count the categories each seating wins. So the cases
here are the ones that rule can get wrong. A blocker added for a man who
produced nothing has to score exactly +1; a move that changes nobody has to
score exactly 0 even when the manager's own lineup was badly set, because the
lineup is not the move's to be paid for; and a man on the bench has to be
seated, since the re-solve plans the roster rather than copying the record.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from scripts.pickups_backtest import Replay
from tests.pickups_db import ANY, SMALL_LINEUP, clear_schedule, configure, eligible, played
from tests.scoring_db import held, league_season, matchup, player

#: What the home team's one real starter posts, on the one day he plays:
#: ahead of the opponent everywhere except blocks.
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

#: Five blocks and not one other count, so a swap can only move blocks.
FIVE_BLOCKS: Mapping[str, float] = {**NOTHING, "BLK": 5.0}

#: The decision day, and the period it sits in: days 1-7.
TODAY = 5
WHOLE_PERIOD = range(1, 8)


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


@dataclass(frozen=True)
class Built:
    """The constructed period, and the ids the cases name."""

    book: Replay
    team: int
    starter: int
    spare: int
    blocker: int


def build(
    session: Session,
    *,
    starter_slot: str = "UT",
    blocker_days: Sequence[int] = (TODAY,),
) -> Built:
    """A period the home team loses on blocks, and a blocker on the wire.

    Both rostered men are held every day of the period, because the re-solve
    reads the roster from `daily_lineup_slots` rather than the starts. The
    starter's one game is on day 1, before the decision, so it lands in the
    period's line whatever the move is; the spare's is on the decision day
    itself, which is the place a pickup would take.
    """
    ls, (home, away), (first, _second) = league_season(session, days_per_period=7)
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
    matchup(session, first, home, away, {away: OPPONENT})

    starter = player(session, "Starter")
    spare = player(session, "Spare")
    blocker = player(session, "Blocker")
    for who in (starter, spare, blocker):
        eligible(session, who, ANY, "PG")
    for day in WHOLE_PERIOD:
        held(session, home, first, starter, day, slot=starter_slot if day == 1 else "BE")
        held(session, home, first, spare, day, slot="UT" if day == TODAY else "BE")
    played(session, starter, 1, 30.0, STARTER)
    played(session, spare, TODAY, 30.0, NOTHING)
    # The blocker is on the wire: box scores and no lineup row at all, which
    # is what a free agent looks like historically.
    for day in blocker_days:
        played(session, blocker, day, 30.0, FIVE_BLOCKS)

    return Built(Replay.load(session, ls), int(home.id), starter.id, spare.id, blocker.id)


def test_a_swap_that_flips_one_category_scores_one(session: Session) -> None:
    built = build(session)
    window = built.book.periods[0]

    delta = built.book.delta(
        built.team, window, TODAY, window.final, [built.spare], [built.blocker]
    )

    assert delta == pytest.approx(1.0), "blocks go from 5 against 8 to 10 against 8"


def test_a_swap_that_changes_nothing_scores_nothing(session: Session) -> None:
    """The spare man produced nothing, so losing him moves no category."""
    built = build(session)
    window = built.book.periods[0]

    assert built.book.delta(built.team, window, TODAY, window.final, [built.spare], []) == (
        pytest.approx(0.0)
    )


def test_the_added_man_gets_every_start_the_lineup_can_give_him(session: Session) -> None:
    """The old rule capped him at the dropped man's started days, so an
    empty-day pickup scored zero by construction. The re-solve seats him on
    every day the lineup has room, which is the whole point of streaming."""
    built = build(session, blocker_days=(TODAY, 6, 7))
    window = built.book.periods[0]

    with_him = built.book.resolved(
        built.team, window, TODAY, window.final, [built.spare], [built.blocker]
    )
    without = built.book.resolved(built.team, window, TODAY, window.final)

    assert without.get("BLK") == pytest.approx(5.0), "only the starter's day-1 blocks"
    assert with_him.get("BLK") == pytest.approx(20.0), "three nights of five, and day 1"
    assert built.book.delta(
        built.team, window, TODAY, window.final, [built.spare], [built.blocker]
    ) == pytest.approx(1.0), "still one category, because blocks was the only close one"


def test_both_sides_are_re_solved_so_a_badly_set_lineup_is_not_the_moves_credit(
    session: Session,
) -> None:
    """The manager benched his only producer. Re-solving the swapped roster
    against his actual starts would pay a move that changes nobody the whole
    difference; re-solving both sides pays it nothing, and the gap is
    reported as drift instead."""
    built = build(session, starter_slot="BE")
    window = built.book.periods[0]

    actual = built.book.team_line(built.team, window)
    resolved = built.book.resolved(built.team, window, 1, window.final)

    assert actual.get("PTS") == 0.0, "the one man who scored was on the bench"
    assert resolved.get("PTS") == pytest.approx(600.0), "the re-solve starts him"
    assert built.book.drift(built.team, window, 1) > 0.0, "the re-solve beat the manager"
    assert built.book.delta(built.team, window, 1, window.final, [], []) == pytest.approx(0.0), (
        "and a move that changes nobody is worth nothing at all"
    )


def test_a_man_on_the_bench_is_seated_when_the_lineup_has_room(session: Session) -> None:
    """The re-solve plans the roster; it does not copy what was started."""
    built = build(session, starter_slot="BE")

    assert built.book.seat([built.starter, built.spare], 1) == (built.starter,)
    assert built.book.seat([built.starter, built.spare], TODAY) == (built.spare,), (
        "only the man with a game that day"
    )
    assert built.book.seat([built.blocker], 3) == (), "nobody has a game on day 3"


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
    assert built.book.drift(built.team, missing, 8) == 0.0
