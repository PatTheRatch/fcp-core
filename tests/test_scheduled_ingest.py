"""Scheduled ingest tests.

Two things matter here and neither is about ESPN. A narrowed run must touch
only the days it covers and leave everything else exactly as it was, and
every execution must leave a record even when it fails.

A third came later: the season after the current one has its settings
refreshed while its draft is still ahead, and is left alone otherwise.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    DailyLineupSlot,
    IngestRun,
    LeagueSeason,
    MatchupPeriod,
    PlayerGameStat,
)
from app.db.session import make_engine, make_session_factory
from app.ingest import (
    FULL_SCOPE,
    IngestScope,
    draft_is_pending,
    ingest_season,
    ingest_season_settings,
    recent_scope,
)
from app.ingest_runs import FAILED, RUNNING, SUCCEEDED, last_successful_run, record_run
from scripts.ingest_league import refresh_upcoming_season
from tests.fakes import (
    BOX_LINE,
    DEFAULT_DRAFT_SETTINGS,
    DEFAULT_ROSTER_SETTINGS,
    attach_transactions,
    fake_box,
    fake_card,
    fake_league,
    fake_player,
    fake_team,
    league_with_days,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


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
        session.execute(
            text("TRUNCATE leagues, owners, players, ingest_runs RESTART IDENTITY CASCADE")
        )
        session.commit()
        yield session
        session.rollback()


def _three_day_league(*, current_week: int, points: dict[int, float]) -> Any:
    """A league whose single period covers days 1 to 3, with given scoring."""
    home, away = fake_team(3, "A"), fake_team(21, "B")
    starter = fake_player(100, "Starter", slot="PG")
    weekly = fake_box(home, away, home_lineup=[starter])
    days = {
        1: {day: [fake_box(home, away, home_lineup=[starter], away_lineup=[])] for day in (1, 2, 3)}
    }
    league = league_with_days(
        teams=[home, away],
        boxes={1: [weekly]},
        days=days,
        windows={1: ["1", "2", "3"]},
        reg_season_count=1,
        matchup_period_count=1,
        cards={
            100: fake_card(
                100, "Starter", {day: dict(BOX_LINE, PTS=pts) for day, pts in points.items()}
            )
        },
    )
    league.current_week = current_week
    return league


def test_recent_scope_covers_only_the_trailing_days(session: Session) -> None:
    espn = _three_day_league(current_week=3, points={1: 10.0, 2: 20.0, 3: 30.0})
    stored = ingest_season(session, espn)
    session.commit()

    scope = recent_scope(session, stored, espn, days_back=2)

    assert scope.scoring_periods == frozenset({2, 3})
    assert scope.matchup_periods == frozenset({1})
    assert scope.is_full is False


def test_recent_scope_falls_back_to_full_when_nothing_is_stored(session: Session) -> None:
    """A first ever run has no windows to narrow against."""
    espn = _three_day_league(current_week=3, points={1: 10.0})
    bare = LeagueSeason(
        league_id=1,
        season=2026,
        name="x",
        scoring_type="H2H_CATEGORY",
        team_count=0,
        regular_season_periods=1,
        total_matchup_periods=1,
        playoff_team_count=1,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=0,
        median_scoring=False,
        raw_settings={},
    )
    bare.id = -1  # not persisted; no matchup periods will match
    assert recent_scope(session, bare, espn, days_back=2).is_full


def test_recent_scope_ignores_a_missing_anchor(session: Session) -> None:
    espn = _three_day_league(current_week=3, points={1: 10.0})
    espn.current_week = None
    stored = ingest_season(session, espn)
    session.commit()

    assert recent_scope(session, stored, espn, days_back=2).is_full


def test_a_narrowed_run_leaves_days_outside_it_alone(session: Session) -> None:
    """The safety property the whole schedule rests on."""
    full = _three_day_league(current_week=3, points={1: 10.0, 2: 20.0, 3: 30.0})
    ingest_season(session, full)
    session.commit()

    before = {(s.scoring_period): s.id for s in session.scalars(select(DailyLineupSlot)).all()}
    assert sorted(before) == [1, 2, 3]

    # Day 3's scoring changes; days 1 and 2 must not be rewritten or removed.
    updated = _three_day_league(current_week=3, points={1: 10.0, 2: 20.0, 3: 99.0})
    ingest_season(session, updated, IngestScope(frozenset({1}), frozenset({3})))
    session.commit()

    after = {s.scoring_period: s.id for s in session.scalars(select(DailyLineupSlot)).all()}
    assert sorted(after) == [1, 2, 3], "no day was deleted"
    assert after[1] == before[1] and after[2] == before[2], "untouched days keep their rows"

    lines = {g.scoring_period: g.points for g in session.scalars(select(PlayerGameStat)).all()}
    assert lines[3] == 99.0, "the covered day was refreshed"
    assert lines[1] == 10.0 and lines[2] == 20.0, "the others were left as they were"


def test_a_narrowed_run_does_not_rewrite_period_windows(session: Session) -> None:
    """It only saw part of each period, so narrowing the stored window would lie."""
    espn = _three_day_league(current_week=3, points={1: 10.0, 2: 20.0, 3: 30.0})
    ingest_season(session, espn)
    session.commit()

    period = session.scalars(select(MatchupPeriod)).one()
    assert (period.first_scoring_period, period.final_scoring_period) == (1, 3)

    ingest_season(session, espn, IngestScope(frozenset({1}), frozenset({3})))
    session.commit()
    session.refresh(period)

    assert (period.first_scoring_period, period.final_scoring_period) == (1, 3)


def test_a_successful_run_is_recorded(factory: sessionmaker[Session]) -> None:
    with record_run(factory, espn_league_id=7, season=2026, mode="recent") as detail:
        detail["counts"] = {"teams": 14}

    with factory() as session:
        run = session.scalars(select(IngestRun).order_by(IngestRun.id.desc())).first()
        assert run is not None
        assert run.status == SUCCEEDED
        assert run.mode == "recent"
        assert run.detail["counts"] == {"teams": 14}
        assert run.finished_at is not None
        assert run.duration_seconds is not None


def test_a_failing_run_is_recorded_and_the_error_re_raised(
    factory: sessionmaker[Session],
) -> None:
    """A schedule is only observable if failures leave a trace too."""
    with (
        pytest.raises(RuntimeError, match="ESPN said no"),
        record_run(factory, espn_league_id=7, season=2026, mode="full"),
    ):
        raise RuntimeError("ESPN said no")

    with factory() as session:
        run = session.scalars(select(IngestRun).order_by(IngestRun.id.desc())).first()
        assert run is not None
        assert run.status == FAILED
        assert run.error is not None
        assert "ESPN said no" in run.error
        assert run.finished_at is not None


def test_the_run_row_exists_before_the_work_finishes(factory: sessionmaker[Session]) -> None:
    """So a process killed mid-run leaves a row saying so, not nothing."""
    with record_run(factory, espn_league_id=7, season=2026, mode="full"), factory() as session:
        run = session.scalars(select(IngestRun).order_by(IngestRun.id.desc())).first()
        assert run is not None
        assert run.status == RUNNING


def test_last_successful_run_ignores_failures(factory: sessionmaker[Session]) -> None:
    with record_run(factory, espn_league_id=7, season=2031, mode="full") as detail:
        detail["marker"] = "good"
    with (
        pytest.raises(RuntimeError),
        record_run(factory, espn_league_id=7, season=2031, mode="recent"),
    ):
        raise RuntimeError("later, but failed")

    with factory() as session:
        latest = last_successful_run(session, 2031)
        assert latest is not None
        assert latest.detail.get("marker") == "good"


def test_full_scope_describes_itself_as_full() -> None:
    assert FULL_SCOPE.describe() == {"mode": "full"}
    assert FULL_SCOPE.is_full is True
    assert FULL_SCOPE.covers_day(999) and FULL_SCOPE.covers_period(999)


#: 2026-10-03 14:00 UTC, the 2027 draft as ESPN had it on 2026-09-15.
DRAFT_2027_EPOCH_MS = 1791036000000
SEPTEMBER = datetime(2026, 9, 16, tzinfo=UTC)


def _next_season(
    *,
    team_count: int = 15,
    auction_budget: int = 200,
    centre_limit: int = 3,
    draft_epoch_ms: int | None = DRAFT_2027_EPOCH_MS,
) -> Any:
    league = fake_league(season=2027, team_count=team_count)
    league.teams = [fake_team(team_id, f"Team {team_id}") for team_id in range(1, team_count + 1)]
    roster = dict(DEFAULT_ROSTER_SETTINGS, positionLimits={"5": centre_limit})
    draft = dict(DEFAULT_DRAFT_SETTINGS, auctionBudget=auction_budget, date=draft_epoch_ms)
    return attach_transactions(league, {}, roster_settings=roster, draft_settings=draft)


def _stored(session: Session, season: int) -> LeagueSeason | None:
    session.expire_all()
    return session.scalars(select(LeagueSeason).where(LeagueSeason.season == season)).one_or_none()


def _runs(session: Session, season: int) -> list[IngestRun]:
    return list(session.scalars(select(IngestRun).where(IngestRun.season == season)).all())


def test_a_draft_is_pending_until_its_date_passes() -> None:
    assert draft_is_pending(None, SEPTEMBER), "an unscheduled draft has not happened"
    assert draft_is_pending(datetime(2026, 10, 3, 14, tzinfo=UTC), SEPTEMBER)
    assert not draft_is_pending(datetime(2025, 10, 18, 17, tzinfo=UTC), SEPTEMBER)


def test_season_settings_repair_a_stale_row_without_touching_play(session: Session) -> None:
    """The 2026-09-15 row: 16 teams, $0, four centres, no draft date."""
    stale = _next_season(team_count=16, auction_budget=0, centre_limit=4, draft_epoch_ms=None)
    ingest_season_settings(session, stale)
    session.commit()

    ingest_season_settings(session, _next_season())
    session.commit()

    stored = _stored(session, 2027)
    assert stored is not None
    assert stored.team_count == 15
    assert stored.auction_budget == 200
    assert stored.position_limits == {"C": 3}
    assert stored.drafted_at == datetime(2026, 10, 3, 14, tzinfo=UTC)
    assert stored.draft_order == DEFAULT_DRAFT_SETTINGS["pickOrder"]
    assert len(stored.teams) == 16, "teams are updated in place, never deleted"
    assert session.scalar(select(func.count()).select_from(MatchupPeriod)) == 0
    assert session.scalar(select(func.count()).select_from(PlayerGameStat)) == 0


def test_upcoming_refresh_writes_settings_while_the_draft_is_ahead(
    factory: sessionmaker[Session], session: Session
) -> None:
    note = refresh_upcoming_season(
        factory, espn_league_id=3853870, season=2027, fetch=lambda _: _next_season(), now=SEPTEMBER
    )

    assert "settings refreshed" in note
    stored = _stored(session, 2027)
    assert stored is not None
    assert (stored.team_count, stored.auction_budget) == (15, 200)
    [run] = _runs(session, 2027)
    assert (run.mode, run.status) == ("settings", SUCCEEDED)
    assert run.detail["counts"] == {"teams": 15}


def test_upcoming_refresh_skips_a_season_espn_does_not_have(
    factory: sessionmaker[Session], session: Session
) -> None:
    """Most of the year. Not a failure, so no failed run to set off the health check."""

    def missing(season: int) -> Any:
        raise RuntimeError(f"League {season} does not exist")

    note = refresh_upcoming_season(
        factory, espn_league_id=3853870, season=2027, fetch=missing, now=SEPTEMBER
    )

    assert "not on ESPN yet" in note
    assert _stored(session, 2027) is None
    assert _runs(session, 2027) == []


def test_upcoming_refresh_leaves_a_drafted_season_alone(
    factory: sessionmaker[Session], session: Session
) -> None:
    """Once the draft is done, the regular ingest owns the season."""
    ingest_season_settings(session, _next_season())
    session.commit()

    note = refresh_upcoming_season(
        factory,
        espn_league_id=3853870,
        season=2027,
        fetch=lambda _: _next_season(team_count=12, auction_budget=500),
        now=datetime(2026, 10, 4, tzinfo=UTC),
    )

    assert "drafted 2026-10-03" in note
    stored = _stored(session, 2027)
    assert stored is not None
    assert (stored.team_count, stored.auction_budget) == (15, 200)
    assert _runs(session, 2027) == []


def test_the_scheduled_job_asks_for_the_upcoming_season() -> None:
    script = (REPO_ROOT / "scripts" / "scheduled_ingest.sh").read_text()
    assert '--recent "$RECENT_DAYS" --upcoming' in script
