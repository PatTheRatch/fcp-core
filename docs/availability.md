# Availability: how much of the projection's error is games rather than rate

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft, FAAB from 2026
**Seasons:** 2022–2026 (five seasons of the NBA's official injury reports, Oct 2021 – Apr 2026)
**Script:** `scripts/availability.py` — passes `ruff check`, `ruff format --check` and `mypy`
**Companions:** [`injuries.md`](injuries.md) (the reports, the schema and the point-in-time rule), [`projection_prior.md`](projection_prior.md) (the checkpoint design this reuses), [`pickups.md`](pickups.md) §4 (the availability discount), [`replay_status.md`](replay_status.md) §6 (§3 of this document asked again of the recommender, now that a replayed morning can read a status: 40.45% of its per-man error is the games term, and on the men the league had Out the error falls by nearly half)
**Reproduce:** `cd /home/aisha/fcp-core-avail && PYTHONPATH=. /opt/fcp-core/.venv/bin/python scripts/availability.py`
**Read-only:** every query is a SELECT. Nothing is written to the database and no file is written by the run.

---

## Limitations, stated before conclusions

### 1. One morning snapshot a day, and the afternoon is not in it

The reports were loaded at one snapshot per game date — the league's nine
o'clock Eastern report — except for seventeen dates in the 2021-22 season,
which were loaded at the full hourly cadence. So every table below knows what
was said at ten in the morning and nothing about what was said at four, and
step 4 measures *only* those seventeen dates. A status that was Out at ten and
Active at five is an Out in every number here but §4.

### 2. The match rate, and who is missing

A report line names a player as `Last, First` and is placed on a `players` row
by the same strict matcher the draft board uses. In 2026, of 13,605 lines,
7,953 placed (73.7%); outside the G-League lines, 92.5%. The gap is a
population gap, not a matching failure — the misses are G-League two-way and
on-assignment men who have never been in ESPN's fantasy pool, plus 2025-26
rookies. Two consequences for what follows: **an unplaced line is invisible
here**, and because G-League men are disproportionately unplaced, the
`g_league` reason class below is under-counted relative to the league's own
population. It is not under-counted relative to *this league's* population,
which is the one the product projects.

### 3. Reason classification is a rule over free text, and free text is messy

The league prints 2,473 distinct reason strings. They are classified by a
two-stage rule: the prefix names the family (`G League`,
`Injury/Illness`, `Rest`, `Health and Safety Protocols`, …), and the
disposition after the semicolon names the complaint. The second stage exists
because `Injury/Illness - Left Ankle; Sprain` and `Injury/Illness - N/a;
Illness` share a prefix and are different animals; a one-stage rule that reads
the whole string scores 46,524 lines as "illness", which is the prefix's word
and not the player's problem. The rule is stated in full in the script
(`FAMILY_RULES`, `DISP_RULES`) so it can be recomputed. 211 lines carry the
literal reason `-` and land in `unknown`.

The class boundaries are the script's, not the league's, and two are worth
naming: `rest / management` folds `Rest` together with `Injury Management` and
`Injury Maintenance` (the league uses all three for a load-management night),
and `trade` is the league's `Trade Pending`.

### 4. `played` is ESPN's box-score flag, and it is not "he was on the floor"

`player_game_stats.played` is what the product already reads. Ten 2026 rows
carry `played` true with zero minutes; a `played` flag with no minutes is a
man who dressed and did not get in. The minutes column here is ESPN's
`minutes`, and the "minutes / his own last ten" figure divides it by the man's
own mean over his previous ten played games, so a DNP is 0.00 rather than a
division by zero.

### 5. The decomposition is 2026 only, and it is one attribution not two halves

Step 3 needs checkpoints, so it reads 2026 — the season with saved
player-checkpoints and the season `scripts/projection_prior.py` uses. Nothing
in steps 1, 2, 4 or 5 is 2026-only.

The games and rate numbers in §3b are two *attributions* of one error, not two
errors that sum to it: the metric is a sum of absolute deviations, so holding
the games term perfect and holding the rate perfect give two different
readings of the same quantity and they do not add to the total. The share in
§3b's closing line is `1 − rate_error / total`, which is the reading anyone
would take from it.

### 6. Two clocks, and the wrong one reads plausible

`player_game_stats.game_date` and `pro_team_games.game_at` are both stored in
**UTC**. A seven o'clock Eastern tip-off is 23:00 UTC and a ten o'clock one is
02:00 UTC the next day, so reading `::date` off either puts most evening games
on the wrong calendar date. An early pass of this study read it naively and
produced a 19% coverage rate and a 29% Questionable play rate; both were the
bug. Every date here comes from one conversion function.

The second clock is worse because it is quieter: `player_game_stats` has a
`scoring_period` per row and the blend counts on *that*, while a
date-keyed count silently drops the rows whose `game_date` is NULL. The
difference showed up as the blend reading 5 games where the product read 8 —
`check_blend_against_product` prints the gap on every run, and it was that
check that found it.

### 7. The intraday sample is seventeen dates, not sixteen, and 2021 not 2026

The brief expected sixteen 2026 dates at quarter-hour cadence. **The database
holds no 2026 intraday snapshots at all.** What it holds is seventeen
2021-22 dates — 19 October to 4 November 2021 — loaded at the full hourly
cadence, plus one date (3 November) that is partial. Step 4 uses those and says
so in its own header. Small n, one season, one month of one season: read it as
a direction, not a rate.

### 8. Steps 1, 2 and 5 score a line only when it is about a game that happened

A report line is a statement about one game, and a man can be named on a
morning for a game his team does not play that day. The morning's visible line
is taken for each (player, day) on which the league named him, and is then
scored only where his own team played that day and he has a box-score row. In
2026 that is 10,578 player-day lines, all 10,578 of them falling on a day his
team played, of which **6,196 have a box-score row to be scored against**. The
4,382 without one are men with no row at all that night — nearly all G-League
and two-way men who never touched the NBA floor. The accounting is printed by
`--why` and is reproduced below; every table carries its own `n` because of it.

| season | player-days | on a game day | no box row | scored |
|---|---|---|---|---|
| 2022 | 8607 | 8478 | 3520 | 4958 |
| 2023 | 8358 | 8358 | 3298 | 5060 |
| 2024 | 9443 | 9443 | 4357 | 5086 |
| 2025 | 10829 | 10829 | 5089 | 5740 |
| 2026 | 10578 | 10578 | 4382 | 6196 |

The "about a later game" column is zero because the point-in-time rule is
applied as this script reads the lines: a line whose `game_date` is later than
the day being asked about is not visible for that day at all, so it never
enters the population rather than being counted and discarded.

A note on the season sizes, because the raw line counts at the top of a run do
not match the player-day counts in that table: 2022 shows 26,271 named lines
against 8,607 player-days, while every other season's two figures are within a
few hundred of each other. That is the 2021-22 season carrying October and
November 2021 at the full *hourly* cadence — twenty-four snapshots a day where
every other season has one — so each player-day is counted many times over
before the point-in-time rule collapses it to one line. The collapsed count,
8,607, is the comparable one and is what every table uses.

---

## The answer, up front

**The success check, stated exactly.** Over **27,045 player-game mornings
2022–2026, a man listed Questionable played 55.23% of the time** (n = 6,531;
for 2026 alone it is **55.92% on n = 1,427**). On **2026's** checkpoints
availability was **67.29%** of the projection's 28-day error, and a
status-conditional games term removed **11.08%** of it. The X, Y and Z are
those three numbers; §1's table and `--why` are what recompute them, and the
definitions are stated in limitations 2 and 8 and in `scripts/availability.py`
itself so the recomputation has one answer.

Out is 0.70%, Doubtful 5.88%, Probable 90.98%, Available 92.30%. The league's
five words are a real five-point scale, and the two middle ones are the ones
that cost a projection money.

**On 2026's checkpoints, availability was 67.29% of the projection's 28-day
error, and a status-conditional games term removed 11.08% of it** (§3). That
is the headline and it cuts both ways. The games term *is* most of the error —
but the flat discount the product applies is 0.881, and the men a projection
is written about play about **0.745** of their team's games over a 28-day
window. **The flat discount is wrong in the generous direction, and the
error it causes is not the error a status-conditional term fixes.** Knowing
who was Out that morning fixes the *spread*; the *level* needs a smaller flat
number, not a smarter one.

**The forward-looking news layer has one thing left to sell and it is small.**
The reports carry no timeline words at all — 0 of 6,987 Out runs had a
"week", a "re-evaluated" or a date in the first reason the league gave (§2).
So every return estimate here is built from the length of absences already
observed, not from anything the league said, and a layer that could supply the
return date would be adding information the data does not contain. But the
status a morning carries already resolves most of it: on the seventeen
intraday dates, a Questionable man was *better* by evening 53.10% of the time
and *worse* 38.94% (§4), and the flat games term's error is dominated by men
who play a little less than a flat number predicts, not by men who vanish.

---

## 1. What a morning status is worth

Every (player, game date) where the league named the man on the morning of a
game his own team played, scored against ESPN's `played` flag. n is the count
of such lines with a box-score row.

| season | Out | Doubtful | Questionable | Probable | Available |
|---|---|---|---|---|---|
| 2022 | 1.19% (3365) | 9.65% (114) | 53.27% (1117) | 92.21% (321) | 80.49% (41) |
| 2023 | 1.26% (3014) | 8.24% (182) | 52.85% (1298) | 88.59% (526) | 90.00% (40) |
| 2024 | 0.58% (2951) | 3.17% (189) | 59.75% (1339) | 92.04% (490) | 91.45% (117) |
| 2025 | 0.43% (3471) | 7.03% (185) | 53.93% (1350) | 91.94% (558) | 95.45% (176) |
| 2026 | 0.18% (3825) | 3.61% (249) | 55.92% (1427) | 90.48% (399) | 92.69% (301) |
| **pooled** | **0.70% (16626)** | **5.88% (919)** | **55.23% (6531)** | **90.98% (2294)** | **92.30% (675)** |

The ordering is stable across all five seasons and there is no season that
reverses it. The league's Doubtful is a genuine near-dead status (5.88%) and
its Questionable is a genuine coin flip slightly better than even (55.23%).
The one oddity is 2022's Available at 80.49% on n = 41, which is the league
using the word rarely and in the first weeks of the season; by 2026 it is a
settled 92.69% on 301 lines.

Minutes, as a share of the man's own previous-ten mean, tells the same story
one level down — and it is the more useful number for a projection, because a
man who plays 12 minutes instead of 28 has hurt you nearly as much as one who
did not play:

| status | mean minutes / his own last ten | mean minutes |
|---|---|---|
| Out | 0.01 | 0.14 |
| Doubtful | 0.05 | 1.40 |
| Questionable | 0.53 | 14.69 |
| Probable | 0.90 | 25.63 |
| Available | 0.94 | 24.87 |

### By reason class — "Questionable, rest" is not "Questionable, ankle"

Pooled, cells with 20 lines or more. This is the table a games term would
actually read, because it is the finest split the data supports.

| status | reason | n | played | minutes / own last ten |
|---|---|---|---|---|
| Out | injury | 13373 | 0.19% | 0.00 |
| Out | illness | 149 | 1.34% | 0.01 |
| Out | rest / management | 710 | 0.56% | 0.01 |
| Out | G League | 992 | 5.24% | 0.07 |
| Out | personal | 193 | 0.52% | 0.01 |
| Out | suspension | 168 | 0.00% | 0.00 |
| Out | trade | 38 | 18.42% | 0.27 |
| Out | concussion | 121 | 2.48% | 0.02 |
| Out | protocols | 612 | 3.27% | 0.03 |
| Out | reconditioning | 110 | 0.91% | 0.01 |
| Out | not with team | 160 | 0.62% | 0.01 |
| Doubtful | injury | 750 | 6.13% | 0.05 |
| Doubtful | illness | 42 | 7.14% | 0.07 |
| Doubtful | rest / management | 32 | 0.00% | 0.00 |
| Doubtful | G League | 74 | 1.35% | 0.00 |
| Questionable | injury | 5438 | 55.08% | 0.53 |
| Questionable | illness | 491 | 57.03% | 0.56 |
| Questionable | rest / management | 306 | 57.19% | 0.54 |
| Questionable | G League | 93 | 27.96% | 0.40 |
| Questionable | personal | 46 | 58.70% | 0.61 |
| Questionable | trade | 22 | 50.00% | 0.51 |
| Questionable | concussion | 34 | 70.59% | 0.70 |
| Questionable | protocols | 31 | 70.97% | 0.71 |
| Questionable | reconditioning | 68 | 66.18% | 0.55 |
| Probable | injury | 1975 | 91.19% | 0.90 |
| Probable | illness | 144 | 88.19% | 0.84 |
| Probable | rest / management | 123 | 91.06% | 0.92 |
| Available | injury | 610 | 94.43% | 0.96 |
| Available | unknown | 28 | 82.14% | 1.14 |

**Two classes carry information the status does not.** `Questionable, G
League` plays 27.96% against `Questionable, injury` at 55.08% — a two-way man
tagged Questionable is much more likely to be in the G League that night than
on the floor, and a games term that read the status alone would miss it by 27
points. `Out, trade` plays 18.42%, which is what `Trade Pending` looks like
while a deal is being processed. Everything else collapses toward its status:
the reason class matters most exactly where the status is least informative.

Note the size asymmetry. `Out, injury` is 13,373 lines and `Questionable,
injury` is 5,438, while every other Questionable cell is under 500. The
fine-grained cells are real but thin, and a live games term would need to fall
back to the status for most of the reason classes.

### What silence means

A man whose team played, whose team filed a report that day, and whom the
league did not name. This is the population the flat discount mostly prices.

| season | named n | named played | silent n | silent played | silent mean minutes |
|---|---|---|---|---|---|
| 2022 | 4958 | 19.67% | 10294 | 93.22% | 25.14 |
| 2023 | 5060 | 24.53% | 11870 | 90.96% | 24.10 |
| 2024 | 5086 | 27.15% | 10744 | 93.27% | 24.48 |
| 2025 | 5740 | 25.03% | 9966 | 92.64% | 24.55 |
| 2026 | 6201 | 23.45% | 10761 | 92.70% | 23.62 |
| **pooled** | **27045** | **23.99%** | **53635** | **92.52%** | **24.36** |

**Silence is worth 92.52%, and that is a fact about tonight, not about a
month.** This is the single most important distinction in the document and it
is easy to read past: step 1 answers "does he play the next game", and for a
man nobody named the answer is 92.52%. Step 3 needs a different question —
"what share of a 28-day window's worth of his team's games does he appear in" —
and for the same population that is **74.5%**. The gap is a season's load
management, minor knocks that never reach a report, and bench nights. A games
term built on 92.52% would discount every healthy man by a quarter less than
he is worth; §3 uses the 74.5% and the difference between the two numbers is
why the flat discount is generous rather than harsh.

The class column is the second surprise: 23.99% of named mornings the man
played anyway, one named morning in four.

---

## 2. Absences

6,987 Out runs over five seasons, from `app.injuries.absences` — the product's
own accessor, so the silence-carries-across rule that function documents is the
one being measured.

| season | runs | mean days | median | p90 | max | one-day runs | back within 7 |
|---|---|---|---|---|---|---|---|
| 2022 | 1311 | 11.99 | 5 | 26 | 173 | 31.73% | 65.22% |
| 2023 | 1267 | 11.20 | 3 | 28 | 174 | 39.15% | 66.77% |
| 2024 | 1261 | 12.76 | 5 | 33 | 173 | 33.31% | 60.35% |
| 2025 | 1660 | 12.24 | 4 | 30 | 172 | 37.29% | 61.20% |
| 2026 | 1488 | 12.85 | 4 | 33 | 174 | 38.31% | 62.16% |
| **pooled** | **6987** | **12.23** | **4** | **30** | **174** | **36.08%** | **63.02%** |

The mean is a poor summary and the median is the good one: 36.08% of runs are
a single day and the median run is four days, while the mean is dragged to
12.23 by a tail. The maximum is 172–174 days in every season, which is a man
Out from October to April — an injury that ends a season, seen from here as one
enormous run rather than as an absence with a return.

**The return curve.** Given a man is N days out and still out, the chance he
is back within M more days. Only runs still going at N can be asked, so the
denominator shrinks down the table.

| N days out | runs still going | in 1d | in 3d | in 7d | in 14d | in 28d |
|---|---|---|---|---|---|---|
| 1 | 4466 | 0.20% | 23.87% | 46.69% | 66.64% | 83.95% |
| 3 | 3698 | 8.06% | 24.39% | 45.21% | 63.93% | 82.10% |
| 7 | 2584 | 7.86% | 21.59% | 38.82% | 58.71% | 77.52% |
| 14 | 1581 | 5.76% | 15.62% | 32.51% | 52.75% | 70.78% |
| 28 | 747 | 4.02% | 11.38% | 22.22% | 38.15% | 59.97% |

**The curve is flat in a way that matters.** A man one day out has a 46.69%
chance of being back within a week; a man fourteen days out still has 32.51%.
Being out longer makes a return less likely, but nowhere near proportionally —
the daily hazard falls by about a third from day 1 to day 28, not by the order
of magnitude a "he has been out a month, he is a month away" intuition
suggests. The 28-day column is the number a games term reads for a long
absence, and it says a man four weeks out still plays in about five of the
season's remaining eight weeks' worth of games.

### By reason class

| reason | runs | mean days | median | back within 7 |
|---|---|---|---|---|
| injury | 2514 | 18.13 | 7.0 | 50.88% |
| illness | 148 | 3.06 | 1.0 | 90.54% |
| rest / management | 387 | 5.09 | 1 | 89.66% |
| G League | 2212 | 10.16 | 5.0 | 62.66% |
| personal | 136 | 5.38 | 1.0 | 86.03% |
| suspension | 68 | 6.47 | 1.0 | 80.88% |
| trade | 66 | 2.67 | 1.0 | 95.45% |
| concussion | 65 | 4.02 | 3 | 84.62% |
| protocols | 331 | 5.65 | 6 | 73.41% |
| reconditioning | 22 | 7.55 | 1.0 | 72.73% |
| not with team | 145 | 11.05 | 1 | 79.31% |
| unknown | 893 | 10.86 | 3 | 66.41% |

An injury absence averages 18.13 days and a rest absence 5.09, and the
difference in the median is starker still — 7 days against 1. The class is
worth about a factor of four in expected length, which is the strongest
argument in this document for classifying the reason rather than reading the
status.

### The timeline question, and the answer is never

**The first report's reason carried a timeline word or a printed date in 0 of
6,987 runs.** Not rarely — never. The league's reasons are
`Injury/Illness - Left Ankle; Sprain` and nothing else: no "week", no
"re-evaluated", no date. The words were searched for in the reason text of the
first line of each run, and the vocabulary the search used (`week`, `weeks`,
`re-evaluated`, `reevaluated`, `day-to-day`, `day to day`, `indefinitely`, `out
for`, `month`, `months`) plus a `M/D` date pattern matches nothing in the whole
five-season corpus.

That is a hard negative result and it is the answer to the brief's question
about a forward-looking news layer: **the reports contain no forward-looking
information about return dates at all.** The only return information in the
data is the status itself and the shape of absences already observed.

---

## 3. Availability's share of the error

2026, the six checkpoints `scripts/projection_prior.py` uses (days 21, 42, 63,
84, 105, 126), 1,404 player-checkpoints. The error metric is the fit's own: the
sum over the eleven counts of `COUNTS` of the absolute difference in per-game
rate over the next 28 days against what the man did, each count divided by its
spread. Lower is better, and a zero line scores 18.74 on this population — at
or above that is a wiring defect, not a finding.

The games term enters the metric as a scale on the rate: a line of `rate ×
games` spread over the games the window really held is `rate × games /
actual_games`. So a games term *below* the truth scales the line down, and the
product's 0.881 against a realized 0.745 does exactly that.

**The rates the games term reads, measured on these very checkpoints:**

| status | played / his team's games |
|---|---|
| Out | 0.390 |
| Doubtful | 0.490 |
| Available | 0.730 |
| silent | 0.745 |
| Questionable | 0.746 |
| Probable | 0.749 |
| **the product's flat 0.881** | **above every one of them but Probable's equal** |

Read that table against the product's number. The flat discount is **higher
than every status except Probable** — higher than silence, higher than
Available, higher than Questionable. The product over-projects games for
essentially everyone.

### 3a. The error before and after

| games term | 28-day window | 7 days discounted, then undiscounted |
|---|---|---|
| the product: flat 0.881 × his team's games | **14.19** | 11.37 |
| status-conditional | **12.62** | 12.07 |

The status-conditional term removes **11.08%** of the 28-day error (14.19 →
12.62). The sensitivity the brief asks for is the more interesting result:
discounting only the first seven days and leaving the rest undiscounted scores
**11.37**, which is *better than the status-conditional term over the whole
window* (12.62) and better than anything else measured here. In other words,
the biggest available gain is not from knowing who is hurt — it is from
**not discounting games three weeks out at all**. The near window is where the
uncertainty is; by week three of a 28-day horizon either the man is back or he
was never coming back, and multiplying the far games by a penalty is close to
pure loss.

### 3b. Where the error sits

| reading | error | what it holds |
|---|---|---|
| the product: flat 0.881 × his team's games | 14.19 | the line the product publishes |
| the same rate, given the games he actually played | 4.64 | the rate error, with the games term made perfect |
| the games term alone | 11.55 | the flat games term against the rate's own line |
| oracle: his team's games × his real play share | 5.44 | the best a games term could do, status-blind |
| a zero line | 18.74 | the floor a broken forecast sits above |

**67.29% of the 28-day error is the games term.** Hand the same per-game rate
the games he actually played and the error falls from 14.19 to 4.64. The rate —
what `app.scoring.knowable` blends — is responsible for less than a third of
the error the product publishes.

The oracle row is the one to read carefully, because it is what a *perfect
status-blind* games term would score: his team's games times the share he
really played. It scores 5.44, better than the status-conditional term's 12.62
by a wide margin. That is not a contradiction — the oracle is using
information no system can have (the realized play share) — but it does bound
what is available: **the games term is where the error lives, and the status a
morning carries buys about a third of what the games term could buy.**

### 3c. In the recommender's currency

The fit's units are not the currency the product publishes. Both lines are
priced through `app.scoring.value.marginal` inside the league-average roster
of `app.pickups.judge.standard_lens`, differenced against what the man
delivered. Four decimals: at two, both arms read the same.

| games term | mean absolute error (categories a week) | share removed |
|---|---|---|
| the product: flat 0.881 | 0.4688 | — |
| status-conditional | 0.4444 | **5.20%** |

**The hurdles do not move much.** The recommender's own published error is
roughly 0.47 categories a week and the status-conditional term takes about
0.024 of that off — five percent. For comparison, the hurdles in
`docs/pickups.md` are written at 0.20 and 0.10, and `docs/projection_prior.md`
§6 reports the entire vendor choice between ESPN and BBM as 0.0006 a week. A
0.024 improvement is **forty times larger than the vendor choice** and about a
quarter of one hurdle. It is real and it is not decisive.

The honest reading: this is a measurement that says the *games term* is where
the projection's error is, that a flat 0.881 is the wrong number for it, and
that knowing who was hurt that morning is a fifth of the way to fixing it. It
does not say the product's published numbers move.

---

## 4. The intraday sample

**Seventeen dates, 19 October to 4 November 2021, hourly** — the only intraday
cadence the database holds. See limitation 7: the brief expected sixteen 2026
dates at quarter-hour cadence and there are none.

Morning means before 11:00 Eastern (the nine o'clock report and the ten
o'clock one); evening means the last report from 17:00 Eastern, which is the
last word before a late tip. A pair is a (player, game date) present at both.

| morning status | n | worse by evening | better by evening |
|---|---|---|---|
| Out | 408 | — | 1.72% |
| Questionable | 113 | 38.94% | 53.10% |
| Probable | 39 | 10.26% | 84.62% |

Of 581 pairs, **101 moved up, 60 down, 420 did not move** — 27.71% changed at
all.

The Questionable row is the finding. **A man Questionable in the morning was
better by evening 53.10% of the time and worse 38.94%** — a genuine coin flip
that resolves, and it resolves *toward playing* more often than not. The
product's tilt logic (`app.pickups.projection.minutes_tilt`) reads only a
listener's recorded status *events*, and this is the first evidence of how
much movement a same-day window actually contains: about a quarter of statuses
move, and for the status that matters most the move is roughly symmetric with a
slight upward bias.

n = 113 on the Questionable row and 39 on Probable, over three weeks of one
season. This is a direction, not a rate.

---

## 5. The beneficiary hook

The beneficiary ticket needs to know what a box-score absence really was. The
definition applied here is the brief's statement of `docs/beneficiary.md`'s
(that document does not exist yet): a man with 10 or more games already played
who misses 3 or more consecutive team games, read from box scores. Each such
run is classified by what the report said in its first three days, about the
team he was then on.

| season | absences | injury | illness | rest / mgmt | assignment | suspension | trade | unreported |
|---|---|---|---|---|---|---|---|---|
| 2022 | 575 | 45.04% | 1.39% | 1.39% | 1.57% | 0.00% | 4.17% | 46.43% |
| 2023 | 550 | 57.64% | 1.82% | 1.27% | 3.64% | 0.91% | 3.45% | 31.27% |
| 2024 | 523 | 59.46% | 1.91% | 0.96% | 3.44% | 0.76% | 6.31% | 27.15% |
| 2025 | 531 | 65.35% | 3.01% | 2.07% | 4.71% | 0.38% | 4.33% | 20.15% |
| 2026 | 588 | 59.86% | 2.04% | 4.25% | 4.25% | 0.17% | 5.44% | 23.98% |
| **pooled** | **2767** | **57.32%** | **2.02%** | **2.02%** | **3.51%** | **0.43%** | **4.73%** | **29.96%** |

**The answer for the beneficiary model: about three in ten rotation absences
are not reported at all.** A man misses three or more consecutive team games
and the league's reports never say why — 29.96% pooled, and improving across
the seasons (46.43% in 2022 to 23.98% in 2026) as coverage got more complete.
The remaining two in three are genuinely injuries (57.32%), with illness, rest,
G-League assignments, suspensions and trades together accounting for about 13%.

Two things follow. The first is the useful one: **an absence a box score shows
and the report does not explain is a real and large category**, so a
beneficiary model keyed on `absences()` will be pricing a population about
30% of which the reports cannot vouch for. The second is a caution: 2022's
46.43% is inflated by the season being covered only from 19 October, when the
intraday load ends and coverage becomes sparse for a stretch; the trend from
2023 on is the trustworthy part.

---

## What this means for the product

**Is the flat 0.881 wrong, and by how much?** Yes, and in the direction nobody
expected. The men a projection is written about play **0.745** of their team's
games over a 28-day window, against the 0.881 the product multiplies by. That
is a **15% over-projection of games**, and because games scale the line
linearly it is a 15% over-projection of every counting stat in a
rest-of-season line. The `0.881` figure was measured as realized-over-projected
games against ESPN's own projections (`app/draft/bbm.py`'s docstring: ESPN's
players deliver 0.88 of projected games). Both numbers can be right —
ESPN's projected game counts and the actual games a rostered man's team plays
over the next 28 days are different denominators — but the one that multiplies
a *line* is the second, and it is 0.745. **The candidates are 0.745 flat, or
the status-conditional version below; the 0.881 is not one of them.**

**What is a status-conditional games term worth?** 11.08% of the 28-day error
in the fit's own units, and 5.20% in categories a week. The bigger prize turned
up in the sensitivity: discounting only the first seven days and leaving the
rest alone scores better (11.37) than the status-conditional term over the full
window (12.62). **The simplest available improvement is not a smarter games
term — it is a shorter one.** Discount the next week and stop discounting the
month.

**Would the forward-looking news layer have anything left to add?** One thing,
and the size of it is now bounded. The reports contain *zero* forward-looking
information about return dates (§2) — 0 of 6,987 runs carried a timeline word —
so everything this document estimates about returns comes from the shape of
absences already observed. A layer that could supply the actual return date
would be adding a quantity the data does not contain, and §3b's oracle says the
ceiling on that is the gap between the status-conditional term (12.62) and the
perfect games term (5.44). But §4 says the status itself resolves most of the
same-day uncertainty (a Questionable man is better than the tag 53% of the time
and worse 39%), so the layer's realistic edge is narrower than the oracle
suggests: it would be competing against a status that is already a decent
forecast of itself.

**The verdict: worth building is the wrong question, because the ordering is
the opposite of what the brief assumed.** The flat discount is not too harsh
and does not need injury news to soften it — it is too generous, and the fix is
a smaller flat number plus a short window. A status-conditional term buys a
fifth of the games error and a twentieth of the published error. The
forward-looking layer would be adding detail to a term that is already
over-credited at the level, and its honest justification is not "the projection
is missing injury news" but "the projection is missing a return date, and the
return date is worth about as much as the status already is."

### The three tables a games term would read

**1. Status × reason → play rate over a window** (`window_rate`, §3). A live
term multiplies his team's games in the window by this.

| status | reason class | rate |
|---|---|---|
| silent | — | 0.745 |
| Available | any | 0.730 |
| Probable | any | 0.749 |
| Questionable | any | 0.746 |
| Questionable | G League | 0.280 |
| Doubtful | any | 0.490 |
| Out | any | 0.390 |
| Out | injury | 0.00 |
| Out | G League | 0.052 |

The top six cells are the ones a term can use today; the `Out` rows are near
zero because an Out man is out, and the refinement there belongs to table 2.

**2. Days out → return distribution** (§2b). For a man the morning has Out and
has had Out for N days, the chance he is back within M more days.

| N days out | in 1d | in 3d | in 7d | in 14d | in 28d |
|---|---|---|---|---|---|
| 1 | 0.20% | 23.87% | 46.69% | 66.64% | 83.95% |
| 3 | 8.06% | 24.39% | 45.21% | 63.93% | 82.10% |
| 7 | 7.86% | 21.59% | 38.82% | 58.71% | 77.52% |
| 14 | 5.76% | 15.62% | 32.51% | 52.75% | 70.78% |
| 28 | 4.02% | 11.38% | 22.22% | 38.15% | 59.97% |

**3. Silence rate** (§1c): **0.925 for the next game, 0.745 for a window.**
Which one a caller wants depends on the horizon, and using the first where the
second belongs is the mistake this document exists to prevent.

---

## Decisions

Every judgement call, in the order it arose.

- **Both `player_game_stats.game_date` and `pro_team_games.game_at` are UTC, and `eastern_date()` is the only date conversion in the script.** The brief's `.env` note and the house style both warn about `.env`; the clock trap was not in the brief and cost the first two passes of this study. A naive `::date` read produced a 19% line-coverage rate and a 29% Questionable play rate, both internally consistent and both wrong. Everything downstream of that read is corrected, and the corrected numbers are the ones in this document.
- **The blend counts on `scoring_period`, not on a date.** `player_game_stats` has NULL `game_date` on a meaningful share of rows, and a date-keyed count silently drops them — the blend read 5 games where the product read 8. `check_blend_against_product` now reports a worst gap of **3.55e-15** over 200 checkpoints and would have caught it; that check is on every run.
- **The local absence reader is held to `app.injuries.absences`, which caught a real bug.** `check_absences_against_product` reported a player whose run the local reader merged across a day the product correctly split (2026-02-08, when the Lakers filed about the 9th and not the 8th). The fix is the same `game_date >= day` bound `status_as_of` uses. It now reads **no gap** over 400 players.
- **Step 1 scores a line only when it is about a game that happened.** The brief says "for every (player, game_date) with a morning status". Taken literally that scores lines about tomorrow's game against nothing. This script requires the morning's own line to be about that day and a box-score row to exist, which drops 4,382 of 2026's 10,578 lines — nearly all G-League men with no NBA floor time. Every table carries its `n` because of it, and `--why` prints the accounting.
- **The reason rule has two stages, and the obvious one-stage rule is wrong.** `Injury/Illness - Left Ankle; Sprain` and `Injury/Illness - N/a; Illness` share a prefix. A rule that reads the whole string scores 46,524 lines (all of `Injury/Illness`) as "illness"; the two-stage rule lands illness at 1,505 and injury at 45,052. The disposition after the semicolon decides illness, concussion and reconditioning; the prefix decides the rest.
- **`rest / management` folds `Rest`, `Injury Management` and `Injury Maintenance`.** The league uses all three for a load-management night and separates them inconsistently; keeping them apart produced cells of 2 and 1 lines. This is a judgement and a reader who disagrees can split them by editing `FAMILY_RULES`.
- **The games term uses the *window* play rate, not step 1's next-game rate.** These are different quantities and conflating them made the first version of §3 report a status-conditional term that was *worse* than flat. Silence is 92.5% for tomorrow and 74.5% for a month; only the second is a games term. `window_rate` measures it off the very checkpoints the term is scored on, in a two-pass build, so the term and the thing it is scored against are one population.
- **The 0.881 is applied to the 28-day window as the brief describes it, and the realized figure is reported beside it.** The brief asks for "games projected under the flat 0.881 discount versus games actually played in the window". 12.59 team games × 0.881 = 11.09 projected against 9.24 actually played.
- **The near-window sensitivity discounts 7 days and leaves the rest undiscounted rather than applying 0.881 to the near seven.** The brief's "the discount applied to the next 7 days only" is read as the flat number applied to a seven-day window; the alternative reading — the same full discount but decayed after a week — is not what the words say and would have been a third arm. Reported as it stands, the seven-day arm beats the status-conditional term and that is the finding.
- **The games/rate split is an attribution, not a partition.** The metric is a sum of absolute deviations, so the two readings do not add to the total. §3b says this in the limitation and in the table.
- **The beneficiary rotation threshold is 10 played games.** The brief's definition is "a rotation player missing 3+ consecutive team games". "Rotation" needed a number and 10 is the `knowables` docstring's own `PRIOR_GAMES`, which is the boundary the product itself uses for "has enough games that his own record means something".
- **A box-score absence is classified from the first three days of the run and only where the line is about the team he was then on.** A trade is the fallback where the man's season holds two teams and the reports explain none of the gap. This produces a 29.96% `unreported` share, which is the finding rather than a failure of the classifier — the classifier's job was to distinguish "unreported" from "injury", and it does.
- **Step 4 uses the seventeen 2021 dates the database actually holds, not the sixteen 2026 dates the brief names.** There are no 2026 intraday snapshots. The step detects its own sample from the data (`>= 15` snapshots a date) and prints the date range in its header, so the substitution is visible rather than silent. n = 113 on the Questionable row and it is one month of one season.
- **"Worse" and "better" in step 4 are `app.injuries`' own severity order** (`SEVERITY`, the same ordering as `RULES_OUT`/`IN_DOUBT`/`EXPECTED` in the module docstring), so "better" means moved toward Available and "worse" toward Out.
- **The timeline search looks at the first line of each Out run, and searches the whole reason text.** It finds nothing — 0 of 6,987. The search is stated with its vocabulary in the script so a reader can widen it and re-run; widening it to any word containing "return" would match the 516 `Return to Competition Reconditioning` reasons, which are a status rather than a timeline, and that widening was considered and rejected.
- **The document's first section quotes X for 2026 as well as pooled.** The success check specifies "for 2026"; Questionable plays 55.92% on 2026 (n = 1,427) and 55.23% pooled (n = 6,531). Both are stated so the recomputation has no ambiguity about which the check means.
- **Nothing is wired and nothing reads this.** It is a measurement: no file in `app/` changed, the product's 0.881 is untouched, and the branch adds one script and one document. **Still true of this document on 2026-09-24**, and one thing around it has changed: the engine now reads the same reports for a replayed morning ([`replay_status.md`](replay_status.md)), which is the *source*, not the term. The status-conditional games term of §3 and the 0.745 of "What this means for the product" are still unbuilt, and §6 of that document prices what they are worth on the recommender's own population — a Doubtful man is counted for 24.88 games there and plays 16.17.
