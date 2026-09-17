"""The listener's routes, over rows the real pass wrote from a fake ESPN."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.db.session import make_engine, make_session_factory
from app.ingest_runs import record_run
from app.listener.status import next_pass_after, run_status_pass
from app.main import create_app
from tests.fakes import attach_pool, fake_league, fake_pool_entry, fake_pro_game, fake_team

REPO_ROOT = Path(__file__).resolve().parent.parent

LEAGUE_ID = 3853870
SEASON = 2027
FIRST = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
SECOND = datetime(2026, 11, 3, 22, 30, tzinfo=UTC)


def _league(entries: list[dict[str, Any]], news: dict[int, list[dict[str, Any]]]) -> Any:
    league = fake_league(league_id=LEAGUE_ID, season=SEASON)
    league.teams = [fake_team(3, "Through The Wire"), fake_team(21, "Load Management")]
    game_ms = int((FIRST + timedelta(days=1)).timestamp() * 1000)
    schedule = {
        13: {"1": [fake_pro_game(13, 25, game_ms)]},
        25: {"1": [fake_pro_game(13, 25, game_ms)]},
    }
    return attach_pool(league, entries, schedule=schedule, news=news, scoring_period=1)


def _pass(session: Session, league: Any, now: datetime) -> None:
    run_status_pass(
        session,
        league,
        label="morning",
        now=now,
        tracked_team_id=3,
        next_pass_at=next_pass_after(now),
    )
    session.commit()


@pytest.fixture(scope="module")
def seeded(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")

    engine = make_engine(test_database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        first = [
            fake_pool_entry(100, "Star", on_team_id=3, percent_owned=99.0),
            fake_pool_entry(200, "Rival Guy", on_team_id=21, percent_owned=80.0),
            fake_pool_entry(300, "Wire Guy", percent_owned=12.0, percent_change=1.0),
        ]
        _pass(
            session,
            _league(
                first, {100: [{"published": int(FIRST.timestamp() * 1000), "headline": "Fit"}]}
            ),
            FIRST,
        )
        second = [
            fake_pool_entry(100, "Star", on_team_id=3, percent_owned=99.0, injury_status="OUT"),
            fake_pool_entry(200, "Rival Guy", on_team_id=21, percent_owned=80.0),
            fake_pool_entry(300, "Wire Guy", percent_owned=20.0, percent_change=8.0),
        ]
        _pass(session, _league(second, {}), SECOND)
    yield factory
    engine.dispose()


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


EVENTS = f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/events"


def test_events_list_newest_first_with_the_player_named(client: TestClient) -> None:
    body = client.get(EVENTS).json()
    assert body["total"] == 2
    kinds = {(e["player_name"], e["kind"]) for e in body["items"]}
    assert kinds == {("Star", "went_out"), ("Wire Guy", "ownership_surge")}
    [surge] = [e for e in body["items"] if e["kind"] == "ownership_surge"]
    assert surge["espn_player_id"] == 300
    assert surge["previous"] == {"percent_owned": 12.0, "percent_change": 1.0}
    assert surge["current"] == {"percent_owned": 20.0, "percent_change": 8.0}
    assert surge["detail"] == {"crossed_line": False}
    assert surge["notified_at"] is None
    assert surge["observed_at"] == "2026-11-03T22:30:00Z"


def test_events_filter_by_kind_team_and_time(client: TestClient) -> None:
    assert [
        e["kind"] for e in client.get(EVENTS, params={"kinds": ["went_out"]}).json()["items"]
    ] == ["went_out"]
    on_three = client.get(EVENTS, params={"team": 3}).json()
    assert [e["player_name"] for e in on_three["items"]] == ["Star"]
    on_wire = client.get(EVENTS, params={"team": 0}).json()
    assert [e["player_name"] for e in on_wire["items"]] == ["Wire Guy"]
    assert client.get(EVENTS, params={"team": 21}).json()["total"] == 0
    assert client.get(EVENTS, params={"since": "2026-11-03T22:30:00Z"}).json()["total"] == 0
    assert client.get(EVENTS, params={"since": "2026-11-03T22:29:00Z"}).json()["total"] == 2


def test_an_unknown_kind_is_rejected(client: TestClient) -> None:
    response = client.get(EVENTS, params={"kinds": ["retired"]})
    assert response.status_code == 422
    assert "retired" in response.json()["detail"]


def test_events_for_an_unknown_season_are_404(client: TestClient) -> None:
    assert client.get(f"/leagues/{LEAGUE_ID}/seasons/1999/events").status_code == 404


def test_a_players_status_history_is_newest_first(client: TestClient) -> None:
    body = client.get("/players/100/status").json()
    assert body["total"] == 2
    latest, earlier = body["items"]
    assert (latest["observed_at"], latest["injury_status"]) == ("2026-11-03T22:30:00Z", "OUT")
    assert (earlier["observed_at"], earlier["injury_status"]) == ("2026-11-03T15:00:00Z", "ACTIVE")
    assert latest["pass_label"] == "morning"
    assert (latest["on_team_id"], latest["status"]) == (3, "ONTEAM")
    assert client.get("/players/100/status", params={"season": 2026}).json()["total"] == 0
    assert client.get("/players/999/status").status_code == 404


def test_a_players_news_is_served(client: TestClient) -> None:
    body = client.get("/players/100/news").json()
    assert body["total"] == 1
    [item] = body["items"]
    assert (item["headline"], item["source"]) == ("Fit", "espn")
    assert item["published"] == "2026-11-03T15:00:00Z"
    assert client.get("/players/300/news").json()["total"] == 0
    assert client.get("/players/999/news").status_code == 404


def test_health_can_be_asked_about_the_listener_alone(
    client: TestClient, seeded: sessionmaker[Session]
) -> None:
    with record_run(seeded, espn_league_id=LEAGUE_ID, season=SEASON, mode="recent"):
        pass

    overall = client.get(f"/ingest-runs/health/{SEASON}").json()
    assert overall["stale"] is False and overall["mode"] is None

    listener = client.get(f"/ingest-runs/health/{SEASON}", params={"mode": "status"}).json()
    assert listener["stale"] is True, "a healthy ingest must not hide a silent listener"
    assert listener["mode"] == "status" and listener["last_status"] is None

    with record_run(seeded, espn_league_id=LEAGUE_ID, season=SEASON, mode="status") as detail:
        detail["label"] = "report"
    listener = client.get(f"/ingest-runs/health/{SEASON}", params={"mode": "status"}).json()
    assert listener["stale"] is False and listener["last_status"] == "succeeded"
    [run] = client.get("/ingest-runs", params={"season": SEASON, "status": "succeeded"}).json()[
        "items"
    ][:1]
    assert run["mode"] == "status" and run["detail"] == {"label": "report"}


def test_the_current_listener_health_follows_the_season_it_records(
    client: TestClient, seeded: sessionmaker[Session]
) -> None:
    """In September the calendar says last season; the listener already watches the next."""
    with record_run(seeded, espn_league_id=LEAGUE_ID, season=2099, mode="status"):
        pass
    body = client.get("/ingest-runs/health", params={"mode": "status"}).json()
    assert body["season"] == 2099 and body["stale"] is False
