"""Trade reconstruction: the cases the rule has to get right.

Built on the `scoring_session` fixture (tests/conftest.py) and the row helpers
in tests/scoring_db.py, so each test writes only the roster movement its case
needs. The reconstruction itself is `app.scoring.trades.reconstruct_trades`.

The cases come from the live league's own failures. Counting every movement
over-detects badly before 2026 (526 sides against ESPN's 204), because a waiver
claim looks exactly like one half of a trade. So the four that matter are: a
plain two-team swap, a 2-for-1 (uneven, still one trade), a one-sided movement
that only the ledger proves was a trade, and a drop-then-pickup-by-someone-else
that must not count at all.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod, Team
from app.scoring.trades import Trade, reconstruct_trades
from tests.scoring_db import held, league_season, player, transaction

#: Two matchup periods of seven days: enough for a spell either side of a move.
SEASON = 2026


def _season(session: Session) -> tuple[LeagueSeason, list[Team], list[MatchupPeriod]]:
    """A two-team, two-period season: enough for a spell either side of a move."""
    return league_season(session, season=SEASON, periods=2, days_per_period=7)


def test_two_team_swap_is_one_trade_both_ways(scoring_session: Session) -> None:
    """Both halves move; each team sees one trade, one player in and one out."""
    _, (home, away), (period, _) = _season(scoring_session)
    mine = player(scoring_session, "Mine Out", 1)
    theirs = player(scoring_session, "Theirs In", 2)

    # Home holds `mine` through day 4 and `theirs` from day 6: the swap is on
    # day 5, and each player's spell for the other team starts the day after his
    # spell for this one ends.
    held(scoring_session, home, period, mine, 3)
    held(scoring_session, home, period, mine, 4)
    held(scoring_session, home, period, theirs, 6)
    held(scoring_session, away, period, theirs, 3)
    held(scoring_session, away, period, theirs, 4)
    held(scoring_session, away, period, mine, 6)

    got = reconstruct_trades(scoring_session, SEASON, home.id)

    assert len(got) == 1
    trade = got[0]
    assert isinstance(trade, Trade)
    assert trade.day == 4
    assert [party.name for party in trade.players_in] == [theirs.name]
    assert [party.name for party in trade.players_out] == [mine.name]
    assert trade.counterparty_names == (away.name,)
    assert trade.gradeable is True
    assert trade.from_ledger is False

    # The far side reconstructs the same deal mirrored.
    mirrored = reconstruct_trades(scoring_session, SEASON, away.id)
    assert len(mirrored) == 1
    assert [party.name for party in mirrored[0].players_in] == [mine.name]
    assert [party.name for party in mirrored[0].players_out] == [theirs.name]


def test_two_for_one_is_a_single_trade_with_uneven_sides(scoring_session: Session) -> None:
    """Two players one way and one the other is one trade, not two."""
    _, (home, away), (period, _) = _season(scoring_session)
    one = player(scoring_session, "Away Star", 11)
    two = player(scoring_session, "Away Role", 12)
    three = player(scoring_session, "Home Star", 13)

    # Home gives up two, receives one: `one` and `two` leave Home after day 4,
    # `three` arrives from day 6.
    for who in (one, two):
        held(scoring_session, home, period, who, 3)
        held(scoring_session, home, period, who, 4)
    held(scoring_session, home, period, three, 6)
    held(scoring_session, away, period, three, 3)
    held(scoring_session, away, period, three, 4)
    for who in (one, two):
        held(scoring_session, away, period, who, 6)

    got = reconstruct_trades(scoring_session, SEASON, home.id)

    assert len(got) == 1, "a 2-for-1 is one trade, not the two legs it contains"
    assert [party.name for party in got[0].players_in] == ["Home Star"]
    assert sorted(party.name for party in got[0].players_out) == ["Away Role", "Away Star"]
    assert got[0].gradeable is True


def test_one_sided_move_proven_by_the_ledger_is_kept(scoring_session: Session) -> None:
    """No reciprocal partner, but an executed TRADE_UPHOLD: a real trade whose
    other half left no roster trace. Keep it, and mark it not gradeable."""
    _, (home, away), (period, _) = _season(scoring_session)
    incomer = player(scoring_session, "Lone Arrival", 21)
    other = player(scoring_session, "Other Deal Player", 22)

    held(scoring_session, away, period, incomer, 3)
    held(scoring_session, away, period, incomer, 4)
    held(scoring_session, home, period, incomer, 6)
    # A second movement on the same day, so a reciprocal test could in principle
    # find a partner; it must not, because it goes the same way.
    held(scoring_session, away, period, other, 3)
    held(scoring_session, away, period, other, 4)
    held(scoring_session, home, period, other, 6)

    # An uphold carries no items at all, which is what ESPN really sends.
    transaction(scoring_session, home, 5, "TRADE_UPHOLD", [])

    got = reconstruct_trades(scoring_session, SEASON, home.id)

    assert len(got) == 1
    assert sorted(party.name for party in got[0].players_in) == [
        "Lone Arrival",
        "Other Deal Player",
    ]
    assert got[0].players_out == ()
    assert got[0].gradeable is False, "one side only: real, but nothing to grade it against"
    assert got[0].from_ledger is True


def test_waiver_pickup_is_not_a_trade(scoring_session: Session) -> None:
    """Home drops a player, Away adds him a day later, nobody moves back the
    other way. That is a waiver claim, and it must not be reported as a trade."""
    _, (home, away), (period, _) = _season(scoring_session)
    claimed = player(scoring_session, "Wire Guy", 31)

    held(scoring_session, home, period, claimed, 2)
    held(scoring_session, home, period, claimed, 3)
    held(scoring_session, away, period, claimed, 5)
    held(scoring_session, away, period, claimed, 6)

    transaction(scoring_session, away, 4, "WAIVER", [("ADD", claimed, None, away)])

    assert reconstruct_trades(scoring_session, SEASON, home.id) == []
    assert reconstruct_trades(scoring_session, SEASON, away.id) == []


def test_a_one_way_move_with_no_ledger_is_not_a_trade(scoring_session: Session) -> None:
    """One-way movement with no transaction row is unexplained, so it is not
    promoted to a trade. This is the pre-2026 false positive, reproduced."""
    _, (home, away), (period, _) = _season(scoring_session)
    mover = player(scoring_session, "Unproven", 41)

    held(scoring_session, home, period, mover, 2)
    held(scoring_session, home, period, mover, 3)
    held(scoring_session, away, period, mover, 5)

    assert reconstruct_trades(scoring_session, SEASON, home.id) == []
    assert reconstruct_trades(scoring_session, SEASON, away.id) == []


def test_reciprocal_moves_a_week_apart_are_not_one_trade(scoring_session: Session) -> None:
    """Two one-way moves in opposite directions, a week apart, are not a trade.

    Guards the pairing window (`SAME_TRADE_DAYS`) together with the pickup
    window: the movements have to be close enough in time to be one deal. Note
    which constant actually does the work here -- widening `SAME_TRADE_DAYS`
    alone still leaves this passing, because the two movements are also too far
    apart to be legs, so `PICKUP_WINDOW_DAYS` is what rejects them first.
    """
    _, (home, away), (period, _) = _season(scoring_session)
    mine = player(scoring_session, "Early Out", 51)
    theirs = player(scoring_session, "Late Back", 52)

    # Day 2: Away's player joins Home. Day 9: Home's player joins Away, so the
    # two moves point opposite ways but are seven days apart.
    held(scoring_session, away, period, theirs, 1)
    held(scoring_session, home, period, theirs, 2)
    held(scoring_session, home, period, mine, 3)
    held(scoring_session, away, period, mine, 9)

    assert reconstruct_trades(scoring_session, SEASON, home.id) == [], (
        "movements this far apart are not one deal"
    )


def test_reciprocal_legs_a_few_days_apart_are_not_one_trade(scoring_session: Session) -> None:
    """The same shape, but close enough to be legs, so `SAME_TRADE_DAYS` is what
    rejects it. Without this the pairing window is untested: the test above is
    satisfied by the pickup window alone."""
    _, (home, away), (period, _) = _season(scoring_session)
    mine = player(scoring_session, "Out First", 53)
    theirs = player(scoring_session, "Back Later", 54)

    # Both movements sit inside the two-day pickup window, but the two of them
    # are five days apart -- far outside the one-day pairing window.
    held(scoring_session, home, period, mine, 1)
    held(scoring_session, away, period, mine, 2)
    held(scoring_session, away, period, theirs, 6)
    held(scoring_session, home, period, theirs, 7)

    assert reconstruct_trades(scoring_session, SEASON, home.id) == [], (
        "a one-day window is what pairs two legs into a trade"
    )


def test_no_movement_means_no_trades(scoring_session: Session) -> None:
    _, (home, away), (period, _) = _season(scoring_session)
    stayed = player(scoring_session, "Loyal", 61)
    held(scoring_session, home, period, stayed, 1)
    held(scoring_session, home, period, stayed, 6)

    assert reconstruct_trades(scoring_session, SEASON, home.id) == []
    assert reconstruct_trades(scoring_session, SEASON, away.id) == []


def test_unknown_season_returns_nothing(scoring_session: Session) -> None:
    _, (home, _), _ = _season(scoring_session)
    assert reconstruct_trades(scoring_session, 1999, home.id) == []
