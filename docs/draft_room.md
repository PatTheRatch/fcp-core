# The draft screen

**Written:** 2026-09-18, when the screen was rebuilt to the approved design.

One page, served by the draft service (`app/draft/service.py`, `GET /`) from
`app/draft/static/draft.html`. Plain HTML, CSS and JavaScript: no framework and
no build step, because on the night the page has to come up from a file on
disk on the manager's own machine. It is read once per request, so an edit
shows on a refresh.

## What is on it, top to bottom

**The masthead.** The pick counter, inflation, the field's maximum bid, who
nominates next, the feed's status, the rehearsal controls when one is running,
and the light/dark switch. Under the title: how many teams, the budget, the
roster size and **where the numbers came from**, which is `source_note` from
the state (`app/projections/sources.py`, `describe`).

**The block.** Whoever is up, as big as the decision is: his name, position and
eligibility, the games he is projected for, and then the money.

*Worth to us* is the primary figure, large and in the accent colour: our
ceiling, which is the number a manager bids by. Under it, why — "the most we
should pay for him", or "the most the plan lets one player take" when the cap
is what set it. A ceiling of none reads *no bid*. Until the exact ceiling
lands it carries an **estimate** instead, in the warning colour and labelled
as one (see *Ceilings on the clock* below); it is never blank.

Beside it, at a reading size and as context rather than instruction: *our
price* (the board's valuation), *expected to go* (what the room will probably
pay), *plan pays* and *went for* (which reads *current offer* while he is
still being bid on).

*Plan pays* is his own planned price when he is one of the plan's thirteen.
When he is not — which is nearly every nomination — it is the open place his
expected going price falls into, read as "a $40 place": the cheapest open
place that covers him, or the largest when none does, which is the rule
`Allocation.open_places` uses to retire a place when we buy.

Under the money, the nine-category strip, each cell shaded by where he sits
among everyone on this board, then the verdict from our ceiling, the bargain
warning when there is one, and — only when the room was loaded on Basketball
Monster — its facts and its analyst's note.

**Entering a pick** is its own block, full width, directly under the block.
It is the draft-night fallback: when the page reader stops working, this is
how picks get in, so it is the thing that has to be found first and cannot be
allowed to fail quietly. The name box has autocomplete (`/` jumps to it,
`Enter` moves to the price and saves), then the team to charge it to, then
Save, put on block, undo and clear the block — all with 52px tap targets at
phone width.

Every answer lands in a panel under the buttons and **stays there until the
next action**: what was saved (player, team, price, pick number) in green, or
why it was not in red. A pick the rules refuse (409) and a name that matches
nobody or several (422, with its alternatives as buttons to tap) both land
there. Save disables itself and reads "Saving…" while the request is in
flight. Nothing about a saved pick is left to a toast.

**The desk** is the search of everyone still for sale, with what he will go
for, our ceiling and BBM's value when there is one.

**The board.** Teams across, roster places down, every pick with its price, the
most expensive at the top of each column. Our column is in the accent colour
and our open places carry, in italic, what the plan says each should cost. The
team to nominate next is underlined. The foot of each column: money left,
places open, maximum bid, and the average left per place. Hovering, tapping or
tabbing to any cell raises that player's card with his own strip.

A tap pins the card. It closes on a tap anywhere outside, on its own 44px
close control, on Escape, and on any scroll — the window's, and the board's
own sideways scroller, which is the one a thumb moves and which the window's
scroll event never hears about. It is also kept clear of the pick entry: a
card flips above its cell when there is no room below, and a card over Save
covers the one control that has to work.

**What is left on the board.** A *source* is a player in the top quarter of the
draftable pool for a category, where the draftable pool is the best N by a
plain z-composite and N is how many players the room drafts. Impact, not the
raw figure: percentages count as made shots above the pool's rate on the same
attempts, turnovers count upside down. The panel shows how many sources are
left against how many picks are left, so a category being cornered and one
going cheap are both visible.

**Where every team stands.** Each roster's projected per-game totals, the two
percentages weighted by attempts, shaded by rank down the column.

**The roster we can still finish**, from `GET /api/plan`; **the ladder** of what
each open place should cost; and **the tape**, the last fourteen picks.

The page redraws on every version: it holds `/api/events` open (server-sent
events) and the service pushes the whole state each time anything moves.
Replay warnings from the pick log are printed in the footnote.

## When a phone takes the stream away

iOS Safari drops an EventSource when the tab is backgrounded or the screen
locks, and it does not always fire an error when it does. The page then looks
live and is in fact frozen at whatever it last drew, which on draft night is
the worst way to be wrong. Three answers:

* the masthead carries a **reconnecting…** pill whenever the stream is not
  open, so a stale page is never a silent one;
* while it is down the page **polls `/api/state`** every three seconds, which
  is slower than the stream and is not nothing;
* coming back to the tab (`visibilitychange`), back through the history
  (`pageshow`, which covers a bfcache restore) or back onto the network
  (`online`) **reopens the stream at once** rather than waiting for a retry.

Driven against a running rehearsal at 375x812: on a stream error the pill
appeared and the version moved by 2 through the poll alone; on a silent close
the version moved 0 over three seconds — the frozen phone, exactly — and a
`visibilitychange` reopened it and the version moved 5.

## Ceilings on the clock

The bid ceiling is the number the screen now points at, and it is a bisection
over whole re-solves. Measured on the VPS mid-draft, on two worker processes
in a 16-team 2027 room on BBM projections, one took **8, 21, 24 and 56
seconds** for four players against a **30-second** nomination clock. A pick
applies in 9ms and a plan re-solve is cached per pick sequence, so the ceiling
was the whole of the problem. Three answers.

**A number at once, refined after.** The moment a player hits the block the
card carries an estimate of what he is worth to us, computed with no search at
all (`app/draft/estimate.py`): the best swap he could make into the plan,
priced through the league's own curve (`app.scoring.draft`,
`PRICE_INTERCEPT`/`PRICE_SLOPE`, fitted on 1,206 picks 2019-2026). Since the
question is marginal it is the curve's *slope* that is used — one more
category a week bought around a price of p costs `2*sqrt(p)/PRICE_SLOPE` — so
a player `gain` better than the $p place he would take is worth about
`p + gain * 2*sqrt(p)/PRICE_SLOPE`. It is thirteen roster scorings, not a
search. The screen shows it in the warning colour and says it is an estimate,
and replaces it the moment the exact ceiling lands. There is never a blank
where a number can go.

Against the exact ceiling, on 30 nominations of the 2026 replay where both
exist: mean absolute **$3.87**, median **$2**, within $3 on 67%, within $5 on
73%, within $10 on 90%, worst $20. It is an estimate and is labelled one.

**Fewer solves.** `bid_ceiling` was about nine solves at four restarts, which
is fifty-four local searches. Four changes (see its docstring):

* the without-him baseline is **reused from the cached plan** when the player
  is not in it — a plan that did not choose him *is* the best roster without
  him, and the session already has one per pick sequence;
* the with-him solves **drop their restarts**, because they warm start from
  that baseline, and each one now also seeds the next with the roster it
  found — a poor start at the top of the range was where accuracy went;
* the bisection stops on a **two-dollar bracket**, or a twentieth of the
  range where that is narrower, and returns the low end, a price already
  proved worth paying, so stopping early errs low;
* its **first probe goes to the expected going price** rather than the middle
  of the range.

**A worker of his own.** The man on the block gets a reserved process
(`block_executor`), so his ceiling never queues behind the precomputation of
twelve likely nominations — and, since he is usually one of those twelve
himself, work already queued for him is taken back and handed to his worker.
The precomputation is still ranked by market price and nothing else: a
rehearsal knows who is nominated next and is never allowed to say so, because
the rehearsal may not do anything a real draft could not.

### The accuracy gate

`scripts/ceiling_check.py` replays the 2026 draft into the 2027 room and, at
every nomination, computes the ceiling both the old way and the new way
against the same room. Over all **169** nominations of the replay that are on
our board:

| difference, new less old | count | share |
| --- | --- | --- |
| $0 | 144 | 85.2% |
| $1 | 16 | 9.5% |
| $2 | 2 | 1.2% |
| $3 or more | 6 | 3.6% |
| one path priced him, the other did not | 1 | 0.6% |

Mean absolute **$0.35**, median $0, worst $16. **Over $2 on 7 of 169, 4.1%**,
inside the 5% the gate allows, so **the fast path is the default**. The margin
is not large: a third of the nominations sampled on their own came out at 5.3%,
so this is a gate that passed rather than one that passed comfortably.
`bid_ceiling(..., fast=False)` is the old path exactly, and
`scripts/redraft.py` uses it deliberately — a scorecard wants numbers
comparable with last month's, not fast ones.

Where the two disagree by more than $2 the script asks a 24-restart solve
which without-him roster was nearer the truth: the new path's was closer 5
times, the old path's 3. The disagreements are the old path's baseline search
being noisy, not the shortcut being sloppy. The worst case, Cade Cunningham at
pick 5 ($46 old, $30 new), is exactly that: the old baseline scored 5.4713,
the cached plan 5.4820 and a 40-restart solve 5.4931, so the old path was
measuring against the worst roster of the three and handing out a ceiling $16
too high.

Seconds for one ceiling, seven workers contending on an eight-core Mac, which
is the loaded case rather than the quiet one:

| | median | 90th | max |
| --- | --- | --- | --- |
| old | 1.0s | 30.4s | 44.9s |
| new | 0.3s | 6.6s | 12.4s |

The median is small because most late nominations are trivial. The 90th
percentile is the one that matters, and it is what Patrick measured: **30.4s
to 6.6s**, worst case **44.9s to 12.4s**. Inside the clock.

## The theme

Dark is the default and the design the screen was approved in. The switch in
the masthead flips to the light palette and back, and the choice is kept in
`localStorage` (wrapped in `try`/`catch`, so a private window merely forgets
it). Both palettes are the same tokens on `:root` and `:root[data-theme=
"light"]`; no colour is written anywhere else in the page.

## `GET /api/pool`

The strips, the scarcity panel and the standings are all sums over the whole
pool, so the pool goes out once rather than a card at a time:

```json
{
  "source": "espn",
  "source_note": "ESPN's projections, 2026 projected",
  "categories": ["FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"],
  "withheld": false,
  "withheld_note": null,
  "players": [
    {"player_id": 3468, "name": "Russell Westbrook", "position": "PG",
     "eligible": ["PG", "SG", "UT"], "team_id": null, "price": null,
     "games": 67.0, "line": {"FG%": 0.448, "...": 0}, "fga": 10.2, "fta": 2.8,
     "board_price": 5, "market_price": 4}
  ]
}
```

Read-only, and computed from what the room already holds: `Room.lines`
(`app/draft/live.py`) is the pool's projections divided by the games they are
spread over, the two percentages left as rates with the attempts behind them
carried alongside — because a rate cannot be averaged across a roster or a pool
without them. `team_id` and `price` are filled in for a player already bought.

**The category order is fixed** — FG%, FT%, 3PM, PTS, REB, AST, STL, BLK, TO —
in `SCREEN_CATEGORIES`, and it is the order everywhere on the page. ESPN's own
order is whatever the league's settings say and has moved between seasons.

## The gate

Everything in the pool response below the name is derived per player from the
projections, so it goes out through the same check as a card
(`docs/projection_sources.md`): `may_show(room.projection_source,
viewer_owns_source=True)`. When that answer is false the response carries
`"withheld": true`, a `withheld_note` naming the source, and each player keeps
only his name, position, eligibility and whether he has been bought. The page
then degrades on purpose: no strips, no scarcity, no standings, and a sentence
in each of those places saying why. The board, the money, the picks and the
plan are unaffected, because none of them is per-player projection data.

`viewer_owns_source` is a constant `True` today, as it is everywhere else in
this codebase; it is the one argument that has to learn an answer when accounts
land. A room drafted on ESPN or on an uploaded set is never gated, and a room
with no `bbm` rows at all renders with no BBM column, no BBM facts and no
analyst note rather than with empty boxes.

## Rehearsing

`.claude/launch.json` carries two configurations: **draft-rehearsal** (the room
on Basketball Monster, port 8765) and **draft-rehearsal-espn** (the same room
on ESPN's 2026 projections, port 8766, writing its pick log outside `logs/` so
a rehearsal can never be mistaken for the real draft's log). Both replay the
league's real 2026 auction into the 2027 room. The ESPN one is the way to see the screen behave with `bbm` absent.
