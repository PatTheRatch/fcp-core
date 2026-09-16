"""What a manager could have known about a player on a given day.

The decision lens grades a move on what was knowable when it was made, not
on what followed. Before 2027 no in-season projections were saved, so the
knowable line is rebuilt from what was on record that day: the preseason
projection and the player's own games so far.

THE BLEND, MEASURED

Per-game rates, each counting stat and shot component:

    to_date_weight = games so far / (games so far + PRIOR_GAMES)
    base  = to_date_weight * season-to-date rate + (1 - to_date_weight) * projection
    rate  = (1 - RECENT_WEIGHT) * base + RECENT_WEIGHT * last-RECENT_DAYS rate

Fitted 2026-09-16 on the live database: 7,165 player-checkpoints (days 21 to
126, every sixth of the season) in the six seasons with real preseason
projections (2019, 2021, 2022, 2024, 2025, 2026), predicting each player's
per-game rates over the next 28 days, error summed over the eleven counts
with each scaled by its spread (lower is better):

| knowable line | error |
|---|---|
| preseason projection alone | 4.36 |
| last 14 days alone | 5.32 |
| season to date alone | 3.26 |
| projection and last 14 days, best mix (0.4 recent) | 3.33 |
| season to date shrunk to projection, n/(n+10) | 2.90 |
| **the blend above, PRIOR_GAMES 15, RECENT_WEIGHT 0.15** | **2.85** |

The ticket proposed the projection and the last 14 days. Season to date
beats that mix on its own, so it is in; recent form still earns a small
share on top. The surface is flat near the optimum (2.855 to 2.878 for
PRIOR_GAMES 10-20 and RECENT_WEIGHT 0.1-0.2), so the round numbers are
chosen for being easy to explain.

A season with no usable projection (2020, 2023: `app.draft.projections`)
or a player with none uses his season to date alone, then recent form; a
player with no games and no projection has an empty line.

From 2027, a saved daily projection snapshot (`player_projection_snapshots`,
S10) on or before the day replaces this reconstruction when one exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlayerGameStat, PlayerSeasonStat
from app.draft.projections import usable
from app.scoring.lines import COUNTS, CategoryLine

#: How many games of evidence the preseason projection counts as.
PRIOR_GAMES = 15

#: The share of the rate taken from the last `RECENT_DAYS` days of games.
RECENT_WEIGHT = 0.15
RECENT_DAYS = 14


@dataclass(frozen=True)
class Knowable:
    """A per-game rate line, and what it was built from."""

    per_game: CategoryLine
    games_so_far: int
    recent_games: int
    had_projection: bool
    source: str  # "blend" or "snapshot"

    def over(self, games: float) -> CategoryLine:
        """The rate line for `games` games."""
        return CategoryLine({k: v * games for k, v in self.per_game.counts.items()}, round(games))


def _rate(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not rows:
        return dict.fromkeys(COUNTS, 0.0)
    return {
        key: sum(float(r[column] or 0.0) for r in rows) / len(rows)
        for key, column in COUNTS.items()
    }


def _mix(a: Mapping[str, float], b: Mapping[str, float], weight_a: float) -> dict[str, float]:
    return {key: weight_a * a.get(key, 0.0) + (1 - weight_a) * b.get(key, 0.0) for key in COUNTS}


def projection_rate(session: Session, player_id: int, season: int) -> dict[str, float] | None:
    """The preseason projection per game, or None when there is no usable one."""
    if not usable(season):
        return None
    row = session.scalar(
        select(PlayerSeasonStat).where(
            PlayerSeasonStat.player_id == player_id,
            PlayerSeasonStat.season == season,
            PlayerSeasonStat.kind == "projected",
        )
    )
    if row is None or not row.games_played:
        return None
    games = float(row.games_played)
    return {key: float(getattr(row, column) or 0.0) / games for key, column in COUNTS.items()}


def knowable(session: Session, player_id: int, season: int, day: int) -> Knowable:
    """The per-game line knowable at the end of scoring period `day - 1`.

    Only games before `day` count: a move made on a day is judged before
    that day's games.
    """
    columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    rows = session.execute(
        select(PlayerGameStat.scoring_period, *columns).where(
            PlayerGameStat.player_id == player_id,
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period < day,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes > 0,
        )
    ).all()
    games = [dict(row._mapping) for row in rows]
    recent = [g for g in games if int(g["scoring_period"]) >= day - RECENT_DAYS]

    projected = projection_rate(session, player_id, season)
    to_date = _rate(games)
    if projected is None:
        base = to_date
    else:
        base = _mix(to_date, projected, len(games) / (len(games) + PRIOR_GAMES))
    rate = _mix(_rate(recent), base, RECENT_WEIGHT) if recent else base
    if not games and projected is None:
        rate = dict.fromkeys(COUNTS, 0.0)
    return Knowable(
        per_game=CategoryLine(rate, 1),
        games_so_far=len(games),
        recent_games=len(recent),
        had_projection=projected is not None,
        source="blend",
    )


def knowable_line(
    session: Session, player_id: int, season: int, day: int, games: float
) -> CategoryLine:
    """The knowable line for `games` games, as of `day`."""
    return knowable(session, player_id, season, day).over(games)
