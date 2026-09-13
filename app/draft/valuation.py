"""Turning projections into comparable player value.

Standard nine-category z-scoring, with the two details that decide whether
it is any use:

* **Turnovers are inverted.** Fewer is better, and ESPN awards the category
  to the lower total, which was confirmed against real box scores rather
  than assumed from the `isReverseItem` flag, which is false for turnovers.
* **Percentages are weighted by volume.** A player shooting 90% on two free
  throws a game moves nothing. The contribution is the made shots above
  what the pool would have made on the same attempts, so both accuracy and
  volume count, and a high-volume poor shooter scores negative.

Value is computed on season totals rather than per-game rates. In a
head-to-head league a player only helps on the nights they play, so games
missed are a real cost and totals carry that for free. A rate-based view
would rank a brilliant forty game season above a good seventy game one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev

#: Counting categories where more is better.
COUNTING_CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "3PM")

#: Categories where fewer is better. ESPN reports `isReverseItem` as false
#: for turnovers, which is wrong; the box scores award the category to the
#: lower total.
INVERTED_CATEGORIES = ("TO",)

#: Percentage categories, mapped to the made and attempted totals behind
#: them. A percentage on its own cannot be averaged or z-scored honestly.
PERCENTAGE_COMPONENTS = {
    "FG%": ("FGM", "FGA"),
    "FT%": ("FTM", "FTA"),
}

#: Roster size used to size the valuation pool when the caller gives none.
#: Thirteen is what this league has drafted every season.
DEFAULT_ROSTER_SLOTS = 13


@dataclass(frozen=True)
class PlayerProjection:
    """One player's projected season, as totals."""

    player_id: int
    name: str
    games: float
    totals: dict[str, float]

    def get(self, abbreviation: str) -> float:
        value = self.totals.get(abbreviation)
        return float(value) if value is not None else 0.0


@dataclass(frozen=True)
class CategoryValue:
    """What a player is worth in one category.

    `raw` is their own figure, in the category's own units. `value` is that
    expressed in standard deviations above the pool, always signed so more
    is better, including for turnovers.
    """

    abbreviation: str
    raw: float
    value: float


@dataclass(frozen=True)
class PlayerValue:
    player_id: int
    name: str
    total: float
    categories: tuple[CategoryValue, ...]

    def category(self, abbreviation: str) -> CategoryValue | None:
        return next((c for c in self.categories if c.abbreviation == abbreviation), None)


def _percentage_impact(
    projections: Sequence[PlayerProjection], made_key: str, attempted_key: str
) -> dict[int, float]:
    """Made shots above what the pool would have made on the same attempts.

    Equivalent to `attempts x (player rate - pool rate)`, written without the
    division so a player with no attempts contributes exactly zero rather
    than dividing by it.
    """
    pool_made = sum(p.get(made_key) for p in projections)
    pool_attempted = sum(p.get(attempted_key) for p in projections)
    pool_rate = pool_made / pool_attempted if pool_attempted else 0.0
    return {p.player_id: p.get(made_key) - p.get(attempted_key) * pool_rate for p in projections}


def _z_scores(values: dict[int, float]) -> dict[int, float]:
    """Standard scores. A pool with no spread scores everyone at zero."""
    if not values:
        return {}
    numbers = list(values.values())
    mean = fmean(numbers)
    spread = pstdev(numbers)
    if spread == 0:
        return dict.fromkeys(values, 0.0)
    return {key: (value - mean) / spread for key, value in values.items()}


def _category_scores(
    projections: Sequence[PlayerProjection], abbreviation: str
) -> dict[int, float]:
    """Every player's standing in one category, signed so more is better."""
    if abbreviation in PERCENTAGE_COMPONENTS:
        made_key, attempted_key = PERCENTAGE_COMPONENTS[abbreviation]
        return _z_scores(_percentage_impact(projections, made_key, attempted_key))

    raw = {p.player_id: p.get(abbreviation) for p in projections}
    scores = _z_scores(raw)
    if abbreviation in INVERTED_CATEGORIES:
        return {key: -value for key, value in scores.items()}
    return scores


def _raw_figure(projection: PlayerProjection, abbreviation: str) -> float:
    """The player's own number, in the units the category is reported in."""
    if abbreviation in PERCENTAGE_COMPONENTS:
        made_key, attempted_key = PERCENTAGE_COMPONENTS[abbreviation]
        attempted = projection.get(attempted_key)
        return projection.get(made_key) / attempted if attempted else 0.0
    return projection.get(abbreviation)


def value_players(
    projections: Sequence[PlayerProjection],
    categories: Sequence[str],
    *,
    pool_size: int | None = None,
    refine: bool = True,
) -> list[PlayerValue]:
    """Value every player against the pool that will actually be drafted.

    The pool matters more than it looks. Scoring against all projected
    players drags the mean down with people nobody will roster, which
    flatters replacement-level players and understates stars. So the first
    pass ranks everyone, and the second re-scores against only the top
    `pool_size`, which is the standard fix.

    `refine=False` skips the second pass, which is useful when the caller
    has already narrowed the pool themselves.
    """
    if not projections or not categories:
        return []

    ranked = _value_against(projections, projections, categories)
    if not refine or pool_size is None or pool_size >= len(projections):
        return ranked

    keep = {value.player_id for value in ranked[:pool_size]}
    pool = [p for p in projections if p.player_id in keep]
    return _value_against(projections, pool, categories)


def _value_against(
    projections: Sequence[PlayerProjection],
    pool: Sequence[PlayerProjection],
    categories: Sequence[str],
) -> list[PlayerValue]:
    """Value everyone, with the pool setting the mean and spread."""
    by_category = {
        abbreviation: _category_scores(pool, abbreviation) for abbreviation in categories
    }

    # A player outside the pool still needs a score, measured on the pool's
    # own scale rather than left out of the board entirely.
    pool_ids = {p.player_id for p in pool}
    outside = [p for p in projections if p.player_id not in pool_ids]
    if outside:
        for abbreviation in categories:
            combined = _category_scores([*pool, *outside], abbreviation)
            for player in outside:
                by_category[abbreviation].setdefault(
                    player.player_id, combined.get(player.player_id, 0.0)
                )

    values: list[PlayerValue] = []
    for projection in projections:
        scores = tuple(
            CategoryValue(
                abbreviation=abbreviation,
                raw=_raw_figure(projection, abbreviation),
                value=by_category[abbreviation].get(projection.player_id, 0.0),
            )
            for abbreviation in categories
        )
        values.append(
            PlayerValue(
                player_id=projection.player_id,
                name=projection.name,
                total=sum(score.value for score in scores),
                categories=scores,
            )
        )

    values.sort(key=lambda value: value.total, reverse=True)
    return values
