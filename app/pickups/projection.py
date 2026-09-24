"""What a player is expected to produce from today: a rate, a tilt, and games.

THE RATE

Per game, the knowable line (`app.scoring.knowable`): season to date pulled
toward the preseason projection with fifteen games of prior weight, and
fifteen percent of the last fortnight on top. The design note (docs/pickups.md
section 4.2) proposed a plain shrinkage blend with `k = 20`; the knowable line
was fitted afterwards on 7,165 player-checkpoints and beats that shape (2.85
against 2.90 error on the next 28 days), so it is used instead and the
note's formula is not reimplemented here. From 2027 the fit takes the latest
saved projection snapshot as its prior when the caller passes `as_of`.

THE TILT

The role-change signal, kept as a separate switchable factor so the backtest
can say whether it helps. When the listener has recorded a `minutes_spike`
or `minutes_drop` whose window ran through a game in the last
`TILT_WINDOW_DAYS`, every count in the line is scaled by the event's recent
minutes over the player's season minutes, capped to [`TILT_FLOOR`,
`TILT_CEILING`]. Every count, not only the scoring ones: shots scale with
minutes too, and scaling makes and attempts alike leaves the percentages
where they were. The window and caps are the note's starting values.

THE GAMES

Games are the caller's to count, because the week and the season count them
differently: `app.pickups.state.playable_days` reads the schedule for the
days left in a period, less what ESPN has ruled out. A rest-of-season line
is discounted once by `ESPN_AVAILABILITY`, the share of projected games
ESPN-projected players actually delivered (0.881, measured against 2026 in
the `app.draft.bbm` docstring). A rest-of-period line is not: the days left
in a week are a known schedule and a known status, not a season's worth of
unforeseen injuries.

THE CACHE

`per_game_line` and `rest_of_season_line` are pure functions of the stored
rows for a given `(player, season, today, games, tilt, as_of)`, and the
recommender asks for the same player's line many times over: once per roster
member when it values the roster, once per free agent when it ranks the
wire, again inside `app.pickups.judge` for the season charge, and again for
every re-run of the week a two-move plan costs. On the 2026 database that
was ~33,000 single-row queries for one decision point. So both are memoized
here, in a bounded LRU per function (`CACHE_SIZE` entries), and every caller
gets it: `stream` and `bids` import `per_game_line`, `season` and `judge`
import `rest_of_season_line`, and the cache being inside the function means
none of those import sites has to know about it. `rest_of_period_line` rides
on `per_game_line`'s.

`today` is part of the key, so a new scoring period is a new entry and no
answer is carried across days; a process restart clears the whole thing, and
nothing is persisted. The key also carries a **token for the session**, so
one session's answers are never served to another. That is not caution about
tests alone: the API is a long-lived process with a session per request, and
between two requests on the same day the listener may have written the box
scores of the day before, which `today`'s line reads. A per-session key
gives the cache exactly the lifetime the backtest's own context manager gave
it -- the span of one report -- while keeping the store module-level and
bounded. The token is an integer kept in `Session.info`, rather than the
session itself, so a finished session is not held alive by a cache entry
that has not been evicted yet.

Call `clear_cache()` to empty it; nothing in the recommender needs to.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import date
from itertools import count

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import PlayerGameStat, PlayerStatusEvent
from app.listener.events import MINUTES_DROP, MINUTES_SPIKE
from app.scoring.knowable import knowable
from app.scoring.lines import CategoryLine

#: A minutes event is live for this many days after the last game it ran
#: through (docs/pickups.md section 4.2).
TILT_WINDOW_DAYS = 10

#: The tilt's caps: a role cannot more than halve or grow by more than half
#: on three games' evidence (docs/pickups.md section 4.2).
TILT_FLOOR = 0.6
TILT_CEILING = 1.5

#: Share of ESPN-projected games actually played, measured against 2026 in
#: the `app.draft.bbm` docstring. Applied to a rest-of-season line only.
ESPN_AVAILABILITY = 0.88

#: The event kinds that carry a role change.
_MINUTES_KINDS = (MINUTES_SPIKE, MINUTES_DROP)

#: Lines each cache keeps before the least recently used one is dropped. One
#: streaming report asks for about two hundred distinct keys (a roster, an
#: opponent's roster and eighty free agents, per game and over the season)
#: and a season report for a few hundred more, so a few thousand holds a
#: whole day's work for one team without thrashing, and a `CategoryLine` is
#: eleven floats.
CACHE_SIZE = 4096

#: Where a session's cache token is kept on the session itself.
_TOKEN_KEY = "app.pickups.projection.token"
_tokens = count(1)


def _session_token(session: Session) -> int:
    """A small integer standing for this session, for the cache key.

    The session is not itself put in the key: an entry that has not been
    evicted would then keep a finished session, and its whole identity map,
    alive. The token is stored on the session, so it dies with it.
    """
    token = session.info.get(_TOKEN_KEY)
    if token is None:
        token = next(_tokens)
        session.info[_TOKEN_KEY] = token
    return int(token)


class _LineCache:
    """A bounded LRU of category lines. Not thread-safe, like a Session."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._entries: OrderedDict[Hashable, CategoryLine] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable, build: Callable[[], CategoryLine]) -> CategoryLine:
        found = self._entries.get(key)
        if found is not None:
            self._entries.move_to_end(key)
            self.hits += 1
            return found
        self.misses += 1
        line = build()
        self._entries[key] = line
        if len(self._entries) > self._size:
            self._entries.popitem(last=False)
        return line

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0


_PER_GAME = _LineCache(CACHE_SIZE)
_REST_OF_SEASON = _LineCache(CACHE_SIZE)


def clear_cache() -> None:
    """Empty both caches. For a long-lived process that wants a clean slate."""
    _PER_GAME.clear()
    _REST_OF_SEASON.clear()


def cache_stats() -> dict[str, int]:
    """Hits and misses per cache, for measuring a run."""
    return {
        "per_game_hits": _PER_GAME.hits,
        "per_game_misses": _PER_GAME.misses,
        "rest_of_season_hits": _REST_OF_SEASON.hits,
        "rest_of_season_misses": _REST_OF_SEASON.misses,
    }


@dataclass(frozen=True)
class MinutesTilt:
    """A live minutes event and the factor it puts on the line."""

    kind: str
    #: The event's recent-games mean over his season mean, before the caps.
    raw: float
    factor: float
    through_scoring_period: int


def minutes_tilt(session: Session, season: int, player_id: int, today: int) -> MinutesTilt | None:
    """The live minutes event's tilt, or None when there is none.

    The latest spike or drop on record is live when the last game its window
    ran through (`detail.through_scoring_period`) is within
    `TILT_WINDOW_DAYS` of `today`. Season minutes are his played games
    before `today`; a player with none falls back to the event's own prior
    mean, which is the same comparison over fewer games.
    """
    event = session.scalar(
        select(PlayerStatusEvent)
        .where(
            PlayerStatusEvent.player_id == player_id,
            PlayerStatusEvent.season == season,
            PlayerStatusEvent.kind.in_(_MINUTES_KINDS),
        )
        .order_by(PlayerStatusEvent.observed_at.desc())
        .limit(1)
    )
    if event is None:
        return None
    through = event.detail.get("through_scoring_period")
    recent = event.detail.get("recent_mean")
    if through is None or recent is None or int(through) < today - TILT_WINDOW_DAYS:
        return None
    season_minutes = session.scalar(
        select(func.avg(PlayerGameStat.minutes)).where(
            PlayerGameStat.player_id == player_id,
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period < today,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes > 0,
        )
    )
    prior = float(event.detail.get("prior_mean") or 0)
    baseline = float(season_minutes) if season_minutes else prior
    if baseline <= 0:
        return None
    raw = float(recent) / baseline
    return MinutesTilt(
        kind=str(event.kind),
        raw=raw,
        factor=min(TILT_CEILING, max(TILT_FLOOR, raw)),
        through_scoring_period=int(through),
    )


def per_game_line(
    session: Session,
    season: int,
    player_id: int,
    today: int,
    *,
    tilt: bool = True,
    as_of: date | None = None,
) -> CategoryLine:
    """The knowable per-game line as of `today`, tilted when a role changed.

    Memoized for the life of `session` (the module docstring).
    """
    key = (_session_token(session), player_id, season, today, tilt, as_of)
    return _PER_GAME.get(
        key, lambda: _per_game_line(session, season, player_id, today, tilt=tilt, as_of=as_of)
    )


def _per_game_line(
    session: Session,
    season: int,
    player_id: int,
    today: int,
    *,
    tilt: bool,
    as_of: date | None,
) -> CategoryLine:
    line = knowable(session, player_id, season, today, as_of=as_of).per_game
    if tilt:
        found = minutes_tilt(session, season, player_id, today)
        if found is not None:
            line = line.scaled(found.factor)
    return line


def rest_of_period_line(
    session: Session,
    season: int,
    player_id: int,
    today: int,
    games: int,
    *,
    tilt: bool = True,
    as_of: date | None = None,
) -> CategoryLine:
    """Expected counts over `games` games this period: the rate, times games.

    No availability discount: the caller has already counted only the days
    he has a game and is not ruled out of.
    """
    return _over(per_game_line(session, season, player_id, today, tilt=tilt, as_of=as_of), games)


def rest_of_season_line(
    session: Session,
    season: int,
    player_id: int,
    today: int,
    games: float,
    *,
    tilt: bool = True,
    as_of: date | None = None,
) -> CategoryLine:
    """Expected counts over the rest of the season, discounted for availability.

    `games` is his NBA team's remaining games less the days he will not play,
    and it is a `float` because a man who is ruled out is counted for the
    games he is *expected* to play rather than for all or none
    (`app.pickups.state.RosteredPlayer.season_games`, the declared rule of
    `docs/stash_mode.md`). `ESPN_AVAILABILITY` then takes the share a season's
    unforeseen absences cost.

    Memoized for the life of `session` (the module docstring); `games` is
    part of the key, since the same player is counted over several horizons.
    """
    key = (_session_token(session), player_id, season, today, games, tilt, as_of)
    return _REST_OF_SEASON.get(
        key,
        lambda: _over(
            per_game_line(session, season, player_id, today, tilt=tilt, as_of=as_of),
            games * ESPN_AVAILABILITY,
        ),
    )


def _over(rate: CategoryLine, games: float) -> CategoryLine:
    return CategoryLine({key: value * games for key, value in rate.counts.items()}, round(games))
