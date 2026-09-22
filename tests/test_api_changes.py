"""The "what changed" route, over a small league built row by row.

`app.inseason.changes` is proved in tests/test_inseason_changes.py; what is
under test here is the route: its window, its defaults, its refusals, and
the shape the page reads.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from tests.pickups_db import day_date, games
from tests.scoring_db import league_season, matchup, player, transaction

REPO_ROOT = Path(__file__).resolve().parent.parent

LEAGUE_ID = 3853870
SEASON = 2026
MINE = 1
DAY = 8
MORNING = datetime.combine(day_date(DAY), time(9, 0), tzinfo=UTC)
YESTERDAY = MORNING - timedelta(days=1)
SINCE = datetime.combine(day_date(DAY), time(0, 0), tzinfo=UTC)
UNTIL = datetime.combine(day_date(DAY + 1), time(0, 0), tzinfo=UTC)

CHANGES = f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/changes"


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
        ls, teams, periods = league_season(
            session, team_names=("Through The Wire", "Load Management", "Third Man")
        )
        matchup(session, periods[1], teams[0], teams[1])
        games(session, 13, list(range(1, 15)))
        transaction(
            session,
            teams[0],
            DAY,
            "WAIVER",
            [
                ("ADD", player(session, "Jock Landale"), None, teams[0]),
                ("DROP", player(session, "Zach Edey"), teams[0], None),
            ],
            processed=MORNING,
            bid=5,
        )
        transaction(
            session,
            teams[1],
            DAY,
            "FREEAGENT",
            [("ADD", player(session, "Cam Spencer"), None, teams[1])],
            processed=MORNING,
        )
        transaction(
            session,
            teams[2],
            DAY - 1,
            "FREEAGENT",
            [("ADD", player(session, "Long Ago"), None, teams[2])],
            processed=YESTERDAY,
        )
        session.commit()
        del ls
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


def _window(**extra: object) -> dict[str, object]:
    return {"since": SINCE.isoformat(), "until": UNTIL.isoformat(), **extra}


def test_the_window_is_what_the_caller_asked_for_and_is_echoed_back(client: TestClient) -> None:
    body = client.get(CHANGES, params=_window()).json()

    assert body["since"] == "2025-10-28T00:00:00Z"
    assert body["until"] == "2025-10-29T00:00:00Z"
    assert body["total"] == 2, "yesterday's pickup is outside it"
    assert [item["text"] for item in body["items"]] == [
        "Load Management added Cam Spencer.",
        "Through The Wire claimed Jock Landale for $5, dropping Zach Edey.",
    ]


def test_a_team_brings_the_flags_and_names_its_opponent(client: TestClient) -> None:
    body = client.get(CHANGES, params=_window(team_id=MINE)).json()

    assert body["team_id"] == MINE
    assert body["opponent_team_id"] == 2
    flags = {item["teams"][0]["name"]: (item["mine"], item["opponent"]) for item in body["items"]}
    assert flags == {"Through The Wire": (True, False), "Load Management": (False, True)}


def test_every_change_carries_its_parts_as_well_as_its_sentence(client: TestClient) -> None:
    body = client.get(CHANGES, params=_window(kinds=["claim"])).json()

    [claim] = body["items"]
    assert claim["kind"] == "claim"
    assert [person["name"] for person in claim["players"]] == ["Jock Landale", "Zach Edey"]
    assert all(person["espn_player_id"] > 0 for person in claim["players"])
    assert claim["teams"] == [{"espn_team_id": 1, "name": "Through The Wire"}]
    assert claim["severity"] == 3


def test_without_a_window_it_is_the_last_day(client: TestClient) -> None:
    """Nothing happened in the last day of real time, which is the point:
    the default is a window and not the whole season."""
    body = client.get(CHANGES).json()

    assert body["items"] == []
    assert datetime.fromisoformat(body["until"]) - datetime.fromisoformat(
        body["since"]
    ) == timedelta(hours=24)


def test_a_window_the_wrong_way_round_or_too_long_is_refused(client: TestClient) -> None:
    backwards = client.get(CHANGES, params={"since": UNTIL.isoformat(), "until": SINCE.isoformat()})
    assert backwards.status_code == 422
    assert "after" in backwards.json()["detail"]

    huge = client.get(
        CHANGES,
        params={"since": (UNTIL - timedelta(days=200)).isoformat(), "until": UNTIL.isoformat()},
    )
    assert huge.status_code == 422
    assert "90 days" in huge.json()["detail"]


def test_an_unknown_kind_is_refused_by_name(client: TestClient) -> None:
    response = client.get(CHANGES, params=_window(kinds=["gossip"]))
    assert response.status_code == 422
    assert "gossip" in response.json()["detail"]


def test_a_team_of_another_season_is_not_found(client: TestClient) -> None:
    assert client.get(CHANGES, params=_window(team_id=99)).status_code == 404
    assert client.get(f"/leagues/{LEAGUE_ID}/seasons/1999/changes").status_code == 404


def test_the_limit_caps_the_list_and_says_how_many_there_were(client: TestClient) -> None:
    body = client.get(CHANGES, params=_window(limit=1)).json()

    assert len(body["items"]) == 1
    assert body["total"] == 2
    assert body["limit"] == 1
