"""The projection upload routes, over the real test database.

The upload path is what lets anyone who does not pay Basketball Monster use
the room at all (docs/projection_sources.md), so these pin what a manager
actually does through it: preview a file and read back the mapping it guessed
before trusting it, have a file the importer cannot use refused with the
reason rather than half-imported, store a set, and read the set and its lines
back afterwards.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.db.models import PlayerSeasonStat
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from tests.scoring_db import player

REPO_ROOT = Path(__file__).resolve().parent.parent

SEASON = 2027

#: A per-game file, with the columns spelled the way a real site spells them.
PER_GAME = """Player,Team,Pos,GP,PTS,REB,AST,STL,BLK,3PM,TO,FGM,FGA,FTM,FTA,ADP
Evan Mobley,CLE,PF,70,18.5,9.3,3.2,0.9,1.6,1.1,2.1,7.2,13.4,3.0,4.1,21
Cameron Boozer,CHA,PF/C,65,14.0,7.5,2.0,0.8,0.9,0.7,1.8,5.5,11.0,2.5,3.4,48
"""

#: A percentage and no attempts behind it, which cannot become a roster line.
NO_ATTEMPTS = (
    "Player,GP,PTS,REB,AST,STL,BLK,3PM,TOV,FG%,FT%\nEvan Mobley,70,18.5,9,3,1,2,1,2,.54,.73\n"
)


@pytest.fixture(scope="module")
def seeded(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    """A migrated test database holding one player the names can match."""
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
        mobley = player(session, "Evan Mobley")
        session.add(
            PlayerSeasonStat(
                player_id=mobley.id,
                season=SEASON,
                kind="projected",
                games_played=70.0,
                raw_totals={},
                eligible_slots=["PF", "C", "F", "UT"],
                primary_position="PF",
            )
        )
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def client(seeded: sessionmaker[Session]) -> Iterator[TestClient]:
    """A client whose requests use the seeded test database, never DATABASE_URL."""
    app = create_app()

    def override() -> Iterator[Session]:
        with seeded() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def no_sets(seeded: sessionmaker[Session]) -> Iterator[None]:
    """Each case starts with nothing stored, so listing is answerable."""
    with seeded() as session:
        session.execute(text("TRUNCATE projection_sets RESTART IDENTITY CASCADE"))
        session.commit()
    yield


def upload(body: str = PER_GAME, name: str = "projections.csv") -> dict[str, Any]:
    return {"file": (name, body.encode("utf-8"), "text/csv")}


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_a_preview_reports_the_mapping_and_stores_nothing(client: TestClient) -> None:
    response = client.post(
        "/projections/sets/preview",
        files=upload(),
        data={"season": str(SEASON), "name": "preseason"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["set_id"] is None
    assert body["basis"] == "per_game"
    assert body["column_map"]["fields"]["PTS"] == "PTS"
    assert body["column_map"]["fields"]["games"] == "GP"
    assert body["column_map"]["basis"] == "per_game"
    assert body["column_map"]["ignored"] == ["ADP"]
    assert (body["rows_read"], body["rows_stored"], body["matched"]) == (2, 2, 1)
    assert body["unmatched"] == ["Cameron Boozer"]
    assert body["rejected"] == []

    assert client.get("/projections/sets").json() == []


def test_a_preview_names_the_row_it_could_not_read(client: TestClient) -> None:
    body = PER_GAME + "Broken Row,CLE,PG,70,many,1,1,1,1,1,1,1,2,1,2,99\n"
    reported = client.post(
        "/projections/sets/preview", files=upload(body), data={"season": str(SEASON)}
    ).json()

    assert reported["rows_read"] == 3
    assert reported["rows_stored"] == 2
    assert len(reported["rejected"]) == 1
    assert reported["rejected"][0]["where"] == "row 4"
    assert "Broken Row" in reported["rejected"][0]["why"]
    assert "not a number" in reported["rejected"][0]["why"]


def test_a_map_override_corrects_the_guess(client: TestClient) -> None:
    body = PER_GAME.replace("PTS", "Scoring")
    guessed = client.post(
        "/projections/sets/preview", files=upload(body), data={"season": str(SEASON)}
    ).json()
    assert guessed["ok"] is False
    assert any("PTS is missing" in reason for reason in guessed["reasons"])

    mapped = client.post(
        "/projections/sets/preview",
        files=upload(body),
        data={"season": str(SEASON), "map": ["Scoring=PTS"]},
    ).json()
    assert mapped["ok"] is True
    assert mapped["column_map"]["fields"]["PTS"] == "Scoring"


# ---------------------------------------------------------------------------
# a file that cannot be used
# ---------------------------------------------------------------------------


def test_a_percentage_with_no_attempts_is_refused_with_the_reason(client: TestClient) -> None:
    """The refusal the CLI makes: a roster's FG% is makes over attempts."""
    previewed = client.post(
        "/projections/sets/preview",
        files=upload(NO_ATTEMPTS, "rates_only.csv"),
        data={"season": str(SEASON), "name": "no attempts"},
    )
    assert previewed.status_code == 200
    assert previewed.json()["ok"] is False

    response = client.post(
        "/projections/sets",
        files=upload(NO_ATTEMPTS, "rates_only.csv"),
        data={"season": str(SEASON), "name": "no attempts"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["set_id"] is None
    assert detail["rows_stored"] == 0
    said = " ".join(detail["reasons"])
    assert "a percentage cannot be rebuilt" in said
    assert "app/scoring/lines.py" in said

    assert client.get("/projections/sets").json() == []


def test_a_file_no_reader_handles_is_refused(client: TestClient) -> None:
    response = client.post(
        "/projections/sets",
        files=upload(PER_GAME, "projections.json"),
        data={"season": str(SEASON), "name": "wrong format"},
    )

    assert response.status_code == 422
    assert "not a projection file" in response.json()["detail"]


def test_a_malformed_map_entry_is_refused(client: TestClient) -> None:
    response = client.post(
        "/projections/sets/preview",
        files=upload(),
        data={"season": str(SEASON), "map": ["Points"]},
    )

    assert response.status_code == 422
    assert "HEADER=FIELD" in response.json()["detail"]


def test_a_set_without_a_name_is_refused(client: TestClient) -> None:
    """The name is required to store, and optional to preview."""
    stored = client.post("/projections/sets", files=upload(), data={"season": str(SEASON)})
    assert stored.status_code == 422
    previewed = client.post(
        "/projections/sets/preview", files=upload(), data={"season": str(SEASON)}
    )
    assert previewed.status_code == 200


# ---------------------------------------------------------------------------
# storing a set and reading it back
# ---------------------------------------------------------------------------


def test_a_stored_set_is_listed_and_read_back_as_per_game_lines(client: TestClient) -> None:
    stored = client.post(
        "/projections/sets",
        files=upload(),
        data={
            "season": str(SEASON),
            "name": "Hashtag preseason",
            "note": "from a site I pay for",
        },
    )

    assert stored.status_code == 200
    report = stored.json()
    assert report["ok"] is True
    assert report["dry_run"] is False
    set_id = report["set_id"]
    assert set_id is not None
    assert report["rows_stored"] == 2
    assert report["filename"] == "projections.csv"

    listed = client.get("/projections/sets", params={"season": SEASON}).json()
    assert [(s["id"], s["name"], s["rows"], s["owner"]) for s in listed] == [
        (set_id, "Hashtag preseason", 2, "patrick")
    ]
    assert listed[0]["source_note"] == "from a site I pay for"
    assert listed[0]["column_map"]["fields"]["FGA"] == "FGA"
    assert client.get("/projections/sets", params={"season": SEASON + 1}).json() == []

    one = client.get(f"/projections/sets/{set_id}").json()
    assert one["name"] == "Hashtag preseason"
    assert one["uploaded_at"]

    rows = client.get(f"/projections/sets/{set_id}/rows").json()
    assert [row["name"] for row in rows] == ["Cameron Boozer", "Evan Mobley"]
    mobley = rows[1]
    assert mobley["espn_player_id"] is not None
    assert mobley["games"] == 70.0
    assert mobley["team"] == "CLE"
    assert mobley["per_game"]["PTS"] == pytest.approx(18.5)
    assert mobley["per_game"]["FGA"] == pytest.approx(13.4)
    # Nobody of ours is called Cameron Boozer: he is stored, and unmatched.
    assert rows[0]["espn_player_id"] is None


def test_a_totals_file_is_measured_as_totals_and_stored_per_game(client: TestClient) -> None:
    body = (
        "Player,GP,PTS,REB,AST,STL,BLK,3PM,TO,FGM,FGA,FTM,FTA\n"
        "Evan Mobley,70,1295,651,224,63,112,77,147,504,938,210,287\n"
    )
    stored = client.post(
        "/projections/sets",
        files=upload(body, "totals.csv"),
        data={"season": str(SEASON), "name": "totals"},
    ).json()

    assert stored["basis"] == "totals"
    rows = client.get(f"/projections/sets/{stored['set_id']}/rows").json()
    assert rows[0]["per_game"]["PTS"] == pytest.approx(1295 / 70)


def test_a_set_nobody_stored_is_a_404(client: TestClient) -> None:
    assert client.get("/projections/sets/9999").status_code == 404
    assert client.get("/projections/sets/9999/rows").status_code == 404
    assert "no projection set" in client.get("/projections/sets/9999").json()["detail"]
