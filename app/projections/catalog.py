"""Every pool a viewer may plan on, as the draft plan's SOURCES lists them.

One list, read by the SOURCES section (`GET /projections/sources`), the
plan's source chooser (`app.api.draft_plan`, `choices`) and the
co-manager's `draft_board`, so the three can never offer different pools:

- **Basketball Monster**, the newest stored capture for the season: only to
  the viewer who owns the captures (`owns_bbm`; the site's owner, whose
  membership pulls them), because BBM's numbers are paid and per user
  (`app.projections.sources.may_show`).
- **ESPN's projections** for the season: everyone's.
- **Each of his uploads**, by name: his own only.
- **Each of his composites**, by name (`app.projections.composite`): his own,
  and one that has BBM in its recipe only when he owns BBM too.

Each entry says what the grid shows: its kind, its season, how many rows,
how many matched to a player we hold and how many are on the board by name
only, when it was uploaded, captured or built, and by whom. Nothing here
reads a per-player number.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BBMCapture, PlayerSeasonStat, ProjectionRow, ProjectionSet, User
from app.draft import bbm_store
from app.projections import sources

#: The words each kind is called by on the page.
KIND_WORDS = {
    "bbm": "BBM capture",
    "espn": "ESPN",
    "upload": "upload",
    "composite": "composite",
}


@dataclass(frozen=True)
class Source:
    """One pool a viewer may plan on."""

    #: What the plan's `source` parameter is: "bbm", "espn", "upload:7", "composite:9".
    choice: str
    name: str
    kind: str
    season: int
    rows: int
    matched: int
    when: dt.datetime | dt.date | None
    by: str
    gated: bool
    note: str = ""
    set_id: int | None = None
    #: A composite's recipe and how many players each count of sources carried.
    recipe: list[dict[str, Any]] = field(default_factory=list)
    carried: dict[str, int] = field(default_factory=dict)

    @property
    def unmatched(self) -> int:
        return max(0, self.rows - self.matched)

    @property
    def short(self) -> str:
        """The chooser's label: BBM, ESPN, or the set's own name."""
        if self.kind == "bbm":
            return "BBM"
        if self.kind == "espn":
            return "ESPN"
        return self.name

    def as_json(self) -> dict[str, Any]:
        return {
            "source": self.choice,
            "name": self.name,
            "short": self.short,
            "kind": self.kind,
            "kind_words": KIND_WORDS.get(self.kind, self.kind),
            "season": self.season,
            "rows": self.rows,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "when": self.when.isoformat() if self.when is not None else None,
            "by": self.by,
            "gated": self.gated,
            "note": self.note,
            "set_id": self.set_id,
            "recipe": self.recipe,
            "carried": self.carried,
        }


def catalog(
    session: Session,
    season: int,
    *,
    owners: Iterable[str],
    owns_bbm: bool,
) -> list[Source]:
    """The pools this viewer may plan on for the season, BBM first, then
    ESPN, then his uploads and composites newest first."""
    out: list[Source] = []
    bbm = bbm_entry(session, season)
    if bbm is not None and sources.may_show(sources.BBM, viewer_owns_source=owns_bbm):
        out.append(bbm)
    out.append(espn_entry(session, season))
    out.extend(set_entries(session, season, owners=list(owners), owns_bbm=owns_bbm))
    return out


def bbm_entry(session: Session, season: int) -> Source | None:
    """The newest stored capture, or None when there is none for the season."""
    day = bbm_store.latest_capture(session, season)
    if day is None:
        return None
    log = session.scalar(
        select(BBMCapture).where(
            BBMCapture.season == season,
            BBMCapture.value_type == "total",
            BBMCapture.captured_on == day,
        )
    )
    versions = bbm_store.as_of(session, season, day)
    matched = sum(1 for version in versions if version.player_id is not None)
    return Source(
        choice="bbm",
        name=f"Basketball Monster, captured {day:%b} {day.day}",
        kind="bbm",
        season=season,
        rows=len(versions) or (log.players if log is not None else 0),
        matched=matched,
        when=day,
        by="the site's owner, whose membership pulls it",
        gated=True,
        note="paid; private to the member whose account fetched it",
    )


def espn_entry(session: Session, season: int) -> Source:
    """ESPN's own projections for the season: every row is one of ours."""
    rows = (
        session.scalar(
            select(func.count())
            .select_from(PlayerSeasonStat)
            .where(PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected")
        )
        or 0
    )
    return Source(
        choice="espn",
        name="ESPN's projections",
        kind="espn",
        season=season,
        rows=int(rows),
        matched=int(rows),
        when=None,
        by="ESPN, with the league's own ingest",
        gated=False,
    )


def set_entries(
    session: Session, season: int, *, owners: list[str], owns_bbm: bool
) -> list[Source]:
    """His uploads and composites for the season, newest first."""
    if not owners:
        return []
    found = list(
        session.scalars(
            select(ProjectionSet)
            .where(ProjectionSet.season == season, ProjectionSet.owner.in_(owners))
            .order_by(ProjectionSet.uploaded_at.desc(), ProjectionSet.id.desc())
        )
    )
    matched = _matched(session, [s.id for s in found])
    emails = _emails(session, [s.owner for s in found])
    out: list[Source] = []
    for one in found:
        tag = sources.set_source(one)
        if not sources.may_show(tag, viewer_owns_source=owns_bbm):
            continue
        out.append(
            Source(
                choice=sources.choice_of(tag),
                name=one.name,
                kind=one.kind,
                season=one.season,
                rows=one.rows,
                matched=matched.get(one.id, 0),
                when=one.uploaded_at,
                by=emails.get(one.owner, one.owner),
                gated=sources.is_gated(tag),
                note=one.source_note,
                set_id=one.id,
                recipe=list(one.recipe or []),
                carried=dict((one.built_from or {}).get("carried") or {}),
            )
        )
    return out


def _matched(session: Session, set_ids: list[int]) -> dict[int, int]:
    if not set_ids:
        return {}
    return {
        int(set_id): int(count)
        for set_id, count in session.execute(
            select(ProjectionRow.set_id, func.count())
            .where(ProjectionRow.set_id.in_(set_ids), ProjectionRow.player_id.is_not(None))
            .group_by(ProjectionRow.set_id)
        ).all()
    }


def _emails(session: Session, owners: list[str]) -> dict[str, str]:
    """An owner stored as a user id, as that user's email; a label as itself."""
    ids = {int(owner) for owner in owners if owner.isdigit()}
    if not ids:
        return {}
    return {
        str(user_id): str(email)
        for user_id, email in session.execute(
            select(User.id, User.email).where(User.id.in_(ids))
        ).all()
    }
