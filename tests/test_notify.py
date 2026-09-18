"""Delivery: one POST in whichever shape the manager's service wants, one
plain-SMTP email, and both at once without either costing the other."""

import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
import requests

from app import notify
from app.config import Settings


class _Response:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return _Response(kwargs.pop("_status", 200))

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def test_the_default_shape_is_the_text_itself(posted: list[dict[str, Any]]) -> None:
    """ntfy takes the body as the message and the title as a header."""
    notify.send("https://ntfy.sh/fcp-core-secret", "two lines\nof news", title="FCP")

    [call] = posted
    assert call["url"] == "https://ntfy.sh/fcp-core-secret"
    assert call["data"] == b"two lines\nof news"
    assert call["headers"]["Title"] == "FCP"
    assert call["headers"]["Content-Type"] == "text/plain; charset=utf-8"
    assert call["timeout"] == notify.TIMEOUT_SECONDS
    assert "json" not in call


def test_no_title_sends_no_title_header(posted: list[dict[str, Any]]) -> None:
    notify.send("https://ntfy.sh/topic", "bare")
    assert "Title" not in posted[0]["headers"]


def test_a_chat_id_switches_to_the_telegram_shape(posted: list[dict[str, Any]]) -> None:
    notify.send("https://api.telegram.org/botX/sendMessage", "news", title="FCP", chat_id="42")

    [call] = posted
    assert call["json"] == {"chat_id": "42", "text": "FCP\n\nnews"}
    assert "data" not in call


def test_telegram_without_a_title_sends_the_text_alone(posted: list[dict[str, Any]]) -> None:
    notify.send("https://api.telegram.org/botX/sendMessage", "news", chat_id="42")
    assert posted[0]["json"] == {"chat_id": "42", "text": "news"}


def test_a_refusal_is_raised_rather_than_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed send must leave the events unmarked, so it cannot be quiet."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(500))
    with pytest.raises(requests.HTTPError):
        notify.send("https://ntfy.sh/topic", "news")


# ---------------------------------------------------------------------------
# The email channel. Plain SMTP through the standard library, so the fake is
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
# Both channels at once.


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


def test_both_channels_are_delivered_to(
    posted: list[dict[str, Any]], smtp: type[_FakeSMTP]
) -> None:
    settings = _settings(
        fcp_digest_url="https://ntfy.sh/topic",
        fcp_smtp_host="smtp.example.net",
        fcp_email_from="fcp@example.net",
        fcp_email_to="patrick@example.com",
    )

    results = notify.deliver(settings, "news", title="FCP morning digest")

    assert [(r.channel, r.sent) for r in results] == [("url", True), ("email", True)]
    assert len(posted) == 1
    assert smtp.made[0].sent[0][0]["Subject"] == "FCP morning digest"


def test_one_channel_failing_does_not_stop_the_other(
    monkeypatch: pytest.MonkeyPatch, smtp: type[_FakeSMTP]
) -> None:
    """The point of two channels: a refused push must not cost the manager
    his email, and a broken SMTP login must not cost him his push."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(500))
    settings = _settings(
        fcp_digest_url="https://ntfy.sh/topic",
        fcp_smtp_host="smtp.example.net",
        fcp_email_from="fcp@example.net",
        fcp_email_to="patrick@example.com",
    )

    results = notify.deliver(settings, "news", title="FCP")

    assert [(r.channel, r.sent) for r in results] == [("url", False), ("email", True)]
    assert "HTTPError" in results[0].error
    assert smtp.made[0].sent, "the email still went"
    assert results[0].describe().startswith("url FAILED: ")
    assert results[1].describe() == "email ok"


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


def test_an_unconfigured_channel_is_not_reported_at_all() -> None:
    assert notify.deliver(_settings(), "news", title="FCP") == []
