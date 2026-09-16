"""A small league built row by row, for the scoring package's database tests.

Tests take the `scoring_session` fixture (tests/conftest.py), a fresh schema
per module, then build only the rows their case needs: teams, matchup
periods, players, days in a lineup and the stat lines behind them.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    League,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Player,
    PlayerGameStat,
    Team,
    Transaction,
    TransactionItem,
)
from app.scoring.lines import COUNTS

LEAGUE_ID = 3853870
NINE = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")


def league_season(
    session: Session,
    *,
    season: int = 2026,
    team_names: tuple[str, ...] = ("Home", "Away"),
    periods: int = 2,
    regular_season_periods: int | None = None,
    days_per_period: int = 7,
) -> tuple[LeagueSeason, list[Team], list[MatchupPeriod]]:
    """A season with teams, the nine categories and `periods` matchup periods."""
    league = session.scalar(select(League).where(League.espn_league_id == LEAGUE_ID))
    if league is None:
        league = League(espn_league_id=LEAGUE_ID)
        session.add(league)
        session.flush()
    regular = periods if regular_season_periods is None else regular_season_periods
    ls = LeagueSeason(
        league_id=league.id,
        season=season,
        name=f"S{season}",
        scoring_type="H2H_CATEGORY",
        team_count=len(team_names),
        regular_season_periods=regular,
        total_matchup_periods=periods,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        auction_budget=200,
        median_scoring=False,
        raw_settings={},
    )
    session.add(ls)
    session.flush()
    for position, abbreviation in enumerate(NINE):
        session.add(
            LeagueSeasonCategory(
                league_season_id=ls.id,
                stat_id=position,
                abbreviation=abbreviation,
                position=position,
                is_reverse=abbreviation == "TO",
            )
        )
    teams = []
    for number, name in enumerate(team_names, start=1):
        team = Team(
            league_season_id=ls.id,
            espn_team_id=number,
            name=name,
            categories_won=0,
            categories_lost=0,
            categories_tied=0,
        )
        session.add(team)
        teams.append(team)
    session.flush()
    matchup_periods = []
    for index in range(periods):
        period = MatchupPeriod(
            league_season_id=ls.id,
            period=index + 1,
            is_playoff=index + 1 > regular,
            first_scoring_period=index * days_per_period + 1,
            final_scoring_period=(index + 1) * days_per_period,
        )
        session.add(period)
        matchup_periods.append(period)
    session.flush()
    return ls, teams, matchup_periods


def player(session: Session, name: str, espn_id: int | None = None) -> Player:
    existing = session.scalar(select(Player).where(Player.name == name))
    if existing is not None:
        return existing
    row = Player(espn_player_id=espn_id or zlib.crc32(name.encode()), name=name)
    session.add(row)
    session.flush()
    return row


def held(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    who: Player,
    day: int,
    *,
    slot: str = "UT",
    stats: Mapping[str, float] | None = None,
    season: int = 2026,
) -> None:
    """`who` sat in `slot` for `team` on `day`; with `stats`, he played that day.

    `stats` is keyed like a line (PTS, REB, ..., FGM, FGA, FTM, FTA).
    """
    session.add(
        DailyLineupSlot(
            team_id=team.id,
            matchup_period_id=period.id,
            scoring_period=day,
            player_id=who.id,
            slot=slot,
            started=slot not in ("BE", "IR", "FA"),
        )
    )
    if stats is not None:
        existing = session.scalar(
            select(PlayerGameStat).where(
                PlayerGameStat.player_id == who.id,
                PlayerGameStat.season == season,
                PlayerGameStat.scoring_period == day,
            )
        )
        if existing is None:
            session.add(
                PlayerGameStat(
                    player_id=who.id,
                    season=season,
                    scoring_period=day,
                    played=True,
                    minutes=30.0,
                    raw_totals={},
                    **{column: float(stats.get(key, 0.0)) for key, column in COUNTS.items()},
                )
            )
    session.flush()


def matchup(
    session: Session,
    period: MatchupPeriod,
    home: Team,
    away: Team,
    posted: Mapping[Team, Mapping[str, float]] | None = None,
) -> Matchup:
    """A matchup, optionally with the category totals each side posted."""
    row = Matchup(
        matchup_period_id=period.id,
        home_team_id=home.id,
        away_team_id=away.id,
        winner="HOME",
        home_categories_won=5,
        home_categories_lost=4,
        categories_tied=0,
    )
    session.add(row)
    session.flush()
    categories = {
        c.abbreviation: c.id
        for c in session.scalars(
            select(LeagueSeasonCategory).where(
                LeagueSeasonCategory.league_season_id == period.league_season_id
            )
        )
    }
    for team, values in (posted or {}).items():
        for abbreviation, value in values.items():
            session.add(
                MatchupTeamStat(
                    matchup_id=row.id,
                    team_id=team.id,
                    abbreviation=abbreviation,
                    value=float(value),
                    result="WIN" if abbreviation in categories else None,
                    league_season_category_id=categories.get(abbreviation),
                )
            )
    session.flush()
    return row


def transaction(
    session: Session,
    team: Team,
    day: int,
    kind: str,
    items: list[tuple[str, Player, Team | None, Team | None]],
    *,
    status: str = "EXECUTED",
) -> None:
    """A transaction on `day`: items are (item type, player, from team, to team).

    A None team is ESPN's team 0, meaning free agency. `kind` is ESPN's type
    string, so the same helper builds a waiver claim, a TRADE_ACCEPT or an
    items-less TRADE_UPHOLD (pass `items=[]`).
    """
    row = Transaction(
        league_season_id=team.league_season_id,
        # ESPN's id is unique league-wide and the table is not truncated between
        # tests, so the key has to separate rows a test builds deliberately:
        # the same kind of move on the same day by the same team is two rows
        # when it names two different players. An items-less row (a TRADE_UPHOLD,
        # which is what ESPN really sends) has no player to key on, so the item
        # count carries it.
        espn_transaction_id=(
            f"{kind}-{day}-{team.id}-{len(items)}-{items[0][1].id}"
            if items
            else f"{kind}-{day}-{team.id}"
        ),
        team_id=team.id,
        type=kind,
        status=status,
        scoring_period=day,
    )
    session.add(row)
    session.flush()
    for item_type, who, source, destination in items:
        session.add(
            TransactionItem(
                transaction_id=row.id,
                player_id=who.id,
                item_type=item_type,
                from_team_id=source.id if source else None,
                to_team_id=destination.id if destination else None,
            )
        )
    session.flush()
