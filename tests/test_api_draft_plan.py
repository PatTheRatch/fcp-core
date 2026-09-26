"""The draft plan routes: the shape, the two gates, the marks and the drafted season.

The model's build is swapped for the small room of `tests/test_draft_plan.py`
(`plan_store.BUILDER`) and run on the request's thread (`plan_store.SYNC`),
so every answer here is the route's own logic over a plan that takes no
time. The league is built as rows: league 111, 2027 (auction ahead, teams 3
and 5, a stored BBM capture) and 2026 (drafted).

Accounts mode is switched on per app, as tests/test_access.py does, with
each person's own token: the site's owner (who owns the BBM captures) and
Alice both manage team 3, Bob manages team 5.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, api_tokens, memberships
from app.api.deps import get_session
from app.config import get_settings
from app.db.models import BBMCapture, League, LeagueSeason, Team, TeamManager
from app.draft import plan as engine
from app.draft import plan_store
from app.main import create_app
from tests.test_draft_plan import build, small_room

LEAGUE = 111
SEASON = 2027
OWNER = "owner@example.com"


def _season(league: League, season: int, drafted_at: dt.datetime) -> LeagueSeason:
    row = LeagueSeason(
        league=league,
        season=season,
        name="League 111",
        scoring_type="H2H_CATEGORY",
        team_count=2,
        regular_season_periods=1,
        total_matchup_periods=1,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        auction_budget=10,
        draft_type="AUCTION",
        drafted_at=drafted_at,
        draft_order=[5, 3],
        lineup_slots={"UT": 2},
        bench_slots=0,
        median_scoring=False,
        raw_settings={},
    )
    for tid in (3, 5):
        row.teams.append(Team(espn_team_id=tid, name=f"Team {tid}"))
    return row


def _claim(session: Session, email: str, team: int) -> None:
    user = accounts.get_or_create_user(session, email)
    league = session.scalars(select(League).where(League.espn_league_id == LEAGUE)).one()
    memberships.join_league(session, user.id, league.id, accounts.MEMBER_ROLE)
    team_pk = session.scalars(
        select(Team.id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .where(LeagueSeason.season == SEASON, Team.espn_team_id == team)
    ).one()
    session.add(
        TeamManager(
            user_id=user.id,
            team_id=team_pk,
            state="verified",
            how="approved",
            verified_at=dt.datetime.now(dt.UTC),
        )
    )


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        league = League(espn_league_id=LEAGUE)
        session.add(league)
        now = dt.datetime.now(dt.UTC)
        session.add(_season(league, SEASON, now + dt.timedelta(days=14)))
        session.add(_season(league, SEASON - 1, now - dt.timedelta(days=340)))
        session.add(
            BBMCapture(
                season=SEASON,
                value_type="total",
                captured_on=dt.date.today() - dt.timedelta(days=2),
                players=4,
                changed=4,
                dropped=0,
            )
        )
        session.flush()
        _claim(session, OWNER, 3)
        _claim(session, "alice@example.com", 3)
        _claim(session, "bob@example.com", 5)
        session.commit()
    yield scoring_factory


def fake_builder(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    source: engine.PoolSource,
    fan_team: str | None = None,
    must: dict[int, int | None] | None = None,
    **_: Any,
) -> engine.Plan:
    """The small room's plan, on the pool asked for: no database, no time."""
    room = small_room()
    plan = engine.plan_from_room(
        room,
        draft_at=league_season.drafted_at,
        order=[2, 1],
        source=source,
        source_detail="captured Sep 24" if source.kind == "bbm" else "",
        fan_team=fan_team,
        must=must,
        ceilings=engine.ceilings_inline,
    )
    plan.build_seconds = 0.1
    return plan


@pytest.fixture(autouse=True)
def fast_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_store, "BUILDER", fake_builder)
    monkeypatch.setattr(plan_store, "SYNC", True)
    plan_store.BUILDS.clear()
    plan_store.ROOMS.clear()
    plan_store.EFFECTIVE.clear()


def make_app(seeded: sessionmaker[Session], *, accounts_mode: bool) -> FastAPI:
    app = create_app()

    def override() -> Iterator[Session]:
        with seeded() as open_session:
            yield open_session

    app.dependency_overrides[get_session] = override
    if accounts_mode:
        settings = get_settings().model_copy(
            update={
                "fcp_auth_mode": "accounts",
                "fcp_owner_email": OWNER,
                "espn_league_id": LEAGUE,
                "fcp_tracked_team_id": 3,
                "fcp_billing_enabled": False,
            }
        )
        app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.fixture
def client(seeded: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(make_app(seeded, accounts_mode=False)) as test_client:
        yield test_client


def as_user(seeded: sessionmaker[Session], email: str) -> TestClient:
    with seeded() as session:
        user = accounts.get_or_create_user(session, email)
        token = api_tokens.mint(session, user.id, "test").token
        session.commit()
    test_client = TestClient(make_app(seeded, accounts_mode=True))
    test_client.headers["Authorization"] = f"Bearer {token}"
    return test_client


def url(team: int = 3, season: int = SEASON, tail: str = "") -> str:
    return f"/leagues/{LEAGUE}/seasons/{season}/teams/{team}/draft/plan{tail}"


@pytest.fixture(autouse=True)
def clean_marks(seeded: sessionmaker[Session]) -> Iterator[None]:
    yield
    from sqlalchemy import delete

    from app.db.models import DraftPlan, TeamReport

    with seeded() as session:
        session.execute(delete(DraftPlan))
        session.execute(delete(TeamReport))
        session.commit()


# -- the shape ----------------------------------------------------------------


def test_the_plan_carries_the_models_figures_and_the_marks_side_by_side(
    client: TestClient,
) -> None:
    got = client.get(url())
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["state"] == "ready"
    assert body["source"]["kind"] == "bbm" and body["source"]["age_days"] == 2
    assert body["auction_when"].endswith("ET")
    plan = body["plan"]
    assert plan["facts"] == {
        "pot": 20,
        "teams": 2,
        "budget": 10,
        "places": 2,
        "floor": 1,
        "nominate": 2,
        "cap": 6,
        "slack": 0.1,
    }
    man = next(p for p in plan["players"] if p["id"] == 2)
    assert {"going", "ceiling", "bid_to", "bbm_total", "nine", "sections"} <= set(man)
    assert body["effective"]["players"]["2"]["going"] == man["going"]
    assert body["marks"] == {} and body["yours"] == 0

    kept = client.put(url(tail="/marks"), json={"marks": [{"player_id": 2, "bid_up_to": 5}]})
    assert kept.status_code == 200, kept.text
    again = client.get(url()).json()
    assert again["marks"]["2"] == {"going_price": None, "bid_up_to": 5, "tag": "none", "note": ""}
    model = next(p for p in again["plan"]["players"] if p["id"] == 2)
    assert model["ceiling"] == man["ceiling"], "the model's figure is not overwritten"
    assert again["effective"]["players"]["2"]["ceiling"] == min(5, man["ceiling"])


def test_an_espn_plan_is_asked_for_by_name(client: TestClient) -> None:
    body = client.get(url(), params={"source": "espn"}).json()
    assert body["state"] == "ready" and body["source"]["kind"] == "espn"
    assert [c["source"] for c in body["choices"]] == ["bbm", "espn"]


# -- the marks ------------------------------------------------------------------


def test_marks_round_trip_whole_and_partial(client: TestClient) -> None:
    client.put(
        url(tail="/marks"),
        json={"marks": [{"player_id": 1, "going_price": 8, "tag": "target", "note": "watch"}]},
    )
    client.put(url(tail="/marks"), json={"marks": [{"player_id": 1, "tag": "must"}]})
    body = client.get(url()).json()
    assert body["marks"]["1"] == {
        "going_price": 8,
        "bid_up_to": None,
        "tag": "must",
        "note": "watch",
    }, "a field left out is left as it was"
    client.put(url(tail="/marks"), json={"marks": [{"player_id": 1, "going_price": None}]})
    assert client.get(url()).json()["marks"]["1"]["going_price"] is None, "null clears it"


def test_a_ceiling_and_a_going_price_are_checked(client: TestClient) -> None:
    client.get(url())  # a build, so the model's cap ($6) is known
    over = client.put(url(tail="/marks"), json={"marks": [{"player_id": 2, "bid_up_to": 7}]})
    assert over.status_code == 422 and "$6 cap" in over.json()["detail"]
    zero = client.put(url(tail="/marks"), json={"marks": [{"player_id": 2, "bid_up_to": 0}]})
    assert zero.status_code == 422
    dear = client.put(url(tail="/marks"), json={"marks": [{"player_id": 2, "going_price": 10}]})
    assert dear.status_code == 422 and "$9" in dear.json()["detail"]
    bad_tag = client.put(url(tail="/marks"), json={"marks": [{"player_id": 2, "tag": "avoid"}]})
    assert bad_tag.status_code == 422


def test_a_ladder_adds_up_to_the_budget_with_a_dollar_a_place(client: TestClient) -> None:
    short = client.put(url(tail="/marks"), json={"ladder": [5, 4]})
    assert short.status_code == 422 and "not the $10 budget" in short.json()["detail"]
    floor = client.put(url(tail="/marks"), json={"ladder": [10, 0]})
    assert floor.status_code == 422 and "at least $1" in floor.json()["detail"]
    places = client.put(url(tail="/marks"), json={"ladder": [10]})
    assert places.status_code == 422
    kept = client.put(url(tail="/marks"), json={"ladder": [3, 7]})
    assert kept.status_code == 200 and kept.json()["ladder"] == [7, 3], "largest first"
    assert kept.json()["cap"] == 7, "the cap follows his ladder: $7 and 10% slack"
    client.put(url(tail="/marks"), json={"ladder": None})
    assert client.get(url()).json()["ladder"] is None, "null is the model's ladder again"


def test_reset_puts_every_figure_back_and_keeps_the_tags(client: TestClient) -> None:
    before = client.get(url()).json()["effective"]
    client.put(
        url(tail="/marks"),
        json={
            "marks": [
                {"player_id": 2, "going_price": 9, "bid_up_to": 3, "tag": "target"},
                {"player_id": 3, "going_price": 1},
            ]
        },
    )
    changed = client.get(url()).json()
    assert changed["yours"] == 3 and changed["effective"]["yours"] == 3
    client.put(url(tail="/marks"), json={"reset": "figures"})
    after = client.get(url()).json()
    assert after["yours"] == 0 and after["marks"]["2"]["tag"] == "target"
    for key in ("players", "sections", "best"):
        assert after["effective"][key] == before[key]


def test_a_must_man_rebuilds_the_plan_around_him(client: TestClient) -> None:
    client.get(url())
    client.put(url(tail="/marks"), json={"marks": [{"player_id": 2, "tag": "must"}]})
    body = client.get(url()).json()
    assert body["rebuilding"] is None, "built at once here; the page shows the old one meanwhile"
    must = body["plan"]["must"]
    assert must["applied"] and must["prices"] == {"2": 4} and must["money_left"] == 6


def test_a_must_set_that_does_not_fit_is_said_and_the_plan_is_free(client: TestClient) -> None:
    client.put(
        url(tail="/marks"),
        json={
            "marks": [
                {"player_id": 1, "tag": "must", "going_price": 6},
                {"player_id": 2, "tag": "must", "going_price": 5},
            ]
        },
    )
    must = client.get(url()).json()["plan"]["must"]
    assert not must["applied"] and "over the $10 budget" in must["error"]


def test_the_fan_team_is_a_setting(client: TestClient) -> None:
    assert client.get(url(tail="/settings")).json()["fan_team"] is None
    wrong = client.put(url(tail="/settings"), json={"fan_team": "XYZ"})
    assert wrong.status_code == 422
    assert client.put(url(tail="/settings"), json={"fan_team": "cle"}).json()["fan_team"] == "CLE"
    body = client.get(url()).json()
    assert [s["key"] for s in body["plan"]["sections"] if s["key"] == "fan"] == ["fan"]
    assert body["plan"]["fan"][0]["id"] == 3


# -- a drafted season -------------------------------------------------------------


def test_a_drafted_season_says_when_and_where_the_board_is(client: TestClient) -> None:
    body = client.get(url(season=SEASON - 1)).json()
    assert body["state"] == "drafted"
    assert body["note"].startswith("The auction was held")
    assert body["draft_page"] == f"/l/{LEAGUE}/{SEASON - 1}/draft"


# -- the gates ------------------------------------------------------------------------


def test_another_teams_manager_is_refused(seeded: sessionmaker[Session]) -> None:
    with as_user(seeded, "bob@example.com") as bob:
        assert bob.get(url(team=3)).status_code == 403
        assert bob.put(url(team=3, tail="/marks"), json={}).status_code == 403
        assert bob.get(url(team=5)).status_code == 200


def test_a_bbm_plan_is_only_for_the_owner_of_the_source(seeded: sessionmaker[Session]) -> None:
    with as_user(seeded, "alice@example.com") as alice:
        body = alice.get(url()).json()
        assert body["state"] == "withheld"
        assert "Basketball Monster" in body["note"] and body["offer"] == "espn"
        assert "plan" not in body and "effective" not in body
        assert [c["source"] for c in body["choices"]] == ["espn"]
        espn = alice.get(url(), params={"source": "espn"}).json()
        assert espn["state"] == "ready"
        assert alice.post(url(tail="/rebuild")).status_code == 403
    with as_user(seeded, OWNER) as owner:
        assert owner.get(url()).json()["state"] == "ready"
