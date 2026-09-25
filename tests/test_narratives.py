"""Narrative derivation tests.

These build matchups directly rather than going through the ingest, because
what is under test is the arithmetic on top of stored rows: streaks, records,
head-to-head pairings and the handling of ties and byes.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app import narratives
from app.db.models import (
    League,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    Owner,
    Team,
)
from app.db.session import make_engine, make_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent
LEAGUE_ID = 99


@pytest.fixture(scope="module")
def factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")

    engine = make_engine(test_database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        session.execute(text("TRUNCATE leagues, owners RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


class Builder:
    """Builds a season's worth of matchups without going through ESPN."""

    def __init__(self, session: Session, season: int = 2026, regular_periods: int = 10) -> None:
        self.session = session
        league = session.scalar(select(League).where(League.espn_league_id == LEAGUE_ID))
        if league is None:
            league = League(espn_league_id=LEAGUE_ID)
            session.add(league)
            session.flush()
        self.league = league
        self.league_season = LeagueSeason(
            league_id=self.league.id,
            season=season,
            name=f"Season {season}",
            scoring_type="H2H_CATEGORY",
            team_count=0,
            regular_season_periods=regular_periods,
            total_matchup_periods=regular_periods + 2,
            playoff_team_count=2,
            playoff_matchup_period_length=1,
            keeper_count=0,
            uses_faab=True,
            acquisition_budget=100,
            median_scoring=False,
            raw_settings={},
        )
        session.add(self.league_season)
        session.flush()
        self.teams: dict[int, Team] = {}
        self.periods: dict[int, MatchupPeriod] = {}

    def team(
        self,
        espn_team_id: int,
        name: str,
        *,
        owners: list[str] | None = None,
        final_standing: int | None = None,
    ) -> Team:
        team = Team(
            league_season_id=self.league_season.id,
            espn_team_id=espn_team_id,
            name=name,
            categories_won=0,
            categories_lost=0,
            categories_tied=0,
            final_standing=final_standing,
        )
        self.session.add(team)
        self.session.flush()
        for guid in owners or []:
            owner = self.session.scalar(select(Owner).where(Owner.espn_owner_id == guid))
            if owner is None:
                owner = Owner(espn_owner_id=guid, display_name=guid)
                self.session.add(owner)
                self.session.flush()
            team.owners.append(owner)
        self.session.flush()
        self.teams[espn_team_id] = team
        return team

    def period(self, number: int, *, playoff: bool = False) -> MatchupPeriod:
        if number not in self.periods:
            period = MatchupPeriod(
                league_season_id=self.league_season.id, period=number, is_playoff=playoff
            )
            self.session.add(period)
            self.session.flush()
            self.periods[number] = period
        return self.periods[number]

    def matchup(
        self,
        period: int,
        home: int,
        away: int | None,
        winner: str,
        home_won: int = 5,
        home_lost: int = 4,
        tied: int = 0,
        playoff: bool = False,
    ) -> Matchup:
        matchup = Matchup(
            matchup_period_id=self.period(period, playoff=playoff).id,
            home_team_id=self.teams[home].id,
            away_team_id=self.teams[away].id if away is not None else None,
            winner=winner,
            home_categories_won=home_won,
            home_categories_lost=home_lost,
            categories_tied=tied,
        )
        self.session.add(matchup)
        self.session.flush()
        return matchup


def test_both_teams_get_a_view_of_each_matchup(session: Session) -> None:
    b = Builder(session)
    b.team(1, "Alpha")
    b.team(2, "Beta")
    b.matchup(1, 1, 2, "HOME", home_won=6, home_lost=3)

    sides = narratives.matchup_sides(session, b.league_season)

    assert len(sides) == 2
    by_team = {s.team_name: s for s in sides}
    assert by_team["Alpha"].result == "WIN"
    assert by_team["Alpha"].categories_won == 6
    assert by_team["Alpha"].margin == 3
    assert by_team["Beta"].result == "LOSS"
    assert by_team["Beta"].categories_won == 3, "the loser's tally is the mirror image"
    assert by_team["Beta"].margin == -3


def test_byes_are_excluded_entirely(session: Session) -> None:
    """A team with no opponent neither won nor lost."""
    b = Builder(session)
    b.team(1, "Alpha")
    b.matchup(1, 1, None, "UNDECIDED", home_won=0, home_lost=0, playoff=True)

    assert narratives.matchup_sides(session, b.league_season, include_playoffs=True) == []


def test_streaks_count_runs_and_a_tie_breaks_them(session: Session) -> None:
    b = Builder(session)
    b.team(1, "Alpha")
    b.team(2, "Beta")
    # Alpha: W W W TIE W  -> longest win run 3, not 4.
    for period, winner in enumerate(["HOME", "HOME", "HOME", "TIE", "HOME"], start=1):
        b.matchup(period, 1, 2, winner)

    result = {
        s.name: s for s in narratives.streaks(narratives.matchup_sides(session, b.league_season))
    }

    assert result["Alpha"].longest_win_streak == 3, "the tie must not bridge the runs"
    assert result["Alpha"].longest_loss_streak == 0
    assert result["Alpha"].final_streak == 1
    assert result["Alpha"].final_streak_result == "WIN"
    assert result["Beta"].longest_loss_streak == 3


def test_a_season_ending_in_a_tie_has_no_final_streak(session: Session) -> None:
    b = Builder(session)
    b.team(1, "Alpha")
    b.team(2, "Beta")
    b.matchup(1, 1, 2, "HOME")
    b.matchup(2, 1, 2, "TIE")

    result = {
        s.name: s for s in narratives.streaks(narratives.matchup_sides(session, b.league_season))
    }
    assert result["Alpha"].final_streak == 0
    assert result["Alpha"].final_streak_result is None


def test_playoffs_are_excluded_unless_asked_for(session: Session) -> None:
    b = Builder(session, regular_periods=1)
    b.team(1, "Alpha")
    b.team(2, "Beta")
    b.matchup(1, 1, 2, "HOME")
    b.matchup(2, 1, 2, "HOME", playoff=True)

    assert len(narratives.matchup_sides(session, b.league_season)) == 2
    assert len(narratives.matchup_sides(session, b.league_season, include_playoffs=True)) == 4


def test_notable_matchups_rank_by_margin_and_skip_ties(session: Session) -> None:
    b = Builder(session)
    for team_id in (1, 2, 3, 4, 5, 6):
        b.team(team_id, f"T{team_id}")
    b.matchup(1, 1, 2, "HOME", home_won=9, home_lost=0)  # sweep
    b.matchup(1, 3, 4, "HOME", home_won=5, home_lost=4)  # nail-biter
    b.matchup(1, 5, 6, "TIE", home_won=4, home_lost=4, tied=1)

    grouped = narratives.notable_matchups(narratives.matchup_sides(session, b.league_season))

    assert grouped["sweeps"][0].margin == 9
    assert grouped["sweeps"][0].winner_name == "T1"
    assert grouped["nail_biters"][0].margin == 1
    names = {m.winner_name for m in grouped["sweeps"] + grouped["nail_biters"]}
    assert "T5" not in names and "T6" not in names, "a tie has no winner to name"


def test_owner_records_span_seasons(session: Session) -> None:
    """The payoff of owners being global: a record that outlives a team."""
    first = Builder(session, season=2025, regular_periods=2)
    first.team(1, "Old Name", owners=["guid-pat"], final_standing=1)
    first.team(2, "Rival", owners=["guid-sam"])
    first.matchup(1, 1, 2, "HOME")
    first.matchup(2, 1, 2, "HOME")

    second = Builder(session, season=2026, regular_periods=2)
    second.team(7, "New Name", owners=["guid-pat"], final_standing=4)
    second.team(8, "Rival", owners=["guid-sam"])
    second.matchup(1, 7, 8, "AWAY")
    second.matchup(2, 7, 8, "TIE")
    session.flush()

    records = {r.display_name: r for r in narratives.owner_records(session, LEAGUE_ID)}
    pat = records["guid-pat"]  # display_name is seeded from the guid in the builder

    assert pat.matchups_won == 2
    assert pat.matchups_lost == 1
    assert pat.matchups_tied == 1
    assert pat.titles == 1
    assert [s.season for s in pat.seasons] == [2025, 2026]
    assert [s.team_name for s in pat.seasons] == ["Old Name", "New Name"]


def test_head_to_head_combines_seasons_and_counts_each_meeting_once(session: Session) -> None:
    first = Builder(session, season=2025, regular_periods=2)
    first.team(1, "A", owners=["guid-pat"])
    first.team(2, "B", owners=["guid-sam"])
    first.matchup(1, 1, 2, "HOME")
    first.matchup(2, 1, 2, "AWAY")

    second = Builder(session, season=2026, regular_periods=1)
    second.team(1, "A", owners=["guid-pat"])
    second.team(2, "B", owners=["guid-sam"])
    second.matchup(1, 1, 2, "TIE")
    session.flush()

    pairs = narratives.head_to_head(session, LEAGUE_ID)

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.meetings == 3, "three meetings, not six"
    assert pair.ties == 1
    assert {pair.a_wins, pair.b_wins} == {1}
    assert pair.seasons == [2025, 2026]


def test_head_to_head_handles_a_jointly_owned_team(session: Session) -> None:
    """A co-owned team gives each of its owners the meeting."""
    b = Builder(session, regular_periods=1)
    b.team(1, "Solo", owners=["guid-pat"])
    b.team(2, "Duo", owners=["guid-sam", "guid-alex"])
    b.matchup(1, 1, 2, "HOME")
    session.flush()

    pairs = {
        tuple(sorted((p.owner_a, p.owner_b))): p
        for p in narratives.head_to_head(session, LEAGUE_ID)
    }

    assert len(pairs) == 2, "one meeting for each of the two co-owners"
    for pair in pairs.values():
        assert pair.meetings == 1


def test_owner_identifiers_never_carry_the_espn_guid(session: Session) -> None:
    """The GUID is half of ESPN's cookie pair; it stays in the database.

    It is still the identity key, so this checks the exported id is ours and
    that the secret is not smuggled out under another name.
    """
    b = Builder(session, regular_periods=1)
    b.team(1, "A", owners=["{DEADBEEF-0000-0000-0000-000000000001}"])
    b.team(2, "B", owners=["{DEADBEEF-0000-0000-0000-000000000002}"])
    b.matchup(1, 1, 2, "HOME")
    session.flush()

    exported = [
        *(r.owner_id for r in narratives.owner_records(session, LEAGUE_ID)),
        *(h.owner_a for h in narratives.head_to_head(session, LEAGUE_ID)),
        *(h.owner_b for h in narratives.head_to_head(session, LEAGUE_ID)),
    ]
    assert exported, "nothing was exported, so the check would pass vacuously"
    assert all(isinstance(value, int) for value in exported)
    assert not any("DEADBEEF" in str(value) for value in exported)

    # The GUID is still stored, because identity across seasons depends on it.
    stored = session.scalars(select(Owner.espn_owner_id)).all()
    assert any("DEADBEEF" in guid for guid in stored)


def test_owner_records_are_ordered_on_categories_with_matchups_beside_them(
    session: Session,
) -> None:
    """A category league's all-time table is its category record: Pat wins two
    matchups five to four and loses one nought to nine; Sam wins one nine to
    nothing. Pat has the matchups, Sam the categories, and Sam is first."""
    b = Builder(session, regular_periods=3)
    b.team(1, "Pat", owners=["guid-pat"])
    b.team(2, "Sam", owners=["guid-sam"])
    b.matchup(1, 1, 2, "HOME", home_won=5, home_lost=4)
    b.matchup(2, 1, 2, "HOME", home_won=5, home_lost=4)
    b.matchup(3, 1, 2, "AWAY", home_won=0, home_lost=9)
    session.flush()

    records = narratives.owner_records(session, LEAGUE_ID)
    assert [r.display_name for r in records] == ["guid-sam", "guid-pat"]
    sam, pat = records
    assert (pat.matchups_won, pat.matchups_lost) == (2, 1)
    assert (pat.categories_won, pat.categories_lost) == (10, 17)
    assert (sam.categories_won, sam.categories_lost) == (17, 10)
    assert sam.share == 17 / 27
    assert [s.categories_won for s in sam.seasons] == [17]

    pair = narratives.head_to_head(session, LEAGUE_ID)[0]
    by_owner = {pair.owner_a_name: pair.a_categories, pair.owner_b_name: pair.b_categories}
    assert by_owner == {"guid-pat": 10, "guid-sam": 17}
    assert pair.meetings == 3
