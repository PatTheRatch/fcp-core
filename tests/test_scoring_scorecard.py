"""The scorecard: one team's season assembled, served by the API and the CLI."""

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.main import create_app
from app.scoring.scorecard import scorecard
from scripts.scorecard import SECTIONS, render
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player, transaction

LEAGUE = {"PTS": 550, "REB": 200, "AST": 120, "STL": 35, "BLK": 20, "3PM": 60, "TO": 60}
CORE = {"PTS": 500, "REB": 180, "AST": 110, "STL": 31, "BLK": 17, "3PM": 54, "TO": 56}
GOOD = {"PTS": 25, "REB": 8, "AST": 5, "STL": 2, "BLK": 1, "3PM": 3, "TO": 2}


def build(session: Session) -> int:
    _, (home, away), periods = league_season(session, periods=3)
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
    core, pickup = player(session, "Core"), player(session, "Pickup")
    for index, period in enumerate(periods):
        held(session, home, period, core, index * 7 + 1, stats=CORE)
    for day in (9, 16):
        held(session, home, periods[(day - 1) // 7], pickup, day, stats=GOOD)
    transaction(session, home, 8, "FREEAGENT", [("ADD", pickup, None, home)])
    session.commit()
    return home.id


def test_the_scorecard_assembles_every_grade(scoring_session: Session) -> None:
    team_id = build(scoring_session)
    card = scorecard(scoring_session, 2026, team_id)
    assert card.team_name == "Home"
    assert [p.name for p in card.players] == ["Core", "Pickup"]
    assert len(card.wire) == 1 and card.wire[0].added == ("Pickup",)
    assert card.trades == [] and card.draft == []
    text = render(card, set(SECTIONS))
    assert "Home, 2026" in text
    assert "day 8 +Pickup:" in text


def test_the_api_serves_the_scorecard(
    scoring_session: Session, scoring_factory: sessionmaker[Session]
) -> None:
    build(scoring_session)
    app = create_app()

    def override() -> Iterator[Session]:
        with scoring_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        response = client.get(f"/leagues/{LEAGUE_ID}/seasons/2026/teams/1/scorecard")
        missing = client.get(f"/leagues/{LEAGUE_ID}/seasons/2026/teams/99/scorecard")
    assert response.status_code == 200
    body = response.json()
    assert body["espn_team_id"] == 1
    assert body["wire"][0]["added"] == ["Pickup"]
    assert body["wire"][0]["regular"]["verdict"]["text"].endswith("delivered.")
    assert body["players"][0]["regular"]["weeks_held"] == 3
    assert missing.status_code == 404
