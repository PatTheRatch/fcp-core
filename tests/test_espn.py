import pytest
from pydantic import ValidationError

from app.espn import ESPNSettings


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
    for field in ("espn_league_id", "espn_season", "espn_swid", "espn_s2"):
        assert field in message
