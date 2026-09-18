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
eligibility, the games he is projected for, and three amounts — *our price*
(the board's valuation), *plan pays* (his place in the best roster we can still
finish, or that he has none) and *went for* (which reads *current offer* while
he is still being bid on). Under them the nine-category strip, each cell shaded
by where he sits among everyone on this board, and then the verdict from our
ceiling, the bargain warning when there is one, and — only when the room was
loaded on Basketball Monster — its facts and its analyst's note.

**The desk.** Entering a pick by name with autocomplete (`/` jumps to the name
box, `Enter` moves to the price and saves), the team to charge it to, undo, put
on the block, clear the block, and a search of everyone still for sale with
what he will go for, our ceiling and BBM's value when there is one.

**The board.** Teams across, roster places down, every pick with its price, the
most expensive at the top of each column. Our column is in the accent colour
and our open places carry, in italic, what the plan says each should cost. The
team to nominate next is underlined. The foot of each column: money left,
places open, maximum bid, and the average left per place. Hovering, tapping or
tabbing to any cell raises that player's card with his own strip.

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
events) and the service pushes the whole state each time anything moves. When
the stream drops, the status pill says *service not reachable — retrying* and
the browser reconnects. Replay warnings from the pick log are printed in the
footnote.

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
