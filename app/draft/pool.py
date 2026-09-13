"""Loading the draftable pool out of the database.

The valuation is a pure function over projections; this is the only part
that knows where projections come from.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, LeagueSeasonCategory, Player, PlayerSeasonStat
from app.draft.lineup import lineup_from_settings
from app.draft.valuation import PERCENTAGE_COMPONENTS, PlayerProjection

#: Stats a valuation needs beyond the scored categories themselves: the made
#: and attempted totals that sit behind the percentages.
_COMPONENT_KEYS = tuple(key for pair in PERCENTAGE_COMPONENTS.values() for key in pair)


def season_categories(session: Session, league_season: LeagueSeason) -> list[str]:
    """The categories this season scores, in ESPN's display order."""
    return list(
        session.scalars(
            select(LeagueSeasonCategory.abbreviation)
            .where(LeagueSeasonCategory.league_season_id == league_season.id)
            .order_by(LeagueSeasonCategory.position)
        ).all()
    )


def load_projections(
    session: Session, season: int, *, kind: str = "projected", min_games: float = 0.0
) -> list[PlayerProjection]:
    """Every player with a stored season line, as projections.

    `kind` is "projected" for the forecast and "total" for what actually
    happened, which is what makes a backtest possible: value the field on
    what was known beforehand, then score it against what followed.

    Raw totals are used rather than the parsed columns, because a valuation
    needs the shooting components and those are not all promoted to columns.
    """
    rows = session.execute(
        select(Player.espn_player_id, Player.name, PlayerSeasonStat)
        .join(PlayerSeasonStat, PlayerSeasonStat.player_id == Player.id)
        .where(PlayerSeasonStat.season == season, PlayerSeasonStat.kind == kind)
    ).all()

    pool: list[PlayerProjection] = []
    for espn_player_id, name, stat in rows:
        games = float(stat.games_played or 0.0)
        if games < min_games:
            continue
        totals = {
            key: float(value)
            for key, value in (stat.raw_totals or {}).items()
            if isinstance(value, int | float)
        }
        if not totals:
            continue
        pool.append(
            PlayerProjection(
                player_id=int(espn_player_id),
                name=str(name),
                games=games,
                totals=totals,
                eligible=frozenset(str(slot) for slot in (stat.eligible_slots or [])),
                position=stat.primary_position,
            )
        )
    return pool


def drafted_prices(session: Session, league_season: LeagueSeason) -> dict[int, int]:
    """ESPN player id -> what the league actually paid at the draft."""
    from app.db.models import DraftPick

    rows = session.execute(
        select(Player.espn_player_id, DraftPick.bid_amount)
        .join(DraftPick, DraftPick.player_id == Player.id)
        .where(
            DraftPick.league_season_id == league_season.id,
            DraftPick.bid_amount.is_not(None),
        )
    ).all()
    return {int(player_id): int(paid) for player_id, paid in rows}


def lineup_for(league_season: LeagueSeason) -> tuple[str, ...]:
    """The season's starting lineup, from its own stored settings."""
    return lineup_from_settings(league_season.lineup_slots or {})


def position_limits_for(league_season: LeagueSeason) -> dict[str, int]:
    """The season's caps on primary position, e.g. {"C": 3}."""
    return dict(league_season.position_limits or {})


def roster_size_for(league_season: LeagueSeason) -> int:
    """Starters plus bench. Injured reserve is not a roster place for the
    draft: it holds players who are already hurt, not ones you draft into."""
    starters = sum((league_season.lineup_slots or {}).values())
    return int(starters) + int(league_season.bench_slots or 0)


def roster_slots(session: Session, league_season: LeagueSeason) -> int:
    """How many players a team drafts, taken from the draft that happened."""
    from app.db.models import DraftPick

    rounds = session.scalar(
        select(DraftPick.round_num)
        .where(DraftPick.league_season_id == league_season.id)
        .order_by(DraftPick.round_num.desc())
        .limit(1)
    )
    return int(rounds) if rounds else 0


def pool_size_for(league_season: LeagueSeason, slots: int) -> int:
    """How many players will actually be rostered, so how wide the pool is."""
    return max(1, int(league_season.team_count) * max(1, slots))


def category_keys_needed(categories: Sequence[str]) -> tuple[str, ...]:
    """Every stat key a valuation of these categories has to find."""
    plain = tuple(c for c in categories if c not in PERCENTAGE_COMPONENTS)
    return (*plain, *_COMPONENT_KEYS)
