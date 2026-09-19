#!/usr/bin/env python3
"""Say when a scheduled job has gone quiet. Intended for a scheduler.

Usage:
    python scripts/watchdog.py              # check, deliver if anything is quiet
    python scripts/watchdog.py --dry-run    # print the message, deliver nothing

Reads the rows the jobs write (`app/watchdog.py`) and delivers one message to
FCP_DIGEST_URL, the same place the digest goes. Exit 0 when everything is
running, 2 when something is quiet, so `systemctl --failed` is not the only
place it shows. A delivery that fails exits 1.

Since step 4 (docs/jobs.md), two more things, both silent until they apply:

* A league someone connected whose ingest has not succeeded inside its
  window is named in the message, and its connection's owner is emailed
  (through his verified email channel) a plain line saying to reconnect, as
  is the owner of this server (FCP_EMAIL_TO). Never the error's text: ESPN's
  words can quote a login. With no connected league, nothing changes.
* Once the job queue is in use, whether a worker is taking jobs. Before any
  job exists, the check is left out and the message is as it always was.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app import channels, notify
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.notify import send
from app.watchdog import (
    StaleLeague,
    checks,
    message,
    reconnect_message,
    stale_connections,
    stale_lines,
    worker_check,
)

#: Where scripts/backup_db.sh keeps its dumps, relative to the checkout.
BACKUPS = Path(__file__).resolve().parent.parent / "backups"
#: Written by scripts/offsite_backup.py after each good copy to S3.
OFFSITE_MARKER = BACKUPS.parent / "logs" / "offsite-last-ok"

QUIET_EXIT = 2


def _stale(session: Session) -> list[StaleLeague]:
    """The stale connected leagues; none on a database before migration 0019."""
    try:
        return stale_connections(session)
    except ProgrammingError:
        session.rollback()
        return []


def tell_owners(
    factory: sessionmaker[Session], settings: Settings, stale: list[StaleLeague], now: datetime
) -> None:
    """Email each stale league's connector through his verified email
    channels, and this server's owner (FCP_EMAIL_TO). Best effort: a failure
    is printed by its class, and never stops the rest."""
    for league in stale:
        title = f"FCP: reconnect {league.name}"
        text = reconnect_message(league, settings.fcp_public_url)
        try:
            with factory() as session:
                mine = [
                    c
                    for c in channels.verified(session, league.owner_user_id)
                    if c.kind == channels.EMAIL
                ]
                for result in channels.deliver(mine, text, title=title, settings=settings):
                    print(f"reconnect note for league {league.espn_league_id}: {result.describe()}")
                if not mine:
                    print(
                        f"league {league.espn_league_id}: its owner has no verified email channel"
                    )
        except Exception as error:  # the owner's note is best effort
            print(f"league {league.espn_league_id}: owner not told ({type(error).__name__})")
    if settings.email_configured:
        body = (
            "These connected leagues have not refreshed inside their window; each "
            "connector has been asked to reconnect:\n\n" + "\n".join(stale_lines(stale, now)) + "\n"
        )
        try:
            notify.send_email(
                body,
                host=str(settings.fcp_smtp_host),
                port=settings.fcp_smtp_port,
                sender=str(settings.fcp_email_from),
                recipients=settings.email_recipients,
                subject="fcp-core: leagues to reconnect",
                user=settings.fcp_smtp_user,
                password=settings.fcp_smtp_password,
            )
        except Exception as error:  # best effort, like the owners' notes
            print(f"the server owner's email not sent ({type(error).__name__})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, deliver nothing")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            results = checks(
                session,
                backups=BACKUPS if BACKUPS.exists() else None,
                offsite_marker=OFFSITE_MARKER if settings.fcp_s3_bucket else None,
            )
            worker = worker_check(session)
            if worker is not None:
                results.append(worker)
            stale = _stale(session)

        now = datetime.now(UTC)
        for check in results:
            print(("QUIET  " if check.quiet else "ok     ") + check.detail)
        for line in stale_lines(stale, now):
            print("STALE  " + line.strip())
        text = message(results)
        if stale:
            head = text or f"fcp-core: {len(stale)} league(s) not refreshing, {now:%a %d %b}"
            text = "\n".join([head, "", "leagues to reconnect:", *stale_lines(stale, now)])
        if text is None:
            return 0
        if stale and not args.dry_run:
            tell_owners(factory, settings, stale, now)
    finally:
        engine.dispose()

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
