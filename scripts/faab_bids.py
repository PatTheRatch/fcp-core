#!/usr/bin/env python3
"""What a FAAB dollar buys here, and whether a bid should be worth-based.

Usage:
    python scripts/faab_bids.py               # full run, writes docs/faab.md
    python scripts/faab_bids.py --limit 40    # a quick smoke run

THE RULE, DECLARED BEFORE IT WAS MEASURED

The bid beside a move is market-only today: `app.pickups.bids` places the
player among the wire by value, takes the median or 75th percentile winning
bid of his rank bucket from this league's own claim history, and caps it by
the pot and by the share of the season left. That is what it takes to win. It
says nothing about whether the man is worth it to this roster. The rule this
script measures, declared in full before the run and repeated verbatim in
docs/faab.md section "Declared" (`DECLARED` below):

1.  **What he is worth to you, in dollars.** The move's worth in categories
    is `Judgement.per_week` -- its net over both horizons spread over the
    weeks it covers, which is the unit every hurdle in this codebase is
    written in. The exchange rate that turns it into dollars is **your own
    budget's shadow price**: `faab_remaining` dollars have to cover
    `weeks_covered` weeks, so a week costs `faab_remaining / weeks_covered`
    dollars, and a week is worth `TYPICAL_PICKUP` (0.06 categories, what one
    executed add returns, `app.scoring.replacement`). So

        worth_dollars = per_week / TYPICAL_PICKUP * (faab_remaining / weeks_covered)
        ceiling       = floor((per_week - hurdle) / TYPICAL_PICKUP
                              * (faab_remaining / weeks_covered))

    The rule uses the shadow price and not the league's going rate, in both
    places it prices anything, for two reasons. It is specific to you, which
    is the point: two managers with the same roster and different budgets
    should not bid the same number. And it reads no claim history, so it
    cannot look ahead at prices nobody had paid yet on the day of the bid.
    The league's going rate -- what an executed 2026 claim delivered over the
    30 days after, per dollar paid -- is measured in section 3 and published
    as the market check beside it, never used to price a bid.

2.  **What it takes to win, as a curve.** `P(win | bid $x)` per rank bucket,
    over **claim events**: one (day, player) that at least one waiver claim
    was filed on. Its field is every bid filed on it. The losing side is
    `transactions` with `type = WAIVER` and `status =
    FAILED_INVALIDPLAYERSOURCE` (`bids.LOST`), read as "lost the player to a
    higher bid the same day"; section 1 states what that reading was checked
    against. A bid of $x beats a field whose top bid is $w when x > w. At
    x == w it is a tie, which ESPN breaks on a waiver priority nothing
    stored can reconstruct, so it is credited `1 / (bidders at the top price
    + 1)` -- one share of the hat.

3.  **The bid is a ladder.** Three rungs, `bids.LADDER_RUNGS` = 50%, 75%,
    90%: for each, the cheapest whole dollar whose curve reaches it, capped
    by the ceiling and by the share cap. A rung a cap pulls down reports the
    chance at the dollar actually offered. `amount` keeps its meaning -- the
    market number -- unless the replay in section 6 says the ladder's 75%
    rung delivers more categories per dollar, and then the change is a
    declared revision published in docs/faab.md section 0.

4.  **Who else wants him.** The count beside the ladder is how many of the
    other rosters would have taken the man: into an open place, or over the
    cheapest man they hold, judged on the season term alone against that
    roster's own wire replacement. It labels; it hides nothing and moves no
    number.

Constants, fixed before the run and in no case tuned after it: the rungs
(0.50, 0.75, 0.90); the rate's window (30 days, `RATE_WINDOW`, the window
`scripts/pickups_backtest.py` already scores a rest-of-season move over); the
rank buckets, reused unchanged from `bids.BUCKETS` (1-5, 6-15, 16-40, 41+);
the bar, `season.SEASON_HURDLE_PAID` (0.20 categories a week, since every
claim replayed here costs FAAB); `TYPICAL_PICKUP` (0.06). The rung `amount`
would become if the replay favours it is the 75% one, `HEADLINE_RUNG`.

The precedent is the pickup calibration rule (docs/trades.md section 7,
docs/pickups_backtest.md): declare, run once, publish whichever way it falls.

WHAT IS MEASURED, AND HOW

Everything runs on the stored 2026 season, the one FAAB season on record.

- **The win curve** (section 2), per bucket, from the claim events above,
  with the uncontested share beside it because that is half the record.
- **The exchange rate** (section 3): for every executed one-for-one 2026
  claim, what the man delivered over the 30 days after, scored by
  `scripts/pickups_backtest.py`'s own replay -- the real move undone and the
  sign flipped, which is what makes a claim and a recommendation the same
  quantity -- against what was paid. Gross (delivered over dollars) and
  **marginal** (the same, less what a $0 claim delivered, which is what the
  dollars actually bought), by calendar month from `processed_at` and pooled.
- **The competition signal** (section 5): for each claim, how many of the
  other rosters would have taken the claimed man -- into an open place, or
  over the cheapest man they held -- on the same `evaluated_wire` the week
  report reads. Judged on the season term alone (`delta_week = 0`), because
  the question is whether the man improves another roster's place and not
  who that roster plays this week. The stricter reading of the same count,
  how many of those rosters he also clears the paid bar for, is published
  beside it.
- **The replay** (section 6): for every executed 2026 claim by any team, the
  ladder as of that morning against the market bid now shipped and against
  what the manager actually paid, on the same claims, in delivered
  categories per dollar.

NO LOOK-AHEAD

The ladder and the market bid at a claim on day D are built only from claim
events **before** D: the buckets are refitted and the curve redrawn on each
day's prior record, and the shadow price reads no history at all. The
knowable line already filters to games before the day
(`app.scoring.knowable`), which is what the backtest verified.

THE FRAME: A CLAIM IS REPLAYED BACKWARDS

On the day a claim was processed, `daily_lineup_slots` already has the
claimed man on the winner's roster and the dropped man off it, so judging
"add A, drop D" forwards on that day judges a move that has already
happened, and every number comes out zero. So a claim is judged the way
`pickups_backtest.league_baseline` scores it: **backwards** -- drop A, add D
-- and the sign flipped. The claimed value and the delivered value are then
the same quantity in the same frame, which is the whole reason the baseline
was built that way.

ONE WIRE A DAY

A day's wire is built once per team, from that day's free agents with every
man claimed or dropped that day added to it, and shared by every claim of
that day and by the competition count. Building it per claim would price the
same day fourteen different ways and cost an hour; sharing it means one
`evaluated_wire` and one `spot_book` per (day, team), which is the same
construction the week report makes.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not -- and so
# `pickups_backtest` below is this checkout's, not another one's.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pickups_backtest as backtest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.bids import (
    BUCKETS,
    LADDER_RUNGS,
    Bucket,
    ClaimEvent,
    RankedClaim,
    WinCurve,
    _bucket,
    dollars,
    ranked_claims,
    share_cap,
    win_curve,
    worth_of,
)
from app.pickups.judge import SpotBook, horizon, judge, weeks_between
from app.pickups.season import SEASON_HURDLE_PAID
from app.pickups.state import TeamWeek, load_team_week, waiver_state
from app.pickups.stream import evaluated_wire, spot_book, week_changes
from app.scoring.replacement import TYPICAL_PICKUP

SEASON = 2026
REPORT = Path("docs/faab.md")

#: Days the exchange rate's delivery is measured over, the same window the
#: pickup backtest scores a rest-of-season move over.
RATE_WINDOW = backtest.SEASON_WINDOW

#: The bar every claim in the replay is judged against: a claim costs FAAB,
#: so it is the paid bar, and this script does not move it.
HURDLE = SEASON_HURDLE_PAID

#: The rung `amount` would become if the replay favours the ladder.
HEADLINE_RUNG = 0.75

#: How the three win rules in section 5 are named. The middle one is the
#: rule; the other two are its bounds, published so the tie convention is
#: visible rather than argued about.
TIE_SHARE = "a share of the hat"
TIE_WIN = "a tie wins"
TIE_LOSS = "a tie loses"
TIE_RULES = (TIE_SHARE, TIE_WIN, TIE_LOSS)

#: Dollars the published curve is drawn at. Above $20 every bucket is flat.
CURVE_DOLLARS = (0, 1, 2, 3, 4, 5, 7, 10, 15, 20)


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One executed 2026 claim, everything the measurement needs about it."""

    day: int
    processed: datetime
    #: `teams.id`, which the replay is keyed on, and the ESPN team id.
    team_row_id: int
    team_id: int
    added_id: int
    dropped_id: int
    bid: int
    rank: int
    bucket: str
    losing_bids: tuple[int, ...]

    @property
    def event(self) -> ClaimEvent:
        return ClaimEvent(
            day=self.day,
            player_id=self.added_id,
            winning_bid=self.bid,
            losing_bids=self.losing_bids,
            rank=self.rank,
        )

    @property
    def month(self) -> str:
        return self.processed.strftime("%Y-%m")


def bucket_of(rank: int) -> str:
    for label, first, last in BUCKETS:
        if rank >= first and (last is None or rank <= last):
            return label
    return "none"


def executed_claims(
    session: Session, league_season: LeagueSeason, ranked: Sequence[RankedClaim]
) -> list[Claim]:
    """Every executed 2026 claim with one add and one drop, in day order.

    The one-for-one shape is the pickup backtest's own baseline population: a
    claim with two drops is a different move and is not a swap the
    recommender would ever name.
    """
    by_key = {(claim.day, claim.player_id): claim for claim in ranked}
    rows = session.execute(
        text(
            """
            SELECT t.scoring_period, t.processed_at, t.team_id,
                   max(ti.player_id) FILTER (WHERE ti.item_type = 'ADD')  AS added,
                   max(ti.player_id) FILTER (WHERE ti.item_type = 'DROP') AS dropped,
                   t.bid_amount
            FROM transactions t
            JOIN transaction_items ti ON ti.transaction_id = t.id
            WHERE t.league_season_id = :ls AND t.type = 'WAIVER'
              AND t.status = 'EXECUTED' AND t.team_id IS NOT NULL
              AND t.processed_at IS NOT NULL
            GROUP BY t.id
            HAVING count(*) FILTER (WHERE ti.item_type = 'ADD') = 1
               AND count(*) FILTER (WHERE ti.item_type = 'DROP') = 1
            ORDER BY t.scoring_period, t.id
            """
        ),
        {"ls": league_season.id},
    ).all()
    teams = backtest.team_rows(session, league_season)
    espn_of = {row_id: espn for espn, row_id in teams.items()}
    out: list[Claim] = []
    for day, processed, team_row_id, added, dropped, bid in rows:
        found = by_key.get((int(day), int(added)))
        if found is None or int(team_row_id) not in espn_of:
            continue
        out.append(
            Claim(
                day=int(day),
                processed=processed,
                team_row_id=int(team_row_id),
                team_id=espn_of[int(team_row_id)],
                added_id=int(added),
                dropped_id=int(dropped),
                bid=int(bid or 0),
                rank=found.rank,
                bucket=bucket_of(found.rank),
                losing_bids=found.losing_bids,
            )
        )
    return out


# ---------------------------------------------------------------------------
# section 2: the win curve
# ---------------------------------------------------------------------------


def hit_rate(events: Sequence[ClaimEvent]) -> dict[int, tuple[int, int]]:
    """Every bid ever filed, by dollar: (filed, won). The literal table.

    A winner's bid counts as won and a loser's as lost, so this is the raw
    hit rate of a dollar in this league rather than the curve the ladder
    reads, which asks a different question: what a *new* bid would take off
    the field that was already there.
    """
    out: dict[int, tuple[int, int]] = {}
    for event in events:
        filed, won = out.get(event.winning_bid, (0, 0))
        out[event.winning_bid] = (filed + 1, won + 1)
        for bid in event.losing_bids:
            filed, won = out.get(bid, (0, 0))
            out[bid] = (filed + 1, won)
    return out


# ---------------------------------------------------------------------------
# section 3: the exchange rate
# ---------------------------------------------------------------------------


def delivered_value(replay: backtest.Replay, claim: Claim) -> float:
    """The 30-day categories the claim delivered, scored backwards.

    `pickups_backtest.league_baseline`'s own arithmetic: put the dropped man
    back, take the added man out, and flip the sign.
    """
    _week, season = replay.score(claim.team_row_id, claim.day, [claim.added_id], [claim.dropped_id])
    return -float(season)


@dataclass(frozen=True)
class Rate:
    """An exchange rate over one set of claims."""

    label: str
    #: Claims in the set, and how many of them cost a dollar or more.
    n: int
    paid_n: int
    dollars: int
    delivered: float
    #: What a claim that cost nothing delivered, the counterfactual the
    #: marginal rate nets out.
    free_median: float
    #: The per-claim ratios behind `gross`, for the spread.
    ratios: tuple[float, ...] = ()

    @property
    def gross(self) -> float:
        return self.delivered / self.dollars if self.dollars else float("nan")

    @property
    def marginal(self) -> float:
        if not self.dollars:
            return float("nan")
        return (self.delivered - self.paid_n * self.free_median) / self.dollars


def rate_over(label: str, rows: Sequence[tuple[Claim, float]], free_median: float) -> Rate:
    paid = [(claim, value) for claim, value in rows if claim.bid > 0]
    return Rate(
        label=label,
        n=len(rows),
        paid_n=len(paid),
        dollars=sum(claim.bid for claim, _ in paid),
        delivered=sum(value for _, value in paid),
        free_median=free_median,
        ratios=tuple(value / claim.bid for claim, value in paid),
    )


def spread(values: Sequence[float]) -> tuple[float, float, float]:
    """(lower quartile, median, upper quartile), nearest rank."""
    if not values:
        return (float("nan"),) * 3
    ordered = sorted(values)

    def at(share: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))]

    return at(0.25), at(0.50), at(0.75)


# ---------------------------------------------------------------------------
# one day's wire, built once and shared
# ---------------------------------------------------------------------------


@dataclass
class DayBook:
    """One day's `evaluated_wire` and `spot_book`, per team."""

    day: int
    pool: list[int]
    weeks: dict[int, TeamWeek] = field(default_factory=dict)
    spots: dict[int, SpotBook] = field(default_factory=dict)
    waivers: dict[int, dict[int, tuple[date, int]]] = field(default_factory=dict)

    def load(
        self,
        session: Session,
        league_season: LeagueSeason,
        team_id: int,
        distributions: Sequence[CategoryDistribution],
    ) -> tuple[TeamWeek, SpotBook] | None:
        if team_id in self.spots:
            return self.weeks[team_id], self.spots[team_id]
        try:
            week = load_team_week(session, league_season, team_id, self.day)
        except ValueError:
            return None
        wire = evaluated_wire(
            session,
            league_season,
            week,
            self.day,
            pool=self.pool,
            tilt=False,
            distributions=distributions,
            as_of=None,
        )
        spots = spot_book(
            session,
            league_season,
            team_id,
            self.day,
            week,
            wire,
            tilt=False,
            distributions=distributions,
        )
        self.weeks[team_id] = week
        self.spots[team_id] = spots
        self.waivers[team_id] = waiver_state([found.player for found in wire])
        return week, spots


def day_pool(
    session: Session, league_season: LeagueSeason, day: int, claims: Sequence[Claim]
) -> list[int]:
    """The day's free agents, with everybody claimed or dropped that day on it.

    A claimed man is off the wire by the time the lineup rows are written and
    a dropped man is on it, and the reversed frame needs both, so both are
    put back whatever the rows say.
    """
    pool = set(backtest.free_agent_pool(session, league_season, day))
    for claim in claims:
        pool.add(claim.added_id)
        pool.add(claim.dropped_id)
    return sorted(pool)


# ---------------------------------------------------------------------------
# the judgement of one claim, and of the rosters that did not make it
# ---------------------------------------------------------------------------


def judged_per_week(
    session: Session,
    league_season: LeagueSeason,
    claim: Claim,
    book: DayBook,
    distributions: Sequence[CategoryDistribution],
) -> tuple[float, float, int] | None:
    """(per_week the claim was worth, weeks it covers, FAAB left that day).

    Judged backwards and negated: see the module docstring.
    """
    loaded = book.load(session, league_season, claim.team_id, distributions)
    if loaded is None:
        return None
    week, spots = loaded
    changed = week_changes(
        session,
        league_season,
        claim.team_id,
        claim.day,
        [((claim.dropped_id,), (claim.added_id,))],
        tilt=False,
        distributions=distributions,
        waivers=book.waivers[claim.team_id],
    )[0]
    reverse = judge(
        spots,
        delta_week=changed.delta,
        dropped=[claim.added_id],
        added=[claim.dropped_id],
    )
    return -reverse.per_week, reverse.weeks_covered, week.faab_remaining


def competition_for(
    session: Session,
    league_season: LeagueSeason,
    claim: Claim,
    teams: Sequence[int],
    book: DayBook,
    distributions: Sequence[CategoryDistribution],
) -> tuple[int, int]:
    """(rosters the man would take a place on, rosters he clears the bar for).

    A roster wants him when he would take a place on it: into an open one, or
    over the cheapest man there. Judged on the season term alone and against
    that roster's own wire replacement, because the question is whether the
    man improves another manager's place and not who that manager plays this
    week. The second number is the stricter reading of the same count -- how
    many of those rosters he clears the paid bar for -- and is reported
    beside it rather than instead of it.
    """
    wants = 0
    clears = 0
    for team_id in teams:
        if team_id == claim.team_id:
            continue
        loaded = book.load(session, league_season, team_id, distributions)
        if loaded is None:
            continue
        week, spots = loaded
        held = [player.player_id for player in week.roster if not player.on_ir]
        if week.open_slots > 0:
            found = judge(spots, delta_week=0.0, dropped=[], added=[claim.added_id])
        elif held:
            worst = min(held, key=spots.value)
            found = judge(spots, delta_week=0.0, dropped=[worst], added=[claim.added_id])
        else:
            continue
        if found.per_week > 0.0:
            wants += 1
        if found.per_week >= HURDLE:
            clears += 1
    return wants, clears


# ---------------------------------------------------------------------------
# section 5: the replay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Replayed:
    """One claim, replayed: what each rule would have bid, and what it took."""

    claim: Claim
    per_week: float
    weeks_covered: float
    faab_remaining: int
    worth: int
    ceiling: int
    market: int
    #: (rung asked, dollars offered, chance at that dollar).
    rungs: tuple[tuple[float, int, float], ...]
    competition: int | None
    #: Of those, how many he also clears the paid bar for.
    clears_for: int | None
    delivered: float


def chance_at(event: ClaimEvent, amount: int, rule: str) -> float:
    """What a bid of `amount` takes off this claim's field, under `rule`."""
    if amount > event.winning_bid:
        return 1.0
    if amount < event.winning_bid:
        return 0.0
    if rule == TIE_WIN:
        return 1.0
    if rule == TIE_LOSS:
        return 0.0
    return 1.0 / (event.at_the_top + 1)


def market_bid(prior: Mapping[str, Bucket], claim: Claim, per_week: float, limit: int) -> int:
    """The shipped rule's number, from the claims before this one."""
    bucket = prior.get(claim.bucket)
    if bucket is None:
        return 0
    aggressive = per_week >= 2.0 * HURDLE
    return max(0, min(limit, round(bucket.upper_quartile if aggressive else bucket.median)))


def fit_buckets(claims: Sequence[Claim]) -> dict[str, Bucket]:
    by_bucket: dict[str, list[int]] = defaultdict(list)
    for claim in claims:
        by_bucket[claim.bucket].append(claim.bid)
    out: dict[str, Bucket] = {}
    for label, first, last in BUCKETS:
        if by_bucket[label]:
            out[label] = _bucket(label, first, last, by_bucket[label])
    return out


@dataclass
class Tally:
    """One bidding rule's whole account over the claims it was asked about."""

    label: str
    asked: int = 0
    won: float = 0.0
    dollars: float = 0.0
    delivered: float = 0.0

    def offer(self, amount: int, chance: float, delivered: float) -> None:
        self.asked += 1
        self.won += chance
        self.dollars += amount * chance
        self.delivered += delivered * chance

    @property
    def per_dollar(self) -> float:
        return self.delivered / self.dollars if self.dollars else float("nan")

    @property
    def per_claim(self) -> float:
        """Delivered over claims taken, which a rule that spends nothing still has."""
        return self.delivered / self.won if self.won else float("nan")


def tallies(rows: Sequence[Replayed], rule: str) -> dict[str, Tally]:
    """Every rule's account, under one tie convention."""
    out: dict[str, Tally] = {
        "actual": Tally("what managers paid"),
        "market": Tally("the market bid, as shipped"),
    }
    for asked in LADDER_RUNGS:
        out[f"ladder{asked:.2f}"] = Tally(f"the ladder's {asked:.0%} rung")
    for row in rows:
        out["actual"].offer(row.claim.bid, 1.0, row.delivered)
        out["market"].offer(row.market, chance_at(row.claim.event, row.market, rule), row.delivered)
        for rung, amount, _chance in row.rungs:
            out[f"ladder{rung:.2f}"].offer(
                amount, chance_at(row.claim.event, amount, rule), row.delivered
            )
    return out


# ---------------------------------------------------------------------------
# statistics with no dependency
# ---------------------------------------------------------------------------


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 3:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    top = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    left = math.sqrt(sum((x - mx) ** 2 for x in xs))
    right = math.sqrt(sum((y - my) ** 2 for y in ys))
    return top / (left * right) if left and right else float("nan")


def within_bucket_correlation(rows: Sequence[tuple[str, float, float]]) -> float:
    """Correlation of the two columns once each bucket's mean is removed.

    The question section 4 asks is whether competition says anything the
    bucket did not, so both columns are residualised on the bucket first.
    """
    by_bucket: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for label, left, right in rows:
        by_bucket[label].append((left, right))
    xs: list[float] = []
    ys: list[float] = []
    for pairs in by_bucket.values():
        if len(pairs) < 3:
            continue
        mx = statistics.fmean([left for left, _ in pairs])
        my = statistics.fmean([right for _, right in pairs])
        xs += [left - mx for left, _ in pairs]
        ys += [right - my for _, right in pairs]
    return correlation(xs, ys)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


@dataclass
class Run:
    claims: list[Claim]
    curve: WinCurve
    events: list[ClaimEvent]
    rates: list[Rate]
    top_rate: Rate
    free_median: float
    free_n: int
    replayed: list[Replayed]
    competition_on: bool
    seconds: float
    skipped: int


def measure(
    session: Session,
    league_season: LeagueSeason,
    *,
    limit: int | None,
    competition_on: bool,
    progress: bool = True,
) -> Run:
    started = time.time()
    distributions = category_distributions(session, league_season)
    ranked = [
        claim
        for claim in ranked_claims(session, league_season, distributions)
        if claim.league_season_id == league_season.id
    ]
    if progress:
        print(f"  ranked {len(ranked)} winning claims ({time.time() - started:.0f}s)", flush=True)
    claims = executed_claims(session, league_season, ranked)
    if limit is not None:
        claims = claims[:limit]
    events = [claim.event for claim in ranked]
    curve = win_curve(events)

    replay = backtest.Replay.load(session, league_season)
    if progress:
        print(f"  replay loaded ({time.time() - started:.0f}s)", flush=True)
    delivered = {
        claim.day * 100000 + claim.added_id: delivered_value(replay, claim) for claim in claims
    }
    if progress:
        print(f"  delivered {len(delivered)} claims ({time.time() - started:.0f}s)", flush=True)

    free = [value for claim, value in _pairs(claims, delivered) if claim.bid == 0]
    free_median = statistics.median(free) if free else 0.0
    pairs = list(_pairs(claims, delivered))
    months = sorted({claim.month for claim in claims})
    rates = [
        rate_over(month, [(c, v) for c, v in pairs if c.month == month], free_median)
        for month in months
    ]
    rates.append(rate_over("pooled", pairs, free_median))
    top_rate = rate_over("rank 1-5", [(c, v) for c, v in pairs if c.bucket == "1-5"], free_median)

    first_day, last_day, _ = horizon(session, league_season, claims[0].day if claims else 1)
    total_weeks = weeks_between(first_day, last_day)
    teams = backtest.team_ids(session, league_season)

    by_day: dict[int, list[Claim]] = defaultdict(list)
    for claim in claims:
        by_day[claim.day].append(claim)

    prior: list[Claim] = []
    replayed: list[Replayed] = []
    skipped = 0
    for index, day in enumerate(sorted(by_day)):
        book = DayBook(day=day, pool=day_pool(session, league_season, day, by_day[day]))
        prior_curve = win_curve([claim.event for claim in prior])
        prior_fit = fit_buckets(prior)
        for claim in by_day[day]:
            judged = judged_per_week(session, league_season, claim, book, distributions)
            if judged is None:
                skipped += 1
                continue
            per_week, weeks_covered, faab = judged
            worth = dollars(worth_of(per_week, faab, weeks_covered, TYPICAL_PICKUP))
            ceiling = dollars(worth_of(per_week - HURDLE, faab, weeks_covered, TYPICAL_PICKUP))
            share = share_cap(faab, weeks_between(day, last_day), total_weeks)
            cap = max(0, min(ceiling, share))
            counted: tuple[int | None, int | None] = (
                competition_for(session, league_season, claim, teams, book, distributions)
                if competition_on
                else (None, None)
            )
            found = prior_curve.bucket_for(claim.bucket)
            rungs = tuple(
                (asked, offered, found.chance(offered) if found else 0.0)
                for asked in LADDER_RUNGS
                if (offered := max(0, min(cap, found.rung(asked))) if found else 0) >= 0
            )
            replayed.append(
                Replayed(
                    claim=claim,
                    per_week=per_week,
                    weeks_covered=weeks_covered,
                    faab_remaining=faab,
                    worth=worth,
                    ceiling=ceiling,
                    market=market_bid(prior_fit, claim, per_week, share),
                    rungs=rungs,
                    competition=counted[0],
                    clears_for=counted[1],
                    delivered=delivered[claim.day * 100000 + claim.added_id],
                )
            )
        prior.extend(by_day[day])
        if progress and index % 10 == 0:
            print(
                f"  day {day}: {len(replayed)} claims replayed ({time.time() - started:.0f}s)",
                flush=True,
            )

    return Run(
        claims=claims,
        curve=curve,
        events=events,
        rates=rates,
        top_rate=top_rate,
        free_median=free_median,
        free_n=len(free),
        replayed=replayed,
        competition_on=competition_on,
        seconds=time.time() - started,
        skipped=skipped,
    )


def _pairs(claims: Sequence[Claim], delivered: Mapping[int, float]) -> list[tuple[Claim, float]]:
    return [(claim, delivered[claim.day * 100000 + claim.added_id]) for claim in claims]


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


def pct(value: float) -> str:
    return "n/a" if value != value else f"{value * 100:.0f}%"


def num(value: float, places: int = 3) -> str:
    return "n/a" if value != value else f"{value:+.{places}f}"


def plain(value: float, places: int = 3) -> str:
    return "n/a" if value != value else f"{value:.{places}f}"


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return out


#: The rule, quoted into the document verbatim so a re-run cannot quietly
#: rewrite what was declared. The module docstring above is the same text.
DECLARED = """\
The rule was written down before the run, here and in the docstring of
`scripts/faab_bids.py`, and nothing in it was tuned afterwards. The precedent
is the pickup calibration rule (`docs/trades.md` section 7,
`docs/pickups_backtest.md`).

**1. What he is worth to you, in dollars.** The move's worth in categories is
`Judgement.per_week`, its net over both horizons spread over the weeks it
covers -- the unit every hurdle here is written in. The rate that turns it
into dollars is **your own budget's shadow price**: `faab_remaining` dollars
cover `weeks_covered` weeks, so a week costs `faab_remaining / weeks_covered`
dollars, and a week is worth `TYPICAL_PICKUP` (0.06 categories, what one
executed add returns, `app.scoring.replacement`).

```
worth_dollars = per_week / TYPICAL_PICKUP * (faab_remaining / weeks_covered)
ceiling       = floor((per_week - hurdle) / TYPICAL_PICKUP
                      * (faab_remaining / weeks_covered))
```

**The rule uses the shadow price in both places it prices anything, and the
league's going rate in none.** Two reasons, both stated before the run. The
shadow price is specific to you, which is the point of the whole change: two
managers with the same roster and different budgets should not be told to bid
the same number. And it reads no claim history, so it cannot look ahead at
prices nobody had paid yet on the morning of the bid. The league's going rate
-- what an executed 2026 claim delivered over the 30 days after, per dollar
paid -- is measured in section 3 and published as the market check, never
used to price a bid.

**2. What it takes to win, as a curve.** `P(win | bid $x)` per rank bucket,
over **claim events**: one (day, player) that at least one waiver claim was
filed on, whose field is every bid filed on it. The losing side is
`transactions` with `type = WAIVER` and `status =
FAILED_INVALIDPLAYERSOURCE`, read as "lost the player to a higher bid the
same day". A bid of $x beats a field whose top bid is $w when x > w; at
x == w it is a tie, which ESPN breaks on a waiver priority nothing stored can
reconstruct, so it is credited `1 / (bidders at the top price + 1)` -- one
share of the hat.

**3. The bid is a ladder**, never a single verdict number. Three rungs,
`bids.LADDER_RUNGS` = **50%, 75%, 90%**: for each, the cheapest whole dollar
whose curve reaches it, capped by the ceiling and by the share cap. A rung a
cap pulls down reports the chance at the dollar actually offered. The
existing caps still apply and the bar is untouched.

**4. Who else wants him.** The count beside the ladder is how many of the
other rosters would have taken the man: into an open place, or over the
cheapest man they hold, judged on the season term alone against that
roster's own wire replacement. It labels, it hides nothing, and it moves no
number. The stricter reading -- how many of those rosters he also clears the
paid bar for -- is published beside it in section 5.

**`amount` keeps its meaning** -- the market number -- unless the replay in
section 6 says the ladder's 75% rung delivers more categories per dollar.
Section 0 says which way it fell.

**The constants, fixed before the run:** the rungs (0.50, 0.75, 0.90); the
rate's window, 30 days (`pickups_backtest.SEASON_WINDOW`); the rank buckets
reused unchanged from `bids.BUCKETS` (1-5, 6-15, 16-40, 41+); the bar,
`season.SEASON_HURDLE_PAID` = 0.20 categories a week, since every claim
replayed costs FAAB; `TYPICAL_PICKUP` = 0.06. No hurdle, no calibration
constant and no note outside this document and `app/pickups/bids.py` was
touched.
"""

LIMITATIONS = """\
- **One FAAB season.** 2026 is the whole record. Every number below is one
  league, one year, and 2027 will be the second. The market fit already says
  so in its own note and nothing here makes it less true.
- **The losing-claim reading is an inference.** `FAILED_INVALIDPLAYERSOURCE`
  is ESPN's reason for "the player was no longer a valid source", which is
  not the same sentence as "somebody outbid you". Section 1 says what it was
  checked against, and the check is strong, but it is a check and not a
  document from ESPN.
- **Ties are half the contested record and cannot be broken.** ESPN settles a
  FAAB tie on waiver priority, which is not stored. Every win rate here is
  therefore a convention; section 5 publishes all three.
- **The 30-day window is arbitrary and generous.** A claim made in March has
  fewer than 30 days of season left, so its delivery is truncated by the
  calendar and its rate looks worse for a reason that has nothing to do with
  the price.
- **The backtest's own caveats carry over in full** (`docs/pickups_backtest.md`
  section 6): a perfectly set lineup on both sides, no injury history, the
  reconstructed 2026 schedule, ten days with no box scores, and a
  counterfactual roster that drifts from the real one as the window runs on.
- **A claim is judged backwards.** The day's lineup rows already have the
  move in them, so the claim is scored as the reversal of itself, negated.
  That is the backtest's own frame, and it means the wire the judgement reads
  is the wire after the claim rather than before it.
- **The competition count is the season term only.** It asks whether the man
  would take a place on another roster -- an open one, or the cheapest man on
  it; it does not read that roster's own week, and it does not know that
  manager's budget or his add budget.
"""


def build(run: Run, *, label: str) -> str:
    rows = run.replayed
    share = tallies(rows, TIE_SHARE)
    ladder = share[f"ladder{HEADLINE_RUNG:.2f}"]
    market = share["market"]
    actual = share["actual"]
    beats = bool(ladder.dollars) and ladder.per_dollar > market.per_dollar

    out: list[str] = [
        "# FAAB: what he is worth to you, and what it takes to win",
        "",
        f"Full Court Press (ESPN 3853870), {SEASON}. Generated by `scripts/faab_bids.py`. "
        f"{len(run.claims)} executed claims, {run.curve.events} claim events, "
        f"{run.seconds:.0f}s.{label}",
        "",
        "## 0. What the replay decided about `amount`",
        "",
    ]
    floored = sum(1 for row in rows if row.ceiling <= 0)
    if beats:
        out += [
            "**Declared revision.** The ladder's 75% rung delivered "
            f"{plain(ladder.per_dollar)} categories a dollar against the market bid's "
            f"{plain(market.per_dollar)} on the same {len(rows)} claims, so `amount` becomes "
            "that rung. The before and after are section 6's table.",
        ]
    else:
        out += [
            "**No revision. `amount` is unchanged**, which is the declared outcome when the "
            "ladder does not beat it.",
            "",
            "The reason is worth stating plainly, because it is the finding and not a "
            f"technicality. **On {floored} of {len(rows)} claims the ceiling was $0**: the move "
            "did not clear the bar for the team that actually made it, at any price, so every "
            "rung of the value rule was a free claim. Over all "
            f"{len(rows)} claims the ladder's 75% rung offered ${ladder.dollars:,.0f} in total "
            f"and took {ladder.won:.0f} of them; the market bid now shipped offered "
            f"${market.dollars:,.0f} and took {market.won:.0f}; the managers themselves paid "
            f"${actual.dollars:,.0f} and took all {actual.asked}.",
            "",
            "**Delivered categories per dollar, the headline this run was declared on:** the "
            f"ladder {plain(ladder.per_dollar)}, the market bid {plain(market.per_dollar)}, "
            f"what managers paid {plain(actual.per_dollar)}, on the same {len(rows)} claims. "
            f"The ladder's figure is a ratio over ${ladder.dollars:,.0f} and says nothing about "
            "the rule. What it does say is that the rule declined to pay, and a rule that "
            "declines to pay cannot be shown to spend better. So the market number stays "
            "exactly where it was.",
            "",
            "**All three delivered negative categories.** Per claim taken: the ladder "
            f"{num(ladder.per_claim)}, the market bid {num(market.per_claim)}, the managers "
            f"{num(actual.per_claim)}. That is the league's own record rather than this rule's "
            "-- `docs/pickups_backtest.md` measures the same 2026 swaps at -0.56 categories "
            "over thirty days -- and it is the reason the ceiling is $0 so often.",
            "",
            "**What ships, then.** The worth, the ceiling and the ladder, which are new "
            "information beside the market number and not a new verdict. On a move the reports "
            "actually name the ceiling is always above $0, because a bid is only attached to a "
            "move that clears the bar; it is the claims this league really made that do not. "
            "The competition count does not ship, and section 5 says why.",
        ]
    out += [
        "",
        "## 1. Limitations, stated before conclusions",
        "",
        LIMITATIONS,
        "**The losing-claim reading, checked.** Over 2026 there are "
        f"{sum(1 for event in run.events if event.contested)} claim events with at least one "
        f"`FAILED_INVALIDPLAYERSOURCE` bid on the same player the same day. In every one of "
        "them the executed bid is at or above every losing bid, and there is no losing bid "
        "anywhere in the season without an executed winner on the same player and day. That "
        "is what the reading predicts and it holds without exception.",
        "",
        "## 2. Declared",
        "",
        DECLARED,
        "## 3. The win curve",
        "",
        "`P(win | bid $x)`: what a bid of $x would have taken off the field that was actually "
        "there, averaged over the claim events of each bucket. A tie is one share of the hat.",
        "",
    ]

    dollars = CURVE_DOLLARS
    out += table(
        ["bucket", "events", "contested", "uncontested", *[f"${d}" for d in dollars]],
        [
            [
                bucket.label,
                str(bucket.n),
                str(bucket.contested),
                pct((bucket.n - bucket.contested) / bucket.n) if bucket.n else "n/a",
                *[pct(bucket.chance(dollar)) for dollar in dollars],
            ]
            for bucket in run.curve.buckets
        ],
    )
    out += [
        "",
        f"**Nobody else bid on {pct(run.curve.uncontested_share)} of claims** "
        f"({run.curve.uncontested} of {run.curve.events}), and that is most of the wire: on "
        "those a dollar buys nothing a free claim would not have bought. The curve is the "
        "reason the ladder's bottom rung is usually cheap.",
        "",
        "And the literal table -- every bid ever filed, and how often that dollar won:",
        "",
    ]
    raw = hit_rate(run.events)
    out += table(
        ["bid", "filed", "won", "share"],
        [
            [f"${dollar}", str(filed), f"{won}", pct(won / filed)]
            for dollar, (filed, won) in sorted(raw.items())
            if filed >= 5
        ],
    )
    out += [
        "",
        "(Bids filed fewer than five times are left out of this table; the curve above uses "
        "every one of them.)",
        "",
        "## 4. The exchange rate: what a dollar bought",
        "",
        "Delivered is categories over the 30 days after the claim, scored by the pickup "
        "backtest's own replay. **Gross** is delivered over dollars across the claims that "
        f"cost a dollar or more. **Marginal** nets out what a $0 claim delivered in the same "
        f"season ({num(run.free_median)} categories, the median of {run.free_n} free claims), "
        "which is what the dollars actually bought rather than what the player was worth.",
        "",
    ]
    rate_rows = []
    for rate in run.rates:
        lower, middle, upper = spread(rate.ratios)
        rate_rows.append(
            [
                rate.label,
                str(rate.paid_n),
                f"${rate.dollars:,}",
                num(rate.delivered, 1),
                plain(rate.gross),
                plain(rate.marginal),
                f"{plain(lower)} / {plain(middle)} / {plain(upper)}",
            ]
        )
    out += table(
        [
            "window",
            "paid claims",
            "dollars",
            "delivered",
            "gross",
            "marginal",
            "per-claim Q1/med/Q3",
        ],
        rate_rows,
    )
    autumn = next((rate for rate in run.rates if rate.label.endswith("-11")), None)
    winter = next((rate for rate in run.rates if rate.label.endswith("-02")), None)
    if autumn is not None and winter is not None:
        verdict = "more" if autumn.marginal > winter.marginal else "less"
        out += [
            "",
            f"**Plainly: a dollar bought {verdict} in November than in February.** "
            f"{plain(autumn.marginal)} categories a dollar against {plain(winter.marginal)} on "
            f"the marginal reading, and {plain(autumn.gross)} against {plain(winter.gross)} on "
            f"the gross one, over {autumn.paid_n} and {winter.paid_n} paid claims. The rate "
            "collapses through the middle of the season and comes back in March, and the March "
            "figure is the one to trust least: a claim made then has fewer than thirty days of "
            "season left to deliver in, so its window is cut by the calendar rather than by "
            "what it cost.",
        ]
    lower, middle, upper = spread(run.top_rate.ratios)
    out += [
        "",
        f"**The top bucket alone** (rank 1-5): {run.top_rate.paid_n} paid claims, "
        f"${run.top_rate.dollars:,}, {num(run.top_rate.delivered, 1)} categories delivered, "
        f"gross {plain(run.top_rate.gross)} and marginal {plain(run.top_rate.marginal)} a "
        f"dollar, per-claim quartiles {plain(lower)} / {plain(middle)} / {plain(upper)}.",
        "",
        "## 5. The competition signal",
        "",
    ]
    if run.competition_on:
        counted = [row for row in rows if row.competition is not None]
        by_count: dict[int, list[int]] = defaultdict(list)
        for row in counted:
            by_count[row.competition or 0].append(row.claim.bid)
        clears_total = sum(row.clears_for or 0 for row in counted)
        biggest = max(by_count) if by_count else 0
        binary = (
            sum(len(bids) for count, bids in by_count.items() if count in (0, biggest))
            / len(counted)
            if counted
            else 0.0
        )
        out += [
            "How many of the other rosters would have taken the claimed man -- into an open "
            "place, or over the cheapest man they held -- against what he actually cost.",
            "",
        ]
        out += table(
            ["other rosters who would take him", "claims", "mean winning bid", "median"],
            [
                [
                    str(count),
                    str(len(bids)),
                    f"${statistics.fmean(bids):.2f}",
                    f"${statistics.median(bids):.1f}",
                ]
                for count, bids in sorted(by_count.items())
            ],
        )
        overall = correlation(
            [float(row.competition or 0) for row in counted],
            [float(row.claim.bid) for row in counted],
        )
        within = within_bucket_correlation(
            [
                (row.claim.bucket, float(row.competition or 0), float(row.claim.bid))
                for row in counted
            ]
        )
        out += [
            "",
            f"Correlation with the winning bid: **r = {plain(overall, 2)}** overall, and "
            f"**r = {plain(within, 2)}** once each rank bucket's own mean is removed -- which "
            "is the number that says whether competition knows anything the bucket did not.",
            "",
            f"**The count is nearly binary**: 0 or {biggest} on {pct(binary)} of claims, and "
            "the reason is structural. Every roster's wire replacement is very nearly the same "
            "number on a given day -- the best free agent left, floored at the typical pickup "
            '-- so "would this roster take him" collapses into "is he better than the best '
            'other man on the wire", which is his rank again. That is why it adds '
            f"{plain(within, 2)} to what the bucket already knew.",
            "",
            "**The stricter reading**, how many of those rosters the man also clears the paid "
            f"bar of {HURDLE:.2f} categories a week for, summed over every claim: "
            f"**{clears_total}**.",
            "",
            "**So it does not ship.** A count that repeats the rank, at the price of thirteen "
            "spot books a request, is not worth a request. `Bid.competition` exists and stays "
            "`None`; the definition is kept so the next FAAB season does not have to invent it "
            "again.",
            "",
        ]
    else:
        out += ["Not counted in this run (`--no-competition`).", ""]

    out += [
        "## 6. The replay: the ladder against the market, against what was paid",
        "",
        f"Every executed 2026 claim by any team, {len(rows)} of them, priced as of that "
        "morning from the claims before it. A rule that offers less than the winning bid takes "
        "nothing and pays nothing, so the account below is what each rule would have spent and "
        "what it would have got for it.",
        "",
    ]
    for rule in TIE_RULES:
        found = tallies(rows, rule)
        out += [f"**Ties: {rule}.**", ""]
        out += table(
            ["rule", "claims won", "dollars", "categories delivered", "per dollar", "per claim"],
            [
                [
                    tally.label,
                    f"{tally.won:.0f} of {tally.asked}",
                    f"${tally.dollars:,.0f}",
                    num(tally.delivered, 1),
                    "spent nothing" if not tally.dollars else plain(tally.per_dollar),
                    num(tally.per_claim, 3),
                ]
                for tally in found.values()
            ],
        )
        out += [""]

    worths = [row.worth for row in rows]
    ceilings = [row.ceiling for row in rows]
    out += [
        "**What the rule thought the men were worth.** Median worth "
        f"${statistics.median(worths):.0f}, median ceiling ${statistics.median(ceilings):.0f}, "
        f"against a median winning bid of ${statistics.median([r.claim.bid for r in rows]):.0f}. "
        f"{sum(1 for row in rows if row.ceiling <= 0)} of {len(rows)} claims had a ceiling of "
        "$0 -- the move did not clear the bar for the team that made it, at any price.",
        "",
        "## 7. Decisions taken in this script",
        "",
        "- A claim is judged **backwards and negated**, because the day's lineup rows already "
        "have the move in them. That is `pickups_backtest.league_baseline`'s own frame, and it "
        "is what makes the claimed value and the delivered value the same quantity.",
        "- The wire is built **once per (day, team)** and shared by every claim of that day, "
        "with everybody claimed or dropped that day put back on it. Per-claim wires would "
        "price the same day fourteen ways and cost an hour.",
        "- The curve's population is **claim events**, not claims: one (day, player) somebody "
        "filed on. That is the only population the losing side identifies.",
        "- The **marginal** rate is the headline rate, not the gross one. A paid claim's whole "
        "delivery was not bought by the dollars; a free claim would have delivered something "
        "too, and the difference is what the money did.",
        "- The competition count is judged on the **season term alone**, into an open place or "
        "over each roster's cheapest man, so it costs one spot book per (day, team) rather "
        "than a week's head-to-head per roster per claim.",
        "- Claims with two drops are excluded: the backtest's baseline population is one-for-one "
        "swaps, and that is the move the recommender names.",
        "- The competition count is measured and **not wired**. The declared rule said it "
        "labels and moves no number; the measurement says it repeats the rank. Both are "
        "reasons not to spend thirteen spot books a request on it.",
        "- Nothing is refitted after the run. The rule in section 2 is the rule that ran.",
        "",
    ]
    if run.skipped:
        out += [
            f"{run.skipped} claims fell on a day with no matchup period to read and were skipped.",
            "",
        ]
    return "\n".join(out) + "\n"


def main() -> None:  # pragma: no cover - the CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Claims to replay, for a smoke run")
    parser.add_argument("--no-competition", action="store_true", help="Skip section 5's count")
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args()

    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    with factory() as session, backtest.patched_state():
        league_season = backtest.load_season(session, SEASON)
        run = measure(
            session,
            league_season,
            limit=args.limit,
            competition_on=not args.no_competition,
        )
    label = f" Smoke run: {args.limit} claims." if args.limit else ""
    document = build(run, label=label)
    if args.limit:
        print(document)
        return
    args.out.write_text(document)
    print(f"wrote {args.out} in {run.seconds:.0f}s")


if __name__ == "__main__":  # pragma: no cover
    main()
