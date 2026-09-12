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
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    DailyLineupSlot,
    League,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Owner,
    Player,
    PlayerGameStat,
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
    slot: str = "PG",
) -> Any:
    return SimpleNamespace(
        playerId=player_id,
        name=name,
        position=position,
        proTeam=pro_team,
        injured=injured,
        injuryStatus=injury_status,
        # Always "PG" on the aggregate roster, which is why ingest ignores it.
        lineupSlot="PG",
        # The real daily slot, from rosterForCurrentScoringPeriod.
        slot_position=slot,
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
    home_stats: dict[str, Any] | None = None,
    away_stats: dict[str, Any] | None = None,
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
        home_stats=home_stats if home_stats is not None else {},
        away_stats=away_stats if away_stats is not None else {},
    )


def stat_block(
    *, pts: float = 503.0, result: str | None = "WIN", fgm: float = 182.0, fga: float = 398.0
) -> dict[str, Any]:
    """A scored category, a percentage, and the components behind it."""
    return {
        "PTS": {"value": pts, "result": result},
        "FG%": {"value": round(fgm / fga, 8), "result": result},
        "FGM": {"value": fgm, "result": None},
        "FGA": {"value": fga, "result": None},
    }


def fake_card(player_id: int, name: str, periods: dict[int, dict[str, Any] | None]) -> Any:
    """A player card: scoring period -> stat line, or None for a day not played.

    Also carries the season rollups ESPN mixes into the same dict, which the
    ingest has to ignore.
    """
    stats: dict[str, Any] = {
        "2026_total": {"total": {"PTS": 1.0}, "date": None, "team": None},
        "2026_projected": {"total": {"PTS": 2.0}, "date": None, "team": None},
    }
    for period, line in periods.items():
        stats[str(period)] = {
            "total": line or {},
            "date": datetime(2025, 10, 23, 0, 30) if line is not None else datetime(2025, 10, 24),
            "team": "TOR",
        }
    return SimpleNamespace(playerId=player_id, name=name, stats=stats)


def league_with_play(
    *,
    season: int = 2026,
    teams: list[Any],
    boxes: dict[int, list[Any]],
    reg_season_count: int = 2,
    matchup_period_count: int = 3,
    cards: dict[int, Any] | None = None,
) -> Any:
    """A fake league that also answers `box_scores(period)` and `player_info`."""
    league = fake_league(
        season=season,
        reg_season_count=reg_season_count,
        matchup_period_count=matchup_period_count,
    )
    league.teams = teams
    league.box_scores = lambda period: boxes.get(period, [])
    by_id = cards or {}
    league.player_info = lambda playerId: [  # noqa: N803  (ESPN's own parameter name)
        by_id[i] for i in playerId if i in by_id
    ]
    league.matchup_ids = {}
    league.box_scores = lambda matchup_period=None, scoring_period=None, matchup_total=True: (
        boxes.get(matchup_period, [])
    )
    return league


def league_with_days(
    *,
    teams: list[Any],
    boxes: dict[int, list[Any]],
    days: dict[int, dict[int, list[Any]]],
    windows: dict[int, list[str]],
    reg_season_count: int = 2,
    matchup_period_count: int = 2,
    cards: dict[int, Any] | None = None,
) -> Any:
    """A fake league with daily lineups.

    `days` maps matchup period -> scoring period -> box scores for that day.
    `windows` mirrors ESPN's `matchup_ids`: int keys, string values, and
    deliberately in lexicographic order so the sorting is exercised.
    """
    league = league_with_play(
        teams=teams,
        boxes=boxes,
        reg_season_count=reg_season_count,
        matchup_period_count=matchup_period_count,
        cards=cards,
    )
    league.matchup_ids = windows

    def box_scores(
        matchup_period: int | None = None,
        scoring_period: int | None = None,
        matchup_total: bool = True,
    ) -> list[Any]:
        if not matchup_total and scoring_period is not None:
            return days.get(matchup_period or 0, {}).get(scoring_period, [])
        return boxes.get(matchup_period or 0, [])

    league.box_scores = box_scores
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


def _stats_league(**box_kwargs: Any) -> tuple[Any, Any, Any]:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    espn = league_with_play(
        teams=[home, away],
        boxes={1: [fake_box(home, away, **box_kwargs)]},
        matchup_period_count=1,
    )
    return home, away, espn


def test_scored_categories_and_components_are_both_stored(session: Session) -> None:
    _, _, espn = _stats_league(
        home_stats=stat_block(result="WIN"), away_stats=stat_block(pts=397.0, result="LOSS")
    )

    ingest_season(session, espn)

    stats = session.scalars(select(MatchupTeamStat)).all()
    assert len(stats) == 8, "four statistics for each of two teams"
    by_abbrev = {(s.team_id, s.abbreviation): s for s in stats}
    teams = {t.espn_team_id: t.id for t in session.scalars(select(Team)).all()}
    assert by_abbrev[(teams[3], "PTS")].value == 503.0
    assert by_abbrev[(teams[3], "PTS")].result == "WIN"
    assert by_abbrev[(teams[21], "PTS")].result == "LOSS"


def test_a_scored_category_links_to_the_seasons_category_row(session: Session) -> None:
    """The link, not the result, is what marks a statistic as scored."""
    _, _, espn = _stats_league(home_stats=stat_block(), away_stats=stat_block())

    ingest_season(session, espn)

    stats = {s.abbreviation: s for s in session.scalars(select(MatchupTeamStat)).all()}
    assert stats["PTS"].league_season_category_id is not None
    assert stats["PTS"].category is not None
    assert stats["PTS"].category.stat_id == 0, "PTS is ESPN stat id 0"
    # FGM and FGA are components of FG%, not categories the league scores.
    assert stats["FGM"].league_season_category_id is None
    assert stats["FGA"].league_season_category_id is None


def test_components_let_a_percentage_be_recomputed(session: Session) -> None:
    _, _, espn = _stats_league(home_stats=stat_block(fgm=182.0, fga=398.0), away_stats={})

    ingest_season(session, espn)

    stats = {s.abbreviation: s.value for s in session.scalars(select(MatchupTeamStat)).all()}
    assert stats["FGM"] / stats["FGA"] == pytest.approx(stats["FG%"])


def test_a_bye_stores_values_with_no_result(session: Session) -> None:
    """The trap: a bye reports real values but a null result on every one."""
    home = fake_team(3, "A")
    espn = league_with_play(
        teams=[home],
        boxes={
            1: [
                fake_box(
                    home,
                    0,
                    winner="UNDECIDED",
                    home_wins=0,
                    away_wins=0,
                    home_stats=stat_block(pts=634.0, result=None),
                )
            ]
        },
        matchup_period_count=1,
    )

    ingest_season(session, espn)

    points = session.scalars(
        select(MatchupTeamStat).where(MatchupTeamStat.abbreviation == "PTS")
    ).one()
    assert points.value == 634.0
    assert points.result is None
    # Still a scored category, despite having no result to show for it.
    assert points.league_season_category_id is not None


def test_stats_are_not_duplicated_on_reingest(session: Session) -> None:
    def build() -> Any:
        _, _, espn = _stats_league(home_stats=stat_block(), away_stats=stat_block())
        return espn

    ingest_season(session, build())
    session.commit()
    ingest_season(session, build())
    session.commit()

    assert len(session.scalars(select(MatchupTeamStat)).all()) == 8


def test_a_statistic_that_disappears_is_removed(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")

    def build(stats: dict[str, Any]) -> Any:
        return league_with_play(
            teams=[home, away],
            boxes={1: [fake_box(home, away, home_stats=stats, away_stats={})]},
            matchup_period_count=1,
        )

    ingest_season(session, build(stat_block()))
    session.commit()
    ingest_season(session, build({"PTS": {"value": 503.0, "result": "WIN"}}))
    session.commit()

    abbrevs = {s.abbreviation for s in session.scalars(select(MatchupTeamStat)).all()}
    assert abbrevs == {"PTS"}


BOX_LINE = {
    "PTS": 22.0,
    "REB": 9.0,
    "OREB": 2.0,
    "DREB": 7.0,
    "AST": 5.0,
    "STL": 1.0,
    "BLK": 2.0,
    "TO": 3.0,
    "PF": 4.0,
    "MIN": 35.0,
    "FGM": 8.0,
    "FGA": 17.0,
    "3PM": 2.0,
    "3PA": 5.0,
    "FTM": 4.0,
    "FTA": 4.0,
    "PPG": 22.0,
    "FG%": 0.47058824,
}


def _league_with_cards(periods: dict[int, dict[str, Any] | None]) -> Any:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    kawhi = fake_player(6450, "Kawhi Leonard")
    return league_with_play(
        teams=[home, away],
        boxes={1: [fake_box(home, away, home_lineup=[kawhi], away_lineup=[])]},
        matchup_period_count=1,
        cards={6450: fake_card(6450, "Kawhi Leonard", periods)},
    )


def test_player_stats_are_stored_per_scoring_period(session: Session) -> None:
    ingest_season(session, _league_with_cards({2: BOX_LINE, 4: BOX_LINE}))

    rows = session.scalars(select(PlayerGameStat).order_by(PlayerGameStat.scoring_period)).all()
    assert [r.scoring_period for r in rows] == [2, 4], "season rollups must not become rows"
    assert rows[0].season == 2026
    assert rows[0].points == 22.0
    assert rows[0].minutes == 35.0
    assert rows[0].field_goals_made == 8.0
    assert rows[0].opponent == "TOR"


def test_a_day_without_a_stat_line_is_kept_and_flagged(session: Session) -> None:
    """Absence of a row would mean "no game". This means "played no part in one"."""
    ingest_season(session, _league_with_cards({2: BOX_LINE, 5: None}))

    rows = {r.scoring_period: r for r in session.scalars(select(PlayerGameStat)).all()}
    assert rows[2].played is True
    assert rows[5].played is False
    assert rows[5].points is None
    assert rows[5].game_date is not None, "the fixture still happened"


def test_game_dates_are_stored_as_utc(session: Session) -> None:
    """ESPN sends no offset, so the naive timestamp is read as UTC."""
    ingest_season(session, _league_with_cards({2: BOX_LINE}))

    row = session.scalars(select(PlayerGameStat)).one()
    assert row.game_date == datetime(2025, 10, 23, 0, 30, tzinfo=UTC)


def test_rate_stats_stay_in_raw_totals_only(session: Session) -> None:
    ingest_season(session, _league_with_cards({2: BOX_LINE}))

    row = session.scalars(select(PlayerGameStat)).one()
    assert row.raw_totals["FG%"] == pytest.approx(0.47058824)
    assert row.raw_totals["PPG"] == 22.0
    # Recomputable from the columns, so it is not one.
    made, attempted = row.field_goals_made, row.field_goals_attempted
    assert made is not None and attempted is not None
    assert made / attempted == pytest.approx(row.raw_totals["FG%"])


def test_player_stats_are_not_duplicated_on_reingest(session: Session) -> None:
    ingest_season(session, _league_with_cards({2: BOX_LINE, 4: BOX_LINE}))
    session.commit()
    ingest_season(session, _league_with_cards({2: BOX_LINE, 4: BOX_LINE}))
    session.commit()

    assert len(session.scalars(select(PlayerGameStat)).all()) == 2


def test_only_players_rostered_this_season_are_fetched(session: Session) -> None:
    """Ingesting one league must not pull the whole player universe."""
    home, away = fake_team(3, "A"), fake_team(21, "B")
    rostered, stranger = fake_player(6450, "Rostered"), fake_player(999, "Never Rostered")
    espn = league_with_play(
        teams=[home, away],
        boxes={1: [fake_box(home, away, home_lineup=[rostered], away_lineup=[])]},
        matchup_period_count=1,
        cards={
            6450: fake_card(6450, "Rostered", {2: BOX_LINE}),
            999: fake_card(999, "Never Rostered", {2: BOX_LINE}),
        },
    )
    assert stranger is not None  # present in the league's cards, absent from every roster

    ingest_season(session, espn)

    names = {r.player.name for r in session.scalars(select(PlayerGameStat)).all()}
    assert names == {"Rostered"}


def _daily_league(
    *,
    day_slots: dict[int, list[tuple[int, str]]],
    cards: dict[int, Any] | None = None,
) -> Any:
    """A two-team league where `day_slots` maps scoring period -> (player id, slot).

    Both teams share the matchup; the slots given are the home team's.
    """
    home, away = fake_team(3, "A"), fake_team(21, "B")
    weekly = fake_box(
        home, away, home_lineup=[fake_player(pid, f"P{pid}") for pid, _ in day_slots.get(1, [])]
    )
    days: dict[int, dict[int, list[Any]]] = {1: {}}
    for scoring_period, slots in day_slots.items():
        lineup = [fake_player(pid, f"P{pid}", slot=slot) for pid, slot in slots]
        days[1][scoring_period] = [fake_box(home, away, home_lineup=lineup, away_lineup=[])]
    return league_with_days(
        teams=[home, away],
        boxes={1: [weekly]},
        days=days,
        # Lexicographic on purpose: "10" sorts before "7" as a string.
        windows={1: [str(d) for d in sorted(day_slots, key=str)]},
        matchup_period_count=1,
        cards=cards,
    )


def test_daily_slots_record_who_started_and_who_sat(session: Session) -> None:
    espn = _daily_league(day_slots={1: [(100, "PG"), (200, "BE"), (300, "IR"), (400, "UT")]})

    ingest_season(session, espn)

    rows = {r.player.name: r for r in session.scalars(select(DailyLineupSlot)).all()}
    assert rows["P100"].slot == "PG"
    assert rows["P100"].started is True
    assert rows["P200"].slot == "BE"
    assert rows["P200"].started is False, "bench does not count"
    assert rows["P300"].started is False, "injured reserve does not count"
    assert rows["P400"].started is True, "utility is a starting slot"


def test_the_same_player_can_start_one_day_and_sit_the_next(session: Session) -> None:
    """The whole point of the daily grain."""
    espn = _daily_league(day_slots={7: [(100, "PG")], 8: [(100, "BE")]})

    ingest_season(session, espn)

    by_day = {r.scoring_period: r.started for r in session.scalars(select(DailyLineupSlot)).all()}
    assert by_day == {7: True, 8: False}


def test_period_window_is_taken_from_matchup_ids(session: Session) -> None:
    """Values arrive as unsorted strings, so the window must be sorted numerically."""
    espn = _daily_league(day_slots={7: [(100, "PG")], 8: [(100, "PG")], 10: [(100, "PG")]})

    ingest_season(session, espn)

    period = session.scalars(select(MatchupPeriod)).one()
    assert period.first_scoring_period == 7
    assert period.final_scoring_period == 10, "10 must not lose to 8 lexicographically"


def test_daily_rows_carry_injury_state(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    hurt = fake_player(100, "Hurt", injured=True, injury_status="OUT", slot="BE")
    espn = league_with_days(
        teams=[home, away],
        boxes={1: [fake_box(home, away, home_lineup=[hurt])]},
        days={1: {1: [fake_box(home, away, home_lineup=[hurt], away_lineup=[])]}},
        windows={1: ["1"]},
        matchup_period_count=1,
    )

    ingest_season(session, espn)

    row = session.scalars(select(DailyLineupSlot)).one()
    assert row.injured is True
    assert row.injury_status == "OUT"
    assert row.started is False


def test_the_weekly_snapshot_is_kept_alongside_the_daily_one(session: Session) -> None:
    """Both grains are retained; the daily rows do not replace roster_slots."""
    espn = _daily_league(day_slots={1: [(100, "PG"), (200, "BE")]})

    ingest_season(session, espn)

    assert len(session.scalars(select(RosterSlot)).all()) > 0, "weekly rows still written"
    assert len(session.scalars(select(DailyLineupSlot)).all()) == 2


def test_daily_lineups_are_not_duplicated_on_reingest(session: Session) -> None:
    def build() -> Any:
        return _daily_league(day_slots={1: [(100, "PG")], 2: [(100, "BE")]})

    ingest_season(session, build())
    session.commit()
    ingest_season(session, build())
    session.commit()

    assert len(session.scalars(select(DailyLineupSlot)).all()) == 2


def test_a_player_dropped_from_a_day_is_removed(session: Session) -> None:
    ingest_season(session, _daily_league(day_slots={1: [(100, "PG"), (200, "BE")]}))
    session.commit()

    ingest_season(session, _daily_league(day_slots={1: [(100, "PG")]}))
    session.commit()

    names = {r.player.name for r in session.scalars(select(DailyLineupSlot)).all()}
    assert names == {"P100"}


def test_started_players_reconcile_with_the_team_total(session: Session) -> None:
    """The narrative payoff, and the check that the two grains agree.

    P100 starts and scores 30, P200 is benched and scores 40. The team total
    must count only the 30.
    """
    espn = _daily_league(
        day_slots={1: [(100, "PG"), (200, "BE")]},
        cards={
            100: fake_card(100, "P100", {1: dict(BOX_LINE, PTS=30.0)}),
            200: fake_card(200, "P200", {1: dict(BOX_LINE, PTS=40.0)}),
        },
    )

    ingest_season(session, espn)

    started_points = session.scalar(
        select(func.sum(PlayerGameStat.points))
        .join(DailyLineupSlot, DailyLineupSlot.player_id == PlayerGameStat.player_id)
        .where(
            DailyLineupSlot.scoring_period == PlayerGameStat.scoring_period,
            DailyLineupSlot.started,
        )
    )
    assert started_points == 30.0, "the benched 40 must not count"

    benched = session.scalars(
        select(DailyLineupSlot).where(DailyLineupSlot.started == False)  # noqa: E712
    ).one()
    assert benched.player.name == "P200"
