"""The playoff lens: what a wait costs a team that has already won its place.

`lock_block` is arithmetic over the shipped return prior and nothing else, so
it can be checked by hand. Four things have to hold or the lens is lying.

**The two bounds really are bounds.** The measured stake sits between "the
seed is settled" and "the seed is wide open", and the second of those is the
regular-season reading's own pricing. A number outside its own bounds would be
a different formula.

**The dead days are the same dead days.** The slice before the playoffs and
the slice inside them add up to exactly what `app.pickups.returns` counts over
the whole horizon, so nothing is lost or double-charged at the seam.

**A free injured-reserve slot costs nothing**, the same gate the first reading
reads.

**Nothing here is a bar.** The block carries numbers and the odds beside them
and no verdict word at all.
"""

from dataclasses import replace

import pytest

from app.api.pickups import stash_out
from app.mcp import trim
from app.pickups.returns import expected_dead_days, probability_back_within
from app.pickups.stash import LOCK_ODDS, lock_block, stash_block

#: The league's measured value of a streamed place, as every other test uses.
OPENED = 0.38

#: A wait a manager would really be asked about: out a fortnight, two weeks to
#: the first playoff day, three playoff weeks on the other side.
DAYS_OUT = 14
TO_PLAYOFFS = 14
HORIZON = 35
PLAYOFF_WEEKS = 3.0
WORTH = 1.2


def block(**changes: float | bool | int):
    """The same lock with one thing changed, so a test reads as one line."""
    kwargs: dict[str, object] = {
        "playoff_odds": 0.98,
        "seeding_stake": 0.40,
        "days_out": DAYS_OUT,
        "horizon_days": HORIZON,
        "days_to_playoffs": TO_PLAYOFFS,
        "playoff_weeks": PLAYOFF_WEEKS,
        "playoff_weeks_value": WORTH,
        "opened": OPENED,
        "ir_slot_free": False,
    }
    kwargs.update(changes)
    return lock_block(**kwargs)  # type: ignore[arg-type]


def test_the_benefit_is_the_prior_times_what_he_is_worth_over_the_playoff_weeks() -> None:
    """One multiplication, and it is the shipped prior doing it."""
    lock = block()
    back = probability_back_within(DAYS_OUT, TO_PLAYOFFS)
    assert lock.back_by_playoffs == pytest.approx(back)
    assert 0.0 < back < 1.0
    assert lock.benefit == pytest.approx(back * WORTH)
    assert lock.playoff_weeks_value == pytest.approx(WORTH)


def test_a_settled_seed_makes_the_dead_regular_weeks_free() -> None:
    """The whole point of the lens: nothing left to play for, nothing to lose.

    With the stake at zero the only charge left is the dead weeks that fall
    **inside** the playoffs, which are not free to anybody.
    """
    lock = block(seeding_stake=0.0)
    assert lock.seeding_stake == 0.0
    assert lock.dead_regular_weeks > 0.0, "he is still expected to miss days"
    assert lock.cost == pytest.approx(OPENED * lock.dead_playoff_weeks)
    assert lock.lock_net == pytest.approx(lock.net_if_seed_settled)


def test_a_seed_wide_open_charges_every_dead_week_in_full() -> None:
    """The stake at one is the regular-season reading's own pricing."""
    lock = block(seeding_stake=1.0)
    assert lock.cost == pytest.approx(OPENED * (lock.dead_regular_weeks + lock.dead_playoff_weeks))
    assert lock.lock_net == pytest.approx(lock.net_if_seed_open)


def test_the_measured_number_sits_between_its_own_two_bounds() -> None:
    """A stake in the middle costs more than a settled seed and less than an
    open one, and the net runs the other way."""
    lock = block(seeding_stake=0.40)
    assert lock.net_if_seed_open < lock.lock_net < lock.net_if_seed_settled
    assert lock.net_if_seed_settled - lock.net_if_seed_open == pytest.approx(
        OPENED * lock.dead_regular_weeks
    )


def test_the_two_slices_of_dead_days_are_the_whole_horizon() -> None:
    """Nothing is lost or double-charged where the regular season ends."""
    lock = block()
    whole = expected_dead_days(HORIZON, days_out=DAYS_OUT) / 7.0
    assert lock.dead_regular_weeks + lock.dead_playoff_weeks == pytest.approx(whole)


def test_a_free_injured_reserve_slot_costs_nothing_at_all() -> None:
    """He sits on IR and the roster place goes on being streamed, so there is
    no dead place to charge for -- the same gate the first reading reads."""
    lock = block(ir_slot_free=True, seeding_stake=1.0)
    assert lock.dead_regular_weeks == 0.0
    assert lock.dead_playoff_weeks == 0.0
    assert lock.cost == 0.0
    assert lock.lock_net == pytest.approx(lock.benefit)
    assert lock.net_if_seed_settled == pytest.approx(lock.net_if_seed_open)


def test_the_playoffs_already_begun_makes_the_return_question_moot() -> None:
    """Past the first playoff day there is no "back by the playoffs" left to
    ask, and every dead day is a playoff dead day."""
    lock = block(days_to_playoffs=0, seeding_stake=1.0)
    assert lock.back_by_playoffs == 1.0
    assert lock.dead_regular_weeks == 0.0
    assert lock.dead_playoff_weeks > 0.0
    assert lock.benefit == pytest.approx(WORTH)


def test_the_stake_is_held_inside_nought_and_one() -> None:
    """A caller handing in a stake off the end of the scale gets the end."""
    assert block(seeding_stake=-0.5).seeding_stake == 0.0
    assert block(seeding_stake=4.0).seeding_stake == 1.0


def test_the_line_gives_the_odds_beside_every_number_and_no_verdict() -> None:
    """House style: the odds travel with the figure and nothing is labelled."""
    line = block().line
    assert "You are a lock (98%)" in line
    assert "the dead weeks cost your seeding" in line
    assert "back for the playoffs 53%" in line
    assert "over the 3 playoff weeks" in line
    assert "lock net" in line
    assert "-0.00" not in line, "a negative zero is rounding, not a loss"
    for verdict in ("worth a look", "recommend", "should", "avoid", "bad", "good"):
        assert verdict not in line.lower()


def test_the_language_says_what_the_reading_is_and_is_not() -> None:
    said = block().language
    assert "seeding" in said
    assert "not a second bar" in said
    assert "docs/stash_locks.md" in said


def test_the_gate_is_the_band_the_forecast_is_honest_in() -> None:
    """Declared, and nothing reads it but the caller that gates on it."""
    assert LOCK_ODDS == 0.95


def test_the_lock_survives_the_route_and_the_tool_unchanged() -> None:
    """Three layers, one set of numbers.

    The block, the route's schema and the co-manager's trim each hold the
    lock, and a number that changed on the way through would let a page and
    an assistant tell a manager two different stories about one morning.
    """
    lock = block()
    stash = replace(
        stash_block(
            player_id=1,
            name="Stashable",
            days_out=DAYS_OUT,
            horizon_days=HORIZON,
            opened=OPENED,
            ir_slot_free=False,
            net=0.5,
            late_net=0.1,
            expected_games=3.0,
            healthy_games=12,
        ),
        lock=lock,
    )
    said = stash_out(stash).model_dump(mode="json")
    assert said["lock"] is not None
    assert said["lock"]["lock_net"] == pytest.approx(lock.lock_net)
    assert said["lock"]["seeding_stake"] == pytest.approx(lock.seeding_stake)

    trimmed = trim.stash(said)
    assert trimmed is not None
    carried = trimmed["lock"]
    assert carried["playoff_odds"] == pytest.approx(lock.playoff_odds, abs=5e-3)
    assert carried["lock_net"] == pytest.approx(lock.lock_net, abs=5e-3)
    assert carried["net_if_seed_settled"] == pytest.approx(lock.net_if_seed_settled, abs=5e-3)
    assert carried["net_if_seed_open"] == pytest.approx(lock.net_if_seed_open, abs=5e-3)
    assert carried["one_line"] == lock.line

    # And a stash with no lock carries no lock key at all, which is every
    # ordinary morning.
    plain = stash_out(replace(stash, lock=None)).model_dump(mode="json")
    assert plain["lock"] is None
    assert "lock" not in (trim.stash(plain) or {})
