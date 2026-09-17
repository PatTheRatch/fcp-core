"""The startable-starts counting, over rows the listener's tables would hold.

The cases that matter are the ones a games count gets wrong: a day where the
matching caps below the number of bodies, a day where nobody plays, a player
ruled out who must not be counted, and the two swaps that look alike in a
games count and are opposite in starts. Plus the matching helper itself,
which `can_field` now sits on, so a regression there would change feasibility
everywhere the optimizer uses it.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    League,
    LeagueSeason,
    MatchupPeriod,
    Player,
    PlayerSeasonStat,
    PlayerStatusSnapshot,
    ProTeamGame,
    Team,
)
from app.db.session import make_engine, make_session_factory
from app.draft.lineup import DEFAULT_LINEUP, can_field, max_matching
from app.inseason.startable import (
    NO_PRO_TEAM,
    RULED_OUT_STATUSES,
    matchup_window,
    roster_week,
    startable_starts,
    swap_gain,
    team_by_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SEASON = 2027
LEAGUE_ID = 1
#: The league's real 2027 lineup, from `league_seasons.lineup_slots` in the
#: live database: one of each position, one G, one F and three utility places.
LINEUP = ("PG", "SG", "SF", "PF", "C", "G", "F", "UT", "UT", "UT")
LEAGUE_TEAM_ID = 3

GUARD = ["PG", "SG", "G", "UT", "BE", "IR"]
BIG = ["PF", "C", "F", "UT", "BE", "IR"]
CENTRE = ["C", "PF/C", "F/C", "UT", "BE", "IR"]

OBSERVED = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
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
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as opened:
        opened.execute(text("TRUNCATE leagues, players, owners RESTART IDENTITY CASCADE"))
        opened.execute(text("TRUNCATE pro_team_games RESTART IDENTITY"))
        opened.commit()
        yield opened
        opened.rollback()


def _league_season(session: Session, *, season: int = SEASON) -> LeagueSeason:
    """A season row with this league's real 2027 lineup rules."""
    league = session.scalar(select(League).where(League.espn_league_id == LEAGUE_ID))
    if league is None:
        league = League(espn_league_id=LEAGUE_ID)
        session.add(league)
        session.flush()
    row = LeagueSeason(
        league_id=league.id,
        season=season,
        name="Patriot Games",
        scoring_type="H2H_CATEGORY",
        team_count=16,
        regular_season_periods=18,
        total_matchup_periods=21,
        playoff_team_count=7,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        auction_budget=200,
        median_scoring=False,
        raw_settings={},
        lineup_slots={"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3},
        bench_slots=3,
        injured_reserve_slots=1,
        position_limits={"C": 3},
    )
    session.add(row)
    session.flush()
    return row


def _team(session: Session, league_season: LeagueSeason, espn_team_id: int, name: str) -> Team:
    team = Team(league_season_id=league_season.id, espn_team_id=espn_team_id, name=name)
    session.add(team)
    session.flush()
    return team


def _player(
    session: Session,
    espn_player_id: int,
    name: str,
    *,
    slots: list[str],
    position: str = "PG",
) -> Player:
    player = Player(espn_player_id=espn_player_id, name=name)
    session.add(player)
    session.flush()
    session.add(
        PlayerSeasonStat(
            player_id=player.id,
            season=SEASON,
            kind="total",
            eligible_slots=slots,
            primary_position=position,
            raw_totals={},
        )
    )
    session.flush()
    return player


def _held(
    session: Session,
    player: Player,
    *,
    pro_team_id: int,
    on_team_id: int | None = LEAGUE_TEAM_ID,
    injury_status: str = "ACTIVE",
    observed_at: datetime = OBSERVED,
) -> PlayerStatusSnapshot:
    snapshot = PlayerStatusSnapshot(
        player_id=player.id,
        season=SEASON,
        observed_at=observed_at,
        pass_label="morning",
        injury_status=injury_status,
        injured=injury_status in RULED_OUT_STATUSES,
        pro_team_id=pro_team_id,
        on_team_id=on_team_id,
        status="ONTEAM" if on_team_id else "FREEAGENT",
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _games(session: Session, pro_team_id: int, days: list[int]) -> None:
    """`pro_team_games` rows for one NBA team, dated one day per scoring period.

    The date is period N = 2026-10-20 + (N-1) days, which is the shape the
    stored 2027 schedule has.
    """
    for day in days:
        session.add(
            ProTeamGame(
                season=SEASON,
                pro_team_id=pro_team_id,
                scoring_period=day,
                game_at=datetime(2026, 10, 20, 23, 0, tzinfo=UTC) + timedelta(days=day - 1),
                opponent_pro_team_id=99,
                home=True,
            )
        )
    session.flush()


def _ask(
    players: dict[int, list[str]], games_by_day: dict[int, list[int]]
) -> list[tuple[int, int, tuple[str, ...]]]:
    """(day, filled, empty) per day, for a constructed roster."""
    days = startable_starts(players, games_by_day, LINEUP)
    return [(day, slots.filled, slots.empty) for day, slots in sorted(days.items())]


# --- the matching helper, which can_field now sits on ---------------------


def test_max_matching_counts_the_slots_a_set_of_men_can_cover() -> None:
    guards = {i: GUARD for i in range(4)}

    # Four men fill four places: PG, SG, G and one of the three UT slots.
    assert max_matching(guards, LINEUP) == 4


def test_a_seventh_guard_stops_adding_starts() -> None:
    """The cap is the slots, not the bodies.

    A player eligible at PG, SG, G and UT reaches four places, and two of the
    three UT slots are genuinely guard-only in this league's lineup (SF, PF
    and C are not), so six guards is the last one that adds a start.
    """
    six = {i: GUARD for i in range(6)}
    seven = {i: GUARD for i in range(7)}

    assert max_matching(six, LINEUP) == 6, "PG, SG, G and all three UTs"
    assert max_matching(seven, LINEUP) == 6, "the seventh guard has nowhere to start"


def test_max_matching_is_capped_by_the_number_of_players() -> None:
    assert max_matching({1: GUARD}, LINEUP) == 1
    assert max_matching({}, LINEUP) == 0


def test_a_rigid_player_displaces_a_flexible_one_and_can_field_agrees() -> None:
    """The augmenting path, counted and asked the same way.

    Player 1 can play PG or SG; player 2 can only play PG. A greedy pass that
    seats player 1 at PG first would leave player 2 with nothing and report
    one slot of two. Matching moves player 1 to SG and fills both, which is
    the difference between the count and a count of eligible bodies.
    """
    roster = {1: ["PG", "SG"], 2: ["PG"]}

    assert max_matching(roster, ("PG", "SG")) == 2
    assert can_field(roster, lineup=("PG", "SG")) is True


def test_max_matching_does_not_count_bench_or_ir() -> None:
    assert max_matching({i: ["BE", "IR"] for i in range(13)}, LINEUP) == 0
    assert can_field({i: ["BE", "IR"] for i in range(13)}) is False


def test_can_field_still_refuses_thirteen_centres() -> None:
    """The case that motivated the matching, unchanged by the refactor."""
    assert can_field({i: CENTRE for i in range(13)}) is False
    assert max_matching({i: CENTRE for i in range(13)}, LINEUP) < len(LINEUP)


def test_can_field_still_accepts_a_lineup_it_can_cover_exactly() -> None:
    assert can_field({i: DEFAULT_LINEUP for i in range(len(DEFAULT_LINEUP))}) is True
    assert max_matching({i: DEFAULT_LINEUP for i in range(len(DEFAULT_LINEUP))}) == len(
        DEFAULT_LINEUP
    )


def test_max_matching_of_one_slot_says_whether_anyone_can_take_it() -> None:
    assert max_matching({1: ["PG"]}, ("PG",)) == 1
    assert max_matching({1: ["C"]}, ("PG",)) == 0
    assert max_matching({1: ["C"], 2: ["PG"]}, ("PG",)) == 1
    assert can_field({1: ["C"]}, lineup=("PG",)) is False


# --- the day, as the ticket describes it ---------------------------------


def test_three_guards_and_a_centre_against_a_day_only_guards_play() -> None:
    """The matching caps at the guard slots, not at the bodies available.

    Guards can cover PG, SG, G and the three UT places; the centre covers C,
    PF, F and UT. On a day only the guards play, four men fill at most four
    (or five) slots, and the PF/C/F slots are empty however many guards are
    on the roster.
    """
    roster = {1: GUARD, 2: GUARD, 3: GUARD, 4: CENTRE}

    (day, filled, empty) = _ask(roster, {1: [1, 2, 3]})[0]

    assert day == 1
    assert filled == 3, "three guards, three starts, and no more"
    assert "C" in empty and "PF" in empty and "F" in empty
    assert "PG" not in empty, "the guard slots are filled"


def test_a_day_where_nobody_plays_has_every_slot_empty() -> None:
    roster = {1: GUARD, 2: GUARD, 3: CENTRE}

    (_, filled, empty) = _ask(roster, {1: [], 2: [1, 2]})[0]

    assert filled == 0
    assert set(empty) == set(LINEUP)


def test_a_player_with_a_game_and_no_slot_is_worth_nothing_that_day() -> None:
    """The ticket's opening case: four shooting guards, two guard places.

    SG and G are the only slots these men reach, so a four-man night fills
    two and wastes two. A games count would report four.
    """
    roster = {i: ["SG", "G", "BE", "IR"] for i in range(1, 5)}
    days = startable_starts(roster, {1: [1, 2, 3, 4]}, LINEUP)

    assert days[1].filled == 2
    assert days[1].wasted == 2
    assert days[1].full is False, "two of ten slots is not a full lineup"


# --- the roster week, over stored rows -----------------------------------


def test_a_roster_with_a_game_on_every_day_fills_the_lineup_every_day(
    session: Session,
) -> None:
    league_season = _league_season(session)
    roster = {
        "Guard One": GUARD,
        "Guard Two": GUARD,
        "Wing": ["SF", "SG", "F", "UT"],
        "Big": BIG,
        "Centre": CENTRE,
    }
    for espn_id, (name, slots) in enumerate(roster.items(), start=1):
        player = _player(session, espn_id, name, slots=slots)
        _held(session, player, pro_team_id=13)
    _games(session, 13, [1, 2, 3])

    plan = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 3)

    assert [day.filled for day in plan.days] == [5, 5, 5]
    assert plan.starts == 15
    assert plan.capacity == 30
    assert plan.wasted == 0
    assert plan.empty_slots == 15, "five slots stay empty on each of the three days"


def test_a_player_ruled_out_is_not_counted(session: Session) -> None:
    league_season = _league_season(session)
    out = _player(session, 1, "Out Guard", slots=GUARD)
    _held(session, out, pro_team_id=13, injury_status="OUT")
    playing = _player(session, 2, "Playing Guard", slots=GUARD)
    _held(session, playing, pro_team_id=13, injury_status="ACTIVE")
    _games(session, 13, [1])

    plan = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 1)

    assert [player.name for player in plan.days[0].available] == ["Playing Guard"]
    assert plan.starts == 1


def test_a_questionable_player_is_still_counted(session: Session) -> None:
    """The rule the docstring argues for, pinned so it cannot drift."""
    league_season = _league_season(session)
    doubtful = _player(session, 1, "Questionable Guard", slots=GUARD)
    _held(session, doubtful, pro_team_id=13, injury_status="QUESTIONABLE")
    _games(session, 13, [1])

    plan = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 1)

    assert plan.starts == 1
    assert "OUT" in RULED_OUT_STATUSES


def test_a_team_with_no_game_that_day_has_nothing_available(session: Session) -> None:
    league_season = _league_season(session)
    player = _player(session, 1, "Guard", slots=GUARD)
    _held(session, player, pro_team_id=13)
    _games(session, 13, [1])
    # Day 2 has games, but not for this roster's team.
    _games(session, 27, [2])

    plan = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 2)

    assert plan.starts == 1
    assert plan.days[1].available == ()
    assert plan.days[1].wasted == 0
    assert len(plan.days[1].empty) == len(LINEUP)


def test_the_roster_is_read_from_the_latest_snapshot(session: Session) -> None:
    """A player dropped between passes is not on the roster any more."""
    league_season = _league_season(session)
    player = _player(session, 1, "Guard", slots=GUARD)
    _held(session, player, pro_team_id=13, on_team_id=LEAGUE_TEAM_ID)
    _held(
        session,
        player,
        pro_team_id=13,
        on_team_id=0,
        observed_at=OBSERVED + timedelta(hours=3),
    )
    _games(session, 13, [1])

    plan = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 1)

    assert plan.starts == 0


def test_a_player_with_no_nba_team_never_has_a_game(session: Session) -> None:
    league_season = _league_season(session)
    unsigned = _player(session, 1, "Unsigned", slots=GUARD)
    _held(session, unsigned, pro_team_id=NO_PRO_TEAM)
    _games(session, 13, [1])

    plan = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 1)

    assert plan.starts == 0


# --- the swaps -----------------------------------------------------------


def test_a_swap_that_fills_an_empty_slot_beats_one_that_adds_surplus(
    session: Session,
) -> None:
    """The ticket's comparison, and the reason starts beat a games count.

    The roster holds six guards and no big. Guards reach PG, SG, G and one UT
    between them, so four places stay bare every night and the centre is
    unclaimable by anyone on the roster. Adding a seventh guard buys nothing:
    every place he can occupy already has someone. Adding a centre claims the
    C slot on every day he plays.
    """
    league_season = _league_season(session)
    for espn_id in range(1, 7):
        guard = _player(session, espn_id, f"Guard {espn_id}", slots=GUARD)
        _held(session, guard, pro_team_id=13)
    spare = _player(session, 7, "Seventh Guard", slots=GUARD)
    _held(session, spare, pro_team_id=14, on_team_id=0)
    centre = _player(session, 8, "Centre", slots=CENTRE, position="C")
    _held(session, centre, pro_team_id=14, on_team_id=0)
    _games(session, 13, [1, 2, 3])
    _games(session, 14, [1, 2, 3])

    before = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 3)
    seventh_guard = swap_gain(session, league_season, LEAGUE_TEAM_ID, 1, 3, add_player=spare.id)
    a_centre = swap_gain(session, league_season, LEAGUE_TEAM_ID, 1, 3, add_player=centre.id)

    # Six guards cover PG, SG, G and one UT: six places, leaving SF, PF, C
    # and a UT bare. A seventh guard reaches no further, so the roster needs
    # a big man for the centre slot and nothing else buys starts.
    assert before.starts == 18, "six guards fill six places a day, three days"
    assert seventh_guard == 0, "a seventh guard has nowhere to start"
    assert a_centre == 3, "the centre slot is bare on all three days"
    assert a_centre > seventh_guard, "filling a bare slot beats adding surplus"


def test_a_swap_that_breaks_the_lineup_loses_starts(session: Session) -> None:
    """The case a games count cannot see: the man you drop was filling a slot.

    Two centres with the same eligibility, on teams that both play both
    nights, is a wash. When the incoming centre's team is idle on one of
    those nights it is not: the C slot goes empty, which is a start lost, and
    the added player's own game count says nothing about it.
    """
    league_season = _league_season(session)
    centre = _player(session, 1, "Centre", slots=CENTRE, position="C")
    _held(session, centre, pro_team_id=13)
    other_centre = _player(session, 2, "Other Centre", slots=CENTRE, position="C")
    _held(session, other_centre, pro_team_id=14, on_team_id=0)
    _games(session, 13, [1, 2])
    _games(session, 14, [1])

    before = roster_week(session, league_season, LEAGUE_TEAM_ID, 1, 2)
    gain = swap_gain(
        session,
        league_season,
        LEAGUE_TEAM_ID,
        1,
        2,
        add_player=other_centre.id,
        drop_player=centre.id,
    )

    assert before.starts == 2, "one centre fills one place on each of two nights"
    assert gain == -1, "the second night goes bare, and that is the whole cost"


def test_dropping_a_player_for_nobody_costs_the_starts_he_filled(
    session: Session,
) -> None:
    league_season = _league_season(session)
    centre = _player(session, 1, "Centre", slots=CENTRE, position="C")
    _held(session, centre, pro_team_id=13)
    _games(session, 13, [1, 2])

    gain = swap_gain(session, league_season, LEAGUE_TEAM_ID, 1, 2, drop_player=centre.id)

    assert gain == -2


def test_a_swap_with_nothing_on_either_side_gains_nothing(session: Session) -> None:
    league_season = _league_season(session)
    _games(session, 13, [1])

    assert swap_gain(session, league_season, LEAGUE_TEAM_ID, 1, 1) == 0


def test_dropping_a_player_the_roster_does_not_hold_changes_nothing(
    session: Session,
) -> None:
    league_season = _league_season(session)
    player = _player(session, 1, "Guard", slots=GUARD)
    _held(session, player, pro_team_id=13)
    stranger = _player(session, 2, "Someone Else", slots=GUARD)
    _held(session, stranger, pro_team_id=13, on_team_id=99)
    _games(session, 13, [1])

    gain = swap_gain(session, league_season, LEAGUE_TEAM_ID, 1, 1, drop_player=stranger.id)

    assert gain == 0


def test_a_swap_can_be_read_even_when_the_added_player_is_ruled_out(
    session: Session,
) -> None:
    """An add of an OUT player is worth zero starts, not an error."""
    league_season = _league_season(session)
    guard = _player(session, 1, "Guard", slots=["SG", "G", "UT"])
    _held(session, guard, pro_team_id=13)
    hurt = _player(session, 2, "Hurt Centre", slots=CENTRE, position="C")
    _held(session, hurt, pro_team_id=14, injury_status="OUT")
    _games(session, 13, [1])
    _games(session, 14, [1])

    assert swap_gain(session, league_season, LEAGUE_TEAM_ID, 1, 1, add_player=hurt.id) == 0


# --- the window and the lookup -------------------------------------------


def test_the_matchup_window_comes_from_the_stored_periods(session: Session) -> None:
    """Periods are not a fixed length, so the window is read, not computed."""
    league_season = _league_season(session)
    session.add_all(
        [
            MatchupPeriod(
                league_season_id=league_season.id,
                period=1,
                is_playoff=False,
                first_scoring_period=1,
                final_scoring_period=6,
            ),
            MatchupPeriod(
                league_season_id=league_season.id,
                period=2,
                is_playoff=False,
                first_scoring_period=7,
                final_scoring_period=13,
            ),
        ]
    )
    session.flush()

    assert matchup_window(session, league_season, 1) == (1, 6)
    assert matchup_window(session, league_season, 2) == (7, 13)
    assert matchup_window(session, league_season, 9) is None


def test_a_team_is_found_by_name_or_a_unique_prefix(session: Session) -> None:
    league_season = _league_season(session)
    _team(session, league_season, 3, "Through The Wire")
    _team(session, league_season, 21, "Load Management")

    assert team_by_name(session, league_season, "Through The Wire") is not None
    assert team_by_name(session, league_season, "through") is not None
    assert team_by_name(session, league_season, "Nobody") is None


def test_a_team_name_matching_two_teams_is_not_guessed(session: Session) -> None:
    """A prefix that picks out two teams is ambiguous, so it finds nothing."""
    league_season = _league_season(session)
    _team(session, league_season, 3, "Through The Wire")
    _team(session, league_season, 21, "Through The Ringer")

    assert team_by_name(session, league_season, "Through The") is None
