#!/usr/bin/env python3
"""Did the man a waiver claim added deserve to stay on the roster?

Usage:
    python scripts/keepers.py                 # full run, prints every table
    python scripts/keepers.py --season 2026   # one season only

WHAT THIS MEASURES

The FAAB study (docs/faab.md) found the wire nearly interchangeable: a pickup
returns 0.06 categories a week, the league's own one-for-one swaps lose 0.56
categories over the next thirty days, and half the top claims go uncontested.
The owner's question of 2026-09-23 is the one nobody had measured: **how often
does a waiver pickup actually deserve to stay on a roster, and how bad is it?**

So every executed add -- a `transactions` row of type WAIVER or FREEAGENT,
status EXECUTED, carrying an ADD item (the canonical tuple is
`app.scoring.replacement.ADD_TYPES`) -- is followed two ways, for the player,
the team and the scoring period the claim is stamped with.

RETENTION (section 1). Days until the adding team dropped him, or the season
ended. A man is HELD on a day when `daily_lineup_slots` carries a row for
(his team, him) on that scoring period -- `BE` included, since a benched man is
still on the roster -- and STARTED when that row's `started` flag is set. The
shares still held at 7, 14, 30 and 60 days and at the season's end; the share
of held days on which he was in a starting slot; the distribution rather than
the mean alone: the median days held, the share dropped within seven days (a
stream) and the share held past thirty (a keep).

**The 30-day figure is RUN-BASED, and it is the number the doc's headline
quotes.** An add is "still on the roster 30 days later" only if the adding team
held him *continuously* from the claim day through day+30. A man dropped and
then re-added by the same team inside those thirty days was not held for
thirty days; counting two spells as one stay reports a stream as a keep. The
loose reading -- the last day he was held by that team minus the claim day, a
gap or a second spell counted as held -- is printed in section 1b beside it,
because it is the number a careless query returns and 2026's is 24.6% against
the headline's 8.6%. A THIRD reading, "cumulative held days over the season,
gaps as held, every later spell by anyone", gives 65.7% on 2026 and is the
number an earlier pass of this study produced; it is neither of the other two.
The doc's Limitations gives all three side by side and names the districts of
the loose set, so no two of them can be mistaken for each other.

WHY CENSORING MATTERS HERE (section 1c). A claim made in the season's last
thirty days cannot be observed for thirty days: it is right-censored at the
season's end, and a season ending while a man is still held *looks* like a
long stay. Section 1c prints every 2026 horizon three ways -- over all
attributed adds (the headline, 8.6%), over only the adds reachable for the
full window (11.0%), and over all adds with the censored ones counted as held
(8.6%, the same to two decimals) -- and section Limitations 2 gives the
like-for-like column for every season.

WHAT DESERVED MEANS (section 2). In the product's own currency, categories a
week, through `scripts/pickups_backtest.py`'s machinery rather than a new
lens. The added man's realised value over the window is the counterfactual the
backtest and `scripts/faab_bids.py` already use: the real matchup is replayed
day by day with the claim **undone** -- the man dropped for him goes back into
the lineup, the added man comes out -- and the sign flipped, so a real claim
is the same quantity the recommender's own moves are scored in
(`Replay.score`; `league_baseline` is the same call over the same population).
It costs about 0.04s a claim because the day-by-day solve is memoized per
period, which is why the whole eight-season census is affordable (6,524 claims
in 276s) and why it is worth reusing rather than approximating.

The hurdle is the wire's replacement on the day he was added: the best free
agent left *before* the claim, in the same currency, floored at
`TYPICAL_PICKUP`. "Above replacement" means his own realised value, week by
week, beat that day's wire number. A claim **deserved** when he beat it in at
least four of the next eight whole weeks (WEEKS_NEEDED of WEEKS). Claims with
fewer than eight whole weeks left in the season are judged on the weeks they
have -- stated in the doc's limitations, because it makes a late claim easier
to call deserved than the same man claimed in November. On 2026 that is nine
of the fifteen deserving claims, which the doc's section 5 works through.

BY WHAT WAS KNOWN AT THE CLAIM (section 3). Retention and deserved shares cut
by his value rank at the time of the claim (`app.pickups.bids.value_rank` and
its `BUCKETS`), by FAAB paid (2026, the only FAAB season), and by month. The
fourth cut -- would the recommender's own bar have passed the move -- cannot
be a replay here: replaying the 2026 recommender is `scripts/pickups_backtest.py`'s
85-to-110-minute job. What `recommender_rows` measures instead is the same
question read backwards from what happened: the claim's own realised value per
week, in the unit the bar is written in, against `SEASON_HURDLE_PAID` /
`SEASON_HURDLE_FREE`. That needs a realised value and a paid/free flag, both of
which exist only for 2026, and the doc says plainly it is a re-reading and not
what the recommender said that morning.

BY LEAGUE SIZE (section 4). The same shares for the ten-, twelve-, fourteen-
and sixteen-team seasons, and the wire's replacement level by size, which is
the 2027 question.

THE MEN WORTH THE MONEY (section 5). The 2026 claims that did deserve to stay:
paid, rank, and whatever marked them beforehand, so far as this data can see it.

REPRODUCE. From the worktree root:

    PYTHONPATH=. /opt/fcp-core/.venv/bin/python scripts/keepers.py

`.env` cannot be sourced by a shell (its line 29 holds an unquoted value with
angle brackets), so `DATABASE_URL` is read out of it in Python when the
environment does not already carry it.

READ-ONLY. Every query is a SELECT; nothing is written anywhere.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this `app` resolves to whichever checkout the interpreter's venv installed,
# which is not this one when a worktree borrows another checkout's .venv -- and
# a census that measures a different copy of the league measures nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# `pickups_backtest` is a sibling script, not an installed module, so mypy
# cannot resolve it -- the same import `scripts/faab_bids.py` carries. The
# ignore is the resolution, and the import is the point: this study reuses the
# backtest's Replay rather than growing a second counterfactual engine.
import pickups_backtest as bt  # type: ignore[import-not-found]
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft.targets import CategoryDistribution
from app.pickups.bids import BUCKETS, free_agents_on, value_rank
from app.pickups.judge import Standard, standard_lens
from app.pickups.projection import per_game_line
from app.scoring.replacement import ADD_TYPES, TYPICAL_PICKUP

#: Slots that mean "not on the roster". ESPN writes `BE` for a rostered man out
#: of the lineup, so a held man always has a row; this filter is belt and
#: braces. Measured 2026: 1,146 of 1,150 adds land on a day that already
#: carries a row for the new man.
HELD_SLOTS_EXCLUDED = ("FA", "IR")

#: Retention horizons, in scoring periods.
HORIZONS = (7, 14, 30, 60)

#: A man dropped inside this many days is a stream.
STREAM_DAYS = 7

#: The "deserved" test: whole weeks it runs over, and how many he must beat the
#: wire in.
WEEKS = 8
WEEKS_NEEDED = 4

#: The 30-day window the repo's other studies score delivered value over.
SEASON_WINDOW = 30

#: FAAB buckets for the 2026 cut, as `docs/faab.md` reads them.
FAAB_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("$0", 0, 0),
    ("$1-5", 1, 5),
    ("$6-20", 6, 20),
    ("$21+", 21, None),
)

#: How many men the "worth the money" section names.
WORTH_LISTING = 15

#: The rest-of-season rank a man must reach to count as a season-long keeper.
TOP_RANK = 100


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
    """A session against `DATABASE_URL`, from the environment or from `.env`."""
    import os

    url = os.environ.get("DATABASE_URL")
    if not url:
        url = load_env(Path(__file__).resolve().parent.parent / ".env")["DATABASE_URL"]
    return make_session_factory(make_engine(url))()


def median_of(values: Sequence[float]) -> float:
    """`statistics.median`, never the half index.

    Every season here has an even number of teams, so `values[len // 2]` picks
    the upper of the two middles and biases every median upward -- the bug
    `docs/roster_churn.md` shipped once.
    """
    return statistics.median(values) if values else 0.0


def share(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def two(value: float) -> str:
    return f"{value:.2f}"


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

    @property
    def last_day(self) -> int:
        return max(final for _first, final, _playoff in self.periods.values())

    def period_of(self, day: int) -> int | None:
        for number, (first, final, _playoff) in self.periods.items():
            if first <= day <= final:
                return number
        return None

    def period_days(self, period: int) -> int:
        first, final, _playoff = self.periods[period]
        return final - first + 1

    def whole_weeks_after(self, day: int, weeks: int) -> tuple[int, ...]:
        """The regular periods touched by the next `weeks` weeks from `day`.

        A period counts when the window covers any of its days; the first is
        the one the claim falls in, so a claim is judged on the period it was
        made in as well as the ones after it, which is what a manager means by
        "the next eight weeks".
        """
        regular = sorted(
            number for number, (_f, _l, playoff) in self.periods.items() if not playoff
        )
        out: list[int] = []
        for number in regular:
            first, final, _playoff = self.periods[number]
            if first <= day + weeks * 7 and final >= day:
                out.append(number)
        return tuple(out)

    def remaining_weeks(self, day: int) -> int:
        """Regular weeks with any day left at or after `day`."""
        return len(self.whole_weeks_after(day, 10**4))


def load_calendars(session: Session) -> dict[int, Calendar]:
    """Every drafted season's shape. 2027 has no periods, so it drops out."""
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
        # A period with no window is one the season never reached (2027's
        # playoff periods are stored with a NULL first day), so it is not a
        # calendar entry and nothing may be measured on it.
        periods = {
            int(period): (int(first), int(final), bool(playoff))
            for period, first, final, playoff in raw
            if first is not None and final is not None
        }
        if not periods:
            continue
        out[int(ls.season)] = Calendar(
            year=int(ls.season),
            league_season_id=int(ls.id),
            team_count=int(ls.team_count or 0),
            periods=periods,
        )
    return out


# ---------------------------------------------------------------------------
# the claims, and what the lineup rows say about them
# ---------------------------------------------------------------------------


@dataclass
class Raw:
    """One executed add, before its value is scored."""

    season: int
    day: int
    team: int
    player: int
    kind: str
    bid: int
    transaction_id: int
    dropped: int | None
    # -- retention, filled by `retention()` --
    days: int = 0
    censored: bool = False
    started: int = 0
    run_30: bool = False
    loose_days: int = 0
    # -- value, filled by `score_claims()` --
    #: Realised value over the 30-day window, categories (claim undone).
    value_30: float = 0.0
    #: Value per whole period the 30-day window touches, in period order.
    weekly_30: tuple[float, ...] = ()
    #: Periods in `weekly_30`, as (period number, first day) pairs.
    weekly_30_periods: tuple[int, ...] = ()
    #: The man dropped for him, scored over the same window the same way.
    dropped_value: float | None = None
    #: The wire's replacement level on the claim day, categories a week.
    replacement: float = 0.0
    # -- what was knowable --
    rank: int = 0
    bucket: str = ""
    ros_rank: int = 0
    readded: bool = False

    @property
    def windows_30(self) -> int:
        """Whole periods the 30-day window touched."""
        return len(self.weekly_30)

    @property
    def beat_30(self) -> int:
        """Periods in the 30-day window he beat his claim day's wire in."""
        return sum(1 for value in self.weekly_30 if value > self.replacement)

    @property
    def deserved_30(self) -> bool:
        """Above replacement for at least four of the next eight weeks.

        A claim that cannot span eight whole weeks is judged on the periods it
        has, and `docs/keepers.md`'s limitations says so: a late claim faces a
        lower bar than the same claim made in November.
        """
        return self.beat_30 >= min(WEEKS_NEEDED, self.windows_30)

    @property
    def top_100(self) -> bool:
        return 0 < self.ros_rank <= TOP_RANK

    @property
    def worse_than_dropped(self) -> bool:
        return self.dropped_value is not None and self.dropped_value > self.value_30


def load_claims(session: Session) -> list[Raw]:
    """Every executed add, with the man it dropped when it dropped one."""
    from app.db.models import Transaction, TransactionItem

    added = session.execute(
        select(
            LeagueSeason.season,
            Transaction.scoring_period,
            Transaction.team_id,
            TransactionItem.player_id,
            Transaction.type,
            Transaction.bid_amount,
            Transaction.id,
        )
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .join(LeagueSeason, LeagueSeason.id == Transaction.league_season_id)
        .where(
            Transaction.status == "EXECUTED",
            Transaction.type.in_(ADD_TYPES),
            TransactionItem.item_type == "ADD",
            TransactionItem.to_team_id.is_not(None),
        )
        .order_by(LeagueSeason.season, Transaction.scoring_period, Transaction.id)
    ).all()

    dropped: dict[int, list[int]] = defaultdict(list)
    for tx, player in session.execute(
        text(
            "SELECT ti.transaction_id, ti.player_id FROM transaction_items ti"
            " WHERE ti.item_type = 'DROP'"
        )
    ):
        dropped[int(tx)].append(int(player))

    out: list[Raw] = []
    for season, day, team, player, kind, bid, tx in added:
        men = dropped.get(int(tx), [])
        out.append(
            Raw(
                season=int(season),
                day=int(day),
                team=int(team),
                player=int(player),
                kind=str(kind),
                bid=int(bid or 0),
                transaction_id=int(tx),
                dropped=men[0] if len(men) == 1 else None,
            )
        )
    return out


def load_held(session: Session) -> dict[tuple[int, int, int], dict[int, bool]]:
    """(season, team, player) -> {day: started} for every rostered man-day."""
    held: dict[tuple[int, int, int], dict[int, bool]] = defaultdict(dict)
    for season, team, player, day, started in session.execute(
        text(
            "SELECT ls.season, dls.team_id, dls.player_id, dls.scoring_period,"
            " dls.started FROM daily_lineup_slots dls"
            " JOIN teams tt ON tt.id = dls.team_id"
            " JOIN league_seasons ls ON ls.id = tt.league_season_id"
            " WHERE dls.slot <> ALL(:unheld)"
        ),
        {"unheld": list(HELD_SLOTS_EXCLUDED)},
    ):
        held[(int(season), int(team), int(player))][int(day)] = bool(started)
    return held


def runs_of(days: Sequence[int]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive scoring periods."""
    out: list[tuple[int, int]] = []
    start = prev = days[0]
    for day in days[1:]:
        if day == prev + 1:
            prev = day
            continue
        out.append((start, prev))
        start = prev = day
    out.append((start, prev))
    return out


def retention(
    claim: Raw,
    held: dict[tuple[int, int, int], dict[int, bool]],
    calendars: dict[int, Calendar],
) -> bool:
    """Fill the claim's retention fields; False when it has no roster run.

    The run the claim opened is the consecutive held run that *begins* on the
    claim day, or the next run beginning after it when the claim day itself
    carries no row (10 in 1,150 in 2026). A run that began before the claim
    day is not this claim's stay -- the man was already held -- and a claim
    whose man never appears again is dropped from the census and counted.
    """
    table = held.get((claim.season, claim.team, claim.player))
    if not table:
        return False
    days = sorted(table)
    runs = runs_of(days)
    covering = next((r for r in runs if r[0] <= claim.day <= r[1]), None)
    if covering is None:
        covering = next(((a, b) for a, b in runs if a > claim.day), None)
    if covering is None:
        return False
    run_start, run_end = covering
    if run_start < claim.day:
        return False

    end = calendars[claim.season].last_day
    claim.days = run_end - claim.day + 1
    claim.censored = run_end >= end
    claim.started = sum(1 for d in range(claim.day, run_end + 1) if table.get(d))
    claim.run_30 = claim.days >= SEASON_WINDOW + 1
    claim.loose_days = days[-1] - claim.day + 1
    return True


# ---------------------------------------------------------------------------
# the wire's replacement level on a day, in the currency
# ---------------------------------------------------------------------------


class WireBook:
    """The day's wire, valued in categories a week, built lazily per day.

    A free agent is a man `app.pickups.bids.free_agents_on` returns -- the wire
    definition `docs/pickups.md` section 4.6 and `scripts/waiver_value.py` both
    use. His value is `app.pickups.judge.standard_lens().value` of his per-game
    line, which is `marginal` against this season's opponent distributions: the
    same currency `pickup_values` measures an add in, so the hurdle and the
    realised value are comparable. `app.pickups.stream.weight` is deliberately
    not used -- it is an ordinal seating aid (counts over spreads) and not a
    number of categories.

    The pool is the wire *before* the claim, which is what makes the rank and
    the bucket the ones a manager could have seen that morning. A claimed man
    is therefore on it, which is correct here: this study asks what the wire
    held on the day of the claim, not what it held after the claim resolved.

    **The lens is memoized by period length.** `standard_lens` rebuilds the
    season's `category_distributions` and its average team line on every call,
    and it only reads them, so the two together depend on the season and the
    length of the period the day falls in -- seven days, mostly, six for the
    opener and fourteen for the All-Star fortnight. Measured 2026: 6.25s a
    call, which over the ~140 distinct claim days of a season is fifteen
    minutes and over eight seasons two hours. Caching the two per length
    brings a whole season in under a minute and cannot change an answer,
    because the callers pass the same `distributions` object in.
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

    def _lens(self, day: int) -> Standard:
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
            lens = self._lens(day)
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

    def rank_of(self, day: int, player: int) -> int:
        return value_rank(player, self.pool(day))

    def bucket_of(self, day: int, player: int) -> str:
        rank = self.rank_of(day, player)
        for label, first, last in BUCKETS:
            if rank >= first and (last is None or rank <= last):
                return label
        return BUCKETS[-1][0]


def league_row(session: Session, calendar: Calendar) -> LeagueSeason:
    return session.scalars(
        select(LeagueSeason).where(LeagueSeason.id == calendar.league_season_id)
    ).one()


def score_claims(
    session: Session,
    calendar: Calendar,
    claims: Sequence[Raw],
) -> None:
    """Fill every claim's realised value, per period and over the window.

    One `Replay` for the season, then two calls a claim. The claim is scored
    **undone and negated** -- `bt.Replay.score(team, day, [added], [dropped])`
    with the sign flipped -- which is `scripts/pickups_backtest.py`'s
    `league_baseline` frame: the man dropped for him goes back into the lineup
    and the added man comes out, so a real claim is the same quantity the
    recommender's own moves are scored in. Every call is memoized per period
    inside `Replay`, which is why a whole season of claims costs seconds.

    The per-period split is what the "deserved" test reads: a claim's weeks are
    the periods the 30-day window touches, and his value in each is compared
    against his claim day's wire replacement level.
    """
    if not claims:
        return
    with bt.patched_state():
        replay = bt.Replay.load(session, league_row(session, calendar))
        for claim in claims:
            windows = replay.touched(claim.day, claim.day + SEASON_WINDOW)
            if not windows:
                continue
            added = [claim.player]
            dropped = [claim.dropped] if claim.dropped is not None else []
            # The claim undone: take the added man out, put the dropped man
            # back -- the same call `league_baseline` makes, sign flipped.
            end = claim.day + SEASON_WINDOW
            values = [
                -replay.delta(claim.team, window, claim.day, end, added, dropped)
                for window in windows
            ]
            claim.weekly_30 = tuple(values)
            claim.weekly_30_periods = tuple(window.period for window in windows)
            claim.value_30 = sum(values)
            if claim.dropped is not None:
                # The dropped man over the same window, scored the other way
                # round: drop him (he leaves), add nobody. Read as what he was
                # worth to the roster that let him go.
                claim.dropped_value = sum(
                    -replay.delta(claim.team, window, claim.day, end, [claim.dropped], [])
                    for window in windows
                )


# ---------------------------------------------------------------------------
# the rest-of-season top 100
# ---------------------------------------------------------------------------


def load_season_ranks(session: Session) -> dict[int, dict[int, int]]:
    """season -> {player: rank by season-total value}, best first.

    `player_season_stats` totals (`kind = 'total'`) for the season, scaled to a
    week and run through `marginal` against that season's distributions -- the
    same currency as everything else here, so "top 100" means the top hundred
    by what the product values and not by a raw points total. The claim's own
    `player_season_stats` row is the season's whole production, so a claim made
    in week two is judged on weeks it could not have been on the roster for;
    the doc says so, and the reading is only ever used as "did this man end the
    season as a top-100 player", which is what the brief asks.
    """
    from app.db.models import MatchupPeriod
    from app.draft.targets import category_distributions
    from app.scoring.lines import CategoryLine
    from app.scoring.value import marginal

    out: dict[int, dict[int, int]] = {}
    for ls in session.scalars(select(LeagueSeason).order_by(LeagueSeason.season)):
        rows = session.execute(
            text(
                "SELECT ps.player_id, ps.points, ps.rebounds, ps.assists,"
                " ps.steals, ps.blocks, ps.three_pointers_made, ps.turnovers"
                " FROM player_season_stats ps"
                " WHERE ps.season = :season AND ps.kind = 'total'"
            ),
            {"season": int(ls.season)},
        ).all()
        if not rows:
            continue
        try:
            distributions = category_distributions(session, ls)
        except Exception:
            continue
        days = session.scalar(
            select(MatchupPeriod.final_scoring_period - MatchupPeriod.first_scoring_period + 1)
            .where(MatchupPeriod.league_season_id == ls.id, MatchupPeriod.is_playoff.is_(False))
            .order_by(MatchupPeriod.period)
        )
        weeks = max(1.0, float(days or 7) / 7.0)
        values: dict[int, float] = {}
        for row in rows:
            counts = {
                "PTS": float(row[1] or 0),
                "REB": float(row[2] or 0),
                "AST": float(row[3] or 0),
                "STL": float(row[4] or 0),
                "BLK": float(row[5] or 0),
                "3PM": float(row[6] or 0),
                "TO": float(row[7] or 0),
            }
            try:
                values[int(row[0])] = marginal(
                    CategoryLine(), CategoryLine(counts, 1).scaled(1.0 / weeks), distributions
                )
            except Exception:
                values[int(row[0])] = 0.0
        ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
        out[int(ls.season)] = {player: i + 1 for i, (player, _v) in enumerate(ordered)}
    return out


def load_month_of(session: Session) -> dict[int, dict[int, str]]:
    """season -> {scoring period: month label}, from the stored game dates.

    A scoring period is not a calendar date, so the month a claim fell in is
    read off `player_game_stats.game_date`: the modal month of the boxes played
    on that period. Ten 2026 days carry no box scores at all and fall back to
    the neighbouring period's month, which is the best available reading.
    """
    out: dict[int, dict[int, str]] = defaultdict(dict)
    for season, period, month in session.execute(
        text(
            "SELECT pgs.season, pgs.scoring_period, to_char(pgs.game_date, 'YYYY-MM')"
            " FROM player_game_stats pgs"
            " WHERE pgs.played AND pgs.game_date IS NOT NULL"
            " GROUP BY 1, 2, 3"
        )
    ):
        out[int(season)][int(period)] = str(month)
    return out


def month_of(
    calendars: dict[int, Calendar],
    months: dict[int, dict[int, str]],
    claim: Raw,
) -> str:
    """The month `claim` was made in, or its season and period when unknown."""
    period = calendars[claim.season].period_of(claim.day)
    if period is not None:
        for candidate in range(period, 0, -1):
            found = months.get(claim.season, {}).get(candidate)
            if found:
                return found
    return f"{claim.season} p{period}"


def recommender_rows(calendar: Calendar | None, ranked: Sequence[Raw]) -> list[list[str]] | None:
    """Retention and deserved shares by the bar the recommender shipped.

    The full backtest replays 44 decision points a team and takes 85 to 110
    minutes (`docs/pickups_backtest.md`), which is why it is a document of its
    own and not a section of this one. What can be asked here is the same
    question the recommender's own hurdle asks of a claim: would the move have
    cleared the bar it was published with? Since 2026-09-21 that is
    `SEASON_HURDLE_PAID` 0.20 categories a week for a claim that costs FAAB and
    `SEASON_HURDLE_FREE` 0.10 for one that does not, applied to the move's net
    spread over the weeks it covers (`app.pickups.judge.Judgement.per_week`).

    `per_week` here is the claim's realised value per week -- the same quantity
    the hurdle gates, read backwards from what happened. So this is "would the
    bar have passed a move that turned out like this one", which is the honest
    answer available without the replay, and not "what did the recommender say
    that morning". The doc says which of the two it is.
    """
    if calendar is None:
        return None
    from app.pickups.season import SEASON_HURDLE_FREE, SEASON_HURDLE_PAID

    sub = [c for c in ranked if c.season == calendar.year and c.windows_30]
    if not sub:
        return None
    rows = []
    # `bar` is a rate per week (`app.pickups.judge.Judgement.per_week`), so the
    # realised net the claim is compared against is `value_30 / windows_30`:
    # categories a week, the unit every hurdle in the repo is written in.
    bars = (
        ("paid (0.20/wk)", SEASON_HURDLE_PAID, True),
        ("free (0.10/wk)", SEASON_HURDLE_FREE, False),
    )
    for label, bar, paid in bars:
        group = [c for c in sub if (c.bid > 0) == paid]
        passing = [c for c in group if c.value_30 / c.windows_30 >= bar]
        rows.append(
            [
                label,
                str(len(group)),
                str(len(passing)),
                share(sum(1 for c in passing if c.run_30), len(passing) or 1),
                share(sum(1 for c in passing if c.deserved_30), len(passing) or 1),
            ]
        )
    return rows


def load_readds(session: Session) -> dict[int, list[tuple[int, int, int]]]:
    """season -> [(day, team, player)] for every executed add, re-adds included."""
    out: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for season, day, team, player in session.execute(
        text(
            "SELECT ls.season, t.scoring_period, t.team_id, ti.player_id"
            " FROM transactions t"
            " JOIN transaction_items ti ON ti.transaction_id = t.id"
            " AND ti.item_type = 'ADD'"
            " JOIN league_seasons ls ON ls.id = t.league_season_id"
            " WHERE t.status = 'EXECUTED' AND t.type IN ('WAIVER','FREEAGENT')"
        )
    ):
        out[int(season)].append((int(day), int(team), int(player)))
    return out


def load_names(session: Session) -> dict[int, str]:
    """player id -> name, for the `worth the money` table."""
    from app.db.models import Player

    return {int(p.id): str(p.name or p.id) for p in session.scalars(select(Player))}


def load_minutes_trend(session: Session) -> dict[tuple[int, int], tuple[float, float]]:
    """(season, player) -> (mean minutes over his last five games, prior five).

    Read from `player_game_stats` only, so it is what a manager could see on
    the morning of the claim: his box scores up to the claim's own period.
    A man with fewer than six games played gets a zero for the earlier five.
    """
    played: dict[tuple[int, int], list[tuple[int, float]]] = defaultdict(list)
    for season, player, period, minutes in session.execute(
        text(
            "SELECT pgs.season, pgs.player_id, pgs.scoring_period, pgs.minutes"
            " FROM player_game_stats pgs WHERE pgs.played"
        )
    ):
        played[(int(season), int(player))].append((int(period), float(minutes or 0.0)))
    return {
        key: (
            statistics.fmean([m for _d, m in sorted(games)[-5:]]),
            statistics.fmean([m for _d, m in sorted(games)[-10:-5]] or [0.0]),
        )
        for key, games in played.items()
    }


def worth_rows(
    session: Session,
    claims: Sequence[Raw],
    worthy: Sequence[Raw],
) -> list[list[str]]:
    """The 2026 claims that deserved to stay, with what marked them beforehand."""
    del claims
    names = load_names(session)
    trends = load_minutes_trend(session)
    rows = []
    for claim in sorted(worthy, key=lambda c: (-c.value_30, c.day))[:WORTH_LISTING]:
        recent, prior = trends.get((claim.season, claim.player), (0.0, 0.0))
        rows.append(
            [
                str(claim.day),
                names.get(claim.player, str(claim.player))[:22],
                str(claim.bid),
                f"#{claim.rank}",
                str(claim.days),
                two(claim.value_30),
                str(claim.ros_rank),
                f"{recent:.1f}",
                f"{prior:.1f}",
            ]
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Waiver retention and keepers.")
    parser.add_argument("--season", type=int, default=None, help="one season only")
    parser.add_argument("--skip-values", action="store_true", help="retention only")
    args = parser.parse_args()

    started_at = time.time()
    session = connect()
    calendars = load_calendars(session)
    # 2027 has not been drafted: its periods are stored but no game has been
    # played and no add made, so nothing about it is measurable here.
    calendars.pop(2027, None)
    years = [args.season] if args.season else sorted(calendars)
    print(f"seasons: {[(y, calendars[y].team_count) for y in years]}")

    held = load_held(session)
    claims = [c for c in load_claims(session) if c.season in set(years)]
    print(f"executed adds loaded: {len(claims)}")

    # ---- 0. the lens reproduces, before anything is measured --------------
    print("\n== 0. the currency reproduces (`pickup_values`, the 14-day window) ==")
    from app.scoring.replacement import pickup_values
    from app.scoring.season import SeasonBook

    doc = {
        2019: (599, 0.113),
        2020: (543, 0.128),
        2021: (758, 0.096),
        2022: (648, 0.086),
        2023: (475, 0.091),
        2024: (789, 0.068),
        2025: (924, 0.062),
        2026: (973, 0.072),
    }
    rows = []
    for year in years:
        values = pickup_values(SeasonBook.load(session, year, None))
        rows.append(
            [
                str(year),
                str(len(values)),
                str(doc[year][0]),
                two(median_of(values)),
                two(doc[year][1]),
            ]
        )
    table(["season", "n", "doc n", "median", "doc med"], rows)

    # ---- 1. retention ------------------------------------------------------
    print("== 1. retention (run-based 30-day rule) ==")
    kept: list[Raw] = []
    unattributed = 0
    for claim in claims:
        if retention(claim, held, calendars):
            kept.append(claim)
        else:
            unattributed += 1
    print(f"adds {len(claims)}; attributed to a roster run {len(kept)}; not {unattributed}")

    def retention_rows(subset: Sequence[Raw]) -> list[list[str]]:
        out = []
        for year in sorted({c.season for c in subset}):
            sub = [c for c in subset if c.season == year]
            d = [c.days for c in sub]
            started = sum(c.started for c in sub)
            held_days = sum(c.days for c in sub)
            out.append(
                [
                    str(year),
                    str(len(sub)),
                    two(median_of([float(x) for x in d])),
                    two(statistics.fmean(d)),
                    share(sum(1 for c in sub if c.days >= 7), len(sub)),
                    share(sum(1 for c in sub if c.days >= 14), len(sub)),
                    share(sum(1 for c in sub if c.run_30), len(sub)),
                    share(sum(1 for c in sub if c.days >= 60), len(sub)),
                    share(sum(1 for c in sub if c.days <= STREAM_DAYS), len(sub)),
                    share(sum(1 for c in sub if c.censored), len(sub)),
                    share(started, held_days),
                ]
            )
        return out

    header = [
        "season",
        "n",
        "med d",
        "mean",
        ">=7d",
        ">=14d",
        ">=30d run",
        ">=60d",
        "<=7d",
        "censored",
        "started",
    ]
    table(header, retention_rows(kept))

    pooled = [c for c in kept]
    d = [c.days for c in pooled]
    print(
        f"pooled  n={len(pooled)}  median {median_of([float(x) for x in d]):.1f} days  "
        f"mean {statistics.fmean(d):.1f}  "
        f">=30d run {share(sum(1 for c in pooled if c.run_30), len(pooled))}  "
        f"never considered by 7d {share(sum(1 for c in pooled if c.days <= 7), len(pooled))}"
    )
    print()

    # ---- 1b. the loose reading --------------------------------------------
    rows = []
    for year in sorted({c.season for c in kept}):
        sub = [c for c in kept if c.season == year]
        rows.append(
            [
                str(year),
                str(len(sub)),
                two(median_of([float(c.loose_days) for c in sub])),
                share(sum(1 for c in sub if c.loose_days > SEASON_WINDOW), len(sub)),
                share(sum(1 for c in sub if c.days >= 7), len(sub)),
                share(sum(1 for c in sub if c.run_30), len(sub)),
            ]
        )
    table(
        ["season", "n", "med loose", "loose >30", "run >=7d", "run-based >=30d"],
        rows,
    )
    print(
        f"pooled n={len(kept)}: median loose "
        f"{two(median_of([float(c.loose_days) for c in kept]))} days, loose >30 "
        f"{share(sum(1 for c in kept if c.loose_days > SEASON_WINDOW), len(kept))}\n"
    )

    # ---- 1c. the censoring census -----------------------------------------
    rows = []
    for year in sorted({c.season for c in kept}):
        sub = [c for c in kept if c.season == year]
        reach = [c for c in sub if c.day + SEASON_WINDOW <= calendars[c.season].last_day]
        rows.append(
            [
                str(year),
                str(len(sub)),
                str(len(reach)),
                share(sum(1 for c in sub if c.censored), len(sub)),
                share(sum(1 for c in reach if c.run_30), len(reach)),
                share(sum(1 for c in sub if c.run_30), len(sub)),
            ]
        )
    table(
        [
            "season",
            "n",
            "can reach 30d",
            "censored at end",
            ">=30d of reachable",
            ">=30d of all",
        ],
        rows,
    )

    # ---- 2. deserved to stay ----------------------------------------------
    if not args.skip_values:
        print("== 2. did he deserve to stay ==")
        values_started = time.time()
        for year in years:
            season_claims = [c for c in kept if c.season == year and c.dropped is not None]
            score_claims(session, calendars[year], season_claims)
            wire = WireBook(session, calendars[year])
            for claim in season_claims:
                claim.replacement = wire.replacement(claim.day)
        scored = sum(1 for c in kept if c.dropped is not None)
        print(f"scored {scored} claims in {time.time() - values_started:.0f}s\n")

        ranked = [c for c in kept if c.dropped is not None]
        rows = []
        for year in years:
            sub = [c for c in ranked if c.season == year]
            if not sub:
                continue
            rows.append(
                [
                    str(year),
                    str(len(sub)),
                    share(sum(1 for c in sub if c.deserved_30), len(sub)),
                    share(sum(1 for c in sub if c.value_30 > 0), len(sub)),
                    two(median_of([c.value_30 for c in sub])),
                    two(statistics.fmean([c.value_30 for c in sub])),
                    two(median_of([c.replacement for c in sub])),
                ]
            )
        table(
            ["season", "n", "deserved", "30d > 0", "med 30d", "mean 30d", "med wire"],
            rows,
        )
        pooled = ranked
        deserved = share(sum(1 for c in pooled if c.deserved_30), len(pooled))
        above = share(sum(1 for c in pooled if c.value_30 > 0), len(pooled))
        print(
            f"pooled n={len(pooled)}: deserved {deserved}, "
            f"30-day value above zero {above}, "
            f"median 30-day value {two(median_of([c.value_30 for c in pooled]))}\n"
        )

        # -- 2b. did he finish the season as a top-100 man? --
        season_ranks = load_season_ranks(session)
        for claim in ranked:
            claim.ros_rank = season_ranks.get(claim.season, {}).get(claim.player, 0)
        rows = []
        for year in years:
            sub = [c for c in ranked if c.season == year]
            if not sub:
                continue
            rows.append(
                [
                    str(year),
                    str(len(sub)),
                    share(sum(1 for c in sub if c.top_100), len(sub)),
                    share(
                        sum(1 for c in sub if c.top_100 and c.deserved_30),
                        sum(1 for c in sub if c.top_100) or 1,
                    ),
                ]
            )
        table(["season", "n", "top 100 at season end", "of those, deserved"], rows)
        print(
            f"pooled n={len(ranked)}: top 100 at season end "
            f"{share(sum(1 for c in ranked if c.top_100), len(ranked))}\n"
        )

        # -- 2c. was the league still asking for him? --
        readds = load_readds(session)
        # A man is "wanted again" when ANY team adds him on a day *after* the
        # claim that dropped his first stay started -- which is his own later
        # stays and other managers', the loose reading the brief asks for.
        later_adds: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for season, entries in readds.items():
            for day, team, player in entries:
                later_adds[(season, player)].append((day, team))
        for claim in ranked:
            claim.readded = any(
                day > claim.day for day, _team in later_adds.get((claim.season, claim.player), [])
            )
        rows = []
        for year in years:
            sub = [c for c in ranked if c.season == year]
            if not sub:
                continue
            re_added = [c for c in sub if c.readded]
            rows.append(
                [
                    str(year),
                    str(len(sub)),
                    share(len(re_added), len(sub)),
                    share(sum(1 for c in re_added if c.deserved_30), len(re_added) or 1),
                ]
            )
        table(["season", "n", "re-added by ANY team", "of those, deserved"], rows)
        wanted_back = sum(1 for c in ranked if c.readded)
        print(
            f"pooled n={len(ranked)}: re-added by any team "
            f"{share(wanted_back, len(ranked))}, of those deserved "
            f"{share(sum(1 for c in ranked if c.readded and c.deserved_30), wanted_back or 1)}"
            "\n"
        )

        # -- 2d. was he worse than the man dropped for him? --
        rows = []
        for year in years:
            sub = [c for c in ranked if c.season == year]
            if not sub:
                continue
            rows.append(
                [
                    str(year),
                    str(len(sub)),
                    share(sum(1 for c in sub if c.worse_than_dropped), len(sub)),
                    two(median_of([c.dropped_value or 0.0 for c in sub])),
                ]
            )
        table(["season", "n", "worse than the man dropped", "med dropped 30d"], rows)
        print(
            f"pooled n={len(ranked)}: worse than the man dropped "
            f"{share(sum(1 for c in ranked if c.worse_than_dropped), len(ranked))}"
            "\n"
        )

    # ---- 3. by what was known at the claim --------------------------------
    if not args.skip_values:
        print("== 3. by what was known at the claim ==")
        wire_ranks: dict[int, WireBook] = {
            year: WireBook(session, calendars[year]) for year in years
        }
        for claim in ranked:
            book = wire_ranks[claim.season]
            claim.rank = book.rank_of(claim.day, claim.player)
            claim.bucket = book.bucket_of(claim.day, claim.player)

        rows = []
        for label, _first, _last in BUCKETS:
            sub = [c for c in ranked if c.bucket == label]
            rows.append(
                [
                    label,
                    str(len(sub)),
                    share(sum(1 for c in sub if c.run_30), len(sub)),
                    share(sum(1 for c in sub if c.days <= STREAM_DAYS), len(sub)),
                    share(sum(1 for c in sub if c.deserved_30), len(sub)),
                    share(sum(1 for c in sub if c.top_100), len(sub)),
                ]
            )
        table(
            ["rank bucket", "n", "held >=30d", "dropped <=7d", "deserved", "top 100"],
            rows,
        )

        # by FAAB paid, 2026 only (the only FAAB season)
        rows = []
        year_2026 = [c for c in ranked if c.season == 2026]
        for label, first, last in FAAB_BUCKETS:
            sub = [c for c in year_2026 if c.bid >= first and (last is None or c.bid <= last)]
            rows.append(
                [
                    label,
                    str(len(sub)),
                    share(sum(1 for c in sub if c.run_30), len(sub)),
                    share(sum(1 for c in sub if c.deserved_30), len(sub)),
                    share(sum(1 for c in sub if c.top_100), len(sub)),
                ]
            )
        table(["FAAB (2026)", "n", "held >=30d", "deserved", "top 100"], rows)

        # by month of the claim
        months = load_month_of(session)
        rows = []
        for month in sorted({month_of(calendars, months, c) for c in ranked}):
            sub = [c for c in ranked if month_of(calendars, months, c) == month]
            rows.append(
                [
                    month,
                    str(len(sub)),
                    share(sum(1 for c in sub if c.run_30), len(sub)),
                    share(sum(1 for c in sub if c.deserved_30), len(sub)),
                ]
            )
        table(["month", "n", "held >=30d", "deserved"], rows)

        # the recommender's own bar: 2026 only, and said so
        verdict = recommender_rows(calendars.get(2026), ranked)
        if verdict is None:
            print("the recommender's own bar: cannot be read before 2026\n")
        else:
            table(["the shipped bar", "n", "passed", "held >=30d", "deserved"], verdict)

    # ---- 4. by league size -------------------------------------------------
    print("== 4. by league size ==")
    rows = []
    for size in sorted({calendars[y].team_count for y in years}):
        subset_years = [y for y in years if calendars[y].team_count == size]
        sub = [c for c in kept if c.season in subset_years]
        rows.append(
            [
                str(size),
                str(len(subset_years)),
                str(len(sub)),
                share(sum(1 for c in sub if c.run_30), len(sub)),
                share(sum(1 for c in sub if c.days <= STREAM_DAYS), len(sub)),
                share(sum(1 for c in sub if c.deserved_30), len(sub)),
                two(median_of([c.replacement for c in sub])),
                two(median_of([c.value_30 for c in sub])),
            ]
        )
    table(
        ["teams", "seasons", "n", "held >=30d", "dropped <=7d", "deserved", "med wire", "med 30d"],
        rows,
    )

    # ---- 5. the men worth the money ---------------------------------------
    if not args.skip_values:
        print("== 5. the 2026 men worth the money ==")
        worth = sorted(
            [c for c in ranked if c.season == 2026 and c.deserved_30],
            key=lambda c: (-c.value_30, c.day),
        )
        rows = []
        for claim in worth[:WORTH_LISTING]:
            rows.append(
                [
                    str(claim.day),
                    str(claim.team),
                    str(claim.player),
                    str(claim.bid),
                    f"#{claim.rank}",
                    claim.bucket,
                    str(claim.days),
                    two(claim.value_30),
                    str(claim.ros_rank),
                ]
            )
        table(
            ["day", "team", "player", "paid", "rank", "bucket", "held", "30d value", "ros rank"],
            rows,
        )
        table(
            ["day", "player", "paid", "rank", "held d", "30d", "ros rank", "mp last5", "mp prior5"],
            worth_rows(session, ranked, worth),
        )
        print(
            f"2026 deserved claims: {len(worth)} of {sum(1 for c in ranked if c.season == 2026)}\n"
        )

    print(f"elapsed {time.time() - started_at:.0f}s")
    return 0


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        raise SystemExit(main())
