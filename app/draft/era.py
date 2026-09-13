"""How much the game itself has moved, separately from the league.

A target read from a sixteen team season is the right shape but the wrong
year. The 2023 season is the only sixteen team one on record and it is four
years from the 2027 draft, over which the NBA has kept scoring more.

The measure here is deliberately independent of fantasy league structure:
the per-game output of established starters, players with at least forty
games and twenty-eight minutes a night. That population exists in every
season regardless of how many fantasy teams shared it, so it isolates the
game from the roster dilution that `targets` already handles.

Not every category drifts, and scaling by a trend that is really noise
would be worse than leaving it alone. Fitting each category across eight
seasons:

| category | change per year | r squared |
|---|---|---|
| PTS | +1.02% | 0.84 |
| AST | +1.30% | 0.78 |
| 3PM | +1.59% | 0.54 |
| BLK | +0.79% | 0.11 |
| REB | -0.52% | 0.20 |
| STL, TO | flat | 0.02, 0.01 |

So points, assists and threes move and the rest do not. Only a fit above
`MEANINGFUL_FIT` is allowed to adjust anything.
"""

from dataclasses import dataclass
from statistics import fmean

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import PlayerGameStat
from app.draft.valuation import PERCENTAGE_COMPONENTS

#: An established starter: enough of a season, and enough of a role, to
#: represent the game rather than the fringe of a roster.
MIN_GAMES = 40
MIN_MINUTES = 28.0

#: How well a straight line has to fit before a trend is treated as real.
#: Below this the year to year movement is noise, and scaling a target by
#: noise is worse than not scaling it at all.
MEANINGFUL_FIT = 0.5

#: Category to the column holding it on a game line.
_COUNTING_COLUMNS = {
    "PTS": PlayerGameStat.points,
    "REB": PlayerGameStat.rebounds,
    "AST": PlayerGameStat.assists,
    "STL": PlayerGameStat.steals,
    "BLK": PlayerGameStat.blocks,
    "TO": PlayerGameStat.turnovers,
    "3PM": PlayerGameStat.three_pointers_made,
}

_PERCENTAGE_COLUMNS = {
    "FGM": PlayerGameStat.field_goals_made,
    "FGA": PlayerGameStat.field_goals_attempted,
    "FTM": PlayerGameStat.free_throws_made,
    "FTA": PlayerGameStat.free_throws_attempted,
}


@dataclass(frozen=True)
class CategoryTrend:
    """How one category has moved across the seasons on record."""

    abbreviation: str
    #: Fractional change a year, so 0.0102 is a little over one percent.
    change_per_year: float
    r_squared: float
    index: dict[int, float]

    @property
    def is_meaningful(self) -> bool:
        return self.r_squared >= MEANINGFUL_FIT

    def scale(self, from_season: int, to_season: int) -> float:
        """What to multiply a `from_season` figure by to reach `to_season`.

        Both ends are read off the fitted line rather than the raw seasons.
        A single noisy year on either end would otherwise drag the whole
        adjustment with it, which matters here because one end is usually a
        season with no data yet.

        Returns 1.0 whenever the trend is not worth trusting, so a caller
        can apply this unconditionally.
        """
        if not self.is_meaningful or not self.index:
            return 1.0
        seasons = sorted(self.index)
        mean_season = fmean(seasons)
        mean_value = fmean(self.index[s] for s in seasons)
        slope = self.change_per_year * mean_value

        start = mean_value + slope * (from_season - mean_season)
        end = mean_value + slope * (to_season - mean_season)
        return end / start if start else 1.0


def _starter_seasons(session: Session) -> list[int]:
    return sorted(
        {int(season) for season in session.scalars(select(PlayerGameStat.season).distinct()).all()}
    )


def _established_starters(session: Session, season: int) -> list[int]:
    """Player ids with a real role that season."""
    rows = session.execute(
        select(PlayerGameStat.player_id)
        .where(PlayerGameStat.season == season, PlayerGameStat.played.is_(True))
        .group_by(PlayerGameStat.player_id)
        .having(func.count() >= MIN_GAMES)
        .having(func.avg(cast(PlayerGameStat.minutes, Float)) >= MIN_MINUTES)
    ).all()
    return [int(player_id) for (player_id,) in rows]


def _counting_index(session: Session, season: int, abbreviation: str) -> float | None:
    """Mean per-game output of that season's established starters."""
    starters = _established_starters(session, season)
    if not starters:
        return None
    column = _COUNTING_COLUMNS[abbreviation]
    per_player = (
        select(func.avg(cast(column, Float)).label("per_game"))
        .where(
            PlayerGameStat.season == season,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.player_id.in_(starters),
        )
        .group_by(PlayerGameStat.player_id)
        .subquery()
    )
    value = session.scalar(select(func.avg(per_player.c.per_game)))
    return float(value) if value is not None else None


def _rate_index(session: Session, season: int, abbreviation: str) -> float | None:
    """Pooled shooting rate of that season's established starters."""
    starters = _established_starters(session, season)
    if not starters:
        return None
    made_key, attempted_key = PERCENTAGE_COMPONENTS[abbreviation]
    made, attempted = session.execute(
        select(
            func.sum(cast(_PERCENTAGE_COLUMNS[made_key], Float)),
            func.sum(cast(_PERCENTAGE_COLUMNS[attempted_key], Float)),
        ).where(
            PlayerGameStat.season == season,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.player_id.in_(starters),
        )
    ).one()
    if not attempted:
        return None
    return float(made) / float(attempted)


def _fit(index: dict[int, float]) -> tuple[float, float]:
    """Least squares slope as a fraction of the mean, and how well it fits."""
    if len(index) < 3:
        return 0.0, 0.0
    seasons = sorted(index)
    values = [index[s] for s in seasons]
    mean_season, mean_value = fmean(seasons), fmean(values)
    variance = sum((s - mean_season) ** 2 for s in seasons)
    if not variance or not mean_value:
        return 0.0, 0.0

    slope = sum((s - mean_season) * (v - mean_value) for s, v in zip(seasons, values, strict=True))
    slope /= variance
    predicted = [mean_value + slope * (s - mean_season) for s in seasons]
    residual = sum((v - p) ** 2 for v, p in zip(values, predicted, strict=True))
    total = sum((v - mean_value) ** 2 for v in values)
    r_squared = 1.0 - residual / total if total else 0.0
    return slope / mean_value, r_squared


def category_trends(session: Session, categories: list[str]) -> dict[str, CategoryTrend]:
    """How each category has moved, measured free of league structure."""
    seasons = _starter_seasons(session)
    trends: dict[str, CategoryTrend] = {}

    for abbreviation in categories:
        if abbreviation in PERCENTAGE_COMPONENTS:
            reader = _rate_index
        elif abbreviation in _COUNTING_COLUMNS:
            reader = _counting_index
        else:
            continue

        index = {}
        for season in seasons:
            value = reader(session, season, abbreviation)
            if value is not None:
                index[season] = value

        change, fit = _fit(index)
        trends[abbreviation] = CategoryTrend(
            abbreviation=abbreviation,
            change_per_year=change,
            r_squared=fit,
            index=index,
        )
    return trends
