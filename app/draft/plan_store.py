"""The plan kept: the model's build in `team_reports`, the manager's marks in their own tables.

THE CACHE, IN TWO LAYERS

A cold plan is two minutes of ceilings (`app.draft.plan`), so the model's
build is made once and kept the way the week report is: a `team_reports`
row, kind `draft_plan`, whose payload is the model's half of what the route
answers. One row per team per pool (`slot_for`: BBM, ESPN, or an uploaded
set), so switching the page between pools does not throw the other away.

The model's build is fresh while its `key` matches (`plan_key`): the pool
(a new BBM capture is a new key), the league's own numbers (the budget, the
places, the teams), the fan team, and the must set at its prices. When the
key has moved the stored build is still served, marked `rebuilding`, while a
new one is made in the background; with nothing stored the answer is
`building`, and the page waits. One build per (team, pool) runs at a time,
and one that failed says why until the page asks for it again.

The manager's own going prices and ceilings do not rebuild it. They are the
second layer (`app.draft.plan.effective`): the lists re-read and the best
roster re-solved over the stored build, keyed on the build's key and the
marks' `updated_at`, so every edit is followed by a fresh answer in a few
seconds -- the solve is about three on the 2027 pool, plus four to reload
the room when this process has not got it in memory (`ROOMS`).

THE MARKS

What the manager keeps (`draft_plans`, `draft_plan_marks`): his going price
and his ceiling per man, a tag per man, a note per man, his ladder, his
notes, his fan team. Never merged into the model's numbers: the route hands
both, side by side.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import reports
from app.db.models import DraftPlan, LeagueSeason, Team, TeamReport
from app.draft import plan as engine
from app.draft.live import Room, RoomError
from app.draft.market import MINIMUM_BID
from app.draft.room import DraftState

log = logging.getLogger("fcp.draft_plan")

#: What a cold build takes, when nothing stored says so: the 2027 BBM pool
#: on seven workers took 125 seconds (docs/draft_plan.md, "What it costs").
COLD_BUILD_SECONDS = 130

#: The ladder's slack: how far past a place the room lets a bid go
#: (`app.draft.room.Allocation`, 10%). A must man with no ceiling of the
#: manager's own is held up to his going price plus this on the night.
SLACK = 0.10


def slot_for(source: engine.PoolSource) -> int:
    """The `scoring_period` a plan on this pool is kept under: 0 for BBM,
    1 for ESPN, 1000 plus the set id for an uploaded set."""
    if source.kind == "bbm":
        return 0
    if source.kind == "upload" and source.set_id is not None:
        return 1000 + source.set_id
    return 1


def plan_key(
    source: engine.PoolSource,
    league_season: LeagueSeason,
    fan_team: str | None,
    must: Mapping[int, int | None],
) -> str:
    """Everything the model's build depends on, as one string."""
    locked = ",".join(f"{pid}@{price or 'going'}" for pid, price in sorted(must.items()))
    return (
        f"{source.key}|budget={league_season.auction_budget}|teams={league_season.team_count}"
        f"|lineup={sorted((league_season.lineup_slots or {}).items())}"
        f"|bench={league_season.bench_slots}|fan={fan_team or ''}|must={locked}"
    )


def stored(session: Session, team: Team, source: engine.PoolSource) -> TeamReport | None:
    """The build kept for this team on this pool, whatever its key."""
    return session.scalar(
        select(TeamReport).where(
            TeamReport.team_id == team.id,
            TeamReport.kind == reports.DRAFT_PLAN,
            TeamReport.scoring_period == slot_for(source),
        )
    )


# ---------------------------------------------------------------------------
# building in the background
# ---------------------------------------------------------------------------


@dataclass
class Build:
    """One build in flight, or one that finished or failed."""

    key: str
    started: dt.datetime
    error: str | None = None
    done: bool = False


#: (team pk, slot) -> the build running, or last run, for it.
BUILDS: dict[tuple[int, int], Build] = {}
_LOCK = threading.Lock()

#: The builder: `app.draft.plan.build_plan`. Tests swap in a fast one.
Builder = Callable[..., engine.Plan]
BUILDER: Builder = engine.build_plan

#: Build on the calling thread rather than in the background (tests).
SYNC = False

#: Rooms this process has built plans in, by build key, for re-solving the
#: best roster at the manager's prices without reloading the pool.
ROOMS: OrderedDict[str, tuple[Room, DraftState]] = OrderedDict()
ROOMS_KEPT = 3


def _keep_room(key: str, room: Room | None, state: DraftState | None) -> None:
    if room is None or state is None:
        return
    with _LOCK:
        ROOMS[key] = (room, state)
        ROOMS.move_to_end(key)
        while len(ROOMS) > ROOMS_KEPT:
            ROOMS.popitem(last=False)


def building(team: Team, source: engine.PoolSource) -> Build | None:
    with _LOCK:
        return BUILDS.get((team.id, slot_for(source)))


def start(
    factory: sessionmaker[Session],
    league_season: LeagueSeason,
    team: Team,
    source: engine.PoolSource,
    *,
    key: str,
    fan_team: str | None,
    must: Mapping[int, int | None],
    force: bool = False,
) -> Build:
    """Start a build of this plan unless one for the same key is running.

    A build that failed for this key is not tried again until `force` (the
    page's Rebuild), so a pool that cannot be planned on says why once
    rather than spending two minutes on every look.
    """
    slot = (team.id, slot_for(source))
    with _LOCK:
        running = BUILDS.get(slot)
        if running is not None and running.key == key and not running.done:
            return running
        if running is not None and running.key == key and running.error and not force:
            return running
        build = Build(key=key, started=dt.datetime.now(dt.UTC))
        BUILDS[slot] = build
    ids = (int(league_season.id), int(team.id))
    args = (factory, ids, source, key, fan_team, dict(must), build)
    if SYNC:
        _run(*args)
    else:
        threading.Thread(target=_run, args=args, name=f"draft-plan-{team.id}", daemon=True).start()
    return build


def _run(
    factory: sessionmaker[Session],
    ids: tuple[int, int],
    source: engine.PoolSource,
    key: str,
    fan_team: str | None,
    must: dict[int, int | None],
    build: Build,
) -> None:
    league_season_pk, team_pk = ids
    try:
        with factory() as session:
            league_season = session.get(LeagueSeason, league_season_pk)
            team = session.get(Team, team_pk)
            if league_season is None or team is None:
                raise RoomError("the team is no longer stored")
            plan = BUILDER(
                session,
                league_season,
                int(team.espn_team_id),
                source=source,
                fan_team=fan_team,
                must=must or None,
            )
            payload = plan.payload(dt.date.today())
            payload["key"] = key
            payload["built_at"] = dt.datetime.now(dt.UTC).isoformat()
            reports.store(session, team_pk, reports.DRAFT_PLAN, slot_for(source), payload)
            session.commit()
        _keep_room(key, plan.room, plan.plan_state)
        build.done = True
    except (RoomError, ValueError) as error:
        build.error = str(error)
        build.done = True
    except Exception as error:  # a build must never take the server with it
        log.exception("draft plan build failed for team %s", team_pk)
        build.error = f"the plan could not be built: {type(error).__name__}"
        build.done = True


# ---------------------------------------------------------------------------
# the manager's prices over the stored build
# ---------------------------------------------------------------------------

#: (build key, marks version) -> the effective plan, the last few.
EFFECTIVE: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
EFFECTIVE_KEPT = 8


def _solver(
    session: Session,
    league_season: LeagueSeason,
    team: Team,
    source: engine.PoolSource,
    payload: Mapping[str, Any],
) -> engine.BestSolver:
    """The best-roster solver for a stored build: its room from memory, or
    loaded again (with the must men bought) when this process has not got it."""
    key = str(payload.get("key") or "")
    with _LOCK:
        held = ROOMS.get(key)
    if held is None:
        room, _ = engine.load_plan_room(session, league_season, int(team.espn_team_id), source)
        state = room.state
        must = payload.get("must") or {}
        if must.get("applied"):
            prices = {int(k): int(v) for k, v in (must.get("prices") or {}).items()}
            state = engine.lock_state(room, engine.MustLock(prices=prices))
        _keep_room(key, room, state)
        held = (room, state)
    return engine.best_solver(*held)


def effective_for(
    session: Session,
    league_season: LeagueSeason,
    team: Team,
    source: engine.PoolSource,
    payload: Mapping[str, Any],
    marks: Mapping[int, Mark],
    version: str,
) -> dict[str, Any]:
    """The build with the manager's own going prices and ceilings applied.

    Kept per (build key, marks version), so a page that looks twice between
    edits solves once. With no figure of the manager's, nothing is solved.
    """
    overrides = {
        pid: engine.Override(going=m.going_price, ceiling=m.bid_up_to)
        for pid, m in marks.items()
        if m.going_price is not None or m.bid_up_to is not None
    }
    memo = (str(payload.get("key") or ""), version if overrides else "")
    with _LOCK:
        hit = EFFECTIVE.get(memo)
    if hit is not None:
        return hit
    started = dt.datetime.now(dt.UTC)
    solve = _solver(session, league_season, team, source, payload) if overrides else None
    answer = engine.effective(payload, overrides, solve)
    answer["rebuilt_at"] = dt.datetime.now(dt.UTC).isoformat()
    answer["seconds"] = round((dt.datetime.now(dt.UTC) - started).total_seconds(), 2)
    with _LOCK:
        EFFECTIVE[memo] = answer
        EFFECTIVE.move_to_end(memo)
        while len(EFFECTIVE) > EFFECTIVE_KEPT:
            EFFECTIVE.popitem(last=False)
    return answer


# ---------------------------------------------------------------------------
# the marks
# ---------------------------------------------------------------------------


def plan_row(session: Session, team: Team, *, create: bool = False) -> DraftPlan | None:
    """The manager's kept plan for this team, made on first write."""
    row = session.scalar(select(DraftPlan).where(DraftPlan.team_id == team.id))
    if row is None and create:
        row = DraftPlan(team_id=team.id, notes="")
        session.add(row)
        session.flush()
    return row


@dataclass(frozen=True)
class Mark:
    """One man's marks as the manager kept them. None is the model's figure."""

    player_id: int
    going_price: int | None
    bid_up_to: int | None
    tag: str
    note: str
    #: The pool in view when he last wrote it: marks are per team, not per
    #: source, and this only lets the page say when it was another pool.
    source: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "going_price": self.going_price,
            "bid_up_to": self.bid_up_to,
            "tag": self.tag,
            "note": self.note,
            "source": self.source,
        }


def marks_of(row: DraftPlan | None) -> dict[int, Mark]:
    """The marks that say something: a figure, a tag other than none, or a note."""
    if row is None:
        return {}
    return {
        m.player_id: Mark(m.player_id, m.going_price, m.bid_up_to, m.tag, m.note or "", m.source)
        for m in row.marks
        if m.going_price is not None or m.bid_up_to is not None or m.tag != "none" or m.note
    }


def version_of(row: DraftPlan | None) -> str:
    """The marks' version: when the manager last wrote any of them."""
    return row.updated_at.isoformat() if row is not None and row.updated_at else ""


def must_set(marks: Mapping[int, Mark]) -> dict[int, int | None]:
    """The must-have men, each at the manager's going price (None: the model's)."""
    return {pid: m.going_price for pid, m in marks.items() if m.tag == "must"}


def marks_for(
    session: Session, league_season: LeagueSeason, espn_team_id: int
) -> tuple[dict[int, Mark], list[int] | None]:
    """What the room reads on the night: this team's marks and its ladder.

    The seam the draft screen reads (`app.draft.session.DraftSession`); empty
    when the manager kept nothing, which is a room exactly as it was.
    """
    team = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id, Team.espn_team_id == espn_team_id
        )
    )
    if team is None:
        return {}, None
    row = plan_row(session, team)
    return marks_of(row), (list(row.ladder) if row is not None and row.ladder else None)


def ladder_problem(
    ladder: list[int], *, budget: int, places: int, floor: int = MINIMUM_BID
) -> str | None:
    """Why a ladder cannot be kept, or None: one amount per place, each at
    least the minimum bid, adding up to the budget exactly."""
    if len(ladder) != places:
        return f"a ladder has one amount per place: {places}, not {len(ladder)}"
    if any(amount < floor for amount in ladder):
        return f"every place costs at least ${floor}"
    total = sum(ladder)
    if total != budget:
        return f"the places add up to ${total}, not the ${budget} budget"
    return None


def cap_for(ladder: Iterable[int], floor: int = MINIMUM_BID) -> int:
    """The most one man may take under a ladder: its top place plus the slack,
    the rule the room's own cap follows (`Allocation.cap`)."""
    amounts = sorted(ladder, reverse=True)
    return max(floor, int(amounts[0] * (1.0 + SLACK))) if amounts else floor


def held_up_to(mark: Mark | None, going: int | None) -> int | None:
    """The figure the room holds up for a man on the night.

    His ceiling when the manager set one; for a must man without one, his
    going price (the manager's, else the model's) plus the ladder's slack,
    because he is a man the plan does not let go; otherwise none.
    """
    if mark is None:
        return None
    if mark.bid_up_to is not None:
        return mark.bid_up_to
    if mark.tag == "must":
        price = mark.going_price if mark.going_price is not None else going
        if price:
            return max(MINIMUM_BID, int(price * (1.0 + SLACK)))
    return None
