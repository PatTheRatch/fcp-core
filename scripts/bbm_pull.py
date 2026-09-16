#!/usr/bin/env python3
"""Download BBM's season-total and per-game exports into data/bbm/.

Usage:
    python scripts/bbm_pull.py [--season 2027] [--dir data/bbm] [--movers 3]

Logs in with BBM_USERNAME / BBM_PASSWORD from .env (never from the command
line), pulls both exports and writes BBM_Projections_<season>_total.xls and
BBM_Projections_<season>_pergame.xls, the names the draft scripts take. Each
file must load through the room's reader before it replaces the old one.
Then it prints what moved since the last pull: players added or dropped, and
league dollars (Leag$) that changed by at least --movers.

The files are paid data: data/bbm/ is git-ignored, and stays that way.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from app.draft.bbm import BBMRow, read_bbm
from app.draft.bbm_pull import BBMClient, BBMPullError, BBMSettings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, help="refuse the pull unless BBM is projecting this")
    ap.add_argument("--dir", type=Path, default=Path("data/bbm"))
    ap.add_argument("--movers", type=float, default=3.0, help="Leag$ change worth listing")
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
    for export in result.exports:
        target = args.dir / f"BBM_Projections_{result.season}_{export.kind}.xls"
        fresh = target.with_suffix(".xls.new")
        fresh.write_bytes(export.body)
        try:
            rows = read_bbm(fresh)
        except Exception as exc:
            fresh.unlink()
            raise SystemExit(
                f"{export.kind} export did not load, kept the old file: {exc}"
            ) from exc
        before = read_bbm(target) if target.exists() else []
        fresh.replace(target)
        print(f"\n{target}: {len(rows)} players")
        report_movers(before, rows, args.movers)
    print(f"\npulled {datetime.date.today():%-d %b %Y}")
    return 0


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
