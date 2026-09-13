"""How much of a season a player actually gives you.

Three things were measured before anything was modelled, and together they
decide what an injury model can honestly be.

**ESPN is optimistic.** Across seven seasons with projections, players
delivered about 87% of their projected games. Excluding the 2020 COVID
season the figure is steady between 0.84 and 0.93.

**It does not persist.** A player's availability one season correlates with
the next at 0.014, across 658 player seasons. After a year under 70%
availability the next year averages 0.839; after a year above 95% it
averages 0.844. Injury proneness, as a usable draft signal, is not there.

**It does not vary by quality.** Split by projected points, availability
runs 0.867, 0.872, 0.879, 0.881 from best quartile to worst, against a
spread of 0.21. Stars are no more fragile than filler.

So nobody can be singled out from history. What follows is that a league
wide factor is the honest default, and that such a factor is *scale
invariant in z-space*: multiply everyone's totals by 0.87 and every z-score,
every ranking and every price comes back identical. An injury adjustment
only moves anything when it differs between players.

Two places it legitimately does:

* **Known injuries at draft time.** A player already ruled out for two
  months has a knowable availability that the league average does not
  describe. That has to come from a live status, not from history.
* **Absolute production against targets.** Targets are real totals, and a
  roster built on raw projections will land about 13% short of them.

A warning about the stored history: `daily_lineup_slots.injury_status`
cannot be used for any of this. ESPN returns a player's status *as of the
request*, so every one of the 351 players in 2026 carries a single status
across all 160 days. Trae Young reads OUT on days he played 30 minutes.
It is a snapshot smeared over a season, not a time series.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import PlayerSeasonStat
from app.draft.projections import UNUSABLE_PROJECTIONS
from app.draft.valuation import PlayerProjection

#: Seasons excluded from the league-wide figure. 2019-20 was suspended in
#: March and resumed as a bubble for some teams only, which puts its 0.710
#: far outside every other season and describes nothing repeatable.
#: Seasons whose projections are not forecasts, from the shared registry.
#: 2023 was only ever excluded here by accident -- its snapshot projections
#: all fall under MIN_PROJECTED_GAMES -- and an accident is not a rule.
DISTORTED_SEASONS = tuple(sorted(UNUSABLE_PROJECTIONS))

#: Players need a real projected workload before their availability says
#: anything; a player projected for ten games who plays five is noise.
MIN_PROJECTED_GAMES = 50.0

#: Fallback when there is no history to measure, close to what eight seasons
#: of this league actually show.
DEFAULT_AVAILABILITY = 0.87


@dataclass(frozen=True)
class Availability:
    """What a season's worth of projected games is really worth."""

    factor: float
    seasons: tuple[int, ...]
    players: int

    @property
    def shortfall(self) -> float:
        """The fraction of projected production that does not arrive."""
        return 1.0 - self.factor


def measured_availability(session: Session) -> Availability:
    """Delivered games over projected games, across every usable season."""
    projected = PlayerSeasonStat.__table__.alias("projected")
    actual = PlayerSeasonStat.__table__.alias("actual")

    rows = session.execute(
        select(
            projected.c.season,
            func.sum(cast(actual.c.games_played, Float)),
            func.sum(cast(projected.c.games_played, Float)),
            func.count(),
        )
        .select_from(
            projected.join(
                actual,
                (actual.c.player_id == projected.c.player_id)
                & (actual.c.season == projected.c.season)
                & (actual.c.kind == "total"),
            )
        )
        .where(
            projected.c.kind == "projected",
            projected.c.games_played >= MIN_PROJECTED_GAMES,
            projected.c.season.not_in(DISTORTED_SEASONS),
        )
        .group_by(projected.c.season)
    ).all()

    usable = [(int(s), float(a or 0), float(p or 0), int(n)) for s, a, p, n in rows if p]
    if not usable:
        return Availability(factor=DEFAULT_AVAILABILITY, seasons=(), players=0)

    delivered = sum(a for _, a, _, _ in usable)
    promised = sum(p for _, _, p, _ in usable)
    return Availability(
        factor=delivered / promised if promised else DEFAULT_AVAILABILITY,
        seasons=tuple(sorted(season for season, _, _, _ in usable)),
        players=sum(n for _, _, _, n in usable),
    )


def apply_availability(
    projections: Sequence[PlayerProjection],
    factor_for: Callable[[PlayerProjection], float] | float,
) -> list[PlayerProjection]:
    """Scale projected totals by how much of the season is expected.

    Passing a single number scales everyone alike, which is a deliberate
    no-op in z-space and exists so a caller can be explicit about assuming
    nothing. Pass a callable to price a player who is already hurt, which is
    the case where this actually changes a board.

    Games are scaled too, so a caller can see the expectation rather than
    ESPN's.
    """
    resolve = factor_for if callable(factor_for) else (lambda _p: float(factor_for))

    adjusted: list[PlayerProjection] = []
    for projection in projections:
        factor = max(0.0, resolve(projection))
        adjusted.append(
            PlayerProjection(
                player_id=projection.player_id,
                name=projection.name,
                games=projection.games * factor,
                totals={key: value * factor for key, value in projection.totals.items()},
                eligible=projection.eligible,
                position=projection.position,
            )
        )
    return adjusted
