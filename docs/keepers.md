# Keepers: how often a waiver pickup deserved to stay on a roster

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft, FAAB from 2026
**Seasons covered:** 2019–2026 (eight seasons; league sizes 10, 10, 10, 12, 16, 14, 12, 14)
**Script:** `scripts/keepers.py` (re-runnable; passes `ruff check`, `ruff format --check` and `mypy`)
**Instrument:** `transactions` · `transaction_items` · `daily_lineup_slots` · `player_season_stats`
**Currency:** categories a week, the `pickup_values` lens, via `scripts/pickups_backtest.py`'s `Replay`
**Reproduce:** `cd /home/aisha/fcp-core-keep && PYTHONPATH=. /opt/fcp-core/.venv/bin/python scripts/keepers.py`
**Read-only:** every query is a SELECT. Nothing is written to the database. Runtime 496s for all eight seasons (472s before this revision added the hold definition beside the 30-day one — §2a-hold reuses the same `Replay`, so the extra cost is small).

> **Note on the reproduce line.** The house style sources `.env` before running
> (`set -a && . ./.env && set +a`). That fails against this worktree's `.env` —
> line 29 holds an unquoted `FCP_EMAIL_FROM` with angle brackets and bash
> rejects the file. The script reads `DATABASE_URL` straight out of `.env`
> itself, so the line above needs no sourcing.

---

## Limitations, stated before conclusions

### 1. The 30-day number is RUN-BASED, and the loose reading is nearly three times larger — do not confuse them

This is the first thing a reader needs, because a careless query returns a
totally different answer and it looks plausible.

**The headline's rule.** An add is "still on the roster 30 days later" only if
the adding team held that man **continuously** from the claim day through
day+30 — one unbroken run of `daily_lineup_slots` rows for (that team, that
man), starting on the claim day. **2026: 8.6%.**

**The loose reading, for contrast.** "The last day that team ever held him in
the season, minus the claim day, greater than 30." **2026: 24.6%**, pooled
median 8.0 days against the run-based 4.0. Published in §1b beside every
season.

**Why they differ.** The loose reading is not counting the adding team's own
consecutive spell. It counts **everything that happened afterward**, and three
things inflate it. 2026, over the 282 adds whose loose reading clears thirty
days:

| District | 2026 | Share | What it is |
|---|---|---|---|
| Genuinely held >30 days in one run | 99 | 35.1% | The headline's number. |
| Dropped and **re-added by the same team** inside the window, then held past it | 49 | 17.4% | Two spells with a gap between them. Not a 30-day stay. |
| The man took up with **another team**, or was re-signed at season's end, or never held again | 134 | 47.5% | The adding team held him a few days; the *league* held him a while. |

So **two thirds of what the loose reading counts as a "30-day stay" is not
one**: 47.5% of it is somebody else's holding and 17.4% is the same team
holding him after dropping him. Only a third is the thing the question means.

Across all eight seasons, **1,087 of 6,646 attributed adds (16.4%) had more
than one separate roster run inside the 30 days after the claim**, and of the
adds whose *loose* reading clears thirty days, **927 had their next add made
by a different team.** "Days held" in the loose sense is days held *by
someone*.

**A third reading, and where 65.7% came from.** An earlier pass of this study
reported a "days held over the season > 30" figure of **65.7%** — cumulative
held days across the whole season, gaps counted as held, every later spell by
anyone included, and no censoring control. That is a different quantity from
both of the above and **the headline is not it**. The doc's headline never
quotes a loose number, and no number here should be compared against one from
another reading. The three, side by side for 2026:

| reading | 2026 |
|---|---|
| **run-based, continuous from the claim day (the headline)** | **8.6%** |
| loose: last day held by that team minus claim day, > 30 | 24.6% |
| cumulative: held days over the season summed, gaps as held, > 30 | 65.7% |

**A trap in the opposite direction.** The run-based rule gives a man claimed
and dropped on the same day (or held for a day and cut) a *flattering*
possible stay if he is picked up again later, because the run the census
attributes is the long second one. Measured: 2026 has men reading "held 157
days" whose first stay was a day. The census keeps the attribution, because
"this team held him 157 days from this claim" is literally true of the run
that opened, but a reader who wants "did this claim become a keeper" should
read §2 rather than §1 for that man.

### 2. Right-censoring bites hardest in 2026, the season the question is about

A claim made in the season's last 30 days cannot be observed for 30. It is
**right-censored** at the season's end. The census handles this two ways and
prints both: every horizon over all adds (censored ones counted as still
held, which flatters late claims), and over the adds that can actually reach
the horizon — **the honest denominator, and the one the headline uses**.

| Season | adds | can reach 30d | censored at season end | ≥30d run, of reachable | ≥30d run, of all |
|---|---|---|---|---|---|
| 2019 | 700 | 587 | 11.6% | 14.5% | 12.1% |
| 2020 | 580 | 580 | 12.6% | 20.2% | 20.2% |
| 2021 | 817 | 652 | 10.2% | 11.0% | 8.8% |
| 2022 | 784 | 637 | 12.2% | 10.2% | 8.3% |
| 2023 | 599 | 462 | 16.7% | 21.4% | 16.5% |
| 2024 | 915 | 724 | 12.5% | 12.6% | 9.9% |
| 2025 | 1106 | 871 | 9.8% | 9.9% | 7.8% |
| **2026** | **1145** | **903** | **10.0%** | **11.0%** | **8.6%** |

2026's own 8.6% is measured over the 903 adds that could be observed for the
full window, not over all 1,145. Censoring is not why the number is low — the
reachable-denominator figure is *higher* than the raw one — but a reader
comparing 2026 against 2020 or 2023 is comparing different denominators and
should use the "of reachable" column.

### 3. Five 2026 adds cannot be attributed to a roster run, and all five are data artefacts, not behaviour

Of 1,150 executed 2026 adds, **1,145 are attributed** and **5 are not**. The
five are named here so a re-run can recognise them:

| tx | day | what the rows say |
|---|---|---|
| 7743 | 6 | Added by team 85. `daily_lineup_slots` **never** carries a row for (85, him) — that team's rows show him on 96 and 97, and a `TRADE_ACCEPT` dropped him the same scoring period. |
| 8192 | 19 | FREEAGENT add by team 86; the man was **already held** by 86, continuously, since day 14. The claim-day row belongs to the earlier stay, so no new run begins. |
| 8203 | 19 | Added by team 86; no lineup row for (86, him) anywhere. His only rows in 2026 belong to team 98. |
| 9660 | 71 | FREEAGENT add by team 87; already held by 87 since day 62. |
| 10532 | 98 | Added by team 88; no lineup row for (88, him) anywhere. |

So the exclusion rate is 0.43%, it is concentrated in same-day adds to a man
already rostered and in adds whose lineup rows are missing entirely, and it
does not favour any team.

**One number to be careful with, because two defensible counts differ by one.**
The census's own denominator is **1,145**, because the census additionally
requires the attributed run to *begin* on the claim day (two of the five above
are adds to a man already held, and three have no row at all). A replica that
only asks "was he in the lineup on the claim day, and how long does that run
last" gets **1,146** — it counts tx 8192, whose run began on day 14 rather than
on the claim day. The headline is quoted over **1,145**, the census's own
population, and the difference moves the 30-day share by less than 0.01 points
either way (99/1,145 = 8.65% against 99/1,146 = 8.64%; both round to 8.6%).

### 4. "Started" is read off the lineup rows, and that is not the same as "played"

A held man-day is a `daily_lineup_slots` row for (team, man, day) whose `slot`
is not `FA` or `IR`. A **started** man-day is the same row with `started` set.
That is what a manager did with his lineup, which is the question here — but
`docs/streaming_lane.md` §3 measured that **9.1% of started slots in 2026
have `played = false`** (ESPN marked the man in the lineup, the box score
records no game). The repo's lens, `started_lines`, filters on `played`. So
the "share of held days started" column here is a **lineup-decision** figure
and runs ~1.6–1.7× the production-weighted one in seven-day periods. Both are
correct answers to slightly different questions; this doc reports the lineup
one and says so.

### 5. The lens is the product's, not ESPN's, and it does not know the future

"Value" everywhere below is categories a week through
`app.pickups.judge.standard_lens().value` on the per-game line — the same
`marginal` lens `pickup_values` uses, so the numbers are comparable to
`TYPICAL_PICKUP` (0.06) and `OPENED_PLACE` (0.38). It is **not** ESPN's
scoring and it is not the composite figure `acquirable_value.md` quotes.
`app.pickups.stream.weight` is deliberately unused: it is an ordinal seating
aid, not a number of categories.

The wire's replacement level on a day is the best free agent left **before**
the claim, by the same lens, floored at `TYPICAL_PICKUP`. That is a
same-day, same-lens, hindsight-free comparison — but it is a *projection*
lens, so a man the model liked poorly and who then broke out posts a large
positive value against a low wire number. **This makes "deserved" easier on
surprise breakouts than on the man the model already rated**, which is a real
bias and it runs in the direction of the finding rather than against it.

### 6. "Deserved" is defined by me, in the product's currency, and it is strict

The brief's definition, stated so it can be attacked:

> Over the 30 days after the add, the man's **realised** per-week value —
> `Replay` with the claim undone and negated, the same machinery
> `scripts/pickups_backtest.py` uses on a real swap — against the **wire's
> replacement level on the day he was added**. "Deserved" = above that bar in
> **at least four of the next eight weeks**.

Two consequences the reader must carry. First, a claim with **fewer than eight
whole weeks left is judged on the weeks it has** — a claim made in the last
fortnight of the season is judged on one or two weeks and can therefore be
"deserved" on a much thinner record than the same man claimed in November. In
2026 that is visible and large: **nine of the fifteen deserving claims come
from days 150–160, the season's last eleven days**, and eight of those nine
cleared the bar on one or two weeks. §5 works through what that does to the
2026 keeper list.
Second — and this is the dominant effect — the bar is the **best left on the
wire**, so on a day when the wire is empty of anything real, the bar is
`TYPICAL_PICKUP` = 0.06 and clearing it four times out of eight is easy.
**Four of eight weeks above the wire is not the same as four of eight weeks
above zero.** §2c reports the zero-based reading beside it for exactly this
reason.

### 7. Seasons differ in more than size, so §4's league-size reading is confounded

The 10-team seasons are 2019–2021, the 16-team season is 2023, the 14-team
seasons are 2024 and 2026. Anything that changed league-wide between 2019 and
2026 — how much managers stream, how the scoring was set, what the wire
looked like — is inside the size columns. §4 says plainly which direction the
size effect runs and how much of it can be separated, and it does not claim a
causal estimate for 2027.

### 8. FAAB buckets have almost no n, and the recommender's-bar cut is a re-reading, not a replay

2026 is the only FAAB season: **2 claims** at $21+, **35** at $6–20, 390 at
$1–5, 688 at $0. Every FAAB-bucket number in §3 carries that n and none of
them is a finding.

The `the shipped bar` table in §3 is **not** a replay of the 2026 recommender.
Replaying it is `scripts/pickups_backtest.py`'s job and takes 85–110 minutes
(44 decision points a team). What is measured here is the same question read
**backwards from what happened**: would a move that turned out like this one
have cleared the bar it was published with? `Judgement.per_week`'s unit is
categories a week, over the weeks the move covers, so the realised quantity
compared against the bar is the claim's own realised value per week. It is
therefore "would the bar have passed this outcome", **not** "what did the
recommender say that morning", and for 2019–2025 it cannot be done at all,
because those seasons have no stored wire and no projection to decide from.

### 9. The hold definition sits beside the 30-day one, never replacing it, and its denominator answers a different question

The owner caught a real flaw in §6's four-of-eight-weeks test within minutes
of the first publish, on his own claim: a man claimed while injured or with
no role yet spends his early weeks unable to clear any bar at all, by
construction, however good he later turns out to be (§5a works this through
for Brandon Miller). A **second** test, `deserved_hold`
(`Raw.deserved_hold`, `scripts/keepers.py`), is scored over the man's own
**actual continuous hold** — the same run-based stay §1 already computes —
rather than a fixed thirty days, so a stash's dead early weeks are outnumbered
by the weeks he actually played once his role arrived, instead of being the
whole sample.

**The test, exactly.** Each whole matchup period the hold touches is scored
the identical way §2a already scores a week: `Replay.delta` with the claim
undone and the sign flipped, against the wire's replacement level **on the
claim day** — the same bar the 30-day test uses, so the two are reading the
same currency and the same hurdle, just over a different stretch of weeks. A
period counts as **available** only if he played at least one game in the
days of it his hold actually covered — a period only partly touched by the
hold (its first or last) is judged over the days he actually held it, not
the whole period. **Deserved over the hold** = above replacement in at least
half his available periods
(rounded up: three available periods needs two above, not one), with at
least two available periods. Both constants —
`HOLD_MIN_AVAILABLE = 2` and the half-rounded-up fraction — are declared in
`scripts/keepers.py` before this section's numbers were produced and were not
adjusted afterward, including after they turned out not to rescue Brandon
Miller's own claim (§5a).

**Why "played a game", not the injury reports.** `app/injuries.py`'s
point-in-time status (`docs/injuries.md`) would be the more precise
availability signal, but it only covers 2022–2026; 2019–2021 have no injury
reports at all (docs/injuries.md, "The older seasons ... are not loaded" for
anything before 2022, and no NBA report existed before the 2021-22 season).
A rule that only works on five of eight seasons cannot be the rule this
census runs on every season the same way, so `deserved_hold` uses
`player_game_stats.played` instead — the one signal every season has. The
injury reports are still used, but only as *context*, never as the test
itself: `--why` and §5b's stash table print the league's own status line on
the claim morning where it exists (2022+) and say `no injury-report coverage
before 2022` where it does not.

**What each definition's denominator can and cannot say.** The 30-day test's
1.6% is a rate over every claim this census can score (6,524 of 6,646
attributed adds, §Decisions 13) — it asks "of the claims this league actually
made, how many would have looked deserved after a fixed, decision-relevant
window", and a claim dropped on day one is a zero in that denominator exactly
as it should be. The hold test cannot be read the same way: its window
*is* how long the man was actually kept, which is not knowable at the moment
of the claim, so a man dropped after one bad week and a man dropped after one
*good* week both get exactly one period to their name, scored on whatever
that period happened to hold. `deserved_hold` therefore answers "was the
decision to keep him this long vindicated", not "was this claim, at the
moment it was made, a good bet" — the 30-day test's question. Both
definitions are published over the same population (`ranked`, claims with a
single dropped man) so the side-by-side comparison in §2a-hold is apples to
apples, but neither is a replacement for the other and the doc says which
question each one answers everywhere it is quoted.

### 10. Brandon Miller's own claim has no matched drop, and is invisible to every share in this document except the one built to find it

Miller's 2026 claim (tx 8299, day 23, Through The Wire, $2) carries an `ADD`
item and **no `DROP` item at all** — Through The Wire had an open roster
place, not a man to cut. §Decisions 13 excludes exactly this shape of claim
(`dropped_value` cannot be computed, so it is not in `ranked`) from every
percentage in §§1–5, the same rule that has governed this document since its
first publish. That means Miller is not merely failing the four-of-eight
test by construction (§5a) — under the existing convention he is not counted
by *either* test at all, and would not appear in this document even as a
"no". `scripts/keepers.py --why 512` (or any player id) carves a single
target out of that rule so `--why` and the worked example in §5a can still
show his real accounting; nothing else in the document's published shares
changes, and no other no-drop claim is added back into `ranked`. This is a
second, blunter version of the flaw the owner found: a stash into an open
slot is not just hard to call "deserved" — as scored today, it is invisible.

---

## The answer, up front

**Of the 1,145 executed 2026 adds the census can attribute to a roster run,
8.6% were still on the adding team's roster 30 days later, 1.3% were above
replacement for four of the next eight weeks, and the median man was held 3
days — and 9.3% deserved over the hold (104).**

That is the success check, and every part of it is recomputable from
`transactions`, `transaction_items` and `daily_lineup_slots`. The 30-day share
is the **run-based, continuous** reading (§Limitations 1); the 1.3% is
**15 of the 1,115 claims that carry a single dropped man** and can therefore be
scored in the product's currency (§Limitations 6, §Decisions 13) — this
paragraph previously misquoted that figure as 4.1%, the `≥60d` column from
§1's retention table, and it is corrected here. The 9.3% is **104 of the same
1,115**, over the man's actual hold rather than a fixed thirty days
(§Limitations 9, §2a-hold): 97 claims the 30-day reading missed and 7 that
clear both.

**The plain reading.** A waiver add in this league is a three-day rental. The
median man is gone inside a week; **68.5% of all 6,646 adds 2019–2026 were
dropped within seven days**, and only **10.7%** were ever held in one
uninterrupted spell past thirty. Of the 6,524 adds that can be scored in the
product's own currency, **1.6% deserved to stay** by the four-of-eight-weeks
bar and **10.7% deserved over the hold** — the man's own continuous stay,
scored the same way (§2a-hold; 652 of the 6,524 clear the hold and miss the
30-day bar, 59 run the other way). **27.5% returned more than zero categories
over the next 30 days**, and
the median returned **exactly zero** — he was worth nothing at all to the
roster that claimed him. The league's own swaps, measured by the backtest,
lose 0.56 categories over thirty days; this study says why: the man is almost
never around long enough to matter, and when he is, he is usually worse than
the wire he came from. **He was re-added by some team later in the season
76.4% of the time** — the league keeps wanting the men it keeps dropping, and
**99.6% of the men it wanted back were still not worth a roster place** by the
bar. In 2026 he was more often worse over the next thirty days than the man
dropped to make room for him: **54.8%**.

**It did not get worse in 2026 by the retention measures; it got worse by the
one that matters.** 2026's median stay (3 days) is tied for the shortest on
record with 2021, 2022 and 2025, and its 7-day drop share (71.6%) is the
second-highest behind 2025 (76.9%). The deeper fact is that retention has been
in single digits for a decade of this league and 2026's 8.6% is unremarkable.
What moved is the **share of adds that returned anything at all**: 2026's
**24.4% above zero is the lowest of the eight seasons** (the high is 2023's
32.4%, not 2019's), and the median 2026 add returned **−0.50 categories** —
**the only season in eight whose median 30-day return is negative.** Every
other season's median is exactly 0.00. **The wire is not thinner in 2026 — it
is more useless.**

---

## 0. The currency reproduces before anything is measured

`scripts/keepers.py` opens with the check every other study in this repo opens
with: `pickup_values` (`app/scoring/replacement.py`, the 14-day window) run
against `app/scoring/replacement.py`'s own published table. If this table did
not reproduce, nothing below it would mean anything.

| season | n | doc n | median | doc median |
|---|---|---|---|---|
| 2019 | 599 | 599 | 0.11 | 0.11 |
| 2020 | 543 | 543 | 0.13 | 0.13 |
| 2021 | 758 | 758 | 0.10 | 0.10 |
| 2022 | 648 | 648 | 0.09 | 0.09 |
| 2023 | 475 | 475 | 0.09 | 0.09 |
| 2024 | 789 | 789 | 0.07 | 0.07 |
| 2025 | 924 | 924 | 0.06 | 0.06 |
| 2026 | 973 | 973 | 0.07 | 0.07 |

Eight seasons, eight exact matches on both `n` and the median. The lens this
study scores in is the lens the repo published.

---

## 1. Retention

Every executed add (a `transactions` row of type `WAIVER` or `FREEAGENT`,
status `EXECUTED`, carrying an `ADD` item), followed for the man, the team and
the scoring period it is stamped with. Days held is the length of the
unbroken run of `daily_lineup_slots` rows for (team, man) beginning on the
claim day. `started` is the share of held days on which the man was in a
starting slot.

| season | n | med days | mean | ≥7d | ≥14d | ≥30d run | ≥60d | ≤7d | censored | started |
|---|---|---|---|---|---|---|---|---|---|---|
| 2019 | 700 | 4.00 | 14.92 | 41.9% | 24.4% | 12.1% | 6.9% | 62.3% | 11.6% | 68.2% |
| 2020 | 580 | 5.00 | 26.08 | 42.9% | 29.7% | 20.2% | 15.3% | 60.0% | 12.6% | 68.6% |
| 2021 | 817 | 3.00 | 10.88 | 31.6% | 19.8% | 8.8% | 4.2% | 71.0% | 10.2% | 67.5% |
| 2022 | 784 | 3.00 | 11.74 | 33.5% | 18.5% | 8.3% | 5.5% | 70.7% | 12.2% | 66.4% |
| 2023 | 599 | 6.00 | 17.76 | 44.9% | 27.7% | 16.5% | 8.3% | 59.1% | 16.7% | 70.0% |
| 2024 | 915 | 4.00 | 12.65 | 37.5% | 22.2% | 9.9% | 5.5% | 66.3% | 12.5% | 64.9% |
| 2025 | 1106 | 3.00 | 9.03 | 26.9% | 14.2% | 7.8% | 3.0% | 76.9% | 9.8% | 63.2% |
| **2026** | **1145** | **3.00** | **10.55** | **32.1%** | **17.2%** | **8.6%** | **4.1%** | **71.6%** | **10.0%** | **66.4%** |
| **pooled** | **6646** | **4.00** | **13.2** | — | — | **10.7%** | — | **68.5%** | — | — |

**The distribution, not the mean.** The mean (13.2 days pooled) is more than
three times the median (4.0). That gap is the whole story: a small tail of men
who are kept for months, and a large mass cut inside a week. **68.5% of every
add in this league's history was cut within seven days** — better than two
thirds, before he had a chance to be anything.

**The share of held days started tracks nothing.** 63–70% in every season, no
trend, and the low point is 2025 (63.2%), not a year with any retention
catastrophe. Managers start the men they just claimed at about the same rate
they start anyone else, because a claimed man is a body in a lineup that was
already short.

### 1b. The loose reading, printed so the two cannot be confused

| season | n | median "loose" | loose >30d | run-based ≥7d | run-based ≥30d |
|---|---|---|---|---|---|
| 2019 | 700 | 10.00 | 29.0% | 41.9% | 12.1% |
| 2020 | 580 | 12.00 | 37.8% | 42.9% | 20.2% |
| 2021 | 817 | 9.00 | 28.5% | 31.6% | 8.8% |
| 2022 | 784 | 8.00 | 25.4% | 33.5% | 8.3% |
| 2023 | 599 | 8.00 | 27.9% | 44.9% | 16.5% |
| 2024 | 915 | 8.00 | 26.4% | 37.5% | 9.9% |
| 2025 | 1106 | 6.00 | 25.0% | 26.9% | 7.8% |
| **2026** | **1145** | **7.00** | **24.6%** | **32.1%** | **8.6%** |

"Loose >30" is last held day by that team minus the claim day, greater than
30, with gaps and second spells counted as held. §Limitations 1 says exactly
what is inside it. Its pooled median is 8.0 days against the run-based 4.0.

### 1c. Censoring, in full

See §Limitations 2 for the table and the reading. The three 2026 numbers, so
there is no ambiguity:

| reading, 2026 | value | denominator |
|---|---|---|
| **run-based, all attributed adds (the headline)** | **8.6%** | 1,145 |
| run-based, only adds that can be observed for 30 days | 11.0% | 903 |
| run-based, all attributed adds, censored ones counted as held | 8.6% | 1,145 |

The headline is the first. Censoring makes no difference to it in 2026
because the two coincide — the adds that cannot be observed are not being
counted as keeps — and the reachable-only figure is *higher*, so censoring is
not what makes 2026's retention look low. A reader comparing 2026 against 2020
or 2023 should use the "of reachable" column in §Limitations 2, which is the
like-for-like comparison.

---

## 2. Did he deserve to stay

### 2a. The bar, and the share that cleared it

Realised value over the 30 days after the claim is the counterfactual the
backtest and `scripts/faab_bids.py` already use: the matchup that actually
happened is replayed with the claim **undone** and the sign flipped — the man
dropped goes back into the lineup, the added man comes out — so a real claim
is scored in the same quantity the recommender's own moves are. It costs
about 0.04s a claim because `Replay`'s day-by-day solve is memoized per
period, which is what makes an eight-season census affordable.

The hurdle is the wire's replacement level on the **day he was added**: the
best free agent left before the claim, same lens, floored at
`TYPICAL_PICKUP`. "Deserved" is beating that in at least four of the next
eight whole weeks.

| season | n scored | deserved (4 of 8) | 30d value > 0 | median 30d | mean 30d | median wire |
|---|---|---|---|---|---|---|
| 2019 | 688 | 0.7% | 28.9% | 0.00 | −0.28 | 0.20 |
| 2020 | 572 | 1.7% | 28.5% | 0.00 | −0.40 | 0.21 |
| 2021 | 803 | 1.2% | 27.0% | 0.00 | −0.44 | 0.20 |
| 2022 | 768 | 2.1% | 30.1% | 0.00 | −0.34 | 0.19 |
| 2023 | 590 | 2.0% | 32.4% | 0.00 | −0.14 | 0.17 |
| 2024 | 895 | 0.6% | 27.3% | 0.00 | −0.23 | 0.17 |
| 2025 | 1093 | 3.0% | 25.2% | 0.00 | −0.44 | 0.20 |
| **2026** | **1115** | **1.3%** | **24.4%** | **−0.50** | **−0.56** | **0.19** |
| **pooled** | **6524** | **1.6%** | **27.5%** | **0.00** | — | — |

**Read this as: about one add in sixty-three deserved to stay, and the median
add returned nothing.** The pooled 30-day mean is −0.37 categories; 2026's is
−0.56, matching the backtest's own baseline of −0.558 over thirty days on the
league's real swaps. Two independent routes to the same number is the check
that the machinery did not move.

**The median wire is 0.17–0.21 categories a week in every season.** The bar a
claim had to clear to be "deserved" was therefore around a fifth of a
category a week — three to three-and-a-half times `TYPICAL_PICKUP`, and under
half of `OPENED_PLACE`. **The bar is low and the claims still miss it.** That
is the single most damning number in this document.

Note the shape of 2026: it has one of the *better* `deserved` shares (1.3%
against a pooled 1.6%) while having the worst median 30-day value on record.
The `deserved` bar can be cleared on a lucky fortnight late in the season,
which is what happened — see §5.

### 2a-hold. A second bar, over the man's actual hold, never replacing the first

§2a's four-of-eight-weeks test cannot pass a claim whose early weeks were
dead — hurt, or no role yet — however good he turns out to be, because those
weeks are scored anyway and count against him (§Limitations 9). `deserved_hold`
is the same machinery (`Replay.delta`, the claim undone and sign-flipped,
against the claim day's wire) scored over the man's own **continuous hold**
instead: above replacement in at least half his available periods (a period
counts if he played a game in the days of it he held), needing at least two
available periods. Both constants (`HOLD_MIN_AVAILABLE = 2`, "at least half"
rounded up) are declared in `scripts/keepers.py` before this run and were not
tuned afterward — including after they turned out not to rescue Brandon
Miller's own claim (§5a).

Published over the same population as §2a (`ranked`, claims with a single
dropped man, §Decisions 13):

| season | n | deserved 30d | deserved hold | reclassified | reverse | med avail | med above | med hold value |
|---|---|---|---|---|---|---|---|---|
| 2019 | 688 | 0.7% | 11.9% | 77 | 0 | 1.00 | 0.00 | 0.00 |
| 2020 | 572 | 1.7% | 13.5% | 73 | 6 | 1.00 | 0.00 | 0.00 |
| 2021 | 803 | 1.2% | 9.7% | 72 | 4 | 1.00 | 0.00 | 0.00 |
| 2022 | 768 | 2.1% | 11.2% | 79 | 9 | 1.00 | 0.00 | 0.00 |
| 2023 | 590 | 2.0% | 13.9% | 76 | 6 | 1.00 | 0.00 | 0.00 |
| 2024 | 895 | 0.6% | 9.6% | 82 | 1 | 1.00 | 0.00 | 0.00 |
| 2025 | 1093 | 3.0% | 9.5% | 96 | 25 | 1.00 | 0.00 | 0.00 |
| **2026** | **1115** | **1.3%** | **9.3%** | **97** | **8** | **1.00** | **0.00** | **0.00** |
| **pooled** | **6524** | **1.6%** | **10.7%** | **652** | **59** | — | — | — |

"Reclassified" is every claim that **fails** the 30-day test and **passes**
the hold test; "reverse" is the other way — passed 30 days, failed the hold
(§2a-hold's "one-good-week men", below). The median row is 1.00/0.00/0.00 in
every season because the median claim is held only a few days (§1): most
claims give the hold test exactly one available period to work with, and the
plain count the brief asks for — periods above replacement, and the hold's
total realised value — is genuinely zero for the median man, on both tests
alike. **The hold test roughly triples the pooled deserved share (1.6% →
10.7%), and almost all of the movement is new claims the 30-day test missed
by construction, not claims it got wrong the other way**: 652 reclassified
against 59 reverse, an 11-to-1 ratio.

**What the product should carry, and why.** The two tests answer different
questions (§Limitations 9) and neither should replace the other on the page.
But for a manager deciding what a claim's actual stay was worth after the
fact — the rest-of-season report's own use case — `deserved_hold` is the
truer number: it does not fail a man for being hurt when he was claimed, and
it is scored over the days the roster actually had him rather than a window
picked for census convenience. The 30-day test should stay as the
decision-time reading (what does four weeks in, judged the same way every
time, tell a manager considering a claim right now); the hold test should be
the retrospective one (was keeping this man, for as long as he was kept,
vindicated). The 1.6% headline undercounts "deserved" by roughly two thirds
against the hold reading, and Brandon Miller's own claim is the reason this
document exists — but, worked through below, the hold test does not rescue
it either.

**2026 reclassified — failed the 30-day test, passed the hold test (n=97):**

| day | player | team | paid | held d | avail/periods | above | hold value |
|---|---|---|---|---|---|---|---|
| 1 | Grayson Allen | Masters of their Domains | $0 | 12 | 2/2 | 2 | 2.00 |
| 2 | Brice Sensabaugh | BC KO | $10 | 10 | 2/2 | 1 | 0.50 |
| 4 | Tre Jones | Fantastic 5 | $2 | 10 | 2/2 | 1 | 1.00 |
| 5 | Nickeil Alexander-Walker | Fantastic 5 | $1 | 156 | 22/22 | 11 | 17.50 |
| 5 | Aaron Wiggins | The Infirmary | $0 | 6 | 2/2 | 1 | 0.00 |
| 6 | Wendell Carter Jr. | Fast and Curryous | $5 | 21 | 3/4 | 2 | 0.50 |
| 6 | Jaime Jaquez Jr. | Uncle Dennis's Phone | $0 | 6 | 2/2 | 1 | 1.00 |
| 7 | Quentin Grimes | Foxes ShutUpNDribble | $6 | 20 | 3/3 | 2 | 3.00 |
| 7 | Keldon Johnson | Fast and Curryous | $1 | 10 | 2/2 | 1 | 0.00 |
| 8 | Josh Minott | Masters of their Domains | $0 | 20 | 3/3 | 2 | 1.00 |
| 10 | Jarace Walker | Through The Wire | $5 | 7 | 2/2 | 1 | 1.00 |
| 10 | Dillon Brooks | LeBron's Load Management LLC | $0 | 70 | 10/11 | 5 | 3.50 |
| 12 | Miles McBride | Chat GTP inspired | $2 | 3 | 2/2 | 1 | 0.50 |
| 12 | Mike Conley | BC KO | $0 | 3 | 2/2 | 2 | 2.00 |
| 14 | Bennedict Mathurin | Through The Wire | $5 | 31 | 3/5 | 2 | 1.50 |
| 18 | Jaylon Tyson | BC KO | $1 | 13 | 2/3 | 2 | 1.50 |
| 19 | Isaiah Jackson | BC KO | $4 | 8 | 2/2 | 1 | 0.50 |
| 20 | Trendon Watford | Through The Wire | $6 | 3 | 2/2 | 1 | 0.50 |
| 25 | Duncan Robinson | Fantastic 5 | $0 | 17 | 3/3 | 2 | 2.50 |
| 27 | Andre Drummond | Fast and Curryous | $1 | 13 | 2/3 | 1 | 1.00 |
| 31 | Ausar Thompson | Foxes ShutUpNDribble | $21 | 7 | 2/2 | 1 | 0.50 |
| 32 | Daniel Gafford | Foxes ShutUpNDribble | $6 | 4 | 2/2 | 1 | 1.00 |
| 33 | Tobias Harris | Fantastic 5 | $2 | 41 | 7/7 | 4 | 5.00 |
| 37 | Saddiq Bey | Masters of their Domains | $1 | 38 | 6/6 | 4 | 4.00 |
| 40 | Kris Dunn | Masters of their Domains | $2 | 6 | 2/2 | 1 | 1.00 |
| 41 | Josh Okogie | Ben's Need Some VC | $0 | 4 | 2/2 | 1 | 0.50 |
| 42 | Wendell Carter Jr. | BC KO | $3 | 13 | 2/2 | 1 | 1.00 |
| 44 | Bobby Portis | Brockley Heat | $0 | 117 | 16/16 | 10 | 8.50 |
| 45 | Aaron Wiggins | Ben's Need Some VC | $0 | 12 | 2/3 | 1 | 1.00 |
| 45 | Kyle Filipowski | Fantastic 5 | $0 | 9 | 2/2 | 1 | 2.00 |
| 47 | Justin Champagnie | Brockley Heat | $0 | 23 | 4/4 | 3 | 2.50 |
| 48 | Pat Spencer | Chat GTP inspired | $0 | 8 | 2/2 | 1 | 1.00 |
| 53 | Jalen Smith | Masters of their Domains | $0 | 6 | 2/2 | 1 | 0.50 |
| 55 | Dominick Barlow | BC KO | $0 | 19 | 4/4 | 2 | 2.00 |
| 61 | Jordan Poole | Ben's Need Some VC | $12 | 38 | 6/7 | 3 | 3.00 |
| 61 | Bub Carrington | Chat GTP inspired | $0 | 48 | 8/8 | 4 | 0.50 |
| 66 | Tim Hardaway Jr. | Foxes ShutUpNDribble | $0 | 5 | 2/2 | 1 | 1.00 |
| 68 | Egor Demin | Through The Wire | $0 | 9 | 2/2 | 1 | 0.50 |
| 70 | Aaron Nesmith | Chat GTP inspired | $5 | 40 | 6/6 | 3 | 3.50 |
| 73 | Isaiah Collier | Foxes ShutUpNDribble | $0 | 8 | 2/2 | 1 | 1.00 |
| 75 | Sam Hauser | LeBron's Load Management LLC | $0 | 3 | 2/2 | 1 | 1.00 |
| 75 | Sandro Mamukelashvili | Brighton Bears | $0 | 6 | 2/2 | 1 | 0.00 |
| 76 | Goga Bitadze | BC KO | $0 | 4 | 2/2 | 1 | 1.00 |
| 76 | Marvin Bagley III | BC KO | $2 | 7 | 2/2 | 1 | 0.50 |
| 77 | Brice Sensabaugh | Brockley Heat | $1 | 84 | 11/11 | 6 | 6.50 |
| 78 | Sam Merrill | Foxes ShutUpNDribble | $0 | 9 | 2/2 | 1 | 1.00 |
| 82 | Nikola Jovic | LeBron's Load Management LLC | $0 | 5 | 2/2 | 1 | −1.00 |
| 83 | Dominick Barlow | Fast and Curryous | $0 | 2 | 2/2 | 1 | 1.00 |
| 85 | Aaron Wiggins | LeBron's Load Management LLC | $0 | 13 | 2/2 | 2 | 1.50 |
| 86 | Miles McBride | BC KO | $3 | 9 | 2/2 | 1 | 0.50 |
| 87 | Anfernee Simons | Optimize the MVPs | $1 | 7 | 2/2 | 1 | 2.50 |
| 89 | Day'Ron Sharpe | LeBron's Load Management LLC | $0 | 6 | 2/2 | 1 | 0.50 |
| 91 | Sam Hauser | The Infirmary | $3 | 10 | 2/2 | 2 | 1.50 |
| 92 | Malik Monk | BC KO | $0 | 13 | 2/2 | 1 | 0.50 |
| 92 | Moses Moody | Chat GTP inspired | $3 | 15 | 3/3 | 2 | 2.00 |
| 94 | Tre Johnson | LeBron's Load Management LLC | $0 | 11 | 2/2 | 1 | 0.50 |
| 95 | Egor Demin | Masters of their Domains | $1 | 5 | 2/2 | 1 | 1.00 |
| 96 | Miles McBride | BC KO | $0 | 7 | 2/2 | 1 | 1.00 |
| 99 | Bilal Coulibaly | Through The Wire | $1 | 8 | 2/2 | 1 | 1.00 |
| 100 | Klay Thompson | BC KO | $0 | 9 | 2/2 | 1 | 0.00 |
| 103 | Nikola Jovic | BC KO | $1 | 4 | 2/2 | 1 | 0.50 |
| 103 | Ty Jerome | Through The Wire | $0 | 30 | 4/4 | 2 | 1.50 |
| 103 | Jock Landale | Optimize the MVPs | $0 | 3 | 2/2 | 1 | 0.50 |
| 105 | Davion Mitchell | Fast and Curryous | $5 | 22 | 2/3 | 1 | 1.00 |
| 105 | Herbert Jones | BC KO | $1 | 8 | 2/2 | 1 | 0.50 |
| 107 | Robert Williams III | Ben's Need Some VC | $0 | 22 | 2/3 | 1 | 1.50 |
| 108 | Max Christie | Through The Wire | $2 | 6 | 2/2 | 1 | 0.50 |
| 109 | Taylor Hendricks | Masters of their Domains | $3 | 4 | 2/2 | 1 | −0.50 |
| 109 | Scoot Henderson | Optimize the MVPs | $1 | 25 | 3/4 | 3 | 2.50 |
| 111 | Landry Shamet | LeBron's Load Management LLC | $0 | 4 | 2/2 | 2 | 1.50 |
| 122 | Dominick Barlow | BC KO | $0 | 6 | 2/2 | 1 | −0.50 |
| 123 | Moussa Diabate | Through The Wire | $0 | 13 | 2/3 | 2 | 2.00 |
| 125 | Andrew Nembhard | BC KO | $0 | 25 | 4/5 | 2 | 2.00 |
| 125 | Jared McCain | Optimize the MVPs | $0 | 16 | 4/4 | 3 | 2.50 |
| 126 | Herbert Jones | Masters of their Domains | $0 | 14 | 2/2 | 1 | 1.00 |
| 126 | Moses Moody | Masters of their Domains | $1 | 13 | 2/2 | 1 | 1.00 |
| 128 | Scotty Pippen Jr. | Ben's Need Some VC | $0 | 11 | 2/2 | 1 | −0.50 |
| 129 | Jayson Tatum | Optimize the MVPs | $0 | 32 | 4/5 | 2 | 1.00 |
| 130 | Gui Santos | Brighton Bears | $2 | 31 | 5/5 | 3 | 2.00 |
| 131 | Bilal Coulibaly | Fast and Curryous | $0 | 4 | 2/2 | 1 | 0.50 |
| 131 | Davion Mitchell | Fast and Curryous | $2 | 4 | 2/2 | 2 | 2.50 |
| 134 | Nic Claxton | Ben's Need Some VC | $0 | 12 | 2/2 | 1 | −0.50 |
| 138 | Cedric Coward | Chat GTP inspired | $0 | 22 | 4/4 | 2 | −0.50 |
| 139 | Keon Ellis | Brighton Bears | $0 | 2 | 2/2 | 1 | 0.50 |
| 142 | Kris Dunn | Brighton Bears | $1 | 19 | 3/3 | 2 | 1.50 |
| 142 | Kevin Porter Jr. | Through The Wire | $0 | 19 | 2/3 | 1 | 1.00 |
| 143 | Danny Wolf | Through The Wire | $0 | 11 | 2/2 | 1 | 2.00 |
| 145 | Daeqwon Plowden | BC KO | $0 | 9 | 2/2 | 1 | 0.50 |
| 146 | John Collins | Brighton Bears | $7 | 15 | 2/3 | 1 | 0.50 |
| 146 | Khris Middleton | Fantastic 5 | $1 | 2 | 2/2 | 1 | 0.50 |
| 146 | Herbert Jones | Through The Wire | $0 | 15 | 2/3 | 1 | 2.50 |
| 147 | Isaiah Jackson | Through The Wire | $0 | 8 | 2/2 | 1 | 2.50 |
| 148 | Cameron Payne | Fantastic 5 | $1 | 9 | 2/2 | 1 | 0.50 |
| 149 | Sandro Mamukelashvili | Fantastic 5 | $1 | 12 | 2/2 | 1 | 0.50 |
| 149 | Walter Clayton Jr. | Through The Wire | $0 | 12 | 2/2 | 1 | 0.00 |
| 150 | Maxime Raynaud | BC KO | $5 | 11 | 2/2 | 1 | 1.00 |
| 153 | Matisse Thybulle | Fantastic 5 | $3 | 2 | 2/2 | 1 | 0.50 |

**2026 reverse — passed the 30-day test, failed the hold test, the
one-good-week men (n=8):**

| day | player | team | paid | held d | avail/periods | above | hold value |
|---|---|---|---|---|---|---|---|
| 72 | De'Anthony Melton | Masters of their Domains | $0 | 1 | 1/1 | 1 | 0.50 |
| 123 | Derrick Jones Jr. | The Infirmary | $0 | 3 | 1/1 | 1 | 1.00 |
| 154 | Andrew Wiggins | The Infirmary | $10 | 7 | 1/1 | 1 | 1.00 |
| 154 | Daniss Jenkins | Masters of their Domains | $1 | 7 | 1/1 | 1 | 1.00 |
| 154 | Jaime Jaquez Jr. | Brighton Bears | $0 | 7 | 1/1 | 1 | 1.00 |
| 154 | Tre Jones | Fantastic 5 | $1 | 7 | 1/1 | 1 | 1.00 |
| 155 | Daeqwon Plowden | Masters of their Domains | $0 | 6 | 1/1 | 1 | 1.00 |
| 160 | Gary Trent Jr. | The Infirmary | $5 | 1 | 1/1 | 1 | 1.00 |

Every row reads `1/1` in `avail/periods`, and that is the whole mechanism:
the 30-day test is a **sustained hypothetical** — it scores what the claim
would have returned had it stayed in the lineup the full 30 days, whatever
actually happened to the real roster — so a man genuinely dropped after
three days (Derrick Jones Jr.) or one (De'Anthony Melton, Gary Trent Jr.)
can still clear four-of-eight on the strength of games he played for his own
NBA team while hypothetically still rostered. `deserved_hold` scores none of
that: it only ever sees the periods his **actual** hold touched, and
`HOLD_MIN_AVAILABLE = 2` means a hold with a single available period cannot
pass regardless of how good that period was. Six of these eight are exactly
six of the nine after-day-150 claims §Limitations 6 already flagged as
thin (Andrew Wiggins, Daniss Jenkins, Jaime Jaquez Jr.'s day-154 claim, Tre
Jones, Daeqwon Plowden, Gary Trent Jr.); the other three from that group of
nine — Mitchell Robinson, Ziaire Williams and Cameron Johnson, all claimed
day 153 — clear the hold test too and are not reversed. The remaining two
reverse rows, Melton and Derrick Jones Jr., were among the six claims
§5 said "survive" striking the nine — real, short, genuinely productive
stays that the hold test still will not call consistent on one data point.
The hold test is not more lenient than the 30-day one in general
(§2a-hold's pooled ratio is 11 reclassified for every 1 reverse) — it is
*less* lenient specifically for a short, lucky stay, because a short hold
gives the consistency test almost no room to be wrong, and `deserved_30`'s
own sustained-hypothetical framing can credit a man for weeks he was never
actually rostered for.

### 2b. Did he finish the season as a top-100 man?

Rest-of-season rank is `player_season_stats` season totals (`kind = 'total'`),
scaled to a week and run through the same `marginal` lens, ranked across the
season's players. "Top 100" means the top hundred by **what the product
values**, not by a raw points total. A claim made in week two is judged on the
season's whole production, so the reading is only ever "did this man end the
season as a top-100 player", which is the brief's question.

| season | n | top 100 at season end | of those, deserved |
|---|---|---|---|
| 2019 | 688 | 38.1% | 0.0% |
| 2020 | 572 | 38.3% | 1.4% |
| 2021 | 803 | 32.8% | 1.1% |
| 2022 | 768 | 32.2% | 2.8% |
| 2023 | 590 | 26.8% | 2.5% |
| 2024 | 895 | 27.8% | 0.4% |
| 2025 | 1093 | 30.8% | 3.0% |
| **2026** | **1115** | **25.1%** | **1.8%** |
| **pooled** | **6524** | **30.9%** | **1.6%** |

**This column is a trap and I am labelling it as one.** 31% of claimed men end
the season as top-100 players, which sounds like a third of pickups work. It
does not, for two reasons, both stated here rather than left for a critic to
find. First, **the top 100 is a large slice of a small league** — 100 of
roughly 350 rostered men, so a man can be top-100 and still be the tenth-best
player on his own roster; "top 100" is not "worth a place here". Second,
**this reading has no counterfactual**: it says what the man did over the
whole season, not what he did for the roster that claimed him, and a
breakout in February counts the same as one in November. Of the claims that
reached the top 100, **1.6% cleared the four-of-eight-weeks bar** — the two
readings are nearly orthogonal, which is itself the finding: **a man finishing
as a top-100 player tells you almost nothing about whether the claim that
acquired him was worth making.**

### 2c. Was the league still asking for him?

A man is "re-added" when any team adds him on a day after the claim, by any
route (`WAIVER` or `FREEAGENT`).

| season | n | re-added by ANY team | of those, deserved |
|---|---|---|---|
| 2019 | 688 | 70.6% | 0.4% |
| 2020 | 572 | 68.2% | 0.3% |
| 2021 | 803 | 73.1% | 0.3% |
| 2022 | 768 | 72.3% | 0.9% |
| 2023 | 590 | 65.3% | 0.5% |
| 2024 | 895 | 73.0% | 0.3% |
| 2025 | 1093 | 76.9% | 0.5% |
| **2026** | **1115** | **76.4%** | **0.4%** |
| **pooled** | **6524** | **72.8%** | **0.4%** |

**This is the cruellest number here.** Three quarters of every claimed man is
claimed again by somebody later in the season — the league is not merely
indifferent, it is actively churning the same men. And **99.6% of the men the
league wanted back were still not worth a roster place** by the bar. The wire
is not a market discovering value; it is the same pool of replacement-level
bodies being passed around.

### 2d. Was he worse than the man dropped for him?

Both men scored over the same 30-day window in the same currency, the dropped
man read the other way round (what he was worth to the roster that let him
go).

| season | n | worse than the man dropped | median dropped 30d |
|---|---|---|---|
| 2019 | 688 | 45.9% | 0.00 |
| 2020 | 572 | 50.7% | 0.00 |
| 2021 | 803 | 50.7% | 0.00 |
| 2022 | 768 | 49.1% | 0.00 |
| 2023 | 590 | 40.0% | 0.00 |
| 2024 | 895 | 43.5% | 0.00 |
| 2025 | 1093 | 51.6% | 0.00 |
| **2026** | **1115** | **54.8%** | **0.00** |
| **pooled** | **6524** | **48.9%** | **0.00** |

**A coin flip, tilting the wrong way.** Half the time the claimed man delivered
less over thirty days than the man cut to make room for him — and the median
dropped man was worth zero too, so half the league's moves are a swap of one
nothing for another nothing. Which is exactly what the backtest's −0.56 over
thirty days is made of.

---

## 3. By what was known at the claim

### 3a. By the man's value rank on the wire that morning

Rank is `app.pickups.bids.value_rank` on the day's free-agent pool, bucketed
by `bids.BUCKETS`.

| rank bucket | n | held ≥30d | dropped ≤7d | deserved | top 100 |
|---|---|---|---|---|---|
| 1–5 | 28 | 17.9% | 53.6% | 0.0% | 50.0% |
| 6–15 | 36 | 11.1% | 52.8% | 0.0% | 13.9% |
| 16–40 | 34 | 20.6% | 47.1% | 0.0% | 35.3% |
| **41+** | **6426** | **10.6%** | **68.8%** | **1.6%** | **30.9%** |

**The top-40 buckets are 98 claims out of 6,524 and they are worse than the
tail on retention.** Claims on the top-ranked free agents were dropped within
seven days 47–54% of the time against 69% for the tail, and held past thirty
days 11–21% of the time. **Not one of them, in eight seasons, deserved to
stay** — and the three buckets together are n=98, so this says "no evidence
that the top of the wire is better" rather than "it is worse". It is consistent
with `docs/faab.md`'s finding that half the top claims go uncontested: the
top of *this* wire is not a scarce good.

### 3b. By FAAB paid (2026 — the only FAAB season)

| paid | n | held ≥30d | deserved | top 100 |
|---|---|---|---|---|
| $0 | 688 | 4.8% | 1.0% | 25.1% |
| $1–5 | 390 | 12.3% | 1.3% | 25.4% |
| $6–20 | 35 | 37.1% | 8.6% | 22.9% |
| $21+ | 2 | 0.0% | 0.0% | 0.0% |

**Money predicts retention and almost nothing else.** A $6–20 claim is held
past thirty days 37.1% of the time against 4.8% for a free add — **a 7.7-fold
difference** on n=35, which is the one number in this section I would defend.
But the *deserved* share only moves 1.0% → 8.6%, and of the fifteen 2026
claims that did deserve to stay, **twelve were free or $1–5** and only three
cost more than that. **Managers can tell which men will stay; they cannot tell
which men will be good.** That is the cleanest statement of the product
opportunity in this document.

### 3c. By month

The month is read off `player_game_stats.game_date` (the modal month of the
boxes played in that scoring period); ten 2026 days carry no boxes and fall
back to the neighbouring period.

| month | n | held ≥30d | deserved |
|---|---|---|---|
| 2018-10 | 401 | 14.7% | 0.5% |
| 2018-11 | 287 | 8.4% | 1.0% |
| 2019-10 | 280 | 13.9% | 0.7% |
| 2019-11 | 292 | 25.7% | 2.7% |
| 2020-12 | 344 | 12.8% | 0.6% |
| 2021-01 | 459 | 6.1% | 1.7% |
| 2021-10 | 470 | 11.3% | 1.3% |
| 2021-11 | 298 | 2.3% | 3.4% |
| 2022-10 | 346 | 19.4% | 0.6% |
| 2022-11 | 194 | 9.3% | 5.2% |
| 2023 p1 | 50 | 24.0% | 0.0% |
| 2023-10 | 258 | 14.3% | 0.8% |
| 2023-11 | 637 | 8.3% | 0.5% |
| 2024-10 | 463 | 10.2% | 0.9% |
| 2024-11 | 630 | 6.2% | 4.6% |
| 2025-10 | 428 | 11.7% | 0.7% |
| 2025-11 | 687 | 6.4% | 1.7% |

**No month effect worth carrying.** The `deserved` share wanders between 0.5%
and 5.2% with no pattern that survives the n, and the retention column's
spread (2.3% to 25.7%) tracks how much of the season was left, not the
calendar. The `2023 p1` row is a scoring period the month reader could not
place; n=50 and it changes nothing.

### 3d. Would the recommender's own bar have passed it?

**2026 only, and it is a re-reading, not a replay** (§Limitations 8). The bar
is the one shipped since 2026-09-21: `SEASON_HURDLE_PAID` 0.20 categories a
week for a claim that costs FAAB, `SEASON_HURDLE_FREE` 0.10 for one that does
not.

| shipped bar | n | passed | of those, held ≥30d | of those, deserved |
|---|---|---|---|---|
| paid (0.20/wk) | 427 | 91 | 33.0% | 8.8% |
| free (0.10/wk) | 688 | 132 | 12.9% | 5.3% |

**The bar is doing real work.** A claim whose realised net would have cleared
the paid bar was held past thirty days **33.0%** of the time against 4.8% for
all free adds, and deserved to stay **8.8%** of the time against a 1.3%
season baseline — a **6.8×** lift on n=91. The free bar is much weaker (12.9%
and 5.3%), which is the expected asymmetry: a free add into an open place was
always going to be held, so retention there is nearly uninformative.

Read the caveat properly: this is not evidence that the recommender *named*
these claims. It is evidence that **the bar's threshold, applied to what
actually happened, separates keeps from streams about seven times better than
chance.** For 2019–2025 this cut cannot be made at all — no stored wire, no
projection — and this document does not attempt it.

---

## 4. By league size

2019–2021 were 10-team, 2022 and 2025 12-team, 2024 and 2026 14-team, 2023
16-team.

| teams | seasons | n | held ≥30d | dropped ≤7d | deserved | median wire |
|---|---|---|---|---|---|---|
| 10 | 3 | 2097 | 13.1% | 65.0% | 2.8% | 0.20 |
| 12 | 2 | 1890 | 8.0% | 74.3% | 4.1% | 0.19 |
| 14 | 2 | 2060 | 9.2% | 69.3% | 3.4% | 0.18 |
| **16** | **1** | **599** | **16.5%** | **59.1%** | **3.5%** | **0.17** |

**The wire is thinner with more teams, and by less than the raw numbers
suggest.** The lens's replacement value on a mid-season day — the best man
left on the wire — falls monotonically with league size: **0.20 at ten teams,
0.19 at twelve, 0.18 at fourteen, 0.17 at sixteen.** That is a real
measurement of a thinner wire and it is what the 2027 question asks: **a
twenty-team-equivalent wire would leave the best available man at roughly
0.15–0.16 categories a week.**

**But the retention and deserved columns do not move monotonically**, and the
16-team season is the *best* on retention (16.5% held past thirty days, 59.1%
dropped inside a week) — better than the 10-team seasons on both. The
season-level confound is too strong to read through: 2023 is also the season
with the fewest adds (599, or 37 per team) and the strongest draft retention
(51.9%, `docs/roster_churn.md`), i.e. a season in which managers mostly stood
pat, which is a fact about 2023 and not about sixteen teams.

**What this predicts for 2027, plainly.** Going 14 → 16 teams removes
roughly **0.01 categories a week** from the best man on the wire by this
lens — about a sixth of `TYPICAL_PICKUP`, and the direction is not in doubt
because the ordering is clean across four sizes. Two cautionary notes, both
of which cut against the obvious reading:

- **The add volume does not rise with league size in this league's history.**
  Adds per team-season: **69.9** at ten teams, **78.8** at twelve, **73.6** at
  fourteen, **37.4** at sixteen. The 16-team season ran *the fewest* adds per
  team, by a wide margin, and there is no trend in the middle. So the
  prediction "more teams, more churn" is **not supported by these eight
  seasons** and I am not making it; what the 16-team season actually shows is
  men holding tight, which is what a thin wire does to a manager who cannot
  replace what he cuts.
- **`OPENED_PLACE` (0.38) is the one constant a 16-team wire touches**, and it
  is measured against the wire *here* (10-to-14 teams). A thinner wire means
  the same lane is streaming worse men into the same slots, so 0.38 is, if
  anything, high. But `docs/streaming_lane.md` already caps it below an
  ordinary held place (0.43), so the room to move is small in both directions
  and **this study has no measurement of a sixteen-team lane.** The proposal
  is a measurement, not a constant change: re-run `scripts/streaming_lane.py`
  after 2027 and move it only if the per-place median moves by more than that
  doc's own spread.

The honest summary: **sixteen teams makes the wire about a sixth thinner by
the lens's own replacement value, and on this league's record it makes
managers churn *less*, not more.** Neither effect is the reason pickups do not
work — the median add returning zero is.

---

## 5. The men worth the money

**Fifteen 2026 claims, of 1,115 scored, cleared the four-of-eight-weeks bar.**
n=15 is small enough that nothing here is a rate. The list, with what was
knowable the morning of the claim: his rank on that day's wire, his minutes
over his last five games and the five before those (from `player_game_stats`
only, so no hindsight), and where he finished the season by rest-of-season
value.

| day | player | paid | rank | held d | 30d | ros rank | mp last 5 | mp prior 5 |
|---|---|---|---|---|---|---|---|---|
| 3 | Donovan Clingan | $11 | #109 | 74 | +5.00 | 20 | 26.4 | 26.2 |
| 4 | Ryan Rollins | $0 | #109 | 157 | +4.00 | 68 | 31.4 | 30.8 |
| 13 | Jaime Jaquez Jr. | $10 | #115 | 125 | +4.00 | 132 | 28.8 | 25.6 |
| 87 | Wendell Carter Jr. | $2 | #100 | 74 | +4.00 | 32 | 25.2 | 30.0 |
| 123 | Derrick Jones Jr. | $0 | #96 | 3 | +4.00 | 86 | 24.4 | 25.4 |
| 72 | De'Anthony Melton | $0 | #92 | 1 | +2.50 | 141 | 22.8 | 24.8 |
| 153 | Mitchell Robinson | $0 | #81 | 8 | +2.00 | 302 | 19.2 | 21.4 |
| 153 | Ziaire Williams | $3 | #81 | 8 | +1.50 | 134 | 23.6 | 21.6 |
| 153 | Cameron Johnson | $0 | #81 | 8 | +1.50 | 131 | 32.0 | 29.4 |
| 154 | Andrew Wiggins | $10 | #88 | 7 | +1.00 | 5 | 26.0 | 29.8 |
| 154 | Daniss Jenkins | $1 | #88 | 7 | +1.00 | 188 | 24.6 | 34.2 |
| 154 | Jaime Jaquez Jr. | $0 | #88 | 7 | +1.00 | 132 | 28.8 | 25.6 |
| 154 | Tre Jones | $1 | #88 | 7 | +1.00 | 224 | 26.6 | 26.4 |
| 155 | Daeqwon Plowden | $0 | #88 | 6 | +1.00 | 281 | 30.4 | 29.4 |
| 160 | Gary Trent Jr. | $5 | #88 | 1 | +1.00 | 339 | 22.4 | 20.8 |

**Is upside visible in advance in this data? Largely no, and the exceptions
are not signals a tool can trade on.**

- **Nothing in the rank column.** Every one of the fifteen was claimed at rank
  **#81 to #115 on that day's wire** — the far tail, where the rank carries no
  information (§3a). Two of them (Jaquez on day 13, again on day 154) are the
  same man claimed twice, and the day-13 claim is one of the largest returns
  in the set. **The tool's own ranking said nothing, fifteen times out of
  fifteen.**
- **Nothing in the minutes trend.** Eleven of the fifteen were already playing
  a full starter's load (19–32 minutes a night) before the claim, so there was
  no role to grow into; four were in the 19–23 range. Nine of the fifteen had
  *some* upward drift in the last five games against the prior five, but eight
  of those nine moved by less than 3.3 minutes a night — noise, not a signal,
  and the two biggest movers (Jaquez +3.2, Cameron Johnson +2.6) were ordinary
  rotation players, not breakouts. **The clearest "rising minutes" story a
  filter would have found is Daniss Jenkins — and his minutes went the *wrong*
  way (24.6 against a prior 34.2); he produced the weakest return in the set.**
  A minutes-trend rule would have thrown away most of these men and kept the
  worst one.
- **Nothing in the injury reports.** The listener's `player_status_events`
  carry **no** minutes spike or drop for any of the fifteen in the ten days
  before the claim. Every one of them arrived as a body, not as an
  opportunity created by a teammate's absence in the recorded events.
- **What does mark them is boring and semi-visible: they were already
  rotation players, claimed cheap, and they were kept.** Seven of the fifteen
  were free and five more cost $1–5. The three largest returns (Clingan,
  Rollins, Jaquez) were all claimed by the **same team** in the season's first
  fortnight for $11/$0/$10 and held 74–157 days. That is a manager betting on
  a young big man's role before it arrived — a **thesis about playing time**,
  not a signal in the rows.

- **And the tail of the table is the caveat.** Nine of the fifteen were claimed
  on days 150–160, the season's last eleven days, and **eight of those nine
  were held seven days or less** and cleared the bar on **one or two weeks**
  (§Limitations 6). They are honest by the stated definition and they are not
  evidence of anything: a man who posts one good week in the last week of a
  lost season is a man who was held for a week. **Strike the nine claimed
  after day 150 and 2026 produced six keepers in 1,115 adds, or 0.5%** — and
  the six are the ones with a stay worth the name: Clingan (74d), Rollins
  (157d), Jaquez (125d), Carter Jr. (74d), Melton (1d, from day 72 — the same
  team re-added him on days 77 and 83, so his day-72 stay really was one day
  and the loose reading is what counts his later spells, §Limitations 1), and
  Derrick Jones Jr. (3d but a genuine +4.00 on five of five weeks, in
  November rather than March).

**The one thing here that is actionable** is not a predictor — it is the
absence of one. The men worth the money were invisible to the rank column,
invisible to the minutes trend, and invisible to the injury feed, and what
they had in common was a **playing-time thesis** that the current data does
not encode: "this man's role is about to grow because someone ahead of him is
hurt / has been traded / is on a minutes restriction." The listener stores
injury status and `expected_return_date` and the box-score minutes. It does
not store **depth-chart position**, and that is the variable all three of the
large returns were actually a bet on.

### 5a. Brandon Miller, the claim this study was built to explain

The owner's own claim, worked through in full with `scripts/keepers.py --why
512`. **Through The Wire claimed Brandon Miller on day 23 of 2026 for $2,
while the league's own injury report had
him `Out`** (a left shoulder subluxation, `docs/injuries.md`'s own worked
example). He returned to Charlotte's lineup around day 33, was held without
a gap through the end of the season — **138 days, censored, run_30 = true**
— and by the box score played the way the brief describes: a starter's
role, every week, through the playoffs.

**Why the 30-day test fails him, exactly.** The claim's own transaction (tx
8299) carries an `ADD` item and no `DROP` — Through The Wire had an open
roster place, not a man to cut, so this is also §Limitations 10's case: he
is invisible to every published share in this document except the one built
to find him. Scored anyway, his 30-day window touches five whole periods
(4–8), the wire's replacement on day 23 was **0.20 categories a week** —
near the top of the 0.17–0.21 range every season runs in — and he clears it
in exactly **one of five: period 8 (+1.00)**. Periods 4–7 read `-0.00`
because he had not returned yet or had just returned into a below-replacement
week. `beat_30 = 1`, the bar for a five-period claim is `min(4, 5) = 4`, and
**`deserved_30 = False`.** Four of five dead periods from a stash the
30-day test cannot see coming is exactly the flaw the owner caught.

**Why the hold test does not rescue him either, and that is the honest
finding.** Scored over his full 138-day hold (periods 4–22), one period
(4, the week of the claim itself) has no game and is unavailable; the other
**18 are available**, and he is above the same 0.20 bar in **7** of them
(periods 8, 9, 12, 13, 15, 16, 21 — +1.00, +1.00, +1.50, +0.50, +2.00,
+1.00, +1.00) and at or below it in the other 11, several of them small
losses (period 19: −1.00, period 22: −1.00). `HOLD_MIN_AVAILABLE = 2` is
cleared easily; the "at least half" bar needs `⌈18/2⌉ = 9` and he has 7.
**`deserved_hold = False.`** The hold's total realised value is **+5.00
categories over 138 days** — genuinely positive, and higher on average
(≈0.28/period) than the 0.20 bar — but "above replacement on average" and
"above replacement in most weeks" are different questions, and §2a-hold's
plain-count columns exist precisely so a reader can see both: a man can
carry real positive value and still not read as *consistently* better than
the wire, which is what "deserved to stay" asks.

**Two things this document owes the number, not just the man.** First, the
open-slot claim means the counterfactual `Replay` scores is not "Miller
against the man he replaced" — there was no man — it is "Miller against
Through The Wire's own bench reseated without him," so a deep roster that
could partly cover his box score with its own bench makes his marginal
category-swing smaller than his box score alone would suggest; this is a
real property of the currency (§Limitations 5), not an error. Second,
**category scoring is not points scoring**: 20/5/3.5/3+ threes/a steal a
night is a real NBA season, and it still nets below-replacement weeks in a
nine-category league once turnovers and shooting percentage are in the
ledger — which is exactly why this product scores in categories rather than
a composite, and exactly the gap this document's Limitations 5 warned the
lens can produce. **The reclassified 97 are real evidence the 30-day test
is too strict on a stash. Brandon Miller's own claim is evidence the hold
test is not a rubber stamp** — it was declared before the run and it says
no to the claim that prompted it.

### 5b. The other 2026 stashes

Every 2026 claim made while the league's own report had the man `Out`, or
with no game at all in the claim's own matchup period — the same two
signals Miller's claim carries, found across the whole season rather than
one man: **133 of the 1,115 ranked claims**, 104 with no report at all that
morning (most of these are simply a day off between games, not an
injury — `unreported` is not evidence of health, §Limitations 9), 23
flagged `Out`, 4 `Questionable` and 2 `Available`. 128 of the 133 had zero
games in their own claim period.

**Not one of the 133 clears the 30-day test.** `deserved_30` is `False` on
every single row — the strongest confirmation this document has that "a
stash can never pass the 30-day test" is not a one-man anecdote. Of the same
133, **8 clear the hold test**:

| day | player | team | paid | status | held d | avail/periods | above | hold value |
|---|---|---|---|---|---|---|---|---|
| 6 | Wendell Carter Jr. | Fast and Curryous | $5 | unreported | 21 | 3/4 | 2 | 0.50 |
| 10 | Dillon Brooks | LeBron's Load Management LLC | $0 | unreported | 70 | 10/11 | 5 | 3.50 |
| 14 | Bennedict Mathurin | Through The Wire | $5 | Out | 31 | 3/5 | 2 | 1.50 |
| 27 | Andre Drummond | Fast and Curryous | $1 | unreported | 13 | 2/3 | 1 | 1.00 |
| 123 | Moussa Diabate | Through The Wire | $0 | unreported | 13 | 2/3 | 2 | 2.00 |
| 129 | Jayson Tatum | Optimize the MVPs | $0 | unreported | 32 | 4/5 | 2 | 1.00 |
| 146 | John Collins | Brighton Bears | $7 | unreported | 15 | 2/3 | 1 | 0.50 |
| 146 | Herbert Jones | Through The Wire | $0 | unreported | 15 | 2/3 | 1 | 2.50 |

Jayson Tatum belongs on this list for the reason that makes the point
sharpest: a star coming back from an Achilles tear, claimed off the wire
for $0 with no games yet in his return period, invisible to the 30-day
test by exactly the same construction as Miller — and the hold test, over
his actual 32-day stay, correctly reads him as kept for a reason (4 of 5
available periods above replacement). The other 125 stash claims are mostly
what a stash usually is: **95 of the 133 returned exactly zero hold value**
and only 23 returned positive value at all, so the hold test rescuing 8 of
133 (6.0%) rather than a much larger share says the test is not simply
crediting every stash — it is crediting the ones that, once available, were
actually good, which is the whole design (§2a-hold). The full 133-row table
is `scripts/keepers.py`'s own `== 5b. ==` output and is not re-typed in
full here.

---

## What this means for the product

**1. The page should say it out loud, and it should say it with these numbers.**
The rest-of-season report currently names moves that clear the 0.10/0.20 bar.
Measured against outcomes, a move clearing the paid bar was held past thirty
days **33.0%** of the time and deserved to stay **8.8%** of the time — **six
point eight times the 1.3% season baseline**, which is a good bar and the
strongest endorsement of the recommender anywhere in this study. The honest
sentence is: *"most pickups are streams — 69% of every add in this league's
history was cut inside a week, and half the time the man you cut returns more
over thirty days than the man you claimed. A move worth keeping looks like a
clear paid-bar net and a player whose role is growing; everything else is a
three-day body."* The page already refuses to say "recommended"
(`docs/pickups.md` §5.2, Patrick's rule); this gives it something true to say
instead.

**2. Yes, there is a retention number the bar should carry.** The
rest-of-season report should show, beside every named swap, **the share of
claims like it that were still on the roster 30 days later** — read from this
table, not recomputed live: **33.0%** for a claim clearing the paid bar,
**12.9%** for one clearing the free bar, **4.8%** for an unconditional 2026
free add. That is the single most legible number for a manager deciding
whether to spend FAAB, and unlike "Δ expected wins" it is **measured from this
league's own transactions** rather than projected. Suggested wording on the
page: *"of 91 claims in 2026 that cleared this bar, 30 were still held a month
later."*

**3. What a 16-team wire does to `OPENED_PLACE` and `TYPICAL_PICKUP` —
proposed, not changed.** Both constants live in `app/scoring/replacement.py`
and **neither should move on the strength of this study alone**:

- **`TYPICAL_PICKUP` (0.06)** should **not** be adjusted for 2027 on the
  evidence here. The lens's replacement value of the best wire man falls only
  0.20 → 0.17 from ten to sixteen teams (§4), and the realised median add has
  already gone to zero and stays there regardless of size. Sixteen teams
  changes which men are free; it does not change what a free man is worth,
  because at 0.06 there is nothing left to take.
- **`OPENED_PLACE` (0.38)** is the one I would revisit, and *after* 2027
  produces a season at sixteen teams, not before. It is `docs/streaming_lane.md`'s
  per-place median measured on a wire that has been 10-to-14 teams deep. On a
  thinner wire the same lane is streaming worse men into the same slots, so
  0.38 is, if anything, **high**. But the streaming_lane doc already records
  that an opened place can never be priced above an ordinary held place
  (0.43), so the room to move it is only 0.38 → ~0.40 upward and 0.38 → ~0.30
  downward, and this study has no measurement of a sixteen-team lane. **The
  proposal is a measurement, not a constant change: re-run
  `scripts/streaming_lane.py` after 2027 and move `OPENED_PLACE` only if the
  per-place median moves by more than the doc's own spread.**
- **`app.scoring.moves`' replacement charge and the `opened_places` rule**
  read the same constants and should be left alone for the same reason.

**4. Nothing is wired by this document.** No constant moved, no route changed,
no page edited. It is a measurement, and the three proposals above are for
Patrick to accept or refuse.

---

## Decisions

Every judgement call this study made, so a reviewer can attack it.

1. **The 30-day headline is RUN-BASED and continuous.** An add counts as "still
   on the roster 30 days later" only if the adding team held him without a gap
   from the claim day through day+30, so a man dropped and re-added inside
   those thirty days does **not** count (Claude's answer, 2026-09-23). The
   loose reading is printed in §1b beside it and §Limitations 1 explains the
   difference rather than leaving a reader to guess.
2. **The loose reading is reported, never headlined.** Its pooled median is
   8.0 days against 4.0 and its 2026 >30 share is 24.6% against 8.6%. The
   65.7% an earlier pass produced is a *third* reading (cumulative held days
   over the whole season, gaps and other teams' spells included) and is
   reproduced in §Limitations 1 with its census so nobody re-derives it and
   thinks it is the headline.
3. **Censoring is handled by printing both denominators.** The headline uses
   the **full attributed population of 1,145**, with the reachable-only figure
   (**11.0%** over 903 adds) and the censored-as-held figure (**8.6%**, the
   same to two decimals) printed beside it in §1c, and every season's
   like-for-like comparison given as "≥30d run, of reachable" in §Limitations 2.
   Censoring is *not* what makes 2026's retention look low — the reachable
   figure is higher than the headline.
4. **The value currency is `pickup_values`' lens, via the backtest's
   `Replay`, not a new counterfactual engine.** `Replay.delta` is called with
   the claim undone and the sign flipped — the same frame
   `pickups_backtest.league_baseline` uses — so a real claim is scored in the
   identical quantity the recommender's moves are. `docs/pickups_backtest.md`'s
   machinery is reused, not rewritten.
5. **"Above replacement" is measured against the wire's best man on the claim
   day**, by the same lens, floored at `TYPICAL_PICKUP`. "Deserved" = four of
   the next eight whole weeks. A claim with fewer than eight whole weeks left
   is judged on the weeks it has, and §Limitations 6 says this makes a very
   late claim easier to call deserved; §5 names the nine 2026 claims affected
   and reports the six that survive excluding them.
6. **The top-100 reading is reported but labelled a trap**, with the two
   reasons (top 100 of ~350 rostered men is not "worth a place"; no
   counterfactual) written into §2b rather than buried.
7. **Rest-of-season rank uses `player_season_stats` season totals scaled to a
   week through `marginal`** — the product's lens, not raw points — so "top
   100" means the top hundred by what this product values.
8. **The recommender-bar table (§3d) is a backward re-reading, not a replay**,
   because replaying the 2026 recommender is an 85-to-110-minute job that
   belongs to `scripts/pickups_backtest.py`. For 2019–2025 the cut is reported
   as impossible and not approximated.
9. **The FAAB-bucket cut is 2026-only** and the $21+ bucket (n=2) is published
   as a row rather than suppressed, with its n beside it.
10. **Month is read from `player_game_stats.game_date`**, because a scoring
    period is not a calendar date; ten 2026 periods have no boxes and fall
    back to the neighbouring period, and one 2023 period could not be placed
    at all and appears as `2023 p1`.
11. **Five unattributed 2026 adds are named in §Limitations 3 with their
    transactions**, rather than dropped silently. All five are same-day adds
    to a man already rostered or adds with no lineup rows anywhere; the
    exclusion rate is 0.43%.
12. **The lens is memoized per period length**, which is why the eight-season
    census runs in 472s instead of hours. `standard_lens` rebuilds the
    season's `category_distributions` and average team line on every call and
    only reads them, so caching them per length cannot change an answer — the
    callers pass the same `distributions` object in.
13. **A claim with no `DROP` item on the same transaction is scored with
    `dropped_value = None`** and excluded from §2d's denominator (6,524 of
    6,646 attributed adds have a single dropped man). Two-drop or no-drop
    transactions are not reconstructed, because the brief's question is about
    the one-for-one move.
14. **Nothing was wired.** No constant in `app/` changed, no route, no page.
    The three proposals in "What this means for the product" are proposals.
15. **Read-only throughout.** Every query is a SELECT against the worktree's
    own `DATABASE_URL`. No ingest, no migration, no write.
16. **All eight seasons were run in full — no sampling.** The brief allowed
    running 2026 in full and the rest at a stated sample if the census were too
    slow. With the lens memoized per period length it is not: the whole
    2019–2026 census, 6,779 adds and 6,524 scored claims, completed in **472
    seconds** on first publish and **496 seconds** once the hold definition
    was scored alongside it (§Limitations 9) — the same `Replay`, so the
    extra cost is one more `delta` call a period rather than a second pass.
    Every number in this document is the full run, and there is no sample
    limitation to declare.
17. **The success-check sentence's "N" is 1,145** — the census's attributed
    2026 population — and §Limitations 3 names the one-add difference a
    lineup-only replica can produce (1,146) with its cause and its size
    (8.65% against 8.64%). A reviewer recomputing X from the tables should get
    8.6% either way; D is 3 days on both.
18. **The hold definition's constants were declared in `scripts/keepers.py`
    (`HOLD_MIN_AVAILABLE = 2`, "at least half" of available periods, rounded
    up) before this revision's eight-season run, and were not changed
    afterward** — including after they turned out not to reclassify Brandon
    Miller's own claim (§5a). Declaring, then running once, is the whole
    point of a constant a reviewer can attack: it would have been easy to
    lower the bar until Miller passed, and that was not done.
19. **Availability, for the hold test, is "played at least one game", not
    the injury reports.** `app/injuries.py`'s point-in-time status is the
    more precise signal but only covers 2022–2026 (docs/injuries.md); a test
    that must run the same way on all eight seasons cannot use a signal five
    of them do not have. The injury reports are still read, but only as
    context for `--why` and §5b, never as part of either "deserved" test
    (§Limitations 9).
20. **`--why PLAYER_ID` carves that one player's claims out of the "single
    dropped man" population so they are scored even with no matched drop**
    (`scripts/keepers.py`'s section-2 loop). This exists because Brandon
    Miller's own claim has no `DROP` item (§Limitations 10) and would
    otherwise be scored nowhere in this document; no other no-drop claim is
    added back into `ranked`, and every published share in §§1–5 is
    unchanged by this carve-out.
21. **The success-check sentence now also states the hold figure (9.3%,
    104 of 1,115) and its own previous "4.1%" is corrected to 1.3%** — that
    was always the `≥60d` column of §1's retention table, mis-copied into
    the sentence about §2a's four-of-eight test, and is fixed here rather
    than carried forward silently because the gate this document's revision
    is held to is exactly "can a reviewer recompute this sentence."
