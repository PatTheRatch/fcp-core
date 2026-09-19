"""Players and their game logs.

Players are global, not league-scoped, so these routes sit at the top level
rather than under a league. The same is true of the stat lines they return.
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.access import SIGNED_IN
from app.api.deps import SessionDep
from app.api.schemas import Page, PlayerGameOut, PlayerOut
from app.db.models import Player, PlayerGameStat

router = APIRouter(tags=["players"])

PLAYER_PAGE_LIMIT = 200


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
