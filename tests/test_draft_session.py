"""The draft session and the service in front of it.

A hand-built room: three teams, $20 each, two places each, six players.
No database, no ESPN, no browser. Ceilings run on a thread pool in the test
process, which exercises the same scheduling a process pool gets on draft
day.
"""

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.draft.bbm import BBMRow
from app.draft.feed import LoggedPick, OnBlock
from app.draft.live import Room
from app.draft.optimizer import Candidate
from app.draft.room import DraftError, DraftState
from app.draft.service import create_draft_app
from app.draft.session import DraftLog, DraftSession, UnknownNameError, _install, context_for
from app.draft.targets import CategoryDistribution

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
NAMES |= {5: "Ausar Thompson", 6: "Kevin Porter Jr."}


def cand(player_id: int, price: int, pts: float) -> Candidate:
    return Candidate(
        player_id=player_id,
        name=NAMES[player_id],
        price=price,
        weekly={"PTS": pts, "FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0},
        eligible=frozenset({"UT"}),
        position="SF",
    )


def bbm_row(name: str, espn: float | None) -> BBMRow:
    return BBMRow(
        name=name,
        position="SF",
        games=56.0,
        rates={},
        fg_pct=0.5,
        ft_pct=0.8,
        dollars=40.0,
        injury="",
        injury_risk="E",
        age=35.2,
        espn_dollars=espn,
        league_dollars=41.0,
    )


def make_room() -> Room:
    candidates = [
        cand(1, 12, 90.0),
        cand(2, 6, 70.0),
        cand(3, 5, 65.0),
        cand(4, 3, 50.0),
        cand(5, 1, 40.0),
        cand(6, 1, 35.0),
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
        bbm={2: bbm_row("Kawhi Leonard", 3.0)},
        per_game_dollars={2: 52.0},
        pool_note="test pool",
    )


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    room = make_room()
    with ThreadPoolExecutor(1, initializer=_install, initargs=(context_for(room),)) as pool:
        yield pool


def test_every_pick_and_undo_is_logged_and_a_new_session_replays_them(tmp_path: Path) -> None:
    log = DraftLog(tmp_path / "draft.jsonl")
    first = DraftSession(make_room(), log=log)
    first.apply(1, 2, 12)
    first.apply(2, 1, 5)
    first.undo()
    first.apply(3, 1, 4)

    again = DraftSession(make_room(), log=DraftLog(tmp_path / "draft.jsonl"))
    assert again.state.picks == first.state.picks
    assert [p.player_id for p in again.state.picks] == [1, 3]
    assert again.replay_warnings == []


def test_a_refused_pick_is_not_logged(tmp_path: Path) -> None:
    log = DraftLog(tmp_path / "draft.jsonl")
    session = DraftSession(make_room(), log=log)
    with pytest.raises(DraftError):
        session.apply(1, 2, 25)
    assert log.read() == []


def test_a_pick_read_twice_off_the_page_is_applied_once() -> None:
    session = DraftSession(make_room())
    logged = LoggedPick(overall=1, player="Jokic", team="Foxes", price=12)
    assert session.apply_logged(logged) is not None
    assert session.apply_logged(logged) is None
    assert len(session.state.picks) == 1


def test_a_player_nobody_holds_is_tracked_by_name() -> None:
    session = DraftSession(make_room())
    session.apply_logged(LoggedPick(overall=1, player="Cameron Boozer", team="Brighton", price=3))
    pick = session.state.picks[0]
    assert pick.player_id < 0
    assert session.snapshot()["picks"][0]["name"] == "Cameron Boozer"


def test_an_ambiguous_name_is_refused_with_the_alternatives() -> None:
    session = DraftSession(make_room())
    with pytest.raises(UnknownNameError) as refused:
        session.player_id("Zzzzz")
    assert "no player" in str(refused.value)


def test_every_change_moves_the_version() -> None:
    session = DraftSession(make_room())
    seen = [session.version]
    session.apply(1, 2, 12)
    seen.append(session.version)
    session.set_block(OnBlock("Kawhi Leonard", 4, "Brighton Bears", None))
    seen.append(session.version)
    session.undo()
    seen.append(session.version)
    assert seen == sorted(set(seen)), "strictly increasing"


def test_ceilings_are_ready_for_the_likeliest_nominations_and_never_stale(
    executor: ThreadPoolExecutor,
) -> None:
    session = DraftSession(make_room(), executor=executor, precompute=3)
    session.wait_for_ceilings()
    assert session.card(1)["ceiling"]["status"] == "ready", "the most expensive is precomputed"

    session.apply(1, 2, 12)
    card = session.card(2)
    assert card["ceiling"]["status"] in ("pending", "ready")
    session.wait_for_ceilings()
    assert session.card(2)["ceiling"]["status"] == "ready"
    assert session.card(1)["taken"] == {
        "team_id": 2,
        "team": "Foxes ShutUpNDribble",
        "price": 12,
    }


def test_the_card_carries_market_bbm_and_the_injury_discount() -> None:
    session = DraftSession(make_room())
    card = session.card(2)
    assert card["market_source"].startswith("ESPN average and our board, sized")
    assert card["bbm"]["league_total"] == 41
    assert card["bbm"]["league_per_game"] == 52
    assert card["bbm"]["injury_discount"] == 11
    assert session.card(3)["market_source"].startswith("our board")


def test_the_service_takes_picks_by_name_and_refuses_what_the_rules_refuse(
    tmp_path: Path, executor: ThreadPoolExecutor
) -> None:
    session = DraftSession(make_room(), log=DraftLog(tmp_path / "d.jsonl"), executor=executor)
    client = TestClient(create_draft_app(session))

    state = client.get("/api/state").json()
    assert state["pick_number"] == 0 and len(state["teams"]) == 3

    ok = client.post("/api/picks", json={"player": "Jokic", "team": "Foxes", "price": 12})
    assert ok.status_code == 200
    assert ok.json()["picks"][0]["name"] == "Nikola Jokic"

    mine = client.post("/api/picks", json={"player": "Kawhi", "price": 5})
    assert mine.json()["picks"][1]["team_id"] == 1, "no team means us"

    too_much = client.post("/api/picks", json={"player": "Turner", "team": "Brighton", "price": 25})
    assert too_much.status_code == 409

    unknown = client.post("/api/picks", json={"player": "Zzzzz", "price": 1})
    assert unknown.status_code == 422

    assert len(client.post("/api/undo").json()["picks"]) == 1

    block = client.post("/api/block", json={"player": "Turner", "current_offer": 2})
    assert block.json()["block"]["player"]["name"] == "Myles Turner"

    card = client.get("/api/players/4", params={"wait": 10}).json()
    assert card["ceiling"]["status"] == "ready"

    found = client.get("/api/players", params={"q": "thom"}).json()
    assert [p["name"] for p in found] == ["Ausar Thompson"]

    assert client.get("/api/plan").json()["players"]


def test_a_rehearsal_sells_what_we_pass_on_and_leaves_what_we_buy() -> None:
    from app.draft.rehearsal import Nomination, Rehearsal

    session = DraftSession(make_room())
    nominations = [
        Nomination(1, "Nikola Jokic", 2, 12),
        Nomination(2, "Kawhi Leonard", 1, 4),  # our real pick: someone else takes him
        Nomination(3, "Jalen Johnson", 3, 5),
    ]
    rehearsal = Rehearsal(session, nominations, seconds=0.5)
    rehearsal.start()
    deadline = time.monotonic() + 5
    while session.state.picks == () and time.monotonic() < deadline:
        time.sleep(0.02)
    session.apply(3, 1, 6)  # we buy Johnson before he comes up
    rehearsal.join(timeout=10)

    owners = {p.player_id: p.team_id for p in session.state.picks}
    assert owners[1] == 2, "sold to the team that really bought him"
    assert owners[2] != 1, "our real pick is not ours unless we enter it"
    assert owners[3] == 1, "what we bought stays bought"
    assert session.snapshot()["feed"]["rehearsal"]["done"]


def test_the_screen_is_served() -> None:
    client = TestClient(create_draft_app(DraftSession(make_room())))
    page = client.get("/")
    assert page.status_code == 200 and "Draft Room" in page.text


def test_going_prices_share_the_rooms_money_and_price_a_quarter_at_a_dollar() -> None:
    """An auction is a fixed pot, and a quarter of the places go for $1."""
    from app.draft.live import DOLLAR_ONE_SHARE, SPEND_RATE, market_prices

    names = {i: f"Player {i}" for i in range(1, 61)}
    candidates = [
        Candidate(
            player_id=i,
            name=names[i],
            price=max(1, 70 - i),
            weekly={"PTS": float(100 - i), "FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0},
            eligible=frozenset({"UT"}),
            position="SF",
        )
        for i in names
    ]
    teams = {1: "A", 2: "B", 3: "C", 4: "D"}
    state = DraftState.open(budget=200, roster_slots=10, teams=teams, me=1)
    room = Room(
        season=2027,
        state=state,
        candidates=candidates,
        distributions=[PTS],
        lineup=("UT",),
        limits={},
        names={c.name: c.player_id for c in candidates},
        team_names=teams,
        punt=(),
        restarts=1,
    )
    prices = market_prices(room, state)
    rostered = sorted((p for p, _ in prices.values() if p is not None), reverse=True)[:40]

    assert sum(1 for p in rostered if p == 1) == round(DOLLAR_ONE_SHARE * 40)
    assert sum(rostered) == pytest.approx(800 * SPEND_RATE, abs=15)
    assert rostered[0] > rostered[-1]
