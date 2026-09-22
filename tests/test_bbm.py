"""Matching Basketball Monster names to ours.

The loader used the feed's typing matcher, which is loose on purpose, and
it put Caleb Wilson's projection on Jalen Wilson. These pin the stricter
rule: the same name, or the same surname with a short first name.
"""

from app.draft.bbm import split_list, split_note
from app.player_names import match_player, name_key, synthetic_id

KNOWN = {
    1: "Jalen Wilson",
    2: "OG Anunoby",
    3: "Cameron Johnson",
    4: "Ronald Holland II",
    5: "LeBron James",
    6: "Keldon Johnson",
    7: "Tre Johnson",
    8: "Tre Johnson",
}
RECENCY = {1: 2026, 2: 2026, 3: 2026, 4: 2026, 5: 2026, 6: 2026, 7: 2026, 8: 2026}


def test_punctuation_and_suffixes_are_not_part_of_a_name() -> None:
    assert name_key("O.G. Anunoby") == name_key("OG Anunoby")
    assert name_key("Ronald Holland II") == "ronald holland"
    assert name_key("Ja'Kobe Walter") == "jakobe walter"


def test_a_similar_name_is_not_the_same_player() -> None:
    assert match_player("Caleb Wilson", KNOWN, RECENCY) is None
    assert match_player("Bronny James", KNOWN, RECENCY) is None
    assert match_player("Keshad Johnson", KNOWN, RECENCY) is None


def test_the_same_name_matches_and_a_short_first_name_does_too() -> None:
    assert match_player("O.G. Anunoby", KNOWN, RECENCY) == 2
    assert match_player("Cam Johnson", KNOWN, RECENCY) == 3
    assert match_player("Ron Holland", KNOWN, RECENCY) == 4


def test_two_players_with_one_name_are_told_apart_by_recency_or_not_at_all() -> None:
    assert match_player("Tre Johnson", KNOWN, RECENCY) is None
    assert match_player("Tre Johnson", KNOWN, {**RECENCY, 8: 2019}) == 7


def test_an_unmatched_player_gets_a_stable_negative_id() -> None:
    assert synthetic_id("Cameron Boozer") < 0
    assert synthetic_id("Cameron Boozer") == synthetic_id("cameron boozer")
    assert synthetic_id("Cameron Boozer") != synthetic_id("AJ Dybantsa")


def test_a_note_is_split_from_its_author() -> None:
    assert split_note("[Josh: Starts at centre; blocks are elite.]") == (
        "Josh",
        "Starts at centre; blocks are elite.",
    )
    assert split_note("") == ("", "")
    assert split_note("No brackets here") == ("", "No brackets here")


def test_bbm_lists_split_on_pipes_and_drop_trailing_detail() -> None:
    assert split_list("New Team|Player Option|New Contract - singing with minnesota") == (
        "New Team",
        "Player Option",
        "New Contract",
    )
    assert split_list("Breakout Candidate|Breakout Candidate") == ("Breakout Candidate",)
    assert split_list("") == ()


def test_the_2027_export_carries_confidence_tags_and_notes() -> None:
    from pathlib import Path

    import pytest

    export = Path("data/bbm/BBM_Projections_2027_total.xls")
    if not export.exists():
        pytest.skip("BBM export not present")
    from app.draft.bbm import read_bbm

    rows = {r.name: r for r in read_bbm(export)}
    ware = rows["Kel'el Ware"]
    assert ware.confidence == 3
    assert "Position Battle" in ware.tags
    assert ware.note_by == "Josh" and ware.note
