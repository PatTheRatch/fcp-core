"""The morning digest: what changed, for the one team being tracked.

Layer 3 of docs/pickups.md, phase 1b. Plain text, built from stored rows
only: no ESPN request. Four sections:

1. The tracked roster, from the listener's events (went out, returned, a
   moved return date, a minutes drop), then where that roster stands now.
2. The wire, from events on unrostered players worth a look (a minutes
   spike, an ownership surge, dropped by a rival, waivers clearing).
3. This week: the matchup as it stands and the day's streaming plan, from
   `app.pickups.stream` (section 3 of the design note).
4. Churn, the team's adds in the last fortnight, because this league's own
   history says the heavier movers returned less per move
   (docs/acquirable_value.md, r = -0.63 between add volume and return).

Section 4 of the design note, the rest-of-season advice, is still to come,
as is a value rank for a free agent: percent owned stands in meanwhile.

THE PLAN NEVER BREAKS THE DIGEST

Section 3 is the only one that runs the recommender, and the recommender
needs a schedule, a roster, a wire and a matchup period. On a bye, before
the season's first matchup, or with any of those missing, the section is one
line saying so and the other three are untouched. `week_plan` therefore
catches everything, including exceptions it cannot name: a digest that
fails to go out because a pickup report could not be built would lose the
roster news too, which is the part that is always worth reading.

Every event the digest reports on, including the ones it summarises as "and
N more", is marked `notified_at` by the caller once delivery has actually
succeeded. Kinds the digest never shows are never queried and never marked.
The plan marks nothing: it reports state, not news.

MORE THAN ONE READER (step 4, docs/jobs.md)

`notified_at` is one column on the event, so it can only mean "the owner has
been told". A member's digest, sent by the `digest` job, passes `since`
instead: the events observed since his own last digest went out, and marks
nothing. The owner's digest keeps marking, exactly as `scripts/digest.py`
always has. `league_section` is the free part every member gets: the week's
matchups as they stand and the wire's traffic, from the league's own rows.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    LeagueSeason,
    PlayerStatusEvent,
    PlayerStatusSnapshot,
    Team,
    Transaction,
    TransactionItem,
)
from app.listener import events as kinds
from app.listener.pool import UNROSTERED_STATUSES
from app.listener.snapshots import latest_snapshots
from app.pickups.judge import Judgement
from app.pickups.state import RosteredPlayer, period_for_day, season_calendar
from app.pickups.stream import ADD, IR_MOVE, Move, StreamReport, stream_recommendations
from app.scoring.wire import WIRE_TYPES

#: What a change to your own player can be: anything that moves whether he
#: plays, or how much. An ownership move on a player you already hold tells
#: you nothing you can act on, so it is not here.
ROSTER_KINDS = (
    kinds.WENT_OUT,
    kinds.DOWNGRADED,
    kinds.UPGRADED,
    kinds.RETURNED,
    kinds.RETURN_DATE_CHANGED,
    kinds.CHANGED_PRO_TEAM,
    kinds.MINUTES_SPIKE,
    kinds.MINUTES_DROP,
)

#: What makes an unrostered player worth a look this morning.
WIRE_KINDS = (
    kinds.MINUTES_SPIKE,
    kinds.OWNERSHIP_SURGE,
    kinds.DROPPED,
    kinds.WAIVER_CLEARING,
)

#: Worth interrupting the day for, between digests. Deliberately one kind:
#: an alert that fires for everything is an alert nobody reads.
URGENT_KINDS = (kinds.WENT_OUT,)

#: Line budgets. Anything past a cap is counted, not listed, and the events
#: route has the rest.
ROSTER_EVENT_LIMIT = 8
WIRE_EVENT_LIMIT = 10
#: A roster with seven players carrying a status at once is exceptional, and
#: the count line below still says how many there are.
STATUS_LINE_LIMIT = 6
#: Empty days named before the rest are counted. Three is already a bad week.
EMPTY_DAY_LIMIT = 3
#: The whole message's cap, which is what makes it readable on a phone. It
#: was forty before the week section; that section is a header, at most
#: `EMPTY_DAY_LIMIT` + 1 empty-day lines and at most `PLAN_MOVES` move lines,
#: so a dozen more keeps every other section's budget exactly where it was.
MAX_LINES = 52

#: The window the churn line counts over, from docs/pickups.md section 4.4.
CHURN_DAYS = 14

#: Statuses worth naming in the roster's standing line.
_CONCERNING = ("OUT", "SUSPENSION", "DOUBTFUL", "QUESTIONABLE", "DAY_TO_DAY")

_KIND_LABELS = {
    kinds.WENT_OUT: "out",
    kinds.DOWNGRADED: "downgraded",
    kinds.UPGRADED: "upgraded",
    kinds.RETURNED: "back",
    kinds.RETURN_DATE_CHANGED: "return moved",
    kinds.CHANGED_PRO_TEAM: "traded",
    kinds.MINUTES_SPIKE: "minutes up",
    kinds.MINUTES_DROP: "minutes down",
    kinds.OWNERSHIP_SURGE: "being added",
    kinds.OWNERSHIP_SLIDE: "being dropped",
    kinds.DROPPED: "dropped",
    kinds.CLAIMED: "claimed",
    kinds.WAIVER_CLEARING: "clears waivers",
}


@dataclass(frozen=True)
class Line:
    """One reported event, already rendered down to its three columns."""

    kind: str
    player: str
    detail: str


@dataclass
class Digest:
    """What the digest found. `render()` is the message that gets sent."""

    season: int
    team_name: str
    generated_at: datetime
    roster: list[Line] = field(default_factory=list)
    roster_extra: int = 0
    standing: list[str] = field(default_factory=list)
    healthy: int = 0
    wire: list[Line] = field(default_factory=list)
    wire_extra: int = 0
    adds_recently: int = 0
    #: The week section, already rendered and indented (`week_plan`). One
    #: line when there is no plan to make; never empty.
    plan: list[str] = field(default_factory=list)
    #: Every event reported on, to mark notified once this has been sent.
    event_ids: list[int] = field(default_factory=list)

    @property
    def roster_size(self) -> int:
        return self.healthy + len(self.standing)

    def render(self) -> str:
        out = [
            f"{self.team_name} - {self.generated_at:%a %d %b}, "
            f"{self.generated_at:%H:%M} UTC - season {self.season}"
        ]

        out.append("")
        out.append("YOUR ROSTER")
        out.extend(_render(self.roster, self.roster_extra, "nothing new"))

        out.append("")
        if self.standing:
            out.append("  Standing now:")
            out.extend(f"  {line}" for line in self.standing[:STATUS_LINE_LIMIT])
            hidden = len(self.standing) - STATUS_LINE_LIMIT
            if hidden > 0:
                out.append(f"  and {hidden} more carrying a status")
            out.append(f"  {self.healthy} of {self.roster_size} active")
        else:
            out.append(f"  All {self.healthy} active")

        out.append("")
        out.append("ON THE WIRE")
        out.extend(_render(self.wire, self.wire_extra, "nothing new"))

        out.append("")
        out.append("THIS WEEK")
        out.extend(self.plan or ["  no plan today"])

        out.append("")
        out.append("CHURN")
        churn = f"  {_plural(self.adds_recently, 'add')} in the last {CHURN_DAYS} days."
        if self.adds_recently:
            # Only worth saying when there is volume to weigh it against.
            churn += " Heavier movers here have returned less per move."
        out.append(churn)
        return "\n".join(out[:MAX_LINES])


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _render(lines: Sequence[Line], extra: int, empty: str) -> list[str]:
    if not lines:
        return [f"  {empty}"]
    out = [
        f"  {_KIND_LABELS.get(line.kind, line.kind):<14} {line.player:<22} {line.detail}".rstrip()
        for line in lines
    ]
    if extra > 0:
        out.append(f"  and {extra} more, see /events")
    return out


def _day(value: Any) -> str:
    """An ISO date from an event payload, printed short."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d %b")
    except ValueError:
        return str(value)


def describe(event: PlayerStatusEvent, teams: dict[int, str]) -> str:
    """The right-hand column: what this event actually says."""
    previous, current, detail = event.previous, event.current, event.detail
    if event.kind in (kinds.WENT_OUT, kinds.DOWNGRADED, kinds.UPGRADED, kinds.RETURNED):
        moved = f"{previous.get('injury_status') or '?'} to {current.get('injury_status') or '?'}"
        back = _day(current.get("expected_return_date"))
        return f"{moved}, back {back}" if back else moved
    if event.kind == kinds.RETURN_DATE_CHANGED:
        days = detail.get("days")
        way = "later" if isinstance(days, int) and days > 0 else "sooner"
        return f"{_day(current.get('expected_return_date'))}, {abs(int(days or 0))} days {way}"
    if event.kind == kinds.CHANGED_PRO_TEAM:
        return f"NBA team {previous.get('pro_team_id')} to {current.get('pro_team_id')}"
    if event.kind in (kinds.MINUTES_SPIKE, kinds.MINUTES_DROP):
        return (
            f"{detail.get('recent_mean')} min last {detail.get('recent_games')},"
            f" was {detail.get('prior_mean')}"
        )
    if event.kind in (kinds.OWNERSHIP_SURGE, kinds.OWNERSHIP_SLIDE):
        return f"{current.get('percent_owned')}% owned, {current.get('percent_change'):+g} today"
    if event.kind == kinds.DROPPED:
        by = teams.get(int(detail.get("from_team_id") or 0), "another team")
        return f"by {by}"
    if event.kind == kinds.CLAIMED:
        return f"by {teams.get(int(detail.get('to_team_id') or 0), 'another team')}"
    if event.kind == kinds.WAIVER_CLEARING:
        clears = detail.get("clears_at")
        return f"at {clears[11:16]} UTC" if isinstance(clears, str) and len(clears) > 16 else ""
    return ""


def _team_names(session: Session, league_season: LeagueSeason) -> dict[int, str]:
    return {
        team.espn_team_id: team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }


def league_season_for(session: Session, espn_league_id: int, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == espn_league_id, LeagueSeason.season == season)
    )


def latest_listened_season(session: Session) -> int | None:
    """The newest season the listener has snapshotted.

    The digest reads the database and never ESPN, so the season comes from
    what the passes wrote rather than from a fetch. In September that is
    already next season, which is the one the passes follow.
    """
    return session.scalar(select(func.max(PlayerStatusSnapshot.season)))


def unnotified(
    session: Session,
    season: int,
    kinds_wanted: Sequence[str],
    *,
    since: datetime | None = None,
) -> list[PlayerStatusEvent]:
    """The events still to tell: never notified, or, with `since`, observed
    after it (a member's own window, whatever the owner has been told)."""
    fresh = (
        PlayerStatusEvent.notified_at.is_(None)
        if since is None
        else PlayerStatusEvent.observed_at > since
    )
    return list(
        session.scalars(
            select(PlayerStatusEvent)
            .where(
                PlayerStatusEvent.season == season,
                fresh,
                PlayerStatusEvent.kind.in_(kinds_wanted),
            )
            .order_by(PlayerStatusEvent.observed_at.desc(), PlayerStatusEvent.id.desc())
        ).all()
    )


def adds_in_window(
    session: Session, league_season: LeagueSeason, team_id: int, *, now: datetime, days: int
) -> int:
    """Executed wire adds by this team in the trailing window, the volume guard.

    The window has two ends. Without the far one it is not a trailing window
    at all but everything since `now - days`, which on a live morning reads
    right because nothing has happened after now, and on any replayed day
    reads the rest of the season: a digest built for a day in January counted
    83 adds in the last fortnight where 15 had been made.
    """
    since = now - timedelta(days=days)
    return (
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
            .where(
                Transaction.league_season_id == league_season.id,
                Transaction.team_id == team_id,
                Transaction.type.in_(WIRE_TYPES),
                Transaction.status == "EXECUTED",
                Transaction.processed_at.is_not(None),
                Transaction.processed_at >= since,
                Transaction.processed_at <= now,
                TransactionItem.item_type == "ADD",
            )
        )
        or 0
    )


def _side(move: Move, today: int) -> str:
    """One move in the streaming CLI's own words, on one line.

    `scripts/stream.py` spreads this over four lines; a phone message cannot
    afford them, so the same facts -- who comes in, for how many of his games
    left, who goes out, and whether the man coming in is still on waivers --
    go on one, phrased as the CLI phrases them.
    """
    add: RosteredPlayer = move.add
    coming = f"{add.name} ({move.add_starts} of {add.games_remaining_this_period} games)"
    if not add.seatable_on(today) and add.waiver_clears_at is not None:
        coming += f", on waivers, clears {add.waiver_clears_at:%a}"
    if move.kind == ADD:
        return f"add {coming} into the open place"
    if move.kind == IR_MOVE and move.to_ir is not None:
        return f"add {coming}, {move.to_ir.name} to IR"
    if move.drop is not None:
        going = f"{move.drop.name} ({move.drop_starts} of {move.drop.games_remaining_this_period})"
        return f"add {coming}, drop {going}"
    return f"add {coming}"


def _record(record: tuple[float, float]) -> str:
    return f"{record[0]:.1f}-{record[1]:.1f}"


def _worth(judgement: Judgement) -> str:
    """Both horizons, the net, and the season record either way."""
    return (
        f"week {judgement.delta_week:+.2f} + season "
        f"{judgement.delta_season_per_week:+.2f}/wk = net {judgement.delta_total:+.2f}; "
        f"record {_record(judgement.record_without)} without, "
        f"{_record(judgement.record_with)} with"
    )


def _week_lines(report: StreamReport, opponent: str | None) -> list[str]:
    """The week section's body, from a report that was built."""
    first, last = report.scoring_periods_remaining[0], report.scoring_periods_remaining[-1]
    out = [f"  period {report.matchup_period}, days {first}-{last} left ({report.days_remaining})"]
    if report.on_bye:
        out.append("  on a bye this period, so there is no week to plan for")
        return out

    out[0] += f", v {opponent or report.opponent_team_id}"
    out.append(
        f"  {report.expected_wins:.2f} of 9 categories as things stand; "
        f"adds this period: used {report.adds_used} of {report.adds_budget}"
    )

    for day in report.empty_days[:EMPTY_DAY_LIMIT]:
        out.append(f"  day {day.scoring_period}: {', '.join(day.empty_slots)} going empty")
    hidden = len(report.empty_days) - EMPTY_DAY_LIMIT
    if hidden > 0:
        out.append(f"  and {hidden} more day(s) with a slot going empty")

    plan = report.recommended
    if report.adds_left == 0:
        out.append("  no adds left this period, so there is nothing to plan today")
    elif not plan:
        out.append(
            f"  nothing clears the bar ({report.hurdle:.2f} categories, or an empty day "
            f"filled); {report.pool_size} free agents were weighed"
        )
    elif len(plan) == 1:
        out.append(f"  worth a look: {_side(plan[0], report.today)}")
        out.append(f"    {_worth(plan[0].judgement)}")
    else:
        out.append("  worth a look, in this order:")
        for rank, move in enumerate(plan, start=1):
            out.append(f"  {rank}. {_side(move, report.today)}")
            out.append(f"    {_worth(move.judgement)}")
    return out


def week_plan(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    on: date,
) -> list[str]:
    """The week section for `on`, as indented lines. Never raises.

    The recommender is the one part of the digest that can fail on rows the
    listener has not written yet, and the digest must go out anyway (the
    module docstring). So every failure becomes one line: a missing schedule
    and a `today` in no matchup period name themselves, and anything else is
    named by its type rather than its message, since an exception's text can
    carry a query and a connection string.
    """
    season = int(league_season.season)
    try:
        calendar = season_calendar(session, season)
        if calendar is None:
            return [f"  no NBA schedule stored for {season}, so no plan today"]
        report = stream_recommendations(
            session, league_season, espn_team_id, calendar.scoring_period_on(on)
        )
        opponent = _team_names(session, league_season).get(report.opponent_team_id or -1)
    except ValueError as error:
        return [f"  no plan today: {error}"]
    except Exception as error:  # The digest goes out regardless.
        return [f"  no plan today: the week could not be built ({type(error).__name__})"]
    return _week_lines(report, opponent)


def _standing(roster: Sequence[PlayerStatusSnapshot]) -> tuple[list[str], int]:
    """Who on the roster carries a status worth knowing, and how many do not."""
    lines: list[str] = []
    healthy = 0
    for snapshot in sorted(roster, key=lambda s: (_concern_rank(s), s.player.name)):
        status = (snapshot.injury_status or "").upper()
        if status not in _CONCERNING:
            healthy += 1
            continue
        back = snapshot.expected_return_date
        suffix = f", back {back:%d %b}" if back else ""
        lines.append(f"{status:<12} {snapshot.player.name}{suffix}")
    return lines, healthy


def _concern_rank(snapshot: PlayerStatusSnapshot) -> int:
    status = (snapshot.injury_status or "").upper()
    return _CONCERNING.index(status) if status in _CONCERNING else len(_CONCERNING)


def build_digest(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
) -> Digest:
    """The morning message for one team. Reads only; marking is the caller's.

    `since` reads the events observed after it rather than the unnotified
    ones: a member's digest (docs/jobs.md), which marks nothing.

    An event lands in the roster section when the player's latest snapshot
    puts him on the tracked team, whatever his status was when it fired, and
    on the wire when he is unrostered now. An event on a rival's roster is
    neither, and stays unreported.

    The week section is built for the calendar day of `now`, and says so in
    one line when it cannot be (`week_plan`). This is the morning message;
    `build_alert`, which is what the later passes send, has no plan in it,
    because an add is not what a player being ruled out at 22:30 calls for.
    """
    generated_at = now or datetime.now(UTC)
    snapshots = latest_snapshots(session, league_season.season)
    team_names = _team_names(session, league_season)
    team = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id, Team.espn_team_id == espn_team_id
        )
    )

    mine = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.on_team_id == espn_team_id
    }
    wire = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.status in UNROSTERED_STATUSES
    }

    roster_events: list[Line] = []
    wire_events: list[Line] = []
    reported: list[int] = []
    for event in unnotified(
        session, league_season.season, sorted({*ROSTER_KINDS, *WIRE_KINDS}), since=since
    ):
        line = Line(event.kind, event.player.name, describe(event, team_names))
        if event.player_id in mine and event.kind in ROSTER_KINDS:
            roster_events.append(line)
        elif event.player_id in wire and event.kind in WIRE_KINDS:
            wire_events.append(line)
        else:
            continue
        reported.append(event.id)

    standing, healthy = _standing([snapshots[player_id] for player_id in mine])
    return Digest(
        season=league_season.season,
        team_name=team.name if team is not None else f"team {espn_team_id}",
        generated_at=generated_at,
        roster=roster_events[:ROSTER_EVENT_LIMIT],
        roster_extra=max(0, len(roster_events) - ROSTER_EVENT_LIMIT),
        standing=standing,
        healthy=healthy,
        wire=wire_events[:WIRE_EVENT_LIMIT],
        wire_extra=max(0, len(wire_events) - WIRE_EVENT_LIMIT),
        plan=week_plan(session, league_season, espn_team_id, on=generated_at.date()),
        adds_recently=adds_in_window(
            session,
            league_season,
            team.id if team is not None else 0,
            now=generated_at,
            days=CHURN_DAYS,
        ),
        event_ids=reported,
    )


def build_alert(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    since: datetime | None = None,
) -> tuple[str, list[int]] | None:
    """A one-liner for an urgent change to the tracked roster, or None.

    What runs after the passes that are not the morning one: a player of
    yours ruled out at 22:30 is worth knowing before the lineup locks, and
    everything else can wait for tomorrow's digest.
    """
    snapshots = latest_snapshots(session, league_season.season)
    mine = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.on_team_id == espn_team_id
    }
    team_names = _team_names(session, league_season)
    found = [
        event
        for event in unnotified(session, league_season.season, URGENT_KINDS, since=since)
        if event.player_id in mine
    ]
    if not found:
        return None
    lines = [f"{event.player.name}: {describe(event, team_names)}" for event in found]
    return "\n".join(lines), [event.id for event in found]


def mark_notified(session: Session, event_ids: Sequence[int], at: datetime) -> int:
    """Record that these events went out. Called only after delivery succeeded."""
    if not event_ids:
        return 0
    found = session.scalars(
        select(PlayerStatusEvent).where(PlayerStatusEvent.id.in_(list(event_ids)))
    ).all()
    for event in found:
        event.notified_at = at
    return len(found)


# ---------------------------------------------------------------------------
# the league section: free, for every member (step 4)
# ---------------------------------------------------------------------------

#: Matchups listed before the rest are counted; a sixteen-team league has eight.
LEAGUE_MATCHUP_LIMIT = 8
#: The window the wire's traffic is counted over.
LEAGUE_MOVES_HOURS = 24


def league_section(session: Session, league_season: LeagueSeason, *, now: datetime) -> list[str]:
    """THE LEAGUE: this period's matchups as they stand, and the wire's
    traffic in the last day. From the league's own rows, so it is right for
    any league the ingest reads. Never raises: a missing schedule or a day
    in no period is one line."""
    out = ["THE LEAGUE"]
    season = int(league_season.season)
    try:
        calendar = season_calendar(session, season)
        period = (
            period_for_day(session, league_season, calendar.scoring_period_on(now.date()))
            if calendar is not None
            else None
        )
    except Exception as error:  # The digest goes out regardless.
        return [*out, f"  the week could not be read ({type(error).__name__})"]
    if period is None:
        out.append("  no matchup period in play today")
    else:
        out.append(
            f"  period {period.period}, days {period.first_scoring_period}"
            f"-{period.final_scoring_period}"
        )
        names = {team.id: team.name for team in league_season.teams}
        rows = sorted(period.matchups, key=lambda m: m.id)
        for matchup in rows[:LEAGUE_MATCHUP_LIMIT]:
            home = names.get(matchup.home_team_id, "?")
            if matchup.away_team_id is None:
                out.append(f"  {home} on a bye")
                continue
            away = names.get(matchup.away_team_id, "?")
            tied = f", {matchup.categories_tied} level" if matchup.categories_tied else ""
            out.append(
                f"  {home} {matchup.home_categories_won}-{matchup.home_categories_lost}"
                f" {away}{tied}"
            )
        if len(rows) > LEAGUE_MATCHUP_LIMIT:
            out.append(f"  and {len(rows) - LEAGUE_MATCHUP_LIMIT} more")
    # Both ends of the day, as `adds_in_window` has both ends of its
    # fortnight: open at the far end this counted every move the league went
    # on to make, and a replayed morning said 609 moves on the wire in the
    # last day where nine had been made.
    since = now - timedelta(hours=LEAGUE_MOVES_HOURS)
    moves = (
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.league_season_id == league_season.id,
                Transaction.type.in_(WIRE_TYPES),
                Transaction.status == "EXECUTED",
                Transaction.processed_at >= since,
                Transaction.processed_at <= now,
            )
        )
        or 0
    )
    out.append(f"  {_plural(moves, 'move')} on the wire in the last day")
    return out


def team_section_without_listener(
    session: Session, league_season: LeagueSeason, espn_team_id: int, *, now: datetime
) -> list[str]:
    """A team's part of the digest in a league the listener does not follow.

    The roster and wire news come from the listener's status snapshots, which
    record one league's view of who holds whom (docs/jobs.md, "One listener
    league"), so a team in any other league gets the parts that read its own
    league's rows: this week's plan and its churn.
    """
    team = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id, Team.espn_team_id == espn_team_id
        )
    )
    name = team.name if team is not None else f"team {espn_team_id}"
    adds = adds_in_window(
        session, league_season, team.id if team is not None else 0, now=now, days=CHURN_DAYS
    )
    return [
        f"{name} - {now:%a %d %b}, {now:%H:%M} UTC - season {league_season.season}",
        "",
        "THIS WEEK",
        *week_plan(session, league_season, espn_team_id, on=now.date()),
        "",
        "CHURN",
        f"  {_plural(adds, 'add')} in the last {CHURN_DAYS} days.",
    ]
