#!/usr/bin/env python3
"""Who to hold for the rest of the year, who should go, and what to bid.

Usage:
    python scripts/season.py --season 2027 --team "Through The Wire"
    python scripts/season.py --season 2027 --team "Through The Wire" --today 12
    python scripts/season.py --season 2027 --team "Through The Wire" --no-tilt

Read-only: it prints, it writes nothing. The companion to `scripts/stream.py`,
which answers the same day's short question; this one answers the long one,
from today to the end of the regular season. `--today` is a scoring period;
without it the calendar day is turned into one from the stored schedule.

Domain logic lives in app/pickups/season.py; this file is argument parsing
and layout. See docs/pickups.md section 4.4.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this, `app` resolves to whichever checkout the interpreter's venv installed,
# which is the production one when a worktree borrows /opt/fcp-core/.venv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.inseason.startable import team_by_name, team_names
from app.pickups.bids import Bid
from app.pickups.season import SeasonReport, Swap, season_recommendations
from app.pickups.state import season_calendar

# The league id, the stored-season lookup and the schema guard are the
# streaming CLI's, not copied: the two scripts must refuse the same database
# for the same reason and name the same league.
from scripts.stream import LEAGUE_ID, _league_season, _missing_tables


def describe_bid(bid: Bid | None) -> str:
    """The bid, the bucket it came from, and what capped it."""
    if bid is None:
        return ""
    if not bid.sample:
        return f"  bid: nothing to go on ({bid.note})"
    capped = f", capped by {bid.capped_by}" if bid.capped_by else ""
    return (
        f"  bid ${bid.amount}: the {bid.basis} of rank {bid.bucket} "
        f"(${bid.low}-${bid.high} over {bid.sample} claims{capped})"
    )


def describe(move: Swap) -> str:
    into = ", ".join(player.name for player in move.into)
    if not move.out:
        return f"add {into} into the open place"
    out = ", ".join(player.name for player in move.out)
    return f"drop {out}, add {into}"


def render(
    report: SeasonReport,
    *,
    season: int,
    team_name: str,
    when: date | None,
) -> str:
    out: list[str] = []
    add = out.append
    dated = f", {when.isoformat()}" if when is not None else ""
    add(
        f"{team_name} - season {season}, rest of season from day {report.today}{dated}; "
        f"{report.weeks_remaining:.1f} of {report.total_weeks:.1f} weeks left, "
        f"through day {report.last_scoring_period}"
    )
    add(f"expected categories won in an ordinary week: {report.expected_wins:.2f} of 9")
    add("  " + "  ".join(f"{key} {p:.2f}" for key, p in report.probabilities.items()))
    add(
        f"roster: {report.open_slots} open place(s), IR slot "
        f"{'free' if report.ir_slot_free else 'used or none'}, FAAB ${report.faab_remaining}, "
        f"{report.pool_size} free agents evaluated"
    )
    add(f"adds in the last {report.churn.days} days: {report.churn.adds}")
    add(f"  {report.churn.finding}")

    add("")
    add("moves, by change in expected categories won per week:")
    if not report.moves:
        add("  none legal")
    for move in report.moves:
        mark = "clears" if move.clears(report.hurdle_paid, report.hurdle_free) else "below"
        add(
            f"  {move.kind:<9} {move.delta:+.3f}  {describe(move)}  "
            f"({mark} its hurdle of {move.hurdle(report.hurdle_paid, report.hurdle_free):.2f})"
        )
        moved = ", ".join(f"{s.abbreviation} {s.delta:+.2f}" for s in move.moved()[:4])
        if moved:
            add(f"     {moved}")
        priced = describe_bid(move.bid)
        if priced:
            add(f"   {priced}")

    add("")
    add("drop candidates, the men who cost least to lose:")
    if not report.drops:
        add("  none")
    for rank, drop in enumerate(report.drops, start=1):
        replacement = drop.replacement.name if drop.replacement is not None else "nobody"
        add(f"  {rank}. {drop.player.name} ({drop.delta:+.3f}, replaced by {replacement})")

    add("")
    add("stash candidates (out now, back soon, worth a place when they are):")
    if not report.stashes:
        add("  none")
    for stash in report.stashes:
        cost = "needs a drop" if stash.needs_drop else "free, the IR slot is open"
        add(
            f"  {stash.player.name}, back {stash.expected_return_date.isoformat()} "
            f"({stash.weeks_away:.1f} weeks), healthy rank {stash.healthy_rank}, {cost}"
        )

    add("")
    chosen = report.recommended
    if chosen is None:
        add(
            f"no move clears the hurdle ({report.hurdle_paid:.2f} categories a week for a "
            f"claim, {report.hurdle_free:.2f} for a free add)."
        )
    else:
        add(f"recommended: {describe(chosen)} ({chosen.delta:+.3f} a week)")
        priced = describe_bid(chosen.bid)
        if priced:
            add(priced.strip())
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

            try:
                report = season_recommendations(
                    session, league_season, team.espn_team_id, today, tilt=not args.no_tilt
                )
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 1
            text = render(
                report,
                season=args.season,
                team_name=team.name,
                when=calendar.date_of(report.today),
            )
        print(text)
        print("")
        print("Read-only; nothing was written.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
