"""Accounts mode: signing in, the scope checks, and every route having one.

The other API tests run in single mode, where every request is the owner
and nothing is refused, which is the tailnet API as it has always been.
These switch one app to `FCP_AUTH_MODE=accounts` by overriding the settings
dependency, and pin what docs/accounts.md promises: a signed-out caller is
refused (401 from a route, a redirect from a page), a member of one league
cannot read another, a manager of one team cannot read another team's plan,
the entitlement check waits on `BILLING_ENABLED`, a link works once and only
for fifteen minutes, and signing out ends the session.

The leagues are built as rows rather than ingested, because only their
shape matters here: league A (two seasons, teams 3 and 5) and league B.
"""

import logging
import re
import smtplib
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, memberships
from app.api import access
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.db.models import (
    Entitlement,
    League,
    LeagueSeason,
    SignInToken,
    Team,
    TeamManager,
)
from app.db.session import make_engine, make_session_factory
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent

LEAGUE_A = 111
LEAGUE_B = 222
SEASON = 2026
SERVICE_TOKEN = "a-service-token-long-enough-to-mean-something-0123456789"
OWNER = "owner@example.com"

PER_GAME = """Player,Team,Pos,GP,PTS,REB,AST,STL,BLK,3PM,TO,FGM,FGA,FTM,FTA
Evan Mobley,CLE,PF,70,18.5,9.3,3.2,0.9,1.6,1.1,2.1,7.2,13.4,3.0,4.1
"""


def _season(league: League, season: int, team_ids: list[int]) -> LeagueSeason:
    row = LeagueSeason(
        league=league,
        season=season,
        name=f"League {league.espn_league_id}",
        scoring_type="H2H_CATEGORY",
        team_count=len(team_ids),
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
    for tid in team_ids:
        row.teams.append(Team(espn_team_id=tid, name=f"Team {tid}"))
    return row


def _claim(session: Session, email: str, league: int, season: int, team: int) -> None:
    """A member of the league with a verified claim, as an accepted invite and
    an approved claim write them (tests/test_leagues_admin.py drives those)."""
    user = accounts.get_or_create_user(session, email)
    league_row = session.scalars(select(League).where(League.espn_league_id == league)).one()
    memberships.join_league(session, user.id, league_row.id, accounts.MEMBER_ROLE)
    team_row = session.scalars(
        select(Team.id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .where(
            League.espn_league_id == league,
            LeagueSeason.season == season,
            Team.espn_team_id == team,
        )
    ).one()
    session.add(
        TeamManager(
            user_id=user.id,
            team_id=team_row,
            state="verified",
            how="approved",
            verified_at=datetime.now(UTC),
        )
    )


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
    # Alembic's env.py runs `logging.config.fileConfig`, which disables every
    # logger that already exists; the dev link is read from `fcp.auth`.
    for name in ("fcp", "fcp.auth", "fcp.access"):
        logging.getLogger(name).disabled = False

    engine = make_engine(test_database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        a = League(espn_league_id=LEAGUE_A)
        b = League(espn_league_id=LEAGUE_B)
        session.add_all([a, b])
        session.add(_season(a, SEASON - 1, [3, 5]))
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


def accounts_settings(**changes: Any) -> Settings:
    """Accounts mode, no SMTP (so links are logged), the owner on league A's team 3."""
    base = {
        "fcp_auth_mode": "accounts",
        "fcp_owner_email": OWNER,
        "fcp_service_token": SERVICE_TOKEN,
        "fcp_public_url": None,
        "fcp_smtp_host": None,
        "fcp_email_from": None,
        "espn_league_id": LEAGUE_A,
        "fcp_tracked_team_id": 3,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


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


@pytest.fixture
def anon(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


SignIn = Callable[[str], TestClient]


@pytest.fixture
def sign_in(app: FastAPI, caplog: pytest.LogCaptureFixture) -> Iterator[SignIn]:
    """A signed-in client per email, through the real link and callback."""
    opened: list[TestClient] = []

    def make(email: str) -> TestClient:
        client = TestClient(app)
        opened.append(client)
        link = request_link(client, caplog, email)
        landed = client.get(link, follow_redirects=False)
        assert landed.status_code == 303, landed.text
        assert access.COOKIE in client.cookies
        return client

    yield make
    for client in opened:
        client.close()


def request_link(
    client: TestClient, caplog: pytest.LogCaptureFixture, email: str, next_path: str | None = None
) -> str:
    """Ask for a link and read it from the dev log, as a developer would."""
    caplog.clear()
    caplog.set_level(logging.INFO, logger="fcp.auth")
    body: dict[str, str] = {"email": email}
    if next_path is not None:
        body["next"] = next_path
    asked = client.post("/auth/sign-in", json=body)
    assert asked.status_code == 202, asked.text
    logged = [r.getMessage() for r in caplog.records if r.name == "fcp.auth"]
    assert logged, "the dev link was not logged"
    link = re.search(r"(http\S+/auth/callback\?token=\S+)", logged[-1])
    assert link is not None
    token = link.group(1).split("token=")[1]
    # Never in the response: the link has to come from the mailbox.
    assert token not in asked.text
    return link.group(1).replace("http://testserver", "")


def standings(league: int) -> str:
    return f"/leagues/{league}/seasons/{SEASON}/standings"


def pickups(team: int, league: int = LEAGUE_A) -> str:
    return f"/leagues/{league}/seasons/{SEASON}/teams/{team}/pickups/stream"


def today(team: int, league: int = LEAGUE_A) -> str:
    return f"/leagues/{league}/seasons/{SEASON}/teams/{team}/today"


def trades(team: int, which: str = "rosters", league: int = LEAGUE_A) -> str:
    return f"/leagues/{league}/seasons/{SEASON}/teams/{team}/trades/{which}"


def what_if(team: int, league: int = LEAGUE_A) -> str:
    return f"/leagues/{league}/seasons/{SEASON}/teams/{team}/what-if"


def week_page(team: int) -> str:
    return f"/l/{LEAGUE_A}/{SEASON}/team/{team}/week"


#: A pickups route that passed its checks and then found nothing to report
#: on: these leagues have no draft, no schedule and no lineups, so the route
#: answers 200 with `readiness` and no number -- and a 200 is the sign the
#: request got through the door (a refused one is a 401, 402 or 403).
THROUGH = 200


# ---------------------------------------------------------------------------
# signed out
# ---------------------------------------------------------------------------


def test_signed_out_is_401_on_a_league_route(anon: TestClient) -> None:
    refused = anon.get(standings(LEAGUE_A))
    assert refused.status_code == 401
    assert refused.headers["www-authenticate"] == "Bearer"
    assert anon.get(pickups(3)).status_code == 401
    assert anon.get(today(3)).status_code == 401
    assert anon.get(trades(3)).status_code == 401
    assert anon.get(trades(3, "report")).status_code == 401
    assert anon.get(what_if(3)).status_code == 401
    assert anon.get("/leagues").status_code == 401
    assert anon.get("/projections/sets").status_code == 401
    assert anon.get("/ingest-runs/health").status_code == 401


def test_signed_out_is_sent_to_sign_in_from_a_page(anon: TestClient) -> None:
    for path in (week_page(3), f"/l/{LEAGUE_A}/{SEASON}/standings", "/account/alerts"):
        sent = anon.get(path + "?today=5", follow_redirects=False)
        assert sent.status_code == 303
        assert sent.headers["location"].startswith("/sign-in?next=")
        assert "%3Ftoday%3D5" in sent.headers["location"]


def test_the_open_routes_stay_open(anon: TestClient) -> None:
    assert anon.get("/health").json() == {"status": "ok"}
    assert anon.get("/sign-in").status_code == 200
    assert anon.get("/pages/static/pages.css").status_code == 200
    assert anon.get("/pages/static/pages.js").status_code == 200
    assert anon.get("/pages/static/shell.js").status_code == 200
    assert anon.get("/").status_code == 200, "the landing page"


# ---------------------------------------------------------------------------
# the scope checks
# ---------------------------------------------------------------------------


def test_a_member_of_league_a_is_refused_league_b(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    assert alice.get(standings(LEAGUE_A)).status_code == 200
    assert alice.get(f"/leagues/{LEAGUE_A}/owners").status_code == 200
    assert alice.get(standings(LEAGUE_B)).status_code == 403
    assert alice.get(f"/leagues/{LEAGUE_B}/owners").status_code == 403
    assert alice.get(f"/l/{LEAGUE_B}/{SEASON}/week").status_code == 403
    # Any season of a league he is in: his claim is on 2026, 2025 opens too.
    assert alice.get(f"/leagues/{LEAGUE_A}/seasons/{SEASON - 1}").status_code == 200
    # The list is his leagues only.
    assert [row["espn_league_id"] for row in alice.get("/leagues").json()] == [LEAGUE_A]


def test_a_manager_of_team_5_is_refused_team_3s_plan(sign_in: SignIn) -> None:
    bob = sign_in("bob@example.com")
    refused = bob.get(pickups(3))
    assert refused.status_code == 403
    assert refused.json()["detail"] == "This team's plan is its manager's."
    assert bob.get(pickups(5)).status_code == THROUGH
    # The day's lineup is the same paid team layer as the week's plan.
    shut_today = bob.get(today(3))
    assert shut_today.status_code == 403
    assert shut_today.json()["detail"] == "This team's plan is its manager's."
    assert bob.get(today(5)).status_code == THROUGH
    # The trade routes are the same paid team layer, both of them.
    for which in ("rosters", "report"):
        shut = bob.get(trades(3, which))
        assert shut.status_code == 403
        assert shut.json()["detail"] == "This team's plan is its manager's."
        # His own team: through the door, and told the season has nothing to
        # judge from rather than refused, which is what these leagues hold.
        his = bob.get(trades(5, which))
        assert his.status_code == 200
        assert his.json()["readiness"]["ready"] is False
    # A hypothetical about a roster is that roster's plan, on the same layer.
    shut_what_if = bob.get(what_if(3))
    assert shut_what_if.status_code == 403
    assert shut_what_if.json()["detail"] == "This team's plan is its manager's."
    assert bob.get(what_if(5)).status_code == THROUGH
    # The league's pages, every team's scorecard among them, are shared.
    assert bob.get(f"/leagues/{LEAGUE_A}/seasons/{SEASON}/teams/3/scorecard").status_code != 403
    page = bob.get(week_page(3))
    assert page.status_code == 403
    assert "This team&#x27;s plan is its manager&#x27;s." in page.text
    assert bob.get(week_page(5)).status_code == 200


def test_an_unknown_team_is_refused_not_revealed(sign_in: SignIn) -> None:
    bob = sign_in("bob@example.com")
    assert bob.get(pickups(99)).status_code == 403
    assert bob.get(f"/leagues/{LEAGUE_B + 1}/seasons/{SEASON}/standings").status_code == 403


def test_the_entitlement_waits_on_billing(
    sign_in: SignIn, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = sign_in("alice@example.com")
    user = accounts.user_by_email(session, "alice@example.com")
    assert user is not None
    assert accounts.active_entitlement(session, user.id) is None

    assert alice.get(pickups(3)).status_code == THROUGH, "no plan, but billing is off"

    monkeypatch.setattr(access, "BILLING_ENABLED", True)
    refused = alice.get(pickups(3))
    assert refused.status_code == 402
    assert alice.get(trades(3)).status_code == 402
    assert alice.get(what_if(3)).status_code == 402
    assert alice.get(week_page(3)).status_code == 402
    # The free league pages do not ask.
    assert alice.get(standings(LEAGUE_A)).status_code == 200

    lapsed = Entitlement(
        user_id=user.id,
        tier="team",
        source="trial",
        valid_until=datetime.now(UTC) - timedelta(days=1),
    )
    session.add(lapsed)
    session.commit()
    assert alice.get(pickups(3)).status_code == 402, "a lapsed trial is no plan"

    session.add(Entitlement(user_id=user.id, tier="team", source="comp"))
    session.commit()
    assert alice.get(pickups(3)).status_code == THROUGH
    session.execute(text("DELETE FROM entitlements WHERE user_id = :u"), {"u": user.id})
    session.commit()


def test_a_stranger_hears_about_the_team_before_the_plan(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "BILLING_ENABLED", True)
    bob = sign_in("bob@example.com")
    assert bob.get(pickups(3)).status_code == 403


# ---------------------------------------------------------------------------
# the links
# ---------------------------------------------------------------------------


def test_a_used_link_is_refused(anon: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    link = request_link(anon, caplog, "alice@example.com")
    assert anon.get(link, follow_redirects=False).status_code == 303
    anon.cookies.clear()
    again = anon.get(link, follow_redirects=False)
    assert again.status_code == 400
    assert access.COOKIE not in again.cookies


def test_an_expired_link_is_refused(
    anon: TestClient, caplog: pytest.LogCaptureFixture, session: Session
) -> None:
    link = request_link(anon, caplog, "alice@example.com")
    token = link.split("token=")[1]
    session.execute(
        update(SignInToken)
        .where(SignInToken.token_hash == accounts.hash_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    session.commit()
    assert anon.get(link, follow_redirects=False).status_code == 400
    assert anon.get("/auth/callback?token=nonsense", follow_redirects=False).status_code == 400
    assert anon.get("/auth/callback", follow_redirects=False).status_code == 400


def test_only_hashes_are_stored(anon: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    link = request_link(anon, caplog, "dave@example.com")
    token = link.split("token=")[1]
    anon.get(link, follow_redirects=False)
    cookie = anon.cookies[access.COOKIE]
    engine = make_engine(str(get_settings().test_database_url))
    with engine.connect() as connection:
        dumped = " ".join(
            str(row)
            for table in ("sign_in_tokens", "sessions")
            for row in connection.execute(text(f"SELECT * FROM {table}"))
        )
    engine.dispose()
    assert token not in dumped and cookie not in dumped
    assert accounts.hash_token(token) in dumped and accounts.hash_token(cookie) in dumped


def test_the_link_comes_back_to_the_page_that_asked(
    anon: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    link = request_link(anon, caplog, "bob@example.com", next_path=week_page(5) + "?today=4")
    landed = anon.get(link, follow_redirects=False)
    assert landed.headers["location"] == week_page(5) + "?today=4"

    for elsewhere in ("//evil.example/x", "https://evil.example", "/\\evil.example"):
        link = request_link(anon, caplog, "bob@example.com", next_path=elsewhere)
        assert anon.get(link, follow_redirects=False).headers["location"] == "/"


def test_the_cookie_is_httponly_lax_and_secure_on_https(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    with TestClient(app) as plain:
        cookie = plain.get(request_link(plain, caplog, "alice@example.com"), follow_redirects=False)
        header = cookie.headers["set-cookie"].lower()
        assert "httponly" in header and "samesite=lax" in header and "secure" not in header
        assert "max-age=2592000" in header

    with TestClient(app, base_url="https://testserver") as tls:
        link = request_link(tls, caplog, "alice@example.com")
        header = tls.get(link, follow_redirects=False).headers["set-cookie"].lower()
        assert "secure" in header and "httponly" in header


def test_sign_in_is_rate_limited_per_address(anon: TestClient) -> None:
    answers = [
        anon.post("/auth/sign-in", json={"email": "Erin@Example.com"}).status_code for _ in range(7)
    ]
    assert answers[:5] == [202] * 5
    assert answers[5:] == [429, 429]
    # Another address from the same client still gets its link.
    assert anon.post("/auth/sign-in", json={"email": "frank@example.com"}).status_code == 202


def test_sign_in_is_rate_limited_per_client(anon: TestClient) -> None:
    answers = [
        anon.post("/auth/sign-in", json={"email": f"u{n}@example.com"}).status_code
        for n in range(22)
    ]
    assert answers.count(202) == 20 and answers[-1] == 429


def test_an_address_is_stored_lower_cased_and_junk_is_refused(
    anon: TestClient, session: Session
) -> None:
    assert anon.post("/auth/sign-in", json={"email": "  Gina@Example.COM "}).status_code == 202
    assert accounts.user_by_email(session, "gina@example.com") is not None
    assert anon.post("/auth/sign-in", json={"email": "not an address"}).status_code == 422
    assert anon.post("/auth/sign-in", json={"email": "@example.com"}).status_code == 422


def test_sign_out_revokes_the_session(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    cookie = alice.cookies[access.COOKIE]
    assert alice.get("/auth/me").status_code == 200

    out = alice.post("/auth/sign-out")
    assert out.json() == {"signed_out": True}
    assert alice.get("/auth/me").status_code == 401

    # The old cookie, replayed, is dead: revoked, not merely forgotten.
    alice.cookies.set(access.COOKIE, cookie)
    assert alice.get("/auth/me").status_code == 401
    assert alice.get(standings(LEAGUE_A)).status_code == 401


def test_an_expired_session_is_refused(sign_in: SignIn, session: Session) -> None:
    alice = sign_in("alice@example.com")
    cookie = alice.cookies[access.COOKIE]
    session.execute(
        text("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE token_hash = :h"),
        {"h": accounts.hash_token(cookie)},
    )
    session.commit()
    assert alice.get("/auth/me").status_code == 401


def test_me_says_who_and_where(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    me = alice.get("/auth/me").json()
    assert me["email"] == "alice@example.com"
    assert me["mode"] == "accounts" and me["via"] == "session" and me["owner"] is False
    assert me["leagues"] == [
        {
            "espn_league_id": LEAGUE_A,
            "role": "member",
            "teams": [{"season": SEASON, "espn_team_id": 3, "name": "Team 3", "state": "verified"}],
        }
    ]
    assert me["entitlement"] is None


# ---------------------------------------------------------------------------
# the owner and the service token
# ---------------------------------------------------------------------------


def test_the_service_token_is_the_owner(anon: TestClient, session: Session) -> None:
    bearer = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    assert anon.get(pickups(3), headers=bearer).status_code == THROUGH
    assert anon.get(pickups(5), headers=bearer).status_code == 403, "his team, not every team"
    me = anon.get("/auth/me", headers=bearer).json()
    assert me["email"] == OWNER and me["via"] == "service" and me["owner"] is True
    assert me["entitlement"]["source"] == "owner"
    # Verified on the tracked team in every season the league has.
    assert [t["season"] for t in me["leagues"][0]["teams"]] == [SEASON, SEASON - 1]

    assert anon.get(pickups(3), headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert anon.get(pickups(3), headers={"Authorization": "Bearer "}).status_code == 401


def test_the_owner_is_written_once(anon: TestClient, session: Session) -> None:
    bearer = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    for _ in range(3):
        anon.get("/auth/me", headers=bearer)
    owner = accounts.user_by_email(session, OWNER)
    assert owner is not None
    counts = session.execute(
        text(
            "SELECT (SELECT count(*) FROM entitlements WHERE user_id = :u),"
            " (SELECT count(*) FROM team_managers WHERE user_id = :u),"
            " (SELECT count(*) FROM memberships WHERE user_id = :u AND role = 'owner')"
        ),
        {"u": owner.id},
    ).one()
    assert tuple(counts) == (1, 2, 1)
    assert accounts.is_league_owner(session, owner.id, LEAGUE_A)
    assert not accounts.is_member(session, owner.id, LEAGUE_B)


def test_no_service_token_configured_means_no_bearer(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: accounts_settings(fcp_service_token=None)
    with TestClient(app) as client:
        assert client.get(pickups(3), headers={"Authorization": "Bearer "}).status_code == 401
        assert client.get(pickups(3), headers={"Authorization": "Bearer None"}).status_code == 401


# ---------------------------------------------------------------------------
# projection sets are their owner's
# ---------------------------------------------------------------------------


def test_a_projection_set_is_readable_only_by_its_owner(sign_in: SignIn, session: Session) -> None:
    alice = sign_in("alice@example.com")
    bob = sign_in("bob@example.com")
    stored = alice.post(
        "/projections/sets",
        files={"file": ("p.csv", PER_GAME.encode(), "text/csv")},
        data={"season": str(SEASON + 1), "name": "Mine", "owner": "patrick"},
    )
    assert stored.status_code == 200, stored.text
    set_id = stored.json()["set_id"]
    alice_user = accounts.user_by_email(session, "alice@example.com")
    assert alice_user is not None
    listed = alice.get("/projections/sets").json()
    assert [(s["id"], s["owner"]) for s in listed] == [(set_id, str(alice_user.id))]
    assert alice.get(f"/projections/sets/{set_id}/rows").status_code == 200

    assert bob.get("/projections/sets").json() == []
    assert bob.get(f"/projections/sets/{set_id}").status_code == 404
    assert bob.get(f"/projections/sets/{set_id}/rows").status_code == 404
    session.execute(text("TRUNCATE projection_sets RESTART IDENTITY CASCADE"))
    session.commit()


def test_the_owner_keeps_the_sets_stored_before_accounts(
    anon: TestClient, session: Session
) -> None:
    session.execute(
        text(
            "INSERT INTO projection_sets (season, name, owner, source_note, column_map, rows)"
            " VALUES (2027, 'Old', 'patrick', '', '{}', 0)"
        )
    )
    session.commit()
    bearer = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    assert [s["name"] for s in anon.get("/projections/sets", headers=bearer).json()] == ["Old"]
    session.execute(text("TRUNCATE projection_sets RESTART IDENTITY CASCADE"))
    session.commit()


# ---------------------------------------------------------------------------
# single mode, and every route has its one check
# ---------------------------------------------------------------------------


def test_single_mode_is_the_owner_with_no_cookie(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: accounts_settings(fcp_auth_mode="single")
    with TestClient(app) as client:
        me = client.get("/auth/me").json()
        assert me["mode"] == "single" and me["via"] == "single" and me["owner"] is True
        assert me["email"] == OWNER
        assert client.get(pickups(5)).status_code == THROUGH, "single mode refuses nothing"
        assert client.get(standings(LEAGUE_B)).status_code == 200
        assert {row["espn_league_id"] for row in client.get("/leagues").json()} == {
            LEAGUE_A,
            LEAGUE_B,
        }


#: The routes nobody has to be signed in for.
OPEN = {
    ("GET", "/health"),
    ("GET", "/sign-in"),
    ("POST", "/auth/sign-in"),
    ("GET", "/auth/callback"),
    ("POST", "/auth/sign-out"),
    ("GET", "/pages/static/{name}"),
    # The design language: a static file with no data in it; its specimens
    # read league routes, each behind its own check (docs/design_system.md).
    ("GET", "/design"),
    # The landing page signed out; signed in, a page that goes to his league.
    ("GET", "/"),
    # The OAuth front door (docs/mcp.md, tests/test_oauth.py). Open because
    # the spec says so and because none of them gives anything away: the
    # metadata describes the server, registering gets no secret, and the
    # token and revocation endpoints are held to a one-time code with PKCE or
    # to holding the token already. `/oauth/authorize` and `/oauth/consent`
    # are NOT here: they are pages, and a signed-out browser goes to sign in.
    ("GET", "/.well-known/oauth-authorization-server"),
    ("POST", "/oauth/register"),
    ("POST", "/oauth/token"),
    ("POST", "/oauth/revoke"),
}


def endpoints(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
    """Every (method, path, route) the app serves.

    FastAPI keeps an included router as one entry wrapping the router, so the
    routes are walked out of it; FastAPI's own /docs, /redoc and
    /openapi.json are not APIRoutes and are left out.
    """

    def walk(routes: Iterable[Any]) -> Iterator[APIRoute]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "original_router"):
                yield from walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                yield from walk(route.routes)

    return [(m, r.path, r) for r in walk(app.routes) for m in sorted(r.methods or ())]


def test_every_route_declares_exactly_one_check(app: FastAPI) -> None:
    found = endpoints(app)
    assert len(found) >= 50, "the walk found the routes"
    missing, doubled = [], []
    for method, path, route in found:
        declared = [d for d in route.dependant.dependencies if d.call in access.CHECKS]
        if (method, path) in OPEN:
            assert not declared, f"{method} {path} is open and declares {declared}"
        elif not declared:
            missing.append(f"{method} {path}")
        elif len(declared) > 1:
            doubled.append(f"{method} {path}")
    assert missing == [] and doubled == []


def test_the_scope_table_in_the_docs_names_every_route(app: FastAPI) -> None:
    table = (REPO_ROOT / "docs" / "accounts.md").read_text()
    for method, path, _ in endpoints(app):
        assert f"`{method} {path}`" in table, f"{method} {path} is not in docs/accounts.md"


def test_the_access_log_never_carries_a_token(app: FastAPI) -> None:
    """Uvicorn logs each request line; a callback's token is blanked in it."""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", "/auth/callback?token=abc-DEF_123&x=1", "1.1", 303),
        None,
    )
    ours = [f for f in logging.getLogger("uvicorn.access").filters if getattr(f, "fcp", False)]
    assert len(ours) == 1, "attached once, however many apps are built"
    assert isinstance(ours[0], logging.Filter) and ours[0].filter(record)
    line = record.getMessage()
    assert "abc-DEF_123" not in line
    assert "/auth/callback?token=[redacted]&x=1" in line


# ---------------------------------------------------------------------------
# the mailed link
# ---------------------------------------------------------------------------


def smtp_settings(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_smtp_host": "smtp.example.test",
        "fcp_email_from": "fcp@example.test",
        "fcp_public_url": "https://fcp.example.test/",
    }
    base.update(changes)
    return accounts_settings(**base)


def test_with_smtp_the_link_is_mailed_on_the_public_url_and_not_logged(
    app: FastAPI, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def fake_send(text: str, **kwargs: Any) -> None:
        sent.append({"text": text, **kwargs})

    monkeypatch.setattr("app.api.auth.send_email", fake_send)
    app.dependency_overrides[get_settings] = lambda: smtp_settings()
    caplog.set_level(logging.INFO, logger="fcp.auth")
    with TestClient(app) as client:
        asked = client.post(
            "/auth/sign-in", json={"email": "Hana@Example.com"}, headers={"Host": "evil.test"}
        )
    assert asked.status_code == 202
    assert len(sent) == 1 and sent[0]["recipients"] == ["hana@example.com"]
    link = re.search(r"(\S+/auth/callback\?token=(\S+))", sent[0]["text"])
    assert link is not None
    # Built on the configured address, not on the Host header the caller wrote.
    assert link.group(1).startswith("https://fcp.example.test/auth/callback?token=")
    token = link.group(2)
    assert token not in asked.text
    assert not any(token in r.getMessage() for r in caplog.records)


def test_with_smtp_and_no_public_url_nothing_is_mailed(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []

    def fake_send(text: str, **_: Any) -> None:
        sent.append(text)

    monkeypatch.setattr("app.api.auth.send_email", fake_send)
    app.dependency_overrides[get_settings] = lambda: smtp_settings(fcp_public_url=None)
    with TestClient(app) as client:
        assert client.post("/auth/sign-in", json={"email": "ian@example.com"}).status_code == 503
    assert sent == []


def test_a_refused_email_says_so_without_the_servers_words(
    app: FastAPI, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(text: str, **_: Any) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"bad password hunter2")

    monkeypatch.setattr("app.api.auth.send_email", refuse)
    app.dependency_overrides[get_settings] = lambda: smtp_settings()
    caplog.set_level(logging.INFO, logger="fcp.auth")
    with TestClient(app) as client:
        failed = client.post("/auth/sign-in", json={"email": "jo@example.com"})
    assert failed.status_code == 503
    assert "hunter2" not in failed.text
    assert not any("hunter2" in r.getMessage() for r in caplog.records)
