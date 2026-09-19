"""One league's nightly ingest, for the worker (app/jobs.py, docs/jobs.md).

What `scripts/scheduled_ingest.sh` runs today (`ingest_league.py --recent 10
--upcoming`), but for any league and with whatever login reads it: the
league's own sealed connection, or the `.env` cookies for `ESPN_LEAGUE_ID`.
The script itself is left exactly as it is, so the timers that run it tonight
run the same code; this module is what the worker runs once the VPS is moved
onto the job queue.

Two differences from the script, both because there is now more than one
league:

* The season to narrow against is looked up for **this** league. The script
  asks for any league's row of that season (`LeagueSeason.season == season`),
  which is right only while there is one league.
* A league with no season stored yet (a league just connected) is backfilled:
  every season ESPN holds, oldest first, which is what the product promises
  ("the ingest backfills its seasons"). About two and a half minutes a
  season, once.

`fetch_league` is looked up on this module at call time, so a test replaces
it without touching ESPN. Nothing here logs or raises a credential: the
login travels inside `ESPNSettings`, and ESPN's own errors are left to the
caller to put into words (`app.job_kinds`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from espn_api.basketball import League as ESPNLeague
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import League, LeagueSeason, PlayerGameStat, Transaction
from app.espn import ESPNSettings, current_season, fetch_league, prior_seasons
from app.ingest import (
    FULL_SCOPE,
    draft_is_pending,
    ingest_season,
    ingest_season_settings,
    recent_scope,
    scheduled_draft,
)
from app.ingest_runs import record_run

#: The trailing scoring periods a nightly run rewrites; the script's default.
RECENT_DAYS = 10

__all__ = ["RECENT_DAYS", "FetchError", "IngestSummary", "fetch_league", "ingest_league"]


@dataclass
class IngestSummary:
    """What one league's ingest did, in lines for the job's log."""

    espn_league_id: int
    seasons: list[int] = field(default_factory=list)
    backfilled: bool = False
    lines: list[str] = field(default_factory=list)

    def describe(self) -> str:
        what = "backfilled" if self.backfilled else "refreshed"
        return f"league {self.espn_league_id}: {what} {', '.join(map(str, self.seasons))}"


class FetchError(Exception):
    """ESPN refused or failed a fetch. Carries the original's class and
    nothing of its text, so what `record_run` writes into `ingest_runs.error`
    (which a signed-in route lists) cannot quote a login, however an error
    is worded."""

    def __init__(self, kind: type[BaseException]) -> None:
        super().__init__(f"the ESPN fetch failed ({kind.__name__})")
        self.kind = kind


def _fetch(settings: ESPNSettings, season: int) -> ESPNLeague:
    # `fetch_league` is a module global, looked up at call time, so
    # `monkeypatch.setattr(league_ingest, "fetch_league", fake)` is all a
    # test needs.
    try:
        return fetch_league(settings, season=season)
    except Exception as error:
        raise FetchError(type(error)) from None


def current_league(settings: ESPNSettings, today: date | None = None) -> ESPNLeague:
    """The season in progress, as `app.espn.fetch_current_league` finds it:
    the derived year, else the one before (ESPN creates a season late)."""
    if settings.espn_season:
        return _fetch(settings, settings.espn_season)
    derived = current_season(today)
    try:
        return _fetch(settings, derived)
    except Exception:
        return _fetch(settings, derived - 1)


def _stored_season(session: Session, league_pk: int, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.league_id == league_pk, LeagueSeason.season == season
        )
    )


def has_seasons(session: Session, espn_league_id: int) -> bool:
    """Whether anything of this league has been ingested yet."""
    found = session.scalar(
        select(LeagueSeason.id)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == espn_league_id)
        .limit(1)
    )
    return found is not None


def _ingest_season(
    factory: sessionmaker[Session],
    settings: ESPNSettings,
    league: ESPNLeague,
    *,
    recent_days: int | None,
) -> dict[str, Any]:
    season = int(league.year)
    mode = "full" if recent_days is None else "recent"
    with (
        record_run(
            factory, espn_league_id=settings.espn_league_id, season=season, mode=mode
        ) as detail,
        factory() as session,
    ):
        scope = FULL_SCOPE
        if recent_days is not None:
            stored_league = session.scalar(
                select(League).where(League.espn_league_id == settings.espn_league_id)
            )
            existing = (
                _stored_season(session, stored_league.id, season)
                if stored_league is not None
                else None
            )
            if existing is not None:
                scope = recent_scope(session, existing, league, recent_days)
        stored = ingest_season(session, league, scope)
        session.commit()
        detail.update(scope.describe())
        detail["counts"] = {
            "teams": len(stored.teams),
            "matchup_periods": len(stored.matchup_periods),
            "player_game_lines": session.scalar(
                select(func.count())
                .select_from(PlayerGameStat)
                .where(PlayerGameStat.season == stored.season)
            )
            or 0,
            "transactions": session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.league_season_id == stored.id)
            )
            or 0,
        }
        if scope.is_full and recent_days is not None:
            detail["note"] = "fell back to a full pass: nothing stored to narrow against"
    return detail


def _refresh_upcoming(factory: sessionmaker[Session], settings: ESPNSettings, season: int) -> str:
    """Next season's settings and teams while its draft is ahead; the
    script's `refresh_upcoming_season`, for this league's login."""
    try:
        league = _fetch(settings, season)
    except Exception as error:
        return f"{season}: not on ESPN yet ({type(error).__name__})"
    drafted_at = scheduled_draft(league)
    if not draft_is_pending(drafted_at):
        return f"{season}: drafted, settings left to the regular ingest"
    with (
        record_run(
            factory, espn_league_id=settings.espn_league_id, season=season, mode="settings"
        ) as detail,
        factory() as session,
    ):
        stored = ingest_season_settings(session, league)
        detail["counts"] = {"teams": len(stored.teams)}
        detail["drafted_at"] = drafted_at.isoformat() if drafted_at else None
        session.commit()
    return f"{season}: settings refreshed"


def ingest_league(
    factory: sessionmaker[Session],
    settings: ESPNSettings,
    *,
    recent_days: int = RECENT_DAYS,
    upcoming: bool = True,
) -> IngestSummary:
    """Refresh one league: the trailing days of the season in progress and,
    while its draft is ahead, next season's settings. A league never ingested
    before gets every season ESPN holds instead. Every season written is
    recorded in `ingest_runs`, succeeded or failed, as the script's are.

    Raises whatever ESPN or the database raised, untouched; the caller words it.
    """
    started = time.monotonic()
    summary = IngestSummary(espn_league_id=settings.espn_league_id)
    with factory() as session:
        first_time = not has_seasons(session, settings.espn_league_id)

    current = current_league(settings)
    if first_time:
        summary.backfilled = True
        for season in prior_seasons(current):
            _ingest_season(factory, settings, _fetch(settings, season), recent_days=None)
            summary.seasons.append(season)
        _ingest_season(factory, settings, current, recent_days=None)
    else:
        _ingest_season(factory, settings, current, recent_days=recent_days)
    summary.seasons.append(int(current.year))

    if upcoming:
        summary.lines.append(_refresh_upcoming(factory, settings, int(current.year) + 1))
    summary.lines.append(f"done in {time.monotonic() - started:.0f}s")
    return summary
