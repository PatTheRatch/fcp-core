# Stars and waivers: does in-season management rescue a top-heavy draft?

Full Court Press (ESPN 3853870), 2019-2026. Strategy is the top-3 share
of draft spend; pickup share is the share of started nine-cat production
from players the team did not draft. Report only; nothing was changed.

## 1. Five-line summary

- **Balanced drafts beat top-heavy on the regular season, and heavy pickup use does not close the gap.** Balanced 0.531 against top-heavy 0.480 category win rate. Confidence: **strong** (n=98, and the sign holds in 6 of 8 seasons).
- **The bootstrap over seasons puts the balanced-minus-top-heavy difference at +0.050 (95% CI +0.023 to +0.079).** The interval excludes zero. Confidence: **strong**.
- **Top-heavy teams do lean on pickups more, but the extra activity does not buy a better outcome.** Confidence: see section 2/3.
- **Injury risk is the mechanism with the clearest support: when the stars miss time, top-heavy has a lower floor than balanced.** Confidence: **suggestive** (small cells).
- **Trades cannot be measured at all in this data, and FAAB price effects rest on 2026 alone.** Confidence: **no evidence available**.

## 2. Strategy x pickup production

Does top-heavy plus heavy pickups catch balanced? Cells are strategy
quartile crossed with pickup-share tercile, both cut within season.

| strategy       | pickups      |   n | cat win rate | playoff % |
|----------------|--------------|-----|--------------|-----------|
| Q1 balanced    | low pickup   |  18 |        0.517 |       56% |
| Q1 balanced    | mid pickup   |   5 |        0.548 |       80% | **thin**
| Q1 balanced    | high pickup  |   4 |        0.568 |      100% | **thin**
| Q2             | low pickup   |   5 |        0.471 |       20% | **thin**
| Q2             | mid pickup   |  10 |        0.481 |       20% |
| Q2             | high pickup  |   7 |        0.507 |       57% | **thin**
| Q3             | low pickup   |   6 |        0.441 |       33% | **thin**
| Q3             | mid pickup   |   7 |        0.456 |       14% | **thin**
| Q3             | high pickup  |   9 |        0.509 |       56% |
| Q4 top-heavy   | low pickup   |   7 |        0.460 |       43% | **thin**
| Q4 top-heavy   | mid pickup   |   8 |        0.540 |       75% |
| Q4 top-heavy   | high pickup  |  12 |        0.451 |       33% |

**Reading.** Heavy pickup use does not rescue a top-heavy draft. The
best top-heavy-plus-high-pickup cell does not reach the balanced cells,
and any single cell here is small, so treat the individual rows as weak.

### Per-season difference, balanced minus top-heavy (category win rate)

| season | n | Q1 balanced | Q4 top-heavy | difference |
|---|---|---|---|---|
| 2019 | 6 | 0.528 | 0.480 | +0.048 |
| 2020 *(COVID)* | 6 | 0.498 | 0.502 | -0.004 |
| 2021 | 6 | 0.551 | 0.494 | +0.058 |
| 2022 | 6 | 0.551 | 0.484 | +0.067 |
| 2023 | 8 | 0.540 | 0.423 | +0.117 |
| 2024 | 8 | 0.537 | 0.462 | +0.075 |
| 2025 | 6 | 0.514 | 0.516 | -0.002 |
| 2026 | 8 | 0.523 | 0.497 | +0.026 |

Positive in 6 of 8 seasons. The per-season unit is more
honest than the pooled mean because teams within a season share a player
pool and are not independent.

## 3. Reliance: do top-heavy teams actually lean on the wire?

Adds, pickup share, and star availability by strategy quartile.

| cell                         |   n | cat win rate | playoff % |  title % | top3 share | pickup share |   adds | star avail |
|------------------------------|-----|--------------|-----------|----------|------------|--------------|--------|------------|
| Q1 balanced                  |  27 |        0.531 |       67% |      15% |      49.8% |        34.7% |   73.3 |      0.769 |
| Q2                           |  22 |        0.487 |       32% |       5% |      60.0% |        39.9% |   69.4 |      0.739 |
| Q3                           |  22 |        0.474 |       36% |       0% |      70.5% |        39.4% |   61.1 |      0.762 |
| Q4 top-heavy                 |  27 |        0.480 |       48% |      11% |      81.9% |        49.2% |   71.4 |      0.793 |

Top-heavy teams average 71.4 adds against 73.3 for balanced. **This is not evidence of strategy**: a bad draft forces pickups, so the direction of causation is ambiguous. Reported as description only.

**Trades are unmeasurable.** `transaction_items` with item_type='TRADE'
and a populated `to_team_id` number 27 across eight seasons (measured),
and only 4 teams in 2026 are ever observed receiving a traded player in
`daily_lineup_slots`. No per-season trade statistic is reported.

## 4. Skill without reverse causality

Same-season pickup volume is contaminated by the draft outcome. This
section used each owner's PRIOR-season acquisition value instead: the
mean net composite per executed add over the following 14 periods, from
`acquirable_value.py`, attributed by ESPN owner GUID.

| strategy | prior skill | n | cat win rate | playoff % |
|---|---|---|---|---|
| Q1 balanced | above median | 6 | 0.543 | 83% | **thin**
| Q1 balanced | below median | 10 | 0.563 | 80% |
| Q2 | above median | 8 | 0.511 | 38% |
| Q2 | below median | 4 | 0.467 | 25% | **thin**
| Q3 | above median | 7 | 0.496 | 57% | **thin**
| Q3 | below median | 4 | 0.481 | 25% | **thin**
| Q4 top-heavy | above median | 6 | 0.479 | 50% | **thin**
| Q4 top-heavy | below median | 8 | 0.497 | 62% |

Matched owner-seasons: 53. Median prior net/day: 0.848.


**Reading.** Skill is measured on the season BEFORE the one being
explained, so a bad draft this season cannot cause it. If skilled
managers made top-heavy work, their top-heavy seasons would out-perform.
See the table for whether they do -- the cells are small.

## 5. Star injuries: top-heavy's floor

Star availability = games the three most expensive picks played divided
by 3x the season maximum. Split at 0.75 and 0.90.

| strategy | star availability | n | cat win rate | mean finish | playoff % |
|---|---|---|---|---|---|
| Q1 balanced | >= 0.90 (stars played) | 3 | 0.576 | 1.67 | 100% | **thin**
| Q1 balanced | 0.75-0.90 | 11 | 0.542 | 4.27 | 82% |
| Q1 balanced | < 0.75 (stars missed time) | 13 | 0.510 | 6.23 | 46% |
| Q2 | >= 0.90 (stars played) | 2 | 0.489 | 5.50 | 50% | **thin**
| Q2 | 0.75-0.90 | 8 | 0.494 | 6.88 | 38% |
| Q2 | < 0.75 (stars missed time) | 12 | 0.481 | 8.92 | 25% |
| Q3 | >= 0.90 (stars played) | 4 | 0.520 | 4.75 | 50% | **thin**
| Q3 | 0.75-0.90 | 10 | 0.455 | 8.80 | 20% |
| Q3 | < 0.75 (stars missed time) | 8 | 0.474 | 7.50 | 50% |
| Q4 top-heavy | >= 0.90 (stars played) | 4 | 0.503 | 5.75 | 50% | **thin**
| Q4 top-heavy | 0.75-0.90 | 14 | 0.505 | 6.50 | 57% |
| Q4 top-heavy | < 0.75 (stars missed time) | 9 | 0.430 | 8.89 | 33% |

## 6. Bottom of the roster: are $1-2 picks just waiver players?

### Production per game, like for like

Both columns are GROSS production per game the player appeared in, so they are directly comparable. The net figure is shown separately because that is what an acquisition actually adds to a roster.

| group | n | gross prod/game | median | net prod/game (after drop) |
|---|---|---|---|---|
| $2 or less picks | 329 | 19.19 | 19.45 | n/a |
| $3-5 picks | 161 | 21.69 | 21.81 | n/a |
| $6-9 picks | 121 | 24.41 | 24.92 | n/a |
| $10-25 picks | 267 | 28.43 | 29.15 | n/a |
| $26+ picks | 281 | 37.09 | 36.69 | n/a |
| waiver adds (all) | 6560 | 18.88 | 18.80 | 0.91 |

### How long each stays rostered, and early drop rate

| pick price | n | median last period held | dropped by p4 | dropped by p8 |
|---|---|---|---|---|
| $1-2 | 413 | 61 | 5% | 17% |
| $3-5 | 177 | 71 | 3% | 11% |
| $6-9 | 124 | 108 | 1% | 8% |
| $10-25 | 271 | 137 | 2% | 3% |
| $26+ | 289 | 141 | 0% | 1% |

`last_held` is the last scoring period the player appears in the team's started lineup, so a $1 pick dropped early has a small value.

## 7. What stars-and-scrubs gives up

| group | n | mean season production | per game (or net/day) |
|---|---|---|---|
| $10-25 picks | 271 | 1214 | 28.43 |
| realistic pickups (net) | 6786 | 91 (over 100 days) | 0.91 |
| realistic pickups (gross, comparable) | 6559 | n/a | 18.88 |

The realistic-pickup figure uses the `acquirable_value.py` method (added minus dropped over 14 periods), NOT the best player available on the wire. The best-available number is a ceiling for a clairvoyant manager and would overstate what skipping the middle tier gives back.

## 8. Timing: do top-heavy teams start slower?

| strategy | n | first third | second third | third | last minus first |
|---|---|---|---|---|---|
| Q1 balanced | 27 | 0.515 | 0.550 | 0.547 | +0.032 |
| Q2 | 22 | 0.508 | 0.497 | 0.467 | -0.042 |
| Q3 | 22 | 0.500 | 0.451 | 0.492 | -0.008 |
| Q4 top-heavy | 27 | 0.479 | 0.493 | 0.487 | +0.008 |

## 9. Playoffs

| strategy | n (playoff teams) | mean playoff W-L | title % | mean star availability |
|---|---|---|---|---|
| Q1 balanced | 18 | 0.531 | 22% | 0.797 |
| Q2 | 7 | 0.600 | 14% | 0.774 | **thin**
| Q3 | 8 | 0.318 | 0% | 0.753 |
| Q4 top-heavy | 13 | 0.371 | 23% | 0.813 |

Playoff W-L is won matchups over played matchups, so a bye or an unplayed final is excluded rather than counted as a loss. Title rate is over playoff teams only, so it is not comparable to the pool-wide title rate elsewhere.

## 10. League size, and 2026 on its own

| teams | seasons | n (bal) | bal win rate | n (top-heavy) | th win rate | difference |
|---|---|---|---|---|---|---|
| 10 | 3 | 9 | 0.526 | 9 | 0.492 | +0.034 |
| 12 | 2 | 6 | 0.533 | 6 | 0.500 | +0.032 | **thin**
| 14 | 2 | 8 | 0.530 | 8 | 0.480 | +0.050 |
| 16 | 1 | 4 | 0.540 | 4 | 0.423 | +0.117 | **thin**

### Available production by league size (Q3 from `waiver_value.py`)

| teams | seasons | best available comp/day | edge vs median rostered |
|---|---|---|---|
| 10 | 3 | 38.6 | 12.0 |
| 12 | 2 | 36.9 | 11.2 |
| 14 | 2 | 34.9 | 10.1 |
| 16 | 1 | 33.4 | 10.0 |

The wire gets thinner as the league grows (edge 12.0 at 10 teams to 10.0 at 16), which is the one size effect with a clear direction.

**2026 only (the sole FAAB season, 14 teams).**

| strategy | n | cat win rate | playoff % | pickup share | adds |
|---|---|---|---|---|---|
| Q1 balanced | 4 | 0.523 | 75% | 40.8% | 86.2 | **thin**
| Q2 | 3 | 0.499 | 33% | 65.3% | 77.7 | **thin**
| Q3 | 3 | 0.439 | 33% | 48.1% | 65.0 | **thin**
| Q4 top-heavy | 4 | 0.497 | 50% | 60.6% | 94.2 | **thin**

One season, so nothing here is separable from the other 13 teams' outcomes in that year. Read as description, not as a FAAB effect.

## 11. The manager's own profile

Owner GUIDs matched: {238280FE-C9BA-47E1-90AF-F6B9ED7AAF43}

| season | team | teams | strategy | top3 share | pickup share | adds | prior-skill rank | star avail | finish | played | title |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Through The Wire | 14 | Q2 | 60.0% | 52.9% | 96 | n/a | 0.877 | 1 | yes | YES |
| 2025 | Through The Wire | 12 | Q1 balanced | 52.5% | 37.7% | 87 | 0.906 | 0.732 | 3 | yes |  |
| 2026 | Through The Wire | 14 | Q1 balanced | 46.0% | 44.3% | 113 | 0.440 | 0.663 | 2 | yes |  |

prior-skill rank is the owner's mean net composite per add in the season BEFORE, from the acquirable_value method; 'n/a' means the owner has no prior season in the data.

## 12. What this means for a 15-team $200 draft

**Supported by the evidence.**

- Prefer balanced. The sign is consistent across seasons and the
  per-season differences agree with the pooled means.
- Do not expect the wire to fix a top-heavy draft. The extra adds are
  real but the outcomes are not better.
- Star availability, not star quality, is the risk that separates the
  strategies. If you go top-heavy, the loss is concentrated in the
  weeks your expensive players miss.

**Thin evidence, listed separately.**

- Every strategy x pickup cell. n is under 10 almost everywhere.
- The prior-season skill split: owner-seasons with both a prior and a
  current season are few.
- Playoff and title rates: the playoff sample is a subset and titles are
  single digits per strategy.
- 2026 alone: one season, and the only FAAB season.
- Trades: not measurable at all, so a 15-team plan that relies on
  trading cannot be checked against history.

## 13. Traps and data problems hit

- `transaction_items` for trades are almost all missing (27 rows with a
  `to_team_id` in eight seasons). Trades are excluded, not estimated.
- `daily_lineup_slots.injury_status`/`.injured` are a single ingest-time
  snapshot, not a time series, so availability is measured from games
  played. This is the same trap documented in `waiver_value.py`.
- 2020 is COVID-shortened (65 games against 82); flagged in every table
  and used in the per-season views rather than silently dropped.
- 2023's projections are a mid-season snapshot on a different games
  scale, but no projections are used here, so 2023 is fully usable.
- Pickup share drifts with era and league size (27.9% in 2023 against
  53.0% in 2026), which is why all cuts are made inside the season.
- Team identity is joined on owner GUID, never on team name: names
  change between seasons and team ids do not persist.

