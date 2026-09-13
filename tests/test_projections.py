"""Seasons whose projections are not forecasts.

For 2023, ESPN's player cards carry a rest-of-season projection captured
forty games in, not a preseason forecast. Nothing on the row says so. These
tests pin the rule that detects that from the numbers, the registry that
records it, and the three places that consult the registry: the projection
loader, the availability measure, and the projection-gaps view.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.db.models import League, LeagueSeason, Player, PlayerSeasonStat
from app.db.session import make_engine, make_session_factory
from app.draft import availability, pool
from app.draft.projections import (
    MIN_PAIRS,
    UNUSABLE_PROJECTIONS,
    looks_like_snapshot,
    projection_problem,
    usable,
)
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent


# -- the rule, on numbers alone -------------------------------------------


def test_a_mid_season_snapshot_is_low_and_tight() -> None:
    """2023's shape: every player's projected games near 0.59 of actual."""
    actual = [float(60 + (i % 20)) for i in range(60)]
    projected = [a * (0.55 + 0.08 * ((i * 7) % 10) / 10) for i, a in enumerate(actual)]
    assert looks_like_snapshot(projected, actual)


def test_a_real_forecast_is_near_one_and_wide() -> None:
    """2024's shape: a median near one, and injuries spread it widely."""
    actual = [float(20 + (i * 13) % 62) for i in range(60)]
    projected = [70.0 + (i % 12) for i in range(60)]
    assert not looks_like_snapshot(projected, actual)


def test_low_alone_is_not_a_snapshot() -> None:
    """A year of many injuries is low but wide: unforeseen, so a forecast."""
    actual = [float(15 + (i * 17) % 60) for i in range(60)]
    projected = [70.0] * 60
    ratios = sorted(p / a for p, a in zip(projected, actual, strict=True))
    assert ratios[len(ratios) // 2] > 1.0 or ratios[-1] - ratios[0] > 1.0
    assert not looks_like_snapshot(projected, actual)


def test_too_few_pairs_say_nothing() -> None:
    projected = [40.0] * (MIN_PAIRS - 1)
    actual = [70.0] * (MIN_PAIRS - 1)
    assert not looks_like_snapshot(projected, actual)


def test_players_without_games_are_ignored_not_divided_by() -> None:
    projected = [40.0] * 40 + [0.0, 40.0]
    actual = [70.0] * 40 + [70.0, 0.0]
    assert looks_like_snapshot(projected, actual)


# -- the registry ----------------------------------------------------------


def test_the_registry_names_both_known_seasons_with_reasons() -> None:
    assert set(UNUSABLE_PROJECTIONS) == {2020, 2023}
    assert "COVID" in projection_problem(2020)  # type: ignore[operator]
    assert "mid-season" in projection_problem(2023)  # type: ignore[operator]
    assert projection_problem(2026) is None
    assert usable(2026) and not usable(2023)
    assert availability.DISTORTED_SEASONS == (2020, 2023), (
        "availability no longer excludes 2023 by the accident of a games threshold"
    )


# -- the loader and the view, against a database ---------------------------


@pytest.fixture(scope="module")
def factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
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
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        session.execute(text("TRUNCATE leagues, players, owners RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


def _season(session: Session, season: int) -> LeagueSeason:
    league = League(espn_league_id=77)
    session.add(league)
    session.flush()
    row = LeagueSeason(
        league_id=league.id,
        season=season,
        name=f"S{season}",
        scoring_type="H2H_CATEGORY",
        team_count=14,
        regular_season_periods=1,
        total_matchup_periods=1,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        auction_budget=200,
        median_scoring=False,
        raw_settings={},
    )
    session.add(row)
    player = Player(espn_player_id=1, name="Someone")
    session.add(player)
    session.flush()
    for kind, games in (("projected", 40.0), ("total", 70.0)):
        session.add(
            PlayerSeasonStat(
                player_id=player.id,
                season=season,
                kind=kind,
                games_played=games,
                points=900.0,
                raw_totals={"PTS": 900.0, "GP": games},
            )
        )
    session.commit()
    return row


def test_the_loader_refuses_a_snapshot_season_and_says_why(session: Session) -> None:
    _season(session, 2023)
    with pytest.raises(ValueError, match="mid-season"):
        pool.load_projections(session, 2023)
    assert pool.load_projections(session, 2023, allow_unusable=True), "studying it is allowed"
    assert pool.load_projections(session, 2023, kind="total"), "actual totals are not a forecast"


def test_the_loader_serves_a_real_forecast_untouched(session: Session) -> None:
    _season(session, 2026)
    assert len(pool.load_projections(session, 2026)) == 1


def test_the_projection_gaps_view_refuses_a_snapshot_season(
    factory: sessionmaker[Session], session: Session
) -> None:
    _season(session, 2023)
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        response = client.get("/leagues/77/seasons/2023/projection-gaps")
    app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "mid-season" in response.json()["detail"]
