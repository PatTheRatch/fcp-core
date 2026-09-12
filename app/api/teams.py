"""Daily lineups and bench reports.

This is where the daily grain earns its keep. `roster_slots` says who a team
held during a matchup period; these routes read `daily_lineup_slots`, which
says what the team actually did with them, and join the player's stat line
for that same day.
"""

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.schemas import (
    BenchCallOut,
    BenchReportOut,
    LineupSlotOut,
    Page,
)
from app.db.models import DailyLineupSlot, MatchupPeriod, Player, PlayerGameStat

router = APIRouter(tags=["teams"])

#: A season is about 160 days times a roster, so lineups are always paged.
LINEUP_PAGE_LIMIT = 500
#: How many individual bench calls a report returns.
WORST_CALL_LIMIT = 10


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/lineups",
    summary="Where every player sat, day by day, with what they scored",
)
def list_lineups(
    team: TeamDep,
    league_season: LeagueSeasonDep,
    session: SessionDep,
    scoring_period: int | None = Query(default=None, description="Restrict to one day"),
    period: int | None = Query(default=None, description="Restrict to one matchup period"),
    started: bool | None = Query(
        default=None, description="True for starters only, false for bench and injured reserve"
    ),
    limit: int = Query(default=100, ge=1, le=LINEUP_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[LineupSlotOut]:
    """The day's lineup, left-joined to that day's stat line.

    The join is outer because a slot can exist on a day the player's team had
    no fixture, in which case there is simply nothing to report.
    """
    base = (
        select(DailyLineupSlot, Player, PlayerGameStat)
        .join(Player, Player.id == DailyLineupSlot.player_id)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .outerjoin(
            PlayerGameStat,
            (PlayerGameStat.player_id == DailyLineupSlot.player_id)
            & (PlayerGameStat.scoring_period == DailyLineupSlot.scoring_period)
            & (PlayerGameStat.season == league_season.season),
        )
        .where(DailyLineupSlot.team_id == team.id)
    )
    if scoring_period is not None:
        base = base.where(DailyLineupSlot.scoring_period == scoring_period)
    if period is not None:
        base = base.where(MatchupPeriod.period == period)
    if started is not None:
        base = base.where(DailyLineupSlot.started.is_(started))

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(DailyLineupSlot.scoring_period, Player.name).limit(limit).offset(offset)
    ).all()

    return Page(
        items=[
            LineupSlotOut(
                scoring_period=slot.scoring_period,
                player_id=player.espn_player_id,
                player_name=player.name,
                slot=slot.slot,
                started=slot.started,
                injured=slot.injured,
                injury_status=slot.injury_status,
                played=game.played if game else None,
                points=game.points if game else None,
                rebounds=game.rebounds if game else None,
                assists=game.assists if game else None,
            )
            for slot, player, game in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/bench",
    summary="What a team left on its bench, and its worst individual calls",
)
def get_bench_report(
    team: TeamDep,
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(default=False, description="Include playoff periods"),
) -> BenchReportOut:
    """Totals for benched production, plus days a benched player beat every starter.

    Only players who actually played count toward the totals: a benched
    player whose team had no fixture cost the manager nothing.

    Both halves read from explicitly labelled subqueries. Aggregating over an
    outer table while selecting from a subquery of it silently produces a
    cartesian product, which is how an earlier version of this route
    overcounted.
    """

    def _benched_days(started: bool) -> Any:
        query = (
            select(
                DailyLineupSlot.scoring_period.label("scoring_period"),
                Player.name.label("player_name"),
                PlayerGameStat.points.label("points"),
                PlayerGameStat.game_date.label("game_date"),
            )
            .select_from(DailyLineupSlot)
            .join(Player, Player.id == DailyLineupSlot.player_id)
            .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
            .join(
                PlayerGameStat,
                (PlayerGameStat.player_id == DailyLineupSlot.player_id)
                & (PlayerGameStat.scoring_period == DailyLineupSlot.scoring_period)
                & (PlayerGameStat.season == league_season.season),
            )
            .where(
                DailyLineupSlot.team_id == team.id,
                DailyLineupSlot.started.is_(started),
                PlayerGameStat.played.is_(True),
            )
        )
        if not include_playoffs:
            query = query.where(MatchupPeriod.is_playoff.is_(False))
        return query.subquery()

    bench = _benched_days(started=False)
    totals = session.execute(
        select(
            func.coalesce(func.sum(bench.c.points), 0.0),
            func.coalesce(func.sum(case((bench.c.points >= 20, 1), else_=0)), 0),
        )
    ).one()

    # The best starter this team fielded on each day, to judge the call against.
    starters = _benched_days(started=True)
    best_starter = (
        select(
            starters.c.scoring_period.label("scoring_period"),
            func.max(starters.c.points).label("best"),
        )
        .group_by(starters.c.scoring_period)
        .subquery()
    )

    calls = session.execute(
        select(
            bench.c.scoring_period,
            bench.c.game_date,
            bench.c.player_name,
            bench.c.points,
            best_starter.c.best,
        )
        .join(best_starter, best_starter.c.scoring_period == bench.c.scoring_period)
        .where(bench.c.points > best_starter.c.best)
        .order_by((bench.c.points - best_starter.c.best).desc())
        .limit(WORST_CALL_LIMIT)
    ).all()

    return BenchReportOut(
        espn_team_id=team.espn_team_id,
        name=team.name,
        bench_points=float(totals[0]),
        benched_games_of_20_plus=int(totals[1]),
        worst_calls=[
            BenchCallOut(
                scoring_period=scoring_period,
                game_date=game_date,
                player_name=player_name,
                benched_points=float(points or 0.0),
                best_starter_points=float(best or 0.0),
                margin=float((points or 0.0) - (best or 0.0)),
            )
            for scoring_period, game_date, player_name, points, best in calls
        ],
    )
