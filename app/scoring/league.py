"""The league-average team, and which categories a team gave up on.

AVERAGE TEAM

`average_team_line` is what the league's teams posted in a typical
regular-season matchup period of the season's usual length: the mean of each
count in `matchup_team_stats`, the made and attempted shots included, so a
percentage rebuilt from it is the league's pooled rate. It is the "league
standard" roster of the spec: one player's value against it is comparable
across teams, where his value to his own team depends on that team.

PUNTS ARE INFERRED, NEVER ASSUMED

A category counts as punted when a team won it in fewer than
`PUNT_THRESHOLD` of its contested regular-season matchups (a tie counts
half). Nobody declares a punt, so this reads the result, and the reports say
"looks like a punt" rather than claiming intent.

Why a quarter. Measured 2026-09-16 over all 882 team-category-seasons
2019-2026: the 10th percentile of category win rate is 0.237 and the 5th
0.167; the distribution has no gap to cut at, so the line is a judgement.
A quarter is the draft optimizer's line for a conceded category
(`app.draft.optimizer.CONCEDE_THRESHOLD`), so the room and the report agree
on what giving up a category means. And it is not chance: over a 19-week
season a team with an even chance in a category wins four or fewer about
1% of the time. It flags Brighton Bears' threes in 2026 (0.184) and The
Infirmary's assists (0.237), the two the manager named.

What it cannot tell apart: a punt from a weak team. In 2026 Brockley Heat and
Ben's Need Some VC each flag four categories, which reads as a losing season
more than a plan. Consumers that write commentary should say so when a team
flags three or more.
"""

from __future__ import annotations

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Team,
)
from app.draft.optimizer import CONCEDE_THRESHOLD
from app.draft.targets import _period_length, modal_period_days
from app.scoring.lines import COUNTS, CategoryLine

#: A category won in fewer matchups than this share is read as punted.
PUNT_THRESHOLD = CONCEDE_THRESHOLD


def _league_season(session: Session, season: int) -> LeagueSeason:
    found = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
    if found is None:
        raise ValueError(f"no season {season}")
    return found


def average_team_line(session: Session, season: int) -> CategoryLine:
    """The mean team line of a regular-season period of the usual length."""
    league_season = _league_season(session, season)
    days = modal_period_days(session, [season])
    rows = session.execute(
        select(MatchupTeamStat.abbreviation, func.avg(cast(MatchupTeamStat.value, Float)))
        .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
            MatchupTeamStat.abbreviation.in_(list(COUNTS)),
            _period_length == days,
        )
        .group_by(MatchupTeamStat.abbreviation)
    ).all()
    return CategoryLine({str(a): float(v) for a, v in rows if v is not None})


def category_record(session: Session, season: int, team_id: int) -> dict[str, float]:
    """The share of contested regular-season matchups won in each category."""
    league_season = _league_season(session, season)
    won = case(
        (MatchupTeamStat.result == "WIN", 1.0),
        (MatchupTeamStat.result == "TIE", 0.5),
        else_=0.0,
    )
    rows = session.execute(
        select(MatchupTeamStat.abbreviation, func.avg(won))
        .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .join(Team, Team.id == MatchupTeamStat.team_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
            MatchupTeamStat.team_id == team_id,
            MatchupTeamStat.league_season_category_id.is_not(None),
            MatchupTeamStat.result.is_not(None),
        )
        .group_by(MatchupTeamStat.abbreviation)
    ).all()
    return {str(a): float(rate) for a, rate in rows}


def punts(session: Session, season: int, team_id: int) -> dict[str, bool]:
    """Each category, and whether the team's record in it looks like a punt."""
    return {
        category: rate < PUNT_THRESHOLD
        for category, rate in category_record(session, season, team_id).items()
    }
