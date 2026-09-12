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
  `league_season_categories` (migration 0002). `scripts/ingest_league.py`
  writes one season and is safe to re-run. Verified against the live league.

## Building now

- Persisting teams, matchup periods and rosters on top of `league_seasons`

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

## Next

1. Persist teams / matchup periods / rosters
2. Derive matchup records from the schedule endpoint (ESPN does not give them)

## Not building yet

- frontend
- auth
- projections
- optimizer
- newsroom
- AI features
