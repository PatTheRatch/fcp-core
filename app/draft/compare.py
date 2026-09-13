"""Telling whether two plans are actually different.

When two strategies are compared on one season, the comparison is only as
independent as the rosters are. Two plans sharing six of thirteen players
are half the same experiment, and whichever shared player got hurt decides
the result for both. So any comparison should say how much it shares.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.draft.optimizer import RosterPlan


@dataclass(frozen=True)
class Overlap:
    shared: tuple[str, ...]
    only_first: tuple[str, ...]
    only_second: tuple[str, ...]

    @property
    def shared_fraction(self) -> float:
        total = len(self.shared) + max(len(self.only_first), len(self.only_second))
        return len(self.shared) / total if total else 0.0


def overlap(first: RosterPlan, second: RosterPlan) -> Overlap:
    """Which players two plans share, and which are unique to each."""
    a = {p.player_id: p.name for p in first.players}
    b = {p.player_id: p.name for p in second.players}
    shared = a.keys() & b.keys()
    return Overlap(
        shared=tuple(sorted(a[i] for i in shared)),
        only_first=tuple(sorted(a[i] for i in a.keys() - shared)),
        only_second=tuple(sorted(b[i] for i in b.keys() - shared)),
    )


def player_frequency(plans: Sequence[RosterPlan]) -> list[tuple[str, int]]:
    """How many of the plans each player appears in, most common first.

    A player in every plan is not necessarily a mistake, they may simply be
    the best value on the board, but it means the plans are not independent
    tests of anything and a comparison between them should say so.
    """
    counts: Counter[str] = Counter()
    for plan in plans:
        counts.update(p.name for p in plan.players)
    return counts.most_common()
