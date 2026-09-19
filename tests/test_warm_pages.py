"""The morning warm-up asks for exactly the two routes the pages read."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest
import requests

WARM = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "warm_pages.py"))


def test_urls_are_the_pages_routes_for_one_team_and_day() -> None:
    urls = WARM["urls_for"]("http://100.1.2.3:8001/", 3853870, 2026, 3, 100)
    assert urls == [
        "http://100.1.2.3:8001/leagues/3853870/seasons/2026/teams/3/pickups/stream?today=100",
        "http://100.1.2.3:8001/leagues/3853870/seasons/2026/teams/3/pickups/season?today=100",
    ]
    assert WARM["urls_for"]("http://h", 1, 2027, 3, None)[0].endswith("/pickups/stream")


def test_a_dead_api_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, timeout: Any) -> Any:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", refuse)
    out = WARM["warm"](["http://h/x"])
    assert out[0][0] == "http://h/x" and out[0][1] is None


def test_each_url_is_asked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    class Answer:
        status_code = 200

    def ok(url: str, timeout: Any) -> Any:
        asked.append(url)
        return Answer()

    monkeypatch.setattr(requests, "get", ok)
    out = WARM["warm"](["http://h/a", "http://h/b"])
    assert asked == ["http://h/a", "http://h/b"] and [s for _, s, _ in out] == [200, 200]


def test_the_service_token_goes_in_a_header_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the warm-up keeps working when the API enforces accounts."""
    seen: list[dict[str, Any]] = []

    class Answer:
        status_code = 200

    def ok(url: str, timeout: Any, **kwargs: Any) -> Any:
        seen.append({"url": url, **kwargs})
        return Answer()

    monkeypatch.setattr(requests, "get", ok)
    WARM["warm"](["http://h/a"], "sekret")
    WARM["warm"](["http://h/b"])
    assert seen[0]["headers"] == {"Authorization": "Bearer sekret"}
    assert "sekret" not in seen[0]["url"]
    assert "headers" not in seen[1], "no token, no header: single mode as before"
