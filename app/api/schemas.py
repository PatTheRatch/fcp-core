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
    name: str | None = Field(
        default=None, description="The newest stored season's name; null before the first ingest"
    )
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
    #: The rest of the stored line, so a page can print a box score rather
    #: than three of its nine numbers. Null on a day with no line, and on a
    #: row served before these fields existed.
    minutes: float | None = None
    steals: float | None = None
    blocks: float | None = None
    turnovers: float | None = None
    three_pointers_made: float | None = None
    field_goals_made: float | None = None
    field_goals_attempted: float | None = None
    free_throws_made: float | None = None
    free_throws_attempted: float | None = None


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


class RungOut(BaseModel):
    """One step of the ladder: a chance, and the dollar that buys it."""

    asked: float = Field(description="The chance this rung asked for: 0.50, 0.75 or 0.90")
    amount: int
    win_chance: float = Field(description="The chance at the dollar actually offered")
    n: int = Field(description="Claim events the chance was read off")


class BidOut(BaseModel):
    """What to pay for a claim, what he is worth, and where both came from."""

    amount: int
    rank: int = Field(description="The added player's value rank on the wire today")
    bucket: str
    basis: str = Field(description='"median" or "75th percentile"')
    uncapped: float = Field(description="That statistic, before the caps")
    low: int
    high: int
    sample: int = Field(description="Winning bids in the bucket")
    capped_by: str | None
    note: str
    worth_dollars: int = Field(
        default=0, description="What the move is worth to this roster, at the budget's own rate"
    )
    ceiling: int = Field(
        default=0, description="The dollar above which the move stops clearing the bar"
    )
    rate: float = Field(default=0.0, description="What one dollar costs, categories a week")
    rate_note: str = Field(default="", description="Where that rate came from")
    ladder: list[RungOut] = Field(default_factory=list, description="A chance and its dollar")


class PickupPlayerOut(BaseModel):
    """One player as the recommender sees him."""

    espn_player_id: int
    name: str
    pro_team_id: int
    pro_team: str | None = Field(
        default=None,
        description=(
            "His NBA team's abbreviation, e.g. DET. The mark a page sets beside his name, "
            "so no reader has to carry ESPN's team table; null for a stored report built "
            "before the field existed, or a team id ESPN has no abbreviation for"
        ),
    )
    position: str | None
    injury_status: str | None
    expected_return_date: date | None
    games_remaining: int = Field(description="Games left in the window the report covers")
    on_ir: bool
    waiver_clears_at: date | None = Field(
        default=None, description="The day he clears waivers, when the league has him on waivers"
    )
    waiver_clears_on: int | None = Field(
        default=None,
        description="That day as a scoring period; he cannot play for us before it",
    )


class CategoryShiftOut(BaseModel):
    abbreviation: str
    before: float = Field(description="Probability of winning the category before the move")
    after: float
    delta: float


class JudgementOut(BaseModel):
    """A move in one currency over both horizons (`app.pickups.judge`).

    `delta_total` is what the ranking and the hurdle read: the change in this
    week's matchup plus the change per week over the rest of the season, times
    the weeks left. The projected records are the season's category record as
    it would end, with the move and without it.
    """

    delta_week: float = Field(description="Change in expected categories won this matchup")
    delta_season_per_week: float = Field(description="Change in an ordinary week from then on")
    weeks_remaining: float = Field(description="Matchup weeks after this one")
    delta_total: float = Field(description="The net, in categories")
    per_week: float = Field(description="The net over the weeks it covers")
    replacement: float = Field(description="What the wire gives a place back, categories a week")
    banked_won: float = Field(description="Categories won in settled matchups so far")
    banked_lost: float
    record_without: list[float] = Field(description="Projected [won, lost] with no move")
    record_with: list[float]
    measured: bool = Field(
        description="False when the season has posted nothing to measure a league standard against"
    )


class FinishWeekOut(BaseModel):
    """One remaining matchup, with the change and without it."""

    period: int
    days_remaining: int
    in_play: bool = Field(description="The period being played now")
    opponent_espn_team_id: int | None = Field(description="Null on a bye")
    opponent_name: str | None
    expected_before: float = Field(description="Expected categories won, as things stand")
    expected_after: float
    delta: float


class FinishOut(BaseModel):
    """Where a change leaves one team (`app.inseason.what_if`).

    The projected-standings engine read twice -- as the league stands, and
    with the change made -- on the same seed and the same simulation count,
    every other roster untouched. A second lens and never a second bar:
    nothing is labelled, recommended or refused on these numbers, and
    `calibration_note` is the published record of the forecast behind them.
    """

    espn_team_id: int
    team_name: str
    record_before: list[float] = Field(description="Projected final [categories won, lost]")
    record_after: list[float]
    matchups_before: list[float] = Field(description="Mean simulated [won, lost, tied]")
    matchups_after: list[float]
    place_before: int = Field(description="Row in the projected table, 1 for first")
    place_after: int
    playoff_odds_before: float
    playoff_odds_after: float
    bye_odds_before: float | None = Field(description="Null where the format gives no bye")
    bye_odds_after: float | None
    seed_odds_before: list[float] = Field(description="P(each finishing place), first place first")
    seed_odds_after: list[float]
    weeks: list[FinishWeekOut]
    n_sims: int
    seed: int
    odds_band: float = Field(
        description="The 95% sampling band on one playoff-odds figure, from the simulation alone"
    )
    readable: bool = Field(description="False when the odds moved by less than that band")
    noise_note: str
    calibration_note: str = Field(description="What this forecast scored on a replayed season")
    language: str = Field(description="That the finish is a second lens and not a second bar")


class StreamMoveOut(BaseModel):
    """One move and what it does to the week (docs/pickups.md section 4.3)."""

    kind: str = Field(description="swap, add or ir_move")
    add: PickupPlayerOut
    drop: PickupPlayerOut | None
    to_ir: PickupPlayerOut | None
    delta: float = Field(description="Change in expected categories won this period")
    net: float = Field(description="The judgement's net over both horizons; the ranking reads it")
    judgement: JudgementOut
    add_starts: int
    drop_starts: int
    fills_empty_day: bool
    clears_hurdle: bool
    moved: list[CategoryShiftOut] = Field(description="Categories the move changed")
    bid: BidOut | None


class EmptyDayOut(BaseModel):
    scoring_period: int
    empty_slots: list[str]
    fillers: list[PickupPlayerOut]


class ScheduleManOut(PickupPlayerOut):
    """A man with a game on a day of the schedule, and whether he starts."""

    seated: bool = Field(
        description="Whether the lineup can seat him that day; false is a game that will not count"
    )


class SideGamesOut(BaseModel):
    """One side's games on one day, or its total over the days left."""

    games: int = Field(
        description=(
            "Men on that roster with a game that day who are not ruled out of it; injured "
            "reserve is left out, and an OUT man counts no day before his expected return"
        )
    )
    seated: int = Field(
        description=(
            "How many of those games the lineup can start, by the same seating the week is "
            "projected from. Fewer than `games` when there are more games than places"
        )
    )
    open_places: int = Field(
        description="Starting places no man of this roster can fill; slot-days on a total"
    )
    men: list[ScheduleManOut] = Field(
        default_factory=list,
        description=(
            "Every man the `games` count counted, best first, each marked with whether he "
            "got a place. Empty on a total"
        ),
    )


class ScheduleDayOut(BaseModel):
    scoring_period: int
    mine: SideGamesOut
    theirs: SideGamesOut | None = Field(default=None, description="Null on a bye")


class ScheduleOut(BaseModel):
    """The week's games day by day, read off the projection itself.

    Only the days still to play: a day already played is in the matchup
    period's own days and not here. Empty on a report stored before the
    field existed.
    """

    days: list[ScheduleDayOut] = Field(default_factory=list)
    mine_total: SideGamesOut = Field(
        default_factory=lambda: SideGamesOut(games=0, seated=0, open_places=0)
    )
    theirs_total: SideGamesOut | None = None


class GlanceOut(BaseModel):
    """A team's week at a glance: what the free This week page shows its
    manager, from the week report, without the plan."""

    espn_team_id: int
    matchup_period: int
    opponent_espn_team_id: int | None = Field(description="Null on a bye")
    expected_wins: float = Field(description="Categories expected to be won this week, of 9")
    probabilities: dict[str, float] = Field(description="Each category's chance this week")
    record_without: list[float] = Field(
        description="The season's projected record in categories, won and lost, with no move"
    )
    stored: bool = Field(description="Read from the morning's stored report, not built now")


class BoxScoreOut(BaseModel):
    """One man's stored line for one day, in raw counts.

    The counts and not the two rates, so FG% and FT% are rebuilt from the
    made and attempted under them the way every other total on these pages
    is (`app/scoring/lines.py`). A stored fact about a day that has been
    played, not an estimate of one.
    """

    scoring_period: int
    minutes: float
    points: float
    rebounds: float
    assists: float
    steals: float
    blocks: float
    three_pointers_made: float
    turnovers: float
    field_goals_made: float
    field_goals_attempted: float
    free_throws_made: float
    free_throws_attempted: float


class PostedManOut(BaseModel):
    """One man's share of what his side has posted this matchup period.

    Every man whose started line is in the score, including one the team has
    since dropped: the table these rows draw has to add up to the score above
    it, so nobody in the score may be missing from it.
    """

    espn_player_id: int
    name: str
    games: int = Field(description="Days he started and produced a line, before `today`")
    minutes: float
    line: dict[str, float] = Field(
        description="His raw counts over those days, keyed as the nine are"
    )


class StreamReportOut(BaseModel):
    """Who to stream this week, and whether anyone is worth a look."""

    espn_team_id: int
    matchup_period: int
    scoring_periods_remaining: list[int]
    opponent_espn_team_id: int | None = Field(description="Null on a bye")
    expected_wins: float
    probabilities: dict[str, float]
    projected: dict[str, float] = Field(description="Raw counts, the week as projected")
    opponent_projected: dict[str, float]
    posted: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Raw counts, the score as it stands: what this side has posted in the period "
            "by the morning of `today`, under the same live/replay rule the projection "
            "uses (`app.pickups.state`). `projected` is this plus the days still to play. "
            "Empty on a report stored before the field existed"
        ),
    )
    opponent_posted: dict[str, float] = Field(default_factory=dict)
    posted_source: str = Field(
        default="",
        description=(
            "Where the two totals above came from: `espn` (ESPN's own running matchup "
            "row, kept on a live morning because it carries stat corrections ours may "
            "not) or `box_scores` (our stored started lines on the period's days before "
            "`today`). Empty on a report stored before the field existed"
        ),
    )
    posted_men: list[PostedManOut] = Field(
        default_factory=list,
        description=(
            "The score broken out a man at a time, always from the stored box scores, "
            "best first by games then points. On `box_scores` these add up to `posted` "
            "exactly; on `espn` they can fall short of it by whatever ESPN has counted "
            "and the ingest has not yet stored"
        ),
    )
    opponent_posted_men: list[PostedManOut] = Field(default_factory=list)
    moves: list[StreamMoveOut]
    recommended: list[StreamMoveOut] = Field(
        description=(
            "The plan: independent moves worth a look today, in order, each clearing the "
            "bar on its own. Empty when nothing clears it and when no adds are left"
        )
    )
    empty_days: list[EmptyDayOut]
    schedule: ScheduleOut = Field(
        default_factory=ScheduleOut,
        description=(
            "Games and starts day by day for both sides, over the days left. Empty on a "
            "report stored before the field existed"
        ),
    )
    outlook: JudgementOut = Field(description="The season as it stands, with no move")
    hurdle: float
    hurdle_source: str = Field(
        default="default",
        description=(
            "Where the bar came from: `owner` (this league's manager set it), `measured` "
            "(this league's own backtest), `pooled` (leagues like this one) or `default`"
        ),
    )
    hurdle_note: str = Field(
        default="",
        description=(
            "The one sentence a page prints under the bar, the way a projection's "
            "`source_note` is printed: 'measured on this league, 616 decision points'"
        ),
    )
    pool_size: int = Field(description="Free agents evaluated")
    historical_wire: bool = Field(
        description=(
            "True when the wire was rebuilt from what was played rather than read from the "
            "listener's snapshots, which is every played season. A narrower wire: it cannot "
            "see a free agent who did not play, and it knows nothing about waivers"
        )
    )
    faab_remaining: int = Field(description="The pot left as of `today`, never below zero")
    faab_overspent: int = Field(
        description=(
            "How far our sum of ESPN's bid feed ran past the budget, normally 0. Non-zero "
            "means the feed and ESPN's own ledger disagree and the pot should be read as spent"
        )
    )
    open_slots: int
    ir_slot_free: bool
    adds_used: int = Field(description="Executed adds this matchup period")
    adds_budget: int = Field(description="Adds the period allows: one for each of its days")
    adds_left: int


class SeasonSwapOut(BaseModel):
    """One move and what it does to an ordinary week (section 4.4)."""

    kind: str = Field(description="free_add, swap or two_swap")
    out: list[PickupPlayerOut]
    into: list[PickupPlayerOut]
    delta: float = Field(description="Change in expected categories won per week")
    net: float = Field(description="The judgement's net over both horizons")
    judgement: JudgementOut
    costs_faab: bool
    hurdle: float = Field(description="The bar, in categories a week")
    clears_hurdle: bool
    moved: list[CategoryShiftOut]
    bid: BidOut | None


class DropCandidateOut(BaseModel):
    """A roster member and what replacing him with the best free agent buys."""

    player: PickupPlayerOut
    replacement: PickupPlayerOut | None
    delta: float


class StashCandidateOut(BaseModel):
    player: PickupPlayerOut
    expected_return_date: date
    weeks_away: float
    healthy_rank: int = Field(description="Where his healthy value would rank on the wire")
    needs_drop: bool = Field(description="False when the injured-reserve slot is free")


class VolumeGuardOut(BaseModel):
    adds: int
    days: int
    finding: str


class SeasonReportOut(BaseModel):
    """Who to hold for the rest of the year, and who should go."""

    espn_team_id: int
    today: int
    last_scoring_period: int
    weeks_remaining: float
    total_weeks: float
    expected_wins: float
    probabilities: dict[str, float]
    weekly: dict[str, float] = Field(description="The roster's own weekly category totals")
    best_add: SeasonSwapOut | None
    best_swap: SeasonSwapOut | None
    best_two_swap: SeasonSwapOut | None
    recommended: SeasonSwapOut | None = Field(description="Null when nothing clears its bar")
    drops: list[DropCandidateOut]
    stashes: list[StashCandidateOut]
    churn: VolumeGuardOut
    outlook: JudgementOut = Field(description="The season as it stands, with no move")
    hurdle_paid: float
    hurdle_free: float
    hurdle_source: str = Field(
        default="default",
        description=(
            "Where the two bars came from: `owner`, `measured`, `pooled` or `default` "
            "(`app.calibration`, docs/intake.md)"
        ),
    )
    hurdle_note: str = Field(
        default="",
        description="The one sentence a page prints under them",
    )
    pool_size: int = Field(description="Free agents evaluated")
    historical_wire: bool = Field(
        description=(
            "True when the wire was rebuilt from what was played rather than read from the "
            "listener's snapshots, which is every played season. A narrower wire: it cannot "
            "see a free agent who did not play, and it knows nothing about waivers"
        )
    )
    faab_remaining: int = Field(description="The pot left as of `today`, never below zero")
    faab_overspent: int = Field(
        description=(
            "How far our sum of ESPN's bid feed ran past the budget, normally 0. Non-zero "
            "means the feed and ESPN's own ledger disagree and the pot should be read as spent"
        )
    )
    open_slots: int
    ir_slot_free: bool
    adds_used: int = Field(description="Executed adds in the matchup period `today` falls in")
    adds_budget: int = Field(description="Adds the period allows: one for each of its days")
    adds_left: int


class TodayGameOut(BaseModel):
    """The game a player's NBA team plays today, from the stored schedule."""

    opponent_pro_team_id: int
    opponent: str = Field(description="The opponent's abbreviation, e.g. MIL")
    home: bool
    describe: str = Field(description='The whole thing in three words: "at MIL"')
    at: datetime | None = Field(
        default=None,
        description=(
            "Tip-off, from the stored schedule. A moment and not a clock: every reader "
            "prints it in its own zone. Null for a stored report built before the field"
        ),
    )


class TodayPlayerOut(PickupPlayerOut):
    """One man the team holds, as today sees him.

    The pickup shape with the day's three extra facts on it, rather than the
    pickup shape nested inside a wrapper: a lineup grid reads a name once
    per place, and `place.player.player.name` is a sentence nobody should
    have to write.
    """

    game: TodayGameOut | None = Field(description="Null when his NBA team does not play today")
    status: str = Field(description="healthy, injured (with the return date when ESPN gives one)")
    plays: bool = Field(
        description="A game today ESPN has not ruled him out of, so he can be started"
    )
    line: BoxScoreOut | None = Field(
        default=None,
        description=(
            "What he actually did today, once the ingest has stored it. Null until then, "
            "and on a report stored before the field existed. Display only: nothing in the "
            "seating or the projection reads it"
        ),
    )


class TodaySeatOut(BaseModel):
    """One place in the lineup and the man in it."""

    slot: str
    player: TodayPlayerOut | None = Field(description="Null when nobody can fill the place")


class TodayBenchedOut(BaseModel):
    """A man with a game the proposed lineup has no room for, and why."""

    player: TodayPlayerOut
    reason: str = Field(description="no_slot (he fits no starting place) or outranked")
    behind: list[TodayPlayerOut] = Field(
        description="The men seated in the places he fits, best first; empty for no_slot"
    )


class TodayFixOut(BaseModel):
    """A place in the set lineup that will produce nothing tonight: the man
    in it has no game, or (`seat.player` null) it was left unset."""

    seat: TodaySeatOut
    instead: list[TodayPlayerOut] = Field(
        description="Men on its bench with a game who fit that very place, best first"
    )


class TodayReportOut(BaseModel):
    """Who starts today, against who the team is actually set to start.

    The week report's own seating for one day (`app.pickups.today`), with the
    places named. It reads nothing after `today`: a man's games remaining is
    0 or 1, and no row of the schedule for a later day reaches the answer.
    """

    espn_team_id: int
    today: int = Field(description="The scoring period reported on")
    calendar_date: date | None = Field(
        description="The day it falls on; null with no stored schedule. Not `date`, which\n"
        " would shadow the type the fields beside it are annotated with"
    )
    matchup_period: int
    teams_playing: int = Field(
        description="NBA teams with a game today. Zero on a day like the All-Star break"
    )
    lineup: list[TodaySeatOut] = Field(
        description="The proposed lineup, one entry per place, in the league's slot order"
    )
    starts: int = Field(
        description="Places the proposal fills, which is the most the roster can fill today"
    )
    actual_starts: int = Field(
        description="Places the set lineup fills with a man who is playing; 0 when unknown"
    )
    benched: list[TodayBenchedOut]
    idle: list[TodayPlayerOut] = Field(description="Men held who cannot be started today")
    injured_reserve: list[TodayPlayerOut]
    actual_known: bool = Field(
        description="Whether the stored lineup days carry today's lineup yet"
    )
    actual: list[TodaySeatOut] = Field(description="What the team has set; empty when unknown")
    fix: list[TodayFixOut] = Field(
        description="Places set with a man who is not playing while the bench has one who is"
    )
    projected: dict[str, float] = Field(
        description="Raw counts the proposed lineup projects to add today"
    )
    actual_projected: dict[str, float] = Field(description="The same for the lineup that is set")
    edge: float = Field(
        description=(
            "What the proposal is worth over what is set, in the currency the seating orders "
            "by: each count over its category's weekly spread, turnovers against. Zero when "
            "the two lineups agree and when today's is not stored"
        )
    )
    source_note: str = Field(description="Where the numbers came from, in the page's words")


class TradeReadinessOut(BaseModel):
    """Whether the season has anything to judge a trade from, and what is missing.

    A season before its draft -- no rosters, no schedule -- is not an error:
    there is simply nothing to judge yet, and the page says so in a sentence
    rather than drawing a broken builder (docs/trades.md, "The page").
    """

    ready: bool
    missing: list[str] = Field(description="What the season lacks; empty when it is ready")
    note: str | None = Field(description="The same thing as one sentence for a reader")


class TradeRosterPlayerOut(BaseModel):
    """One man on a roster, as the two pickers draw him."""

    espn_player_id: int
    name: str
    position: str | None
    pro_team_id: int
    injury_status: str | None
    expected_return_date: date | None
    on_ir: bool = Field(description="On injured reserve: he holds no active place")
    games_remaining: int = Field(description="Games left in the matchup period `today` falls in")


class TradeRosterOut(BaseModel):
    """One team's roster as of the day asked for, and the room it has."""

    espn_team_id: int
    team_name: str
    ours: bool = Field(description="Whether this is the team whose page asked")
    open_slots: int = Field(description="Active places not held, injured reserve aside")
    ir_slot_free: bool
    players: list[TradeRosterPlayerOut]


class TradeRostersOut(BaseModel):
    """What the builder needs to name a deal: who is on each roster today."""

    season: int
    today: int = Field(description="The scoring period the rosters are read as of")
    today_date: date | None = Field(description="Null when no NBA schedule is stored")
    readiness: TradeReadinessOut
    teams: list[TradeRosterOut] = Field(description="Ours first; empty when nothing is stored")
    calibration_note: str = Field(
        description="The published record of the headline number, printed verbatim by the page"
    )


class TradePlayerOut(BaseModel):
    """A player in the deal, and what the judgement about him rests on."""

    espn_player_id: int
    name: str
    value: float = Field(description="Categories a week his roster place is worth, league standard")
    games_left: int
    playoff_games: int
    injury_status: str | None
    expected_return_date: date | None
    games_so_far: int = Field(description="Games of his own behind the knowable line")
    had_projection: bool
    projection_source: str = Field(description='"blend" or "snapshot"')
    thin: bool = Field(description="Fewer games of his own than the report trusts a rate on")
    hurt: bool


class PlayerCardOut(BaseModel):
    """One player's card: what every in-season page shows on his name.

    The same numbers the page it was opened from is drawn from, never a second
    computation of them (`app.inseason.card`): the per-game line under his
    projection, an ordinary week of it from here on, the games he has left and
    the games he has in the playoff weeks, whether he is hurt and when he is
    back, and how many games of his own stand behind the rate.

    What he is worth a week is deliberately not here: it needs the league's
    measured category spreads, which take about two seconds to build, and a
    card is a hover. The pages that show that number show it beside the name.
    """

    espn_player_id: int
    name: str
    position: str | None
    pro_team_id: int
    pro_team: str | None = Field(description='ESPN\'s abbreviation ("DET"); null when unknown')
    injury_status: str | None
    expected_return_date: date | None
    hurt: bool
    today: int = Field(description="The day it is read as of; nothing after it is read")
    last_scoring_period: int
    games_left: int = Field(description="Games he is not ruled out of, through the last day")
    playoff_games: int
    playoff_first: int | None
    playoff_last: int | None
    games_so_far: int = Field(description="Games of his own behind the knowable line")
    had_projection: bool
    projection_source: str = Field(description='"blend" or "snapshot"')
    thin: bool = Field(description="Fewer games of his own than a rate can be trusted on")
    per_game: dict[str, float] = Field(description="The nine per game, percentages as rates")
    weekly: dict[str, float] = Field(description="The same over an ordinary week from here on")


class TradeCategoryOut(BaseModel):
    """One category before and after, in counts and in the chance of winning it."""

    abbreviation: str
    before: float
    after: float
    delta: float
    p_before: float
    p_after: float
    p_delta: float
    moved: bool = Field(description="Whether the chance of winning it moved by a point or more")


class TradePlayoffsOut(BaseModel):
    """The same deal counted over the playoff matchup periods alone."""

    first_scoring_period: int | None
    last_scoring_period: int | None
    weeks: float
    games: int = Field(description="Games the men in the deal have scheduled in the window")
    delta_per_week: float
    delta_total: float
    categories: list[TradeCategoryOut]
    note: str | None = Field(description="Why the lens says nothing, when it says nothing")
    measurable: bool


class TradeSideOut(BaseModel):
    """One team's side of the trade, in full.

    The other side is judged with the same machinery on our own projections:
    it is our estimate of his roster's needs, and never his opinion.
    """

    espn_team_id: int
    team_name: str
    receives: list[TradePlayerOut]
    gives: list[TradePlayerOut]
    drops: list[TradePlayerOut] = Field(description="Men dropped to make room for the arrivals")
    fills: list[TradePlayerOut] = Field(
        description=(
            "Free agents named by the caller for the places this deal opens. Each one is "
            "judged as a man arriving: his line is in the categories and in the judgement"
        )
    )
    drop_source: str = Field(description='"named", "cheapest", "named and cheapest" or ""')
    places_opened: int
    places_filled: int = Field(description="Opened places a named free agent goes into")
    places_left_open: int = Field(description="Opened places valued as a streamed lane")
    places_used: int
    judgement: JudgementOut = Field(
        description="The deal in one currency; its season term is the roster with-and-without"
    )
    season_independent: float = Field(
        description=(
            "The season term the first cut used, each man valued on his own inside a "
            "league-average team. Kept so both can be measured; never the headline"
        )
    )
    categories: list[TradeCategoryOut]
    playoffs: TradePlayoffsOut
    replacement: float = Field(description="What the wire gives a roster place back, a week")
    opened_value: float = Field(
        description=(
            "Categories a week the places this deal leaves open are worth: the better of "
            "the man the wire offers and streaming the place. Zero when it opens none"
        )
    )
    replacement_player: TradePlayerOut | None = Field(
        description="The free agent whose line fills a place the deal opens; null when none does"
    )
    hurdle: float
    clears: bool = Field(description="At or above the bar. A label, not advice")
    net: float = Field(description="Categories the deal is worth this side over both horizons")
    per_week: float
    expected_per_week: float = Field(description="Categories this roster wins in a week, before")
    summary: str
    notes: list[str]
    finish: FinishOut | None = Field(
        default=None,
        description=(
            "Where this deal leaves the side: the projected record, the place and the "
            "playoff odds before and after, from the projected-standings Monte Carlo "
            "with both rosters changed at once. Null when the season cannot be projected"
        ),
    )


class TradeOut(BaseModel):
    """A proposed trade judged from both sides on one day (docs/trades.md section 6)."""

    season: int
    today: int = Field(description="The day it is judged on; nothing after it is read")
    effective_day: int = Field(description="The first day the deal could be in a lineup")
    review_days: int
    review_source: str
    first_scoring_period: int
    last_scoring_period: int
    weeks_remaining: float
    sides: list[TradeSideOut] = Field(description="Ours first, then the other side")
    hurdle: float
    pool_size: int = Field(description="Free agents the wire replacement was taken over")
    historical_wire: bool
    notes: list[str]


class TradeFillCandidateOut(BaseModel):
    """One free agent who could fill the place a deal opens for one side."""

    espn_player_id: int
    name: str
    position: str | None
    pro_team_id: int
    pro_team: str | None = Field(description='ESPN\'s abbreviation ("DET"); null when unknown')
    injury_status: str | None
    expected_return_date: date | None
    hurt: bool
    on_waivers: bool = Field(description="He cannot play for us until he clears")
    waiver_clears_at: date | None
    waiver_clears_on: int | None = Field(description="That day as a scoring period")
    games_left: int
    worth: float = Field(
        description=(
            "Change in this side's expected category wins in a week with him in the opened "
            "place, against the place left open. What the list is sorted by"
        )
    )
    value: float = Field(
        description="Categories a week he gives an ordinary place, league standard"
    )
    weekly: dict[str, float] = Field(description="His week in the nine, percentages as rates")


class TradeFillPoolOut(BaseModel):
    """The wire as one side of one deal sees it, on one day.

    `worth` is what the list is ranked by and it is deliberately not the
    league standard: the question is what this roster is short of once these
    men have left it, which is the same lens the nine-category table uses
    (docs/trades.md, "Filling the opened place").
    """

    season: int
    today: int
    espn_team_id: int = Field(description="The side the places are opened on")
    team_name: str
    ours: bool = Field(description="Whether that side is the team whose page asked")
    readiness: TradeReadinessOut
    places_opened: int = Field(description="Zero means there is nothing to fill")
    opened_value: float = Field(
        description="What those places are worth left open and streamed: the other choice"
    )
    replacement: float
    replacement_espn_player_id: int | None = Field(
        description="The man the report stands in an opened place when nobody is named"
    )
    pool_size: int
    historical_wire: bool
    measured: bool = Field(description="False when no league standard is measurable yet")
    candidates: list[TradeFillCandidateOut] = Field(description="Best first, by `worth`")
    calibration_note: str


class TradeReportOut(BaseModel):
    """The judged deal, or the sentence saying there is nothing to judge it from.

    `calibration_note` travels with the number it qualifies, so the page never
    keeps a copy of the record that could drift from the run behind it
    (`app.trades.calibration`).
    """

    readiness: TradeReadinessOut
    trade: TradeOut | None = Field(description="Null when the season is not ready")
    calibration_note: str


class ProjectionSetOut(BaseModel):
    """One stored upload of a manager's own projections.

    `owner` is a label today and a user id once accounts exist, which is also
    when it starts deciding who may read the rows (docs/projection_sources.md).
    """

    id: int
    season: int
    name: str
    owner: str = Field(description="Whose set it is; a label until accounts exist")
    uploaded_at: datetime
    source_note: str = Field(description="Where the numbers came from, in the uploader's words")
    rows: int = Field(description="How many player rows were stored")
    column_map: dict[str, Any] = Field(
        description="How this file's headers were read, exactly as applied"
    )


class RejectedRowOut(BaseModel):
    """A row the importer could not read, and why it could not."""

    where: str = Field(description="Which row, counting the header as row 1")
    why: str


class ProjectionImportOut(BaseModel):
    """What an import read, mapped, matched and refused.

    The same shape for a preview and for a stored set. `set_id` is null unless
    a set was stored, and `ok` is false when the mapping cannot be used at
    all, in which case `reasons` says why and nothing was stored.
    """

    ok: bool
    dry_run: bool = Field(description="True for a preview, which never stores")
    set_id: int | None
    season: int
    name: str
    filename: str
    basis: str = Field(description="'per_game' or 'totals', measured from the file's own numbers")
    column_map: dict[str, Any] = Field(description="Which header each field was read from")
    rows_read: int
    rows_stored: int
    matched: int = Field(description="Rows matched to a player we already hold")
    unmatched: list[str] = Field(description="On the board by name only: no player of ours matched")
    loose: list[str] = Field(description="Matched through a short first name rather than the full")
    ambiguous: list[str] = Field(description="A second row for a player another row already took")
    duplicates: list[str] = Field(description="The same player twice in the file; the second went")
    rejected: list[RejectedRowOut]
    reasons: list[str] = Field(description="Why the mapping is unusable; empty when ok")


class ProjectionLineOut(BaseModel):
    """One player's line in a stored set, per game.

    Per game because that is how a set is stored and how two sets compare; the
    room multiplies back up by `games`, which is what makes availability
    visible (`app.projections.upload.load_projection_set`).
    """

    name: str = Field(description="The name as uploaded, not as we spell it")
    espn_player_id: int | None = Field(description="Null when no player of ours matched the name")
    team: str | None
    position: str | None
    games: float
    per_game: dict[str, float] = Field(
        description="PTS, REB, AST, STL, BLK, 3PM, TO, FGM, FGA, FTM and FTA"
    )


class PageTeamOut(BaseModel):
    """One team as the in-season pages name it."""

    espn_team_id: int
    name: str
    abbreviation: str | None
    ours: bool = Field(description="Whether this is the manager's own team")


class PageDayOut(BaseModel):
    """One day of a matchup period, for the schedule strip.

    The calendar day is `calendar_date` and not `date`, because a field of
    that name shadows the `date` type inside the class body and the
    annotation on the next field then cannot be evaluated at all.
    """

    scoring_period: int
    calendar_date: date | None = Field(description="Null when no NBA schedule is stored")
    played: bool = Field(description="Whether the day is behind `today`")
    today: bool


class PagePeriodOut(BaseModel):
    """The matchup period `today` falls in, and the days it covers."""

    period: int
    first_scoring_period: int
    final_scoring_period: int
    days: list[PageDayOut]


class PageContextOut(BaseModel):
    """What the in-season pages need besides the two pickup reports.

    One route rather than four calls to existing ones: the pages need the
    day the report is about as a date, the days of its matchup period for
    the schedule strip, every team's name, and the line naming where the
    numbers came from. Records for the index come from `/standings`, which
    already derives them, so they are not repeated here.
    """

    league_id: int
    season: int
    today: int = Field(description="The scoring period the pages are reporting on")
    today_date: date | None = Field(description="Null when no NBA schedule is stored")
    first_scoring_period: int | None
    last_scoring_period: int | None
    period: PagePeriodOut | None = Field(description="Null when `today` is in no matchup period")
    teams: list[PageTeamOut]
    our_espn_team_id: int | None
    source_note: str = Field(description="Where the numbers came from, in the page's words")
    box_scores_as_of: datetime | None = Field(
        default=None,
        description=(
            "When the last ingest of this season finished. Every stored box score on the "
            "page is as of that moment, because they arrive with the nightly pass "
            "(`scripts/ingest_league.py --recent`, docs/jobs.md): a line is last night's "
            "until an in-game refresh exists. Null when no run has ever succeeded"
        ),
    )
    generated_at: datetime = Field(description="When this answer was built, for the refreshed line")


class ChangePlayerOut(BaseModel):
    """A player named in a change, with the id a player card is built on."""

    espn_player_id: int
    name: str


class ChangeTeamOut(BaseModel):
    """A fantasy team named in a change."""

    espn_team_id: int
    name: str


class ChangeOut(BaseModel):
    """One thing that happened (app/inseason/changes.py).

    `text` is the whole of it in the house's own words; the parts are here
    as well so a caller can group, filter or link them without taking the
    sentence apart.
    """

    at: datetime
    kind: str = Field(description="status, add, drop, claim, trade, minutes, waiver_clear, lineup")
    players: list[ChangePlayerOut]
    teams: list[ChangeTeamOut]
    text: str
    mine: bool = Field(description="Touches the team asked about; false without one")
    opponent: bool = Field(description="Touches that team's opponent this matchup period")
    severity: int = Field(description="0 is a man ruled out, 4 is a fact with nothing to do about")


class ChangesOut(BaseModel):
    """What changed in a league between two moments, newest first."""

    league_id: int
    season: int
    since: datetime
    until: datetime
    team_id: int | None = Field(description="The ESPN team id the flags are about, if named")
    opponent_team_id: int | None = Field(description="Its opponent in the period `until` falls in")
    items: list[ChangeOut]
    total: int = Field(description="Changes in the window, before `limit`")
    limit: int


class ProjectedWeekOut(BaseModel):
    """One remaining matchup, from one team's side (app/inseason/projected.py)."""

    period: int
    first_scoring_period: int
    final_scoring_period: int
    days_remaining: int = Field(description="Days of the period still to play, today included")
    in_play: bool = Field(description="The period being played now, whose totals include so far")
    opponent_espn_team_id: int | None = Field(description="Null on a bye")
    opponent_name: str | None
    probabilities: dict[str, float] = Field(
        description="P(this team wins the category); {} on a bye"
    )
    expected_wins: float = Field(description="The nine chances summed; zero on a bye")
    projected: dict[str, float] = Field(description="This side's projected raw counts this period")
    opponent_projected: dict[str, float]


class ProjectedTeamOut(BaseModel):
    """One team's rest of season and where it ends up."""

    espn_team_id: int
    name: str
    banked_won: float = Field(description="Categories won in regular-season weeks already settled")
    banked_lost: float
    banked_matchups: list[int] = Field(description="Matchups won, lost and tied so far")
    expected_won: float = Field(description="Categories expected over the weeks left")
    expected_lost: float
    projected_record: list[float] = Field(description="Banked plus expected: won, lost")
    projected_matchups: list[float] = Field(description="Mean simulated final record: W, L, T")
    weeks: list[ProjectedWeekOut]
    finishes: list[float] = Field(description="P(finishing in each place), first place first")
    playoff_odds: float
    bye_odds: float | None = Field(description="Null where the format gives no first-round bye")


class ProjectedOut(BaseModel):
    """The league's projected standings (docs/projected_record.md).

    A forecast with its reasons and its record: every number here is built
    from today's rosters, the NBA schedule and the league's own matchup
    schedule, and `calibration_note` is what the same method scored when it
    was replayed against a season already played.
    """

    league_id: int
    season: int
    as_of: int = Field(description="The scoring period this was built for")
    as_of_date: date | None
    matchup_period: int
    teams: list[ProjectedTeamOut] = Field(description="In projected order, first place first")
    periods: list[int] = Field(description="Matchup periods projected, ascending")
    playoff_team_count: int
    bye_count: int = Field(description="Seeds that skip the first round; 0 when there are none")
    playoffs_projected: bool
    playoff_note: str = Field(
        description="Why the playoff rounds were left out; '' when they were not"
    )
    tiebreak: str = Field(description="How the table is ordered, in words")
    n_sims: int
    seed: int
    source_note: str
    basis: str
    calibration_note: str = Field(description="What this forecast scored on a replayed season")
    calibration_short: str = Field(
        default="",
        description=(
            "The same record in one sentence (`projected_calibration.SHORT_NOTE`), for a "
            "page that prints the projected finish on one line and keeps the full note "
            "a tap away. Empty for a stored report built before the field existed"
        ),
    )
    stored: bool = Field(default=False, description="Answered from the morning's stored report")


class WhatIfManOut(PickupPlayerOut):
    """One man in a named change, with the starts he gets or was getting."""

    starts: int = Field(
        description=(
            "Starts this period: after the change for a man arriving, before it for a "
            "man leaving or going to injured reserve"
        )
    )


class WhatIfWeekOut(BaseModel):
    """The matchup in front of us, before the change and after it."""

    matchup_period: int
    opponent_espn_team_id: int | None = Field(description="Null on a bye")
    opponent_name: str | None
    days_remaining: int = Field(description="Days of the period still to play, today included")
    posted: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Raw counts, the score as it stands before any change: the same field the "
            "week report carries, under the same live/replay rule. A change made today "
            "cannot move it, which is why there is one and not a pair"
        ),
    )
    opponent_posted: dict[str, float] = Field(default_factory=dict)
    before: dict[str, float] = Field(description="P(winning the category) as things stand")
    after: dict[str, float]
    expected_before: float
    expected_after: float
    delta: float = Field(description="Change in expected categories won this period")
    moved: list[CategoryShiftOut] = Field(description="The categories that moved, largest first")
    fills_empty_day: bool = Field(
        description="The change seats a man on a day a lineup slot was going empty"
    )


class WhatIfOut(BaseModel):
    """A pickup a manager named, in three layers (docs/what_if.md).

    The week and the judgement are the recommender's own numbers for the
    same move, built from the same functions on the same wire; the finish is
    the new half, and it is a second lens rather than a second bar.
    """

    season: int
    today: int = Field(description="The day it is judged on; nothing after it is read")
    today_date: date | None
    espn_team_id: int
    team_name: str
    adds: list[WhatIfManOut]
    drops: list[WhatIfManOut]
    to_ir: list[WhatIfManOut]
    kind: str = Field(description='"swap", "add", "ir_move", or "" for any other shape')
    week: WhatIfWeekOut
    finish: FinishOut
    judgement: JudgementOut = Field(description="The recommender's own, untouched")
    net: float = Field(description="Categories the move is worth over both horizons")
    hurdle: float
    hurdle_source: str
    hurdle_note: str
    clears_hurdle: bool = Field(description="A label, not advice")
    bid: BidOut | None = Field(description="What to pay, on a move that clears the bar")
    pool_size: int = Field(description="Free agents the replacement charge was taken over")
    historical_wire: bool
    notes: list[str]
