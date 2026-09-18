"""The recommender's two routes, over a seeded league.

One small season is built once for the module: a four-man roster on a
three-slot lineup, two men on the wire, and an NBA schedule every one of
them plays every day. The routes are then asked the same questions the CLI
asks, plus the two refusals that matter -- a team that does not exist, and
a season the listener has never run for.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.main import create_app
from app.pickups.bids import clear_cache
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    configure,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

SEASON = 2026
QUIET_SEASON = 2025
HOME, AWAY, NOBODY = 1, 2, 99

EVERY_DAY = list(range(1, 15))

STARTER = {
    "PTS": 20.0,
    "REB": 8.0,
    "AST": 4.0,
    "STL": 1.2,
    "BLK": 0.8,
    "3PM": 2.0,
    "TO": 2.4,
    "FGM": 8.0,
    "FGA": 17.0,
    "FTM": 4.0,
    "FTA": 5.0,
}


#: What the two sides have posted so far. These two lines are also the only
#: evidence `category_distributions` has for this league, so they are what
#: the season's opponent spread ends up being measured from.
POSTED = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 20.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 235.0,
    "FGA": 500.0,
    "FTM": 78.0,
    "FTA": 100.0,
}


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in STARTER.items()}


def scaled_totals(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in POSTED.items()}


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        ls, (home, away), (first, _) = league_season(session, season=SEASON, days_per_period=7)
        configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        matchup(session, first, home, away, {home: POSTED, away: scaled_totals(0.8)})
        for name, factor, team in (
            ("A", 1.0, home),
            ("B", 1.0, home),
            ("C", 1.0, home),
            ("Weak", 0.5, home),
            ("Rival", 1.0, away),
        ):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=10, on_team_id=team.espn_team_id, season=SEASON)
            projected(session, who, 70, scaled(factor), season=SEASON)
            held(session, team, first, who, 1, season=SEASON)
        for name, factor, pro_team in (("Star", 1.4, 20), ("Scrub", 0.2, 21)):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=pro_team, on_team_id=0, season=SEASON)
            projected(session, who, 70, scaled(factor), season=SEASON)
            on_the_wire(session, ls, who)
        for pro_team in (10, 20, 21):
            games(session, pro_team, EVERY_DAY, season=SEASON)
        # A season nobody listened to: settings and teams, no schedule and
        # no snapshots, which is what a report cannot be built from.
        league_season(session, season=QUIET_SEASON, days_per_period=7)
        session.commit()
    clear_cache()
    yield scoring_factory


@pytest.fixture
def client(seeded: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app()

    def override() -> Iterator[Session]:
        with seeded() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def url(team: int = HOME, season: int = SEASON, which: str = "stream") -> str:
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/pickups/{which}"


def test_the_stream_route_reports_the_week_and_the_moves(client: TestClient) -> None:
    body = client.get(url(), params={"today": 1}).json()

    assert body["espn_team_id"] == HOME
    assert body["matchup_period"] == 1
    assert body["scoring_periods_remaining"] == [1, 2, 3, 4, 5, 6, 7]
    assert body["opponent_espn_team_id"] == AWAY
    assert body["faab_remaining"] == 100
    assert body["ir_slot_free"] is False
    assert body["moves"], "both free agents are legal pickups"
    names = {move["add"]["name"] for move in body["moves"]}
    assert names == {"Star", "Scrub"}
    star = next(move for move in body["moves"] if move["add"]["name"] == "Star")
    assert star["add"]["espn_player_id"] > 0, "players go out as ESPN ids"
    assert star["kind"] in ("swap", "add")
    assert isinstance(star["clears_hurdle"], bool)


def test_the_season_route_reports_the_drops_and_the_churn(client: TestClient) -> None:
    body = client.get(url(which="season"), params={"today": 1}).json()

    assert body["espn_team_id"] == HOME
    assert body["today"] == 1
    assert body["last_scoring_period"] == 14
    assert body["weeks_remaining"] == 2.0
    assert body["pool_size"] == 2
    assert body["drops"][0]["player"]["name"] == "Weak"
    assert body["drops"][0]["replacement"]["name"] == "Star"
    assert body["best_swap"]["out"][0]["name"] == "Weak"
    assert body["best_swap"]["into"][0]["name"] == "Star"
    assert body["best_swap"]["costs_faab"] is True
    assert body["churn"] == {"adds": 0, "days": 14, "finding": body["churn"]["finding"]}
    assert "r = -0.63" in body["churn"]["finding"]
    assert body["stashes"] == []


def test_the_day_defaults_to_the_calendars_own(client: TestClient) -> None:
    body = client.get(url(which="season")).json()

    assert body["today"] >= 1, "the calendar names a day without one being passed"
    assert body["last_scoring_period"] == 14


def test_an_unknown_team_is_404(client: TestClient) -> None:
    for which in ("stream", "season"):
        response = client.get(url(team=NOBODY, which=which), params={"today": 1})
        assert response.status_code == 404
        assert "team 99" in response.json()["detail"]


def test_a_season_the_listener_never_saw_is_409(client: TestClient) -> None:
    for which in ("stream", "season"):
        response = client.get(url(season=QUIET_SEASON, which=which), params={"today": 1})
        assert response.status_code == 409
        assert "listener has stored nothing" in response.json()["detail"]


def test_an_unknown_season_is_still_404(client: TestClient) -> None:
    assert client.get(url(season=1999)).status_code == 404
