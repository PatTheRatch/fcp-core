"""Player value per team-season: team fit and league standard, stretches kept apart."""

import pytest
from sqlalchemy.orm import Session

from app.scoring.players import player_values
from tests.scoring_db import held, league_season, matchup, player

LEAGUE = {"PTS": 550, "REB": 200, "AST": 120, "STL": 35, "BLK": 20, "3PM": 60, "TO": 60}
REST = {"PTS": 520, "REB": 190, "AST": 115, "STL": 33, "BLK": 19, "3PM": 57, "TO": 58}
SCORER = {"PTS": 30, "REB": 5, "AST": 4, "STL": 1, "BLK": 0, "3PM": 3, "TO": 2}


def test_value_is_split_into_regular_season_and_playoffs(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, away), periods = league_season(session, periods=3, regular_season_periods=2)
    for period in periods[:2]:
        matchup(
            session,
            period,
            home,
            away,
            {
                home: {k: v * 0.95 for k, v in LEAGUE.items()},
                away: {k: v * 1.05 for k, v in LEAGUE.items()},
            },
        )
    rest, star, benchwarmer = (player(session, n) for n in ("Rest", "Star", "Benchwarmer"))
    for index, period in enumerate(periods):
        day = index * 7 + 1
        held(session, home, period, rest, day, stats=REST)
        held(session, home, period, star, day, stats=SCORER)
        held(session, home, period, benchwarmer, day, slot="BE", stats=SCORER)

    values = {v.name: v for v in player_values(session, 2026, home.id)}
    star_value, bench_value = values["Star"], values["Benchwarmer"]
    assert star_value.regular.weeks_started == 2
    assert star_value.playoffs.weeks_started == 1
    assert star_value.regular.team_fit > 0
    assert star_value.regular.team_fit_per_week == pytest.approx(star_value.regular.team_fit / 2)
    assert star_value.regular.league_standard > 0
    # Held all season, never started: worth nothing, and the weeks still count.
    assert bench_value.regular.team_fit == 0
    assert bench_value.regular.weeks_held == 2
    assert next(iter(values)) == "Rest"
