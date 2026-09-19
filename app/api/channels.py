"""A member's own alert channels: add, verify, list, disable.

Step 4 of docs/product.md. The database half is `app.channels`; the page is
Account, Alerts (`/account/alerts`, app/api/site.py); docs/accounts.md has
each route's scope, all of them "signed in", about the caller's own rows.

    GET    /me/channels                  his channels, masked, and which kinds can be added
    POST   /me/channels   {kind, target} add one; a link or a code goes to it
    POST   /me/channels/verify  {token}  the link's token or the code, spent
    DELETE /me/channels/{channel_id}     disable one, and wipe its target

VERIFYING

Nothing is sent to a channel until he proves it is his. An email address
gets a link (`/account/alerts?token=...`, built on `FCP_PUBLIC_URL` only,
never on the request's Host header), which the page spends. A Telegram chat
or an ntfy topic gets a test message with a code, which he types on the
page. Without SMTP (development, the tests) the link is logged at INFO on
`fcp.channels` rather than mailed, as the sign-in link is.

SECRETS

A target is sealed before it is stored and never comes back: every answer
carries `masked` ("p•••@example.com"). The body is read by hand rather than
by pydantic's constraints, whose 422 repeats the value it refused. A failed
send is logged and answered by the exception's class, never its text, which
for Telegram quotes the bot's URL and with it the bot's token.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import channels, secrets_box
from app.api.access import CurrentUser, SettingsDep, Viewer
from app.api.auth import TokenBucket
from app.api.deps import SessionDep
from app.config import Settings
from app.db.models import NotificationChannel

log = logging.getLogger("fcp.channels")

router = APIRouter(tags=["alerts"])

NO_KEY = "Alert channels are not set up on this server yet (no secrets key)."
NO_ACCOUNT = "accounts are not set up on this server yet"
NOT_AVAILABLE = {
    channels.EMAIL: "email is not set up on this server (SMTP)",
    channels.TELEGRAM: "Telegram is not set up on this server (no bot)",
    channels.NTFY: NO_KEY,
}
NO_LINK = "an email channel needs FCP_PUBLIC_URL, so its link can be built"
NOT_SENT = "the {kind} message could not be sent, so the channel was not added"
BAD_SECRET = "That link or code is not valid, or has expired. Add the channel again for a new one."
TOO_MANY = f"at most {channels.MAX_CHANNELS} channels at once; disable one first"
SUBJECT = "Confirm this address for FCP alerts"
TITLE = "FCP: confirm this channel"


class ChannelLimits:
    """Per user: five adds (each one sends a message), then one a minute;
    ten verification tries, then one a minute. In memory, like sign-in's."""

    def __init__(self) -> None:
        self.add = TokenBucket(capacity=5, per_second=1 / 60)
        self.verify = TokenBucket(capacity=10, per_second=1 / 60)


def _limits(request: Request) -> ChannelLimits:
    state = request.app.state
    if not hasattr(state, "channel_limits"):
        state.channel_limits = ChannelLimits()
    found: ChannelLimits = state.channel_limits
    return found


def _user_id(viewer: Viewer) -> int:
    if viewer.user_id is None:
        raise HTTPException(status_code=503, detail=NO_ACCOUNT)
    return viewer.user_id


class ChannelOut(BaseModel):
    id: int
    kind: str = Field(description="'email', 'telegram' or 'ntfy'")
    masked: str = Field(description="Enough of the target to tell it apart; never all of it")
    verified: bool
    verified_at: datetime | None
    created_at: datetime
    disabled_at: datetime | None


class ChannelsOut(BaseModel):
    channels: list[ChannelOut]
    available: dict[str, bool] = Field(description="Which kinds this server can add now")


class AddIn(BaseModel):
    kind: str
    target: str


class AddOut(ChannelOut):
    sent: str = Field(description="What was sent to the target to verify it, in words")


class VerifyIn(BaseModel):
    token: str


def _out(channel: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=channel.id,
        kind=channel.kind,
        masked=channel.masked_target,
        verified=channel.verified_at is not None and channel.disabled_at is None,
        verified_at=channel.verified_at,
        created_at=channel.created_at,
        disabled_at=channel.disabled_at,
    )


@router.get("/me/channels", summary="Your alert channels, masked")
def list_channels(viewer: CurrentUser, session: SessionDep, settings: SettingsDep) -> ChannelsOut:
    rows = channels.listed(session, viewer.user_id) if viewer.user_id is not None else []
    return ChannelsOut(channels=[_out(row) for row in rows], available=channels.available(settings))


def _email_link(settings: Settings, request: Request, token: str) -> str:
    base = settings.fcp_public_url or str(request.base_url)
    return f"{base.rstrip('/')}/account/alerts?token={token}"


def _send_verification(
    added: channels.Added, request: Request, settings: Settings, user_id: int
) -> str:
    """Send the link or the code to the new target; what was sent, in words.
    Raises `HTTPException` when it could not be."""
    channel = added.channel
    if channel.kind == channels.EMAIL:
        link = _email_link(settings, request, added.secret)
        if not settings.fcp_public_url:
            if settings.smtp_configured:
                raise HTTPException(status_code=503, detail=NO_LINK)
            # Development and tests only, as for sign-in: no SMTP, so the link
            # is logged for whoever runs the server, and nothing is mailed.
            log.info("channel link for user %s (SMTP not configured): %s", user_id, link)
            return "a link, logged by the server (no SMTP here)"
        text = (
            "Someone (we hope you) asked for FCP's digest and alerts to come to this "
            f"address. To confirm, open this link while signed in to FCP:\n\n{link}\n\n"
            "It works once, for a day. If this was not you, ignore it and nothing is sent.\n"
        )
        channels.send_to(channel, text, title=SUBJECT, settings=settings)
        return f"a link to {channel.masked_target}"
    code = added.secret
    text = (
        f"Your FCP code is {code}. Type it on the Alerts page to have the digest and "
        "alerts sent here. It works for a day. If this was not you, ignore it."
    )
    channels.send_to(channel, text, title=TITLE, settings=settings)
    return f"a code to {channel.masked_target}"


@router.post("/me/channels", status_code=201, summary="Add an alert channel and verify it")
def add_channel(
    body: AddIn, request: Request, viewer: CurrentUser, session: SessionDep, settings: SettingsDep
) -> AddOut:
    user_id = _user_id(viewer)
    if not secrets_box.configured(settings):
        raise HTTPException(status_code=503, detail=NO_KEY)
    kind = body.kind.strip().lower()
    if kind not in channels.KINDS:
        raise HTTPException(status_code=422, detail="a channel is email, telegram or ntfy")
    if not channels.available(settings)[kind]:
        raise HTTPException(status_code=503, detail=NOT_AVAILABLE[kind])
    if len(body.target) > 300:
        raise HTTPException(status_code=422, detail="that target is too long to be one")
    if not _limits(request).add.allow(str(user_id)):
        raise HTTPException(status_code=429, detail="too many channels added; wait a minute")
    try:
        added = channels.add(session, user_id, kind, body.target, settings)
    except channels.ChannelInputError as refused:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(refused)) from None
    except channels.TooManyChannelsError:
        session.rollback()
        raise HTTPException(status_code=409, detail=TOO_MANY) from None
    try:
        sent = _send_verification(added, request, settings, user_id)
    except HTTPException:
        session.rollback()
        raise
    except Exception as error:
        session.rollback()
        log.error("channel for user %s not verified: %s", user_id, type(error).__name__)
        raise HTTPException(status_code=502, detail=NOT_SENT.format(kind=kind)) from None
    session.commit()
    log.info("user %s added a %s channel", user_id, kind)
    return AddOut(**_out(added.channel).model_dump(), sent=sent)


@router.post("/me/channels/verify", summary="Verify a channel with its link's token or its code")
def verify_channel(
    body: VerifyIn, request: Request, viewer: CurrentUser, session: SessionDep
) -> ChannelOut:
    user_id = _user_id(viewer)
    if not _limits(request).verify.allow(str(user_id)):
        raise HTTPException(status_code=429, detail="too many tries; wait a minute")
    channel = channels.verify(session, user_id, body.token)
    if channel is None:
        session.rollback()
        raise HTTPException(status_code=404, detail=BAD_SECRET)
    session.commit()
    log.info("user %s verified channel %s", user_id, channel.id)
    return _out(channel)


@router.delete("/me/channels/{channel_id}", summary="Disable an alert channel and wipe its target")
def disable_channel(channel_id: int, viewer: CurrentUser, session: SessionDep) -> ChannelOut:
    """Your own channel only; anyone else's is a 404 like one never added."""
    channel = channels.disable(session, _user_id(viewer), channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="no such channel of yours")
    session.commit()
    log.info("user %s disabled channel %s", viewer.user_id, channel_id)
    return _out(channel)
