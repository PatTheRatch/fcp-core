#!/usr/bin/env python3
"""Run the draft service on this machine, for the draft screen.

Usage:
    python scripts/draft_service.py --season 2027 --me "Through The Wire" \\
        --bbm ~/Documents/PatriotGames/player_rankings/BBM_Projections_2027_total.xls \\
        --bbm-per-game ~/Documents/PatriotGames/player_rankings/BBM_Projections_2027_pergame.xls \\
        --page "https://fantasy.espn.com/basketball/draft?leagueId=...&seasonId=2027&teamId=3"

Then http://127.0.0.1:8765/api/state, or /docs for every endpoint.

Without a Basketball Monster membership, `--projection-set <id>` drafts on an
uploaded set instead (scripts/upload_projections.py). The two are exclusive,
and the state and every card name whichever source the room was loaded from.

Every accepted pick and undo is written to --log (by default
logs/draft-<season>.jsonl) and replayed on start, so stopping and starting
this mid-draft loses nothing. Start a fresh draft -- after a mock, say --
with a new --log path, or move the old file aside; the service never
deletes one.

Open http://127.0.0.1:8765 for the draft screen.

REHEARSING

    python scripts/draft_service.py --season 2027 --me "Through The Wire" \\
        --bbm ...total.xls --bbm-per-game ...pergame.xls --rehearse 2026 --seconds 20

replays the league's real 2026 auction, nomination by nomination, into the
2027 room: each player sits on the block for --seconds, and unless we
enter him as ours he goes to the team that really bought him at what they
paid. The log defaults to logs/rehearsal-<season>-<time>.jsonl so a
rehearsal never touches the real draft's log.

Without --page or --rehearse, picks come in through the screen or POST
/api/picks and nothing reads ESPN. The room options (--plan, --restarts, --punt ...) are the same as
scripts/draft_room.py's.

BIDDING

    python scripts/espn_login.py            # once, sign in, leave it open
    python scripts/draft_service.py ... --page "<draft room>" --bid

adds a second browser window that can place bids: a one-tap offer, and a
maximum held for one player until it is reached, the player changes, or the
STOP button is pressed. Off unless --bid is given, and with --bid --no-bid
it rehearses the whole thing without ever clicking. Read docs/bidding.md
before using it on a real draft: every click is real money.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import uvicorn

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.draft.live import RoomError, load_room
from app.draft.rehearsal import Rehearsal, load_nominations
from app.draft.service import PageFeed, RoomFeed, create_draft_app
from app.draft.session import DraftLog, DraftSession, block_executor, process_executor
from app.projections.sources import describe


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True, help="our team's name")
    ap.add_argument("--bbm", type=Path, help="Basketball Monster export, Total Games Value")
    ap.add_argument("--bbm-per-game", type=Path, help="the same export on Per Game Value")
    ap.add_argument(
        "--projection-set",
        type=int,
        help="an uploaded projection set to draft on instead of --bbm",
    )
    ap.add_argument("--page", help="the ESPN draft room URL; omit to enter picks by hand")
    ap.add_argument("--trust-money", action="store_true", help="apply picks inferred from budgets")
    ap.add_argument(
        "--bid",
        action="store_true",
        help="open a second browser that can place bids; needs --page and scripts/espn_login.py",
    )
    ap.add_argument(
        "--no-bid",
        action="store_true",
        help="with --bid: rehearse everything except the final click",
    )
    ap.add_argument("--bid-headless", action="store_true", help="with --bid: no window to watch")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between page reads")
    ap.add_argument("--log", type=Path, help="pick log (default logs/draft-<season>.jsonl)")
    ap.add_argument("--rehearse", type=int, help="replay this season's real draft into the room")
    ap.add_argument("--seconds", type=float, default=20.0, help="seconds per rehearsal nomination")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--workers",
        type=int,
        default=3,
        help="processes precomputing ceilings; one more is reserved for the man on the block",
    )
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
            projection_set=args.projection_set,
            plan=args.plan,
            plan_slack=args.plan_slack,
        )
    except RoomError as exc:
        raise SystemExit(str(exc)) from exc

    if args.page and args.rehearse:
        raise SystemExit("--page and --rehearse are two different feeds; pick one")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    default_log = (
        Path("logs") / f"rehearsal-{args.rehearse}-{stamp}.jsonl"
        if args.rehearse
        else Path("logs") / f"draft-{args.season}.jsonl"
    )
    log_path = args.log or default_log
    session = DraftSession(
        room,
        log=DraftLog(log_path),
        executor=process_executor(room, args.workers),
        on_block=block_executor(room),
    )
    print(room.pool_note, flush=True)
    print(f"source: {room.projection_source} ({describe(room.projection_source)})", flush=True)
    print(
        f"log {log_path}: {len(session.state.picks)} picks replayed"
        + (f", {len(session.replay_warnings)} skipped" if session.replay_warnings else ""),
        flush=True,
    )
    for warning in session.replay_warnings:
        print(f"  {warning}", flush=True)

    rehearsal = None
    if args.page and not args.bid:
        PageFeed(session, args.page, interval=args.interval, trust_money=args.trust_money).start()
    elif args.rehearse:
        factory = make_session_factory(make_engine(get_settings().database_url))
        with factory() as db:
            nominations = load_nominations(db, args.rehearse, room.team_names)
        rehearsal = Rehearsal(session, nominations, seconds=args.seconds)
        rehearsal.start()
        print(f"rehearsing {len(nominations)} nominations from {args.rehearse}", flush=True)

    bidder = None
    if args.bid:
        if not args.page:
            raise SystemExit("--bid needs --page: it bids in the room the URL names")
        from app.draft.bidder import Bidder

        def cap() -> int | None:
            allocation = room.allocation
            return allocation.cap(session.state) if allocation is not None else None

        bidder = Bidder(
            args.page,
            headless=args.bid_headless,
            dry_run=args.no_bid,
            cap=cap,
            on_change=session.bump,
        )
        bidder.start()
        # The bidder's window is signed in and already reading the room
        # every half second, so it is also the board's feed: PageFeed's
        # second copy of the page, on the cookies in `.env`, is not started
        # at all (see RoomFeed in app/draft/service.py).
        RoomFeed(session, bidder).start()
        print(
            "bidding is ON"
            + (" (dry run: nothing will be clicked)" if args.no_bid else "")
            + " -- every click is real money; docs/bidding.md",
            flush=True,
        )

    print(f"draft screen: http://{args.host}:{args.port}", flush=True)
    app = create_draft_app(session, rehearsal=rehearsal, bidder=bidder)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
