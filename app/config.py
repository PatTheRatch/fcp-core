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

    #: Where the digest and its alerts are delivered (app/notify.py). Unset
    #: means print and deliver nothing. Read here rather than from the
    #: environment alone so a scheduled run picks it up from `.env`, which is
    #: where every other secret on the VPS lives.
    fcp_digest_url: str | None = None
    #: Set as well for Telegram's shape; unset posts ntfy's.
    fcp_digest_chat_id: str | None = None

    @field_validator("fcp_digest_url", "fcp_digest_chat_id", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: str | None) -> str | None:
        """An empty value in `.env` or the environment means unset, not "".

        A scheduled unit that exports the name with nothing after it would
        otherwise deliver to the empty string and fail.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
