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
Needs `ESPN_LEAGUE_ID`, `ESPN_SWID` and `ESPN_S2` (see `.env.example`). The
season is derived from the date; `ESPN_SEASON` is optional and only pins a
run to a particular year.

Note the per-team `W-L-T` is a count of *categories* won, not matchups won.
ESPN does not return a matchup record for a category league.

## Ingest a league season

```bash
python scripts/ingest_league.py                  # the configured season
python scripts/ingest_league.py --season 2023    # one prior season
python scripts/ingest_league.py --all-seasons    # every season ESPN holds
python scripts/ingest_league.py --recent         # trailing days only, ~15s
```

Persists one whole season to `DATABASE_URL`: league settings and scoring
categories, teams and their owners, matchup periods and the matchups inside
them, a roster snapshot per team per matchup period, every statistic each team
posted in each matchup, a box score line per player per scoring period, and
where every player sat in every team's lineup on every day.

Safe to re-run: an already-stored season is updated in place, a new season is
inserted alongside it, and earlier seasons are never modified. `--all-seasons`
reads the league's own `previousSeasons`, so the list is not guessed. It makes one
ESPN call per matchup period, so a full season is a couple of dozen requests
plus one per scoring period for the daily lineups and a handful of batched
player-card calls, and takes roughly two and a half minutes.

Two grains of roster data are kept on purpose. `roster_slots` says who a team
held during a matchup period. `daily_lineup_slots` says what the team did with
them each day, including who sat on the bench or injured reserve, and is the
one to use for anything about decisions. Summing the started players there
reproduces a team's stored category totals exactly.

One thing the schema does not have. There is
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

## Run the API

```bash
uvicorn app.main:create_app --factory
```

On the VPS it runs as a service, bound to the Tailscale address only:

```bash
sudo cp deploy/fcp-core-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fcp-core-api.service
```

There is no authentication. That bind address is the only thing keeping it
private, so do not put it behind a public reverse proxy without adding auth
first. Owner responses carry this database's own id rather than ESPN's SWID
GUID, which is half of the cookie pair that authenticates an ESPN account.

Read-only access to whatever has been ingested. Interactive documentation is
at `/docs`; the generated schema is at `/openapi.json`.

Paths are keyed on ESPN's own identifiers, so a URL can be built from a
league id and a year:

```bash
curl localhost:8000/leagues/3853870/seasons/2026/standings
curl "localhost:8000/leagues/3853870/seasons/2026/teams/3/bench"
curl "localhost:8000/leagues/3853870/seasons/2026/teams/3/lineups?scoring_period=91"
```

`/standings` is worth singling out. ESPN reports no matchup record, so that
route derives one by counting winners, excludes byes, and prints it next to
the category tally rather than instead of it.

## Narratives

Seven routes answer questions ESPN does not, with the derivation in
`app/narratives.py` rather than in the routers:

```bash
curl localhost:8000/leagues/3853870/owners
curl "localhost:8000/leagues/3853870/head-to-head?min_meetings=8"
curl localhost:8000/leagues/3853870/seasons/2026/streaks
curl localhost:8000/leagues/3853870/seasons/2026/notable-matchups
curl localhost:8000/leagues/3853870/seasons/2026/worst-bench-calls
curl localhost:8000/leagues/3853870/seasons/2026/category-profiles
curl localhost:8000/leagues/3853870/seasons/2026/bench-leaderboard
```

Owner routes sit under the league rather than a season, because ESPN's owner
GUID is stable across seasons and an all-time record is the point.

Three conventions apply throughout. Byes never count toward a record. A tie
breaks a streak rather than extending it. Head-to-head counts each meeting
once, and a co-owned team gives the meeting to each of its owners.

## Keeping the current season current

This runs on the VPS at `/opt/fcp-core`, not on a laptop, because a laptop
asleep at 09:00 does not refresh anything.

```bash
sudo cp deploy/fcp-core-ingest.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fcp-core-ingest.timer
```

`sudo systemctl start fcp-core-ingest.service` runs one immediately and
`journalctl -u fcp-core-ingest.service` shows what happened.

On a host that already has a Postgres, set `FCP_DB_PORT` and match it in
`DATABASE_URL`, then give Compose its own project name so the two stacks
cannot reach each other:

```bash
FCP_DB_PORT=5433 docker compose -p fcp-core up -d
```

### On a Mac instead

A nightly launchd agent runs the ingest in `--recent` mode, which refreshes
the trailing ten scoring periods in about 15 seconds instead of the two and a
half minutes a full season takes. Nothing outside that window is rewritten,
so the narrow nightly run and an occasional `--all-seasons` pass coexist.

```bash
cp deploy/com.fcp-core.ingest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fcp-core.ingest.plist
```

`launchctl start com.fcp-core.ingest` triggers one immediately;
`launchctl unload ~/Library/LaunchAgents/com.fcp-core.ingest.plist` removes
the schedule. Edit the `Hour` in the plist to move it.

The Compose database has to be running for the job to do anything. The
wrapper waits a minute for it and then exits 69, logging that it skipped
rather than failed, so enable Docker at login if you want the schedule to be
dependable.

Every attempt is recorded whether it succeeds or not:

```bash
curl localhost:8000/ingest-runs
curl localhost:8000/ingest-runs/health
```

Ask `/ingest-runs/health` without a season. It answers for whichever season
is running now, which is the question you actually want answered: a finished
season refreshed nightly looks perfectly healthy while the live one is going
unrecorded.

A run still showing `running` means the process died partway. `stale` goes
true when nothing has succeeded for 36 hours, which tolerates one missed
night. Text output lands in `logs/scheduled-ingest.log`.
