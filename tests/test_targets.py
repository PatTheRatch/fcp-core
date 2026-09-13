"""Category target tests.

What matters here is not the percentile arithmetic but the three decisions
around it: that counting categories are read only from leagues of the same
size, that rate categories pool every season, and that turnovers invert into
a ceiling rather than a floor.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    League,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Team,
)
from app.db.session import make_engine, make_session_factory
from app.draft.targets import category_targets

REPO_ROOT = Path(__file__).resolve().parent.parent
LEAGUE_ID = 77


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
        session.execute(text("TRUNCATE leagues, players, owners RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


def build_season(
    session: Session,
    *,
    season: int,
    team_count: int,
    points: list[int],
    turnovers: list[int] | None = None,
    field_goal_pct: list[float] | None = None,
) -> LeagueSeason:
    """A season whose contested matchups posted exactly these totals."""
    league = session.scalar(select(League).where(League.espn_league_id == LEAGUE_ID))
    if league is None:
        league = League(espn_league_id=LEAGUE_ID)
        session.add(league)
        session.flush()

    league_season = LeagueSeason(
        league_id=league.id,
        season=season,
        name=f"S{season}",
        scoring_type="H2H_CATEGORY",
        team_count=team_count,
        regular_season_periods=1,
        total_matchup_periods=1,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        median_scoring=False,
        raw_settings={},
    )
    session.add(league_season)
    session.flush()

    categories = {}
    for position, abbreviation in enumerate(("PTS", "TO", "FG%")):
        category = LeagueSeasonCategory(
            league_season_id=league_season.id,
            stat_id=position,
            abbreviation=abbreviation,
            position=position,
            is_reverse=False,
        )
        session.add(category)
        session.flush()
        categories[abbreviation] = category

    home = Team(
        league_season_id=league_season.id,
        espn_team_id=1,
        name="H",
        categories_won=0,
        categories_lost=0,
        categories_tied=0,
    )
    away = Team(
        league_season_id=league_season.id,
        espn_team_id=2,
        name="A",
        categories_won=0,
        categories_lost=0,
        categories_tied=0,
    )
    session.add_all([home, away])
    session.flush()

    turnovers = turnovers or [0] * len(points)
    field_goal_pct = field_goal_pct or [0.0] * len(points)
    # One period per posted total. A team plays once a week, which the schema
    # enforces, so several totals have to mean several weeks.
    for index, value in enumerate(points):
        period = MatchupPeriod(
            league_season_id=league_season.id,
            period=index + 1,
            is_playoff=False,
            first_scoring_period=index * 7 + 1,
            final_scoring_period=index * 7 + 7,
        )
        session.add(period)
        session.flush()
        matchup = Matchup(
            matchup_period_id=period.id,
            home_team_id=home.id,
            away_team_id=away.id,
            winner="HOME",
            home_categories_won=2,
            home_categories_lost=1,
            categories_tied=0,
        )
        session.add(matchup)
        session.flush()
        for abbreviation, figure in (
            ("PTS", float(value)),
            ("TO", float(turnovers[index])),
            ("FG%", float(field_goal_pct[index])),
        ):
            session.add(
                MatchupTeamStat(
                    matchup_id=matchup.id,
                    team_id=home.id,
                    abbreviation=abbreviation,
                    value=figure,
                    result="WIN",
                    league_season_category_id=categories[abbreviation].id,
                )
            )
    session.flush()
    return league_season


def test_the_target_is_the_percentile_of_what_opponents_post(session: Session) -> None:
    ls = build_season(session, season=2026, team_count=10, points=[100, 200, 300, 400, 500])

    at_median = {t.abbreviation: t for t in category_targets(session, ls)}["PTS"]

    assert at_median.target == 300.0, "clear the median opponent to win half the time"
    assert at_median.win_probability == 0.5
    assert at_median.lower_is_better is False
    assert at_median.sample == 5


def test_a_higher_win_rate_demands_a_higher_total(session: Session) -> None:
    ls = build_season(session, season=2026, team_count=10, points=[100, 200, 300, 400, 500])

    median = {t.abbreviation: t.target for t in category_targets(session, ls)}["PTS"]
    ambitious = {
        t.abbreviation: t.target for t in category_targets(session, ls, win_probability=0.75)
    }["PTS"]

    assert ambitious > median


def test_turnovers_become_a_ceiling_not_a_floor(session: Session) -> None:
    """Fewer is better, so winning more often means a lower number."""
    ls = build_season(
        session,
        season=2026,
        team_count=10,
        points=[1, 2, 3, 4, 5],
        turnovers=[100, 200, 300, 400, 500],
    )

    median = {t.abbreviation: t for t in category_targets(session, ls)}["TO"]
    ambitious = {t.abbreviation: t for t in category_targets(session, ls, win_probability=0.75)}[
        "TO"
    ]

    assert median.lower_is_better is True
    assert median.target == 300.0
    assert ambitious.target < median.target, "to win turnovers more often, commit fewer"


def test_counting_targets_come_only_from_leagues_of_the_same_size(
    session: Session,
) -> None:
    """A sixteen team league is thinner, so its totals cannot borrow from ten."""
    build_season(session, season=2024, team_count=10, points=[900, 1000, 1100])
    big = build_season(session, season=2026, team_count=16, points=[400, 500, 600])
    session.flush()

    points = {t.abbreviation: t for t in category_targets(session, big)}["PTS"]

    assert points.target == 500.0, "read from the sixteen team season alone"
    assert points.basis_team_count == 16
    assert points.basis_seasons == (2026,)
    assert points.sample == 3


def test_rate_targets_pool_every_season(session: Session) -> None:
    """A percentage does not care how many players produced it."""
    build_season(
        session,
        season=2024,
        team_count=10,
        points=[1, 2, 3],
        field_goal_pct=[0.40, 0.45, 0.50],
    )
    big = build_season(
        session,
        season=2026,
        team_count=16,
        points=[4, 5, 6],
        field_goal_pct=[0.42, 0.47, 0.52],
    )
    session.flush()

    rate = {t.abbreviation: t for t in category_targets(session, big)}["FG%"]

    assert rate.sample == 6, "both seasons, not just the matching size"
    assert rate.basis_team_count == 0, "zero marks a pooled rate"


def test_an_unseen_league_size_borrows_the_nearest_and_says_so(
    session: Session,
) -> None:
    """Better a visibly borrowed target than a silently wrong one."""
    build_season(session, season=2024, team_count=12, points=[100, 200, 300])
    unseen = build_season(session, season=2026, team_count=20, points=[])
    session.flush()

    points = {t.abbreviation: t for t in category_targets(session, unseen)}["PTS"]

    assert points.basis_team_count == 12, "nearest size available"
    assert points.basis_seasons == (2024,)


def test_an_impossible_win_probability_is_rejected(session: Session) -> None:
    ls = build_season(session, season=2026, team_count=10, points=[1, 2, 3])

    for bad in (0.0, 1.0, -0.5, 2.0):
        with pytest.raises(ValueError, match="strictly between"):
            category_targets(session, ls, win_probability=bad)


def test_the_season_being_drafted_for_does_not_count_as_its_own_basis(
    session: Session,
) -> None:
    """The real case: 2027 exists at sixteen teams and has not been played.

    Matching on size alone would find that empty season and return nothing,
    which is worse than borrowing visibly from a season that was played.
    """
    build_season(session, season=2023, team_count=16, points=[400, 500, 600])
    upcoming = build_season(session, season=2027, team_count=16, points=[])
    session.flush()

    points = {t.abbreviation: t for t in category_targets(session, upcoming)}["PTS"]

    assert points.target == 500.0
    assert points.basis_seasons == (2023,), "the unplayed season is not a basis"
    assert points.sample == 3


def build_two_period_lengths(session: Session, *, season: int, team_count: int) -> LeagueSeason:
    """A season of ordinary weeks plus one All-Star fortnight.

    The fortnight posts far more of everything, which is the distortion under
    test: pooling it with the weeks drags every target upward.
    """
    league = session.scalar(select(League).where(League.espn_league_id == LEAGUE_ID))
    if league is None:
        league = League(espn_league_id=LEAGUE_ID)
        session.add(league)
        session.flush()

    league_season = LeagueSeason(
        league_id=league.id,
        season=season,
        name=f"S{season}",
        scoring_type="H2H_CATEGORY",
        team_count=team_count,
        regular_season_periods=4,
        total_matchup_periods=4,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        median_scoring=False,
        raw_settings={},
    )
    session.add(league_season)
    session.flush()

    category = LeagueSeasonCategory(
        league_season_id=league_season.id,
        stat_id=0,
        abbreviation="PTS",
        position=0,
        is_reverse=False,
    )
    session.add(category)
    home = Team(
        league_season_id=league_season.id,
        espn_team_id=1,
        name="H",
        categories_won=0,
        categories_lost=0,
        categories_tied=0,
    )
    away = Team(
        league_season_id=league_season.id,
        espn_team_id=2,
        name="A",
        categories_won=0,
        categories_lost=0,
        categories_tied=0,
    )
    session.add_all([home, away])
    session.flush()

    # Three ordinary weeks at 100/200/300, then a fortnight at 900.
    plan = [(7, 100.0), (7, 200.0), (7, 300.0), (14, 900.0)]
    day = 1
    for index, (length, value) in enumerate(plan):
        period = MatchupPeriod(
            league_season_id=league_season.id,
            period=index + 1,
            is_playoff=False,
            first_scoring_period=day,
            final_scoring_period=day + length - 1,
        )
        day += length
        session.add(period)
        session.flush()
        matchup = Matchup(
            matchup_period_id=period.id,
            home_team_id=home.id,
            away_team_id=away.id,
            winner="HOME",
            home_categories_won=1,
            home_categories_lost=0,
            categories_tied=0,
        )
        session.add(matchup)
        session.flush()
        session.add(
            MatchupTeamStat(
                matchup_id=matchup.id,
                team_id=home.id,
                abbreviation="PTS",
                value=value,
                result="WIN",
                league_season_category_id=category.id,
            )
        )
    session.flush()
    return league_season


def test_the_all_star_fortnight_is_left_out_of_the_weekly_target(
    session: Session,
) -> None:
    """A fortnight posts about a third more, so pooling it inflates the target."""
    ls = build_two_period_lengths(session, season=2026, team_count=12)

    weekly = {t.abbreviation: t for t in category_targets(session, ls)}["PTS"]

    assert weekly.period_days == 7, "the ordinary week is the default"
    assert weekly.target == 200.0, "median of 100, 200, 300 and not the 900 fortnight"
    assert weekly.sample == 3


def test_the_fortnight_can_be_asked_about_directly(session: Session) -> None:
    """Useful in season, when the double week is the thing being planned for."""
    ls = build_two_period_lengths(session, season=2026, team_count=12)

    fortnight = {t.abbreviation: t for t in category_targets(session, ls, period_days=14)}["PTS"]

    assert fortnight.period_days == 14
    assert fortnight.target == 900.0
    assert fortnight.target > 200.0, "a fortnight demands far more than a week"


def test_the_ordinary_length_is_read_from_the_data_not_assumed(
    session: Session,
) -> None:
    """A league on a different schedule should not keep being told about weeks."""
    from app.draft.targets import modal_period_days

    build_two_period_lengths(session, season=2026, team_count=12)
    session.flush()

    assert modal_period_days(session, [2026]) == 7, "three weeks against one fortnight"
