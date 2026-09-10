import os

import pytest

from app.config import Settings


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The URL of the disposable test database. Tests never touch DATABASE_URL."""
    url = os.environ.get("TEST_DATABASE_URL") or Settings().test_database_url
    if not url.endswith("_test"):
        raise RuntimeError(f"refusing to run tests against {url!r}: name must end in '_test'")
    return url
