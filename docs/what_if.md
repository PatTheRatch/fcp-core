# What if: one named move, over three horizons

**League:** Full Court Press (ESPN 3853870), nine-category H2H
**Written:** 2026-09-23, alongside the build
**Status:** built (`app/inseason/what_if.py`, `app/api/what_if.py`, the trade
page's Finish block, the `what_if` MCP tool, and the Week page's **What if**
form — §7, added the same day).
**Companions:** [`pickups.md`](pickups.md) §4.3-4.5 (the week report and its
bar), [`trades.md`](trades.md) §1 and §6, [`projected_record.md`](projected_record.md)
§2-§3 (the engine the finish comes from, and the seam this takes),
[`mcp.md`](mcp.md), [`product.md`](product.md)

---

## 0. Why

The owner asked, on 2026-09-23:

> *if someone is like what happens if I drop this person for this person --
> for my week and for my season outlook -- and the same for trades: I want to
> know if it enhances my projected finish or not.*

Half of that was already built and is not touched here. The pickup
recommender and the trade evaluator both carry this week's category chances
before and after, the change per week over the rest of the season, the net
over both horizons, and a projected category record with the move and without
it. Two things were missing.

**A manager could not name his own pickup.** `/pickups/stream` answers "what
should I do", ranked by a search over every legal swap; there was no route
that answered "what happens if I do *this*". The trade page has taken an
arbitrary pair from the beginning; the week page never could.

**Nothing hypothetical reached the projected finish.** The record a judgement
carries (`app.pickups.judge.Judgement.record_without` / `record_with`) is
played against a **league-average** opponent. It is comparable across teams,
it is the right yardstick for the bar, and it is not where a manager
finishes. Where he finishes is `app.inseason.projected`, which plays every
remaining week against the real opponent on the real schedule and runs ten
thousand seasons for the place and the playoff odds -- and which, until now,
only ever ran on the rosters as they stand.
[`projected_record.md`](projected_record.md) §3, "The seam that is
deliberately not taken yet", names exactly that gap and leaves it as a
decision of its own. The owner has now made the decision for the
**hypothetical** view: the finish a move produces comes from the projected
engine.

---

## 1. The three layers, and the fourth thing beside them

`app/inseason/what_if.py`, pure over loaded state like the rest of the
recommender. One entry point for a pickup and one for a deal:

```python
what_if(session, league_season, team_id, today, *, add=(), drop=(), to_ir=(),
        hurdle=…, floor=…, opened=…, n_sims=N_SIMS, seed=SEED) -> WhatIf
trade_finishes(session, league_season, report) -> dict[int, Finish]
```

### The change

`Change` is a dataclass: player ids arriving, player ids leaving, and player
ids moved to injured reserve. A man on injured reserve keeps his roster place
and stops being started, which is why he is not a drop and why nothing is
charged for him over the rest of the season -- the rule
`stream_recommendations` already applies to its own `IR_MOVE`.

For a **trade** the change is not rebuilt here at all. `trade_finishes` reads
each side's men leaving off its `gives` and its `drops`, and its men arriving
off its `receives` and its `fills`, which is exactly `_Side.leaving` and
`_Side.arriving` *after* the evaluator has settled who gets dropped. So the
roster the finish is projected on is the roster the deal was scored on, man
for man, and there is no second construction to disagree with the first.

### This week

`app.pickups.stream.week_changes`, which is `week_deltas` with the working
shown. The same function the rest-of-season report and the trade page call,
so the week page, the trade page and this cannot disagree about what a swap
does to a Thursday. It gives the nine chances against the real opponent's
projected line before and after, the expected categories either way, the days
left, the starts each man gets, and whether the move seats somebody on a day a
lineup slot was going empty.

`week_deltas` is now literally `[change.delta for change in week_changes(...)]`
and every number it has ever returned is unchanged.

### The rest of the season

Each remaining matchup: the period, the opponent, and the expected categories
won with the change and without it, from the projected engine. The pairings
are the league's stored schedule and a roster change cannot move them, so the
two lists are the same weeks in the same order and are read together.

### The finish

`project_standings` grew one argument:

```python
rosters: Mapping[int, Collection[int]] | None = None
```

keyed by ESPN team id, holding the men that team is projected on instead of
the ones stored. Every team not named stands exactly as it is. Only men who
can be started belong in the list, because a projection seats nobody from
injured reserve.

The finish layer is that engine run twice on the same day, the same
`n_sims`, the same `seed` and the same measured weekly spreads: once as the
league stands, once with the change in. Out of the pair comes the projected
final category record, the projected matchup record, the place (the team's row
in the engine's own ordering, which is the order the Standings page prints;
since 2026-09-25 each simulated table is ranked on category win share, not
matchups won, and the finish carries that order in words as `ranked_by`),
the playoff odds, the bye odds and the full seed distribution -- before and
after, each of them.

For a **deal**, both sides' rosters are changed in **one** projection,
because the deal happens to both of them at once: each side's finish is read
off a table where the other side has its new roster too.

### And beside all three: the judgement, untouched

`delta_week`, `delta_season_per_week`, `weeks_remaining`, the net,
`record_without` / `record_with`, the hurdle label and the FAAB bid are the
recommender's own. They are not re-derived here and they are not replaced.
The hurdle, the pickup backtest, the bid model and the trade calibration are
all priced on those numbers; a second derivation would be a second thing to
be wrong.

Making that true took three names being made public in
`app.pickups.stream` rather than three new functions:

* `evaluated_wire` -- the free agents a week report actually evaluates, less
  whoever the team holds, cut to the best eighty by that week's line. This one
  matters most: the replacement charge is the best free agent *in that set*,
  so reading a different wire is a different season term for the same swap.
  It takes a `keep` since 2026-09-24, and the what-if passes the men it was
  asked about: a man ESPN has ruled out has no games this week, so his line is
  zero and the cut dropped him, which made a stash unaskable (`stash_mode.md`).
* `spot_book` -- the lens and the weekly values the charge is taken through.
* `priced` -- the FAAB bid, so a named pickup that clears the bar is priced by
  the function that prices every other pickup.

`tests/test_inseason_what_if.py::test_a_named_move_is_the_recommenders_own_numbers`
runs the search, takes its best move, asks for the same move by name, and
compares every one of those numbers. The day somebody changes one path the
other fails, rather than drifting.

### And, when the man is not playing yet: the stash block

Added 2026-09-24 ([`stash_mode.md`](stash_mode.md)). When the man being added
or moved to injured reserve is one ESPN has ruled out, the answer carries a
`stash` beside the three layers: `days_out`, `return_odds_by_week` (P back by
weeks 1, 2, 4 and 8), `expected_dead_weeks`, `dead_cost`, `expected_net`,
`net_if_out_past_week` and `ir_slot_free`, with a `language` saying the odds
are the NBA's own return record for men out this long and **not a diagnosis, a
timeline or a date**. There is no date to print: ESPN's basketball API carries
no return-date field at all.

Two things about it are worth being clear on, because both are easy to get
wrong. **It is not a second opinion on the move.** The engine already prices
what a ruled-out man is worth — his expected games feed `rest_of_season_line`
like anyone else's — so `expected_net` is the judgement's own net less one
term the judgement cannot see: that the roster place he is holding cannot be
streamed while he waits. **And it is not a bar.** Nothing is labelled against
it; a stash under the hurdle comes back in full with its number and its odds,
exactly as any other move under the hurdle does.

The page renders one line under the number, in the house style:

> Out 18 days · back within a fortnight 48% · dead weeks cost 1.58 · expected
> −1.76 (or −3.52 if he is not back by week 4)

The trades page prints the same line per man under each side's finish block,
for the men that side is taking on who are out.

#### And, when the team has already won its place: the lock line

Added 2026-09-24 ([`stash_locks.md`](stash_locks.md)). When the finish layer
puts the team's playoff odds at or above **0.95** as the league stands, the
stash block carries a `lock` beside the first reading and never instead of it:
`playoff_odds`, `seeding_stake` (`1 − max(seed odds)`), `back_by_playoffs`,
`playoff_weeks`, `playoff_weeks_value`, `cost`, `lock_net` and the two bounds
`net_if_seed_settled` and `net_if_seed_open`.

The reason is measured rather than argued. Over 294 lock stashes in eight
seasons the dead place costs a lock **0.18 categories against the 0.38 the
census charges it**, because the median seeding stake is 0.51 — about half of
every dead regular week is a week the team was not going to be paid for. The
two nets disagree on the sign for **26.87%** of lock stashes and for **46.38%**
of the ones whose man reached the bracket, which is why it is worth a line.
And the engine's own counterfactual says why: taking the stashed man off the
roster moves a lock's playoff odds by a median **0.006** and the chance of its
own most likely seed by **0.040**.

> You are a lock (98%) · the dead weeks cost your seeding 0.21 · back for the
> playoffs 71% · worth +1.20 over the 3 playoff weeks · lock net +0.64 (+0.85
> to +0.32)

### And on every move: the playoff lens

Added 2026-09-24. The owner: *"maybe for all the moves in the what-if we show
a playoff impact too. We wouldn't actually know who we are playing, but it
could give some indication if people want to think that far ahead."*

So every answer carries `playoffs` — the same change counted over the playoff
matchup periods alone: `weeks`, `games_added` against `games_dropped`,
`delta_per_week`, `delta_total`, `expected_wins_before` and
`expected_wins_after`, the nine categories, and `measurable` with a `note`
when there is nothing to count.

**It is the trade evaluator's own lens, not a second one.**
`app.trades.evaluate.playoff_lens` was already computing exactly this for each
side of a deal; it now takes a roster change as plain tuples and the trade
passes its side's while the what-if passes its own drops, IR moves and adds.
A trade and a what-if about the same two men therefore give the same March
number, which two implementations could not have guaranteed.

**The opponent is the league's average week, and the payload says so.** The
bracket is not known while the regular season is being played — the pairings
depend on seeding nobody has earned, which is the whole of
`app.inseason.projected.NO_BRACKET` — so `PLAYOFF_LANGUAGE` travels with the
block and the page prints it under the line. It is a lens and not a bar.

> Playoff weeks (3): −0.08 a week · 8 games in against 7 out · 4.6 categories
> a week becomes 4.5 · bracket unknown, priced against an average opponent

**It costs nothing worth naming.** Measured on the stored 2026 season, the
same swap judged twice with the lens and twice without, on two real mornings:

| day | without | with |
|---|---|---|
| 52 | 1.93 s, 2.35 s | 2.27 s, 1.89 s |
| 80 | 2.03 s, 2.12 s | 1.88 s, 2.10 s |

A mean of **2.11 s without and 2.04 s with**: the difference is smaller than
the spread between two identical calls. The reason is that the per-game rates
are already cached per day by the projections above it, so the lens is one
extra `build_players` pass over the playoff days and a scaling. It is
therefore always on, and there is no `?playoffs=1` to ask for.

---

## 2. The noise, stated

The finish deltas one man moves are small. On the live example of §5 the
playoff odds move by two tenths of a point, and the odds are counted off a
simulation, so they carry sampling error of their own. A payload that printed
"92.7% → 92.9%" and stopped would be claiming a precision it does not have.

`Finish.odds_band` is the ninety-five percent band on **one** odds figure at
`n_sims`, from the binomial alone:

    band = 1.96 * sqrt(p(1-p) / n_sims)

taken at whichever of the two odds sits nearer a coin, which is the wider of
the two. At ten thousand seasons and a coin that is about one point; at 92.7%
it is half a point. `Finish.readable` is False when the odds moved by less
than the band, and the payload then carries a note saying to read the finish
as unchanged. The pages print the band in brackets and add "so this is inside
the noise" when it applies.

**The difference is steadier than the band suggests, and that is worth
saying.** Before and after are run from the same seed, and `_simulate` draws
one uniform per matchup in a fixed order, so every matchup the change does not
touch is drawn identically in both worlds. It is a paired comparison, not two
independent ones, and the common draws cancel; what does not cancel is the
part of the draw the change actually moves, which is the part being measured.
The band is still the honest size of either *level*.

**Which of the two the brief allowed was taken, and why.** The binomial,
rather than running the after-roster twice from different seeds and printing
the spread. It is free, where a second pair of runs would double the cost of a
call that already pays for two league projections (§4); and a two-seed spread
would measure the wrong thing anyway, because it would break the pairing that
makes the difference readable at all.

Beside the band, every finish carries
`app.inseason.projected_calibration.SHORT_NOTE` verbatim -- *"Made at the
halfway mark of 2026, this projection's final records were off by 7.2
categories on average."* -- and the MCP answers carry it in the provenance as
`projected_record_note`, because the finish comes out of a different forecast
from the week's chances and has a different record of being right.

---

## 3. The finish is a second lens, not a second bar

This is the decision the rest of the design hangs on, and it is stated in the
code as a constant (`what_if.FINISH_IS_A_SECOND_LENS`) so that the route, the
tool and the page all say it in the same words.

Nothing is labelled, recommended, ranked or refused on the finish. The bar a
move is read against is still `stream_hurdle` on `Judgement.delta_total`,
measured on the pickup backtest; the bar a deal is read against is still
`season_hurdle_paid`, measured on six seasons of trades. The finish is a
second way of looking at the same roster -- against the real opponent each
week rather than a league-average one -- and it has no measured record of
pointing at good decisions, because nobody has measured one.

The alternative was to make the finish the headline, which is what a manager
asking "does it enhance my projected finish" might expect. It was not taken.
The projected-standings calibration (`projected_record.md` §0) is plain about
what that forecast is worth: it is about as sure as it ought to be in the
middle, it is overconfident at the ends, it names the right side of a matchup
five weeks out about 55% of the time, and its playoff odds in the 60-70% band
came in at 49% once the table was ranked on category win share (revision
R6, 2026-09-25; 57% before it, ranked by matchups won). A bar on top of that would be a bar on a coin. The
recommender's own numbers have a backtest under them; the finish has a
reliability table and a warning. Both are on the page, in that order.

---

## 4. Timing, and where the time goes

On the stored 2026 season, fourteen teams, day 52 (period 8 of 19, twelve
weeks left), on the local Docker Postgres, measured piece by piece:

| | seconds |
|---|---|
| `category_distributions` (the league's measured weekly spreads) | **2.88** |
| `project_standings`, the "before" table | 1.54 |
| `project_standings`, the "after" table (warm) | 0.72 |
| `load_team_week` + `evaluated_wire` + `spot_book` + `week_changes` | 0.38 |
| **a whole `what_if` call, warm** | **4.3** |

A cold call in a fresh process reads **34 s**, and 32 of those are
`app.pickups.bids.bid_fit` building the league's whole FAAB history on its
first call. That cost is not this route's: the week report pays exactly the
same 32 s the first time a move clears the bar and a bid has to be priced, and
it is memoised for the life of the process, so the morning precompute warms it
long before a manager opens a page. It is named here because a cold `what_if`
in a fresh shell looks alarming and is not.

**So a warm call is about 4.3 s, which is over the three seconds the brief
asked about, and two thirds of it is one line.** `category_distributions`
alone is 2.9 s of the 4.3. The two projections together are 2.3 s cold and
1.4 s warm.

Three things were considered and one was done.

1. **Read the spreads once per call and hand them to everything.** Done, in
   both routes. `what_if` reads them once and passes them to
   `evaluated_wire`, `spot_book`, `week_changes` and both projections;
   `/trades/report` reads them once and passes them to `evaluate_trade` and
   to both projections of the finish layer, where before the evaluator read
   its own. Without this a what-if would pay 2.9 s three times over.
2. **Cache the league's "before" projection.** The morning's
   `project_standings` job already stores it as a `league_reports` row, and
   the route could read it instead of building one. It was **not** done, and
   the measurement is why: it saves 1.5 s cold and 0.7 s warm, which leaves
   the call at 3.6 s -- still over the bar -- and it buys that by mixing a
   table built this morning with a table built now. On a replayed day
   (`?today=52`, which is every calibration and rehearsal page) there is no
   stored row at all, so the saving is zero exactly where the route is used
   most.
3. **Cache `category_distributions` across a process.** This is the one that
   would work: it takes a warm call to about 1.4 s. It is
   `docs/inseason_rehearsal.md` finding 4, it would help every job and every
   route rather than this one, and it is a change to a function the draft
   room and the whole recommender share. It belongs with that finding and not
   here.

The trade report's own cost: `evaluate_trade` is 0.27 s with the spreads
already read, and the finish layer adds **1.48 s** on top of it. So
`/trades/report` goes from about 3.2 s to about 4.7 s warm, and the same 2.9 s
of spreads is still the largest single line in it.

---

## 5. What it says, on a real day

Through The Wire, day 52 of the stored 2026 season, dropping Maxime Raynaud
for Josh Minott -- which is the move that team's own week report ranks first
that morning:

```
week    period 8 vs Brockley Heat, 4 days left
        expected categories 4.900 -> 4.926  (+0.0269)
        3PM 0.659 -> 0.686, STL 0.527 -> 0.549, FG% 0.166 -> 0.153
judgement
        delta_week +0.0269   season +0.0391 a week over 12.0 weeks
        net +0.4964          per week +0.0382
        record without (95.8, 75.2)   with (96.3, 74.7)
        clears the 0.20 bar (the one the owner set)   bid $2
finish  94.6-76.4 · 3rd · playoffs 92.7%  ->  94.7-76.3 · 3rd · 92.9%
        ±0.5 points on each, so this is inside the noise
```

The last two lines are the whole point of the exercise, and they say something
the judgement does not: a move worth half a category over the rest of the
season, which clears the bar and is worth making, moves this team's finish by
less than the simulation can resolve. It is already third and it is already at
92.7%. The judgement says take it; the finish says it will not be what decides
the year. Both are true and the payload says both.

The same day, the Turner-Queta deal -- Foxes give Neemias Queta to Through The
Wire for Myles Turner:

```
Foxes ShutUpNDribble   net +0.7043, +0.054 a week (below the 0.09 bar)
        84.1-86.9 · 6th · playoffs 58.8%  ->  84.1-86.9 · 6th · 58.7%
        ±1.0 points, so this is inside the noise
Through The Wire       net -0.5499, -0.042 a week (our estimate of his side)
        94.6-76.4 · 3rd · playoffs 92.7%  ->  94.4-76.6 · 3rd · 92.2%
        ±0.5 points
```

Two things a reader should take from that. The side the categories say gains
half a category a week barely moves its odds, because it is already a coin for
a playoff place and one man does not settle a coin. And the side that loses
half a category a week moves *further* in odds terms, because it is up near the
top where a small loss of expected categories costs real seed probability.
Neither of those is in the judgement, and neither is a reason to make or
refuse the deal.

---

## 6. Refusals

Every way a change can be impossible gets one sentence a manager can act on,
in the house style of [`trades.md`](trades.md) §10. The sentences live as
constants in `app/inseason/what_if.py` so the words are in one place and the
tests hold them; the route turns each into a 422.

| when | what it says |
|---|---|
| nothing named | Name at least one man to add, drop or move to injured reserve. |
| a drop he does not hold | *{team}* does not have *{names}* on its roster on day *{day}*. |
| an add who is not on the wire | *{names}* is not a free agent on day *{day}*: a what-if can only add a man off the wire. To ask about a man another team holds, judge it as a trade. |
| the roster would be over size | *{team}* would hold *{n}* players after this change and the roster holds *{size}*. Drop one more, or add one fewer. |
| the roster would be under size | This change leaves *{team}* a roster place short… Name the man going into the place, or ask the season report what dropping him alone costs. |
| outside the position limits | *{team}* would be outside this league's position limits after this change (*at most 3 at C*). Swap a man at a different position. |
| an injured-reserve move with no place | *{team}* has no free injured-reserve place on day *{day}*… |
| an injured-reserve move on a fit man | *{name}* is not ruled out on day *{day}*, and ESPN's injured reserve only holds a man who is. |
| a player id nobody knows | There is no player *{id}* on record… (the route's own, because the ids come in as ESPN's) |
| a season not drafted, or with no schedule or no rosters | not a refusal: 200 with `readiness` and every number empty, before any name is read (docs/site.md, "Readiness") |
| a day in no matchup period | scoring period *{day}* is in no matchup period of *{season}* -- the projected engine's own sentence, which is a preseason with no matchups yet and a season already over |

A **drop with no add** is refused rather than answered, which is a choice
worth naming. It is a legal thing to do in ESPN and the judgement can price it
(`places_cost` values a place left open at `OPENED_PLACE`). It is refused here
because a what-if is a comparison and "drop him for nothing" is not the
comparison anybody means by it; the question "what does dropping him alone
cost" is the season report's `drop_candidates`, which already answers it with
the wire's replacement named.

---

## 7. The Week page: what it calls and renders

**Built**, 2026-09-23, in `app/api/static/week.html` and the shared
`pages.js`. It went in directly under **The read**, which is where the
redesign put the plan, because a move the manager names is his own read and
belongs beside ours rather than beside the rest of the season. The rest of
this section is what it calls and draws, as built.

A **"What if"** form: two pickers, one more where the league allows it, and
a button:

* **Drop** — a select over the team's own roster, drawn from the day's own
  report (`/today`), which the page fetches first and which is the only
  thing on it carrying every rostered man: the lineup, the bench, the men
  with no game and injured reserve. Nothing new is read. A man on injured
  reserve is marked and cannot be picked, because he keeps his roster place
  and is not a drop, which is `_check`'s own rule (§1, §6).
* **Add** — a chooser over the wire, from
  `GET .../teams/{team_id}/trades/pool?with_team={any other team}&side=ours`,
  which is the sorted pool the trade page's chooser already draws: each row
  carries `name`, `position`, `pro_team`, `games_left`, `value` (what he gives
  an ordinary place, the league standard) and `weekly` (his nine), plus `hurt`
  and `on_waivers` badges. The pool route needs a deal to price *worth*
  against; a straight pickup has none, so the list is re-sorted and shown by
  `value` and `worth` is ignored. It is the trade page's own row, markup and
  CSS, with the shared player card beside the name.
* **to IR**, shown only when the week report says `ir_slot_free` and the
  roster holds a man whose `injury_status` is OUT. Picking it clears the
  drop and picking a drop clears it: one man arriving fills one place, and
  naming both leaves the roster short, which the route refuses.

**Nothing is fetched until it is asked for.** The wire is read the first
time the chooser is opened, and the move is judged only on **Run**, so the
section costs a first load a heading and a form and no request at all — 363
px of 2,992 at 1280, 441 px of 4,107 at 390. The cold call of §4 is real and
the button says so while it waits.

Run calls
`GET .../teams/{team_id}/what-if?drop={espn_id}&add={espn_id}&today={day}`
— carrying `?today=` exactly as every other fetch on the page does — and the
answer is drawn in the same three layers as the trades block:

1. **This week.** `week.after` in the three bands the page's own **The nine**
   draws, with the categories in `week.moved` tinted and carrying what each
   was and how far it went in points of probability, under the figure. A sign
   and a word before a colour: the strip is read by people who see no colour.
   Above it `week.expected_before` → `week.expected_after`, the delta, the
   opponent and `week.days_remaining`, and `week.fills_empty_day` as a clause
   when it is true.
2. **The number.** `judgement` through the page's existing `judged()` helper
   (it is the same `JudgementOut` shape the week report's moves carry), in
   the page's own `article.move`, plus `net`, `clears_hurdle` against
   `hurdle` as the same *clears the bar* / *below the bar* label the list
   under More uses, `hurdle_source` / `hurdle_note` for where the bar came
   from, and `bid` when there is one.
3. **Finish.** The same compact block the trade page draws — `finishHtml`,
   now in `app/api/static/pages.js` so both pages draw one implementation:
   `Projected 94.6-76.4 · 3rd · playoffs 92.7% → 94.7-76.3 · 3rd · 92.9%`,
   the band in brackets with "so this is inside the noise" when the odds
   moved by less than it, and `finish.calibration_note` underneath in faint
   type. Where they did, the route's own sentence from `notes` follows it, so
   a finish the simulation cannot resolve is said in words and not left to
   two percentages a tenth of a point apart.

The section's lede carries `finish.language` verbatim once an answer is in
hand: the finish is a second lens and not a second bar. Nothing in the
section is re-sorted, hidden, ranked or labelled on it.

A change that cannot be made is the route's 422 sentence, printed as it is
sent, in the warn line — the one other thing on this page stated as a
mistake. The form keeps its last drop and add in `localStorage`, wrapped, so
a re-run after a reload is one tap and a private window merely forgets.

`finish.weeks` is **not** drawn. The page's "Rest of season" table is built
from `/projected` under More and a second column on it would have to be
un-drawn on every new Run; the week-by-week list is on the payload for
whoever wants it next.

---

## 8. Decisions taken here

1. **The judgement is the recommender's, read off the recommender's own
   functions.** §1. Anything else would eventually give two answers to one
   question, and the bar would be priced on one of them.
2. **The seam of `projected_record.md` §3 is taken for the hypothetical view
   only.** The stored per-team reports still play a league-average opponent
   and still carry `record_without` / `record_with` unchanged. Moving those
   would move every pickup and trade number on the site and needs its own
   backtest; that decision is still open and still named there.
3. **The finish is a second lens and not a second bar.** §3.
4. **Both sides of a trade change in one projection.** The deal happens to
   both of them, so reading one side's finish off a table where the other side
   still holds the man he traded away would be a table of a world that cannot
   happen.
5. **A place a deal leaves open is filled, in the finish, by the one free
   agent the report already names for it** (`SideReport.replacement_player`),
   which is the man the evaluator's own nine-category table stands in that
   place. A deal that leaves more than one place open puts him in the first
   and leaves the rest empty, because a projection seats men on days and a
   streamed lane is not a man. `SideReport.opened_value` is what those places
   are worth and is on the payload beside the finish. In this league's history
   no executed deal has left more than one place open.
6. **The noise is the binomial band, not a second seed's spread.** §2.
7. **Nothing is stored.** A hypothetical is not a report: the morning job
   precomputes the three reports a team actually has and cannot precompute a
   question nobody has asked. Every call is built live.
8. **A drop with no add is refused.** §6.
9. **Waivers are not modelled in the finish.** A man claimed off waivers
   cannot play for 48 hours, which `week_changes` does model (the week layer
   seats him from the day he clears) and which the projected engine has no
   notion of. Over the rest of a season two days is inside every other
   rounding in the model; over this week it is not, which is why the week
   layer has it and the finish does not.

## 9. Not done

- **The week-by-week list on the Week page.** §7.
- **More than one hypothetical at once.** "Which of these three pickups helps
  my finish most" would be one projection per candidate; at 0.7 s each that is
  affordable for a handful and not for a search, and nothing has measured
  whether ranking by finish is better than ranking by the bar. It is not
  offered, deliberately: a ranked list with no measurement behind it is the
  failure `docs/product.md` is about.
- **A hypothetical for another team.** The engine takes any team's roster, but
  the route is the team's own manager's, because a plan for a roster is that
  roster's.
- **The finish over the playoff weeks alone.** The trade report has a playoff
  lens (`trades.md` §1) and the projected engine does not project the bracket
  (`projected_record.md` §2); joining those two is its own piece of work.
- **`category_distributions` cached across a process**, which is the one thing
  that would take a call under three seconds. §4.
