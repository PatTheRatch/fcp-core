"""Each member's own channels: where his digest and alerts go.

Step 4 of docs/product.md. The routes are `/me/channels` in
`app/api/channels.py`, the page is Account, Alerts; docs/accounts.md has the
scopes and docs/jobs.md how the digest job uses these.

THREE KINDS

* `email`: an address. Verified by a link mailed to it through
  `app.notify.send_email`: a one-time token like a sign-in link's, of which
  only the sha256 is kept, good for a day.
* `telegram`: a chat id, reached through the server's own bot
  (`FCP_TELEGRAM_BOT_URL`, else the digest's own Telegram URL). Verified by
  a test message carrying a code, which he types back on the page.
* `ntfy`: a topic on ntfy.sh, verified the same way. Only ntfy.sh: a URL of
  the member's choosing would let him make this server post anywhere,
  including to addresses only it can reach.

SECRETS

The target (the address, the chat id, the topic URL) is sealed with
`FCP_SECRETS_KEY` before it is written, opened only to send, and never
returned: the page shows `masked_target`. Disabling a channel wipes the
sealed target. Nothing here logs a target, a token or a code.

SINGLE MODE

The server's owner keeps the channels in `.env` (FCP_DIGEST_URL and its chat
id, FCP_EMAIL_*): nothing about them is sealed or stored, and they stay his
whatever is added here (`app.job_kinds`).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import accounts, notify, secrets_box
from app.config import Settings
from app.db.models import NotificationChannel

EMAIL = "email"
TELEGRAM = "telegram"
NTFY = "ntfy"
KINDS = (EMAIL, TELEGRAM, NTFY)

#: How long a verification link or code works.
VERIFY_TTL = timedelta(days=1)
#: Live channels (not disabled) one member may hold.
MAX_CHANNELS = 5
#: The one ntfy server a member's topic may be on.
NTFY_BASE = "https://ntfy.sh/"
#: A code a person can read off a phone and type: no 0/O, no 1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

_CHAT_ID = re.compile(r"-?\d{1,20}")
_TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
MASK = "•••"

BAD_EMAIL = "that is not an email address"
BAD_CHAT = "a Telegram chat id is a number, like 123456789 (a group's starts with -100)"
BAD_TOPIC = "an ntfy topic is letters, digits, - and _, or a https://ntfy.sh/ address"


class ChannelInputError(ValueError):
    """A target that is not one: the message is ours and names no value."""


class TooManyChannelsError(Exception):
    """He already has `MAX_CHANNELS` live ones."""


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------


def normalise_target(kind: str, raw: str) -> str:
    """The target as sealed and used, or `ChannelInputError`."""
    value = raw.strip()
    if kind == EMAIL:
        email = accounts.normalise_email(value)
        if email is None:
            raise ChannelInputError(BAD_EMAIL)
        return email
    if kind == TELEGRAM:
        if not _CHAT_ID.fullmatch(value):
            raise ChannelInputError(BAD_CHAT)
        return value
    if kind == NTFY:
        topic = value
        if value.lower().startswith(("http://", "https://")):
            parsed = urlparse(value)
            if parsed.scheme != "https" or parsed.netloc.lower() != "ntfy.sh" or parsed.query:
                raise ChannelInputError(BAD_TOPIC)
            topic = parsed.path.strip("/")
        if not _TOPIC.fullmatch(topic):
            raise ChannelInputError(BAD_TOPIC)
        return NTFY_BASE + topic
    raise ChannelInputError("a channel is email, telegram or ntfy")


def mask(kind: str, target: str) -> str:
    """What the page shows of a target: enough to tell two apart, not enough
    to use."""
    if kind == EMAIL:
        local, _, domain = target.partition("@")
        return f"{local[:1]}{MASK}@{domain}"
    if kind == TELEGRAM:
        return f"chat {MASK}{target[-4:]}" if len(target) > 4 else f"chat {MASK}"
    topic = target.removeprefix(NTFY_BASE)
    return f"ntfy.sh/{topic[:2]}{MASK}"


def telegram_url(settings: Settings) -> str | None:
    """The bot a member's chat is reached through: the configured one, else
    the digest's own when it is Telegram's (a chat id is set beside it)."""
    if settings.fcp_telegram_bot_url:
        return settings.fcp_telegram_bot_url
    if settings.fcp_digest_url and settings.fcp_digest_chat_id:
        return settings.fcp_digest_url
    return None


def available(settings: Settings) -> dict[str, bool]:
    """Which kinds this server can add now. Every kind needs the secrets key
    to seal with; Telegram needs a bot. Email is offered without SMTP as
    sign-in is: the link is then logged for whoever runs the server, which is
    development, and nothing is mailed."""
    sealing = secrets_box.configured(settings)
    return {
        EMAIL: sealing,
        TELEGRAM: sealing and telegram_url(settings) is not None,
        NTFY: sealing,
    }


def new_code() -> str:
    """A code for a test message: `CODE_LENGTH` characters, shown as two
    groups of four."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def _code_hash(presented: str) -> str:
    """Codes are compared without their dash, spaces or case."""
    return accounts.hash_token(re.sub(r"[\s-]", "", presented).upper())


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Added:
    channel: NotificationChannel
    #: The link's token (email) or the code (telegram, ntfy): shown to
    #: nobody, only sent to the target itself.
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
    """Where his digest goes: verified and not disabled."""
    return [channel for channel in live(session, user_id) if channel.verified_at is not None]


def target_of(channel: NotificationChannel, settings: Settings | None = None) -> str:
    """The channel's target, opened: for sending, and for nothing else."""
    if channel.sealed_target is None:
        raise ValueError("this channel was disabled; its target is gone")
    return secrets_box.open_(channel.sealed_target, settings)


def add(
    session: Session, user_id: int, kind: str, raw: str, settings: Settings | None = None
) -> Added:
    """A new, unverified channel, its target sealed; with the secret that
    verifies it, for the caller to send to the target. A target he already
    has live is reset (new secret) rather than added twice. Does not commit.
    """
    target = normalise_target(kind, raw)
    sealed = secrets_box.seal(target, settings)
    secret = accounts.new_token() if kind == EMAIL else new_code()
    hashed = accounts.hash_token(secret) if kind == EMAIL else _code_hash(secret)
    expires = accounts.now() + VERIFY_TTL
    mine = live(session, user_id)
    for channel in mine:
        if channel.kind == kind and target_of(channel, settings) == target:
            channel.verify_hash = hashed
            channel.verify_expires_at = expires
            session.flush()
            return Added(channel, secret)
    if len(mine) >= MAX_CHANNELS:
        raise TooManyChannelsError()
    channel = NotificationChannel(
        user_id=user_id,
        kind=kind,
        sealed_target=sealed,
        masked_target=mask(kind, target),
        verify_hash=hashed,
        verify_expires_at=expires,
    )
    session.add(channel)
    session.flush()
    return Added(channel, secret)


def verify(session: Session, user_id: int, presented: str) -> NotificationChannel | None:
    """Spend a verification link's token or a test message's code, on one of
    his own live channels; the channel it verified, or None. Does not commit."""
    presented = presented.strip()
    if not presented or len(presented) > 200:
        return None
    candidates = {accounts.hash_token(presented), _code_hash(presented)}
    channel = session.scalar(
        select(NotificationChannel)
        .where(
            NotificationChannel.user_id == user_id,
            NotificationChannel.disabled_at.is_(None),
            NotificationChannel.verify_hash.in_(sorted(candidates)),
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
) -> None:
    """Send one message to one channel. Raises on a refusal; the caller
    records the exception's class, never its text (a Telegram refusal quotes
    the bot's URL, and with it the bot's token)."""
    target = target_of(channel, settings)
    if channel.kind == EMAIL:
        if not settings.smtp_configured:
            raise RuntimeError("SMTP is not configured")
        notify.send_email(
            text,
            host=str(settings.fcp_smtp_host),
            port=settings.fcp_smtp_port,
            sender=str(settings.fcp_email_from),
            recipients=[target],
            subject=title,
            user=settings.fcp_smtp_user,
            password=settings.fcp_smtp_password,
        )
    elif channel.kind == TELEGRAM:
        bot = telegram_url(settings)
        if bot is None:
            raise RuntimeError("no Telegram bot is configured")
        notify.send(bot, text, title=title, chat_id=target)
    else:
        notify.send(target, text, title=title)


def deliver(
    channels: list[NotificationChannel], text: str, *, title: str, settings: Settings
) -> list[notify.Delivery]:
    """Send to each channel; what each one did, by its masked name. Never
    raises, and no error carries more than the exception's class."""
    out: list[notify.Delivery] = []
    for channel in channels:
        name = f"{channel.kind} {channel.masked_target}"
        try:
            send_to(channel, text, title=title, settings=settings)
            out.append(notify.Delivery(name, True))
        except Exception as error:
            out.append(notify.Delivery(name, False, type(error).__name__))
    return out
