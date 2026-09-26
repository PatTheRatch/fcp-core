#!/usr/bin/env python3
"""Compute the pre-draft plan: who to target, at what price, and who to let go.

Usage:
    python scripts/draft_plan.py --season 2027 --me "Through The Wire" \\
        --bbm data/bbm/BBM_Projections_2027_total.xls \\
        --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls \\
        --out logs/draft-plan-2027.json

    python scripts/draft_plan.py --season 2027 --me "Through The Wire" \\
        --projection-set 4 --out logs/draft-plan-2027.json

The pool is a BBM export (or its stored capture) or an uploaded projection set,
never both. The page
names which, and refuses to be written at all for a reader who may not see the
source it was built from (`app.projections.sources.may_show`); BBM's numbers
are paid, so a plan page built on them is not to be shared.

The plan's engine is `app.draft.plan` (`build_plan`), which the site's plan
page runs too; this script is its command-line caller and writes the same
JSON and page it always has. `--from-store` builds on the newest BBM capture
kept in the database instead of the files, as the site does. Everything is
read from the same room the draft screen loads, before the first pick: the
league's winning spending shape, the tested going price (ESPN average and
board, fitted to this league), BBM's league values, and our ceiling for each
player from an empty room. Re-run it after refreshing the BBM
exports; the plan page is rendered from the JSON it writes.

WHAT A CEILING MEANS HERE

A ceiling is computed from an empty room, so it answers "what is he worth to a
roster that has bought nothing yet". It snaps to the spending plan's places
($60, $46, $30, $16, $9 ...): read it as the tier he qualifies for. On the day
the room recomputes it after every pick, and it moves as the roster fills.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app import brand
from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft.live import RoomError
from app.draft.plan import CONSIDER_FROM, PoolSource, build_plan, with_top_place
from app.projections.sources import describe, may_show

__all__ = ["CONSIDER_FROM", "with_top_place"]

#: The plan page; the computed plan is injected where `__PLAN_DATA__` sits.
TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "draft" / "static" / "draft_plan.html"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True)
    ap.add_argument("--bbm", type=Path, help="a Basketball Monster export (.xls)")
    ap.add_argument("--bbm-per-game", type=Path)
    ap.add_argument(
        "--from-store",
        action="store_true",
        help="plan on the newest BBM capture stored in the database instead of --bbm",
    )
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
    pools = [args.bbm is not None, args.projection_set is not None, args.from_store]
    if sum(pools) != 1:
        raise SystemExit("pass one pool: --bbm <export.xls>, --from-store or --projection-set <id>")

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        league_season = session.scalars(
            select(LeagueSeason).where(LeagueSeason.season == args.season)
        ).one_or_none()
        if league_season is None:
            raise SystemExit(f"season {args.season} is not in the database; ingest it first")
        if args.bbm is not None:
            source = PoolSource("bbm", files=(args.bbm, args.bbm_per_game), exported=args.exported)
        elif args.from_store:
            try:
                source = PoolSource.parse("bbm", session, args.season)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            source = PoolSource("bbm", captured_on=source.captured_on, exported=args.exported)
        else:
            source = PoolSource("upload", set_id=args.projection_set, exported=args.exported)
        try:
            plan = build_plan(
                session,
                league_season,
                args.me,
                source=source,
                fan_team=args.fan_team,
                workers=args.workers,
                restarts=args.restarts,
                top_place=args.top_place,
                progress=lambda line: print(line, flush=True),
            )
        except RoomError as exc:
            raise SystemExit(str(exc)) from exc
    # The file has always named the team as it was asked for.
    plan.team = args.me
    out = plan.script_json()
    # One of the places the gate is asked (the others are the draft screen's
    # card, app/draft/session.py, and the plan route, app/api/draft_plan.py).
    # This page carries a price, a ceiling and a target roster for every
    # player, all of it derived from the pool, so a source the reader does
    # not own means the page is not written at all. True here: the reader is
    # the account that fetched the numbers.
    if not may_show(plan.source.tag, viewer_owns_source=True):
        raise SystemExit(
            f"{describe(plan.source.tag)}: these numbers may not be rendered for "
            "this reader, so no plan was written (docs/projection_sources.md)"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1))
    page = args.out.with_suffix(".html")
    page.write_text(
        brand.fill(
            TEMPLATE.read_text().replace("__PLAN_DATA__", json.dumps(out, separators=(",", ":")))
        )
    )
    print(f"wrote {args.out} and {page}: {len(out['players'])} players", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
