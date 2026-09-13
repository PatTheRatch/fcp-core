"""Choosing a roster.

The objective is not total value. A head-to-head category league is won by
winning categories, and a category won by a hair counts the same as one won
by a mile, so piling value into a category already won is wasted. What gets
maximised is the **expected number of categories won per week**: for each
category, the probability that the roster's weekly total beats what an
opponent posts, summed across categories. That probability comes from the
measured opponent distribution in `targets`, so it is this league's, at this
size, in this era.

Two measured facts make the roster arithmetic simple. Managers start 98.4%
of their roster's production, because daily lineups with three utility
slots leave almost nobody on the bench who has a game, so a roster's weekly
total is just the sum of everyone on it. And ESPN's projections run about
13% optimistic on games, so each player's season is scaled by the measured
availability before being spread across the season's matchup periods.

The solver is a greedy seed followed by swap improvement: fill the roster by
value per dollar, then keep making the single swap that most raises expected
wins until none does. It is not guaranteed optimal, and it is fast, needs no
dependency, handles the non-linear objective directly, and is easy to read.
If it ever proves too weak the objective is already in the right shape for
a proper solver.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.draft.market import PriceBoard
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PERCENTAGE_COMPONENTS, PlayerProjection

#: Stat keys a roster total needs: the counting categories, and the shooting
#: components that the percentages are rebuilt from.
_COMPONENT_KEYS = tuple(key for pair in PERCENTAGE_COMPONENTS.values() for key in pair)


@dataclass(frozen=True)
class Candidate:
    """A player as the optimizer sees them: a price, and a weekly line."""

    player_id: int
    name: str
    price: int
    #: Expected contribution per matchup period, by stat key.
    weekly: dict[str, float]


@dataclass(frozen=True)
class RosterPlan:
    players: tuple[Candidate, ...]
    cost: int
    #: The roster's weekly line, with percentages rebuilt from components.
    totals: dict[str, float]
    #: Probability of winning each category in a given week.
    win_probability: dict[str, float]
    expected_wins: float

    @property
    def player_ids(self) -> frozenset[int]:
        return frozenset(p.player_id for p in self.players)


def candidates_from(
    projections: Sequence[PlayerProjection],
    board: PriceBoard,
    *,
    periods: int,
    availability: float,
    keys: Iterable[str],
) -> list[Candidate]:
    """Turn season projections and a price board into weekly candidates.

    A player without a price on the board is not a candidate: the market
    model already decided they will not be rostered.
    """
    wanted = tuple(keys)
    divisor = max(1, periods)
    out: list[Candidate] = []
    for projection in projections:
        price = board.price_of(projection.player_id)
        if price is None:
            continue
        weekly = {
            key: projection.get(key) * availability / divisor for key in (*wanted, *_COMPONENT_KEYS)
        }
        out.append(
            Candidate(
                player_id=projection.player_id,
                name=projection.name,
                price=price,
                weekly=weekly,
            )
        )
    return out


def roster_totals(players: Iterable[Candidate], categories: Sequence[str]) -> dict[str, float]:
    """Sum the roster's weekly lines, rebuilding percentages from components."""
    summed: dict[str, float] = {}
    for player in players:
        for key, value in player.weekly.items():
            summed[key] = summed.get(key, 0.0) + value

    totals: dict[str, float] = {}
    for category in categories:
        if category in PERCENTAGE_COMPONENTS:
            made_key, attempted_key = PERCENTAGE_COMPONENTS[category]
            attempted = summed.get(attempted_key, 0.0)
            totals[category] = summed.get(made_key, 0.0) / attempted if attempted else 0.0
        else:
            totals[category] = summed.get(category, 0.0)
    return totals


def score(
    totals: dict[str, float],
    distributions: Sequence[CategoryDistribution],
    punt: frozenset[str] = frozenset(),
) -> tuple[float, dict[str, float]]:
    """Expected categories won, and the per-category probabilities.

    A punted category is left out of the sum, which is what punting means:
    the roster stops paying for it and the optimizer stops chasing it.
    """
    probabilities: dict[str, float] = {}
    expected = 0.0
    for distribution in distributions:
        probability = distribution.win_probability(totals.get(distribution.abbreviation, 0.0))
        probabilities[distribution.abbreviation] = probability
        if distribution.abbreviation not in punt:
            expected += probability
    return expected, probabilities


def _plan(
    players: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    punt: frozenset[str],
) -> RosterPlan:
    categories = [d.abbreviation for d in distributions]
    totals = roster_totals(players, categories)
    expected, probabilities = score(totals, distributions, punt)
    return RosterPlan(
        players=tuple(players),
        cost=sum(p.price for p in players),
        totals=totals,
        win_probability=probabilities,
        expected_wins=expected,
    )


def optimize(
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    budget: int,
    roster_slots: int,
    punt: Iterable[str] = (),
    locked: Iterable[int] = (),
    excluded: Iterable[int] = (),
    minimum_bid: int = 1,
) -> RosterPlan:
    """The roster that maximises expected weekly category wins under a budget.

    `locked` players are kept whatever happens, which is how a live draft
    room feeds back what has already been bought. `excluded` players are
    gone to someone else. Both exist so the same optimizer serves the plan
    before the draft and the re-solve during it.
    """
    punted = frozenset(punt)
    keep = frozenset(locked)
    gone = frozenset(excluded)
    pool = [c for c in candidates if c.player_id not in gone]
    by_id = {c.player_id: c for c in pool}

    roster: list[Candidate] = [by_id[pid] for pid in keep if pid in by_id]
    if len(roster) > roster_slots:
        roster = roster[:roster_slots]

    # Greedy seed by value per dollar, always leaving enough for the floor
    # bids on whatever slots remain.
    def worth(candidate: Candidate) -> float:
        value = sum(candidate.weekly.get(k, 0.0) for k in candidate.weekly)
        return value / max(1, candidate.price)

    chosen = {c.player_id for c in roster}
    spent = sum(c.price for c in roster)
    for candidate in sorted(pool, key=worth, reverse=True):
        if len(roster) >= roster_slots:
            break
        if candidate.player_id in chosen:
            continue
        slots_after = roster_slots - len(roster) - 1
        if spent + candidate.price + slots_after * minimum_bid > budget:
            continue
        roster.append(candidate)
        chosen.add(candidate.player_id)
        spent += candidate.price

    if not roster:
        return _plan([], distributions, punted)

    # Swap improvement: the single change that most raises expected wins,
    # repeated until nothing does.
    best = _plan(roster, distributions, punted)
    while True:
        improved: RosterPlan | None = None
        for index, outgoing in enumerate(best.players):
            if outgoing.player_id in keep:
                continue
            budget_left = budget - (best.cost - outgoing.price)
            for incoming in pool:
                if incoming.player_id in chosen or incoming.price > budget_left:
                    continue
                trial = list(best.players)
                trial[index] = incoming
                plan = _plan(trial, distributions, punted)
                if plan.expected_wins > (improved or best).expected_wins + 1e-9:
                    improved = plan
        if improved is None:
            return best
        chosen = set(improved.player_ids)
        best = improved
