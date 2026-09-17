"""Delivery: one POST, in whichever shape the manager's service wants."""

from typing import Any

import pytest
import requests

from app import notify


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
