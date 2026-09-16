"""Draft grades: the price curve, what each pick became, and the market read."""

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import DraftPick, Player, Team
from app.scoring.draft import DRAFT_BAND, draft_grades, expected_value
from tests.scoring_db import held, league_season, matchup, player

LEAGUE = {"PTS": 550, "REB": 200, "AST": 120, "STL": 35, "BLK": 20, "3PM": 60, "TO": 60}
CORE = {"PTS": 500, "REB": 180, "AST": 110, "STL": 31, "BLK": 17, "3PM": 54, "TO": 56}
GOOD = {"PTS": 25, "REB": 8, "AST": 5, "STL": 2, "BLK": 1, "3PM": 3, "TO": 2}


def pick(session: Session, team: Team, who: Player, price: int, number: int) -> None:
    session.add(
        DraftPick(
            league_season_id=team.league_season_id,
            player_id=who.id,
            team_id=team.id,
            round_num=1,
            round_pick=number,
            bid_amount=price,
        )
    )
    session.flush()


def test_the_price_curve_rises_with_the_square_root() -> None:
    assert expected_value(1) == pytest.approx(0.1239, abs=1e-3)
    assert expected_value(0) == expected_value(1)
    assert expected_value(20) - expected_value(10) == pytest.approx(DRAFT_BAND, abs=0.01)


def test_each_pick_is_kept_or_dropped_and_read_against_the_market(
    scoring_session: Session, tmp_path: Path
) -> None:
    session = scoring_session
    # 2023: stored projections are not forecasts, so there is no board and no
    # decision lens, but the result and the market still read.
    _, (home, away), periods = league_season(session, season=2023, periods=3)
    for period in periods:
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
    core = player(session, "Core", espn_id=1)
    keeper = player(session, "Keeper", espn_id=2)
    bust = player(session, "Bust", espn_id=3)
    for index, period in enumerate(periods):
        day = index * 7 + 1
        held(session, home, period, core, day, stats=CORE, season=2023)
        held(session, home, period, keeper, day + 1, stats=GOOD, season=2023)
    held(session, home, periods[0], bust, 3, slot="BE", season=2023)
    pick(session, home, keeper, 30, 1)
    pick(session, home, bust, 5, 2)
    (tmp_path / "2023.json").write_text(json.dumps({"2": {"aav": 24.6}, "3": {"aav": None}}))

    grades = {g.name: g for g in draft_grades(session, 2023, home.id, price_cache=tmp_path)}
    assert list(grades) == ["Keeper", "Bust"]
    kept, dropped = grades["Keeper"], grades["Bust"]
    assert kept.outcome == "kept" and dropped.outcome == "dropped"
    assert kept.market == 25 and kept.market_source == "ESPN average"
    assert dropped.market is None
    assert kept.projected_value is None and kept.decision is None and kept.verdict is None
    assert kept.delivered > 0
    assert dropped.delivered == 0
    assert dropped.result == pytest.approx(-expected_value(5))
