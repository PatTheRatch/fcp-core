# Draft night: the runbook

**Written:** 2026-09-18, for the 2027 auction on Saturday 10 October, 6:00 pm UTC.
**Status:** the room is built and rehearsed on the 2026 replay; the live page
reader has been used once, on last year's mock. Follow this top to bottom.

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

2. **Re-run the plan** on the fresh export and read it once:

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

4. **One ESPN mock draft with the reader running.** The page reader needs
   Playwright once: `pip install -e '.[live]' && playwright install chromium`.
   Then, with the mock draft open in ESPN, start the service with `--page`
   and the mock's draft-room URL (it carries `leagueId`, `seasonId` and
   `teamId`), and watch the feed pill in the masthead. The reader logs in
   with the same cookies the ingest uses, which is a second session on the
   account; last year a second tab caused the first no trouble. If ESPN has
   changed the page and the feed never fills, the night's plan is to type
   picks (step 3), which is why the rehearsal practises it.

5. **Back up.** `bash scripts/backup_db.sh` on the VPS, and copy
   `data/bbm/` somewhere safe. The room reads the two BBM files at start, so
   keep them where the command below expects.

## The hour before

- Close every other tab on the draft account. Charge the laptop. Have the
  runbook and the plan page open on a second screen, not in the draft browser.
- Start the room, live, with the reader:

  ```bash
  .venv/bin/python scripts/draft_service.py --season 2027 --me "Through The Wire" --bbm data/bbm/BBM_Projections_2027_total.xls --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls --page "<the ESPN draft room URL>"
  ```

  Without `--page`, the same command opens the room for typed picks. Either
  way the pick log is `logs/draft-2027.jsonl`. If that file already exists
  from a mock, move it aside first: the service replays whatever it finds
  and never deletes.

- Open http://127.0.0.1:8765 and check three things: the source line under
  the title says Basketball Monster with today's export; the pick counter
  reads 0 / 208; the plan and the ladder are filled. Three worker processes
  compute ceilings in the background; the first few cards say "ceiling
  pending" for a moment, which is normal.

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
  "Put on block" keeps the strip on the man being bid on.
- **If the service itself dies:** start the same command again. It replays
  `logs/draft-2027.jsonl` and picks up where it was; the reader re-reads the
  whole board and skips what it already applied. Nothing is lost that was
  saved, and a pick that was mid-entry is the only thing to re-type.
- **If a price was entered wrong:** "Undo last pick", then re-enter. Undo is
  logged too, so a restart replays the correction.
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
| Feed pill says the page is not a draft page | The URL is not the draft room, or the room has not rendered yet (it waits 30 s for the ticker). Check the URL, then restart with `--page`. |
| A player the reader saw is not on the board | He may be on the block, not sold. Wait a refresh; then "Put on block" or type him. |
| Cards say ceiling pending for minutes | The workers are behind; the price and the plan are still right. Bid on those. |
| The service will not start: the log has picks in it | A mock's log is in the way. Move `logs/draft-2027.jsonl` aside. |
