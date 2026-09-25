# The projected record: where every team finishes, and how well it knows

**League:** Full Court Press (ESPN 3853870), nine-category H2H
**Written:** 2026-09-22, alongside the build
**Status:** built (`app/inseason/projected.py`, `app/api/projected.py`,
`scripts/projected.py`, `scripts/projected_calibration.py`, the Standings and
This week pages, the Week page's "Rest of season" section, the digest's
Standing line). The calibration below is the run of 2026-09-24, the first in
which a replayed morning could read who was hurt ([`replay_status.md`](replay_status.md)).
**Companions:** [`pickups.md`](pickups.md) §4.4-4.5 (the one-team projected
record), [`trades.md`](trades.md) §1 and §7 (each side's record, and how a
forecast's own record is published), [`site.md`](site.md),
[`jobs.md`](jobs.md), [`in_season_pages.md`](in_season_pages.md)

---

## 0. The answer, up front: it is about as sure as it ought to be, except at the ends

### Ranking revision (R6), declared 2026-09-25 before the engine was changed

**The fault.** This league is ESPN's **Head-to-Head Each Category**
(`league_seasons.scoring_type` = `raw_settings.scoring.scoringType` =
`H2H_CATEGORY`, every season 2019-2027). ESPN ranks that format on the
**category record**; there is no matchup record in it at all, which is why
ESPN reports none. Everything here ordered the table by **matchups won, then
fewest lost, then categories won** -- the simulated table, and so the seeding,
the projected place, `finishes`, the playoff and bye odds, and `final_table`,
which is who the calibration below counts as having made the playoffs. The
owner, 2026-09-25: "this is a categories league. we don't care about matchups."

**What ESPN's order is, measured before this was written** (read-only, the
dev database, every stored season against ESPN's own `teams.standing`):

* Ordered by **category win share**, `(W + T/2) / (W + L + T)`, the table is
  ESPN's in every season 2019-2026 except at exact ties of share and at
  2023's places 2 and 3. Matchups won do not predict it (2025, regular season:
  Thibs Dust's nine matchup wins sit fourth, ahead of Foxes' ten; Fantastic
  5's six sit eighth, ahead of The Infirmary's eight and FEAR THE BEARD's
  seven).
* **Exact ties of share: nine, in six seasons, and all nine go to the team
  with the better category record against the other tied team in their
  regular-season meetings** (2020: 10-8 and 10-8; 2021: 13-4 and 11-7; 2022:
  6-3; 2024: 6-3 and 5-4; 2025: 9-7; 2026: 10-8). That is the league's own
  setting read literally: `raw_settings.schedule.playoffSeedingRule` is
  `H2H_RECORD`, ESPN's "head-to-head record" seeding tiebreaker (2025's
  `INTRA_DIVISION_RECORD` is the same record in a one-division league, and
  its one tie still went head to head). **Categories won alone, the tiebreak
  first proposed for this revision, gets two of the nine right, three wrong,
  and cannot separate the other four** -- so it stays in the rule only
  behind the head-to-head term.
* **2023's places 2 and 3 are not a tie** (Team Stylios .6142, Allen Iverson
  Team .6080). 2023 had two divisions, and Allen Iverson Team led USA: ESPN
  seeds the division leaders first. It is the only season of the four with
  two divisions where a division's leader was not already in the top two by
  share (2019, 2022 and 2024 agree). Not modelled: 2026 and 2027 are one
  division, and one season is too little to build a rule on. The standings
  route reports ESPN's own order for a played season and flags the two places.

**The rule, from here on** (`app/scoring/ranking.py`, gated on the scoring
type): for `H2H_CATEGORY`, **category win share, then the tied teams'
category record against each other, then categories won, then fewest lost**;
the simulation breaks anything left with a draw per team per simulated
season, as before. `H2H_MOST_CATEGORIES` and points leagues keep the matchup
order. Roto is out of scope.

**What changes.** The order of the simulated table, and so the seeding:
`finishes`, `playoff_odds`, `bye_odds`, the projection's team order and every
place printed from it (the Standings page, the Week page, the digest, the
what-if's Finish block, the MCP tools), and `final_table`.

**What is expected to move.** The playoff-odds reliability table, for two
reasons at once: the forecast orders its simulated table differently, and the
field it is scored against -- `final_table` at the end of the season -- is
reordered too. So the playoff odds are run three ways: (A) the engine as it
stands, scored against its own settled table, which reproduces the published
figures; (B) the engine as it stands, scored against **ESPN's stored
standing** (`--field espn`), which is who really made the playoffs; (C) the
new engine against its own settled table, and again against ESPN's. A minus B
is how much of the published table was scored against the wrong field; B
minus C is what the new order itself does. The lock study's classification
(lock / race / out) reads the playoff odds, so every figure in
[`stash_locks.md`](stash_locks.md) may move: the lock count and share, the
seeding stake, the lock cost against 0.38, the agreement on the sign, and the
counterfactual's odds and seed moves.

**What is expected not to move, and will be checked byte for byte.** The
per-category probabilities and the projected category record are computed
before the simulation and do not read the order: the Brier score (0.2184)
overall and by weeks ahead, the category reliability table, the
matchup-winner hit rate (0.583), and the record error at the quarter, half
and three-quarter marks (8.1, 6.3, 5.2). The simulation draws its random
numbers in the same sequence, so the mean simulated matchup record
(`projected_matchups`) is unchanged too.

**Nothing is tuned.** `SPREAD_SCALE` (2.0), `N_SIMS`, `SEED`, the lock's
0.95 and the race's 0.25 stay as they are, whatever the runs say, and both
columns are published whichever way they fall.

**The commands**, on the local dev database, read-only, full runs (the
projected calibration took about forty seconds on 2026-09-24; the lock study
1,114 seconds, both well inside an hour):

```
# (A) and (B), on the code as it stands
PYTHONPATH=. python scripts/projected_calibration.py --season 2026 \
    --json docs/runs/2026-09-25-projected-calibration-before.json \
    > docs/runs/2026-09-25-projected-calibration-before.txt
PYTHONPATH=. python scripts/projected_calibration.py --season 2026 --field espn \
    --json docs/runs/2026-09-25-projected-calibration-before-espn.json \
    > docs/runs/2026-09-25-projected-calibration-before-espn.txt
PYTHONPATH=. python scripts/stash_locks.py > docs/runs/2026-09-25-stash-locks-before.txt
# then the engine change, then (C): the same three with -after in the names
```

### Revision R5, applied 2026-09-24: a replayed morning reads the NBA's own injury report

**What changed.** R4 below came back byte-identical, and the reason was the
*source*, not the rule: a replayed morning read its injury status from
`player_status_snapshots`, which the listener writes only for the season in
progress, so every man in a replayed 2026 was counted fit. The engine now
applies a declared source order ([`replay_status.md`](replay_status.md)):
ESPN's snapshot on a morning the listener had already run for, and otherwise
**the NBA's own official report as of ten o'clock Eastern that morning**, read
point-in-time through `app/injuries.py`, with the league's five words mapped
onto ESPN's. `Out` alone is ruled out, as before. Nothing else moved.

**This is the first run in which the ruled-out branch of the games count fires
at all**, and it moved both ways.

| | before | after |
|---|---|---|
| **overall score** (lower is better; a coin is 0.2500) | **0.2179** | **0.2184** |
| right side of this week's matchup | 0.584 over 5,320 team-weeks | **0.583** |
| final record off by, made at the quarter | 8.4 of 171 | **8.1** |
| final record off by, made at halfway | 7.2 of 171 | **6.3** |
| final record off by, made at three-quarters | 4.8 of 171 | **5.2** |
| what it called above 90% happened | 80.4% (n = 317) | **83.5%** (n = 370) |
| what it called under 10% happened | 19.6% (n = 317) | **16.5%** (n = 370) |

**The headline got slightly worse and the line a manager reads got better.**
The Brier rose five ten-thousandths and the matchup-winner rate fell a
thousandth — both inside what one run can separate from noise, and both
published because they are what the run says. The projected final record at
halfway, which is the figure this document leads with and the one `SHORT_NOTE`
prints, improved from 7.2 categories to **6.3**; the three-quarter mark went
the other way, 4.8 to 5.2.

The two ends are the interesting part. The model reaches them more often — 370
calls above 0.9 against 317 — and is *better* calibrated when it does. Knowing
who was Out sharpens the extremes and costs a little in the middle, which is
the shape to expect from a term that speaks about one rostered man in ten
(`replay_status.md` §2).

**One thing did not double up.** `scripts/projected_calibration.py` already
patched around the blindfold itself, reading `statuses_as_of` for the same
morning and passing the days as `unavailable=`. That patch is now redundant
rather than additive — an Out man with no return date loses *every* remaining
day in `playable_days`, which contains the single game day the patch blocks —
and it was left in place deliberately, because removing it would be a second
change inside the run that scores the first.

**The constants follow the run.** `app/inseason/projected_calibration.py` and
its guard tests are updated to these figures; `SHORT_NOTE` now says 6.3.
`WIDENED` is deliberately *not* updated: those three rows are the
status-blind run of 2026-09-22 and are the evidence the owner chose the
spread factor on, so they stay as that run gave them, and the guard test now
holds them to the shipped score within a thousandth rather than exactly.

### Revision R4, applied 2026-09-24: an OUT man is counted for the games he is expected to play

**What changed.** `app.pickups.state.playable_days` gave a man ESPN had ruled
out no games at all for the rest of the season, because it waited for an
`expected_return_date` ESPN's basketball API has no field for. Every
rest-of-season caller — this engine included, through
`app.pickups.judge.weekly_lines` — now counts him for his *expected* games:
each remaining game day weighted by the chance he is back by it, from the
box-score return prior of [`stashes.md`](stashes.md) §2a, times the ramp of
its §3. The rule was declared in [`stash_mode.md`](stash_mode.md) before any
calibration was re-run and was not tuned afterwards. No constant moved.

**This record came back byte-identical, and the reason matters.**
`player_status_snapshots` holds the season in progress only — 1,095 rows, all
2027 — so replaying 2026 reads no injury status for anybody and every man in
it is projected as fit. Every line of the run's output is the same as the run
before the change, wall time aside (39s, then 37s). That is a **guard rather
than a calibration**: it says nothing about a healthy roster moved, which is
what the change claims, and it cannot say whether the rule is right. The
measurement standing behind the rule is `stashes.md`'s, and §7 of it re-scored
is in `stash_mode.md`.

### Revision R3, applied 2026-09-23: the spread was widened by two

**What changed.** `app.pickups.stream.SPREAD_SCALE`, a new constant, is 2.0,
and `head_to_head` multiplies every weekly spread by it. The measured spread
is one *team's* total over a period; what decides a category is the
*difference* between two of them, and the model had been using the first for
the second.

**Why, and declared when.** The run of 2026-09-22, written up below as it
stood, found the forecast badly overconfident and priced the fix at three
factors without making it. Patrick chose **2.0** on **2026-09-23**, before
this run started and on the evidence already published: sqrt(2) is the
principled number for two *independent* totals and closes about half the gap,
2.0 closes nearly all of it, and the rest is within-week dependence between
the categories and a roster's own form. The factor was not moved afterwards,
and no hurdle, no `OPENED_PLACE`, no `TYPICAL_PICKUP` and no part of the bid
model was touched. That is the rule of §7 of `docs/trades.md` kept: nothing is
tuned on the run that scores it.

**Before and after.** Brier **0.2288 -> 0.2179**. The two rows a manager
feels: what it called at 15% used to happen 30% of the time and now happens
15%; what it called at 85% used to happen 71% and now happens 85%. The
matchup-winner hit rate barely moved, 0.580 -> 0.584, which is the right
shape -- widening a spread changes how sure the forecast is, not which side it
points at. The projected-record error improved a little at all three marks,
7.4 -> 7.2 at the halfway mark.

**What it is not.** This run is **by construction the same evidence** as the
widened row of the table the last run published, not a new test of it: the
same season, the same thirty-eight checkpoints, the same spreads, the same
scoring. It reproduces 0.2179 exactly, which is the check that the constant
went where the diagnostic went and nowhere else. The out-of-sample question is
the trade calibration (`docs/trades.md` §7, six seasons) and the 2026 pickup
replay (`docs/pickups_backtest.md`); both were re-run whole on the same day.
The one-paragraph version for a manager is `docs/spread_revision.md`.

**Not applied to the draft.** `app/draft/optimizer.py` and
`app/draft/targets.py` use the same measured spreads for a different question
-- a whole season against the field, not a week against one known opponent --
and whether they want the same factor is its own measurement. They were not
changed.

---

The forecast was replayed against 2026 from thirty-eight mornings -- the first
day and the midpoint of each of the nineteen regular-season weeks -- with only
what was on record that day, including -- since R5 -- who the NBA's own injury
report had out that morning. It made 47,880 per-category calls about every
week still to play. **It lands about where it says it will, except at the two
ends, which it rarely reaches.**

| it said | it happened | calls | calls before R3 |
|---|---|---|---|
| 5% | 17% | 370 | 3,872 |
| 15% | 18% | 1,644 | 4,317 |
| 25% | 25% | 4,300 | 4,921 |
| 35% | 35% | 7,540 | 5,182 |
| 45% | 46% | 10,086 | 5,648 |
| 55% | 54% | 10,086 | 5,648 |
| 65% | 65% | 7,540 | 5,182 |
| 75% | 75% | 4,300 | 4,921 |
| 85% | 82% | 1,644 | 4,317 |
| 95% | 84% | 370 | 3,872 |

The Brier score is **0.2184** where a forecast that said "coin" to everything
scores 0.2500. The last column is the rest of the finding: the wide model puts
two thirds of its calls in the four middle bands and almost none past 90%, so
the rows that are still wrong are the rows it hardly ever writes. 370 calls of
47,880 sit above 90%, against 3,872 before.

**It is much better about the week in front of it.** Brier by how far ahead
the week is: 0.174 for the week being played, 0.211 for the next, 0.214,
0.215, 0.215, and then flat at about 0.23 from five weeks out -- barely better
than a coin. The matchup-winner hit rate does the same, over 5,320 team-weeks
(both sides of each matchup):

| weeks ahead | 0 | 1 | 2 | 3 | 4 | 5+ |
|---|---|---|---|---|---|---|
| hit rate | 0.688 | 0.635 | 0.605 | 0.571 | 0.581 | ~0.55 |

**The record it projects is worth more than the chances behind it.** Mean
absolute error of a team's projected final category record, of the 171 a
nineteen-week season contests:

| made at | mean error | worst | before R5 | before R3 |
|---|---|---|---|---|
| the quarter mark (period 5) | **8.1** | 14.2 | 8.4 | 8.7 |
| the halfway mark (period 10) | **6.3** | 15.1 | 7.2 | 7.4 |
| the three-quarter mark (period 14) | **5.2** | 12.5 | 4.8 | 4.9 |

Six categories of 171 is under four percent, and about three tenths of a
category for each week still to be played. The halfway mark is the best it has
been and the three-quarter mark the worst of the three runs, which is one
reason to read all three rather than the one the page prints. The one-line
note on every page is the halfway figure, from
`app.inseason.projected_calibration.RECORD_ERROR["half"]` with a guard test on
the sentence.

**The playoff odds are honest at the ends and poor in the middle.**

| it said | it happened | teams |
|---|---|---|
| 3% | 13% | 112 |
| 15% | 23% | 43 |
| 25% | 21% | 39 |
| 34% | 27% | 34 |
| 45% | 37% | 38 |
| 55% | **37%** | 38 |
| 65% | 58% | 40 |
| 75% | 74% | 35 |
| 85% | 82% | 34 |
| 98% | 100% | 119 |

Teams given better than 90% made it every time and the 70-90% bands are close
to honest; the 40-60% bands are the worst rows, at 37% apiece. The middle is
still where the error lives, and it is exactly the band a manager in a fight
actually reads.

### The proposal, applied 2026-09-23

The variance model was **not** tuned on the run that found the fault, and
nothing in the shipped code was changed until the owner had chosen a number
and said so. That is the rule the trade calibration set (`docs/trades.md` §7):
a model fitted on the run that scores it has not been scored.

What the run of 2026-09-22 priced, with the diagnostic in the script
(`--sigma-scale`, which since R3 multiplies **on top of** the shipped factor,
so the 1.0 row is now `--sigma-scale 0.5`):

| spread widened by | Brier | 15% band happened | 35% | 65% | 85% |
|---|---|---|---|---|---|
| 1.0 (shipped before R3) | 0.2288 | 30% | 41% | 59% | 71% |
| sqrt(2) | 0.2202 | 22% | 39% | 62% | 78% |
| **2.0 (shipped since R3)** | **0.2179** | **15%** | **36%** | **65%** | **85%** |

At **two**, the table is calibrated almost everywhere. sqrt(2) is the
principled number -- the spread of the difference between two independent team
totals -- and it closes about half the gap; two closes nearly all of it, which
says the remaining half is something else (the categories are not independent
of each other within a week, and a roster's own week-to-week form varies more
than the league's cross-sectional spread suggests). The owner took the number
that calibrates the table over the number the theory alone gives, knowing
which was which.

That is a change to `app.pickups.stream.head_to_head`, which is also what the
pickup judgement, the streaming hurdle (`STREAM_HURDLE`), the bid sizing and
the trade evaluator are priced on. Moving it moved every one of those numbers,
so all three calibrations were re-run whole on 2026-09-23 and republished
whichever way they fell.
`app.inseason.projected_calibration.WIDENED` keeps all three measured factors,
`SHIPPED_SCALE` records which one the product is, and a test holds those two
and `app.pickups.stream.SPREAD_SCALE` to the same number.

### The day-80 sense check

Projecting the whole 2026 league on day 80 (period 12 of 19, eight weeks left)
against how 2026 really finished:

| projected | team | projected cats | real cats | err | real place |
|---|---|---|---|---|---|
| 1 | Brighton Bears | 102.7-68.3 | 90.0-81.0 | **+12.7** | 4 |
| 2 | Through The Wire | 101.6-69.4 | 107.5-63.5 | -5.9 | 1 |
| 3 | The Infirmary | 91.4-79.6 | 95.0-76.0 | -3.6 | 2 |
| 4 | Masters of their Domains | 90.8-80.2 | 88.5-82.5 | +2.3 | 6 |
| 5 | Fantastic 5 | 93.0-78.0 | 99.5-71.5 | -6.5 | 5 |
| 6 | Foxes ShutUpNDribble | 83.8-87.2 | 95.0-76.0 | -11.2 | 3 |
| 7 | LeBron's Load Management LLC | 85.2-85.8 | 72.5-98.5 | **+12.7** | 12 |
| 8 | BC KO | 79.4-91.6 | 76.0-95.0 | +3.4 | 11 |
| 9 | Uncle Dennis's Phone | 81.8-89.2 | 83.0-88.0 | -1.2 | 9 |
| 10 | Fast and Curryous | 81.7-89.3 | 79.0-92.0 | +2.7 | 10 |
| 11 | Ben's Need Some VC | 77.4-93.6 | 70.0-101.0 | +7.4 | 13 |
| 12 | Optimize the MVPs | 81.8-89.2 | 89.0-82.0 | -7.2 | 7 |
| 13 | Chat GTP inspired | 77.3-93.7 | 86.0-85.0 | -8.7 | 8 |
| 14 | Brockley Heat | 69.1-101.9 | 66.0-105.0 | +3.1 | 14 |

Mean absolute error 6.3 categories, exactly what it was before R3; mean place
error 2.1 against 2.0; **six of the seven teams it put in the playoff places
really made it**, as before. The visible change is the compression: the
projected records are pulled toward 85.5-85.5, the top team from 106.9 to
102.7 and the bottom from 67.3 to 69.1, because every week's expected wins is
now nearer 4.5. The two big misses are the two a manager would notice -- 
Brighton Bears, whose roster on day 80 was much better than its season turned
out to be, and LeBron's Load Management, which it flattered. Note this table
is the *shipped* call, which reads the league's weekly spreads including
2026's own later weeks (see §4); the calibration's own numbers are the
leak-free ones.

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

    P = Phi( (total_mine - total_theirs)
             / (spread * SPREAD_SCALE * sqrt(days_left / period_days)) )

`spread` is `app.draft.targets.CategoryDistribution.spread`, the standard
deviation of one team's total in that category over a period of the ordinary
length, measured on this league's own results. `SPREAD_SCALE` is 2.0 since
revision R3 (§0), which is what turns a one-team spread into the spread of the
difference between two. Turnovers are inverted. FG% and
FT% are rates rebuilt from the projected makes and attempts
(`CategoryLine.totals`), never averaged across a roster or a week. With no
days left the category is settled outright, and a level one is a coin --
which is exactly how `app.scoring.league.category_record` counts a tie, half
to each side, so the forecast and the record it is scored against count a tie
the same way.

**The variance model is the one judgement call in this module**, and §0 is
where it is argued. It is stated in full in the module docstring. The two
choices in it:

1. The spread is one team's, **doubled** (`SPREAD_SCALE`, revision R3). It was
   one team's undoubled until 2026-09-23, which is the fault §0 found: a
   category is decided by the difference between two totals and was being
   judged against the wobble of one. The factor lives in
   `app.pickups.stream`, not here, which is what stops this page and the week
   page disagreeing about what a week is worth.
2. The nine categories are drawn **independently** in the simulation. They
   are not: a roster with four games on Sunday gains in most of them at once.
   So the spread of simulated outcomes is narrower than the truth. This one
   is still open; part of what the factor of two buys over sqrt(2) is
   standing in for it.

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

**Taken for the hypothetical view, 2026-09-23, and for nothing else.** The
owner asked what a named pickup or a trade does to *his projected finish*, and
that question has no answer against a league-average opponent. So
`project_standings` grew a `rosters` argument -- a team named there is
projected on the men given instead of the ones stored, every other team
standing as it is -- and `app/inseason/what_if.py` runs this engine twice on
the same seed, once as the league stands and once with the change in. That is
the `/what-if` route, the Finish block on the trade page and the `what_if`
tool; `docs/what_if.md` is the write-up.

The seam above is **still not taken**, and this is the reason to be careful
about the difference. Nothing stored moved: the precomputes, the hurdle, the
bid model and the trade calibration all still read the league-average record,
and a hypothetical's judgement is the recommender's own numbers untouched. The
finish sits *beside* them as a second lens with no bar on it, because this
forecast's record is the table in section 0 and a bar on top of that would be a
bar on something that names the right side of a week five out about 55% of the
time. Making the finish the headline is the decision that is still open, and it
needs the backtest the seam has always needed.

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

**Nothing is projected before the draft** (2026-09-25). Both routes ask
`app.api.pickups.readiness` first, and a season that has not been drafted
(`app.inseason.drafted`), or has no schedule or roster stored, answers 200
with `readiness` -- the draft's own sentence as `note`, "The auction is Sat,
Oct 10 at 2:00 PM ET; there are no rosters to project until then." -- and an
empty table: no teams, no periods, no odds, `n_sims` and `seed` null.
`calibration_note` stays, since the method's published record is true of any
season. The team route answers the same thing rather than the 409 it gave for
a team missing from the table. Before this, 2027's ghost rosters -- ESPN's
pre-draft feed, stored as lineup days -- were projected to a 93.2-68.8 finish
for a team nobody had drafted. The morning `project_standings` job skips such
a season with the same reason in its note.

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
python scripts/projected_calibration.py --season 2026 --sigma-scale 0.5   # the pre-R3 model
```

`--sigma-scale` multiplies **on top of** `app.pickups.stream.SPREAD_SCALE`, so
a plain run is the product exactly as it ships and the run prints the
effective factor it applied. The full output of the published run is in
`docs/runs/2026-09-23-projected-calibration.txt`, with its JSON beside it.

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
| cold, in a fresh session | **3.3** |
| warm, second call in the same session | **2.4** |
| day 35 instead, fourteen weeks left | **3.0** |

The third row is the point: the cost barely moves with the number of weeks,
because what it pays for is per player and per team rather than per week.

Of the cold time, about 2.1 s is `category_distributions` -- the 3.79 s the
rehearsal measured is for a season with more results to average, and it is
called **once** here rather than once per team per report kind, which is what
`docs/inseason_rehearsal.md` finding 4 is about, and it is nearly two thirds
of the warm number. The rest is one `load_team_week` per team, one
`build_players` over the whole horizon for the league, one `per_game_line`
per player (memoised on the day, so a man is priced once however many weeks
he appears in), the seating -- fourteen teams times eight weeks times seven
days -- and the simulation.

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

1. **The variance model is `stream.head_to_head`, and it is the only one.** A
   second model would disagree with the week page invisibly. §0 found the one
   it had was too narrow; the fix was taken as its own decision on 2026-09-23
   (revision R3) and applied in that function, so the week page, the pickup
   judgement, the bid sizing, the trade evaluator and this page all moved
   together or not at all.
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
