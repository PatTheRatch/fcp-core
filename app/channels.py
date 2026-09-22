"""Each member's own channel: the address his digest and alerts go to.

Step 4 of docs/product.md. The routes are `/me/channels` in
`app/api/channels.py`, the page is Account, Alerts; docs/accounts.md has the
scopes and docs/jobs.md how the digest job uses these.

ONE KIND

`email`: an address, and nothing else (2026-09-22). It is verified by a link
mailed to it through `app.notify.send_email`: a one-time token like a sign-in
link's, of which only the sha256 is kept, good for a day.

The `telegram` and `ntfy` kinds were here until 2026-09-22. Everything this
server says now goes by email, so they are gone: migration
`0024_email_only_channels` disabled the rows rather than deleting them (the
member is told on the Alerts page that his old channel has stopped and that
he should add an address), and the table's CHECK now allows a row that is
not email only while it is disabled, so no new one can be written.

SECRETS

The address is sealed with `FCP_SECRETS_KEY` before it is written, opened
only to send, and never returned: the page shows `masked_target`. Disabling
a channel wipes the sealed target. Nothing here logs a target or a token.

SINGLE MODE

The server's owner keeps his own recipients in `.env` (`FCP_EMAIL_*`):
nothing about them is sealed or stored, and they stay his whatever is added
here (`app.job_kinds`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import accounts, notify, secrets_box
from app.config import Settings
from app.db.models import NotificationChannel

EMAIL = "email"
KINDS = (EMAIL,)

#: How long a verification link works.
VERIFY_TTL = timedelta(days=1)
#: Live channels (not disabled) one member may hold.
MAX_CHANNELS = 5

MASK = "•••"

BAD_EMAIL = "that is not an email address"
#: What a row the migration disabled reads as on the page, so a member whose
#: Telegram chat stopped is told why rather than left wondering.
RETIRED = "this kind of channel has been retired; add an email address"


class ChannelInputError(ValueError):
    """A target that is not one: the message is ours and names no value."""


class TooManyChannelsError(Exception):
    """He already has `MAX_CHANNELS` live ones."""


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------


def normalise_target(kind: str, raw: str) -> str:
    """The address as sealed and used, or `ChannelInputError`."""
    if kind != EMAIL:
        raise ChannelInputError("a channel is an email address")
    email = accounts.normalise_email(raw.strip())
    if email is None:
        raise ChannelInputError(BAD_EMAIL)
    return email


def mask(kind: str, target: str) -> str:
    """What the page shows of an address: enough to tell two apart, not
    enough to use."""
    local, _, domain = target.partition("@")
    return f"{local[:1]}{MASK}@{domain}"


def available(settings: Settings) -> dict[str, bool]:
    """Which kinds this server can add now: one, and it needs the secrets key
    to seal with. Email is offered without SMTP as sign-in is: the link is
    then logged for whoever runs the server, which is development, and
    nothing is mailed."""
    return {EMAIL: secrets_box.configured(settings)}


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Added:
    channel: NotificationChannel
    #: The link's token: shown to nobody, only mailed to the address itself.
    secret: str


def live(session: Session, user_id: int) -> list[NotificationChannel]:
    """His channels that are not disabled, verified or not, oldest first."""
    return list(
        session.scalars(
            select(NotificationChannel)
            .where(
                NotificationChannel.user_id == user_id, NotificationChannel.disabled_at.is_(None)
            )
            .order_by(NotificationChannel.created_at, NotificationChannel.id)
        ).all()
    )


def listed(session: Session, user_id: int) -> list[NotificationChannel]:
    """All of his channels, disabled ones too, newest first."""
    return list(
        session.scalars(
            select(NotificationChannel)
            .where(NotificationChannel.user_id == user_id)
            .order_by(NotificationChannel.created_at.desc(), NotificationChannel.id.desc())
        ).all()
    )


def verified(session: Session, user_id: int) -> list[NotificationChannel]:
    """Where his digest goes: verified, not disabled, and an address.

    The kind is checked as well as the flags. A member whose Telegram row the
    migration disabled has no deliverable channel at all, and the digest job
    says so rather than failing to reach him quietly.
    """
    return [
        channel
        for channel in live(session, user_id)
        if channel.verified_at is not None and channel.kind == EMAIL
    ]


def target_of(channel: NotificationChannel, settings: Settings | None = None) -> str:
    """The channel's address, opened: for sending, and for nothing else."""
    if channel.sealed_target is None:
        raise ValueError("this channel was disabled; its target is gone")
    return secrets_box.open_(channel.sealed_target, settings)


def add(
    session: Session, user_id: int, kind: str, raw: str, settings: Settings | None = None
) -> Added:
    """A new, unverified channel, its address sealed; with the token that
    verifies it, for the caller to mail there. An address he already has live
    is reset (a new token) rather than added twice. Does not commit.
    """
    target = normalise_target(kind, raw)
    sealed = secrets_box.seal(target, settings)
    secret = accounts.new_token()
    hashed = accounts.hash_token(secret)
    expires = accounts.now() + VERIFY_TTL
    mine = live(session, user_id)
    for channel in mine:
        if channel.kind == EMAIL and target_of(channel, settings) == target:
            channel.verify_hash = hashed
            channel.verify_expires_at = expires
            session.flush()
            return Added(channel, secret)
    if len(mine) >= MAX_CHANNELS:
        raise TooManyChannelsError()
    channel = NotificationChannel(
        user_id=user_id,
        kind=EMAIL,
        sealed_target=sealed,
        masked_target=mask(EMAIL, target),
        verify_hash=hashed,
        verify_expires_at=expires,
    )
    session.add(channel)
    session.flush()
    return Added(channel, secret)


def verify(session: Session, user_id: int, presented: str) -> NotificationChannel | None:
    """Spend a verification link's token, on one of his own live channels;
    the channel it verified, or None. Does not commit."""
    presented = presented.strip()
    if not presented or len(presented) > 200:
        return None
    channel = session.scalar(
        select(NotificationChannel)
        .where(
            NotificationChannel.user_id == user_id,
            NotificationChannel.disabled_at.is_(None),
            NotificationChannel.verify_hash == accounts.hash_token(presented),
            NotificationChannel.verify_expires_at > accounts.now(),
        )
        .with_for_update()
    )
    if channel is None:
        return None
    channel.verified_at = accounts.now()
    channel.verify_hash = None
    channel.verify_expires_at = None
    session.flush()
    return channel


def disable(session: Session, user_id: int, channel_id: int) -> NotificationChannel | None:
    """Stop sending to one of his channels and wipe its target; None when it
    is not his. Disabling twice is harmless. Does not commit."""
    channel = session.scalar(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id, NotificationChannel.user_id == user_id
        )
    )
    if channel is None:
        return None
    if channel.disabled_at is None:
        channel.disabled_at = accounts.now()
    channel.sealed_target = None
    channel.verify_hash = None
    channel.verify_expires_at = None
    session.flush()
    return channel


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------


def send_to(
    channel: NotificationChannel,
    text: str,
    *,
    title: str,
    settings: Settings,
    html: str | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """Send one message to one channel. Raises on a refusal; the caller
    records the exception's class, never its text (an SMTP refusal quotes the
    server's reply)."""
    if channel.kind != EMAIL:
        raise RuntimeError("this channel's kind was retired; only email is delivered")
    target = target_of(channel, settings)
    if not settings.smtp_configured:
        raise RuntimeError("SMTP is not configured")
    notify.send_email(
        text,
        host=str(settings.fcp_smtp_host),
        port=settings.fcp_smtp_port,
        sender=str(settings.fcp_email_from),
        recipients=[target],
        subject=title,
        html=html,
        headers=headers,
        user=settings.fcp_smtp_user,
        password=settings.fcp_smtp_password,
    )


def deliver(
    channels: list[NotificationChannel],
    text: str,
    *,
    title: str,
    settings: Settings,
    html: str | None = None,
    headers: dict[str, str] | None = None,
) -> list[notify.Delivery]:
    """Send to each channel; what each one did, by its masked name. Never
    raises, and no error carries more than the exception's class."""
    out: list[notify.Delivery] = []
    for channel in channels:
        name = f"{channel.kind} {channel.masked_target}"
        try:
            send_to(channel, text, title=title, settings=settings, html=html, headers=headers)
            out.append(notify.Delivery(name, True))
        except Exception as error:
            out.append(notify.Delivery(name, False, type(error).__name__))
    return out
