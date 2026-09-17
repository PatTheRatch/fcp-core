#!/bin/sh
# Shared by the scheduled wrappers (scheduled_ingest.sh, scheduled_status.sh).
# Source it after setting REPO_DIR and LOG_FILE:
#
#     . "$REPO_DIR/scripts/scheduled_common.sh"
#
# It provides:
#   log MESSAGE          append a UTC-stamped line to $LOG_FILE
#   trim_log             keep $LOG_FILE readable rather than unbounded
#   require_python       exit 1 if $PYTHON is not an interpreter
#   wait_for_database    exit 69 if the database is unreachable after $DB_WAIT_SECONDS
#   require_schema_at_head   exit 1 if the database is behind the migrations
#
# The exit codes are the contract with the scheduler, and the same for every
# wrapper: 0 succeeded, 69 nothing attempted (database unreachable), 1 failed
# or refused. See scheduled_ingest.sh for why 69 is left as a failure.
#
# Overridable for tests: FCP_PYTHON names the interpreter, FCP_LOG_DIR the log
# directory.

PYTHON="${FCP_PYTHON:-$REPO_DIR/.venv/bin/python}"
DB_WAIT_SECONDS="${FCP_DB_WAIT_SECONDS:-60}"

log() {
    printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG_FILE"
}

trim_log() {
    if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 2000000 ]; then
        tail -n 2000 "$LOG_FILE" > "$LOG_FILE.trimmed" && mv "$LOG_FILE.trimmed" "$LOG_FILE"
        log "log trimmed to the last 2000 lines"
    fi
}

require_python() {
    if [ ! -x "$PYTHON" ]; then
        log "FAILED: no interpreter at $PYTHON"
        exit 1
    fi
}

# Wait for the database rather than failing the moment Docker is still waking.
wait_for_database() {
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
}

# Refuse to run code against a schema it was not written for. The checkout
# is deployed by hand and the migration is a separate step, so the two can
# drift: on 2026-09-13 the ORM gained a column before the database did, and
# only luck in the timing kept the nightly run from failing on it. Alembic
# prints "(head)" beside the revision when the database is current.
require_schema_at_head() {
    if ! "$PYTHON" -m alembic current 2>/dev/null | grep -q '(head)'; then
        log "REFUSED: database schema is not at the latest migration; run '.venv/bin/alembic upgrade head' in $REPO_DIR"
        exit 1
    fi
}
