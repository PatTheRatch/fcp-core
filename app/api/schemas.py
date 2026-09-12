"""Response models.

Two shapes are used deliberately. Bounded collections (teams, standings,
categories) return a plain list. Collections that grow with the season
(lineups, game logs, matchups) return a `Page`, so a caller is never handed
an unbounded response by accident.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    """One window onto a collection that can grow without bound."""

    items: list[T]
    total: int = Field(description="Total matching rows, ignoring limit and offset")
    limit: int
    offset: int


class CategoryOut(BaseModel):
    stat_id: int = Field(description="ESPN's own stat id, stable across seasons")
    abbreviation: str
    position: int = Field(description="ESPN's display order, which is not sorted by stat id")


class LeagueOut(BaseModel):
    espn_league_id: int
    seasons: list[int] = Field(description="Every season stored for this league, ascending")


class SeasonSummaryOut(BaseModel):
    season: int
    name: str
    scoring_type: str
    team_count: int


class SeasonOut(SeasonSummaryOut):
    """One season's settings as they were that year, not as they are now."""

    regular_season_periods: int
    total_matchup_periods: int = Field(description="Includes playoffs, so always the larger")
    playoff_team_count: int
    trade_deadline: datetime | None
    categories: list[CategoryOut]


class OwnerOut(BaseModel):
    espn_owner_id: str = Field(description="ESPN's GUID, stable across seasons and leagues")
    display_name: str | None
    first_name: str | None


class TeamOut(BaseModel):
    espn_team_id: int
    name: str
    abbreviation: str | None
    division_name: str | None
    logo_url: str | None
    owners: list[OwnerOut]


class StandingOut(BaseModel):
    """A team's season record, in both of the senses this league has one.

    ESPN reports no matchup record at all, so `matchups_won` and friends are
    derived here by counting winners. The category tallies are what ESPN
    calls wins and losses, and they count categories, not matchups.
    """

    espn_team_id: int
    name: str
    final_standing: int | None
    matchups_won: int
    matchups_lost: int
    matchups_tied: int
    categories_won: int
    categories_lost: int
    categories_tied: int


class MatchupPeriodOut(BaseModel):
    period: int
    is_playoff: bool
    first_scoring_period: int | None = Field(description="First day in this window, inclusive")
    final_scoring_period: int | None = Field(description="Last day in this window, inclusive")
    matchup_count: int


class MatchupCategoryOut(BaseModel):
    abbreviation: str
    value: float = Field(description="A count, or a ratio for percentages: FG% is 0.457")
    result: str | None = Field(description="WIN, LOSS or TIE. Null on a bye and for components")
    is_scored_category: bool = Field(
        description="True when the league scores this statistic, rather than it being a component"
    )


class MatchupSideOut(BaseModel):
    espn_team_id: int
    name: str
    categories_won: int
    statistics: list[MatchupCategoryOut]


class MatchupOut(BaseModel):
    """One pairing. `away` is null for a bye, which ESPN leaves UNDECIDED."""

    id: int
    period: int
    is_playoff: bool
    winner: str
    categories_tied: int
    home: MatchupSideOut
    away: MatchupSideOut | None


class LineupSlotOut(BaseModel):
    """Where a player sat on one day, and what they did while sitting there."""

    scoring_period: int
    player_id: int = Field(description="ESPN player id")
    player_name: str
    slot: str = Field(description="PG, SG, SF, PF, C, G, F, UT, BE, IR or FA")
    started: bool = Field(description="False for BE, IR and FA")
    injured: bool
    injury_status: str | None
    played: bool | None = Field(description="Null when no game line exists for that day")
    points: float | None
    rebounds: float | None
    assists: float | None


class BenchCallOut(BaseModel):
    """A day a benched player outproduced the team's best starter."""

    scoring_period: int
    game_date: datetime | None
    player_name: str
    benched_points: float
    best_starter_points: float
    margin: float


class BenchReportOut(BaseModel):
    """What a team left on its bench, and its worst individual calls."""

    espn_team_id: int
    name: str
    bench_points: float = Field(description="Points scored by benched players who played")
    benched_games_of_20_plus: int
    worst_calls: list[BenchCallOut]


class PlayerGameOut(BaseModel):
    scoring_period: int
    game_date: datetime | None
    opponent: str | None = Field(description="The opposing pro team, never the player's own")
    played: bool = Field(
        description="False when their team had a fixture and they recorded nothing"
    )
    minutes: float | None
    points: float | None
    rebounds: float | None
    assists: float | None
    steals: float | None
    blocks: float | None
    turnovers: float | None
    three_pointers_made: float | None
    field_goals_made: float | None
    field_goals_attempted: float | None
    free_throws_made: float | None
    free_throws_attempted: float | None


class PlayerOut(BaseModel):
    espn_player_id: int
    name: str


class StreakOut(BaseModel):
    """A team's best and worst runs. A tie breaks a run rather than extending it."""

    espn_team_id: int
    name: str
    longest_win_streak: int
    longest_loss_streak: int
    final_streak: int = Field(description="Length of the run the season ended on")
    final_streak_result: str | None = Field(description="WIN or LOSS, null if it ended in a tie")


class CategoryRecordOut(BaseModel):
    abbreviation: str
    stat_id: int
    won: int
    lost: int
    tied: int
    win_rate: float | None = Field(
        description="Share of decided contests won. Null when none were decided"
    )


class TeamCategoryProfileOut(BaseModel):
    """Where a team was strong and where it was not, category by category."""

    espn_team_id: int
    name: str
    categories: list[CategoryRecordOut]


class BenchTotalOut(BaseModel):
    espn_team_id: int
    name: str
    bench_points: float
    benched_games_of_20_plus: int
    benched_appearances: int = Field(description="Benched player-days where they did play")


class NotableMatchupOut(BaseModel):
    period: int
    is_playoff: bool
    winner_name: str
    loser_name: str
    categories_won: int
    categories_lost: int
    categories_tied: int
    margin: int = Field(description="Categories won minus lost, from the winner's side")


class NotableMatchupsOut(BaseModel):
    """The season's most and least lopsided results. Ties appear in neither."""

    sweeps: list[NotableMatchupOut]
    nail_biters: list[NotableMatchupOut]


class OwnerSeasonOut(BaseModel):
    season: int
    team_name: str
    matchups_won: int
    matchups_lost: int
    matchups_tied: int
    final_standing: int | None


class OwnerRecordOut(BaseModel):
    """One person's history, across every season stored.

    Owners are keyed on the GUID ESPN keeps stable across seasons, so this is
    the only view that outlives a team.
    """

    espn_owner_id: str
    display_name: str | None
    matchups_won: int
    matchups_lost: int
    matchups_tied: int
    titles: int = Field(description="Seasons finished in first place")
    seasons: list[OwnerSeasonOut]


class HeadToHeadOut(BaseModel):
    """How two owners have fared against each other, all seasons combined."""

    owner_a: str
    owner_a_name: str | None
    owner_b: str
    owner_b_name: str | None
    a_wins: int
    b_wins: int
    ties: int
    meetings: int
    seasons: list[int]


class LeagueBenchCallOut(BaseModel):
    """A bench call anywhere in the league, so it names the team too."""

    scoring_period: int
    team_name: str
    player_name: str
    benched_points: float
    best_starter_points: float
    margin: float
