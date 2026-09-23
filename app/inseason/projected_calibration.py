"""What the projected standings have been measured at, as data and a sentence.

The pages print `CALIBRATION_NOTE` verbatim beside the numbers, because a
manager reading a forecast is owed the forecast's record next to it. The
figures below are the run of 2026-09-22 (`scripts/projected_calibration.py
--season 2026 --sims 2000`), written up in `docs/projected_record.md`.
Nothing here is computed at runtime: it is a published result, and it changes
only when the calibration is run again and the doc is rewritten with it.

THE SHORT VERSION

**The forecast is overconfident, badly, and says so on the page.** Replaying
2026 from thirty-eight mornings -- the start and the midpoint of each of the
nineteen regular-season weeks -- and scoring every per-category probability it
made about every remaining week gives 47,880 of them and a Brier score of
0.2288, where saying "coin" to everything scores 0.2500. The reliability
table is the finding: categories it called at 95% were won 81% of the time,
and ones it called at 5% were won 19%. Every row is pulled toward the middle,
in both directions, at every distance. The forecast knows which side is
better; it does not know how much better.

It is much better about the week in front of it than about the rest. Brier by
distance runs 0.173 for the week being played, 0.216 for the next one, and
settles at about 0.24 from four weeks out -- which is barely better than a
coin. The matchup-winner hit rate does the same: 68.4% this week, 63.5% next
week, and about 55% from five weeks out over 5,320 team-weeks.

The record it projects is worth more than the probabilities behind it. Made
at the halfway mark of 2026, each team's final category record was off by 7.4
categories in 171 -- a bit over four percent, and about four tenths of a
category per week still to play. The quarter mark gives 8.7 and the
three-quarter mark 4.9, which is the shape you would expect of something that
is learning as the season goes.

The playoff odds are honest at the ends and poor in the middle. Teams given
better than 90% made it 98.7% of the time and teams given under 10% made it
7.9%; but teams given 50-60% made it 30% and teams given 80-90% made it 67%.
That middle band is where the overconfidence lands, and it is the band a
manager actually reads.

WHAT WAS NOT DONE

The variance model was not tuned on this run. The doc proposes a change and
prices it, twice, without making it (`WIDENED`). Widening every weekly spread
by sqrt(2) -- what the spread of the difference between two teams' totals
would be if they wobbled independently -- takes the Brier score to 0.2202 and
halves the gap at the extremes without closing it. Widening it by **two**
takes it to 0.2179 and makes the table calibrated almost everywhere: 0.159
predicted against 0.150 happened, 0.354 against 0.355, 0.646 against 0.645,
0.841 against 0.850. Only the last tenth stays cocky, and by then it holds
317 calls rather than 3,872, because a model that wide almost never claims
95%.

That is a change to `app.pickups.stream.head_to_head`, which the pickup
hurdle, the streaming report and the trade evaluator are all priced on. It
would move every one of those numbers, so it is a decision of its own with
its own re-measurement, and not a knob this module may turn.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BRIER",
    "BRIER_BY_LEAD",
    "CALIBRATION_NOTE",
    "CATEGORY_RELIABILITY",
    "CHECKPOINTS",
    "COIN_BRIER",
    "MEASURED_ON",
    "N_CATEGORY_CALLS",
    "PLAYOFF_RELIABILITY",
    "RECORD_ERROR",
    "REPLAYED_SEASON",
    "SHORT_NOTE",
    "WIDENED",
    "WIDENED_BRIER",
    "WINNER_HIT_RATE",
    "WINNER_SAMPLE",
    "Reliability",
]


@dataclass(frozen=True)
class Reliability:
    """One row of a reliability table: how sure it was, how often it happened."""

    #: The bucket, e.g. (0.9, 1.0).
    band: tuple[float, float]
    n: int
    predicted: float
    happened: float

    @property
    def gap(self) -> float:
        """How far the forecast sat above what happened. Positive is cocky."""
        return self.predicted - self.happened


#: The day the published run was made, and the season it replayed.
MEASURED_ON = "2026-09-22"
REPLAYED_SEASON = 2026

#: Mornings replayed: the first day and the midpoint of each of the nineteen
#: regular-season matchup periods.
CHECKPOINTS = 38

#: Per-category probabilities scored, over every remaining week of every
#: checkpoint, and the Brier score they earned. A forecast that said "coin"
#: to all of them would score `COIN_BRIER`.
N_CATEGORY_CALLS = 47_880
BRIER = 0.2288
COIN_BRIER = 0.25

#: The same run with every weekly spread widened by a factor, as a
#: diagnostic: the factor mapped to the Brier score it earned. **Not** the
#: shipped model, which is 1.0; see the module docstring and
#: docs/projected_record.md, which proposes 2.0 and says what it would cost.
WIDENED: dict[float, float] = {1.0: 0.2288, 1.4142: 0.2202, 2.0: 0.2179}
WIDENED_BRIER = WIDENED[1.4142]

#: Brier by how many weeks ahead the week was, 0 being the one being played.
#: Truncated at six; beyond that it is flat at about 0.24.
BRIER_BY_LEAD: tuple[float, ...] = (0.1725, 0.2158, 0.2236, 0.2243, 0.2252, 0.2351, 0.2367)

#: Predicted against what happened, per category, in tenths.
CATEGORY_RELIABILITY: tuple[Reliability, ...] = (
    Reliability((0.0, 0.1), 3872, 0.049, 0.193),
    Reliability((0.1, 0.2), 4317, 0.151, 0.295),
    Reliability((0.2, 0.3), 4921, 0.251, 0.370),
    Reliability((0.3, 0.4), 5182, 0.350, 0.414),
    Reliability((0.4, 0.5), 5648, 0.450, 0.480),
    Reliability((0.5, 0.6), 5648, 0.550, 0.520),
    Reliability((0.6, 0.7), 5182, 0.650, 0.586),
    Reliability((0.7, 0.8), 4921, 0.749, 0.630),
    Reliability((0.8, 0.9), 4317, 0.849, 0.705),
    Reliability((0.9, 1.0), 3872, 0.951, 0.807),
)

#: The same for the playoff odds: teams given X% made it Y%.
PLAYOFF_RELIABILITY: tuple[Reliability, ...] = (
    Reliability((0.0, 0.1), 126, 0.023, 0.079),
    Reliability((0.1, 0.2), 36, 0.160, 0.250),
    Reliability((0.2, 0.3), 53, 0.250, 0.321),
    Reliability((0.3, 0.4), 33, 0.346, 0.455),
    Reliability((0.4, 0.5), 37, 0.454, 0.378),
    Reliability((0.5, 0.6), 23, 0.545, 0.304),
    Reliability((0.6, 0.7), 26, 0.650, 0.462),
    Reliability((0.7, 0.8), 25, 0.753, 0.680),
    Reliability((0.8, 0.9), 18, 0.855, 0.667),
    Reliability((0.9, 1.0), 155, 0.982, 0.987),
)

#: Matchups called, both sides of each, and the share called right. A coin is
#: 0.500; the number is 0.684 for the week being played and about 0.55 from
#: five weeks out.
WINNER_SAMPLE = 5320
WINNER_HIT_RATE = 0.580

#: Mean absolute error of a team's projected final category record, in
#: categories of the 171 a nineteen-week season contests, made at three marks.
RECORD_ERROR = {"quarter": 8.7, "half": 7.4, "three-quarter": 4.9}

#: The one line the pages put under the projected record, from
#: `RECORD_ERROR["half"]`. Kept as a written sentence rather than formatted at
#: runtime, so the words are reviewed with the number, and guarded by a test
#: that the number in it is the number above.
SHORT_NOTE = (
    "Made at the halfway mark of 2026, this projection's final records were off by "
    "7.4 categories on average."
)

#: Printed in full beside the table. No jargon, no verdict, and no claim the
#: run does not support.
CALIBRATION_NOTE = (
    "This is a forecast, and here is its record. Replaying the 2026 season from "
    "thirty-eight mornings and scoring every category it called about every week "
    "still to play -- 47,880 calls -- it is clearly overconfident: categories it "
    "gave a 95% chance were won 81% of the time, and ones it gave 5% were won 19%. "
    "Read a number near the ends of the scale as a direction rather than a "
    "quantity. It is much better about the week in front of it than about the rest "
    "of the season: it named the right side of this week's matchup about 68% of the "
    "time, next week's about 64%, and a week five or more ahead about 55%, where a "
    "coin gives 50%. The record it projects holds up better than the chances behind "
    "it -- made at the halfway mark of 2026, each team's final category record was "
    "off by 7.4 of 171 on average, and by 4.9 with a quarter of the season left. "
    "The playoff odds are trustworthy at the extremes and not in the middle: teams "
    "given better than 90% made it 99% of the time, and teams given 50-60% made it "
    "30%. Read these numbers as one input to a decision that is yours."
)
