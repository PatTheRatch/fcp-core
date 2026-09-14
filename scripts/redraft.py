#!/usr/bin/env python3
"""Redraft a season that already happened, bidding on the room's numbers.

Usage:
    python scripts/redraft.py --season 2026 --me "Through The Wire"

THE QUESTION

The draft room says a player is "worth" a price: the most we should pay
before the rest of the board would give us as good a roster. That is a
judgment made of three parts -- the objective, the projections, and the
board's prices for the alternatives -- and any of them could be wrong. So
this replays a real draft, in its real order, with our team bidding on
the ceiling and everyone else paying what they actually paid, and then
scores the roster we would have ended with on WHAT ACTUALLY HAPPENED that
season: production, and category wins against our real weekly opponents.
If the ceiling is right, that roster beats the one we drafted. If it is
not, this is where we find out.

NO HINDSIGHT, AS FAR AS THE DATA ALLOWS

  projections   the season's own preseason set, which existed before the draft
  tier curve    fitted without this season (see app/draft/tiers.py)
  opponents     distributions from seasons strictly BEFORE this one
                (`before=season`), era adjustment off, since that reads all
                seasons
  availability  measured on seasons before this one
  prices        the board's, repriced live for the money left in the room

THE RULES OF THE REPLAY

Picks are walked in the order they happened. At each one, the room
computes our ceiling for the player nominated. If our ceiling is at least
one dollar above what the real winner paid, and we can legally bid that,
we win him at the real price plus one; the real winner does not get him.
Otherwise the real winner gets him at the real price. A player who is not
on our board -- no projection, no price -- is not something the room can
value, so the real winner gets him and the count of those is reported.

When the real winner was us and the ceiling says pass, the player has to
go somewhere; the second-highest bid is unknown. He goes to the other
team with the most money left that can afford the real price. This only
affects that team's remaining money, which reaches us through the field
ceiling and inflation, and never affects the scoring below, which uses
what opponents actually posted.

If we end the draft with places unfilled, the optimizer fills them from
what is left, as a manager would with a dollar a slot.

SCORING

Both rosters -- the one we drafted and the one the room would have -- are
scored the same way: every regular-season matchup period, sum each
player's real game lines across the whole period (managers start 98.4% of
their production, so this is the roster's total to within a rounding),
rebuild the percentages from makes and attempts, and compare against what
our REAL opponent that week actually posted. Nine categories, won, lost or
tied, summed across the season. Drafted rosters only, no waivers and no
trades on either side, so the draft is what is being measured.

The opponent's real line still includes any player we "took" from him in
the replay. That biases the comparison against our replayed roster, which
is the right side to be wrong on.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    DraftPick,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Player,
    PlayerGameStat,
    Team,
)
from app.db.session import make_engine, make_session_factory
from app.draft import availability, pool
from app.draft.market import price_board
from app.draft.optimizer import Candidate, candidates_from
from app.draft.room import DraftState, Pick, bid_ceiling, reprice, resolve
from app.draft.targets import CategoryDistribution, category_distributions
from app.draft.tiers import LEAGUE_TIER_CURVE, apply_tier_curve
from app.draft.valuation import PERCENTAGE_COMPONENTS, value_players

CATEGORIES = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")
COLUMN = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "3PM": "three_pointers_made",
    "TO": "turnovers",
    "FGM": "field_goals_made",
    "FGA": "field_goals_attempted",
    "FTM": "free_throws_made",
    "FTA": "free_throws_attempted",
}


@dataclass(frozen=True)
class HistoricalPick:
    overall: int
    player_id: int
    name: str
    winner: int
    price: int


@dataclass
class Replay:
    state: DraftState
    bought: list[tuple[HistoricalPick, int, int | None]]  # pick, paid, ceiling
    passed: list[tuple[HistoricalPick, int | None]]  # pick, ceiling
    off_board: int = 0
    reassigned: int = 0


def say(text: str = "") -> None:
    print(text, flush=True)


# ---------------------------------------------------------------------------
# loading, with the hindsight controls
# ---------------------------------------------------------------------------


def load(session: Session, season: int, me: str, *, pool_kind: str = "projected"):  # type: ignore[no-untyped-def]
    ls = session.scalars(select(LeagueSeason).where(LeagueSeason.season == season)).one()
    teams = {
        int(t.espn_team_id): t
        for t in session.scalars(select(Team).where(Team.league_season_id == ls.id))
    }
    names = {t.espn_team_id: t.name for t in teams.values()}
    my_id = next(tid for tid, t in teams.items() if t.name == me)

    categories = pool.season_categories(session, ls)
    slots = pool.roster_size_for(ls)
    projections = pool.load_projections(session, season, kind=pool_kind)
    distributions = category_distributions(session, ls, before=season, adjust_for_era=False)

    # Availability measured only on seasons before this one.
    original = availability.DISTORTED_SEASONS
    availability.DISTORTED_SEASONS = tuple(sorted({*original, *(y for y in range(season, 2100))}))
    try:
        factor = availability.measured_availability(session).factor
    finally:
        availability.DISTORTED_SEASONS = original

    board = apply_tier_curve(
        price_board(
            value_players(projections, categories),
            teams=ls.team_count,
            budget_per_team=ls.auction_budget,
            roster_slots=slots,
        ),
        LEAGUE_TIER_CURVE,
    )
    candidates = candidates_from(
        projections, board, periods=ls.regular_season_periods, availability=factor, keys=categories
    )

    picks = [
        HistoricalPick(
            overall=(dp.round_num - 1) * ls.team_count + dp.round_pick,
            player_id=int(p.espn_player_id),
            name=p.name,
            winner=int(teams_by_db[dp.team_id].espn_team_id),
            price=int(dp.bid_amount or 0),
        )
        for dp, p in session.execute(
            select(DraftPick, Player)
            .join(Player, Player.id == DraftPick.player_id)
            .where(DraftPick.league_season_id == ls.id)
            .order_by(DraftPick.round_num, DraftPick.round_pick)
        ).all()
        if (teams_by_db := {t.id: t for t in teams.values()})
    ]
    state = DraftState.open(
        budget=ls.auction_budget,
        roster_slots=slots,
        teams=names,
        me=my_id,
        nomination_order=ls.draft_order or (),
    )
    return (
        ls,
        state,
        candidates,
        list(distributions),
        pool.lineup_for(ls),
        pool.position_limits_for(ls),
        picks,
        factor,
    )


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------


def replay(
    state: DraftState,
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    picks: Sequence[HistoricalPick],
    *,
    lineup: tuple[str, ...],
    limits: dict[str, int],
    restarts: int,
    market_cap: float | None = None,
) -> Replay:
    on_board = {c.player_id for c in candidates}
    out = Replay(state=state, bought=[], passed=[])
    for pick in picks:
        state = out.state
        if state.mine.open_slots == 0 and pick.winner != state.me:
            out.state = _give(state, pick, pick.winner)
            continue
        if pick.player_id not in on_board:
            out.off_board += 1
            out.state = _give_or_reassign(out, pick)
            continue
        ceiling = bid_ceiling(
            state,
            pick.player_id,
            candidates,
            distributions,
            lineup=lineup,
            limits=limits,
            restarts=restarts,
        )
        want = ceiling.price
        if want is not None and market_cap is not None:
            # The market's own price for him, repriced for the room, as a
            # prior: never bid more than that times the cap, whatever the
            # objective says. Tests whether the room's losses come from
            # ignoring what the market knows.
            going = next(
                (c.price for c in reprice(state, candidates) if c.player_id == pick.player_id),
                None,
            )
            if going is not None:
                want = min(want, round(going * market_cap))
        bid = pick.price + 1
        if want is not None and want >= bid and bid <= state.mine.max_bid(state.minimum_bid):
            out.state = state.apply(Pick(pick.player_id, state.me, bid))
            out.bought.append((pick, bid, want))
        else:
            out.passed.append((pick, want))
            out.state = _give_or_reassign(out, pick)
    return out


def _give(state: DraftState, pick: HistoricalPick, team_id: int) -> DraftState:
    team = state.teams[team_id]
    price = min(pick.price, team.max_bid(state.minimum_bid))
    if price < state.minimum_bid or team.open_slots == 0:
        return state
    return state.apply(Pick(pick.player_id, team_id, price))


def _give_or_reassign(out: Replay, pick: HistoricalPick) -> DraftState:
    state = out.state
    if pick.winner != state.me:
        return _give(state, pick, pick.winner)
    # We were the real winner and are passing: the runner-up is unknown, so
    # the richest other team that can afford him takes him.
    others = sorted(
        (t for t in state.teams.values() if t.team_id != state.me and t.open_slots > 0),
        key=lambda t: -t.max_bid(state.minimum_bid),
    )
    for team in others:
        if team.max_bid(state.minimum_bid) >= pick.price:
            out.reassigned += 1
            return state.apply(Pick(pick.player_id, team.team_id, pick.price))
    return state


# ---------------------------------------------------------------------------
# scoring on what actually happened
# ---------------------------------------------------------------------------


def _period_windows(session: Session, ls: LeagueSeason) -> list[tuple[int, int, int]]:
    rows = session.execute(
        select(
            MatchupPeriod.period,
            MatchupPeriod.first_scoring_period,
            MatchupPeriod.final_scoring_period,
        )
        .where(MatchupPeriod.league_season_id == ls.id, MatchupPeriod.is_playoff.is_(False))
        .order_by(MatchupPeriod.period)
    ).all()
    return [(int(p), int(a), int(b)) for p, a, b in rows if a is not None and b is not None]


def _opponent_lines(
    session: Session, ls: LeagueSeason, my_db_id: int
) -> dict[int, dict[str, float]]:
    """What our real opponent posted each regular-season period, by category."""
    out: dict[int, dict[str, float]] = {}
    rows = session.execute(
        select(MatchupPeriod.period, Matchup.home_team_id, Matchup.away_team_id)
        .join(Matchup, Matchup.matchup_period_id == MatchupPeriod.id)
        .where(MatchupPeriod.league_season_id == ls.id, MatchupPeriod.is_playoff.is_(False))
    ).all()
    for period, home, away in rows:
        if my_db_id not in (home, away) or away is None:
            continue
        opponent = away if home == my_db_id else home
        stats = session.execute(
            select(MatchupTeamStat.abbreviation, MatchupTeamStat.value)
            .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
            .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
            .where(
                MatchupPeriod.period == period,
                MatchupPeriod.league_season_id == ls.id,
                MatchupTeamStat.team_id == opponent,
            )
        ).all()
        out[int(period)] = {str(a): float(v) for a, v in stats}
    return out


def roster_lines(
    session: Session, season: int, espn_ids: Sequence[int], windows: Sequence[tuple[int, int, int]]
) -> dict[int, dict[str, float]]:
    """Per period, the roster's summed real game lines, percentages rebuilt."""
    db_ids: dict[int, int] = {
        int(espn): int(internal)
        for espn, internal in session.execute(
            select(Player.espn_player_id, Player.id).where(
                Player.espn_player_id.in_(list(espn_ids))
            )
        ).all()
    }
    raw: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for period, first, last in windows:
        rows = (
            session.execute(
                select(PlayerGameStat).where(
                    PlayerGameStat.player_id.in_(list(db_ids.values())),
                    PlayerGameStat.season == season,
                    PlayerGameStat.played.is_(True),
                    PlayerGameStat.scoring_period.between(first, last),
                )
            )
            .scalars()
            .all()
        )
        for g in rows:
            for key, column in COLUMN.items():
                raw[period][key] += float(getattr(g, column) or 0.0)
    out: dict[int, dict[str, float]] = {}
    for period, totals in raw.items():
        line = {c: totals[c] for c in CATEGORIES if c not in PERCENTAGE_COMPONENTS}
        for cat, (made, att) in PERCENTAGE_COMPONENTS.items():
            line[cat] = totals[made] / totals[att] if totals[att] else 0.0
        out[period] = line
    return out


def record(
    mine: dict[int, dict[str, float]], theirs: dict[int, dict[str, float]]
) -> tuple[int, int, int, dict[str, int]]:
    won = lost = tied = 0
    by_cat: dict[str, int] = defaultdict(int)
    for period, opp in theirs.items():
        line = mine.get(period, {})
        for cat in CATEGORIES:
            a, b = line.get(cat, 0.0), opp.get(cat, 0.0)
            if cat == "TO":
                a, b = -a, -b
            if abs(a - b) < 1e-9:
                tied += 1
            elif a > b:
                won += 1
                by_cat[cat] += 1
            else:
                lost += 1
    return won, lost, tied, dict(by_cat)


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True)
    ap.add_argument(
        "--restarts", type=int, default=2, help="per solve; 2 keeps 182 picks under ten minutes"
    )
    ap.add_argument(
        "--pool-kind",
        default="projected",
        choices=("projected", "total"),
        help="'total' drafts on the season's ACTUAL totals: pure hindsight, "
        "to test the objective alone",
    )
    ap.add_argument(
        "--cap-at-market",
        type=float,
        help="never bid more than the market's price times this (e.g. 1.15): the market as a prior",
    )
    args = ap.parse_args()

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        ls, state, candidates, dists, lineup, limits, picks, factor = load(
            session, args.season, args.me, pool_kind=args.pool_kind
        )
        say(
            f"{args.season}: {len(picks)} picks, {len(candidates)} priced players, "
            f"availability {factor:.3f} "
            f"(before {args.season}), opponents from {dists[0].basis_seasons if dists else ()}"
        )
        t0 = time.time()
        if args.pool_kind == "total":
            say("!! HINDSIGHT: the room is drafting on this season's actual totals")
        if args.cap_at_market:
            say(f"market prior: bids capped at {args.cap_at_market:.2f}x the market price")
        out = replay(
            state,
            candidates,
            dists,
            picks,
            lineup=lineup,
            limits=limits,
            restarts=args.restarts,
            market_cap=args.cap_at_market,
        )
        say(
            f"replayed in {time.time() - t0:.0f}s: bought {len(out.bought)}, "
            f"passed {len(out.passed)} "
            f"({sum(1 for p, _ in out.passed if p.winner == state.me)} of them our real picks), "
            f"{out.off_board} nominations off our board, "
            f"{out.reassigned} of our real picks reassigned"
        )

        final = out.state
        if final.mine.open_slots:
            plan = resolve(
                final, candidates, dists, lineup=lineup, limits=limits, restarts=args.restarts
            )
            fills = [c for c in plan.players if c.player_id not in final.mine.player_ids][
                : final.mine.open_slots
            ]
            for c in fills:
                price = min(max(1, c.price), final.mine.max_bid(final.minimum_bid))
                final = final.apply(Pick(c.player_id, final.me, price))
            say(f"filled {len(fills)} open places from what was left")

        say("\nWHAT THE ROOM WOULD HAVE BOUGHT")
        for pick, paid, ceiling in out.bought:
            tag = (
                "ours anyway"
                if pick.winner == state.me
                else f"taken from {state.teams[pick.winner].name}"
            )
            say(
                f"  #{pick.overall:>3} {pick.name:24s} ${paid:>3}  "
                f"(ceiling ${ceiling}, real ${pick.price}) {tag}"
            )
        say("\nOUR REAL PICKS THE ROOM PASSED ON")
        for pick, ceiling in out.passed:
            if pick.winner == state.me:
                note = "no bid" if ceiling is None else f"${ceiling}"
                say(f"  #{pick.overall:>3} {pick.name:24s} real ${pick.price:>3}  ceiling {note}")

        my_db = next(
            t.id
            for t in session.scalars(select(Team).where(Team.league_season_id == ls.id))
            if t.name == args.me
        )
        windows = _period_windows(session, ls)
        opp = _opponent_lines(session, ls, my_db)
        actual_ids = [p.player_id for p in picks if p.winner == state.me]
        sim_ids = sorted(final.mine.player_ids)
        actual_lines = roster_lines(session, args.season, actual_ids, windows)
        sim_lines = roster_lines(session, args.season, sim_ids, windows)
        aw, al, at, acat = record(actual_lines, opp)
        sw, sl, st, scat = record(sim_lines, opp)

        say(
            f"\nSCORED ON WHAT ACTUALLY HAPPENED, {len(opp)} regular-season weeks "
            "vs our real opponents"
        )
        say(
            f"  {'':22s} {'cats W-L-T':>12} {'win rate':>9}   "
            + "  ".join(f"{c:>4}" for c in CATEGORIES)
        )
        say(
            f"  {'roster we drafted':22s} {f'{aw}-{al}-{at}':>12} {aw / (aw + al + at):9.3f}   "
            + "  ".join(f"{acat.get(c, 0):>4}" for c in CATEGORIES)
        )
        say(
            f"  {'roster the room picks':22s} {f'{sw}-{sl}-{st}':>12} {sw / (sw + sl + st):9.3f}   "
            + "  ".join(f"{scat.get(c, 0):>4}" for c in CATEGORIES)
        )
        named = {c.player_id: c.name for c in candidates}
        drafted_names = ", ".join(named.get(i, str(i)) for i in actual_ids)
        room_names = ", ".join(named.get(i, str(i)) for i in sim_ids)
        say(f"\n  drafted roster:   {drafted_names}")
        say(f"  room's roster:    {room_names}   (${final.mine.spent})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
