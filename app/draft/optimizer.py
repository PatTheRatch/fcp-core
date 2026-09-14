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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import erf, sqrt

from app.draft.lineup import DEFAULT_LINEUP, can_field, within_position_limits
from app.draft.market import PriceBoard
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PERCENTAGE_COMPONENTS, PlayerProjection

#: Folded into the z-score so the inner loop divides by nothing.
_ROOT_TWO = sqrt(2.0)

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
    #: Lineup slots the player may occupy. A roster is only valid if its
    #: players can cover every starting slot at once.
    eligible: frozenset[str] = frozenset()
    #: Primary position, which is what roster position limits count.
    position: str | None = None


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
    periods: float,
    availability: float,
    keys: Iterable[str],
) -> list[Candidate]:
    """Turn season projections and a price board into weekly candidates.

    A player without a price on the board is not a candidate: the market
    model already decided they will not be rostered.
    """
    wanted = tuple(keys)
    divisor = max(1.0, periods)
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
                eligible=projection.eligible,
                position=projection.position,
            )
        )
    return out


def _summed(players: Iterable[Candidate]) -> dict[str, float]:
    """The roster's raw weekly line, stat key to total, percentages not yet built.

    Kept separate from `roster_totals` because a swap changes it by one
    subtraction and one addition, which is the whole reason the search is
    fast enough to run between two bids.
    """
    summed: dict[str, float] = {}
    for player in players:
        for key, value in player.weekly.items():
            summed[key] = summed.get(key, 0.0) + value
    return summed


def _totals_from(summed: Mapping[str, float], categories: Sequence[str]) -> dict[str, float]:
    """Category totals from a raw line, rebuilding percentages from components.

    A shooting percentage is made over attempted for the whole roster, never
    the average of individual percentages, which would weight a player taking
    two shots the same as one taking twenty.
    """
    totals: dict[str, float] = {}
    for category in categories:
        if category in PERCENTAGE_COMPONENTS:
            made_key, attempted_key = PERCENTAGE_COMPONENTS[category]
            attempted = summed.get(attempted_key, 0.0)
            totals[category] = summed.get(made_key, 0.0) / attempted if attempted else 0.0
        else:
            totals[category] = summed.get(category, 0.0)
    return totals


def roster_totals(players: Iterable[Candidate], categories: Sequence[str]) -> dict[str, float]:
    """Sum the roster's weekly lines, rebuilding percentages from components."""
    return _totals_from(_summed(players), categories)


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


#: A scored category reduced to the three numbers the inner loop needs:
#: which key to read, how to turn a total into a z-score, and whether the
#: category is flat (no spread, so every total is a coin flip).
_Scored = tuple[str, float, float, bool]


def _scored(distributions: Sequence[CategoryDistribution], punt: frozenset[str]) -> list[_Scored]:
    """Flatten the distributions the search actually sums over.

    `CategoryDistribution.win_probability` is the definition; this is the
    same arithmetic with the per-call work hoisted out. The search evaluates
    it millions of times, and a dataclass attribute lookup and two function
    calls per category per trial is most of the cost.
    """
    out: list[_Scored] = []
    for distribution in distributions:
        if distribution.abbreviation in punt:
            continue
        flat = distribution.spread <= 0
        # z = (total - mean) / spread, negated when less is better, then
        # halved for the erf. Folded into one multiplier.
        scale = 0.0 if flat else 1.0 / (distribution.spread * _ROOT_TWO)
        if distribution.lower_is_better:
            scale = -scale
        out.append((distribution.abbreviation, distribution.mean, scale, flat))
    return out


def _expected_scored(totals: Mapping[str, float], scored: Sequence[_Scored]) -> float:
    """Expected categories won, from the flattened distributions."""
    expected = 0.0
    for key, mean, scale, flat in scored:
        if flat:
            expected += 0.5
        else:
            expected += 0.5 + 0.5 * erf((totals.get(key, 0.0) - mean) * scale)
    return expected


def _expected(
    totals: Mapping[str, float],
    distributions: Sequence[CategoryDistribution],
    punt: frozenset[str],
) -> float:
    """Expected categories won, without building the per-category breakdown.

    `score` is the same number with the probabilities kept.
    """
    return _expected_scored(totals, _scored(distributions, punt))


def fieldable(
    players: Iterable[Candidate],
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
) -> bool:
    """Whether this roster is legal: fills every slot, and respects the caps.

    Two separate rules. The lineup asks whether the players can cover the
    starting slots at once, which is a matching. The limits ask whether too
    many share a primary position, which is a count. A roster of four
    centres can field a lineup perfectly well and still be illegal.
    """
    roster = list(players)
    if limits and not within_position_limits((p.position for p in roster), limits):
        return False
    return can_field({p.player_id: p.eligible for p in roster}, lineup)


def within_shape(
    players: Iterable[Candidate], shape: Sequence[int] | None, exempt: frozenset[int]
) -> bool:
    """Whether the roster's spending fits a plan's places.

    `shape` is the most each place may cost, largest first. The players not
    `exempt` -- what we already own is -- must be assignable to distinct
    places that each cover their price, which for two descending lists is
    simply element by element. A partial roster is checked against the
    first places, which is the same condition. Extra players beyond the
    shape are held to its last place.
    """
    if not shape:
        return True
    prices = sorted((p.price for p in players if p.player_id not in exempt), reverse=True)
    last = shape[-1]
    return all(price <= (shape[i] if i < len(shape) else last) for i, price in enumerate(prices))


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
    shape: Sequence[int] | None = None,
    exempt: frozenset[int] = frozenset(),
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
        if shape and not within_shape([*roster, candidate], shape, exempt):
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
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    shape: Sequence[int] | None = None,
    exempt: frozenset[int] = frozenset(),
) -> RosterPlan:
    """Repeat the single best swap until no swap raises expected wins.

    The result is the same roster the straightforward version reaches -- the
    best fieldable swap, every sweep -- and there is a test that holds the
    two to the same answer. The difference is how much work each trial costs,
    and there are tens of thousands of trials per sweep.

    Two things make it cheap. A swap changes the roster's line by one
    subtraction and one addition, so the line is carried and adjusted rather
    than summed from scratch. And fieldability, which is a bipartite matching
    and by far the most expensive thing here, is checked only for a trial
    that has already beaten the incumbent. Almost none do. Checking the
    constraint first meant paying for a matching on every trial to reject it
    on score a moment later.
    """
    categories = [d.abbreviation for d in distributions]
    #: Split once rather than per trial: which categories are ratios, and
    #: which are plain sums.
    ratios = [(c, PERCENTAGE_COMPONENTS[c]) for c in categories if c in PERCENTAGE_COMPONENTS]
    plain = [c for c in categories if c not in PERCENTAGE_COMPONENTS]
    scored = _scored(distributions, punt)

    best = _plan(roster, distributions, punt)
    chosen = set(best.player_ids)
    summed = _summed(best.players)

    while True:
        threshold = best.expected_wins + 1e-9
        swap: tuple[int, Candidate] | None = None

        for index, outgoing in enumerate(best.players):
            if outgoing.player_id in keep:
                continue
            budget_left = budget - (best.cost - outgoing.price)
            # The roster's line without this player, computed once for the
            # whole inner loop rather than once per candidate.
            without = {key: summed[key] - value for key, value in outgoing.weekly.items()}

            for incoming in pool:
                if incoming.player_id in chosen or incoming.price > budget_left:
                    continue
                weekly = incoming.weekly
                totals = {key: without.get(key, 0.0) + weekly.get(key, 0.0) for key in plain}
                for category, (made_key, attempted_key) in ratios:
                    attempted = without.get(attempted_key, 0.0) + weekly.get(attempted_key, 0.0)
                    made = without.get(made_key, 0.0) + weekly.get(made_key, 0.0)
                    totals[category] = made / attempted if attempted else 0.0

                expected = _expected_scored(totals, scored)
                if expected <= threshold:
                    continue
                # Only now is a matching worth paying for. A swap that breaks
                # the lineup is not a swap, whatever it does to the score.
                trial = list(best.players)
                trial[index] = incoming
                if not within_shape(trial, shape, exempt) or not fieldable(trial, lineup, limits):
                    continue
                # The margin is re-applied, not dropped: the obvious loop
                # compares each further candidate against the incumbent plus
                # 1e-9, and without that this accepts a swap better by a
                # float's breadth and walks off to a different local optimum.
                threshold = expected + 1e-9
                swap = (index, incoming)

        if swap is None:
            return best
        index, incoming = swap
        players = list(best.players)
        outgoing = players[index]
        players[index] = incoming
        best = _plan(players, distributions, punt)
        chosen = set(best.player_ids)
        for key, value in outgoing.weekly.items():
            summed[key] = summed[key] - value
        for key, value in incoming.weekly.items():
            summed[key] = summed.get(key, 0.0) + value


def _repair_lineup(
    roster: list[Candidate],
    pool: Sequence[Candidate],
    *,
    budget: int,
    keep: frozenset[int],
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    shape: Sequence[int] | None = None,
    exempt: frozenset[int] = frozenset(),
) -> list[Candidate] | None:
    """Make an illegal start legal with one swap, cheapest first."""
    chosen = {c.player_id for c in roster}
    cost = sum(c.price for c in roster)
    for index, outgoing in enumerate(roster):
        if outgoing.player_id in keep:
            continue
        for incoming in sorted(pool, key=lambda c: c.price):
            if incoming.player_id in chosen or cost - outgoing.price + incoming.price > budget:
                continue
            trial = list(roster)
            trial[index] = incoming
            if within_shape(trial, shape, exempt) and fieldable(trial, lineup, limits):
                return trial
    return None


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
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    starts: Iterable[Iterable[int]] = (),
    shape: Sequence[int] | None = None,
    exempt: Iterable[int] = (),
) -> RosterPlan:
    """The roster that maximises expected weekly category wins under a budget.

    `locked` players are kept whatever happens, which is how a live draft
    room feeds back what has already been bought. `excluded` players are
    gone to someone else. Both exist so the same optimizer serves the plan
    before the draft and the re-solve during it.

    `starts` are rosters, as player ids, to begin the search from as well
    as the greedy and shuffled ones. A bid ceiling compares the best roster
    with a player against the best without him, and two independent local
    searches differ by more than most players are worth: measured on the
    2026 pool at two restarts, the same player's marginal came out -0.10
    and +0.11 on consecutive runs. Starting the with-him search from the
    without-him roster makes the two neighbours rather than strangers, and
    the comparison stops measuring the search.

    `shape` holds the roster to a spending plan: the most each place may
    cost, largest first, for every player not in `exempt` (see
    `within_shape`). The draft room passes its allocation's open places and
    exempts what we already own, so the roster with a player and the roster
    without him are both rosters the plan allows. If the locked players
    alone break the shape it is dropped, since no roster could keep it.

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
    spared = frozenset(exempt)
    if shape and not within_shape(kept, shape, spared):
        shape = None

    def worth(candidate: Candidate) -> float:
        return sum(candidate.weekly.values()) / max(1, candidate.price)

    ordered_pool = sorted(pool, key=worth, reverse=True)
    orders: list[list[Candidate]] = [ordered_pool]
    for warm in starts:
        # The warm roster first, in value order, then everyone else: the
        # greedy fill rebuilds it as far as the budget allows and patches
        # the rest, which is what a start should be.
        wanted = frozenset(warm)
        head = [c for c in ordered_pool if c.player_id in wanted]
        if head:
            orders.append(head + [c for c in ordered_pool if c.player_id not in wanted])
    shuffler = random.Random(seed)
    for _ in range(max(0, restarts)):
        shuffled = pool[:]
        shuffler.shuffle(shuffled)
        orders.append(shuffled)

    # A start has to fill the roster. One that could not is never compared:
    # it would score its empty slots as nothing and could still win on the
    # strength of a single star, which is not a roster anyone can field.
    required = min(roster_slots, len(pool) + len(kept))

    best: RosterPlan | None = None
    for ordered in orders:
        roster = _greedy(
            ordered,
            keep=kept,
            roster_slots=roster_slots,
            budget=budget,
            minimum_bid=minimum_bid,
            shape=shape,
            exempt=spared,
        )
        if len(roster) < required:
            continue
        # An unfieldable start is repaired by one cheap swap if any swap does
        # it, and abandoned otherwise; the other starts will usually manage.
        if not fieldable(roster, lineup, limits):
            repaired = _repair_lineup(
                roster,
                pool,
                budget=budget,
                keep=keep,
                lineup=lineup,
                limits=limits,
                shape=shape,
                exempt=spared,
            )
            if repaired is None:
                continue
            roster = repaired
        plan = _swap_improve(
            roster,
            pool,
            distributions,
            punt=punted,
            keep=keep,
            budget=budget,
            lineup=lineup,
            limits=limits,
            shape=shape,
            exempt=spared,
        )
        if best is None or plan.expected_wins > best.expected_wins + 1e-9:
            best = plan
    return best if best is not None else _plan([], distributions, punted)
