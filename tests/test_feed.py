"""Parsing the draft page.

The fixtures are text captured from the mock draft on 2026-09-13, plus the
pick-log shapes seen rendered there. The parser is pure, so this is the
whole test of it: what the page said, and what the room should conclude.
"""

import pytest

from app.draft.feed import (
    BoardSnapshot,
    LoggedPick,
    OnBlock,
    TickerRow,
    budget_drops,
    inferred_picks,
    match_name,
    match_team,
    new_picks,
    parse_board,
)
from app.player_names import normalise

TEAMS = [
    "Fast and Curryous",
    "Foxes ShutUpNDribble",
    "Brighton Bears",
    "LeBron's Load Management LLC",
    "Through The Wire",
    "Thibs Dust",
    "Ben's Need Some VC",
    "Brockley Heat",
]
PLAYERS = ["Cooper Flagg", "Luka Doncic", "Victor Wembanyama", "Nikola Jokic", "Jalen Johnson"]

#: Captured verbatim from the mock, trimmed to the rows the tests need.
TICKER = """\
1. Fast and Curryous $200AUTO $null
2. Foxes ShutUpNDribble $103AUTO $null
3. Brighton Bears $18AUTO $null
12. LeBron's Load Management LLC $109AUTO $19
14. Through The Wire $200 $null
15. Ben's Need Some VC $92AUTO $null
"""
CARD = (
    "Cooper FlaggDALSF, PF 2026 STATS: 70 GP, 21.0 PTS, 6.7 REB, .468 FG% "
    "2027 PROJECTED: -- GP PRE-DRAFT VAL: $67\n"
)
OFFER = "CURRENT OFFER: $72\nOFFER $73\nMANUAL OFFER (MAX $188)\nPK 12 OF 195\n"
LOG_ONE_LINE = "1 Luka Doncic Thibs Dust $94\n2 Victor Wembanyama Ben's Need Some VC $108\n"
LOG_STACKED = "1\nLuka Doncic\nThibs Dust\n$94\n2\nVictor Wembanyama\nBen's Need Some VC\n$108\n"


def test_the_ticker_reads_money_autopilot_and_the_live_bid() -> None:
    board = parse_board(TICKER, TEAMS)
    rows = {row.team: row for row in board.ticker}
    assert len(rows) == 6
    assert rows["Brighton Bears"] == TickerRow("Brighton Bears", 18, None, True)
    assert rows["Through The Wire"] == TickerRow("Through The Wire", 200, None, False)
    assert rows["LeBron's Load Management LLC"].bid == 19, "the one team bidding shows its bid"
    assert board.budgets["Foxes ShutUpNDribble"] == 103


def test_the_card_names_the_player_and_the_offer() -> None:
    board = parse_board(TICKER + CARD + OFFER, TEAMS, PLAYERS)
    assert board.on_block == OnBlock("Cooper Flagg", 72, "LeBron's Load Management LLC", 67)
    assert board.pick_number == 12
    assert board.total_picks == 195
    assert board.in_progress


def test_an_unknown_player_on_the_card_is_parsed_by_shape() -> None:
    board = parse_board("Kon KnueppelCHASG, SF 2026 STATS: 60 GP PRE-DRAFT VAL: $3\n", TEAMS)
    assert board.on_block is not None
    assert board.on_block.player == "Kon Knueppel"
    assert board.on_block.espn_value == 3


@pytest.mark.parametrize("log", [LOG_ONE_LINE, LOG_STACKED])
def test_the_pick_log_is_read_in_either_shape(log: str) -> None:
    board = parse_board(log, TEAMS)
    assert board.picks == (
        LoggedPick(1, "Luka Doncic", "Thibs Dust", 94),
        LoggedPick(2, "Victor Wembanyama", "Ben's Need Some VC", 108),
    )


def test_the_longest_team_name_splits_the_pick() -> None:
    """`Heat` must not steal the tail of `Brockley Heat`."""
    board = parse_board("3 Nikola Jokic Brockley Heat $97\n", [*TEAMS, "Heat"])
    assert board.picks[0].player == "Nikola Jokic"
    assert board.picks[0].team == "Brockley Heat"


def test_a_page_with_nothing_on_it_is_an_empty_snapshot() -> None:
    board = parse_board("ESPN - Serving Sports Fans.\n", TEAMS)
    assert board == BoardSnapshot((), (), None, None, None)
    assert not board.in_progress


def test_new_picks_are_the_ones_not_seen_before() -> None:
    before = parse_board(LOG_ONE_LINE, TEAMS)
    after = parse_board(LOG_ONE_LINE + "3 Nikola Jokic Foxes ShutUpNDribble $97\n", TEAMS)
    assert new_picks(None, before) == list(before.picks)
    assert new_picks(before, after) == [LoggedPick(3, "Nikola Jokic", "Foxes ShutUpNDribble", 97)]
    assert new_picks(after, after) == []


def test_a_budget_drop_with_no_logged_pick_is_offered_as_an_inference() -> None:
    before = parse_board(TICKER + CARD + OFFER, TEAMS, PLAYERS)
    later = TICKER.replace(
        "12. LeBron's Load Management LLC $109AUTO $19",
        "12. LeBron's Load Management LLC $37AUTO $null",
    )
    after = parse_board(later, TEAMS, PLAYERS)
    assert budget_drops(before, after) == {"LeBron's Load Management LLC": 72}
    inferred = inferred_picks(before, after)
    assert inferred == [LoggedPick(12, "Cooper Flagg", "LeBron's Load Management LLC", 72)]


def test_two_simultaneous_drops_cannot_be_attributed() -> None:
    before = parse_board(TICKER + CARD + OFFER, TEAMS, PLAYERS)
    later = TICKER.replace("$109AUTO $19", "$37AUTO $null").replace("$18AUTO", "$10AUTO")
    assert inferred_picks(before, parse_board(later, TEAMS, PLAYERS)) == []


def test_a_logged_pick_pre_empts_inference() -> None:
    before = parse_board(TICKER + CARD + OFFER, TEAMS, PLAYERS)
    later = (
        TICKER.replace("$109AUTO $19", "$37AUTO $null")
        + "12 Cooper Flagg LeBron's Load Management LLC $72\n"
    )
    assert inferred_picks(before, parse_board(later, TEAMS, PLAYERS)) == []


def test_names_normalise_across_accents_case_and_punctuation() -> None:
    assert normalise("Nikola Jokić") == normalise("nikola jokic.") == "nikola jokic"
    assert normalise("Jaren Jackson Jr.") == "jaren jackson jr"


def test_matching_finds_a_player_from_a_surname_or_a_typo() -> None:
    known = {"Nikola Jokic": 1, "Victor Wembanyama": 2, "Jalen Johnson": 3, "Jalen Williams": 4}
    assert match_name("jokic", known).value == 1  # type: ignore[union-attr]
    assert match_name("Wembanyma", known).value == 2  # type: ignore[union-attr]
    assert match_name("Nikola Jokić", known).score == 1.0  # type: ignore[union-attr]


def test_an_ambiguous_name_names_its_rival() -> None:
    known = {"Jalen Johnson": 3, "Jalen Williams": 4}
    found = match_name("Jalen", known)
    assert found is not None and found.rival is not None, "two Jalens: the caller must ask"
    assert match_name("Zzzz", known) is None


def test_teams_match_loosely_but_never_ambiguously() -> None:
    assert match_team("brighton bears", TEAMS) == "Brighton Bears"
    assert match_team("lebrons load management", TEAMS) == "LeBron's Load Management LLC"
    assert match_team("Zzz", TEAMS) is None
