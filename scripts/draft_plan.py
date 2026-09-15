#!/usr/bin/env python3
"""Compute the pre-draft plan: who to target, at what price, and who to let go.

Usage:
    python scripts/draft_plan.py --season 2027 --me "Through The Wire" \\
        --bbm data/bbm/BBM_Projections_2027_total.xls \\
        --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls \\
        --out logs/draft-plan-2027.json

Everything here is read from the same room the draft screen loads, before the
first pick: the league's winning spending shape, the tested going price (ESPN
average and board, fitted to this league), BBM's league values, and our
ceiling for each player from an empty room. Re-run it after refreshing the BBM
exports; the plan page is rendered from the JSON it writes.

WHAT A CEILING MEANS HERE

A ceiling is computed from an empty room, so it answers "what is he worth to a
roster that has bought nothing yet". It snaps to the spending plan's places
($60, $46, $30, $16, $9 ...): read it as the tier he qualifies for. On the day
the room recomputes it after every pick, and it moves as the roster fills.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from concurrent.futures import as_completed
from pathlib import Path
from typing import Any

from app.draft.live import RoomError, load_room, market_prices
from app.draft.room import resolve
from app.draft.session import _compute, process_executor
from app.draft.valuation import value_players

CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")

#: Players considered: anyone the market or BBM prices at this or more.
CONSIDER_FROM = 3

#: The plan page; the computed plan is injected where `__PLAN_DATA__` sits.
TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "draft" / "static" / "draft_plan.html"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True)
    ap.add_argument("--bbm", type=Path, required=True)
    ap.add_argument("--bbm-per-game", type=Path)
    ap.add_argument("--restarts", type=int, default=4)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("logs/draft-plan.json"))
    ap.add_argument(
        "--exported", default="", help="when the BBM exports were pulled, for the page footer"
    )
    ap.add_argument(
        "--fan-team", default="CLE", help="NBA team to find a loyalty pick from (default CLE)"
    )
    args = ap.parse_args()

    try:
        room = load_room(
            args.season,
            args.me,
            pool_season=None,
            pool_kind="projected",
            punt=[],
            restarts=args.restarts,
            bbm=args.bbm,
            bbm_per_game=args.bbm_per_game,
            plan="history",
        )
    except RoomError as exc:
        raise SystemExit(str(exc)) from exc
    state = room.state
    market = market_prices(room, state)

    # League-standard category values, for each player's profile.
    projections = [c for c in room.candidates]
    from app.config import get_settings
    from app.db.session import make_engine, make_session_factory
    from app.draft.bbm import load_bbm

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        loaded = load_bbm(session, args.bbm, args.season)
    profile = {
        v.player_id: {c.abbreviation: round(c.value, 2) for c in v.categories}
        for v in value_players(loaded.projections, list(CATEGORIES))
    }

    considered = []
    for c in projections:
        row = room.bbm.get(c.player_id)
        total = row.league_dollars if row and row.league_dollars is not None else None
        going = market.get(c.player_id, (None, ""))[0] or 0
        fan = row is not None and row.team == args.fan_team
        if going >= CONSIDER_FROM or (total or 0) >= CONSIDER_FROM or (fan and (total or 0) >= 1):
            considered.append(c)
    print(f"{len(considered)} players considered; computing ceilings", flush=True)

    ceilings: dict[int, Any] = {}
    executor = process_executor(room, args.workers)
    with executor:
        futures = {executor.submit(_compute, state, c.player_id): c.player_id for c in considered}
        for done, future in enumerate(as_completed(futures), 1):
            ceilings[futures[future]] = future.result()
            if done % 25 == 0:
                print(f"  {done}/{len(futures)}", flush=True)

    players = []
    for c in considered:
        row = room.bbm.get(c.player_id)
        ceiling = ceilings[c.player_id]
        priced, source = market.get(c.player_id, (0, ""))
        going_price = priced or 0
        total = row.league_dollars if row and row.league_dollars is not None else None
        per_game = room.per_game_dollars.get(c.player_id)
        players.append(
            {
                "id": c.player_id,
                "name": c.name,
                "position": c.position,
                "eligible": sorted(e for e in c.eligible if e in ("PG", "SG", "SF", "PF", "C")),
                "going": going_price,
                "going_source": source,
                "board": room.board.get(c.player_id, c.price),
                "ceiling": ceiling.price,
                "capped": ceiling.capped,
                "marginal": (
                    None
                    if ceiling.marginal_at_floor == float("-inf")
                    else round(ceiling.marginal_at_floor, 3)
                ),
                "bbm_total": None if total is None else round(total),
                "bbm_per_game": None if per_game is None else round(per_game),
                "espn_avg": _rounded(row.espn_dollars if row else None),
                "yahoo_avg": _rounded(row.yahoo_dollars if row else None),
                "age": None if not row or row.age is None else round(row.age, 1),
                "games": None if not row else round(row.games),
                "injury_risk": (row.injury_risk or None) if row else None,
                "injury": (row.injury or None) if row else None,
                "nba_team": (row.team or None) if row else None,
                "profile": profile.get(c.player_id, {}),
            }
        )

    builds = {}
    for label, punt in (("balanced", ()), ("punt FT%", ("FT%",))):
        plan = resolve(
            state,
            room.candidates,
            room.distributions,
            punt=punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=12,
            allocation=room.allocation,
        )
        builds[label] = {
            "expected_wins": round(plan.expected_wins, 2),
            "cost": plan.cost,
            "win_probability": {k: round(v, 2) for k, v in plan.win_probability.items()},
            "roster": [
                {"name": p.name, "price": p.price, "position": p.position}
                for p in sorted(plan.players, key=lambda p: -p.price)
            ],
        }

    # The loyalty pick: for each fan-team player, the best roster that has him
    # at his going price, against the best roster with no such requirement.
    base = resolve(
        state,
        room.candidates,
        room.distributions,
        lineup=room.lineup,
        limits=room.limits,
        restarts=12,
        allocation=room.allocation,
    )
    fan_rows = []
    for c in considered:
        row = room.bbm.get(c.player_id)
        if row is None or row.team != args.fan_team:
            continue
        with_him = resolve(
            state,
            room.candidates,
            room.distributions,
            lineup=room.lineup,
            limits=room.limits,
            restarts=12,
            allocation=room.allocation,
            lock={c.player_id: c.price},
            starts=[tuple(base.player_ids)],
        )
        fan_rows.append(
            {
                "id": c.player_id,
                "cost": round(max(0.0, base.expected_wins - with_him.expected_wins), 3),
                "in_best": c.player_id in base.player_ids,
                "roster_with": [
                    {"name": p.name, "price": p.price}
                    for p in sorted(with_him.players, key=lambda p: -p.price)
                ],
            }
        )
    print(f"{len(fan_rows)} {args.fan_team} players costed", flush=True)

    out = {
        "season": args.season,
        "fan_team": args.fan_team,
        "fan": fan_rows,
        "team": args.me,
        "teams": len(state.teams),
        "budget": state.budget,
        "roster_slots": state.roster_slots,
        "allocation": list(room.allocation.places) if room.allocation else [],
        "cap": room.allocation.cap(state) if room.allocation else None,
        "opponent": {
            d.abbreviation: {"mean": round(d.mean, 3), "spread": round(d.spread, 3)}
            for d in room.distributions
        },
        "pool": room.pool_note,
        "players": players,
        "builds": builds,
    }
    out["exported"] = args.exported
    out["generated"] = "computed " + datetime.date.today().strftime("%-d %b %Y")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1))
    page = args.out.with_suffix(".html")
    page.write_text(
        TEMPLATE.read_text().replace("__PLAN_DATA__", json.dumps(out, separators=(",", ":")))
    )
    print(f"wrote {args.out} and {page}: {len(players)} players", flush=True)
    return 0


def _rounded(value: float | None) -> int | None:
    return None if value is None else round(value)


if __name__ == "__main__":
    sys.exit(main())
