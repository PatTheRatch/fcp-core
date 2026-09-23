"""The OAuth front door, end to end, and the MCP server as a resource server.

The claim being tested is the one docs/mcp.md makes: **a league member adds
`https://mcp.boxoutfantasy.com` to his own Claude, signs in the way this site
signs anybody in, clicks Allow, and what his app is given is an ordinary
`bo_` token of his.** So the flow is driven the whole way here -- register,
authorize, consent, exchange -- and the token that comes out is then used as
a bearer on the site's own routes and on the co-manager's streamable-HTTP
form, and revoked on the account page.

The fixture is `tests/test_access.py`'s, deliberately, for the same reason
`tests/test_mcp_access.py` uses it: the token must open exactly what its
owner opens, so it is held to the leagues and claims those routes are held
to. Alice manages team 3 in league A; Bob manages team 5; Carol is in
league B.

The refusals get as much room as the happy path, because in an authorization
server the refusals are the product: a code spent twice, a verifier that does
not match, a redirect URI an app did not register, a form that came from
somewhere else, and a revoked token.
"""

import base64
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient as StarletteClient

import scripts.mcp_server as entry_point
from app import accounts, api_tokens, oauth
from app.api import oauth as oauth_routes
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.db.models import OAuthCode
from app.main import create_app
from app.mcp import tools
from app.mcp.scope import viewer_for_token
from app.mcp.server import (
    NoPublicUrlError,
    auth_settings,
    build_server,
    resource_url,
    transport_security,
)
from tests.test_access import (
    LEAGUE_A,
    OWNER,
    SEASON,
    SERVICE_TOKEN,
    seeded,  # noqa: F401  -- the fixture itself, reused as it stands
)

#: The site, and the co-manager's own address, as the VPS will set them.
SITE = "https://boxoutfantasy.com"
MCP = "https://mcp.boxoutfantasy.com"

#: Where an app in a browser is sent back to, and where a tool on a laptop is.
BROWSER_REDIRECT = "https://claude.ai/api/mcp/auth_callback"
LOOPBACK_REDIRECT = "http://127.0.0.1:33418/callback"

#: Where the co-manager actually binds. The SDK's DNS-rebinding guard wants a
#: host with a port, which is what a caller on the machine sends.
LOOPBACK = "http://127.0.0.1:8787"

ALICE = "alice@example.com"
BOB = "bob@example.com"


def site_settings(**changes: Any) -> Settings:
    """Accounts mode with a public address: the shape the cutover produces."""
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_owner_email": OWNER,
        "fcp_service_token": SERVICE_TOKEN,
        "fcp_public_url": SITE,
        "fcp_mcp_public_url": MCP,
        "fcp_smtp_host": None,
        "fcp_email_from": None,
        "espn_league_id": LEAGUE_A,
        "fcp_tracked_team_id": 3,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


@pytest.fixture
def session(seeded: sessionmaker[Session]) -> Iterator[Session]:  # noqa: F811
    with seeded() as open_session:
        yield open_session


def _app(seeded: sessionmaker[Session], **changes: Any) -> FastAPI:  # noqa: F811
    built = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: site_settings(**changes)
    return built


@pytest.fixture
def app(seeded: sessionmaker[Session]) -> Iterator[FastAPI]:  # noqa: F811
    built = _app(seeded)
    yield built
    built.dependency_overrides.clear()


def browser(app: FastAPI) -> TestClient:
    """A client at the site's own https address.

    Not `http://testserver`: the session cookie is `Secure` whenever
    `FCP_PUBLIC_URL` is https, and a browser does not send a Secure cookie
    over http -- so a test on the wrong scheme would look signed out for a
    reason that has nothing to do with what it is testing.
    """
    return TestClient(app, base_url=SITE)


@pytest.fixture
def anon(app: FastAPI) -> Iterator[TestClient]:
    with browser(app) as client:
        yield client


SignIn = Callable[[str], TestClient]


def _link(client: TestClient, caplog: pytest.LogCaptureFixture, email: str, next_path: str) -> str:
    """Ask for a sign-in link and read it out of the dev log, as a developer does.

    The link is built on `FCP_PUBLIC_URL`, so the host is stripped to leave
    the path this test client can ask for.
    """
    caplog.clear()
    caplog.set_level(logging.INFO, logger="fcp.auth")
    asked = client.post("/auth/sign-in", json={"email": email, "next": next_path})
    assert asked.status_code == 202, asked.text
    logged = [record.getMessage() for record in caplog.records if record.name == "fcp.auth"]
    assert logged, "the dev link was not logged"
    found = re.search(r"(https?\S+/auth/callback\?token=\S+)", logged[-1])
    assert found is not None
    assert found.group(1).startswith(SITE), "built on FCP_PUBLIC_URL, never on Host"
    return found.group(1).removeprefix(SITE)


@pytest.fixture
def sign_in(app: FastAPI, caplog: pytest.LogCaptureFixture) -> Iterator[SignIn]:
    """A signed-in client per email, through the real link and callback."""
    opened: list[TestClient] = []

    def make(email: str) -> TestClient:
        client = browser(app)
        opened.append(client)
        landed = client.get(_link(client, caplog, email, "/"), follow_redirects=False)
        assert landed.status_code == 303, landed.text
        return client

    yield make
    for client in opened:
        client.close()


# ---------------------------------------------------------------------------
# PKCE, and the flow as a helper
# ---------------------------------------------------------------------------


def tokens_of(session: Session, email: str) -> list[str]:
    """This manager's tokens by name, live ones first, newest first.

    Counted rather than compared to a fixed list: the database is built once
    per module, so every test in this file adds to the same account's tokens.
    """
    session.commit()  # a fresh snapshot: the rows were written by the app's session
    user = accounts.user_by_email(session, email)
    assert user is not None, email
    return [str(row.name) for row in api_tokens.listing(session, user.id)]


def pkce() -> tuple[str, str]:
    """A verifier and its S256 challenge, as an app generates them."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def register(client: TestClient, name: str = "Claude", uris: list[str] | None = None) -> str:
    made = client.post(
        "/oauth/register",
        json={"client_name": name, "redirect_uris": uris or [BROWSER_REDIRECT]},
    )
    assert made.status_code == 201, made.text
    return str(made.json()["client_id"])


def authorize_url(client_id: str, challenge: str, **extra: str) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": BROWSER_REDIRECT,
        "state": "the-apps-own-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **extra,
    }
    return "/oauth/authorize?" + "&".join(f"{key}={value}" for key, value in query.items())


def csrf_of(page: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    assert found is not None, "the consent form carries no token"
    return found.group(1)


def consent_form(page: str) -> dict[str, str]:
    """Every hidden field of the consent form, as a browser would post them."""
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', page))


def code_from(location: str) -> str:
    found = re.search(r"[?&]code=([^&]+)", location)
    assert found is not None, location
    return found.group(1)


def allowed(client: TestClient, client_id: str, challenge: str, **extra: str) -> str:
    """Sign-in already done: open the consent page and press Allow. The code."""
    page = client.get(authorize_url(client_id, challenge, **extra))
    assert page.status_code == 200, page.text
    fields = consent_form(page.text)
    said = client.post(
        "/oauth/consent", data={**fields, "decision": "allow"}, follow_redirects=False
    )
    assert said.status_code == 302, said.text
    return code_from(said.headers["location"])


def exchange(client: TestClient, client_id: str, code: str, verifier: str) -> Any:
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": BROWSER_REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )


def a_token_for(client: TestClient) -> tuple[str, str]:
    """The whole flow for an already-signed-in browser. The token and client id."""
    client_id = register(client)
    verifier, challenge = pkce()
    got = exchange(client, client_id, allowed(client, client_id, challenge), verifier)
    assert got.status_code == 200, got.text
    return str(got.json()["access_token"]), client_id


# ---------------------------------------------------------------------------
# what this server says it is (RFC 8414)
# ---------------------------------------------------------------------------


def test_the_metadata_names_the_endpoints_and_only_s256(anon: TestClient) -> None:
    said = anon.get("/.well-known/oauth-authorization-server")
    assert said.status_code == 200
    body = said.json()
    assert body["issuer"] == SITE, "an exact string, and no trailing slash"
    assert body["authorization_endpoint"] == f"{SITE}/oauth/authorize"
    assert body["token_endpoint"] == f"{SITE}/oauth/token"
    assert body["registration_endpoint"] == f"{SITE}/oauth/register"
    assert body["revocation_endpoint"] == f"{SITE}/oauth/revoke"
    assert body["code_challenge_methods_supported"] == ["S256"], "no `plain`, ever"
    assert body["response_types_supported"] == ["code"]
    # No refresh token is issued, so none is advertised (docs/mcp.md).
    assert body["grant_types_supported"] == ["authorization_code"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert body["authorization_response_iss_parameter_supported"] is True


def test_the_metadata_is_built_on_settings_and_never_on_the_host_header(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    """A document built on `Host` would let a caller name our own server for us."""
    built = _app(seeded)
    with TestClient(built, base_url="http://attacker.example") as client:
        body = client.get("/.well-known/oauth-authorization-server").json()
    built.dependency_overrides.clear()
    assert body["issuer"] == SITE
    assert "attacker.example" not in json.dumps(body)


def test_without_a_public_address_nothing_is_issued(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    built = _app(seeded, fcp_public_url=None)
    with browser(built) as client:
        assert client.get("/.well-known/oauth-authorization-server").status_code == 503
    built.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# an app registers itself (RFC 7591)
# ---------------------------------------------------------------------------


def test_an_app_registers_itself_and_is_given_no_secret(anon: TestClient, session: Session) -> None:
    made = anon.post(
        "/oauth/register",
        json={"client_name": "Claude", "redirect_uris": [BROWSER_REDIRECT, LOOPBACK_REDIRECT]},
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["client_id"].startswith(oauth.CLIENT_PREFIX)
    assert "client_secret" not in body, "a public client proves itself with PKCE"
    assert body["redirect_uris"] == [BROWSER_REDIRECT, LOOPBACK_REDIRECT]
    assert body["token_endpoint_auth_method"] == "none"

    stored = oauth.client_for(session, body["client_id"])
    assert stored is not None and stored.client_name == "Claude"


@pytest.mark.parametrize(
    "uris",
    [
        [],
        ["http://evil.example/callback"],
        ["https://claude.ai/cb#fragment"],
        ["ftp://claude.ai/cb"],
        ["not a url"],
        ["https://claude.ai/cb", "http://evil.example/cb"],
    ],
)
def test_a_redirect_uri_we_would_not_send_a_code_to_is_refused(
    anon: TestClient, uris: list[str]
) -> None:
    """https, or http on the loopback, and nothing else: a code delivered
    anywhere else is a code handed to somebody."""
    said = anon.post("/oauth/register", json={"client_name": "x", "redirect_uris": uris})
    assert said.status_code == 400, said.text
    assert said.json()["error"] == "invalid_redirect_uri"


def test_a_tool_on_a_laptop_may_register_a_loopback_address(anon: TestClient) -> None:
    for uri in (LOOPBACK_REDIRECT, "http://localhost:8765/callback"):
        assert anon.post("/oauth/register", json={"redirect_uris": [uri]}).status_code == 201


def test_registering_is_rate_limited(anon: TestClient) -> None:
    """Open by design, so the limit is what stops it filling a table."""
    tries = oauth_routes.REGISTER_LIMIT + 2
    codes = [
        anon.post("/oauth/register", json={"redirect_uris": [BROWSER_REDIRECT]}).status_code
        for _ in range(tries)
    ]
    assert codes.count(201) == oauth_routes.REGISTER_LIMIT
    assert codes[-1] == 429


# ---------------------------------------------------------------------------
# signing in, and the consent page
# ---------------------------------------------------------------------------


def test_authorize_sends_a_signed_out_browser_to_sign_in_and_back(
    anon: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole request comes back: its state and its challenge intact."""
    client_id = register(anon)
    _, challenge = pkce()
    asked = authorize_url(client_id, challenge)
    sent = anon.get(asked, follow_redirects=False)
    assert sent.status_code == 303
    where = sent.headers["location"]
    assert where.startswith("/sign-in?next=")
    assert "%3Fclient_id" in where or "client_id" in where

    with browser(anon.app) as signed_in:
        landed = signed_in.get(_link(signed_in, caplog, ALICE, asked), follow_redirects=False)
        assert landed.status_code == 303
        assert landed.headers["location"] == asked, "back on the authorization request"
        page = signed_in.get(asked)
    assert page.status_code == 200
    assert "Claude" in page.text
    assert "never add, drop, bid or accept" in page.text
    assert ALICE in page.text, "the page says who it is about to act as"


def test_the_consent_page_names_the_app_and_escapes_what_it_called_itself(
    sign_in: SignIn,
) -> None:
    alice = sign_in(ALICE)
    client_id = register(alice, name="<script>alert(1)</script>")
    _, challenge = pkce()
    page = alice.get(authorize_url(client_id, challenge))
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_an_app_we_do_not_know_is_answered_to_the_reader_not_redirected(
    sign_in: SignIn,
) -> None:
    """There is nowhere trusted to send this error, so nobody is redirected."""
    alice = sign_in(ALICE)
    _, challenge = pkce()
    said = alice.get(authorize_url("boc_never-registered", challenge), follow_redirects=False)
    assert said.status_code == 400
    assert "not registered here" in said.text


def test_a_redirect_uri_it_did_not_register_is_refused_to_the_reader(sign_in: SignIn) -> None:
    alice = sign_in(ALICE)
    client_id = register(alice)
    _, challenge = pkce()
    said = alice.get(
        authorize_url(client_id, challenge).replace(
            BROWSER_REDIRECT, "https://evil.example/collect"
        ),
        follow_redirects=False,
    )
    assert said.status_code == 400
    assert "did not register" in said.text
    assert "evil.example" not in said.headers.get("location", "")


def test_a_request_without_pkce_is_sent_back_to_the_app_as_an_error(sign_in: SignIn) -> None:
    alice = sign_in(ALICE)
    client_id = register(alice)
    said = alice.get(
        authorize_url(client_id, "").replace("&code_challenge=&", "&"), follow_redirects=False
    )
    assert said.status_code == 302
    where = said.headers["location"]
    assert where.startswith(BROWSER_REDIRECT)
    assert "error=invalid_request" in where
    assert "state=the-apps-own-state" in where


def test_plain_pkce_is_refused(sign_in: SignIn) -> None:
    alice = sign_in(ALICE)
    client_id = register(alice)
    verifier, _ = pkce()
    said = alice.get(
        authorize_url(client_id, verifier, code_challenge_method="plain"), follow_redirects=False
    )
    assert said.status_code == 302 and "error=invalid_request" in said.headers["location"]


def test_deny_issues_nothing_and_says_so(sign_in: SignIn, session: Session) -> None:
    alice = sign_in(ALICE)
    before = tokens_of(session, ALICE)
    client_id = register(alice)
    _, challenge = pkce()
    page = alice.get(authorize_url(client_id, challenge))
    said = alice.post(
        "/oauth/consent",
        data={**consent_form(page.text), "decision": "deny"},
        follow_redirects=False,
    )
    assert said.status_code == 302
    where = said.headers["location"]
    assert "error=access_denied" in where
    assert "code=" not in where
    assert "state=the-apps-own-state" in where

    assert tokens_of(session, ALICE) == before, "nothing was minted"


def test_the_consent_form_has_to_come_from_this_browsers_own_page(sign_in: SignIn) -> None:
    """SameSite=Lax already stops a cross-site POST carrying the cookie; this
    is the second lock on the one button that gives something away."""
    alice = sign_in(ALICE)
    client_id = register(alice)
    _, challenge = pkce()
    page = alice.get(authorize_url(client_id, challenge))
    forged = {**consent_form(page.text), "csrf": "not-this-browsers", "decision": "allow"}
    said = alice.post("/oauth/consent", data=forged, follow_redirects=False)
    assert said.status_code == 400
    assert "did not come from this browser" in said.text


def test_one_browsers_consent_token_is_not_anothers(sign_in: SignIn) -> None:
    alice, bob = sign_in(ALICE), sign_in(BOB)
    client_id = register(alice)
    _, challenge = pkce()
    hers = consent_form(alice.get(authorize_url(client_id, challenge)).text)
    his = consent_form(bob.get(authorize_url(client_id, challenge)).text)
    assert hers["csrf"] != his["csrf"]
    crossed = bob.post(
        "/oauth/consent",
        data={**his, "csrf": hers["csrf"], "decision": "allow"},
        follow_redirects=False,
    )
    assert crossed.status_code == 400


# ---------------------------------------------------------------------------
# the code, and the token it becomes
# ---------------------------------------------------------------------------


def test_allow_gives_a_code_that_becomes_one_of_the_managers_own_tokens(
    sign_in: SignIn, session: Session
) -> None:
    """The whole claim of this piece of work, in one test."""
    alice = sign_in(ALICE)
    client_id = register(alice)
    verifier, challenge = pkce()

    page = alice.get(authorize_url(client_id, challenge, resource=f"{MCP}/mcp"))
    said = alice.post(
        "/oauth/consent",
        data={**consent_form(page.text), "decision": "allow"},
        follow_redirects=False,
    )
    assert said.status_code == 302
    where = said.headers["location"]
    assert where.startswith(BROWSER_REDIRECT)
    assert "state=the-apps-own-state" in where
    # RFC 9207: the app checks that the answer came from the issuer it asked.
    assert f"iss={SITE.replace(':', '%3A').replace('/', '%2F')}" in where or f"iss={SITE}" in where

    got = exchange(alice, client_id, code_from(where), verifier)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].startswith(api_tokens.PREFIX), "it IS a bo_ token"
    assert "refresh_token" not in body, "none is issued; the token lasts until it is revoked"
    assert "expires_in" not in body
    assert got.headers["cache-control"] == "no-store"

    # The row it is: hers, named after the app, on her Connections page.
    assert tokens_of(session, ALICE)[0] == "Claude (OAuth)", "the newest is the one just made"
    holder = api_tokens.user_for_token(session, body["access_token"])
    assert holder is not None and holder.email == ALICE

    # And the listing the page draws shows it, with the app's name.
    listed = alice.get("/me/api-tokens").json()
    assert listed[0]["name"] == "Claude (OAuth)"
    assert all("token" not in row for row in listed), "never the secret, not even once more"


def test_the_connections_page_tells_the_two_kinds_of_token_apart(sign_in: SignIn) -> None:
    """The page draws its list from `/me/api-tokens`, so what is checked here
    is that the route carries the app's name and the page knows the mark."""
    alice = sign_in(ALICE)
    a_token_for(alice)
    assert any(row["name"] == "Claude (OAuth)" for row in alice.get("/me/api-tokens").json()), (
        "the app's own name, and where the token came from"
    )
    page = alice.get("/account/connections").text
    assert "via OAuth" in page
    assert '(OAuth)"' in page, "the page knows the mark the server writes"


def test_the_token_reads_exactly_what_its_manager_reads(sign_in: SignIn) -> None:
    """It is her, through the same checks: her leagues, her team's plan."""
    alice = sign_in(ALICE)
    token, _ = a_token_for(alice)
    with browser(alice.app) as app_client:
        headers = {"Authorization": f"Bearer {token}"}
        me = app_client.get("/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["email"] == ALICE and me.json()["via"] == "token"
        assert [
            row["espn_league_id"] for row in app_client.get("/leagues", headers=headers).json()
        ] == [LEAGUE_A]
        # Bob's team is not hers, over a token as in a browser.
        shut = app_client.get(
            f"/leagues/{LEAGUE_A}/seasons/{SEASON}/teams/5/pickups/stream", headers=headers
        )
        assert shut.status_code == 403
        assert shut.json()["detail"] == "This team's plan is its manager's."


def test_a_pkce_verifier_that_does_not_match_is_refused(sign_in: SignIn) -> None:
    alice = sign_in(ALICE)
    client_id = register(alice)
    _, challenge = pkce()
    other, _ = pkce()
    got = exchange(alice, client_id, allowed(alice, client_id, challenge), other)
    assert got.status_code == 400
    assert got.json()["error"] == "invalid_grant"


def test_a_code_cannot_be_spent_twice(sign_in: SignIn, session: Session) -> None:
    alice = sign_in(ALICE)
    before = tokens_of(session, ALICE)
    client_id = register(alice)
    verifier, challenge = pkce()
    code = allowed(alice, client_id, challenge)
    assert exchange(alice, client_id, code, verifier).status_code == 200
    again = exchange(alice, client_id, code, verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert len(tokens_of(session, ALICE)) == len(before) + 1, "one code, one token"


def test_a_failed_verifier_burns_the_code(sign_in: SignIn) -> None:
    """Somebody holding a code who cannot prove he started the flow has it
    taken away rather than left for a second try."""
    alice = sign_in(ALICE)
    client_id = register(alice)
    verifier, challenge = pkce()
    code = allowed(alice, client_id, challenge)
    wrong, _ = pkce()
    assert exchange(alice, client_id, code, wrong).status_code == 400
    assert exchange(alice, client_id, code, verifier).status_code == 400


def test_another_app_cannot_spend_this_apps_code(sign_in: SignIn) -> None:
    alice = sign_in(ALICE)
    mine = register(alice, name="Claude")
    theirs = register(alice, name="Someone else")
    verifier, challenge = pkce()
    code = allowed(alice, mine, challenge)
    stolen = exchange(alice, theirs, code, verifier)
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    assert exchange(alice, mine, code, verifier).status_code == 200, "still the real app's"


def test_an_expired_code_is_refused(sign_in: SignIn, session: Session) -> None:
    alice = sign_in(ALICE)
    client_id = register(alice)
    verifier, challenge = pkce()
    code = allowed(alice, client_id, challenge)
    session.commit()  # a fresh snapshot: the row was written by the app's session
    row = session.scalars(select(OAuthCode).order_by(OAuthCode.id.desc()).limit(1)).one()
    row.expires_at = oauth.now() - timedelta(seconds=1)
    session.commit()
    assert exchange(alice, client_id, code, verifier).status_code == 400


def test_no_refresh_token_grant_is_served_and_it_says_why(anon: TestClient) -> None:
    said = anon.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": "x", "client_id": "boc_x"},
    )
    assert said.status_code == 400
    assert said.json()["error"] == "unsupported_grant_type"
    assert "until it is revoked" in said.json()["error_description"]


def test_an_unregistered_client_at_the_token_endpoint(anon: TestClient) -> None:
    said = anon.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "anything",
            "client_id": "boc_nobody",
            "code_verifier": "x",
        },
    )
    assert said.status_code == 401 and said.json()["error"] == "invalid_client"


# ---------------------------------------------------------------------------
# ending one
# ---------------------------------------------------------------------------


def test_revoking_at_the_endpoint_stops_the_token(sign_in: SignIn) -> None:
    alice = sign_in(ALICE)
    token, client_id = a_token_for(alice)
    headers = {"Authorization": f"Bearer {token}"}
    with browser(alice.app) as app_client:
        assert app_client.get("/auth/me", headers=headers).status_code == 200
        ended = app_client.post("/oauth/revoke", data={"token": token, "client_id": client_id})
        assert ended.status_code == 200
        assert app_client.get("/auth/me", headers=headers).status_code == 401
        # RFC 7009: a token that was never minted answers the same 200.
        assert app_client.post("/oauth/revoke", data={"token": "bo_never"}).status_code == 200


def test_revoking_on_the_account_page_stops_it_too(sign_in: SignIn) -> None:
    """The Connections page is the lifetime a manager can actually see."""
    alice = sign_in(ALICE)
    token, _ = a_token_for(alice)
    listed = alice.get("/me/api-tokens").json()
    assert alice.delete(f"/me/api-tokens/{listed[0]['id']}").status_code == 200
    with browser(alice.app) as app_client:
        shut = app_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert shut.status_code == 401


# ---------------------------------------------------------------------------
# single mode: the flow is refused outright
# ---------------------------------------------------------------------------


def test_single_mode_refuses_the_whole_flow(seeded: sessionmaker[Session]) -> None:  # noqa: F811
    """Single mode makes every request the owner, so an authorization endpoint
    there would hand the owner's token to whoever asked."""
    built = _app(seeded, fcp_auth_mode="single")
    with browser(built) as client:
        client_id = register(client)
        _, challenge = pkce()
        said = client.get(authorize_url(client_id, challenge), follow_redirects=False)
        assert said.status_code == 400
        assert "single mode" in said.text
        posted = client.post("/oauth/consent", data={"decision": "allow"}, follow_redirects=False)
        assert posted.status_code == 400
    built.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# the MCP server as a resource server
# ---------------------------------------------------------------------------


def mcp_app(seeded: sessionmaker[Session], **changes: Any) -> Any:  # noqa: F811
    """The co-manager's streamable-HTTP form, with auth on, in process."""
    settings = site_settings(**changes)
    server = build_server(session_factory=seeded, settings=settings, auth=auth_settings(settings))
    return server.streamable_http_app(stateless_http=True)


def rpc(client: StarletteClient, method: str, params: Any, token: str | None) -> Any:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=headers,
    )


def result_of(answer: Any) -> dict[str, Any]:
    """The JSON a tool returned, out of whichever framing came back."""
    body = answer.text
    if body.startswith("event:") or body.startswith("data:"):
        body = next(line[5:] for line in body.splitlines() if line.startswith("data:"))
    message = json.loads(body)
    assert "error" not in message, message
    content = message["result"]["content"][0]
    return dict(json.loads(content["text"]))


def test_the_resource_names_its_authorization_server(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    """RFC 9728, at both addresses clients ask for it."""
    with StarletteClient(mcp_app(seeded), base_url=LOOPBACK) as client:
        for path in (
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource",
        ):
            body = client.get(path).json()
            assert body["resource"] == f"{MCP}/mcp", path
            assert body["authorization_servers"] == [SITE], path


def test_an_unauthenticated_call_is_401_with_the_header_the_spec_names(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    """This header is the whole discovery path: without it an app has no way
    to find the front door."""
    with StarletteClient(mcp_app(seeded), base_url=LOOPBACK) as client:
        shut = rpc(client, "tools/list", {}, token=None)
    assert shut.status_code == 401
    header = shut.headers["www-authenticate"]
    assert header.startswith("Bearer ")
    assert f'resource_metadata="{MCP}/.well-known/oauth-protected-resource/mcp"' in header


def test_the_public_name_is_allowed_through_the_proxy(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    """Caddy passes the browser's Host through, and the SDK's rebinding guard
    would otherwise answer 421 to every request behind it."""
    allowed_hosts = transport_security(site_settings()).allowed_hosts
    assert "mcp.boxoutfantasy.com" in allowed_hosts
    assert "127.0.0.1:*" in allowed_hosts


def test_a_token_from_the_oauth_flow_calls_a_tool_over_streamable_http(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
    sign_in: SignIn,
) -> None:
    """The end of the road: Allow, then a tool call, answering what the route
    answers -- the leagues this manager is a member of and no others."""
    alice = sign_in(ALICE)
    token, _ = a_token_for(alice)

    with StarletteClient(mcp_app(seeded), base_url=LOOPBACK) as client:
        answer = rpc(client, "tools/call", {"name": "my_leagues", "arguments": {}}, token)
    assert answer.status_code == 200, answer.text
    over_http = result_of(answer)

    # The same function the route and the in-process tool call, on the same
    # viewer: a tool and a page cannot be told two different things.
    viewer = viewer_for_token(session, site_settings(), token)
    direct = tools.my_leagues(session, viewer)
    assert over_http == json.loads(json.dumps(direct))
    assert over_http["as"] == ALICE and over_http["how"] == "token"
    assert [row["espn_league_id"] for row in over_http["leagues"]] == [LEAGUE_A]


def test_a_revoked_token_is_refused_by_the_resource_server(
    seeded: sessionmaker[Session],  # noqa: F811
    sign_in: SignIn,
) -> None:
    alice = sign_in(ALICE)
    token, client_id = a_token_for(alice)
    with StarletteClient(mcp_app(seeded), base_url=LOOPBACK) as client:
        assert rpc(client, "tools/list", {}, token).status_code == 200
    with browser(alice.app) as app_client:
        app_client.post("/oauth/revoke", data={"token": token, "client_id": client_id})
    with StarletteClient(mcp_app(seeded), base_url=LOOPBACK) as client:
        shut = rpc(client, "tools/list", {}, token)
    assert shut.status_code == 401
    assert "resource_metadata=" in shut.headers["www-authenticate"]


def test_with_auth_on_the_environments_token_is_not_a_fallback(
    seeded: sessionmaker[Session],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    """A public server must never act as the owner for an anonymous caller."""
    user = accounts.user_by_email(session, ALICE)
    assert user is not None
    minted = api_tokens.mint(session, user.id, "the owner's own")
    session.commit()
    monkeypatch.setenv("BOX_OUT_TOKEN", minted.token)
    with StarletteClient(mcp_app(seeded), base_url=LOOPBACK) as client:
        assert rpc(client, "tools/list", {}, token=None).status_code == 401


def test_the_http_server_refuses_to_start_without_its_public_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A misconfigured public server must not run open.

    Checked on the entry point as well as on the settings, because the whole
    point is that `mcp_server.py --http` exits rather than serving.
    """
    for missing in ({"fcp_mcp_public_url": None}, {"fcp_public_url": None}):
        with pytest.raises(NoPublicUrlError):
            auth_settings(site_settings(**missing))
    assert resource_url(site_settings(fcp_mcp_public_url=None)) is None
    assert resource_url(site_settings()) == f"{MCP}/mcp"

    monkeypatch.setattr(entry_point, "get_settings", lambda: site_settings(fcp_mcp_public_url=None))
    assert entry_point.main(["--http"]) == 2
    said = capsys.readouterr().err
    assert "FCP_MCP_PUBLIC_URL" in said
    assert "--no-auth" in said
