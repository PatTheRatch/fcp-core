"""Minimal ESPN fantasy basketball client.

Read-only. Fetches one league via the `espn-api` package; nothing here
persists anything. Kept separate from `app.config.Settings` so ESPN
credentials are not required to boot the API or run the DB test suite.
"""

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
    espn_season: int
    espn_swid: str
    espn_s2: str


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


def fetch_league(settings: ESPNSettings) -> League:
    """Fetch one ESPN league."""
    _install_timeout_patch()

    return League(
        league_id=settings.espn_league_id,
        year=settings.espn_season,
        espn_s2=settings.espn_s2,
        swid=settings.espn_swid,
    )
