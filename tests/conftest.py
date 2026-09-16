import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import make_engine, make_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent


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
        session.execute(text("TRUNCATE leagues, players, owners RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()
