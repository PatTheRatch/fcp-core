# Stashing when you are already in: what a dead week costs a lock

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft, FAAB from 2026
**Seasons covered:** 2019–2026 (eight seasons; league sizes 10, 10, 10, 12, 16, 14, 12, 14)
**Script:** `scripts/stash_locks.py` — a sibling of `scripts/stashes.py` that **imports** it, so the population, the levels, the wire book, the `Replay` scoring and the standard net are one definition and not two
**Instrument:** everything `docs/stashes.md` reads, plus `app.inseason.projected.project_standings` run once per distinct decision morning
**Currency:** categories a week, through `scripts/pickups_backtest.py`'s `Replay` and `app.pickups.judge.standard_lens`
**Companions:** [`stashes.md`](stashes.md) (the study this extends; §5 is the section it replaces), [`projected_record.md`](projected_record.md) (the engine, and how well it knows), [`stash_mode.md`](stash_mode.md) (the rule the first study produced), [`what_if.md`](what_if.md) (where the lens shipped)
**Reproduce:** `PYTHONPATH=. ~/fcp-core/.venv/bin/python scripts/stash_locks.py` — the published run was against the **local** Docker Postgres (`fcp-core-db-1`, `localhost:5432`). **Wall time 1,114s**, of which **919s is 871 engine runs over 865 distinct decision mornings, 1.05s a run**, memoized per day.
**Read-only:** every query is a SELECT. Nothing is written to the database.

---

## Why this exists

The owner, 2026-09-24: *"What about if you are a playoff lock? Do we do any
research on that?"*

`docs/stashes.md` §5 cut the census by where the team stood that morning — the
top third of the league by banked categories paid 30.84% of the time against
the bottom third's 24.40% — and then priced every stash the same way. Every
dead regular-season week was charged at `OPENED_PLACE` (0.38) and every benefit
was counted on the regular-season weeks after the man's return.

That is right for a team in the race. It is wrong for a team that has already
won its place. **A lock's dead regular weeks buy seeding and nothing else**,
and what the roster place is really buying is the man **in the playoff weeks**,
times the chance he is back for them.

§5 could not do better because it had no playoff odds: the database holds 37
stored `team_reports` rows, all 2026, all from a handful of days. It does not
need stored ones. The engine runs on any morning of any stored season in about
a second, and this study runs it once per decision morning and reads every
team's odds off the one run.

---

## Declared before anything ran

* **A lock** on a decision day: playoff odds **≥ 0.95** from the engine run on
  that morning, at **10,000** simulated seasons and seed **3853870**. **In the
  race:** 0.25 up to 0.95. **Out of it:** below 0.25.
* **The seeding stake**, for a lock: **`1 − max(seed odds)`** off the same run —
  the chance the team does *not* finish in its own most likely place. Zero
  means the seed is settled and there is nothing left to play for in the
  regular season.
* **The lock's net**, beside the study's standard net and never replacing it:

  ```
  benefit = P(back by the first playoff week | days out, the return prior)
            × (his playoff-weeks value − replacement, over the playoff weeks he was held)
  cost    = OPENED_PLACE × dead regular-season weeks × seeding stake
            + OPENED_PLACE × dead playoff weeks
  ```

  `P` is `app.pickups.returns.probability_back_within`, the shipped prior. The
  playoff-weeks value is the same counterfactual the first study uses for a
  regular week — `-Replay.delta(team, window, first, last, [him], [])` — taken
  over the playoff matchup periods instead. The replacement is the wire's best
  man on the decision morning, pro-rated by the period's length.
* **Two bounds beside it:** **stake = 1**, which is how `stashes.md` prices it,
  and **stake = 0**, "nothing to lose".
* **No hurdle, and no constant moves.** `OPENED_PLACE`, `TYPICAL_PICKUP`, the
  return prior, the ramp and both season hurdles are the shipped values.

**On the band.** The brief said the engine's odds are honest at the ends and
put the >90% band at 99%. The published figure is smaller and it is the one
quoted here: `docs/projected_record.md` §0's latest run says **what it called
above 90% happened 83.5% of the time, over 370 calls**. The band is honest
enough to cut a population on and it is not a certificate; 0.95 sits inside it
and every table below carries n.

---

## Limitations, stated before conclusions

### 1. A decision taken on a playoff day is not classified, and there are 391 of them

Once the playoffs have begun, "playoff odds" from a bracket projection is not
the quantity this study means. **391 of the 2,111 stash decisions — 18.5% —
were taken on a playoff day**, and they are counted apart in every table rather
than folded into "out of it". They are also the worst stashes in the census
(median standard net −0.38, **none** of them positive), which is its own small
finding: a stash made after the regular season is over has almost no weeks left
to pay for itself.

### 2. 2019 has no earlier season to measure the league's weekly spreads on

Every other season is run with `before=season` and the era adjustment off,
which is `scripts/projected_calibration.py`'s own no-look-ahead rule. 2019 is
the first stored season and `category_distributions(before=2019)` returns
nothing, so its run uses the **pooled** basis and is flagged in §1's table.
That leaks the league's weekly *spreads* — not any team's results — and 2019
carries 132 of 2,111 decisions and 9 of 294 locks. Every pooled figure is
printed again without it.

### 3. The benefit multiplies a prior by a realised number, and that is a hybrid

`P` is what the morning knew; the playoff-weeks value is what happened. The
product is the brief's declared formula and it double-discounts: a man who
never reached the playoffs contributes a realised value of zero *and* is
multiplied by a probability. **So the realised playoff benefit is printed
beside the lock's net in every table**, undiscounted, and a reader who wants
the other reading can take it.

### 4. The playoff-weeks value counts every stored matchup in a playoff period

ESPN stores consolation brackets in the same periods. For a lock that is the
real bracket, so the number is what it says it is; for the other two groups it
may not be, which is one more reason the lock's net is only ever reported for
locks.

### 5. 2020 is the suspended season, and it is where the worst lock stashes are

Six of this study's ten worst lock stashes are 2020's, for the same reason
`docs/stashes.md` §Limitations 5 gives: every man out in March 2020 reads as an
absence that never ended. Pooled figures are printed with 2020 in and with it
out.

### 6. Nothing here is a hurdle and no bar moved

This measures outcomes. It does not measure the recommender, and a bar invented
here would have no backtest under it.

---

## The answer, up front

**A lock's dead place costs less than half what the study charges it, and that
is the whole of the finding.** Over 294 lock stashes the median lock cost is
**0.18 categories against the standard charge of 0.38** — because the median
seeding stake is 0.51, so about half of every dead regular week is a week the
team was not going to be paid for anyway.

**The two nets disagree on the sign more than a quarter of the time.** They
agree for **73.13%** of lock stashes; the lens says the move paid where the
standard net says it did not in **8.50%**, and the other way in **18.37%**.
Among the 138 lock stashes whose man was back and still held for playoff week
one, they agree only **53.62%** of the time. **That is the test a product
change has to pass, and it passes it.**

**The engine says why, and it is seeding rather than survival.** Re-running the
same morning with the stashed man taken off the roster — the what-if engine's
own `rosters=` override — over the 131 lock stashes that reached the playoffs:
the median move in the team's **playoff odds** from holding him is **+0.0060**,
and the largest in eight seasons is **+0.1984**. The median move in the chance
of its **own most likely seed** is **+0.0401** and the largest **+0.3357**. A
lock is not buying its place with that roster spot. It is buying a seed.

**And the plain reading is not "locks should stash more".** Locks stash
**late** — a median of three regular weeks left against the race's ten — and
`docs/stashes.md` §4c already showed that late is where stashing cannot work.
The locks' *standard* net is the worst of the three groups (median −0.15,
positive 29.25%), and under the lens **46.94% of lock stashes have no playoff
weeks at all** because the man was gone or dropped before the bracket started.
The lens makes the wait cheaper and makes the benefit harder to earn, and both
of those are true at once.

---

## 1. Every stash decision, classified by the morning's odds

| season | stash decisions | classified | on a playoff day | no projection | lock | race | out | lock share | spread basis |
|---|---|---|---|---|---|---|---|---|---|
| 2019 | 132 | 108 | 24 | 0 | 9 | 49 | 50 | 8.33% | **pooled** |
| 2020 | 280 | 146 | 134 | 0 | 29 | 38 | 79 | 19.86% | earlier seasons |
| 2021 | 224 | 202 | 22 | 0 | 15 | 84 | 103 | 7.43% | earlier seasons |
| 2022 | 276 | 237 | 39 | 0 | 32 | 115 | 90 | 13.50% | earlier seasons |
| 2023 | 277 | 244 | 33 | 0 | 56 | 86 | 102 | 22.95% | earlier seasons |
| 2024 | 279 | 225 | 54 | 0 | 61 | 78 | 86 | 27.11% | earlier seasons |
| 2025 | 295 | 252 | 43 | 0 | 26 | 165 | 61 | 10.32% | earlier seasons |
| 2026 | 348 | 306 | 42 | 0 | 66 | 125 | 115 | 21.57% | earlier seasons |
| **pooled** | **2111** | **1720** | **391** | **0** | **294** | **740** | **686** | **17.09%** | |
| without 2019 | 1979 | 1612 | 367 | 0 | 285 | 691 | 636 | 17.68% | |
| without 2019/2020 | 1699 | 1466 | 233 | 0 | 256 | 653 | 557 | 17.46% | |

**About one stash in six in this league is a lock's** — 294 of the 1,720 that
can be classified, 17.09%, and the figure barely moves when 2019 and 2020 come
out. The **"no projection" column is zero in every season**, which is stated as
a column rather than assumed: every one of the 2,111 decisions falls inside a
stored matchup period, so no decision is quietly filed under "out of it"
because the engine could not answer.

### The three groups against the standing third §5 used

| group | n | med playoff odds | in the top third | middle | bottom |
|---|---|---|---|---|---|
| lock | 294 | 1.00 | **80.20%** | 19.80% | 0.00% |
| race | 740 | 0.68 | 35.12% | 47.52% | 17.36% |
| out | 686 | 0.03 | 4.28% | 20.94% | **74.78%** |

**The record rank was a fair stand-in and it was not the same thing.** No lock
sat in the bottom third of the banked record, and four per cent of the teams
that were out of it sat in the top third — teams with a good record and a
schedule or a rival that made it not enough. The odds are a strictly better cut
and §5's fallback was not misleading.

### What each group's stash decisions were, and what they paid the study's way

| group | n | med days out | med weeks left | med healthy | med net (standard) | positive | med net (swap) |
|---|---|---|---|---|---|---|---|
| lock | 294 | 13.00 | **3.00** | 0.64 | **−0.15** | **29.25%** | 0.00 |
| race | 740 | 14.00 | **10.00** | 0.63 | −0.05 | **39.05%** | 0.00 |
| out | 686 | 14.00 | 7.00 | 0.60 | −0.11 | 30.32% | 0.00 |
| playoffs | 391 | 25.00 | 0.00 | 0.65 | **−0.38** | **0.00%** | 0.00 |

**A lock's stash is a late stash, and that is most of why it reads badly.** A
lock has three regular weeks left when it stashes; a team in the race has ten.
`docs/stashes.md` §4c measured that window directly: with four weeks or fewer
left, 12.95% of stashes paid. **The situation is not causing the result; the
calendar is.** It is a lock *because* it is late in the season.

---

## 2. The lock's net against the standard net

| cut | n | med net | positive | med lock net | positive | lock net, stake=1 | lock net, stake=0 |
|---|---|---|---|---|---|---|---|
| **every lock** | **294** | **−0.15** | **29.25%** | **−0.08** | **19.39%** | **−0.22** | **0.00** |
| 2019 | 9 | −0.05 | 44.44% | −0.05 | 33.33% | −0.11 | 0.00 |
| 2020 | 29 | −0.27 | 31.03% | −0.29 | 6.90% | −0.46 | 0.00 |
| 2021 | 15 | −0.17 | 26.67% | −0.28 | 13.33% | −0.54 | −0.08 |
| 2022 | 32 | −0.16 | 31.25% | −0.13 | 12.50% | −0.29 | 0.00 |
| 2023 | 56 | −0.16 | 25.00% | −0.04 | 33.93% | −0.10 | 0.00 |
| 2024 | 61 | −0.15 | 32.79% | −0.12 | 24.59% | −0.23 | 0.00 |
| 2025 | 26 | −0.25 | 11.54% | −0.03 | 19.23% | −0.11 | 0.00 |
| 2026 | 66 | −0.05 | 33.33% | −0.07 | 10.61% | −0.27 | 0.00 |

**The two bounds are the point of the table.** At stake = 1 — the study's own
pricing — the median lock's net is −0.22. At stake = 0 it is exactly 0.00,
because with a settled seed the only charge left is dead *playoff* weeks and the
median lock has none. The measured stake puts the real answer at **−0.08**.

By level (days out at the decision):

| level | n | med net | positive | med lock net | positive | stake=1 | stake=0 |
|---|---|---|---|---|---|---|---|
| 8–14 | 166 | −0.06 | 40.36% | −0.09 | 24.70% | −0.22 | 0.00 |
| 15–28 | 61 | −0.16 | 21.31% | −0.15 | 13.11% | −0.43 | 0.00 |
| 29+ | 67 | −0.16 | **8.96%** | **−0.04** | 11.94% | −0.11 | 0.00 |

**The lens rescues the month-plus stash and only the month-plus stash.** At 29+
days out the standard net is −0.16 and 8.96% pay; the lock's net is −0.04. That
is the shape you would expect from a charge that is cut in half: the longer the
wait, the more of the net is the charge.

By his healthy value:

| healthy tier | n | med net | positive | med lock net | positive | stake=1 | stake=0 |
|---|---|---|---|---|---|---|---|
| <0.50 | 70 | −0.05 | 27.14% | −0.02 | 20.00% | −0.05 | 0.00 |
| 0.50–1.00 | 181 | −0.17 | 28.73% | −0.13 | 17.68% | −0.27 | 0.00 |
| **1.00–2.00** | 34 | −0.35 | 29.41% | **−0.22** | **29.41%** | −0.49 | 0.00 |
| ? (norm under 5 games) | 9 | 0.40 | 55.56% | −0.04 | 11.11% | −0.05 | 0.00 |

By the seeding stake:

| cut | n | med net | positive | med lock net | positive | stake=1 | stake=0 |
|---|---|---|---|---|---|---|---|
| settled (stake ≤ 0.10) | **17** | −0.27 | 11.76% | **−0.04** | 11.76% | −0.43 | 0.00 |
| still moving | 277 | −0.11 | 30.32% | −0.09 | 19.86% | −0.22 | 0.00 |

**A settled seed is rare even among locks: 17 of 294.** The median stake is
0.51, which says that in a league of ten to sixteen teams a side can be certain
of its place and still have a coin-flip's worth of seeding in front of it. That
is exactly why the stake is a *number* on the page and not a flag.

### What the lock's net is made of

| cut | n | med stake | dead reg wk | dead po wk | P(back by po) | po weeks held | realised po benefit | **lock cost** | **standard cost** |
|---|---|---|---|---|---|---|---|---|---|
| every lock | 294 | 0.51 | 0.86 | 0.00 | 0.69 | 0.43 | 0.00 | **0.18** | **0.38** |
| settled | 17 | 0.01 | 0.43 | 0.00 | 0.27 | 0.00 | 0.00 | 0.01 | 0.27 |
| still moving | 277 | 0.52 | 0.86 | 0.00 | 0.70 | 0.57 | 0.00 | 0.20 | 0.38 |

**0.18 against 0.38.** That is the one number this document exists to produce.
A lock waiting on a man pays a little over half of what the census charges it,
and a lock whose seed is settled pays **0.01**.

### Does the lens change the answer?

| cut | n | the two nets agree on the sign | lock says yes, standard no | standard yes, lock no | med lock − standard | mean lock − standard | has playoff weeks |
|---|---|---|---|---|---|---|---|
| every lock | 294 | **73.13%** | 8.50% | 18.37% | +0.05 | −0.30 | 53.06% |
| settled | 17 | 76.47% | 11.76% | 11.76% | +0.16 | +0.11 | 35.29% |
| still moving | 277 | 72.92% | 8.30% | 18.77% | +0.04 | −0.33 | 54.15% |
| **reached the playoffs** | **138** | **53.62%** | **15.94%** | **30.43%** | −0.22 | −0.81 | 98.55% |

**This is the gate the product change was held to.** A second reading that
agreed with the first 95% of the time would be decoration. It disagrees on the
sign for **26.87%** of lock stashes and for **46.38%** of the ones whose man
actually reached the bracket, and it disagrees in both directions. The median
difference is **+0.05** and the mean is **−0.30**: the lens is a little kinder
than the standard net in the middle and much harsher in the tail, because it
refuses to count regular-season weeks a lock was not being paid for.

### The race and the out groups, the study's way, so the three read side by side

| group | n | med net | positive | med cost | med benefit |
|---|---|---|---|---|---|
| race | 740 | −0.05 | 39.05% | 0.38 | 0.00 |
| out | 686 | −0.11 | 30.32% | 0.38 | 0.00 |
| lock | 294 | −0.15 | 29.25% | 0.38 | 0.00 |

Priced the study's way all three pay the same 0.38 a dead week, which is the
assumption this document set out to test.

---

## 2e. The break-even inverted for a lock

*A man worth X a week is worth stashing if he is back by the playoffs with
probability ≥ Y.* The cell is the smallest P(back by the first playoff week)
that makes the lock's net positive, given the tier's own **measured
playoff-weeks net a week** and the median lock's dead weeks (0.86 regular, 0.00
playoff, 3.00 playoff weeks held). `OPENED_PLACE` is 0.38.

| worth X a week | n | measured po net a week | stake 0.00 | stake 0.25 | stake 0.50 | stake 1.00 |
|---|---|---|---|---|---|---|
| <0.50 | 31 | −0.06 | any | never | never | never |
| 0.50–1.00 | 99 | −0.06 | any | never | never | never |
| **1.00–2.00** | 23 | **+0.17** | any | **0.16** | **0.32** | **0.64** |
| 2.00+ | 0 | — | — | — | — | — |
| ? | 3 | +0.01 | any | never | never | never |

**In one sentence: only the good men are worth a lock's roster place, and for
them the bar is low.** A man worth a category a week or more returned +0.17 a
playoff week above the wire; with half the seed still in play he only has to be
**32%** to be back by the bracket for the hold to pay, and with the seed
settled any chance at all does. Below a category a week the measured playoff
return is **negative** — those men did not beat the wire in the playoff weeks —
and no return probability rescues a charge, which is why the cells read
"never".

"any" in the stake-0.00 column is not rhetoric: with a settled seed and no dead
playoff weeks the cost term is zero outright, so the sign of the net is the
sign of the man's playoff value and nothing else.

---

## 3. Did the stash reach the playoffs?

| cut | n | returned at all | back & held for playoff week 1 | med po weeks | med po benefit | positive |
|---|---|---|---|---|---|---|
| every lock | 294 | 91.16% | **46.94%** | 3.00 | −0.03 | 46.38% |
| claims | 65 | 90.77% | 35.38% | 1.29 | −0.10 | 34.78% |
| holds | 229 | 91.27% | 50.22% | 3.00 | −0.03 | 48.70% |

**Nine locks in ten got the man back, and fewer than half still had him when
the bracket started.** The gap is drops: a lock that waited, got him back and
then moved him on before the playoffs bought nothing the lens can count. A
*claim* reaches the bracket only 35% of the time, against 50% for a man already
held, which is the honest warning on the move the product is most likely to be
asked about.

What the teams that got him there did in the bracket:

| the team's playoff rounds | n |
|---|---|
| lost then lost | 22 |
| won then lost then won | 19 |
| won then won | 16 |
| lost then won | 14 |
| lost then won then won | 14 |
| won then lost | 12 |
| won then won then lost | 10 |
| lost then lost then lost | 9 |
| won then won then won | 9 |
| lost then won then lost | 7 |
| won then lost then lost | 6 |

**Nothing in that table can be attributed to the stash and it is printed
because the brief asked for it.** A playoff period in this league's stored
bracket includes consolation matchups, a first-round bye reads as no matchup at
all, and one roster place is one of thirteen. The honest counterfactual is the
next table.

### The honest counterfactual: the same morning with him off the roster

The what-if engine's own `rosters=` override, run on the decision morning, for
every lock stash whose man reached playoff week one. Top thirty by realised
playoff benefit:

| season | day | player | odds | stake | odds with him | without him | playoff odds moved | seed odds moved | po benefit | lock net |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 93 | Kyrie Irving | 1.00 | 0.38 | 1.00 | 1.00 | +0.0000 | **+0.2196** | 3.50 | **2.58** |
| 2024 | 49 | Kyrie Irving | 0.95 | 0.78 | 0.95 | 0.76 | **+0.1984** | +0.0647 | 3.50 | 2.34 |
| 2023 | 130 | Damian Lillard | 1.00 | 0.01 | 1.00 | 1.00 | +0.0000 | +0.0061 | 3.16 | 0.90 |
| 2025 | 114 | Damian Lillard | 1.00 | 0.47 | 1.00 | 1.00 | +0.0000 | −0.0471 | 2.77 | 1.40 |
| 2021 | 129 | LaMelo Ball | 1.00 | 0.00 | 1.00 | 1.00 | +0.0000 | +0.0000 | 2.58 | 0.32 |
| 2024 | 80 | Luka Doncic | 0.99 | 0.74 | 0.99 | 0.96 | +0.0384 | +0.0726 | 2.51 | 2.04 |
| 2023 | 43 | Brandon Ingram | 0.99 | 0.70 | 0.99 | 0.94 | +0.0507 | +0.0701 | 2.49 | 0.09 |
| 2025 | 108 | Mark Williams | 1.00 | 0.40 | 1.00 | 1.00 | +0.0001 | −0.0220 | 2.41 | 1.09 |
| 2022 | 100 | Jerami Grant | 1.00 | 0.47 | 1.00 | 1.00 | +0.0000 | +0.1112 | 2.28 | 0.57 |
| 2023 | 122 | Kyrie Irving | 1.00 | 0.53 | 1.00 | 1.00 | +0.0000 | +0.0338 | 2.15 | 1.02 |
| 2019 | 97 | Danilo Gallinari | 0.96 | 0.59 | 0.96 | 0.90 | +0.0605 | +0.0466 | 2.10 | 1.07 |
| 2026 | 114 | Ryan Rollins | 1.00 | 0.47 | 1.00 | 1.00 | +0.0025 | +0.0568 | 2.05 | 1.22 |
| 2023 | 100 | Kristaps Porzingis | 1.00 | 0.34 | 1.00 | 1.00 | +0.0000 | +0.1703 | 1.98 | 1.48 |
| 2026 | 109 | OG Anunoby | 1.00 | 0.71 | 1.00 | 1.00 | +0.0024 | +0.0155 | 1.97 | 0.86 |
| 2024 | 49 | John Collins | 0.95 | 0.78 | 0.95 | 0.87 | +0.0800 | +0.0448 | 1.88 | 1.50 |
| 2024 | 114 | Myles Turner | 1.00 | 0.26 | 1.00 | 1.00 | +0.0000 | +0.0642 | 1.84 | 1.22 |
| 2023 | 120 | Jerami Grant | 1.00 | 0.38 | 1.00 | 1.00 | +0.0000 | −0.0498 | 1.65 | 0.57 |
| 2026 | 123 | Trey Murphy III | 1.00 | 0.47 | 1.00 | 1.00 | +0.0000 | +0.0027 | 1.55 | 0.57 |
| 2024 | 79 | Tyrese Haliburton | 1.00 | 0.34 | 1.00 | 1.00 | +0.0019 | **+0.3357** | 1.55 | 1.21 |
| 2024 | 90 | Tyrese Haliburton | 1.00 | 0.24 | 1.00 | 1.00 | +0.0001 | +0.2252 | 1.53 | 1.18 |
| 2019 | 121 | DeMarcus Cousins | 0.95 | 0.55 | 0.95 | 0.92 | +0.0277 | +0.0835 | 1.51 | 0.89 |
| 2024 | 103 | Brook Lopez | 1.00 | 0.75 | 1.00 | 0.99 | +0.0064 | +0.0172 | 1.50 | 1.05 |
| 2026 | 88 | Kawhi Leonard | 1.00 | 0.32 | 1.00 | 1.00 | +0.0001 | +0.2210 | 1.44 | 1.17 |
| 2023 | 9 | Kawhi Leonard | 0.97 | 0.78 | 0.97 | 0.89 | +0.0816 | +0.0839 | 1.36 | 0.34 |
| 2024 | 114 | Kentavious Caldwell-Pope | 1.00 | 0.26 | 1.00 | 1.00 | +0.0000 | +0.0401 | 1.32 | 0.85 |
| 2023 | 67 | Rui Hachimura | 0.99 | 0.77 | 0.99 | 0.98 | +0.0131 | +0.0204 | 1.31 | 1.10 |
| 2022 | 79 | Rudy Gobert | 1.00 | 0.48 | 1.00 | 0.99 | +0.0103 | +0.1887 | 1.26 | 0.63 |
| 2023 | 122 | Lauri Markkanen | 1.00 | 0.53 | 1.00 | 1.00 | +0.0000 | +0.0317 | 1.15 | 0.44 |
| 2026 | 114 | Jalen Duren | 1.00 | 0.47 | 1.00 | 1.00 | +0.0021 | +0.0474 | 1.05 | 0.47 |
| 2024 | 108 | Kentavious Caldwell-Pope | 1.00 | 0.56 | 1.00 | 1.00 | +0.0000 | +0.0104 | 1.02 | 0.70 |

**Over 131 lock stashes that reached the playoffs, the median move in playoff
odds from holding him is +0.0060 and the largest in eight seasons is +0.1984;
the median move in the chance of his team's own most likely seed is +0.0401 and
the largest +0.3357.**

**What can be attributed and what cannot.** The odds columns are attributable:
they are the same engine, the same seed, the same morning, with one man's id
taken out of one roster list, and nothing else in the league changed. What they
say is that **a lock is not buying its place with that roster spot — it is
buying a seed**, by a factor of about seven on the median. The `po benefit`
column is *not* a counterfactual on the same footing: it is what the man
actually won in the playoff weeks against the wire's best free agent of the
decision morning, which is a realised quantity and carries the team's whole
season with it. And the bracket results above cannot be attributed at all. The
two rows where the playoff odds really moved — Kyrie Irving 2024 day 49 at
+0.1984 and John Collins on the same morning at +0.0800 — are the exception
that proves the rule: that team was at 0.95, not 1.00, which is the bottom edge
of the band.

---

## 4. The locks named

### 4a. Every 2026 stash taken by a team that was already a lock

66 of them, on six teams. The ten with the largest playoff benefit and the ten
with the worst lock net are enough to read the shape; `scripts/stash_locks.py`
prints all 66.

| day | team | player | kind | d out | odds | stake | P(back by po) | po benefit | net | lock net |
|---|---|---|---|---|---|---|---|---|---|---|
| 114 | Brighton Bears | Ryan Rollins | hold | 11 | 1.00 | 0.47 | 0.71 | **2.05** | 0.65 | **1.22** |
| 109 | Brighton Bears | OG Anunoby | hold | 15 | 1.00 | 0.71 | 0.69 | 1.97 | 1.84 | 0.86 |
| 123 | The Infirmary | Trey Murphy III | hold | 18 | 1.00 | 0.47 | 0.51 | 1.55 | 1.34 | 0.57 |
| 88 | Through The Wire | Kawhi Leonard | hold | 8 | 1.00 | 0.32 | 0.89 | 1.44 | 2.45 | 1.17 |
| 114 | Brighton Bears | Jalen Duren | hold | 12 | 1.00 | 0.47 | 0.69 | 1.05 | 1.12 | 0.47 |
| 107 | Brighton Bears | Shai Gilgeous-Alexander | hold | 24 | 1.00 | 0.62 | 0.63 | 0.97 | 0.00 | −0.16 |
| 114 | The Infirmary | Jalen Green | hold | 9 | 1.00 | 0.50 | 0.75 | 0.57 | 2.18 | 0.21 |
| 114 | The Infirmary | Alex Sarr | hold | 28 | 1.00 | 0.50 | 0.50 | 0.55 | −1.38 | −0.40 |
| 104 | The Infirmary | Jalen Green | hold | 8 | 1.00 | 0.66 | 0.83 | 0.40 | 2.28 | 0.12 |
| 114 | Foxes ShutUpNDribble | Miles Bridges | hold | 15 | 1.00 | 0.48 | 0.64 | 0.05 | −0.98 | −0.31 |
| … | | | | | | | | | | |
| 115 | Foxes ShutUpNDribble | Myles Turner | hold | 11 | 1.00 | 0.59 | 0.70 | **−1.44** | −0.85 | **−1.32** |
| 50 | Brighton Bears | Franz Wagner | hold | 39 | 0.98 | 0.63 | 0.91 | 0.00 | −3.01 | −1.27 |
| 91 | Brighton Bears | Jalen Williams | hold | 23 | 1.00 | 0.67 | 0.76 | 0.00 | −1.44 | −0.76 |
| 14 | Masters of their Domain | Austin Reaves | hold | 8 | 0.97 | 0.59 | 0.98 | −0.51 | −2.58 | −0.73 |
| 100 | Brighton Bears | Evan Mobley | hold | 24 | 1.00 | 0.51 | 0.70 | −0.10 | 1.29 | −0.68 |
| 94 | Brighton Bears | Franz Wagner | hold | 22 | 0.99 | 0.68 | 0.75 | 0.00 | −1.25 | −0.67 |
| 87 | The Infirmary | P.J. Washington | hold | 8 | 0.97 | 0.57 | 0.89 | −0.49 | 0.42 | −0.66 |
| 53 | The Infirmary | Anthony Edwards | hold | 11 | 0.95 | 0.78 | 0.94 | −0.26 | **8.34** | −0.54 |
| 93 | The Infirmary | Kevin Porter Jr. | hold | 16 | 1.00 | 0.65 | 0.79 | −0.03 | 3.35 | −0.52 |
| 69 | The Infirmary | Kyshawn George | hold | 14 | 0.95 | 0.74 | 0.90 | 0.00 | 5.24 | −0.48 |

**The bottom of that table is the lens disagreeing loudly, and it is worth
reading rather than dismissing.** Anthony Edwards on day 53 is the census's
best 2026 lock stash by the standard net at **+8.34** — eleven days out,
returned, and worth eight and a half categories over the rest of the regular
season. Through the playoff lens he is **−0.54**, because by the time the
bracket started The Infirmary no longer had him. Both numbers are true. The
first says the hold won the team a lot of regular-season categories; the second
says none of them arrived in the weeks a lock is actually playing for. **That
is exactly why the page prints both lines and neither replaces the other.**

Through The Wire — the owner's team, and the subject of `docs/stashes.md` §6b —
made **25 of the 66**, more than any other side (The Infirmary 16, Brighton
Bears 15, Foxes ShutUpNDribble 5, Masters of their Domain 4, Fantastic 5 one),
and its best was Kawhi Leonard held from day 88 for a lock net of **+1.17**.

### 4b. The best and the worst lock stash in eight seasons

| season | day | team | player | kind | d out | odds | stake | po benefit | net | lock net |
|---|---|---|---|---|---|---|---|---|---|---|
| **2024** | **93** | **The Infirmary** | **Kyrie Irving** | hold | 14 | 1.00 | 0.38 | **3.50** | −0.48 | **+2.58** |
| 2024 | 49 | The Infirmary | Kyrie Irving | hold | 24 | 0.95 | 0.78 | 3.50 | 4.20 | +2.34 |
| 2024 | 80 | Team Morin | Luka Doncic | hold | 8 | 0.99 | 0.74 | 2.51 | 4.91 | +2.04 |
| 2024 | 49 | The Infirmary | John Collins | hold | 10 | 0.95 | 0.78 | 1.88 | 6.12 | +1.50 |
| 2023 | 100 | Team Stylios | Kristaps Porzingis | hold | 9 | 1.00 | 0.34 | 1.98 | 0.98 | +1.48 |
| 2025 | 114 | Through The Wire | Damian Lillard | hold | 10 | 1.00 | 0.47 | 2.77 | −0.48 | +1.40 |
| 2026 | 114 | Brighton Bears | Ryan Rollins | hold | 11 | 1.00 | 0.47 | 2.05 | 0.65 | +1.22 |
| 2024 | 114 | The Infirmary | Myles Turner | hold | 10 | 1.00 | 0.26 | 1.84 | 0.41 | +1.22 |
| 2024 | 79 | The Infirmary | Tyrese Haliburton | hold | 11 | 1.00 | 0.34 | 1.55 | −1.21 | +1.21 |
| 2024 | 90 | The Infirmary | Tyrese Haliburton | hold | 11 | 1.00 | 0.24 | 1.53 | −1.01 | +1.18 |
| 2023 | 88 | Blame Throwers | Kevin Durant | hold | 52 | 0.98 | 0.79 | 0.79 | −2.61 | −1.51 |
| 2022 | 104 | Ado Bro | Kristaps Porzingis | hold | 36 | 1.00 | 0.67 | −0.03 | −1.90 | −1.76 |
| 2022 | 59 | Foxes ShutupNDribble | Pascal Siakam | hold | 14 | 0.96 | 0.67 | −1.83 | 3.13 | −1.96 |
| 2020 | 103 | East End Doncic | Kemba Walker | hold | 8 | 1.00 | 0.54 | −2.29 | −1.03 | −2.00 |
| 2023 | 45 | Blame Throwers | Karl-Anthony Towns | hold | 114 | 1.00 | 0.54 | 0.00 | −4.18 | −2.27 |
| 2020 | 74 | East End Doncic | Kemba Walker | hold | 8 | 0.97 | 0.67 | −2.34 | 0.12 | −2.30 |
| 2020 | 54 | Moneyballers ££ | Devin Booker | hold | 9 | 0.99 | 0.47 | −2.49 | 2.90 | −2.49 |
| 2020 | 81 | Moneyballers ££ | Anthony Davis | hold | 13 | 0.99 | 0.52 | −3.16 | 2.27 | −2.92 |
| 2020 | 74 | Thibs Dust | Bradley Beal | hold | 11 | 0.99 | 0.63 | −3.34 | 1.02 | −3.25 |
| 2020 | 68 | Moneyballers ££ | Andrew Wiggins | hold | 10 | 0.96 | 0.68 | −3.45 | −0.69 | −3.43 |

**The best lock stash in eight seasons is Kyrie Irving, 2024 day 93, at +2.58,
and its standard net is −0.48.** Fourteen days out, held by a team already
certain of its place with a 0.38 stake on its seed, back in time, and worth
three and a half categories over the playoff weeks. The census calls that move a
small loss. The lens calls it the best stash a lock made in eight seasons, and
the reason is the whole argument: what that team gave up was half of a dead
week's seeding, and what it got was the bracket.

**The worst is Andrew Wiggins, 2020 day 68, at −3.43**, and six of the ten
worst are 2020's — the suspended season, where the "playoff weeks" are the
bubble and the men held into it never played (limitation 5). The worst lock
stash of an ordinary season is **Kevin Durant, 2023 day 88, at −1.51**: held
fifty-two days out by a team at 0.98 with almost everything still to play for in
its seeding (stake 0.79), and he came back for the bracket worth less than the
wire.

### 4c. A case like Miller's on a team that had already clinched

`docs/stashes.md` §6b worked through the owner's own Brandon Miller claim — day
23 of 2026, $2, eighteen days out, +4.50 on the swap arm — made by a team that
was first in the standings but nowhere near clinched that early. **The question
the brief asked is whether anybody has made that move with the seed already
settled, and one manager has.**

| season | day | team | player | d out | paid | odds | stake | in for po wk 1 | po benefit | net | lock net |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **2021** | **129** | **Bone's Boyos** | **LaMelo Ball** | **40** | **$0** | **1.00** | **0.00** | **yes** | **+2.58** | **−0.17** | **+0.32** |
| 2026 | 122 | Through The Wire | Tristan Vukcevic | 11 | $0 | 1.00 | 0.07 | no | 0.00 | +0.40 | 0.00 |
| 2026 | 134 | Through The Wire | Franz Wagner | 20 | $0 | 1.00 | 0.00 | no | 0.00 | −0.27 | 0.00 |
| 2026 | 133 | Through The Wire | Peyton Watson | 26 | $0 | 1.00 | 0.00 | no | 0.00 | −0.43 | −0.05 |
| 2026 | 130 | Through The Wire | Aaron Gordon | 35 | $0 | 1.00 | 0.09 | no | 0.00 | −0.46 | −0.19 |

**LaMelo Ball, 2021, day 129, free, forty days out, claimed by a team at 1.00
whose seed could not move.** He was back and on the roster for playoff week one
and he won that team 2.58 categories against the wire's best man over the
bracket. **The census scores it −0.17 — a small loss. The lens scores it +0.32,
and the lens is the one that describes what the manager actually bought:** a
free option, on a place that was costing him nothing, cashed in the only weeks
that were left to matter.

Five settled-seed lock claims in eight seasons is a thin census, and the other
four are all the owner's own team late in 2026, all worth about nothing. The
finding is the *shape* of the LaMelo move, not a rate.

---

## What this means for the product

**Ship the playoff lens on the what-if's stash block.** The measurement
supports it on the test that was declared for it: the lock's net disagrees with
the standard net on the sign for **26.87%** of lock stashes and **46.38%** of
the ones that reached the bracket; the lock's dead charge is **0.18 against
0.38**; and the engine's own counterfactual says a lock's roster place buys
seeding (median +0.0401) rather than its place (median +0.0060), which is
exactly what the formula charges for.

**Shipped 2026-09-24** in `app/pickups/stash.py` (`Lock`, `lock_block`) and
`app/inseason/what_if.py` (`_lock`), gated on the finish layer's own playoff
odds at `LOCK_ODDS` = 0.95. The page gains one line under the existing one,
never instead of it, the route carries `stash.lock`, the MCP `what_if` trim
carries it, and the skill has a line. No constant moved, no hurdle moved, and
nothing is labelled against it.

**Three things the page must keep saying.**

1. **Both readings, never one.** The Anthony Edwards row above is the case: a
   hold worth +8.34 over the regular season and −0.54 through the bracket. A
   page that printed only the second would be lying about the first.
2. **The stake is a number, not a flag.** The median lock's seeding stake is
   **0.51**: certain of its place and still playing for the seed. The two
   bounds travel with the figure for exactly that reason.
3. **The odds beside the number, and no verdict.** A lock's stash under the bar
   still shows with its number, which is the owner's rule.

**What was deliberately not built.**

* **No new hurdle.** A stash's net belongs against `SEASON_HURDLE_PAID` and
  `SEASON_HURDLE_FREE` divided by the weeks left, the same as every other
  season move. This study measures outcomes, not the recommender.
* **Nothing for the race and the out groups.** They are priced the way
  `docs/stashes.md` prices them and this document changes nothing about them.
* **Nothing on the season page.** `held_stashes` has no projected standings in
  hand and inventing a second engine read for a table of held men would double
  the cost of that page for a line most of its rows would not carry.
* **No IR change.** `injured_reserve_slots` is 0 in all nine stored seasons and
  the lens reads the same gate the first reading reads: a free slot makes the
  whole charge zero.

---

## Decisions

1. **A lock is 0.95 and the band is the engine's own.**
   `docs/projected_record.md` §0 says calls above 90% happened 83.5% of the
   time over 370 of them. 0.95 sits inside the band the forecast is honest in,
   and the brief's own recollection of that figure (99%) is corrected here
   rather than quoted.
2. **The seeding stake is `1 − max(seed odds)` and not the spread of the seed
   distribution.** It is the quantity a manager can act on — "is my place in
   the table still moving?" — and it is one subtraction off a number the finish
   layer already computes, so the page and this study cannot disagree about it.
3. **A decision taken on a playoff day is classified as nothing**, not as "out
   of it". 391 of 2,111, printed in their own row in every table.
4. **A decision the engine could not project would be classified as nothing
   too.** There are none; the column is printed anyway.
5. **2019 runs on the pooled spread basis and says so.** No earlier season of
   its size exists. Every pooled figure is printed again without it, and it
   moves nothing (lock share 17.09% against 17.68%).
6. **The benefit is the declared hybrid, and the realised half is printed
   beside it.** `P` × a realised value double-discounts; the undiscounted
   column is in every table that carries the net.
7. **The counterfactual runs only for lock stashes that reached playoff week
   one.** 131 of them, one extra engine run each. For the rest there is no
   playoff value to attribute anything to, and the run would cost a second for
   an answer nobody reads.
8. **Nothing was tuned on the run that scores it.** `OPENED_PLACE`,
   `TYPICAL_PICKUP`, the return prior, the ramp, `N_SIMS`, `SEED` and
   `SPREAD_SCALE` are the shipped values throughout, and `LOCK_ODDS`,
   `RACE_FLOOR` and the stake were declared in the script's docstring before
   the first run.
9. **`scripts/stashes.py` and `docs/stashes.md` keep their numbers.** This
   study imports the first one rather than copying it, and the only change to
   the document is a pointer from §5, which said playoff odds were unavailable
   and now says where they are.
