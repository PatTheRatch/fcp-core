#!/usr/bin/env python3
"""Does the fast ceiling give the same answer as the old one?

`bid_ceiling` was cut from about nine solves at four restarts to about six
at none, because on the VPS it was taking 8 to 56 seconds against a
thirty-second nomination clock (see `app.draft.room`). Four changes, each
of which could in principle move the answer:

  the without-him baseline is reused from the cached plan rather than
  solved again; the with-him solves drop their restarts because they warm
  start from that baseline; the bisection stops on a two-dollar bracket
  rather than a one-dollar one; and its first probe goes to the expected
  going price rather than the middle of the range.

This replays a real draft into the room and, at every nomination, computes
the ceiling both ways against the same room. It reports the distribution of
the differences and the worst cases.

    python scripts/ceiling_check.py --season 2027 --me "Through The Wire" \\
        --rehearse 2026 --bbm ...total.xls --bbm-per-game ...pergame.xls

THE GATE. If the fast ceiling differs by more than $2 on more than 5% of
nominations, the old path stays the default: `app.draft.room.bid_ceiling`
is called with `fast=False` and the screen keeps waiting. The script says
which way the gate fell.

Read-only. It touches no log, no draft and nothing on disk.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.draft.estimate import estimated_worth
from app.draft.live import Room, RoomError, load_room
from app.draft.optimizer import RosterPlan
from app.draft.rehearsal import Nomination, load_nominations
from app.draft.room import Ceiling, DraftState, Pick, bid_ceiling, resolve

#: The gate, in dollars and as a share of nominations.
TOLERANCE = 2
ALLOWED_SHARE = 0.05

#: Restarts for the referee. Where the two paths disagree by more than the
#: tolerance, the question is not which is which but which is right, and the
#: answer turns on whose without-him roster is the better one. A solve at
#: this many restarts is the best roster either could have been aiming at.
REFEREE_RESTARTS = 24

_ROOM: Room | None = None


def _install(room: Room) -> None:
    global _ROOM
    _ROOM = room


@dataclass(frozen=True)
class Comparison:
    name: str
    pick_number: int
    going: int | None
    old: int | None
    new: int | None
    old_seconds: float
    new_seconds: float
    #: Expected weekly wins of each path's best roster without him, and of
    #: the referee's, for the rows where the two disagree. Higher is better:
    #: the ceiling is how far above that bar the player can be carried, so a
    #: baseline that searched badly hands out a ceiling that is too high.
    old_without: float = 0.0
    new_without: float = 0.0
    referee_without: float | None = None
    #: What the card shows the moment he hits the block, before any search
    #: (`app.draft.estimate`). Reported against the exact ceiling, because a
    #: number a manager may bid on for the first seconds of the clock had
    #: better be in the right neighbourhood.
    estimate: int | None = None

    @property
    def better_baseline(self) -> str | None:
        """Which path's without-him roster was nearer the referee's."""
        if self.referee_without is None:
            return None
        old_gap = abs(self.referee_without - self.old_without)
        new_gap = abs(self.referee_without - self.new_without)
        if abs(old_gap - new_gap) < 1e-9:
            return "tie"
        return "old" if old_gap < new_gap else "new"

    @property
    def difference(self) -> int | None:
        """New less old, in dollars. None when only one of them named a price."""
        if self.old is None and self.new is None:
            return 0
        if self.old is None or self.new is None:
            return None
        return self.new - self.old


def _one(state: DraftState, player_id: int, name: str) -> Comparison:
    """Both ceilings for one nomination, against the same room."""
    room = _ROOM
    if room is None:
        raise RuntimeError("worker started without its room")

    def ceiling(*, fast: bool, baseline: RosterPlan | None) -> Ceiling:
        return bid_ceiling(
            state,
            player_id,
            room.candidates,
            room.distributions,
            punt=room.punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=room.restarts,
            allocation=room.allocation,
            baseline=baseline,
            fast=fast,
        )

    started = time.monotonic()
    old = ceiling(fast=False, baseline=None)
    old_seconds = time.monotonic() - started

    # The fast path is handed the cached plan the session would have, so what
    # is timed is what the draft room actually runs. The plan is cached per
    # pick sequence and shared across every ceiling at that pick, so its own
    # cost is not part of one ceiling's.
    plan = resolve(
        state,
        room.candidates,
        room.distributions,
        punt=room.punt,
        lineup=room.lineup,
        limits=room.limits,
        restarts=room.restarts,
        allocation=room.allocation,
    )
    started = time.monotonic()
    new = ceiling(fast=True, baseline=plan)
    new_seconds = time.monotonic() - started

    # Only where they disagree is it worth asking a referee, and only the
    # baseline needs refereeing: everything else the two paths do is the
    # same arithmetic.
    referee = None
    if old.price is None or new.price is None or abs(new.price - old.price) > TOLERANCE:
        referee = resolve(
            state,
            room.candidates,
            room.distributions,
            punt=room.punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=REFEREE_RESTARTS,
            allocation=room.allocation,
            exclude=[player_id],
        ).expected_wins

    return Comparison(
        name=name,
        pick_number=len(state.picks),
        going=next((c.price for c in room.candidates if c.player_id == player_id), None),
        old=old.price,
        new=new.price,
        old_seconds=old_seconds,
        new_seconds=new_seconds,
        old_without=old.without,
        new_without=new.without,
        referee_without=referee,
        estimate=estimated_worth(
            state,
            plan,
            next(c for c in room.candidates if c.player_id == player_id),
            room.distributions,
            punt=room.punt,
            lineup=room.lineup,
            limits=room.limits,
            cap=room.allocation.cap(state) if room.allocation is not None else None,
        ),
    )


def nominations_with_rooms(
    room: Room, nominations: list[Nomination], *, every: int, limit: int | None
) -> list[tuple[DraftState, int, str]]:
    """Each nomination, with the room as it stood when he went up.

    The draft is replayed exactly as the rehearsal replays it -- every player
    to the team that really bought him, at what they really paid -- and the
    state is snapshotted before each nomination is sold.
    """
    on_board = {c.player_id for c in room.candidates}
    state = room.state
    out: list[tuple[DraftState, int, str]] = []
    for index, nomination in enumerate(nominations):
        if nomination.player_id in state.taken or state.complete:
            continue
        if nomination.player_id in on_board and index % every == 0:
            out.append((state, nomination.player_id, nomination.name))
            if limit is not None and len(out) >= limit:
                break
        try:
            state = state.apply(Pick(nomination.player_id, nomination.team_id, nomination.price))
        except Exception:
            continue
    return out


def report(rows: list[Comparison]) -> bool:
    """Print the distribution and the worst cases. True when the gate passes."""
    if not rows:
        print("no nominations compared")
        return False
    differences = [row.difference for row in rows]
    disagreed = [row for row in rows if row.difference is None]
    numeric = [d for d in differences if d is not None]
    absolute = [abs(d) for d in numeric]
    over = [row for row in rows if row.difference is None or abs(row.difference) > TOLERANCE]
    share = len(over) / len(rows)

    print(f"\n{len(rows)} nominations compared\n")
    print("difference, new ceiling less old, in dollars")
    buckets = Counter(
        "0" if d == 0 else "1" if abs(d) == 1 else "2" if abs(d) == 2 else "3 or more"
        for d in numeric
    )
    for label in ("0", "1", "2", "3 or more"):
        count = buckets.get(label, 0)
        print(f"  {label:>9}  {count:4d}  {count / len(rows):6.1%}")
    if disagreed:
        print(
            f"  {'one only':>9}  {len(disagreed):4d}  {len(disagreed) / len(rows):6.1%}"
            "   (one path named a price and the other did not)"
        )
    print()
    print(f"  mean absolute   ${statistics.fmean(absolute):.2f}")
    print(f"  median absolute ${statistics.median(absolute):.1f}")
    print(f"  worst           ${max(absolute) if absolute else 0}")
    print(f"  over ${TOLERANCE}         {len(over)} of {len(rows)} ({share:.1%})")

    refereed = [row for row in rows if row.referee_without is not None]
    if refereed:
        verdicts = Counter(row.better_baseline for row in refereed)
        print(
            f"\nwhere they disagree by more than ${TOLERANCE}, whose without-him roster was "
            f"nearer a {REFEREE_RESTARTS}-restart solve"
        )
        for label in ("new", "old", "tie"):
            print(f"  {label:>4}  {verdicts.get(label, 0):4d}")
        print(
            "  (a baseline that searched badly sets the bar too low and hands out a "
            "ceiling that is too high)"
        )

    print("\nworst cases")
    worst = sorted(rows, key=lambda r: (r.difference is None, -abs(r.difference or 0)))[:10]
    for row in worst:
        old = "none" if row.old is None else f"${row.old}"
        new = "none" if row.new is None else f"${row.new}"
        print(
            f"  pick {row.pick_number:>3}  {row.name:<26} going ${row.going:<4} "
            f"old {old:>5}  new {new:>5}"
        )

    # The estimate is what the card shows while the search runs. It is not
    # the ceiling and is not meant to be; the question is whether a manager
    # acting on it for ten seconds is acting on something sane.
    estimated = [
        abs(row.estimate - row.new)
        for row in rows
        if row.estimate is not None and row.new is not None
    ]
    if estimated:

        def within(dollars: int) -> float:
            return sum(1 for gap in estimated if gap <= dollars) / len(estimated)

        print(f"\nthe instant estimate against the exact ceiling, on {len(estimated)} nominations")
        print(f"  mean absolute   ${statistics.fmean(estimated):.2f}")
        print(f"  median absolute ${statistics.median(estimated):.1f}")
        for dollars in (3, 5, 10):
            print(f"  within ${dollars:<10} {within(dollars):.1%}")
        print(f"  worst           ${max(estimated)}")

    old_seconds = [row.old_seconds for row in rows]
    new_seconds = [row.new_seconds for row in rows]
    print("\nseconds for one ceiling")
    for label, values in (("old", old_seconds), ("new", new_seconds)):
        ordered = sorted(values)
        print(
            f"  {label}  median {statistics.median(ordered):5.1f}  "
            f"90th {ordered[int(0.9 * (len(ordered) - 1))]:5.1f}  max {max(ordered):5.1f}"
        )
    faster = statistics.median(old_seconds) / max(1e-9, statistics.median(new_seconds))
    print(f"  speedup, on the median: {faster:.1f}x")

    passed = share <= ALLOWED_SHARE
    print()
    if passed:
        print(
            f"GATE PASSED: {share:.1%} differ by more than ${TOLERANCE}, "
            f"within the {ALLOWED_SHARE:.0%} allowed. The fast path is the default."
        )
    else:
        print(
            f"GATE FAILED: {share:.1%} differ by more than ${TOLERANCE}, over the "
            f"{ALLOWED_SHARE:.0%} allowed. Keep the old path as the default."
        )
    return passed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2027)
    ap.add_argument("--me", default="Through The Wire")
    ap.add_argument("--rehearse", type=int, default=2026, help="the draft to replay")
    ap.add_argument("--bbm", type=Path)
    ap.add_argument("--bbm-per-game", type=Path)
    ap.add_argument("--projection-set", type=int)
    ap.add_argument("--pool-season", type=int)
    ap.add_argument("--pool-kind", default="projected", choices=("projected", "total"))
    ap.add_argument("--plan", default="history", choices=("history", "optimizer", "none"))
    ap.add_argument("--restarts", type=int, default=4)
    ap.add_argument("--punt", action="append", default=[])
    ap.add_argument("--every", type=int, default=1, help="compare every Nth nomination")
    ap.add_argument("--limit", type=int, help="stop after this many comparisons")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    try:
        room = load_room(
            args.season,
            args.me,
            pool_season=args.pool_season,
            pool_kind=args.pool_kind,
            punt=args.punt,
            restarts=args.restarts,
            bbm=args.bbm,
            bbm_per_game=args.bbm_per_game,
            projection_set=args.projection_set,
            plan=args.plan,
        )
    except RoomError as exc:
        raise SystemExit(str(exc)) from exc

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as db:
        nominations = load_nominations(db, args.rehearse, room.team_names)
    work = nominations_with_rooms(room, nominations, every=args.every, limit=args.limit)
    print(
        f"{room.pool_note}\nreplaying {args.rehearse} into the {args.season} room: "
        f"{len(nominations)} nominations, {len(work)} of them on our board",
        flush=True,
    )

    rows: list[Comparison] = []
    started = time.monotonic()
    with ProcessPoolExecutor(args.workers, initializer=_install, initargs=(room,)) as pool:
        for done, row in enumerate(pool.map(_one, *zip(*work, strict=True)), start=1):
            rows.append(row)
            print(
                f"  [{done}/{len(work)}] {row.name:<26} old {row.old}  new {row.new}  "
                f"({row.old_seconds:.1f}s -> {row.new_seconds:.1f}s)",
                flush=True,
            )
    print(f"\nwall clock {time.monotonic() - started:.0f}s", flush=True)
    return 0 if report(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
