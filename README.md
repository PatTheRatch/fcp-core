# FCP Core

The backend for **Full Court Press**, a fantasy basketball intelligence platform.

This is a greenfield rebuild. The first milestone is: given an ESPN fantasy league,
ingest its real structure and data into PostgreSQL in a clean canonical form we can
query and reason from. Everything else (projections, streaming, trades, drafts,
simulations) comes later and builds on that.

The older repository, `PatTheRatch/fantasy-ball-is-life` (V1 on `main`, a previous
rebuild on `v2`), is **reference material, not a dependency**. Nothing here imports
from it. We consult it for proven basketball behaviour, ESPN API discoveries, and
tests that captured real bugs. We do not port its architecture.

See [STATUS.md](STATUS.md) for what works today and what is next.

## Stack

Python 3.12+, FastAPI, SQLAlchemy 2.x, PostgreSQL 16, psycopg 3, Alembic, pytest, Ruff, mypy.

## Install

```bash
uv venv --python 3.12 && uv sync --extra dev
# or, without uv:
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Then activate the venv (`source .venv/bin/activate`) or prefix commands with `.venv/bin/`.

## Configure

```bash
cp .env.example .env
```

Two values are required and there are no defaults. The app refuses to start without them:

| Variable            | Purpose                                  |
| ------------------- | ---------------------------------------- |
| `DATABASE_URL`      | Application database (`postgresql+psycopg://...`) |
| `TEST_DATABASE_URL` | Disposable test database; its name must end in `_test` |

Never point either at a production database.

## Start PostgreSQL

```bash
docker compose up -d
```

This runs Postgres 16 with two databases, `fcp` and `fcp_test`, matching `.env.example`.

## Run migrations

```bash
alembic upgrade head
```

Migrations read `DATABASE_URL` from the environment or `.env`; `alembic.ini` carries no URL.
To create a new migration after changing ORM models:

```bash
alembic revision --autogenerate -m "describe the change"
```

## Run the API

```bash
uvicorn app.main:create_app --factory --reload
curl http://127.0.0.1:8000/health   # -> {"status":"ok"}
```

## Probe an ESPN league (read-only)

```bash
python scripts/espn_probe.py
```

Prints one league's settings and team list. Fetches only, nothing is persisted.
Needs `ESPN_LEAGUE_ID`, `ESPN_SEASON`, `ESPN_SWID`, `ESPN_S2` (see `.env.example`).

Note the per-team `W-L-T` is a count of *categories* won, not matchups won.
ESPN does not return a matchup record for a category league.

## Ingest a league season

```bash
python scripts/ingest_league.py
```

Persists one whole season to `DATABASE_URL`: league settings and scoring
categories, teams and their owners, matchup periods and the matchups inside
them, a roster snapshot per team per matchup period, every statistic each team
posted in each matchup, and a box score line per player per scoring period.

Safe to re-run: an already-stored season is updated in place, a new season is
inserted alongside it, and earlier seasons are never modified. It makes one
ESPN call per matchup period, so a full season is a couple of dozen requests
plus a handful of batched player-card calls, and takes roughly fifty seconds.

Two things the schema deliberately does not have. There is no lineup slot on
a roster row, because `espn-api` reports every player's slot as `PG`. There is
no season matchup record on a team, because ESPN does not report one; the
team's `categories_won` is a tally of categories, and a matchup record is
derived by counting winners in `matchups`.

In `matchup_team_stats`, a row is a scored category when
`league_season_category_id` is set. Do not use `result` for that test: a bye
reports real values with a null result on every statistic. Percentages are
stored as ratios, so FG% is `0.457`.

In `player_game_stats`, a row with `played = false` means the player's team
had a fixture and they recorded nothing, so do not treat a missing row and an
unplayed row as the same thing. `opponent` is the opposing team, never the
player's own. ESPN omits the date and opponent on a small share of otherwise
valid lines.

## Quality gates

```bash
pytest          # tests (needs TEST_DATABASE_URL reachable)
ruff check . && ruff format --check .
mypy
```

All three must pass before a change is done.

## Layout

```
app/          application code (config, API routes, database session)
migrations/   Alembic environment and versions
tests/        pytest suite
scripts/      small operational helpers (currently: test-db bootstrap for Docker)
```

## How we build

- Every change should make something runnable, observable, or testable.
- Product behavior comes before architecture.
- Existing V1/V2 code is reference material, not a template.
- No speculative infrastructure.
- No abstraction without demonstrated need.
- Keep PRs small enough for a human to understand.
- STATUS.md must describe reality.
- Prefer deleting complexity over documenting complexity.
