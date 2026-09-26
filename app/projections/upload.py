"""A manager's own projections, read from whatever file he already has.

This is the path that lets a second person use the software at all. Basketball
Monster's numbers are paid and stay with the member who fetched them
(`docs/projection_sources.md`), so anybody else brings his own set and the room
is loaded from that instead.

NO TEMPLATE, A MAPPING

Nobody exports the columns we would ask for, and demanding a fixed template
means the file is edited by hand before it is uploaded, which is where the
mistakes come from. So the header row is read and mapped by a synonyms table
(`SYNONYMS`), the mapping is reported for confirmation before anything is
stored, and `--map` overrides any column the guess got wrong. What was applied
is kept on the set (`projection_sets.column_map`), so a set read back next
season still says how its columns were understood.

The draft plan page's mapping is the same override, extended to every field
(`FIELDS`) and sent whole (`exact`, so a field he set to none stays none),
with the basis forced when he switches it. A set also keeps the mapping it
was stored with (`projection_sets.mapping`, `ImportReport.stored_mapping`):
a name is the source, unique per owner and season, and a file uploaded
under it again replaces its rows in place and is read with last time's
mapping first when he sends none (`last_time`). A per-file fix never edits
`SYNONYMS`.

WHY ATTEMPTS ARE REQUIRED AND A PERCENTAGE IS NOT ENOUGH

Nine categories, and two of them are rates. A roster's FG% is its made shots
over its attempts, not the average of its players' percentages, so a set that
carries `fg%` and no `fga` cannot be turned into a roster line at all
(`app/scoring/lines.py`, `app.draft.valuation.PERCENTAGE_COMPONENTS`). Such a
file is refused with that reason rather than imported into a board that looks
right and is not. A percentage *with* attempts is fine: the makes are rebuilt
from the two.

PER GAME OR SEASON TOTALS

Both are exported in the wild and neither is labelled, so the basis is
measured rather than asked for: a per-game points figure is never above 200
and a season total almost always is (`TOTALS_ABOVE`). Totals are divided by
games on the way in, because rows are stored per game and the room multiplies
back up by the games projection -- which is what makes availability visible.

MATCHING

The strict matcher the draft room already uses (`app.draft.bbm.match_player`),
which refuses a doubtful match rather than guessing, over the same ESPN lookup
(`app.draft.bbm.espn_lookup`). A name that matches nobody is still stored and
still goes on the board under a synthetic id, the way BBM's rookies do.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import ProjectionRow, ProjectionSet
from app.draft.bbm import espn_lookup
from app.draft.valuation import PERCENTAGE_COMPONENTS, PlayerProjection
from app.player_names import match_player, name_key, synthetic_id
from app.projections.sources import upload_source

#: The counting stats a projection set carries, mapped to the column each is
#: stored in. Keyed exactly as the valuation and the scoring package key them
#: (`app.scoring.lines.COUNTS`), so a set drops into either without a rename.
COUNTS: Mapping[str, str] = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "3PM": "three_pointers_made",
    "TO": "turnovers",
    "FGM": "field_goals_made",
    "FGA": "field_goals_attempted",
    "FTM": "free_throws_made",
    "FTA": "free_throws_attempted",
}

#: Everything a set needs before it can be valued: a name to match on, a games
#: projection to turn rates into a season, and every count including the makes
#: *and* attempts behind both percentages.
REQUIRED: tuple[str, ...] = ("name", "games", *COUNTS)

#: Worth having and not worth refusing a file over. `minutes` is per game
#: (divided by games when the file is totals), `value` the source's own dollar
#: value as the file wrote it, `injury` its note. None of the three moves the
#: room's own arithmetic; a composite averages the first two.
OPTIONAL: tuple[str, ...] = ("team", "position", "minutes", "value", "injury")

#: Every field a mapping can name, in the order the plan page lists them:
#: the required, the two percentages (which rebuild a makes column from its
#: attempts), then the optional.
FIELDS: tuple[str, ...] = (*REQUIRED, "FG%", "FT%", *OPTIONAL)

#: A percentage column and the pair it can rebuild a makes column from:
#: FGM = fg% x FGA. The pairs are the valuation's own.
PERCENTAGE_FIELDS: Mapping[str, tuple[str, str]] = {
    f"{key[:2]}%": value for key, value in PERCENTAGE_COMPONENTS.items()
}

#: Header spellings for each field, normalised by `header_key`. First match in
#: the file wins, so the least ambiguous spelling is listed first and a header
#: is claimed by only one field.
#:
#: The order is not cosmetic. Checked against Basketball Monster's own 115
#: column export, `tov` matched BBM's `toV` -- its derived turnover *value*,
#: not turnovers -- because `tov` was listed before `to/g`. A file that carries
#: both a count and a value column for a category has to land on the count.
#:
#: Widened 2026-09-26 with the spellings of the files managers bring, written
#: from memory of those sites' tables (nothing was downloaded to check):
#: Hashtag Basketball (`TREB`, `MPG`), Rotowire (`MIN`), FantasyPros
#: (`Positions`), Yahoo (`GP*`, `3PTM`, `ST` -- the asterisk is dropped by
#: `header_key`), ESPN's own table (`PLAYER`, `MIN`) and a hand-made sheet
#: (`Points`, `Field Goals Made`, `Free Throws Attempted`, `Threes`). Not
#: handled, whatever the spelling: a single `FGM/FGA` or `FGM/A` column that
#: carries "7.2/13.4" in one cell (ESPN's and Yahoo's tables do), which has to
#: be split into two columns before it is uploaded. A mapping the manager
#: fixes on the page is his, for that file; it never edits this table.
SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "name": ("name", "player", "player name", "players", "full name", "playername", "athlete"),
    "games": (
        "g",
        "gp",
        "games",
        "games played",
        "gms",
        "gm",
        "proj gp",
        "proj g",
        "projected games",
    ),
    "team": ("team", "tm", "nba team", "pro team", "nba"),
    "position": ("pos", "position", "positions", "pos.", "eligibility", "elig", "eligible"),
    "PTS": ("pts", "points", "p/g", "ppg", "pts/g", "p", "points per game"),
    "REB": ("reb", "rebounds", "r/g", "rpg", "trb", "reb/g", "tot reb", "treb", "total rebounds"),
    "AST": ("ast", "assists", "a/g", "apg", "ast/g"),
    "STL": ("stl", "steals", "s/g", "spg", "stl/g", "st"),
    "BLK": ("blk", "blocks", "b/g", "bpg", "blk/g", "bl"),
    "3PM": (
        "3pm",
        "3/g",
        "threes",
        "3ptm",
        "3pg",
        "3p",
        "3s",
        "tpm",
        "3pm/g",
        "3pt",
        "3pt made",
        "3 pointers made",
        "three pointers made",
        "threes made",
        "3ptm/g",
    ),
    "TO": ("to/g", "turnovers", "to", "tov", "topg", "tos", "tov/g"),
    "FGM": ("fgm", "fg made", "fgm/g", "fg m", "field goals made", "fg"),
    "FGA": (
        "fga",
        "fg att",
        "fga/g",
        "fg a",
        "fg attempts",
        "field goals attempted",
        "fg attempted",
    ),
    "FTM": ("ftm", "ft made", "ftm/g", "ft m", "free throws made", "ft"),
    "FTA": (
        "fta",
        "ft att",
        "fta/g",
        "ft a",
        "ft attempts",
        "free throws attempted",
        "ft attempted",
    ),
    "FG%": ("fg%", "fg pct", "fgpct", "fg percent", "field goal %", "fg %", "field goal pct"),
    "FT%": ("ft%", "ft pct", "ftpct", "ft percent", "free throw %", "ft %", "free throw pct"),
    "minutes": ("mpg", "min", "minutes", "m/g", "min/g", "mins", "minutes per game"),
    # `$` before `value`: Basketball Monster's `Value` column is a z-score
    # total, not dollars, and a file carrying both means the dollars.
    "value": ("$", "auction $", "auction value", "dollars", "dollar value", "$ value", "value"),
    "injury": ("inj", "injury", "injury status", "injury note", "health"),
}

#: Above this many points the file is season totals, not per-game rates. The
#: highest per-game average in NBA history is Wilt Chamberlain's 50.4 and the
#: lowest full season total for a rotation player is well above 200, so the
#: two never overlap and the basis does not have to be asked for.
TOTALS_ABOVE = 200.0

#: A percentage written as 47.5 rather than 0.475. No shooter is above 100%,
#: so anything over 1 is a percentage in points.
_PERCENT_IN_POINTS = 1.0

#: Cells that mean "nothing here" rather than a number.
_EMPTY = ("", "-", "--", "n/a", "na", "null", "none", "#n/a")


# ---------------------------------------------------------------------------
# reading a file
# ---------------------------------------------------------------------------


def read_table(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """The header row and one dict per row, for CSV, .xlsx or .xls.

    Values come back as the file had them: numbers from a spreadsheet, strings
    from a CSV. Everything that reads them goes through `_number`, which takes
    either.
    """
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        return _read_csv(path)
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    if suffix == ".xls":
        return _read_xls(path)
    raise ValueError(f"{path.name}: not a projection file; expected .csv, .xlsx or .xls")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8-sig")
    sample = "\n".join(text.splitlines()[:5])
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return _table(rows)


def _read_xlsx(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    import openpyxl  # type: ignore[import-untyped]  # optional; only .xlsx needs it

    book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        sheet = book[book.sheetnames[0]]
        rows = [list(values) for values in sheet.iter_rows(values_only=True)]
    finally:
        book.close()
    return _table(rows)


def _read_xls(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    import xlrd  # type: ignore[import-untyped]  # optional; only .xls needs it

    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    rows = [list(sheet.row_values(index)) for index in range(sheet.nrows)]
    return _table(rows)


def _table(rows: Sequence[Sequence[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Rows of cells as a header and dicts, with blank and duplicate headers handled."""
    first = next((index for index, row in enumerate(rows) if any(_text(c) for c in row)), None)
    if first is None:
        return [], []
    headers: list[str] = []
    for index, cell in enumerate(rows[first]):
        label = _text(cell) or f"column {index + 1}"
        while label in headers:
            label = f"{label} (2)"
        headers.append(label)
    out: list[dict[str, Any]] = []
    for row in rows[first + 1 :]:
        if not any(_text(cell) for cell in row):
            continue
        out.append({header: (row[i] if i < len(row) else None) for i, header in enumerate(headers)})
    return headers, out


def _text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()


# ---------------------------------------------------------------------------
# mapping the columns
# ---------------------------------------------------------------------------


def header_key(header: str) -> str:
    """A header with case, underscores, spacing and a trailing footnote
    asterisk (Yahoo's `GP*`) gone, for matching synonyms."""
    text = re.sub(r"[_\-]+", " ", str(header).strip().lower()).rstrip("*").strip()
    return " ".join(text.split())


@dataclass(frozen=True)
class ColumnMapping:
    """Which header each field was read from, and what could not be found.

    Named `ColumnMapping` rather than `Mapping` so it does not shadow
    `collections.abc.Mapping`, which this module also uses.
    """

    #: canonical field -> the header it is read from.
    fields: Mapping[str, str] = field(default_factory=dict)
    #: A makes field rebuilt from a percentage: "FGM" -> ("FG%" header, "FGA").
    derived: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: Headers no field claimed, kept so the manager can see what was skipped.
    ignored: tuple[str, ...] = ()
    #: Required fields with no column, and why each matters, in plain words.
    missing: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.missing and not self.problems

    def header_for(self, field_name: str) -> str | None:
        return self.fields.get(field_name)

    def as_json(self, basis: str | None = None) -> dict[str, Any]:
        """What is stored on the set: exactly how the file was understood."""
        out: dict[str, Any] = {
            "fields": dict(self.fields),
            "derived": {
                key: {"percentage": header, "attempts": attempts}
                for key, (header, attempts) in self.derived.items()
            },
            "ignored": list(self.ignored),
        }
        if basis is not None:
            out["basis"] = basis
        return out


def parse_overrides(entries: Iterable[str]) -> dict[str, str]:
    """`["Points=PTS"]` as `{"Points": "PTS"}`, or a ValueError naming the entry.

    The terminal spells these `--map "Points=PTS"` and the API spells them
    `map=Points=PTS`; both arrive as the same strings, so both parse them here
    rather than each having its own idea of what a malformed one looks like.
    """
    out: dict[str, str] = {}
    for entry in entries:
        # From the right: a field never has "=" in it, and a header might.
        header, sep, field_name = entry.rpartition("=")
        if not sep or not header.strip() or not field_name.strip():
            raise ValueError(f"map {entry!r}: expected HEADER=FIELD, e.g. 'Points=PTS'")
        out[header.strip()] = field_name.strip()
    return out


def guess_mapping(
    headers: Sequence[str],
    *,
    overrides: Mapping[str, str] | None = None,
    exact: bool = False,
) -> ColumnMapping:
    """Map a file's headers onto our fields, and say what is missing and why.

    `overrides` is the manager's own `header=FIELD` corrections, applied over
    the guess and winning against it. With `exact` the overrides are the
    whole mapping and nothing is guessed: the plan page sends one entry per
    field he left a column on, so a field he set to none stays none.
    """
    by_key: dict[str, str] = {}
    for header in headers:
        by_key.setdefault(header_key(header), header)

    fields: dict[str, str] = {}
    claimed: set[str] = set()
    for field_name in () if exact else (*REQUIRED, *OPTIONAL, *PERCENTAGE_FIELDS):
        for synonym in SYNONYMS.get(field_name, ()):
            column = by_key.get(synonym)
            if column is not None and column not in claimed:
                fields[field_name] = column
                claimed.add(column)
                break

    problems: list[str] = []
    known = (*REQUIRED, *OPTIONAL, *PERCENTAGE_FIELDS)
    for header, field_name in (overrides or {}).items():
        found = header if header in headers else by_key.get(header_key(header))
        if found is None:
            problems.append(
                f"--map {header}={field_name}: the file has no column called {header!r}"
            )
            continue
        if field_name not in known:
            problems.append(
                f"--map {header}={field_name}: {field_name!r} is not a field; "
                f"one of {', '.join(known)}"
            )
            continue
        for existing, existing_header in list(fields.items()):
            if existing_header == found and existing != field_name:
                del fields[existing]
        fields[field_name] = found
        claimed.add(found)

    # FGM = fg% x FGA, and the same for free throws: a set that carries the
    # percentage and the attempts has the makes, it just has not written them.
    derived: dict[str, tuple[str, str]] = {}
    for percentage, (made, attempted) in PERCENTAGE_COMPONENTS.items():
        rate_field = f"{percentage[:2]}%"
        if made in fields or rate_field not in fields or attempted not in fields:
            continue
        derived[made] = (fields[rate_field], attempted)

    missing: list[str] = []
    notes: list[str] = list(problems)
    for field_name in REQUIRED:
        if field_name in fields or field_name in derived:
            continue
        missing.append(field_name)
        rebuilt_from = _percentage_for(field_name)
        if rebuilt_from is not None and rebuilt_from in fields:
            notes.append(
                f"{field_name} is missing and only {rebuilt_from} is on file: a percentage cannot "
                "be rebuilt into a roster's percentage, which is made shots over attempts, so "
                f"the file has to carry {_attempts_for(field_name)} as well (app/scoring/lines.py)"
            )
        else:
            notes.append(
                f"{field_name} is missing: "
                + ("no column is chosen for it" if exact else "no column matched it")
            )

    used = set(fields.values())
    ignored = tuple(header for header in headers if header not in used)
    return ColumnMapping(
        fields=fields,
        derived=derived,
        ignored=ignored,
        missing=tuple(missing),
        problems=tuple(notes) if missing or problems else (),
    )


def _percentage_for(made: str) -> str | None:
    """The percentage column a makes field could be rebuilt from."""
    for percentage, (made_key, _) in PERCENTAGE_COMPONENTS.items():
        if made_key == made:
            return f"{percentage[:2]}%"
    return None


def _attempts_for(made: str) -> str:
    for _, (made_key, attempted) in PERCENTAGE_COMPONENTS.items():
        if made_key == made:
            return attempted
    return made


# ---------------------------------------------------------------------------
# reading the numbers
# ---------------------------------------------------------------------------


def _blank(value: Any) -> bool:
    return value is None or _text(value).lower() in _EMPTY


def _number(value: Any) -> float | None:
    """A cell as a number, or None when it is not one. Blanks are not numbers."""
    if isinstance(value, bool) or _blank(value):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = _text(value).replace(",", "").replace("$", "")
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100.0 if percent else number


def _rate(value: Any) -> float | None:
    """A shooting percentage, whether the file wrote 0.475, 47.5 or "47.5%"."""
    number = _number(value)
    if number is None:
        return None
    return number / 100.0 if number > _PERCENT_IN_POINTS else number


def detect_basis(rows: Sequence[Mapping[str, Any]], mapping: ColumnMapping) -> str:
    """ "per_game" or "totals", measured from the points column.

    The median rather than the mean, so a handful of unparsed cells or one
    season-total row in a per-game file cannot flip it.
    """
    return measure_basis(rows, mapping)[0]


def measure_basis(rows: Sequence[Mapping[str, Any]], mapping: ColumnMapping) -> tuple[str, str]:
    """The basis and the reason for it, in the words the plan page shows."""
    header = mapping.header_for("PTS")
    if header is None:
        return "per_game", "no points column to measure, so read as per game"
    points = [number for row in rows if (number := _number(row.get(header))) is not None]
    if not points:
        return "per_game", f"no numbers in {header!r} to measure, so read as per game"
    middle = median(points)
    if middle > TOTALS_ABOVE:
        return "totals", (
            f"the median of {header!r} is {middle:,.1f}, above {TOTALS_ABOVE:.0f}: no one scores "
            "that many a game, so these are season totals, divided by games on the way in"
        )
    return "per_game", (
        f"the median of {header!r} is {middle:,.1f}, not above {TOTALS_ABOVE:.0f}: per-game numbers"
    )


def samples(
    headers: Sequence[str], rows: Sequence[Mapping[str, Any]], count: int = 3
) -> dict[str, list[Any]]:
    """The first few values of every column, so a wrong guess is visible."""
    return {header: [_jsonable(row.get(header)) for row in rows[:count]] for header in headers}


#: What a forced basis may be; "auto" is measured (`measure_basis`).
BASES = ("auto", "per_game", "totals")


# ---------------------------------------------------------------------------
# importing a set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportReport:
    """What an import read, stored, matched and refused."""

    season: int
    name: str
    path: str
    mapping: ColumnMapping
    basis: str
    rows_read: int = 0
    rows_stored: int = 0
    matched: int = 0
    #: Matched through a short first name rather than the full name.
    loose: tuple[str, ...] = ()
    #: On the board under a synthetic id: nobody of ours has the name.
    unmatched: tuple[str, ...] = ()
    #: A second row that matched a player another row already took.
    ambiguous: tuple[str, ...] = ()
    #: The same player twice in the file; the second is skipped.
    duplicates: tuple[str, ...] = ()
    #: (which row, why) for every row that could not be read.
    rejected: tuple[tuple[str, str], ...] = ()
    dry_run: bool = True
    #: The stored set, when one was stored.
    set_id: int | None = None
    #: The file's headers in order, and the first values under each.
    headers: tuple[str, ...] = ()
    samples: Mapping[str, list[Any]] = field(default_factory=dict)
    #: Why the basis is what it is: measured, or forced by the manager.
    basis_reason: str = ""
    basis_forced: bool = False
    #: The set of the same name this replaced (or would replace), if any.
    replaces: int | None = None
    #: Last time's mapping, when it was applied because none was given.
    last_time: LastTime | None = None

    @property
    def ok(self) -> bool:
        return self.mapping.usable

    def stored_mapping(self) -> dict[str, Any]:
        """What the set keeps of how it was mapped (`projection_sets.mapping`):
        the column per field, the basis if he forced one, the headers seen."""
        fields: dict[str, str | None] = {name: self.mapping.header_for(name) for name in FIELDS}
        return {
            "fields": fields,
            "basis": self.basis if self.basis_forced else "auto",
            "headers": list(self.headers),
        }

    def lines(self) -> list[str]:
        """The report in plain language, for a terminal."""
        out = [f"{self.path}: {self.rows_read} rows, read as {self.basis.replace('_', ' ')}"]
        out.append("columns:")
        for field_name in (*REQUIRED, *OPTIONAL):
            header = self.mapping.header_for(field_name)
            if header is not None:
                out.append(f"  {field_name:<9} <- {header}")
            elif field_name in self.mapping.derived:
                rate_header, attempts = self.mapping.derived[field_name]
                out.append(f"  {field_name:<9} <- {rate_header} x {attempts}")
            elif field_name in REQUIRED:
                out.append(f"  {field_name:<9} -- MISSING")
        if self.mapping.ignored:
            out.append(f"  ignored: {', '.join(self.mapping.ignored)}")
        if not self.ok:
            out.append("refused, nothing stored:")
            out.extend(f"  {problem}" for problem in self.mapping.problems)
            return out
        out.append(
            f"{self.matched} of {self.rows_read} matched to ESPN ids "
            f"({len(self.loose)} by short first name), "
            f"{len(self.unmatched)} on the board by name only"
        )
        for label, names in (
            ("not matched", self.unmatched),
            ("matched loosely", self.loose),
            ("ambiguous", self.ambiguous),
            ("duplicate in the file", self.duplicates),
        ):
            if names:
                out.append(f"{label} ({len(names)}): {', '.join(names)}")
        for where, why in self.rejected:
            out.append(f"rejected {where}: {why}")
        if self.dry_run:
            out.append(f"dry run: {self.rows_stored} rows would be stored. Re-run with --commit.")
        else:
            out.append(f"stored set {self.set_id}: {self.rows_stored} rows")
        return out


@dataclass
class _Parsed:
    name: str
    games: float
    per_game: dict[str, float]
    position: str | None
    team: str | None
    raw: dict[str, Any]
    minutes: float | None = None
    value: float | None = None
    injury: str | None = None


def named_set(session: Session, *, owner: str, season: int, name: str) -> ProjectionSet | None:
    """The set this owner already keeps under this name for this season.

    A name is the source (unique per owner and season, migration 0033): a
    file uploaded under it again replaces its rows, keeping its id.
    """
    return session.scalar(
        select(ProjectionSet).where(
            ProjectionSet.owner == owner,
            ProjectionSet.season == season,
            ProjectionSet.name == name,
        )
    )


@dataclass(frozen=True)
class LastTime:
    """The mapping a named source was last stored with, fitted to a new file.

    `overrides` are the stored columns this file still has, as `header=FIELD`
    entries for `import_set`; `gone` the fields whose stored column this
    file has not got, which the synonyms guess instead (and the page says
    so); `basis` what was forced last time, or "auto".
    """

    set_id: int
    uploaded_at: dt.datetime
    overrides: dict[str, str]
    gone: tuple[str, ...]
    basis: str

    @property
    def whole(self) -> bool:
        """Every stored column is in this file: the mapping is last time's exactly."""
        return not self.gone


def last_time(projection_set: ProjectionSet | None, headers: Sequence[str]) -> LastTime | None:
    """Last time's mapping for this file, when the name was stored before with one."""
    if projection_set is None or not projection_set.mapping:
        return None
    stored = projection_set.mapping.get("fields") or {}
    overrides: dict[str, str] = {}
    gone: list[str] = []
    for field_name, header in stored.items():
        if not header:
            continue
        if header in headers:
            overrides[str(header)] = str(field_name)
        else:
            gone.append(str(field_name))
    return LastTime(
        set_id=int(projection_set.id),
        uploaded_at=projection_set.uploaded_at,
        overrides=overrides,
        gone=tuple(gone),
        basis=str(projection_set.mapping.get("basis") or "auto"),
    )


def import_set(
    session: Session,
    *,
    season: int,
    name: str,
    owner: str,
    source_note: str = "",
    path: Path,
    mapping_overrides: Mapping[str, str] | None = None,
    exact: bool = False,
    basis: str | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Read a projections file, match its names and store it as a set.

    Nothing is written when the mapping is unusable, and nothing is written on
    a dry run; both come back as a report that says what would have happened,
    because a manager should see the mapping before he trusts a board built on
    it.

    `exact` makes `mapping_overrides` the whole mapping (`guess_mapping`);
    `basis` forces "per_game" or "totals" over the measured one ("auto" or
    None measures it). A set this owner already keeps under `name` for the
    season is replaced in place: its rows, its mapping and its upload time
    are new, its id is not, so a composite built on it knows to rebuild.
    A composite's name cannot be taken by an upload.
    """
    headers, rows = read_table(path)
    existing = named_set(session, owner=owner, season=season, name=name)
    if existing is not None and existing.kind != "upload":
        raise ValueError(
            f"{name!r} is the name of one of your composites; an upload needs a name of its own"
        )
    # A name stored before, and no mapping of his own this time: last time's first.
    remembered = last_time(existing, headers) if mapping_overrides is None else None
    if remembered is not None:
        mapping_overrides = remembered.overrides
        exact = remembered.whole
        if basis is None:
            basis = remembered.basis
    mapping = guess_mapping(headers, overrides=mapping_overrides, exact=exact)
    measured, reason = measure_basis(rows, mapping)
    forced = basis is not None and basis != "auto"
    if forced:
        if basis not in BASES:
            raise ValueError(f"basis {basis!r}: one of {', '.join(BASES)}")
        reason = (
            f"set by hand to {str(basis).replace('_', ' ')}; measured, it reads as "
            f"{measured.replace('_', ' ')} ({reason})"
        )
    chosen = str(basis) if forced else measured
    head: dict[str, Any] = {
        "headers": tuple(headers),
        "samples": samples(headers, rows),
        "basis_reason": reason,
        "basis_forced": forced,
        "replaces": int(existing.id) if existing is not None else None,
        "last_time": remembered,
    }
    if not mapping.usable:
        return ImportReport(
            season=season,
            name=name,
            path=str(path),
            mapping=mapping,
            basis=chosen,
            rows_read=len(rows),
            dry_run=dry_run,
            **head,
        )
    basis = chosen

    lookup = espn_lookup(session, season)
    placed: list[tuple[_Parsed, int | None]] = []
    keys: set[str] = set()
    taken: dict[int, str] = {}
    loose: list[str] = []
    unmatched: list[str] = []
    ambiguous: list[str] = []
    duplicates: list[str] = []
    rejected: list[tuple[str, str]] = []

    for index, row in enumerate(rows, start=2):  # 1 is the header
        parsed, why = _parse_row(row, mapping, basis)
        if parsed is None:
            rejected.append((f"row {index}", why or "unreadable"))
            continue
        key = name_key(parsed.name)
        if key in keys:
            duplicates.append(parsed.name)
            continue
        found = match_player(parsed.name, lookup.known, lookup.recency)
        if found is not None and found in taken:
            ambiguous.append(f"{parsed.name} (a second name for {taken[found]})")
            found = None
        if found is None:
            unmatched.append(parsed.name)
        else:
            taken[found] = lookup.known[found]
            if name_key(parsed.name) != name_key(lookup.known[found]):
                loose.append(f"{parsed.name} = {lookup.known[found]}")
        keys.add(key)
        placed.append((parsed, found))

    report = ImportReport(
        season=season,
        name=name,
        path=str(path),
        mapping=mapping,
        basis=basis,
        rows_read=len(rows),
        rows_stored=len(placed),
        matched=len(taken),
        loose=tuple(loose),
        unmatched=tuple(unmatched),
        ambiguous=tuple(ambiguous),
        duplicates=tuple(duplicates),
        rejected=tuple(rejected),
        dry_run=dry_run,
        **head,
    )
    if dry_run:
        return report

    if existing is not None:
        projection_set = existing
        session.execute(delete(ProjectionRow).where(ProjectionRow.set_id == existing.id))
        projection_set.source_note = source_note or projection_set.source_note
        projection_set.uploaded_at = dt.datetime.now(dt.UTC)
    else:
        projection_set = ProjectionSet(season=season, name=name, owner=owner, kind="upload")
        projection_set.source_note = source_note
        session.add(projection_set)
    projection_set.column_map = mapping.as_json(basis)
    projection_set.mapping = report.stored_mapping()
    projection_set.rows = len(placed)
    session.flush()
    set_id = int(projection_set.id)
    for parsed, found in placed:
        session.add(
            ProjectionRow(
                set_id=set_id,
                name=parsed.name,
                name_key=name_key(parsed.name),
                player_id=lookup.ours.get(found) if found is not None else None,
                games=parsed.games,
                position=parsed.position,
                team=parsed.team,
                minutes=parsed.minutes,
                value=parsed.value,
                injury=parsed.injury,
                raw=parsed.raw,
                **{COUNTS[key]: value for key, value in parsed.per_game.items()},
            )
        )
    session.flush()
    session.refresh(projection_set)
    return replace(report, set_id=set_id)


def _parse_row(
    row: Mapping[str, Any], mapping: ColumnMapping, basis: str
) -> tuple[_Parsed | None, str | None]:
    """One file row as per-game numbers, or the reason it cannot be read."""
    name = _text(row.get(mapping.fields["name"], ""))
    if not name:
        return None, "no player name"
    games = _number(row.get(mapping.fields["games"]))
    if games is None:
        return None, f"{name}: games is not a number"
    if games <= 0:
        return None, f"{name}: no projected games"

    per_game: dict[str, float] = {}
    for key in COUNTS:
        header = mapping.header_for(key)
        if header is not None:
            value = row.get(header)
            number = 0.0 if _blank(value) else _number(value)
            if number is None:
                return None, f"{name}: {key} is {_text(value)!r}, which is not a number"
            per_game[key] = number / games if basis == "totals" else number
    for made, (rate_header, attempted) in mapping.derived.items():
        rate = _rate(row.get(rate_header))
        per_game[made] = 0.0 if rate is None else rate * per_game[attempted]

    position = mapping.header_for("position")
    team = mapping.header_for("team")
    minutes_header = mapping.header_for("minutes")
    minutes = _number(row.get(minutes_header)) if minutes_header else None
    if minutes is not None and basis == "totals":
        minutes = minutes / games
    value_header = mapping.header_for("value")
    injury_header = mapping.header_for("injury")
    return (
        _Parsed(
            name=name,
            games=games,
            per_game=per_game,
            position=(_text(row.get(position)) or None) if position else None,
            team=(_text(row.get(team)) or None) if team else None,
            raw={key: _jsonable(value) for key, value in row.items()},
            minutes=minutes,
            value=_number(row.get(value_header)) if value_header else None,
            injury=(_text(row.get(injury_header)) or None) if injury_header else None,
        ),
        None,
    )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# reading a set back
# ---------------------------------------------------------------------------


def load_projection_set(session: Session, set_id: int) -> list[PlayerProjection]:
    """A stored set as season totals, keyed the way the room keys players.

    Totals are per-game rates times the games projection, because value is
    computed on totals: in a head-to-head league a player only helps on the
    nights he plays (`app.draft.valuation`). Eligible slots come from ESPN's
    own line where we hold one, and from the set's position string otherwise,
    exactly as a BBM load takes them.
    """
    projection_set = session.get(ProjectionSet, set_id)
    if projection_set is None:
        raise ValueError(f"no projection set {set_id}")
    lookup = espn_lookup(session, projection_set.season)
    espn_of = {ours: espn for espn, ours in lookup.ours.items()}

    out: list[PlayerProjection] = []
    for row in session.scalars(
        select(ProjectionRow).where(ProjectionRow.set_id == set_id).order_by(ProjectionRow.name_key)
    ):
        espn_id = espn_of.get(row.player_id) if row.player_id is not None else None
        player_id = espn_id if espn_id is not None else synthetic_id(row.name)
        eligible, position, _ = lookup.eligibility(player_id, row.position or "")
        out.append(
            PlayerProjection(
                player_id=player_id,
                name=row.name,
                games=row.games,
                totals={
                    key: float(getattr(row, column)) * row.games for key, column in COUNTS.items()
                },
                eligible=eligible,
                position=position,
                source=upload_source(set_id),
            )
        )
    return out


def set_headline(session: Session, set_id: int) -> str:
    """What a set is called and where its numbers came from, for a page header.

    Shorter than `set_note`, which counts the matching as well: a header wants
    the one thing a reader cannot see from the board itself.
    """
    projection_set = session.get(ProjectionSet, set_id)
    if projection_set is None:
        raise ValueError(f"no projection set {set_id}")
    note = projection_set.source_note.strip()
    return f"{projection_set.name!r}{f': {note}' if note else ''}"


def stored_sets(session: Session, season: int | None = None) -> list[ProjectionSet]:
    """Every stored set, newest first, for a season or for all of them."""
    query = select(ProjectionSet).order_by(
        ProjectionSet.uploaded_at.desc(), ProjectionSet.id.desc()
    )
    if season is not None:
        query = query.where(ProjectionSet.season == season)
    return list(session.scalars(query))


def set_note(session: Session, set_id: int) -> str:
    """One line about a set, for the room's header."""
    projection_set = session.get(ProjectionSet, set_id)
    if projection_set is None:
        raise ValueError(f"no projection set {set_id}")
    matched = (
        session.scalar(
            select(func.count())
            .select_from(ProjectionRow)
            .where(ProjectionRow.set_id == set_id, ProjectionRow.player_id.is_not(None))
        )
        or 0
    )
    note = projection_set.source_note.strip()
    return (
        f"pool: uploaded set {set_id}, {projection_set.name!r} "
        f"(uploaded by {projection_set.owner}): {projection_set.rows} players, "
        f"{matched} matched to ESPN ids, {projection_set.rows - matched} on the board by "
        f"name only{f'. {note}' if note else ''}"
    )
