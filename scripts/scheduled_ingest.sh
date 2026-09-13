#!/bin/sh
# Refresh the current season. Intended for a scheduler, not for a person.
#
# Deliberately narrow: it runs the ingest in --recent mode, which rewrites
# only the trailing days and leaves the rest of the season untouched. A full
# pass stays a manual decision.
#
# Exit codes matter here, because a scheduler is the only thing reading them:
#   0   the ingest succeeded
#   69  the database was unreachable, so nothing was attempted
#   1   the ingest itself failed, or the database schema is behind the code
#       (the reason is in the log; the latter says so in capitals)
#
# Every attempt is appended to logs/scheduled-ingest.log. The run is also
# recorded in the ingest_runs table, but only once the database is up, which
# is why the file log exists at all.

set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_DIR"

PYTHON="$REPO_DIR/.venv/bin/python"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/scheduled-ingest.log"
RECENT_DAYS="${FCP_RECENT_DAYS:-10}"
DB_WAIT_SECONDS="${FCP_DB_WAIT_SECONDS:-60}"

mkdir -p "$LOG_DIR"

log() {
    printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG_FILE"
}

# Keep the log readable rather than unbounded; a scheduler appends forever.
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 2000000 ]; then
    tail -n 2000 "$LOG_FILE" > "$LOG_FILE.trimmed" && mv "$LOG_FILE.trimmed" "$LOG_FILE"
    log "log trimmed to the last 2000 lines"
fi

if [ ! -x "$PYTHON" ]; then
    log "FAILED: no interpreter at $PYTHON"
    exit 1
fi

# Wait for the database rather than failing the moment Docker is still waking.
waited=0
until "$PYTHON" -c "
import sys
from sqlalchemy import text
from app.config import get_settings
from app.db.session import make_engine
engine = make_engine(get_settings().database_url)
try:
    with engine.connect() as connection:
        connection.execute(text('SELECT 1'))
except Exception:
    sys.exit(1)
finally:
    engine.dispose()
" >/dev/null 2>&1; do
    if [ "$waited" -ge "$DB_WAIT_SECONDS" ]; then
        log "SKIPPED: database unreachable after ${DB_WAIT_SECONDS}s (is Docker running?)"
        exit 69
    fi
    sleep 5
    waited=$((waited + 5))
done

# Refuse to run code against a schema it was not written for. The checkout
# is deployed by hand and the migration is a separate step, so the two can
# drift: on 2026-09-13 the ORM gained a column before the database did, and
# only luck in the timing kept the nightly run from failing on it. Alembic
# prints "(head)" beside the revision when the database is current.
if ! "$PYTHON" -m alembic current 2>/dev/null | grep -q '(head)'; then
    log "REFUSED: database schema is not at the latest migration; run '.venv/bin/alembic upgrade head' in $REPO_DIR"
    exit 1
fi

log "starting: --recent $RECENT_DAYS"
if "$PYTHON" scripts/ingest_league.py --recent "$RECENT_DAYS" >> "$LOG_FILE" 2>&1; then
    log "succeeded"
    exit 0
fi

log "FAILED: see the lines above and the ingest_runs table"
exit 1
