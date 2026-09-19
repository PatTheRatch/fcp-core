#!/usr/bin/env python3
"""Ask the API for today's reports once, so the pages open at once.

Usage:
    python scripts/warm_pages.py            # our team, today, the season the listener follows
    python scripts/warm_pages.py --season 2026 --team 3 --today 100

A real-season streaming or rest-of-season report takes twenty to forty
seconds the first time a day is asked for, and is cached in the API process
after that (app/pickups/projection.py keys its cache on the day). The
morning pass runs this after the digest so that by the time the manager
opens the week page the answer is already there. It reaches the API over
HTTP because the cache lives in the API's own process, not in this one, and
sends `FCP_SERVICE_TOKEN` as a bearer when it is set, so it keeps working
when the API enforces accounts (docs/accounts.md).

Never fails the pass: a missing setting, an unreachable API or a slow report
is printed and the exit code is 0. The unit that matters is the digest.

Unnecessary once the job queue runs (docs/jobs.md): the morning
`precompute` job stores each claimed team's reports in `team_reports`, and
the routes answer from those rows, in any process. Kept for today's timers,
which still run it after the morning digest; the switched-over units do not.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.digest import latest_listened_season
from app.espn import get_espn_settings

#: Long enough for a cold real-season report, short enough that a stuck API
#: cannot hold the scheduled pass for long.
TIMEOUT_SECONDS = 180


def urls_for(base: str, league_id: int, season: int, team_id: int, today: int | None) -> list[str]:
    """The two JSON routes the pages read, for one team on one day."""
    root = f"{base.rstrip('/')}/leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups"
    suffix = f"?today={today}" if today is not None else ""
    return [f"{root}/stream{suffix}", f"{root}/season{suffix}"]


def warm(urls: list[str], token: str | None = None) -> list[tuple[str, int | None, float]]:
    """GET each url once; (url, status or None on a connection failure, seconds).

    `token` is `FCP_SERVICE_TOKEN`, sent as a bearer so the warm-up still
    works once the API enforces accounts (docs/accounts.md); in single mode
    the API ignores it. It goes in a header, never in the URL, and is never
    printed.
    """
    extra: dict[str, Any] = {}
    if token:
        extra["headers"] = {"Authorization": f"Bearer {token}"}
    out: list[tuple[str, int | None, float]] = []
    for url in urls:
        started = time.monotonic()
        try:
            status: int | None = requests.get(url, timeout=TIMEOUT_SECONDS, **extra).status_code
        except requests.RequestException:
            status = None
        out.append((url, status, time.monotonic() - started))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, help="default: the season the listener follows")
    ap.add_argument("--team", type=int, help="ESPN team id; default: FCP_TRACKED_TEAM_ID")
    ap.add_argument("--today", type=int, help="a scoring period; default: today")
    args = ap.parse_args()

    settings = get_settings()
    if not settings.fcp_api_url:
        print("warm: FCP_API_URL is not set; nothing to warm")
        return 0
    espn = get_espn_settings()
    team_id = args.team if args.team is not None else espn.fcp_tracked_team_id
    if team_id is None:
        print("warm: no team (set FCP_TRACKED_TEAM_ID or pass --team); nothing to warm")
        return 0
    season = args.season
    if season is None:
        with make_session_factory(make_engine(settings.database_url))() as session:
            season = latest_listened_season(session)
    if season is None:
        print("warm: the listener has followed no season yet; nothing to warm")
        return 0

    for url, status, seconds in warm(
        urls_for(settings.fcp_api_url, espn.espn_league_id, season, int(team_id), args.today),
        settings.fcp_service_token,
    ):
        verdict = "unreachable" if status is None else f"HTTP {status}"
        print(f"warm: {verdict} in {seconds:.1f}s  {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
