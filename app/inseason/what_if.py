"""What one named move would do: this week, the rest of the season, the finish.

The owner's question, 2026-09-23: *"if someone is like what happens if I drop
this person for this person -- for my week and for my season outlook -- and
the same for trades: I want to know if it enhances my projected finish or
not."*

Half of it was already built and is not touched here. The pickup recommender
(`app.pickups.stream`) and the trade evaluator (`app.trades.evaluate`) both
carry this week's category chances before and after, the change per week over
the rest of the season, the net over both horizons, and a projected category
record with the move and without it. Two things were missing, and this module
is those two things.

**A manager could not name his own pickup.** The week page shows what the
search ranked and nothing else; a manager who wants to ask about a particular
man had no route to ask it through. He does now, and the answer is the
recommender's own numbers for that move -- `week_changes` for the week and
`judge` for the currency, off the same wire the search would have read -- and
not a second opinion. Nothing here re-derives `delta_week`,
`delta_season_per_week` or the net. The hurdle, the backtest and the trade
record are all priced on those numbers, so a second derivation would be a
second thing to be wrong.

**Nothing hypothetical reached the projected finish.** The record a judgement
carries is played against a *league-average* opponent (`app.pickups.judge`):
it is comparable across teams and it is not where a manager finishes. Where he
finishes is `app.inseason.projected`, which plays every remaining week against
the real opponent on the real schedule and runs ten thousand seasons for the
place and the playoff odds -- but only ever for the rosters as they stand.
`docs/projected_record.md` section 3 names that seam and leaves it alone. This
module takes it, **for the hypothetical view only**: `project_standings` grew
a `rosters` argument, and the finish layer is that engine run twice, once as
things are and once with this team's roster changed, same seed, same
simulation count, every other roster untouched.

THE THREE LAYERS, AND WHY THEY ARE THREE

1. **This week.** `app.pickups.stream.week_changes` on the changed roster: the
   nine chances against the real opponent's projected line, before and after,
   the expected categories either way, and the days left. The same function
   the rest-of-season report and the trade page call, so the week page, the
   trade page and this cannot disagree about what a swap does to a Thursday.
2. **The rest of the season.** Each remaining week's opponent and expected
   categories, before and after, from the projected engine.
3. **The finish.** The projected final record, the place, the playoff odds and
   the seed odds, before and after, from the Monte Carlo behind the projected
   standings.

And beside all three, unchanged: the `Judgement` the recommender makes of the
same move, with its hurdle label and its FAAB bid. **The finish is a second
lens, not a second bar.** Nothing is labelled against it.

THE NOISE, SAID OUT LOUD

The finish deltas one man moves are small -- tenths of a category and a point
or two of playoff odds -- and the odds are counted off a simulation, so they
carry sampling error of their own. `Finish.odds_band` is the ninety-five
percent band on one odds figure at `n_sims` from the binomial alone, which at
ten thousand seasons is about a point. That is the honest size of a single
number.

The *difference* between two of them is steadier than that, and for a reason
worth stating: before and after are run from the same seed, and the simulation
draws one uniform per matchup in a fixed order, so every matchup the change
does not touch is drawn identically in both worlds. It is a paired comparison,
not two independent ones, and the common draws cancel. What does not cancel is
the part of the draw the change actually moves, which is the part being
measured. The band is reported rather than a second seed's spread because it
is free: a second pair of runs would double a call that already costs two
league projections (section "Timing" of `docs/what_if.md`).

NO LOOK-AHEAD

Everything is `today`'s or earlier, because every piece of it already was: the
roster from the latest lineup day at or before `today`, the wire from the
listener's latest pass or from who played and was in nobody's lineup, the
per-game rates from box scores before `today`, and the pairings from the
stored schedule. The one inherited leak is the one
`docs/projected_record.md` section 4 names -- the league's weekly spreads are
measured over every played season of this size, which on a replayed day
includes weeks after it -- and it is the same leak the week page and the bid
prices have.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Player
from app.draft.lineup import within_position_limits
from app.draft.pool import position_limits_for, roster_size_for
from app.draft.targets import CategoryDistribution, category_distributions
from app.inseason.projected import N_SIMS, SEED, Projection, TeamOutlook, project_standings
from app.inseason.projected_calibration import SHORT_NOTE
from app.listener.events import OUT
from app.pickups.bids import Bid
from app.pickups.judge import OPENED_PLACE, TYPICAL_PICKUP, Judgement, judge
from app.pickups.state import (
    RosteredPlayer,
    TeamWeek,
    has_free_agent_snapshots,
    load_team_week,
    season_calendar,
    team_row,
    waiver_state,
)
from app.pickups.stream import (
    ADD,
    IR_MOVE,
    MOVED_THRESHOLD,
    STREAM_HURDLE,
    SWAP,
    CategoryShift,
    Contender,
    Move,
    WeekChange,
    evaluated_wire,
    priced,
    spot_book,
    week_changes,
)
from app.scoring.lines import CategoryLine
from app.trades.evaluate import TradeReport

__all__ = [
    "BREAKS_LIMITS",
    "EMPTIES_A_PLACE",
    "FINISH_IS_A_SECOND_LENS",
    "NOISE_NOTE",
    "NOTHING_NAMED",
    "NOT_A_FREE_AGENT",
    "NOT_ON_ROSTER",
    "NOT_OUT",
    "NO_IR_SLOT",
    "OVER_SIZE",
    "Change",
    "Finish",
    "WeekAhead",
    "WeekLayer",
    "WhatIf",
    "trade_finishes",
    "what_if",
]

# ---------------------------------------------------------------------------
# the sentences a refusal gives (docs/trades.md section 10's house style)
# ---------------------------------------------------------------------------

NOTHING_NAMED = "Name at least one man to add, drop or move to injured reserve."
NOT_ON_ROSTER = "{team} does not have {names} on its roster on day {day}."
NOT_A_FREE_AGENT = (
    "{names} is not a free agent on day {day}: a what-if can only add a man off the wire. "
    "To ask about a man another team holds, judge it as a trade."
)
OVER_SIZE = (
    "{team} would hold {after} players after this change and the roster holds {size}. "
    "Drop one more, or add one fewer."
)
EMPTIES_A_PLACE = (
    "This change leaves {team} a roster place short: {drops} leave and {adds} arrive. Name the "
    "man going into the place, or ask the season report what dropping him alone costs."
)
BREAKS_LIMITS = (
    "{team} would be outside this league's position limits after this change ({limits}). "
    "Swap a man at a different position."
)
NO_IR_SLOT = "{team} has no free injured-reserve place on day {day}, so nobody can be moved to it."
NOT_OUT = (
    "{name} is not ruled out on day {day}, and ESPN's injured reserve only holds a man who is. "
    "Drop him instead, or leave him where he is."
)

#: Said on every finish layer. The bar labels a move and never hides one, and
#: this is not a bar: nothing is recommended, refused or ranked on the finish.
FINISH_IS_A_SECOND_LENS = (
    "the finish is a second lens, not a second bar: nothing here is labelled against it. The "
    "hurdle, the backtest and the bid are priced on the judgement's own numbers, which this "
    "does not touch. Quote the noise beside any finish figure you quote."
)

#: How the sampling error on an odds figure is to be read. The band itself is
#: computed per answer, because it depends on the odds and on `n_sims`.
NOISE_NOTE = (
    "the odds come from {n_sims:,} simulated seasons, so each one carries a sampling band of "
    "about {band} of a point at ninety-five percent from the simulation alone. Before and after "
    "are run from the same seed and draw the same numbers for every matchup this change does "
    "not touch, so the difference between them is steadier than the two bands suggest -- but a "
    "difference smaller than the band is inside the noise either way."
)


@dataclass(frozen=True)
class Change:
    """The roster change being asked about, for one team.

    Player ids are this database's, as every engine in `app/` uses them; the
    routes translate ESPN's at the edge. `to_ir` is a man who keeps his roster
    place and stops being started, which is why he is not a drop.
    """

    team_id: int
    adds: tuple[int, ...] = ()
    drops: tuple[int, ...] = ()
    to_ir: tuple[int, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.adds or self.drops or self.to_ir)

    def after(self, active: Sequence[int]) -> tuple[int, ...]:
        """The active roster this change leaves, in the order it was held.

        The men who can be started, which is the only list a projection or a
        week's seating reads: everyone held but the drops and the men moved to
        injured reserve, and then the men arriving.
        """
        gone = set(self.drops) | set(self.to_ir)
        kept = [player_id for player_id in active if player_id not in gone]
        return (*kept, *(player_id for player_id in self.adds if player_id not in set(kept)))


@dataclass(frozen=True)
class WeekLayer:
    """The matchup in front of us, before the change and after it."""

    matchup_period: int
    opponent_team_id: int | None
    opponent_name: str | None
    days_remaining: int
    #: The score as it stands, both sides, in raw counts: the week report's
    #: own `posted`, under the same live/replay rule. One and not a pair,
    #: because a change made today cannot move a day already played.
    posted: CategoryLine
    opponent_posted: CategoryLine
    #: P(this team wins the category), as things stand and with the change made.
    before: Mapping[str, float]
    after: Mapping[str, float]
    #: Starts each man arriving gets, and each man leaving was getting.
    add_starts: Mapping[int, int]
    drop_starts: Mapping[int, int]
    fills_empty_day: bool

    @property
    def expected_before(self) -> float:
        return sum(self.before.values())

    @property
    def expected_after(self) -> float:
        return sum(self.after.values())

    @property
    def delta(self) -> float:
        return self.expected_after - self.expected_before

    @property
    def on_bye(self) -> bool:
        return self.opponent_team_id is None

    def shifts(self) -> tuple[CategoryShift, ...]:
        """Every category's chance before and after, in the league's own order."""
        return tuple(CategoryShift(key, self.before[key], self.after[key]) for key in self.before)

    def moved(self, threshold: float = MOVED_THRESHOLD) -> tuple[CategoryShift, ...]:
        """The categories the change moved, largest change first.

        The week report's own threshold, so the same swap lists the same
        categories on both pages.
        """
        return tuple(
            sorted(
                (shift for shift in self.shifts() if abs(shift.delta) >= threshold),
                key=lambda shift: -abs(shift.delta),
            )
        )


@dataclass(frozen=True)
class WeekAhead:
    """One remaining matchup, with and without the change."""

    period: int
    days_remaining: int
    in_play: bool
    opponent_team_id: int | None
    opponent_name: str | None
    expected_before: float
    expected_after: float

    @property
    def delta(self) -> float:
        return self.expected_after - self.expected_before

    @property
    def on_bye(self) -> bool:
        return self.opponent_team_id is None


@dataclass(frozen=True)
class Finish:
    """Where one team ends, before the change and after it.

    Everything here is the projected-standings engine's own
    (`app.inseason.projected`), read twice: once for the league as it stands
    and once for the league with this change made. The place is the team's
    row in that engine's own ordering, which is the order the Standings page
    prints, so "3rd" means the same thing on both pages.
    """

    team_id: int
    team_name: str
    #: Categories won and lost as the season would end.
    record_before: tuple[float, float]
    record_after: tuple[float, float]
    #: Matchups won, lost and tied, the mean over the simulated seasons.
    matchups_before: tuple[float, float, float]
    matchups_after: tuple[float, float, float]
    #: The projected table's own order, 1 for first.
    place_before: int
    place_after: int
    playoff_odds_before: float
    playoff_odds_after: float
    #: P(a first-round bye), or None where the format gives none.
    bye_odds_before: float | None
    bye_odds_after: float | None
    #: P(each finishing place), first place first; sums to one.
    seed_odds_before: tuple[float, ...]
    seed_odds_after: tuple[float, ...]
    weeks: tuple[WeekAhead, ...]
    n_sims: int
    seed: int
    #: The published record of this forecast, for printing under the numbers.
    calibration_note: str = SHORT_NOTE

    @property
    def record_delta(self) -> float:
        """Categories the change is worth on the final record."""
        return self.record_after[0] - self.record_before[0]

    @property
    def playoff_delta(self) -> float:
        return self.playoff_odds_after - self.playoff_odds_before

    @property
    def place_delta(self) -> int:
        """Places gained: positive is a better finish."""
        return self.place_before - self.place_after

    @property
    def odds_band(self) -> float:
        """The 95% sampling band on one playoff-odds figure, at `n_sims`.

        The binomial one, taken at whichever of the two odds sits nearer a
        coin, which is the wider of the two: 1.96 * sqrt(p(1-p)/n). At ten
        thousand seasons and a coin that is about one point.
        """
        nearest = max(
            (self.playoff_odds_before, self.playoff_odds_after),
            key=lambda odds: odds * (1.0 - odds),
        )
        return 1.96 * math.sqrt(max(0.0, nearest * (1.0 - nearest)) / max(1, self.n_sims))

    @property
    def readable(self) -> bool:
        """Whether the odds moved by more than the simulation's own noise."""
        return abs(self.playoff_delta) > self.odds_band

    @property
    def noise_note(self) -> str:
        return NOISE_NOTE.format(n_sims=self.n_sims, band=f"{self.odds_band * 100:.1f}")


@dataclass(frozen=True)
class WhatIf:
    """One named pickup, in three layers, with the recommender's own number."""

    season: int
    today: int
    today_date: date | None
    team_id: int
    team_name: str
    adds: tuple[RosteredPlayer, ...]
    drops: tuple[RosteredPlayer, ...]
    to_ir: tuple[RosteredPlayer, ...]
    #: `SWAP`, `ADD` into an open place, or `IR_MOVE`; "" for anything else.
    kind: str
    week: WeekLayer
    finish: Finish
    #: The recommender's own judgement of this move, untouched.
    judgement: Judgement
    hurdle: float
    clears_hurdle: bool
    #: What to pay, on a move that clears the hurdle and on no other.
    bid: Bid | None
    #: Free agents the replacement charge was taken over, and whether that
    #: wire was the listener's or rebuilt from who played.
    pool_size: int
    historical_wire: bool
    notes: tuple[str, ...] = ()

    @property
    def net(self) -> float:
        """Categories the move is worth over both horizons."""
        return self.judgement.delta_total


# ---------------------------------------------------------------------------
# a named pickup
# ---------------------------------------------------------------------------


def what_if(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    *,
    add: Sequence[int] = (),
    drop: Sequence[int] = (),
    to_ir: Sequence[int] = (),
    hurdle: float = STREAM_HURDLE,
    floor: float = TYPICAL_PICKUP,
    opened: float = OPENED_PLACE,
    tilt: bool = True,
    bids: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
    pool: Sequence[int] | None = None,
    n_sims: int = N_SIMS,
    seed: int = SEED,
) -> WhatIf:
    """Judge the move a manager named, and say where it leaves him.

    `add`, `drop` and `to_ir` are this database's player ids. The judgement is
    the recommender's, built out of the recommender's own functions on the
    recommender's own wire -- so the numbers a manager reads here for a move
    the search also found are the numbers the week page prints for it, to the
    last decimal. What is new is the third layer: the finish.

    Raises `ValueError` with one sentence for every way a change can be
    wrong: a man dropped who is not held, a man added who is not on the wire,
    a roster left over or under size, a roster outside the position limits, an
    injured-reserve move with no place or no injury, and a season with no
    matchup period holding `today`.
    """
    change = Change(
        team_id=team_id,
        adds=tuple(dict.fromkeys(int(player_id) for player_id in add)),
        drops=tuple(dict.fromkeys(int(player_id) for player_id in drop)),
        to_ir=tuple(dict.fromkeys(int(player_id) for player_id in to_ir)),
    )
    if change.empty:
        raise ValueError(NOTHING_NAMED)

    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)

    week = load_team_week(session, league_season, team_id, today)
    team = team_row(session, league_season, team_id)
    wire = evaluated_wire(
        session,
        league_season,
        week,
        today,
        pool=pool,
        tilt=tilt,
        distributions=distributions,
        as_of=as_of,
    )
    _check(session, league_season, str(team.name), week, change, wire, today)

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
    held = {player.player_id: player for player in week.roster}
    arriving = {found.player_id: found.player for found in wire}

    # The week, from the one function every page reads a week's change off.
    changed = week_changes(
        session,
        league_season,
        team_id,
        today,
        [(change.adds, (*change.drops, *change.to_ir))],
        tilt=tilt,
        distributions=distributions,
        waivers=waiver_state([found.player for found in wire]),
    )[0]
    layer = _week_layer(session, league_season, week, change, changed)

    # The currency, from `judge` and nothing else. A man moved to injured
    # reserve keeps his roster place, so only a drop is charged over the
    # season -- which is the rule `stream_recommendations` applies.
    judgement = judge(
        spots,
        delta_week=changed.delta,
        dropped=change.drops,
        added=change.adds,
    )
    move = _move(change, held, arriving, changed, judgement)
    if bids and move is not None:
        move = priced(session, league_season, week, (move,), wire, hurdle)[0]

    finish = _finishes(
        session,
        league_season,
        today,
        {team_id: change.after([player.player_id for player in week.active])},
        (team_id,),
        distributions=distributions,
        n_sims=n_sims,
        seed=seed,
    )[team_id]

    net = judgement.delta_total
    clears = net >= hurdle or (changed.fills_empty_day and net > 0)
    return WhatIf(
        season=season,
        today=today,
        today_date=as_of,
        team_id=team_id,
        team_name=str(team.name),
        adds=tuple(arriving[player_id] for player_id in change.adds),
        drops=tuple(held[player_id] for player_id in change.drops),
        to_ir=tuple(held[player_id] for player_id in change.to_ir),
        kind="" if move is None else move.kind,
        week=layer,
        finish=finish,
        judgement=judgement,
        hurdle=hurdle,
        clears_hurdle=clears,
        bid=None if move is None else move.bid,
        pool_size=len(wire),
        historical_wire=pool is None and not has_free_agent_snapshots(session, league_season),
        notes=_notes(week, change, finish),
    )


def _week_layer(
    session: Session,
    league_season: LeagueSeason,
    week: TeamWeek,
    change: Change,
    changed: WeekChange,
) -> WeekLayer:
    """The week's own layer, with the opponent named."""
    opponent = (
        None
        if week.opponent_team_id is None
        else str(team_row(session, league_season, week.opponent_team_id).name)
    )
    return WeekLayer(
        matchup_period=week.matchup_period,
        opponent_team_id=week.opponent_team_id,
        opponent_name=opponent,
        days_remaining=changed.days_remaining,
        posted=week.my_totals,
        opponent_posted=week.opp_totals,
        before=dict(changed.before),
        after=dict(changed.after),
        add_starts={player_id: changed.starts.get(player_id, 0) for player_id in change.adds},
        drop_starts={
            player_id: changed.starts_before.get(player_id, 0)
            for player_id in (*change.drops, *change.to_ir)
        },
        fills_empty_day=changed.fills_empty_day,
    )


def _move(
    change: Change,
    held: Mapping[int, RosteredPlayer],
    arriving: Mapping[int, RosteredPlayer],
    changed: WeekChange,
    judgement: Judgement,
) -> Move | None:
    """The change as the recommender's own `Move`, when it is one of its kinds.

    Only so the FAAB bid is priced by the function that prices every other
    pickup (`app.pickups.stream.priced`). A change of a shape the search never
    makes -- two men at once, a drop with no add -- has no `Move` and no bid,
    and the judgement beside it is the same either way.
    """
    if len(change.adds) != 1:
        return None
    add = arriving[change.adds[0]]
    if len(change.drops) == 1 and not change.to_ir:
        kind, drop, hurt = SWAP, held[change.drops[0]], None
    elif not change.drops and len(change.to_ir) == 1:
        kind, drop, hurt = IR_MOVE, None, held[change.to_ir[0]]
    elif not change.drops and not change.to_ir:
        kind, drop, hurt = ADD, None, None
    else:
        return None
    return Move(
        kind=kind,
        add=add,
        drop=drop,
        to_ir=hurt,
        delta=changed.delta,
        add_starts=changed.starts.get(add.player_id, 0),
        drop_starts=0 if drop is None else changed.starts_before.get(drop.player_id, 0),
        shifts=changed.shifts,
        fills_empty_day=changed.fills_empty_day,
        judgement=judgement,
    )


def _notes(week: TeamWeek, change: Change, finish: Finish) -> tuple[str, ...]:
    """Honest caveats, in the words a page prints."""
    out: list[str] = []
    if week.on_bye:
        out.append("this team is on a bye, so the week half of the judgement is zero")
    if week.adds_left <= 0 and change.adds:
        out.append(
            f"this matchup period allows {week.adds_budget} adds and {week.adds_used} have been "
            "made, so there is no add left to make this one with"
        )
    if not finish.readable:
        out.append(
            "the playoff odds moved by less than the simulation's own sampling band, so read "
            "the finish here as unchanged"
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# checking the change
# ---------------------------------------------------------------------------


def _check(
    session: Session,
    league_season: LeagueSeason,
    team_name: str,
    week: TeamWeek,
    change: Change,
    wire: Sequence[Contender],
    today: int,
) -> None:
    """Every way a change can be wrong, as one sentence each.

    Checked here rather than left to come out as a strange number, which is
    the rule docs/trades.md section 10 sets for the trade builder: bad input
    gets a sentence a manager can act on.
    """
    active = {player.player_id: player for player in week.active}
    held = {player.player_id: player for player in week.roster}
    on_the_wire = {found.player_id: found.player for found in wire}

    missing = [player_id for player_id in (*change.drops, *change.to_ir) if player_id not in active]
    if missing:
        raise ValueError(
            NOT_ON_ROSTER.format(team=team_name, names=_names(session, missing), day=today)
        )
    # A man this team already holds is not on the wire it reads
    # (`evaluated_wire` takes the roster out), so "add somebody I have" lands
    # here, which is where it belongs.
    off_the_wire = [player_id for player_id in change.adds if player_id not in on_the_wire]
    if off_the_wire:
        raise ValueError(NOT_A_FREE_AGENT.format(names=_names(session, off_the_wire), day=today))

    if change.to_ir:
        if not week.ir_slot_free:
            raise ValueError(NO_IR_SLOT.format(team=team_name, day=today))
        for player_id in change.to_ir:
            man = active[player_id]
            if (man.injury_status or "").upper() != OUT:
                raise ValueError(NOT_OUT.format(name=man.name, day=today))

    after = change.after(list(active))
    size = roster_size_for(league_season)
    if len(after) > size:
        raise ValueError(OVER_SIZE.format(team=team_name, after=len(after), size=size))
    if len(after) < len(active):
        raise ValueError(
            EMPTIES_A_PLACE.format(
                team=team_name,
                drops=len(change.drops) + len(change.to_ir),
                adds=len(change.adds),
            )
        )

    limits = position_limits_for(league_season)
    if limits:
        # The whole roster's primary positions, injured reserve included,
        # which is what `stream_recommendations` checks a swap against.
        gone = set(change.drops)
        positions = [player.position for player_id, player in held.items() if player_id not in gone]
        positions.extend(on_the_wire[player_id].position for player_id in change.adds)
        if not within_position_limits(positions, limits):
            said = ", ".join(
                f"at most {limit} at {position}" for position, limit in sorted(limits.items())
            )
            raise ValueError(BREAKS_LIMITS.format(team=team_name, limits=said))


def _names(session: Session, player_ids: Sequence[int]) -> str:
    """The men named, for a sentence: "A", "A and B", "A, B and C".

    A refusal a manager can act on has a name in it, not a number, so the one
    extra query a refusal costs is worth making.
    """
    found = {
        int(player_id): str(name)
        for player_id, name in session.execute(
            select(Player.id, Player.name).where(Player.id.in_(sorted(set(player_ids))))
        ).all()
    }
    names = [found.get(player_id, f"player {player_id}") for player_id in player_ids]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# the finish
# ---------------------------------------------------------------------------


def trade_finishes(
    session: Session,
    league_season: LeagueSeason,
    report: TradeReport,
    *,
    distributions: Sequence[CategoryDistribution] | None = None,
    n_sims: int = N_SIMS,
    seed: int = SEED,
) -> dict[int, Finish]:
    """Where a judged deal leaves both sides, keyed by ESPN team id.

    The rosters are the evaluator's own and are not rebuilt here: each side's
    men leaving are its `gives` and its `drops`, the men arriving its
    `receives` and its `fills`, which is exactly `_Side.leaving` and
    `_Side.arriving` after the evaluator has settled who gets dropped. So the
    roster the finish is projected on is the roster the deal was scored on,
    man for man.

    Both sides change in one projection, because the deal happens to both of
    them at once: the finish for one side is read off a table where the other
    side has its new roster too.

    A place a deal leaves open is filled by the one free agent the report
    already names for it (`SideReport.replacement_player`), which is the man
    the evaluator's own nine-category table shows in the place. A deal that
    leaves more than one place open puts him in the first and leaves the rest
    empty: a projection seats men on days, and a streamed lane is not a man.
    `SideReport.opened_value` is what those places are worth, and it is on the
    payload beside this.
    """
    today = report.today
    rosters: dict[int, Collection[int]] = {}
    for side in report.sides:
        week = load_team_week(session, league_season, side.team_id, today)
        leaving = {card.player_id for card in (*side.gives, *side.drops)}
        arriving = [card.player_id for card in (*side.receives, *side.fills)]
        if side.places_left_open and side.replacement_player is not None:
            arriving.append(side.replacement_player.player_id)
        kept = [player.player_id for player in week.active if player.player_id not in leaving]
        rosters[side.team_id] = (
            *kept,
            *(player_id for player_id in arriving if player_id not in set(kept)),
        )
    return _finishes(
        session,
        league_season,
        today,
        rosters,
        tuple(side.team_id for side in report.sides),
        distributions=distributions,
        n_sims=n_sims,
        seed=seed,
    )


def _finishes(
    session: Session,
    league_season: LeagueSeason,
    today: int,
    rosters: Mapping[int, Collection[int]],
    wanted: Sequence[int],
    *,
    distributions: Sequence[CategoryDistribution] | None,
    n_sims: int,
    seed: int,
) -> dict[int, Finish]:
    """The projected engine, twice: as things stand, and with `rosters` in.

    One `category_distributions` between the two runs, which is two thirds of
    what a projection costs (docs/projected_record.md section 6), and the same
    seed and simulation count on both sides so the comparison is paired.
    """
    if distributions is None:
        distributions = category_distributions(session, league_season)
    before = project_standings(
        session, league_season, today, distributions=distributions, n_sims=n_sims, seed=seed
    )
    after = project_standings(
        session,
        league_season,
        today,
        distributions=distributions,
        n_sims=n_sims,
        seed=seed,
        rosters=rosters,
    )
    return {team_id: _finish(before, after, team_id) for team_id in wanted}


def _finish(before: Projection, after: Projection, team_id: int) -> Finish:
    was, now = before.team(team_id), after.team(team_id)
    if was is None or now is None:
        raise ValueError(f"team {team_id} is not in the {before.season} projection")
    return Finish(
        team_id=team_id,
        team_name=now.name,
        record_before=was.projected_record,
        record_after=now.projected_record,
        matchups_before=was.projected_matchups,
        matchups_after=now.projected_matchups,
        place_before=_place(before, team_id),
        place_after=_place(after, team_id),
        playoff_odds_before=was.playoff_odds,
        playoff_odds_after=now.playoff_odds,
        bye_odds_before=was.bye_odds,
        bye_odds_after=now.bye_odds,
        seed_odds_before=was.finishes,
        seed_odds_after=now.finishes,
        weeks=_weeks(was, now),
        n_sims=after.n_sims,
        seed=after.seed,
    )


def _place(projection: Projection, team_id: int) -> int:
    """The team's row in the projected table, 1 for first."""
    for place, team in enumerate(projection.teams, start=1):
        if team.team_id == team_id:
            return place
    raise ValueError(f"team {team_id} is not in the {projection.season} projection")


def _weeks(was: TeamOutlook, now: TeamOutlook) -> tuple[WeekAhead, ...]:
    """Each remaining week, before and after.

    The pairings are the league's stored schedule and a roster change cannot
    move them, so the two lists are the same weeks in the same order and are
    read together.
    """
    return tuple(
        WeekAhead(
            period=old.period,
            days_remaining=old.days_remaining,
            in_play=old.in_play,
            opponent_team_id=old.opponent_team_id,
            opponent_name=old.opponent_name,
            expected_before=old.expected_wins,
            expected_after=new.expected_wins,
        )
        for old, new in zip(was.weeks, now.weeks, strict=True)
    )
