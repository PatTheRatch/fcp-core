#!/usr/bin/env python3
"""Say when a scheduled job has gone quiet. Intended for a scheduler.

Usage:
    python scripts/watchdog.py              # check, deliver if anything is quiet
    python scripts/watchdog.py --dry-run    # print the message, deliver nothing

Reads the rows the jobs write (`app/watchdog.py`) and delivers one message to
FCP_DIGEST_URL, the same place the digest goes. Exit 0 when everything is
running, 2 when something is quiet, so `systemctl --failed` is not the only
place it shows. A delivery that fails exits 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.notify import send
from app.watchdog import checks, message

#: Where scripts/backup_db.sh keeps its dumps, relative to the checkout.
BACKUPS = Path(__file__).resolve().parent.parent / "backups"

QUIET_EXIT = 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, deliver nothing")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            results = checks(session, backups=BACKUPS if BACKUPS.exists() else None)
    finally:
        engine.dispose()

    for check in results:
        print(("QUIET  " if check.quiet else "ok     ") + check.detail)
    text = message(results)
    if text is None:
        return 0
    if args.dry_run or not settings.fcp_digest_url:
        print("\n(not delivered)" if args.dry_run else "\n(FCP_DIGEST_URL is not set)")
        return QUIET_EXIT
    send(
        settings.fcp_digest_url,
        text,
        title="fcp-core",
        chat_id=settings.fcp_digest_chat_id,
    )
    return QUIET_EXIT


if __name__ == "__main__":
    sys.exit(main())
