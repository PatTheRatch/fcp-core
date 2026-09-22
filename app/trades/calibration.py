"""What the trade evaluator has been measured at, as data and as a sentence.

The trade page prints `CALIBRATION_NOTE` verbatim under the number, because a
manager reading a forecast is owed the forecast's record beside it. The figures
below are the run of 2026-09-22 (`scripts/trade_calibration.py`, review_days=1,
every season the database can reconstruct), written up in `docs/trades.md`
section 7. Nothing here is computed at runtime: it is a published result, and
it changes only when the calibration is run again and the doc is rewritten
with it.

THE SHORT VERSION

Fifty-five deals, both sides of each, is the whole sample. On the window that
was declared as primary before the run -- the thirty days after the deal -- the
evaluator picked the side that did better in 25 of them, which is what it did
before revision R2 as well. A coin gives 20 to 35 of 55 nineteen times in
twenty, so that is a coin, and so is every other cell of the table.

What R2 did change is the one measured model defect. Consolidating deals -- two
men for one -- were over-rated by +0.389 categories a week, and the run says
that was mostly the two engines pricing an emptied roster place differently
rather than the forecast: with the forecast alone re-priced it is +0.404, and
with the hindsight yardstick re-priced beside it, +0.103. The sides where no
place opens are identical to the run before, to three decimals, which is the
check that nothing else moved.

What the evaluator is better at is men: what it says a player is worth a week
ranks at +0.39 against what he went on to do, over 174 of them, unchanged by
R2, and nearly all of that is lost when one side of a deal is subtracted from
the other. The category-by-category half of the report is not in this sentence
at all: it is the same week laid out one line at a time, and it does not depend
on the headline being right.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CALIBRATION_NOTE",
    "COIN_RANGE",
    "DEALS",
    "MEASURED_ON",
    "PLAYER_LEVEL_SAMPLE",
    "PLAYER_LEVEL_SPEARMAN",
    "PUBLISHED",
    "SEASONS",
    "SIDES",
    "UNEVEN_ERROR",
    "UNEVEN_SIDES",
    "WINDOW_DAYS",
    "Measured",
]


@dataclass(frozen=True)
class Measured:
    """One cell of the published table: a headline read against a horizon."""

    #: "the roster with-and-without" or "the per-man number".
    headline: str
    #: "next 30 days" or "rest of season".
    horizon: str
    #: Deals where the evaluator named the side that did better, of `DEALS`.
    picked: int
    #: Rank correlation over the sides, and mean error in categories a week.
    spearman: float
    mean_error: float
    #: How the hindsight grade settled a place the deal emptied: the streamed
    #: lane since revision R2, or the flat median pickup it charged before.
    #: The old-yardstick cells are published so that re-pricing the forecast
    #: and re-pricing the grade can be told apart (docs/trades.md section 7).
    yardstick: str = "the streamed lane"

    @property
    def share(self) -> float:
        return self.picked / DEALS if DEALS else 0.0


#: The day the published run was made.
MEASURED_ON = "2026-09-22"

#: Deals the database can both evaluate forward and grade in hindsight, and
#: the sides they make. Six seasons: 2019, 2021, 2023, 2024, 2025, 2026.
DEALS = 55
SIDES = 110
SEASONS = 6

#: Days after the deal the primary horizon covers.
WINDOW_DAYS = 30

#: What a fair coin gives in `DEALS` tosses, 95% of the time. Printed with
#: every hit rate, because 25 of 55 and 30 of 55 are the same number.
COIN_RANGE = (20, 35)

#: The 2x2 declared before the run, primary cell first, and under it the same
#: forecast read against the yardstick the hindsight grade used before R2.
PUBLISHED: tuple[Measured, ...] = (
    Measured("the roster with-and-without", "next 30 days", 25, -0.02, 0.008),
    Measured("the roster with-and-without", "rest of season", 31, 0.09, 0.013),
    Measured("the per-man number", "next 30 days", 19, -0.19, -0.065),
    Measured("the per-man number", "rest of season", 29, 0.02, -0.060),
    Measured("the roster with-and-without", "next 30 days", 25, 0.00, 0.077, "the old flat level"),
    Measured(
        "the roster with-and-without", "rest of season", 30, 0.09, 0.082, "the old flat level"
    ),
)

#: Sides of a deal where the two teams exchanged a different number of men, so
#: a roster place was emptied and had to be settled.
UNEVEN_SIDES = 25

#: The mean error on those sides over the primary horizon, in categories a
#: week: what R1 published on 2026-09-21, the same forecast re-priced by R2
#: against the old yardstick, and R2 against R2. The middle figure is the one
#: that says the over-rating was the settlement and not the forecast.
UNEVEN_ERROR = {
    "R1, the old yardstick": 0.389,
    "R2, the old yardstick": 0.404,
    "R2, the streamed lane": 0.103,
}

#: Men in the scored deals, and the rank correlation between what the
#: evaluator said each was worth a week and what his real box scores were
#: worth over the same thirty days. The projections carry signal about a
#: player; the difference between two of them, on two rosters, does not.
PLAYER_LEVEL_SAMPLE = 174
PLAYER_LEVEL_SPEARMAN = 0.39

#: Printed verbatim on the trade page, under the number. No jargon, no
#: verdict, and no claim the run does not support.
CALIBRATION_NOTE = (
    "This number is a forecast, and here is its record. Over the 55 trades in this "
    "league's history that can be replayed, it pointed at the side that did better in "
    "25 of them, judged on the thirty days after the deal; a coin lands between 20 and "
    "35 of 55 nineteen times in twenty, so on past evidence this number is not better "
    "than a coin at picking the winner of a trade. It is better at players than at "
    "deals: what it says a man is worth a week lines up reasonably well with what he "
    "goes on to do, and most of that agreement is lost when one side of a deal is "
    "subtracted from the other. One thing did get better. On deals that send two men "
    "for one, this number used to run about four tenths of a category a week above "
    "what those deals really did; most of that turned out to be the empty roster "
    "place being priced as an ordinary waiver pickup at both ends, and now that it is "
    "priced at what a streamed place really returns the gap is about a tenth. The "
    "category table beside it is a different matter: it is what your roster posts in a "
    "week with the deal and without it, laid out one category at a time, and it does "
    "not depend on this number being right. Read the number as one input to a "
    "conversation."
)
