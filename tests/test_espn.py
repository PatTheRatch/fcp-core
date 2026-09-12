from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app import espn as espn_module
from app.espn import ESPNSettings, current_season, fetch_current_league, resolve_season


def test_espn_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESPN_LEAGUE_ID", "12345")
    monkeypatch.setenv("ESPN_SEASON", "2025")
    monkeypatch.setenv("ESPN_SWID", "{fake-swid}")
    monkeypatch.setenv("ESPN_S2", "fake-s2-cookie")

    settings = ESPNSettings(_env_file=None)

    assert settings.espn_league_id == 12345
    assert settings.espn_season == 2025


def test_missing_required_values_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ESPN_LEAGUE_ID", "ESPN_SEASON", "ESPN_SWID", "ESPN_S2"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as excinfo:
        ESPNSettings(_env_file=None)

    message = str(excinfo.value)
    for field in ("espn_league_id", "espn_swid", "espn_s2"):
        assert field in message
    assert "espn_season" not in message, "the season is derived, not required"


def test_the_season_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ESPN_LEAGUE_ID", "ESPN_SWID", "ESPN_S2"):
        monkeypatch.setenv(name, "1")
    monkeypatch.delenv("ESPN_SEASON", raising=False)

    assert ESPNSettings(_env_file=None).espn_season is None


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 4, 13), 2026),  # the 2025-26 season, ending
        (date(2026, 9, 12), 2026),  # the gap between seasons
        (date(2026, 9, 30), 2026),  # last day before the rollover
        (date(2026, 10, 1), 2027),  # the rollover
        (date(2026, 12, 25), 2027),  # mid 2026-27 season
        (date(2027, 4, 13), 2027),  # the 2026-27 season, ending
    ],
)
def test_the_season_follows_the_calendar(today: date, expected: int) -> None:
    """ESPN labels a season by the year it ends in, turning over in October."""
    assert current_season(today) == expected


def test_a_configured_season_overrides_the_calendar() -> None:
    pinned = ESPNSettings(
        _env_file=None, espn_league_id=1, espn_swid="x", espn_s2="y", espn_season=2023
    )
    assert resolve_season(pinned, date(2026, 12, 25)) == 2023


def test_without_a_configured_season_the_calendar_wins() -> None:
    free = ESPNSettings(_env_file=None, espn_league_id=1, espn_swid="x", espn_s2="y")
    assert resolve_season(free, date(2026, 12, 25)) == 2027


def _settings(season: int | None = None) -> ESPNSettings:
    return ESPNSettings(
        _env_file=None, espn_league_id=1, espn_swid="x", espn_s2="y", espn_season=season
    )


def test_the_new_season_is_used_as_soon_as_espn_has_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[int] = []

    def fake_fetch(settings: ESPNSettings, season: int | None = None) -> Any:
        asked.append(season or 0)
        return f"league-{season}"

    monkeypatch.setattr(espn_module, "fetch_league", fake_fetch)

    assert fetch_current_league(_settings(), date(2026, 10, 1)) == "league-2027"
    assert asked == [2027]


def test_it_falls_back_a_year_when_the_new_season_does_not_exist_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ESPN creates a season shortly before play starts, not on 1 October."""
    asked: list[int] = []

    def fake_fetch(settings: ESPNSettings, season: int | None = None) -> Any:
        asked.append(season or 0)
        if season == 2027:
            raise RuntimeError("404 from ESPN")
        return f"league-{season}"

    monkeypatch.setattr(espn_module, "fetch_league", fake_fetch)

    assert fetch_current_league(_settings(), date(2026, 10, 1)) == "league-2026"
    assert asked == [2027, 2026], "tries the new season first, then the old"


def test_a_pinned_season_skips_the_derivation_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[int] = []

    def fake_fetch(settings: ESPNSettings, season: int | None = None) -> Any:
        asked.append(season or 0)
        return f"league-{season}"

    monkeypatch.setattr(espn_module, "fetch_league", fake_fetch)

    assert fetch_current_league(_settings(2019), date(2026, 12, 25)) == "league-2019"
    assert asked == [2019]
