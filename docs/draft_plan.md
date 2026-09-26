# The draft plan

**Written:** 2026-09-26, two weeks before the 2027 auction (Sat, Oct 10,
2:00 PM ET). **Status:** built and driven on a copy of the league's own
database: the engine, the kept marks, the route, the page, the rail item, the
room reading the plan on the night, the co-manager's `draft_board`, and the
fan-team setting. Local only; not deployed.

The owner (2026-09-23): "We need planning help. We also need to build a UI
for making a plan." The rule the page is built to: **a plan the manager
makes, not one he is handed.** The model proposes -- the spending ladder, a
ceiling per man, its lists. The manager edits -- his own going price and
ceiling per man, a tag per man, the ladder's amounts, a note -- and what he
keeps is what the draft room shows him on the night.

## The page

`/l/{l}/{s}/team/{t}/draft/plan` (`app/api/static/draft-plan.html`), in the
rail's TEAM group between Trades and Season. The team layer's check, as every
team page; its data route asks the projection source's gate as well.

- **The header line**: `Draft plan · 2027 · auction Sat, Oct 10 · 2:00 PM ET`,
  the pools he may plan on as a segmented control (BBM, ESPN, his own sets),
  Build again, and the page's account.
- **The facts line**: POT (`$3,200 · 16 teams`), BUDGET (`$200 · 13
  places`), NOMINATE (`13th of 16`), CAP (`$60 while the top place is open`,
  his ladder's when he kept one), SOURCE (`BBM captured Sep 17 (9 days)`,
  flagged OLD past seven days). Every figure opens the drawer on its field
  and, for the source, the command that refreshes it.
- **THE LADDER**: a field per place, largest first; the sum against the
  budget, red and not kept until it adds up; "Reset to the league's shape".
  The game plan's sentences (draft balanced, pay for the middle, a star only
  inside the price) are the drawer's facts. After must-have men, the shape
  refit to the places and money left.
- **THE BOARD**: the pool as the screener -- PLAYER (mark, his tag), POS,
  GOING $, YOUR GOING $, OURS $, YOUR CEILING $, BBM $, BEST (in the best
  roster at his prices), TAG, G, and the nine as glyphs. Sortable; filtered by
  position, tag and name; the men worth $3 or more first, the rest behind
  "Show the rest". A row opens the man in the drawer: the tag as a segmented
  control, a note, "Reset to the model's", his nine with their figures, and
  every figure with where it came from and which one the build read.
- **The model's lists**: STARS, FAN (only with a fan team), BBM FLAGS,
  TARGETS, BBM LIKES / WE'RE LUKEWARM, LET GO, NOMINATE EARLY, LATE STEALS &
  THREES, CHEAP BIGS, IR STASH -- each a label with its rule in the tag, a man
  a row (going, ours, BBM, his figures beside them when set, the one-line
  reason, the tag), ten shown and the rest a click away. Read over the
  effective figures, so his prices move men between lists.
- **YOUR PLAN**: how many figures are his and "Reset every figure to the
  model's"; the must-have men first with their price, the money and places
  left and what the lock costs; his targets in ladder order, his ceiling
  beside the model's, committed against the budget; his let go, nominations
  and IR pick; the best roster at his prices, place by place against the
  ladder (a price red only past the place plus its 10% slack); his notes.

A season already drafted keeps the rail item; the page says when the auction
was held and links to the league's Draft page. Before a build lands the page
says it is being worked out, and asks every five seconds.

Nothing on the page bids, nominates or touches ESPN. The words are the
house's: "inside the model's $43", "the room pays ~$80" -- never "buy" or
"avoid". No headshots.

## The engine

`app/draft/plan.py`, `build_plan(session, league_season, team, *, source,
fan_team, must) -> Plan`: everything `scripts/draft_plan.py` computed, moved
into the app. The room is the draft room's own from before the first pick
(`app.draft.live.room_for`, which `load_room` now calls): the league's winning
spending shape as the ladder (`app.draft.shape`), the tested going price
(`market_prices`), BBM's league values, and our ceiling from an empty room for
every man the market or BBM prices at $3 or more (`CONSIDER_FROM`), with the
script's four alternative builds and the fan team's costs.

Beside the script's figures the plan carries:

- **the lists** (`sections`), the old page's script moved to Python word for
  word, each with its rule. On a pool with no BBM values the lists that need
  BBM are left out and targets and let go read on our ceiling alone;
- **the nine against this league's targets** (`nine_against_targets`): a par
  roster posts exactly the median opponent (`targets.category_distributions`,
  the mean the optimizer plays against) -- a counting category shared evenly
  over the places, a percentage at the opponent's rate on an average
  rostered man's attempts -- and a man's cell is the change in the chance of
  beating that opponent when he takes a par man's place, in probability,
  scored by the optimizer's own `win_probability`. The glyphs cut at 2 and 10
  points;
- **the best roster** (the balanced build), with ids, for the page.

`scripts/draft_plan.py` is now a thin caller and writes the same JSON and page
it always has; `--from-store` builds on the stored capture instead of the
files. **The numbers did not move**: run on the same BBM exports before the
engine moved and after (twice: after the move, and after the overrides layer
landed), the JSON and the page were byte-identical (`sha1 54eb8049…` for the
JSON).

### The pool

One source per plan, never mixed (`PoolSource`): the newest stored BBM
capture for the season by default (`bbm_store.latest_capture`), else the
viewer's newest uploaded set, else ESPN's projections. BBM's stored rows are
read by the same parser as the file (`bbm.parse_records`) and put back in the
export's order by BBM's own `Rank`, because the order is the order players are
matched and the optimizer's candidates are shuffled in. A capture older than
seven days is flagged, and the drawer carries `.venv/bin/python
scripts/bbm_pull.py --season 2027 --store`.

## What it costs, and the cache

A cold build is the ceilings: 150 of them, each a bisection over re-solves.
On the 2027 BBM pool with seven worker processes on an eight-core Mac,
**125-129 seconds** (the script, 125 s wall and ten minutes of CPU; the site's
background build, 129 s; with one must man, 120 s). The VPS has fewer cores
and will be slower.

So it is built once and kept, in two layers (`app/draft/plan_store.py`):

1. **The model's build**, in `team_reports` (kind `draft_plan`, its
   "scoring period" the pool: 0 BBM, 1 ESPN, 1000 + the set id), keyed on the
   pool (a new capture is a new plan), the league's numbers, the fan team and
   the must set at its prices. When the key moves the stored build is served,
   marked `rebuilding` with the reason, while a new one runs in a thread of
   the API process; one per team and pool at a time; a failure says why until
   Build again.
2. **The manager's prices**, the fast layer (`effective`), keyed on the
   build's key and the marks' last write: the lists re-read and the best
   roster re-solved at his prices, **about 3.2-3.8 seconds** on the 2027 pool
   (plus about four seconds to reload the room when this process has not got
   it in memory). The page keeps an edit for 0.6 s, writes it, asks again and
   stamps "saved · worked out at 7:46:35 PM".

## What the manager keeps

Migration 0032. `draft_plans`, one per team-season: `ladder` (JSON, the
amount per place largest first, null for the league's shape), `notes`,
`fan_team`, `updated_at`, `updated_by`. `draft_plan_marks`, one per (plan,
man): `going_price` and `bid_up_to` (both null for the model's), `tag`
(`target`, `let_go`, `nominate`, `ir`, `must`, `none`) and `note`.
`player_id` is the room's id, an ESPN id or a BBM rookie's negative synthetic
one, so it is not a foreign key.

`PUT .../draft/plan/marks` takes any of `marks` (a field left out is left as
it is; null clears it), `ladder` (null: the league's shape again), `notes`,
and `reset: "figures"` (every going price and ceiling back to the model's;
tags and notes kept). Everything is checked before anything is kept:

- **the ladder rule**: one amount per place, each at least the $1 minimum
  bid, adding up to the budget exactly; kept largest first. A ladder the page
  puts back to the league's shape exactly is kept as none;
- **a ceiling** is a whole number from $1 to the cap -- the top place of his
  ladder plus the room's 10% slack when he kept one, else the model's cap
  (`Allocation.cap`, $60 on the 2027 shape);
- **a going price** from $1 to the most one man can cost (the budget less a
  dollar for every other place, $188).

## Your prices

Two of his figures are inputs to the plan, not only marks:

| His figure | Feeds | Never touches |
|---|---|---|
| **Your going $** (`going_price`) | the man's cost in the best roster (the balanced build's own search, warm-started from the model's roster); the lists, which read the effective going price; a must man's price | the model's going price, the ceilings, BBM |
| **Your ceiling $** (`bid_up_to`) | the ceiling the lists and the model's figure read: the lower of ours and his; a man whose ceiling is under his going price is left out of the best roster; the figure the room holds up on the night | our ceiling |

The route answers per man with the model's figures untouched (`plan.players`)
and the effective ones (`effective.players`: `going`, `ceiling`, `bid_to`,
each with `going_from` / `ceiling_from` = `model` or `yours`, the lists he is
in, `in_best`). With no figure of his, the effective plan is the model's byte
for byte and nothing is solved.

## Must-have men

The `must` tag fixes a man into the roster whatever the model thinks. The
plan is built again around the set (`must_lock`): each man bought first at his
going price (his, when he set one, else the model's), as if he had already won
him (`lock_state`: the room with our picks applied), and the ceilings, the
builds and the ladder (refit to the places and money left,
`Allocation.open_places`) worked out for the rest. A must man carries no
ceiling of his own; the board says "must".

**It has to fit**: every man in the pool, no more men than places, and their
prices leaving at least $1 for every place still open. Otherwise the route
carries one sentence -- "The must men cost $11 together, and the 0 places left
need $0 at $1 each: $1 over the $10 budget, so the plan is the free build." --
drawn red on YOUR PLAN, and the plan is the free build.

**What the lock costs** (`lock_cost`): the best roster with the set at its
prices, warm-started from the free best roster, against the free best roster,
in categories a week, for the set (`must.cost`) and for each man alone
(`must.each`). It is the fan section's own function, so a one-man set costs
exactly what the fan section says he costs: on the 2027 pool, Evan Mobley at
his $28 going price, 0.099 both ways. A figure and its provenance in the
drawer; no advice.

## The fan team

The script's Cavaliers section, generalised: `draft_plans.fan_team`, an NBA
team as Basketball Monster abbreviates it (the section reads a man's team from
BBM's row, so it is BBM's spelling), none by default. Set on Connections under
the league's numbers (`PUT .../draft/plan/settings`); the plan is built again
with a FAN section costing each of that team's men. On a pool without BBM's
rows there is no team to read, and the section is empty. The command-line
script keeps `--fan-team CLE`.

## The gates

- **The team layer's**, as every team route: his verified team, with a pass.
- **The source's**: a plan built on Basketball Monster carries its paid
  numbers per man, so it is served only to the viewer who owns that source.
  The stored captures are pulled with the site owner's membership, so that is
  the site's owner (`viewer_owns_bbm`: `Viewer.is_owner`; everyone in single
  mode). Anyone else hears `state: "withheld"`, the sentence, and the pools he
  may plan on; nothing derived from BBM is in that answer, and Build again on
  it is refused. An uploaded set is his own, and is readable only by him.
- **The season's**: a drafted season (`app.inseason.drafted`) answers
  `state: "drafted"` with when it was held and the league's Draft page.

## On the night

The draft room reads the plan for the team it drafts for when it opens
(`plan_store.marks_for`, in `scripts/draft_service.py`; `--no-plan-marks`
leaves it out). A card carries `yours` -- his figure, his tag, his going
price, his note -- beside our ceiling and never in its place. The figure held
up is his ceiling when he set one, and for a man he tagged must with none,
his going price plus the ladder's 10% slack (`held_up_to`): the plan does not
let that man go. The block's money draws it under a dashed rule, "Yours ·
from your plan"; the desk's rows carry his tag and figure (◆$43). The
ceilings on the clock and their accuracy gate are untouched.

## The co-manager

`draft_board(league_id, season, team_id, source?)` (docs/mcp.md): the route's
own answer trimmed to the ladder, the cap, the forty dearest men with `model`,
`effective` and `yours` side by side, his marked men, the lists (twelve men
each) with their rules, the must set and its lock cost, and the best roster
at his prices. Gated the same way; provenance names the pool and its capture
date. About 28k characters. It never bids or nominates.

## Every figure, traced

| On the page | Route field (`GET .../draft/plan`) | Engine |
|---|---|---|
| POT | `plan.facts.pot` | `Plan.teams × Plan.budget` |
| teams | `plan.facts.teams` | `len(state.teams)` |
| BUDGET, places | `plan.facts.budget`, `plan.facts.places` | `state.budget`, `state.roster_slots` |
| NOMINATE | `plan.facts.nominate` | `draft_order.index(me) + 1` |
| CAP | `cap` (his ladder's, `plan_store.cap_for`), else `plan.facts.cap` | `Allocation.cap(state)` |
| SOURCE, captured, age, OLD | `source.kind`, `source.captured_on`, `source.age_days`, `source.stale` | `PoolSource`, `source_facts` |
| the ladder | `ladder` (his), else `plan.ladder` | `Allocation.places` |
| after must men | `plan.must.places_left`, `money_left`, `ladder_after` | `must_lock` |
| GOING $ | `plan.players[].going` | `market_prices` |
| YOUR GOING $ | `marks[id].going_price` | `draft_plan_marks.going_price` |
| OURS $, cap | `plan.players[].ceiling`, `capped` | `bid_ceiling(...).price`, `.capped` |
| YOUR CEILING $ | `marks[id].bid_up_to` | `draft_plan_marks.bid_up_to` |
| BBM $ | `plan.players[].bbm_total` | `BBMRow.league_dollars` |
| BEST | `effective.players[id].in_best` | `effective`, `best_solver` |
| TAG | `marks[id].tag` | `draft_plan_marks.tag` |
| G | `plan.players[].games` | `BBMRow.games` |
| the nine | `plan.players[].nine` | `nine_against_targets` |
| the model's figure | `plan.players[].bid_to`; with his ceiling `effective.players[id].bid_to` | `bid_to` |
| the lists, rules, reasons | `effective.sections` | `sections` over the effective figures |
| lock cost | `plan.must.cost`, `plan.must.each` | `lock_cost` |
| the best roster | `effective.best` | the balanced build, or `best_solver` |
| "worked out at" | `effective.rebuilt_at` | `plan_store.effective_for` |

`scratchpad/dplan/shoot.py` (not in the repo) read 40 board rows, the facts
line and the ladder off the page and asserted each equal to the route's
field: all equal.

## Tests

`tests/test_draft_plan.py` (the engine, on a two-team room: the lists' rules,
the model's figure, the nine, a must set that fits and one that does not, a
one-man set costing what the fan section says, a man whose raised going
price moves him from targets to let go, a ceiling under the going price
keeping him out of the best roster, the effective figures beside the
model's, reset restoring the model's plan byte for byte);
`tests/test_api_draft_plan.py` (the route's shape, the marks' round trip and
checks, the ladder rule, a must man, the fan team, the drafted season, the
team gate, the source gate, and `draft_board`'s gate); the rail item in
`tests/test_shell.py`; the room's `yours` in `tests/test_draft_session.py`.
