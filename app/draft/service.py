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

ESPN's room is connected from the screen, not the command line:

    POST /api/connect {url?}    open the ESPN window on the room (the league's own URL by default)
    POST /api/disconnect        close it
    GET  /api/state             carries `connect`: the URL, whether the window is signed
                                in and reading, and a sentence saying which

Connecting makes the bidder (app/draft/bidder.py) and the feed that reads
the board through its window (`RoomFeed`, below); `--bid --page` at the
command line is the same thing connected at start. Only when the service
can open a browser at all are there three more routes, and the bidder's own
state inside /api/state (see docs/bidding.md):

    POST /api/bid/once          offer the next increment, once; {"amount": n} to type one
    POST /api/bid/arm {max}     hold a maximum for the man on the block
    POST /api/bid/stop          disarm

/api/state carries a `bid` key only while a bidder exists -- which is what
the screen reads to decide whether to draw any of it -- and the three routes
answer 503 until one does. A service made with no way to open a browser
does not define them at all.

A refused pick is a 409 with the rule it broke. A name that matches nobody,
or several, is a 422 with the alternatives. A bid the bidder's own rules
refuse is a 409 with the rule; a bidder that is not running is a 503.
Connecting while connected is a 409; connecting with no URL anywhere is a
422 that says what to set.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.draft.feed import (
    BoardSnapshot,
    LoggedPick,
    OnBlock,
    inferred_picks,
    new_picks,
    parse_board,
)
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


class ConnectIn(BaseModel):
    url: str | None = Field(None, description="the ESPN draft room; omit for the league's own")


#: Makes an unstarted bidder for a URL. The script supplies one that wires
#: the room's cap and the session's version into it; a test supplies one
#: that opens no browser.
BidderFactory = Callable[[str], "Bidder"]


class RoomLink:
    """The bidder and the feed that reads the board through its window,
    made and unmade while the service runs.

    Both used to be built once at start-up from `--bid --page`, so
    connecting to ESPN meant a terminal and a nine-flag command, and
    nothing could be done about a window that had gone wrong short of
    restarting the draft. Now they are made from the screen, on the
    league's own URL or a pasted one, and taken down the same way. A bidder
    started at the command line is one that was connected at start.
    """

    def __init__(
        self,
        session: DraftSession,
        factory: BidderFactory | None = None,
        *,
        default_url: str | None = None,
        bidder: Bidder | None = None,
    ) -> None:
        self.session = session
        self.factory = factory
        self.default_url = default_url
        self.bidder: Bidder | None = None
        self.feed: RoomFeed | None = None
        self._lock = threading.Lock()
        if bidder is not None:
            self._attach(bidder)

    @property
    def possible(self) -> bool:
        """Whether this service can ever have a room to bid in."""
        return self.factory is not None or self.bidder is not None

    @property
    def connected(self) -> bool:
        return self.bidder is not None and self.bidder.is_alive()

    def connect(self, url: str | None) -> None:
        """Open the window on `url`, or on the league's own room. Raises
        `ValueError` when there is nothing to open it on or with."""
        with self._lock:
            if self.connected:
                raise ValueError("already connected to ESPN's room; disconnect first")
            if self.factory is None:
                raise ValueError("this service cannot open a browser")
            target = (url or "").strip() or self.default_url
            if not target:
                raise ValueError(
                    "no draft room URL: paste the room's URL, or set ESPN_LEAGUE_ID, "
                    "ESPN_SWID and FCP_TRACKED_TEAM_ID in .env so it can be built"
                )
            self._drop()
            bidder = self.factory(target)
            bidder.start()
            self._attach(bidder)
        self.session.bump()

    def disconnect(self) -> None:
        with self._lock:
            self._drop()
        self.session.bump()

    def _attach(self, bidder: Bidder) -> None:
        self.bidder = bidder
        self.feed = RoomFeed(self.session, bidder)
        self.feed.start()

    def _drop(self) -> None:
        """Stop both and wait for them, so the next connect starts clean and
        the feed cannot write a stale status over the reset below."""
        feed, bidder = self.feed, self.bidder
        self.feed = self.bidder = None
        if feed is not None:
            feed.stopping.set()
        if bidder is not None:
            bidder.stop()
            bidder.join(timeout=10.0)
        if feed is not None:
            feed.join(timeout=2.0)
            self.session.set_feed_status(mode="typed", url=None, connected=False, error=None)

    def status(self) -> dict[str, Any]:
        """What the screen says about the link, in one sentence: the order a
        connection goes through, and where it is stuck when it is."""
        bidder = self.bidder
        base: dict[str, Any] = {
            "url": bidder.url if bidder is not None else None,
            "default_url": self.default_url,
            "connected": False,
            "signed_in": None,
            "readable": None,
            "room_open": None,
            "message": "not connected",
        }
        if bidder is None:
            return base
        state = bidder.state()
        if not state["running"]:
            return {**base, "message": state["error"] or "the ESPN window has closed"}
        if not state["page_open"]:
            return {**base, "connected": True, "message": "opening the ESPN window…"}
        if state["signed_in"] is False:
            return {
                **base,
                "connected": True,
                "signed_in": False,
                "readable": False,
                "message": "sign in, in the ESPN window",
            }
        if state.get("room_open") is False:
            # No room on the page, and not a fault: the page is still being
            # painted, or the draft has not opened. The second is where the
            # day-before check ends, and it is the answer wanted: the
            # address, the window and the session are all right.
            not_open = state["error"] == "the draft has not opened yet"
            return {
                **base,
                "connected": True,
                "signed_in": state["signed_in"],
                "readable": False,
                "room_open": False,
                "message": (
                    "the draft has not opened yet · ESPN says “Loading your draft”"
                    if not_open
                    else "reading the room…"
                ),
            }
        read = state["room"] is not None and state["error"] is None
        if read:
            message = f"Auction room · read {state['last_read']}"
        elif state["error"]:
            message = str(state["error"])
        else:
            message = "reading the room…"
        return {
            **base,
            "connected": True,
            "signed_in": state["signed_in"],
            "readable": read if state["last_read"] or state["error"] else None,
            "room_open": state.get("room_open"),
            "message": message,
        }


def create_draft_app(
    session: DraftSession,
    *,
    poll: float = 0.25,
    rehearsal: Rehearsal | None = None,
    bidder: Bidder | None = None,
    bidder_factory: BidderFactory | None = None,
    default_url: str | None = None,
    on_ready: Callable[[], None] | None = None,
) -> FastAPI:
    """The app. `on_ready` is called once the server is listening, which is
    when a browser can be pointed at it (scripts/draft_night.py)."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if on_ready is not None:
            on_ready()
        yield

    app = FastAPI(title="FCP Draft", version="0.1.0", lifespan=lifespan)
    link = RoomLink(session, bidder_factory, default_url=default_url, bidder=bidder)

    def snapshot() -> dict[str, Any]:
        """The session's state, the link's, and the bidder's when there is one.

        `bid` is absent rather than null without a bidder, because absent
        is what the screen tests to decide whether to draw any of it: a
        service with no browser must not show a button that bids.
        """
        state = session.snapshot()
        state["connect"] = link.status()
        if link.bidder is not None:
            state["bid"] = link.bidder.state()
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

    # -- the ESPN window --------------------------------------------------

    @app.post("/api/connect")
    def connect(body: ConnectIn | None = None) -> dict[str, Any]:
        if link.connected:
            raise HTTPException(
                status_code=409, detail="already connected to ESPN's room; disconnect first"
            )
        if not link.possible:
            raise HTTPException(status_code=409, detail="this service cannot open a browser")
        try:
            link.connect(body.url if body else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"could not start: {type(exc).__name__}: {exc}"
            ) from exc
        return snapshot()

    @app.post("/api/disconnect")
    def disconnect() -> dict[str, Any]:
        link.disconnect()
        return snapshot()

    # -- bidding, only when the service can open a browser at all ---------
    #
    # Defined inside the `if` rather than guarded by a 404 inside each
    # handler, so that a service with no browser does not advertise them in
    # /docs either. The routes are thin on purpose: every rule they appear
    # to enforce is enforced again in the bidder's own thread, because the
    # screen is not the only thing that can call these.

    if link.possible:

        def _bidder() -> Bidder:
            if link.bidder is None:
                raise HTTPException(
                    status_code=503, detail="not connected to ESPN's room; press Connect"
                )
            return link.bidder

        def _bid_error(exc: Exception) -> HTTPException:
            if isinstance(exc, HTTPException):
                return exc
            if isinstance(exc, ValueError):
                return HTTPException(status_code=409, detail=str(exc))
            return HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}")

        @app.post("/api/bid/once")
        def bid_once(body: TapIn | None = None) -> dict[str, Any]:
            try:
                bidder = _bidder()
                amount = body.amount if body else None
                return bidder.bid_exact(amount) if amount is not None else bidder.bid_once()
            except Exception as exc:
                raise _bid_error(exc) from exc

        @app.post("/api/bid/arm")
        def bid_arm(body: ArmIn) -> dict[str, Any]:
            try:
                _bidder().arm(body.player or "", body.max)
            except Exception as exc:
                raise _bid_error(exc) from exc
            return snapshot()

        @app.post("/api/bid/stop")
        def bid_stop() -> dict[str, Any]:
            try:
                _bidder().disarm("stopped from the screen")
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


# ---------------------------------------------------------------------------
# the same room, read through the bidder's browser
# ---------------------------------------------------------------------------


class RoomFeed(threading.Thread):
    """The board, taken from the window the bidder is already watching.

    `PageFeed` opens a second copy of the draft page with the cookies in
    `.env`, which expire silently: when they have, it reads the sign-in
    page, shows nobody on the block and no picks at all. The bidder has no
    such problem -- it drives the window the manager signed in himself
    (scripts/espn_login.py) and reads the room every half second. So when
    there is a bidder, it is the feed: who is on the block comes straight
    off its read, and a pick is recorded when the block moves on, to
    whoever was leading at the last price seen.

    A sale is only written when the *next* nomination arrives, because the
    price at the moment the gavel falls is the last one the page showed.
    Between the two the block keeps showing the man who has just gone, and
    nothing is computed for a player nobody has nominated yet.
    """

    def __init__(self, session: DraftSession, bidder: Bidder, *, interval: float = 0.5) -> None:
        super().__init__(name="draft-room-feed", daemon=True)
        self.session = session
        self.bidder = bidder
        self.interval = interval
        self.stopping = threading.Event()
        #: The read blanks for a moment at every nomination, and a blank is
        #: not a sale: the last read that had a player is what we hold.
        self._last: dict[str, Any] | None = None
        self._problems: list[str] = []

    def run(self) -> None:
        self.session.set_feed_status(mode="room", url=self.bidder.url, connected=False)
        while not self.stopping.is_set():
            try:
                self.once(self.bidder.state())
            except Exception as exc:  # a bad read must not end the draft
                self.session.set_feed_status(connected=False, error=f"{type(exc).__name__}: {exc}")
            if not self.bidder.is_alive():
                # The window has gone, and its last state has just been
                # read into the status; there is nothing more to poll.
                return
            self.stopping.wait(self.interval)

    def once(self, state: dict[str, Any]) -> None:
        """One pass over the bidder's published state. Separated from the
        loop so a test can drive it a read at a time."""
        session = self.session
        room = state.get("room") if state.get("running") else None
        if not room:
            session.set_feed_status(mode="room", connected=False, error=state.get("error"))
            return
        player = room.get("player")
        if player:
            previous = self._last
            if previous is not None and previous.get("player") != player:
                self._sell(previous)
            self._last = room
            session.set_block(
                OnBlock(
                    player,
                    room.get("current_offer"),
                    room.get("high_bidder"),
                    room.get("espn_value"),
                )
            )
        session.set_feed_status(
            mode="room",
            connected=True,
            error=None,
            last_read=time.strftime("%H:%M:%S"),
            ticker_rows=len(room.get("history") or ()),
            problems=self._problems[-5:] or None,
        )

    def _sell(self, sold: dict[str, Any]) -> None:
        """The block has moved on: the man who was on it went to whoever was
        leading, at the price the page last showed. A nomination nobody ever
        bid on cannot be sold, and is left for the manager to type."""
        player, team, price = sold.get("player"), sold.get("high_bidder"), sold.get("current_offer")
        if not player or not team or not price:
            return
        logged = LoggedPick(self._overall(sold), str(player), str(team), int(price))
        try:
            self.session.apply_logged(logged, source="room")
        except (DraftError, UnknownNameError) as exc:
            self._problems.append(f"{player} to {team} for ${price}: {exc}")

    def _overall(self, sold: dict[str, Any]) -> int:
        """ESPN's own pick number ("PK 15 OF 208") when the read had one,
        and otherwise the next one by our own count."""
        text = str(sold.get("pick") or "")
        digits = "".join(c if c.isdigit() else " " for c in text).split()
        return int(digits[0]) if digits else len(self.session.state.picks) + 1
