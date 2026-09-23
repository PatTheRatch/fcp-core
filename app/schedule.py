"""What each schedule label enqueues (docs/jobs.md), for `scripts/enqueue.py`.

The labels are the listener's pass labels, at the same UTC times as the
timers that run today (`app.listener.status.PASS_SCHEDULE`):

| label | UTC | per league | then, after it |
|---|---|---|---|
| nightly | 09:00 | `ingest`, spread over `NIGHT_WINDOW` | its `status_pass` (nightly) |
| morning | 15:00 | `status_pass` (morning) | `precompute` per claimed team, `digest` per member |
| report | 22:30 | `status_pass` (report) | an alert `digest` per member with a team |
| late | 00:30 | `status_pass` (late) | the same |

Everything after the pass `depends_on` it, so when a pass fails for good the
digest does not go out, which is what `scripts/scheduled_status.sh` does
today. The precomputes are due a second before the digests, so a worker
taking jobs in order builds the reports first.

WHICH LEAGUES

Every league with a live connection, and `ESPN_LEAGUE_ID` whenever its `.env`
login is set (read with that login when it has no connection of its own).
The `.env` league is scheduled in both modes, because it is the owner's and
his cookies read it: switching to accounts mode must not stop its ingest.

SPREAD

Each league's ingest is due `ingest_offset` after 09:00: a stable hash of
the ESPN league id into `NIGHT_WINDOW`, so ten leagues reach ESPN over half
an hour rather than all at once, and the same league at the same minute
every night. Thirty minutes keeps every league's ingest before the 09:30
BBM pull and the 10:00 backup, as the one league's is today.

WHO GETS A DIGEST

In single mode, the owner alone, about the tracked team, to the `.env`
channels (and any he has verified). In accounts mode, every member of each
league: his verified team there in the season the digest is about, if he
manages one, and the league section either way.

A connection whose league wants an ingest (`ingest_requested_at` newer than
its last good one: just connected, or reconnected) gets one at once, on
whichever label fires next.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app import accounts, jobs, memberships
from app.config import Settings
from app.db.models import (
    IngestRun,
    Job,
    League,
    LeagueConnection,
    LeagueSeason,
    Membership,
    Team,
    TeamManager,
)
from app.espn import get_espn_settings
from app.ingest_runs import SUCCEEDED
from app.job_kinds import ALERT, MORNING, listened_season
from app.listener.status import PASS_SCHEDULE

log = logging.getLogger("fcp.schedule")

NIGHTLY = "nightly"
LABELS = ("nightly", "morning", "report", "late")
#: Where the night's ingests are spread: after 09:00, for this long.
NIGHT_WINDOW = timedelta(minutes=30)
#: The label of an ingest asked for by a connection, outside the night.
REQUESTED = "requested"
#: The stagger that orders a pass's follow-ons: reports before digests.
STEP = timedelta(seconds=1)


@dataclass(frozen=True)
class ScheduledLeague:
    league_pk: int
    espn_league_id: int
    #: The league the listener follows (`ESPN_LEAGUE_ID`).
    listener: bool


@dataclass(frozen=True)
class Recipient:
    user_id: int
    #: `teams.id` of his team in the digest's season, or None.
    team_pk: int | None


def ingest_offset(espn_league_id: int) -> timedelta:
    """Where in the night window this league's ingest falls: stable across
    nights and processes (sha256, not Python's salted `hash`)."""
    digest = hashlib.sha256(str(int(espn_league_id)).encode()).hexdigest()
    return timedelta(seconds=int(digest[:12], 16) % int(NIGHT_WINDOW.total_seconds()))


def slot(label: str, now: datetime) -> datetime:
    """The scheduled time this firing belongs to: the label's slot nearest
    `now`, so a timer that fires a few minutes late (or a caught-up
    `Persistent=` firing) still keys its jobs on the slot's own day."""
    at = PASS_SCHEDULE[label]
    candidates = [
        datetime.combine(now.date() + timedelta(days=offset), at, tzinfo=UTC)
        for offset in (-1, 0, 1)
    ]
    return min(candidates, key=lambda candidate: abs(candidate - now))


def _env_league_configured(settings: Settings) -> bool:
    if settings.espn_league_id is None:
        return False
    try:
        get_espn_settings()
    except ValidationError:
        return False
    return True


def scheduled_leagues(session: Session, settings: Settings) -> list[ScheduledLeague]:
    """Every league with a live connection, and the `.env` league."""
    rows = session.execute(
        select(League.id, League.espn_league_id)
        .join(LeagueConnection, LeagueConnection.league_id == League.id)
        .where(
            LeagueConnection.revoked_at.is_(None),
            LeagueConnection.sealed_credentials.is_not(None),
        )
    ).all()
    found = {int(espn_id): int(pk) for pk, espn_id in rows}
    if _env_league_configured(settings) and settings.espn_league_id is not None:
        league = memberships.get_or_create_league(session, int(settings.espn_league_id))
        found.setdefault(int(league.espn_league_id), int(league.id))
    return [
        ScheduledLeague(pk, espn_id, espn_id == settings.espn_league_id)
        for espn_id, pk in sorted(found.items())
    ]


def claimed_teams(
    session: Session, league: ScheduledLeague, season_row: LeagueSeason, settings: Settings
) -> list[int]:
    """`teams.id` of every team in the season with a verified manager, and in
    single mode the tracked team, which is the owner's whatever is written."""
    ids = set(
        session.scalars(
            select(Team.id)
            .join(TeamManager, TeamManager.team_id == Team.id)
            .where(Team.league_season_id == season_row.id, TeamManager.state == accounts.VERIFIED)
        ).all()
    )
    if league.listener and settings.fcp_tracked_team_id is not None:
        tracked = session.scalar(
            select(Team.id).where(
                Team.league_season_id == season_row.id,
                Team.espn_team_id == settings.fcp_tracked_team_id,
            )
        )
        if tracked is not None:
            ids.add(int(tracked))
    return sorted(int(team) for team in ids)


def _owner(session: Session, settings: Settings) -> int:
    email = accounts.normalise_email(settings.fcp_owner_email or "") or (
        accounts.OWNER_FALLBACK_EMAIL
    )
    user = accounts.ensure_owner(
        session, email, settings.espn_league_id, settings.fcp_tracked_team_id
    )
    return int(user.id)


def recipients(
    session: Session, league: ScheduledLeague, season_row: LeagueSeason, settings: Settings
) -> list[Recipient]:
    """Who gets this league's digest, each with his team in the season."""
    if settings.fcp_auth_mode == "single":
        if not league.listener:
            return []
        owner = _owner(session, settings)
        team = session.scalar(
            select(Team.id).where(
                Team.league_season_id == season_row.id,
                Team.espn_team_id == settings.fcp_tracked_team_id,
            )
        )
        return [Recipient(owner, int(team) if team is not None else None)]
    if league.listener and settings.espn_league_id is not None:
        _owner(session, settings)  # his membership and claims, as the API writes them
    members = session.scalars(
        select(Membership.user_id).where(Membership.league_id == league.league_pk)
    ).all()
    out: list[Recipient] = []
    for user_id in sorted({int(member) for member in members}):
        teams = session.scalars(
            select(Team.id)
            .join(TeamManager, TeamManager.team_id == Team.id)
            .where(
                Team.league_season_id == season_row.id,
                TeamManager.user_id == user_id,
                TeamManager.state == accounts.VERIFIED,
            )
            .order_by(Team.espn_team_id)
        ).all()
        if teams:
            out.extend(Recipient(user_id, int(team)) for team in teams)
        else:
            out.append(Recipient(user_id, None))
    return out


def _requested_ingests(session: Session, at: datetime) -> list[tuple[int, int]]:
    """(league pk, ESPN id) of every live connection that asked for an ingest
    since its league's last good one."""
    last_good = (
        select(IngestRun.espn_league_id, func.max(IngestRun.started_at).label("at"))
        .where(IngestRun.status == SUCCEEDED, IngestRun.mode.in_(("recent", "full")))
        .group_by(IngestRun.espn_league_id)
        .subquery()
    )
    rows = session.execute(
        select(
            League.id, League.espn_league_id, LeagueConnection.ingest_requested_at, last_good.c.at
        )
        .join(LeagueConnection, LeagueConnection.league_id == League.id)
        .outerjoin(last_good, last_good.c.espn_league_id == League.espn_league_id)
        .where(
            LeagueConnection.revoked_at.is_(None),
            LeagueConnection.sealed_credentials.is_not(None),
            LeagueConnection.ingest_requested_at.is_not(None),
        )
    ).all()
    return [
        (int(pk), int(espn_id)) for pk, espn_id, asked, done in rows if done is None or asked > done
    ]


def _intake_for(session: Session, league_pk: int, at: datetime) -> list[jobs.Enqueued]:
    """The intake chain for a league that has never had one, or nothing.

    A newly connected league is the whole reason the chain exists: the
    connection asks for an ingest, and what it really wants is every season
    ESPN holds and the four measurements on them (docs/intake.md). A league
    measured on an earlier day is left alone; re-measuring is the account
    page's button.

    Enqueued with `force`, so a timer that fires twice in a day hits the
    dedupe key and adds nothing the second time rather than being refused --
    which keeps `enqueue_schedule`'s own promise that running it twice
    returns the same jobs.
    """
    from app import intake

    previous = intake.last_started(session, league_pk)
    if previous is not None and previous.astimezone(UTC).date() != at.astimezone(UTC).date():
        return []
    return intake.enqueue_intake(session, league_pk, at=at, force=True)


def enqueue_schedule(
    session: Session, settings: Settings, label: str, now: datetime | None = None
) -> list[jobs.Enqueued]:
    """Enqueue what `label` means for every scheduled league. Safe to run
    twice: the second run adds nothing (`jobs.dedupe_key`). Commits."""
    if label not in LABELS:
        raise ValueError(f"unknown schedule {label!r}; one of {', '.join(LABELS)}")
    at = now or jobs.now()
    base = slot(label, at)
    out: list[jobs.Enqueued] = []
    for pk, _ in _requested_ingests(session, at):
        out.append(jobs.enqueue(session, jobs.INGEST, run_after=at, label=REQUESTED, league_id=pk))
        # A league that has never been measured gets the whole intake chain
        # rather than one ingest: every season ESPN will give us, and each of
        # the measurements on its own history (docs/intake.md). The refusal
        # is quiet here -- one already running, or one today -- because this
        # runs on a timer and has nobody to tell.
        out += _intake_for(session, pk, at)

    for league in scheduled_leagues(session, settings):
        if label == NIGHTLY:
            due = base + ingest_offset(league.espn_league_id)
            ingest = jobs.enqueue(
                session, jobs.INGEST, run_after=due, label=label, league_id=league.league_pk
            )
            passed = jobs.enqueue(
                session,
                jobs.STATUS_PASS,
                run_after=due + STEP,
                label=label,
                league_id=league.league_pk,
                depends_on=ingest.id,
            )
            out += [ingest, passed]
            continue

        passed = jobs.enqueue(
            session, jobs.STATUS_PASS, run_after=base, label=label, league_id=league.league_pk
        )
        out.append(passed)
        stored_league = session.get(League, league.league_pk)
        season_row = (
            listened_season(session, stored_league, listener=league.listener)
            if stored_league is not None
            else None
        )
        if season_row is None:
            continue
        if label == "morning":
            for team_pk in claimed_teams(session, league, season_row, settings):
                out.append(
                    jobs.enqueue(
                        session,
                        jobs.PRECOMPUTE,
                        run_after=base + STEP,
                        label=label,
                        league_id=league.league_pk,
                        team_id=team_pk,
                        depends_on=passed.id,
                    )
                )
        mode = MORNING if label == "morning" else ALERT
        for person in recipients(session, league, season_row, settings):
            if mode == ALERT and person.team_pk is None:
                continue
            out.append(
                jobs.enqueue(
                    session,
                    jobs.DIGEST,
                    run_after=base + 2 * STEP,
                    label=label,
                    league_id=league.league_pk,
                    team_id=person.team_pk,
                    user_id=person.user_id,
                    depends_on=passed.id,
                    payload={"mode": mode},
                )
            )
    session.commit()
    return out


def label_now(now: datetime | None = None) -> str | None:
    """The schedule label a firing at `now` stands for, or None when it is
    near no slot (the pass's own rule, `label_for`)."""
    from app.listener.status import label_for

    label = label_for(now or jobs.now())
    return label if label in LABELS else None


def jobs_table_exists(session: Session) -> bool:
    """Whether migration 0020 has run: the enqueue and the worker refuse to
    start without it, rather than failing on every query."""
    try:
        session.execute(select(Job.id).limit(1))
    except ProgrammingError:
        session.rollback()
        return False
    return True
