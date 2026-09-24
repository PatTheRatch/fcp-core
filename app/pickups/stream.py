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

That day-by-day answer is kept as well as summed (`Schedule`): for each
remaining day and both rosters, the games by men who are not ruled out, how
many of them the lineup seats, and the starting places nobody can fill. It
is read off the projection's own record rather than worked out again, so a
grid drawn from it and the expected wins below can never disagree.

THE CHANCE

Head to head, not against the field: the opponent is known, and the
uncertainty is only in the days left. P(I win a category) is the normal
probability that my projected total beats his, with the spread of a whole
period's totals (`app.draft.targets.CategoryDistribution`) scaled by the
square root of the share of the period remaining and then by `SPREAD_SCALE`.
Turnovers are inverted. Expected wins is the sum over the nine; a move's
worth is the change in it.

`SPREAD_SCALE` is two, and it is the whole reason a week here is worth less
than it used to be. The measured spread is **one team's** total over a
period; what decides a category is the difference between two of them, and
the model had been reading a one-team spread as if it were that difference.
Replaying 2026 from thirty-eight mornings said so in the plainest way there
is (docs/projected_record.md section 0): at scale one, categories called at
95% were won 81% of the time and ones called at 5% were won 19%. At two the
table lines up almost everywhere. So every chance in this codebase is now
nearer the middle, every delta between two chances is smaller, and the same
0.20 bar catches fewer moves. That is the intended effect and not a side
effect.

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
from app.pickups.stash import Stash, held_stashes
from app.pickups.state import (
    PostedMan,
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

#: What every weekly spread is multiplied by inside `head_to_head`, before the
#: share-of-the-period scaling. **2.0, applied 2026-09-23, Patrick's decision.**
#:
#: What it is: `CategoryDistribution.spread` is the standard deviation of *one*
#: team's total in a category over a period of the ordinary length, measured on
#: this league's own results. A category is decided by the *difference* between
#: two such totals, and the model used that one-team spread for it, which made
#: every chance too sure of itself.
#:
#: Why 2.0: the projected-standings calibration replayed 2026 from thirty-eight
#: mornings and scored 47,880 per-category calls (docs/projected_record.md
#: section 0). At scale 1.0 the reliability table was pulled toward the middle
#: at every distance -- 95% calls won 81% of the time, 5% calls won 19% -- for
#: a Brier of 0.2288. The same run at sqrt(2), the number two *independent*
#: totals would give, reads 0.2202 and closes about half the gap; at 2.0 it
#: reads 0.2179 and the table is calibrated almost everywhere. The extra over
#: sqrt(2) is the part the independence argument does not cover: the nine
#: categories move together inside a week, and a roster's own week-to-week form
#: varies more than the league's cross-sectional spread suggests. The owner
#: chose the number that calibrates the published table rather than the
#: number the theory alone gives.
#:
#: What it costs: this is the one function every "chance of winning a category"
#: in the product comes from -- the pickup judgement, the streaming hurdle's
#: units, the bid sizing, the trade evaluator, the projected standings, the
#: day's lineup edge and the MCP tools over all of them -- so widening it
#: shrinks every delta and every net. All three calibrations were re-run whole
#: on 2026-09-23 and republished; see docs/spread_revision.md.
#:
#: Not applied to the draft. `app.draft.optimizer` and `app.draft.targets` use
#: the same spreads for a different question (a whole season against the field,
#: not a week against one opponent), and whether they want the same factor is
#: its own measurement. They are unchanged.
SPREAD_SCALE = 2.0

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
class WeekChange:
    """One move's effect on the matchup in front of us, chance by chance.

    What `week_deltas` has always computed, with the working shown: the nine
    chances before the move and after it, the starts each man gets either
    way, and whether the move seats somebody on a day a lineup slot was
    going empty. `week_deltas` is the sum of `delta` over these, so a caller
    that wants the table and a caller that wants the number are reading one
    computation and cannot disagree.

    On a bye there is no head to head at all: both mappings are empty and
    `delta` is zero, which is exactly what `week_deltas` has always returned.
    """

    opponent_team_id: int | None
    #: Days of the period still to play, today included.
    days_remaining: int
    #: P(this team wins the category) as things stand, and with the move made.
    before: Mapping[str, float]
    after: Mapping[str, float]
    #: Starts per player over the window, without the move and with it.
    starts_before: Mapping[int, int]
    starts: Mapping[int, int]
    #: True when the move seats a man on a day a slot was going empty.
    fills_empty_day: bool

    @property
    def expected_before(self) -> float:
        return sum(self.before.values())

    @property
    def expected_after(self) -> float:
        return sum(self.after.values())

    @property
    def delta(self) -> float:
        """Change in expected categories won this period."""
        return self.expected_after - self.expected_before

    @property
    def shifts(self) -> tuple[CategoryShift, ...]:
        """Every category's chance before and after, in the league's order."""
        return tuple(CategoryShift(key, self.before[key], self.after[key]) for key in self.before)

    def moved(self, threshold: float = MOVED_THRESHOLD) -> tuple[CategoryShift, ...]:
        """The categories the move changed, largest change first."""
        return tuple(
            sorted(
                (shift for shift in self.shifts if abs(shift.delta) >= threshold),
                key=lambda shift: -abs(shift.delta),
            )
        )


@dataclass(frozen=True)
class EmptyDay:
    """A remaining day where a slot goes empty and a free agent could take it."""

    scoring_period: int
    empty_slots: tuple[str, ...]
    #: Free agents with a game that day who are eligible for an empty slot,
    #: best first.
    fillers: tuple[RosteredPlayer, ...]


@dataclass(frozen=True)
class DayMan:
    """A man with a game on a day, and whether the lineup seats him."""

    player: RosteredPlayer
    seated: bool


@dataclass(frozen=True)
class SideGames:
    """One side's games on one day, or over the days left.

    `games` is the men on that roster with a game that day who are not ruled
    out of it (`app.pickups.state.playable_days`, injured reserve left out);
    `seated` is how many of them the lineup can start, which is the same
    seating the week is projected from. They differ when there are more games
    than places: a ten-place lineup seats ten of eleven games and the
    eleventh is a game that will not count. `open_places` is the other
    direction -- starting places no man of this roster can fill that day.

    Over the days left the three are simply summed, so `open_places` on a
    week is slot-days rather than slots.

    `men` is empty on a total, and on a day it is every man the `games` count
    counted, in the order the seating considered them: best first, each
    marked with whether he got a place.
    """

    games: int
    seated: int
    open_places: int
    men: tuple[DayMan, ...] = ()


@dataclass(frozen=True)
class DayGames:
    """One remaining scoring period, both sides of it."""

    scoring_period: int
    mine: SideGames
    #: None on a bye, where there is no other side.
    theirs: SideGames | None


@dataclass(frozen=True)
class Schedule:
    """The week's games day by day, and the totals the page reads across.

    Read off the projection itself rather than computed again, so the grid
    and the expected wins can never disagree about who plays and who starts.
    """

    days: tuple[DayGames, ...]
    mine_total: SideGames
    theirs_total: SideGames | None


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
    #: The score as it stands: what each side has posted in this period by
    #: the morning of `today`, under the live/replay rule `app.pickups.state`
    #: states. The projection above is this plus the days still to play.
    posted: CategoryLine
    opponent_posted: CategoryLine
    #: `POSTED_ESPN` or `POSTED_BOX_SCORES`, and the two totals broken out a
    #: man at a time from the stored box scores.
    posted_source: str
    posted_men: tuple[PostedMan, ...]
    opponent_posted_men: tuple[PostedMan, ...]
    #: Best first, one per added player, at most `REPORT_MOVES`; empty on a bye.
    moves: tuple[Move, ...]
    #: The plan: independent moves to make today, in order, each one over the
    #: hurdle on its own and judged with the ones before it already made.
    #: Empty when nothing is worth doing and when no adds are left.
    recommended: tuple[Move, ...]
    empty_days: tuple[EmptyDay, ...]
    #: Games and starts, day by day, for both sides. Additive: nothing above
    #: is computed from it and no number above moved when it arrived.
    schedule: Schedule
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
    #: The men on this roster ESPN has ruled out, longest out first: how long
    #: they have been out, the odds on each week, and what the place they are
    #: holding costs while it waits (`app.pickups.stash`). The season half of
    #: every number above already counts them for the games they are expected
    #: to play; this is the term that count cannot carry.
    stashed: tuple[Stash, ...] = ()

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
class _DaySeats:
    """One day of a projection: who could play, and whom the lineup seated."""

    #: Ids with a game that day and nothing in the way of playing it, in the
    #: order the seating considered them, which is weight order.
    available: tuple[int, ...]
    #: The ids of `available` the lineup seated, in the order they took a place.
    seated: tuple[int, ...]


@dataclass(frozen=True)
class _Projection:
    line: CategoryLine
    starts: Mapping[int, int]
    #: Slot-days the roster left empty over the window.
    empty_slot_days: int
    #: Each day of the window as it was seated. The schedule table is read
    #: off this, so it is the projection's own arithmetic and not a second
    #: pass over the same rosters.
    by_day: Mapping[int, _DaySeats]


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
        by_day: dict[int, _DaySeats] = {}
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
            by_day[day] = _DaySeats(available=key[1], seated=seated)
        line = self._totals
        for player_id, count in starts.items():
            line = line + contenders[player_id].per_game.scaled(count)
        return _Projection(line=line, starts=starts, empty_slot_days=empty, by_day=by_day)


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

    The spread is the period's, widened by `SPREAD_SCALE` and scaled by the
    square root of the share of the period still to play. With nothing left
    the result is settled: ahead wins, behind loses, level is a coin.

    A caller that hands in its own `distributions` -- the backtest, the
    calibration's `--sigma-scale` diagnostic -- is widened on top of what it
    hands in, so "as shipped" is the spreads as measured and nothing else
    done to them.
    """
    categories = [distribution.abbreviation for distribution in distributions]
    my_totals = mine.totals(categories)
    their_totals = theirs.totals(categories)
    out: dict[str, float] = {}
    for distribution in distributions:
        key = distribution.abbreviation
        share = days_remaining / distribution.period_days if distribution.period_days > 0 else 1.0
        sigma = distribution.spread * SPREAD_SCALE * math.sqrt(max(0.0, share))
        edge = my_totals[key] - their_totals[key]
        if distribution.lower_is_better:
            edge = -edge
        if sigma > 0:
            out[key] = _normal_cdf(edge / sigma)
        else:
            out[key] = 1.0 if edge > 0 else 0.0 if edge < 0 else 0.5
    return out


def contender_for(
    session: Session,
    league_season: LeagueSeason,
    player: RosteredPlayer,
    today: int,
    *,
    tilt: bool,
    distributions: Sequence[CategoryDistribution],
    as_of: date | None,
) -> Contender:
    """One man as a week values him: his per-game line, his weight, his days.

    The one construction, so a report, a named move and the projected
    standings all price the same man the same way.
    """
    per_game = per_game_line(
        session, int(league_season.season), player.player_id, today, tilt=tilt, as_of=as_of
    )
    return Contender(
        player=player,
        per_game=per_game,
        weight=weight(per_game, distributions),
        days=frozenset(player.game_days),
    )


def evaluated_wire(
    session: Session,
    league_season: LeagueSeason,
    week: TeamWeek,
    today: int,
    *,
    pool: Iterable[int] | None = None,
    pool_size: int = POOL_SIZE,
    tilt: bool = True,
    distributions: Sequence[CategoryDistribution],
    as_of: date | None,
) -> tuple[Contender, ...]:
    """The free agents a week report actually evaluates, in its own order.

    The wire less whoever this team already holds, cut to the best
    `pool_size` by this week's line (`_ranked_wire`). Public because the
    replacement charge a judgement makes is taken over exactly this set
    (`spot_book`), so a caller judging one named move has to read the same
    wire the search would have read or its season term is a different number.
    """
    held = {player.player_id for player in week.roster}
    return _ranked_wire(
        [
            contender_for(
                session,
                league_season,
                player,
                today,
                tilt=tilt,
                distributions=distributions,
                as_of=as_of,
            )
            for player in load_free_agents(session, league_season, week, player_ids=pool)
            if player.player_id not in held
        ],
        pool_size,
    )


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
        return contender_for(
            session,
            league_season,
            player,
            today,
            tilt=tilt,
            distributions=distributions or (),
            as_of=as_of,
        )

    mine = {player.player_id: contender(player) for player in week.active}
    historical_wire = pool is None and not has_free_agent_snapshots(session, league_season)
    wire = evaluated_wire(
        session,
        league_season,
        week,
        today,
        pool=pool,
        pool_size=pool_size,
        tilt=tilt,
        distributions=distributions,
        as_of=as_of,
    )
    by_id = {found.player_id: found for found in wire}

    spots = spot_book(
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
    # The men this report is already counting for a fraction of their games,
    # and what the wait costs the place (`app.pickups.stash`).
    stashed = held_stashes(
        session,
        league_season,
        spots,
        today,
        week.roster,
        ir_slot_free=week.ir_slot_free,
        tilt=tilt,
        as_of=as_of,
    )

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
            _schedule(days, len(lineup), base, mine, None, {}),
            outlook,
            hurdle,
            len(wire),
            historical_wire,
            stashed,
        )

    opponent = load_team_week(session, league_season, week.opponent_team_id, today)
    theirs = {player.player_id: contender(player) for player in opponent.active}
    their_projection = _Week(days, lineup, opponent.my_totals).project(theirs)
    their_line = their_projection.line

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
            reported = priced(session, league_season, week, reported, wire, hurdle)
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
        _schedule(days, len(lineup), first.base, mine, their_projection, theirs),
        outlook,
        hurdle,
        len(wire),
        historical_wire,
        stashed,
    )


def spot_book(
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

    Public because a caller judging one move a manager named by hand
    (`app.inseason.what_if`) has to charge it against the same book the
    search would have: the replacement is the best free agent in `wire`, so
    a different wire is a different season term for the same swap.
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

    The number half of `week_changes`, which is where the derivation and the
    arguments are documented. Kept as its own function because it is what the
    rest-of-season report and the trade evaluator ask for, and what they have
    always asked for.
    """
    return [
        change.delta
        for change in week_changes(
            session,
            league_season,
            team_id,
            today,
            moves,
            tilt=tilt,
            distributions=distributions,
            waivers=waivers,
            effective_day=effective_day,
            opponent_move=opponent_move,
        )
    ]


def week_changes(
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
) -> list[WeekChange]:
    """This week before and after, for each (added, dropped): the nine chances.

    The rest-of-season report needs the week's half of a judgement for moves
    it found by another route, and it must be the same number the streaming
    report would give: the same seating, the same head-to-head, the same
    knowable lines. So it is computed here rather than approximated there.
    A bye has no head to head at all, so both mappings come back empty and
    every delta is zero.

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
    days = week.scoring_periods_remaining
    if week.opponent_team_id is None:
        return [
            WeekChange(
                opponent_team_id=None,
                days_remaining=len(days),
                before={},
                after={},
                starts_before={},
                starts={},
                fills_empty_day=False,
            )
            for _ in moves
        ]
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
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
    base = engine.project(mine)
    before = head_to_head(base.line, their_line, distributions, len(days))

    out: list[WeekChange] = []
    for added, dropped in moves:
        projection = engine.project(moved(mine, added, dropped))
        out.append(
            WeekChange(
                opponent_team_id=week.opponent_team_id,
                days_remaining=len(days),
                before=dict(before),
                after=head_to_head(projection.line, their_line, distributions, len(days)),
                starts_before=dict(base.starts),
                starts=dict(projection.starts),
                fills_empty_day=projection.empty_slot_days < base.empty_slot_days,
            )
        )
    return out


def priced(
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

    `move.net` is the net over both horizons, which is what the choice
    between the median and the 75th percentile has always read here. What
    the man is worth in dollars is priced off the judgement's own `per_week`
    and the weeks it covers, passed separately, because those two are the
    budget's units and the net is not -- and the bar goes with them, as
    `hurdle / weeks_covered`, which is `Move.clears`'s own test written in
    those units rather than a second, stricter bar.
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
            per_week=move.judgement.per_week,
            weeks_covered=move.judgement.weeks_covered,
            bar=hurdle / move.judgement.weeks_covered,
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


def _side_games(
    days: Sequence[int],
    places: int,
    projection: _Projection,
    contenders: Mapping[int, Contender],
) -> tuple[dict[int, SideGames], SideGames]:
    """One side's days, and the total over them, from its own projection."""
    out: dict[int, SideGames] = {}
    for day in days:
        seats = projection.by_day[day]
        taken = set(seats.seated)
        out[day] = SideGames(
            games=len(seats.available),
            seated=len(seats.seated),
            open_places=places - len(seats.seated),
            men=tuple(
                DayMan(player=contenders[player_id].player, seated=player_id in taken)
                for player_id in seats.available
            ),
        )
    return out, SideGames(
        games=sum(side.games for side in out.values()),
        seated=sum(side.seated for side in out.values()),
        open_places=sum(side.open_places for side in out.values()),
    )


def _schedule(
    days: Sequence[int],
    places: int,
    mine: _Projection,
    my_contenders: Mapping[int, Contender],
    theirs: _Projection | None,
    their_contenders: Mapping[int, Contender],
) -> Schedule:
    """The per-day games table for both sides, off the two projections.

    Both sides are seated by the same rule and counted the same way; on a
    bye there is no other side and the table is one row deep.
    """
    my_days, my_total = _side_games(days, places, mine, my_contenders)
    their_days: dict[int, SideGames] = {}
    their_total: SideGames | None = None
    if theirs is not None:
        their_days, their_total = _side_games(days, places, theirs, their_contenders)
    return Schedule(
        days=tuple(
            DayGames(scoring_period=day, mine=my_days[day], theirs=their_days.get(day))
            for day in days
        ),
        mine_total=my_total,
        theirs_total=their_total,
    )


def _report(
    week: TeamWeek,
    base: _Projection,
    their_line: CategoryLine,
    before: Mapping[str, float],
    moves: tuple[Move, ...],
    recommended: tuple[Move, ...],
    empty_days: tuple[EmptyDay, ...],
    schedule: Schedule,
    outlook: Judgement,
    hurdle: float,
    pool_size: int,
    historical_wire: bool,
    stashed: tuple[Stash, ...] = (),
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
        posted=week.my_totals,
        opponent_posted=week.opp_totals,
        posted_source=week.posted_source,
        posted_men=week.my_posted_men,
        opponent_posted_men=week.opp_posted_men,
        moves=moves,
        recommended=recommended,
        empty_days=empty_days,
        schedule=schedule,
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
        stashed=stashed,
    )
