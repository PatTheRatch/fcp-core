#!/bin/sh
# Dump the database to a compressed file, then prove the file is readable.
#
# An untested backup is not a backup, so every dump is verified with
# `pg_restore --list` before it is kept and the old ones are pruned. A dump
# that cannot be listed is moved aside rather than counted as success.
#
# Connects over TCP rather than through Docker, so it needs neither the
# docker group nor sudo.
#
# Exit codes:
#   0   a verified dump was written
#   69  the database was unreachable, so nothing was attempted
#   1   the dump failed, or was written and could not be verified

set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_DIR"

BACKUP_DIR="${FCP_BACKUP_DIR:-$REPO_DIR/backups}"
KEEP_DAYS="${FCP_BACKUP_KEEP_DAYS:-14}"
LOG_FILE="$REPO_DIR/logs/backup.log"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
TARGET="$BACKUP_DIR/fcp-$STAMP.dump"

mkdir -p "$BACKUP_DIR" "$REPO_DIR/logs"

log() {
    printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG_FILE"
}

# DATABASE_URL carries SQLAlchemy's driver suffix, which libpq does not accept.
DB_URL="$(sed -n 's/^DATABASE_URL=//p' .env | head -n 1 | sed 's|postgresql+psycopg://|postgresql://|')"
if [ -z "$DB_URL" ]; then
    log "FAILED: no DATABASE_URL in .env"
    exit 1
fi

if ! pg_isready -d "$DB_URL" >/dev/null 2>&1; then
    log "SKIPPED: database unreachable"
    exit 69
fi

# -Fc keeps it compressed and lets pg_restore inspect it without unpacking.
if ! pg_dump -Fc -d "$DB_URL" -f "$TARGET" 2>>"$LOG_FILE"; then
    log "FAILED: pg_dump did not complete"
    rm -f "$TARGET"
    exit 1
fi

# Verify before trusting it. A dump that lists no tables is not a backup.
TABLES="$(pg_restore --list "$TARGET" 2>/dev/null | grep -c 'TABLE DATA' || true)"
if [ "${TABLES:-0}" -lt 1 ]; then
    mv "$TARGET" "$TARGET.unverified"
    log "FAILED: dump written but unreadable, kept as $(basename "$TARGET").unverified"
    exit 1
fi

SIZE="$(du -h "$TARGET" | cut -f1)"
log "ok: $(basename "$TARGET") $SIZE, $TABLES tables"

# Prune only verified dumps, and only after a good one exists.
find "$BACKUP_DIR" -name 'fcp-*.dump' -type f -mtime "+$KEEP_DAYS" -print -delete \
    2>/dev/null | while read -r old; do log "pruned $(basename "$old")"; done

exit 0
