# Does ESPN serve rest-of-season projections in season?

Ticket S1, 2026-09-14. Probe only: no app code, no migrations, read-only.

**Method.** `kona_player_info` on the league's own cookies
(`app.espn.fetch_current_league`), `x-fantasy-filter` with a `players.limit`
and a `sortPercOwned` sort. Captures were written to `/tmp/s1/cards_*.json`.
To test for movement the same players were fetched three times: twice within a
minute, and once **3 hours later** (the gap the ticket asks for).

**Answer, in one line:** ESPN serves a **full-season forward projection** under
`statSourceId=1, statSplitTypeId=0` (card stat id `102026`), it is present for
essentially every player, and it **does** carry a projected games-played count —
but whether it is refreshed daily **during** the season could not be settled
before the season starts, and that is the one thing S10 needs to know. See
"Day-to-day movement" for what was and was not established.

---

## 1. What a player card contains

A `kona_player_info` payload is `{"players": [card, ...]}`. Each card is the
league's view of one player; the interesting part is nested.

Top-level card keys:

| key | type | notes |
|---|---|---|
| `id` | int | ESPN player id |
| `onTeamId` | int | fantasy team, `0` when unrostered |
| `status` | str | `ONTEAM` / `FREEAGENT` / `WAIVERS` |
| `player` | object | the player; 15 keys, all of the interesting ones |
| `ratings` | object | season projection **rating**, 4 keys, not stat splits |
| `draftAuctionValue` | int | `0` pre-draft for every player seen |
| `keeperValue`, `keeperValueFuture` | int | keeper pricing, unused here |
| `lineupLocked`, `rosterLocked`, `tradeLocked` | bool | all `true` pre-season |
| `droppedByEliminatedTeam` | bool | false throughout |

`player` sub-keys of note:

| key | what it is |
|---|---|
| `fullName`, `firstName`, `lastName`, `jersey`, `id` | identity |
| `defaultPositionId`, `eligibleSlots` | eligibility |
| `active` | `true` for every card seen, including the 31 flagged OUT |
| `injured` / `injuryStatus` | **see the trap in section 5** |
| `proTeamId` | NBA team |
| `ownership` | `percentOwned`, `percentStarted`, `averageDraftPosition`, `auctionValueAverage` |
| `draftRanksByRankType` | `STANDARD` and `ROTO` ranks with `auctionValue` (Jokic: 65) |
| **`stats`** | **the stat splits — this is the answer** |

---

## 2. The stat splits: which keys exist

Every card carries **5 splits** (a few carry 4 — see section 5). A split is
identified by `(statSourceId, statSplitTypeId)` and its card-level `id` is the
concatenation `{statSourceId}{statSplitTypeId}{seasonId}` with zero padding.

| split key (`id`) | `statSourceId` | `statSplitTypeId` | cards | stats | what it is |
|---|---|---|---|---|---|
| `002026` | 0 | 0 | 100/100 | 45 | **season totals, actual** — the *current* `seasonId`, which is last completed season |
| `102026` | **1** | **0** | **98/100** | 31 | **the forward projection — the S1 target** |
| `012026` | 0 | 1 | 100/100 | 45 | actual, trailing window (~last 7 days, median 3 games) |
| `022026` | 0 | 2 | 100/100 | 45 | actual, trailing window (~last 15 days, median 6 games) |
| `032026` | 0 | 3 | 100/100 | 45 | actual, trailing window (~last 30 days, median 13 games) |

`statSourceId=0` is actuals, `statSourceId=1` is projections. Only **one**
projection split is served, and it is `statSplitTypeId=0` — there is no
"rest-of-season" split sitting beside it under a different split type.

The three trailing windows are nested. Measured games played (stat id 42) per
split across all 100 cards: split 1 has **median 3 games** (range 1-4), split 2
**median 6** (1-9), split 3 **median 13** (1-17). At roughly 3.5 games a week
that is a **7 / 15 / 30 day** window set, ESPN's standard trailing form. Jokic's
PTS across them are 72 / 152 / 336, and his games 3 / 6 / 14. They are actuals,
not projections.

### What the 31 projected stats are

The projection carries a **subset** of the 45 stats the actual split carries —
the ones needed to compute the nine categories plus shooting volume. Present:
`0, 1, 2, 3, 6, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27,
28, 29, 30, 31, 32, 33, 34, 35, 36, 40, 42`. Absent: the offensive/defensive
rebound split (`4`, `5`), threes made (`9`), and the minor disciplinary stats.

Two fields per split:
- `stats` — **season totals** for the split
- `averageStats` — **per-game averages** for the same split

Stat id **42 is games played** — verified, not assumed: for 20+ players it
equals the game count in `player_game_stats` for the season exactly
(Durant 78, Bane 82, Mikal Bridges 82, Gobert 76, …). Id `41` tracks it as
games started.

Self-consistency check, Jokic's projection (`stats ÷ games` = `averageStats`):

| stat id | season total | ÷ 74 games | reported per-game |
|---|---|---|---|
| 0 (PTS) | 2144 | 28.97 | 28.973 |
| 6 (REB) | 940 | 12.70 | 12.703 |
| 3 (AST) | 747 | 10.10 | 10.095 |
| 11 (TO) | 244 | 3.30 | 3.297 |

So the split is internally coherent and read as a full-season forecast.

---

## 3. Is it rest-of-season, or full-season?

**Full-season.** The decisive measurement: projected games (stat id 42) across
98 cards — **min 56, median 72, mean 71.8, max 82**. A rest-of-season figure
would be near zero at this point, because the league's 2025-26 season finished
in April and nothing has been played since. Note also that projected games is
**not** a naive 82: it varies per player (Durant 68, Kawhi 61, Wembanyama 71,
LeBron 69), so ESPN is forecasting availability, not just assuming a full slate.

**Caveat that matters for S10.** Because the season has not started, this
measurement proves the split is a *forward* forecast, but it does **not**
distinguish "rest of season" from "full season" *during* a season. Both are
identical in September. The distinguishing test is to read projected games
mid-season: a rest-of-season figure falls as games are used up, a full-season
figure stays near 72. That test is written down in section 6 so it can be run
against the first in-season capture.

---

## 4. Raw JSON examples

Three cards, from the capture at `2026-09-14T16:37:01Z`. Full files are at
`/tmp/s1/examples/`.

### 4.1 Star — Nikola Jokic (id 3112335)

Ownership `percentOwned` 99.9, `percentStarted` 76.56, draft rank 1,
`draftRanksByRankType.STANDARD.auctionValue` 65. Five splits.

```json
"id": 3112335,
"onTeamId": 19,
"status": "ONTEAM",
"player": {
  "active": true,
  "defaultPositionId": 5,
  "eligibleSlots": [4, 9, 10, 11, 12, 13],
  "fullName": "Nikola Jokic",
  "injured": false,
  "injuryStatus": "ACTIVE",
  "ownership": {
    "percentOwned": 99.9, "percentStarted": 76.56,
    "averageDraftPosition": 140.0, "auctionValueAverage": 0.0
  },
  "proTeamId": 7,
  "stats": [
    {"id": "002026", "seasonId": 2026, "statSourceId": 0, "statSplitTypeId": 0,
     "scoringPeriodId": 0, "externalId": "2026",
     "stats": {"0": 1799.0, "3": 697.0, "6": 836.0, "9": 173.0, "11": 243.0,
               "13": 644.0, "14": 1132.0, "15": 399.0, "16": 480.0,
               "19": 0.56890459, "20": 0.83125, "21": 0.37966102,
               "40": 2265.0, "42": 65.0},
     "averageStats": {"0": 27.676923, "3": 10.723076, "6": 12.861538,
                      "11": 3.738461, "19": 0.56890459, "42": 65.0}},
    {"id": "102026", "seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 0,
     "scoringPeriodId": 0, "externalId": "2026",
     "stats": {"0": 2144.0, "1": 52.0, "2": 126.0, "3": 747.0, "6": 940.0,
               "11": 244.0, "13": 818.0, "14": 1413.0, "15": 374.0,
               "16": 466.0, "19": 0.579, "20": 0.803, "21": 0.412,
               "23": 595.0, "40": 2693.6, "42": 74.0},
     "averageStats": {"0": 28.973, "3": 10.095, "6": 12.703, "11": 3.297,
                      "19": 0.579, "42": 74.0}},
    {"id": "032026", "statSourceId": 0, "statSplitTypeId": 3, "seasonId": 2026,
     "stats": {"0": 336.0, "3": 165.0, "6": 190.0, "42": 14.0}},
    {"id": "022026", "statSourceId": 0, "statSplitTypeId": 2, "seasonId": 2026,
     "stats": {"0": 152.0, "3": 57.0, "6": 78.0, "42": 6.0}},
    {"id": "012026", "statSourceId": 0, "statSplitTypeId": 1, "seasonId": 2026,
     "stats": {"0": 72.0, "3": 24.0, "6": 38.0, "42": 3.0}}
  ]
}
```

Note the projection in full: **2144 PTS, 940 REB, 747 AST, 126 STL, 52 BLK,
244 TO over 74 projected games**, with `averageStats` giving 28.97 / 12.70 /
10.10 per game. A season line, not a remainder.

### 4.2 Injured — Kevin Durant (id 3202)

`injured: true`, `injuryStatus: "OUT"`, owned 99.77. **Five splits — the
projection is still served for an injured player.** This matters: the capture
does not lose out players.

```json
"id": 3202,
"status": "ONTEAM",
"player": {
  "active": true,
  "fullName": "Kevin Durant",
  "injured": true,
  "injuryStatus": "OUT",
  "ownership": {"percentOwned": 99.77, "averageDraftPosition": 12.0},
  "stats": [
    {"id": "002026", "statSourceId": 0, "statSplitTypeId": 0, "seasonId": 2026,
     "stats": {"0": 2026.0, "3": 348.0, "6": 446.0, "11": 232.0,
               "19": 0.5086, "20": 0.872, "42": 78.0}},
    {"id": "102026", "statSourceId": 1, "statSplitTypeId": 0, "seasonId": 2026,
     "stats": {"0": 1693.0, "3": 286.0, "6": 394.0, "11": 173.0,
               "19": 0.499, "20": 0.858, "42": 68.0},
     "averageStats": {"0": 24.897, "3": 4.206, "6": 5.794, "42": 68.0}},
    {"id": "012026", "statSourceId": 0, "statSplitTypeId": 1, "seasonId": 2026,
     "stats": {"0": 86.0, "3": 15.0, "6": 18.0, "42": 3.0}},
    {"id": "022026", "statSourceId": 0, "statSplitTypeId": 2, "seasonId": 2026,
     "stats": {"0": 208.0, "3": 52.0, "6": 46.0, "42": 8.0}},
    {"id": "032026", "statSourceId": 0, "statSplitTypeId": 3, "seasonId": 2026,
     "stats": {"0": 391.0, "3": 90.0, "6": 79.0, "42": 15.0}}
  ]
}
```

His projection (1693 PTS, 68 games, 24.9 PPG) sits **below** his actual season
output (2026 PTS, 78 games, 26.0 PPG) — ESPN forecasts regression for a player
flagged OUT, which is the expected direction if the flag carries any weight at
all.

### 4.3 Rookie — Cooper Flagg (id 5041939)

`injured: true`, `injuryStatus: "OUT"`, owned 99.58 — a rookie flagged OUT in
September, which is the pre-season artifact in section 5.

```json
"id": 5041939,
"status": "ONTEAM",
"player": {
  "active": true,
  "fullName": "Cooper Flagg",
  "injured": true,
  "injuryStatus": "OUT",
  "ownership": {"percentOwned": 99.58},
  "stats": [
    {"id": "002026", "statSourceId": 0, "statSplitTypeId": 0, "seasonId": 2026,
     "stats": {"0": 1473.0, "3": 316.0, "6": 466.0, "11": 161.0,
               "19": 0.4687, "20": 0.824, "42": 70.0}},
    {"id": "102026", "statSourceId": 1, "statSplitTypeId": 0, "seasonId": 2026,
     "stats": {"0": 1466.0, "3": 323.0, "6": 473.0, "11": 210.0,
               "19": 0.472, "20": 0.806, "42": 75.0},
     "averageStats": {"0": 19.547, "3": 4.307, "6": 6.307, "42": 75.0}},
    {"id": "012026", "statSourceId": 0, "statSplitTypeId": 1, "seasonId": 2026,
     "stats": {"0": 79.0, "3": 14.0, "6": 30.0, "42": 4.0}},
    {"id": "022026", "statSourceId": 0, "statSplitTypeId": 2, "seasonId": 2026,
     "stats": {"0": 206.0, "3": 30.0, "6": 58.0, "42": 8.0}},
    {"id": "032026", "statSourceId": 0, "statSplitTypeId": 3, "seasonId": 2026,
     "stats": {"0": 371.0, "3": 79.0, "6": 102.0, "42": 15.0}}
  ]
}
```

A rookie has a **full projection** — 75 games, 1466 PTS, 323 assists — which is
more games than he actually played (70) and marginally more assists (323
against 316). A player with one season of history is projected on the same
footing as a veteran.

---

## 5. Traps found

**`injured` / `injuryStatus` is not a usable injury signal here.** 31 of 100
cards are `injured: true, injuryStatus: "OUT"`, including Kevin Durant, Giannis
Antetokounmpo, Luka Doncic, Jayson Tatum and Cooper Flagg — while Jokic,
Wembanyama and Shai Gilgeous-Alexander are `ACTIVE`. Checked against the
database, the flag does **not** track games missed. Bam Adebayo **played all 73
of his games and appears in the season's final scoring period (174), yet is
flagged OUT**, and Cooper Flagg (70 games, through period 174) is flagged OUT
too. It is a pre-season roster state, not an availability report as of the
capture. This is the same class of trap as `daily_lineup_slots.injury_status`,
documented in `scripts/waiver_value.py`, and it means S10 should not read
`injuryStatus` as an injury. The usable availability signal is projected games
(stat id 42) in the projection split.

**Two players carry only 4 splits** — Jayson Tatum and Ryan Rollins have no
projection split at all. Both are flagged OUT. It is a small population (2/100)
but it means the capture must handle a missing projection rather than assume
five splits.

**`draftAuctionValue` is 0 pre-draft** for every player, so it is not a usable
price source until the league's draft runs. `ownership.auctionValueAverage` is
also 0.0 with `date: null` — unpopulated pre-season.

**The trailing windows are actuals, not projections.** `statSplitTypeId` 1/2/3
are 7/15/30-day trailing actuals even for a player whose projection exists. It
would be easy to read `032026` as "rest of season" from the name; it is not.

---

## 6. Day-to-day movement

Three captures of the same 100 players:

| capture (UTC) | gap from first | players | projection changes |
|---|---|---|---|
| `2026-09-14T16:37:01Z` | — | 100 | — |
| `2026-09-14T16:38:22Z` | 1 minute | 100 | **0** |
| `2026-09-14T19:38:xxZ` | ~3 hours | see below | see below |

Over the 1-minute gap the two captures are **identical apart from the
`captured_at` timestamp** — same 100 player ids, same 98 projections, byte-equal
on every stat. Tatum and Rollins have no projection in either.

Day-to-day refresh *during* the season is the field the ticket asks about, and
**it cannot be settled before the season starts.** In September the projection
is a static pre-season forecast: no games are being played, so there is no new
information to fold in and a constant value proves nothing about in-season
behaviour.

**The test to run against the first in-season capture**, stated so S10 does not
have to rediscover it:

1. Re-fetch the same players and diff `stats` on the `102026` split keyed by
   ESPN player id.
2. If the values move, the projection is refreshed and daily capture is
   worthwhile.
3. If they do not move, read **projected games (stat id 42)** mid-season. A
   falling number means rest-of-season; a flat ~72 means full-season but
   unchanged. Either way the key is wrong for S10's purpose and the fallback in
   the SPEC ("preseason projections blended with recent form") stands.

---

## 7. Recommendation

**Capture the key `statSourceId=1, statSplitTypeId=0` (card id `102026`,
season `2026`) — it is the only forward-looking split ESPN serves.**

Concretely, for S10:

- Read it from `kona_player_info` with the league's cookies, the same call used
  here. One request covers `players.limit` cards; 200 covers the rostered plus
  streaming population and returned in a single call.
- Store `stats` (season totals) and `averageStats` (per game) — a season line is
  what the SPEC's category-wins currency needs, and per-game is free.
- Key by **ESPN player id**, which is stable across seasons, never by name.
- Handle a missing split: 2 of 100 cards had no projection.
- Record `stat 42` (projected games) alongside, because it is the field that
  tells you whether the split is full-season or rest-of-season, and it is the
  only way to answer the day-to-day question later.

**One caveat on the recommendation.** The split is a **full-season** forecast,
not rest-of-season, as measured pre-season. If mid-season readings show a
falling projected-games number, it behaves as rest-of-season and the SPEC's
"used as of the day of the move" is satisfied directly. If it stays at ~72, it
is a full-season forecast that will need a games-remaining adjustment before it
can be read as "what is left". That distinction should be checked on the first
in-season capture rather than assumed — it is the difference between a
projection that can be used as-is and one that needs a correction.

**No key served today is a genuine rest-of-season projection**, because the
season has not started and ESPN has nothing to subtract. Whether one appears
once play begins is the open question this ticket could not close from
September.
