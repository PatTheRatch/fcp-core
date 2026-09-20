"""The draft service: a draft session behind HTTP, for the draft screen.

Runs on the manager's own machine, not the VPS. The page reader needs the
manager's ESPN login, and the VPS API is tailnet-only and read-only by
design; a draft is neither. So this is a separate app bound to localhost,
started by `scripts/draft_service.py`, with nothing shared with
`app.main` except the room it loads.

    GET  /                      the draft screen
    POST /api/rehearsal/{pause|resume|skip}   when rehearsing a past draft
    GET  /api/state             everything the screen draws, with a version
    GET  /api/events            server-sent events: the state, each time the version moves
    POST /api/picks             a pick, by name or id
    POST /api/undo              take back the last pick
    POST /api/block             who is on the block, when typing rather than reading
    GET  /api/players?q=        search, most expensive by market price first
    GET  /api/players/{id}      one player's card; starts his ceiling if it is not ready
    GET  /api/pool              the whole board's per-game lines, for the screen's arithmetic
    GET  /api/plan              the best roster we can still finish

With `--bid`, and only then, three more, and the bidder's own state inside
/api/state (see app/draft/bidder.py and docs/bidding.md):

    POST /api/bid/once          offer the next increment, once; {"amount": n} to type one
    POST /api/bid/arm {max}     hold a maximum for the man on the block
    POST /api/bid/stop          disarm

Without `--bid` nothing opens a browser, the three routes are not defined at
all, and /api/state carries no `bid` key -- which is what the screen reads to
decide whether any of it exists.

A refused pick is a 409 with the rule it broke. A name that matches nobody,
or several, is a 422 with the alternatives. A bid the bidder's own rules
refuse is a 409 with the rule; a bidder that is not running is a 503.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.draft.feed import BoardSnapshot, OnBlock, inferred_picks, new_picks, parse_board
from app.draft.room import DraftError
from app.draft.session import DraftSession, UnknownNameError

if TYPE_CHECKING:
    from app.draft.bidder import Bidder
    from app.draft.rehearsal import Rehearsal

SCREEN = Path(__file__).parent / "static" / "draft.html"


class PickIn(BaseModel):
    player: str | None = Field(None, description="a name; loose matching, refused if ambiguous")
    player_id: int | None = None
    team: str | None = Field(None, description="a team name; defaults to us")
    team_id: int | None = None
    price: int


class BlockIn(BaseModel):
    player: str | None = None
    player_id: int | None = None
    current_offer: int | None = None
    high_bidder: str | None = None


class ArmIn(BaseModel):
    max: int = Field(description="the most we will pay for the man on the block")
    player: str | None = Field(
        None, description="who we think is on the block; refused if it is somebody else"
    )


class TapIn(BaseModel):
    amount: int | None = Field(
        None, description="a typed offer; omit for the button's own next increment"
    )


def create_draft_app(
    session: DraftSession,
    *,
    poll: float = 0.25,
    rehearsal: Rehearsal | None = None,
    bidder: Bidder | None = None,
) -> FastAPI:
    app = FastAPI(title="FCP Draft", version="0.1.0")

    def snapshot() -> dict[str, Any]:
        """The session's state, plus the bidder's when there is one.

        The key is absent rather than null without `--bid`, because absent
        is what the screen tests to decide whether to draw any of it: a
        service with no browser must not show a button that bids.
        """
        state = session.snapshot()
        if bidder is not None:
            state["bid"] = bidder.state()
        return state

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def screen() -> str:
        # Read per request, so an edit to the page shows on refresh.
        return SCREEN.read_text()

    @app.post("/api/rehearsal/{action}")
    def rehearse(action: str) -> dict[str, Any]:
        if rehearsal is None:
            raise HTTPException(status_code=404, detail="not rehearsing")
        try:
            return rehearsal.control(action)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _name_error(exc: UnknownNameError) -> HTTPException:
        return HTTPException(
            status_code=422, detail={"message": str(exc), "alternatives": list(exc.alternatives)}
        )

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return snapshot()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            last = -1
            while not await request.is_disconnected():
                if session.version != last:
                    state = await asyncio.to_thread(snapshot)
                    last = state["version"]
                    yield f"event: state\ndata: {json.dumps(state)}\n\n"
                await asyncio.sleep(poll)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/picks")
    def pick(body: PickIn) -> dict[str, Any]:
        try:
            player_id = body.player_id
            if player_id is None:
                if not body.player:
                    raise HTTPException(status_code=422, detail="player or player_id is required")
                player_id = session.player_id(body.player)
            team_id = body.team_id
            if team_id is None:
                team_id = session.team_id(body.team) if body.team else session.state.me
            session.apply(player_id, team_id, body.price)
        except UnknownNameError as exc:
            raise _name_error(exc) from exc
        except DraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return session.snapshot()

    @app.post("/api/undo")
    def undo() -> dict[str, Any]:
        session.undo()
        return session.snapshot()

    @app.post("/api/block")
    def block(body: BlockIn) -> dict[str, Any]:
        if body.player is None and body.player_id is None:
            session.set_block(None)
            return session.snapshot()
        try:
            player_id = body.player_id
            if player_id is None and body.player:
                player_id = session.player_id(body.player)
        except UnknownNameError as exc:
            raise _name_error(exc) from exc
        name = body.player or (session.name_of(player_id) if player_id is not None else "")
        session.set_block(
            OnBlock(name, body.current_offer, body.high_bidder, None), player_id=player_id
        )
        return session.snapshot()

    @app.get("/api/players")
    def players(q: str = "", available: bool = True, limit: int = 25) -> list[dict[str, Any]]:
        return session.search(q, available_only=available, limit=max(1, min(limit, 200)))

    @app.get("/api/players/{player_id}")
    def player(player_id: int, wait: float = 0.0) -> dict[str, Any]:
        session.ceiling(player_id, wait=max(0.0, min(wait, 20.0)))
        return session.card(player_id)

    @app.get("/api/pool")
    def pool() -> dict[str, Any]:
        # The screen's scarcity, its strips and its standings are sums over
        # the whole pool, so the pool goes out once rather than a card at a
        # time. Gated like a card: see DraftSession.pool_view.
        return session.pool_view()

    @app.get("/api/plan")
    def plan() -> dict[str, Any]:
        return session.plan_view()

    # -- bidding, only when the service was started with --bid -------------
    #
    # Defined inside the `if` rather than guarded by a 404 inside each
    # handler, so that a service with no browser does not advertise them in
    # /docs either. The routes are thin on purpose: every rule they appear
    # to enforce is enforced again in the bidder's own thread, because the
    # screen is not the only thing that can call these.

    if bidder is not None:

        def _bid_error(exc: Exception) -> HTTPException:
            if isinstance(exc, ValueError):
                return HTTPException(status_code=409, detail=str(exc))
            return HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}")

        @app.post("/api/bid/once")
        def bid_once(body: TapIn | None = None) -> dict[str, Any]:
            try:
                amount = body.amount if body else None
                return bidder.bid_exact(amount) if amount is not None else bidder.bid_once()
            except Exception as exc:
                raise _bid_error(exc) from exc

        @app.post("/api/bid/arm")
        def bid_arm(body: ArmIn) -> dict[str, Any]:
            try:
                bidder.arm(body.player or "", body.max)
            except Exception as exc:
                raise _bid_error(exc) from exc
            return snapshot()

        @app.post("/api/bid/stop")
        def bid_stop() -> dict[str, Any]:
            try:
                bidder.disarm("stopped from the screen")
            except Exception as exc:
                raise _bid_error(exc) from exc
            return snapshot()

    return app


# ---------------------------------------------------------------------------
# the page reader, on its own thread
# ---------------------------------------------------------------------------


class PageFeed(threading.Thread):
    """Reads the draft page every `interval` seconds into the session.

    Picks in the page's log are applied; ones already applied are skipped,
    so a restart that re-reads the whole board is safe. Picks the money
    implies but the log does not show are only applied with `trust_money`,
    and otherwise reported in the feed status for the screen to raise.
    """

    def __init__(
        self,
        session: DraftSession,
        url: str,
        *,
        interval: float = 2.0,
        trust_money: bool = False,
    ) -> None:
        super().__init__(name="draft-page-feed", daemon=True)
        self.session = session
        self.url = url
        self.interval = interval
        self.trust_money = trust_money
        self.stopping = threading.Event()

    def run(self) -> None:
        from app.draft.page import Cookies, open_draft
        from app.espn import get_espn_settings

        espn = get_espn_settings()
        session = self.session
        teams = list(session.room.team_names.values())
        players = list(session.room.names)
        session.set_feed_status(mode="page", url=self.url, connected=False)
        while not self.stopping.is_set():
            try:
                with open_draft(self.url, Cookies(espn.espn_swid, espn.espn_s2)) as page:
                    session.set_feed_status(connected=True, error=None)
                    previous: BoardSnapshot | None = None
                    while not self.stopping.is_set():
                        board = parse_board(page.text(), teams, players)
                        self._absorb(previous, board)
                        previous = board
                        self.stopping.wait(self.interval)
            except Exception as exc:  # a dropped page must not end the draft
                session.set_feed_status(connected=False, error=f"{type(exc).__name__}: {exc}")
                self.stopping.wait(5.0)

    def _absorb(self, previous: BoardSnapshot | None, board: BoardSnapshot) -> None:
        session = self.session
        problems: list[str] = []
        for logged in new_picks(previous, board):
            try:
                session.apply_logged(logged)
            except (DraftError, UnknownNameError) as exc:
                problems.append(f"{logged.player} to {logged.team} for ${logged.price}: {exc}")
        unlogged = []
        for guess in inferred_picks(previous, board):
            if self.trust_money:
                try:
                    session.apply_logged(guess, source="money")
                except (DraftError, UnknownNameError) as exc:
                    problems.append(f"inferred {guess.player}: {exc}")
            else:
                unlogged.append(f"{guess.player} to {guess.team} for ${guess.price}")
        session.set_block(board.on_block)
        session.set_feed_status(
            last_read=time.strftime("%H:%M:%S"),
            ticker_rows=len(board.ticker),
            problems=problems[-5:] or None,
            unlogged=unlogged or None,
        )
