"""The draft room, loaded: everything a live draft needs, read once.

Both the typed room (`scripts/draft_room.py`) and the draft service
(`scripts/draft_service.py`) start from `load_room`, so the two can never
disagree about the pool, the board, the opponents or the plan.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason, Player, Team
from app.db.session import make_engine, make_session_factory
from app.draft import pool
from app.draft.availability import measured_availability
from app.draft.bbm import BBMRow, load_bbm, read_bbm
from app.draft.feed import match_team
from app.draft.market import price_board
from app.draft.optimizer import Candidate, candidates_from
from app.draft.room import (
    Allocation,
    DraftState,
    plan_allocation,
)
from app.draft.shape import winning_shape
from app.draft.targets import CategoryDistribution, category_distributions
from app.draft.tiers import LEAGUE_TIER_CURVE, apply_tier_curve
from app.draft.valuation import PERCENTAGE_COMPONENTS, PlayerProjection, value_players
from app.player_names import name_key
from app.projections import sources
from app.projections.upload import load_projection_set, set_headline, set_note


class RoomError(RuntimeError):
    """A room that cannot be opened: missing season, budget, pool or team."""


#: The nine categories in the order every page shows them. ESPN's own order
#: is whatever the league's settings say and has moved between seasons; the
#: screen's does not, so a reader's eye learns one place per category.
SCREEN_CATEGORIES = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")


@dataclass(frozen=True)
class PlayerLine:
    """One player's projected season as a per-game line, for display only.

    The room values players on season totals (`app.draft.valuation`), which is
    right for a head-to-head league and unreadable on a page: nobody knows
    what 1,742 points is. This is the same projection divided by the games it
    is spread over, with the two percentages left as rates and the attempts
    behind them carried alongside, because a rate cannot be averaged or
    compared across a roster without them. Rounded here: it exists to be
    drawn, and a screen that shades by rank does not need the last digit.
    """

    games: float
    #: The nine categories of `SCREEN_CATEGORIES`. A percentage is None when
    #: the player takes no shots of that kind, which is not the same as zero.
    per_game: dict[str, float | None]
    #: Field goals and free throws attempted per game, which is what weights
    #: the two rates into a roster's or a pool's own rate.
    fga: float
    fta: float


def player_lines(projections: Sequence[PlayerProjection]) -> dict[int, PlayerLine]:
    """Every projection as a per-game line, keyed by player id.

    A projection with no games is left out rather than divided by zero: a
    player nobody expects to play has no line to draw.
    """
    lines: dict[int, PlayerLine] = {}
    for projection in projections:
        games = float(projection.games or 0.0)
        if games <= 0:
            continue
        per_game: dict[str, float | None] = {}
        for category in SCREEN_CATEGORIES:
            components = PERCENTAGE_COMPONENTS.get(category)
            if components is None:
                per_game[category] = round(projection.get(category) / games, 3)
                continue
            made, attempted = components
            shots = projection.get(attempted)
            per_game[category] = round(projection.get(made) / shots, 4) if shots else None
        lines[projection.player_id] = PlayerLine(
            games=round(games, 1),
            per_game=per_game,
            fga=round(projection.get("FGA") / games, 2),
            fta=round(projection.get("FTA") / games, 2),
        )
    return lines


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
    #: Where the pool's numbers came from: "espn", "bbm" or "upload:<set id>"
    #: (`app.projections.sources`). Every page built from this room names it,
    #: and the two page builders ask `may_show` before rendering it, because a
    #: gated source's per-player numbers are not ours to show.
    projection_source: str = sources.ESPN
    #: The part of the source a page should name and the tag cannot carry: the
    #: export it was read from, or an uploaded set's name and note. Passed to
    #: `sources.describe` so the plan page and the screen say the same thing.
    source_detail: str = ""
    #: Our board's price for each player: the valuation, before sizing to the
    #: room. Candidates carry the going price, which is what the optimizer
    #: plans with; this is kept for the blend and for display.
    board: dict[int, int] = field(default_factory=dict)
    #: The pool's per-game lines, for the screen to shade, rank and total
    #: (`GET /api/pool`). Numbers derived per player from the projections, so
    #: they go out through the same gate as everything else derived from them.
    #: A room built by hand, as the tests build one, carries none and the
    #: screen degrades to the names and the money.
    lines: dict[int, PlayerLine] = field(default_factory=dict)


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
    me: str | int,
    *,
    pool_season: int | None,
    pool_kind: str,
    punt: Sequence[str],
    restarts: int,
    tier_curve: bool = True,
    bbm: Path | None = None,
    bbm_per_game: Path | None = None,
    projection_set: int | None = None,
    plan: str = "history",
    plan_slack: float = 0.10,
) -> Room:
    if bbm is not None and projection_set is not None:
        raise RoomError(
            "a room is drafted on one pool: pass a BBM export or an uploaded "
            "projection set, not both"
        )
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
            projection_source = sources.BBM
            source_detail = bbm.name
            pool_note = (
                f"pool: Basketball Monster, {bbm.name}: {len(projections)} players, "
                f"{loaded.matched} matched to ESPN ids ({len(loaded.loose)} by short first name), "
                f"{len(loaded.unmatched)} on the board by name only"
            )
        elif projection_set is not None:
            try:
                projections = load_projection_set(session, projection_set)
                pool_note = set_note(session, projection_set)
                source_detail = set_headline(session, projection_set)
            except ValueError as exc:
                raise RoomError(str(exc)) from exc
            if not projections:
                raise RoomError(f"projection set {projection_set} has no rows stored")
            projection_source = sources.upload_source(projection_set)
        else:
            projections = pool.load_projections(session, source_season, kind=pool_kind)
            projection_source = sources.ESPN
            source_detail = f"{source_season} {pool_kind}"
            pool_note = f"pool: ESPN {source_season} {pool_kind}"
        # One room, one source. The gate and every page read
        # `projection_source` alone, so a pool that quietly mixed two would
        # make a paid row invisible to the check that exists to find it.
        mixed = sources.sources_in(projections) - {projection_source}
        if mixed:
            raise RoomError(
                f"pool tagged {projection_source} carries {', '.join(sorted(mixed))} as well; "
                "a room is drafted on one source"
            )
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
        # BBM's games already price availability; ESPN's do not, and an
        # uploaded set makes no promise either way, so it is discounted like
        # ESPN's rather than trusted like BBM's.
        availability = 1.0 if bbm is not None else measured_availability(session).factor
        candidates = candidates_from(
            projections,
            board,
            periods=pool.effective_weeks(session),
            availability=availability,
            keys=categories,
        )
        names = {p.name: int(p.espn_player_id) for p in session.scalars(sql_select(Player)).all()}
        names.update({c.name: c.player_id for c in candidates})

        # A name, matched loosely, or ESPN's own team id -- which is how the
        # launcher names us from FCP_TRACKED_TEAM_ID without knowing the name.
        mine = teams.get(me) if isinstance(me, int) else match_team(me, teams.values())
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
        lineup = pool.lineup_for(league_season)
        limits = pool.position_limits_for(league_season)
        bbm_view = Room(
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
            bbm=bbm_rows,
            board={c.player_id: c.price for c in candidates},
            projection_source=projection_source,
        )
        # Plan at what players will cost, not at what they are worth: priced
        # at the board, the model built rosters around a $52 Doncic the room
        # has paid $69-91 for in five of six drafts.
        going = market_prices(bbm_view, state)
        board_prices = dict(bbm_view.board)
        candidates = [
            replace(c, price=going.get(c.player_id, (c.price, ""))[0] or c.price)
            for c in candidates
        ]
        allocation = None
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
            projection_source=projection_source,
            source_detail=source_detail,
            board=board_prices,
            lines=player_lines(projections),
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


#: How the going price is built, measured against 712 drafted players in
#: 2019, 2021, 2022, 2024 and 2025, each season scored with numbers taken
#: from the others (scripts/price_scorecard.py and the note in STATUS.md):
#:
#:     ESPN average price alone              misses $6.14 a player, $4.40 low
#:     our board alone                       misses $6.71
#:     half and half, fitted                 misses $5.42; stars $5.40 low
#:     half and half, sized to the room      misses $5.14; stars $2.60 low
#:
#: Sizing to the room is what an auction is: a fixed pot, a quarter of the
#: roster places bought for a dollar. The fitted blend priced the top 195 of
#: the 2027 pool at $2,833 of a $3,000 room, and the missing money is exactly
#: the stars' shortfall. So players are ranked by the blend, the bottom
#: `DOLLAR_ONE_SHARE` of places go for $1 and the next `DOLLAR_TWO_SHARE` for
#: $2, and the money the room will actually spend is shared above that in
#: proportion to the blend.
MARKET_WEIGHT = 0.5
#: Shares of all picks that went for $1 and $2, 2019-2026 (17-32% by season).
DOLLAR_ONE_SHARE = 0.242
DOLLAR_TWO_SHARE = 0.076
#: Share of the league's budget actually spent at the draft, 2019-2026.
SPEND_RATE = 0.988
#: Where the shared-out money starts: above the $1 and $2 places.
_BODY_FLOOR = 3


def market_prices(room: Room, state: DraftState) -> dict[int, tuple[int | None, str]]:
    """What each player still available will probably go for, and why.

    Ranked by the half-and-half blend of ESPN's average auction price and
    our board (the board alone for a player ESPN drafts never priced), then
    sized to the room: of the places still open, the expected number of
    remaining $1 and $2 buys go to the bottom of the ranking, and the money
    the room is expected to spend from here is shared across the rest in
    proportion to the blend. Recomputed from the live state, so as the room
    spends and the dollar players come off the board the prices follow.
    """
    taken = state.taken
    blended: list[tuple[float, int, str]] = []
    for c in room.candidates:
        if c.player_id in taken:
            continue
        board = room.board.get(c.player_id, c.price)
        row = room.bbm.get(c.player_id)
        if row is not None and row.espn_dollars is not None:
            value = MARKET_WEIGHT * max(1.0, row.espn_dollars) + (1 - MARKET_WEIGHT) * board
            source = "ESPN average and our board, sized to the room"
        else:
            value = float(board)
            source = "our board, sized to the room; no ESPN average on file"
        blended.append((value, c.player_id, source))
    blended.sort(reverse=True)
    return size_to_room(blended, state)


def size_to_room(
    blended: Sequence[tuple[float, int, str]], state: DraftState
) -> dict[int, tuple[int | None, str]]:
    """Share the money the room will spend across players ranked by value.

    `blended` is (value, player id, source), most valuable first, players
    still available only. Of the places still open, the expected number of
    remaining $1 and $2 buys go to the bottom; the money the room is
    expected to spend from here is shared across the rest in proportion to
    value. Shared by the live room and the redraft, so a replay prices a
    past season exactly the way the room prices this one.
    """
    floor = state.minimum_bid
    total_places = state.roster_slots * len(state.teams)
    bought = [p.price for p in state.picks]
    ones_left = max(0, round(DOLLAR_ONE_SHARE * total_places) - sum(1 for x in bought if x <= 1))
    twos_left = max(0, round(DOLLAR_TWO_SHARE * total_places) - sum(1 for x in bought if x == 2))
    open_places = state.open_slots
    unspent = (1 - SPEND_RATE) * state.budget * len(state.teams)
    money = max(float(open_places * floor), state.dollars_left - unspent)

    rostered = list(blended[:open_places])
    ones = min(ones_left, len(rostered))
    twos = min(twos_left, len(rostered) - ones)
    body = rostered[: len(rostered) - ones - twos]
    spare = money - ones * floor - twos * 2 - _BODY_FLOOR * len(body)
    weight = sum(max(0.0, v - _BODY_FLOOR) for v, _, _ in body)

    out: dict[int, tuple[int | None, str]] = {}
    for value, player_id, source in body:
        share = spare * max(0.0, value - _BODY_FLOOR) / weight if weight > 0 and spare > 0 else 0.0
        out[player_id] = (max(_BODY_FLOOR, round(_BODY_FLOOR + share)), source)
    for _, player_id, source in rostered[len(body) : len(body) + twos]:
        out[player_id] = (2, source)
    for _, player_id, source in rostered[len(body) + twos :]:
        out[player_id] = (floor, source)
    for _, player_id, source in blended[open_places:]:
        out[player_id] = (floor, source)
    return out


def market_price(room: Room, state: DraftState, player_id: int) -> tuple[int | None, str]:
    """`market_prices` for one player."""
    return market_prices(room, state).get(player_id, (None, "not on the board"))


#: How far our ceiling has to clear the going price before a cheap price
#: stops looking like a bargain and starts looking like information.
BARGAIN_GAP = 8
BARGAIN_RATIO = 1.5
#: BBM backs a bargain when its value clears the going price by this much.
BBM_BACKS_BY = 5


def bargain_warning(going: int | None, ceiling: int | None, bbm_total: float | None) -> str | None:
    """A caution when the room is cheap on a player only our model likes.

    Replaying 2022, the room bought the players the market had discounted for
    reasons the projections did not carry: Kyrie Irving at $6 (the vaccine
    mandate), Jonathan Isaac at $6 (he missed the season), Porter Jr., Ball
    and George (long absences). Every one had a ceiling far above his price.
    A price far below projected value is usually information. BBM's values
    price availability and role, so when BBM agrees the gap is more likely a
    real bargain; when it does not, or has no view, the card says so.
    """
    if going is None or ceiling is None:
        return None
    if ceiling < going + BARGAIN_GAP or ceiling < going * BARGAIN_RATIO:
        return None
    if bbm_total is not None and bbm_total >= going + BBM_BACKS_BY:
        return None
    basis = (
        "BBM has no value for him"
        if bbm_total is None
        else f"BBM values him at ${round(bbm_total)}"
    )
    return (
        f"Our ceiling is ${ceiling - going} above what he'll go for and {basis}. "
        "A price this low is often news the projections don't have (injury, role, "
        "suspension) — check before calling it a bargain."
    )
