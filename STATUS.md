# FCP Core Status

## Works today

- FastAPI application boots (`create_app()`)
- `GET /health` returns `{"status": "ok"}`
- Local PostgreSQL 16 via Docker Compose (`fcp` and `fcp_test` databases)
- Alembic migrations (one empty initial revision; upgrade to head verified by test)
- Typed config: `DATABASE_URL` and `TEST_DATABASE_URL` required, fail loudly if missing
- Quality gates: pytest, Ruff, mypy (strict)
- `scripts/espn_probe.py` fetches one ESPN league (settings + team list),
  read-only, no persistence. Code path is verified up to the network call
  (correct URL, correct auth, timeout patch active) but not yet confirmed
  against a live ESPN response — needs a run from a machine with network
  access to espn.com.

## Building now

- Confirming the ESPN probe against a real league, then designing the first
  canonical tables from what it returns

## Next

1. Run `scripts/espn_probe.py` against a real league and confirm it works
2. Persist league structure
3. Persist teams / matchup periods / rosters

## Not building yet

- frontend
- auth
- projections
- optimizer
- newsroom
- AI features
