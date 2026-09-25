"""The product's name: one source, and nothing a reader sees says the old one.

The product is Box Out (`app/brand.py`). It was Full Court Press, which is
still the name of Patrick's own league, so the old string survives in the
measurement notes and in ESPN's data on purpose. What must not survive is a
*page* or a *mail* that says it: those are the product naming itself.

So this module does three sweeps and a reading of the landing page.

* **Every file the site serves**, filled as the routes fill it, must be clean.
  That catches a page whose masthead was missed.
* **Every page fetched over HTTP**, in single mode where the one user opens
  everything, must be clean *and* must carry no `{{token}}` left unfilled.
  That catches a route that forgot `brand.fill`, which the file sweep cannot
  see.
* **Every mail the server can build**, subject, HTML and text part, because
  an email is a page too (docs/site.md) and is the one thing a reader keeps.
* **The landing page**, signed out, is the one page a stranger reads, so its
  wiring is checked in full: the name, the tagline, the three measured
  figures with their sources, the stance, the way in, and nothing that
  fetches anybody's data.

`FCP_*` settings, `fcp-core`, `fcp_session` and the rest are identifiers and
are deliberately not matched: the pattern wants FCP as a word, and those all
run it into an underscore or a hyphen, or are lower case.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import brand
from app.api.deps import get_session
from app.config import get_settings
from app.mail import Mail, alert_mail, confirm_mail, digest_mail, lines_mail, render, sign_in_mail
from app.main import create_app
from app.subscriptions import FULL, everything
from tests.test_access import LEAGUE_A, SEASON, accounts_settings, seeded  # noqa: F401
from tests.test_mail import SITE, _digest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The old name, in the two shapes it was ever written in. `\bFCP\b` does not
#: match `FCP_SMTP_HOST`, `fcp-core` or `fcp_session`: an underscore is a word
#: character and the others are lower case.
OLD = (re.compile(r"full\s+court\s+press", re.IGNORECASE), re.compile(r"\bFCP\b"))

#: A token that reached a reader means a route served a file without filling
#: it in. `{{` is not otherwise written in any of these files.
UNFILLED = re.compile(r"\{\{\s*brand")

SERVED_DIRS = (
    REPO_ROOT / "app" / "api" / "static",
    REPO_ROOT / "app" / "draft" / "static",
)


def offences(text: str) -> list[str]:
    return [found.group(0) for pattern in OLD for found in pattern.finditer(text)]


# ---------------------------------------------------------------------------
# the constant itself
# ---------------------------------------------------------------------------


def test_the_brand_is_what_the_cutover_expects() -> None:
    assert brand.BRAND == "Box Out"
    assert brand.BRAND_DOMAIN == "boxoutfantasy.com"
    assert brand.BRAND_TAGLINE and brand.BRAND_TAGLINE[-1] != "."


def test_fill_replaces_every_token_and_leaves_the_rest_alone() -> None:
    filled = brand.fill("<h1>{{brand}}</h1><p>{{brand_tagline}} at {{brand_domain}}</p>")
    want = f"<h1>{brand.BRAND}</h1><p>{brand.BRAND_TAGLINE} at {brand.BRAND_DOMAIN}</p>"
    assert filled == want
    assert brand.fill("body{margin:0}") == "body{margin:0}", "a file with no token is untouched"


# ---------------------------------------------------------------------------
# the files the site serves
# ---------------------------------------------------------------------------


#: The one served file that is not text: the touch icon, bytes with no name in them.
BINARY = {".png"}


def served_files() -> list[Path]:
    return sorted(
        path
        for folder in SERVED_DIRS
        for path in folder.iterdir()
        if path.is_file() and path.suffix not in BINARY
    )


def test_no_served_file_says_the_old_name() -> None:
    assert len(served_files()) > 15, "the sweep found the pages"
    guilty = {
        str(path.relative_to(REPO_ROOT)): found
        for path in served_files()
        if (found := offences(brand.fill(path.read_text())))
    }
    assert guilty == {}


def test_the_pages_that_name_the_product_read_it_from_the_constant() -> None:
    """Not just "no old name": the new one has to actually arrive."""
    named = [
        path
        for path in served_files()
        if brand.BRAND in brand.fill(path.read_text()) and "{{brand}}" in path.read_text()
    ]
    assert len(named) >= 10, "the mastheads, the titles and the shell all read the constant"


# ---------------------------------------------------------------------------
# the pages, over HTTP
# ---------------------------------------------------------------------------

PAGES = (
    "/",
    "/sign-in",
    f"/l/{LEAGUE_A}/{SEASON}/week",
    f"/l/{LEAGUE_A}/{SEASON}/standings",
    f"/l/{LEAGUE_A}/{SEASON}/draft",
    f"/l/{LEAGUE_A}/{SEASON}/history",
    f"/l/{LEAGUE_A}/{SEASON}/team/3/week",
    f"/l/{LEAGUE_A}/{SEASON}/team/3/season",
    f"/l/{LEAGUE_A}/{SEASON}/team/3/moves",
    f"/l/{LEAGUE_A}/{SEASON}/team/3/trades",
    "/account/connections",
    "/account/projections",
    "/account/alerts",
    f"/pages/claim/{LEAGUE_A}/{SEASON}",
    "/join/a-token-that-goes-nowhere",
    "/pages/static/pages.css",
    "/pages/static/pages.js",
    "/pages/static/shell.js",
    "/pages/static/scenario.js",
    "/design",
)


@pytest.fixture
def single(seeded: sessionmaker[Session]) -> Iterator[TestClient]:  # noqa: F811
    """Single mode, where the one user opens every page with no cookie."""
    built = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    built.dependency_overrides[get_session] = override
    with TestClient(built) as client:
        yield client
    built.dependency_overrides.clear()


@pytest.mark.parametrize("path", PAGES)
def test_a_served_page_says_the_new_name_and_no_token(single: TestClient, path: str) -> None:
    page = single.get(path)
    assert page.status_code == 200, path
    assert offences(page.text) == [], path
    assert UNFILLED.search(page.text) is None, f"{path} was served without brand.fill"


@pytest.mark.parametrize("path", [p for p in PAGES if "/pages/static/" not in p])
def test_a_served_page_declares_the_icon(single: TestClient, path: str) -> None:
    page = single.get(path)
    assert page.status_code == 200, path
    assert '<link rel="icon" href="/pages/static/favicon.svg" type="image/svg+xml">' in page.text


def test_the_shell_draws_the_name(single: TestClient) -> None:
    shell = single.get("/pages/static/shell.js").text
    assert f'aria-label="{brand.BRAND}, home"' in shell
    assert f">{brand.BRAND}</a>" in shell


def test_a_refused_page_and_a_dead_link_carry_the_name(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    """The two pages built in Python rather than read from a file."""
    built = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: accounts_settings()
    with TestClient(built) as client:
        # A league nobody signed out is a member of: the refusal page.
        refused = client.get(f"/l/{LEAGUE_A}/{SEASON}/week", follow_redirects=False)
        assert refused.status_code == 303, "signed out goes to sign in"
        dead = client.get("/auth/callback?token=nothing-of-the-sort")
        assert dead.status_code == 400
        assert brand.BRAND in dead.text
        assert offences(dead.text) == []
    built.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# the mails
# ---------------------------------------------------------------------------


def every_mail() -> dict[str, Mail]:
    """One of each, with no database behind them.

    The digest and the alert are built from the same fixture
    `tests/test_mail.py` uses, so this sweep and that module's are about the
    same message.
    """
    link = "https://boxoutfantasy.com/auth/callback?token=a-token-that-goes-nowhere"
    return {
        "sign-in": sign_in_mail(link, public_url=SITE),
        "confirm": confirm_mail(link, public_url=SITE),
        "digest": digest_mail(_digest(), wanted=everything(FULL), public_url=SITE),
        "alert": alert_mail(
            "Through The Wire", "A Player is out", when="Wed 14 Jan, 09:00 UTC", public_url=SITE
        ),
        "league lines": lines_mail("Patriot Games", "9 moves", when="Wed 14 Jan", public_url=SITE),
    }


@pytest.mark.parametrize("which", sorted(every_mail()))
def test_no_mail_says_the_old_name(which: str) -> None:
    mail = every_mail()[which]
    for part, text in (("subject", mail.subject), ("html", mail.html), ("text", mail.text)):
        assert offences(text) == [], f"{which} {part}"


def test_the_account_mail_names_the_product() -> None:
    """The two mails a stranger reads before he has an account."""
    mails = every_mail()
    assert mails["sign-in"].subject == f"Your {brand.BRAND} sign-in link"
    assert mails["confirm"].subject == f"Confirm this address for {brand.BRAND} alerts"
    for which in ("sign-in", "confirm"):
        assert brand.BRAND in mails[which].html
        assert brand.BRAND in mails[which].text


def test_the_digest_says_whose_digest_it_is() -> None:
    """Its masthead is the league's and the team's; the line saying why it
    arrived is the product's, and that is the only place it names itself."""
    assert brand.BRAND in render.WHY_DIGEST
    assert brand.BRAND in every_mail()["digest"].html


# ---------------------------------------------------------------------------
# the landing page
# ---------------------------------------------------------------------------


@pytest.fixture
def landing(seeded: sessionmaker[Session]) -> Iterator[str]:  # noqa: F811
    """`/` with nobody signed in, which is accounts mode and no cookie."""
    built = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: accounts_settings()
    with TestClient(built) as client:
        page = client.get("/")
        assert page.status_code == 200
        yield page.text
    built.dependency_overrides.clear()


def test_the_landing_page_names_itself(landing: str) -> None:
    assert f"<title>{brand.BRAND}</title>" in landing
    assert brand.BRAND_TAGLINE in landing
    assert brand.BRAND_DOMAIN in landing
    assert offences(landing) == []


def test_the_landing_page_carries_the_measured_figures_and_their_sources(landing: str) -> None:
    """Every claim on the one page a stranger reads cites the note it is from."""
    for figure, source in (
        ("+0.16", "docs/pickups_backtest.md"),
        ("0.38", "docs/streaming_lane.md"),
        ("25 of 55", "docs/trades.md"),
    ):
        assert figure in landing, figure
        assert source in landing, source
    assert "616 team-decision points" in landing
    assert "1,536 team-periods" in landing


def test_the_landing_page_states_the_stance_and_the_terms(landing: str) -> None:
    assert "A tool, not gospel" in landing
    assert "you decide" in landing
    # What it needs, and the one promise about ESPN.
    assert "nine categories" in landing
    assert "Nothing here touches ESPN on your behalf." in landing
    assert "no lineup is ever submitted for you" in landing


def test_the_landing_page_offers_a_way_in_for_a_league_not_connected_yet(landing: str) -> None:
    assert 'href="/sign-in"' in landing
    assert "Sign in with your email" in landing
    assert "Ask whoever runs your league to connect" in landing
    assert "connect it yourself" in landing


def test_the_landing_page_reads_nobody_s_data(landing: str) -> None:
    assert "shell.js" not in landing
    assert "/auth/me" not in landing
    assert "SCREENSHOT SLOT" in landing, "the slot is marked rather than filled with a frame"
