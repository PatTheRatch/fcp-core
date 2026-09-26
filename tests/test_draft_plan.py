"""The draft plan's engine (`app.draft.plan`), on a room small enough to reason about.

No database: a hand-built room of two teams, $10 each and two places, the
same shape `tests/test_room.py` uses, with BBM rows for a fan team. The
plan's lists, the model's figure, the nine against the targets, and the
must-have men -- a set that fits, one that does not, and a one-man set
costing exactly what the fan section says he costs.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.draft.bbm import BBMRow
from app.draft.live import Room
from app.draft.optimizer import Candidate
from app.draft.plan import (
    MustLock,
    Override,
    PoolSource,
    best_solver,
    bid_to,
    ceilings_inline,
    effective,
    lock_state,
    must_lock,
    must_shortfall,
    nine_against_targets,
    plan_from_room,
    sections,
)
from app.draft.room import Allocation, DraftState
from app.draft.targets import CategoryDistribution

ANY = frozenset({"UT"})


def cand(player_id: int, price: int, pts: float) -> Candidate:
    return Candidate(
        player_id=player_id,
        name=f"P{player_id}",
        price=price,
        weekly={"PTS": pts, "FGM": 0.0, "FGA": 0.0, "FTM": 0.0, "FTA": 0.0},
        eligible=ANY,
        position="PG",
    )


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


def bbm_row(name: str, team: str, dollars: float) -> BBMRow:
    return BBMRow(
        name=name,
        position="PG",
        games=70.0,
        rates={},
        fg_pct=0.0,
        ft_pct=0.0,
        dollars=dollars,
        injury="",
        injury_risk="",
        league_dollars=dollars,
        team=team,
    )


def small_room() -> Room:
    """Two teams, $10 each, two places each; four men, one of them a Cav."""
    state = DraftState.open(
        budget=10, roster_slots=2, teams={1: "Us", 2: "Them"}, me=1, nomination_order=(2, 1)
    )
    candidates = [cand(1, 6, 70.0), cand(2, 4, 55.0), cand(3, 3, 40.0), cand(4, 1, 20.0)]
    return Room(
        season=2027,
        state=state,
        candidates=candidates,
        distributions=[PTS],
        lineup=("UT", "UT"),
        limits={},
        names={c.name: c.player_id for c in candidates},
        team_names={1: "Us", 2: "Them"},
        punt=(),
        restarts=2,
        allocation=Allocation.from_prices([6, 4], state),
        bbm={3: bbm_row("P3", "CLE", 4.0), 1: bbm_row("P1", "BOS", 7.0)},
        board={c.player_id: c.price for c in candidates},
    )


def build(room: Room, **kwargs: Any) -> Any:
    return plan_from_room(
        room,
        draft_at=None,
        order=[2, 1],
        source=PoolSource("espn"),
        source_detail="",
        fan_team=kwargs.pop("fan_team", None),
        must=kwargs.pop("must", None),
        ceilings=ceilings_inline,
        today=dt.date(2026, 9, 26),
    )


# -- the model's figure and its lists -----------------------------------------


def test_the_models_figure_is_the_lower_of_ceiling_and_bbm() -> None:
    assert bid_to({"ceiling": 46, "bbm_total": 50}) == 46
    assert bid_to({"ceiling": 46, "bbm_total": 30}) == 30
    assert bid_to({"ceiling": 46, "bbm_total": None}) == 46, "no BBM leaves the ceiling"
    assert bid_to({"ceiling": None, "bbm_total": None}) == 1, "never under a dollar"


def row(pid: int, going: int, ceiling: int, bbm: int | None, **extra: Any) -> dict[str, Any]:
    return {"id": pid, "going": going, "ceiling": ceiling, "bbm_total": bbm, "profile": {}, **extra}


def test_the_lists_follow_their_rules() -> None:
    players = [
        row(1, 55, 60, 70),  # a star inside the model's figure
        row(2, 20, 21, 30),  # a target: ceiling over going, BBM $5+ over
        row(3, 20, 12, 30),  # BBM likes, we're lukewarm
        row(4, 30, 20, 20),  # let go, and nominate early (going $20+)
        row(5, 5, 5, 5, profile={"STL": 1.5}),  # late steals
        row(6, 6, 6, 6, profile={"REB": 2.0, "BLK": 1.0, "FT%": -2.0}),  # a cheap big
        row(7, 10, 10, 10, bbm_per_game=20),  # IR stash: per game $8+ over the season
    ]
    players[6]["bbm_per_game"] = 20
    for p in players:
        p.setdefault("bbm_per_game", None)
    found = {s.key: s for s in sections(players, [], fan_team=None, has_bbm=True)}
    assert found["stars"].ids == [1]
    assert "inside" in found["stars"].reasons[1]
    assert found["targets"].ids == [2]
    assert found["bbm_likes"].ids == [3]
    assert found["let_go"].ids == [4]
    assert found["nominate"].ids == [4]
    assert found["late"].ids == [5]
    assert found["bigs"].ids == [6] and "costs FT%" in found["bigs"].reasons[6]
    assert found["ir"].ids == [7]
    assert "fan" not in found, "no fan section without a fan team"
    for section in found.values():
        assert section.rule, "every list says its rule"


def test_without_bbm_the_lists_that_need_it_are_left_out() -> None:
    players = [row(2, 20, 26, None), row(4, 30, 20, None)]
    found = {s.key: s for s in sections(players, [], fan_team=None, has_bbm=False)}
    assert "flags" not in found and "bbm_likes" not in found and "ir" not in found
    assert found["targets"].ids == [2], "read on our ceiling alone: $5+ over the going price"
    assert found["let_go"].ids == [4]


# -- the nine ------------------------------------------------------------------


def test_the_nine_is_a_change_in_the_chance_against_the_median_opponent() -> None:
    room = small_room()
    nine, par = nine_against_targets(room)
    # Par is half the opponent's 100 points a man; a 70-point man is 20 over
    # par, one spread (20): the chance goes from 50% to 84%.
    assert par["PTS"]["per_man"] == 50.0
    assert nine[1]["PTS"] == pytest.approx(0.3413, abs=1e-3)
    assert nine[4]["PTS"] < 0, "a 20-point man costs the par roster"


# -- the whole plan on the small room ----------------------------------------


def test_a_plan_from_the_small_room_has_every_part() -> None:
    plan = build(small_room(), fan_team="CLE")
    assert plan.allocation == [6, 4] and plan.cap == 6
    assert plan.nominate == 2
    assert {p["id"] for p in plan.players} == {1, 2, 3}, "going $3 or more, or a priced Cav"
    assert [f["id"] for f in plan.fan] == [3]
    payload = plan.payload(dt.date(2026, 9, 26))
    first = payload["players"][0]
    assert {"going", "ceiling", "bid_to", "nine", "sections"} <= set(first)
    assert payload["rest"][0]["id"] == 4 and payload["rest"][0]["ceiling"] is None
    assert payload["must"] is None
    json = plan.script_json()
    assert list(json)[-2:] == ["exported", "generated"], "the file's key order is kept"


# -- must-have men -------------------------------------------------------------


def test_a_must_set_that_fits_is_bought_first_and_the_rest_refit() -> None:
    room = small_room()
    lock = must_lock(room, {2: None})
    assert lock.applied and lock.prices == {2: 4}, "his going price when no bid-up-to is set"
    assert lock.places_left == 1 and lock.money_left == 6
    assert lock.ladder_after == [6], "the ladder refit to the place and money left"
    assert lock.cost is not None and lock.cost >= 0
    state = lock_state(room, lock)
    assert state.mine.player_ids == frozenset({2})


def test_a_must_set_at_the_managers_price_uses_his_price() -> None:
    lock = must_lock(small_room(), {2: 7})
    assert lock.prices == {2: 7} and lock.money_left == 3


def test_a_must_set_that_does_not_fit_says_why_and_the_plan_is_the_free_build() -> None:
    room = small_room()
    lock = must_lock(room, {1: 6, 2: 5})
    assert not lock.applied
    assert lock.error is not None and "$1 over the $10 budget" in lock.error
    plan = build(room, must={1: 6, 2: 5})
    assert plan.must is not None and plan.must.error == lock.error
    assert all(p["ceiling"] is not None for p in plan.players), "the free build's ceilings"
    too_many = must_shortfall({1: 1, 2: 1, 3: 1}, room.state)
    assert too_many is not None and "more than the 2 places" in too_many


def test_a_one_man_must_set_costs_what_the_fan_section_says() -> None:
    room = small_room()
    fan = build(room, fan_team="CLE").fan
    cav = next(f for f in fan if f["id"] == 3)
    lock = must_lock(room, {3: None})
    assert lock.cost == cav["cost"]
    assert lock.each == {3: cav["cost"]}


def test_a_plan_built_around_a_must_man_carries_his_price_and_no_ceiling() -> None:
    plan = build(small_room(), must={2: None})
    assert isinstance(plan.must, MustLock) and plan.must.applied
    two = next(p for p in plan.payload(dt.date(2026, 9, 26))["players"] if p["id"] == 2)
    assert two["must_price"] == 4 and two["ceiling"] is None


# -- your prices ---------------------------------------------------------------


def solved(room: Room) -> tuple[Any, dict[str, Any]]:
    plan = build(room)
    payload = plan.payload(dt.date(2026, 9, 26))
    return best_solver(room, room.state), payload


def test_with_no_figure_of_his_own_the_effective_plan_is_the_models() -> None:
    solve, payload = solved(small_room())
    same = effective(payload, {}, solve)
    assert same["yours"] == 0 and not same["solved"]
    assert same["best"] == payload["best"]
    assert same["sections"] == payload["sections"]
    for p in payload["players"]:
        mine = same["players"][str(p["id"])]
        assert (mine["going"], mine["ceiling"], mine["bid_to"]) == (
            p["going"],
            p["ceiling"],
            p["bid_to"],
        )
        assert mine["going_from"] == mine["ceiling_from"] == "model"


def test_an_override_changes_a_mans_section() -> None:
    payload = {
        "players": [row(2, 20, 26, None, considered=True)],
        "rest": [],
        "fan": [],
        "fan_team": None,
        "best": {"roster": []},
    }
    before = effective(payload, {}, None)
    assert before["players"]["2"]["sections"] == ["targets"]
    raised = effective(payload, {2: Override(going=34)}, None)
    assert raised["players"]["2"]["sections"] == ["let_go", "nominate"]
    assert raised["players"]["2"]["going"] == 34
    assert raised["players"]["2"]["going_from"] == "yours"
    assert payload["players"][0]["going"] == 20, "the model's figure is never overwritten"


def test_a_ceiling_below_the_going_price_keeps_him_out_of_the_best_roster() -> None:
    room = small_room()
    solve, payload = solved(room)
    model_best = {m["id"] for m in payload["best"]["roster"]}
    assert 2 in model_best, "the 55-point man at $4 is the model's"
    going = next(p["going"] for p in payload["players"] if p["id"] == 2)
    capped = effective(payload, {2: Override(ceiling=going - 1)}, solve)
    assert capped["solved"]
    assert 2 not in {m["id"] for m in capped["best"]["roster"]}
    assert capped["players"]["2"]["ceiling_from"] == "yours"
    assert capped["players"]["2"]["in_best"] is False


def test_the_effective_figures_are_reported_beside_the_models() -> None:
    solve, payload = solved(small_room())
    two = next(p for p in payload["players"] if p["id"] == 2)
    mine = effective(payload, {2: Override(going=2, ceiling=3)}, solve)["players"]["2"]
    assert mine["going"] == 2 and two["going"] != 2
    assert mine["ceiling"] == min(3, two["ceiling"])
    assert mine["bid_to"] == min(3, two["ceiling"])


def test_reset_restores_the_models_plan_byte_for_byte() -> None:
    import json

    solve, payload = solved(small_room())
    untouched = json.dumps(effective(payload, {}, solve), sort_keys=True)
    effective(payload, {1: Override(going=9, ceiling=2), 2: Override(going=1)}, solve)
    again = json.dumps(effective(payload, {}, solve), sort_keys=True)
    assert again == untouched
