"""Which swap most improves this week's matchup, and whether any is worth it.

The question of docs/pickups.md section 4.3, for one team on one day: my
projected week against my opponent's, the chance of winning each category,
and how much each possible move changes the sum.

THE WEEK

Each side's projected week is what it has posted so far plus what its
roster will add over the days left. What a roster adds is not the sum of
its players' games: ESPN starts ten a day, and a game on a day the lineup is
already full is a start going nowhere. So each remaining day is a matching
of the players with a game to the lineup (`app.inseason.startable`), and the
day's line is the per-game lines of the men the matching seats. Which men,
when there are more games than places, is decided by a per-game weight: the
sets of players a lineup can seat at once form a transversal matroid, so
taking players in weight order and keeping each one the matching can still
seat is the best seating, not an approximation of it. The weight is the
line's value against the league's spreads (counts over each category's
spread, turnovers against), the ordering the draft board uses; it decides
who sits on a full day and nothing else.

THE CHANCE

Head to head, not against the field: the opponent is known, and the
uncertainty is only in the days left. P(I win a category) is the normal
probability that my projected total beats his, with the spread of a whole
period's totals (`app.draft.targets.CategoryDistribution`) scaled by the
square root of the share of the period remaining. Turnovers are inverted.
Expected wins is the sum over the nine; a move's worth is the change in it.

THE MOVES

Every (drop, add) with the add on the wire and the roster still legal after
it: within the season's position limits, and able to seat at least as much
of the lineup as before. Plus an add into an open place when there is one,
and an add with an OUT player moved to injured reserve when the slot is
free. Ranked by the change in expected wins; the top five, one per added
player, are reported with the categories that moved and the starts each man
gets.

THE HURDLE

A move is recommended only when it adds `STREAM_HURDLE` categories or
fills a day the lineup would otherwise leave a slot empty; a marginal swap
is listed, not recommended. The design note argues this at length: the
league's own adds returned less the more of them a manager made, so a tool
that always names a pickup makes its user worse. The hurdle is the note's
starting value, to be set by the backtest.

THE PLAN, AND THE BUDGET

What is recommended is a short ordered plan rather than a single move,
because more than one add in a day is often right: one man will not play
again this week and another is a bum, and both places are worth changing.
Every move in the plan has to stand on its own -- a distinct pickup, a
distinct drop, each over the hurdle by itself -- and the second is found by
re-running the week with the first already made, not by reading the next
row of the list. That is what stops two moves being paid twice for filling
the same empty day, and it is why the second move costs a second search.
The plan is capped by `PLAN_MOVES` and by the period's own budget: this
league allows one add per day of a matchup period (`ADDS_PER_PERIOD_DAY`),
so with no adds left the report says so and names nothing, while still
listing what it found.

WAIVERS

A dropped player sits on waivers for 48 hours, and a free agent the league
has on waivers cannot play for us before the scoring period in which he
clears. So he is left out of the seating on the days before that
(`_Week.project`) -- he is still worth claiming, and a claim on him is a
FAAB bid that resolves when he clears, but the games he plays before then
are not ours.

BOTH HORIZONS, ONE CURRENCY

The change in this week's expected wins is no longer the whole answer, and
it never was: a week's gain bought by dropping a man worth half a category a
week for the next fifteen weeks is a loss. Every move therefore carries a
`app.pickups.judge.Judgement`, and the ranking and the hurdle read its
`delta_total` -- this week's change plus the change per week over the rest of
the season, times the weeks left. `Move.delta` is still this week's change
alone, because the report shows both. The season charge, not the drop rules,
is what stops a good player being dropped: every rostered man is still a
candidate to drop, and one who is worth keeping is simply too expensive.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.draft.lineup import max_matching, within_position_limits
from app.draft.pool import lineup_for, position_limits_for
from app.draft.targets import CategoryDistribution, _normal_cdf, category_distributions
from app.draft.valuation import INVERTED_CATEGORIES, PERCENTAGE_COMPONENTS
from app.inseason.startable import startable_starts
from app.listener.events import OUT
from app.pickups.judge import (
    OPENED_PLACE,
    TYPICAL_PICKUP,
    Judgement,
    SpotBook,
    judge,
    load_spots,
    weekly_lines,
)
from app.pickups.projection import per_game_line
from app.pickups.state import (
    RosteredPlayer,
    TeamWeek,
    build_players,
    has_free_agent_snapshots,
    load_free_agents,
    load_team_week,
    season_calendar,
)
from app.scoring.lines import CategoryLine

if TYPE_CHECKING:  # A cycle at runtime: `bids` ranks the wire with `weight`.
    from app.pickups.bids import Bid

#: Categories a move must add, over both horizons (`Judgement.delta_total`),
#: to be recommended (docs/pickups.md section 4.3). Patrick chose it on
#: 2026-09-18, off the backtest of that day, because adds are budgeted per
#: matchup period and FAAB is finite: the sweep over 0.05-0.20 read +0.17
#: categories a matchup at every setting, so the top of it bought the least
#: churn for no loss.
#:
#: Re-measured 2026-09-21 (docs/pickups_backtest.md, 14 teams, 616 decision
#: points), after the posted totals stopped counting the decision day twice:
#: the sweep now reads +0.15, +0.14, +0.15, +0.16 at 0.05, 0.10, 0.15, 0.20,
#: with the no-move rate rising from 4.8% to 11.0%. Lower than the numbers
#: this comment used to quote, and no longer flat -- 0.20 is the best of the
#: four rather than merely the cheapest -- so the reason for the number
#: stands and the figures behind it have changed. The bar barely bites
#: because an empty-day fill is recommended whenever it helps at all
#: (`Move.clears`); that rule, not this number, is the churn lever.
#:
#: **This is the fallback now, not the bar** (2026-09-22, docs/intake.md).
#: It was chosen on one league, and a league connected to this server has
#: its own bar: measured on its own backtest, or set by its own manager on
#: the account page. What a report actually uses is
#: `app.calibration.calibration(session, league, "stream_hurdle")`, and this
#: is what that falls back to. The number has not moved, and Full Court
#: Press's seeded row is Patrick's own choice of exactly this.
STREAM_HURDLE = 0.20

#: How many free agents are evaluated, the best by this week's line. Beyond
#: the top eighty the wire is players without a role, and the swap search
#: is quadratic in the pool.
POOL_SIZE = 80

#: Moves reported, best first.
REPORT_MOVES = 5

#: Moves a day's plan may hold. Patrick's rule, verbatim in spirit: "you can
#: add more than one player a day if it makes sense ... one player isn't
#: going to play for the rest of the week and another is a bum, you might
#: want two swaps that day. We don't want to just say don't do any moves."
#: Two, because each extra move costs a full re-run of the week and a third
#: swap in one day is churn rather than a plan; the period's remaining adds
#: cap it as well.
PLAN_MOVES = 2

#: A category has "moved" when its win probability changes by this much.
MOVED_THRESHOLD = 0.01

#: Days in a matchup period, which is what a week's claim buys: a bid made
#: with two days left is bid for two of seven.
PERIOD_DAYS = 7.0

SWAP = "swap"
ADD = "add"
IR_MOVE = "ir_move"


@dataclass(frozen=True)
class Contender:
    """A player as the week values him: his per-game line and his weight."""

    player: RosteredPlayer
    per_game: CategoryLine
    weight: float
    days: frozenset[int]

    @property
    def player_id(self) -> int:
        return self.player.player_id


@dataclass(frozen=True)
class CategoryShift:
    abbreviation: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class Move:
    """One possible move and what it does to the week."""

    #: `SWAP`, `ADD` (into an open place) or `IR_MOVE` (`to_ir` goes to
    #: injured reserve and `add` takes his place).
    kind: str
    add: RosteredPlayer
    drop: RosteredPlayer | None
    to_ir: RosteredPlayer | None
    #: Change in expected categories won this period.
    delta: float
    #: Starts the added player actually gets, of his games left.
    add_starts: int
    #: Starts the dropped player was getting.
    drop_starts: int
    #: Every category's win probability before and after.
    shifts: tuple[CategoryShift, ...]
    #: Whether the move seats a player on a day a slot was going empty.
    fills_empty_day: bool
    #: The move in one currency over both horizons (`app.pickups.judge`).
    #: This is what the ranking and the hurdle read; `delta` above is the
    #: week's half of it, kept because the report shows both.
    judgement: Judgement
    #: What to pay for the added player, on a move that clears the hurdle
    #: (`app.pickups.bids`). None on a move that does not, and when the
    #: caller asked for no bids.
    bid: Bid | None = None

    @property
    def net(self) -> float:
        """Categories the move is worth over both horizons."""
        return self.judgement.delta_total

    def moved(self, threshold: float = MOVED_THRESHOLD) -> tuple[CategoryShift, ...]:
        """The categories the move changed, largest change first."""
        return tuple(
            sorted(
                (shift for shift in self.shifts if abs(shift.delta) >= threshold),
                key=lambda shift: -abs(shift.delta),
            )
        )

    def clears(self, hurdle: float) -> bool:
        """Whether the move is worth making. See the module docstring.

        Read on the net over both horizons, not on the week: a week's gain
        that costs more than it is worth over the weeks left is not a move.

        A filled day only counts when the move helps at all: dropping a
        starter for a body that plays on the empty day fills it and loses
        the season, and the net is what says so.
        """
        return self.net >= hurdle or (self.fills_empty_day and self.net > 0)


@dataclass(frozen=True)
class EmptyDay:
    """A remaining day where a slot goes empty and a free agent could take it."""

    scoring_period: int
    empty_slots: tuple[str, ...]
    #: Free agents with a game that day who are eligible for an empty slot,
    #: best first.
    fillers: tuple[RosteredPlayer, ...]


@dataclass(frozen=True)
class StreamReport:
    team_id: int
    matchup_period: int
    scoring_periods_remaining: tuple[int, ...]
    opponent_team_id: int | None
    #: Expected categories won as things stand; zero on a bye.
    expected_wins: float
    probabilities: Mapping[str, float]
    projected: CategoryLine
    opponent_projected: CategoryLine
    #: Best first, one per added player, at most `REPORT_MOVES`; empty on a bye.
    moves: tuple[Move, ...]
    #: The plan: independent moves to make today, in order, each one over the
    #: hurdle on its own and judged with the ones before it already made.
    #: Empty when nothing is worth doing and when no adds are left.
    recommended: tuple[Move, ...]
    empty_days: tuple[EmptyDay, ...]
    #: The season as it stands, with no move: the projected record and the
    #: weeks the judgements are charged over (`app.pickups.judge`).
    outlook: Judgement
    hurdle: float
    #: Free agents actually evaluated.
    pool_size: int
    #: True when the wire was rebuilt from what was played rather than read
    #: from the listener's snapshots, which is every played season
    #: (`app.pickups.state.historical_free_agents`). A report says so.
    historical_wire: bool
    faab_remaining: int
    #: How far the bid feed's sum ran past the budget; see `app.pickups.state`.
    faab_overspent: int
    open_slots: int
    ir_slot_free: bool
    #: Adds already made this matchup period, and what it allows.
    adds_used: int
    adds_budget: int

    @property
    def today(self) -> int:
        return self.scoring_periods_remaining[0]

    @property
    def days_remaining(self) -> int:
        return len(self.scoring_periods_remaining)

    @property
    def on_bye(self) -> bool:
        return self.opponent_team_id is None

    @property
    def adds_left(self) -> int:
        """Adds still to spend this period; zero means no plan at all."""
        return max(0, self.adds_budget - self.adds_used)


@dataclass(frozen=True)
class _Projection:
    line: CategoryLine
    starts: Mapping[int, int]
    #: Slot-days the roster left empty over the window.
    empty_slot_days: int


@dataclass(frozen=True)
class _Board:
    """The roster a search looks out from.

    The first move of a plan is searched from the week as it stands; the
    second from the board the first one leaves behind, which is the only way
    the two can be judged without counting the same empty day twice.
    """

    #: The men who can be started, by id.
    mine: Mapping[int, Contender]
    #: The whole roster's positions, injured reserve included, for the limits.
    positions: Mapping[int, str | None]
    open_slots: int
    ir_slot_free: bool


@dataclass(frozen=True)
class _Search:
    """One sweep of the wire from one board: the moves, and what they are from."""

    moves: tuple[Move, ...]
    base: _Projection
    before: Mapping[str, float]


class _Week:
    """Projects a set of contenders over the remaining days.

    The seating on a day depends only on who is available that day, and most
    swaps do not touch most days, so each day's seating is cached by the ids
    available on it.

    Available means a game that day and nothing in the way of playing it: a
    free agent on waivers is not ours until the scoring period he clears in
    (`RosteredPlayer.seatable_on`), so his games before then seat nobody.
    """

    def __init__(self, days: Sequence[int], lineup: Sequence[str], totals: CategoryLine) -> None:
        self._days = tuple(days)
        self._lineup = tuple(lineup)
        self._totals = totals
        self._seated: dict[tuple[int, tuple[int, ...]], tuple[int, ...]] = {}

    def project(self, contenders: Mapping[int, Contender]) -> _Projection:
        starts: dict[int, int] = {}
        empty = 0
        for day in self._days:
            available = sorted(
                (c for c in contenders.values() if day in c.days and c.player.seatable_on(day)),
                key=lambda c: (-c.weight, c.player_id),
            )
            key = (day, tuple(c.player_id for c in available))
            seated = self._seated.get(key)
            if seated is None:
                seated = seat(available, self._lineup)
                self._seated[key] = seated
            for player_id in seated:
                starts[player_id] = starts.get(player_id, 0) + 1
            empty += len(self._lineup) - len(seated)
        line = self._totals
        for player_id, count in starts.items():
            line = line + contenders[player_id].per_game.scaled(count)
        return _Projection(line=line, starts=starts, empty_slot_days=empty)


def seat(available: Sequence[Contender], lineup: Sequence[str]) -> tuple[int, ...]:
    """The best-weighted set of `available` the lineup can seat at once.

    Greedy in weight order, keeping a player only when he raises the
    matching; exact for a transversal matroid (module docstring). `available`
    is expected in weight order, which is the order the week projects in and
    the order a day's report reads back as "who beat whom to a place".

    Public because the day's own report (`app.pickups.today`) is this rule
    applied to a single day and must be the same rule: a second seating
    would be a second thing to be wrong, and the week page and the morning
    page would then disagree about who starts.
    """
    chosen: dict[int, frozenset[str]] = {}
    seated: list[int] = []
    for contender in available:
        if len(seated) >= len(lineup):
            break
        trial = {**chosen, contender.player_id: contender.player.eligible}
        if max_matching(trial, lineup) > len(seated):
            chosen = trial
            seated.append(contender.player_id)
    return tuple(seated)


def weight(per_game: CategoryLine, distributions: Sequence[CategoryDistribution]) -> float:
    """A per-game line's value against the league's spreads, for seating.

    Counts over each category's spread, turnovers against; percentages are
    left out, since a rate has no per-game size to weigh. This orders who
    starts on a full day and ranks the wire; it is not the objective.
    """
    total = 0.0
    for distribution in distributions:
        if distribution.abbreviation in PERCENTAGE_COMPONENTS or distribution.spread <= 0:
            continue
        value = per_game.get(distribution.abbreviation) / distribution.spread
        total += -value if distribution.abbreviation in INVERTED_CATEGORIES else value
    return total


def head_to_head(
    mine: CategoryLine,
    theirs: CategoryLine,
    distributions: Sequence[CategoryDistribution],
    days_remaining: int,
) -> dict[str, float]:
    """P(I win each category), from two projected weeks.

    The spread is the period's, scaled by the square root of the share of
    the period still to play. With nothing left the result is settled:
    ahead wins, behind loses, level is a coin.
    """
    categories = [distribution.abbreviation for distribution in distributions]
    my_totals = mine.totals(categories)
    their_totals = theirs.totals(categories)
    out: dict[str, float] = {}
    for distribution in distributions:
        key = distribution.abbreviation
        share = days_remaining / distribution.period_days if distribution.period_days > 0 else 1.0
        sigma = distribution.spread * math.sqrt(max(0.0, share))
        edge = my_totals[key] - their_totals[key]
        if distribution.lower_is_better:
            edge = -edge
        if sigma > 0:
            out[key] = _normal_cdf(edge / sigma)
        else:
            out[key] = 1.0 if edge > 0 else 0.0 if edge < 0 else 0.5
    return out


def stream_recommendations(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    *,
    pool: Iterable[int] | None = None,
    hurdle: float = STREAM_HURDLE,
    pool_size: int = POOL_SIZE,
    tilt: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
    bids: bool = True,
    floor: float = TYPICAL_PICKUP,
    opened: float = OPENED_PLACE,
) -> StreamReport:
    """The streaming report for ESPN team `team_id` on scoring period `today`.

    `pool` names the free agents to consider instead of the latest pass's
    wire; `distributions` stands in for the season's measured ones. Both
    exist for tests and the backtest. `tilt` switches the minutes tilt, and
    `bids` whether a move that clears the hurdle is priced in FAAB.

    `hurdle`, `floor` and `opened` are this league's own numbers when the
    caller has them (`app.calibration.Bars`, docs/intake.md); the defaults
    are the constants, which is what a league with no measurement of its own
    goes on using.
    """
    week = load_team_week(session, league_season, team_id, today)
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
    lineup = lineup_for(league_season)
    limits = position_limits_for(league_season)
    days = week.scoring_periods_remaining

    def contender(player: RosteredPlayer) -> Contender:
        per_game = per_game_line(session, season, player.player_id, today, tilt=tilt, as_of=as_of)
        return Contender(
            player=player,
            per_game=per_game,
            weight=weight(per_game, distributions),
            days=frozenset(player.game_days),
        )

    mine = {player.player_id: contender(player) for player in week.active}
    held = {player.player_id for player in week.roster}
    historical_wire = pool is None and not has_free_agent_snapshots(session, league_season)
    wire = _ranked_wire(
        [
            contender(player)
            for player in load_free_agents(session, league_season, week, player_ids=pool)
            if player.player_id not in held
        ],
        pool_size,
    )
    by_id = {found.player_id: found for found in wire}

    spots = _spots(
        session,
        league_season,
        team_id,
        today,
        week,
        wire,
        tilt=tilt,
        distributions=distributions,
        floor=floor,
        opened=opened,
    )
    outlook = judge(spots, delta_week=0.0, delta_season_per_week=0.0)

    engine = _Week(days, lineup, week.my_totals)
    empty_days = _empty_days(week, wire, lineup)
    board = _Board(
        mine=mine,
        positions={player.player_id: player.position for player in week.roster},
        open_slots=week.open_slots,
        ir_slot_free=week.ir_slot_free,
    )

    if week.opponent_team_id is None:
        base = engine.project(mine)
        return _report(
            week,
            base,
            week.opp_totals,
            {},
            (),
            (),
            empty_days,
            outlook,
            hurdle,
            len(wire),
            historical_wire,
        )

    opponent = load_team_week(session, league_season, week.opponent_team_id, today)
    theirs = {player.player_id: contender(player) for player in opponent.active}
    their_line = _Week(days, lineup, opponent.my_totals).project(theirs).line

    def search(board: _Board, available: Sequence[Contender], taken: frozenset[int]) -> _Search:
        """Every legal move from `board`, ranked, the best one per pickup.

        `taken` is the men a move earlier in the plan has just added. They
        are no candidate to drop -- adding a man and dropping him again is
        not two moves but none -- and they are off the wire for the
        replacement charge, since nobody else can claim them now.
        """
        book = replace(spots, wire=spots.wire - taken)
        base = engine.project(board.mine)
        before = head_to_head(base.line, their_line, distributions, len(days))
        seats_now = max_matching(
            {c.player_id: c.player.eligible for c in board.mine.values()}, lineup
        )

        def legal(active: Mapping[int, Contender], roster_positions: Iterable[str | None]) -> bool:
            if not within_position_limits(roster_positions, limits):
                return False
            return max_matching(
                {c.player_id: c.player.eligible for c in active.values()}, lineup
            ) >= (seats_now)

        def evaluate(
            kind: str, add: Contender, drop: Contender | None, to_ir: Contender | None
        ) -> Move:
            active = {k: v for k, v in board.mine.items() if k not in (_id(drop), _id(to_ir))}
            active[add.player_id] = add
            after_projection = engine.project(active)
            after = head_to_head(after_projection.line, their_line, distributions, len(days))
            delta = sum(after.values()) - sum(before.values())
            # A man moved to injured reserve keeps his place, so only a drop
            # is charged against the rest of the season.
            return Move(
                kind=kind,
                add=add.player,
                drop=drop.player if drop is not None else None,
                to_ir=to_ir.player if to_ir is not None else None,
                delta=delta,
                add_starts=after_projection.starts.get(add.player_id, 0),
                drop_starts=base.starts.get(drop.player_id, 0) if drop is not None else 0,
                shifts=tuple(CategoryShift(key, before[key], after[key]) for key in before),
                fills_empty_day=after_projection.empty_slot_days < base.empty_slot_days,
                judgement=judge(
                    book,
                    delta_week=delta,
                    dropped=() if drop is None else (drop.player_id,),
                    added=(add.player_id,),
                ),
            )

        moves: list[Move] = []
        for add in available:
            for drop in board.mine.values():
                if drop.player_id in taken:
                    continue
                active = {k: v for k, v in board.mine.items() if k != drop.player_id}
                active[add.player_id] = add
                roster_positions = [
                    *(
                        position
                        for pid, position in board.positions.items()
                        if pid != drop.player_id
                    ),
                    add.player.position,
                ]
                if legal(active, roster_positions):
                    moves.append(evaluate(SWAP, add, drop, None))
            if board.open_slots > 0:
                active = {**board.mine, add.player_id: add}
                if legal(active, [*board.positions.values(), add.player.position]):
                    moves.append(evaluate(ADD, add, None, None))
            if board.ir_slot_free:
                for hurt in board.mine.values():
                    if hurt.player_id in taken:
                        continue
                    if (hurt.player.injury_status or "").upper() != OUT:
                        continue
                    active = {k: v for k, v in board.mine.items() if k != hurt.player_id}
                    active[add.player_id] = add
                    if legal(active, [*board.positions.values(), add.player.position]):
                        moves.append(evaluate(IR_MOVE, add, None, hurt))

        moves.sort(key=lambda move: (-move.net, move.add.player_id, _id_of(move.drop)))
        reported = _best_per_add(moves)
        if bids:
            # Priced against the wire this report evaluated, so a pickup's
            # value rank means the same thing in the second move as the first.
            reported = _priced(session, league_season, week, reported, wire, hurdle)
        return _Search(moves=reported, base=base, before=before)

    # The plan. Each move after the first is found by searching again from
    # the board the one before it leaves, so the two never claim the same
    # empty day; the period's remaining adds and `PLAN_MOVES` cap the list,
    # and no adds left is an empty plan beside a full list of moves.
    first = search(board, wire, frozenset())
    wanted = min(PLAN_MOVES, week.adds_left)
    found = first
    available = wire
    taken: set[int] = set()
    plan: list[Move] = []
    while len(plan) < wanted:
        chosen = next((move for move in found.moves if move.clears(hurdle)), None)
        if chosen is None:
            break
        plan.append(chosen)
        taken.add(chosen.add.player_id)
        if len(plan) >= wanted:
            break
        board = _after(board, chosen, by_id[chosen.add.player_id])
        available = tuple(c for c in available if c.player_id != chosen.add.player_id)
        found = search(board, available, frozenset(taken))

    return _report(
        week,
        first.base,
        their_line,
        first.before,
        first.moves,
        tuple(plan),
        empty_days,
        outlook,
        hurdle,
        len(wire),
        historical_wire,
    )


def _spots(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    week: TeamWeek,
    wire: Sequence[Contender],
    *,
    tilt: bool,
    distributions: Sequence[CategoryDistribution] | None,
    floor: float = TYPICAL_PICKUP,
    opened: float = OPENED_PLACE,
) -> SpotBook:
    """What every man in play is worth to a roster place for the rest of the year.

    The week's own lines cannot answer this: a four-game week and a role
    change are different facts, and the season charge is about the second.
    So the roster and the evaluated wire are re-counted over the rest of the
    season, which is the same arithmetic `app.pickups.season` builds its
    candidates from.
    """
    calendar = season_calendar(session, int(league_season.season))
    as_of = calendar.date_of(today) if calendar is not None else None
    roster = [player.player_id for player in week.roster]
    pool = [contender.player_id for contender in wire]
    weekly, _weeks = weekly_lines(
        session, league_season, [*roster, *pool], today, tilt=tilt, as_of=as_of
    )
    return load_spots(
        session,
        league_season,
        team_id,
        today,
        roster=roster,
        wire=pool,
        weekly=weekly,
        distributions=distributions,
        floor=floor,
        opened=opened,
    )


def week_deltas(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    moves: Sequence[tuple[Sequence[int], Sequence[int]]],
    *,
    tilt: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
    waivers: Mapping[int, tuple[date, int]] | None = None,
    effective_day: int | None = None,
    opponent_move: tuple[Sequence[int], Sequence[int]] | None = None,
) -> list[float]:
    """This week's change in expected categories won, for each (added, dropped).

    The rest-of-season report needs the week's half of a judgement for moves
    it found by another route, and it must be the same number the streaming
    report would give: the same seating, the same head-to-head, the same
    knowable lines. So it is computed here rather than approximated there.
    Zero for every move on a bye, where there is no head to head at all.

    `waivers` is the caller's `app.pickups.state.waiver_state` for the men
    arriving, so a claim that cannot play until Thursday is seated here on
    the same days the streaming report would seat him.

    `effective_day` is the first day of this period the move is actually in
    force, for a move that cannot take effect today: a trade waits on the
    other manager and on the league's review (`app.trades`). The days before
    it are projected with the roster as it stands, on both sides of the
    comparison, so they cancel in the delta while still counting toward the
    totals the probabilities are read off. Seating is per day and independent,
    so splitting the window in two is exact rather than an approximation, and
    the default -- today -- is the whole window and the number this function
    has always returned.

    `opponent_move` is the same (added, dropped) applied to the opponent's
    roster from the same day, for the case where the man on the other side of
    the deal is also the man on the other side of this week's matchup. Without
    it a trade with this week's opponent would be judged against the roster he
    no longer has.
    """
    week = load_team_week(session, league_season, team_id, today)
    if week.opponent_team_id is None:
        return [0.0 for _ in moves]
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
    days = week.scoring_periods_remaining
    split = days[0] if effective_day is None else effective_day
    before_days = tuple(day for day in days if day < split)
    after_days = tuple(day for day in days if day >= split)

    def contender(player: RosteredPlayer) -> Contender:
        per_game = per_game_line(session, season, player.player_id, today, tilt=tilt, as_of=as_of)
        return Contender(
            player=player,
            per_game=per_game,
            weight=weight(per_game, distributions or ()),
            days=frozenset(player.game_days),
        )

    held = {player.player_id for player in week.roster}
    opponent = load_team_week(session, league_season, week.opponent_team_id, today)
    theirs_held = {player.player_id for player in opponent.roster}
    incoming = {player_id for added, _dropped in moves for player_id in added} - held
    if opponent_move is not None:
        incoming |= set(opponent_move[0]) - theirs_held
    arrivals = {
        player.player_id: contender(player)
        for player in build_players(session, league_season, incoming, days, waivers=waivers)
    }
    mine = {player.player_id: contender(player) for player in week.active}
    lineup = lineup_for(league_season)

    def moved(
        roster: Mapping[int, Contender], added: Sequence[int], dropped: Sequence[int]
    ) -> dict[int, Contender]:
        active = {k: v for k, v in roster.items() if k not in set(dropped)}
        for player_id in added:
            found = arrivals.get(player_id) or roster.get(player_id)
            if found is not None:
                active[player_id] = found
        return active

    theirs = {player.player_id: contender(player) for player in opponent.active}
    their_before = _Week(before_days, lineup, opponent.my_totals).project(theirs).line
    their_after = moved(theirs, *opponent_move) if opponent_move is not None else theirs
    their_line = _Week(after_days, lineup, their_before).project(their_after).line

    # The days before the move lands are the roster as it stands, in both
    # worlds; the days from it are the roster the move leaves.
    my_before = _Week(before_days, lineup, week.my_totals).project(mine).line
    engine = _Week(after_days, lineup, my_before)
    base_line = engine.project(mine).line
    base = sum(head_to_head(base_line, their_line, distributions, len(days)).values())

    out: list[float] = []
    for added, dropped in moves:
        after = head_to_head(
            engine.project(moved(mine, added, dropped)).line,
            their_line,
            distributions,
            len(days),
        )
        out.append(sum(after.values()) - base)
    return out


def _priced(
    session: Session,
    league_season: LeagueSeason,
    week: TeamWeek,
    moves: Sequence[Move],
    wire: Sequence[Contender],
    hurdle: float,
) -> tuple[Move, ...]:
    """A FAAB bid on every move that clears the hurdle.

    `app.pickups.bids` reads this module for its own ranking, so it is
    imported here rather than at the top: the two halves of the recommender
    would otherwise import each other.

    The rank is taken among the free agents this report evaluated, ranked by
    the same per-game weight the fit ranks a historical wire by. The bid's
    share of the pot is the week's share of the period, since a streaming
    claim is bought for the days that are left.
    """
    from app.pickups.bids import bid_fit, recommend_bid, value_rank

    if not any(move.clears(hurdle) for move in moves):
        return tuple(moves)
    fit = bid_fit(session, league_season)
    weights = {contender.player_id: contender.weight for contender in wire}
    priced: list[Move] = []
    for move in moves:
        if not move.clears(hurdle):
            priced.append(move)
            continue
        bid = recommend_bid(
            move.net,
            hurdle,
            value_rank(move.add.player_id, weights),
            week.faab_remaining,
            len(week.scoring_periods_remaining),
            PERIOD_DAYS,
            fit,
        )
        priced.append(replace(move, bid=bid))
    return tuple(priced)


def _best_per_add(ranked: Sequence[Move]) -> tuple[Move, ...]:
    """The top `REPORT_MOVES`, one per added player.

    Several drops for the same pickup are usually within a hair of each
    other, and five rows of one name would crowd out the second-best
    pickup, which is the one the manager may prefer for other reasons.
    """
    kept: list[Move] = []
    seen: set[int] = set()
    for move in ranked:
        if move.add.player_id in seen:
            continue
        seen.add(move.add.player_id)
        kept.append(move)
        if len(kept) >= REPORT_MOVES:
            break
    return tuple(kept)


def _after(board: _Board, move: Move, add: Contender) -> _Board:
    """The board `move` leaves behind, which the next move is searched from."""
    gone = {player.player_id for player in (move.drop, move.to_ir) if player is not None}
    mine = {player_id: c for player_id, c in board.mine.items() if player_id not in gone}
    mine[add.player_id] = add
    dropped = move.drop.player_id if move.drop is not None else None
    positions = {
        player_id: position
        for player_id, position in board.positions.items()
        if player_id != dropped
    }
    positions[add.player_id] = add.player.position
    return _Board(
        mine=mine,
        positions=positions,
        # A swap and an injured-reserve move both leave the roster the size
        # it was; only an add into an open place spends one of them. The IR
        # move spends the free slot, and the week carries that as a flag
        # rather than a count, so a second one is not offered.
        open_slots=board.open_slots - 1 if move.kind == ADD else board.open_slots,
        ir_slot_free=board.ir_slot_free and move.kind != IR_MOVE,
    )


def _id(contender: Contender | None) -> int | None:
    return contender.player_id if contender is not None else None


def _id_of(player: RosteredPlayer | None) -> int:
    return player.player_id if player is not None else 0


def _ranked_wire(wire: Sequence[Contender], pool_size: int) -> tuple[Contender, ...]:
    """The best `pool_size` free agents by this week's line: weight times games."""
    ranked = sorted(wire, key=lambda c: (-(c.weight * len(c.days)), c.player_id))
    return tuple(ranked[:pool_size])


def _empty_days(
    week: TeamWeek, wire: Sequence[Contender], lineup: Sequence[str]
) -> tuple[EmptyDay, ...]:
    """The days a slot goes empty that a free agent with a game could fill.

    The check the note wants reported on its own, from the same daily
    matching as everything else (`startable_starts`), without the
    probability model.
    """
    active = {player.player_id: player.eligible for player in week.active}
    games_by_day = {
        day: [player.player_id for player in week.active if day in player.game_days]
        for day in week.scoring_periods_remaining
    }
    out: list[EmptyDay] = []
    for day, slots in sorted(startable_starts(active, games_by_day, lineup).items()):
        if not slots.empty:
            continue
        open_slots = set(slots.empty)
        fillers = [
            contender.player
            for contender in wire
            if day in contender.days
            and contender.player.seatable_on(day)
            and contender.player.eligible & open_slots
        ]
        if fillers:
            out.append(EmptyDay(day, slots.empty, tuple(fillers)))
    return tuple(out)


def _report(
    week: TeamWeek,
    base: _Projection,
    their_line: CategoryLine,
    before: Mapping[str, float],
    moves: tuple[Move, ...],
    recommended: tuple[Move, ...],
    empty_days: tuple[EmptyDay, ...],
    outlook: Judgement,
    hurdle: float,
    pool_size: int,
    historical_wire: bool,
) -> StreamReport:
    return StreamReport(
        team_id=week.team_id,
        matchup_period=week.matchup_period,
        scoring_periods_remaining=week.scoring_periods_remaining,
        opponent_team_id=week.opponent_team_id,
        expected_wins=sum(before.values()),
        probabilities=dict(before),
        projected=base.line,
        opponent_projected=their_line,
        moves=moves,
        recommended=recommended,
        empty_days=empty_days,
        outlook=outlook,
        hurdle=hurdle,
        pool_size=pool_size,
        historical_wire=historical_wire,
        faab_remaining=week.faab_remaining,
        faab_overspent=week.faab_overspent,
        open_slots=week.open_slots,
        ir_slot_free=week.ir_slot_free,
        adds_used=week.adds_used,
        adds_budget=week.adds_budget,
    )
