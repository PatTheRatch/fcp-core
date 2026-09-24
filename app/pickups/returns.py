"""When a man who is ruled out plays again, and what he is worth when he does.

THE DECLARED RULE, IN THREE SENTENCES

1. **An OUT man's games are expected, not zero.** For a man ruled out with no
   ESPN return date, each remaining game day counts with the probability he is
   back by then -- read off the box-score return prior below by how many days
   he has been out already -- times the ramp he comes back on, as a fractional
   games weight in the same place a healthy man's whole games are counted.
2. **With an ESPN return date, the date wins** for the expected return and the
   ramp still applies after it.
3. **The dead weeks are priced** by the caller, not here: this module says how
   many days the place is expected to stand empty (`expected_dead_days`), and
   `app.inseason.what_if` multiplies them by what an opened place is worth --
   unless the league has a free injured-reserve slot, in which case the place
   is not dead and the charge is zero.

Declared 2026-09-24, before any calibration was run, and not tuned afterwards
(`docs/stash_mode.md`, "Declared").

WHERE THE NUMBERS COME FROM

`RETURN_PRIOR` is `docs/stashes.md` section 2a, the seven-season box-score
curve with the suspended 2020 season taken out, measured 2026-09-23 over
11,473 absences by `scripts/stashes.py`. It is shipped as a table rather than
recomputed per request: it is a measurement, and re-measuring it every morning
would make it drift.

`RAMP_FIRST_WEEK` and `RAMP_SECOND_WEEK` are section 3 of the same document:
a man back from a month out returns 82% of his old value over his first five
games and 94% over the next five, which at about 3.3 games a week is 0.91 on
the first week back and 0.98 on the second, and nothing after that.

Both are facts about the NBA and not about one league, so neither is a
per-league calibration key (`app.calibration`, `docs/intake.md`). The
injured-reserve slot **is** a league setting and is read from
`league_seasons.injured_reserve_slots` wherever it matters.

THE UNITS, WHICH ARE EASY TO GET WRONG

`days_out` is **calendar days since his last played game**, which is what
`scripts/stashes.py` measures (`Standing.days_out` is the decision day less
his last `played = true` scoring period, and a scoring period is a calendar
day in this database). The horizon `within_days` is calendar days ahead of
today, and the prior's cell at (N, M) is P(he plays again by day N + M given
he is still out at day N) -- so a game `o` days from now is reached with
`probability_back_within(days_out, o)`.

`docs/stashes.md` section 2b's prose says a box-score day out "counts only
nights his team played". The script it describes does not do that, and the
table above is the script's. The caution section 2b is really making still
stands and is worth repeating: this curve is **not** interchangeable with
`docs/availability.md` table 2, which counts report days, and the two must
never be read across the same N column.

WHAT IS HELD, AND WHAT IS CHAINED

The prior's last row is 28 days out and its last column is 28 days ahead.
**A man out longer than 28 days keeps the 28-day row**, held rather than
extrapolated. **A horizon longer than 28 days is the same table asked
again**: a man still out in four weeks is a man four weeks further out, and
his chances from that morning are the row he has then reached. Neither
invents a number and neither was chosen on a calibration.

The chain is worth a check, because the alternative -- flat-lining the last
column -- would say a man 28 days out has a 47.6% chance of never playing
again this season, and `docs/stashes.md` section Limitations 5 measures 26.0%
at the 29+ level with 2020 taken out. The chain says 22.7% are still out
fifty-six days later and 10.8% eighty-four days later, which brackets the
measured figure from the right side. Flat-lining does not.

WHAT THIS DOES NOT TOUCH

Doubtful and Questionable are not in `RULED_OUT_STATUSES` and nothing here
sees them; the availability study's play rates are a separate, later change.
And the week's own seating is untouched: `app.pickups.state.playable_days`
still counts an OUT man for no games tonight, and `app.inseason.startable`
still seats nobody, because a man OUT tonight is out tonight. The prior
belongs to the season horizon, which is the distinction
`docs/availability.md` section 1c exists to prevent anybody from blurring.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from itertools import pairwise

__all__ = [
    "PRIOR_DAYS_OUT",
    "PRIOR_HORIZONS",
    "PRIOR_SAMPLE",
    "PRIOR_SOURCE",
    "RAMP_FIRST_WEEK",
    "RAMP_SECOND_WEEK",
    "RAMP_SOURCE",
    "RETURN_PRIOR",
    "expected_dead_days",
    "expected_dead_days_if_out_past",
    "expected_games",
    "expected_games_from_date",
    "expected_games_if_out_past",
    "odds_back_by_week",
    "probability_back_within",
    "ramp",
    "return_density",
    "still_out_after",
]

#: The rows of `RETURN_PRIOR`: how many days he has been out already.
PRIOR_DAYS_OUT: tuple[int, ...] = (1, 3, 7, 14, 28)

#: The columns: how many more days ahead the question asks about.
PRIOR_HORIZONS: tuple[int, ...] = (1, 3, 7, 14, 28)

#: P(he plays again within M more days | out N days and still out).
#: `docs/stashes.md` section 2a, seven seasons with 2020 taken out.
RETURN_PRIOR: Mapping[int, Mapping[int, float]] = {
    1: {1: 0.0011, 3: 0.3833, 7: 0.6810, 14: 0.8230, 28: 0.8839},
    3: {1: 0.2463, 3: 0.4996, 7: 0.7074, 14: 0.8290, 28: 0.8895},
    7: {1: 0.1293, 3: 0.3319, 7: 0.5466, 14: 0.6999, 28: 0.8016},
    14: {1: 0.0781, 3: 0.2081, 7: 0.3644, 14: 0.5279, 28: 0.6658},
    28: {1: 0.0241, 3: 0.0979, 7: 0.2045, 14: 0.3608, 28: 0.5241},
}

#: Absences still running at each row, so a reader knows what the cell is
#: measured on. The same table's "still out" column.
PRIOR_SAMPLE: Mapping[int, int] = {1: 11473, 3: 8996, 7: 3619, 14: 1523, 28: 582}

PRIOR_SOURCE = (
    "docs/stashes.md section 2a, the box-score return prior over seven seasons "
    "(2020 taken out), 11,473 absences, measured 2026-09-23"
)

#: What a man is worth in his first week back and his second, as a share of
#: his own pre-absence line. `docs/stashes.md` section 3.
RAMP_FIRST_WEEK = 0.91
RAMP_SECOND_WEEK = 0.98

#: Days in each ramp band.
RAMP_BAND_DAYS = 7

RAMP_SOURCE = (
    "docs/stashes.md section 3, minutes and value over the first twenty games "
    "back across 2,419 absences, measured 2026-09-23"
)

#: The furthest ahead the prior is measured, and so the furthest a return is
#: counted at all.
MAX_HORIZON = PRIOR_HORIZONS[-1]


def ramp(days_since_return: float) -> float:
    """What he is worth on a game this many days after he came back.

    `RAMP_FIRST_WEEK` for his first seven days back, `RAMP_SECOND_WEEK` for
    the seven after that, and his whole line from then on. A negative gap --
    a game before the return, which only an ESPN date in the past can
    produce -- reads as his first day back.
    """
    if days_since_return < RAMP_BAND_DAYS:
        return RAMP_FIRST_WEEK
    if days_since_return < 2 * RAMP_BAND_DAYS:
        return RAMP_SECOND_WEEK
    return 1.0


def _interpolate(low_key: float, high_key: float, low: float, high: float, at: float) -> float:
    if high_key <= low_key:
        return low
    share = (at - low_key) / (high_key - low_key)
    return low + (high - low) * share


def _row(days_out: float) -> Mapping[int, float]:
    """The prior's row for `days_out`, between the measured ones.

    Below the first row it is the first row: a man ruled out this morning has
    been out at least a day. Above the last it is the last, held rather than
    extrapolated.
    """
    if days_out <= PRIOR_DAYS_OUT[0]:
        return RETURN_PRIOR[PRIOR_DAYS_OUT[0]]
    if days_out >= PRIOR_DAYS_OUT[-1]:
        return RETURN_PRIOR[PRIOR_DAYS_OUT[-1]]
    for low, high in pairwise(PRIOR_DAYS_OUT):
        if low <= days_out <= high:
            return {
                horizon: _interpolate(
                    low, high, RETURN_PRIOR[low][horizon], RETURN_PRIOR[high][horizon], days_out
                )
                for horizon in PRIOR_HORIZONS
            }
    return RETURN_PRIOR[PRIOR_DAYS_OUT[-1]]


def _within_measured(days_out: float, within_days: float) -> float:
    """The prior inside its own measured range, interpolated both ways.

    Anchored at zero today: he is out this morning, so the chance he is back
    in no days at all is zero.
    """
    row = _row(days_out)
    if within_days >= PRIOR_HORIZONS[-1]:
        return row[PRIOR_HORIZONS[-1]]
    low_key, low = 0.0, 0.0
    for horizon in PRIOR_HORIZONS:
        if within_days <= horizon:
            return _interpolate(low_key, horizon, low, row[horizon], within_days)
        low_key, low = float(horizon), row[horizon]
    return low


def probability_back_within(days_out: float, within_days: float) -> float:
    """P(he plays again within `within_days` days | out `days_out` and still out).

    Inside the table's own range this is `RETURN_PRIOR` interpolated between
    the five rows and the five columns. Past its last column the same table is
    asked again from the row he would then be on: a man out `N` days who is
    still out in four weeks is a man out `N + 28` days, and his chances from
    that morning are that row's. Nothing is extrapolated and no new number is
    invented; the curve just goes on being read (the module docstring).
    """
    if within_days <= 0:
        return 0.0
    if within_days <= MAX_HORIZON:
        return _within_measured(days_out, within_days)
    first = _within_measured(days_out, MAX_HORIZON)
    later = probability_back_within(days_out + MAX_HORIZON, within_days - MAX_HORIZON)
    return first + (1.0 - first) * later


@lru_cache(maxsize=4096)
def return_density(days_out: int, horizon: int) -> tuple[float, ...]:
    """P(he comes back exactly `r` days from now), for r = 0 .. `horizon`.

    The prior differenced. Index 0 is zero by construction, and the tuple sums
    to the chance he is back inside `horizon` at all: what is left over is the
    chance he is not, which earns no games and pays a dead day for every day
    of the horizon.
    """
    cdf = [probability_back_within(days_out, offset) for offset in range(horizon + 1)]
    return tuple(
        0.0 if offset == 0 else max(0.0, cdf[offset] - cdf[offset - 1])
        for offset in range(horizon + 1)
    )


def expected_games(offsets: Iterable[int], *, days_out: int) -> float:
    """Fractional games over game days `offsets` days from today, ramp included.

    `offsets` is one entry per remaining day his NBA team plays, counted from
    today (0 is tonight). A game `o` days out is worth the chance he came back
    on each earlier day `r`, times what the ramp says he is worth `o - r` days
    into his return. Today itself is worth nothing: he is out this morning.
    """
    days = [int(offset) for offset in offsets if offset > 0]
    if not days:
        return 0.0
    density = return_density(int(days_out), max(days))
    total = 0.0
    for offset in days:
        total += sum(density[r] * ramp(offset - r) for r in range(1, offset + 1))
    return total


def expected_games_from_date(gaps: Iterable[float]) -> float:
    """The same count when ESPN has given a return date, so the date wins.

    `gaps` is one entry per remaining game day: the days between that game and
    the return date, negative before it. A game before the date is not played;
    one on or after it is played, at the ramp for how long he has been back.
    """
    return sum(ramp(gap) for gap in gaps if gap >= 0)


def expected_dead_days(horizon_days: int, *, days_out: int) -> float:
    """Days the roster place is expected to stand empty, today included.

    The chance he is still out summed over every day of the horizon: today is
    dead outright, and each day after it is dead with probability one less the
    chance he is back by then. A man who never returns inside the prior's last
    column is dead for the whole horizon, which is what the leftover mass in
    `return_density` means.
    """
    if horizon_days <= 0:
        return 0.0
    return sum(
        1.0 - probability_back_within(days_out, offset) for offset in range(int(horizon_days))
    )


def odds_back_by_week(days_out: int, weeks: Sequence[int] = (1, 2, 4, 8)) -> dict[int, float]:
    """P(back by the end of week k), for the weeks a page prints."""
    return {int(week): probability_back_within(days_out, 7 * int(week)) for week in weeks}


def still_out_after(days_out: int, past_days: int) -> int:
    """The days-out row a man reaches by being still out `past_days` from now.

    The branch the page's second arm is about. It is the same prior asked
    again from further along, which is the only honest way to condition it:
    the curve is *already* "given he is still out at N", so a man who does not
    come back inside four weeks is a man at row N + 28, and his chances from
    then on are that row's.
    """
    return int(days_out) + max(0, int(past_days))


def expected_games_if_out_past(offsets: Iterable[int], *, days_out: int, past_days: int) -> float:
    """`expected_games` in the branch where he is still out in `past_days` days.

    The prior re-read from the row he will then be on (`still_out_after`) and
    the game days re-counted from that day, so nothing is conditioned by hand:
    the same table answers the same question one month further along.
    """
    later = still_out_after(days_out, past_days)
    return expected_games(
        (offset - past_days for offset in offsets),
        days_out=later,
    )


def expected_dead_days_if_out_past(horizon_days: int, *, days_out: int, past_days: int) -> float:
    """`expected_dead_days` in the same branch: the wait, plus what follows it."""
    waited = min(int(past_days), int(horizon_days))
    later = still_out_after(days_out, past_days)
    return waited + expected_dead_days(int(horizon_days) - waited, days_out=later)
