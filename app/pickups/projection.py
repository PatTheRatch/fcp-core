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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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
    """The knowable per-game line as of `today`, tilted when a role changed."""
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
    games: int,
    *,
    tilt: bool = True,
    as_of: date | None = None,
) -> CategoryLine:
    """Expected counts over the rest of the season, discounted for availability.

    `games` is his NBA team's remaining games less the days before his
    return date; `ESPN_AVAILABILITY` then takes the share a season's
    unforeseen absences cost.
    """
    rate = per_game_line(session, season, player_id, today, tilt=tilt, as_of=as_of)
    return _over(rate, games * ESPN_AVAILABILITY)


def _over(rate: CategoryLine, games: float) -> CategoryLine:
    return CategoryLine({key: value * games for key, value in rate.counts.items()}, round(games))
