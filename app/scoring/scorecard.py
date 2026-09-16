"""One team's season, graded: players, draft, trades and the wire.

Assembles the scoring package for a team-season from one loaded
`SeasonBook`, so the season's lines are read once however many grades use
them. This is what the API route and `scripts/scorecard.py` return, and what
the season report reads.

Everything here is a plain value: the numbers are in categories a week, and
each grade that has both lenses carries its verdict sentence
(`app.scoring.verdicts`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Team
from app.scoring.draft import DraftGrade, draft_grades
from app.scoring.league import PUNT_THRESHOLD, category_record
from app.scoring.players import PlayerValue, player_values
from app.scoring.replacement import pickup_values
from app.scoring.season import SeasonBook
from app.scoring.trade_grades import TradeGrade, trade_grades
from app.scoring.wire import WireMove, median_of, wire_grades


@dataclass(frozen=True)
class Scorecard:
    season: int
    team_id: int
    team_name: str
    #: Categories a week a typical pickup added this season.
    replacement: float
    #: Share of regular-season matchups won in each category.
    category_record: dict[str, float]
    #: Categories that look like punts (won under PUNT_THRESHOLD).
    looks_like_punts: tuple[str, ...]
    players: list[PlayerValue]
    draft: list[DraftGrade]
    trades: list[TradeGrade]
    wire: list[WireMove]


def scorecard(session: Session, season: int, team_id: int) -> Scorecard:
    """The whole graded season for one team (`team_id` is our id, not ESPN's)."""
    team = session.get(Team, team_id)
    if team is None:
        raise ValueError(f"no team {team_id}")
    book = SeasonBook.load(session, season)
    record = category_record(session, season, team_id)
    return Scorecard(
        season=season,
        team_id=team_id,
        team_name=str(team.name),
        replacement=median_of(pickup_values(book)),
        category_record=record,
        looks_like_punts=tuple(c for c, rate in record.items() if rate < PUNT_THRESHOLD),
        players=player_values(session, season, team_id, book=book),
        draft=draft_grades(session, season, team_id, book=book),
        trades=trade_grades(session, season, team_id, book=book),
        wire=wire_grades(session, season, team_id, book=book),
    )
