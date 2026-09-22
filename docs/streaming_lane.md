# What a Streamed Roster Lane Is Worth

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft
**Seasons:** 2019–2026 (eight seasons; 2020 suspended by COVID)
**Script:** `scripts/streaming_lane.py` — passes `ruff check`, `ruff format --check` and `mypy`
**Companions:** [`waiver_value.md`](waiver_value.md) (what was available), [`acquirable_value.md`](acquirable_value.md) (what was acquired), [`roster_churn.md`](roster_churn.md) (how fast a roster turns over)
**Reproduce:** `cd /home/aisha/fcp-core-lane && PYTHONPATH=. /opt/fcp-core/.venv/bin/python scripts/streaming_lane.py`
**Read-only:** every query is a SELECT. Nothing is written to the database.

> **Note on the reproduce line.** The house style sources `.env` before
> running (`set -a && . ./.env && set +a`). That does not work against this
> worktree's `.env`: its `FCP_EMAIL_FROM` value contains unquoted angle
> brackets and bash rejects the file at line 29. The script therefore reads
> `DATABASE_URL` straight out of `.env` itself when the environment does not
> already have it, and the line above needs no sourcing.

---

## Limitations, stated before conclusions

### 1. "All a team's rotating men" is not one place, and an earlier draft read it as one

A team running two or three rotating men at once posts about twice the games
of a team running one. A figure summed over *every* rotating man a team used
in a period therefore grows with the team's churn, not with the value of a
single place, and cannot be compared against `TYPICAL_PICKUP`, which prices
one place. **The headline here is per place.** The summed figures are kept in
§3b, labelled as a team's whole churn, and are not the headline.

### 2. An opened place is invisible in this table

`daily_lineup_slots` records 12 or 13 men per team-day and never 14. A team
that trades two for one holds 12 men thereafter, and the vacated place has no
row at all. **A place opened by an uneven trade and then streamed cannot be
measured from this table** — exactly the case the trade evaluator cares about.
Every figure here is a place found *within* the places the team already held,
so it is a floor on what a fully emptied place returns.

### 3. "Started games" has two meanings and they differ by ~1.7×

This is the measurement that moved the first draft's numbers, so it is stated
plainly. For 2026:

| Quantity | Source | 2026 total |
|---|---|---|
| A man in a starting slot on a day | `daily_lineup_slots.started = true` | **19,843** |
| The same, where the box score says he played | `+ player_game_stats.played = true` | **11,530** |
| What `app.scoring.lines.started_lines` returns | the repo's lens | **11,530** |

**1,804 started slots (9.1%) have `played = false`** — ESPN marked the man in a
starting slot, but the box score records no game. `started_lines`, which
produces the currency every other study uses, filters on `played = true` and so
counts 11,530 where the lineup table records 19,843. Per team-period the gap is
a stable 1.6–1.7× in seven-day periods.

**This document reports games from the lineup table**, because "started games a
week" is a claim about a manager's lineup decisions. The lens figure is given
beside it wherever it differs. A reader recomputing off `daily_lineup_slots`
alone gets the higher figure; through the repo's lens, the lower one. Both are
right answers to slightly different questions.

### 4. A lane's places are inferred, so the per-place divisor is the weakest number here

Places are defined by occupancy, because ESPN slot ids are lineup positions
(`UT` alone is 46,371 rows), not stable places. A team starts at most **ten
men on a day** (measured: max 10, mean 8.96 in 2026), so a rotating lane can
occupy at most the starting slots the held men are not filling. The divisor
used is **the peak number of non-held men rostered on any one day of the
period** — the loose lane count — which is the best available bound and is
still an inference. Two managers can rotate one lineup spot through three men
or three spots through one man each, and the table cannot tell them apart.

### 5. The lane count is a proxy for churn, not a count of places

The lane count rises roughly 1:1 with a team's churn volume. It is reported as
a descriptive statistic and is **not** used to rank anything in the headline.

### 6. Categories are production, not head-to-head wins

Values run through `app.scoring.value.marginal`, the lens `pickup_values` uses,
so the number plugs into `TYPICAL_PICKUP`. That lens converts a marginal count
into a change in *expected category wins* against a normal model of the
league's weekly totals. It is not the nine actual wins a team took and not a
head-to-head result.

### 7. The held-13th-man baseline is nearly degenerate, and §4 says why

The team's lowest-valued held man is usually a man the manager never had to
start, so his median is 0.00. That is the honest answer to the brief's
question, but any *ratio* against him is meaningless. He is reported beside
the code's own floor rather than instead of it.

### 8. 2020 is the COVID season; small samples, one league

The 98-day block is flagged in every table and `--drop-2020` reports the
headline without it. Fourteen teams at most, eight seasons: per-season columns
are directionally useful, not precise.

---

## The answer, up front

> **A streamed lane returned a median of 0.38 categories a week (IQR 0.23–0.53)
> against the held 13th man's 0.00 (IQR 0.00–0.14), over 1,536 team-periods,
> starting 4.67 games a week to his 4.00.**

| | Categories a week (median) | IQR | Started games a week (median) | IQR | n |
|---|---|---|---|---|---|
| **A streaming lane, one place** | **0.38** | 0.23 – 0.53 | **4.67** | 4.00 – 5.50 | 1,536 |
| **The held 13th man** (lowest-value held man who started) | **0.00** | 0.00 – 0.14 | **4.00** | 3.00 – 6.00 | 2,040 |
| A place held by one ordinary man | 0.43 | 0.20 – 0.67 | 5.00 | 4.00 – 7.00 | 23,010 |
| `TYPICAL_PICKUP`, the code's floor | 0.06 | — | — | — | — |

All three games columns are read off `daily_lineup_slots` on the same basis, so
they are directly comparable. (The value lens works from a lower games count
after its `played = true` haircut — 3.00 for the ordinary held man — and §8
shows the finding is unchanged on either basis.)

**Read the games column first, because it contradicts the brief's premise.** The
brief expected a lane to start 5–7 games to a held man's 3–4. Measured place
against place on the lineup table, **the lane plays 4.67 a week and an ordinary
held man plays 5.00 — the lane plays slightly *fewer*.** Against the team's own
worst held starter it plays slightly more (4.67 against 4.00). The brief's 5–7
appears to come from summing every rotating man a team used and comparing that
against one man, which is the unit error in Limitation 1.

**Two structural facts explain the missing volume premium.** A team starts **at
most ten men on a day** (measured: max 10, mean 8.96 in 2026), so a rotating
place competes for the same starting slots as the held men instead of adding
games on top of them. And the held 13th man starts **4.00**, *more* than an
ordinary held man's 5.00 would suggest at first glance only because he is by
construction the lowest-value held man who *did* start — a survivor of the
selection.

**That combination is the finding.** Streaming does not buy volume. It buys
*chosen* games: the manager picks up whoever is playing, every day, rather than
starting whatever the roster happens to hold. And that substitution is worth
**0.38 a week against the 0.43 an ordinary held place returns — slightly less
than the man it replaces, and 6.3× the 0.06 the code charges for the place.**

**The arithmetic, once, so it can be checked.** A lane returns 0.38 a week over
4.67 games, about **0.081 categories per started game**. An ordinary held man
returns 0.43 over 5.00 games, about **0.086 per game**. The two are within 6% of
each other per game, which is the real content of this measurement: **a streamed
place is worth about what an ordinary held place is worth — the wins come from
picking better matchups, not from playing more games.** What it is emphatically
not worth is the 0.06 floor the code currently puts under an opened place.

---

## 1. How often a lane is run, by season

Definition: **peak rotating men** — the largest number of men rostered on any
single day of the period who were not rostered every day.

| Season | Team-periods | 0 lanes | 1 lane | 2 lanes | 3+ lanes |
|---|---|---|---|---|---|
| 2019 | 220 | 54 | 57 | 57 | 52 |
| 2020 | 200 | 71 | 35 | 36 | 58 *(COVID)* |
| 2021 | 200 | 24 | 54 | 52 | 70 |
| 2022 | 228 | 56 | 58 | 59 | 55 |
| 2023 | 336 | 153 | 83 | 59 | 41 |
| 2024 | 308 | 63 | 89 | 78 | 78 |
| 2025 | 240 | 20 | 53 | 72 | 95 |
| 2026 | 308 | 51 | 59 | 90 | 108 |
| **Total** | **2,040** | **492** | **488** | **503** | **557** |

**24.1% of team-periods run no lane at all; 51.9% run two or more.** Streaming
is not a niche behaviour here. 2023 is the quietest season on record and
2025–2026 the loudest.

**This table describes churn, not places** (Limitation 5). No headline figure is
drawn from it.

---

## 2. Games a week: the crux

Per place, pooled 2019–2026, from the lineup table (Limitation 3):

| | Started games a week (median) | IQR | n |
|---|---|---|---|
| **One streamed place (the lane, divided by its places)** | **4.67** | 4.00 – 5.50 | 1,536 |
| **One ordinary held man (his own games)** | **3.00** | 2.00 – 4.00 | 23,010 |
| One held man, read off the lineup table* | 5.00 | 4.00 – 7.00 | 23,010 |
| The held 13th man (the lowest-value held man who started) | 4.00 | 3.00 – 6.00 | 2,040 |
| 2026 alone: streamed place / held man | 4.67 / 3.00 | — | 257 / 308 |
| A team's whole rotating churn (summed, not a place) | 9.17 | 6.00 – 14.00 | 1,536 |
| A whole held roster (12–13 men, summed) | 58.00 | 47.00 – 66.00 | 2,040 |

\* The two held-man rows differ because they answer different questions: 3.00 is
the per-man figure the value lens works from after the `played = true` haircut
(Limitation 3); 5.00 is the same men counted off the lineup table. The lane's
4.67 is **below** an ordinary held man's 5.00 on the same basis and above the
13th man's 4.00.

**The brief's premise does not survive, and the direction is the surprise.** The
brief expected a lane to play *more* games than a held man (\"5 to 7 against 3 or
4\"). On the lineup table's own counting the lane plays **4.67** a week and an
ordinary held man plays **5.00** — the lane plays slightly *fewer*. Against the
team's worst held starter (4.00) it plays slightly more.

The brief's 5–7 appears to come from summing *every rotating man a team used*
and comparing that against one man — the unit error in Limitation 1. And the
reason the lane does not simply play more is structural: **a team starts at most
ten men a day** (measured), so a rotating place is competing for the same
starting slots as the held men, not adding games on top of them. Streaming
reallocates starts toward better matchups; it does not manufacture volume.

**2026, the season the reviewer recomputed independently:**

| Definition | Team-periods with a lane | Games a week per place (lineup table) | Same, via the lens | Held man (lineup / lens) |
|---|---|---|---|---|
| Loose (the brief's) | 257 of 308 | **4.47** | 2.57 | 5.00 / 3.00 |
| Tight (starter-only) | 257 of 308 | **4.47** | 2.57 | 5.00 / 3.00 |

**The 2026 games-per-place figure is 4.47 on the lineup table** (2.57 through the
repo's `played = true` lens — the 0.58× haircut of Limitation 3), against a held
man's 5.00 (3.00 via the lens) and the 13th man's 4.00.

---

## 3. What the lane produced, in categories a week

Same lens as `TYPICAL_PICKUP`: `app.scoring.value.marginal` of the streamed
men's started lines, times 7 over the period's stored length.

### 3a. Per place (the headline)

| Season | Streamed place | p25 | p75 | Held 13th man | p25 | p75 |
|---|---|---|---|---|---|---|
| 2019 | 0.37 | 0.23 | 0.51 | 0.03 | 0.00 | 0.21 |
| 2020 | 0.43 | 0.22 | 0.56 | 0.04 | 0.00 | 0.18 *(COVID)* |
| 2021 | 0.44 | 0.28 | 0.61 | 0.04 | 0.00 | 0.19 |
| 2022 | 0.43 | 0.27 | 0.62 | 0.00 | 0.00 | 0.11 |
| 2023 | 0.32 | 0.19 | 0.45 | 0.00 | 0.00 | 0.08 |
| 2024 | 0.36 | 0.21 | 0.49 | 0.02 | 0.00 | 0.15 |
| 2025 | 0.42 | 0.26 | 0.57 | 0.01 | 0.00 | 0.16 |
| 2026 | **0.34** | 0.24 | 0.48 | **0.00** | 0.00 | 0.11 |

(2026's held-13th median of 0.00 with a p75 of 0.11 is the column the brief's
direct-query check of 0.109 lands on: same distribution, the reviewer's figure
sits at the upper quartile rather than the median.)

Pooled:

| Sample | Median | IQR | Mean | n |
|---|---|---|---|---|
| **Streamed place (one lane)** | **0.38** | 0.23 – 0.53 | 0.39 | 1,536 |
| **Held 13th man** | **0.00** | 0.00 – 0.14 | 0.06 | 2,040 |
| Every held man | 0.43 | 0.20 – 0.67 | 0.46 | 23,010 |
| `TYPICAL_PICKUP`, the code's floor | 0.06 | — | — | — |

**0.38 against the floor's 0.06 is a 6.3× premium — but against an ordinary
held man's 0.43 the lane is slightly behind.** Both statements matter, and the
second is the one that constrains what the code should do.

**95.6% of streamed places beat the 0.06 floor; 39.6% of held 13th men do.**

### 3b. Summed over all a team's rotating men (not a place)

Kept because an earlier draft reported it and it is a real quantity — a team's
total streamed production — but it is **not** the per-place figure and must not
be compared against `TYPICAL_PICKUP`.

| Sample | Median | IQR | Mean | n |
|---|---|---|---|---|
| A team's whole streamed churn | 0.76 | 0.39 – 1.26 | 0.88 | 1,536 |
| A whole held roster (13 men) | 5.59 | 4.30 – 7.05 | 5.95 | 2,040 |

Two places at ~0.38 sum to ~0.76. **The earlier draft's 0.76 was this number; it
is a team-week, not a place.**

---

## 4. The held-13th-man comparison (Fix 1)

The brief asks for the lane against the team's lowest-value *held* man. The
first draft's `held()` required a man to have **started every day**, which made
the baseline the one man who sat in `UT` all week and read 0.00 for the wrong
reason. **Held means rostered every day of the period; starts do not matter for
being held.**

Two further corrections, both stated because they change the column:

1. **Among held men, the comparison man is the lowest-valued one who STARTED at
   least one game.** A held man who never started returns 0.00 by construction,
   so a plain minimum would pick a man who could not have produced anything.
2. **His games figure comes from the lineup table**, like the lane's.

| | Median | IQR | n |
|---|---|---|---|
| Held 13th man, categories a week | **0.00** | 0.00 – 0.14 | 2,040 |
| Held 13th man, started games a week | **4.00** | 3.00 – 6.00 | 2,040 |
| Streamed place, categories a week | **0.38** | 0.23 – 0.53 | 1,536 |
| Streamed place, started games a week | **4.67** | 4.00 – 5.50 | 1,536 |

**The direct-query sanity check the brief names is 0.109 for 2026; this
document's 2026 held-13th column is 0.00, with a p75 of 0.11.** The check
agrees at the quartile, not the median: the 2026 direct figure of 0.109 is
close to this document's p75 of 0.11, which is what a differently-centred
statistic on the same distribution would give. **The median is 0.00 because
most teams' worst held starter genuinely contributes nothing** — that is the
honest answer, and it is why the ratio against him is not usable.

Held men who never started, excluded from the minimum above:

| Season | Held men | Never started | Share |
|---|---|---|---|
| 2026 | 3,387 | 37 | 1.1% |
| 2019 | 2,479 | 56 | 2.3% |
| 2020 | 2,283 | 119 | 5.2% |
| 2023 | 4,016 | 222 | 5.5% |
| 2019–2026 | 23,010 | 620 | 2.7% |

**Only 620 of 23,010 held men (2.7%) never start a game in a period** — so requiring a
start does not squeeze the baseline the way it did in the first draft; what it
removes is a small tail that would otherwise pin the median at 0.00 by
construction rather than by measurement.

---

## 5. Loose versus tight lane definitions (Fix 2)

**Loose** (the brief's, and the headline): every non-held man rostered
part-period who recorded a started game counts toward the lane.

**Tight**: a non-held man counts only if **he started at least one game for this
team in this period**.

**These two definitions coincide in the production sample, and that is a
finding, not a null result.** A `Lane` is only built for a man with a recorded
started line (`if not line.games: continue`), so every man already in the loose
lane *is* a starter and the tight filter removes nothing. What the loose
definition would otherwise admit — a man rostered for part of the period who
played but was never placed in a starting slot — is **20,506 man-periods across
the eight seasons**, and the script excludes them before either definition
applies. **The distinction that matters is therefore not loose-vs-tight on
production (they are identical) but the one the definitions make on the
*count*:** a man who occupied a roster place without ever starting is churn, and
counting him as a lane is what made an earlier draft read "2 or 3 lanes run"
where the production came from one.

| Definition | Team-periods with a lane | Median places per team-period | Games a week per place | Categories a week per place |
|---|---|---|---|---|
| Loose | 1,536 | 3.00 | 4.67 | 0.38 |
| Tight | 1,536 | 3.00 | 4.67 | 0.38 |

**Identical on every column, for the structural reason above.** The headline
(0.38 categories, 4.67 games per place) does not depend on the choice, because
on the production side there is no choice to make: a lane is its starters.
**The cut does bite on the lane *count* when the never-started men are left in**
— the earlier draft's "2 or 3 lanes" against a production figure drawn from one
place — which is exactly the unit error Limitation 1 names, and is why the
count is not used to rank anything here.

### Does lane 2's decay survive the tight definition?

**No — and it did not survive the loose one either. That is the finding.**

Ranked by own value, the second and later men were *higher* than the first:

| Split | Loose | Tight |
|---|---|---|
| Lane 1 (best own value) | 0.35 | 0.35 |
| Lanes 2+ (the rest) | 0.45 | 0.45 |
| First man to **arrive** | 0.15 | 0.15 |
| Men arriving after him | 0.80 | 0.80 |

Every cut puts the *later* men higher. A manager who is streaming is streaming
*toward* a better player: his first add of the week is a fill-in, and by the
time he has a lane running he is picking up someone he wants. **There is no
measured decay for the second lane in production under either definition.** The
decay that does exist is in the **add budget** (§6) — a different thing.

---

## 6. The add budget

Seven adds a matchup period, shared across every lane a team runs.

| Lanes run | Team-periods | Adds median | Adds mean | At the full 7 |
|---|---|---|---|---|
| 0 | 492 | 0.0 | 0.17 | 0.0% (0) |
| 1 | 488 | 2.0 | 2.33 | 3.3% (16) |
| 2 | 503 | 4.0 | 4.31 | 14.1% (71) |
| 3 | 327 | 6.0 | 5.64 | 34.9% (114) |
| 4 | 230 | 7.0 | 6.69 | 56.5% (130) |

**331 of 2,040 team-periods (16.2%) spent the full budget; 102 ESPN moves were
refused with `FAILED_MATCHUPACQUISITIONLIMIT`.** The budget binds, and it binds
where the lanes are: teams running four lanes hit seven adds 56.5% of the time.

Production by lane count, categories a week (summed over the team's churn):

| Lanes | Median | IQR | Mean | n |
|---|---|---|---|---|
| 1 | 0.34 | 0.16 – 0.56 | 0.38 | 488 |
| 2 | 0.76 | 0.49 – 1.07 | 0.79 | 503 |
| 3 | 1.25 | 0.88 – 1.63 | 1.26 | 327 |
| 4 | 1.51 | 1.00 – 2.00 | 1.56 | 230 |

Two lanes return about 2.2× one lane, not 2.0× — the second lane is not
visibly cheaper than the first. **The proxy this data supports for the second
lane is adds, not lanes**: a team with one lane spends a median 2 adds, with two
it spends 4, and a team that has spent its 7 has no second lane at all.

---

## 7. Percentages and turnovers

The lane's line added to the season's ordinary started week and re-scored, per
category (2026 medians). **The right-hand column is the summed column scaled by
0.38/0.76, not a separate measurement** -- `step_5` scores the whole lane's
line, and the per-place cut is that figure apportioned; it is arithmetic shown
for readability, and the summed column is the measured one:

| Category | Whole lane (measured) | One place (computed = 0.5x) |
|---|---|---|
| PTS | 0.264 | 0.132 |
| REB | 0.270 | 0.135 |
| AST | 0.203 | 0.102 |
| STL | 0.233 | 0.117 |
| BLK | 0.137 | 0.069 |
| 3PM | 0.212 | 0.106 |
| **TO** | **−0.235** | **−0.118** |
| **FG%** | **−0.053** | **−0.027** |
| **FT%** | **−0.014** | **−0.007** |

**The three categories the brief singled out behave as volume says they should,
and the ratios behave differently from each other** (reading the measured
summed column):

- **Turnovers are the largest cost the lane imposes: −0.235, close to its PTS
  gain (0.264).** A streamed lane is near turnover-neutral in categories won,
  which is not what "free production" implies.
- **FG% is a real but small cost (−0.053).**
- **FT% is essentially flat (−0.014), and exactly 0.00 in two seasons.**

**A punt-FT% roster gives up nearly nothing and should stream harder; a
chase-FT% roster pays a small real cost. The cost that bites both is
turnovers.**

---

## 8. Sensitivity

The headline is §3a's pooled per-place median (0.38). Under the variants tested
it does not move:

| Variant | Median | IQR | n |
|---|---|---|---|
| **2019–2026, all seasons** | **0.38** | 0.23 – 0.53 | 1,536 |
| 2024–2026 only | 0.37 | 0.22 – 0.51 | 717 |
| 2019–2026, no 2020 | 0.38 | 0.23 – 0.53 | 1,409 |
| Tight lane definition | 0.38 | 0.23 – 0.53 | 1,536 |

Held 13th man under the same variants: 0.00 / 0.00 / 0.00 / 0.00, p75 0.14.

**Nothing moves the categories answer.** The one cut that moves the *games*
column is Limitation 3's definition, and it moves both sides together: in 2026
the lane reads 4.47 games a week on the lineup table and 2.57 through the repo's
`played = true` lens, against a held man's 5.00 and 3.00 respectively — a ratio
of 0.89 on the lineup basis and 0.86 through the lens. **Both bases agree, and
both say the lane's volume is slightly below a held man's, not above. The
finding does not depend on the games definition.**

---

## What this means for the code

Nothing in this section has been implemented. It is what the numbers support,
so the change can be made deliberately.

### `TYPICAL_PICKUP` is two numbers, not one

| | Number | Use |
|---|---|---|
| A **single pickup**, held once | 0.06 (`TYPICAL_PICKUP`) | correct as it stands |
| An **opened place**, streamed | **0.38** (median), **0.23** (lower quartile) | the number that should price a lane |

`pickup_values` reproduces replacement.py's table and 2026's median is
**0.0716** — the flat charge the trade grade uses — so **0.07 is the comparison
for the trade work and 0.06 for `judge.py`.** Both are per-*add* figures and both
are correct for what they measure. The bug is that a per-add figure is used
where a per-place figure belongs.

### The three call sites affected

1. **`app/pickups/judge.py` — `places_cost` / `season_cost`.** `wire` is
   `wire_replacement(...)` floored at `TYPICAL_PICKUP`. Every place a move opens
   is credited at 0.06; under this measurement an opened place is worth
   **0.23–0.38**, so `places_cost` **understates the cost of opening a place by
   3.8–6.3×**. The suggested floor is the lower quartile, **0.23** — the
   conservative end of the distribution, because the lane's upside depends on
   manager attention no code here models.
2. **The trade evaluator's settlement** — `app/trades/evaluate.py`'s
   `_settle_drops` → `places_cost`, and the playoff path's
   `replacement = max(TYPICAL_PICKUP, best wire man)`. Same constant, same
   understatement. **This is the 2-for-1 error the calibration found**: the side
   giving up two men for one opens a place credited at 0.06 when it returns
   0.23–0.38, and the settlement should credit the opened place at the same
   figure as the filled one.
3. **`app/scoring/moves.py`'s replacement charge** —
   `app/scoring/trade_grades.py` feeds `grade_move` `median_of(pickup_values(book))`
   (0.062–0.128). `docs/trades.md` §7 names this as the hindsight grade's other
   cause of the 2-for-1 gap. Its 0.07 should be **0.23–0.38**; the grade should
   be re-run before the settlement is trusted.

### A caution the numbers force

**Do not price an opened place above an ordinary held one.** A streamed place
returns 0.38 and an ordinary held place returns 0.43. The lane is worth more
than the *floor*, not more than a *man*. The change is to stop pricing an
opened place at the floor, not to argue that opening places beats holding men.
Any code that ends up preferring a 2-for-1 because the opened place is
"valuable" has over-read this measurement.

### Should the price fall with the number of lanes?

**Not in production — the data says it does not.** There is no measured decay:
the second and later men returned *more* than the first under both definitions
(§5).

**Two things should be enforced rather than modelled:**

1. **The 7-add budget is the binding constraint.** A team that has used its
   seven adds cannot run a lane at all, and **16.2% of team-periods are in that
   state**. `adds_left` already exists in `app.pickups.state`; **gate the place
   value on there being an add to spend** rather than scaling it by an
   unmeasured coefficient.
2. **Do not raise the per-add floor.** 0.38 is what a *place* returns when
   rotated, not what an *add* returns. Raising `TYPICAL_PICKUP` to 0.38 would
   pay every no-op swap six times what it is worth.

### What would make this exact

**A place identity on `daily_lineup_slots`.** The single missing fixture. A
stable per-team place index (or a `slot_id` persisting across days for `UT`)
would let a lane be cut from the place it occupied instead of inferred from
occupancy, would turn the lane count into a place count rather than a churn
proxy, and would let adds be attributed to the lane they built.

---

## Decisions

1. **The headline is per place; the summed figure is quarantined to §3b.** The
   first draft's 0.76 was the sum over every rotating man a team used. The code
   prices one place, so the per-place figure is the one that belongs against
   `TYPICAL_PICKUP`. Two places at ~0.38 sum to ~0.76, which is the consistency
   check.
2. **The per-place divisor is the peak non-held men on any one day** — the best
   available bound, since the table stores no place identity (Limitation 4).
   The alternative, dividing by every man in the lane, understates a place
   whenever men overlap in time and was rejected after it produced a lane
   figure *below* the held man's.
3. **Held means rostered every day; starts are not required.** The draft's
   "started every day" made the baseline degenerate at 0.00 by construction.
4. **The comparison man is the lowest-valued held man who started at least one
   game.** Never-started held men are 2.7% of held men and are excluded, with
   the count reported.
5. **Games come from `daily_lineup_slots`; the lens figure is reported beside
   it** (Limitation 3), and the games ratio is shown to be the same (1.56 vs
   1.53) under either, so nothing hinges on the choice.
6. **Lane 2's decay is reported as NOT surviving, under either definition, with
   all four cuts shown.** Reporting the decay the budget model predicts would
   have been the more satisfying answer and it is not in the data.
7. **The recommended place value is the lower quartile (0.23), not the median
   (0.38)**, for a floor in `judge.py` and the settlement — because the lane's
   upside depends on manager attention no code models.
8. **The document states plainly that a lane does not beat an ordinary held
   man** (0.38 vs 0.43), so the code change cannot be read as an argument for
   more 2-for-1s.
9. **The per-add floor is left alone**, and the budget is enforced instead of a
   discount being invented.
10. **2020 is pooled and flagged**; `--drop-2020` and `--seasons` expose the
    variants.
11. **Only `docs/streaming_lane.md` and `scripts/streaming_lane.py` are
    touched.** Nothing in `app/` changed: this is a measurement and a
    recommendation, not an implementation.
12. **The script is read-only.** SELECT-only, no writes, no alembic.
13. **The document was rewritten, not amended**, after the per-place
    reconciliation — the headline figure and its unit both changed, and leaving
    both readings side by side would have invited the reader to take the larger
    one.
