"""Which seasons' stored projections are real preseason forecasts.

`player_season_stats` holds one "projected" line per player per season,
read from the player card ESPN serves *today*. For most seasons that is
the preseason forecast. For two it is not, and nothing on the row says so:

2020  COVID truncated the season. 222 of the 226 stored projected lines
      carry no stats at all, and the four that do are on a scale nothing
      else uses.

2023  Not a preseason forecast. ESPN retained a rest-of-season projection
      captured about 41% of the way through the year: every player's
      projected games sit at a median 0.59 of his actual games with an
      interquartile range of 0.18 -- tighter than any real preseason year,
      which run 0.27 to 0.34 -- and his projected per-game rates match his
      actual rates at 1.005 with half the spread of any other season. That
      is what a projection looks like when most of it is already history.
      A board built on it is 60% hindsight; a calibration against it says
      the board is better than it is; the projection-gaps view says everyone
      beat a forecast that was made of their own first forty games.

Both were found by measurement, not by reading ESPN's documentation, which
says nothing. `looks_like_snapshot` is the rule that flags 2023 from its
numbers alone, so a future season with the same problem is caught by a
test rather than by someone noticing a table looks odd.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Final

#: Seasons whose stored "projected" lines are not preseason forecasts, and
#: why. Anything that values, calibrates or compares against projections
#: consults this and refuses, flags, or asks to be told it knows.
UNUSABLE_PROJECTIONS: Final[dict[int, str]] = {
    2020: "COVID-truncated season; almost every stored projected line carries no stats",
    2023: (
        "not a preseason forecast: a rest-of-season projection captured mid-season, so "
        "projected games are ~59% of actual and per-game rates are largely actual"
    ),
}

#: A real preseason forecast's projected-to-actual games ratio sits near one
#: and varies widely, because injuries are unforeseen. A snapshot taken part
#: way through a season sits well below one and varies little, because the
#: games already played are subtracted from everyone alike.
SNAPSHOT_MAX_MEDIAN_RATIO = 0.8
SNAPSHOT_MAX_IQR = 0.22
#: Fewer pairs than this and the medians say nothing.
MIN_PAIRS = 30


def projection_problem(season: int) -> str | None:
    """Why this season's projections cannot be treated as a forecast, or None."""
    return UNUSABLE_PROJECTIONS.get(season)


def usable(season: int) -> bool:
    return season not in UNUSABLE_PROJECTIONS


def looks_like_snapshot(projected_games: Sequence[float], actual_games: Sequence[float]) -> bool:
    """Whether a season's projections were captured mid-season rather than before it.

    Takes projected and actual games for the same players, in the same
    order, and asks whether the ratio is both low and tight. Both are
    needed: a low median alone could be a year of many injuries; a tight
    spread alone could be a year of few. Low *and* tight is a subtraction
    applied to everyone, which is a date.
    """
    pairs = [
        projected / actual
        for projected, actual in zip(projected_games, actual_games, strict=True)
        if actual > 0 and projected > 0
    ]
    if len(pairs) < MIN_PAIRS:
        return False
    quartiles = statistics.quantiles(pairs, n=4)
    median = quartiles[1]
    iqr = quartiles[2] - quartiles[0]
    return median < SNAPSHOT_MAX_MEDIAN_RATIO and iqr < SNAPSHOT_MAX_IQR
