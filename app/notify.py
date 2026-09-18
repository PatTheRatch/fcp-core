"""Sending one short message to a phone, or to an inbox.

THE URL CHANNEL

One POST, no client library, no account: the manager picks a service and
gives it a URL. Two shapes cover the two services docs/pickups.md section
10 left open, and which one is used is decided by whether a chat id is set:

* **ntfy** (the least setup, and the default shape): the text is the body,
  the title is a header. A topic URL like `https://ntfy.sh/fcp-core-abc123`
  is all it needs. The topic name is the only secret, so make it long.
* **Telegram** (the nicer phone experience): set the chat id as well, and
  the same text goes as JSON to `https://api.telegram.org/bot<token>/sendMessage`.

THE EMAIL CHANNEL

Plain SMTP through the standard library, so any transactional provider's
endpoint works without a provider library or a second dependency: Postmark,
Resend, SES, Fastmail, a company relay. Port 465 is implicit TLS and
anything else is STARTTLS, which is the 587 submission port nearly every
provider asks for; the message never crosses the wire unencrypted either
way. A user and password are optional, because a relay on the same host
often wants neither. The password is never logged, never printed and never
put in an error message.

BOTH AT ONCE

`deliver` sends to every channel that is configured and returns what each
one did. One channel failing does not stop the other: a broken SMTP login
should not cost the manager his Telegram digest, and the caller decides what
a partial delivery means (for `scripts/digest.py`: mark the events notified,
because he got the news, and still exit non-zero so the failure is visible).

Nothing here retries. A missed digest is tomorrow's digest, and a send that
reaches nobody leaves the events unmarked so the next one repeats them.
"""

import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:  # `app.config` imports nothing from here; keep it that way.
    from app.config import Settings

#: Long enough for a phone push to be accepted, short enough that a hung
#: notification service cannot hold up a scheduled pass.
TIMEOUT_SECONDS = (5, 15)

#: The same bound for SMTP, which has a handshake and a login to get through
#: and so wants more than the POST does. One number: smtplib has no separate
#: connect and read timeouts.
SMTP_TIMEOUT_SECONDS = 20

#: The port that speaks TLS from the first byte. Everything else is assumed
#: to be submission or plain SMTP, and is upgraded with STARTTLS.
IMPLICIT_TLS_PORT = 465

URL_CHANNEL = "url"
EMAIL_CHANNEL = "email"


@dataclass(frozen=True)
class Delivery:
    """What one channel did with one message."""

    channel: str
    sent: bool
    #: Why it failed, for the log. Never carries a credential.
    error: str = ""

    def describe(self) -> str:
        return f"{self.channel} ok" if self.sent else f"{self.channel} FAILED: {self.error}"


def send(url: str, text: str, *, title: str | None = None, chat_id: str | None = None) -> None:
    """POST one message. Raises `requests.HTTPError` on a refusal."""
    if chat_id:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if title:
            payload["text"] = f"{title}\n\n{text}"
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
    else:
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if title:
            # ntfy reads these; anything else ignores them.
            headers["Title"] = title
        response = requests.post(
            url, data=text.encode("utf-8"), headers=headers, timeout=TIMEOUT_SECONDS
        )
    response.raise_for_status()


def send_email(
    text: str,
    *,
    host: str,
    port: int,
    sender: str,
    recipients: Sequence[str],
    subject: str,
    user: str | None = None,
    password: str | None = None,
) -> None:
    """Send one plain-text email. Raises `smtplib.SMTPException` on a refusal.

    One attempt, one connection, no retry, like `send`. Implicit TLS on
    `IMPLICIT_TLS_PORT`, STARTTLS everywhere else, so the body and the login
    are encrypted on either. A user without a password (or the reverse) is
    treated as no credentials at all rather than half a login.
    """
    if not recipients:
        raise ValueError("no recipients to send to")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(text)

    client: smtplib.SMTP
    if port == IMPLICIT_TLS_PORT:
        client = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    else:
        client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    with client:
        if port != IMPLICIT_TLS_PORT:
            client.starttls()
        if user and password:
            client.login(user, password)
        # `send_message` takes the envelope recipients from the headers, but
        # they are passed explicitly so a comma in a display name cannot
        # change who the mail actually goes to.
        client.send_message(message, from_addr=sender, to_addrs=list(recipients))


def deliver(settings: "Settings", text: str, *, title: str) -> list[Delivery]:
    """Send `text` to every configured channel, and say what each one did.

    Never raises: a channel's failure is one `Delivery` with its reason, so
    the other channels still get their turn. An unconfigured channel is not
    reported at all, so an empty list means nothing was set up to receive
    this at all.

    The error text is the exception's own, which for `requests` and
    `smtplib` names the host and the server's refusal. Neither library puts
    a password in it, and none is added here.
    """
    out: list[Delivery] = []
    if settings.fcp_digest_url:
        try:
            send(settings.fcp_digest_url, text, title=title, chat_id=settings.fcp_digest_chat_id)
            out.append(Delivery(URL_CHANNEL, True))
        except Exception as error:
            out.append(Delivery(URL_CHANNEL, False, f"{type(error).__name__}: {error}"))
    if settings.email_configured:
        try:
            send_email(
                text,
                host=str(settings.fcp_smtp_host),
                port=settings.fcp_smtp_port,
                sender=str(settings.fcp_email_from),
                recipients=settings.email_recipients,
                subject=title,
                user=settings.fcp_smtp_user,
                password=settings.fcp_smtp_password,
            )
            out.append(Delivery(EMAIL_CHANNEL, True))
        except Exception as error:
            out.append(Delivery(EMAIL_CHANNEL, False, f"{type(error).__name__}: {error}"))
    return out
