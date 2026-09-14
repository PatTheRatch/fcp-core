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
categories are read from seasons of the same size, and rate categories
from the most recent seasons of any size.

A SIZE THE LEAGUE HAS NEVER PLAYED

The 2027 league has fifteen teams, a size with no history. Borrowing the
nearest size raw (fourteen) overstated what a fifteen-team opponent posts,
because every extra team thins every roster. Fitting each counting category's
weekly mean on league size and season together, across the played seasons
except 2020, the effect is steady and the same in every category:

| category | per extra team |
|---|---|
| PTS, REB | -3.8% |
| TO, 3PM, BLK | -4.3% to -4.4% |
| AST, STL | -4.7%, -4.9% |

So a counting distribution borrowed from another size is scaled by that
fitted effect for the difference (`size_scale`). Against the raw fourteen-team
borrow, 2027's opponent came down 1-8% by category, and it moved ceilings:
Mobley $30 to $16, Gobert $16 to $9, measured on the 2027 BBM pool.

RATES ARE NOT BROUGHT FORWARD

Rates used to pool every season and then take the NBA-wide trend, which is
measured on established starters (`app.draft.era`). That put 2027's opponent
field-goal percentage at .486, above any season the league has posted (.462
to .483, with no trend of its own: +0.0006 a year). Fantasy rosters select
their shooters, and the league's own series is flat. Rates now read the most
recent `RECENT_RATE_SEASONS` played seasons and are not era scaled.
"""

import math
from dataclasses import dataclass
from statistics import fmean

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
)
from app.draft.era import category_trends
from app.draft.valuation import INVERTED_CATEGORIES, PERCENTAGE_COMPONENTS

#: Categories whose value is a rate, so league size does not move them and
#: every season can be pooled. Measured, not assumed: see the module docstring.
RATE_CATEGORIES = tuple(PERCENTAGE_COMPONENTS)

#: How many of the most recent played seasons a rate category reads.
RECENT_RATE_SEASONS = 3

#: Seasons left out of the league-size fit: 2020 was cut short by COVID and
#: its weeks are not comparable.
SIZE_FIT_EXCLUDED = (2020,)


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
    #: What the raw historical figure was multiplied by to bring it forward
    #: to this season. Exactly 1.0 where the category does not really drift,
    #: so a reader can see which targets were adjusted and which were not.
    era_scale: float
    #: What a counting figure borrowed from another league size was
    #: multiplied by for the difference. 1.0 when the size matched.
    size_scale: float = 1.0


def _seasons_with_results(session: Session, *, before: int | None = None) -> list[tuple[int, int]]:
    """(team count, season) for every season that actually has results.

    A season with no contested matchups yet is useless as a basis, and the
    season being drafted for is always one of those: it exists, it has a
    size, and it has not been played. Excluding it here is what stops a
    brand new season matching its own size and finding nothing.

    `before` keeps only seasons strictly earlier than the one given. A
    backtest replaying a season that has since been played needs this,
    or the opponents it is measured against would include the very results
    it is trying to predict.
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
        if before is None or int(season) < before
    ]


def _sized_seasons(
    session: Session, team_count: int, *, before: int | None = None
) -> tuple[int, list[int]]:
    """Played seasons at this league size, or the nearest size that exists.

    Returns the size actually used alongside its seasons, so a caller can
    see when a target is borrowed from a different sized league rather than
    assuming it matched.
    """
    rows = _seasons_with_results(session, before=before)
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


@dataclass(frozen=True)
class CategoryDistribution:
    """What opponents post in one category, as a whole distribution.

    A target answers "what do I need to win this often". The optimizer needs
    the reverse, "how often does this total win", and for that it needs the
    shape rather than one point on it. Mean and spread together give a
    probability of winning for any total, which is what gets maximised.
    """

    abbreviation: str
    mean: float
    spread: float
    lower_is_better: bool
    sample: int
    basis_seasons: tuple[int, ...]
    period_days: int
    era_scale: float
    size_scale: float = 1.0

    def win_probability(self, total: float) -> float:
        """How often a roster posting `total` beats the opponent.

        Normal on the opponent's distribution. For turnovers the sign flips,
        since posting less than the opponent is the win.
        """
        if self.spread <= 0:
            return 0.5
        z = (total - self.mean) / self.spread
        if self.lower_is_better:
            z = -z
        return _normal_cdf(z)


def _normal_cdf(z: float) -> float:
    """Standard normal CDF, via the error function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _moments(
    session: Session,
    abbreviation: str,
    seasons: list[int],
    period_days: int,
) -> tuple[float | None, float | None, int]:
    """Mean, standard deviation and sample of one category's posted totals."""
    if not seasons:
        return None, None, 0

    row = session.execute(
        select(
            func.avg(cast(MatchupTeamStat.value, Float)),
            func.stddev_pop(cast(MatchupTeamStat.value, Float)),
            func.count(),
        )
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
    ).one()
    mean, spread, sample = row
    return (
        float(mean) if mean is not None else None,
        float(spread) if spread is not None else None,
        int(sample or 0),
    )


def _season_means(
    session: Session, abbreviation: str, seasons: list[int], period_days: int
) -> list[tuple[int, int, float]]:
    """(season, team count, mean weekly total) for each season given."""
    rows = session.execute(
        select(
            LeagueSeason.season,
            LeagueSeason.team_count,
            func.avg(cast(MatchupTeamStat.value, Float)),
        )
        .join(MatchupPeriod, MatchupPeriod.league_season_id == LeagueSeason.id)
        .join(Matchup, Matchup.matchup_period_id == MatchupPeriod.id)
        .join(MatchupTeamStat, MatchupTeamStat.matchup_id == Matchup.id)
        .where(
            MatchupTeamStat.abbreviation == abbreviation,
            MatchupTeamStat.league_season_category_id.is_not(None),
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
            LeagueSeason.season.in_(seasons),
            _period_length == period_days,
        )
        .group_by(LeagueSeason.season, LeagueSeason.team_count)
    ).all()
    return [(int(a), int(b), float(c)) for a, b, c in rows if c is not None and float(c) > 0]


def size_effect(session: Session, abbreviation: str, seasons: list[int], period_days: int) -> float:
    """A counting category's fractional change in log weekly mean per extra team.

    Fitted on season means as log(mean) = a + b * teams + c * season, so the
    game's own drift is not mistaken for league size. Falls back to a fit on
    size alone when the seasons cannot separate the two, and to zero -- no
    adjustment -- when fewer than two sizes have been played.
    """
    points = [
        (season, teams, math.log(mean))
        for season, teams, mean in _season_means(session, abbreviation, seasons, period_days)
        if season not in SIZE_FIT_EXCLUDED
    ]
    if len({teams for _, teams, _ in points}) < 2:
        return 0.0
    if len(points) >= 4:
        slope = _fit_two(points)
        if slope is not None:
            return slope
    xs = [float(teams) for _, teams, _ in points]
    ys = [value for _, _, value in points]
    mean_x, mean_y = fmean(xs), fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx


def _fit_two(points: list[tuple[int, int, float]]) -> float | None:
    """The team-count coefficient of y = a + b*teams + c*season, or None if singular."""
    xs1 = [float(t) for _, t, _ in points]
    xs2 = [float(s) for s, _, _ in points]
    ys = [y for _, _, y in points]
    m1, m2, my = fmean(xs1), fmean(xs2), fmean(ys)
    s11 = sum((a - m1) ** 2 for a in xs1)
    s22 = sum((b - m2) ** 2 for b in xs2)
    s12 = sum((a - m1) * (b - m2) for a, b in zip(xs1, xs2, strict=True))
    s1y = sum((a - m1) * (y - my) for a, y in zip(xs1, ys, strict=True))
    s2y = sum((b - m2) * (y - my) for b, y in zip(xs2, ys, strict=True))
    determinant = s11 * s22 - s12 * s12
    if abs(determinant) < 1e-9:
        return None
    return (s1y * s22 - s2y * s12) / determinant


def category_distributions(
    session: Session,
    league_season: LeagueSeason,
    *,
    period_days: int | None = None,
    adjust_for_era: bool = True,
    before: int | None = None,
) -> list[CategoryDistribution]:
    """Every scored category's opponent distribution, on the same basis as
    `category_targets`: same league size, same period length, same era
    adjustment. Both mean and spread are scaled, since a category that
    drifts up drifts its whole distribution.

    `before` restricts the basis to seasons strictly earlier than the one
    given, for a backtest of a season that has since been played. The era
    trend is not restricted by it; a backtest that wants no hindsight at
    all passes `adjust_for_era=False` as well.
    """
    categories = session.scalars(
        select(LeagueSeasonCategory)
        .where(LeagueSeasonCategory.league_season_id == league_season.id)
        .order_by(LeagueSeasonCategory.position)
    ).all()
    target_size = int(league_season.team_count)
    sized_count, sized_seasons = _sized_seasons(session, target_size, before=before)
    all_seasons = sorted({s for _, s in _seasons_with_results(session, before=before)})
    recent_seasons = all_seasons[-RECENT_RATE_SEASONS:]
    days = period_days if period_days is not None else modal_period_days(session, all_seasons)
    trends = (
        category_trends(session, [c.abbreviation for c in categories]) if adjust_for_era else {}
    )

    out: list[CategoryDistribution] = []
    for category in categories:
        pooled = category.abbreviation in RATE_CATEGORIES
        seasons = recent_seasons if pooled else sized_seasons
        mean, spread, sample = _moments(session, category.abbreviation, seasons, days)
        if mean is None or spread is None:
            continue

        scale = 1.0
        trend = trends.get(category.abbreviation)
        if trend is not None and seasons and not pooled:
            scale = trend.scale(round(fmean(seasons)), int(league_season.season))

        sized = 1.0
        if not pooled and sized_count != target_size:
            effect = size_effect(session, category.abbreviation, all_seasons, days)
            sized = math.exp(effect * (target_size - sized_count))

        out.append(
            CategoryDistribution(
                abbreviation=category.abbreviation,
                mean=mean * scale * sized,
                spread=spread * scale * sized,
                lower_is_better=category.abbreviation in INVERTED_CATEGORIES,
                sample=sample,
                basis_seasons=tuple(seasons),
                period_days=days,
                era_scale=scale,
                size_scale=sized,
            )
        )
    return out


def category_targets(
    session: Session,
    league_season: LeagueSeason,
    *,
    win_probability: float = 0.5,
    period_days: int | None = None,
    adjust_for_era: bool = True,
) -> list[CategoryTarget]:
    """What to aim for in each scored category, to win it that often.

    A target is the percentile of opposing totals you have to clear, so
    `win_probability=0.5` is the median opponent and 0.75 is a category you
    intend to win most weeks.

    `period_days` defaults to the ordinary week. Pass 14 to ask what the
    All-Star fortnight demands, which is a different and much larger number.

    Figures are brought forward to this season where the category genuinely
    drifts. The only sixteen team season on record is 2023, and the game has
    kept scoring since, so a 2027 target read from it raw would be too low.
    Categories whose year to year movement is noise are left alone; see
    `app.draft.era`.
    """
    if not 0.0 < win_probability < 1.0:
        raise ValueError("win_probability must sit strictly between 0 and 1")

    categories = session.scalars(
        select(LeagueSeasonCategory)
        .where(LeagueSeasonCategory.league_season_id == league_season.id)
        .order_by(LeagueSeasonCategory.position)
    ).all()

    target_size = int(league_season.team_count)
    sized_count, sized_seasons = _sized_seasons(session, target_size)
    # Rates read the most recent seasons actually played. A season with no
    # results contributes nothing and must not widen the basis.
    all_seasons = sorted({season for _, season in _seasons_with_results(session)})
    recent_seasons = all_seasons[-RECENT_RATE_SEASONS:]

    days = period_days if period_days is not None else modal_period_days(session, all_seasons)
    abbreviations = [category.abbreviation for category in categories]
    trends = category_trends(session, abbreviations) if adjust_for_era else {}

    targets: list[CategoryTarget] = []
    for category in categories:
        lower_is_better = category.abbreviation in INVERTED_CATEGORIES
        # Beating the opponent means clearing their total, or staying under
        # it when fewer is better, so the percentile flips with the category.
        fraction = 1.0 - win_probability if lower_is_better else win_probability

        pooled = category.abbreviation in RATE_CATEGORIES
        seasons = recent_seasons if pooled else sized_seasons
        value, sample = _percentile(session, category.abbreviation, seasons, fraction, days)
        if value is None:
            continue

        # Bring the figure forward from the middle of the seasons it came
        # from to the season being asked about. Categories whose year to
        # year movement is noise come back with a scale of exactly 1.0, and
        # rates are never brought forward (see the module docstring).
        scale = 1.0
        trend = trends.get(category.abbreviation)
        if trend is not None and seasons and not pooled:
            scale = trend.scale(round(fmean(seasons)), int(league_season.season))
            value *= scale

        sized = 1.0
        if not pooled and sized_count != target_size:
            effect = size_effect(session, category.abbreviation, all_seasons, days)
            sized = math.exp(effect * (target_size - sized_count))
            value *= sized

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
                era_scale=scale,
                size_scale=sized,
            )
        )
    return targets
