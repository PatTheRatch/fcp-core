#!/usr/bin/env python3
"""When is a stash worth it? Eight seasons of men held while they were out.

Usage:
    python scripts/stashes.py                 # full run, every table
    python scripts/stashes.py --season 2026   # one season only
    python scripts/stashes.py --why           # the headline's accounting

WHAT THIS MEASURES

The owner, 2026-09-23: *"When is a stash worth it? Levels to the stash -- what
happens when a player doesn't play for a week, two weeks, three weeks. Also the
record of the team that is stashing. Then the what-if would need an estimated
return date so it knows it's a stash, and the usual minutes ramp-up when he
comes back."*

His own 2026 claim of Brandon Miller -- day 23, $2, while the man had not
played since day 11 -- is a move the product cannot value at all.
`app.pickups.state.playable_days` gives an Out man zero games until ESPN's
`expected_return_date` and **zero games forever when there is none**, and this
database holds no `expected_return_date` on any row of any season. So the
engine's projection for every stash is exactly zero, and section 7 measures how
big that zero is against what happened. Nothing here is wired; it feeds a
what-if "stash mode" later.

DECLARED DEFINITIONS

**A scoring period is a calendar day.** Verified on the stored dates: 2026's
period 12 is 2025-11-01 and period 33 is 2025-11-22, twenty-one periods and
twenty-one days apart. Every "day" below is a scoring period and every "week"
is seven of them, so "days out" is a real count of days and not a count of
games.

**Out, by the box score.** `player_game_stats` carries one row per man per game
his NBA team played, `played` true when he was on the floor and false when he
was not; a man's row set is his own team's schedule and follows him through a
trade. Measured over the 2,467 player-seasons with twenty or more rows, 97.9%
of 2026's rows fall on the schedule of the pro team his rows best match and
96.3% of that team's game days carry a row for him (§0c prints every season;
2020 is the bubble and reads 78.0%). So a **missed team game** is a
`played = false` row, and that is the whole definition -- no team attribution,
no `pro_team_games` join.

**Days out at a decision on day D** = D minus the latest day before D on which
he has a `played = true` row. When he has no played row before D at all, the
first day of his own first out run less one stands in for it, and the count is
flagged. The decision must fall while he is out: the latest row before D must
be `played = false`.

**A STASH** is a decision taken while he was `STASH_DAYS` (8) or more days out.
1-7 days out is the control -- a man who missed a night or two. The brief's
other phrasing, "seven or more consecutive team game days", is a different
count and §1b reports the overlap between the two rather than choosing.

**Two populations.**
*Claimed stashes*: an executed add (`transactions` of type WAIVER or FREEAGENT,
status EXECUTED, carrying an ADD item -- `app.scoring.replacement.ADD_TYPES`)
made on a day the man was 8+ days out.
*Held stashes*: a man already on a roster who began an absence of 8+ total days
while that team held him. The outcome is KEPT (held unbroken from the day he
went out through his return) or DROPPED (let go during the absence). The
decision day is the day he went out, and the level is the **total** days the
absence ran, because a manager holding a man decides again every morning.

**Levels.** `LEVELS`: 1-7 (the control), 8-14, 15-28, 29+. For a claim the
level is days out at the claim; for a held man it is the absence's total days.

**Cost.** Dead weeks x `OPENED_PLACE` (0.38 categories a week -- what a roster
place returns when it is left open and streamed, `docs/streaming_lane.md`).
Dead days run from the decision to the first of his return, his drop and the
season's end. **This league has `injured_reserve_slots` = 0** -- there is not
one `IR` row in `daily_lineup_slots` in eight seasons -- so every stash pays
the full place. §5 states the formula for a league with IR, where the dead cost
is about zero while he sits in one. Beside the flat 0.38, §5 prints the
*actual* streamed return that team got from its own open places that season,
from `scripts/streaming_lane.py`'s `TeamPeriod.streamed_per_place_value`.

**Benefit.** For each regular matchup period after his return in which the team
still held him, his realised value that week through
`scripts/pickups_backtest.py`'s `Replay`: the roster of each day is re-seated
with him **taken out**, and the categories the team loses is what he was worth
(`-Replay.delta(team, window, first, last, [player], [])`, the same call and
sign `scripts/keepers.py` uses for the man a claim dropped). Against it stands
the wire's replacement level on the **decision** day, the same `WireBook` rule
keepers uses, pro-rated by the period's length.

**Net.** `net = sum(weekly value - replacement) over the weeks held after
return - 0.38 x dead weeks`. Positive means the stash paid.

**Healthy value.** His own per-game line over his last `PRE_INJURY_GAMES` (10)
played games before the decision, scaled to a week by the season's measured
games a week, through the same lens. `HEALTHY_TIERS` cuts it.

**Return.** His next `played = true` row. The prior it is calibrated against is
`docs/availability.md` table 2 -- the report-based return curve measured on
2022-2026. §2a measures the same curve from the box scores, **with the
suspended `COVID_SEASON` left out**, and that is the number the engine should
read; the eight-season version is printed beside it, because an absence that
ran into March 2020 never ends in a box score and 2020's 927 spells carry a
quarter of the whole census's never-returned share.

**The ramp.** Minutes and value over games 1-5, 6-10 and 11-20 after return, as
a share of his last-10 pre-injury norm (`RAMP_BANDS`). The value ratio is taken
on the band's mean per-game line scaled to a week, and only where the
pre-injury week is worth more than `RAMP_FLOOR`, because a ratio of marginals
near zero is noise.

LIMITATIONS WORTH KNOWING BEFORE THE CODE

* **This database holds the injury reports for 2026 only** -- 20,776 lines,
  game dates 2025-10-21 to 2026-04-12. The brief expected 2022-2026; that is
  the VPS. So the second witness (§1b) is a one-season check.
* **The held population is chosen by managers**, not by us: a held stash is a
  man somebody decided to keep, and §1c's KEPT rows are that selection.
* **The lens and the Replay are two currencies with one name.** `Replay.delta`
  counts categories won in a real matchup; the replacement level and
  `OPENED_PLACE` are marginal values through `Standard.value`. Both are
  "categories a week" and they are not the same measurement.
  `scripts/keepers.py` ships the same pairing and the doc's limitations repeat
  it; do not read a net of 0.3 as a third of a category of the standings.

REPRODUCE. From the worktree root, against whichever database `DATABASE_URL`
names -- the published run was the local Docker Postgres, where the injury
reports are one season deep:

    PYTHONPATH=. ~/fcp-core/.venv/bin/python scripts/stashes.py --why

`.env` cannot be sourced by a shell (its line 29 holds an unquoted value with
angle brackets), so `DATABASE_URL` is read out of it in Python; a worktree with
no `.env` of its own falls back to the main checkout's.

READ-ONLY. Every query is a SELECT; nothing is written anywhere.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this `app` resolves to whichever checkout the interpreter's venv installed,
# which is not this one when a worktree borrows another checkout's .venv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Sibling scripts, not installed modules, so mypy cannot resolve them -- the
# same import `scripts/keepers.py` and `scripts/faab_bids.py` carry. The
# imports are the point: this study reuses the backtest's counterfactual, the
# availability study's reason rule and the lane study's per-place return
# rather than growing second copies of any of them.
import availability as av  # type: ignore[import-not-found]
import pickups_backtest as bt  # type: ignore[import-not-found]
import streaming_lane as lane  # type: ignore[import-not-found]
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft.targets import CategoryDistribution
from app.injuries import morning_of, status_as_of
from app.pickups.bids import free_agents_on
from app.pickups.judge import Standard, standard_lens
from app.pickups.projection import ESPN_AVAILABILITY, per_game_line
from app.pickups.returns import expected_games
from app.scoring.lines import COUNTS, CategoryLine
from app.scoring.replacement import ADD_TYPES, OPENED_PLACE, TYPICAL_PICKUP

#: Days out at the decision (or, for a held man, total days out) at or above
#: which the decision is a STASH. Below it is the control.
STASH_DAYS = 8

#: The levels every table cuts by: (label, first day, last day or None).
LEVELS: tuple[tuple[str, int, int | None], ...] = (
    ("1-7 (control)", 1, 7),
    ("8-14", 8, 14),
    ("15-28", 15, 28),
    ("29+", 29, None),
)

#: Played games a man must have in the season before a *held* absence counts
#: as a rotation player's. `app.scoring.knowables`' own `PRIOR_GAMES`, which is
#: the boundary the product uses for "his own record means something", and the
#: one `docs/availability.md` section 5 uses for the same purpose.
ROTATION_GAMES = 10

#: Played games taken as his pre-injury norm.
PRE_INJURY_GAMES = 10

#: Played games his pre-injury norm must rest on before his healthy value and
#: his ramp are read at all. Below it he sits in the `?` tier and out of the
#: ramp: three October games is not a norm, and Brandon Miller -- five games
#: at twenty minutes before the shoulder -- is the case that forced the gate.
MIN_NORM = 5

#: The ramp's bands: games since return, inclusive.
RAMP_BANDS: tuple[tuple[str, int, int], ...] = (
    ("1-5", 1, 5),
    ("6-10", 6, 10),
    ("11-20", 11, 20),
)

#: A pre-injury week worth less than this is not a denominator: a ratio of
#: marginal values near zero is noise, not a ramp.
RAMP_FLOOR = 0.10

#: Healthy value tiers, categories a week through the standard lens.
HEALTHY_TIERS: tuple[tuple[str, float, float | None], ...] = (
    ("<0.50", -(10.0**6), 0.50),
    ("0.50-1.00", 0.50, 1.00),
    ("1.00-2.00", 1.00, 2.00),
    ("2.00+", 2.00, None),
)

#: Weeks-left buckets at the decision.
WEEKS_LEFT_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1-4", 1, 4),
    ("5-9", 5, 9),
    ("10-15", 10, 15),
    ("16+", 16, None),
)

#: `docs/availability.md` table 2, the report-based return prior: given a man
#: has been Out N days and is still out, the chance he is back within M more
#: days. Measured on 2022-2026's injury reports, 6,987 Out runs, on the VPS.
#: Quoted here as a literal so section 2b can be recomputed from the document
#: without re-reading the reports this database does not hold.
AVAILABILITY_TABLE_2: dict[int, dict[int, float]] = {
    1: {1: 0.0020, 3: 0.2387, 7: 0.4669, 14: 0.6664, 28: 0.8395},
    3: {1: 0.0806, 3: 0.2439, 7: 0.4521, 14: 0.6393, 28: 0.8210},
    7: {1: 0.0786, 3: 0.2159, 7: 0.3882, 14: 0.5871, 28: 0.7752},
    14: {1: 0.0576, 3: 0.1562, 7: 0.3251, 14: 0.5275, 28: 0.7078},
    28: {1: 0.0402, 3: 0.1138, 7: 0.2222, 14: 0.3815, 28: 0.5997},
}

#: The N and M the return curve is reported at, in days.
PRIOR_N: tuple[int, ...] = (1, 3, 7, 14, 28)
PRIOR_M: tuple[int, ...] = (1, 3, 7, 14, 28)

#: Weeks-until-return the inverted break-even table is written over.
BACK_WITHIN: tuple[int, ...] = (1, 2, 3, 4, 6, 8)

#: Weeks-left the inversion searches for the smallest one that pays.
WEEKS_LEFT_SEARCH: tuple[int, ...] = tuple(range(1, 25))

#: Injured-reserve slots in this league. Zero, and not a guess: eight seasons
#: of `daily_lineup_slots` carry no `IR` row at all. Every formula that would
#: divide by it is written out in the document instead.
IR_SLOTS = 0

#: Slots that mean "not on the roster". ESPN writes `BE` for a rostered man out
#: of the lineup, so a held man always has a row.
HELD_SLOTS_EXCLUDED = ("FA", "IR")

#: How many rows the named tables print.
LISTING = 40

#: The player this study's worked example is about.
MILLER = "Brandon Miller"

#: The season the NBA suspended in March 2020. Every man out when the league
#: stopped reads here as an absence that never ended, which is why the
#: 29+ level and the never-returned share are both inflated in that season
#: and why every pooled figure is printed a second time without it
#: (`scripts/streaming_lane.py` flags the same season for the same reason).
COVID_SEASON = 2020


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


def load_env(path: Path) -> dict[str, str]:
    """`.env` parsed, because `set -a && . ./.env` dies on its line 29."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def connect() -> Session:
    """A session against `DATABASE_URL`, from the environment or from `.env`.

    A worktree has no `.env` of its own, so the main checkout's is the
    fallback. Nothing from it is ever printed.
    """
    import os

    url = os.environ.get("DATABASE_URL")
    if not url:
        here = Path(__file__).resolve().parent.parent / ".env"
        main = Path.home() / "fcp-core" / ".env"
        found = here if here.exists() else main
        url = load_env(found)["DATABASE_URL"]
    return make_session_factory(make_engine(url))()


def median_of(values: Sequence[float]) -> float:
    """`statistics.median`, never the half index."""
    return statistics.median(values) if values else 0.0


def mean_of(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def share(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.2f}%" if whole else "n/a"


def two(value: float) -> str:
    """Two decimals, and never `-0.00`: a negative zero is a rounding artefact."""
    text_value = f"{value:.2f}"
    return "0.00" if text_value == "-0.00" else text_value


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """A fixed-width table, so every table in the doc comes off this run."""
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    print("  ".join(h.rjust(w) for h, w in zip(header, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(c.rjust(w) for c, w in zip(row, widths, strict=True)))
    print()


def bucket_of(value: float, buckets: Sequence[tuple[str, float, float | None]]) -> str:
    for label, first, last in buckets:
        if value >= first and (last is None or value < last):
            return label
    return buckets[-1][0]


def level_of(days: int) -> str:
    for label, first, last in LEVELS:
        if days >= first and (last is None or days <= last):
            return label
    return LEVELS[-1][0]


def weeks_left_bucket(weeks: int) -> str:
    for label, first, last in WEEKS_LEFT_BUCKETS:
        if weeks >= first and (last is None or weeks <= last):
            return label
    return WEEKS_LEFT_BUCKETS[0][0]


# ---------------------------------------------------------------------------
# the season's shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calendar:
    """One league season, as the census needs it."""

    year: int
    league_season_id: int
    team_count: int
    #: period number -> (first day, last day, is_playoff)
    periods: dict[int, tuple[int, int, bool]]
    #: NBA games a team played per seven days, measured on `pro_team_games`.
    games_a_week: float

    @property
    def last_day(self) -> int:
        return max(final for _first, final, _playoff in self.periods.values())

    @property
    def first_day(self) -> int:
        return min(first for first, _final, _playoff in self.periods.values())

    def period_of(self, day: int) -> int | None:
        for number, (first, final, _playoff) in self.periods.items():
            if first <= day <= final:
                return number
        return None

    def regular_periods(self) -> tuple[int, ...]:
        return tuple(
            sorted(number for number, (_f, _l, playoff) in self.periods.items() if not playoff)
        )

    def remaining_weeks(self, day: int) -> int:
        """Regular periods with any day left at or after `day`."""
        return sum(1 for number in self.regular_periods() if self.periods[number][1] >= day)


def load_calendars(session: Session) -> dict[int, Calendar]:
    """Every drafted season's shape. 2027 has no played day, so it drops out."""
    schedule: dict[int, tuple[int, int, int]] = {}
    for season, games, teams, span in session.execute(
        text(
            "SELECT season, count(*), count(distinct pro_team_id),"
            " max(scoring_period) - min(scoring_period) + 1"
            " FROM pro_team_games GROUP BY 1"
        )
    ):
        schedule[int(season)] = (int(games), int(teams), int(span))

    out: dict[int, Calendar] = {}
    for ls in session.scalars(select(LeagueSeason).order_by(LeagueSeason.season)):
        raw = session.execute(
            text(
                "SELECT mp.period, mp.first_scoring_period,"
                " mp.final_scoring_period, mp.is_playoff"
                " FROM matchup_periods mp WHERE mp.league_season_id = :lsid"
            ),
            {"lsid": ls.id},
        ).all()
        periods = {
            int(period): (int(first), int(final), bool(playoff))
            for period, first, final, playoff in raw
            if first is not None and final is not None
        }
        if not periods:
            continue
        games, teams, span = schedule.get(int(ls.season), (0, 30, 1))
        out[int(ls.season)] = Calendar(
            year=int(ls.season),
            league_season_id=int(ls.id),
            team_count=int(ls.team_count or 0),
            periods=periods,
            games_a_week=7.0 * games / max(1, teams) / max(1, span),
        )
    return out


def league_row(session: Session, calendar: Calendar) -> LeagueSeason:
    return session.scalars(
        select(LeagueSeason).where(LeagueSeason.id == calendar.league_season_id)
    ).one()


def load_dates(session: Session) -> dict[tuple[int, int], date]:
    """(season, scoring period) -> the Eastern calendar date it fell on.

    Both stored clocks are UTC (`docs/availability.md` limitation 6), so a
    seven o'clock tip is 23:00 UTC and a ten o'clock one 02:00 the next day.
    The modal Eastern date of the games on that period is the day's date, and
    it is what the injury reports are keyed by.
    """
    counted: dict[tuple[int, int], dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for season, day, when in session.execute(
        text("SELECT season, scoring_period, game_at FROM pro_team_games")
    ):
        eastern = av.eastern_date(when)
        counted[(int(season), int(day))][eastern] += 1
    return {key: max(days, key=lambda d: (days[d], d)) for key, days in counted.items()}


# ---------------------------------------------------------------------------
# the box scores, and what "out" means in them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    """One man's row for one game his NBA team played."""

    day: int
    played: bool
    minutes: float
    counts: tuple[float, ...]

    def line(self) -> CategoryLine:
        return CategoryLine(dict(zip(COUNTS, self.counts, strict=True)), 1)


def load_boxes(session: Session) -> dict[tuple[int, int], tuple[Box, ...]]:
    """(season, player) -> his rows, in day order. One query for everything."""
    columns = ", ".join(f"pgs.{column}" for column in COUNTS.values())
    raw: dict[tuple[int, int], list[Box]] = defaultdict(list)
    for row in session.execute(
        text(
            f"SELECT pgs.season, pgs.player_id, pgs.scoring_period, pgs.played,"
            f" pgs.minutes, {columns} FROM player_game_stats pgs"
        )
    ):
        raw[(int(row[0]), int(row[1]))].append(
            Box(
                day=int(row[2]),
                played=bool(row[3]),
                minutes=float(row[4] or 0.0),
                counts=tuple(float(value or 0.0) for value in row[5:]),
            )
        )
    return {key: tuple(sorted(boxes, key=lambda b: b.day)) for key, boxes in raw.items()}


@dataclass(frozen=True)
class Spell:
    """A run of consecutive team game days the man did not play."""

    #: The day of the first team game he missed.
    first: int
    #: The day of the last team game he missed in the run.
    last: int
    #: His next played day, or None when he never came back that season.
    returned: int | None
    #: Team games missed in the run.
    missed: int
    #: The last day he played before it, or `first - 1` when there is none.
    prior_played: int
    #: True when there was no played day before it.
    no_prior: bool
    #: The season it fell in, so the suspended one can be taken out.
    season: int = 0

    @property
    def total_days(self) -> int:
        """Days out: from his last played day to his return, or to `last`."""
        end = self.returned if self.returned is not None else self.last + 1
        return end - self.prior_played


def spells_of(boxes: Sequence[Box], season: int = 0) -> list[Spell]:
    """Every out run in a man's season, from the box scores alone."""
    out: list[Spell] = []
    index = 0
    while index < len(boxes):
        if boxes[index].played:
            index += 1
            continue
        start = index
        while index < len(boxes) and not boxes[index].played:
            index += 1
        stop = index - 1
        prior = boxes[start - 1].day if start > 0 else boxes[start].day - 1
        out.append(
            Spell(
                first=boxes[start].day,
                last=boxes[stop].day,
                returned=boxes[index].day if index < len(boxes) else None,
                missed=stop - start + 1,
                prior_played=prior,
                no_prior=start == 0,
                season=season,
            )
        )
    return out


@dataclass(frozen=True)
class Standing:
    """What his box scores said about him on the morning of day `D`."""

    days_out: int
    missed: int
    returned: int | None
    no_prior: bool


def standing_on(boxes: Sequence[Box], day: int) -> Standing | None:
    """His out standing the morning of `day`, or None when he was not out.

    None means one of three things and the caller counts them apart: he has no
    row before `day` at all, or the last one before it has him playing.
    """
    before = [box for box in boxes if box.day < day]
    if not before or before[-1].played:
        return None
    missed = 0
    for box in reversed(before):
        if box.played:
            break
        missed += 1
    played_before = [box.day for box in before if box.played]
    prior = played_before[-1] if played_before else before[-missed].day - 1
    after = [box.day for box in boxes if box.day >= day and box.played]
    return Standing(
        days_out=day - prior,
        missed=missed,
        returned=after[0] if after else None,
        no_prior=not played_before,
    )


def pre_injury(boxes: Sequence[Box], day: int) -> tuple[CategoryLine, float, int]:
    """His last `PRE_INJURY_GAMES` played games before `day`: line, minutes, n."""
    played = [box for box in boxes if box.day < day and box.played][-PRE_INJURY_GAMES:]
    if not played:
        return CategoryLine(), 0.0, 0
    line = CategoryLine()
    for box in played:
        line = line + box.line()
    return line.scaled(1.0 / len(played)), mean_of([box.minutes for box in played]), len(played)


# ---------------------------------------------------------------------------
# the wire's replacement level on a day, in the currency
# ---------------------------------------------------------------------------


class WireBook:
    """The day's wire, valued in categories a week, built lazily per day.

    `scripts/keepers.py`'s book, reused verbatim in behaviour so the two
    studies' hurdles are one number. The lens is memoized **by period length**
    -- `standard_lens` rebuilds the season's distributions and average team
    line on every call and only reads them, so the answer depends on the
    season and the length of the period the day falls in (seven days mostly,
    six for the opener, fourteen for the All-Star fortnight). Without the
    cache a season costs fifteen minutes and eight seasons two hours.

    Note what a free agent is here: `app.pickups.bids.free_agents_on` requires
    a man to have *played* in the matchup period, so a stashed man is never on
    his own day's wire. That is correct -- the replacement level is what a
    manager could have had instead, playing.
    """

    def __init__(self, session: Session, calendar: Calendar) -> None:
        self.session = session
        self.calendar = calendar
        self.league = league_row(session, calendar)
        self._days: dict[int, dict[int, float]] = {}
        self._replacement: dict[int, float] = {}
        self._lens_by_length: dict[int, Standard] = {}
        self._distributions: Sequence[CategoryDistribution] | None = None

    def _length(self, day: int) -> int:
        period = self.calendar.period_of(day)
        if period is None:
            return 7
        first, final, _playoff = self.calendar.periods[period]
        return final - first + 1

    def lens(self, day: int) -> Standard:
        from app.draft.targets import category_distributions

        length = self._length(day)
        if length not in self._lens_by_length:
            if self._distributions is None:
                self._distributions = category_distributions(self.session, self.league)
            self._lens_by_length[length] = standard_lens(
                self.session, self.league, day, self._distributions
            )
        return self._lens_by_length[length]

    def pool(self, day: int) -> dict[int, float]:
        if day not in self._days:
            lens = self.lens(day)
            values: dict[int, float] = {}
            for player in sorted(free_agents_on(self.session, self.league, day)):
                line = per_game_line(self.session, self.calendar.year, player, day, tilt=False)
                values[player] = lens.value(line)
            self._days[day] = values
        return self._days[day]

    def replacement(self, day: int) -> float:
        """Best man left on the wire that day, floored at `TYPICAL_PICKUP`."""
        if day not in self._replacement:
            self._replacement[day] = max([TYPICAL_PICKUP, *self.pool(day).values()])
        return self._replacement[day]

    def best(self, day: int) -> int | None:
        """*Who* the best man left on the wire that day was.

        The replacement level as a player rather than a number, so the same
        comparison can be made inside the replay and come out in the replay's
        own currency (`score`). None when the wire held nobody.
        """
        pool = self.pool(day)
        if not pool:
            return None
        return max(sorted(pool), key=lambda player: pool[player])

    def weekly(self, day: int, per_game: CategoryLine) -> float:
        """A per-game line, scaled to a week of NBA games, through the lens."""
        if not per_game.counts:
            return 0.0
        return self.lens(day).value(per_game.scaled(self.calendar.games_a_week))


# ---------------------------------------------------------------------------
# the decisions
# ---------------------------------------------------------------------------


@dataclass
class Stash:
    """One decision to hold a man who was not playing, and what came of it."""

    season: int
    kind: str  # "claim" or "hold"
    team: int
    player: int
    #: The day of the decision: the claim day, or the day he went out.
    day: int
    #: Days out at the decision (a claim) or the absence's total (a hold).
    days_out: int
    #: Team games missed at the decision.
    missed: int
    bid: int = 0
    #: The man dropped to make room, when the claim named exactly one.
    dropped: int | None = None
    #: His next played day, or None when he never returned that season.
    returned: int | None = None
    #: The last day this team held him in the run the decision opened.
    held_to: int = 0
    #: True when the run ran to the season's end.
    censored: bool = False
    #: For a hold: whether the team kept him through the absence.
    kept: bool = True
    #: Days the place was dead: decision to return, drop or season end.
    dead_days: int = 0
    #: Post-return weekly values, one per regular period still held.
    weekly: tuple[float, ...] = ()
    #: The wire's replacement on the decision day, categories a week.
    replacement: float = 0.0
    #: Replacement charged over those same weeks, pro-rated by period length.
    replacement_charged: float = 0.0
    #: Who the wire's best man was on the decision day.
    wire_man: int | None = None
    #: The same hold, week by week, with that man in the place instead --
    #: dead weeks included. One currency, no constant. See `score`.
    swap: tuple[float, ...] = ()
    #: His pre-injury week, categories a week through the lens.
    healthy: float = 0.0
    #: His pre-injury minutes a game.
    healthy_minutes: float = 0.0
    #: Played games his pre-injury norm was taken over.
    healthy_games: int = 0
    #: Teams in the league that season, for the standings third.
    teams: int = 0
    #: Regular weeks left in the season at the decision.
    weeks_left: int = 0
    #: The morning's injury-report status and reason class, where stored.
    report_status: str | None = None
    report_reason: str = ""
    #: The team's category record rank on the decision day, 1 = best.
    rank: int = 0
    #: Open roster places the team had on the decision day.
    open_places: int = 0
    #: The team's own streamed return per open place that season.
    streamed: float | None = None
    #: The ramp, filled by `ramp_rows`: games since return -> (minutes, value).
    ramp: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def level(self) -> str:
        return level_of(self.days_out)

    @property
    def stash(self) -> bool:
        return self.days_out >= STASH_DAYS

    @property
    def dead_weeks(self) -> float:
        return self.dead_days / 7.0

    @property
    def dead_cost(self) -> float:
        return OPENED_PLACE * self.dead_weeks

    @property
    def benefit(self) -> float:
        return sum(self.weekly) - self.replacement_charged

    @property
    def net(self) -> float:
        """The brief's declared net: post-return benefit less the dead weeks."""
        return self.benefit - self.dead_cost

    @property
    def net_swap(self) -> float:
        """The same hold in one currency: him against the wire's best man.

        No constant and no second lens. Positive means holding him beat
        putting the wire's best free agent of the decision day in that place
        for the whole hold, dead days and all.
        """
        return sum(self.swap)

    @property
    def weeks_after(self) -> int:
        return len(self.weekly)

    @property
    def paid(self) -> bool:
        return self.net > 0.0

    @property
    def paid_swap(self) -> bool:
        return self.net_swap > 0.0

    @property
    def tier(self) -> str:
        """His healthy-value tier, or `?` when the norm is too thin to trust.

        A man claimed in his season's first fortnight can have two or three
        played games behind him, and three games is not a norm. `MIN_NORM`
        games are required; below it he is in `?` and every tier table says
        how many are there.
        """
        return bucket_of(self.healthy, HEALTHY_TIERS) if self.healthy_games >= MIN_NORM else "?"

    @property
    def days_to_return(self) -> int | None:
        return None if self.returned is None else self.returned - self.day


def load_claims(session: Session) -> list[tuple[int, int, int, int, int, int | None]]:
    """(season, day, team, player, bid, dropped) for every executed add."""
    rows = session.execute(
        text(
            "SELECT ls.season, t.scoring_period, t.team_id, ti.player_id,"
            " coalesce(t.bid_amount, 0), t.id"
            " FROM transactions t"
            " JOIN transaction_items ti ON ti.transaction_id = t.id"
            " JOIN league_seasons ls ON ls.id = t.league_season_id"
            " WHERE t.status = 'EXECUTED' AND t.type = ANY(:kinds)"
            " AND ti.item_type = 'ADD' AND ti.to_team_id IS NOT NULL"
            " ORDER BY ls.season, t.scoring_period, t.id"
        ),
        {"kinds": list(ADD_TYPES)},
    ).all()
    dropped: dict[int, list[int]] = defaultdict(list)
    for tx, player in session.execute(
        text("SELECT transaction_id, player_id FROM transaction_items WHERE item_type = 'DROP'")
    ):
        dropped[int(tx)].append(int(player))
    out = []
    for season, day, team, player, bid, tx in rows:
        men = dropped.get(int(tx), [])
        out.append(
            (
                int(season),
                int(day),
                int(team),
                int(player),
                int(bid),
                men[0] if len(men) == 1 else None,
            )
        )
    return out


def load_held(session: Session) -> dict[tuple[int, int, int], set[int]]:
    """(season, team, player) -> the days that team held him."""
    held: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    for season, team, player, day in session.execute(
        text(
            "SELECT ls.season, dls.team_id, dls.player_id, dls.scoring_period"
            " FROM daily_lineup_slots dls"
            " JOIN teams tt ON tt.id = dls.team_id"
            " JOIN league_seasons ls ON ls.id = tt.league_season_id"
            " WHERE dls.slot <> ALL(:unheld)"
        ),
        {"unheld": list(HELD_SLOTS_EXCLUDED)},
    ):
        held[(int(season), int(team), int(player))].add(int(day))
    return held


def load_roster_room(session: Session) -> tuple[dict[tuple[int, int, int], int], dict[int, int]]:
    """(season, team, day) -> men held, and season -> the modal roster size."""
    counted: dict[tuple[int, int, int], int] = defaultdict(int)
    for season, team, day, held in session.execute(
        text(
            "SELECT ls.season, dls.team_id, dls.scoring_period, count(*)"
            " FROM daily_lineup_slots dls"
            " JOIN teams tt ON tt.id = dls.team_id"
            " JOIN league_seasons ls ON ls.id = tt.league_season_id"
            " WHERE dls.slot <> ALL(:unheld) GROUP BY 1, 2, 3"
        ),
        {"unheld": list(HELD_SLOTS_EXCLUDED)},
    ):
        counted[(int(season), int(team), int(day))] = int(held)
    sizes: dict[int, list[int]] = defaultdict(list)
    for (season, _team, _day), held in counted.items():
        sizes[season].append(held)
    # The MODAL team-day count, not the maximum: one team carrying a
    # fourteenth man for one day would otherwise give every ordinary roster in
    # the league a spare place it never had.
    return counted, {season: statistics.mode(values) for season, values in sizes.items()}


def load_records(session: Session) -> dict[tuple[int, int, int], float]:
    """(season, team row, period) -> categories won in that period.

    ESPN records a result per category per matchup, the way
    `app.pickups.judge.banked_record` counts a record; a tie is half.
    """
    out: dict[tuple[int, int, int], float] = defaultdict(float)
    for season, team, period, result, count in session.execute(
        text(
            "SELECT ls.season, mts.team_id, mp.period, mts.result, count(*)"
            " FROM matchup_team_stats mts"
            " JOIN matchups m ON m.id = mts.matchup_id"
            " JOIN matchup_periods mp ON mp.id = m.matchup_period_id"
            " JOIN league_seasons ls ON ls.id = mp.league_season_id"
            " WHERE mp.is_playoff IS FALSE AND m.away_team_id IS NOT NULL"
            " AND mts.league_season_category_id IS NOT NULL AND mts.result IS NOT NULL"
            " GROUP BY 1, 2, 3, 4"
        )
    ):
        key = (int(season), int(team), int(period))
        if str(result) == "WIN":
            out[key] += float(count)
        elif str(result) == "TIE":
            out[key] += float(count) / 2.0
    return out


def rank_on(
    records: dict[tuple[int, int, int], float],
    calendar: Calendar,
    team: int,
    day: int,
) -> int:
    """The team's rank by categories won in periods finished before `day`."""
    banked: dict[int, float] = defaultdict(float)
    for (season, team_row, period), won in records.items():
        if season != calendar.year:
            continue
        final = calendar.periods.get(period, (0, -1, False))[1]
        if final < day:
            banked[team_row] += won
    if not banked:
        return 0
    order = sorted(banked.items(), key=lambda kv: (-kv[1], kv[0]))
    for index, (team_row, _won) in enumerate(order, start=1):
        if team_row == team:
            return index
    return 0


# ---------------------------------------------------------------------------
# scoring a stash
# ---------------------------------------------------------------------------


def score(
    replay: Any,
    calendar: Calendar,
    wire: WireBook,
    stash: Stash,
) -> None:
    """Fill the dead cost, the post-return weeks and the swap of one stash.

    **Arm one, the brief's declared accounting.** The weekly value is
    `-Replay.delta(team, window, first, last, [him], [])`: the roster of each
    day is re-seated with him taken out, and the categories the team loses
    against the opponent it really faced is what he was worth. Both sides are
    re-solved by the same rule, so none of the credit is credit for setting a
    better lineup (`scripts/pickups_backtest.py`'s `Replay.delta` docstring).
    Against it stands the wire's replacement level on the decision day through
    the lens, pro-rated by the period's length, and 0.38 a dead week.

    **Arm two, the same hold in one currency.** The declared accounting adds a
    replay quantity (categories won in a real matchup) to two lens quantities
    (a marginal value, and `OPENED_PLACE`), and they are not the same
    measurement even though both are called categories a week. So the hold is
    also scored as one swap: `-Replay.delta(team, window, first, last, [him],
    [the wire's best man that day])`, over **every** week of the hold, dead
    weeks included. That number needs no constant and no second lens, and it
    answers the manager's own question -- was holding him better than having
    the best free agent of that morning in the place instead? The two arms are
    printed side by side wherever the net appears.

    The no-move side of every delta is memoized per (team, period, window),
    which is why a whole season of stashes costs seconds rather than minutes.
    """
    end = min(stash.held_to, calendar.last_day)
    stop = end if stash.returned is None else min(stash.returned, end)
    stash.dead_days = max(0, stop - stash.day)
    stash.replacement = wire.replacement(stash.day)
    stash.wire_man = wire.best(stash.day)

    values: list[float] = []
    swapped: list[float] = []
    charged = 0.0
    for number in calendar.regular_periods():
        first_day, final_day, _playoff = calendar.periods[number]
        if final_day < stash.day or first_day > end:
            continue
        window = next((w for w in replay.periods if w.period == number), None)
        if window is None:
            continue
        if stash.wire_man is not None and stash.wire_man != stash.player:
            swapped.append(
                -replay.delta(
                    stash.team,
                    window,
                    max(first_day, stash.day),
                    min(final_day, end),
                    [stash.player],
                    [stash.wire_man],
                )
            )
        if stash.returned is None or final_day < stash.returned:
            continue
        first = max(first_day, stash.returned)
        last = min(final_day, end)
        if first > last:
            continue
        values.append(-replay.delta(stash.team, window, first, last, [stash.player], []))
        charged += stash.replacement * (last - first + 1) / 7.0
    stash.weekly = tuple(values)
    stash.swap = tuple(swapped)
    stash.replacement_charged = charged


def ramp_of(boxes: Sequence[Box], stash: Stash, wire: WireBook) -> dict[str, tuple[float, float]]:
    """Minutes and value in each band after return, as a share of his norm."""
    if stash.returned is None:
        return {}
    pre_line, pre_minutes, pre_n = pre_injury(boxes, stash.day)
    if pre_n < MIN_NORM or pre_minutes <= 0.0:
        return {}
    pre_value = wire.weekly(stash.day, pre_line)
    after = [box for box in boxes if box.day >= stash.returned and box.played]
    out: dict[str, tuple[float, float]] = {}
    for label, first, last in RAMP_BANDS:
        band = after[first - 1 : last]
        if not band:
            continue
        line = CategoryLine()
        for box in band:
            line = line + box.line()
        minutes = mean_of([box.minutes for box in band]) / pre_minutes
        value = (
            wire.weekly(stash.day, line.scaled(1.0 / len(band))) / pre_value
            if pre_value > RAMP_FLOOR
            else float("nan")
        )
        out[label] = (minutes, value)
    return out


# ---------------------------------------------------------------------------
# the return prior, from the box scores
# ---------------------------------------------------------------------------


def return_prior(
    spells: Sequence[Spell],
) -> tuple[dict[int, dict[int, float]], dict[int, int]]:
    """P(back within M more days | out N days and still out).

    Only runs still going at N can be asked, so the denominator shrinks down
    the table -- the same shape as `docs/availability.md` table 2, measured
    from the box scores instead of the reports. The caller decides which
    seasons go in; this study's own curve leaves `COVID_SEASON` out.
    """
    curve: dict[int, dict[int, float]] = {}
    sizes: dict[int, int] = {}
    for n in PRIOR_N:
        still = [s for s in spells if s.total_days > n]
        sizes[n] = len(still)
        row: dict[int, float] = {}
        for m in PRIOR_M:
            back = sum(1 for s in still if s.returned is not None and s.total_days <= n + m)
            row[m] = back / len(still) if still else 0.0
        curve[n] = row
    return curve, sizes


def prior_at(curve: dict[int, dict[int, float]], days_out: int, within_days: int) -> float:
    """The prior read off `curve`, nearest N at or below, nearest M at or below."""
    n = max([key for key in curve if key <= days_out] or [min(curve)])
    row = curve[n]
    keys = [key for key in row if key <= within_days]
    if not keys:
        return 0.0
    return row[max(keys)]


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def build_claims(
    calendar: Calendar,
    boxes: dict[tuple[int, int], tuple[Box, ...]],
    held: dict[tuple[int, int, int], set[int]],
    claims: Sequence[tuple[int, int, int, int, int, int | None]],
) -> tuple[list[Stash], dict[str, int]]:
    """Every executed add of this season that landed on a man who was out."""
    counts = {"adds": 0, "no boxes": 0, "was playing": 0, "out": 0, "no run": 0}
    out: list[Stash] = []
    for season, day, team, player, bid, dropped in claims:
        if season != calendar.year:
            continue
        counts["adds"] += 1
        his = boxes.get((season, player))
        if not his:
            counts["no boxes"] += 1
            continue
        standing = standing_on(his, day)
        if standing is None:
            counts["was playing"] += 1
            continue
        counts["out"] += 1
        days = sorted(held.get((season, team, player), set()))
        run = [d for d in days if d >= day]
        if not run:
            counts["no run"] += 1
            continue
        held_to = run[0]
        for d in run[1:]:
            if d != held_to + 1:
                break
            held_to = d
        out.append(
            Stash(
                season=season,
                kind="claim",
                team=team,
                player=player,
                day=day,
                days_out=standing.days_out,
                missed=standing.missed,
                bid=bid,
                dropped=dropped,
                returned=standing.returned,
                held_to=held_to,
                censored=held_to >= calendar.last_day,
                weeks_left=calendar.remaining_weeks(day),
            )
        )
    return out, counts


def build_holds(
    calendar: Calendar,
    boxes: dict[tuple[int, int], tuple[Box, ...]],
    held: dict[tuple[int, int, int], set[int]],
) -> list[Stash]:
    """Every absence a team met while already holding a rotation player."""
    out: list[Stash] = []
    for (season, team, player), days in held.items():
        if season != calendar.year or not days:
            continue
        his = boxes.get((season, player), ())
        if sum(1 for box in his if box.played) < ROTATION_GAMES:
            continue
        for spell in spells_of(his, season):
            if spell.total_days < STASH_DAYS or spell.no_prior:
                continue
            if spell.first not in days:
                continue
            end = spell.returned if spell.returned is not None else calendar.last_day
            held_to = spell.first
            while held_to + 1 <= calendar.last_day and held_to + 1 in days:
                held_to += 1
            out.append(
                Stash(
                    season=season,
                    kind="hold",
                    team=team,
                    player=player,
                    day=spell.first,
                    days_out=spell.total_days,
                    missed=spell.missed,
                    returned=spell.returned,
                    held_to=held_to,
                    censored=held_to >= calendar.last_day,
                    kept=held_to >= min(end, calendar.last_day),
                    weeks_left=calendar.remaining_weeks(spell.first),
                )
            )
    return out


def attach_reports(
    session: Session, dates: dict[tuple[int, int], date], rows: Sequence[Stash]
) -> int:
    """The morning's injury-report line for each decision, where one exists."""
    found = 0
    for stash in rows:
        when = dates.get((stash.season, stash.day))
        if when is None:
            continue
        line = status_as_of(session, stash.player, morning_of(when))
        if line is None:
            continue
        found += 1
        stash.report_status = line.status
        stash.report_reason = av.reason_class(line.reason)
    return found


def streamed_per_place(session: Session, calendar: Calendar) -> dict[int, float]:
    """team row -> the median categories a week its own open places returned.

    `scripts/streaming_lane.py`'s own measurement, per team-period, reused so
    the actual number stands beside the flat `OPENED_PLACE` charge.
    """
    from app.scoring.season import SeasonBook

    occupancy = [
        occ
        for occ in lane.load_occupancy(session, [calendar.year])
        if int(occ.season) == calendar.year
    ]
    if not occupancy:
        return {}
    book = SeasonBook.load(session, calendar.year, None)
    per_team: dict[int, list[float]] = defaultdict(list)
    for team_period in lane.build_team_periods(calendar.year, occupancy, book):
        if team_period.lane_places(True) <= 0:
            continue
        per_team[int(team_period.team_id)].append(team_period.streamed_per_place_value(True))
    return {team: median_of(values) for team, values in per_team.items()}


def coverage_rows(session: Session) -> list[list[str]]:
    """Do the box rows really trace his team's schedule? The check, per season."""
    schedule: dict[tuple[int, int], set[int]] = defaultdict(set)
    for season, team, day in session.execute(
        text("SELECT season, pro_team_id, scoring_period FROM pro_team_games")
    ):
        schedule[(int(season), int(team))].add(int(day))
    rows: dict[tuple[int, int], set[int]] = defaultdict(set)
    for season, player, day in session.execute(
        text("SELECT season, player_id, scoring_period FROM player_game_stats")
    ):
        rows[(int(season), int(player))].add(int(day))
    by_season: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for (season, _player), days in rows.items():
        if len(days) < 20:
            continue
        teams = [team for (s, team) in schedule if s == season]
        if not teams:
            continue
        best = max(teams, key=lambda team: len(days & schedule[(season, team)]))
        hit = len(days & schedule[(season, best)])
        by_season[season].append((hit / len(days), hit / len(schedule[(season, best)])))
    out = []
    for season in sorted(by_season):
        values = by_season[season]
        out.append(
            [
                str(season),
                str(len(values)),
                two(100.0 * mean_of([a for a, _b in values])),
                two(100.0 * mean_of([b for _a, b in values])),
            ]
        )
    return out


def section_one(
    rows: Sequence[Stash], years: Sequence[int], counts: dict[int, dict[str, int]]
) -> None:
    claims = [s for s in rows if s.kind == "claim"]
    holds = [s for s in rows if s.kind == "hold"]

    print("== 1a. claims that landed on a man who was out, by level ==")
    header = [
        "season",
        "executed adds",
        "on an out man",
        *[label for label, _f, _l in LEVELS],
        "stashes",
        "stash share of all adds",
    ]
    body = []
    for year in years:
        sub = [s for s in claims if s.season == year]
        stashes = sum(1 for s in sub if s.stash)
        body.append(
            [
                str(year),
                str(counts[year]["adds"]),
                str(len(sub)),
                *[str(sum(1 for s in sub if s.level == label)) for label, _f, _l in LEVELS],
                str(stashes),
                share(stashes, counts[year]["adds"]),
            ]
        )
    total_adds = sum(counts[year]["adds"] for year in years)
    pooled_stashes = sum(1 for s in claims if s.stash)
    body.append(
        [
            "pooled",
            str(total_adds),
            str(len(claims)),
            *[str(sum(1 for s in claims if s.level == label)) for label, _f, _l in LEVELS],
            str(pooled_stashes),
            share(pooled_stashes, total_adds),
        ]
    )
    table(header, body)

    print("== 1b. the two counts of 'out', and the report as a second witness ==")
    body = []
    for label, _first, _last in LEVELS:
        sub = [s for s in claims if s.level == label]
        witnessed = [s for s in sub if s.report_status is not None]
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([float(s.missed) for s in sub])),
                share(sum(1 for s in sub if s.missed >= 4), len(sub)),
                str(len(witnessed)),
                share(
                    sum(1 for s in witnessed if s.report_status in ("Out", "Doubtful")),
                    len(witnessed),
                ),
            ]
        )
    table(
        [
            "level",
            "n",
            "med missed games",
            ">=4 missed",
            "report seen",
            "of those Out/Doubtful",
        ],
        body,
    )

    seen = [s for s in claims if s.report_status is not None]
    by_reason: dict[str, list[Stash]] = defaultdict(list)
    for stash in seen:
        by_reason[stash.report_reason].append(stash)
    body = []
    for reason in sorted(by_reason, key=lambda r: -len(by_reason[r])):
        sub = by_reason[reason]
        body.append(
            [
                reason,
                str(len(sub)),
                share(sum(1 for s in sub if s.stash), len(sub)),
                two(median_of([float(s.days_out) for s in sub])),
                share(sum(1 for s in sub if s.returned is not None), len(sub)),
            ]
        )
    table(["reason class", "n", "stash share", "med days out", "returned"], body)

    print("== 1c. held stashes: kept through the absence, or dropped ==")
    header = ["season", "n", "kept", "dropped", *[label for label, _f, _l in LEVELS[1:]]]
    body = []
    for year in years:
        sub = [s for s in holds if s.season == year]
        body.append(
            [
                str(year),
                str(len(sub)),
                share(sum(1 for s in sub if s.kept), len(sub)),
                share(sum(1 for s in sub if not s.kept), len(sub)),
                *[str(sum(1 for s in sub if s.level == label)) for label, _f, _l in LEVELS[1:]],
            ]
        )
    body.append(
        [
            "pooled",
            str(len(holds)),
            share(sum(1 for s in holds if s.kept), len(holds)),
            share(sum(1 for s in holds if not s.kept), len(holds)),
            *[str(sum(1 for s in holds if s.level == label)) for label, _f, _l in LEVELS[1:]],
        ]
    )
    table(header, body)

    body = []
    for label, _first, _last in LEVELS[1:]:
        sub = [s for s in holds if s.level == label]
        body.append(
            [
                label,
                str(len(sub)),
                share(sum(1 for s in sub if s.kept), len(sub)),
                two(median_of([s.net for s in sub if s.kept])),
                share(sum(1 for s in sub if s.kept and s.paid), sum(1 for s in sub if s.kept)),
                two(median_of([s.net_swap for s in sub if s.kept])),
                share(
                    sum(1 for s in sub if s.kept and s.paid_swap),
                    sum(1 for s in sub if s.kept),
                ),
                two(median_of([s.net_swap for s in sub if not s.kept])),
            ]
        )
    table(
        [
            "level (total days out)",
            "n",
            "kept",
            "med net of the kept",
            "kept and paid",
            "med net (swap)",
            "paid (swap)",
            "med swap of the dropped",
        ],
        body,
    )


def section_two(
    rows: Sequence[Stash],
    curve: dict[int, dict[int, float]],
    sizes: dict[int, int],
    spells: Sequence[Spell],
    *,
    all_curve: dict[int, dict[int, float]],
    all_sizes: dict[int, int],
) -> None:
    print("== 2a. the box-score return prior ==")
    print(
        "The top block drops the suspended 2020 season and is the curve this\n"
        "study uses everywhere; the bottom block is all eight seasons, where\n"
        "every man out in March 2020 reads as an absence that never ended.\n"
    )
    for label, this_curve, this_sizes in (
        ("seven seasons, 2020 out", curve, sizes),
        ("all eight seasons", all_curve, all_sizes),
    ):
        body = []
        for n in PRIOR_N:
            body.append(
                [
                    label if n == PRIOR_N[0] else "",
                    str(n),
                    str(this_sizes[n]),
                    *[two(100.0 * this_curve[n][m]) for m in PRIOR_M],
                ]
            )
        table(
            ["population", "N days out", "still out", *[f"in {m}d" for m in PRIOR_M]],
            body,
        )

    print("== 2b. the box-score prior against docs/availability.md table 2 ==")
    body = []
    for n in PRIOR_N:
        for m in PRIOR_M:
            mine = 100.0 * curve[n][m]
            pooled = 100.0 * all_curve[n][m]
            theirs = 100.0 * AVAILABILITY_TABLE_2[n][m]
            body.append([str(n), str(m), two(mine), two(pooled), two(theirs), two(mine - theirs)])
    table(
        ["N days out", "within M", "2020 out", "all eight", "table 2", "gap (pts)"],
        body,
    )

    never = sum(1 for s in spells if s.returned is None)
    pooled_spells = [s for s in spells if s.season != COVID_SEASON]
    print(
        f"spells n={len(spells)}; never returned that season "
        f"{share(never, len(spells))}; median total days out "
        f"{two(median_of([float(s.total_days) for s in spells]))}\n"
        f"without 2020: n={len(pooled_spells)}, never returned "
        f"{share(sum(1 for s in pooled_spells if s.returned is None), len(pooled_spells))}\n"
    )

    print("== 2c. what the stashes themselves did ==")
    body = []
    for label, _first, _last in LEVELS:
        sub = [s for s in rows if s.level == label]
        back = [s for s in sub if s.days_to_return is not None]
        body.append(
            [
                label,
                str(len(sub)),
                share(len(back), len(sub)),
                two(median_of([float(s.days_to_return or 0) for s in back])),
                two(
                    100.0 * sum(1 for s in back if (s.days_to_return or 0) <= 7) / max(1, len(sub))
                ),
                two(
                    100.0 * sum(1 for s in back if (s.days_to_return or 0) <= 14) / max(1, len(sub))
                ),
            ]
        )
    table(
        ["level", "n", "returned", "med days to return", "back in 7d", "back in 14d"],
        body,
    )


def section_three(rows: Sequence[Stash]) -> None:
    print("== 3. the ramp after return ==")

    def ramp_table(title: str, groups: dict[str, list[Stash]]) -> None:
        print(title)
        body = []
        for key in groups:
            sub = groups[key]
            cells = [key, str(len(sub))]
            for label, _first, _last in RAMP_BANDS:
                minutes = [s.ramp[label][0] for s in sub if label in s.ramp]
                values = [
                    s.ramp[label][1]
                    for s in sub
                    if label in s.ramp and s.ramp[label][1] == s.ramp[label][1]
                ]
                cells.append(f"{median_of(minutes):.2f}" if minutes else "-")
                cells.append(f"{median_of(values):.2f}" if values else "-")
            body.append(cells)
        header = ["group", "n"]
        for label, _first, _last in RAMP_BANDS:
            header += [f"mp {label}", f"val {label}"]
        table(header, body)

    ramp_table(
        "by level (share of his last-10 pre-injury norm)",
        {label: [s for s in rows if s.level == label] for label, _f, _l in LEVELS},
    )
    by_reason: dict[str, list[Stash]] = defaultdict(list)
    for stash in rows:
        if stash.report_status is not None:
            by_reason[stash.report_reason].append(stash)
    ramp_table(
        "by reason class (2026, the only season with reports here)",
        dict(sorted(by_reason.items(), key=lambda kv: -len(kv[1]))),
    )


def section_four(rows: Sequence[Stash], curve: dict[int, dict[int, float]]) -> None:
    print("== 4a. the break-even, by level ==")
    body = []
    for label, _first, _last in LEVELS:
        sub = [s for s in rows if s.level == label]
        if not sub:
            continue
        back = [s for s in sub if s.weeks_after]
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([s.dead_weeks for s in sub])),
                two(median_of([s.dead_cost for s in sub])),
                two(median_of([s.benefit for s in sub])),
                two(median_of([s.net for s in sub])),
                share(sum(1 for s in sub if s.paid), len(sub)),
                str(len(back)),
                two(median_of([s.net for s in back])),
                share(sum(1 for s in back if s.paid), len(back)),
                two(median_of([s.net_swap for s in sub])),
                share(sum(1 for s in sub if s.paid_swap), len(sub)),
            ]
        )
    table(
        [
            "level",
            "n",
            "med dead wk",
            "med cost",
            "med benefit",
            "med net",
            "positive",
            "back & held",
            "med net of those",
            "positive",
            "med net (swap)",
            "positive (swap)",
        ],
        body,
    )

    print("== 4b. by his healthy value ==")
    body = []
    for label in [*[tier for tier, _low, _high in HEALTHY_TIERS], "?"]:
        sub = [s for s in rows if s.tier == label]
        if not sub:
            continue
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([s.healthy for s in sub])),
                two(median_of([s.net for s in sub])),
                share(sum(1 for s in sub if s.paid), len(sub)),
                two(
                    median_of(
                        [
                            (sum(s.weekly) - s.replacement_charged) / s.weeks_after
                            for s in sub
                            if s.weeks_after
                        ]
                    )
                ),
                two(median_of([s.net_swap for s in sub])),
                share(sum(1 for s in sub if s.paid_swap), len(sub)),
            ]
        )
    table(
        [
            "healthy tier",
            "n",
            "med healthy",
            "med net",
            "positive",
            "med net a week back",
            "med net (swap)",
            "positive (swap)",
        ],
        body,
    )

    print("== 4c. by weeks left at the decision ==")
    body = []
    for label, _first, _last in WEEKS_LEFT_BUCKETS:
        sub = [s for s in rows if weeks_left_bucket(s.weeks_left) == label]
        if not sub:
            continue
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([s.net for s in sub])),
                share(sum(1 for s in sub if s.paid), len(sub)),
                two(median_of([float(s.weeks_after) for s in sub])),
                two(median_of([s.net_swap for s in sub])),
                share(sum(1 for s in sub if s.paid_swap), len(sub)),
            ]
        )
    table(
        [
            "weeks left",
            "n",
            "med net",
            "positive",
            "med weeks back",
            "med net (swap)",
            "positive (swap)",
        ],
        body,
    )

    print("== 4d. the inversion: a man worth X a week, back within Y, with Z left ==")
    # Stashes only: the control's weeks back are a different animal and the
    # rate the inversion multiplies must be a stash's rate.
    stashes = [s for s in rows if s.stash]
    per_week = weekly_net_by_tier(stashes)
    ramp = ramp_factors(stashes)
    body = []
    for label, _low, _high in HEALTHY_TIERS:
        rate = per_week.get(label)
        cells = [label, "-" if rate is None else two(rate)]
        for weeks in BACK_WITHIN:
            cells.append(break_even(rate, weeks, curve, ramp=ramp))
        body.append(cells)
    table(
        ["worth X a week", "net a week back", *[f"back in {w}w" for w in BACK_WITHIN]],
        body,
    )
    print(
        "Each cell is the smallest number of regular weeks that must be left in\n"
        "the season at the decision for the expected net to be positive, with the\n"
        "return probability from section 2a folded in:\n"
        "  expected = P(back within Y | 8 days out) x sum(ramped weekly net over\n"
        "             Z-Y weeks) - 0.38 x Y.\n"
        f"The ramp applied to the first two weeks back is {ramp[0]:.2f} and "
        f"{ramp[1]:.2f}.\n"
        "'never' means no number of weeks left makes it pay at that rate.\n"
    )
    print("== 4e. the same figures with the suspended 2020 season taken out ==")
    body = []
    for label, sub in (
        ("all eight seasons", list(rows)),
        ("without 2020", [s for s in rows if s.season != COVID_SEASON]),
        ("2020 alone", [s for s in rows if s.season == COVID_SEASON]),
    ):
        if not sub:
            continue
        deep = [s for s in sub if s.level == LEVELS[-1][0]]
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([s.net for s in sub])),
                share(sum(1 for s in sub if s.paid), len(sub)),
                str(len(deep)),
                two(median_of([s.net for s in deep])),
                share(sum(1 for s in deep if s.returned is None), len(deep)),
            ]
        )
    table(
        ["population", "n", "med net", "positive", "29+ n", "29+ med net", "29+ never back"],
        body,
    )


def weekly_net_by_tier(rows: Sequence[Stash]) -> dict[str, float]:
    """Median realised net a week after return, by healthy-value tier."""
    out: dict[str, float] = {}
    for label, _low, _high in HEALTHY_TIERS:
        values = [
            (sum(s.weekly) - s.replacement_charged) / s.weeks_after
            for s in rows
            if s.tier == label and s.weeks_after
        ]
        if values:
            out[label] = median_of(values)
    return out


def ramp_factors(rows: Sequence[Stash]) -> tuple[float, float]:
    """The ramp the inversion applies to the first two weeks back.

    A week back is about three and a half games, so the first week is the
    `1-5` band and the second the `6-10` one; from the third week on the
    measured ramp is at or above one and nothing is applied. `nan` guards the
    cells where the pre-injury week was worth less than `RAMP_FLOOR`.
    """
    out: list[float] = []
    for label, _first, _last in RAMP_BANDS[:2]:
        values = [
            s.ramp[label][1]
            for s in rows
            if label in s.ramp and s.ramp[label][1] == s.ramp[label][1]
        ]
        out.append(median_of(values) if values else 1.0)
    return out[0], out[1]


def break_even(
    rate: float | None,
    back_within: int,
    curve: dict[int, dict[int, float]],
    *,
    ramp: tuple[float, float] = (1.0, 1.0),
) -> str:
    """The smallest weeks-left that makes a stash of this shape pay."""
    if rate is None:
        return "-"
    probability = prior_at(curve, STASH_DAYS, back_within * 7)
    cost = OPENED_PLACE * back_within
    for weeks_left in WEEKS_LEFT_SEARCH:
        weeks_back = weeks_left - back_within
        if weeks_back <= 0:
            continue
        gained = 0.0
        for week in range(1, weeks_back + 1):
            factor = ramp[0] if week == 1 else ramp[1] if week == 2 else 1.0
            gained += rate * factor
        if probability * gained - cost > 0.0:
            return str(weeks_left)
    return "never"


def section_five(rows: Sequence[Stash], years: Sequence[int]) -> None:
    print("== 5. the stasher's situation ==")
    ranked = [s for s in rows if s.rank > 0]
    thirds = [("top third", 1), ("middle third", 2), ("bottom third", 3)]
    body = []
    for label, which in thirds:
        sub = [s for s in ranked if third_of(s) == which]
        if not sub:
            continue
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([s.net for s in sub])),
                share(sum(1 for s in sub if s.paid), len(sub)),
                two(median_of([float(s.open_places) for s in sub])),
                two(median_of([float(s.weeks_left) for s in sub])),
            ]
        )
    table(
        ["standing on the day", "n", "med net", "positive", "med open places", "med weeks left"],
        body,
    )

    body = []
    for label, sub in (
        ("0", [s for s in rows if s.open_places == 0]),
        ("1", [s for s in rows if s.open_places == 1]),
        ("2+", [s for s in rows if s.open_places >= 2]),
    ):
        if not sub:
            continue
        body.append(
            [
                label,
                str(len(sub)),
                two(median_of([s.net for s in sub])),
                share(sum(1 for s in sub if s.paid), len(sub)),
            ]
        )
    table(["open places", "n", "med net", "positive"], body)

    print("the flat dead charge against what the team's own open places returned")
    body = []
    for year in years:
        sub = [s for s in rows if s.season == year and s.streamed is not None]
        if not sub:
            continue
        body.append(
            [
                str(year),
                str(len(sub)),
                two(OPENED_PLACE),
                two(median_of([s.streamed or 0.0 for s in sub])),
                two(median_of([(s.streamed or 0.0) * s.dead_weeks for s in sub])),
                two(median_of([s.dead_cost for s in sub])),
            ]
        )
    table(
        ["season", "n", "flat 0.38", "team's own per place", "its dead cost", "flat dead cost"],
        body,
    )
    print(f"injured_reserve_slots in this league: {IR_SLOTS}. Every stash pays the place.\n")


def third_of(stash: Stash) -> int:
    """Which third of the standings the team sat in on the decision day.

    0 when no regular period had finished yet, so nothing is ranked.
    """
    if stash.rank <= 0 or stash.teams <= 0:
        return 0
    return 1 if stash.rank <= stash.teams / 3 else 3 if stash.rank > 2 * stash.teams / 3 else 2


def section_six(rows: Sequence[Stash], names: dict[int, str], teams: dict[int, str]) -> None:
    print("== 6a. every 2026 claimed stash, named ==")
    body = []
    for stash in sorted(
        [s for s in rows if s.season == 2026 and s.kind == "claim" and s.stash],
        key=lambda s: s.day,
    ):
        body.append(
            [
                str(stash.day),
                teams.get(stash.team, str(stash.team))[:18],
                names.get(stash.player, str(stash.player))[:20],
                str(stash.days_out),
                str(stash.missed),
                f"${stash.bid}",
                str(stash.days_to_return if stash.days_to_return is not None else "-"),
                str(stash.held_to - stash.day),
                two(stash.dead_cost),
                two(stash.benefit),
                two(stash.net),
                two(stash.net_swap),
            ]
        )
    table(
        [
            "day",
            "team",
            "player",
            "d out",
            "missed",
            "paid",
            "back in",
            "held",
            "cost",
            "benefit",
            "net",
            "net (swap)",
        ],
        body,
    )

    print("== 6c. the best and the worst stash in eight seasons ==")
    scored = [s for s in rows if s.stash and (s.weeks_after or s.dead_days)]
    ordered = sorted(scored, key=lambda s: -s.net)
    body = []
    for stash in [*ordered[: LISTING // 4], *ordered[-(LISTING // 4) :]]:
        body.append(
            [
                str(stash.season),
                stash.kind,
                "kept" if stash.kept else "dropped",
                names.get(stash.player, str(stash.player))[:20],
                str(stash.day),
                str(stash.days_out),
                str(stash.days_to_return if stash.days_to_return is not None else "-"),
                str(stash.weeks_after),
                two(stash.dead_cost),
                two(stash.net),
                two(stash.net_swap),
            ]
        )
    table(
        [
            "season",
            "kind",
            "outcome",
            "player",
            "day",
            "d out",
            "back in",
            "wk back",
            "cost",
            "net",
            "net (swap)",
        ],
        body,
    )


def miller_walkthrough(
    rows: Sequence[Stash],
    boxes: dict[tuple[int, int], tuple[Box, ...]],
    names: dict[int, str],
    calendars: dict[int, Calendar],
) -> None:
    print("== 6b. Brandon Miller, 2026, worked through ==")
    player = next((pid for pid, name in names.items() if name == MILLER), None)
    if player is None:
        print("no such player in this database\n")
        return
    his = [s for s in rows if s.player == player and s.season == 2026]
    if not his:
        print("no 2026 decision on him in this database\n")
        return
    for stash in sorted(his, key=lambda s: (s.kind, s.day)):
        calendar = calendars[stash.season]
        print(
            f"{stash.kind}: day {stash.day}, team row {stash.team}, ${stash.bid}\n"
            f"  days out at the decision {stash.days_out}, team games missed {stash.missed}\n"
            f"  report that morning: {stash.report_status or 'none'}"
            f" ({stash.report_reason or '-'})\n"
            f"  returned day {stash.returned}"
            f" ({stash.days_to_return} days after the decision)\n"
            f"  held to day {stash.held_to} ({stash.held_to - stash.day} days),"
            f" censored={stash.censored}\n"
            f"  dead days {stash.dead_days} = {two(stash.dead_weeks)} weeks"
            f" x 0.38 = {two(stash.dead_cost)} categories\n"
            f"  wire replacement on the day {two(stash.replacement)} a week;"
            f" charged {two(stash.replacement_charged)} over {stash.weeks_after} weeks\n"
            f"  weekly value after return: "
            f"{', '.join(two(v) for v in stash.weekly)}\n"
            f"  benefit {two(stash.benefit)} - cost {two(stash.dead_cost)}"
            f" = net {two(stash.net)}\n"
            f"  the swap arm: against wire man {stash.wire_man} over"
            f" {len(stash.swap)} weeks of the hold, net {two(stash.net_swap)}"
            f" ({', '.join(two(v) for v in stash.swap)})\n"
            f"  healthy week {two(stash.healthy)} ({two(stash.healthy_minutes)} mp);"
            f" ramp {', '.join(f'{k} {v[0]:.2f}mp/{v[1]:.2f}val' for k, v in stash.ramp.items())}\n"
            f"  standing rank {stash.rank} of {calendar.team_count},"
            f" open places {stash.open_places}, weeks left {stash.weeks_left}\n"
        )
    his_boxes = boxes.get((2026, player), ())
    back = min((s.returned for s in his if s.returned is not None), default=0)
    missed = [box.day for box in his_boxes if not box.played and box.day < back]
    played = [(box.day, box.minutes) for box in his_boxes if box.played and box.day >= back][:8]
    print(f"  team games missed before the return: {missed}")
    print(f"  first games back (day, minutes): {played}\n")


def section_seven(
    rows: Sequence[Stash],
    names: dict[int, str],
    returns: int,
    *,
    wire: WireBook | None = None,
    boxes: Mapping[tuple[int, int], Sequence[Box]] | None = None,
) -> None:
    print("== 7. what the engine projects for a stash today, scored ==")
    print(
        "BEFORE 2026-09-24. `app.pickups.state.playable_days` dropped every day\n"
        "before ESPN's `expected_return_date` for a man whose status is in\n"
        "`RULED_OUT_STATUSES` (OUT, SUSPENSION), and every day at all when there\n"
        f"was no date. This database holds {returns} rows with an\n"
        "`expected_return_date` in any season -- and a read-only probe of ESPN's\n"
        "own kona views on 2026-09-24 found the field does not exist for\n"
        "basketball at all -- so the engine's projected games for a stash was\n"
        "zero, its rest-of-season line the empty line, its value 0.00 a week.\n"
        "\n"
        "AFTER. `docs/stash_mode.md`'s declared rule: each remaining game day\n"
        "carries the chance he is back by it (the section 2a prior, read by the\n"
        "days out this study measures) times the ramp of section 3. The column\n"
        "below is that projection made on the claim morning -- his knowable\n"
        "per-game line, the expected share of his team's remaining games,\n"
        "`ESPN_AVAILABILITY`, through this study's own lens -- multiplied by the\n"
        "weeks he was actually held after returning, which is the stretch the\n"
        "`delivered` column covers.\n"
        "\n"
        "The two columns are the two currencies of Limitation 3 and the error\n"
        "between them is read as a size, not as a calibration: `delivered` is\n"
        "`Replay` categories and `engine says` is a marginal through the lens.\n"
        "Zero was zero in both, which is why the before column needed no such\n"
        "warning and this one does.\n"
    )
    stashes = [s for s in rows if s.season == 2026 and s.kind == "claim" and s.stash]
    projected_now = {
        (stash.player, stash.day): engine_projection(stash, wire, boxes) for stash in stashes
    }
    body = []
    for stash in sorted(stashes, key=lambda s: -sum(s.weekly)):
        says = projected_now[(stash.player, stash.day)] * stash.weeks_after
        body.append(
            [
                str(stash.day),
                names.get(stash.player, str(stash.player))[:20],
                str(stash.days_out),
                "0.00",
                two(says),
                two(sum(stash.weekly)),
                two(sum(stash.weekly)),
                two(sum(stash.weekly) - says),
                str(stash.weeks_after),
            ]
        )
    table(
        [
            "day",
            "player",
            "d out",
            "was",
            "now says",
            "he delivered",
            "error was",
            "error now",
            "weeks",
        ],
        body,
    )
    delivered = [sum(s.weekly) for s in stashes]
    now = [projected_now[(s.player, s.day)] * s.weeks_after for s in stashes]
    errors = [sum(s.weekly) - projected_now[(s.player, s.day)] * s.weeks_after for s in stashes]
    weekly_now = [projected_now[(s.player, s.day)] for s in stashes]
    print(
        f"2026 claimed stashes n={len(stashes)}.\n"
        f"BEFORE: the engine projected 0.00 for every one, so its mean error was "
        f"the mean delivered, {two(mean_of(delivered))} a stash "
        f"(median {two(median_of(delivered))}, total {two(sum(delivered))}).\n"
        f"AFTER: it projects a mean of {two(mean_of(weekly_now))} categories a week "
        f"(median {two(median_of(weekly_now))}), which over the weeks each was held "
        f"comes to a mean of {two(mean_of(now))} a stash and {two(sum(now))} in total.\n"
        f"Mean error {two(mean_of(errors))} a stash, median {two(median_of(errors))}, "
        f"mean absolute {two(mean_of([abs(e) for e in errors]))}.\n"
        f"Men who returned and were held: "
        f"{sum(1 for s in stashes if s.weeks_after)} of {len(stashes)}.\n"
    )


def engine_projection(
    stash: Stash,
    wire: WireBook | None,
    boxes: Mapping[tuple[int, int], Sequence[Box]] | None,
) -> float:
    """What the engine now says a stash is worth a week, on the claim morning.

    The declared rule applied to this study's own instrument, because a replay
    cannot read it off a snapshot: this database holds status snapshots for the
    season in progress only, so a 2026 morning has no stored injury status at
    all and `build_players` would call every one of these men fit. The days out
    are the box scores' (section 0), which is the count the prior is measured
    against in the first place, and his remaining game days are his own rows.
    """
    if wire is None or boxes is None:
        return 0.0
    his = boxes.get((stash.season, stash.player), ())
    ahead = [box.day for box in his if box.day >= stash.day]
    if not ahead:
        return 0.0
    share = expected_games((day - stash.day for day in ahead), days_out=stash.days_out) / len(ahead)
    per_game = per_game_line(wire.session, stash.season, stash.player, stash.day, tilt=False)
    if not per_game.counts:
        return 0.0
    return wire.weekly(stash.day, per_game.scaled(share * ESPN_AVAILABILITY))


def headline(rows: Sequence[Stash], years: Sequence[int]) -> str:
    year = max(years)
    claims = [s for s in rows if s.season == year and s.kind == "claim"]
    stashes = [s for s in claims if s.stash]
    back = [s for s in stashes if s.returned is not None]
    nets = [s.net for s in stashes]
    return (
        f"Of the {len(claims)} {year} executed adds that landed on a man who was "
        f"already out, {len(stashes)} were stashes (out {STASH_DAYS}+ days at the "
        f"claim); {len(back)} of {len(stashes)} returned that season, the median "
        f"after {median_of([float(s.days_to_return or 0) for s in back]):.1f} days; "
        f"the median stash netted {median_of(nets):+.2f} categories against the dead "
        f"weeks, positive in "
        f"{100.0 * sum(1 for s in stashes if s.paid) / max(1, len(stashes)):.2f}%.\n"
        f"Scored the second way -- him against the wire's best man of that morning "
        f"in the same place, every week of the hold, one currency and no constant -- "
        f"the median is {median_of([s.net_swap for s in stashes]):+.2f} and "
        f"{100.0 * sum(1 for s in stashes if s.paid_swap) / max(1, len(stashes)):.2f}% "
        f"are positive."
    )


def why(rows: Sequence[Stash], counts: dict[int, dict[str, int]], years: Sequence[int]) -> None:
    """Recompute the headline a second way, from the raw parts."""
    year = max(years)
    print("== the accounting, recomputed ==")
    body = []
    for season in years:
        row = counts[season]
        body.append(
            [
                str(season),
                str(row["adds"]),
                str(row["no boxes"]),
                str(row["was playing"]),
                str(row["out"]),
                str(row["no run"]),
                str(sum(1 for s in rows if s.season == season and s.kind == "claim")),
            ]
        )
    table(
        ["season", "adds", "no box rows", "was playing", "was out", "no roster run", "kept"],
        body,
    )

    claims = [s for s in rows if s.season == year and s.kind == "claim"]
    stashes = [s for s in claims if s.stash]
    # The second way: the level count rebuilt from the raw days-out list, and
    # the median net rebuilt by re-summing every stash's own components rather
    # than reading the `net` property.
    days = sorted(s.days_out for s in claims)
    by_hand = sum(1 for d in days if d >= STASH_DAYS)
    parts = [
        (sum(s.weekly) - s.replacement_charged) - OPENED_PLACE * (s.dead_days / 7.0)
        for s in stashes
    ]
    print(
        f"{year}: claims on an out man {len(claims)}; days-out list length {len(days)};\n"
        f"  stashes by the property {sum(1 for s in claims if s.stash)}, "
        f"by re-reading the list {by_hand}\n"
        f"  median net by the property {median_of([s.net for s in stashes]):+.4f}, "
        f"by re-summing the parts {median_of(parts):+.4f}\n"
        f"  largest absolute difference between the two nets "
        f"{max((abs(a - b.net) for a, b in zip(parts, stashes, strict=True)), default=0.0):.2e}\n"
    )
    returned = [s for s in stashes if s.returned is not None]
    print(
        f"  returned {len(returned)}; median days to return "
        f"{median_of([float(s.days_to_return or 0) for s in returned]):.2f}; "
        f"positive {sum(1 for s in stashes if s.net > 0)} of {len(stashes)}\n"
    )
    # The third reading: the same holds scored as one swap against the wire's
    # best man, which shares no constant and no lens with the first.
    swaps = [s.net_swap for s in stashes]
    agree = sum(1 for s in stashes if s.paid == s.paid_swap)
    print(
        f"  swap arm: median {median_of(swaps):+.4f}, positive "
        f"{sum(1 for s in stashes if s.paid_swap)} of {len(stashes)}; the two arms "
        f"agree on the sign for {agree} of {len(stashes)} "
        f"({100.0 * agree / max(1, len(stashes)):.2f}%)\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="When a stash is worth it.")
    parser.add_argument("--season", type=int, default=None, help="one season only")
    parser.add_argument("--why", action="store_true", help="print the accounting")
    args = parser.parse_args()

    started_at = time.time()
    session = connect()
    calendars = load_calendars(session)
    calendars.pop(2027, None)
    years = [args.season] if args.season else sorted(calendars)
    print(f"seasons: {[(y, calendars[y].team_count) for y in years]}")
    print(f"games a week: {[(y, round(calendars[y].games_a_week, 2)) for y in years]}")

    boxes = load_boxes(session)
    held = load_held(session)
    room, roster_size = load_roster_room(session)
    records = load_records(session)
    dates = load_dates(session)
    raw_claims = load_claims(session)
    names = {
        int(pid): str(name) for pid, name in session.execute(text("SELECT id, name FROM players"))
    }
    team_names = {
        int(row[0]): str(row[1] or row[0])
        for row in session.execute(text("SELECT id, name FROM teams"))
    }
    returns_stored = int(
        session.scalar(text("SELECT count(expected_return_date) FROM player_status_snapshots")) or 0
    )

    print("\n== 0a. the box-score rows trace his own team's schedule ==")
    table(
        ["season", "player-seasons", "rows on that schedule %", "team games with a row %"],
        coverage_rows(session),
    )

    print("== 0b. absences in the box scores, eight seasons ==")
    spells: list[Spell] = []
    for (season, _player), his in boxes.items():
        if season not in set(years) or sum(1 for b in his if b.played) < ROTATION_GAMES:
            continue
        spells.extend(spell for spell in spells_of(his, season) if not spell.no_prior)
    body = []
    for year in years:
        sub = [spell for spell in spells if spell.season == year]
        body.append(
            [
                str(year),
                str(len(sub)),
                two(median_of([float(s.total_days) for s in sub])),
                two(median_of([float(s.missed) for s in sub])),
                share(sum(1 for s in sub if s.total_days >= STASH_DAYS), len(sub)),
                share(sum(1 for s in sub if s.returned is None), len(sub)),
            ]
        )
    body.append(
        [
            "pooled",
            str(len(spells)),
            two(median_of([float(s.total_days) for s in spells])),
            two(median_of([float(s.missed) for s in spells])),
            share(sum(1 for s in spells if s.total_days >= STASH_DAYS), len(spells)),
            share(sum(1 for s in spells if s.returned is None), len(spells)),
        ]
    )
    table(["season", "spells", "med days out", "med missed", "8+ days", "never back"], body)

    # The prior this study uses drops the suspended 2020 season: an absence
    # that ran into the stoppage never ends in the box scores, and 2020's 927
    # spells carry a quarter of the whole census's never-returned share.
    all_curve, all_sizes = return_prior(spells)
    curve, sizes = return_prior([s for s in spells if s.season != COVID_SEASON])

    rows: list[Stash] = []
    counts: dict[int, dict[str, int]] = {}
    for year in years:
        calendar = calendars[year]
        claims, count = build_claims(calendar, boxes, held, raw_claims)
        counts[year] = count
        holds = build_holds(calendar, boxes, held)
        season_rows = [*claims, *holds]
        wire = WireBook(session, calendar)
        streamed = streamed_per_place(session, calendar)
        with bt.patched_state():
            replay = bt.Replay.load(session, league_row(session, calendar))
            for stash in season_rows:
                score(replay, calendar, wire, stash)
        for stash in season_rows:
            his = boxes.get((stash.season, stash.player), ())
            line, minutes, norm = pre_injury(his, stash.day)
            stash.healthy = wire.weekly(stash.day, line)
            stash.healthy_minutes = minutes
            stash.healthy_games = norm
            stash.teams = calendar.team_count
            stash.ramp = ramp_of(his, stash, wire)
            stash.rank = rank_on(records, calendar, stash.team, stash.day)
            stash.open_places = max(
                0,
                roster_size.get(year, 0) - room.get((year, stash.team, stash.day), 0),
            )
            stash.streamed = streamed.get(stash.team)
        rows.extend(season_rows)
        print(
            f"{year}: claims on an out man {len(claims)}, held stashes {len(holds)}, "
            f"{time.time() - started_at:.0f}s elapsed"
        )

    found = attach_reports(session, dates, rows)
    print(f"\ninjury-report lines found for a decision morning: {found} of {len(rows)}\n")

    print("== The answer, up front ==")
    print(headline(rows, years) + "\n")

    section_one(rows, years, counts)
    section_two(rows, curve, sizes, spells, all_curve=all_curve, all_sizes=all_sizes)
    section_three(rows)
    section_four([s for s in rows if s.weeks_after or s.dead_days], curve)
    section_five([s for s in rows if s.stash], years)
    section_six(rows, names, team_names)
    miller_walkthrough(rows, boxes, names, calendars)
    section_seven(
        rows,
        names,
        returns_stored,
        wire=WireBook(session, calendars[max(years)]),
        boxes=boxes,
    )

    if args.why:
        why(rows, counts, years)

    print(f"elapsed {time.time() - started_at:.0f}s")
    return 0


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        raise SystemExit(main())
