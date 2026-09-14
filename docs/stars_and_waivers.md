# Stars and waivers: does in-season management rescue a top-heavy draft?

Full Court Press (ESPN 3853870), 2019-2026. Strategy is the top-3 share
of draft spend; pickup share is the share of started nine-cat production
from players the team did not draft. Report only; nothing was changed.

## 1. Five-line summary

- **Balanced drafts beat top-heavy on the regular season, and heavy pickup use does not close the gap.** Balanced 0.531 against top-heavy 0.480 category win rate. Confidence: **strong** (n=98, and the sign holds in 6 of 8 seasons).
- **The bootstrap over seasons puts the balanced-minus-top-heavy difference at +0.050 (95% CI +0.023 to +0.079).** The interval excludes zero. Confidence: **strong**.
- **Top-heavy teams rely on pickups more but do not work the wire harder.** Their pickup share is higher (49.2% against 34.7%) while they make slightly FEWER adds (71.4 against 73.3). The reliance is a consequence of the draft, not extra effort. Confidence: **descriptive only** -- a bad draft forces pickups, so the causation runs both ways.
- **Injuries deepen top-heavy's losses but do not explain them.** Top-heavy loses even when its stars are healthy: with availability above 0.90 it scores 0.503 against 0.510 for balanced teams whose stars MISSED time. Star availability is a second penalty on an already worse baseline, not the cause. Confidence: **suggestive** (cells of 3-9).
- **Trades are real but small: 130 reconstructed trades across 47 of 98 team-seasons, supplying 4.2% of production.** They cannot be the mechanism that rescues a top-heavy draft. Confidence: **strong on the magnitude, suggestive on the exact count**, because the reconstruction is a lower bound.

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

### Do top-heavy teams trade more?

Trades are reconstructed from roster movement (see the module docstring); ESPN's transaction tables do not carry most of them.

| strategy | n | trades (mean) | teams with >=1 trade | trade share of production |
|---|---|---|---|---|
| Q1 balanced | 27 | 1.48 | 13/27 | 4.1% |
| Q2 | 22 | 1.68 | 12/22 | 5.4% |
| Q3 | 22 | 0.95 | 10/22 | 2.2% |
| Q4 top-heavy | 27 | 1.19 | 12/27 | 5.1% |

League-wide: 130 reconstructed trades across 47 of 98 team-seasons, supplying 4.2% of production on average. Trades are rare and small relative to the wire, so they cannot be the mechanism that makes a top-heavy draft work.

### Waiver vs trade split of pickup production

| strategy | n | wire share of production | trade share of production | other pickup share |
|---|---|---|---|---|
| Q1 balanced | 27 | 27.0% | 4.1% | 3.6% |
| Q2 | 22 | 30.6% | 5.4% | 4.0% |
| Q3 | 22 | 33.3% | 2.2% | 3.9% |
| Q4 top-heavy | 27 | 40.0% | 5.1% | 4.1% |

Pickup production splits into three parts: players the team added via a waiver or free-agent transaction (wire), players reconstructed as arriving by trade, and a remainder -- a player dropped and re-added, or one whose move the reconstruction could not attribute. The remainder is small and is shown rather than hidden.

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
explained, so a bad draft this season cannot cause it. It does not
rescue top-heavy: top-heavy teams with above-median prior skill score
0.479 (n=6) against 0.497 (n=8) for those below it -- the wrong sign,
and within noise. Balanced teams show the same non-effect (0.543 above
against 0.563 below), which suggests the prior-season measure is too
noisy at these counts to separate managers at all. The honest reading
is that this test found no skill effect in either direction, not that
skill is absent.

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

**Reading.** Injuries deepen top-heavy's loss but do NOT explain it.

- Top-heavy with healthy stars (0.503, n=4) is still below balanced with stars that missed time (0.510, n=13). It loses even when its stars play.
- Missing stars costs top-heavy 0.073 (0.503 to 0.430) and balanced 0.066 (0.576 to 0.510); the two penalties are of similar size, and the balanced healthy band is only n=3, so they should not be read as different from each other.
- So star availability is not what separates the strategies. It is a second, separate penalty on top of an already worse baseline.

These cells are small (single digits in the healthy band), so the levels are suggestive rather than firm; the ORDERING is consistent with the pooled result in section 2.


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

### How long each stays rostered, and when he is dropped

Held = in the team's daily lineup in ANY slot, so a benched player is still rostered. A dropped player is one whose last roster day falls before the end of the matchup period named, so the column reads "gone before period 4 finished".

| pick price | n | never held | median last roster day | dropped by end of MP4 | dropped by end of MP8 |
|---|---|---|---|---|---|
| $1-2 | 413 | 13% | 50 | 49% | 58% |
| $3-5 | 177 | 6% | 72 | 37% | 48% |
| $6-9 | 124 | 2% | 110 | 21% | 35% |
| $10-25 | 271 | 1% | 139 | 13% | 23% |
| $26+ | 289 | 2% | 146 | 8% | 11% |

**Reading.** Cheap picks are not merely worse than expensive ones; they behave like waiver claims. 13% of $1-2 picks are never held in any slot at all, and 49% are gone by the end of matchup period 4 against 8% of $26+ picks. A $1-2 pick is a lottery ticket you drop within a month, which is exactly how the wire is used.

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

**Treat the size trend as suggestive, not established.** There are
four sizes, and the 16-team row rests on a single season (n=4 per
cell); the direction is consistent but the magnitude at 16 teams is
not something this data can pin down.

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

| season | team | teams | strategy | top3 share | pickup share | adds | prior-season net per add | star avail | finish | played | title |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | Through The Wire | 14 | Q2 | 60.0% | 52.9% | 96 | n/a | 0.877 | 1 | yes | YES |
| 2025 | Through The Wire | 12 | Q1 balanced | 52.5% | 37.7% | 87 | 0.906 | 0.732 | 3 | yes |  |
| 2026 | Through The Wire | 14 | Q1 balanced | 46.0% | 44.3% | 113 | 0.440 | 0.663 | 2 | yes |  |

prior-season net per add is the owner's mean net composite per acquisition in the season BEFORE, from the acquirable_value method (added player minus dropped player over 14 periods). It is a level, not a rank. 'n/a' means the owner has no prior season in the data.

## 12. What this means for a 15-team $200 draft

**Supported by the evidence.**

- Prefer balanced. The sign is consistent across seasons and the
  per-season differences agree with the pooled means.
- Do not expect the wire to fix a top-heavy draft. The extra adds are
  real but the outcomes are not better.
- Going top-heavy costs you baseline performance, not just downside:
  healthy top-heavy teams still finish behind balanced teams whose
  stars missed time. Missing stars is a further penalty on top.
- Trades are not a rescue route -- the league trades rarely and for
  little production (see section 3).

**Thin evidence, listed separately.**

- Every strategy x pickup cell. n is under 10 almost everywhere.
- The prior-season skill split: owner-seasons with both a prior and a
  current season are few.
- Playoff and title rates: the playoff sample is a subset and titles are
  single digits per strategy.
- 2026 alone: one season, and the only FAAB season.
- Trades: the reconstruction recovers moves, not deals, and is a
  lower bound. A plan built on trading two-for-ones cannot be checked
  precisely, though the league-wide trade share is small enough that
  the direction of the conclusion is unaffected.

## 13. Traps and data problems hit

- `transaction_items` for trades are almost all missing (27 rows with a
  `to_team_id` in eight seasons), so trades are reconstructed from
  roster movement instead. The waiver exclusion is load-bearing:
  without it 2026 shows 184 moves, because pickups are also moves.
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

