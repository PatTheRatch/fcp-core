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
#: The team's pages; "" is the team's own address, its Overview.
TEAM_SECTIONS = ("", "week", "season", "moves", "trades")
ACCOUNT_SECTIONS = ("connections", "projections", "alerts")

LEAGUE_REFUSED = "This league&#x27;s pages are its members&#x27;."
TEAM_REFUSED = "This team&#x27;s plan is its manager&#x27;s."


def league_page(section: str, league: int = LEAGUE_A) -> str:
    return f"/l/{league}/{SEASON}/{section}"


def team_page(team: int, which: str, league: int = LEAGUE_A) -> str:
    return f"/l/{league}/{SEASON}/team/{team}" + (f"/{which}" if which else "")


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


def test_the_alerts_are_the_owners_alone(app: FastAPI, sign_in: SignIn) -> None:
    owner = sign_in(OWNER)
    alice = sign_in("alice@example.com")
    app.dependency_overrides[get_settings] = lambda: accounts_settings(
        fcp_smtp_host="smtp.example.com",
        fcp_email_from="fcp@example.com",
        fcp_email_to="owner@example.com, second@example.com",
    )
    mine = owner.get("/me/alerts")
    assert mine.status_code == 200
    assert mine.json() == {
        "yours": True,
        "channels": [{"kind": "email", "detail": "owner@example.com, second@example.com"}],
        "per_member": True,
    }
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
    "overview.html",
    "claim.html",
    "join.html",
    "design.html",
]


@pytest.mark.parametrize("name", SHELL_PAGES)
def test_every_page_draws_the_shell_in_the_house_style(name: str) -> None:
    page = (STATIC / name).read_text()
    assert '<div id="shell"></div>' in page
    assert page.index("/pages/static/pages.js") < page.index("/pages/static/shell.js")
    assert "/pages/static/pages.css" in page
    visible = re.sub(r"<script.*?</script>", "", page, flags=re.S).lower()
    assert "recommend" not in visible, "the tool suggests; the manager decides"


def test_the_shell_is_the_rail_the_design_draws() -> None:
    """docs/design_system.md, "The rail": the words, in the order drawn --
    the team's pages, then the league's -- and the two with no page of their
    own yet say so rather than pretending."""
    shell = (STATIC / "shell.js").read_text()
    order = [
        "Matchup",
        "Roster",
        "Moves",
        "Trades",
        "Season",
        "This week",
        "Standings",
        "Players",
        "Draft",
        "History",
    ]
    at = [shell.index(f'"{word}"') for word in order]
    assert at == sorted(at)
    for word in ("Overview", "Scenario", "Claim your team", "Connections", "Projections"):
        assert word in shell
    for word in ("Alerts", "Sign out", "Theme"):
        assert word in shell
    assert '"roster", "Roster", "week", "#tonight-section", false' in shell, "pending, and says so"
    assert '"players", "Players", null, false' in shell
    assert "localStorage" in shell and "try {" in shell, "the league is remembered, wrapped"
    assert "aria-expanded" in shell and "Escape" in shell, "the menus work from the keyboard"
    assert "recommend" not in shell.lower()


def test_a_tap_on_a_name_opens_the_card_the_first_time() -> None:
    """The card opens into the inspection drawer on a click -- a tap, a
    mouse click and Enter are all one click -- so the first tap opens it.

    The old floating card was opened by hover and focus as well, and at
    phone width the focus that comes with a tap pinned it a moment before
    the tap arrived, which then read as a second tap and closed it. Now
    hover and focus never open anything: on a `data-card-hover` control of
    its own they only follow a drawer that is already open, on a desk.
    """
    shell = (STATIC / "shell.js").read_text()
    wired = shell.split("function wireCards")[1].split("\n}\n")[0]
    follow = wired.split("const follow")[1].split("return;\n    }")[0]
    assert "DRAWER.isOpen()" in follow and "!phoneWidth()" in follow, "hover only follows"
    assert "trigger.onmouseenter = follow" in wired and "trigger.onfocus = follow" in wired
    assert "trigger.onclick" in wired and "openCard(id, trigger)" in wired, "the click opens it"
    assert wired.index("const follow") < wired.index("trigger.onclick")


def test_the_drawer_is_one_panel_the_page_stays_live_beside() -> None:
    """A right-side panel on a desk and a sheet from the bottom on a phone,
    closed by its button or Escape, which gives the focus back."""
    shell = (STATIC / "shell.js").read_text()
    css = (STATIC / "pages.css").read_text()
    drawer = shell.split("const DRAWER")[1].split("})();")[0]
    assert 'setAttribute("aria-modal", "false")' in drawer, "the page stays live beside it"
    assert "was.focus()" in drawer, "closing gives the focus back"
    assert ".ws-drawer{position:fixed;top:0;right:0;bottom:0" in css
    phone = css.split("@media (max-width:700px){\n  .ws-drawer{")[1].split("}")[0]
    assert "top:auto" in phone and "bottom:0" in phone, "a sheet from the bottom on a phone"


def test_the_scenario_is_a_seam_with_one_writer() -> None:
    """scenario.js holds the state and the hook; only the week page's What
    if sets it today, from its own answer; nothing is persisted."""
    scenario = (STATIC / "scenario.js").read_text()
    week = (STATIC / "week.html").read_text()
    for part in ("get:", "set(next)", "view(which)", "reset()", "subscribe(listener)"):
        assert part in scenario, part
    for field in ("changes", "effect", "view", "source", "provenance", "saved"):
        assert f"{field}:" in scenario, field
    assert "localStorage" not in scenario, "global, persisted state is not this module's yet"
    assert "answer.week ? answer.week.delta" in scenario
    assert "answer.judgement ? answer.judgement.delta_season_per_week" in scenario
    assert "playoffs.measurable ? playoffs.delta_per_week" in scenario
    assert "SCENARIO.set(\n    SCENARIO.fromWhatIf(a," in week
    assert "SCENARIO.subscribe(" in week and "whatIfBaselineHtml" in week
    setters = [
        name for name in sorted(STATIC.glob("*.html")) if "SCENARIO.set(" in name.read_text()
    ]
    assert [p.name for p in setters] == ["design.html", "week.html"]


def test_the_week_page_leaves_its_navigation_to_the_shell() -> None:
    """The rail carries Trades, Season and Moves, so the week page's own
    "Elsewhere" and the product's name in its eyebrow are gone."""
    week = (STATIC / "week.html").read_text()
    assert "Elsewhere" not in week and 'id="elsewhere"' not in week
    assert "{{brand}} &middot; the week" not in week


def test_orange_is_never_a_buttons_fill() -> None:
    """Orange is spent on what is selected, not on what to press: the new
    controls' primary button is ink (docs/design_system.md, "Colour")."""
    css = (STATIC / "pages.css").read_text()
    primary = css.split(".ws-btn.primary{")[1].split("}")[0]
    assert "accent" not in primary
    tokens = css.split(":root,.t-light{")[1].split("}")[0]
    for name in ("--accent:", "--pos:", "--neg:", "--neutral:"):
        assert name in tokens, name


def test_the_design_page_is_open_and_carries_no_data(anon: TestClient) -> None:
    """Signed out it is served, with no league's anything in the file: its
    specimens read league routes, which still refuse a stranger."""
    page = anon.get("/design")
    assert page.status_code == 200
    assert '<div id="shell"></div>' in page.text
    assert "signedOutOk" in (STATIC / "shell.js").read_text()
    assert anon.get(f"/leagues/{LEAGUE_A}/seasons/{SEASON}/pages/context").status_code == 401


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


def test_what_changed_prints_the_api_sentence_and_leaves_the_card_a_hook() -> None:
    """The league's This week page carries the feed (docs/in_season_pages.md).

    Nothing in it is worked out in the browser: the page asks `/changes` for
    a window, prints the sentence the API wrote -- the same one the digest
    sends, so the two cannot disagree -- and only groups and filters. Each
    player's name is marked up so the shared player card can attach to it
    once that has landed.
    """
    page = (STATIC / "league-week.html").read_text()
    # The line itself is the shared script's since the Overview's RECENT
    # prints the same feed.
    script = (STATIC / "pages.js").read_text()

    assert "What changed" in page
    assert "/changes?" in page, "the feed comes from the route, not from the page"
    assert 'id="changed-mine"' in page and 'for="changed-mine"' in page, "the filter, labelled"
    assert "change.mine || change.opponent" in page, "the flags are the API's, not the page's"
    assert "changedHtml(shown)" in page, "the shared line, not a copy"
    assert "cardName(person.espn_player_id" in script, "a name in a sentence opens the card"
    assert 'wireCards($("changed-body"))' in page, "and is wired again on every redraw"
    assert "escape(change.text)" in script, "the sentence is printed, never rebuilt"


# ---------------------------------------------------------------------------
# the Overview
# ---------------------------------------------------------------------------


def test_the_rail_marks_overview_current_on_the_overview_and_nowhere_else() -> None:
    """The rail's first item is the team's own address, and the one page it
    carries `aria-current` on is that one; it no longer says it is to come."""
    shell = (STATIC / "shell.js").read_text()
    item = shell.split("parts.push(\n    myTeam\n")[1].split(");")[0]
    assert 'teamPage("overview")' in item, "the team's own address"
    assert 'current(ours && where.section === "team-overview")' in item, "current there only"
    assert "still to come" not in shell and "title=" not in item, "the title attribute is gone"
    pages = (STATIC / "pages.js").read_text()
    assert '"team-overview"' in pages, "the team's own address is the overview section"
    # Every other team page has a suffix, so no other page is that section:
    # the rail's item and the top bar's words are the only two readers.
    assert shell.count('"team-overview"') == 2


def test_the_overview_opens_to_its_manager_alone(sign_in: SignIn) -> None:
    """The team pages' check: the verified manager, entitled; a stranger to
    the team hears the one line and nothing else."""
    alice = sign_in("alice@example.com")
    mine = alice.get(team_page(3, ""))
    assert mine.status_code == 200 and 'id="matchup-section"' in mine.text
    bob = sign_in("bob@example.com")
    refused = bob.get(team_page(3, ""))
    assert refused.status_code == 403
    assert TEAM_REFUSED in refused.text and "shell.js" not in refused.text
    assert bob.get(team_page(5, "")).status_code == 200
    carol = sign_in("carol@example.com")
    assert carol.get(team_page(3, "")).status_code == 403


def test_the_overview_closes_with_billing(sign_in: SignIn, monkeypatch: pytest.MonkeyPatch) -> None:
    alice = sign_in("alice@example.com")
    monkeypatch.setattr(access, "BILLING_ENABLED", True)
    refused = alice.get(team_page(3, ""))
    assert refused.status_code == 402
    assert "The team layer is part of the paid plan." in refused.text
    assert alice.get(league_page("week")).status_code == 200, "the free tier's This week"


def test_a_claimed_viewer_lands_on_his_overview(sign_in: SignIn) -> None:
    """`/` is the shell's page; the shell's team is his verified claim, and
    the page goes on to that team's Overview (with none, This week)."""
    alice = sign_in("alice@example.com")
    home = alice.get("/")
    assert home.status_code == 200 and "shell.js" in home.text
    assert 'ctx.myTeam.espn_team_id, "overview")' in home.text
    claims = alice.get("/auth/me").json()["leagues"]
    teams = [
        team
        for league in claims
        if league["espn_league_id"] == LEAGUE_A
        for team in league["teams"]
    ]
    held = [(t["season"], t["espn_team_id"], t["state"]) for t in teams]
    assert held == [(SEASON, 3, "verified")]
    shell = (STATIC / "shell.js").read_text()
    assert 'const verified = (league.teams || []).filter((t) => t.state === "verified")' in shell


def test_before_the_draft_the_overview_draws_only_what_needs_no_roster(
    sign_in: SignIn,
) -> None:
    """These leagues have no draft, so every projection route answers with
    `readiness`; the page prints its sentence once, keeps STANDINGS and
    RECENT, makes the draft the one line of NEEDS ATTENTION, and leaves
    MATCHUP, THE WIRE and TONIGHT out rather than empty."""
    alice = sign_in("alice@example.com")
    base = f"/leagues/{LEAGUE_A}/seasons/{SEASON}/teams/3"
    glance = alice.get(f"{base}/pickups/glance").json()
    assert glance["readiness"]["ready"] is False and glance["readiness"]["note"]
    page = alice.get(team_page(3, "")).text
    script = page.split('<script>\n"use strict";')[1]
    for draw in ("drawMatchup", "drawWire", "drawTonight"):
        body = script.split(f"function {draw}()")[1].split("\n}\n")[0]
        assert "NOT_READY" in body and ".hidden = true" in body, draw
    for draw in ("drawStandings", "drawRecent"):
        body = script.split(f"function {draw}()")[1].split("\n}\n")[0]
        assert "NOT_READY" not in body, f"{draw} needs no roster"
    assert "NOT_READY.note" in script and '$("ready")' in script, "the sentence, once"
    assert 'leagueUrl(WHERE.league, WHERE.season, "draft")' in script, "the draft, linked"
    assert 'STREAM.state = "none"' in script, "the week report is not asked for"
