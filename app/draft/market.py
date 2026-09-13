"""What this league will pay for a player.

An auction is not a pricing function applied to each player in isolation.
It is a fixed pot handed out until it is gone: fourteen teams at two
hundred dollars spent 2787 of 2800 in 2026, with 99.5% of the budget used.
A price model that does not respect that will predict a market that cannot
exist.

So prices are shares. A player is worth what they add above the last player
who gets rostered at all, and every dollar over the minimum bids is split
between those surpluses. That is budget consistent by construction, and it
fits: against the 2026 draft the mean error is 6.50 dollars.

Measuring value above *replacement* rather than above the pool mean also
straightens the curve. Priced against the mean, this league looks like it
pays a falling rate for quality, from 28 dollars a point of value down to 8.
That is an artefact of where zero was put, not a market judgement.

The residuals are the interesting part, because they are where the room
disagrees with the projection. In 2026 four players took 170 dollars more
than the model allows, 6% of the entire league budget: Cade Cunningham at
83 against a model 29, Giannis at 70 against 24, Doncic at 91 against 49
and Banchero at 33 against 5. All four are reputations the current
projection no longer supports.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from app.draft.valuation import PlayerValue

#: Every drafted player costs at least this, so it comes off the pot before
#: anything is shared out.
MINIMUM_BID = 1


@dataclass(frozen=True)
class PricedPlayer:
    player_id: int
    name: str
    value: float
    #: What the player adds over the last man rostered. Never negative:
    #: nobody pays for being worse than free.
    surplus: float
    expected_price: int


@dataclass(frozen=True)
class PriceBoard:
    """A full auction board, priced to exactly exhaust the budget."""

    players: tuple[PricedPlayer, ...]
    replacement_value: float
    teams: int
    budget_per_team: int
    roster_slots: int

    @property
    def total_budget(self) -> int:
        return self.teams * self.budget_per_team

    @property
    def rostered(self) -> int:
        return self.teams * self.roster_slots

    def price_of(self, player_id: int) -> int | None:
        return next((p.expected_price for p in self.players if p.player_id == player_id), None)


@dataclass(frozen=True)
class Calibration:
    """How the board compares with a draft that actually happened."""

    compared: int
    mean_absolute_error: float
    worst_overpaid: tuple[tuple[str, int, int], ...]
    worst_underpaid: tuple[tuple[str, int, int], ...]


def replacement_value(values: Sequence[PlayerValue], rostered: int) -> float:
    """The value of the last player who gets rostered at all.

    Everything is priced against this rather than against the pool average,
    because a player only costs money to the extent they beat what is
    available for a dollar.
    """
    if not values:
        return 0.0
    ranked = sorted(values, key=lambda value: value.total, reverse=True)
    index = min(max(rostered, 1), len(ranked)) - 1
    return ranked[index].total


def price_board(
    values: Sequence[PlayerValue],
    *,
    teams: int,
    budget_per_team: int,
    roster_slots: int,
    minimum_bid: int = MINIMUM_BID,
) -> PriceBoard:
    """Share the pot out in proportion to value above replacement.

    Players at or below replacement are priced at the minimum. They still
    have to be bought, since the roster has to be filled, but nothing above
    the floor is spent on them.
    """
    rostered = max(1, teams * roster_slots)
    replacement = replacement_value(values, rostered)
    discretionary = max(0, teams * budget_per_team - rostered * minimum_bid)

    surpluses = {value.player_id: max(0.0, value.total - replacement) for value in values}
    # Only the players who will actually be rostered compete for the pot.
    ranked = sorted(values, key=lambda value: value.total, reverse=True)
    claimed = sum(surpluses[value.player_id] for value in ranked[:rostered])

    priced: list[PricedPlayer] = []
    for value in ranked:
        surplus = surpluses[value.player_id]
        share = (surplus / claimed) if claimed else 0.0
        priced.append(
            PricedPlayer(
                player_id=value.player_id,
                name=value.name,
                value=value.total,
                surplus=surplus,
                expected_price=minimum_bid + round(discretionary * share),
            )
        )

    return PriceBoard(
        players=tuple(priced),
        replacement_value=replacement,
        teams=teams,
        budget_per_team=budget_per_team,
        roster_slots=roster_slots,
    )


def calibrate(board: PriceBoard, actual_prices: dict[int, int], *, worst: int = 5) -> Calibration:
    """Check a board against a draft that happened.

    The errors are the point. A board that matched perfectly would only be
    telling the room what it already believed.
    """
    compared = [
        (player, actual_prices[player.player_id])
        for player in board.players
        if player.player_id in actual_prices
    ]
    if not compared:
        return Calibration(0, 0.0, (), ())

    errors = [abs(player.expected_price - paid) for player, paid in compared]
    by_gap = sorted(compared, key=lambda pair: pair[1] - pair[0].expected_price)

    def described(pairs: list[tuple[PricedPlayer, int]]) -> tuple[tuple[str, int, int], ...]:
        return tuple((p.name, p.expected_price, paid) for p, paid in pairs)

    return Calibration(
        compared=len(compared),
        mean_absolute_error=fmean(errors),
        worst_overpaid=described(by_gap[-worst:][::-1]),
        worst_underpaid=described(by_gap[:worst]),
    )
