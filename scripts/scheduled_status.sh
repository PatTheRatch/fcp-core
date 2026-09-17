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

log "starting: status pass $*"
if "$PYTHON" scripts/status_pass.py "$@" >> "$LOG_FILE" 2>&1; then
    log "succeeded"
    exit 0
fi

log "FAILED: see the lines above and the ingest_runs table"
exit 1
