"""Accounts mode, checked rather than assumed, before the site goes public.

docs/cutover.md is followed by hand and cannot check itself, so the things
the cutover depends on are checked here, against the running app in accounts
mode, over the league shape `tests/test_access.py` builds:

* **Every route** outside the handful that are open on purpose refuses a
  signed-out request -- 401 for the JSON, a redirect to sign in for a page.
  This walks the app rather than naming routes, so a route added without a
  check fails here the day it is added.
* **The cookie** is `Secure`, `HttpOnly`, `SameSite=Lax` -- and `Secure` when
  `FCP_PUBLIC_URL` is https even though the request behind Caddy is not.
* **A mailed link** is built on `FCP_PUBLIC_URL` and never on the `Host`
  header, which the caller writes.
* **Sign-in and the invite routes** are rate-limited.
* **`Authorization: Bearer <FCP_SERVICE_TOKEN>`** is the owner, which is how
  the scheduled scripts reach the API once cookies are enforced.

And `scripts/preflight_public.py`, which prints all of the above on the VPS,
is run here against settings built by hand, including the ones that should
fail it.
"""

from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import access
from app.api.deps import get_session
from app.config import get_settings
from app.main import create_app
from scripts import preflight_public
from tests.test_access import (  # noqa: F401
    LEAGUE_A,
    OWNER,
    SEASON,
    SERVICE_TOKEN,
    accounts_settings,
    request_link,
    seeded,
)

#: What answers a stranger. Every one of them either says nothing about
#: anybody (`/health`), is the way in (`/sign-in`, the two auth posts, the
#: callback, whose link is itself the credential), or carries no data
#: (`/`, the shared stylesheet and scripts).
OPEN = {
    "GET /",
    "GET /sign-in",
    "GET /health",
    "GET /auth/callback",
    "POST /auth/sign-in",
    "POST /auth/sign-out",
    "GET /pages/static/{name}",
}

#: A value for each path parameter, so a route can actually be asked for.
STANDS_FOR = {
    "league_id": str(LEAGUE_A),
    "season": str(SEASON),
    "team_id": "3",
    "player_id": "1",
    "set_id": "1",
    "connection_id": "1",
    "invite_id": "1",
    "claim_id": "1",
    "channel_id": "1",
    "token": "a-token-that-goes-nowhere",
    "name": "pages.css",
}


@pytest.fixture
def app(seeded: sessionmaker[Session]) -> Iterator[FastAPI]:  # noqa: F811
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


def walk(routes: Iterable[Any]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from walk(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from walk(route.routes)


def endpoints(app: FastAPI) -> list[tuple[str, str, str]]:
    """Every (method, template, askable path) the app serves."""
    out = []
    for route in walk(app.routes):
        asked = route.path
        for name, value in STANDS_FOR.items():
            asked = asked.replace("{" + name + "}", value)
        for method in sorted(route.methods or ()):
            out.append((method, route.path, asked))
    return out


# ---------------------------------------------------------------------------
# signed out
# ---------------------------------------------------------------------------


def test_every_route_but_the_open_ones_refuses_a_signed_out_request(
    anon: TestClient, app: FastAPI
) -> None:
    """A page sends the browser to sign in; everything else is a 401.

    Nothing here asserts a 200 on the open ones -- that is
    tests/test_access.py's -- only that no other route answers a stranger.
    """
    served = endpoints(app)
    assert len(served) >= 50, "the walk found the routes"
    leaked = []
    for method, template, asked in served:
        if f"{method} {template}" in OPEN:
            continue
        answer = anon.request(method, asked, follow_redirects=False)
        if answer.status_code == 401:
            continue
        if answer.status_code == 303 and answer.headers.get("location", "").startswith("/sign-in"):
            continue
        leaked.append(f"{method} {template} -> {answer.status_code}")
    assert leaked == []


def test_the_open_routes_are_exactly_the_ones_the_preflight_names() -> None:
    """The script a person runs on the VPS and this test cannot drift apart."""
    assert set(preflight_public.OPEN_ROUTES) == OPEN


# ---------------------------------------------------------------------------
# the cookie
# ---------------------------------------------------------------------------


def test_the_cookie_is_secure_behind_a_proxy_that_terminates_tls(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Caddy speaks https to the browser and http to the API on the tailnet.

    The request the API sees is http, so the `Secure` flag has to come from
    `FCP_PUBLIC_URL` being https, which is the case this covers:
    tests/test_access.py covers the plain and the https request.
    """
    app.dependency_overrides[get_settings] = lambda: accounts_settings(
        fcp_public_url="https://boxoutfantasy.com"
    )
    with TestClient(app) as client:
        link = request_link(client, caplog, "alice@example.com")
        header = client.get(link, follow_redirects=False).headers["set-cookie"].lower()
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header


# ---------------------------------------------------------------------------
# the mailed link
# ---------------------------------------------------------------------------


def test_a_link_is_built_on_the_public_url_and_not_on_the_host_header(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    app.dependency_overrides[get_settings] = lambda: accounts_settings(
        fcp_public_url="https://boxoutfantasy.com"
    )
    with TestClient(app, base_url="http://attacker.example") as client:
        link = request_link(client, caplog, "alice@example.com")
    assert link.startswith("https://boxoutfantasy.com/auth/callback?token=")
    assert "attacker.example" not in link


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------


def test_the_invite_routes_are_rate_limited(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    """A signed-in member cannot work through invite tokens all day.

    Twenty tries, then one every six seconds, shared by the two routes: the
    token is 256 random bits, so this is tidiness rather than the defence,
    but a public site should not answer an unbounded number of guesses.
    """
    with TestClient(app) as client:
        link = request_link(client, caplog, "alice@example.com")
        client.get(link, follow_redirects=False)
        answers = [client.get(f"/invites/token-{n}").status_code for n in range(22)]
    assert answers.count(404) == 20, "a token nobody issued is a 404 until the bucket is empty"
    assert answers[-1] == 429


def test_sign_in_is_still_rate_limited(anon: TestClient) -> None:
    """Named here too, because the cutover depends on it and the preflight says so."""
    answers = [
        anon.post("/auth/sign-in", json={"email": "zoe@example.com"}).status_code for _ in range(7)
    ]
    assert answers[:5] == [202] * 5
    assert answers[5:] == [429, 429]


# ---------------------------------------------------------------------------
# the service token
# ---------------------------------------------------------------------------


def test_the_scheduled_scripts_bearer_path_works(anon: TestClient) -> None:
    """What `scripts/warm_pages.py` sends when cookies are enforced."""
    bearer = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    me = anon.get("/auth/me", headers=bearer)
    assert me.status_code == 200
    assert me.json()["via"] == "service" and me.json()["email"] == OWNER
    assert anon.get(f"/l/{LEAGUE_A}/{SEASON}/team/3/week", headers=bearer).status_code == 200
    # Only the configured token; anything else is refused rather than
    # falling through to whoever the browser last was.
    assert anon.get("/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


# ---------------------------------------------------------------------------
# the preflight script
# ---------------------------------------------------------------------------


def ready(**changes: Any) -> Any:
    """Settings a green preflight would find on the VPS."""
    base = {
        "fcp_auth_mode": "accounts",
        "fcp_public_url": "https://boxoutfantasy.com",
        "fcp_owner_email": "pmcdowellthe3@example.com",
        "fcp_service_token": "a" * 43,
        "fcp_secrets_key": "0" * 42 + "==",
        "fcp_smtp_host": "smtp.example.test",
        "fcp_email_from": "Box Out <hello@mail.boxoutfantasy.com>",
        "fcp_smtp_password": "not printed",
        "fcp_api_url": "http://100.105.64.94:8001",
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


def states(settings: Any) -> dict[str, str]:
    return {check.name: check.state for check in preflight_public.run(settings)}


def test_the_preflight_passes_a_server_that_is_ready() -> None:
    checks = preflight_public.run(ready())
    failed = [check.name for check in checks if not check.ok]
    assert failed == []
    assert any(check.state == preflight_public.NOTE for check in checks), "the paywall note"
    assert "Ready." in preflight_public.report(checks)


@pytest.mark.parametrize(
    ("change", "fails"),
    [
        ({"fcp_auth_mode": "single"}, "FCP_AUTH_MODE"),
        ({"fcp_public_url": None}, "FCP_PUBLIC_URL"),
        ({"fcp_public_url": "http://boxoutfantasy.com"}, "FCP_PUBLIC_URL"),
        ({"fcp_owner_email": None}, "FCP_OWNER_EMAIL"),
        ({"fcp_service_token": None}, "FCP_SERVICE_TOKEN"),
        ({"fcp_secrets_key": None}, "FCP_SECRETS_KEY"),
        ({"fcp_smtp_host": None}, "SMTP"),
        ({"fcp_api_url": "https://boxoutfantasy.com"}, "FCP_API_URL"),
    ],
)
def test_the_preflight_fails_what_should_fail(change: dict[str, Any], fails: str) -> None:
    found = states(ready(**change))
    assert found[fails] == preflight_public.FAIL, change
    report = preflight_public.report(preflight_public.run(ready(**change)))
    assert "NOT ready" in report


def test_the_preflight_never_prints_a_secret() -> None:
    secret_token = "S3CRET-" + "x" * 40
    settings = ready(fcp_service_token=secret_token, fcp_smtp_password="hunter2")
    report = preflight_public.report(preflight_public.run(settings))
    assert secret_token not in report
    assert "hunter2" not in report
    assert str(settings.fcp_secrets_key) not in report
    assert "set, 47 characters" in report, "described, not shown"


def test_the_preflight_sees_an_unchecked_route() -> None:
    """The check that matters most, exercised by taking a check away."""
    assert states(ready())["every route checked"] == preflight_public.PASS
    kept = access.CHECKS
    try:
        access.CHECKS = ()  # type: ignore[assignment]
        assert states(ready())["every route checked"] == preflight_public.FAIL
    finally:
        access.CHECKS = kept  # type: ignore[assignment]
