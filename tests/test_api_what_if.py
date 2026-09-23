"""The what-if route, on the same small league the trade routes use.

What is pinned here: that the route answers with the engine's own numbers and
not a second computation; that a change that cannot be made is a sentence a
manager can act on rather than a stack trace; that `?today=` is carried, so
the rehearsal and the calibration pages can replay a day; and that the finish
travels with the noise on its own odds and the record of the forecast behind
them, because a number without either is the failure this whole design exists
to prevent.
"""

from collections.abc import Iterable, Iterator, Mapping

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api import access
from app.api.deps import get_session
from app.db.models import LeagueSeason, Player, Team
from app.inseason.projected_calibration import SHORT_NOTE
from app.inseason.what_if import FINISH_IS_A_SECOND_LENS, what_if
from app.main import create_app
from app.pickups.bids import clear_cache
from app.pickups.projection import clear_cache as clear_lines
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    played,
    projected,
    snapshot,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

SEASON = 2026
UNDRAFTED = 2027
HOME, AWAY = 1, 2
PERIODS = 3
SEASON_DAYS = list(range(1, PERIODS * 7 + 1))
TODAY = 8
LATER = 9

STARTER: Mapping[str, float] = {
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
    "FG%": 235 / 500,
    "FT%": 0.78,
}


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in STARTER.items()}


def posted(factor: float) -> dict[str, float]:
    out = {key: value * factor for key, value in POSTED.items() if not key.endswith("%")}
    out["FG%"] = out["FGM"] / out["FGA"]
    out["FT%"] = out["FTM"] / out["FTA"]
    return out


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        clear_schedule(session)
        ls, (home, away), periods = league_season(
            session,
            season=SEASON,
            periods=PERIODS,
            regular_season_periods=PERIODS,
            days_per_period=7,
        )
        configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        # One of the two makes the playoffs, so the odds are a real number
        # with a real sampling band on it rather than a certainty.
        ls.playoff_team_count = 1
        matchup(session, periods[0], home, away, {home: posted(1.0), away: posted(0.8)})
        matchup(session, periods[1], home, away)
        matchup(session, periods[2], home, away)
        games(session, 10, SEASON_DAYS)
        games(session, 20, SEASON_DAYS)

        def rostered(team: Team, name: str, per_game: Mapping[str, float], days: tuple[int, ...]):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(
                session,
                who,
                pro_team_id=10 if team is home else 20,
                on_team_id=int(team.espn_team_id),
            )
            projected(session, who, 70, per_game, season=SEASON)
            for day in range(1, TODAY):
                played(session, who, day, 30.0, per_game, season=SEASON)
            for day in days:
                held(session, team, periods[1], who, day, season=SEASON)
            return who

        held_days = (1, TODAY, LATER)
        for name in ("HomeA", "HomeB"):
            rostered(home, name, STARTER, held_days)
        rostered(home, "HomeWeak", scaled(0.3), held_days)
        # In Home's lineup on the later day alone: a change judged on day 8
        # must not be able to drop him, which is the no-look-ahead claim.
        rostered(home, "HomeLater", STARTER, (LATER,))
        for name in ("AwayA", "AwayB", "AwayC"):
            rostered(away, name, STARTER, held_days)

        for name, factor in (("Wire", 1.4), ("Spare", 0.35)):
            free = player(session, name)
            eligible(session, free, ANY, "PG")
            snapshot(session, free, pro_team_id=20, on_team_id=0)
            projected(session, free, 70, scaled(factor), season=SEASON)
            for day in range(1, TODAY):
                played(session, free, day, 30.0, scaled(factor), season=SEASON)
            on_the_wire(session, ls, free)

        league_season(session, season=UNDRAFTED, periods=1, days_per_period=7)
        session.commit()
    clear_cache()
    clear_lines()
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


@pytest.fixture
def session(seeded: sessionmaker[Session]) -> Iterator[Session]:
    with seeded() as open_session:
        yield open_session


def url(team: int = HOME, season: int = SEASON) -> str:
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/what-if"


def ids(session: Session, *names: str) -> tuple[int, ...]:
    return tuple(
        session.scalars(select(Player.id).where(Player.name == name)).one() for name in names
    )


def espn(session: Session, *names: str) -> tuple[int, ...]:
    return tuple(
        session.scalars(select(Player.espn_player_id).where(Player.name == name)).one()
        for name in names
    )


def stored_season(session: Session, season: int) -> LeagueSeason:
    found = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# the answer is the engine's answer
# ---------------------------------------------------------------------------


def test_the_route_is_the_engines_payload_and_not_a_second_computation(
    client: TestClient, session: Session
) -> None:
    ls = stored_season(session, SEASON)
    (weak,) = ids(session, "HomeWeak")
    (wire,) = ids(session, "Wire")
    built = what_if(session, ls, HOME, TODAY, add=[wire], drop=[weak])

    body = client.get(
        url(),
        params={
            "drop": list(espn(session, "HomeWeak")),
            "add": list(espn(session, "Wire")),
            "today": TODAY,
        },
    ).json()

    assert (body["season"], body["today"]) == (built.season, built.today)
    assert body["espn_team_id"] == HOME and body["team_name"] == "Home"
    assert body["kind"] == built.kind == "swap"
    assert body["net"] == pytest.approx(built.net)
    assert body["clears_hurdle"] == built.clears_hurdle
    assert body["judgement"]["delta_week"] == pytest.approx(built.judgement.delta_week)
    assert body["judgement"]["delta_season_per_week"] == pytest.approx(
        built.judgement.delta_season_per_week
    )
    assert body["judgement"]["record_without"] == list(built.judgement.record_without)
    assert body["judgement"]["record_with"] == list(built.judgement.record_with)
    assert body["week"]["before"] == pytest.approx(dict(built.week.before))
    assert body["week"]["after"] == pytest.approx(dict(built.week.after))
    assert body["week"]["delta"] == pytest.approx(built.week.delta)
    assert body["finish"]["record_before"] == pytest.approx(list(built.finish.record_before))
    assert body["finish"]["playoff_odds_after"] == pytest.approx(built.finish.playoff_odds_after)
    assert [man["name"] for man in body["adds"]] == ["Wire"]
    assert [man["name"] for man in body["drops"]] == ["HomeWeak"]
    assert body["adds"][0]["espn_player_id"] == espn(session, "Wire")[0]


def test_a_player_id_nobody_has_heard_of_is_a_sentence(client: TestClient) -> None:
    """The ids come in as ESPN's, so one that names nobody is a typo, and a
    typo gets words rather than a strange number or a 500."""
    answer = client.get(url(), params={"drop": 0, "add": 0, "today": TODAY})
    assert answer.status_code == 422
    assert "There is no player" in answer.json()["detail"]


def test_the_finish_block_says_how_sure_it_is(client: TestClient, session: Session) -> None:
    body = client.get(
        url(),
        params={
            "drop": list(espn(session, "HomeWeak")),
            "add": list(espn(session, "Wire")),
            "today": TODAY,
        },
    ).json()
    finish = body["finish"]

    assert finish["calibration_note"] == SHORT_NOTE
    assert finish["language"] == FINISH_IS_A_SECOND_LENS
    assert "second lens, not a second bar" in finish["language"]
    assert finish["odds_band"] > 0
    assert "simulated seasons" in finish["noise_note"]
    assert finish["n_sims"] == 10_000
    assert sum(finish["seed_odds_before"]) == pytest.approx(1.0)
    assert sum(finish["seed_odds_after"]) == pytest.approx(1.0)
    assert finish["place_before"] in (1, 2) and finish["place_after"] in (1, 2)
    assert [week["period"] for week in finish["weeks"]] == [2, 3]
    # A genuine upgrade cannot cost the team categories over the rest of it.
    assert finish["record_after"][0] >= finish["record_before"][0]


def test_the_day_is_carried_and_a_roster_is_that_days(client: TestClient, session: Session) -> None:
    """`HomeLater` is in the lineup on day 9 and on no day before it."""
    (later,) = espn(session, "HomeLater")
    (wire,) = espn(session, "Wire")
    on_the_day = client.get(url(), params={"drop": later, "add": wire, "today": TODAY})
    assert on_the_day.status_code == 422
    assert "Home does not have HomeLater on its roster on day 8" in on_the_day.json()["detail"]

    afterwards = client.get(url(), params={"drop": later, "add": wire, "today": LATER})
    assert afterwards.status_code == 200
    assert afterwards.json()["today"] == LATER


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


def test_every_refusal_is_a_sentence_and_never_a_stack_trace(
    client: TestClient, session: Session
) -> None:
    (wire,) = espn(session, "Wire")
    (spare,) = espn(session, "Spare")
    (weak,) = espn(session, "HomeWeak")
    (theirs,) = espn(session, "AwayA")
    cases = [
        ({"today": TODAY}, "Name at least one man"),
        ({"drop": theirs, "add": wire, "today": TODAY}, "Home does not have AwayA"),
        ({"drop": weak, "add": theirs, "today": TODAY}, "AwayA is not a free agent"),
        ({"add": [wire, spare], "today": TODAY}, "the roster holds 4"),
        ({"drop": weak, "today": TODAY}, "a roster place short"),
    ]
    for params, said in cases:
        answer = client.get(url(), params=params)
        assert answer.status_code == 422, params
        detail = answer.json()["detail"]
        assert said in detail, params
        assert "Traceback" not in detail


def test_a_season_with_nothing_to_judge_from_is_told_so(client: TestClient) -> None:
    """2027 before its draft: the pickup routes' own 409, word for word."""
    answer = client.get(url(season=UNDRAFTED), params={"add": 1})
    assert answer.status_code == 409
    said = answer.json()["detail"]
    assert f"season {UNDRAFTED} has nothing to build a pickup report from" in said


def test_the_route_is_on_the_app_under_the_teams_paid_layer(client: TestClient) -> None:
    """The same paid team layer the week plan and the trade report declare."""
    wanted = "/leagues/{league_id}/seasons/{season}/teams/{team_id}/what-if"

    def walk(routes: Iterable[object]) -> Iterator[APIRoute]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "original_router"):
                yield from walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                yield from walk(route.routes)

    found = next(route for route in walk(client.app.routes) if route.path == wanted)  # type: ignore[attr-defined]
    declared = [
        dependency.call
        for dependency in found.dependant.dependencies
        if dependency.call in access.CHECKS
    ]
    assert declared == [access.require_team_plan]
