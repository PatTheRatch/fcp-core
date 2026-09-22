"""Delivery: one plain-SMTP email, and nothing else.

Email is the only channel there is (2026-09-22): the push URL and the
Telegram bot are gone, so this file is the SMTP transport, the two parts of
a message, and what `deliver` says when a send fails or when nothing is set
up at all.
"""

import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from app import notify
from app.config import Settings

# ---------------------------------------------------------------------------
# The transport. Plain SMTP through the standard library, so the fake is
# a stand-in for `smtplib.SMTP` and `smtplib.SMTP_SSL` and the test reads the
# calls it recorded. Nothing here opens a socket.


class _FakeSMTP:
    """Records what the sender did, in order. Doubles as its own context."""

    #: Every instance built during a test, newest last.
    made: ClassVar[list["_FakeSMTP"]] = []
    #: Set to raise from `login`, for the refusal case.
    login_error: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.implicit_tls = False
        self.calls: list[str] = []
        self.logged_in_as: tuple[str, str] | None = None
        self.sent: list[tuple[EmailMessage, str, list[str]]] = []
        self.closed = False
        _FakeSMTP.made.append(self)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        if _FakeSMTP.login_error is not None:
            raise _FakeSMTP.login_error
        self.logged_in_as = (user, password)

    def send_message(
        self, message: EmailMessage, from_addr: str, to_addrs: list[str]
    ) -> dict[str, str]:
        self.calls.append("send_message")
        self.sent.append((message, from_addr, to_addrs))
        return {}


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSMTP]:
    _FakeSMTP.made = []
    _FakeSMTP.login_error = None

    def plain(host: str, port: int, timeout: float | None = None) -> _FakeSMTP:
        return _FakeSMTP(host, port, timeout)

    def implicit(host: str, port: int, timeout: float | None = None) -> _FakeSMTP:
        made = _FakeSMTP(host, port, timeout)
        made.implicit_tls = True
        return made

    monkeypatch.setattr(smtplib, "SMTP", plain)
    monkeypatch.setattr(smtplib, "SMTP_SSL", implicit)
    return _FakeSMTP


def _send(**overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "host": "smtp.example.net",
        "port": 587,
        "sender": "fcp@example.net",
        "recipients": ["patrick@example.com"],
        "subject": "FCP morning digest",
        "user": "apikey",
        "password": "hunter2",
    }
    kwargs.update(overrides)
    notify.send_email("two lines\nof news", **kwargs)


def test_the_message_carries_the_subject_the_sender_and_the_text(smtp: type[_FakeSMTP]) -> None:
    _send()

    [client] = smtp.made
    message, from_addr, to_addrs = client.sent[0]
    assert message["Subject"] == "FCP morning digest"
    assert message["From"] == "fcp@example.net"
    assert message["To"] == "patrick@example.com"
    assert message.get_content_type() == "text/plain"
    assert message.get_content() == "two lines\nof news\n"
    assert (from_addr, to_addrs) == ("fcp@example.net", ["patrick@example.com"])
    assert client.closed, "the connection is closed whatever happens"


def test_submission_upgrades_with_starttls_and_465_is_tls_from_the_first_byte(
    smtp: type[_FakeSMTP],
) -> None:
    _send(port=587)
    _send(port=2525)
    _send(port=465)

    submission, other, implicit = smtp.made
    assert submission.implicit_tls is False
    assert submission.calls == ["starttls", "login", "send_message"]
    assert other.implicit_tls is False, "anything that is not 465 is upgraded"
    assert other.calls == ["starttls", "login", "send_message"]
    assert implicit.implicit_tls is True
    assert implicit.calls == ["login", "send_message"], "no STARTTLS over implicit TLS"
    assert implicit.timeout == notify.SMTP_TIMEOUT_SECONDS


def test_several_recipients_are_split_and_all_get_the_envelope(smtp: type[_FakeSMTP]) -> None:
    _send(recipients=["a@example.com", "b@example.com", "c@example.com"])

    message, _from, to_addrs = smtp.made[0].sent[0]
    assert message["To"] == "a@example.com, b@example.com, c@example.com"
    assert to_addrs == ["a@example.com", "b@example.com", "c@example.com"]


def test_no_credentials_means_no_login_at_all(smtp: type[_FakeSMTP]) -> None:
    """A relay on the same host often wants neither, and half a login is none."""
    _send(user=None, password=None)
    _send(user="apikey", password=None)
    _send(user=None, password="hunter2")

    assert all("login" not in client.calls for client in smtp.made)
    assert all(client.logged_in_as is None for client in smtp.made)


def test_a_refused_login_is_raised_rather_than_swallowed(smtp: type[_FakeSMTP]) -> None:
    """A failed send must leave the events unmarked, so it cannot be quiet."""
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")

    with pytest.raises(smtplib.SMTPAuthenticationError):
        _send()

    assert smtp.made[0].sent == [], "nothing went out"
    assert smtp.made[0].closed, "and the connection still closed"


def test_no_recipient_is_refused_before_a_connection_is_opened(smtp: type[_FakeSMTP]) -> None:
    with pytest.raises(ValueError, match="no recipients"):
        _send(recipients=[])
    assert smtp.made == []


# ---------------------------------------------------------------------------
# What `deliver` and `notice` make of the settings.


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://x/y",
        "test_database_url": "postgresql+psycopg://x/y_test",
    }
    values.update(overrides)
    return Settings(**values)


def test_a_blank_setting_is_unset_and_a_blank_port_is_the_default() -> None:
    blank = _settings(
        fcp_email_to="  ", fcp_email_from="", fcp_smtp_host=" ", fcp_smtp_port="", fcp_smtp_user=""
    )
    assert blank.fcp_email_to is None and blank.fcp_smtp_host is None
    assert blank.fcp_smtp_port == 587
    assert blank.email_configured is False


def test_email_is_configured_only_when_host_sender_and_recipient_are_all_set() -> None:
    assert _settings(fcp_smtp_host="s", fcp_email_from="f").email_configured is False
    assert _settings(fcp_smtp_host="s", fcp_email_to="t@x").email_configured is False
    assert _settings(fcp_email_from="f", fcp_email_to="t@x").email_configured is False
    ready = _settings(fcp_smtp_host="s", fcp_email_from="f@x", fcp_email_to="t@x")
    assert ready.email_configured is True
    assert ready.fcp_smtp_port == 587, "submission by default"


def test_recipients_are_split_on_commas_and_blanks_dropped() -> None:
    many = _settings(fcp_email_to=" a@x.com , b@x.com ,, ")
    assert many.email_recipients == ["a@x.com", "b@x.com"]


def _configured(**overrides: Any) -> Settings:
    return _settings(
        fcp_smtp_host="smtp.example.net",
        fcp_email_from="fcp@example.net",
        fcp_email_to="patrick@example.com",
        **overrides,
    )


def test_delivery_is_one_email_and_nothing_else(smtp: type[_FakeSMTP]) -> None:
    results = notify.deliver(_configured(), "news", title="FCP morning digest")

    assert [(r.channel, r.sent) for r in results] == [("email", True)]
    assert results[0].describe() == "email ok"
    assert smtp.made[0].sent[0][0]["Subject"] == "FCP morning digest"


def test_the_operators_notice_says_what_broke_in_its_subject(smtp: type[_FakeSMTP]) -> None:
    """The watchdog's message goes the same way as everything else; what is
    different is that the subject names the quiet jobs, because a phone shows
    the subject and little else."""
    notify.notice(_configured(), "listener: never succeeded", subject="fcp-core: listener quiet")

    message, _from, to_addrs = smtp.made[0].sent[0]
    assert message["Subject"] == "fcp-core: listener quiet"
    assert to_addrs == ["patrick@example.com"]
    assert message.get_content_type() == "text/plain"


def test_a_failed_email_never_carries_the_password(smtp: type[_FakeSMTP]) -> None:
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")
    settings = _settings(
        fcp_smtp_host="smtp.example.net",
        fcp_email_from="fcp@example.net",
        fcp_email_to="patrick@example.com",
        fcp_smtp_user="apikey",
        fcp_smtp_password="hunter2",
    )

    [result] = notify.deliver(settings, "news", title="FCP")

    assert result.sent is False
    assert "hunter2" not in result.error and "hunter2" not in result.describe()
    assert "SMTPAuthenticationError" in result.error


def test_mail_that_is_not_configured_is_not_reported_at_all() -> None:
    assert notify.deliver(_settings(), "news", title="FCP") == []
    assert notify.notice(_settings(), "quiet", subject="fcp-core: bbm quiet") == []


# ---------------------------------------------------------------------------
# The two parts: what makes the message a page as well as a message.


def test_html_rides_beside_the_text_as_the_alternative(smtp: type[_FakeSMTP]) -> None:
    """`multipart/alternative`, text first and HTML second, so a client that
    can draw the page draws it and everything else keeps the words."""
    notify.send_email(
        "two lines\nof news",
        html="<html><body><p>two lines</p></body></html>",
        host="smtp.example.net",
        port=587,
        sender="fcp@example.net",
        recipients=["patrick@example.com"],
        subject="FCP morning digest",
    )

    message, _from, _to = smtp.made[0].sent[0]
    assert message.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in message.iter_parts()]  # type: ignore[union-attr]
    assert parts == ["text/plain", "text/html"]
    assert message.get_body(("plain",)).get_content() == "two lines\nof news\n"  # type: ignore[union-attr]
    assert "<p>two lines</p>" in message.get_body(("html",)).get_content()  # type: ignore[union-attr]


def test_headers_are_carried_and_nothing_is_added(smtp: type[_FakeSMTP]) -> None:
    """`List-Unsubscribe` is the one a transactional mail owes its reader.
    Nothing that tracks is added here or anywhere else."""
    notify.send_email(
        "news",
        headers={"List-Unsubscribe": "<https://fcp.example/account/alerts>"},
        host="smtp.example.net",
        port=587,
        sender="fcp@example.net",
        recipients=["patrick@example.com"],
        subject="FCP morning digest",
    )

    message, _from, _to = smtp.made[0].sent[0]
    assert message["List-Unsubscribe"] == "<https://fcp.example/account/alerts>"


def test_a_message_can_be_built_without_a_connection(smtp: type[_FakeSMTP]) -> None:
    """A preview renders exactly what would go out and opens nothing."""
    built = notify.build_email(
        "news",
        sender="fcp@example.net",
        recipients=["patrick@example.com"],
        subject="FCP morning digest",
        html="<html><body>news</body></html>",
    )

    assert isinstance(built, EmailMessage)
    assert built["Subject"] == "FCP morning digest"
    assert smtp.made == [], "nothing connected"
