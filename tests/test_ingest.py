"""Ingest tests.

The load-bearing case is `test_new_season_leaves_earlier_seasons_untouched`:
ESPN settings change between years, and last year's record must not move when
this year's is written.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    League,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    Owner,
    Player,
    RosterSlot,
    Team,
)
from app.db.session import make_engine, make_session_factory
from app.ingest import ingest_league_structure, ingest_season

REPO_ROOT = Path(__file__).resolve().parent.parent

# The real league's nine categories, in the order ESPN returns them.
NINE_CAT_STAT_IDS = [20, 6, 11, 0, 1, 17, 2, 3, 19]

# 2026-02-20T12:00:00Z, the shape ESPN uses for the trade deadline.
TRADE_DEADLINE_EPOCH_MS = 1771588800000


def _scoring_items(stat_ids: list[int]) -> list[dict[str, Any]]:
    return [{"statId": stat_id, "isReverseItem": False, "points": 1.0} for stat_id in stat_ids]


def fake_league(
    *,
    league_id: int = 3853870,
    season: int = 2026,
    name: str = "Patriot Games",
    team_count: int = 14,
    stat_ids: list[int] | None = None,
    reg_season_count: int = 19,
    matchup_period_count: int = 22,
    trade_deadline: int | None = TRADE_DEADLINE_EPOCH_MS,
) -> Any:
    """A stand-in for `espn_api.basketball.League`, carrying only what ingest reads."""
    settings = SimpleNamespace(
        name=name,
        scoring_type="H2H_CATEGORY",
        team_count=team_count,
        reg_season_count=reg_season_count,
        playoff_team_count=7,
        playoff_matchup_period_length=1,
        keeper_count=0,
        faab=True,
        acquisition_budget=100,
        median_scoring=False,
        trade_deadline=trade_deadline,
        division_map={1: "UK"},
        matchup_periods={str(i): [i] for i in range(1, matchup_period_count + 1)},
    )
    settings._raw_scoring_settings = {
        "scoringItems": _scoring_items(stat_ids if stat_ids is not None else NINE_CAT_STAT_IDS)
    }
    settings._raw_schedule_settings = {"matchupPeriodCount": reg_season_count}
    return SimpleNamespace(league_id=league_id, year=season, settings=settings)


@pytest.fixture(scope="module")
def session_factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    """A migrated, empty test schema for this module."""
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")

    engine = make_engine(test_database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A clean slate per test: every table is emptied first."""
    with session_factory() as session:
        session.execute(text("TRUNCATE leagues RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


def test_ingest_stores_season_settings_and_categories(session: Session) -> None:
    stored = ingest_league_structure(session, fake_league())

    assert stored.season == 2026
    assert stored.name == "Patriot Games"
    assert stored.scoring_type == "H2H_CATEGORY"
    assert stored.team_count == 14
    assert stored.regular_season_periods == 19
    # Playoffs included, so this is deliberately larger than the regular season count.
    assert stored.total_matchup_periods == 22
    assert [c.stat_id for c in stored.categories] == NINE_CAT_STAT_IDS
    assert [c.abbreviation for c in stored.categories][:3] == ["FT%", "REB", "TO"]
    assert [c.position for c in stored.categories] == list(range(9))


def test_trade_deadline_is_converted_from_epoch_milliseconds(session: Session) -> None:
    stored = ingest_league_structure(session, fake_league())

    assert stored.trade_deadline == datetime(2026, 2, 20, 12, 0, tzinfo=UTC)


def test_absent_trade_deadline_is_null(session: Session) -> None:
    stored = ingest_league_structure(session, fake_league(trade_deadline=0))

    assert stored.trade_deadline is None


def test_raw_settings_keeps_fields_we_do_not_model(session: Session) -> None:
    """The escape hatch: unmodelled ESPN fields survive so they can be backfilled."""
    stored = ingest_league_structure(session, fake_league())

    assert stored.raw_settings["division_map"] == {"1": "UK"}
    assert len(stored.raw_settings["matchup_periods"]) == 22
    assert stored.raw_settings["scoring"]["scoringItems"][0]["statId"] == 20


def test_reingesting_a_season_updates_in_place(session: Session) -> None:
    first = ingest_league_structure(session, fake_league(name="Old Name"))
    session.commit()

    second = ingest_league_structure(session, fake_league(name="Renamed Mid-Season"))
    session.commit()

    assert first.id == second.id
    assert second.name == "Renamed Mid-Season"
    assert len(session.scalars(select(League)).all()) == 1
    assert len(session.scalars(select(LeagueSeason)).all()) == 1
    assert len(session.scalars(select(LeagueSeasonCategory)).all()) == 9


def test_new_season_leaves_earlier_seasons_untouched(session: Session) -> None:
    """Next year's settings can differ in every way. Last year must not move."""
    ingest_league_structure(session, fake_league(season=2026, team_count=14))
    session.commit()

    # A smaller league next year, scoring eight categories instead of nine,
    # with a shorter regular season.
    ingest_league_structure(
        session,
        fake_league(
            season=2027,
            name="Patriot Games II",
            team_count=12,
            stat_ids=[20, 6, 0, 1, 17, 2, 3, 19],
            reg_season_count=18,
            matchup_period_count=21,
        ),
    )
    session.commit()

    leagues = session.scalars(select(League)).all()
    assert len(leagues) == 1, "same ESPN league id must not create a second league"

    seasons = {s.season: s for s in session.scalars(select(LeagueSeason)).all()}
    assert sorted(seasons) == [2026, 2027]

    assert seasons[2026].team_count == 14
    assert seasons[2026].regular_season_periods == 19
    assert len(seasons[2026].categories) == 9

    assert seasons[2027].team_count == 12
    assert seasons[2027].regular_season_periods == 18
    assert len(seasons[2027].categories) == 8
    assert 11 not in {c.stat_id for c in seasons[2027].categories}, "turnovers were dropped"


def test_changing_categories_within_a_season_is_reconciled(session: Session) -> None:
    stored = ingest_league_structure(session, fake_league())
    session.commit()
    points_id = next(c.id for c in stored.categories if c.stat_id == 0)

    # Turnovers (11) out, double-doubles (41) in.
    ingest_league_structure(session, fake_league(stat_ids=[20, 6, 41, 0, 1, 17, 2, 3, 19]))
    session.commit()

    session.refresh(stored)
    stat_ids = {c.stat_id for c in stored.categories}
    assert 11 not in stat_ids
    assert 41 in stat_ids
    assert len(stored.categories) == 9
    kept = next(c for c in stored.categories if c.stat_id == 0)
    assert kept.id == points_id, "a category that survived should keep its row id"
    assert len(session.scalars(select(LeagueSeasonCategory)).all()) == 9


def fake_player(
    player_id: int,
    name: str,
    *,
    position: str = "PG",
    pro_team: str = "LAL",
    injured: bool = False,
    injury_status: str = "ACTIVE",
) -> Any:
    return SimpleNamespace(
        playerId=player_id,
        name=name,
        position=position,
        proTeam=pro_team,
        injured=injured,
        injuryStatus=injury_status,
        # Always "PG" upstream, which is why ingest ignores it.
        lineupSlot="PG",
    )


def fake_team(
    team_id: int,
    name: str,
    *,
    owners: list[dict[str, Any]] | None = None,
    categories: tuple[int, int, int] = (95, 76, 0),
    standing: int = 1,
) -> Any:
    return SimpleNamespace(
        team_id=team_id,
        team_name=name,
        team_abbrev=name[:4].upper(),
        logo_url=f"https://example.test/{team_id}.png",
        division_id=1,
        division_name="UK",
        standing=standing,
        final_standing=standing,
        wins=categories[0],
        losses=categories[1],
        ties=categories[2],
        owners=owners if owners is not None else [owner_dict(f"owner-{team_id}")],
        acquisitions=113,
        drops=113,
        trades=4,
        acquisition_budget_spent=50,
    )


def owner_dict(guid: str, first: str = "Pat", last: str = "M") -> dict[str, Any]:
    return {
        "id": guid,
        "displayName": f"{first.lower()}{last.lower()}",
        "firstName": first,
        "lastName": last,
    }


def fake_box(
    home: Any,
    away: Any,
    *,
    winner: str = "HOME",
    home_wins: int = 5,
    away_wins: int = 4,
    ties: int = 0,
    scoring_period: int = 6,
    home_lineup: list[Any] | None = None,
    away_lineup: list[Any] | None = None,
) -> Any:
    return SimpleNamespace(
        home_team=home,
        away_team=away,
        winner=winner,
        home_wins=home_wins,
        away_wins=away_wins,
        home_ties=ties,
        scoring_period=scoring_period,
        home_lineup=home_lineup or [],
        away_lineup=away_lineup or [],
    )


def league_with_play(
    *,
    season: int = 2026,
    teams: list[Any],
    boxes: dict[int, list[Any]],
    reg_season_count: int = 2,
    matchup_period_count: int = 3,
) -> Any:
    """A fake league that also answers `box_scores(period)`."""
    league = fake_league(
        season=season,
        reg_season_count=reg_season_count,
        matchup_period_count=matchup_period_count,
    )
    league.teams = teams
    league.box_scores = lambda period: boxes.get(period, [])
    return league


def test_ingest_teams_stores_identity_and_category_tallies(session: Session) -> None:
    home, away = fake_team(3, "Through The Wire"), fake_team(21, "Load Management")
    espn = league_with_play(teams=[home, away], boxes={})

    ingest_season(session, espn)

    stored = {t.espn_team_id: t for t in session.scalars(select(Team)).all()}
    assert sorted(stored) == [3, 21]
    assert stored[3].name == "Through The Wire"
    assert stored[3].division_name == "UK"
    # Named for what they are: category tallies, not a matchup record.
    assert (stored[3].categories_won, stored[3].categories_lost) == (95, 76)


def test_a_team_can_have_several_owners(session: Session) -> None:
    shared = fake_team(25, "Ben's Need Some VC", owners=[owner_dict("g-ben"), owner_dict("g-bern")])
    espn = league_with_play(teams=[shared], boxes={})

    ingest_season(session, espn)

    team = session.scalars(select(Team)).one()
    assert {o.espn_owner_id for o in team.owners} == {"g-ben", "g-bern"}


def test_owners_are_shared_across_seasons_not_duplicated(session: Session) -> None:
    """An owner is a person, so the same GUID must not create a second row."""
    guid = owner_dict("g-pat")
    ingest_season(
        session, league_with_play(season=2026, teams=[fake_team(3, "A", owners=[guid])], boxes={})
    )
    session.commit()
    ingest_season(
        session, league_with_play(season=2027, teams=[fake_team(3, "B", owners=[guid])], boxes={})
    )
    session.commit()

    assert len(session.scalars(select(Owner)).all()) == 1
    assert len(session.scalars(select(Team)).all()) == 2, "teams are season-scoped"


def test_matchup_periods_flag_the_playoffs(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    espn = league_with_play(
        teams=[home, away],
        boxes={1: [fake_box(home, away)], 2: [fake_box(home, away)], 3: [fake_box(home, away)]},
        reg_season_count=2,
        matchup_period_count=3,
    )

    ingest_season(session, espn)

    periods = {p.period: p for p in session.scalars(select(MatchupPeriod)).all()}
    assert sorted(periods) == [1, 2, 3]
    assert periods[1].is_playoff is False
    assert periods[2].is_playoff is False
    assert periods[3].is_playoff is True, "period beyond the regular season is a playoff period"


def test_a_bye_is_stored_with_no_opponent(session: Session) -> None:
    """ESPN reports the absent side as team 0 and the result as UNDECIDED."""
    home = fake_team(3, "A")
    espn = league_with_play(
        teams=[home],
        boxes={1: [fake_box(home, 0, winner="UNDECIDED", home_wins=0, away_wins=0)]},
        matchup_period_count=1,
    )

    ingest_season(session, espn)

    matchup = session.scalars(select(Matchup)).one()
    assert matchup.away_team_id is None
    assert matchup.winner == "UNDECIDED"


def test_rosters_are_snapshots_per_matchup_period(session: Session) -> None:
    """A roster changes constantly, so each period keeps its own record."""
    home, away = fake_team(3, "A"), fake_team(21, "B")
    kawhi, sengun = fake_player(6450, "Kawhi Leonard"), fake_player(4066261, "Alperen Sengun")
    espn = league_with_play(
        teams=[home, away],
        boxes={
            1: [fake_box(home, away, home_lineup=[kawhi], away_lineup=[sengun])],
            # Traded: the same two players swap teams for period 2.
            2: [fake_box(home, away, home_lineup=[sengun], away_lineup=[kawhi])],
        },
        matchup_period_count=2,
    )

    ingest_season(session, espn)

    assert len(session.scalars(select(Player)).all()) == 2, "players are global, not per team"
    slots = session.scalars(select(RosterSlot)).all()
    assert len(slots) == 4

    by_period = {(s.matchup.matchup_period.period, s.player.name): s.team_id for s in slots}
    teams = {t.espn_team_id: t.id for t in session.scalars(select(Team)).all()}
    assert by_period[(1, "Kawhi Leonard")] == teams[3]
    assert by_period[(2, "Kawhi Leonard")] == teams[21], "period 2 reflects the trade"


def test_roster_details_are_recorded_as_at_that_period(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    hurt = fake_player(
        6450, "Kawhi Leonard", position="SF", pro_team="LAC", injured=True, injury_status="OUT"
    )
    espn = league_with_play(
        teams=[home, away],
        boxes={1: [fake_box(home, away, home_lineup=[hurt])]},
        matchup_period_count=1,
    )

    ingest_season(session, espn)

    slot = session.scalars(select(RosterSlot)).one()
    assert (slot.position, slot.pro_team) == ("SF", "LAC")
    assert slot.injured is True
    assert slot.injury_status == "OUT"


def test_reingesting_a_season_does_not_duplicate_play(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    kawhi = fake_player(6450, "Kawhi Leonard")

    def build() -> Any:
        return league_with_play(
            teams=[home, away],
            boxes={1: [fake_box(home, away, home_lineup=[kawhi], away_lineup=[])]},
            matchup_period_count=1,
        )

    ingest_season(session, build())
    session.commit()
    ingest_season(session, build())
    session.commit()

    assert len(session.scalars(select(MatchupPeriod)).all()) == 1
    assert len(session.scalars(select(Matchup)).all()) == 1
    assert len(session.scalars(select(RosterSlot)).all()) == 1
    assert len(session.scalars(select(Player)).all()) == 1


def test_a_dropped_player_leaves_the_roster_on_reingest(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    kawhi, sengun = fake_player(6450, "Kawhi"), fake_player(4066261, "Sengun")

    ingest_season(
        session,
        league_with_play(
            teams=[home, away],
            boxes={1: [fake_box(home, away, home_lineup=[kawhi, sengun])]},
            matchup_period_count=1,
        ),
    )
    session.commit()

    ingest_season(
        session,
        league_with_play(
            teams=[home, away],
            boxes={1: [fake_box(home, away, home_lineup=[kawhi])]},
            matchup_period_count=1,
        ),
    )
    session.commit()

    names = {s.player.name for s in session.scalars(select(RosterSlot)).all()}
    assert names == {"Kawhi"}
    assert len(session.scalars(select(Player)).all()) == 2, "the player row itself survives a drop"
