#!/usr/bin/env python3
"""Replay a matchup period of a season already played, through the real queue.

    python scripts/rehearse_week.py --season 2026 --period 12 \
        --teams all --days 2 --workers 1,3 --out /tmp/rehearsal

The in-season loop -- the morning's `precompute` jobs, the worker that takes
them, the stored `team_reports` row, the digest rendered from it and the
pages served out of it -- has never run against a live season. This runs it
against a dead one: a morning in January 2026 replayed a day at a time, with
real jobs on the real `jobs` table, taken by real worker processes, so that
whatever falls over falls over here rather than on opening night.

WHAT IS REAL AND WHAT IS NOT

Real: `app.jobs` (enqueue, dedupe, claim under `FOR UPDATE SKIP LOCKED`,
backoff, parking, the reaper), `scripts/worker.py`'s loop shape,
`app.job_kinds.run_precompute`, `app.api.pickups.build_payload`,
`app.reports.store`, the digest's own builders, and the two report routes
through a FastAPI `TestClient`.

Not real, and named so in docs/inseason_rehearsal.md:

* **No ESPN and no VPS.** `ingest` and `status_pass` jobs are live-network by
  nature and are out of scope; nothing here enqueues one, and the rehearsal
  worker has no handler for either, so it could not run one if it found one.
* **Nothing is delivered.** No `digest` job is ever enqueued: the digests are
  rendered by calling the same builders `app.job_kinds` calls and written to
  files. On top of that `forbid_delivery` replaces `app.notify.deliver` and
  `app.channels.deliver` in this process with a refusal, so a code path that
  tried to send would raise rather than send. That is two locks, neither of
  them an environment variable, because the environment variables here are
  set.
* **The listener never ran for a played season**, so there are no status
  snapshots and no wire snapshots. The reports fall back to the
  reconstructed historical wire (docs/in_season_pages.md) and the digest's
  roster-news half has nothing to read. That is a property of the rehearsal,
  not a finding.

HOW A REHEARSAL JOB IS TOLD APART

Its payload carries `rehearsal: true` and the run's tag, and its dedupe
label is `rehearsal:<run>:<day>`. The rehearsal's workers narrow every claim
to those rows (`jobs.claim(only=...)`), so they cannot take, fail or reap a
real job; and a real worker running the real handlers would build the named
day rather than today, because the day rides in the payload
(`app.job_kinds.payload_day`) -- but it would still be obviously a
rehearsal row in `worker.py --list`.

That mark is also how a run clears up after itself: at the end it deletes
the `jobs` rows carrying `rehearsal: true` and its own tag, and nothing
else. `--keep` leaves them for inspection. The stored `team_reports` rows
stay either way; they are for days in the past and no route serves one as
fresh.

THE CHECKS

Each is reported pass/fail with the rows that offended, into `--out`:

1. no look-ahead      a report for day N recommends nobody who was rostered
                      that day, counts no day before N as remaining, has
                      posted only what was scored before N, has spent only
                      the adds made by N, and counts no transaction after N
2. determinism        one team, one day, built twice, same payload
3. digest size        4096 characters is Telegram's limit
4. served from store   the route answers from the stored row, and quickly
5. queue semantics    no job twice, no duplicate enqueue, retry then park,
                      a killed worker's job recovered
6. edges              first day, last day, a day with no NBA games, the
                      first playoff day

`--worker` is this file re-entered as a worker process; it is not for hands.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import channels, jobs, notify, reports
from app.config import get_settings
from app.db.models import (
    DailyLineupSlot,
    Job,
    League,
    LeagueSeason,
    MatchupPeriod,
    Player,
    PlayerGameStat,
    ProTeamGame,
    Team,
    TeamReport,
    Transaction,
    TransactionItem,
)
from app.db.session import make_engine, make_session_factory
from app.digest import TEXT_LIMIT
from app.job_kinds import handlers
from app.jobs import JobError, JobRef
from app.pickups.state import (
    EXECUTED,
    SeasonCalendar,
    load_team_week,
    period_for_day,
    season_calendar,
)
from app.scoring.lines import COUNTS
from app.scoring.replacement import ADD_TYPES

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The mark every rehearsal job carries, in its payload and its dedupe label.
REHEARSAL = "rehearsal"
#: A digest with fewer useful lines than this is saying nothing.
THIN_DIGEST_LINES = 6
#: How long a rehearsal worker waits on an empty queue before it exits.
IDLE_SECONDS = 4.0
#: The failure the retry check injects. A `JobError`, so `last_error` shows
#: the sentence rather than an exception's class, which is what a real
#: failure of ours looks like.
INJECTED_FAULT = "the rehearsal's injected fault"
#: How the killed-worker check moves the clock rather than waiting two hours.
PAST_LEASE = jobs.LEASE + timedelta(minutes=1)


# ---------------------------------------------------------------------------
# the two locks on delivery
# ---------------------------------------------------------------------------


def _refuse_to_deliver(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("the rehearsal is not allowed to deliver anything")


def forbid_delivery() -> None:
    """Make this process unable to send, whatever the environment says.

    The SMTP settings are set on this machine, so "nothing is configured" is
    not a guard. Both senders are replaced
    through `__dict__` because a module's function cannot be rebound in a
    type-checked assignment, and the point is that it is rebound.
    """
    notify.__dict__["deliver"] = _refuse_to_deliver
    channels.__dict__["deliver"] = _refuse_to_deliver


# ---------------------------------------------------------------------------
# the pure parts (tests/test_rehearse_week.py)
# ---------------------------------------------------------------------------


def rehearsal_label(run: str, day: int) -> str:
    """The schedule label a rehearsal job is enqueued under.

    It is the dedupe key's last part, so two runs never collide and the same
    run enqueueing one day twice still adds nothing -- which is check 5.
    """
    return f"{REHEARSAL}:{run}:{day:03d}"


def rehearsal_payload(run: str, day: int, on: date, *, fault: bool = False) -> dict[str, Any]:
    """What a rehearsal job carries: the mark, the run, and the day it means."""
    body: dict[str, Any] = {
        REHEARSAL: True,
        "run": run,
        "scoring_period": day,
        "today": on.isoformat(),
    }
    if fault:
        body["fault"] = True
    return body


def only_this_run(run: str) -> ColumnElement[bool]:
    """The narrowing a rehearsal worker claims under: this run's rows alone."""
    marked: ColumnElement[bool] = Job.payload[REHEARSAL].astext == "true"
    mine: ColumnElement[bool] = Job.payload["run"].astext == run
    return and_(marked, mine)


def clean_up(factory: sessionmaker[Session], run: str) -> int:
    """Delete this run's own rows from `jobs`, and say how many. See `--keep`.

    A rehearsal used to leave its queue behind -- 44 rows after the first
    pass, one of them deliberately parked -- so the next person to look at
    `jobs` found a morning that never happened. The narrowing is the same one
    a rehearsal worker claims under (`only_this_run`): a row carrying
    `rehearsal: true` AND this run's tag, so no real job can be caught by it
    and no other run's rows are touched either.

    `team_reports` rows are left alone deliberately. They are ordinary stored
    reports for days in the past, no route will serve one as fresh (the
    freshness rule is today's date for today's period), and the next run of
    the same day overwrites them.
    """
    with factory() as session:
        result = session.execute(delete(Job).where(only_this_run(run)))
        deleted = int(getattr(result, "rowcount", 0) or 0)
        session.commit()
    return deleted


def chosen_days(
    first: int, final: int, only: Sequence[int] | None = None, limit: int | None = None
) -> list[int]:
    """Which scoring periods to replay, in order.

    The period's own days by default; `only` names them instead (the edge
    days live outside whichever period is being replayed in full), and
    `limit` takes the first few, which is what `--days` is for.
    """
    if only:
        days = sorted(dict.fromkeys(int(day) for day in only))
    else:
        days = list(range(first, final + 1))
    return days[:limit] if limit else days


def worker_counts(days: Sequence[int], spec: str) -> list[int]:
    """How many workers each day's morning gets.

    A single number is every day's. A comma list is cycled, which is how one
    run times a full league morning at one worker and then at three without
    building the same reports twice.
    """
    counts = [int(part) for part in spec.split(",") if part.strip()]
    if not counts or any(count < 1 for count in counts):
        raise ValueError(f"--workers must be one or more positive numbers, not {spec!r}")
    return [counts[index % len(counts)] for index in range(len(days))]


def summarise(seconds: Sequence[float]) -> dict[str, float]:
    """Median, ninetieth percentile and worst, for a timing table."""
    if not seconds:
        return {"n": 0, "median": 0.0, "p90": 0.0, "max": 0.0}
    ordered = sorted(seconds)
    rank = max(0, min(len(ordered) - 1, round(0.9 * (len(ordered) - 1))))
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 2),
        "p90": round(ordered[rank], 2),
        "max": round(ordered[-1], 2),
    }


def _players_of(entries: Iterable[Any]) -> list[int]:
    return [int(entry["espn_player_id"]) for entry in entries if entry]


def added_player_ids(kind: str, payload: dict[str, Any]) -> set[int]:
    """Everyone a report proposes bringing in, by ESPN player id.

    The week report's moves and its empty-day fillers; the season report's
    free add, swap and two-for-two, each of which names who comes in.
    """
    found: list[int] = []
    if kind == reports.STREAM:
        for move in [*payload.get("moves", []), *payload.get("recommended", [])]:
            if move.get("add"):
                found.append(int(move["add"]["espn_player_id"]))
        for day in payload.get("empty_days", []):
            found.extend(_players_of(day.get("fillers", [])))
    else:
        for name in ("best_add", "best_swap", "best_two_swap", "recommended"):
            swap = payload.get(name)
            if swap:
                found.extend(_players_of(swap.get("into", [])))
    return set(found)


def look_ahead_findings(
    kind: str,
    payload: dict[str, Any],
    day: int,
    rostered: set[int],
    adds_so_far: int | None = None,
) -> list[str]:
    """What in this payload knows something the morning of `day` could not.

    Three things are checkable: a day already played counted as still to
    come; a man proposed as an add whom somebody had in his lineup that very
    day; and the adds the report says this team has spent of the period's
    budget, which on the morning of day N can only be the ones made on days
    up to and including N. `rostered` is that day's `daily_lineup_slots` as
    ESPN player ids, and `adds_so_far` the adds actually made by then.
    """
    out: list[str] = []
    used = payload.get("adds_used")
    if adds_so_far is not None and used is not None and int(used) != adds_so_far:
        out.append(
            f"adds_used is {used}, but only {adds_so_far} add(s) had been made by day {day}"
            f" (adds_left {payload.get('adds_left')} of {payload.get('adds_budget')})"
        )
    if kind == reports.STREAM:
        remaining = [int(value) for value in payload.get("scoring_periods_remaining", [])]
        behind = [value for value in remaining if value < day]
        if behind:
            out.append(f"scoring_periods_remaining counts {behind}, already played on day {day}")
        for empty in payload.get("empty_days", []):
            if int(empty["scoring_period"]) < day:
                out.append(f"empty_days names day {empty['scoring_period']}, before day {day}")
    else:
        today = payload.get("today")
        if today is not None and int(today) != day:
            out.append(f"the season report says today is {today}, not {day}")
    held = sorted(added_player_ids(kind, payload) & rostered)
    if held:
        out.append(f"proposes adding {held}, who were in a lineup on day {day}")
    return out


def digest_findings(name: str, text: str, *, games: bool) -> list[str]:
    """What is wrong with a rendered digest, if anything.

    The length cap is the hard one. The soft one is a message that renders
    and says nothing: on a day the league played, a digest whose week section
    could not be built, or which is barely any lines at all, is a digest the
    reader learns nothing from.
    """
    out: list[str] = []
    if len(text) > TEXT_LIMIT:
        out.append(f"{name}: {len(text)} characters, over the {TEXT_LIMIT} the text part allows")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        out.append(f"{name}: renders empty")
    elif games and len(lines) < THIN_DIGEST_LINES:
        out.append(f"{name}: only {len(lines)} lines on a day with games")
    if games and "no plan today" in text:
        out.append(f"{name}: the week section could not be built on a day with games")
    return out


# ---------------------------------------------------------------------------
# what the database says about the season
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """One team the rehearsal replays, by both sets of ids."""

    team_pk: int
    espn_team_id: int
    name: str


@dataclass
class Morning:
    """One replayed day: what each team's job did, and how long it all took."""

    day: int
    on: str
    matchup_period: int | None
    workers: int
    games: int
    wall_seconds: float
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    digests: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    page_findings: list[str] = field(default_factory=list)
    look_ahead: list[str] = field(default_factory=list)


def league_season_of(session: Session, season: int) -> LeagueSeason:
    row = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
    if row is None:
        raise SystemExit(f"no stored season {season}")
    return row


def period_of(session: Session, league_season: LeagueSeason, period: int) -> MatchupPeriod:
    row = session.scalar(
        select(MatchupPeriod).where(
            MatchupPeriod.league_season_id == league_season.id, MatchupPeriod.period == period
        )
    )
    if row is None:
        raise SystemExit(f"season {league_season.season} has no matchup period {period}")
    return row


def subjects(session: Session, league_season: LeagueSeason, wanted: str) -> list[Subject]:
    rows = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id).order_by(Team.espn_team_id)
    ).all()
    found = [Subject(int(t.id), int(t.espn_team_id), str(t.name)) for t in rows]
    if wanted == "all":
        return found
    asked = {int(part) for part in wanted.split(",") if part.strip()}
    picked = [team for team in found if team.team_pk in asked]
    if len(picked) != len(asked):
        held = [team.team_pk for team in found]
        raise SystemExit(f"--teams named {sorted(asked)}; the season holds {held}")
    return picked


def calendar_of(session: Session, season: int) -> SeasonCalendar:
    calendar = season_calendar(session, season)
    if calendar is None:
        raise SystemExit(f"no NBA schedule stored for {season}")
    return calendar


def games_on(session: Session, season: int, day: int) -> int:
    """How many NBA games were played on this scoring period."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(ProTeamGame)
            .where(ProTeamGame.season == season, ProTeamGame.scoring_period == day)
        )
        or 0
    )


def rostered_espn_ids(session: Session, league_season: LeagueSeason, day: int) -> set[int]:
    """Everyone in somebody's lineup on `day`, by ESPN player id."""
    return {
        int(espn_id)
        for espn_id in session.scalars(
            select(Player.espn_player_id)
            .join(DailyLineupSlot, DailyLineupSlot.player_id == Player.id)
            .join(Team, Team.id == DailyLineupSlot.team_id)
            .where(
                Team.league_season_id == league_season.id,
                DailyLineupSlot.scoring_period == day,
            )
            .distinct()
        ).all()
        if espn_id is not None
    }


def first_day_of_period(session: Session, league_season_pk: int, day: int) -> int | None:
    """The first scoring period of the matchup period `day` falls in."""
    found = session.scalar(
        select(MatchupPeriod.first_scoring_period).where(
            MatchupPeriod.league_season_id == league_season_pk,
            MatchupPeriod.first_scoring_period <= day,
            MatchupPeriod.final_scoring_period >= day,
        )
    )
    return int(found) if found is not None else None


def adds_through(
    session: Session, league_season_pk: int, team_pk: int, first: int, day: int
) -> int:
    """Adds this team had actually made by the end of `day`, this period.

    `app.pickups.state._adds_in_period`'s query with the window ending today
    rather than at the period's last day, which is the difference check 1 is
    looking for.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
            .where(
                Transaction.league_season_id == league_season_pk,
                Transaction.type.in_(ADD_TYPES),
                Transaction.status == EXECUTED,
                Transaction.scoring_period >= first,
                Transaction.scoring_period <= day,
                TransactionItem.item_type == "ADD",
                TransactionItem.to_team_id == team_pk,
            )
        )
        or 0
    )


def posted_through(
    session: Session, league_season: LeagueSeason, team_pk: int, period_id: int, day: int
) -> dict[str, float]:
    """What this team had really posted this period by the morning of `day`.

    `app.pickups.state._posted`'s sum written out here rather than called,
    because a check that asks the function it is checking proves nothing. The
    started lines on the period's days **before** `day`: the morning's report
    is built before that day's games, and `scoring_periods_remaining` begins
    there, so day `day` belongs to the projection and not to the score.
    """
    columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    rows = session.execute(
        select(*columns)
        .join(
            DailyLineupSlot,
            (DailyLineupSlot.player_id == PlayerGameStat.player_id)
            & (DailyLineupSlot.scoring_period == PlayerGameStat.scoring_period),
        )
        .where(
            DailyLineupSlot.team_id == team_pk,
            DailyLineupSlot.matchup_period_id == period_id,
            DailyLineupSlot.started.is_(True),
            PlayerGameStat.season == int(league_season.season),
            PlayerGameStat.scoring_period < day,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes > 0,
        )
    ).all()
    totals = dict.fromkeys(COUNTS, 0.0)
    for row in rows:
        for abbreviation, value in zip(COUNTS, row, strict=True):
            totals[abbreviation] += float(value or 0.0)
    return totals


def posted_findings(
    session: Session, league_season: LeagueSeason, team: Subject, period_id: int, day: int
) -> list[str]:
    """Whether the week the report is built on stops at this morning.

    `matchup_team_stats` carries a finished period's final total with no day
    on it, so a report for a replayed day used to open with the whole week
    already banked and project seven more days on top. Checked against the
    independent sum above rather than against the stored payload, because the
    payload carries only `projected`, which is this plus the projection.
    """
    week = load_team_week(session, league_season, team.espn_team_id, day)
    truth = posted_through(session, league_season, team.team_pk, period_id, day)
    wrong = sorted(
        abbreviation
        for abbreviation, value in truth.items()
        if abs(week.my_totals.get(abbreviation) - value) > 1e-6
    )
    if not wrong:
        return []
    return [
        f"the week's posted totals are wrong in {len(wrong)} categories"
        f" ({', '.join(wrong)}): PTS reads {week.my_totals.get('PTS'):.0f},"
        f" but {truth['PTS']:.0f} had been scored by the morning of day {day}"
    ]


def trailing_window_findings(
    session: Session, league_season: LeagueSeason, team_pk: int, at: datetime
) -> list[str]:
    """Whether the digest's two trailing counts stop at `at`.

    `app.digest.adds_in_window` and `league_section`'s wire tally used to ask
    for rows newer than `now - window` and never for rows older than `now`.
    On the live season that reads right, because there is nothing after now;
    on a replay it reads to the end of the year.

    Both halves ask the product for its answer and compare it against the
    same window closed at both ends, computed here. The wire tally has no
    function of its own to call, so the number is read back off the line
    `league_section` renders -- which is the line the member actually sees.
    """
    from app.digest import CHURN_DAYS, LEAGUE_MOVES_HOURS, adds_in_window, league_section
    from app.scoring.wire import WIRE_TYPES

    out: list[str] = []
    open_ended = adds_in_window(session, league_season, team_pk, now=at, days=CHURN_DAYS)
    closed = int(
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
            .where(
                Transaction.league_season_id == league_season.id,
                Transaction.team_id == team_pk,
                Transaction.type.in_(WIRE_TYPES),
                Transaction.status == EXECUTED,
                Transaction.processed_at.between(at - timedelta(days=CHURN_DAYS), at),
                TransactionItem.item_type == "ADD",
            )
        )
        or 0
    )
    if open_ended != closed:
        out.append(
            f"the digest's churn line counts {open_ended} adds in the last {CHURN_DAYS} days;"
            f" only {closed} were made before {at:%Y-%m-%d %H:%M}"
        )
    since = at - timedelta(hours=LEAGUE_MOVES_HOURS)
    behind = int(
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.league_season_id == league_season.id,
                Transaction.type.in_(WIRE_TYPES),
                Transaction.status == EXECUTED,
                Transaction.processed_at.between(since, at),
            )
        )
        or 0
    )
    shown = wire_moves_line(league_section(session, league_season, now=at))
    if shown != behind:
        out.append(
            f"the league section counts {shown} wire moves in the last day; only {behind}"
            f" were made before {at:%Y-%m-%d %H:%M}"
        )
    return out


def wire_moves_line(lines: Sequence[str]) -> int | None:
    """The number off `league_section`'s last line, or None if it is not there."""
    for line in reversed(lines):
        found = re.search(r"(\d+) moves? on the wire in the last day", line)
        if found is not None:
            return int(found.group(1))
    return None


def quiet_day(session: Session, season: int, first: int, final: int) -> int | None:
    """A day of this period with no NBA game at all, if it has one."""
    for day in range(first, final + 1):
        if not games_on(session, season, day):
            return day
    return None


# ---------------------------------------------------------------------------
# the worker process
# ---------------------------------------------------------------------------


def rehearsal_handlers() -> dict[str, jobs.Handler]:
    """`precompute`, and nothing else.

    The real handler, reached through the real registry, so the day comes out
    of the payload exactly as it would in production. A job whose payload
    asks for the fault raises before it: that is check 5's retry case, and it
    is injected rather than borrowed from a real kind so no real failure is
    ever provoked.
    """
    real = handlers()

    def precompute(factory: sessionmaker[Session], job: JobRef) -> str | None:
        if job.payload.get("fault"):
            raise JobError(INJECTED_FAULT)
        return real[jobs.PRECOMPUTE](factory, job)

    return {jobs.PRECOMPUTE: precompute}


def work(run: str, log_path: Path, idle_seconds: float = IDLE_SECONDS) -> int:
    """`scripts/worker.py`'s loop, narrowed to this rehearsal's own jobs.

    It writes one JSON line per job it finished, so the parent can prove
    afterwards that no job was taken by two workers.
    """
    forbid_delivery()
    factory = make_session_factory(make_engine(get_settings().database_url))
    kinds = rehearsal_handlers()
    worker = jobs.worker_name()
    only = only_this_run(run)
    idle_since = time.monotonic()
    with log_path.open("a", encoding="utf-8") as log:
        while True:
            outcome = jobs.run_next(factory, kinds, worker=worker, only=only)
            if outcome is None:
                if time.monotonic() - idle_since > idle_seconds:
                    return 0
                time.sleep(0.25)
                continue
            idle_since = time.monotonic()
            log.write(
                json.dumps(
                    {
                        "job": outcome.job.id,
                        "kind": outcome.job.kind,
                        "team": outcome.job.team_id,
                        "state": outcome.state,
                        "message": outcome.message,
                        "worker": worker,
                    }
                )
                + "\n"
            )
            log.flush()


def spawn_workers(run: str, count: int, log_path: Path) -> list[subprocess.Popen[bytes]]:
    return [
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--run",
                run,
                "--log",
                str(log_path),
            ],
            cwd=str(REPO_ROOT),
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(count)
    ]


# ---------------------------------------------------------------------------
# one morning
# ---------------------------------------------------------------------------


def one_job(
    session: Session,
    league_pk: int,
    team: Subject,
    label: str,
    day: int,
    on: date,
    *,
    fault: bool = False,
    run: str = "",
) -> jobs.Enqueued:
    """One rehearsal precompute, through the queue's own enqueue."""
    return jobs.enqueue(
        session,
        jobs.PRECOMPUTE,
        run_after=jobs.now(),
        label=label,
        league_id=league_pk,
        team_id=team.team_pk,
        payload=rehearsal_payload(run, day, on, fault=fault),
    )


def enqueue_morning(
    session: Session,
    league_pk: int,
    teams: Sequence[Subject],
    run: str,
    day: int,
    on: date,
) -> list[jobs.Enqueued]:
    """This morning's precompute per team, as `app.schedule` enqueues them
    (minus the pass they would depend on, which reaches ESPN)."""
    label = rehearsal_label(run, day)
    added = [one_job(session, league_pk, team, label, day, on, run=run) for team in teams]
    session.commit()
    return added


def morning_outcomes(
    session: Session, run: str, day: int, teams: Sequence[Subject]
) -> list[dict[str, Any]]:
    by_pk = {team.team_pk: team for team in teams}
    rows = session.scalars(
        select(Job).where(Job.dedupe_key.like(f"%|{rehearsal_label(run, day)}")).order_by(Job.id)
    ).all()
    out: list[dict[str, Any]] = []
    for job in rows:
        team = by_pk.get(int(job.team_id or 0))
        seconds = (
            (job.finished_at - job.started_at).total_seconds()
            if job.finished_at and job.started_at
            else None
        )
        out.append(
            {
                "job": int(job.id),
                "team": int(job.team_id or 0),
                "name": team.name if team else "?",
                "state": str(job.state),
                "attempts": int(job.attempts),
                "note": job.last_error,
                "seconds": round(seconds, 2) if seconds is not None else None,
            }
        )
    return out


def stored_payloads(
    session: Session, team: Subject, day: int
) -> dict[str, tuple[dict[str, Any], datetime]]:
    """What the precompute left in `team_reports` for this team and day.

    Read straight from the table rather than through `reports.fresh`, which
    asks whether a row is fresh *today* -- the question the route asks, and
    the one check 4 is about. Here we only want to know what was written.
    """
    rows = session.scalars(
        select(TeamReport).where(
            TeamReport.team_id == team.team_pk, TeamReport.scoring_period == day
        )
    ).all()
    return {str(row.kind): (dict(row.payload), row.built_at) for row in rows}


# ---------------------------------------------------------------------------
# the digests, rendered and never sent
# ---------------------------------------------------------------------------


def render_digests(
    session: Session,
    league_season: LeagueSeason,
    owner: Subject,
    member: Subject,
    at: datetime,
) -> list[tuple[str, str, float]]:
    """The owner's morning digest and an ordinary member's, for `at`.

    The same two compositions `app.job_kinds._owner_digest` and
    `_member_digest` make, built by calling the same functions -- but no
    `digest` job is enqueued and `_deliver` is never entered, which is what
    makes the rehearsal structurally unable to send. The member's reads the
    events since his own last digest (a day), as his job would.
    """
    from app.digest import build_digest, league_section

    out: list[tuple[str, str, float]] = []
    started = time.monotonic()
    digest = build_digest(session, league_season, owner.espn_team_id, now=at)
    text = digest.render() + "\n\n" + "\n".join(league_section(session, league_season, now=at))
    out.append(("owner", text, round(time.monotonic() - started, 2)))

    started = time.monotonic()
    his = build_digest(
        session, league_season, member.espn_team_id, now=at, since=at - timedelta(hours=24)
    )
    text = his.render() + "\n\n" + "\n".join(league_section(session, league_season, now=at))
    out.append(("member", text, round(time.monotonic() - started, 2)))
    return out


# ---------------------------------------------------------------------------
# the pages, served from the store
# ---------------------------------------------------------------------------


def page_timings(espn_league_id: int, season: int, team: Subject, on: date) -> list[dict[str, Any]]:
    """Fetch both reports and the glance through the API, as the pages do.

    `app.api.pickups.TODAY` is moved to the rehearsal's day: without it a
    row built now could never be the fresh one for a morning in January, and
    the route would rebuild every time, which is the thing being tested.
    """
    from fastapi.testclient import TestClient

    from app.api import pickups
    from app.main import create_app

    was = pickups.TODAY
    pickups.__dict__["TODAY"] = lambda: on
    try:
        base = f"/leagues/{espn_league_id}/seasons/{season}/teams/{team.espn_team_id}/pickups"
        out: list[dict[str, Any]] = []
        with TestClient(create_app()) as client:
            for route in ("stream", "season", "glance"):
                started = time.monotonic()
                answer = client.get(f"{base}/{route}")
                seconds = round(time.monotonic() - started, 3)
                body = answer.json() if answer.status_code == 200 else None
                out.append(
                    {
                        "route": route,
                        "status": answer.status_code,
                        "seconds": seconds,
                        "stored_flag": body.get("stored") if body and route == "glance" else None,
                        "body": body,
                    }
                )
        return out
    finally:
        pickups.__dict__["TODAY"] = was


def page_findings(
    pages: list[dict[str, Any]], stored: dict[str, tuple[dict[str, Any], datetime]]
) -> list[str]:
    """Whether each page was answered out of the stored row."""
    out: list[str] = []
    for page in pages:
        if page["status"] != 200:
            out.append(f"{page['route']}: HTTP {page['status']}")
            continue
        if page["route"] == "glance":
            if page["stored_flag"] is not True:
                out.append("glance: answered live, not from the stored row")
            continue
        kept = stored.get(page["route"])
        if kept is None:
            out.append(f"{page['route']}: nothing was stored for this day")
        elif page["body"] != kept[0]:
            out.append(f"{page['route']}: the answer differs from the stored payload")
    return out


# ---------------------------------------------------------------------------
# check 5: the queue under concurrency
# ---------------------------------------------------------------------------


def no_job_ran_twice(log_path: Path) -> tuple[bool, list[str]]:
    """Every finished job appears in exactly one worker's log."""
    seen: dict[int, list[str]] = {}
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            seen.setdefault(int(row["job"]), []).append(str(row["worker"]))
    twice = [f"job {job} run by {workers}" for job, workers in seen.items() if len(workers) > 1]
    return not twice, twice


def dedupe_check(
    session: Session, league_pk: int, teams: Sequence[Subject], run: str, day: int, on: date
) -> tuple[bool, list[str]]:
    """Enqueueing the same morning again adds no row."""
    before = len(session.scalars(select(Job.id)).all())
    again = enqueue_morning(session, league_pk, teams, run, day, on)
    after = len(session.scalars(select(Job.id)).all())
    created = [job.id for job in again if job.created]
    if created or after != before:
        return False, [f"{len(created)} new job(s), table grew by {after - before}"]
    return True, []


def retry_then_park(
    factory: sessionmaker[Session], league_pk: int, team: Subject, run: str, day: int, on: date
) -> tuple[bool, list[str]]:
    """A job that raises is tried three times and then parked, error visible.

    The clock is moved rather than waited out: `BACKOFF` is five minutes and
    then twenty, and `run_next(at=...)` is the seam the queue already has for
    saying when "now" is. Everything else -- the attempt count, the backoff,
    the parking, the sentence in `last_error` -- is the real code.
    """
    label = f"{rehearsal_label(run, day)}-fault"
    with factory() as session:
        added = one_job(session, league_pk, team, label, day, on, fault=True, run=run)
        session.commit()
        job_id = added.id
    only = Job.id == job_id
    kinds = rehearsal_handlers()
    at = jobs.now()
    states: list[str] = []
    for step in range(jobs.MAX_ATTEMPTS):
        outcome = jobs.run_next(factory, kinds, worker=f"fault-{step}", at=at, only=only)
        if outcome is None:
            return False, [f"attempt {step + 1} claimed nothing"]
        states.append(outcome.state)
        at = at + timedelta(minutes=30)
    with factory() as session:
        row = session.get(Job, job_id)
        final = "" if row is None else f"{row.state}/{row.attempts}/{row.last_error}"
    wanted = [jobs.QUEUED] * (jobs.MAX_ATTEMPTS - 1) + [jobs.FAILED]
    if states != wanted:
        return False, [f"states were {states}, wanted {wanted}", final]
    if final != f"{jobs.FAILED}/{jobs.MAX_ATTEMPTS}/{INJECTED_FAULT}":
        return False, [f"parked as {final}"]
    return True, [final]


def killed_worker_recovered(
    factory: sessionmaker[Session],
    league_pk: int,
    team: Subject,
    run: str,
    day: int,
    on: date,
    log_path: Path,
) -> tuple[bool, list[str]]:
    """A worker killed mid-job leaves a row the reaper gives back.

    A real SIGKILL, so nothing unwinds and the row stays `running` with the
    dead worker's name on it; then `jobs.reap` with the clock past `LEASE`,
    which is the same call the next `claim` makes.
    """
    label = f"{rehearsal_label(run, day)}-killed"
    with factory() as session:
        added = one_job(session, league_pk, team, label, day, on, run=run)
        session.commit()
        job_id = added.id
    victim = spawn_workers(run, 1, log_path)[0]
    claimed = False
    for _ in range(120):
        time.sleep(0.5)
        with factory() as session:
            row = session.get(Job, job_id)
            claimed = row is not None and row.state == jobs.RUNNING
        if claimed:
            break
    if not claimed:
        victim.kill()
        return False, ["the worker never claimed the job"]
    victim.kill()
    victim.wait(timeout=30)
    notes: list[str] = []
    with factory() as session:
        row = session.get(Job, job_id)
        notes.append(f"after the kill: {row.state if row else '?'}")
        if row is None or row.state != jobs.RUNNING:
            return False, notes
        reaped = jobs.reap(session, jobs.now() + PAST_LEASE, only=only_this_run(run))
        session.commit()
        row = session.get(Job, job_id)
        notes.append(
            f"reaped {reaped}, now {row.state if row else '?'}: {row.last_error if row else ''}"
        )
        if row is None or row.state != jobs.QUEUED:
            return False, notes
        row.run_after = jobs.now()
        session.commit()
    for worker in spawn_workers(run, 1, log_path):
        worker.wait(timeout=600)
    with factory() as session:
        row = session.get(Job, job_id)
        notes.append(f"rerun: {row.state if row else '?'} after {row.attempts if row else 0} tries")
        return (row is not None and row.state == jobs.DONE), notes


# ---------------------------------------------------------------------------
# the profile
# ---------------------------------------------------------------------------


#: Functions whose cost is the league's, not the team's: the same answer for
#: every team in the league on the same morning. Read out of the profile by
#: cumulative time, to say how much of a precompute they are.
LEAGUE_WIDE = (
    ("bids.py", "bid_fit"),
    ("targets.py", "category_distributions"),
    ("era.py", "category_trends"),
    ("replacement.py", "replacement_value"),
    ("state.py", "historical_free_agents"),
    ("state.py", "load_free_agents"),
)


def cumulative(stats: pstats.Stats, where: str, name: str) -> float:
    """That function's cumulative seconds in this profile, or zero."""
    for (path, _, function), row in stats.stats.items():  # type: ignore[attr-defined]
        if function == name and str(path).endswith(where):
            return round(float(row[3]), 2)
    return 0.0


def profile_one(team_pk: int, second_pk: int, day: int, on: date, out_dir: Path) -> int:
    """A cold precompute profiled, then a warm one, in one fresh process.

    `--profile-one` re-enters this file in an interpreter that has built
    nothing, because cold is the case worth profiling: the league's FAAB fit
    is cached for the life of a process (`app.pickups.bids._CACHE`), so a
    precompute run at the end of a replay is the cheap one. The second
    build, of another team in the same process, is what every team after the
    first costs on a real morning -- the worker is long-running.
    """
    from app.job_kinds import run_precompute

    factory = make_session_factory(make_engine(get_settings().database_url))

    def build(pk: int, name: str) -> tuple[float, pstats.Stats]:
        job = JobRef(0, jobs.PRECOMPUTE, None, pk, None, 0, {})
        profiler = cProfile.Profile()
        started = time.monotonic()
        profiler.enable()
        run_precompute(factory, job, today=on)
        profiler.disable()
        seconds = round(time.monotonic() - started, 2)
        report = out_dir / f"profile-{name}.txt"
        with report.open("w", encoding="utf-8") as handle:
            handle.write(f"{name} precompute, team {pk}, day {day}: {seconds}s\n\n")
            pstats.Stats(profiler, stream=handle).sort_stats("cumulative").print_stats(25)
        return seconds, pstats.Stats(profiler)

    out_dir.mkdir(parents=True, exist_ok=True)
    cold, cold_stats = build(team_pk, "cold")
    warm, _ = build(second_pk, "warm")
    answer = {
        "cold_seconds": cold,
        "warm_seconds": warm,
        "league_wide_seconds": {
            name: cumulative(cold_stats, where, name) for where, name in LEAGUE_WIDE
        },
    }
    (out_dir / "profile.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
    print(json.dumps(answer))
    return 0


def profile_precompute(first: Subject, second: Subject, day: int, on: date, out_dir: Path) -> Any:
    """Profile a precompute in a fresh interpreter of its own, cold then warm."""
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--profile-one",
            "--teams",
            f"{first.team_pk},{second.team_pk}",
            "--only-days",
            str(day),
            "--out",
            str(out_dir),
            "--run",
            on.isoformat(),
        ],
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        check=True,
    )
    written = out_dir / "profile.json"
    return json.loads(written.read_text(encoding="utf-8")) if written.exists() else "not written"


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Season:
    """The handful of keys every later session re-reads its rows by.

    Nothing ORM crosses a session boundary here: a `LeagueSeason` read in the
    morning's session and used in the digest's would be detached, and its
    `teams` would raise rather than load. So the replay carries ids.
    """

    season: int
    league_pk: int
    espn_league_id: int
    league_season_pk: int


def determinism(
    factory: sessionmaker[Session],
    league_pk: int,
    team: Subject,
    run: str,
    day: int,
    on: date,
    log_path: Path,
) -> tuple[bool, list[str]]:
    """The same team, the same day, built twice: the payload must not move.

    The second build goes through its own job and its own worker process, so
    it shares no session, no projection cache and no interpreter with the
    first. Only `built_at` may differ, and it must, or nothing was rebuilt.
    """
    with factory() as session:
        first = stored_payloads(session, team, day)
    with factory() as session:
        one_job(session, league_pk, team, f"{rehearsal_label(run, day)}-again", day, on, run=run)
        session.commit()
    for worker in spawn_workers(run, 1, log_path):
        worker.wait(timeout=1800)
    with factory() as session:
        second = stored_payloads(session, team, day)
    rows: list[str] = []
    for kind in reports.KINDS:
        if kind not in first or kind not in second:
            rows.append(f"{kind}: missing from one of the two builds")
        elif first[kind][0] != second[kind][0]:
            rows.append(f"{kind}: the payload changed between builds")
        elif first[kind][1] == second[kind][1]:
            rows.append(f"{kind}: built_at did not move, so it was not rebuilt")
    return not rows, rows


@dataclass(frozen=True)
class Window:
    """A matchup period's numbers, kept apart from the row they came from."""

    period: int
    first: int
    final: int
    is_playoff: bool


def read_the_season(
    factory: sessionmaker[Session], args: argparse.Namespace
) -> tuple[Season, Window, list[Subject], SeasonCalendar, int | None]:
    """Everything the replay needs to know before it enqueues anything."""
    with factory() as session:
        league_season = league_season_of(session, args.season)
        league = session.get(League, int(league_season.league_id))
        if league is None:  # pragma: no cover - the foreign key guarantees it
            raise SystemExit(f"season {args.season} has no league row")
        keys = Season(
            season=int(league_season.season),
            league_pk=int(league.id),
            espn_league_id=int(league.espn_league_id),
            league_season_pk=int(league_season.id),
        )
        row = period_of(session, league_season, args.period)
        if row.first_scoring_period is None or row.final_scoring_period is None:
            raise SystemExit(f"matchup period {args.period} has no window of scoring periods")
        period = Window(
            period=int(row.period),
            first=int(row.first_scoring_period),
            final=int(row.final_scoring_period),
            is_playoff=bool(row.is_playoff),
        )
        teams = subjects(session, league_season, args.teams)
        calendar = calendar_of(session, args.season)
        quiet = quiet_day(session, args.season, period.first, period.final)
    return keys, period, teams, calendar, quiet


def replay_one_day(
    factory: sessionmaker[Session],
    keys: Season,
    teams: Sequence[Subject],
    owner: Subject,
    member: Subject,
    run: str,
    day: int,
    on: date,
    workers: int,
    out_dir: Path,
    log_path: Path,
) -> Morning:
    """One morning: enqueue, let real workers take it, then read what it left."""
    with factory() as session:
        enqueue_morning(session, keys.league_pk, teams, run, day, on)
        games = games_on(session, keys.season, day)
        held = rostered_espn_ids(session, league_season_of(session, keys.season), day)
        in_period = session.scalar(
            select(MatchupPeriod.period).where(
                MatchupPeriod.league_season_id == keys.league_season_pk,
                MatchupPeriod.first_scoring_period <= day,
                MatchupPeriod.final_scoring_period >= day,
            )
        )
    started = time.monotonic()
    for worker in spawn_workers(run, workers, log_path):
        worker.wait(timeout=7200)
    morning = Morning(
        day=day,
        on=on.isoformat(),
        matchup_period=int(in_period) if in_period is not None else None,
        workers=workers,
        games=games,
        wall_seconds=round(time.monotonic() - started, 2),
    )

    with factory() as session:
        morning.outcomes = morning_outcomes(session, run, day, teams)
        league_season = league_season_of(session, keys.season)
        first = first_day_of_period(session, keys.league_season_pk, day)
        period_row = period_for_day(session, league_season, day)
        for team in teams:
            so_far = (
                adds_through(session, keys.league_season_pk, team.team_pk, first, day)
                if first is not None
                else None
            )
            if period_row is not None:
                morning.look_ahead += [
                    f"day {day} {team.name}: {line}"
                    for line in posted_findings(
                        session, league_season, team, int(period_row.id), day
                    )
                ]
            for kind, (payload, _) in stored_payloads(session, team, day).items():
                morning.look_ahead += [
                    f"day {day} {team.name} {kind}: {line}"
                    for line in look_ahead_findings(kind, payload, day, held, so_far)
                ]
        at = datetime.combine(on, datetime.min.time(), tzinfo=UTC) + timedelta(hours=15)
        morning.look_ahead += [
            f"day {day} the digest: {line}"
            for line in trailing_window_findings(session, league_season, owner.team_pk, at)
        ]
        for who, text, seconds in render_digests(session, league_season, owner, member, at):
            path = out_dir / f"day-{day:03d}-digest-{who}.txt"
            path.write_text(text, encoding="utf-8")
            morning.digests.append(
                {
                    "who": who,
                    "characters": len(text),
                    "lines": len(text.splitlines()),
                    "seconds": seconds,
                    "file": path.name,
                    "findings": digest_findings(who, text, games=bool(games)),
                }
            )
        kept = stored_payloads(session, owner, day)

    pages = page_timings(keys.espn_league_id, keys.season, owner, on)
    morning.page_findings = page_findings(pages, kept)
    morning.pages = [{key: value for key, value in page.items() if key != "body"} for page in pages]
    return morning


def replay(args: argparse.Namespace) -> int:
    forbid_delivery()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "workers.jsonl"
    factory = make_session_factory(make_engine(get_settings().database_url))
    run = args.run or f"{args.season}p{args.period}"

    keys, period, teams, calendar, quiet = read_the_season(factory, args)
    only = [int(part) for part in args.only_days.split(",")] if args.only_days else None
    days = chosen_days(period.first, period.final, only, args.days)
    counts = worker_counts(days, args.workers)
    owner = next((team for team in teams if team.espn_team_id == args.owner), teams[0])
    member = next((team for team in teams if team.team_pk != owner.team_pk), owner)
    print(
        f"rehearsal {run}: season {keys.season}, period {args.period}"
        f"{' (playoff)' if period.is_playoff else ''}, days {days}, "
        f"{len(teams)} team(s), workers {counts}"
    )
    if quiet is not None:
        print(f"  period {args.period} has a day with no NBA games: {quiet}")

    mornings: list[Morning] = []
    for index, day in enumerate(days):
        morning = replay_one_day(
            factory,
            keys,
            teams,
            owner,
            member,
            run,
            day,
            calendar.date_of(day),
            counts[index],
            out_dir,
            log_path,
        )
        mornings.append(morning)
        done = len([row for row in morning.outcomes if row["state"] == jobs.DONE])
        print(
            f"  day {day} ({morning.on}, period {morning.matchup_period}, {morning.games} games): "
            f"{morning.wall_seconds}s wall on {morning.workers} worker(s), {done}/{len(teams)} done"
        )

    look_ahead = [line for morning in mornings for line in morning.look_ahead]
    page_notes = [
        f"day {morning.day}: {line}" for morning in mornings for line in morning.page_findings
    ]
    digest_notes = [
        f"day {morning.day}: {line}"
        for morning in mornings
        for row in morning.digests
        for line in row["findings"]
    ]
    broken = [
        f"day {morning.day} {row['name']}: {row['state']} ({row['note']})"
        for morning in mornings
        for row in morning.outcomes
        if row["state"] != jobs.DONE
    ]

    checks: dict[str, Any] = {
        "0_every_job_finished": {"pass": not broken, "rows": broken[:40]},
        "1_no_look_ahead": {"pass": not look_ahead, "rows": look_ahead[:40]},
        "3_digest_fits_the_channel": {
            "pass": not digest_notes,
            "rows": digest_notes[:40],
            "characters": summarise(
                [float(row["characters"]) for morning in mornings for row in morning.digests]
            ),
        },
        "4_served_from_the_store": {"pass": not page_notes, "rows": page_notes[:40]},
    }
    if args.checks:
        first_day, first_date = days[0], calendar.date_of(days[0])
        ok, rows = no_job_ran_twice(log_path)
        checks["5a_no_job_ran_twice"] = {"pass": ok, "rows": rows}
        with factory() as session:
            ok, rows = dedupe_check(session, keys.league_pk, teams, run, first_day, first_date)
        checks["5b_no_duplicate_enqueue"] = {"pass": ok, "rows": rows}
        ok, rows = retry_then_park(factory, keys.league_pk, owner, run, first_day, first_date)
        checks["5c_retried_then_parked"] = {"pass": ok, "rows": rows}
        ok, rows = killed_worker_recovered(
            factory, keys.league_pk, owner, run, first_day, first_date, log_path
        )
        checks["5d_killed_worker_recovered"] = {"pass": ok, "rows": rows}
        ok, rows = determinism(factory, keys.league_pk, owner, run, first_day, first_date, log_path)
        checks["2_determinism"] = {"pass": ok, "rows": rows}

    result: dict[str, Any] = {
        "run": run,
        "season": keys.season,
        "period": args.period,
        "playoff_period": bool(period.is_playoff),
        "days": days,
        "teams": [asdict(team) for team in teams],
        "quiet_day_in_period": quiet,
        "mornings": [asdict(morning) for morning in mornings],
        "timing": {
            "per_team_day": summarise(
                [
                    float(row["seconds"])
                    for morning in mornings
                    for row in morning.outcomes
                    if row["seconds"] is not None
                ]
            ),
            "digest_seconds": summarise(
                [float(row["seconds"]) for morning in mornings for row in morning.digests]
            ),
            "wall_by_workers": [
                {
                    "day": morning.day,
                    "teams": len(teams),
                    "workers": morning.workers,
                    "wall_seconds": morning.wall_seconds,
                }
                for morning in mornings
            ],
            "page_seconds": summarise(
                [
                    float(page["seconds"])
                    for morning in mornings
                    for page in morning.pages
                    if page.get("status") == 200
                ]
            ),
        },
        "checks": checks,
    }
    if args.profile:
        result["profile"] = profile_precompute(
            owner, member, days[0], calendar.date_of(days[0]), out_dir
        )

    (out_dir / "rehearsal.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    failed = [name for name, body in checks.items() if not body.get("pass")]
    print(
        f"\nchecks: {len(checks) - len(failed)} passed, {len(failed)} failed"
        + (f" ({', '.join(failed)})" if failed else "")
    )
    if args.keep:
        print(f"kept this run's rows on the jobs table (--keep), labelled {REHEARSAL}:{run}:*")
    else:
        print(f"cleaned up {clean_up(factory, run)} job row(s) this run created")
    print(f"written to {out_dir}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--period", type=int, default=12, help="matchup period to replay")
    parser.add_argument("--teams", default="all", help="'all' or teams.id,teams.id")
    parser.add_argument("--days", type=int, default=None, help="only the first N days of it")
    parser.add_argument("--only-days", default=None, help="replay exactly these scoring periods")
    parser.add_argument("--workers", default="3", help="workers per day; a comma list is cycled")
    parser.add_argument("--out", default="rehearsals/latest")
    parser.add_argument("--run", default=None, help="tag for this run's jobs; default the season")
    parser.add_argument("--owner", type=int, default=3, help="ESPN id of the team we manage")
    parser.add_argument("--checks", action="store_true", help="also run the queue-semantics checks")
    parser.add_argument(
        "--keep", action="store_true", help="leave this run's job rows on the queue"
    )
    parser.add_argument("--profile", action="store_true", help="also profile one precompute")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--profile-one", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.run or not args.log:
            parser.error("--worker needs --run and --log")
        return work(args.run, Path(args.log))
    if args.profile_one:
        # Re-entered by `--profile`, in an interpreter that has built nothing.
        first, second = (int(part) for part in args.teams.split(","))
        return profile_one(
            first,
            second,
            int(args.only_days),
            date.fromisoformat(args.run),
            Path(args.out).resolve(),
        )
    return replay(args)


if __name__ == "__main__":
    sys.exit(main())
