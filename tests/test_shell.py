"""The site shell: every page on the map, who may open it, and the old addresses.

Step 3 of docs/product.md; docs/site.md is the map. In accounts mode, like
tests/test_access.py and over the same shape of league (its helpers build
it): league A with teams 3 and 5 (alice manages 3, bob 5), league B with
team 3 (carol). Checked here:

* every page on the map sends a signed-out browser to sign in and back, and
  `/` is the landing page instead;
* a league page opens to any member of the league and gives anyone else the
  one refusal line; a team page opens to its manager only; an account page
  opens to anyone signed in;
* the old `/pages/...` addresses redirect to the new ones with their query,
  and keep refusing whoever they refused;
* the team pages close when billing is on, and the league pages do not;
* `/me/alerts` shows the digest's channels to the owner alone, never a URL;
* single mode serves everything with no cookie, as it always has;
* every page draws the shell, and no page's scripts declare a name another
  script on it already has (a SyntaxError in the browser, which no route
  test would otherwise see).
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api import access
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
    SignIn,
    _claim,
    _season,
    accounts_settings,
    request_link,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "app" / "api" / "static"

LEAGUE_SECTIONS = ("week", "standings", "draft", "history")
TEAM_SECTIONS = ("week", "season", "moves", "trades")
ACCOUNT_SECTIONS = ("connections", "projections", "alerts")

LEAGUE_REFUSED = "This league&#x27;s pages are its members&#x27;."
TEAM_REFUSED = "This team&#x27;s plan is its manager&#x27;s."


def league_page(section: str, league: int = LEAGUE_A) -> str:
    return f"/l/{league}/{SEASON}/{section}"


def team_page(team: int, which: str, league: int = LEAGUE_A) -> str:
    return f"/l/{league}/{SEASON}/team/{team}/{which}"


LEAGUE_PAGES = [league_page(s) for s in LEAGUE_SECTIONS]
TEAM_PAGES = [team_page(3, w) for w in TEAM_SECTIONS]
ACCOUNT_PAGES = [f"/account/{s}" for s in ACCOUNT_SECTIONS]
EVERY_PAGE = LEAGUE_PAGES + TEAM_PAGES + ACCOUNT_PAGES


# ---------------------------------------------------------------------------
# fixtures: tests/test_access.py's league, in a schema of this module's own
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# signed out
# ---------------------------------------------------------------------------


def test_every_page_sends_a_signed_out_browser_to_sign_in_and_back(anon: TestClient) -> None:
    for path in EVERY_PAGE:
        asked = f"{path}?today=5"
        sent = anon.get(asked, follow_redirects=False)
        assert sent.status_code == 303, path
        assert sent.headers["location"] == f"/sign-in?next={quote(asked, safe='/')}", path


def test_the_front_door_is_the_landing_page_when_signed_out(anon: TestClient) -> None:
    landing = anon.get("/")
    assert landing.status_code == 200
    assert "Sign in with your email" in landing.text
    assert 'href="/sign-in"' in landing.text
    # No shell and no fetch: nothing on it is anybody's data.
    assert "shell.js" not in landing.text
    assert "/auth/me" not in landing.text


def test_the_json_the_pages_read_is_refused_signed_out(anon: TestClient) -> None:
    assert anon.get("/me/alerts").status_code == 401
    assert anon.get("/leagues").status_code == 401


# ---------------------------------------------------------------------------
# who may open what
# ---------------------------------------------------------------------------


def test_signed_in_the_front_door_is_the_shell_that_finds_his_league(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    home = alice.get("/")
    assert home.status_code == 200
    assert "/pages/static/shell.js" in home.text
    assert "Sign in with your email" not in home.text


def test_a_manager_opens_his_league_his_team_and_his_account(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    for path in EVERY_PAGE:
        page = alice.get(path)
        assert page.status_code == 200, path
        assert page.headers["content-type"].startswith("text/html")
        assert '<div id="shell"></div>' in page.text, path
    # Any season of a league he is in.
    assert alice.get(f"/l/{LEAGUE_A}/{SEASON - 1}/standings").status_code == 200


def test_a_member_is_refused_another_members_team(sign_in: SignIn) -> None:
    bob = sign_in("bob@example.com")
    for path in LEAGUE_PAGES:
        assert bob.get(path).status_code == 200, path
    for which in TEAM_SECTIONS:
        assert bob.get(team_page(5, which)).status_code == 200
        refused = bob.get(team_page(3, which))
        assert refused.status_code == 403
        assert TEAM_REFUSED in refused.text


def test_a_stranger_hears_the_refusal_line_and_nothing_else(sign_in: SignIn) -> None:
    carol = sign_in("carol@example.com")
    for path in LEAGUE_PAGES:
        refused = carol.get(path)
        assert refused.status_code == 403, path
        assert LEAGUE_REFUSED in refused.text
        assert "shell.js" not in refused.text
    for path in TEAM_PAGES:
        refused = carol.get(path)
        assert refused.status_code == 403, path
        assert TEAM_REFUSED in refused.text
    # A league that does not exist is the same answer as one he is not in.
    assert carol.get(league_page("week", league=999)).status_code == 403
    # His own league and his account are his.
    assert carol.get(league_page("week", league=LEAGUE_B)).status_code == 200
    for path in ACCOUNT_PAGES:
        assert carol.get(path).status_code == 200, path


def test_the_team_pages_close_with_billing_and_the_league_pages_do_not(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = sign_in("alice@example.com")
    monkeypatch.setattr(access, "BILLING_ENABLED", True)
    for path in TEAM_PAGES:
        assert alice.get(path).status_code == 402, path
    for path in LEAGUE_PAGES:
        assert alice.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# the old addresses
# ---------------------------------------------------------------------------


def test_the_old_addresses_redirect_with_their_query(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    moved = {
        f"/pages/teams/{LEAGUE_A}/{SEASON}?today=5": f"/l/{LEAGUE_A}/{SEASON}/standings?today=5",
        f"/pages/teams/{LEAGUE_A}/{SEASON}/3/week?today=5&me=x": (
            f"/l/{LEAGUE_A}/{SEASON}/team/3/week?today=5&me=x"
        ),
        f"/pages/teams/{LEAGUE_A}/{SEASON}/3/season": f"/l/{LEAGUE_A}/{SEASON}/team/3/season",
        "/pages/connections": "/account/connections",
    }
    for old, new in moved.items():
        sent = alice.get(old, follow_redirects=False)
        assert sent.status_code == 308, old
        assert sent.headers["location"] == new
        assert alice.get(old).status_code == 200, f"{old} lands"


def test_the_old_addresses_refuse_whoever_they_refused(sign_in: SignIn, anon: TestClient) -> None:
    bob = sign_in("bob@example.com")
    assert (
        bob.get(f"/pages/teams/{LEAGUE_A}/{SEASON}/3/week", follow_redirects=False).status_code
        == 403
    )
    carol = sign_in("carol@example.com")
    assert carol.get(f"/pages/teams/{LEAGUE_A}/{SEASON}", follow_redirects=False).status_code == 403
    sent = anon.get(f"/pages/teams/{LEAGUE_A}/{SEASON}/3/week", follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"].startswith("/sign-in?next=")


# ---------------------------------------------------------------------------
# the JSON the shell and the account pages read
# ---------------------------------------------------------------------------


def test_the_leagues_carry_their_names_for_the_switcher(sign_in: SignIn) -> None:
    alice = sign_in("alice@example.com")
    assert alice.get("/leagues").json() == [
        {"espn_league_id": LEAGUE_A, "name": f"League {LEAGUE_A}", "seasons": [SEASON - 1, SEASON]}
    ]


def test_the_alerts_are_the_owners_alone_and_never_a_url(app: FastAPI, sign_in: SignIn) -> None:
    owner = sign_in(OWNER)
    alice = sign_in("alice@example.com")
    secret_url = "https://api.telegram.org/bot123:SECRET/sendMessage"
    app.dependency_overrides[get_settings] = lambda: accounts_settings(
        fcp_smtp_host="smtp.example.com",
        fcp_email_from="fcp@example.com",
        fcp_email_to="owner@example.com, second@example.com",
        fcp_digest_url=secret_url,
        fcp_digest_chat_id="42",
    )
    mine = owner.get("/me/alerts")
    assert mine.status_code == 200
    assert mine.json() == {
        "yours": True,
        "channels": [
            {"kind": "email", "detail": "owner@example.com, second@example.com"},
            {"kind": "telegram", "detail": "a Telegram chat"},
        ],
        "per_member": True,
    }
    assert "SECRET" not in mine.text and "42" not in mine.text
    theirs = alice.get("/me/alerts").json()
    assert theirs == {"yours": False, "channels": [], "per_member": True}


# ---------------------------------------------------------------------------
# single mode
# ---------------------------------------------------------------------------


def test_single_mode_serves_every_page_with_no_cookie(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: accounts_settings(fcp_auth_mode="single")
    with TestClient(app) as client:
        for path in [*EVERY_PAGE, team_page(5, "week"), league_page("week", league=LEAGUE_B)]:
            assert client.get(path).status_code == 200, path
        home = client.get("/")
        assert home.status_code == 200 and "shell.js" in home.text, "the owner's home"
        old = client.get(f"/pages/teams/{LEAGUE_A}/{SEASON}/5/week", follow_redirects=False)
        assert old.status_code == 308


# ---------------------------------------------------------------------------
# the files themselves
# ---------------------------------------------------------------------------

#: The pages the shell draws on, by file.
SHELL_PAGES = [
    "league-week.html",
    "league-standings.html",
    "league-draft.html",
    "league-history.html",
    "week.html",
    "season.html",
    "moves.html",
    "trades.html",
    "connections.html",
    "projections.html",
    "alerts.html",
    "home.html",
    "claim.html",
    "join.html",
]


@pytest.mark.parametrize("name", SHELL_PAGES)
def test_every_page_draws_the_shell_in_the_house_style(name: str) -> None:
    page = (STATIC / name).read_text()
    assert '<div id="shell"></div>' in page
    assert page.index("/pages/static/pages.js") < page.index("/pages/static/shell.js")
    assert "/pages/static/pages.css" in page
    visible = re.sub(r"<script.*?</script>", "", page, flags=re.S).lower()
    assert "recommend" not in visible, "the tool suggests; the manager decides"


def test_the_shell_is_the_navigation_the_product_draws() -> None:
    """docs/product.md, "Navigation": the words, in the order drawn."""
    shell = (STATIC / "shell.js").read_text()
    order = ["This week", "Standings", "Draft", "History", "Week", "Season", "Moves", "Trades"]
    at = [shell.index(f'"{word}"') for word in order]
    assert at == sorted(at)
    for word in ("My team", "Claim your team", "Connections", "Projections", "Alerts", "Sign out"):
        assert word in shell
    assert "localStorage" in shell and "try {" in shell, "the league is remembered, wrapped"
    assert "aria-expanded" in shell and "Escape" in shell, "the menus work from the keyboard"
    assert "recommend" not in shell.lower()


#: A declaration at the top level of a classic script: one global scope is
#: shared by every script on a page, so a name declared twice with const or
#: let stops the second script from running at all.
TOP_LEVEL = re.compile(r"^(?:async\s+)?(?:function\s+(\w+)|(?:const|let|class)\s+(\w+))", re.M)


def _scripts(page: str) -> list[str]:
    found = []
    for match in re.finditer(r'<script(?:\s+src="([^"]+)")?\s*>(.*?)</script>', page, re.S):
        src, body = match.group(1), match.group(2)
        found.append((STATIC / src.rsplit("/", 1)[-1]).read_text() if src else body)
    return found


@pytest.mark.parametrize("name", sorted(p.name for p in STATIC.glob("*.html")))
def test_no_script_on_a_page_declares_a_name_another_already_has(name: str) -> None:
    seen: dict[str, int] = {}
    for index, script in enumerate(_scripts((STATIC / name).read_text())):
        for match in TOP_LEVEL.finditer(script):
            declared = match.group(1) or match.group(2)
            assert declared not in seen, f"{name}: {declared} is declared twice"
            seen[declared] = index


def test_the_map_in_the_docs_names_every_page() -> None:
    site = (REPO_ROOT / "docs" / "site.md").read_text()
    for path in (
        "/l/{league_id}/{season}/week",
        "/l/{league_id}/{season}/standings",
        "/l/{league_id}/{season}/draft",
        "/l/{league_id}/{season}/history",
        "/l/{league_id}/{season}/team/{team_id}/week",
        "/l/{league_id}/{season}/team/{team_id}/season",
        "/l/{league_id}/{season}/team/{team_id}/moves",
        "/l/{league_id}/{season}/team/{team_id}/trades",
        "/account/connections",
        "/account/projections",
        "/account/alerts",
    ):
        assert f"`{path}`" in site, path
