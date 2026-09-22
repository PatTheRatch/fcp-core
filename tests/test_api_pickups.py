"""The recommender's routes, over a seeded league: the day, the week, the season.

One small season is built once for the module: a four-man roster on a
three-slot lineup, two men on the wire, and an NBA schedule every one of
them plays every day. The routes are then asked the same questions the CLIs
ask, plus the two refusals that matter -- a team that does not exist, and a
season the listener has never run for.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.main import create_app
from app.pickups.bids import clear_cache
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    configure,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

SEASON = 2026
QUIET_SEASON = 2025
HOME, AWAY, NOBODY = 1, 2, 99

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


#: What the two sides have posted so far. These two lines are also the only
#: evidence `category_distributions` has for this league, so they are what
#: the season's opponent spread ends up being measured from.
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


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in STARTER.items()}


def scaled_totals(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in POSTED.items()}


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        ls, (home, away), (first, _) = league_season(session, season=SEASON, days_per_period=7)
        configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        matchup(session, first, home, away, {home: POSTED, away: scaled_totals(0.8)})
        for name, factor, team in (
            ("A", 1.0, home),
            ("B", 1.0, home),
            ("C", 1.0, home),
            ("Weak", 0.5, home),
            ("Rival", 1.0, away),
        ):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=10, on_team_id=team.espn_team_id, season=SEASON)
            projected(session, who, 70, scaled(factor), season=SEASON)
            held(session, team, first, who, 1, season=SEASON)
        for name, factor, pro_team in (("Star", 1.4, 20), ("Scrub", 0.2, 21)):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=pro_team, on_team_id=0, season=SEASON)
            projected(session, who, 70, scaled(factor), season=SEASON)
            on_the_wire(session, ls, who)
        for pro_team in (10, 20, 21):
            games(session, pro_team, EVERY_DAY, season=SEASON)
        # A season nobody listened to: settings and teams, no schedule and
        # no snapshots, which is what a report cannot be built from.
        league_season(session, season=QUIET_SEASON, days_per_period=7)
        session.commit()
    clear_cache()
    yield scoring_factory


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


def url(team: int = HOME, season: int = SEASON, which: str = "stream") -> str:
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/pickups/{which}"


def today_url(team: int = HOME, season: int = SEASON) -> str:
    """The day's lineup. Not under /pickups/: it is not a pickup."""
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/today"


def test_the_stream_route_reports_the_week_and_the_moves(client: TestClient) -> None:
    body = client.get(url(), params={"today": 1}).json()

    assert body["espn_team_id"] == HOME
    assert body["matchup_period"] == 1
    assert body["scoring_periods_remaining"] == [1, 2, 3, 4, 5, 6, 7]
    assert body["opponent_espn_team_id"] == AWAY
    assert body["faab_remaining"] == 100
    assert body["ir_slot_free"] is False
    assert (body["adds_used"], body["adds_budget"], body["adds_left"]) == (0, 7, 7)
    assert isinstance(body["recommended"], list), "a plan of moves, in the order to make them"
    assert len(body["recommended"]) <= body["adds_left"]
    assert body["moves"], "both free agents are legal pickups"
    names = {move["add"]["name"] for move in body["moves"]}
    assert names == {"Star", "Scrub"}
    star = next(move for move in body["moves"] if move["add"]["name"] == "Star")
    assert star["add"]["espn_player_id"] > 0, "players go out as ESPN ids"
    assert star["kind"] in ("swap", "add")
    assert isinstance(star["clears_hurdle"], bool)


def test_the_stream_route_carries_the_judgement_and_the_projected_record(
    client: TestClient,
) -> None:
    """Section 4.3's second pass: a caller sees both horizons and the record."""
    body = client.get(url(), params={"today": 1}).json()

    outlook = body["outlook"]
    assert outlook["delta_total"] == 0.0, "no move, no change"
    assert outlook["record_without"] == outlook["record_with"]
    assert sum(outlook["record_without"]) == pytest.approx(9 * (outlook["weeks_remaining"]))
    assert outlook["replacement"] > 0.0

    star = next(move for move in body["moves"] if move["add"]["name"] == "Star")
    judgement = star["judgement"]
    assert star["net"] == pytest.approx(judgement["delta_total"])
    assert judgement["delta_total"] == pytest.approx(
        judgement["delta_week"] + judgement["delta_season_per_week"] * judgement["weeks_remaining"]
    )
    moved = judgement["record_with"][0] - judgement["record_without"][0]
    assert moved == pytest.approx(round(judgement["delta_total"], 1), abs=0.11)
    assert judgement["measured"] is True


def test_the_season_route_reports_the_drops_and_the_churn(client: TestClient) -> None:
    body = client.get(url(which="season"), params={"today": 1}).json()

    assert body["espn_team_id"] == HOME
    assert body["today"] == 1
    assert body["last_scoring_period"] == 14
    assert body["weeks_remaining"] == 2.0
    assert body["pool_size"] == 2
    assert body["drops"][0]["player"]["name"] == "Weak"
    assert body["drops"][0]["replacement"]["name"] == "Star"
    assert body["best_swap"]["out"][0]["name"] == "Weak"
    assert body["best_swap"]["into"][0]["name"] == "Star"
    assert body["best_swap"]["costs_faab"] is True
    assert body["churn"] == {"adds": 0, "days": 14, "finding": body["churn"]["finding"]}
    assert (body["adds_used"], body["adds_budget"], body["adds_left"]) == (0, 7, 7)
    assert "r = -0.63" in body["churn"]["finding"]
    assert body["stashes"] == []
    swap = body["best_swap"]
    assert swap["net"] == pytest.approx(swap["judgement"]["delta_total"])
    assert swap["judgement"]["per_week"] == pytest.approx(
        swap["judgement"]["delta_total"] / (swap["judgement"]["weeks_remaining"] + 1.0)
    )
    assert swap["clears_hurdle"] is (swap["judgement"]["per_week"] >= swap["hurdle"])
    assert body["outlook"]["record_without"] == body["outlook"]["record_with"]


def test_the_day_defaults_to_the_calendars_own(client: TestClient) -> None:
    body = client.get(url(which="season")).json()

    assert body["today"] >= 1, "the calendar names a day without one being passed"
    assert body["last_scoring_period"] == 14


def test_the_today_route_reports_the_lineup_and_says_where_it_came_from(
    client: TestClient,
) -> None:
    """The morning question, as the page and the CLI ask it.

    The seeded roster is four men on a three-place lineup, all playing every
    day, so the lineup is full, the fourth is outranked, and the team's own
    stored lineup for the day is the same men -- nothing to fix.
    """
    body = client.get(today_url(), params={"today": 1}).json()

    assert body["espn_team_id"] == HOME
    assert body["today"] == 1 and body["matchup_period"] == 1
    assert body["teams_playing"] == 3, "the three NBA teams the seeded men play for"
    assert [place["slot"] for place in body["lineup"]] == ["G", "F", "UT"]
    assert body["starts"] == 3
    seated = [place["player"]["name"] for place in body["lineup"]]
    assert set(seated) < {"A", "B", "C", "Weak"}
    assert "Weak" not in seated, "the worst of four for three places"
    for place in body["lineup"]:
        assert place["player"]["game"]["opponent_pro_team_id"] == 99
        assert place["player"]["status"] == "healthy"
        assert place["player"]["plays"] is True
        assert place["player"]["espn_player_id"] > 0, "ESPN ids, as everywhere"
    assert [man["player"]["name"] for man in body["benched"]] == ["Weak"]
    assert body["benched"][0]["reason"] == "outranked"
    assert body["idle"] == [] and body["injured_reserve"] == []
    assert body["actual_known"] is True
    assert body["fix"] == [], "what they set fills as much of the lineup as anything could"
    assert body["projected"]["PTS"] > 0
    assert "ESPN" in body["source_note"]


def test_an_unknown_team_is_404(client: TestClient) -> None:
    for which in ("stream", "season"):
        response = client.get(url(team=NOBODY, which=which), params={"today": 1})
        assert response.status_code == 404
        assert "team 99" in response.json()["detail"]
    unknown = client.get(today_url(team=NOBODY), params={"today": 1})
    assert unknown.status_code == 404 and "team 99" in unknown.json()["detail"]


def test_a_season_with_nothing_to_report_on_is_409(client: TestClient) -> None:
    """Settings and teams and nothing else: no schedule, and no roster anywhere.

    Not the same thing as a season the listener never ran for, which is
    every played season and now reports perfectly well off its lineup days
    (`tests/test_api_pages.py`). The refusal names both things it wanted.
    """
    asked = [url(season=QUIET_SEASON, which="stream"), url(season=QUIET_SEASON, which="season")]
    asked.append(today_url(season=QUIET_SEASON))
    for where in asked:
        response = client.get(where, params={"today": 1})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert f"season {QUIET_SEASON} has nothing to build a pickup report from" in detail
        assert "no NBA schedule is stored" in detail
        assert "no roster can be read" in detail


def test_an_unknown_season_is_still_404(client: TestClient) -> None:
    assert client.get(url(season=1999)).status_code == 404
