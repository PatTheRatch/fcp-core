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

WHOSE RECORD IT IS

`CALIBRATION_NOTE` below is the constant this league's page printed before
the numbers moved into `league_calibrations` (docs/intake.md). It is no
longer the only note there can be: `trade_note` writes the same sentence from
a run's own figures, so a second league's page carries a record of its own
trades rather than of ours. `trade_note` applied to the run below returns
`CALIBRATION_NOTE` character for character, which is what
`tests/test_trades.py` holds it to; that is the whole reason the constant is
still here.
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
    "trade_note",
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


# ---------------------------------------------------------------------------
# the same sentence, from any run's own numbers
# ---------------------------------------------------------------------------

#: Small whole numbers in words, for the horizon. A note a manager reads says
#: "the thirty days after the deal", not "the 30 days after the deal".
_NUMBERS = {7: "seven", 14: "fourteen", 21: "twenty-one", 30: "thirty", 60: "sixty"}

#: Tenths of a category, in words. The note is deliberately vague about a
#: quantity it only knows to about a tenth, and printing "0.389" would claim
#: three digits of precision that twenty-five sides cannot carry.
_TENTHS = {
    0: "under a tenth",
    1: "a tenth",
    2: "two tenths",
    3: "three tenths",
    4: "four tenths",
    5: "half",
    6: "six tenths",
    7: "seven tenths",
    8: "eight tenths",
    9: "nine tenths",
    10: "a whole category",
}


def _words(number: int) -> str:
    return _NUMBERS.get(number, str(number))


def _tenths(value: float) -> str:
    """A size in categories a week, said the way a person would say it."""
    steps = round(abs(value) * 10)
    if steps in _TENTHS:
        return _TENTHS[steps]
    return f"{steps / 10:.1f} categories"


def trade_note(
    *,
    deals: int,
    picked: int,
    coin_range: tuple[int, int],
    uneven_now: float,
    window_days: int = WINDOW_DAYS,
    uneven_before: float | None = None,
    whose: str = "this league's history",
) -> str:
    """The sentence the trade page prints under the number, for one run.

    Every figure in it comes from the run: how many deals could be replayed,
    how many the evaluator called right, what a fair coin would give over the
    same number of tosses (`coin_interval` in `scripts/trade_calibration.py`),
    and what a consolidating deal is mis-priced by. Nothing is claimed that
    the numbers do not support, and the verdict against the coin is read off
    the interval rather than asserted: a league where the evaluator lands
    outside the coin's range is told so.

    `uneven_before` is the same figure from an earlier revision, when there is
    one. Our own run has one -- R1's 0.389 against R2's 0.103, the one
    measured defect R2 fixed -- so that clause is in our note; a league
    measured once has no "before", and its note says only where it stands.

    Applied to the published run this returns `CALIBRATION_NOTE` exactly,
    which `tests/test_trades.py` holds it to, so this league's page cannot
    move by a character.
    """
    low, high = coin_range
    if picked > high:
        verdict = (
            "so on past evidence this number does better than a coin at picking the "
            "winner of a trade"
        )
    elif picked < low:
        verdict = (
            "so on past evidence this number does worse than a coin at picking the "
            "winner of a trade, which is a reason to distrust it"
        )
    else:
        verdict = (
            "so on past evidence this number is not better than a coin at picking the "
            "winner of a trade"
        )
    if uneven_before is None:
        consolidating = (
            "On deals that send two men for one, where a roster place is left open, "
            f"this number runs about {_tenths(uneven_now)} of a category a week away "
            "from what those deals really did."
        )
    else:
        consolidating = (
            "One thing did get better. On deals that send two men for one, this number "
            f"used to run about {_tenths(uneven_before)} of a category a week above "
            "what those deals really did; most of that turned out to be the empty "
            "roster place being priced as an ordinary waiver pickup at both ends, and "
            "now that it is priced at what a streamed place really returns the gap is "
            f"about {_tenths(uneven_now)}."
        )
    return (
        f"This number is a forecast, and here is its record. Over the {deals} trades in "
        f"{whose} that can be replayed, it pointed at the side that did better in "
        f"{picked} of them, judged on the {_words(window_days)} days after the deal; a "
        f"coin lands between {low} and {high} of {deals} nineteen times in twenty, "
        f"{verdict}. It is better at players than at deals: what it says a man is "
        "worth a week lines up reasonably well with what he goes on to do, and most of "
        "that agreement is lost when one side of a deal is subtracted from the other. "
        f"{consolidating} The category table beside it is a different matter: it is "
        "what your roster posts in a week with the deal and without it, laid out one "
        "category at a time, and it does not depend on this number being right. Read "
        "the number as one input to a conversation."
    )
