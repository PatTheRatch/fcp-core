#!/usr/bin/env python3
"""Who starts today, for one team, and what the lineup that is set is missing.

Usage:
    python scripts/today.py --season 2027 --team "Through The Wire"
    python scripts/today.py --season 2027 --team "Through The Wire" --today 52
    python scripts/today.py --season 2027 --team "Through The Wire" --no-tilt

Read-only: it prints, it writes nothing. The day is read from the stored
rows (the lineup days, the listener's snapshots, the NBA schedule), so the
answer is the same whenever it is run for the same day, and nothing after
that day is read at all. `--today` is a scoring period; without it the
calendar day is turned into one from the stored schedule.

Domain logic lives in app/pickups/today.py; this file is argument parsing
and layout, and it prints exactly what the week page's Today section shows.
See docs/pickups.md section 4.3.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this, `app` resolves to whichever checkout the interpreter's venv installed,
# which is the production one when a worktree borrows /opt/fcp-core/.venv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import League, LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.inseason.startable import team_by_name, team_names
from app.pickups.state import season_calendar
from app.pickups.today import NO_SLOT, DayPlayer, Seat, TodayReport, today_lineup

#: ESPN's league id for Full Court Press, named rather than assumed, so a
#: stale database is obvious instead of silently used.
LEAGUE_ID = 3853870

#: Without these there is no roster, no schedule and no day to report on.
LISTENER_TABLES = ("player_status_snapshots", "pro_team_games", "daily_lineup_slots")

#: Men named on one line before the rest are counted.
NAMED = 4


def _league_season(session: Session, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == LEAGUE_ID, LeagueSeason.season == season)
    )


def _missing_tables(engine: Engine) -> list[str]:
    inspector = inspect(engine)
    return [table for table in LISTENER_TABLES if not inspector.has_table(table)]


def _opponent(player: DayPlayer) -> str:
    """Who his team plays tonight, in the three words a column has room for."""
    return "no game" if player.game is None else player.game.describe()


def _flag(player: DayPlayer) -> str:
    """His status, when it is worth saying, and the day he is back."""
    status = (player.player.injury_status or "").upper()
    if not status or status == "ACTIVE":
        return ""
    back = player.player.expected_return_date
    return f"{status}{f', back {back:%d %b}' if back else ''}"


def _row(slot: str, player: DayPlayer | None) -> str:
    if player is None:
        return f"  {slot:<4} -- nobody on the roster can fill it"
    return f"  {slot:<4} {player.name:<28} {_opponent(player):<10} {_flag(player)}".rstrip()


def _named(players: tuple[DayPlayer, ...]) -> str:
    names = ", ".join(player.name for player in players[:NAMED])
    extra = len(players) - NAMED
    return f"{names} and {extra} more" if extra > 0 else names


def _seat_of(place: Seat) -> str:
    return place.slot if place.player is None else f"{place.slot} ({place.player.name})"


def render(report: TodayReport, *, season: int, team_name: str) -> str:
    out: list[str] = []
    add = out.append
    dated = f", {report.calendar_date.isoformat()}" if report.calendar_date is not None else ""
    add(
        f"{team_name} - season {season}, day {report.today}{dated}, "
        f"matchup period {report.matchup_period}"
    )
    if report.teams_playing == 0:
        # The All-Star break, and the odd single dark night. Ten rows of
        # "nobody" would be ten rows of noise: there is no lineup to set.
        add("no NBA team plays today, so there is no lineup to set and nothing to fix")
        add("")
        add(f"the whole roster sits: {_named(report.idle) if report.idle else 'nobody held'}")
        if report.injured_reserve:
            add(f"injured reserve: {_named(report.injured_reserve)}")
        return "\n".join(out)

    add(f"{report.teams_playing} NBA teams play today; this roster can fill {report.starts}")

    add("")
    add("the lineup, as we would set it:")
    for place in report.lineup:
        add(_row(place.slot, place.player))

    add("")
    if not report.actual_known:
        add("what is set: not stored for this day yet, so there is nothing to compare with.")
    else:
        add(
            f"what is set: {report.actual_starts} of {len(report.lineup)} places with a man playing"
        )
        for place in report.actual:
            add(_row(place.slot, place.player))
        if report.fix:
            add("")
            add("WORTH FIXING - these places will produce nothing tonight:")
            for misstart in report.fix:
                add(f"  {_seat_of(misstart.seat)}: {_named(misstart.instead)} could take it")
        elif report.starts > report.actual_starts:
            add("  (the lineup is short, and nobody on the bench fits the places going begging)")
        else:
            add("  nothing to fix: it fills as much of the lineup as anything could")

    add("")
    add("on the bench with a game:")
    if not report.benched:
        add("  nobody")
    for benched in report.benched:
        if benched.reason == NO_SLOT:
            why = "no starting place he is eligible for"
        else:
            why = f"behind {_named(benched.behind)} in every place he fits"
        add(f"  {benched.player.name:<28} {_opponent(benched.player):<10} {why}")

    add("")
    add(f"no game today: {_named(report.idle) if report.idle else 'nobody'}")
    if report.injured_reserve:
        add(f"injured reserve: {_named(report.injured_reserve)}")
    if report.actual_known and report.edge:
        add(f"the proposal is worth {report.edge:+.3f} against what is set, on the seating's scale")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="ESPN season, e.g. 2027")
    parser.add_argument("--team", required=True, help="Team name, or a unique prefix of one")
    parser.add_argument("--today", type=int, default=None, help="Scoring period (default: today)")
    parser.add_argument("--no-tilt", action="store_true", help="Ignore minutes spikes and drops")
    args = parser.parse_args()

    engine = make_engine(get_settings().database_url)
    try:
        missing = _missing_tables(engine)
        if missing:
            print(
                "The database is behind the code: no "
                + ", ".join(missing)
                + ". Run 'alembic upgrade head' where the listener runs; nothing was written.",
                file=sys.stderr,
            )
            return 1
        factory = make_session_factory(engine)
        with factory() as session:
            league_season = _league_season(session, args.season)
            if league_season is None:
                print(
                    f"No stored season {args.season} for ESPN league {LEAGUE_ID}.", file=sys.stderr
                )
                return 1
            team = team_by_name(session, league_season, args.team)
            if team is None:
                print(f"No team matching {args.team!r} in {args.season}.", file=sys.stderr)
                for name in team_names(session, league_season):
                    print(f"  {name}", file=sys.stderr)
                return 1

            calendar = season_calendar(session, args.season)
            if calendar is None:
                print(f"No NBA schedule stored for {args.season}.", file=sys.stderr)
                return 1
            today = (
                args.today if args.today is not None else calendar.scoring_period_on(date.today())
            )

            try:
                report = today_lineup(
                    session, league_season, team.espn_team_id, today, tilt=not args.no_tilt
                )
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 1
            text = render(report, season=args.season, team_name=team.name)
        print(text)
        print("")
        print("Ideas, not instructions; the manager sets the lineup.")
        print("Read-only; nothing was written.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
