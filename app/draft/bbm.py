"""Basketball Monster projections, as the room consumes them.

The manager drafts on these, not on ESPN's, and the redraft of 2026 showed
why that matters: bidding on ESPN's projections the room bought every
ageing star the market was discounting and finished 81-88 against the
real opponents, where the roster actually drafted went 99-69. ESPN's
projections do not price availability -- players deliver about 88% of
their projected games -- and carry nothing about age or injury risk.

Measured against 2026, Basketball Monster's projected games already do:
realized over projected games runs 0.96 on average across players
projected for fifty or more, against ESPN's 0.88. Curry was projected 56
games, not 72; Morant 48, not 67; Kawhi 49, with the injury noted. So a
board built from these takes an availability factor of one, not the 0.881
measured for ESPN. Stacking the two would discount twice.

The file is BBM's exported .xls: one row per player, per-game rates and a
games projection, a position string like `PG/SG`, and BBM's own dollar
value. There is no ESPN id, so players are matched to ours by name, and
strictly: the same name once punctuation and suffixes are gone (`O.G.
Anunoby` is `OG Anunoby`, `Ronald Holland II` is `Ron Holland`'s full
name), or the same surname with one first name the start of the other
(`Cam`/`Cameron`, `Herb`/`Herbert`). The typing matcher in the feed is
deliberately loose, and loading a projection file through it put Caleb
Wilson's line on Jalen Wilson and Bronny James's next to LeBron's. A
wrong match is worse than none.

A player who matches nobody -- in 2027 that is the rookie class, Cameron
Boozer and AJ Dybantsa among them -- still goes on the board, under a
negative id of his own (`synthetic_id`), so the room can value him and a
pick of him by name still lands. Eligible slots come from ESPN's line for
the player, this season's projection first and his most recent line
otherwise, because ESPN's eligibility is what the lineup constraint is
checked against; a player ESPN has never carried gets slots derived from
BBM's position.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerSeasonStat
from app.draft.valuation import PlayerProjection

# The strict matcher this loader used to define. It lives in
# `app.player_names` now, because the NBA's injury reports place a name on a
# player by exactly the same rule (docs/injuries.md).
from app.player_names import match_player, name_key, synthetic_id
from app.projections import sources

#: Per-game columns in BBM's export, and the season-total keys they become.
_RATES: dict[str, str] = {
    "p/g": "PTS",
    "r/g": "REB",
    "a/g": "AST",
    "s/g": "STL",
    "b/g": "BLK",
    "3/g": "3PM",
    "to/g": "TO",
    "fga/g": "FGA",
    "fta/g": "FTA",
}

#: What a BBM position string entitles a player to start at, when ESPN's
#: own eligibility for him is not on file.
_SLOTS: dict[str, frozenset[str]] = {
    "PG": frozenset({"PG", "G", "UT"}),
    "SG": frozenset({"SG", "G", "UT"}),
    "SF": frozenset({"SF", "F", "UT"}),
    "PF": frozenset({"PF", "F", "UT"}),
    "C": frozenset({"C", "UT"}),
}


@dataclass(frozen=True)
class BBMRow:
    name: str
    position: str
    games: float
    rates: Mapping[str, float]
    fg_pct: float
    ft_pct: float
    dollars: float | None
    injury: str
    injury_risk: str
    age: float | None = None
    #: What ESPN's and Yahoo's own drafts paid for him on average, when the
    #: export carries it (2027 onward). The market, not a projection.
    espn_dollars: float | None = None
    yahoo_dollars: float | None = None
    #: BBM's value for the league the export was made from (`Leag$`), which
    #: prices this league's categories, size and budget; `dollars` is BBM's
    #: generic value.
    league_dollars: float | None = None
    #: NBA team abbreviation, e.g. "CLE".
    team: str = ""
    #: The analyst's written take, and who wrote it.
    note: str = ""
    note_by: str = ""
    #: BBM's confidence in the projection, 1-10.
    confidence: int | None = None
    #: Expected role code (ST, mST, BN, mBN, limBN, EM, NVR); see `ROLES`.
    role: str = ""
    #: Contract and roster situation, e.g. ("Rookie", "New Team").
    status: tuple[str, ...] = ()
    #: Analyst flags, e.g. ("Breakout Candidate", "Position Battle"). Read from
    #: every analyst column, so tags BBM adds later (Bust Candidate, Sleeper,
    #: Tank Candidate) come through without a code change.
    tags: tuple[str, ...] = ()


@dataclass
class BBMLoad:
    projections: list[PlayerProjection]
    matched: int = 0
    #: Matched through a short first name rather than the full name.
    loose: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    #: On the board under a synthetic id: nobody of ours has the name.
    unmatched: list[str] = field(default_factory=list)
    espn_eligibility: int = 0
    derived_eligibility: int = 0
    #: The export's row for every projection, by the id it was loaded under.
    rows: dict[int, BBMRow] = field(default_factory=dict)


#: BBM's role codes, in the words a draft screen should use.
ROLES: dict[str, str] = {
    "ST": "starter",
    "mST": "marginal starter",
    "BN": "bench",
    "mBN": "marginal bench",
    "limBN": "limited bench",
    "EM": "emergency only",
    "NVR": "not expected to play",
}

#: Columns BBM fills with one analyst's flags.
_ANALYST_COLUMNS = ("Josh", "Kyle", "Matt")

_NOTE = re.compile(r"^\s*\[\s*(?P<by>[^:\]]+):\s*(?P<text>.*?)\]?\s*$", re.DOTALL)


def split_note(raw: str) -> tuple[str, str]:
    """`[Josh: text]` -> ("Josh", "text"). Unwrapped text keeps an empty author."""
    text = raw.strip()
    if not text:
        return "", ""
    match = _NOTE.match(text)
    if match is None:
        return "", text
    return match.group("by").strip(), match.group("text").strip().rstrip("]").strip()


def split_list(raw: str) -> tuple[str, ...]:
    """BBM's pipe-separated lists, cleaned and de-duplicated, order kept."""
    out: list[str] = []
    for part in str(raw).split("|"):
        item = part.split(" - ")[0].strip()
        if item and item not in out:
            out.append(item)
    return tuple(out)


def read_bbm(path: Path) -> list[BBMRow]:
    """Every row of the export with a games projection."""
    import xlrd  # type: ignore[import-untyped]  # optional; only this loader needs it

    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    header = [str(v).strip() for v in sheet.row_values(0)]
    records = []
    for r in range(1, sheet.nrows):
        values = sheet.row_values(r)
        records.append({name: values[index] for index, name in enumerate(header)})
    return parse_records(records, header, label=path.name)


def parse_records(
    records: Iterable[Mapping[str, object]], columns: Iterable[str], *, label: str
) -> list[BBMRow]:
    """BBM rows from records keyed by the export's own column headers.

    One parser for both places an export is read from: the file on disk
    (`read_bbm`) and the rows the store kept of it (`app.draft.bbm_store`,
    whose `row` is exactly such a record), so a plan built from a stored
    capture reads a player the way the draft room reads the file.
    """
    col = set(columns)
    required = ("Name", "Pos", "g", "fg%", "ft%", *_RATES)
    missing = [c for c in required if c not in col]
    if missing:
        raise ValueError(f"{label}: not a BBM projection export, missing {missing}")

    rows: list[BBMRow] = []
    for record in records:
        name = str(record.get("Name", "")).strip()
        games = record.get("g")
        if not name or not isinstance(games, int | float) or games <= 0:
            continue

        def num(column: str, values: Mapping[str, object] = record) -> float:
            value = values.get(column)
            return float(value) if isinstance(value, int | float) else 0.0

        def maybe(column: str, values: Mapping[str, object] = record) -> float | None:
            value = values.get(column) if column in col else None
            return float(value) if isinstance(value, int | float) else None

        def cell(column: str, values: Mapping[str, object] = record) -> str:
            return str(values.get(column, "")).strip() if column in col else ""

        note_by, note = split_note(cell("Note"))
        raw_confidence = cell("Conf")
        confidence = float(raw_confidence) if raw_confidence.replace(".", "", 1).isdigit() else None
        tags: list[str] = []
        for column in _ANALYST_COLUMNS:
            for tag in split_list(cell(column)):
                if tag not in tags:
                    tags.append(tag)
        rows.append(
            BBMRow(
                name=name,
                position=str(record.get("Pos", "")).strip(),
                games=float(games),
                rates={key: num(column) for column, key in _RATES.items()},
                fg_pct=num("fg%"),
                ft_pct=num("ft%"),
                dollars=maybe("$"),
                injury=cell("Inj"),
                injury_risk=cell("Inj Risk"),
                age=maybe("Age"),
                espn_dollars=maybe("ESPN$"),
                yahoo_dollars=maybe("Y!Avg$"),
                league_dollars=maybe("Leag$"),
                team=cell("Team"),
                note=note,
                note_by=note_by,
                confidence=int(confidence) if confidence else None,
                role=cell("Role"),
                status=split_list(cell("Status")),
                tags=tuple(tags),
            )
        )
    return rows


def slots_for(position: str) -> frozenset[str]:
    out: set[str] = set()
    for part in position.replace(",", "/").split("/"):
        out |= _SLOTS.get(part.strip().upper(), frozenset())
    return frozenset(out)


def to_projection(
    row: BBMRow, player_id: int, *, eligible: Iterable[str], position: str | None
) -> PlayerProjection:
    """Season totals from per-game rates and projected games."""
    totals = {key: rate * row.games for key, rate in row.rates.items()}
    totals["FGM"] = row.fg_pct * totals["FGA"]
    totals["FTM"] = row.ft_pct * totals["FTA"]
    return PlayerProjection(
        player_id=player_id,
        name=row.name,
        games=row.games,
        totals=totals,
        eligible=frozenset(eligible),
        position=position,
        source=sources.BBM,
    )


@dataclass(frozen=True)
class EspnLookup:
    """What our database knows about players, for placing a foreign file's names.

    Shared by every projection file that arrives without ESPN ids -- BBM's
    export and a manager's upload (`app.projections.upload`) -- so the two
    place a name and take a player's eligibility by exactly the same rule.
    """

    #: ESPN player id -> the name we hold for him.
    known: dict[int, str]
    #: ESPN player id -> the most recent season we hold an eligible line for.
    recency: dict[int, int]
    #: ESPN player id -> our own `players.id`, for a foreign key.
    ours: dict[int, int]
    #: ESPN player id -> (eligible slots, primary position), best line only.
    lines: dict[int, tuple[frozenset[str], str | None]]

    def eligibility(self, player_id: int, position: str) -> tuple[frozenset[str], str | None, bool]:
        """Slots and position for a player, and whether ESPN's own line gave them.

        ESPN's eligibility is what the lineup constraint is checked against, so
        it wins wherever we hold it; a player ESPN has never carried takes
        slots derived from the file's position string.
        """
        held = self.lines.get(player_id)
        if held is not None:
            return held[0], held[1], True
        return slots_for(position), position.split("/")[0].strip() or None, False


def espn_lookup(session: Session, season: int) -> EspnLookup:
    """Names, recency and eligibility by ESPN player id.

    The best line for a player is this season's projection first, then his most
    recent line of any kind; lines carrying no eligible slots are ignored,
    because an empty set is unknown eligibility rather than none.
    """
    known: dict[int, str] = {}
    ours: dict[int, int] = {}
    for player_id, espn_id, name in session.execute(
        select(Player.id, Player.espn_player_id, Player.name)
    ).all():
        known[int(espn_id)] = str(name)
        ours[int(espn_id)] = int(player_id)

    best: dict[int, tuple[tuple[int, int], frozenset[str], str | None]] = {}
    recency: dict[int, int] = {}
    for espn_id, line_season, kind, slots, position in session.execute(
        select(
            Player.espn_player_id,
            PlayerSeasonStat.season,
            PlayerSeasonStat.kind,
            PlayerSeasonStat.eligible_slots,
            PlayerSeasonStat.primary_position,
        ).join(PlayerSeasonStat, PlayerSeasonStat.player_id == Player.id)
    ).all():
        eligible = frozenset(str(x) for x in (slots or []))
        if not eligible:
            continue
        pid = int(espn_id)
        rank = (1 if line_season == season and kind == "projected" else 0, int(line_season))
        recency[pid] = max(recency.get(pid, 0), int(line_season))
        held = best.get(pid)
        if held is None or rank > held[0]:
            best[pid] = (rank, eligible, position)
    return EspnLookup(
        known=known,
        recency=recency,
        ours=ours,
        lines={pid: (eligible, position) for pid, (_, eligible, position) in best.items()},
    )


def load_bbm(session: Session, path: Path, season: int) -> BBMLoad:
    """BBM's export file as projections keyed on our ESPN ids (`load_bbm_rows`)."""
    return load_bbm_rows(session, read_bbm(path), season)


def load_bbm_rows(session: Session, bbm_rows: Iterable[BBMRow], season: int) -> BBMLoad:
    """BBM's rows as projections keyed on our ESPN ids.

    Every row with a games projection becomes a projection: matched rows
    under our id, the rest under a synthetic one. The load reports what it
    matched loosely, what was ambiguous and what it could not place, so a
    run can say so.
    """
    lookup = espn_lookup(session, season)
    known = lookup.known

    out = BBMLoad(projections=[])
    seen: set[int] = set()
    for row in bbm_rows:
        found = match_player(row.name, known, lookup.recency)
        if found is not None and found in seen:
            out.ambiguous.append(f"{row.name} (a second name for {known[found]})")
            found = None
        if found is None:
            player_id = synthetic_id(row.name)
            if player_id in seen:
                continue
            out.unmatched.append(row.name)
        else:
            player_id = found
            out.matched += 1
            if name_key(row.name) != name_key(known[found]):
                out.loose.append(f"{row.name} = {known[found]}")
        seen.add(player_id)

        eligible, position, from_espn = lookup.eligibility(player_id, row.position)
        if from_espn:
            out.espn_eligibility += 1
        else:
            out.derived_eligibility += 1
        out.projections.append(to_projection(row, player_id, eligible=eligible, position=position))
        out.rows[player_id] = row
    return out
