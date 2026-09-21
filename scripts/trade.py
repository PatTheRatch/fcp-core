#!/usr/bin/env python3
"""What a trade would be worth, to both sides, before it is made.

Usage:
    python scripts/trade.py --season 2026 --team "Through The Wire" \
        --give "Neemias Queta" --with "The Infirmary" --get "Myles Turner"
    python scripts/trade.py --season 2026 --team "Through The Wire" \
        --give "A" --give "B" --with "Foxes" --get "C" --drop "D" --today 53

Read-only: it prints, it writes nothing. The companion to `scripts/stream.py`
and `scripts/season.py`, in the same currency and the same layout: a trade and
a pickup are two answers to one question, so they are printed the same way.
`--today` is a scoring period; without it the calendar day is turned into one
from the stored schedule.

`--give` names players leaving `--team`; `--get` names players leaving
`--with`; each may be repeated. `--drop` names a man `--team` drops to make
room and `--their-drop` one the other side drops; without them a side that
needs room drops the cheapest place on its roster, and the report says so.

Domain logic lives in app/trades/; this file is argument parsing and layout.
See docs/trades.md.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this, `app` resolves to whichever checkout the interpreter's venv installed,
# which is the production one when a worktree borrows /opt/fcp-core/.venv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason, Player, Team
from app.db.session import make_engine, make_session_factory
from app.inseason.startable import team_by_name, team_names
from app.pickups.state import load_team_week, season_calendar
from app.trades import (
    TRADE_REVIEW_DAYS,
    CategoryView,
    PlayerCard,
    PlayoffLens,
    SideReport,
    TeamOffer,
    TradeReport,
    evaluate_trade,
)

# The league id, the stored-season lookup and the schema guard are the
# streaming CLI's, not copied: the three scripts must refuse the same database
# for the same reason and name the same league.
from scripts.stream import (
    LEAGUE_ID,
    _judged,
    _league_season,
    _missing_tables,
    _wire,
)


class AmbiguousNameError(ValueError):
    """A name that matched more than one player, with the names it matched."""

    def __init__(self, name: str, matches: Sequence[str]) -> None:
        super().__init__(name)
        self.name = name
        self.matches = tuple(matches)


def roster_names(
    session: Session, league_season: LeagueSeason, team: Team, today: int
) -> dict[int, str]:
    """Player id -> name for everyone the team held on `today`.

    The roster is the trade's own scope: a name is looked up among the men who
    could actually be in the deal, so "Turner" resolves on a roster that has
    one and refuses on a roster that has two.
    """
    week = load_team_week(session, league_season, team.espn_team_id, today)
    return {player.player_id: player.name for player in week.roster}


def player_by_name(roster: dict[int, str], name: str) -> int:
    """A player id from a name, the way the other scripts match a team.

    Exact first, then a unique case-insensitive substring. Raises `AmbiguousNameError`
    with what it matched, so the caller can print the alternatives rather than
    silently picking one, and `KeyError` when nothing matched at all.
    """
    for player_id, full in roster.items():
        if full == name:
            return player_id
    lowered = name.strip().lower()
    matches = [(player_id, full) for player_id, full in roster.items() if lowered in full.lower()]
    if len(matches) == 1:
        return matches[0][0]
    if matches:
        raise AmbiguousNameError(name, sorted(full for _id, full in matches))
    raise KeyError(name)


def anywhere(session: Session, name: str) -> list[str]:
    """Players in the database whose name contains `name`, for an error line."""
    return sorted(
        str(found)
        for found in session.scalars(
            select(Player.name).where(Player.name.ilike(f"%{name.strip()}%")).limit(10)
        ).all()
    )


def _card(card: PlayerCard) -> str:
    """One player, with what he is worth and what the projection rests on."""
    bits = [
        f"{card.value:+.3f}/wk",
        f"{card.games_left} games left",
        f"{card.playoff_games} in the playoff weeks",
    ]
    if card.hurt:
        status = card.injury_status or ""
        back = (
            f", back {card.expected_return_date.isoformat()}"
            if card.expected_return_date is not None
            else ""
        )
        bits.append(f"{status}{back}")
    return f"{card.name} ({', '.join(bits)})"


def _rests_on(card: PlayerCard) -> str:
    thin = ", THIN" if card.thin else ""
    prior = "a preseason projection" if card.had_projection else "no projection"
    return (
        f"{card.name}: {card.games_so_far} games of his own and {prior} "
        f"({card.projection_source}){thin}"
    )


def _category_line(view: CategoryView) -> str:
    scale = 3 if view.abbreviation in ("FG%", "FT%") else 1
    return (
        f"  {view.abbreviation:<4} {view.before:>8.{scale}f} -> {view.after:>8.{scale}f} "
        f"({view.delta:+.{scale}f})   win {view.p_before:.2f} -> {view.p_after:.2f} "
        f"({view.p_delta:+.2f})"
    )


def _playoffs(lens: PlayoffLens) -> list[str]:
    if not lens.measurable:
        return [f"playoffs: not counted -- {lens.note}"]
    return [
        f"playoffs (days {lens.first_scoring_period}-{lens.last_scoring_period}, "
        f"{lens.weeks:.1f} weeks, {lens.games} games for the men in the deal): "
        f"{lens.delta_per_week:+.3f} a week, {lens.delta_total:+.3f} over the round",
        *[_category_line(view) for view in lens.categories if view.moved],
    ]


def render_side(side: SideReport, *, estimate: bool) -> list[str]:
    out: list[str] = []
    add = out.append
    whose = " (our estimate of their side, not what they think)" if estimate else ""
    add(f"=== {side.team_name}{whose} ===")
    add("  gets:  " + (", ".join(_card(card) for card in side.receives) or "nobody"))
    add("  gives: " + (", ".join(_card(card) for card in side.gives) or "nobody"))
    if side.drops:
        how = "named" if side.drop_source == "named" else "cheapest place on the roster"
        add(f"  drops: {', '.join(_card(card) for card in side.drops)}  [{how}]")
    if side.places_opened:
        add(
            f"  opens {side.places_opened} roster place(s), worth "
            f"{side.replacement:.3f} a week on the wire"
            + (
                f" (the best free agent is {side.replacement_player.name})"
                if side.replacement_player is not None
                else " (nobody on the wire beats the floor)"
            )
        )
    if side.places_used:
        add(f"  fills {side.places_used} open roster place(s)")
    add("")
    for line in _judged(side.judgement):
        add(f"  {line}")
    mark = "clears" if side.clears else "below"
    add(
        f"  {mark} its bar of {side.hurdle:.2f} categories a week; "
        f"this deal is {side.per_week:+.3f} a week"
    )
    add("")
    add("  the nine, in an ordinary week (before -> after, and the chance of winning it):")
    for view in side.categories:
        add(f"  {_category_line(view)}")
    add("")
    for line in _playoffs(side.playoffs):
        add(f"  {line}")
    add("")
    add("  what it rests on:")
    for card in (*side.receives, *side.gives, *side.drops):
        add(f"    {_rests_on(card)}")
    if side.notes:
        add("  notes:")
        for note in side.notes:
            add(f"    - {note}")
    add("")
    add(f"  {side.summary}")
    return out


def render(
    report: TradeReport,
    *,
    ours: int,
    when: date | None,
) -> str:
    out: list[str] = []
    add = out.append
    names = " <-> ".join(side.team_name for side in report.sides)
    dated = f", {when.isoformat()}" if when is not None else ""
    add(f"{names} - season {report.season}, judged on day {report.today}{dated}")
    add(
        f"in a lineup from day {report.effective_day} "
        f"({report.review_days} day(s) of review; {report.review_source})"
    )
    add(
        f"rest of season: {report.weeks_remaining:.1f} weeks left, through day "
        f"{report.last_scoring_period}; {_wire(report.pool_size, report.historical_wire)}"
    )
    if report.notes:
        for note in report.notes:
            add(f"  - {note}")
    for side in report.sides:
        add("")
        out.extend(render_side(side, estimate=side.team_id != ours))
    add("")
    add(
        "Both numbers are ours: the other side is judged on our projections and the same "
        "league standard, which is an estimate of his roster's needs and not his opinion."
    )
    return "\n".join(out)


def _resolve(
    session: Session,
    league_season: LeagueSeason,
    team: Team,
    today: int,
    names: Sequence[str],
    label: str,
) -> tuple[int, ...] | None:
    """Player ids for `names` on this team's roster, or None after complaining."""
    roster = roster_names(session, league_season, team, today)
    out: list[int] = []
    for name in names:
        try:
            out.append(player_by_name(roster, name))
        except AmbiguousNameError as clash:
            print(
                f"{label} {clash.name!r} matches more than one man on {team.name}:",
                file=sys.stderr,
            )
            for found in clash.matches:
                print(f"  {found}", file=sys.stderr)
            return None
        except KeyError:
            print(f"{label} {name!r} is not on {team.name} on day {today}.", file=sys.stderr)
            elsewhere = anywhere(session, name)
            if elsewhere:
                print("  players of that name in the database:", file=sys.stderr)
                for found in elsewhere:
                    print(f"    {found}", file=sys.stderr)
            print("  on this roster:", file=sys.stderr)
            for found in sorted(roster.values()):
                print(f"    {found}", file=sys.stderr)
            return None
    return tuple(out)


def _team(session: Session, league_season: LeagueSeason, name: str) -> Team | None:
    team = team_by_name(session, league_season, name)
    if team is None:
        print(f"No team matching {name!r} in {league_season.season}.", file=sys.stderr)
        for found in team_names(session, league_season):
            print(f"  {found}", file=sys.stderr)
    return team


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="ESPN season, e.g. 2027")
    parser.add_argument("--team", required=True, help="Our team name, or a unique prefix")
    parser.add_argument("--give", action="append", default=[], help="A player we give (repeat)")
    parser.add_argument("--with", dest="other", required=True, help="The other team")
    parser.add_argument("--get", action="append", default=[], help="A player we get (repeat)")
    parser.add_argument("--drop", action="append", default=[], help="Whom we drop for room")
    parser.add_argument("--their-drop", action="append", default=[], help="Whom they drop for room")
    parser.add_argument("--today", type=int, default=None, help="Scoring period (default: today)")
    parser.add_argument(
        "--review-days",
        type=int,
        default=None,
        help="Days before the deal can be in a lineup (default: the league's own, 1)",
    )
    parser.add_argument("--no-tilt", action="store_true", help="Ignore minutes spikes and drops")
    args = parser.parse_args()

    if not args.give and not args.get:
        print("Nothing is being traded: name at least one --give or --get.", file=sys.stderr)
        return 1

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
            ours = _team(session, league_season, args.team)
            theirs = _team(session, league_season, args.other)
            if ours is None or theirs is None:
                return 1
            if ours.id == theirs.id:
                print("A team cannot trade with itself.", file=sys.stderr)
                return 1

            calendar = season_calendar(session, args.season)
            if calendar is None:
                print(f"No NBA schedule stored for {args.season}.", file=sys.stderr)
                return 1
            today = (
                args.today if args.today is not None else calendar.scoring_period_on(date.today())
            )

            give = _resolve(session, league_season, ours, today, args.give, "--give")
            get = _resolve(session, league_season, theirs, today, args.get, "--get")
            our_drops = _resolve(session, league_season, ours, today, args.drop, "--drop")
            their_drops = _resolve(
                session, league_season, theirs, today, args.their_drop, "--their-drop"
            )
            if give is None or get is None or our_drops is None or their_drops is None:
                return 1

            drops = {ours.espn_team_id: our_drops, theirs.espn_team_id: their_drops}
            try:
                report = evaluate_trade(
                    session,
                    league_season,
                    today,
                    TeamOffer(team_id=ours.espn_team_id, gives=give),
                    TeamOffer(team_id=theirs.espn_team_id, gives=get),
                    drops={k: v for k, v in drops.items() if v},
                    review_days=(
                        args.review_days if args.review_days is not None else TRADE_REVIEW_DAYS
                    ),
                    tilt=not args.no_tilt,
                )
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 1
            text = render(report, ours=ours.espn_team_id, when=calendar.date_of(report.today))
        print(text)
        print("")
        print("Read-only; nothing was written.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
