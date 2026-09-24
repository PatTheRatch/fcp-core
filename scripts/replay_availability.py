#!/usr/bin/env python3
"""How much of the replay's error is availability, now that OUT men are known.

Usage:
    python scripts/replay_availability.py
    python scripts/replay_availability.py --season 2026 --limit 4

`docs/availability.md` section 3 asked this of the projection's own fit and
answered 67.29% on 2026's six checkpoints. It could not be asked of the
recommender, because a replayed 2026 morning had no injury status at all and
so had no availability to attribute anything to. Since the declared status
source of `docs/replay_status.md` it can.

WHAT IS DECOMPOSED, AND WHY IT IS THIS QUANTITY

**The per-man rest-of-season line, in categories a week.** Not the move-level
error, and the distinction matters enough to state.

`app.pickups.judge.weekly_lines` is, in its own docstring, "the one place the
season charge counts games". Every rest-of-season number the recommender
publishes -- the season report's net, the week report's season half, a
trade's finish, a stash's expected value -- is built from it, and it is the
only place a games term enters at all. So the games term's share of the
error is exactly measurable there, on the same men the backtest recommends
between, through the same league-standard lens the backtest prices in.

The move-level error would confound three things at once: the games term, the
lineup re-solve on both sides, and which men the wire search happened to
rank. The before-and-after of that number is in
`docs/pickups_backtest.md` section 0; this is the attribution under it.

THE FOUR READINGS

For every man on a 2026 roster on each of the backtest's own decision points,
his rest-of-season line per week against what his box scores were really
worth per week over the same stretch:

1. **status-blind** -- every man counted for all his team's remaining games,
   which is what a replayed 2026 morning read before this change;
2. **statuses visible** -- the declared source order's answer, which is what
   it reads now;
3. **the games oracle** -- the same per-game rate given the games he really
   played, which is the best a games term could do and is status-blind in the
   sense that no system could have it;
4. **a zero line** -- the floor a broken forecast sits above.

The share the games term carries is `1 - oracle / published`, which is the
reading `docs/availability.md` section 3b takes from the same four rows.

An attribution, not a partition: the metric is a mean absolute deviation, so
holding the games term perfect and holding the rate perfect are two readings
of one quantity and they do not add to it (`docs/availability.md`,
limitation 5).

Read-only. Every query is a SELECT and nothing is written to the database.
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DailyLineupSlot, LeagueSeason, Player, PlayerGameStat, Team
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups import state, status_source
from app.pickups.judge import horizon, standard_lens, weeks_between
from app.pickups.projection import rest_of_season_line
from app.pickups.state import build_players, season_calendar
from app.pickups.status_source import NO_SOURCE, StatusRead
from app.scoring.lines import COUNTS, CategoryLine
from scripts.pickups_backtest import (
    SEASON,
    decision_points,
    load_season,
    patched_state,
    rebuild_schedule,
)
from scripts.stashes import connect

#: Days in a matchup week, the divisor every weekly quantity here uses.
DAYS_A_WEEK = 7.0

#: The four readings, in the order the table prints them.
BLIND = "status-blind: every man counted for all his team's games"
VISIBLE = "statuses visible: the declared source order"
ORACLE = "the games oracle: the same rate, given the games he really played"
ZERO = "a zero line"

_SILENT = StatusRead(
    source=NO_SOURCE, read_as_of=None, reported_at=None, statuses={}, placed=0, unmatched=0
)


@dataclass(frozen=True)
class Man:
    """One man on one decision morning, priced three ways and graded once."""

    day: int
    player_id: int
    name: str
    #: His rest-of-season games under each reading.
    blind_games: float
    visible_games: float
    real_games: int
    #: Categories a week, through the morning's own league-standard lens.
    blind: float
    visible: float
    oracle: float
    delivered: float
    #: Whether the morning's source had anything to say about him.
    named: str | None


def rostered_on(session: Session, league_season: LeagueSeason, day: int) -> dict[int, int]:
    """Every man every team of the league held on `day`, to his team row.

    The latest lineup day at or before `day`, per team, which is the same
    roster `app.pickups.state.load_team_week` reads.
    """
    latest = (
        select(
            DailyLineupSlot.team_id,
            func.max(DailyLineupSlot.scoring_period).label("day"),
        )
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .where(Team.league_season_id == league_season.id, DailyLineupSlot.scoring_period <= day)
        .group_by(DailyLineupSlot.team_id)
        .subquery()
    )
    rows = session.execute(
        select(DailyLineupSlot.player_id, DailyLineupSlot.team_id)
        .join(
            latest,
            (DailyLineupSlot.team_id == latest.c.team_id)
            & (DailyLineupSlot.scoring_period == latest.c.day),
        )
        .where(DailyLineupSlot.slot != state.GONE_SLOT)
    ).all()
    return {int(player_id): int(team_id) for player_id, team_id in rows}


def delivered_weekly(
    session: Session, season: int, player_id: int, first: int, last: int
) -> tuple[CategoryLine, int]:
    """What he really posted over the window, per week, and his games.

    `scripts/trade_calibration._delivered_weekly` word for word, so the
    delivered side of this table and of the trade record are one quantity.
    """
    columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    found = session.execute(
        select(*columns).where(
            PlayerGameStat.player_id == player_id,
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period.between(first, last),
            PlayerGameStat.played.is_(True),
        )
    ).all()
    line = CategoryLine(
        {key: sum(float(row[index] or 0.0) for row in found) for index, key in enumerate(COUNTS)},
        len(found),
    )
    return line.scaled(DAYS_A_WEEK / max(1, last - first + 1)), len(found)


def _games(
    session: Session, league_season: LeagueSeason, ids: Sequence[int], days: Sequence[int]
) -> dict[int, tuple[float, str | None]]:
    """Each man's expected rest-of-season games, and the status behind it."""
    return {
        player.player_id: (player.season_games, player.injury_status)
        for player in build_players(session, league_season, ids, days)
    }


def _blind(
    session: Session, league_season: LeagueSeason, ids: Sequence[int], days: Sequence[int]
) -> dict[int, tuple[float, str | None]]:
    """The same count with the status source forced silent.

    Attribute substitution on the one function `build_players` reads a status
    through, put back in a `finally`, which is how `patched_state` installs
    the reconstructed schedule beside it. Nothing on disk changes and the
    memo the real source keeps is dropped on the way in and on the way out.
    """
    real = status_source.read_statuses
    status_source.read_statuses = lambda *_args, **_kwargs: _SILENT
    session.info.pop("pickups_status_source", None)
    try:
        return _games(session, league_season, ids, days)
    finally:
        status_source.read_statuses = real
        session.info.pop("pickups_status_source", None)


def one_day(
    session: Session,
    league_season: LeagueSeason,
    day: int,
    distributions: Sequence[CategoryDistribution],
) -> list[Man]:
    """Every rostered man on this morning, priced three ways and graded."""
    season = int(league_season.season)
    _first, last, today = horizon(session, league_season, day)
    days = tuple(range(today, last + 1))
    weeks = weeks_between(today, last)
    ids = sorted(rostered_on(session, league_season, day))
    if not ids:
        return []
    lens = standard_lens(session, league_season, day, distributions)
    visible = _games(session, league_season, ids, days)
    blind = _blind(session, league_season, ids, days)
    names = {
        int(player_id): str(name)
        for player_id, name in session.execute(
            select(Player.id, Player.name).where(Player.id.in_(ids))
        ).all()
    }

    def priced(player_id: int, games: float) -> float:
        line = rest_of_season_line(session, season, player_id, today, games, tilt=True)
        return lens.value(line.scaled(1.0 / weeks))

    out: list[Man] = []
    for player_id in ids:
        got, status = visible.get(player_id, (0.0, None))
        was, _ = blind.get(player_id, (0.0, None))
        line, real = delivered_weekly(session, season, player_id, today, last)
        out.append(
            Man(
                day=day,
                player_id=player_id,
                name=str(names.get(player_id, player_id)),
                blind_games=was,
                visible_games=got,
                real_games=real,
                blind=priced(player_id, was),
                visible=priced(player_id, got),
                oracle=priced(player_id, float(real)),
                delivered=lens.value(line),
                named=status,
            )
        )
    return out


def _mae(values: Sequence[float]) -> float:
    return statistics.fmean([abs(value) for value in values]) if values else 0.0


def table(men: Sequence[Man]) -> list[str]:
    """The one table, in the shape `docs/availability.md` section 3b prints."""
    readings: Mapping[str, list[float]] = {
        BLIND: [man.blind - man.delivered for man in men],
        VISIBLE: [man.visible - man.delivered for man in men],
        ORACLE: [man.oracle - man.delivered for man in men],
        ZERO: [0.0 - man.delivered for man in men],
    }
    published = _mae(readings[VISIBLE])
    lines = [
        "| reading | mean absolute error (categories a week) | mean error | "
        "share of the published error it removes |",
        "|---|---|---|---|",
    ]
    for label, errors in readings.items():
        if not errors:
            lines.append(f"| {label} | -- | -- | -- |")
            continue
        mae = _mae(errors)
        # Against the reading the product publishes, which is the statuses
        # visible one. A negative share is a reading that is worse than it.
        share = "-- (the reference)" if label == VISIBLE else f"{1 - mae / published:+.2%}"
        lines.append(f"| {label} | {mae:.4f} | {statistics.fmean(errors):+.4f} | {share} |")
    return lines


def status_split(men: Sequence[Man]) -> list[str]:
    """The same error, split by what the morning said about the man."""
    groups: dict[str, list[Man]] = {}
    for man in men:
        groups.setdefault(man.named or "silent", []).append(man)
    lines = [
        "| morning status | n | mean games, blind | visible | really played | "
        "MAE blind | MAE visible |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, rows in sorted(groups.items(), key=lambda pair: -len(pair[1])):
        lines.append(
            f"| {label} | {len(rows)} | "
            f"{statistics.fmean([row.blind_games for row in rows]):.2f} | "
            f"{statistics.fmean([row.visible_games for row in rows]):.2f} | "
            f"{statistics.fmean([float(row.real_games) for row in rows]):.2f} | "
            f"{_mae([row.blind - row.delivered for row in rows]):.4f} | "
            f"{_mae([row.visible - row.delivered for row in rows]):.4f} |"
        )
    return lines


def run(session: Session, season: int, limit: int | None) -> list[str]:
    league_season = load_season(session, season)
    points = decision_points(session, league_season)
    if limit:
        points = points[:limit]
    distributions = category_distributions(session, league_season)
    calendar = season_calendar(session, season)
    men: list[Man] = []
    with patched_state():
        for index, (_period, day) in enumerate(points, start=1):
            men.extend(one_day(session, league_season, day, distributions))
            print(f"   ... {index}/{len(points)} decision points, {len(men)} man-mornings")
    named = sum(1 for man in men if man.named)
    out = [
        f"season {season}, {len(points)} decision points, {len(men)} man-mornings, "
        f"{named} of them ({named / max(1, len(men)):.1%}) with a status on the morning",
        f"calendar: {calendar}",
        "",
        *table(men),
        "",
        *status_split(men),
    ]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=SEASON)
    parser.add_argument("--limit", type=int, default=None, help="Decision points, for a smoke run")
    args = parser.parse_args()
    with connect() as session:
        # The 2026 schedule the recommender counts games from is the
        # reconstructed one, exactly as the backtest installs it.
        assert rebuild_schedule is not None
        for line in run(session, args.season, args.limit):
            print(line)


if __name__ == "__main__":
    main()
