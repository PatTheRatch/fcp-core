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
value. There is no ESPN id, so players are matched to ours by name
through `app.draft.feed.match_name`; an ambiguous name is dropped rather
than guessed, and the loader reports what it could not place. Eligible
slots come from ESPN's projected line for the same season when we hold
one, because ESPN's eligibility is what the lineup constraint is checked
against; a player ESPN never projected gets slots derived from BBM's
position, which is the only reason such a player is on the board at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerSeasonStat
from app.draft.feed import match_name
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


@dataclass
class BBMLoad:
    projections: list[PlayerProjection]
    matched: int = 0
    fuzzy: int = 0
    ambiguous: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    espn_eligibility: int = 0
    derived_eligibility: int = 0


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

        dollars = values[col["$"]] if "$" in col else None
        age = values[col["Age"]] if "Age" in col else None
        rows.append(
            BBMRow(
                name=name,
                position=str(values[col["Pos"]]).strip(),
                games=float(games),
                rates={key: num(column) for column, key in _RATES.items()},
                fg_pct=num("fg%"),
                ft_pct=num("ft%"),
                dollars=float(dollars) if isinstance(dollars, int | float) else None,
                injury=str(values[col["Inj"]]).strip() if "Inj" in col else "",
                injury_risk=str(values[col["Inj Risk"]]).strip() if "Inj Risk" in col else "",
                age=float(age) if isinstance(age, int | float) else None,
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


def load_bbm(session: Session, path: Path, season: int) -> BBMLoad:
    """BBM's export as projections keyed on our ESPN ids.

    Eligibility is ESPN's for the season where we hold it, BBM's position
    otherwise. The load reports what it matched loosely and what it could
    not place, so a run can say so rather than silently draft from a
    smaller pool than the file holds.
    """
    known: dict[str, int] = {
        str(name): int(espn_id)
        for espn_id, name in session.execute(select(Player.espn_player_id, Player.name)).all()
    }
    espn_lines: dict[int, tuple[frozenset[str], str | None]] = {
        int(espn_id): (frozenset(str(s) for s in (slots or [])), position)
        for espn_id, slots, position in session.execute(
            select(
                Player.espn_player_id,
                PlayerSeasonStat.eligible_slots,
                PlayerSeasonStat.primary_position,
            )
            .join(PlayerSeasonStat, PlayerSeasonStat.player_id == Player.id)
            .where(PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected")
        ).all()
    }

    out = BBMLoad(projections=[])
    seen: set[int] = set()
    for row in read_bbm(path):
        found = match_name(row.name, known)
        if found is None:
            out.unmatched.append(row.name)
            continue
        if found.rival:
            out.ambiguous.append(f"{row.name} ({found.name} or {found.rival})")
            continue
        if found.value in seen:
            # Two BBM names resolved to one of ours; the first wins.
            out.ambiguous.append(f"{row.name} (duplicate of {found.name})")
            continue
        seen.add(found.value)
        out.matched += 1
        out.fuzzy += found.score < 1.0
        held = espn_lines.get(found.value)
        if held and held[0]:
            eligible, position = held
            out.espn_eligibility += 1
        else:
            eligible, position = (
                _slots_for(row.position),
                row.position.split("/")[0].strip() or None,
            )
            out.derived_eligibility += 1
        out.projections.append(
            to_projection(row, found.value, eligible=eligible, position=position)
        )
    return out
