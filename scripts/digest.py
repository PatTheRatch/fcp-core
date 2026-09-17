#!/usr/bin/env python3
"""Send the morning digest, or an alert between digests.

Usage:
    python scripts/digest.py              # the morning message
    python scripts/digest.py --alert      # only an urgent roster change, if there is one
    python scripts/digest.py --dry-run    # print it, deliver nothing, mark nothing

Reads the database only: no ESPN request. The season is the newest one the
listener has snapshotted, and the team is FCP_TRACKED_TEAM_ID from the
environment or .env.

Delivery is one POST to FCP_DIGEST_URL (see app/notify.py for the two
shapes; FCP_DIGEST_CHAT_ID switches to the Telegram one). With no URL set
the message is printed and nothing is marked as notified, which is also how
this is tested: a message nobody received is a message still to send.

Exit codes:
    0   sent, or printed, or there was nothing to say
    1   the tracked team or the season could not be resolved, or delivery failed
"""

import argparse
import os
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

DIGEST_URL = "FCP_DIGEST_URL"
DIGEST_CHAT_ID = "FCP_DIGEST_CHAT_ID"


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

    url = os.environ.get(DIGEST_URL)
    chat_id = os.environ.get(DIGEST_CHAT_ID)
    engine = make_engine(get_settings().database_url)
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

            if args.dry_run or not url:
                print(text)
                if not args.dry_run:
                    print(
                        f"\n[{DIGEST_URL} is not set, so nothing was delivered"
                        f" and {len(event_ids)} event(s) stay unnotified]"
                    )
                return 0

            notify.send(url, text, title=title, chat_id=chat_id)
            marked = mark_notified(session, event_ids, now)
            session.commit()
            print(f"Sent {len(text.splitlines())} lines; marked {marked} event(s) notified.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
