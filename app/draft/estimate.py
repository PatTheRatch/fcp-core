"""What a player is worth to us, before the search has finished.

A bid ceiling is a bisection over whole re-solves. On the VPS, mid-draft,
that measured 8, 21, 24 and 56 seconds for four players against a
thirty-second nomination clock, and a screen that answers after the
hammer has answered nothing. `bid_ceiling` is now a great deal cheaper
(see `app.draft.room`), but cheaper is not instant, and the moment a
player hits the block the manager needs a number rather than an ellipsis.

So: an estimate, from a swap rather than a search.

The plan -- the best roster we can still finish -- is already on hand,
cached per pick sequence. For every player in it we have not already
bought, ask what the roster would win with this one in his place. The
best of those swaps is what he adds. Nothing is re-solved and nothing is
searched: each trial is one roster scored, which is a sum over nine
categories.

Dollars come from the league's own price curve (`app.scoring.draft`,
fitted on 1,206 picks 2019-2026):

    expected(price) = PRICE_INTERCEPT + PRICE_SLOPE * sqrt(price)

categories a week that a pick at a price has usually delivered. What
matters here is its slope, because the question is marginal: one more
category a week, bought around a price of p, costs

    d price / d value = 2 * sqrt(p) / PRICE_SLOPE

So a player `gain` categories a week better than the $p place he would
take is worth about p + gain * 2 * sqrt(p) / PRICE_SLOPE.

WHAT IT IS NOT. It holds the plan's other twelve fixed, where the real
ceiling re-solves the whole roster around him and lets the budget move.
It cannot see that paying up for one player changes who else is
affordable, which is the entire reason the exact answer needs a search.
It is labelled an estimate on the screen for that reason, and it is
replaced the moment the exact ceiling lands.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from app.draft.lineup import DEFAULT_LINEUP
from app.draft.optimizer import Candidate, RosterPlan, evaluate, fieldable
from app.draft.room import DraftState
from app.draft.targets import CategoryDistribution
from app.scoring.draft import PRICE_SLOPE


def dollars_for(gain: float, at_price: int) -> float:
    """What `gain` more categories a week costs, bought around `at_price`.

    The price curve's slope, inverted. Below a dollar the curve's square
    root is meaningless, so the floor stands in.
    """
    return gain * 2.0 * math.sqrt(max(1.0, float(at_price))) / PRICE_SLOPE


def estimated_worth(
    state: DraftState,
    plan: RosterPlan,
    player: Candidate,
    distributions: Sequence[CategoryDistribution],
    *,
    punt: Iterable[str] = (),
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    cap: int | None = None,
) -> int | None:
    """Roughly the most we should pay for him, without a search.

    `plan` is the best roster we can still finish. A player already in it
    is worth at least what the plan budgeted for him, so that is the
    estimate and the search will say whether it should be more. Anyone
    else is priced by the best swap he could make into it.

    Returns None when there is no swap to make -- every place in the plan
    is a player we have already bought, so nothing can be given up. Zero
    means no swap is worth making: a floor bid buys more elsewhere.
    """
    if player.player_id in plan.player_ids:
        return next(c.price for c in plan.players if c.player_id == player.player_id)

    owned = state.mine.player_ids
    base = plan.expected_wins
    best: float | None = None
    for index, outgoing in enumerate(plan.players):
        if outgoing.player_id in owned:
            continue  # what we have bought is a fact, not a place to be traded
        trial = list(plan.players)
        trial[index] = player
        # A swap that cannot be fielded is not a swap, the same rule the
        # search applies. It is a matching over thirteen players, not a cost.
        if not fieldable(trial, lineup, limits):
            continue
        gain = evaluate(trial, distributions, punt).expected_wins - base
        worth = outgoing.price + dollars_for(gain, outgoing.price)
        if best is None or worth > best:
            best = worth
    if best is None:
        return None

    limit = state.mine.max_bid(state.minimum_bid)
    if cap is not None:
        limit = min(limit, cap)
    return max(0, min(round(best), limit))
