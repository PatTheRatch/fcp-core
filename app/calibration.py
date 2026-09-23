"""Every measured number this code leans on, per league, with its provenance.

docs/intake.md is the whole story. Until 2026-09-22 each of these was a
constant in the module that used it, measured once on Full Court Press (ESPN
3853870, eight seasons) and typed in by hand. The product is meant for other
leagues, and a number measured on one league is not a fact about another. So
the numbers moved into `league_calibrations`, one row per (league, key), and
every place that used to read a constant now asks here.

THE SIX KEYS

| key | what it is | the constant it replaces |
|---|---|---|
| `typical_pickup` | what one executed add returns | `replacement.TYPICAL_PICKUP` |
| `opened_place` | what a place left open and streamed returns | `replacement.OPENED_PLACE` |
| `stream_hurdle` | the bar a week's move clears | `stream.STREAM_HURDLE` |
| `season_hurdle_paid` | the bar a season move costing FAAB clears | `season.SEASON_HURDLE_PAID` |
| `season_hurdle_free` | the same for a free add into an open place | `season.SEASON_HURDLE_FREE` |
| `trade_record` | the trade number's record, and its sentence | `trades.calibration.PUBLISHED` |

`trade_record` has no single number: its `value` is null and its `payload`
holds the 2x2. Every other key's `value` is categories a week.

WHERE A NUMBER COMES FROM, IN ORDER

1. **`owner`** -- the league's own manager set it on the account page, with
   one line of reason. A re-measurement never overwrites it: a bar is a
   choice about how much churn a manager wants, and the sweep only ever
   recommends one.
2. **`measured`** -- the intake chain measured it on this league's own
   history (docs/intake.md), and the sample clears this key's `minimum`.
   Below the minimum the row is kept (the page shows it) but not used, and
   the fallback carries on: a hurdle fitted on forty decision points is not
   a measurement, it is a coincidence.
3. **`pooled`** -- the n-weighted aggregate of every league measured so far
   whose settings match this one's (`settings_key`), over at least
   `POOL_LEAGUES` leagues. Aggregates only: a pooled row is built from other
   leagues' *numbers*, never from their rosters, names or transactions.
4. **`default`** -- `DEFAULTS` below: the constants as they stood on
   2026-09-22, each with the document that measured them in its comment.

The order is the point. A manager's own choice beats a measurement of his
league; a measurement of his league beats other leagues like his; other
leagues like his beat one league that is not his at all. Every step says so
out loud in `Calibrated.note`, which the pages print the way they print a
projection's `source_note`: a bar labels, it never hides.

THE MINIMUMS

Each key's `minimum` is read off the measurement that produces it, and is
written down in docs/intake.md beside this table:

* `typical_pickup`, 100 adds -- the median over a season's executed adds.
  This league runs 475 to 973 a season; a hundred is the point below which
  the median moves more than the number it is measuring.
* `opened_place`, 100 team-periods -- docs/streaming_lane.md pooled 1,536.
* the three hurdles, 200 decision points -- docs/pickups_backtest.md swept
  616 (14 teams x 44), and the tuning rule turns on a no-move *rate*, which
  needs a few hundred decisions before it means anything.
* `trade_record`, 20 deals -- docs/trades.md section 7 measured 55, and
  reported that 55 is already a coin toss's worth of evidence. Below twenty
  there is nothing to say at all.

POOLED ROWS AND THEIR SETTINGS

What moves these numbers is the shape of the league: how many teams share
one wire, how many places each holds, how many adds a period allows, whether
the wire costs FAAB, and which categories are scored. So the pool is keyed on
exactly those (`settings_key`), and leagues of different shapes are never
averaged together.

**No relationship is fitted across settings, and none may be until there are
at least `FIT_LEAGUES` leagues.** With three or four leagues a regression of
the hurdle on the team count is a line through noise. The pool is a table by
settings, and it stays a table.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import LeagueCalibration, LeagueSeason, LeagueSeasonCategory
from app.trades.calibration import CALIBRATION_NOTE, PUBLISHED

TYPICAL_PICKUP = "typical_pickup"
OPENED_PLACE = "opened_place"
STREAM_HURDLE = "stream_hurdle"
SEASON_HURDLE_PAID = "season_hurdle_paid"
SEASON_HURDLE_FREE = "season_hurdle_free"
TRADE_RECORD = "trade_record"

#: Every key, in the order a page lists them: the two measurements of the
#: wire, the three bars, the trade record.
KEYS = (
    TYPICAL_PICKUP,
    OPENED_PLACE,
    STREAM_HURDLE,
    SEASON_HURDLE_PAID,
    SEASON_HURDLE_FREE,
    TRADE_RECORD,
)

#: The three a manager may set himself. The other three are measurements of
#: what happened, not choices about what to do, and nothing on the account
#: page offers to overrule them.
OWNER_SETTABLE = (STREAM_HURDLE, SEASON_HURDLE_PAID, SEASON_HURDLE_FREE)

OWNER = "owner"
MEASURED = "measured"
POOLED = "pooled"
DEFAULT = "default"
SOURCES = (OWNER, MEASURED, POOLED, DEFAULT)

#: Leagues a pooled row needs before it is written at all. Two, because one
#: league's measurement pooled with nothing is that league's measurement
#: wearing a different hat, and this league would be told "the pool of 1
#: league like yours", which says nothing.
POOL_LEAGUES = 2

#: Leagues before anyone may fit a relationship *across* settings -- the
#: hurdle against the team count, say -- rather than keeping a table of
#: them. Ten. Below that a fitted line is noise with a slope, and the code
#: that would fit it is deliberately not written.
FIT_LEAGUES = 10


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# the defaults: the constants as they stood, with what measured them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Default:
    """One key's fallback: the number, what it rests on, and its minimum."""

    #: Categories a week, or None for `trade_record`, which is a table.
    value: float | None
    #: The sample the default rests on, in this key's own unit.
    n: int
    #: What that unit is, for a page and for the intake's email.
    unit: str
    #: The one sentence a page prints under the number.
    note: str
    #: The sample a measurement of another league needs before it is used
    #: instead of this (see the module docstring).
    minimum: int
    #: What the number is, in the words the account page uses.
    title: str


#: The numbers as `app/scoring/replacement.py`, `app/pickups/stream.py`,
#: `app/pickups/season.py` and `app/trades/calibration.py` held them on
#: 2026-09-22, every one of them measured on Full Court Press alone. Moving
#: them here changed no value: `migrations/versions/0026_league_calibrations.py`
#: seeds that league with exactly these, so its pages print what they printed.
DEFAULTS: Mapping[str, Default] = {
    TYPICAL_PICKUP: Default(
        value=0.06,
        n=924,
        unit="adds",
        note=(
            "the default, measured on another league: what one waiver pickup "
            "returned there, over the 924 adds of its leanest recent season"
        ),
        minimum=100,
        title="What a pickup is worth",
    ),
    OPENED_PLACE: Default(
        value=0.38,
        n=1536,
        unit="team-periods",
        note=(
            "the default, measured on another league: what a roster place left "
            "open and streamed returned there, over 1,536 team-periods"
        ),
        minimum=100,
        title="What an open place is worth",
    ),
    STREAM_HURDLE: Default(
        value=0.20,
        n=616,
        unit="decision points",
        note=(
            "the default bar, chosen on another league's backtest; yours is "
            "measured once it has enough history"
        ),
        minimum=200,
        title="The bar for a move this week",
    ),
    SEASON_HURDLE_PAID: Default(
        value=0.20,
        n=616,
        unit="decision points",
        note=(
            "the default bar, chosen on another league's backtest; yours is "
            "measured once it has enough history"
        ),
        minimum=200,
        title="The bar for a move that costs FAAB",
    ),
    SEASON_HURDLE_FREE: Default(
        value=0.10,
        n=616,
        unit="decision points",
        note=(
            "the default bar, chosen on another league's backtest; yours is "
            "measured once it has enough history"
        ),
        minimum=200,
        title="The bar for a free add",
    ),
    TRADE_RECORD: Default(
        value=None,
        n=55,
        unit="deals",
        # The published sentence itself, not a shorter one about it. It is
        # what every trade page printed before these numbers became rows, it
        # is what `trade_note` rebuilds from the run behind it, and a league
        # with no trades of its own to replay is owed the real record of the
        # number it is being shown rather than a summary of one.
        note=CALIBRATION_NOTE,
        minimum=20,
        title="The trade number's record",
    ),
}


def minimum(key: str) -> int:
    return DEFAULTS[key].minimum


def unit(key: str) -> str:
    return DEFAULTS[key].unit


# ---------------------------------------------------------------------------
# what a caller gets back
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calibrated:
    """One number, and everything a page needs to say where it came from."""

    key: str
    #: Categories a week; None only for `trade_record`.
    value: float | None
    #: One of `SOURCES`.
    source: str
    #: The sample it rests on, in this key's `unit`.
    n: int
    #: The sentence printed under it.
    note: str
    #: Whatever the measurement produced: the sweep grid, the IQR, the 2x2.
    payload: dict[str, Any]
    #: When the run that produced it finished, or None for a default.
    measured_at: datetime | None = None

    @property
    def number(self) -> float:
        """The value, for a caller that knows this key has one."""
        if self.value is None:
            raise ValueError(f"{self.key} has no single number; read its payload")
        return float(self.value)

    def describe(self) -> str:
        """ "0.20 a week -- measured on this league, 616 decision points"."""
        head = "-" if self.value is None else f"{self.value:.2f}"
        return f"{head} — {self.note}"


#: What the default's payload carries. Only `trade_record` has one: the 2x2
#: the note is written from, so a page showing the record can show the table
#: under it whether or not this league has been measured.
_DEFAULT_PAYLOAD: Mapping[str, dict[str, Any]] = {
    TRADE_RECORD: {
        "cells": [
            {
                "headline": cell.headline,
                "horizon": cell.horizon,
                "picked": cell.picked,
                "spearman": cell.spearman,
                "mean_error": cell.mean_error,
                "yardstick": cell.yardstick,
            }
            for cell in PUBLISHED
        ]
    }
}


def _default(key: str) -> Calibrated:
    fallback = DEFAULTS[key]
    return Calibrated(
        key=key,
        value=fallback.value,
        source=DEFAULT,
        n=fallback.n,
        note=fallback.note,
        payload=dict(_DEFAULT_PAYLOAD.get(key, {})),
    )


def _row_out(row: LeagueCalibration) -> Calibrated:
    return Calibrated(
        key=row.key,
        value=None if row.value is None else float(row.value),
        source=row.source,
        n=int(row.n or 0),
        note=row.note or "",
        payload=dict(row.payload or {}),
        measured_at=row.measured_at,
    )


# ---------------------------------------------------------------------------
# the shape of a league, which is what a pooled row is keyed on
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeagueSettings:
    """The settings that move these numbers, and nothing else.

    Not the league's name, not its members, not its id: a pooled row is
    grouped on this and carries only this, so a league learning from the pool
    learns the shape of the leagues in it and nothing about them.
    """

    team_count: int
    #: Starting places, bench and injured reserve together.
    roster_size: int
    #: Adds a matchup period allows per day (`ADDS_PER_PERIOD_DAY`).
    adds_per_day: int
    uses_faab: bool
    #: ESPN stat ids of the scored categories, sorted.
    categories: tuple[int, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "team_count": self.team_count,
            "roster_size": self.roster_size,
            "adds_per_day": self.adds_per_day,
            "uses_faab": self.uses_faab,
            "categories": list(self.categories),
        }

    @property
    def digest(self) -> str:
        """A stable string to group on, so two shapes never share a row."""
        return json.dumps(self.as_json(), sort_keys=True, separators=(",", ":"))


def newest_season(session: Session, league_id: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .where(LeagueSeason.league_id == league_id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )


def settings_key(session: Session, league_id: int) -> LeagueSettings | None:
    """This league's shape, from its newest stored season, or None when it
    has no season stored and so has no shape to match on yet."""
    from app.pickups.state import ADDS_PER_PERIOD_DAY

    season = newest_season(session, league_id)
    if season is None:
        return None
    places = sum(int(count) for count in (season.lineup_slots or {}).values())
    categories = tuple(
        sorted(
            int(stat_id)
            for stat_id in session.scalars(
                select(LeagueSeasonCategory.stat_id).where(
                    LeagueSeasonCategory.league_season_id == season.id
                )
            ).all()
        )
    )
    return LeagueSettings(
        team_count=int(season.team_count),
        roster_size=places + int(season.bench_slots or 0) + int(season.injured_reserve_slots or 0),
        adds_per_day=ADDS_PER_PERIOD_DAY,
        uses_faab=bool(season.uses_faab),
        categories=categories,
    )


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def stored(session: Session, league_id: int | None, key: str) -> LeagueCalibration | None:
    """The one row for this (league, key), pooled rows being `league_id` null."""
    where = (
        LeagueCalibration.league_id.is_(None)
        if league_id is None
        else LeagueCalibration.league_id == league_id
    )
    return session.scalar(select(LeagueCalibration).where(where, LeagueCalibration.key == key))


def _pooled(session: Session, league_id: int, key: str) -> Calibrated | None:
    """The pooled row for this league's settings, when there is one that
    clears the minimum. A league with no season stored matches nothing."""
    shape = settings_key(session, league_id)
    if shape is None:
        return None
    for row in session.scalars(
        select(LeagueCalibration).where(
            LeagueCalibration.league_id.is_(None),
            LeagueCalibration.key == key,
            LeagueCalibration.source == POOLED,
        )
    ).all():
        if str((row.payload or {}).get("settings_digest")) != shape.digest:
            continue
        if int(row.n or 0) < minimum(key):
            return None
        return _row_out(row)
    return None


def calibration(session: Session, league_id: int | None, key: str) -> Calibrated:
    """This league's number for `key`, and where it came from.

    The fallback order is the module docstring's: owner, then a measurement
    of this league that clears the key's minimum, then the pool of leagues
    shaped like it, then the default. Never raises: there is always an
    answer, and the answer always says what it is.
    """
    if key not in DEFAULTS:
        raise ValueError(f"unknown calibration key {key!r}")
    if league_id is None:
        return _default(key)
    row = stored(session, league_id, key)
    if row is not None and row.source == OWNER:
        return _row_out(row)
    if row is not None and row.source == MEASURED and int(row.n or 0) >= minimum(key):
        return _row_out(row)
    from_pool = _pooled(session, league_id, key)
    if from_pool is not None:
        return from_pool
    return _default(key)


@dataclass(frozen=True)
class Bars:
    """A league's six numbers, resolved once for a page, a report or a job.

    A report reads four of them and a trade page the fifth; resolving them
    one at a time would be five round trips per request, and worse, two
    halves of one answer could be resolved a moment apart.
    """

    typical_pickup: Calibrated
    opened_place: Calibrated
    stream_hurdle: Calibrated
    season_hurdle_paid: Calibrated
    season_hurdle_free: Calibrated
    trade_record: Calibrated

    def of(self, key: str) -> Calibrated:
        return getattr(self, key)  # type: ignore[no-any-return]

    def all(self) -> list[Calibrated]:
        return [self.of(key) for key in KEYS]


def bars(session: Session, league_id: int | None) -> Bars:
    """Every key for one league, resolved together."""
    found = {key: calibration(session, league_id, key) for key in KEYS}
    return Bars(**found)


def default_bars() -> Bars:
    """The defaults alone, for a caller with no league in hand (a test, a
    unit of the recommender exercised on a fixture)."""
    return Bars(**{key: _default(key) for key in KEYS})


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def write(
    session: Session,
    league_id: int | None,
    key: str,
    *,
    value: float | None,
    source: str,
    n: int,
    note: str,
    payload: Mapping[str, Any] | None = None,
    run_seconds: float | None = None,
    measured_at: datetime | None = None,
) -> Calibrated | None:
    """Store one row, replacing whatever was there. Does not commit.

    A `measured` or `pooled` write over an `owner` row does nothing and
    returns None: the manager's choice stands until he changes it himself.
    An `owner` write replaces anything.
    """
    if key not in DEFAULTS:
        raise ValueError(f"unknown calibration key {key!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown calibration source {source!r}")
    existing = stored(session, league_id, key)
    if existing is not None and existing.source == OWNER and source != OWNER:
        return None
    body = dict(payload or {})
    values = {
        "league_id": league_id,
        "key": key,
        "value": value,
        "payload": body,
        "n": int(n),
        "measured_at": measured_at or now(),
        "run_seconds": run_seconds,
        "source": source,
        "note": note,
    }
    statement = insert(LeagueCalibration).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[LeagueCalibration.league_id, LeagueCalibration.key],
            set_={name: values[name] for name in values if name not in ("league_id", "key")},
        )
        if league_id is not None
        else statement.on_conflict_do_update(
            index_elements=[LeagueCalibration.key],
            index_where=LeagueCalibration.league_id.is_(None),
            set_={name: values[name] for name in values if name not in ("league_id", "key")},
        )
    )
    session.flush()
    written = stored(session, league_id, key)
    return _row_out(written) if written is not None else None


def set_by_owner(
    session: Session,
    league_id: int,
    key: str,
    *,
    value: float,
    reason: str,
    on: datetime | None = None,
) -> Calibrated:
    """The league's manager choosing a bar himself, with his one line of why.

    The note is built here rather than taken from him whole, so every note on
    the page reads the same way and his reason is quoted rather than obeyed.
    """
    if key not in OWNER_SETTABLE:
        raise ValueError(f"{key} is a measurement, not a choice")
    at = on or now()
    said = reason.strip()
    note = f"your choice, {at:%Y-%m-%d}" + (f": {said}" if said else "")
    written = write(
        session,
        league_id,
        key,
        value=value,
        source=OWNER,
        n=0,
        note=note,
        payload={"reason": said},
        measured_at=at,
    )
    assert written is not None  # an owner write is never refused
    return written


def forget_owner(session: Session, league_id: int, key: str) -> bool:
    """Drop the manager's own row, so the measurement (or the pool, or the
    default) is used again. True when there was one."""
    row = stored(session, league_id, key)
    if row is None or row.source != OWNER:
        return False
    session.delete(row)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# the pool
# ---------------------------------------------------------------------------


def _weighted(rows: Sequence[LeagueCalibration]) -> float | None:
    """The n-weighted mean of the rows that carry a number."""
    pairs = [
        (float(row.value), int(row.n))
        for row in rows
        if row.value is not None and int(row.n or 0) > 0
    ]
    if not pairs:
        return None
    weight = sum(n for _, n in pairs)
    return sum(value * n for value, n in pairs) / weight


def recompute_pool(session: Session, *, at: datetime | None = None) -> list[Calibrated]:
    """Rebuild every pooled row from the leagues measured so far.

    One row per (key, settings shape) over at least `POOL_LEAGUES` leagues
    whose own measurement cleared the key's minimum. Reads `n`, `value` and
    the settings digest off the measured rows and nothing else: no roster, no
    name, no transaction of any league goes into a pooled row, and none is
    read to build one.

    Stale shapes are deleted, so a league that re-measures into a different
    shape does not leave a pool nobody belongs to. Does not commit.
    """
    when = at or now()
    measured = session.scalars(
        select(LeagueCalibration).where(
            LeagueCalibration.league_id.is_not(None), LeagueCalibration.source == MEASURED
        )
    ).all()
    groups: dict[tuple[str, str], list[LeagueCalibration]] = {}
    for row in measured:
        shape = str((row.payload or {}).get("settings_digest") or "")
        if not shape or int(row.n or 0) < minimum(row.key):
            continue
        groups.setdefault((row.key, shape), []).append(row)

    written: list[Calibrated] = []
    kept: set[tuple[str, str]] = set()
    for (key, shape), rows in sorted(groups.items()):
        leagues = {int(row.league_id) for row in rows if row.league_id is not None}
        if len(leagues) < POOL_LEAGUES:
            continue
        kept.add((key, shape))
        total = sum(int(row.n or 0) for row in rows)
        settings = next((dict((row.payload or {}).get("settings") or {}) for row in rows), {})
        payload: dict[str, Any] = {
            "settings": settings,
            "settings_digest": shape,
            "leagues": len(leagues),
            # No fitted relationship across settings, by design, until there
            # are `FIT_LEAGUES` of them (the module docstring).
            "fitted": False,
        }
        if key == TRADE_RECORD:
            deals = sum(int((row.payload or {}).get("deals") or 0) for row in rows)
            picked = sum(int((row.payload or {}).get("picked") or 0) for row in rows)
            payload |= {"deals": deals, "picked": picked}
            value = None
        else:
            value = _weighted(rows)
        note = pooled_note(len(leagues))
        found = write(
            session,
            None,
            key,
            value=value,
            source=POOLED,
            n=total,
            note=note,
            payload=payload,
            measured_at=when,
        )
        if found is not None:
            written.append(found)

    for row in session.scalars(
        select(LeagueCalibration).where(
            LeagueCalibration.league_id.is_(None), LeagueCalibration.source == POOLED
        )
    ).all():
        if (row.key, str((row.payload or {}).get("settings_digest") or "")) not in kept:
            session.delete(row)
    session.flush()
    return written


def pooled_note(leagues: int) -> str:
    return f"the pool of {leagues} league{'' if leagues == 1 else 's'} like yours"


def measured_note(key: str, n: int) -> str:
    """What a measurement of this league says under the number."""
    counted = f"{n:,} {unit(key)}"
    if n < minimum(key):
        return (
            f"measured on this league, but only {counted}: too little to trust, "
            f"so it is not being used yet"
        )
    return f"measured on this league, {counted}"


# ---------------------------------------------------------------------------
# what the account page lists
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Listed:
    """One key as the account page shows it: what is used, and what is stored.

    The two differ when a manager has set a bar (his row is used and the
    measurement is still there under it), and when a measurement has not
    cleared its minimum (the row is there and is not used).
    """

    key: str
    title: str
    unit: str
    minimum: int
    used: Calibrated
    #: This league's own measurement, whatever is being used.
    measured: Calibrated | None
    #: The manager's own row, when he has set one.
    owner: Calibrated | None


def listing(session: Session, league_id: int) -> list[Listed]:
    rows = {
        row.key: row
        for row in session.scalars(
            select(LeagueCalibration).where(LeagueCalibration.league_id == league_id)
        ).all()
    }
    out: list[Listed] = []
    for key in KEYS:
        row = rows.get(key)
        own = _row_out(row) if row is not None and row.source == OWNER else None
        mine = _row_out(row) if row is not None and row.source == MEASURED else None
        out.append(
            Listed(
                key=key,
                title=DEFAULTS[key].title,
                unit=unit(key),
                minimum=minimum(key),
                used=calibration(session, league_id, key),
                measured=mine,
                owner=own,
            )
        )
    return out


def leagues_measured(session: Session, key: str) -> int:
    """How many leagues have a usable measurement of this key, for a page
    that wants to say how big the pool could be."""
    rows = session.execute(
        select(LeagueCalibration.league_id, LeagueCalibration.n).where(
            LeagueCalibration.league_id.is_not(None),
            LeagueCalibration.key == key,
            LeagueCalibration.source == MEASURED,
            func.coalesce(LeagueCalibration.n, 0) >= minimum(key),
        )
    ).all()
    return len({int(league_id) for league_id, _ in rows})
