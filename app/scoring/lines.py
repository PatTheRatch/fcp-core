"""The category line a set of players produced while started for a team.

Everything the scoring package grades is built from these lines: what a
player did for a team, in the weeks and on the days he actually counted.

A line is the raw counts (points, rebounds, assists, steals, blocks, threes,
turnovers, and the made and attempted shots behind both percentages), so
lines add and subtract exactly and a percentage is rebuilt from the sums,
never averaged. `CategoryLine.totals` turns one into the per-category totals
the draft optimizer scores (`app.draft.optimizer.score`).

A player counts for a team on a day when his `daily_lineup_slots` row for
that team and day is a starting slot (not BE, IR or FA) and he has a
`player_game_stats` line for the same season and scoring period. The join is
on the day, and a partial match is expected: a rostered player only has a
line on days his NBA team plays (see `scripts/waiver_value.py` for the audit).

Verified 2026-09-16: summing started lines per team for matchup periods 3, 9
and 15 of every season 2019-2026 reproduced ESPN's own weekly team totals in
`matchup_team_stats` (PTS, FGA, TO) exactly on all 294 team-weeks.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    MatchupPeriod,
    PlayerGameStat,
    Team,
)
from app.draft.valuation import PERCENTAGE_COMPONENTS

#: The counts a line carries, keyed as `matchup_team_stats` abbreviates them,
#: mapped to the `player_game_stats` column each is summed from.
COUNTS: Mapping[str, str] = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "3PM": "three_pointers_made",
    "TO": "turnovers",
    "FGM": "field_goals_made",
    "FGA": "field_goals_attempted",
    "FTM": "free_throws_made",
    "FTA": "free_throws_attempted",
}


@dataclass(frozen=True)
class CategoryLine:
    """Raw counts, plus the games and days they came from."""

    counts: Mapping[str, float] = field(default_factory=dict)
    #: Games that contributed (a started day with a stat line).
    games: int = 0

    def __add__(self, other: CategoryLine) -> CategoryLine:
        keys = set(self.counts) | set(other.counts)
        return CategoryLine(
            {k: self.counts.get(k, 0.0) + other.counts.get(k, 0.0) for k in keys},
            self.games + other.games,
        )

    def __sub__(self, other: CategoryLine) -> CategoryLine:
        keys = set(self.counts) | set(other.counts)
        return CategoryLine(
            {k: self.counts.get(k, 0.0) - other.counts.get(k, 0.0) for k in keys},
            self.games - other.games,
        )

    def scaled(self, factor: float) -> CategoryLine:
        """Every count multiplied, e.g. a season line spread over its weeks."""
        return CategoryLine({k: v * factor for k, v in self.counts.items()}, self.games)

    def get(self, key: str) -> float:
        return self.counts.get(key, 0.0)

    def totals(self, categories: Sequence[str]) -> dict[str, float]:
        """Per-category totals, percentages rebuilt from made over attempted.

        A line with no attempts has no percentage; it reads 0.0, the same
        convention as `app.draft.optimizer.roster_totals`.
        """
        out: dict[str, float] = {}
        for category in categories:
            if category in PERCENTAGE_COMPONENTS:
                made, attempted = PERCENTAGE_COMPONENTS[category]
                out[category] = self.get(made) / self.get(attempted) if self.get(attempted) else 0.0
            else:
                out[category] = self.get(category)
        return out


EMPTY = CategoryLine()


def sum_lines(lines: Iterable[CategoryLine]) -> CategoryLine:
    total = EMPTY
    for line in lines:
        total = total + line
    return total


def started_lines(
    session: Session,
    league_season_id: int,
    *,
    team_id: int | None = None,
) -> dict[tuple[int, int, int], CategoryLine]:
    """Every started line of a season, keyed (team id, matchup period, player id).

    One query for the whole season, which is what the grades need: a team's
    week line is the sum of its players', and a player's value is his line
    against the week without it.
    """
    stat_columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    query = (
        select(
            DailyLineupSlot.team_id,
            MatchupPeriod.period,
            DailyLineupSlot.player_id,
            *stat_columns,
        )
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
        .join(
            PlayerGameStat,
            (PlayerGameStat.player_id == DailyLineupSlot.player_id)
            & (PlayerGameStat.season == LeagueSeason.season)
            & (PlayerGameStat.scoring_period == DailyLineupSlot.scoring_period),
        )
        .where(
            LeagueSeason.id == league_season_id,
            DailyLineupSlot.started.is_(True),
            PlayerGameStat.played.is_(True),
        )
    )
    if team_id is not None:
        query = query.where(DailyLineupSlot.team_id == team_id)

    sums: dict[tuple[int, int, int], dict[str, float]] = defaultdict(
        lambda: dict.fromkeys(COUNTS, 0.0)
    )
    games: dict[tuple[int, int, int], int] = defaultdict(int)
    for row in session.execute(query):
        key = (int(row[0]), int(row[1]), int(row[2]))
        bucket = sums[key]
        for index, stat in enumerate(COUNTS, start=3):
            bucket[stat] += float(row[index] or 0.0)
        games[key] += 1
    return {key: CategoryLine(counts, games[key]) for key, counts in sums.items()}


def team_week_line(
    session: Session,
    team_id: int,
    period: int,
    players: Iterable[int] | None = None,
) -> CategoryLine:
    """What `players` (every started player when None) posted for a team in a
    matchup period, while started."""
    team = session.get(Team, team_id)
    if team is None:
        raise ValueError(f"no team {team_id}")
    lines = started_lines(session, team.league_season_id, team_id=team_id)
    wanted = None if players is None else set(players)
    return sum_lines(
        line
        for (_, week, player), line in lines.items()
        if week == period and (wanted is None or player in wanted)
    )


def player_week_line(session: Session, team_id: int, period: int, player_id: int) -> CategoryLine:
    """One player's started line for a team in a matchup period."""
    return team_week_line(session, team_id, period, players=[player_id])
