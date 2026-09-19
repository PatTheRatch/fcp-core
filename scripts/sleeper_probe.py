#!/usr/bin/env python3
"""Fetch one real Sleeper league and print its shape. Read-only, no persistence.

Usage:
    python scripts/sleeper_probe.py 1404516094114377728          # the summary
    python scripts/sleeper_probe.py --settings                   # every settings key, verbatim
    python scripts/sleeper_probe.py --history                    # this season and earlier ones
    python scripts/sleeper_probe.py --season                     # count a whole season's weeks
    python scripts/sleeper_probe.py --week 3                     # one week, matchups and moves
    python scripts/sleeper_probe.py --draft                      # the draft and its picks
    python scripts/sleeper_probe.py --players                    # can we join Sleeper to players?
    python scripts/sleeper_probe.py --raw league                 # one endpoint's JSON, unedited

The league id comes from the argument, or `SLEEPER_LEAGUE_ID` in the
environment or .env. Sleeper needs no credentials at all: every call is an
anonymous GET.

This is the Sleeper twin of `scripts/espn_probe.py` and has the same job —
find out what the platform actually returns before any table is designed
around it. It is not the start of an ingest. Nothing here writes to the
database, and nothing here maps a field onto a canonical column; that mapping
is proposed in docs/sleeper.md and is not built.

Three questions decide how much of this codebase can serve the league, and
the default summary leads with all three:

1. **Which sport.** Full Court Press is fantasy basketball end to end. An
   `nfl` league can be ingested in the same shape but means nothing to the
   scoring, pickups and draft packages.
2. **Which scoring.** Only head-to-head categories is currency the scoring
   package understands (docs/platforms.md, "Not covered: roto and points").
   The probe prints the evidence rather than a verdict, because Sleeper's
   settings say it obliquely and a wrong guess here would be expensive.
3. **Can we join the players.** Sleeper's player dump carries each player's
   `espn_id`, so the cross-platform match docs/platforms.md budgets for may
   be an id join instead of name matching. `--players` measures how much of
   this league's rostered pool that actually covers.
"""

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable
from typing import Any

from app import sleeper

#: Stop looking for weeks after this many. An NBA fantasy season runs to
#: about 25 scoring weeks; the loop also gives up early on a quiet stretch.
MAX_WEEK = 30


# --- helpers ----------------------------------------------------------------


def _league_id(argument: str | None) -> str:
    league_id = argument or os.environ.get("SLEEPER_LEAGUE_ID") or _from_dotenv("SLEEPER_LEAGUE_ID")
    if not league_id:
        raise SystemExit(
            "No league id. Pass one as an argument or set SLEEPER_LEAGUE_ID in the environment."
        )
    return str(league_id).strip()


def _from_dotenv(key: str) -> str | None:
    """Read one key out of .env, so the probe matches how the rest of the repo is configured."""
    try:
        with open(".env", encoding="utf-8") as handle:
            for line in handle:
                name, _, value = line.partition("=")
                if name.strip() == key and value.strip():
                    return value.strip()
    except OSError:
        pass
    return None


def _team_names(users: list[dict[str, Any]]) -> dict[str, str]:
    """Sleeper user id to the name to show for their team.

    The team name is optional: a manager who never set one is shown by
    display name instead. That fallback is Sleeper's own behaviour, so we
    have to repeat it rather than store an empty name.
    """
    names = {}
    for user in users:
        metadata = user.get("metadata") or {}
        names[str(user.get("user_id"))] = (
            metadata.get("team_name") or user.get("display_name") or "?"
        )
    return names


def _print_json(label: str, value: Any) -> None:
    print(f"\n{label}:")
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


# --- the summary ------------------------------------------------------------


def _scoring_evidence(league: dict[str, Any]) -> None:
    """Print what the league says about its scoring, and refuse to conclude.

    Sleeper does not carry a plain "this is a categories league" flag in a
    place worth trusting. What it carries is a `scoring_settings` map of stat
    key to weight, plus assorted settings; a points league weights stats
    unevenly, a categories league tends towards a flat map. That is a tell,
    not a fact, so this prints both the shape and the raw map and leaves the
    call to a person looking at the league's own page.
    """
    scoring = league.get("scoring_settings") or {}
    print("\nScoring — the evidence, not a verdict (see the module docstring):")
    if not scoring:
        print("  scoring_settings is empty, which itself is worth explaining")
    else:
        weights = Counter(str(v) for v in scoring.values())
        print(f"  scoring_settings has {len(scoring)} stat keys")
        print(f"  distinct weights: {', '.join(f'{w} x{n}' for w, n in weights.most_common())}")
        print("  stats scored: " + ", ".join(sorted(scoring)))

    settings = league.get("settings") or {}
    telling = {
        k: v
        for k, v in settings.items()
        if any(word in k.lower() for word in ("scor", "categor", "roto", "best_ball", "average"))
    }
    print(f"  settings keys that mention scoring: {telling or 'none'}")
    metadata = league.get("metadata") or {}
    if metadata:
        print(f"  league metadata: {metadata}")
    print("  -> confirm against the league's own settings page before mapping anything.")


def _summary(league_id: str) -> None:
    league = sleeper.fetch_league(league_id)
    users = sleeper.fetch_users(league_id)
    rosters = sleeper.fetch_rosters(league_id)
    names = _team_names(users)

    print(f"League: {league.get('name')!r}  ({league.get('league_id')})")
    print(f"  sport:            {league.get('sport')!r}")
    print(f"  season:           {league.get('season')!r} ({league.get('season_type')})")
    print(f"  status:           {league.get('status')!r}")
    print(f"  rosters:          {league.get('total_rosters')}")
    print(f"  previous season:  {league.get('previous_league_id') or 'none — this is the first'}")
    print(f"  draft id:         {league.get('draft_id') or 'none'}")

    if str(league.get("sport")) != sleeper.NBA:
        print(
            f"\n  !! sport is {league.get('sport')!r}, not 'nba'. This repo is fantasy basketball "
            "end to end;\n     everything past the ingest would be inapplicable."
        )

    settings = league.get("settings") or {}
    slots = league.get("roster_positions") or []
    starters = [s for s in slots if s not in ("BN", "IR", "TAXI")]
    print("\nRoster:")
    print(f"  roster_positions ({len(slots)}): {', '.join(slots) if slots else 'none reported'}")
    print(f"  starters {len(starters)}, bench {slots.count('BN')}, IR {slots.count('IR')}")
    for key in (
        "num_teams",
        "playoff_teams",
        "playoff_week_start",
        "playoff_type",
        "leg",
        "waiver_type",
        "waiver_budget",
        "trade_deadline",
        "type",
        "max_keepers",
        "draft_rounds",
        "reserve_slots",
        "taxi_slots",
        "disable_trades",
    ):
        if key in settings:
            print(f"  {key:<20} {settings[key]}")

    _scoring_evidence(league)

    print(f"\nTeams ({len(rosters)}) — roster_id is the key every other endpoint uses:")
    for roster in sorted(rosters, key=lambda r: r.get("roster_id") or 0):
        owner = str(roster.get("owner_id"))
        team_settings = roster.get("settings") or {}
        record = (
            f"{team_settings.get('wins', 0)}-"
            f"{team_settings.get('losses', 0)}-"
            f"{team_settings.get('ties', 0)}"
        )
        players = roster.get("players") or []
        co_owners = roster.get("co_owners") or []
        print(
            f"  [{roster.get('roster_id'):>2}] {names.get(owner, '(unclaimed)'):<28} "
            f"{record:<10} {len(players):>3} players  "
            f"fpts {team_settings.get('fpts', 0)}  "
            f"faab spent {team_settings.get('waiver_budget_used', 0)}"
            + (f"  +{len(co_owners)} co-owner(s)" if co_owners else "")
        )

    unclaimed = [r for r in rosters if not r.get("owner_id")]
    if unclaimed:
        print(f"  ({len(unclaimed)} roster(s) with no owner — orphan teams)")

    print(
        "\nWhat is NOT in the summary and needs its own call: "
        "matchups and transactions (per week, --season),\n"
        "the draft and its picks (--draft), the playoff bracket, "
        "traded picks, and the player dump (--players)."
    )


# --- the other passes -------------------------------------------------------


def _settings(league_id: str) -> None:
    """Every settings key verbatim. The summary picks favourites; this hides nothing."""
    league = sleeper.fetch_league(league_id)
    _print_json("settings", league.get("settings"))
    _print_json("scoring_settings", league.get("scoring_settings"))
    _print_json("roster_positions", league.get("roster_positions"))
    top = {k: v for k, v in league.items() if not isinstance(v, (dict, list))}
    _print_json("top-level scalar fields", top)


def _history(league_id: str) -> None:
    """Walk `previous_league_id` back. Each season is its own league id on Sleeper."""
    print("Seasons, newest first. Each is a separate league id:")
    count = 0
    for league in sleeper.league_history(league_id):
        count += 1
        settings = league.get("settings") or {}
        print(
            f"  {league.get('season')}  {league.get('league_id'):<22} "
            f"{str(league.get('name'))[:30]:<32} "
            f"{league.get('total_rosters')} teams  status={league.get('status')}  "
            f"draft={league.get('draft_id') or '-'}"
        )
        if settings.get("type") is not None:
            print(f"          type={settings['type']} (0 redraft, 1 keeper, 2 dynasty)")
    print(f"\n{count} season(s). A full history pull is this many times the per-season calls.")


def _week(league_id: str, week: int) -> None:
    matchups = sleeper.fetch_matchups(league_id, week)
    transactions = sleeper.fetch_transactions(league_id, week)
    print(f"Week {week}: {len(matchups)} matchup rows, {len(transactions)} transactions")

    pairings: dict[Any, list[dict[str, Any]]] = {}
    for row in matchups:
        pairings.setdefault(row.get("matchup_id"), []).append(row)
    for matchup_id, rows in sorted(pairings.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if matchup_id is None:
            # These rows share no opponent, they share the *absence* of one.
            # Printing them as a pairing would invent a game nobody played.
            for row in rows:
                print(f"  no matchup_id (bye or unplayed): roster {row.get('roster_id')}")
            continue
        sides = "  vs  ".join(f"roster {r.get('roster_id')} {r.get('points')}" for r in rows)
        print(f"  matchup {matchup_id}: {sides}")

    if matchups:
        _print_json("one matchup row, whole", matchups[0])
    if transactions:
        kinds = Counter(f"{t.get('type')}/{t.get('status')}" for t in transactions)
        print(f"\n  transaction kinds: {dict(kinds)}")
        _print_json("one transaction, whole", transactions[0])


def _season(league_id: str, max_week: int) -> None:
    """Sweep every week and count. This is the cost of one season's ingest, measured."""
    league = sleeper.fetch_league(league_id)
    print(f"Sweeping {league.get('season')} weeks 1..{max_week} (2 calls each)\n")
    kinds: Counter[str] = Counter()
    total_matchups = total_moves = 0
    quiet = 0
    calls = 1
    for week in range(1, max_week + 1):
        matchups = sleeper.fetch_matchups(league_id, week)
        transactions = sleeper.fetch_transactions(league_id, week)
        calls += 2
        total_matchups += len(matchups)
        total_moves += len(transactions)
        kinds.update(f"{t.get('type')}/{t.get('status')}" for t in transactions)
        played = sum(1 for m in matchups if m.get("points"))
        print(
            f"  week {week:>2}: {len(matchups):>2} rows ({played} with points), "
            f"{len(transactions):>3} moves"
        )
        if not matchups and not transactions:
            quiet += 1
            if quiet >= 3:
                print(f"  (three quiet weeks; stopping at {week})")
                break
        else:
            quiet = 0

    print(f"\n{total_matchups} matchup rows, {total_moves} transactions, in {calls} calls")
    print("transaction kinds:")
    for kind, count in kinds.most_common():
        print(f"  {kind:<28} {count}")

    bracket = sleeper.fetch_winners_bracket(league_id)
    losers = sleeper.fetch_losers_bracket(league_id)
    traded = sleeper.fetch_traded_picks(league_id)
    print(
        f"\nwinners bracket: {len(bracket)} games, losers bracket: {len(losers)}, "
        f"traded picks: {len(traded)}"
    )
    if bracket:
        _print_json("one bracket game", bracket[0])


def _draft(league_id: str) -> None:
    drafts = sleeper.fetch_drafts(league_id)
    print(f"{len(drafts)} draft(s) on this league")
    for entry in drafts:
        draft = sleeper.fetch_draft(str(entry.get("draft_id")))
        settings = draft.get("settings") or {}
        print(
            f"\nDraft {draft.get('draft_id')}  type={draft.get('type')!r} "
            f"status={draft.get('status')!r} season={draft.get('season')}"
        )
        print(f"  metadata: {draft.get('metadata')}")
        for key in (
            "teams",
            "rounds",
            "budget",
            "pick_timer",
            "nomination_timer",
            "reversal_round",
            "player_type",
            "enforce_position_limits",
        ):
            if key in settings:
                print(f"  {key:<24} {settings[key]}")
        print(f"  slot_to_roster_id: {draft.get('slot_to_roster_id')}")
        picks = sleeper.fetch_draft_picks(str(draft.get("draft_id")))
        print(f"  {len(picks)} picks made")
        if picks:
            keepers = sum(1 for p in picks if p.get("is_keeper"))
            priced = [p for p in picks if (p.get("metadata") or {}).get("amount")]
            print(f"  keepers: {keepers}; picks carrying an auction amount: {len(priced)}")
            _print_json("  one pick, whole", picks[0])


def _players(league_id: str) -> None:
    """Measure the join to our `players` table. This is the interesting one.

    docs/platforms.md budgets a player-matching step for a second platform:
    match by name, NBA team and position, refuse a tie. Sleeper may make that
    unnecessary — every player it knows carries `espn_id` — but only if the
    coverage is real. This counts it over the players this league actually
    rosters, which is the population that matters, not the whole dump.
    """
    league = sleeper.fetch_league(league_id)
    sport = str(league.get("sport") or sleeper.NBA)
    rosters = sleeper.fetch_rosters(league_id)
    print(f"Fetching the {sport} player dump (several MB; Sleeper asks for once a day at most)...")
    players = sleeper.fetch_players(sport)
    print(f"{len(players)} players in the dump\n")

    rostered = {str(p) for r in rosters for p in (r.get("players") or [])}
    print(f"{len(rostered)} distinct players rostered in this league")

    id_fields = (
        "espn_id",
        "yahoo_id",
        "rotowire_id",
        "swish_id",
        "stats_id",
        "fantasy_data_id",
        "sportradar_id",
        "gsis_id",
        "oddsjam_id",
        "opta_id",
    )
    coverage: Counter[str] = Counter()
    missing_espn: list[str] = []
    unknown: list[str] = []
    for player_id in sorted(rostered):
        entry = players.get(player_id)
        if entry is None:
            unknown.append(player_id)
            continue
        for field in id_fields:
            if entry.get(field):
                coverage[field] += 1
        if not entry.get("espn_id"):
            missing_espn.append(entry.get("full_name") or player_id)

    print("\ncross-platform ids present on this league's rostered players:")
    for field in id_fields:
        count = coverage[field]
        if count:
            print(f"  {field:<20} {count:>4} of {len(rostered)}")
    if unknown:
        print(f"\n  {len(unknown)} rostered id(s) absent from the dump: {unknown[:10]}")
    if missing_espn:
        print(f"\n  no espn_id ({len(missing_espn)}): {', '.join(missing_espn[:20])}")
        print("  ^ these are the ones a name match would have to settle, or a person would.")
    else:
        print("\n  every rostered player carries an espn_id: the join can be an id join.")

    sample = next((players[p] for p in sorted(rostered) if p in players), None)
    if sample:
        _print_json("one player entry, whole", sample)


def _raw(league_id: str, what: str, week: int) -> None:
    calls: dict[str, Callable[[], Any]] = {
        "league": lambda: sleeper.fetch_league(league_id),
        "users": lambda: sleeper.fetch_users(league_id),
        "rosters": lambda: sleeper.fetch_rosters(league_id),
        "matchups": lambda: sleeper.fetch_matchups(league_id, week),
        "transactions": lambda: sleeper.fetch_transactions(league_id, week),
        "traded_picks": lambda: sleeper.fetch_traded_picks(league_id),
        "winners_bracket": lambda: sleeper.fetch_winners_bracket(league_id),
        "losers_bracket": lambda: sleeper.fetch_losers_bracket(league_id),
        "drafts": lambda: sleeper.fetch_drafts(league_id),
        "state": lambda: sleeper.fetch_state(),
    }
    if what not in calls:
        raise SystemExit(f"--raw takes one of: {', '.join(sorted(calls))}")
    print(json.dumps(calls[what](), indent=2, sort_keys=True, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("league_id", nargs="?", help="Sleeper league id; else SLEEPER_LEAGUE_ID")
    parser.add_argument("--settings", action="store_true", help="every settings key, verbatim")
    parser.add_argument("--history", action="store_true", help="this season and the ones before it")
    parser.add_argument("--season", action="store_true", help="sweep every week and count")
    parser.add_argument("--week", type=int, metavar="N", help="one week's matchups and moves")
    parser.add_argument("--draft", action="store_true", help="the draft and its picks")
    parser.add_argument("--players", action="store_true", help="measure the join to our players")
    parser.add_argument("--raw", metavar="ENDPOINT", help="one endpoint's JSON, unedited")
    parser.add_argument("--max-week", type=int, default=MAX_WEEK, help="cap for --season")
    args = parser.parse_args()

    league_id = _league_id(args.league_id)
    try:
        if args.raw:
            _raw(league_id, args.raw, args.week or 1)
        elif args.settings:
            _settings(league_id)
        elif args.history:
            _history(league_id)
        elif args.season:
            _season(league_id, args.max_week)
        elif args.week is not None:
            _week(league_id, args.week)
        elif args.draft:
            _draft(league_id)
        elif args.players:
            _players(league_id)
        else:
            _summary(league_id)
    except sleeper.SleeperError as exc:
        raise SystemExit(f"Sleeper: {exc}") from exc


if __name__ == "__main__":
    main()
