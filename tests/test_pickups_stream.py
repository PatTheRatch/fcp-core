"""The streaming report on constructed weeks.

Small lineups (three slots) so a full day is easy to build, both sides
level so a category starts as a coin flip, and free agents whose games fall
where the roster is short or where it is not. The cases are the ones the
design note names and the ones a games count gets wrong: a swap that moves
a coin flip, an empty day, a marginal swap the hurdle refuses, and a free
agent whose four games land on days the lineup is already full.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Matchup, MatchupPeriod, Player, Team
from app.pickups.bids import clear_cache
from app.pickups.stream import (
    ADD,
    IR_MOVE,
    STREAM_HURDLE,
    SWAP,
    head_to_head,
    stream_recommendations,
)
from app.scoring.lines import CategoryLine
from tests.pickups_db import (
    ANY,
    CENTRE,
    FORWARD,
    GUARD,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
)
from tests.scoring_db import NINE, held, league_season, matchup, player

HOME, AWAY = 1, 2

#: What both sides have posted so far: level in every category.
EVEN = {
    "PTS": 300.0,
    "REB": 120.0,
    "AST": 60.0,
    "STL": 18.0,
    "BLK": 12.0,
    "3PM": 30.0,
    "TO": 36.0,
    "FGM": 110.0,
    "FGA": 240.0,
    "FTM": 50.0,
    "FTA": 62.0,
    "FG%": 110 / 240,
    "FT%": 50 / 62,
}

TEN_POINTS = {"PTS": 10.0, "FGM": 4.0, "FGA": 8.0, "FTM": 2.0, "FTA": 2.5}


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


def build_week(
    session: Session,
    *,
    bench: int = 1,
    injured_reserve: int = 0,
    limits: Mapping[str, int] | None = None,
    bye: bool = False,
) -> tuple[LeagueSeason, Team, Team, MatchupPeriod]:
    """A season on the small lineup, with period 1 level so far."""
    ls, (home, away), (first, _) = league_season(session, days_per_period=7)
    configure(ls, lineup=SMALL_LINEUP, bench=bench, injured_reserve=injured_reserve, limits=limits)
    if bye:
        session.add(
            Matchup(
                matchup_period_id=first.id,
                home_team_id=home.id,
                away_team_id=None,
                winner="UNDECIDED",
            )
        )
        session.flush()
    else:
        matchup(session, first, home, away, {home: EVEN, away: EVEN})
    return ls, home, away, first


def rostered(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    name: str,
    *,
    slots: list[str],
    pro_team: int,
    per_game: Mapping[str, float],
    position: str = "PG",
    injury_status: str = "ACTIVE",
) -> Player:
    who = player(session, name)
    eligible(session, who, slots, position)
    snapshot(
        session,
        who,
        pro_team_id=pro_team,
        on_team_id=team.espn_team_id,
        injury_status=injury_status,
    )
    projected(session, who, 70, per_game)
    held(session, team, period, who, 1)
    return who


def free_agent(
    session: Session,
    ls: LeagueSeason,
    name: str,
    *,
    slots: list[str],
    pro_team: int,
    per_game: Mapping[str, float],
    position: str = "PG",
) -> Player:
    who = player(session, name)
    eligible(session, who, slots, position)
    snapshot(session, who, pro_team_id=pro_team, on_team_id=0)
    projected(session, who, 70, per_game)
    on_the_wire(session, ls, who)
    return who


def test_a_coin_flip_category_is_flipped_by_a_free_agent_with_three_games_left(
    session: Session,
) -> None:
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session, ls, "Blocker", slots=CENTRE, pro_team=20, per_game={"BLK": 3.0}, position="C"
    )
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.scoring_periods_remaining == (5, 6, 7)
    assert report.opponent_team_id == AWAY
    assert report.probabilities["BLK"] == pytest.approx(0.5), "level so far, nobody plays"
    assert report.expected_wins == pytest.approx(4.5)

    move = report.recommended
    assert move is not None
    assert move.kind == ADD and move.add.name == "Blocker" and move.drop is None
    assert move.add_starts == 3, "one game a day into an empty slot"
    assert move.delta >= STREAM_HURDLE
    assert move.fills_empty_day is True
    blocks = move.moved()[0]
    assert blocks.abbreviation == "BLK"
    # Nine blocks against a ten-block weekly spread over three of seven days.
    assert blocks.after == pytest.approx(
        head_to_head(CategoryLine({"BLK": 9}), CategoryLine(), WEEK, 3)["BLK"]
    )
    assert blocks.after > 0.9
    assert [shift.abbreviation for shift in move.moved()] == ["BLK"], "nothing else moved"
    assert [day.scoring_period for day in report.empty_days] == [5, 6, 7]
    assert all(day.fillers[0].name == "Blocker" for day in report.empty_days)


def test_the_empty_day_check_names_the_day_the_slots_and_who_could_fill_them(
    session: Session,
) -> None:
    ls, home, _, first = build_week(session, bench=1)
    rostered(session, home, first, "Guard", slots=GUARD, pro_team=10, per_game=TEN_POINTS)
    free_agent(session, ls, "Forward", slots=FORWARD, pro_team=20, per_game=TEN_POINTS)
    free_agent(session, ls, "Other Guard", slots=GUARD, pro_team=21, per_game=TEN_POINTS)
    free_agent(session, ls, "Never Plays", slots=ANY, pro_team=22, per_game=TEN_POINTS)
    games(session, 10, [5])
    games(session, 20, [6])
    games(session, 21, [7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    days = {day.scoring_period: day for day in report.empty_days}
    assert sorted(days) == [6, 7], "day 5 is short too, but no free agent plays then"
    assert days[6].empty_slots == ("F", "G", "UT")
    assert [p.name for p in days[6].fillers] == ["Forward"]
    assert [p.name for p in days[7].fillers] == ["Other Guard"]


def test_a_marginal_swap_is_listed_but_not_recommended(session: Session) -> None:
    ls, home, away, first = build_week(session, bench=1)
    for name in ("A", "B", "C", "D"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session, ls, "Slightly Better", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 10.5}
    )
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.open_slots == 0
    assert report.moves, "the swap is still shown"
    best = report.moves[0]
    assert best.kind == SWAP and best.add.name == "Slightly Better"
    assert 0 < best.delta < STREAM_HURDLE
    assert best.fills_empty_day is False, "every day was already full"
    assert best.clears(STREAM_HURDLE) is False
    assert report.recommended is None
    assert {move.kind for move in report.moves} == {SWAP}


def test_a_free_agent_whose_games_fall_on_full_days_adds_nothing(session: Session) -> None:
    ls, home, away, first = build_week(session, bench=2)
    for name in ("A", "B", "C", "D"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session, ls, "Bench Body", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 5.0}
    )
    games(session, 10, [4, 5, 6, 7])
    games(session, 11, [4, 5, 6, 7])
    games(session, 20, [4, 5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=4, distributions=WEEK)

    assert report.open_slots == 1
    # Three slots on four days, not four men on four days.
    assert report.projected.get("PTS") == pytest.approx(300 + 3 * 4 * 10)
    add = next(move for move in report.moves if move.kind == ADD)
    assert add.add.name == "Bench Body"
    assert add.add_starts == 0, "four games, none of them a start"
    assert add.delta == pytest.approx(0.0)
    assert add.fills_empty_day is False
    assert report.recommended is None


def test_a_better_free_agent_displaces_the_worst_starter_on_a_full_day(session: Session) -> None:
    ls, home, away, first = build_week(session, bench=2)
    for name in ("A", "B", "C"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Star", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 30.0})
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    add = next(move for move in report.moves if move.kind == ADD)
    assert add.add_starts == 3
    assert add.delta > 0
    assert add.fills_empty_day is False, "the lineup was full; a starter sits instead"
    assert add.moved()[0].abbreviation == "PTS"


def test_on_a_bye_there_is_no_head_to_head_and_no_move(session: Session) -> None:
    ls, home, _, first = build_week(session, bye=True)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    free_agent(session, ls, "Busy", slots=ANY, pro_team=20, per_game=TEN_POINTS)
    games(session, 10, [1])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.on_bye is True
    assert report.moves == ()
    assert report.expected_wins == 0.0
    assert report.recommended is None
    assert [day.scoring_period for day in report.empty_days] == [5, 6, 7], "still worth knowing"


def test_an_out_player_can_go_to_injured_reserve_to_make_room(session: Session) -> None:
    ls, home, away, first = build_week(session, bench=1, injured_reserve=1)
    rostered(session, home, first, "A", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, home, first, "B", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(
        session,
        home,
        first,
        "Hurt",
        slots=ANY,
        pro_team=10,
        per_game=TEN_POINTS,
        injury_status="OUT",
    )
    rostered(session, home, first, "Idle", slots=ANY, pro_team=12, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Fill In", slots=ANY, pro_team=20, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.open_slots == 0 and report.ir_slot_free is True
    move = report.recommended
    assert move is not None
    assert move.kind == IR_MOVE
    assert move.to_ir is not None and move.to_ir.name == "Hurt"
    assert move.drop is None
    assert move.add.name == "Fill In" and move.add_starts == 3
    assert move.fills_empty_day is True
    assert [m.add.name for m in report.moves] == ["Fill In"], "one row per pickup"
    assert move.delta > 0, "the empty slot is filled, and the IR move keeps a man over a swap"


def test_a_swap_must_respect_the_position_limits(session: Session) -> None:
    ls, home, away, first = build_week(session, bench=1, limits={"C": 1})
    rostered(
        session, home, first, "Big", slots=CENTRE, pro_team=10, per_game=TEN_POINTS, position="C"
    )
    for name in ("A", "B", "C"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session,
        ls,
        "Another Big",
        slots=CENTRE,
        pro_team=20,
        per_game={**TEN_POINTS, "PTS": 20.0},
        position="C",
    )
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.moves, "dropping the centre for him is legal"
    assert all(move.drop is not None and move.drop.name == "Big" for move in report.moves)


def test_the_pool_is_capped_by_this_weeks_value(session: Session) -> None:
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Better", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 20.0})
    free_agent(session, ls, "Worse", slots=ANY, pro_team=21, per_game=TEN_POINTS)
    games(session, 10, [1])
    games(session, 11, [1])
    games(session, 20, [5, 6, 7])
    games(session, 21, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK, pool_size=1)

    assert report.pool_size == 1
    assert {move.add.name for move in report.moves} == {"Better"}
    named = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK, pool=[])
    assert named.moves == () and named.pool_size == 0


def test_the_seasons_own_results_serve_as_the_spread_when_none_is_given(
    session: Session,
) -> None:
    """Without `distributions`, the report measures the week against what the
    league's teams have posted, which here is period 1 of this season."""
    ls, (home, away), (first, second) = league_season(session, days_per_period=7)
    configure(ls, lineup=SMALL_LINEUP, bench=1)
    posted = {home: {**EVEN, "PTS": 280.0}, away: {**EVEN, "PTS": 320.0}}
    matchup(session, first, home, away, posted)
    matchup(session, second, home, away, {home: EVEN, away: EVEN})
    rostered(session, home, second, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, second, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Scorer", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 25.0})
    games(session, 10, [1])
    games(session, 11, [1])
    games(session, 20, [12, 13])

    report = stream_recommendations(session, ls, HOME, today=12)

    assert report.matchup_period == 2
    assert set(report.probabilities) == set(NINE)
    assert report.recommended is not None
    assert report.recommended.add.name == "Scorer"


def test_head_to_head_is_settled_with_no_days_left_and_inverts_turnovers() -> None:
    mine = CategoryLine({"PTS": 500.0, "TO": 70.0, "BLK": 20.0, "FGM": 100.0, "FGA": 200.0})
    theirs = CategoryLine({"PTS": 480.0, "TO": 60.0, "BLK": 20.0, "FGM": 100.0, "FGA": 200.0})

    settled = head_to_head(mine, theirs, WEEK, days_remaining=0)
    open_week = head_to_head(mine, theirs, WEEK, days_remaining=7)

    assert settled["PTS"] == 1.0 and settled["TO"] == 0.0 and settled["BLK"] == 0.5
    assert settled["FG%"] == 0.5
    assert 0.5 < open_week["PTS"] < 1.0
    assert 0.0 < open_week["TO"] < 0.5, "more turnovers is losing"
    assert open_week["BLK"] == pytest.approx(0.5)
    # Twenty points over a hundred-point spread, a whole week to go.
    assert open_week["PTS"] == pytest.approx(0.5793, abs=0.001)


def test_every_move_carries_a_judgement_over_both_horizons(session: Session) -> None:
    """The ranking and the hurdle read the net, not the week (section 4.3's
    second pass). The week's own change is still on the move beside it."""
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Scorer", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 25.0})
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    move = report.moves[0]
    judgement = move.judgement
    assert judgement.delta_week == pytest.approx(move.delta), "the week's half is the week"
    assert move.net == pytest.approx(
        judgement.delta_week + judgement.delta_season_per_week * judgement.weeks_remaining
    )
    assert judgement.weeks_remaining == pytest.approx(1.0), "period 2 of a two-period season"
    assert judgement.delta_season_per_week > 0.0, "an add into an open place gains a place"
    assert judgement.replacement > 0.0, "the wire always gives something back"
    assert report.moves == tuple(sorted(report.moves, key=lambda m: -m.net)), "ranked on the net"

    outlook = report.outlook
    assert outlook.delta_total == 0.0 and outlook.record_with == outlook.record_without
    assert outlook.banked == (0.0, 0.0), "nothing has finished yet"


def test_a_week_of_gain_is_refused_when_the_season_costs_more(session: Session) -> None:
    """The principle's case, through the report: dropping a man who is worth
    keeping to win a category this week is a loss over the weeks left."""
    ls, home, away, first = build_week(session, bench=0)
    for name in ("A", "B"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    # A star with no games left in this period and every game after it:
    # dropping him costs the week nothing at all, and the season a great deal.
    rostered(
        session,
        home,
        first,
        "Star",
        slots=ANY,
        pro_team=12,
        per_game={"PTS": 40.0, "REB": 16.0, "AST": 8.0, "FGM": 16.0, "FGA": 30.0},
    )
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Body", slots=ANY, pro_team=20, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 12, [1, 2, 8, 9, 10, 11, 12, 13, 14])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    # Dropping the star wins the week outright: he plays no more games, so
    # the place he holds is dead, and the body who takes it fills a day the
    # lineup was leaving empty. A week-only ranking names that move.
    assert [day.scoring_period for day in report.empty_days] == [5, 6, 7]
    chosen = report.moves[0]
    assert chosen.add.name == "Body"
    assert chosen.drop is not None and chosen.drop.name in ("A", "B"), (
        "the season charge steers the drop away from the man worth keeping"
    )
    assert chosen.judgement.delta_season_per_week == pytest.approx(0.0, abs=0.02), (
        "swapping one ordinary man for another costs the season nothing"
    )


def test_a_recommended_move_is_priced_in_faab_unless_bids_are_off(session: Session) -> None:
    """The hook section 4.5 adds: a move worth making says what to pay for it.

    This league has no winning claims on record, so the number is nothing
    and the note says why. What is pinned here is that the bid is attached
    to a move that clears the hurdle, to no other, and not at all when the
    caller asks for none.
    """
    clear_cache()
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session, ls, "Blocker", slots=CENTRE, pro_team=20, per_game={"BLK": 3.0}, position="C"
    )
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])

    priced = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)
    quiet = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK, bids=False)

    move = priced.recommended
    assert move is not None and move.bid is not None
    assert move.bid.amount == 0
    assert move.bid.rank == 1, "the only man on the wire"
    assert "no winning FAAB bids on record" in move.bid.note
    assert all(other.bid is None for other in priced.moves if not other.clears(priced.hurdle))
    assert quiet.recommended is not None and quiet.recommended.bid is None
