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
import json
import multiprocessing
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

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
from app.draft.bbm import load_bbm
from app.draft.live import MARKET_WEIGHT, size_to_room
from app.draft.market import price_board
from app.draft.optimizer import Candidate, candidates_from
from app.draft.room import (
    Allocation,
    DraftState,
    Pick,
    bid_ceiling,
    plan_allocation,
    reprice,
    resolve,
)
from app.draft.shape import winning_shape
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


def load(  # type: ignore[no-untyped-def]
    session: Session,
    season: int,
    me: str,
    *,
    pool_kind: str = "projected",
    bbm: Path | None = None,
    bbm_availability: float = 1.0,
):
    ls = session.scalars(select(LeagueSeason).where(LeagueSeason.season == season)).one()
    teams = {
        int(t.espn_team_id): t
        for t in session.scalars(select(Team).where(Team.league_season_id == ls.id))
    }
    names = {t.espn_team_id: t.name for t in teams.values()}
    my_id = next(tid for tid, t in teams.items() if t.name == me)

    categories = pool.season_categories(session, ls)
    slots = pool.roster_size_for(ls)
    if bbm is not None:
        loaded = load_bbm(session, bbm, season)
        # A replay can only buy players who were actually nominated, and
        # those all have our ids; a synthetic one could only ever be a
        # phantom the end-of-draft fill reaches for.
        projections = [p for p in loaded.projections if p.player_id > 0]
        say(
            f"BBM {bbm.name}: {loaded.matched} matched ({len(loaded.loose)} loosely), "
            f"{len(loaded.ambiguous)} ambiguous, {len(loaded.unmatched)} unmatched left out; "
            f"eligibility from ESPN for {loaded.espn_eligibility}, from BBM position for "
            f"{loaded.derived_eligibility}"
        )
    else:
        projections = pool.load_projections(session, season, kind=pool_kind)
    distributions = category_distributions(session, ls, before=season, adjust_for_era=False)

    # Availability measured only on seasons before this one.
    original = availability.DISTORTED_SEASONS
    availability.DISTORTED_SEASONS = tuple(sorted({*original, *(y for y in range(season, 2100))}))
    try:
        factor = availability.measured_availability(session).factor
    finally:
        availability.DISTORTED_SEASONS = original
    if bbm is not None:
        # BBM's games already price availability (0.96 realized over
        # projected in 2026, against ESPN's 0.88); do not discount twice.
        factor = bbm_availability

    board_prices = apply_tier_curve(
        price_board(
            value_players(projections, categories),
            teams=ls.team_count,
            budget_per_team=ls.auction_budget,
            roster_slots=slots,
        ),
        LEAGUE_TIER_CURVE,
    )
    candidates = candidates_from(
        projections,
        board_prices,
        periods=pool.effective_weeks(session, before=season),
        availability=factor,
        keys=categories,
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
    # Price the pool the way the live room does: ranked by ESPN's average
    # auction price for the season blended with the board, then sized to the
    # room's money with the league's share of $1 and $2 buys. ESPN's averages
    # come from the price scorecard's cache; without them, the board alone.
    cache = Path("logs/price-cache") / f"{season}.json"
    averages: dict[int, float] = {}
    if cache.exists():
        averages = {
            int(k): float(v["aav"])
            for k, v in json.loads(cache.read_text()).items()
            if v.get("aav")
        }
    ranked = sorted(
        (
            (
                MARKET_WEIGHT * max(1.0, averages[c.player_id]) + (1 - MARKET_WEIGHT) * c.price
                if c.player_id in averages
                else float(c.price),
                c.player_id,
                "",
            )
            for c in candidates
        ),
        reverse=True,
    )
    going = size_to_room(ranked, state)
    candidates = [
        replace(c, price=going.get(c.player_id, (c.price, ""))[0] or c.price) for c in candidates
    ]
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
    allocation: Allocation | None = None,
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
            allocation=allocation,
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


@dataclass(frozen=True)
class Options:
    pool_kind: str = "projected"
    bbm: Path | None = None
    bbm_availability: float = 1.0
    market_cap: float | None = None
    plan: str = "none"
    plan_slack: float = 0.10
    restarts: int = 2


@dataclass(frozen=True)
class Result:
    season: int
    team: str
    actual: tuple[int, int, int]
    room: tuple[int, int, int]
    spent: int
    #: The full readout, for a single-team run.
    lines: tuple[str, ...]

    @property
    def gain(self) -> int:
        """Categories the room's roster won beyond the drafted roster."""
        return self.room[0] - self.actual[0]


def simulate(season: int, me: str, options: Options) -> Result:
    """Replay one team-season and score both rosters. Opens its own session."""
    lines: list[str] = []

    def note(text: str = "") -> None:
        lines.append(text)

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        ls, state, candidates, dists, lineup, limits, picks, factor = load(
            session,
            season,
            me,
            pool_kind=options.pool_kind,
            bbm=options.bbm,
            bbm_availability=options.bbm_availability,
        )
        note(
            f"{season}: {len(picks)} picks, {len(candidates)} priced players, "
            f"availability {factor:.3f} (before {season}), "
            f"opponents from {dists[0].basis_seasons if dists else ()}"
        )
        if options.pool_kind == "total":
            note("!! HINDSIGHT: the room is drafting on this season's actual totals")
        if options.market_cap:
            note(f"market prior: bids capped at {options.market_cap:.2f}x the market price")

        allocation = None
        if options.plan == "optimizer":
            allocation, plan = plan_allocation(
                state, candidates, dists, slack=options.plan_slack, lineup=lineup, limits=limits
            )
            note(f"plan (optimizer, {plan.expected_wins:.2f} expected wins):")
            for c in sorted(plan.players, key=lambda c: -c.price):
                note(f"    ${c.price:>3} {c.name}")
        elif options.plan == "history":
            shape = winning_shape(
                session, roster_slots=state.roster_slots, budget=state.budget, before=season
            )
            allocation = Allocation.from_prices(shape, state, slack=options.plan_slack)
        if allocation is not None:
            note(
                f"allocation ({options.plan}, slack {options.plan_slack:.0%}): "
                f"{list(allocation.places)}"
            )

        t0 = time.time()
        out = replay(
            state,
            candidates,
            dists,
            picks,
            lineup=lineup,
            limits=limits,
            restarts=options.restarts,
            market_cap=options.market_cap,
            allocation=allocation,
        )
        note(
            f"replayed in {time.time() - t0:.0f}s: bought {len(out.bought)}, "
            f"passed {len(out.passed)} "
            f"({sum(1 for p, _ in out.passed if p.winner == state.me)} of them our real picks), "
            f"{out.off_board} nominations off our board, "
            f"{out.reassigned} of our real picks reassigned"
        )

        final = out.state
        if final.mine.open_slots:
            plan = resolve(
                final, candidates, dists, lineup=lineup, limits=limits, restarts=options.restarts
            )
            fills = [c for c in plan.players if c.player_id not in final.mine.player_ids][
                : final.mine.open_slots
            ]
            for c in fills:
                price = min(max(1, c.price), final.mine.max_bid(final.minimum_bid))
                final = final.apply(Pick(c.player_id, final.me, price))
            note(f"filled {len(fills)} open places from what was left")

        note("\nWHAT THE ROOM WOULD HAVE BOUGHT")
        for pick, paid, ceiling in out.bought:
            tag = (
                "ours anyway"
                if pick.winner == state.me
                else f"taken from {state.teams[pick.winner].name}"
            )
            note(
                f"  #{pick.overall:>3} {pick.name:24s} ${paid:>3}  "
                f"(ceiling ${ceiling}, real ${pick.price}) {tag}"
            )
        note("\nOUR REAL PICKS THE ROOM PASSED ON")
        for pick, ceiling in out.passed:
            if pick.winner == state.me:
                bid = "no bid" if ceiling is None else f"${ceiling}"
                note(f"  #{pick.overall:>3} {pick.name:24s} real ${pick.price:>3}  ceiling {bid}")

        my_db = next(
            t.id
            for t in session.scalars(select(Team).where(Team.league_season_id == ls.id))
            if t.name == me
        )
        windows = _period_windows(session, ls)
        opp = _opponent_lines(session, ls, my_db)
        actual_ids = [p.player_id for p in picks if p.winner == state.me]
        sim_ids = sorted(final.mine.player_ids)
        aw, al, at, acat = record(roster_lines(session, season, actual_ids, windows), opp)
        sw, sl, st, scat = record(roster_lines(session, season, sim_ids, windows), opp)

        note(
            f"\nSCORED ON WHAT ACTUALLY HAPPENED, {len(opp)} regular-season weeks "
            "vs our real opponents"
        )
        note(
            f"  {'':22s} {'cats W-L-T':>12} {'win rate':>9}   "
            + "  ".join(f"{c:>4}" for c in CATEGORIES)
        )
        for label, (w, lo, t, cats) in (
            ("roster we drafted", (aw, al, at, acat)),
            ("roster the room picks", (sw, sl, st, scat)),
        ):
            played = max(1, w + lo + t)
            note(
                f"  {label:22s} {f'{w}-{lo}-{t}':>12} {w / played:9.3f}   "
                + "  ".join(f"{cats.get(c, 0):>4}" for c in CATEGORIES)
            )
        named = {c.player_id: c.name for c in candidates}
        note(f"\n  drafted roster:   {', '.join(named.get(i, str(i)) for i in actual_ids)}")
        note(
            f"  room's roster:    {', '.join(named.get(i, str(i)) for i in sim_ids)}"
            f"   (${final.mine.spent})"
        )
        return Result(season, me, (aw, al, at), (sw, sl, st), final.mine.spent, tuple(lines))


def _simulate_job(job: tuple[int, str, Options]) -> Result | str:
    season, team, options = job
    try:
        return simulate(season, team, options)
    except Exception as exc:  # one broken team-season should not sink the batch
        return f"{season} {team}: {type(exc).__name__}: {exc}"


def _team_names(season: int) -> list[str]:
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        return [
            str(t.name)
            for t in session.scalars(
                select(Team)
                .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
                .where(LeagueSeason.season == season)
                .order_by(Team.espn_team_id)
            )
        ]


def summarise(results: Sequence[Result]) -> list[str]:
    """Room against the drafted roster, per season and overall."""
    out = [
        f"  {'season':>6} {'teams':>5} {'room beat':>9} {'mean gain':>9} "
        f"{'room win%':>9} {'drafted win%':>12}"
    ]
    groups: dict[int | None, list[Result]] = defaultdict(list)
    for r in results:
        groups[r.season].append(r)
        groups[None].append(r)
    for season in [*sorted(k for k in groups if k is not None), None]:
        rows = groups[season]
        room = sum(r.room[0] for r in rows) / max(1, sum(sum(r.room) for r in rows))
        drafted = sum(r.actual[0] for r in rows) / max(1, sum(sum(r.actual) for r in rows))
        gains = [r.gain for r in rows]
        mean = sum(gains) / len(gains)
        spread = (sum((g - mean) ** 2 for g in gains) / max(1, len(gains) - 1)) ** 0.5
        label = "all" if season is None else str(season)
        out.append(
            f"  {label:>6} {len(rows):>5} {sum(g > 0 for g in gains):>5} of {len(rows):<2} "
            f"{mean:+9.1f} {room:9.3f} {drafted:12.3f}"
            + (
                f"   (gain sd {spread:.1f}, se {spread / len(gains) ** 0.5:.1f})"
                if season is None
                else ""
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, action="append", required=True, help="repeatable")
    ap.add_argument("--me", help="the team to replay as")
    ap.add_argument(
        "--all-teams",
        action="store_true",
        help="replay as every team in each season and summarise, in parallel",
    )
    ap.add_argument("--workers", type=int, default=6)
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
        "--bbm",
        type=Path,
        help="draft on a Basketball Monster projection export instead of ESPN's projections",
    )
    ap.add_argument(
        "--bbm-availability",
        type=float,
        default=1.0,
        help="availability factor for BBM games, which already price it (default 1.0)",
    )
    ap.add_argument(
        "--cap-at-market",
        type=float,
        help="never bid more than the market's price times this (e.g. 1.15): the market as a prior",
    )
    ap.add_argument(
        "--plan",
        default="none",
        choices=("none", "optimizer", "history"),
        help="bid to an allocation: the optimizer's empty-room roster, or the league's "
        "winning spending shape from seasons before this one",
    )
    ap.add_argument(
        "--plan-slack",
        type=float,
        default=0.10,
        help="how far past the largest open place a bid may go (default 0.10)",
    )
    args = ap.parse_args()
    options = Options(
        pool_kind=args.pool_kind,
        bbm=args.bbm,
        bbm_availability=args.bbm_availability,
        market_cap=args.cap_at_market,
        plan=args.plan,
        plan_slack=args.plan_slack,
        restarts=args.restarts,
    )

    if not args.all_teams:
        if not args.me or len(args.season) != 1:
            ap.error("a single replay takes one --season and --me; or pass --all-teams")
        for line in simulate(args.season[0], args.me, options).lines:
            say(line)
        return 0

    jobs = [(season, team, options) for season in args.season for team in _team_names(season)]
    say(f"{len(jobs)} team-seasons, plan={options.plan}, {args.workers} workers")
    results: list[Result] = []
    t0 = time.time()
    with multiprocessing.get_context("spawn").Pool(args.workers) as workers:
        for done in workers.imap_unordered(_simulate_job, jobs):
            if isinstance(done, str):
                say(f"  FAILED {done}")
                continue
            results.append(done)
            say(
                f"  {done.season} {done.team:32s} drafted {'-'.join(map(str, done.actual)):>9}  "
                f"room {'-'.join(map(str, done.room)):>9}  {done.gain:+4d}  "
                f"({len(results)}/{len(jobs)}, {time.time() - t0:.0f}s)"
            )
    say("\nTHE ROOM AGAINST EACH TEAM'S OWN DRAFT, scored on what actually happened")
    for line in summarise(results):
        say(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
