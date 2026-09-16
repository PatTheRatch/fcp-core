"""Trade grades: every reconstructed trade a team made, both lenses.

Each trade from `app.scoring.trades.reconstruct_trades` goes through the move
engine (`app.scoring.moves.grade_move`) from the team's side: the players it
received in, the players it gave up out, over the rest of the regular season
and, separately, the playoffs. A two-for-one is settled at replacement level
for the spot it opened or used.

A trade whose other half left no roster trace (`Trade.gradeable` false) is
listed with `part_missing` and no grade. Grading the side that is present
reads as a verdict on the whole deal: The Infirmary's 2026 day-34 trade, with
only LaVine and Powell leaving visible, would have read "Poor call, -1.33
categories a week" when what came back is simply unknown.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.scoring.moves import MoveGrade, grade_move
from app.scoring.replacement import pickup_values
from app.scoring.season import SeasonBook
from app.scoring.trades import Trade, reconstruct_trades
from app.scoring.wire import median_of


@dataclass(frozen=True)
class TradeGrade:
    trade: Trade
    regular: MoveGrade | None
    playoffs: MoveGrade | None
    #: Only one side of the deal was recovered; the grade covers that side.
    part_missing: bool


def trade_grades(
    session: Session, season: int, team_id: int, *, book: SeasonBook | None = None
) -> list[TradeGrade]:
    """Every trade the team made in `season`, oldest first."""
    book = book or SeasonBook.load(session, season)
    replacement = median_of(pickup_values(book))
    out = []
    for trade in reconstruct_trades(session, season, team_id):
        if not trade.gradeable:
            out.append(TradeGrade(trade=trade, regular=None, playoffs=None, part_missing=True))
            continue
        players_in = [p.player_id for p in trade.players_in]
        players_out = [p.player_id for p in trade.players_out]
        grades = {
            playoffs: grade_move(
                book,
                team_id,
                trade.day,
                players_in,
                players_out,
                replacement=replacement,
                playoffs=playoffs,
            )
            for playoffs in (False, True)
        }
        out.append(
            TradeGrade(
                trade=trade, regular=grades[False], playoffs=grades[True], part_missing=False
            )
        )
    return out
