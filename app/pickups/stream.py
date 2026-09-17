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
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.draft.lineup import max_matching, within_position_limits
from app.draft.pool import lineup_for, position_limits_for
from app.draft.targets import CategoryDistribution, _normal_cdf, category_distributions
from app.draft.valuation import INVERTED_CATEGORIES, PERCENTAGE_COMPONENTS
from app.inseason.startable import startable_starts
from app.listener.events import OUT
from app.pickups.projection import per_game_line
from app.pickups.state import (
    RosteredPlayer,
    TeamWeek,
    load_free_agents,
    load_team_week,
    season_calendar,
)
from app.scoring.lines import CategoryLine

#: Categories a move must add to be recommended (docs/pickups.md section
#: 4.3, a starting value pending the backtest). A typical pickup measured
#: 0.06-0.13 categories a week (STATUS.md), so this asks for a good one.
STREAM_HURDLE = 0.10

#: How many free agents are evaluated, the best by this week's line. Beyond
#: the top eighty the wire is players without a role, and the swap search
#: is quadratic in the pool.
POOL_SIZE = 80

#: Moves reported, best first.
REPORT_MOVES = 5

#: A category has "moved" when its win probability changes by this much.
MOVED_THRESHOLD = 0.01

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

        A filled day only counts when the move helps at all: dropping a
        starter for a body that plays on the empty day fills it and loses
        the week.
        """
        return self.delta >= hurdle or (self.fills_empty_day and self.delta > 0)


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
    empty_days: tuple[EmptyDay, ...]
    hurdle: float
    #: Free agents actually evaluated.
    pool_size: int
    faab_remaining: int
    open_slots: int
    ir_slot_free: bool

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
    def recommended(self) -> Move | None:
        """The best move that clears the hurdle, or None: no move today."""
        for move in self.moves:
            if move.clears(self.hurdle):
                return move
        return None


@dataclass(frozen=True)
class _Projection:
    line: CategoryLine
    starts: Mapping[int, int]
    #: Slot-days the roster left empty over the window.
    empty_slot_days: int


class _Week:
    """Projects a set of contenders over the remaining days.

    The seating on a day depends only on who is available that day, and most
    swaps do not touch most days, so each day's seating is cached by the ids
    available on it.
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
                (c for c in contenders.values() if day in c.days),
                key=lambda c: (-c.weight, c.player_id),
            )
            key = (day, tuple(c.player_id for c in available))
            seated = self._seated.get(key)
            if seated is None:
                seated = _seat(available, self._lineup)
                self._seated[key] = seated
            for player_id in seated:
                starts[player_id] = starts.get(player_id, 0) + 1
            empty += len(self._lineup) - len(seated)
        line = self._totals
        for player_id, count in starts.items():
            line = line + contenders[player_id].per_game.scaled(count)
        return _Projection(line=line, starts=starts, empty_slot_days=empty)


def _seat(available: Sequence[Contender], lineup: Sequence[str]) -> tuple[int, ...]:
    """The best-weighted set of `available` the lineup can seat at once.

    Greedy in weight order, keeping a player only when he raises the
    matching; exact for a transversal matroid (module docstring).
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
) -> StreamReport:
    """The streaming report for ESPN team `team_id` on scoring period `today`.

    `pool` names the free agents to consider instead of the latest pass's
    wire; `distributions` stands in for the season's measured ones. Both
    exist for tests and the backtest. `tilt` switches the minutes tilt.
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
    wire = _ranked_wire(
        [
            contender(player)
            for player in load_free_agents(session, league_season, week, player_ids=pool)
            if player.player_id not in held
        ],
        pool_size,
    )

    engine = _Week(days, lineup, week.my_totals)
    base = engine.project(mine)
    empty_days = _empty_days(week, wire, lineup)

    if week.opponent_team_id is None:
        return _report(week, base, week.opp_totals, {}, (), empty_days, hurdle, len(wire))

    opponent = load_team_week(session, league_season, week.opponent_team_id, today)
    theirs = {player.player_id: contender(player) for player in opponent.active}
    their_line = _Week(days, lineup, opponent.my_totals).project(theirs).line

    before = head_to_head(base.line, their_line, distributions, len(days))
    positions = {player.player_id: player.position for player in week.roster}
    seats_now = max_matching({p.player_id: p.eligible for p in week.active}, lineup)

    def legal(active: Mapping[int, Contender], roster_positions: Iterable[str | None]) -> bool:
        if not within_position_limits(roster_positions, limits):
            return False
        return max_matching({c.player_id: c.player.eligible for c in active.values()}, lineup) >= (
            seats_now
        )

    def evaluate(
        kind: str, add: Contender, drop: Contender | None, to_ir: Contender | None
    ) -> Move:
        active = {k: v for k, v in mine.items() if k not in (_id(drop), _id(to_ir))}
        active[add.player_id] = add
        after_projection = engine.project(active)
        after = head_to_head(after_projection.line, their_line, distributions, len(days))
        return Move(
            kind=kind,
            add=add.player,
            drop=drop.player if drop is not None else None,
            to_ir=to_ir.player if to_ir is not None else None,
            delta=sum(after.values()) - sum(before.values()),
            add_starts=after_projection.starts.get(add.player_id, 0),
            drop_starts=base.starts.get(drop.player_id, 0) if drop is not None else 0,
            shifts=tuple(CategoryShift(key, before[key], after[key]) for key in before),
            fills_empty_day=after_projection.empty_slot_days < base.empty_slot_days,
        )

    moves: list[Move] = []
    for add in wire:
        for drop in mine.values():
            active = {k: v for k, v in mine.items() if k != drop.player_id}
            active[add.player_id] = add
            roster_positions = [
                *(position for pid, position in positions.items() if pid != drop.player_id),
                add.player.position,
            ]
            if legal(active, roster_positions):
                moves.append(evaluate(SWAP, add, drop, None))
        if week.open_slots > 0:
            active = {**mine, add.player_id: add}
            if legal(active, [*positions.values(), add.player.position]):
                moves.append(evaluate(ADD, add, None, None))
        if week.ir_slot_free:
            for hurt in mine.values():
                if (hurt.player.injury_status or "").upper() != OUT:
                    continue
                active = {k: v for k, v in mine.items() if k != hurt.player_id}
                active[add.player_id] = add
                if legal(active, [*positions.values(), add.player.position]):
                    moves.append(evaluate(IR_MOVE, add, None, hurt))

    moves.sort(key=lambda move: (-move.delta, move.add.player_id, _id_of(move.drop)))
    return _report(
        week, base, their_line, before, _best_per_add(moves), empty_days, hurdle, len(wire)
    )


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
            if day in contender.days and contender.player.eligible & open_slots
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
    empty_days: tuple[EmptyDay, ...],
    hurdle: float,
    pool_size: int,
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
        empty_days=empty_days,
        hurdle=hurdle,
        pool_size=pool_size,
        faab_remaining=week.faab_remaining,
        open_slots=week.open_slots,
        ir_slot_free=week.ir_slot_free,
    )
