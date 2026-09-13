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

The solver is swap improvement from several starting rosters: fill a roster,
then keep making the single swap that most raises expected wins until none
does, and keep the best of the runs. A single greedy start by value per
dollar turned out to be a trap. Against 2026 it reached 5.95 expected wins
while every one of twelve random starts did better, the best by 0.30, so
the search now begins from the greedy roster and a fixed set of shuffled
ones. Not guaranteed optimal; fast, dependency-free, handles the non-linear
objective directly, and deterministic for a given seed.
"""

import random
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


#: Shuffled starting rosters tried in addition to the greedy one. Twelve was
#: enough for the best run to appear repeatedly against 2026; each costs
#: well under a second.
DEFAULT_RESTARTS = 12


def _greedy(
    ordered: Sequence[Candidate],
    *,
    keep: Sequence[Candidate],
    roster_slots: int,
    budget: int,
    minimum_bid: int,
) -> list[Candidate]:
    """Fill a roster in the given order, always leaving the floor bids.

    A shuffled order can take an expensive player early and then find
    nothing affordable in the rest of its sequence, which would leave the
    roster short. So after the ordered pass, any open slot is filled with
    the cheapest remaining player who fits. A short roster is never a valid
    start: it would score its missing slots as zero and could still win the
    comparison on the strength of one star.
    """
    roster = list(keep)[:roster_slots]
    chosen = {c.player_id for c in roster}
    spent = sum(c.price for c in roster)
    # The floor is what the cheapest remaining players actually cost, not a
    # nominal minimum bid. Reserving $1 a slot when the cheapest man left
    # costs $3 is how a roster ends up short with money unspent.
    ascending = sorted((c.price for c in ordered), reverse=False)

    def floor_for(open_slots: int) -> int:
        cheapest = ascending[:open_slots]
        return max(sum(cheapest), open_slots * minimum_bid)

    def try_add(candidate: Candidate) -> None:
        nonlocal spent
        if len(roster) >= roster_slots or candidate.player_id in chosen:
            return
        slots_after = roster_slots - len(roster) - 1
        if spent + candidate.price + floor_for(slots_after) > budget:
            return
        roster.append(candidate)
        chosen.add(candidate.player_id)
        spent += candidate.price

    for candidate in ordered:
        try_add(candidate)
    for candidate in sorted(ordered, key=lambda c: c.price):
        try_add(candidate)
    return roster


def _swap_improve(
    roster: Sequence[Candidate],
    pool: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    punt: frozenset[str],
    keep: frozenset[int],
    budget: int,
) -> RosterPlan:
    """Repeat the single best swap until no swap raises expected wins."""
    best = _plan(roster, distributions, punt)
    chosen = set(best.player_ids)
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
                plan = _plan(trial, distributions, punt)
                if plan.expected_wins > (improved or best).expected_wins + 1e-9:
                    improved = plan
        if improved is None:
            return best
        best = improved
        chosen = set(best.player_ids)


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
    restarts: int = DEFAULT_RESTARTS,
    seed: int = 0,
) -> RosterPlan:
    """The roster that maximises expected weekly category wins under a budget.

    `locked` players are kept whatever happens, which is how a live draft
    room feeds back what has already been bought. `excluded` players are
    gone to someone else. Both exist so the same optimizer serves the plan
    before the draft and the re-solve during it.

    `restarts` shuffled starts are tried beside the greedy one and the best
    result kept. `seed` fixes the shuffles, so the same inputs always give
    the same roster.
    """
    punted = frozenset(punt)
    keep = frozenset(locked)
    gone = frozenset(excluded)
    pool = [c for c in candidates if c.player_id not in gone]
    by_id = {c.player_id: c for c in pool}
    kept = [by_id[pid] for pid in keep if pid in by_id]
    if not pool:
        return _plan(kept[:roster_slots], distributions, punted)

    def worth(candidate: Candidate) -> float:
        return sum(candidate.weekly.values()) / max(1, candidate.price)

    starts: list[list[Candidate]] = [sorted(pool, key=worth, reverse=True)]
    shuffler = random.Random(seed)
    for _ in range(max(0, restarts)):
        shuffled = pool[:]
        shuffler.shuffle(shuffled)
        starts.append(shuffled)

    # A start has to fill the roster. One that could not is never compared:
    # it would score its empty slots as nothing and could still win on the
    # strength of a single star, which is not a roster anyone can field.
    required = min(roster_slots, len(pool) + len(kept))

    best: RosterPlan | None = None
    for ordered in starts:
        roster = _greedy(
            ordered, keep=kept, roster_slots=roster_slots, budget=budget, minimum_bid=minimum_bid
        )
        if len(roster) < required:
            continue
        plan = _swap_improve(roster, pool, distributions, punt=punted, keep=keep, budget=budget)
        if best is None or plan.expected_wins > best.expected_wins + 1e-9:
            best = plan
    return best if best is not None else _plan([], distributions, punted)
