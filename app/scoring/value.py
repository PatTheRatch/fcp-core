"""The currency: expected categories won in a matchup period.

A line's value is how many categories it would win, on average, against the
opponent a team actually faced that season: for each category, the chance
its total beats a draw from what the league's teams posted (a normal on that
season's mean and spread), summed. A player's value to a team is the team's
figure with his line minus without it. So a category the team is far behind
or far ahead in moves nothing, and a close one moves the most, which is what
the nine-cat composite could never say.

The arithmetic is the draft optimizer's (`app.draft.optimizer.score`, the
normal-CDF model on `CategoryDistribution`); this wraps it rather than
copying it. One deliberate difference: the optimizer charges a penalty for
conceding a category, a draft-planning rule measured on whole rosters. A
grade asks what a player's line did, so `expected_wins` sums the
probabilities without that charge.

WHICH OPPONENT

For grading a played season, the opponent is that season's own teams: the
mean and spread of what they posted, in periods of the same length as the one
being graded, regular season only. Not the draft room's basis, which pools
seasons of the same league size and brings them forward for a season not yet
played. Period length matters because the All-Star fortnight posts about a
third more of everything (`app.draft.targets`), so a fortnight line is scored
against fortnights.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, LeagueSeasonCategory, MatchupPeriod
from app.draft.optimizer import score
from app.draft.targets import CategoryDistribution, _moments
from app.draft.valuation import INVERTED_CATEGORIES
from app.scoring.lines import CategoryLine


def expected_wins(
    line: CategoryLine,
    distributions: Sequence[CategoryDistribution],
    punt: frozenset[str] = frozenset(),
) -> float:
    """Expected categories won by `line`, without the draft's concede charge."""
    totals = line.totals([d.abbreviation for d in distributions])
    _, probabilities = score(totals, distributions, punt)
    return sum(p for category, p in probabilities.items() if category not in punt)


def category_wins(
    line: CategoryLine, distributions: Sequence[CategoryDistribution]
) -> dict[str, float]:
    """The win probability in each category."""
    totals = line.totals([d.abbreviation for d in distributions])
    return score(totals, distributions)[1]


def marginal(
    team_line: CategoryLine,
    player_line: CategoryLine,
    distributions: Sequence[CategoryDistribution],
    punt: frozenset[str] = frozenset(),
) -> float:
    """What `player_line` adds to `team_line`, which already includes it.

    With minus without: the team's expected wins, less the expected wins of
    the same team with the player's counts taken out. Percentages are rebuilt
    from the remaining makes and attempts, so a high-volume poor shooter can
    be worth less than nothing in FG%, and turnovers count against.
    """
    return expected_wins(team_line, distributions, punt) - expected_wins(
        team_line - player_line, distributions, punt
    )


def per_category_marginal(
    team_line: CategoryLine,
    player_line: CategoryLine,
    distributions: Sequence[CategoryDistribution],
) -> dict[str, float]:
    """`marginal`, broken out by category."""
    with_him = category_wins(team_line, distributions)
    without = category_wins(team_line - player_line, distributions)
    return {c: with_him[c] - without[c] for c in with_him}


@dataclass
class SeasonOpponents:
    """A played season's opponent distributions, one set per period length."""

    session: Session
    league_season: LeagueSeason
    _cache: dict[int, list[CategoryDistribution]] = field(default_factory=dict)

    def for_days(self, period_days: int) -> list[CategoryDistribution]:
        """What teams posted in regular-season periods of this many days.

        A length the regular season never used (a playoff round of an odd
        length) falls back to the nearest length it did use.
        """
        if period_days not in self._cache:
            self._cache[period_days] = self._build(period_days)
        return self._cache[period_days]

    def for_period(self, period: MatchupPeriod) -> list[CategoryDistribution]:
        return self.for_days(period_length(period))

    def _build(self, period_days: int) -> list[CategoryDistribution]:
        season = int(self.league_season.season)
        categories = self.session.scalars(
            select(LeagueSeasonCategory.abbreviation)
            .where(LeagueSeasonCategory.league_season_id == self.league_season.id)
            .order_by(LeagueSeasonCategory.position)
        ).all()
        days = self._nearest_length(period_days)
        out = []
        for abbreviation in categories:
            mean, spread, sample = _moments(self.session, abbreviation, [season], days)
            if mean is None or spread is None:
                continue
            out.append(
                CategoryDistribution(
                    abbreviation=abbreviation,
                    mean=mean,
                    spread=spread,
                    lower_is_better=abbreviation in INVERTED_CATEGORIES,
                    sample=sample,
                    basis_seasons=(season,),
                    period_days=days,
                    era_scale=1.0,
                )
            )
        return out

    def _nearest_length(self, period_days: int) -> int:
        lengths = {
            period_length(p)
            for p in self.session.scalars(
                select(MatchupPeriod).where(
                    MatchupPeriod.league_season_id == self.league_season.id,
                    MatchupPeriod.is_playoff.is_(False),
                )
            )
        }
        lengths.discard(0)
        if not lengths or period_days in lengths:
            return period_days
        return min(lengths, key=lambda n: (abs(n - period_days), n))


def period_length(period: MatchupPeriod) -> int:
    """Days in a matchup period, inclusive; 0 when the window is unknown."""
    if period.first_scoring_period is None or period.final_scoring_period is None:
        return 0
    return int(period.final_scoring_period) - int(period.first_scoring_period) + 1
