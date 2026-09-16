"""Verdicts: the nine cells, the band edges, and the sentence."""

import pytest

from app.scoring.verdicts import NEUTRAL_BAND, Verdict, lens, signed, verdict

# Each cell of the 3x3 table: decision lens, result lens, the label the
# ticket fixes for it. One number per row, well clear of the band, so the
# label under test is the only thing these rows are pinning.
CELLS = [
    (0.40, 0.30, "Good call, and it paid off"),
    (0.40, 0.00, "Good call, no payoff yet"),
    (0.40, -0.10, "Good call, bad break"),
    (0.00, 0.30, "Lucky break"),
    (0.00, 0.00, "Wash"),
    (0.00, -0.10, "Unlucky"),
    (-0.30, 0.30, "Lucky break on a poor call"),
    (-0.30, 0.00, "Poor call, got away with it"),
    (-0.30, -0.10, "Poor call, and it cost you"),
]


@pytest.mark.parametrize(("decision", "result", "label"), CELLS)
def test_every_cell_of_the_table(decision: float, result: float, label: str) -> None:
    got = verdict(decision, result)
    assert got.label == label
    assert got.text.startswith(f"{label}: ")


@pytest.mark.parametrize(("decision", "result", "label"), CELLS)
def test_the_two_lenses_are_read_independently(decision: float, result: float, label: str) -> None:
    """A cell is decided by its own lens, not by which number is larger."""
    got = verdict(decision, result)
    assert got.good_decision is lens(decision)
    assert got.good_result is lens(result)
    assert got.decision == decision
    assert got.result == result


def test_decision_and_result_are_not_swapped() -> None:
    """The whole point of the ticket: expected and delivered are different."""
    good_break = verdict(0.40, -0.10)
    lucky_break = verdict(-0.10, 0.40)
    assert good_break.label == "Good call, bad break"
    assert lucky_break.label == "Lucky break on a poor call"
    assert good_break.text != lucky_break.text


def test_the_band_is_closed_at_both_ends() -> None:
    """Exactly the band is neutral; a hair beyond it is good."""
    assert verdict(NEUTRAL_BAND, 0.0).good_decision is None
    assert verdict(-NEUTRAL_BAND, 0.0).good_decision is None
    assert verdict(NEUTRAL_BAND + 0.01, 0.0).good_decision is True
    assert verdict(-NEUTRAL_BAND - 0.01, 0.0).good_decision is False


def test_the_band_applies_to_the_result_lens_too() -> None:
    assert verdict(0.0, NEUTRAL_BAND).label == "Wash"
    assert verdict(0.0, NEUTRAL_BAND + 0.01).label == "Lucky break"
    assert verdict(0.0, -NEUTRAL_BAND - 0.01).label == "Unlucky"


def test_the_band_is_smaller_than_a_move_worth_naming() -> None:
    """The band is a fraction of a typical pickup (0.062-0.128 a week)."""
    assert 0.0 < NEUTRAL_BAND < 0.062


def test_the_label_follows_the_band_and_not_the_rounded_number() -> None:
    """0.035 prints as "+0.04" but is good; 0.025 prints as "+0.03" but is not.

    That is the band working as intended, per the module docstring: the
    classification is made on the number, the sentence reports it rounded.
    """
    assert verdict(0.035, 0.0).good_decision is True
    assert verdict(0.025, 0.0).good_decision is None


def test_the_text_format() -> None:
    assert verdict(0.40, -0.10).text == (
        "Good call, bad break: +0.40 categories a week expected, -0.10 delivered."
    )
    assert verdict(0.0, 0.0).text == "Wash: +0.00 categories a week expected, +0.00 delivered."
    assert verdict(-0.30, -0.40).text == (
        "Poor call, and it cost you: -0.30 categories a week expected, -0.40 delivered."
    )


def test_the_units_are_named_once() -> None:
    assert verdict(0.40, -0.10).text.count("categories a week") == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.4, "+0.40"),
        (-0.1, "-0.10"),
        (0.0, "+0.00"),
        (-0.0, "+0.00"),
        # A lens that returns negative zero by arithmetic, not by literal.
        (-1e-17, "+0.00"),
        (0.005, "+0.01"),
        (-0.005, "-0.01"),
        (0.125, "+0.12"),  # banker's rounding, and it does not matter here
        (1.0, "+1.00"),
    ],
)
def test_numbers_are_signed_and_two_decimals(value: float, expected: str) -> None:
    assert signed(value) == expected


def test_a_negative_zero_never_reaches_the_text() -> None:
    """Why `signed` flattens: "-0.00" is read as a bug, not as zero."""
    got = verdict(-1e-17, -0.0)
    assert got.label == "Wash"
    assert "-0.00" not in got.text
    assert got.text == "Wash: +0.00 categories a week expected, +0.00 delivered."


def test_a_move_that_cost_you_says_so() -> None:
    """Either lens may be negative, and the label has to carry that."""
    assert verdict(-0.50, -0.20).label == "Poor call, and it cost you"
    assert verdict(0.50, -0.20).label == "Good call, bad break"


def test_the_verdict_is_a_value() -> None:
    got = verdict(0.40, -0.10)
    assert isinstance(got, Verdict)
    with pytest.raises(AttributeError):
        got.label = "nope"  # type: ignore[misc]
