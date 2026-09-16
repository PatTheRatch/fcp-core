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

A lens within `NEUTRAL_BAND` categories a week of zero is neutral. The scale
comes from `app.scoring.replacement`: a typical waiver pickup adds 0.06-0.13
categories a week (the median by season, 2019-2026), with the middle half of
pickups spread across roughly 0.01 to 0.30. `NEUTRAL_BAND` is 0.05, about
half of a typical pickup: a move worth less than half of the most ordinary
move in the league is not worth calling good or bad.

The band is a size, not a share, so one number means the same thing to every
reader. It is sized for moves on the wire and in trades of one or two
players. A draft pick's value is several times larger, so callers grading
bigger things pass their own `band` rather than tuning this one.

FORMATTING

Two decimals, always signed, and "categories a week" written once, after the
first number: "Good call, bad break: +0.40 categories a week expected, -0.10
delivered." The unit is on the decision number because that is the one a
reader meets first; repeating it reads as noise, and a bare "-0.10" a clause
later cannot be mistaken for a different quantity.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Categories a week either lens may sit from zero and still read as neutral:
#: about half of a typical pickup's median value (0.06-0.13 by season,
#: `app.scoring.replacement`). See the module docstring.
NEUTRAL_BAND = 0.05

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


def lens(value: float, band: float = NEUTRAL_BAND) -> bool | None:
    """Where one lens sits: good, neutral or bad.

    The band is closed: exactly `band` is neutral, just beyond it is good.
    """
    if value > band:
        return True
    if value < -band:
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


def verdict(decision: float, result: float, *, band: float = NEUTRAL_BAND) -> Verdict:
    """Grade a move on both lenses: `decision` expected, `result` delivered.

    Both are categories a week and either may be negative, because a move can
    cost you. Which lens is which is not a detail: a good call that lost is
    "Good call, bad break" and a poor call that won is "Lucky break on a poor
    call", and both facts have to survive to the sentence.
    """
    good_decision = lens(decision, band)
    good_result = lens(result, band)
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
