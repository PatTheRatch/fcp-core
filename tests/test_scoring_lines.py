"""Weekly lines: what started players produced for a team.

The live check (every team-week of periods 3, 9 and 15 in 2019-2026 matched
ESPN's own totals) is recorded in the module docstring; these pin the rules
that check depends on: benched and injured days do not count, days without a
game add nothing, and percentages come from summed makes and attempts.
"""

import pytest
from sqlalchemy.orm import Session

from app.scoring.lines import CategoryLine, player_week_line, started_lines, team_week_line
from tests.scoring_db import held, league_season, player


def test_percentages_are_rebuilt_from_makes_and_attempts() -> None:
    volume = CategoryLine({"FGM": 9.0, "FGA": 20.0})
    two_shots = CategoryLine({"FGM": 2.0, "FGA": 2.0})
    together = (volume + two_shots).totals(["FG%"])
    assert together["FG%"] == pytest.approx(11 / 22)


def test_lines_add_and_subtract_exactly() -> None:
    a = CategoryLine({"PTS": 30.0, "TO": 3.0}, games=2)
    b = CategoryLine({"PTS": 12.0, "BLK": 2.0}, games=1)
    assert (a + b - b).counts == {"PTS": 30.0, "TO": 3.0, "BLK": 0.0}
    assert (a + b).games == 3


def test_no_attempts_reads_zero_not_an_error() -> None:
    assert CategoryLine({"PTS": 10.0}).totals(["FT%", "PTS"]) == {"FT%": 0.0, "PTS": 10.0}


def test_only_started_days_with_a_game_count(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, _), (week1, week2) = league_season(session)
    star = player(session, "Star")
    bench = player(session, "Bench")
    held(session, home, week1, star, 1, slot="PG", stats={"PTS": 30, "FGM": 10, "FGA": 20})
    held(session, home, week1, star, 2, slot="PG")  # no game that day
    held(session, home, week1, star, 3, slot="BE", stats={"PTS": 40, "FGM": 15, "FGA": 25})
    held(session, home, week1, bench, 1, slot="IR", stats={"PTS": 25})
    held(session, home, week2, star, 8, slot="UT", stats={"PTS": 10, "FGM": 5, "FGA": 5})

    line = team_week_line(session, home.id, 1)
    assert line.get("PTS") == 30
    assert line.games == 1
    assert line.totals(["FG%"])["FG%"] == pytest.approx(0.5)
    assert player_week_line(session, home.id, 2, star.id).get("PTS") == 10
    assert team_week_line(session, home.id, 1, players=[bench.id]).get("PTS") == 0


def test_a_player_traded_mid_week_counts_for_each_team_on_its_own_days(
    scoring_session: Session,
) -> None:
    session = scoring_session
    ls, (home, away), (week1, _) = league_season(session)
    mover = player(session, "Mover")
    held(session, home, week1, mover, 1, stats={"PTS": 20})
    held(session, away, week1, mover, 4, stats={"PTS": 15})

    lines = started_lines(session, ls.id)
    assert lines[(home.id, 1, mover.id)].get("PTS") == 20
    assert lines[(away.id, 1, mover.id)].get("PTS") == 15
