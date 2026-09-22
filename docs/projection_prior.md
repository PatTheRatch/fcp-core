# Does Basketball Monster's preseason forecast make the in-season tool better than ESPN's?

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft, FAAB from 2026
**Season:** 2026 (14 teams) — the one season with a BBM preseason export
**Script:** `scripts/projection_prior.py` — passes `ruff` and `mypy`
**Reproduce:** `cd /opt/fcp-core && set -a && . ./.env && set +a && .venv/bin/python scripts/projection_prior.py`
**The prior under test:** Basketball Monster's projections, as the draft room reads them (`app/draft/bbm.py`), against ESPN's stored preseason projection (`player_season_stats`, `kind = "projected"`) that every in-season number the product publishes was measured on.

---

## Limitations, stated before conclusions

### 1. One season, and it is the only one that can be measured

The BBM export we hold is 2026's. `bbm_projections` holds 2027, captured *after* that draft, so it is a rest-of-season file rather than a preseason forecast and cannot answer the question at all. There is no third source. So this is one season, 14 teams, 1,402 paired player-checkpoints — not the six seasons and 7,165 checkpoints the fit in `knowable.py`'s docstring rests on. Anything here is a 2026 result.

### 2. The 2026 export predates the fix that made BBM exports trustworthy

`app/draft/bbm.py`'s docstring and `scripts/bbm_pull.py` record a trap found on **2026-09-16**: a browser settings change in BBM (the punt panel's `cat_25` box, unticked) both dropped the `Leag$` column and **shifted projected games across the board**, and the same change lifted the draft plan from 5.53 to 6.19 expected wins. The pull now forces the box on and refuses any export whose median player's projected games moved by `GAMES_SHIFT = 2.0` or more.

**The file this study reads is dated 2026-09-15 07:12 — the day before that fix.** It carries 48 columns and no `Leag$`, which is exactly the signature of the pre-fix state. Whether its projected-games column was inflated by the setting is not recorded anywhere, and cannot now be recovered: the file is what it is. Since BBM's projected games are the one place it beats ESPN (its realized-over-projected games run 0.96 against ESPN's 0.88, per `app/draft/bbm.py`), an inflated games column would make BBM look **worse than it is** on everything measured in games, and would leave the per-game rates untouched. Read every BBM figure here as a *lower bound* on a correctly pulled export.

### 3. Unmatched names are treated as "no projection", which is the product's own rule

`load_bbm` places a BBM row on our ids through the strict matcher (`app/draft/bbm.name_key`, `match_player`), which refuses a doubtful match rather than guessing. Of the export's 512 rows, **507 matched (5 of them by a short first name) and 5 were placed nowhere**. A man BBM cannot place is then exactly what a man ESPN never projected is: `knowable` leaves him on his season to date alone. That is deliberate — a wrong match is worse than none — but it means BBM's arm is short 194 of 1,610 player-checkpoints that ESPN's arm covers, and the coverage table prices what that costs.

### 4. What the error metric is, and what it is not

The fit's error is the sum, over the eleven counts of `app.scoring.lines.COUNTS` (the nine categories plus the made and attempted shots behind the two percentages), of the absolute difference between the forecast per-game rate over the next 28 days and what the player did, **each count divided by its spread**. It is a number with no units, useful for ranking two forecasts on the same players and nothing else.

Two things follow, and both matter for reading the headline. The **scaling is order-dependent**: the docstring's 2.85 divides each count by its spread pooled across six seasons, while every figure here is 2026 alone against 2026's own spreads. The two are not on the same scale and are not quoted against each other. And the metric is *absolute*, so a prior that is systematically low (BBM's is — see section 3) is penalised for its level even where its *shape* is better. The currency table in section 6 is where level-fairness is restored.

### 5. Both arms are scored against the same games, so nothing here leaks

A checkpoint's forecast is built from games before it (`scoring_period < day`, `played` and `minutes > 0`) and scored on games from it to day + 28. `tests/test_scoring_knowable.py` pins the day boundary; this script's own `blended` is held against `app.scoring.knowable.knowable` on 451 player-arms with **worst gap 0.00e+00**, so the arithmetic being measured is the product's arithmetic and not a re-implementation that drifted.

---

## The answer, up front

**On 2026, over 1,402 players with both priors and 6 checkpoints, the blend's error on the next 28 days was 4.65 with ESPN's prior and 4.62 with BBM's.**

That is a **0.6% improvement**, against a noise criterion fixed before computing it (better at 4 of 6 individual checkpoints *and* pooled by 3% or more). BBM's blend is better at 4 of 6 checkpoints — 21, 42, 63 and 105 — and worse at 84 and 126; pooled it is better by 0.03 of 4.65. **The criterion is not met.**

The interesting result is not the blend. It is the two priors *alone*: BBM's own forecast is materially better than ESPN's (**5.59 against 5.76**, a 3.0% gain), and it is the blend that throws most of that away. Season to date displaces the prior so fast — fifteen games of prior weight against a 28-day window — that by the time a man has played a few weeks, which is almost every checkpoint here, which forecast fed the prior barely survives. The prior choice is a **first-few-weeks** decision, and this season cannot resolve it further.

**So BBM is not a dependency for the in-season tool.** It is a comparable-but-not-better prior, worth a small and late-decaying edge in the opening weeks, and the recommender's published numbers do not move for it.

---

## 1. The fit's own units, on the players both priors cover

Paired population: the 1,402 player-checkpoints where the man has both an ESPN and a BBM projection, so the two arms are the same men and the difference is the forecast, not the coverage.

| checkpoint | n | ESPN prior alone | ESPN blend | BBM prior alone | BBM blend | to date alone |
|---|---|---|---|---|---|---|
| 21 | 219 | 5.61 | 4.78 | 5.41 | 4.71 | 5.98 |
| 42 | 229 | 5.64 | 4.69 | 5.45 | 4.64 | 5.03 |
| 63 | 236 | 5.71 | 4.53 | 5.46 | 4.45 | 4.66 |
| 84 | 239 | 5.66 | 4.45 | 5.41 | 4.42 | 4.64 |
| 105 | 240 | 6.16 | 4.91 | 6.00 | 4.90 | 5.19 |
| 126 | 239 | 5.75 | 4.57 | 5.77 | 4.59 | 4.77 |
| **pooled** | **1402** | **5.76** | **4.65** | **5.59** | **4.62** | **5.03** |

Read across the two "alone" columns and BBM wins at five of six checkpoints; read across the two "blend" columns and it wins at four, by 0.01–0.08, and loses the last two outright. The blend column is the one that matters, and it is a tie by any honest reading.

Checkpoint 105 is the All-Star fortnight for most of its 28-day window, which is why every arm's error rises there; that is a property of the schedule, not of either prior.

## 2. The same, every rostered player (the coverage effect)

Everyone rostered at the checkpoint, with a prior the man's arm does not cover leaving him on his season to date — the rule `knowable` already applies. The counts differ by column because the arms are one prior each.

| checkpoint | n | n with a prior (ESPN/BBM) | ESPN prior alone | ESPN blend | BBM prior alone | BBM blend | to date alone |
|---|---|---|---|---|---|---|---|
| 21 | 232 | 220/231 | 5.61 | 4.78 | 5.46 | 4.70 | 5.94 |
| 42 | 253 | 231/251 | 5.64 | 4.68 | 5.48 | 4.55 | 4.94 |
| 63 | 269 | 238/267 | 5.70 | 4.53 | 5.57 | 4.43 | 4.61 |
| 84 | 282 | 242/279 | 5.64 | 4.44 | 5.56 | 4.35 | 4.50 |
| 105 | 285 | 243/282 | 6.14 | 4.90 | 5.97 | 4.76 | 5.00 |
| 126 | 289 | 242/286 | 5.75 | 4.58 | 5.78 | 4.50 | 4.71 |
| **pooled** | **1610** | **1416/1596** | **5.75** | **4.65** | **5.65** | **4.54** | **4.92** |

Unmatched names *help* BBM here (4.54 against 4.65 pooled), which reads backwards until you see which men they are: BBM's five unplaced rows are the deep-bench fringe, whose forecasts are poor whichever arm misses them, so dropping them from BBM's arm lowers its average. That is a coverage artefact, not an edge, and it is why the headline is taken on the paired table.

## 3. Coverage

| population | player-checkpoints | mean games at the checkpoint | blend error on the arm it does cover |
|---|---|---|---|
| both priors | 1402 | 27.41 | 4.65 |
| BBM cannot place (ESPN only) | 14 | 31.57 | 4.28 |
| ESPN has no line (BBM only) | 194 | 31.70 | 4.02 |

The middle row is the matcher's cost: **14 player-checkpoints** — five BBM rows with no id of ours at all. It is small, and the men it costs are late-season arrivals. The bottom row is the other asymmetry, and the larger one: **194 player-checkpoints, 12% of the roster-weeks, where ESPN has no line and BBM does.** ESPN publishes projections for the pool it considers draftable; BBM projects deeper. On those men BBM's blend scores 4.02, against 4.65 on the men both cover. That is where BBM's real in-season edge lives, and the paired table is structurally blind to it.

## 4. By games played at the checkpoint (paired players)

The prior should matter most early, and the question is how fast a man's own games make the source irrelevant.

| games at the checkpoint | n | ESPN prior alone | ESPN blend | BBM prior alone | BBM blend | to date alone |
|---|---|---|---|---|---|---|
| 0-10 | 254 | 5.83 | 4.97 | 5.63 | 4.91 | 6.42 |
| 11-30 | 593 | 5.82 | 4.72 | 5.60 | 4.66 | 4.86 |
| 31+ | 555 | 5.70 | 4.47 | 5.59 | 4.46 | 4.60 |

The premise holds and the answer is "quickly". With ten or fewer games the blend beats season to date alone by a wide margin (4.91 against 6.42 on BBM's prior, 4.97 against 6.42 on ESPN's) and BBM's blend leads ESPN's by **0.06**; past thirty games season to date alone is within 0.14 of the blend and the two priors are separated by **0.01**. BBM's advantage is real, small, and confined to the first third of a season — roughly six weeks.

## 5. Which counts each prior gets wrong (paired, pooled)

Mean absolute error per count, in spreads. Positive `ESPN - BBM` means BBM is better on that count.

| count | ESPN | BBM | ESPN - BBM |
|---|---|---|---|
| PTS | 0.38 | 0.38 | 0.00 |
| REB | 0.36 | 0.36 | 0.01 |
| AST | 0.35 | 0.35 | -0.00 |
| STL | 0.64 | 0.64 | -0.00 |
| BLK | 0.46 | 0.46 | 0.00 |
| 3PM | 0.42 | 0.42 | 0.00 |
| TO | 0.47 | 0.46 | 0.01 |
| FGM | 0.41 | 0.41 | 0.00 |
| FGA | 0.37 | 0.37 | 0.01 |
| FTM | 0.39 | 0.38 | 0.01 |
| FTA | 0.40 | 0.39 | 0.00 |

**Nothing is concentrated.** Every difference is 0.00 or 0.01 of a spread — the whole story is in the blend, not in any category. There is no "BBM is better on minutes-driven counts, ESPN on percentages" pattern to find: steals are the hardest count for both (0.64), assists the easiest (0.35), and the two priors are within a hundredth of each other on all eleven. A difference this uniform, this small, is what a tie looks like when you look closely.

## 6. In the recommender's currency: categories a week

The fit's units are not the currency the product publishes. Each arm's line is priced through `app.scoring.value.marginal` inside the league-average roster of the period's own length (`app.pickups.judge.standard_lens` — the lens `pickup_values` measures the wire with), over the games a week a manager actually gets (2.49, measured from the season), and differenced against what the man then delivered. This is the number that says whether a hurdle moves. Four decimals: at two, every arm reads the same.

| checkpoint | n | ESPN prior alone | ESPN blend | BBM prior alone | BBM blend | to date alone |
|---|---|---|---|---|---|---|
| 21 | 219 | 0.1229 | 0.1096 | 0.1245 | 0.1106 | 0.1449 |
| 42 | 229 | 0.1171 | 0.1019 | 0.1205 | 0.1013 | 0.1119 |
| 63 | 236 | 0.1198 | 0.1020 | 0.1197 | 0.1014 | 0.1068 |
| 84 | 239 | 0.1217 | 0.1062 | 0.1211 | 0.1062 | 0.1136 |
| 105 | 240 | 0.1334 | 0.1093 | 0.1370 | 0.1114 | 0.1161 |
| 126 | 239 | 0.1227 | 0.1055 | 0.1247 | 0.1068 | 0.1094 |
| **pooled** | **1402** | **0.1230** | **0.1057** | **0.1246** | **0.1063** | **0.1168** |

**The hurdles do not move.** The blend is wrong by about **0.105 categories a week** on ESPN's prior and **0.106** on BBM's — a gap of 0.0006, against hurdles written at 0.20 and 0.10. The recommender's own published errors are two orders of magnitude larger than the difference between the two priors, and there is no checkpoint where the ordering is stable enough to matter (BBM's blend is ahead at 42 and 63 and behind at the rest).

The first column is the one thing here that would matter if true: with no games at all the two priors differ by 0.0016 a week, and BBM's *alone* is worse (0.1246 against 0.1230) despite being better in the fit's units. That is the level effect of section 4's limitation showing up: priced inside a marginal—which cares about shape against a roster, not absolute level—BBM's systematically low line is worth slightly less at the start of a season, not more.

## 7. The weights, held out of the comparison

The comparison above runs both priors through the product's own fitted shape (15 games of prior weight, 15% recent). A prior could in principle buy a better *shape*, so the grid is swept to show how much is on the table. It is reported, not adopted: letting one prior have its own weights would make the comparison partly about the tuning.

| weights (games / recent) | ESPN | BBM | ESPN - BBM |
|---|---|---|---|
| 0 / 0.00 | 5.76 | 5.59 | 0.17 |
| 5 / 0.00 | 4.67 | 4.66 | 0.01 |
| 10 / 0.00 | 4.68 | 4.65 | 0.03 |
| 10 / 0.15 | 4.63 | 4.61 | 0.03 |
| 15 / 0.00 | 4.73 | 4.68 | 0.04 |
| **15 / 0.15 (the product)** | **4.65** | **4.62** | **0.04** |
| 20 / 0.00 | 4.78 | 4.73 | 0.05 |
| 30 / 0.00 | 4.89 | 4.82 | 0.07 |

Two things. BBM leads on **every** cell, by 0.01 to 0.17 — the edge is consistent, and it grows as the prior is given more weight (0.17 with no games at all). And the gain available from re-tuning is larger than the gain from switching prior: the best cell for ESPN (4.63 at 10/0.15) beats the product's own cell (4.65), so shape is worth about as much as the vendor choice and neither is worth much. The flat surface the docstring describes is still flat on a second vendor's numbers.

---

## What this means

**Is BBM a better prior?** On the blend, the in-season tool's own line: marginally, inconsistently, and not beyond noise. BBM's blend beats ESPN's at four of six checkpoints and by 0.6% pooled, against a criterion of 4-of-6 and 3%. It is better on the prior *alone* (5.59 against 5.76) and the blend gives most of that up. Two of six checkpoints go the other way.

**By how much, and for how long?** The useful edge is early. Inside the first ten games BBM's blend is ahead by 0.06 in the fit's units and season to date is still clearly worse than either prior; past thirty games the two are separated by 0.01 and the player's own games carry the line. Call it a real advantage for the first six weeks of a season that decays to nothing.

**Would it move any number the product publishes?** No. The recommender's own currency says the blend is wrong by 0.1057 a week on ESPN's prior and 0.1063 on BBM's — a difference of six ten-thousandths of a category, against hurdles of 0.20 and 0.10 and a measured stream worth +0.16 a week. The trade calibration, the hurdles and the +0.16 all stand whether the prior is ESPN's or BBM's. The one published number at risk is none of them: it is *coverage*, the 12% of roster-weeks ESPN has no line for and BBM does, which the paired table cannot see and which no current product figure depends on.

**So the answer to Patrick's question is that BBM is a nice-to-have for draft night, not an in-season dependency.** The draft room is right to run on it — availability is priced there and ESPN does not price it, and `app/draft/bbm.py`'s docstring reports the consequence: bidding on ESPN's projections, the market bought every ageing star ESPN was discounting and finished 81-88, where the roster actually drafted went 99-69. In season, where the player's own games dominate within a few weeks, the vendor choice is noise. And this is one season of a file pulled before the export settings were fixed, so even the 0.6% should be read as a rough edge of a tie rather than a figure to plan on.

---

## Decisions

Every judgement call, in the order it arose.

- **The seam in `app/scoring/knowable.py` takes a `prior`, not a source name.** It accepts a mapping of player id to per-game rate or a callable, and `prior_from` wraps a mapping so the blend has one shape to reason about. Passing nothing leaves the function exactly as it was — the whole existing suite passes unchanged — and passing a prior replaces *both* `projection_rate` and `snapshot_rate`, because a caller that hands over a prior is asking about a prior that is not ESPN's at all.
- **The script calls `blended`, a local copy of the blend with the weights exposed, rather than the product's `knowable`.** The sweep needs weights the product's fit deliberately does not expose. To stop that copy drifting, `check_blend_against_product` holds it to `knowable` on 451 player-arms at two priors; it reports **worst gap 0.00e+00**, and the script prints that line on every run.
- **"Spread" is read as the spread over the player-checkpoints of the cut being reported.** The docstring says "each scaled by its spread" without saying over what. The cut's own population is the only reading that puts every row of a table on one scale. A consequence stated plainly in section 4 of the limitations: this makes 2026's numbers *not* the docstring's 2.85 and no figure here is quoted against that.
- **The correlated pairs (FGM/FGA, FTM/FTA) are counted, and double-counting the two percentages is accepted.** All eleven `COUNTS` are summed, as the docstring's fit does. A made shot and the attempt it came from are near-perfectly correlated, so the eleven counts are not eleven independent errors. The alternative — nine — would not be the docstring's metric and the reviewer could not recompute it.
- **The population is rostered men at the checkpoint** (`daily_lineup_slots`, any slot but FA, first day before the checkpoint), because that is who the recommender answers about. A free agent who is never rostered is not a decision the tool makes.
- **The headline is the paired table, and the coverage effect is separate.** Paired holds both arms to the same men so the difference is the forecast; the all-rostered table and the coverage table exist so the matcher's cost and BBM's deeper pool are visible rather than hidden by the pairing. The brief asks for both; the coverage table is where the more interesting asymmetry turned up.
- **A checkpoint needs 28 days of games to follow it or it is dropped** (`if not after: continue`), so day 126 is the last one. Two-player checkpoints are also dropped: no games, nothing to score.
- **The currency table reports four decimals.** Two decimals flattened all five arms to 0.10–0.12 and the table said nothing; the quantity is a hundredth of a category and the brief's two-decimal rule is for figures, not for a column where two decimals is zero.
- **`games_a_week` is measured (2.49), not assumed to be seven.** The currency question is what a line is worth inside a competing roster over the games a manager actually gets, and spreading a rate over seven games would price men for games nobody plays. 2.49 is the season's played games per man per week.
- **The weights are swept, not re-fitted per prior**, and the sweep is reported rather than adopted — otherwise the comparison becomes partly about tuning and the reviewer could not recompute the headline from the product's own shape.
- **The backtest criterion was fixed before computing it: better at 4 of 6 checkpoints AND pooled by 3% or more.** BBM's blend is better at 4 of 6 but pooled by 0.6%, so the criterion is **not met** and `scripts/pickups_backtest.py` was **not run**: at ~50 minutes it would buy a re-measurement of a difference six ten-thousandths of a category wide, which the currency table in section 6 already shows cannot move a hurdle.
- **A zero line is reported as a wiring check.** `scales` prints what predicting nothing would score (18.78 on the paired set); a forecast at or above its own number is a defect rather than a finding. It caught one: an all-zero recent window was being treated as a live one, which is worth 3.7 of error on a man with no games yet.
- **`load_bbm` keys its rows by ESPN player id, and this script translates them to `players.id`.** The two are different numbers in this database (id 2 is ESPN 3133628) and the first run silently paired nothing because of it. The translation goes through `players.espn_player_id`, and a matched name the roster never carried is reported rather than dropped.
- **`mypy app` was failing before this branch started**, on two missing `playwright` stubs in `app/draft/page.py` and `app/draft/bidder.py`. I installed the declared extra into the shared venv — `pip install 'playwright>=1.47'`, from `pyproject.toml`'s `[project.optional-dependencies] live`. No file in the repo changed for it. **Flagging it as an environment change outside the worktree**, not a code change to review.
- **The brief's `tests/test_knowable*.py` glob matches no file.** The real suite is `tests/test_scoring_knowable.py`, and it is included in every run quoted here beside `tests/test_pickups*.py`.
- **The new script is not wired into anything and nothing reads it.** It is a measurement; the seam it uses defaults to today's behaviour, so the product's numbers are untouched until something passes a prior, and nothing does.
