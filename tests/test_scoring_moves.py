"""Grading moves: the wire, with both lenses and replacement for uneven counts."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import MatchupPeriod, Team
from app.scoring.moves import grade_move, start_share
from app.scoring.season import SeasonBook
from app.scoring.wire import wire_grades
from tests.scoring_db import held, league_season, matchup, player, transaction

LEAGUE = {"PTS": 550, "REB": 200, "AST": 120, "STL": 35, "BLK": 20, "3PM": 60, "TO": 60}
CORE = {"PTS": 500, "REB": 180, "AST": 110, "STL": 31, "BLK": 17, "3PM": 54, "TO": 56}
GOOD = {"PTS": 25, "REB": 8, "AST": 5, "STL": 2, "BLK": 1, "3PM": 3, "TO": 2}
POOR = {"PTS": 6, "REB": 2, "AST": 1, "STL": 0, "BLK": 0, "3PM": 0, "TO": 1}


def season(session: Session) -> tuple[Team, Team, list[MatchupPeriod]]:
    _, (home, away), periods = league_season(session, periods=4, regular_season_periods=4)
    for period in periods:
        matchup(
            session,
            period,
            home,
            away,
            {
                home: {k: v * 0.9 for k, v in LEAGUE.items()},
                away: {k: v * 1.1 for k, v in LEAGUE.items()},
            },
        )
    return home, away, periods


def test_swapping_a_poor_player_for_a_good_one_is_graded_up(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = season(session)
    core, good, poor = (player(session, n) for n in ("Core", "Good", "Poor"))
    for index, period in enumerate(periods):
        day = index * 7 + 1
        held(session, home, period, core, day, stats=CORE)
        if index == 0:
            held(session, home, period, poor, day + 1, stats=POOR)
        else:
            held(session, home, period, good, day + 1, stats=GOOD)
    # The dropped player keeps playing elsewhere, poorly.
    for day in (9, 16, 23):
        held(session, home, periods[(day - 1) // 7], poor, day, slot="FA", stats=POOR)
    held(session, home, periods[0], good, 3, stats=GOOD)  # form before the move
    transaction(session, home, 7, "WAIVER", [("ADD", good, None, home), ("DROP", poor, home, None)])

    moves = wire_grades(session, 2026, home.id)
    assert len(moves) == 1
    move = moves[0]
    assert move.added == ("Good",) and move.dropped == ("Poor",)
    grade = move.regular
    assert grade is not None
    assert grade.periods == (2, 3, 4)
    assert grade.result > 0
    assert grade.decision > 0
    assert grade.verdict.label == "Good call, and it paid off"
    assert move.playoffs is None


def test_an_add_without_a_drop_costs_a_spot_at_replacement(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = season(session)
    core, bench = player(session, "Core"), player(session, "Bench")
    for index, period in enumerate(periods):
        held(session, home, period, core, index * 7 + 1, stats=CORE)
        held(session, home, period, bench, index * 7 + 2, slot="BE", stats=POOR)
    book = SeasonBook.load(session, 2026)
    grade = grade_move(book, home.id, 7, [bench.id], [], replacement=0.1)
    assert grade is not None
    # Never started: added nothing, and used a spot worth a typical pickup.
    assert grade.result == pytest.approx(-0.1)


def test_the_window_ends_when_the_pickup_is_gone(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = season(session)
    core, streamer = player(session, "Core"), player(session, "Streamer")
    for index, period in enumerate(periods):
        held(session, home, period, core, index * 7 + 1, stats=CORE)
    held(session, home, periods[1], streamer, 9, stats=GOOD)
    book = SeasonBook.load(session, 2026)
    grade = grade_move(book, home.id, 8, [streamer.id], [], replacement=0.0)
    assert grade is not None
    assert grade.periods == (2,)


def test_start_share_counts_only_games_while_held(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = season(session)
    swingman = player(session, "Swingman")
    held(session, home, periods[0], swingman, 1, stats=POOR)
    held(session, home, periods[0], swingman, 2, slot="BE", stats=POOR)
    held(session, home, periods[0], swingman, 3, slot="BE")  # no game: does not count
    book = SeasonBook.load(session, 2026)
    assert start_share(book, home.id, swingman.id, before_day=7) == pytest.approx(0.5)
