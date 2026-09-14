"""A live draft as a long-running thing: state, a log that survives, and ceilings ready early.

The room itself (`app.draft.room`) is a value: picks in, state out. That is
what makes it testable, and it is not enough on draft day. Three things a
screen needs that a value cannot give:

  A LOG THAT SURVIVES. Every pick and every undo is appended to a JSON-lines
  file the moment it is accepted, and a session opened on the same file
  replays it. A crash, a closed laptop or a refreshed page mid-auction loses
  nothing, because the room is a pure function of its picks.

  CEILINGS BEFORE THEY ARE ASKED FOR. One ceiling is three to ten seconds
  of search (measured on the 2027 BBM pool, 2026-09-14), and a nomination
  gives ninety. The session computes the player on the block first and then
  the likeliest nominations -- the most expensive players still available by
  market price -- in worker processes, keyed on the exact picks they were
  computed against, so an answer for a room that has since moved on is never
  shown. Every pick cancels the queued work it made stale.

  ONE VERSION NUMBER. Anything a screen shows -- a pick, an undo, the block,
  a ceiling landing -- bumps `version`. A client that holds the last version
  it drew needs nothing else to know when to draw again.

Threads, not asyncio: the page reader is Playwright's sync API on its own
thread, requests arrive on the web server's threads, and ceilings come back
on the executor's. One lock guards the state.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.draft.feed import LoggedPick, OnBlock, match_name, match_team
from app.draft.live import Room, market_prices
from app.draft.optimizer import Candidate, RosterPlan
from app.draft.room import (
    Allocation,
    Ceiling,
    DraftError,
    DraftState,
    Pick,
    bid_ceiling,
    inflation,
    reprice,
    resolve,
)
from app.draft.targets import CategoryDistribution


class UnknownNameError(LookupError):
    """A name that matches nobody, or more than one player or team."""

    def __init__(self, message: str, alternatives: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.alternatives = tuple(alternatives)


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------


class DraftLog:
    """Append-only JSON lines: one accepted pick or undo per line."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamped = {**event, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        with self.path.open("a") as handle:
            handle.write(json.dumps(stamped) + "\n")
            handle.flush()


# ---------------------------------------------------------------------------
# ceilings, off the request thread
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CeilingContext:
    """Everything a worker needs besides the state and the player."""

    candidates: tuple[Candidate, ...]
    distributions: tuple[CategoryDistribution, ...]
    lineup: tuple[str, ...]
    limits: dict[str, int]
    punt: tuple[str, ...]
    restarts: int
    allocation: Allocation | None


_CONTEXT: CeilingContext | None = None


def _install(context: CeilingContext) -> None:
    global _CONTEXT
    _CONTEXT = context


def _compute(state: DraftState, player_id: int) -> Ceiling:
    context = _CONTEXT
    if context is None:
        raise RuntimeError("ceiling worker started without its context")
    return bid_ceiling(
        state,
        player_id,
        context.candidates,
        context.distributions,
        punt=context.punt,
        lineup=context.lineup,
        limits=context.limits,
        restarts=context.restarts,
        allocation=context.allocation,
    )


def context_for(room: Room) -> CeilingContext:
    return CeilingContext(
        candidates=tuple(room.candidates),
        distributions=tuple(room.distributions),
        lineup=tuple(room.lineup),
        limits=dict(room.limits),
        punt=tuple(room.punt),
        restarts=room.restarts,
        allocation=room.allocation,
    )


def process_executor(room: Room, workers: int = 2) -> Executor:
    """Worker processes, each holding the pool once rather than per task."""
    import multiprocessing

    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_install,
        initargs=(context_for(room),),
    )


# ---------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------

#: How many of the likeliest nominations to have ceilings ready for.
PRECOMPUTE = 12


class DraftSession:
    def __init__(
        self,
        room: Room,
        *,
        log: DraftLog | None = None,
        executor: Executor | None = None,
        precompute: int = PRECOMPUTE,
    ) -> None:
        self.room = room
        self.log = log
        self.executor = executor
        self.precompute = precompute
        self._lock = threading.RLock()
        self._state = room.state
        self._version = 0
        self._block: OnBlock | None = None
        self._block_id: int | None = None
        self._ceilings: dict[tuple[tuple[Pick, ...], int], Ceiling] = {}
        self._pending: dict[tuple[tuple[Pick, ...], int], Future[Ceiling]] = {}
        self._plans: dict[tuple[Pick, ...], RosterPlan] = {}
        self._priced: (
            tuple[tuple[Pick, ...], dict[int, tuple[int | None, str]], dict[int, int]] | None
        ) = None
        self._names_by_id: dict[int, str] = {}
        for name, player_id in room.names.items():
            self._names_by_id.setdefault(player_id, name)
        for c in room.candidates:
            self._names_by_id[c.player_id] = c.name
        self._by_id = {c.player_id: c for c in room.candidates}
        self.feed_status: dict[str, Any] = {"mode": "typed"}
        self.replay_warnings: list[str] = []
        if log is not None:
            self._replay(log.read())
        self._schedule()

    # -- reading --------------------------------------------------------

    @property
    def state(self) -> DraftState:
        return self._state

    @property
    def version(self) -> int:
        return self._version

    def name_of(self, player_id: int) -> str:
        return self._names_by_id.get(player_id, f"#{player_id}")

    # -- resolving names ------------------------------------------------

    def player_id(self, text: str) -> int:
        """The player a typed or scraped name means. Raises when unsure."""
        exact = self.room.names.get(text)
        if exact is not None:
            return exact
        found = match_name(text, self.room.names)
        if found is None:
            raise UnknownNameError(f"no player matches {text!r}")
        if found.rival:
            raise UnknownNameError(
                f"{text!r} could be {found.name} or {found.rival}", (found.name, found.rival)
            )
        return found.value

    def team_id(self, text: str) -> int:
        name = match_team(text, self.room.team_names.values())
        if name is None:
            raise UnknownNameError(
                f"no single team matches {text!r}", self.room.team_names.values()
            )
        return next(tid for tid, n in self.room.team_names.items() if n == name)

    # -- writing --------------------------------------------------------

    def apply(self, player_id: int, team_id: int, price: int, *, source: str = "typed") -> Pick:
        with self._lock:
            pick = Pick(player_id, team_id, price)
            self._state = self._state.apply(pick)  # raises DraftError, logs nothing
            if self.log is not None:
                self.log.append(
                    {
                        "type": "pick",
                        "player_id": player_id,
                        "name": self.name_of(player_id),
                        "team_id": team_id,
                        "price": price,
                        "source": source,
                    }
                )
            if self._block_id == player_id:
                self._block, self._block_id = None, None
            self._changed()
            return pick

    def undo(self) -> Pick | None:
        with self._lock:
            if not self._state.picks:
                return None
            last = self._state.picks[-1]
            self._state = self._state.undo()
            if self.log is not None:
                self.log.append({"type": "undo", "player_id": last.player_id})
            self._changed()
            return last

    def apply_logged(self, logged: LoggedPick, *, source: str = "page") -> Pick | None:
        """A pick read off the page. One already applied is quietly skipped,
        which is what makes re-reading a whole board after a restart safe."""
        try:
            player_id = self.player_id(logged.player)
        except UnknownNameError:
            # Nobody on any list we hold. The room needs a unique id, not a
            # known one; money and places are what matter.
            player_id = -(abs(hash(logged.player)) % 10**9) - 1
            self.room.names[logged.player] = player_id
            self._names_by_id[player_id] = logged.player
        if player_id in self._state.taken:
            return None
        return self.apply(player_id, self.team_id(logged.team), logged.price, source=source)

    def set_block(self, block: OnBlock | None, player_id: int | None = None) -> None:
        """Who is on the block, as read or as typed."""
        with self._lock:
            if block is not None and player_id is None:
                try:
                    player_id = self.player_id(block.player)
                except UnknownNameError:
                    player_id = None
            if block == self._block and player_id == self._block_id:
                return
            self._block, self._block_id = block, player_id
            self._changed()

    def set_feed_status(self, **status: Any) -> None:
        with self._lock:
            if {**self.feed_status, **status} != self.feed_status:
                self.feed_status = {**self.feed_status, **status}
                self._version += 1

    # -- ceilings and plans ----------------------------------------------

    def ceiling(self, player_id: int, *, wait: float = 0.0) -> Ceiling | None:
        """The ceiling for the current room if it is ready; starts it if not."""
        key = (self._state.picks, player_id)
        with self._lock:
            ready = self._ceilings.get(key)
            if ready is not None or player_id in self._state.taken:
                return ready
            future = self._submit(self._state, player_id, front=True)
        if wait > 0 and future is not None:
            try:
                return future.result(timeout=wait)
            except Exception:  # still running, or failed; the caller shows pending
                return self._ceilings.get(key)
        return None

    def plan(self) -> RosterPlan:
        """The best roster we can still finish. Cached per room."""
        picks = self._state.picks
        cached = self._plans.get(picks)
        if cached is not None:
            return cached
        room = self.room
        plan = resolve(
            self._state,
            room.candidates,
            room.distributions,
            punt=room.punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=room.restarts,
            allocation=room.allocation,
        )
        with self._lock:
            self._plans[picks] = plan
        return plan

    def wait_for_ceilings(self, timeout: float = 30.0) -> None:
        """Block until queued ceilings finish. For tests and scripts."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                pending = [f for f in self._pending.values() if not f.done()]
            if not pending:
                return
            time.sleep(0.02)

    # -- internals ------------------------------------------------------

    def _prices(self) -> tuple[dict[int, tuple[int | None, str]], dict[int, int]]:
        """Market and board prices for the current room, computed once per room."""
        picks = self._state.picks
        if self._priced is None or self._priced[0] != picks:
            market = market_prices(self.room, self._state)
            board = {c.player_id: c.price for c in reprice(self._state, self.room.candidates)}
            self._priced = (picks, market, board)
        return self._priced[1], self._priced[2]

    def _replay(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            try:
                if event.get("type") == "pick":
                    pick = Pick(int(event["player_id"]), int(event["team_id"]), int(event["price"]))
                    self._state = self._state.apply(pick)
                    name = event.get("name")
                    if name and pick.player_id not in self._names_by_id:
                        self._names_by_id[pick.player_id] = str(name)
                        self.room.names.setdefault(str(name), pick.player_id)
                elif event.get("type") == "undo":
                    self._state = self._state.undo()
            except (DraftError, KeyError, ValueError) as exc:
                self.replay_warnings.append(f"skipped {event}: {exc}")

    def _changed(self) -> None:
        """Called under the lock after anything a screen would redraw for."""
        self._version += 1
        picks = self._state.picks
        for key, future in list(self._pending.items()):
            if key[0] != picks:
                future.cancel()
                del self._pending[key]
        self._schedule()

    def _likeliest(self) -> list[int]:
        """Players still available, most expensive by market price first."""
        state = self._state
        market, _ = self._prices()
        priced = [
            (market.get(c.player_id, (None, ""))[0] or 0, c.player_id)
            for c in self.room.candidates
            if c.player_id not in state.taken
        ]
        priced.sort(reverse=True)
        return [pid for _, pid in priced[: self.precompute]]

    def _schedule(self) -> None:
        if self.executor is None:
            return
        if self._block_id is not None and self._block_id not in self._state.taken:
            self._submit(self._state, self._block_id, front=True)
        for player_id in self._likeliest():
            self._submit(self._state, player_id)

    def _submit(
        self, state: DraftState, player_id: int, *, front: bool = False
    ) -> Future[Ceiling] | None:
        if self.executor is None or player_id not in self._by_id:
            return None
        key = (state.picks, player_id)
        if key in self._ceilings:
            return None
        existing = self._pending.get(key)
        if existing is not None and not existing.cancelled():
            return existing
        # A process pool cannot reorder its queue, so "front" means: cancel
        # the precomputations not yet started, run this, and requeue them.
        requeue: list[int] = []
        if front:
            for other_key, future in list(self._pending.items()):
                if other_key != key and future.cancel():
                    del self._pending[other_key]
                    requeue.append(other_key[1])
        future = self.executor.submit(_compute, state, player_id)
        self._pending[key] = future

        def landed(done: Future[Ceiling], key: tuple[tuple[Pick, ...], int] = key) -> None:
            self._landed(key, done)

        future.add_done_callback(landed)
        for other in requeue:
            self._submit(state, other)
        return future

    def _landed(self, key: tuple[tuple[Pick, ...], int], future: Future[Ceiling]) -> None:
        if future.cancelled():
            return
        error = future.exception()
        with self._lock:
            self._pending.pop(key, None)
            if error is not None:
                self.feed_status = {**self.feed_status, "ceiling_error": repr(error)}
                self._version += 1
                return
            self._ceilings[key] = future.result()
            if key[0] == self._state.picks:
                self._version += 1

    # -- what a screen draws ---------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            room = self.room
            nominator = state.to_nominate()
            places = room.allocation.open_places(state) if room.allocation else None
            return {
                "version": self._version,
                "season": room.season,
                "me": state.me,
                "budget": state.budget,
                "roster_slots": state.roster_slots,
                "minimum_bid": state.minimum_bid,
                "pick_number": len(state.picks),
                "total_picks": state.roster_slots * len(state.teams),
                "complete": state.complete,
                "inflation": round(inflation(state, room.candidates), 3),
                "field_ceiling": state.field_ceiling(),
                "to_nominate": (
                    {"team_id": nominator, "name": room.team_names.get(nominator)}
                    if nominator is not None
                    else None
                ),
                "teams": [
                    {
                        "team_id": t.team_id,
                        "name": t.name,
                        "remaining": t.remaining,
                        "open_slots": t.open_slots,
                        "max_bid": t.max_bid(state.minimum_bid),
                        "is_me": t.team_id == state.me,
                        "roster": [
                            {
                                "player_id": p.player_id,
                                "name": self.name_of(p.player_id),
                                "price": p.price,
                            }
                            for p in t.picks
                        ],
                    }
                    for t in sorted(state.teams.values(), key=lambda t: t.team_id)
                ],
                "picks": [
                    {
                        "number": i + 1,
                        "player_id": p.player_id,
                        "name": self.name_of(p.player_id),
                        "team_id": p.team_id,
                        "team": state.teams[p.team_id].name,
                        "price": p.price,
                    }
                    for i, p in enumerate(state.picks)
                ],
                "plan": (
                    {
                        "source": room.plan_source,
                        "open_places": list(places or ()),
                        "cap": room.allocation.cap(state) if room.allocation else None,
                    }
                    if room.allocation is not None
                    else None
                ),
                "block": self._block_card(),
                "feed": dict(self.feed_status),
                "pool": room.pool_note,
                "stand_in": room.stand_in,
                "replay_warnings": list(self.replay_warnings),
            }

    def _block_card(self) -> dict[str, Any] | None:
        if self._block is None and self._block_id is None:
            return None
        card = self.card(self._block_id) if self._block_id is not None else None
        block = self._block
        return {
            "player": card,
            "name": block.player if block else (card or {}).get("name"),
            "current_offer": block.current_offer if block else None,
            "high_bidder": block.high_bidder if block else None,
            "espn_value": block.espn_value if block else None,
        }

    def card(self, player_id: int) -> dict[str, Any]:
        """Everything the room knows about one player, for this room."""
        with self._lock:
            state = self._state
            room = self.room
            candidate = self._by_id.get(player_id)
            market, boards = self._prices()
            going, going_source = market.get(player_id, (None, "not on the board"))
            board = boards.get(player_id)
            taken = next((p for p in state.picks if p.player_id == player_id), None)
            row = room.bbm.get(player_id)
            ceiling = self._ceilings.get((state.picks, player_id))
            pending = (state.picks, player_id) in self._pending
            bbm = None
            if row is not None:
                total = row.league_dollars if row.league_dollars is not None else row.dollars
                per_game = room.per_game_dollars.get(player_id)
                bbm = {
                    "age": row.age,
                    "games": row.games,
                    "injury_risk": row.injury_risk or None,
                    "injury": row.injury or None,
                    "league_total": _rounded(total),
                    "league_per_game": _rounded(per_game),
                    "injury_discount": (
                        _rounded(per_game - total)
                        if total is not None and per_game is not None and per_game - total >= 8
                        else None
                    ),
                    "espn_avg": _rounded(row.espn_dollars),
                    "yahoo_avg": _rounded(row.yahoo_dollars),
                }
            return {
                "player_id": player_id,
                "name": self.name_of(player_id),
                "position": candidate.position if candidate else None,
                "eligible": sorted(candidate.eligible) if candidate else [],
                "on_board": candidate is not None,
                "board_price": board,
                "market_price": going,
                "market_source": going_source,
                "taken": (
                    {
                        "team_id": taken.team_id,
                        "team": state.teams[taken.team_id].name,
                        "price": taken.price,
                    }
                    if taken
                    else None
                ),
                "bbm": bbm,
                "ceiling": _ceiling_view(ceiling, going, pending),
            }

    def search(
        self, text: str = "", *, available_only: bool = True, limit: int = 25
    ) -> list[dict[str, Any]]:
        with self._lock:
            state = self._state
            wanted = text.strip().lower()
            ids = [
                c.player_id
                for c in self.room.candidates
                if (not available_only or c.player_id not in state.taken)
                and (not wanted or wanted in c.name.lower())
            ]
            market, _ = self._prices()
            priced = sorted(ids, key=lambda pid: -(market.get(pid, (None, ""))[0] or 0))
            return [self.card(pid) for pid in priced[:limit]]

    def plan_view(self) -> dict[str, Any]:
        plan = self.plan()
        owned = self._state.mine.player_ids
        return {
            "expected_wins": round(plan.expected_wins, 3),
            "cost": plan.cost,
            "players": [
                {
                    "player_id": c.player_id,
                    "name": c.name,
                    "price": c.price,
                    "owned": c.player_id in owned,
                }
                for c in sorted(plan.players, key=lambda c: (c.player_id not in owned, -c.price))
            ],
            "win_probability": {k: round(v, 3) for k, v in plan.win_probability.items()},
        }


def _rounded(value: float | None) -> int | None:
    return None if value is None else round(value)


def _ceiling_view(ceiling: Ceiling | None, going: int | None, pending: bool) -> dict[str, Any]:
    if ceiling is None:
        return {"status": "pending" if pending else "not_started"}
    marginal = ceiling.marginal_at_floor
    if ceiling.price is None:
        verdict = "do not bid"
    elif going is not None and going > ceiling.price:
        verdict = "expected to go for more than he is worth to us"
    else:
        verdict = "inside our ceiling"
    return {
        "status": "ready",
        "price": ceiling.price,
        "max_bid": ceiling.max_bid,
        "field": ceiling.field,
        "plan_cap": ceiling.plan_cap,
        "capped": ceiling.capped,
        "marginal_at_floor": None if marginal == float("-inf") else round(marginal, 3),
        "verdict": verdict,
    }
