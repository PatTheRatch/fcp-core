"""Persist ESPN league structure into the canonical tables.

Read ESPN, write Postgres. Nothing here derives or computes anything: it is a
faithful record of how a league was configured for one season.

The unit of work is a *season*, never a league. Re-running for a season that
is already stored updates that row in place; running for a new season inserts
alongside it and leaves prior seasons untouched. That is what makes the
ingest safe to run every year, and on a schedule within a year.
"""

from datetime import UTC, datetime
from typing import Any

from espn_api.basketball import League as ESPNLeague
from espn_api.basketball.constant import STATS_MAP
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League, LeagueSeason, LeagueSeasonCategory


def _epoch_ms_to_datetime(epoch_ms: Any) -> datetime | None:
    """ESPN reports the trade deadline as epoch milliseconds; 0 means none."""
    if not isinstance(epoch_ms, int) or epoch_ms <= 0:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)


def _scoring_items(settings: Any) -> list[dict[str, Any]]:
    """The scored categories, in ESPN's own display order.

    `espn_api` exposes no parsed category list, so the raw settings dict is
    the only source for this.
    """
    raw = getattr(settings, "_raw_scoring_settings", None) or {}
    items = raw.get("scoringItems") or []
    return [item for item in items if isinstance(item, dict) and "statId" in item]


def _raw_snapshot(settings: Any) -> dict[str, Any]:
    """Everything ESPN sent that we do not model as a column yet."""
    division_map = getattr(settings, "division_map", None) or {}
    return {
        "scoring": getattr(settings, "_raw_scoring_settings", None) or {},
        "schedule": getattr(settings, "_raw_schedule_settings", None) or {},
        "matchup_periods": dict(getattr(settings, "matchup_periods", None) or {}),
        "division_map": {str(key): value for key, value in division_map.items()},
    }


def _get_or_create_league(session: Session, espn_league_id: int) -> League:
    league = session.scalar(select(League).where(League.espn_league_id == espn_league_id))
    if league is None:
        league = League(espn_league_id=espn_league_id)
        session.add(league)
        session.flush()  # assign league.id before the season row references it
    return league


def _sync_categories(league_season: LeagueSeason, items: list[dict[str, Any]]) -> None:
    """Make the stored categories match ESPN exactly, by stat id.

    Categories are reconciled rather than replaced so that a season whose
    scoring changed keeps stable row ids for the categories it kept.
    """
    existing = {category.stat_id: category for category in league_season.categories}
    seen: set[int] = set()

    for position, item in enumerate(items):
        stat_id = int(item["statId"])
        seen.add(stat_id)

        category = existing.get(stat_id)
        if category is None:
            category = LeagueSeasonCategory(stat_id=stat_id)
            league_season.categories.append(category)

        category.abbreviation = STATS_MAP.get(str(stat_id), f"STAT_{stat_id}")
        category.position = position
        category.is_reverse = bool(item.get("isReverseItem", False))

    for stat_id, category in existing.items():
        if stat_id not in seen:
            league_season.categories.remove(category)  # delete-orphan deletes the row


def ingest_league_structure(session: Session, espn_league: ESPNLeague) -> LeagueSeason:
    """Write one season of one league. Returns the stored row.

    Does not commit: the caller owns the transaction.
    """
    settings = espn_league.settings
    league = _get_or_create_league(session, int(espn_league.league_id))
    season = int(espn_league.year)

    league_season = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.league_id == league.id,
            LeagueSeason.season == season,
        )
    )
    if league_season is None:
        league_season = LeagueSeason(league_id=league.id, season=season)
        session.add(league_season)

    league_season.name = str(settings.name)
    league_season.scoring_type = str(settings.scoring_type)
    league_season.team_count = int(settings.team_count)
    league_season.regular_season_periods = int(settings.reg_season_count)
    league_season.total_matchup_periods = len(getattr(settings, "matchup_periods", None) or {})
    league_season.playoff_team_count = int(settings.playoff_team_count)
    league_season.playoff_matchup_period_length = int(settings.playoff_matchup_period_length)
    league_season.keeper_count = int(settings.keeper_count)
    league_season.uses_faab = bool(settings.faab)
    league_season.acquisition_budget = int(settings.acquisition_budget)
    league_season.median_scoring = bool(settings.median_scoring)
    league_season.trade_deadline = _epoch_ms_to_datetime(getattr(settings, "trade_deadline", None))
    league_season.raw_settings = _raw_snapshot(settings)
    league_season.ingested_at = datetime.now(UTC)

    _sync_categories(league_season, _scoring_items(settings))
    session.flush()
    return league_season
