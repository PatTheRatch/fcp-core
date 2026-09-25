"""The standings route orders by the league's ranking rule, and reports ESPN's
own table for a season ESPN has finished."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.leagues import get_standings
from app.db.models import LeagueSeason, Matchup, MatchupPeriod, Team
from app.digest import standing_lines
from app.scoring.ranking import CATEGORY_WORDS, MATCHUP_WORDS
from tests.scoring_db import league_season


def _meet(
    session: Session,
    period: MatchupPeriod,
    home: Team,
    away: Team,
    *,
    home_won: int,
    home_lost: int,
    winner: str | None = None,
) -> None:
    decided = winner or ("HOME" if home_won > home_lost else "AWAY")
    session.add(
        Matchup(
            matchup_period_id=period.id,
            home_team_id=home.id,
            away_team_id=away.id,
            winner=decided,
            home_categories_won=home_won,
            home_categories_lost=home_lost,
            categories_tied=9 - home_won - home_lost,
        )
    )
    session.flush()


def _season(
    session: Session, *, finished: bool, scoring: str = "H2H_CATEGORY"
) -> tuple[LeagueSeason, tuple[Team, Team, Team]]:
    """Three teams. Alpha wins two matchups narrowly and loses one heavily;
    Bravo wins one heavily. By matchups Alpha leads; by categories Bravo does."""
    ls, (alpha, bravo, charlie), windows = league_season(
        session, team_names=("Alpha", "Bravo", "Charlie"), periods=3, regular_season_periods=3
    )
    ls.scoring_type = scoring
    _meet(session, windows[0], alpha, charlie, home_won=5, home_lost=4)
    _meet(session, windows[1], alpha, bravo, home_won=1, home_lost=8)
    _meet(session, windows[2], alpha, charlie, home_won=5, home_lost=4)
    if not finished:
        # A week still being played: nothing in it counts, and it is what
        # says the season is in play.
        _meet(session, windows[2], bravo, charlie, home_won=0, home_lost=0, winner="UNDECIDED")
    for team, (won, lost) in zip((alpha, bravo, charlie), ((11, 16), (8, 1), (8, 10)), strict=True):
        team.categories_won, team.categories_lost, team.categories_tied = won, lost, 0
    session.flush()
    return ls, (alpha, bravo, charlie)


def test_a_season_in_play_is_ordered_by_category_share_not_matchups(
    scoring_session: Session,
) -> None:
    ls, _teams = _season(scoring_session, finished=False)
    rows = get_standings(ls, scoring_session, include_playoffs=False)
    assert [row.name for row in rows] == ["Bravo", "Charlie", "Alpha"]
    by_name = {row.name: row for row in rows}
    # Alpha won the most matchups and sits last, which is the whole bug.
    assert (by_name["Alpha"].matchups_won, by_name["Alpha"].matchups_lost) == (2, 1)
    assert by_name["Bravo"].share == 8 / 9
    assert all(row.unit == "categories" for row in rows)
    assert all(row.order_note == CATEGORY_WORDS for row in rows)
    assert [row.place for row in rows] == [1, 2, 3]
    assert [row.rule_place for row in rows] == [1, 2, 3]
    # In play, ESPN's standing is not reported and nothing is flagged.
    assert all(row.standing is None and row.place_note is None for row in rows)


def test_a_finished_season_is_espn_s_table_with_its_differences_flagged(
    scoring_session: Session,
) -> None:
    ls, (alpha, bravo, charlie) = _season(scoring_session, finished=True)
    # ESPN's published table: Bravo, Alpha, Charlie (Alpha, say, led a division).
    bravo.standing, alpha.standing, charlie.standing = 1, 2, 3
    scoring_session.flush()
    rows = get_standings(ls, scoring_session, include_playoffs=False)
    assert [row.name for row in rows] == ["Bravo", "Alpha", "Charlie"]
    assert [row.standing for row in rows] == [1, 2, 3]
    by_name = {row.name: row for row in rows}
    assert by_name["Alpha"].rule_place == 3
    assert by_name["Charlie"].rule_place == 2
    assert by_name["Bravo"].place_note is None
    note = by_name["Alpha"].place_note
    assert note is not None
    assert "ESPN's published table puts Alpha 2nd" in note
    assert "category win share puts it 3rd" in note
    assert rows[0].order_note.startswith("ESPN's own published table")


def test_a_most_categories_league_keeps_the_matchup_order(scoring_session: Session) -> None:
    ls, _teams = _season(scoring_session, finished=False, scoring="H2H_MOST_CATEGORIES")
    rows = get_standings(ls, scoring_session, include_playoffs=False)
    assert [row.name for row in rows] == ["Alpha", "Bravo", "Charlie"]
    assert all(row.unit == "matchups" and row.order_note == MATCHUP_WORDS for row in rows)


def test_the_digest_s_place_is_the_standings_place_on_categories(
    scoring_session: Session,
) -> None:
    """The morning message reads the same table: Alpha, with the most
    matchups won, is third on categories, and the line says so first."""
    ls, (alpha, _bravo, _charlie) = _season(scoring_session, finished=False)
    place, lines = standing_lines(scoring_session, ls, int(alpha.espn_team_id))
    assert place is not None
    assert (place.place, place.of, place.unit) == (3, 3, "categories")
    assert lines[0] == "  3 of 3, 11-16 on categories (.407)"
    assert lines[1] == "  2-1 on matchups"
