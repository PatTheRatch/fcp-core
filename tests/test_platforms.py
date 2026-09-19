"""Platform-neutral identity (docs/platforms.md).

The load-bearing test is `test_every_row_the_ingest_writes_carries_its_platform_id`:
after an ingest, every league, owner, team, transaction and player carries a
platform id equal to its ESPN id as text, so the two cannot have drifted.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    League,
    Owner,
    Player,
    PlayerPlatformId,
    Team,
    Transaction,
)
from app.db.session import make_engine, make_session_factory
from app.ingest import get_or_create_player, ingest_season
from app.memberships import get_or_create_league
from app.platforms import (
    ESPN,
    PLATFORMS,
    league_by_platform_id,
    player_by_platform_id,
    require_platform,
)
from tests.fakes import (
    BOX_LINE,
    attach_draft,
    attach_transactions,
    fake_box,
    fake_card,
    fake_pick,
    fake_player,
    fake_team,
    fake_transaction,
    league_with_days,
    owner_dict,
    tx_item,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="module")
def session_factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    command.upgrade(_alembic_config(test_database_url), "head")
    engine = make_engine(test_database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        session.execute(text("TRUNCATE leagues, players, owners RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


def _league(season: int = 2026) -> Any:
    """Every kind of row: owners (one shared), rostered players, lineups, a
    transaction naming a player nobody rostered, and a drafted one likewise."""
    shared = owner_dict("{GUID-SHARED}")
    home = fake_team(3, "A", owners=[owner_dict("{GUID-HOME}"), shared])
    away = fake_team(21, "B", owners=[shared])
    starter = fake_player(100, "Starter", slot="PG")
    sitter = fake_player(101, "Sitter", slot="BE")
    espn = league_with_days(
        teams=[home, away],
        boxes={1: [fake_box(home, away, home_lineup=[starter], away_lineup=[sitter])]},
        days={
            1: {
                day: [fake_box(home, away, home_lineup=[starter], away_lineup=[sitter])]
                for day in (1, 2, 3)
            }
        },
        windows={1: ["1", "2", "3"]},
        reg_season_count=1,
        matchup_period_count=1,
        cards={
            100: fake_card(100, "Starter", {1: BOX_LINE}),
            101: fake_card(101, "Sitter", {1: BOX_LINE}),
        },
    )
    espn.year = season
    attach_draft(espn, [fake_pick(1, 1, 999, "Drafted Only", team=home, nominated_by=away)])
    return attach_transactions(
        espn,
        {
            2: [
                fake_transaction(
                    "tx-claim",
                    team_id=3,
                    bid=5,
                    items=[tx_item(777, "ADD", to_team=3), tx_item(101, "DROP", from_team=21)],
                ),
                fake_transaction(f"tx-uphold-{season}", team_id=21, type_="TRADE_UPHOLD"),
            ]
        },
        names={777: "Transaction Only", 101: "Sitter"},
    )


def _assert_every_platform_id_is_its_espn_id(session: Session) -> None:
    leagues = session.scalars(select(League)).all()
    owners = session.scalars(select(Owner)).all()
    teams = session.scalars(select(Team)).all()
    transactions = session.scalars(select(Transaction)).all()
    players = session.scalars(select(Player)).all()
    assert leagues and owners and teams and transactions and players, "fixture wrote nothing"

    for league in leagues:
        assert (league.platform, league.platform_league_id) == (ESPN, str(league.espn_league_id))
    for owner in owners:
        assert (owner.platform, owner.platform_owner_id) == (ESPN, owner.espn_owner_id)
    for team in teams:
        assert team.platform_team_id == str(team.espn_team_id)
    for transaction in transactions:
        assert transaction.platform_transaction_id == str(transaction.espn_transaction_id)
    for player in players:
        assert [(m.platform, m.platform_player_id) for m in player.platform_ids] == [
            (ESPN, str(player.espn_player_id))
        ], player.name


def test_every_row_the_ingest_writes_carries_its_platform_id(session: Session) -> None:
    ingest_season(session, _league(2026))
    session.commit()
    # Again, and a second season: the update path, and owners met twice.
    ingest_season(session, _league(2026))
    ingest_season(session, _league(2027))
    session.commit()
    session.expire_all()

    names = {p.name for p in session.scalars(select(Player)).all()}
    assert {"Starter", "Sitter", "Transaction Only", "Drafted Only"} <= names
    assert len(session.scalars(select(Owner)).all()) == 2
    _assert_every_platform_id_is_its_espn_id(session)


def test_the_listener_path_maps_a_new_player(session: Session) -> None:
    """The listener creates players through `get_or_create_player` too."""
    get_or_create_player(session, 4242, "Listener Find")
    session.commit()

    found = player_by_platform_id(session, ESPN, 4242)
    assert found is not None and found.name == "Listener Find"


def test_a_writer_naming_only_the_espn_id_still_gets_the_platform_id(session: Session) -> None:
    """What keeps every existing test and script working: the model derives it."""
    league = League(espn_league_id=55)
    session.add(league)
    session.flush()
    team_owner = Owner(espn_owner_id="{G}")
    session.add(team_owner)
    session.flush()

    assert (league.platform, league.platform_league_id) == (ESPN, "55")
    assert (team_owner.platform, team_owner.platform_owner_id) == (ESPN, "{G}")

    # The memberships path is a Core insert, not the ORM.
    connected = get_or_create_league(session, 56)
    assert (connected.platform, connected.platform_league_id) == (ESPN, "56")


def test_resolvers_find_by_platform_id(session: Session) -> None:
    ingest_season(session, _league())
    session.commit()
    league = session.scalars(select(League)).one()

    assert league_by_platform_id(session, ESPN, league.espn_league_id) == league
    assert league_by_platform_id(session, ESPN, str(league.espn_league_id)) == league
    assert league_by_platform_id(session, ESPN, "no-such-league") is None

    drafted = player_by_platform_id(session, ESPN, "999")
    assert drafted is not None and drafted.espn_player_id == 999
    assert player_by_platform_id(session, ESPN, 123456) is None


def test_an_unknown_platform_is_refused_not_looked_up(session: Session) -> None:
    assert require_platform(ESPN) == ESPN
    with pytest.raises(ValueError, match="unknown platform 'yahoo'"):
        league_by_platform_id(session, "yahoo", "1")
    with pytest.raises(ValueError, match="unknown platform"):
        player_by_platform_id(session, "sleeper", "1")


def test_platforms_matches_what_the_database_accepts(session: Session) -> None:
    """`PLATFORMS` and the CHECK constraints name the same platforms."""
    player = Player(espn_player_id=1, name="P")
    session.add(player)
    session.flush()
    for platform in PLATFORMS:
        with session.begin_nested():
            session.add(PlayerPlatformId(player=player, platform=platform, platform_player_id="x"))
    session.rollback()

    for table in ("leagues", "owners", "player_platform_ids", "league_connections"):
        checks = inspect(session.connection()).get_check_constraints(table)
        (named,) = [c["sqltext"] for c in checks if c["name"] == f"ck_{table}_platform"]
        for platform in PLATFORMS:
            assert f"'{platform}'" in named, (table, named)

    player = Player(espn_player_id=2, name="Q")
    session.add(player)
    session.flush()
    with pytest.raises(IntegrityError, match="ck_player_platform_ids_platform"):
        session.add(PlayerPlatformId(player=player, platform="yahoo", platform_player_id="2"))
        session.flush()
    session.rollback()

    # A league still carrying an ESPN id cannot claim another platform either.
    with pytest.raises(IntegrityError, match="ck_leagues_espn_id"):
        session.add(League(espn_league_id=9, platform="yahoo", platform_league_id="9"))
        session.flush()


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        (lambda: League(espn_league_id=7, platform_league_id="8"), "ck_leagues_espn_id"),
        (lambda: Owner(espn_owner_id="{A}", platform_owner_id="{B}"), "ck_owners_espn_id"),
    ],
)
def test_the_database_refuses_a_platform_id_that_drifts(
    session: Session, row: Any, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        session.add(row())
        session.flush()


def test_a_team_or_transaction_that_drifts_is_refused(session: Session) -> None:
    ingest_season(session, _league())
    session.commit()
    team = session.scalars(select(Team)).first()
    transaction = session.scalars(select(Transaction)).first()
    assert team is not None and transaction is not None

    team.platform_team_id = "not-its-id"
    with pytest.raises(IntegrityError, match="ck_teams_espn_id"):
        session.flush()
    session.rollback()

    transaction.platform_transaction_id = "not-its-id"
    with pytest.raises(IntegrityError, match="ck_transactions_espn_id"):
        session.flush()


def test_one_id_per_player_per_platform(session: Session) -> None:
    get_or_create_player(session, 10, "Ten")
    other = get_or_create_player(session, 11, "Eleven")
    session.commit()

    with pytest.raises(IntegrityError, match="uq_player_platform_ids_player"):
        other.platform_ids.append(PlayerPlatformId(platform=ESPN, platform_player_id="12"))
        session.flush()
    session.rollback()

    with pytest.raises(IntegrityError, match="uq_player_platform_ids_platform_id"):
        session.add(Player(espn_player_id=13, name="Thirteen"))
        session.flush()
        thirteen = session.scalars(select(Player).where(Player.espn_player_id == 13)).one()
        session.add(PlayerPlatformId(player=thirteen, platform=ESPN, platform_player_id="10"))
        session.flush()


def test_the_migration_backfills_existing_rows_and_downgrades_cleanly(
    session: Session, test_database_url: str
) -> None:
    """Rows written before 0021 get their platform ids from the ESPN ones.

    Written at head, taken down to 0020 (which drops only what 0021 added),
    and brought back up: the backfill has to reproduce what the ingest wrote.
    Last in the module, and leaves the schema at head.
    """
    ingest_season(session, _league())
    session.commit()
    session.close()
    config = _alembic_config(test_database_url)

    command.downgrade(config, "0020")
    engine = make_engine(test_database_url)
    with engine.connect() as connection:
        schema = inspect(connection)
        assert "player_platform_ids" not in schema.get_table_names()
        for table in ("leagues", "owners", "teams", "transactions"):
            columns = {c["name"] for c in schema.get_columns(table)}
            assert not {c for c in columns if c.startswith("platform")}, table
        assert connection.scalar(text("select count(*) from players")) == 4
    engine.dispose()

    command.upgrade(config, "head")
    engine = make_engine(test_database_url)
    with make_session_factory(engine)() as fresh:
        _assert_every_platform_id_is_its_espn_id(fresh)
    engine.dispose()
