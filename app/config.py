"""Typed process configuration.

Every required value must be present in the environment (or a local ``.env``
file). There are no defaults for database URLs: a missing value raises at
startup rather than silently pointing at the wrong database.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

POSTGRES_SCHEME = "postgresql+psycopg://"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    test_database_url: str

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _must_use_psycopg(cls, value: str) -> str:
        if not value.startswith(POSTGRES_SCHEME):
            raise ValueError(f"must start with {POSTGRES_SCHEME!r} (SQLAlchemy + psycopg 3)")
        return value


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Raises if a required value is missing."""
    return Settings()
