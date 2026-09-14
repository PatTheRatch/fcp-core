"""The live draft room: what has happened, and what to bid next.

An auction is a sequence of picks, each a player going to a team for a
price. The room keeps that sequence and derives everything else from it: how
much every manager has left, how many places each still has to fill, the
most anyone can legally bid, and -- the number that matters on the clock --
the most *we* should pay for the player on the block.

Nothing here reads ESPN. A mock draft on 2026-09-13 showed the read API
carries a draft's existence (`inProgress`) but none of its picks until it is
over: 1,347 polls across a whole auction, zero picks. The bidding lives on
the draft client's websocket. So picks reach the room from outside, typed or
scraped from the page, and the room stays a pure function of them. That is
also what makes it testable against a draft that already happened.

THREE NUMBERS, THREE DIFFERENT QUESTIONS

  max_bid       The most a team can legally bid: what it has left, less a
                floor bid for every other place it still has to fill. The
                rules' ceiling, not a judgment.

  field ceiling The highest max_bid among the *other* teams. No player can
                go for more than this whatever anyone thinks he is worth,
                and it falls as the room spends.

  bid ceiling   The most we should pay for one player: the highest price at
                which owning him still leaves us a roster at least as good
                as the best roster without him. Above it we are paying for
                something the rest of the board provides more cheaply. This
                one is a judgment, and it is the optimizer's.

REPRICING

Board prices are set before the draft to exhaust the total budget exactly.
Once money starts moving they drift: a room that overpays for stars leaves
less for everyone after, so the remaining board is worth less in dollars
than it was priced at, and a room that sits on its money does the reverse.
The remaining players are repriced by the ratio of discretionary money left
(what teams hold above the floor bids they still owe) to the board value
left (what the players who will still be rostered were priced at above the
floor). That is the standard auction inflation figure, applied continuously.

Prices we have actually paid are facts, not estimates, and are used as such:
a player we own is carried at what he cost, never at what the board said.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from app.draft.lineup import DEFAULT_LINEUP
from app.draft.market import MINIMUM_BID
from app.draft.optimizer import DEFAULT_RESTARTS, Candidate, RosterPlan, optimize
from app.draft.targets import CategoryDistribution


class DraftError(ValueError):
    """A pick the rules would not allow, or a room that does not exist."""


@dataclass(frozen=True)
class Pick:
    """One player going to one team for one price."""

    player_id: int
    team_id: int
    price: int


@dataclass(frozen=True)
class TeamState:
    team_id: int
    name: str
    budget: int
    roster_slots: int
    picks: tuple[Pick, ...] = ()

    @property
    def spent(self) -> int:
        return sum(p.price for p in self.picks)

    @property
    def remaining(self) -> int:
        return self.budget - self.spent

    @property
    def open_slots(self) -> int:
        return self.roster_slots - len(self.picks)

    @property
    def player_ids(self) -> frozenset[int]:
        return frozenset(p.player_id for p in self.picks)

    def max_bid(self, minimum_bid: int = MINIMUM_BID) -> int:
        """The most this team can legally bid on its next player.

        Every place still to fill after this one needs a floor bid, so that
        money is spoken for. A team with no open place cannot bid at all.
        """
        if self.open_slots <= 0:
            return 0
        return max(0, self.remaining - (self.open_slots - 1) * minimum_bid)


@dataclass(frozen=True)
class DraftState:
    """Everything the room knows, as a value. `apply` returns a new one."""

    budget: int
    roster_slots: int
    teams: Mapping[int, TeamState]
    me: int
    minimum_bid: int = MINIMUM_BID
    #: ESPN team ids in nomination order, repeating. Empty when unknown.
    nomination_order: tuple[int, ...] = ()
    picks: tuple[Pick, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.me not in self.teams:
            raise DraftError(f"team {self.me} is not in this room")
        if self.budget <= 0:
            raise DraftError(
                f"auction budget is {self.budget}; zero means the season's draft settings "
                "have not been ingested, and a plan against it would be fiction"
            )

    @classmethod
    def open(
        cls,
        *,
        budget: int,
        roster_slots: int,
        teams: Mapping[int, str],
        me: int,
        minimum_bid: int = MINIMUM_BID,
        nomination_order: Iterable[int] = (),
    ) -> DraftState:
        """A room before the first nomination."""
        return cls(
            budget=budget,
            roster_slots=roster_slots,
            teams={
                team_id: TeamState(team_id, name, budget, roster_slots)
                for team_id, name in teams.items()
            },
            me=me,
            minimum_bid=minimum_bid,
            nomination_order=tuple(nomination_order),
        )

    # -- reading --------------------------------------------------------

    @property
    def mine(self) -> TeamState:
        return self.teams[self.me]

    @property
    def taken(self) -> frozenset[int]:
        """Every player on any roster."""
        return frozenset(p.player_id for p in self.picks)

    @property
    def taken_by_others(self) -> frozenset[int]:
        return self.taken - self.mine.player_ids

    @property
    def complete(self) -> bool:
        return all(t.open_slots == 0 for t in self.teams.values())

    @property
    def open_slots(self) -> int:
        """Places still to fill across the whole room."""
        return sum(t.open_slots for t in self.teams.values())

    @property
    def dollars_left(self) -> int:
        return sum(t.remaining for t in self.teams.values())

    @property
    def discretionary(self) -> int:
        """Money in the room above the floor bids still owed."""
        return sum(
            max(0, t.remaining - t.open_slots * self.minimum_bid) for t in self.teams.values()
        )

    def field_ceiling(self) -> int:
        """The most any *other* team can legally bid right now."""
        return max(
            (t.max_bid(self.minimum_bid) for t in self.teams.values() if t.team_id != self.me),
            default=0,
        )

    def to_nominate(self) -> int | None:
        """Whose turn it is to put a player up, if the order is known."""
        if not self.nomination_order:
            return None
        return self.nomination_order[len(self.picks) % len(self.nomination_order)]

    # -- writing --------------------------------------------------------

    def apply(self, pick: Pick) -> DraftState:
        """The room after this pick. Refuses a pick the rules would refuse."""
        team = self.teams.get(pick.team_id)
        if team is None:
            raise DraftError(f"team {pick.team_id} is not in this room")
        if pick.player_id in self.taken:
            raise DraftError(f"player {pick.player_id} has already been drafted")
        if pick.price < self.minimum_bid:
            raise DraftError(f"${pick.price} is below the ${self.minimum_bid} minimum")
        if team.open_slots <= 0:
            raise DraftError(f"{team.name} has no roster place left")
        ceiling = team.max_bid(self.minimum_bid)
        if pick.price > ceiling:
            raise DraftError(
                f"{team.name} cannot pay ${pick.price}: ${team.remaining} left with "
                f"{team.open_slots} places to fill caps a bid at ${ceiling}"
            )
        updated = replace(team, picks=(*team.picks, pick))
        return replace(
            self,
            teams={**self.teams, pick.team_id: updated},
            picks=(*self.picks, pick),
        )

    def undo(self) -> DraftState:
        """The room before the last pick. For a mis-typed entry."""
        if not self.picks:
            return self
        last = self.picks[-1]
        team = self.teams[last.team_id]
        return replace(
            self,
            teams={**self.teams, last.team_id: replace(team, picks=team.picks[:-1])},
            picks=self.picks[:-1],
        )


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------


def inflation(state: DraftState, candidates: Sequence[Candidate]) -> float:
    """How far the remaining board's prices have drifted from its money.

    Above one, the room has money to burn and the remaining players will go
    for more than the board said; below one, the stars were overpaid and the
    rest goes cheaper. Exactly one before the first pick, by construction.

    Only players who will still be rostered count: the room has `open_slots`
    places left, so the most valuable `open_slots` remaining players are the
    board value that the discretionary money is going to buy.
    """
    remaining = sorted(
        (c.price for c in candidates if c.player_id not in state.taken), reverse=True
    )[: state.open_slots]
    board_value = sum(max(0, price - state.minimum_bid) for price in remaining)
    if board_value <= 0:
        return 1.0
    return state.discretionary / board_value


def reprice(state: DraftState, candidates: Sequence[Candidate]) -> list[Candidate]:
    """The pool as the room should see it now.

    Players other teams own are gone. Players we own carry what we paid.
    Everyone else is scaled by the inflation figure, above the floor.
    """
    factor = inflation(state, candidates)
    paid = {p.player_id: p.price for p in state.mine.picks}
    floor = state.minimum_bid
    out: list[Candidate] = []
    for c in candidates:
        if c.player_id in state.taken_by_others:
            continue
        if c.player_id in paid:
            out.append(replace(c, price=paid[c.player_id]))
            continue
        scaled = floor + round((c.price - floor) * factor)
        out.append(replace(c, price=max(floor, scaled)))
    return out


# ---------------------------------------------------------------------------
# solving
# ---------------------------------------------------------------------------


def resolve(
    state: DraftState,
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    punt: Iterable[str] = (),
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    restarts: int = DEFAULT_RESTARTS,
    seed: int = 0,
    lock: Mapping[int, int] | None = None,
    exclude: Iterable[int] = (),
    starts: Iterable[Iterable[int]] = (),
) -> RosterPlan:
    """The best roster we can still finish from here.

    What we own is locked at what we paid. What others own is gone. The rest
    is repriced for the money left in the room. `lock` adds players at given
    prices as if we had just won them, which is how a bid is tested; `exclude`
    removes players as if someone else had, which is how the alternative is.
    """
    pool = reprice(state, candidates)
    forced = dict(lock or {})
    if forced:
        pool = [replace(c, price=forced[c.player_id]) if c.player_id in forced else c for c in pool]
    locked = [*state.mine.player_ids, *forced]
    return optimize(
        pool,
        distributions,
        budget=state.budget,
        roster_slots=state.roster_slots,
        punt=punt,
        locked=locked,
        excluded=exclude,
        minimum_bid=state.minimum_bid,
        restarts=restarts,
        seed=seed,
        lineup=lineup,
        limits=limits,
        starts=starts,
    )


@dataclass(frozen=True)
class Ceiling:
    """What one player is worth to us right now, and why."""

    player_id: int
    #: The most we should pay. None when even the floor bid is not worth it.
    price: int | None
    #: The most we could legally pay.
    max_bid: int
    #: Expected weekly category wins of the best roster without him.
    without: float
    #: The same with him at `price`, when there is one.
    with_him: float | None
    #: What he adds at the floor bid: with him at the minimum, less without
    #: him. This is the magnitude behind the price. Late in a draft it is
    #: small for everyone -- a roster already winning a category at 94% gets
    #: almost nothing from more of it -- and a reader deciding whether to
    #: fight for a player needs to see how much is actually at stake.
    marginal_at_floor: float
    #: The most any other team can pay. Bidding past it is pointless.
    field: int


def bid_ceiling(
    state: DraftState,
    player_id: int,
    candidates: Sequence[Candidate],
    distributions: Sequence[CategoryDistribution],
    *,
    punt: Iterable[str] = (),
    lineup: Sequence[str] = DEFAULT_LINEUP,
    limits: Mapping[str, int] | None = None,
    restarts: int = DEFAULT_RESTARTS,
    seed: int = 0,
) -> Ceiling:
    """The most we should pay for the player on the block.

    The roster with him at price p is compared against the best roster
    without him. As p rises, the plan with him can only get worse -- the
    money has to come from somewhere -- so the answer is found by bisection
    over the legal range: the highest p at which having him is still at
    least as good as not.

    Costs one solve for the baseline and about seven for the search. Expect
    a few seconds at the default restarts; pass fewer on the clock.
    """
    if player_id in state.taken:
        raise DraftError(f"player {player_id} has already been drafted")
    if not any(c.player_id == player_id for c in candidates):
        raise DraftError(f"player {player_id} is not on the board")

    def solve(
        *,
        lock: Mapping[int, int] | None = None,
        exclude: Iterable[int] = (),
        starts: Iterable[Iterable[int]] = (),
    ) -> RosterPlan:
        return resolve(
            state,
            candidates,
            distributions,
            punt=punt,
            lineup=lineup,
            limits=limits,
            restarts=restarts,
            seed=seed,
            lock=lock,
            exclude=exclude,
            starts=starts,
        )

    baseline = solve(exclude=[player_id])
    # Every with-him search starts from the without-him roster as well as
    # from the usual seeds, so the comparison measures the player and not
    # the search. It can only raise the with-him side, so a ceiling errs
    # generous rather than refusing a player worth having.
    warm = [tuple(baseline.player_ids)]
    ceiling = state.mine.max_bid(state.minimum_bid)
    field_max = state.field_ceiling()

    def with_price(price: int) -> float:
        plan = solve(lock={player_id: price}, starts=warm)
        # A plan that could not actually fit him is not a plan with him.
        if player_id not in plan.player_ids:
            return float("-inf")
        return plan.expected_wins

    without = baseline.expected_wins
    if ceiling < state.minimum_bid:
        return Ceiling(player_id, None, ceiling, without, None, float("-inf"), field_max)

    low, high = state.minimum_bid, ceiling
    at_low = with_price(low)
    marginal = at_low - without
    if at_low < without:
        return Ceiling(player_id, None, ceiling, without, None, marginal, field_max)
    at_high = with_price(high)
    if at_high >= without:
        return Ceiling(player_id, high, ceiling, without, at_high, marginal, field_max)

    # Invariant: with_price(low) >= without > with_price(high).
    best_value = at_low
    while high - low > 1:
        mid = (low + high) // 2
        value = with_price(mid)
        if value >= without:
            low, best_value = mid, value
        else:
            high = mid
    return Ceiling(player_id, low, ceiling, without, best_value, marginal, field_max)
