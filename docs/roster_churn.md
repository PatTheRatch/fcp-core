# Roster Churn: How Much Does the Draft Actually Matter?

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870)
**Seasons covered:** 2019–2026 (eight seasons, 103 team-seasons)
**Script:** `scripts/roster_churn.py` (re-runnable, gates clean)
**Baseline:** `draft_picks` · **Instrument:** `daily_lineup_slots`

---

## The short version

**The draft matters less than the draft-day experience suggests, and it is
getting less important over time.**

By season's end, teams retain a median of **~31% of the players they drafted**.
The other ~69% of the closing roster arrived via free agency or waivers. A
typical team cycles through **41–79 different players** in a season to fill 13
slots.

And the trend is one-directional. Retention has fallen from **51.9% in 2023 to
23–26% in 2025 and 2026** — the last three seasons are the three lowest on
record, and 2026 is the lowest-but-one with the season still unfinished.

---

## 1. How much of the draft survives to season end

| Season | Teams | End retention (mean) | Median | Min | Max | Players used |
|---|---|---|---|---|---|---|
| 2019 | 10 | 29.2% | 30.8% | 7.7% | 53.8% | 62.6 |
| 2020 | 10 | 46.2% | 42.3% | 15.4% | 69.2% | 52.1 |
| 2021 | 10 | 33.8% | 34.6% | 7.7% | 61.5% | 66.0 |
| 2022 | 12 | 40.4% | 42.3% | 15.4% | 61.5% | 58.0 |
| 2023 | 16 | **51.9%** | 50.0% | 23.1% | 92.3% | 41.1 |
| 2024 | 14 | 29.7% | 26.9% | 0.0% | 61.5% | 63.2 |
| 2025 | 12 | **23.1%** | 23.1% | 0.0% | 46.2% | 79.3 |
| 2026 | 14 | 25.8% | 26.9% | 7.7% | 46.2% | 73.5 |

**Read this as: roughly two thirds of a finished roster was never drafted by
that team.** Four of thirteen drafted players is a good season. One team in
2025 finished with **zero** of its drafted players.

The `max` column is the interesting one. The best retention ever recorded is
92.3% — 12 of 13 — and it has not been approached since 2023. Recent maxima sit
at 46.2%, meaning **not one team in 2025 or 2026 has finished with even half its
draft intact.**

### Cross-validation

Computed independently, player-by-player rather than team-by-team, the same
figure falls out:

| Season | Per-team | Per-player |
|---|---|---|
| 2019 | 29.2% | 29.2% |
| 2020 | 46.2% | 46.2% |
| 2025 | 23.1% | 23.1% |
| 2026 | 25.8% | 26.0% |

Two methods, same answer. The instrument is measuring what it claims to.

---

## 2. When does the roster stop resembling the draft?

Median point in the season at which retention drops below a threshold and
**never recovers**. Parentheses show how many teams ever crossed — a team still
above the threshold at season end simply never crossed it.

| Season | 75% of draft | 50% of draft | 25% of draft |
|---|---|---|---|
| 2019 | 15% (10/10) | 53% (9/10) | 93% (4/10) |
| 2020 | 6% (10/10) | 34% (7/10) | 52% (1/10) |
| 2021 | 10% (10/10) | 57% (9/10) | 90% (3/10) |
| 2022 | 29% (12/12) | 75% (10/12) | 74% (1/12) |
| 2023 | 16% (13/16) | 91% (8/16) | 99% (2/16) |
| 2024 | 10% (14/14) | 44% (12/14) | 64% (7/14) |
| 2025 | 10% (12/12) | 66% (12/12) | 93% (10/12) |
| 2026 | 8% (14/14) | 30% (14/14) | 78% (7/14) |

**The answer: the draft is meaningfully damaged almost immediately.**

- **Every single team in every season** — 100/100 where measured — has lost 25%
  of its draft within the first ~8–29% of the season. Usually inside the first
  six weeks.
- Half the draft is typically gone by roughly the **midpoint** (median 30–91%
  of the season elapsed; 2026 and 2020 are the fastest at 30% and 34%).
- The 25% threshold is where seasons genuinely diverge. In 2023 only 2 of 16
  teams ever fell below a quarter of their draft. In 2025, **10 of 12** did.

The line that matters for planning: **by around the quarter mark, a quarter of
your draft is already gone, and by midseason you have lost about half.** Your
draft is not a season-long asset. It is roughly a first-half asset.

---

## 3. Does it vary by owner?

Yes, substantially — but read the caveats before drawing conclusions.

**Owners with enough history to judge (3+ seasons):**

| Owner | Seasons | End retention | Range | Players/szn |
|---|---|---|---|---|
| Anthony Turner | 6 | 57.7% | 46–69% | 30.2 |
| Brandon Draper | 6 | 43.6% | 31–54% | 59.5 |
| Anthony Demetriou | 5 | 41.5% | 23–54% | 56.8 |
| Yohan Udunuwara | 5 | 40.0% | 15–54% | 60.0 |
| Malachi Chadwick | 3 | 38.5% | 38–39% | 56.7 |
| Juan Vergara | 3 | 38.5% | 15–54% | 63.3 |
| Keegan D | 3 | 33.3% | 23–46% | 58.7 |
| allen vega | 3 | 30.8% | 15–46% | 67.0 |
| **Patrick McDowell** | **3** | **30.8%** | 23–39% | **86.0** |
| Tom Foley | 4 | 28.8% | 23–46% | 74.0 |
| Tom Walker | 4 | 28.8% | 15–39% | 69.8 |
| Derrick Anderson | 3 | 25.6% | 15–39% | 66.3 |
| Darnell Odom | **8** | **18.3%** | 0–46% | 77.1 |

**The spread is real: 18.3% to 57.7% across long-tenured owners — a 3× gap.**

Two archetypes emerge, and they are genuinely different strategies rather than
just different luck:

- **Anthony Turner** (6 seasons) retains 57.7% of his draft while using only
  **30 players a season** — roughly half the league norm. Maximum stability;
  picks, and sticks.
- **Darnell Odom** (8 seasons, the only owner present all eight years) retains
  18.3% and turns over **77 players a season**. Almost no attachment to his own
  draft; he is effectively redrafting in-season.

**Patrick** sits at 30.8% retention, 86 players used — one of the highest churn
rates among multi-season owners, so he is already firmly in the "in-season is
where I do my work" camp, and he pays for it in draft retention: only 3 owners
run hotter.

**Caveats, stated plainly:**

1. ~24 of the listed owners have only 1 season, which cannot separate skill from
   a single good or bad year. They're marked `*` in the script output.
2. Some retention is forced, not chosen — injuries drive drops. High churn is
   not automatically a strategy; it can be damage control.
- **Owners are identified by ESPN GUID, not by name or handle.** `display_name`
  is an ESPN handle that is neither unique (two different owners are both named
  "Anthony Turner") nor stable, so it is never used as a key. Reports show the
  real name with the handle where it helps a reader recognise someone; the
  auto-generated 16-character `ESPNFAN<n>` handles carry no information and are
  dropped. The CSV keeps both the label and the GUID.
- **5 teams have co-owners**; those rosters are attributed to the primary owner
  and flagged (`co` column), so a co-managed team's number is the team's, not one
  person's.

---

## 4. Does draft spend predict finishing position?

**Essentially no.** Pearson correlation between total auction spend and final
standing, by season:

| Season | Correlation |
|---|---|
| 2019 | +0.171 |
| 2020 | +0.252 |
| 2021 | +0.027 |
| 2022 | +0.177 |
| 2023 | **−0.390** |
| 2024 | −0.093 |
| 2025 | −0.169 |
| 2026 | −0.271 |

Negative correlation between spend and final standing means *higher spend →
better finish* (standing 1 is best). So:

- Only **2019, 2020, 2021 and 2022** show the intuitively correct sign, and even
  there the effect is weak (r ≈ 0.03–0.25).
- **2023 through 2026 — the four most recent seasons — all have the wrong
  sign.** More money spent at auction is associated with a *worse* finish, and
  2023's −0.39 is the largest effect in the dataset.
- Explained variance is ≤16% in every season, and ≤7% in four of eight.

Even taken at face value, draft spend explains almost nothing about where you
finish. And in the recent era it's mildly anti-predictive.

---

## 5. What this means for the draft-vs-waivers question

Your instinct is directionally right, but I'd sharpen it rather than accept the
strong version.

**Supported:**

- Roughly **two thirds of a finished roster is not from the draft.** The draft
  cannot be the main driver of outcomes because it isn't the main input.
- **Draft spend barely predicts finish**, and in the last four seasons it's
  anti-predictive.
- The draft degrades fast — a quarter gone in the first weeks, half gone by
  midseason. Whatever edge a draft gives you, it has to pay off early.
- Churn strategy varies 3× across owners, so there is real strategic room here.

**Where I'd push back:**

- The draft determines your **starting asset base**. Those players are also the
  *trade and drop currency* you use in-season — you can't churn well from a bad
  base. A weak draft limits your in-season options even if it doesn't directly
  cost you standings.
- Retention falling season over season (51.9% → 23%) may reflect *league-wide*
  improvement in in-season activity rather than the draft mattering less. If
  everyone got better at waivers, the draft's relative value falls without the
  draft becoming unimportant.
- Correlation over 8–16 teams is **small-sample noise**. These r values are
  suggestive, not decisive; −0.39 on 16 teams is roughly a coin flip with
  seasoning.

**The actionable version:** draft capital should be spent on players you expect
to hold value *or* to be tradeable, not on winning the auction. Optimize the
draft for a strong first-half position and high liquidity — and treat the
in-season acquisition budget as the primary lever, since that's where two
thirds of your final roster comes from.

---

## A correction to this table

The median column originally took the upper of the two middle values. Every
season in this league has an even number of teams, so that biased every
median upward, by as much as 3.9 points. The means, minima and maxima were
unaffected and the conclusions do not change: the 2026 median moves from
30.8% to 26.9%, which is if anything slightly worse for the draft.

## Data notes

- **`daily_lineup_slots` is the instrument, not `roster_slots`.** `roster_slots`
  holds only bookend snapshots for 2019–2024 (period 1, sometimes the last) and
  would understate churn in exactly the years of interest. `daily_lineup_slots`
  is continuous for all eight seasons.
- **Baseline is exact.** This is a redraft league (`keeper_count=0` for all
  eight seasons), and every team holds exactly its 13 drafted players on day 1 —
  verified for all 102 team-seasons. Retention starts at precisely 1.0, so no
  denominator adjustment was needed.
- **Retention is not monotonic.** Players are dropped and re-added. A naive
  "first day below threshold" measures a wobble, not durable change (one 2023
  team walked 13-12-11-12-11-8-7-8-7-8). Reported dates are the last day
  retention held above the threshold.
- **2026 is in progress** (data through scoring period 174), so its end-of-season
  figure is current-state, not final.
- **2026 acquisition mechanism changed** from FREEAGENT to WAIVER labels when
  `uses_faab` flipped to true. Same behavior, different ESPN label — don't read
  the 2026 transaction mix as a behavioral shift.
- Per-team detail: `/tmp/fcp_roster_churn_detail.csv` (written by the script).

**Reproduce:** `cd /opt/fcp-core && set -a && . ./.env && set +a && .venv/bin/python scripts/roster_churn.py`
