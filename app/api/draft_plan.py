"""The draft plan: the model's plan for one team's auction, and what the manager keeps of it.

    GET  /leagues/{l}/seasons/{s}/teams/{t}/draft/plan            the plan, with the marks
    PUT  /leagues/{l}/seasons/{s}/teams/{t}/draft/plan/marks      his figures, tags, ladder, notes
    POST /leagues/{l}/seasons/{s}/teams/{t}/draft/plan/rebuild    build the model's plan again
    GET  /leagues/{l}/seasons/{s}/teams/{t}/draft/plan/settings   his fan team
    PUT  /leagues/{l}/seasons/{s}/teams/{t}/draft/plan/settings   set it

All of them are the team layer (`require_team_plan`): this team's manager,
with a pass. docs/draft_plan.md has what each answers.

THE MODEL AND THE MANAGER, SIDE BY SIDE

The plan answer carries the model's figures per man (`plan.players`, as the
engine built them, never edited), the figures the build read once the
manager's own going prices and ceilings are applied (`effective`), and the
manager's marks (`marks`), each keyed by the man's id. Nothing is merged:
the page shows "the model says $46, you said $50" because it holds both.

THE SOURCE GATE

A plan built on Basketball Monster carries BBM's paid numbers per man, so it
is served only to the viewer who owns that source (`may_show`). The stored
captures are fetched with the site owner's BBM membership
(`scripts/bbm_pull.py`, `.env`), so the owner is the site's owner
(`Viewer.is_owner`). Anyone else asking for a BBM plan hears
`state: "withheld"`, a sentence naming the source, and the pools he may plan
on instead; nothing derived from BBM is in that answer.

A SEASON ALREADY DRAFTED

The plan is for a season whose auction is ahead (`app.inseason.drafted`).
For a drafted season the answer is `state: "drafted"` with the sentence
saying when it was held and the league's Draft page, where the board and
the grades are; no plan is built.

Nothing here bids, nominates or touches ESPN.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import reports
from app.api.access import Viewer, require_team_plan
from app.api.deps import LeagueIdPath, LeagueSeasonDep, SessionDep, TeamDep
from app.api.projections import DEFAULT_OWNER
from app.db.models import DraftPlan, DraftPlanMark, LeagueSeason, ProjectionSet, Team, TeamReport
from app.draft import bbm_store, plan_store
from app.draft import plan as engine
from app.draft.market import MINIMUM_BID
from app.draft.pool import roster_size_for
from app.inseason.drafted import season_is_drafted, when
from app.projections.sources import choice_of, describe, may_show

router = APIRouter(tags=["draft plan"])

TeamPlanViewer = Annotated[Viewer, Depends(require_team_plan)]

PLAN = "/leagues/{league_id}/seasons/{season}/teams/{team_id}/draft/plan"

#: What the page says when a BBM plan is not this viewer's to see.
WITHHELD = (
    "This plan is built on Basketball Monster's projections, which are paid and "
    "private to the member whose account fetched them. It can be built on another pool."
)


def viewer_owns_bbm(viewer: Viewer) -> bool:
    """Whether this viewer owns the stored BBM captures: the site's owner,
    whose membership `scripts/bbm_pull.py` signs in with."""
    return viewer.is_owner


def _upload_owners(viewer: Viewer) -> list[str]:
    """Whose uploaded sets are this viewer's (`app.api.projections._owns`)."""
    owners = [str(viewer.user_id)] if viewer.user_id is not None else []
    if viewer.is_owner or viewer.all_access:
        owners.append(DEFAULT_OWNER)
    return owners


def _source(
    session: Session, league_season: LeagueSeason, viewer: Viewer, asked: str | None
) -> engine.PoolSource:
    season = int(league_season.season)
    if not asked:
        return engine.default_source(session, season, upload_owners=_upload_owners(viewer))
    try:
        source = engine.PoolSource.parse(asked, session, season)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if source.kind == "upload":
        found = session.get(ProjectionSet, source.set_id)
        if found is None or (found.owner not in _upload_owners(viewer) and not viewer.all_access):
            raise HTTPException(status_code=404, detail=f"no projection set {source.set_id}")
        if found.season != season:
            raise HTTPException(
                status_code=422, detail=f"projection set {found.id} is for {found.season}"
            )
    return source


def _choices(session: Session, season: int, viewer: Viewer) -> list[dict[str, Any]]:
    """The pools this viewer may plan on: BBM's newest capture when it is his,
    his own uploaded sets, and ESPN's."""
    out: list[dict[str, Any]] = []
    day = bbm_store.latest_capture(session, season)
    if day is not None and viewer_owns_bbm(viewer):
        out.append({"source": "bbm", "label": f"Basketball Monster, captured {day:%b} {day.day}"})
    owners = _upload_owners(viewer)
    for found in session.scalars(
        select(ProjectionSet)
        .where(ProjectionSet.season == season, ProjectionSet.owner.in_(owners))
        .order_by(ProjectionSet.uploaded_at.desc())
    ):
        out.append({"source": f"upload:{found.id}", "label": f"your set: {found.name}"})
    out.append({"source": "espn", "label": "ESPN's projections"})
    return out


def _factory(session: Session) -> sessionmaker[Session]:
    """Sessions for a background build, on the request's own database."""
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


def _places(league_season: LeagueSeason) -> int:
    return roster_size_for(league_season)


def _max_bid(league_season: LeagueSeason) -> int:
    """The most one man can cost: the budget less a dollar for every other place."""
    return int(league_season.auction_budget) - (_places(league_season) - 1) * MINIMUM_BID


def _drafted(
    session: Session, league_season: LeagueSeason, league_id: int
) -> dict[str, Any] | None:
    drafted = season_is_drafted(session, league_season)
    if not drafted.drafted:
        return None
    return {
        "state": "drafted",
        "note": drafted.reason,
        "drafted_at": drafted.drafted_at.isoformat() if drafted.drafted_at else None,
        "draft_page": f"/l/{league_id}/{league_season.season}/draft",
    }


def _marks_json(row: DraftPlan | None) -> dict[str, Any]:
    marks = plan_store.marks_of(row)
    return {
        "marks": {str(pid): m.as_json() for pid, m in marks.items()},
        "ladder": list(row.ladder) if row is not None and row.ladder else None,
        "notes": row.notes if row is not None else "",
        "fan_team": row.fan_team if row is not None else None,
        "updated_at": plan_store.version_of(row) or None,
        "yours": sum(
            (m.going_price is not None) + (m.bid_up_to is not None) for m in marks.values()
        ),
    }


@router.get(PLAN, summary="The draft plan for one team's auction, with the manager's marks")
def get_plan(
    league_id: LeagueIdPath,
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    viewer: TeamPlanViewer,
    source: Annotated[
        str | None, Query(description="bbm, espn or upload:<set id>; default: the newest")
    ] = None,
) -> dict[str, Any]:
    """The plan: `state` is `ready`, `building`, `failed`, `withheld` or `drafted`.

    Ready carries the model's plan (`plan`), the plan with the manager's own
    figures applied (`effective`), his marks, and `rebuilding` when the
    model's plan shown is an older build while a new one is made.
    """
    held = _drafted(session, league_season, league_id)
    if held is not None:
        return held
    season = int(league_season.season)
    pool = _source(session, league_season, viewer, source)
    head: dict[str, Any] = {
        "season": season,
        "espn_team_id": int(team.espn_team_id),
        "team": str(team.name),
        "auction_at": league_season.drafted_at.isoformat() if league_season.drafted_at else None,
        "auction_when": when(league_season.drafted_at) if league_season.drafted_at else None,
        "source": {
            **engine.source_facts(pool, season, dt.date.today()),
            "name": describe(pool.tag),
            "choice": choice_of(pool.tag),
        },
        "choices": _choices(session, season, viewer),
    }
    if not may_show(pool.tag, viewer_owns_source=viewer_owns_bbm(viewer)):
        return {**head, "state": "withheld", "note": WITHHELD, "offer": "espn"}

    row = plan_store.plan_row(session, team)
    marks = plan_store.marks_of(row)
    fan_team = row.fan_team if row is not None else None
    must = plan_store.must_set(marks)
    key = plan_store.plan_key(pool, league_season, fan_team, must)
    kept = plan_store.stored(session, team, pool)
    build = plan_store.building(team, pool)
    fresh = kept is not None and kept.payload.get("key") == key
    if not fresh:
        build = plan_store.start(
            _factory(session), league_season, team, pool, key=key, fan_team=fan_team, must=must
        )
        if plan_store.SYNC:
            session.expire_all()
            kept = plan_store.stored(session, team, pool)
            fresh = kept is not None and kept.payload.get("key") == key
    expected = (
        kept.payload.get("build_seconds") if kept is not None else None
    ) or plan_store.COLD_BUILD_SECONDS
    if kept is None:
        if build is not None and build.error:
            return {**head, "state": "failed", "detail": build.error}
        return {
            **head,
            "state": "building",
            "since": build.started.isoformat() if build is not None else None,
            "expected_seconds": expected,
        }
    payload = kept.payload
    rebuilding = None
    if not fresh:
        rebuilding = {
            "since": build.started.isoformat() if build is not None else None,
            "expected_seconds": expected,
            "failed": build.error if build is not None else None,
            "why": _why_rebuilding(payload, key),
        }
    effective = plan_store.effective_for(
        session, league_season, team, pool, payload, marks, plan_store.version_of(row)
    )
    ladder = list(row.ladder) if row is not None and row.ladder else None
    return {
        **head,
        "state": "ready",
        "rebuilding": rebuilding,
        "built_at": payload.get("built_at"),
        "plan": payload,
        "effective": effective,
        **_marks_json(row),
        "cap": plan_store.cap_for(ladder) if ladder else payload["facts"].get("cap"),
        "max_bid": _max_bid(league_season),
    }


def _why_rebuilding(payload: dict[str, Any], key: str) -> str:
    was = str(payload.get("key") or "").split("|")
    now = key.split("|")
    if was[:1] != now[:1]:
        return "a newer pool"
    changed = {a.split("=")[0] for a, b in zip(was, now, strict=False) if a != b}
    if "must" in changed:
        return "your must-have men"
    if "fan" in changed:
        return "your fan team"
    return "the league's settings"


class MarkIn(BaseModel):
    """One man's marks. A field left out is left as it is; null clears it."""

    player_id: int
    going_price: int | None = Field(default=None, description="Your going price; null: the model's")
    bid_up_to: int | None = Field(default=None, description="Your ceiling; null: the model's")
    tag: Literal["target", "let_go", "nominate", "ir", "must", "none"] | None = None
    note: str | None = Field(default=None, max_length=500)


class MarksIn(BaseModel):
    """The manager's edits, whole or partial."""

    marks: list[MarkIn] = Field(default_factory=list, max_length=1000)
    ladder: list[int] | None = Field(
        default=None, description="One amount per place; null: the model's ladder"
    )
    notes: str | None = Field(default=None, max_length=5000)
    reset: Literal["figures"] | None = Field(
        default=None, description="'figures': every going price and ceiling back to the model's"
    )


@router.put(PLAN + "/marks", summary="Keep the manager's figures, tags, ladder and notes")
def put_marks(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    viewer: TeamPlanViewer,
    edits: MarksIn,
) -> dict[str, Any]:
    """Every write is checked first and nothing is kept unless all of it passes:
    a ladder adds up to the budget with at least the minimum bid a place; a
    ceiling is a whole number from $1 to the cap; a going price from $1 to
    the most one man can cost."""
    places = _places(league_season)
    budget = int(league_season.auction_budget)
    row = plan_store.plan_row(session, team, create=True)
    assert row is not None
    ladder = list(row.ladder) if row.ladder else None
    if "ladder" in edits.model_fields_set:
        if edits.ladder is not None:
            problem = plan_store.ladder_problem(edits.ladder, budget=budget, places=places)
            if problem is not None:
                raise HTTPException(status_code=422, detail=problem)
            ladder = sorted(edits.ladder, reverse=True)
        else:
            ladder = None
    cap = plan_store.cap_for(ladder) if ladder else _model_cap(session, team, league_season)
    most = _max_bid(league_season)
    for mark in edits.marks:
        sent = mark.model_fields_set
        if "bid_up_to" in sent and mark.bid_up_to is not None and not 1 <= mark.bid_up_to <= cap:
            raise HTTPException(
                status_code=422, detail=f"a ceiling is a whole number from $1 to the ${cap} cap"
            )
        if (
            "going_price" in sent
            and mark.going_price is not None
            and not 1 <= mark.going_price <= most
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"a going price is a whole number from $1 to ${most}, the most one man can cost"
                ),
            )
    by_player = {m.player_id: m for m in row.marks}
    for mark in edits.marks:
        kept = by_player.get(mark.player_id)
        if kept is None:
            kept = DraftPlanMark(player_id=mark.player_id, tag="none", note="")
            row.marks.append(kept)
            by_player[mark.player_id] = kept
        sent = mark.model_fields_set
        if "going_price" in sent:
            kept.going_price = mark.going_price
        if "bid_up_to" in sent:
            kept.bid_up_to = mark.bid_up_to
        if "tag" in sent:
            kept.tag = mark.tag or "none"
        if "note" in sent:
            kept.note = mark.note or ""
        kept.updated_at = dt.datetime.now(dt.UTC)
    if edits.reset == "figures":
        for kept in row.marks:
            kept.going_price = None
            kept.bid_up_to = None
    if "ladder" in edits.model_fields_set:
        row.ladder = ladder
    if "notes" in edits.model_fields_set:
        row.notes = edits.notes or ""
    row.updated_at = dt.datetime.now(dt.UTC)
    row.updated_by = viewer.user_id
    session.commit()
    session.refresh(row)
    return {"ok": True, **_marks_json(row), "cap": cap}


def _model_cap(session: Session, team: Team, league_season: LeagueSeason) -> int:
    """The model's cap from the newest stored build for this team, else the most
    one man can cost."""
    rows = session.scalars(
        select(TeamReport).where(
            TeamReport.team_id == team.id, TeamReport.kind == reports.DRAFT_PLAN
        )
    ).all()
    caps = [r.payload.get("facts", {}).get("cap") for r in rows]
    known = [int(c) for c in caps if c]
    return max(known) if known else _max_bid(league_season)


@router.post(PLAN + "/rebuild", summary="Build the model's plan again, now")
def rebuild(
    league_id: LeagueIdPath,
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    viewer: TeamPlanViewer,
    source: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """A fresh build on the same pool, even when the kept one is current, or
    after one failed. The page keeps showing the kept one until it lands."""
    held = _drafted(session, league_season, league_id)
    if held is not None:
        return held
    pool = _source(session, league_season, viewer, source)
    if not may_show(pool.tag, viewer_owns_source=viewer_owns_bbm(viewer)):
        raise HTTPException(status_code=403, detail=WITHHELD)
    row = plan_store.plan_row(session, team)
    fan_team = row.fan_team if row is not None else None
    must = plan_store.must_set(plan_store.marks_of(row))
    key = plan_store.plan_key(pool, league_season, fan_team, must)
    kept = plan_store.stored(session, team, pool)
    if kept is not None and kept.payload.get("key") == key:
        # Current, so a forced build is a new key for the same inputs.
        key = key + f"|again={dt.datetime.now(dt.UTC).isoformat()}"
    build = plan_store.start(
        _factory(session),
        league_season,
        team,
        pool,
        key=key,
        fan_team=fan_team,
        must=must,
        force=True,
    )
    return {"state": "building", "since": build.started.isoformat(), "error": build.error}


class PlanSettingsIn(BaseModel):
    fan_team: str | None = Field(
        default=None, description="An NBA team, as Basketball Monster abbreviates it; null: none"
    )


@router.get(PLAN + "/settings", summary="The manager's plan settings: his fan team")
def get_settings(team: TeamDep, session: SessionDep, viewer: TeamPlanViewer) -> dict[str, Any]:
    row = plan_store.plan_row(session, team)
    return {"fan_team": row.fan_team if row is not None else None, "teams": list(engine.NBA_TEAMS)}


@router.put(PLAN + "/settings", summary="Set the manager's fan team")
def put_settings(
    team: TeamDep, session: SessionDep, viewer: TeamPlanViewer, settings: PlanSettingsIn
) -> dict[str, Any]:
    """His fan team: the plan's FAN section costs each of its men. None by default."""
    fan = settings.fan_team.strip().upper() if settings.fan_team else None
    if fan is not None and fan not in engine.NBA_TEAMS:
        raise HTTPException(
            status_code=422, detail=f"{fan} is not an NBA team: {', '.join(engine.NBA_TEAMS)}"
        )
    row = plan_store.plan_row(session, team, create=True)
    assert row is not None
    row.fan_team = fan
    row.updated_at = dt.datetime.now(dt.UTC)
    row.updated_by = viewer.user_id
    session.commit()
    return {"fan_team": fan, "teams": list(engine.NBA_TEAMS)}
