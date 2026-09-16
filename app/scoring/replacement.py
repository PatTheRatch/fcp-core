"""Replacement level: what an open roster spot is worth.

A trade that sends two players for one, or a drop, leaves a spot that the
manager fills from the wire. Scoring that spot as worth nothing punishes
every uneven trade by construction, so the spec prices it at a typical
waiver pickup, measured from what this league's managers actually picked up.

THE MEASURE

For every executed add in the season (`transactions` of type WAIVER or
FREEAGENT, status EXECUTED, an ADD item), the added player's started lines
for the adding team over the `WINDOW` days after the move, in the scoring
currency: categories a week his line added to that team's week
(`app.scoring.value.marginal`), averaged over the matchup periods the window
touches. A pickup who never starts is worth zero, which is what an unused
pickup is worth. The typical value is the median, since a few breakout
pickups drag the mean.

Gross, not net of the player dropped: the question is what filling a spot
gives, and the dropped player is accounted for wherever he was graded.

Measured 2026-09-16 on the live database, categories a week:

| season | median | mean | quartiles | adds |
|---|---|---|---|---|
| 2019 | 0.113 | 0.176 | 0.022-0.280 | 599 |
| 2020 | 0.128 | 0.186 | 0.034-0.298 | 543 |
| 2021 | 0.096 | 0.160 | 0.032-0.239 | 758 |
| 2022 | 0.086 | 0.146 | 0.027-0.219 | 648 |
| 2023 | 0.091 | 0.157 | 0.028-0.242 | 475 |
| 2024 | 0.068 | 0.137 | 0.017-0.207 | 789 |
| 2025 | 0.062 | 0.124 | 0.012-0.173 | 924 |
| 2026 | 0.072 | 0.135 | 0.013-0.195 | 973 |

For scale, Kawhi Leonard added 0.77 a week to Through The Wire in 2026. The
value falls as adds rise: the more a league streams, the less each add is.

`WINDOW` is fourteen days, the window `scripts/acquirable_value.py` measured
pickups over: most adds are streamers held for days, and a longer window
mostly measures the rare keeper.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, median, quantiles

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Transaction, TransactionItem
from app.scoring.season import SeasonBook

#: Days after an add over which the pickup's started lines count.
WINDOW = 14

#: Transaction types that fill a roster spot from outside the league.
ADD_TYPES = ("WAIVER", "FREEAGENT")


@dataclass(frozen=True)
class ReplacementValue:
    season: int
    #: Categories a week a typical pickup added: the median over adds.
    value: float
    mean: float
    #: Interquartile range of the per-add values.
    lower_quartile: float
    upper_quartile: float
    #: Adds measured.
    n: int


def pickup_values(book: SeasonBook) -> list[float]:
    """Categories a week each executed add returned in its first `WINDOW` days."""
    session = book.session
    adds = session.execute(
        select(Transaction.scoring_period, TransactionItem.to_team_id, TransactionItem.player_id)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .where(
            Transaction.league_season_id == book.league_season.id,
            Transaction.type.in_(ADD_TYPES),
            Transaction.status == "EXECUTED",
            TransactionItem.item_type == "ADD",
            TransactionItem.to_team_id.is_not(None),
        )
    ).all()

    values = []
    for day, team_id, player_id in adds:
        touched = {
            period
            for offset in range(1, WINDOW + 1)
            if (period := book.period_for_day(int(day) + offset)) is not None
            and not book.is_playoff(period)
        }
        if not touched:
            continue
        values.append(
            fmean(book.value(int(team_id), period, int(player_id)) for period in sorted(touched))
        )
    return values


def replacement_value(session: Session, season: int) -> ReplacementValue:
    """The value of an open roster spot in `season`, in categories a week."""
    values = pickup_values(SeasonBook.load(session, season))
    if not values:
        raise ValueError(f"no executed adds with started lines in {season}")
    lower, _, upper = quantiles(values, n=4) if len(values) > 1 else (values[0],) * 3
    return ReplacementValue(
        season=season,
        value=median(values),
        mean=fmean(values),
        lower_quartile=lower,
        upper_quartile=upper,
        n=len(values),
    )
