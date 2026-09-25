"""The projected-standings routes, over a seeded league.

The same small season the pickup routes are tested on: two teams, a
three-slot lineup, and an NBA schedule everyone plays every day. What matters
here is not the numbers -- `tests/test_inseason_projected.py` pins those -- but
the two scopes, the shapes they answer, the day they are about, and the
answer on a season with nothing to build from -- one with no schedule, and
2027 before its auction, whose ghost rosters are not rosters.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.api.pickups import NO_ROSTER, NO_SCHEDULE, NOT_DRAFTED
from app.inseason import drafted
from app.inseason.projected_calibration import CALIBRATION_NOTE, SHORT_NOTE
from app.main import create_app
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    configure,
    eligible,
    games,
    projected,
    snapshot,
)
from tests.scoring_db import (
    AUCTION_NOTE,
    BEFORE_THE_AUCTION,
    LEAGUE_ID,
    held,
    league_season,
    matchup,
    player,
    undrafted_season,
)

SEASON = 2026
QUIET_SEASON = 2025
UNDRAFTED = 2027
HOME, AWAY = 1, 2
EVERY_DAY = list(range(1, 15))

STARTER = {
    "PTS": 20.0,
    "REB": 8.0,
    "AST": 4.0,
    "STL": 1.2,
    "BLK": 0.8,
    "3PM": 2.0,
    "TO": 2.4,
    "FGM": 8.0,
    "FGA": 17.0,
    "FTM": 4.0,
    "FTA": 5.0,
}

POSTED = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 20.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 235.0,
    "FGA": 500.0,
    "FTM": 78.0,
    "FTA": 100.0,
}


def _scaled(source: dict[str, float], factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in source.items()}


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        ls, (home, away), (first, second) = league_season(session, season=SEASON, days_per_period=7)
        configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        matchup(session, first, home, away, {home: POSTED, away: _scaled(POSTED, 0.8)})
        matchup(session, second, away, home)
        for name, factor, team in (
            ("A", 1.0, home),
            ("B", 1.0, home),
            ("C", 1.0, home),
            ("Rival", 0.8, away),
            ("Rival Two", 0.8, away),
        ):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=10, on_team_id=team.espn_team_id, season=SEASON)
            projected(session, who, 70, _scaled(STARTER, factor), season=SEASON)
            held(session, team, first, who, 1, season=SEASON)
        games(session, 10, EVERY_DAY, season=SEASON)
        # A season nobody listened to: teams and settings, no schedule.
        league_season(session, season=QUIET_SEASON, days_per_period=7)
        # 2027 before its auction: a schedule and a roster on every day, all
        # of it ESPN's pre-draft feed.
        undrafted_season(session, days_per_period=7)
        games(session, 10, EVERY_DAY, season=UNDRAFTED)
        session.commit()
    yield scoring_factory


@pytest.fixture(autouse=True)
def before_the_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    """The morning the ghost rosters were found, so 2027's auction is ahead."""
    monkeypatch.setattr(drafted, "CLOCK", lambda: BEFORE_THE_AUCTION)


@pytest.fixture
def client(seeded: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app()

    def override() -> Iterator[Session]:
        with seeded() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def league_url(season: int = SEASON) -> str:
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/projected"


def team_url(team: int = HOME, season: int = SEASON) -> str:
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/projected"


def test_the_league_route_answers_for_every_team(client: TestClient) -> None:
    body = client.get(league_url(), params={"today": 1}).json()

    assert body["league_id"] == LEAGUE_ID
    assert body["season"] == SEASON
    assert body["as_of"] == 1
    assert body["matchup_period"] == 1
    assert body["periods"] == [1, 2]
    assert {team["espn_team_id"] for team in body["teams"]} == {HOME, AWAY}
    assert body["stored"] is False, "nothing stored yet, so it was built live"

    for team in body["teams"]:
        assert len(team["weeks"]) == 2
        assert sum(team["finishes"]) == pytest.approx(1.0)
        assert 0.0 <= team["playoff_odds"] <= 1.0
        # Banked plus expected is what the page prints as projected.
        assert team["projected_record"][0] == pytest.approx(
            team["banked_won"] + team["expected_won"]
        )
        assert team["projected_record"][1] == pytest.approx(
            team["banked_lost"] + team["expected_lost"]
        )


def test_the_two_sides_of_a_week_are_the_same_week(client: TestClient) -> None:
    """One matchup, two rows: the chances mirror and the totals swap over."""
    body = client.get(league_url(), params={"today": 1}).json()
    teams = {team["espn_team_id"]: team for team in body["teams"]}
    mine = teams[HOME]["weeks"][0]
    theirs = teams[AWAY]["weeks"][0]

    assert mine["opponent_espn_team_id"] == AWAY
    assert theirs["opponent_espn_team_id"] == HOME
    assert mine["in_play"] is True
    for category, p in mine["probabilities"].items():
        assert theirs["probabilities"][category] == pytest.approx(1.0 - p)
    assert mine["projected"] == theirs["opponent_projected"]
    # Every contested category goes to one side or the other, so the two
    # expectations add to the number of them. (This fixture posts no FG%/FT%
    # rows, so the league has measured seven, not nine.)
    contested = len(mine["probabilities"])
    assert mine["expected_wins"] + theirs["expected_wins"] == pytest.approx(contested)


def test_the_team_route_is_the_league_answer_narrowed(client: TestClient) -> None:
    whole = client.get(league_url(), params={"today": 1}).json()
    slice_ = client.get(team_url(), params={"today": 1}).json()

    assert len(slice_["teams"]) == 1
    assert slice_["teams"][0]["espn_team_id"] == HOME
    mine = next(team for team in whole["teams"] if team["espn_team_id"] == HOME)
    assert slice_["teams"][0] == mine, "the slice is the league answer, not a second one"
    # Everything outside `teams` is the league's and travels with the slice.
    assert slice_["tiebreak"] == whole["tiebreak"]
    assert slice_["periods"] == whole["periods"]
    assert slice_["calibration_short"] == whole["calibration_short"]


def test_every_answer_carries_its_record_and_its_source(client: TestClient) -> None:
    """A tool, not gospel: the forecast's own record travels with it."""
    body = client.get(league_url(), params={"today": 1}).json()

    assert body["calibration_note"] == CALIBRATION_NOTE
    # The same record in one line, for a page that prints the projected
    # finish on one line and keeps the whole note a tap away. Verbatim from
    # the constant, so the two can never say different numbers.
    # Verbatim from the constant, whose own number is guarded against
    # `RECORD_ERROR` in tests/test_inseason_projected.py, so there is one
    # place the sentence and the figure in it can disagree and it is not here.
    assert body["calibration_short"] == SHORT_NOTE
    assert body["source_note"]
    assert body["basis"].startswith("rosters and box scores as of scoring period 1")
    assert body["n_sims"] > 0
    assert body["seed"] > 0
    assert body["playoffs_projected"] is False
    assert "seeding" in body["playoff_note"]
    # No verdict words anywhere in the payload.
    text = str(body).lower()
    for word in ("recommend", "should", "must", "verdict", "guaranteed"):
        assert word not in text


def test_the_day_moves_the_week_being_played(client: TestClient) -> None:
    """`?today=` picks the day, and so the period and what is left of it."""
    early = client.get(league_url(), params={"today": 1}).json()
    later = client.get(league_url(), params={"today": 5}).json()

    assert early["teams"][0]["weeks"][0]["days_remaining"] == 7
    assert later["teams"][0]["weeks"][0]["days_remaining"] == 3
    # The second week is untouched by which day of the first one it is.
    assert later["periods"] == [1, 2]

    next_week = client.get(league_url(), params={"today": 8}).json()
    assert next_week["matchup_period"] == 2
    assert next_week["periods"] == [2]


def test_a_season_with_nothing_to_build_from_says_so(client: TestClient) -> None:
    """Not a 409 since 2026-09-25: a 200 with `readiness`, and no table."""
    answer = client.get(league_url(season=QUIET_SEASON))
    assert answer.status_code == 200
    body = answer.json()
    assert body["readiness"]["missing"] == [NO_SCHEDULE, NO_ROSTER]
    assert "nothing to build" in body["readiness"]["note"]
    assert body["teams"] == [] and body["periods"] == []
    assert body["as_of"] is None, "no schedule, so no day to be about"


def test_the_standings_before_the_auction_project_nobody(client: TestClient) -> None:
    """The league route is the Standings page's projected tab and the league
    week page's chances: before the draft, readiness and an empty table."""
    answer = client.get(league_url(season=UNDRAFTED), params={"today": 1})

    assert answer.status_code == 200
    body = answer.json()
    assert body["readiness"] == {"ready": False, "missing": [NOT_DRAFTED], "note": AUCTION_NOTE}
    assert (body["league_id"], body["season"], body["as_of"]) == (LEAGUE_ID, UNDRAFTED, 1)
    assert body["as_of_date"] == "2025-10-21"
    assert body["teams"] == [] and body["periods"] == []
    for empty in ("matchup_period", "n_sims", "seed", "source_note", "basis", "playoff_note"):
        assert body[empty] is None, empty
    # The method's published record is true of any season, and says nothing false here.
    assert body["calibration_note"] == CALIBRATION_NOTE
    assert body["stored"] is False


def test_one_teams_slice_before_the_auction_is_the_same_answer(client: TestClient) -> None:
    """The team route used to 409 when its team was missing from the table;
    before the draft the table is empty on purpose, and it says why."""
    answer = client.get(team_url(season=UNDRAFTED), params={"today": 1})

    assert answer.status_code == 200
    assert answer.json() == client.get(league_url(season=UNDRAFTED), params={"today": 1}).json()


def test_a_projected_season_carries_no_readiness_field(client: TestClient) -> None:
    for where in (league_url(), team_url()):
        assert "readiness" not in client.get(where, params={"today": 1}).json()


def test_a_team_that_is_not_in_the_season_is_a_404(client: TestClient) -> None:
    assert client.get(team_url(team=99), params={"today": 1}).status_code == 404
