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

The draft only ever asks whether the whole lineup can be covered, but the
in-season question is how much of it can be, so the matching is sized rather
than asked as a boolean (`max_matching`, with `can_field` on top of it).
Same algorithm either way: a second one would be a second thing to be wrong.
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


def max_matching(
    eligibilities: Mapping[int, Iterable[str]],
    lineup: Sequence[str] = DEFAULT_LINEUP,
) -> int:
    """How many lineup slots a set of players can fill at once.

    The count `can_field` never needed: a roster of four guards against a
    lineup with three guard places fills three, and the fourth is a start
    going nowhere. In season that number is the whole question, because a
    player with four games in a week is worth nothing on a day the lineup is
    already full.

    `eligibilities` maps a player id to the slot names they may occupy. A
    player who could sit in UT covers any UT slot, so the three UT slots are
    simply three copies of the same requirement.

    Returns the size of the largest assignment, which is at most
    `len(lineup)`. It is the size of `assign`'s answer and nothing else, so
    the count and the lineup it counts can never disagree.
    """
    return len(assign(eligibilities, lineup))


def assign(
    eligibilities: Mapping[int, Iterable[str]],
    lineup: Sequence[str] = DEFAULT_LINEUP,
) -> dict[int, int]:
    """Who sits where: the index of a place in `lineup` -> the player in it.

    The matching itself, which the draft only ever needed the size of and a
    daily lineup needs by name: "who starts today" is a grid, not a count
    (`app.pickups.today`).

    Standard augmenting-path matching: places are distinguished by position
    in `lineup`, so two UT places are two places. The answer is *a* largest
    assignment and not the only one -- two men who fit the same two places
    can be swapped between them -- so a caller who wants a stable grid hands
    the players in a stable order, which is the order they are tried in.
    """
    players = list(eligibilities)
    if not players or not lineup:
        return {}

    slot_ids = list(range(len(lineup)))
    eligible = {player: _usable(eligibilities[player]) for player in players}

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

    for player in players:
        try_assign(player, set())
    return assigned


def _usable(slots: Iterable[str]) -> set[str]:
    """The slots of one player's eligibility that are lineup slots at all.

    Bench and injured reserve are eligibility ESPN lists and places nobody
    starts, so they are dropped once here rather than at every call site.
    """
    return {slot for slot in slots if slot not in _NON_LINEUP}


def can_field(
    eligibilities: Mapping[int, Iterable[str]],
    lineup: Sequence[str] = DEFAULT_LINEUP,
) -> bool:
    """True if every lineup slot can be filled by a distinct eligible player.

    The yes/no over `max_matching`: a lineup is fieldable exactly when the
    largest assignment covers all of it. Kept as a name because it reads as
    the question the optimizer asks, and because everything already calling
    it means this, not the count.
    """
    if not lineup:
        return True
    return max_matching(eligibilities, lineup) >= len(lineup)


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
