"""The ranking rule: one order per scoring type, and ESPN's own order reproduced."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.models import LeagueSeason
from app.scoring.ranking import (
    CATEGORIES,
    CATEGORY_WORDS,
    MATCHUP_WORDS,
    MATCHUPS,
    Record,
    lookup,
    ranking_rule,
)

FIXTURE = Path(__file__).parent / "fixtures" / "espn_standings.json"


def season(scoring: str, seeding: str | None = "H2H_RECORD") -> LeagueSeason:
    schedule = {"playoffSeedingRule": seeding} if seeding is not None else {}
    return LeagueSeason(
        scoring_type=scoring,
        raw_settings={"scoring": {"scoringType": scoring}, "schedule": schedule},
    )


def teams(records: list[Record]) -> list[int]:
    return [record.team for record in records]


# ---------------------------------------------------------------------------
# which rule
# ---------------------------------------------------------------------------


def test_each_category_is_ranked_on_categories() -> None:
    rule = ranking_rule(season("H2H_CATEGORY"))
    assert rule.unit == CATEGORIES
    assert rule.head_to_head is True
    assert rule.words == CATEGORY_WORDS
    assert rule.words.startswith("category win share")


def test_most_categories_and_points_are_ranked_on_matchups() -> None:
    for scoring in ("H2H_MOST_CATEGORIES", "H2H_POINTS"):
        rule = ranking_rule(season(scoring))
        assert rule.unit == MATCHUPS
        assert rule.words == MATCHUP_WORDS


def test_roto_and_the_unnamed_are_refused_with_a_reason() -> None:
    for scoring in ("ROTO", "SOMETHING_NEW", ""):
        with pytest.raises(ValueError, match="rotisserie is out of scope"):
            ranking_rule(season(scoring))


def test_the_scoring_type_falls_back_to_espn_s_own_settings() -> None:
    blank = LeagueSeason(scoring_type="", raw_settings={"scoring": {"scoringType": "H2H_CATEGORY"}})
    assert ranking_rule(blank).unit == CATEGORIES


def test_a_seeding_tiebreaker_that_is_not_head_to_head_drops_that_term() -> None:
    rule = ranking_rule(season("H2H_CATEGORY", seeding="TOTAL_POINTS_SCORED"))
    assert rule.head_to_head is False
    assert "against each other" not in rule.words
    # An unset setting is ESPN's default, which is head to head.
    assert ranking_rule(season("H2H_CATEGORY", seeding=None)).head_to_head is True


# ---------------------------------------------------------------------------
# the category order
# ---------------------------------------------------------------------------


def test_share_decides_not_matchups() -> None:
    rule = ranking_rule(season("H2H_CATEGORY"))
    # A: more matchups, lower share. B: fewer matchups, better share.
    a = Record(1, 90, 81, 0, matchups_won=12, matchups_lost=7)
    b = Record(2, 95, 76, 0, matchups_won=9, matchups_lost=10)
    assert teams(rule.order([a, b])) == [2, 1]
    assert a.share == pytest.approx(90 / 171)


def test_a_tie_counts_half_in_the_share() -> None:
    rule = ranking_rule(season("H2H_CATEGORY"))
    # 86-75-1 and 85-74-3 are both 86.5 of 162.
    assert Record(1, 86, 75, 1).share == Record(2, 85, 74, 3).share
    # 88-74-0 is 88 of 162, ahead of both.
    order = rule.order([Record(1, 86, 75, 1), Record(3, 88, 74, 0), Record(2, 85, 74, 3)])
    assert teams(order)[0] == 3


def test_a_tie_of_share_goes_head_to_head_before_categories_won() -> None:
    # 2021: Draped Up (85-74-3) beat Bone's Boyos (86-75-1) 11-7 in their
    # meetings and ESPN put Draped Up fourth, Bone's fifth.
    rule = ranking_rule(season("H2H_CATEGORY"))
    draped, bones = Record(1, 85, 74, 3), Record(2, 86, 75, 1)
    meetings = lookup({(1, 2): (11, 7, 0), (2, 1): (7, 11, 0)})
    assert teams(rule.order([bones, draped], meetings)) == [1, 2]
    # Without the meetings, categories won decides it the other way.
    assert teams(rule.order([draped, bones])) == [2, 1]


def test_a_tie_with_no_meetings_falls_to_categories_won_then_fewest_lost() -> None:
    rule = ranking_rule(season("H2H_CATEGORY"))
    meetings = lookup({})
    a, b = Record(1, 94, 75, 2), Record(2, 95, 76, 0)  # both 95 of 171
    assert teams(rule.order([a, b], meetings)) == [2, 1]
    c, d = Record(3, 90, 80, 0), Record(4, 90, 80, 0)
    # A complete tie keeps the order it came in, and a draw breaks it.
    assert teams(rule.order([d, c], meetings)) == [4, 3]
    assert teams(rule.order([d, c], meetings, draw=lambda team: team)) == [3, 4]


def test_a_three_way_tie_reads_each_team_s_record_against_the_other_two() -> None:
    rule = ranking_rule(season("H2H_CATEGORY"))
    level = [Record(1, 50, 40, 0), Record(2, 50, 40, 0), Record(3, 50, 40, 0), Record(4, 60, 30, 0)]
    meetings = lookup(
        {
            (1, 2): (5, 4, 0),
            (2, 1): (4, 5, 0),
            (1, 3): (3, 6, 0),
            (3, 1): (6, 3, 0),
            (2, 3): (7, 2, 0),
            (3, 2): (2, 7, 0),
        }
    )
    # Against the other two: 1 is 8-10, 2 is 11-7, 3 is 8-10; 1 and 3 level
    # again, so categories won and lost (equal) leave them as they came.
    assert teams(rule.order(level, meetings)) == [4, 2, 1, 3]


def test_the_head_to_head_term_is_skipped_where_the_league_does_not_use_it() -> None:
    rule = ranking_rule(season("H2H_CATEGORY", seeding="TOTAL_POINTS_SCORED"))
    draped, bones = Record(1, 85, 74, 3), Record(2, 86, 75, 1)
    meetings = lookup({(1, 2): (11, 7, 0), (2, 1): (7, 11, 0)})
    assert teams(rule.order([draped, bones], meetings)) == [2, 1]


def test_nothing_decided_yet_is_an_order_that_keeps_its_input() -> None:
    rule = ranking_rule(season("H2H_CATEGORY"))
    assert Record(1, 0, 0, 0).share is None
    assert teams(rule.order([Record(3, 0, 0, 0), Record(1, 0, 0, 0)])) == [3, 1]


# ---------------------------------------------------------------------------
# the matchup order, unchanged
# ---------------------------------------------------------------------------


def test_the_matchup_order_is_the_old_one() -> None:
    rule = ranking_rule(season("H2H_MOST_CATEGORIES"))
    a = Record(1, 90, 81, 0, matchups_won=12, matchups_lost=7)
    b = Record(2, 95, 76, 0, matchups_won=9, matchups_lost=10)
    c = Record(3, 99, 72, 0, matchups_won=12, matchups_lost=7)
    # Matchups won, then fewest lost, then categories won; meetings ignored.
    meetings = lookup({(1, 3): (9, 0, 0), (3, 1): (0, 9, 0)})
    assert teams(rule.order([a, b, c], meetings)) == [3, 1, 2]


# ---------------------------------------------------------------------------
# the measurement: ESPN's own order, every stored season
# ---------------------------------------------------------------------------


def _espn_seasons() -> dict[str, dict]:
    return json.loads(FIXTURE.read_text())["seasons"]


@pytest.mark.parametrize("year", sorted(_espn_seasons()))
def test_the_rule_is_espn_s_order_in_every_stored_season_but_2023_s_second_and_third(
    year: str,
) -> None:
    """docs/projected_record.md, revision R6: measured on the dev database.

    The only difference is 2023's places 2 and 3, which are not a tie of
    share: 2023 had two divisions and ESPN seeds the division leaders first,
    which the rule does not model.
    """
    stored = _espn_seasons()[year]
    rule = ranking_rule(season(stored["scoring_type"], stored["seeding_rule"]))
    records = [
        Record(team["espn_team_id"], *team["categories"])
        # In ESPN team id order, so no test passes by inheriting ESPN's order.
        for team in sorted(stored["teams"], key=lambda team: team["espn_team_id"])
    ]
    meetings = lookup({(a, b): (w, lo, t) for a, b, w, lo, t in stored["meetings"]})
    ours = teams(rule.order(records, meetings))
    espn = [team["espn_team_id"] for team in sorted(stored["teams"], key=lambda t: t["standing"])]
    differs = [place for place, (a, b) in enumerate(zip(ours, espn, strict=True), 1) if a != b]
    if year == "2023":
        assert differs == [2, 3]
        names = {team["espn_team_id"]: team for team in stored["teams"]}
        second = names[espn[1]]
        # ESPN's second is the USA division's leader; by share it is third.
        assert second["division"] == "USA"
        assert ours[2] == espn[1]
    else:
        assert differs == []


def test_categories_won_alone_would_not_have_reproduced_espn() -> None:
    """Why the head-to-head term is in the rule: without it, 2021, 2024 and
    2025 each differ from ESPN at a tie of share."""
    wrong = []
    for year, stored in sorted(_espn_seasons().items()):
        rule = ranking_rule(season(stored["scoring_type"], stored["seeding_rule"]))
        records = [
            Record(team["espn_team_id"], *team["categories"])
            for team in sorted(stored["teams"], key=lambda team: team["espn_team_id"])
        ]
        ours = teams(rule.order(records))  # no meetings: share, categories won, fewest lost
        espn = [t["espn_team_id"] for t in sorted(stored["teams"], key=lambda t: t["standing"])]
        if year != "2023" and ours != espn:
            wrong.append(year)
    assert "2021" in wrong and "2024" in wrong
