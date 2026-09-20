# Bidding from the draft room

**Written:** 2026-09-20, against a live ESPN practice auction of the real
league. **Status:** the parsing is proven against that auction and against a
saved copy of its markup; the bidding path has placed real bids in a
practice room and has never been run in a draft that counted.

The draft screen already says what a player is worth to us. This puts two
controls next to that number:

- **Bid $N** — one tap, which clicks ESPN's own offer button at ESPN's own
  next increment. Nothing more than pressing the button faster than a hand
  can find it.
- **Hold to $X** — a maximum, held for the man on the block. While it is
  armed, every time the page shows we have been outbid and the next
  increment is still at or below $X, the tool offers once, then goes back to
  watching. It never bids above $X and it never bids for anybody else.

And one control that matters more than either: a **STOP** button, which is
pinned to the corner of the screen the whole time anything is armed.

## The honest caveats

Read these before using it on a draft that counts.

1. **It automates clicks on ESPN's website.** ESPN does not offer an API for
   this. The tool drives a real browser window with a real signed-in
   session. If that is against the spirit of the league or the letter of
   ESPN's terms as you read them, do not use it.
2. **Every click is real money in a real auction.** There is no undo. A
   maximum typed with an extra digit is a maximum the tool will honour up
   to the point where ESPN or the plan's cap stops it.
3. **The human is expected to watch.** The browser window it drives is
   headed by default, on purpose: it is there to be watched and to be taken
   over with a mouse. This is a fast hand, not an agent. It holds one number
   that a person chose seconds earlier, on one player, and it stops at the
   first thing it does not understand.
4. **It is only as current as ESPN's markup.** See "when ESPN changes"
   below. The failure is loud, but it is a failure.

## Starting it

Once, ever:

```bash
pip install -e '.[live]' && playwright install chromium
```

Once per draft session — this opens a browser window, you sign in by hand,
and it saves the signed-in session to `~/.fcp-core/espn-state.json`. Leave
the window open:

```bash
.venv/bin/python scripts/espn_login.py --url "<the ESPN draft room URL>"
```

The `espn_s2` cookie in `.env` does **not** authenticate a browser any more;
this file is the only way in. Nothing in the bidding path reads `.env`.

Then start the service with `--bid` (which needs `--page`):

```bash
.venv/bin/python scripts/draft_service.py --season 2027 --me "Through The Wire" \
  --bbm data/bbm/BBM_Projections_2027_total.xls \
  --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls \
  --page "<the ESPN draft room URL>" --bid
```

Add `--no-bid` to rehearse: everything happens — reading, arming, deciding,
logging, the live line, the whole screen — except the final click. Do this
first, in a mock. `--bid-headless` hides the window, which is for tests.

**Without `--bid` nothing changes.** No browser opens, the three routes are
not defined, `/api/state` carries no `bid` key, and the screen draws none of
it. The rehearsal (`--rehearse`) and the typed-pick path work exactly as
before.

## The safety rules

All of these live in `app/draft/bidder.py`, in `decide` and `check_maximum`,
and are enforced on the bidder's own thread. The screen enforces some of
them too, so the reason arrives before the round trip — but the screen is
courtesy and the bidder is the rule. A bid asked for by any other route gets
the same refusals.

| Rule | What it means |
|---|---|
| **One player** | A maximum is armed for whoever is on the block at that moment. If the name on the block changes, it disarms and says so. It cannot be armed for a player who is not up. |
| **Never above the maximum** | The number you typed. |
| **Never above the plan's cap** | `Room.allocation.cap` — the most the finishable roster will give one player. Checked when arming *and* on every bid, because the cap moves as the room does. |
| **Never above ESPN's own maximum** | The page's `MANUAL OFFER (MAX $n)`, re-read every tick. Our money and our open places, as ESPN counts them. |
| **Never when we are already winning** | Read two independent ways: the newest row of the bid history naming our team, and our own live bid standing at the current offer. Either is enough. |
| **Never when the page cannot say who is up** | A read with no player card on it bids nothing. |
| **At most one bid a second** | A hard floor, whatever else the page says. |
| **At most one bid per change in the offer** | The increment only rises when the offer does, so an increment we have already bid means the page has not moved. A frozen page produces exactly one bid and then nothing. |
| **Stops on** | winning the player, the player changing, three reads in a row that cannot find the room, any exception, or STOP. |
| **Every stop is reported** | The reason appears on the live line, in the log, and in `/api/state`. It never stops quietly. |

The screen adds one thing of its own: a maximum **above the ceiling** is
allowed, but the button first changes to say how far above, and wants a
second tap. A maximum above the plan's cap, above ESPN's maximum, or above
the money we have left is refused outright with the reason. Deciding to
overpay is a decision; doing it because a box was already full of the wrong
number is an accident.

## What is on the screen

On the block card, below the strip, when `--bid` is on:

- **Bid $N** — greyed out while we lead or while there is no button to press.
- **Hold to $X** — the box opens at what he is worth to us (the ceiling, or
  the estimate standing in for it) once per player, and is then left alone;
  a number you are typing is never overwritten by the stream.
- **STOP** — appears the moment anything is armed and pins itself to the
  bottom-right of the window (the full width of the foot, on a phone) so it
  is reachable without scrolling.
- **The live line** — armed or not, our maximum, the current offer, who
  holds it, what it is waiting for, how it stopped, and the last thing it
  did with the time.
- **The log** — every bid the tool placed and every reason it stopped, newest
  first. Bids are in the accent colour.

## When ESPN changes its markup

Every hook into ESPN's page is in one dictionary, `SELECTORS`, at the top of
`app/draft/bidder.py`, and nowhere else. Each entry is a tuple tried in
order, most specific first.

The failure is loud, by design:

- `read_room` raises `SelectorError` naming the selectors it tried.
- The message goes to the live line and the log on the screen.
- Three in a row disarm anything armed, with that message as the reason.

To fix it on the night, in order:

1. **Run the parse tests first** — they will tell you exactly which hook
   died, without a draft running:

   ```bash
   .venv/bin/pytest tests/test_bidder.py -o addopts="" -q
   ```

   They read `tests/fixtures/espn_auction_room.html`, a saved copy of a real
   auction room. `test_every_selector_resolves_against_the_saved_auction_room`
   is the canary.

2. **If the saved copy is stale**, take a new one from the live room
   (`document.documentElement.outerHTML` in the console), strip the member
   id, and replace the fixture. The tests will then say what actually moved.

3. **Edit `SELECTORS`**, adding the new selector as the *first* option and
   leaving the old one after it. Nothing else in the file needs touching:
   `read_room` is the only reader and every rule is downstream of it.

4. **If it cannot be fixed in the time you have**, run without `--bid` and
   bid by hand in ESPN. Everything else about the room — the ceiling, the
   plan, the board, the typed-pick path — is unaffected and is what the
   runbook's fallback has always been.

Two things about ESPN's form worth knowing:

- The custom bid box is `maxlength="2"`, so a typed offer cannot exceed $99.
  Above that the one-tap increment button is the only way up.
- The one-tap button's label goes **empty** while we hold the high bid. That
  is information, not a missing selector, and the reader treats it as such —
  which is why the selector for it must not fall back to a looser match that
  would find the *custom* button and read its "OFFER" as an increment.

## The routes

Only defined with `--bid`; they are not in `/docs` otherwise.

```
POST /api/bid/once              offer the next increment, once
POST /api/bid/once {"amount":n} a typed offer, through the custom box
POST /api/bid/arm  {"max":n}    hold a maximum for the man on the block
POST /api/bid/stop              disarm
GET  /api/state                 carries `bid` with the room, the log and the reason
```

A bid the rules refuse is a **409** with the rule as the message. A bidder
that is not running is a **503**.

## What is not tested, and cannot be

There is no test against live ESPN, and there will not be: a test that
places a real bid in a real auction is not a test. What is tested is every
selector against a real saved room, and every rule against a fake room built
from the same markup and read by the same code. The gap between those and
the night is a browser, and the answer to that gap is the window you can
watch and the button that stops it.
