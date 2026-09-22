"""A member's own alert address: add, verify, list, disable.

Step 4 of docs/product.md. The database half is `app.channels`; the page is
Account, Alerts (`/account/alerts`, app/api/site.py); docs/accounts.md has
each route's scope, all of them "signed in", about the caller's own rows.

    GET    /me/channels                  his channels, masked, and whether one can be added
    POST   /me/channels   {target}       add one; a confirmation link goes to it
    POST   /me/channels/verify  {token}  the link's token, spent
    DELETE /me/channels/{channel_id}     disable one, and wipe its target
    GET    /me/subscriptions             what goes in his email, per league
    PUT    /me/subscriptions/{league_id} choose it (`app.subscriptions`)

**Email and nothing else** since 2026-09-22. `kind` is still accepted in the
body and still answered, because it is a column and the page shows a row the
migration disabled, but the only kind that can be added is `email`.

VERIFYING

Nothing is sent to an address until he proves it is his: a link
(`/account/alerts?token=...`, built on `FCP_PUBLIC_URL` only, never on the
request's Host header), which the page spends. Without SMTP (development,
the tests) the link is logged at INFO on `fcp.channels` rather than mailed,
as the sign-in link is.

SECRETS

A target is sealed before it is stored and never comes back: every answer
carries `masked` ("p•••@example.com"). The body is read by hand rather than
by pydantic's constraints, whose 422 repeats the value it refused. A failed
send is logged and answered by the exception's class, never its text, which
can quote the mail server's reply.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import accounts, channels, memberships, secrets_box, subscriptions
from app.api.access import CurrentUser, SettingsDep, Viewer
from app.api.auth import TokenBucket
from app.api.deps import LeagueIdPath, SessionDep
from app.config import Settings
from app.db.models import League, NotificationChannel

log = logging.getLogger("fcp.channels")

router = APIRouter(tags=["alerts"])

NO_KEY = "Alert channels are not set up on this server yet (no secrets key)."
NO_ACCOUNT = "accounts are not set up on this server yet"
ONLY_EMAIL = "a channel is an email address"
NO_LINK = "an email channel needs FCP_PUBLIC_URL, so its link can be built"
NOT_SENT = "the confirmation email could not be sent, so the address was not added"
BAD_SECRET = "That link is not valid, or has expired. Add the address again for a new one."
TOO_MANY = f"at most {channels.MAX_CHANNELS} addresses at once; disable one first"
SUBJECT = "Confirm this address for FCP alerts"
NOT_HIS_LEAGUE = "no such league of yours"


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
    kind: str = Field(description="'email'; a disabled row may be a retired kind")
    masked: str = Field(description="Enough of the target to tell it apart; never all of it")
    verified: bool
    verified_at: datetime | None
    created_at: datetime
    disabled_at: datetime | None
    retired: bool = Field(
        default=False,
        description="A kind this server no longer delivers to; the row is disabled",
    )


class ChannelsOut(BaseModel):
    channels: list[ChannelOut]
    available: dict[str, bool] = Field(description="Which kinds this server can add now")


class AddIn(BaseModel):
    target: str
    kind: str = "email"


class AddOut(ChannelOut):
    sent: str = Field(description="What was sent to the address to verify it, in words")


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
        retired=channel.kind != channels.EMAIL,
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
    """Mail the confirmation link to the new address; what was sent, in
    words. Raises `HTTPException` when it could not be."""
    channel = added.channel
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


@router.post("/me/channels", status_code=201, summary="Add an alert address and verify it")
def add_channel(
    body: AddIn, request: Request, viewer: CurrentUser, session: SessionDep, settings: SettingsDep
) -> AddOut:
    user_id = _user_id(viewer)
    if not secrets_box.configured(settings):
        raise HTTPException(status_code=503, detail=NO_KEY)
    kind = body.kind.strip().lower()
    if kind != channels.EMAIL:
        raise HTTPException(status_code=422, detail=ONLY_EMAIL)
    if not channels.available(settings)[kind]:
        raise HTTPException(status_code=503, detail=NO_KEY)
    if len(body.target) > 300:
        raise HTTPException(status_code=422, detail="that address is too long to be one")
    if not _limits(request).add.allow(str(user_id)):
        raise HTTPException(status_code=429, detail="too many addresses added; wait a minute")
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
        raise HTTPException(status_code=502, detail=NOT_SENT) from None
    session.commit()
    log.info("user %s added an email channel", user_id)
    return AddOut(**_out(added.channel).model_dump(), sent=sent)


@router.post("/me/channels/verify", summary="Verify an address with its link's token")
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


# ---------------------------------------------------------------------------
# what he wants to hear about, per league (app/subscriptions.py)
# ---------------------------------------------------------------------------


class TopicOut(BaseModel):
    name: str = Field(description="The topic's key, as `topics` is keyed")
    label: str = Field(description="What the page and the email call it")
    note: str = Field(description="One line of what it puts in the message")
    on: bool


class SubscriptionOut(BaseModel):
    espn_league_id: int
    league: str = Field(description="The league's name, for the page's heading")
    topics: list[TopicOut] = Field(description="Every topic, in the order the email reads them")
    morning: bool = Field(description="The morning digest")
    alerts: bool = Field(description="The urgent roster change between digests")
    silent: bool = Field(description="Every topic off: nothing is sent, and the job says so")


class SubscriptionsOut(BaseModel):
    leagues: list[SubscriptionOut]


class SubscriptionIn(BaseModel):
    topics: dict[str, bool]
    morning: bool = True
    alerts: bool = True


def _subscription(session: Session, user_id: int, league: League) -> SubscriptionOut:
    held = subscriptions.for_member(session, user_id, league.id)
    return SubscriptionOut(
        espn_league_id=int(league.espn_league_id),
        league=memberships.league_name(session, league),
        topics=[
            TopicOut(
                name=topic,
                label=subscriptions.LABELS[topic],
                note=subscriptions.NOTES[topic],
                on=held.on(topic),
            )
            for topic in subscriptions.TOPICS
        ],
        morning=held.morning,
        alerts=held.alerts,
        silent=held.silent,
    )


def _his_league(session: Session, user_id: int, espn_league_id: int) -> League:
    """A league he is a member of; anything else is a 404, like one that does
    not exist. A subscription is about a league's news, and its news is its
    members'."""
    if espn_league_id not in accounts.member_league_ids(session, user_id):
        raise HTTPException(status_code=404, detail=NOT_HIS_LEAGUE)
    league = memberships.league_by_espn_id(session, espn_league_id)
    if league is None:  # pragma: no cover - a membership names a stored league
        raise HTTPException(status_code=404, detail=NOT_HIS_LEAGUE)
    return league


@router.get("/me/subscriptions", summary="What goes in your email, per league")
def list_subscriptions(viewer: CurrentUser, session: SessionDep) -> SubscriptionsOut:
    """One answer per league you are a member of. A league you have never
    changed reads as the defaults; no row is written until you do."""
    user_id = _user_id(viewer)
    out: list[SubscriptionOut] = []
    for espn_league_id, _role in accounts.member_leagues(session, user_id):
        league = memberships.league_by_espn_id(session, espn_league_id)
        if league is not None:
            out.append(_subscription(session, user_id, league))
    return SubscriptionsOut(leagues=out)


@router.put("/me/subscriptions/{league_id}", summary="Choose what goes in your email")
def set_subscription(
    league_id: LeagueIdPath, body: SubscriptionIn, viewer: CurrentUser, session: SessionDep
) -> SubscriptionOut:
    """Replaces the whole answer for this league: every topic you left out
    takes its default rather than its last value, so the page always sends
    what it is showing."""
    user_id = _user_id(viewer)
    league = _his_league(session, user_id, league_id)
    subscriptions.save(
        session,
        user_id,
        league.id,
        topics=dict(body.topics),
        morning=body.morning,
        alerts=body.alerts,
    )
    session.commit()
    log.info("user %s set his subscription in league %s", user_id, league_id)
    return _subscription(session, user_id, league)
