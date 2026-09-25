"""Has a season been drafted? Every branch of `app.inseason.drafted`'s rule.

The case the rule exists for is 2027 as it was stored on 2026-09-23: an
auction scheduled for Sat, Oct 10 at 2:00 PM ET, and a full set of lineup
rows -- ESPN's pre-draft roster feed, last season's rosters projected onto
every future day -- with no pick, no move and no score behind them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import DraftPick, LeagueSeason, MatchupPeriod, Team
from app.inseason.drafted import (
    HELD,
    PROVEN,
    SCHEDULED,
    UNSCHEDULED,
    season_is_drafted,
    when,
)
from tests import scoring_db as db

#: The morning the bug was found, and the auction it was found before.
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
AUCTION = datetime(2026, 10, 10, 18, tzinfo=UTC)


def _season(
    session: Session, drafted_at: datetime | None, *, draft_type: str | None = "AUCTION"
) -> tuple[LeagueSeason, list[Team], list[MatchupPeriod]]:
    league_season, teams, periods = db.league_season(session, season=2027, drafted_at=drafted_at)
    league_season.draft_type = draft_type
    session.flush()
    return league_season, teams, periods


def _ghost_rosters(session: Session, teams: list[Team], periods: list[MatchupPeriod]) -> None:
    """Lineup rows and nothing else: the signature ESPN's pre-draft feed leaves."""
    for number, team in enumerate(teams):
        for day in (1, 2, 3):
            db.held(session, team, periods[0], db.player(session, f"Held {number}"), day)


def _pick(session: Session, league_season: LeagueSeason, team: Team) -> None:
    session.add(
        DraftPick(
            league_season_id=league_season.id,
            player_id=db.player(session, "First Pick").id,
            team_id=team.id,
            round_num=1,
            round_pick=1,
            bid_amount=60,
        )
    )
    session.flush()


def test_an_auction_still_ahead_is_not_drafted_whatever_the_lineups_say(
    scoring_session: Session,
) -> None:
    league_season, teams, periods = _season(scoring_session, AUCTION)
    _ghost_rosters(scoring_session, teams, periods)

    found = season_is_drafted(scoring_session, league_season, NOW)

    assert found.drafted is False
    assert found.drafted_at == AUCTION
    assert found.reason == (
        "The auction is Sat, Oct 10 at 2:00 PM ET; there are no rosters to project until then."
    )


def test_a_date_ahead_wins_even_over_a_stored_move(scoring_session: Session) -> None:
    """Nothing held before the draft is a roster, and a pre-draft move is no proof."""
    league_season, teams, _ = _season(scoring_session, AUCTION)
    db.transaction(
        scoring_session,
        teams[0],
        1,
        "FREEAGENT",
        [("ADD", db.player(scoring_session, "A"), None, teams[0])],
    )
    _pick(scoring_session, league_season, teams[0])

    assert season_is_drafted(scoring_session, league_season, NOW).drafted is False


def test_a_snake_draft_is_called_a_draft(scoring_session: Session) -> None:
    league_season, _, _ = _season(scoring_session, AUCTION, draft_type="SNAKE")

    found = season_is_drafted(scoring_session, league_season, NOW)

    assert found.reason == SCHEDULED.format(draft="draft", when="Sat, Oct 10 at 2:00 PM ET")


def test_a_date_in_the_past_is_drafted_even_with_no_picks_stored(
    scoring_session: Session,
) -> None:
    """Older seasons may lack their picks; a draft date behind us settles it."""
    league_season, teams, periods = _season(scoring_session, AUCTION)
    _ghost_rosters(scoring_session, teams, periods)
    after = datetime(2026, 10, 10, 18, 1, tzinfo=UTC)

    found = season_is_drafted(scoring_session, league_season, after)

    assert found.drafted is True
    assert found.reason == HELD.format(draft="auction", when="Sat, Oct 10 at 2:00 PM ET")


def test_the_draft_moment_itself_counts_as_held(scoring_session: Session) -> None:
    """`draft_is_pending` is strictly "later than now", so the minute it starts
    the season is drafted: an auction under way is not one still to come."""
    league_season, _, _ = _season(scoring_session, AUCTION)

    assert season_is_drafted(scoring_session, league_season, AUCTION).drafted is True


def test_an_unknown_date_with_only_lineups_is_the_ghost_signature(
    scoring_session: Session,
) -> None:
    league_season, teams, periods = _season(scoring_session, None)
    _ghost_rosters(scoring_session, teams, periods)

    found = season_is_drafted(scoring_session, league_season, NOW)

    assert found.drafted is False
    assert found.drafted_at is None
    assert found.reason == UNSCHEDULED
    assert found.reason == (
        "The draft has not been scheduled; there are no rosters to project until it is held."
    )


def test_an_unknown_date_and_nothing_stored_is_not_drafted(scoring_session: Session) -> None:
    league_season, _, _ = _season(scoring_session, None)

    assert season_is_drafted(scoring_session, league_season, NOW).drafted is False


def test_an_unknown_date_with_a_draft_pick_is_drafted(scoring_session: Session) -> None:
    """2019-2025 here: no `drafted_at` was ever read, and their picks are stored."""
    league_season, teams, periods = _season(scoring_session, None)
    _ghost_rosters(scoring_session, teams, periods)
    _pick(scoring_session, league_season, teams[0])

    found = season_is_drafted(scoring_session, league_season, NOW)

    assert (found.drafted, found.reason) == (True, PROVEN)


def test_an_unknown_date_with_a_transaction_is_drafted(scoring_session: Session) -> None:
    league_season, teams, _ = _season(scoring_session, None)
    db.transaction(
        scoring_session,
        teams[0],
        5,
        "FREEAGENT",
        [("ADD", db.player(scoring_session, "B"), None, teams[0])],
    )

    assert season_is_drafted(scoring_session, league_season, NOW).drafted is True


def test_an_unknown_date_with_a_scored_matchup_is_drafted(scoring_session: Session) -> None:
    league_season, teams, periods = _season(scoring_session, None)
    db.matchup(scoring_session, periods[0], teams[0], teams[1], posted={teams[0]: {"PTS": 400.0}})

    assert season_is_drafted(scoring_session, league_season, NOW).drafted is True


def test_an_unscored_matchup_proves_nothing(scoring_session: Session) -> None:
    """ESPN sets every pairing before the draft: the schedule is not a draft."""
    league_season, teams, periods = _season(scoring_session, None)
    db.matchup(scoring_session, periods[0], teams[0], teams[1])

    assert season_is_drafted(scoring_session, league_season, NOW).drafted is False


def test_the_evidence_is_this_seasons_own(scoring_session: Session) -> None:
    """Last season's picks prove nothing about this one."""
    last, last_teams, _ = db.league_season(scoring_session, season=2026)
    _pick(scoring_session, last, last_teams[0])
    league_season, _, _ = _season(scoring_session, None)

    assert season_is_drafted(scoring_session, league_season, NOW).drafted is False
    assert season_is_drafted(scoring_session, last, NOW).drafted is True


def test_the_real_clock_is_the_default(scoring_session: Session) -> None:
    league_season, _, _ = _season(scoring_session, datetime(2999, 1, 1, tzinfo=UTC))

    assert season_is_drafted(scoring_session, league_season).drafted is False


def test_when_is_eastern_time_in_both_halves_of_the_year() -> None:
    assert when(AUCTION) == "Sat, Oct 10 at 2:00 PM ET", "summer time: UTC-4"
    assert when(datetime(2027, 1, 9, 18, tzinfo=UTC)) == "Sat, Jan 9 at 1:00 PM ET", "UTC-5"
    assert when(datetime(2026, 10, 10, 4, 5, tzinfo=UTC)) == "Sat, Oct 10 at 12:05 AM ET"
    assert when(datetime(2026, 10, 10, 16, 30, tzinfo=UTC)) == "Sat, Oct 10 at 12:30 PM ET"
