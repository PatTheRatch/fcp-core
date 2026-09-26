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
remember.

A set is readable only by its owner (docs/accounts.md). In accounts mode
`ProjectionSet.owner` is the uploader's user id, as a string (the column
stays a string), whatever the `owner` form field says; `_readable` compares
it with the caller's, and a set that is not the caller's is a 404, the same
answer as a set nobody stored. The owner also reads the sets stored under
the old label, "patrick", so nothing uploaded before accounts has to be
rewritten. In single mode every caller is the owner and the label is kept as
the form gives it, exactly as before.
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

from app.api.access import CurrentUser, Viewer
from app.api.deps import SessionDep
from app.api.schemas import (
    FieldMapOut,
    LastTimeOut,
    ProjectionImportOut,
    ProjectionLineOut,
    ProjectionSetOut,
    RejectedRowOut,
)
from app.db.models import Player, ProjectionRow, ProjectionSet
from app.projections.sources import describe, may_show, upload_source
from app.projections.upload import (
    COUNTS,
    FIELDS,
    REQUIRED,
    ImportReport,
    import_set,
    parse_overrides,
    stored_sets,
)

router = APIRouter(prefix="/projections", tags=["projections"])

#: Whose set it is in single mode, and the label the owner's sets were stored
#: under before accounts. The CLI defaults to the same name.
DEFAULT_OWNER = "patrick"

MAP_HELP = "Force one column, as 'Header=FIELD'. Repeat the field per column."
FILE_HELP = "The projections file: .csv, .xlsx or .xls, one row per player"
NAME_HELP = "What to call this set, e.g. 'Hashtag preseason'"
NOTE_HELP = "Where the numbers came from, in your own words"
OWNER_HELP = "Whose set it is: a label in single mode; ignored with accounts, where it is you"

FileUpload = Annotated[UploadFile, File(description=FILE_HELP)]
SeasonForm = Annotated[int, Form(description="The season these project, e.g. 2027")]
NoteForm = Annotated[str, Form(description=NOTE_HELP)]
OwnerForm = Annotated[str, Form(description=OWNER_HELP)]
MapForm = Annotated[list[str] | None, Form(alias="map", description=MAP_HELP)]
ExactForm = Annotated[
    bool,
    Form(description="True: the map entries are the whole mapping, nothing is guessed (the page)"),
]
BasisForm = Annotated[
    str,
    Form(description="'auto' measures per game or totals from the points column; or force one"),
]


@router.post(
    "/sets/preview",
    summary="Read a projections file, report how it was understood, store nothing",
)
def preview_set(
    session: SessionDep,
    viewer: CurrentUser,
    file: FileUpload,
    season: SeasonForm,
    name: Annotated[str, Form(description=NAME_HELP)] = "preview",
    note: NoteForm = "",
    owner: OwnerForm = DEFAULT_OWNER,
    column_map: MapForm = None,
    exact: ExactForm = False,
    basis: BasisForm = "auto",
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
        owner=_owner_of(viewer, owner),
        column_map=column_map,
        exact=exact,
        basis=basis,
        dry_run=True,
    )
    return _import_out(report, file)


@router.post("/sets", summary="Store a projections file as a set the room can draft on")
def create_set(
    session: SessionDep,
    viewer: CurrentUser,
    file: FileUpload,
    season: SeasonForm,
    name: Annotated[str, Form(description=NAME_HELP)],
    note: NoteForm = "",
    owner: OwnerForm = DEFAULT_OWNER,
    column_map: MapForm = None,
    exact: ExactForm = False,
    basis: BasisForm = "auto",
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
        owner=_owner_of(viewer, owner),
        column_map=column_map,
        exact=exact,
        basis=basis,
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
    viewer: CurrentUser,
    season: int | None = Query(default=None, description="Restrict to one season"),
) -> list[ProjectionSetOut]:
    """The caller's own sets. Bounded by how many he has uploaded, so a plain list."""
    return [
        _set_out(stored) for stored in stored_sets(session, season) if _readable(stored, viewer)
    ]


@router.get("/sets/{set_id}", summary="One stored set")
def get_set(set_id: int, session: SessionDep, viewer: CurrentUser) -> ProjectionSetOut:
    return _set_out(_stored_set(session, set_id, viewer))


@router.get("/sets/{set_id}/rows", summary="A stored set's players, as per-game lines")
def list_set_rows(set_id: int, session: SessionDep, viewer: CurrentUser) -> list[ProjectionLineOut]:
    """Every row in the set. Bounded by the upload, whose size `/sets/{id}` gives."""
    _stored_set(session, set_id, viewer)
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
    exact: bool = False,
    basis: str = "auto",
    dry_run: bool,
) -> ImportReport:
    """`import_set` over an upload, with the caller's mistakes turned into 422s.

    No `map` at all is None, not an empty mapping, so a name stored before
    is read with last time's mapping first; `exact` with no entries is an
    empty mapping he chose, which is refused for its missing fields.
    """
    try:
        overrides = parse_overrides(column_map or []) if column_map or exact else None
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
                exact=exact,
                basis=basis,
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


def _owner_of(viewer: Viewer, asked: str) -> str:
    """Whose a new set is: the uploader's user id, or in single mode the label."""
    if viewer.all_access or viewer.user_id is None:
        return asked
    return str(viewer.user_id)


def _owns(projection_set: ProjectionSet, viewer: Viewer) -> bool:
    """Whether this set is the caller's (see WHO MAY READ A SET)."""
    if viewer.all_access:
        return True
    if viewer.user_id is not None and projection_set.owner == str(viewer.user_id):
        return True
    return viewer.is_owner and projection_set.owner == DEFAULT_OWNER


def _readable(projection_set: ProjectionSet, viewer: Viewer) -> bool:
    """Whether this caller may be shown a set's numbers.

    His own set only, and then the API's half of the one check the
    constraint asks for (docs/projection_sources.md): `may_show`, with
    `viewer_owns_source` answered from `projection_set.owner`. An uploaded
    set is not gated, so for an owner that answer is yes.
    """
    owns = _owns(projection_set, viewer)
    return owns and may_show(upload_source(projection_set.id), viewer_owns_source=owns)


def _stored_set(session: Session, set_id: int, viewer: Viewer) -> ProjectionSet:
    projection_set = session.get(ProjectionSet, set_id)
    if projection_set is None or not _owns(projection_set, viewer):
        raise HTTPException(status_code=404, detail=f"no projection set {set_id}")
    if not _readable(projection_set, viewer):
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
        kind=projection_set.kind,
        mapping=projection_set.mapping,
        recipe=projection_set.recipe,
        built_from=projection_set.built_from,
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
        headers=list(report.headers),
        samples=dict(report.samples),
        fields=[
            FieldMapOut(
                field=name,
                required=name in REQUIRED,
                header=report.mapping.header_for(name),
                derived=(
                    f"{report.mapping.derived[name][0]} x {report.mapping.derived[name][1]}"
                    if name in report.mapping.derived
                    else None
                ),
            )
            for name in FIELDS
        ],
        basis_reason=report.basis_reason,
        basis_forced=report.basis_forced,
        replaces=report.replaces,
        last_time=(
            LastTimeOut(
                set_id=report.last_time.set_id,
                uploaded_at=report.last_time.uploaded_at,
                whole=report.last_time.whole,
                gone=list(report.last_time.gone),
            )
            if report.last_time is not None
            else None
        ),
    )
