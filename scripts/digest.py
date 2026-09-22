#!/usr/bin/env python3
"""Send the morning digest, or an alert between digests.

Usage:
    python scripts/digest.py                  # the morning message
    python scripts/digest.py --alert          # only an urgent roster change, if there is one
    python scripts/digest.py --dry-run        # print it, deliver nothing, mark nothing
    python scripts/digest.py --html OUT.html  # write the HTML part to a file and send nothing
    python scripts/digest.py --on 2026-01-14  # build it for a day of a stored season

Reads the database only: no ESPN request. The season is the newest one the
listener has snapshotted, and the team is FCP_TRACKED_TEAM_ID from the
environment or .env.

Delivery is email and nothing else (app/notify.py): SMTP when FCP_SMTP_HOST,
FCP_EMAIL_FROM and FCP_EMAIL_TO are all set.

**--html is how the design is looked at.** It writes the HTML part of exactly
the message that would go out to a file and opens no connection, so it can be
opened in a browser at 600px and at a phone's width, and in a viewer that
ignores CSS to see the fallback order. It implies --dry-run: nothing is sent
and nothing is marked.

With no mail configured the message is printed and nothing is marked as
notified, which is also how this is tested: a message nobody received is a
message still to send. A run with a failed send exits 1 so a broken channel
is visible in the unit rather than silently dead.

Exit codes:
    0   delivered, or printed, or written, or nothing to say
    1   the tracked team or the season could not be resolved, or the send failed
"""

import argparse
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import notify
from app.config import get_settings
from app.db.models import LeagueSeason, Team
from app.db.session import make_engine, make_session_factory
from app.digest import (
    build_alert,
    build_digest,
    latest_listened_season,
    league_season_for,
    league_section,
    mark_notified,
)
from app.espn import get_espn_settings
from app.mail import Mail, alert_mail, digest_mail
from app.subscriptions import everything


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
    parser.add_argument(
        "--html",
        metavar="OUT.html",
        help="Write the HTML part to this file and send nothing. Implies --dry-run.",
    )
    parser.add_argument(
        "--on",
        metavar="YYYY-MM-DD",
        help="Build the message for this day rather than now, for a stored season.",
    )
    parser.add_argument(
        "--season",
        type=int,
        help="The stored season to read, rather than the newest one the listener has.",
    )
    args = parser.parse_args()

    espn_settings = get_espn_settings()
    team_id = espn_settings.fcp_tracked_team_id
    if team_id is None:
        print("FCP_TRACKED_TEAM_ID is not set; there is no team to report on.", file=sys.stderr)
        return 1

    settings = get_settings()
    configured = settings.email_configured
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            season = args.season or latest_listened_season(session)
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

            now = (
                datetime.combine(date.fromisoformat(args.on), time(9, 0), tzinfo=UTC)
                if args.on
                else datetime.now(UTC)
            )
            mail: Mail
            if args.alert:
                alert = build_alert(session, league_season, team_id)
                if alert is None:
                    print("No urgent change on the tracked roster.")
                    return 0
                text, event_ids = alert
                mail = alert_mail(
                    _team_name(session, league_season, team_id),
                    text,
                    when=f"{now:%a %d %b, %H:%M} UTC",
                    public_url=settings.fcp_public_url,
                )
            else:
                digest = build_digest(session, league_season, team_id, now=now)
                event_ids = digest.event_ids
                mail = digest_mail(
                    digest,
                    wanted=everything(),
                    public_url=settings.fcp_public_url,
                    league_tail="\n".join(league_section(session, league_season, now=now)),
                )

            if args.html:
                out = Path(args.html)
                out.write_text(mail.html, encoding="utf-8")
                print(f"Subject: {mail.subject}")
                print(f"Wrote {len(mail.html)} characters of HTML to {out}. Nothing was sent.")
                return 0

            if args.dry_run or not configured:
                print(mail.text)
                if not args.dry_run:
                    print(
                        f"\n[no mail is configured, so nothing was sent"
                        f" and {len(event_ids)} event(s) stay unnotified]"
                    )
                return 0

            results = notify.deliver(
                settings, mail.text, title=mail.subject, html=mail.html, headers=mail.headers
            )
            done = [result for result in results if result.sent]
            marked = 0
            if done:
                # He has the news, so it is not repeated tomorrow.
                marked = mark_notified(session, event_ids, now)
                session.commit()
            print(
                f"Sent {len(mail.text.splitlines())} lines "
                f"({'; '.join(result.describe() for result in results)}); "
                f"marked {marked} event(s) notified."
            )
            if len(done) != len(results):
                return 1
    finally:
        engine.dispose()
    return 0


def _team_name(session: Session, league_season: LeagueSeason, espn_team_id: int) -> str:
    """The tracked team's name, for the alert's masthead."""
    team = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id,
            Team.espn_team_id == espn_team_id,
        )
    )
    return str(team.name) if team is not None else f"team {espn_team_id}"


if __name__ == "__main__":
    raise SystemExit(main())
