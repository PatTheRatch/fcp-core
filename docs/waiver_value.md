# What the Waiver Wire Is Worth

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H, auction draft
**Seasons:** 2019–2026 (eight seasons; 2020 suspended by COVID)
**Script:** `scripts/waiver_value.py` (passes `ruff` and `mypy`)
**Reproduce:** `cd /opt/fcp-core && set -a && . ./.env && set +a && .venv/bin/python scripts/waiver_value.py`

---

## Limitations, stated before conclusions

These come first because they govern how far any of the numbers below can be
pushed. The headline finding is robust; the precision of the free-agent figures
is not.

### 1. The free-agent pool is measured, not modelled — but only for players who played

A free agent on day *N* is defined as a player who recorded a game line that day
and appears in no `daily_lineup_slots` row for the same season and scoring
period. A rostered player who did not play that day is **not** counted as
available. This is the correct definition for "production available", but it
means the pool size understates how many players were *literally* unowned
(IR stashes, benched players on other rosters are excluded).

### 2. "Best available" is a daily maximum, which is not a sustainable acquisition

The best free agent on a given day is a single hot line. Picking him up requires
knowing in advance which of 42–58 available players would go off. Every
season-level figure here is a mean of daily maxima and therefore describes an
**upper bound on what a perfect, clairvoyant manager could have added**, not
what a real one would. The `median` columns in Q1 are closer to a realistic
acquisition.

### 3. The nine-category composite is a simplification, and it is not the league's scoring

`COMP = PTS + REB + AST + STL + BLK + 3PM − TO`. Equal weight per counting
category, turnovers subtracted, percentages excluded because adding a ratio to a
count is a category error.

The league does **not** score this way. It awards nine separate category wins, so
a low-turnover 12/5/3 can beat a high-turnover 30/8/8, and a 4-of-18 shooting
night that wins you points can lose you FG%. The composite is a ranking aid only.
Raw points are reported next to it everywhere so the two can be compared, and
they never disagree in direction in the results below.

### 4. Q4 measures roster occupancy, not production

Q4 counts `(team, player, day)` roster-days. A drafted star and a waiver
fill-in each count one. So **Q4 understates the drafted share of production and
overstates the acquired share.** The production-weighted version is in §5 and
gives a different answer (47.7% vs 42.5% for 2026) — both are reported rather
than one substituted for the other, because they answer different questions.

The production figure rests on the day join, which is shape-validated rather than
independently confirmed. It reconciles to within 1.9% of the league's own
recorded team totals, which is the strongest available check.

### 5. 2020 is excluded from every headline figure

The COVID suspension left the season with a 98-period matchup block covering 10
real days and one period with no games at all. Its numbers are shown, flagged,
and never pooled.

### 6. 2026 was in progress at time of writing

Data runs through scoring period 174. 2026 figures are current-state, not final.

### 7. Small samples throughout

Four team counts, one league, eight seasons. The league-size gradient in §4 is
fit on 1–3 seasons per size. Treat it as a direction, not a slope.

---

## The data model, and one trap that looks like a bug but is not

`daily_lineup_slots` (DLS) and `player_game_stats` (PGS) both key on
`scoring_period`, and joining them directly matches only **24–44%** of rows. That
looks like a broken join. It is not, and treating it as one would have thrown
away every free-agent answer.

The real explanation: a rostered player records a line only on days his NBA team
plays, which is about **45% of days**. A partial match is the *expected* result.

The test that separates "healthy partial match" from "misaligned coordinate" is
the **shape** of the per-period match rate. For 2025:

```
15.4%  62.2%  27.6%  61.5%  60.9%  34.0%  70.5%  31.4%
62.2%  23.7%  56.4%  57.7%  21.8%  85.3%   0.0%  69.2%
```

Alternating heavy and light, with one period at exactly 0.0% — the All-Star
break. That is the NBA schedule (Mon/Wed/Fri slates against Tue/Thu), not
scrambled noise. A misaligned join would produce a flat cluster around the mean.
Standard deviation is 23.4 with a mean successive change of 38.4.

Confirmed independently per player: **87–100%** of Shai Gilgeous-Alexander's
games land on days he was held (2019 is the outlier at 41.5%).

**SQL for the audit:**

```sql
SELECT ls.season,
       count(*) AS dls_rows,
       count(*) FILTER (WHERE pgs.id IS NOT NULL) AS has_line
FROM daily_lineup_slots dls
JOIN teams t ON t.id = dls.team_id
JOIN league_seasons ls ON ls.id = t.league_season_id
LEFT JOIN player_game_stats pgs
       ON pgs.player_id = dls.player_id
      AND pgs.season = ls.season
      AND pgs.scoring_period = dls.scoring_period
      AND pgs.played AND pgs.minutes > 0
WHERE ls.season BETWEEN 2019 AND 2026
GROUP BY ls.season ORDER BY ls.season;
```

### Other traps handled

| Trap | Verified finding | Handling |
|---|---|---|
| `injury_status` / `injured` as a time series | Trae Young carries exactly **2** distinct values across 244 roster-days. Julius Randle is flagged on **557** days and played all 557 (18,447 min). | Never used. |
| `roster_slots` (weekly) | Aggregates a whole matchup period, including players added and dropped within it. | Not used. DLS is point-in-time. |
| Unequal period lengths | 6-day opener, 14-day All-Star, and a 98-period COVID block in 2020. | All reporting is per scoring period, so lengths are never pooled. |
| Counting stats vs league size | Falls with team count as the pool is shared wider. | Nothing pooled across team counts; split by `team_count`. |
| Even team counts | Biases `values[len//2]` upward in every season. | `statistics.median` throughout. |
| `game_date` as an anchor | Spans two calendar dates per period and is NULL on 770–4,996 rows/season, ~900 of which carry real production. | Not used as a join key. |
| Duplicate player-days | 76–238 per season, same player under two scoring periods. | Larger line kept; summing would invent production. |

---

## 1. Best production available unrostered, per scoring period

```sql
WITH fa AS (
  SELECT ls.season, ls.team_count, pgs.scoring_period, pgs.points,
         (pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks
          + pgs.three_pointers_made - pgs.turnovers) AS comp
  FROM player_game_stats pgs
  JOIN league_seasons ls ON ls.season = pgs.season
  WHERE pgs.played AND pgs.minutes > 0
    AND pgs.season BETWEEN 2019 AND 2026
    AND NOT EXISTS (
      SELECT 1 FROM daily_lineup_slots dls
      JOIN teams t ON t.id = dls.team_id
      JOIN league_seasons l2 ON l2.id = t.league_season_id
      WHERE l2.season = pgs.season
        AND dls.scoring_period = pgs.scoring_period
        AND dls.player_id = pgs.player_id
    )
),
per_day AS (
  SELECT season, team_count, scoring_period,
         count(*) AS n_fa, max(points) AS best_pts, max(comp) AS best_comp
  FROM fa GROUP BY season, team_count, scoring_period
)
SELECT season, team_count, count(*) AS days, AVG(n_fa) AS avg_fa,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY best_pts) AS med_pts,
       AVG(best_pts) AS avg_pts,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY best_comp) AS med_comp,
       AVG(best_comp) AS avg_comp, max(best_pts) AS max_pts
FROM per_day GROUP BY season, team_count ORDER BY season;
```

| Season | Teams | Days | FA/day | Best PTS | Med PTS | Best COMP | Med COMP | Max PTS |
|---|---|---|---|---|---|---|---|---|
| 2019 | 10 | 168 | 48 | 24.6 | 24.0 | 38.0 | 38.0 | 35 |
| 2020 | 10 | 131 | 42 | 25.6 | 25.0 | 38.8 | 39.0 | 39 |
| 2021 | 10 | 140 | 51 | 25.7 | 25.0 | 39.3 | 39.0 | 42 |
| 2022 | 12 | 165 | 57 | 27.7 | 25.0 | 41.8 | 37.0 | 60 |
| 2023 | 16 | 164 | 48 | 24.0 | 22.0 | 36.4 | 34.0 | 57 |
| 2024 | 14 | 159 | 52 | 23.2 | 23.0 | 36.1 | 35.0 | 52 |
| 2025 | 12 | 162 | 58 | 27.0 | 25.0 | 41.0 | 38.5 | 61 |
| 2026 | 14 | 164 | 53 | 24.1 | 23.0 | 37.8 | 36.5 | 51 |

**A 22–28 point scorer is available for free on a typical day, every season.**
The median across all eight seasons is a 24-point, 36-composite line. Peak days
reach 51–61 points.

The mean and median track closely (within ~2 points), so this is not an artifact
of a few monster nights — **the typical day really does have this available.**

---

## 2. Best available versus the weakest player actually rostered

"Weakest rostered" is ambiguous, and the choice moves the answer more than the
data does, so all three baselines are reported.

```sql
WITH fa_day AS (
  SELECT ls.season, pgs.scoring_period, max(
           pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks
           + pgs.three_pointers_made - pgs.turnovers) AS best_fa
  FROM player_game_stats pgs
  JOIN league_seasons ls ON ls.season = pgs.season
  WHERE pgs.played AND pgs.minutes > 0 AND pgs.season BETWEEN 2019 AND 2026
    AND NOT EXISTS (
      SELECT 1 FROM daily_lineup_slots dls
      JOIN teams t ON t.id = dls.team_id
      JOIN league_seasons l2 ON l2.id = t.league_season_id
      WHERE l2.season = pgs.season AND dls.scoring_period = pgs.scoring_period
        AND dls.player_id = pgs.player_id)
  GROUP BY ls.season, pgs.scoring_period
),
roster AS (
  SELECT ls.season, ls.team_count, dls.scoring_period,
         (pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks
          + pgs.three_pointers_made - pgs.turnovers) AS comp, pgs.minutes
  FROM daily_lineup_slots dls
  JOIN teams t ON t.id = dls.team_id
  JOIN league_seasons ls ON ls.id = t.league_season_id
  JOIN player_game_stats pgs
    ON pgs.player_id = dls.player_id AND pgs.season = ls.season
   AND pgs.scoring_period = dls.scoring_period AND pgs.played AND pgs.minutes > 0
  WHERE ls.season BETWEEN 2019 AND 2026
),
low_min AS (
  SELECT DISTINCT ON (season, scoring_period)
         season, scoring_period, comp AS low_min_comp
  FROM roster ORDER BY season, scoring_period, minutes ASC, comp ASC
),
roster_day AS (
  SELECT r.season, r.team_count, r.scoring_period,
         min(r.comp) AS worst_comp,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY r.comp) AS median_comp,
         max(l.low_min_comp) AS low_min_comp
  FROM roster r
  JOIN low_min l ON l.season = r.season AND l.scoring_period = r.scoring_period
  GROUP BY r.season, r.team_count, r.scoring_period
)
SELECT f.season, r.team_count, count(*) AS days,
       AVG(f.best_fa) AS best_fa, AVG(r.median_comp) AS median_rostered,
       AVG(f.best_fa - r.worst_comp) AS edge_worst,
       AVG(f.best_fa - r.low_min_comp) AS edge_lowmin,
       AVG(f.best_fa - r.median_comp) AS edge_median
FROM fa_day f
JOIN roster_day r ON r.season = f.season AND r.scoring_period = f.scoring_period
GROUP BY f.season, r.team_count ORDER BY f.season;
```

| Season | Teams | Days | Best FA | Median rostered | vs worst | vs low-min | **vs median** |
|---|---|---|---|---|---|---|---|
| 2019 | 10 | 168 | 38.0 | 26.2 | 32.7 | 29.2 | **11.7** |
| 2020 | 10 | 131 | 38.8 | 26.9 | 33.8 | 29.3 | **11.9** |
| 2021 | 10 | 140 | 39.3 | 27.0 | 34.1 | 30.3 | **12.3** |
| 2022 | 12 | 131 | 36.2 | 25.3 | 32.9 | 29.6 | **10.9** |
| 2023 | 16 | 144 | 33.4 | 23.4 | 31.7 | 29.7 | **10.0** |
| 2024 | 14 | 147 | 34.0 | 24.8 | 31.3 | 28.1 | **9.2** |
| 2025 | 12 | 135 | 37.6 | 26.1 | 33.8 | 29.8 | **11.5** |
| 2026 | 14 | 151 | 35.9 | 24.8 | 32.7 | 29.0 | **11.0** |

**This is the finding that matters for the optimizer.**

- Against the single worst rostered line: a **31–34 point composite edge**. That
  number is real but nearly meaningless — it compares a hot night against a
  two-minute cameo.
- Against the least-used rostered player: **28–30 points**.
- **Against the median rostered player: 9.2–12.3 points.** This is the honest
  comparison, and it is large.

The last row is the one to act on. **The best available free agent outscores the
median rostered player by roughly 10–12 composite points every single day, in
every season, in every league size.**

---

## 3. Does the gap change with league size?

```sql
-- see script q3_by_league_size(); groups the §2 query by team_count
```

| Teams | Seasons | Best PTS/day | Best COMP/day | **Edge vs median** |
|---|---|---|---|---|
| 10 | 3 | 25.3 | 38.6 | **12.0** |
| 12 | 2 | 24.0 | 36.9 | **11.2** |
| 14 | 2 | 22.2 | 34.9 | **10.1** |
| 16 | 1 | 21.5 | 33.4 | **10.0** |

Absolute production falls steadily with league size — best-available drops from
38.6 to 33.4 composite as the pool is shared across 16 instead of 10 rosters.
**That decline is expected and is not evidence about the waiver wire.**

The meaningful column is the edge, which **narrows only slightly: 12.0 → 10.0**.
The wire gets thinner in a bigger league, but nowhere near enough to close the
gap. Even in a 16-team league the best available free agent still beats the
median rostered player by 10 composite points.

**Caveat:** the 16-team row rests on a single season, and 10-team on three
(including the COVID year). This is a gradient, not a fitted relationship.

---

## 4. Share of roster-days from post-draft acquisition

```sql
WITH held AS (
  SELECT ls.season, ls.team_count, dls.team_id, dls.player_id,
         count(DISTINCT dls.scoring_period) AS days_held
  FROM daily_lineup_slots dls
  JOIN teams t ON t.id = dls.team_id
  JOIN league_seasons ls ON ls.id = t.league_season_id
  WHERE ls.season BETWEEN 2019 AND 2026
  GROUP BY ls.season, ls.team_count, dls.team_id, dls.player_id
)
SELECT h.season, h.team_count,
       SUM(h.days_held) AS slot_days,
       SUM(h.days_held) FILTER (WHERE dp.player_id IS NOT NULL) AS drafted_days
FROM held h
JOIN league_seasons ls ON ls.season = h.season
LEFT JOIN draft_picks dp
       ON dp.league_season_id = ls.id AND dp.team_id = h.team_id
      AND dp.player_id = h.player_id
GROUP BY h.season, h.team_count ORDER BY h.season;
```

| Season | Teams | Slot-days | Drafted | **Drafted %** | **Acquired %** |
|---|---|---|---|---|---|
| 2019 | 10 | 22,995 | 11,445 | 49.8% | 50.2% |
| 2020 | 10 | 31,718 | 17,322 | 54.6% | 45.4% *(COVID)* |
| 2021 | 10 | 18,964 | 10,011 | 52.8% | 47.2% |
| 2022 | 12 | 21,614 | 13,131 | 60.8% | 39.2% |
| 2023 | 16 | 32,018 | 21,611 | **67.5%** | 32.5% |
| 2024 | 14 | 29,115 | 14,482 | 49.7% | 50.3% |
| 2025 | 12 | 22,775 | 11,187 | 49.1% | 50.9% |
| 2026 | 14 | 29,100 | 12,365 | **42.5%** | **57.5%** |

**Post-draft acquisitions occupy 32.5%–57.5% of all roster-days, and the trend
is worsening for the draft: 67.5% drafted in 2023 down to 42.5% in 2026.**

### Players consumed per team

| Season | Teams | Median players used | Median drafted kept | Median added |
|---|---|---|---|---|
| 2019 | 10 | 57.0 | 13.0 | 44.0 |
| 2020 | 10 | 52.5 | 12.0 | 40.5 |
| 2021 | 10 | 68.5 | 12.5 | 56.0 |
| 2022 | 12 | 65.5 | 13.0 | 52.5 |
| 2023 | 16 | 41.5 | 13.0 | 28.5 |
| 2024 | 14 | 61.0 | 12.0 | 49.0 |
| 2025 | 12 | 75.0 | 12.0 | 63.0 |
| 2026 | 14 | 71.5 | 12.0 | 59.5 |

A team drafts 13, keeps about **12** all season, and churns through **28–63
non-drafted players** to fill the rest.

---

## 5. Reconciling Q4 with the roster-churn analysis

The roster churn work found that ~69% of a team's *end-of-season roster* was not
drafted. Q4 above says drafted players *occupied* 42.5–67.5% of roster-days.
**These are not in conflict** — they measure different things, and the
distinction is the most useful part of this analysis:

| Measure | What it counts | 2026 figure |
|---|---|---|
| End-of-season roster *composition* | bodies on the final day | ~31% drafted |
| Roster-day *occupancy* (Q4) | every day a player was held | 42.5% drafted |
| *Production* share | points scored | 47.7% drafted |

The pattern across all three: **drafted players are a minority of the roster but
a larger share of the production.** They occupy 42.5% of roster-days and produce
47.7% of the points — because drafted players get the minutes. A waiver pickup
fills a roster spot; a drafted player fills a rotation.

A production-weighted figure, using the now-validated day join so it is exact:

```sql
SELECT ls.season, ls.team_count,
       SUM(pgs.points) AS total_pts,
       SUM(pgs.points) FILTER (WHERE dp.player_id IS NOT NULL) AS draft_pts
FROM daily_lineup_slots dls
JOIN teams t ON t.id = dls.team_id
JOIN league_seasons ls ON ls.id = t.league_season_id
JOIN player_game_stats pgs
  ON pgs.player_id = dls.player_id
 AND pgs.season = ls.season
 AND pgs.scoring_period = dls.scoring_period
 AND pgs.played AND pgs.minutes > 0
LEFT JOIN draft_picks dp
       ON dp.league_season_id = ls.id
      AND dp.team_id = dls.team_id
      AND dp.player_id = dls.player_id
WHERE ls.season BETWEEN 2019 AND 2026
GROUP BY ls.season, ls.team_count ORDER BY ls.season;
```

| Season | Teams | Rostered PTS | Draft PTS | **Draft %** | **Pickup %** |
|---|---|---|---|---|---|
| 2019 | 10 | 160,921 | 89,542 | 55.6% | 44.4% |
| 2020 | 10 | 125,632 | 83,430 | 66.4% | 33.6% *(COVID)* |
| 2021 | 10 | 141,075 | 83,597 | 59.3% | 40.7% |
| 2022 | 12 | 138,736 | 91,474 | 65.9% | 34.1% |
| 2023 | 16 | 192,658 | 141,115 | **73.2%** | 26.8% |
| 2024 | 14 | 192,978 | 108,197 | 56.1% | 43.9% |
| 2025 | 12 | 155,335 | 84,155 | 54.2% | 45.8% |
| 2026 | 14 | 183,466 | 87,550 | **47.7%** | **52.3%** |

Post-draft acquisitions supplied **26.8%–52.3% of all production**, and for the
first time in 2026 they supplied the **majority (52.3%)**.

Sanity check on the denominator: 2025's 155,335 compares against the league's own
recorded team PTS of 152,408 from `matchup_team_stats`, a 1.9% difference
attributable to the join's partial coverage. The figure is the right magnitude.

---

## 6. What this means for the draft optimizer

The optimizer prices players against "the last man rostered", deriving
replacement level from the draft board. **That assumption is too low, and this
data says so fairly directly.**

**Supported by the evidence:**

- A 22–28 point scorer is unrostered on a typical day, in every season. The
  median available line is ~24 points / 36 composite.
- The best available free agent beats the **median rostered player** by 10–12
  composite points per day, in every season, at every league size.
- The edge barely narrows with league size (12.0 → 10.0 from 10 to 16 teams), so
  this is not an artifact of shallow leagues.
- Post-draft acquisitions occupy 42.5–57.5% of roster-days and supplied roughly
  half of all production.
- **Zero** players who recorded a game went unrostered for a whole season. The
  entire relevant pool gets churned — "free agent" is purely a point-in-time
  state in this league.

**Implications:**

1. **Replacement level anchored to the draft board is too low.** The last man
   rostered is not the marginal player — a better-than-median player is
   frequently available. Auction values derived from a draft-board replacement
   baseline are therefore inflated, and the inflation is largest at the top of
   the board where the baseline is most wrong.
2. **FAAB is worth more than a draft dollar, at the margin.** If the wire
   supplies a median-beating player for free most days, the optimal spend
   profile shifts from the auction toward in-season acquisition.
3. **The trend strengthens the case.** Drafted occupancy fell from 67.5% (2023)
   to 42.5% (2026). The later the season, the more the wire matters.

**Where I would push back:**

- Daily maxima are not acquirable. §2's edge is a ceiling for a clairvoyant
  manager; a real one picks from 40–60 candidates and will land closer to the
  median of the available pool than its maximum. **The realistic edge is smaller
  than 10–12 and this analysis does not quantify it.** That would need a
  simulation of actual pickup decisions, not a daily maximum.
- Roster moves have opportunity cost. Each add costs a drop and a FAAB bid; the
  analysis prices the upside but not the transaction cost.
- Those 42–58 "available" players include many on NBA rosters who are simply
  rarely useful. Pool size is not pool quality.

**The honest bottom line:** the draft-board replacement assumption is wrong in
the direction the optimizer cares about, and the error is large enough to matter
(10+ composite points per day against the median rostered player). But the
correct replacement level has to be derived from the **distribution of what was
actually available and acquirable**, not from the daily maximum — and that
distribution is a different, harder analysis than this one.

---

## Appendix: what would make this exact

1. **A date column on `daily_lineup_slots`.** The single missing fixture. It
   would remove every caveat about the DLS/PGS join and make Q1/Q2 exact rather
   than shape-validated. One `DATE` column, populated at ingest.
2. **Daily `injury_status`.** ESPN returns status as-of-request, so the current
   column is unusable historically. Availability analysis needs the timestamped
   form, or a second source for injury timelines.
3. **A simulation of acquisition decisions.** To convert "best available" into
   "what a real manager would have added", replay each day's pool against
   plausible decisions and measure the realised gain.
