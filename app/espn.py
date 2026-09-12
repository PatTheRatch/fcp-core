"""Minimal ESPN fantasy basketball client.

Read-only. Fetches one league via the `espn-api` package; nothing here
persists anything. Kept separate from `app.config.Settings` so ESPN
credentials are not required to boot the API or run the DB test suite.
"""

from datetime import UTC, date, datetime
from functools import lru_cache

from espn_api.basketball import League
from pydantic_settings import BaseSettings, SettingsConfigDict

#: espn-api issues its internal `requests.get()` calls with no timeout, so a
#: stalled ESPN response hangs the caller forever. (Confirmed by reading
#: espn_api/requests/espn_requests.py; not documented anywhere upstream.)
ESPN_TIMEOUT_SECONDS = (5, 15)  # (connect, read)

_TIMEOUT_PATCHED = False


class ESPNSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    espn_league_id: int
    espn_swid: str
    espn_s2: str

    #: Optional. Leave unset and the current season is derived from the date.
    #: Set it only to pin a run to one particular year; a pinned value will
    #: happily keep refreshing a season that ended months ago.
    espn_season: int | None = None


@lru_cache
def get_espn_settings() -> ESPNSettings:
    """Load ESPN settings once per process. Raises if a required value is missing."""
    return ESPNSettings()


def _install_timeout_patch() -> None:
    """Give espn-api's HTTP calls an explicit timeout. Idempotent.

    This process only ever talks to ESPN, so patching the shared `requests`
    module directly (rather than scoping the patch to espn-api's own import)
    is fine here.
    """
    global _TIMEOUT_PATCHED
    if _TIMEOUT_PATCHED:
        return

    import requests

    original_get = requests.get

    def get_with_timeout(*args: object, **kwargs: object) -> "requests.Response":
        kwargs.setdefault("timeout", ESPN_TIMEOUT_SECONDS)
        return original_get(*args, **kwargs)  # type: ignore[arg-type]

    requests.get = get_with_timeout
    _TIMEOUT_PATCHED = True


#: ESPN labels an NBA season by the calendar year it ends in, so 2026-27 is
#: season 2027. Play starts in October, which is where the label turns over.
SEASON_ROLLOVER_MONTH = 10


def current_season(today: date | None = None) -> int:
    """The season ESPN is on for a given date.

    Derived rather than configured. A pinned season silently goes stale the
    moment the next one starts: the ingest keeps succeeding against a year
    that finished months ago, and nothing looks wrong.
    """
    today = today or datetime.now(UTC).date()
    return today.year + 1 if today.month >= SEASON_ROLLOVER_MONTH else today.year


def resolve_season(settings: ESPNSettings, today: date | None = None) -> int:
    """The season to read: the configured override, or today's."""
    return settings.espn_season or current_season(today)


def fetch_league(settings: ESPNSettings, season: int | None = None) -> League:
    """Fetch one ESPN league, defaulting to the current season.

    Pass `season` to read a prior year. ESPN serves past seasons from a
    different endpoint (`leagueHistory`), which `espn-api` selects on the
    year, so nothing else here changes.
    """
    _install_timeout_patch()

    return League(
        league_id=settings.espn_league_id,
        year=resolve_season(settings) if season is None else season,
        espn_s2=settings.espn_s2,
        swid=settings.espn_swid,
    )


def fetch_current_league(settings: ESPNSettings, today: date | None = None) -> League:
    """The league for the season now in progress.

    Tries the derived season and falls back one year if it is not there yet.
    ESPN creates a season shortly before play begins, so between the turn of
    October and that moment the newer label does not resolve. Failing over is
    better than a fortnight of failed nightly runs, and better still than the
    pinned-season alternative, which fails silently instead of loudly.

    A configured `ESPN_SEASON` skips all of this and is used as given.
    """
    if settings.espn_season:
        return fetch_league(settings, season=settings.espn_season)

    derived = current_season(today)
    try:
        return fetch_league(settings, season=derived)
    except Exception:
        # Not an error worth surfacing on its own: the previous season is the
        # right answer until ESPN publishes the new one.
        return fetch_league(settings, season=derived - 1)


def prior_seasons(league: League) -> list[int]:
    """Seasons before this one that ESPN still holds for the league.

    Taken from the league's own `previousSeasons` rather than guessed by
    probing years.
    """
    seasons = getattr(league, "previousSeasons", None) or []
    return sorted(int(year) for year in seasons)
