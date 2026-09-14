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

    --bbm BBM_Projections_2027_total.xls           draft on Basketball Monster's
    --bbm-per-game BBM_Projections_2027_pergame.xls   and show its per-game values

The manager drafts on Basketball Monster's projections, and --bbm reads the
export directly: its games already price availability, so no discount is
stacked on them, and every row goes on the board, rookies included. Its
age, injury risk and ESPN and Yahoo average auction prices are shown with
each ceiling. BBM's dollar values depend on the page's Value Type: an
export on Total Games Value prices missed games, one on Per Game Value
does not. Pass the total export as --bbm and the per-game one as
--bbm-per-game and the readout shows both; a wide gap is an injury
discount, which an active manager with an IR slot can partly collect.
Without --bbm the room prices from ESPN's stored projections;
--pool-season and --pool-kind can stand in a prior season's, which the room
will say so about loudly.

THE PLAN

The room bids to an allocation (see app/draft/room.py): no player takes
more of the budget than the largest place still open in it, plus
--plan-slack. By default the allocation is the spending shape of this
league's best category teams (app/draft/shape.py); --plan optimizer uses
the optimizer's own best roster from the empty room, which on BBM's 2026
projections was $103 on one star and ten one-dollar players; --plan none
bids on the ceiling alone.
"""

from __future__ import annotations

import argparse
import select
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason, Player, Team
from app.db.session import make_engine, make_session_factory
from app.draft import pool
from app.draft.availability import measured_availability
from app.draft.bbm import BBMRow, load_bbm, name_key, read_bbm
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
    Allocation,
    Ceiling,
    DraftError,
    DraftState,
    Pick,
    bid_ceiling,
    inflation,
    plan_allocation,
    reprice,
    resolve,
)
from app.draft.shape import winning_shape
from app.draft.targets import CategoryDistribution, category_distributions
from app.draft.tiers import LEAGUE_TIER_CURVE, apply_tier_curve
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
    #: The spending plan bids are capped to, when there is one.
    allocation: Allocation | None = None
    #: Where the allocation came from, for the readout.
    plan_source: str = "none"
    #: Basketball Monster's row for each player, when drafting on BBM.
    bbm: dict[int, BBMRow] = field(default_factory=dict)
    #: BBM's league value on a per-game basis, from a second export.
    per_game_dollars: dict[int, float] = field(default_factory=dict)
    #: One line about the pool, for the header.
    pool_note: str = ""


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
    tier_curve: bool = True,
    bbm: Path | None = None,
    bbm_per_game: Path | None = None,
    plan: str = "history",
    plan_slack: float = 0.10,
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
        stand_in = None
        bbm_rows: dict[int, BBMRow] = {}
        if bbm is not None:
            loaded = load_bbm(session, bbm, season)
            projections = loaded.projections
            bbm_rows = loaded.rows
            pool_note = (
                f"pool: Basketball Monster, {bbm.name}: {len(projections)} players, "
                f"{loaded.matched} matched to ESPN ids ({len(loaded.loose)} by short first name), "
                f"{len(loaded.unmatched)} on the board by name only"
            )
        else:
            projections = pool.load_projections(session, source_season, kind=pool_kind)
            pool_note = f"pool: ESPN {source_season} {pool_kind}"
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
        if tier_curve:
            # Reshape to how this league actually spends: about half again on
            # the top five, less below rank 60. See app/draft/tiers.py.
            board = apply_tier_curve(board, LEAGUE_TIER_CURVE)
        # BBM's games already price availability; ESPN's do not.
        availability = 1.0 if bbm is not None else measured_availability(session).factor
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
        allocation = None
        lineup = pool.lineup_for(league_season)
        limits = pool.position_limits_for(league_season)
        if plan == "history":
            shape = winning_shape(session, roster_slots=slots, budget=state.budget)
            allocation = Allocation.from_prices(shape, state, slack=plan_slack)
        elif plan == "optimizer":
            allocation, _ = plan_allocation(
                state,
                candidates,
                distributions,
                slack=plan_slack,
                punt=punt,
                lineup=lineup,
                limits=limits,
            )
        return Room(
            season=season,
            state=state,
            candidates=candidates,
            distributions=list(distributions),
            lineup=lineup,
            limits=limits,
            names=names,
            team_names=teams,
            punt=tuple(punt),
            restarts=restarts,
            stand_in=stand_in,
            allocation=allocation,
            plan_source=plan,
            bbm=bbm_rows,
            per_game_dollars=_per_game(bbm_rows, bbm_per_game),
            pool_note=pool_note,
        )


def _per_game(rows: dict[int, BBMRow], path: Path | None) -> dict[int, float]:
    """League dollars from a per-game export, keyed like the total export's rows."""
    if path is None:
        return {}
    by_name = {
        name_key(r.name): r.league_dollars if r.league_dollars is not None else r.dollars
        for r in read_bbm(path)
    }
    out: dict[int, float] = {}
    for player_id, row in rows.items():
        value = by_name.get(name_key(row.name))
        if value is not None:
            out[player_id] = value
    return out


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


def market_price(room: Room, state: DraftState, player_id: int) -> tuple[int | None, str]:
    """What he will probably go for, and where that guess came from."""
    row = room.bbm.get(player_id)
    floor = state.minimum_bid
    if row is not None and row.espn_dollars is not None:
        factor = inflation(state, room.candidates)
        price = floor + round(max(0.0, row.espn_dollars - floor) * factor)
        return max(floor, price), "ESPN drafts, repriced for the room"
    board = next(
        (c.price for c in reprice(state, room.candidates) if c.player_id == player_id), None
    )
    return board, "our board; no market price on file"


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
    if ceiling.capped:
        say(
            f"  the plan caps one player at ${ceiling.plan_cap} now; he rates higher against "
            "the board, and the budget says no more"
        )
    row = room.bbm.get(ceiling.player_id)
    if row is not None:
        facts = []
        if row.age is not None:
            facts.append(f"age {row.age:.1f}")
        facts.append(f"{row.games:.0f} games")
        if row.injury_risk:
            facts.append(f"injury risk {row.injury_risk}")
        if row.injury:
            facts.append(row.injury)
        total = row.league_dollars if row.league_dollars is not None else row.dollars
        per_game = room.per_game_dollars.get(ceiling.player_id)
        if total is not None and per_game is not None:
            facts.append(f"BBM league ${total:.0f} total / ${per_game:.0f} per game")
        elif total is not None:
            facts.append(f"BBM league ${total:.0f}")
        if row.espn_dollars is not None:
            facts.append(f"ESPN avg ${row.espn_dollars:.0f}")
        if row.yahoo_dollars is not None:
            facts.append(f"Yahoo avg ${row.yahoo_dollars:.0f}")
        say("  " + " · ".join(facts))
        if total is not None and per_game is not None and per_game - total >= 8:
            say(
                f"  injury discount: ${per_game - total:.0f} of per-game value lost to missed "
                "games; worth more to a roster that can stash him on IR"
            )
    # Two different numbers, and the clock needs both: what he is worth to
    # us, above; and what he will probably go for, here. The going price is
    # the market's, not ours: what ESPN drafts pay for him on average, since
    # this league drafts on ESPN and sees ESPN's values on the block, repriced
    # for the money left in the room. Our board's price is a valuation, and
    # for exactly the players worth swinging on it is the wrong guess -- it
    # had Kawhi at $47 in 2027 where ESPN drafts pay $13, and this league
    # paid $12 for him in 2026. The board stands in only when no market
    # number is on file.
    going, source = market_price(room, state, ceiling.player_id)
    if going is not None:
        verdict = (
            "more than he is worth to us; let him go"
            if ceiling.price is None or going > ceiling.price
            else "inside our ceiling"
        )
        say(f"  expected to go for about ${going} ({source}) -- {verdict}")
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
        allocation=room.allocation,
    )
    say(f"best finish from here: {plan.expected_wins:.2f} expected categories a week, ${plan.cost}")
    if room.allocation is not None:
        places = room.allocation.open_places(state)
        say(
            f"spending plan ({room.plan_source}): open places {list(places)}, "
            f"one player capped at ${room.allocation.cap(state)}"
        )
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
            allocation=room.allocation,
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
    ap.add_argument(
        "--no-tier-curve",
        action="store_true",
        help="price from value alone, without reshaping to how this league spends",
    )
    ap.add_argument(
        "--bbm", type=Path, help="draft on a Basketball Monster projection export (.xls)"
    )
    ap.add_argument(
        "--bbm-per-game",
        type=Path,
        help="a second BBM export made on Per Game Value, shown beside the total value",
    )
    ap.add_argument(
        "--plan",
        default="history",
        choices=("history", "optimizer", "none"),
        help="the spending plan bids are capped to (default: this league's winning shape)",
    )
    ap.add_argument(
        "--plan-slack",
        type=float,
        default=0.10,
        help="how far past the largest open place a bid may go (default 0.10)",
    )
    args = ap.parse_args()

    room = load_room(
        args.season,
        args.me,
        pool_season=args.pool_season,
        pool_kind=args.pool_kind,
        punt=args.punt,
        restarts=args.restarts,
        tier_curve=not args.no_tier_curve,
        bbm=args.bbm,
        bbm_per_game=args.bbm_per_game,
        plan=args.plan,
        plan_slack=args.plan_slack,
    )
    state = room.state
    say(
        f"{args.season} draft room · {len(state.teams)} teams · ${state.budget} · "
        f"{state.roster_slots} places · {len(room.candidates)} priced players · we are "
        f"{room.team_names[state.me]}"
    )
    say(
        "board reshaped to this league's spending (tier curve on)"
        if not args.no_tier_curve
        else "board priced from value alone (tier curve off)"
    )
    say(room.pool_note)
    if room.allocation is not None:
        say(
            f"spending plan ({room.plan_source}, slack {args.plan_slack:.0%}): "
            f"{list(room.allocation.places)}"
        )
    else:
        say("no spending plan: bidding on the ceiling alone")
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
