#!/usr/bin/env python3
"""Compute the pre-draft plan: who to target, at what price, and who to let go.

Usage:
    python scripts/draft_plan.py --season 2027 --me "Through The Wire" \\
        --bbm data/bbm/BBM_Projections_2027_total.xls \\
        --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls \\
        --out logs/draft-plan-2027.json

    python scripts/draft_plan.py --season 2027 --me "Through The Wire" \\
        --projection-set 4 --out logs/draft-plan-2027.json

The pool is a BBM export or an uploaded projection set, never both. The page
names which, and refuses to be written at all for a reader who may not see the
source it was built from (`app.projections.sources.may_show`); BBM's numbers
are paid, so a plan page built on them is not to be shared.

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
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import LeagueSeason
from app.draft.bbm import ROLES
from app.draft.live import Room, RoomError, load_room, market_prices
from app.draft.optimizer import roster_totals, score
from app.draft.room import Allocation, _fit, resolve
from app.draft.session import _compute, process_executor
from app.draft.valuation import value_players
from app.projections.sources import describe, may_show

CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")

#: Players considered: anyone the market or BBM prices at this or more.
CONSIDER_FROM = 3

#: The plan page; the computed plan is injected where `__PLAN_DATA__` sits.
TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "draft" / "static" / "draft_plan.html"


def with_top_place(room: Room, top: int) -> Room:
    """The room with its plan's first place forced to `top`, for a what-if.

    The other places keep history's proportions, fitted to what is left of
    the budget, so the plan still spends every dollar and no place falls
    below the minimum bid.
    """
    if room.allocation is None:
        return room
    state = room.state
    rest = list(room.allocation.places[1:])
    floor = state.minimum_bid
    top = max(floor, min(top, state.budget - floor * len(rest)))
    places = (top, *_fit(rest, state.budget - top, floor))
    return replace(room, allocation=Allocation(places, room.allocation.slack))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True)
    ap.add_argument("--bbm", type=Path, help="a Basketball Monster export (.xls)")
    ap.add_argument("--bbm-per-game", type=Path)
    ap.add_argument(
        "--projection-set",
        type=int,
        help="plan on a stored uploaded projection set instead of --bbm",
    )
    ap.add_argument("--restarts", type=int, default=4)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("logs/draft-plan.json"))
    ap.add_argument(
        "--exported", default="", help="when the BBM exports were pulled, for the page footer"
    )
    ap.add_argument(
        "--fan-team", default="CLE", help="NBA team to find a loyalty pick from (default CLE)"
    )
    ap.add_argument(
        "--top-place",
        type=int,
        help=(
            "force the plan's most expensive place to this many dollars and spread the rest"
            " of the budget over the other places in history's proportions; the cap is this"
            " plus the plan's slack (a what-if: history's shape puts it at $55, a $60 cap)"
        ),
    )
    args = ap.parse_args()
    if (args.bbm is None) == (args.projection_set is None):
        raise SystemExit("pass one pool: --bbm <export.xls> or --projection-set <id>")

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
            projection_set=args.projection_set,
            plan="history",
        )
    except RoomError as exc:
        raise SystemExit(str(exc)) from exc
    state = room.state
    if args.top_place is not None:
        room = with_top_place(room, args.top_place)
        print(
            f"what-if: top place ${args.top_place}, cap ${room.allocation.cap(state)}"
            if room.allocation
            else "what-if: no allocation to change",
            flush=True,
        )
    market = market_prices(room, state)

    # League-standard category values, for each player's profile. Valued on
    # the pool the room was loaded from, whichever source that is.
    projections = [c for c in room.candidates]
    from app.config import get_settings
    from app.db.session import make_engine, make_session_factory
    from app.draft.bbm import load_bbm
    from app.projections.upload import load_projection_set

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        pool = (
            load_projection_set(session, args.projection_set)
            if args.projection_set is not None
            else load_bbm(session, args.bbm, args.season).projections
        )
    profile = {
        v.player_id: {c.abbreviation: round(c.value, 2) for c in v.categories}
        for v in value_players(pool, list(CATEGORIES))
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
                "confidence": row.confidence if row else None,
                "role": (ROLES.get(row.role, row.role) or None) if row else None,
                "status": list(row.status) if row else [],
                "tags": list(row.tags) if row else [],
                "note": (row.note or None) if row else None,
                "note_by": (row.note_by or None) if row else None,
                "profile": profile.get(c.player_id, {}),
            }
        )

    # Alternative builds. Each is scored on all nine categories with the concede
    # penalty (`all_nine`), so a build that punts by choice is compared on
    # what it will actually win, not on the categories it stopped counting.
    categories = [d.abbreviation for d in room.distributions]
    risky = [
        c.player_id
        for c in room.candidates
        if (row := room.bbm.get(c.player_id)) is not None and row.injury_risk in ("H", "E")
    ]
    variants: tuple[tuple[str, tuple[str, ...], list[int]], ...] = (
        ("balanced", (), []),
        ("no high injury risk", (), risky),
        ("punt TO", ("TO",), []),
        ("punt FT%", ("FT%",), []),
    )
    builds = {}
    for label, punt, exclude in variants:
        plan = resolve(
            state,
            room.candidates,
            room.distributions,
            punt=punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=12,
            allocation=room.allocation,
            exclude=exclude,
        )
        all_nine, _ = score(roster_totals(plan.players, categories), room.distributions)
        builds[label] = {
            "expected_wins": round(plan.expected_wins, 2),
            "all_nine": round(all_nine, 2),
            "punt": list(punt),
            "excluded": len(exclude),
            "cost": plan.cost,
            "win_probability": {k: round(v, 2) for k, v in plan.win_probability.items()},
            "roster": [
                {
                    "name": p.name,
                    "price": p.price,
                    "position": p.position,
                    "injury_risk": (
                        room.bbm[p.player_id].injury_risk or None
                        if p.player_id in room.bbm
                        else None
                    ),
                }
                for p in sorted(plan.players, key=lambda p: -p.price)
            ],
        }
    print("builds: " + ", ".join(f"{k} {v['all_nine']}" for k, v in builds.items()), flush=True)

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

    with factory() as session:
        league_season = session.scalars(
            select(LeagueSeason).where(LeagueSeason.season == args.season)
        ).one()
        drafted_at = league_season.drafted_at
        order = list(league_season.draft_order or [])
    out = {
        "season": args.season,
        "draft_at": drafted_at.isoformat() if drafted_at else None,
        "nominate": order.index(state.me) + 1 if state.me in order else None,
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
        "source": room.projection_source,
        # The page's one line about where its numbers came from. `--exported`
        # is when the BBM exports were pulled, which the room cannot know;
        # otherwise the room's own detail (the export, or the uploaded set's
        # name and note) says it.
        "source_note": describe(
            room.projection_source,
            f"pulled {args.exported}" if args.exported else room.source_detail,
        ),
        "players": players,
        "builds": builds,
    }
    out["exported"] = args.exported
    out["generated"] = "computed " + datetime.date.today().strftime("%-d %b %Y")
    # One of the two places the gate is asked (the other is the draft screen's
    # card, app/draft/session.py). This page carries a price, a ceiling and a
    # target roster for every player, all of it derived from the pool, so a
    # source the reader does not own means the page is not written at all.
    # True today: the reader is the account that fetched the numbers.
    if not may_show(room.projection_source, viewer_owns_source=True):
        raise SystemExit(
            f"{describe(room.projection_source)}: these numbers may not be rendered for "
            "this reader, so no plan was written (docs/projection_sources.md)"
        )
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
