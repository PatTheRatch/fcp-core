import logging
import re

from fastapi import FastAPI

from app.api import access
from app.api.auth import router as auth_router
from app.api.changes import router as changes_router
from app.api.channels import router as channels_router
from app.api.draft import router as draft_router
from app.api.health import router as health_router
from app.api.ingest_runs import router as ingest_runs_router
from app.api.leagues import router as leagues_router
from app.api.leagues_admin import router as leagues_admin_router
from app.api.listener import router as listener_router
from app.api.narratives import router as narratives_router
from app.api.pages import router as pages_router
from app.api.pickups import router as pickups_router
from app.api.players import router as players_router
from app.api.projections import router as projections_router
from app.api.scorecard import router as scorecard_router
from app.api.site import router as site_router
from app.api.teams import router as teams_router
from app.api.trades import router as trades_router
from app.api.transactions import router as transactions_router

DESCRIPTION = """Read-only access to stored ESPN fantasy basketball seasons.

Writes belong to the ingest, not to this API, with four exceptions: a
manager's own projections are uploaded through `/projections/sets`, because
no ingest can fetch a file that only he has (docs/projection_sources.md);
signing in writes the account tables (`/auth`); connecting a league,
inviting its members and claiming teams write the league's membership
(`/connections`, `/invites`, `/claims`, docs/accounts.md); and a member's
own alert channels (`/me/channels`, docs/jobs.md).

Every route declares who may call it: anyone signed in, a member of the
league in its path, an owner of that league, or the manager of the team in
its path (docs/accounts.md has the table). With `FCP_AUTH_MODE=single`, the
default, every request is the owner and nothing is refused.

Paths are keyed on ESPN's own identifiers, so a URL can be built from a
league id and a year. Two details are worth knowing before reading results:

* A team's `categories_won` counts CATEGORIES, not matchups. ESPN reports no
  matchup record, so `/standings` derives one and shows both side by side.
* Percentages are ratios. FG% is 0.457, not 45.7.
"""


def _log_accounts() -> None:
    """Let the `fcp.*` loggers speak at INFO under uvicorn.

    Uvicorn configures only its own loggers, so without this the dev sign-in
    link (logged when SMTP is not configured) would go nowhere. Idempotent:
    one handler, however many apps a test process builds.
    """
    logger = logging.getLogger("fcp")
    logger.setLevel(logging.INFO)
    if not any(getattr(h, "fcp", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
        handler.fcp = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


_TOKEN_IN_URL = re.compile(r"(token=)[^&\s]+")
#: An invite token is a path segment: `/invites/<token>`, `/join/<token>`,
#: and the same inside a `next=` that sends a signed-out browser to sign in.
_TOKEN_IN_PATH = re.compile(r"((?:/|%2F)(?:invites|join)(?:/|%2F))[^/?&\s%]+", re.IGNORECASE)


def _redact(text: str) -> str:
    return _TOKEN_IN_PATH.sub(r"\1[redacted]", _TOKEN_IN_URL.sub(r"\1[redacted]", text))


class _RedactTokens(logging.Filter):
    """Blank the tokens in a logged request line: `/auth/callback?token=…`,
    `/invites/…` and `/join/…`.

    Uvicorn's access log writes every request's path and query. A sign-in
    token is spent by the time its click is logged, but an invite link works
    until it is revoked, and no secret belongs in a log line either way
    (docs/accounts.md, "Security notes").
    """

    fcp = True

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _redact(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def _redact_access_log() -> None:
    """Attach the filter to uvicorn's access logger, once per process."""
    access_log = logging.getLogger("uvicorn.access")
    if not any(getattr(f, "fcp", False) for f in access_log.filters):
        access_log.addFilter(_RedactTokens())


def create_app() -> FastAPI:
    _log_accounts()
    _redact_access_log()
    app = FastAPI(title="FCP Core", description=DESCRIPTION, version="0.1.0")
    access.install(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(leagues_router)
    app.include_router(leagues_admin_router)
    app.include_router(draft_router)
    app.include_router(teams_router)
    app.include_router(narratives_router)
    app.include_router(transactions_router)
    app.include_router(players_router)
    app.include_router(ingest_runs_router)
    app.include_router(scorecard_router)
    app.include_router(listener_router)
    app.include_router(pickups_router)
    app.include_router(trades_router)
    app.include_router(projections_router)
    app.include_router(pages_router)
    app.include_router(site_router)
    app.include_router(channels_router)
    app.include_router(changes_router)
    return app
