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


class BidOut(BaseModel):
    """What to pay for a claim, and the history the number came from."""

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


class PickupPlayerOut(BaseModel):
    """One player as the recommender sees him."""

    espn_player_id: int
    name: str
    pro_team_id: int
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
    moves: list[StreamMoveOut]
    recommended: list[StreamMoveOut] = Field(
        description=(
            "The plan: independent moves worth a look today, in order, each clearing the "
            "bar on its own. Empty when nothing clears it and when no adds are left"
        )
    )
    empty_days: list[EmptyDayOut]
    outlook: JudgementOut = Field(description="The season as it stands, with no move")
    hurdle: float
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
    generated_at: datetime = Field(description="When this answer was built, for the refreshed line")
