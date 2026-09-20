#!/usr/bin/env python3
"""Run the draft service on this machine, for the draft screen.

Usage:
    python scripts/draft_service.py --season 2027 --me "Through The Wire" \\
        --bbm ~/Documents/PatriotGames/player_rankings/BBM_Projections_2027_total.xls \\
        --bbm-per-game ~/Documents/PatriotGames/player_rankings/BBM_Projections_2027_pergame.xls

Then http://127.0.0.1:8765/api/state, or /docs for every endpoint. On the
night, `scripts/draft_night.py` (or `Draft Room.command`) runs this with
every default filled in and opens the screen.

Without a Basketball Monster membership, `--projection-set <id>` drafts on an
uploaded set instead (scripts/upload_projections.py). The two are exclusive,
and the state and every card name whichever source the room was loaded from.

Every accepted pick and undo is written to --log (by default
logs/draft-<season>.jsonl) and replayed on start, so stopping and starting
this mid-draft loses nothing. Start a fresh draft -- after a mock, say --
with a new --log path, or move the old file aside; the service never
deletes one.

Open http://127.0.0.1:8765 for the draft screen.

ESPN'S ROOM

The screen's Connect button opens a browser window on the league's own
draft room (built from ESPN_LEAGUE_ID, ESPN_SWID and FCP_TRACKED_TEAM_ID in
.env, or a pasted URL for a mock), reads the board through it, and can
place bids from the screen. Sign in, in that window, if it asks; the
profile in ~/.fcp-core/espn remembers it. `--page URL --bid` does the same
connect at start-up, and `--page` alone reads the room the older way, on
the cookies in .env.

REHEARSING

    python scripts/draft_service.py --season 2027 --me "Through The Wire" \\
        --bbm ...total.xls --bbm-per-game ...pergame.xls --rehearse 2026 --seconds 20

replays the league's real 2026 auction, nomination by nomination, into the
2027 room: each player sits on the block for --seconds, and unless we
enter him as ours he goes to the team that really bought him at what they
paid. The log defaults to logs/rehearsal-<season>-<time>.jsonl so a
rehearsal never touches the real draft's log.

Without --page or --rehearse, picks come in through the screen or POST
/api/picks and nothing reads ESPN until Connect is pressed. The room
options (--plan, --restarts, --punt ...) are the same as
scripts/draft_room.py's.

BIDDING

Every click in the ESPN window is real money. Read docs/bidding.md before
connecting to a draft that counts. `--no-bid` rehearses the whole thing
without ever clicking, whether connected at start or from the screen.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.draft.live import RoomError, load_room
from app.draft.rehearsal import Rehearsal, load_nominations
from app.draft.room_url import draft_room_url
from app.draft.service import PageFeed, create_draft_app
from app.draft.session import DraftLog, DraftSession, block_executor, process_executor
from app.projections.sources import describe

if TYPE_CHECKING:
    from app.draft.bidder import Bidder


def build_parser(*, require_room: bool = True) -> argparse.ArgumentParser:
    """Every flag. `require_room=False` is for scripts/draft_night.py, which
    fills --season and --me in itself and lets the flags override."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--season", type=int, required=require_room)
    ap.add_argument("--me", required=require_room, help="our team's name")
    ap.add_argument("--bbm", type=Path, help="Basketball Monster export, Total Games Value")
    ap.add_argument("--bbm-per-game", type=Path, help="the same export on Per Game Value")
    ap.add_argument(
        "--projection-set",
        type=int,
        help="an uploaded projection set to draft on instead of --bbm",
    )
    ap.add_argument("--page", help="the ESPN draft room URL; omit to connect from the screen")
    ap.add_argument("--trust-money", action="store_true", help="apply picks inferred from budgets")
    ap.add_argument(
        "--bid",
        action="store_true",
        help="with --page: open the ESPN window at start rather than from the screen",
    )
    ap.add_argument(
        "--no-bid",
        action="store_true",
        help="rehearse everything except the final click, in the ESPN window",
    )
    ap.add_argument("--bid-headless", action="store_true", help="no ESPN window to watch")
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
    return ap


def league_room_url(season: int) -> str | None:
    """The league's own draft room, when .env holds enough to name it. None
    when it does not, or when .env has no ESPN login at all: the box on the
    screen is then empty and waits for a paste."""
    from pydantic import ValidationError

    from app.espn import get_espn_settings

    try:
        espn = get_espn_settings()
    except ValidationError:
        return None
    return draft_room_url(espn.espn_league_id, season, espn.fcp_tracked_team_id, espn.espn_swid)


def serve(args: argparse.Namespace, *, on_ready: Callable[[], None] | None = None) -> int:
    """Load the room and run the service until it is stopped. `on_ready` is
    called once the server is listening, which is when a browser can open."""
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
    if args.bid and not args.page:
        raise SystemExit("--bid needs --page: it opens the room the URL names")
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

    def cap() -> int | None:
        allocation = room.allocation
        return allocation.cap(session.state) if allocation is not None else None

    def make_bidder(url: str) -> Bidder:
        # Imported here, and only here, so that a room that never connects
        # needs nothing of Playwright's installed.
        from app.draft.bidder import Bidder

        return Bidder(
            url,
            headless=args.bid_headless,
            dry_run=args.no_bid,
            cap=cap,
            on_change=session.bump,
        )

    bidder = None
    if args.bid:
        bidder = make_bidder(args.page)
        bidder.start()
        print(
            "connected to ESPN's room at start"
            + (" (dry run: nothing will be clicked)" if args.no_bid else "")
            + " -- every click is real money; docs/bidding.md",
            flush=True,
        )
    default_url = league_room_url(args.season)
    print(
        f"ESPN room: {default_url.split('&memberId=')[0]}..."
        if default_url
        else "ESPN room: no URL in .env; paste one on the screen",
        flush=True,
    )

    print(f"draft screen: http://{args.host}:{args.port}", flush=True)
    app = create_draft_app(
        session,
        rehearsal=rehearsal,
        bidder=bidder,
        bidder_factory=make_bidder,
        default_url=default_url,
        on_ready=on_ready,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main() -> int:
    return serve(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
