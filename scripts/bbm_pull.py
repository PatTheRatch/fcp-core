#!/usr/bin/env python3
"""Download BBM's season-total and per-game exports into data/bbm/.

Usage:
    python scripts/bbm_pull.py [--season 2027] [--dir data/bbm] [--movers 3]

Logs in with BBM_USERNAME / BBM_PASSWORD from .env (never from the command
line), pulls both exports and writes BBM_Projections_<season>_total.xls and
BBM_Projections_<season>_pergame.xls, the names the draft scripts take. Each
file must load through the room's reader before it replaces the old one.
Then it prints what moved since the last pull: players added or dropped, and
league dollars (Leag$) that changed by at least --movers. It refuses, keeping
the old files, an export without Leag$ or one whose projected games moved
across the board, since both come from BBM settings, not news.

With --store it also keeps both exports in the database, a version of each
player's row whenever it changes (`app.draft.bbm_store`), which is what the
daily timer on the VPS runs.

The files are paid data: data/bbm/ is git-ignored, and stays that way.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from app.draft.bbm import BBMRow, read_bbm
from app.draft.bbm_pull import BBMClient, BBMPullError, BBMSettings, PullResult


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, help="refuse the pull unless BBM is projecting this")
    ap.add_argument("--dir", type=Path, default=Path("data/bbm"))
    ap.add_argument("--movers", type=float, default=3.0, help="Leag$ change worth listing")
    ap.add_argument(
        "--store",
        action="store_true",
        help="also keep today's exports in the database (app.draft.bbm_store)",
    )
    ap.add_argument(
        "--accept-games",
        action="store_true",
        help="replace the files even if projected games shifted across the board",
    )
    args = ap.parse_args()

    settings = BBMSettings()  # read from .env
    client = BBMClient(settings.bbm_username, settings.bbm_password)
    try:
        client.login()
        result = client.pull()
    except BBMPullError as exc:
        raise SystemExit(f"BBM pull failed: {exc}") from exc
    if args.season is not None and result.season != args.season:
        raise SystemExit(f"BBM is projecting {result.season}, not {args.season}; nothing written")
    print(f"BBM {result.season}, {result.source}, league {result.league!r}")

    args.dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path, list[BBMRow], list[BBMRow]]] = []
    for export in result.exports:
        target = args.dir / f"BBM_Projections_{result.season}_{export.kind}.xls"
        fresh = target.with_suffix(".xls.new")
        fresh.write_bytes(export.body)
        before = read_bbm(target) if target.exists() else []
        days = (
            (datetime.date.today() - datetime.date.fromtimestamp(target.stat().st_mtime)).days
            if target.exists()
            else 0
        )
        try:
            rows = read_bbm(fresh)
            check(rows, before, accept_games=args.accept_games, days=days)
        except Exception as exc:
            for path in [fresh, *(f for _, f, _, _ in staged)]:
                path.unlink(missing_ok=True)
            raise SystemExit(f"{export.kind} export refused, kept the old files: {exc}") from exc
        staged.append((target, fresh, before, rows))
    for target, fresh, before, rows in staged:
        fresh.replace(target)
        print(f"\n{target}: {len(rows)} players")
        report_movers(before, rows, args.movers)
    print(f"\npulled {datetime.date.today():%-d %b %Y}")
    if args.store:
        store(result)
    return 0


def store(result: PullResult) -> None:
    """Keep today's exports in the database, as versions of each player's row."""
    from app.config import get_settings
    from app.db.session import make_engine, make_session_factory
    from app.draft.bbm_store import capture, read_export

    factory = make_session_factory(make_engine(get_settings().database_url))
    today = datetime.datetime.now(datetime.UTC).date()
    with factory() as session:
        for export in result.exports:
            saved = capture(
                session,
                season=result.season,
                value_type=export.kind,
                rows=read_export(export.body),
                captured_on=today,
                source=result.source,
                league=result.league,
            )
            print(
                f"stored {export.kind}: {saved.players} players, {saved.changed} changed, "
                f"{saved.dropped} dropped"
            )
        session.commit()


#: A rise in the median player's projected games this large is a settings
#: change (Assume Good Health, the projection window), not news.
GAMES_SHIFT = 2.0

#: How fast a rest-of-season projection's games fall on their own once the
#: season is on: an NBA team plays about 3.5 games a week.
GAMES_PER_DAY = 0.5


def check(rows: list[BBMRow], before: list[BBMRow], *, accept_games: bool, days: int = 0) -> None:
    """Refuse an export the room would misread.

    Projected games may fall with the calendar (half a game a day in season,
    as games are played) but a jump up, or a fall faster than the days
    explain, is a setting.
    """
    if not any(r.league_dollars is not None for r in rows):
        raise ValueError("no Leag$ column; the league-settings values are switched off in BBM")
    if not before or accept_games:
        return
    old = {r.name: r.games for r in before}
    shifts = sorted(r.games - old[r.name] for r in rows if r.name in old)
    if shifts:
        median = shifts[len(shifts) // 2]
        if median >= GAMES_SHIFT or median <= -(GAMES_SHIFT + GAMES_PER_DAY * days):
            raise ValueError(
                f"projected games moved {median:+.0f} for the median player in {days} days. "
                "That is a BBM setting (Assume Good Health, the projection window), not "
                "news; BBM's games already price missed time and the room relies on that. "
                "Fix the setting, or pass --accept-games if the change is real"
            )


def report_movers(before: list[BBMRow], after: list[BBMRow], threshold: float) -> None:
    if not before:
        return
    old = {r.name: r for r in before}
    new = {r.name: r for r in after}
    added = sorted(n for n in new if n not in old)
    dropped = sorted(n for n in old if n not in new)
    if added:
        print(f"  added: {', '.join(added)}")
    if dropped:
        print(f"  dropped: {', '.join(dropped)}")
    moves = []
    for name, row in new.items():
        prior = old.get(name)
        if prior is None or row.league_dollars is None or prior.league_dollars is None:
            continue
        change = row.league_dollars - prior.league_dollars
        if abs(change) >= threshold:
            moves.append((change, name, prior.league_dollars, row.league_dollars))
    for change, name, was, now in sorted(moves, key=lambda m: -abs(m[0])):
        print(f"  {name:28s} Leag$ {was:6.1f} -> {now:6.1f}  ({change:+.1f})")
    if not (added or dropped or moves):
        print(f"  no player moved ${threshold:g} or more")


if __name__ == "__main__":
    sys.exit(main())
