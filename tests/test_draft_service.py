"""The draft screen and what it reads: the page itself, and the pool behind it.

The screen's strips, its scarcity panel and its team standings are all sums
over the whole pool, so the service hands the pool over once (`GET /api/pool`)
and the page does the arithmetic. These are the tests for that: the shape of
what goes out, and that it goes out through the same gate as a card.

The page's own JavaScript is not testable here -- there is no browser in this
suite -- which is the reason it is small, defensive about missing numbers, and
built to render a room with no lines at all.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.draft.live import SCREEN_CATEGORIES, PlayerLine, Room, player_lines
from app.draft.optimizer import Candidate
from app.draft.room import DraftState
from app.draft.service import create_draft_app
from app.draft.session import DraftSession
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PlayerProjection

PTS = CategoryDistribution(
    abbreviation="PTS",
    mean=100.0,
    spread=20.0,
    lower_is_better=False,
    sample=100,
    basis_seasons=(2026,),
    period_days=7,
    era_scale=1.0,
)
NAMES = {1: "Nikola Jokic", 2: "Kawhi Leonard", 3: "Jalen Johnson", 4: "Myles Turner"}


def projection(player_id: int, points: float, games: float = 70.0) -> PlayerProjection:
    """A season as the database holds one: totals, with the shooting behind them."""
    return PlayerProjection(
        player_id=player_id,
        name=NAMES[player_id],
        games=games,
        totals={
            "PTS": points * games,
            "REB": 7.0 * games,
            "AST": 5.0 * games,
            "STL": 1.0 * games,
            "BLK": 0.5 * games,
            "3PM": 2.0 * games,
            "TO": 3.0 * games,
            "FGM": 8.0 * games,
            "FGA": 16.0 * games,
            "FTM": 4.0 * games,
            "FTA": 5.0 * games,
        },
        eligible=frozenset({"UT", "C"}),
        position="C",
    )


def make_room(
    *, lines: dict[int, PlayerLine] | None = None, projection_source: str = "espn"
) -> Room:
    projections = [
        projection(1, 26.0),
        projection(2, 20.0),
        projection(3, 18.0),
        projection(4, 14.0),
    ]
    candidates = [
        Candidate(
            player_id=p.player_id,
            name=p.name,
            price=price,
            weekly={"PTS": 90.0, "FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0},
            eligible=p.eligible,
            position=p.position,
        )
        for p, price in zip(projections, (12, 6, 5, 3), strict=True)
    ]
    teams = {1: "Through The Wire", 2: "Foxes ShutUpNDribble", 3: "Brighton Bears"}
    return Room(
        season=2027,
        state=DraftState.open(budget=20, roster_slots=2, teams=teams, me=1),
        candidates=candidates,
        distributions=[PTS],
        lineup=("UT", "UT"),
        limits={},
        names={c.name: c.player_id for c in candidates},
        team_names=teams,
        punt=(),
        restarts=1,
        pool_note="pool: ESPN 2026 projected",
        source_detail="2026 projected",
        board={c.player_id: c.price for c in candidates},
        projection_source=projection_source,
        lines=player_lines(projections) if lines is None else lines,
    )


def test_a_season_of_totals_becomes_the_per_game_line_a_page_draws() -> None:
    lines = player_lines([projection(1, 26.0, games=70.0), projection(2, 20.0, games=0.0)])

    assert 2 not in lines, "nobody expects him to play, so there is no line to draw"
    line = lines[1]
    assert isinstance(line, PlayerLine)
    assert line.games == 70.0
    assert line.per_game["PTS"] == pytest.approx(26.0)
    assert line.per_game["FG%"] == pytest.approx(0.5), "a rate, not a per-game count"
    assert line.per_game["FT%"] == pytest.approx(0.8)
    assert line.fga == pytest.approx(16.0) and line.fta == pytest.approx(5.0)
    assert list(line.per_game) == list(SCREEN_CATEGORIES), "one fixed order everywhere"


def test_a_percentage_with_no_attempts_is_nothing_rather_than_zero() -> None:
    dry = replace(
        projection(1, 10.0),
        totals={**projection(1, 10.0).totals, "FTM": 0.0, "FTA": 0.0},
    )

    assert player_lines([dry])[1].per_game["FT%"] is None


def test_the_screen_is_served_and_asks_for_the_pool() -> None:
    client = TestClient(create_draft_app(DraftSession(make_room())))

    page = client.get("/")

    assert page.status_code == 200
    assert "Draft Room" in page.text
    assert "/api/pool" in page.text, "the page reads the pool it shades from"
    assert "/api/events" in page.text, "and stays live on the stream"


def test_the_page_reopens_a_stream_a_phone_has_dropped() -> None:
    """iOS Safari drops the EventSource when the tab is backgrounded or the
    screen locks, and does not always say so: the page then looks live and is
    frozen. The page's answer is three wakeups, a "reconnecting…" pill and a
    poll of /api/state while the stream is down. There is no browser in this
    suite, so this only guards that the wiring is still in the file; the
    behaviour itself was driven in a real browser against a running rehearsal.
    """
    page = TestClient(create_draft_app(DraftSession(make_room()))).get("/").text

    for event in ("visibilitychange", "pageshow", "online"):
        assert event in page, f"the page has to reopen the stream on {event}"
    assert 'id="link"' in page, "the masthead needs somewhere to say it is reconnecting"
    assert "reconnecting…" in page
    assert "STREAM_POLL_MS" in page, "and it polls /api/state while the stream is down"


def test_the_pool_carries_a_line_a_price_and_who_bought_him() -> None:
    session = DraftSession(make_room())
    session.apply(1, 2, 12)
    client = TestClient(create_draft_app(session))

    pool = client.get("/api/pool").json()

    assert pool["source"] == "espn"
    assert pool["withheld"] is False and pool["withheld_note"] is None
    assert pool["categories"] == list(SCREEN_CATEGORIES)
    rows = {row["player_id"]: row for row in pool["players"]}
    assert set(rows) == {1, 2, 3, 4}

    sold = rows[1]
    assert sold["name"] == "Nikola Jokic" and sold["position"] == "C"
    assert sold["eligible"] == ["C", "UT"]
    assert sold["team_id"] == 2 and sold["price"] == 12, "drafted, and by whom"
    assert sold["games"] == 70.0
    assert set(sold["line"]) == set(SCREEN_CATEGORIES)
    assert sold["line"]["PTS"] == pytest.approx(26.0)
    assert sold["fga"] == pytest.approx(16.0) and sold["fta"] == pytest.approx(5.0)
    assert sold["board_price"] == 12
    assert sold["market_price"] is None, "nobody is still going to pay for a player already sold"

    unsold = rows[3]
    assert unsold["team_id"] is None and unsold["price"] is None
    assert unsold["market_price"] is not None, "what he will probably go for"


def test_a_room_with_no_stored_lines_still_answers_with_its_players() -> None:
    """A hand-built room -- and the screen degrades to names and money."""
    client = TestClient(create_draft_app(DraftSession(make_room(lines={}))))

    pool = client.get("/api/pool").json()

    assert [row["name"] for row in pool["players"]] == list(NAMES.values())
    assert all(row["line"] is None and row["games"] is None for row in pool["players"])
    assert pool["withheld"] is False


def test_a_gated_source_the_viewer_does_not_own_leaves_the_pool_with_names_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same seam the card has: today every viewer owns the source."""
    monkeypatch.setattr("app.draft.session.may_show", lambda source, viewer_owns_source: False)
    session = DraftSession(make_room(projection_source="bbm"))
    session.apply(1, 2, 12)
    client = TestClient(create_draft_app(session))

    pool = client.get("/api/pool").json()

    assert pool["withheld"] is True
    assert "not to be shared" in pool["withheld_note"]
    rows = {row["player_id"]: row for row in pool["players"]}
    assert rows[1]["name"] == "Nikola Jokic", "the names are ours to show"
    assert rows[1]["team_id"] == 2 and rows[1]["price"] == 12, "so is who bought him"
    assert rows[1]["position"] == "C" and rows[1]["eligible"] == ["C", "UT"]
    for row in pool["players"]:
        assert "line" not in row and "games" not in row
        assert "board_price" not in row and "market_price" not in row
