# Stashes: when a man who is not playing is worth a roster place

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft, FAAB from 2026
**Seasons covered:** 2019–2026 (eight seasons; league sizes 10, 10, 10, 12, 16, 14, 12, 14)
**Script:** `scripts/stashes.py` (re-runnable; passes `ruff check`, `ruff format --check` and `mypy`)
**Instrument:** `player_game_stats` · `pro_team_games` · `transactions` · `transaction_items` · `daily_lineup_slots` · `matchup_team_stats` · `injury_reports`
**Currency:** categories a week, through `scripts/pickups_backtest.py`'s `Replay` and `app.pickups.judge.standard_lens`
**Companions:** [`stash_mode.md`](stash_mode.md) (**what this study produced**: the rule, declared and built 2026-09-24), [`availability.md`](availability.md) (the absences and the report-based return prior this calibrates against), [`keepers.md`](keepers.md) (the hold, the replacement level, the `Replay` reuse), [`streaming_lane.md`](streaming_lane.md) (`OPENED_PLACE`, what a dead place costs), [`pickups.md`](pickups.md) §4.4 (the stash lane as designed), [`what_if.md`](what_if.md) (the engine this feeds)
**Reproduce:** `PYTHONPATH=. ~/fcp-core/.venv/bin/python scripts/stashes.py --why` — this run was made against the **local** Docker Postgres (`fcp-core-db-1`, `localhost:5432`), not the VPS; on the VPS the interpreter is `/opt/fcp-core/.venv/bin/python` and the injury-report tables of §1b and §3 will be five seasons deep rather than one (limitation 2).
**Read-only:** every query is a SELECT. Nothing is written to the database. **Runtime 254s** for all eight seasons.

> **Note on the reproduce line.** The house style sources `.env` before running
> (`set -a && . ./.env && set +a`). That fails here — line 29 of `.env` holds an
> unquoted `FCP_EMAIL_FROM` with angle brackets and bash rejects the file — so
> the script reads `DATABASE_URL` out of `.env` itself. A worktree has no `.env`
> of its own and falls back to the main checkout's. Nothing from it is printed.

---

## Limitations, stated before conclusions

### 1. "Out" here is a box score, and the two ways of counting it are not the same population

**The definition.** `player_game_stats` carries one row per man per game his NBA
team played, `played` true when he was on the floor and false when he was not.
A **missed team game** is a `played = false` row. **Days out at a decision on
day D** is D minus the latest day before D on which he has a `played = true`
row; a scoring period is a calendar day in this database (2026's period 12 is
2025-11-01 and period 33 is 2025-11-22 — twenty-one periods, twenty-one days),
so "days out" is a real count of days.

**A stash is a decision taken at 8 or more days out.** 1–7 days is the control.

**The brief's other phrasing — "seven or more consecutive team game days" — is a
different count, and both are printed rather than one being chosen.** The two
populations overlap but do not coincide, because a team plays about 3.3 games a
week:

| level (days out) | n | median team games missed | share with ≥4 missed |
|---|---|---|---|
| 1–7 (control) | 308 | 1.00 | 0.32% |
| 8–14 | 158 | 4.00 | 68.35% |
| 15–28 | 133 | 9.00 | 100.00% |
| 29+ | 128 | 20.00 | 100.00% |

Read the 8–14 row: a third of the men who are "eight days out" have missed
three team games or fewer. A games-based threshold of four would drop them. The
day count is used as the level everywhere below because it is the quantity a
manager actually has on the morning of a claim, and the games count is printed
beside it in §1b so a reader who prefers the other rule can see who moves.

### 2. This database holds the injury reports for **2026 only**, so the second witness is one season

`injury_reports` here is 20,776 lines with game dates from 2025-10-21 to
2026-04-12, 12,451 of them placed on a player. The brief expected 2022–2026;
that corpus is on the VPS, and `docs/availability.md` was written against it.
**216 of this study's 2,419 decisions have a report line for their morning.**
Every reason-class cut below is therefore a 2026 cut with n in the single or
low double digits outside `injury`, and none of them should be read as a rate.

### 3. Two currencies carry the same name, so the net is reported twice

This is the most important thing to understand before reading any net.

`Replay.delta` counts **categories won in a real matchup**: the roster of each
day is re-seated with the man taken out, and the difference against the
opponent's real period totals is what he was worth. It moves in steps of a half
category, because a category is won, lost or tied.

The wire's replacement level and `OPENED_PLACE` (0.38) are **marginal values
through `Standard.value`** — a lens quantity, continuous, measured on
`app.scoring.value.marginal` against the league's own weekly spreads.

Both are called "categories a week" and they are not the same measurement.
`scripts/keepers.py` ships the same pairing. Rather than pick one, every table
here prints **two arms**:

* **the declared net** — the brief's formula: Σ(weekly `Replay` value −
  replacement) over the weeks held after return, less 0.38 × dead weeks;
* **the swap net** — the identical hold scored as one `Replay` swap,
  `-Replay.delta(team, week, [him], [the wire's best free agent of the decision
  morning])`, over **every** week of the hold, dead weeks included. No constant,
  no second lens, and it answers the manager's own question: was holding him
  better than putting the best man on that morning's wire in the place?

**The two arms agree on the sign for 77 of 2026's 91 claimed stashes (84.62%).**
Where they disagree, the declared arm is usually the more generous one, because
its replacement hurdle is a per-game line through the lens — 0.20 a week on the
morning of the Miller claim — and that is small against the `Replay` scale it is
subtracted from.

### 4. The held population is chosen by managers, and that is the whole point of it

A "held stash" is a man somebody **decided** to keep through an absence. The
KEPT rows of §1c are that decision, not a random sample of injured rosters. The
DROPPED rows are the counterfactual managers actually took, and they are
reported beside it — but a dropped man's post-return value accrues to whoever
picked him up, so a dropped hold's net is only the cost of the days it burned,
never the value it forwent. **§1c's "kept" shares are behaviour; its nets are
outcomes; do not read the pair as a causal claim.**

### 5. 2020 was suspended in March, and every man out that day reads as an absence that never ended

The NBA stopped on 2020-03-11. In the box scores that is not a stoppage, it is
1,000 players simultaneously never playing again. 2020 has **51.37%** of its
absences at 8+ days against a 33.01% pooled figure, **27.43%** never returning
against 9.75%, and 157 of its 251 held stashes at the 29+ level against a
typical 30–50.

| population | n | med net | positive | 29+ n | 29+ med net | 29+ never back |
|---|---|---|---|---|---|---|
| all eight seasons | 2165 | −0.16 | 30.76% | 459 | −0.54 | 46.84% |
| **without 2020** | **1881** | **−0.11** | **32.43%** | **300** | **−0.26** | **26.00%** |
| 2020 alone | 284 | −1.44 | 19.72% | 159 | −5.59 | 86.16% |

**The return prior in §2a drops 2020 and that is the curve everything else
here uses.** The pooled eight-season curve is printed beside it. Nothing else
in the document is cut on 2020, and §4e is the sensitivity for the break-even.

### 6. Small n at the deep end, and one healthy tier is empty

419 claimed stashes over eight seasons, 128 of them at 29+; 1,692 held stashes,
395 at 29+ (159 of those are 2020's stoppage). The `2.00+` healthy tier holds
nobody: through this lens a per-game line scaled to 3.3 games is worth 1.14
categories a week at the top of the measured range, so the tier boundary is
above anything the population contains and its row is a dash.

### 7. The "healthy value" is his last ten played games, and ten games is often not there

A man claimed in the season's first fortnight can have two or three played games
behind him. `MIN_NORM` is 5: below it his healthy value and his ramp are not
read at all and he sits in the `?` tier (179 of 2,165 scored decisions).
**Brandon Miller is exactly that case** — two played games in 2026 before the
shoulder, at twenty minutes — which is why §6b has no ramp row for him and why
the gate exists.

### 8. A late stash cannot be observed, and its net is a floor

A claim made in the season's last weeks is right-censored: the post-return weeks
this study can count are the weeks the season had left. §4c is the census of
that — 865 of 2,165 decisions had four regular weeks or fewer remaining, and
their median net is −0.36 against −0.05 for those with sixteen or more. **Much
of the "stashes lose money" headline is late-season stashing**, and §4c is where
that shows rather than being hidden in a pooled number.

### 9. The engine's zero (§7) is a fact about this database

`app.pickups.state.playable_days` gives an OUT man zero games before ESPN's
`expected_return_date` and zero games **at all** when there is no date. This
database holds **0 rows with a non-null `expected_return_date`** in
`player_status_snapshots`, across every season. So the engine's projection for
every stash here is exactly zero. A live morning with a populated listener
snapshot would behave differently for the men ESPN has dated — but the same
table is the only source the engine reads, and it has never held a date.

> **Twice superseded, both on 2026-09-24.** The rule changed first
> ([`stash_mode.md`](stash_mode.md): an OUT man's games are expected, not
> zero), and then the source did
> ([`replay_status.md`](replay_status.md): a replayed morning reads the NBA's
> own official report when the listener took no snapshot by then). Both
> changes are re-scored in §7 and neither alters what is written above, which
> is the state that caused them. The probe named in §7's own banner settled
> the open question in this limitation: there is no `expected_return_date`
> field on ESPN's basketball player object at all.

---

## The answer, up front

**Of 2026's 1,150 executed adds, 156 landed on a man who was already out and 91
were stashes (out 8+ days at the claim); 84 of the 91 returned that season, the
median 3.5 days after the claim; the median stash netted −0.05 categories
against the dead weeks, positive in 31.87%.** Scored the second way — him
against the best free agent of that morning, in the same place, every week of
the hold — the median is +0.00 and 23.08% are positive.

Every part of that is recomputable from `transactions`, `transaction_items`,
`player_game_stats` and `daily_lineup_slots`; `--why` prints the accounting and
rebuilds the same two numbers a second way (§Decisions 12).

**The plain reading, in four sentences.**

**A stash is not a rare move and it is getting commoner.** 419 of 6,779
executed adds over eight seasons landed on a man 8+ days out — **6.18%** — and
the share has risen every era, 3.56% in 2019 to 7.91% in 2026. Holding an
injured man is commoner still: **1,692** times a team met an 8+ day absence
while holding the man, and it kept him through **66.49%** of them.

**The median stash is worth nothing and the mean is worth a lot.** Across the
1,881 scored decisions outside the suspended 2020 season the median declared net
is **−0.11** and **32.43%** are positive. But 2026's 91 claimed stashes
delivered **94.50 categories** in
total over the weeks they were held after returning, a mean of **1.04** each,
and the top of the list is Jaylon Tyson at +9.00 and Saddiq Bey at +8.50. **The
distribution is the finding: a stash is a lottery ticket with a small, known
cost and a long right tail**, and a median is the wrong statistic to run a
product off.

**What separates the winners is almost entirely three things, and none of them
is the injury.** How good he is (the `1.00–2.00` tier is positive 41.72% of the
time against 24.39% for `<0.50`), how long the season has left (45.44% positive
with ten to fifteen weeks left against **12.95%** with four or fewer), and how
long he is out (40.89% at 8–14 days against **11.33%** at 29+). §4d turns those
three into one table a manager can read.

**The engine could not see any of it, and now it can see most of it.** When
this was written, all 91 of 2026's claimed stashes were projected at **0.00**
on the claim morning: `playable_days` counted an OUT man with no return date
for zero games forever, and this database has never held a return date.
Fifty-three of the ninety-one returned and were held, so the error was not a
bias to correct but the whole quantity — **+1.04 categories a stash**.

Two changes on 2026-09-24 closed most of it. The rule
([`stash_mode.md`](stash_mode.md)) counts an OUT man for his *expected* games,
and the source ([`replay_status.md`](replay_status.md)) lets a replayed
morning read the NBA's own official report. §7 is re-scored on both: through
the engine's own path the ninety-one are now projected at **1.17 a stash**
against the 1.04 they delivered — **a mean error of −0.13 and a mean absolute
error of 0.71**, where it was +1.04 and 1.04.

**The limit is now coverage, not arithmetic.** On only **34 of the 91 claim
mornings** did the league say anything at all about the man, and on only
**17** did it say `Out`; on the other 57 his team was not playing that day and
the report is silent, so the engine still counts him for all his team's
remaining games. That is the honest shape of what one morning snapshot a day
can support.

---

## 0. The instrument, checked before anything is measured

### 0a. A man's box-score rows really are his own team's schedule

The whole study rests on "a `played = false` row is a team game he missed". If
`player_game_stats` were missing rows, an absence would look shorter than it
was. For every player-season with twenty or more rows, the pro team whose
`pro_team_games` schedule his rows best match, and the two coverage rates:

| season | player-seasons | his rows on that schedule | that team's games with a row for him |
|---|---|---|---|
| 2019 | 276 | 97.22% | 96.63% |
| 2020 | 253 | **77.97%** | 98.34% |
| 2021 | 286 | 98.02% | 97.48% |
| 2022 | 304 | 97.65% | 97.30% |
| 2023 | 343 | 98.01% | 98.00% |
| 2024 | 335 | 97.67% | 97.31% |
| 2025 | 319 | 97.61% | 96.64% |
| 2026 | 351 | 97.93% | 96.29% |

The residual two to four points is trades — a man who changed teams has rows on
two schedules and matches one. 2020's 77.97% is the bubble: the season's
`pro_team_games` holds 1,948 rows against a normal 2,470, so a large part of
each man's real schedule has no stored team-game row to match against. **The
definition needs no team attribution at all** — it reads the man's own rows —
which is why the study survives both.

### 0b. Absences, as the box scores have them

Rotation players only (10+ played games in the season, the `PRIOR_GAMES`
boundary `docs/availability.md` §5 uses for the same purpose):

| season | spells | med days out | med games missed | 8+ days | never back |
|---|---|---|---|---|---|
| 2019 | 652 | 6.00 | 2.00 | 37.73% | 12.27% |
| 2020 | 915 | 8.00 | 2.00 | **51.37%** | **27.43%** |
| 2021 | 1479 | 5.00 | 1.00 | 28.80% | 7.64% |
| 2022 | 1654 | 6.00 | 2.00 | 35.91% | 8.22% |
| 2023 | 1939 | 5.00 | 1.00 | 30.12% | 8.15% |
| 2024 | 1783 | 5.00 | 1.00 | 32.59% | 7.23% |
| 2025 | 1835 | 5.00 | 1.00 | 31.55% | 8.94% |
| 2026 | 2131 | 5.00 | 1.00 | 28.58% | 8.31% |
| **pooled** | **12388** | **5.00** | **1.00** | **33.01%** | **9.75%** |

Without 2020: 11,473 spells, 8.34% never back. The median absence is five days
and one missed game — **two thirds of absences never become a stash question at
all**, which is the base rate every figure below sits on.

---

## 1. Stashes found

### 1a. Claims that landed on a man who was already out

| season | executed adds | on an out man | 1–7 (control) | 8–14 | 15–28 | 29+ | stashes | stash share of all adds |
|---|---|---|---|---|---|---|---|---|
| 2019 | 731 | 40 | 14 | 6 | 7 | 13 | 26 | 3.56% |
| 2020 | 592 | 52 | 23 | 10 | 8 | 11 | 29 | 4.90% |
| 2021 | 832 | 82 | 42 | 10 | 12 | 18 | 40 | 4.81% |
| 2022 | 789 | 87 | 39 | 24 | 11 | 13 | 48 | 6.08% |
| 2023 | 608 | 62 | 27 | 13 | 6 | 16 | 35 | 5.76% |
| 2024 | 940 | 103 | 42 | 23 | 19 | 19 | 61 | 6.49% |
| 2025 | 1137 | 145 | 56 | 40 | 37 | 12 | 89 | 7.83% |
| 2026 | 1150 | 156 | 65 | 32 | 33 | 26 | 91 | 7.91% |
| **pooled** | **6779** | **727** | **308** | **158** | **133** | **128** | **419** | **6.18%** |

**The share of all claims that were stashes has more than doubled, 3.56% to
7.91%.** Two things are mixed in it and this study cannot separate them: the
league got bigger (10 teams to 14, with a 16-team season in between), and the
managers plainly got more willing. 2025 and 2026 are the two highest and they
are not the two largest leagues.

### 1b. The two counts of "out", and the report as a second witness

The overlap between the box-score definition and the injury reports' — 2026
only (limitation 2), the morning's visible line by `app.injuries.status_as_of`
at `morning_of`:

| level | n | med games missed | ≥4 missed | report seen | of those, Out or Doubtful |
|---|---|---|---|---|---|
| 1–7 (control) | 308 | 1.00 | 0.32% | 20 | 30.00% |
| 8–14 | 158 | 4.00 | 68.35% | 13 | 30.77% |
| 15–28 | 133 | 9.00 | 100.00% | 13 | 61.54% |
| 29+ | 128 | 20.00 | 100.00% | 8 | 62.50% |

**The two definitions agree where it matters and disagree exactly where you
would expect.** At 15+ days out the league had him Out or Doubtful about
62% of the mornings it named him at all; at 8–14 days only 31%, and at 1–7 days
also 31%. A man eight days out by the box score is often a man the league has
*stopped* naming — back on the report as Questionable or off it entirely — which
is the case `docs/availability.md`'s silence rule is about. **The box score is
the stricter witness and the earlier one**, and it is available for all eight
seasons where the reports are available for one.

By reason class, the claims whose morning carried a line (2026, n = 54 in total,
so this is an illustration and not a rate):

| reason class | n | stash share | med days out | returned |
|---|---|---|---|---|
| injury | 45 | 68.89% | 14.00 | 97.78% |
| rest / management | 3 | 0.00% | 3.00 | 100.00% |
| concussion | 2 | 50.00% | 9.00 | 100.00% |
| G League | 2 | 50.00% | 11.00 | 100.00% |
| illness | 1 | 100.00% | 24.00 | 100.00% |
| suspension | 1 | 0.00% | 5.00 | 100.00% |

**A claim on a man the league calls out is nearly always a claim on an injury**
— 45 of 54 — and rest, illness and G-League absences essentially never reach the
stash threshold, which is `docs/availability.md` §2's "an injury absence averages
18 days and a rest absence 5" showing up on the other side of the transaction.

### 1c. Held stashes: kept through the absence, or dropped

A man already on a roster when he began an absence of 8+ total days, and what
the team did:

| season | n | kept | dropped | 8–14 | 15–28 | 29+ |
|---|---|---|---|---|---|---|
| 2019 | 106 | 65.09% | 34.91% | 59 | 31 | 16 |
| 2020 | 251 | 76.89% | 23.11% | 62 | 32 | **157** |
| 2021 | 184 | 61.96% | 38.04% | 107 | 51 | 26 |
| 2022 | 228 | 67.98% | 32.02% | 145 | 49 | 34 |
| 2023 | 242 | 79.34% | 20.66% | 147 | 54 | 41 |
| 2024 | 218 | 55.96% | 44.04% | 126 | 52 | 40 |
| 2025 | 206 | 60.68% | 39.32% | 128 | 46 | 32 |
| 2026 | 257 | 60.31% | 39.69% | 122 | 86 | 49 |
| **pooled** | **1692** | **66.49%** | **33.51%** | **896** | **401** | **395** |

And what the keeping was worth, by how long the absence ran:

| level (total days out) | n | kept | med net of the kept | kept and paid | med swap net | paid (swap) | med swap of the dropped |
|---|---|---|---|---|---|---|---|
| 8–14 | 896 | 78.46% | 0.00 | 49.64% | 0.00 | 43.67% | 0.00 |
| 15–28 | 401 | 59.60% | −0.30 | 41.42% | 0.00 | 39.75% | 0.00 |
| 29+ | 395 | 46.33% | **−5.59** | **8.20%** | 0.00 | 13.11% | 0.00 |

**Managers are already roughly right about this and the numbers say so.** They
keep 78% of men out a week or two, 60% of those out two to four weeks, and 46%
of those out a month or more — and the payoff falls in exactly that order:
49.64% of the short holds paid, 41.42% of the medium, **8.20%** of the long. The
29+ row's −5.59 median is heavily 2020's stoppage (159 of its 395); without 2020
the 29+ median net is −0.26 (§Limitations 5). **The behaviour is well-calibrated
at the top of the distribution and too generous at the bottom**: a man a month
out was kept nearly half the time and paid one time in twelve.

---

## 2. Return timing, against the prior

### 2a. The box-score return prior

Given a man has been out N days and is still out, the chance he plays again
within M more days. Only runs still going at N can be asked, so the denominator
shrinks down the table — the same shape as `docs/availability.md` table 2,
measured from the box scores instead of the reports.

**Seven seasons, 2020 out — this is the curve the rest of the document uses.**

| N days out | still out | in 1d | in 3d | in 7d | in 14d | in 28d |
|---|---|---|---|---|---|---|
| 1 | 11473 | 0.11% | 38.33% | 68.10% | 82.30% | 88.39% |
| 3 | 8996 | 24.63% | 49.96% | 70.74% | 82.90% | 88.95% |
| 7 | 3619 | 12.93% | 33.19% | 54.66% | 69.99% | 80.16% |
| 14 | 1523 | 7.81% | 20.81% | 36.44% | 52.79% | 66.58% |
| 28 | 582 | 2.41% | 9.79% | 20.45% | 36.08% | 52.41% |

All eight seasons, for contrast:

| N days out | still out | in 1d | in 3d | in 7d | in 14d | in 28d |
|---|---|---|---|---|---|---|
| 1 | 12388 | 0.10% | 37.30% | 66.93% | 80.89% | 86.96% |
| 3 | 9798 | 23.74% | 48.81% | 69.10% | 81.01% | 86.99% |
| 7 | 4089 | 12.25% | 31.18% | 51.43% | 65.98% | 75.76% |
| 14 | 1868 | 6.85% | 18.15% | 31.85% | 46.25% | 58.62% |
| 28 | 867 | 1.73% | 7.04% | 15.11% | 26.64% | 38.64% |

**The curve is flat in the same way `docs/availability.md` found it to be.** A
man one day out is back within a week 68% of the time; a man fourteen days out
is still 36%. Being out longer makes a return less likely but nowhere near
proportionally — the 28-day row still says **half** the men a month out play
again inside another month.

### 2b. The calibration against `docs/availability.md` table 2

Table 2 is the report-based curve measured on 2022–2026's injury reports, 6,987
Out runs, on the VPS. It is quoted in `scripts/stashes.py` as a literal so this
comparison can be recomputed without the reports this database does not hold.
"gap" is the 2020-out box-score figure less table 2, in percentage points.

| N days out | within M | box score (2020 out) | box score (all eight) | table 2 | gap (pts) |
|---|---|---|---|---|---|
| 1 | 1 | 0.11 | 0.10 | 0.20 | −0.09 |
| 1 | 3 | 38.33 | 37.30 | 23.87 | **+14.46** |
| 1 | 7 | 68.10 | 66.93 | 46.69 | **+21.41** |
| 1 | 14 | 82.30 | 80.89 | 66.64 | **+15.66** |
| 1 | 28 | 88.39 | 86.96 | 83.95 | +4.44 |
| 3 | 1 | 24.63 | 23.74 | 8.06 | **+16.57** |
| 3 | 3 | 49.96 | 48.81 | 24.39 | **+25.57** |
| 3 | 7 | 70.74 | 69.10 | 45.21 | **+25.53** |
| 3 | 14 | 82.90 | 81.01 | 63.93 | **+18.97** |
| 3 | 28 | 88.95 | 86.99 | 82.10 | +6.85 |
| 7 | 1 | 12.93 | 12.25 | 7.86 | +5.07 |
| 7 | 3 | 33.19 | 31.18 | 21.59 | +11.60 |
| 7 | 7 | 54.66 | 51.43 | 38.82 | +15.84 |
| 7 | 14 | 69.99 | 65.98 | 58.71 | +11.28 |
| 7 | 28 | 80.16 | 75.76 | 77.52 | +2.64 |
| 14 | 1 | 7.81 | 6.85 | 5.76 | +2.05 |
| 14 | 3 | 20.81 | 18.15 | 15.62 | +5.19 |
| 14 | 7 | 36.44 | 31.85 | 32.51 | +3.93 |
| 14 | 14 | 52.79 | 46.25 | 52.75 | **+0.04** |
| 14 | 28 | 66.58 | 58.62 | 70.78 | −4.20 |
| 28 | 1 | 2.41 | 1.73 | 4.02 | −1.61 |
| 28 | 3 | 9.79 | 7.04 | 11.38 | −1.59 |
| 28 | 7 | 20.45 | 15.11 | 22.22 | −1.77 |
| 28 | 14 | 36.08 | 26.64 | 38.15 | −2.07 |
| 28 | 28 | 52.41 | 38.64 | 59.97 | −7.56 |

**The prior is well calibrated where a stash lives and badly calibrated where it
does not.** At 14 and 28 days out — the range the whole of §4 is about — the two
curves sit within 0.04 to 5.2 points of each other at every horizon but one
(28-in-28, −7.56). At 1 and 3 days out the box score is 14 to 26 points more
optimistic, and that is not a disagreement about players, it is the definition:
**a box-score day out counts only nights his team played, while a report Out run
counts every calendar day, including the two or three a week nobody plays.** One
box-score day out is about two report days out. A reader comparing the two
tables must not read across the N column as though it meant the same thing.

The 2020 column shows what the stoppage does: at 28 days out it drags the
28-day return share from 52.41% to 38.64%, which would put the prior 21 points
below table 2 and make it look badly wrong.

### 2c. What the stashes themselves did

Claims and holds together, 2020 included:

| level | n | returned | med days to return | back in 7d | back in 14d |
|---|---|---|---|---|---|
| 1–7 (control) | 308 | 95.13% | 0.00 | 85.71% | 90.58% |
| 8–14 | 1054 | 98.20% | 8.00 | 47.34% | 96.96% |
| 15–28 | 534 | 92.51% | 15.50 | 17.23% | 40.26% |
| 29+ | 523 | 54.11% | 31.00 | 13.77% | 17.21% |

**The control row is the tell: the median 1–7 day claim returns on the very day
he is claimed.** Managers are, overwhelmingly, buying a man on the morning the
news breaks that he is fine. The 8–14 row is the one that looks like a stash and
behaves like one: back in a week half the time, back in a fortnight essentially
always. The 29+ row is where the coin flip lives — **46% of decisions at 29+ out
never saw the man play again that season.**

---

## 3. The ramp after return

Minutes and value over the first 5, the next 5 and the next 10 games back, each
as a share of his own last-10 pre-injury norm. Value is the band's mean per-game
line scaled to a week through the same lens; men whose pre-injury week was worth
less than 0.10 are left out of the value column (a ratio of marginals near zero
is noise), and men with fewer than five pre-injury games are left out of both.

| level | n | mp 1–5 | val 1–5 | mp 6–10 | val 6–10 | mp 11–20 | val 11–20 |
|---|---|---|---|---|---|---|---|
| 1–7 (control) | 308 | 1.01 | 1.00 | 0.99 | 0.95 | 0.99 | 1.03 |
| 8–14 | 1054 | 0.97 | 0.95 | 1.00 | 0.97 | 0.99 | 0.98 |
| 15–28 | 534 | 0.92 | 0.87 | 0.99 | 0.96 | 0.99 | 0.98 |
| 29+ | 523 | 0.90 | 0.82 | 0.97 | 0.94 | 0.99 | 0.92 |

**The ramp is real, it is small, and it is over in five games.** A man back from
a month out plays 90% of his old minutes and returns 82% of his old value in his
first five games; by games 6–10 he is at 97% and 94%, and by 11–20 the minutes
are indistinguishable from his norm. **A week back is about 3.3 games**, so in
weekly terms the discount is roughly **0.91 on the first week back and 0.98 on
the second, and nothing after that** — the two factors §4d applies.

The control row at 1.00 / 1.01 is the check: a man who missed a night or two
comes back at exactly his old rate, which is what "not a stash" should look
like.

By reason class (2026 only, limitation 2 — `injury` is the only row with an n
worth reading):

| reason class | n | mp 1–5 | val 1–5 | mp 6–10 | val 6–10 | mp 11–20 | val 11–20 |
|---|---|---|---|---|---|---|---|
| injury | 186 | 0.92 | 0.86 | 0.98 | 0.99 | 0.98 | 0.96 |
| rest / management | 13 | 1.04 | 0.82 | 1.05 | 0.97 | 1.15 | 1.36 |
| illness | 5 | 0.88 | 0.78 | 0.99 | 1.17 | 0.96 | 0.86 |
| concussion | 4 | 1.09 | 0.92 | 1.22 | 1.15 | 1.00 | 0.74 |
| G League | 3 | 0.99 | 0.77 | 1.26 | 2.01 | 1.39 | 1.42 |
| suspension | 2 | 0.84 | 0.36 | 0.93 | 0.46 | — | — |
| personal | 2 | 0.93 | 1.36 | 1.04 | 1.11 | 0.90 | 1.29 |
| not with team | 1 | 0.59 | 0.62 | 0.70 | 1.07 | 0.61 | 0.71 |

`injury` at 0.92 / 0.86 in the first five games is the same shape as the pooled
15–28 row, which is the honest reading: **the reason class adds nothing to the
ramp that the length of the absence has not already said.**

---

## 4. The break-even, and the table inverted

### 4a. By level

`n` is every scored decision (claims and holds) at that level; "back & held" is
the subset that returned while the team still held him, which is the only subset
with any post-return weeks to count.

| level | n | med dead wk | med cost | med benefit | med net | positive | back & held | med net of those | positive | med swap net | positive (swap) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1–7 (control) | 258 | 0.00 | 0.00 | 0.00 | −0.05 | 32.17% | 199 | −0.03 | 41.71% | 0.00 | 25.97% |
| 8–14 | 961 | 1.00 | 0.38 | 0.00 | −0.11 | **40.89%** | 709 | **+0.29** | **55.43%** | 0.00 | 36.42% |
| 15–28 | 487 | 1.71 | 0.65 | 0.00 | −0.16 | 28.34% | 290 | −0.03 | 47.59% | 0.00 | 29.16% |
| 29+ | 459 | 1.71 | 0.65 | 0.00 | −0.54 | **11.33%** | 119 | −0.08 | 43.70% | 0.00 | 13.51% |

**Read the "back & held" columns, because the headline columns are two different
things averaged together.** A stash either comes back while you still hold him —
709 of 961 at the 8–14 level, **119 of 459** at 29+ — or it does not, and a
stash that does not is a pure loss of 0.38 a dead week with nothing on the other
side. Conditional on him coming back and you still having him, **the median
8–14 day stash nets +0.29 and 55.43% of them pay.** Unconditionally it nets
−0.11 and 40.89% pay. The difference between those two numbers is the whole
risk, and §4d is the arithmetic of pricing it.

The 29+ row is the sharp one: 11.33% of month-plus stashes paid, and the reason
is not that the men were bad when they came back (their conditional median is
−0.08, no worse than 15–28's) — it is that **three quarters of them never came
back while the team still held them.**

### 4b. By his healthy value

His own last-10 pre-injury per-game line, scaled to the season's measured games
a week (3.24 to 3.56, from `pro_team_games`) and valued through
`standard_lens`:

| healthy tier | n | med healthy | med net | positive | med net a week back | med swap net | positive (swap) |
|---|---|---|---|---|---|---|---|
| <0.50 | 570 | 0.39 | −0.11 | 24.39% | −0.02 | 0.00 | 22.98% |
| 0.50–1.00 | 1265 | 0.68 | −0.18 | 30.28% | +0.13 | 0.00 | 27.67% |
| 1.00–2.00 | 151 | 1.14 | −0.22 | **41.72%** | **+0.34** | 0.00 | **45.03%** |
| 2.00+ | — | — | — | — | — | — | — |
| ? (norm under 5 games) | 179 | 0.37 | −0.05 | 45.25% | +0.19 | 0.00 | 40.22% |

**"How good is he" is the single strongest signal in the study, and it works on
the rate rather than the total.** The best tier's median *net a week back* is
+0.34 against −0.02 for the worst — a seventeenfold difference in the quantity
that has to repay the dead weeks — while the median *net* barely moves, because
the good men are also the men held through longer absences. The `?` tier is
interesting and should not be over-read: it is men with fewer than five games
behind them, which is disproportionately rookies and mid-season arrivals, and
they pay 45.25% of the time. Brandon Miller is one of them.

### 4c. By weeks left in the season at the decision

| weeks left | n | med net | positive | med weeks back | med swap net | positive (swap) |
|---|---|---|---|---|---|---|
| 1–4 | 865 | **−0.36** | **12.95%** | 0.00 | 0.00 | 12.95% |
| 5–9 | 566 | −0.09 | 39.05% | 3.00 | 0.00 | 37.81% |
| 10–15 | 537 | −0.05 | **45.44%** | 5.00 | 0.00 | 38.73% |
| 16+ | 197 | −0.05 | 45.18% | 3.00 | 0.00 | 44.16% |

**A stash with four weeks left is a different move from a stash with twelve, and
it is the commonest one made.** 865 of 2,165 decisions were taken with four
regular weeks or fewer remaining, and they paid 12.95% of the time; with ten to
fifteen weeks left it is 45.44%. There is nothing surprising in the mechanism —
a stash is a purchase of future weeks and late in the season there are none to
buy — but the *volume* is the finding: **forty percent of this league's stashing
happens in the window where it cannot work.**

### 4d. The inversion: *a man worth X a week is worth stashing if he is back within Y weeks with Z weeks left*

This is the table the what-if would read. For each healthy tier, the cell is the
**smallest number of regular weeks that must be left in the season** for the
expected net to be positive, given he is back within Y weeks:

    expected net = P(back within Y | 8 days out) × Σ(ramped weekly net over Z−Y weeks)
                   − 0.38 × Y

with P from §2a's 2020-out curve and the ramp from §3 (0.91 on the first week
back, 0.98 on the second, 1.00 after).

| worth X a week | measured net a week back | back in 1w | back in 2w | back in 3w | back in 4w | back in 6w | back in 8w |
|---|---|---|---|---|---|---|---|
| **<0.50** | +0.03 | 22 | never | never | never | never | never |
| **0.50–1.00** | +0.15 | **6** | **10** | **14** | **17** | never | never |
| **1.00–2.00** | +0.34 | **4** | **6** | **8** | **10** | **15** | **20** |
| 2.00+ | — | — | — | — | — | — | — |

**In one sentence, the rule this study produces:** *a man worth about a category
a week is worth stashing if he is back inside a fortnight and you have ten weeks
left; a man worth two thirds of a category needs the same fortnight and a full
half-season; and a man worth less than half a category is not worth stashing at
all, at any return date, in any week of any season.*

"never" means the search found no answer within twenty-four weeks left, which is
longer than a season. It is not rhetoric: at +0.03 a week back, even
twenty-two weeks of holding him repay about 0.66 categories *before* the return
odds are applied, while two dead weeks cost 0.76 outright. **The bottom tier —
570 of 2,165 decisions, more than a quarter of
all the stashing this league does — is unconditionally a losing move**, and the
one thing a page could say that would change behaviour is to say so with this
number beside it.

### 4e. The same figures with 2020 taken out

| population | n | med net | positive | 29+ n | 29+ med net | 29+ never back |
|---|---|---|---|---|---|---|
| all eight seasons | 2165 | −0.16 | 30.76% | 459 | −0.54 | 46.84% |
| **without 2020** | **1881** | **−0.11** | **32.43%** | **300** | **−0.26** | **26.00%** |
| 2020 alone | 284 | −1.44 | 19.72% | 159 | −5.59 | 86.16% |

The direction of every finding survives; the 29+ level's severity does not. Take
2020 out and a month-plus absence is a bad bet rather than a catastrophic one
(median −0.26, 26% never back, against −0.54 and 47%).

---

## 5. The stasher's situation

### By where the team stood that morning

Rank by categories banked in regular matchup periods finished before the
decision day, the way `app.pickups.judge.banked_record` counts a record; thirds
of the league by that rank. Decisions in a season's first weeks have no finished
period and rank 0, and drop out.

| standing on the day | n | med net | positive | med open places | med weeks left |
|---|---|---|---|---|---|
| top third | 629 | −0.05 | **30.84%** | 0.00 | 6.00 |
| middle third | 668 | −0.11 | 28.29% | 0.00 | 6.00 |
| bottom third | 791 | −0.16 | **24.40%** | 0.00 | 6.00 |

**The teams that stash most are the teams it works least for.** The bottom third
took 791 of the 2,088 ranked decisions against the top third's 629, and paid
24.40% of the time against 30.84%. The gap is real but it is not large, and it
is almost certainly not causal: a team in the bottom third has worse players, so
its stashes are drawn from the `<0.50` tier more often. **The situation is worth
carrying on the page as context and is not worth a hurdle of its own** — the
tier and the weeks-left are doing the work.

**Playoff odds were not available.** `docs/projected_record.md`'s engine
produces them, but the database holds **37 stored `team_reports` rows in total,
all 2026, all from a handful of days** — there is no stored projection to read
for a decision day in 2019. The record rank above is the fallback the brief
allows, and a future run against a season of stored projections could replace it
without changing anything else.

### Roster room, and why the column is almost all zeros

| open places | n | med net | positive |
|---|---|---|---|
| 0 | 2099 | −0.11 | 27.68% |
| 1 | 11 | −0.16 | 18.18% |
| 2+ | 1 | −0.22 | 0.00% |

**2,099 of 2,111 stash decisions in eight seasons were taken with a completely
full roster.** This league's teams carry nine starting slots and three bench
slots and they carry them every day. There is no such thing here as stashing
into a spare place: **every stash is a drop**, and the 0.38 a week is charged in
full for every dead day.

### What the dead place would really have returned

`OPENED_PLACE` is 0.38 — `docs/streaming_lane.md`'s pooled per-place median over
1,536 team-periods. Beside it, the same measurement taken on **the stashing
team's own** open places in that season, from
`scripts/streaming_lane.py`'s `TeamPeriod.streamed_per_place_value`:

| season | n | flat 0.38 | that team's own per place | its dead cost | the flat dead cost |
|---|---|---|---|---|---|
| 2019 | 132 | 0.38 | 0.30 | 0.15 | 0.38 |
| 2020 | 280 | 0.38 | 0.24 | 0.21 | 0.92 |
| 2021 | 224 | 0.38 | 0.43 | 0.24 | 0.35 |
| 2022 | 276 | 0.38 | 0.34 | 0.17 | 0.43 |
| 2023 | 277 | 0.38 | 0.09 | 0.00 | 0.43 |
| 2024 | 279 | 0.38 | 0.35 | 0.17 | 0.27 |
| 2025 | 295 | 0.38 | 0.39 | 0.22 | 0.27 |
| 2026 | 348 | 0.38 | 0.33 | 0.25 | 0.33 |

**The flat charge is fair and slightly harsh.** The median stashing team's own
streamed place returned 0.24 to 0.43 a week depending on the season, against the
0.38 charged, and its median realised dead cost is below the flat one in every
season. 2023 is the outlier at 0.09, which is a season in which this league's
teams barely streamed at all. **Nothing here asks for `OPENED_PLACE` to move**;
it asks for the team's own number to be available to a caller that wants it,
which `app.calibration` already provides for.

### The formula for a league with an injured-reserve slot

`league_seasons.injured_reserve_slots` is **0 in all nine stored seasons** and
there is not one `IR` row in eight seasons of `daily_lineup_slots`. So every
number above is the no-IR case. For a league with IR the accounting changes in
exactly one term:

    dead cost = 0.38 × dead weeks × (1 − share of the dead weeks he spends on IR)

A man who goes to IR the day he is claimed and comes off it the day he returns
costs **nothing** for the wait, and the whole of §4d collapses: every cell that
reads "never" becomes "any week he returns at all", because the left-hand side
of the inequality loses its only negative term. **In a league with a free IR
slot a stash is close to a free option and the only question is whether he is
worth a place when he is back.** `app.pickups.state.TeamWeek.ir_slot_free`
already carries the fact and `app.inseason.what_if` already accepts a `to_ir`
change, so the gate exists; nothing reads it for a stash yet.

One discrepancy worth recording: `docs/pickups.md` §4.4 and
`app/pickups/season.py`'s docstring both say the league carries **one** IR slot
from 2027. The stored 2027 `league_seasons` row says **0**. Either the settings
have not been re-ingested since the change or the change was not made; a stash
mode must read the stored setting and not the sentence.

---

## 6. The league's stashes, named

### 6a. Every 2026 claimed stash

"back in" is days from the claim to his next played game (— = never that
season); "held" is days the claiming team held him in the run the claim opened;
"cost" is 0.38 × dead weeks; "benefit" is Σ(weekly value − replacement) over the
weeks held after return; "net" is benefit − cost; "swap" is the single-currency
arm of limitation 3.

| day | team | player | d out | missed | paid | back in | held | cost | benefit | net | swap |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 11 | Optimize the MVPs | Zach Edey | 10 | 5 | $0 | 15 | 41 | 0.81 | 0.75 | −0.06 | −1.50 |
| 14 | Through The Wire | Bennedict Mathurin | 9 | 4 | $5 | 14 | 30 | 0.76 | 2.04 | 1.28 | −0.50 |
| 15 | Chat GTP inspired | Jared McCain | 14 | 6 | $3 | 0 | 5 | 0.00 | −0.16 | −0.16 | 0.00 |
| 15 | Optimize the MVPs | Isaiah Collier | 14 | 7 | $0 | 3 | 1 | 0.05 | 0.00 | −0.05 | 0.00 |
| 16 | Optimize the MVPs | Sam Merrill | 9 | 3 | $0 | 0 | 18 | 0.00 | 0.50 | 0.50 | 1.00 |
| 19 | Through The Wire | Bennedict Mathurin | 14 | 6 | $0 | 9 | 25 | 0.49 | 2.06 | 1.57 | −1.00 |
| **23** | **Through The Wire** | **Brandon Miller** | **18** | **8** | **$2** | **10** | **137** | **0.54** | **1.99** | **+1.45** | **+4.50** |
| 24 | Optimize the MVPs | Jaden Ivey | 23 | 12 | $2 | 9 | 35 | 0.49 | −1.76 | −2.25 | −1.50 |
| 25 | The Infirmary | Dominick Barlow | 20 | 9 | $0 | 0 | 6 | 0.00 | 0.30 | 0.30 | −0.50 |
| 29 | The Infirmary | Keegan Murray | 28 | 14 | $4 | 2 | 101 | 0.11 | 1.47 | 1.36 | 4.00 |
| 32 | BC KO | Jaylon Tyson | 11 | 5 | $0 | 0 | 110 | 0.00 | 6.14 | **6.14** | 9.00 |
| 33 | Fantastic 5 | Tobias Harris | 21 | 9 | $2 | 0 | 40 | 0.00 | 3.41 | 3.41 | 4.50 |
| 37 | The Infirmary | Kevin Porter Jr. | 35 | 17 | $1 | 3 | 103 | 0.16 | 5.43 | 5.27 | 5.00 |
| 38 | Optimize the MVPs | Jordan Poole | 23 | 12 | $0 | 14 | 4 | 0.22 | 0.00 | −0.22 | −0.50 |
| 41 | Chat GTP inspired | Aaron Wiggins | 25 | 11 | $1 | 0 | 0 | 0.00 | −0.03 | −0.03 | 0.00 |
| 44 | BC KO | Kelly Oubre Jr. | 19 | 8 | $0 | 35 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 44 | Through The Wire | Jordan Poole | 29 | 15 | $6 | 8 | 15 | 0.43 | 0.76 | 0.33 | 1.00 |
| 46 | Optimize the MVPs | Kelly Oubre Jr. | 21 | 9 | $2 | 33 | 96 | 1.79 | 4.26 | 2.47 | 3.50 |
| 46 | Foxes ShutUpNDribb | Cam Thomas | 30 | 14 | $0 | 22 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 49 | Chat GTP inspired | Herbert Jones | 17 | 8 | $2 | 0 | 14 | 0.00 | 0.57 | 0.57 | 0.00 |
| 51 | Uncle Dennis's Pho | Alex Caruso | 12 | 4 | $0 | 0 | 3 | 0.00 | −0.11 | −0.11 | 0.00 |
| 51 | Fantastic 5 | Luguentz Dort | 10 | 3 | $2 | 0 | 3 | 0.00 | −0.11 | −0.11 | 0.00 |
| 53 | Masters of their D | Jalen Smith | 14 | 5 | $0 | 0 | 5 | 0.00 | −0.67 | −0.67 | 0.50 |
| 55 | Fantastic 5 | Tari Eason | 30 | 11 | $5 | 7 | 105 | 0.38 | −0.23 | −0.61 | −2.50 |
| 57 | Through The Wire | Jalen Green | 38 | 16 | $1 | 35 | 6 | 0.33 | 0.00 | −0.33 | 0.00 |
| 59 | Optimize the MVPs | Aaron Gordon | 27 | 10 | $0 | 17 | 41 | 0.92 | 2.28 | 1.36 | 0.00 |
| 60 | Fast and Curryous | Collin Sexton | 16 | 5 | $1 | 4 | 2 | 0.11 | 0.00 | −0.11 | −1.50 |
| 63 | LeBron's Load Mana | Cam Thomas | 47 | 19 | $0 | 5 | 30 | 0.27 | −0.71 | −0.98 | −2.00 |
| 65 | Fast and Curryous | Dejounte Murray | 64 | 31 | $1 | 62 | 8 | 0.43 | 0.00 | −0.43 | 0.00 |
| 70 | Fast and Curryous | Kristaps Porzingis | 24 | 9 | $3 | 2 | 11 | 0.11 | −0.28 | −0.39 | −0.50 |
| 70 | Brighton Bears | Ivica Zubac | 9 | 3 | $11 | 5 | 43 | 0.27 | −0.59 | −0.86 | −0.50 |
| 71 | Through The Wire | Grayson Allen | 16 | 6 | $1 | 6 | 76 | 0.33 | 3.74 | 3.42 | 0.50 |
| 72 | Chat GTP inspired | Christian Braun | 49 | 21 | $2 | 4 | 12 | 0.22 | 0.25 | 0.03 | −1.50 |
| 73 | The Infirmary | Jalen Green | 54 | 23 | $0 | 19 | 86 | 1.03 | 2.75 | 1.71 | 1.00 |
| 75 | Masters of their D | Herbert Jones | 12 | 6 | $2 | 3 | 7 | 0.16 | −1.14 | −1.30 | −1.00 |
| 79 | Optimize the MVPs | Zach Edey | 31 | 12 | $0 | — | 6 | 0.33 | 0.00 | −0.33 | 0.00 |
| 80 | Uncle Dennis's Pho | D'Angelo Russell | 12 | 4 | $0 | 2 | 1 | 0.05 | 0.00 | −0.05 | 0.00 |
| 80 | Ben's Need Some VC | Jerami Grant | 21 | 11 | $0 | 7 | 80 | 0.38 | −0.92 | −1.30 | −1.00 |
| 82 | Fast and Curryous | Ryan Kalkbrenner | 21 | 10 | $0 | 0 | 28 | 0.00 | 0.24 | 0.24 | −2.50 |
| 84 | BC KO | Domantas Sabonis | 57 | 25 | $0 | 4 | 37 | 0.22 | 1.12 | 0.91 | −1.00 |
| 85 | Chat GTP inspired | Saddiq Bey | 13 | 6 | $0 | 0 | 75 | 0.00 | 7.06 | **7.06** | 5.00 |
| 87 | Fantastic 5 | Tobias Harris | 16 | 5 | $3 | 0 | 64 | 0.00 | 4.12 | 4.12 | 4.00 |
| 88 | Through The Wire | Isaiah Hartenstein | 19 | 10 | $0 | 13 | 3 | 0.16 | 0.00 | −0.16 | 0.00 |
| 91 | Fantastic 5 | Bennedict Mathurin | 17 | 8 | $7 | 7 | 57 | 0.38 | 0.37 | −0.01 | −0.50 |
| 95 | LeBron's Load Mana | Isaiah Hartenstein | 26 | 13 | $0 | 6 | 65 | 0.33 | −1.10 | −1.43 | −5.00 |
| 97 | BC KO | Devin Vassell | 27 | 13 | $3 | 0 | 1 | 0.00 | −0.06 | −0.06 | 0.00 |
| 97 | Uncle Dennis's Pho | Tolu Smith | 15 | 5 | $1 | 17 | 3 | 0.16 | 0.00 | −0.16 | 0.00 |
| 98 | Masters of their D | Sam Merrill | 12 | 5 | $0 | 2 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 99 | Through The Wire | Bilal Coulibaly | 13 | 5 | $1 | 0 | 7 | 0.00 | 1.27 | 1.27 | 1.00 |
| 100 | Through The Wire | Sam Merrill | 14 | 6 | $3 | 0 | 0 | 0.00 | 0.97 | 0.97 | 1.00 |
| 102 | Brighton Bears | Ajay Mitchell | 9 | 4 | $3 | 38 | 5 | 0.27 | 0.00 | −0.27 | 0.00 |
| 103 | Through The Wire | Ty Jerome | 102 | 47 | $0 | 0 | 29 | 0.00 | 2.12 | 2.12 | 0.50 |
| 106 | Through The Wire | Quenton Jackson | 8 | 3 | $0 | 0 | 0 | 0.00 | 1.47 | 1.47 | 1.50 |
| 107 | Optimize the MVPs | Kristaps Porzingis | 28 | 13 | $2 | 15 | 25 | 0.81 | −0.28 | −1.09 | −1.00 |
| 108 | Through The Wire | Tristan Vukcevic | 22 | 10 | $1 | 0 | 3 | 0.00 | −0.10 | −0.10 | 0.00 |
| 109 | Optimize the MVPs | Scoot Henderson | 108 | 51 | $1 | 0 | 24 | 0.00 | 1.86 | 1.86 | 1.00 |
| 112 | Through The Wire | Giannis Antetokounmpo | 17 | 7 | $3 | 21 | 35 | 1.14 | −1.15 | −2.29 | −1.50 |
| 116 | BC KO | Dejounte Murray | 115 | 56 | $0 | 11 | 44 | 0.60 | 2.23 | 1.63 | 0.00 |
| 116 | Optimize the MVPs | Trae Young | 48 | 23 | $0 | 20 | 5 | 0.27 | 0.00 | −0.27 | 0.00 |
| 117 | Fast and Curryous | Ivica Zubac | 12 | 5 | $0 | 26 | 4 | 0.22 | 0.00 | −0.22 | 0.00 |
| 118 | Foxes ShutUpNDribb | Ja Morant | 25 | 12 | $0 | — | 3 | 0.16 | 0.00 | −0.16 | 0.00 |
| 122 | Fast and Curryous | Quentin Grimes | 12 | 2 | $1 | 0 | 0 | 0.00 | −0.02 | −0.02 | 0.00 |
| 122 | Foxes ShutUpNDribb | Malik Monk | 13 | 3 | $0 | 0 | 4 | 0.00 | −0.10 | −0.10 | 0.00 |
| 122 | Through The Wire | Tristan Vukcevic | 11 | 1 | $0 | 0 | 4 | 0.00 | 0.40 | 0.40 | 0.00 |
| 123 | Through The Wire | Moussa Diabate | 11 | 2 | $0 | 4 | 12 | 0.22 | 0.81 | 0.59 | 0.50 |
| 124 | Fast and Curryous | Kevin Huerter | 10 | 1 | $0 | 4 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 125 | Chat GTP inspired | Nic Claxton | 13 | 3 | $0 | 0 | 6 | 0.00 | −0.15 | −0.15 | 0.00 |
| 127 | The Infirmary | Trae Young | 59 | 26 | $1 | 9 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 129 | Optimize the MVPs | Jayson Tatum | 128 | 58 | $0 | 8 | 31 | 0.43 | 3.42 | 2.98 | 0.00 |
| 130 | Through The Wire | Aaron Gordon | 35 | 14 | $0 | 7 | 30 | 0.38 | −0.08 | −0.46 | 0.00 |
| 131 | The Infirmary | Jalen Williams | 17 | 6 | $1 | 23 | 8 | 0.43 | 0.00 | −0.43 | 0.00 |
| 133 | Through The Wire | Peyton Watson | 26 | 9 | $0 | 20 | 8 | 0.43 | 0.00 | −0.43 | 1.00 |
| 134 | LeBron's Load Mana | Trae Young | 66 | 30 | $15 | 2 | 26 | 0.11 | −0.14 | −0.25 | 0.00 |
| 134 | Through The Wire | Franz Wagner | 20 | 6 | $0 | 29 | 5 | 0.27 | 0.00 | −0.27 | 1.00 |
| 136 | The Infirmary | Ja Morant | 43 | 20 | $0 | — | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 140 | Ben's Need Some VC | Ajay Mitchell | 47 | 20 | $1 | 0 | 20 | 0.00 | 0.00 | 0.00 | 0.00 |
| 141 | Optimize the MVPs | Jalen Williams | 27 | 11 | $0 | 13 | 6 | 0.33 | 0.00 | −0.33 | 0.00 |
| 142 | Through The Wire | Kevin Porter Jr. | 9 | 4 | $0 | 1 | 18 | 0.05 | 0.00 | −0.05 | 0.00 |
| 143 | BC KO | Jonathan Kuminga | 11 | 3 | $0 | 0 | 1 | 0.00 | 0.00 | 0.00 | 0.00 |
| 143 | LeBron's Load Mana | Ivica Zubac | 38 | 15 | $4 | 0 | 9 | 0.00 | 0.00 | 0.00 | 0.00 |
| 143 | Fantastic 5 | Alex Caruso | 8 | 2 | $0 | 0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 147 | Ben's Need Some VC | Lauri Markkanen | 21 | 10 | $3 | — | 5 | 0.27 | 0.00 | −0.27 | 0.00 |
| 152 | Optimize the MVPs | Jalen Williams | 38 | 15 | $0 | 2 | 8 | 0.11 | 0.00 | −0.11 | 0.00 |
| 153 | Brighton Bears | Peyton Watson | 46 | 19 | $0 | 0 | 7 | 0.00 | 0.00 | 0.00 | 0.00 |
| 154 | The Infirmary | Andrew Wiggins | 18 | 8 | $10 | 0 | 6 | 0.00 | 0.00 | 0.00 | 0.00 |
| 154 | BC KO | Jarrett Allen | 20 | 8 | $0 | 4 | 6 | 0.22 | 0.00 | −0.22 | 0.00 |
| 154 | BC KO | Giannis Antetokounmpo | 8 | 3 | $0 | — | 6 | 0.33 | 0.00 | −0.33 | 0.00 |
| 154 | BC KO | Michael Porter Jr. | 13 | 6 | $0 | — | 6 | 0.33 | 0.00 | −0.33 | 0.00 |
| 155 | LeBron's Load Mana | Paul George | 54 | 25 | $14 | 1 | 5 | 0.05 | 0.00 | −0.05 | 0.00 |
| 156 | Fantastic 5 | Bennedict Mathurin | 9 | 4 | $3 | 0 | 4 | 0.00 | 0.00 | 0.00 | 0.00 |
| 157 | Through The Wire | Anfernee Simons | 33 | 15 | $0 | — | 3 | 0.16 | 0.00 | −0.16 | 0.00 |

Two things jump off that list.

**The last month is noise.** Every claim from day 140 on nets between 0.00 and
−0.33, because there are no weeks left for a return to be worth anything in.
Twenty of the ninety-one stashes were made after day 130.

**The men worth stashing were not the famous ones.** The four biggest nets are
Saddiq Bey (+7.06, $0, thirteen days out), Jaylon Tyson (+6.14, $0, eleven days
out), Kevin Porter Jr. (+5.27, $1, thirty-five days out) and Tobias Harris
(+4.12, $3). Giannis Antetokounmpo at day 112 cost his claimer −2.29, and Paul
George at $14 on day 155 returned nothing at all because the season was over.

### 6b. Brandon Miller, worked through

The owner's own move, in full, as the script prints it.

**The facts.** Charlotte's Brandon Miller last played **2026 scoring period 5**.
He then missed thirteen straight Charlotte games — days **6, 8, 10, 12, 13, 15,
18, 21, 23, 25, 26, 28, 30**. His first team (row 87) dropped him on day 8, one
day into the absence. **Through The Wire claimed him on day 23 for $2**, at
which point he was **18 days out** with **8 team games missed** and the NBA's
report that morning had him **Out (injury)**. He played again on **day 33** —
**10 days after the claim** — for 26 minutes and 21 points, and the team held him
to **day 160, the season's last day, 137 days.**

**The accounting, term by term.**

| term | value | how |
|---|---|---|
| dead days | 10 | claim day 23 → return day 33 |
| dead weeks | 1.43 | 10 ÷ 7 |
| **dead cost** | **0.54** | 1.43 × `OPENED_PLACE` 0.38 |
| the wire's replacement that morning | 0.20 a week | best free agent by `standard_lens`, floored at `TYPICAL_PICKUP` |
| replacement charged | 3.01 | 0.20 × 15 post-return weeks, pro-rated by period length |
| his weekly value, 15 weeks | 0.00, 0.00, 0.00, 1.00, 1.00, 0.00, 0.00, 1.50, 0.50, −0.50, 2.00, 1.00, −0.50, 0.00, −1.00 | `-Replay.delta(team, week, [Miller], [])` |
| Σ his weekly value | 5.00 | |
| **benefit** | **1.99** | 5.00 − 3.01 |
| **net (declared)** | **+1.45** | 1.99 − 0.54 |
| **net (swap arm)** | **+4.50** | him against player 516, the best free agent of day 23, over all 16 weeks of the hold |

**The two arms disagree by three categories and the swap arm is the one to
believe.** The declared arm charges him 3.01 categories of replacement over
fifteen weeks — the lens's best-free-agent number, applied week after week — and
the swap arm asks the sharper question: put the actual best free agent of that
morning in the place instead, replay both, and count. The answer is that Miller
won Through The Wire **four and a half categories** over the whole hold,
including the fortnight when he was not playing.

**What the study says about the move before it knew the answer.** He was 18 days
out, which is the 15–28 level: 92.51% of those return, the median after 15.5
days. The 2020-out prior at 14 days out says 36.44% within a week and 52.79%
within two. He came back in ten. There were **16 regular weeks left**, and the
claiming team was **first in the standings**. In §4d's terms, a man back within
two weeks with sixteen weeks left clears the bar for every tier above 0.50 a
week and misses it only for the bottom one. **His measured healthy value was
0.16 a week on two games** — which is why he is in the `?` tier and not in any
other (limitation 7). That is the honest limit of what this study could have
told the owner on the morning of day 23: *the return odds were good, the season
was long, and nobody could price the man, because he had played twice.*

**And the second row is the other half of the story.** The team that *held* him
— row 87 — met the same absence on day 6, held him one more day and dropped him
on day 8. That decision is in this census as a DROPPED hold at the 29+ level,
net −0.05. It cost that manager almost nothing to walk away, and it handed
Through The Wire a +4.50 for $2. **A drop is cheap and it is not free, and the
thing it costs is invisible in the dropper's own numbers.**

### 6c. The best and the worst in eight seasons

| season | kind | outcome | player | day | d out | back in | wk back | cost | net | swap |
|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | hold | kept | Paul George | 36 | 16 | 14 | 11 | 0.76 | **12.43** | 6.50 |
| 2021 | hold | kept | Devin Booker | 33 | 10 | 9 | 12 | 0.49 | 12.14 | 14.50 |
| 2025 | hold | kept | Nikola Jokic | 25 | 12 | 7 | 13 | 0.38 | 11.52 | 14.50 |
| 2024 | hold | kept | Devin Booker | 12 | 13 | 11 | 16 | 0.60 | 11.18 | 12.00 |
| 2024 | hold | kept | Devin Booker | 3 | 9 | 7 | 18 | 0.38 | 11.11 | 17.50 |
| 2024 | claim | kept | Miles Bridges | 20 | 19 | 5 | 16 | 0.27 | **10.93** | 7.00 |
| 2022 | hold | kept | Luka Doncic | 30 | 8 | 6 | 11 | 0.33 | 10.88 | 11.00 |
| 2026 | hold | kept | Kevin Durant | 35 | 9 | 6 | 14 | 0.33 | 10.69 | 10.50 |
| 2025 | hold | kept | Victor Wembanyama | 26 | 8 | 7 | 13 | 0.38 | 10.36 | 10.50 |
| 2022 | hold | kept | Nikola Jokic | 32 | 11 | 10 | 10 | 0.54 | 10.31 | 6.00 |
| 2023 | hold | kept | Mike Conley | 36 | 20 | 18 | 11 | 0.98 | −5.68 | −5.50 |
| 2020 | hold | kept | Giannis Antetokounmpo | 139 | 41 | — | 0 | 5.70 | −5.70 | 0.00 |
| 2020 | hold | kept | Shai Gilgeous-Alexander | 139 | 41 | — | 0 | 5.70 | −5.70 | 0.00 |
| 2020 | hold | kept | Jae Crowder | 139 | 40 | — | 0 | 5.70 | −5.70 | 0.00 |
| 2020 | hold | kept | Seth Curry | 139 | 41 | — | 0 | 5.70 | −5.70 | 0.00 |
| 2020 | hold | kept | Evan Fournier | 137 | 43 | — | 0 | 5.81 | −5.81 | 0.00 |
| 2024 | hold | kept | Ben Simmons | 16 | 84 | 82 | 5 | 4.45 | −6.00 | −6.50 |
| 2020 | hold | kept | Ben Simmons | 126 | 54 | — | 0 | 6.41 | −6.41 | 0.00 |
| 2025 | hold | kept | Immanuel Quickley | 22 | 52 | 50 | 6 | 2.71 | −6.44 | −6.50 |
| 2025 | hold | kept | Immanuel Quickley | 4 | 17 | 15 | 14 | 0.81 | **−7.08** | −7.00 |

**The best stash in eight seasons is Paul George in 2023 at +12.43, and nine of
the top ten are holds rather than claims.** That is not a fact about stashing —
it is a fact about who the best players are. A manager who drafted Jokic or
Doncic and held him through a fortnight gets credited here with an enormous net,
because his alternative was the wire. **The best *claim* in eight seasons is
Miles Bridges, 2024, day 20, nineteen days out, back in five days, +10.93** —
and that is the shape of the move the product could actually help somebody make.

The worst list is 2020's stoppage and two genuine disasters: Ben Simmons held
through eighty-two days in 2024 for −6.00, and Immanuel Quickley twice in 2025,
for −7.08 and −6.44. **The five 2020 rows at exactly −5.70 are the suspension**
(§Limitations 5) and should be read as an artefact.

---

## 7. What the engine projects for a stash today, scored

> **Superseded twice on 2026-09-24, the first time by this document's own
> recommendation.** The rule below was replaced by the one declared in
> [`stash_mode.md`](stash_mode.md), and then the status source was replaced by
> the one declared in [`replay_status.md`](replay_status.md), so a claim
> morning now reads the NBA's own report. `scripts/stashes.py`'s section 7
> prints four columns: what the league said that morning, what the engine said
> (0.00 for every stash), what the rule says off this study's own instrument,
> and what it says through `build_players` — the path a report really takes.
> The re-scored figures are in `stash_mode.md`'s own section 7.
> Everything in this section is the state of the engine **before** those
> changes, and is kept as written because it is the measurement that caused
> them.
>
> One thing in it turned out to be understated. §Limitations 9 says this
> database holds no `expected_return_date` and leaves open whether that is
> ESPN's doing or ours. It is ESPN's, and absolutely: a read-only probe of the
> league's own player pool on 2026-09-24 found **no such field on the player
> object at all**, across 1,097 entries of which 136 were OUT, in either kona
> view. There was never a date to be missing.

`app.pickups.state.playable_days` removes every day before ESPN's
`expected_return_date` for a man whose status is in `RULED_OUT_STATUSES` (OUT,
SUSPENSION), and **every day at all when there is no date**. That flows into
`rest_of_season_line`, which multiplies a per-game rate by the games that
survive, so an OUT man with no date has a rest-of-season line of zero, a weekly
value of zero and a judgement of zero.

**This database holds 0 rows with a non-null `expected_return_date` in
`player_status_snapshots`, in any season.** Therefore:

**For all 91 of 2026's claimed stashes, the recommender's projected value on the
claim morning is exactly 0.00.** Against that, the top of what happened:

| day | player | d out | engine says | he delivered | error | weeks held after return |
|---|---|---|---|---|---|---|
| 32 | Jaylon Tyson | 11 | 0.00 | 9.00 | **9.00** | 15 |
| 85 | Saddiq Bey | 13 | 0.00 | 8.50 | 8.50 | 7 |
| 37 | Kevin Porter Jr. | 35 | 0.00 | 8.00 | 8.00 | 14 |
| 46 | Kelly Oubre Jr. | 21 | 0.00 | 6.00 | 6.00 | 8 |
| 71 | Grayson Allen | 16 | 0.00 | 5.50 | 5.50 | 8 |
| 87 | Tobias Harris | 16 | 0.00 | 5.50 | 5.50 | 7 |
| **23** | **Brandon Miller** | **18** | **0.00** | **5.00** | **5.00** | **15** |
| 33 | Tobias Harris | 21 | 0.00 | 4.50 | 4.50 | 7 |
| 29 | Keegan Murray | 28 | 0.00 | 4.00 | 4.00 | 14 |
| 73 | Jalen Green | 54 | 0.00 | 4.00 | 4.00 | 6 |
| 129 | Jayson Tatum | 128 | 0.00 | 3.50 | 3.50 | 1 |
| 59 | Aaron Gordon | 27 | 0.00 | 3.00 | 3.00 | 5 |
| 103 | Ty Jerome | 102 | 0.00 | 3.00 | 3.00 | 4 |
| … | (the remaining 78) | | 0.00 | | | |

**The size of the error, stated exactly: 53 of the 91 returned and were held,
and over the weeks they were held they delivered 94.50 categories in total — a
mean of 1.04 a stash, a median of 0.00, a maximum of 9.00.** Because the engine
says 0.00 for every one of them, its mean error on this population **is** +1.04
categories a stash. Four of the ninety-one delivered less than zero (Jaden Ivey
−1.00, Herbert Jones −1.00, Giannis Antetokounmpo −1.00, Jalen Smith −0.50), so
it is not quite one-directional — but thirteen of them delivered three
categories or more, and the engine is silent about every one.

For comparison: `docs/availability.md` §3c reports the recommender's whole
published error at about 0.47 categories a week and the entire status-conditional
games term as worth 0.024 of it. **The stash error is not a refinement of the
same size — it is a projection of zero for a man who is worth a category a
week.**

**There is a stash lane in the product already and it cannot fire.**
`app/pickups/season.py`'s `_stashes` looks for free agents whose ESPN status is
OUT **with an `expected_return_date` inside six weeks**, values them as if
healthy, and lists them. With no return date ever stored, the filter is empty on
every call and the list is always `()`. The design in `docs/pickups.md` §4.4 is
right; it is gated on a field that has never been populated.

---

## What this means for the product

### 1. The what-if stash mode, as a spec

This is the thing to build, and it is a mode of `app.inseason.what_if.what_if`
rather than a new engine. A stash is already expressible as a `Change` (an add,
a drop, optionally a `to_ir`); what is missing is that the projection values the
added man at zero and the judgement charges nothing for the wait.

**Inputs** (all available on a live morning except where noted):

| input | source | note |
|---|---|---|
| days out so far | `player_game_stats`, his last `played = true` row before today | the box-score count of §0, **not** the report's |
| team games missed so far | the same rows | printed beside it, never instead of it |
| reason class | `app.injuries.status_as_of` + `scripts/availability.py`'s `reason_class` | present 2022+ on the VPS, 2026 only locally; **absent is normal and must not be an error** |
| ESPN's `expected_return_date` | `player_status_snapshots` | **optional, and today always null**; when present it overrides the prior for the mean, never for the spread |
| his healthy value | `rest_of_season_line` counting **every** remaining game of his NBA team, through `standard_lens`, ÷ weeks | this is what `_stashes` already does and it is right |
| the norm's depth | played games behind the line | under `MIN_NORM` = 5 the page must say "too few games to price him" rather than print a number |
| weeks left | `whole_weeks_after`, regular periods only | |
| IR slots free | `TeamWeek.ir_slot_free`, from `league_seasons.injured_reserve_slots` | 0 in this league; the gate already exists |
| the wire's replacement | `app.pickups.stream.evaluated_wire` + `spot_book` | the same wire the week report reads, so the two cannot disagree |

**Outputs:**

1. **P(back by week k), k = 1…weeks left.** Read off §2a's 2020-out table by
   days out so far, interpolated between the five N rows and the five M columns.
   Shipped as a constant table, not recomputed live — it is a measurement, and
   re-measuring it per request would make it drift.
2. **Expected net**, the §4d arithmetic made continuous:

   ```
   expected_net = Σ_k  P(back in week k) × Σ_{j=1..weeks_left−k} ramp(j) × weekly_net
                  − opened × E[dead weeks]
   ```

   with `weekly_net = healthy_value − wire_replacement`, `ramp(1) = 0.91`,
   `ramp(2) = 0.98`, `ramp(j) = 1.00` for j ≥ 3, `opened` from
   `app.calibration` (0.38 by default), and `E[dead weeks]` the mean of the
   return distribution truncated at the weeks left. **When an IR slot is free,
   `opened` is multiplied by the share of the wait he cannot spend on IR**,
   which for a full-season IR slot is zero.
3. **The two weeks after the return, ramped**, in the week layer: his first week
   back is his line × 0.91 and his second × 0.98, so the `WeekLayer` a manager
   reads for "the week he comes back" is not his healthy week.
4. **The odds beside the number, never a verdict.** The page says *"he is 53%
   to be back inside a fortnight; if he is, he is worth +0.34 a week against
   your wire for the eleven weeks after that; holding the place costs 0.38 a
   week while you wait; the expected net is +1.9"* — and it stops. The bar
   labels and never hides (`docs/product.md`, the owner's rule); a stash under
   the bar still appears with its number and its odds.

**What the page must not do.** It must not print a single "return date". The
data contains none — `docs/availability.md` §2 found a timeline word in 0 of
6,987 Out runs, and this database holds no ESPN return date at all. A date would
be a fabrication with a spread of three weeks behind it. **The distribution is
the answer, and a distribution is what a manager can act on:** "half of them are
back inside a fortnight" is a true sentence; "back on 14 December" is not.

**The hurdle.** A stash's expected net is a rest-of-season quantity and belongs
against `SEASON_HURDLE_PAID` (0.20 a week) and `SEASON_HURDLE_FREE` (0.10),
divided by the weeks left the same way every other season move is — **not
against a new bar.** This study measures outcomes; it does not measure the
recommender, and a hurdle invented here would have no backtest under it.

### 2. The return prior the engine should use instead of "zero forever"

`playable_days`'s current rule — OUT with no date means zero games for the rest
of the season — is not a conservative assumption, it is a wrong one. §2a says a
man **28 days out** still plays again within four weeks **52.41%** of the time.
The replacement is this, and it is a table, not a model:

| days out so far | P(back in 1d) | 3d | 7d | 14d | 28d |
|---|---|---|---|---|---|
| 1 | 0.001 | 0.383 | 0.681 | 0.823 | 0.884 |
| 3 | 0.246 | 0.500 | 0.707 | 0.829 | 0.890 |
| 7 | 0.129 | 0.332 | 0.547 | 0.700 | 0.802 |
| 14 | 0.078 | 0.208 | 0.364 | 0.528 | 0.666 |
| 28 | 0.024 | 0.098 | 0.205 | 0.361 | 0.524 |

**How to wire it without touching what works.** `playable_days` counts *days*,
and a probability is not a day. The clean change is one line in
`rest_of_season_line`: where the games count is currently `playable_days(...) ×
ESPN_AVAILABILITY`, an OUT man with no date gets `his team's remaining games ×
P(back by day d)` summed over the remaining days — which is the same shape, the
same function signature and the same single multiplication, with a curve in
place of a cliff. **The week report and the startable lane must not change**:
a man OUT tonight is out tonight, and `app.inseason.startable` is right to seat
nobody. The prior belongs to the *season* horizon only, which is exactly the
distinction `docs/availability.md` §1c exists to prevent people from blurring.

Two cautions, both from this study. The prior is measured on box-score days out
and is **not interchangeable with a report-days-out count** (§2b): one
box-score day is about two report days. And the prior must drop the suspended
season — the eight-season curve reads 38.64% where the seven-season one reads
52.41%, and a product that priced a month-out stash off the wrong one would
undervalue it by a quarter.

### 3. The settings gates

Three, and all three already exist as fields:

* **`injured_reserve_slots`.** 0 here, so every stash is a drop and pays 0.38 a
  dead week. In a league with a free slot the dead cost is multiplied by the
  share of the wait he cannot spend on IR, and §4d's "never" cells vanish. Read
  the stored setting; `docs/pickups.md` says the league gets one in 2027 and the
  stored 2027 row says 0 (§5), which is exactly the sort of disagreement a gate
  is for.
* **`acquisition_limit` / adds a period.** Seven adds a matchup period, shared
  with streaming. A stash spends one of them and then holds a place that cannot
  be streamed, so it costs the lane twice; the 0.38 charge prices the second
  cost and nothing prices the first. Not measured here, and named so it is not
  forgotten.
* **`uses_faab`.** 2026 on. A stash that costs FAAB should face the paid bar
  and one that does not the free bar, which is `app.pickups.season`'s existing
  rule and needs no new number.

### 4. Nothing is wired by this document

> **Accepted and built, 2026-09-24.** The owner took proposals 1, 2 and 3
> above. The rule is `app/pickups/returns.py`, the wait is
> `app/pickups/stash.py`, and [`stash_mode.md`](stash_mode.md) is the record:
> what was declared before anything ran, what the three calibrations said,
> and what section 7 above now scores. No constant moved and no hurdle moved,
> which is what proposals 1 and 2 asked for.

No constant moved, no route changed, no page edited, no file in `app/` touched.
It adds one script and one document. The three proposals above are for the owner
to accept or refuse.

---

## Decisions

Every judgement call this study made, so a reviewer can attack it.

1. **A stash is 8 or more days out at the decision, counted in days and not in
   missed games.** The brief gave both ("7+ consecutive team game days" and
   "out 8+ days"), and they are different populations: a third of the men at
   8–14 days out have missed three team games or fewer (§1b). The day count is
   the level because it is what a manager has on the morning; the games count is
   printed in every §1b row so a reader who prefers the other rule can see who
   moves.
2. **"Out" is a `played = false` row in `player_game_stats`, and nothing else.**
   No `pro_team_games` join, no team attribution, no injury report. §0a is the
   check that this really traces a man's own schedule (97–98% both ways, 2020
   excepted), and the definition survives trades and the bubble because it reads
   only his own rows.
3. **Days out is measured from his last played day, so it is in calendar days.**
   A scoring period is a calendar day in this database and §Limitations 1 proves
   it on two stored dates. A man with no played day before the absence gets
   `first missed − 1` as the stand-in and is flagged; held stashes with no prior
   played day are dropped from the census entirely.
4. **Held stashes need a rotation filter and claimed stashes do not.** A held
   stash requires 10 played games in the season (`PRIOR_GAMES`, the boundary
   `docs/availability.md` §5 uses); without it every end-of-bench man's DNP run
   would be a "stash". A claim needs no filter because the claim is the
   selection.
5. **A held stash's decision day is the day he went out, and its level is the
   absence's *total* days.** A manager holding an injured man decides again
   every morning, so there is no single decision day; the day he went out is the
   first of them and the absence's whole length is what the manager was
   eventually deciding about. A claim's level is days out *at the claim*, which
   is what was knowable.
6. **The net is reported two ways and neither is hidden.** The declared arm is
   the brief's formula. The swap arm is the same hold scored as one `Replay`
   swap against the wire's best man of the decision morning, over every week
   including the dead ones — one currency, no constant, no second lens. They
   agree on the sign 84.62% of the time and the document says so before any
   number is read (§Limitations 3).
7. **The post-return value is `-Replay.delta(team, week, [him], [])`.** Take him
   off the roster of each day, re-seat the lineup from scratch, and count the
   categories the team loses against the opponent it really faced. This is the
   same call and the same sign `scripts/keepers.py` uses for the man a claim
   dropped, so the two studies' value numbers are one quantity.
8. **`OPENED_PLACE` is charged flat at 0.38 and the team's own number is printed
   beside it.** The brief declared the flat charge. §5's table shows the median
   stashing team's own streamed place returned 0.24 to 0.43 depending on the
   season, so the flat charge is fair and slightly harsh, and nothing here asks
   for the constant to move.
9. **The return prior drops the suspended 2020 season, and the pooled one is
   printed beside it.** March 2020 turns a thousand ordinary absences into
   absences that never end; at 28 days out the two curves differ by 14 points at
   the 28-day horizon. Everything else in the document is pooled, with §4e as
   the sensitivity.
10. **The healthy value needs five played games or it is not read.** `MIN_NORM`
    = 5, and 179 of 2,165 decisions fall below it into a `?` tier that is
    printed rather than dropped. Brandon Miller is one of them, on two games,
    which is why §6b has no ramp for the worked example and says so.
11. **The ramp's value ratio is floored.** A pre-injury week worth less than
    0.10 through the lens is not a denominator, and those cells are dropped from
    the value column and kept in the minutes column.
12. **The headline is recomputed a second way inside the script.** `--why`
    prints the per-season accounting (adds → no box rows → was playing → was out
    → no roster run), rebuilds the stash count by re-reading the raw days-out
    list, and rebuilds the median net by re-summing every stash's own components
    instead of reading the `net` property. The two agree to **0.00e+00** and the
    third reading, the swap arm, agrees on the sign for 77 of 91.
13. **The stasher's situation is the record rank, not the playoff odds.** The
    brief allows the fallback and the database forces it: 37 stored
    `team_reports` rows exist in total, all 2026. Stated in §5 rather than
    quietly substituted.
14. **Nothing was tuned on the run that scores it.** `OPENED_PLACE`,
    `TYPICAL_PICKUP`, `ESPN_AVAILABILITY`, both season hurdles and the stream
    hurdle are the shipped values throughout, and the ramp factors and the
    return prior are outputs of this run rather than inputs to it.
15. **`scripts/keepers.py` and `docs/keepers.md` were not touched.** Another
    agent owns them. This study reuses `scripts/pickups_backtest.py`'s `Replay`
    directly, reimplements keepers' `WireBook` rather than importing it, and
    imports `scripts/availability.py`'s `reason_class` and
    `scripts/streaming_lane.py`'s per-place measurement so neither is copied.
