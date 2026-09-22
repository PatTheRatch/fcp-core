"""Players, their game logs, and the card every in-season page shows on a name.

Players are global, not league-scoped, so the search and the game log sit at
the top level rather than under a league. The same is true of the stat lines
they return.

**The card is not.** What a page shows when a reader hovers a name -- his line
per game, the games he has left, the games he has in the playoff weeks, what
the projection rests on -- is counted over one league season's calendar and
valued through one league's own standard, so it hangs off the league and the
season like every other in-season answer, and it is a league member's to read,
exactly as the pages it is opened from are (docs/accounts.md). The numbers are
`app.inseason.card`'s, which are the reports' own: nothing here computes a
second version of anything.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api import pickups
from app.api.access import LEAGUE_MEMBER, SIGNED_IN
from app.api.deps import LeagueSeasonDep, SessionDep
from app.api.schemas import Page, PlayerCardOut, PlayerGameOut, PlayerOut
from app.db.models import Player, PlayerGameStat
from app.inseason.card import Card, player_card
from app.pickups.state import season_calendar

router = APIRouter(tags=["players"])

PLAYER_PAGE_LIMIT = 200

#: The nine, in the order the site draws them, for reading a line off.
NINE = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")


@router.get("/players", summary="Search stored players by name", dependencies=[SIGNED_IN])
def list_players(
    session: SessionDep,
    name: str | None = Query(default=None, description="Case-insensitive substring match"),
    limit: int = Query(default=50, ge=1, le=PLAYER_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[PlayerOut]:
    base = select(Player)
    if name:
        base = base.where(Player.name.ilike(f"%{name}%"))

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    players = session.scalars(base.order_by(Player.name).limit(limit).offset(offset)).all()
    return Page(
        items=[PlayerOut(espn_player_id=p.espn_player_id, name=p.name) for p in players],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/players/{player_id}", summary="One player", dependencies=[SIGNED_IN])
def get_player(player_id: int, session: SessionDep) -> PlayerOut:
    player = session.scalar(select(Player).where(Player.espn_player_id == player_id))
    if player is None:
        raise HTTPException(status_code=404, detail=f"player {player_id} not found")
    return PlayerOut(espn_player_id=player.espn_player_id, name=player.name)


@router.get(
    "/leagues/{league_id}/seasons/{season}/players/{player_id}/card",
    summary="One player's card: his line, his games, his status and what it rests on",
    dependencies=[LEAGUE_MEMBER],
)
def player_card_route(
    player_id: int,
    league_season: LeagueSeasonDep,
    session: SessionDep,
    today: Annotated[
        int | None, Query(ge=1, description="Scoring period to read as of; defaults to the day")
    ] = None,
) -> PlayerCardOut:
    """Everything a page shows on a name, as of the morning of `today`.

    `player_id` is ESPN's, as on every other route. A day is read the way the
    in-season pages read one, so a card opened on a replayed day says what was
    known that morning and nothing after it.
    """
    player = session.scalar(select(Player).where(Player.espn_player_id == player_id))
    if player is None:
        raise HTTPException(status_code=404, detail=f"player {player_id} not found")
    calendar = season_calendar(session, int(league_season.season))
    if today is not None:
        day = today
    elif calendar is not None:
        day = calendar.scoring_period_on(pickups.TODAY())
    else:
        day = 1
    try:
        card = player_card(session, league_season, int(player.id), day)
    except ValueError as error:
        # A season with no matchup periods has no stretch to count games over.
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _card_out(card, int(player.espn_player_id))


def _card_out(card: Card, espn_player_id: int) -> PlayerCardOut:
    return PlayerCardOut(
        espn_player_id=espn_player_id,
        name=card.name,
        position=card.position,
        pro_team_id=card.pro_team_id,
        pro_team=card.pro_team,
        injury_status=card.injury_status,
        expected_return_date=card.expected_return_date,
        hurt=card.hurt,
        today=card.today,
        last_scoring_period=card.last_scoring_period,
        games_left=card.games_left,
        playoff_games=card.playoff_games,
        playoff_first=card.playoff_first,
        playoff_last=card.playoff_last,
        games_so_far=card.games_so_far,
        had_projection=card.had_projection,
        projection_source=card.projection_source,
        thin=card.thin,
        per_game=card.per_game.totals(list(NINE)),
        weekly=card.weekly.totals(list(NINE)),
    )


@router.get("/players/{player_id}/games", summary="A player's game log", dependencies=[SIGNED_IN])
def list_player_games(
    player_id: int,
    session: SessionDep,
    season: int | None = Query(default=None, description="Restrict to one season"),
    played_only: bool = Query(
        default=False, description="Drop days their team played and they did not"
    ),
    limit: int = Query(default=100, ge=1, le=PLAYER_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[PlayerGameOut]:
    player = session.scalar(select(Player).where(Player.espn_player_id == player_id))
    if player is None:
        raise HTTPException(status_code=404, detail=f"player {player_id} not found")

    base = select(PlayerGameStat).where(PlayerGameStat.player_id == player.id)
    if season is not None:
        base = base.where(PlayerGameStat.season == season)
    if played_only:
        base = base.where(PlayerGameStat.played.is_(True))

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    games = session.scalars(
        base.order_by(PlayerGameStat.season, PlayerGameStat.scoring_period)
        .limit(limit)
        .offset(offset)
    ).all()

    return Page(
        items=[
            PlayerGameOut(
                scoring_period=g.scoring_period,
                game_date=g.game_date,
                opponent=g.opponent,
                played=g.played,
                minutes=g.minutes,
                points=g.points,
                rebounds=g.rebounds,
                assists=g.assists,
                steals=g.steals,
                blocks=g.blocks,
                turnovers=g.turnovers,
                three_pointers_made=g.three_pointers_made,
                field_goals_made=g.field_goals_made,
                field_goals_attempted=g.field_goals_attempted,
                free_throws_made=g.free_throws_made,
                free_throws_attempted=g.free_throws_attempted,
            )
            for g in games
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
