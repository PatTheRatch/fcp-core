"""What a winning FAAB bid has cost here, and what to bid now.

The question of docs/pickups.md section 4.5. A recommender that names a
pickup has said nothing useful until it says what to pay for him, and the
only honest source for that is what this league has actually paid.

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
from statistics import median, quantiles

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

    @property
    def thin(self) -> bool:
        return len(self.seasons) < THIN_SEASONS

    @property
    def note(self) -> str:
        if not self.claims:
            return "no winning FAAB bids on record; the recommendation is $0."
        seasons = ", ".join(str(season) for season in self.seasons)
        if self.thin:
            return (
                f"one FAAB season ({seasons}) is thin: {self.claims} winning bids are the "
                "whole record, so read the range rather than the number."
            )
        return f"{self.claims} winning bids across {seasons}."

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


def recommend_bid(
    delta: float,
    hurdle: float,
    rank: int,
    faab_remaining: int,
    weeks_remaining: float,
    total_weeks: float,
    fit: BidFit,
) -> Bid:
    """What to bid on a move worth `delta`, for a player ranked `rank`.

    `delta` and `hurdle` are in the same currency, expected categories a
    week. The caps are the module docstring's: the pot, and the share of
    the pot proportional to the weeks left, rounded up so a single week
    can still buy something.
    """
    bucket = fit.bucket_for(rank)
    aggressive = hurdle > 0 and delta >= AGGRESSIVE_MULTIPLE * hurdle
    basis = "75th percentile" if aggressive else "median"
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
        )

    uncapped = bucket.upper_quartile if aggressive else bucket.median
    share = share_cap(faab_remaining, weeks_remaining, total_weeks)
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


def _fit(
    session: Session,
    league_season: LeagueSeason,
    override: Sequence[CategoryDistribution] | None = None,
) -> BidFit:
    claims = _claims(session, league_season.league_id)
    seasons = tuple(sorted({claim.season for claim in claims}))
    by_bucket: dict[str, list[int]] = {label: [] for label, _, _ in BUCKETS}
    league_seasons: dict[int, LeagueSeason] = {}
    distributions: dict[int, Sequence[CategoryDistribution]] = {}
    ranked_days: dict[tuple[int, int], dict[int, float]] = {}

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
        rank = value_rank(claim.player_id, weights)
        for label, first, last in BUCKETS:
            if rank >= first and (last is None or rank <= last):
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
