"""The morning message before the draft: the draft's date, not a plan.

The digest's three plan blocks build on a roster, and before the draft there
is none, whatever ESPN's feed stored (`app.inseason.drafted`). Each block
says the draft's own sentence instead of building on the ghost rosters.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.digest import season_outlook, today_block, week_block
from app.inseason import drafted
from tests.pickups_db import clear_schedule, games
from tests.scoring_db import AUCTION_NOTE, BEFORE_THE_AUCTION, undrafted_season


@pytest.fixture(autouse=True)
def before_the_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafted, "CLOCK", lambda: BEFORE_THE_AUCTION)


def test_every_plan_block_says_the_draft_instead(scoring_session: Session) -> None:
    clear_schedule(scoring_session)
    league_season, _, _ = undrafted_season(scoring_session)
    games(scoring_session, 10, list(range(1, 15)), season=2027)
    on = date(2026, 10, 21)

    assert today_block(scoring_session, league_season, 1, on=on) == (
        None,
        [f"  no lineup today: {AUCTION_NOTE}"],
    )
    assert week_block(scoring_session, league_season, 1, on=on) == (
        None,
        None,
        [f"  no plan today: {AUCTION_NOTE}"],
    )
    assert season_outlook(scoring_session, league_season, 1, on=on) == (
        None,
        [f"  no season view today: {AUCTION_NOTE}"],
    )
