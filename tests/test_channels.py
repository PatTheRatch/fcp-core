"""A member's own alert channels: add, verify, list, disable (docs/accounts.md).

Accounts mode, as a signed-in member would use them. What is pinned: an
email address is verified by the link logged (no SMTP here) or mailed, and a
Telegram chat by the code in its test message; nothing is sent before that;
a target is sealed at rest and never comes back in full from any route or
log line; one member cannot verify or disable another's; a bad target is
refused without repeating it; a Telegram refusal, whose text quotes the
bot's token, reaches neither the answer nor the log; without the secrets key
nothing is kept.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, channels, notify, secrets_box
from app.api import access
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.db.models import NotificationChannel
from app.main import create_app
from app.secrets_box import new_key

KEY = new_key()
BOT = "https://api.telegram.test/bot123456:SECRET-BOT-TOKEN/sendMessage"
ADDRESS = "Member.Person@Example.com"
CHAT = "987654321"


def settings_for(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_secrets_key": KEY,
        "fcp_owner_email": "owner@example.com",
        "fcp_telegram_bot_url": BOT,
        "fcp_digest_url": None,
        "fcp_digest_chat_id": None,
        "fcp_smtp_host": None,
        "fcp_email_from": None,
        "fcp_public_url": None,
        "espn_league_id": None,
        "fcp_tracked_team_id": None,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


@pytest.fixture
def factory(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        session.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))
        session.commit()
    for name in ("fcp", "fcp.channels"):
        logging.getLogger(name).disabled = False
    yield scoring_factory


@pytest.fixture
def app(factory: sessionmaker[Session]) -> Iterator[FastAPI]:
    built = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: settings_for()
    yield built
    built.dependency_overrides.clear()


SignIn = Callable[[str], TestClient]


@pytest.fixture
def sign_in(app: FastAPI, factory: sessionmaker[Session]) -> Iterator[SignIn]:
    """A client with a live session for this address (the link flow itself
    is tests/test_access.py's)."""
    opened: list[TestClient] = []

    def make(email: str) -> TestClient:
        with factory() as session:
            user = accounts.get_or_create_user(session, email)
            token = accounts.start_session(session, user.id)
            session.commit()
        client = TestClient(app)
        client.cookies.set(access.COOKIE, token)
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.close()


class Sent:
    """The messages the notify layer was asked to send."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail_post: bool = False) -> None:
        self.posts: list[tuple[str, str, str | None]] = []
        self.mails: list[tuple[list[str], str]] = []
        self.fail_post = fail_post
        monkeypatch.setattr(notify, "send", self.post)
        monkeypatch.setattr(notify, "send_email", self.mail)

    def post(
        self, url: str, text: str, *, title: str | None = None, chat_id: str | None = None
    ) -> None:
        if self.fail_post:
            raise requests.HTTPError(f"400 Client Error: Bad Request for url: {url}")
        self.posts.append((url, text, chat_id))

    def mail(self, text: str, *, recipients: list[str], **_: Any) -> None:
        self.mails.append((list(recipients), text))


def _logged_link(caplog: pytest.LogCaptureFixture) -> str:
    found = [
        r.getMessage()
        for r in caplog.records
        if r.name == "fcp.channels" and "link" in r.getMessage()
    ]
    assert found, "the dev link was logged"
    match = re.search(r"/account/alerts\?token=(\S+)", found[-1])
    assert match is not None
    return match.group(1)


def _code(text_sent: str) -> str:
    match = re.search(r"code is ([A-Z0-9]{4}-[A-Z0-9]{4})", text_sent)
    assert match is not None
    return match.group(1)


def test_an_email_channel_is_verified_by_its_link_and_sealed_at_rest(
    sign_in: SignIn, factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="fcp.channels")
    member = sign_in("member@example.com")
    added = member.post("/me/channels", json={"kind": "email", "target": ADDRESS})
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["kind"] == "email" and body["verified"] is False
    assert body["masked"] == "m•••@example.com"
    assert "member.person" not in added.text.lower()

    token = _logged_link(caplog)
    assert token not in added.text, "the link is never in a response"
    listed = member.get("/me/channels").json()
    assert [c["verified"] for c in listed["channels"]] == [False]

    confirmed = member.post("/me/channels/verify", json={"token": token})
    assert confirmed.status_code == 200 and confirmed.json()["verified"] is True
    again = member.post("/me/channels/verify", json={"token": token})
    assert again.status_code == 404, "a link works once"

    with factory() as session:
        row = session.scalars(select(NotificationChannel)).one()
        assert row.sealed_target is not None
        assert "member.person" not in row.sealed_target.lower()
        assert secrets_box.open_(row.sealed_target, settings_for()) == ADDRESS.lower()
        assert row.verify_hash is None
    everything = member.get("/me/channels").text.lower()
    assert "member.person" not in everything
    assert "member.person@example.com" not in caplog.text.lower()


def test_with_smtp_the_link_is_mailed_on_the_public_url(
    app: FastAPI, sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = Sent(monkeypatch)
    app.dependency_overrides[get_settings] = lambda: settings_for(
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
        fcp_public_url="https://fcp.example.test",
    )
    member = sign_in("member@example.com")
    added = member.post("/me/channels", json={"kind": "email", "target": ADDRESS})
    assert added.status_code == 201, added.text
    assert added.json()["sent"] == "a link to m•••@example.com"
    ((to, body),) = sent.mails
    assert to == [ADDRESS.lower()]
    assert "https://fcp.example.test/account/alerts?token=" in body


def test_a_telegram_channel_is_verified_by_the_code_in_its_test_message(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = Sent(monkeypatch)
    member = sign_in("member@example.com")
    added = member.post("/me/channels", json={"kind": "telegram", "target": f" {CHAT} "})
    assert added.status_code == 201, added.text
    assert added.json()["masked"] == "chat •••4321"
    ((url, message, chat_id),) = sent.posts
    assert url == BOT and chat_id == CHAT
    code = _code(message)
    assert code not in added.text and CHAT not in added.text

    wrong = member.post("/me/channels/verify", json={"token": "AAAA-AAAA"})
    assert wrong.status_code == 404
    typed = code.replace("-", "").lower()
    confirmed = member.post("/me/channels/verify", json={"token": typed})
    assert confirmed.status_code == 200 and confirmed.json()["verified"] is True


def test_one_member_cannot_verify_or_disable_anothers_channel(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = Sent(monkeypatch)
    alice = sign_in("alice@example.com")
    bob = sign_in("bob@example.com")
    added = alice.post("/me/channels", json={"kind": "telegram", "target": CHAT}).json()
    code = _code(sent.posts[-1][1])
    assert bob.post("/me/channels/verify", json={"token": code}).status_code == 404
    assert bob.delete(f"/me/channels/{added['id']}").status_code == 404
    assert bob.get("/me/channels").json()["channels"] == []
    assert alice.post("/me/channels/verify", json={"token": code}).status_code == 200


def test_disabling_a_channel_wipes_its_target(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> None:
    Sent(monkeypatch)
    member = sign_in("member@example.com")
    added = member.post("/me/channels", json={"kind": "ntfy", "target": "my-long-topic"}).json()
    assert added["masked"] == "ntfy.sh/my•••"
    gone = member.delete(f"/me/channels/{added['id']}")
    assert gone.status_code == 200 and gone.json()["disabled_at"] is not None
    with factory() as session:
        row = session.get(NotificationChannel, added["id"])
        assert row is not None and row.sealed_target is None and row.verify_hash is None
        assert channels.verified(session, row.user_id) == []


def test_a_bad_target_is_refused_without_repeating_it(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    Sent(monkeypatch)
    member = sign_in("member@example.com")
    for kind, target in (
        ("email", "not-an-address-xyzzy"),
        ("telegram", "@xyzzy_handle"),
        ("ntfy", "https://evil.example/xyzzy"),
        ("ntfy", "http://ntfy.sh/xyzzy"),
        ("carrier-pigeon", "xyzzy"),
    ):
        refused = member.post("/me/channels", json={"kind": kind, "target": target})
        assert refused.status_code == 422, (kind, refused.text)
        assert "xyzzy" not in refused.text


def test_a_refused_test_message_adds_nothing_and_never_quotes_the_bot(
    sign_in: SignIn,
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    Sent(monkeypatch, fail_post=True)
    caplog.set_level(logging.DEBUG)
    member = sign_in("member@example.com")
    refused = member.post("/me/channels", json={"kind": "telegram", "target": CHAT})
    assert refused.status_code == 502
    assert "SECRET-BOT-TOKEN" not in refused.text and "SECRET-BOT-TOKEN" not in caplog.text
    with factory() as session:
        assert session.scalars(select(NotificationChannel)).all() == []


def test_without_the_secrets_key_nothing_is_kept(
    app: FastAPI, sign_in: SignIn, factory: sessionmaker[Session]
) -> None:
    app.dependency_overrides[get_settings] = lambda: settings_for(fcp_secrets_key=None)
    member = sign_in("member@example.com")
    assert member.post("/me/channels", json={"kind": "email", "target": ADDRESS}).status_code == 503
    assert member.get("/me/channels").json()["available"] == {
        "email": False,
        "telegram": False,
        "ntfy": False,
    }


def test_telegram_is_offered_only_with_a_bot(app: FastAPI, sign_in: SignIn) -> None:
    app.dependency_overrides[get_settings] = lambda: settings_for(fcp_telegram_bot_url=None)
    member = sign_in("member@example.com")
    assert member.get("/me/channels").json()["available"]["telegram"] is False
    assert member.post("/me/channels", json={"kind": "telegram", "target": CHAT}).status_code == 503
    # The digest's own Telegram URL is the bot when it is Telegram's.
    app.dependency_overrides[get_settings] = lambda: settings_for(
        fcp_telegram_bot_url=None, fcp_digest_url=BOT, fcp_digest_chat_id="1"
    )
    assert member.get("/me/channels").json()["available"]["telegram"] is True


def test_signed_out_is_refused(app: FastAPI) -> None:
    with TestClient(app) as anon:
        assert anon.get("/me/channels").status_code == 401
        assert (
            anon.post("/me/channels", json={"kind": "email", "target": ADDRESS}).status_code == 401
        )
        assert anon.post("/me/channels/verify", json={"token": "x"}).status_code == 401
        assert anon.delete("/me/channels/1").status_code == 401


def test_adding_the_same_target_again_resets_it_rather_than_adding_twice(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = Sent(monkeypatch)
    member = sign_in("member@example.com")
    first = member.post("/me/channels", json={"kind": "telegram", "target": CHAT}).json()
    old_code = _code(sent.posts[-1][1])
    second = member.post("/me/channels", json={"kind": "telegram", "target": CHAT}).json()
    new_code = _code(sent.posts[-1][1])
    assert first["id"] == second["id"]
    assert member.post("/me/channels/verify", json={"token": old_code}).status_code == 404
    assert member.post("/me/channels/verify", json={"token": new_code}).status_code == 200


def test_masks_show_enough_to_tell_apart_and_no_more() -> None:
    assert channels.mask("email", "patrick@example.com") == "p•••@example.com"
    assert channels.mask("telegram", "-1001234567890") == "chat •••7890"
    assert channels.mask("ntfy", "https://ntfy.sh/fcp-alerts-xyz") == "ntfy.sh/fc•••"
    assert channels.normalise_target("ntfy", "https://ntfy.sh/topic_1") == "https://ntfy.sh/topic_1"
    assert channels.normalise_target("ntfy", "topic_1") == "https://ntfy.sh/topic_1"
