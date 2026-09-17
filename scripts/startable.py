#!/usr/bin/env python3
"""What a roster can actually start in a matchup period, day by day.

Usage:
    python scripts/startable.py --season 2027 --team "Through The Wire"
    python scripts/startable.py --season 2027 --team "Through The Wire" --period 2

Read-only: it prints, it writes nothing. The roster is the listener's latest
status snapshots, so this is about now and forward -- for the tracked team the
morning digest already carries the events, and this is the number behind the
streaming advice that phase 2 will act on.

The window defaults to the season's first matchup period with games left, on
the stored schedule rather than on today's date, so the output is the same
whenever it is run. Pass --period to look at another one.

Domain logic lives in app/inseason/startable.py; this file is argument
parsing and layout. See docs/pickups.md section 4.3 for where the empty-day
check it reports came from.
"""

import argparse
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import League, LeagueSeason, ProTeamGame
from app.db.session import make_engine, make_session_factory
from app.inseason.startable import (
    RULED_OUT_STATUSES,
    WeekPlan,
    matchup_window,
    roster_week,
    team_by_name,
    team_names,
)

#: ESPN's league id for Full Court Press. The stored league is looked up by
#: id rather than assumed to be the only one, and the CLI names it so a stale
#: database is obvious rather than silently used.
LEAGUE_ID = 3853870


def _league_season(session: Session, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == LEAGUE_ID, LeagueSeason.season == season)
    )


def _first_period_left(session: Session, league_season: LeagueSeason, season: int) -> int | None:
    """The first matchup period with a game in it, from the stored schedule.

    Not the current date, and not a hardcoded 1: the same command should print
    the same table whether it is run in September or in November, and the
    window of the period holding the season's first game is looked up rather
    than assumed.
    """
    first_game = session.scalar(
        select(func.min(ProTeamGame.scoring_period)).where(ProTeamGame.season == season)
    )
    if first_game is None:
        return None
    for period in range(1, int(league_season.total_matchup_periods) + 1):
        window = matchup_window(session, league_season, period)
        if window is not None and window[0] <= int(first_game) <= window[1]:
            return period
    return None


def render(plan: WeekPlan, *, season: int, team_name: str, period: int) -> str:
    """The day-by-day table and the week's totals."""
    out: list[str] = []
    add = out.append
    width = 34
    add(f"{team_name} - season {season}, matchup period {period}")
    add(f"lineup: {' '.join(plan.lineup)}  ({len(plan.lineup)} slots)")
    add("")
    header = (
        f"{'day':>4}  {'date':<11}  {'players with a game':<{width}}"
        f"{'filled':>8}  {'empty':>5}  {'wasted':>6}"
    )
    add(header)
    add("-" * len(header))

    names = plan.names()
    for day in plan.days:
        date = day.game_date.isoformat() if day.game_date else "-"
        players = ", ".join(names.get(p.player_id, str(p.player_id)) for p in day.available)
        if not players:
            players = "(nobody)"
        if len(players) > width:
            players = players[: width - 4] + " ..."
        add(
            f"{day.scoring_period:>4}  {date:<11}  {players:<{width}}"
            f"{day.filled:>5}/{day.lineup_size:<2}  {len(day.empty):>5}  {day.wasted:>6}"
        )

    add("")
    add(
        f"totals: {plan.starts} starts of {plan.capacity} possible "
        f"({len(plan.days)} days x {len(plan.lineup)} slots)"
    )
    add(f"        {plan.empty_slots} slot-days no available player can fill")
    add(f"        {plan.wasted} games on the roster that cannot become starts")

    starts = plan.starts_by_player()
    if starts:
        add("")
        add("starts by player (days he is needed, of the days he has a game):")
        games: dict[int, int] = {}
        for day in plan.days:
            for player in day.available:
                games[player.player_id] = games.get(player.player_id, 0) + 1
        for player_id, count in sorted(starts.items(), key=lambda pair: (-pair[1], pair[0])):
            total = games.get(player_id, count)
            add(f"  {names.get(player_id, player_id):<24} {count:>2} of {total:>2}")

    full = [day for day in plan.days if day.full]
    short = [day for day in plan.days if not day.full]
    add("")
    add(f"days full: {len(full)}; days short: {len(short)}")
    if short:
        worst = max(short, key=lambda day: len(day.empty))
        when = worst.game_date.isoformat() if worst.game_date else f"period {worst.scoring_period}"
        add(f"shortest day: {when}, {len(worst.empty)} empty ({', '.join(worst.empty) or 'none'})")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="ESPN season, e.g. 2027")
    parser.add_argument("--team", required=True, help="Team name, or a unique prefix of one")
    parser.add_argument("--period", type=int, default=None, help="Matchup period (default: first)")
    args = parser.parse_args()

    engine = make_engine(get_settings().database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            league_season = _league_season(session, args.season)
            if league_season is None:
                print(
                    f"No stored season {args.season} for ESPN league {LEAGUE_ID}.",
                    file=sys.stderr,
                )
                return 1

            team = team_by_name(session, league_season, args.team)
            if team is None:
                print(f"No team matching {args.team!r} in {args.season}.", file=sys.stderr)
                for name in team_names(session, league_season):
                    print(f"  {name}", file=sys.stderr)
                return 1

            period = args.period or _first_period_left(session, league_season, args.season)
            if period is None:
                print(f"No NBA schedule stored for {args.season}.", file=sys.stderr)
                return 1
            window = matchup_window(session, league_season, period)
            if window is None:
                print(
                    f"Season {args.season} has no scoring window for period {period}.",
                    file=sys.stderr,
                )
                return 1
            first_day, last_day = window

            plan = roster_week(session, league_season, team.espn_team_id, first_day, last_day)

        print(render(plan, season=args.season, team_name=team.name, period=period))
        print("")
        print(
            "Counted: everyone except "
            f"{', '.join(sorted(RULED_OUT_STATUSES))} on his latest snapshot. "
            "Read-only; nothing was written."
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
