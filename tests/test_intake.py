"""The intake chain: its order, its refusals and its message (docs/intake.md).

What is pinned: the eight steps are enqueued in order, each waiting on the
one before it, with the sweep at the queue's lowest priority; a step that
fails takes the rest of the chain down with it rather than letting the email
say "ready"; a league this code does not model is refused with what it is
scored on in the sentence, and nothing else is enqueued; one chain per league
at a time, and one a day; and the email's exact words, rendered to a string
and delivered nowhere.

ESPN is never reached. The two ESPN-facing steps are replaced by fake
handlers, which is the only way the chain's dependency order can be exercised
at all -- and is why docs/intake.md says plainly that they are unexercised.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, calibration, channels, intake, jobs, memberships
from app.config import Settings, get_settings
from app.db.models import Job, League, LeagueSeason, LeagueSeasonCategory
from app.intake import steps, summary
from app.jobs import JobError, JobRef
from app.secrets_box import new_key
from tests.scoring_db import league_season

LEAGUE = 4242424
NOW = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
KEY = new_key()


def settings_for(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_secrets_key": KEY,
        "fcp_owner_email": "owner@example.com",
        "espn_league_id": None,
        "fcp_tracked_team_id": None,
        "fcp_smtp_host": None,
        "fcp_email_from": None,
        "fcp_email_to": None,
        "fcp_public_url": "https://fcp.example.test",
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


@pytest.fixture
def factory(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        session.execute(
            text(
                "TRUNCATE jobs, leagues, players, owners, users, league_calibrations "
                "RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
    yield scoring_factory


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as found:
        yield found


def _nine_cat(session: Session, espn_league_id: int = LEAGUE) -> League:
    """A league with one nine-category season, as the ingest would leave it."""
    league = memberships.get_or_create_league(session, espn_league_id)
    season, _teams, _periods = league_season(session, season=2026)
    season.league_id = league.id
    session.commit()
    return league


def _points_league(session: Session, espn_league_id: int = 5151515) -> League:
    league = _nine_cat(session, espn_league_id)
    season = session.scalar(select(LeagueSeason).where(LeagueSeason.league_id == league.id))
    assert season is not None
    season.scoring_type = "H2H_POINTS"
    session.commit()
    return league


# ---------------------------------------------------------------------------
# what this code can measure
# ---------------------------------------------------------------------------


def test_a_nine_category_league_is_not_refused(session: Session) -> None:
    league = _nine_cat(session)
    assert intake.refusal(session, league.id) is None


def test_a_points_league_is_refused_and_told_what_it_is(session: Session) -> None:
    league = _points_league(session)
    refused = intake.refusal(session, league.id)
    assert refused is not None
    assert refused.scoring == "H2H_POINTS"
    assert "scored on points, head to head" in refused.reason
    assert "not supported yet" in refused.reason


def test_an_eight_category_league_is_refused_by_its_count(session: Session) -> None:
    league = _nine_cat(session, 5252525)
    season = session.scalar(select(LeagueSeason).where(LeagueSeason.league_id == league.id))
    assert season is not None
    dropped = session.scalars(
        select(LeagueSeasonCategory)
        .where(LeagueSeasonCategory.league_season_id == season.id)
        .limit(1)
    ).one()
    session.delete(dropped)
    session.commit()
    refused = intake.refusal(session, league.id)
    assert refused is not None
    assert "scores 8 categories" in refused.reason


def test_a_league_with_no_season_yet_has_nothing_to_measure(session: Session) -> None:
    league = memberships.get_or_create_league(session, 5353535)
    session.commit()
    refused = intake.refusal(session, league.id)
    assert refused is not None
    assert "no season of this league has been read from ESPN yet" in refused.reason


def test_a_refused_league_enqueues_nothing_and_stops_the_step(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        league = _points_league(session)
        with pytest.raises(JobError) as raised:
            steps.run_intake_replacement(
                factory, JobRef(1, jobs.INTAKE_REPLACEMENT, league.id, None, None, 1)
            )
    assert raised.value.retry is False, "twenty minutes will not make it nine categories"
    assert "head-to-head categories" in raised.value.message


# ---------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------


def test_the_chain_is_eight_steps_each_waiting_on_the_last(session: Session) -> None:
    league = _nine_cat(session)
    added = intake.enqueue_intake(session, league.id, at=NOW, user_id=None)
    session.commit()
    assert [job.kind for job in added] == list(intake.STEPS)

    rows = {job.kind: session.get(Job, job.id) for job in added}
    previous: int | None = None
    for kind in intake.STEPS:
        row = rows[kind]
        assert row is not None
        assert row.depends_on == previous, f"{kind} waits on the step before it"
        previous = row.id
    assert rows[jobs.INTAKE_HURDLES].priority == jobs.LOW, "ninety minutes goes last"
    assert all(
        rows[kind].priority == jobs.NORMAL for kind in intake.STEPS if kind != jobs.INTAKE_HURDLES
    )


def test_the_low_priority_sweep_waits_behind_an_ordinary_job(session: Session) -> None:
    """Priority is read before `run_after`: a morning's precompute first."""
    league = _nine_cat(session)
    jobs.enqueue(
        session,
        jobs.INTAKE_HURDLES,
        run_after=NOW - timedelta(hours=2),
        label="intake",
        league_id=league.id,
        priority=jobs.LOW,
    )
    ordinary = jobs.enqueue(
        session, jobs.INGEST, run_after=NOW, label="nightly", league_id=league.id
    )
    session.commit()
    taken = jobs.lock_next(session, NOW)
    assert taken is not None and taken.id == ordinary.id
    session.rollback()


def test_a_failed_step_takes_the_rest_of_the_chain_with_it(
    factory: sessionmaker[Session],
) -> None:
    """`fail_orphans`: nothing after a failure runs, and the email never
    goes out saying "ready" over one."""
    with factory() as session:
        league = _nine_cat(session)
        league_id = int(league.id)
        added = intake.enqueue_intake(session, league_id, at=NOW, user_id=None)
        session.commit()
        ids = {job.kind: job.id for job in added}

    def blows_up(_factory: sessionmaker[Session], _job: JobRef) -> str | None:
        raise JobError("ESPN could not be reached, or answered with an error")

    fine: dict[str, jobs.Handler] = {kind: lambda f, j: "done" for kind in intake.STEPS}
    fine[jobs.INTAKE_SCHEDULE] = blows_up

    # Three attempts, each a failure, then the step is given up on.
    at = NOW
    for _ in range(jobs.MAX_ATTEMPTS * len(intake.STEPS)):
        outcome = jobs.run_next(factory, fine, at=at)
        if outcome is None:
            break
        at = at + timedelta(hours=1)

    with factory() as session:
        states = {
            job.kind: (job.state, job.last_error)
            for job in session.scalars(select(Job).where(Job.id.in_(ids.values()))).all()
        }
    assert states[jobs.INTAKE_INGEST][0] == jobs.DONE
    assert states[jobs.INTAKE_SCHEDULE][0] == jobs.FAILED
    for kind in intake.STEPS[2:]:
        assert states[kind][0] == jobs.FAILED, f"{kind} never ran"
        assert states[kind][1] == jobs.GAVE_UP_ON_PREREQUISITE

    with factory() as session:
        state = intake.progress(session, league_id)
    assert state.running is False
    assert state.step == jobs.INTAKE_SCHEDULE
    assert "storing the NBA schedules" in state.words
    assert next(kind for kind, _why in state.failed) == jobs.INTAKE_SCHEDULE


def test_one_intake_at_a_time_and_one_a_day(session: Session) -> None:
    league = _nine_cat(session)
    intake.enqueue_intake(session, league.id, at=NOW, user_id=None)
    session.commit()

    with pytest.raises(intake.IntakeRefusedError, match="already being measured"):
        intake.enqueue_intake(session, league.id, at=NOW + timedelta(minutes=5), user_id=None)

    # Finished, and still refused the same day.
    session.execute(text("UPDATE jobs SET state = 'done', finished_at = now()"))
    session.commit()
    with pytest.raises(intake.IntakeRefusedError, match="measured today"):
        intake.enqueue_intake(session, league.id, at=NOW + timedelta(hours=3), user_id=None)

    # Tomorrow it may be measured again.
    again = intake.enqueue_intake(session, league.id, at=NOW + timedelta(days=1), user_id=None)
    session.commit()
    assert all(job.created for job in again)


def test_the_same_day_twice_with_force_adds_nothing(session: Session) -> None:
    """The timer's path: the dedupe key, not a refusal (`app.schedule`)."""
    league = _nine_cat(session)
    first = intake.enqueue_intake(session, league.id, at=NOW, force=True)
    second = intake.enqueue_intake(session, league.id, at=NOW + timedelta(minutes=4), force=True)
    session.commit()
    assert all(job.created for job in first)
    assert not any(job.created for job in second)
    assert [job.id for job in first] == [job.id for job in second]


def test_the_progress_says_which_step_a_league_is_on(session: Session) -> None:
    league = _nine_cat(session)
    intake.enqueue_intake(session, league.id, at=NOW, user_id=None)
    session.commit()
    assert intake.progress(session, league.id).step == jobs.INTAKE_INGEST
    assert intake.progress(session, league.id).words == "reading your seasons from ESPN"
    assert intake.progress(session, league.id).running

    session.execute(
        text("UPDATE jobs SET state = 'done' WHERE kind IN ('intake_ingest', 'intake_schedule')")
    )
    session.commit()
    assert intake.progress(session, league.id).step == jobs.INTAKE_REPLACEMENT


def test_a_league_never_measured_says_so(session: Session) -> None:
    league = _nine_cat(session)
    state = intake.progress(session, league.id)
    assert (state.running, state.step, state.words) == (False, None, "not measured yet")


def test_every_step_has_a_handler_and_words_for_a_page() -> None:
    from app.job_kinds import handlers

    bound = handlers(settings_for())
    for kind in intake.STEPS:
        assert kind in bound, f"{kind} has a handler"
        assert kind in intake.STEP_WORDS, f"{kind} has words a page can print"
    assert set(intake.STEPS) == set(jobs.INTAKE_KINDS)


# ---------------------------------------------------------------------------
# the pool step
# ---------------------------------------------------------------------------


def test_the_pool_step_says_plainly_when_there_is_no_pool_yet(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        league = _nine_cat(session)
        job = JobRef(1, jobs.INTAKE_POOL, league.id, None, None, 1)
    note = steps.run_intake_pool(factory, job)
    assert note is not None and "needs 2 leagues" in note


# ---------------------------------------------------------------------------
# the message
# ---------------------------------------------------------------------------


def _summary(session: Session, league: League, **changes: Any) -> summary.Summary:
    built = summary.summarise(session, league, at=NOW, settings=settings_for())
    return built if not changes else type(built)(**{**vars(built), **changes})


def test_the_email_names_every_number_with_its_sample(session: Session) -> None:
    league = _nine_cat(session)
    calibration.write(
        session,
        league.id,
        calibration.OPENED_PLACE,
        value=0.34,
        source=calibration.MEASURED,
        n=1120,
        note=calibration.measured_note(calibration.OPENED_PLACE, 1120),
    )
    session.commit()

    mail = summary.mail_for(_summary(session, league), public_url="https://fcp.example.test")
    assert mail.subject.endswith("your league's numbers are ready")
    text_part = mail.text
    assert "What an open place is worth: 0.34 categories a week" in text_part
    assert "measured on this league, 1,120 team-periods" in text_part
    for key in calibration.KEYS:
        assert calibration.DEFAULTS[key].title in text_part, f"{key} is in the message"
    assert "the default" in text_part, "and the ones falling back say so"
    assert "https://fcp.example.test/account/connections" in text_part
    assert "it never hides one" in text_part
    assert "<html" in (mail.html or ""), "the same words in the house style"


def test_the_email_never_says_ready_over_a_failure(session: Session) -> None:
    league = _nine_cat(session)
    built = _summary(
        session, league, failed=((jobs.INTAKE_HURDLES, "the worker stopped while running it"),)
    )
    mail = summary.mail_for(built, public_url="https://fcp.example.test")
    assert "unfinished" in mail.subject
    assert "ready" not in mail.subject
    assert "WHAT DID NOT FINISH" in mail.text
    assert "backtesting the bars (the long one)" in mail.text
    assert "using the fallback" in mail.text


def test_the_email_says_how_much_history_there_is(session: Session) -> None:
    league = _nine_cat(session)
    built = _summary(session, league, seasons=(2019, 2020, 2021, 2026))
    assert built.history == "4 seasons, 2019 to 2026"
    assert "Seasons read: 4 seasons, 2019 to 2026." in summary.mail_for(built).text

    thin = _summary(session, league, seasons=())
    assert thin.history == "no played season yet"


def test_the_email_mentions_no_other_league(session: Session) -> None:
    """The pool is the only thing another league contributes, and the only
    thing said about it is how many leagues are in it."""
    league = _nine_cat(session)
    body = summary.mail_for(_summary(session, league)).text
    for forbidden in ("3853870", "Patriot", "Full Court Press"):
        assert forbidden not in body


def test_the_message_goes_nowhere_when_nobody_has_confirmed_an_address(
    session: Session,
) -> None:
    league = _nine_cat(session)
    user = accounts.get_or_create_user(session, "connector@example.com")
    session.commit()
    note = summary.deliver(
        session,
        _summary(session, league),
        job=JobRef(1, jobs.INTAKE_DONE, league.id, None, user.id, 1),
        settings=settings_for(),
    )
    assert summary.NO_ADDRESS in note
    assert "measured on" in note, "the numbers are measured either way"


def test_the_message_reaches_a_confirmed_address(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(fcp_smtp_host="mail.example.test", fcp_email_from="fcp@example.test")
    league = _nine_cat(session)
    user = accounts.get_or_create_user(session, "connector@example.com")
    added = channels.add(session, user.id, channels.EMAIL, "connector@example.com", settings)
    channels.verify(session, user.id, added.secret)
    session.commit()

    sent: list[str] = []

    def fake_send(*_args: Any, **kwargs: Any) -> None:
        sent.append(str(kwargs.get("subject", "")))

    monkeypatch.setattr("app.notify.send_email", fake_send)
    note = summary.deliver(
        session,
        _summary(session, league),
        job=JobRef(1, jobs.INTAKE_DONE, league.id, None, user.id, 1),
        settings=settings,
    )
    assert sent and "numbers" in sent[0]
    assert "sent to 1 of 1 address(es)" in note


def test_the_done_step_runs_and_writes_its_summary(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        league = _nine_cat(session)
        job = JobRef(1, jobs.INTAKE_DONE, league.id, None, None, 1)
    note = steps.run_intake_done(factory, job, settings_for(), NOW)
    assert note is not None and summary.NO_ADDRESS in note


# ---------------------------------------------------------------------------
# what a step says when there is nothing to measure
# ---------------------------------------------------------------------------


def test_a_league_with_no_adds_is_not_a_failure(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        league = _nine_cat(session)
        job = JobRef(1, jobs.INTAKE_REPLACEMENT, league.id, None, None, 1)
    note = steps.run_intake_replacement(factory, job)
    assert note is not None
    assert "nothing to measure yet" in note and "keeps the fallback" in note
    with factory() as session:
        assert calibration.stored(session, league.id, calibration.TYPICAL_PICKUP) is None


def test_a_league_with_no_lane_is_not_a_failure(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        league = _nine_cat(session)
        job = JobRef(1, jobs.INTAKE_LANE, league.id, None, None, 1)
    note = steps.run_intake_lane(factory, job)
    assert note is not None and "nothing to measure yet" in note


def test_a_league_with_no_tradeable_deal_is_not_a_failure(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        league = _nine_cat(session)
        job = JobRef(1, jobs.INTAKE_TRADES, league.id, None, None, 1)
    note = steps.run_intake_trades(factory, job)
    assert note is not None and "nothing to measure yet" in note
