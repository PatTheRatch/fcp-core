"""Leagues, seasons, teams, standings and matchups.

Routes are keyed on the identifiers ESPN uses rather than on surrogate
database ids, so a URL can be constructed from what a user already knows:
their league id and a year.
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.accounts import member_league_ids
from app.api.access import LEAGUE_MEMBER, CurrentUser
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
from app.scoring.ranking import Record, category_meetings, lookup, matchup_records, ranking_rule

router = APIRouter(tags=["leagues"])

#: Bounded by a season's length, so listing every matchup is safe by default.
MATCHUP_PAGE_LIMIT = 200


@router.get("/leagues", summary="The viewer's leagues, with the seasons held for each")
def list_leagues(session: SessionDep, viewer: CurrentUser) -> list[LeagueOut]:
    """Every league the viewer is a member of; in single mode, every league stored.

    The one league route with no league in its path, so its check is a
    filter rather than a refusal: signed in, and only his own leagues listed.
    """
    query = select(League).options(selectinload(League.seasons)).order_by(League.espn_league_id)
    if not viewer.all_access:
        mine = member_league_ids(session, viewer.user_id) if viewer.user_id is not None else set()
        query = query.where(League.espn_league_id.in_(mine))
    leagues = session.scalars(query).all()
    return [
        LeagueOut(
            espn_league_id=league.espn_league_id,
            # The newest season's name, for the site's league switcher.
            name=max(league.seasons, key=lambda s: s.season).name if league.seasons else None,
            seasons=sorted(season.season for season in league.seasons),
        )
        for league in leagues
    ]


@router.get(
    "/leagues/{league_id}/seasons",
    summary="Seasons stored for one league",
    dependencies=[LEAGUE_MEMBER],
)
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
    dependencies=[LEAGUE_MEMBER],
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


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams",
    summary="Teams and their owners",
    dependencies=[LEAGUE_MEMBER],
)
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
                    owner_id=o.id,
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
    summary="The table, in the league's own order: categories first in a category league",
    dependencies=[LEAGUE_MEMBER],
)
def get_standings(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    include_playoffs: bool = Query(
        default=False, description="Include playoff matchups in the derived matchup record"
    ),
) -> list[StandingOut]:
    """Every team's record, ordered by the league's ranking rule.

    The rule is `app.scoring.ranking.ranking_rule`, read off the scoring
    type: a head-to-head each-category league -- this one -- is ranked on
    category win share, with its tiebreaks in `order_note`. The category
    record is ESPN's own. The matchup record is counted from the stored
    winners, because ESPN reports none for a category league; it stays in
    the payload as a figure, and orders the table only in a league ranked on
    matchups. Byes are excluded: an unopposed matchup is not a win.

    For a season whose regular season is over, ESPN has published its own
    table (`teams.standing`), and that is the order returned: the rule does
    not overrule it. Any place where the rule would say otherwise carries a
    `place_note` -- 2023's second and third, where ESPN seeded a division
    leader first. A season in play is ordered by the rule. A league this code
    does not rank (rotisserie) is a 409 with the reason.
    """
    try:
        rule = ranking_rule(league_season)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    teams = sorted(
        session.scalars(select(Team).where(Team.league_season_id == league_season.id)).all(),
        key=lambda team: team.espn_team_id,
    )
    matchups = matchup_records(session, league_season, include_playoffs=include_playoffs)
    records = [
        Record(
            team.id,
            team.categories_won,
            team.categories_lost,
            team.categories_tied,
            *matchups.get(team.id, (0, 0, 0)),
        )
        for team in teams
    ]
    meetings = lookup(category_meetings(session, league_season))
    by_rule = [record.team for record in rule.order(records, meetings)]
    rule_place = {team_id: place for place, team_id in enumerate(by_rule, start=1)}
    by_row = {team.id: team for team in teams}
    published = _published(session, league_season, teams)
    order = (
        [team.id for team in sorted(teams, key=lambda team: team.standing or 0)]
        if published
        else by_rule
    )
    divisions = len({team.division_id for team in teams if team.division_id is not None})
    record_of = {record.team: record for record in records}

    standings: list[StandingOut] = []
    for place, team_id in enumerate(order, start=1):
        team = by_row[team_id]
        won, lost, tied = matchups.get(team.id, (0, 0, 0))
        note = None
        if published and rule_place[team.id] != place:
            note = (
                f"ESPN's published table puts {team.name} {_nth(place)}; "
                f"{rule.words.split(',')[0]} puts it {_nth(rule_place[team.id])}"
                + (
                    f". The season had {divisions} divisions, and ESPN seeds the division "
                    "leaders first"
                    if divisions > 1
                    else ""
                )
            )
        standings.append(
            StandingOut(
                espn_team_id=team.espn_team_id,
                name=team.name,
                final_standing=team.final_standing,
                matchups_won=won,
                matchups_lost=lost,
                matchups_tied=tied,
                categories_won=team.categories_won,
                categories_lost=team.categories_lost,
                categories_tied=team.categories_tied,
                unit=rule.unit,
                share=record_of[team.id].share,
                order_note=(
                    "ESPN's own published table; by the league's rule: " + rule.words
                    if published
                    else rule.words
                ),
                place=place,
                rule_place=rule_place[team.id],
                standing=team.standing if published else None,
                place_note=note,
            )
        )
    return standings


def _published(session: SessionDep, league_season: LeagueSeason, teams: list[Team]) -> bool:
    """True when the regular season is over and ESPN has published its table.

    Every team carries a standing, and the season has regular-season
    matchups, none of them still undecided. While the season is being played
    ESPN's `standing` moves daily and is not stored as it moves, so the rule
    orders the table instead.
    """
    if not teams or any(not team.standing for team in teams):
        return False
    rows = session.execute(
        select(Matchup.winner)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(False),
        )
    ).all()
    return bool(rows) and all(str(winner) in ("HOME", "AWAY", "TIE") for (winner,) in rows)


def _nth(place: int) -> str:
    suffix = "th" if 10 <= place % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(place % 10, "th")
    return f"{place}{suffix}"


@router.get(
    "/leagues/{league_id}/seasons/{season}/periods",
    summary="Matchup periods and the days each covers",
    dependencies=[LEAGUE_MEMBER],
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
    dependencies=[LEAGUE_MEMBER],
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
