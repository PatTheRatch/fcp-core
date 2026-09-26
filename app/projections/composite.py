"""A composite: a named source worked out from other sources with weights.

The owner (2026-09-26): "a consensus one where you can add weights and, to
the best of our ability, come up with composite projections." A composite is
a `projection_sets` row of kind `composite` whose `recipe` is the truth --
`[{"source": "bbm" | "espn" | <set id>, "weight": 0-100}]` -- and whose rows
are a cache of what the recipe works out to, materialised here so the room
reads a composite exactly as it reads an upload
(`app.projections.upload.load_projection_set`). `built_from` records what
the rows were worked out from: the BBM capture's date, each input set's
upload time, a fingerprint of ESPN's projections, and when. The rows are
rebuilt when any of those moves (`ensure_current`) and not otherwise.

HOW A ROW IS WORKED OUT

Per player, over the sources that carry him:

- **Per-game rates are the weighted mean**, the weights renormalised over
  the sources present. A man BBM has and an upload lacks is BBM's number at
  full weight, and the row says so (`raw.sources`, `raw.of`: "1 of 2").
- **Games** are the weighted mean the same way.
- **The two percentages come from weighted makes and attempts**, never from
  averaging percentages: FGM, FGA, FTM and FTA are each a weighted mean
  like any count, and the room's FG% is the makes over the attempts
  (`app/scoring/lines.py`'s rule). A 50/50 blend of a .600 shooter on 2
  attempts and a .400 shooter on 18 is .420, not .500.
- **Minutes and value** are the weighted mean over the sources that carry
  them, renormalised again; null where none does. A value is the source's
  own dollar figure (BBM's league value, an upload's value column), a
  number on the page, never read by the room.
- **Position, team and injury** come from the first source in the recipe
  that names one. There is no positional eligibility merge beyond that:
  eligibility is ESPN's own line where we hold one, as for any set.
- A player present in no source is absent. A source at weight 0 is not read.

Matching across sources is the id each source already puts a man under:
his ESPN id where the source placed him (BBM and an upload through the
strict name matcher, `app.player_names.match_player`; ESPN by its own id),
else the synthetic id of his name (`synthetic_id`), so two sources' unmatched
rows for the same man merge only on an exact `name_key`.

THE GATE

A composite that includes BBM (at any weight above none) is gated exactly
like BBM: a per-player number that is half BBM's is still BBM's number to
anyone who is not the member. Its tag says so (`sources.composite_source`,
"composite:9+bbm"), so `may_show` answers it with no lookup. One without
BBM is its owner's, like an upload. Only a viewer who may plan on BBM can
put BBM in a recipe.

WHAT IS NOT DONE

To the best of our ability, and no further:

- **No source calibration.** A weight is the manager's, not measured from a
  source's past accuracy. Which source's 2026 projection came closest to
  the season, by category, is a study for after the draft
  (docs/projection_sources.md).
- **No injury-aware blending.** A source that has a man out for half the
  season and one that has him healthy are averaged like any other two.
- **No availability promise.** The room discounts a composite's games for
  availability like an upload's or ESPN's, even when BBM is in it (BBM's
  own games already price availability; a blend makes no such promise).
- **No composite of composites.** A recipe reads BBM, ESPN and uploads.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import PlayerSeasonStat, ProjectionRow, ProjectionSet
from app.draft import bbm_store, pool
from app.draft.bbm import espn_lookup, load_bbm_rows, parse_records
from app.draft.valuation import PlayerProjection
from app.player_names import name_key, synthetic_id
from app.projections import sources
from app.projections.upload import COUNTS, named_set

#: The most a weight may be; the least is 0 (not read).
MAX_WEIGHT = 100.0


@dataclass(frozen=True)
class Part:
    """One input of a recipe: "bbm", "espn" or a set id, and its weight."""

    source: str | int
    weight: float

    @property
    def key(self) -> str:
        """How `built_from` and a row's `raw` name this input."""
        return str(self.source) if isinstance(self.source, str) else f"set:{self.source}"


@dataclass
class Line:
    """One player's line in one input, per game."""

    player_id: int
    name: str
    games: float
    rates: dict[str, float]
    minutes: float | None = None
    value: float | None = None
    position: str | None = None
    team: str | None = None
    injury: str | None = None


def parts_of(recipe: Iterable[Mapping[str, Any]] | None) -> list[Part]:
    """A stored recipe as parts, in its order."""
    out = []
    for item in recipe or ():
        raw = str(item.get("source"))
        source: str | int = int(raw) if raw.isdigit() else raw
        out.append(Part(source=source, weight=float(item.get("weight") or 0)))
    return out


# ---------------------------------------------------------------------------
# reading each input
# ---------------------------------------------------------------------------


def _rates(projection: PlayerProjection) -> dict[str, float]:
    games = projection.games or 1.0
    return {key: float(projection.totals.get(key, 0.0)) / games for key in COUNTS}


def bbm_lines(session: Session, season: int) -> dict[int, Line]:
    """BBM's newest stored capture, placed the way the room places it."""
    day = bbm_store.latest_capture(session, season)
    if day is None:
        raise ValueError(f"no Basketball Monster capture is stored for {season}")
    records, columns = bbm_store.stored_records(session, season, day, "total")
    loaded = load_bbm_rows(
        session, parse_records(records, columns, label=f"capture of {day}"), season
    )
    out: dict[int, Line] = {}
    for projection in loaded.projections:
        row = loaded.rows.get(projection.player_id)
        value = None
        if row is not None:
            value = row.league_dollars if row.league_dollars is not None else row.dollars
        out[projection.player_id] = Line(
            player_id=projection.player_id,
            name=projection.name,
            games=projection.games,
            rates=_rates(projection),
            minutes=row.minutes if row is not None else None,
            value=value,
            position=projection.position or (row.position if row is not None else None),
            team=(row.team or None) if row is not None else None,
            injury=(row.injury or None) if row is not None else None,
        )
    return out


def espn_lines(session: Session, season: int) -> dict[int, Line]:
    """ESPN's own projections for the season, under their ESPN ids."""
    minutes = {
        int(our_id): raw.get("MPG")
        for our_id, raw in session.execute(
            select(PlayerSeasonStat.player_id, PlayerSeasonStat.raw_totals).where(
                PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected"
            )
        ).all()
    }
    espn_of = {ours: espn for espn, ours in espn_lookup(session, season).ours.items()}
    by_espn = {espn_of.get(our_id): mpg for our_id, mpg in minutes.items()}
    out: dict[int, Line] = {}
    for projection in pool.load_projections(session, season, kind="projected"):
        if projection.games <= 0:
            continue
        mpg = by_espn.get(projection.player_id)
        out[projection.player_id] = Line(
            player_id=projection.player_id,
            name=projection.name,
            games=projection.games,
            rates=_rates(projection),
            minutes=float(mpg) if isinstance(mpg, int | float) else None,
            position=projection.position,
        )
    return out


def set_lines(session: Session, set_id: int) -> dict[int, Line]:
    """An uploaded set's rows, under the id `load_projection_set` gives each."""
    found = session.get(ProjectionSet, set_id)
    if found is None:
        raise ValueError(f"no projection set {set_id}")
    lookup = espn_lookup(session, found.season)
    espn_of = {ours: espn for espn, ours in lookup.ours.items()}
    out: dict[int, Line] = {}
    for row in session.scalars(
        select(ProjectionRow).where(ProjectionRow.set_id == set_id).order_by(ProjectionRow.name_key)
    ):
        espn_id = espn_of.get(row.player_id) if row.player_id is not None else None
        player_id = espn_id if espn_id is not None else synthetic_id(row.name)
        out[player_id] = Line(
            player_id=player_id,
            name=row.name,
            games=float(row.games),
            rates={key: float(getattr(row, column)) for key, column in COUNTS.items()},
            minutes=row.minutes,
            value=row.value,
            position=row.position,
            team=row.team,
            injury=row.injury,
        )
    return out


def lines_of(session: Session, season: int, part: Part) -> dict[int, Line]:
    if part.source == sources.BBM:
        return bbm_lines(session, season)
    if part.source == sources.ESPN:
        return espn_lines(session, season)
    if isinstance(part.source, int):
        return set_lines(session, part.source)
    raise ValueError(f"a recipe reads bbm, espn or one of your uploads, not {part.source!r}")


# ---------------------------------------------------------------------------
# the arithmetic
# ---------------------------------------------------------------------------


def _mean(pairs: Sequence[tuple[float, float | None]]) -> float | None:
    """The weighted mean of the values present, renormalised over them."""
    present = [(w, v) for w, v in pairs if v is not None]
    total = sum(w for w, _ in present)
    return sum(w * v for w, v in present) / total if total > 0 else None


def blend(parts: Sequence[Part], inputs: Mapping[str, Mapping[int, Line]]) -> list[dict[str, Any]]:
    """Every player any read input carries, as one blended per-game line.

    `inputs` is each part's lines by `Part.key`. Parts at weight 0 are not
    read. Order: the first input's order, then each later one's newcomers.
    """
    read = [p for p in parts if p.weight > 0]
    order: list[int] = []
    seen: set[int] = set()
    for part in read:
        for player_id in inputs[part.key]:
            if player_id not in seen:
                seen.add(player_id)
                order.append(player_id)
    out: list[dict[str, Any]] = []
    for player_id in order:
        carried = [(p, inputs[p.key][player_id]) for p in read if player_id in inputs[p.key]]
        rates = {
            key: _mean([(p.weight, line.rates.get(key, 0.0)) for p, line in carried]) or 0.0
            for key in COUNTS
        }
        first = carried[0][1]

        def pick(attr: str, carried: list[tuple[Part, Line]] = carried) -> str | None:
            return next((getattr(line, attr) for _, line in carried if getattr(line, attr)), None)

        out.append(
            {
                "player_id": player_id,
                "name": first.name,
                "games": _mean([(p.weight, line.games) for p, line in carried]) or 0.0,
                "rates": rates,
                "minutes": _mean([(p.weight, line.minutes) for p, line in carried]),
                "value": _mean([(p.weight, line.value) for p, line in carried]),
                "position": pick("position"),
                "team": pick("team"),
                "injury": pick("injury"),
                "sources": [p.key for p, _ in carried],
                "of": len(read),
            }
        )
    return out


# ---------------------------------------------------------------------------
# the cache: what the rows were worked out from
# ---------------------------------------------------------------------------


def espn_fingerprint(session: Session, season: int) -> str:
    """ESPN's projections for a season, in one string that moves when they do."""
    count, points, games = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(PlayerSeasonStat.points), 0.0),
            func.coalesce(func.sum(PlayerSeasonStat.games_played), 0.0),
        ).where(PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected")
    ).one()
    return f"{count}:{float(points):.1f}:{float(games):.1f}"


def fingerprint(session: Session, season: int, parts: Sequence[Part]) -> dict[str, str]:
    """What each read input is now: the cache key a composite's rows carry."""
    out: dict[str, str] = {}
    for part in parts:
        if part.weight <= 0:
            continue
        if part.source == sources.BBM:
            day = bbm_store.latest_capture(session, season)
            out[part.key] = day.isoformat() if day else ""
        elif part.source == sources.ESPN:
            out[part.key] = espn_fingerprint(session, season)
        elif isinstance(part.source, int):
            found = session.get(ProjectionSet, part.source)
            out[part.key] = found.uploaded_at.isoformat() if found is not None else "gone"
        out[part.key] = f"{out.get(part.key, '')}|w={part.weight:g}"
    return out


def build(session: Session, composite: ProjectionSet) -> dict[str, Any]:
    """Work the composite's rows out from its recipe and store them, replacing
    whatever was there. Returns `built_from`."""
    parts = parts_of(composite.recipe)
    read = [p for p in parts if p.weight > 0]
    if not read:
        raise ValueError("a composite needs at least one source with a weight above 0")
    inputs = {p.key: lines_of(session, composite.season, p) for p in read}
    blended = blend(parts, inputs)
    lookup = espn_lookup(session, composite.season)

    session.execute(delete(ProjectionRow).where(ProjectionRow.set_id == composite.id))
    keys: set[str] = set()
    carried: dict[str, int] = {}
    for line in blended:
        key = name_key(line["name"])
        while key in keys:  # two men of one name, one matched and one not
            key = f"{key}#"
        keys.add(key)
        carried[str(len(line["sources"]))] = carried.get(str(len(line["sources"])), 0) + 1
        session.add(
            ProjectionRow(
                set_id=composite.id,
                name=line["name"],
                name_key=key,
                player_id=lookup.ours.get(line["player_id"]),
                games=line["games"],
                position=line["position"],
                team=line["team"],
                minutes=line["minutes"],
                value=line["value"],
                injury=line["injury"],
                raw={"sources": line["sources"], "of": line["of"]},
                **{COUNTS[k]: v for k, v in line["rates"].items()},
            )
        )
    now = dt.datetime.now(dt.UTC)
    built_from = {
        "inputs": fingerprint(session, composite.season, parts),
        "at": now.isoformat(),
        "carried": carried,
    }
    composite.built_from = built_from
    composite.rows = len(blended)
    composite.uploaded_at = now
    session.flush()
    return built_from


def stale(session: Session, composite: ProjectionSet) -> bool:
    """Whether an input has moved since the rows were worked out."""
    kept = (composite.built_from or {}).get("inputs")
    return kept != fingerprint(session, composite.season, parts_of(composite.recipe))


def ensure_current(session: Session, composite: ProjectionSet) -> bool:
    """Rebuild the rows when an input changed; True when it did."""
    if composite.kind != "composite" or not stale(session, composite):
        return False
    build(session, composite)
    return True


def refresh(session: Session, season: int, *, owners: Sequence[str]) -> int:
    """Rebuild every composite of these owners' whose inputs moved; how many.
    One whose input cannot be read (a capture gone) is left as it was."""
    rebuilt = 0
    for one in session.scalars(
        select(ProjectionSet).where(
            ProjectionSet.kind == "composite",
            ProjectionSet.season == season,
            ProjectionSet.owner.in_(list(owners)),
        )
    ):
        try:
            rebuilt += ensure_current(session, one)
        except ValueError:
            continue
    return rebuilt


def dependents(session: Session, set_id: int) -> list[ProjectionSet]:
    """The composites whose recipe reads this set."""
    found = session.get(ProjectionSet, set_id)
    if found is None:
        return []
    return [
        one
        for one in session.scalars(
            select(ProjectionSet).where(
                ProjectionSet.kind == "composite",
                ProjectionSet.season == found.season,
                ProjectionSet.owner == found.owner,
            )
        )
        if any(p.source == set_id for p in parts_of(one.recipe))
    ]


# ---------------------------------------------------------------------------
# making and changing one
# ---------------------------------------------------------------------------


def check_recipe(
    session: Session,
    *,
    season: int,
    owners: Sequence[str],
    owns_bbm: bool,
    recipe: Sequence[Mapping[str, Any]],
) -> list[Part]:
    """The recipe as parts, or a ValueError naming what is wrong with it."""
    parts = parts_of(recipe)
    if not parts:
        raise ValueError("a composite needs at least one source")
    seen: set[str] = set()
    for part in parts:
        if not 0 <= part.weight <= MAX_WEIGHT:
            raise ValueError(
                f"a weight is from 0 to {MAX_WEIGHT:g}; {part.key} has {part.weight:g}"
            )
        if part.key in seen:
            raise ValueError(f"{part.key} is in the recipe twice")
        seen.add(part.key)
        if part.source == sources.BBM:
            if not owns_bbm:
                raise ValueError(
                    "Basketball Monster's numbers are private to the member whose account "
                    "fetched them, so a composite of yours cannot read them"
                )
            if bbm_store.latest_capture(session, season) is None:
                raise ValueError(f"no Basketball Monster capture is stored for {season}")
        elif part.source == sources.ESPN:
            continue
        elif isinstance(part.source, int):
            found = session.get(ProjectionSet, part.source)
            if found is None or found.owner not in owners:
                raise ValueError(f"no projection set {part.source} of yours")
            if found.kind != "upload":
                raise ValueError(f"{found.name!r} is a composite; a recipe reads uploads")
            if found.season != season:
                raise ValueError(f"{found.name!r} is for {found.season}, not {season}")
        else:
            raise ValueError(
                f"a recipe reads bbm, espn or one of your uploads, not {part.source!r}"
            )
    if not any(p.weight > 0 for p in parts):
        raise ValueError("a composite needs at least one source with a weight above 0")
    return parts


def save(
    session: Session,
    *,
    season: int,
    owner: str,
    owners: Sequence[str],
    owns_bbm: bool,
    name: str,
    recipe: Sequence[Mapping[str, Any]],
    composite: ProjectionSet | None = None,
) -> ProjectionSet:
    """Make a composite (or change one's name and weights) and build its rows."""
    name = name.strip()
    if not name:
        raise ValueError("a composite needs a name")
    parts = check_recipe(session, season=season, owners=owners, owns_bbm=owns_bbm, recipe=recipe)
    taken = named_set(
        session, owner=owner if composite is None else composite.owner, season=season, name=name
    )
    if taken is not None and (composite is None or taken.id != composite.id):
        raise ValueError(f"you already have a source called {name!r} for {season}")
    if composite is None:
        composite = ProjectionSet(season=season, name=name, owner=owner, kind="composite")
        composite.source_note = ""
        composite.column_map = {}
        session.add(composite)
    composite.name = name
    composite.recipe = [{"source": p.source, "weight": p.weight} for p in parts]
    session.flush()
    build(session, composite)
    return composite


def recipe_words(session: Session, recipe: Iterable[Mapping[str, Any]] | None) -> str:
    """A recipe in words: "70% Odd sheet + 30% ESPN" (shares of the total weight)."""
    parts = [p for p in parts_of(recipe) if p.weight > 0]
    total = sum(p.weight for p in parts) or 1.0
    names = []
    for part in parts:
        if part.source == sources.BBM:
            label = "BBM"
        elif part.source == sources.ESPN:
            label = "ESPN"
        else:
            found = session.get(ProjectionSet, part.source)
            label = found.name if found is not None else f"set {part.source}"
        names.append(f"{round(100 * part.weight / total)}% {label}")
    return " + ".join(names)
