"""A manager's own machine tokens, made and ended on the account page.

    GET    /me/api-tokens              his own, never the secrets
    POST   /me/api-tokens  {name}      a new one: the token, shown this once
    DELETE /me/api-tokens/{token_id}   revoke one of his own

The database half is `app/api_tokens.py` and the whole story is docs/mcp.md.
A token is the man who made it: it goes through the same dependencies every
route declares, so it reads his leagues and his teams' plans and nothing
else. There is no scope to set, because a scope nobody can see is a promise
nobody can check.

The secret is in the response to the POST that made it and nowhere else --
not in the listing, not in a log, and not in the database, which keeps only
its sha256. A manager who loses one makes another and revokes the old.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import api_tokens
from app.api.access import CurrentUser
from app.api.auth import TokenBucket
from app.api.deps import SessionDep
from app.db.models import ApiToken

log = logging.getLogger("fcp.tokens")

router = APIRouter(tags=["accounts"])

#: Tokens a manager may mint before he has to slow down: ten, then one a
#: minute. Minting is cheap, but a form that can be held down is a form that
#: fills a table.
MINT_LIMIT = 10

NO_SUCH_TOKEN = "no token of yours has that id"


class ApiTokenOut(BaseModel):
    """One token as the page lists it. The secret is not here, ever."""

    id: int
    name: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None

    @property
    def live(self) -> bool:
        return self.revoked_at is None


class MintedOut(BaseModel):
    """The one answer that carries a token, and only this once."""

    id: int
    name: str
    created_at: str
    #: The token itself. Copy it now: no route will say it again.
    token: str
    #: What to do with it, in one line, because this is where a manager is.
    note: str


class MintIn(BaseModel):
    name: str = Field(default="", max_length=api_tokens.MAX_NAME)


MINT_NOTE = (
    "Copy this now: it is not shown again. Put it in the co-manager's "
    "environment as BOX_OUT_TOKEN (docs/mcp.md). It reads exactly what you "
    "read in the browser, and it can change nothing."
)


def _out(row: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=int(row.id),
        name=str(row.name),
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
    )


def _limits(request: Request) -> TokenBucket:
    """The app's minting limiter, made on first use so each app has its own."""
    state = request.app.state
    if not hasattr(state, "mint_limits"):
        state.mint_limits = TokenBucket(capacity=MINT_LIMIT, per_second=1 / 60)
    found: TokenBucket = state.mint_limits
    return found


@router.get("/me/api-tokens", summary="Your own machine tokens, without their secrets")
def list_api_tokens(viewer: CurrentUser, session: SessionDep) -> list[ApiTokenOut]:
    if viewer.user_id is None:
        return []
    return [_out(row) for row in api_tokens.listing(session, viewer.user_id)]


@router.post("/me/api-tokens", status_code=201, summary="Make a token, shown this once")
def mint_api_token(
    body: MintIn, request: Request, viewer: CurrentUser, session: SessionDep
) -> MintedOut:
    if viewer.user_id is None:
        raise HTTPException(status_code=503, detail="accounts are not set up on this server")
    if not _limits(request).allow(str(viewer.user_id)):
        raise HTTPException(status_code=429, detail="too many tokens at once; wait a minute")
    minted = api_tokens.mint(session, viewer.user_id, body.name)
    session.commit()
    # The id and the name only: the token is in the response and nowhere else.
    log.info("user %s minted api token %s (%s)", viewer.user_id, minted.token_id, minted.name)
    return MintedOut(
        id=minted.token_id,
        name=minted.name,
        created_at=minted.created_at.isoformat(),
        token=minted.token,
        note=MINT_NOTE,
    )


@router.delete("/me/api-tokens/{token_id}", summary="Revoke one of your own tokens")
def revoke_api_token(token_id: int, viewer: CurrentUser, session: SessionDep) -> ApiTokenOut:
    if viewer.user_id is None or not api_tokens.revoke(session, viewer.user_id, token_id):
        # Someone else's token, and one that was never his, answer the same:
        # nothing here says whose ids exist.
        raise HTTPException(status_code=404, detail=NO_SUCH_TOKEN)
    session.commit()
    row = next(
        (each for each in api_tokens.listing(session, viewer.user_id) if int(each.id) == token_id),
        None,
    )
    if row is None:  # pragma: no cover - it was just revoked, so it is there
        raise HTTPException(status_code=404, detail=NO_SUCH_TOKEN)
    return _out(row)
