"""Narrative routes.

Thin wrappers over `app.narratives`, which holds the derivation. None of
this is data ESPN reports; it is all computed from what was ingested.

Owner-level routes sit under the league rather than a season, because an
owner's GUID is stable across seasons and their history is the point.
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app import narratives
from app.api.deps import LeagueIdPath, LeagueSeasonDep, SessionDep
from app.api.schemas import (
    BenchTotalOut,
    CategoryRecordOut,
    HeadToHeadOut,
    LeagueBenchCallOut,
    NotableMatchupOut,
    NotableMatchupsOut,
    OwnerRecordOut,
    OwnerSeasonOut,
    ProjectionGapOut,
    StreakOut,
    TeamCategoryProfileOut,
)
from app.db.models import DraftPick, League, Player, PlayerSeasonStat, Team

router = APIRouter(tags=["narratives"])

NOTABLE_LIMIT = 25


@router.get(
    "/leagues/{league_id}/seasons/{season}/streaks",
    summary="Longest winning and losing runs per team",
)
def get_streaks(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(default=False, description="Include playoff matchups"),
) -> list[StreakOut]:
    sides = narratives.matchup_sides(session, league_season, include_playoffs=include_playoffs)
    return [
        StreakOut(
            espn_team_id=s.espn_team_id,
            name=s.name,
            longest_win_streak=s.longest_win_streak,
            longest_loss_streak=s.longest_loss_streak,
            final_streak=s.final_streak,
            final_streak_result=s.final_streak_result,
        )
        for s in narratives.streaks(sides)
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/category-profiles",
    summary="Where each team was strong, category by category",
)
def get_category_profiles(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(default=False, description="Include playoff matchups"),
) -> list[TeamCategoryProfileOut]:
    profiles = narratives.category_profiles(
        session, league_season, include_playoffs=include_playoffs
    )
    return [
        TeamCategoryProfileOut(
            espn_team_id=profile.espn_team_id,
            name=profile.name,
            categories=[
                CategoryRecordOut(
                    abbreviation=c.abbreviation,
                    stat_id=c.stat_id,
                    won=c.won,
                    lost=c.lost,
                    tied=c.tied,
                    win_rate=c.win_rate,
                )
                for c in profile.categories
            ],
        )
        for profile in profiles
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/bench-leaderboard",
    summary="Which teams left the most on their bench",
)
def get_bench_leaderboard(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(default=False, description="Include playoff periods"),
) -> list[BenchTotalOut]:
    return [
        BenchTotalOut(
            espn_team_id=t.espn_team_id,
            name=t.name,
            bench_points=t.bench_points,
            benched_games_of_20_plus=t.benched_games_of_20_plus,
            benched_appearances=t.benched_appearances,
        )
        for t in narratives.bench_leaderboard(
            session, league_season, include_playoffs=include_playoffs
        )
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/worst-bench-calls",
    summary="Days a benched player beat every starter, league-wide",
)
def get_worst_bench_calls(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[LeagueBenchCallOut]:
    return [
        LeagueBenchCallOut(
            scoring_period=c.scoring_period,
            team_name=c.team_name,
            player_name=c.player_name,
            benched_points=c.benched_points,
            best_starter_points=c.best_starter_points,
            margin=c.margin,
        )
        for c in narratives.worst_bench_calls(session, league_season, limit=limit)
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/notable-matchups",
    summary="The season's biggest sweeps and closest calls",
)
def get_notable_matchups(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(default=True, description="Include playoff matchups"),
    limit: int = Query(default=10, ge=1, le=NOTABLE_LIMIT),
) -> NotableMatchupsOut:
    sides = narratives.matchup_sides(session, league_season, include_playoffs=include_playoffs)
    grouped = narratives.notable_matchups(sides, limit=limit)

    def out(items: list[narratives.NotableMatchup]) -> list[NotableMatchupOut]:
        return [
            NotableMatchupOut(
                period=m.period,
                is_playoff=m.is_playoff,
                winner_name=m.winner_name,
                loser_name=m.loser_name,
                categories_won=m.categories_won,
                categories_lost=m.categories_lost,
                categories_tied=m.categories_tied,
                margin=m.margin,
            )
            for m in items
        ]

    return NotableMatchupsOut(
        sweeps=out(grouped["sweeps"]), nail_biters=out(grouped["nail_biters"])
    )


def _require_league(session: SessionDep, league_id: int) -> None:
    if session.scalar(select(League).where(League.espn_league_id == league_id)) is None:
        raise HTTPException(status_code=404, detail=f"league {league_id} not found")


@router.get(
    "/leagues/{league_id}/owners",
    summary="Every owner's record across all stored seasons",
)
def get_owner_records(
    league_id: LeagueIdPath,
    session: SessionDep,
    include_playoffs: bool = Query(default=False, description="Include playoff matchups"),
) -> list[OwnerRecordOut]:
    """All-time records. Owners persist across seasons; teams do not."""
    _require_league(session, league_id)
    return [
        OwnerRecordOut(
            owner_id=r.owner_id,
            display_name=r.display_name,
            matchups_won=r.matchups_won,
            matchups_lost=r.matchups_lost,
            matchups_tied=r.matchups_tied,
            titles=r.titles,
            seasons=[
                OwnerSeasonOut(
                    season=s.season,
                    team_name=s.team_name,
                    matchups_won=s.matchups_won,
                    matchups_lost=s.matchups_lost,
                    matchups_tied=s.matchups_tied,
                    final_standing=s.final_standing,
                )
                for s in r.seasons
            ],
        )
        for r in narratives.owner_records(session, league_id, include_playoffs=include_playoffs)
    ]


@router.get(
    "/leagues/{league_id}/head-to-head",
    summary="Every pair of owners who have met, all seasons combined",
)
def get_head_to_head(
    league_id: LeagueIdPath,
    session: SessionDep,
    include_playoffs: bool = Query(default=False, description="Include playoff matchups"),
    min_meetings: int = Query(default=1, ge=1, description="Drop pairs who met fewer times"),
) -> list[HeadToHeadOut]:
    _require_league(session, league_id)
    return [
        HeadToHeadOut(
            owner_a=h.owner_a,
            owner_a_name=h.owner_a_name,
            owner_b=h.owner_b,
            owner_b_name=h.owner_b_name,
            a_wins=h.a_wins,
            b_wins=h.b_wins,
            ties=h.ties,
            meetings=h.meetings,
            seasons=h.seasons,
        )
        for h in narratives.head_to_head(
            session, league_id, include_playoffs=include_playoffs, min_meetings=min_meetings
        )
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/projection-gaps",
    summary="Who beat their preseason projection and who did not",
)
def get_projection_gaps(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    order: str = Query(default="misses", description="'misses' or 'beats' first"),
    drafted_only: bool = Query(
        default=True, description="Only players someone paid for at the draft"
    ),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ProjectionGapOut]:
    """ESPN's own forecast against what happened.

    Mostly a health story rather than a scouting one: the largest misses
    belong to players who were projected a full season and did not get one.
    The games columns are returned alongside so that is visible rather than
    mistaken for a collapse in form.
    """
    projected = (
        select(
            PlayerSeasonStat.player_id.label("player_id"),
            PlayerSeasonStat.points.label("points"),
            PlayerSeasonStat.games_played.label("games"),
        )
        .where(
            PlayerSeasonStat.season == league_season.season,
            PlayerSeasonStat.kind == "projected",
        )
        .subquery()
    )
    actual = (
        select(
            PlayerSeasonStat.player_id.label("player_id"),
            PlayerSeasonStat.points.label("points"),
            PlayerSeasonStat.games_played.label("games"),
        )
        .where(
            PlayerSeasonStat.season == league_season.season,
            PlayerSeasonStat.kind == "total",
        )
        .subquery()
    )

    difference = (actual.c.points - projected.c.points).label("difference")
    query = (
        select(
            Player.name,
            Team.name,
            DraftPick.bid_amount,
            projected.c.points,
            actual.c.points,
            projected.c.games,
            actual.c.games,
        )
        .select_from(projected)
        .join(actual, actual.c.player_id == projected.c.player_id)
        .join(Player, Player.id == projected.c.player_id)
        .outerjoin(
            DraftPick,
            (DraftPick.player_id == projected.c.player_id)
            & (DraftPick.league_season_id == league_season.id),
        )
        .outerjoin(Team, Team.id == DraftPick.team_id)
        .where(projected.c.points.is_not(None), actual.c.points.is_not(None))
        .order_by(difference.asc() if order == "misses" else difference.desc())
        .limit(limit)
    )
    if drafted_only:
        query = query.where(DraftPick.bid_amount.is_not(None))

    return [
        ProjectionGapOut(
            player_name=name,
            drafted_by=team,
            paid=paid,
            projected_points=float(proj_pts or 0.0),
            actual_points=float(act_pts or 0.0),
            difference=float((act_pts or 0.0) - (proj_pts or 0.0)),
            projected_games=proj_gp,
            actual_games=act_gp,
        )
        for name, team, paid, proj_pts, act_pts, proj_gp, act_gp in session.execute(query)
    ]
