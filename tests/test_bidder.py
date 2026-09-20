"""Bidding: the rules, and the selectors they are read through.

Two halves, and the split is the point.

THE RULES are `app.draft.bidder.decide`, a pure function, and the loop body
that acts on it. They are driven here through a fake auction room -- real
markup in the shape ESPN renders, built by `room_html` below and read by the
same `read_room` that runs on the night -- so a test can outbid us, freeze
the page, swap the player or break the markup and watch what the bidder
does. Nothing here opens a browser.

THE SELECTORS are checked against `tests/fixtures/espn_auction_room.html`, a
saved copy of a real ESPN auction room (2026-09-20, member id stripped,
stylesheets and scripts emptied, every element left alone). Every hook in
`SELECTORS` is asserted against it, including the ones whose job is to *not*
match: the room lists seventy-odd other players with the same class names as
the man on the block, and a selector that picks up one of those would bid on
the wrong player's card. When ESPN moves its markup this file fails first,
which is the whole reason the snapshot is in the repository.

There is deliberately no test against live ESPN. A test that places a real
bid in a real auction is not a test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.draft.bidder import (
    BID,
    MAX_MISSES,
    MIN_BID_INTERVAL,
    PLAYER_CHANGED,
    SELECTORS,
    STOP,
    VANISH_READS,
    WAIT,
    WON,
    ArmState,
    Bidder,
    RoomNotOpenError,
    RoomView,
    SelectorError,
    SignInRequiredError,
    StaticPage,
    check_maximum,
    decide,
    read_room,
)

SNAPSHOT = Path(__file__).parent / "fixtures" / "espn_auction_room.html"
SIGN_IN = Path(__file__).parent / "fixtures" / "espn_sign_in.html"

#: The room's address before the draft opens, as seen on the league's real
#: room on 2026-09-20: the page shell and this one line. Not a capture.
NOT_OPEN_HTML = '<html><body><div id="__next"><p>Loading your draft</p></div></body></html>'


# ---------------------------------------------------------------------------
# a fake auction room
# ---------------------------------------------------------------------------


def room_html(
    *,
    player: str = "Pascal Siakam",
    offer: int | None = 52,
    legal: int = 80,
    button: str | None = "Offer $53",
    disabled: bool = False,
    history: tuple[str, ...] = ("$52 Joe Mazzulla's Search History",),
    our_team: str = "Through The Wire",
    cash: int = 91,
    our_bid: str = "$null",
    clock: str = "00:07",
) -> str:
    """ESPN's markup, cut down to the elements `SELECTORS` names.

    The nesting and the class names are copied from the snapshot, so a fake
    room and a real one are read by exactly the same code down to the last
    selector -- which is what makes these rules tests worth anything.
    """
    rows = "".join(f'<li class="bid di truncate">{row}</li>' for row in history)
    current = f"Current offer: ${offer}" if offer is not None else ""
    return f"""
    <div data-testid="player-selected" class="jsx-1 player-selected flex">
      <div class="player-selected__player-info-container dib">
        <span class="playerinfo__playername">{player}</span>
        <span class="playerinfo__playerteam">IND</span>
        <span class="playerinfo__playerpos ttu">PF</span>
        <span class="player-default-bid clr-gray-04"><span>Pre-Draft Val:
          <span class="fw-normal">$49</span></span></span>
      </div>
      <div data-testid="bidding-form" class="jsx-2 bidding-form__container flex">
        <div class="jsx-2 bidding-form__labels flex flex-column">
          <div class="current-amount">{current}</div>
          <div class="manual-bid self-end">Manual offer (max ${legal})</div>
        </div>
        <div class="jsx-2 bidding-form">
          <form class="bidding-form__default mb3 flex items-center">
            <button class="Button Button--default bid-player__button"
              {'disabled=""' if disabled else ""}>{button if button is not None else ""}</button>
          </form>
          <form class="bidding-form__custom flex items-center">
            <input type="text" id="bid__input" class="form__control opening-bid__form-input"
              maxlength="2" value="">
            <button class="Button bid-player__button ttu">Offer</button>
          </form>
        </div>
      </div>
    </div>
    <div class="jsx-3 bid-history__container">
      <ul class="bid-history__list overflow-hidden w-100">{rows}</ul>
    </div>
    <div data-testid="clock" class="jsx-4 clock__container">
      <div class="jsx-4 clock__label ttu truncate">PK 23 OF 208</div>
      <div class="jsx-4 clock__digits"><span class="clock__digit">{clock}</span></div>
    </div>
    <ul class="picklist">
      <li class="picklist--item"><div data-testid="auction-pick"
        class="jsx-5 auction-pick-component">
        <div class="content auction-pick-component--own">
          <div class="jsx-5 team-name truncate">10. {our_team}</div>
          <div class="cash">${cash}</div>
          <div class="bid-amount" style="opacity: 0;">{our_bid}</div>
        </div></div></li>
    </ul>
    """


class FakeRoom:
    """A `PageLike` whose markup a test can rewrite between reads."""

    def __init__(self, **state: Any) -> None:
        self.state: dict[str, Any] = dict(state)
        self.clicks: list[str] = []
        self.typed: list[tuple[str, str]] = []
        self.blank = False
        #: ESPN has swapped the room for its sign-in page.
        self.signed_out = False
        #: The draft has not opened: the address shows "Loading your draft".
        self.not_open = False
        self.remembered = 0
        self.raises: Exception | None = None
        self._page = StaticPage(room_html(**self.state))
        self._sign_in = StaticPage.from_file(SIGN_IN)
        self._not_open = StaticPage(NOT_OPEN_HTML)
        #: `blank`: a page with words on it that are not the room's, which
        #: is what a moved markup looks like. A page with no words at all
        #: is "still rendering", and a different test.
        self._elsewhere = StaticPage(
            "<html><body><h1>Fantasy Basketball</h1><p>Welcome back.</p></body></html>"
        )

    def set(self, **state: Any) -> None:
        self.state.update(state)
        self._page = StaticPage(room_html(**self.state))

    def texts(self, selector: str) -> list[str]:
        if self.raises is not None:
            raise self.raises
        if self.signed_out:
            return self._sign_in.texts(selector)
        if self.not_open:
            return self._not_open.texts(selector)
        return self._elsewhere.texts(selector) if self.blank else self._page.texts(selector)

    def enabled(self, selector: str) -> bool:
        away = self.blank or self.signed_out or self.not_open
        return False if away else self._page.enabled(selector)

    def click(self, selector: str) -> None:
        self.clicks.append(selector)

    def fill(self, selector: str, value: str) -> None:
        self.typed.append((selector, value))

    def remember(self) -> None:
        self.remembered += 1

    def close(self) -> None:
        return None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float = 2.0) -> None:
        self.now += seconds


def make_bidder(room: FakeRoom, *, dry_run: bool = False, cap: int | None = None) -> Bidder:
    """A bidder wired to a fake room, never started.

    The thread is not run: the tests call `_tick` themselves, which is the
    real loop body, one read at a time, with no sleeping and no races.
    """
    bidder = Bidder(
        "http://example.invalid/draft",
        dry_run=dry_run,
        open_page=lambda: room,
        cap=lambda: cap,
        clock=Clock(),
    )
    bidder._page = room
    return bidder


def view(**state: Any) -> RoomView:
    return read_room(StaticPage(room_html(**state)))


# ---------------------------------------------------------------------------
# reading a room
# ---------------------------------------------------------------------------


def test_a_fake_room_reads_the_way_a_real_one_does() -> None:
    found = view()

    assert found.player == "Pascal Siakam"
    assert found.current_offer == 52 and found.next_increment == 53
    assert found.legal_max == 80 and found.our_cash == 91
    assert found.our_team == "Through The Wire", "the pick train numbers the row; we do not"
    assert found.high_bidder == "Joe Mazzulla's Search History"
    assert found.leading is False
    assert found.missing == ()


def test_the_page_says_we_are_leading_two_independent_ways() -> None:
    """Either sign is enough, because acting on the wrong one bids against
    ourselves: the newest row of the bid history, and our own live bid
    standing at the current offer."""
    by_history = view(history=("$52 Through The Wire",))
    by_own_bid = view(our_bid="$52")

    assert by_history.leading is True and by_history.high_bidder == "Through The Wire"
    assert by_own_bid.leading is True
    assert view().leading is False


def test_a_blank_offer_button_is_information_and_not_a_miss() -> None:
    """ESPN empties the one-tap button's label while we hold the high bid.
    A looser fallback selector there would find the *custom* button and read
    its "Offer" as a bid, so the first selector that matches anything wins
    even when what it matched has no text."""
    found = view(button="", history=("$52 Through The Wire",))

    assert found.next_increment is None and found.offer_ready is False
    assert found.leading is True


def test_a_disabled_offer_button_is_not_ready() -> None:
    assert view(disabled=True).offer_ready is False


def test_a_page_that_is_not_an_auction_room_raises_loudly() -> None:
    page = FakeRoom()
    page.blank = True

    with pytest.raises(SelectorError) as caught:
        read_room(page)

    assert "markup has moved" in str(caught.value)
    assert SELECTORS["offer_button"][0] in str(caught.value), "the failure names what it tried"


def test_the_room_before_the_draft_is_recognised_and_is_not_a_moved_markup() -> None:
    """Three weeks out, the league's own room address shows "Loading your
    draft" and nothing else, for as long as you leave it. The first Connect
    against it said the markup had moved, in red -- when it was the best
    news the day-before check can give: right address, window and session."""
    with pytest.raises(RoomNotOpenError) as caught:
        read_room(StaticPage(NOT_OPEN_HTML))

    assert str(caught.value) == "the draft has not opened yet"
    assert isinstance(caught.value, SelectorError), "still a page that is not a room"


def test_a_page_with_nothing_on_it_yet_is_still_rendering_and_not_a_moved_markup() -> None:
    """Every Connect reads a blank page for its first second or two, and it
    showed as red for those seconds. Blank is "not yet", and waited out."""
    with pytest.raises(RoomNotOpenError) as caught:
        read_room(StaticPage('<html><body><div id="__next"></div></body></html>'))

    assert str(caught.value) == "the page has not rendered yet"


def test_the_sign_in_page_is_recognised_and_is_not_a_moved_markup() -> None:
    """A lapsed session gets ESPN's sign-in page in place of the room. It has
    none of the room's hooks either, and for an hour one morning that read as
    a selector failure and a board with "Nobody" on it. The page says what it
    is, and so does the error."""
    with pytest.raises(SignInRequiredError) as caught:
        read_room(StaticPage.from_file(SIGN_IN))

    assert str(caught.value) == "sign in, in the ESPN window"
    assert isinstance(caught.value, SelectorError), "still a page that is not a room"


# ---------------------------------------------------------------------------
# the rules, one at a time
# ---------------------------------------------------------------------------


def armed(maximum: int = 70, **kwargs: Any) -> ArmState:
    return ArmState(player="Pascal Siakam", maximum=maximum, **kwargs)


def test_it_bids_when_we_are_outbid_and_the_next_increment_is_inside_the_maximum() -> None:
    call = decide(view(), armed(), now=1000.0)

    assert call.act == BID and call.amount == 53
    assert "outbid at $52" in call.reason


def test_it_never_bids_while_we_already_hold_the_high_bid() -> None:
    call = decide(view(history=("$52 Through The Wire",)), armed(), now=1000.0)

    assert call.act == WAIT and "high bid" in call.reason


def test_it_never_bids_above_the_maximum_it_was_given() -> None:
    call = decide(view(offer=52, button="Offer $53"), armed(maximum=52), now=1000.0)

    assert call.act == WAIT and "over the $52 maximum" in call.reason


def test_it_never_bids_above_the_plans_cap() -> None:
    call = decide(view(), armed(maximum=70, cap=45), now=1000.0)

    assert call.act == WAIT and "over the plan's $45 cap" in call.reason


def test_it_never_bids_above_the_legal_maximum_the_page_reports() -> None:
    """ESPN's own ceiling -- what our money and our open places allow -- and
    it is re-read on every tick, not only when the maximum was set."""
    call = decide(view(legal=52), armed(maximum=70), now=1000.0)

    assert call.act == WAIT and "over the $52 ESPN will let us offer" in call.reason


def test_the_lowest_ceiling_is_the_one_the_reason_names() -> None:
    call = decide(view(legal=50), armed(maximum=40, cap=45), now=1000.0)

    assert "over the $40 maximum" in call.reason, "the maximum binds before the cap or ESPN"


def test_a_page_that_stops_changing_produces_exactly_one_bid() -> None:
    frozen = view()
    first = decide(frozen, armed(), now=1000.0)
    after = armed(last_bid_amount=53, last_bid_against=52, last_bid_at=1000.0)

    again = decide(frozen, after, now=2000.0)

    assert first.act == BID
    assert again.act == WAIT and "the page has not moved on" in again.reason


def test_one_bid_per_change_in_the_offer() -> None:
    after = armed(last_bid_amount=53, last_bid_against=52, last_bid_at=1000.0)

    moved = decide(view(offer=57, button="Offer $58"), after, now=2000.0)

    assert moved.act == BID and moved.amount == 58, "the room moved on, so we may answer once"


def test_at_most_one_bid_a_second() -> None:
    after = armed(last_bid_amount=53, last_bid_against=52, last_bid_at=1000.0)
    outbid = view(offer=57, button="Offer $58")

    too_soon = decide(outbid, after, now=1000.0 + MIN_BID_INTERVAL / 2)
    later = decide(outbid, after, now=1000.0 + MIN_BID_INTERVAL)

    assert too_soon.act == WAIT and too_soon.reason == "one bid a second"
    assert later.act == BID


def test_the_block_changing_to_somebody_else_disarms() -> None:
    call = decide(view(player="Alperen Sengun"), armed(), now=1000.0)

    assert call.act == STOP and call.stop_reason == PLAYER_CHANGED
    assert "moved from Pascal Siakam to Alperen Sengun" in call.reason


def test_the_block_changing_while_we_led_is_a_player_won() -> None:
    call = decide(
        view(player="Alperen Sengun"),
        armed(was_leading=True, last_bid_amount=53),
        now=1000.0,
    )

    assert call.act == STOP and call.stop_reason == WON
    assert "we won Pascal Siakam at $53" in call.reason


def test_an_empty_block_has_to_stay_empty_before_it_counts_as_the_block_moving() -> None:
    """One read with nobody on the block is a render gap between nominations.
    Disarming on that would lose the player mid-war for nothing."""
    gap = view(player="")

    waiting = decide(gap, armed(vanished=1), now=1000.0)
    assert waiting.act == WAIT and "not showing anybody" in waiting.reason
    assert decide(gap, armed(vanished=VANISH_READS), now=1000.0).act == STOP


def test_with_no_button_there_is_nothing_to_press() -> None:
    call = decide(view(button=""), armed(), now=1000.0)

    assert call.act == WAIT and "no offer button" in call.reason


# ---------------------------------------------------------------------------
# arming
# ---------------------------------------------------------------------------


def test_a_maximum_is_refused_above_the_cap_the_legal_max_and_our_money() -> None:
    for maximum, cap, expected in (
        (90, 45, "plan's $45 cap"),
        (90, None, "$80 ESPN will let us offer"),
        (85, None, "$80 ESPN will let us offer"),
        (0, None, "at least $1"),
    ):
        with pytest.raises(ValueError, match=expected.replace("$", r"\$")):
            check_maximum(maximum, view(), cap)

    with pytest.raises(ValueError, match=r"\$20 we have left"):
        check_maximum(30, view(cash=20, legal=99), None)


def test_a_maximum_is_refused_when_nobody_is_on_the_block() -> None:
    with pytest.raises(ValueError, match="nobody is on the block"):
        check_maximum(20, view(player=""), None)

    with pytest.raises(ValueError, match="nobody is on the block"):
        check_maximum(20, None, None)


def test_arming_names_the_player_it_was_armed_for_and_refuses_anybody_else() -> None:
    bidder = make_bidder(FakeRoom())

    bidder._handle("arm", ("Pascal Siakam", 60))
    state = bidder.state()

    assert state["armed"] is True
    assert state["player"] == "Pascal Siakam" and state["maximum"] == 60

    bidder._handle("disarm", "by hand")
    with pytest.raises(ValueError, match="is on the block, not"):
        bidder._handle("arm", ("Alperen Sengun", 60))


# ---------------------------------------------------------------------------
# the loop that acts on all of it
# ---------------------------------------------------------------------------


def test_armed_and_outbid_it_clicks_the_offer_button_once_and_only_once() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)
    bidder._handle("arm", ("Pascal Siakam", 70))

    bidder._tick()
    bidder._tick()
    bidder._tick()

    assert room.clicks == [SELECTORS["offer_button"][0]], "three reads of a frozen page, one bid"
    assert any("offered $53" in line["text"] for line in bidder.state()["log"])


def test_it_answers_each_time_the_room_raises_and_stops_at_the_maximum() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)
    clock = bidder._clock
    assert isinstance(clock, Clock)
    bidder._handle("arm", ("Pascal Siakam", 56))

    for offer, button in ((52, "Offer $53"), (54, "Offer $55"), (56, "Offer $57")):
        room.set(offer=offer, button=button)
        clock.tick()
        bidder._tick()

    assert len(room.clicks) == 2, "$53 and $55; $57 is over the $56 we were given"
    assert "over the $56 maximum" in (bidder.state()["waiting"] or "")
    assert bidder.state()["armed"] is True, "still watching, so it can still say how it ended"


def test_winning_the_player_stops_it_and_says_so() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)
    bidder._handle("arm", ("Pascal Siakam", 70))

    room.set(history=("$53 Through The Wire",), offer=53, button="")
    bidder._tick()
    room.set(player="Alperen Sengun", offer=1, button="Offer $2", history=())
    bidder._tick()

    state = bidder.state()
    assert state["armed"] is False
    assert "we won Pascal Siakam" in (state["stopped_because"] or "")


def test_the_player_changing_under_it_stops_it_and_says_so() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)
    bidder._handle("arm", ("Pascal Siakam", 70))

    room.set(player="Alperen Sengun")
    bidder._tick()

    state = bidder.state()
    assert state["armed"] is False and state["log"][-1]["text"].startswith("disarmed")
    assert "moved from Pascal Siakam to Alperen Sengun" in (state["stopped_because"] or "")


def test_three_reads_in_a_row_that_cannot_find_the_room_disarm_it() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)
    bidder._handle("arm", ("Pascal Siakam", 70))
    room.blank = True

    for _ in range(MAX_MISSES - 1):
        bidder._tick()
    assert bidder.state()["armed"] is True, "a single bad read is a blink, not a failure"

    bidder._tick()

    state = bidder.state()
    assert state["armed"] is False
    assert "reads in a row failed" in (state["stopped_because"] or "")
    assert state["error"] and "markup has moved" in state["error"]


def test_an_exception_reading_the_room_is_caught_counted_and_reported() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)
    bidder._handle("arm", ("Pascal Siakam", 70))
    room.raises = TimeoutError("the page went away")

    for _ in range(MAX_MISSES):
        bidder._tick()

    state = bidder.state()
    assert state["armed"] is False
    assert "TimeoutError" in (state["error"] or "")
    assert room.clicks == []


def test_a_bidder_at_a_room_not_yet_open_says_so_once_and_waits() -> None:
    """One line, `room_open` false, and polling; the read that finds the
    room ends it, with nothing to do at the window."""
    room = FakeRoom()
    bidder = make_bidder(room)
    room.not_open = True

    for _ in range(6):
        bidder._tick()

    state = bidder.state()
    assert state["room_open"] is False
    assert state["error"] == "the draft has not opened yet"
    assert sum("not opened" in line["text"] for line in state["log"]) == 1, "said once"
    assert state["signed_in"] is None, "a page with no room in it proves nothing about the session"

    room.not_open = False
    bidder._tick()

    state = bidder.state()
    assert state["room_open"] is True and state["error"] is None
    assert state["room"]["player"] == "Pascal Siakam"


def test_a_signed_out_bidder_says_so_once_and_waits_for_the_room() -> None:
    """Not a miss every half second: one line in the log, `signed_in` false
    in the state, and then polling until the person at the window has
    signed in -- at which point the room reads, the state flips back on its
    own, and the session is kept for next time."""
    room = FakeRoom()
    bidder = make_bidder(room)
    room.signed_out = True

    for _ in range(6):
        bidder._tick()

    state = bidder.state()
    assert state["signed_in"] is False
    assert state["error"] == "sign in, in the ESPN window"
    assert sum("sign-in" in line["text"] for line in state["log"]) == 1, "said once"
    assert room.remembered == 0, "nothing to keep from a sign-in page"

    room.signed_out = False
    bidder._tick()

    state = bidder.state()
    assert state["signed_in"] is True and state["error"] is None
    assert state["room"]["player"] == "Pascal Siakam"
    assert state["last_read"], "and the read is stamped"
    assert room.remembered == 1, "the session that just read the room is the one to keep"


def test_a_sign_in_mid_draft_drops_the_maximum_and_says_why() -> None:
    """There is no room to hold a maximum in, so it does not survive."""
    room = FakeRoom()
    bidder = make_bidder(room)
    bidder._handle("arm", ("Pascal Siakam", 70))
    room.signed_out = True

    bidder._tick()

    state = bidder.state()
    assert state["armed"] is False
    assert "sign-in" in (state["stopped_because"] or "")
    assert room.clicks == []


def test_a_dry_run_does_everything_except_click() -> None:
    room = FakeRoom()
    bidder = make_bidder(room, dry_run=True)
    bidder._handle("arm", ("Pascal Siakam", 70))

    bidder._tick()

    assert room.clicks == [], "the one thing a rehearsal must never do"
    assert any("would have offered $53" in line["text"] for line in bidder.state()["log"])
    assert bidder.state()["dry_run"] is True


def test_a_dry_run_still_rate_limits_itself() -> None:
    """Otherwise the rehearsal proves nothing about the thing it rehearses."""
    room = FakeRoom()
    bidder = make_bidder(room, dry_run=True)
    bidder._handle("arm", ("Pascal Siakam", 70))

    bidder._tick()
    bidder._tick()

    assert sum("would have offered" in line["text"] for line in bidder.state()["log"]) == 1


# ---------------------------------------------------------------------------
# a bid by hand
# ---------------------------------------------------------------------------


def test_one_tap_clicks_the_offer_button_and_reports_both_sides_of_it() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)

    result = bidder._handle("once", None)

    assert room.clicks == [SELECTORS["offer_button"][0]]
    assert result["amount"] == 53
    assert result["before"]["current_offer"] == 52 and "after" in result


def test_one_tap_is_refused_while_we_are_already_the_high_bidder() -> None:
    """The rule is the bidder's, not the screen's: the route is not the only
    thing that can ask for a bid."""
    bidder = make_bidder(FakeRoom(history=("$52 Through The Wire",)))

    with pytest.raises(ValueError, match="already hold the high bid"):
        bidder._handle("once", None)


def test_a_typed_amount_goes_through_the_custom_box() -> None:
    room = FakeRoom()
    bidder = make_bidder(room)

    bidder._handle("exact", 60)

    assert room.typed == [(SELECTORS["custom_input"][0], "60")]
    assert room.clicks == [SELECTORS["custom_button"][0]]


def test_a_typed_amount_is_refused_below_the_offer_over_the_legal_max_and_over_two_digits() -> None:
    bidder = make_bidder(FakeRoom())

    with pytest.raises(ValueError, match="does not beat"):
        bidder._handle("exact", 40)
    with pytest.raises(ValueError, match=r"\$80 ESPN will let us offer"):
        bidder._handle("exact", 85)
    with pytest.raises(ValueError, match=r"between \$1 and \$99"):
        bidder._handle("exact", 120)


def test_a_typed_amount_is_refused_over_the_plans_cap() -> None:
    bidder = make_bidder(FakeRoom(), cap=55)

    with pytest.raises(ValueError, match=r"over the plan's \$55 cap"):
        bidder._handle("exact", 60)


# ---------------------------------------------------------------------------
# the selectors, against a real auction room
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def snapshot() -> StaticPage:
    return StaticPage.from_file(SNAPSHOT)


def test_every_selector_resolves_against_the_saved_auction_room(snapshot: StaticPage) -> None:
    """The one test that fails the day ESPN renames a class. It is named in
    docs/bidding.md as the thing to run before the draft."""
    missed = [
        key for key, options in SELECTORS.items() if not any(snapshot.texts(o) for o in options)
    ]

    assert missed == [], f"these hooks into ESPN's room found nothing: {missed}"


def test_the_saved_room_reads_as_the_room_it_was(snapshot: StaticPage) -> None:
    found = read_room(snapshot)

    assert found.player == "Pascal Siakam"
    assert found.player_team == "IND" and found.player_pos == "PF"
    assert found.espn_value == 49, "PRE-DRAFT VAL: $49"
    assert found.current_offer == 52, "CURRENT OFFER: $52"
    assert found.legal_max == 80, "MANUAL OFFER (MAX $80)"
    assert found.next_increment == 53, "the button's own label, OFFER $53"
    assert found.offer_ready is True
    assert found.our_team == "Through The Wire" and found.our_cash == 91
    assert found.our_bid is None, "$null, because we were not in on him"
    assert found.high_bidder == "Joe Mazzulla's Search History"
    assert found.leading is False
    assert found.clock == "00:03" and found.pick_label == "PK 23 OF 208"
    assert found.history[:2] == (
        "$52 Joe Mazzulla's Search History",
        "$51 Ben's Need Some VC",
    ), "newest first"
    assert found.missing == ()


def test_the_player_selectors_find_the_man_on_the_block_and_not_the_other_seventy(
    snapshot: StaticPage,
) -> None:
    """The room's player table uses the same class names as the card. An
    unscoped `.playerinfo__playername` matches seventy-four elements, and
    would have us bidding on whoever happens to head the list."""
    loose = snapshot.texts(".playerinfo__playername")
    scoped = snapshot.texts(SELECTORS["player_name"][0])

    assert len(loose) > 70, "the list of everyone still for sale"
    assert scoped == ["Pascal Siakam"], "the card, and only the card"


def test_the_two_offer_buttons_are_told_apart(snapshot: StaticPage) -> None:
    """One bids the next increment, the other bids what is typed beside it.
    They share a class, so only the form around them separates them."""
    both = snapshot.texts("button.bid-player__button")
    default = snapshot.texts(SELECTORS["offer_button"][0])
    custom = snapshot.texts(SELECTORS["custom_button"][0])

    assert len(both) == 2
    assert default == ["Offer $53"] and custom == ["Offer"]
    assert snapshot.enabled(SELECTORS["custom_button"][0]) is False, "disabled with an empty box"


def test_our_own_row_in_the_pick_train_is_ours_alone(snapshot: StaticPage) -> None:
    every_team = snapshot.texts(".auction-pick-component .team-name")

    assert len(every_team) > 10, "every team in the league rides the pick train"
    assert snapshot.texts(SELECTORS["our_team"][0]) == ["10. Through The Wire"]
    assert snapshot.texts(SELECTORS["our_cash"][0]) == ["$91"]


def test_the_custom_bid_box_is_two_digits_wide(snapshot: StaticPage) -> None:
    """Which is why a typed offer is capped at $99 and the one-tap button is
    the only way past it. Not a rule of ours -- a rule of ESPN's form."""
    boxes = snapshot._select(SELECTORS["custom_input"][0])

    assert boxes and boxes[0].attrs.get("maxlength") == "2"
