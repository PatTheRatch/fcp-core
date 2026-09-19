import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/app")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/app_test")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/app"
    assert settings.test_database_url == "postgresql+psycopg://u:p@db:5432/app_test"


def test_missing_required_values_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    message = str(excinfo.value)
    assert "database_url" in message
    assert "test_database_url" in message


def test_non_psycopg_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/app")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/app_test")

    with pytest.raises(ValidationError, match="postgresql\\+psycopg://"):
        Settings(_env_file=None)


def _urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/app")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/app_test")


def test_auth_mode_defaults_to_single_and_forgives_blanks(monkeypatch: pytest.MonkeyPatch) -> None:
    _urls(monkeypatch)
    monkeypatch.delenv("FCP_AUTH_MODE", raising=False)
    assert Settings(_env_file=None).fcp_auth_mode == "single"
    monkeypatch.setenv("FCP_AUTH_MODE", "")
    assert Settings(_env_file=None).fcp_auth_mode == "single"
    monkeypatch.setenv("FCP_AUTH_MODE", " Accounts ")
    assert Settings(_env_file=None).fcp_auth_mode == "accounts"
    monkeypatch.setenv("FCP_AUTH_MODE", "open")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_short_service_token_is_refused_without_being_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _urls(monkeypatch)
    monkeypatch.setenv("FCP_SERVICE_TOKEN", "hunter2")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert "hunter2" not in str(excinfo.value)
    monkeypatch.setenv("FCP_SERVICE_TOKEN", "")
    assert Settings(_env_file=None).fcp_service_token is None
    monkeypatch.setenv("FCP_SERVICE_TOKEN", "x" * 43)
    assert Settings(_env_file=None).fcp_service_token == "x" * 43
