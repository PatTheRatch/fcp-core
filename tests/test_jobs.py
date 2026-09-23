"""The job queue, the schedule and the four kinds of job (docs/jobs.md).

What is pinned: a schedule enqueued twice adds nothing; two workers never
hold the same job (`SKIP LOCKED`, with two real sessions and with two
threads); a failure is retried with backoff and given up after three tries,
and a job waiting on it fails with it; the ingest job reads the league with
its connection's unsealed login and never lets that login into
`last_error`, `ingest_runs.error`, the connection's row or a log line; after
a league's first good ingest its connector is verified on his team; the
precompute stores both reports and the route answers from the stored row
(only when it is today's); the digest reaches every verified channel and
skips the rest; and single mode's schedule is the owner's league and team.

ESPN is never reached: the fetch is replaced (`app.league_ingest.fetch_league`)
and a fake league stands in (`tests/fakes.py`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from espn_api.requests.espn_requests import ESPNAccessDenied
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app import (
    accounts,
    channels,
    jobs,
    league_ingest,
    memberships,
    notify,
    reports,
    schedule,
    subscriptions,
)
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.db.models import (
    FreeAgentSnapshot,
    IngestRun,
    Job,
    League,
    LeagueConnection,
    NotificationChannel,
    PlayerStatusSnapshot,
    Team,
    TeamManager,
    TeamReport,
)
from app.espn import ESPNSettings
from app.job_kinds import (
    BAD_CLOCK,
    NO_ADDRESS,
    NO_ROSTER_ALERT,
    NO_TOPICS,
    NOT_SUBSCRIBED,
    REFUSED,
    handlers,
    run_digest,
    run_pass,
    run_precompute,
)
from app.main import create_app
from app.pickups.bids import clear_cache
from app.pickups.state import season_calendar
from app.secrets_box import new_key
from app.watchdog import reconnect_message, stale_connections, worker_check
from scripts.watchdog import tell_owners
from tests.fakes import (
    attach_pool,
    fake_league,
    fake_pool_entry,
    fake_team,
    league_with_play,
    owner_dict,
)
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    configure,
    day_date,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

SEASON = 2026
KEY = new_key()
SWID = "0A1B2C3D-4E5F-6A7B-8C9D-0E1F2A3B4C5D"
ESPN_S2 = "AEBsecretS2cookieValue0123456789abcdefSECRET"
OTHER_LEAGUE = 777001
NOW = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)


def settings_for(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_secrets_key": KEY,
        "fcp_owner_email": "owner@example.com",
        "espn_league_id": None,
        "fcp_tracked_team_id": None,
        "fcp_digest_chat_id": None,
        "fcp_smtp_host": None,
        "fcp_email_from": None,
        "fcp_email_to": None,
        "fcp_public_url": None,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


@pytest.fixture
def factory(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        session.execute(
            text(
                "TRUNCATE jobs, leagues, players, owners, users, ingest_runs, pro_team_games "
                "RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
    clear_cache()
    yield scoring_factory


def _league(session: Session, espn_league_id: int = OTHER_LEAGUE) -> League:
    league = memberships.get_or_create_league(session, espn_league_id)
    session.commit()
    return league


def _connect(
    session: Session, espn_league_id: int = OTHER_LEAGUE, email: str = "connector@example.com"
) -> tuple[League, LeagueConnection, int]:
    """A league connected by a user, as the connect route leaves it."""
    user = accounts.get_or_create_user(session, email)
    league = memberships.get_or_create_league(session, espn_league_id)
    connection = LeagueConnection(
        league_id=league.id,
        user_id=user.id,
        sealed_credentials=memberships.seal_credentials(SWID, ESPN_S2, settings_for()),
        league_name="The Other League",
        ingest_requested_at=NOW - timedelta(hours=1),
    )
    session.add(connection)
    memberships.join_league(session, user.id, league.id, accounts.OWNER_ROLE)
    session.commit()
    return league, connection, user.id


# ---------------------------------------------------------------------------
# enqueueing
# ---------------------------------------------------------------------------


def test_enqueueing_the_same_job_twice_adds_one_row(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        league = _league(session)
        first = jobs.enqueue(
            session, jobs.INGEST, run_after=NOW, label="nightly", league_id=league.id
        )
        again = jobs.enqueue(
            session,
            jobs.INGEST,
            run_after=NOW + timedelta(minutes=7),
            label="nightly",
            league_id=league.id,
        )
        other_label = jobs.enqueue(
            session, jobs.INGEST, run_after=NOW, label="requested", league_id=league.id
        )
        next_day = jobs.enqueue(
            session,
            jobs.INGEST,
            run_after=NOW + timedelta(days=1),
            label="nightly",
            league_id=league.id,
        )
        session.commit()
        assert first.created and not again.created
        assert again.id == first.id, "the same job, whatever minute it was asked for"
        assert other_label.created and next_day.created
        assert session.scalar(select(text("count(*)")).select_from(Job)) == 3


def test_a_schedule_enqueued_twice_adds_nothing(factory: sessionmaker[Session]) -> None:
    settings = settings_for()
    with factory() as session:
        _connect(session)
        first = schedule.enqueue_schedule(session, settings, "nightly", NOW)
        second = schedule.enqueue_schedule(session, settings, "nightly", NOW + timedelta(minutes=4))
        kinds = sorted(job.kind for job in first)
        assert kinds == ["ingest", "ingest", "status_pass"], "requested, nightly, and its pass"
        assert all(job.created for job in first)
        assert not any(job.created for job in second)
        assert sorted(job.id for job in first) == sorted(job.id for job in second)


def test_each_leagues_ingest_is_spread_by_a_stable_hash() -> None:
    offsets = {league: schedule.ingest_offset(league) for league in range(100, 110)}
    assert all(timedelta(0) <= o < schedule.NIGHT_WINDOW for o in offsets.values())
    assert len(set(offsets.values())) > 5, "ten leagues do not all start together"
    assert schedule.ingest_offset(3853870) == schedule.ingest_offset(3853870)


def test_the_nightly_ingest_falls_in_the_window_and_its_pass_waits_on_it(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        _, connection, _ = _connect(session)
        connection.ingest_requested_at = None
        session.commit()
        schedule.enqueue_schedule(session, settings_for(), "nightly", NOW + timedelta(minutes=3))
        ingest = session.scalars(select(Job).where(Job.kind == "ingest")).one()
        passed = session.scalars(select(Job).where(Job.kind == "status_pass")).one()
        assert ingest.run_after == NOW + schedule.ingest_offset(OTHER_LEAGUE)
        assert passed.depends_on == ingest.id and passed.run_after > ingest.run_after


# ---------------------------------------------------------------------------
# taking jobs
# ---------------------------------------------------------------------------


def _many(session: Session, count: int) -> list[int]:
    league = _league(session)
    ids = [
        jobs.enqueue(session, jobs.DIGEST, run_after=NOW, label=f"n{i}", league_id=league.id).id
        for i in range(count)
    ]
    session.commit()
    return ids


def test_a_job_locked_by_one_worker_is_skipped_by_another(
    factory: sessionmaker[Session],
) -> None:
    with factory() as setup:
        first, second = _many(setup, 2)
    with factory() as a, factory() as b:
        held_by_a = jobs.lock_next(a, NOW)
        assert held_by_a is not None and held_by_a.id == first
        taken_by_b = jobs.claim(b, "worker-b", NOW)
        assert taken_by_b is not None and taken_by_b.id == second, "skipped, not waited on"
        assert jobs.claim(b, "worker-b", NOW) is None, "nothing else is due"
        a.rollback()


def test_two_workers_never_take_the_same_job(factory: sessionmaker[Session]) -> None:
    with factory() as setup:
        ids = _many(setup, 30)
    taken: dict[str, list[int]] = {"a": [], "b": []}

    def work(name: str) -> None:
        with factory() as session:
            while (job := jobs.claim(session, name, NOW)) is not None:
                taken[name].append(job.id)

    threads = [threading.Thread(target=work, args=(name,)) for name in taken]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    everything = taken["a"] + taken["b"]
    assert sorted(everything) == sorted(ids), "every job taken, none twice"
    assert len(set(everything)) == len(everything)


def test_a_failure_is_retried_with_backoff_and_then_given_up(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with factory() as session:
        (job_id,) = _many(session, 1)
    calls: list[int] = []

    def broken(_: sessionmaker[Session], job: jobs.JobRef) -> str | None:
        calls.append(job.attempts)
        raise RuntimeError("the database said: password=hunter2")

    kinds = {jobs.DIGEST: broken}
    at = NOW
    first = jobs.run_next(factory, kinds, worker="w", at=at)
    assert first is not None and first.state == jobs.QUEUED
    assert jobs.run_next(factory, kinds, worker="w", at=at) is None, "not due until the backoff"
    at += jobs.BACKOFF[0]
    assert jobs.run_next(factory, kinds, worker="w", at=at).state == jobs.QUEUED  # type: ignore[union-attr]
    at += jobs.BACKOFF[1]
    last = jobs.run_next(factory, kinds, worker="w", at=at)
    assert last is not None and last.state == jobs.FAILED
    assert calls == [1, 2, 3]
    with factory() as session:
        row = session.get(Job, job_id)
        assert row is not None and row.state == "failed" and row.attempts == jobs.MAX_ATTEMPTS
        assert row.last_error == "failed (RuntimeError)", "the class, never the text"
    assert "hunter2" not in caplog.text


def test_a_failure_in_our_own_words_that_cannot_mend_is_not_retried(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        _many(session, 1)

    def hopeless(_: sessionmaker[Session], __: jobs.JobRef) -> str | None:
        raise jobs.JobError("no login for this league", retry=False)

    outcome = jobs.run_next(factory, {jobs.DIGEST: hopeless}, worker="w", at=NOW)
    assert outcome is not None and outcome.state == jobs.FAILED
    assert outcome.message == "no login for this league"


def test_a_job_waits_on_its_prerequisite_and_fails_with_it(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        league = _league(session)
        passed = jobs.enqueue(
            session, jobs.STATUS_PASS, run_after=NOW, label="morning", league_id=league.id
        )
        digest = jobs.enqueue(
            session,
            jobs.DIGEST,
            run_after=NOW - timedelta(minutes=1),
            label="morning",
            league_id=league.id,
            depends_on=passed.id,
        )
        session.commit()
    ran: list[str] = []

    def hopeless(_: sessionmaker[Session], job: jobs.JobRef) -> str | None:
        ran.append(job.kind)
        raise jobs.JobError("ESPN is down", retry=False)

    def fine(_: sessionmaker[Session], job: jobs.JobRef) -> str | None:
        ran.append(job.kind)
        return None

    kinds = {jobs.STATUS_PASS: hopeless, jobs.DIGEST: fine}
    first = jobs.run_next(factory, kinds, worker="w", at=NOW)
    assert first is not None and first.job.id == passed.id, "the digest is due first but waits"
    assert jobs.run_next(factory, kinds, worker="w", at=NOW) is None
    assert ran == ["status_pass"], "the digest never ran"
    with factory() as session:
        row = session.get(Job, digest.id)
        assert row is not None and row.state == "failed"
        assert row.last_error == jobs.GAVE_UP_ON_PREREQUISITE


def test_a_job_whose_worker_died_is_taken_again(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        (job_id,) = _many(session, 1)
        assert jobs.claim(session, "dead", NOW) is not None
    later = NOW + jobs.LEASE + timedelta(minutes=1)
    with factory() as session:
        assert jobs.claim(session, "alive", later) is None, "reaped, and backing off"
        row = session.get(Job, job_id)
        assert row is not None and row.state == "queued" and row.last_error == jobs.LOST_WORKER
        again = jobs.claim(session, "alive", later + jobs.BACKOFF[0])
        assert again is not None and again.id == job_id and again.attempts == 2


def test_a_worker_that_narrows_its_claim_never_touches_another_workers_rows(
    factory: sessionmaker[Session],
) -> None:
    """The rehearsal (`scripts/rehearse_week.py`) puts its own jobs on the
    real queue beside real ones, and must not be able to take, fail or reap
    one of those. `only` is what stops it, and it has to hold for all three:
    a narrowed claim skips the other rows, and the reaper it runs first must
    leave another worker's dead job alone rather than handing it back."""
    with factory() as session:
        league = _league(session)
        # The real job first, so it is also the one a claim would reach for.
        theirs = jobs.enqueue(
            session, jobs.DIGEST, run_after=NOW, label="theirs", league_id=league.id
        ).id
        mine = jobs.enqueue(
            session,
            jobs.DIGEST,
            run_after=NOW,
            label="mine",
            league_id=league.id,
            payload={"rehearsal": True},
        ).id
        session.commit()
    ours = Job.payload["rehearsal"].astext == "true"

    with factory() as session:
        held = jobs.claim(session, "theirs", NOW)
        assert held is not None and held.id == theirs, "the real job, claimed first, is due first"
    with factory() as session:
        narrowed = jobs.claim(session, "rehearsal", NOW, only=ours)
        assert narrowed is not None and narrowed.id == mine
        assert jobs.claim(session, "rehearsal", NOW, only=ours) is None, "nothing else is ours"

    # Their job has now been `running` past the lease: a narrowed reaper has
    # to leave it there, for the worker that owns it.
    later = NOW + jobs.LEASE + timedelta(minutes=1)
    with factory() as session:
        assert jobs.reap(session, later, only=ours) == 1, "only our own lost job"
        session.commit()
        assert session.get(Job, theirs).state == jobs.RUNNING  # type: ignore[union-attr]
        assert session.get(Job, mine).state == jobs.QUEUED  # type: ignore[union-attr]


def test_a_job_that_names_a_day_is_built_for_that_day_not_for_today(
    factory: sessionmaker[Session],
) -> None:
    """The precompute's day comes from its payload when it carries one.

    A real morning's job carries none and means today. The rehearsal replays
    a morning in a season already played, and the report has to be built for
    *that* morning inside the worker process -- which is the only place it
    is built -- so the day rides in the payload. A payload that names
    something that is not a date is a failure nobody should retry."""
    with factory() as session:
        home = _seed_a_season(session)
        calendar = season_calendar(session, SEASON)
        assert calendar is not None
        that_morning = day_date(4)
        payload = {"today": that_morning.isoformat()}
        job = jobs.JobRef(1, jobs.PRECOMPUTE, None, home.id, None, 1, payload)
        wanted = calendar.scoring_period_on(that_morning)
        today = calendar.scoring_period_on(date.today())
    assert wanted != today, "the fixture's calendar would not tell the two apart"

    note = handlers(settings_for())[jobs.PRECOMPUTE](factory, job)
    assert note == f"stored stream, season, today for day {wanted}"
    with factory() as session:
        stored = {int(row.scoring_period) for row in session.scalars(select(TeamReport)).all()}
        assert stored == {wanted}

    bad = jobs.JobRef(2, jobs.PRECOMPUTE, None, home.id, None, 1, {"today": "the morning"})
    with pytest.raises(jobs.JobError) as refused:
        handlers(settings_for())[jobs.PRECOMPUTE](factory, bad)
    assert refused.value.message == BAD_CLOCK and refused.value.retry is False


# ---------------------------------------------------------------------------
# the ingest job
# ---------------------------------------------------------------------------


def _ingest_job(session: Session, league: League) -> None:
    jobs.enqueue(session, jobs.INGEST, run_after=NOW, label="nightly", league_id=league.id)
    session.commit()


def _nothing_secret(*texts: str | None) -> None:
    swid_bare = SWID.lower()
    for found in texts:
        if found is None:
            continue
        assert ESPN_S2 not in found
        assert SWID not in found and swid_bare not in found.lower()


def test_the_ingest_reads_with_the_connections_unsealed_login_and_never_repeats_it(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[ESPNSettings] = []

    def refused(settings: ESPNSettings, season: int | None = None) -> Any:
        seen.append(settings)
        # The worst case: an error that quotes the login it was given.
        raise ESPNAccessDenied(f"denied for espn_s2={settings.espn_s2} swid={settings.espn_swid}")

    monkeypatch.setattr(league_ingest, "fetch_league", refused)
    caplog.set_level(logging.DEBUG)
    with factory() as session:
        league, connection, _ = _connect(session)
        _ingest_job(session, league)
    outcome = jobs.run_next(factory, handlers(settings_for()), worker="w", at=NOW)

    assert seen, "ESPN was asked"
    assert seen[0].espn_s2 == ESPN_S2 and seen[0].espn_swid == "{" + SWID + "}"
    assert seen[0].espn_league_id == OTHER_LEAGUE
    assert outcome is not None and outcome.state == jobs.FAILED, "a refused login is not retried"
    assert outcome.message == REFUSED
    with factory() as session:
        job = session.scalars(select(Job)).one()
        stored = session.get(LeagueConnection, connection.id)
        # The season in progress is asked for before any run is opened, as
        # the script does; any run that was opened failed.
        runs = session.scalars(select(IngestRun)).all()
        assert all(run.status == "failed" for run in runs)
        assert stored is not None and stored.last_error == REFUSED
        _nothing_secret(
            job.last_error, stored.last_error, caplog.text, *(run.error for run in runs)
        )


def test_after_a_first_good_ingest_the_connector_is_verified_on_his_team(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    league_obj = league_with_play(
        season=SEASON,
        teams=[
            fake_team(1, "Connector's", owners=[owner_dict("{" + SWID + "}")]),
            fake_team(2, "Someone Else's"),
        ],
        boxes={},
    )
    league_obj.league_id = OTHER_LEAGUE

    def fetch(settings: ESPNSettings, season: int | None = None) -> Any:
        assert settings.espn_s2 == ESPN_S2
        if season != SEASON:
            raise ESPNAccessDenied("no such season yet")
        return league_obj

    monkeypatch.setattr(league_ingest, "fetch_league", fetch)
    monkeypatch.setattr(league_ingest, "current_season", lambda today=None: SEASON)
    with factory() as session:
        league, connection, user_id = _connect(session)
        _ingest_job(session, league)
    outcome = jobs.run_next(factory, handlers(settings_for()), worker="w", at=NOW)
    assert outcome is not None and outcome.state == jobs.DONE, outcome
    assert outcome.message == f"league {OTHER_LEAGUE}: backfilled {SEASON}"
    with factory() as session:
        stored = session.get(LeagueConnection, connection.id)
        assert stored is not None and stored.last_ok_at is not None and stored.last_error is None
        claims = session.execute(
            select(Team.espn_team_id, TeamManager.state, TeamManager.how)
            .join(TeamManager, TeamManager.team_id == Team.id)
            .where(TeamManager.user_id == user_id)
        ).all()
        assert [tuple(row) for row in claims] == [(1, "verified", "owner_guid")]


def test_a_league_with_no_connection_and_not_the_env_league_is_refused(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        league = _league(session)
        _ingest_job(session, league)
    outcome = jobs.run_next(factory, handlers(settings_for()), worker="w", at=NOW)
    assert outcome is not None and outcome.state == jobs.FAILED
    assert outcome.message == "the league has no live connection to read it with"


# ---------------------------------------------------------------------------
# precompute, and the routes reading what it stored
# ---------------------------------------------------------------------------

STARTER = {
    "PTS": 20.0, "REB": 8.0, "AST": 4.0, "STL": 1.2, "BLK": 0.8, "3PM": 2.0,
    "TO": 2.4, "FGM": 8.0, "FGA": 17.0, "FTM": 4.0, "FTA": 5.0,
}  # fmt: skip
POSTED = {
    "PTS": 500.0, "REB": 200.0, "AST": 100.0, "STL": 30.0, "BLK": 20.0, "3PM": 50.0,
    "TO": 60.0, "FGM": 235.0, "FGA": 500.0, "FTM": 78.0, "FTA": 100.0,
}  # fmt: skip


def _scaled(line: dict[str, float], factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in line.items()}


def _seed_a_season(session: Session) -> Team:
    """The pickups routes' own small season (tests/test_api_pickups.py)."""
    ls, (home, away), (first, _) = league_season(session, season=SEASON, days_per_period=7)
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
    matchup(session, first, home, away, {home: POSTED, away: _scaled(POSTED, 0.8)})
    for name, factor, team in (("A", 1.0, home), ("B", 1.0, home), ("C", 1.0, home),
                               ("Weak", 0.5, home), ("Rival", 1.0, away)):  # fmt: skip
        who = player(session, name)
        eligible(session, who, ANY, "PG")
        snapshot(session, who, pro_team_id=10, on_team_id=team.espn_team_id, season=SEASON)
        projected(session, who, 70, _scaled(STARTER, factor), season=SEASON)
        held(session, team, first, who, 1, season=SEASON)
    for name, factor, pro_team in (("Star", 1.4, 20), ("Scrub", 0.2, 21)):
        who = player(session, name)
        eligible(session, who, ANY, "PG")
        snapshot(session, who, pro_team_id=pro_team, on_team_id=0, season=SEASON)
        projected(session, who, 70, _scaled(STARTER, factor), season=SEASON)
        on_the_wire(session, ls, who)
    for pro_team in (10, 20, 21):
        games(session, pro_team, list(range(1, 15)), season=SEASON)
    session.commit()
    return home


@pytest.fixture
def client(factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _url(which: str, today: int | None = None) -> str:
    tail = f"?today={today}" if today is not None else ""
    return f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/1/pickups/{which}{tail}"


def _today_url(today: int | None = None) -> str:
    """The day's lineup, which is not under /pickups/ but stores beside them."""
    tail = f"?today={today}" if today is not None else ""
    return f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/1/today{tail}"


def test_precompute_stores_all_three_reports_and_the_routes_read_them(
    factory: sessionmaker[Session], client: TestClient
) -> None:
    with factory() as session:
        home = _seed_a_season(session)
        home_id = home.id
        calendar = season_calendar(session, SEASON)
        assert calendar is not None
        today = calendar.scoring_period_on(date.today())
    live_stream = client.get(_url("stream", today)).json()
    live_today = client.get(_today_url(today)).json()
    job = jobs.JobRef(1, jobs.PRECOMPUTE, None, home_id, None, 1, {})
    note = run_precompute(factory, job)
    assert note == f"stored stream, season, today for day {today}"

    with factory() as session:
        rows = {row.kind: row for row in session.scalars(select(TeamReport)).all()}
        assert set(rows) == {"stream", "season", "today"}
        assert rows["stream"].scoring_period == today
        assert rows["stream"].payload == live_stream, "what is stored is what the route says"
        assert rows["today"].payload == live_today
        # Mark the stored rows, so an answer from them is unmistakable.
        for row in rows.values():
            row.payload = {**row.payload, "expected_wins": 99.0, "edge": 99.0}
        session.commit()

    assert client.get(_url("stream")).json()["expected_wins"] == 99.0
    assert client.get(_url("season")).json()["expected_wins"] == 99.0
    assert client.get(_today_url()).json()["edge"] == 99.0
    glance = client.get(_url("glance")).json()
    assert glance["stored"] is True and glance["expected_wins"] == 99.0
    assert glance["record_without"] == live_stream["outlook"]["record_without"]

    other_day = today - 1
    assert client.get(_url("stream", other_day)).json()["expected_wins"] != 99.0, (
        "another day is built live"
    )
    assert client.get(_today_url(other_day)).json()["edge"] != 99.0


def test_a_stored_report_from_an_earlier_day_is_not_served(
    factory: sessionmaker[Session], client: TestClient
) -> None:
    with factory() as session:
        home = _seed_a_season(session)
        calendar = season_calendar(session, SEASON)
        assert calendar is not None
        today = calendar.scoring_period_on(date.today())
        reports.store(
            session,
            home.id,
            reports.STREAM,
            today,
            {"expected_wins": 99.0},
            built_at=datetime.now(UTC) - timedelta(days=2),
        )
        session.commit()
    body = client.get(_url("stream")).json()
    assert body["expected_wins"] != 99.0, "last week's row for the same clamped day is stale"
    assert client.get(_url("glance")).json()["stored"] is False


# ---------------------------------------------------------------------------
# the digest job
# ---------------------------------------------------------------------------


class Outbox:
    """Every message the notify layer was asked to send, and where."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sent: list[tuple[str, str]] = []
        monkeypatch.setattr(notify, "send_email", self.mail)

    def mail(self, text: str, *, recipients: Any, **_: Any) -> None:
        self.sent.append((f"mail {','.join(recipients)}", text))


def _channel(
    session: Session, user_id: int, target: str, settings: Settings, *, verified: bool
) -> None:
    added = channels.add(session, user_id, channels.EMAIL, target, settings)
    if verified:
        channels.verify(session, user_id, added.secret)
    session.commit()


def _retired_channel(session: Session, user_id: int) -> None:
    """A row as migration 0024 leaves a Telegram channel: kept, disabled,
    its target wiped. The member has nowhere to be sent."""
    session.add(
        NotificationChannel(
            user_id=user_id,
            kind="telegram",
            sealed_target=None,
            masked_target="chat •••4321",
            verified_at=NOW,
            disabled_at=NOW,
        )
    )
    session.commit()


def test_a_members_digest_goes_to_each_confirmed_address_and_skips_the_rest(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
    )
    outbox = Outbox(monkeypatch)
    with factory() as session:
        league, _, member = _connect(session)
        ls, _, _ = league_season(session, season=SEASON)
        ls.league_id = league.id
        session.flush()
        _channel(session, member, "member@example.com", settings, verified=True)
        _channel(session, member, "second@example.com", settings, verified=True)
        _channel(session, member, "unconfirmed@example.com", settings, verified=False)
        outbox.sent.clear()  # the verification messages
        job = jobs.enqueue(
            session,
            jobs.DIGEST,
            run_after=NOW,
            label="morning",
            league_id=league.id,
            user_id=member,
            payload={"mode": "morning"},
        )
        session.commit()
    ref = jobs.JobRef(job.id, jobs.DIGEST, league.id, None, member, 1, {"mode": "morning"})
    note = run_digest(factory, ref, settings, now=NOW)
    assert note == "sent to 2 of 2 channel(s)"
    where = sorted(target for target, _ in outbox.sent)
    assert where == ["mail member@example.com", "mail second@example.com"]
    assert all("THE LEAGUE" in body for _, body in outbox.sent)
    assert not any("unconfirmed" in target for target, _ in outbox.sent), "unconfirmed: skipped"


def test_a_member_with_no_confirmed_address_is_sent_nothing(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox = Outbox(monkeypatch)
    with factory() as session:
        league, _, member = _connect(session)
        ls, _, _ = league_season(session, season=SEASON)
        ls.league_id = league.id
        session.commit()
    ref = jobs.JobRef(1, jobs.DIGEST, league.id, None, member, 1, {"mode": "morning"})
    assert run_digest(factory, ref, settings_for(), now=NOW) == NO_ADDRESS
    assert outbox.sent == []


def test_a_member_left_with_only_a_telegram_row_has_nowhere_to_be_sent(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0024 disabled it. He is not silently skipped: the job note
    says he has no confirmed address, which is what the operator reads."""
    outbox = Outbox(monkeypatch)
    with factory() as session:
        league, _, member = _connect(session)
        ls, _, _ = league_season(session, season=SEASON)
        ls.league_id = league.id
        session.flush()
        _retired_channel(session, member)
    ref = jobs.JobRef(1, jobs.DIGEST, league.id, None, member, 1, {"mode": "morning"})
    assert run_digest(factory, ref, settings_for(), now=NOW) == NO_ADDRESS
    assert outbox.sent == []


def test_a_member_who_wants_no_morning_digest_is_not_sent_one(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the alert between digests is a separate answer: switching off the
    morning one does not switch off being told a man is out."""
    settings = settings_for(fcp_smtp_host="smtp.example.test", fcp_email_from="fcp@example.test")
    outbox = Outbox(monkeypatch)
    with factory() as session:
        league, _, member = _connect(session)
        ls, _, _ = league_season(session, season=SEASON)
        ls.league_id = league.id
        session.flush()
        _channel(session, member, "member@example.com", settings, verified=True)
        subscriptions.save(session, member, league.id, morning=False, alerts=True)
        session.commit()
        outbox.sent.clear()
    ref = jobs.JobRef(1, jobs.DIGEST, league.id, None, member, 1, {"mode": "morning"})

    assert run_digest(factory, ref, settings, now=NOW) == NOT_SUBSCRIBED["morning"]
    assert outbox.sent == []


def test_a_member_with_every_topic_off_is_sent_nothing_and_the_job_says_so(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rather than a masthead with nothing under it."""
    settings = settings_for(fcp_smtp_host="smtp.example.test", fcp_email_from="fcp@example.test")
    outbox = Outbox(monkeypatch)
    with factory() as session:
        league, _, member = _connect(session)
        ls, _, _ = league_season(session, season=SEASON)
        ls.league_id = league.id
        session.flush()
        _channel(session, member, "member@example.com", settings, verified=True)
        subscriptions.save(
            session,
            member,
            league.id,
            topics=dict.fromkeys(subscriptions.TOPICS, False),
        )
        session.commit()
        outbox.sent.clear()
    ref = jobs.JobRef(1, jobs.DIGEST, league.id, None, member, 1, {"mode": "morning"})

    assert run_digest(factory, ref, settings, now=NOW) == NO_TOPICS
    assert outbox.sent == []


def test_an_alert_is_suppressed_when_its_topic_is_off(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An alert is filtered by the same topics the digest is: a reader who
    does not want his own roster's news is not interrupted for it."""
    settings = settings_for(
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
        espn_league_id=LEAGUE_ID,
    )
    outbox = Outbox(monkeypatch)
    with factory() as session:
        league, _, member = _connect(session)
        ls, (home, _), _ = league_season(session, season=SEASON)
        ls.league_id = league.id
        session.flush()
        _channel(session, member, "member@example.com", settings, verified=True)
        subscriptions.save(
            session,
            member,
            league.id,
            topics={**subscriptions.DEFAULTS, subscriptions.MY_TEAM: False},
        )
        session.commit()
        outbox.sent.clear()
        team_id = home.id
    ref = jobs.JobRef(1, jobs.DIGEST, league.id, team_id, member, 1, {"mode": "alert"})

    assert run_digest(factory, ref, settings, now=NOW) == NO_ROSTER_ALERT
    assert outbox.sent == []


def test_the_owners_digest_in_single_mode_goes_to_the_env_recipients_and_marks(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.models import PlayerStatusEvent

    settings = settings_for(
        fcp_auth_mode="single",
        espn_league_id=LEAGUE_ID,
        fcp_tracked_team_id=1,
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
        fcp_email_to="owner@example.com",
    )
    outbox = Outbox(monkeypatch)
    with factory() as session:
        ls, (home, _), _ = league_season(session, season=SEASON)
        who = player(session, "Hurt")
        snapshot(session, who, pro_team_id=10, on_team_id=1, injury_status="OUT", season=SEASON)
        session.add(
            PlayerStatusEvent(
                player_id=who.id,
                season=SEASON,
                kind="went_out",
                observed_at=NOW,
                previous={"injury_status": "ACTIVE"},
                current={"injury_status": "OUT"},
                detail={},
            )
        )
        owner = accounts.ensure_owner(session, "owner@example.com", LEAGUE_ID, 1)
        ref = jobs.JobRef(1, jobs.DIGEST, ls.league_id, home.id, owner.id, 1, {"mode": "morning"})
        session.commit()
    note = run_digest(factory, ref, settings, now=NOW)
    assert note == "sent to 1 of 1 channel(s); marked 1 event(s) notified"
    assert [target for target, _ in outbox.sent] == ["mail owner@example.com"]
    body = outbox.sent[0][1]
    # Compact by default, even here: single mode keeps every topic on, and
    # how long the message is stays a choice (docs/jobs.md, "The two forms").
    assert "SINCE YESTERDAY" in body and "Hurt" in body
    assert "YOUR ROSTER" not in body and "THE LEAGUE" not in body
    with factory() as session:
        event = session.scalars(select(PlayerStatusEvent)).one()
        assert event.notified_at is not None


def test_the_owner_who_asked_for_the_long_form_gets_it_with_every_topic_on(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single mode keeps every topic on whatever is stored; the length in
    the same row is still his, so the one setting he did choose is honoured
    rather than overridden along with the topics."""
    settings = settings_for(
        fcp_auth_mode="single",
        espn_league_id=LEAGUE_ID,
        fcp_tracked_team_id=1,
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
        fcp_email_to="owner@example.com",
    )
    outbox = Outbox(monkeypatch)
    with factory() as session:
        ls, (home, _), _ = league_season(session, season=SEASON)
        snapshot(session, player(session, "Hurt"), pro_team_id=10, on_team_id=1, season=SEASON)
        owner = accounts.ensure_owner(session, "owner@example.com", LEAGUE_ID, 1)
        _channel(session, owner.id, "owner@example.com", settings, verified=True)
        subscriptions.save(
            session,
            owner.id,
            ls.league_id,
            topics={topic: False for topic in subscriptions.TOPICS},
            length=subscriptions.FULL,
        )
        ref = jobs.JobRef(1, jobs.DIGEST, ls.league_id, home.id, owner.id, 1, {"mode": "morning"})
        session.commit()

    run_digest(factory, ref, settings, now=NOW)

    body = outbox.sent[0][1]
    assert "YOUR ROSTER" in body and "THE LEAGUE" in body, "every topic, at length"
    assert "SINCE YESTERDAY" not in body


# ---------------------------------------------------------------------------
# single mode's schedule
# ---------------------------------------------------------------------------


def test_single_modes_schedule_is_the_env_league_and_the_owners_team(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_login = ESPNSettings(
        espn_league_id=LEAGUE_ID,
        espn_swid="{X}",
        espn_s2="Y",
        espn_season=None,
        fcp_tracked_team_id=1,
    )
    monkeypatch.setattr(schedule, "get_espn_settings", lambda: fake_login)
    settings = settings_for(fcp_auth_mode="single", espn_league_id=LEAGUE_ID, fcp_tracked_team_id=1)
    with factory() as session:
        _, (home, _), _ = league_season(session, season=SEASON)
        snapshot(session, player(session, "Anyone"), pro_team_id=10, on_team_id=1, season=SEASON)
        session.commit()
        nightly = schedule.enqueue_schedule(session, settings, "nightly", NOW)
        morning = schedule.enqueue_schedule(session, settings, "morning", NOW.replace(hour=15))
        report = schedule.enqueue_schedule(
            session, settings, "report", NOW.replace(hour=22, minute=30)
        )
        assert [job.kind for job in nightly] == ["ingest", "status_pass"]
        assert [job.kind for job in morning] == ["status_pass", "precompute", "digest"]
        assert [job.kind for job in report] == ["status_pass", "digest"]
        rows = {row.id: row for row in session.scalars(select(Job)).all()}
        digest = rows[morning[2].id]
        owner = accounts.user_by_email(session, "owner@example.com")
        assert owner is not None and digest.user_id == owner.id and digest.team_id == home.id
        assert rows[morning[1].id].team_id == home.id
        assert rows[report[1].id].payload["mode"] == "alert"
        again = schedule.enqueue_schedule(
            session, settings, "morning", NOW.replace(hour=15, minute=4)
        )
        assert not any(job.created for job in again)


def test_a_requested_ingest_is_enqueued_until_one_succeeds(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        _connect(session)
        first = schedule.enqueue_schedule(session, settings_for(), "report", NOW)
        assert [job.kind for job in first if job.kind == "ingest"] == ["ingest"]
        session.add(
            IngestRun(
                espn_league_id=OTHER_LEAGUE,
                season=SEASON,
                mode="recent",
                status="succeeded",
                started_at=NOW + timedelta(minutes=1),
                detail={},
            )
        )
        session.execute(update(Job).values(state="done"))
        session.commit()
        later = schedule.enqueue_schedule(session, settings_for(), "late", NOW + timedelta(days=1))
        assert [job.kind for job in later if job.kind == "ingest"] == []


# ---------------------------------------------------------------------------
# the status pass job
# ---------------------------------------------------------------------------


def _pool_league() -> Any:
    league = fake_league(league_id=OTHER_LEAGUE, season=SEASON)
    league.teams = [fake_team(1, "Mine"), fake_team(2, "Theirs")]
    entries = [
        fake_pool_entry(501, "Held Man", on_team_id=1),
        fake_pool_entry(502, "Free Man"),
        fake_pool_entry(503, "Waiver Man", status="WAIVERS"),
    ]
    return attach_pool(league, entries)


def test_a_league_the_listener_does_not_follow_records_only_its_own_wire(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        league, _, _ = _connect(session)
    seen: list[ESPNSettings] = []

    def fetch(login: ESPNSettings) -> Any:
        seen.append(login)
        return _pool_league()

    ref = jobs.JobRef(1, jobs.STATUS_PASS, league.id, None, None, 1, {"label": "morning"})
    note = run_pass(factory, ref, settings_for(), fetch=fetch)
    assert seen[0].espn_s2 == ESPN_S2, "read with the connection's own login"
    assert note == "morning: 2 players, 0 events"
    with factory() as session:
        assert session.scalars(select(PlayerStatusSnapshot)).all() == []
        wire = session.scalars(select(FreeAgentSnapshot)).all()
        assert sorted(row.status for row in wire) == ["FREEAGENT", "WAIVERS"]
        (run,) = session.scalars(select(IngestRun)).all()
        assert run.mode == "wire" and run.status == "succeeded"


def test_the_listeners_own_league_gets_the_whole_pass(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        league, _, _ = _connect(session)
    ref = jobs.JobRef(1, jobs.STATUS_PASS, league.id, None, None, 1, {"label": "report"})
    settings = settings_for(espn_league_id=OTHER_LEAGUE)
    note = run_pass(factory, ref, settings, fetch=lambda login: _pool_league())
    assert note is not None and note.startswith("report: 3 players")
    with factory() as session:
        assert len(session.scalars(select(PlayerStatusSnapshot)).all()) == 3
        (run,) = session.scalars(select(IngestRun)).all()
        assert run.mode == "status"


# ---------------------------------------------------------------------------
# the watchdog
# ---------------------------------------------------------------------------


def test_a_connected_league_gone_stale_is_named_and_its_owner_told_to_reconnect(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(
        fcp_smtp_host="smtp.example.test",
        fcp_email_from="fcp@example.test",
        fcp_email_to="patrick@example.test",
        fcp_public_url="https://fcp.example.test",
    )
    outbox = Outbox(monkeypatch)
    now = datetime.now(UTC)
    with factory() as session:
        _, connection, owner = _connect(session)
        connection.created_at = now - timedelta(days=3)
        connection.last_error = "ESPN said: espn_s2=" + ESPN_S2  # the worst case
        _channel(session, owner, "connector@example.com", settings, verified=True)
        session.commit()
        stale = stale_connections(session, now=now)
    assert [league.espn_league_id for league in stale] == [OTHER_LEAGUE]
    note = reconnect_message(stale[0], settings.fcp_public_url)
    assert "https://fcp.example.test/account/connections" in note and "reconnect" in note
    assert ESPN_S2 not in note

    tell_owners(factory, settings, stale, now)
    where = sorted(target for target, _ in outbox.sent)
    assert where == ["mail connector@example.com", "mail patrick@example.test"]
    assert all(ESPN_S2 not in body for _, body in outbox.sent)

    with factory() as session:
        session.add(
            IngestRun(
                espn_league_id=OTHER_LEAGUE,
                season=SEASON,
                mode="recent",
                status="succeeded",
                started_at=now - timedelta(hours=2),
                detail={},
            )
        )
        session.commit()
        assert stale_connections(session, now=now) == [], "a good ingest clears it"


def test_the_worker_check_appears_only_once_the_queue_is_in_use(
    factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with factory() as session:
        assert worker_check(session, now=now) is None, "no job yet: the message is unchanged"
        league = _league(session)
        jobs.enqueue(session, jobs.INGEST, run_after=now, label="nightly", league_id=league.id)
        session.commit()
        running = worker_check(session, now=now)
        assert running is not None and running.quiet is False
        late = worker_check(session, now=now + timedelta(hours=3))
        assert late is not None and late.quiet is True
