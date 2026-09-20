#!/usr/bin/env python3
"""Open the draft room for the night, with nothing to type.

Usage:
    python scripts/draft_night.py

or double-click `scripts/Draft Room.command`. Every default is worked out
here and every one of them is overridable with scripts/draft_service.py's
own flags:

    season      the newest data/bbm/BBM_Projections_<season>_total.xls that
                has its _pergame twin (--season, --bbm, --bbm-per-game)
    our team    FCP_TRACKED_TEAM_ID in .env, resolved to its name through
                the room (--me)
    the log     logs/draft-<season>.jsonl, the real one (--log)
    the screen  http://127.0.0.1:8765, opened in the default browser once
                the service is listening (--host, --port)

The service is scripts/draft_service.py's, not a copy of it. ESPN's room is
connected from the screen: press Connect, sign in in the window if it
asks, and watch the pill go green. docs/draft_night.md is the runbook.
"""

from __future__ import annotations

import argparse
import re
import sys
import webbrowser
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this, `app` resolves to whichever checkout the interpreter's venv installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.draft_service import build_parser, serve

EXPORTS = Path("data") / "bbm"
_TOTAL = re.compile(r"BBM_Projections_(\d{4})_total\.xls$")


def newest_export(folder: Path = EXPORTS) -> tuple[int, Path, Path] | None:
    """The newest season with both Basketball Monster exports in `folder`:
    (season, total, per game). A total without its per-game twin is not a
    room, so it is skipped rather than half-loaded."""
    found: list[tuple[int, Path, Path]] = []
    for total in folder.glob("BBM_Projections_*_total.xls"):
        match = _TOTAL.search(total.name)
        if match is None:
            continue
        per_game = total.with_name(f"BBM_Projections_{match.group(1)}_pergame.xls")
        if per_game.exists():
            found.append((int(match.group(1)), total, per_game))
    return max(found, key=lambda item: item[0]) if found else None


def fill_defaults(
    args: argparse.Namespace, *, exports: Path = EXPORTS, tracked_team_id: int | None
) -> argparse.Namespace:
    """The night's defaults, where a flag did not say otherwise. Refuses,
    with what to do, rather than starting a room on the wrong files."""
    if args.season is None or (args.bbm is None and args.projection_set is None):
        export = newest_export(exports)
        if export is None:
            raise SystemExit(
                f"no Basketball Monster export in {exports}/: expected "
                "BBM_Projections_<season>_total.xls and its _pergame twin. "
                "Pull them (scripts/bbm_pull.py --season <season> --store), "
                "or pass --season with --bbm and --bbm-per-game."
            )
        season, total, per_game = export
        if args.season is None:
            args.season = season
        if args.bbm is None and args.projection_set is None:
            if args.season != season:
                raise SystemExit(
                    f"the newest export is for {season}, not {args.season}; "
                    "pass --bbm and --bbm-per-game for the files you mean"
                )
            args.bbm, args.bbm_per_game = total, per_game
    if args.me is None:
        if tracked_team_id is None:
            raise SystemExit("no team: set FCP_TRACKED_TEAM_ID in .env, or pass --me")
        args.me = tracked_team_id
    return args


def main() -> int:
    from app.config import get_settings

    args = build_parser(require_room=False).parse_args()
    args = fill_defaults(args, tracked_team_id=get_settings().fcp_tracked_team_id)
    print(f"season {args.season} on {args.bbm or f'projection set {args.projection_set}'}")
    screen = f"http://{args.host}:{args.port}"

    def open_screen() -> None:
        webbrowser.open(screen)

    return serve(args, on_ready=open_screen)


if __name__ == "__main__":
    sys.exit(main())
