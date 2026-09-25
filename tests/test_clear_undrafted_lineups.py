"""The one clean-up: 2027's ghost lineup rows go, and nothing else does.

`scripts/clear_undrafted_lineups.py` deletes the `daily_lineup_slots` rows of
one league's one season, and only when `app.inseason.drafted` says that
season has not been drafted. Everything it could get wrong is here: another
season's rows, another table's, a drafted season, a season not stored, and
a dry run that writes.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import DailyLineupSlot, LeagueSeason, Matchup, Team
from app.inseason import drafted
from scripts import clear_undrafted_lineups as script
from tests.scoring_db import (
    AUCTION_NOTE,
    BEFORE_THE_AUCTION,
    LEAGUE_ID,
    held,
    league_season,
    player,
    undrafted_season,
)


@pytest.fixture(autouse=True)
def before_the_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafted, "CLOCK", lambda: BEFORE_THE_AUCTION)


def _rows(session: Session, season: int) -> int:
    return int(
        session.scalar(
            select(func.count(DailyLineupSlot.id))
            .join(Team, Team.id == DailyLineupSlot.team_id)
            .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
            .where(LeagueSeason.season == season)
        )
        or 0
    )


def _two_seasons(session: Session) -> None:
    """2026 played, with a real lineup day, and 2027 with its ghost rosters."""
    _, (home, _away), (first, _) = league_season(session, season=2026)
    held(session, home, first, player(session, "Real Starter"), 1)
    undrafted_season(session)
    session.commit()


def test_the_survey_counts_the_undrafted_seasons_rows_and_no_others(
    scoring_session: Session,
) -> None:
    _two_seasons(scoring_session)

    found = script.survey(scoring_session, LEAGUE_ID, 2027)

    assert (found.rows, found.teams, found.first_day, found.last_day) == (42, 2, 1, 7)
    assert found.reason == AUCTION_NOTE
    assert found.lines()[-1] == "  seasons it would touch: 2027"


def test_clear_deletes_exactly_those_rows_and_leaves_everything_else(
    scoring_session: Session,
) -> None:
    _two_seasons(scoring_session)
    matchups = scoring_session.scalar(select(func.count(Matchup.id)))

    deleted = script.clear(scoring_session, LEAGUE_ID, 2027)
    scoring_session.commit()

    assert deleted == 42
    assert _rows(scoring_session, 2027) == 0
    assert _rows(scoring_session, 2026) == 1, "the played season's record is untouched"
    assert scoring_session.scalar(select(func.count(Matchup.id))) == matchups
    assert scoring_session.scalar(select(func.count(Team.id))) == 4


def test_a_drafted_season_is_refused_whatever_is_asked(scoring_session: Session) -> None:
    _two_seasons(scoring_session)

    with pytest.raises(script.RefusedError, match="has been drafted"):
        script.survey(scoring_session, LEAGUE_ID, 2026)
    with pytest.raises(script.RefusedError, match="has been drafted"):
        script.clear(scoring_session, LEAGUE_ID, 2026)
    assert _rows(scoring_session, 2026) == 1


def test_a_season_not_stored_is_refused(scoring_session: Session) -> None:
    _two_seasons(scoring_session)
    with pytest.raises(script.RefusedError, match="no stored season 2031"):
        script.survey(scoring_session, LEAGUE_ID, 2031)


def _on_the_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    settings = get_settings().model_copy(update={"database_url": test_database_url})
    monkeypatch.setattr(script, "get_settings", lambda: settings)


def test_the_dry_run_writes_nothing_and_says_what_would_go(
    scoring_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    test_database_url: str,
) -> None:
    _two_seasons(scoring_session)
    _on_the_test_database(monkeypatch, test_database_url)

    assert script.main(["--league", str(LEAGUE_ID), "--season", "2027"]) == 0

    said = capsys.readouterr().out
    assert "daily_lineup_slots: 42 rows, 2 teams, scoring periods 1-7" in said
    assert "Dry run: nothing deleted." in said
    assert _rows(scoring_session, 2027) == 42


def test_apply_deletes_and_says_how_many(
    scoring_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    test_database_url: str,
) -> None:
    _two_seasons(scoring_session)
    _on_the_test_database(monkeypatch, test_database_url)

    assert script.main(["--league", str(LEAGUE_ID), "--season", "2027", "--apply"]) == 0

    said = capsys.readouterr().out
    assert "Deleted 42 daily_lineup_slots rows for season 2027." in said
    assert "Left for season 2027: 0." in said
    scoring_session.expire_all()
    assert _rows(scoring_session, 2027) == 0
    assert _rows(scoring_session, 2026) == 1


def test_apply_on_a_drafted_season_exits_two_and_deletes_nothing(
    scoring_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    test_database_url: str,
) -> None:
    _two_seasons(scoring_session)
    _on_the_test_database(monkeypatch, test_database_url)

    code: Any = script.main(["--league", str(LEAGUE_ID), "--season", "2026", "--apply"])

    assert code == 2
    assert "Refused:" in capsys.readouterr().err
    assert _rows(scoring_session, 2026) == 1
