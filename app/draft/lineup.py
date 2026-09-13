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

from collections.abc import Iterable, Mapping, Sequence

#: This league's daily starting lineup. Ten slots; three bench spots make
#: the thirteen-man roster and are not part of the feasibility question.
DEFAULT_LINEUP: tuple[str, ...] = ("PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT")

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
