"""Minimal Sleeper client.

Read-only, and read-only is not a caution here but the whole API: Sleeper
publishes no write endpoints and no login. Every call below is an anonymous
GET, so a league needs nothing from a user except its id — no cookies, no
OAuth, nothing to seal in `league_connections`. That is the one way Sleeper
is *easier* than ESPN.

Nothing here persists anything, and nothing here maps onto our tables: this
is the fetch layer only, matching `app.espn`'s split. docs/sleeper.md has
what the responses carry and how it would land in the canonical schema.

Two facts shape every function:

* **A Sleeper league id is one season.** ESPN keeps one league id and asks
  which year you want; Sleeper mints a fresh id per season and links last
  year's through `previous_league_id`. So a league's history is a walk, not a
  parameter — `league_history` does that walk.
* **Weeks are the unit.** Matchups and transactions are fetched one week at a
  time; there is no "give me the season" call. `total_weeks` says how many to
  ask for, and the helpers loop.

Sleeper asks for under 1000 calls a minute, which a whole-season pull is
nowhere near (about 40 calls), and asks that the player dump be fetched at
most once a day — it is several megabytes and changes slowly. `fetch_players`
carries that warning rather than enforcing it; a cache belongs in the ingest.
"""

from collections.abc import Iterator
from typing import Any

import requests

#: Every documented endpoint hangs off this. There is no v2.
SLEEPER_BASE = "https://api.sleeper.app/v1"

#: (connect, read). Generous on the read because `/players/<sport>` is a
#: multi-megabyte document; every other call returns in well under a second.
SLEEPER_TIMEOUT_SECONDS = (5, 60)

#: Sleeper's own name for the sports it serves. This repo is basketball, so
#: `nba` is the only one that means anything downstream — but a league states
#: its sport and the probe reports it, because an `nfl` league id would make
#: everything past the ingest inapplicable rather than merely unmapped.
NBA = "nba"


class SleeperError(RuntimeError):
    """A Sleeper request that did not come back usable."""


def _get(path: str) -> Any:
    """GET one Sleeper path. Returns None for 404, raises for anything else.

    Sleeper answers an unknown league, draft or week with `null` and HTTP 200
    about as often as with a 404, so callers must treat both as "nothing
    there". Returning None for each keeps that in one place.
    """
    url = f"{SLEEPER_BASE}/{path.lstrip('/')}"
    try:
        response = requests.get(url, timeout=SLEEPER_TIMEOUT_SECONDS)
    except requests.RequestException as exc:  # network, DNS, timeout, proxy
        raise SleeperError(f"GET {url} failed: {exc}") from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SleeperError(f"GET {url} returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise SleeperError(f"GET {url} returned a body that is not JSON") from exc


def _require(path: str, what: str) -> Any:
    value = _get(path)
    if value is None:
        raise SleeperError(f"Sleeper has no {what} at /{path.lstrip('/')}")
    return value


# --- one league season ------------------------------------------------------


def fetch_league(league_id: str) -> dict[str, Any]:
    """The league's own row: sport, season, status, settings, scoring, slots.

    This is the call that answers the two questions that decide how much of
    this codebase applies at all — `sport`, and whether the scoring is
    categories, points or roto (docs/sleeper.md).
    """
    league: dict[str, Any] = _require(f"league/{league_id}", f"league {league_id}")
    return league


def fetch_rosters(league_id: str) -> list[dict[str, Any]]:
    """One row per team: who owns it, who is on it, and its season record.

    `roster_id` (1..n, stable for the season) is the team key everywhere else
    in the API. `owner_id` is a Sleeper user id, which is the person, and is
    null on a seat nobody has taken yet.

    Beyond `players` and `starters` a roster carries `reserve` (who is on IR —
    IR is not a `roster_positions` entry), `taxi`, `keepers` and `player_map`.
    `starters` has one entry per starting slot even when empty, and an empty
    slot is the string `"0"`, not a missing entry: `"0"` is not a player.
    """
    return _require(f"league/{league_id}/rosters", f"rosters for league {league_id}") or []


def fetch_users(league_id: str) -> list[dict[str, Any]]:
    """One row per person in the league: user id, display name, team name.

    The team's *name* lives here (in `metadata.team_name`), not on the roster,
    and it is absent when a manager never set one — Sleeper then shows the
    display name instead, which is a presentation rule we have to repeat.
    `is_owner` is true for the commissioner, not for team owners. A seat
    nobody has joined has a roster but no user row.
    """
    return _require(f"league/{league_id}/users", f"users for league {league_id}") or []


def fetch_matchups(league_id: str, week: int) -> list[dict[str, Any]]:
    """Every team's line for one week. Empty once past the played season.

    Two rows sharing a `matchup_id` are the pairing; a row whose `matchup_id`
    is null had no opponent that week.
    """
    return _get(f"league/{league_id}/matchups/{week}") or []


def fetch_transactions(league_id: str, week: int) -> list[dict[str, Any]]:
    """Adds, drops, waiver claims and trades filed in one week.

    Failed claims come back too, with `status` "failed" and the bid intact,
    which is the same reason `app.ingest` keeps ESPN's failed ones.
    """
    return _get(f"league/{league_id}/transactions/{week}") or []


def fetch_traded_picks(league_id: str) -> list[dict[str, Any]]:
    """Future draft picks that have changed hands. Empty in a redraft league."""
    return _get(f"league/{league_id}/traded_picks") or []


def fetch_winners_bracket(league_id: str) -> list[dict[str, Any]]:
    """The playoff bracket, laid out from the day the league is created.

    It is not empty before the playoffs: a pre-draft league already returns
    every game, seeded provisionally. Each game is `r` (round), `m` (game
    number), `t1`/`t2` (roster ids, null until known), `t1_from`/`t2_from`
    (`{"w": m}` or `{"l": m}`: the winner or loser of game m), `w`/`l` (the
    result, null until played) and, on placement games only, `p` (the place
    it decides: 1 is the final, 3 third place). So the seeds are not real
    until `playoff_week_start`, and only `w` says a game has happened.
    """
    return _get(f"league/{league_id}/winners_bracket") or []


def fetch_losers_bracket(league_id: str) -> list[dict[str, Any]]:
    """The consolation bracket, same shape as the winners' one and also present from the start."""
    return _get(f"league/{league_id}/losers_bracket") or []


# --- drafts -----------------------------------------------------------------


def fetch_drafts(league_id: str) -> list[dict[str, Any]]:
    """The league's drafts, newest first. Normally one; more in a dynasty rookie draft."""
    return _get(f"league/{league_id}/drafts") or []


def fetch_draft(draft_id: str) -> dict[str, Any]:
    """One draft's settings: type, rounds, budget, order, slot-to-team map."""
    draft: dict[str, Any] = _require(f"draft/{draft_id}", f"draft {draft_id}")
    return draft


def fetch_draft_picks(draft_id: str) -> list[dict[str, Any]]:
    """Every pick made, with the player, the team, the slot and the price.

    Unlike ESPN, a draft is a separate request from the league — cheap, but
    it does mean the draft is not free the way `app.ingest` gets ESPN's.
    """
    return _get(f"draft/{draft_id}/picks") or []


# --- global -----------------------------------------------------------------


def fetch_state(sport: str = NBA) -> dict[str, Any]:
    """Where the sport is right now: current week, season, and season type.

    The only way to know which week to stop fetching at during a live season.
    """
    state: dict[str, Any] = _require(f"state/{sport}", f"state for {sport}")
    return state


def fetch_players(sport: str = NBA) -> dict[str, dict[str, Any]]:
    """Every player Sleeper knows, keyed by Sleeper player id. About 2.7 MB.

    Sleeper asks that this be called at most once a day, and means it.

    It is *not* an id bridge to our `players` table, whatever the field list
    suggests. Every NBA entry has an `espn_id` key, and on 2026-09-19 it was
    null on all 2117 of them (`yahoo_id` too). The ids that are filled are
    `sportradar_id`, `fantasy_data_id`, `rotowire_id` and `kalshi_id`, none
    of which we store — so the join is a name match, docs/sleeper.md
    finding 5. `scripts/sleeper_probe.py --players` measures it.

    Thirty entries are not players: each NBA team appears keyed by its
    abbreviation (`"BKN"`), position `DEF`, with no `full_name`.
    """
    players: dict[str, dict[str, Any]] = _require(f"players/{sport}", f"the {sport} player dump")
    return players


# --- the seasons behind this one -------------------------------------------


def league_history(league_id: str, limit: int = 25) -> Iterator[dict[str, Any]]:
    """This league and every earlier season of it, newest first.

    A Sleeper league id names one season; `previous_league_id` chains them.
    `limit` is a stop on a chain that loops back on itself, which a league
    copied from itself can do.
    """
    seen: set[str] = set()
    current: str | None = str(league_id)
    while current and current not in seen and len(seen) < limit:
        seen.add(current)
        league = _get(f"league/{current}")
        if league is None:
            return
        yield league
        current = previous_league_id(league)


def previous_league_id(league: dict[str, Any]) -> str | None:
    """The season before this one, or None when this is the first.

    Sleeper says "no previous season" two ways: `null`, and the string
    `"0"` (both seen in NBA leagues on 2026-09-19). The second is truthy, so
    a bare `if previous` would go looking for league 0.

    `metadata.copy_from_league_id` is not a substitute. It marks a league
    created by copying another's settings, it is carried forward into later
    seasons unchanged, and it can sit beside a null `previous_league_id` —
    so it records where the settings came from, not which season came before.
    """
    previous = league.get("previous_league_id")
    if previous is None or str(previous) in ("", "0"):
        return None
    return str(previous)
