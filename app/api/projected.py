"""The projected standings: the whole league's, and one team's slice of it.

Two routes over `app.inseason.projected`, which holds the whole derivation:

    GET /leagues/{league_id}/seasons/{season}/projected
    GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/projected

THE SCOPES, AND WHY THEY DIFFER

The league route is a member's, like the standings and This week it is drawn
on. Nothing in it is a plan: it is every team's remaining schedule, which is
on the Standings page already, run through a model. Any member may see where
the league is heading, and a projection only one manager could see would be
worth less to everyone.

The team route is that team's manager and the paid tier (`TEAM_PLAN`), the
same scope as the Week page it is drawn on. It carries nothing the league
route does not, and exists so the Week page can fetch one team's weeks and
finish distribution rather than fourteen teams' -- which on a phone is the
difference between a page and a download.

STORED, OR BUILT

The morning's `project_standings` job builds the league's projection once and
stores it (`league_reports`, app/reports.py); both routes answer from the
stored row when the day asked for is today's and the row was built today, and
build live otherwise, exactly as the pickup routes do. Nothing here writes a
row, so a route never races the job.

`today` is a scoring period; left out it is the calendar day turned into one
through the stored NBA schedule, resolved by the same helpers the pickup
routes use, so a page and the report it draws are always about the same day.

A TOOL, NOT GOSPEL

Every answer carries `calibration_note`
(`app.inseason.projected_calibration`), which is what this forecast scored
when 2026 was replayed against it, and `source_note` and `basis`, which are
where the numbers came from. There is no verdict word anywhere in the
payload: it is chances, records and the reasons for them.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from app import reports
from app.api.access import LEAGUE_MEMBER, TEAM_PLAN
from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.pickups import TODAY, TodayQuery, _asked_day, _day, readiness, readiness_out
from app.api.schemas import ProjectedOut, ProjectedTeamOut, ProjectedWeekOut
from app.db.models import LeagueSeason
from app.inseason.projected import Projection, TeamOutlook, Week, project_standings
from app.inseason.projected_calibration import CALIBRATION_NOTE, SHORT_NOTE
from app.pickups.state import SeasonCalendar

router = APIRouter(tags=["projected"])


def build_projected(
    session: SessionDep, league_season: LeagueSeasonDep, day: int
) -> dict[str, Any]:
    """The league's projection, built live, as the JSON its route answers.

    The morning job stores exactly this, so a stored answer and a live one
    cannot drift apart. Raises `ValueError` when `day` is in no matchup
    period, which is the season being over or not yet begun.
    """
    return _out(project_standings(session, league_season, day)).model_dump(mode="json")


def _week_out(week: Week) -> ProjectedWeekOut:
    return ProjectedWeekOut(
        period=week.period,
        first_scoring_period=week.first_scoring_period,
        final_scoring_period=week.final_scoring_period,
        days_remaining=week.days_remaining,
        in_play=week.in_play,
        opponent_espn_team_id=week.opponent_team_id,
        opponent_name=week.opponent_name,
        probabilities=dict(week.probabilities),
        expected_wins=week.expected_wins,
        projected=dict(week.projected.counts),
        opponent_projected=dict(week.opponent_projected.counts),
    )


def _team_out(team: TeamOutlook) -> ProjectedTeamOut:
    expected = team.expected
    return ProjectedTeamOut(
        espn_team_id=team.team_id,
        name=team.name,
        banked_won=team.banked[0],
        banked_lost=team.banked[1],
        banked_matchups=list(team.banked_matchups),
        expected_won=expected[0],
        expected_lost=expected[1],
        projected_record=list(team.projected_record),
        projected_matchups=list(team.projected_matchups),
        weeks=[_week_out(week) for week in team.weeks],
        finishes=list(team.finishes),
        playoff_odds=team.playoff_odds,
        bye_odds=team.bye_odds,
    )


def _out(report: Projection) -> ProjectedOut:
    return ProjectedOut(
        league_id=report.league_id,
        season=report.season,
        as_of=report.as_of,
        as_of_date=report.as_of_date,
        matchup_period=report.matchup_period,
        teams=[_team_out(team) for team in report.teams],
        periods=list(report.periods),
        playoff_team_count=report.playoff_team_count,
        bye_count=report.bye_count,
        playoffs_projected=report.playoffs_projected,
        playoff_note=report.playoff_note,
        tiebreak=report.tiebreak,
        n_sims=report.n_sims,
        seed=report.seed,
        source_note=report.source_note,
        basis=report.basis,
        calibration_note=CALIBRATION_NOTE,
        calibration_short=SHORT_NOTE,
    )


def not_ready(
    session: Session,
    league_season: LeagueSeason,
    calendar: SeasonCalendar | None,
    today: int | None,
    missing: list[str],
) -> ProjectedOut:
    """The answer for a season that cannot be projected: `readiness`, the day
    it was asked about, and no teams, no weeks, no odds.

    `calibration_note` is kept: it is this method's published record, true of
    any season, and a page that prints it under an empty table is saying
    nothing false. Everything the run would have produced is empty.
    """
    day = _asked_day(calendar, today)
    return ProjectedOut(
        readiness=readiness_out(session, league_season, missing),
        league_id=int(league_season.league.espn_league_id),
        season=int(league_season.season),
        as_of=day,
        as_of_date=calendar.date_of(day) if calendar is not None and day else None,
        calibration_note=CALIBRATION_NOTE,
        calibration_short=SHORT_NOTE,
    )


def _projection(
    session: SessionDep, league_season: LeagueSeasonDep, today: int | None
) -> ProjectedOut:
    """The projection asked for: the stored one when it is fresh, else built.

    "Fresh" is the day asked for being today's and the row having been built
    today, which is `app.reports.fresh_league` and the rule the pickup routes
    use. Nothing here writes a row, so a reader never races the morning job;
    a build takes a few seconds for a fourteen-team league
    (docs/projected_record.md, "Timing").
    """
    calendar, missing = readiness(session, league_season)
    if missing or calendar is None:
        return not_ready(session, league_season, calendar, today, missing)
    day = _day(calendar, today)
    on = TODAY()
    if day == calendar.scoring_period_on(on):
        row = reports.fresh_league(session, league_season.id, reports.PROJECTED, day, on=on)
        if row is not None:
            # `calibration_short` is a constant of this build, not something the
            # run worked out, so a row stored before the field existed still
            # gets the sentence rather than an empty line on the page.
            return ProjectedOut.model_validate(
                {"calibration_short": SHORT_NOTE, **dict(row.payload), "stored": True}
            )
    try:
        body = build_projected(session, league_season, day)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ProjectedOut.model_validate(body)


@router.get(
    "/leagues/{league_id}/seasons/{season}/projected",
    summary="Where every team is heading: each remaining week, the record, the odds",
    dependencies=[LEAGUE_MEMBER],
)
def projected_standings(
    league_season: LeagueSeasonDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> ProjectedOut:
    """Every team's remaining matchups, projected head to head, summed onto
    what is banked, with the odds of each finishing place.

    A forecast and its record together: `calibration_note` is what the same
    method scored when 2026 was replayed against it, and it is not
    flattering. Read the chances as directions rather than quantities.
    """
    return _projection(session, league_season, today)


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/projected",
    summary="One team's rest of season: its weeks, its record, where it finishes",
    dependencies=[TEAM_PLAN],
)
def projected_team(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> ProjectedOut:
    """The league projection narrowed to one team, for the Week page.

    The same payload with one team in `teams`, so the page reads one shape
    whichever route it came from and the two can never disagree: the slice is
    taken from the league answer rather than computed again.
    """
    whole = _projection(session, league_season, today)
    if whole.readiness is not None:
        return whole
    espn_team_id = int(team.espn_team_id)
    mine = [one for one in whole.teams if one.espn_team_id == espn_team_id]
    if not mine:
        raise HTTPException(
            status_code=409, detail=f"team {espn_team_id} is not in the {whole.season} projection"
        )
    return whole.model_copy(update={"teams": mine})
