"""Grading moves: the wire, with both lenses and replacement for uneven counts."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import MatchupPeriod, Player, Team
from app.scoring.moves import grade_move, start_share
from app.scoring.season import SeasonBook
from app.scoring.trade_grades import trade_grades
from app.scoring.wire import wire_grades
from tests.scoring_db import held, league_season, matchup, player, transaction

LEAGUE = {"PTS": 550, "REB": 200, "AST": 120, "STL": 35, "BLK": 20, "3PM": 60, "TO": 60}
CORE = {"PTS": 500, "REB": 180, "AST": 110, "STL": 31, "BLK": 17, "3PM": 54, "TO": 56}
GOOD = {"PTS": 25, "REB": 8, "AST": 5, "STL": 2, "BLK": 1, "3PM": 3, "TO": 2}
POOR = {"PTS": 6, "REB": 2, "AST": 1, "STL": 0, "BLK": 0, "3PM": 0, "TO": 1}
#: A line nothing on the day could have forecast, for the window's own test.
MONSTROUS = {"PTS": 90, "REB": 30, "AST": 20, "STL": 8, "BLK": 6, "3PM": 12, "TO": 0}

#: A deal on day 7 and thirty days after it, the short window the trade
#: calibration grades over (`scripts/trade_calibration.py`).
MOVED_ON = 7
SHORT_WINDOW = 30


def season(session: Session, periods: int = 4) -> tuple[Team, Team, list[MatchupPeriod]]:
    _, (home, away), periods_built = league_season(
        session, periods=periods, regular_season_periods=periods
    )
    for period in periods_built:
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
    return home, away, periods_built


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


def _eight_weeks(
    session: Session, late: dict[str, float] | None = None
) -> tuple[Team, list[MatchupPeriod], Player]:
    """Eight seven-day periods, a core and a man acquired on day 7.

    He is held to the end, so nothing but the window can shorten the grade.
    `late` is what he posts in periods 7 and 8 -- everything after day 42, and
    so everything outside the thirty days.
    """
    home, _, periods = season(session, periods=8)
    core, good = player(session, "Core"), player(session, "Good")
    for index, period in enumerate(periods):
        day = index * 7 + 1
        held(session, home, period, core, day, stats=CORE)
        if index:
            held(session, home, period, good, day + 1, stats=late if index >= 6 else GOOD)
    return home, periods, good


def test_a_short_window_keeps_the_matchup_periods_that_begin_inside_it(
    scoring_session: Session,
) -> None:
    """`within` is the short horizon the trade calibration grades over.

    A deal on day 7 and thirty days after it is day 37. Period 6 begins on day
    36 and ends on day 42: it is counted whole, because the grade compares a
    period's totals with the opponent's and half a matchup has no opponent, so
    a period that starts inside the window and ends a few days outside it is
    the one approximation in the cut. Period 7 begins on day 43 and is out.
    """
    session = scoring_session
    home, _periods, good = _eight_weeks(session, GOOD)
    book = SeasonBook.load(session, 2026)

    whole = grade_move(book, home.id, MOVED_ON, [good.id], [], replacement=0.0)
    short = grade_move(
        book, home.id, MOVED_ON, [good.id], [], replacement=0.0, within=MOVED_ON + SHORT_WINDOW
    )

    assert whole is not None and short is not None
    assert whole.periods == (2, 3, 4, 5, 6, 7, 8), "the rest of the season, as before"
    assert short.periods == (2, 3, 4, 5, 6), "period 6 begins on day 36; period 7 on day 43"
    assert short.day == whole.day and short.players_in == whole.players_in


def test_the_short_window_does_not_read_what_happened_after_it(
    scoring_session: Session,
) -> None:
    """The window's own no-look-ahead, on a fixture where the future exists.

    The man posts lines nothing could have forecast, but only in periods 7 and
    8 -- outside the thirty days. The two grades are taken, every row after day
    42 is deleted, and they are taken again. The short grade must not move; the
    rest-of-season grade must, or the fixture is not testing anything.
    """
    session = scoring_session
    home, _periods, good = _eight_weeks(session, MONSTROUS)
    session.commit()

    def graded() -> tuple[float, float]:
        book = SeasonBook.load(session, 2026)
        whole = grade_move(book, home.id, MOVED_ON, [good.id], [], replacement=0.0)
        short = grade_move(
            book, home.id, MOVED_ON, [good.id], [], replacement=0.0, within=MOVED_ON + SHORT_WINDOW
        )
        assert whole is not None and short is not None
        return whole.result, short.result

    with_the_future = graded()
    session.execute(text("DELETE FROM player_game_stats WHERE scoring_period > 42"))
    session.execute(text("DELETE FROM daily_lineup_slots WHERE scoring_period > 42"))
    session.commit()
    without_it = graded()

    assert without_it[1] == pytest.approx(with_the_future[1]), "the short grade reads 30 days"
    assert without_it[0] != pytest.approx(with_the_future[0]), "the long one reads all of them"


def test_start_share_counts_only_games_while_held(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = season(session)
    swingman = player(session, "Swingman")
    held(session, home, periods[0], swingman, 1, stats=POOR)
    held(session, home, periods[0], swingman, 2, slot="BE", stats=POOR)
    held(session, home, periods[0], swingman, 3, slot="BE")  # no game: does not count
    book = SeasonBook.load(session, 2026)
    assert start_share(book, home.id, swingman.id, before_day=7) == pytest.approx(0.5)


def test_a_trade_is_graded_from_the_teams_side(scoring_session: Session) -> None:
    session = scoring_session
    home, away, periods = season(session)
    core, good, poor = (player(session, n) for n in ("Core", "Good", "Poor"))
    for index, period in enumerate(periods):
        held(session, home, period, core, index * 7 + 1, stats=CORE)
    # Swapped after day 6: Poor to away, Good to home.
    held(session, home, periods[0], poor, 5, stats=POOR)
    held(session, home, periods[0], poor, 6)
    held(session, away, periods[0], good, 5, stats=GOOD)
    held(session, away, periods[0], good, 6)
    for day in (7, 9, 15, 22):
        period = periods[(day - 1) // 7]
        held(session, home, period, good, day, stats=GOOD)
        held(session, away, period, poor, day, stats=POOR)

    grades = trade_grades(session, 2026, home.id)
    assert len(grades) == 1
    grade = grades[0]
    assert not grade.part_missing
    assert [p.name for p in grade.trade.players_in] == ["Good"]
    assert grade.regular is not None
    assert grade.regular.result > 0
