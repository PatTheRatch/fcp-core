"""Loading the draftable pool out of the database.

The valuation is a pure function over projections; this is the only part
that knows where projections come from.
"""

from collections.abc import Sequence
from statistics import median

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    LeagueSeason,
    LeagueSeasonCategory,
    MatchupPeriod,
    Player,
    PlayerGameStat,
    PlayerSeasonStat,
)
from app.draft.lineup import lineup_from_settings
from app.draft.projections import projection_problem
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
    session: Session,
    season: int,
    *,
    kind: str = "projected",
    min_games: float = 0.0,
    allow_unusable: bool = False,
) -> list[PlayerProjection]:
    """Every player with a stored season line, as projections.

    `kind` is "projected" for the forecast and "total" for what actually
    happened, which is what makes a backtest possible: value the field on
    what was known beforehand, then score it against what followed.

    A season whose stored projections are not a forecast (see
    `app.draft.projections`) is refused with the reason, because a board
    built on it looks right and is not. `allow_unusable` is for the
    calibration scripts, which load it on purpose and flag it in their
    output; nothing that plans a draft should pass it.

    Raw totals are used rather than the parsed columns, because a valuation
    needs the shooting components and those are not all promoted to columns.
    """
    problem = projection_problem(season)
    if kind == "projected" and problem and not allow_unusable:
        raise ValueError(
            f"{season} projections are not usable as a forecast: {problem}. "
            "Pass allow_unusable=True only to study them, never to draft on them."
        )
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


#: How many ordinary weeks a season's production is spread across, when there
#: is no played season to measure it from. Measured 2026-09-14: 23.4 in 2022,
#: 23.8 in 2024, 23.2 in 2025, 23.4 in 2026.
DEFAULT_EFFECTIVE_WEEKS = 23.4

#: Seasons left out of the measure: 2020 was cut short by COVID.
_WEEKS_EXCLUDED = (2020,)


def weeks_from(season_total: float, ordinary_weeks: Sequence[float]) -> float:
    """A season total divided by what an ordinary week of it looks like."""
    weeks = sorted(w for w in ordinary_weeks if w > 0)
    if season_total <= 0 or not weeks:
        return DEFAULT_EFFECTIVE_WEEKS
    return season_total / median(weeks)


def effective_weeks(session: Session, *, before: int | None = None) -> float:
    """How many ordinary weeks a player's season total is really spread over.

    A projection is a season total, and a category is won in a week, so the
    total has to be turned into a week. Dividing by the league's regular-season
    matchup periods was wrong twice over. The NBA season runs past the fantasy
    regular season into the fantasy playoffs, so production is spread across
    more weeks than the regular season has: league-wide points divided by an
    ordinary seven-day week come to 23.4 in every season measured, against 16
    to 19 regular-season periods. Dividing by 19 overstated every roster's
    weekly line by 23%, which is most of why the optimizer expected six and
    more categories a week that no team in the league has won. And the
    setting moves: ESPN's 2027 settings switched to 15 regular-season periods
    before the schedule was finished, which would have overstated it by 56%.

    So the divisor is measured from game logs: each played season's league-wide
    points over its median seven-day regular-season week, the median across
    seasons. `before` keeps a replayed season out of its own measure.
    """
    seasons = [
        int(season)
        for (season,) in session.execute(
            select(PlayerGameStat.season).group_by(PlayerGameStat.season)
        ).all()
        if int(season) not in _WEEKS_EXCLUDED and (before is None or int(season) < before)
    ]
    length = cast(
        MatchupPeriod.final_scoring_period - MatchupPeriod.first_scoring_period + 1, Integer
    )
    measured: list[float] = []
    for season in seasons:
        total = session.scalar(
            select(func.sum(PlayerGameStat.points)).where(
                PlayerGameStat.season == season, PlayerGameStat.played.is_(True)
            )
        )
        windows = session.execute(
            select(MatchupPeriod.first_scoring_period, MatchupPeriod.final_scoring_period)
            .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
            .where(
                LeagueSeason.season == season,
                MatchupPeriod.is_playoff.is_(False),
                length == 7,
            )
        ).all()
        weekly = []
        for first, last in windows:
            points = session.scalar(
                select(func.sum(PlayerGameStat.points)).where(
                    PlayerGameStat.season == season,
                    PlayerGameStat.played.is_(True),
                    PlayerGameStat.scoring_period.between(first, last),
                )
            )
            weekly.append(float(points or 0.0))
        if total and weekly:
            measured.append(weeks_from(float(total), weekly))
    return median(measured) if measured else DEFAULT_EFFECTIVE_WEEKS
