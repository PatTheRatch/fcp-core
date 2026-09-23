"""Giving a newly connected league the same treatment ours had: the chain.

docs/intake.md is the whole story; `app.calibration` is where the numbers
land and `app.intake.measure` is what measures them.

WHAT IT IS FOR

Every number the recommenders lean on was measured on one league. A second
league connecting to this server should get its own, without anybody running
four scripts by hand: every season ESPN will give us, the NBA schedules
behind them, and each of the measurements run on ITS history, then an email
saying it is ready with its own numbers in it.

THE CHAIN

Eight jobs, each waiting on the one before it (`depends_on`), so a step that
fails parks with its own error and nothing after it runs at all
(`jobs.fail_orphans`):

| step | does |
|---|---|
| `intake_ingest` | every season the login can read, newest first |
| `intake_schedule` | the NBA schedule behind each of them |
| `intake_replacement` | `typical_pickup` |
| `intake_lane` | `opened_place` |
| `intake_hurdles` | the sweep, and the three bars |
| `intake_trades` | `trade_record` |
| `intake_pool` | the pooled rows, over every league measured so far |
| `intake_done` | the summary, and the email to whoever connected it |

Why eight kinds rather than one job with eight stages: the queue already
knows how to hold a job back until another finishes, how to fail the rest
when one fails, how to retry a step with a backoff and how to show a person
which step a league is on. A stage machine inside one job would be all of
that written again, badly, inside a two-hour lease.

THE RULES

* **One intake at a time per league.** The dedupe key is the kind, the
  league and the day; `running` says one is under way, and `enqueue_intake`
  refuses rather than adding a second.
* **Leagues one after another, not in parallel.** The ESPN-facing step runs
  in the one worker that takes the queue, so two leagues' backfills never
  reach ESPN at once whatever the queue holds.
* **Every step is idempotent.** A schedule already stored is skipped, a
  season already swept is read out of the payload, and a measurement simply
  overwrites its row. A step run twice writes what it wrote.
* **A failure is never "ready".** The email names the steps that did not
  finish and says what each number is falling back to; it never says a
  league is measured when it is not.
* **The hurdle sweep is `jobs.LOW`.** An hour and a half of category replay
  must never be in front of a morning's precomputes.

WHAT IS REFUSED

A league this code cannot model is refused at the first step, with the
reason, and nothing else is enqueued: the recommenders count categories, and
a points or roto league is not a league they have anything to say about.
`refusal` is the check, and it is made from what the ingest stored
(`league_seasons.scoring_type` and the category rows), never from a guess.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs
from app.db.models import Job, League, LeagueSeason, LeagueSeasonCategory

log = logging.getLogger("fcp.intake")

#: The schedule label every job of a chain carries, so the queue can be read
#: by eye and `dedupe_key` holds one chain a day per league.
LABEL = "intake"

#: The steps, in order. Each waits on the one before it.
STEPS = (
    jobs.INTAKE_INGEST,
    jobs.INTAKE_SCHEDULE,
    jobs.INTAKE_REPLACEMENT,
    jobs.INTAKE_LANE,
    jobs.INTAKE_HURDLES,
    jobs.INTAKE_TRADES,
    jobs.INTAKE_POOL,
    jobs.INTAKE_DONE,
)

#: What a page calls each step, so "which step is it on" is a sentence.
STEP_WORDS = {
    jobs.INTAKE_INGEST: "reading your seasons from ESPN",
    jobs.INTAKE_SCHEDULE: "storing the NBA schedules behind them",
    jobs.INTAKE_REPLACEMENT: "measuring what a pickup is worth",
    jobs.INTAKE_LANE: "measuring what an open roster place is worth",
    jobs.INTAKE_HURDLES: "backtesting the bars (the long one)",
    jobs.INTAKE_TRADES: "replaying your trades",
    jobs.INTAKE_POOL: "comparing you with the other leagues measured",
    jobs.INTAKE_DONE: "writing to you",
}

#: How often a league may ask for its numbers to be measured again. Once a
#: day: the sweep is an hour and a half, and nothing about a league's own
#: history changes fast enough for a second run in one day to say anything.
AGAIN_AFTER = timedelta(days=1)

#: The scoring type this code models, as ESPN spells it.
H2H_CATEGORIES = "H2H_CATEGORY"

#: How many categories the recommenders count. Nine is what
#: `app.pickups.judge.CONTESTED_CATEGORIES` assumes a week adds up to, and
#: every distribution, every record and every bar is in those units.
NINE = 9


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# what this code can and cannot measure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """Why a league is not one this code can measure, in plain words."""

    #: What the league is, so the email can say it back to him.
    scoring: str
    reason: str


def refusal(session: Session, league_id: int) -> Refusal | None:
    """None when this league is one the recommenders understand.

    Read off the newest season the ingest stored, because that is what the
    reports would be built from: its `scoring_type`, and how many categories
    it scores. A league scored on points, or on rotisserie standings, or on
    some other number of categories, is refused with its own description in
    the sentence -- "yours is scored on points" -- rather than a shrug.
    """
    season = session.scalar(
        select(LeagueSeason)
        .where(LeagueSeason.league_id == league_id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )
    if season is None:
        return Refusal(
            scoring="not known yet",
            reason=(
                "no season of this league has been read from ESPN yet, so there is "
                "nothing to measure"
            ),
        )
    scoring = str(season.scoring_type or "").strip() or "not stated"
    if scoring != H2H_CATEGORIES:
        return Refusal(
            scoring=scoring,
            reason=(
                f"this league is scored {_scoring_words(scoring)}, and these reports "
                "only understand head-to-head categories; they are not supported yet"
            ),
        )
    counted = len(
        session.scalars(
            select(LeagueSeasonCategory.id).where(
                LeagueSeasonCategory.league_season_id == season.id
            )
        ).all()
    )
    if counted != NINE:
        return Refusal(
            scoring=f"{scoring}, {counted} categories",
            reason=(
                f"this league scores {counted} categories and these reports are built "
                "for the nine; they are not supported yet"
            ),
        )
    return None


def _scoring_words(scoring: str) -> str:
    """ESPN's own word for a scoring type, said the way a person would."""
    return {
        "H2H_POINTS": "on points, head to head",
        "POINTS": "on points",
        "ROTO": "on rotisserie standings",
        "H2H_MOST_CATEGORIES": "head to head, most categories",
        "H2H_EACH_CATEGORY": "head to head, each category",
    }.get(scoring, f"as {scoring}")


# ---------------------------------------------------------------------------
# the state of a league's intake
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Progress:
    """Where a league's intake has got to, for the account page and the email."""

    #: True while any step of the newest chain is queued or running.
    running: bool
    #: The step being worked on, or the one that failed.
    step: str | None
    #: What a page prints: "backtesting the bars (the long one)".
    words: str
    #: Steps that ended `failed`, with the sentence each parked with.
    failed: tuple[tuple[str, str], ...] = ()
    #: When the newest chain was enqueued.
    started_at: datetime | None = None
    #: When the last one finished, however it finished.
    finished_at: datetime | None = None


def chain_jobs(session: Session, league_id: int) -> list[Job]:
    """The newest chain's jobs, in the order the steps run."""
    rows = session.scalars(
        select(Job)
        .where(Job.league_id == league_id, Job.kind.in_(STEPS))
        .order_by(Job.id.desc())
        .limit(len(STEPS))
    ).all()
    order = {kind: index for index, kind in enumerate(STEPS)}
    return sorted(rows, key=lambda job: order.get(job.kind, len(STEPS)))


def progress(session: Session, league_id: int) -> Progress:
    """What to tell a person about this league's intake."""
    found = chain_jobs(session, league_id)
    if not found:
        return Progress(running=False, step=None, words="not measured yet")
    running = [job for job in found if job.state in (jobs.QUEUED, jobs.RUNNING)]
    failed = tuple(
        (job.kind, job.last_error or "it failed") for job in found if job.state == jobs.FAILED
    )
    started = min((job.created_at for job in found), default=None)
    finished = max((job.finished_at for job in found if job.finished_at), default=None)
    if running:
        step = running[0].kind
        return Progress(
            running=True,
            step=step,
            words=STEP_WORDS.get(step, step),
            failed=failed,
            started_at=started,
            finished_at=finished,
        )
    if failed:
        return Progress(
            running=False,
            step=failed[0][0],
            words=f"stopped at: {STEP_WORDS.get(failed[0][0], failed[0][0])}",
            failed=failed,
            started_at=started,
            finished_at=finished,
        )
    return Progress(
        running=False,
        step=None,
        words="measured",
        started_at=started,
        finished_at=finished,
    )


def last_started(session: Session, league_id: int) -> datetime | None:
    """The moment this league's most recent chain was asked for.

    Its first step's `run_after`, not `created_at`: that is the moment the
    caller named, it is the moment `dedupe_key` is keyed on, and it is the
    moment a rehearsal or a replayed schedule means. `created_at` is the
    database's own clock, which is a different day for anything that names
    its own.
    """
    return session.scalar(
        select(Job.run_after)
        .where(Job.league_id == league_id, Job.kind == jobs.INTAKE_INGEST)
        .order_by(Job.id.desc())
        .limit(1)
    )


class IntakeRefusedError(Exception):
    """An intake was asked for and not enqueued; the message says why."""


# ---------------------------------------------------------------------------
# enqueueing
# ---------------------------------------------------------------------------


def enqueue_intake(
    session: Session,
    league_id: int,
    *,
    at: datetime | None = None,
    user_id: int | None = None,
    force: bool = False,
) -> list[jobs.Enqueued]:
    """The whole chain for one league, in order, each waiting on the last.

    `user_id` is whoever asked for it, and is carried to the last step so the
    email goes to him. Refuses (`IntakeRefusedError`) when one is already
    under way, and when the last one started less than `AGAIN_AFTER` ago:
    the sweep alone is an hour and a half, and nothing about a league's own
    history changes fast enough for a second run in a day to say anything.

    `force` skips both checks and leans on `dedupe_key` instead -- one chain
    per league per UTC day -- which is what makes a timer that fires twice
    harmless. It is for the timer (`app.schedule`) and for an operator at a
    terminal, never for a page: a person who presses a button twice should
    be told why the second press did nothing.

    Does not commit: the caller does, which is what makes "one at a time"
    hold -- the check and the insert are in one transaction.
    """
    when = at or now()
    if not force:
        state = progress(session, league_id)
        if state.running:
            doing = STEP_WORDS.get(state.step or "", "in progress")
            raise IntakeRefusedError(f"this league is already being measured ({doing})")
        previous = last_started(session, league_id)
        if previous is not None and when - previous < AGAIN_AFTER:
            raise IntakeRefusedError(
                "this league was measured today; it can be measured again tomorrow"
            )

    out: list[jobs.Enqueued] = []
    waiting_on: int | None = None
    for index, kind in enumerate(STEPS):
        step = jobs.enqueue(
            session,
            kind,
            # A second apart, so a worker that takes jobs in order takes them
            # in this order even before `depends_on` is consulted.
            run_after=when + timedelta(seconds=index),
            label=LABEL,
            league_id=league_id,
            user_id=user_id if kind == jobs.INTAKE_DONE else None,
            depends_on=waiting_on,
            priority=jobs.LOW if kind == jobs.INTAKE_HURDLES else jobs.NORMAL,
            payload={"asked_by": user_id} if user_id is not None else None,
        )
        waiting_on = step.id
        out.append(step)
    log.info("league %s: intake chain enqueued (%s jobs)", league_id, len(out))
    return out


def league_of(session: Session, league_id: int) -> League | None:
    return session.get(League, league_id)


def espn_id_of(session: Session, league_id: int) -> int | None:
    league = session.get(League, league_id)
    return None if league is None else int(league.espn_league_id)


def steps_that_failed(session: Session, league_id: int) -> Sequence[tuple[str, str]]:
    return progress(session, league_id).failed
