"""The season pass over HTTP: redeeming, the owner's codes, and the gate.

In accounts mode, over tests/test_access.py's league (its helpers build it):
league A with teams 3 and 5 (alice manages 3, bob 5), league B with team 3
(carol), and the site's owner on league A's team 3. Checked here:

* the owner makes, lists and revokes codes; a member and a stranger hear 403,
  and signed out is 401;
* a member redeems a code and his team's plan opens with billing on; a
  second try, a wrong code and a spent one hear one sentence each;
* redeeming is rate-limited per account;
* a pass that lapses puts the viewer back on 402 with billing on;
* `/billing/pass` says what the upgrade page draws, and that purchase is
  closed.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, billing
from app.api.deps import get_session
from app.config import get_settings
from app.db.models import League
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from tests.test_access import (
    LEAGUE_A,
    LEAGUE_B,
    OWNER,
    SEASON,
    SERVICE_TOKEN,
    SignIn,
    _claim,
    _season,
    accounts_settings,
    request_link,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PLAN = f"/leagues/{LEAGUE_A}/seasons/{SEASON}/teams/3/pickups/stream"
BOB_PLAN = f"/leagues/{LEAGUE_A}/seasons/{SEASON}/teams/5/pickups/stream"


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
    for name in ("fcp", "fcp.auth", "fcp.access", "fcp.billing"):
        logging.getLogger(name).disabled = False

    engine = make_engine(test_database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        a = League(espn_league_id=LEAGUE_A)
        b = League(espn_league_id=LEAGUE_B)
        session.add_all([a, b])
        session.add(_season(a, SEASON, [3, 5]))
        session.add(_season(b, SEASON, [3]))
        session.flush()
        _claim(session, "alice@example.com", LEAGUE_A, SEASON, 3)
        _claim(session, "bob@example.com", LEAGUE_A, SEASON, 5)
        _claim(session, "carol@example.com", LEAGUE_B, SEASON, 3)
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def session(seeded: sessionmaker[Session]) -> Iterator[Session]:
    with seeded() as open_session:
        yield open_session


@pytest.fixture(autouse=True)
def no_passes_or_codes(seeded: sessionmaker[Session]) -> Iterator[None]:
    """Each test starts with no code and no pass but the owner's."""
    yield
    with seeded() as clean:
        clean.execute(text("DELETE FROM comp_code_redemptions"))
        clean.execute(text("DELETE FROM comp_codes"))
        clean.execute(text("DELETE FROM entitlements WHERE source <> 'owner'"))
        clean.commit()


@pytest.fixture
def app(seeded: sessionmaker[Session]) -> Iterator[FastAPI]:
    built = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: accounts_settings()
    yield built
    built.dependency_overrides.clear()


def billing_on(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: accounts_settings(fcp_billing_enabled=True)


@pytest.fixture
def anon(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sign_in(app: FastAPI, caplog: pytest.LogCaptureFixture) -> Iterator[SignIn]:
    opened: list[TestClient] = []

    def make(email: str) -> TestClient:
        client = TestClient(app)
        opened.append(client)
        landed = client.get(request_link(client, caplog, email), follow_redirects=False)
        assert landed.status_code == 303, landed.text
        return client

    yield make
    for client in opened:
        client.close()


def made(owner: TestClient, **ask: object) -> dict[str, object]:
    answer = owner.post("/billing/codes", json={"note": "for a test", **ask})
    assert answer.status_code == 200, answer.text
    body: dict[str, object] = answer.json()
    return body


# ---------------------------------------------------------------------------
# the owner's codes
# ---------------------------------------------------------------------------


def test_the_owner_makes_lists_and_revokes_codes(sign_in: SignIn) -> None:
    owner = sign_in(OWNER)
    first = made(owner, uses=3, valid_until="2027-06-30", redeem_by="2027-01-31")
    assert billing.normalise_code(str(first["code"])) == first["code"]
    assert first["uses_total"] == 3 and first["uses_left"] == 3 and first["state"] == "open"
    assert str(first["valid_until"]).startswith("2027-06-30T23:59:59")
    assert first["valid_until_words"] == "Jun 30, 2027"
    assert str(first["redeem_by"]).startswith("2027-01-31")
    assert first["default_valid_until"] is False
    second = made(owner)
    assert second["default_valid_until"] is True and second["uses_total"] == 1

    listed = owner.get("/billing/codes").json()
    assert [c["id"] for c in listed] == [second["id"], first["id"]]
    assert listed[1]["note"] == "for a test"

    assert owner.post(f"/billing/codes/{first['id']}/revoke").json() == {"revoked": True}
    assert owner.post(f"/billing/codes/{first['id']}/revoke").json() == {"revoked": False}
    assert owner.get("/billing/codes").json()[1]["state"] == "revoked"


def test_a_code_the_owner_cannot_mean_is_one_sentence(sign_in: SignIn) -> None:
    owner = sign_in(OWNER)
    for ask in ({"uses": 0}, {"valid_until": "2020-01-01"}, {"redeem_by": "2020-01-01"}):
        refused = owner.post("/billing/codes", json=ask)
        assert refused.status_code == 422, ask
        assert isinstance(refused.json()["detail"], str)


def test_the_codes_are_the_site_owners_alone(sign_in: SignIn, anon: TestClient) -> None:
    owner = sign_in(OWNER)
    code = made(owner)
    # alice is a member of league A; bob too; carol of another league. The
    # owner's machine token is him, and the service token is him too.
    for who in ("alice@example.com", "carol@example.com"):
        member = sign_in(who)
        assert member.get("/billing/codes").status_code == 403, who
        assert member.post("/billing/codes", json={}).status_code == 403, who
        assert member.post(f"/billing/codes/{code['id']}/revoke").status_code == 403, who
    assert anon.get("/billing/codes").status_code == 401
    assert anon.post("/billing/codes", json={}).status_code == 401
    bearer = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    assert anon.get("/billing/codes", headers=bearer).status_code == 200


# ---------------------------------------------------------------------------
# redeeming, and the gate
# ---------------------------------------------------------------------------


def test_a_member_redeems_a_code_and_the_plan_opens(app: FastAPI, sign_in: SignIn) -> None:
    code = made(sign_in(OWNER), valid_until="2027-06-30")
    alice = sign_in("alice@example.com")
    billing_on(app)
    assert alice.get(PLAN).status_code == 402
    state = alice.get("/billing/pass").json()
    assert state["billing_enabled"] is True and state["entitled"] is False
    assert state["live"] is None and state["lapsed"] is None
    assert state["purchase_available"] is False
    assert state["purchase_note"] == billing.PURCHASE_CLOSED
    assert state["site_owner"] is False

    typed = str(code["code"]).lower().replace("-", " ")
    done = alice.post("/billing/redeem", json={"code": typed})
    assert done.status_code == 200, done.text
    assert done.json()["detail"] == "Your pass runs until Jun 30, 2027."
    assert done.json()["live"]["source_words"] == "a code"
    assert alice.get(PLAN).status_code == 200
    state = alice.get("/billing/pass").json()
    assert state["entitled"] is True and state["live"]["until_words"] == "Jun 30, 2027"

    again = alice.post("/billing/redeem", json={"code": code["code"]})
    assert again.status_code == 422
    assert again.json()["detail"].startswith("You already have a pass until Jun 30, 2027.")

    bob = sign_in("bob@example.com")
    spent = bob.post("/billing/redeem", json={"code": code["code"]})
    assert spent.status_code == 422 and spent.json()["detail"] == billing.NO_GOOD
    wrong = bob.post("/billing/redeem", json={"code": "ZZZZ-ZZZZ-ZZZZ"})
    assert wrong.json()["detail"] == billing.NO_GOOD
    assert bob.get(BOB_PLAN).status_code == 402


def test_redeeming_works_the_same_with_billing_off(sign_in: SignIn) -> None:
    code = made(sign_in(OWNER))
    bob = sign_in("bob@example.com")
    assert bob.get("/billing/pass").json()["entitled"] is True, "billing off: everyone"
    assert bob.post("/billing/redeem", json={"code": code["code"]}).status_code == 200
    assert bob.get("/billing/pass").json()["live"]["source"] == "comp"


def test_a_lapsed_pass_is_the_free_tier_again(
    app: FastAPI, sign_in: SignIn, session: Session
) -> None:
    alice = sign_in("alice@example.com")
    user = accounts.user_by_email(session, "alice@example.com")
    assert user is not None
    billing.grant(session, user.id, "comp", datetime.now(UTC) + timedelta(days=1))
    session.commit()
    billing_on(app)
    assert alice.get(PLAN).status_code == 200

    session.execute(
        text("UPDATE entitlements SET valid_until = :at WHERE user_id = :u"),
        {"at": datetime(2026, 9, 1, tzinfo=UTC), "u": user.id},
    )
    session.commit()
    assert alice.get(PLAN).status_code == 402
    state = alice.get("/billing/pass").json()
    assert state["live"] is None and state["lapsed"]["until_words"] == "Sep 1, 2026"


def test_the_owner_is_entitled_always(app: FastAPI, sign_in: SignIn) -> None:
    billing_on(app)
    owner = sign_in(OWNER)
    assert owner.get(PLAN).status_code == 200
    state = owner.get("/billing/pass").json()
    assert state["live"]["source_words"] == "your own" and state["live"]["until"] is None
    assert state["site_owner"] is True


def test_redeeming_is_rate_limited_per_account(sign_in: SignIn) -> None:
    carol = sign_in("carol@example.com")
    answers = [
        carol.post("/billing/redeem", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code for _ in range(7)
    ]
    assert answers[:5] == [422] * 5 and answers[5:] == [429, 429]


def test_every_attempt_is_logged_without_the_code(
    sign_in: SignIn, caplog: pytest.LogCaptureFixture
) -> None:
    code = made(sign_in(OWNER))
    bob = sign_in("bob@example.com")
    caplog.set_level(logging.INFO, logger="fcp.billing")
    bob.post("/billing/redeem", json={"code": "ZZZZ-ZZZZ-ZZZZ"})
    bob.post("/billing/redeem", json={"code": code["code"]})
    lines = [r.getMessage() for r in caplog.records if r.name == "fcp.billing"]
    assert any("refused: unknown" in line for line in lines)
    assert any("redeemed by user" in line for line in lines)
    assert not any(str(code["code"]) in line or "ZZZZ" in line for line in lines)


def test_a_date_is_the_last_day_of_the_pass() -> None:
    assert billing.end_of_day(date(2027, 6, 30)) == datetime(2027, 6, 30, 23, 59, 59, tzinfo=UTC)
