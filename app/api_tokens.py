"""A manager's own machine tokens: what a tool sends instead of a cookie.

The database half, kept apart from the API like `app.accounts` is, so the
MCP server (`app/mcp/`, docs/mcp.md) can resolve a token without importing
FastAPI. The routes are `app/api/tokens.py` and the page is Connections.

WHAT ONE IS

`secrets.token_urlsafe(32)` behind the prefix `bo_`, so a token found in a
config file is recognisably ours and a search for it finds every copy. Only
its sha256 is stored, exactly as a session cookie's is (`app.accounts`): a
copy of this table opens nothing. The token is shown once, when it is made,
and no route ever returns it again -- not even to the man who made it.

WHAT ONE IS FOR

It **is** its owner. A request carrying it goes through the same
dependencies every route declares (`app.api.access.resolve_viewer`), so it
sees his leagues and his teams' plans and nothing else: there is no scope on
a token, because a scope nobody can see is a promise nobody can check. A
token that could read more than the man who made it is the bug this design
exists to prevent.

The service token in `.env` stays what it was: the owner's machine token for
the scheduled scripts, configured rather than minted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.accounts import hash_token, new_token
from app.db.models import ApiToken, User

#: What every token starts with. Not a secret, and deliberately short: it is
#: there so a token pasted into a config file is recognisable as one of ours.
PREFIX = "bo_"

#: `last_used_at` is written at most this often, so a conversation of twenty
#: tool calls is not twenty writes.
USED_EVERY = timedelta(minutes=5)

#: A name long enough to say what a token is for and short enough to read.
MAX_NAME = 60

#: What a caller hears when the token is not one, is revoked, or was never
#: minted. One sentence, and the same one for all three: a token that does
#: not work should not be able to tell its holder which kind of dead it is.
NO_SUCH_TOKEN = "that token is not valid: make a new one at /account/connections"


def now() -> datetime:
    return datetime.now(UTC)


def looks_like_one(presented: str) -> bool:
    """Whether this bearer is a token of ours rather than the service token.

    Read before the hash is looked up, so a bearer that is plainly not ours
    is never a database round trip.
    """
    return presented.startswith(PREFIX)


@dataclass(frozen=True)
class Minted:
    """A freshly made token: the secret, once, and the row that records it."""

    token: str
    token_id: int
    name: str
    created_at: datetime


def mint(session: Session, user_id: int, name: str) -> Minted:
    """A new token for this user. Returns the secret; stores only its hash.

    Does not commit: the route does, so the row and nothing else is written
    if anything after this raises.
    """
    token = PREFIX + new_token()
    row = ApiToken(
        user_id=user_id,
        name=(name.strip() or "unnamed")[:MAX_NAME],
        token_hash=hash_token(token),
    )
    session.add(row)
    session.flush()
    return Minted(token=token, token_id=int(row.id), name=str(row.name), created_at=row.created_at)


def user_for_token(session: Session, presented: str) -> User | None:
    """The user a token belongs to, if it is live; touches last-used.

    A revoked token is None, and so is one that was never minted: the caller
    says the same sentence for both (`NO_SUCH_TOKEN`).
    """
    if not looks_like_one(presented):
        return None
    found = session.execute(
        select(ApiToken, User)
        .join(User, User.id == ApiToken.user_id)
        .where(ApiToken.token_hash == hash_token(presented), ApiToken.revoked_at.is_(None))
    ).first()
    if found is None:
        return None
    row: ApiToken = found[0]
    user: User = found[1]
    at = now()
    if row.last_used_at is None or row.last_used_at < at - USED_EVERY:
        row.last_used_at = at
        session.commit()
    return user


def listing(session: Session, user_id: int) -> list[ApiToken]:
    """This user's tokens, live ones first, newest first within each."""
    return list(
        session.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user_id)
            .order_by(ApiToken.revoked_at.is_not(None), ApiToken.created_at.desc())
        ).all()
    )


def revoke(session: Session, user_id: int, token_id: int) -> bool:
    """End one of this user's own tokens. True when there was a live one.

    Scoped to the user in the UPDATE itself, so no token of anyone else's can
    be revoked however the id was arrived at. Does not commit.
    """
    result = session.execute(
        update(ApiToken)
        .where(
            ApiToken.id == token_id,
            ApiToken.user_id == user_id,
            ApiToken.revoked_at.is_(None),
        )
        .values(revoked_at=now())
        .returning(ApiToken.id)
    ).first()
    return result is not None
