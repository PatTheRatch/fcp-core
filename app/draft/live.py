"""The draft room, loaded: everything a live draft needs, read once.

Both the typed room (`scripts/draft_room.py`) and the draft service
(`scripts/draft_service.py`) start from `load_room`, so the two can never
disagree about the pool, the board, the opponents or the plan.
"""

from __future__ import annotations

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
from app.draft.feed import match_team
from app.draft.market import price_board
from app.draft.optimizer import Candidate, candidates_from
from app.draft.room import (
    Allocation,
    DraftState,
    inflation,
    plan_allocation,
    reprice,
)
from app.draft.shape import winning_shape
from app.draft.targets import CategoryDistribution, category_distributions
from app.draft.tiers import LEAGUE_TIER_CURVE, apply_tier_curve
from app.draft.valuation import value_players


class RoomError(RuntimeError):
    """A room that cannot be opened: missing season, budget, pool or team."""


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
            raise RoomError(f"season {season} is not in the database; ingest it first")
        if league_season.auction_budget <= 0:
            raise RoomError(
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
            raise RoomError(
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
            raise RoomError(f"no team called {me!r}. Teams: {', '.join(teams.values())}")
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


#: What this league pays, from ESPN's average auction price and our board.
#: Fitted on 712 drafted players in 2019, 2021, 2022, 2024 and 2025 against
#: the prices this league actually paid, and scored season by season with
#: each season held out of its own fit (scripts/price_scorecard.py):
#:
#:     ESPN average price alone     misses $6.14 a player, $4.40 too low
#:     our board alone              misses $6.71
#:     half and half, fitted        misses $5.42, unbiased
#:
#: ESPN's average comes from leagues of ten and twelve, which pay less for
#: the middle of the board than this one does; the board knows this
#: league's size and spending shape but not the reputations the room pays
#: for. Each covers the other's blind spot. The misses that remain are the
#: stars: $11 a player at $40 and up, where Doncic went for $91.
MARKET_WEIGHT = 0.5
MARKET_INTERCEPT = 0.56
MARKET_SLOPE = 1.133


def market_prices(room: Room, state: DraftState) -> dict[int, tuple[int | None, str]]:
    """What each player still on the board will probably go for, and why.

    Where ESPN's average auction price is on file: the fitted blend of it
    and our board (see `MARKET_WEIGHT`), both repriced for the money left in
    the room. Where it is not, our board alone, which is what the blend
    falls back to for rookies and fringe players ESPN drafts never priced.
    """
    floor = state.minimum_bid
    factor = inflation(state, room.candidates)
    board = {c.player_id: c.price for c in reprice(state, room.candidates)}
    out: dict[int, tuple[int | None, str]] = {}
    for player_id, price in board.items():
        row = room.bbm.get(player_id)
        if row is not None and row.espn_dollars is not None:
            espn = floor + max(0.0, row.espn_dollars - floor) * factor
            blended = MARKET_WEIGHT * espn + (1 - MARKET_WEIGHT) * price
            going = round(MARKET_INTERCEPT + MARKET_SLOPE * blended)
            out[player_id] = (
                max(floor, going),
                "ESPN average and our board, fitted to this league",
            )
        else:
            out[player_id] = (price, "our board; no ESPN average on file")
    return out


def market_price(room: Room, state: DraftState, player_id: int) -> tuple[int | None, str]:
    """`market_prices` for one player."""
    return market_prices(room, state).get(player_id, (None, "not on the board"))
