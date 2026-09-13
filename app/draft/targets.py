"""What it actually takes to win a category in this league.

Read from eight seasons of real results rather than simulated. To win a
category you have to beat the team opposite, so a target is simply a
percentile of what opponents post: clear the median and you win about half
the time.

Two things cannot be pooled. The first is **matchup period length**. Most
periods are a week, but every season has one fortnight over the All-Star
break, and it posts about a third more of everything: 794 median points
against 607. There is also a short opening week of six days at 502. Mixing
them compares unlike things, and because the fortnight sits in the upper
tail it inflates exactly the targets a manager cares about, by 1% at the
75th percentile and 2.3% at the 90th. Targets are therefore read from
periods of one length, the most common one by default.

Dividing by days would be wrong rather than helpful: the All-Star fortnight
has fourteen days but nowhere near fourteen days of basketball, which is
why it is up a third rather than double.

The second is league size. Counting categories
fall about a fifth between a ten team league and a sixteen team one, because
sixteen rosters share the same pool of players and each one is thinner:

| category | 10 teams | 16 teams |
|---|---|---|
| PTS | 657 | 534 |
| REB | 241 | 193 |
| BLK | 26.3 | 20.2 |

The two percentages do not move at all, 0.473 against 0.477 for field goals,
because a rate does not care how many players produced it. So counting
categories are read only from seasons of the same size, and rate categories
from every season, which gives them a far larger sample for free.
"""

from dataclasses import dataclass

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
)
from app.draft.valuation import INVERTED_CATEGORIES, PERCENTAGE_COMPONENTS

#: Categories whose value is a rate, so league size does not move them and
#: every season can be pooled. Measured, not assumed: see the module docstring.
RATE_CATEGORIES = tuple(PERCENTAGE_COMPONENTS)


@dataclass(frozen=True)
class CategoryTarget:
    """What to post in one category, and how often that wins.

    For turnovers `lower_is_better` is true and the target is a ceiling:
    stay under it. For everything else it is a floor.
    """

    abbreviation: str
    target: float
    win_probability: float
    lower_is_better: bool
    #: How many opposing totals the target was read from.
    sample: int
    #: The league size the counting figure came from, and the seasons used.
    #: Worth surfacing: a target drawn from one season of the right size is
    #: a different claim from one drawn from five.
    basis_team_count: int
    basis_seasons: tuple[int, ...]
    #: The matchup period length this target describes, in scoring periods.
    #: A week for the usual target; 14 asks about the All-Star fortnight.
    period_days: int


def _seasons_with_results(session: Session) -> list[tuple[int, int]]:
    """(team count, season) for every season that actually has results.

    A season with no contested matchups yet is useless as a basis, and the
    season being drafted for is always one of those: it exists, it has a
    size, and it has not been played. Excluding it here is what stops a
    brand new season matching its own size and finding nothing.
    """
    return [
        (int(size), int(season))
        for size, season in session.execute(
            select(LeagueSeason.team_count, LeagueSeason.season)
            .join(MatchupPeriod, MatchupPeriod.league_season_id == LeagueSeason.id)
            .join(Matchup, Matchup.matchup_period_id == MatchupPeriod.id)
            .join(MatchupTeamStat, MatchupTeamStat.matchup_id == Matchup.id)
            .where(
                MatchupPeriod.is_playoff.is_(False),
                Matchup.away_team_id.is_not(None),
                MatchupTeamStat.league_season_category_id.is_not(None),
            )
            .group_by(LeagueSeason.team_count, LeagueSeason.season)
            .order_by(LeagueSeason.season)
        ).all()
    ]


def _sized_seasons(session: Session, team_count: int) -> tuple[int, list[int]]:
    """Played seasons at this league size, or the nearest size that exists.

    Returns the size actually used alongside its seasons, so a caller can
    see when a target is borrowed from a different sized league rather than
    assuming it matched.
    """
    rows = _seasons_with_results(session)
    if not rows:
        return team_count, []

    sizes = {size for size, _ in rows}
    nearest = min(sizes, key=lambda size: (abs(size - team_count), size))
    return nearest, [season for size, season in rows if size == nearest]


#: A matchup period's length in scoring periods, as a SQL expression.
#: Lower case deliberately. Named as a constant, Ruff reads a comparison
#: against it as a yoda condition and swaps the operands, which puts a
#: plain int on the left and quietly turns a SQL clause into a Python bool.
_period_length = cast(
    MatchupPeriod.final_scoring_period - MatchupPeriod.first_scoring_period + 1,
    Integer,
)


def modal_period_days(session: Session, seasons: list[int]) -> int:
    """The most common matchup period length, which is the ordinary week.

    Taken from the data rather than assumed to be seven: a league that moved
    to a different schedule should not silently keep answering about weeks.
    """
    if not seasons:
        return 7
    row = session.execute(
        select(_period_length.label("days"), func.count())
        .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
        .where(
            MatchupPeriod.is_playoff.is_(False),
            MatchupPeriod.first_scoring_period.is_not(None),
            LeagueSeason.season.in_(seasons),
        )
        .group_by(_period_length)
        .order_by(func.count().desc())
        .limit(1)
    ).first()
    return int(row[0]) if row and row[0] else 7


def _percentile(
    session: Session,
    abbreviation: str,
    seasons: list[int],
    fraction: float,
    period_days: int,
) -> tuple[float | None, int]:
    """One category's percentile across the given seasons, with its sample.

    Restricted to contested regular season matchups of one period length.
    Byes have no opponent to beat, the playoffs are a different bracket, and
    a fortnight is not a week.
    """
    if not seasons:
        return None, 0

    base = (
        select(MatchupTeamStat.value)
        .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
        .where(
            MatchupTeamStat.abbreviation == abbreviation,
            MatchupTeamStat.league_season_category_id.is_not(None),
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
            LeagueSeason.season.in_(seasons),
            _period_length == period_days,
        )
        .subquery()
    )

    row = session.execute(
        select(
            func.percentile_cont(fraction).within_group(cast(base.c.value, Float)),
            func.count(),
        )
    ).one()
    value, sample = row
    return (float(value) if value is not None else None), int(sample or 0)


def category_targets(
    session: Session,
    league_season: LeagueSeason,
    *,
    win_probability: float = 0.5,
    period_days: int | None = None,
) -> list[CategoryTarget]:
    """What to aim for in each scored category, to win it that often.

    A target is the percentile of opposing totals you have to clear, so
    `win_probability=0.5` is the median opponent and 0.75 is a category you
    intend to win most weeks.

    `period_days` defaults to the ordinary week. Pass 14 to ask what the
    All-Star fortnight demands, which is a different and much larger number.
    """
    if not 0.0 < win_probability < 1.0:
        raise ValueError("win_probability must sit strictly between 0 and 1")

    categories = session.scalars(
        select(LeagueSeasonCategory)
        .where(LeagueSeasonCategory.league_season_id == league_season.id)
        .order_by(LeagueSeasonCategory.position)
    ).all()

    sized_count, sized_seasons = _sized_seasons(session, int(league_season.team_count))
    all_seasons = [int(season) for season in session.scalars(select(LeagueSeason.season)).all()]

    days = period_days if period_days is not None else modal_period_days(session, all_seasons)

    targets: list[CategoryTarget] = []
    for category in categories:
        lower_is_better = category.abbreviation in INVERTED_CATEGORIES
        # Beating the opponent means clearing their total, or staying under
        # it when fewer is better, so the percentile flips with the category.
        fraction = 1.0 - win_probability if lower_is_better else win_probability

        pooled = category.abbreviation in RATE_CATEGORIES
        seasons = all_seasons if pooled else sized_seasons
        value, sample = _percentile(session, category.abbreviation, seasons, fraction, days)
        if value is None:
            continue

        targets.append(
            CategoryTarget(
                abbreviation=category.abbreviation,
                target=value,
                win_probability=win_probability,
                lower_is_better=lower_is_better,
                sample=sample,
                basis_team_count=0 if pooled else sized_count,
                basis_seasons=tuple(seasons),
                period_days=days,
            )
        )
    return targets
