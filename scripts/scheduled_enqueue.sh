#!/bin/sh
# Put the schedule's jobs on the queue. Intended for a scheduler
# (deploy/fcp-core-enqueue.timer), and not installed until the VPS is moved
# onto the job queue (docs/jobs.md, "Switching over"). Until then the timers
# that run scheduled_ingest.sh and scheduled_status.sh do this work.
#
# The timer passes no arguments: the label (nightly, morning, report, late)
# is named from the clock, the same way scheduled_status.sh names its pass.
# The jobs themselves are run by the worker (deploy/fcp-core-worker.service).
#
# Exit codes, the same contract as the other wrappers:
#   0   enqueued (or no slot is near, and nothing was)
#   69  the database was unreachable, so nothing was attempted
#   1   refused: the database schema is behind the code
#
# Every attempt is appended to logs/scheduled-enqueue.log.

set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_DIR"

LOG_DIR="${FCP_LOG_DIR:-$REPO_DIR/logs}"
LOG_FILE="$LOG_DIR/scheduled-enqueue.log"
mkdir -p "$LOG_DIR"

. "$REPO_DIR/scripts/scheduled_common.sh"

trim_log
require_python
wait_for_database
require_schema_at_head

if [ "$#" -eq 0 ]; then
    set -- --schedule auto
fi

log "starting: enqueue $*"
if ! "$PYTHON" scripts/enqueue.py "$@" >> "$LOG_FILE" 2>&1; then
    log "FAILED: see the lines above"
    exit 1
fi
log "succeeded"
exit 0
