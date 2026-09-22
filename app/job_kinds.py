"""What each kind of job does (docs/jobs.md). The queue is `app.jobs`; when
they are enqueued is `app.schedule`.

* `ingest`: one league's nightly refresh (`app.league_ingest`), read with
  the league's own sealed connection, or with the `.env` cookies for
  `ESPN_LEAGUE_ID` when it has none. After it works, the connection is
  marked good and its owner verified on the teams his SWID owns
  (`memberships.verify_connection_owner`), which is how a newly connected
  league's connector gets his team.
* `status_pass`: the listener, for one league. The league the listener
  follows (`ESPN_LEAGUE_ID`) gets the whole pass, exactly as
  `scripts/status_pass.py` runs it; any other league gets its own wire only
  (`run_wire_pass`, and "One listener league" in docs/jobs.md for why).
* `precompute`: one team's day, week and season reports for today, stored
  in `team_reports` for the pages and routes to read (`app.reports`).
* `digest`: one member's morning digest, or an alert between digests, mailed
  to his verified addresses. The server's owner also gets the `.env`
  recipients, and his digest of the tracked team marks the events it reports
  as notified, as `scripts/digest.py` always has; everyone else's reads the
  events since his own last digest and marks nothing. A member whose only
  channel was a Telegram chat or an ntfy topic has none now (migration
  `0024`), and the job says so.

  **What is in it is his** (`app.subscriptions`): per league, the topics he
  has switched on and whether he wants the morning digest and the alerts at
  all. A member who wants neither, or who has turned off every topic, is not
  sent to and the job's note says which of those it was. The owner's tracked
  team in single mode keeps everything on: single mode is one person reading
  his own server, and nothing there should be silently missing.
* `injury_backfill`: a whole season of the NBA's official injury reports
  (`app.injury_backfill`, docs/injuries.md). Hours at the full cadence,
  which is why it is queued rather than held open in a terminal.
* `injury_pass`: the same for today only, for the season in progress.
  Neither belongs to a league, so both carry a `season` in their payload
  and no `league_id`; neither is on a schedule yet (docs/jobs.md).

Every failure is put into a fixed sentence (`JobError`) before it is stored
or logged: ESPN's and the mail server's words stay out of both.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import requests
from espn_api.basketball import League as ESPNLeague
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague, ESPNUnknownError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import (
    accounts,
    channels,
    injury_backfill,
    injury_reports,
    jobs,
    league_ingest,
    memberships,
    notify,
    reports,
    secrets_box,
    subscriptions,
)
from app.config import Settings, get_settings
from app.db.models import League, LeagueConnection, LeagueSeason, Team, User
from app.digest import (
    Digest,
    build_alert,
    build_digest,
    latest_listened_season,
    league_season_for,
    league_section,
    mark_notified,
    team_section_without_listener,
)
from app.espn import ESPNSettings, fetch_newest_league, get_espn_settings
from app.ingest_runs import record_run
from app.jobs import JobError, JobRef
from app.listener.status import label_for, next_pass_after, run_status_pass, run_wire_pass
from app.mail import Mail, alert_mail, digest_mail, lines_mail

log = logging.getLogger("fcp.jobs")

MORNING = "morning"
ALERT = "alert"
DIGEST_TITLE = "FCP morning digest"
ALERT_TITLE = "FCP: a player of yours is out"
#: How far back a member's first digest (or alert) looks, with no earlier one.
FIRST_WINDOW = {MORNING: timedelta(hours=24), ALERT: timedelta(hours=12)}

#: The ingest_runs mode of a pass over a league the listener does not follow,
#: so the watchdog's listener check (mode "status") stays the listener's own.
WIRE_MODE = "wire"

#: What a member with nowhere to send to hears. Named rather than written
#: twice: a Telegram row the migration disabled is not a channel, so a member
#: who only ever confirmed one lands here and the note has to say why.
NO_ADDRESS = "no confirmed email address; nothing was sent"

#: What the job says when a member has switched the message off. Three
#: separate notes, because they are three different things to look at: he
#: does not want the morning one, he does not want to be interrupted between
#: them, or he has turned off every topic there is and so there is nothing to
#: put in a message at all (`app.subscriptions`).
NOT_SUBSCRIBED = {
    MORNING: "he does not want the morning digest in this league",
    ALERT: "he does not want alerts between digests in this league",
}
NO_TOPICS = "every topic is off in this league; there is nothing to send"

#: An alert is filtered by the same topics the digest is. `build_alert` names
#: men on the reader's own roster, so today it rides on `my_team`; an alert
#: about the opponent, when there is one to send, rides on `opponent` through
#: `Subscription.allows` like every other line of the feed.
NO_ROSTER_ALERT = "he does not want his own roster's news, so there is no alert to send"

NO_LOGIN = "the league has no live connection to read it with"
NO_KEY = "FCP_SECRETS_KEY is not set, so the league's login cannot be opened"
BAD_KEY = "the league's sealed login does not open with this FCP_SECRETS_KEY"
REFUSED = "ESPN refused the league's login; whoever connected it needs to reconnect"
NO_LEAGUE = "ESPN has no such league or season"
ESPN_DOWN = "ESPN could not be reached, or answered with an error"


# ---------------------------------------------------------------------------
# logins
# ---------------------------------------------------------------------------


def _env_login() -> ESPNSettings | None:
    """The `.env` ESPN login, or None when it is not configured."""
    try:
        return get_espn_settings()
    except ValidationError:
        return None


def league_login(
    session: Session, league: League, settings: Settings
) -> tuple[ESPNSettings, LeagueConnection | None]:
    """The login that reads this league, and its connection if it is one:
    the active connection's sealed cookies, else the `.env` ones when this
    is `ESPN_LEAGUE_ID`. Raises `JobError` (no retry) when there is none."""
    connection = memberships.active_connection(session, league.id)
    if connection is not None and connection.sealed_credentials is not None:
        try:
            swid, espn_s2 = memberships.connection_credentials(connection, settings)
        except secrets_box.SecretsKeyMissingError:
            raise JobError(NO_KEY, retry=False) from None
        except secrets_box.SecretsUnreadableError:
            raise JobError(BAD_KEY, retry=False) from None
        login = ESPNSettings(
            espn_league_id=int(league.espn_league_id),
            espn_swid=swid,
            espn_s2=espn_s2,
            espn_season=None,
            fcp_tracked_team_id=None,
        )
        return login, connection
    if settings.espn_league_id is not None and int(league.espn_league_id) == int(
        settings.espn_league_id
    ):
        env = _env_login()
        if env is not None:
            return env, None
    raise JobError(NO_LOGIN, retry=False)


def espn_failure(error: BaseException) -> JobError:
    """An ESPN failure in our own words. A refused login or a missing league
    will not mend in twenty minutes, so neither is retried."""
    if isinstance(error, JobError):
        return error
    kind = error.kind if isinstance(error, league_ingest.FetchError) else type(error)
    if issubclass(kind, ESPNAccessDenied):
        return JobError(REFUSED, retry=False)
    if issubclass(kind, ESPNInvalidLeague):
        return JobError(NO_LEAGUE, retry=False)
    if issubclass(kind, ESPNUnknownError | requests.RequestException):
        return JobError(ESPN_DOWN)
    return JobError(f"failed ({kind.__name__})")


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def run_ingest(factory: sessionmaker[Session], job: JobRef, settings: Settings) -> str | None:
    with factory() as session:
        league = session.get(League, job.league_id) if job.league_id is not None else None
        if league is None:
            raise JobError("the league is not stored", retry=False)
        login, connection = league_login(session, league, settings)
        connection_id = connection.id if connection is not None else None
    try:
        summary = league_ingest.ingest_league(factory, login)
    except Exception as error:
        failure = espn_failure(error)
        if connection_id is not None:
            with factory() as session:
                found = session.get(LeagueConnection, connection_id)
                if found is not None:
                    memberships.record_check(session, found, failure.message)
                    session.commit()
        raise failure from None
    if connection_id is not None:
        with factory() as session:
            found = session.get(LeagueConnection, connection_id)
            if found is not None:
                memberships.record_check(session, found, None)
                claimed = memberships.verify_connection_owner(session, found, settings)
                session.commit()
                if claimed:
                    log.info(
                        "connection %s: its owner verified on %s team(s)",
                        connection_id,
                        len(claimed),
                    )
    return summary.describe()


# ---------------------------------------------------------------------------
# status pass
# ---------------------------------------------------------------------------

#: How a pass reaches ESPN: the newest season of the league. A test swaps it.
NewestFetch = Callable[[ESPNSettings], ESPNLeague]


def run_pass(
    factory: sessionmaker[Session],
    job: JobRef,
    settings: Settings,
    fetch: NewestFetch = fetch_newest_league,
) -> str | None:
    now = jobs.now()
    label = str(job.payload.get("label") or label_for(now))
    if label not in ("late", "nightly", "morning", "report"):
        label = "adhoc"
    with factory() as session:
        league = session.get(League, job.league_id) if job.league_id is not None else None
        if league is None:
            raise JobError("the league is not stored", retry=False)
        login, _ = league_login(session, league, settings)
    listener = settings.espn_league_id is not None and login.espn_league_id == int(
        settings.espn_league_id
    )
    try:
        espn_league = fetch(login)
    except Exception as error:
        raise espn_failure(error) from None
    season = int(espn_league.year)
    mode = "status" if listener else WIRE_MODE
    with (
        record_run(
            factory, espn_league_id=login.espn_league_id, season=season, mode=mode
        ) as detail,
        factory() as session,
    ):
        if listener:
            result = run_status_pass(
                session,
                espn_league,
                label=label,
                now=now,
                tracked_team_id=settings.fcp_tracked_team_id,
                next_pass_at=next_pass_after(now),
            )
        else:
            result = run_wire_pass(session, espn_league, label=label, now=now)
        session.commit()
        detail.update(result.describe())
    if result.skipped:
        return f"{label}: skipped, {result.skipped}"
    return f"{label}: {result.players} players, {result.events} events"


# ---------------------------------------------------------------------------
# precompute
# ---------------------------------------------------------------------------


def run_precompute(
    factory: sessionmaker[Session], job: JobRef, today: date | None = None
) -> str | None:
    """Build and store one team's three reports for today. A season with
    nothing to build from yet (no schedule, no roster) is not a failure:
    the note says so, and the routes build live as before."""
    from app.api.pickups import build_payload, readiness

    on = today or date.today()
    with factory() as session:
        team = session.get(Team, job.team_id) if job.team_id is not None else None
        if team is None:
            raise JobError("the team is not stored", retry=False)
        league_season = session.get(LeagueSeason, team.league_season_id)
        if league_season is None:  # pragma: no cover - the foreign key guarantees it
            raise JobError("the team's season is not stored", retry=False)
        calendar, missing = readiness(session, league_season)
        if missing or calendar is None:
            return "nothing to build yet: " + " and ".join(missing)
        day = calendar.scoring_period_on(on)
        built: list[str] = []
        for kind in reports.KINDS:
            try:
                payload = build_payload(session, league_season, team, kind, day)
            except ValueError:
                continue
            reports.store(session, team.id, kind, day, payload)
            built.append(kind)
        session.commit()
    if not built:
        return f"day {day} is in no matchup period; nothing stored"
    return f"stored {', '.join(built)} for day {day}"


# ---------------------------------------------------------------------------
# digest
# ---------------------------------------------------------------------------


def _entitled(session: Session, user: User, is_owner: bool) -> bool:
    from app.api import access

    if not access.BILLING_ENABLED or is_owner:
        return True
    return accounts.active_entitlement(session, user.id) is not None


def _owner_email(settings: Settings) -> str:
    return accounts.normalise_email(settings.fcp_owner_email or "") or accounts.OWNER_FALLBACK_EMAIL


def listened_season(session: Session, league: League, *, listener: bool) -> LeagueSeason | None:
    """The season a league's digest is about: for the listener's league, the
    newest season the listener has snapshotted (the digest's rule); for any
    other, its newest stored season."""
    if listener:
        season = latest_listened_season(session)
        if season is not None:
            found = league_season_for(session, int(league.espn_league_id), season)
            if found is not None:
                return found
    return session.scalar(
        select(LeagueSeason)
        .where(LeagueSeason.league_id == league.id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )


def _deliver(
    session: Session,
    user: User,
    *,
    to_env: bool,
    mail: Mail,
    settings: Settings,
) -> list[notify.Delivery] | None:
    """Mail his verified addresses, and the `.env` ones when he is the
    server's owner. None when he has no address at all.

    One `Mail` for both: the same subject, the same two parts and the same
    headers whoever it reaches, so the operator's copy is the copy the member
    got and not a second rendering of it.
    """
    mine = channels.verified(session, user.id)
    env_set = to_env and settings.email_configured
    if not mine and not env_set:
        return None
    results = (
        notify.deliver(
            settings, mail.text, title=mail.subject, html=mail.html, headers=mail.headers
        )
        if env_set
        else []
    )
    results += channels.deliver(
        mine,
        mail.text,
        title=mail.subject,
        settings=settings,
        html=mail.html,
        headers=mail.headers,
    )
    return results


def _outcome(results: list[notify.Delivery]) -> str:
    """A delivery's outcome in words: channel names only, never an error's text."""
    done = [r for r in results if r.sent]
    failed = [r.channel for r in results if not r.sent]
    if not done:
        raise JobError(f"no channel took it ({', '.join(failed)})"[:200])
    note = f"sent to {len(done)} of {len(results)} channel(s)"
    return note + (f"; failed: {', '.join(failed)}" if failed else "")


def run_digest(
    factory: sessionmaker[Session],
    job: JobRef,
    settings: Settings,
    now: datetime | None = None,
) -> str | None:
    mode = ALERT if job.payload.get("mode") == ALERT else MORNING
    at = now or jobs.now()
    with factory() as session:
        user = session.get(User, job.user_id) if job.user_id is not None else None
        league = session.get(League, job.league_id) if job.league_id is not None else None
        if user is None or league is None:
            raise JobError("the member or the league is not stored", retry=False)
        team = session.get(Team, job.team_id) if job.team_id is not None else None
        is_owner = user.email == _owner_email(settings)
        listener = settings.espn_league_id is not None and int(league.espn_league_id) == int(
            settings.espn_league_id
        )
        tracked = (
            is_owner
            and listener
            and team is not None
            and settings.fcp_tracked_team_id is not None
            and int(team.espn_team_id) == int(settings.fcp_tracked_team_id)
        )
        if tracked and team is not None and settings.fcp_auth_mode == "single":
            # Single mode is one person reading his own server: nothing there
            # should be silently missing, so the tracked team keeps every
            # topic whatever is stored (docs/jobs.md, "Subscriptions").
            return _owner_digest(
                session, user, team, mode, at, settings, subscriptions.everything()
            )
        wanted = subscriptions.for_member(session, user.id, league.id)
        if not (wanted.morning if mode == MORNING else wanted.alerts):
            return NOT_SUBSCRIBED[mode]
        if wanted.silent:
            return NO_TOPICS
        if tracked and team is not None:
            return _owner_digest(session, user, team, mode, at, settings, wanted)
        return _member_digest(
            session, job, user, league, team, mode, at, settings, listener, wanted
        )


def _owner_digest(
    session: Session,
    user: User,
    team: Team,
    mode: str,
    at: datetime,
    settings: Settings,
    wanted: subscriptions.Subscription,
) -> str | None:
    """The owner's digest of the tracked team: `scripts/digest.py`, with the
    league section after it, marking what it reports once it is delivered."""
    season = latest_listened_season(session)
    if season is None:
        raise JobError("the listener has not run yet; nothing to report", retry=False)
    league_id = int(settings.espn_league_id or 0)
    league_season = league_season_for(session, league_id, season)
    if league_season is None:
        raise JobError(f"no stored season {season} for the league", retry=False)
    espn_team_id = int(team.espn_team_id)
    if mode == ALERT:
        if not wanted.on(subscriptions.MY_TEAM):
            return NO_ROSTER_ALERT
        alert = build_alert(session, league_season, espn_team_id)
        if alert is None:
            return "no urgent change on the tracked roster"
        text, event_ids = alert
        mail = alert_mail(
            team.name, text, when=f"{at:%a %d %b, %H:%M} UTC", public_url=settings.fcp_public_url
        )
    else:
        digest = build_digest(session, league_season, espn_team_id, now=at, wanted=wanted)
        league_lines = league_section(session, league_season, now=at)
        event_ids = digest.event_ids
        mail = digest_mail(
            digest,
            wanted=wanted,
            public_url=settings.fcp_public_url,
            league_tail="\n".join(league_lines),
        )
    results = _deliver(session, user, to_env=True, mail=mail, settings=settings)
    if results is None:
        return f"{NO_ADDRESS}, {len(event_ids)} event(s) stay unnotified"
    note = _outcome(results)
    marked = mark_notified(session, event_ids, at)
    session.commit()
    return f"{note}; marked {marked} event(s) notified"


def _member_digest(
    session: Session,
    job: JobRef,
    user: User,
    league: League,
    team: Team | None,
    mode: str,
    at: datetime,
    settings: Settings,
    listener: bool,
    wanted: subscriptions.Subscription,
) -> str | None:
    """Anyone else's: the league section, and his team's when he manages one
    and is entitled; the events since his own last digest, marking nothing."""
    is_owner = user.email == _owner_email(settings)
    since = jobs.last_done(
        session, jobs.DIGEST, user_id=user.id, team_id=job.team_id, mode=mode
    ) or (at - FIRST_WINDOW[mode])
    entitled = _entitled(session, user, is_owner)
    parts: list[str] = []
    # The `Digest` when there is one, so the HTML part can draw the lineup as
    # a grid rather than re-reading a sentence. A league the listener does not
    # follow has no `Digest` (docs/jobs.md, "One listener league"), and its
    # message is the same lines as a plain page.
    built: Digest | None = None
    league_tail = ""
    if team is not None and entitled:
        league_season = session.get(LeagueSeason, team.league_season_id)
        if league_season is None:  # pragma: no cover - the foreign key guarantees it
            raise JobError("the team's season is not stored", retry=False)
        espn_team_id = int(team.espn_team_id)
        if mode == ALERT:
            # What he wants is asked before what the server can do: a reader
            # who does not want to be interrupted should not have the answer
            # depend on which league he is in.
            if not wanted.on(subscriptions.MY_TEAM):
                return NO_ROSTER_ALERT
            if not listener:
                return "alerts need the listener's league; none for this one yet"
            alert = build_alert(session, league_season, espn_team_id, since=since)
            if alert is None:
                return "no urgent change on his roster"
            parts.append(alert[0])
        elif listener:
            built = build_digest(
                session, league_season, espn_team_id, now=at, since=since, wanted=wanted
            )
            parts.append(built.render())
        else:
            parts.append(
                "\n".join(
                    team_section_without_listener(session, league_season, espn_team_id, now=at)
                )
            )
    elif mode == ALERT:
        return "no team of his to alert about"
    if mode == MORNING:
        season_row = listened_season(session, league, listener=listener)
        if season_row is None:
            return "the league has no season stored yet; nothing to send"
        name = memberships.league_name(session, league)
        header = [] if parts else [f"{name} - {at:%a %d %b}, {at:%H:%M} UTC", ""]
        league_tail = "\n".join(header + league_section(session, season_row, now=at))
        parts.append(league_tail)
    text = "\n\n".join(parts)
    where = settings.fcp_public_url
    if mode == ALERT:
        mail = alert_mail(
            team.name if team is not None else "FCP",
            text,
            when=f"{at:%a %d %b, %H:%M} UTC",
            public_url=where,
        )
    elif built is not None:
        mail = digest_mail(built, wanted=wanted, public_url=where, league_tail=league_tail)
    else:
        mail = lines_mail(
            memberships.league_name(session, league),
            text,
            when=f"{at:%A %d %B %Y}",
            public_url=where,
        )
    results = _deliver(session, user, to_env=is_owner, mail=mail, settings=settings)
    if results is None:
        return NO_ADDRESS
    return _outcome(results)


# ---------------------------------------------------------------------------
# the NBA's injury reports
# ---------------------------------------------------------------------------

NO_SEASON = "the job names no season to load injury reports for"
NO_SCHEDULE = "no NBA schedule is stored for that season, so there are no dates to walk"
NO_PARSER = "pdfplumber is not installed, so the injury report PDFs cannot be read"


def _payload_season(job: JobRef) -> int:
    season = job.payload.get("season")
    try:
        return int(str(season))
    except (TypeError, ValueError):
        raise JobError(NO_SEASON, retry=False) from None


def _injury_days(factory: sessionmaker[Session], season: int, *, only: date | None) -> list[date]:
    with factory() as session:
        days = injury_backfill.game_dates(session, season)
    if not days:
        raise JobError(NO_SCHEDULE, retry=False)
    return [day for day in days if day == only] if only is not None else days


def _load(
    factory: sessionmaker[Session],
    *,
    season: int,
    days: list[date],
    which: str,
    mode: str,
) -> str:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        raise JobError(NO_PARSER, retry=False) from None
    counts = injury_backfill.run_backfill(factory, season=season, days=days, which=which, mode=mode)
    rate = "" if counts.match_rate is None else f", {counts.match_rate:.1%} of names placed"
    return (
        f"{counts.dates} dates, {counts.snapshots_fetched} snapshots, {counts.inserted} rows{rate}"
    )


def run_injury_backfill(factory: sessionmaker[Session], job: JobRef) -> str | None:
    """Walk a whole season's injury reports (docs/injuries.md).

    Hours, not minutes, at `--snapshots all`, which is why it is a job:
    queued per season and taken by the worker rather than held open in a
    terminal. `snapshots` in the payload picks the cadence.
    """
    season = _payload_season(job)
    which = str(job.payload.get("snapshots", "all"))
    return _load(
        factory,
        season=season,
        days=_injury_days(factory, season, only=None),
        which=which,
        mode=injury_backfill.BACKFILL,
    )


def run_injury_pass(factory: sessionmaker[Session], job: JobRef, today: date | None) -> str | None:
    """Today's injury report snapshots for the live season.

    Only today: a snapshot already lists the next day's games, so today's is
    enough to learn about tomorrow. A day the league does not play has no
    report to fetch and is not a failure.
    """
    season = _payload_season(job)
    day = today or datetime.now(UTC).astimezone(injury_reports.ET).date()
    days = _injury_days(factory, season, only=day)
    if not days:
        return "no NBA games that day; nothing to fetch"
    return _load(
        factory,
        season=season,
        days=days,
        which=str(job.payload.get("snapshots", "all")),
        mode=injury_backfill.PASS,
    )


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------


BAD_CLOCK = "the job names a day that is not a date"


def payload_day(job: JobRef, key: str) -> date | None:
    """The day a job says it is about, or None when it means today.

    A real job carries no clock: the morning's precompute is about the
    morning it runs in. A **rehearsal** job does carry one
    (`scripts/rehearse_week.py` replays a played season through this queue,
    a day at a time), and the handler has to honour it in the worker
    process, which is where the report is actually built. Without this the
    day could only be passed by calling the handler directly, and then the
    queue, the claim and the worker would never be exercised at all.
    """
    named = job.payload.get(key)
    if named is None:
        return None
    try:
        return date.fromisoformat(str(named))
    except ValueError:
        raise JobError(BAD_CLOCK, retry=False) from None


def payload_moment(job: JobRef, key: str) -> datetime | None:
    """The moment a job says it is about, or None when it means now."""
    named = job.payload.get(key)
    if named is None:
        return None
    try:
        return datetime.fromisoformat(str(named))
    except ValueError:
        raise JobError(BAD_CLOCK, retry=False) from None


def handlers(settings: Settings | None = None) -> dict[str, jobs.Handler]:
    """The six kinds, bound to the process's settings (or a test's)."""

    def current() -> Settings:
        return settings or get_settings()

    return {
        jobs.INGEST: lambda factory, job: run_ingest(factory, job, current()),
        jobs.STATUS_PASS: lambda factory, job: run_pass(factory, job, current()),
        jobs.PRECOMPUTE: lambda factory, job: run_precompute(
            factory, job, payload_day(job, "today")
        ),
        jobs.DIGEST: lambda factory, job: run_digest(
            factory, job, current(), payload_moment(job, "now")
        ),
        jobs.INJURY_BACKFILL: lambda factory, job: run_injury_backfill(factory, job),
        jobs.INJURY_PASS: lambda factory, job: run_injury_pass(
            factory, job, payload_day(job, "today")
        ),
    }
