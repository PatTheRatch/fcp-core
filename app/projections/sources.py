"""Which projections a number came from, and who is allowed to see it.

Basketball Monster's projections are behind a paid membership and nobody has
given permission to republish them, so BBM's rows -- and the board prices,
ceilings and target rosters computed from them per player -- stay private to
the member whose account fetched them (`docs/projection_sources.md`). ESPN's
projections are already ours to show, and a manager's own upload is his.

The rule cannot be a thing people remember, so the source rides with the
numbers: every `PlayerProjection` carries one, a `Room` carries the source its
pool was built from, and the two page builders ask `may_show` before they
render. The ownership half of that question needs an account, which does not
exist yet; until it does the builders pass `viewer_owns_source=True`, which is
true of the only person running this. The seam is here so that when auth
lands, one function learns the answer and nothing else moves.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.draft.valuation import PlayerProjection

#: ESPN's own season projections, ingested for every season. Public-safe:
#: they came with the league we are in.
ESPN = "espn"

#: Basketball Monster's export. Paid, and the only gated source.
BBM = "bbm"

#: A manager's uploaded set, tagged with the row id of the set it came from,
#: e.g. "upload:7". Public-safe: they are his numbers, not ours to gate.
UPLOAD_PREFIX = "upload:"


#: A composite: a named source worked out from other sources with weights
#: (`app.projections.composite`), tagged with its set id, e.g. "composite:9".
#: One whose recipe carries BBM is tagged "composite:9+bbm", and is gated
#: exactly like BBM: a number that is half BBM's is still BBM's number.
COMPOSITE_PREFIX = "composite:"
WITH_BBM = "+bbm"


def upload_source(set_id: int) -> str:
    """The source tag for one stored projection set."""
    return f"{UPLOAD_PREFIX}{set_id}"


def recipe_has_bbm(recipe: Iterable[Mapping[str, Any]] | None) -> bool:
    """Whether a composite's recipe reads Basketball Monster at any weight above none."""
    return any(
        str(part.get("source")) == BBM and float(part.get("weight") or 0) > 0
        for part in recipe or ()
    )


def composite_source(set_id: int, recipe: Iterable[Mapping[str, Any]] | None) -> str:
    """The source tag for a composite: its gate is learned from its recipe."""
    return f"{COMPOSITE_PREFIX}{set_id}{WITH_BBM if recipe_has_bbm(recipe) else ''}"


def composite_set_id(source: str) -> int | None:
    """The set id behind a composite tag or choice, or None for any other source."""
    if not source.startswith(COMPOSITE_PREFIX):
        return None
    tail = source[len(COMPOSITE_PREFIX) :].removesuffix(WITH_BBM)
    return int(tail) if tail.isdigit() else None


def set_source(projection_set: Any) -> str:
    """The tag for a stored set of either kind (`projection_sets.kind`)."""
    if getattr(projection_set, "kind", "upload") == "composite":
        return composite_source(int(projection_set.id), projection_set.recipe)
    return upload_source(int(projection_set.id))


def choice_of(source: str) -> str:
    """What a page or a URL names a source by: the tag without its gate mark."""
    return source.removesuffix(WITH_BBM) if source.startswith(COMPOSITE_PREFIX) else source


def upload_set_id(source: str) -> int | None:
    """The set id behind an upload tag, or None for any other source."""
    if not source.startswith(UPLOAD_PREFIX):
        return None
    tail = source[len(UPLOAD_PREFIX) :]
    return int(tail) if tail.isdigit() else None


def is_gated(source: str) -> bool:
    """Whether per-player numbers from this source may leave their owner.

    Only BBM's are, and a composite's whose recipe carries BBM (its tag says
    so, `composite_source`). An uploaded set belongs to the manager who
    uploaded it and an ESPN line belongs to the league, so neither is
    withheld from anyone who can already see the room.
    """
    if source.startswith(COMPOSITE_PREFIX):
        return source.endswith(WITH_BBM)
    return source == BBM


def may_show(source: str, viewer_owns_source: bool) -> bool:
    """Whether this viewer may be shown numbers from this source.

    The whole gate, in one place: an ungated source is always shown, a gated
    one only to the account that supplied it. `viewer_owns_source` is the part
    auth will answer; today every caller is the owner and passes True.
    """
    return not is_gated(source) or viewer_owns_source


def sources_in(projections: Iterable[PlayerProjection]) -> set[str]:
    """Every source represented in a pool, for a readout or a gate check."""
    return {projection.source for projection in projections}


def describe(source: str, detail: str = "") -> str:
    """The source in the words a page should use, as one line.

    `detail` is the part this module cannot know and the page should still
    say: when a BBM export was pulled, what an uploaded set is called and
    where its numbers came from. A room carries it (`Room.source_detail`) so
    the plan page and the draft screen name a source the same way.
    """
    named = _named(source)
    detail = detail.strip()
    return f"{named}, {detail}" if detail else named


def _named(source: str) -> str:
    if source == ESPN:
        return "ESPN's projections"
    if source == BBM:
        return "Basketball Monster (paid; not to be shared)"
    set_id = upload_set_id(source)
    if set_id is not None:
        return f"uploaded projection set {set_id}"
    set_id = composite_set_id(source)
    if set_id is not None:
        with_bbm = " (carries Basketball Monster's paid numbers; not to be shared)"
        return f"composite source {set_id}{with_bbm if source.endswith(WITH_BBM) else ''}"
    return source
