"""API tests.

These run against the real test database with a small season ingested
through the real ingest, rather than against hand-built rows. That way the
routes are exercised over data shaped exactly like production data,
including the awkward parts: a bye, a benched player, and a day with no
stat line.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.db.session import make_engine, make_session_factory
from app.ingest import ingest_season
from app.main import create_app
from tests.fakes import (
    BOX_LINE,
    attach_draft,
    attach_transactions,
    fake_box,
    fake_card,
    fake_pick,
    fake_player,
    fake_team,
    fake_transaction,
    league_with_days,
    owner_dict,
    tx_item,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

LEAGUE_ID = 3853870
SEASON = 2026


def _seeded_league() -> Any:
    """A two-team season with the edge cases the routes have to survive.

    Period 1 is a contested matchup over two days. Period 2 is a playoff
    period in which team 3 has a bye. Kawhi is benched on day 2 and scores
    40 there, which is what the bench report should find.
    """
    home = fake_team(3, "Through The Wire", owners=[owner_dict("g-pat", "Patrick")])
    away = fake_team(21, "Load Management", owners=[owner_dict("g-a"), owner_dict("g-b")])

    day1 = fake_box(
        home,
        away,
        home_lineup=[
            fake_player(6450, "Kawhi Leonard", slot="PG"),
            fake_player(4871144, "Alperen Sengun", slot="UT"),
        ],
        away_lineup=[],
        home_stats={
            "PTS": {"value": 52.0, "result": "WIN"},
            "FGM": {"value": 20.0, "result": None},
        },
        away_stats={
            "PTS": {"value": 40.0, "result": "LOSS"},
            "FGM": {"value": 15.0, "result": None},
        },
    )
    day2 = fake_box(
        home,
        away,
        home_lineup=[
            fake_player(6450, "Kawhi Leonard", slot="BE"),
            fake_player(4871144, "Alperen Sengun", slot="PG"),
        ],
        away_lineup=[],
    )
    bye = fake_box(
        home,
        0,
        winner="UNDECIDED",
        home_wins=0,
        away_wins=0,
        home_lineup=[fake_player(6450, "Kawhi Leonard", slot="PG")],
        home_stats={"PTS": {"value": 30.0, "result": None}},
    )

    espn = league_with_days(
        teams=[home, away],
        boxes={1: [day1], 2: [bye]},
        days={1: {1: [day1], 2: [day2]}, 2: {3: [bye]}},
        windows={1: ["1", "2"], 2: ["3"]},
        reg_season_count=1,
        matchup_period_count=2,
        cards={
            6450: fake_card(
                6450,
                "Kawhi Leonard",
                {1: dict(BOX_LINE, PTS=12.0), 2: dict(BOX_LINE, PTS=40.0), 3: None},
            ),
            4871144: fake_card(
                4871144,
                "Alperen Sengun",
                {1: dict(BOX_LINE, PTS=30.0), 2: dict(BOX_LINE, PTS=10.0)},
            ),
        },
    )
    attach_draft(
        espn,
        [
            # Both already exist as rostered players. An existing player keeps
            # the name we know them by; the draft does not rename anyone.
            fake_pick(1, 1, 6450, "Kawhi Leonard", team=home, nominated_by=away, bid=100),
            fake_pick(1, 2, 4871144, "Alperen Sengun", team=away, nominated_by=away, bid=5),
        ],
    )
    # Two teams bid on the same player on day 2; one wins, one fails.
    return attach_transactions(
        espn,
        {
            2: [
                fake_transaction(
                    "tx-win",
                    team_id=3,
                    bid=17,
                    status="EXECUTED",
                    items=[tx_item(555, "ADD", to_team=3), tx_item(6450, "DROP", from_team=3)],
                ),
                fake_transaction(
                    "tx-lose",
                    team_id=21,
                    bid=9,
                    status="FAILED_INVALIDPLAYERSOURCE",
                    items=[tx_item(555, "ADD", to_team=21)],
                ),
            ]
        },
        {555: "Wanted Guy"},
    )


@pytest.fixture(scope="module")
def seeded(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    """A migrated test database holding one small, fully ingested season."""
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")

    engine = make_engine(test_database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        ingest_season(session, _seeded_league())
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def client(seeded: sessionmaker[Session]) -> Iterator[TestClient]:
    """A client whose requests use the seeded test database, never DATABASE_URL."""
    app = create_app()

    def override() -> Iterator[Session]:
        with seeded() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_still_works(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_leagues_lists_stored_seasons(client: TestClient) -> None:
    body = client.get("/leagues").json()
    assert body == [{"espn_league_id": LEAGUE_ID, "seasons": [SEASON]}]


def test_season_detail_includes_categories(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}").json()
    assert body["name"] == "Patriot Games"
    assert body["scoring_type"] == "H2H_CATEGORY"
    # Playoffs included, so the total exceeds the regular season count.
    assert body["total_matchup_periods"] > body["regular_season_periods"]
    assert [c["abbreviation"] for c in body["categories"]][:2] == ["FT%", "REB"]


def test_unknown_league_and_season_are_404(client: TestClient) -> None:
    assert client.get("/leagues/999/seasons/2026").status_code == 404
    assert client.get(f"/leagues/{LEAGUE_ID}/seasons/1999").status_code == 404
    assert client.get("/leagues/999/seasons").status_code == 404


def test_teams_carry_their_owners(client: TestClient) -> None:
    teams = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams").json()
    by_id = {t["espn_team_id"]: t for t in teams}
    assert by_id[3]["name"] == "Through The Wire"
    assert [o["first_name"] for o in by_id[3]["owners"]] == ["Patrick"]
    assert len(by_id[21]["owners"]) == 2, "a team can have more than one owner"

    # The response carries our id, never ESPN's SWID GUID.
    owner = by_id[3]["owners"][0]
    assert isinstance(owner["owner_id"], int)
    assert "espn_owner_id" not in owner


def test_standings_show_matchup_record_and_category_tally_separately(client: TestClient) -> None:
    """The two records mean different things, and ESPN only reports one."""
    standings = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/standings").json()
    winner = next(s for s in standings if s["espn_team_id"] == 3)
    loser = next(s for s in standings if s["espn_team_id"] == 21)

    assert (winner["matchups_won"], winner["matchups_lost"]) == (1, 0)
    assert (loser["matchups_won"], loser["matchups_lost"]) == (0, 1)
    # Category tallies come from ESPN and are a different measure entirely.
    assert winner["categories_won"] == 95
    assert standings[0]["espn_team_id"] == 3, "sorted by matchups won"


def test_standings_exclude_byes(client: TestClient) -> None:
    """An unopposed matchup is not a win."""
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/standings", params={"include_playoffs": True}
    ).json()
    team3 = next(s for s in body if s["espn_team_id"] == 3)
    assert team3["matchups_won"] == 1, "the playoff bye must not become a second win"


def test_periods_report_their_day_windows(client: TestClient) -> None:
    periods = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/periods").json()
    first, second = periods[0], periods[1]
    assert (first["first_scoring_period"], first["final_scoring_period"]) == (1, 2)
    assert first["is_playoff"] is False
    assert second["is_playoff"] is True
    assert first["matchup_count"] == 1


def test_matchups_include_per_category_detail(client: TestClient) -> None:
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/matchups", params={"period": 1}
    ).json()
    assert body["total"] == 1
    matchup = body["items"][0]
    assert matchup["winner"] == "HOME"
    assert matchup["home"]["name"] == "Through The Wire"

    stats = {s["abbreviation"]: s for s in matchup["home"]["statistics"]}
    assert stats["PTS"]["value"] == 52.0
    assert stats["PTS"]["is_scored_category"] is True
    assert stats["FGM"]["is_scored_category"] is False, "a component, not a scored category"


def test_a_bye_reports_no_away_side(client: TestClient) -> None:
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/matchups", params={"period": 2}
    ).json()
    matchup = body["items"][0]
    assert matchup["away"] is None
    assert matchup["winner"] == "UNDECIDED"


def test_lineups_show_the_slot_and_that_days_production(client: TestClient) -> None:
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/3/lineups", params={"scoring_period": 2}
    ).json()
    rows = {r["player_name"]: r for r in body["items"]}
    assert rows["Kawhi Leonard"]["slot"] == "BE"
    assert rows["Kawhi Leonard"]["started"] is False
    assert rows["Kawhi Leonard"]["points"] == 40.0, "benched, and still scored 40"
    assert rows["Alperen Sengun"]["started"] is True


def test_lineups_can_be_filtered_to_the_bench(client: TestClient) -> None:
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/3/lineups", params={"started": False}
    ).json()
    assert body["total"] >= 1
    assert all(r["started"] is False for r in body["items"])


def test_a_lineup_day_without_a_game_reports_null_production(client: TestClient) -> None:
    """Kawhi is rostered on day 3 but has no stat line for it."""
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/3/lineups", params={"scoring_period": 3}
    ).json()
    row = next(r for r in body["items"] if r["player_name"] == "Kawhi Leonard")
    assert row["played"] is False
    assert row["points"] is None


def test_lineups_are_paged(client: TestClient) -> None:
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/3/lineups", params={"limit": 1}
    ).json()
    assert len(body["items"]) == 1
    assert body["total"] > 1, "total ignores the limit"


def test_bench_report_finds_the_costly_call(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/3/bench").json()
    assert body["name"] == "Through The Wire"
    assert body["bench_points"] == 40.0
    assert body["benched_games_of_20_plus"] == 1

    call = body["worst_calls"][0]
    assert call["player_name"] == "Kawhi Leonard"
    assert call["benched_points"] == 40.0
    assert call["best_starter_points"] == 10.0
    assert call["margin"] == 30.0


def test_unknown_team_is_404(client: TestClient) -> None:
    assert client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/999/lineups").status_code == 404


def test_player_search_and_game_log(client: TestClient) -> None:
    found = client.get("/players", params={"name": "kawhi"}).json()
    assert [p["name"] for p in found["items"]] == ["Kawhi Leonard"]

    log = client.get("/players/6450/games", params={"season": SEASON}).json()
    by_day = {g["scoring_period"]: g for g in log["items"]}
    assert by_day[2]["points"] == 40.0
    assert by_day[2]["opponent"] == "TOR"
    assert by_day[3]["played"] is False, "a fixture they took no part in"


def test_played_only_drops_the_blank_days(client: TestClient) -> None:
    log = client.get("/players/6450/games", params={"played_only": True}).json()
    assert all(g["played"] for g in log["items"])
    assert log["total"] == 2


def test_unknown_player_is_404(client: TestClient) -> None:
    assert client.get("/players/1/games").status_code == 404
    assert client.get("/players/1").status_code == 404


def test_no_endpoint_leaks_the_espn_owner_guid(client: TestClient) -> None:
    """A blanket check, because the GUID is half of ESPN's cookie pair.

    Sweeps the routes that carry owner identity and asserts none of them
    returns anything shaped like the SWID GUID the seed data uses.
    """
    paths = [
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams",
        f"/leagues/{LEAGUE_ID}/owners",
        f"/leagues/{LEAGUE_ID}/head-to-head",
    ]
    for path in paths:
        body = client.get(path).text
        assert body, f"{path} returned nothing, so the check would pass vacuously"
        assert "g-pat" not in body, f"{path} leaked the owner GUID"
        assert "espn_owner_id" not in body, f"{path} exposes the GUID field"


def test_owner_ids_correlate_across_endpoints(client: TestClient) -> None:
    """The opaque id has to be worth having, not just safe."""
    teams = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams").json()
    owners = client.get(f"/leagues/{LEAGUE_ID}/owners").json()

    from_teams = {o["owner_id"] for t in teams for o in t["owners"]}
    from_records = {o["owner_id"] for o in owners}
    assert from_teams, "no owners came back from the teams route"
    assert from_teams <= from_records, "the same owner must have the same id everywhere"


def test_transactions_list_both_sides_of_a_move(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/transactions").json()
    assert body["total"] >= 1
    # Both the winning and losing claim are returned; pick the one that landed.
    claim = next(t for t in body["items"] if t["status"] == "EXECUTED")
    assert claim["bid_amount"] == 17
    moves = {i["item_type"]: i for i in claim["items"]}
    assert moves["ADD"]["player_name"] == "Wanted Guy"
    assert moves["ADD"]["from_team"] is None, "free agency is not a team"
    assert moves["ADD"]["to_team"] == "Through The Wire"


def test_transactions_can_be_filtered(client: TestClient) -> None:
    base = f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/transactions"
    executed = client.get(base, params={"status": "EXECUTED"}).json()
    assert executed["total"] >= 1
    assert all(t["status"] == "EXECUTED" for t in executed["items"])

    big = client.get(base, params={"min_bid": 15}).json()
    assert all(t["bid_amount"] >= 15 for t in big["items"])


def test_a_losing_bid_is_still_reported(client: TestClient) -> None:
    """The whole reason failed claims are stored."""
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/transactions",
        params={"status": "FAILED_INVALIDPLAYERSOURCE"},
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["bid_amount"] == 9


def test_contested_claims_name_the_winner_and_count_the_losers(client: TestClient) -> None:
    claims = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/contested-claims").json()
    assert claims, "the seed data has a contested player"
    fight = claims[0]
    assert fight["player_name"] == "Wanted Guy"
    assert fight["winning_team"] == "Through The Wire"
    assert fight["winning_bid"] == 17
    assert fight["losing_bids"] == 1
    assert fight["highest_losing_bid"] == 9


def test_the_draft_board_is_returned_in_pick_order(client: TestClient) -> None:
    picks = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/draft").json()
    assert [(p["round_num"], p["round_pick"]) for p in picks] == [(1, 1), (1, 2)]
    first = picks[0]
    assert first["player_name"] == "Kawhi Leonard"
    assert first["paid"] == 100
    assert first["team"] == "Through The Wire"
    assert first["nominated_by"] == "Load Management", "the nominator is not the buyer"


def test_draft_value_ranks_by_return_per_dollar(client: TestClient) -> None:
    """Kawhi cost 100 for 52 points; Sengun cost 5 for 40. Value is the ratio."""
    worst = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/draft-value", params={"order": "worst"}
    ).json()
    assert worst[0]["player_name"] == "Kawhi Leonard"
    assert worst[0]["paid"] == 100
    assert worst[0]["points_per_dollar"] < worst[-1]["points_per_dollar"]

    best = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/draft-value", params={"order": "best"}
    ).json()
    assert best[0]["player_name"] == "Alperen Sengun"


def test_draft_value_can_ignore_cheap_picks(client: TestClient) -> None:
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/draft-value", params={"min_paid": 50}
    ).json()
    assert [p["player_name"] for p in body] == ["Kawhi Leonard"]
