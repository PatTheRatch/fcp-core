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
import zlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerSeasonStat
from app.draft.feed import normalise
from app.draft.valuation import PlayerProjection

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


def read_bbm(path: Path) -> list[BBMRow]:
    """Every row of the export with a games projection."""
    import xlrd  # type: ignore[import-untyped]  # optional; only this loader needs it

    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    header = [str(v).strip() for v in sheet.row_values(0)]
    col = {name: index for index, name in enumerate(header)}
    required = ("Name", "Pos", "g", "fg%", "ft%", *_RATES)
    missing = [c for c in required if c not in col]
    if missing:
        raise ValueError(f"{path.name}: not a BBM projection export, missing {missing}")

    rows: list[BBMRow] = []
    for r in range(1, sheet.nrows):
        values = sheet.row_values(r)
        name = str(values[col["Name"]]).strip()
        games = values[col["g"]]
        if not name or not isinstance(games, int | float) or games <= 0:
            continue

        def num(column: str, row_values: list[object] = values) -> float:
            value = row_values[col[column]]
            return float(value) if isinstance(value, int | float) else 0.0

        def maybe(column: str, row_values: list[object] = values) -> float | None:
            value = row_values[col[column]] if column in col else None
            return float(value) if isinstance(value, int | float) else None

        rows.append(
            BBMRow(
                name=name,
                position=str(values[col["Pos"]]).strip(),
                games=float(games),
                rates={key: num(column) for column, key in _RATES.items()},
                fg_pct=num("fg%"),
                ft_pct=num("ft%"),
                dollars=maybe("$"),
                injury=str(values[col["Inj"]]).strip() if "Inj" in col else "",
                injury_risk=str(values[col["Inj Risk"]]).strip() if "Inj Risk" in col else "",
                age=maybe("Age"),
                espn_dollars=maybe("ESPN$"),
                yahoo_dollars=maybe("Y!Avg$"),
            )
        )
    return rows


def _slots_for(position: str) -> frozenset[str]:
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
    )


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def name_key(name: str) -> str:
    """A name with case, accents, joining punctuation and suffixes gone."""
    joined = re.sub(r"[.'\u2019]", "", name)
    return " ".join(_SUFFIX.sub(" ", normalise(joined)).split())


def synthetic_id(name: str) -> int:
    """A stable negative id for a player we hold no ESPN id for."""
    return -(zlib.crc32(name_key(name).encode()) % 1_000_000_000 + 1)


def match_player(name: str, known: Mapping[int, str], recency: Mapping[int, int]) -> int | None:
    """Our id for a BBM name, or None when there is no safe match.

    Exact on `name_key` first; among several exact matches the one with the
    most recent season line wins, and a tie is no match. Failing that, the
    same surname and the rest of the name equal, with one first name the
    start of the other, if exactly one player fits.
    """
    wanted = name_key(name)
    if not wanted:
        return None
    exact = [pid for pid, known_name in known.items() if name_key(known_name) == wanted]
    if exact:
        exact.sort(key=lambda pid: -recency.get(pid, 0))
        if len(exact) > 1 and recency.get(exact[0], 0) == recency.get(exact[1], 0):
            return None
        return exact[0]
    first, _, rest = wanted.partition(" ")
    if not rest or len(first) < 2:
        return None
    loose = []
    for pid, known_name in known.items():
        other_first, _, other_rest = name_key(known_name).partition(" ")
        if other_rest == rest and (other_first.startswith(first) or first.startswith(other_first)):
            loose.append(pid)
    return loose[0] if len(loose) == 1 else None


def load_bbm(session: Session, path: Path, season: int) -> BBMLoad:
    """BBM's export as projections keyed on our ESPN ids.

    Every row with a games projection becomes a projection: matched rows
    under our id, the rest under a synthetic one. The load reports what it
    matched loosely, what was ambiguous and what it could not place, so a
    run can say so.
    """
    known: dict[int, str] = {
        int(espn_id): str(name)
        for espn_id, name in session.execute(select(Player.espn_player_id, Player.name)).all()
    }
    # For each player, ESPN's eligibility: this season's projection first,
    # then his most recent line of any kind.
    lines: dict[int, list[tuple[tuple[int, int], frozenset[str], str | None]]] = defaultdict(list)
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
        rank = (1 if line_season == season and kind == "projected" else 0, int(line_season))
        lines[int(espn_id)].append((rank, eligible, position))
    recency = {pid: max(rank[1] for rank, _, _ in held) for pid, held in lines.items()}

    out = BBMLoad(projections=[])
    seen: set[int] = set()
    for row in read_bbm(path):
        found = match_player(row.name, known, recency)
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

        held = max(lines.get(player_id, []), key=lambda line: line[0], default=None)
        if held is not None:
            _, eligible, position = held
            out.espn_eligibility += 1
        else:
            eligible = _slots_for(row.position)
            position = row.position.split("/")[0].strip() or None
            out.derived_eligibility += 1
        out.projections.append(to_projection(row, player_id, eligible=eligible, position=position))
        out.rows[player_id] = row
    return out
