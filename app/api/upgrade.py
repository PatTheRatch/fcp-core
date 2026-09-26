"""The season pass over HTTP: the upgrade page, redeeming a code, the owner's codes.

    GET  /upgrade                          the page (signed in): what the team layer is,
                                           your pass, "Have a code?", the free tier
    GET  /billing/pass                     the page's context: your pass, the switch,
                                           whether purchase is open
    POST /billing/redeem  {code}           spend a code on your season pass
    GET  /billing/codes                    (site owner) every code, and who redeemed it
    POST /billing/codes   {note, uses, valid_until?, redeem_by?}
                                           (site owner) make one; the code is in the answer
    POST /billing/codes/{code_id}/revoke   (site owner) it redeems nothing more

docs/accounts.md, "The pass" and "Codes", is the story; `app/billing.py` does
the work, and the owner's command line (`scripts/comp_code.py`) calls the
same functions. No payment provider is here: `billing.purchase_available()`
is False, and the page draws the purchase button disabled with its reason.

Redeeming is rate-limited the way signing in is: per account and per client
address, in memory (`app.api.auth.TokenBucket`). Every attempt is logged,
with why a refusal was one; the answer is one sentence that never says which
part of a code was wrong.
"""

import logging
from datetime import date, datetime
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import accounts, billing, brand
from app.api.access import (
    SIGNED_IN_PAGE,
    CurrentUser,
    SettingsDep,
    SiteOwner,
    Viewer,
    is_entitled,
)
from app.api.auth import TokenBucket
from app.api.deps import SessionDep
from app.db.models import Entitlement

log = logging.getLogger("fcp.billing")

router = APIRouter(tags=["billing"])

STATIC = FilePath(__file__).parent / "static"

NO_ACCOUNT = "accounts are not set up on this server yet"
TOO_MANY = "Too many codes tried. Wait a minute and try again."


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------


class RedeemLimits:
    """Per account: five codes, then one a minute. Per client address: twenty,
    then one every ten seconds. Both have to allow a try; both are charged.

    Sign-in's numbers, because it is the same question: someone working
    through guesses. A code is 60 random bits, which is the real defence."""

    def __init__(self) -> None:
        self.by_user = TokenBucket(capacity=5, per_second=1 / 60)
        self.by_ip = TokenBucket(capacity=20, per_second=1 / 10)

    def allow(self, user_id: int, ip: str) -> bool:
        user_ok = self.by_user.allow(str(user_id))
        ip_ok = self.by_ip.allow(ip)
        return user_ok and ip_ok


def _limits(request: Request) -> RedeemLimits:
    state = request.app.state
    if not hasattr(state, "redeem_limits"):
        state.redeem_limits = RedeemLimits()
    found: RedeemLimits = state.redeem_limits
    return found


def _user_id(viewer: Viewer) -> int:
    """Only single mode on a database the accounts migrations have not reached
    has no account, and nothing here can be written then."""
    if viewer.user_id is None:
        raise HTTPException(status_code=503, detail=NO_ACCOUNT)
    return viewer.user_id


# ---------------------------------------------------------------------------
# the page, and what it reads
# ---------------------------------------------------------------------------


@router.get(
    "/upgrade", include_in_schema=False, response_class=HTMLResponse, dependencies=[SIGNED_IN_PAGE]
)
def upgrade_page() -> HTMLResponse:
    """The team layer, your pass, a code, and the free tier. Where a team
    page's 402 sends a browser, with `?next=` the page it asked for. Read per
    request, like the other pages, so an edit shows on a refresh."""
    return HTMLResponse(brand.fill((STATIC / "upgrade.html").read_text()))


class PassOut(BaseModel):
    until: str | None = Field(description="When it ends, ISO; null: never")
    until_words: str = Field(description="The same, as the page writes it")
    source: str
    source_words: str = Field(description="'a code', 'your own', 'purchased'")


class PassStateOut(BaseModel):
    billing_enabled: bool = Field(description="Whether the team layer is gated at all")
    entitled: bool = Field(description="Whether this viewer opens the team layer now")
    live: PassOut | None = Field(description="His live pass, if he has one")
    lapsed: PassOut | None = Field(description="With none live, the last one he had")
    purchase_available: bool
    purchase_note: str | None = Field(description="Why purchase is closed, while it is")
    site_owner: bool = Field(description="Whether he makes the codes (Connections)")


def _pass_out(held: Entitlement) -> PassOut:
    return PassOut(
        until=held.valid_until.isoformat() if held.valid_until else None,
        until_words=billing.says_until(held.valid_until),
        source=held.source,
        source_words=billing.SOURCE_WORDS.get(held.source, held.source),
    )


@router.get("/billing/pass", summary="Your season pass, and whether the team layer is gated")
def my_pass(viewer: CurrentUser, session: SessionDep, settings: SettingsDep) -> PassStateOut:
    live = accounts.active_entitlement(session, viewer.user_id) if viewer.user_id else None
    lapsed = None
    if live is None and viewer.user_id is not None:
        lapsed = billing.latest_pass(session, viewer.user_id)
    purchase = billing.purchase_available()
    return PassStateOut(
        billing_enabled=bool(settings.fcp_billing_enabled),
        entitled=is_entitled(session, viewer, settings),
        live=_pass_out(live) if live is not None else None,
        lapsed=_pass_out(lapsed) if lapsed is not None else None,
        purchase_available=purchase,
        purchase_note=None if purchase else billing.PURCHASE_CLOSED,
        site_owner=viewer.is_owner,
    )


# ---------------------------------------------------------------------------
# redeeming
# ---------------------------------------------------------------------------


class RedeemIn(BaseModel):
    code: str = Field(max_length=64)


class RedeemOut(BaseModel):
    detail: str
    live: PassOut


@router.post("/billing/redeem", summary="Spend a code on your season pass")
def redeem(body: RedeemIn, request: Request, viewer: CurrentUser, session: SessionDep) -> RedeemOut:
    user_id = _user_id(viewer)
    ip = request.client.host if request.client else "unknown"
    if not _limits(request).allow(user_id, ip):
        log.info("redeem by user %s from %s: rate-limited", user_id, ip)
        raise HTTPException(status_code=429, detail=TOO_MANY)
    try:
        held = billing.redeem(session, user_id, body.code)
    except billing.RedeemRefusedError as no:
        session.rollback()
        log.info("redeem by user %s from %s refused: %s", user_id, ip, no.why)
        raise HTTPException(status_code=422, detail=no.sentence) from None
    session.commit()
    until = billing.says_until(held.valid_until)
    return RedeemOut(detail=f"Your pass runs until {until}.", live=_pass_out(held))


# ---------------------------------------------------------------------------
# the owner's codes
# ---------------------------------------------------------------------------


class RedemptionOut(BaseModel):
    email: str
    redeemed_at: str


class CodeOut(BaseModel):
    id: int
    code: str
    note: str
    uses_total: int
    uses_left: int
    valid_until: str
    valid_until_words: str
    redeem_by: str | None
    revoked_at: str | None
    created_at: str
    state: str = Field(description="open, spent, revoked or expired")
    redeemed: list[RedemptionOut]


def _code_out(view: billing.CodeView) -> CodeOut:
    iso = datetime.isoformat
    return CodeOut(
        id=view.id,
        code=view.code,
        note=view.note,
        uses_total=view.uses_total,
        uses_left=view.uses_left,
        valid_until=iso(view.valid_until),
        valid_until_words=billing.says_until(view.valid_until),
        redeem_by=iso(view.redeem_by) if view.redeem_by else None,
        revoked_at=iso(view.revoked_at) if view.revoked_at else None,
        created_at=iso(view.created_at),
        state=view.state,
        redeemed=[
            RedemptionOut(email=r.email, redeemed_at=iso(r.redeemed_at)) for r in view.redeemed
        ],
    )


class MakeCodeIn(BaseModel):
    note: str = Field(default="", max_length=billing.MAX_NOTE)
    uses: int = Field(default=1)
    #: The pass's last day. Left out: the newest season's playoffs plus a
    #: month (`billing.default_valid_until`).
    valid_until: date | None = None
    #: The code's own last day to be redeemed. Left out: until it is spent.
    redeem_by: date | None = None


class MadeCodeOut(CodeOut):
    default_valid_until: bool = Field(description="Whether the pass's end was the default")


@router.get("/billing/codes", summary="Every comp code, and who redeemed it (site owner)")
def list_codes(owner: SiteOwner, session: SessionDep) -> list[CodeOut]:
    return [_code_out(view) for view in billing.list_codes(session)]


@router.post("/billing/codes", summary="Make a comp code (site owner)")
def make_code(body: MakeCodeIn, owner: SiteOwner, session: SessionDep) -> MadeCodeOut:
    try:
        made = billing.make_code(
            session,
            owner.user_id,
            note=body.note,
            uses=body.uses,
            valid_until=billing.end_of_day(body.valid_until) if body.valid_until else None,
            redeem_by=billing.end_of_day(body.redeem_by) if body.redeem_by else None,
        )
    except billing.CodeError as no:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(no)) from None
    session.commit()
    view = next(v for v in billing.list_codes(session) if v.id == made.id)
    return MadeCodeOut(**_code_out(view).model_dump(), default_valid_until=body.valid_until is None)


@router.post("/billing/codes/{code_id}/revoke", summary="Revoke a comp code (site owner)")
def revoke_code(
    code_id: Annotated[int, Path(ge=1)], owner: SiteOwner, session: SessionDep
) -> dict[str, bool]:
    revoked = billing.revoke(session, code_id)
    session.commit()
    return {"revoked": revoked}
