"""The knowable line: projection shrunk toward season to date, a little recent form."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.db.models import PlayerProjectionSnapshot, PlayerSeasonStat
from app.scoring.knowable import PRIOR_GAMES, RECENT_WEIGHT, knowable, knowable_line
from tests.scoring_db import held, league_season, player


def project(session: Session, player_id: int, season: int, points: float, games: float) -> None:
    session.add(
        PlayerSeasonStat(
            player_id=player_id,
            season=season,
            kind="projected",
            games_played=games,
            points=points,
            raw_totals={},
        )
    )
    session.flush()


def test_before_any_game_the_projection_is_the_line(scoring_session: Session) -> None:
    session = scoring_session
    who = player(session, "Rookie")
    project(session, who.id, 2026, points=1500, games=75)
    line = knowable(session, who.id, 2026, day=1)
    assert line.per_game.get("PTS") == pytest.approx(20.0)
    assert knowable_line(session, who.id, 2026, 1, games=3).get("PTS") == pytest.approx(60.0)


def test_games_played_pull_the_line_toward_what_he_is_doing(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, _), (week1, week2, week3) = league_season(session, periods=3)
    who = player(session, "Breakout")
    project(session, who.id, 2026, points=750, games=75)  # 10 a game
    for day in range(1, 16):  # 15 games at 30, the last 14 days of them recent
        period = (week1, week2, week3)[(day - 1) // 7]
        held(session, home, period, who, day, stats={"PTS": 30})
    line = knowable(session, who.id, 2026, day=16)
    base = 0.5 * 30 + 0.5 * 10  # 15 games against PRIOR_GAMES of 15
    assert PRIOR_GAMES == 15
    expected = (1 - RECENT_WEIGHT) * base + RECENT_WEIGHT * 30
    assert line.per_game.get("PTS") == pytest.approx(expected)
    assert line.games_so_far == 15


def test_the_day_itself_is_not_knowable(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, _), (week1, _) = league_season(session)
    who = player(session, "Tonight")
    held(session, home, week1, who, 3, stats={"PTS": 50})
    assert knowable(session, who.id, 2026, day=3).per_game.get("PTS") == 0.0
    assert knowable(session, who.id, 2026, day=4).per_game.get("PTS") == pytest.approx(50.0)


def test_an_unusable_projection_season_leans_on_games_alone(scoring_session: Session) -> None:
    session = scoring_session
    _, (home, _), (week1, _) = league_season(session, season=2023)
    who = player(session, "Snapshot")
    project(session, who.id, 2023, points=3000, games=75)
    held(session, home, week1, who, 2, stats={"PTS": 12}, season=2023)
    line = knowable(session, who.id, 2023, day=5)
    assert not line.had_projection
    assert line.per_game.get("PTS") == pytest.approx(12.0)


def test_a_saved_snapshot_stands_in_for_the_preseason_projection(
    scoring_session: Session,
) -> None:
    session = scoring_session
    who = player(session, "Revised")
    project(session, who.id, 2027, points=750, games=75)  # 10 a game preseason
    for captured, points in ((date(2026, 11, 1), 1500.0), (date(2026, 11, 20), 1800.0)):
        session.add(
            PlayerProjectionSnapshot(
                player_id=who.id,
                season=2027,
                captured_on=captured,
                kind="projected",
                games_played=60.0,
                stats={"PTS": points, "GP": 60.0},
            )
        )
    session.flush()
    early = knowable(session, who.id, 2027, day=1, as_of=date(2026, 11, 10))
    assert early.source == "snapshot"
    assert early.per_game.get("PTS") == pytest.approx(25.0)
    assert knowable(session, who.id, 2027, day=1).per_game.get("PTS") == pytest.approx(10.0)
    before_any = knowable(session, who.id, 2027, day=1, as_of=date(2026, 10, 1))
    assert before_any.source == "blend"
