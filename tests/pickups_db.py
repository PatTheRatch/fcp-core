"""Rows the pickup recommender reads, built one at a time for its tests.

Extends `tests/scoring_db.py` (league, teams, periods, lineup days, matchup
totals) with the listener's tables: status snapshots, the NBA schedule, the
free-agent pool, minutes events, and a player's eligibility and projection.
Tests take `scoring_session` and call `clear_schedule` first, because
`pro_team_games` hangs off no league and so survives the fixture's truncate.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import (
    FreeAgentSnapshot,
    LeagueSeason,
    Player,
    PlayerGameStat,
    PlayerSeasonStat,
    PlayerStatusEvent,
    PlayerStatusSnapshot,
    ProTeamGame,
    Team,
    Transaction,
    TransactionItem,
)
from app.draft.targets import CategoryDistribution
from app.scoring.lines import COUNTS
from tests.scoring_db import NINE

SEASON = 2026
#: Scoring period 1 is this day; period N is N-1 days later, the shape the
#: stored schedule has.
OPENING = date(2025, 10, 21)
OBSERVED = datetime(2025, 10, 20, 15, 0, tzinfo=UTC)

#: This league's real lineup, for the state tests; the streaming tests use a
#: smaller one so a full day is three men.
LINEUP = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3}
SMALL_LINEUP = {"G": 1, "F": 1, "UT": 1}

GUARD = ["PG", "SG", "G", "UT", "BE", "IR"]
FORWARD = ["SF", "PF", "F", "UT", "BE", "IR"]
CENTRE = ["C", "F", "UT", "BE", "IR"]
ANY = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "BE", "IR"]


def clear_schedule(session: Session) -> None:
    session.execute(text("TRUNCATE pro_team_games RESTART IDENTITY"))
    session.commit()


def configure(
    league_season: LeagueSeason,
    *,
    lineup: Mapping[str, int] = LINEUP,
    bench: int = 3,
    injured_reserve: int = 1,
    limits: Mapping[str, int] | None = None,
) -> None:
    """The season's roster rules, which `scoring_db.league_season` leaves blank."""
    league_season.lineup_slots = dict(lineup)
    league_season.bench_slots = bench
    league_season.injured_reserve_slots = injured_reserve
    league_season.position_limits = dict(limits or {})


def day_date(day: int) -> date:
    return OPENING + timedelta(days=day - 1)


def games(session: Session, pro_team_id: int, days: list[int], *, season: int = SEASON) -> None:
    """The NBA team's games, one per scoring period named."""
    for day in days:
        session.add(
            ProTeamGame(
                season=season,
                pro_team_id=pro_team_id,
                scoring_period=day,
                game_at=datetime.combine(day_date(day), datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=23),
                opponent_pro_team_id=99,
                home=True,
            )
        )
    session.flush()


def snapshot(
    session: Session,
    who: Player,
    *,
    pro_team_id: int,
    on_team_id: int | None = 0,
    injury_status: str = "ACTIVE",
    expected_return_date: date | None = None,
    observed_at: datetime = OBSERVED,
    season: int = SEASON,
) -> PlayerStatusSnapshot:
    row = PlayerStatusSnapshot(
        player_id=who.id,
        season=season,
        observed_at=observed_at,
        pass_label="morning",
        injury_status=injury_status,
        injured=injury_status == "OUT",
        expected_return_date=expected_return_date,
        pro_team_id=pro_team_id,
        on_team_id=on_team_id,
        status="ONTEAM" if on_team_id else "FREEAGENT",
    )
    session.add(row)
    session.flush()
    return row


def eligible(
    session: Session,
    who: Player,
    slots: list[str],
    position: str = "PG",
    *,
    season: int = SEASON,
) -> None:
    """The player's eligibility this season, on a total line with no stats."""
    session.add(
        PlayerSeasonStat(
            player_id=who.id,
            season=season,
            kind="total",
            eligible_slots=slots,
            primary_position=position,
            raw_totals={},
        )
    )
    session.flush()


def projected(
    session: Session,
    who: Player,
    games_played: int,
    per_game: Mapping[str, float],
    *,
    season: int = SEASON,
) -> None:
    """ESPN's preseason projection: `per_game` rates over `games_played` games."""
    session.add(
        PlayerSeasonStat(
            player_id=who.id,
            season=season,
            kind="projected",
            games_played=games_played,
            raw_totals={},
            **{
                column: float(per_game.get(key, 0.0)) * games_played
                for key, column in COUNTS.items()
            },
        )
    )
    session.flush()


def played(
    session: Session,
    who: Player,
    day: int,
    minutes: float,
    stats: Mapping[str, float],
    *,
    season: int = SEASON,
) -> None:
    """A game the player played on `day`, keyed like a line."""
    session.add(
        PlayerGameStat(
            player_id=who.id,
            season=season,
            scoring_period=day,
            played=True,
            minutes=minutes,
            raw_totals={},
            **{column: float(stats.get(key, 0.0)) for key, column in COUNTS.items()},
        )
    )
    session.flush()


def minutes_event(
    session: Session,
    who: Player,
    kind: str,
    *,
    through: int,
    recent_mean: float,
    prior_mean: float,
    observed_at: datetime = OBSERVED,
    season: int = SEASON,
) -> None:
    session.add(
        PlayerStatusEvent(
            player_id=who.id,
            season=season,
            kind=kind,
            observed_at=observed_at,
            previous={},
            current={},
            detail={
                "recent_mean": recent_mean,
                "prior_mean": prior_mean,
                "recent_games": 3,
                "prior_games": 10,
                "through_scoring_period": through,
            },
        )
    )
    session.flush()


def on_the_wire(
    session: Session,
    league_season: LeagueSeason,
    who: Player,
    *,
    observed_at: datetime = OBSERVED,
    scoring_period: int = 1,
    status: str = "FREEAGENT",
    clears_at: datetime | None = None,
) -> None:
    """The pool saw him unrostered. `status` WAIVERS with `clears_at` is a
    man on waivers, who cannot play for us until the day he clears."""
    session.add(
        FreeAgentSnapshot(
            league_season_id=league_season.id,
            observed_at=observed_at,
            scoring_period=scoring_period,
            player_id=who.id,
            status=status,
            waiver_clears_at=clears_at,
        )
    )
    session.flush()


def clears_waivers_on(day: int) -> datetime:
    """Noon on the day scoring period `day` falls on, as ESPN times a claim."""
    return datetime.combine(day_date(day), datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)


def winning_bid(session: Session, team: Team, day: int, amount: int, who: Player) -> None:
    """An executed waiver claim by `team` for `amount` of FAAB, adding `who`."""
    claim = Transaction(
        league_season_id=team.league_season_id,
        espn_transaction_id=f"bid-{day}-{team.id}-{who.id}",
        team_id=team.id,
        type="WAIVER",
        status="EXECUTED",
        scoring_period=day,
        bid_amount=amount,
    )
    session.add(claim)
    session.flush()
    session.add(
        TransactionItem(
            transaction_id=claim.id,
            player_id=who.id,
            item_type="ADD",
            from_team_id=None,
            to_team_id=team.id,
        )
    )
    session.flush()


def distribution(abbreviation: str, mean: float, spread: float) -> CategoryDistribution:
    return CategoryDistribution(
        abbreviation=abbreviation,
        mean=mean,
        spread=spread,
        lower_is_better=abbreviation == "TO",
        sample=100,
        basis_seasons=(SEASON - 1,),
        period_days=7,
        era_scale=1.0,
    )


#: A plain week's opponent: the nine categories with round means and spreads.
WEEK = tuple(
    distribution(abbreviation, mean, spread)
    for abbreviation, mean, spread in (
        ("PTS", 500.0, 100.0),
        ("REB", 200.0, 40.0),
        ("AST", 100.0, 25.0),
        ("STL", 30.0, 8.0),
        ("BLK", 20.0, 10.0),
        ("3PM", 50.0, 12.0),
        ("TO", 60.0, 12.0),
        ("FG%", 0.47, 0.02),
        ("FT%", 0.78, 0.04),
    )
)
assert tuple(d.abbreviation for d in WEEK) == NINE
