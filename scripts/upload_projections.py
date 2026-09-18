#!/usr/bin/env python3
"""Store a manager's own projections as a set the draft room can be loaded from.

Usage:
    python scripts/upload_projections.py --season 2027 --file ~/proj.csv \\
        --name "Hashtag preseason" --note "hashtagbasketball.com, 14 Sep"
    python scripts/upload_projections.py --season 2027 --file ~/proj.csv \\
        --name "Hashtag preseason" --map "Points=PTS" --commit
    python scripts/upload_projections.py --season 2027 --list

Basketball Monster's numbers are paid and stay with the member who fetched
them (docs/projection_sources.md), so this is how anybody else gets a board:
his own CSV, .xlsx or .xls, from wherever he pays for it.

DRY RUN BY DEFAULT. Nothing is stored until --commit. The first run prints
the mapping it guessed, the basis it measured, who it could not match and any
row it could not read, so the mapping is confirmed before a board is built on
it. `--map "Header=FIELD"` corrects a column the guess got wrong; repeat it
per column. The fields are name, games, PTS, REB, AST, STL, BLK, 3PM, TO,
FGM, FGA, FTM, FTA, FG%, FT%, team and position.

A file carrying a percentage and no attempts is refused with the reason: a
roster's FG% is made shots over attempts, and cannot be rebuilt from a
percentage (app/scoring/lines.py).

Then draft on it:
    python scripts/draft_room.py --season 2027 --me "..." --projection-set <id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.projections.upload import import_set, stored_sets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--file", type=Path, help="the projections file (.csv, .xlsx or .xls)")
    ap.add_argument("--name", help="what to call this set, e.g. 'Hashtag preseason'")
    ap.add_argument("--owner", default="patrick", help="whose set it is (default: patrick)")
    ap.add_argument("--note", default="", help="where the numbers came from, in your own words")
    ap.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="HEADER=FIELD",
        help="force one column, e.g. --map 'Points=PTS'; repeat per column",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="read the file and print what would be stored (the default)",
    )
    ap.add_argument("--commit", action="store_true", help="store the set")
    ap.add_argument("--list", action="store_true", help="list the sets stored for this season")
    args = ap.parse_args()

    if args.dry_run and args.commit:
        raise SystemExit("--dry-run and --commit are opposites; --dry-run is the default")

    factory = make_session_factory(make_engine(get_settings().database_url))
    if args.list:
        with factory() as session:
            return _list(session, args.season)
    if args.file is None or args.name is None:
        raise SystemExit("--file and --name are required unless you pass --list")
    if not args.file.exists():
        raise SystemExit(f"no such file: {args.file}")

    try:
        overrides = dict(_pair(entry) for entry in args.map)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    dry_run = not args.commit
    with factory() as session:
        report = import_set(
            session,
            season=args.season,
            name=args.name,
            owner=args.owner,
            source_note=args.note,
            path=args.file,
            mapping_overrides=overrides,
            dry_run=dry_run,
        )
        for line in report.lines():
            print(line)
        if not report.ok:
            session.rollback()
            return 1
        if dry_run:
            session.rollback()
        else:
            session.commit()
            print(
                f"draft on it with: python scripts/draft_room.py --season {args.season} "
                f'--me "<your team>" --projection-set {report.set_id}'
            )
    return 0


def _list(session: Session, season: int) -> int:
    """Every set stored for a season, newest first."""
    sets = stored_sets(session, season)
    if not sets:
        print(f"no projection sets stored for {season}")
        return 0
    print(f"{'id':>4}  {'uploaded':<16}  {'owner':<12}  {'rows':>5}  name")
    for stored in sets:
        print(
            f"{stored.id:>4}  {stored.uploaded_at.strftime('%Y-%m-%d %H:%M'):<16}  "
            f"{stored.owner:<12}  {stored.rows:>5}  {stored.name}"
        )
        if stored.source_note:
            print(f"{'':>4}  {stored.source_note}")
    return 0


def _pair(entry: str) -> tuple[str, str]:
    header, sep, field = entry.partition("=")
    if not sep or not header.strip() or not field.strip():
        raise ValueError(f"--map {entry!r}: expected HEADER=FIELD, e.g. --map 'Points=PTS'")
    return header.strip(), field.strip()


if __name__ == "__main__":
    sys.exit(main())
