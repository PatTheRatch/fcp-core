"""Uploading a manager's own projections, and reading a stored set back.

Basketball Monster's numbers are paid and stay with the member who fetched
them (docs/projection_sources.md), so this is the path by which anybody else
gets a board: his own CSV, .xlsx or .xls, from wherever he pays for it.
`scripts/upload_projections.py` does the same over a terminal, and these
routes keep both of its habits. Preview first: `POST /projections/sets/preview`
reads the file, reports the mapping it guessed and the basis it measured, and
stores nothing, so a mapping is confirmed before a board is built on it. And a
file whose columns cannot be used is refused with the reason (422) rather than
imported into a board that looks right and is not.

WHO MAY READ A SET

Nothing served here is gated. Only BBM's numbers are
(`app.projections.sources.is_gated`), and an uploaded set belongs to the
manager who uploaded it, so `may_show` can only answer yes. The question is
still asked, on every response that carries a set's numbers, because the
constraint document wants one check in the API layer rather than a rule people
remember. `ProjectionSet.owner` is the field that becomes that gate when
accounts land: today it is a label defaulting to "patrick", and the moment a
second person can sign in it is what `_readable` compares the caller against.
This module is the only place in the API that has to learn the answer.
"""

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep
from app.api.schemas import (
    ProjectionImportOut,
    ProjectionLineOut,
    ProjectionSetOut,
    RejectedRowOut,
)
from app.db.models import Player, ProjectionRow, ProjectionSet
from app.projections.sources import describe, may_show, upload_source
from app.projections.upload import (
    COUNTS,
    ImportReport,
    import_set,
    parse_overrides,
    stored_sets,
)

router = APIRouter(prefix="/projections", tags=["projections"])

#: Whose set it is, until accounts exist. The CLI defaults to the same name.
DEFAULT_OWNER = "patrick"

MAP_HELP = "Force one column, as 'Header=FIELD'. Repeat the field per column."
FILE_HELP = "The projections file: .csv, .xlsx or .xls, one row per player"
NAME_HELP = "What to call this set, e.g. 'Hashtag preseason'"
NOTE_HELP = "Where the numbers came from, in your own words"
OWNER_HELP = "Whose set it is; a label until accounts exist"

FileUpload = Annotated[UploadFile, File(description=FILE_HELP)]
SeasonForm = Annotated[int, Form(description="The season these project, e.g. 2027")]
NoteForm = Annotated[str, Form(description=NOTE_HELP)]
OwnerForm = Annotated[str, Form(description=OWNER_HELP)]
MapForm = Annotated[list[str] | None, Form(alias="map", description=MAP_HELP)]


@router.post(
    "/sets/preview",
    summary="Read a projections file, report how it was understood, store nothing",
)
def preview_set(
    session: SessionDep,
    file: FileUpload,
    season: SeasonForm,
    name: Annotated[str, Form(description=NAME_HELP)] = "preview",
    note: NoteForm = "",
    owner: OwnerForm = DEFAULT_OWNER,
    column_map: MapForm = None,
) -> ProjectionImportOut:
    """The mapping, the basis and the matching, before anything is stored.

    Never refuses a file: a mapping that cannot be used comes back with `ok`
    false and the reasons, which is the thing the caller uploaded a preview to
    find out. Correct it with `map` and preview again.
    """
    report = _run(
        session,
        file=file,
        season=season,
        name=name,
        note=note,
        owner=owner,
        column_map=column_map,
        dry_run=True,
    )
    return _import_out(report, file)


@router.post("/sets", summary="Store a projections file as a set the room can draft on")
def create_set(
    session: SessionDep,
    file: FileUpload,
    season: SeasonForm,
    name: Annotated[str, Form(description=NAME_HELP)],
    note: NoteForm = "",
    owner: OwnerForm = DEFAULT_OWNER,
    column_map: MapForm = None,
) -> ProjectionImportOut:
    """Store the set and report what was stored, or refuse the file and say why.

    A set is immutable once stored and a fresh upload is a fresh set, which is
    what makes "the board I drafted on" answerable next season.
    """
    report = _run(
        session,
        file=file,
        season=season,
        name=name,
        note=note,
        owner=owner,
        column_map=column_map,
        dry_run=False,
    )
    out = _import_out(report, file)
    if not report.ok:
        # The same refusal the CLI makes, for the same reason: a file carrying
        # a percentage and no attempts cannot be turned into a roster line at
        # all (app/scoring/lines.py). The whole report goes back, because the
        # mapping it guessed is what the caller needs to correct with `map`.
        session.rollback()
        raise HTTPException(status_code=422, detail=out.model_dump(mode="json"))
    session.commit()
    return out


@router.get("/sets", summary="Stored projection sets, newest first")
def list_sets(
    session: SessionDep,
    season: int | None = Query(default=None, description="Restrict to one season"),
) -> list[ProjectionSetOut]:
    """Bounded by how many files have been uploaded, so a plain list."""
    return [_set_out(stored) for stored in stored_sets(session, season) if _readable(stored)]


@router.get("/sets/{set_id}", summary="One stored set")
def get_set(set_id: int, session: SessionDep) -> ProjectionSetOut:
    return _set_out(_stored_set(session, set_id))


@router.get("/sets/{set_id}/rows", summary="A stored set's players, as per-game lines")
def list_set_rows(set_id: int, session: SessionDep) -> list[ProjectionLineOut]:
    """Every row in the set. Bounded by the upload, whose size `/sets/{id}` gives."""
    _stored_set(session, set_id)
    rows = session.execute(
        select(ProjectionRow, Player.espn_player_id)
        .outerjoin(Player, Player.id == ProjectionRow.player_id)
        .where(ProjectionRow.set_id == set_id)
        .order_by(ProjectionRow.name_key)
    ).all()
    return [
        ProjectionLineOut(
            name=row.name,
            espn_player_id=espn_player_id,
            team=row.team,
            position=row.position,
            games=row.games,
            per_game={key: float(getattr(row, column)) for key, column in COUNTS.items()},
        )
        for row, espn_player_id in rows
    ]


# ---------------------------------------------------------------------------
# running an import
# ---------------------------------------------------------------------------


def _run(
    session: Session,
    *,
    file: UploadFile,
    season: int,
    name: str,
    note: str,
    owner: str,
    column_map: list[str] | None,
    dry_run: bool,
) -> ImportReport:
    """`import_set` over an upload, with the caller's mistakes turned into 422s."""
    try:
        overrides = parse_overrides(column_map or [])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        with _as_path(file) as path:
            return import_set(
                session,
                season=season,
                name=name,
                owner=owner,
                source_note=note,
                path=path,
                mapping_overrides=overrides,
                dry_run=dry_run,
            )
    except ValueError as exc:  # an extension no reader handles
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@contextmanager
def _as_path(file: UploadFile) -> Iterator[Path]:
    """The upload on disk, because `import_set` reads a file by path.

    Written into a temporary directory under its own basename rather than a
    generated one, so the reader still sees the suffix that picks it and any
    complaint names the file the caller sent. The directory goes on the way
    out, whatever happened inside.
    """
    filename = Path(file.filename or "upload").name or "upload"
    with tempfile.TemporaryDirectory(prefix="fcp-projections-") as folder:
        path = Path(folder) / filename
        with path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        yield path


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------


def _readable(projection_set: ProjectionSet) -> bool:
    """Whether this caller may be shown a set's numbers.

    Always true today, and asked anyway: this is the API's half of the one
    check the constraint asks for (docs/projection_sources.md). An uploaded
    set is not gated, so the answer only moves if a gated source is ever
    served from here; `viewer_owns_source` is what accounts will answer, from
    `projection_set.owner`.
    """
    return may_show(upload_source(projection_set.id), viewer_owns_source=True)


def _stored_set(session: Session, set_id: int) -> ProjectionSet:
    projection_set = session.get(ProjectionSet, set_id)
    if projection_set is None:
        raise HTTPException(status_code=404, detail=f"no projection set {set_id}")
    if not _readable(projection_set):
        raise HTTPException(
            status_code=403,
            detail=f"{describe(upload_source(set_id))}: not this reader's to see",
        )
    return projection_set


def _set_out(projection_set: ProjectionSet) -> ProjectionSetOut:
    return ProjectionSetOut(
        id=projection_set.id,
        season=projection_set.season,
        name=projection_set.name,
        owner=projection_set.owner,
        uploaded_at=projection_set.uploaded_at,
        source_note=projection_set.source_note,
        rows=projection_set.rows,
        column_map=dict(projection_set.column_map or {}),
    )


def _import_out(report: ImportReport, file: UploadFile) -> ProjectionImportOut:
    """The report as JSON: the mapping, the basis, the counts and the refusals."""
    column_map: dict[str, Any] = report.mapping.as_json(report.basis)
    return ProjectionImportOut(
        ok=report.ok,
        dry_run=report.dry_run,
        set_id=report.set_id,
        season=report.season,
        name=report.name,
        filename=Path(file.filename or "upload").name,
        basis=report.basis,
        column_map=column_map,
        rows_read=report.rows_read,
        rows_stored=report.rows_stored,
        matched=report.matched,
        unmatched=list(report.unmatched),
        loose=list(report.loose),
        ambiguous=list(report.ambiguous),
        duplicates=list(report.duplicates),
        rejected=[RejectedRowOut(where=where, why=why) for where, why in report.rejected],
        reasons=list(report.mapping.problems),
    )
