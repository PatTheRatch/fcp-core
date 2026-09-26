# Draft night: the runbook

*The room is Box Out's draft screen (`app/brand.py`); the league it drafts is
Full Court Press, which is a different thing wearing the same old name.*

**Written:** 2026-09-18, for the 2027 auction on Saturday 10 October, 6:00 pm UTC.
**Revised:** 2026-09-20, when the room learned to connect to ESPN from the
screen and `Draft Room.command` replaced the command line.
**Status:** the room is built and rehearsed on the 2026 replay; the live page
reader has been used once, on last year's mock; Connect has been driven
against a fake ESPN window and not yet against a real mock. Follow this top
to bottom.

The one rule: the tool generates ideas and the board holds the room's facts.
You decide every bid. If anything on the screen looks wrong, the price you
believe beats the price it shows.

## The week before

1. **Check the BBM settings, then pull.** In Basketball Monster, the auction
   settings have to be the league's: 16 teams, $200, 13 roster places, the
   playoff Start/End Week set (clearing it inflates every player's games by
   ten and drops Leag$). Then:

   ```bash
   .venv/bin/python scripts/bbm_pull.py --season 2027 --store
   ```

   The pull refuses an export whose median games moved two or more, or that
   lacks Leag$; if it refuses, fix the settings and pull again rather than
   forcing it.

2. **Make the plan on the site**: the rail's **Draft plan**, under your team
   (`/l/{league}/2027/team/{team}/draft/plan`, docs/draft_plan.md). After a
   fresh `--store` pull press **Build again**; the plan is worked out on the
   new capture in about two minutes, and the source line says which capture
   and how old. Set your own going prices and ceilings where you disagree,
   tag your targets, must-haves, let-gos, nominations and IR pick, and keep
   notes: that is what the room shows you on the night (step "The hour
   before"). It carries BBM's numbers and is served only to you.

   The script is the offline fallback, and writes the same file it always
   has:

   ```bash
   .venv/bin/python scripts/draft_plan.py --season 2027 --me "Through The Wire" --bbm data/bbm/BBM_Projections_2027_total.xls --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls
   ```

   The page is `logs/draft-plan-2027.html`. It carries BBM's numbers; do not
   share it.

3. **Rehearse in the room** at least once, at the real pace:

   ```bash
   .venv/bin/python scripts/draft_service.py --season 2027 --me "Through The Wire" --bbm data/bbm/BBM_Projections_2027_total.xls --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls --rehearse 2026 --seconds 20
   ```

   Open http://127.0.0.1:8765. A rehearsal logs to
   `logs/rehearsal-2027-<time>.jsonl` and never touches the real log. Practise
   the three things that matter under a clock: the pick entry flow (`/` to
   type, Enter to save), "Put on block" when the reader is behind, and "Undo
   last pick" when a price was typed wrong.

4. **One ESPN mock draft, connected from the screen.** The ESPN window
   needs Playwright once: `pip install -e '.[live]' && playwright install
   chromium`. Check `.env` has `ESPN_LEAGUE_ID`, `ESPN_SWID` and
   `FCP_TRACKED_TEAM_ID` (the last was missing on 2026-09-20; without it
   the launcher refuses to start and says so). Then double-click
   `scripts/Draft Room.command`. The screen opens; the URL box in the
   masthead holds the league's own room. For a mock, paste the mock's
   draft-room URL over it. Press **Connect**. A Chromium window opens on
   the room; if it shows ESPN's sign-in page, sign in there -- the pill
   reads *sign in, in the ESPN window* until you have -- and the window
   remembers it for next time. The pill goes green: *Auction room · read
   18:02:11*. That window is the reader and the bidder both; the board
   fills from it. If ESPN has changed the page and the pill goes red, the
   night's plan is to type picks (step 3), which is why the rehearsal
   practises it.

5. **If you mean to bid from the room, rehearse that too.** Run the mock
   with `--no-bid` (`scripts/Draft Room.command` passes flags through, or
   `scripts/draft_night.py --no-bid`), which does everything except the
   final click. Read **docs/bidding.md** first, in full: it automates
   clicks on ESPN's site, every bid is real money, and you are expected to
   watch the window it drives. Until Connect is pressed none of it exists.

6. **The day before, press Connect against the real room.** Open
   `scripts/Draft Room.command`, leave the URL box as it is, press
   **Connect**. The pill should settle, in amber, on *the draft has not
   opened yet · ESPN says "Loading your draft"*: that one line proves the
   address, the window and the sign-in, which is everything that can go
   stale before the night (checked this way on 2026-09-20; the first few
   reads say *reading the room…* while the page paints). *sign in, in the
   ESPN window* means do that, now, not on the night. Then Disconnect.

7. **Back up.** `bash scripts/backup_db.sh` on the VPS, and copy
   `data/bbm/` somewhere safe. The launcher reads the two newest BBM files
   from `data/bbm/` at start, so keep them there.

8. **Point the room at the plan you made on the site.** The marks on the
   draft plan page (your going prices, ceilings, tags, must men) live in the
   VPS's database; the room on this laptop reads whatever database its
   `DATABASE_URL` names, which is the laptop's own. So on the night the room
   reads the VPS's database through an SSH tunnel (the VPS's Postgres
   listens only on its own localhost, port 5433; nothing is opened to the
   internet). In one terminal, leave this running:

   ```bash
   ssh -N -L 5434:localhost:5433 aisha@aisha-vps
   ```

   In another, start the room with the VPS's connection string, host and
   port swapped for the tunnel's end: take `DATABASE_URL` from
   `/opt/fcp-core/.env` on the VPS, replace `localhost:5433` with
   `127.0.0.1:5434`, and pass it for that command only, never into this
   repo's `.env`:

   ```bash
   DATABASE_URL='postgresql+psycopg://…@127.0.0.1:5434/fcp' .venv/bin/python scripts/draft_night.py
   ```

   The terminal then says how many men carry your figure or tag from the
   plan page; if it says none and you marked some, the room is reading the
   wrong database. Checked 2026-09-26: the tunnel reaches the VPS's `fcp`,
   and the plan tables read through it. The tunnel is read-only in
   practice, since the room writes its picks to `logs/`, not the database;
   `--no-plan-marks` turns the reading off. Close the tunnel afterwards
   (Ctrl-C in its terminal).

## The hour before

- Close every other tab on the draft account. Charge the laptop. Have the
  runbook and the plan page open on a second screen, not in the draft browser.
- If `logs/draft-2027.jsonl` exists from a mock, move it aside first: the
  service replays whatever it finds and never deletes.
- **Double-click `scripts/Draft Room.command`.** A terminal window opens
  and stays; the screen opens in the browser by itself. Check three things
  on it: the source line under the title says Basketball Monster with the
  latest export; the pick counter reads 0 / 208; the plan and the ladder
  are filled. The terminal says how many men carry your own figure or tag
  from the plan page; a man you marked shows "Yours · from your plan" beside
  our ceiling on the block, and your tag in the desk. Three worker processes compute ceilings in the background;
  the first few cards say "ceiling pending" for a moment, which is normal.
- **Press Connect.** The URL box already holds the league's room. The pill
  goes *opening the ESPN window…*, then either *sign in, in the ESPN
  window* -- do that, in the Chromium window that opened, and wait -- or
  straight to green, *Auction room · read HH:MM:SS*. Leave that window
  open and visible: it is the reader, and it is where a bid is placed.
- Amber *the draft has not opened yet* means you are early: leave it, the
  pill goes green on its own when ESPN opens the room.
- If the pill stays red, read what it says. *could not open the ESPN
  window* means Playwright or Chromium is missing (step 4). A read failure
  after sign-in means the markup has moved: the table below. Either way
  the room works without it -- picks can be typed -- and Connect can be
  pressed again at any time after Disconnect.

The terminal way, if the launcher will not start or the flags need
changing, is the same service by hand:

```bash
.venv/bin/python scripts/draft_night.py --season 2027 --me "Through The Wire" --bbm data/bbm/BBM_Projections_2027_total.xls --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls
```

or `scripts/draft_service.py` with the same flags, which opens no browser
tab. Adding `--page "<URL>" --bid` connects at start instead of from the
screen; `--page` alone reads the room the older way, on the cookies in
`.env`, which expire silently and is why the screen's Connect exists.

## During the draft

- **The block strip is the whole decision.** Our price is the board sized to
  the room, "plan pays" is what the best finishable roster wants him at, and
  the ceiling is the most he is worth to us specifically. Bid to our price,
  stop at the plan's, never above the ceiling. The nine-category strip and
  "what is left on the board" say what the bid is buying and whether it is
  being cornered.
- **The reader lags a few seconds** behind ESPN by design (`--interval 2`).
  A pick it has not seen yet is not lost; wait one refresh before typing it.
  Picks the money implies but the log has not shown are raised in the feed
  pill and not applied unless the service was started with `--trust-money`;
  leave that off unless the reader is visibly behind for a whole round.
- **If the reader dies or ESPN changes under it:** the room keeps running.
  Type each pick as it happens: `/`, the player, the team, the price, Enter.
  "Put on block" keeps the strip on the man being bid on. If the pill says
  *sign in, in the ESPN window*, ESPN's session lapsed mid-draft: sign in
  again in that window and the pill goes green on its own; a maximum that
  was being held is dropped, and says so.
- **If the ESPN window dies:** Disconnect, then Connect. The window opens
  again on the same URL, signed in, and the board picks up from what the
  page shows.
- **If the service itself dies:** double-click `Draft Room.command` again.
  It replays `logs/draft-2027.jsonl` and picks up where it was; press
  Connect again, and the reader re-reads the board and skips what it
  already applied. Nothing is lost that was saved, and a pick that was
  mid-entry is the only thing to re-type.
- **If a price was entered wrong:** "Undo last pick", then re-enter. Undo is
  logged too, so a restart replays the correction.
- **If you are bidding from the room** (connected): type the maximum once,
  early in the nomination, and let it hold. STOP is pinned to the corner the
  whole time it is armed and disarms instantly. It stops by itself when you
  win him, when the block moves on, or when it cannot read the page three
  times running, and it says which. It never bids above your maximum, above
  the plan's cap, or above what ESPN will accept. docs/bidding.md.
- **Our column is the accent column.** Its open places carry the plan's
  prices in italic: that is the money still allotted per place, and it moves
  as the room does. "Field max bid" is the most anyone else can pay; when it
  falls below our price for a player, he is ours for a dollar more than the
  next bid.

## After

- The log is the record. Copy `logs/draft-2027.jsonl` and the plan JSON
  somewhere safe. The nightly ingest will bring the draft into the database
  from ESPN by the next morning; if it does not, `scripts/ingest.py` for
  2027 does it by hand.
- Stop the service. The pages and the digest take over from here
  (docs/in_season_pages.md, docs/pickups.md).

## What can go wrong, and the answer

| Symptom | Answer |
|---|---|
| "No projected lines stored for 2027" at start | ESPN has not published 2027 projections yet; the BBM room does not need them, so this only appears without `--bbm`. Pass the BBM files. |
| The pull refuses the export | The BBM settings changed. Restore the playoff weeks and 16 teams, pull again. |
| `Draft Room.command` refuses to start | It says which: no BBM export in `data/bbm/` (pull one, step 1), no `FCP_TRACKED_TEAM_ID` in `.env` (set it, or run `draft_night.py --me "Through The Wire"`), or the database is not up (Docker Desktop stopped while the laptop slept, on 2026-09-20: open Docker, then `docker compose up -d` in the repo, then try again). The window stays open so the message can be read. |
| The URL box is empty | `.env` is missing one of `ESPN_LEAGUE_ID`, `ESPN_SWID`, `FCP_TRACKED_TEAM_ID`. Paste the room's URL from ESPN's address bar; it works the same. |
| Pill says *could not open the ESPN window* | Playwright or Chromium is not installed (step 4), or `scripts/espn_login.py` is running and has the profile open -- close it; the two cannot share the profile. Then Disconnect, Connect. |
| Pill stays on *sign in, in the ESPN window* after signing in | Wait one read (half a second). If it stays, the window is on ESPN's home rather than the room: Disconnect, Connect. |
| Feed pill says the page is not a draft page | The URL is not the draft room, or the room has not rendered yet (it waits 30 s for the ticker). Check the URL, then Disconnect and Connect (or restart with `--page`). |
| A player the reader saw is not on the board | He may be on the block, not sold. Wait a refresh; then "Put on block" or type him. |
| Cards say ceiling pending for minutes | The workers are behind; the price and the plan are still right. Bid on those. |
| The service will not start: the log has picks in it | A mock's log is in the way. Move `logs/draft-2027.jsonl` aside. |
| The bid line says it cannot find the room | ESPN moved its markup. Run `pytest tests/test_bidder.py` to see which hook died, then docs/bidding.md, "when ESPN changes". Until it is fixed, Disconnect and bid by hand in ESPN's own tab. |
| It disarmed itself mid-nomination | It says why on the live line, always. The usual reasons are the block moving on and three unreadable reads. Re-arm, or bid by hand. |
