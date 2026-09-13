"""Minimal ESPN fantasy basketball client.

Read-only. Fetches one league via the `espn-api` package; nothing here
persists anything. Kept separate from `app.config.Settings` so ESPN
credentials are not required to boot the API or run the DB test suite.
"""

import json
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

from espn_api.basketball import League
from espn_api.basketball.constant import POSITION_MAP
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


#: Transaction types worth storing. FUTURE_ROSTER is excluded on purpose: it
#: is daily lineup shuffling, 53 of 86 rows on a sampled day, and already
#: recorded far better in `daily_lineup_slots`.
TRANSACTION_TYPES = (
    "FREEAGENT",
    "WAIVER",
    "WAIVER_ERROR",
    "TRADE_ACCEPT",
    "TRADE_PROPOSAL",
    "TRADE_DECLINE",
    "TRADE_UPHOLD",
    "TRADE_VETO",
    "TRADE_ERROR",
)


def fetch_transactions(league: League, scoring_period: int) -> list[dict[str, Any]]:
    """Raw transactions for one day.

    Parsed here rather than through `League.transactions()`, which is unusable
    for this: it drops the transaction id, drops `fromTeamId` and `toTeamId`
    on every item so a trade cannot be read, and raises outright on a
    TRADE_UPHOLD, which carries no items at all. Its HTTP layer is still used,
    so endpoint selection and the history fallback stay in one place.

    A scoring period must be given. The default is the league's current
    scoring period, which runs past the end of the fantasy season and returns
    a payload with no transactions key.
    """
    headers = {
        "x-fantasy-filter": json.dumps(
            {"transactions": {"filterType": {"value": list(TRANSACTION_TYPES)}}}
        )
    }
    data = league.espn_request.league_get(
        params={"view": "mTransactions2", "scoringPeriodId": scoring_period},
        headers=headers,
    )
    found = (data or {}).get("transactions") or []
    return [item for item in found if isinstance(item, dict)]


def player_names(league: League) -> dict[int, str]:
    """ESPN player id -> name, for everyone the league knows about.

    Transactions reference players by id alone, including ones never rostered
    long enough to appear in a box score, so this is how they get named.
    """
    raw = getattr(league, "player_map", None) or {}
    return {int(k): str(v) for k, v in raw.items() if isinstance(k, int) and isinstance(v, str)}


#: ESPN's `defaultPositionId` is one-based over the same order as
#: POSITION_MAP, so id 5 is POSITION_MAP[4], the centre. `positionLimits` is
#: keyed by that id, which is how "at most three centres" is expressed.
_POSITION_ID_OFFSET = 1

#: A limit of -1 means unlimited. ESPN also emits an entry for position id 0,
#: which matches no player, so it is dropped rather than treated as a ban.
_UNLIMITED = -1


def position_name(default_position_id: int) -> str | None:
    """The position a `defaultPositionId` names, or None if it names none."""
    index = int(default_position_id) - _POSITION_ID_OFFSET
    if index < 0:
        return None
    name = POSITION_MAP.get(index)
    return str(name) if isinstance(name, str) else None


def fetch_roster_settings(league: League) -> dict[str, Any]:
    """The league's roster rules: lineup slots, bench, IR, position limits.

    Read from ESPN rather than inferred. The lineup had been inferred by
    counting slots in box scores, which happened to be right, and the
    position limits were missed entirely: this league caps centres, at three
    in 2025 and 2026 and four in 2027. Both are season settings and both
    move, so neither can be a constant.
    """
    data = league.espn_request.league_get(params={"view": "mSettings"}) or {}
    roster = (data.get("settings") or {}).get("rosterSettings") or {}
    counts = roster.get("lineupSlotCounts") or {}

    lineup: dict[str, int] = {}
    bench = 0
    injured_reserve = 0
    for raw_slot, raw_count in counts.items():
        count = int(raw_count or 0)
        if count <= 0:
            continue
        name = POSITION_MAP.get(int(raw_slot))
        if name == "BE":
            bench = count
        elif name == "IR":
            injured_reserve = count
        elif isinstance(name, str) and name:
            lineup[name] = count

    limits: dict[str, int] = {}
    for raw_id, raw_limit in (roster.get("positionLimits") or {}).items():
        limit = int(raw_limit)
        if limit == _UNLIMITED:
            continue
        name = position_name(int(raw_id))
        if name:
            limits[name] = limit

    return {
        "lineup_slots": lineup,
        "bench_slots": bench,
        "injured_reserve_slots": injured_reserve,
        "position_limits": limits,
    }


def prior_seasons(league: League) -> list[int]:
    """Seasons before this one that ESPN still holds for the league.

    Taken from the league's own `previousSeasons` rather than guessed by
    probing years.
    """
    seasons = getattr(league, "previousSeasons", None) or []
    return sorted(int(year) for year in seasons)
