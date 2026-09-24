import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def single_mode() -> Iterator[None]:
    """Every test starts in single mode, whatever a local `.env` says.

    The API tests were written for the tailnet API, where every request is
    the owner; tests/test_access.py switches to accounts mode itself, by
    overriding the settings dependency on its own app.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FCP_AUTH_MODE", "single")
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The URL of the disposable test database. Tests never touch DATABASE_URL."""
    url = os.environ.get("TEST_DATABASE_URL") or Settings().test_database_url
    if not url.endswith("_test"):
        raise RuntimeError(f"refusing to run tests against {url!r}: name must end in '_test'")
    return url


# The scoring package's database tests: a fresh schema per module.
@pytest.fixture(scope="module")
def scoring_factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")
    engine = make_engine(test_database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def scoring_session(scoring_factory: sessionmaker[Session]) -> Iterator[Session]:
    with scoring_factory() as session:
        # Both roots have to be named: cascading from `leagues` reaches
        # `league_seasons` and everything under them, but `players` and `owners`
        # are global by design and have no foreign key back to a league, so
        # their rows survived into the next test.
        session.execute(text("TRUNCATE leagues, players, owners RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


#: Every setting that can make `Settings.email_configured` true. An empty value
#: means unset (app/config.py), and the environment outranks `.env`, so blanking
#: these hides a real `.env` from every test.
_MAIL_SETTINGS = (
    "FCP_SMTP_HOST",
    "FCP_SMTP_PORT",
    "FCP_SMTP_USER",
    "FCP_SMTP_PASSWORD",
    "FCP_EMAIL_FROM",
    "FCP_EMAIL_TO",
    "FCP_OWNER_EMAIL",
)


class _NoRealMail:
    """A test opened an SMTP connection without installing a fake."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError(
            "a test tried to open a real SMTP connection; use the `smtp` fixture "
            "(tests/test_notify.py) or an Outbox"
        )


@pytest.fixture(scope="session", autouse=True)
def no_real_mail() -> Iterator[None]:
    """No test sends a real email, whatever a local `.env` says.

    On 2026-09-23 the suite mailed the owner seven times in an evening: a
    test built `Settings()` with no mail overrides, a developer's `.env`
    supplied a real SMTP login, and `notify.deliver(..., "news", title="FCP")`
    went out for real on every run. Two guards, because either alone has a
    hole: the mail settings are blanked for the whole session, and
    `smtplib.SMTP` is a tripwire that a test replaces on purpose (the `smtp`
    fixture) or not at all.
    """
    import smtplib

    with pytest.MonkeyPatch.context() as patch:
        for name in _MAIL_SETTINGS:
            patch.setenv(name, "")
        patch.setattr(smtplib, "SMTP", _NoRealMail)
        patch.setattr(smtplib, "SMTP_SSL", _NoRealMail)
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()
