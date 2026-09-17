"""The watchdog: a job that stops writing rows has to be noticed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import BBMCapture, IngestRun, ProTeamGame
from app.watchdog import LISTENER_QUIET_HOURS, checks, message
from tests.scoring_db import league_season

NOW = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)


def run(session: Session, mode: str, hours_ago: float, status: str = "succeeded") -> None:
    session.add(
        IngestRun(
            espn_league_id=3853870,
            season=2027,
            mode=mode,
            status=status,
            started_at=NOW - timedelta(hours=hours_ago),
        )
    )
    session.flush()


def game(session: Session, days_ahead: float) -> None:
    session.add(
        ProTeamGame(
            season=2027,
            pro_team_id=5,
            scoring_period=10,
            game_at=NOW + timedelta(days=days_ahead),
            opponent_pro_team_id=12,
            home=True,
        )
    )
    session.flush()


def capture(session: Session, days_ago: int) -> None:
    session.add(
        BBMCapture(
            season=2027,
            value_type="total",
            captured_on=(NOW - timedelta(days=days_ago)).date(),
            players=586,
            changed=4,
            dropped=0,
        )
    )
    session.flush()


def quiet(session: Session, **kwargs: object) -> set[str]:
    return {c.name for c in checks(session, now=NOW, **kwargs) if c.quiet}  # type: ignore[arg-type]


def test_a_job_that_stopped_writing_rows_is_quiet(scoring_session: Session) -> None:
    session = scoring_session
    league_season(session, season=2027)
    game(session, days_ahead=1)  # in season
    run(session, "recent", hours_ago=2)
    run(session, "status", hours_ago=2)
    capture(session, days_ago=0)
    assert quiet(session) == set()

    run(session, "status", hours_ago=LISTENER_QUIET_HOURS + 1)
    session.flush()
    assert quiet(session) == set(), "the newest success is what counts, not the oldest"


def test_the_listener_is_held_to_a_tighter_window_in_season(scoring_session: Session) -> None:
    session = scoring_session
    league_season(session, season=2027)
    run(session, "recent", hours_ago=20)
    run(session, "status", hours_ago=20)
    capture(session, days_ago=0)
    assert quiet(session) == set(), "out of season, four passes a day are not expected"

    game(session, days_ahead=1)
    assert quiet(session) == {"listener"}, "in season, twenty hours of silence is a problem"


def test_never_having_run_counts_as_quiet_and_reads_that_way(scoring_session: Session) -> None:
    session = scoring_session
    league_season(session, season=2027)
    results = checks(session, now=NOW)
    assert {c.name for c in results if c.quiet} == {"ingest", "listener", "bbm"}
    text = message(results, today=NOW.date())
    assert text is not None
    assert "3 job(s) quiet" in text
    assert "ingest: never succeeded" in text and "bbm: never captured" in text


def test_a_failed_last_run_is_named_beside_the_last_success(scoring_session: Session) -> None:
    session = scoring_session
    league_season(session, season=2027)
    run(session, "recent", hours_ago=30)
    run(session, "recent", hours_ago=2, status="failed")
    [ingest] = [c for c in checks(session, now=NOW) if c.name == "ingest"]
    assert not ingest.quiet
    assert "last run failed" in ingest.detail


def test_backups_are_checked_from_the_directory(scoring_session: Session, tmp_path: Path) -> None:
    session = scoring_session
    league_season(session, season=2027)
    assert "backup" in quiet(session, backups=tmp_path), "an empty directory is not a backup"

    dump = tmp_path / "fcp-20261103T100000Z.dump"
    dump.write_bytes(b"x")
    import os

    fresh = (NOW - timedelta(hours=1)).timestamp()
    os.utime(dump, (fresh, fresh))
    assert "backup" not in quiet(session, backups=tmp_path)


def test_nothing_is_sent_when_every_job_is_running(scoring_session: Session) -> None:
    session = scoring_session
    league_season(session, season=2027)
    run(session, "recent", hours_ago=2)
    run(session, "status", hours_ago=2)
    capture(session, days_ago=0)
    assert message(checks(session, now=NOW)) is None
