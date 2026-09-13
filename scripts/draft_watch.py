#!/usr/bin/env python3
"""Watch a live ESPN draft and record whether picks appear as they happen.

Usage:
    python scripts/draft_watch.py --league-id 1234567 --season 2027
    python scripts/draft_watch.py --url "https://fantasy.espn.com/basketball/draft?leagueId=1234567&..."

WHAT THIS IS FOR

We do not yet know that ESPN's read API carries a draft live. The evidence
says it should: `mDraftDetail` is served with `cache-control: must-revalidate`,
`expires: -1` and `pragma: no-cache`, it answers in about a third of a second,
it carries an `inProgress` flag beside `drafted`, and an undrafted season
already holds every pick as an empty shell (`playerId: -1`, `teamId: -1`,
`bidAmount: 0`) waiting to be filled. None of that is proof. A mock draft is,
and this script is how we find out: point it at a mock league and watch.

It answers three questions and writes the evidence to a JSONL file:

  1. Do completed picks appear in the read API at all?
  2. How long after a pick lands does it show up?
  3. Is the in-flight bid on the player currently up ever visible, or do we
     only ever see picks after they complete?

Question 3 is the one that shapes the draft room. ESPN's own draft client runs
on a websocket, so the live "$47 with eight seconds left" state may never
appear here. If it does not, the room tracks completed picks and the bidding
stays on the manager's screen -- which is workable, because a bid ceiling is
something you want computed before you bid, not during.

Read-only. It sends no picks and makes no changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from app.espn import ESPN_TIMEOUT_SECONDS, get_espn_settings

READ_HOST = "https://lm-api-reads.fantasy.espn.com"

#: An unfilled pick slot. ESPN pre-allocates every pick before a draft starts,
#: so the array length never changes -- only these sentinels do.
EMPTY_PLAYER = -1


def league_id_from(text: str) -> int:
    """Pull a league id out of a draft URL, or accept a bare number.

    The draft room URL carries it as a query parameter, but people paste all
    sorts of things, so a bare id and a URL both work.
    """
    stripped = text.strip()
    if stripped.isdigit():
        return int(stripped)
    query = parse_qs(urlparse(stripped).query)
    for key in ("leagueId", "leagueid", "league_id"):
        if key in query and query[key] and query[key][0].isdigit():
            return int(query[key][0])
    match = re.search(r"league(?:Id|_id)?[=/](\d+)", stripped, re.IGNORECASE)
    if match:
        return int(match.group(1))
    raise SystemExit(f"could not find a league id in {text!r}")


def fetch(session: requests.Session, league_id: int, season: int, view: str) -> dict[str, Any]:
    url = f"{READ_HOST}/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{league_id}"
    response = session.get(url, params={"view": view}, timeout=ESPN_TIMEOUT_SECONDS)
    if response.status_code == 401:
        raise SystemExit(
            f"ESPN refused league {league_id} (401). A mock league you joined with a "
            "different ESPN account will not open with these cookies."
        )
    if response.status_code == 404:
        raise SystemExit(
            f"ESPN has no league {league_id} in season {season}. Check the id and the "
            "season on the draft URL -- a mock for next season is not this season."
        )
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


def player_names(session: requests.Session, league_id: int, season: int) -> dict[int, str]:
    """Best-effort id to name. Names are a convenience; the test works without them."""
    try:
        headers = {
            "x-fantasy-filter": json.dumps(
                {"players": {"limit": 2000, "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
            )
        }
        url = (
            f"{READ_HOST}/apis/v3/games/fba/seasons/{season}/segments/0/"
            f"leagues/{league_id}?view=kona_player_info"
        )
        response = session.get(url, headers=headers, timeout=ESPN_TIMEOUT_SECONDS)
        response.raise_for_status()
        found = (response.json() or {}).get("players") or []
        return {
            int(entry["id"]): str((entry.get("player") or {}).get("fullName") or entry["id"])
            for entry in found
            if isinstance(entry, dict) and entry.get("id") is not None
        }
    except Exception:
        return {}


def watch(
    league_id: int,
    season: int,
    *,
    interval: float,
    minutes: float,
    out: Path,
) -> int:
    espn = get_espn_settings()
    session = requests.Session()
    session.cookies.update({"SWID": espn.espn_swid, "espn_s2": espn.espn_s2})

    first = fetch(session, league_id, season, "mDraftDetail")
    detail = first.get("draftDetail") or {}
    picks = detail.get("picks") or []
    filled = sum(1 for p in picks if int(p.get("playerId", EMPTY_PLAYER)) != EMPTY_PLAYER)
    print(
        f"league {league_id}, season {season}: {len(picks)} pick slots, "
        f"{filled} already filled, drafted={detail.get('drafted')}, "
        f"inProgress={detail.get('inProgress')}"
    )
    if not picks:
        print("no pick slots at all -- this league has no draft set up yet")

    names = player_names(session, league_id, season)
    print(f"{len(names)} player names loaded" if names else "no player names; ids only")
    print(f"polling every {interval:g}s for {minutes:g} minutes, writing {out}\n")

    seen: dict[int, dict[str, Any]] = {}
    for pick in picks:
        if int(pick.get("playerId", EMPTY_PLAYER)) != EMPTY_PLAYER:
            seen[int(pick["overallPickNumber"])] = pick

    deadline = time.monotonic() + minutes * 60
    polls = 0
    new_picks = 0
    last_flags: tuple[Any, Any] | None = None

    with out.open("a", encoding="utf-8") as log:
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                data = fetch(session, league_id, season, "mDraftDetail")
            except requests.RequestException as exc:
                print(f"  poll failed: {type(exc).__name__}: {exc}")
                time.sleep(interval)
                continue
            latency = time.monotonic() - started
            polls += 1
            now = datetime.now(UTC)

            detail = data.get("draftDetail") or {}
            flags = (detail.get("drafted"), detail.get("inProgress"))
            if flags != last_flags:
                print(f"[{now:%H:%M:%S}] drafted={flags[0]} inProgress={flags[1]}")
                log.write(
                    json.dumps(
                        {
                            "at": now.isoformat(),
                            "event": "flags",
                            "drafted": flags[0],
                            "inProgress": flags[1],
                        }
                    )
                    + "\n"
                )
                last_flags = flags

            for pick in detail.get("picks") or []:
                player_id = int(pick.get("playerId", EMPTY_PLAYER))
                number = int(pick.get("overallPickNumber", 0))
                if player_id == EMPTY_PLAYER or number in seen:
                    continue
                seen[number] = pick
                new_picks += 1
                name = names.get(player_id, f"player {player_id}")
                bid = pick.get("bidAmount")
                cost = f"${bid}" if bid else "no bid recorded"
                print(
                    f"[{now:%H:%M:%S}] pick {number:>3} r{pick.get('roundId')}  "
                    f"{name} -> team {pick.get('teamId')}  {cost}  "
                    f"(seen {latency:.2f}s into a {interval:g}s poll)"
                )
                log.write(
                    json.dumps(
                        {
                            "at": now.isoformat(),
                            "event": "pick",
                            "overall": number,
                            "round": pick.get("roundId"),
                            "player_id": player_id,
                            "name": name,
                            "team_id": pick.get("teamId"),
                            "bid": bid,
                            "poll_latency_seconds": round(latency, 3),
                        }
                    )
                    + "\n"
                )
                log.flush()

            time.sleep(max(0.0, interval - (time.monotonic() - started)))

    print(f"\n{polls} polls, {new_picks} new picks seen, {len(seen)} filled in total")
    if new_picks:
        print("VERDICT: completed picks do reach the read API live.")
    else:
        print(
            "VERDICT: nothing new appeared. Either no pick completed while watching, "
            "or this league's draft does not write here."
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--league-id", help="league id, or a draft URL containing one")
    source.add_argument("--url", help="the draft room URL, pasted whole")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between polls")
    ap.add_argument("--minutes", type=float, default=30.0, help="how long to watch")
    ap.add_argument("--out", type=Path, default=Path("draft-watch.jsonl"))
    args = ap.parse_args()

    league_id = league_id_from(args.league_id or args.url)
    return watch(
        league_id,
        args.season,
        interval=args.interval,
        minutes=args.minutes,
        out=args.out,
    )


if __name__ == "__main__":
    sys.exit(main())
