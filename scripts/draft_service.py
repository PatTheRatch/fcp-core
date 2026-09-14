#!/usr/bin/env python3
"""Run the draft service on this machine, for the draft screen.

Usage:
    python scripts/draft_service.py --season 2027 --me "Through The Wire" \\
        --bbm ~/Documents/PatriotGames/player_rankings/BBM_Projections_2027_total.xls \\
        --bbm-per-game ~/Documents/PatriotGames/player_rankings/BBM_Projections_2027_pergame.xls \\
        --page "https://fantasy.espn.com/basketball/draft?leagueId=...&seasonId=2027&teamId=3"

Then http://127.0.0.1:8765/api/state, or /docs for every endpoint.

Every accepted pick and undo is written to --log (by default
logs/draft-<season>.jsonl) and replayed on start, so stopping and starting
this mid-draft loses nothing. Start a fresh draft -- after a mock, say --
with a new --log path, or move the old file aside; the service never
deletes one.

Without --page, picks come in through POST /api/picks and nothing reads
ESPN. The room options (--plan, --restarts, --punt ...) are the same as
scripts/draft_room.py's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from app.draft.live import RoomError, load_room
from app.draft.service import PageFeed, create_draft_app
from app.draft.session import DraftLog, DraftSession, process_executor


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True, help="our team's name")
    ap.add_argument("--bbm", type=Path, help="Basketball Monster export, Total Games Value")
    ap.add_argument("--bbm-per-game", type=Path, help="the same export on Per Game Value")
    ap.add_argument("--page", help="the ESPN draft room URL; omit to enter picks by hand")
    ap.add_argument("--trust-money", action="store_true", help="apply picks inferred from budgets")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between page reads")
    ap.add_argument("--log", type=Path, help="pick log (default logs/draft-<season>.jsonl)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workers", type=int, default=3, help="processes computing ceilings")
    ap.add_argument("--restarts", type=int, default=4)
    ap.add_argument("--punt", action="append", default=[])
    ap.add_argument("--plan", default="history", choices=("history", "optimizer", "none"))
    ap.add_argument("--plan-slack", type=float, default=0.10)
    ap.add_argument("--pool-season", type=int)
    ap.add_argument("--pool-kind", default="projected", choices=("projected", "total"))
    args = ap.parse_args()

    try:
        room = load_room(
            args.season,
            args.me,
            pool_season=args.pool_season,
            pool_kind=args.pool_kind,
            punt=args.punt,
            restarts=args.restarts,
            bbm=args.bbm,
            bbm_per_game=args.bbm_per_game,
            plan=args.plan,
            plan_slack=args.plan_slack,
        )
    except RoomError as exc:
        raise SystemExit(str(exc)) from exc

    log_path = args.log or Path("logs") / f"draft-{args.season}.jsonl"
    session = DraftSession(
        room, log=DraftLog(log_path), executor=process_executor(room, args.workers)
    )
    print(room.pool_note, flush=True)
    print(
        f"log {log_path}: {len(session.state.picks)} picks replayed"
        + (f", {len(session.replay_warnings)} skipped" if session.replay_warnings else ""),
        flush=True,
    )
    for warning in session.replay_warnings:
        print(f"  {warning}", flush=True)

    if args.page:
        PageFeed(session, args.page, interval=args.interval, trust_money=args.trust_money).start()

    uvicorn.run(create_draft_app(session), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
