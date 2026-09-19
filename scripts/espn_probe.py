#!/usr/bin/env python3
"""Fetch one real ESPN league and print its shape. Read-only, no persistence.

Usage:
    python scripts/espn_probe.py                      # settings and the team list
    python scripts/espn_probe.py --dump-card 3112335  # one raw player-pool entry
    python scripts/espn_probe.py --pool-keys          # which fields the pool carries

Requires ESPN_LEAGUE_ID, ESPN_SWID, ESPN_S2 in the environment or .env (see
.env.example); ESPN_SEASON pins a year. This is a learning tool for
discovering what espn-api actually returns before any table gets designed
around it, not a starting point for an ingestion pipeline.

`--dump-card` and `--pool-keys` read the `kona_player_info` pool the
listener snapshots (app/listener/pool.py). The names the listener relies on
are `onTeamId` and `status` at the top level, `injuryStatus`, `injured`,
`expectedReturnDate`, `proTeamId` and the `ownership` block under `player`,
and `waiverProcessDate` for a player on waivers. Confirm the last one against
a player actually on waivers: `--pool-keys` says how many entries carry it.
"""

import argparse
import json
from collections import Counter
from typing import Any

from app.espn import fetch_current_league, fetch_player_pool, get_espn_settings
from app.listener.pool import parse_pool_entry


def _print_league(league: Any) -> None:
    """Print the league's shape, and nothing that could identify a member.

    **No names.** This is the probe most likely to be run against a league
    that is not ours — a survey of public leagues, a stranger's league someone
    linked — and a league's name, a team's name and an owner's name are
    personal data about people who never agreed to be printed. The shape is
    what this probe is for; the ids are what a second look needs. So the
    league is named by its id and its season, teams by their team id, and an
    owner by nothing at all: `unclaimed` or `claim-1`, `claim-2`, ... in team
    order, which is enough to see that two teams share an owner.
    """
    print(f"League: id {league.league_id}, season {league.year}")
    print(f"  teams: {league.settings.team_count}")
    print(f"  scoring_type: {league.settings.scoring_type}")
    print(f"  regular season matchup periods: {league.settings.reg_season_count}")
    print(f"  playoff teams: {league.settings.playoff_team_count}")
    print()

    # In an H2H_CATEGORY league `team.wins/losses/ties` count CATEGORIES won,
    # not matchups won: they sum to (matchup periods x categories) per team.
    # ESPN exposes no matchup record here; it has to be derived from the
    # schedule endpoint, so label these for what they actually are.
    #
    # Two teams may be run by the same owner, and who owns which is a real
    # fact about the league's shape, so the claim label is per owner id
    # (ESPN's own), not per team: `claim-1` on two rows is one person with
    # two teams, which is why this is worth keeping and why it can be kept
    # without a name.
    claims: dict[str, str] = {}
    print(f"Teams ({len(league.teams)}) - W-L-T below are category tallies, not matchup records:")
    for team in league.teams:
        owner_ids = [str(o.get("id")) for o in (team.owners or []) if o.get("id") is not None]
        claims.setdefault(next(iter(owner_ids), ""), f"claim-{len(claims) + 1}")
        label = " ".join(claims[i] for i in owner_ids) or "unclaimed"
        categories = team.wins + team.losses + team.ties
        print(
            f"  [{team.team_id:>2}] {label:<16} "
            f"cat {team.wins}-{team.losses}-{team.ties} of {categories}"
        )


def _dump_card(league: Any, player_id: int) -> None:
    entries = fetch_player_pool(league)
    match = next((e for e in entries if int(e.get("id") or 0) == player_id), None)
    if match is None:
        print(f"player {player_id} is not in the pool ({len(entries)} entries)")
        return
    trimmed = dict(match)
    player = dict(trimmed.get("player") or {})
    # The stat splits are the S1 probe's subject, not this one's, and run to pages.
    player.pop("stats", None)
    trimmed["player"] = player
    print(json.dumps(trimmed, indent=2, sort_keys=True))
    print()
    print("parsed:", parse_pool_entry(match))


def _pool_keys(league: Any) -> None:
    entries = fetch_player_pool(league)
    top: Counter[str] = Counter()
    under_player: Counter[str] = Counter()
    ownership: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    injury: Counter[str] = Counter()
    for entry in entries:
        top.update(entry.keys())
        player = entry.get("player") or {}
        under_player.update(player.keys())
        ownership.update((player.get("ownership") or {}).keys())
        statuses[str(entry.get("status"))] += 1
        injury[str(player.get("injuryStatus"))] += 1

    print(f"{len(entries)} entries")
    for title, counter in (
        ("top-level keys", top),
        ("player keys", under_player),
        ("ownership keys", ownership),
        ("status", statuses),
        ("injuryStatus", injury),
    ):
        print(f"\n{title}:")
        for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {key:<28} {count}")
    on_waivers = [e for e in entries if e.get("status") == "WAIVERS"]
    with_date = [e for e in on_waivers if e.get("waiverProcessDate")]
    print(f"\nwaiverProcessDate: on {len(with_date)} of {len(on_waivers)} WAIVERS entries")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-card", type=int, metavar="PLAYER_ID", help="print one raw entry")
    parser.add_argument("--pool-keys", action="store_true", help="summarise the pool's fields")
    args = parser.parse_args()

    league = fetch_current_league(get_espn_settings())
    if args.dump_card is not None:
        _dump_card(league, args.dump_card)
    elif args.pool_keys:
        _pool_keys(league)
    else:
        _print_league(league)


if __name__ == "__main__":
    main()
