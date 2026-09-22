"""Sending one message, to an inbox. There is no other channel.

EMAIL, AND ONLY EMAIL

Everything this server has to say goes by email: a member's morning digest
and his alerts, and the operator's own notices when a scheduled job has gone
quiet (2026-09-22). The phone-push channel and the Telegram bot are gone —
`FCP_DIGEST_URL`, `FCP_DIGEST_CHAT_ID` and `FCP_TELEGRAM_BOT_URL` no longer
exist — because a digest that has to fit a 4,096-character chat box cannot
also be the page the manager reads at breakfast, and keeping two shapes of
the same message meant neither was properly made.

Plain SMTP through the standard library, so any transactional provider's
endpoint works without a provider library or a second dependency: Postmark,
Resend, SES, Fastmail, a company relay. Port 465 is implicit TLS and
anything else is STARTTLS, which is the 587 submission port nearly every
provider asks for; the message never crosses the wire unencrypted either
way. A user and password are optional, because a relay on the same host
often wants neither. The password is never logged, never printed and never
put in an error message.

TWO PARTS, ONE MESSAGE

`send_email` takes the text and, where there is one, the HTML beside it, and
posts them as `multipart/alternative`: the reader's client shows the HTML and
everything that cannot — a terminal client, a screen reader set to plain
text, a digest quoted into a reply — shows the text. The text is never an
apology for the HTML; it is the message `Digest.render()` has always made
(`app/mail/` builds both).

`headers` carries the few a transactional mail owes its reader:
`List-Unsubscribe` pointing at the Alerts page, and nothing that tracks.

Nothing here retries. A missed digest is tomorrow's digest, and a send that
reaches nobody leaves the events unmarked so the next one repeats them.
"""

import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # `app.config` imports nothing from here; keep it that way.
    from app.config import Settings

#: The bound for SMTP, which has a handshake and a login to get through. One
#: number: smtplib has no separate connect and read timeouts.
SMTP_TIMEOUT_SECONDS = 20

#: The port that speaks TLS from the first byte. Everything else is assumed
#: to be submission or plain SMTP, and is upgraded with STARTTLS.
IMPLICIT_TLS_PORT = 465

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


def build_email(
    text: str,
    *,
    sender: str,
    recipients: Sequence[str],
    subject: str,
    html: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> EmailMessage:
    """The message itself, built but not sent: one plain-text part, and the
    HTML alternative beside it when there is one.

    Separate from the sending so a preview can render exactly what would go
    out without opening a connection (`scripts/notify_test.py --preview`).
    """
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(text)
    if html is not None:
        # The text part is added first and the HTML second, which is what
        # `multipart/alternative` means by "best last": a client that can
        # draw the page draws it, and everything else keeps the words.
        message.add_alternative(html, subtype="html")
    return message


def send_email(
    text: str,
    *,
    host: str,
    port: int,
    sender: str,
    recipients: Sequence[str],
    subject: str,
    html: str | None = None,
    headers: Mapping[str, str] | None = None,
    user: str | None = None,
    password: str | None = None,
) -> None:
    """Send one email. Raises `smtplib.SMTPException` on a refusal.

    One attempt, one connection, no retry. Implicit TLS on
    `IMPLICIT_TLS_PORT`, STARTTLS everywhere else, so the body and the login
    are encrypted on either. A user without a password (or the reverse) is
    treated as no credentials at all rather than half a login.
    """
    if not recipients:
        raise ValueError("no recipients to send to")
    message = build_email(
        text,
        sender=sender,
        recipients=recipients,
        subject=subject,
        html=html,
        headers=headers,
    )

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


def deliver(
    settings: "Settings",
    text: str,
    *,
    title: str,
    html: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> list[Delivery]:
    """Send `text` to the `.env` recipients, and say what happened.

    Never raises: a failure is one `Delivery` with its reason. Mail that is
    not configured is not reported at all, so an empty list means nothing was
    set up to receive this.

    The error text is the exception's own, which for `smtplib` names the host
    and the server's refusal. The library does not put a password in it, and
    none is added here.
    """
    if not settings.email_configured:
        return []
    try:
        send_email(
            text,
            host=str(settings.fcp_smtp_host),
            port=settings.fcp_smtp_port,
            sender=str(settings.fcp_email_from),
            recipients=settings.email_recipients,
            subject=title,
            html=html,
            headers=headers,
            user=settings.fcp_smtp_user,
            password=settings.fcp_smtp_password,
        )
        return [Delivery(EMAIL_CHANNEL, True)]
    except Exception as error:
        return [Delivery(EMAIL_CHANNEL, False, f"{type(error).__name__}: {error}")]


def notice(settings: "Settings", text: str, *, subject: str) -> list[Delivery]:
    """The operator's own notice: a scheduled job that has gone quiet, a
    league that needs reconnecting. Plain text to `FCP_EMAIL_TO`, with a
    subject that says what broke, so it is readable in a phone's notification
    without the message being opened.

    The same transport as everything else, named apart because the caller is
    the watchdog rather than a manager's digest.
    """
    return deliver(settings, text, title=subject)
