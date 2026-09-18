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

    #: The email channel (app/notify.py). Plain SMTP, so any transactional
    #: provider's endpoint will do. Configured when the host, the sender and
    #: at least one recipient are all set; anything less delivers no mail.
    #: `fcp_email_to` is one address or several, comma separated.
    fcp_email_to: str | None = None
    fcp_email_from: str | None = None
    fcp_smtp_host: str | None = None
    #: 587 is submission with STARTTLS, which is what nearly every provider
    #: wants; 465 is implicit TLS and `notify.send_email` switches on it.
    fcp_smtp_port: int = 587
    fcp_smtp_user: str | None = None
    #: Lives only in `.env` on the VPS. Never logged, never printed, and
    #: never put in an error message.
    fcp_smtp_password: str | None = None

    #: Where this API answers, as the scheduled passes reach it
    #: (`scripts/warm_pages.py`): the tailnet address on the VPS. Unset means
    #: the morning pass warms nothing, and the first look at a page each day
    #: waits for the report to be built.
    fcp_api_url: str | None = None

    @field_validator(
        "fcp_digest_url",
        "fcp_digest_chat_id",
        "fcp_email_to",
        "fcp_email_from",
        "fcp_smtp_host",
        "fcp_smtp_user",
        "fcp_smtp_password",
        "fcp_api_url",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: str | None) -> str | None:
        """An empty value in `.env` or the environment means unset, not "".

        A scheduled unit that exports the name with nothing after it would
        otherwise deliver to the empty string and fail.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("fcp_smtp_port", mode="before")
    @classmethod
    def _blank_port_is_the_default(cls, value: object) -> object:
        """`FCP_SMTP_PORT=` with nothing after it is the default, not a crash."""
        if isinstance(value, str) and not value.strip():
            return 587
        return value

    @property
    def email_recipients(self) -> list[str]:
        """The addresses `fcp_email_to` names, blanks dropped."""
        raw = self.fcp_email_to or ""
        return [address.strip() for address in raw.split(",") if address.strip()]

    @property
    def email_configured(self) -> bool:
        """Whether there is enough to send mail: a host, a sender, a recipient."""
        return bool(self.fcp_smtp_host and self.fcp_email_from and self.email_recipients)

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
