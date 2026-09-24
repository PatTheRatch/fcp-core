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

THE PLAYOFF LENS, FOR A TEAM THAT HAS ALREADY WON ITS PLACE

Everything above prices a dead week at `OPENED_PLACE` and counts the benefit
on the weeks left in the regular season. That is right for a team in the race
and wrong for a lock. A lock's dead regular weeks buy only seeding -- the
place in the table it would have had anyway -- and what the roster place is
really buying is the man **in the playoff weeks**, times the chance he is back
for them.

So a lock gets a second reading beside the first, never instead of it
(`Lock`, `lock_block`):

    benefit = P(back by the first playoff week) x (his playoff-weeks value
              less the wire's, over those weeks)
    cost    = OPENED_PLACE x expected dead regular weeks x seeding stake
              + OPENED_PLACE x expected dead playoff weeks

The **seeding stake** is `1 - max(seed odds)`: the chance the team does not
finish in its own most likely place. Zero means the seed is settled and the
dead regular weeks cost nothing at all; one means the study's own pricing.
Both bounds are carried beside the measured number, because the stake is an
estimate and the two ends are not.

`docs/stash_locks.md` is the measurement behind it: over eight seasons the
lock's cost is about a third of the standard charge, and the two nets
disagree about a fifth of the time. Nothing here is a bar and no constant
moves.

NOTHING HERE IS A BAR

The hurdle is untouched and nothing is labelled against this. A stash under
the bar still appears with its number and its odds, which is the owner's rule
(`docs/product.md`, memory `tool-not-gospel`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.pickups.judge import DAYS_A_WEEK, SpotBook, horizon, weeks_between
from app.pickups.projection import rest_of_season_line
from app.pickups.returns import (
    PRIOR_SOURCE,
    RAMP_SOURCE,
    expected_dead_days,
    expected_dead_days_if_out_past,
    expected_games_if_out_past,
    odds_back_by_week,
    probability_back_within,
)
from app.pickups.state import RosteredPlayer, build_players

__all__ = [
    "LATE_WEEK",
    "LOCK_LANGUAGE",
    "LOCK_ODDS",
    "ODDS_WEEKS",
    "STASH_LANGUAGE",
    "Lock",
    "Stash",
    "held_stashes",
    "lock_block",
    "stash_block",
]

#: The weeks a page prints the return odds for.
ODDS_WEEKS: tuple[int, ...] = (1, 2, 4, 8)

#: The branch the second arm is about: still out in four weeks.
LATE_WEEK = 4

#: Playoff odds at or above which the second reading is printed at all. The
#: band `docs/projected_record.md` section 0 says the forecast is honest in:
#: of 370 calls above ninety per cent, 83.5% happened.
LOCK_ODDS = 0.95

#: What the lock's reading is and is not, beside the odds themselves.
LOCK_LANGUAGE = (
    "a lock's dead regular-season weeks buy seeding and nothing else, so they are charged "
    "at the chance the seed is still moving rather than in full; the benefit is counted on "
    "the playoff weeks alone, times the chance he is back for them. The two bounds are the "
    "same number with the seed treated as settled and as wide open. It is a second reading "
    "of the same move, not a second bar: nothing is labelled against it "
    "(docs/stash_locks.md)."
)

#: What the odds are and are not, in the words a page and an assistant repeat.
STASH_LANGUAGE = (
    "the odds are the NBA's own return record for men who have been out this long -- "
    f"{PRIOR_SOURCE} -- and not a diagnosis, a timeline or anything anybody has said about "
    "this man. ESPN's basketball API carries no return date at all, so there is no date to "
    "print and the distribution is the answer. The ramp on the other side is "
    f"{RAMP_SOURCE}."
)


@dataclass(frozen=True)
class Lock:
    """The same wait, read again for a team that has already won its place.

    Every field is a quantity the caller's own engine produced: the odds and
    the stake come off the projected standings, the playoff-weeks value off
    the same lens the judgement uses, and the dead weeks off the same return
    prior. Nothing here is a bar and nothing is recommended.
    """

    #: The team's playoff odds on this morning, as the projection has them.
    playoff_odds: float
    #: `1 - max(seed odds)`: the chance it does not finish where it most
    #: likely finishes. Zero means the seed is settled.
    seeding_stake: float
    #: P(he plays again by the first playoff day), from the return prior.
    back_by_playoffs: float
    #: Playoff matchup weeks still ahead, and his value over them against the
    #: wire's best man, in categories over the whole window.
    playoff_weeks: float
    playoff_weeks_value: float
    #: Dead weeks expected before the playoffs begin, and during them.
    dead_regular_weeks: float
    dead_playoff_weeks: float
    #: The benefit, the cost and their difference, at the measured stake.
    benefit: float
    cost: float
    lock_net: float
    #: The same net with the seed treated as settled (stake 0) and as wide
    #: open (stake 1, which is how the regular-season reading prices it).
    net_if_seed_settled: float
    net_if_seed_open: float
    language: str = LOCK_LANGUAGE

    @property
    def line(self) -> str:
        """The one line a page prints under the stash line. No verdict words."""
        return (
            f"You are a lock ({self.playoff_odds * 100:.0f}%) · "
            f"the dead weeks cost your seeding {self.cost:.2f} · "
            f"back for the playoffs {self.back_by_playoffs * 100:.0f}% · "
            f"worth {_signed(self.playoff_weeks_value)} over the "
            f"{self.playoff_weeks:.0f} playoff weeks · "
            f"lock net {_signed(self.lock_net)} "
            f"({_signed(self.net_if_seed_settled)} to {_signed(self.net_if_seed_open)})"
        )


def _signed(value: float) -> str:
    """Two decimals with a sign, and never `-0.00`: that is rounding, not a loss."""
    written = f"{value:+.2f}"
    return "+0.00" if written == "-0.00" else written


def lock_block(
    *,
    playoff_odds: float,
    seeding_stake: float,
    days_out: int,
    horizon_days: int,
    days_to_playoffs: int,
    playoff_weeks: float,
    playoff_weeks_value: float,
    opened: float,
    ir_slot_free: bool,
) -> Lock:
    """Price the same wait for a lock: seeding on one side, the playoffs on the other.

    `days_to_playoffs` is days from today to the first playoff scoring period,
    zero or less once they have begun. `playoff_weeks_value` is what he is
    worth over those weeks **against the wire's best man**, in categories over
    the whole window, which is the caller's own lens answering the same
    question `judge` answers for the rest of the season.

    The expected dead days are the ones `app.pickups.returns` already counts,
    split at the first playoff day: every day before it is charged at the
    seeding stake, every day after it in full. With a free injured-reserve
    slot there is no dead place and no charge at all, which is the same gate
    `stash_block` reads.
    """
    waiting = max(0, min(int(days_to_playoffs), int(horizon_days)))
    dead_regular = 0.0 if ir_slot_free else _dead_days(0, waiting, days_out=days_out)
    dead_playoff = (
        0.0 if ir_slot_free else _dead_days(waiting, int(horizon_days), days_out=days_out)
    )
    regular_weeks = dead_regular / DAYS_A_WEEK
    playoff_dead_weeks = dead_playoff / DAYS_A_WEEK
    back = 1.0 if days_to_playoffs <= 0 else probability_back_within(days_out, days_to_playoffs)
    benefit = back * playoff_weeks_value

    def net(stake: float) -> float:
        return benefit - opened * (regular_weeks * stake + playoff_dead_weeks)

    stake = min(1.0, max(0.0, float(seeding_stake)))
    return Lock(
        playoff_odds=float(playoff_odds),
        seeding_stake=stake,
        back_by_playoffs=back,
        playoff_weeks=float(playoff_weeks),
        playoff_weeks_value=float(playoff_weeks_value),
        dead_regular_weeks=regular_weeks,
        dead_playoff_weeks=playoff_dead_weeks,
        benefit=benefit,
        cost=opened * (regular_weeks * stake + playoff_dead_weeks),
        lock_net=net(stake),
        net_if_seed_settled=net(0.0),
        net_if_seed_open=net(1.0),
    )


def _dead_days(first: int, last: int, *, days_out: int) -> float:
    """`expected_dead_days` over one slice of the horizon, days `first` to `last`.

    The same sum `app.pickups.returns.expected_dead_days` takes over the whole
    of it -- one less the chance he is back by each day -- so the two slices
    add up to exactly what that function returns for the whole horizon.
    """
    if last <= first:
        return 0.0
    return sum(
        1.0 - probability_back_within(days_out, offset) for offset in range(int(first), int(last))
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
    #: The same wait read again for a team that has already won its place
    #: (`Lock`). None for everybody else, which is every ordinary morning: a
    #: caller with no projected standings in hand never fills it.
    lock: Lock | None = None

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
    return_in_days: int | None = None,
) -> Stash:
    """Price the wait around a net the caller has already judged.

    `net` and `late_net` are the caller's own two nets **before** the dead
    charge -- the recommender's judgement of the move in the mean branch and
    in the still-out-in-four-weeks branch -- so nothing about the judgement is
    re-derived here. `horizon_days` is the days the plan covers from today,
    and `opened` this league's `OPENED_PLACE`.

    `return_in_days` is ESPN's own date, when it gives one: **the date wins**,
    so the wait is that many days and the odds are one from the week it falls
    in and zero before it, with no second arm to print. ESPN's basketball API
    has never given one, so the prior answers in practice; the branch is here
    because the declared rule says the date wins and a rule with no code is
    not a rule.
    """
    dated = return_in_days is not None
    if dated:
        waiting = float(max(0, min(int(return_in_days or 0), horizon_days)))
        odds = {
            int(week): (1.0 if DAYS_A_WEEK * week >= float(return_in_days or 0) else 0.0)
            for week in odds_weeks
        }
    else:
        waiting = expected_dead_days(horizon_days, days_out=days_out)
        odds = odds_back_by_week(int(days_out), odds_weeks)
    dead_days = 0.0 if ir_slot_free else waiting
    dead_weeks = dead_days / DAYS_A_WEEK
    cost = opened * dead_weeks
    if ir_slot_free:
        late_days = 0.0
    elif dated:
        late_days = waiting
    else:
        late_days = expected_dead_days_if_out_past(
            horizon_days, days_out=days_out, past_days=int(late_week * DAYS_A_WEEK)
        )
    return Stash(
        player_id=player_id,
        name=name,
        days_out=int(days_out),
        return_odds_by_week=odds,
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


def held_stashes(
    session: Session,
    league_season: LeagueSeason,
    spots: SpotBook,
    today: int,
    players: Iterable[RosteredPlayer],
    *,
    ir_slot_free: bool,
    tilt: bool = True,
    as_of: date | None = None,
) -> tuple[Stash, ...]:
    """The block for every man in `players` ESPN has ruled out, longest out first.

    What a report says about the men it is already counting for a fraction of
    their games: how long they have been out, the odds on each week, what the
    place costs while they wait and the net either side of it. The net is the
    one `judge` would charge -- what he is worth to the place against what the
    wire would give it back -- so nothing here invents a second currency.

    A man already on injured reserve costs no place, whatever the roster's
    room is: he is not in it.
    """
    out = [player for player in players if player.ruled_out]
    if not out:
        return ()
    _first, last, day = horizon(session, league_season, today)
    weeks = weeks_between(day, last)
    horizon_days = last - day + 1
    rebuilt = {
        player.player_id: player
        for player in build_players(
            session,
            league_season,
            [player.player_id for player in out],
            tuple(range(day, last + 1)),
        )
    }
    blocks: list[Stash] = []
    for player in out:
        man = rebuilt.get(player.player_id, player)
        days_out = man.days_out or 1
        dated = (
            None
            if man.expected_return_date is None or as_of is None
            else (man.expected_return_date - as_of).days
        )
        replacement = spots.replacement(exclude=[man.player_id])
        late_line = rest_of_season_line(
            session,
            int(league_season.season),
            man.player_id,
            day,
            expected_games_if_out_past(
                (period - day for period in man.schedule_days),
                days_out=days_out,
                past_days=int(LATE_WEEK * DAYS_A_WEEK),
            ),
            tilt=tilt,
            as_of=as_of,
        ).scaled(1.0 / weeks)
        blocks.append(
            stash_block(
                player_id=man.player_id,
                name=man.name,
                days_out=days_out,
                horizon_days=horizon_days,
                opened=spots.opened,
                ir_slot_free=ir_slot_free or player.on_ir,
                net=(spots.value(man.player_id) - replacement) * spots.weeks_remaining,
                late_net=(spots.lens.value(late_line) - replacement) * spots.weeks_remaining,
                expected_games=man.season_games,
                healthy_games=len(man.schedule_days),
                return_in_days=dated,
            )
        )
    blocks.sort(key=lambda block: (-block.days_out, block.name))
    return tuple(blocks)
