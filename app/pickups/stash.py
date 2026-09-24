"""What a man who is not playing costs the place he is holding.

The engine already prices what a stashed man is *worth*: since 2026-09-24
`app.pickups.returns` gives him the games he is expected to play rather than
none, and every rest-of-season number in the product -- the week report's
season half, the season report, a trade, the projected standings, the what-if
-- reads that count through `app.pickups.judge`. This module adds the one term
the judgement cannot see.

THE DEAD PLACE

While he is out, the roster place he holds cannot be streamed. `judge` does
not know that: it prices the place at the better of the man in it and what
the wire would give it back, and for a man who is not playing yet the wire
arm wins, which quietly assumes the place is being used. It is not.

So a stash carries one extra charge, and exactly one:

    dead cost = OPENED_PLACE x E[dead days] / 7

`E[dead days]` is the chance he is still out, summed over every day of the
horizon, today included (`app.pickups.returns.expected_dead_days`): today is
dead outright, tomorrow is dead with probability one less the chance he is
back by then, and a man who never returns is dead for every day left. A dead
day converts to the weekly number by **one seventh**, `DAYS_A_WEEK`, the same
divisor every other weekly quantity in this engine uses -- which reproduces
`docs/stashes.md` section 6b's own arithmetic for the Brandon Miller claim
exactly: ten dead days, 1.43 dead weeks, 0.54 categories.

`OPENED_PLACE` is this league's measured value of a place that is streamed
(`app.calibration`, 0.38 by default, `docs/streaming_lane.md`). It is not
moved here.

THE INJURED-RESERVE GATE

**Zero when the league has a free injured-reserve slot**, because then the
place is not dead: he sits on IR and the roster place goes on being streamed.
That is read from the stored setting -- `league_seasons.injured_reserve_slots`
against the roster's own IR occupancy, which is
`app.pickups.state.TeamWeek.ir_slot_free` -- and never from a sentence about
what the league is supposed to carry. `docs/pickups.md` section 4.4 and
`app/pickups/season.py` both used to say the league gains a slot in 2027; the
stored 2027 row says 0 (`docs/stashes.md` section 5). The setting wins.

THE TWO ARMS, AND WHY BOTH ARE PRINTED

`expected_net` is the mean: the recommender's own net for the move, less the
dead cost. `net_if_out_past_week` is the same number in the branch where he
is still out in four weeks' time -- the prior re-read from the row he would
then be on, which is the honest way to condition a curve that is already
conditional. `docs/stashes.md`'s finding is that the distribution *is* the
answer, so a page prints both and the odds beside them.

NOTHING HERE IS A BAR

The hurdle is untouched and nothing is labelled against this. A stash under
the bar still appears with its number and its odds, which is the owner's rule
(`docs/product.md`, memory `tool-not-gospel`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.pickups.returns import (
    PRIOR_SOURCE,
    RAMP_SOURCE,
    expected_dead_days,
    expected_dead_days_if_out_past,
    odds_back_by_week,
)

__all__ = ["LATE_WEEK", "ODDS_WEEKS", "STASH_LANGUAGE", "Stash", "stash_block"]

#: Days in a matchup period, the divisor that turns a dead day into a dead
#: week. `app.pickups.judge.DAYS_A_WEEK`, repeated here rather than imported
#: because `judge` imports `state` which imports `returns`, and this module
#: sits on top of all three.
DAYS_A_WEEK = 7.0

#: The weeks a page prints the return odds for.
ODDS_WEEKS: tuple[int, ...] = (1, 2, 4, 8)

#: The branch the second arm is about: still out in four weeks.
LATE_WEEK = 4

#: What the odds are and are not, in the words a page and an assistant repeat.
STASH_LANGUAGE = (
    "the odds are the NBA's own return record for men who have been out this long -- "
    f"{PRIOR_SOURCE} -- and not a diagnosis, a timeline or anything anybody has said about "
    "this man. ESPN's basketball API carries no return date at all, so there is no date to "
    "print and the distribution is the answer. The ramp on the other side is "
    f"{RAMP_SOURCE}."
)


@dataclass(frozen=True)
class Stash:
    """What holding a man who is not playing is expected to cost and return."""

    player_id: int
    name: str
    #: Calendar days since his last played game.
    days_out: int
    #: P(back by the end of week k), for `ODDS_WEEKS`.
    return_odds_by_week: Mapping[int, float]
    #: Days the place is expected to stand empty, and the same in weeks.
    expected_dead_days: float
    expected_dead_weeks: float
    #: `OPENED_PLACE` x the dead weeks, or zero with a free IR slot.
    dead_cost: float
    #: The recommender's own net for the move, less the dead cost.
    expected_net: float
    #: The same in the branch where he is still out in `late_week` weeks.
    net_if_out_past_week: float
    #: Dead weeks expected in that branch, for a page that wants to show it.
    late_dead_weeks: float
    late_week: int
    ir_slot_free: bool
    #: Games he is expected to play over the horizon, against the games his
    #: NBA team has left. The first is what the projection counted.
    expected_games: float
    healthy_games: int
    language: str = STASH_LANGUAGE

    @property
    def back_within_a_fortnight(self) -> float:
        return self.return_odds_by_week.get(2, 0.0)

    @property
    def line(self) -> str:
        """The one line a page prints, in the house style: no verdict words."""
        return (
            f"Out {self.days_out} days · back within a fortnight "
            f"{self.back_within_a_fortnight * 100:.0f}% · "
            f"dead weeks cost {self.dead_cost:.2f} · "
            f"expected {self.expected_net:+.2f} "
            f"(or {self.net_if_out_past_week:+.2f} if he is not back by week {self.late_week})"
        )


def stash_block(
    *,
    player_id: int,
    name: str,
    days_out: int,
    horizon_days: int,
    opened: float,
    ir_slot_free: bool,
    net: float,
    late_net: float,
    expected_games: float,
    healthy_games: int,
    late_week: int = LATE_WEEK,
    odds_weeks: Sequence[int] = ODDS_WEEKS,
) -> Stash:
    """Price the wait around a net the caller has already judged.

    `net` and `late_net` are the caller's own two nets **before** the dead
    charge -- the recommender's judgement of the move in the mean branch and
    in the still-out-in-four-weeks branch -- so nothing about the judgement is
    re-derived here. `horizon_days` is the days the plan covers from today,
    and `opened` this league's `OPENED_PLACE`.
    """
    dead_days = 0.0 if ir_slot_free else expected_dead_days(horizon_days, days_out=days_out)
    dead_weeks = dead_days / DAYS_A_WEEK
    cost = opened * dead_weeks
    late_days = (
        0.0
        if ir_slot_free
        else expected_dead_days_if_out_past(
            horizon_days, days_out=days_out, past_days=int(late_week * DAYS_A_WEEK)
        )
    )
    return Stash(
        player_id=player_id,
        name=name,
        days_out=int(days_out),
        return_odds_by_week=odds_back_by_week(int(days_out), odds_weeks),
        expected_dead_days=dead_days,
        expected_dead_weeks=dead_weeks,
        dead_cost=cost,
        expected_net=net - cost,
        net_if_out_past_week=late_net - opened * (late_days / DAYS_A_WEEK),
        late_dead_weeks=late_days / DAYS_A_WEEK,
        late_week=late_week,
        ir_slot_free=ir_slot_free,
        expected_games=expected_games,
        healthy_games=healthy_games,
    )
