#!/usr/bin/env python3
"""Send the morning digest, or an alert between digests.

Usage:
    python scripts/digest.py              # the morning message
    python scripts/digest.py --alert      # only an urgent roster change, if there is one
    python scripts/digest.py --dry-run    # print it, deliver nothing, mark nothing

Reads the database only: no ESPN request. The season is the newest one the
listener has snapshotted, and the team is FCP_TRACKED_TEAM_ID from the
environment or .env.

Delivery goes to every channel that is configured (app/notify.py): a POST to
FCP_DIGEST_URL (FCP_DIGEST_CHAT_ID switches it to Telegram's shape), and an
email over SMTP when FCP_SMTP_HOST, FCP_EMAIL_FROM and FCP_EMAIL_TO are all
set. One channel failing does not stop the other, and the log line says what
each one did.

With no channel configured the message is printed and nothing is marked as
notified, which is also how this is tested: a message nobody received is a
message still to send. Events are marked once *any* channel has delivered,
because the manager has the news; a run with a failed channel still exits 1
so a broken channel is visible in the unit rather than silently dead.

Exit codes:
    0   delivered on every configured channel, or printed, or nothing to say
    1   the tracked team or the season could not be resolved, or a channel failed
"""

import argparse
import sys
from datetime import UTC, datetime

from app import notify
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.digest import (
    build_alert,
    build_digest,
    latest_listened_season,
    league_season_for,
    mark_notified,
)
from app.espn import get_espn_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alert",
        action="store_true",
        help="Send only an urgent change to the tracked roster, or nothing at all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the message and deliver nothing. Events stay unnotified.",
    )
    args = parser.parse_args()

    espn_settings = get_espn_settings()
    team_id = espn_settings.fcp_tracked_team_id
    if team_id is None:
        print("FCP_TRACKED_TEAM_ID is not set; there is no team to report on.", file=sys.stderr)
        return 1

    settings = get_settings()
    configured = bool(settings.fcp_digest_url) or settings.email_configured
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            season = latest_listened_season(session)
            if season is None:
                print("The listener has not run yet; nothing to report.", file=sys.stderr)
                return 1
            league_season = league_season_for(session, espn_settings.espn_league_id, season)
            if league_season is None:
                print(
                    f"No stored season {season} for league {espn_settings.espn_league_id}.",
                    file=sys.stderr,
                )
                return 1

            now = datetime.now(UTC)
            if args.alert:
                alert = build_alert(session, league_season, team_id)
                if alert is None:
                    print("No urgent change on the tracked roster.")
                    return 0
                text, event_ids = alert
                title = "FCP: a player of yours is out"
            else:
                digest = build_digest(session, league_season, team_id, now=now)
                text, event_ids = digest.render(), digest.event_ids
                title = "FCP morning digest"

            if args.dry_run or not configured:
                print(text)
                if not args.dry_run:
                    print(
                        f"\n[no delivery channel is configured, so nothing was sent"
                        f" and {len(event_ids)} event(s) stay unnotified]"
                    )
                return 0

            results = notify.deliver(settings, text, title=title)
            done = [result for result in results if result.sent]
            marked = 0
            if done:
                # He has the news, so it is not repeated tomorrow, even when
                # the other channel is broken.
                marked = mark_notified(session, event_ids, now)
                session.commit()
            print(
                f"Sent {len(text.splitlines())} lines to "
                f"{len(done)} of {len(results)} channel(s) "
                f"({'; '.join(result.describe() for result in results)}); "
                f"marked {marked} event(s) notified."
            )
            if len(done) != len(results):
                return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
