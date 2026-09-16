"""Wire grades: every executed pickup and drop a team made.

Each executed WAIVER or FREEAGENT transaction is one move: the players it
added to the team in, the players it dropped from the team out, graded by
`app.scoring.moves.grade_move` for the rest of the regular season, and again
for the playoffs when the move's incoming players were still held then.
A drop with no add opens a spot, worth replacement level; an add with no drop
uses one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, Transaction, TransactionItem
from app.scoring.moves import MoveGrade, grade_move
from app.scoring.replacement import pickup_values
from app.scoring.season import SeasonBook

#: Transaction types that move players between a team and the wire.
WIRE_TYPES = ("WAIVER", "FREEAGENT")


@dataclass(frozen=True)
class WireMove:
    transaction_id: int
    day: int
    kind: str
    added: tuple[str, ...]
    dropped: tuple[str, ...]
    regular: MoveGrade | None
    playoffs: MoveGrade | None


def median_of(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def wire_grades(
    session: Session, season: int, team_id: int, *, book: SeasonBook | None = None
) -> list[WireMove]:
    """Every executed wire move by the team, in date order."""
    book = book or SeasonBook.load(session, season)
    replacement = median_of(pickup_values(book))
    rows = session.execute(
        select(
            Transaction.id,
            Transaction.scoring_period,
            Transaction.type,
            TransactionItem.item_type,
            TransactionItem.player_id,
            TransactionItem.from_team_id,
            TransactionItem.to_team_id,
            Player.name,
        )
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .join(Player, Player.id == TransactionItem.player_id)
        .where(
            Transaction.league_season_id == book.league_season.id,
            Transaction.type.in_(WIRE_TYPES),
            Transaction.status == "EXECUTED",
        )
        .order_by(Transaction.scoring_period, Transaction.id)
    ).all()

    moves: dict[int, dict[str, object]] = {}
    ins: dict[int, list[tuple[int, str]]] = defaultdict(list)
    outs: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for tx, day, kind, item, player_id, source, destination, name in rows:
        if item == "ADD" and destination == team_id:
            ins[tx].append((int(player_id), str(name)))
        elif item == "DROP" and source == team_id:
            outs[tx].append((int(player_id), str(name)))
        else:
            continue
        moves.setdefault(tx, {"day": int(day), "kind": str(kind)})

    out = []
    for tx, meta in moves.items():
        day = int(str(meta["day"]))
        added = [pid for pid, _ in ins[tx]]
        dropped = [pid for pid, _ in outs[tx]]
        grades = {
            playoffs: grade_move(
                book, team_id, day, added, dropped, replacement=replacement, playoffs=playoffs
            )
            for playoffs in (False, True)
        }
        out.append(
            WireMove(
                transaction_id=tx,
                day=day,
                kind=str(meta["kind"]),
                added=tuple(name for _, name in ins[tx]),
                dropped=tuple(name for _, name in outs[tx]),
                regular=grades[False],
                playoffs=grades[True],
            )
        )
    return sorted(out, key=lambda m: (m.day, m.transaction_id))
