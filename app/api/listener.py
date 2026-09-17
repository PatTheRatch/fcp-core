"""What the listener saw: events, a player's status history, and news.

The season routes hang off the league season like the rest of the API.
Status and news are on the player, because a card is global: the same
snapshot serves every league holding him.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.deps import LeagueSeasonDep, SessionDep
from app.api.schemas import Page, PlayerNewsOut, StatusEventOut, StatusSnapshotOut
from app.db.models import Player, PlayerNews, PlayerStatusEvent, PlayerStatusSnapshot
from app.listener.events import KINDS
from app.listener.pool import UNROSTERED_STATUSES
from app.listener.snapshots import latest_snapshot_ids

router = APIRouter(tags=["listener"])

EVENT_PAGE_LIMIT = 500
KINDS_HELP = "Any of " + ", ".join(KINDS)
SNAPSHOT_PAGE_LIMIT = 500


def _players_on(season: int, team: int) -> Select[tuple[int]]:
    """Player ids whose latest snapshot puts them on `team`, or on the wire for 0."""
    base = select(PlayerStatusSnapshot.player_id).where(
        PlayerStatusSnapshot.id.in_(latest_snapshot_ids(season))
    )
    if team == 0:
        return base.where(PlayerStatusSnapshot.status.in_(UNROSTERED_STATUSES))
    return base.where(PlayerStatusSnapshot.on_team_id == team)


@router.get(
    "/leagues/{league_id}/seasons/{season}/events",
    summary="Status changes the listener saw this season, newest first",
)
def list_events(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    since: Annotated[datetime | None, Query(description="Only events observed after this")] = None,
    kinds: Annotated[list[str] | None, Query(description=KINDS_HELP)] = None,
    team: int | None = Query(
        default=None,
        ge=0,
        description="ESPN team id to restrict to that roster, or 0 for free agents",
    ),
    limit: int = Query(default=100, ge=1, le=EVENT_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[StatusEventOut]:
    unknown = sorted(set(kinds or []) - set(KINDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown event kinds: {', '.join(unknown)}")

    base = select(PlayerStatusEvent).where(PlayerStatusEvent.season == league_season.season)
    if since is not None:
        base = base.where(PlayerStatusEvent.observed_at > since)
    if kinds:
        base = base.where(PlayerStatusEvent.kind.in_(kinds))
    if team is not None:
        base = base.where(PlayerStatusEvent.player_id.in_(_players_on(league_season.season, team)))

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    found = session.scalars(
        base.order_by(PlayerStatusEvent.observed_at.desc(), PlayerStatusEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[
            StatusEventOut(
                id=e.id,
                espn_player_id=e.player.espn_player_id,
                player_name=e.player.name,
                season=e.season,
                kind=e.kind,
                observed_at=e.observed_at,
                previous=e.previous,
                current=e.current,
                detail=e.detail,
                notified_at=e.notified_at,
            )
            for e in found
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def _player(session: Session, espn_player_id: int) -> Player:
    player = session.scalar(select(Player).where(Player.espn_player_id == espn_player_id))
    if player is None:
        raise HTTPException(status_code=404, detail=f"player {espn_player_id} not found")
    return player


@router.get("/players/{player_id}/status", summary="A player's status history, newest first")
def list_player_status(
    player_id: int,
    session: SessionDep,
    season: int | None = Query(default=None, description="Restrict to one season"),
    limit: int = Query(default=100, ge=1, le=SNAPSHOT_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[StatusSnapshotOut]:
    player = _player(session, player_id)
    base = select(PlayerStatusSnapshot).where(PlayerStatusSnapshot.player_id == player.id)
    if season is not None:
        base = base.where(PlayerStatusSnapshot.season == season)

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    found = session.scalars(
        base.order_by(PlayerStatusSnapshot.observed_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[
            StatusSnapshotOut(
                season=s.season,
                observed_at=s.observed_at,
                pass_label=s.pass_label,
                injury_status=s.injury_status,
                injured=s.injured,
                expected_return_date=s.expected_return_date,
                pro_team_id=s.pro_team_id,
                on_team_id=s.on_team_id,
                status=s.status,
                percent_owned=s.percent_owned,
                percent_change=s.percent_change,
                percent_started=s.percent_started,
                auction_value_average=s.auction_value_average,
            )
            for s in found
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/players/{player_id}/news", summary="Stored news about a player, newest first")
def list_player_news(
    player_id: int,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=SNAPSHOT_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[PlayerNewsOut]:
    player = _player(session, player_id)
    base = select(PlayerNews).where(PlayerNews.player_id == player.id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    found = session.scalars(
        base.order_by(PlayerNews.published.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[
            PlayerNewsOut(
                published=n.published,
                headline=n.headline,
                story=n.story,
                source=n.source,
                seen_at=n.seen_at,
            )
            for n in found
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
