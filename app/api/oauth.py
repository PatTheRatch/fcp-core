"""The OAuth front door: how an app that is not the owner's gets a token.

    GET  /.well-known/oauth-authorization-server   what this server supports
    POST /oauth/register                           an app registers itself
    GET  /oauth/authorize                          sign in, then Allow or Deny
    POST /oauth/consent                            the Allow button
    POST /oauth/token                              the code, for a `bo_` token
    POST /oauth/revoke                             end one

The database half is `app/oauth.py` and the whole story is docs/mcp.md.

WHY THE AUTHORIZATION SERVER IS HERE AND NOT ON THE MCP SERVER

Because the sign-in is here. An authorization server's only real job is to
be certain who is at the keyboard, and the thing that knows that is the
magic link and the `fcp_session` cookie -- both of them on this app. The MCP
server is the resource server: it advertises this site as its authorization
server (RFC 9728) and checks the bearers this site hands out
(`app/mcp/server.py`).

WHAT THE APP RECEIVES

`api_tokens.mint`'s own `bo_` token, named after the app, listed and revoked
on Account -> Connections like every other. There is no second kind of
token here, no second lifetime, and no second answer to "who may open what":
the token acts as the manager through `resolve_viewer`, exactly as the one
he pastes into a config file by hand does.

WHAT THIS REFUSES

PKCE S256 or nothing. An exactly-matching registered redirect URI or
nothing. A code once, within ten minutes, for the app it was issued to. And,
in single mode, the whole flow: single mode makes every request the owner,
so an authorization endpoint there would hand the owner's token to whoever
asked (`app/api/access.py`, "The two modes").
"""

import logging
from hmac import compare_digest
from html import escape
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import accounts, api_tokens, brand, oauth
from app.api.access import COOKIE, PageViewer, SettingsDep
from app.api.auth import TokenBucket
from app.api.deps import SessionDep
from app.config import Settings
from app.db.models import OAuthClient

log = logging.getLogger("fcp.oauth")

router = APIRouter(tags=["accounts"])

STATIC = Path(__file__).parent / "static"

#: Registrations from one address before it has to slow down: ten, then one a
#: minute, the shape `POST /auth/sign-in` uses. Registering is open by
#: design -- that is what dynamic registration means -- so the limit is what
#: stops a form that can be held down from filling a table.
REGISTER_LIMIT = 10

#: What a caller hears when the site has no public address configured. The
#: endpoints in the metadata are built on `FCP_PUBLIC_URL` and never on the
#: request's Host header, which the caller writes (docs/cutover.md).
NO_PUBLIC_URL = "this server has no public address configured, so it issues no tokens"

#: What the flow says in single mode, where every request is already the
#: owner and nobody signs in.
SINGLE_MODE = (
    "This server is in single mode, where every request is already its owner, "
    "so there is nobody to sign in and nothing to consent to. Set "
    "FCP_AUTH_MODE=accounts to let an app ask for a token of its own."
)

#: The sentence the consent page asks, in the words of what a token can and
#: cannot do. It is the promise `docs/mcp.md` makes, in front of the button
#: that acts on it.
CONSENT_ASKS = (
    "wants to read your leagues and your teams' plans — the same pages you read "
    "in the browser, and nothing more. It can never add, drop, bid or accept "
    "anything on ESPN."
)


# ---------------------------------------------------------------------------
# the pieces every endpoint needs
# ---------------------------------------------------------------------------


def issuer(settings: Settings) -> str | None:
    """This authorization server's name, from settings and never from Host.

    A metadata document built on the caller's own `Host` header would let
    somebody point an app at a server of his choosing and have it believe the
    answer came from us (docs/cutover.md's rule, and `app.api.auth._link`'s).
    """
    base = (settings.fcp_public_url or "").strip()
    return base.rstrip("/") or None


def _limits(request: Request) -> TokenBucket:
    """The app's registration limiter, made on first use so each app has its own."""
    state = request.app.state
    if not hasattr(state, "oauth_register_limits"):
        state.oauth_register_limits = TokenBucket(capacity=REGISTER_LIMIT, per_second=1 / 60)
    found: TokenBucket = state.oauth_register_limits
    return found


def _error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    """An OAuth error object, in the shape RFC 6749 section 5.2 names."""
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _back(redirect_uri: str, **params: str | None) -> RedirectResponse:
    """Back to the app, with whatever the spec says goes in the query.

    302 rather than 303: this is the user agent being handed an
    authorization response, which is what every OAuth client expects to see.
    """
    query = urlencode({key: value for key, value in params.items() if value is not None})
    joiner = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{joiner}{query}", status_code=302, headers={"Cache-Control": "no-store"}
    )


def csrf_for(request: Request) -> str:
    """A token for the consent form, tied to this browser's own session.

    The cookie is `SameSite=Lax`, so a cross-site POST does not carry it and
    cannot be signed in at all; this is the second lock on the one button on
    this site that gives something away. It is derived from the session
    rather than stored, so there is no table to expire and nothing for a
    signed-out browser to replay.
    """
    return accounts.hash_token((request.cookies.get(COOKIE) or "") + "|oauth-consent")


def _single_mode_page() -> HTMLResponse:
    return HTMLResponse(_shell("Not here", f"<p class='sub'>{escape(SINGLE_MODE)}</p>"), 400)


SHELL = """<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{brand}</title><link rel="stylesheet" href="/pages/static/pages.css">
<link rel="icon" href="/pages/static/favicon.svg" type="image/svg+xml"></head>
<body><main class="page"><header class="mast"><p class="eyebrow">{brand}</p>
<h1>{heading}</h1>{body}</header></main></body></html>
"""


def _shell(heading: str, body: str) -> str:
    """The one-line pages this module answers with: the same shell
    `app/api/auth.py` gives a bad link."""
    return SHELL.format(brand=escape(brand.BRAND), heading=escape(heading), body=body)


# ---------------------------------------------------------------------------
# what this server supports (RFC 8414)
# ---------------------------------------------------------------------------


@router.get(
    "/.well-known/oauth-authorization-server",
    include_in_schema=False,
    summary="What this authorization server supports",
)
def authorization_server_metadata(settings: SettingsDep) -> Response:
    """RFC 8414, at the address every client looks for it.

    This is the document `claude.ai` and ChatGPT read after the MCP server
    names this site as its authorization server, and it is the only place
    the endpoints are written down for a machine.
    """
    base = issuer(settings)
    if base is None:
        return _error("server_error", NO_PUBLIC_URL, status_code=503)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            # No refresh token is issued, so none is advertised: the access
            # token lasts until the manager revokes it (docs/mcp.md).
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": [oauth.CHALLENGE_METHOD],
            # Public clients only: PKCE carries the proof, so there is no
            # client secret to present here or at the revocation endpoint.
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": f"{base}/",
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# an app registers itself (RFC 7591)
# ---------------------------------------------------------------------------


class RegisterIn(BaseModel):
    """What an app sends. Everything else RFC 7591 allows is read and dropped:
    this server has nothing to do with a logo or a contact address."""

    redirect_uris: list[str] = Field(default_factory=list, max_length=oauth.MAX_REDIRECT_URIS * 2)
    client_name: str | None = Field(default=None, max_length=300)
    client_uri: str | None = Field(default=None, max_length=oauth.MAX_URI)


@router.post(
    "/oauth/register",
    include_in_schema=False,
    status_code=201,
    summary="Register an app that wants to read for a manager",
)
def register(body: RegisterIn, request: Request, session: SessionDep) -> Response:
    """Open by design, rate-limited by address, and it gives away nothing.

    A registration is a client id and the redirect URIs it may use. No
    secret is issued, because a public client's proof is PKCE; a row here
    lets nobody read anything until a manager has signed in and said yes.
    """
    ip = request.client.host if request.client else "unknown"
    if not _limits(request).allow(ip):
        return _error("invalid_request", "too many registrations; wait a minute", status_code=429)
    try:
        client = oauth.register_client(
            session,
            client_name=body.client_name,
            redirect_uris=[str(uri) for uri in body.redirect_uris],
            client_uri=body.client_uri,
        )
    except oauth.RegistrationError as no:
        return _error("invalid_redirect_uri", str(no))
    session.commit()
    log.info("registered oauth client %s (%s)", client.client_id, client.client_name)
    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_id_issued_at": int(client.created_at.timestamp()),
            "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris),
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


# ---------------------------------------------------------------------------
# the manager signs in and decides
# ---------------------------------------------------------------------------


def _asked(
    session: Session, client_id: str, redirect_uri: str | None
) -> tuple[OAuthClient, str] | HTMLResponse:
    """The app and the redirect URI this request may use, or a page saying no.

    An unknown client and a redirect URI it did not register are the two
    errors that must NOT be sent back to the app: there is nowhere trusted to
    send them, so they are answered to the person in front of the browser
    instead (RFC 6749 section 4.1.2.1).
    """
    client = oauth.client_for(session, client_id)
    if client is None:
        return HTMLResponse(
            _shell(
                "Not an app we know",
                "<p class='sub'>That app is not registered here. Remove the connection in "
                "your app and add it again.</p>",
            ),
            400,
        )
    allowed = oauth.registered_redirect(client, redirect_uri)
    if allowed is None:
        return HTMLResponse(
            _shell(
                "That is not its address",
                "<p class='sub'>The app asked us to send your answer somewhere it did not "
                "register. Nothing has been given to it.</p>",
            ),
            400,
        )
    return client, allowed


@router.get("/oauth/authorize", include_in_schema=False, summary="Ask a manager to allow an app")
def authorize(
    request: Request,
    viewer: PageViewer,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """The consent page, once whoever is here has signed in.

    Signed out, `PageViewer` raises before this runs and the browser is sent
    to `/sign-in?next=<this whole request>`, so the click in the email lands
    back on exactly this authorization request with its state and its
    challenge intact (`app.accounts.safe_next` keeps that `next` a local
    path, so the link cannot be turned into a redirect elsewhere).
    """
    if settings.fcp_auth_mode == "single":
        return _single_mode_page()
    if issuer(settings) is None:
        said = f"<p class='sub'>{escape(NO_PUBLIC_URL)}</p>"
        return HTMLResponse(_shell("Not set up", said), 503)

    query = request.query_params
    found = _asked(session, query.get("client_id", ""), query.get("redirect_uri"))
    if isinstance(found, HTMLResponse):
        return found
    client, redirect_uri = found
    state = query.get("state")

    # From here the app has a registered address, so every other refusal goes
    # back to it as an error response rather than to the reader as a page.
    if query.get("response_type") != "code":
        return _back(
            redirect_uri,
            error="unsupported_response_type",
            state=state,
            error_description="only the authorization code flow is served here",
            iss=issuer(settings),
        )
    challenge = query.get("code_challenge") or ""
    method = query.get("code_challenge_method") or oauth.CHALLENGE_METHOD
    if not challenge or method != oauth.CHALLENGE_METHOD:
        return _back(
            redirect_uri,
            error="invalid_request",
            state=state,
            error_description="PKCE with code_challenge_method=S256 is required",
            iss=issuer(settings),
        )

    return HTMLResponse(
        _consent_page(
            request,
            client=client,
            viewer_email=viewer.email,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
            resource=query.get("resource"),
        )
    )


def _consent_page(
    request: Request,
    *,
    client: OAuthClient,
    viewer_email: str,
    redirect_uri: str,
    state: str | None,
    code_challenge: str,
    resource: str | None,
) -> str:
    """The one page in this flow, in the house style, read from disk per
    request like every other page here.

    Everything the app said about itself is escaped: `client_name` is a
    string an app chose, and it is drawn in a heading and in a hidden field.
    """
    hidden = {
        "client_id": str(client.client_id),
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "csrf": csrf_for(request),
    }
    if state is not None:
        hidden["state"] = state
    if resource is not None:
        hidden["resource"] = resource
    fields = "\n".join(
        f'<input type="hidden" name="{escape(name, quote=True)}" '
        f'value="{escape(value, quote=True)}">'
        for name, value in hidden.items()
    )
    page = brand.fill((STATIC / "consent.html").read_text())
    for token, value in {
        "{{client_name}}": escape(client.client_name),
        "{{consent_asks}}": escape(CONSENT_ASKS),
        "{{signed_in_as}}": escape(viewer_email),
        "{{fields}}": fields,
    }.items():
        page = page.replace(token, value)
    return page


@router.post("/oauth/consent", include_in_schema=False, summary="Allow or deny an app")
def consent(
    request: Request,
    viewer: PageViewer,
    session: SessionDep,
    settings: SettingsDep,
    decision: Annotated[str, Form()] = "deny",
    client_id: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str, Form()] = "",
    code_challenge: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
    state: Annotated[str | None, Form()] = None,
    resource: Annotated[str | None, Form()] = None,
) -> Response:
    """The button. Allow issues one code; Deny issues nothing and says so.

    Nothing posted here is trusted: the app is looked up again, the redirect
    URI is matched against its registration again, and the code is bound to
    what this route found rather than to what the form said.
    """
    if settings.fcp_auth_mode == "single":
        return _single_mode_page()
    if not compare_digest(csrf, csrf_for(request)):
        # A form that did not come from this browser's own consent page.
        return HTMLResponse(
            _shell(
                "Ask again",
                "<p class='sub'>That form did not come from this browser. Start again from "
                "your app.</p>",
            ),
            400,
        )
    found = _asked(session, client_id, redirect_uri or None)
    if isinstance(found, HTMLResponse):
        return found
    client, allowed = found
    where = issuer(settings)

    if decision != "allow":
        log.info("user %s denied oauth client %s", viewer.user_id, client.client_id)
        return _back(
            allowed,
            error="access_denied",
            state=state,
            iss=where,
            error_description="the manager did not allow it",
        )
    if viewer.user_id is None or not code_challenge:
        return _back(
            allowed,
            error="server_error",
            state=state,
            iss=where,
            error_description="this account cannot be granted a token",
        )

    code = oauth.issue_code(
        session,
        client=client,
        user_id=viewer.user_id,
        redirect_uri=allowed,
        code_challenge=code_challenge,
        resource=resource,
    )
    session.commit()
    log.info("user %s allowed oauth client %s", viewer.user_id, client.client_id)
    return _back(allowed, code=code, state=state, iss=where)


# ---------------------------------------------------------------------------
# the code, for a token
# ---------------------------------------------------------------------------


@router.post("/oauth/token", include_in_schema=False, summary="Exchange a code for a token")
def token(
    session: SessionDep,
    grant_type: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str | None, Form()] = None,
    client_id: Annotated[str, Form()] = "",
    code_verifier: Annotated[str, Form()] = "",
) -> Response:
    """The one grant this server serves, and the token it hands back.

    The token is `api_tokens.mint`'s: a `bo_` string, returned in this
    response and nowhere else, with only its sha256 stored. It does not
    expire, so no `expires_in` is sent and no refresh token is issued -- it
    lasts until the manager revokes it on his Connections page, which is the
    lifetime a manager can actually see (docs/mcp.md, "Decisions").
    """
    if grant_type == "refresh_token":
        return _error(
            "unsupported_grant_type",
            "no refresh token is issued here: the access token lasts until it is revoked",
        )
    if grant_type != "authorization_code":
        return _error("unsupported_grant_type", "only the authorization_code grant is served here")

    client = oauth.client_for(session, client_id)
    if client is None:
        return _error("invalid_client", "that client is not registered here", status_code=401)

    spent = oauth.spend_code(session, code, client)
    if spent is None:
        # Expired, already spent, never issued, or another app's: one answer.
        session.rollback()
        return _error("invalid_grant", "that code cannot be exchanged")
    if redirect_uri is not None and redirect_uri != spent.redirect_uri:
        session.rollback()
        return _error("invalid_grant", "the redirect_uri is not the one the code was issued for")
    if not oauth.verifier_matches(code_verifier, spent.code_challenge):
        # The code is spent all the same: a failed verifier means somebody
        # who is not the app that started the flow is holding this code, and
        # it must not be left for a second try.
        session.commit()
        return _error("invalid_grant", "the code_verifier does not match the challenge")

    minted = api_tokens.mint(session, spent.user_id, oauth.token_name(client))
    session.commit()
    log.info(
        "oauth client %s given token %s for user %s",
        client.client_id,
        minted.token_id,
        spent.user_id,
    )
    body: dict[str, Any] = {"access_token": minted.token, "token_type": "Bearer"}
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/oauth/revoke", include_in_schema=False, summary="End a token an app was given")
def revoke(
    session: SessionDep,
    token: Annotated[str, Form()] = "",
    token_type_hint: Annotated[str | None, Form()] = None,
    client_id: Annotated[str, Form()] = "",
) -> Response:
    """RFC 7009. Always 200, whatever was sent.

    Holding the token is the credential, so no other proof is asked for and
    none would mean anything: a public client has no secret. A token that
    was never minted, one already revoked and one belonging to somebody else
    all answer the same, because an answer that told them apart would be a
    way to test tokens.
    """
    ended = api_tokens.revoke_presented(session, token.strip()) if token else False
    session.commit()
    if ended:
        log.info("a token was revoked at the request of client %s", client_id or "(unnamed)")
    return Response(status_code=200, headers={"Cache-Control": "no-store"})
