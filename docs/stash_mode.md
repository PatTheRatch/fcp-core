# Stash mode: what a man who is not playing is worth, and what the wait costs

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H
**Built:** 2026-09-24, on the study published the day before
**Applied in:** `app/pickups/returns.py` (the rule), `app/pickups/stash.py` (the wait), and the one place the season charge counts games, `app.pickups.judge.weekly_lines`
**Companions:** [`stashes.md`](stashes.md) (the measurement this is built on),
[`availability.md`](availability.md) (the report-based curve it must not be
confused with), [`what_if.md`](what_if.md) (the engine the mode lives in),
[`pickups.md`](pickups.md) §4.4 (the season lane), [`intake.md`](intake.md)
(which numbers are per-league and which are not),
[`spread_revision.md`](spread_revision.md) (the shape of a declared revision)

---

## Declared

Written before any calibration was run, and not tuned afterwards. It is the
docstring of `app/pickups/returns.py` word for word.

1. **An OUT man's games are expected, not zero.** For a man ruled out with no
   ESPN return date, each remaining game day counts with the probability he is
   back by then — read off the box-score return prior of `stashes.md` §2a by
   how many days he has been out already — times the ramp he comes back on
   (0.91 for game days in his first week back, 0.98 in the second, 1.00
   after), as a fractional games weight in the same place a healthy man's
   whole games are counted. With an ESPN date, the date wins for the expected
   return and the ramp still applies after it. A man out longer than the
   prior's last row keeps its last value; a horizon longer than its last
   column is the same table asked again from the row he would then be on.
   Doubtful and Questionable are untouched — the availability study's play
   rates are a separate, later change.
2. **The dead weeks are priced.** A stash's cost is the expected dead
   game-days times `OPENED_PLACE` per week-equivalent — **a dead day is one
   seventh of a dead week**, `DAYS_A_WEEK = 7.0`, the divisor every other
   weekly quantity in this engine uses — and **zero when the league has a free
   injured-reserve slot**, read from `league_seasons.injured_reserve_slots`
   against the roster's own IR occupancy, because then the place is not dead.
3. **Nothing is labelled against the finish**, the what-if rule stands, the bar
   is untouched, and the hurdles, `OPENED_PLACE`, `TYPICAL_PICKUP` and
   `SPREAD_SCALE` do not move.

---

## Why the field was never filled: it does not exist

`docs/stashes.md` §Limitations 9 found that `player_status_snapshots` has never
held a non-null `expected_return_date`, in any season, on any machine, and left
open whether that was ESPN's fault or ours.

It is ESPN's, and not in the sense of a field arriving empty. **There is no
such field.** A read-only probe of the league's own player pool on 2026-09-24
(`scripts/espn_probe.py`'s two views, asked directly) returned 1,097 entries;
every one carried `injured` and 738 carried `injuryStatus`, of which **136 were
OUT**. The `player` object's whole key set is:

```
active, defaultPositionId, draftRanksByRankType, droppable, eligibleSlots,
firstName, fullName, id, injured, injuryStatus, jersey, lastName, ownership,
proGamePlayerDetail, proTeamId, stats
```

`kona_playercard`, asked for one OUT man (Kevin Durant) on his own, adds
`invalid`, `laterality`, `stance` and `universeId` and nothing else. The
strings `eturn`, `imeline`, `utDate` and `tatusDate` do not occur anywhere in
either payload.

`app/listener/pool.py` reads `player.expectedReturnDate` and parses ESPN's
`[year, month, day, …]` array correctly; the parser is right and has never had
anything to parse. `expectedReturnDate` is a field ESPN's **fantasy football**
API carries. Nothing was fixed, because nothing here is broken.

**So the prior is the whole answer**, which is what the brief said it would
mean. The date-handling branch is kept and tested anyway: the declared rule
says the date wins when there is one, and a rule with no code is not a rule.

---

## The rule, as arithmetic

### The games

`P(back within m days | out N days and still out)` is `RETURN_PRIOR`, the
seven-season box-score curve of `stashes.md` §2a with the suspended 2020 season
taken out, measured 2026-09-23 over 11,473 absences. It is shipped as a table
rather than recomputed per request: it is a measurement, and re-measuring it
every morning would make it drift.

`days_out` is **calendar days since his last played game**, which is the count
`scripts/stashes.py` measures and the count the table's rows are indexed by.
(`stashes.md` §2b's prose says a box-score day "counts only nights his team
played"; the script it describes does not do that, and this uses the script.
The caution §2b was really making stands: this curve is **not** interchangeable
with `availability.md` table 2, which counts report days.)

A game `o` days from today is then worth

```
weight(o) = Σ_{r=1..o}  P(he came back exactly r days from now) × ramp(o − r)
```

and his season games are the sum of that over every day his NBA team plays.
Today itself is worth nothing: he is out this morning, which is the same answer
`app.inseason.startable` gives and the same answer the week's own seating gives.

**Past the table's last column the chain, not a flat line.** Asked about a
horizon longer than 28 days, the table is read again from the row he would then
be on. The alternative — holding `F(28)` — would say a man 28 days out has a
47.6% chance of never playing again this season, where `stashes.md`
§Limitations 5 measures **26.0%** at the 29+ level with 2020 out. The chain
says 22.7% are still out fifty-six days later and 10.8% eighty-four days later,
which brackets the measured figure from the right side.

### The dead days

```
E[dead days] = Σ_{o=0..H−1} ( 1 − P(back within o days) )
dead cost    = OPENED_PLACE × E[dead days] / 7
```

Today is dead outright; each day after it is dead with the probability he is not
back by then; a man who never returns is dead for every day of the horizon `H`.
This reproduces `stashes.md` §6b's own accounting for the Brandon Miller claim
exactly when the realised wait is used instead of the expectation: ten dead
days, 1.43 dead weeks, 0.54 categories.

**Zero with a free injured-reserve slot.** `TeamWeek.ir_slot_free` is
`league_seasons.injured_reserve_slots` against the roster's own IR occupancy,
and it is the only thing that decides. `pickups.md` §4.4 and
`app/pickups/season.py`'s docstring both used to say the league gains a slot in
2027; the stored 2027 row says **0** (`stashes.md` §5). Both sentences are now
gone and the setting decides.

### The two arms

`expected_net` is **the recommender's own net for the move, less the dead
cost** — not a second engine and not a second lens. `judge` already prices what
the man is worth, because his expected games now feed
`rest_of_season_line`; what it cannot see is that the place he is holding
cannot be streamed while he waits, and that is the one term added.

`net_if_out_past_week` is the same judgement made again with one number
changed: his weekly value re-counted from the prior's row he would be on if he
were still out in four weeks. It is `judge` answering twice. `stashes.md`'s
central finding is that **the distribution is the answer**, so a page prints
both and the odds beside them.

---

## What is on the page, and what is not

One line, in the house style, no verdict words, the odds beside the number:

> Out 18 days · back within a fortnight 48% · dead weeks cost 1.58 · expected
> −1.76 (or −3.52 if he is not back by week 4)

It carries **no return date**. `availability.md` §2 found a timeline word in 0
of 6,987 Out runs, and ESPN gives no date at all for basketball. A date would be
a fabrication with three weeks of spread behind it. "Half of them are back
inside a fortnight" is a true sentence; "back on 14 December" is not.

It appears on the week page's What if section, on the trades page under the
finish, and in the season page's Stashes table. It appears in the `what_if`,
`week_report`, `season_report` and `judge_trade` MCP tools. It does not appear
for a healthy man, and a stash under the bar still appears with its number and
its odds, which is the owner's rule.

---

## §0. The calibrations, before and after

Re-run whole, the way `spread_revision.md` did it.

### The honest headline first: **on this database the three records cannot see
this change at all, and that is a fact about the database.**

`player_status_snapshots` holds **1,095 rows, all season 2027, all from one
pass on 2026-09-23**. The listener only ever runs for the season in progress,
so a replay of 2019–2026 reads no injury status for anybody — `build_players`
calls every man in every one of those seasons fit, and a rule about ruled-out
men therefore never fires. The three records below came back with every
published digit where it was.

That is a **guard, not a calibration**. It says the change is inert on
everything already measured — nothing about a healthy roster moved, no
constant drifted, no ranking shifted. It does not say the rule is right, and
nothing here could: there is no stored season with a stored status to test it
against. The measurement that stands behind the rule is `stashes.md`'s, and the
first real test of it will be a live morning.

### 1. The projected standings: identical

| | before | after |
|---|---|---|
| overall score (lower is better; a coin is 0.2500) | 0.2179 | 0.2179 |
| right side of this week's matchup | 0.584 over 5,320 team-weeks | 0.584 |
| final record off by, made at halfway | 7.16 of 171 | 7.16 |

Every line of the run's output is byte-identical apart from its own wall time.

### 2. Trades: identical

| | before | after |
|---|---|---|
| picked the better side, 55 deals, 30-day window | 25 of 55 | 25 of 55 |
| rank correlation over 110 sides | −0.02 | −0.02 |
| what a man is worth a week, against what he did | +0.39 over 174 men | +0.39 |

Byte-identical apart from the wall time in its own provenance line.

### 3. Pickups, the 2026 replay: identical

<!-- BACKTEST -->

### What moved instead

Nothing in the three records. What moved is the number the study said was
wrong, and §7 of `stashes.md` is where that is scored.

---

## §7 re-scored: the engine's error on 2026's ninety-one stashes

<!-- RESCORE -->

---

## Not done, and named so it is not forgotten

**The Week page's wire chooser still shows the best forty and has no search.**
The engine will now judge any man a caller names — `evaluated_wire` keeps a
named man whatever he ranks, which it did not before and which is why the
route used to answer "Brandon Miller is not a free agent" — but the page's own
list is `/trades/pool`'s top forty by value, and a fringe stash is not in it.
That is not about the injury: an OUT man's value is return-weighted now, so a
good player out a fortnight ranks where he should. It is that a manager cannot
ask about a man the page has not listed. A search box on that list is the fix
and it is not in this change.

**The first add is still free.** `stashes.md` §3 of "the settings gates" names
it: a stash spends one of the seven adds a matchup period allows and then
holds a place that cannot be streamed, so it costs the lane twice. The 0.38
prices the second cost; nothing prices the first.

**Doubtful and Questionable are untouched.** The availability study's play
rates are a separate change and folding them in here would have mixed two
measurements under one declaration.

## Decisions

1. **The prior is a shipped table, not a live query.** It is a measurement, and
   a product that re-measured it every morning would drift. `PRIOR_SOURCE` and
   `PRIOR_SAMPLE` travel with it so a page can say what it rests on.
2. **The prior and the ramp are not per-league calibration keys.** They are
   facts about the NBA, measured over eleven thousand absences across the
   league's whole eight-season history; the league they were measured on
   contributes no more than any other league's would. The **injured-reserve
   slot is** a setting, and it is read from the stored row everywhere.
   (`intake.md`'s rule, applied by saying which side of it each number is on.)
3. **The week is untouched.** `playable_days` still counts an OUT man for no
   games tonight and `startable` still seats nobody, because a man OUT tonight
   is out tonight. The prior belongs to the season horizon, which is the
   distinction `availability.md` §1c exists to prevent anybody from blurring.
   `RosteredPlayer` carries both counts: `game_days` for the seating,
   `season_games` for everything that plans past this week.
4. **Two counts, one place each.** Every rest-of-season caller reads
   `season_games`, and `judge.weekly_lines` is the single place the season
   charge counts games — so the week report's season half, the season report,
   the trade evaluator, the projected standings, the what-if and the MCP tools
   all get the rule from one line.
5. **A trade's playoff window counts the absence from today.** A man a
   fortnight out in January is not a man out since March, so `build_players`
   grew a `today` that defaults to the window's first day and is passed
   explicitly by the two callers whose window starts later.
6. **The dead cost is the only new term.** `judge` is not touched, the hurdle
   is not touched, and the stash block is built around the judgement's own net
   rather than replacing it.
7. **The second arm is the prior asked again, not a hand-made conditional.**
   The curve is already "given he is still out at N", so the branch where he is
   still out in four weeks is the row `N + 28`. No renormalisation, no new
   assumption.
8. **A man with no stored game at all reads as one day out**, the prior's most
   optimistic row, and `days_out` comes back None so a caller can say the
   number was not measured. A man with rows but none played is counted from the
   day before his first missed game, which is the same stand-in
   `scripts/stashes.py` Decision 3 uses.
9. **The season lane's gate stopped asking for a date.** `_stashes` wanted an
   ESPN return date inside `STASH_WEEKS` and so returned `()` on every call it
   has ever had. It now asks the prior how long the place is expected to stand
   empty, which is what the date was standing in for; when a date does arrive,
   the date wins.
10. **`section_seven` of `scripts/stashes.py` scores the rule off the study's
    own instrument**, not off `build_players`, because a 2026 replay has no
    stored status and `build_players` would call every one of those men fit.
    The days out are the box scores' — which is the count the prior is measured
    against in the first place.
