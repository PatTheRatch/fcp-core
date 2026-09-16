"""The league-average team and inferred punts."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import Matchup, MatchupTeamStat
from app.scoring.league import PUNT_THRESHOLD, average_team_line, category_record, punts
from tests.scoring_db import league_season, matchup


def record(session: Session, row: Matchup, team_id: int, category: str, result: str) -> None:
    stat = (
        session.query(MatchupTeamStat)
        .filter_by(matchup_id=row.id, team_id=team_id, abbreviation=category)
        .one()
    )
    stat.result = result
    session.flush()


def test_the_average_team_is_the_mean_of_every_count(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, away), (week1, week2) = league_season(session)
    matchup(
        session,
        week1,
        home,
        away,
        {home: {"PTS": 500, "FGM": 190, "FGA": 400}, away: {"PTS": 600, "FGM": 210, "FGA": 400}},
    )
    matchup(
        session,
        week2,
        home,
        away,
        {home: {"PTS": 540, "FGM": 200, "FGA": 400}, away: {"PTS": 560, "FGM": 200, "FGA": 400}},
    )
    line = average_team_line(session, 2026)
    assert line.get("PTS") == pytest.approx(550)
    assert line.totals(["FG%"])["FG%"] == pytest.approx(0.5)


def test_a_category_won_under_a_quarter_of_the_time_looks_like_a_punt(
    scoring_session: Session,
) -> None:
    session = scoring_session
    _, (home, away), periods = league_season(session, periods=8, regular_season_periods=8)
    for index, period in enumerate(periods):
        row = matchup(
            session,
            period,
            home,
            away,
            {home: {"3PM": 40, "AST": 120}, away: {"3PM": 60, "AST": 110}},
        )
        # Threes: won once in eight (12.5%). Assists: won twice and tied once (31%).
        record(session, row, home.id, "3PM", "WIN" if index == 0 else "LOSS")
        record(
            session, row, home.id, "AST", "WIN" if index < 2 else ("TIE" if index == 2 else "LOSS")
        )
    assert category_record(session, 2026, home.id) == {"3PM": 0.125, "AST": 0.3125}
    assert punts(session, 2026, home.id) == {"3PM": True, "AST": False}
    assert PUNT_THRESHOLD == 0.25


def test_playoffs_do_not_count_toward_a_punt(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, away), (regular, playoff) = league_season(
        session, periods=2, regular_season_periods=1
    )
    won = matchup(session, regular, home, away, {home: {"BLK": 30}, away: {"BLK": 10}})
    lost = matchup(session, playoff, home, away, {home: {"BLK": 5}, away: {"BLK": 25}})
    record(session, won, home.id, "BLK", "WIN")
    record(session, lost, home.id, "BLK", "LOSS")
    assert punts(session, 2026, home.id) == {"BLK": False}
