"""A member's own alert address: add, verify, list, disable (docs/accounts.md).

Accounts mode, as a signed-in member would use them. What is pinned: an
address is verified by the link logged (no SMTP here) or mailed, and nothing
is sent before that; a target is sealed at rest and never comes back in full
from any route or log line; one member cannot verify or disable another's; a
bad address is refused without repeating it; a refused send leaves no row and
never quotes the mail server; without the secrets key nothing is kept.

**Email and nothing else** (2026-09-22). The Telegram and ntfy kinds are
gone: the page cannot offer them, the route refuses them, and a row migration
`0023` disabled is listed as retired and is not a channel the digest can use.
"""

from __future__ import annotations

import logging
import re
import smtplib
from collections.abc import Callable, Iterator
from typing import Any

import pytest
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
ADDRESS = "Member.Person@Example.com"


def settings_for(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_secrets_key": KEY,
        "fcp_owner_email": "owner@example.com",
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
    """The mail the notify layer was asked to send: a fake transport, so no
    connection is ever opened."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
        self.mails: list[tuple[list[str], str]] = []
        self.fail = fail
        monkeypatch.setattr(notify, "send_email", self.mail)

    def mail(self, text: str, *, recipients: list[str], **_: Any) -> None:
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({"x": (550, b"no such user SECRET-RELAY-PASSWORD")})
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


def _retired(session: Session, user_id: int, kind: str) -> NotificationChannel:
    """A row of a kind that was retired, as migration 0023 leaves one: kept,
    disabled, its target wiped."""
    row = NotificationChannel(
        user_id=user_id,
        kind=kind,
        sealed_target=None,
        masked_target="chat •••4321",
        disabled_at=accounts.now(),
    )
    session.add(row)
    session.commit()
    return row


def test_an_email_channel_is_verified_by_its_link_and_sealed_at_rest(
    sign_in: SignIn, factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="fcp.channels")
    member = sign_in("member@example.com")
    added = member.post("/me/channels", json={"target": ADDRESS})
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
    added = member.post("/me/channels", json={"target": ADDRESS})
    assert added.status_code == 201, added.text
    assert added.json()["sent"] == "a link to m•••@example.com"
    ((to, body),) = sent.mails
    assert to == [ADDRESS.lower()]
    assert "https://fcp.example.test/account/alerts?token=" in body


def test_no_channel_but_email_can_be_added(
    sign_in: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page cannot offer a Telegram chat or an ntfy topic, and the route
    refuses one whatever the page does."""
    Sent(monkeypatch)
    member = sign_in("member@example.com")
    assert member.get("/me/channels").json()["available"] == {"email": True}
    for kind, target in (("telegram", "987654321"), ("ntfy", "my-long-topic")):
        refused = member.post("/me/channels", json={"kind": kind, "target": target})
        assert refused.status_code == 422, (kind, refused.text)
        assert refused.json()["detail"] == "a channel is an email address"


def test_a_retired_row_is_listed_as_stopped_and_is_not_a_channel(
    sign_in: SignIn, factory: sessionmaker[Session]
) -> None:
    """A member whose only channel was a Telegram chat has none now: the
    digest job has nowhere to send, and the page tells him why."""
    member = sign_in("member@example.com")
    with factory() as session:
        user = accounts.get_or_create_user(session, "member@example.com")
        _retired(session, user.id, "telegram")
        assert channels.verified(session, user.id) == []

    listed = member.get("/me/channels").json()["channels"]
    assert [(c["kind"], c["retired"], bool(c["disabled_at"])) for c in listed] == [
        ("telegram", True, True)
    ]


def test_one_member_cannot_verify_or_disable_anothers_channel(
    sign_in: SignIn, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="fcp.channels")
    alice = sign_in("alice@example.com")
    bob = sign_in("bob@example.com")
    added = alice.post("/me/channels", json={"target": ADDRESS}).json()
    token = _logged_link(caplog)
    assert bob.post("/me/channels/verify", json={"token": token}).status_code == 404
    assert bob.delete(f"/me/channels/{added['id']}").status_code == 404
    assert bob.get("/me/channels").json()["channels"] == []
    assert alice.post("/me/channels/verify", json={"token": token}).status_code == 200


def test_disabling_a_channel_wipes_its_target(
    sign_in: SignIn, factory: sessionmaker[Session]
) -> None:
    member = sign_in("member@example.com")
    added = member.post("/me/channels", json={"target": ADDRESS}).json()
    gone = member.delete(f"/me/channels/{added['id']}")
    assert gone.status_code == 200 and gone.json()["disabled_at"] is not None
    with factory() as session:
        row = session.get(NotificationChannel, added["id"])
        assert row is not None and row.sealed_target is None and row.verify_hash is None
        assert channels.verified(session, row.user_id) == []


def test_a_bad_address_is_refused_without_repeating_it(sign_in: SignIn) -> None:
    member = sign_in("member@example.com")
    for target in ("not-an-address-xyzzy", "@xyzzy_handle", "xyzzy@"):
        refused = member.post("/me/channels", json={"target": target})
        assert refused.status_code == 422, (target, refused.text)
        assert "xyzzy" not in refused.text


def test_a_refused_send_adds_nothing_and_never_quotes_the_server(
    app: FastAPI,
    sign_in: SignIn,
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    Sent(monkeypatch, fail=True)
    caplog.set_level(logging.DEBUG)
    app.dependency_overrides[get_settings] = lambda: settings_for(
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
        fcp_public_url="https://fcp.example.test",
    )
    member = sign_in("member@example.com")
    refused = member.post("/me/channels", json={"target": ADDRESS})
    assert refused.status_code == 502
    assert "SECRET-RELAY-PASSWORD" not in refused.text
    assert "SECRET-RELAY-PASSWORD" not in caplog.text
    with factory() as session:
        assert session.scalars(select(NotificationChannel)).all() == []


def test_without_the_secrets_key_nothing_is_kept(app: FastAPI, sign_in: SignIn) -> None:
    app.dependency_overrides[get_settings] = lambda: settings_for(fcp_secrets_key=None)
    member = sign_in("member@example.com")
    assert member.post("/me/channels", json={"target": ADDRESS}).status_code == 503
    assert member.get("/me/channels").json()["available"] == {"email": False}


def test_signed_out_is_refused(app: FastAPI) -> None:
    with TestClient(app) as anon:
        assert anon.get("/me/channels").status_code == 401
        assert anon.post("/me/channels", json={"target": ADDRESS}).status_code == 401
        assert anon.post("/me/channels/verify", json={"token": "x"}).status_code == 401
        assert anon.delete("/me/channels/1").status_code == 401


def test_adding_the_same_address_again_resets_it_rather_than_adding_twice(
    sign_in: SignIn, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="fcp.channels")
    member = sign_in("member@example.com")
    first = member.post("/me/channels", json={"target": ADDRESS}).json()
    old = _logged_link(caplog)
    second = member.post("/me/channels", json={"target": ADDRESS}).json()
    fresh = _logged_link(caplog)
    assert first["id"] == second["id"]
    assert old != fresh
    assert member.post("/me/channels/verify", json={"token": old}).status_code == 404
    assert member.post("/me/channels/verify", json={"token": fresh}).status_code == 200


def test_a_mask_shows_enough_to_tell_two_apart_and_no_more() -> None:
    assert channels.mask("email", "patrick@example.com") == "p•••@example.com"
    assert channels.normalise_target("email", " Patrick@Example.com ") == "patrick@example.com"
    with pytest.raises(channels.ChannelInputError):
        channels.normalise_target("telegram", "987654321")
