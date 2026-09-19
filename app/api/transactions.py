"""Transaction routes.

Roster moves, and the one narrative that only they can tell: which players
the league fought over, and what it cost.
"""

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.access import LEAGUE_MEMBER
from app.api.deps import LeagueSeasonDep, SessionDep
from app.api.schemas import ContestedClaimOut, Page, TransactionItemOut, TransactionOut
from app.db.models import Player, Team, Transaction, TransactionItem

router = APIRouter(tags=["transactions"])

TRANSACTION_PAGE_LIMIT = 500
EXECUTED = "EXECUTED"


@router.get(
    "/leagues/{league_id}/seasons/{season}/transactions",
    summary="Waiver claims, pickups and trades",
    dependencies=[LEAGUE_MEMBER],
)
def list_transactions(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    type: str | None = Query(default=None, description="e.g. WAIVER or TRADE_ACCEPT"),
    status: str | None = Query(default=None, description="e.g. EXECUTED"),
    scoring_period: int | None = Query(default=None, description="Restrict to one day"),
    min_bid: int | None = Query(default=None, ge=0, description="Only bids at or above this"),
    limit: int = Query(default=100, ge=1, le=TRANSACTION_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[TransactionOut]:
    base = select(Transaction).where(Transaction.league_season_id == league_season.id)
    if type is not None:
        base = base.where(Transaction.type == type)
    if status is not None:
        base = base.where(Transaction.status == status)
    if scoring_period is not None:
        base = base.where(Transaction.scoring_period == scoring_period)
    if min_bid is not None:
        base = base.where(Transaction.bid_amount >= min_bid)

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.options(selectinload(Transaction.items).selectinload(TransactionItem.player))
        .order_by(Transaction.scoring_period.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    teams = {
        team.id: team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }

    return Page(
        items=[
            TransactionOut(
                id=tx.id,
                scoring_period=tx.scoring_period,
                processed_at=tx.processed_at,
                team=teams.get(tx.team_id) if tx.team_id else None,
                type=tx.type,
                status=tx.status,
                bid_amount=tx.bid_amount,
                items=[
                    TransactionItemOut(
                        player_id=item.player.espn_player_id,
                        player_name=item.player.name,
                        item_type=item.item_type,
                        from_team=teams.get(item.from_team_id) if item.from_team_id else None,
                        to_team=teams.get(item.to_team_id) if item.to_team_id else None,
                    )
                    for item in tx.items
                ],
            )
            for tx in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/leagues/{league_id}/seasons/{season}/contested-claims",
    summary="Players more than one team bid on, most fought over first",
    dependencies=[LEAGUE_MEMBER],
)
def list_contested_claims(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[ContestedClaimOut]:
    """Who the league fought over, and what it cost to win.

    Only possible because failed claims are kept. Grouped by player and day,
    since the same player contested in December and again in March is two
    separate stories.
    """
    claims = (
        select(
            Transaction.scoring_period.label("scoring_period"),
            Player.name.label("player_name"),
            Transaction.status.label("status"),
            Transaction.bid_amount.label("bid"),
            Team.name.label("team_name"),
        )
        .select_from(Transaction)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .join(Player, Player.id == TransactionItem.player_id)
        .outerjoin(Team, Team.id == Transaction.team_id)
        .where(
            Transaction.league_season_id == league_season.id,
            TransactionItem.item_type == "ADD",
        )
        .subquery()
    )

    contested = (
        select(claims.c.scoring_period, claims.c.player_name)
        .group_by(claims.c.scoring_period, claims.c.player_name)
        .having(func.count() > 1)
        .subquery()
    )

    rows = session.execute(
        select(
            claims.c.scoring_period,
            claims.c.player_name,
            func.max(func.coalesce(claims.c.bid, 0))
            .filter(claims.c.status == EXECUTED)
            .label("winning_bid"),
            func.min(claims.c.team_name).filter(claims.c.status == EXECUTED).label("winner"),
            func.count().filter(claims.c.status != EXECUTED).label("losers"),
            func.max(func.coalesce(claims.c.bid, 0))
            .filter(claims.c.status != EXECUTED)
            .label("top_losing_bid"),
        )
        .join(
            contested,
            (contested.c.scoring_period == claims.c.scoring_period)
            & (contested.c.player_name == claims.c.player_name),
        )
        .group_by(claims.c.scoring_period, claims.c.player_name)
        .order_by(func.count().filter(claims.c.status != EXECUTED).desc())
        .limit(limit)
    ).all()

    return [
        ContestedClaimOut(
            scoring_period=scoring_period,
            player_name=player_name,
            winning_team=winner,
            winning_bid=winning_bid,
            losing_bids=int(losers or 0),
            highest_losing_bid=top_losing_bid,
        )
        for scoring_period, player_name, winning_bid, winner, losers, top_losing_bid in rows
    ]
