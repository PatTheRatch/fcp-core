"""What a winning FAAB bid has cost here, what he is worth to you, and what
it takes to win.

The question of docs/pickups.md section 4.5, and since 2026-09-23 the one
docs/faab.md asks: the market price is what it takes to win, and it says
nothing about whether the man is worth that *to this roster*. So the module
now carries three things beside the market number -- what the move is worth
in dollars, the dollar above which it stops clearing the bar, and the chance
a given dollar wins -- and the rule that reads them is declared in
docs/faab.md section "Declared" before it was ever measured.

WHAT HE IS WORTH TO YOU, IN DOLLARS

A move's worth is already in categories (`app.pickups.judge.Judgement`). The
missing piece is the exchange rate, and the one this module's rule uses is
**your own budget's shadow price**: with `faab_remaining` dollars covering
`weeks_covered` weeks, a week of your season costs `faab_remaining /
weeks_covered` dollars, and a week of your season is worth what an ordinary
claim returns (`app.scoring.replacement.TYPICAL_PICKUP`, 0.06 categories a
week). So

    worth_dollars = per_week / TYPICAL_PICKUP * (faab_remaining / weeks_covered)
    ceiling       = (per_week - hurdle) / TYPICAL_PICKUP * (faab_remaining / weeks_covered)

`worth_dollars` is what the move is worth; `ceiling` is the dollar above
which paying for it no longer clears the bar, since a dollar charges the
place `TYPICAL_PICKUP * weeks_covered / faab_remaining` categories a week.

It is the shadow price rather than the league's measured going rate for two
reasons. It is specific to you -- two managers with the same roster and
different budgets should not bid the same, which is the whole point -- and it
reads no claim history at all, so it cannot look ahead at prices that had not
been paid yet. The league's going rate is measured beside it in docs/faab.md
and published as the market check, not used to price a bid.

THE WIN CURVE

`P(win | bid $x)`, per rank bucket, over the claim events this league has on
record. An event is one (day, player) that at least one waiver claim was
filed on; its field is every bid filed on it, the winner's and the losers'.
The losing side is readable because ESPN keeps it: a claim that lost the
player to a higher bid the same day is stored with status
`FAILED_INVALIDPLAYERSOURCE` (`LOST`). Against 2026 that reading holds
exactly -- in all 574 contested events the executed bid is at or above every
`LOST` bid, and no `LOST` bid exists without an executed winner on the same
player and day.

A bid of $x beats a field whose top bid is $w when x > w, and when x == w it
is a tie, which ESPN breaks on waiver priority. Priority cannot be
reconstructed from what is stored, so a tie is credited at 1/(bidders at the
top price + 1): the chance of being drawn out of the hat, which is neither
the optimistic reading nor the pessimistic one.

THE LADDER

`LADDER_RUNGS` win chances, each with the cheapest whole dollar that reaches
it, every rung capped by the ceiling and by the two caps the market bid has
always had. A rung the caps pull down reports the chance at the dollar
actually offered, not at the dollar it asked for, so the ladder never
promises a chance the number beside it does not buy.

WHO ELSE WANTS HIM

`competition` is how many of the other rosters in the league would take the
man -- into an open place, or over the cheapest man they hold -- judged on
the season term alone against each roster's own wire replacement. It is
counted by the caller that already has the wire and handed in, because this
module prices one move and does not read fourteen rosters. It labels; it
hides nothing and it moves no number.

`amount` is still the market number: the median or 75th percentile winning
bid of the rank bucket. docs/faab.md section 0 is where it would change, and
says what the replay found.

THE FIT

Every executed WAIVER claim in a season with FAAB
(`league_seasons.uses_faab`) carries a bid and a player. The player is
placed among the free agents of the day he was claimed -- players with a
game line in that matchup period and no `daily_lineup_slots` row in it,
the definition `scripts/waiver_value.py` uses -- ranked by value, and his
position is the claim's rank. The median and the 75th percentile winning
bid are then taken per rank bucket. The buckets are wide because the
sample is small: the handful of players worth a real bid, the next ten,
the rest of the startable wire, and everyone else.

RANKING BY VALUE, WITHOUT A SCHEDULE

Section 4.5 ranks by the rest-of-season line's weight. A rank is ordinal,
and every NBA team plays the same 82 games, so over the rest of a season
two healthy players' remaining game counts differ by a game or two: the
ordering by rest-of-season value is the ordering by per-game value. That
matters because `pro_team_games` only exists from 2027, when the listener
started, and the one FAAB season on record is 2026. So the fit ranks on
the knowable per-game line's `weight`, which needs no schedule, and
`app.pickups.season` ranks the live wire on its rest-of-season line's
weight. The two are the same ordering.

The tilt is off in the fit: it keys on listener events, and no season being
fitted has any.

THE RECOMMENDATION

The 75th percentile when the move is worth twice its hurdle, the median
otherwise: pay up for the move that is clearly worth making, pay the going
rate for the one that is merely worth making. Then two caps. Never more
than the pot, and never more than the share of the pot the rest of the
season deserves -- with a third of the weeks left, a third of the money.
That is the guard against emptying the budget in November, and it is the
one place this module refuses a number the history would allow.

The output carries the bucket's range and sample so the number is
checkable, and the note says how thin the history is. One FAAB season is
one season; 2027 will be the second.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, median, quantiles

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    PlayerGameStat,
    Team,
    Transaction,
    TransactionItem,
)
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.projection import per_game_line
from app.pickups.state import EXECUTED, period_for_day
from app.pickups.stream import weight
from app.scoring.replacement import TYPICAL_PICKUP

#: Rank buckets, as docs/pickups.md section 4.5 names them: (label, first
#: rank, last rank). The last bucket is open-ended.
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1-5", 1, 5),
    ("6-15", 6, 15),
    ("16-40", 16, 40),
    ("41+", 41, None),
)

#: A claim of this type, in this state, is a bid that won.
WAIVER = "WAIVER"

#: A claim of this type, in this state, is a bid that LOST: ESPN's reason for
#: a waiver claim whose player was no longer a valid source by the time it was
#: processed, which in this league's record is always the same fact -- a
#: higher bid on the same player, the same day, took him first. Verified
#: against 2026 in the module docstring.
LOST = "FAILED_INVALIDPLAYERSOURCE"

#: How far above its hurdle a move has to be worth before the bid goes to
#: the 75th percentile rather than the median (docs/pickups.md section 4.5).
AGGRESSIVE_MULTIPLE = 2.0

#: Fewer FAAB seasons than this and the fit is a curiosity, not a
#: distribution. The league's first FAAB season was 2026.
THIN_SEASONS = 2

#: How many of a day's free agents are priced with the knowable line. A
#: rank past the last bucket's floor changes no bucket, so the day's pool is
#: narrowed first by the cheapest measure there is -- what each man has
#: produced in the last fortnight -- and only these are ranked properly.
#: Three times the last boundary, so a man the fortnight understates has
#: room to climb into the buckets that matter.
RANK_POOL = 120

#: Days of the pre-filter's production window, the same fortnight
#: `app.scoring.replacement` measures a pickup over.
PREFILTER_DAYS = 14

#: What the caps are called in the output.
CAP_FAAB = "faab_remaining"
CAP_SHARE = "share_of_the_pot"

#: The ladder's rungs: the win chances a manager is offered a dollar for.
#: Three, because the point of a ladder is that the price of certainty is
#: visible -- the cheap bid that usually wins, the one that nearly always
#: does, and the one that is buying the last tenth.
LADDER_RUNGS: tuple[float, ...] = (0.50, 0.75, 0.90)

#: Days the exchange rate's delivery is measured over in docs/faab.md, the
#: same window `scripts/pickups_backtest.py` scores a rest-of-season move
#: over. Recorded here because the published rate is quoted in these units;
#: the rule's own shadow price is per week and needs no window.
RATE_WINDOW_DAYS = 30

#: The most a win curve is drawn out to. Above the highest bid this league
#: has ever recorded every curve is flat at 1.0, so the table stops there.
CURVE_HEADROOM = 1


@dataclass(frozen=True)
class ClaimEvent:
    """One (day, player) at least one claim was filed on, and its whole field.

    `winning_bid` is the executed claim's; `losing_bids` is every bid that
    lost him the same day (`LOST`). An event with no losing bid is
    uncontested, which is half of this league's record and the reason $0 is
    worth a row of its own in the curve.
    """

    day: int
    player_id: int
    winning_bid: int
    losing_bids: tuple[int, ...]
    rank: int

    @property
    def contested(self) -> bool:
        return bool(self.losing_bids)

    @property
    def at_the_top(self) -> int:
        """Bids already standing at the winning price, the winner included."""
        return 1 + sum(1 for bid in self.losing_bids if bid == self.winning_bid)

    def chance(self, amount: int) -> float:
        """What a bid of `amount` would have taken off this field.

        Above the top bid it wins outright; level with it, it joins a tie
        ESPN breaks on a waiver priority nothing here can reconstruct, so it
        is credited one share of the hat; below it, nothing.
        """
        if amount > self.winning_bid:
            return 1.0
        if amount == self.winning_bid:
            return 1.0 / (self.at_the_top + 1)
        return 0.0


@dataclass(frozen=True)
class CurveBucket:
    """`P(win | bid $x)` for one band of value ranks, dollar by dollar."""

    label: str
    #: Claim events in the bucket.
    n: int
    #: How many of them anybody else bid on.
    contested: int
    #: (dollar, chance), from $0 up to a dollar above the bucket's highest bid.
    points: tuple[tuple[int, float], ...]

    def chance(self, amount: int) -> float:
        """The curve at `amount`, flat at 1.0 past its last point."""
        found = 0.0
        for dollar, chance in self.points:
            if dollar > amount:
                break
            found = chance
        return found

    def rung(self, target: float) -> int:
        """The cheapest whole dollar whose chance reaches `target`."""
        for dollar, chance in self.points:
            if chance >= target:
                return dollar
        return self.points[-1][0] if self.points else 0


@dataclass(frozen=True)
class WinCurve:
    """What a dollar has actually won here, by rank bucket."""

    buckets: tuple[CurveBucket, ...]
    #: Claim events fitted, across every bucket.
    events: int
    #: How many of them nobody else bid on.
    uncontested: int

    @property
    def uncontested_share(self) -> float:
        return self.uncontested / self.events if self.events else 0.0

    def bucket_for(self, label: str) -> CurveBucket | None:
        for bucket in self.buckets:
            if bucket.label == label:
                return bucket
        return None


@dataclass(frozen=True)
class Rung:
    """One step of the ladder: a chance, and the dollar that buys it."""

    #: The chance the rung asked for, one of `LADDER_RUNGS`.
    asked: float
    amount: int
    #: The chance at the dollar actually offered, which is lower than `asked`
    #: whenever a cap pulled the rung down.
    win_chance: float
    #: Claim events the chance was read off.
    n: int


def curve_points(events: Sequence[ClaimEvent]) -> tuple[tuple[int, float], ...]:
    """`P(win | bid $x)` at every whole dollar, averaged over `events`."""
    if not events:
        return ()
    top = max(event.winning_bid for event in events) + CURVE_HEADROOM
    return tuple(
        (dollar, fmean([event.chance(dollar) for event in events])) for dollar in range(0, top + 1)
    )


def win_curve(events: Sequence[ClaimEvent]) -> WinCurve:
    """The curve per rank bucket, from claim events already ranked."""
    by_bucket: dict[str, list[ClaimEvent]] = {label: [] for label, _, _ in BUCKETS}
    for event in events:
        for label, first, last in BUCKETS:
            if event.rank >= first and (last is None or event.rank <= last):
                by_bucket[label].append(event)
                break
    buckets = tuple(
        CurveBucket(
            label=label,
            n=len(found),
            contested=sum(1 for event in found if event.contested),
            points=curve_points(found),
        )
        for label, _first, _last in BUCKETS
        if (found := by_bucket[label])
    )
    fitted = [event for bucket in buckets for event in by_bucket[bucket.label]]
    return WinCurve(
        buckets=buckets,
        events=len(fitted),
        uncontested=sum(1 for event in fitted if not event.contested),
    )


@dataclass(frozen=True)
class Bucket:
    """Winning bids for one band of value ranks."""

    label: str
    first_rank: int
    last_rank: int | None
    median: float
    upper_quartile: float
    low: int
    high: int
    #: Claims in the bucket.
    n: int

    def holds(self, rank: int) -> bool:
        return rank >= self.first_rank and (self.last_rank is None or rank <= self.last_rank)


@dataclass(frozen=True)
class BidFit:
    """What the league has paid, by rank bucket."""

    league_season_id: int
    seasons: tuple[int, ...]
    buckets: tuple[Bucket, ...]
    #: Claims fitted, across every bucket.
    claims: int
    #: What a dollar has won here, from the same claims and the losing bids
    #: beside them. Empty when the league has no claims on record.
    curve: WinCurve = WinCurve(buckets=(), events=0, uncontested=0)

    @property
    def thin(self) -> bool:
        return len(self.seasons) < THIN_SEASONS

    @property
    def note(self) -> str:
        if not self.claims:
            return "no winning FAAB bids on record; the recommendation is $0."
        seasons = ", ".join(str(season) for season in self.seasons)
        curve = (
            f" The ladder reads {self.curve.events} claim events, "
            f"{self.curve.uncontested_share:.0%} of which nobody else bid on."
            if self.curve.events
            else ""
        )
        if self.thin:
            return (
                f"one FAAB season ({seasons}) is thin: {self.claims} winning bids are the "
                f"whole record, so read the range rather than the number.{curve}"
            )
        return f"{self.claims} winning bids across {seasons}.{curve}"

    def bucket_for(self, rank: int) -> Bucket | None:
        for bucket in self.buckets:
            if bucket.holds(rank):
                return bucket
        return None


@dataclass(frozen=True)
class Bid:
    """What to bid, why that much, and what the history behind it looks like."""

    amount: int
    rank: int
    bucket: str
    #: "75th percentile" or "median": which statistic the amount came from.
    basis: str
    #: What that statistic is, before the caps.
    uncapped: float
    #: The bucket's lowest and highest winning bid, and how many there were.
    low: int
    high: int
    sample: int
    #: `CAP_FAAB`, `CAP_SHARE`, or None when the history's number stood.
    capped_by: str | None
    note: str
    #: What the move is worth to this roster, in dollars, at the shadow price
    #: of this manager's own budget (the module docstring).
    worth_dollars: int = 0
    #: The dollar above which the move stops clearing its bar. Never below 0.
    ceiling: int = 0
    #: The shadow price itself, categories a week per dollar, so every figure
    #: above is checkable, and the sentence saying where it came from.
    rate: float = 0.0
    rate_note: str = ""
    #: A chance, and the dollar that buys it, cheapest first.
    ladder: tuple[Rung, ...] = ()
    #: How many other rosters this league's wire says the man clears the bar
    #: for today. None when the caller did not count.
    competition: int | None = None


def value_rank(player_id: int, weights: Mapping[int, float]) -> int:
    """A player's 1-based rank among the day's free agents, best first.

    `weights` is every free agent's value, the player among them. A player
    the caller did not weigh ranks last, which is what an unvalued man is.
    """
    mine = weights.get(player_id)
    if mine is None:
        return len(weights) + 1
    ahead = sum(
        1
        for other, value in weights.items()
        if value > mine or (value == mine and other < player_id)
    )
    return 1 + ahead


def shadow_price(faab_remaining: int, weeks_covered: float, typical: float) -> float:
    """What one FAAB dollar costs this roster, categories a week.

    The budget left, spread over the weeks it has to cover, against what an
    ordinary claim returns in a week: `faab_remaining / weeks_covered`
    dollars buy one week, and one week is worth `typical`. A manager with no
    money left has nothing to spend and no shadow price, which is 0.0 and a
    ceiling of nothing.
    """
    if faab_remaining <= 0 or weeks_covered <= 0:
        return 0.0
    return typical * weeks_covered / faab_remaining


def worth_of(per_week: float, faab_remaining: int, weeks_covered: float, typical: float) -> float:
    """What a move worth `per_week` categories is worth, in dollars."""
    rate = shadow_price(faab_remaining, weeks_covered, typical)
    if rate <= 0.0:
        return 0.0
    return per_week / rate


def ladder_for(
    bucket: CurveBucket | None,
    limit: int,
    rungs: Sequence[float] = LADDER_RUNGS,
) -> tuple[Rung, ...]:
    """The rungs, each capped at `limit` and re-read at the dollar offered."""
    if bucket is None:
        return ()
    return tuple(
        Rung(
            asked=asked,
            amount=(offered := max(0, min(limit, bucket.rung(asked)))),
            win_chance=bucket.chance(offered),
            n=bucket.n,
        )
        for asked in rungs
    )


def recommend_bid(
    delta: float,
    hurdle: float,
    rank: int,
    faab_remaining: int,
    weeks_remaining: float,
    total_weeks: float,
    fit: BidFit,
    *,
    per_week: float | None = None,
    weeks_covered: float | None = None,
    typical: float = TYPICAL_PICKUP,
    competition: int | None = None,
) -> Bid:
    """What to bid on a move worth `delta`, for a player ranked `rank`.

    `delta` and `hurdle` are in the same currency, expected categories a
    week. The caps are the module docstring's: the pot, and the share of
    the pot proportional to the weeks left, rounded up so a single week
    can still buy something.

    `per_week` is the move's net spread over the weeks it covers, which is
    what the worth and the ceiling are priced off; it defaults to `delta`,
    which is that number for every caller that passes one. `weeks_covered`
    is the weeks the budget has to last, defaulting to the weeks after this
    one plus this one. `competition` is how many other rosters the wire says
    the man clears the bar for, counted by the caller that has the wire.
    """
    bucket = fit.bucket_for(rank)
    aggressive = hurdle > 0 and delta >= AGGRESSIVE_MULTIPLE * hurdle
    basis = "75th percentile" if aggressive else "median"
    weekly = delta if per_week is None else per_week
    covered = (weeks_remaining + 1.0) if weeks_covered is None else weeks_covered
    rate = shadow_price(faab_remaining, covered, typical)
    worth = max(0, math.floor(worth_of(weekly, faab_remaining, covered, typical)))
    ceiling = max(0, math.floor(worth_of(weekly - hurdle, faab_remaining, covered, typical)))
    share = share_cap(faab_remaining, weeks_remaining, total_weeks)
    rate_note = (
        f"a dollar costs {rate:.3f} categories a week: ${faab_remaining} left over "
        f"{covered:.1f} weeks, against the {typical:.2f} an ordinary claim returns."
        if rate > 0
        else "no budget left, so no dollar has a price and the ceiling is $0."
    )
    ladder = ladder_for(fit.curve.bucket_for(bucket.label) if bucket else None, min(ceiling, share))
    if bucket is None:
        return Bid(
            amount=0,
            rank=rank,
            bucket="none",
            basis=basis,
            uncapped=0.0,
            low=0,
            high=0,
            sample=0,
            capped_by=None,
            note=fit.note,
            worth_dollars=worth,
            ceiling=ceiling,
            rate=rate,
            rate_note=rate_note,
            ladder=ladder,
            competition=competition,
        )

    uncapped = bucket.upper_quartile if aggressive else bucket.median
    amount = max(0, round(uncapped))
    capped_by: str | None = None
    if amount > faab_remaining:
        amount, capped_by = faab_remaining, CAP_FAAB
    if amount > share:
        amount, capped_by = share, CAP_SHARE
    return Bid(
        amount=amount,
        rank=rank,
        bucket=bucket.label,
        basis=basis,
        uncapped=uncapped,
        low=bucket.low,
        high=bucket.high,
        sample=bucket.n,
        capped_by=capped_by,
        note=fit.note,
        worth_dollars=worth,
        ceiling=ceiling,
        rate=rate,
        rate_note=rate_note,
        ladder=ladder,
        competition=competition,
    )


def share_cap(faab_remaining: int, weeks_remaining: float, total_weeks: float) -> int:
    """The most of the pot the weeks left justify spending on one claim."""
    if total_weeks <= 0:
        return faab_remaining
    share = faab_remaining * min(1.0, max(0.0, weeks_remaining / total_weeks))
    return min(faab_remaining, math.ceil(share))


#: Fits already built, by league season id. A fit reads a few hundred
#: transactions and prices a few thousand player-days, which is seconds; it
#: never changes within a run, and both the API and the CLI ask for it once
#: per request.
_CACHE: dict[int, BidFit] = {}


def clear_cache() -> None:
    """Forget every fitted season. For tests, which build new leagues."""
    _CACHE.clear()


def bid_fit(
    session: Session,
    league_season: LeagueSeason,
    *,
    use_cache: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
) -> BidFit:
    """Fit the winning bids of every FAAB season this league has played.

    The cache is checked against the number of claims on record rather than
    trusted outright: the API is a long-running process, and a season gains
    claims every week it is played.
    """
    cached = _CACHE.get(league_season.id) if use_cache else None
    if cached is not None and cached.claims == _claim_count(session, league_season.league_id):
        return cached
    fit = _fit(session, league_season, distributions)
    if use_cache:
        _CACHE[league_season.id] = fit
    return fit


def _claim_count(session: Session, league_id: int) -> int:
    """How many winning claims a fit of this league would read."""
    return int(
        session.scalar(select(func.count()).select_from(_claims_query(league_id).subquery())) or 0
    )


@dataclass(frozen=True)
class RankedClaim:
    """One winning claim, with the rank that buckets it and its whole field.

    Public because two readers need exactly this ranking and must not each
    build their own: the fit below, and `scripts/faab_bids.py`, which
    measures the curve, the exchange rate and the replay off it.
    """

    season: int
    league_season_id: int
    day: int
    player_id: int
    bid: int
    rank: int
    #: Every bid that lost this player the same day (`LOST`).
    losing_bids: tuple[int, ...] = ()

    @property
    def event(self) -> ClaimEvent:
        return ClaimEvent(
            day=self.day,
            player_id=self.player_id,
            winning_bid=self.bid,
            losing_bids=self.losing_bids,
            rank=self.rank,
        )


@dataclass(frozen=True)
class _Claim:
    season: int
    league_season_id: int
    day: int
    player_id: int
    bid: int


def _claims_query(league_id: int) -> Select[tuple[int, int, int, int, int | None]]:
    """Every executed winning waiver claim in a FAAB season of this league."""
    return (
        select(
            LeagueSeason.season,
            LeagueSeason.id,
            Transaction.scoring_period,
            TransactionItem.player_id,
            Transaction.bid_amount,
        )
        .join(LeagueSeason, LeagueSeason.id == Transaction.league_season_id)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .where(
            LeagueSeason.league_id == league_id,
            LeagueSeason.uses_faab.is_(True),
            Transaction.type == WAIVER,
            Transaction.status == EXECUTED,
            Transaction.bid_amount.is_not(None),
            TransactionItem.item_type == "ADD",
        )
        .order_by(LeagueSeason.season, Transaction.scoring_period)
    )


def _claims(session: Session, league_id: int) -> list[_Claim]:
    rows = session.execute(_claims_query(league_id)).all()
    return [
        _Claim(int(season), int(ls_id), int(day), int(player_id), int(bid))
        for season, ls_id, day, player_id, bid in rows
    ]


def _losing_bids(session: Session, league_id: int) -> dict[tuple[int, int, int], list[int]]:
    """(league season, day, player) -> every bid that lost him that day."""
    rows = session.execute(
        select(
            LeagueSeason.id,
            Transaction.scoring_period,
            TransactionItem.player_id,
            Transaction.bid_amount,
        )
        .join(LeagueSeason, LeagueSeason.id == Transaction.league_season_id)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .where(
            LeagueSeason.league_id == league_id,
            LeagueSeason.uses_faab.is_(True),
            Transaction.type == WAIVER,
            Transaction.status == LOST,
            Transaction.bid_amount.is_not(None),
            TransactionItem.item_type == "ADD",
        )
    ).all()
    out: dict[tuple[int, int, int], list[int]] = {}
    for ls_id, day, player_id, bid in rows:
        out.setdefault((int(ls_id), int(day), int(player_id)), []).append(int(bid))
    return out


def ranked_claims(
    session: Session,
    league_season: LeagueSeason,
    override: Sequence[CategoryDistribution] | None = None,
) -> tuple[RankedClaim, ...]:
    """Every winning claim of every FAAB season this league has played, ranked.

    The ranking is the module docstring's: the claimed player placed among
    the free agents of the day he was claimed, by the knowable per-game
    line's weight. Each claim carries the bids that lost him the same day, so
    the win curve and the fit read one ranking and not two.
    """
    claims = _claims(session, league_season.league_id)
    losing = _losing_bids(session, league_season.league_id)
    league_seasons: dict[int, LeagueSeason] = {}
    distributions: dict[int, Sequence[CategoryDistribution]] = {}
    ranked_days: dict[tuple[int, int], dict[int, float]] = {}
    out: list[RankedClaim] = []

    for claim in claims:
        season_row = league_seasons.get(claim.league_season_id)
        if season_row is None:
            found = session.get(LeagueSeason, claim.league_season_id)
            if found is None:
                continue
            season_row = league_seasons[claim.league_season_id] = found
        if claim.league_season_id not in distributions:
            distributions[claim.league_season_id] = (
                override if override is not None else category_distributions(session, season_row)
            )
        key = (claim.league_season_id, claim.day)
        weights = ranked_days.get(key)
        if weights is None:
            weights = ranked_days[key] = _weigh_wire(
                session, season_row, claim.day, distributions[claim.league_season_id]
            )
        # The claimed player was on the wire by definition, whatever the
        # day's lineup rows say about the team that has him now.
        if claim.player_id not in weights:
            weights = dict(weights)
            weights[claim.player_id] = _weigh_one(
                session,
                season_row,
                claim.day,
                claim.player_id,
                distributions[claim.league_season_id],
            )
            ranked_days[key] = weights
        out.append(
            RankedClaim(
                season=claim.season,
                league_season_id=claim.league_season_id,
                day=claim.day,
                player_id=claim.player_id,
                bid=claim.bid,
                rank=value_rank(claim.player_id, weights),
                losing_bids=tuple(
                    sorted(losing.get((claim.league_season_id, claim.day, claim.player_id), ()))
                ),
            )
        )
    return tuple(out)


def _fit(
    session: Session,
    league_season: LeagueSeason,
    override: Sequence[CategoryDistribution] | None = None,
) -> BidFit:
    claims = ranked_claims(session, league_season, override)
    seasons = tuple(sorted({claim.season for claim in claims}))
    by_bucket: dict[str, list[int]] = {label: [] for label, _, _ in BUCKETS}
    for claim in claims:
        for label, first, last in BUCKETS:
            if claim.rank >= first and (last is None or claim.rank <= last):
                by_bucket[label].append(claim.bid)
                break

    buckets = tuple(
        _bucket(label, first, last, bids)
        for label, first, last in BUCKETS
        if (bids := by_bucket[label])
    )
    return BidFit(
        league_season_id=league_season.id,
        seasons=seasons,
        buckets=buckets,
        claims=sum(bucket.n for bucket in buckets),
        curve=win_curve([claim.event for claim in claims]),
    )


def _bucket(label: str, first: int, last: int | None, bids: list[int]) -> Bucket:
    """One bucket's median, 75th percentile and range.

    `quantiles` needs two points; with one claim the single bid is both.
    """
    ordered = sorted(bids)
    upper = quantiles(ordered, n=4)[2] if len(ordered) > 1 else float(ordered[0])
    return Bucket(
        label=label,
        first_rank=first,
        last_rank=last,
        median=float(median(ordered)),
        upper_quartile=float(upper),
        low=ordered[0],
        high=ordered[-1],
        n=len(ordered),
    )


def free_agents_on(session: Session, league_season: LeagueSeason, day: int) -> set[int]:
    """Who was unrostered on `day`: a game line that period, no lineup row.

    `scripts/waiver_value.py`'s definition, widened from the day to the
    matchup period because a free agent only has a line on the days his NBA
    team plays and a claim can land on any of them.
    """
    period = period_for_day(session, league_season, day)
    if period is None or period.first_scoring_period is None:
        return set()
    first = int(period.first_scoring_period)
    last = int(period.final_scoring_period or first)
    played = session.scalars(
        select(distinct(PlayerGameStat.player_id)).where(
            PlayerGameStat.season == league_season.season,
            PlayerGameStat.scoring_period.between(first, last),
            PlayerGameStat.played.is_(True),
        )
    ).all()
    held = session.scalars(_held_in(league_season, first, last)).all()
    return {int(player_id) for player_id in played} - {int(player_id) for player_id in held}


def _held_in(league_season: LeagueSeason, first: int, last: int) -> Select[tuple[int]]:
    return (
        select(distinct(DailyLineupSlot.player_id))
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .where(
            Team.league_season_id == league_season.id,
            DailyLineupSlot.scoring_period.between(first, last),
        )
    )


def _weigh_wire(
    session: Session,
    league_season: LeagueSeason,
    day: int,
    distributions: Sequence[CategoryDistribution],
) -> dict[int, float]:
    """Every free agent of `day` worth ranking, with his per-game weight."""
    wire = free_agents_on(session, league_season, day)
    if not wire:
        return {}
    shortlist = _shortlist(session, int(league_season.season), day, wire)
    return {
        player_id: _weigh_one(session, league_season, day, player_id, distributions)
        for player_id in shortlist
    }


def _weigh_one(
    session: Session,
    league_season: LeagueSeason,
    day: int,
    player_id: int,
    distributions: Sequence[CategoryDistribution],
) -> float:
    line = per_game_line(session, int(league_season.season), player_id, day, tilt=False)
    return weight(line, distributions)


def _shortlist(session: Session, season: int, day: int, wire: set[int]) -> list[int]:
    """The `RANK_POOL` free agents with the most recent production.

    The pre-filter, so a day's ranking prices a hundred men and not five
    hundred. Composite production over the last fortnight, the same crude
    sum `scripts/waiver_value.py` ranks the wire by.
    """
    if len(wire) <= RANK_POOL:
        return sorted(wire)
    composite = (
        func.sum(
            PlayerGameStat.points
            + PlayerGameStat.rebounds
            + PlayerGameStat.assists
            + PlayerGameStat.steals
            + PlayerGameStat.blocks
            + PlayerGameStat.three_pointers_made
            - PlayerGameStat.turnovers
        )
    ).label("composite")
    rows = session.execute(
        select(PlayerGameStat.player_id, composite)
        .where(
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period.between(day - PREFILTER_DAYS, day - 1),
            PlayerGameStat.played.is_(True),
            PlayerGameStat.player_id.in_(sorted(wire)),
        )
        .group_by(PlayerGameStat.player_id)
        .order_by(composite.desc())
        .limit(RANK_POOL)
    ).all()
    return [int(player_id) for player_id, _ in rows]
