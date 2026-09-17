#!/bin/sh
# One status pass of the in-season listener. Intended for a scheduler.
#
# Three firings a day in season (deploy/fcp-core-status.timer), each a few
# requests and a few seconds: the whole player pool is snapshotted, diffed
# against the last pass, and news fetched for the players that changed. The
# pass labels itself from the clock (morning, report, late) and decides on
# its own to snapshot only once a day out of season.
#
# Exit codes, the same contract as scheduled_ingest.sh:
#   0   the pass succeeded
#   69  the database was unreachable, so nothing was attempted
#   1   the pass failed, or the database schema is behind the code
#
# The morning pass is followed by the digest (scripts/digest.py); the later
# passes by an alert, which sends nothing unless a player on the tracked
# roster has just been ruled out. Both need FCP_TRACKED_TEAM_ID and
# FCP_DIGEST_URL to deliver anything; without them they print and exit 0.
#
# Every attempt is appended to logs/scheduled-status.log, and the pass itself
# to the ingest_runs table with mode "status".

set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_DIR"

LOG_DIR="${FCP_LOG_DIR:-$REPO_DIR/logs}"
LOG_FILE="$LOG_DIR/scheduled-status.log"
mkdir -p "$LOG_DIR"

. "$REPO_DIR/scripts/scheduled_common.sh"

trim_log
require_python
wait_for_database
require_schema_at_head

# The timer passes no arguments. Name the pass from the clock here, once, so
# the pass and the digest below agree on which one this is; left to the pass,
# the digest would never see "morning" and the morning digest would never go.
if [ "$#" -eq 0 ]; then
    LABEL="$("$PYTHON" -c 'from datetime import UTC, datetime
from app.listener.status import label_for
print(label_for(datetime.now(UTC)))')"
    set -- --label "$LABEL"
fi

log "starting: status pass $*"
if ! "$PYTHON" scripts/status_pass.py "$@" >> "$LOG_FILE" 2>&1; then
    log "FAILED: see the lines above and the ingest_runs table"
    exit 1
fi
log "succeeded"

# Then tell the manager. The morning pass earns the whole digest; the later
# ones only interrupt for a player of his being ruled out. A delivery that
# fails leaves the events unnotified, so the next run repeats them, and the
# failure is the unit's rather than being swallowed here.
case "${1:-}${2:-}" in
    *morning*) DIGEST_ARGS="" ;;
    *) DIGEST_ARGS="--alert" ;;
esac
log "starting: digest ${DIGEST_ARGS:-morning}"
if ! "$PYTHON" scripts/digest.py $DIGEST_ARGS >> "$LOG_FILE" 2>&1; then
    log "FAILED: the digest; the status pass itself succeeded"
    exit 1
fi
log "succeeded: digest"
exit 0
