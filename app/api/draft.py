"""Draft routes.

The league drafts by auction, so every pick carries a price. That makes a
pick comparable against what the player actually returned, which is the one
question a draft board cannot answer on its own.
"""

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import LeagueSeasonDep, SessionDep
from app.api.schemas import DraftPickOut, DraftValueOut
from app.db.models import DraftPick, Player, PlayerGameStat, Team

router = APIRouter(tags=["draft"])


@router.get(
    "/leagues/{league_id}/seasons/{season}/draft",
    summary="The draft board, in pick order",
)
def get_draft(league_season: LeagueSeasonDep, session: SessionDep) -> list[DraftPickOut]:
    teams = {
        team.id: team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }
    rows = session.execute(
        select(DraftPick, Player)
        .join(Player, Player.id == DraftPick.player_id)
        .where(DraftPick.league_season_id == league_season.id)
        .order_by(DraftPick.round_num, DraftPick.round_pick)
    ).all()

    return [
        DraftPickOut(
            round_num=pick.round_num,
            round_pick=pick.round_pick,
            player_id=player.espn_player_id,
            player_name=player.name,
            team=teams.get(pick.team_id) if pick.team_id else None,
            nominated_by=teams.get(pick.nominating_team_id) if pick.nominating_team_id else None,
            paid=pick.bid_amount,
            keeper=pick.keeper,
        )
        for pick, player in rows
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/draft-value",
    summary="What each pick cost against what the player returned",
)
def get_draft_value(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    min_paid: int = Query(default=1, ge=1, description="Ignore picks cheaper than this"),
    order: str = Query(default="worst", description="'worst' or 'best' value first"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[DraftValueOut]:
    """Points returned per dollar paid.

    A blunt measure, and deliberately so: it says nothing about which
    categories a team needed. It answers who was expensive and quiet, which
    is usually the argument being had.
    """
    produced = (
        select(
            PlayerGameStat.player_id.label("player_id"),
            func.coalesce(func.sum(PlayerGameStat.points), 0.0).label("points"),
            func.count().filter(PlayerGameStat.played.is_(True)).label("games"),
        )
        .where(PlayerGameStat.season == league_season.season)
        .group_by(PlayerGameStat.player_id)
        .subquery()
    )

    per_dollar = (produced.c.points / DraftPick.bid_amount).label("per_dollar")
    rows = session.execute(
        select(Player.name, Team.name, DraftPick.bid_amount, produced.c.points, produced.c.games)
        .select_from(DraftPick)
        .join(Player, Player.id == DraftPick.player_id)
        .outerjoin(Team, Team.id == DraftPick.team_id)
        .join(produced, produced.c.player_id == DraftPick.player_id)
        .where(
            DraftPick.league_season_id == league_season.id,
            DraftPick.bid_amount >= min_paid,
        )
        .order_by(per_dollar.asc() if order == "worst" else per_dollar.desc())
        .limit(limit)
    ).all()

    return [
        DraftValueOut(
            player_name=player_name,
            team=team_name,
            paid=int(paid),
            season_points=float(points or 0.0),
            points_per_dollar=round(float(points or 0.0) / paid, 2) if paid else 0.0,
            games_played=int(games or 0),
        )
        for player_name, team_name, paid, points, games in rows
    ]
