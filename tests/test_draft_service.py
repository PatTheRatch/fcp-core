"""The draft screen and what it reads: the page itself, and the pool behind it.

The screen's strips, its scarcity panel and its team standings are all sums
over the whole pool, so the service hands the pool over once (`GET /api/pool`)
and the page does the arithmetic. These are the tests for that: the shape of
what goes out, and that it goes out through the same gate as a card.

The page's own JavaScript is not testable here -- there is no browser in this
suite -- which is the reason it is small, defensive about missing numbers, and
built to render a room with no lines at all.
"""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.draft.bidder import SELECTORS, Bidder
from app.draft.live import SCREEN_CATEGORIES, PlayerLine, Room, player_lines
from app.draft.optimizer import Candidate
from app.draft.room import DraftState
from app.draft.service import RoomFeed, create_draft_app
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


# ---------------------------------------------------------------------------
# bidding, which exists only when the service was started with --bid
# ---------------------------------------------------------------------------


@pytest.fixture
def bidder() -> Iterator[Bidder]:
    """A running bidder on a fake auction room. No browser, no ESPN."""
    from tests.test_bidder import FakeRoom

    room = FakeRoom()
    agent = Bidder("http://example.invalid/draft", open_page=lambda: room, poll=0.01)
    agent.start()
    yield agent
    agent.stop()
    agent.join(timeout=5)


def test_without_bid_there_are_no_bidding_routes_and_no_bidding_state() -> None:
    """The flag is the whole safety story for a service nobody meant to arm:
    no routes to call, and no `bid` key for the screen to draw from."""
    client = TestClient(create_draft_app(DraftSession(make_room())))

    assert client.post("/api/bid/once").status_code == 404
    assert client.post("/api/bid/arm", json={"max": 10}).status_code == 404
    assert client.post("/api/bid/stop").status_code == 404
    assert "bid" not in client.get("/api/state").json()
    assert "/api/bid" not in client.get("/openapi.json").text, "nor in the docs"


def test_with_bid_the_routes_are_there_and_the_state_carries_the_room(bidder: Bidder) -> None:
    client = TestClient(create_draft_app(DraftSession(make_room()), bidder=bidder))

    state = client.get("/api/state").json()

    assert state["bid"]["running"] is True and state["bid"]["armed"] is False
    assert state["bid"]["room"]["player"] == "Pascal Siakam"
    assert state["bid"]["room"]["next_increment"] == 53, "what one tap would offer"
    assert state["bid"]["room"]["legal_max"] == 80


def test_arming_and_stopping_go_through_the_bidder_and_come_back_in_the_state(
    bidder: Bidder,
) -> None:
    client = TestClient(create_draft_app(DraftSession(make_room()), bidder=bidder))

    armed = client.post("/api/bid/arm", json={"max": 60, "player": "Pascal Siakam"})
    assert armed.status_code == 200
    assert armed.json()["bid"]["armed"] is True and armed.json()["bid"]["maximum"] == 60

    stopped = client.post("/api/bid/stop")
    assert stopped.status_code == 200 and stopped.json()["bid"]["armed"] is False


def test_a_maximum_the_bidders_own_rules_refuse_is_a_409_with_the_rule(bidder: Bidder) -> None:
    """Not a 422, and not a silent clamp: the screen shows the sentence."""
    client = TestClient(create_draft_app(DraftSession(make_room()), bidder=bidder))

    refused = client.post("/api/bid/arm", json={"max": 500})

    assert refused.status_code == 409
    assert "ESPN will let us offer" in refused.json()["detail"]
    assert client.get("/api/state").json()["bid"]["armed"] is False


def test_one_tap_bids_once_and_says_what_it_saw(bidder: Bidder) -> None:
    from tests.test_bidder import FakeRoom

    client = TestClient(create_draft_app(DraftSession(make_room()), bidder=bidder))

    result = client.post("/api/bid/once", json={}).json()

    assert result["amount"] == 53
    assert result["before"]["current_offer"] == 52
    room = bidder._page
    assert isinstance(room, FakeRoom)
    assert room.clicks == [SELECTORS["offer_button"][0]], "one click, on the one-tap button"


def test_the_screen_draws_no_bidding_controls_unless_the_state_carries_them() -> None:
    """There is no browser in this suite, so this guards the wiring: the row
    exists in the file, starts hidden, and is only unhidden from `S.bid`."""
    page = TestClient(create_draft_app(DraftSession(make_room()))).get("/").text

    assert '<div class="bidbar" id="bidbar" hidden>' in page
    assert 'const b = S.bid, bar = $("bidbar");' in page
    assert "if (!b) { bar.hidden = true;" in page, "no bid state, no controls"
    assert 'id="bid-stop"' in page and "body.armed #bid-stop{position:fixed" in page


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


# --- the board, read through the bidder's own window ------------------------


def room_read(player: str | None, offer: int | None, bidder: str | None, pick: str = "PK 3 OF 8"):
    """One of the bidder's published reads, as `Bidder.state()` hands it over."""
    return {
        "running": True,
        "error": None,
        "room": None
        if player is None
        else {
            "player": player,
            "current_offer": offer,
            "high_bidder": bidder,
            "espn_value": 9,
            "history": [f"${offer} {bidder}"] if bidder else [],
            "pick": pick,
        },
    }


class FakeBidder:
    url = "https://fantasy.espn.com/basketball/draft?leagueId=1"


def test_the_block_comes_from_the_window_we_are_signed_in_to() -> None:
    """The cookies in `.env` expire silently, and a page feed reading the
    sign-in page shows nobody on the block all night. The bidder's window is
    signed in by hand, so its read is the board's."""
    session = DraftSession(make_room())
    feed = RoomFeed(session, FakeBidder())  # type: ignore[arg-type]

    feed.once(room_read("Nikola Jokic", 12, "Brighton Bears"))

    state = session.snapshot()
    assert state["block"]["name"] == "Nikola Jokic"
    assert state["block"]["current_offer"] == 12
    assert state["block"]["high_bidder"] == "Brighton Bears"
    assert state["feed"]["mode"] == "room" and state["feed"]["connected"] is True


def test_a_pick_is_written_when_the_block_moves_on() -> None:
    session = DraftSession(make_room())
    feed = RoomFeed(session, FakeBidder())  # type: ignore[arg-type]

    feed.once(room_read("Nikola Jokic", 12, "Brighton Bears"))
    feed.once(room_read(None, None, None))  # the gap at every nomination
    feed.once(room_read("Kawhi Leonard", 1, "Through The Wire"))

    picks = session.state.picks
    assert [(p.player_id, p.team_id, p.price) for p in picks] == [(1, 3, 12)], (
        "Jokic went to Brighton at the last price the page showed, and Kawhi "
        "is on the block, not sold"
    )
    assert session.snapshot()["block"]["name"] == "Kawhi Leonard"


def test_a_nomination_nobody_bid_on_is_left_for_the_manager() -> None:
    """No leader and no price is not a sale we can write; typing it is
    better than inventing one."""
    session = DraftSession(make_room())
    feed = RoomFeed(session, FakeBidder())  # type: ignore[arg-type]

    feed.once(room_read("Nikola Jokic", None, None))
    feed.once(room_read("Kawhi Leonard", 1, "Through The Wire"))

    assert session.state.picks == ()


def test_a_read_we_have_already_applied_is_not_applied_twice() -> None:
    session = DraftSession(make_room())
    session.apply(1, 3, 12)
    feed = RoomFeed(session, FakeBidder())  # type: ignore[arg-type]

    feed.once(room_read("Nikola Jokic", 12, "Brighton Bears"))
    feed.once(room_read("Kawhi Leonard", 1, "Through The Wire"))

    assert len(session.state.picks) == 1, "the same sale read again is the same sale"


def test_a_bidder_with_no_read_says_so_rather_than_blanking_the_board() -> None:
    session = DraftSession(make_room())
    feed = RoomFeed(session, FakeBidder())  # type: ignore[arg-type]

    feed.once(room_read("Nikola Jokic", 12, "Brighton Bears"))
    feed.once({"running": True, "error": "could not read the room", "room": None})

    assert session.snapshot()["feed"]["connected"] is False
    assert session.snapshot()["block"]["name"] == "Nikola Jokic", "the block stands"
