"""Response models.

Two shapes are used deliberately. Bounded collections (teams, standings,
categories) return a plain list. Collections that grow with the season
(lineups, game logs, matchups) return a `Page`, so a caller is never handed
an unbounded response by accident.
"""

from datetime import date, datetime
from typing import Any

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
    """A league member.

    `owner_id` is this database's own id, not ESPN's. ESPN identifies an
    owner by their SWID GUID, which is half of the cookie pair that
    authenticates a real ESPN account, so it is a join key and never a
    response field. The opaque id is stable and correlates across every
    endpoint here, which is all a caller needs it for.
    """

    owner_id: int
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

    Identity persists across seasons, so this is the only view that outlives
    a team. See `OwnerOut` for why the id here is ours and not ESPN's.
    """

    owner_id: int
    display_name: str | None
    matchups_won: int
    matchups_lost: int
    matchups_tied: int
    titles: int = Field(description="Seasons finished in first place")
    seasons: list[OwnerSeasonOut]


class HeadToHeadOut(BaseModel):
    """How two owners have fared against each other, all seasons combined."""

    owner_a: int
    owner_a_name: str | None
    owner_b: int
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


class IngestRunOut(BaseModel):
    """One ingest execution. A row still 'running' means the process died."""

    id: int
    season: int
    mode: str = Field(
        description=(
            "'full' rewrites the season, 'recent' the trailing days, 'settings' next "
            "season's rules, 'status' a listener pass"
        )
    )
    status: str = Field(description="'running', 'succeeded' or 'failed'")
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    error: str | None
    detail: dict[str, Any] = Field(description="Row counts and the scope the run covered")


class IngestHealthOut(BaseModel):
    """Whether the schedule is actually keeping the current season current."""

    season: int
    mode: str | None = Field(
        default=None, description="When set, only runs of this mode were counted"
    )
    last_success_at: datetime | None
    hours_since_last_success: float | None
    last_status: str | None = Field(description="Status of the most recent run, successful or not")
    stale: bool = Field(description="True when no run has succeeded within the staleness window")
    staleness_threshold_hours: int


class TransactionItemOut(BaseModel):
    """One player moving. A null team on either side means free agency."""

    player_id: int = Field(description="ESPN player id")
    player_name: str
    item_type: str = Field(description="ADD, DROP or TRADE")
    from_team: str | None
    to_team: str | None


class TransactionOut(BaseModel):
    """A waiver claim, a pickup or a trade.

    Failed and cancelled moves are included. A losing bid records who wanted
    a player and what they offered, which a successful claim never shows.
    """

    id: int
    scoring_period: int
    processed_at: datetime | None
    team: str | None
    type: str = Field(description="WAIVER, FREEAGENT, TRADE_ACCEPT and so on")
    status: str | None = Field(description="EXECUTED, CANCELED, PENDING or a FAILED_* reason")
    bid_amount: int | None = Field(description="FAAB offered. Meaningful even when it failed")
    items: list[TransactionItemOut]


class ContestedClaimOut(BaseModel):
    """A player several teams bid on, on the same day."""

    scoring_period: int
    player_name: str
    winning_team: str | None
    winning_bid: int | None
    losing_bids: int = Field(description="How many other teams bid and did not get him")
    highest_losing_bid: int | None


class DraftPickOut(BaseModel):
    """One pick. `paid` is the auction price, not a FAAB bid."""

    round_num: int
    round_pick: int
    player_id: int = Field(description="ESPN player id")
    player_name: str
    team: str | None
    nominated_by: str | None = Field(description="Who put the player up, often not the buyer")
    paid: int | None
    keeper: bool


class DraftValueOut(BaseModel):
    """What a pick cost against what the player went on to produce."""

    player_name: str
    team: str | None
    paid: int
    season_points: float
    points_per_dollar: float
    games_played: int


class ProjectionGapOut(BaseModel):
    """A player against ESPN's preseason forecast for them."""

    player_name: str
    drafted_by: str | None
    paid: int | None = Field(description="Auction price, null if undrafted")
    projected_points: float
    actual_points: float
    difference: float = Field(description="Actual minus projected. Negative is a miss")
    projected_games: float | None
    actual_games: float | None


class VerdictOut(BaseModel):
    """Both lenses of a grade, in categories a week, with the sentence."""

    label: str
    text: str
    decision: float
    result: float


class LensValueOut(BaseModel):
    team_fit: float
    team_fit_per_week: float
    league_standard: float
    league_standard_per_week: float
    weeks_started: int
    weeks_held: int


class PlayerValueOut(BaseModel):
    player_id: int
    name: str
    regular: LensValueOut
    playoffs: LensValueOut


class DraftGradeOut(BaseModel):
    player_id: int
    name: str
    price: int
    projected_value: int | None
    market: int | None
    market_source: str
    outcome: str
    delivered: float
    decision: float | None
    result: float
    verdict: VerdictOut | None


class MoveGradeOut(BaseModel):
    """A trade or wire move over one stretch (regular season or playoffs)."""

    periods: list[int]
    decision: float
    result: float
    verdict: VerdictOut


class TradeGradeOut(BaseModel):
    day: int
    counterparties: list[str]
    players_in: list[str]
    players_out: list[str]
    part_missing: bool
    regular: MoveGradeOut | None
    playoffs: MoveGradeOut | None


class WireMoveOut(BaseModel):
    day: int
    kind: str
    added: list[str]
    dropped: list[str]
    regular: MoveGradeOut | None
    playoffs: MoveGradeOut | None


class ScorecardOut(BaseModel):
    """One team's season in categories a week: see docs/scoring/SPEC.md."""

    season: int
    espn_team_id: int
    team_name: str
    replacement: float
    category_record: dict[str, float]
    looks_like_punts: list[str]
    players: list[PlayerValueOut]
    draft: list[DraftGradeOut]
    trades: list[TradeGradeOut]
    wire: list[WireMoveOut]


class StatusEventOut(BaseModel):
    """One change the listener saw between two passes (app/listener/events.py)."""

    id: int
    espn_player_id: int
    player_name: str
    season: int
    kind: str
    observed_at: datetime
    previous: dict[str, Any] = Field(description="The fields that changed, before")
    current: dict[str, Any] = Field(description="The same fields, after")
    detail: dict[str, Any] = Field(description="What the rule computed, e.g. minutes means")
    notified_at: datetime | None = Field(description="When the digest reported it, if it has")


class StatusSnapshotOut(BaseModel):
    """A player's status and ownership as ESPN reported it on one pass."""

    season: int
    observed_at: datetime
    pass_label: str
    injury_status: str | None
    injured: bool
    expected_return_date: date | None
    pro_team_id: int | None
    on_team_id: int | None = Field(description="Fantasy team holding him, 0 when unrostered")
    status: str | None = Field(description="ONTEAM, FREEAGENT or WAIVERS")
    percent_owned: float | None
    percent_change: float | None = Field(description="24-hour move across all ESPN leagues")
    percent_started: float | None
    auction_value_average: float | None


class PlayerNewsOut(BaseModel):
    published: datetime
    headline: str
    story: str
    source: str
    seen_at: datetime
