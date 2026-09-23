# The projected record: where every team finishes, and how well it knows

**League:** Full Court Press (ESPN 3853870), nine-category H2H
**Written:** 2026-09-22, alongside the build
**Status:** built (`app/inseason/projected.py`, `app/api/projected.py`,
`scripts/projected.py`, `scripts/projected_calibration.py`, the Standings and
This week pages, the Week page's "Rest of season" section, the digest's
Standing line). The calibration below is the run of 2026-09-22.
**Companions:** [`pickups.md`](pickups.md) §4.4-4.5 (the one-team projected
record), [`trades.md`](trades.md) §1 and §7 (each side's record, and how a
forecast's own record is published), [`site.md`](site.md),
[`jobs.md`](jobs.md), [`in_season_pages.md`](in_season_pages.md)

---

## 0. The answer, up front: it is overconfident, and the page says so

The forecast was replayed against 2026 from thirty-eight mornings -- the first
day and the midpoint of each of the nineteen regular-season weeks -- with only
what was on record that day. It made 47,880 per-category calls about every
week still to play. **It is clearly overconfident.**

| it said | it happened | calls |
|---|---|---|
| 5% | 19% | 3,872 |
| 15% | 30% | 4,317 |
| 25% | 37% | 4,921 |
| 35% | 41% | 5,182 |
| 45% | 48% | 5,648 |
| 55% | 52% | 5,648 |
| 65% | 59% | 5,182 |
| 75% | 63% | 4,921 |
| 85% | 71% | 4,317 |
| 95% | 81% | 3,872 |

Every row is pulled toward the middle, in both directions, at every distance.
The Brier score is **0.2288** where a forecast that said "coin" to everything
scores 0.2500. So: it knows which side is better and does not know how much
better. That is the headline, and it is on the page in those words.

**It is much better about the week in front of it.** Brier by how far ahead
the week is: 0.173 for the week being played, 0.216 for the next, 0.224,
0.224, 0.225, and then flat at about 0.24 from five weeks out -- barely better
than a coin. The matchup-winner hit rate does the same, over 5,320 team-weeks
(both sides of each matchup):

| weeks ahead | 0 | 1 | 2 | 3 | 4 | 5+ |
|---|---|---|---|---|---|---|
| hit rate | 0.684 | 0.635 | 0.605 | 0.585 | 0.581 | ~0.55 |

**The record it projects is worth more than the chances behind it.** Mean
absolute error of a team's projected final category record, of the 171 a
nineteen-week season contests:

| made at | mean error | worst |
|---|---|---|
| the quarter mark (period 5) | **8.7** | 16.6 |
| the halfway mark (period 10) | **7.4** | 13.6 |
| the three-quarter mark (period 14) | **4.9** | 12.2 |

Seven and a half categories of 171 is a bit over four percent, and about four
tenths of a category for each week still to be played. The one-line note on
every page is the halfway figure, from
`app.inseason.projected_calibration.RECORD_ERROR["half"]` with a guard test on
the sentence.

**The playoff odds are honest at the ends and poor in the middle.**

| it said | it happened | teams |
|---|---|---|
| 2% | 8% | 126 |
| 16% | 25% | 36 |
| 25% | 32% | 53 |
| 35% | 45% | 33 |
| 45% | 38% | 37 |
| 55% | **30%** | 23 |
| 65% | 46% | 26 |
| 75% | 68% | 25 |
| 86% | 67% | 18 |
| 98% | 99% | 155 |

Teams given better than 90% made it 99% of the time; teams given 50-60% made
it 30%. That middle band is exactly the band a manager in a fight actually
reads, and it is the weakest part of the whole thing.

### The proposal, priced and not applied

The variance model was **not** tuned on this run, and nothing in the shipped
code was changed after seeing it. That is the rule the trade calibration set
(`docs/trades.md` §7) and it is kept here: a model fitted on the run that
scores it has not been scored.

The diagnostic is in the script (`--sigma-scale`, which widens every weekly
spread by a factor and is never shipped). Re-running the same thirty-eight
mornings:

| spread widened by | Brier | 15% band happened | 35% | 65% | 85% |
|---|---|---|---|---|---|
| 1.0 (shipped) | 0.2288 | 30% | 41% | 59% | 71% |
| sqrt(2) | 0.2202 | 22% | 39% | 62% | 78% |
| **2.0** | **0.2179** | **15%** | **36%** | **65%** | **85%** |

At **two**, the table is calibrated almost everywhere. sqrt(2) is the
principled number -- the spread of the difference between two independent team
totals -- and it closes about half the gap; two closes nearly all of it, which
says the remaining half is something else (the categories are not independent
of each other within a week, and a roster's own week-to-week form varies more
than the league's cross-sectional spread suggests).

**Proposed, for a separate decision:** widen the sigma in
`app.pickups.stream.head_to_head` by a factor between sqrt(2) and 2. It is not
this module's to change, because that one function is also what the pickup
judgement, the streaming hurdle (`STREAM_HURDLE`), the bid sizing and the
trade evaluator are priced on. Moving it moves every one of those numbers and
needs their calibrations re-run. `app.inseason.projected_calibration.WIDENED`
records the three Brier scores so the proposal does not have to be re-measured
to be discussed, and a test asserts the shipped scale is still 1.0.

### The day-80 sense check

Projecting the whole 2026 league on day 80 (period 12 of 19, eight weeks left)
against how 2026 really finished:

| projected | team | projected cats | real cats | err | real place |
|---|---|---|---|---|---|
| 1 | Brighton Bears | 106.9-64.1 | 90.0-81.0 | **+16.9** | 4 |
| 2 | Through The Wire | 102.9-68.1 | 107.5-63.5 | -4.6 | 1 |
| 3 | The Infirmary | 91.5-79.5 | 95.0-76.0 | -3.5 | 2 |
| 4 | Masters of their Domains | 91.7-79.3 | 88.5-82.5 | +3.2 | 6 |
| 5 | Fantastic 5 | 93.0-78.0 | 99.5-71.5 | -6.5 | 5 |
| 6 | Foxes ShutUpNDribble | 82.4-88.6 | 95.0-76.0 | -12.6 | 3 |
| 7 | LeBron's Load Management LLC | 84.0-87.0 | 72.5-98.5 | +11.5 | 12 |
| 8 | BC KO | 79.8-91.2 | 76.0-95.0 | +3.8 | 11 |
| 9 | Uncle Dennis's Phone | 80.8-90.2 | 83.0-88.0 | -2.2 | 9 |
| 10 | Optimize the MVPs | 84.0-87.0 | 89.0-82.0 | -5.0 | 7 |
| 11 | Fast and Curryous | 80.1-90.9 | 79.0-92.0 | +1.1 | 10 |
| 12 | Ben's Need Some VC | 76.6-94.4 | 70.0-101.0 | +6.6 | 13 |
| 13 | Chat GTP inspired | 76.0-95.0 | 86.0-85.0 | -10.0 | 8 |
| 14 | Brockley Heat | 67.3-103.7 | 66.0-105.0 | +1.3 | 14 |

Mean absolute error 6.3 categories; mean place error 2.0; **six of the seven
teams it put in the playoff places really made it**. The two big misses are
the two a manager would notice: Brighton Bears, whose roster on day 80 was
much better than its season turned out to be, and LeBron's Load Management,
which it flattered by eleven categories. Note this table is the *shipped*
call, which reads the league's weekly spreads including 2026's own later weeks
(see §4); the calibration's own numbers are the leak-free ones.

---

## 1. Why this exists

Every piece of it was already here, for one team at a time. The pickup
judgement carries "the projected record with the move and without it"
(`app.pickups.judge.Judgement.record_without`, docs/pickups.md §4.4); the
trade evaluator carries each side's record with and without the deal
(docs/trades.md §1). Both of them play the rest of the season against a
**league-average** opponent, because that is all a one-team question needs.

The question a manager actually asks -- *where am I going to finish* -- needs
the real opponent each week, which means every other team's roster, which
means the whole league at once. Basketball Monster sells exactly this. It is
also the engine the trade proposal's paid league view and the co-manager's
"where do I finish" will call (docs/product.md, "Trades between managers": the
paid tier sees "what the deal does to the standings and the other
contenders"), so it is built league-wide and stored once rather than per
reader.

---

## 2. The engine: `app/inseason/projected.py`

Pure over loaded state, like the rest of the recommender. One entry point:

```python
project_standings(session, league_season, today, *,
                  distributions=None, tilt=True,
                  n_sims=N_SIMS, seed=SEED, unavailable=None) -> Projection
```

### The horizon

`app.pickups.judge.horizon`, unchanged and reused: the rest of the regular
season while the regular season lasts, the playoff rounds once it is over.
Every matchup period inside that window **whose days are recorded** is a week
to project. The pairings come from `matchups`, which ESPN stores for the whole
season in advance -- 152 rows for 2027 before a game was played -- so who
plays whom in week 15 is a fact, not a forecast.

### The weekly line

For each team and each remaining week: today's roster, that week's NBA
schedule, and the daily-lineup solve. The solve is
`app.pickups.stream.seat`, imported rather than reimplemented -- ESPN starts
ten men a day, so a game on a day the lineup is already full is a start going
nowhere, and who sits on a full day is decided by the per-game weight the
draft board and the week report already order by. The week page, the morning
lineup and this cannot disagree about who starts, because there is one seating
rule in the codebase.

The per-game rate is `app.pickups.projection.per_game_line` as of today, which
is where the knowable blend, the minutes tilt and the availability rules
already live. Games are counted by `app.pickups.state.build_players` over the
**whole** remaining horizon in one call and then sliced per week, which is
what keeps the cost down (§6). Injured and injured-reserve men stand as
`state` has them today; the season discount (`ESPN_AVAILABILITY`) is applied
only where `projection.py` applies it, which is the rest-of-season line and
not a week's.

For the **period in play**, the line starts from what has already been posted
-- `load_team_week`'s `my_totals`, whose live/replay rule decides whether that
is ESPN's running tally or a sum of the started box scores -- and adds only the
days from today on. So this week's probabilities move as the week is played,
and the rehearsal's finding 1 (a week's posted totals being the whole
period's) cannot come back: the boundary is exclusive at both ends and the
tests pin it.

### The head to head

`app.pickups.stream.head_to_head`, imported. For each category:

    P = Phi( (total_mine - total_theirs) / (spread * sqrt(days_left / period_days)) )

`spread` is `app.draft.targets.CategoryDistribution.spread`, the standard
deviation of one team's total in that category over a period of the ordinary
length, measured on this league's own results. Turnovers are inverted. FG% and
FT% are rates rebuilt from the projected makes and attempts
(`CategoryLine.totals`), never averaged across a roster or a week. With no
days left the category is settled outright, and a level one is a coin --
which is exactly how `app.scoring.league.category_record` counts a tie, half
to each side, so the forecast and the record it is scored against count a tie
the same way.

**The variance model is the one judgement call in this module**, and §0 shows
it is the thing that is wrong. It is stated in full in the module docstring.
The two choices in it:

1. The spread is **one team's**, not the spread of the difference between
   two. Keeping it is what stops this page and the week page disagreeing
   about what a week is worth. §0 prices the alternative.
2. The nine categories are drawn **independently** in the simulation. They
   are not: a roster with four games on Sunday gains in most of them at once.
   So the spread of simulated outcomes is narrower than the truth.

### The record

Per team: the nine probabilities and their sum for each remaining week; the
expected record over the rest of the regular season (the sums, and nine minus
them for each contested week -- a bye contributes nothing either way); the
banked record from `app.pickups.judge.banked_record`, which counts ESPN's own
per-category results in weeks already finished; and their sum, the projected
final record. **banked + expected = projected** is asserted for every team in
the tests, because those are the three numbers a manager reads together.

### The finish distribution

A Monte Carlo of `N_SIMS` (10,000) seasons from a fixed `SEED`. Each season
plays every remaining matchup once, adds the banked record, and orders the
table.

The draw per matchup is **one uniform, not nine**. The number of categories
one side takes is Poisson-binomial in the nine probabilities, so the exact
ten-vector is folded out once per matchup and sampled by its cumulative form.
That is the same distribution sampled nine times more cheaply, and it is what
keeps ten thousand seasons to about a second.

The table is ordered by **matchups won, then fewest lost, then categories
won** -- the order `app.api.leagues.get_standings` and `app.digest._place_of`
both use, and whose first term is this league's own
`raw_settings.schedule.playoffSeedingRule`, `H2H_RECORD`. ESPN does not
publish what breaks a tie beyond that, so an exact tie on all three is broken
by a number drawn per team per simulated season: ordering by team id would
hand the same team the better seed in all ten thousand.

Out of that: `finishes` (P of each place, summing to one), `playoff_odds` (the
top `playoff_team_count` places) and `bye_odds`. The byes are derived rather
than configured -- a bracket is played in rounds of two, so a field that is not
a power of two starts with byes and ESPN gives them to the top seeds
(`bye_seats`: seven of fourteen is one bye, eight of sixteen is none).

### The playoff bracket, and why it is usually not projected

ESPN stores a full bracket for a season already played, because it happened.
For 2026 that is periods 20-22 with real pairings. For **2027, the season in
progress**, the playoff periods carry `first_scoring_period = NULL` and
`final_scoring_period = 0`, and their pairings depend on seeding nobody has
yet earned.

So: during the regular season only the regular season is projected;
`playoffs_projected` is False and `playoff_note` says why, and the playoff
odds come from the simulated final table rather than a bracket. Once the
regular season is over, `horizon` moves to the playoff periods, their stored
pairings are the real ones, and they are projected like any other week.
Projecting a played season's stored bracket from the middle of its regular
season would be pure hindsight, and this is how that is refused.

---

## 3. The payload, the store and the routes

`Projection` carries, per team: the banked record, the banked matchup record,
each remaining week (period, days left, whether it is in play, the opponent,
the nine probabilities, the expected wins, both sides' projected counts), the
finish distribution, the playoff odds and the bye odds. And at the top: the
scoring period and date it is `as_of`, the periods projected, the tiebreak in
words, `n_sims` and `seed`, a `source_note` naming what the weekly spreads
were measured on, a `basis` naming the day the rosters and box scores are
from, and `calibration_note`, which is §0 in one paragraph.

**The store is a table of its own, `league_reports`**, not a row in
`team_reports`. `team_reports` is keyed on `team_id` with a foreign key to
`teams` and a unique index on (team, kind, period); a league projection
belongs to no team, and giving it one would mean either fourteen copies of the
same payload or a nullable foreign key on a table whose whole shape says
otherwise. `league_reports` is (league_season_id, kind, scoring_period,
built_at, payload) with the same freshness rule `app.reports.fresh` uses: a
row counts as today's only if it is for today's scoring period **and** was
built on today's date.

A new job kind, `project_standings`, builds and stores it once a morning,
after the `status_pass` and **before** the per-team precomputes.

**The seam that is deliberately not taken yet.** The per-team precomputes
build their "projected record with and without" against a league-average
opponent (`app.pickups.judge`). They could read the stored league projection
for the without side and get the real opponent each week instead. That would
change every pickup and trade number on the site, so it is a decision of its
own with its own backtest; this work does not touch them. The seam is named
here and in `app/inseason/projected.py` so the next person finds it.

Routes:

```
GET /leagues/{league_id}/seasons/{season}/projected            (league member)
GET /leagues/{league_id}/seasons/{season}/teams/{id}/projected (team plan)
```

Both answer from the stored row when the day asked for is today's and the row
was built today, and build live otherwise, exactly as the pickup routes do.
`?today=` is carried. The team route is the **league answer narrowed**, not a
second computation, so the Week page and the Standings page can never
disagree; it exists so a phone fetches one team's weeks rather than fourteen.

The league route is a member's because nothing in it is a plan: every team's
remaining schedule is on the Standings page already, and a projection only one
manager could see would be worth less to everyone. The team route is that
team's manager and the paid tier, the same scope as the Week page it is drawn
on.

---

## 4. No look-ahead, and the one place it is not proved

Everything the engine reads is keyed on `today` or earlier: the roster from
the latest lineup day at or before it, the posted totals from this period's
days **before** it, the per-game rates from box scores before it, the NBA
schedule, and the matchup pairings, which are knowable in advance and are the
one legitimate fact about the future here. A test deletes the future from a
fixture -- box scores, lineup days, and the results and posted totals of weeks
not yet finished -- and asserts the payload is identical.

That test runs with `today` on the **first day of a period**, deliberately. In
the middle of one, deleting the future also deletes what the period has posted
so far, and `app.pickups.state.is_live` then flips the report from a replay to
a live morning. That is a real and wanted difference in where the posted
totals come from, not a leak, and the test says so in place.

**The one leak, inherited rather than added.**
`app.draft.targets.category_distributions` measures the league's weekly
spreads from every played season of this league's size, and on a season being
**replayed** that includes weeks after `today`. On a live season the rows do
not exist yet, so there is nothing to leak; on a replayed day (`?today=` a
past day, or the day-80 table in §0) there is. It is the same shape as the bid
prices, which `docs/inseason_rehearsal.md` names under "what could not be
verified".

Callers that need a clean replay pass their own basis, and the calibration
does: `category_distributions(..., before=season, adjust_for_era=False)`, so
the spreads come from strictly earlier seasons with no trend brought forward.
Every number in §0 is on that basis. Fixing it in the product means giving
`category_distributions` a day bound, which is a change to a function the
draft room and the whole recommender share, and belongs with the variance
change rather than beside it.

---

## 5. The calibration: `scripts/projected_calibration.py`

```
python scripts/projected_calibration.py --season 2026 --sims 2000 --json out.json
python scripts/projected_calibration.py --season 2026 --sigma-scale 2.0   # diagnostic
```

Read-only. For each regular-season matchup period it rebuilds the whole
projection twice -- the morning the period began and again at its midpoint --
and scores every per-category probability it made about every week still to
play against ESPN's own result for that category (a win one, a tie a half, a
loss nothing, which is `category_record`'s convention).

Three things make the replay honest, and each is a leak if it is left out:

1. The spreads are read with `before=season` and no era adjustment (§4).
2. Availability comes from **the NBA's own injury reports** as they stood at
   ten o'clock Eastern that morning (`app.injuries.statuses_as_of`, the
   point-in-time rule of docs/injuries.md). A played season has no listener
   snapshots -- the listener only ever runs for the season in progress -- so
   without this every man reads as fit. A report line is a statement about one
   game, so a man listed Out loses exactly that scoring period; the engine
   takes them through its `unavailable` argument.
3. Everything else is the engine's own `today` bound.

Outputs: the Brier score overall and by how far ahead the week was; the
reliability table; the matchup-winner hit rate by the same split; the record
error at the quarter, half and three-quarter marks; and the playoff-odds
reliability. All of §0.

---

## 6. Timing

On the stored 2026 season, fourteen teams and eight remaining weeks
(day 80), on the local Docker Postgres:

| | seconds |
|---|---|
| cold, in a fresh session | **3.6** |
| warm, second call in the same session | **2.4** |

Of the cold time, about 1.2 s is `category_distributions` -- the 3.79 s the
rehearsal measured is for a season with more results to average, and it is
called **once** here rather than once per team per report kind, which is what
`docs/inseason_rehearsal.md` finding 4 is about. The rest is one
`load_team_week` per team, one `build_players` over the whole horizon for the
league, one `per_game_line` per player (memoised on the day, so a man is
priced once however many weeks he appears in), the seating -- fourteen teams
times eight weeks times seven days -- and about a second of simulation.

Three seconds is well inside what the morning job needs, and no further
optimisation was done. The two things that would matter if it ever did not
fit: caching `category_distributions` across the morning (finding 4, which
would help every job and not just this one), and dropping `n_sims`, which
only affects the finish distribution and is linear.

---

## 7. What is on the pages

**Standings** gains two column groups -- the projected final record and the
playoff odds, with the seed odds on hover or tap -- and a toggle between "as it
stands" and "projected". Sortable like the rest of the table, with a footnote
naming the basis, the as-of day and the one-line record from §0.

**This week** (the league page) gains, for each matchup, the nine
probabilities as a compact strip and each side's expected categories, plus
"this week so far" once games have been played. It sits under the matchups and
above What changed, whose lines it does not touch.

**My team / Week** gains a "Rest of season" section under Today: the
week-by-week list with opponents and expected categories, the projected finish
and the odds, from the team route.

**The digest** fills the Standing line's marked `projected finish` slot from
the stored league report -- one line, the place and the projected record, and
"not built yet" when the morning job has not run.

Player names carry the shared card, as everywhere else on the site.

---

## 8. Decisions taken here

1. **The variance model is `stream.head_to_head`, unchanged.** A second model
   would disagree with the week page invisibly. §0 says it is wrong and
   prices the fix; the fix is a separate decision because four other numbers
   are priced on the same function.
2. **A `league_reports` table, not a scope on `team_reports`.** §3.
3. **The regular season only, during the regular season.** §2. A stored
   bracket from a played season is hindsight.
4. **The team route is the league answer narrowed.** Two computations would
   eventually give two answers.
5. **Both sides of every matchup are scored in the calibration.** A correct
   call on one side is a correct call on the other, so the hit rate is
   unchanged and the sample is stated as team-weeks rather than matchups.
6. **Ties are half.** The normal model gives a tie no mass, and a level
   category reads 0.5, which is what `category_record` scores a tie at. The
   two therefore agree in the limit rather than by a special case.
7. **The tiebreak beyond the first three terms is random, per simulated
   season.** ESPN does not publish it and a deterministic fallback would bias
   every draw the same way.
8. **The per-team precomputes were not changed.** §3, the named seam.

## 9. Not done

- The playoff **bracket** itself is never simulated round by round, even once
  the pairings are known: the odds are the table's, and once the playoffs
  start the remaining rounds are projected as ordinary weeks. A real bracket
  simulation is a different and larger thing.
- Nothing models a **future move**: the projection is today's roster held to
  the end of the season. A team with FAAB and seven adds a week will not
  finish where this says.
- **Strength of schedule** is implicit in the weekly head-to-heads and is
  nowhere summarised. "Your remaining schedule is the third hardest" is a one
  line addition on top of what is already computed.
- The **`category_distributions` day bound** (§4).
