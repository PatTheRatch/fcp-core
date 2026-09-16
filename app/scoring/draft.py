"""Draft grades: what each pick cost, what it was worth, and what it became.

Per pick, the spec's two checks on price and one on the asset:

- **Price against projected value**, knowable at the draft: the board price
  the room's own valuation gave him from that season's preseason projections
  (`app.draft.market.price_board` with the league's tier curve). None for
  2020 and 2023, whose stored projections are not forecasts.
- **Price against the market**: what he was expected to go for, ESPN's
  average auction price across its leagues where the price cache
  (`logs/price-cache/<season>.json`, written by `scripts/price_scorecard.py`)
  has it, the board otherwise. Reading the cache only: grading never calls ESPN.
- **The asset**, one hop: kept, his value to the team; traded, his value to
  the team until the trade plus a share of what came back (the trade's
  incoming players' value to the team, split evenly over the players sent);
  dropped, his value to the team until the drop and nothing after. A trade
  whose return left no roster trace counts only his value until the trade,
  and says so ("traded, return unknown").

ONE CURRENCY FOR DOLLARS AND CATEGORIES

A price is dollars and value is categories a week, so the two lenses go
through what a dollar has bought in this league. Measured 2026-09-16 on the
live database over 1,206 auction picks 2019-2026, each pick's value to the
team that drafted him (team fit while held, over the season's regular weeks):

| price | picks | categories a week |
|---|---|---|
| $1 | 314 | 0.11 |
| $2 | 99 | 0.15 |
| $3-5 | 177 | 0.19 |
| $6-9 | 124 | 0.28 |
| $10-15 | 129 | 0.34 |
| $16-25 | 142 | 0.39 |
| $26-40 | 154 | 0.46 |
| $41-60 | 82 | 0.56 |
| $61+ | 53 | 0.65 |

Least squares on the square root of price fits it better than on price
(mean squared error 0.0403 against 0.0432): `expected(price) = 0.050 +
0.0735 x sqrt(price)`. It runs a little high above $60 (0.62 at $60 and 0.79
at $100, against 0.65 for $61+), so the very top of a grade leans harsh.

So:

    decision = expected(projected value) - expected(price paid)
    result   = what the asset delivered - expected(price paid)

both in categories a week. A $40 pick the board valued at $25 starts at
-0.10; one who then delivers 0.60 a week against the 0.52 a $40 pick
usually does is +0.08 on result.

`DRAFT_BAND`, the neutral band for these verdicts, is 0.10: about what
doubling a mid-board price buys ($10 to $20 is 0.096). A pick within that of
par is a fair price, not a steal or a reach.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DraftPick, Player
from app.draft import pool
from app.draft.market import price_board
from app.draft.projections import projection_problem
from app.draft.tiers import LEAGUE_TIER_CURVE, apply_tier_curve
from app.draft.valuation import value_players
from app.scoring.players import PlayerValue, held_weeks, player_values
from app.scoring.season import SeasonBook
from app.scoring.trade_grades import trade_grades
from app.scoring.verdicts import Verdict, verdict

#: expected(price) = PRICE_INTERCEPT + PRICE_SLOPE * sqrt(price), categories a
#: week. Fitted on 1,206 picks 2019-2026; see the module docstring.
PRICE_INTERCEPT = 0.0504
PRICE_SLOPE = 0.0735

#: Neutral band for draft verdicts: about what doubling a mid-board price buys
#: ($10 to $20 is 0.096 on the curve).
DRAFT_BAND = 0.10

#: Where `scripts/price_scorecard.py` caches ESPN's average auction prices.
PRICE_CACHE = Path("logs/price-cache")


def expected_value(price: float) -> float:
    """Categories a week a pick at this price has usually delivered."""
    return PRICE_INTERCEPT + PRICE_SLOPE * math.sqrt(max(price, 1.0))


@dataclass(frozen=True)
class DraftGrade:
    player_id: int
    name: str
    price: int
    #: The room's board price from that season's projections, or None.
    projected_value: int | None
    #: What he was expected to go for, and where that came from.
    market: int | None
    market_source: str  # "ESPN average", "board" or ""
    #: "kept", "traded", "traded, return unknown" or "dropped". Kept means
    #: held, in any slot, through the last regular-season period.
    outcome: str
    #: Categories a week the asset delivered over the regular season.
    delivered: float
    #: Categories a week; None when there is no projected value to judge by.
    decision: float | None
    result: float
    #: None without a projected value, or when a trade's return is unknown.
    verdict: Verdict | None


def _board(session: Session, season: int) -> dict[int, int]:
    """Board price by ESPN player id, from the season's preseason projections."""
    if projection_problem(season):
        return {}
    book = SeasonBook.load(session, season)
    league_season = book.league_season
    projections = pool.load_projections(session, season)
    priced = apply_tier_curve(
        price_board(
            value_players(projections, pool.season_categories(session, league_season)),
            teams=int(league_season.team_count),
            budget_per_team=int(league_season.auction_budget or 200),
            roster_slots=pool.roster_size_for(league_season),
        ),
        LEAGUE_TIER_CURVE,
    )
    return {
        p.player_id: price
        for p in projections
        if (price := priced.price_of(p.player_id)) is not None
    }


def _espn_average(season: int, cache: Path) -> dict[int, int]:
    path = cache / f"{season}.json"
    if not path.exists():
        return {}
    cards = json.loads(path.read_text())
    return {int(espn_id): round(card["aav"]) for espn_id, card in cards.items() if card.get("aav")}


def draft_grades(
    session: Session,
    season: int,
    team_id: int,
    *,
    book: SeasonBook | None = None,
    price_cache: Path = PRICE_CACHE,
) -> list[DraftGrade]:
    """Every auction pick the team made, most expensive first."""
    book = book or SeasonBook.load(session, season)
    regular_weeks = sum(1 for p in book.periods.values() if not p.is_playoff) or 1
    picks = session.execute(
        select(DraftPick.player_id, DraftPick.bid_amount, Player.name, Player.espn_player_id)
        .join(Player, Player.id == DraftPick.player_id)
        .where(
            DraftPick.league_season_id == book.league_season.id,
            DraftPick.team_id == team_id,
            DraftPick.bid_amount.is_not(None),
        )
    ).all()
    if not picks:
        return []

    board = _board(session, season)
    espn = _espn_average(season, price_cache)
    values: dict[int, PlayerValue] = {
        v.player_id: v for v in player_values(session, season, team_id, book=book)
    }
    sent_in: dict[int, float | None] = {}
    for grade in trade_grades(session, season, team_id, book=book):
        trade = grade.trade
        if not trade.players_out:
            continue
        if not trade.players_in:
            # Traded, but what came back left no trace: the return is unknown.
            for party in trade.players_out:
                sent_in[party.player_id] = None
            continue
        came_back = sum(
            values[p.player_id].regular.team_fit for p in trade.players_in if p.player_id in values
        )
        for party in trade.players_out:
            sent_in[party.player_id] = came_back / len(trade.players_out)
    held_periods = held_weeks(session, book.league_season.id, team_id)

    final_regular = max((n for n, p in book.periods.items() if not p.is_playoff), default=0)
    out = []
    for player_id, price, name, espn_id in picks:
        own = values.get(int(player_id))
        own_value = own.regular.team_fit if own else 0.0
        held = held_periods.get(int(player_id), set())
        if int(player_id) in sent_in:
            returned = sent_in[int(player_id)]
            if returned is None:
                outcome, delivered_total = "traded, return unknown", own_value
            else:
                outcome, delivered_total = "traded", own_value + returned
        elif held and max(held) >= final_regular:
            outcome, delivered_total = "kept", own_value
        else:
            outcome, delivered_total = "dropped", own_value
        delivered = delivered_total / regular_weeks

        projected = board.get(int(espn_id))
        if int(espn_id) in espn:
            market, source = espn[int(espn_id)], "ESPN average"
        elif projected is not None:
            market, source = projected, "board"
        else:
            market, source = None, ""

        par = expected_value(float(price))
        decision = None if projected is None else expected_value(float(projected)) - par
        result = delivered - par
        out.append(
            DraftGrade(
                player_id=int(player_id),
                name=str(name),
                price=int(price),
                projected_value=projected,
                market=market,
                market_source=source,
                outcome=outcome,
                delivered=delivered,
                decision=decision,
                result=result,
                verdict=(
                    None
                    if decision is None or outcome == "traded, return unknown"
                    else verdict(decision, result, band=DRAFT_BAND)
                ),
            )
        )
    return sorted(out, key=lambda g: (-g.price, g.name))
