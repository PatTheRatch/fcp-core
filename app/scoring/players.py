"""What each player was worth to a team over a season.

Two numbers per player, per the spec:

- **Team fit (the headline).** What his started lines added to this team's
  expected category wins, week by week, against the season's opponents
  (`SeasonBook.value`). A three-point shooter on a team punting threes adds
  little here, which is the point.
- **League standard (fine print).** The same line against a league-average
  team of that week's length (`app.scoring.league.average_team_line`): the
  average team with his line in it, against the average team without it.
  Comparable across teams, blind to fit.

Regular season and playoffs are separate lines and never blended.

PER WEEK

`per_week` divides the total by the weeks the team **held** him, in any slot,
not the weeks he started. A player benched or hurt while holding a roster
spot cost that spot, so the weeks count; a player who started four weeks and
sat on the bench for ten is worth less a week than one who started all
fourteen at the same level.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyLineupSlot, MatchupPeriod, Player
from app.scoring.league import average_team_line
from app.scoring.lines import CategoryLine
from app.scoring.season import SeasonBook
from app.scoring.value import marginal, period_length


@dataclass(frozen=True)
class LensValue:
    """One stretch (regular season or playoffs) of one player's value."""

    #: Categories added over the stretch, summed over weeks.
    team_fit: float
    league_standard: float
    weeks_started: int
    weeks_held: int

    @property
    def team_fit_per_week(self) -> float:
        return self.team_fit / self.weeks_held if self.weeks_held else 0.0

    @property
    def league_standard_per_week(self) -> float:
        return self.league_standard / self.weeks_held if self.weeks_held else 0.0


@dataclass(frozen=True)
class PlayerValue:
    player_id: int
    name: str
    team_id: int
    regular: LensValue
    playoffs: LensValue


def held_weeks(session: Session, league_season_id: int, team_id: int) -> dict[int, set[int]]:
    """Each player the team held on any day, and the matchup periods it did."""
    rows = session.execute(
        select(DailyLineupSlot.player_id, MatchupPeriod.period)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season_id,
            DailyLineupSlot.team_id == team_id,
            DailyLineupSlot.slot != "FA",
        )
        .distinct()
    ).all()
    out: dict[int, set[int]] = defaultdict(set)
    for player_id, period in rows:
        out[int(player_id)].add(int(period))
    return out


class _Standard:
    """League-standard value: a line inside a league-average team of its week's length."""

    def __init__(self, book: SeasonBook) -> None:
        self.book = book
        self._average: dict[int, CategoryLine] = {}

    def value(self, period: int, line: CategoryLine) -> float:
        if not line.games:
            return 0.0
        found = self.book.periods[period]
        days = period_length(found)
        if days not in self._average:
            average = average_team_line(
                self.book.session, int(self.book.league_season.season), days
            )
            if not average.counts:
                average = average_team_line(self.book.session, int(self.book.league_season.season))
            self._average[days] = average
        return marginal(self._average[days], line, self.book.opponents.for_period(found))


def player_values(
    session: Session, season: int, team_id: int, *, book: SeasonBook | None = None
) -> list[PlayerValue]:
    """Every player the team held in `season`, best regular-season team fit first."""
    book = book or SeasonBook.load(session, season)
    standard = _Standard(book)
    held = held_weeks(session, book.league_season.id, team_id)
    started = book.players(team_id)
    names = {
        int(pid): str(name)
        for pid, name in session.execute(
            select(Player.id, Player.name).where(Player.id.in_(set(held) | set(started)))
        )
    }

    out = []
    for player_id in set(held) | set(started):
        stretches: dict[bool, list[float]] = {False: [0.0, 0.0, 0, 0], True: [0.0, 0.0, 0, 0]}
        for period in started.get(player_id, []):
            stretch = stretches[book.is_playoff(period)]
            stretch[0] += book.value(team_id, period, player_id)
            stretch[1] += standard.value(period, book.player_week(team_id, period, player_id))
            stretch[2] += 1
        for period in held.get(player_id, set()):
            stretches[book.is_playoff(period)][3] += 1
        lenses = {
            playoff: LensValue(
                team_fit=float(s[0]),
                league_standard=float(s[1]),
                weeks_started=int(s[2]),
                weeks_held=max(int(s[3]), int(s[2])),
            )
            for playoff, s in stretches.items()
        }
        out.append(
            PlayerValue(
                player_id=player_id,
                name=names.get(player_id, str(player_id)),
                team_id=team_id,
                regular=lenses[False],
                playoffs=lenses[True],
            )
        )
    return sorted(out, key=lambda v: -v.regular.team_fit)
