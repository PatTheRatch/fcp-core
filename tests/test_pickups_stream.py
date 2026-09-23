"""The streaming report on constructed weeks.

Small lineups (three slots) so a full day is easy to build, both sides
level so a category starts as a coin flip, and free agents whose games fall
where the roster is short or where it is not. The cases are the ones the
design note names and the ones a games count gets wrong: a swap that moves
a coin flip, an empty day, a marginal swap the hurdle refuses, and a free
agent whose four games land on days the lineup is already full.
"""

from collections.abc import Iterator, Mapping
from datetime import date

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
from scripts.stream import render
from tests.pickups_db import (
    ANY,
    CENTRE,
    FORWARD,
    GUARD,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    clears_waivers_on,
    configure,
    day_date,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
    winning_bid,
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
    back_on: date | None = None,
) -> Player:
    who = player(session, name)
    eligible(session, who, slots, position)
    snapshot(
        session,
        who,
        pro_team_id=pro_team,
        on_team_id=team.espn_team_id,
        injury_status=injury_status,
        expected_return_date=back_on,
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
    clears_on: int | None = None,
) -> Player:
    """A man on the wire. `clears_on` puts him on waivers until that day."""
    who = player(session, name)
    eligible(session, who, slots, position)
    snapshot(session, who, pro_team_id=pro_team, on_team_id=0)
    projected(session, who, 70, per_game)
    on_the_wire(
        session,
        ls,
        who,
        status="FREEAGENT" if clears_on is None else "WAIVERS",
        clears_at=None if clears_on is None else clears_waivers_on(clears_on),
    )
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

    assert len(report.recommended) == 1, "one pickup is all the wire offers"
    move = report.recommended[0]
    assert move.kind == ADD and move.add.name == "Blocker" and move.drop is None
    assert move.add_starts == 3, "one game a day into an empty slot"
    assert move.delta >= STREAM_HURDLE
    assert move.fills_empty_day is True
    blocks = move.moved()[0]
    assert blocks.abbreviation == "BLK"
    # Nine blocks against a ten-block weekly spread, doubled by
    # `SPREAD_SCALE`, over three of seven days: 9 / (10 * 2 * sqrt(3/7)) is
    # 0.687 of a standard deviation, which is a 75% chance. Before the spread
    # was widened on 2026-09-23 the same nine blocks read 92%.
    assert blocks.after == pytest.approx(
        head_to_head(CategoryLine({"BLK": 9}), CategoryLine(), WEEK, 3)["BLK"]
    )
    assert blocks.after == pytest.approx(0.7541, abs=0.0005)
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
    assert report.recommended == ()
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
    assert report.recommended == ()


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
    assert report.recommended == ()
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
    assert len(report.recommended) == 1
    move = report.recommended[0]
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
    assert report.recommended
    assert report.recommended[0].add.name == "Scorer"


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
    # Twenty points over a hundred-point spread doubled by `SPREAD_SCALE`, a
    # whole week to go: a tenth of a standard deviation, 54%. It read 58% on
    # the undoubled spread, before 2026-09-23.
    assert open_week["PTS"] == pytest.approx(0.5398, abs=0.001)


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


def test_the_cli_prints_both_horizons_and_the_projected_record(session: Session) -> None:
    """The layout is the CLI's own, but a field it cannot read is a crash on
    a real week, so the render is exercised on a report the tests build."""
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Scorer", slots=ANY, pro_team=20, per_game={**TEN_POINTS, "PTS": 25.0})
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])
    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    text = render(report, season=2026, team_name="Home", opponent_name="Away", when=None)

    assert "moves, by net categories over both horizons:" in text
    assert "projected record" in text
    assert "season so far:" in text
    assert "categories net" in text, "the recommendation is named in the net"


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

    assert priced.recommended
    move = priced.recommended[0]
    assert move.bid is not None
    assert move.bid.amount == 0
    assert move.bid.rank == 1, "the only man on the wire"
    assert "no winning FAAB bids on record" in move.bid.note
    assert all(other.bid is None for other in priced.moves if not other.clears(priced.hurdle))
    assert quiet.recommended and quiet.recommended[0].bid is None


def test_two_independent_moves_are_planned_for_one_day(session: Session) -> None:
    """Patrick's rule: two swaps in a day are right when each stands alone.

    One man plays this week and three do not, so two of the three lineup
    slots go empty every day. Two free agents each fill one of them, and the
    second is found by re-running the week with the first already made.
    """
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Playing", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    for name in ("Dead One", "Dead Two", "Dead Three"):
        rostered(session, home, first, name, slots=ANY, pro_team=12, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Streamer One", slots=ANY, pro_team=20, per_game=TEN_POINTS)
    free_agent(session, ls, "Streamer Two", slots=ANY, pro_team=21, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 12, [1, 2])
    games(session, 20, [5, 6, 7])
    games(session, 21, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.adds_budget == 7 and report.adds_used == 0
    assert [len(day.empty_slots) for day in report.empty_days] == [2, 2, 2], "two a day"
    plan = report.recommended
    assert len(plan) == 2, "one add does not fill both empty slots"
    assert {move.add.name for move in plan} == {"Streamer One", "Streamer Two"}
    dropped = [move.drop.name for move in plan if move.drop is not None]
    assert len(set(dropped)) == 2 and set(dropped) <= {"Dead One", "Dead Two", "Dead Three"}
    assert all(move.clears(report.hurdle) for move in plan)
    assert all(move.fills_empty_day for move in plan), "each fills a day of its own"
    assert plan[1].add_starts == 3, "the second man plays, he does not sit behind the first"

    text = render(report, season=2026, team_name="Home", opponent_name="Away", when=None)
    assert "worth a look, in this order" in text
    assert "adds this period: used 0 of 7" in text


def test_a_second_move_that_only_refills_the_first_moves_empty_day_is_not_planned(
    session: Session,
) -> None:
    """The honest half of the rule. Both free agents fill the same one empty
    slot, so the list clears the hurdle twice and the plan is one move."""
    ls, home, away, first = build_week(session, bench=1)
    for name in ("Playing One", "Playing Two"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    for name in ("Dead One", "Dead Two"):
        rostered(session, home, first, name, slots=ANY, pro_team=12, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Streamer One", slots=ANY, pro_team=20, per_game=TEN_POINTS)
    free_agent(session, ls, "Streamer Two", slots=ANY, pro_team=21, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])
    games(session, 12, [1, 2])
    games(session, 20, [5, 6, 7])
    games(session, 21, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert [len(day.empty_slots) for day in report.empty_days] == [1, 1, 1], "one a day"
    listed = [move for move in report.moves if move.clears(report.hurdle)]
    assert len(listed) == 2, "read off the list, both pickups look worth making"
    assert {move.add.name for move in listed} == {"Streamer One", "Streamer Two"}
    assert len(report.recommended) == 1, "the second only refills the first's empty day"
    assert report.recommended[0].add.name in {"Streamer One", "Streamer Two"}


def test_with_no_adds_left_the_report_still_lists_moves_and_recommends_none(
    session: Session,
) -> None:
    """One add a day of the period, spent on any days: seven here, and the
    seventh is the last. The wire is still worth reading, and nothing is
    recommended until the next period.

    All seven land on or before the day asked about, because what has been
    spent is counted through today and not over the period's whole span --
    a report for day 5 that charged the team for claims it makes on day 6
    would silence itself for a reason that is not true (`state._adds_in_period`).
    """
    ls, home, away, first = build_week(session, bench=1)
    idle = rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session, ls, "Blocker", slots=CENTRE, pro_team=20, per_game={"BLK": 3.0}, position="C"
    )
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])
    # Seven adds over the first five days: two on day 1, two on day 2, one
    # each on 3, 4 and 5. More than one add in a day is what this league
    # allows, and 204 of its 2026 team-days had one.
    for day in (1, 2, 3, 4, 5):
        winning_bid(session, home, day, 0, idle)
    for day in (1, 2):
        winning_bid(session, home, day, 0, player(session, f"Also day {day}"))

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert (report.adds_used, report.adds_budget, report.adds_left) == (7, 7, 0)
    assert report.faab_remaining == 100, "the adds cost no FAAB here"
    assert report.moves and report.moves[0].clears(report.hurdle), "the move is a good one"
    assert report.recommended == (), "there is no add to make it with"

    text = render(report, season=2026, team_name="Home", opponent_name="Away", when=None)
    assert "no adds left this period (adds this period: used 7 of 7)" in text
    assert "worth a look" not in text


def test_a_free_agent_on_waivers_cannot_play_before_he_clears(session: Session) -> None:
    """The 48-hour rule, through the report: he is worth claiming, and worth
    nothing on the days before the claim resolves."""
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(
        session,
        ls,
        "Blocker",
        slots=CENTRE,
        pro_team=20,
        per_game={"BLK": 3.0},
        position="C",
        clears_on=6,
    )
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert len(report.recommended) == 1
    move = report.recommended[0]
    assert move.add.name == "Blocker"
    assert move.add.games_remaining_this_period == 3, "the games are his; the first is not ours"
    assert move.add.waiver_clears_on == 6
    assert move.add.seatable_on(5) is False and move.add.seatable_on(6) is True
    assert move.add_starts == 2, "days 6 and 7 only"
    assert [day.scoring_period for day in report.empty_days] == [6, 7], (
        "day 5 is short too, but nobody on the wire can be seated on it"
    )

    text = render(report, season=2026, team_name="Home", opponent_name="Away", when=None)
    assert f"on waivers, clears {day_date(6):%A}" in text


def test_a_pool_named_by_id_is_taken_as_men_who_are_free_agents_now(session: Session) -> None:
    """The backtest's path: a pool named by id carries no waiver state, so a
    historical replay seats every man on every day he plays."""
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Idle", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    blocker = free_agent(
        session,
        ls,
        "Blocker",
        slots=CENTRE,
        pro_team=20,
        per_game={"BLK": 3.0},
        position="C",
        clears_on=6,
    )
    games(session, 10, [1, 2])
    games(session, 11, [1, 2])
    games(session, 20, [5, 6, 7])

    report = stream_recommendations(
        session, ls, HOME, today=5, distributions=WEEK, pool=[blocker.id]
    )

    assert len(report.recommended) == 1
    move = report.recommended[0]
    assert move.add.waiver_clears_on is None
    assert move.add_starts == 3


# ---------------------------------------------------------------------------
# the schedule: games, seated, open, day by day
# ---------------------------------------------------------------------------


def test_the_schedule_counts_games_by_men_who_are_not_ruled_out(session: Session) -> None:
    """The owner's three complaints, 2026-09-23, in one week.

    A man ESPN has OUT with no date back plays no day of it; a man OUT with
    a date back plays the days from it and none before; and what is counted
    is games, not the places a lineup happens to have men in.
    """
    ls, home, away, first = build_week(session, bench=3)
    rostered(session, home, first, "Fit", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(
        session,
        home,
        first,
        "Out For The Year",
        slots=ANY,
        pro_team=10,
        per_game=TEN_POINTS,
        injury_status="OUT",
    )
    rostered(
        session,
        home,
        first,
        "Back On Seven",
        slots=ANY,
        pro_team=10,
        per_game=TEN_POINTS,
        injury_status="OUT",
        back_on=day_date(7),
    )
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    mine = {day.scoring_period: day.mine for day in report.schedule.days}
    assert sorted(mine) == [5, 6, 7], "the days left, and no day already played"
    assert [mine[day].games for day in (5, 6, 7)] == [1, 1, 2], (
        "the man with no date back never counts; the other counts from the day he is back"
    )
    assert [mine[day].seated for day in (5, 6, 7)] == [1, 1, 2], "three places, never full"
    assert [mine[day].open_places for day in (5, 6, 7)] == [2, 2, 1]
    assert sorted(man.player.name for man in mine[7].men) == ["Back On Seven", "Fit"]
    assert all(man.seated for man in mine[7].men)
    assert [man.player.name for man in mine[5].men] == ["Fit"]


def test_the_schedule_seats_what_the_lineup_holds_and_no_more(session: Session) -> None:
    """More games than places is the whole reason a games count is not an
    answer on its own: four men play, three places, one game goes nowhere."""
    ls, home, away, first = build_week(session, bench=3)
    for name in ("A", "B", "C", "D"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])
    games(session, 11, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    mine = {day.scoring_period: day.mine for day in report.schedule.days}
    assert [mine[day].games for day in (5, 6, 7)] == [4, 4, 4]
    assert [mine[day].seated for day in (5, 6, 7)] == [3, 3, 3], "the lineup is three places"
    assert [mine[day].open_places for day in (5, 6, 7)] == [0, 0, 0]
    benched = [man.player.name for man in mine[5].men if not man.seated]
    assert len(benched) == 1, "one man a day is a game that will not count"
    # The seating the week was projected from and nothing else: the day's
    # starts add up to the starts the projection scored.
    assert report.projected.get("PTS") == pytest.approx(300 + 3 * 3 * 10)


def test_the_schedule_is_the_same_arithmetic_on_the_other_side(session: Session) -> None:
    """The opponent's row is his own roster seated by the same rule, and the
    totals down each side are the days added up."""
    ls, home, away, first = build_week(session, bench=3)
    for name in ("A", "B", "C", "D"):
        rostered(session, home, first, name, slots=ANY, pro_team=10, per_game=TEN_POINTS)
    for name in ("Rival", "Rival Two"):
        rostered(session, away, first, name, slots=ANY, pro_team=11, per_game=TEN_POINTS)
    rostered(
        session,
        away,
        first,
        "Their Hurt Man",
        slots=ANY,
        pro_team=11,
        per_game=TEN_POINTS,
        injury_status="OUT",
    )
    games(session, 10, [5, 6, 7])
    games(session, 11, [6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    mine = [day.mine for day in report.schedule.days]
    theirs = [day.theirs for day in report.schedule.days]
    assert all(side is not None for side in theirs)
    assert [side.games for side in theirs if side] == [0, 2, 2], (
        "their OUT man is no more counted than ours; on day 5 his NBA team does not play"
    )
    assert [side.seated for side in theirs if side] == [0, 2, 2]
    assert [side.open_places for side in theirs if side] == [3, 1, 1]

    total = report.schedule.mine_total
    their_total = report.schedule.theirs_total
    assert their_total is not None
    assert (total.games, total.seated, total.open_places) == (12, 9, 0)
    assert (their_total.games, their_total.seated, their_total.open_places) == (4, 4, 5)
    assert total.men == () and their_total.men == (), "a total is a sum, not a list of men"
    for sums, sides in ((total, mine), (their_total, [side for side in theirs if side])):
        assert sums.games == sum(side.games for side in sides)
        assert sums.seated == sum(side.seated for side in sides)
        assert sums.open_places == sum(side.open_places for side in sides)


def test_on_a_bye_the_schedule_has_one_side(session: Session) -> None:
    ls, home, _, first = build_week(session, bye=True)
    rostered(session, home, first, "Fit", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])

    report = stream_recommendations(session, ls, HOME, today=5, distributions=WEEK)

    assert report.schedule.theirs_total is None
    assert all(day.theirs is None for day in report.schedule.days)
    assert report.schedule.mine_total.games == 3
