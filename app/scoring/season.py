"""One played season, loaded once: every started line, every team's week, the opponents.

The grades all ask the same question many times -- what did this player's
line add to that team in that week -- so the season's started lines are read
in one query and each team's week line is summed once, and the opponent
distributions are built once per period length.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod
from app.scoring.lines import EMPTY, CategoryLine, started_lines
from app.scoring.value import SeasonOpponents, marginal


@dataclass
class SeasonBook:
    session: Session
    league_season: LeagueSeason
    #: (team id, matchup period, player id) -> started line.
    lines: dict[tuple[int, int, int], CategoryLine]
    periods: dict[int, MatchupPeriod]
    opponents: SeasonOpponents
    _team_weeks: dict[tuple[int, int], CategoryLine] = field(default_factory=dict)
    _by_team: dict[int, dict[int, list[int]]] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, season: int) -> SeasonBook:
        league_season = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
        if league_season is None:
            raise ValueError(f"no season {season}")
        periods = {
            int(p.period): p
            for p in session.scalars(
                select(MatchupPeriod).where(MatchupPeriod.league_season_id == league_season.id)
            )
        }
        book = cls(
            session=session,
            league_season=league_season,
            lines=started_lines(session, league_season.id),
            periods=periods,
            opponents=SeasonOpponents(session, league_season),
        )
        weeks: dict[tuple[int, int], CategoryLine] = defaultdict(lambda: EMPTY)
        by_team: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
        for (team, period, player), line in book.lines.items():
            weeks[(team, period)] = weeks[(team, period)] + line
            by_team[team][player].append(period)
        book._team_weeks = dict(weeks)
        book._by_team = {team: dict(players) for team, players in by_team.items()}
        return book

    def team_week(self, team_id: int, period: int) -> CategoryLine:
        return self._team_weeks.get((team_id, period), EMPTY)

    def player_week(self, team_id: int, period: int, player_id: int) -> CategoryLine:
        return self.lines.get((team_id, period, player_id), EMPTY)

    def players(self, team_id: int) -> dict[int, list[int]]:
        """Each player who started for the team, and the periods he did."""
        return self._by_team.get(team_id, {})

    def period_for_day(self, day: int) -> int | None:
        for number, period in self.periods.items():
            first, final = period.first_scoring_period, period.final_scoring_period
            if first is not None and final is not None and first <= day <= final:
                return number
        return None

    def is_playoff(self, period: int) -> bool:
        found = self.periods.get(period)
        return bool(found and found.is_playoff)

    def value(self, team_id: int, period: int, player_id: int) -> float:
        """Categories a week the player's started line added to the team."""
        line = self.player_week(team_id, period, player_id)
        if not line.games:
            return 0.0
        return marginal(
            self.team_week(team_id, period),
            line,
            self.opponents.for_period(self.periods[period]),
        )
