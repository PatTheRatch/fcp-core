"""Step 2: connecting a league, invites, and verified team claims.

Accounts mode, like tests/test_access.py, with ESPN monkeypatched: nothing
here talks to ESPN. What is pinned is what docs/accounts.md promises: the
login is sealed and never comes back, a bad login is a plain 422 that does
not repeat it, an invite makes a member and only a live invite does, a claim
is verified by a SWID that owns the team (braces and case forgiven) or by the
league's owner, and nobody else. And, for every request in this module, no
owner GUID, SWID or espn_s2 is in any response body.

League 111 is stored (two seasons; teams 3, 5 and 7 owned by Alice, Bob and
Carol by GUID). League 333 is not: connecting it creates only its row.
"""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, memberships, secrets_box
from app.api import access, leagues_admin
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.db.models import (
    Invite,
    League,
    LeagueConnection,
    LeagueSeason,
    Owner,
    Team,
    TeamManager,
    UserEspnIdentity,
)
from app.db.session import make_engine, make_session_factory
from app.main import _RedactTokens, create_app
from scripts import new_secrets_key

REPO_ROOT = Path(__file__).resolve().parent.parent

STORED = 111
UNSTORED = 333
SEASON = 2026
KEY = Fernet.generate_key().decode()
OWNER = "owner@example.com"

G_ALICE = "{A11CE000-0000-4000-8000-000000000001}"
G_BOB = "{B0B00000-0000-4000-8000-000000000002}"
G_CAROL = "{CA501000-0000-4000-8000-000000000003}"
G_DAVE = "{DA7E0000-0000-4000-8000-000000000004}"
GUIDS = (G_ALICE, G_BOB, G_CAROL, G_DAVE)
S2 = "AEBfakeEspnS2Cookie%2Bwith%2Fsome%3Dcharacters0123456789abcdef"
OTHER_S2 = "AEBanotherFakeCookie0123456789"
SECRETS = (S2, OTHER_S2)


def bare(guid: str) -> str:
    return guid.strip("{}")


# ---------------------------------------------------------------------------
# the database and the app
# ---------------------------------------------------------------------------


def _season(league: League, season: int, owners: dict[int, Owner]) -> LeagueSeason:
    row = LeagueSeason(
        league=league,
        season=season,
        name="Full Court Test",
        scoring_type="H2H_CATEGORY",
        team_count=len(owners),
        regular_season_periods=1,
        total_matchup_periods=1,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        median_scoring=False,
        raw_settings={},
    )
    for tid, owner in owners.items():
        row.teams.append(Team(espn_team_id=tid, name=f"Team {tid}", owners=[owner]))
    return row


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
    for name in ("fcp", "fcp.auth", "fcp.access", "fcp.leagues"):
        logging.getLogger(name).disabled = False

    engine = make_engine(test_database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        league = League(espn_league_id=STORED)
        alice, bob, carol = (Owner(espn_owner_id=g) for g in (G_ALICE, G_BOB, G_CAROL))
        session.add(league)
        session.add(_season(league, SEASON - 1, {3: alice, 5: bob}))
        session.add(_season(league, SEASON, {3: alice, 5: bob, 7: carol}))
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def session(seeded: sessionmaker[Session]) -> Iterator[Session]:
    """A clean slate of people for each test: every account table is emptied,
    and league 333 goes if a test connected it. League 111 stays."""
    with seeded() as open_session:
        open_session.execute(text("TRUNCATE users, invites RESTART IDENTITY CASCADE"))
        open_session.execute(
            text("DELETE FROM leagues WHERE espn_league_id <> :kept"), {"kept": STORED}
        )
        open_session.commit()
        yield open_session


def settings_for(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_owner_email": OWNER,
        "fcp_service_token": None,
        "fcp_public_url": "https://fcp.example.test",
        "fcp_smtp_host": None,
        "fcp_email_from": None,
        "fcp_secrets_key": KEY,
        "espn_league_id": None,
        "fcp_tracked_team_id": None,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


@pytest.fixture
def app(seeded: sessionmaker[Session], session: Session) -> Iterator[FastAPI]:
    built = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: settings_for()
    yield built
    built.dependency_overrides.clear()


#: Every response body this module's clients saw, checked after each test.
SEEN: list[str] = []


class Recording(TestClient):
    def request(self, *args: Any, **kwargs: Any) -> Any:
        response = super().request(*args, **kwargs)
        SEEN.append(response.text)
        return response


@pytest.fixture(autouse=True)
def nothing_secret_is_answered() -> Iterator[None]:
    """No owner GUID (any case, braces or not), and no espn_s2, in any response
    body of any test here: asserted on the raw text."""
    SEEN.clear()
    yield
    for body in SEEN:
        lowered = body.lower()
        for guid in GUIDS:
            assert bare(guid).lower() not in lowered, "an owner GUID was in a response"
        for secret in SECRETS:
            assert secret.lower() not in lowered, "an espn_s2 was in a response"
        assert "espn_owner_id" not in body


People = Callable[[str], TestClient]


@pytest.fixture
def person(app: FastAPI, session: Session) -> Iterator[People]:
    """A signed-in client per email: a session started directly, since the
    link itself is tests/test_access.py's business."""
    opened: list[TestClient] = []

    def make(email: str) -> TestClient:
        user = accounts.get_or_create_user(session, email)
        cookie = accounts.start_session(session, user.id)
        session.commit()
        client = Recording(app)
        client.cookies.set(access.COOKIE, cookie)
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.close()


# ---------------------------------------------------------------------------
# ESPN, faked
# ---------------------------------------------------------------------------


def espn_league(league_id: int) -> dict[str, Any]:
    """What ESPN's mSettings + mTeam answer looks like, trimmed."""
    if league_id == STORED:
        teams = [
            {"id": 3, "name": "Team 3", "owners": [G_ALICE]},
            {"id": 5, "location": "Team", "nickname": "5", "owners": [G_BOB]},
            {"id": 7, "name": "Team 7", "owners": [G_CAROL]},
        ]
        return {"seasonId": SEASON + 1, "settings": {"name": "Full Court Test"}, "teams": teams}
    return {
        "seasonId": SEASON + 1,
        "settings": {"name": "The New League"},
        "teams": [{"id": 1, "name": "Dave's Team", "owners": [G_DAVE]}],
    }


@pytest.fixture
def espn(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str, str, int]]:
    """ESPN answers for leagues 111 and 333 with S2 or OTHER_S2, refuses any
    other cookie, and has no other league. Records each call."""
    calls: list[tuple[int, str, str, int]] = []

    def fake(league_id: int, swid: str, espn_s2: str, season: int) -> dict[str, Any]:
        calls.append((league_id, swid, espn_s2, season))
        if league_id not in (STORED, UNSTORED):
            raise ESPNInvalidLeague(f"League {league_id} does not exist")
        if espn_s2 not in SECRETS:
            raise ESPNAccessDenied(f"League {league_id} cannot be accessed")
        return espn_league(league_id)

    monkeypatch.setattr(leagues_admin, "fetch_league_settings_with", fake)
    # Pinned, so which seasons are tried does not move with the calendar.
    monkeypatch.setattr(leagues_admin, "current_season", lambda: SEASON)
    return calls


def connect(client: TestClient, league: int, swid: str, s2: str = S2) -> Any:
    return client.post("/connections", json={"league_id": league, "swid": swid, "espn_s2": s2})


def standings(league: int = STORED) -> str:
    return f"/leagues/{league}/seasons/{SEASON}/standings"


def plan(team: int) -> str:
    return f"/leagues/{STORED}/seasons/{SEASON}/teams/{team}/pickups/stream"


def claim_path(team: int) -> str:
    return f"/leagues/{STORED}/seasons/{SEASON}/teams/{team}/claim"


def invite_into(owner: TestClient, league: int = STORED) -> str:
    made = owner.post(f"/leagues/{league}/invites", json={})
    assert made.status_code == 201, made.text
    return str(made.json()["path"]).rsplit("/", 1)[1]


def member(person: People, email: str, owner: TestClient) -> TestClient:
    """A signed-in member of league 111, in by an invite."""
    client = person(email)
    token = invite_into(owner)
    assert client.post(f"/invites/{token}/accept").status_code == 200
    return client


def owner_of_stored(person: People) -> TestClient:
    """Alice, who connected league 111 with her own login."""
    alice = person("alice@example.com")
    assert connect(alice, STORED, G_ALICE).status_code == 201
    return alice


# ---------------------------------------------------------------------------
# sealing
# ---------------------------------------------------------------------------


def test_sealing_round_trips_and_hides_the_value() -> None:
    settings = settings_for()
    sealed = secrets_box.seal("espn_s2 value", settings)
    assert "espn_s2 value" not in sealed
    assert secrets_box.open_(sealed, settings) == "espn_s2 value"
    assert secrets_box.seal("espn_s2 value", settings) != sealed, "a fresh nonce each time"

    other = settings_for(fcp_secrets_key=Fernet.generate_key().decode())
    with pytest.raises(secrets_box.SecretsUnreadableError):
        secrets_box.open_(sealed, other)
    with pytest.raises(secrets_box.SecretsUnreadableError):
        secrets_box.open_(sealed[:-4] + "AAAA", settings)


def test_without_a_key_nothing_is_sealed() -> None:
    keyless = settings_for(fcp_secrets_key=None)
    assert not secrets_box.configured(keyless)
    with pytest.raises(secrets_box.SecretsKeyMissingError) as refused:
        secrets_box.seal("espn_s2 value", keyless)
    assert "FCP_SECRETS_KEY" in str(refused.value)
    assert "espn_s2 value" not in str(refused.value)
    with pytest.raises(secrets_box.SecretsKeyMissingError):
        secrets_box.open_("anything", keyless)


def test_a_bad_key_is_refused_at_startup_without_repeating_it() -> None:
    with pytest.raises(ValidationError) as refused:
        Settings(fcp_secrets_key="not-a-real-key-at-all")
    assert "not-a-real-key-at-all" not in str(refused.value)
    assert "new_secrets_key" in str(refused.value)
    assert Settings(fcp_secrets_key=f"  {KEY} ").fcp_secrets_key == KEY
    assert Settings(fcp_secrets_key="").fcp_secrets_key is None


def test_the_key_script_prints_one_key_and_nothing_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    new_secrets_key.main()
    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    Fernet(printed.strip().encode())  # a usable key
    assert Settings(fcp_secrets_key=printed.strip()).fcp_secrets_key == printed.strip()


# ---------------------------------------------------------------------------
# connecting a league
# ---------------------------------------------------------------------------


def test_connecting_keeps_the_login_sealed_and_makes_an_owner(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = person("alice@example.com")
    # Lower case, no braces: forgiven, and matched to the GUID ESPN stores.
    done = connect(alice, STORED, bare(G_ALICE).lower())
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["espn_league_id"] == STORED and body["league_name"] == "Full Court Test"
    assert body["replaced"] is False and body["identity_kept"] is True
    assert body["ingest_requested_at"] is not None
    # Verified on her team in both seasons, by her SWID.
    assert [(t["season"], t["espn_team_id"]) for t in body["claimed"]] == [
        (SEASON, 3),
        (SEASON - 1, 3),
    ]
    assert body["espn_team"] == {"season": SEASON + 1, "espn_team_id": 3, "name": "Team 3"}
    # One call to ESPN, with the cookie as ESPN wants it: braces, upper case.
    assert espn == [(STORED, G_ALICE, S2, SEASON + 1)]

    me = alice.get("/auth/me").json()
    assert [(lg["espn_league_id"], lg["role"]) for lg in me["leagues"]] == [(STORED, "owner")]
    assert alice.get(standings()).status_code == 200
    assert alice.get(plan(3)).status_code not in (401, 403)

    listed = alice.get("/connections").json()
    assert len(listed) == 1 and listed[0]["id"] == body["id"]
    assert not {"sealed_credentials", "espn_s2", "swid"} & set(listed[0])

    # Stored sealed: neither cookie is in the row as written, and the key opens it.
    row = session.scalars(select(LeagueConnection)).one()
    dumped = " ".join(str(v) for v in vars(row).values())
    assert S2 not in dumped and bare(G_ALICE) not in dumped.upper()
    opened = json.loads(secrets_box.open_(str(row.sealed_credentials), settings_for()))
    assert opened == {"espn_s2": S2, "swid": G_ALICE}
    assert memberships.connection_credentials(row, settings_for()) == (G_ALICE, S2)
    identity = session.scalars(select(UserEspnIdentity)).one()
    assert bare(G_ALICE) not in identity.sealed_swid.upper()
    assert identity.swid_hash == memberships.swid_hash(bare(G_ALICE))


def test_a_refused_login_is_a_plain_422_and_keeps_nothing(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = person("alice@example.com")
    refused = connect(alice, STORED, G_ALICE, s2="AEBnot-the-right-cookie")
    assert refused.status_code == 422
    assert "refused these cookies" in refused.json()["detail"]
    assert "AEBnot-the-right-cookie" not in refused.text
    assert bare(G_ALICE).lower() not in refused.text.lower()
    # Every season was tried before giving up.
    assert [call[3] for call in espn] == [SEASON + 1, SEASON, SEASON - 1]

    missing = connect(alice, 999, G_ALICE)
    assert missing.status_code == 422 and "has no league 999" in missing.json()["detail"]

    not_a_swid = connect(alice, STORED, "hunter2-not-a-guid")
    assert not_a_swid.status_code == 422 and "hunter2" not in not_a_swid.text
    long_s2 = "A" * 5000
    assert connect(alice, STORED, G_ALICE, s2=long_s2).status_code == 422
    assert connect(alice, STORED, G_ALICE, s2="a b").status_code == 422

    assert session.scalar(select(LeagueConnection.id)) is None
    assert session.scalar(text("SELECT count(*) FROM memberships")) == 0
    assert session.scalar(text("SELECT count(*) FROM league_connections")) == 0


def test_espn_not_answering_is_a_502(person: People, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_: Any) -> dict[str, Any]:
        raise requests.ConnectionError(f"could not reach ESPN with {S2}")

    monkeypatch.setattr(leagues_admin, "fetch_league_settings_with", down)
    failed = connect(person("alice@example.com"), STORED, G_ALICE)
    assert failed.status_code == 502 and S2 not in failed.text


def test_without_a_key_connecting_is_refused_before_espn_is_asked(
    app: FastAPI, person: People, espn: list[tuple[int, str, str, int]]
) -> None:
    app.dependency_overrides[get_settings] = lambda: settings_for(fcp_secrets_key=None)
    alice = person("alice@example.com")
    refused = connect(alice, STORED, G_ALICE)
    assert refused.status_code == 503 and "secrets key" in refused.json()["detail"]
    assert espn == []
    assert alice.post("/me/espn-identity", json={"swid": G_ALICE}).status_code == 503


def test_connecting_a_new_league_writes_its_row_and_starts_no_ingest(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    dave = person("dave@example.com")
    done = connect(dave, UNSTORED, G_DAVE)
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["league_name"] == "The New League"
    assert body["claimed"] == [] and body["espn_team"]["name"] == "Dave's Team"
    league = session.scalars(select(League).where(League.espn_league_id == UNSTORED)).one()
    assert league.seasons == [], "the row only: seasons are the ingest's"
    assert session.scalar(text("SELECT count(*) FROM ingest_runs")) == 0
    # A member, with nothing to see yet.
    assert [lg["role"] for lg in dave.get("/auth/me").json()["leagues"]] == ["owner"]

    # Once its teams are stored (step 4's ingest), he is verified on his.
    season = _season(league, SEASON + 1, {1: Owner(espn_owner_id=G_DAVE)})
    session.add(season)
    session.flush()
    connection = session.scalars(select(LeagueConnection)).one()
    claimed = memberships.verify_connection_owner(session, connection, settings_for())
    session.commit()
    assert [(c.season, c.espn_team_id) for c in claimed] == [(SEASON + 1, 1)]
    assert (
        dave.get(f"/leagues/{UNSTORED}/seasons/{SEASON + 1}/teams/1/pickups/stream").status_code
        != 403
    )
    session.execute(text("DELETE FROM owners WHERE espn_owner_id = :g"), {"g": G_DAVE})
    session.commit()


def test_one_active_connection_per_league(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    first = alice.get("/connections").json()[0]["id"]

    bob = person("bob@example.com")
    taken = connect(bob, STORED, G_BOB)
    assert taken.status_code == 409 and "Ask its owner" in taken.json()["detail"]
    assert not accounts.is_member(session, _uid(session, "bob@example.com"), STORED)

    # Alice again, with new cookies: replaced in place.
    again = connect(alice, STORED, G_ALICE, s2=OTHER_S2)
    assert again.status_code == 201 and again.json()["replaced"] is True
    assert again.json()["id"] == first
    row = session.scalars(select(LeagueConnection)).one()
    session.refresh(row)
    assert memberships.connection_credentials(row, settings_for())[1] == OTHER_S2


def test_revoking_wipes_the_login_and_frees_the_league(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    connection_id = alice.get("/connections").json()[0]["id"]
    bob = person("bob@example.com")
    assert bob.delete(f"/connections/{connection_id}").status_code == 404, "not his"

    revoked = alice.delete(f"/connections/{connection_id}")
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    row = session.get(LeagueConnection, connection_id)
    assert row is not None
    session.refresh(row)
    assert row.sealed_credentials is None
    with pytest.raises(ValueError):
        memberships.connection_credentials(row, settings_for())
    # Still the league's owner: he brought it in.
    assert accounts.is_league_owner(session, _uid(session, "alice@example.com"), STORED)

    assert connect(bob, STORED, G_BOB).status_code == 201, "the league is free again"


def test_connecting_is_rate_limited_per_user(
    person: People, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = person("alice@example.com")
    answers = [connect(alice, STORED, G_ALICE, s2="AEBwrong").status_code for _ in range(7)]
    assert answers[:5] == [422] * 5 and answers[5:] == [429, 429]


def _uid(session: Session, email: str) -> int:
    user = accounts.user_by_email(session, email)
    assert user is not None
    return user.id


# ---------------------------------------------------------------------------
# invites
# ---------------------------------------------------------------------------


def test_an_invite_makes_a_member(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    made = alice.post(f"/leagues/{STORED}/invites", json={})
    assert made.status_code == 201
    link = made.json()
    assert link["url"] == f"https://fcp.example.test{link['path']}"
    assert link["path"].startswith("/join/") and link["expires_at"] is None
    token = link["path"].rsplit("/", 1)[1]
    # Only the hash is kept.
    stored = session.scalars(select(Invite)).one()
    assert stored.token_hash == accounts.hash_token(token) and token not in stored.token_hash

    erin = person("erin@example.com")
    assert erin.get(standings()).status_code == 403, "not a member yet"
    assert "Join" in erin.get(f"/join/{token}").text
    assert "Connect a league" in erin.get("/account/connections").text
    shown = erin.get(f"/invites/{token}")
    assert shown.status_code == 200
    assert shown.json() == {
        "espn_league_id": STORED,
        "league_name": "Full Court Test",
        "member": False,
    }
    joined = erin.post(f"/invites/{token}/accept")
    assert joined.json() == {
        "espn_league_id": STORED,
        "league_name": "Full Court Test",
        "joined": True,
        "latest_season": SEASON,
    }
    assert erin.get(standings()).status_code == 200
    assert erin.get(f"/pages/claim/{STORED}/{SEASON}").status_code == 200
    assert erin.post(f"/invites/{token}/accept").json()["joined"] is False
    assert [i["uses"] for i in alice.get(f"/leagues/{STORED}/invites").json()] == [1]
    assert [lg["role"] for lg in erin.get("/auth/me").json()["leagues"]] == ["member"]

    # A member is not an owner: no invites of his own, no claims to decide.
    assert erin.post(f"/leagues/{STORED}/invites", json={}).status_code == 403
    assert erin.get(f"/leagues/{STORED}/invites").status_code == 403
    assert erin.get(f"/leagues/{STORED}/claims").status_code == 403


def test_an_invite_can_be_revoked(person: People, espn: list[tuple[int, str, str, int]]) -> None:
    alice = owner_of_stored(person)
    token = invite_into(alice)
    invite_id = alice.get(f"/leagues/{STORED}/invites").json()[0]["id"]
    erin = person("erin@example.com")
    assert erin.delete(f"/leagues/{STORED}/invites/{invite_id}").status_code == 403

    assert alice.delete(f"/leagues/{STORED}/invites/{invite_id}").status_code == 200
    assert erin.get(f"/invites/{token}").status_code == 404
    assert erin.post(f"/invites/{token}/accept").status_code == 404
    assert erin.get(standings()).status_code == 403
    assert alice.delete(f"/leagues/{STORED}/invites/99999").status_code == 404


def test_an_expired_invite_is_refused(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    lasting = alice.post(f"/leagues/{STORED}/invites", json={"expires_in_days": 7}).json()
    assert lasting["expires_at"] is not None
    assert alice.post(f"/leagues/{STORED}/invites", json={"expires_in_days": 0}).status_code == 422
    token = lasting["path"].rsplit("/", 1)[1]
    session.execute(update(Invite).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    session.commit()
    erin = person("erin@example.com")
    assert erin.get(f"/invites/{token}").status_code == 404
    assert erin.post(f"/invites/{token}/accept").status_code == 404
    assert erin.get("/invites/not-a-token").status_code == 404


def test_an_invite_needs_a_signed_in_viewer(
    app: FastAPI, person: People, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    token = invite_into(alice)
    with Recording(app) as anon:
        assert anon.get(f"/invites/{token}").status_code == 401
        assert anon.post(f"/invites/{token}/accept").status_code == 401
        sent = anon.get(f"/join/{token}", follow_redirects=False)
        assert sent.status_code == 303
        assert sent.headers["location"] == f"/sign-in?next=/join/{token}"


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------


def test_a_claim_is_verified_by_a_matching_swid(
    person: People,
    session: Session,
    espn: list[tuple[int, str, str, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memberships, "TRUST_BARE_SWID", True)
    alice = owner_of_stored(person)
    bob = member(person, "bob@example.com", alice)
    # Braces and case forgiven: ESPN stores {B0B...}, he pastes it bare, lower-case.
    kept = bob.post("/me/espn-identity", json={"swid": bare(G_BOB).lower()})
    assert kept.status_code == 200 and kept.json() == {"verified": []}
    claimed = bob.post(claim_path(5))
    assert claimed.json() == {
        "season": SEASON,
        "espn_team_id": 5,
        "name": "Team 5",
        "state": "verified",
    }
    how = session.scalar(
        select(TeamManager.how).where(TeamManager.user_id == _uid(session, "bob@example.com"))
    )
    assert how == "owner_guid"
    assert bob.get(plan(5)).status_code not in (401, 403)
    assert bob.get(plan(3)).status_code == 403, "Alice's team is still hers"

    listed = {
        t["espn_team_id"]: t
        for t in bob.get(f"/leagues/{STORED}/seasons/{SEASON}/teams/claimable").json()
    }
    assert listed[5] == {"espn_team_id": 5, "name": "Team 5", "claimed": True, "mine": "verified"}
    assert listed[3]["claimed"] is True and listed[3]["mine"] is None
    assert listed[7] == {"espn_team_id": 7, "name": "Team 7", "claimed": False, "mine": None}


def test_a_swid_added_later_verifies_a_pending_claim(
    person: People,
    espn: list[tuple[int, str, str, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memberships, "TRUST_BARE_SWID", True)
    alice = owner_of_stored(person)
    carol = member(person, "carol@example.com", alice)
    assert carol.post(claim_path(7)).json()["state"] == "pending"
    assert carol.get(plan(7)).status_code == 403
    kept = carol.post("/me/espn-identity", json={"swid": G_CAROL.lower()})
    assert kept.json() == {"verified": [{"season": SEASON, "espn_team_id": 7, "name": "Team 7"}]}
    assert carol.get(plan(7)).status_code not in (401, 403)


def test_a_claim_without_a_match_waits_for_the_owner(
    person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    erin = member(person, "erin@example.com", alice)
    frank = member(person, "frank@example.com", alice)
    assert erin.post(claim_path(7)).json()["state"] == "pending"
    assert frank.post(claim_path(5)).json()["state"] == "pending"
    assert erin.post(f"/leagues/{STORED}/seasons/{SEASON}/teams/99/claim").status_code == 404

    # A member with no verified claim is refused that team's plan and its pages.
    assert erin.get(plan(7)).status_code == 403
    assert erin.get(f"/l/{STORED}/{SEASON}/team/7/week").status_code == 403
    # ...and cannot decide claims, his own or anyone's.
    pending = alice.get(f"/leagues/{STORED}/claims").json()
    assert [(c["email"], c["espn_team_id"], c["state"]) for c in pending] == [
        ("erin@example.com", 7, "pending"),
        ("frank@example.com", 5, "pending"),
    ]
    erins, franks = pending[0]["id"], pending[1]["id"]
    assert erin.post(f"/leagues/{STORED}/claims/{erins}/approve").status_code == 403
    assert frank.post(f"/leagues/{STORED}/claims/{erins}/reject").status_code == 403

    approved = alice.post(f"/leagues/{STORED}/claims/{erins}/approve")
    assert approved.status_code == 200
    assert approved.json()["state"] == "verified" and approved.json()["how"] == "approved"
    assert erin.get(plan(7)).status_code not in (401, 403)
    decided = session.scalars(select(TeamManager).where(TeamManager.id == erins)).one()
    assert decided.decided_by == _uid(session, "alice@example.com")
    assert decided.decided_at is not None and decided.verified_at is not None

    rejected = alice.post(f"/leagues/{STORED}/claims/{franks}/reject")
    assert rejected.json()["state"] == "rejected"
    assert frank.get(plan(5)).status_code == 403
    assert alice.get(f"/leagues/{STORED}/claims").json() == []

    # A claim in another league is not this league's to decide.
    assert alice.post(f"/leagues/{STORED}/claims/99999/approve").status_code == 404

    # Rejecting a verified manager takes the plan away again.
    assert alice.post(f"/leagues/{STORED}/claims/{erins}/reject").status_code == 200
    assert erin.get(plan(7)).status_code == 403
    # And a rejected claim can be made again, back to pending.
    assert erin.post(claim_path(7)).json()["state"] == "pending"


def test_with_bare_swids_untrusted_every_claim_waits(
    person: People, monkeypatch: pytest.MonkeyPatch, espn: list[tuple[int, str, str, int]]
) -> None:
    monkeypatch.setattr(memberships, "TRUST_BARE_SWID", False)
    alice = owner_of_stored(person)
    bob = member(person, "bob@example.com", alice)
    assert bob.post("/me/espn-identity", json={"swid": G_BOB}).json() == {"verified": []}
    assert bob.post(claim_path(5)).json()["state"] == "pending"
    assert bob.get(plan(5)).status_code == 403
    assert bob.post("/me/espn-identity", json={"swid": G_BOB}).json() == {"verified": []}
    # The connector's own verification stands: ESPN accepted his espn_s2.
    assert alice.get(plan(3)).status_code not in (401, 403)


def test_one_account_per_espn_identity(
    person: People, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    bob = member(person, "bob@example.com", alice)
    assert bob.post("/me/espn-identity", json={"swid": G_BOB}).status_code == 200
    frank = member(person, "frank@example.com", alice)
    taken = frank.post("/me/espn-identity", json={"swid": G_BOB.lower()})
    assert taken.status_code == 409
    assert frank.post("/me/espn-identity", json={"swid": "nonsense"}).status_code == 422
    # Forgetting it frees it; claims it verified would stay verified.
    assert bob.delete("/me/espn-identity").json() == {"forgotten": True}
    assert frank.post("/me/espn-identity", json={"swid": G_BOB}).status_code == 200


def test_a_verified_claim_alone_is_not_membership(person: People, session: Session) -> None:
    zed = person("zed@example.com")
    team = session.scalars(
        select(Team.id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .where(LeagueSeason.season == SEASON, Team.espn_team_id == 7)
    ).one()
    session.add(
        TeamManager(
            user_id=_uid(session, "zed@example.com"),
            team_id=team,
            state="verified",
            how="approved",
            verified_at=datetime.now(UTC),
        )
    )
    session.commit()
    assert zed.get(standings()).status_code == 403, "the league's pages are its members'"
    assert zed.get(plan(7)).status_code not in (401, 403), "his team's plan is his"
    assert zed.get("/leagues").json() == []


def test_a_stranger_cannot_list_or_claim(person: People) -> None:
    stranger = person("stranger@example.com")
    base = f"/leagues/{STORED}/seasons/{SEASON}/teams"
    assert stranger.get(f"{base}/claimable").status_code == 403
    assert stranger.post(f"{base}/3/claim").status_code == 403
    assert stranger.get(f"/pages/claim/{STORED}/{SEASON}").status_code == 403


# ---------------------------------------------------------------------------
# single mode
# ---------------------------------------------------------------------------


def test_single_mode_owns_the_tracked_league_without_a_connection(
    app: FastAPI, session: Session
) -> None:
    app.dependency_overrides[get_settings] = lambda: settings_for(
        fcp_auth_mode="single", espn_league_id=STORED, fcp_tracked_team_id=3
    )
    with Recording(app) as client:
        me = client.get("/auth/me").json()
        assert [(lg["espn_league_id"], lg["role"]) for lg in me["leagues"]] == [(STORED, "owner")]
        assert client.get("/connections").json() == []
        assert client.get(standings()).status_code == 200
        assert client.get(plan(5)).status_code not in (401, 403), "single mode refuses nothing"
        assert client.get(f"/leagues/{STORED}/claims").status_code == 200
    owner = _uid(session, OWNER)
    assert accounts.is_league_owner(session, owner, STORED)
    assert session.scalar(text("SELECT count(*) FROM league_connections")) == 0


def test_an_owner_who_joined_as_a_member_is_made_the_owner(
    app: FastAPI, person: People, session: Session, espn: list[tuple[int, str, str, int]]
) -> None:
    alice = owner_of_stored(person)
    owner = member(person, OWNER, alice)
    assert [lg["role"] for lg in owner.get("/auth/me").json()["leagues"]] == ["member"]
    accounts.ensure_owner(session, OWNER, STORED, 3)
    assert accounts.role_in_league(session, _uid(session, OWNER), STORED) == "owner"


# ---------------------------------------------------------------------------
# the access log
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("/invites/abc-DEF_123", "/invites/[redacted]"),
        ("/invites/abc-DEF_123/accept", "/invites/[redacted]/accept"),
        ("/join/abc-DEF_123", "/join/[redacted]"),
        ("/sign-in?next=/join/abc-DEF_123", "/sign-in?next=/join/[redacted]"),
        ("/sign-in?next=%2Fjoin%2Fabc-DEF_123", "/sign-in?next=%2Fjoin%2F[redacted]"),
        (f"/leagues/{STORED}/invites/12", f"/leagues/{STORED}/invites/[redacted]"),
    ],
)
def test_the_access_log_never_carries_an_invite(path: str, kept: str) -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", path, "1.1", 200),
        None,
    )
    assert _RedactTokens().filter(record)
    line = record.getMessage()
    assert "abc-DEF_123" not in line
    assert kept in line
