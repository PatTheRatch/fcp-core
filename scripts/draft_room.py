#!/usr/bin/env python3
"""Run the draft room, live.

Usage:
    python scripts/draft_room.py --season 2027 --me "Through The Wire"
    python scripts/draft_room.py --season 2027 --me "Through The Wire" \\
        --page "https://fantasy.espn.com/basketball/draft?leagueId=...&seasonId=2027&teamId=3"
    python scripts/draft_room.py --season 2027 --me "Through The Wire" --page URL --probe

Without --page, picks are typed. With it, a headless browser reads the draft
page every few seconds and picks are taken from the board, with typed
commands still accepted. --probe opens the page, shows what was read, and
exits: run it before the draft to confirm the page looks the way the parser
expects.

TYPED COMMANDS

    Jokic, Brighton Bears, 97     a pick: player, team, price
    me Jokic 97                   the same, for our team
    ? Jokic                       what he is worth to us right now
    undo                          take back the last pick
    state                         every team's money, places and max bid
    plan                          the best roster we can still finish
    next                          whose nomination it is
    quit

Names are matched loosely: a surname will do, a typo will usually do. An
ambiguous name is refused with the alternatives rather than guessed.

THE POOL

The room prices players from the season's projections. ESPN publishes those
in the weeks before the draft; until then --pool-season and --pool-kind can
stand in a prior season's projections or totals, which the room will say so
about loudly. Do not draft on a stand-in without knowing it is one.
"""

from __future__ import annotations

import argparse
import select
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason, Player, Team
from app.db.session import make_engine, make_session_factory
from app.draft import pool
from app.draft.availability import measured_availability
from app.draft.feed import (
    BoardSnapshot,
    LoggedPick,
    inferred_picks,
    match_name,
    match_team,
    new_picks,
    parse_board,
)
from app.draft.market import price_board
from app.draft.optimizer import Candidate, candidates_from
from app.draft.room import (
    Ceiling,
    DraftError,
    DraftState,
    Pick,
    bid_ceiling,
    inflation,
    resolve,
)
from app.draft.targets import CategoryDistribution, category_distributions
from app.draft.valuation import value_players


@dataclass(frozen=True)
class Room:
    """Everything the runner needs, loaded once."""

    season: int
    state: DraftState
    candidates: list[Candidate]
    distributions: list[CategoryDistribution]
    lineup: tuple[str, ...]
    limits: dict[str, int]
    #: Every player name we can turn into an id: the board first, then the
    #: whole players table, so a pick of someone off our board still applies.
    names: dict[str, int]
    team_names: dict[int, str]
    punt: tuple[str, ...]
    restarts: int
    #: Set when the pool is a stand-in from another season.
    stand_in: str | None = None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _teams(session: Session, league_season: LeagueSeason) -> dict[int, str]:
    rows = session.scalars(sql_select(Team).where(Team.league_season_id == league_season.id)).all()
    if rows:
        return {int(t.espn_team_id): t.name for t in rows}
    # A season whose structure is ingested but whose teams are not yet:
    # the draft is before the season, so ask ESPN.
    from app.espn import fetch_league, get_espn_settings

    league = fetch_league(get_espn_settings(), season=league_season.season)
    return {int(t.team_id): str(t.team_name) for t in league.teams}


def load_room(
    season: int,
    me: str,
    *,
    pool_season: int | None,
    pool_kind: str,
    punt: Sequence[str],
    restarts: int,
) -> Room:
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        league_season = session.scalars(
            sql_select(LeagueSeason).where(LeagueSeason.season == season)
        ).one_or_none()
        if league_season is None:
            raise SystemExit(f"season {season} is not in the database; ingest it first")
        if league_season.auction_budget <= 0:
            raise SystemExit(
                f"season {season} has no auction budget stored; re-run the ingest so the "
                "draft settings are read from ESPN"
            )

        categories = pool.season_categories(session, league_season)
        slots = pool.roster_size_for(league_season)
        teams = _teams(session, league_season)

        source_season = pool_season or season
        projections = pool.load_projections(session, source_season, kind=pool_kind)
        stand_in = None
        if not projections and pool_season is None:
            raise SystemExit(
                f"no {pool_kind} lines stored for {season}. ESPN publishes projections in "
                "the weeks before the draft; until then pass --pool-season and --pool-kind "
                "to stand in another season's, knowing that is what they are."
            )
        if source_season != season or pool_kind != "projected":
            stand_in = f"{source_season} {pool_kind}"

        distributions = category_distributions(session, league_season)
        values = value_players(projections, categories)
        board = price_board(
            values,
            teams=league_season.team_count,
            budget_per_team=league_season.auction_budget,
            roster_slots=slots,
        )
        availability = measured_availability(session).factor
        candidates = candidates_from(
            projections,
            board,
            periods=league_season.regular_season_periods,
            availability=availability,
            keys=categories,
        )
        names = {p.name: int(p.espn_player_id) for p in session.scalars(sql_select(Player)).all()}
        names.update({c.name: c.player_id for c in candidates})

        mine = match_team(me, teams.values())
        if mine is None:
            raise SystemExit(f"no team called {me!r}. Teams: {', '.join(teams.values())}")
        my_id = next(tid for tid, name in teams.items() if name == mine)

        state = DraftState.open(
            budget=league_season.auction_budget,
            roster_slots=slots,
            teams=teams,
            me=my_id,
            nomination_order=league_season.draft_order or (),
        )
        return Room(
            season=season,
            state=state,
            candidates=candidates,
            distributions=list(distributions),
            lineup=pool.lineup_for(league_season),
            limits=pool.position_limits_for(league_season),
            names=names,
            team_names=teams,
            punt=tuple(punt),
            restarts=restarts,
            stand_in=stand_in,
        )


# ---------------------------------------------------------------------------
# printing
# ---------------------------------------------------------------------------


def say(text: str = "") -> None:
    print(text, flush=True)


def show_state(room: Room, state: DraftState) -> None:
    infl = inflation(state, room.candidates)
    say(
        f"pick {len(state.picks)} of {state.roster_slots * len(state.teams)}  ·  "
        f"${state.dollars_left} left in the room  ·  inflation {infl:.2f}  ·  "
        f"field ceiling ${state.field_ceiling()}"
    )
    say(f"{'team':32s} {'left':>5} {'open':>4} {'max':>5}")
    for team in sorted(state.teams.values(), key=lambda t: -t.remaining):
        flag = " <- us" if team.team_id == state.me else ""
        say(
            f"{team.name:32s} {team.remaining:>5} {team.open_slots:>4} "
            f"{team.max_bid(state.minimum_bid):>5}{flag}"
        )


def show_ceiling(
    room: Room, state: DraftState, ceiling: Ceiling, name: str, board: BoardSnapshot | None = None
) -> None:
    mine = state.mine
    price = f"${ceiling.price}" if ceiling.price is not None else "do not bid"
    line = (
        f"{name}: {price}  (we can go to ${ceiling.max_bid}, the field to ${ceiling.field})"
        f"  marginal at $1 {ceiling.marginal_at_floor:+.3f}"
    )
    say(line)
    if board and board.on_block and board.on_block.player == name:
        block = board.on_block
        bits = []
        if block.current_offer is not None:
            who = f" by {block.high_bidder}" if block.high_bidder else ""
            bits.append(f"current offer ${block.current_offer}{who}")
        if block.espn_value is not None:
            bits.append(f"ESPN values him at ${block.espn_value}")
        if bits:
            say("  " + " · ".join(bits))
    say(f"  we hold ${mine.remaining} with {mine.open_slots} to fill")


def show_plan(room: Room, state: DraftState) -> None:
    plan = resolve(
        state,
        room.candidates,
        room.distributions,
        punt=room.punt,
        lineup=room.lineup,
        limits=room.limits,
        restarts=room.restarts,
    )
    say(f"best finish from here: {plan.expected_wins:.2f} expected categories a week, ${plan.cost}")
    owned = state.mine.player_ids
    for c in sorted(plan.players, key=lambda c: (c.player_id not in owned, -c.price)):
        tag = "owned" if c.player_id in owned else "target"
        say(f"  ${c.price:>3}  {c.name:24s} {tag}")


# ---------------------------------------------------------------------------
# resolving what was typed or read
# ---------------------------------------------------------------------------


def resolve_player(room: Room, text: str) -> int | None:
    found = match_name(text, room.names)
    if found is None:
        say(f"  no player matches {text!r}")
        return None
    if found.rival:
        say(f"  {text!r} could be {found.name} or {found.rival}; say which")
        return None
    if found.score < 1.0:
        say(f"  ({text!r} taken as {found.name})")
    return found.value


def resolve_team(room: Room, text: str) -> int | None:
    name = match_team(text, room.team_names.values())
    if name is None:
        say(f"  no team matches {text!r}")
        return None
    return next(tid for tid, n in room.team_names.items() if n == name)


def apply_pick(
    room: Room, state: DraftState, player_id: int, team_id: int, price: int
) -> DraftState:
    try:
        after = state.apply(Pick(player_id, team_id, price))
    except DraftError as exc:
        say(f"  refused: {exc}")
        return state
    name = next((n for n, pid in room.names.items() if pid == player_id), str(player_id))
    team = after.teams[team_id]
    say(
        f"  {name} -> {team.name} for ${price}   ({team.name}: ${team.remaining} left, "
        f"{team.open_slots} to fill)"
    )
    return after


def ceiling_for(room: Room, state: DraftState, player_id: int) -> Ceiling | None:
    try:
        return bid_ceiling(
            state,
            player_id,
            room.candidates,
            room.distributions,
            punt=room.punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=room.restarts,
        )
    except DraftError as exc:
        say(f"  {exc}")
        return None


def handle_command(
    room: Room, state: DraftState, line: str, board: BoardSnapshot | None = None
) -> tuple[DraftState, bool]:
    """Apply one typed line. Returns the new state and whether to keep going."""
    text = line.strip()
    if not text:
        return state, True
    lower = text.lower()
    if lower in ("quit", "q", "exit"):
        return state, False
    if lower == "undo":
        if not state.picks:
            say("  nothing to undo")
            return state, True
        last = state.picks[-1]
        name = next((n for n, pid in room.names.items() if pid == last.player_id), "?")
        say(f"  undone: {name} from {state.teams[last.team_id].name} for ${last.price}")
        return state.undo(), True
    if lower == "state":
        show_state(room, state)
        return state, True
    if lower == "plan":
        show_plan(room, state)
        return state, True
    if lower == "next":
        who = state.to_nominate()
        say(f"  {room.team_names.get(who, '?') if who else 'unknown'} to nominate")
        return state, True
    if text.startswith("?"):
        player_id = resolve_player(room, text[1:])
        if player_id is not None:
            ceiling = ceiling_for(room, state, player_id)
            if ceiling:
                name = next(n for n, pid in room.names.items() if pid == player_id)
                show_ceiling(room, state, ceiling, name, board)
        return state, True
    if lower.startswith("me "):
        parts = text[3:].rsplit(None, 1)
        if len(parts) == 2 and parts[1].lstrip("$").isdigit():
            player_id = resolve_player(room, parts[0])
            if player_id is not None:
                return apply_pick(room, state, player_id, state.me, int(parts[1].lstrip("$"))), True
        say("  me <player> <price>")
        return state, True
    parts = [p.strip() for p in text.replace("/", ",").split(",")]
    if len(parts) == 3 and parts[2].lstrip("$").isdigit():
        player_id = resolve_player(room, parts[0])
        team_id = resolve_team(room, parts[1])
        if player_id is not None and team_id is not None:
            return apply_pick(room, state, player_id, team_id, int(parts[2].lstrip("$"))), True
        return state, True
    say(
        "  not understood. Commands: <player>, <team>, <price> · me <player> <price> · "
        "? <player> · undo · state · plan · next · quit"
    )
    return state, True


# ---------------------------------------------------------------------------
# the two loops
# ---------------------------------------------------------------------------


def typed_loop(room: Room) -> None:
    state = room.state
    say(
        "typed entry. Commands: <player>, <team>, <price> · me <player> <price> · ? <player>"
        " · undo · state · plan · next · quit"
    )
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        state, keep = handle_command(room, state, line)
        if not keep:
            break


def _stdin_line() -> str | None:
    """A typed line if one is waiting, without blocking the page loop."""
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    return sys.stdin.readline() if ready else None


def page_loop(room: Room, url: str, interval: float, trust_money: bool) -> None:
    from app.draft.page import Cookies, open_draft
    from app.espn import get_espn_settings

    espn = get_espn_settings()
    state = room.state
    team_names = list(room.team_names.values())
    player_names = list(room.names)
    previous: BoardSnapshot | None = None
    last_block: str | None = None

    say(f"opening {url}")
    with open_draft(url, Cookies(espn.espn_swid, espn.espn_s2)) as page:
        say("page open; typed commands still work here")
        while True:
            board = parse_board(page.text(), team_names, player_names)
            if not board.ticker and previous is None:
                say("  no ticker on the page yet; is this the draft room?")

            for logged in new_picks(previous, board):
                state = _apply_logged(room, state, logged)
            for guess in inferred_picks(previous, board):
                say(
                    f"  money says {guess.player} went to {guess.team} for ${guess.price} "
                    "but the log does not show it"
                )
                if trust_money:
                    state = _apply_logged(room, state, guess)
                else:
                    say("  (type it to apply, or run with --trust-money)")

            block = board.on_block.player if board.on_block else None
            if block and block != last_block:
                last_block = block
                player_id = room.names.get(block) or (
                    match_name(block, room.names).value  # type: ignore[union-attr]
                    if match_name(block, room.names)
                    else None
                )
                if player_id is not None and player_id not in state.taken:
                    say(f"\nON THE BLOCK: {block}")
                    ceiling = ceiling_for(room, state, player_id)
                    if ceiling:
                        show_ceiling(room, state, ceiling, block, board)
            previous = board

            if state.complete:
                say("every place is filled; the draft is over")
                break
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                line = _stdin_line()
                if line is None:
                    time.sleep(0.1)
                    continue
                state, keep = handle_command(room, state, line, board)
                if not keep:
                    return


def _apply_logged(room: Room, state: DraftState, logged: LoggedPick) -> DraftState:
    player_id = room.names.get(logged.player)
    if player_id is None:
        found = match_name(logged.player, room.names)
        player_id = found.value if found and not found.rival else None
    if player_id is None:
        # Someone off every list we hold. The room only needs an id that is
        # unique, so make one; the money and the place are what matter.
        player_id = -abs(hash(logged.player)) % 10**9
        room.names[logged.player] = player_id
        say(f"  ({logged.player} is not on any list we hold; tracked by name)")
    team_id = resolve_team(room, logged.team)
    if team_id is None:
        return state
    return apply_pick(room, state, player_id, team_id, logged.price)


def probe(url: str, room: Room) -> None:
    from app.draft.page import Cookies, open_draft
    from app.espn import get_espn_settings

    espn = get_espn_settings()
    with open_draft(url, Cookies(espn.espn_swid, espn.espn_s2)) as page:
        time.sleep(5)
        text = page.text()
        say(f"landed on {page.current_url}")
        say(f"{len(text.splitlines())} lines of text. First 40:")
        for line in text.splitlines()[:40]:
            say("  | " + line[:110])
        board = parse_board(text, list(room.team_names.values()), list(room.names))
        say(
            f"\nparsed: {len(board.ticker)} ticker rows, {len(board.picks)} logged picks, "
            f"on block: {board.on_block.player if board.on_block else None}, "
            f"pick {board.pick_number} of {board.total_picks}"
        )
        if not board.ticker:
            say(
                "no ticker: either the draft room is not open yet, or the page has changed "
                "and app/draft/feed.py needs adjusting against this text"
            )


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True, help="our team's name")
    ap.add_argument("--page", help="the draft room URL; omit for typed entry")
    ap.add_argument("--probe", action="store_true", help="open the page, show what was read, exit")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between page reads")
    ap.add_argument(
        "--trust-money",
        action="store_true",
        help="apply picks inferred from budget drops without asking",
    )
    ap.add_argument("--punt", action="append", default=[], help="a category to give up on")
    ap.add_argument(
        "--restarts",
        type=int,
        default=4,
        help="optimizer restarts per solve; 4 answers in about a second",
    )
    ap.add_argument("--pool-season", type=int, help="stand in another season's lines")
    ap.add_argument("--pool-kind", default="projected", choices=("projected", "total"))
    args = ap.parse_args()

    room = load_room(
        args.season,
        args.me,
        pool_season=args.pool_season,
        pool_kind=args.pool_kind,
        punt=args.punt,
        restarts=args.restarts,
    )
    state = room.state
    say(
        f"{args.season} draft room · {len(state.teams)} teams · ${state.budget} · "
        f"{state.roster_slots} places · {len(room.candidates)} priced players · we are "
        f"{room.team_names[state.me]}"
    )
    if room.stand_in:
        say(
            f"!! POOL IS A STAND-IN: {room.stand_in}. Prices and ceilings reflect that season, "
            f"not {args.season}."
        )
    if room.punt:
        say(f"punting {', '.join(room.punt)}")

    if args.page and args.probe:
        probe(args.page, room)
    elif args.page:
        page_loop(room, args.page, args.interval, args.trust_money)
    else:
        typed_loop(room)
    return 0


if __name__ == "__main__":
    sys.exit(main())
