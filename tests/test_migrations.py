from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.session import make_engine

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _reset_public_schema(database_url: str) -> None:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def test_upgrade_empty_database_to_head(test_database_url: str) -> None:
    _reset_public_schema(test_database_url)
    config = _alembic_config(test_database_url)

    command.upgrade(config, "head")

    head = ScriptDirectory.from_config(config).get_current_head()
    engine = make_engine(test_database_url)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == head
        assert "alembic_version" in inspect(connection).get_table_names()
    engine.dispose()


def test_migrations_match_orm_metadata(test_database_url: str) -> None:
    """Any model added without a migration (or vice versa) fails here."""
    _reset_public_schema(test_database_url)
    command.upgrade(_alembic_config(test_database_url), "head")

    engine = make_engine(test_database_url)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert diff == []


def test_the_injury_reports_migration_goes_down_and_up_again(test_database_url: str) -> None:
    """0022 is reversible: it drops exactly what it adds, and re-adds it."""
    _reset_public_schema(test_database_url)
    config = _alembic_config(test_database_url)
    command.upgrade(config, "head")
    added = {"injury_reports", "injury_report_runs"}

    engine = make_engine(test_database_url)
    with engine.connect() as connection:
        assert added <= set(inspect(connection).get_table_names())
    engine.dispose()

    command.downgrade(config, "0021")
    engine = make_engine(test_database_url)
    with engine.connect() as connection:
        remaining = set(inspect(connection).get_table_names())
        assert not (added & remaining)
        # Everything 0021 built is untouched.
        assert "player_platform_ids" in remaining
        assert MigrationContext.configure(connection).get_current_revision() == "0021"
    engine.dispose()

    command.upgrade(config, "head")
    engine = make_engine(test_database_url)
    with engine.connect() as connection:
        assert added <= set(inspect(connection).get_table_names())
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()
