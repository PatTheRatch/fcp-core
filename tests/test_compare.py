from app.draft.compare import overlap, player_frequency
from app.draft.optimizer import Candidate, RosterPlan


def plan(*names: str) -> RosterPlan:
    players = tuple(
        Candidate(player_id=hash(n) % 10_000, name=n, price=1, weekly={}) for n in names
    )
    return RosterPlan(
        players=players, cost=len(players), totals={}, win_probability={}, expected_wins=0.0
    )


def test_overlap_separates_shared_from_unique() -> None:
    result = overlap(plan("Luka", "Jokic", "Curry"), plan("Luka", "Jokic", "Wemby"))

    assert result.shared == ("Jokic", "Luka")
    assert result.only_first == ("Curry",)
    assert result.only_second == ("Wemby",)
    assert result.shared_fraction == 2 / 3


def test_identical_plans_share_everything() -> None:
    result = overlap(plan("A", "B"), plan("A", "B"))

    assert result.shared_fraction == 1.0
    assert result.only_first == () and result.only_second == ()


def test_disjoint_plans_share_nothing() -> None:
    assert overlap(plan("A"), plan("B")).shared_fraction == 0.0


def test_frequency_counts_appearances_across_plans() -> None:
    plans = [plan("Luka", "A"), plan("Luka", "B"), plan("Luka", "C"), plan("D")]

    counts = dict(player_frequency(plans))

    assert counts["Luka"] == 3
    assert counts["D"] == 1
    assert player_frequency(plans)[0] == ("Luka", 3), "most common first"
