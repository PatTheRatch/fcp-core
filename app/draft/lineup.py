"""Can this roster actually be fielded?

A roster is only usable if its players can fill the starting slots at once.
ESPN lists each player's eligible slots and the league fixes its lineup, so
the question is a bipartite matching: assign players to slots such that
every slot is covered and no player is used twice. Standard augmenting-path
matching, small enough here that the simplest correct version is the right
one.

The optimizer treats an unfieldable roster as invalid, not as low scoring.
Before this existed, a roster of thirteen centres would have scored well on
blocks and rebounds and been accepted.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

#: Fallback lineup, used only when a season has no stored roster rules.
#: The real one comes from ESPN via `lineup_from_settings`, because it is a
#: season setting and this league has already changed others between years.
DEFAULT_LINEUP: tuple[str, ...] = ("PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT")

#: Slot order for a readable lineup. Anything unrecognised follows.
_SLOT_ORDER = ("PG", "SG", "SF", "PF", "C", "G", "F", "G/F", "PF/C", "F/C", "UT")


def lineup_from_settings(lineup_slots: Mapping[str, int]) -> tuple[str, ...]:
    """Expand ESPN's slot counts into one entry per startable place.

    `{"PG": 1, "UT": 3}` becomes `("PG", "UT", "UT", "UT")`, because three
    utility places are three separate requirements to cover.
    """
    if not lineup_slots:
        return DEFAULT_LINEUP

    def rank(slot: str) -> tuple[int, str]:
        return (_SLOT_ORDER.index(slot) if slot in _SLOT_ORDER else len(_SLOT_ORDER), slot)

    expanded: list[str] = []
    for slot in sorted(lineup_slots, key=rank):
        expanded.extend([slot] * max(0, int(lineup_slots[slot])))
    return tuple(expanded)


def within_position_limits(
    positions: Iterable[str | None],
    limits: Mapping[str, int],
) -> bool:
    """Whether a roster respects caps like "at most three centres".

    Counted on primary position, which is what ESPN limits. A power forward
    eligible at centre is not a centre for this purpose, so eligibility is
    the wrong thing to count and would refuse legal rosters.
    """
    if not limits:
        return True
    counts = Counter(position for position in positions if position)
    return all(counts.get(position, 0) <= limit for position, limit in limits.items())


#: Slots that ESPN counts as a position but that are never lineup slots.
_NON_LINEUP = frozenset({"BE", "IR"})


def can_field(
    eligibilities: Mapping[int, Iterable[str]],
    lineup: Sequence[str] = DEFAULT_LINEUP,
) -> bool:
    """True if every lineup slot can be filled by a distinct eligible player.

    `eligibilities` maps a player id to the slot names they may occupy. A
    player who could sit in UT covers any UT slot, so the three UT slots are
    simply three copies of the same requirement.
    """
    players = list(eligibilities)
    slot_ids = list(range(len(lineup)))
    eligible = {
        player: {slot for slot in eligibilities[player] if slot not in _NON_LINEUP}
        for player in players
    }

    # slot index -> player currently assigned to it.
    assigned: dict[int, int] = {}

    def try_assign(player: int, seen: set[int]) -> bool:
        """Find a slot for `player`, displacing an assignee who can move."""
        for slot_id in slot_ids:
            if slot_id in seen or lineup[slot_id] not in eligible[player]:
                continue
            seen.add(slot_id)
            occupant = assigned.get(slot_id)
            if occupant is None or try_assign(occupant, seen):
                assigned[slot_id] = player
                return True
        return False

    matched = sum(1 for player in players if try_assign(player, set()))
    return matched >= len(lineup)


def uncovered_slots(
    eligibilities: Mapping[int, Iterable[str]],
    lineup: Sequence[str] = DEFAULT_LINEUP,
) -> tuple[str, ...]:
    """Which slots a roster cannot fill, for telling a user what is missing.

    Greedy rather than exact, since it exists to name the problem and not to
    decide it; `can_field` decides.
    """
    remaining = list(lineup)
    for slots in eligibilities.values():
        usable = {slot for slot in slots if slot not in _NON_LINEUP}
        for index, slot in enumerate(remaining):
            if slot in usable:
                del remaining[index]
                break
    return tuple(remaining)
