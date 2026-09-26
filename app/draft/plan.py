"""The draft plan: what a manager takes into the auction, worked out from the room.

The plan used to be a terminal script (`scripts/draft_plan.py`) that wrote a
page to disk. This is its engine, moved into the app so the site can serve
the plan, the manager can mark it up, and the draft room can read what he
kept. The script is now a thin caller of `build_plan` and writes the same
JSON it always did (`Plan.script_json`), so the runbook keeps working.

EVERYTHING IS READ FROM THE ROOM

The plan is the draft room's own arithmetic from before the first pick
(`app.draft.live.room_for`): the league's winning spending shape
(`app.draft.shape`) as the ladder, the tested going price
(`app.draft.live.market_prices`), BBM's league value when the pool is BBM's,
and our ceiling for every player the market or BBM prices at `CONSIDER_FROM`
or more, from an empty room (`app.draft.room.bid_ceiling`). A ceiling snaps
to the ladder's places: read it as the tier a man qualifies for.

THE POOL

One source per plan, never mixed (`PoolSource`): the newest stored BBM
capture for the season by default (`app.draft.bbm_store`), else the viewer's
newest uploaded projection set, else ESPN's projections. A plan names its
source and its date, and a BBM capture older than `STALE_AFTER_DAYS` says so
with the command that refreshes it. BBM's numbers are paid: the plan carries
them, and whoever serves it asks `app.projections.sources.may_show` first.

THE SECTIONS

The model's own lists -- stars, targets, BBM likes, let go, nominate early,
late steals and threes, cheap bigs, IR stash, the fan team -- are rules over
the players' figures (`sections`), moved here from the old page's script so
the page, the co-manager and the room read one set of them. Each carries its
rule in words; each man a one-line reason. They are the manager's labels for
his plan, not verdicts.

MUST-HAVE MEN

A manager may fix men into the roster whatever the model thinks (the
`must` tag, docs/draft_plan.md). The plan is then built around them: they
are bought at their going price -- his own going price for the man when he
set one, else the model's -- as if he had already won them, and the ladder,
the ceilings and the builds are recomputed for the places and the money left (`must_lock`). What the
lock costs -- the best roster with the set against the free best roster, in
categories a week, for the set and for each man alone -- is the fan
section's arithmetic (`lock_cost`), so a one-man set costs exactly what the
fan section says he costs. A set that does not fit the budget is refused
with a sentence and the plan falls back to the free build.

YOUR PRICES

Two of the manager's figures are inputs, not only marks (docs/draft_plan.md,
"Your prices"): his going price for a man replaces the model's as that man's
cost in the best roster, and his ceiling caps what the plan will pay for
him. They are applied in a second, fast layer over the built plan
(`effective`): the lists are re-read, and the best roster re-solved, with
them. The model's own figures are never overwritten; the route hands both,
and with no figure of his own the effective plan is the model's exactly.

WHAT IT COSTS

A cold build is dominated by the ceilings: about 150 of them, each a
bisection over whole re-solves. On the 2027 BBM pool with seven worker
processes on an eight-core Mac it took 127 seconds of wall clock and ten
minutes of CPU (2026-09-26). So a plan is built once per (team, source,
settings) and kept (`app.draft.plan_store`).
"""

from __future__ import annotations

import datetime as dt
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import fmean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, ProjectionSet
from app.draft import bbm_store
from app.draft.bbm import ROLES, parse_records
from app.draft.live import BBMInput, Room, RoomError, bargain_warning, market_prices, room_for
from app.draft.optimizer import Candidate, RosterPlan, roster_totals, score
from app.draft.room import Allocation, Ceiling, DraftError, DraftState, Pick, _fit, resolve
from app.draft.valuation import PERCENTAGE_COMPONENTS, value_players
from app.projections import sources
from app.projections.sources import describe

#: The nine, in the order the plan's profile has always been written.
CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")

#: Players considered: anyone the market or BBM prices at this or more.
CONSIDER_FROM = 3

#: A BBM capture older than this many days is flagged, with the pull command.
STALE_AFTER_DAYS = 7

#: What refreshes the stored BBM capture (docs/draft_night.md, step 1).
REFRESH_COMMAND = ".venv/bin/python scripts/bbm_pull.py --season {season} --store"

#: The tags a manager may put on a man (`draft_plan_marks.tag`).
TAGS = ("target", "let_go", "nominate", "ir", "must", "none")

#: The NBA teams a fan team can be, as Basketball Monster abbreviates them:
#: the FAN section reads a man's team from BBM's row, so it is BBM's spelling.
NBA_TEAMS = (
    "ATL",
    "BKN",
    "BOS",
    "CHA",
    "CHI",
    "CLE",
    "DAL",
    "DEN",
    "DET",
    "GSW",
    "HOU",
    "IND",
    "LAC",
    "LAL",
    "MEM",
    "MIA",
    "MIL",
    "MIN",
    "NOR",
    "NYK",
    "OKC",
    "ORL",
    "PHI",
    "PHO",
    "POR",
    "SAC",
    "SAS",
    "TOR",
    "UTA",
    "WAS",
)

#: How the room's search is run for a plan: the script's defaults.
RESTARTS = 4
BUILD_RESTARTS = 12


def default_workers() -> int:
    """Worker processes for the ceilings: the machine's cores less one, at most seven."""
    return max(1, min(7, (os.cpu_count() or 2) - 1))


# ---------------------------------------------------------------------------
# the pool a plan is built on
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolSource:
    """Which projections one plan is built on. Exactly one pool.

    `kind` is "bbm" (a stored capture, `captured_on`, or the two export files
    on disk, `files`, which only the script passes), "upload" (`set_id`),
    "composite" (`set_id`: a named blend of other sources, whose rows are
    stored like an upload's, `app.projections.composite`) or "espn".
    `gated` is a composite whose recipe reads BBM.
    """

    kind: str
    captured_on: dt.date | None = None
    files: tuple[Path, Path | None] | None = None
    set_id: int | None = None
    #: The script's `--exported` words: when the files were pulled.
    exported: str = ""
    #: When a stored set was last uploaded: a set uploaded again under its
    #: name keeps its id, so its plan is keyed on this as well.
    version: str = ""
    gated: bool = False

    @property
    def tag(self) -> str:
        """The source tag the room and the gate read (`app.projections.sources`)."""
        if self.kind == "bbm":
            return sources.BBM
        if self.kind == "upload" and self.set_id is not None:
            return sources.upload_source(self.set_id)
        if self.kind == "composite" and self.set_id is not None:
            recipe = [{"source": sources.BBM, "weight": 1}] if self.gated else []
            return sources.composite_source(self.set_id, recipe)
        return sources.ESPN

    @property
    def key(self) -> str:
        """What a stored plan is keyed on: a new capture is a new plan."""
        if self.kind == "bbm":
            if self.captured_on is not None:
                return f"bbm:{self.captured_on.isoformat()}"
            return f"bbm-files:{self.files[0].name if self.files else ''}"
        if self.kind in ("upload", "composite"):
            return f"{self.kind}:{self.set_id}" + (f"@{self.version}" if self.version else "")
        return "espn"

    @classmethod
    def parse(cls, text: str, session: Session, season: int) -> PoolSource:
        """A source named in a URL: "bbm" (the newest capture), "espn",
        "upload:<id>" or "composite:<id>"."""
        if text == "espn":
            return cls("espn")
        if text == "bbm":
            day = bbm_store.latest_capture(session, season)
            if day is None:
                raise ValueError(f"no Basketball Monster capture is stored for {season}")
            return cls("bbm", captured_on=day)
        set_id = sources.upload_set_id(text)
        if set_id is None:
            set_id = sources.composite_set_id(text)
        if set_id is not None:
            found = cls.of_set(session, set_id)
            if found.kind != text.split(":")[0]:
                raise ValueError(f"projection set {set_id} is not a {text.split(':')[0]}")
            return found
        raise ValueError(
            f"unknown source {text!r}: bbm, espn, upload:<set id> or composite:<set id>"
        )

    @classmethod
    def of_set(cls, session: Session, set_id: int) -> PoolSource:
        """A stored set as a pool, carrying when it was last uploaded (for a
        composite, last worked out) and, for a composite, its gate."""
        found = session.get(ProjectionSet, set_id)
        if found is None:
            return cls("upload", set_id=set_id)
        version = found.uploaded_at.isoformat()
        if found.kind == "composite":
            gated = sources.is_gated(sources.set_source(found))
            return cls("composite", set_id=set_id, version=version, gated=gated)
        return cls("upload", set_id=set_id, version=version)


def default_source(
    session: Session, season: int, *, upload_owners: Iterable[str] = ()
) -> PoolSource:
    """The newest stored BBM capture for the season, else the newest uploaded
    set of this season owned by one of `upload_owners`, else ESPN's pool."""
    day = bbm_store.latest_capture(session, season)
    if day is not None:
        return PoolSource("bbm", captured_on=day)
    owners = list(upload_owners)
    if owners:
        newest = session.scalar(
            select(ProjectionSet.id)
            .where(ProjectionSet.season == season, ProjectionSet.owner.in_(owners))
            .order_by(ProjectionSet.uploaded_at.desc(), ProjectionSet.id.desc())
            .limit(1)
        )
        if newest is not None:
            return PoolSource.of_set(session, int(newest))
    return PoolSource("espn")


def bbm_input(session: Session, season: int, captured_on: dt.date) -> BBMInput:
    """BBM's rows as the store kept them on one day, both value types."""
    total, columns = bbm_store.stored_records(session, season, captured_on, "total")
    if not total:
        raise RoomError(f"no Basketball Monster rows are stored for {season} on {captured_on}")
    rows = parse_records(total, columns, label=f"capture of {captured_on}")
    per_game_rows = None
    day = bbm_store.latest_capture(session, season, captured_on, "pergame")
    if day is not None:
        per_game, per_columns = bbm_store.stored_records(session, season, day, "pergame")
        if per_game:
            per_game_rows = parse_records(per_game, per_columns, label=f"capture of {day}")
    return BBMInput(rows=rows, per_game=per_game_rows, detail=f"captured {_day(captured_on)}")


def source_facts(source: PoolSource, season: int, today: dt.date) -> dict[str, Any]:
    """What the plan says about its pool: which, from when, and whether it is old."""
    age = (today - source.captured_on).days if source.captured_on is not None else None
    stale = source.kind == "bbm" and age is not None and age > STALE_AFTER_DAYS
    return {
        "kind": source.kind,
        "tag": source.tag,
        "key": source.key,
        "captured_on": source.captured_on.isoformat() if source.captured_on else None,
        "age_days": age,
        "stale": stale,
        "refresh": REFRESH_COMMAND.format(season=season) if source.kind == "bbm" else None,
        "gated": sources.is_gated(source.tag),
    }


def _day(day: dt.date) -> str:
    return f"{day:%b} {day.day}"


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One of the model's lists: its rule in words, its men in order, and why each."""

    key: str
    label: str
    rule: str
    ids: list[int]
    reasons: dict[int, str]


@dataclass(frozen=True)
class MustLock:
    """The manager's must-have men, and what fixing them in costs.

    `prices` is what each is bought at in the plan: the manager's going
    price for him when he set one, else the model's. `cost` is the best roster with the
    whole set against the free best roster, in categories a week; `each` the
    same for each man alone (the fan section's figure). `error` is set, and
    the plan was built free, when the set does not fit.
    """

    prices: dict[int, int]
    cost: float | None = None
    each: dict[int, float] = field(default_factory=dict)
    error: str | None = None
    #: The places and money left once the set is bought, and the ladder refit to them.
    places_left: int = 0
    money_left: int = 0
    ladder_after: list[int] = field(default_factory=list)
    roster_with: list[dict[str, Any]] = field(default_factory=list)
    roster_free: list[dict[str, Any]] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return bool(self.prices) and self.error is None


@dataclass
class Plan:
    """Everything a plan is. `script_json` is the old file; `payload` the route's."""

    season: int
    espn_team_id: int
    team: str
    draft_at: dt.datetime | None
    nominate: int | None
    fan_team: str | None
    teams: int
    budget: int
    roster_slots: int
    minimum_bid: int
    allocation: list[int]
    slack: float
    cap: int | None
    opponent: dict[str, dict[str, float]]
    pool_note: str
    source: PoolSource
    source_detail: str
    source_note: str
    #: One row per considered player, in the script's own shape.
    players: list[dict[str, Any]]
    fan: list[dict[str, Any]]
    builds: dict[str, Any]
    #: Each pool player's nine against this league's targets (`nine_against_targets`).
    nine: dict[int, dict[str, float]]
    sections: list[Section]
    must: MustLock | None
    #: Pool players below `CONSIDER_FROM`: no ceiling was worked out for them.
    rest: list[dict[str, Any]]
    #: How the nine were worked out: the par line per category.
    par: dict[str, dict[str, float]]
    #: The model's best roster (the balanced build), with ids.
    best: dict[str, Any] = field(default_factory=dict)
    #: The room the plan was built in, and its state once the must men are
    #: bought: what the manager's own prices are re-solved in. Not written.
    room: Room | None = field(default=None, repr=False)
    plan_state: DraftState | None = field(default=None, repr=False)
    exported: str = ""
    generated: str = ""
    build_seconds: float | None = None

    def script_json(self) -> dict[str, Any]:
        """Exactly what `scripts/draft_plan.py` has always written."""
        out: dict[str, Any] = {
            "season": self.season,
            "draft_at": self.draft_at.isoformat() if self.draft_at else None,
            "nominate": self.nominate,
            "fan_team": self.fan_team,
            "fan": self.fan,
            "team": self.team,
            "teams": self.teams,
            "budget": self.budget,
            "roster_slots": self.roster_slots,
            "allocation": self.allocation,
            "cap": self.cap,
            "opponent": self.opponent,
            "pool": self.pool_note,
            "source": self.source.tag,
            "source_note": self.source_note,
            "players": self.players,
            "builds": self.builds,
        }
        out["exported"] = self.exported
        out["generated"] = self.generated
        return out

    def payload(self, today: dt.date) -> dict[str, Any]:
        """The plan as the route serves it: the model's figures, never the marks."""
        in_section: dict[int, list[str]] = {}
        for section in self.sections:
            for player_id in section.ids:
                in_section.setdefault(player_id, []).append(section.key)
        players = [self._row(p, in_section.get(p["id"], []), considered=True) for p in self.players]
        rest = [self._row(p, [], considered=False) for p in self.rest]
        return {
            "season": self.season,
            "espn_team_id": self.espn_team_id,
            "team": self.team,
            "auction_at": self.draft_at.isoformat() if self.draft_at else None,
            "facts": {
                "pot": self.teams * self.budget,
                "teams": self.teams,
                "budget": self.budget,
                "places": self.roster_slots,
                "floor": self.minimum_bid,
                "nominate": self.nominate,
                "cap": self.cap,
                "slack": self.slack,
            },
            "ladder": self.allocation,
            "source": {
                **source_facts(self.source, self.season, today),
                "detail": self.source_detail,
                "note": self.source_note,
                "pool": self.pool_note,
            },
            "players": players,
            "rest": rest,
            "sections": [
                {
                    "key": s.key,
                    "label": s.label,
                    "rule": s.rule,
                    "ids": s.ids,
                    "reasons": {str(k): v for k, v in s.reasons.items()},
                }
                for s in self.sections
            ],
            "fan_team": self.fan_team,
            "fan": self.fan,
            "must": _must_json(self.must),
            "builds": self.builds,
            "best": self.best,
            "targets": self.opponent,
            "par": self.par,
            "consider_from": CONSIDER_FROM,
            "generated": self.generated,
            "build_seconds": self.build_seconds,
        }

    def _row(
        self, p: dict[str, Any], sections_in: list[str], *, considered: bool
    ) -> dict[str, Any]:
        going = p.get("going")
        ceiling = p.get("ceiling")
        bbm_total = p.get("bbm_total")
        warning = bargain_warning(going, ceiling, bbm_total) if considered else None
        return {
            **p,
            "considered": considered,
            "bid_to": bid_to(p) if considered else None,
            "check_news": warning,
            "nine": self.nine.get(p["id"], {}),
            "sections": sections_in,
            "must_price": self.must.prices.get(p["id"])
            if self.must is not None and self.must.applied
            else None,
        }


def _must_json(lock: MustLock | None) -> dict[str, Any] | None:
    if lock is None:
        return None
    return {
        "prices": {str(k): v for k, v in lock.prices.items()},
        "applied": lock.applied,
        "error": lock.error,
        "cost": lock.cost,
        "each": {str(k): v for k, v in lock.each.items()},
        "places_left": lock.places_left,
        "money_left": lock.money_left,
        "ladder_after": lock.ladder_after,
        "roster_with": lock.roster_with,
        "roster_free": lock.roster_free,
    }


def bid_to(p: Mapping[str, Any]) -> int:
    """The model's bid-up-to: the lower of our ceiling and BBM's league value.

    The old page's `bidTo`, word for word: no BBM value leaves the ceiling;
    never below a dollar.
    """
    ceiling = p.get("ceiling")
    c = ceiling if ceiling is not None else 0
    total = p.get("bbm_total")
    b = total if total is not None else c
    return max(1, min(c, b))


# ---------------------------------------------------------------------------
# building it
# ---------------------------------------------------------------------------

#: Ceilings for many players at once: (state, player ids) -> {id: ceiling}.
CeilingRunner = Callable[[Room, DraftState, Sequence[int]], dict[int, Ceiling]]


def ceilings_in_workers(
    workers: int, progress: Callable[[str], None] | None = None
) -> CeilingRunner:
    """The script's way: a pool of worker processes, each holding the room once."""
    from app.draft.session import _compute, process_executor

    def run(room: Room, state: DraftState, ids: Sequence[int]) -> dict[int, Ceiling]:
        out: dict[int, Ceiling] = {}
        with process_executor(room, workers) as executor:
            futures = {executor.submit(_compute, state, pid): pid for pid in ids}
            for done, future in enumerate(as_completed(futures), 1):
                out[futures[future]] = future.result()
                if progress is not None and done % 25 == 0:
                    progress(f"  {done}/{len(futures)}")
        return out

    return run


def ceilings_inline(room: Room, state: DraftState, ids: Sequence[int]) -> dict[int, Ceiling]:
    """The same ceilings on this thread: for a small room, and the tests."""
    from app.draft.room import bid_ceiling

    return {
        pid: bid_ceiling(
            state,
            pid,
            room.candidates,
            room.distributions,
            punt=room.punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=room.restarts,
            allocation=room.allocation,
        )
        for pid in ids
    }


def with_top_place(room: Room, top: int) -> Room:
    """The room with its plan's first place forced to `top`, for a what-if.

    The other places keep history's proportions, fitted to what is left of
    the budget, so the plan still spends every dollar and no place falls
    below the minimum bid.
    """
    if room.allocation is None:
        return room
    state = room.state
    rest = list(room.allocation.places[1:])
    floor = state.minimum_bid
    top = max(floor, min(top, state.budget - floor * len(rest)))
    places = (top, *_fit(rest, state.budget - top, floor))
    return replace(room, allocation=Allocation(places, room.allocation.slack))


def load_plan_room(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int | str,
    source: PoolSource,
    *,
    restarts: int = RESTARTS,
) -> tuple[Room, str]:
    """The room a plan is built in, and the detail its source line names."""
    bbm = None
    projection_set = None
    if source.kind == "bbm":
        if source.files is not None:
            bbm = BBMInput.from_files(source.files[0], source.files[1])
        elif source.captured_on is not None:
            bbm = bbm_input(session, int(league_season.season), source.captured_on)
        else:
            raise RoomError("a BBM plan needs a stored capture or the export files")
    elif source.kind in ("upload", "composite"):
        projection_set = source.set_id
    room = room_for(
        session,
        league_season,
        espn_team_id,
        pool_season=None,
        pool_kind="projected",
        punt=[],
        restarts=restarts,
        bbm=bbm,
        projection_set=projection_set,
        plan="history",
    )
    return room, room.source_detail


def build_plan(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int | str,
    *,
    source: PoolSource,
    fan_team: str | None = None,
    must: Mapping[int, int | None] | None = None,
    workers: int | None = None,
    restarts: int = RESTARTS,
    top_place: int | None = None,
    ceilings: CeilingRunner | None = None,
    progress: Callable[[str], None] | None = None,
    today: dt.date | None = None,
) -> Plan:
    """The whole plan for one team, on one pool.

    `must` is the manager's must-have men, each at his own going price for
    the man (None: the model's). `top_place` is the script's what-if on the ladder's
    first place. `ceilings` runs the ceilings (worker processes by default).
    """
    started = time.monotonic()
    room, detail = load_plan_room(session, league_season, espn_team_id, source, restarts=restarts)
    if top_place is not None:
        room = with_top_place(room, top_place)
        if progress is not None:
            progress(
                f"what-if: top place ${top_place}, cap ${room.allocation.cap(room.state)}"
                if room.allocation
                else "what-if: no allocation to change"
            )
    plan = plan_from_room(
        room,
        draft_at=league_season.drafted_at,
        order=list(league_season.draft_order or []),
        source=source,
        source_detail=detail,
        fan_team=fan_team,
        must=must,
        ceilings=ceilings or ceilings_in_workers(workers or default_workers(), progress),
        progress=progress,
        today=today,
    )
    plan.build_seconds = round(time.monotonic() - started, 1)
    return plan


def plan_from_room(
    room: Room,
    *,
    draft_at: dt.datetime | None,
    order: Sequence[int],
    source: PoolSource,
    source_detail: str,
    fan_team: str | None,
    must: Mapping[int, int | None] | None,
    ceilings: CeilingRunner,
    progress: Callable[[str], None] | None = None,
    today: dt.date | None = None,
) -> Plan:
    """The plan from a loaded room: everything `build_plan` does after loading."""
    say = progress or (lambda _: None)
    state = room.state
    market = market_prices(room, state)

    # League-standard category values, for each player's profile. Valued on
    # the pool the room was loaded from, whichever source that is.
    profile = {
        v.player_id: {c.abbreviation: round(c.value, 2) for c in v.categories}
        for v in value_players(room.projections, list(CATEGORIES))
    }

    considered: list[Candidate] = []
    rest: list[Candidate] = []
    for c in room.candidates:
        row = room.bbm.get(c.player_id)
        total = row.league_dollars if row and row.league_dollars is not None else None
        going = market.get(c.player_id, (None, ""))[0] or 0
        fan = fan_team is not None and row is not None and row.team == fan_team
        if going >= CONSIDER_FROM or (total or 0) >= CONSIDER_FROM or (fan and (total or 0) >= 1):
            considered.append(c)
        else:
            rest.append(c)

    lock = must_lock(room, must, progress=say) if must else None
    plan_state = lock_state(room, lock) if lock is not None and lock.applied else state
    locked_ids = set(lock.prices) if lock is not None and lock.applied else set()
    say(f"{len(considered)} players considered; computing ceilings")
    ceiling_of = ceilings(
        room, plan_state, [c.player_id for c in considered if c.player_id not in locked_ids]
    )

    players = [
        _player_row(room, c, ceiling_of.get(c.player_id), market, profile) for c in considered
    ]
    rest_rows = [_player_row(room, c, None, market, profile) for c in rest]

    builds, balanced = _builds(room, plan_state)
    say("builds: " + ", ".join(f"{k} {v['all_nine']}" for k, v in builds.items()))

    fan_rows: list[dict[str, Any]] = []
    if fan_team is not None:
        base = resolve(
            plan_state,
            room.candidates,
            room.distributions,
            lineup=room.lineup,
            limits=room.limits,
            restarts=BUILD_RESTARTS,
            allocation=room.allocation,
        )
        for c in considered:
            row = room.bbm.get(c.player_id)
            if row is None or row.team != fan_team:
                continue
            cost, with_him = lock_cost(room, plan_state, base, {c.player_id: c.price})
            fan_rows.append(
                {
                    "id": c.player_id,
                    "cost": cost,
                    "in_best": c.player_id in base.player_ids,
                    "roster_with": [
                        {"name": p.name, "price": p.price}
                        for p in sorted(with_him.players, key=lambda p: -p.price)
                    ],
                }
            )
        say(f"{len(fan_rows)} {fan_team} players costed")

    allocation = room.allocation
    nominate = order.index(state.me) + 1 if state.me in order else None
    nine, par = nine_against_targets(room)
    return Plan(
        season=room.season,
        espn_team_id=state.me,
        team=room.team_names.get(state.me, str(state.me)),
        draft_at=draft_at,
        nominate=nominate,
        fan_team=fan_team,
        teams=len(state.teams),
        budget=state.budget,
        roster_slots=state.roster_slots,
        minimum_bid=state.minimum_bid,
        allocation=list(allocation.places) if allocation else [],
        slack=allocation.slack if allocation else 0.0,
        cap=allocation.cap(state) if allocation else None,
        opponent={
            d.abbreviation: {"mean": round(d.mean, 3), "spread": round(d.spread, 3)}
            for d in room.distributions
        },
        pool_note=room.pool_note,
        source=source,
        source_detail=source_detail,
        # The page's one line about where its numbers came from. `exported`
        # is when the BBM exports were pulled, which the room cannot know;
        # otherwise the room's own detail (the export, the capture, or the
        # uploaded set's name and note) says it.
        source_note=describe(
            room.projection_source,
            f"pulled {source.exported}" if source.exported else room.source_detail,
        ),
        players=players,
        fan=fan_rows,
        builds=builds,
        nine=nine,
        sections=sections(players, fan_rows, fan_team=fan_team, has_bbm=bool(room.bbm)),
        must=lock,
        rest=rest_rows,
        par=par,
        best=best_json(balanced, room.distributions),
        room=room,
        plan_state=plan_state,
        exported=source.exported,
        generated="computed " + (today or dt.date.today()).strftime("%-d %b %Y"),
    )


def _player_row(
    room: Room,
    c: Candidate,
    ceiling: Ceiling | None,
    market: Mapping[int, tuple[int | None, str]],
    profile: Mapping[int, dict[str, float]],
) -> dict[str, Any]:
    """One player as the script has always written him."""
    row = room.bbm.get(c.player_id)
    priced, going_source = market.get(c.player_id, (0, ""))
    total = row.league_dollars if row and row.league_dollars is not None else None
    per_game = room.per_game_dollars.get(c.player_id)
    return {
        "id": c.player_id,
        "name": c.name,
        "position": c.position,
        "eligible": sorted(e for e in c.eligible if e in ("PG", "SG", "SF", "PF", "C")),
        "going": priced or 0,
        "going_source": going_source,
        "board": room.board.get(c.player_id, c.price),
        "ceiling": ceiling.price if ceiling is not None else None,
        "capped": ceiling.capped if ceiling is not None else False,
        "marginal": (
            None
            if ceiling is None or ceiling.marginal_at_floor == float("-inf")
            else round(ceiling.marginal_at_floor, 3)
        ),
        "bbm_total": None if total is None else round(total),
        "bbm_per_game": None if per_game is None else round(per_game),
        "espn_avg": _rounded(row.espn_dollars if row else None),
        "yahoo_avg": _rounded(row.yahoo_dollars if row else None),
        "age": None if not row or row.age is None else round(row.age, 1),
        "games": None if not row else round(row.games),
        "injury_risk": (row.injury_risk or None) if row else None,
        "injury": (row.injury or None) if row else None,
        "nba_team": (row.team or None) if row else None,
        "confidence": row.confidence if row else None,
        "role": (ROLES.get(row.role, row.role) or None) if row else None,
        "status": list(row.status) if row else [],
        "tags": list(row.tags) if row else [],
        "note": (row.note or None) if row else None,
        "note_by": (row.note_by or None) if row else None,
        "profile": profile.get(c.player_id, {}),
    }


def _rounded(value: float | None) -> int | None:
    return None if value is None else round(value)


def alternative_builds(room: Room, state: DraftState) -> dict[str, Any]:
    """The model's best rosters from this room, and three alternatives.

    Each is scored on all nine categories with the concede penalty
    (`all_nine`), so a build that punts by choice is compared on what it will
    actually win, not on the categories it stopped counting.
    """
    return _builds(room, state)[0]


def _builds(room: Room, state: DraftState) -> tuple[dict[str, Any], RosterPlan]:
    """`alternative_builds`, and the balanced build's roster itself."""
    categories = [d.abbreviation for d in room.distributions]
    risky = [
        c.player_id
        for c in room.candidates
        if (row := room.bbm.get(c.player_id)) is not None and row.injury_risk in ("H", "E")
    ]
    variants: tuple[tuple[str, tuple[str, ...], list[int]], ...] = (
        ("balanced", (), []),
        ("no high injury risk", (), risky),
        ("punt TO", ("TO",), []),
        ("punt FT%", ("FT%",), []),
    )
    builds = {}
    balanced: RosterPlan | None = None
    for label, punt, exclude in variants:
        plan = resolve(
            state,
            room.candidates,
            room.distributions,
            punt=punt,
            lineup=room.lineup,
            limits=room.limits,
            restarts=BUILD_RESTARTS,
            allocation=room.allocation,
            exclude=exclude,
        )
        if label == "balanced":
            balanced = plan
        all_nine, _ = score(roster_totals(plan.players, categories), room.distributions)
        builds[label] = {
            "expected_wins": round(plan.expected_wins, 2),
            "all_nine": round(all_nine, 2),
            "punt": list(punt),
            "excluded": len(exclude),
            "cost": plan.cost,
            "win_probability": {k: round(v, 2) for k, v in plan.win_probability.items()},
            "roster": [
                {
                    "name": p.name,
                    "price": p.price,
                    "position": p.position,
                    "injury_risk": (
                        room.bbm[p.player_id].injury_risk or None
                        if p.player_id in room.bbm
                        else None
                    ),
                }
                for p in sorted(plan.players, key=lambda p: -p.price)
            ],
        }
    assert balanced is not None
    return builds, balanced


def best_json(plan: RosterPlan, distributions: Sequence[Any]) -> dict[str, Any]:
    """A best roster as the plan carries it: the men with their ids and prices,
    what it is expected to win, and on all nine."""
    categories = [d.abbreviation for d in distributions]
    all_nine, _ = score(roster_totals(plan.players, categories), distributions)
    return {
        "roster": _roster(plan),
        "cost": plan.cost,
        "expected_wins": round(plan.expected_wins, 3),
        "all_nine": round(all_nine, 3),
        "win_probability": {k: round(v, 3) for k, v in plan.win_probability.items()},
    }


def lock_cost(
    room: Room, state: DraftState, base: RosterPlan, lock: Mapping[int, int]
) -> tuple[float, RosterPlan]:
    """What fixing `lock` into the roster gives up, in categories a week.

    The best roster that has these men at these prices, warm-started from
    the best roster with no such requirement (`base`), against `base`. The
    fan section's arithmetic, and the must set's: one function, so a one-man
    must set costs exactly what the fan section says he costs.
    """
    with_them = resolve(
        state,
        room.candidates,
        room.distributions,
        lineup=room.lineup,
        limits=room.limits,
        restarts=BUILD_RESTARTS,
        allocation=room.allocation,
        lock=dict(lock),
        starts=[tuple(base.player_ids)],
    )
    return round(max(0.0, base.expected_wins - with_them.expected_wins), 3), with_them


def _roster(plan: RosterPlan) -> list[dict[str, Any]]:
    return [
        {"id": p.player_id, "name": p.name, "price": p.price}
        for p in sorted(plan.players, key=lambda p: -p.price)
    ]


def must_lock(
    room: Room,
    must: Mapping[int, int | None],
    *,
    progress: Callable[[str], None] | None = None,
) -> MustLock:
    """The must set priced, checked against the budget, and costed.

    Each man is bought at the manager's going price for him when he set one,
    else at the model's going price (the price the model plans him at). The
    set fits when every man is in the pool, the places hold them, and their
    prices leave at least the minimum bid for every place still open; otherwise the answer carries a
    sentence naming the shortfall, and nothing is costed.
    """
    state = room.state
    by_id = {c.player_id: c for c in room.candidates}
    prices: dict[int, int] = {}
    unknown = []
    for player_id, price in must.items():
        c = by_id.get(player_id)
        if c is None:
            unknown.append(player_id)
            continue
        prices[player_id] = int(price) if price is not None else int(c.price)
    if unknown:
        return MustLock(
            prices=prices,
            error=(
                f"{len(unknown)} of the must men {'is' if len(unknown) == 1 else 'are'} not in "
                "the pool this plan is built on, so the plan is the free build."
            ),
        )
    error = must_shortfall(prices, state)
    if error is not None:
        return MustLock(prices=prices, error=error)
    locked = lock_state(room, MustLock(prices=prices))
    base = resolve(
        state,
        room.candidates,
        room.distributions,
        lineup=room.lineup,
        limits=room.limits,
        restarts=BUILD_RESTARTS,
        allocation=room.allocation,
    )
    cost, with_them = lock_cost(room, state, base, prices)
    each = {pid: lock_cost(room, state, base, {pid: price})[0] for pid, price in prices.items()}
    if progress is not None:
        progress(f"must set of {len(prices)}: costs {cost} categories a week")
    ladder_after = list(room.allocation.open_places(locked)) if room.allocation else []
    return MustLock(
        prices=prices,
        cost=cost,
        each=each,
        places_left=locked.mine.open_slots,
        money_left=locked.mine.remaining,
        ladder_after=ladder_after,
        roster_with=_roster(with_them),
        roster_free=_roster(base),
    )


def must_shortfall(prices: Mapping[int, int], state: DraftState) -> str | None:
    """Why a must set does not fit, in one sentence, or None when it does."""
    count = len(prices)
    if count > state.roster_slots:
        return (
            f"{count} must men is more than the {state.roster_slots} places on a roster, "
            "so the plan is the free build."
        )
    total = sum(prices.values())
    places_left = state.roster_slots - count
    needed = total + places_left * state.minimum_bid
    if needed > state.budget:
        return (
            f"The must men cost ${total} together, and the {places_left} places left need "
            f"${places_left * state.minimum_bid} at ${state.minimum_bid} each: "
            f"${needed - state.budget} over the ${state.budget} budget, so the plan is the "
            "free build."
        )
    if any(price < state.minimum_bid for price in prices.values()):
        return f"A must man's price is under the ${state.minimum_bid} minimum bid."
    return None


def lock_state(room: Room, lock: MustLock) -> DraftState:
    """The empty room with our must men already bought, dearest first."""
    state = room.state
    for player_id, price in sorted(lock.prices.items(), key=lambda item: -item[1]):
        try:
            state = state.apply(Pick(player_id, state.me, price))
        except DraftError as error:  # must_shortfall is checked first; say so if not
            raise RoomError(str(error)) from error
    return state


# ---------------------------------------------------------------------------
# the nine, against this league's targets
# ---------------------------------------------------------------------------


def nine_against_targets(
    room: Room,
) -> tuple[dict[int, dict[str, float]], dict[str, dict[str, float]]]:
    """Each man's nine as a change in the chance of beating this league's opponent.

    The par roster is `roster_slots` men who together post exactly the
    league's median opponent (`app.draft.targets.category_distributions`, the
    mean the optimizer plays against): a counting category's mean shared
    evenly, and a percentage at the opponent's rate on the attempts an
    average rostered man takes (the top teams x places of the pool by going
    price). Against that opponent the par roster wins every category half the
    time. Swap one par man for him and read the chance again: the difference,
    in probability, is his cell. The same `win_probability` the optimizer
    scores rosters with, so the glyph is the model's own arithmetic.
    """
    state = room.state
    n = max(1, state.roster_slots)
    rostered = sorted(room.candidates, key=lambda c: -c.price)[: n * max(1, len(state.teams))]
    par_attempts = {
        attempted: (fmean(c.weekly.get(attempted, 0.0) for c in rostered) if rostered else 0.0)
        for _, attempted in PERCENTAGE_COMPONENTS.values()
    }
    par: dict[str, dict[str, float]] = {}
    for d in room.distributions:
        if d.abbreviation in PERCENTAGE_COMPONENTS:
            _, attempted = PERCENTAGE_COMPONENTS[d.abbreviation]
            par[d.abbreviation] = {
                "rate": round(d.mean, 4),
                "attempts": round(par_attempts[attempted], 2),
                "spread": round(d.spread, 4),
            }
        else:
            par[d.abbreviation] = {"per_man": round(d.mean / n, 2), "spread": round(d.spread, 2)}
    out: dict[int, dict[str, float]] = {}
    for c in room.candidates:
        cells: dict[str, float] = {}
        for d in room.distributions:
            if d.abbreviation in PERCENTAGE_COMPONENTS:
                made_key, attempted_key = PERCENTAGE_COMPONENTS[d.abbreviation]
                par_a = par_attempts[attempted_key]
                attempts = (n - 1) * par_a + c.weekly.get(attempted_key, 0.0)
                made = (n - 1) * par_a * d.mean + c.weekly.get(made_key, 0.0)
                total = made / attempts if attempts else d.mean
            else:
                total = d.mean * (n - 1) / n + c.weekly.get(d.abbreviation, 0.0)
            cells[d.abbreviation] = round(d.win_probability(total) - 0.5, 4)
        out[c.player_id] = cells
    return out, par


# ---------------------------------------------------------------------------
# the model's lists
# ---------------------------------------------------------------------------


def _money(value: int | None) -> str:
    return "no figure" if value is None else f"${value}"


def _z(p: Mapping[str, Any], key: str) -> float:
    return float((p.get("profile") or {}).get(key) or 0.0)


def _flag_kind(p: Mapping[str, Any]) -> int:
    """BBM's flags, good news first: breakout or sleeper, then the uncertain, then bust."""
    text = " ".join(p.get("tags") or []).lower()
    if "bust" in text or "tank" in text:
        return 2
    if "breakout" in text or "sleeper" in text:
        return 0
    return 1


def sections(
    players: Sequence[Mapping[str, Any]],
    fan: Sequence[Mapping[str, Any]],
    *,
    fan_team: str | None,
    has_bbm: bool,
) -> list[Section]:
    """The model's lists over the considered players, each with its rule.

    The rules are the old page's, moved here: a section's rule text says
    exactly what it filters on. On a pool with no BBM values the lists that
    need BBM (its flags, its likes, the IR stash on its per-game value) are
    left out, and targets and let go are read on our ceiling alone.
    """
    out: list[Section] = []
    by_id = {p["id"]: p for p in players}

    def going(p: Mapping[str, Any]) -> int:
        return int(p.get("going") or 0)

    def ceiling(p: Mapping[str, Any]) -> int:
        return int(p.get("ceiling") or 0)

    def bbm(p: Mapping[str, Any]) -> int:
        return int(p.get("bbm_total") or 0)

    stars = sorted((p for p in players if going(p) >= 40), key=lambda p: -going(p))
    reasons = {}
    for p in stars:
        to = bid_to(p)
        if going(p) <= to:
            reasons[p["id"]] = f"going {_money(going(p))}, inside the model's {_money(to)}"
        else:
            reasons[p["id"]] = (
                f"going {_money(going(p))}, {_money(going(p) - to)} over the model's {_money(to)}"
            )
    out.append(
        Section(
            "stars",
            "Stars",
            "expected to go for $40 or more; the model's figure is the lower of our ceiling "
            "and BBM's league value",
            [p["id"] for p in stars],
            reasons,
        )
    )

    if fan_team is not None:
        rows = [(f, by_id[f["id"]]) for f in fan if f["id"] in by_id]
        rows.sort(key=lambda fp: (fp[0]["cost"], -going(fp[1])))
        reasons = {}
        for f, p in rows:
            if f["in_best"]:
                reasons[p["id"]] = f"in the model's best roster, ~{_money(going(p))}"
            else:
                reasons[p["id"]] = f"costs {f['cost']:.2f} categories a week at ~{_money(going(p))}"
        out.append(
            Section(
                "fan",
                fan_team,
                f"each {fan_team} man priced on the board: what the best roster gives up with "
                "him in it at his going price, in categories a week",
                [p["id"] for _, p in rows],
                reasons,
            )
        )

    if has_bbm:
        flagged = [
            p
            for p in players
            if going(p) >= 3
            and (
                (p.get("tags") or []) or (p.get("confidence") is not None and p["confidence"] <= 4)
            )
        ]
        flagged.sort(key=lambda p: (_flag_kind(p), -bid_to(p), -going(p)))
        reasons = {}
        for p in flagged:
            bits = list(p.get("tags") or [])
            if p.get("confidence") is not None and p["confidence"] <= 4:
                bits.append(f"confidence {p['confidence']}/10")
            reasons[p["id"]] = ", ".join(bits)
        out.append(
            Section(
                "flags",
                "BBM flags",
                "going for $3 or more and tagged by BBM's analysts, or a projection BBM rates "
                "4/10 or less: good news first, then the uncertain, then bust calls",
                [p["id"] for p in flagged],
                reasons,
            )
        )

    mid = [p for p in players if 8 <= going(p) < 40]
    if has_bbm:
        targets = [p for p in mid if ceiling(p) >= going(p) and bbm(p) >= going(p) + 5]
        targets.sort(key=lambda p: (-(bid_to(p) - going(p)), -going(p)))
        target_rule = (
            "$8 to $39, our ceiling at or over the going price and BBM's value $5+ over it"
        )
    else:
        targets = [p for p in mid if ceiling(p) >= going(p) + 5]
        targets.sort(key=lambda p: (-(ceiling(p) - going(p)), -going(p)))
        target_rule = "$8 to $39, our ceiling $5+ over the going price"
    out.append(
        Section(
            "targets",
            "Targets",
            target_rule,
            [p["id"] for p in targets],
            {
                p["id"]: f"going {_money(going(p))}, the model's {_money(bid_to(p))}"
                for p in targets
            },
        )
    )

    if has_bbm:
        likes = [p for p in mid if bbm(p) >= going(p) + 8 and ceiling(p) < going(p)]
        likes.sort(key=lambda p: -(bbm(p) - going(p)))
        out.append(
            Section(
                "bbm_likes",
                "BBM likes, we're lukewarm",
                "$8 to $39, BBM's value $8+ over the going price, our ceiling under it",
                [p["id"] for p in likes],
                {
                    p["id"]: f"BBM {_money(bbm(p))}, ours {_money(ceiling(p))}, going "
                    f"{_money(going(p))}"
                    for p in likes
                },
            )
        )

    if has_bbm:
        let_go = [
            p
            for p in players
            if going(p) >= 8 and ceiling(p) <= going(p) - 4 and bbm(p) <= going(p) - 5
        ]
        let_rule = "going $8+, our ceiling $4+ under the going price and BBM's value $5+ under it"
    else:
        let_go = [p for p in players if going(p) >= 8 and ceiling(p) <= going(p) - 4]
        let_rule = "going $8+, our ceiling $4+ under the going price"
    let_go.sort(key=lambda p: -going(p))
    out.append(
        Section(
            "let_go",
            "Let go",
            let_rule,
            [p["id"] for p in let_go],
            {
                p["id"]: f"going {_money(going(p))}, "
                f"{_money(going(p) - max(ceiling(p), bbm(p)))} over the value"
                for p in let_go
            },
        )
    )

    nominate = [p for p in let_go if going(p) >= 20]
    nominate.sort(key=lambda p: -(going(p) - bbm(p)))
    nominate = nominate[:10]
    out.append(
        Section(
            "nominate",
            "Nominate early",
            "from let go, going $20+: the most the room overpays by, ten at most",
            [p["id"] for p in nominate],
            {p["id"]: f"the room pays ~{_money(going(p))}" for p in nominate},
        )
    )

    late = [p for p in players if going(p) <= 7 and (_z(p, "STL") >= 1.4 or _z(p, "3PM") >= 1.6)]
    late.sort(key=lambda p: -(_z(p, "STL") + _z(p, "3PM")))
    late = late[:14]
    out.append(
        Section(
            "late",
            "Late steals & threes",
            "going $7 or less, strong in steals or threes; fourteen at most",
            [p["id"] for p in late],
            {
                p["id"]: f"~{_money(going(p))}, STL {_z(p, 'STL'):+.1f} 3PM {_z(p, '3PM'):+.1f}"
                for p in late
            },
        )
    )

    bigs = [p for p in players if going(p) <= 8 and _z(p, "REB") + _z(p, "BLK") >= 2.8]
    bigs.sort(key=lambda p: -(_z(p, "REB") + _z(p, "BLK")))
    bigs = bigs[:12]
    out.append(
        Section(
            "bigs",
            "Cheap bigs",
            "going $8 or less with real rebounds and blocks; twelve at most",
            [p["id"] for p in bigs],
            {
                p["id"]: (
                    f"~{_money(going(p))}, costs FT% ({_z(p, 'FT%'):+.1f})"
                    if _z(p, "FT%") <= -1.5
                    else f"~{_money(going(p))}, REB {_z(p, 'REB'):+.1f} BLK {_z(p, 'BLK'):+.1f}"
                )
                for p in bigs
            },
        )
    )

    if has_bbm:
        ir = [
            p
            for p in players
            if p.get("bbm_per_game") is not None
            and p.get("bbm_total") is not None
            and p["bbm_per_game"] - p["bbm_total"] >= 8
        ]
        ir.sort(key=lambda p: -(p["bbm_per_game"] - p["bbm_total"]))
        out.append(
            Section(
                "ir",
                "IR stash",
                "BBM's per-game value $8+ over its full-season value: a missed-games discount",
                [p["id"] for p in ir],
                {
                    p["id"]: f"{_money(p['bbm_per_game'])} a game against "
                    f"{_money(p['bbm_total'])} on the season"
                    for p in ir
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# your prices: the plan with the manager's own figures applied
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Override:
    """A man's figures of the manager's own: None is the model's."""

    going: int | None = None
    ceiling: int | None = None


#: Re-solves the best roster: (prices by id, ids to leave out, a roster to
#: start from) -> the roster as `best_json` writes it.
BestSolver = Callable[[Mapping[int, int], Sequence[int], Sequence[int]], dict[str, Any]]


def best_solver(room: Room, state: DraftState) -> BestSolver:
    """The best roster in this room, re-solved at other prices.

    The balanced build's own search (`BUILD_RESTARTS`), warm-started from
    the model's best roster, with the manager's going prices as those men's
    cost and his ceilings keeping out a man he will not pay the price for.
    About three seconds on the 2027 pool (docs/draft_plan.md).
    """

    def solve(
        prices: Mapping[int, int], exclude: Sequence[int], start: Sequence[int]
    ) -> dict[str, Any]:
        candidates = [
            replace(c, price=prices[c.player_id]) if c.player_id in prices else c
            for c in room.candidates
        ]
        plan = resolve(
            state,
            candidates,
            room.distributions,
            lineup=room.lineup,
            limits=room.limits,
            restarts=BUILD_RESTARTS,
            allocation=room.allocation,
            exclude=exclude,
            starts=[tuple(start)] if start else (),
        )
        return best_json(plan, room.distributions)

    return solve


def effective(
    payload: Mapping[str, Any],
    overrides: Mapping[int, Override],
    solve: BestSolver | None,
) -> dict[str, Any]:
    """The plan as the build reads it once the manager's figures are applied.

    Per man: his going price (the manager's when set), his ceiling (the
    lower of the model's and the manager's), the model's figure on that
    ceiling, which lists he is in, and whether he is in the best roster,
    each with where it came from. The lists are re-read over those figures
    (`sections`), and the best roster is re-solved at the manager's going
    prices with any man whose ceiling is under his going price left out.
    With no figure of the manager's the answer is the model's own, and no
    solve is run: the best roster is the model's balanced build.
    """
    considered = list(payload.get("players") or [])
    rows: list[dict[str, Any]] = []
    figures: dict[int, dict[str, Any]] = {}
    prices: dict[int, int] = {}
    exclude: list[int] = []
    count = 0
    for p in [*considered, *(payload.get("rest") or [])]:
        pid = int(p["id"])
        mine = overrides.get(pid, Override())
        going = mine.going if mine.going is not None else p.get("going")
        model_ceiling = p.get("ceiling")
        ceiling = model_ceiling
        if mine.ceiling is not None:
            ceiling = mine.ceiling if ceiling is None else min(ceiling, mine.ceiling)
        count += (mine.going is not None) + (mine.ceiling is not None)
        if mine.going is not None:
            prices[pid] = mine.going
        if mine.ceiling is not None and going is not None and mine.ceiling < going:
            exclude.append(pid)
        yours_capped = mine.ceiling is not None and (
            model_ceiling is None or mine.ceiling < model_ceiling
        )
        figures[pid] = {
            "going": going,
            "going_from": "yours" if mine.going is not None else "model",
            "ceiling": ceiling,
            "ceiling_from": "yours" if yours_capped else "model",
            "bid_to": (
                bid_to({"ceiling": ceiling, "bbm_total": p.get("bbm_total")})
                if ceiling is not None or p.get("considered")
                else None
            ),
            "sections": [],
            "in_best": False,
        }
        if p.get("considered"):
            rows.append({**p, "going": going, "ceiling": ceiling})
    lists = sections(
        rows,
        payload.get("fan") or [],
        fan_team=payload.get("fan_team"),
        has_bbm=any(p.get("bbm_total") is not None for p in considered),
    )
    for section in lists:
        for pid in section.ids:
            figures[pid]["sections"].append(section.key)
    model_best = dict(payload.get("best") or {})
    solved = bool(prices or exclude) and solve is not None
    if solve is not None and solved:
        start = [
            int(m["id"]) for m in model_best.get("roster") or [] if int(m["id"]) not in exclude
        ]
        best = solve(prices, exclude, start)
    else:
        best = model_best
    for man in best.get("roster") or []:
        if int(man["id"]) in figures:
            figures[int(man["id"])]["in_best"] = True
    return {
        "yours": count,
        "solved": solved,
        "players": {str(pid): f for pid, f in figures.items()},
        "sections": [
            {
                "key": s.key,
                "label": s.label,
                "rule": s.rule,
                "ids": s.ids,
                "reasons": {str(k): v for k, v in s.reasons.items()},
            }
            for s in lists
        ],
        "best": best,
    }
