"""Reshaping the board to how this league actually spends.

`price_board` shares the pot out in proportion to value above replacement.
Measured against eight real auctions, that shape is compressed: at 14
teams the room pays about half again what the board says for the top five
players and about 30% less than it says below rank 60. The total is fixed,
so those are one fact -- the money the room throws at the top comes from
the bottom -- and the fix is one curve: a multiplier per rank tier on the
above-floor part of each price, then a rescale so the board still sums to
the pot.

FITTED, HELD OUT, AND WHAT IS LEFT

The multipliers below were fitted on every usable season before 2026 --
2019, 2021, 2022, 2024, 2025; 2020 and 2023 are excluded by
`app.draft.projections` -- and tested on 2026 as if unseen. Mean absolute
error over drafted players fell from 11.3 to 7.5 and the top-five ratio
from 1.45 to 1.01: Wembanyama's $61 becomes $79 against the $100 paid,
Jokic's $70 becomes $90 against $91. Leave-one-season-out, the curve beats
the raw board in five of six years. The exception is 2019, the one year
the room paid the board almost exactly (top-five ratio 0.99), where the
curve over-corrects to 0.80; 2025 also lands under, at 0.84. Fitting on
the same team count alone was tried and was worse held out: one season is
not a curve.

That is the bet, stated plainly: the room keeps paying the star premium
it has paid in four of the last five drafts. In a year like 2019 this
board bids too high early. `scripts/fit_tier_curve.py` refits and
re-tests; the numbers here are what it printed on 2026-09-14. A throwaway
version of that experiment reported 8.1 and 1.13 for the same
multipliers; the committed script and a direct check of the applied
prices agree on 7.5 and 1.01, and the throwaway could not be re-run, so
the reproducible figures are the ones recorded.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from app.draft.market import MINIMUM_BID, PriceBoard, PricedPlayer

#: Rank tiers, inclusive. The top is cut finer because that is where the
#: money concentrates and where the board is most wrong.
DEFAULT_BUCKETS: tuple[tuple[int, int], ...] = (
    (1, 5),
    (6, 15),
    (16, 30),
    (31, 60),
    (61, 100),
    (101, 10**9),
)


@dataclass(frozen=True)
class TierCurve:
    """A multiplier per rank bucket, and where it came from."""

    buckets: tuple[tuple[int, int], ...]
    multipliers: tuple[float, ...]
    fit_seasons: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.buckets) != len(self.multipliers):
            raise ValueError("one multiplier per bucket")

    def bucket_of(self, rank: int) -> int:
        for index, (low, high) in enumerate(self.buckets):
            if low <= rank <= high:
                return index
        raise ValueError(f"rank {rank} is outside every bucket")

    def multiplier_for(self, rank: int) -> float:
        return self.multipliers[self.bucket_of(rank)]


#: The curve the draft room uses. See the module docstring for provenance.
LEAGUE_TIER_CURVE = TierCurve(
    buckets=DEFAULT_BUCKETS,
    multipliers=(1.27, 1.32, 1.13, 0.89, 0.71, 0.87),
    fit_seasons=(2019, 2021, 2022, 2024, 2025),
)

#: A curve that changes nothing, for comparisons and tests.
FLAT_CURVE = TierCurve(DEFAULT_BUCKETS, (1.0,) * len(DEFAULT_BUCKETS), ())


@dataclass(frozen=True)
class Observation:
    """One drafted player: where the board ranked him, what it said, what he cost."""

    rank: int
    predicted: int
    actual: int


def rank_players(board: PriceBoard) -> list[PricedPlayer]:
    """The board in rank order, tiebroken so the order is deterministic."""
    return sorted(board.players, key=lambda p: (-p.expected_price, p.player_id))


def fit_tier_curve(
    observations: Iterable[Observation],
    *,
    buckets: Sequence[tuple[int, int]] = DEFAULT_BUCKETS,
    fit_seasons: Sequence[int] = (),
    minimum_bid: int = MINIMUM_BID,
) -> TierCurve:
    """Multiplier per bucket = money paid above the floor over money the
    board put above the floor, pooled across every observation in that
    bucket. A bucket nobody was drafted from keeps a multiplier of one."""
    paid = [0.0] * len(buckets)
    said = [0.0] * len(buckets)
    probe = TierCurve(tuple(buckets), (1.0,) * len(buckets), ())
    for o in observations:
        index = probe.bucket_of(o.rank)
        paid[index] += max(0, o.actual - minimum_bid)
        said[index] += max(0, o.predicted - minimum_bid)
    multipliers = tuple(p / s if s else 1.0 for p, s in zip(paid, said, strict=True))
    return TierCurve(tuple(buckets), multipliers, tuple(fit_seasons))


def apply_tier_curve(
    board: PriceBoard, curve: TierCurve, *, minimum_bid: int = MINIMUM_BID
) -> PriceBoard:
    """The same board, reshaped, still summing to the pot.

    Each price's above-floor part is scaled by its tier's multiplier; then
    the rostered players' above-floor total is rescaled to the pot above
    floor, exactly as `price_board` allocates it, so nothing the curve does
    can create or destroy money. Nobody drops below the floor.
    """
    ranked = rank_players(board)
    scaled = [
        max(0.0, (p.expected_price - minimum_bid) * curve.multiplier_for(rank))
        for rank, p in enumerate(ranked, 1)
    ]
    rostered = board.rostered
    pot = max(0, board.total_budget - rostered * minimum_bid)
    claimed = sum(sorted(scaled, reverse=True)[:rostered])
    factor = pot / claimed if claimed else 0.0
    by_id = {
        p.player_id: replace(p, expected_price=minimum_bid + round(value * factor))
        for p, value in zip(ranked, scaled, strict=True)
    }
    return replace(board, players=tuple(by_id[p.player_id] for p in board.players))
