"""The season walk, its record, and the two job kinds. No network.

Every snapshot here is served by a stub, because what is being pinned is
the loop's bookkeeping -- which dates, how many, what was recorded, and
that a second run writes nothing -- not the parser, which has its own
fixtures in tests/test_injury_reports.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import injury_backfill, injury_reports, jobs
from app.db.models import InjuryReport, InjuryReportRun, Player, ProTeamGame
from app.injury_backfill import game_dates, run_backfill
from app.injury_reports import ET, Report, ReportLine
from app.job_kinds import NO_SCHEDULE, NO_SEASON, handlers

SEASON = 2026
DAYS = (date(2025, 11, 10), date(2025, 11, 11))


@pytest.fixture
def factory(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        session.execute(
            text(
                "TRUNCATE injury_reports, injury_report_runs, players, pro_team_games "
                "RESTART IDENTITY CASCADE"
            )
        )
        for offset, day in enumerate(DAYS):
            session.add(
                ProTeamGame(
                    season=SEASON,
                    pro_team_id=30,
                    scoring_period=offset + 1,
                    # Ten Eastern, so the UTC date is the next day: the walk
                    # has to file it under the Eastern date, not the UTC one.
                    game_at=datetime(day.year, day.month, day.day, 22, tzinfo=ET),
                    opponent_pro_team_id=1,
                    home=True,
                )
            )
        session.add(Player(espn_player_id=4432816, name="Brandon Miller"))
        session.commit()
    yield scoring_factory


def a_report(at: datetime, day: date, status: str = "Out") -> Report:
    return Report(
        reported_at=at,
        lines=(
            ReportLine(
                game_date=day,
                game_time="07:00 (ET)",
                matchup="CHA@ATL",
                team="Charlotte Hornets",
                player_name_raw="Miller, Brandon",
                status=status,
                reason="Injury/Illness - Right Shoulder; Soreness",
            ),
            ReportLine(
                game_date=day,
                game_time="07:00 (ET)",
                matchup="CHA@ATL",
                team="Atlanta Hawks",
                player_name_raw="Nobody, Ivan",
                status="Questionable",
                reason="Rest",
            ),
        ),
    )


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Serve a report for every asked-for moment, and record what was asked."""
    asked: list[datetime] = []

    def fake_fetch(at: datetime, *, session: Any = None) -> Report | None:
        asked.append(at)
        eastern = at.astimezone(ET)
        return a_report(at.astimezone(UTC), eastern.date())

    monkeypatch.setattr(injury_reports, "fetch_report", fake_fetch)
    monkeypatch.setattr(injury_backfill.time, "sleep", lambda _seconds: None)
    return asked


def test_the_walk_takes_the_eastern_date_of_every_stored_game(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        assert game_dates(session, SEASON) == list(DAYS)
        assert game_dates(session, 2025) == []


def test_a_morning_walk_fetches_one_snapshot_a_date_and_records_the_run(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    counts = run_backfill(factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0)
    assert [at.astimezone(ET).hour for at in served] == [9, 9]
    assert [at.astimezone(ET).date() for at in served] == list(DAYS)
    assert counts.dates == 2
    assert counts.snapshots_fetched == 2
    assert counts.lines == 4
    assert counts.inserted == 4
    assert counts.matched == 2
    assert counts.unmatched == 2
    assert counts.match_rate == 0.5

    with factory() as session:
        run = session.scalar(select(InjuryReportRun))
        assert run is not None
        assert run.status == injury_backfill.SUCCEEDED
        assert run.mode == injury_backfill.BACKFILL
        assert run.duration_seconds is not None
        assert run.detail["snapshots"] == "morning"
        assert run.detail["inserted"] == 4
        assert run.detail["match_rate"] == 0.5
        assert run.detail["first_date"] == "2025-11-10"
        assert run.detail["unmatched_distinct"] == 1
        assert run.detail["seconds"] is not None


def test_a_second_walk_over_the_same_dates_inserts_nothing(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    run_backfill(factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0)
    again = run_backfill(factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0)
    assert again.snapshots_fetched == 2
    assert again.inserted == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InjuryReport)) == 4


def test_skip_loaded_does_not_even_fetch_what_is_already_in(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    run_backfill(factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0)
    served.clear()
    again = run_backfill(
        factory,
        season=SEASON,
        days=list(DAYS),
        which="morning",
        delay=0.0,
        skip_loaded=True,
    )
    # It still has to fetch to learn the snapshot's own timestamp, but it
    # stores nothing and counts it as skipped rather than as written.
    assert again.snapshots_skipped == 2
    assert again.inserted == 0


def test_a_dry_run_writes_no_rows_but_still_records_the_run(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    counts = run_backfill(
        factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0, dry_run=True
    )
    assert counts.lines == 4
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InjuryReport)) == 0
        run = session.scalar(select(InjuryReportRun))
        assert run is not None
        assert run.detail["dry_run"] is True


def test_a_failure_closes_the_run_row_rather_than_leaving_no_trace(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(at: datetime, *, session: Any = None) -> Report | None:
        raise RuntimeError("the CDN fell over")

    monkeypatch.setattr(injury_reports, "fetch_report", explode)
    monkeypatch.setattr(injury_backfill.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError):
        run_backfill(factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0)

    with factory() as session:
        run = session.scalar(select(InjuryReportRun))
        assert run is not None
        assert run.status == injury_backfill.FAILED
        assert run.error is not None and "the CDN fell over" in run.error
        # It tried three times before giving up.
        assert run.detail["retries"] == 2


def test_a_missing_snapshot_is_counted_not_an_error(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(injury_reports, "fetch_report", lambda at, session=None: None)
    monkeypatch.setattr(injury_backfill.time, "sleep", lambda _seconds: None)
    counts = run_backfill(factory, season=SEASON, days=list(DAYS), which="morning", delay=0.0)
    assert counts.snapshots_missing == 2
    assert counts.snapshots_fetched == 0
    assert counts.inserted == 0


# ---------------------------------------------------------------------------
# the job kinds
# ---------------------------------------------------------------------------


def a_job(kind: str, payload: dict[str, Any]) -> jobs.JobRef:
    return jobs.JobRef(1, kind, None, None, None, 1, payload)


def test_the_two_injury_kinds_are_in_the_queues_registry() -> None:
    assert jobs.INJURY_BACKFILL in jobs.KINDS
    assert jobs.INJURY_PASS in jobs.KINDS
    registry = handlers()
    assert set(registry) == set(jobs.KINDS)


def test_the_backfill_job_walks_the_season_and_says_what_it_did(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    note = handlers()[jobs.INJURY_BACKFILL](
        factory, a_job(jobs.INJURY_BACKFILL, {"season": SEASON, "snapshots": "morning"})
    )
    assert note == "2 dates, 2 snapshots, 4 rows, 50.0% of names placed"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InjuryReport)) == 4


def test_the_daily_pass_takes_today_only(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    note = handlers()[jobs.INJURY_PASS](
        factory,
        a_job(
            jobs.INJURY_PASS,
            {"season": SEASON, "snapshots": "morning", "today": DAYS[1].isoformat()},
        ),
    )
    assert note == "1 dates, 1 snapshots, 2 rows, 50.0% of names placed"
    assert [at.astimezone(ET).date() for at in served] == [DAYS[1]]
    with factory() as session:
        stored = session.scalars(select(InjuryReport.game_date).distinct()).all()
        assert set(stored) == {DAYS[1]}
    assert injury_backfill.PASS in {run.mode for run in _runs(factory)}, (
        "the pass records itself as a pass, not a backfill"
    )


def _runs(factory: sessionmaker[Session]) -> list[InjuryReportRun]:
    with factory() as session:
        return list(session.scalars(select(InjuryReportRun)).all())


def test_a_day_the_league_does_not_play_is_not_a_failure(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    quiet = (DAYS[1] + timedelta(days=3)).isoformat()
    note = handlers()[jobs.INJURY_PASS](
        factory, a_job(jobs.INJURY_PASS, {"season": SEASON, "today": quiet})
    )
    assert note == "no NBA games that day; nothing to fetch"
    assert served == []


def test_a_job_with_no_season_or_no_schedule_fails_without_retrying(
    factory: sessionmaker[Session],
) -> None:
    with pytest.raises(jobs.JobError) as no_season:
        handlers()[jobs.INJURY_BACKFILL](factory, a_job(jobs.INJURY_BACKFILL, {}))
    assert no_season.value.message == NO_SEASON
    assert no_season.value.retry is False

    with pytest.raises(jobs.JobError) as no_schedule:
        handlers()[jobs.INJURY_BACKFILL](factory, a_job(jobs.INJURY_BACKFILL, {"season": 2019}))
    assert no_schedule.value.message == NO_SCHEDULE
    assert no_schedule.value.retry is False


def test_a_live_pass_asks_only_for_what_is_published_and_keeps_what_it_has(
    factory: sessionmaker[Session], served: list[datetime]
) -> None:
    """Four passes a day hold the whole day between them: each asks for the
    snapshots up to its own clock and skips the ones an earlier pass stored."""
    noon = datetime.combine(DAYS[1], datetime.min.time(), tzinfo=ET).replace(hour=12)
    injury_backfill.load_days(factory, [DAYS[1]], which="all", until=noon)
    asked_first = list(served)
    assert asked_first and all(at <= noon for at in asked_first), "nothing after the clock"
    assert len(asked_first) < len(injury_reports.snapshot_times(DAYS[1], which="all"))
    with factory() as session:
        rows_after_first = session.scalar(select(func.count()).select_from(InjuryReport))

    evening = noon.replace(hour=20)
    counts = injury_backfill.load_days(
        factory, [DAYS[1]], which="all", until=evening, skip_loaded=True
    )
    assert counts.snapshots_skipped == len(asked_first), "the morning's snapshots are kept"
    assert counts.snapshots_fetched > 0, "the afternoon's are new"
    with factory() as session:
        rows_after_second = session.scalar(select(func.count()).select_from(InjuryReport))
    assert rows_after_second is not None and rows_after_first is not None
    assert rows_after_second > rows_after_first
