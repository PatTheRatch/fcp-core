"""How this league's winning rosters spent their auction money.

The room drafts to an allocation -- dollars per roster place, largest
first -- and the obvious source for one is the optimizer's own best roster
from the empty room. On Basketball Monster's 2026 projections that roster
was $103 on Wembanyama and ten one-dollar players: the objective, at board
prices, likes stars and scrubs. The league's history does not. Across 98
team-seasons the most balanced quartile by top-three share of spend beat
the most top-heavy at every league size (scripts/top_heavy.py).

So this reads the other source: the spending shape of the rosters that
actually won categories. For each drafted team-season, its prices sorted
largest first and scaled to the budget; the teams in the top third of their
season by regular-season category win rate; the mean at each place. It is
a prior about how to spread money, measured, rather than a claim the
objective makes about itself.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DraftPick, LeagueSeason, Team


def winning_shape(
    session: Session,
    *,
    roster_slots: int,
    budget: int,
    before: int | None = None,
    top_share: float = 1 / 3,
) -> list[int]:
    """Mean sorted spend per roster place of each season's best category teams.

    Every team's prices are first scaled to `budget` and padded or trimmed
    to `roster_slots`, so seasons with other budgets and roster sizes add
    like with like. `before` restricts to seasons strictly earlier, for a
    replay of a season that has since been played.
    """
    query = (
        select(
            LeagueSeason.season,
            Team.id,
            Team.categories_won,
            Team.categories_lost,
            Team.categories_tied,
            DraftPick.bid_amount,
            LeagueSeason.auction_budget,
        )
        .join(Team, Team.league_season_id == LeagueSeason.id)
        .join(DraftPick, DraftPick.team_id == Team.id)
        .where(DraftPick.bid_amount.is_not(None))
    )
    if before is not None:
        query = query.where(LeagueSeason.season < before)

    prices: dict[tuple[int, int], list[int]] = defaultdict(list)
    rate: dict[tuple[int, int], float] = {}
    season_budget: dict[int, int] = {}
    for season, team_id, won, lost, tied, amount, auction_budget in session.execute(query).all():
        key = (int(season), int(team_id))
        prices[key].append(int(amount))
        played = won + lost + tied
        rate[key] = (won + tied / 2) / played if played else 0.0
        season_budget[int(season)] = int(auction_budget or 0) or budget

    by_season: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for key in prices:
        by_season[key[0]].append(key)

    rows: list[list[float]] = []
    for season, keys in by_season.items():
        keys.sort(key=lambda k: -rate[k])
        for key in keys[: max(1, round(len(keys) * top_share))]:
            spent = sorted(prices[key], reverse=True)[:roster_slots]
            spent += [1] * (roster_slots - len(spent))
            scale = budget / season_budget[season]
            rows.append([p * scale for p in spent])
    if not rows:
        raise ValueError("no drafted team-seasons to read a spending shape from")
    return [max(1, round(fmean(r[i] for r in rows))) for i in range(roster_slots)]
