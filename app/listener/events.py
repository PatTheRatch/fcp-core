"""What changed between two consecutive observations of one player.

Pure functions over two `Observation`s, or over a minutes series, so every
rule is testable with constructed rows and none touches ESPN or the
database. The kinds and rules are the table in docs/pickups.md section 3.5;
the thresholds are starting values, to be tuned once a season of events
exists, and each is a named constant here for that reason.

A first observation produces nothing: with no previous state there is no
change to report, only a baseline.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from statistics import fmean
from typing import Any

from app.listener.pool import ON_TEAM, UNROSTERED_STATUSES, WAIVERS, PoolEntry

WENT_OUT = "went_out"
DOWNGRADED = "downgraded"
UPGRADED = "upgraded"
RETURNED = "returned"
RETURN_DATE_CHANGED = "return_date_changed"
CHANGED_PRO_TEAM = "changed_pro_team"
DROPPED = "dropped"
CLAIMED = "claimed"
WAIVER_CLEARING = "waiver_clearing"
OWNERSHIP_SURGE = "ownership_surge"
OWNERSHIP_SLIDE = "ownership_slide"
MINUTES_SPIKE = "minutes_spike"
MINUTES_DROP = "minutes_drop"

KINDS = (
    WENT_OUT,
    DOWNGRADED,
    UPGRADED,
    RETURNED,
    RETURN_DATE_CHANGED,
    CHANGED_PRO_TEAM,
    DROPPED,
    CLAIMED,
    WAIVER_CLEARING,
    OWNERSHIP_SURGE,
    OWNERSHIP_SLIDE,
    MINUTES_SPIKE,
    MINUTES_DROP,
)

ACTIVE = "ACTIVE"
OUT = "OUT"
SUSPENSION = "SUSPENSION"

#: Worse to better. OUT and a suspension are the same thing to a lineup;
#: QUESTIONABLE and DAY_TO_DAY are both "maybe" and rank level, so a move
#: between them is no event. Anything ESPN invents later ranks as unknown
#: and takes part in no ordering rule.
_SEVERITY = {
    OUT: 0,
    SUSPENSION: 0,
    "DOUBTFUL": 1,
    "QUESTIONABLE": 2,
    "DAY_TO_DAY": 2,
    "PROBABLE": 3,
    ACTIVE: 4,
}
_OUT_STATUSES = (OUT, SUSPENSION)
_HEALTHY = (ACTIVE, "PROBABLE")
_DOUBTFUL = ("QUESTIONABLE", "DOUBTFUL", "DAY_TO_DAY")

#: A 24-hour ownership move this large across all ESPN leagues is the crowd
#: reacting to news. Fires once as the move crosses the line, not on every
#: pass it stays above it.
OWNERSHIP_CHANGE_THRESHOLD = 5.0
#: Crossing a quarter of leagues upward is a player becoming worth a look.
OWNERSHIP_OWNED_LINE = 25.0

#: The classic pickup: a backup whose minutes jumped because the starter is
#: out. Mean minutes over the last few games against the games before.
MINUTES_SHIFT = 8.0
MINUTES_RECENT_GAMES = 3
MINUTES_PRIOR_GAMES = 10
#: Fewer games than this in either window says nothing about a role.
MINUTES_MIN_GAMES = 3


@dataclass(frozen=True)
class Observation:
    """A player's state at one pass, from a stored snapshot or a fresh entry."""

    injury_status: str | None = None
    injured: bool = False
    expected_return_date: date | None = None
    pro_team_id: int | None = None
    on_team_id: int | None = None
    status: str | None = None
    percent_owned: float | None = None
    percent_change: float | None = None
    percent_started: float | None = None
    auction_value_average: float | None = None
    #: Only a fresh entry knows this; a stored status snapshot does not.
    waiver_clears_at: datetime | None = None

    @classmethod
    def from_entry(cls, entry: PoolEntry) -> "Observation":
        return cls(
            injury_status=entry.injury_status,
            injured=entry.injured,
            expected_return_date=entry.expected_return_date,
            pro_team_id=entry.pro_team_id,
            on_team_id=entry.on_team_id,
            status=entry.status,
            percent_owned=entry.percent_owned,
            percent_change=entry.percent_change,
            percent_started=entry.percent_started,
            auction_value_average=entry.auction_value_average,
            waiver_clears_at=entry.waiver_clears_at,
        )

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "Observation":
        """From a `PlayerStatusSnapshot` row, or anything carrying its columns."""
        return cls(
            injury_status=snapshot.injury_status,
            injured=bool(snapshot.injured),
            expected_return_date=snapshot.expected_return_date,
            pro_team_id=snapshot.pro_team_id,
            on_team_id=snapshot.on_team_id,
            status=snapshot.status,
            percent_owned=snapshot.percent_owned,
            percent_change=snapshot.percent_change,
            percent_started=snapshot.percent_started,
            auction_value_average=snapshot.auction_value_average,
        )


@dataclass(frozen=True)
class Event:
    kind: str
    #: The fields that changed, before and after, JSON-ready.
    previous: dict[str, Any]
    current: dict[str, Any]
    #: Whatever the rule computed on the way.
    detail: dict[str, Any]


def _json_ready(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _fields(observation: Observation, *names: str) -> dict[str, Any]:
    everything = asdict(observation)
    return {name: _json_ready(everything[name]) for name in names}


def _changed(
    kind: str, before: Observation, after: Observation, *names: str, **detail: Any
) -> Event:
    return Event(
        kind=kind, previous=_fields(before, *names), current=_fields(after, *names), detail=detail
    )


def _severity(status: str | None) -> int | None:
    return _SEVERITY.get(status) if status else None


def diff(
    previous: Observation | None,
    current: Observation,
    *,
    observed_at: datetime,
    next_pass_at: datetime | None = None,
) -> list[Event]:
    """Every event the move from `previous` to `current` produces.

    `next_pass_at` is when the listener expects to look again; a waiver
    clearing before then is reported now, since there will be no later
    chance. Rules are independent, so one pass can produce several events
    for one player (a drop and a slide, say). A first observation, with no
    previous, produces none.
    """
    if previous is None:
        return []
    events: list[Event] = []
    events.extend(_injury_events(previous, current))
    events.extend(_roster_events(previous, current, observed_at, next_pass_at))
    events.extend(_ownership_events(previous, current))
    return events


def _injury_events(before: Observation, after: Observation) -> list[Event]:
    events: list[Event] = []
    was, now = before.injury_status, after.injury_status
    was_rank, now_rank = _severity(was), _severity(now)

    if now in _OUT_STATUSES and was not in _OUT_STATUSES:
        events.append(_changed(WENT_OUT, before, after, "injury_status", "expected_return_date"))
    elif was in _HEALTHY and now in _DOUBTFUL:
        events.append(_changed(DOWNGRADED, before, after, "injury_status"))
    elif (
        was_rank is not None
        and now_rank is not None
        and was_rank <= _SEVERITY["QUESTIONABLE"]
        and now_rank > was_rank
        and now != ACTIVE
    ):
        events.append(_changed(UPGRADED, before, after, "injury_status", "expected_return_date"))
    elif now == ACTIVE and was is not None and was != ACTIVE:
        events.append(_changed(RETURNED, before, after, "injury_status"))

    was_date, now_date = before.expected_return_date, after.expected_return_date
    if was_date is not None and now_date is not None and was_date != now_date:
        events.append(
            _changed(
                RETURN_DATE_CHANGED,
                before,
                after,
                "expected_return_date",
                days=(now_date - was_date).days,
            )
        )

    if before.pro_team_id and after.pro_team_id and before.pro_team_id != after.pro_team_id:
        events.append(_changed(CHANGED_PRO_TEAM, before, after, "pro_team_id"))
    return events


def _roster_events(
    before: Observation,
    after: Observation,
    observed_at: datetime,
    next_pass_at: datetime | None,
) -> list[Event]:
    events: list[Event] = []
    if before.status == ON_TEAM and after.status in UNROSTERED_STATUSES:
        events.append(
            _changed(DROPPED, before, after, "status", "on_team_id", from_team_id=before.on_team_id)
        )
    elif before.status in UNROSTERED_STATUSES and after.status == ON_TEAM:
        events.append(
            _changed(CLAIMED, before, after, "status", "on_team_id", to_team_id=after.on_team_id)
        )

    clears = after.waiver_clears_at
    if (
        after.status == WAIVERS
        and clears is not None
        and next_pass_at is not None
        and observed_at < clears <= next_pass_at
    ):
        events.append(
            _changed(WAIVER_CLEARING, before, after, "status", clears_at=clears.isoformat())
        )
    return events


def _ownership_events(before: Observation, after: Observation) -> list[Event]:
    events: list[Event] = []
    change = after.percent_change
    was_change = before.percent_change
    owned, was_owned = after.percent_owned, before.percent_owned

    surged = (
        change is not None
        and change >= OWNERSHIP_CHANGE_THRESHOLD
        and (was_change is None or was_change < OWNERSHIP_CHANGE_THRESHOLD)
    )
    crossed = (
        owned is not None and was_owned is not None and was_owned < OWNERSHIP_OWNED_LINE <= owned
    )
    if surged or crossed:
        events.append(
            _changed(
                OWNERSHIP_SURGE,
                before,
                after,
                "percent_owned",
                "percent_change",
                crossed_line=crossed,
            )
        )

    slid = (
        change is not None
        and change <= -OWNERSHIP_CHANGE_THRESHOLD
        and (was_change is None or was_change > -OWNERSHIP_CHANGE_THRESHOLD)
    )
    if slid:
        events.append(_changed(OWNERSHIP_SLIDE, before, after, "percent_owned", "percent_change"))
    return events


def minutes_events(games: Sequence[tuple[int, float]]) -> list[Event]:
    """A minutes spike or drop from a player's played games, oldest first.

    `games` is (scoring period, minutes) for games actually played; days
    the team played without him are not games and must not be passed. The
    last `MINUTES_RECENT_GAMES` are compared with up to `MINUTES_PRIOR_GAMES`
    before them. Fewer than `MINUTES_MIN_GAMES` in either window is a
    refusal, not a zero: three games say something about a role and one
    does not.

    The detail names the last game the comparison ran through, so a pass
    can tell a spike it has already recorded from a new one.
    """
    ordered = sorted(games)
    recent = ordered[-MINUTES_RECENT_GAMES:]
    prior = ordered[-(MINUTES_RECENT_GAMES + MINUTES_PRIOR_GAMES) : -MINUTES_RECENT_GAMES]
    if len(recent) < MINUTES_MIN_GAMES or len(prior) < MINUTES_MIN_GAMES:
        return []
    recent_mean = fmean(minutes for _, minutes in recent)
    prior_mean = fmean(minutes for _, minutes in prior)
    detail = {
        "recent_mean": round(recent_mean, 1),
        "prior_mean": round(prior_mean, 1),
        "recent_games": len(recent),
        "prior_games": len(prior),
        "through_scoring_period": recent[-1][0],
    }
    if recent_mean >= prior_mean + MINUTES_SHIFT:
        return [Event(MINUTES_SPIKE, {}, {}, detail)]
    if recent_mean <= prior_mean - MINUTES_SHIFT:
        return [Event(MINUTES_DROP, {}, {}, detail)]
    return []
