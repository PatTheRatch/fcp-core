#!/bin/sh
# Refresh the current season. Intended for a scheduler, not for a person.
#
# Deliberately narrow: it runs the ingest in --recent mode, which rewrites
# only the trailing days and leaves the rest of the season untouched. A full
# pass stays a manual decision.
#
# --upcoming also refreshes next season's settings and teams (not its game
# logs) while ESPN has that season and its draft is still ahead. Until
# October the current season is the old one, and the draft being prepared
# reads the new one's budget, team count and position limits.
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
#
# After the ingest, the listener's nightly status pass runs (status_pass.py
# --label nightly), so a player's status is observed four times a day in
# season. The database wait and the schema guard are shared with
# scheduled_status.sh through scheduled_common.sh.

set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_DIR"

LOG_DIR="${FCP_LOG_DIR:-$REPO_DIR/logs}"
LOG_FILE="$LOG_DIR/scheduled-ingest.log"
RECENT_DAYS="${FCP_RECENT_DAYS:-10}"
mkdir -p "$LOG_DIR"

. "$REPO_DIR/scripts/scheduled_common.sh"

trim_log
require_python
wait_for_database
require_schema_at_head

log "starting: --recent $RECENT_DAYS --upcoming"
if ! "$PYTHON" scripts/ingest_league.py --recent "$RECENT_DAYS" --upcoming >> "$LOG_FILE" 2>&1; then
    log "FAILED: see the lines above and the ingest_runs table"
    exit 1
fi
log "succeeded"

# The nightly snapshot of the listener, so status is observed four times a
# day in season. Its own run row records it; a failure here is the
# listener's, not the ingest's, and is reported as such.
log "starting: status pass nightly"
if ! "$PYTHON" scripts/status_pass.py --label nightly >> "$LOG_FILE" 2>&1; then
    log "FAILED: the nightly status pass; the ingest itself succeeded"
    exit 1
fi
log "succeeded: status pass nightly"
exit 0
