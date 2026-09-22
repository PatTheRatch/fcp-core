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

THE PRIOR, AS A PARAMETER

The blend is fitted on ESPN's stored preseason projection, and that is what it
uses when nothing else is offered: the numbers above are the numbers, not a
version of them. A caller measuring a different prior -- Basketball Monster's
export, a manager's own file -- passes `prior`, either a mapping of player id
to a per-game rate over `COUNTS` or a callable taking the player id and
returning one or None, and the blend, the weights and the fallbacks are
exactly the same arithmetic with a different number shrunk toward. That is
the seam `scripts/projection_prior.py` uses to ask whether the in-season tool
is better on another vendor's forecast. A player the given prior does not
cover is treated as a player with no projection at all, which is the rule
already in force for a season ESPN never published one for.

COUNTING A GAME

`played` is not the filter; `played` **and** `minutes > 0` is. Ten 2026 games
carry a row with minutes zero, and summing them into the to-date rate as a
goose egg drags a man's line down for a game he never played.

From 2027, pass `as_of` (the calendar date of the move): the latest saved
projection snapshot on or before it (`player_projection_snapshots`, S10)
stands in for the preseason projection in the blend. It replaces the prior,
not the whole line, because whether ESPN refreshes its projection in season
was not measurable before the season (the S1 probe, branch `scoring-s1`);
if it turns out to be a true rest-of-season forecast, it should earn more
weight, and the first in-season month of snapshots is how to fit that. A
caller that passes `prior` overrides both, since it is asking about a prior
that is not ESPN's at all.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlayerGameStat, PlayerProjectionSnapshot, PlayerSeasonStat
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


#: A per-game rate over `COUNTS`, the shape every prior is reduced to.
Rate = dict[str, float]

#: Where the blend takes its prior from, when it is not ESPN's stored
#: projection: a player id to his per-game rate, or None. The lookup form is
#: wrapped by `prior_from`, since a mapping is not callable.
Prior = Callable[[int], "Rate | None"]


def prior_from(source: Mapping[int, Rate] | Prior) -> Prior:
    """A prior lookup from a mapping of player id to per-game rate.

    Every prior is a plain dict of `COUNTS`; this is the one place a caller's
    mapping is turned into the callable the blend takes, so the blend itself
    has a single shape to reason about.
    """
    if callable(source):
        return source
    return lambda player_id: source.get(player_id)


def rate_from_totals(totals: Mapping[str, float], games: float) -> Rate | None:
    """A per-game rate from season totals and a games projection, or None.

    Takes `COUNTS` keys; `FGM` and `FTM` are in it because they are the made
    halves of the two percentages, which `CategoryLine` rebuilds from them.
    A rate that carries only the nine scored categories leaves a line whose
    percentages have no attempts behind them, which is why a file's own
    `fg%` and `ft%` columns are used to reconstruct the makes rather than
    assumed absent.
    """
    if games <= 0:
        return None
    return {key: float(totals.get(key) or 0.0) / games for key in COUNTS}


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


def snapshot_rate(
    session: Session, player_id: int, season: int, as_of: date
) -> dict[str, float] | None:
    """The latest saved projection on or before `as_of`, per game, or None."""
    row = session.scalar(
        select(PlayerProjectionSnapshot)
        .where(
            PlayerProjectionSnapshot.player_id == player_id,
            PlayerProjectionSnapshot.season == season,
            PlayerProjectionSnapshot.kind == "projected",
            PlayerProjectionSnapshot.captured_on <= as_of,
        )
        .order_by(PlayerProjectionSnapshot.captured_on.desc())
        .limit(1)
    )
    if row is None or not row.games_played:
        return None
    games = float(row.games_played)
    return {key: float(row.stats.get(key) or 0.0) / games for key in COUNTS}


def knowable(
    session: Session,
    player_id: int,
    season: int,
    day: int,
    *,
    as_of: date | None = None,
    prior: Prior | None = None,
) -> Knowable:
    """The per-game line knowable at the end of scoring period `day - 1`.

    Only games before `day` count: a move made on a day is judged before
    that day's games.

    `prior` replaces the preseason projection in the blend -- see the module
    docstring. Left out, the line is ESPN's stored projection and this is the
    arithmetic the docstring's fit was taken on.
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

    if prior is not None:
        projected = prior(player_id)
        snapshot = None
    else:
        snapshot = snapshot_rate(session, player_id, season, as_of) if as_of else None
        projected = (
            snapshot if snapshot is not None else projection_rate(session, player_id, season)
        )
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
        source="snapshot" if snapshot is not None else "blend",
    )


def knowable_line(
    session: Session, player_id: int, season: int, day: int, games: float
) -> CategoryLine:
    """The knowable line for `games` games, as of `day`."""
    return knowable(session, player_id, season, day).over(games)
