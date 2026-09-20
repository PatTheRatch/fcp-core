"""Bidding, on the auction page itself: one tap, or a maximum held for us.

WHY THIS EXISTS

`app.draft.feed` reads the draft. This places bids in it. On the night the
gap between "the screen says he is worth $71" and "$71 is entered before the
clock runs out" is a person typing into ESPN's own form with seven seconds
left, and the thing that person actually wants is not a faster keyboard: it
is to decide a number once, early, and have the room keep him in up to it.
So there are two controls and no others -- offer the next increment now, and
hold my maximum -- and everything else in this module is the arithmetic of
refusing to do something stupid with them.

WHAT IT DRIVES

One Playwright page of its own, on the persistent Chromium profile in
`~/.fcp-core/espn` -- the same one `scripts/espn_login.py` uses -- so a
sign-in done in this window is remembered for the next one. Headed by
default: a window Patrick can watch, and can take over by clicking in it
himself, because the last safety rule is a human with a mouse. When ESPN
wants a sign-in, the window is where it happens: the bidder recognises the
sign-in page, says so once, and keeps polling until the room reads. The
page reader (`app.draft.page`) keeps its own page; these two never share
one, so a bid can never be blocked behind a read.

THREE LAYERS, AND WHY

  READING is `read_room`, a function of a `PageLike` -- anything that can
  return the text of a CSS selector. Playwright is one implementation;
  `StaticPage`, over saved HTML, is the other, and it is what lets every
  selector below be tested against a real auction room with no browser and
  no live draft. When ESPN moves its markup, that test fails first.

  DECIDING is `decide`, a pure function of one `RoomView` and one
  `ArmState`. Every safety rule lives in it. It cannot click anything, so
  the rules can be tested exhaustively in microseconds.

  DOING is `Bidder`, a thread with a command queue that reads, calls
  `decide`, and clicks. It owns the only mutable state and the only browser.

THE SELECTORS ARE ALL IN ONE PLACE

`SELECTORS`, below, and nowhere else. Each entry is a tuple tried in order,
most specific first, so a class ESPN renames falls back to a looser match
rather than to nothing. When none of them resolve, `read_room` raises
`SelectorError` -- loudly, into the bidder's log and the screen -- and three
of those in a row disarm. The failure is never silent, because a proxy
bidder that has quietly stopped reading the page is worse than no proxy
bidder at all.

THE HONEST CAVEAT

This clicks buttons on ESPN's website, and each click is real money in a
real auction. It is a fast hand, not an agent: it holds one number, on one
player, that a person typed seconds earlier, and it stops at the first thing
it does not understand. See docs/bidding.md.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

#: The Chromium profile the bidder's window runs on, shared with
#: `scripts/espn_login.py`: a sign-in done in either window is kept here,
#: so the next window opens signed in. The `espn_s2` cookie in `.env` does
#: not authenticate a browser any more.
PROFILE_DIR = Path.home() / ".fcp-core" / "espn"

#: The signed-in session as a file, rewritten each time the room reads
#: signed in, because a headless job elsewhere loads it.
STATE_FILE = Path.home() / ".fcp-core" / "espn-state.json"

#: What ESPN's sign-in page says, in place of a room, when the profile's
#: session has lapsed. Either phrase on a page with no room on it means
#: "sign in, in the ESPN window" and not "the markup has moved".
SIGNED_OUT = ("Log in Required", "Log In to ESPN")

#: How long to wait for the auction room to render before giving up on it.
LOAD_TIMEOUT_MS = 45_000

#: Never two bids closer together than this, whatever the page says.
MIN_BID_INTERVAL = 1.0

#: Reads in a row that found no room before an armed bidder gives up.
MAX_MISSES = 3

#: Reads in a row with nobody on the block before that counts as the block
#: moving on. One is a render gap between nominations; three is the truth.
VANISH_READS = 3

#: ESPN's custom-bid box is `maxlength="2"`, so a typed offer cannot exceed
#: two digits. Above this the one-tap increment button is the only way up.
CUSTOM_BID_MAX = 99


# ---------------------------------------------------------------------------
# the selectors -- the only place any of them appear
# ---------------------------------------------------------------------------

#: Every hook into ESPN's auction room, most specific first. Verified against
#: a live practice auction on 2026-09-20 and against the saved snapshot the
#: parse tests read (tests/fixtures/espn_auction_room.html).
SELECTORS: dict[str, tuple[str, ...]] = {
    # The nominated player's card.
    "player_name": (
        '[data-testid="player-selected"] .playerinfo__playername',
        ".player-selected .playerinfo__playername",
    ),
    "player_team": (
        '[data-testid="player-selected"] .playerinfo__playerteam',
        ".player-selected .playerinfo__playerteam",
    ),
    "player_pos": (
        '[data-testid="player-selected"] .playerinfo__playerpos',
        ".player-selected .playerinfo__playerpos",
    ),
    "espn_value": ("span.player-default-bid", ".player-default-bid"),
    # "Current offer: $52" and "Manual offer (max $80)", in one element.
    "labels": (".bidding-form__labels",),
    # The one-tap button. Its label carries the next increment -- "Offer $53"
    # -- and goes empty while we are the high bidder, so an empty label here
    # is information and not a miss.
    "offer_button": (
        "form.bidding-form__default button.bid-player__button",
        "button.bid-player__button",
    ),
    # The custom amount: a box and a second button labelled just "Offer".
    "custom_input": ("input.opening-bid__form-input", ".opening-bid__form-input"),
    "custom_button": ("form.bidding-form__custom button.bid-player__button",),
    # Newest first: "$52 Joe Mazzulla's Search History".
    "history": (".bid-history__list li.bid", ".bid-history__list li"),
    "clock": (".clock__digits",),
    "pick_label": (".clock__label",),
    # Our own row in the pick train: team name, money left, live bid.
    "our_team": (".auction-pick-component--own .team-name",),
    "our_cash": (".auction-pick-component--own .cash",),
    "our_bid": (".auction-pick-component--own .bid-amount",),
}

_DOLLARS = re.compile(r"\$(\d+)")
_CURRENT_OFFER = re.compile(r"CURRENT\s+OFFER:?\s*\$(\d+)", re.IGNORECASE)
_LEGAL_MAX = re.compile(r"MAX\s*\$(\d+)", re.IGNORECASE)
_HISTORY_ROW = re.compile(r"^\s*\$(\d+)\s+(.+?)\s*$")
_LEADING_NUMBER = re.compile(r"^\s*\d+\.\s*")


class SelectorError(RuntimeError):
    """The page did not look like an auction room. Always reported."""


class SignInRequiredError(SelectorError):
    """The page is ESPN's sign-in page. Reported once, and not as a fault:
    the fix is a person signing in, in the window, and the bidder waits."""


# ---------------------------------------------------------------------------
# what a page has to be able to do
# ---------------------------------------------------------------------------


class PageLike(Protocol):
    """The whole surface the bidder needs. Playwright is one of two."""

    def texts(self, selector: str) -> list[str]:
        """The text of every element matching `selector`, in document order."""

    def enabled(self, selector: str) -> bool:
        """Whether the first match is an enabled control."""

    def click(self, selector: str) -> None: ...

    def fill(self, selector: str, value: str) -> None: ...

    def remember(self) -> None:
        """Keep the signed-in session for next time, where there is a way to."""

    def close(self) -> None: ...


def _candidates(page: PageLike, key: str) -> list[str]:
    """The texts of the first selector in `key`'s list that matches anything.

    A selector that matches an element with no text has matched: the offer
    button goes blank while we lead, and falling through to a looser selector
    there would find the *custom* button and read its label as an increment.
    """
    for selector in SELECTORS[key]:
        found = page.texts(selector)
        if found:
            return found
    return []


def _money(text: str | None) -> int | None:
    if not text:
        return None
    match = _DOLLARS.search(text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# one read of the room
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomView:
    """Everything one look at the auction page says. A value, always."""

    player: str | None
    player_team: str | None
    player_pos: str | None
    espn_value: int | None
    current_offer: int | None
    #: The most ESPN will let us offer: "MANUAL OFFER (MAX $80)".
    legal_max: int | None
    #: The one-tap button's own number -- what a click would bid.
    next_increment: int | None
    #: The one-tap button is present, labelled and not disabled.
    offer_ready: bool
    our_team: str | None
    our_cash: int | None
    our_bid: int | None
    leading: bool
    high_bidder: str | None
    clock: str | None
    pick_label: str | None
    history: tuple[str, ...]
    #: Selector keys that resolved to nothing. Empty is the happy path.
    missing: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "player_team": self.player_team,
            "player_pos": self.player_pos,
            "espn_value": self.espn_value,
            "current_offer": self.current_offer,
            "legal_max": self.legal_max,
            "next_increment": self.next_increment,
            "offer_ready": self.offer_ready,
            "our_team": self.our_team,
            "our_cash": self.our_cash,
            "our_bid": self.our_bid,
            "leading": self.leading,
            "high_bidder": self.high_bidder,
            "clock": self.clock,
            "pick": self.pick_label,
            "history": list(self.history),
            "missing": list(self.missing),
        }


def _high_bidder(history: Sequence[str]) -> str | None:
    """Whose money is on the table, from the newest row of the bid history."""
    for row in history:
        match = _HISTORY_ROW.match(row)
        if match:
            return match.group(2).strip()
    return None


def read_room(page: PageLike) -> RoomView:
    """One snapshot of the auction room. Raises `SelectorError` on a page
    that is not one -- a logged-out redirect, a lobby, a renamed markup.

    Every selector is asked for exactly once. On a Playwright page each ask
    is a round trip to the browser, and this runs twice a second under a
    clock that gives seven.
    """
    found = {key: _candidates(page, key) for key in SELECTORS}
    first = {key: (rows[0] if rows else None) for key, rows in found.items()}
    missing = [key for key, rows in found.items() if not rows]
    labels, button, name = first["labels"], first["offer_button"], first["player_name"]
    if labels is None and button is None and name is None:
        # Only asked for on a page with no room on it, so the happy path
        # pays nothing for the check.
        body = " ".join(page.texts("body"))
        if any(marker in body for marker in SIGNED_OUT):
            raise SignInRequiredError("sign in, in the ESPN window")
        raise SelectorError(
            "no bidding form, offer button or player card on the page; "
            f"the room's markup has moved (tried {SELECTORS['labels'][0]!r}, "
            f"{SELECTORS['offer_button'][0]!r}, {SELECTORS['player_name'][0]!r})"
        )

    offer_match = _CURRENT_OFFER.search(labels or "")
    max_match = _LEGAL_MAX.search(labels or "")
    history = tuple(row.strip() for row in found["history"] if row.strip())
    our_team_raw = first["our_team"]
    our_team = _LEADING_NUMBER.sub("", our_team_raw).strip() if our_team_raw else None
    our_bid = _money(first["our_bid"])
    current_offer = int(offer_match.group(1)) if offer_match else None
    high = _high_bidder(history)
    # Two independent signs of who is winning, because acting on the wrong
    # one bids against ourselves: the newest row of the history, and our own
    # live bid standing at the current offer.
    leading = bool(
        (our_team is not None and high is not None and high == our_team)
        or (our_bid is not None and current_offer is not None and our_bid == current_offer)
    )
    return RoomView(
        player=(name or "").strip() or None,
        player_team=(first["player_team"] or "").strip() or None,
        player_pos=(first["player_pos"] or "").strip() or None,
        espn_value=_money(first["espn_value"]),
        current_offer=current_offer,
        legal_max=int(max_match.group(1)) if max_match else None,
        next_increment=_money(button),
        offer_ready=bool(
            button and _money(button) is not None and page.enabled(SELECTORS["offer_button"][0])
        ),
        our_team=our_team,
        our_cash=_money(first["our_cash"]),
        our_bid=our_bid,
        leading=leading,
        high_bidder=high,
        clock=(first["clock"] or "").strip() or None,
        pick_label=(first["pick_label"] or "").strip() or None,
        history=history,
        missing=tuple(missing),
    )


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

BID = "bid"
WAIT = "wait"
STOP = "stop"

#: Why an armed bidder stopped. Every one of these reaches the screen.
WON = "won"
PLAYER_CHANGED = "player changed"
SELECTOR = "selector"
ERROR = "error"
ASKED = "stopped"


@dataclass(frozen=True)
class ArmState:
    """A maximum held for one player, and what we have already done with it."""

    player: str
    maximum: int
    #: The plan's cap on one player, when the room has one. A second
    #: ceiling, below the maximum, that the maximum was checked against
    #: when it was set and is checked against again on every bid.
    cap: int | None = None
    last_bid_amount: int | None = None
    last_bid_against: int | None = None
    last_bid_at: float | None = None
    #: Whether we held the high bid at the last read that still showed our
    #: player. It is how the block moving on becomes "we won him".
    was_leading: bool = False
    #: Consecutive reads with nobody on the block at all.
    vanished: int = 0
    waiting: str = "armed"


@dataclass(frozen=True)
class Decision:
    act: str
    amount: int | None
    reason: str
    #: Set only when `act` is STOP.
    stop_reason: str | None = None


def _wait(reason: str) -> Decision:
    return Decision(WAIT, None, reason)


def decide(view: RoomView, armed: ArmState, *, now: float) -> Decision:
    """What an armed bidder should do about this read of the page.

    Pure, total, and the only place a bid is ever authorised. The order is
    the order of the rules: who is on the block first, then whether we are
    already winning, then every ceiling, then the two rate limits.
    """
    # 1. One player, and only the one we were armed for.
    if view.player is not None and view.player != armed.player:
        if armed.was_leading:
            return Decision(
                STOP,
                None,
                f"we won {armed.player} at ${armed.last_bid_amount or '?'}; "
                f"{view.player} is on the block now",
                WON,
            )
        return Decision(
            STOP,
            None,
            f"the block moved from {armed.player} to {view.player}",
            PLAYER_CHANGED,
        )
    if view.player is None:
        # Nobody readable on the block. A read or two like this is the gap
        # between nominations rendering; either way we do not bid into a
        # page that cannot tell us who we would be bidding on.
        if armed.vanished >= VANISH_READS:
            if armed.was_leading:
                return Decision(STOP, None, f"we won {armed.player}; the block is empty", WON)
            return Decision(STOP, None, f"{armed.player} left the block without us", PLAYER_CHANGED)
        return _wait("the page is not showing anybody on the block")

    # 2. Never bid against ourselves.
    if view.leading:
        return _wait(f"we hold the high bid at ${view.current_offer}")

    # 3. There has to be a button with a number on it.
    amount = view.next_increment
    if amount is None or not view.offer_ready:
        return _wait("no offer button on the page to press")

    # 4. Every ceiling, lowest first, so the reason names the binding one.
    if amount > armed.maximum:
        return _wait(f"${amount} is over the ${armed.maximum} maximum we were given")
    if armed.cap is not None and amount > armed.cap:
        return _wait(f"${amount} is over the plan's ${armed.cap} cap")
    if view.legal_max is not None and amount > view.legal_max:
        return _wait(f"${amount} is over the ${view.legal_max} ESPN will let us offer")

    # 5. One bid per change in the offer. A frozen page bids once and stops:
    #    the increment only rises when the offer does, so an increment we
    #    have already bid means we are looking at a page that has not moved.
    if armed.last_bid_amount is not None and amount <= armed.last_bid_amount:
        return _wait(f"we already offered ${armed.last_bid_amount}; the page has not moved on")
    if armed.last_bid_against is not None and view.current_offer == armed.last_bid_against:
        return _wait(f"the offer is still the ${armed.last_bid_against} we bid against")

    # 6. And one bid a second, whatever else the page says.
    if armed.last_bid_at is not None and now - armed.last_bid_at < MIN_BID_INTERVAL:
        return _wait("one bid a second")

    return Decision(BID, amount, f"outbid at ${view.current_offer}; offering ${amount}")


def check_maximum(maximum: int, view: RoomView | None, cap: int | None) -> None:
    """Whether a maximum may be armed at all. Raises `ValueError` with the
    reason, which is the text the screen shows and the API returns."""
    if maximum < 1:
        raise ValueError("a maximum has to be at least $1")
    if view is None or view.player is None:
        raise ValueError("nobody is on the block")
    if cap is not None and maximum > cap:
        raise ValueError(f"${maximum} is over the plan's ${cap} cap for one player")
    if view.legal_max is not None and maximum > view.legal_max:
        raise ValueError(f"${maximum} is over the ${view.legal_max} ESPN will let us offer")
    if view.our_cash is not None and maximum > view.our_cash:
        raise ValueError(f"${maximum} is over the ${view.our_cash} we have left")


# ---------------------------------------------------------------------------
# a page made of saved HTML, for the tests and for a snapshot on the night
# ---------------------------------------------------------------------------

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
_BLOCK = {"div", "li", "p", "tr", "br", "form", "ul", "ol", "section", "h1", "h2", "h3", "td"}
_COMPOUND = re.compile(r"([a-zA-Z0-9_-]+)|\.([a-zA-Z0-9_-]+)|\[([a-zA-Z-]+)=\"([^\"]*)\"\]|#(\S+)")


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None = None
    children: list[_Node] = field(default_factory=list)
    text: str = ""

    def inner_text(self) -> str:
        parts: list[str] = []

        def walk(node: _Node) -> None:
            for child in node.children:
                parts.append(child.text)
                walk(child)
                if child.tag in _BLOCK:
                    parts.append("\n")

        parts.append(self.text)
        walk(self)
        return re.sub(r"[ \t]+", " ", "".join(parts)).strip()

    def ancestors(self) -> Iterator[_Node]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    """Enough of an HTML tree to run the selectors above over saved markup.

    Not a browser and not trying to be: it exists so that every selector the
    bidder uses on the night is exercised in the test suite against a real
    auction room, with no browser and no live draft.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self._open: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self._open[-1]
        node = _Node(tag, {k: (v or "") for k, v in attrs}, parent=parent)
        parent.children.append(node)
        if tag not in _VOID:
            self._open.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        self._open[-1].children.append(_Node("#text", {}, parent=self._open[-1], text=data))

    def walk(self) -> Iterator[_Node]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.tag not in ("#document", "#text"):
                yield node
            stack.extend(reversed(node.children))


def _matches(node: _Node, compound: str) -> bool:
    classes = set(node.attrs.get("class", "").split())
    for tag, klass, attr, value, ident in _COMPOUND.findall(compound):
        if tag and node.tag != tag:
            return False
        if klass and klass not in classes:
            return False
        if attr and node.attrs.get(attr) != value:
            return False
        if ident and node.attrs.get("id") != ident:
            return False
    return True


class StaticPage:
    """A `PageLike` over saved HTML. Reads; refuses to pretend it can click."""

    def __init__(self, html: str) -> None:
        self._tree = _Tree()
        self._tree.feed(html)
        self._nodes = list(self._tree.walk())

    @classmethod
    def from_file(cls, path: Path) -> StaticPage:
        return cls(path.read_text(encoding="utf-8", errors="replace"))

    def _select(self, selector: str) -> list[_Node]:
        parts = [p for p in selector.split(" ") if p]
        found = []
        for node in self._nodes:
            if not _matches(node, parts[-1]):
                continue
            wanted = list(reversed(parts[:-1]))
            for ancestor in node.ancestors():
                if wanted and _matches(ancestor, wanted[0]):
                    wanted.pop(0)
            if not wanted:
                found.append(node)
        return found

    def texts(self, selector: str) -> list[str]:
        return [node.inner_text() for node in self._select(selector)]

    def enabled(self, selector: str) -> bool:
        found = self._select(selector)
        return bool(found) and "disabled" not in found[0].attrs

    def click(self, selector: str) -> None:
        raise SelectorError("a saved page cannot be clicked")

    def fill(self, selector: str, value: str) -> None:
        raise SelectorError("a saved page cannot be typed into")

    def remember(self) -> None:
        return None

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# the real page
# ---------------------------------------------------------------------------


class PlaywrightPage:
    """A `PageLike` over a live Playwright page, and the context behind it."""

    def __init__(
        self,
        page: Any,
        playwright: Any = None,
        context: Any = None,
        state_file: Path | None = None,
    ) -> None:
        self._page = page
        self._playwright = playwright
        self._context = context
        self._state_file = state_file

    def texts(self, selector: str) -> list[str]:
        return [str(t) for t in self._page.locator(selector).all_inner_texts()]

    def enabled(self, selector: str) -> bool:
        locator = self._page.locator(selector).first
        return bool(locator.is_enabled(timeout=1000))

    def click(self, selector: str) -> None:
        self._page.locator(selector).first.click(timeout=4000)

    def fill(self, selector: str, value: str) -> None:
        self._page.locator(selector).first.fill(value, timeout=4000)

    def remember(self) -> None:
        """The profile already keeps the session; the file is for the code
        that reads one. Failing to write it is not the draft's problem."""
        if self._context is None or self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(self._state_file))
        except Exception:
            pass

    def close(self) -> None:
        for closer, how in ((self._context, "close"), (self._playwright, "stop")):
            try:
                if closer is not None:
                    getattr(closer, how)()
            except Exception:
                pass


def open_room(
    url: str,
    *,
    headless: bool = False,
    profile_dir: Path = PROFILE_DIR,
    state_file: Path = STATE_FILE,
) -> PlaywrightPage:
    """A browser on the auction room, on the profile that remembers ESPN.

    Headed unless told otherwise: the window is Patrick's to watch and to
    take over with his own mouse, which is the last safety rule and the only
    one this module cannot implement. It is also where he signs in when ESPN
    asks, and because the profile persists, once is enough. One window at a
    time: Chromium refuses a profile another window already has open, so
    `scripts/espn_login.py` and this cannot run together.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment
        raise SelectorError(
            "bidding needs Playwright: pip install -e '.[live]' && playwright install chromium"
        ) from exc
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
    return PlaywrightPage(page, playwright, context, state_file)


# ---------------------------------------------------------------------------
# the bidder
# ---------------------------------------------------------------------------

#: How many placed bids the screen keeps in view.
LOG_LINES = 40


class Bidder(threading.Thread):
    """One page, one thread, one command queue, one maximum at a time.

    Every request from the service is a command on the queue rather than a
    call into the page, because Playwright's sync API belongs to the thread
    that made it. The thread reads the room every `poll` seconds, asks
    `decide` what to do, and does exactly that and nothing else.
    """

    def __init__(
        self,
        url: str,
        *,
        headless: bool = False,
        dry_run: bool = False,
        poll: float = 0.5,
        profile_dir: Path = PROFILE_DIR,
        state_file: Path = STATE_FILE,
        open_page: Callable[[], PageLike] | None = None,
        cap: Callable[[], int | None] | None = None,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="draft-bidder", daemon=True)
        self.url = url
        self.dry_run = dry_run
        self.poll = poll
        self._open_page = open_page or (
            lambda: open_room(
                url, headless=headless, profile_dir=profile_dir, state_file=state_file
            )
        )
        self._cap = cap or (lambda: None)
        self._on_change = on_change or (lambda: None)
        self._clock = clock
        self._lock = threading.RLock()
        self._commands: queue.Queue[tuple[str, Any, Future[Any]]] = queue.Queue()
        self._stopping = threading.Event()
        self._page: PageLike | None = None
        #: Whether ESPN is showing us a room or its sign-in page. None until
        #: the first read says either; back to False if the session lapses
        #: mid-draft, and True again when the room reads.
        self._signed_in: bool | None = None
        self._last_read: str | None = None
        self._view: RoomView | None = None
        self._armed: ArmState | None = None
        self._misses = 0
        self._error: str | None = None
        self._stopped_because: str | None = None
        self._log: list[dict[str, Any]] = []
        self._last_action: dict[str, Any] | None = None
        self._published: dict[str, Any] | None = None

    # -- what the service calls ------------------------------------------

    def _ask(self, name: str, payload: Any = None, *, timeout: float = 12.0) -> Any:
        if not self.is_alive():
            raise RuntimeError("the bidder is not running")
        future: Future[Any] = Future()
        self._commands.put((name, payload, future))
        return future.result(timeout=timeout)

    def bid_once(self) -> dict[str, Any]:
        """One tap: click the offer button at its own next increment."""
        return dict(self._ask("once"))

    def bid_exact(self, amount: int) -> dict[str, Any]:
        """A typed amount, through the custom box."""
        return dict(self._ask("exact", int(amount)))

    def arm(self, player_key: str, maximum: int) -> dict[str, Any]:
        """Hold `maximum` for `player_key`, who must be the man on the block."""
        return dict(self._ask("arm", (str(player_key), int(maximum))))

    def disarm(self, reason: str = "stopped by hand") -> dict[str, Any]:
        return dict(self._ask("disarm", str(reason)))

    def stop(self) -> None:
        self._stopping.set()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> dict[str, Any]:
        armed, view = self._armed, self._view
        return {
            "running": self.is_alive() and not self._stopping.is_set(),
            "dry_run": self.dry_run,
            "page_open": self._page is not None,
            "signed_in": self._signed_in,
            "last_read": self._last_read,
            "error": self._error,
            "armed": armed is not None,
            "player": armed.player if armed else None,
            "maximum": armed.maximum if armed else None,
            "cap": armed.cap if armed else None,
            "waiting": armed.waiting if armed else None,
            "stopped_because": self._stopped_because,
            "room": view.as_json() if view else None,
            "last_action": self._last_action,
            "log": list(self._log[-LOG_LINES:]),
        }

    # -- the loop ---------------------------------------------------------

    def run(self) -> None:
        try:
            page = self._open_page()
        except Exception as exc:
            self._note(f"could not open the ESPN window: {type(exc).__name__}: {exc}")
            with self._lock:
                self._error = f"could not open the ESPN window: {type(exc).__name__}: {exc}"
            self._publish(force=True)
            self._refuse_queued()
            return
        with self._lock:
            self._page = page
        rehearsing = " (dry run: nothing will be clicked)" if self.dry_run else ""
        self._note(f"watching the auction room{rehearsing}")
        try:
            while not self._stopping.is_set():
                self._commands_once()
                self._tick()
                self._publish()
                self._stopping.wait(self.poll)
        finally:
            self._refuse_queued()
            if self._page is not None:
                self._page.close()

    def _refuse_queued(self) -> None:
        """Answer anything still waiting, rather than leave a request hanging
        on a thread that has stopped. A caller has to be told either way."""
        while True:
            try:
                _, _, future = self._commands.get_nowait()
            except queue.Empty:
                return
            if not future.done():
                future.set_exception(RuntimeError(self._error or "the bidder has stopped"))

    def _commands_once(self) -> None:
        while True:
            try:
                name, payload, future = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                future.set_result(self._handle(name, payload))
            except Exception as exc:
                future.set_exception(exc)

    def _handle(self, name: str, payload: Any) -> dict[str, Any]:
        if name == "arm":
            player, maximum = payload
            return self._arm(player, maximum)
        if name == "disarm":
            self._disarm(ASKED, str(payload))
            return self.state()
        if name == "once":
            return self._tap(None)
        if name == "exact":
            return self._tap(int(payload))
        raise RuntimeError(f"unknown command {name!r}")

    def _tick(self) -> None:
        try:
            view = self._read()
        except SignInRequiredError as exc:
            self._signed_out(str(exc))
            return
        except SelectorError as exc:
            self._miss(f"could not read the room: {exc}")
            return
        except Exception as exc:
            self._miss(f"reading the room raised {type(exc).__name__}: {exc}")
            return
        with self._lock:
            self._misses = 0
            armed = self._armed
            if armed is None:
                return
            if view.player == armed.player:
                armed = replace(armed, vanished=0, was_leading=view.leading)
            elif view.player is None:
                armed = replace(armed, vanished=armed.vanished + 1)
            self._armed = armed
        decision = decide(view, armed, now=self._clock())
        if decision.act == STOP:
            self._disarm(decision.stop_reason or ERROR, decision.reason)
        elif decision.act == BID:
            self._place(decision.amount or 0, view, decision.reason)
        else:
            with self._lock:
                if self._armed is not None:
                    self._armed = replace(self._armed, waiting=decision.reason)

    def _read(self) -> RoomView:
        if self._page is None:
            raise SelectorError("the bidder has no page")
        view = read_room(self._page)
        with self._lock:
            self._view = view
            self._error = None
            self._last_read = time.strftime("%H:%M:%S")
            newly = self._signed_in is not True
            self._signed_in = True
        if newly:
            # The room read, so the session is good: keep it for next time.
            self._page.remember()
        return view

    def _signed_out(self, message: str) -> None:
        """ESPN is showing its sign-in page. Said once, not every half
        second, and then waited out: the fix is a person signing in, in the
        window, and the next read that finds the room ends it. A maximum
        cannot survive it, because there is no room to hold it in."""
        with self._lock:
            first = self._signed_in is not False
            self._signed_in = False
            self._error = message
            armed = self._armed
        if first:
            self._note(f"ESPN wants a sign-in: {message}")
        if armed is not None:
            self._disarm(SELECTOR, f"ESPN asked for a sign-in: {message}")

    def _miss(self, message: str) -> None:
        with self._lock:
            self._misses += 1
            self._error = message
            misses, armed = self._misses, self._armed
        # The room has no bidding form for a second or two at every
        # nomination, so a read or two failing is the draft working, not a
        # fault. Only a run of them is worth a line on the screen: one at
        # MAX_MISSES, where a maximum would be dropped anyway, and nothing
        # after that until reads come back.
        if misses == MAX_MISSES:
            self._note(f"{misses} reads in a row failed: {message}")
        if armed is not None and misses >= MAX_MISSES:
            self._disarm(SELECTOR, f"{misses} reads in a row failed: {message}")

    # -- doing things -----------------------------------------------------

    def _arm(self, player: str, maximum: int) -> dict[str, Any]:
        view = self._read()
        cap = self._cap()
        check_maximum(maximum, view, cap)
        assert view.player is not None  # check_maximum refused None
        if player and player.strip().lower() != view.player.strip().lower():
            raise ValueError(f"{view.player} is on the block, not {player}")
        with self._lock:
            self._armed = ArmState(
                player=view.player, maximum=maximum, cap=cap, was_leading=view.leading
            )
            self._stopped_because = None
        self._note(f"holding up to ${maximum} for {view.player}")
        self._publish(force=True)
        return self.state()

    def _disarm(self, reason: str, detail: str) -> None:
        with self._lock:
            was = self._armed
            self._armed = None
            self._stopped_because = detail if was is not None else self._stopped_because
        if was is not None:
            self._note(f"disarmed ({reason}): {detail}")
        self._publish(force=True)

    def _place(self, amount: int, view: RoomView, why: str) -> None:
        """One bid. The rate limits are recorded before the click, so a click
        that fails cannot be retried in a loop."""
        now = self._clock()
        with self._lock:
            if self._armed is None:
                return
            self._armed = replace(
                self._armed,
                last_bid_amount=amount,
                last_bid_against=view.current_offer,
                last_bid_at=now,
                waiting=why,
            )
        if self.dry_run:
            self._note(f"dry run: would have offered ${amount} ({why})", bid=True)
            return
        try:
            assert self._page is not None
            self._page.click(SELECTORS["offer_button"][0])
        except Exception as exc:
            self._note(f"the offer button would not click: {type(exc).__name__}: {exc}")
            self._disarm(ERROR, f"clicking ${amount} failed: {type(exc).__name__}: {exc}")
            return
        self._note(f"offered ${amount} for {view.player} ({why})", bid=True)

    def _tap(self, amount: int | None) -> dict[str, Any]:
        """A bid a person asked for, by hand. The same rules, minus the maximum."""
        view = self._read()
        if view.player is None:
            raise ValueError("nobody is on the block")
        if view.leading:
            raise ValueError(f"we already hold the high bid at ${view.current_offer}")
        if amount is None:
            if not view.offer_ready or view.next_increment is None:
                raise ValueError("there is no offer button to press")
            amount = view.next_increment
            selector, typed = SELECTORS["offer_button"][0], None
        else:
            if amount < 1 or amount > CUSTOM_BID_MAX:
                raise ValueError(f"a typed offer has to be between $1 and ${CUSTOM_BID_MAX}")
            if view.next_increment is not None and amount < view.next_increment:
                raise ValueError(f"${amount} does not beat the ${view.current_offer} on the table")
            selector, typed = SELECTORS["custom_button"][0], str(amount)
        cap = self._cap()
        if cap is not None and amount > cap:
            raise ValueError(f"${amount} is over the plan's ${cap} cap for one player")
        if view.legal_max is not None and amount > view.legal_max:
            raise ValueError(f"${amount} is over the ${view.legal_max} ESPN will let us offer")
        if self.dry_run:
            self._note(f"dry run: would have offered ${amount} by hand", bid=True)
            return {"before": view.as_json(), "after": view.as_json(), "amount": amount}
        assert self._page is not None
        if typed is not None:
            self._page.fill(SELECTORS["custom_input"][0], typed)
        self._page.click(selector)
        self._note(f"offered ${amount} for {view.player} by hand", bid=True)
        after = self._read()
        return {"before": view.as_json(), "after": after.as_json(), "amount": amount}

    # -- saying so --------------------------------------------------------

    def _note(self, text: str, *, bid: bool = False) -> None:
        entry = {"at": time.strftime("%H:%M:%S"), "text": text, "bid": bid}
        with self._lock:
            self._log.append(entry)
            del self._log[:-LOG_LINES]
            self._last_action = entry
        self._publish(force=True)

    def _publish(self, *, force: bool = False) -> None:
        """Bump the session's version, but only when something a reader would
        notice changed. The clock ticks every second and is not one of those:
        it would turn the screen's stream into a metronome."""
        with self._lock:
            state = self._state_locked()
            compare = {**state, "room": dict(state["room"] or {})}
            compare["room"].pop("clock", None)
            changed = force or compare != self._published
            self._published = compare
        if changed:
            self._on_change()
