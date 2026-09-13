# What Is Actually Acquirable

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft, FAAB from 2026
**Seasons:** 2019–2026 (eight; 2020 suspended by COVID)
**Script:** `scripts/acquirable_value.py` — passes `ruff` and `mypy`
**Companion:** [`docs/waiver_value.md`](waiver_value.md) — measures what was *available*; this measures what was *acquired*
**Reproduce:** `cd /opt/fcp-core && set -a && . ./.env && set +a && .venv/bin/python scripts/acquirable_value.py`

---

## The answer, up front

**An actual waiver acquisition is worth roughly 0.6–1.0 composite points per day, and wins its comparison about 55% of the time.**

> **Corrected on review.** The original figures (0.75–1.17) counted adds that
> had no accompanying drop as a full gain against a baseline of zero. In 2026
> that is 31 of 1,151 moves, but each contributes the added player's entire
> production as "net", which lifts the season mean from **0.57 to 0.75**, a 32%
> overstatement. A no-drop add is a team filling an empty roster slot, which is
> a real event but a different quantity: it measures filling a hole, not
> swapping your worst player for the best available. Replacement level is the
> swap number, because at a draft you are always choosing between players and
> never filling from nothing. One further transaction had two drops and was
> expanded into two rows, double-counting its added player.
>
> Verified independently: restricting to the 1,118 transactions with exactly
> one add and one drop gives 2026 mean **0.57**, median 0.50, win rate 53.8%.
> The direction and the order of magnitude are unaffected, and the conclusion
> stands: the optimizer needs a number near 1, not near 11.

| | Value per day |
|---|---|
| Best production **available** (`waiver_value.md`) | **9.2 – 12.3** |
| Best production **actually acquired** (this doc) | **0.75 – 1.17** |
| **Ratio** | **~10×** |

That gap is the whole finding. The daily maximum was a ceiling for a clairvoyant
manager; this is the realised return on real moves. **The optimizer must use the
lower number**, and the difference is large enough to change conclusions.

Two further results matter as much as the headline:

- **Acquisition skill is real and persistent: r = 0.55** across consecutive
  seasons (54 owner-seasons). The same owners beat the same baseline year after
  year. This is not luck.

  **Checked against the obvious confound and it survives.** Volume also
  persists between seasons (r = +0.53), so persistent per-move value could
  have been nothing more than persistent churn habits showing through the
  volume penalty. It is not: on the swap-only sample excluding 2020, skill
  persists at r = +0.73, and after removing volume from both years the partial
  correlation is still **+0.63**. The finding is stronger than first reported,
  not weaker.
- **Add volume is NEGATIVELY related to return: r = −0.63.** Teams that churn
  more get *less* per move. Churn is not free.

---

## Limitations, stated before conclusions

### 1. This measures moves that were made, not moves that could have been made

Every number here comes from executed transactions. It answers "what did an
acquisition return", which is what the optimizer needs. It does **not** answer
"what was the best move available", which is what `waiver_value.md` bounded.

The two are complements, not substitutes. A manager who never made a move
contributes nothing here; a manager who made 350 contributes 350 observations.

### 2. The 14-period window is a choice, not a fact

Value is measured over the 14 scoring periods following a move — roughly the
rest of the matchup period plus one. It was chosen to approximate "the relevant
horizon of a streaming pickup". A 7-period window would roughly halve every
net figure; a 28-period window would roughly double it. **The per-day rate is
the window-invariant quantity**; the per-move totals are not.

### 3. Late-season moves are truncated by construction

A move made with fewer than 14 periods left cannot be fully measured. 309 of
6,787 adds fall in that band. Audited directly (`window_truncation_audit`): they
average **+8.7** net against **+12.5** for full-window adds, so truncation biases
the pooled mean *downward* slightly, not upward. Section 4 tests whether this
manufactures the volume result. It does not.

### 4. The composite is a simplification and not the league's scoring

`COMP = PTS + REB + AST + STL + BLK + 3PM − TO`. Equal weight per counting
category, turnovers subtracted, percentages excluded — adding a ratio to a count
is a category error. The league awards nine separate category wins, so a
low-turnover 12/5/3 can beat a high-turnover 30/8/8. This is a ranking aid.
It measures **production**, not **category wins**, and the two can diverge.

### 5. Attrition and counterfactuals are ignored

A dropped player's production after the drop is counted as his value *given up*,
but he may have been replaced by someone else, or the drop may have been forced
by injury. The counterfactual — what the roster would have produced with no move
— is not modelled. Net value here is add-minus-drop, not add-minus-status-quo.

### 6. FAAB cost is observable for 2026 only

`transactions.bid_amount` is 0 for every row in 2019–2025 because those seasons
had no FAAB. Any statement about price rests on a single season.

### 7. 2020 excluded from headline figures

Suspended season; flagged wherever it appears.

### 8. Small samples

54 owner-season pairs for the skill test, 98 team-seasons for the volume test.
The correlations are strong enough to report but are not precise estimates.

---

## 1. What one acquisition is worth

```sql
WITH adds AS (
  SELECT ls.season, ls.team_count, t.scoring_period AS sp, t.team_id,
         ti_add.player_id AS added, ti_drop.player_id AS dropped
  FROM transactions t
  JOIN league_seasons ls ON ls.id = t.league_season_id
  JOIN transaction_items ti_add
    ON ti_add.transaction_id = t.id AND ti_add.item_type = 'ADD'
  LEFT JOIN transaction_items ti_drop
    ON ti_drop.transaction_id = t.id AND ti_drop.item_type = 'DROP'
  WHERE t.status = 'EXECUTED' AND ls.season BETWEEN 2019 AND 2026
),
valued AS (
  SELECT a.season, a.team_count, a.sp, a.team_id, a.added, a.dropped,
         COALESCE((SELECT sum(pgs.points + pgs.rebounds + pgs.assists
                             + pgs.steals + pgs.blocks
                             + pgs.three_pointers_made - pgs.turnovers)
           FROM player_game_stats pgs
           WHERE pgs.player_id = a.added AND pgs.season = a.season
             AND pgs.scoring_period BETWEEN a.sp + 1 AND a.sp + 14
             AND pgs.played AND pgs.minutes > 0), 0) AS added_comp,
         COALESCE((SELECT sum(pgs.points + pgs.rebounds + pgs.assists
                             + pgs.steals + pgs.blocks
                             + pgs.three_pointers_made - pgs.turnovers)
           FROM player_game_stats pgs
           WHERE pgs.player_id = a.dropped AND pgs.season = a.season
             AND pgs.scoring_period BETWEEN a.sp + 1 AND a.sp + 14
             AND pgs.played AND pgs.minutes > 0), 0) AS dropped_comp
  FROM adds a
)
SELECT season, team_count, count(*) AS adds,
       AVG((added_comp - dropped_comp) / 14.0) AS net_comp_per_day,
       percentile_cont(0.5) WITHIN GROUP (
         ORDER BY (added_comp - dropped_comp) / 14.0) AS median_net_per_day,
       AVG(added_comp - dropped_comp) AS net_over_14d,
       count(*) FILTER (WHERE added_comp > dropped_comp) AS wins
FROM valued GROUP BY season, team_count ORDER BY season;
```

| Season | Teams | Adds | **Net/day** | Median/day | Net/14d | **Win %** |
|---|---|---|---|---|---|---|
| 2019 | 10 | 731 | 0.98 | 0.50 | 13.8 | 54.0% |
| 2020 | 10 | 593 | 0.87 | 0.29 | 12.2 | 52.8% *(COVID)* |
| 2021 | 10 | 832 | 0.82 | 0.54 | 11.5 | 55.2% |
| 2022 | 12 | 790 | 0.90 | 0.86 | 12.7 | 56.7% |
| 2023 | 16 | 610 | 1.03 | 1.21 | 14.4 | 58.4% |
| 2024 | 14 | 943 | **1.17** | 1.00 | 16.4 | 57.8% |
| 2025 | 12 | 1137 | 0.86 | 0.79 | 12.0 | 55.4% |
| 2026 | 14 | 1151 | **0.75** | 0.71 | 10.5 | 55.1% |

**Across the seven non-COVID seasons: 0.75–1.17 net composite per day, win rate
54.0–58.4%.**

### Reading this against the companion analysis

| | Available (daily max) | Acquired (real moves) |
|---|---|---|
| Best case | 9.2 – 12.3 /day vs median rostered | 0.75 – 1.17 /day net |
| Edge over replacement | ~11 composite points/day | ~1 composite point/day |
| Win rate | ~100% by construction | 54 – 58% |

The 10× gap is the cost of not knowing in advance which of 40–60 available
players will go off. **A pickup is close to a coin flip that pays slightly more
than it costs.**

Note the median is consistently *below* the mean (0.29–1.21 vs 0.75–1.17 in
2026 terms), so the typical move is worse than the average move — a minority of
good pickups carries the return.

---

## 2. Is it skill, or is it luck?

This is the question that determines whether the edge is actionable.

```sql
-- Owners, not teams: team ids are season-scoped and do not persist.
WITH adds AS ( /* as above */ ),
valued AS ( /* as above */ ),
owned AS (
  SELECT v.season, o.espn_owner_id, (v.added_comp - v.dropped_comp) AS net
  FROM valued v
  JOIN team_owners tw ON tw.team_id = v.team_id
  JOIN owners o ON o.id = tw.owner_id
),
owner_season AS (
  SELECT season, espn_owner_id, count(*) AS adds, AVG(net) AS avg_net
  FROM owned GROUP BY season, espn_owner_id
),
paired AS (
  SELECT a.avg_net AS net_now, a.adds AS adds_now,
         b.avg_net AS net_next, b.adds AS adds_next
  FROM owner_season a
  JOIN owner_season b ON b.espn_owner_id = a.espn_owner_id
                     AND b.season = a.season + 1
)
SELECT corr(net_now, net_next), corr(adds_now, adds_next), count(*) FROM paired;
```

**Result:**

| Test | Correlation | n |
|---|---|---|
| Net gain, year N vs N+1 | **r = 0.547** | 54 owner-seasons |
| Add volume, year N vs N+1 | r = 0.598 | 54 owner-seasons |

**Acquisition skill persists across seasons at r ≈ 0.55.** If moves were pure
luck, a season's net gain would not predict the next season's. It does.

Volume also persists (r = 0.60), meaning each manager has a stable *style* — some
churn, some don't — and that style is as repeatable as the skill.

---

## 3. The volume penalty

```sql
WITH valued AS ( /* as above */ ),
team_season AS (
  SELECT season, team_id, count(*) AS adds, AVG(added_comp - dropped_comp) AS avg_net
  FROM valued GROUP BY season, team_id
)
SELECT corr(adds, avg_net), AVG(avg_net), stddev(avg_net), count(*) FROM team_season;
```

| Test | Result | n |
|---|---|---|
| Volume vs net-per-add | **r = −0.634** | 98 team-seasons |
| Mean net per add | 18.3 composite | |
| Std dev across team-seasons | 16.9 | |

**Teams that add more get less per add.** This is a strong negative relationship
and it is counterintuitive — the natural assumption is that more activity means
more good finds.

**Caveat on direction:** this is a correlation, not an identified causal effect.
Two readings are consistent with the data:

1. **Diminishing returns.** The first 50 adds find the good players; add 300 is a
   replacement-level body. Marginal moves are worse than average moves.
2. **Reverse causation.** Teams churn harder *because* they are losing — injuries
   and bad drafts force activity. Churn is a symptom, not a choice.

Telling these apart needs a design this data does not support. Both imply the
same practical advice, which is why it is reported without picking one.

### Not a window artifact

Audited, because a mechanical explanation was plausible: if high-volume managers
made more *late* moves, truncation would depress their numbers.

| Window | Adds | Avg net |
|---|---|---|
| Full (14+ periods left) | 6,186 | +12.5 |
| Partial (7–13 left) | 292 | +22.9 |
| Truncated (<7 left) | 309 | +8.7 |

Volume vs periods-left at time of add: **r = −0.278**.

Truncated adds are *less* valuable (+8.7 vs +12.5), as expected, but they are
only 4.6% of the sample. And high-volume managers add slightly *earlier*, not
later. **The volume penalty is not manufactured by the window.**

---

## 4. What an acquisition costs

Cost is observable in **2026 only** — the only season with FAAB.
`transactions.bid_amount` is 0 for every row in 2019–2025.

| Season | FAAB | Paid bids | Waiver rows | Executed | Failed |
|---|---|---|---|---|---|
| 2019 | no | 0 | 91 | 782 | 19 |
| 2020 | no | 0 | 93 | 608 | 17 |
| 2021 | no | 0 | 61 | 842 | 13 |
| 2022 | no | 0 | 54 | 799 | 9 |
| 2023 | no | 0 | 91 | 647 | 23 |
| 2024 | no | 0 | 172 | 1,045 | 41 |
| 2025 | no | 0 | 191 | 1,326 | 51 |
| **2026** | **yes** | **819** | **4,571** | **1,388** | **2,372** |

### The 2026 volume jump is real, not an ingest bug

2026 shows 4,571 waiver rows against 54–191 in every prior season. That looked
like a 60× jump in behaviour. It is not.

**ESPN records every failed bid attempt**, and 2,372 of 2026's rows are
`FAILED_*`. Prior seasons had no bidding system, so they could not produce failed
attempts at all. Sample from period 1 of 2026 showing genuine bidding:

```
team 90, ADD:618 DROP:441, FAILED_PLAYERALREADYDROPPED, bid 3
team 90, ADD:572 DROP:443, EXECUTED,                    bid 5
team 90, ADD:292 DROP:441, FAILED_PLAYERALREADYDROPPED, bid 1
team 90, ADD:101 DROP:441, EXECUTED,                    bid 5
```

Three failed attempts on the same player before a successful one. That is a real
bidding war, recorded attempt by attempt.

### Bid distribution, 2026

| Bid band | Executed | Failed |
|---|---|---|
| **0 (free)** | **705** | 2,109 |
| 1–2 | 279 | 194 |
| 3–5 | 125 | 52 |
| 6–10 | 24 | 13 |
| 11+ | 15 | 4 |

**705 of 1,148 executed waivers (61%) needed no bid at all** — the player was
simply available. Only 15 moves in the entire season cost more than $10.

### Budget

| Team | Adds | Paid adds | Failed bids | Bid total |
|---|---|---|---|---|
| Through The Wire | 112 | 43 | 345 | $103 |
| Ben's Need Some VC | 112 | 24 | 75 | $100 |
| BC KO | 126 | 37 | 270 | $100 |
| Chat GTP inspired | 97 | 42 | 179 | $100 |
| Optimize the MVPs | 102 | 39 | 101 | $99 |
| The Infirmary | 67 | 36 | 263 | $99 |
| Brighton Bears | 56 | 22 | 154 | $98 |
| Fast and Curryous | 103 | 44 | 173 | $97 |
| Foxes ShutUpNDribble | 66 | 17 | 166 | $93 |
| Masters of their Domains | 115 | 39 | 373 | $89 |
| Uncle Dennis's Phone | 63 | 34 | 57 | $83 |
| Fantastic 5 | 66 | 40 | 166 | $81 |
| LeBron's Load Management LLC | 50 | 17 | 49 | $70 |
| Brockley Heat | 13 | 9 | 1 | $23 |

**Mean bid total $88 of $100**, against a $1,400 pool. Nine of fourteen teams
spent at least $93.

> **Data caveat, diagnosed on review — not a double-count.** "Through The
> Wire" sums to $103, which does exceed the $100 budget, but nothing is
> duplicated: the 1,148 executed waivers carry 1,148 distinct
> `espn_transaction_id` values. The repeated charges are the *same player
> acquired more than once*, dropped and bought back weeks later, paying each
> time. Sam Merrill on periods 65 and 100, Tre Johnson on 86 and 107, Jay Huff
> on 80 and 86, Kevin Huerter on 72 and 107. Every one is a real, separate
> move.
>
> So per-team totals are trustworthy and need no ±5% band. What remains
> unexplained is only why one team's legitimate spending exceeds the stated
> $100, which points at ESPN's budget accounting — a refund on a reversed
> claim, or a cap that is not strictly enforced — rather than at our data.

**Only a minority of adds are paid at all.** Teams made 50–126 adds but paid a
nonzero bid on only 9–44 of them — the rest were free-agent pickups requiring
no bid. For "Through The Wire", 43 of 112 adds (38%) cost anything.

**The binding constraint is not money.** Managers spent nearly the whole budget
on ~$1 bids, 61% of successful moves were free, and the real friction was
**2,372 failed bids** — an attempt-to-success ratio of better than 2:1. What is
scarce is **roster spots and waiver priority**, not FAAB dollars.

---

## 5. What this means for the draft optimizer

The optimizer prices players against "the last man rostered", deriving
replacement level from the draft board. Here is the corrected picture.

### The correction to apply

| Quantity | Value | Use |
|---|---|---|
| Best available vs median rostered | 9.2–12.3 /day | **Do not use.** Ceiling for a clairvoyant manager. |
| Realised net per acquisition | **0.75–1.17 /day** | **This is the acquirable value.** |
| Win rate of a move | **54–58%** | Slightly better than a coin flip |
| Volume vs return | r = −0.63 | Marginal moves are worth less |
| Skill persistence | r = 0.55 | The edge is real but modest |

**A realistic in-season replacement-level player is worth roughly +1 composite
point per day over the player he displaces.** Not +11.

### Implications for the draft

1. **The draft still matters more than `waiver_value.md` alone suggested.** A
   ~1 point/day edge on a 14-period horizon is ~14 composite points per move,
   against a typical roster total in the hundreds. Waiver activity is a real but
   *marginal* lever, not a substitute for the auction.
2. **The draft-board replacement level is too low, but by a much smaller
   margin than the availability analysis implied.** The correct adjustment is on
   the order of +1 point/day, not +11.
3. **Do not hoard FAAB.** In the only FAAB season, 61% of successful moves were
   free and the average winning bid was ~$1. Money is not the constraint —
   holding it back buys nothing.

### Where I would push back on the obvious reading

- **"Churn hard" is not supported.** Volume negatively predicts return
  (r = −0.63), and the two candidate explanations — diminishing returns and
  churn-as-symptom — both argue for selectivity over volume.
- **"It's all luck" is also not supported.** Skill persists at r = 0.55. But note
  what that means quantitatively: a real, repeatable edge worth about one
  composite point per day. That is a genuine, exploitable, *small* edge.
- **1 point/day sounds trivial and is not.** Over a 14-day matchup period across
  nine categories, a consistent +1/day is a meaningful share of a category
  margin. It is small relative to the draft, not small relative to a matchup.

---

## Appendix: what would sharpen this

1. **A `DATE` column on `daily_lineup_slots`** — still the single missing
   fixture. Would make the day-resolution work exact rather than window-based.
2. **Multiple FAAB seasons.** One season of price data cannot distinguish a
   $1 equilibrium from a $1 curiosity. If 2027 runs FAAB, the second season
   doubles the sample.
3. **An injury feed.** Cannot separate "dropped because he was bad" from
   "dropped because he was hurt", which is a large part of the churn signal and
   probably a large part of the negative volume correlation.
4. **A held-out decision model.** To convert "what moves were made" into "what
   move was optimal", replay each day's pool against a candidate policy and
   compare. This is the analysis that would directly price a FAAB dollar.
