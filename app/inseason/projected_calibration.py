"""What the projected standings have been measured at, as data and a sentence.

The pages print `CALIBRATION_NOTE` verbatim beside the numbers, because a
manager reading a forecast is owed the forecast's record next to it. The
figures below are the run of 2026-09-24 (`scripts/projected_calibration.py
--season 2026 --sims 2000`), written up in `docs/projected_record.md`.
Nothing here is computed at runtime: it is a published result, and it changes
only when the calibration is run again and the doc is rewritten with it.

THE REPLAY CAN SEE WHO WAS HURT, AND THIS IS THE RUN AFTER

Until 2026-09-24 a replayed morning read an injury status from the listener's
snapshots, which exist only for the season in progress, so every man in a
replayed 2026 was counted fit. The engine now reads the NBA's own official
report as of ten o'clock Eastern that morning whenever no snapshot is older
than it (`docs/replay_status.md`, the declared source order), and this is the
first run in which the ruled-out branch of the games count fires at all.

**It moved, and both ways.** The Brier rose from 0.2179 to 0.2184 and the
matchup-winner rate fell from 0.584 to 0.583, both inside what one run can
separate from noise; the projected final record at halfway improved from 7.2
categories to 6.3, which is the largest move any of the three records has
made since the spread was widened. The two extreme bands are reached more
often and are better calibrated when they are reached. Nothing was tuned on
it: the source order was declared before the run and no constant moved.

THE SPREAD WAS WIDENED ON 2026-09-23

The run of 2026-09-22 found the forecast badly overconfident and priced the
fix without making it. On 2026-09-23 the owner made it:
`app.pickups.stream.SPREAD_SCALE` is 2.0, so every weekly spread is twice the
one-team spread the league's results measure. `WIDENED` below keeps that
run's three factors, so the choice can be re-read without re-measuring it.

THE SHORT VERSION

**The forecast is about as sure as it ought to be, except at the very ends.**
Replaying 2026 from thirty-eight mornings -- the start and the midpoint of
each of the nineteen regular-season weeks -- and scoring every per-category
probability it made about every remaining week gives 47,880 of them and a
Brier score of 0.2184, where saying "coin" to everything scores 0.2500. The
reliability table is the finding: 0.158 predicted against 0.183 happened,
0.256 against 0.250, 0.354 against 0.354, 0.452 against 0.456, 0.646 against
0.646, 0.744 against 0.750. Only the outer tenths are still cocky -- 0.931
against 0.835, and 0.069 against 0.165 -- and each of them holds 370 calls
rather than the 3,872 they held at the old spread, because a model this wide
almost never claims 95%.

It is still much better about the week in front of it than about the rest.
Brier by distance runs 0.174 for the week being played, 0.211 for the next
one, and settles at about 0.23 from five weeks out -- which is barely better
than a coin. The matchup-winner hit rate does the same: 68.8% this week,
63.5% next week, and about 55% from five weeks out over 5,320 team-weeks.
Neither widening the spread nor reading the injury report can move a hit rate
much, and neither did: they change how sure the forecast is, not which side
it points at.

The record it projects is worth more than the probabilities behind it. Made
at the halfway mark of 2026, each team's final category record was off by 6.3
categories in 171 -- under four percent, and about three tenths of a category
per week still to play. The quarter mark gives 8.1 and the three-quarter mark
5.2.

The playoff odds are honest at the ends and not in the upper middle. Since
revision R6 (2026-09-25) every simulated table is ranked the way the league
is, on category win share, and the odds were re-run on that order: teams given
better than 90% made it 100% of the time (139 teams) and teams given under 10%
made it 5.0%; the 30-60% bands are now close to honest (33%, 47%, 52%); but
teams given 60-70% made it 49% and 70-80% made it 61%. Before R6, ranked by
matchups won, the same bands read 13.4% at the bottom, 37% and 37% at 40-60%
and 57% at 60-70%. docs/projected_record.md, revision R6, has both columns.

WHAT IS STILL NOT TUNED HERE

The factor was chosen on the run before this one and declared before this one
started; nothing in it was moved afterwards. `WIDENED` keeps all three
measured factors so the choice can be re-read without re-measuring, and
`SHIPPED_SCALE` records which of them is the product. The two model choices
`docs/projected_record.md` section 2 names are untouched: the nine categories
are still drawn independently in the simulation, and the draft's own use of
the same spreads (`app.draft.optimizer`, `app.draft.targets`) is a different
question and was not changed with this one.
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
    "PLAYOFF_RELIABILITY_BY_MATCHUPS",
    "RECORD_ERROR",
    "REPLAYED_SEASON",
    "SHIPPED_SCALE",
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
MEASURED_ON = "2026-09-24"
REPLAYED_SEASON = 2026

#: Mornings replayed: the first day and the midpoint of each of the nineteen
#: regular-season matchup periods.
CHECKPOINTS = 38

#: Per-category probabilities scored, over every remaining week of every
#: checkpoint, and the Brier score they earned. A forecast that said "coin"
#: to all of them would score `COIN_BRIER`.
N_CATEGORY_CALLS = 47_880
BRIER = 0.2184
COIN_BRIER = 0.25

#: The same thirty-eight mornings with every weekly spread multiplied by a
#: factor: the **effective** factor mapped to the Brier score it earned. All
#: three are the run of 2026-09-22, when the shipped model was 1.0 and the
#: other two were diagnostics; 2.0 was a diagnostic then and is the shipped
#: model since 2026-09-23, and the run of that day reproduced it exactly.
#: They are kept as that run gave them, status-blind, so the owner's choice
#: can be re-read on the evidence it was made on; `BRIER` above is the
#: current model's own score and is the one to quote.
#: `scripts/projected_calibration.py --sigma-scale` multiplies on top of the
#: shipped factor, so the diagnostic for 1.0 is now `--sigma-scale 0.5`.
WIDENED: dict[float, float] = {1.0: 0.2288, 1.4142: 0.2202, 2.0: 0.2179}

#: What `app.pickups.stream.SPREAD_SCALE` is, repeated here so the published
#: score can be tied to the model that earned it without this module importing
#: the recommender. A guard test holds the two together.
SHIPPED_SCALE = 2.0
WIDENED_BRIER = WIDENED[SHIPPED_SCALE]

#: Brier by how many weeks ahead the week was, 0 being the one being played.
#: Truncated at six; beyond that it is flat at about 0.23.
BRIER_BY_LEAD: tuple[float, ...] = (0.1737, 0.2107, 0.2142, 0.2153, 0.2152, 0.2221, 0.2266)

#: Predicted against what happened, per category, in tenths.
CATEGORY_RELIABILITY: tuple[Reliability, ...] = (
    Reliability((0.0, 0.1), 370, 0.069, 0.165),
    Reliability((0.1, 0.2), 1644, 0.158, 0.183),
    Reliability((0.2, 0.3), 4300, 0.256, 0.250),
    Reliability((0.3, 0.4), 7540, 0.354, 0.354),
    Reliability((0.4, 0.5), 10086, 0.452, 0.456),
    Reliability((0.5, 0.6), 10086, 0.548, 0.544),
    Reliability((0.6, 0.7), 7540, 0.646, 0.646),
    Reliability((0.7, 0.8), 4300, 0.744, 0.750),
    Reliability((0.8, 0.9), 1644, 0.842, 0.817),
    Reliability((0.9, 1.0), 370, 0.931, 0.835),
)

#: The same for the playoff odds: teams given X% made it Y%.
#: The run of 2026-09-25 (revision R6), the table ranked on category win
#: share; `PLAYOFF_RELIABILITY_BY_MATCHUPS` is the same run before R6.
PLAYOFF_RELIABILITY: tuple[Reliability, ...] = (
    Reliability((0.0, 0.1), 119, 0.020, 0.050),
    Reliability((0.1, 0.2), 43, 0.149, 0.209),
    Reliability((0.2, 0.3), 40, 0.248, 0.325),
    Reliability((0.3, 0.4), 36, 0.341, 0.333),
    Reliability((0.4, 0.5), 38, 0.461, 0.474),
    Reliability((0.5, 0.6), 29, 0.550, 0.517),
    Reliability((0.6, 0.7), 35, 0.651, 0.486),
    Reliability((0.7, 0.8), 23, 0.748, 0.609),
    Reliability((0.8, 0.9), 30, 0.855, 0.767),
    Reliability((0.9, 1.0), 139, 0.978, 1.000),
)

#: The playoff odds as published before revision R6, when the simulated table
#: was ranked by matchups won. Kept, like `WIDENED`, as the evidence the
#: revision is read against, not as anything the product prints.
PLAYOFF_RELIABILITY_BY_MATCHUPS: tuple[Reliability, ...] = (
    Reliability((0.0, 0.1), 112, 0.026, 0.134),
    Reliability((0.1, 0.2), 43, 0.147, 0.233),
    Reliability((0.2, 0.3), 39, 0.246, 0.205),
    Reliability((0.3, 0.4), 34, 0.343, 0.265),
    Reliability((0.4, 0.5), 38, 0.447, 0.368),
    Reliability((0.5, 0.6), 38, 0.546, 0.368),
    Reliability((0.6, 0.7), 40, 0.654, 0.575),
    Reliability((0.7, 0.8), 35, 0.754, 0.743),
    Reliability((0.8, 0.9), 34, 0.846, 0.824),
    Reliability((0.9, 1.0), 119, 0.979, 1.000),
)

#: Matchups called, both sides of each, and the share called right. A coin is
#: 0.500; the number is 0.688 for the week being played and about 0.55 from
#: five weeks out. Which side the forecast points at depends on neither the
#: width of the spread nor the injury report, and has barely moved through
#: either: 0.580, then 0.584, now 0.583.
WINNER_SAMPLE = 5320
WINNER_HIT_RATE = 0.583

#: Mean absolute error of a team's projected final category record, in
#: categories of the 171 a nineteen-week season contests, made at three marks.
RECORD_ERROR = {"quarter": 8.1, "half": 6.3, "three-quarter": 5.2}

#: The one line the pages put under the projected record, from
#: `RECORD_ERROR["half"]`. Kept as a written sentence rather than formatted at
#: runtime, so the words are reviewed with the number, and guarded by a test
#: that the number in it is the number above.
SHORT_NOTE = (
    "Made at the halfway mark of 2026, this projection's final records were off by "
    "6.3 categories on average."
)

#: Printed in full beside the table. No jargon, no verdict, and no claim the
#: run does not support.
CALIBRATION_NOTE = (
    "This is a forecast, and here is its record. Replaying the 2026 season from "
    "thirty-eight mornings and scoring every category it called about every week "
    "still to play -- 47,880 calls -- it lands about where it says it will: "
    "categories it gave a 15% chance were won 18% of the time, ones it gave 35% "
    "were won 35%, 65% were won 65%, and 85% were won 82%. It stays overconfident "
    "at the two extremes, which it reaches rarely: the calls it makes above 90% "
    "come in about 84% of the time. Read a number near the ends of the scale as a "
    "direction rather than a quantity. It is much better about the week in front of "
    "it than about the rest of the season: it named the right side of this week's "
    "matchup about 69% of the time, next week's about 64%, and a week five or more "
    "ahead about 55%, where a coin gives 50%. The record it projects holds up "
    "better than the chances behind it -- made at the halfway mark of 2026, each "
    "team's final category record was off by 6.3 of 171 on average, and by 5.2 with "
    "a quarter of the season left. The playoff odds, ranked on category win share "
    "as the league is, are trustworthy at the extremes and not in the upper middle: "
    "teams given better than 90% made it every time and teams given under 10% made "
    "it 5% of the time, but teams given 60-70% made it 49%. Read these numbers as one "
    "input to a decision that is yours."
)
