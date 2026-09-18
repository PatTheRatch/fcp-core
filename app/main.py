from fastapi import FastAPI

from app.api.draft import router as draft_router
from app.api.health import router as health_router
from app.api.ingest_runs import router as ingest_runs_router
from app.api.leagues import router as leagues_router
from app.api.listener import router as listener_router
from app.api.narratives import router as narratives_router
from app.api.pages import router as pages_router
from app.api.pickups import router as pickups_router
from app.api.players import router as players_router
from app.api.projections import router as projections_router
from app.api.scorecard import router as scorecard_router
from app.api.teams import router as teams_router
from app.api.transactions import router as transactions_router

DESCRIPTION = """Read-only access to stored ESPN fantasy basketball seasons.

Writes belong to the ingest, not to this API, with one exception: a manager's
own projections are uploaded through `/projections/sets`, because no ingest
can fetch a file that only he has (docs/projection_sources.md).

Paths are keyed on ESPN's own identifiers, so a URL can be built from a
league id and a year. Two details are worth knowing before reading results:

* A team's `categories_won` counts CATEGORIES, not matchups. ESPN reports no
  matchup record, so `/standings` derives one and shows both side by side.
* Percentages are ratios. FG% is 0.457, not 45.7.
"""


def create_app() -> FastAPI:
    app = FastAPI(title="FCP Core", description=DESCRIPTION, version="0.1.0")
    app.include_router(health_router)
    app.include_router(leagues_router)
    app.include_router(draft_router)
    app.include_router(teams_router)
    app.include_router(narratives_router)
    app.include_router(transactions_router)
    app.include_router(players_router)
    app.include_router(ingest_runs_router)
    app.include_router(scorecard_router)
    app.include_router(listener_router)
    app.include_router(pickups_router)
    app.include_router(projections_router)
    app.include_router(pages_router)
    return app
