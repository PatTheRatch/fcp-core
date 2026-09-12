#!/usr/bin/env python3
"""Fetch one real ESPN league and print its shape. Read-only, no persistence.

Usage:
    python scripts/espn_probe.py

Requires ESPN_LEAGUE_ID, ESPN_SEASON, ESPN_SWID, ESPN_S2 in the environment
or .env (see .env.example). This is a learning tool for discovering what
espn-api actually returns before any table gets designed around it — not a
starting point for an ingestion pipeline.
"""

from app.espn import fetch_league, get_espn_settings


def main() -> None:
    settings = get_espn_settings()
    league = fetch_league(settings)

    print(f"League: {league.settings.name!r} ({settings.espn_season})")
    print(f"  teams: {league.settings.team_count}")
    print(f"  scoring_type: {league.settings.scoring_type}")
    print(f"  regular season matchup periods: {league.settings.reg_season_count}")
    print(f"  playoff teams: {league.settings.playoff_team_count}")
    print()

    # In an H2H_CATEGORY league `team.wins/losses/ties` count CATEGORIES won,
    # not matchups won: they sum to (matchup periods x categories) per team.
    # ESPN exposes no matchup record here — it has to be derived from the
    # schedule endpoint — so label these for what they actually are.
    print(f"Teams ({len(league.teams)}) — W-L-T below are category tallies, not matchup records:")
    for team in league.teams:
        owners = (
            ", ".join(o.get("firstName", "?") for o in team.owners) if team.owners else "unclaimed"
        )
        categories = team.wins + team.losses + team.ties
        print(
            f"  [{team.team_id:>2}] {team.team_name:<28} "
            f"cat {team.wins}-{team.losses}-{team.ties} of {categories}  owners: {owners}"
        )


if __name__ == "__main__":
    main()
