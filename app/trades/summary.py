"""The plain-English line under a trade, generated from its own numbers.

The house voice of `scripts/season.py` and the digest: what the deal does,
where it does it, and what it costs, with no verdict word in it. "Clears the
0.20 bar" or "does not" is the most it ever says, because the bar labels a
move and never hides one -- the manager decides.

Three things make a sentence a manager can act on rather than a number he has
to interpret:

- **Where the gain is.** The categories whose *win probability* moved, not the
  categories whose counts moved. A trade that adds forty points a week to a
  team already winning points has not gained anything, and the probability is
  what says so.
- **What he was already losing.** A category given away that was under a third
  likely to be won is a category he was already losing, and the sentence says
  that rather than listing it as a cost.
- **The rate he is punting.** A percentage whose count moves a long way while
  its probability does not is the punt working, and it is worth a clause of
  its own: it is the most common reason a manager mistrusts a trade tool.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # A cycle at runtime: the report builds its own summary.
    from app.trades.evaluate import CategoryView, SideReport

#: How a category is said out loud.
WORDS = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "3PM": "threes",
    "TO": "turnovers",
    "FG%": "field-goal percentage",
    "FT%": "free-throw percentage",
}

#: Below this the category was being lost anyway; above it, won anyway.
LOSING = 0.35
WINNING = 0.65

#: A count that moves by this share of itself while the probability does not
#: is a category the roster is punting.
PUNT_COUNT_SHARE = 0.08

#: Categories named on either side of the sentence. Two is a phrase; four is
#: a list, and a list is not a summary.
NAMED = 2

#: How far the playoff weeks have to differ from the rest of the season, in
#: categories a week, before the summary mentions them separately. Below this
#: the two lenses are saying the same thing twice.
PLAYOFF_GAP = 0.05


def words(abbreviation: str) -> str:
    return WORDS.get(abbreviation, abbreviation)


def join(names: Sequence[str]) -> str:
    """ "blocks", "blocks and rebounds", "blocks, rebounds and steals"."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def summarise(side: SideReport) -> str:
    """Two to four sentences about one side of a deal, from its own numbers."""
    return " ".join(
        sentence
        for sentence in (
            _headline(side),
            _where(side),
            _cost(side),
            _bar(side),
        )
        if sentence
    )


def _headline(side: SideReport) -> str:
    net, per_week = side.net, side.per_week
    weeks = side.judgement.weeks_covered
    if abs(net) < 0.05:
        return (
            f"Worth about nothing either way over the {weeks:.0f} weeks it covers "
            f"({net:+.2f} categories)."
        )
    direction = "Gains" if net > 0 else "Costs"
    return (
        f"{direction} about {abs(per_week):.2f} categories a week over the "
        f"{weeks:.0f} weeks it covers, {net:+.2f} on the season."
    )


def _where(side: SideReport) -> str:
    """Which categories the chance of winning actually moved in, and how far.

    "Nearly all of it in" is only said of a deal that gains on the whole: a
    losing deal's best categories are not what it is mostly doing, and the
    first draft of this function said "Costs about 0.03 a week. Nearly all of
    it in field-goal percentage", which is a sentence about nothing.
    """
    moved = side.moved()
    gains = [view for view in moved if view.p_delta > 0][:NAMED]
    losses = [view for view in moved if view.p_delta < 0][:NAMED]
    parts: list[str] = []
    if gains:
        named = join([words(view.abbreviation) for view in gains])
        share = sum(view.p_delta for view in gains)
        total = sum(view.p_delta for view in moved if view.p_delta > 0)
        dominant = side.net > 0 and total and share / total > 0.8
        parts.append(f"nearly all of it in {named}" if dominant else f"gains {named}")
    if losses:
        parts.append(f"gives up {join([words(view.abbreviation) for view in losses])}")
        if all(view.p_before < LOSING for view in losses):
            parts[-1] += " it was already losing"
        elif all(view.p_before > WINNING and view.p_after > WINNING for view in losses):
            parts[-1] += " without giving up the category"
    if not parts:
        return "No category's chance of being won moves by a point."
    return _sentence(parts)


def _sentence(parts: Sequence[str]) -> str:
    """Clauses joined by semicolons, capitalised, with a full stop."""
    joined = "; ".join(parts)
    return joined[0].upper() + joined[1:] + "."


def _punted(side: SideReport) -> list[CategoryView]:
    """Categories whose counts move a long way and whose chances do not."""
    return [
        view
        for view in side.categories
        if not view.moved
        and view.before
        and abs(view.delta) / abs(view.before) >= PUNT_COUNT_SHARE
        and view.abbreviation in ("FG%", "FT%", "TO")
    ]


def _cost(side: SideReport) -> str:
    parts: list[str] = []
    week = side.judgement.delta_week
    if abs(week) >= 0.05:
        verb = "adds" if week > 0 else "costs"
        parts.append(f"It {verb} {abs(week):.2f} categories in the week in front of it")
    punted = _punted(side)
    if punted:
        named = join([words(view.abbreviation) for view in punted[:NAMED]])
        parts.append(f"the {named} it moves costs it nothing")
    if side.drops:
        who = join([card.name for card in side.drops])
        cost = sum(card.value for card in side.drops)
        how = "named" if side.drop_source == "named" else "the cheapest place on the roster"
        parts.append(f"{who} goes to make room ({how}, {cost:.2f} categories a week)")
    if side.places_opened:
        parts.append(
            f"it leaves {side.places_opened} place(s) open, worth {side.replacement:.2f} a "
            "week on the wire"
        )
    if not parts:
        return ""
    return _sentence(parts)


def _bar(side: SideReport) -> str:
    playoffs = side.playoffs
    bar = (
        f"Clears the {side.hurdle:.2f} bar."
        if side.clears
        else f"Does not clear the {side.hurdle:.2f} bar."
    )
    apart = abs(playoffs.delta_per_week - side.judgement.delta_season_per_week)
    if playoffs.measurable and apart > PLAYOFF_GAP:
        return f"{bar} Over the playoff weeks alone it is {playoffs.delta_per_week:+.2f} a week."
    return bar
