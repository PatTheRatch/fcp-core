# FCP Core Status

## Works today

- FastAPI application boots (`create_app()`)
- `GET /health` returns `{"status": "ok"}`
- Local PostgreSQL 16 via Docker Compose (`fcp` and `fcp_test` databases)
- Alembic migrations (one empty initial revision; upgrade to head verified by test)
- Typed config: `DATABASE_URL` and `TEST_DATABASE_URL` required, fail loudly if missing
- Quality gates: pytest, Ruff, mypy (strict)
- `scripts/espn_probe.py` fetches one ESPN league (settings + team list),
  read-only, no persistence. Confirmed against a live ESPN response on
  2026-09-12 (league 3853870, season 2026): auth cookies work, the timeout
  patch holds, and settings + all 14 teams come back populated.

- League structure persisted: `leagues`, `league_seasons`,
  `league_season_categories` (migration 0002).
- Teams, owners, matchup periods, matchups and rosters persisted: `teams`,
  `owners`, `team_owners`, `matchup_periods`, `matchups`, `players`,
  `roster_slots` (migration 0003).
- Per-category matchup detail persisted: `matchup_team_stats` (migration
  0004). Every statistic each team posted in each matchup, covering the nine
  scored categories and the four component stats behind the percentages.
- Player statistics per scoring period persisted: `player_game_stats`
  (migration 0005). A box score line per player per day, global rather than
  league-scoped, keyed on (player, season, scoring period).
- `scripts/ingest_league.py` writes one whole season and is safe to re-run.
  Verified against the live league on 2026-09-12: 14 teams, 15 owners,
  22 matchup periods, 157 matchups, 348 players, 4436 roster snapshots and
  4004 matchup statistics and 28215 player game lines (20431 with a stat
  line), in about 50 seconds. A second run changed no row counts.

## Building now

- Deciding what to build on top of the stored season

### Why the schema is season-scoped

ESPN settings are per season, not per league: between years a league can
change its name, team count, playoff format and scored categories. So
`leagues` holds only the durable ESPN id, and everything that can change
hangs off `league_seasons`, one row per (league, season). Re-ingesting a
season updates it in place; a new season inserts alongside and leaves prior
seasons untouched.

Two deliberate consequences:

- Scoring categories are rows, not columns, so nine-cat becoming eight-cat is
  a data change rather than a migration.
- `league_seasons.raw_settings` (JSONB) keeps the payload ESPN returned, so a
  field we do not model yet can be backfilled without re-fetching a season
  that may no longer be available.

### What the live probe taught us

- `scoring_type` is `H2H_CATEGORY`, with 9 scoring categories
  (statIds 0, 1, 2, 3, 6, 11, 17, 19, 20) in `settings._raw_scoring_settings`.
  `espn_api` exposes no parsed category list — the raw dict is the only source.
- `team.wins` / `losses` / `ties` are **category** tallies, not matchup records.
  They sum to 171 per team = 19 matchup periods x 9 categories. A matchup
  record has to be derived; it is not a field ESPN hands back.
- `team.outcomes` is empty for this league, so per-period results must come
  from the schedule/matchup endpoint rather than the team object.
- `settings.matchup_periods` has 22 entries while `reg_season_count` is 19;
  the extra 3 are playoff periods. The two numbers are not interchangeable.
- `team.team_id` is sparse and non-contiguous (1, 3, 11, 12, ... 27), so it is
  a real external key, not a row index.
- `team.owners` is a list of dicts and can hold more than one owner, so the
  team-to-owner relationship is many-to-many, not a single column.

### What the box scores taught us

- Box scores, not `team.schedule`, are the right enumeration. Each matchup
  appears once instead of twice, lineups arrive in the same response, and
  schedule length varies by team (21 for some, 22 for others).
- A bye is reported as opponent team id `0` with winner `UNDECIDED`. Stored
  as a matchup with a null away team. There are six, all in the playoffs.
- `box.home_wins` / `away_wins` / `home_ties` are the categories won in that
  one matchup, and they do sum to the category count.
- **A matchup record is now derivable**, which it was not before. Summing
  matchup winners over the regular season gives, for example, 14-5-0, against
  a category tally of 106-62-3 for the same team.
- `espn_api` reports `lineupSlot` as "PG" for every player in every period
  (601 of 601 checked). It is a parsing bug upstream, so starter-versus-bench
  is not recoverable and no lineup slot column exists.
- ESPN's `matchupPeriods` map claims one scoring period per matchup period,
  which the box scores contradict (period 1 reports scoring period 6).
  `matchup_periods.final_scoring_period` records what the box score said
  rather than inventing a mapping.

### What the per-category detail taught us

- `box.home_stats` carries 13 statistics: the 9 scored categories plus FGM,
  FGA, FTM and FTA. The components mean a stored percentage can be
  recomputed or re-weighted instead of being taken on trust. FG% recomputes
  from its components to within 5e-9 across all 308 sides.
- A statistic is a scored category when it links to a row in
  `league_season_categories`, never because `result` is non-null. On a bye
  ESPN reports real values with a null result on all 13 statistics.
- Percentages are ratios, not percents: FG% arrives as 0.457.
- Turnovers invert: the side with fewer turnovers gets `result = WIN`, even
  though `isReverseItem` is false in the scoring settings. Trust the result,
  not the flag.
- Cross-check passed: tallying per-category WIN, LOSS and TIE reproduces the
  separately stored matchup totals for 151 of 151 contested matchups.

### What the player cards taught us

- `player_info` batches. ESPN returned all 348 players of this league in a
  single call in about 3 seconds, so per-player fetching is unnecessary.
  Ingest batches at 100 to keep request size bounded on larger leagues.
- A player card carries roughly 82 numeric keys, one per scoring period,
  each with a date, the opposing team and 45 statistics. The non-numeric
  keys ("2026_total", "2026_last_7") are season rollups and are skipped.
- The `team` field on a card entry is the OPPONENT. Across 29 distinct
  values for one player, their own team never appeared.
- Rows are kept for days a player's team played and they did not, flagged
  `played = false`. Absence of a row means no fixture; it should not have to
  double as "did not play".
- Dates arrive with no offset and are read as UTC. The stored range,
  2025-10-22 to 2026-04-13, is exactly the NBA regular season.
- Rate stats (PPG, FG%, MPG) stay in `raw_totals` only. For a single game
  they either duplicate a counting stat or divide by one.

## Next

1. An API surface over the stored season
2. Ingesting prior seasons, which the schema already allows
3. Deciding whether starter-versus-bench is worth recovering from another
   endpoint, since it blocks exact reconciliation (see below)

## Open questions

- Rosters are snapshots per matchup period, which is the finest grain the box
  scores expose. Daily roster movement within a period is not recoverable
  from this source.
- ESPN omits the date and opponent on about 4.7% of played lines (969 of
  20431). The statistics are present and correct; only the game context is
  missing. Verified as an upstream gap, not a parsing fault.
- Daily player lines do **not** reconcile exactly with the weekly team
  totals, and should not be presented as validating each other. Two known
  causes: only started players count toward a team's category total and
  `lineupSlot` is unusable, so the bench cannot be excluded; and the
  scoring-period window per matchup period is approximate, because ESPN's own
  period mapping is inconsistent. Of 294 sides compared, 36 matched exactly
  and 230 had a roster sum above the team total, which is the direction the
  bench explanation predicts.

## Open questions

- Rosters are snapshots per matchup period, which is the finest grain the box
  scores expose. Daily roster movement within a period is not recoverable
  from this source.

## Not building yet

- frontend
- auth
- projections
- optimizer
- newsroom
- AI features
