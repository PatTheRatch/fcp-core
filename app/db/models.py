"""Canonical tables for league structure.

ESPN's settings are *per season*, not per league. Between one year and the
next a league can change its name, its team count, its playoff format and
even which statistical categories it scores. So nothing that can change is
stored against a league: `leagues` holds only the durable ESPN id, and every
mutable fact lives on `league_seasons`, one row per (league, season).

Two consequences worth stating explicitly, because they are the whole point
of the shape:

* Scoring categories are **rows**, not columns. A league going from nine
  categories to eight is then a data change, not a migration.
* `raw_settings` keeps the payload ESPN actually returned. When ESPN adds a
  field we do not model yet, it is already recorded and can be backfilled
  without re-fetching a season that may no longer be available.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class League(Base):
    """One ESPN league, independent of any season."""

    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_league_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    seasons: Mapped[list["LeagueSeason"]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
        order_by="LeagueSeason.season",
    )


class LeagueSeason(Base):
    """One league as it was configured for one season.

    Re-ingesting a season updates this row in place. Ingesting a new season
    inserts a second row and leaves earlier ones untouched, so last year's
    settings stay exactly as they were.
    """

    __tablename__ = "league_seasons"
    __table_args__ = (
        UniqueConstraint("league_id", "season", name="uq_league_seasons_league_id_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    scoring_type: Mapped[str] = mapped_column(String, nullable=False)
    team_count: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Matchup periods in the regular season (ESPN `reg_season_count`).
    regular_season_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Every matchup period including playoffs. Always >= regular_season_periods;
    #: the two are NOT interchangeable.
    total_matchup_periods: Mapped[int] = mapped_column(Integer, nullable=False)

    playoff_team_count: Mapped[int] = mapped_column(Integer, nullable=False)
    playoff_matchup_period_length: Mapped[int] = mapped_column(Integer, nullable=False)
    keeper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    uses_faab: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: The in-season FAAB pot, 100 in every season so far. NOT the draft
    #: budget: see `auction_budget`, which is double it.
    acquisition_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What each manager spends at the draft, 200 in every season so far.
    #: Zero means the season has not been ingested since this was added, and
    #: the draft code refuses to plan against it rather than assuming.
    auction_budget: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: AUCTION or SNAKE, as ESPN reports it.
    draft_type: Mapped[str | None] = mapped_column(String)
    #: The clock on one nomination, in seconds.
    seconds_per_pick: Mapped[int | None] = mapped_column(Integer)
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: ESPN team ids in nomination order.
    draft_order: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    median_scoring: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trade_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The daily starting lineup, slot name to how many, e.g. {"PG": 1,
    #: "UT": 3}. Read from ESPN rather than inferred: it is a season setting.
    lineup_slots: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    bench_slots: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Injured reserve places. Zero for 2019 to 2026, and one from 2027,
    #: which changes what an injury costs.
    injured_reserve_slots: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Caps on how many players of a primary position may be rostered, e.g.
    #: {"C": 3}. This league limits centres, at three in 2025 and 2026 and
    #: four in 2027, so it cannot be a constant.
    position_limits: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    #: What ESPN returned, kept verbatim. See the module docstring.
    raw_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    league: Mapped[League] = relationship(back_populates="seasons")
    categories: Mapped[list["LeagueSeasonCategory"]] = relationship(
        back_populates="league_season",
        cascade="all, delete-orphan",
        order_by="LeagueSeasonCategory.position",
    )
    teams: Mapped[list["Team"]] = relationship(
        back_populates="league_season",
        cascade="all, delete-orphan",
        order_by="Team.espn_team_id",
    )
    matchup_periods: Mapped[list["MatchupPeriod"]] = relationship(
        back_populates="league_season",
        cascade="all, delete-orphan",
        order_by="MatchupPeriod.period",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="league_season",
        cascade="all, delete-orphan",
        order_by="Transaction.scoring_period",
    )
    draft_picks: Mapped[list["DraftPick"]] = relationship(
        back_populates="league_season",
        cascade="all, delete-orphan",
        order_by="DraftPick.round_num, DraftPick.round_pick",
    )


class LeagueSeasonCategory(Base):
    """One scored statistical category, for one season of one league."""

    __tablename__ = "league_season_categories"
    __table_args__ = (
        UniqueConstraint(
            "league_season_id", "stat_id", name="uq_league_season_categories_season_stat"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False
    )

    #: ESPN's own stat id. Stable across seasons, so it is the join key.
    stat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Human-readable form ("PTS", "FG%"). Derived, stored for legibility.
    abbreviation: Mapped[str] = mapped_column(String, nullable=False)
    #: ESPN's display order, which is not sorted by stat id.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ESPN's `isReverseItem`. Note it is false even for turnovers, where a
    #: lower total wins the category, so do not trust it as "lower is better".
    is_reverse: Mapped[bool] = mapped_column(Boolean, nullable=False)

    league_season: Mapped[LeagueSeason] = relationship(back_populates="categories")


team_owners = Table(
    "team_owners",
    Base.metadata,
    Column("team_id", ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
    Column("owner_id", ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True),
)


class Owner(Base):
    """A person, identified by the GUID ESPN assigns them.

    Global rather than season-scoped: the same GUID follows someone across
    seasons and across leagues, so an owner outlives any team they managed.
    """

    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: ESPN's owner GUID, e.g. "{238280FE-...}". Stable across seasons.
    espn_owner_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String)
    first_name: Mapped[str | None] = mapped_column(String)
    last_name: Mapped[str | None] = mapped_column(String)

    teams: Mapped[list["Team"]] = relationship(secondary=team_owners, back_populates="owners")


class Team(Base):
    """A team as it existed in one season.

    Season-scoped for the same reason `league_seasons` is: a team can be
    renamed, change hands, or have its ESPN id reused between years. The
    durable identity is the owner, not the team.
    """

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("league_season_id", "espn_team_id", name="uq_teams_season_espn_team"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False
    )
    #: ESPN's team id. Sparse and non-contiguous, so it is a key, not an index.
    espn_team_id: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String)
    logo_url: Mapped[str | None] = mapped_column(String)
    division_id: Mapped[int | None] = mapped_column(Integer)
    division_name: Mapped[str | None] = mapped_column(String)
    standing: Mapped[int | None] = mapped_column(Integer)
    final_standing: Mapped[int | None] = mapped_column(Integer)

    #: Season totals of CATEGORIES won, lost and tied. Not a matchup record:
    #: they sum to (regular season periods x categories). ESPN reports no
    #: season matchup record at all; derive it from `matchups` if needed.
    categories_won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    categories_lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    categories_tied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    acquisitions: Mapped[int | None] = mapped_column(Integer)
    drops: Mapped[int | None] = mapped_column(Integer)
    trades: Mapped[int | None] = mapped_column(Integer)
    acquisition_budget_spent: Mapped[int | None] = mapped_column(Integer)

    league_season: Mapped[LeagueSeason] = relationship(back_populates="teams")
    owners: Mapped[list[Owner]] = relationship(secondary=team_owners, back_populates="teams")


class MatchupPeriod(Base):
    """One scoring window in a season.

    `settings.matchup_periods` claims each period covers a single scoring
    period, which the box scores contradict. The authoritative mapping is
    `League.matchup_ids`, which gives every scoring period in each window
    (period 1 covers days 1-6, period 2 covers 7-13, and so on).

    `final_scoring_period` predates that discovery and holds only the last
    day of the window, which is why it is the weaker of the two.
    """

    __tablename__ = "matchup_periods"
    __table_args__ = (
        UniqueConstraint("league_season_id", "period", name="uq_matchup_periods_season_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    #: True once `period` exceeds the season's regular season period count.
    is_playoff: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: The window this period covers, from `League.matchup_ids`. Inclusive at
    #: both ends. Period 1 is days 1-6, period 2 is days 7-13, and so on.
    first_scoring_period: Mapped[int | None] = mapped_column(Integer)
    final_scoring_period: Mapped[int | None] = mapped_column(Integer)

    league_season: Mapped[LeagueSeason] = relationship(back_populates="matchup_periods")
    matchups: Mapped[list["Matchup"]] = relationship(
        back_populates="matchup_period", cascade="all, delete-orphan"
    )
    daily_lineup_slots: Mapped[list["DailyLineupSlot"]] = relationship(
        back_populates="matchup_period", cascade="all, delete-orphan"
    )


class Matchup(Base):
    """One pairing inside a matchup period.

    `away_team_id` is null for a bye: ESPN reports the absent side as team 0
    and leaves the result UNDECIDED, which happens in the playoff rounds.
    """

    __tablename__ = "matchups"
    __table_args__ = (
        UniqueConstraint("matchup_period_id", "home_team_id", name="uq_matchups_period_home"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matchup_period_id: Mapped[int] = mapped_column(
        ForeignKey("matchup_periods.id", ondelete="CASCADE"), nullable=False
    )
    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    away_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))

    #: HOME, AWAY, TIE or UNDECIDED, as ESPN reports it.
    winner: Mapped[str] = mapped_column(String, nullable=False)
    home_categories_won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    home_categories_lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    categories_tied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    matchup_period: Mapped[MatchupPeriod] = relationship(back_populates="matchups")
    roster_slots: Mapped[list["RosterSlot"]] = relationship(
        back_populates="matchup", cascade="all, delete-orphan"
    )
    team_stats: Mapped[list["MatchupTeamStat"]] = relationship(
        back_populates="matchup", cascade="all, delete-orphan"
    )


class Player(Base):
    """An NBA player, identified by ESPN's global player id.

    Global, like `owners`: a player is not owned by a league or a season.
    Anything about them that changes (team, position, health) is recorded on
    the roster row instead, because that row is pinned to a point in time.
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_player_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class RosterSlot(Base):
    """One player on one team's roster during one matchup period.

    A roster is a moving target, so this is deliberately a snapshot pinned to
    a matchup period rather than a "current roster" that would be wrong the
    moment anyone makes a waiver claim.

    There is no lineup slot column yet, but the slot IS recoverable and this
    is a gap rather than a dead end. `box_scores(matchup_period, ...)` reads
    ESPN's `rosterForMatchupPeriod`, where every `lineupSlotId` is 0, hence
    the useless "PG" on every player. Passing `matchup_total=False` with an
    explicit `scoring_period` reads `rosterForCurrentScoringPeriod` instead,
    which carries the real daily slots including BE and IR.
    """

    __tablename__ = "roster_slots"
    __table_args__ = (
        UniqueConstraint(
            "matchup_id", "team_id", "player_id", name="uq_roster_slots_matchup_team_player"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matchup_id: Mapped[int] = mapped_column(
        ForeignKey("matchups.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )

    #: Position and pro team as at this matchup period, not as of today.
    position: Mapped[str | None] = mapped_column(String)
    pro_team: Mapped[str | None] = mapped_column(String)
    injured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    injury_status: Mapped[str | None] = mapped_column(String)

    matchup: Mapped[Matchup] = relationship(back_populates="roster_slots")
    player: Mapped[Player] = relationship()


class MatchupTeamStat(Base):
    """One statistic posted by one team in one matchup.

    Covers both the categories the league scores and the component stats
    behind the percentages. ESPN returns FGM, FGA, FTM and FTA alongside
    FG% and FT%, so a stored percentage can be recomputed or re-weighted
    rather than being taken on trust.

    `league_season_category_id` is set only when the statistic is one this
    season actually scores, which is the reliable test. `result` is not:
    a bye leaves every result null while still reporting real values.
    """

    __tablename__ = "matchup_team_stats"
    __table_args__ = (
        UniqueConstraint("matchup_id", "team_id", "abbreviation", name="uq_matchup_team_stats_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matchup_id: Mapped[int] = mapped_column(
        ForeignKey("matchups.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)

    #: ESPN's abbreviation, e.g. "PTS", "FG%", "FGM".
    abbreviation: Mapped[str] = mapped_column(String, nullable=False)
    #: A count for counting stats, a ratio for percentages (FG% is 0.457, not 45.7).
    value: Mapped[float] = mapped_column(Float, nullable=False)
    #: WIN, LOSS or TIE. Null for a component stat, and null on both sides of
    #: a bye, where there is no opponent to compare against.
    result: Mapped[str | None] = mapped_column(String)

    #: Set when this statistic is a category the season scores; null otherwise.
    #: This, not `result`, is how to tell a scored category from a component.
    league_season_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("league_season_categories.id", ondelete="SET NULL")
    )

    matchup: Mapped[Matchup] = relationship(back_populates="team_stats")
    category: Mapped[LeagueSeasonCategory | None] = relationship()


class PlayerGameStat(Base):
    """What one player recorded in one scoring period.

    A scoring period in ESPN basketball is a single day, so this is the
    box score line for one game. It is a global fact about a player rather
    than a league one: two leagues holding the same player share this row.
    `season` is part of the key because scoring period numbers restart each
    year.

    A row exists for every scoring period ESPN lists for the player,
    including days their team played and they did not. `played` separates
    the two, which is what makes availability answerable rather than
    guessable from missing rows.
    """

    __tablename__ = "player_game_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "scoring_period", name="uq_player_game_stats_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_period: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Tip-off, as ESPN reports it. ESPN sends no offset, so it is read as UTC.
    game_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The opposing pro team. ESPN never reports the player's own team here.
    opponent: Mapped[str | None] = mapped_column(String)
    #: False when ESPN lists the scoring period but records no stat line.
    played: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    minutes: Mapped[float | None] = mapped_column(Float)
    points: Mapped[float | None] = mapped_column(Float)
    rebounds: Mapped[float | None] = mapped_column(Float)
    offensive_rebounds: Mapped[float | None] = mapped_column(Float)
    defensive_rebounds: Mapped[float | None] = mapped_column(Float)
    assists: Mapped[float | None] = mapped_column(Float)
    steals: Mapped[float | None] = mapped_column(Float)
    blocks: Mapped[float | None] = mapped_column(Float)
    turnovers: Mapped[float | None] = mapped_column(Float)
    personal_fouls: Mapped[float | None] = mapped_column(Float)

    field_goals_made: Mapped[float | None] = mapped_column(Float)
    field_goals_attempted: Mapped[float | None] = mapped_column(Float)
    three_pointers_made: Mapped[float | None] = mapped_column(Float)
    three_pointers_attempted: Mapped[float | None] = mapped_column(Float)
    free_throws_made: Mapped[float | None] = mapped_column(Float)
    free_throws_attempted: Mapped[float | None] = mapped_column(Float)

    #: All 45 statistics ESPN returned, including the rate stats that are
    #: meaningless for a single game (PPG equals PTS) but harmless to keep.
    raw_totals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    player: Mapped[Player] = relationship()


#: Lineup slots that mean the player did not count toward the team's totals.
#: "FA" is `espn-api`'s placeholder when ESPN sends no slot at all.
NON_STARTING_SLOTS = ("BE", "IR", "FA")


class DailyLineupSlot(Base):
    """Where one player sat in one team's lineup on one day.

    The finest grain ESPN exposes, and the one that makes narratives
    possible: who was benched, who was started while injured, and what the
    player they sat went on to do that night.

    Kept alongside `roster_slots` rather than replacing it. The weekly row
    says who a team held during a matchup period; these rows say what the
    team actually did with them, day by day.

    Sourced from ESPN's `rosterForCurrentScoringPeriod`, which is only
    returned when a specific scoring period is requested. The aggregate
    roster used for `roster_slots` reports no usable slot at all.
    """

    __tablename__ = "daily_lineup_slots"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "scoring_period", "player_id", name="uq_daily_lineup_slots_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    #: The matchup period this day falls inside, per `League.matchup_ids`.
    matchup_period_id: Mapped[int] = mapped_column(
        ForeignKey("matchup_periods.id", ondelete="CASCADE"), nullable=False
    )
    #: The day itself. Join to `player_game_stats` on this to see what the
    #: player actually did while sitting in this slot.
    scoring_period: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )

    #: PG, SG, SF, PF, C, G, F, UT, BE, IR, or FA.
    slot: Mapped[str] = mapped_column(String, nullable=False)
    #: False for BE, IR and FA. Materialised because almost every narrative
    #: query filters on it.
    started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    injured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    injury_status: Mapped[str | None] = mapped_column(String)

    team: Mapped[Team] = relationship()
    matchup_period: Mapped[MatchupPeriod] = relationship(back_populates="daily_lineup_slots")
    player: Mapped[Player] = relationship()


class IngestRun(Base):
    """One execution of the ingest, successful or not.

    Deliberately not tied to `leagues` or `league_seasons` by foreign key.
    A run that fails before it writes anything still needs to be recorded,
    and a scheduled job that never reaches ESPN has no season row to hang
    off. `espn_league_id` and `season` are therefore plain numbers.

    This is what makes a schedule observable: without it, "did last night's
    ingest run" is unanswerable.
    """

    __tablename__ = "ingest_runs"
    __table_args__ = (
        # The listing this table exists for: newest runs, per season.
        Index("ix_ingest_runs_season_started", "season", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_league_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: "full" rewrites the season; "recent" only the trailing days.
    mode: Mapped[str] = mapped_column(String, nullable=False)
    #: "running", "succeeded" or "failed". A row left at "running" means the
    #: process died without finishing, which is itself worth seeing.
    status: Mapped[str] = mapped_column(String, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    #: First line of the failure, kept short enough to read in a listing.
    error: Mapped[str | None] = mapped_column(String)
    #: Row counts and the scope the run covered.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Transaction(Base):
    """One roster move: a waiver claim, a free agent pickup, or a trade.

    Failed and cancelled moves are kept, not filtered out. A waiver claim
    that lost is often the more interesting record: it says who wanted a
    player and what they were willing to pay, which a successful claim alone
    never reveals.

    Lineup shuffling (ESPN's FUTURE_ROSTER) is excluded. It is the bulk of
    what the endpoint returns and is already recorded, properly, in
    `daily_lineup_slots`.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("espn_transaction_id", name="uq_transactions_espn_id"),
        Index("ix_transactions_season_period", "league_season_id", "scoring_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False
    )
    #: ESPN's own UUID for the move. Stable, so re-ingesting cannot duplicate.
    espn_transaction_id: Mapped[str] = mapped_column(String, nullable=False)

    #: The team that initiated it. Null when ESPN reports team 0, meaning
    #: the move came from outside any roster.
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))

    #: WAIVER, FREEAGENT, TRADE_ACCEPT, TRADE_PROPOSAL and so on.
    type: Mapped[str] = mapped_column(String, nullable=False)
    #: EXECUTED, CANCELED, PENDING, or one of ESPN's FAILED_* reasons.
    status: Mapped[str | None] = mapped_column(String)
    scoring_period: Mapped[int] = mapped_column(Integer, nullable=False)
    #: When ESPN processed it. Null for anything never processed.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: FAAB bid. Zero on a free pickup, and meaningful even when the claim
    #: failed, since it records what was offered.
    bid_amount: Mapped[int | None] = mapped_column(Integer)

    league_season: Mapped[LeagueSeason] = relationship(back_populates="transactions")
    items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class TransactionItem(Base):
    """One player moving within one transaction.

    A waiver claim usually has two: the player added and the player dropped.
    A trade has one per player changing hands, and the direction is the point,
    which is why both team columns exist.
    """

    __tablename__ = "transaction_items"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", "player_id", "item_type", name="uq_transaction_items_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )

    #: ADD, DROP or TRADE.
    item_type: Mapped[str] = mapped_column(String, nullable=False)
    #: Null on either side means free agency rather than a team.
    from_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    to_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))

    transaction: Mapped[Transaction] = relationship(back_populates="items")
    player: Mapped[Player] = relationship()


class DraftPick(Base):
    """One pick, from the draft that started a season.

    The league drafts by auction, so `bid_amount` is what the team paid
    rather than a formality. That makes the pick comparable against what the
    player went on to return, which is the whole reason to store it.

    Costs nothing to collect: ESPN sends the full draft with the league
    itself, so this needs no request of its own.
    """

    __tablename__ = "draft_picks"
    __table_args__ = (
        UniqueConstraint("league_season_id", "round_num", "round_pick", name="uq_draft_picks_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    #: The team that ended up with the player.
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    #: The team that put the player up for auction, which is often not the
    #: team that won him.
    nominating_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE")
    )

    round_num: Mapped[int] = mapped_column(Integer, nullable=False)
    round_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Auction price. Not a FAAB bid: this is the draft budget, a separate
    #: pot from the in-season acquisition budget.
    bid_amount: Mapped[int | None] = mapped_column(Integer)
    keeper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    league_season: Mapped[LeagueSeason] = relationship(back_populates="draft_picks")
    player: Mapped[Player] = relationship()


#: The season rollups ESPN puts on a player card, keyed as "<season>_<kind>".
#: Rolling windows (last_7 and friends) are deliberately not among them: they
#: describe a moment, not a season, and are meaningless once it has ended.
SEASON_STAT_KINDS = ("projected", "total")


class PlayerSeasonStat(Base):
    """A player's whole season, either as forecast or as it happened.

    `kind` is "projected" for what ESPN's player card labels the season's
    projection and "total" for what the player actually did. Both come free
    with the player cards already fetched for the daily lines.

    "Projected" is a preseason forecast for most seasons and not for all:
    ESPN retains whatever projection it last served, and for 2023 that is a
    rest-of-season projection captured mid-year. Nothing on the row records
    which. `app.draft.projections` is the registry of seasons where it is
    not a forecast, and everything that reads these lines as one consults
    it.

    The actual totals are partly redundant with summing `player_game_stats`,
    and kept anyway: ESPN omits days from its own cards for some seasons, so
    its total and our sum can disagree. Storing both makes that visible
    rather than hiding it behind whichever one was asked for.
    """

    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "kind", name="uq_player_season_stats_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: "projected" or "total".
    kind: Mapped[str] = mapped_column(String, nullable=False)

    games_played: Mapped[float | None] = mapped_column(Float)
    minutes: Mapped[float | None] = mapped_column(Float)
    points: Mapped[float | None] = mapped_column(Float)
    rebounds: Mapped[float | None] = mapped_column(Float)
    assists: Mapped[float | None] = mapped_column(Float)
    steals: Mapped[float | None] = mapped_column(Float)
    blocks: Mapped[float | None] = mapped_column(Float)
    turnovers: Mapped[float | None] = mapped_column(Float)
    three_pointers_made: Mapped[float | None] = mapped_column(Float)
    field_goals_made: Mapped[float | None] = mapped_column(Float)
    field_goals_attempted: Mapped[float | None] = mapped_column(Float)
    free_throws_made: Mapped[float | None] = mapped_column(Float)
    free_throws_attempted: Mapped[float | None] = mapped_column(Float)

    #: Everything ESPN sent. A projection carries 31 stats against 45 on a
    #: total, so the two are not the same shape.
    raw_totals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: Lineup slots the player may occupy that season, as ESPN names them:
    #: PG, SG, SF, PF, C, G, F, G/F, PF/C, F/C, UT, BE, IR. Rides on the
    #: season row because eligibility is set per season and it is what the
    #: optimizer needs to refuse a roster nobody could field.
    eligible_slots: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: The player's primary position, which is what position limits count.
    #: A power forward eligible at centre is not a centre for that purpose.
    primary_position: Mapped[str | None] = mapped_column(String)


class PlayerProjectionSnapshot(Base):
    """ESPN's projection for a player as it stood on one day.

    `player_season_stats` keeps one projection per season and overwrites it
    on every ingest, so the projection a manager was looking at on the day of
    a trade is gone by the next morning. The decision lens of the scoring
    work (docs/scoring/SPEC.md) needs exactly that, so the ingest also writes
    it here, one row per player per day, whenever it stores a projection.

    What the projection is, from the S1 probe (branch `scoring-s1`): the
    card's only projection split, a forecast with its own games count.
    Whether ESPN refreshes it during the season, and whether it becomes
    rest-of-season, cannot be measured in September; these rows are how that
    gets answered.

    Costs no request: the cards are already fetched for the daily lines.
    """

    __tablename__ = "player_projection_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "player_id", "season", "captured_on", "kind", name="uq_player_projection_snapshots_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The UTC date the ingest read the card.
    captured_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: The card's key for the split, "projected" today; kept as a column so a
    #: rest-of-season split, if ESPN ever adds one, lands beside it.
    kind: Mapped[str] = mapped_column(String, nullable=False)
    games_played: Mapped[float | None] = mapped_column(Float)
    #: The split's totals exactly as ESPN sent them.
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    player: Mapped[Player] = relationship()


class BBMCapture(Base):
    """One daily pull of Basketball Monster's projection export.

    A row per export per day, whether or not anything changed, so a gap in
    the history is a day the pull did not run rather than a quiet day.
    """

    __tablename__ = "bbm_captures"
    __table_args__ = (
        UniqueConstraint(
            "season", "value_type", "captured_on", name="uq_bbm_captures_season_type_day"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: "total" (season-total values) or "pergame" (per-game values).
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    captured_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: BBM's projection source and the league its values were computed for.
    source: Mapped[str | None] = mapped_column(String)
    league: Mapped[str | None] = mapped_column(String)
    players: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Rows that differed from the day before (a new version was stored).
    changed: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Players in yesterday's export and not in today's.
    dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BBMProjection(Base):
    """A player's row in BBM's export, for the days it stayed the same.

    Stored as versions rather than daily copies: a row is written when a
    player's line changes, and `last_seen` moves forward on each day it does
    not. The row as BBM served it on a date is the version with
    `first_seen <= date <= last_seen` (`app.draft.bbm_store.as_of`).

    Paid data: this table stays on our database and out of the repository.
    """

    __tablename__ = "bbm_projections"
    __table_args__ = (
        Index("ix_bbm_projections_lookup", "season", "value_type", "name_key", "last_seen"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    #: BBM's name as exported, and the normalised key rows are tracked by.
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_key: Mapped[str] = mapped_column(String, nullable=False)
    #: Our player, when the name matches one strictly (`app.draft.bbm.match_player`).
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))
    first_seen: Mapped[date] = mapped_column(Date, nullable=False)
    last_seen: Mapped[date] = mapped_column(Date, nullable=False)
    #: Hash of `row`, to tell a changed line from an unchanged one cheaply.
    row_hash: Mapped[str] = mapped_column(String, nullable=False)
    #: Every column of the export for this player, as BBM sent it.
    row: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class PlayerStatusSnapshot(Base):
    """A player's status and ownership as ESPN reported it at one moment.

    The one thing the rest of the database cannot reconstruct. ESPN serves a
    player's injury status *as of the request* and never as a history, which
    is why `daily_lineup_slots.injury_status` reads OUT on days a player
    scored 30 (see `app/draft/availability.py`). The listener writes a row
    for every player on every pass, and the time series is the table itself.

    Every observation is stored, not only changes: "no change" is then a
    fact on record, and the diff in `app/listener/events.py` stays honest.
    About 550 players by four passes a day by 180 days is roughly 400k rows a
    season, which is nothing.

    `season` is the season being observed, not a foreign key: the pass runs
    before the season's row may exist, and a player's card is global.
    """

    __tablename__ = "player_status_snapshots"
    __table_args__ = (
        Index("ix_player_status_snapshots_player_observed", "player_id", "observed_at"),
        Index("ix_player_status_snapshots_season_observed", "season", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: One value for the whole pass, so a pass can be selected by it.
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: "morning", "report", "late" or "nightly" (docs/pickups.md section 3.6).
    pass_label: Mapped[str] = mapped_column(String, nullable=False)

    #: ESPN's string as given: ACTIVE, OUT, DAY_TO_DAY, QUESTIONABLE, ...
    injury_status: Mapped[str | None] = mapped_column(String)
    injured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expected_return_date: Mapped[date | None] = mapped_column(Date)
    #: 0 means no NBA team.
    pro_team_id: Mapped[int | None] = mapped_column(Integer)
    #: The fantasy team holding him in this league, 0 when unrostered.
    on_team_id: Mapped[int | None] = mapped_column(Integer)
    #: ONTEAM, FREEAGENT or WAIVERS.
    status: Mapped[str | None] = mapped_column(String)

    percent_owned: Mapped[float | None] = mapped_column(Float)
    #: The 24-hour move in `percent_owned` across all ESPN leagues.
    percent_change: Mapped[float | None] = mapped_column(Float)
    percent_started: Mapped[float | None] = mapped_column(Float)
    auction_value_average: Mapped[float | None] = mapped_column(Float)

    player: Mapped[Player] = relationship()


class PlayerNews(Base):
    """One news item about a player, kept from the first pass that saw it.

    Fetched one request per player, so only for players with a fresh event
    and for the tracked team's roster. Unique on the story so a second pass
    that sees the same item writes nothing.
    """

    __tablename__ = "player_news"
    __table_args__ = (
        UniqueConstraint("player_id", "published", "headline", name="uq_player_news_story"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    published: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    headline: Mapped[str] = mapped_column(String, nullable=False)
    story: Mapped[str] = mapped_column(String, nullable=False, default="")
    source: Mapped[str] = mapped_column(String, nullable=False, default="espn")
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    player: Mapped[Player] = relationship()


class FreeAgentSnapshot(Base):
    """Who was available in this league at one pass, and their waiver state.

    League-scoped where `player_status_snapshots` is global: availability is
    a fact about this league's rosters. The same pool fetch feeds both; an
    entry whose status is not ONTEAM lands here as well.
    """

    __tablename__ = "free_agent_snapshots"
    __table_args__ = (
        Index("ix_free_agent_snapshots_season_observed", "league_season_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scoring_period: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    #: FREEAGENT or WAIVERS.
    status: Mapped[str] = mapped_column(String, nullable=False)
    #: When a player on waivers clears. Read from the entry's
    #: `waiverProcessDate`, which the probe has still to confirm.
    waiver_clears_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    league_season: Mapped[LeagueSeason] = relationship()
    player: Mapped[Player] = relationship()


class ProTeamGame(Base):
    """One NBA game, from the pro schedule ESPN sends with the league.

    Games remaining in a matchup period is the whole basis of streaming, so
    the schedule has to be in the database. Rewritten for the season on
    every pass, since postponements move games. A scoring period is a day,
    and a team plays at most once a day, hence the key.
    """

    __tablename__ = "pro_team_games"
    __table_args__ = (
        UniqueConstraint("season", "pro_team_id", "scoring_period", name="uq_pro_team_games_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    pro_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_period: Mapped[int] = mapped_column(Integer, nullable=False)
    game_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opponent_pro_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    home: Mapped[bool] = mapped_column(Boolean, nullable=False)


class PlayerStatusEvent(Base):
    """A change between two consecutive snapshots of one player.

    Derived and persisted, rather than recomputed, because the digest has to
    know what it has already reported: `notified_at` is set when it goes
    out. The kinds and the rules that produce them live in
    `app/listener/events.py`.
    """

    __tablename__ = "player_status_events"
    __table_args__ = (
        UniqueConstraint("player_id", "kind", "observed_at", name="uq_player_status_events_key"),
        Index("ix_player_status_events_season_observed", "season", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    #: The pass that first saw the new state.
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The fields that changed, before and after.
    previous: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    current: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Whatever the rule computed on the way, e.g. the two minutes means.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    player: Mapped[Player] = relationship()


class ProjectionSet(Base):
    """One upload of a manager's own projections, as a set.

    The answer to a second person using this software: BBM's numbers are paid
    and stay with the member who fetched them (docs/projection_sources.md), so
    a manager brings his own from wherever he pays for them and the room is
    loaded from those instead. A set is immutable once stored; a fresh upload
    is a fresh set, which is what makes "the board I drafted on" answerable
    later.

    `column_map` keeps the header-to-field mapping that was actually applied,
    so a set can be read back knowing how it was interpreted, and a mapping
    that went wrong is visible rather than guessed at.
    """

    __tablename__ = "projection_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What the manager called it, e.g. "Hashtag preseason".
    name: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Whose set it is. A label for now; a user id once accounts exist, which
    #: is also when it starts deciding who may read the rows.
    owner: Mapped[str] = mapped_column(String, nullable=False)
    #: Where the numbers came from, in the uploader's own words.
    source_note: Mapped[str] = mapped_column(String, nullable=False, default="")
    #: canonical field -> the header it was read from, plus how the two
    #: percentages were rebuilt. Written by `app.projections.upload`.
    column_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: How many rows were stored, so a set's size is one read.
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProjectionRow(Base):
    """One player's line in an uploaded set, as per-game rates.

    Stored per game rather than as totals because that is the form a set can
    be checked against another: totals are `rate * games`, which is what
    `app.projections.upload.load_projection_set` hands the room. Makes *and*
    attempts are both columns for each percentage, because a percentage alone
    cannot be rebuilt into a roster's percentage (`app/scoring/lines.py`).

    An unmatched name keeps `player_id` empty and is still stored: the room
    puts him on the board under a synthetic id, the same way BBM's rookies go
    on it (`app.draft.bbm.synthetic_id`).
    """

    __tablename__ = "projection_rows"
    __table_args__ = (
        UniqueConstraint("set_id", "name_key", name="uq_projection_rows_name"),
        Index("ix_projection_rows_set_player", "set_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("projection_sets.id", ondelete="CASCADE"), nullable=False
    )
    #: The name as uploaded, and the normalised key it was matched on.
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_key: Mapped[str] = mapped_column(String, nullable=False)
    #: Our player, when the name matched one strictly (`app.draft.bbm.match_player`).
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))

    games: Mapped[float] = mapped_column(Float, nullable=False)
    points: Mapped[float] = mapped_column(Float, nullable=False)
    rebounds: Mapped[float] = mapped_column(Float, nullable=False)
    assists: Mapped[float] = mapped_column(Float, nullable=False)
    steals: Mapped[float] = mapped_column(Float, nullable=False)
    blocks: Mapped[float] = mapped_column(Float, nullable=False)
    three_pointers_made: Mapped[float] = mapped_column(Float, nullable=False)
    turnovers: Mapped[float] = mapped_column(Float, nullable=False)
    field_goals_made: Mapped[float] = mapped_column(Float, nullable=False)
    field_goals_attempted: Mapped[float] = mapped_column(Float, nullable=False)
    free_throws_made: Mapped[float] = mapped_column(Float, nullable=False)
    free_throws_attempted: Mapped[float] = mapped_column(Float, nullable=False)

    position: Mapped[str | None] = mapped_column(String)
    team: Mapped[str | None] = mapped_column(String)
    #: The row exactly as uploaded, so a column we do not model yet is not lost.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ---------------------------------------------------------------------------
# Accounts (docs/accounts.md, docs/product.md step 1)
# ---------------------------------------------------------------------------


class User(Base):
    """A person who can sign in, known by an email address and nothing else.

    No password is ever stored: a user signs in by a one-time link mailed to
    this address. The address is stored lower-cased, so "Pat@X" and "pat@x"
    are one account; the check constraint says so to the database as well.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_users_email_lower"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignInToken(Base):
    """One magic link: good once, for fifteen minutes.

    Only the sha256 of the token is stored. The token itself exists in the
    email and nowhere else, so a copy of this table signs nobody in.
    """

    __tablename__ = "sign_in_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Where to land after signing in: a path on this site, checked before it
    #: is stored (`app.accounts.safe_next`), never a whole URL.
    next_path: Mapped[str | None] = mapped_column(String)


class UserSession(Base):
    """A signed-in browser: the cookie's hash, and when it stops working.

    Named `UserSession` because SQLAlchemy's `Session` is everywhere here;
    the table is `sessions`. Revoked by sign-out, and dead after thirty days
    whatever happens.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Entitlement(Base):
    """A user's right to the paid tier: which tier, where it came from, until when.

    `source` is `owner` (Patrick's own, always), `subscription` (written by
    the payment provider's webhook, step 7), `trial` or `comp`. An open
    `valid_until` never lapses. One `owner` row per user at most, which is
    what lets the owner's row be written idempotently on every first use.
    """

    __tablename__ = "entitlements"
    __table_args__ = (
        CheckConstraint("tier IN ('team')", name="ck_entitlements_tier"),
        CheckConstraint(
            "source IN ('owner', 'subscription', 'trial', 'comp')", name="ck_entitlements_source"
        ),
        Index(
            "uq_entitlements_one_owner",
            "user_id",
            unique=True,
            postgresql_where=text("source = 'owner'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tier: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeamManager(Base):
    """A user's claim on one team in one season, and whether it is verified.

    The minimal form of step 2's team claims (docs/product.md): the team
    check reads verified rows, and nothing else opens a team's private
    pages. `state` is `pending`, `verified` or `rejected`; `how` says what
    verified it (`owner` for the owner's own team today, the ESPN owner-GUID
    match or a manual approval in step 2). Season-scoped because `teams` is:
    a claim on this year's team says nothing about whoever held that ESPN id
    before. Several verified managers of one team are allowed, as ESPN's
    co-owners are.
    """

    __tablename__ = "team_managers"
    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_team_managers_user_team"),
        CheckConstraint(
            "state IN ('pending', 'verified', 'rejected')", name="ck_team_managers_state"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    how: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
