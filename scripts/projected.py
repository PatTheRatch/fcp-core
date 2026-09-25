#!/usr/bin/env python3
"""Where every team finishes: the projected standings, for one league season.

Usage:
    python scripts/projected.py --season 2027
    python scripts/projected.py --season 2026 --today 80
    python scripts/projected.py --season 2026 --today 80 --team "Through The Wire"
    python scripts/projected.py --season 2026 --today 80 --sims 2000

Read-only: it prints, it writes nothing. `--today` is a scoring period;
without it the calendar day is turned into one from the stored NBA schedule.
Nothing after that day is read (app/inseason/projected.py says how it is kept
that way), so the answer for a given day is the same whenever it is run.

`--team` adds that team's week-by-week list: each remaining matchup, who it
is against, the nine chances and the categories it is expected to take. It is
the same thing the Week page's "Rest of season" section shows.

Domain logic lives in app/inseason/projected.py; this file is argument
parsing and layout. See docs/projected_record.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this, `app` resolves to whichever checkout the interpreter's venv installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import League, LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.inseason.projected import N_SIMS, Projection, TeamOutlook, project_standings
from app.inseason.projected_calibration import CALIBRATION_NOTE, SHORT_NOTE
from app.pickups.state import season_calendar

#: ESPN's league id for Full Court Press, named rather than assumed, so a
#: stale database is obvious instead of silently used.
LEAGUE_ID = 3853870

#: Without these there is no roster, no schedule and no week to project.
NEEDED = ("matchups", "matchup_periods", "pro_team_games", "daily_lineup_slots")

#: The nine, in the order every strip on the site prints them.
CATS = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")


def _league_season(session: Session, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == LEAGUE_ID, LeagueSeason.season == season)
    )


def _missing_tables(engine: Engine) -> list[str]:
    inspector = inspect(engine)
    return [table for table in NEEDED if not inspector.has_table(table)]


def _wrap(text: str, width: int = 78, indent: str = "  ") -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = indent
    for word in words:
        if len(line) + len(word) + 1 > width and line.strip():
            lines.append(line.rstrip())
            line = indent
        line += word + " "
    if line.strip():
        lines.append(line.rstrip())
    return lines


def _record(pair: tuple[float, float]) -> str:
    return f"{pair[0]:.1f}-{pair[1]:.1f}"


def _table(report: Projection) -> None:
    print(f"PROJECTED STANDINGS  {report.season}, day {report.as_of}", end="")
    if report.as_of_date is not None:
        print(f" ({report.as_of_date:%a %d %b %Y})", end="")
    print(f", matchup period {report.matchup_period}")
    print(f"  {len(report.periods)} weeks left; ordered by {report.tiebreak}")
    print()
    bye = "   BYE" if report.bye_count else ""
    # The categories lead, because a category league is ranked on them; the
    # matchup record is the last column, a figure beside the order.
    print(
        f"{'':3}{'team':<30}{'cats now':>12}{'proj. cats':>14}{'playoffs':>10}{bye}"
        f"{'matchups':>10}{'proj.':>11}"
    )
    for place, team in enumerate(report.teams, start=1):
        won, lost, tied = team.banked_matchups
        now = f"{won}-{lost}" + (f"-{tied}" if tied else "")
        projected = f"{team.projected_matchups[0]:.1f}-{team.projected_matchups[1]:.1f}"
        odds = f"{team.playoff_odds * 100:.0f}%"
        bye_odds = f"{team.bye_odds * 100:5.0f}%" if team.bye_odds is not None else ""
        print(
            f"{place:<3}{team.name[:30]:<30}{_record(team.banked):>12}"
            f"{_record(team.projected_record):>14}{odds:>10}{bye_odds:>7}"
            f"{now:>10}{projected:>11}"
        )
    print()
    if report.playoff_note:
        for line in _wrap(report.playoff_note):
            print(line)
    print(f"  {report.playoff_team_count} of {len(report.teams)} teams make the playoffs.")
    for line in _wrap(SHORT_NOTE):
        print(line)


def _weeks(report: Projection, team: TeamOutlook) -> None:
    print()
    print(f"REST OF SEASON  {team.name}")
    print(
        f"  banked {_record(team.banked)} in categories, "
        f"expected {_record(team.expected)} over {len(team.weeks)} weeks, "
        f"projected {_record(team.projected_record)}"
    )
    print()
    header = "".join(f"{cat:>6}" for cat in CATS)
    print(f"{'wk':>3}  {'opponent':<28}{'exp':>6}{header}")
    for week in team.weeks:
        if week.on_bye:
            print(f"{week.period:>3}  {'(bye)':<28}{'-':>6}")
            continue
        cells = "".join(f"{week.probabilities.get(cat, 0.0) * 100:5.0f}%" for cat in CATS)
        played = " *" if week.in_play else ""
        name = f"{week.opponent_name}{played}"
        print(f"{week.period:>3}  {name[:28]:<28}{week.expected_wins:6.2f}{cells}")
    print()
    print("  * the week being played: its totals include what is already posted.")
    print()
    print("WHERE IT FINISHES")
    for place, share in enumerate(team.finishes, start=1):
        if share < 0.005:
            continue
        bar = "#" * max(1, round(share * 40))
        print(f"  {place:>2}  {share * 100:5.1f}%  {bar}")
    print(f"  playoffs {team.playoff_odds * 100:.1f}%", end="")
    if team.bye_odds is not None:
        print(f", a first-round bye {team.bye_odds * 100:.1f}%", end="")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--today", type=int, default=None, help="Scoring period")
    parser.add_argument("--team", type=str, default=None, help="Team name, for its own weeks")
    parser.add_argument("--sims", type=int, default=N_SIMS)
    parser.add_argument("--note", action="store_true", help="Print the calibration in full")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    missing = _missing_tables(engine)
    if missing:
        raise SystemExit(f"nothing to project: no {', '.join(missing)} table")
    with make_session_factory(engine)() as session:
        league_season = _league_season(session, args.season)
        if league_season is None:
            raise SystemExit(f"no stored season {args.season} for league {LEAGUE_ID}")
        calendar = season_calendar(session, args.season)
        if args.today is None and calendar is None:
            raise SystemExit("no NBA schedule is stored, so there is no day to project from")
        day = args.today if args.today is not None else calendar.scoring_period_on(date.today())
        try:
            report = project_standings(session, league_season, day, n_sims=args.sims)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        _table(report)
        if args.team is not None:
            wanted = args.team.strip().casefold()
            found = next(
                (one for one in report.teams if one.name.strip().casefold() == wanted), None
            )
            if found is None:
                names = ", ".join(sorted(one.name for one in report.teams))
                raise SystemExit(f"no team called {args.team!r}. Teams: {names}")
            _weeks(report, found)
        print()
        print(f"  {report.source_note}")
        print(f"  {report.basis}")
        print(f"  {report.n_sims} simulated seasons, seed {report.seed}")
        if args.note:
            print()
            for line in _wrap(CALIBRATION_NOTE):
                print(line)


if __name__ == "__main__":
    main()
