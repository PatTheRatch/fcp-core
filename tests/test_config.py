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
