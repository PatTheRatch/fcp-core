#!/usr/bin/env python3
"""Who to stream this week, for one team, and whether anyone is worth it.

Usage:
    python scripts/stream.py --season 2027 --team "Through The Wire"
    python scripts/stream.py --season 2027 --team "Through The Wire" --today 12
    python scripts/stream.py --season 2027 --team "Through The Wire" --no-tilt

Read-only: it prints, it writes nothing. The week is read from the stored
rows (lineup days, the listener's snapshots and pool, the NBA schedule, the
live matchup's totals), so the answer is the same whenever it is run for the
same day. `--today` is a scoring period; without it the calendar day is
turned into one from the stored schedule, which before opening night is the
season's first day.

Domain logic lives in app/pickups/stream.py; this file is argument parsing
and layout. See docs/pickups.md section 4.3.
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

from app import calibration
from app.config import get_settings
from app.db.models import League, LeagueSeason, Team
from app.db.session import make_engine, make_session_factory
from app.inseason.startable import team_by_name, team_names
from app.pickups.judge import Judgement
from app.pickups.state import season_calendar
from app.pickups.stream import ADD, IR_MOVE, Move, StreamReport, stream_recommendations

#: ESPN's league id for Full Court Press. The stored league is looked up by
#: id rather than assumed to be the only one, and the CLI names it so a stale
#: database is obvious rather than silently used.
LEAGUE_ID = 3853870

#: The listener's tables, without which there is no roster, pool or
#: schedule to read. Checked up front so a database behind the code says
#: so instead of failing on the first query.
LISTENER_TABLES = ("player_status_snapshots", "free_agent_snapshots", "pro_team_games")


def _league_season(session: Session, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == LEAGUE_ID, LeagueSeason.season == season)
    )


def _missing_tables(engine: Engine) -> list[str]:
    inspector = inspect(engine)
    return [table for table in LISTENER_TABLES if not inspector.has_table(table)]


def _names(session: Session, league_season: LeagueSeason) -> dict[int, str]:
    return {
        int(team.espn_team_id): team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }


#: Adds left in the period at or below which the plan says so. Not a second
#: bar: the move is the move, but a manager with one add left should know he
#: is spending his last one.
SCARCE_ADDS = 2


def _adds(used: int, budget: int) -> str:
    return f"adds this period: used {used} of {budget}"


def _scarcity(left: int) -> str:
    if left > SCARCE_ADDS:
        return ""
    return f" ({left} add{'' if left == 1 else 's'} left this period)"


def _describe(move: Move, today: int) -> str:
    add = f"add {move.add.name} ({move.add_starts} of {move.add.games_remaining_this_period} games)"
    if not move.add.seatable_on(today) and move.add.waiver_clears_at is not None:
        # A claim on him is a FAAB bid that resolves when he clears, and he
        # plays for us from that day, not from today.
        add += f", on waivers, clears {move.add.waiver_clears_at:%A}"
    if move.kind == ADD:
        rest = "into the open place"
    elif move.kind == IR_MOVE and move.to_ir is not None:
        rest = f"{move.to_ir.name} to IR"
    elif move.drop is not None:
        rest = (
            f"drop {move.drop.name} ({move.drop_starts} of {move.drop.games_remaining_this_period})"
        )
    else:
        rest = ""
    return f"{add}, {rest}".rstrip(", ")


def _record(record: tuple[float, float]) -> str:
    return f"{record[0]:.1f}-{record[1]:.1f}"


def _faab(remaining: int, overspent: int) -> str:
    """The pot, and a word when our sum of the bid feed ran past the budget.

    Never a negative: `app.pickups.state` explains why the feed and ESPN's
    own ledger disagree by a few dollars on some teams.
    """
    if overspent:
        return f"FAAB $0 (the bid feed reconstructs ${overspent} past the budget; read it as spent)"
    return f"FAAB ${remaining}"


def _wire(pool_size: int, historical: bool) -> str:
    """How many free agents were looked at, and which wire they came from."""
    if historical:
        return f"{pool_size} free agents evaluated, historical wire (no snapshots)"
    return f"{pool_size} free agents evaluated"


def _judged(judgement: Judgement) -> list[str]:
    """The two horizons, the net, and the season record either way."""
    season = judgement.delta_season_per_week
    out = [
        f"week {judgement.delta_week:+.3f}  +  season {season:+.3f}/wk x "
        f"{judgement.weeks_remaining:.1f} wks  =  net {judgement.delta_total:+.3f} categories",
        f"projected record {_record(judgement.record_without)} without, "
        f"{_record(judgement.record_with)} with",
    ]
    if not judgement.measured:
        out.append("(no league standard measurable yet, so nothing is charged for the season)")
    return out


def render(
    report: StreamReport,
    *,
    season: int,
    team_name: str,
    opponent_name: str | None,
    when: date | None,
    hurdle_note: str = "",
) -> str:
    """The week's report as a page of text.

    `hurdle_note` says where the bar came from, and is printed under it the
    way a projection's `source_note` is printed: a bar labels, and a bar
    whose provenance is hidden is a bar asking to be trusted
    (`app.calibration`, docs/intake.md).
    """
    out: list[str] = []
    add = out.append
    first, last = report.scoring_periods_remaining[0], report.scoring_periods_remaining[-1]
    dated = f", {when.isoformat()}" if when is not None else ""
    add(
        f"{team_name} - season {season}, matchup period {report.matchup_period}, "
        f"day {first}{dated}; days {first}-{last} left ({report.days_remaining})"
    )
    if report.on_bye:
        add("opponent: none (bye), so no head-to-head this period")
    else:
        add(f"opponent: {opponent_name or report.opponent_team_id}")
        add(f"expected categories won as things stand: {report.expected_wins:.2f} of 9")
        add("  " + "  ".join(f"{key} {p:.2f}" for key, p in report.probabilities.items()))
    add(
        f"roster: {report.open_slots} open place(s), IR slot "
        f"{'free' if report.ir_slot_free else 'used or none'}, "
        f"{_faab(report.faab_remaining, report.faab_overspent)}, "
        f"{_adds(report.adds_used, report.adds_budget)}, "
        f"{_wire(report.pool_size, report.historical_wire)}"
    )
    add(f"bar {report.hurdle:.2f} a week" + (f" - {hurdle_note}" if hurdle_note else ""))
    outlook = report.outlook
    add(
        f"season so far: {_record(outlook.banked)} in categories; projected to end "
        f"{_record(outlook.record_without)} with no move, "
        f"{outlook.weeks_remaining:.1f} weeks left after this one"
    )

    add("")
    add("empty days (a slot nobody on the roster can fill, that a free agent could):")
    if not report.empty_days:
        add("  none")
    for day in report.empty_days:
        fillers = ", ".join(p.name for p in day.fillers[:4])
        if len(day.fillers) > 4:
            fillers += f" and {len(day.fillers) - 4} more"
        add(f"  day {day.scoring_period}: {', '.join(day.empty_slots)} empty; {fillers}")

    add("")
    if report.on_bye:
        add("moves: none ranked on a bye")
    else:
        add("moves, by net categories over both horizons:")
        if not report.moves:
            add("  none legal")
        for rank, move in enumerate(report.moves, start=1):
            moved = ", ".join(f"{s.abbreviation} {s.delta:+.2f}" for s in move.moved()[:4])
            flag = "  fills an empty day" if move.fills_empty_day else ""
            add(f"  {rank}. {move.net:+.3f}  {_describe(move, report.today)}{flag}")
            for line in _judged(move.judgement):
                add(f"       {line}")
            if moved:
                add(f"       this week: {moved}")

    add("")
    plan = report.recommended
    if report.adds_left == 0:
        add(
            f"no adds left this period ({_adds(report.adds_used, report.adds_budget)}), "
            "so there is nothing to plan today; the moves above are what the wire offers."
        )
        add(f"projected record either way: {_record(outlook.record_without)}")
    elif not plan:
        add(f"nothing clears the bar ({report.hurdle:.2f} categories, or an empty day filled).")
        add(f"projected record either way: {_record(outlook.record_without)}")
    elif len(plan) == 1:
        chosen = plan[0]
        add(
            f"worth a look: {_describe(chosen, report.today)} "
            f"({chosen.net:+.3f} categories net){_scarcity(report.adds_left)}"
        )
        for line in _judged(chosen.judgement):
            add(f"  {line}")
    else:
        # Each move was found against the roster the one before it leaves, so
        # they are independent and the order is the order to make them in.
        add(f"worth a look, in this order{_scarcity(report.adds_left)}:")
        for rank, chosen in enumerate(plan, start=1):
            add(f"  {rank}. {_describe(chosen, report.today)} ({chosen.net:+.3f} categories net)")
            for line in _judged(chosen.judgement):
                add(f"       {line}")
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

            calendar = season_calendar(session, args.season)
            if calendar is None:
                print(f"No NBA schedule stored for {args.season}.", file=sys.stderr)
                return 1
            today = (
                args.today if args.today is not None else calendar.scoring_period_on(date.today())
            )

            bars = calibration.bars(session, int(league_season.league_id))
            try:
                report = stream_recommendations(
                    session,
                    league_season,
                    team.espn_team_id,
                    today,
                    tilt=not args.no_tilt,
                    hurdle=bars.stream_hurdle.number,
                    floor=bars.typical_pickup.number,
                    opened=bars.opened_place.number,
                )
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 1
            names = _names(session, league_season)
            text = render(
                report,
                season=args.season,
                team_name=team.name,
                opponent_name=names.get(report.opponent_team_id or -1),
                when=calendar.date_of(today),
                hurdle_note=bars.stream_hurdle.note,
            )
        print(text)
        print("")
        print("Read-only; nothing was written.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
