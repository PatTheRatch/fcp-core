"""The words a grade gets: what it was expected to do, and what it did.

The spec's rule is two lenses, decision first. A move is graded on what was
knowable when it was made (`decision`) and on what it delivered (`result`),
both in the package's currency, categories a week (`app.scoring.replacement`),
and where the two disagree the verdict has to say so -- "good call, bad break",
"lucky break" -- rather than let one number stand for both. A manager whose
sound process lost to a cold week and a manager who got away with a poor one
are different, and a single +/- number cannot tell them apart.

So this module is only the classification and the sentence. It does not
compute either lens: S8 (recent form) and S7/S9/S11/S12 (what was delivered)
do that, and every caller hands the results here. That keeps the labels in
one place, and it means no grade can quietly disagree with the others about
what counts as neutral.

WHAT COUNTS AS NEUTRAL

Both lenses are money, so the band has to be in categories a week and must
not swallow a real one. `NEUTRAL_BAND` is set from the measured spread of a
typical waiver add, in `app.scoring.replacement` (medians 0.062 to 0.128
across the eight seasons 2019-2026, interquartile ranges 0.012 to 0.034).

0.03 is the top of those interquartile ranges, and about a third of a typical
pickup. That is the argument for it rather than a half of anything. Half of a
pickup's *median*, the ticket's suggestion of 0.05, is also roughly the whole
interquartile range of a pickup, so it would call most of the spread between
one add and the next neutral -- and a starting pitcher's streamer move, which
is the smallest thing the wire grades ever read, would be neutral far more
often than not for no reason the data supports. At 0.03 a move has to clear
the ordinary week-to-week noise of an add to be called anything at all: a
+/- 0.02 is nobody's victory.

Why the floor is a size and not a share. The result lens is measured against
a whole team's season, and the decision lens against one player's expected
line, so the two have different denominators; a share of either would mean
the threshold moves with the thing it is judging, and the same verdict would
need a different band per caller. One absolute size, named once, is the only
version a reader can hold in their head.

A caveat worth writing down rather than solving here, because S14 decides it:
this band is the size of a wire move. A draft grade's numbers are bigger --
a first round pick is worth several categories a week -- so a miss of 0.03
there is not neutral in the way it is here. If that turns out to matter, the
band belongs to the caller and `verdict` should take it as a parameter
defaulting to `NEUTRAL_BAND`, rather than one number being tuned to satisfy
two scales.

FORMATTING

Two decimals, always signed, and "categories a week" written once, after the
first number: "Good call, bad break: +0.40 categories a week expected, -0.10
delivered." The unit is on the decision number because that is the one a
reader meets first; repeating it reads as noise, and a bare "-0.10" a clause
later cannot be mistaken for a different quantity.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Categories a week either lens may sit from zero and still read as neutral.
#: The top of the interquartile range a waiver add shows in every one of the
#: eight measured seasons (0.034 in 2020, the widest), and about a third of a
#: typical pickup's median (0.062-0.128). See the module docstring.
NEUTRAL_BAND = 0.03

#: The nine cells, decision rows by result columns. Every word a verdict can
#: say lives here so no call site invents its own phrasing.
LABELS: dict[tuple[bool | None, bool | None], str] = {
    (True, True): "Good call, and it paid off",
    (True, None): "Good call, no payoff yet",
    (True, False): "Good call, bad break",
    (None, True): "Lucky break",
    (None, None): "Wash",
    (None, False): "Unlucky",
    (False, True): "Lucky break on a poor call",
    (False, None): "Poor call, got away with it",
    (False, False): "Poor call, and it cost you",
}


@dataclass(frozen=True)
class Verdict:
    """One grade, both lenses: what was expected and what was delivered."""

    #: A short plain-language verdict, e.g. "Good call, bad break".
    label: str
    #: The label, then the numbers: what the presentation layer shows.
    text: str
    #: Categories a week expected on what was knowable at the time.
    decision: float
    #: Categories a week delivered.
    result: float
    #: Whether each lens cleared the band; None when it was inside it.
    good_decision: bool | None
    good_result: bool | None


def lens(value: float) -> bool | None:
    """Where one lens sits: good, neutral or bad, against `NEUTRAL_BAND`.

    The band is closed, so a decision exactly `NEUTRAL_BAND` categories a
    week is neutral and one just beyond it is good. The strict comparison is
    what keeps a rounded -0.03 from reading as a loss, which matters because
    the printed numbers are rounded to two decimals and 0.03 is not.
    """
    if value > NEUTRAL_BAND:
        return True
    if value < -NEUTRAL_BAND:
        return False
    return None


def signed(value: float) -> str:
    """A number in categories a week, signed and to two decimals.

    Micro-negatives are flattened first: a lens that returns -1e-17 reads as
    zero, and would otherwise print the "-0.00" that every reader takes for a
    formatting bug.
    """
    rounded = round(value, 2)
    return f"{rounded + 0.0:+.2f}"  # the + 0.0 turns a rounded -0.0 back into zero


def verdict(decision: float, result: float) -> Verdict:
    """Grade a move on both lenses: `decision` expected, `result` delivered.

    Both are categories a week and either may be negative, because a move can
    cost you. Which lens is which is not a detail: a good call that lost is
    "Good call, bad break" and a poor call that won is "Lucky break on a poor
    call", and both facts have to survive to the sentence.
    """
    good_decision = lens(decision)
    good_result = lens(result)
    label = LABELS[(good_decision, good_result)]
    text = f"{label}: {signed(decision)} categories a week expected, {signed(result)} delivered."
    return Verdict(
        label=label,
        text=text,
        decision=decision,
        result=result,
        good_decision=good_decision,
        good_result=good_result,
    )
