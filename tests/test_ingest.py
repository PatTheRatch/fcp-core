"""Ingest tests.

The load-bearing case is `test_new_season_leaves_earlier_seasons_untouched`:
ESPN settings change between years, and last year's record must not move when
this year's is written.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    DailyLineupSlot,
    DraftPick,
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
    Transaction,
    TransactionItem,
)
from app.db.session import make_engine, make_session_factory
from app.ingest import IngestScope, ingest_league_structure, ingest_season
from tests.fakes import (
    BOX_LINE,
    NINE_CAT_STAT_IDS,
    attach_draft,
    attach_transactions,
    fake_box,
    fake_card,
    fake_league,
    fake_pick,
    fake_player,
    fake_team,
    fake_transaction,
    league_with_days,
    league_with_play,
    owner_dict,
    stat_block,
    tx_item,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The real league's nine categories, in the order ESPN returns them.


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


def test_windows_are_discovered_when_espn_omits_them(session: Session) -> None:
    """Seasons before 2025 return no period-to-day mapping, so it is probed.

    The fake mirrors ESPN's actual behaviour: asking for a day under the
    wrong matchup period yields no lineups at all, which is the signal the
    probe advances on.
    """
    home, away = fake_team(3, "A"), fake_team(21, "B")
    starter = fake_player(100, "Starter", slot="PG")
    weekly = fake_box(home, away, home_lineup=[starter])

    # Period 1 covers days 1-2, period 2 covers day 3.
    days = {
        1: {
            1: [fake_box(home, away, home_lineup=[starter], away_lineup=[])],
            2: [fake_box(home, away, home_lineup=[starter], away_lineup=[])],
        },
        2: {3: [fake_box(home, away, home_lineup=[starter], away_lineup=[])]},
    }
    espn = league_with_days(
        teams=[home, away],
        boxes={1: [weekly], 2: [weekly]},
        days=days,
        windows={},  # exactly what ESPN gives for 2019 to 2024
        reg_season_count=1,
        matchup_period_count=2,
        cards={100: fake_card(100, "Starter", {1: BOX_LINE, 2: BOX_LINE, 3: BOX_LINE})},
    )

    ingest_season(session, espn)

    periods = {p.period: p for p in session.scalars(select(MatchupPeriod)).all()}
    assert (periods[1].first_scoring_period, periods[1].final_scoring_period) == (1, 2)
    assert (periods[2].first_scoring_period, periods[2].final_scoring_period) == (3, 3)

    by_day = {
        s.scoring_period: s.matchup_period.period
        for s in session.scalars(select(DailyLineupSlot)).all()
    }
    assert by_day == {1: 1, 2: 1, 3: 2}, "each day attributed to the right period"


def test_discovery_stops_rather_than_probing_past_the_last_period(session: Session) -> None:
    """A day beyond the final period must not spin or mis-attribute."""
    home, away = fake_team(3, "A"), fake_team(21, "B")
    starter = fake_player(100, "Starter", slot="PG")
    weekly = fake_box(home, away, home_lineup=[starter])

    espn = league_with_days(
        teams=[home, away],
        boxes={1: [weekly]},
        days={1: {1: [fake_box(home, away, home_lineup=[starter], away_lineup=[])]}},
        windows={},
        reg_season_count=1,
        matchup_period_count=1,
        # Day 9 has a stat line but belongs to no period ESPN will admit to.
        cards={100: fake_card(100, "Starter", {1: BOX_LINE, 9: BOX_LINE})},
    )

    ingest_season(session, espn)

    days = {s.scoring_period for s in session.scalars(select(DailyLineupSlot)).all()}
    assert days == {1}, "the unattributable day is left out, not guessed at"


def test_two_seasons_of_the_same_league_keep_their_own_shape(session: Session) -> None:
    """The point of the season-scoped schema, now that prior seasons load.

    The real league went from 10 teams in 2019 to 14 in 2026, with the
    regular season changing length too.
    """
    small = [fake_team(1, "Alpha"), fake_team(2, "Beta")]
    big = [fake_team(1, "Alpha Renamed"), fake_team(2, "Beta"), fake_team(3, "Gamma")]

    ingest_season(
        session,
        league_with_days(
            teams=small,
            boxes={1: [fake_box(small[0], small[1])]},
            days={},
            windows={},
            reg_season_count=1,
            matchup_period_count=1,
        ),
    )
    session.commit()

    later = league_with_days(
        teams=big,
        boxes={1: [fake_box(big[0], big[1])]},
        days={},
        windows={},
        reg_season_count=2,
        matchup_period_count=2,
    )
    later.year = 2027
    later.settings.team_count = 3
    ingest_season(session, later)
    session.commit()

    seasons = {s.season: s for s in session.scalars(select(LeagueSeason)).all()}
    assert sorted(seasons) == [2026, 2027]
    assert len(seasons[2026].teams) == 2
    assert len(seasons[2027].teams) == 3
    assert seasons[2026].regular_season_periods == 1
    assert seasons[2027].regular_season_periods == 2

    # A renamed team in the later season must not rewrite the earlier one.
    earlier_names = {t.espn_team_id: t.name for t in seasons[2026].teams}
    later_names = {t.espn_team_id: t.name for t in seasons[2027].teams}
    assert earlier_names[1] == "Alpha"
    assert later_names[1] == "Alpha Renamed"


def _tx_league(by_day: dict[int, list[dict[str, Any]]], names: dict[int, str]) -> Any:
    """A two-team league whose single period covers days 1 to 3."""
    home, away = fake_team(3, "A"), fake_team(21, "B")
    starter = fake_player(100, "Starter", slot="PG")
    weekly = fake_box(home, away, home_lineup=[starter])
    espn = league_with_days(
        teams=[home, away],
        boxes={1: [weekly]},
        days={
            1: {
                day: [fake_box(home, away, home_lineup=[starter], away_lineup=[])]
                for day in (1, 2, 3)
            }
        },
        windows={1: ["1", "2", "3"]},
        reg_season_count=1,
        matchup_period_count=1,
        cards={100: fake_card(100, "Starter", {1: BOX_LINE})},
    )
    return attach_transactions(espn, by_day, names)


def test_a_waiver_claim_records_both_sides_and_the_bid(session: Session) -> None:
    espn = _tx_league(
        {
            2: [
                fake_transaction(
                    "tx-1",
                    team_id=3,
                    bid=17,
                    items=[tx_item(900, "ADD", to_team=3), tx_item(901, "DROP", from_team=3)],
                )
            ]
        },
        names={900: "Added Guy", 901: "Dropped Guy"},
    )

    ingest_season(session, espn)

    tx = session.scalars(select(Transaction)).one()
    assert tx.type == "WAIVER"
    assert tx.status == "EXECUTED"
    assert tx.bid_amount == 17
    assert tx.scoring_period == 2
    assert tx.processed_at is not None

    moves = {i.item_type: i for i in tx.items}
    assert set(moves) == {"ADD", "DROP"}
    assert moves["ADD"].player.name == "Added Guy"
    assert moves["ADD"].from_team_id is None, "team 0 is free agency, not a team"
    assert moves["ADD"].to_team_id is not None
    assert moves["DROP"].to_team_id is None


def test_a_failed_claim_is_kept_with_its_bid(session: Session) -> None:
    """A losing bid says who wanted a player and what they offered."""
    espn = _tx_league(
        {
            2: [
                fake_transaction(
                    "tx-win",
                    team_id=3,
                    bid=20,
                    status="EXECUTED",
                    items=[tx_item(900, "ADD", to_team=3)],
                ),
                fake_transaction(
                    "tx-lose",
                    team_id=21,
                    bid=12,
                    status="FAILED_INVALIDPLAYERSOURCE",
                    items=[tx_item(900, "ADD", to_team=21)],
                ),
            ]
        },
        names={900: "Contested Guy"},
    )

    ingest_season(session, espn)

    by_status = {t.status: t for t in session.scalars(select(Transaction)).all()}
    assert set(by_status) == {"EXECUTED", "FAILED_INVALIDPLAYERSOURCE"}
    assert by_status["FAILED_INVALIDPLAYERSOURCE"].bid_amount == 12


def test_a_trade_records_the_direction_each_player_moved(session: Session) -> None:
    espn = _tx_league(
        {
            3: [
                fake_transaction(
                    "tx-trade",
                    team_id=3,
                    type_="TRADE_ACCEPT",
                    bid=None,
                    items=[
                        tx_item(900, "TRADE", from_team=21, to_team=3),
                        tx_item(901, "TRADE", from_team=3, to_team=21),
                    ],
                )
            ]
        },
        names={900: "Incoming", 901: "Outgoing"},
    )

    ingest_season(session, espn)

    tx = session.scalars(select(Transaction)).one()
    assert tx.type == "TRADE_ACCEPT"
    teams = {t.espn_team_id: t.id for t in session.scalars(select(Team)).all()}
    moves = {i.player.name: i for i in tx.items}
    assert moves["Incoming"].from_team_id == teams[21]
    assert moves["Incoming"].to_team_id == teams[3]
    assert moves["Outgoing"].from_team_id == teams[3]


def test_a_transaction_with_no_items_is_still_stored(session: Session) -> None:
    """TRADE_UPHOLD carries none, which is what breaks espn-api's own parser."""
    espn = _tx_league(
        {2: [fake_transaction("tx-uphold", team_id=3, type_="TRADE_UPHOLD", items=None)]},
        names={},
    )

    ingest_season(session, espn)

    tx = session.scalars(select(Transaction)).one()
    assert tx.type == "TRADE_UPHOLD"
    assert tx.items == []


def test_a_player_never_rostered_is_created_from_the_transaction(session: Session) -> None:
    espn = _tx_league(
        {2: [fake_transaction("tx-1", team_id=3, items=[tx_item(777, "ADD", to_team=3)])]},
        names={777: "Passing Through"},
    )

    ingest_season(session, espn)

    names = {p.name for p in session.scalars(select(Player)).all()}
    assert "Passing Through" in names


def test_transactions_are_not_duplicated_on_reingest(session: Session) -> None:
    def build() -> Any:
        return _tx_league(
            {2: [fake_transaction("tx-1", team_id=3, items=[tx_item(900, "ADD", to_team=3)])]},
            names={900: "Added Guy"},
        )

    ingest_season(session, build())
    session.commit()
    ingest_season(session, build())
    session.commit()

    assert len(session.scalars(select(Transaction)).all()) == 1
    assert len(session.scalars(select(TransactionItem)).all()) == 1


def test_a_narrowed_run_does_not_touch_other_days(session: Session) -> None:
    by_day = {
        1: [fake_transaction("tx-day1", team_id=3, items=[tx_item(900, "ADD", to_team=3)])],
        3: [fake_transaction("tx-day3", team_id=3, items=[tx_item(901, "ADD", to_team=3)])],
    }
    names = {900: "Early", 901: "Late"}
    ingest_season(session, _tx_league(by_day, names))
    session.commit()
    assert len(session.scalars(select(Transaction)).all()) == 2

    ingest_season(session, _tx_league(by_day, names), IngestScope(frozenset({1}), frozenset({3})))
    session.commit()

    stored = {t.espn_transaction_id for t in session.scalars(select(Transaction)).all()}
    assert stored == {"tx-day1", "tx-day3"}, "the uncovered day was left alone"


def test_a_transaction_returned_on_two_days_is_stored_once(session: Session) -> None:
    """ESPN repeats a transaction across scoring periods.

    Looking it up by the day it was requested under misses the stored row and
    violates the unique constraint, which is what broke the first backfill.
    The payload's own scoringPeriodId is the truth.
    """
    repeated = fake_transaction("tx-repeat", team_id=3, items=[tx_item(900, "ADD", to_team=3)])
    # Returned on day 2 and again on day 3, both claiming to belong to day 2.
    espn = _tx_league(
        {2: [dict(repeated)], 3: [dict(repeated, scoringPeriodId=2)]},
        names={900: "Repeated Guy"},
    )
    # The fake stamps the requested day, so pin the second one back to day 2.
    espn.espn_request.league_get = lambda params=None, headers=None, extend="": {
        "transactions": [dict(repeated, scoringPeriodId=2)]
        if int((params or {}).get("scoringPeriodId") or 0) in (2, 3)
        else []
    }

    ingest_season(session, espn)

    stored = session.scalars(select(Transaction)).all()
    assert len(stored) == 1, "the same ESPN id must not create a second row"
    assert stored[0].scoring_period == 2, "the day comes from the payload, not the request"


def test_the_draft_is_stored_with_its_auction_prices(session: Session) -> None:
    home, away = fake_team(3, "A"), fake_team(21, "B")
    espn = league_with_days(
        teams=[home, away], boxes={}, days={}, windows={}, matchup_period_count=1
    )
    attach_draft(
        espn,
        [
            fake_pick(1, 1, 500, "First Overall", team=home, nominated_by=away, bid=100),
            fake_pick(1, 2, 501, "Second", team=away, nominated_by=away, bid=91),
        ],
    )

    ingest_season(session, espn)

    picks = session.scalars(select(DraftPick).order_by(DraftPick.round_pick)).all()
    assert len(picks) == 2
    teams = {t.espn_team_id: t.id for t in session.scalars(select(Team)).all()}
    assert picks[0].player.name == "First Overall"
    assert picks[0].bid_amount == 100
    assert picks[0].team_id == teams[3]
    assert picks[0].nominating_team_id == teams[21], "nominator is not always the buyer"


def test_a_drafted_player_we_never_rostered_is_created(session: Session) -> None:
    home = fake_team(3, "A")
    espn = league_with_days(teams=[home], boxes={}, days={}, windows={}, matchup_period_count=1)
    attach_draft(espn, [fake_pick(5, 3, 999, "Drafted And Cut", team=home)])

    ingest_season(session, espn)

    assert "Drafted And Cut" in {p.name for p in session.scalars(select(Player)).all()}


def test_the_draft_is_not_duplicated_on_reingest(session: Session) -> None:
    home = fake_team(3, "A")

    def build() -> Any:
        espn = league_with_days(teams=[home], boxes={}, days={}, windows={}, matchup_period_count=1)
        return attach_draft(espn, [fake_pick(1, 1, 500, "First", team=home, bid=50)])

    ingest_season(session, build())
    session.commit()
    ingest_season(session, build())
    session.commit()

    assert len(session.scalars(select(DraftPick)).all()) == 1


def test_each_season_keeps_its_own_draft(session: Session) -> None:
    """Auction prices move year to year; one season must not overwrite another."""
    home = fake_team(3, "A")

    first = league_with_days(teams=[home], boxes={}, days={}, windows={}, matchup_period_count=1)
    attach_draft(first, [fake_pick(1, 1, 500, "Star", team=home, bid=40)])
    ingest_season(session, first)
    session.commit()

    second = league_with_days(teams=[home], boxes={}, days={}, windows={}, matchup_period_count=1)
    second.year = 2027
    attach_draft(second, [fake_pick(1, 1, 500, "Star", team=home, bid=85)])
    ingest_season(session, second)
    session.commit()

    by_season = {
        p.league_season.season: p.bid_amount for p in session.scalars(select(DraftPick)).all()
    }
    assert by_season == {2026: 40, 2027: 85}
