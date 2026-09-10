# FCP Core Status

## Works today

- FastAPI application boots (`create_app()`)
- `GET /health` returns `{"status": "ok"}`
- Local PostgreSQL 16 via Docker Compose (`fcp` and `fcp_test` databases)
- Alembic migrations (one empty initial revision; upgrade to head verified by test)
- Typed config: `DATABASE_URL` and `TEST_DATABASE_URL` required, fail loudly if missing
- Quality gates: pytest, Ruff, mypy (strict)

## Building now

- Backend foundation (this PR)

## Next

1. Connect to ESPN and fetch one real league
2. Persist league structure
3. Persist teams / matchup periods / rosters

## Not building yet

- frontend
- auth
- projections
- optimizer
- newsroom
- AI features
