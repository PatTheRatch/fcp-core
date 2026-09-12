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
    DateTime,
    ForeignKey,
    Integer,
    String,
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
