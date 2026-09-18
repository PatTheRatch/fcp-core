# In-season pickups: a listener, a recommender, and a digest

**League:** Full Court Press (ESPN 3853870), nine-category H2H, auction draft, FAAB
**Written:** 2026-09-16, five weeks before the 2027 season tips off (ESPN labels a season by the year it ends in)
**Status:** phase 1, the listener, and phase 1b, the digest, are built (2026-09-17: `app/listener/`, `app/digest.py`, `app/notify.py`, migration `0016`, `scripts/status_pass.py`, `scripts/digest.py`, `deploy/fcp-core-status.*`) and wait on the VPS steps in `STATUS.md` under "The listener". Phase 2 is built (2026-09-18: `app/pickups/state.py`, `projection.py`, `stream.py`, `season.py`, `bids.py`, `app/api/pickups.py`, `scripts/stream.py`, `scripts/season.py`); the backtest (§4.6) is built too (2026-09-18, `scripts/pickups_backtest.py`), scoring in categories, and both halves of the recommender now judge a move in one currency over both horizons (`app/pickups/judge.py`); the digest's sections 3 and 4, the free-agent and week routes and phases 3 to 4 are still design. Where the build departed from this note, the note says so in place, marked **as built**.
**Companions:** [`waiver_value.md`](waiver_value.md) (what the wire offered), [`acquirable_value.md`](acquirable_value.md) (what real moves returned), [`stars_and_waivers.md`](stars_and_waivers.md) (whether pickups rescue a draft)

---

## 0. The answer, up front

Build the **listener first**, before opening night, because it captures the one thing the database cannot reconstruct: a player's injury status and ownership **as a time series**. Everything else in this note can be built and backtested later against 2026 data already stored. The status history cannot be backfilled, and every day it is not running is a day lost.

Then build a **recommender** that answers two questions for one chosen team: *who should I stream this week* and *who should I hold for the rest of the season*. Both reuse the draft optimizer's objective, expected categories won per matchup period against this league's measured opponent distribution. A pickup is a swap, and `_swap_improve` in [`app/draft/optimizer.py`](../app/draft/optimizer.py) already evaluates swaps over a pool.

The recommender must be allowed to say **"no move clears the hurdle today."** `acquirable_value.md` measured a real swap at roughly 0.6 to 1.0 composite points per day, winning its comparison about 55% of the time, and found add volume *negatively* related to return (r = -0.63). A tool that always names a pickup would make its user worse. Acquisition skill persists across seasons (partial r = +0.63 after removing volume), so there is an edge; it comes from fewer, better moves.

"Sentiment" in this note means three quantitative signals already exposed by ESPN: injury status changes, the 24-hour change in percent owned across all ESPN leagues, and a player's own minutes trend from box scores. Social media is deferred (§9). Those three cover most of what a beat reporter adds, at no cost.

---

## 1. What exists, and the three gaps

### Already stored (see `STATUS.md` for row counts)

| Table | What the recommender takes from it |
|---|---|
| `daily_lineup_slots` | Who every team held each day, so "free agent" is derivable historically (used by `waiver_value.py`) |
| `player_game_stats` | Minutes and the nine categories per player per day; the minutes trend comes from here |
| `player_season_stats` | ESPN's preseason projection and season totals, `kind` = projected / total |
| `matchup_team_stats` | Each team's per-category totals per matchup, the live state of the current week |
| `matchup_periods` | Which scoring periods each matchup period covers |
| `transactions`, `transaction_items` | Every add, drop and FAAB bid, with what winning cost (`/contested-claims`) |
| `league_seasons` | `lineup_slots`, `bench_slots`, `injured_reserve_slots` (1 from 2027), `position_limits`, `auction_budget` |
| `roster_slots.injury_status`, `daily_lineup_slots.injury_status` | **Do not use.** One snapshot as of the request, smeared across the season. See the module docstring of [`app/draft/availability.py`](../app/draft/availability.py). |

### Already written, reusable as-is

- `app/draft/valuation.py`: `PlayerProjection` (season totals, eligible slots, position), `value_players` (z-scores against the pool).
- `app/draft/targets.py`: `category_distributions` gives a `CategoryDistribution` per category, the measured distribution of what an opponent posts in a week here.
- `app/draft/optimizer.py`: `Candidate` (a weekly line), `score`, `roster_totals`, `fieldable`, `within_shape`, `_swap_improve`, `optimize(candidates, distributions, budget=, roster_slots=, locked=, excluded=, lineup=, limits=)`. The objective is `expected_wins`, the sum over categories of the probability of beating an opponent.
- `app/draft/pool.py`: `load_projections`, `lineup_for`, `position_limits_for`, `roster_size_for`, `effective_weeks`.
- `app/draft/bbm.py`: Basketball Monster rates per game and projected games, with analyst `note`, `confidence`, `role`, `status`, flags. Preseason only; BBM is an .xls the manager exports by hand.
- `app/ingest.py`: `ingest_player_stats` batches `espn_league.player_info(playerId=[...])` at `PLAYER_INFO_BATCH = 100`; `ingest_transactions` reads `mTransactions2` per scoring period; `IngestRun` records every run.
- `scripts/price_scorecard.py` `espn_values()`: the pattern for a **raw** `kona_player_info` request with paging (`limit`/`offset` in the `X-Fantasy-Filter` header) and cookies from `get_espn_settings()`. The listener's pool fetch should look like this, moved into `app/espn.py`.
- `scripts/scheduled_ingest.sh` and `deploy/fcp-core-ingest.{service,timer}`: the shape a scheduled pass takes, including the schema-at-head guard and exit codes.

### Gap 1: no injury or ownership history

`espn_api.basketball.player.Player` exposes `injuryStatus`, `injured`, `expected_return_date`, `proTeam` on every card, and the raw card carries `ownership.percentOwned`, `ownership.percentChange`, `ownership.percentStarted`, `ownership.auctionValueAverage`. None is stored per observation. The fix is a snapshot table written on every pass (§3.1).

### Gap 2: the visible player pool is only ever-rostered players

`_season_player_ids` scopes `ingest_player_stats` to the union of `roster_slots` and `daily_lineup_slots`. In 2026 that was 348 players. A pickup engine has to see the roughly 500 players ESPN carries, or at least every free agent with a pulse. The fix is a free-agent snapshot (§3.3) whose player ids are unioned into the scope for the current season only.

### Gap 3: the schedule runs at the wrong hour for injuries

`fcp-core-ingest.timer` fires at 09:00 UTC, after games. Injury reports land around 17:00 Eastern (21:00/22:00 UTC depending on DST) and game-time decisions at tip-off. A status pass that reads only cards is a fraction of the nightly ingest's cost and can run three times a day (§3.6).

---

## 2. Architecture

Four layers. Each of the first three is a pure function over stored rows, like the rest of the project, so it is testable against a fake and, for the recommender, against 2026.

```
ESPN (kona_player_info, kona_playercard, news, proTeamSchedules)
        │
        ▼
 1. LISTENER  app/listener/     writes player_status_snapshots, player_news,
                                free_agent_snapshots, pro_team_games
                                then diffs snapshots -> player_status_events
        │
        ▼
 2. RECOMMENDER  app/pickups/   reads everything above + existing tables,
                                for one (league_season, team): stream this week,
                                hold for the season, drop candidates, a bid
        │
        ▼
 3. DELIVERY  app/api/pickups.py routes; scripts/digest.py -> phone
        │
        ▼
 4. LATER    news summarisation by an LLM; a frontend
```

Everything is keyed on `(league_season_id, team_id)`. The manager picks the team by id. No auth is introduced; `STATUS.md` lists auth and a frontend under "not building yet", and a team id parameter is what lets that stay true.

---

## 3. Layer 1: the listener

### 3.1 `player_status_snapshots`

One row per player per observation. Store every observation rather than only changes: about 550 players × 3 passes × 180 days is roughly 300k rows a season, trivial in Postgres, and it keeps the diff (§3.5) honest because "no change" is then a fact on record rather than an absence.

| column | type | source on the raw `kona_player_info` entry (`entry["player"]`) |
|---|---|---|
| `id` | int PK | |
| `player_id` | FK `players.id` | `id`, via `_get_or_create_player` (creates the row if the player has never been rostered here) |
| `season` | int | the season being observed |
| `observed_at` | timestamptz | wall clock of the pass, one value for the whole pass |
| `pass_label` | text | `"morning"`, `"report"`, `"late"` (§3.6), so a query can pick one pass a day |
| `injury_status` | text nullable | `injuryStatus`: ACTIVE, OUT, DAY_TO_DAY, QUESTIONABLE, DOUBTFUL, PROBABLE, SUSPENSION, ... store the string as ESPN gives it |
| `injured` | bool | `injured` |
| `expected_return_date` | date nullable | `expectedReturnDate`, an array `[y, m, d, ...]` on the raw card |
| `pro_team_id` | int nullable | `proTeamId` (0 means no team) |
| `on_team_id` | int nullable | `entry["onTeamId"]`, the fantasy team holding him in this league, 0 if none |
| `status` | text nullable | `entry["status"]`: ONTEAM, FREEAGENT, WAIVERS |
| `percent_owned` | float nullable | `ownership.percentOwned` |
| `percent_change` | float nullable | `ownership.percentChange`, the 24h move |
| `percent_started` | float nullable | `ownership.percentStarted` |
| `auction_value_average` | float nullable | `ownership.auctionValueAverage` |

Indexes: `(player_id, observed_at)` and `(season, observed_at)`.

Field names for `onTeamId`, `status`, `waiverProcessDate` should be confirmed with a probe before the migration is written. Extend `scripts/espn_probe.py` or add `--dump-card PLAYER_ID` to print one raw entry. `injuryStatus`, `injured`, `expectedReturnDate`, `proTeamId` and the `ownership` block are confirmed by `espn_api`'s own parser and by `price_scorecard.py`.

**As built:** `onTeamId` and `status` were already confirmed by the S1 probe (`docs/scoring/espn_projections.md` on branch `scoring-s1`, section 1). `waiverProcessDate` is read on trust; `scripts/espn_probe.py --pool-keys` counts how many WAIVERS entries carry it and `--dump-card ID` prints one. The migration is `0016`, not `0014`, which went to S10.

### 3.2 `player_news`

| column | type | source |
|---|---|---|
| `player_id` | FK | |
| `published` | timestamptz | `item["published"]` |
| `headline` | text | |
| `story` | text | |
| `source` | text | `"espn"` |
| `seen_at` | timestamptz | first pass that saw it |

Unique on `(player_id, published, headline)`. Source: `espn_request.get_player_news(playerId)` (`league.player_info(..., include_news=True)`). This is one request **per player**, so do not call it for the whole pool. Call it only for players who produced an event in this pass (§3.5) and for players on the chosen team's roster. That bounds it to a few dozen requests.

### 3.3 `free_agent_snapshots`

Who was available in this league at each pass, and their waiver state.

| column | type | source |
|---|---|---|
| `league_season_id` | FK | |
| `observed_at` | timestamptz | same value as the status pass |
| `scoring_period` | int | `league.currentMatchupPeriod` / `scoringPeriodId` at the time |
| `player_id` | FK | |
| `status` | text | FREEAGENT or WAIVERS |
| `waiver_clears_at` | timestamptz nullable | field on the entry, name to confirm by probe |

Source: raw `kona_player_info` with `filterStatus: {"value": ["FREEAGENT", "WAIVERS"]}`, `sortPercOwned` descending, paged by `limit`/`offset` exactly as `price_scorecard.espn_values` does. Do **not** use `League.free_agents()`: it has a `size` but no offset, so it cannot page past the first slice. Fetch until a page comes back short. Expect roughly 300 to 400 rows for a 14-team league that rosters 182.

Put the request in `app/espn.py` as `fetch_player_pool(league, *, statuses, scoring_period) -> list[dict]` and have the status pass call it twice: once with FREEAGENT/WAIVERS for the free-agent table and once with ONTEAM (or with no status filter, then split on `onTeamId`) so every rostered player is also snapshotted. One unfiltered pass paged at 250 is four to five requests for the whole pool. That is the cheapest shape and gives §3.1 and §3.3 from the same payload.

**Widening the stats scope.** In `ingest_player_stats`, for the current season only, union the player ids from the latest `free_agent_snapshots` into `_season_player_ids`. Guard it behind the scope being `recent` so a historical full pass does not refetch the universe. This makes an unrostered player's minutes visible in `player_game_stats`, which §4.2 needs.

### 3.4 `pro_team_games`

Games remaining this matchup period is the whole basis of streaming, and it needs the NBA schedule.

| column | type | source |
|---|---|---|
| `season` | int | |
| `pro_team_id` | int | |
| `scoring_period` | int | key of `league.pro_schedule[pro_team_id]` |
| `game_at` | timestamptz | `game["date"]` epoch ms |
| `opponent_pro_team_id` | int | the other of `homeProTeamId` / `awayProTeamId` |
| `home` | bool | |

Unique on `(season, pro_team_id, scoring_period)`. Source: `League.pro_schedule`, populated on league load by `League._get_all_pro_schedule()`, which requests the `proTeamSchedules_wl` view; `espn_api` already parses it in `Player.__init__` to attach `team` and `date` to each stat line. (`espn_request.get_pro_players()` also exists and returns the whole NBA player list without league context, an alternative if the paged pool fetch in §3.3 ever proves awkward.) Rewrite the whole table for the season on each pass; postponements move games.

### 3.5 `player_status_events`: the diff

Derived from consecutive snapshots and persisted, because the digest needs to know what it has already reported.

| column | type | |
|---|---|---|
| `player_id` | FK | |
| `season` | int | |
| `kind` | text | see the table below |
| `observed_at` | timestamptz | the pass that first saw the new state |
| `previous` | jsonb | the fields that changed, before |
| `current` | jsonb | the same fields, after |
| `detail` | jsonb | anything the rule computed, e.g. the minutes figures |
| `notified_at` | timestamptz nullable | set by the digest when sent |

Unique on `(player_id, kind, observed_at)`.

| kind | rule (compare the newest snapshot with the previous one for the same player) |
|---|---|
| `went_out` | `injury_status` moved from anything else to OUT or SUSPENSION |
| `downgraded` | ACTIVE/PROBABLE → QUESTIONABLE/DOUBTFUL/DAY_TO_DAY |
| `upgraded` | OUT/DOUBTFUL/QUESTIONABLE/DAY_TO_DAY → a better status, short of ACTIVE |
| `returned` | anything → ACTIVE after at least one non-ACTIVE observation |
| `return_date_changed` | `expected_return_date` changed, either direction; `detail` carries the day delta |
| `changed_pro_team` | `pro_team_id` changed and both non-zero |
| `dropped` | `status` ONTEAM → FREEAGENT/WAIVERS; `detail` names the dropping fantasy team from `on_team_id` |
| `claimed` | FREEAGENT/WAIVERS → ONTEAM; `detail` names the team |
| `waiver_clearing` | on WAIVERS and `waiver_clears_at` falls before the next scheduled pass |
| `ownership_surge` | `percent_change` ≥ +5.0, or `percent_owned` crossed 25% upwards. Thresholds are a decision (§10); start there and tune |
| `ownership_slide` | `percent_change` ≤ -5.0 |
| `minutes_spike` | from `player_game_stats`, not the snapshot: mean minutes over the last 3 games played ≥ mean over the prior 10 + 8.0, with at least 3 games in each window. `detail` carries both means |
| `minutes_drop` | the mirror, -8.0 |

`minutes_spike` is the one event that needs no ESPN status at all and is the classic pickup: the backup who went from 18 to 31 minutes because the starter is out. It comes from box scores already stored, once §3.3 widens the scope.

Rules live in `app/listener/events.py` as pure functions over two snapshot rows plus a minutes series, tested with constructed rows. The first snapshot for a player produces no events.

**As built**, three rules are tighter than the table so an event means one thing happened:

- `ownership_surge` and `ownership_slide` fire as the 24-hour move crosses ±5.0, not on every pass it stays beyond it; the 25% crossing is a surge on its own.
- `minutes_spike` and `minutes_drop` carry `through_scoring_period` in `detail`, and the pass skips one that names the same game as the last event of that kind for the player. A spike is therefore recorded once, and again only when a new game moves the window.
- `return_date_changed` needs a date on both sides; a date appearing with `went_out` or vanishing with `returned` is part of those events.
- `upgraded` uses a severity order OUT = SUSPENSION < DOUBTFUL < QUESTIONABLE = DAY_TO_DAY < PROBABLE < ACTIVE. A move between two equal ranks is no event.

### 3.6 Cadence and the pass

`scripts/status_pass.py`, run by `scripts/scheduled_status.sh`, with `deploy/fcp-core-status.{service,timer}`. Three firings, `OnCalendar` listed three times in one timer:

| label | UTC | why |
|---|---|---|
| `morning` | 15:00 | 10:00/11:00 Eastern; overnight news and the morning shootaround reports |
| `report` | 22:30 | 17:30/18:30 Eastern, after the 17:00 Eastern injury report |
| `late` | 00:30 | 19:30/20:30 Eastern, game-time decisions for 19:00 and 19:30 tips |

The 09:00 UTC nightly ingest still runs and should also write a snapshot (`pass_label = "nightly"`), so status is observed four times a day during the season. The pass:

1. Load the league (`fetch_current_league`).
2. Fetch the whole pool once, paged (§3.3). Four to five requests.
3. Write `player_status_snapshots` for every entry and `free_agent_snapshots` for the unowned ones.
4. Rewrite `pro_team_games` for the season.
5. Diff against each player's previous snapshot; write `player_status_events`.
6. Fetch news for players with a new event plus the tracked team's roster (§3.2). Bounded, a few dozen requests.
7. Record an `IngestRun` with `mode = "status"` and the pass label and counts in `detail`.

Requests per pass: under 60. Duration: seconds. The script reuses the schema-at-head guard, the database wait and the exit codes from `scheduled_ingest.sh` verbatim; factor those into a shared shell include if it avoids copying.

Out of season (no `pro_team_games` in the next 14 days) the pass should still snapshot once a day at most and skip news; preseason status changes matter for the draft but not three times a day.

**As built:** the pass labels itself from the clock (`app/listener/status.label_for`, the nearest slot within an hour, else `adhoc`), and `scheduled_ingest.sh` runs `status_pass.py --label nightly` after the ingest, so a fourth snapshot needs no change to the ingest itself. The shared shell pieces are in `scripts/scheduled_common.sh`. The season row is written from the league's settings if the ingest has not made it yet. A skipped off-season pass still records a run, with `skipped` in its detail, so the health route sees the listener alive.

### 3.7 Tests for layer 1

- `tests/fakes.py`: `fake_pool_entry(...)` building one raw `kona_player_info` entry with `ownership`, `injuryStatus`, `onTeamId`, `status`; extend `fake_league` with `pro_schedule`.
- Two snapshots for one player, each event rule fires and no other does; a first snapshot fires nothing.
- `minutes_spike` from a constructed game-stat series, including the "fewer than 3 games" refusal.
- The pool fetch pages until a short page and stops.
- Widening the stats scope adds free-agent ids for a `recent` scope and not for a full one.
- `test_scheduled_ingest.py`'s pattern for the new shell script: refuses when the schema is behind.
- Migration `0014_listener.py` upgrades and downgrades against `fcp_test`.

---

## 4. Layer 2: the recommender

Module `app/pickups/`. Pure functions taking a `Session` and returning dataclasses; no HTTP, no printing. Everything is for one `(league_season, team)`.

### 4.1 Shared state: `app/pickups/state.py`

```
@dataclass(frozen=True)
class TeamWeek:
    team_id: int
    matchup_period: int
    scoring_periods_remaining: tuple[int, ...]   # today through the period's last day
    opponent_team_id: int | None                 # None on a bye
    my_totals: dict[str, float]                  # from matchup_team_stats for the live matchup
    opp_totals: dict[str, float]
    roster: tuple[RosteredPlayer, ...]           # from the latest daily_lineup_slots
    faab_remaining: int                          # auction_budget minus winning bids this season
    open_slots: int                              # roster_size minus rostered, IR handled separately
    ir_slot_free: bool

@dataclass(frozen=True)
class RosteredPlayer:
    player_id: int
    name: str
    pro_team_id: int
    eligible: frozenset[str]
    position: str | None
    injury_status: str | None
    expected_return_date: date | None
    games_remaining_this_period: int             # from pro_team_games, minus days ruled out
    on_ir: bool
```

`my_totals` and `opp_totals` come from `matchup_team_stats` for the current matchup. Confirm during phase 2 that the `--recent` ingest writes the in-progress matchup's stats; `ingest_matchups_and_rosters` is believed to, since the box score view returns running totals, but it has only ever been run on finished periods.

**As built** (2026-09-18, `app/pickups/state.py`): `my_totals` and `opp_totals` are `CategoryLine`s of raw counts (FGM/FGA and FTM/FTA included) rather than dicts, so a percentage is rebuilt and never averaged. `faab_remaining` is `acquisition_budget` (the 100 FAAB pot) less executed bids; `auction_budget` above is the draft's 200 and was the wrong field. `RosteredPlayer` carries `game_days`, the remaining scoring periods on which he has a game he is not ruled out of, and `games_remaining_this_period` is its length. Injury status, return date and NBA team come from the latest status snapshot; a player the listener has never seen takes his team from his last `roster_slots.pro_team`. Before the season's first lineup day the roster is the snapshots' `on_team_id`, with nobody on IR. OUT with no return date is out for the whole period, which matches `startable`. `load_free_agents` reads every row of the latest pass of `free_agent_snapshots`, since that table only ever holds the unrostered. `season_calendar` dates a scoring period from the stored schedule, for the CLI's default day and for the knowable line's `as_of`. The in-progress matchup's running totals are still to be confirmed on the live database.

### 4.2 Rest-of-season projection per player: `app/pickups/projection.py`

A `PlayerProjection` for the remainder of the season, built as:

- **Per-game rates**: a shrinkage blend of the preseason rate and the season-to-date rate,
  `rate = (k · pre + gp · season) / (k + gp)`, with `k = 20` games as the starting prior weight. Preseason is BBM's rate where the manager has loaded a file for the season (`app/draft/bbm.py`), else ESPN's `kind = "projected"` divided by projected games. Season-to-date from `player_game_stats` where `played`.
- **Recent-form tilt**: if a `minutes_spike` or `minutes_drop` event is live (within the last 10 days), scale counting rates by `recent_minutes / season_minutes`, capped to [0.6, 1.5]. This is the role-change signal. Keep it a separate, switchable factor so the backtest can measure whether it helps.
- **Games**: count `pro_team_games` from today to the end of the fantasy season, exclude games before `expected_return_date` when status is OUT, and multiply the rest by a league availability factor. Use 0.96 when the rate came from BBM and 0.88 when from ESPN, the two figures measured in the `bbm.py` docstring. Do not stack.

Percentages are carried as FGM/FGA and FTM/FTA totals, the way `valuation.py` already does, so a roster's FG% is rebuilt from components.

**As built** (2026-09-18, `app/pickups/projection.py`): the per-game rate is the knowable line (`app/scoring/knowable.py`: season to date shrunk to the projection with `PRIOR_GAMES` 15, then 15% of the last fortnight), fitted on 7,165 checkpoints and better on the next 28 days than the `k = 20` blend above (2.85 against 2.90), so that blend is not implemented and BBM is not consulted; the rate is ESPN-derived, so the availability factor is always 0.88. The tilt is as designed and switchable (`tilt=`), keyed on the listener's own `minutes_spike` and `minutes_drop` events: live while the event's `through_scoring_period` is within 10 days of today, factor = the event's recent mean over his season mean from `player_game_stats` (the event's own prior mean when he has no games stored), capped to [0.6, 1.5], applied to every count so the percentages stand. Games are the caller's to count (`state.playable_days`); `rest_of_period_line` applies no availability factor, because the days left in a week are a known schedule, and `rest_of_season_line` applies 0.88 once.

### 4.3 Short term, this week: `app/pickups/stream.py`

Answer: *which swap most improves my expected category wins in the current matchup period.*

1. For each rostered player and each free agent, expected remaining line this period = rate × `games_remaining_this_period`.
2. My projected week = `my_totals` + Σ remaining lines. Opponent's projected week likewise from their roster.
3. For each category, P(I win) = Φ((mine − theirs) / σ_cat), with σ_cat the standard deviation of the *remaining-days* portion. Use the `CategoryDistribution` per-week σ scaled by `sqrt(days_remaining / period_days)`. This is a head-to-head against a known opponent, so it is a different shape from the draft objective's "against the field", but the same `_normal_cdf`.
4. Expected wins now = Σ P. For every (drop, add) pair with the add fieldable (`fieldable`, `within_position_limits`) and affordable, recompute. Also evaluate add-only into an open slot, and add-with-IR-move when `ir_slot_free` and a rostered player is OUT.
5. Rank by Δ expected wins. Report the top five with the categories that moved and the games each player has left.

**Empty-day check**, reported separately: any remaining scoring period on which a starting slot has no player with a game while a free agent does. This is the most common streaming reason and does not need the probability model.

**Hurdle**: recommend only when Δ expected wins ≥ 0.10 categories, or an empty day is filled. Below that, say so.

**As built** (2026-09-18, `app/pickups/stream.py`, `scripts/stream.py`): step 1 counts starts, not games. Each remaining day is a matching of the players with a game to the lineup (`app/inseason/startable.py`), seated in order of a per-game weight (counts over each category's spread, turnovers against, percentages left out), which is the exact best seating because the seatable sets form a transversal matroid; a four-game free agent on days the lineup is already full adds nothing. σ is the `CategoryDistribution` spread scaled by `sqrt(days_remaining / period_days)`, settled as a step when no days are left. A swap is legal when the roster respects the position limits and still seats at least as much of the lineup as before, so a roster already short somewhere can still make a move that does not make it shorter. FAAB affordability is not enforced (a claim can be a $0 bid); `faab_remaining` is reported beside the moves. An IR move needs the status OUT exactly, which is what ESPN admits to IR. The five reported are the best move per added player, so five pickups are named rather than one pickup with five drops. A filled empty day clears the hurdle only with a positive Δ, since dropping a starter for a body that plays on the empty day fills it and loses the week. The pool is the latest pass's wire cut to the top 80 by weight times games left; `pool=` and `distributions=` are overrides for the tests and the backtest. On a bye the report carries the empty-day check and no moves. The knowable line is queried per player, so a run costs a few hundred small queries; fine for a CLI and the digest, and to be batched if the backtest minds.

**As built, second pass** (2026-09-18, `app/pickups/judge.py`): the ranking and the hurdle no longer read this week alone. Every move carries a `Judgement` — this week's Δ, the change per week over the rest of the season, the matchup weeks after this one, and the net of the three — and `Move.clears` reads the net. `STREAM_HURDLE` keeps its number and its unit (categories) and changes its meaning: worth making on the season, not only on Sunday. The season term is `value(dropped) − max(value(added), wire_replacement)`, negated: what a man leaving is worth to an ordinary roster place, less what the place gets back, since the place never goes empty. `value()` is the league-standard lens of `app/scoring/players.py` — his rest-of-season weekly line inside `average_team_line`, through `app.scoring.value.marginal` — because a drop charge is about what a man is worth to whoever picks him up, not to the team letting him go. `wire_replacement` is the best free agent still on the wire after the one being added, floored at `TYPICAL_PICKUP` (0.06 categories a week, the lowest recent season's median in `app/scoring/replacement.py`). So streaming a fringe player costs about nothing and dropping a real one is charged his gap to the wire, which is what stops a starter being traded for a body that fills an empty day. Every report also carries the projected end-of-season category record with the move and without it: categories banked in settled matchups, plus the expected categories a week over the weeks left. The difference between the two records is exactly the net.

### 4.4 Long term, rest of season: `app/pickups/season.py`

Answer: *who on the wire would make my roster better for the rest of the year, and who should go.*

Reuse the optimizer directly. Build `Candidate`s with `weekly = projection / weeks_remaining` (see `pool.effective_weeks`) for the roster plus the top N free agents by preliminary value (`value_players`, N ≈ 60), and call `optimize(candidates, distributions, budget=0, roster_slots=roster_size, locked=roster_ids_to_keep, ...)` with every candidate priced at `minimum_bid = 0`. Price is not the constraint in season; roster places are. The current roster is the `starts` seed so the with/without comparison is neighbours, exactly the reasoning in `optimize`'s docstring on `starts`.

Report the single best swap and the best two-swap, each with Δ `expected_wins` per week and the categories that moved. `distributions` come from `category_distributions` for this season's size, the same as the draft.

**Drop candidates** are the roster members whose removal (replaced by the best available free agent) costs least; the bottom three, with the Δ for each.

**Stash candidates**: free agents whose `injury_status` is OUT with `expected_return_date` inside the next 6 weeks and whose healthy rest-of-season value would rank in the top N. From 2027 the league has one IR slot, so stashing is a real strategy here for the first time; when `ir_slot_free`, a stash is an add with no drop.

**Hurdle**: Δ expected wins per week ≥ 0.05 for a swap that costs FAAB, ≥ 0.02 for a free (waiver-cleared) add into an open slot. Starting values; the backtest (§4.6) sets them.

**Volume guard**: report a rolling count of the team's adds over the last 14 days beside every recommendation, with the league's own finding that heavier churn returned less per move.

**As built** (2026-09-18, `app/pickups/season.py`, `scripts/season.py`): the optimizer is asked the same question three ways rather than once. `locked` is the roster less the men a move would drop, `excluded` is those men, `starts` is the current roster and `roster_slots` is exactly the places the move leaves open, so a single swap's answer is the exact best replacement rather than a local search's. `minimum_bid=0` and `restarts=0` go with `budget=0`: a floor of a dollar a place under a budget of nothing leaves a roster short, and a shuffled start would only cost time when all but one or two places are locked. The three drop candidates are the same computation read from the other end, so the best swap and the cheapest drop are one number. The horizon is the rest of the **regular season** from the stored matchup periods, or the playoff periods once it is over, and `weeks_remaining` is its days over seven; `rest_of_season_line` counts each man's games over the same window, so the weekly line is games a week over the stretch the report plans for. The pool is ranked by the rest-of-season line's `weight` rather than `value_players`, which needs no z-score pool and is the ordering `stream` and `bids` already use. A move that drops a player is charged the paid hurdle, an add into an open place the free one, which is the note's rule stated in terms of the move rather than the waiver state. Every move that clears its hurdle carries a `Bid` (§4.5). A stash's healthy value counts every game his NBA team has left, ignoring the injury, because that is the question a stash asks.

**As built, second pass** (2026-09-18): every `Swap` carries a `Judgement` too. Its week half is the streaming report's own head-to-head (`app.pickups.stream.week_deltas`, so the two halves of the recommender cannot disagree about what a week is worth) and its season half is the optimizer's Δ per week, which is already a with-and-without over the whole roster. `Swap.clears` reads the net spread over the weeks it covers (`Judgement.per_week`), so `SEASON_HURDLE_PAID` and `SEASON_HURDLE_FREE` keep the unit and the scale they were written in — categories a week — while the quantity they gate is now both horizons. `horizon` and `weeks_between` moved to `app/pickups/judge.py`, since the week's report needs the same horizon to know how many weeks a drop is charged over; they are re-exported here.

### 4.5 What to bid: `app/pickups/bids.py`

From `transactions` and `transaction_items` for this league across seasons with FAAB (2026 onward, `league_seasons.auction_budget`): for each winning waiver claim, the bid and the claimed player's value rank at the time (rest-of-season value among free agents that day). Fit the median and 75th percentile winning bid by value-rank bucket (1-5, 6-15, 16-40, 41+). Recommend the 75th percentile when the swap's Δ is above twice the hurdle, the median otherwise, capped by `faab_remaining` and never more than a share of remaining budget proportional to weeks remaining. Report the historical range so the number is checkable. One season of FAAB is thin; say so in the output until 2027 adds a second.

**As built** (2026-09-18, `app/pickups/bids.py`): the FAAB seasons are found by `league_seasons.uses_faab`, not `auction_budget`, which is the draft's pot (the same correction §4.1 made). The claim's rank is taken on the **per-game** knowable line's `weight`, not the rest-of-season line's: a rank is ordinal, every NBA team plays the same 82 games, so over the rest of a season the two orderings are the same, and `pro_team_games` only exists from 2027 while the one FAAB season on record is 2026. A day's wire is §4.6's definition widened from the day to the matchup period, because a free agent only has a line on the days his NBA team plays; the claimed player is added to it whatever the lineup rows say, since being claimed is what proves he was free. Before the ranking, the day's wire is narrowed to the 120 men with the most composite production in the last fortnight: a rank past 41 changes no bucket, so the pre-filter cannot move a claim between buckets and it keeps a fit to seconds. The tilt is off in the fit (it keys on listener events, and no fitted season has any). The share cap is rounded up, so a single week left can still buy something, and the output names which cap bound it. The fit is cached per league season and re-read when the number of claims on record changes, because the API is a long-running process and a season gains claims every week.

### 4.6 Backtest against 2026: `scripts/pickups_backtest.py`

Replays the 2026 season day by day for one or every team:

- Free-agent pool on day N: players with a game line that day and no `daily_lineup_slots` row that period, the definition already used in `waiver_value.py`. (Injury status is unavailable historically; the backtest runs with status treated as unknown, which under-serves the stash logic and says so.)
- Run §4.3 and §4.4 with data through day N only.
- Score each recommended swap by the added player's minus the dropped player's actual composite over the next 7 days (streaming) and 30 days (season), the same `COMP` as `acquirable_value.md`, and by whether the swap was net positive.
- Baseline: the league's actual swaps that season, mean **0.57 composite per day**, win rate **53.8%** (swap-only, 2026).

Acceptance: recommended swaps above the hurdle beat the baseline on both mean and win rate, and the "no move" rate is above zero on most days. Tune the hurdles and `k` here, record the chosen values and the resulting table in this document, as the other studies do.

**As built** (2026-09-18, `scripts/pickups_backtest.py`): the score is **categories, not composite**. The first cut scored composite and read −3.2 a day at a 20% win rate; that is a measurement of composite rather than of the recommender, which spends points on purpose to win the categories that are close. A move is now scored by replaying the matchup that actually happened with the swap in it: the dropped man's started lines come out from day N on, the added man's real box scores go in on the days that are left (capped at the starts the place had), the nine totals are rebuilt and counted against the opponent's real period totals, and the categories the team actually won are subtracted. +1 means the swap flipped one category. The season score is the same replay over the next 30 days across every matchup period the window touches. The baseline is the league's own one-for-one swaps replayed **backwards** and negated, so a real claim and a recommendation are one quantity; `docs/acquirable_value.md`'s +0.57 composite a day is a different quantity and is no longer quoted as a target. Beside them the sweep reports what the recommender **claimed** (the judgement's net) against what it **delivered**, which is the calibration question a win rate cannot answer. What the replay cannot do is re-run the daily lineup matching for the swapped roster, so a pickup who fills an empty day is measured pessimistically; the write-up says so. The two data defects of the first cut (no 2026 schedule, the matchup totals leaking the whole period) are still worked around by attribute substitution, unchanged.

### 4.7 Tests for layer 2

- `TeamWeek` construction from fakes: remaining days, bye handling, IR flag.
- Projection blend: gp = 0 returns the prior; gp → large converges on the season rate; OUT with a return date removes exactly the games before it.
- Streaming: a constructed week where one category is a coin flip and one free agent with three games left flips it; the empty-day check finds a slot with no game.
- Season: the swap the optimizer returns on a two-player pool is the obviously better one; the hurdle refuses a marginal one; the volume guard counts adds in the window.
- Bids: bucket fit on a constructed transaction set; the cap by remaining budget.

---

## 5. Layer 3: delivery

### 5.1 API routes, `app/api/pickups.py`

Read-only, keyed on ESPN ids like the rest of the API.

| route | returns |
|---|---|
| `GET /leagues/{id}/seasons/{yr}/teams/{tid}/pickups` | `{week: StreamAdvice, season: SeasonAdvice, drops, stashes, bids, churn, generated_at}` |
| `GET /leagues/{id}/seasons/{yr}/teams/{tid}/week` | the `TeamWeek` state: live totals both sides, P(win) per category, games remaining per player |
| `GET /leagues/{id}/seasons/{yr}/free-agents` | latest snapshot with rest-of-season value, sortable, `{items, total, limit, offset}` |
| `GET /leagues/{id}/seasons/{yr}/events?since=&kinds=&team=` | `player_status_events`, newest first, `team` filters to a roster or to free agents |
| `GET /players/{pid}/status` | snapshot history, one row per day per pass |
| `GET /players/{pid}/news` | stored news, newest first |

Schemas in `app/api/schemas.py`. Follow the existing rule: bounded collections return a list, growing ones return the `{items, total, limit, offset}` envelope.

**As built** (2026-09-18, `app/api/pickups.py`): the recommender's two routes are

| route | returns |
|---|---|
| `GET /leagues/{id}/seasons/{yr}/teams/{tid}/pickups/stream?today=N` | `StreamReportOut`: the week both sides project to, P(win) per category, the moves with the categories each one shifts and its bid, the empty days, and `recommended` (null when nothing clears the hurdle) |
| `GET /leagues/{id}/seasons/{yr}/teams/{tid}/pickups/season?today=N` | `SeasonReportOut`: the roster's ordinary week, the best add, swap and two-swap each with its own hurdle and bid, the drop candidates, the stashes, and the churn guard |

Two reports rather than one `/pickups`, because they answer different questions on different horizons and a caller usually wants one of them; `/week` and `/free-agents` are still to come, and the `TeamWeek` state is visible inside the stream report meanwhile. `today` is a scoring period and defaults to the calendar day turned into one through the stored NBA schedule. Player ids go out as ESPN's, like the rest of the API. An unknown team is a 404 (`TeamDep`); a season the listener has never run for — no `pro_team_games`, no `player_status_snapshots` — is a **409**, because with no schedule, roster or wire there is nothing to decide from, and that is a different answer from "no move is worth making". Neither route makes an ESPN request. Both reports are bounded, so both return an object rather than a `Page`.

### 5.2 The digest, `scripts/digest.py`

Runs after the `morning` pass (chain it in the same service, or a second `ExecStart`). Plain text, under 40 lines:

1. Events since the last digest touching the tracked team's roster (went out, returned, return date changed, minutes drop).
2. Events on free agents worth a look: minutes spikes, ownership surges, dropped by a rival, waivers clearing today, with each one's rest-of-season value rank.
3. This week: category state in one line each, the top streaming swap if it clears the hurdle, or "no stream today".
4. This season: the top swap if it clears the hurdle, drop candidates, stash candidates, a bid.
5. Churn: adds in the last 14 days.

Mark `notified_at` on the events it included. Delivery is one HTTP POST to a notification service the manager chooses. The environment variable is `FCP_DIGEST_URL`; with it unset the script prints and exits 0, which is also how it is tested. Alerts between digests (a `went_out` on the tracked roster from the `report` or `late` pass) go to the same URL as a one-liner. Which service is a decision (§10); ntfy and a Telegram bot both take one POST.

Tracked team: `FCP_TRACKED_TEAM_ID` in `.env`, the ESPN team id. One team for now; the routes take any team.

**As built** (`app/digest.py`, `app/notify.py`, `scripts/digest.py`):

- Sections 1, 2 and 5 are in; 3 and 4 wait on the recommender. A free agent carries his percent owned rather than a value rank, for the same reason.
- Section 1 is followed by where the roster stands now, one line per player carrying a status and a count of the rest. That is the part worth reading on a morning when nothing changed.
- An event is filed by where its player is *now*: on the tracked roster it is roster news, unrostered it is wire news, on a rival's roster it is neither. So a rival's injury is never reported, and a player a rival drops becomes wire news by himself.
- `notified_at` is set only after a delivery succeeds. A dry run, a missing URL or a refused POST all leave the events unmarked, and the next message repeats them.
- Everything the digest reports on is marked, including what it summarises as "and N more"; the events route has the full list. Kinds the digest never shows are never queried and never marked.
- Caps keep it under forty lines: eight roster events, ten wire ones, eight status lines.
- The notification service stays the manager's choice. `app/notify.py` posts the text as the body, which is ntfy's API; `FCP_DIGEST_CHAT_ID` switches it to Telegram's JSON shape. Nothing retries.
- Alerts are `went_out` on the tracked roster only, one line each. `URGENT_KINDS` is one constant, so widening it later is one line.
- `scheduled_status.sh` runs the digest after the morning pass and `--alert` after the others, so no second timer was needed.

### 5.3 Deploy

New units named `fcp-core-status.{service,timer}` in `deploy/`, following `fcp-core-ingest.*` (User `aisha`, `WorkingDirectory=/opt/fcp-core`, the same hardening lines). The host is shared with the live production stack, so keep the `fcp-core-` prefix and touch nothing outside `/opt/fcp-core`. Deploy is still `git pull && alembic upgrade head`, and the schema guard in the pass script refuses to run otherwise.

Update `STATUS.md` "Works today" with the new tables, routes and timer when each phase lands, and `GET /ingest-runs/health` should understand `mode = "status"` so a silent listener is visible.

---

## 6. Layer 4, later

- **News summarisation.** An LLM pass over new `player_news` rows producing a structured line per item: role change yes/no, expected return, severity, confidence. Store beside the news row. Useful, but only once rows exist. Load the `claude-api` skill before writing it; model ids and pricing are not to be recalled from memory.
- **A frontend.** The pickups route is the first thing worth a screen. It forces the auth question, as `STATUS.md` says.

---

## 7. Phases and acceptance

| phase | deliverable | done when | target | state |
|---|---|---|---|---|
| 1 | Listener tables, pool fetch, event diff, status timer, `IngestRun mode=status`, `/events`, `/players/{pid}/status` | Three passes a day recorded on the VPS for a week; events appear for real status changes; tests in §3.7 pass | before 2026-10-20 | written 2026-09-17, tests pass; the VPS week is still owed |
| 1b | Digest, text only, tracked team | Morning message arrives with events and a roster status line | opening week | written 2026-09-17; the first real message is still owed |
| 2 | `TeamWeek`, streaming recommender, empty-day check, `/week`, digest section 3 | Backtest §4.6 on the 7-day horizon beats the baseline; live output sane for two weeks | November | written 2026-09-18; the backtest, `/week` and the digest section are owed |
| 3 | Rest-of-season recommender, drops, stashes, bids, `/pickups`, `/free-agents`, digest section 4 | Backtest on the 30-day horizon beats the baseline; hurdles recorded here | December | written 2026-09-18 (`pickups/season`, `pickups/stream` routes); the backtest, `/free-agents` and the digest section are owed |
| 4 | News summarisation, then a frontend | | 2027 | design |

Phase 1 is the only one with a hard date. Phases 2 and 3 can be built entirely against 2026 data in the test database and on a laptop.

Neither phase 1 nor 1b is *accepted* yet: both acceptance tests are about what happens on the VPS against the real ESPN, and neither has run there. What is done is the code, its tests, and the deploy steps.

---

## 8. Conventions for the implementer

- Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic. `ruff` and `mypy --strict` must pass; `pytest` runs against `TEST_DATABASE_URL`, whose name must end in `_test`.
- Next migration is `0014`. Autogenerate from the ORM, then read it.
- Domain logic in `app/`, never in routers or scripts. Scripts are thin.
- Fakes in `tests/fakes.py`, extended rather than duplicated. No test touches ESPN.
- Nothing here writes to ESPN. Every request is a GET with the manager's own cookies from `get_espn_settings()`.
- Keep request counts in the `IngestRun.detail` so cost is visible.
- Commit to `main`. Update `STATUS.md` in the same commit as the feature.
- When a number is chosen (a threshold, a prior weight), write it in this document with how it was chosen, as the other `docs/` notes do.

---

## 9. Why social sentiment is deferred

The X/Twitter API is paid and its terms change; Reddit is noisy and unstructured; both need an LLM to turn text into a signal, and the signal then needs validating against outcomes. Meanwhile three quantitative signals are free on the ESPN card or already in the database: the status change itself, the 24-hour ownership move (the aggregated behaviour of every ESPN manager), and the minutes trend. The official NBA injury report is the upstream of nearly every injury tweet and is what ESPN's status reflects. Build on those, measure what the recommender gets wrong, and add a text source only if the misses are the kind a text source would have caught.

---

## 10. Decisions taken here, and the ones left open

Taken:

- Store every snapshot, not only changes.
- One unfiltered paged pool fetch per pass, split on `onTeamId`, rather than separate free-agent and roster calls.
- News fetched only for players with an event and for the tracked roster.
- The recommender reuses the draft optimizer's objective and search rather than introducing a second value system.
- Hurdles exist and are tuned by backtest, not set by feel.
- No auth, no frontend; a team id parameter.

Open, to be decided when the phase is reached:

- Exact ESPN field names for `onTeamId`, `status` and the waiver clear date (probe first).
- Event thresholds: ownership ±5.0 and minutes ±8.0 are starting values.
- Prior weight `k = 20` in the rate blend.
- Streaming and season hurdles, 0.10 / 0.05 / 0.02, pending the backtest.
- Notification service. ntfy is the least setup; Telegram is the nicest phone experience.
- Whether the `late` pass at 00:30 UTC earns its keep, or two passes suffice. Measure how many events it produces that `report` did not.
- Whether the recent-form tilt in §4.2 improves the backtest or just adds noise. It is switchable so this can be measured.
