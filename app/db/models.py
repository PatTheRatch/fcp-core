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

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
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
    acquisition_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    median_scoring: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trade_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
