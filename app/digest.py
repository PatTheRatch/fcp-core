"""The morning digest: what changed, for the one team being tracked.

Layer 3 of docs/pickups.md, phase 1b. Plain text, built from stored rows
only: no ESPN request, no recommendation. Two sections and two lines:

1. The tracked roster, from the listener's events (went out, returned, a
   moved return date, a minutes drop), then where that roster stands now.
2. The wire, from events on unrostered players worth a look (a minutes
   spike, an ownership surge, dropped by a rival, waivers clearing).
3. Churn, the team's adds in the last fortnight, because this league's own
   history says the heavier movers returned less per move
   (docs/acquirable_value.md, r = -0.63 between add volume and return).

Sections 3 and 4 of the design note, the streaming and rest-of-season
advice, need the recommender and are not here. Nor is a value rank for a
free agent: percent owned stands in until phase 2 can price him.

Every event the digest reports on, including the ones it summarises as "and
N more", is marked `notified_at` by the caller once delivery has actually
succeeded. Kinds the digest never shows are never queried and never marked.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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

#: Line budgets. The whole message stays under forty lines, which is what
#: makes it readable on a phone; anything past a cap is counted, not listed,
#: and the events route has the rest.
ROSTER_EVENT_LIMIT = 8
WIRE_EVENT_LIMIT = 10
#: A roster with seven players carrying a status at once is exceptional, and
#: the count line below still says how many there are.
STATUS_LINE_LIMIT = 6
MAX_LINES = 40

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
    session: Session, season: int, kinds_wanted: Sequence[str]
) -> list[PlayerStatusEvent]:
    return list(
        session.scalars(
            select(PlayerStatusEvent)
            .where(
                PlayerStatusEvent.season == season,
                PlayerStatusEvent.notified_at.is_(None),
                PlayerStatusEvent.kind.in_(kinds_wanted),
            )
            .order_by(PlayerStatusEvent.observed_at.desc(), PlayerStatusEvent.id.desc())
        ).all()
    )


def adds_in_window(
    session: Session, league_season: LeagueSeason, team_id: int, *, now: datetime, days: int
) -> int:
    """Executed wire adds by this team in the trailing window, the volume guard."""
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
                TransactionItem.item_type == "ADD",
            )
        )
        or 0
    )


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
) -> Digest:
    """The morning message for one team. Reads only; marking is the caller's.

    An event lands in the roster section when the player's latest snapshot
    puts him on the tracked team, whatever his status was when it fired, and
    on the wire when he is unrostered now. An event on a rival's roster is
    neither, and stays unreported.
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
    for event in unnotified(session, league_season.season, sorted({*ROSTER_KINDS, *WIRE_KINDS})):
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
        for event in unnotified(session, league_season.season, URGENT_KINDS)
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
