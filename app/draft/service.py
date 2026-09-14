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
    GET  /api/plan              the best roster we can still finish

A refused pick is a 409 with the rule it broke. A name that matches nobody,
or several, is a 422 with the alternatives.
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


def create_draft_app(
    session: DraftSession, *, poll: float = 0.25, rehearsal: Rehearsal | None = None
) -> FastAPI:
    app = FastAPI(title="FCP Draft", version="0.1.0")

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
        return session.snapshot()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            last = -1
            while not await request.is_disconnected():
                if session.version != last:
                    snapshot = await asyncio.to_thread(session.snapshot)
                    last = snapshot["version"]
                    yield f"event: state\ndata: {json.dumps(snapshot)}\n\n"
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

    @app.get("/api/plan")
    def plan() -> dict[str, Any]:
        return session.plan_view()

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
