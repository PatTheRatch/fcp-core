"""Replacement level: a typical pickup's started value, in categories a week."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import MatchupPeriod, Team
from app.scoring.replacement import pickup_values, replacement_value
from app.scoring.season import SeasonBook
from tests.scoring_db import held, league_season, matchup, player, transaction

AVERAGE = {"PTS": 550, "REB": 200, "AST": 120, "STL": 35, "BLK": 20, "3PM": 60, "TO": 60}


def build(session: Session) -> tuple[Team, Team, list[MatchupPeriod]]:
    _, (home, away), periods = league_season(session, periods=4, regular_season_periods=3)
    for period in periods[:3]:
        matchup(
            session,
            period,
            home,
            away,
            {
                home: {k: v * 0.9 for k, v in AVERAGE.items()},
                away: {k: v * 1.1 for k, v in AVERAGE.items()},
            },
        )
    return home, away, periods


def test_a_pickup_is_valued_on_his_started_days_in_the_window(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = build(session)
    regular = player(session, "Regular")
    streamer = player(session, "Streamer")
    stash = player(session, "Stash")
    # The rest of the roster, one team-sized line a week, just under the league.
    for day in (1, 8):
        period = periods[(day - 1) // 7]
        held(
            session,
            home,
            period,
            regular,
            day,
            stats={"PTS": 540, "REB": 195, "AST": 118, "STL": 34, "BLK": 19, "3PM": 59, "TO": 60},
        )
    held(session, home, periods[0], streamer, 3, stats={"PTS": 20, "BLK": 3, "FGM": 8, "FGA": 14})
    held(session, home, periods[0], stash, 4, slot="BE", stats={"PTS": 30})
    transaction(session, home, 2, "WAIVER", [("ADD", streamer, None, home)])
    transaction(session, home, 2, "FREEAGENT", [("ADD", stash, None, home)])
    transaction(session, home, 2, "WAIVER", [("ADD", regular, None, home)], status="CANCELED")

    values = sorted(pickup_values(SeasonBook.load(session, 2026)))
    assert len(values) == 2
    assert values[0] == 0.0  # benched all window: an unused pickup is worth nothing
    assert values[1] > 0.0
    result = replacement_value(session, 2026)
    assert result.n == 2
    assert result.value == pytest.approx(values[1] / 2)


def test_playoff_weeks_are_not_part_of_replacement_level(scoring_session: Session) -> None:
    session = scoring_session
    home, _, periods = build(session)
    late = player(session, "Late")
    held(session, home, periods[3], late, 23, stats={"PTS": 40})
    transaction(session, home, 21, "WAIVER", [("ADD", late, None, home)])
    with pytest.raises(ValueError):
        replacement_value(session, 2026)
