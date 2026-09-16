"""The currency: expected categories won, and what one line adds to a team."""

import pytest
from sqlalchemy.orm import Session

from app.draft.targets import CategoryDistribution
from app.scoring.lines import CategoryLine
from app.scoring.value import SeasonOpponents, expected_wins, marginal, per_category_marginal
from tests.scoring_db import league_season, matchup


def dist(abbreviation: str, mean: float, spread: float) -> CategoryDistribution:
    return CategoryDistribution(
        abbreviation=abbreviation,
        mean=mean,
        spread=spread,
        lower_is_better=abbreviation == "TO",
        sample=100,
        basis_seasons=(2026,),
        period_days=7,
        era_scale=1.0,
    )


OPPONENT = [dist("PTS", 550, 100), dist("BLK", 20, 8), dist("TO", 60, 14), dist("FG%", 0.47, 0.03)]


def test_a_line_at_the_opponent_mean_wins_half_of_everything() -> None:
    line = CategoryLine({"PTS": 550, "BLK": 20, "TO": 60, "FGM": 47, "FGA": 100})
    assert expected_wins(line, OPPONENT) == pytest.approx(2.0)


def test_a_punted_category_contributes_nothing() -> None:
    # Far behind in blocks: two more blocks move nothing.
    team = CategoryLine({"PTS": 550, "BLK": 0, "TO": 60, "FGM": 47, "FGA": 100})
    shot_blocker = CategoryLine({"BLK": 2})
    by_category = per_category_marginal(team + shot_blocker, shot_blocker, OPPONENT)
    assert by_category["BLK"] == pytest.approx(0.0, abs=0.01)


def test_a_close_category_contributes_the_most() -> None:
    # The same two blocks on a team sitting at the opponent's mean.
    punting = CategoryLine({"PTS": 550, "BLK": 0, "TO": 60, "FGM": 47, "FGA": 100})
    close = CategoryLine({"PTS": 550, "BLK": 19, "TO": 60, "FGM": 47, "FGA": 100})
    ahead = CategoryLine({"PTS": 550, "BLK": 40, "TO": 60, "FGM": 47, "FGA": 100})
    two = CategoryLine({"BLK": 2})
    gains = {
        name: marginal(team + two, two, OPPONENT)
        for name, team in (("punting", punting), ("close", close), ("ahead", ahead))
    }
    assert gains["close"] > 10 * max(gains["ahead"], gains["punting"])
    assert gains["close"] == pytest.approx(0.099, abs=0.005)


def test_turnovers_count_against() -> None:
    team = CategoryLine({"PTS": 550, "BLK": 20, "TO": 60, "FGM": 47, "FGA": 100})
    careless = CategoryLine({"TO": 10})
    assert marginal(team + careless, careless, OPPONENT) < 0


def test_a_poor_shooter_on_volume_costs_field_goal_percentage() -> None:
    team = CategoryLine({"PTS": 550, "BLK": 20, "TO": 60, "FGM": 47, "FGA": 100})
    brick = CategoryLine({"PTS": 20, "FGM": 6, "FGA": 20})
    by_category = per_category_marginal(team + brick, brick, OPPONENT)
    assert by_category["FG%"] < 0
    assert by_category["PTS"] > 0


def test_a_season_is_graded_against_its_own_teams_and_period_length(
    scoring_session: Session,
) -> None:
    session = scoring_session
    ls, (home, away), periods = league_season(session, periods=3, days_per_period=7)
    for period, (h, a) in zip(periods, ((500, 600), (520, 580), (540, 560)), strict=True):
        matchup(session, period, home, away, {home: {"PTS": h}, away: {"PTS": a}})
    opponents = SeasonOpponents(session, ls)
    (points,) = opponents.for_days(7)
    assert points.mean == pytest.approx(550)
    assert points.basis_seasons == (2026,)
    # A fortnight the regular season never used borrows the nearest length.
    assert opponents.for_days(14)[0].mean == pytest.approx(550)
