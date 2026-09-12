"""Leagues, seasons, teams, standings and matchups.

Routes are keyed on the identifiers ESPN uses rather than on surrogate
database ids, so a URL can be constructed from what a user already knows:
their league id and a year.
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import LeagueSeasonDep, SessionDep
from app.api.schemas import (
    CategoryOut,
    LeagueOut,
    MatchupCategoryOut,
    MatchupOut,
    MatchupPeriodOut,
    MatchupSideOut,
    OwnerOut,
    Page,
    SeasonOut,
    SeasonSummaryOut,
    StandingOut,
    TeamOut,
)
from app.db.models import (
    League,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Team,
)

router = APIRouter(tags=["leagues"])

#: Bounded by a season's length, so listing every matchup is safe by default.
MATCHUP_PAGE_LIMIT = 200


@router.get("/leagues", summary="Every league stored, with the seasons held for each")
def list_leagues(session: SessionDep) -> list[LeagueOut]:
    leagues = session.scalars(
        select(League).options(selectinload(League.seasons)).order_by(League.espn_league_id)
    ).all()
    return [
        LeagueOut(
            espn_league_id=league.espn_league_id,
            seasons=sorted(season.season for season in league.seasons),
        )
        for league in leagues
    ]


@router.get("/leagues/{league_id}/seasons", summary="Seasons stored for one league")
def list_seasons(league_id: int, session: SessionDep) -> list[SeasonSummaryOut]:
    league = session.scalar(select(League).where(League.espn_league_id == league_id))
    if league is None:
        raise HTTPException(status_code=404, detail=f"league {league_id} not found")

    seasons = session.scalars(
        select(LeagueSeason)
        .where(LeagueSeason.league_id == league.id)
        .order_by(LeagueSeason.season)
    ).all()
    return [
        SeasonSummaryOut(
            season=s.season, name=s.name, scoring_type=s.scoring_type, team_count=s.team_count
        )
        for s in seasons
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}",
    summary="One season's settings as they were that year",
)
def get_season(league_season: LeagueSeasonDep) -> SeasonOut:
    return SeasonOut(
        season=league_season.season,
        name=league_season.name,
        scoring_type=league_season.scoring_type,
        team_count=league_season.team_count,
        regular_season_periods=league_season.regular_season_periods,
        total_matchup_periods=league_season.total_matchup_periods,
        playoff_team_count=league_season.playoff_team_count,
        trade_deadline=league_season.trade_deadline,
        categories=[
            CategoryOut(stat_id=c.stat_id, abbreviation=c.abbreviation, position=c.position)
            for c in league_season.categories
        ],
    )


@router.get("/leagues/{league_id}/seasons/{season}/teams", summary="Teams and their owners")
def list_teams(league_season: LeagueSeasonDep, session: SessionDep) -> list[TeamOut]:
    teams = session.scalars(
        select(Team)
        .options(selectinload(Team.owners))
        .where(Team.league_season_id == league_season.id)
        .order_by(Team.espn_team_id)
    ).all()
    return [
        TeamOut(
            espn_team_id=team.espn_team_id,
            name=team.name,
            abbreviation=team.abbreviation,
            division_name=team.division_name,
            logo_url=team.logo_url,
            owners=[
                OwnerOut(
                    espn_owner_id=o.espn_owner_id,
                    display_name=o.display_name,
                    first_name=o.first_name,
                )
                for o in team.owners
            ],
        )
        for team in teams
    ]


@router.get(
    "/leagues/{league_id}/seasons/{season}/standings",
    summary="Matchup records, derived, alongside ESPN's category tallies",
)
def get_standings(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(
        default=False, description="Include playoff matchups in the derived record"
    ),
) -> list[StandingOut]:
    """Derive each team's matchup record by counting winners.

    ESPN reports no matchup record, only category tallies, so this is the
    only place the two appear side by side. Byes are excluded: an
    unopposed matchup is not a win.
    """
    teams = session.scalars(select(Team).where(Team.league_season_id == league_season.id)).all()
    record = {team.id: [0, 0, 0] for team in teams}  # won, lost, tied

    query = (
        select(Matchup)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            Matchup.away_team_id.is_not(None),
        )
    )
    if not include_playoffs:
        query = query.where(MatchupPeriod.is_playoff.is_(False))

    for matchup in session.scalars(query).all():
        home, away = matchup.home_team_id, matchup.away_team_id
        if away is None or home not in record or away not in record:
            continue
        if matchup.winner == "HOME":
            record[home][0] += 1
            record[away][1] += 1
        elif matchup.winner == "AWAY":
            record[away][0] += 1
            record[home][1] += 1
        elif matchup.winner == "TIE":
            record[home][2] += 1
            record[away][2] += 1

    standings = [
        StandingOut(
            espn_team_id=team.espn_team_id,
            name=team.name,
            final_standing=team.final_standing,
            matchups_won=record[team.id][0],
            matchups_lost=record[team.id][1],
            matchups_tied=record[team.id][2],
            categories_won=team.categories_won,
            categories_lost=team.categories_lost,
            categories_tied=team.categories_tied,
        )
        for team in teams
    ]
    standings.sort(key=lambda s: (-s.matchups_won, s.matchups_lost, -s.categories_won))
    return standings


@router.get(
    "/leagues/{league_id}/seasons/{season}/periods",
    summary="Matchup periods and the days each covers",
)
def list_periods(league_season: LeagueSeasonDep, session: SessionDep) -> list[MatchupPeriodOut]:
    rows = session.execute(
        select(MatchupPeriod, func.count(Matchup.id))
        .outerjoin(Matchup, Matchup.matchup_period_id == MatchupPeriod.id)
        .where(MatchupPeriod.league_season_id == league_season.id)
        .group_by(MatchupPeriod.id)
        .order_by(MatchupPeriod.period)
    ).all()
    return [
        MatchupPeriodOut(
            period=period.period,
            is_playoff=period.is_playoff,
            first_scoring_period=period.first_scoring_period,
            final_scoring_period=period.final_scoring_period,
            matchup_count=count,
        )
        for period, count in rows
    ]


def _side(session: SessionDep, matchup: Matchup, team: Team, categories_won: int) -> MatchupSideOut:
    stats = session.scalars(
        select(MatchupTeamStat)
        .where(MatchupTeamStat.matchup_id == matchup.id, MatchupTeamStat.team_id == team.id)
        .order_by(MatchupTeamStat.abbreviation)
    ).all()
    return MatchupSideOut(
        espn_team_id=team.espn_team_id,
        name=team.name,
        categories_won=categories_won,
        statistics=[
            MatchupCategoryOut(
                abbreviation=s.abbreviation,
                value=s.value,
                result=s.result,
                # The league's category list is the reliable test, not `result`,
                # which is null on both sides of a bye.
                is_scored_category=s.league_season_category_id is not None,
            )
            for s in stats
        ],
    )


def _matchup_out(session: SessionDep, matchup: Matchup, teams: dict[int, Team]) -> MatchupOut:
    home = teams[matchup.home_team_id]
    away = teams.get(matchup.away_team_id) if matchup.away_team_id else None
    return MatchupOut(
        id=matchup.id,
        period=matchup.matchup_period.period,
        is_playoff=matchup.matchup_period.is_playoff,
        winner=matchup.winner,
        categories_tied=matchup.categories_tied,
        home=_side(session, matchup, home, matchup.home_categories_won),
        away=_side(session, matchup, away, matchup.home_categories_lost) if away else None,
    )


@router.get(
    "/leagues/{league_id}/seasons/{season}/matchups",
    summary="Matchups with per-category detail for both sides",
)
def list_matchups(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    period: int | None = Query(default=None, description="Restrict to one matchup period"),
    limit: int = Query(default=MATCHUP_PAGE_LIMIT, ge=1, le=MATCHUP_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> Page[MatchupOut]:
    teams = {
        team.id: team
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }

    base = (
        select(Matchup)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(MatchupPeriod.league_season_id == league_season.id)
    )
    if period is not None:
        base = base.where(MatchupPeriod.period == period)

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    matchups = session.scalars(
        base.options(selectinload(Matchup.matchup_period))
        .order_by(MatchupPeriod.period, Matchup.id)
        .limit(limit)
        .offset(offset)
    ).all()

    return Page(
        items=[_matchup_out(session, m, teams) for m in matchups if m.home_team_id in teams],
        total=total,
        limit=limit,
        offset=offset,
    )
