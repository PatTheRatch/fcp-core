"""Basketball Monster's projections, kept day by day in the database.

The files in `data/bbm/` hold only the latest export. This keeps the history,
so a question like "what did BBM project for him on 3 November?" or "when did
BBM cut his games?" can be answered later, and the scoring work can grade a
move on the projection that was in front of the manager that day.

STORED AS VERSIONS, NOT COPIES

A player's row is written when it changes (`CHANGE_COLUMNS`: his projected
stats, games, dollar values, team, injury, notes and flags); an unchanged row
only moves its version's `last_seen` forward. The row BBM served on a date is the version
whose `first_seen <= date <= last_seen`. `bbm_captures` logs every pull, with
how many rows changed, so a missing day reads as a pull that did not run
rather than as a quiet day.

Before the season few rows change from one day to the next. Once games are
played nearly every row will (games left, form, injuries, trades), so plan
for the worst case of a full copy a day: measured on the 2027 export, 586
rows at 2.6 KB of JSON each, 1.3 KB compressed, two value types, about 1.5 MB
a day and 270 MB a season. The VPS had 108 GB free when this was written.

Players are tracked by `app.draft.bbm.name_key`, and matched to ours with the
same strict matcher the draft room uses; an unmatched rookie is still stored,
with no `player_id`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BBMCapture, BBMProjection, Player, PlayerSeasonStat
from app.player_names import match_player, name_key


def read_export(body: bytes) -> list[dict[str, Any]]:
    """Every row of an export, as column -> value, keyed by BBM's own headers."""
    import xlrd  # type: ignore[import-untyped]  # optional; only BBM loaders need it

    sheet = xlrd.open_workbook(file_contents=body).sheet_by_index(0)
    header = [str(v).strip() for v in sheet.row_values(0)]
    rows = []
    for index in range(1, sheet.nrows):
        values = sheet.row_values(index)
        row = {column: values[i] for i, column in enumerate(header) if column}
        if str(row.get("Name", "")).strip():
            rows.append(row)
    return rows


#: The columns whose change means BBM changed its view of a player, and how
#: finely each is compared (decimal places; None for text). Everything else in
#: the row is stored but not compared. Found 2026-09-17 when two pulls five
#: hours apart differed in every row: BBM recomputes its derived value columns
#: (pV ... toV, the D/DH splits, LeagV, PuntV, PosV, BZ, Punt+, 1W+-) against
#: the pool on each export, so they wander in the third decimal with nothing
#: new, and ranks, ADP, ownership and Age move daily for reasons of their own.
CHANGE_COLUMNS: dict[str, int | None] = {
    "Team": None,
    "Pos": None,
    "Note": None,
    "Inj": None,
    "Inj Risk": None,
    "Status": None,
    "Conf": None,
    "Role": None,
    "Tier": None,
    "g": 0,
    "m/g": 1,
    "p/g": 1,
    "3/g": 1,
    "r/g": 1,
    "a/g": 1,
    "s/g": 1,
    "b/g": 1,
    "to/g": 1,
    "fga/g": 1,
    "fta/g": 1,
    "fg%": 3,
    "ft%": 3,
    "$": 0,
    "Leag$": 0,
}


def row_hash(row: dict[str, Any]) -> str:
    """A fingerprint of what BBM thinks of the player, blind to recomputation noise."""
    key = {}
    for column, places in CHANGE_COLUMNS.items():
        value = row.get(column)
        if places is not None and isinstance(value, int | float):
            value = round(float(value), places) + 0.0
        key[column] = value
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class CaptureResult:
    season: int
    value_type: str
    captured_on: date
    players: int
    changed: int
    dropped: int


def _matcher(session: Session) -> tuple[dict[int, str], dict[int, int], dict[int, int]]:
    """Names and recency by ESPN id, and our id by ESPN id, for strict matching."""
    known: dict[int, str] = {}
    ours: dict[int, int] = {}
    for player_id, espn_id, name in session.execute(
        select(Player.id, Player.espn_player_id, Player.name)
    ).all():
        known[int(espn_id)] = str(name)
        ours[int(espn_id)] = int(player_id)
    recency = {
        int(espn_id): int(season)
        for espn_id, season in session.execute(
            select(Player.espn_player_id, func.max(PlayerSeasonStat.season))
            .join(PlayerSeasonStat, PlayerSeasonStat.player_id == Player.id)
            .group_by(Player.espn_player_id)
        ).all()
    }
    return known, recency, ours


def capture(
    session: Session,
    *,
    season: int,
    value_type: str,
    rows: Sequence[dict[str, Any]],
    captured_on: date,
    source: str | None = None,
    league: str | None = None,
) -> CaptureResult:
    """Store one day's export. Running it again the same day replaces that day."""
    previous_day = session.scalar(
        select(func.max(BBMCapture.captured_on)).where(
            BBMCapture.season == season,
            BBMCapture.value_type == value_type,
            BBMCapture.captured_on < captured_on,
        )
    )
    since = previous_day or captured_on
    alive: dict[str, BBMProjection] = {}
    for version in session.scalars(
        select(BBMProjection)
        .where(
            BBMProjection.season == season,
            BBMProjection.value_type == value_type,
            BBMProjection.last_seen >= since,
        )
        .order_by(BBMProjection.first_seen)
    ):
        alive[version.name_key] = version

    known, recency, ours = _matcher(session)
    seen: set[str] = set()
    changed = 0
    for row in rows:
        name = str(row["Name"]).strip()
        key = name_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        digest = row_hash(row)
        current = alive.get(key)
        if current is not None and current.row_hash == digest:
            current.last_seen = max(current.last_seen, captured_on)
            continue
        changed += 1
        if current is not None and current.first_seen == captured_on:
            current.row, current.row_hash = dict(row), digest
            continue
        espn_id = match_player(name, known, recency)
        session.add(
            BBMProjection(
                season=season,
                value_type=value_type,
                name=name,
                name_key=key,
                player_id=ours.get(espn_id) if espn_id is not None else None,
                first_seen=captured_on,
                last_seen=captured_on,
                row_hash=digest,
                row=dict(row),
            )
        )
    dropped = sum(1 for key in alive if key not in seen)

    log = session.scalar(
        select(BBMCapture).where(
            BBMCapture.season == season,
            BBMCapture.value_type == value_type,
            BBMCapture.captured_on == captured_on,
        )
    )
    if log is None:
        log = BBMCapture(season=season, value_type=value_type, captured_on=captured_on)
        session.add(log)
    log.source, log.league = source, league
    log.players, log.changed, log.dropped = len(seen), changed, dropped
    session.flush()
    return CaptureResult(season, value_type, captured_on, len(seen), changed, dropped)


def as_of(
    session: Session, season: int, on: date, value_type: str = "total"
) -> list[BBMProjection]:
    """Every player's row as BBM served it on `on`, or the latest capture before it."""
    day = session.scalar(
        select(func.max(BBMCapture.captured_on)).where(
            BBMCapture.season == season,
            BBMCapture.value_type == value_type,
            BBMCapture.captured_on <= on,
        )
    )
    if day is None:
        return []
    return list(
        session.scalars(
            select(BBMProjection)
            .where(
                BBMProjection.season == season,
                BBMProjection.value_type == value_type,
                BBMProjection.first_seen <= day,
                BBMProjection.last_seen >= day,
            )
            .order_by(BBMProjection.name)
        )
    )


def history(
    session: Session, season: int, name: str, value_type: str = "total"
) -> list[BBMProjection]:
    """Every version of one player's row, oldest first."""
    return list(
        session.scalars(
            select(BBMProjection)
            .where(
                BBMProjection.season == season,
                BBMProjection.value_type == value_type,
                BBMProjection.name_key == name_key(name),
            )
            .order_by(BBMProjection.first_seen)
        )
    )


def latest_capture(
    session: Session, season: int, on: date | None = None, value_type: str = "total"
) -> date | None:
    """The day of the newest stored capture for a season, on or before `on`."""
    query = select(func.max(BBMCapture.captured_on)).where(
        BBMCapture.season == season, BBMCapture.value_type == value_type
    )
    if on is not None:
        query = query.where(BBMCapture.captured_on <= on)
    return session.scalar(query)


def _in_export_order(versions: Sequence[BBMProjection]) -> list[dict[str, Any]]:
    """The stored rows in the order the export listed them: BBM's own rank.

    The room reads a pool in file order, and the order is not idle: it is
    the order players are matched in (a second name for the same man loses)
    and the order the optimizer's candidates are shuffled from. The export
    is sorted by `Rank`, so sorting the stored rows by it puts them back.
    """
    records = [dict(version.row) for version in versions]

    def rank(record: dict[str, Any]) -> tuple[float, str]:
        value = record.get("Rank")
        ranked = float(value) if isinstance(value, int | float) else float("inf")
        return (ranked, str(record.get("Name", "")))

    return sorted(records, key=rank)


def stored_records(
    session: Session, season: int, on: date, value_type: str = "total"
) -> tuple[list[dict[str, Any]], list[str]]:
    """The rows BBM served on `on`, as the export's records and its columns.

    What `app.draft.bbm.parse_records` reads, so a plan built on the store
    reads the same rows the draft room reads from the file.
    """
    records = _in_export_order(as_of(session, season, on, value_type))
    columns: list[str] = []
    seen: set[str] = set()
    for record in records:
        for column in record:
            if column not in seen:
                seen.add(column)
                columns.append(column)
    return records, columns
