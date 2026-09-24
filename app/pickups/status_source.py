"""Where a morning's injury status comes from, in a declared order.

THE DECLARED SOURCE ORDER

Declared 2026-09-24 in `docs/replay_status.md` before any calibration was
re-run, and not tuned afterwards.

1. **ESPN's own snapshot, when the listener had taken one by that morning.**
   `player_status_snapshots` is the listener's, and the listener only runs for
   the season in progress, so this is the live season's answer and it is
   unchanged: the newest snapshot per player, ESPN's status as today.
2. **Otherwise the NBA's official report, as of that morning**
   (`app.injuries.status_as_of`, read at `morning_of` -- ten o'clock Eastern,
   which is the read that sees the league's nine o'clock report; the hour is
   `app.injuries`' own and the reasoning is in `docs/injuries.md`).
3. **Otherwise silence, which is read as healthy** -- by the caller, and only
   by the caller. `status_as_of` returning None means the league said nothing
   current, not that the man is fit, and the league only ever names players
   whose team plays that day (`docs/injuries.md`, the last caution). A
   rostered man the reports never place keeps a null status, which is what
   this engine has always given every man on a replayed day.

WHICH SNAPSHOT COUNTS AS "BY THAT MORNING"

The gate is that the season holds **a snapshot observed at or before that
day's morning**, not that the season holds a snapshot at all. Both halves of
that matter.

On a live morning it changes nothing: the listener's last pass is always
older than ten o'clock Eastern today, so the gate opens and the *newest*
snapshot per player is used, tilt and all, exactly as before.

On a replayed day of the live season it is the difference between a leak and
a read. A page asked for `?today=52` in March would otherwise have read the
listener's April snapshot -- next month's status, on a day in March. The
gate shuts, the day falls through to the league's own report for that
morning, and nothing the engine reads was published after ten o'clock
Eastern on the day it is being asked about. That is the fifth place a
look-ahead could have hidden (`docs/inseason_rehearsal.md`).

THE MAPPING

The league prints exactly five words and the engine speaks ESPN's, so the
five are mapped across and the engine's `RULED_OUT_STATUSES` decides what it
always decided:

| the league's | the engine's | ruled out today? |
|---|---|---|
| `Out` | `OUT` | yes |
| `Doubtful` | `DOUBTFUL` | no |
| `Questionable` | `QUESTIONABLE` | no |
| `Probable` | `PROBABLE` | no |
| `Available` | `ACTIVE` | no |
| silence | null | no |

So of the league's five, **`Out` alone** is ruled out, which is the line
`app.inseason.startable` already drew on ESPN's words and is not moved here.
Widening it to `Doubtful` -- the league's own word for about a one-in-four
chance, measured at a 5.88% play rate in `docs/availability.md` §1 -- is the
availability term's decision and belongs to its own declaration.

`SUSPENSION` is not produced. The league has no such status: a suspension is
an `Out` whose *reason* reads `League Suspension`, and mapping on the reason
would change nothing, because `OUT` and `SUSPENSION` are both in
`RULED_OUT_STATUSES`. The mapping reads the status word alone, so it can be
stated in full in six rows and checked in one test.

**No return date comes from a report.** `docs/availability.md` §2 searched
the first line of all 6,987 Out runs for a timeline word or a printed date
and found none, so a report-sourced status carries `expected_return_date`
None and an OUT man is priced by the return prior of `app.pickups.returns`
rather than by a date. That is the path this whole job exists to make fire.

COST

One query for the day's report lines and one for the snapshot gate, both
memoized per `(season, date)` on the session, because a replay walks the same
morning once per team and once per wire. A day's morning is of the order of
eighty status lines, so the whole day is held rather than filtered per
roster.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import InjuryReport, PlayerStatusSnapshot
from app.injuries import morning_of
from app.injury_reports import NBA_OFFICIAL
from app.listener.snapshots import latest_snapshots

#: What the `source` of a read says. `NBA_OFFICIAL` is `app.injury_reports`'.
ESPN = "espn"
#: Neither source could answer: the listener never ran and the league filed
#: nothing this day's morning could see.
NO_SOURCE = "none"

#: The league's five words in the engine's vocabulary, which is ESPN's. See
#: the module docstring for why there is no `SUSPENSION` row.
REPORT_TO_ENGINE: Mapping[str, str] = {
    "Out": "OUT",
    "Doubtful": "DOUBTFUL",
    "Questionable": "QUESTIONABLE",
    "Probable": "PROBABLE",
    "Available": "ACTIVE",
}

#: Where the memo lives, so a replay's ninety-odd calls a morning cost two
#: queries between them. Keyed on the session, which is what a test's
#: rollback throws away.
_MEMO = "pickups_status_source"


@dataclass(frozen=True)
class StatusRead:
    """One morning's statuses, and where they came from.

    `statuses` maps a player id to (the engine's status word, the return
    date). A player absent from it has no status, which every caller reads as
    "nothing is known against him" -- the same thing a replayed day has always
    said about everybody.
    """

    #: `ESPN`, `NBA_OFFICIAL` or `NO_SOURCE`.
    source: str
    #: The moment the read was bounded by: ten o'clock Eastern that morning.
    #: None when the day has no date, which is a season with no schedule.
    read_as_of: datetime | None
    #: The newest report this read could see, for a report-sourced day.
    reported_at: datetime | None
    statuses: Mapping[int, tuple[str | None, date | None]]
    #: Report lines with a status that morning which are placed on a player.
    placed: int
    #: Distinct names the league printed that morning which no `players` row
    #: could be found for, so nothing here knows anything about them and they
    #: read as healthy. See `docs/injuries.md`, "Matching a name to a player":
    #: nearly all are G-League two-way men who were never in ESPN's pool.
    unmatched: int

    def of(self, player_id: int) -> tuple[str | None, date | None]:
        """This man's status and return date, or (None, None)."""
        return self.statuses.get(int(player_id), (None, None))

    def named(self, player_ids: Iterable[int]) -> int:
        """How many of these men the morning's source had a status for."""
        return sum(1 for player_id in player_ids if int(player_id) in self.statuses)

    def as_provenance(self) -> dict[str, Any]:
        """The block a report carries, so a page can name the source."""
        return {
            "used": self.source,
            "read_as_of": self.read_as_of.isoformat() if self.read_as_of else None,
            "reported_at": self.reported_at.isoformat() if self.reported_at else None,
            "placed": self.placed,
            "unmatched": self.unmatched,
        }


_EMPTY = StatusRead(
    source=NO_SOURCE,
    read_as_of=None,
    reported_at=None,
    statuses={},
    placed=0,
    unmatched=0,
)


def read_statuses(session: Session, season: int, on: date | None) -> StatusRead:
    """The declared source order, answered for one morning of one season.

    `on` is the calendar date of the scoring period being asked about; None
    when the season has no schedule to date it by, and then ESPN's snapshots
    are the only possible answer.
    """
    memo: dict[tuple[int, date | None], StatusRead] = session.info.setdefault(_MEMO, {})
    key = (int(season), on)
    if key not in memo:
        memo[key] = _read(session, int(season), on)
    return memo[key]


def _read(session: Session, season: int, on: date | None) -> StatusRead:
    if on is None:
        return (
            _from_snapshots(session, season, None) if _listener_had_run(session, season) else _EMPTY
        )
    at = morning_of(on)
    if _listener_had_run(session, season, at):
        return _from_snapshots(session, season, at)
    return _from_reports(session, on, at)


def _listener_had_run(session: Session, season: int, at: datetime | None = None) -> bool:
    """Whether a snapshot of this season had been taken by `at`.

    With no moment to bound by -- a season with no schedule, so no replay is
    possible either -- any snapshot counts.
    """
    query = select(PlayerStatusSnapshot.id).where(PlayerStatusSnapshot.season == season)
    if at is not None:
        query = query.where(PlayerStatusSnapshot.observed_at <= at)
    return session.scalar(query.limit(1)) is not None


def _from_snapshots(session: Session, season: int, at: datetime | None) -> StatusRead:
    """ESPN's status as today: the newest snapshot per player, unchanged.

    Deliberately the newest and not the newest *by `at`*. The gate above has
    already established that this is a live morning, and on a live morning the
    freshest status is the one the manager is owed -- the listener's afternoon
    pass is the whole point of running it.
    """
    rows = latest_snapshots(session, season)
    return StatusRead(
        source=ESPN,
        read_as_of=at,
        reported_at=None,
        statuses={
            int(player_id): (row.injury_status, row.expected_return_date)
            for player_id, row in rows.items()
        },
        placed=len(rows),
        unmatched=0,
    )


def _from_reports(session: Session, on: date, at: datetime) -> StatusRead:
    """The league's own report for that morning, the whole day in one query.

    The same bound `app.injuries.status_as_of` applies one player at a time --
    published by `at`, about `on` or a later game -- with the newest report
    winning and the nearer game breaking a tie on the instant, which is what
    `statuses_as_of` does for a named roster. Read for every player at once
    because a replayed morning is asked about by fourteen teams and a wire.
    """
    rows = session.execute(
        select(
            InjuryReport.player_id,
            InjuryReport.player_name_raw,
            InjuryReport.status,
            InjuryReport.reported_at,
            InjuryReport.game_date,
        )
        .where(
            InjuryReport.source == NBA_OFFICIAL,
            InjuryReport.reported_at <= at,
            InjuryReport.game_date >= on,
            InjuryReport.status.is_not(None),
        )
        .order_by(InjuryReport.reported_at.desc(), InjuryReport.game_date.asc())
    ).all()
    statuses: dict[int, tuple[str | None, date | None]] = {}
    missed: set[str] = set()
    newest: datetime | None = None
    for player_id, raw, status, reported_at, _game_date in rows:
        if newest is None or reported_at > newest:
            newest = reported_at
        if player_id is None:
            missed.add(str(raw))
            continue
        # The rows arrive newest first, so the first line about a man is his.
        statuses.setdefault(int(player_id), (REPORT_TO_ENGINE.get(str(status)), None))
    return StatusRead(
        source=NBA_OFFICIAL if rows else NO_SOURCE,
        read_as_of=at,
        reported_at=newest,
        statuses=statuses,
        placed=len(statuses),
        unmatched=len(missed),
    )


def report_lines_on(session: Session, on: date) -> int:
    """Status lines the league published that day, whatever the morning saw.

    For a doc's coverage table, not for a read: the read is bounded by the
    morning and this is not.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(InjuryReport)
            .where(
                InjuryReport.source == NBA_OFFICIAL,
                InjuryReport.game_date == on,
                InjuryReport.status.is_not(None),
            )
        )
        or 0
    )
