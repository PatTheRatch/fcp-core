"""The OAuth front door's database half: the apps, and the codes they spend.

Kept apart from the API like `app.accounts` and `app.api_tokens` are, so the
rules can be read and tested without FastAPI in the room. The routes are
`app/api/oauth.py` and the whole story is docs/mcp.md.

WHAT THIS IS FOR

The owner adds the co-manager to his own Claude by minting a `bo_` token on
Account -> Connections and pasting it into a config file. A league member
cannot be asked to do that. He pastes `https://mcp.boxoutfantasy.com` into
his app's connector settings, is sent here to sign in the way this site
signs anybody in, reads what the app is asking for, and clicks Allow.

**What the app receives is a `bo_` token.** Not a token of a second kind
with a second lifetime and a second answer to "who may open what": the same
row in `api_tokens` the owner mints by hand, named after the app that asked,
listed on his Connections page beside the others, and revoked there. OAuth
here is a front door onto the tokens that already exist, and nothing else.

WHAT CARRIES THE PROOF

There is no client secret. A public client -- which every app that registers
itself is -- proves it is the same one that started the flow with PKCE
(RFC 7636, S256 only): it sends the hash of a secret at `/oauth/authorize`
and the secret itself at `/oauth/token`. A stolen authorization code is
worth nothing without the verifier, and a copy of `oauth_clients` is worth
nothing at all.

So the things this module is strict about are the ones that carry the whole
proof: the redirect URI is matched exactly and is `https://` or a loopback
address, the challenge is S256, the code lives ten minutes and is spent once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import api_tokens
from app.accounts import hash_token, new_token
from app.db.models import OAuthClient, OAuthCode

#: What every client id starts with, so one found in an app's stored
#: configuration is recognisably ours. Not a secret.
CLIENT_PREFIX = "boc_"

#: How long a manager has between clicking Allow and his app spending the
#: code. Ten minutes is the ceiling the OAuth 2.1 draft names; the exchange
#: takes a second, and anything longer is a window for a code that leaked
#: through a log or a referrer.
CODE_TTL = timedelta(minutes=10)

#: A name long enough to say which app this is and short enough for a line.
MAX_CLIENT_NAME = 60

#: More than an app could want, and few enough that registering cannot be
#: used to store things here.
MAX_REDIRECT_URIS = 8
MAX_URI = 500

#: The only PKCE method. `plain` is not implemented, not advertised, and not
#: accepted: it proves nothing an eavesdropper could not also produce.
CHALLENGE_METHOD = "S256"

#: The hosts an `http://` redirect URI may name. A native app cannot get a
#: certificate for the loopback listener it opens, so the spec allows these
#: two and nothing else (RFC 8252).
LOOPBACK = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})

#: What a badly registered app hears. One sentence each, and each names the
#: rule rather than the value it refused.
NO_REDIRECT_URI = "a redirect_uri is required"
BAD_REDIRECT_URI = (
    "every redirect_uri must be https, or http on localhost or 127.0.0.1, with no fragment"
)
TOO_MANY_REDIRECT_URIS = f"at most {MAX_REDIRECT_URIS} redirect URIs"
UNNAMED = "an app"


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# the apps
# ---------------------------------------------------------------------------


def valid_redirect_uri(raw: str) -> bool:
    """Whether this is a redirect URI we will send a manager's code to.

    `https://` anywhere, or `http://` on the loopback for a tool running on
    the reader's own machine (RFC 8252 section 7.3). No fragment, because the
    query is appended to it; nothing else, because a code delivered to
    anything else is a code handed to somebody.
    """
    if not raw or len(raw) > MAX_URI:
        return False
    parts = urlsplit(raw)
    if parts.fragment or any(ord(c) < 32 for c in raw):
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    if parts.scheme == "http":
        return (parts.hostname or "") in LOOPBACK
    return False


def clean_name(raw: str | None) -> str:
    """The app's own name, trimmed to a line. Never empty: the consent page
    has to call it something, and "an app" is honest about knowing nothing."""
    name = (raw or "").strip()
    return name[:MAX_CLIENT_NAME] if name else UNNAMED


class RegistrationError(Exception):
    """A registration refused, with the one sentence that says why."""


def register_client(
    session: Session,
    *,
    client_name: str | None,
    redirect_uris: list[str],
    client_uri: str | None = None,
) -> OAuthClient:
    """Record an app that wants to ask managers for tokens. Does not commit.

    Every registration is a new row: nothing here is ever updated by a later
    caller, because a caller holding no secret has proved nothing that would
    entitle it to change an existing app's redirect URIs.
    """
    if not redirect_uris:
        raise RegistrationError(NO_REDIRECT_URI)
    if len(redirect_uris) > MAX_REDIRECT_URIS:
        raise RegistrationError(TOO_MANY_REDIRECT_URIS)
    for uri in redirect_uris:
        if not valid_redirect_uri(uri):
            raise RegistrationError(BAD_REDIRECT_URI)
    row = OAuthClient(
        client_id=CLIENT_PREFIX + new_token(),
        client_name=clean_name(client_name),
        client_uri=(client_uri or None),
        redirect_uris=list(redirect_uris),
    )
    session.add(row)
    session.flush()
    return row


def client_for(session: Session, client_id: str) -> OAuthClient | None:
    """The app with this id, or None. An unknown id and a malformed one are
    the same answer."""
    if not client_id or not client_id.startswith(CLIENT_PREFIX):
        return None
    return session.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))


def registered_redirect(client: OAuthClient, asked: str | None) -> str | None:
    """The redirect URI this request may use, or None.

    Exact string match against what the app registered -- never a prefix,
    never a host comparison, because "starts with" is how an open redirect
    is built. With nothing asked for and exactly one registered, that one is
    it, which is what the spec allows and what most apps do.
    """
    registered = [str(uri) for uri in (client.redirect_uris or [])]
    if asked is None:
        return registered[0] if len(registered) == 1 else None
    return asked if asked in registered else None


# ---------------------------------------------------------------------------
# the codes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spent:
    """A code that has just been exchanged, and everything it was bound to."""

    client_row_id: int
    user_id: int
    redirect_uri: str
    code_challenge: str
    resource: str | None


def issue_code(
    session: Session,
    *,
    client: OAuthClient,
    user_id: int,
    redirect_uri: str,
    code_challenge: str,
    resource: str | None,
) -> str:
    """One authorization code, returned once; only its hash is stored.

    Bound to all five things the token endpoint will check it against, so a
    code issued for one app, one redirect and one PKCE challenge cannot be
    spent as another's. Does not commit.
    """
    code = new_token()
    session.add(
        OAuthCode(
            code_hash=hash_token(code),
            client_row_id=int(client.id),
            user_id=user_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            resource=resource,
            expires_at=now() + CODE_TTL,
        )
    )
    client.last_used_at = now()
    session.flush()
    return code


def spend_code(session: Session, code: str, client: OAuthClient) -> Spent | None:
    """Consume a code for this app, or None if there is nothing to consume.

    One UPDATE that matches only an unused, unexpired row belonging to this
    client, so two requests racing on the same code cannot both come back
    with one -- the second matches nothing, exactly as a sign-in link's
    second click does (`app.accounts.redeem_sign_in_token`). Does not commit.

    Expired, spent, never issued and issued to another app all answer None:
    a code that does not work should not say which kind of dead it is.
    """
    if not code:
        return None
    row = session.execute(
        update(OAuthCode)
        .where(
            OAuthCode.code_hash == hash_token(code),
            OAuthCode.client_row_id == int(client.id),
            OAuthCode.used_at.is_(None),
            OAuthCode.expires_at > now(),
        )
        .values(used_at=now())
        .returning(
            OAuthCode.client_row_id,
            OAuthCode.user_id,
            OAuthCode.redirect_uri,
            OAuthCode.code_challenge,
            OAuthCode.resource,
        )
    ).first()
    if row is None:
        return None
    return Spent(
        client_row_id=int(row.client_row_id),
        user_id=int(row.user_id),
        redirect_uri=str(row.redirect_uri),
        code_challenge=str(row.code_challenge),
        resource=str(row.resource) if row.resource else None,
    )


def verifier_matches(verifier: str, challenge: str) -> bool:
    """PKCE S256: does this verifier hash to the challenge the code carries?

    Compared in constant time, because the comparison is the whole proof
    that the app finishing the flow is the one that started it.
    """
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return hmac.compare_digest(computed, challenge)


#: What is added to an app's name to make the token's, so a manager reading
#: his Connections page can tell a token an app asked for from one he pasted
#: into a config file himself.
VIA_OAUTH = " (OAuth)"


def token_name(client: OAuthClient) -> str:
    """What the token this app is given is called on the Connections page.

    The app's own name, cut short enough that the `(OAuth)` survives
    `api_tokens.mint`'s own trim: the suffix is the part a manager scanning
    the list is reading for.
    """
    room = api_tokens.MAX_NAME - len(VIA_OAUTH)
    return clean_name(client.client_name)[:room] + VIA_OAUTH
