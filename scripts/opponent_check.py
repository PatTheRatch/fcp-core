#!/usr/bin/env python3
"""An independent check on the opponent distribution: what does a 2027 room
post if you draft it from the projections instead of reading it from history?

Usage:
    python scripts/opponent_check.py --season 2027 \\
        --bbm data/bbm/BBM_Projections_2027_total.xls \\
        --backtest 2026=data/bbm/BBM_Projections_2026.xls \\
        [--draws 2000] [--seed 0] [--out docs/opponent_check.md]

`app/draft/targets.category_distributions` reads what opponents post from the
league's own results: the played seasons of the same size, brought forward
where the game drifts, or the nearest size scaled by the fitted league-size
effect when the size has never been played. Every ceiling in the draft room
is measured against it. This is the other way of getting the same number, so
the two can be compared.

THE SIMULATION

A draft is simulated from the projections the room drafts on. The board is
ordered by the room's own going-price blend (ESPN's average auction price and
our board), ties broken by our valuation, and each player's place on it is
jittered by a few places so the last men rostered are not the same every
draw. The top `teams x roster` are rostered. They are dealt to teams most
expensive first, each to a team chosen at random among those that can still
afford him and leave a floor bid for every open place, so every team spends
about its budget and no team is built to a plan. A team's week is the sum of
its roster's projected weekly lines, times the started share of production
the league's managers actually achieve.

That gives the spread BETWEEN teams: how much rosters differ. What an opponent
posts in a given week also varies WITHIN a team -- three-game weeks, injuries,
rest -- and the projections say nothing about that, so the within-team
variance is taken from history: each team's week-to-week variance around its
own season mean, pooled across recent seasons, as a share of the mean for
counting categories and in absolute terms for rates. The two are added in
quadrature.

GAMES

A projection prices absence: a player projected for 60 games contributes
sixty games' worth of a week. A real opponent does not post that, because a
manager streams round absences, so the players actually on his roster in a
given week play more games than a preseason projection gives any of them.
The simulation is therefore also shown on a games-actually-played basis:
scaled by the games the top `teams x roster` scorers really played in recent
seasons over the games the export projects for the top of its board.

THE BACKTEST

The same simulation is run on a season that has been played, from that
season's own projections, and compared with what its opponents actually
posted, raw and on the games basis. That is what tells you whether the
method is any good.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    PlayerGameStat,
    Team,
)
from app.db.session import make_engine, make_session_factory
from app.draft.bbm import load_bbm
from app.draft.live import Room, load_room
from app.draft.optimizer import Candidate, optimize, roster_totals, score
from app.draft.targets import (
    RECENT_RATE_SEASONS,
    CategoryDistribution,
    _moments,
    _seasons_with_results,
    _sized_seasons,
    modal_period_days,
    size_effect,
)
from app.draft.valuation import INVERTED_CATEGORIES, PERCENTAGE_COMPONENTS, value_players

#: Share of a roster's production its manager actually starts. Measured on
#: the league's daily lineups; see app/draft/optimizer.py.
STARTED_SHARE = 0.984

#: How far a player's place on the board is jittered when deciding who gets
#: rostered, in places. Zero rosters exactly the top of the board every draw.
RANK_NOISE = 10.0

CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")


@dataclass(frozen=True)
class Estimate:
    mean: float
    spread: float


def _session_factory() -> sessionmaker[Session]:
    return make_session_factory(make_engine(get_settings().database_url))


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------


def blend_value(room: Room, candidate: Candidate) -> float:
    """The going-price blend before it is sized to the room: half ESPN's
    average auction price and half our board, or the board alone."""
    board = float(room.board.get(candidate.player_id, candidate.price))
    row = room.bbm.get(candidate.player_id)
    if row is not None and row.espn_dollars is not None:
        return 0.5 * max(1.0, row.espn_dollars) + 0.5 * board
    return board


def board_order(room: Room, worth: Mapping[int, float]) -> list[int]:
    """Candidate indices from the top of the board down: by the going-price
    blend, then by our valuation, which is what separates the $1 players."""
    return sorted(
        range(len(room.candidates)),
        key=lambda i: (
            blend_value(room, room.candidates[i]),
            worth.get(room.candidates[i].player_id, float("-inf")),
        ),
        reverse=True,
    )


def deal_rosters(
    candidates: Sequence[Candidate],
    order: Sequence[int],
    *,
    teams: int,
    roster_slots: int,
    budget: int,
    rng: random.Random,
    noise: float = RANK_NOISE,
) -> list[list[Candidate]]:
    """One simulated draft: who gets rostered, and by whom."""
    places = teams * roster_slots
    jittered = sorted(range(len(order)), key=lambda place: place + rng.gauss(0.0, noise))
    rostered = sorted((candidates[order[p]] for p in jittered[:places]), key=lambda c: -c.price)
    rosters: list[list[Candidate]] = [[] for _ in range(teams)]
    money = [budget] * teams
    for player in rostered:
        able = [
            t
            for t in range(teams)
            if len(rosters[t]) < roster_slots
            and money[t] - player.price >= roster_slots - len(rosters[t]) - 1
        ]
        if not able:
            able = [t for t in range(teams) if len(rosters[t]) < roster_slots]
            able = [max(able, key=lambda t: money[t])]
        t = rng.choice(able)
        rosters[t].append(player)
        money[t] -= player.price
    return rosters


def simulate(
    room: Room,
    worth: Mapping[int, float],
    *,
    draws: int,
    seed: int,
    noise: float = RANK_NOISE,
) -> dict[str, Estimate]:
    """Between-team mean and spread of a weekly total, by category."""
    rng = random.Random(seed)
    state = room.state
    order = board_order(room, worth)
    totals: dict[str, list[float]] = {c: [] for c in CATEGORIES}
    for _ in range(draws):
        for roster in deal_rosters(
            room.candidates,
            order,
            teams=len(state.teams),
            roster_slots=state.roster_slots,
            budget=state.budget,
            rng=rng,
            noise=noise,
        ):
            line = roster_totals(roster, list(CATEGORIES))
            for cat in CATEGORIES:
                value = line[cat]
                if cat not in PERCENTAGE_COMPONENTS:
                    value *= STARTED_SHARE
                totals[cat].append(value)
    out: dict[str, Estimate] = {}
    for cat, xs in totals.items():
        mean = sum(xs) / len(xs)
        var = sum((x - mean) ** 2 for x in xs) / len(xs)
        out[cat] = Estimate(mean, math.sqrt(var))
    return out


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

_period_length = cast(
    MatchupPeriod.final_scoring_period - MatchupPeriod.first_scoring_period + 1, Integer
)


def within_team_spread(
    session: Session, seasons: Sequence[int], period_days: int
) -> dict[str, float]:
    """How much a team's weekly total varies around its own season mean.

    Pooled over the seasons given: the root of the mean within-team variance,
    as a share of the mean for counting categories (a bigger week is
    proportionally noisier) and absolute for rates.
    """
    per_team = (
        select(
            MatchupTeamStat.abbreviation.label("cat"),
            MatchupTeamStat.team_id.label("team"),
            func.avg(cast(MatchupTeamStat.value, Float)).label("mean"),
            func.var_pop(cast(MatchupTeamStat.value, Float)).label("var"),
        )
        .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
        .where(
            MatchupTeamStat.league_season_category_id.is_not(None),
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
            LeagueSeason.season.in_(list(seasons)),
            _period_length == period_days,
        )
        .group_by(MatchupTeamStat.abbreviation, MatchupTeamStat.team_id)
        .subquery()
    )
    rows = session.execute(
        select(per_team.c.cat, func.avg(per_team.c.mean), func.avg(per_team.c.var)).group_by(
            per_team.c.cat
        )
    ).all()
    out: dict[str, float] = {}
    for cat, mean, var in rows:
        if mean is None or var is None or cat not in CATEGORIES:
            continue
        within = math.sqrt(float(var))
        out[str(cat)] = within if cat in PERCENTAGE_COMPONENTS else within / float(mean)
    return out


def valuation_worth(session: Session, export: Path, season: int) -> dict[int, float]:
    """Our valuation's total for every player in an export, by the id the
    room loaded him under."""
    loaded = load_bbm(session, export, season)
    return {v.player_id: v.total for v in value_players(loaded.projections, list(CATEGORIES))}


def games_played_top(session: Session, season: int, count: int) -> float:
    """Mean games played by the season's top `count` scorers by total points:
    the players who were actually on rosters, as streaming leaves them."""
    per_player = (
        select(
            PlayerGameStat.player_id,
            func.count().filter(PlayerGameStat.played.is_(True)).label("games"),
            func.sum(PlayerGameStat.points).label("points"),
        )
        .where(PlayerGameStat.season == season)
        .group_by(PlayerGameStat.player_id)
        .order_by(func.sum(PlayerGameStat.points).desc())
        .limit(count)
        .subquery()
    )
    value = session.scalar(select(func.avg(per_player.c.games)))
    return float(value or 0.0)


def export_games_top(room: Room, worth: Mapping[int, float], count: int) -> float:
    """Mean projected games of the top `count` on the export's board."""
    games = [
        room.bbm[room.candidates[i].player_id].games
        for i in board_order(room, worth)[:count]
        if room.candidates[i].player_id in room.bbm
    ]
    return sum(games) / len(games) if games else 0.0


def on_games_basis(estimate: Estimate, factor: float, cat: str) -> Estimate:
    if cat in PERCENTAGE_COMPONENTS:
        return estimate
    return Estimate(estimate.mean * factor, estimate.spread * factor)


def posted(session: Session, season: int, period_days: int) -> dict[str, Estimate]:
    """What opponents actually posted in a played season, by category."""
    out: dict[str, Estimate] = {}
    for cat in CATEGORIES:
        mean, spread, _ = _moments(session, cat, [season], period_days)
        if mean is not None and spread is not None:
            out[cat] = Estimate(mean, spread)
    return out


def size_scaled_alternative(
    session: Session, league_season: LeagueSeason, period_days: int, *, skip_size: int
) -> tuple[int, list[int], dict[str, Estimate]]:
    """The other historical basis: the nearest size other than `skip_size`,
    scaled by the fitted size effect for the teams it does not have."""
    target = int(league_season.team_count)
    rows = [(s, y) for s, y in _seasons_with_results(session) if s != skip_size]
    sizes = {s for s, _ in rows}
    nearest = min(sizes, key=lambda s: (abs(s - target), s))
    seasons = [y for s, y in rows if s == nearest]
    all_seasons = sorted({y for _, y in _seasons_with_results(session)})
    out: dict[str, Estimate] = {}
    for cat in CATEGORIES:
        mean, spread, _ = _moments(session, cat, seasons, period_days)
        if mean is None or spread is None:
            continue
        if cat in PERCENTAGE_COMPONENTS:
            out[cat] = Estimate(mean, spread)
            continue
        factor = math.exp(size_effect(session, cat, all_seasons, period_days) * (target - nearest))
        out[cat] = Estimate(mean * factor, spread * factor)
    return nearest, seasons, out


# ---------------------------------------------------------------------------
# reading the difference
# ---------------------------------------------------------------------------


def combined(between: Estimate, within: float, cat: str) -> Estimate:
    """Between-team spread and within-team spread, added in quadrature."""
    inside = within if cat in PERCENTAGE_COMPONENTS else within * between.mean
    return Estimate(between.mean, math.sqrt(between.spread**2 + inside**2))


def as_distributions(
    estimates: dict[str, Estimate], like: Sequence[CategoryDistribution]
) -> list[CategoryDistribution]:
    return [
        CategoryDistribution(
            abbreviation=d.abbreviation,
            mean=estimates[d.abbreviation].mean,
            spread=estimates[d.abbreviation].spread,
            lower_is_better=d.abbreviation in INVERTED_CATEGORIES,
            sample=0,
            basis_seasons=(),
            period_days=d.period_days,
            era_scale=1.0,
        )
        for d in like
        if d.abbreviation in estimates
    ]


def fmt(cat: str, value: float) -> str:
    return f"{value:.3f}" if cat in PERCENTAGE_COMPONENTS else f"{value:.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2027)
    ap.add_argument("--me", default="Through The Wire")
    ap.add_argument("--bbm", type=Path, required=True)
    ap.add_argument("--bbm-per-game", type=Path)
    ap.add_argument(
        "--backtest",
        default="",
        metavar="SEASON=EXPORT",
        help="a played season and its own preseason BBM export, e.g. 2026=data/bbm/x.xls",
    )
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--noise", type=float, default=RANK_NOISE, help="board-place jitter")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("docs/opponent_check.md"))
    args = ap.parse_args()

    room = load_room(
        args.season,
        args.me,
        pool_season=None,
        pool_kind="projected",
        punt=[],
        restarts=4,
        bbm=args.bbm,
        bbm_per_game=args.bbm_per_game,
        plan="history",
    )
    teams = len(room.state.teams)
    print(f"{args.season}: {teams} teams, {len(room.candidates)} candidates", flush=True)

    factory = _session_factory()
    with factory() as session:
        league_season = session.scalars(
            select(LeagueSeason).where(LeagueSeason.season == args.season)
        ).one()
        all_seasons = sorted({y for _, y in _seasons_with_results(session)})
        days = modal_period_days(session, all_seasons)
        recent = all_seasons[-RECENT_RATE_SEASONS:]
        within = within_team_spread(session, recent, days)
        worth = valuation_worth(session, args.bbm, args.season)
        basis_size, basis_seasons = _sized_seasons(session, int(league_season.team_count))
        alt_size, alt_seasons, alternative = size_scaled_alternative(
            session, league_season, days, skip_size=basis_size
        )

        # The backtest: a played season from its own preseason projections.
        back: dict[str, Estimate] = {}
        back_actual: dict[str, Estimate] = {}
        back_season = 0
        if args.backtest:
            year, export = args.backtest.split("=", 1)
            back_season = int(year)
            back_ls = session.scalars(
                select(LeagueSeason).where(LeagueSeason.season == back_season)
            ).one()
            first = session.scalars(select(Team).where(Team.league_season_id == back_ls.id)).first()
            assert first is not None
            back_room = load_room(
                back_season,
                first.name,
                pool_season=None,
                pool_kind="projected",
                punt=[],
                restarts=4,
                bbm=Path(export),
                plan="history",
            )
            print(
                f"backtest {back_season}: {len(back_room.state.teams)} teams, "
                f"{len(back_room.candidates)} candidates; simulating",
                flush=True,
            )
            back_worth = valuation_worth(session, Path(export), back_season)
            back = simulate(
                back_room, back_worth, draws=args.draws, seed=args.seed, noise=args.noise
            )
            back_actual = posted(session, back_season, days)
            back_places = len(back_room.state.teams) * back_room.state.roster_slots
            back_games_export = export_games_top(back_room, back_worth, back_places)
            back_games_actual = games_played_top(session, back_season, back_places)

        # The games basis for this season: what the top of the export's board
        # is projected for, against what the top of recent seasons played.
        places = teams * room.state.roster_slots
        games_export = export_games_top(room, worth, places)
        played = [
            games_played_top(session, year, count * room.state.roster_slots)
            for count, year in _seasons_with_results(session)
            if year in recent
        ]
        games_actual = sum(played) / len(played) if played else games_export

    print("simulating", flush=True)
    between = simulate(room, worth, draws=args.draws, seed=args.seed, noise=args.noise)
    steadier = simulate(room, worth, draws=args.draws // 4, seed=args.seed + 1, noise=0.0)
    looser = simulate(room, worth, draws=args.draws // 4, seed=args.seed + 2, noise=3 * args.noise)
    current = {d.abbreviation: Estimate(d.mean, d.spread) for d in room.distributions}
    cats = [c for c in CATEGORIES if c in current]

    monte = {c: combined(between[c], within.get(c, 0.0), c) for c in cats}
    games_factor = games_actual / games_export if games_export else 1.0
    calibrated = {c: on_games_basis(monte[c], games_factor, c) for c in cats}
    back_factor = back_games_actual / back_games_export if back and back_games_export else 1.0
    ratio = {
        c: (back_actual[c].mean / combined(back[c], within.get(c, 0.0), c).mean)
        for c in cats
        if c in back and c in back_actual
    }

    # What the choice does to a roster: the room's own best roster, scored
    # against each estimate.
    plan = optimize(
        room.candidates,
        room.distributions,
        budget=room.state.budget,
        roster_slots=room.state.roster_slots,
        lineup=room.lineup,
        limits=room.limits,
        restarts=4,
        shape=room.allocation.limits(room.state) if room.allocation else None,
    )
    line = roster_totals(plan.players, cats)
    scored = {
        "the room's basis": score(line, room.distributions)[0],
        "size-scaled alternative": score(line, as_distributions(alternative, room.distributions))[
            0
        ],
        "Monte Carlo": score(line, as_distributions(monte, room.distributions))[0],
        "Monte Carlo, on actual games": score(
            line, as_distributions(calibrated, room.distributions)
        )[0],
    }

    basis_note = (
        f"{basis_size} teams, seasons {', '.join(map(str, basis_seasons))}"
        if basis_size == int(league_season.team_count)
        else f"borrowed from {basis_size} teams and size-scaled"
    )
    lines = [
        "# The opponent, drafted from the projections",
        "",
        f"Generated by `scripts/opponent_check.py` on the {args.season} room "
        f"({teams} teams, ${room.state.budget}, {room.state.roster_slots} places, "
        f"{len(room.candidates)} candidates from `{args.bbm.name}`), {args.draws} simulated "
        f"drafts, board places jittered by {args.noise:g}, seed {args.seed}.",
        "",
        "## What is being compared",
        "",
        f"- **The room's basis:** `category_distributions` as the draft room uses it: "
        f"{basis_note}, brought forward where the game drifts. Rates from the "
        f"{RECENT_RATE_SEASONS} most recent seasons.",
        f"- **Size-scaled alternative:** the nearest other size ({alt_size} teams, seasons "
        f"{', '.join(map(str, alt_seasons))}) scaled by the fitted per-team effect for the "
        f"difference. This is what the room used while {args.season} was fifteen teams.",
        "- **Monte Carlo:** a draft dealt from the projections (method in the script's "
        "docstring), between-team spread from the simulation, within-team spread from "
        f"history (seasons {', '.join(map(str, recent))}).",
    ]
    lines.append(
        f"- **Monte Carlo, on actual games:** the same, with counting categories scaled by "
        f"{games_factor:.3f}: the top {places} scorers of seasons "
        f"{', '.join(map(str, recent))} played {games_actual:.1f} games on average, and the "
        f"export projects {games_export:.1f} for the top {places} on its board. Streaming "
        "is what closes that gap in a real week."
    )
    lines += [
        "",
        "## Weekly opponent, by category",
        "",
        "Mean (spread).",
        "",
        "| category | room's basis | size-scaled alternative | Monte Carlo | on actual games |",
        "|---|---|---|---|---|",
    ]
    for c in cats:
        lines.append(
            f"| {c} | {fmt(c, current[c].mean)} ({fmt(c, current[c].spread)}) "
            f"| {fmt(c, alternative[c].mean)} ({fmt(c, alternative[c].spread)}) "
            f"| {fmt(c, monte[c].mean)} ({fmt(c, monte[c].spread)}) "
            f"| {fmt(c, calibrated[c].mean)} ({fmt(c, calibrated[c].spread)}) |"
        )
    lines += [
        "",
        "Monte Carlo, taken apart: the between-team spread from the simulation, the "
        "within-team spread from history, and the simulation's sensitivity to the "
        f"jitter: none (the top of the board rostered every draw) and {3 * args.noise:g} "
        "places.",
        "",
        "| category | between teams | within a team | no jitter: mean (between) "
        f"| jitter {3 * args.noise:g}: mean (between) |",
        "|---|---|---|---|---|",
    ]
    for c in cats:
        inside = within.get(c, 0.0)
        inside_abs = inside if c in PERCENTAGE_COMPONENTS else inside * between[c].mean
        lines.append(
            f"| {c} | {fmt(c, between[c].spread)} | {fmt(c, inside_abs)} "
            f"| {fmt(c, steadier[c].mean)} ({fmt(c, steadier[c].spread)}) "
            f"| {fmt(c, looser[c].mean)} ({fmt(c, looser[c].spread)}) |"
        )
    if back:
        lines += [
            "",
            f"## Backtest: {back_season}, from its own projections",
            "",
            f"The export projected {back_games_export:.1f} games for the top {back_places} on "
            f"its board; the top {back_places} scorers played {back_games_actual:.1f}, so the "
            f"games basis scales counting categories by {back_factor:.3f}.",
            "",
            "| category | simulated | on actual games | actual | actual / simulated "
            "| actual / on games |",
            "|---|---|---|---|---|---|",
        ]
        for c in cats:
            if c not in back or c not in back_actual:
                continue
            sim = combined(back[c], within.get(c, 0.0), c)
            based = on_games_basis(sim, back_factor, c)
            lines.append(
                f"| {c} | {fmt(c, sim.mean)} ({fmt(c, sim.spread)}) "
                f"| {fmt(c, based.mean)} ({fmt(c, based.spread)}) "
                f"| {fmt(c, back_actual[c].mean)} ({fmt(c, back_actual[c].spread)}) "
                f"| {ratio[c]:.3f} | {back_actual[c].mean / based.mean:.3f} |"
            )
    lines += [
        "",
        "## What it does to a roster",
        "",
        f"The room's best roster from the empty room (restarts 4, ${plan.cost}), scored "
        "against each estimate. Expected categories won a week:",
        "",
        "| against | expected wins |",
        "|---|---|",
        *(f"| {k} | {v:.3f} |" for k, v in scored.items()),
        "",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
