"""Feasibility tests.

The one that matters is that thirteen centres are refused. The rest pin the
matching down: a player covers one slot only, flex slots widen the pool, and
the answer does not depend on the order players are listed in.
"""

from app.draft.lineup import DEFAULT_LINEUP, can_field, uncovered_slots

GUARD = ["PG", "SG", "G", "UT", "BE", "IR"]
WING = ["SF", "SG", "G/F", "F", "UT", "BE", "IR"]
BIG = ["PF", "C", "PF/C", "F/C", "F", "UT", "BE", "IR"]
CENTRE = ["C", "PF/C", "F/C", "UT", "BE", "IR"]


def test_thirteen_centres_cannot_be_fielded() -> None:
    """The case that motivated all of this."""
    roster = {i: CENTRE for i in range(13)}

    assert can_field(roster) is False


def test_a_balanced_roster_can_be_fielded() -> None:
    roster = {
        1: GUARD,
        2: GUARD,
        3: GUARD,
        4: WING,
        5: WING,
        6: WING,
        7: BIG,
        8: BIG,
        9: CENTRE,
        10: CENTRE,
        11: GUARD,
        12: WING,
        13: BIG,
    }

    assert can_field(roster) is True


def test_exactly_enough_players_still_works() -> None:
    """Ten players for ten slots, each covering what nobody else can."""
    roster = {
        1: ["PG"],
        2: ["SG"],
        3: ["SF"],
        4: ["PF"],
        5: ["C"],
        6: ["G"],
        7: ["F"],
        8: ["UT"],
        9: ["UT"],
        10: ["UT"],
    }

    assert can_field(roster) is True


def test_one_player_cannot_cover_two_slots() -> None:
    """Eligible for everything, but there is only one of him."""
    everything = list(DEFAULT_LINEUP)
    roster = {1: everything}

    assert can_field(roster) is False


def test_a_flexible_player_is_moved_to_make_room() -> None:
    """The augmenting path: the flexible man shifts so the rigid one fits.

    Player 1 can play PG or SG; player 2 can only play PG. A greedy pass
    that seats player 1 at PG first would wrongly conclude there is no room
    for player 2. Matching moves player 1 to SG.
    """
    roster = {1: ["PG", "SG"], 2: ["PG"]}

    assert can_field(roster, lineup=("PG", "SG")) is True


def test_order_of_players_does_not_change_the_answer() -> None:
    forward = {1: ["PG", "SG"], 2: ["PG"]}
    backward = {2: ["PG"], 1: ["PG", "SG"]}

    assert can_field(forward, lineup=("PG", "SG")) == can_field(backward, lineup=("PG", "SG"))


def test_bench_and_ir_do_not_count_as_lineup_slots() -> None:
    roster = {i: ["BE", "IR"] for i in range(13)}

    assert can_field(roster) is False


def test_too_few_players_is_not_fieldable() -> None:
    assert can_field({1: GUARD, 2: BIG}) is False


def test_uncovered_slots_names_what_is_missing() -> None:
    roster = {i: CENTRE for i in range(13)}

    missing = uncovered_slots(roster)

    assert "PG" in missing and "SG" in missing and "SF" in missing
    assert "C" not in missing, "a centre fills the centre slot"


def test_a_lineup_is_built_from_the_league_settings() -> None:
    """Three utility places are three requirements, not one."""
    from app.draft.lineup import lineup_from_settings

    built = lineup_from_settings(
        {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3}
    )

    assert built == ("PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT")
    assert built.count("UT") == 3


def test_an_empty_settings_falls_back_rather_than_allowing_anything() -> None:
    from app.draft.lineup import DEFAULT_LINEUP, lineup_from_settings

    assert lineup_from_settings({}) == DEFAULT_LINEUP


def test_a_league_with_different_slots_gets_a_different_lineup() -> None:
    """The point of reading it: a setting that changes must be followed."""
    from app.draft.lineup import lineup_from_settings

    assert lineup_from_settings({"PG": 2, "UT": 1}) == ("PG", "PG", "UT")


def test_position_limits_count_primary_position() -> None:
    """This league caps centres, at three in 2026 and four in 2027."""
    from app.draft.lineup import within_position_limits

    assert within_position_limits(["C", "C", "C"], {"C": 3}) is True
    assert within_position_limits(["C", "C", "C", "C"], {"C": 3}) is False
    assert within_position_limits(["C", "C", "C", "C"], {"C": 4}) is True


def test_a_forward_eligible_at_centre_is_not_a_centre() -> None:
    """Counting eligibility instead of primary position would refuse legal rosters."""
    from app.draft.lineup import within_position_limits

    roster = ["C", "C", "C", "PF", "PF"]

    assert within_position_limits(roster, {"C": 3}) is True


def test_no_limits_means_no_constraint() -> None:
    from app.draft.lineup import within_position_limits

    assert within_position_limits(["C"] * 13, {}) is True


def test_an_unknown_position_is_not_counted_against_a_cap() -> None:
    from app.draft.lineup import within_position_limits

    assert within_position_limits(["C", None, None], {"C": 1}) is True
