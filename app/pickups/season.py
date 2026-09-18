"""Who on the wire would make this roster better for the rest of the year.

The question of docs/pickups.md section 4.4, the long-term half of the
recommender. Where `app.pickups.stream` asks what wins the week in front of
us, this asks what a roster place is worth from today to the end of the
regular season, which is a different question with a different answer: a
four-game week means nothing here, and a role that has changed means
everything.

THE SAME OBJECTIVE, THE SAME SOLVER

No second value system. Every player, held or on the wire, becomes a draft
`Candidate` whose weekly line is his rest-of-season line divided by the
weeks left, and `app.draft.optimizer.optimize` answers every question by
being asked it differently:

- lock the whole roster and give it one more place: what a free add buys.
- lock the roster less one man and give it his place, with him excluded:
  what replacing that man buys. Over every man, that is the best single
  swap, and the three cheapest removals are the drop candidates -- the
  same computation read from the other end.
- lock the roster less two men: the best two-swap.

Every candidate is priced at zero, because price is not the constraint in
season: roster places are, and FAAB buys a place rather than a player (what
to bid is `app.pickups.bids`). The current roster is the `starts` seed, so
the roster with a man and the roster without him are neighbours rather than
two independent local searches, which is the reasoning `optimize`'s own
docstring gives for `starts`.

WEEKS, AND WHICH ONES

The horizon is the rest of the regular season, from the stored matchup
periods, because that is what the standings are decided on; once it is
over the horizon becomes the playoff periods, so the report still answers
during a title run. A player's games are his NBA team's over the same
window less the days ESPN has ruled him out of, and `rest_of_season_line`
then takes the availability discount once. Dividing by the weeks in the
window turns a season into a week, which is the unit the opponent
distributions are measured in.

STASHES

A free agent who is OUT is worth nothing this week and can be worth a great
deal in six. From 2027 the league carries one injured-reserve slot, so a
stash can be an add with no drop; without the slot free it costs a roster
place, and the report says which. He qualifies when ESPN has a return date
inside `STASH_WEEKS` and his value ignoring the injury would put him in the
pool at all.

THE HURDLES, AND THE VOLUME GUARD

A swap that costs FAAB has to clear more than a free add into an open
place, because it costs a player and money as well as a place. Both
numbers are the note's starting values, to be set by the backtest. Beside
them the report carries the team's adds over the last fortnight, and the
league's own finding that the managers who churned most returned least per
move: the recommender's most valuable answer is often no answer.

BOTH HORIZONS

The optimizer answers about an ordinary week from here on, which is only
half the question: a swap that is worth a tenth of a category a week may
still cost this week's matchup. So every `Swap` also carries a
`app.pickups.judge.Judgement`, whose week half is the streaming report's own
head-to-head (`app.pickups.stream.week_deltas`, so the two halves of the
recommender cannot disagree about what a week is worth) and whose season
half is the optimizer's change per week -- a with-and-without over the whole
roster, which already charges the drop, in team-fit terms rather than the
league-standard ones `judge` charges a stream by. The hurdles are read
against the net spread over the weeks it covers (`Judgement.per_week`), so
the two constants keep the units and the scale they were set in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Transaction, TransactionItem
from app.draft.optimizer import Candidate, RosterPlan, optimize, roster_totals, score
from app.draft.pool import lineup_for, position_limits_for, roster_size_for
from app.draft.targets import CategoryDistribution, category_distributions
from app.listener.events import OUT
from app.pickups.bids import Bid, BidFit, bid_fit, recommend_bid, value_rank
from app.pickups.judge import (
    DAYS_A_WEEK,
    Judgement,
    horizon,
    judge,
    load_spots,
    weeks_between,
)
from app.pickups.projection import rest_of_season_line
from app.pickups.state import (
    EXECUTED,
    RosteredPlayer,
    TeamWeek,
    build_players,
    load_free_agents,
    load_team_week,
    schedule,
    season_calendar,
    team_row,
)
from app.pickups.stream import CategoryShift, week_deltas, weight
from app.scoring.lines import CategoryLine
from app.scoring.replacement import ADD_TYPES

__all__ = [
    "CHURN_DAYS",
    "CHURN_FINDING",
    "DAYS_A_WEEK",
    "DROP_CANDIDATES",
    "FREE_ADD",
    "SEASON_HURDLE_FREE",
    "SEASON_HURDLE_PAID",
    "SEASON_POOL_SIZE",
    "STASH_WEEKS",
    "SWAP",
    "TWO_SWAP",
    "DropCandidate",
    "SeasonReport",
    "StashCandidate",
    "Swap",
    "VolumeGuard",
    "horizon",
    "season_recommendations",
    "weeks_between",
]

#: Expected categories a week a swap that costs FAAB has to add
#: (docs/pickups.md section 4.4, a starting value pending the backtest). A
#: typical pickup measured 0.06-0.13 categories a week (STATUS.md), so this
#: asks for an ordinary good one to be worth a claim.
SEASON_HURDLE_PAID = 0.05

#: The same for a free add into an open place: lower, because it costs
#: neither a player nor money, only the place.
SEASON_HURDLE_FREE = 0.02

#: Free agents carried into the optimizer, the best by rest-of-season value
#: (docs/pickups.md section 4.4, "N = 60"). Beyond this the wire is players
#: without a role, and the two-swap search is quadratic in the roster.
SEASON_POOL_SIZE = 60

#: Roster members named as droppable, worst first.
DROP_CANDIDATES = 3

#: How far ahead a stash's return date may be and still be worth a place
#: (docs/pickups.md section 4.4).
STASH_WEEKS = 6

#: The window the volume guard counts adds over: the fortnight
#: `docs/acquirable_value.md` measured a pickup's return across.
CHURN_DAYS = 14

#: What the league measured about churn, reported beside every move.
CHURN_FINDING = (
    "adds returned less the more of them a manager made: r = -0.63 over 98 "
    "team-seasons (docs/acquirable_value.md), and acquisition skill persists "
    "at +0.63 once volume is removed. Fewer, better moves."
)

#: Move kinds, so a caller can branch without counting players.
FREE_ADD = "free_add"
SWAP = "swap"
TWO_SWAP = "two_swap"


@dataclass(frozen=True)
class Swap:
    """One move the optimizer found, and what it does to an ordinary week."""

    #: `FREE_ADD`, `SWAP` or `TWO_SWAP`.
    kind: str
    #: The men leaving, none of them on a free add.
    out: tuple[RosteredPlayer, ...]
    into: tuple[RosteredPlayer, ...]
    #: Change in expected categories won per week.
    delta: float
    #: Every category's win probability before and after.
    shifts: tuple[CategoryShift, ...]
    #: The move in one currency over both horizons (`app.pickups.judge`):
    #: this week's head-to-head plus `delta` over the weeks after it.
    judgement: Judgement
    #: What to pay, when the move costs FAAB and clears its hurdle.
    bid: Bid | None

    @property
    def net(self) -> float:
        """Categories the move is worth over both horizons."""
        return self.judgement.delta_total

    @property
    def costs_faab(self) -> bool:
        """Whether this is a claim rather than filling an open place.

        A move that drops a player is a claim on a rostered league's wire
        and is charged the paid hurdle; an add into a place the roster
        already has is the free one (docs/pickups.md section 4.4).
        """
        return bool(self.out)

    def hurdle(self, paid: float, free: float) -> float:
        return paid if self.costs_faab else free

    def clears(self, paid: float, free: float) -> bool:
        """Whether the move is worth making, on the net over both horizons.

        The hurdles are categories a week, so the net is spread over the
        weeks it covers -- this matchup and the ones after it -- rather than
        compared to a bar in another unit.
        """
        return self.judgement.per_week >= self.hurdle(paid, free)

    def moved(self, threshold: float = 0.01) -> tuple[CategoryShift, ...]:
        """The categories the move changed, largest change first."""
        return tuple(
            sorted(
                (shift for shift in self.shifts if abs(shift.delta) >= threshold),
                key=lambda shift: -abs(shift.delta),
            )
        )


@dataclass(frozen=True)
class DropCandidate:
    """A roster member, and what replacing him with the best free agent buys.

    `delta` is positive when the roster is better without him. The three
    with the largest delta are the men whose removal costs least, which is
    the question section 4.4 asks.
    """

    player: RosteredPlayer
    replacement: RosteredPlayer | None
    delta: float


@dataclass(frozen=True)
class StashCandidate:
    """A free agent who is out now and worth a place when he returns."""

    player: RosteredPlayer
    expected_return_date: date
    weeks_away: float
    #: Where his healthy rest-of-season value would rank on the wire.
    healthy_rank: int
    #: False when the injured-reserve slot is free, so the stash costs
    #: nobody a place.
    needs_drop: bool


@dataclass(frozen=True)
class VolumeGuard:
    """How much this team has churned lately, and what that has been worth."""

    adds: int
    days: int
    finding: str = CHURN_FINDING


@dataclass(frozen=True)
class SeasonReport:
    team_id: int
    today: int
    #: Last scoring period the report plans for, inclusive.
    last_scoring_period: int
    weeks_remaining: float
    #: Weeks in the whole stretch, which is what the FAAB share is of.
    total_weeks: float
    #: Expected categories won in an ordinary week as the roster stands.
    expected_wins: float
    probabilities: Mapping[str, float]
    weekly: Mapping[str, float]
    best_add: Swap | None
    best_swap: Swap | None
    best_two_swap: Swap | None
    drops: tuple[DropCandidate, ...]
    stashes: tuple[StashCandidate, ...]
    churn: VolumeGuard
    #: The season as it stands, with no move: the projected record and the
    #: weeks a judgement is charged over (`app.pickups.judge`).
    outlook: Judgement
    hurdle_paid: float
    hurdle_free: float
    #: Free agents actually evaluated.
    pool_size: int
    faab_remaining: int
    open_slots: int
    ir_slot_free: bool

    @property
    def moves(self) -> tuple[Swap, ...]:
        """Every move found, best first by the net over both horizons."""
        found = [m for m in (self.best_add, self.best_swap, self.best_two_swap) if m is not None]
        return tuple(sorted(found, key=lambda move: -move.net))

    @property
    def recommended(self) -> Swap | None:
        """The best move that clears its hurdle, or None: no move today."""
        for move in self.moves:
            if move.clears(self.hurdle_paid, self.hurdle_free):
                return move
        return None


def season_recommendations(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    *,
    pool: Iterable[int] | None = None,
    pool_size: int = SEASON_POOL_SIZE,
    hurdle_paid: float = SEASON_HURDLE_PAID,
    hurdle_free: float = SEASON_HURDLE_FREE,
    tilt: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
    fit: BidFit | None = None,
) -> SeasonReport:
    """The rest-of-season report for ESPN team `team_id` on day `today`.

    `pool` names the free agents to consider instead of the latest pass's
    wire, `distributions` stands in for the season's measured ones and
    `fit` for the fitted bids; all three exist for tests and the backtest.
    `tilt` switches the minutes tilt in the projection.
    """
    week = load_team_week(session, league_season, team_id, today)
    season = int(league_season.season)
    first_day, last_day, today = horizon(session, league_season, today)
    days = tuple(range(today, last_day + 1))
    weeks = weeks_between(today, last_day)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
    categories = [distribution.abbreviation for distribution in distributions]
    lineup = lineup_for(league_season)
    limits = position_limits_for(league_season)

    def value(player: RosteredPlayer) -> tuple[CategoryLine, float]:
        line = rest_of_season_line(
            session,
            season,
            player.player_id,
            today,
            player.games_remaining_this_period,
            tilt=tilt,
            as_of=as_of,
        )
        return line, weight(line, distributions or ())

    # The roster and the wire, both counted over the whole stretch rather
    # than the days left in this week.
    on_ir = frozenset(player.player_id for player in week.roster if player.on_ir)
    roster = build_players(
        session,
        league_season,
        [player.player_id for player in week.roster],
        days,
        on_ir=on_ir,
    )
    held = {player.player_id for player in roster}
    wire = [
        player
        for player in load_free_agents(session, league_season, week, player_ids=pool, days=days)
        if player.player_id not in held
    ]

    values = {player.player_id: value(player) for player in (*roster, *wire)}
    weights = {player_id: found[1] for player_id, found in values.items()}
    ranked = sorted(wire, key=lambda p: (-weights[p.player_id], p.player_id))
    chosen = ranked[:pool_size]

    active = [player for player in roster if not player.on_ir]
    candidates = [
        _candidate(player, values[player.player_id][0], weeks) for player in (*active, *chosen)
    ]
    by_id = {player.player_id: player for player in (*roster, *wire)}
    locked = [player.player_id for player in active]
    slots = roster_size_for(league_season)

    base_totals = roster_totals(
        [c for c in candidates if c.player_id in set(locked)],
        categories,
    )
    base_expected, base_probabilities = score(base_totals, distributions)

    def move(out: tuple[int, ...], adds: int) -> tuple[float, RosterPlan] | None:
        """Drop `out`, fill the places, and say what it was worth.

        None when the optimizer could not fill a legal roster, which is
        what an illegal move is.
        """
        keep = [player_id for player_id in locked if player_id not in out]
        places = min(slots, len(keep) + adds)
        plan = optimize(
            candidates,
            distributions or (),
            budget=0,
            roster_slots=places,
            locked=keep,
            excluded=out,
            starts=[keep],
            # No shuffled restarts and no floor bid. The start is the
            # current roster with one or two places open, so the local
            # search is exact on the open places and a shuffle would only
            # cost time; and a floor of a dollar a place under a budget of
            # nothing would leave every second place unfilled.
            restarts=0,
            minimum_bid=0,
            lineup=lineup,
            limits=limits,
        )
        if len(plan.players) < places:
            return None
        return plan.expected_wins - base_expected, plan

    def arrivals(out: tuple[int, ...], plan: RosterPlan) -> tuple[int, ...]:
        gone = set(out)
        arrived = plan.player_ids - {player_id for player_id in locked if player_id not in gone}
        return tuple(player_id for player_id in sorted(arrived) if player_id in by_id)

    def swap_from(
        kind: str, out: tuple[int, ...], found: tuple[float, RosterPlan], judgement: Judgement
    ) -> Swap:
        delta, plan = found
        return Swap(
            kind=kind,
            out=tuple(by_id[player_id] for player_id in out),
            into=tuple(by_id[player_id] for player_id in arrivals(out, plan)),
            delta=delta,
            shifts=tuple(
                CategoryShift(key, base_probabilities[key], plan.win_probability[key])
                for key in base_probabilities
            ),
            judgement=judgement,
            bid=None,
        )

    singles: list[tuple[float, tuple[int, ...], RosterPlan]] = []
    for player_id in locked:
        found = move((player_id,), 1)
        if found is not None:
            singles.append((found[0], (player_id,), found[1]))
    singles.sort(key=lambda row: (-row[0], row[1]))

    pairs: list[tuple[float, tuple[int, ...], RosterPlan]] = []
    for index, first_out in enumerate(locked):
        for second_out in locked[index + 1 :]:
            out = (first_out, second_out)
            found = move(out, 2)
            if found is not None:
                pairs.append((found[0], out, found[1]))
    pairs.sort(key=lambda row: (-row[0], row[1]))

    # Every move the report will carry, found first and judged together: the
    # week's half of a judgement costs one rebuild of the week, so the three
    # are priced in one call rather than three.
    candidates_found: list[tuple[str, tuple[int, ...], tuple[float, RosterPlan]]] = []
    if week.open_slots > 0:
        filled = move((), 1)
        if filled is not None and filled[1].player_ids != frozenset(locked):
            candidates_found.append((FREE_ADD, (), filled))
    if singles:
        candidates_found.append((SWAP, singles[0][1], (singles[0][0], singles[0][2])))
    if pairs:
        candidates_found.append((TWO_SWAP, pairs[0][1], (pairs[0][0], pairs[0][2])))

    spots = load_spots(
        session,
        league_season,
        team_id,
        today,
        roster=[player.player_id for player in roster],
        wire=[player.player_id for player in wire],
        weekly={player_id: line.scaled(1.0 / weeks) for player_id, (line, _w) in values.items()},
        distributions=distributions,
    )
    weeks_deltas = week_deltas(
        session,
        league_season,
        team_id,
        today,
        [(arrivals(out, found[1]), out) for _kind, out, found in candidates_found],
        tilt=tilt,
        distributions=distributions,
    )
    judged = {
        kind: swap_from(
            kind,
            out,
            found,
            judge(
                spots,
                delta_week=delta_week,
                dropped=out,
                added=arrivals(out, found[1]),
                delta_season_per_week=found[0],
            ),
        )
        for (kind, out, found), delta_week in zip(candidates_found, weeks_deltas, strict=True)
    }
    best_add = judged.get(FREE_ADD)
    best_swap = judged.get(SWAP)
    best_two_swap = judged.get(TWO_SWAP)

    drops = tuple(
        DropCandidate(
            player=by_id[out[0]],
            replacement=_replacement(plan, locked, out, by_id),
            delta=delta,
        )
        for delta, out, plan in singles[:DROP_CANDIDATES]
    )

    faab_fit = fit if fit is not None else bid_fit(session, league_season)
    total_weeks = weeks_between(first_day, last_day)
    wire_weights = {player.player_id: weights[player.player_id] for player in wire}

    def priced(found: Swap | None) -> Swap | None:
        if found is None or not found.clears(hurdle_paid, hurdle_free) or not found.into:
            return found
        added = max(found.into, key=lambda player: wire_weights.get(player.player_id, 0.0))
        bid = recommend_bid(
            found.judgement.per_week,
            found.hurdle(hurdle_paid, hurdle_free),
            value_rank(added.player_id, wire_weights),
            week.faab_remaining,
            weeks,
            total_weeks,
            faab_fit,
        )
        return replace(found, bid=bid)

    return SeasonReport(
        team_id=team_id,
        today=today,
        last_scoring_period=last_day,
        weeks_remaining=weeks,
        total_weeks=total_weeks,
        expected_wins=base_expected,
        probabilities=base_probabilities,
        weekly=base_totals,
        best_add=priced(best_add),
        best_swap=priced(best_swap),
        best_two_swap=priced(best_two_swap),
        drops=drops,
        stashes=_stashes(
            session,
            league_season,
            week,
            wire,
            wire_weights,
            last_day=last_day,
            today=today,
            today_date=as_of,
            pool_size=pool_size,
            tilt=tilt,
            distributions=distributions,
        ),
        churn=VolumeGuard(
            adds=_adds_in_window(session, league_season, team_id, today), days=CHURN_DAYS
        ),
        outlook=judge(spots, delta_week=0.0, delta_season_per_week=0.0),
        hurdle_paid=hurdle_paid,
        hurdle_free=hurdle_free,
        pool_size=len(chosen),
        faab_remaining=week.faab_remaining,
        open_slots=week.open_slots,
        ir_slot_free=week.ir_slot_free,
    )


def _candidate(player: RosteredPlayer, line: CategoryLine, weeks: float) -> Candidate:
    """A player as the optimizer sees him: his rest of season, per week."""
    return Candidate(
        player_id=player.player_id,
        name=player.name,
        price=0,
        weekly={key: value / weeks for key, value in line.counts.items()},
        eligible=player.eligible,
        position=player.position,
    )


def _replacement(
    plan: RosterPlan,
    locked: Sequence[int],
    out: tuple[int, ...],
    by_id: Mapping[int, RosteredPlayer],
) -> RosteredPlayer | None:
    """Who the optimizer put in the dropped man's place."""
    kept = {player_id for player_id in locked if player_id not in set(out)}
    arrived = sorted(plan.player_ids - kept)
    return by_id.get(arrived[0]) if arrived else None


def _adds_in_window(session: Session, league_season: LeagueSeason, team_id: int, today: int) -> int:
    """Executed adds this team made in the last `CHURN_DAYS` scoring periods."""
    team = team_row(session, league_season, team_id)
    count = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .where(
            Transaction.league_season_id == league_season.id,
            Transaction.type.in_(ADD_TYPES),
            Transaction.status == EXECUTED,
            Transaction.scoring_period > today - CHURN_DAYS,
            Transaction.scoring_period <= today,
            TransactionItem.item_type == "ADD",
            TransactionItem.to_team_id == team.id,
        )
    )
    return int(count or 0)


def _stashes(
    session: Session,
    league_season: LeagueSeason,
    week: TeamWeek,
    wire: Sequence[RosteredPlayer],
    wire_weights: Mapping[int, float],
    *,
    last_day: int,
    today: int,
    today_date: date | None,
    pool_size: int,
    tilt: bool,
    distributions: Sequence[CategoryDistribution],
) -> tuple[StashCandidate, ...]:
    """Free agents who are out now and would be worth a place when they return.

    Value is counted as if he were healthy -- every game his NBA team has
    left -- because that is the question a stash asks: is this man worth a
    place once he is back? The games he will miss are already priced into
    whether the return date is inside `STASH_WEEKS`.
    """
    if today_date is None:
        return ()
    horizon_date = today_date + timedelta(weeks=STASH_WEEKS)
    hurt = [
        player
        for player in wire
        if (player.injury_status or "").upper() == OUT
        and player.expected_return_date is not None
        and today_date <= player.expected_return_date <= horizon_date
    ]
    if not hurt:
        return ()
    season = int(league_season.season)
    games = schedule(session, season, [player.pro_team_id for player in hurt], today, last_day)
    out: list[StashCandidate] = []
    for player in hurt:
        healthy_games = len(games.get(player.pro_team_id, {}))
        if not healthy_games:
            continue
        line = rest_of_season_line(
            session, season, player.player_id, today, healthy_games, tilt=tilt, as_of=today_date
        )
        healthy = weight(line, distributions)
        rank = 1 + sum(1 for value in wire_weights.values() if value > healthy)
        if rank > pool_size:
            continue
        assert player.expected_return_date is not None
        out.append(
            StashCandidate(
                player=player,
                expected_return_date=player.expected_return_date,
                weeks_away=(player.expected_return_date - today_date).days / DAYS_A_WEEK,
                healthy_rank=rank,
                needs_drop=not week.ir_slot_free,
            )
        )
    return tuple(sorted(out, key=lambda stash: stash.healthy_rank))
