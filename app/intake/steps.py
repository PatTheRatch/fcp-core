"""What each step of the intake chain does (docs/intake.md).

The chain and its rules are `app.intake`; the measurements are
`app.intake.measure`; where the numbers land is `app.calibration`. This
module is the eight handlers the worker calls, and it is deliberately thin:
every step reads the league, does one thing, writes it, and returns a
sentence for the job's note.

WHAT EACH STEP OWES THE ONE AFTER IT

The steps talk to each other through the database and the job payload, never
through memory: a step is retried in a new process, on a new session, after
a worker has died under it. `intake_ingest` writes the seasons it read into
its own payload and into `league_seasons`; `intake_hurdles` writes each
season's grid into its payload as it finishes it, so the next attempt starts
where the last stopped rather than at the beginning of ninety minutes.

FAILING WELL

Every failure is a `JobError` in this code's own words, so nothing of ESPN's
reply reaches `jobs.last_error` or the log (the rule in docs/jobs.md). A
refusal -- a league this code does not model -- is `retry=False`: trying it
again in twenty minutes will not make it a nine-category league.

A step with nothing to measure is **not** a failure. A league with one
unplayed season has no adds, no lanes, no decisions and no trades; the step
writes no row, says so in its note, and the number falls back to the pool or
the default, which is exactly what the fallback is for.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague, ESPNUnknownError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import calibration, intake, league_ingest, memberships
from app.calibration import Calibrated
from app.config import Settings
from app.db.models import League, LeagueSeason
from app.espn import ESPNSettings, current_season, fetch_league, prior_seasons
from app.intake import measure
from app.jobs import JobError, JobRef
from app.scoring import ranking

log = logging.getLogger("fcp.intake")

#: Seasons before the newest that are probed when ESPN's own `previousSeasons`
#: is empty or missing. Eight, which is as far back as this league goes.
PROBE_BACK = 8

NOT_A_LEAGUE = "the league is not stored"
PRIVATE = "ESPN refused the login for this league; whoever connected it needs to reconnect"
UNREACHABLE = "ESPN could not be reached, or answered with an error"
NOTHING_READ = "ESPN offered no season of this league that the login can read"


def _league(session: Session, job: JobRef) -> League:
    league = session.get(League, job.league_id) if job.league_id is not None else None
    if league is None:
        raise JobError(NOT_A_LEAGUE, retry=False)
    return league


def _refuse_unless_nine_cat(session: Session, league_id: int) -> None:
    """Stop the chain on a league these reports do not model.

    Checked at the top of every measuring step rather than once, because the
    scoring type is only known after the ingest has run and a chain can be
    resumed from any step by hand.
    """
    refusal = intake.refusal(session, league_id)
    if refusal is not None:
        raise JobError(refusal.reason[:200], retry=False)


def _login(session: Session, league: League, settings: Settings) -> ESPNSettings:
    from app.job_kinds import league_login

    login, _connection = league_login(session, league, settings)
    return login


# ---------------------------------------------------------------------------
# 1. every season ESPN will give us
# ---------------------------------------------------------------------------


def _seasons_offered(login: ESPNSettings, newest: int) -> tuple[list[int], list[int], int | None]:
    """(readable, not offered, the newest that read), newest first.

    The survey's own rules (docs/league_survey.md): a season ESPN does not
    hold for this league answers 404 and is "not offered", which is ordinary
    -- a league renewed in 2021 has no 2019 -- and a 401 means the login does
    not open it, which stops the whole step rather than being counted as a
    gap.
    """
    readable: list[int] = []
    missing: list[int] = []
    top: int | None = None
    wanted = list(range(newest, newest - PROBE_BACK - 1, -1))
    for season in wanted:
        try:
            league = fetch_league(login, season=season)
        except ESPNAccessDenied:
            raise JobError(PRIVATE, retry=False) from None
        except ESPNInvalidLeague:
            missing.append(season)
            continue
        except (ESPNUnknownError, requests.RequestException):
            raise JobError(UNREACHABLE) from None
        readable.append(season)
        if top is None:
            top = season
            # ESPN tells a league its own earlier seasons; trust that over
            # probing years backwards past the point it was created.
            earlier = [year for year in prior_seasons(league) if year < season]
            if earlier:
                wanted = [year for year in wanted if year >= min(earlier)]
    return readable, missing, top


def run_intake_ingest(
    factory: sessionmaker[Session], job: JobRef, settings: Settings
) -> str | None:
    """Every season of this league the login can read, newest first.

    Each one is ingested in full (`app.league_ingest`), oldest first so the
    newest season is the last thing written and a chain stopped halfway
    leaves the database consistent about what it holds. The seasons that
    landed go into the job's payload, which is where the steps after it read
    what there is to measure.

    And the newest season's draft date (`draft`): the fact every projection
    gates on (`app.inseason.drafted`, docs/intake.md "The draft date"). The
    ingest already stored it with the settings; the payload says it, so a
    league's intake record shows when its rosters become real.
    """
    with factory() as session:
        league = _league(session, job)
        espn_league_id = int(league.espn_league_id)
        login = _login(session, league, settings)

    readable, missing, _top = _seasons_offered(login, current_season() + 1)
    if not readable:
        raise JobError(NOTHING_READ, retry=False)

    landed: list[int] = []
    for season in sorted(readable):
        try:
            fetched = fetch_league(login, season=season)
            league_ingest.ingest_one_season(factory, login, fetched)
        except (ESPNAccessDenied, ESPNInvalidLeague):
            missing.append(season)
            continue
        except (ESPNUnknownError, requests.RequestException):
            raise JobError(UNREACHABLE) from None
        landed.append(season)

    with factory() as session:
        league = _league(session, job)
        _record(
            session,
            job,
            {
                "seasons": landed,
                "not_offered": sorted(set(missing)),
                "draft": _newest_draft(session, league),
                "ranking": _newest_ranking(session, league),
            },
        )
        connection = memberships.active_connection(session, league.id)
        if connection is not None:
            memberships.record_check(session, connection, None)
            memberships.verify_connection_owner(session, connection, settings)
        session.commit()
    return f"league {espn_league_id}: read {len(landed)} season(s), {_years(landed)}"


def _newest_draft(session: Session, league: League) -> dict[str, Any] | None:
    """The newest stored season's draft: its season, type and date, or None."""
    newest = session.scalar(
        select(LeagueSeason)
        .where(LeagueSeason.league_id == league.id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )
    if newest is None:
        return None
    return {
        "season": int(newest.season),
        "type": newest.draft_type,
        "drafted_at": newest.drafted_at.isoformat() if newest.drafted_at else None,
    }


def _newest_ranking(session: Session, league: League) -> dict[str, Any] | None:
    """The newest stored season's scoring type and the order it ranks by, or None.

    The fact the standings, the projected places and the playoff odds gate on
    (`app.scoring.ranking`, docs/intake.md "The scoring type").
    """
    newest = session.scalar(
        select(LeagueSeason)
        .where(LeagueSeason.league_id == league.id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )
    if newest is None:
        return None
    return {"season": int(newest.season), **ranking.describe(newest)}


def _years(seasons: list[int]) -> str:
    return ", ".join(str(season) for season in sorted(seasons)) or "none"


def _record(session: Session, job: JobRef, more: dict[str, Any]) -> None:
    """Add to this job's own payload, so the next attempt and the summary
    can read what this one did."""
    from app.db.models import Job

    row = session.get(Job, job.id)
    if row is None:  # pragma: no cover - the job is being run
        return
    row.payload = {**dict(row.payload or {}), **more}


def read_payload(session: Session, league_id: int, kind: str) -> dict[str, Any]:
    """The newest job of this kind for this league, and what it recorded."""
    from sqlalchemy import select

    from app.db.models import Job

    found = session.scalar(
        select(Job)
        .where(Job.league_id == league_id, Job.kind == kind)
        .order_by(Job.id.desc())
        .limit(1)
    )
    return dict(found.payload or {}) if found is not None else {}


# ---------------------------------------------------------------------------
# 2. the NBA schedules behind them
# ---------------------------------------------------------------------------


def run_intake_schedule(
    factory: sessionmaker[Session], job: JobRef, settings: Settings
) -> str | None:
    """The NBA's schedule for every season this league brought with it.

    Shared across leagues -- a schedule is the NBA's, not ESPN's fantasy
    game's -- so a season another league already stored is skipped without a
    fetch. Without it every recommender refuses the season
    ("no NBA schedule is stored"), which is why this is a step and not a
    footnote.
    """
    from scripts.backfill_pro_schedule import backfill_season, stored_games

    with factory() as session:
        league = _league(session, job)
        login = _login(session, league, settings)
        seasons = measure.stored_seasons(session, league.id)

    stored: list[int] = []
    skipped: list[int] = []
    for season in seasons:
        with factory() as session:
            if stored_games(session, season):
                skipped.append(season)
                continue
            try:
                written, _before = backfill_season(session, login, season)
            except (ESPNAccessDenied, ESPNInvalidLeague):
                continue
            except (ESPNUnknownError, requests.RequestException):
                raise JobError(UNREACHABLE) from None
            session.commit()
        if written:
            stored.append(season)
    return (
        f"stored {len(stored)} season(s) of NBA schedule ({_years(stored)}); "
        f"{len(skipped)} already there"
    )


# ---------------------------------------------------------------------------
# 3-6. the measurements
# ---------------------------------------------------------------------------


def _store(
    session: Session, league_id: int, found: measure.Measurement, *, note: str | None = None
) -> Calibrated | None:
    """One measurement into `league_calibrations`, with its settings digest.

    The digest travels with the row because the pooled rows are grouped on
    it: a league that changes shape between one measurement and the next
    joins a different pool, and nothing has to go back and ask what shape it
    was in when it was measured.
    """
    shape = calibration.settings_key(session, league_id)
    payload = dict(found.payload)
    if shape is not None:
        payload |= {"settings": shape.as_json(), "settings_digest": shape.digest}
    return calibration.write(
        session,
        league_id,
        found.key,
        value=found.value,
        source=calibration.MEASURED,
        n=found.n,
        note=note if note is not None else calibration.measured_note(found.key, found.n),
        payload=payload,
        run_seconds=found.run_seconds,
    )


def _nothing_measured(key: str, why: str) -> str:
    return f"{key}: nothing to measure yet ({why}); it keeps the fallback"


def run_intake_replacement(factory: sessionmaker[Session], job: JobRef) -> str | None:
    with factory() as session:
        league = _league(session, job)
        _refuse_unless_nine_cat(session, league.id)
        found = measure.measure_replacement(session, league.id)
        if found.value is None:
            return _nothing_measured("typical_pickup", "no season has an executed add with starts")
        kept = _store(session, league.id, found)
        session.commit()
    return _said("typical_pickup", found, kept)


def run_intake_lane(factory: sessionmaker[Session], job: JobRef) -> str | None:
    with factory() as session:
        league = _league(session, job)
        _refuse_unless_nine_cat(session, league.id)
        found = measure.measure_lane(session, league.id)
        if found.value is None:
            return _nothing_measured("opened_place", "no team-period held a streamed place")
        kept = _store(session, league.id, found)
        session.commit()
    return _said("opened_place", found, kept)


def run_intake_hurdles(factory: sessionmaker[Session], job: JobRef) -> str | None:
    """The sweep, one season at a time, resumable.

    The long one: about ninety minutes for fourteen teams over eight seasons.
    Each season's grid is written into this job's payload as soon as it is
    finished, so an attempt that dies in the fifth season resumes at the
    fifth rather than the first, and a step re-run by hand after a fix costs
    only the seasons it has not done.
    """
    with factory() as session:
        league = _league(session, job)
        league_id = int(league.id)
        _refuse_unless_nine_cat(session, league_id)
        seasons = measure.stored_seasons(session, league_id)
        done = dict(read_payload(session, league_id, job.kind).get("swept") or {})

    swept: list[measure.SweptSeason] = [
        measure.SweptSeason(
            season=int(year),
            cells=dict(body["cells"]),
            decisions=int(body["decisions"]),
            teams=int(body["teams"]),
            baseline_week=float(body["baseline_week"]),
            baseline_season=float(body["baseline_season"]),
            baseline_n=int(body["baseline_n"]),
            run_seconds=float(body["run_seconds"]),
        )
        for year, body in sorted(done.items())
    ]
    for season in seasons:
        if str(season) in done:
            continue
        with factory() as session:
            found = measure.sweep_one_season(session, league_id, season)
        if found is None:
            done[str(season)] = {}
            continue
        swept.append(found)
        done[str(season)] = {
            "cells": found.cells,
            "decisions": found.decisions,
            "teams": found.teams,
            "baseline_week": found.baseline_week,
            "baseline_season": found.baseline_season,
            "baseline_n": found.baseline_n,
            "run_seconds": found.run_seconds,
        }
        with factory() as session:
            _record(session, job, {"swept": done})
            session.commit()
        log.info("league %s: swept %s in %.0fs", league_id, season, found.run_seconds)

    if not swept:
        return _nothing_measured("the bars", "no season has a matchup period to replay")
    measured = measure.hurdles_from(swept)
    said: list[str] = []
    with factory() as session:
        for key, bar in measured.items():
            kept = _store(session, league_id, bar)
            said.append(_said(key, bar, kept))
        session.commit()
    return "; ".join(said)


def run_intake_trades(factory: sessionmaker[Session], job: JobRef) -> str | None:
    """The trade record, with this league's own revision history carried.

    What an earlier revision of the evaluator was mis-pricing consolidating
    deals by is a fact about that revision, and a league that has it does not
    stop having it because it was measured again. So the stored row's
    revision figures are read first and handed to the run, which is what
    keeps this league's note the sentence it has always printed.
    """
    with factory() as session:
        league = _league(session, job)
        _refuse_unless_nine_cat(session, league.id)
        before = calibration.stored(session, league.id, calibration.TRADE_RECORD)
        carried = dict((before.payload or {}).get("uneven_error") or {}) if before else {}
        found = measure.measure_trades(session, league.id, carry=carried)
        if not found.n:
            return _nothing_measured("trade_record", "no trade can be both forecast and graded")
        kept = _store(session, league.id, found, note=measure.trade_note_for(found.payload))
        session.commit()
    return _said("trade_record", found, kept)


def _said(key: str, found: measure.Measurement, kept: Calibrated | None) -> str:
    """The job's note: what was measured, and whether it is being used."""
    head = "-" if found.value is None else f"{found.value:.2f}"
    if kept is None:
        return f"{key} measured at {head} ({found.n}); your own choice is kept"
    if found.n < calibration.minimum(key):
        return f"{key} measured at {head} on only {found.n}; too little to use yet"
    return f"{key} = {head} on {found.n} ({found.run_seconds:.0f}s)"


# ---------------------------------------------------------------------------
# 7. the pool
# ---------------------------------------------------------------------------


def run_intake_pool(factory: sessionmaker[Session], job: JobRef) -> str | None:
    """Rebuild every pooled row, over every league measured so far.

    After this league's own numbers, because this league has just joined the
    pool and the leagues already in it should get the benefit of it on the
    next page they open.
    """
    with factory() as session:
        written = calibration.recompute_pool(session)
        session.commit()
    if not written:
        return (
            f"no pooled row yet: a pool needs {calibration.POOL_LEAGUES} leagues of the "
            "same shape, each measured"
        )
    leagues = max(int(row.payload.get("leagues", 0)) for row in written)
    return f"{len(written)} pooled row(s), over {leagues} league(s) of the same shape"


# ---------------------------------------------------------------------------
# 8. the summary, and the email
# ---------------------------------------------------------------------------


def run_intake_done(
    factory: sessionmaker[Session], job: JobRef, settings: Settings, at: datetime | None = None
) -> str | None:
    """Write the summary and send it to whoever connected the league."""
    from app.intake.summary import deliver, summarise

    when = at or datetime.now(UTC)
    with factory() as session:
        league = _league(session, job)
        summary = summarise(session, league, at=when, settings=settings)
        note = deliver(session, summary, job=job, settings=settings)
        session.commit()
    return note
