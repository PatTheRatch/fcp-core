#!/usr/bin/env python3
"""A stash made by a team that has already won its place: what it really costs.

Usage:
    python scripts/stash_locks.py                    # eight seasons, every table
    python scripts/stash_locks.py --season 2026      # one season
    python scripts/stash_locks.py --no-counterfactual  # skip the roster-off runs

WHAT THIS MEASURES

The owner, 2026-09-24: *"What about if you are a playoff lock? Do we do any
research on that?"*

`docs/stashes.md` section 5 cut the stash census by where the team stood that
morning -- top third of the league by banked categories paid 30.84% of the
time against the bottom third's 24.40% -- but it priced every stash the same
way. Every dead regular-season week was charged at `OPENED_PLACE` (0.38) and
every benefit was counted on the regular-season weeks after his return. That
is right for a team in the race. It is wrong for a team that has already won
its place: its dead regular-season weeks buy only seeding, and what it is
really buying with the roster place is **the man in the playoff weeks**, times
the chance he is back for them.

Section 5 could not do better because it had no playoff odds: the database
holds 37 stored `team_reports` rows, all 2026, all from a handful of days. It
does not need stored ones. `app.inseason.projected.project_standings` runs on
any day of any stored season in about a second and a half, and this script
runs it once per **distinct decision morning** and reads every team's odds off
the one run.

DECLARED BEFORE ANYTHING RAN

**A lock**, on a decision day: playoff odds at or above `LOCK_ODDS` (0.95) from
the engine run on that morning, at `N_SIMS` (10,000) simulated seasons and
`SEED` (3853870). **In the race**: `RACE_FLOOR` (0.25) up to `LOCK_ODDS`. **Out
of it**: below `RACE_FLOOR`. `docs/projected_record.md` section 0 is the reason
the band can be trusted at the top: of 370 calls above 90%, 83.5% happened.
The band is honest, not perfect, and 0.95 is inside it.

**The seeding stake.** For a lock, `1 - max(seed odds)` off the same run: the
chance the team does *not* finish in its own most likely place. Zero means the
seed is settled and there is nothing left to play for in the regular season;
one means everything is still moving.

**No look-ahead.** The weekly spreads are read with `before=season` and the era
adjustment off, which is `scripts/projected_calibration.py`'s own rule. 2019 is
the one season with no earlier season to measure on; it is run on the pooled
basis, flagged in every table, and every pooled figure is printed a second time
without it.

**The lock's net**, beside the study's standard net and never replacing it:

    benefit = P(back by the first playoff week | days out, the return prior)
              x (his playoff-weeks value - replacement, over the playoff weeks
                 he was held)
    cost    = OPENED_PLACE x dead regular-season weeks x seeding stake
              + OPENED_PLACE x dead playoff weeks
    lock net = benefit - cost

`P` is `app.pickups.returns.probability_back_within`, the shipped prior. The
playoff-weeks value is the same `Replay` counterfactual the study uses for a
regular week -- `-Replay.delta(team, window, first, last, [him], [])` -- taken
over the playoff matchup periods instead. The replacement is the wire's best
man on the decision morning, the same number the study charges, pro-rated by
the playoff period's length.

Two bounds are printed beside it: **stake = 1**, which is the study's own
pricing (every dead regular week charged in full), and **stake = 0**, "nothing
to lose". The measured stake sits between them.

**No hurdle and no constant moves.** `OPENED_PLACE`, `TYPICAL_PICKUP`, the
return prior, the ramp and both season hurdles are the shipped values.

LIMITATIONS, STATED BEFORE CONCLUSIONS

* **A decision taken on a playoff day is not classified.** Once the playoffs
  have begun, "playoff odds" from a bracket projection is not the quantity
  this study means, so those decisions are counted apart and left out of the
  three groups.
* **A decision the engine cannot project is classified as nothing**, not as
  "out of it". No decision in this database is one -- every one of the 2,111
  falls inside a stored matchup period -- and the column is printed anyway so
  a reader can see that it is zero rather than take it on trust.
* **2020 is the suspended season** and it is in every pooled figure with its
  own row beside it, the same way `docs/stashes.md` handles it.
* **The playoff-weeks value counts every stored matchup in a playoff period**,
  consolation brackets included. For a lock that is the real bracket; for the
  other groups it may not be, which is one more reason the lock's net is only
  ever reported for locks.
* **The benefit multiplies a prior by a realised number.** `P` is what the
  morning knew; the playoff-weeks value is what happened. The product is the
  brief's declared formula and it is a hybrid: the realised playoff value
  alone is printed beside it in every table so the reader can see exactly what
  `P` is doing.

REPRODUCE. From the worktree root, against whichever database `DATABASE_URL`
names:

    PYTHONPATH=. ~/fcp-core/.venv/bin/python scripts/stash_locks.py

READ-ONLY. Every query is a SELECT; nothing is written anywhere.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pickups_backtest as bt  # type: ignore[import-not-found]

# The study this one extends. Everything about what a stash *is* -- the
# populations, the levels, the wire book, the `Replay` scoring, the healthy
# tiers, the net -- is imported rather than restated, so the two cannot
# disagree about a single decision.
import stashes as st  # type: ignore[import-not-found]
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.draft.targets import CategoryDistribution, category_distributions
from app.inseason.projected import N_SIMS, SEED, Projection, project_standings
from app.pickups.returns import probability_back_within
from app.pickups.state import load_team_week
from app.scoring.replacement import OPENED_PLACE

#: Playoff odds at or above which a team is a LOCK on that morning.
LOCK_ODDS = 0.95

#: Playoff odds below which it is OUT of it. Between the two it is IN THE RACE.
RACE_FLOOR = 0.25

#: The three groups, in the order every table prints them.
GROUPS: tuple[str, ...] = ("lock", "race", "out")

#: A lock whose seeding stake is at or below this has nothing left to play for
#: in the regular season: its most likely seed is all but settled.
SETTLED_STAKE = 0.10

#: The seeding stakes the inverted table is written over.
STAKE_COLUMNS: tuple[float, ...] = (0.00, 0.25, 0.50, 1.00)

#: How many rows the named tables print.
LISTING = 30

#: The season with no earlier season of its size to measure the league's
#: weekly spreads on, so its run uses the pooled basis and says so.
NO_EARLIER_BASIS = 2019


def group_of(odds: float) -> str:
    if odds >= LOCK_ODDS:
        return "lock"
    if odds >= RACE_FLOOR:
        return "race"
    return "out"


# ---------------------------------------------------------------------------
# the engine, once per morning
# ---------------------------------------------------------------------------


@dataclass
class OddsBook:
    """Every team's playoff odds on a decision morning, memoized per day.

    One `project_standings` run answers for the whole league, so the cost of
    this study is the number of distinct (season, day) pairs its decisions
    fall on and not the number of decisions.
    """

    session: Session
    league: LeagueSeason
    season: int
    #: The weekly spreads, measured on seasons strictly before this one.
    distributions: Sequence[CategoryDistribution]
    pooled_basis: bool
    n_sims: int = N_SIMS
    seed: int = SEED
    _days: dict[int, Projection | None] = field(default_factory=dict)
    runs: int = 0
    failures: int = 0
    seconds: float = 0.0

    @classmethod
    def build(cls, session: Session, calendar: st.Calendar, n_sims: int) -> OddsBook:
        league = st.league_row(session, calendar)
        season = int(league.season)
        measured = category_distributions(session, league, before=season, adjust_for_era=False)
        pooled = not measured
        if pooled:
            measured = category_distributions(session, league, adjust_for_era=False)
        return cls(
            session=session,
            league=league,
            season=season,
            distributions=measured,
            pooled_basis=pooled,
            n_sims=n_sims,
        )

    def on(self, day: int) -> Projection | None:
        """The day's projection, or None when the day is in no matchup period."""
        if day not in self._days:
            started = time.time()
            try:
                self._days[day] = project_standings(
                    self.session,
                    self.league,
                    day,
                    distributions=self.distributions,
                    n_sims=self.n_sims,
                    seed=self.seed,
                )
            except ValueError:
                self._days[day] = None
                self.failures += 1
            self.runs += 1
            self.seconds += time.time() - started
        return self._days[day]

    def counterfactual(self, day: int, espn_team_id: int, without: int) -> Projection | None:
        """The same morning with one man off that team's roster, and nothing else.

        The honest counterfactual for "what did holding him do to the finish":
        the what-if engine's own `rosters=` override, which is the seam
        `app.inseason.what_if` already uses.
        """
        week = load_team_week(self.session, self.league, espn_team_id, day)
        ids = [player.player_id for player in week.active if player.player_id != without]
        if len(ids) == len(week.active):
            return None
        started = time.time()
        try:
            found = project_standings(
                self.session,
                self.league,
                day,
                distributions=self.distributions,
                n_sims=self.n_sims,
                seed=self.seed,
                rosters={espn_team_id: ids},
            )
        except ValueError:
            return None
        finally:
            self.runs += 1
            self.seconds += time.time() - started
        return found


# ---------------------------------------------------------------------------
# one decision, with the morning's odds and the playoff weeks on it
# ---------------------------------------------------------------------------


@dataclass
class LockRow:
    """One stash decision, classified, and priced the lock's way as well."""

    stash: st.Stash
    #: The team's playoff odds that morning, or None on a playoff day.
    odds: float | None = None
    #: `1 - max(seed odds)` that morning; None when the odds are.
    stake: float | None = None
    bye_odds: float | None = None
    #: True when the decision fell inside a playoff matchup period.
    in_playoffs: bool = False
    #: True when no projection could be made for that morning at all, so the
    #: decision has no odds and is classified as nothing.
    unprojectable: bool = False
    #: Dead days that fell in regular periods, and in playoff ones.
    dead_regular_days: int = 0
    dead_playoff_days: int = 0
    #: P(back by the first playoff day at or after the decision).
    p_back_by_playoffs: float = 0.0
    #: Days from the decision to that first playoff day; 0 when there are none.
    days_to_playoffs: int = 0
    #: His realised value over the playoff weeks the team still held him,
    #: and the replacement charged over those same weeks.
    playoff_value: float = 0.0
    playoff_replacement: float = 0.0
    playoff_weeks_held: float = 0.0
    #: Was he back, and on this team's roster, on the first playoff day?
    in_for_playoff_week_one: bool = False
    #: The team's result in each playoff matchup period it played.
    playoff_results: tuple[str, ...] = ()
    #: The finish with him and without him, on the decision morning.
    finish_before: tuple[float, float] | None = None
    finish_after: tuple[float, float] | None = None
    pooled_basis: bool = False

    @property
    def group(self) -> str:
        if self.in_playoffs:
            return "playoffs"
        if self.unprojectable or self.odds is None:
            return "unprojectable"
        return group_of(self.odds)

    @property
    def dead_regular_weeks(self) -> float:
        return self.dead_regular_days / 7.0

    @property
    def dead_playoff_weeks(self) -> float:
        return self.dead_playoff_days / 7.0

    @property
    def realised_playoff_benefit(self) -> float:
        """What he actually won over the playoff weeks, against the wire."""
        return self.playoff_value - self.playoff_replacement

    def benefit(self) -> float:
        return self.p_back_by_playoffs * self.realised_playoff_benefit

    def cost(self, stake: float | None = None) -> float:
        share = self.stake if stake is None else stake
        share = 0.0 if share is None else share
        return OPENED_PLACE * (self.dead_regular_weeks * share + self.dead_playoff_weeks)

    def lock_net(self, stake: float | None = None) -> float:
        return self.benefit() - self.cost(stake)

    @property
    def settled(self) -> bool:
        return self.stake is not None and self.stake <= SETTLED_STAKE


def playoff_window(calendar: st.Calendar) -> tuple[int, ...]:
    """Every playoff matchup period of the season, ascending."""
    return tuple(
        sorted(number for number, (_f, _l, playoff) in calendar.periods.items() if playoff)
    )


def first_playoff_day(calendar: st.Calendar, after: int) -> int | None:
    """The first day of the first playoff period ending at or after `after`."""
    for number in playoff_window(calendar):
        first, final, _playoff = calendar.periods[number]
        if final >= after:
            return max(first, after)
    return None


def split_dead_days(calendar: st.Calendar, stash: st.Stash) -> tuple[int, int]:
    """The decision's dead days, split into regular ones and playoff ones."""
    regular = 0
    playoff = 0
    for day in range(stash.day, stash.day + stash.dead_days):
        number = calendar.period_of(day)
        if number is None:
            continue
        if calendar.periods[number][2]:
            playoff += 1
        else:
            regular += 1
    return regular, playoff


def playoff_value(
    replay: bt.Replay,
    calendar: st.Calendar,
    stash: st.Stash,
) -> tuple[float, float, float]:
    """His value over the playoff weeks held after his return, and the charge.

    The same call and the same sign the study uses for a regular week, taken
    over the playoff matchup periods. Returns (value, replacement charged,
    weeks).
    """
    end = min(stash.held_to, calendar.last_day)
    if stash.returned is None:
        return 0.0, 0.0, 0.0
    value = 0.0
    charged = 0.0
    weeks = 0.0
    for number in playoff_window(calendar):
        first_day, final_day, _playoff = calendar.periods[number]
        window = next((w for w in replay.periods if w.period == number), None)
        if window is None:
            continue
        first = max(first_day, stash.returned, stash.day)
        last = min(final_day, end)
        if first > last:
            continue
        value += -replay.delta(stash.team, window, first, last, [stash.player], [])
        charged += stash.replacement * (last - first + 1) / 7.0
        weeks += (last - first + 1) / 7.0
    return value, charged, weeks


def playoff_results(session: Session, calendar: st.Calendar) -> dict[tuple[int, int], str]:
    """(team row, playoff period) -> WON, LOST, TIED or PLAYED."""
    out: dict[tuple[int, int], str] = {}
    for period, home, away, winner in session.execute(
        text(
            "SELECT mp.period, m.home_team_id, m.away_team_id, m.winner"
            " FROM matchups m JOIN matchup_periods mp ON mp.id = m.matchup_period_id"
            " WHERE mp.league_season_id = :ls AND mp.is_playoff IS TRUE"
            " AND m.away_team_id IS NOT NULL"
        ),
        {"ls": calendar.league_season_id},
    ):
        name = str(winner or "")
        out[(int(home), int(period))] = (
            "won" if name == "HOME" else "lost" if name == "AWAY" else "tied"
        )
        out[(int(away), int(period))] = (
            "won" if name == "AWAY" else "lost" if name == "HOME" else "tied"
        )
    return out


# ---------------------------------------------------------------------------
# the tables
# ---------------------------------------------------------------------------


def net_cells(rows: Sequence[LockRow]) -> list[str]:
    """Median standard net, positive share, and the same for the lock's net."""
    if not rows:
        return ["-"] * 6
    return [
        st.two(st.median_of([r.stash.net for r in rows])),
        st.share(sum(1 for r in rows if r.stash.net > 0), len(rows)),
        st.two(st.median_of([r.lock_net() for r in rows])),
        st.share(sum(1 for r in rows if r.lock_net() > 0), len(rows)),
        st.two(st.median_of([r.lock_net(1.0) for r in rows])),
        st.two(st.median_of([r.lock_net(0.0) for r in rows])),
    ]


def section_one(rows: Sequence[LockRow], years: Sequence[int]) -> None:
    print("== 1. every stash decision, classified by the morning's playoff odds ==")
    header = [
        "season",
        "stash decisions",
        "classified",
        "on a playoff day",
        "no projection",
        *GROUPS,
        "lock share",
        "basis",
    ]
    body = []
    for year in years:
        sub = [r for r in rows if r.stash.season == year]
        ranked = [r for r in sub if r.group in GROUPS]
        locks = sum(1 for r in ranked if r.group == "lock")
        body.append(
            [
                str(year),
                str(len(sub)),
                str(len(ranked)),
                str(sum(1 for r in sub if r.in_playoffs)),
                str(sum(1 for r in sub if r.unprojectable)),
                *[str(sum(1 for r in ranked if r.group == name)) for name in GROUPS],
                st.share(locks, len(ranked)),
                "pooled" if year == NO_EARLIER_BASIS else "earlier seasons",
            ]
        )
    for label, subset in (
        ("pooled", list(rows)),
        ("without 2019", [r for r in rows if r.stash.season != NO_EARLIER_BASIS]),
        (
            "without 2019/2020",
            [r for r in rows if r.stash.season not in (NO_EARLIER_BASIS, st.COVID_SEASON)],
        ),
    ):
        ranked = [r for r in subset if r.group in GROUPS]
        locks = sum(1 for r in ranked if r.group == "lock")
        body.append(
            [
                label,
                str(len(subset)),
                str(len(ranked)),
                str(sum(1 for r in subset if r.in_playoffs)),
                str(sum(1 for r in subset if r.unprojectable)),
                *[str(sum(1 for r in ranked if r.group == name)) for name in GROUPS],
                st.share(locks, len(ranked)),
                "",
            ]
        )
    st.table(header, body)

    print("the three groups against the standing third section 5 used")
    body = []
    for name in GROUPS:
        sub = [r for r in rows if r.group == name]
        thirds = [st.third_of(r.stash) for r in sub]
        body.append(
            [
                name,
                str(len(sub)),
                st.two(st.median_of([r.odds or 0.0 for r in sub])),
                st.share(sum(1 for t in thirds if t == 1), sum(1 for t in thirds if t)),
                st.share(sum(1 for t in thirds if t == 2), sum(1 for t in thirds if t)),
                st.share(sum(1 for t in thirds if t == 3), sum(1 for t in thirds if t)),
            ]
        )
    st.table(
        ["group", "n", "med playoff odds", "in the top third", "middle", "bottom"],
        body,
    )

    print("what each group's stash decisions were, and what they paid the study's way")
    body = []
    for name in (*GROUPS, "playoffs", "unprojectable"):
        sub = [r for r in rows if r.group == name]
        if not sub:
            continue
        body.append(
            [
                name,
                str(len(sub)),
                st.two(st.median_of([float(r.stash.days_out) for r in sub])),
                st.two(st.median_of([float(r.stash.weeks_left) for r in sub])),
                st.two(st.median_of([r.stash.healthy for r in sub])),
                st.two(st.median_of([r.stash.net for r in sub])),
                st.share(sum(1 for r in sub if r.stash.net > 0), len(sub)),
                st.two(st.median_of([r.stash.net_swap for r in sub])),
            ]
        )
    st.table(
        [
            "group",
            "n",
            "med days out",
            "med weeks left",
            "med healthy",
            "med net (standard)",
            "positive",
            "med net (swap)",
        ],
        body,
    )


def section_two(rows: Sequence[LockRow], years: Sequence[int]) -> None:
    locks = [r for r in rows if r.group == "lock"]
    print("== 2. the lock's net against the standard net ==")
    header = [
        "cut",
        "n",
        "med net",
        "positive",
        "med lock net",
        "positive",
        "lock net, stake=1",
        "lock net, stake=0",
    ]
    body = [["every lock", str(len(locks)), *net_cells(locks)]]
    for year in years:
        sub = [r for r in locks if r.stash.season == year]
        if sub:
            body.append([str(year), str(len(sub)), *net_cells(sub)])
    st.table(header, body)

    print("by level (days out at the decision)")
    body = []
    for label, _first, _last in st.LEVELS:
        sub = [r for r in locks if r.stash.level == label]
        if sub:
            body.append([label, str(len(sub)), *net_cells(sub)])
    st.table(header, body)

    print("by his healthy value")
    body = []
    for label, _low, _high in (*st.HEALTHY_TIERS, ("?", 0.0, None)):
        sub = [r for r in locks if r.stash.tier == label]
        if sub:
            body.append([label, str(len(sub)), *net_cells(sub)])
    st.table(header, body)

    print("by the seeding stake")
    body = []
    for label, sub in (
        (f"settled (stake <= {SETTLED_STAKE:.2f})", [r for r in locks if r.settled]),
        ("still moving", [r for r in locks if not r.settled]),
    ):
        if sub:
            body.append([label, str(len(sub)), *net_cells(sub)])
    st.table(header, body)

    print("what the lock's net is made of")
    body = []
    for label, sub in (
        ("every lock", locks),
        ("settled", [r for r in locks if r.settled]),
        ("still moving", [r for r in locks if not r.settled]),
    ):
        if not sub:
            continue
        body.append(
            [
                label,
                str(len(sub)),
                st.two(st.median_of([r.stake or 0.0 for r in sub])),
                st.two(st.median_of([r.dead_regular_weeks for r in sub])),
                st.two(st.median_of([r.dead_playoff_weeks for r in sub])),
                st.two(st.median_of([r.p_back_by_playoffs for r in sub])),
                st.two(st.median_of([r.playoff_weeks_held for r in sub])),
                st.two(st.median_of([r.realised_playoff_benefit for r in sub])),
                st.two(st.median_of([r.cost() for r in sub])),
                st.two(st.median_of([r.stash.dead_cost for r in sub])),
            ]
        )
    st.table(
        [
            "cut",
            "n",
            "med stake",
            "dead reg wk",
            "dead po wk",
            "P(back by po)",
            "po weeks held",
            "realised po benefit",
            "lock cost",
            "standard cost",
        ],
        body,
    )

    print("does the lock's lens change the answer? the two nets, decision by decision")
    body = []
    for label, sub in (
        ("every lock", locks),
        ("settled", [r for r in locks if r.settled]),
        ("still moving", [r for r in locks if not r.settled]),
        ("reached the playoffs", [r for r in locks if r.in_for_playoff_week_one]),
    ):
        if not sub:
            continue
        agree = sum(1 for r in sub if (r.lock_net() > 0) == (r.stash.net > 0))
        body.append(
            [
                label,
                str(len(sub)),
                st.share(agree, len(sub)),
                st.share(
                    sum(1 for r in sub if r.lock_net() > 0 >= r.stash.net),
                    len(sub),
                ),
                st.share(
                    sum(1 for r in sub if r.stash.net > 0 >= r.lock_net()),
                    len(sub),
                ),
                st.two(st.median_of([r.lock_net() - r.stash.net for r in sub])),
                st.two(st.mean_of([r.lock_net() - r.stash.net for r in sub])),
                st.share(sum(1 for r in sub if r.playoff_weeks_held > 0), len(sub)),
            ]
        )
    st.table(
        [
            "cut",
            "n",
            "the two nets agree on the sign",
            "lock says yes, standard no",
            "standard yes, lock no",
            "med lock - standard",
            "mean lock - standard",
            "has playoff weeks",
        ],
        body,
    )

    print("the race and the out groups, the study's way, so the three read side by side")
    body = []
    for name in ("race", "out"):
        sub = [r for r in rows if r.group == name]
        if not sub:
            continue
        body.append(
            [
                name,
                str(len(sub)),
                st.two(st.median_of([r.stash.net for r in sub])),
                st.share(sum(1 for r in sub if r.stash.net > 0), len(sub)),
                st.two(st.median_of([r.stash.dead_cost for r in sub])),
                st.two(st.median_of([r.stash.benefit for r in sub])),
            ]
        )
    sub = locks
    body.append(
        [
            "lock",
            str(len(sub)),
            st.two(st.median_of([r.stash.net for r in sub])),
            st.share(sum(1 for r in sub if r.stash.net > 0), len(sub)),
            st.two(st.median_of([r.stash.dead_cost for r in sub])),
            st.two(st.median_of([r.stash.benefit for r in sub])),
        ]
    )
    st.table(["group", "n", "med net", "positive", "med cost", "med benefit"], body)


def inverted(rows: Sequence[LockRow]) -> None:
    """A man worth X is worth stashing if back by the playoffs with P >= Y."""
    locks = [r for r in rows if r.group == "lock" and r.playoff_weeks_held > 0]
    print("== 2e. the break-even inverted for a lock ==")
    print(
        "The cell is the smallest P(back by the first playoff week) that makes\n"
        "the lock's net positive, given the tier's own measured playoff-weeks\n"
        f"net a week and the median lock's dead weeks. `OPENED_PLACE` is {OPENED_PLACE:.2f}.\n"
    )
    dead_regular = st.median_of([r.dead_regular_weeks for r in rows if r.group == "lock"])
    dead_playoff = st.median_of([r.dead_playoff_weeks for r in rows if r.group == "lock"])
    weeks = st.median_of([r.playoff_weeks_held for r in locks])
    print(
        f"median dead regular weeks {dead_regular:.2f}, dead playoff weeks "
        f"{dead_playoff:.2f}, playoff weeks held {weeks:.2f}\n"
    )
    body = []
    for label, _low, _high in (*st.HEALTHY_TIERS, ("?", 0.0, None)):
        sub = [r for r in locks if r.stash.tier == label]
        if not sub:
            body.append([label, "0", "-", *["-"] * len(STAKE_COLUMNS)])
            continue
        rate = st.median_of([r.realised_playoff_benefit / r.playoff_weeks_held for r in sub])
        cells = []
        for stake in STAKE_COLUMNS:
            cost = OPENED_PLACE * (dead_regular * stake + dead_playoff)
            gain = rate * weeks
            if gain <= 0.0:
                cells.append("never" if cost > 0 else "any")
            elif cost <= 0.0:
                cells.append("any")
            else:
                need = cost / gain
                cells.append(f"{need:.2f}" if need <= 1.0 else "never")
        body.append([label, str(len(sub)), st.two(rate), *cells])
    st.table(
        [
            "worth X a week",
            "n",
            "measured po net a week",
            *[f"stake {stake:.2f}" for stake in STAKE_COLUMNS],
        ],
        body,
    )


def section_three(rows: Sequence[LockRow], names: dict[int, str]) -> None:
    locks = [r for r in rows if r.group == "lock"]
    print("== 3. did the stash reach the playoffs? ==")
    body = []
    for label, sub in (
        ("every lock", locks),
        ("claims", [r for r in locks if r.stash.kind == "claim"]),
        ("holds", [r for r in locks if r.stash.kind == "hold"]),
    ):
        if not sub:
            continue
        reached = [r for r in sub if r.in_for_playoff_week_one]
        body.append(
            [
                label,
                str(len(sub)),
                st.share(sum(1 for r in sub if r.stash.returned is not None), len(sub)),
                st.share(len(reached), len(sub)),
                st.two(st.median_of([r.playoff_weeks_held for r in reached])),
                st.two(st.median_of([r.realised_playoff_benefit for r in reached])),
                st.share(sum(1 for r in reached if r.realised_playoff_benefit > 0), len(reached)),
            ]
        )
    st.table(
        [
            "cut",
            "n",
            "returned at all",
            "back & held for playoff week 1",
            "med po weeks",
            "med po benefit",
            "positive",
        ],
        body,
    )

    reached = [r for r in locks if r.in_for_playoff_week_one]
    print("what the teams that got him there did in the bracket")
    counted: dict[str, int] = defaultdict(int)
    for row in reached:
        counted[" then ".join(row.playoff_results) or "no playoff matchup stored"] += 1
    st.table(
        ["the team's playoff rounds", "n"],
        [[key, str(value)] for key, value in sorted(counted.items(), key=lambda kv: -kv[1])],
    )

    moved = [r for r in reached if r.finish_before is not None and r.finish_after is not None]
    print(
        "the honest counterfactual: the same morning re-run with him off the roster.\n"
        "`odds` is playoff odds, `seed` the chance of his team's most likely place.\n"
    )
    body = []
    for row in sorted(moved, key=lambda r: -(r.realised_playoff_benefit))[:LISTING]:
        before = row.finish_before or (0.0, 0.0)
        after = row.finish_after or (0.0, 0.0)
        body.append(
            [
                str(row.stash.season),
                str(row.stash.day),
                names.get(row.stash.player, str(row.stash.player))[:20],
                st.two(row.odds or 0.0),
                st.two(row.stake or 0.0),
                st.two(before[0]),
                st.two(after[0]),
                f"{before[0] - after[0]:+.4f}",
                f"{before[1] - after[1]:+.4f}",
                st.two(row.realised_playoff_benefit),
                st.two(row.lock_net()),
            ]
        )
    st.table(
        [
            "season",
            "day",
            "player",
            "odds",
            "stake",
            "odds with him",
            "without him",
            "playoff odds moved",
            "seed odds moved",
            "po benefit",
            "lock net",
        ],
        body,
    )
    if moved:
        deltas = [(r.finish_before or (0, 0))[0] - (r.finish_after or (0, 0))[0] for r in moved]
        seeds = [(r.finish_before or (0, 0))[1] - (r.finish_after or (0, 0))[1] for r in moved]
        print(
            f"over {len(moved)} lock stashes that reached the playoffs, the median "
            f"playoff-odds move from holding him is {st.median_of(deltas):+.4f} "
            f"and the largest {max(deltas, key=abs):+.4f}; the median move in the "
            f"chance of his team's own most likely seed is {st.median_of(seeds):+.4f} "
            f"and the largest {max(seeds, key=abs):+.4f}.\n"
        )


def section_four(
    rows: Sequence[LockRow], names: dict[int, str], teams: dict[int, str], years: Sequence[int]
) -> None:
    print("== 4. the locks named ==")
    latest = max(years)
    print(f"every {latest} stash taken by a team that was already a lock")
    body = []
    for row in sorted(
        [r for r in rows if r.stash.season == latest and r.group == "lock"],
        key=lambda r: (r.stash.day, r.stash.player),
    ):
        body.append(
            [
                str(row.stash.day),
                teams.get(row.stash.team, str(row.stash.team))[:18],
                names.get(row.stash.player, str(row.stash.player))[:20],
                row.stash.kind,
                str(row.stash.days_out),
                st.two(row.odds or 0.0),
                st.two(row.stake or 0.0),
                st.two(row.p_back_by_playoffs),
                st.two(row.realised_playoff_benefit),
                st.two(row.stash.net),
                st.two(row.lock_net()),
            ]
        )
    st.table(
        [
            "day",
            "team",
            "player",
            "kind",
            "d out",
            "odds",
            "stake",
            "P(back by po)",
            "po benefit",
            "net",
            "lock net",
        ],
        body,
    )

    locks = [r for r in rows if r.group == "lock"]
    print("the best and the worst lock stash in eight seasons, by the lock's net")
    ordered = sorted(locks, key=lambda r: -r.lock_net())
    body = []
    for row in [*ordered[: LISTING // 3], *ordered[-(LISTING // 3) :]]:
        body.append(
            [
                str(row.stash.season),
                str(row.stash.day),
                teams.get(row.stash.team, str(row.stash.team))[:16],
                names.get(row.stash.player, str(row.stash.player))[:20],
                row.stash.kind,
                str(row.stash.days_out),
                st.two(row.odds or 0.0),
                st.two(row.stake or 0.0),
                st.two(row.realised_playoff_benefit),
                st.two(row.stash.net),
                st.two(row.lock_net()),
            ]
        )
    st.table(
        [
            "season",
            "day",
            "team",
            "player",
            "kind",
            "d out",
            "odds",
            "stake",
            "po benefit",
            "net",
            "lock net",
        ],
        body,
    )

    print("a claim like Miller's on a team that had already clinched: every settled lock claim")
    body = []
    for row in sorted(
        [r for r in locks if r.settled and r.stash.kind == "claim"],
        key=lambda r: -r.lock_net(),
    )[:LISTING]:
        body.append(
            [
                str(row.stash.season),
                str(row.stash.day),
                teams.get(row.stash.team, str(row.stash.team))[:16],
                names.get(row.stash.player, str(row.stash.player))[:20],
                str(row.stash.days_out),
                f"${row.stash.bid}",
                st.two(row.odds or 0.0),
                st.two(row.stake or 0.0),
                "yes" if row.in_for_playoff_week_one else "no",
                st.two(row.realised_playoff_benefit),
                st.two(row.stash.net),
                st.two(row.lock_net()),
            ]
        )
    st.table(
        [
            "season",
            "day",
            "team",
            "player",
            "d out",
            "paid",
            "odds",
            "stake",
            "in for po wk 1",
            "po benefit",
            "net",
            "lock net",
        ],
        body,
    )


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def build_season(
    session: Session,
    calendar: st.Calendar,
    boxes: dict[tuple[int, int], tuple[st.Box, ...]],
    held: dict[tuple[int, int, int], set[int]],
    raw_claims: Sequence[tuple[int, int, int, int, int, int | None]],
    records: dict[tuple[int, int, int], float],
    *,
    n_sims: int,
    counterfactual: bool,
) -> tuple[list[LockRow], OddsBook]:
    """One season: the stash population, scored, classified and priced."""
    claims, _counts = st.build_claims(calendar, boxes, held, raw_claims)
    holds = st.build_holds(calendar, boxes, held)
    season_rows = [s for s in [*claims, *holds]]
    wire = st.WireBook(session, calendar)
    with bt.patched_state():
        replay = bt.Replay.load(session, st.league_row(session, calendar))
        for stash in season_rows:
            st.score(replay, calendar, wire, stash)
        book = OddsBook.build(session, calendar, n_sims)
        espn = {
            int(row[0]): int(row[1])
            for row in session.execute(
                text("SELECT id, espn_team_id FROM teams WHERE league_season_id = :ls"),
                {"ls": calendar.league_season_id},
            )
        }
        results = playoff_results(session, calendar)
        playoff_periods = playoff_window(calendar)
        out: list[LockRow] = []
        for stash in season_rows:
            if not stash.stash:
                continue
            his = boxes.get((stash.season, stash.player), ())
            line, minutes, norm = st.pre_injury(his, stash.day)
            stash.healthy = wire.weekly(stash.day, line)
            stash.healthy_minutes = minutes
            stash.healthy_games = norm
            stash.teams = calendar.team_count
            stash.rank = st.rank_on(records, calendar, stash.team, stash.day)
            row = LockRow(stash=stash, pooled_basis=book.pooled_basis)
            number = calendar.period_of(stash.day)
            row.in_playoffs = number is not None and calendar.periods[number][2]
            row.dead_regular_days, row.dead_playoff_days = split_dead_days(calendar, stash)
            first_day = first_playoff_day(calendar, stash.day)
            if first_day is not None and first_day > stash.day:
                row.days_to_playoffs = first_day - stash.day
                row.p_back_by_playoffs = probability_back_within(
                    stash.days_out, row.days_to_playoffs
                )
            elif first_day is not None:
                # The playoff weeks have already begun, so "back by the first
                # playoff week" is not a question the prior can be asked. Those
                # decisions are counted apart and never classified.
                row.days_to_playoffs = 0
                row.p_back_by_playoffs = 1.0
            row.playoff_value, row.playoff_replacement, row.playoff_weeks_held = playoff_value(
                replay, calendar, stash
            )
            if first_day is not None:
                on_roster = first_day in held.get((stash.season, stash.team, stash.player), set())
                row.in_for_playoff_week_one = bool(
                    on_roster and stash.returned is not None and stash.returned <= first_day
                )
            row.playoff_results = tuple(
                results[(stash.team, number)]
                for number in playoff_periods
                if (stash.team, number) in results
            )
            if not row.in_playoffs:
                found = book.on(stash.day)
                team = None if found is None else found.team(espn.get(stash.team, -1))
                if team is not None:
                    row.odds = team.playoff_odds
                    row.stake = 1.0 - max(team.finishes) if team.finishes else None
                    row.bye_odds = team.bye_odds
                else:
                    # A day in no matchup period, or a team the projection did
                    # not carry. Neither happens in this database -- every one
                    # of the 2,111 decisions falls inside a stored period --
                    # but a decision with no odds is not an "out of it" one,
                    # so it is flagged rather than classified.
                    row.unprojectable = True
            out.append(row)

        if counterfactual:
            for row in out:
                if row.group != "lock" or not row.in_for_playoff_week_one:
                    continue
                found = book.on(row.stash.day)
                team_espn = espn.get(row.stash.team)
                if found is None or team_espn is None:
                    continue
                before = found.team(team_espn)
                after_run = book.counterfactual(row.stash.day, team_espn, row.stash.player)
                after = None if after_run is None else after_run.team(team_espn)
                if before is None or after is None:
                    continue
                row.finish_before = (before.playoff_odds, max(before.finishes or [0.0]))
                row.finish_after = (after.playoff_odds, max(after.finishes or [0.0]))
    return out, book


def main() -> int:
    parser = argparse.ArgumentParser(description="What a stash costs a team that is a lock.")
    parser.add_argument("--season", type=int, default=None, help="one season only")
    parser.add_argument("--sims", type=int, default=N_SIMS, help="simulated seasons a run")
    parser.add_argument(
        "--no-counterfactual",
        action="store_true",
        help="skip the roster-off engine runs of section 3",
    )
    args = parser.parse_args()

    started_at = time.time()
    session = st.connect()
    calendars = st.load_calendars(session)
    calendars.pop(2027, None)
    years = [args.season] if args.season else sorted(calendars)

    print("== 0. what was declared, before anything ran ==")
    print(f"a LOCK is playoff odds >= {LOCK_ODDS:.2f} on the decision morning")
    print(
        f"IN THE RACE is {RACE_FLOOR:.2f} to {LOCK_ODDS:.2f}; OUT OF IT is below {RACE_FLOOR:.2f}"
    )
    print("the seeding stake is 1 - max(seed odds) on the same run")
    print(f"the engine: {args.sims} simulated seasons, seed {SEED}")
    print(f"OPENED_PLACE {OPENED_PLACE:.2f}; no hurdle and no constant moves")
    print(
        "the weekly spreads are measured on seasons strictly before each one, era "
        f"adjustment off; {NO_EARLIER_BASIS} has none and uses the pooled basis\n"
    )

    boxes = st.load_boxes(session)
    held = st.load_held(session)
    records = st.load_records(session)
    raw_claims = st.load_claims(session)
    names = {
        int(pid): str(name) for pid, name in session.execute(text("SELECT id, name FROM players"))
    }
    team_names = {
        int(row[0]): str(row[1] or row[0])
        for row in session.execute(text("SELECT id, name FROM teams"))
    }
    print(f"loaded in {time.time() - started_at:.0f}s")

    rows: list[LockRow] = []
    engine_runs = 0
    engine_seconds = 0.0
    for year in years:
        season_rows, book = build_season(
            session,
            calendars[year],
            boxes,
            held,
            raw_claims,
            records,
            n_sims=args.sims,
            counterfactual=not args.no_counterfactual,
        )
        rows.extend(season_rows)
        engine_runs += book.runs
        engine_seconds += book.seconds
        print(
            f"{year}: {len(season_rows)} stash decisions, {book.runs} engine runs "
            f"({book.seconds:.0f}s), {time.time() - started_at:.0f}s elapsed"
        )

    print()
    section_one(rows, years)
    section_two(rows, years)
    inverted(rows)
    section_three(rows, names)
    section_four(rows, names, team_names, years)

    print("== 5. timing ==")
    distinct = len({(r.stash.season, r.stash.day) for r in rows})
    print(
        f"{distinct} distinct decision mornings, {engine_runs} engine runs in "
        f"{engine_seconds:.0f}s ({engine_seconds / max(1, engine_runs):.2f}s a run), "
        f"memoized per day"
    )
    print(f"elapsed {time.time() - started_at:.0f}s")
    return 0


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        raise SystemExit(main())
