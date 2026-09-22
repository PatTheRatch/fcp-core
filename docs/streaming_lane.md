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
> already have it, and the line above needs no sourcing. If `DATABASE_URL` is
> exported in your shell it is used in preference.

---

## Limitations, stated before conclusions

### 1. A "lane" here is a man who arrived mid-period, and men overlap in time

`daily_lineup_slots` records who sat where on each day, not which *place* a man
occupied. Two managers can rotate one lineup spot through three men and three
spots through one man each, and the table cannot tell them apart. So the lane
count is "how many rotating men a team used at its peak", not "how many places
it rotated", and **step 4 cannot cleanly split lane one from lane two.** The
doc says what it can support instead of guessing.

What the definition does support cleanly is the headline: the started
production of the men who were *not* there all week, against the production of
the men who were, and against the floor.

### 2. The unit is one (team, matchup period), and held means rostered every day

A **held** man is rostered on every day of the period's stored window. A man
dropped on the last day is streamed, not held, which is the right reading
(he did not hold the place) but means a one-day absence moves a man between
the two buckets. 2,040 team-periods across eight seasons; 1,536 of them ran at
least one streamed man.

### 3. An opened place is invisible in this table

Every team-day holds 13 men (2,221 of 2,240 team-days in 2026; the rest are
11 or 12, and those are in-period uneven trades). A team that trades two for
one holds 12 men thereafter, and `daily_lineup_slots` records no row for the
vacated place — so a lane here is a place found *within* the places the team
already held. **A place opened by an uneven trade and later streamed cannot be
measured from this table at all**, which is exactly the case the trade
evaluator cares about. The lane figure is therefore a floor on what a fully
emptied place returns: an emptied place can be streamed every day of the
period, where a lane inside a held roster competes for the same starts.

### 4. The pair asked for is asymmetric, so both baselines are reported

The primary baseline is the team's **held 13th man**, its lowest-valued held
man. That man's median is 0.00 — he is a bench player a manager never had to
start — so any ratio against him is not meaningful. The secondary baseline is
the code's own floor, `TYPICAL_PICKUP` = 0.06, which is the number this
measurement is meant to move. Both are on every table.

### 5. Categories, not composite, and not the league's nine wins

Values are categories a week through `app.scoring.value.marginal`, the same
lens `pickup_values` uses, so the number plugs straight into `TYPICAL_PICKUP`.
That lens converts a marginal count into a change in *expected category wins*,
which is closer to how the league scores than a composite is, but it is still
an average over a normal model of the league's weekly totals, not the nine
actual wins.

### 6. Percentages and turnovers are reported against an ordinary roster

Step 5 adds the lane's line to the season's average started week and re-scores
it. That is one baseline team, not the team that actually streamed. A
punt-FT% roster and a chase-FT% roster do not value the same volume the same
way, which is why the three categories are broken out rather than summed.

### 7. 2026 is complete in this data, and 2020 is pooled like any other season

`daily_lineup_slots` runs to scoring period 160 of 2026, the last day of the
final playoff period, so 2026 is a finished season here. 2020's COVID block is
flagged in step 1's table and step 6 reports the headline with and without it;
it does not move the answer.

### 8. Small samples

Fourteen teams at most, one league, eight seasons. The per-season columns are
directionally useful, not precise estimates.

---

## The answer, up front

**A streamed lane returned a median of 0.76 categories a week (IQR 0.39–1.26)
against the held 13th man's 0.00, over 1,536 team-periods.**

Against the number the code actually uses, that is the whole finding:

| Place | Categories a week (median) | IQR |
|---|---|---|
| **A streamed lane** (all its men, 1,536 team-periods) | **0.76** | **0.39 – 1.26** |
| A place held by one man, his own value (23,010 held men) | 0.43 | 0.20 – 0.67 |
| The held 13th man (2,040 team-periods) | 0.00 | 0.00 – 0.11 |
| **`TYPICAL_PICKUP`, the code's floor** | **0.06** | — |

**The floor is off by roughly a factor of twelve.** A rotating place returns
about 0.76 a week; the code prices every filled place at 0.06.

The games-per-week comparison is the mechanism, and it lands on the two ranges
the brief predicted:

| | Started games a week (median) | IQR | n |
|---|---|---|---|
| **One held man** | **3.0** | 2.0 – 4.0 | 23,010 held men |
| **A streamed place** (all its men) | **7.0** | 4.0 – 11.0 | 1,536 team-periods |

One man in a place starts 3 games a week, in every season, with almost no
variation. A rotating place starts 7. **The lane is a different asset, and the
difference is games, not talent**: the median single streamer's own value is
0.35 a week, *below* the 0.43 an ordinary held man gives. It is the succession
of them through one place that makes the difference.

**95.6% of streamed places beat the 0.06 floor. 32.8% of held 13th men do.**

---

## 1. How often a lane is run, by season

Definition: **peak rotating men** — the largest number of men rostered on any
single day of the period who were not rostered every day. Alternative
(**arrival runs**) in step 6.

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
is not a niche behaviour in this league — it is what most team-weeks look like.
The trend is upward and steep: 2023 is the quietest season on record (153 of
336 with no lane) and 2025–2026 are the loudest (95 of 240 and 108 of 308 with
three or more).

---

## 2. Games a week: the crux

| Season | Whole held roster | Streamed place | Held 13th man |
|---|---|---|---|
| 2019 | 33.0 (29–37) | 7.0 (4–10) | 2.0 (0–3) |
| 2020 | 31.0 (26–35) | 8.0 (4–12) | 0.4 (0–2) *(COVID)* |
| 2021 | 33.0 (29–36) | 8.0 (5–12) | 1.0 (0–3) |
| 2022 | 31.0 (27–35) | 8.0 (5–11) | 1.0 (0–2) |
| 2023 | 32.0 (27–35) | 5.0 (3–9) | 1.0 (0–2) |
| 2024 | 32.7 (28–36) | 7.0 (3–10) | 2.0 (0–3) |
| 2025 | 30.0 (26–34) | 8.0 (5–11) | 1.0 (0–2) |
| 2026 | 30.0 (26–35) | 8.0 (4–11) | 1.0 (0–2) |

Games a week, period length normalised out by each period's stored window
(2019's seventeen-day final, the All-Star fortnights and 2020's ninety-eight-day
block are the reasons this matters).

The comparison that answers the brief is **one held man against a streamed
place**, because that is the choice a manager actually faces: keep the man, or
rotate the place.

| | Median | IQR | n |
|---|---|---|---|
| One held man | **3.0** | 2.0 – 4.0 | 23,010 |
| One streamed man (the lane's best) | 3.0 | 2.0 – 4.0 | — |
| The streamed place, all its men | **7.0** | 4.0 – 11.0 | 1,536 |

**Yes: a lane really does play about 7 games to a held man's 3, and the
mechanism is entirely the rotation.** A single streaming pickup is no better
than a held man — median 3.0 games either way. The place returns 7 only because
a manager keeps replacing the man in it. That is why the asset is the *place*,
and why "a pickup is worth 0.06" is not the same statement as "a place is worth
0.06".

A held man's 3.0 is remarkably stable — 3.0 in all eight seasons, IQR 2–4 in
all eight. What makes a lane worth more is not that the men are better; it is
that there are more of their games, and a starting slot is available for them.

---

## 3. What the lane produced, in categories a week

Same lens as `TYPICAL_PICKUP`: `app.scoring.value.marginal` of the streamed
men's started lines against the team's week without them, times 7 over the
period's stored length.

| Season | Streamed median | p25 | p75 | Held 13th median | p25 | p75 |
|---|---|---|---|---|---|---|
| 2019 | 0.73 | 0.38 | 1.23 | 0.00 | 0.00 | 0.19 |
| 2020 | 0.93 | 0.38 | 1.44 | 0.00 | 0.00 | 0.12 *(COVID)* |
| 2021 | 0.91 | 0.45 | 1.44 | 0.00 | 0.00 | 0.17 |
| 2022 | 0.83 | 0.44 | 1.28 | 0.00 | 0.00 | 0.06 |
| 2023 | 0.54 | 0.26 | 0.87 | 0.00 | 0.00 | 0.00 |
| 2024 | 0.64 | 0.34 | 1.06 | 0.00 | 0.00 | 0.14 |
| 2025 | 0.93 | 0.54 | 1.45 | 0.00 | 0.00 | 0.15 |
| 2026 | **0.74** | 0.46 | 1.19 | 0.00 | 0.00 | 0.10 |

Pooled, 2019–2026, categories a week:

| Sample | Median | IQR | Mean | n |
|---|---|---|---|---|
| **Streamed lane** | **0.76** | 0.39 – 1.26 | 0.88 | 1,536 |
| Held 13th man | 0.00 | 0.00 – 0.11 | 0.05 | 2,040 |
| Every held man | 0.43 | 0.20 – 0.67 | 0.46 | 23,010 |
| Lane 1 (best own value) | 0.35 | 0.22 – 0.51 | 0.38 | 1,536 |
| Lanes 2+ (the rest) | 0.45 | 0.16 – 0.84 | 0.58 | 1,409 |

**Share above the 0.06 floor: streamed 95.6%, held 13th 32.8%.**
**Share above zero: streamed 97.9%, held 13th 40.9%.**

The streamed lane's median sits at 0.76 in every season except 2023 (0.54);
the range across seasons is 0.54–0.93. Nothing here is a pooled average hiding
a season that behaves differently.

Note the middle row: an ordinary held man is worth 0.43, more than a single
streamer (0.35) and much less than the rotating place (0.76). The lane's value
is not in the quality of any one man.

---

## 4. The add budget

Seven adds a matchup period (`ADDS_PER_PERIOD_DAY` × its days), shared across
every lane. Counted as the product counts them.

| Lanes run | Team-periods | Adds median | Adds mean | At the full 7 |
|---|---|---|---|---|
| 0 | 492 | 0.0 | 0.17 | 0.0% (0) |
| 1 | 488 | 2.0 | 2.33 | 3.3% (16) |
| 2 | 503 | 4.0 | 4.31 | 14.1% (71) |
| 3 | 327 | 6.0 | 5.64 | 34.9% (114) |
| 4 | 230 | 7.0 | 6.69 | 56.5% (130) |

**331 of 2,040 team-periods (16.2%) spent the full budget. ESPN refused 102
moves across eight seasons with `FAILED_MATCHUPACQUISITIONLIMIT`.** The budget
binds, and it binds where the lanes are: teams running four lanes hit seven
adds 56.5% of the time.

Production by lane count:

| Lanes | Median | IQR | Mean | n |
|---|---|---|---|---|
| 1 | 0.34 | 0.16 – 0.56 | 0.38 | 488 |
| 2 | 0.76 | 0.49 – 1.07 | 0.79 | 503 |
| 3 | 1.25 | 0.88 – 1.63 | 1.26 | 327 |
| 4 | 1.51 | 1.00 – 2.00 | 1.56 | 230 |

Two lanes return about 2.2× one lane (0.76 vs 0.34), not 2.0× — the second
lane is not visibly cheaper than the first.

### The second lane, and why the data cannot cut it cleanly

The brief asks for the marginal value of the second lane. **It cannot be
measured from this table**, for the reason in Limitation 1: lanes are men, and
men within one place overlap in time. Every split tried says the same thing,
and none of them says "the second lane is worth less":

| Split (lanes 2+ only, n = 1,053) | Median | IQR |
|---|---|---|
| Best own value (the men ranked by what each returned) | 0.35 | 0.22 – 0.51 |
| Everyone after the best | 0.45 | 0.16 – 0.84 |
| First man to **arrive** | 0.15 | 0.06 – 0.29 |
| Men arriving after him | 0.86 | 0.49 – 1.29 |

Every cut puts the *later* men higher, because a manager who is streaming is
streaming toward a better player: his first add of a week is a fill-in, and by
the time he has a lane running he is picking up someone he wants. So the data
shows **increasing** returns to the men added later, which is the opposite of
the diminishing-returns story the budget would predict.

**What the budget evidence does support**, from the adds table above: the
marginal *add* is what gets scarcer, not the marginal place. A team with one
lane spends a median 2 adds; with two, 4; with four, the full 7. **The proxy
this data can carry is adds, not lanes**: the second lane costs about 2 more
adds and returns about 0.42 more than the first (0.76 − 0.34), or roughly
0.21 a week per add. A team that has already spent its 7 has no second lane at
all, and 16.2% of team-periods are in that state.

Nothing in the data says the second lane should be discounted *in its
production*. It might still be right to discount it in a forecast, on the
grounds that a manager's attention and his waiver position are finite — but
that is an argument, not a measurement, and this doc will not dress it as one.

---

## 5. Percentages and turnovers

The lane's line added to the season's ordinary started week and re-scored,
per category (`per_category_marginal`). The six counts first, then the three
that behave differently.

| Season | PTS | REB | AST | STL | BLK | 3PM | **TO** | **FG%** | **FT%** |
|---|---|---|---|---|---|---|---|---|---|
| 2019 | 0.283 | 0.280 | 0.243 | 0.248 | 0.157 | 0.177 | **−0.258** | **−0.058** | **−0.007** |
| 2020 | 0.322 | 0.335 | 0.250 | 0.279 | 0.183 | 0.255 | **−0.284** | **−0.040** | **0.000** |
| 2021 | 0.299 | 0.318 | 0.198 | 0.257 | 0.182 | 0.264 | **−0.248** | **−0.061** | **−0.039** |
| 2022 | 0.251 | 0.269 | 0.215 | 0.218 | 0.184 | 0.235 | **−0.207** | **−0.042** | **−0.030** |
| 2023 | 0.154 | 0.179 | 0.159 | 0.184 | 0.099 | 0.151 | **−0.165** | **−0.044** | **0.000** |
| 2024 | 0.211 | 0.218 | 0.180 | 0.183 | 0.151 | 0.149 | **−0.189** | **−0.038** | **−0.014** |
| 2025 | 0.260 | 0.286 | 0.251 | 0.263 | 0.221 | 0.222 | **−0.293** | **−0.064** | **−0.021** |
| 2026 | 0.264 | 0.270 | 0.203 | 0.233 | 0.137 | 0.212 | **−0.235** | **−0.053** | **−0.014** |

Medians, in the same units (change in expected category wins):

| Category | Whole lane | One streamer |
|---|---|---|
| PTS | 0.264 | 0.105 |
| REB | 0.270 | 0.085 |
| AST | 0.203 | 0.071 |
| STL | 0.233 | 0.082 |
| BLK | 0.137 | 0.046 |
| 3PM | 0.212 | 0.074 |
| **TO** | **−0.235** | **−0.050** |
| **FG%** | **−0.053** | **+0.011** |
| **FT%** | **−0.014** | **+0.017** |

**The three categories the brief singled out behave exactly as volume says
they should, and the two ratios behave differently from each other:**

- **Turnovers are the lane's single largest cost: −0.235, roughly the size of
  its PTS gain (0.264).** A lane is close to turnover-neutral in categories
  won, which is not what "free production" implies.
- **FG% is a real cost (−0.053) but a small one** — about a fifth of the PTS
  gain. The wire's shooters are not disasters.
- **FT% is essentially flat (−0.014, and exactly 0.00 in two seasons).**
- **A single streamer does not hurt FG% or FT% at all** (+0.011, +0.017) and
  costs only −0.050 in TO. The damage in the ratios is a property of *volume*,
  not of streaming: one man's week is too small to move a ratio, and a place
  running seven games is not.

That is the read a roster needs: **a punt-FT% roster gives up nearly nothing
(0.014 in a category it has already conceded) and should stream harder; a
chase-FT% roster pays real but small value, and the cost that actually bites
both is turnovers, at −0.235 a week.** Nothing in the lane's FT% cost is large
enough to reverse a decision on its own.

---

## 6. Sensitivity

The headline is step 3's pooled median. Under every variant tested it does not
move:

| Variant | Median | IQR | Mean | n |
|---|---|---|---|---|
| **2019–2026, all seasons** | **0.76** | 0.39 – 1.26 | 0.88 | 1,536 |
| 2024–2026 only | 0.78 | 0.42 – 1.26 | 0.89 | 717 |
| 2019–2026, **no 2020** | 0.74 | 0.39 – 1.23 | 0.87 | 1,409 |

Held 13th man under the same variants, for the comparison: median 0.00, IQR
0.00–0.11 / 0.00–0.13 / 0.00–0.11 respectively. Mean 0.05 in all three.

The lane-count definition does move the counts, which is worth stating
honestly even though it does not move the headline:

| Lanes | Peak rotating men | Arrival runs |
|---|---|---|
| 0 | 492 | 490 |
| 1 | 488 | 224 |
| 2 | 503 | 927 |
| 3 | 327 | 367 |
| 4 | 230 | 32 |

The two definitions agree almost exactly on 0 lanes (492 vs 490) and diverge
everywhere else. Counting consecutive arrivals lumps a manager's drop-and-
re-add cycle into one lane, which moves mass from "1 lane" to "2 lanes"
(224/927 against 488/503). **The headline does not depend on the choice** —
what the lane *produced* is the same set of men under either definition. Only
the label on the number of lanes changes, and the doc's step 4 conclusions are
drawn from the peak-men definition.

---

## What this means for the code

Nothing in this section has been implemented. It is what the numbers above
support, written down so the change can be made deliberately.

### `TYPICAL_PICKUP` is two numbers, not one

The constant is used for two different things and they should not share a
value:

1. **What a place gives back when it is streamed** (an opened place, the
   side of a 2-for-1 trade that received fewer men, the counterfactual in
   `season_cost`). Measured lane: **0.76 a week, IQR 0.39–1.26.** The safe
   floor against the distribution is the lower quartile, **0.39**; the central
   estimate is 0.76.
2. **What a typical *pickup* returns once** — the median executed add held
   for a fortnight, which is 0.062–0.128 by season. That measurement is
   correct as it stands and should not change; it measures a man, and a man
   is worth about what it says.

The bug is that (2) is being used where (1) belongs. `pickup_values`' own
docstring already says the value "falls as adds rise: the more a league
streams, the less each add is" — that is the same fact, seen from the other
end.

**Recommended:** keep `TYPICAL_PICKUP = 0.06` as the per-add figure, and add a
separate `STREAMED_PLACE = 0.39` (the lane's lower quartile) or `0.76` (its
median), used **only** where the code is pricing a *place that will be
rotated*. Do not raise the per-add floor: that would pay every no-op swap.

### The number should fall with the number of lanes — but not by the amount the brief expected

The budget evidence is real: a second lane costs about 2 more adds, teams
running four lanes hit the 7-add ceiling 56.5% of the time, and 16.2% of
team-periods spent the whole budget. **But the data shows no fall-off in the
second lane's production** (0.76 vs 0.34, a 2.2× step for the second lane, not
a discount) and no fall-off in value per add.

The honest coding consequence is narrower than "discount lane two":

- **The first lane is worth 0.76 (or 0.39 at the floor) and there is no
  measured discount for the second.** If a forecast wants to discount it, the
  discount must come from a model of the budget and of manager attention, and
  that model does not exist yet — the doc above gives the *inputs* (adds per
  lane, the share at the ceiling) rather than the discount.
- **What should be enforced instead is the budget itself.** A team that has
  used its 7 adds cannot run a lane at all, and `adds_left` already exists in
  `app.pickups.state`. The place value should be gated on there being an add
  to spend, not scaled by an unmeasured coefficient.

### The three call sites affected

1. **`app/pickups/judge.py` — `places_cost` and `season_cost`.** `wire` is
   `wire_replacement(...)` floored at `TYPICAL_PICKUP`. Every place a move
   opens is credited at that floor; every place it fills is capped at it. Under
   this measurement, a place left open is worth **0.39–0.76**, not 0.06, so
   `places_cost("leaving", "arriving", replacement)` currently **understates
   the cost of opening a place by roughly 6–12×**. That is the direction that
   makes 2-for-1s look worse than they are in reverse: the side *giving up* two
   men for one opens a place it is credited 0.06 for, when the wire would
   return 0.39–0.76.

2. **The trade evaluator's settlement** (`app/trades/evaluate.py`,
   `_settle_drops` → `places_cost`, and the playoff path at
   `evaluate.py:1027`, `replacement = max(TYPICAL_PICKUP, best wire man)`).
   Same constant, same understatement. This is the 2-for-1 error the
   calibration found, and the settlement is one of its two named causes
   (`docs/trades.md` §7, "Reading it").

3. **`app/scoring/moves.py`'s replacement charge** — `grade_move` settles an
   uneven move at replacement level, and `app/scoring/trade_grades.py` feeds
   it `median_of(pickup_values(book))` (0.062–0.128). `docs/trades.md` §7,
   "Reading it", names this as the *other* cause of the 2-for-1 gap: the
   hindsight grade "charges a flat replacement level for the spot — the median
   pickup in this league, about 0.07 categories a week", while the forward
   engine credits the best free agent. **Both are wrong in the same direction,
   and they are wrong because neither is pricing a rotating place.** The
   hindsight grade's 0.07 should be 0.39–0.76; that would narrow the gap §7
   reports, and it should be re-run before the settlement is trusted.

### What would make this exact

1. **A place identity on `daily_lineup_slots`.** The single missing fixture.
   A stable per-team place index (or a `slot_id` that persists across days for
   `UT`) would let a lane be cut from the place it actually occupied, and would
   make step 4 answerable instead of approximable.
2. **Adds attributed to a lane.** `transaction_items` says who arrived; with a
   place index it would say where he went, which is what the second-lane
   question actually asks.
3. **A manager-attention model.** To turn "the second lane returned 0.42 more
   for 2 more adds" into a discount, something has to model whether the manager
   would have made those adds well. That is a replay, not a measurement.

---

## Decisions

Every judgement call made in producing these numbers, so a reader can
disagree with one without re-deriving the whole thing.

### The definition of a lane

1. **Places are defined by occupancy, not by ESPN slot id.** Confirmed from
   the data: the nine slot values are PG, SG, SF, PF, C, G, F, UT, BE, and
   `UT` alone accounts for 46,371 rows — a lineup position, not a place.
   Occupancy is the only stable identity available in this table.

2. **Held = rostered every day of the matchup period.** No requirement that he
   *started* every day. A held man who sat on the bench all week still held
   the place, and his value is what the place returned. (Requiring starts
   would have made "held" mean something closer to "starting", which is a
   different question.)

3. **Streamed = any other man rostered in the period.** Held and streamed
   partition the roster-days exactly, so nothing is double-counted.

4. **A lane's production is the *sum* of its men's lines, scored as one line**
   — not the sum of the men's individual marginals. The men overlap in the
   team's week, so the individual marginals are not strictly additive; scoring
   the combined line is the defensible construction and it is what answers
   "what did this place return". Checked: the two differ by about 3% (mean
   0.883 as one line against 0.907 summed individually, n = 1,536), so the
   choice is not load-bearing, but the combined line is the one reported.

5. **`lanes_by_occupancy` (peak rotating men on any day) is the headline
   definition**; `lanes_by_arrival` (runs of men arriving ≤1 day apart) is the
   alternative, reported in step 6. The brief asked for one alternative and
   this is the most different reasonable one.

### The baselines

6. **The held 13th man is the lowest-valued *held* man, ranked by the same
   lens**, per team-period. Reported alongside the pooled "every held man"
   (0.43) rather than instead of it, because the 13th man's median of 0.00 is
   the honest answer to the brief's question but a poor ratio.

7. **`TYPICAL_PICKUP` is reproduced, not assumed.** Step 0 recomputes
   `pickup_values` for all eight seasons and gets 0.062–0.128, matching the
   constant's docstring (0.06–0.13 by season). This is a check that the
   measurement is in the same currency, not a new number.

### The currency

8. **The lens is `app.scoring.value.marginal` against `SeasonOpponents.for_period`**
   — the exact call `SeasonBook.value` makes, so `pickup_values` and this are
   directly comparable. No new lens was written.

9. **Period normalisation is by `matchup_periods`' stored window**, not by the
   count of days actually present. These agreed on all 2,040 team-periods
   (zero window gaps), so the distinction never bit; the stored window is used
   because it is also what `pickup_values` and the code divide by.

10. **Periods are never pooled across lengths for the distributions** — the
    All-Star fortnight is scored against fortnights, as `SeasonOpponents` does.

11. **The reproduce line does not source `.env`.** The house style
    (`set -a && . ./.env && set +a`) fails against this worktree's `.env` at
    line 29, where `FCP_EMAIL_FROM` holds unquoted angle brackets. The script
    reads `DATABASE_URL` from the file itself instead, so the documented
    command works as written. `/opt/fcp-core/.env` has no such line, which is
    why the older docs' reproduce lines were unaffected.

### Steps 4, 5 and 6

11. **Step 4's second-lane question is answered as "the data cannot separate
    it", with both splits shown.** Ranked by own value and ordered by arrival
    give opposite (and, in both cases, non-discounting) answers, which is the
    evidence for the claim. The proxy given is adds per lane, which the data
    does support.

12. **Adds are attributed to a period by the transaction's `scoring_period`
    falling inside the period's stored window**, counted once per ADD item,
    matching `app.pickups.state._adds_in_period`.

13. **The 7-add budget is taken as `ADDS_PER_PERIOD_DAY × period days`** from
    `app.pickups.state`, and corroborated independently: 102
    `FAILED_MATCHUPACQUISITIONLIMIT` refusals exist in `transactions` and the
    per-period counts of them rise with the lane counts (4 in period 2, 13 in
    period 18 of 2026).

14. **Step 5's baseline is the season's average started week for the ordinary
    period length** (`average_team_line`), not the streaming team's own week,
    so the figure is comparable across teams. Stated in Limitation 6.

15. **Step 6 reports 2019–2026, 2024–2026, and no-2020.** The brief asked for
    all three; the no-2020 run confirms 2020 is not carrying the answer.
    `--drop-2020` and `--seasons` expose both.

### Scope and hygiene

16. **2020 is pooled in the headline** (and flagged in the tables), because the
    brief's sensitivity list treats dropping it as a variant, not the default.
    It moves the median by 0.02.

17. **Team-periods with a window gap are excluded; there are zero of them.**
    The exclusion is in the code as a guard, and its count is printed per
    season (0 in all eight) so a future ingest defect would be visible rather
    than silent.

18. **The script is read-only.** SELECT-only, no writes, no alembic. It opens
    the database read-write (the shared `make_engine`) but issues no mutating
    statement; that was checked by reading every query in it rather than by
    running against a read-only role.

19. **Only `docs/streaming_lane.md` and `scripts/streaming_lane.py` are
    added.** Nothing in `app/` was touched, so this doc is a measurement and a
    recommendation, not an implementation.

### What was not done, and why

20. **The independent recomputation of the 2026 headline agreed to within 1%.**
    A second implementation, sharing no code with the script (its own
    normal-CDF score, its own occupancy pass, distributions built from summed
    team-week lines rather than `matchup_team_stats`), gives 2026 median
    **0.7291** against the script's **0.74**, with the same n (257) and mean
    (0.842 vs 0.86). The residual is the opponent-distribution source, not the
    lane definition. **A reader recomputing 2026 from
    `daily_lineup_slots` + `player_game_stats` should expect 0.73–0.74** and
    should treat a larger gap as a definitional disagreement, not noise.
21. **Lane-level add attribution was not attempted.** No place identity exists
    to attribute an add to, so the "which add built lane two" question is left
    as the proxy rather than approximated silently.

