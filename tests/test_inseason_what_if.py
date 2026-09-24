"""The what-if engine on a league small enough to check by hand.

Four teams, three seven-day weeks, a three-place lineup with one bench seat,
and a wire of two men. Small enough that a swap's effect on a category is
arithmetic a reader can do, and large enough that the Monte Carlo behind the
finish has something to simulate.

Four claims are worth the fixture, and they are the four ways this could be
quietly wrong.

**The judgement is the recommender's own.** A move the week report's search
also found has to read here exactly as it reads there, to the last decimal --
otherwise a manager who asks "and what about this one?" is told a different
story about the same swap. `test_a_named_move_is_the_recommenders_own_numbers`
holds the two together, so the day somebody changes one of them the other
fails rather than drifting.

**A change that changes nothing changes nothing.** The finish layer is one
engine read twice, and the second read is the first with a roster substituted;
if substituting a roster for itself moved a number, every finish delta on the
site would be noise.

**The simulation is the same twice.** A seed that did not hold would make a
refresh look like a decision.

**A refusal is a sentence.** Every way a change can be impossible gets words a
manager can act on, in the house style of docs/trades.md section 10.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod, Player, Team
from app.inseason.projected import project_standings
from app.inseason.what_if import Change, trade_finishes, what_if
from app.pickups.projection import clear_cache as clear_lines
from app.pickups.stream import stream_recommendations
from app.trades import TeamOffer, evaluate_trade
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    played,
    projected,
    snapshot,
)
from tests.scoring_db import held, league_season, matchup, player

SEASON = 2026
PERIODS = 3
SEASON_DAYS = list(range(1, PERIODS * 7 + 1))

#: The day every case is judged on: the first day of the second week, so one
#: week is banked, one is in front of us and one is still to come.
TODAY = 8

ALPHA, BRAVO, CHARLIE, DELTA = 1, 2, 3, 4

#: A useful man, and a weaker one who never rebounds. The wire's man is the
#: useful one with a week of rebounds added, which is the swap the first test
#: reads: nothing else about him differs, so REB is the category that moves.
STARTER: Mapping[str, float] = {
    "PTS": 20.0,
    "REB": 8.0,
    "AST": 4.0,
    "STL": 1.2,
    "BLK": 0.8,
    "3PM": 2.0,
    "TO": 2.4,
    "FGM": 8.0,
    "FGA": 17.0,
    "FTM": 4.0,
    "FTA": 5.0,
}
NO_BOARDS = {**STARTER, "REB": 0.2}
BOARDS = {**STARTER, "REB": 18.0}

#: What the first week's matchups posted, which is the only evidence the
#: league standard has: a level league would measure no spread at all and
#: leave every man worth nothing.
POSTED = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 20.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 235.0,
    "FGA": 500.0,
    "FTM": 78.0,
    "FTA": 100.0,
    "FG%": 235 / 500,
    "FT%": 0.78,
}


def posted(factor: float) -> dict[str, float]:
    out = {key: value * factor for key, value in POSTED.items() if not key.endswith("%")}
    out["FG%"] = out["FGM"] / out["FGA"]
    out["FT%"] = out["FTM"] / out["FTA"]
    return out


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    clear_lines()
    yield scoring_session


def _man(
    session: Session,
    name: str,
    per_game: Mapping[str, float],
    *,
    pro_team: int,
    injury: str = "ACTIVE",
) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=pro_team, on_team_id=0, injury_status=injury)
    projected(session, who, 70, per_game, season=SEASON)
    for day in range(1, TODAY):
        played(session, who, day, 30.0, per_game, season=SEASON)
    return who


def build(session: Session) -> dict[str, object]:
    """Four teams of three, a settled first week, and two men on the wire."""
    ls, teams, periods = league_season(
        session,
        season=SEASON,
        team_names=("Alpha", "Bravo", "Charlie", "Delta"),
        periods=PERIODS,
        regular_season_periods=PERIODS,
        days_per_period=7,
    )
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=1)
    ls.playoff_team_count = 2
    for index, window in enumerate(periods):
        second = 1 + index % 3
        others = [place for place in range(1, 4) if place != second]
        if index == 0:
            matchup(session, window, teams[0], teams[second], {teams[0]: posted(1.0)})
            matchup(
                session,
                window,
                teams[others[0]],
                teams[others[1]],
                {teams[others[0]]: posted(0.9), teams[others[1]]: posted(0.8)},
            )
        else:
            matchup(session, window, teams[0], teams[second])
            matchup(session, window, teams[others[0]], teams[others[1]])
    for pro_team in (10, 20, 30, 40, 50):
        games(session, pro_team, SEASON_DAYS, season=SEASON)

    who: dict[str, Player] = {}
    for index, team in enumerate(teams):
        pro_team = (index + 1) * 10
        for suffix, line in (("A", STARTER), ("B", STARTER), ("C", NO_BOARDS)):
            name = f"{team.name}{suffix}"
            who[name] = _man(session, name, line, pro_team=pro_team)
            snapshot(
                session,
                who[name],
                pro_team_id=pro_team,
                on_team_id=int(team.espn_team_id),
            )
            for day in (1, TODAY):
                held(session, team, periods[1], who[name], day, season=SEASON)
    # Alpha also carries a man ESPN has ruled out, so the injured-reserve
    # move is a case this fixture can ask at all.
    who["AlphaOut"] = _man(session, "AlphaOut", NO_BOARDS, pro_team=10, injury="OUT")
    snapshot(
        session,
        who["AlphaOut"],
        pro_team_id=10,
        on_team_id=ALPHA,
        injury_status="OUT",
    )
    for day in (1, TODAY):
        held(session, teams[0], periods[1], who["AlphaOut"], day, season=SEASON)

    for name, line in (("Boards", BOARDS), ("Spare", NO_BOARDS)):
        who[name] = _man(session, name, line, pro_team=50)
        on_the_wire(session, ls, who[name])
    session.flush()
    return {"ls": ls, "teams": teams, "periods": periods, "who": who}


def _ls(built: dict[str, object]) -> LeagueSeason:
    found = built["ls"]
    assert isinstance(found, LeagueSeason)
    return found


def _who(built: dict[str, object]) -> dict[str, Player]:
    found = built["who"]
    assert isinstance(found, dict)
    return found


def _teams(built: dict[str, object]) -> list[Team]:
    found = built["teams"]
    assert isinstance(found, list)
    return found


def _periods(built: dict[str, object]) -> list[MatchupPeriod]:
    found = built["periods"]
    assert isinstance(found, list)
    return found


def ask(
    session: Session,
    built: dict[str, object],
    *,
    team: int = ALPHA,
    add: tuple[str, ...] = (),
    drop: tuple[str, ...] = (),
    to_ir: tuple[str, ...] = (),
    today: int = TODAY,
    n_sims: int = 2000,
):
    who = _who(built)
    return what_if(
        session,
        _ls(built),
        team,
        today,
        add=[who[name].id for name in add],
        drop=[who[name].id for name in drop],
        to_ir=[who[name].id for name in to_ir],
        distributions=WEEK,
        bids=False,
        n_sims=n_sims,
    )


# ---------------------------------------------------------------------------
# the three layers
# ---------------------------------------------------------------------------


def test_a_swap_that_adds_rebounds_lifts_the_category_and_the_finish(
    session: Session,
) -> None:
    """The one case the whole module exists for, read end to end.

    AlphaC never rebounds and Boards rebounds twice as hard as anybody. The
    swap has to show up in three places at once: the week's REB chance, the
    projected final record, and the playoff odds -- and it has to show up in
    the same direction in all three, because they are one roster seen over
    three horizons.
    """
    built = build(session)
    answer = ask(session, built, add=("Boards",), drop=("AlphaC",))

    week = answer.week
    assert week.opponent_name == "Charlie"
    assert week.after["REB"] > week.before["REB"], "a week of rebounds is a week of rebounds"
    assert week.delta > 0
    assert next(shift.abbreviation for shift in week.moved()) == "REB", "the biggest mover"

    finish = answer.finish
    assert finish.record_after[0] > finish.record_before[0]
    assert finish.record_after[1] < finish.record_before[1]
    assert finish.playoff_odds_after >= finish.playoff_odds_before
    assert finish.place_after <= finish.place_before
    # Every remaining week is projected, this one included, and the weeks are
    # the same weeks against the same opponents in both worlds.
    assert [one.period for one in finish.weeks] == [2, 3]
    assert finish.weeks[0].in_play is True
    assert [one.opponent_name for one in finish.weeks] == ["Charlie", "Delta"]

    # And the judgement beside them, in the recommender's own currency.
    assert answer.judgement.delta_week == pytest.approx(week.delta)
    assert answer.net == pytest.approx(answer.judgement.delta_total)
    assert answer.kind == "swap"
    assert answer.adds[0].name == "Boards" and answer.drops[0].name == "AlphaC"


def test_the_weeks_ahead_are_the_same_pairings_before_and_after(session: Session) -> None:
    """A roster change cannot move the schedule, so both worlds play the same
    opponents in the same order; only the expected categories differ."""
    built = build(session)
    finish = ask(session, built, add=("Boards",), drop=("AlphaC",)).finish
    for week in finish.weeks:
        assert week.opponent_team_id is not None
        assert week.expected_before != pytest.approx(week.expected_after) or week.on_bye


def test_a_change_that_changes_nothing_leaves_the_projection_identical(
    session: Session,
) -> None:
    """A roster substituted for itself is the no-op the finish layer rests on.

    The finish is one engine read twice, the second time with a roster handed
    in. If handing in the roster the team already has moved a single number,
    every before-and-after on the site would be measuring the substitution
    rather than the move.
    """
    built = build(session)
    ls = _ls(built)
    teams = _teams(built)
    stood = project_standings(session, ls, TODAY, distributions=WEEK, n_sims=500)
    mine = stood.team(ALPHA)
    assert mine is not None

    from app.pickups.state import load_team_week

    week = load_team_week(session, ls, ALPHA, TODAY)
    same = project_standings(
        session,
        ls,
        TODAY,
        distributions=WEEK,
        n_sims=500,
        rosters={ALPHA: [player.player_id for player in week.active]},
    )
    for was, now in zip(stood.teams, same.teams, strict=True):
        assert was.team_id == now.team_id
        assert was.projected_record == now.projected_record
        assert was.finishes == now.finishes
        assert was.playoff_odds == now.playoff_odds
    assert len(teams) == 4


def test_the_finish_is_the_same_twice_under_the_seed(session: Session) -> None:
    """One seed, one answer: a refresh is not a decision."""
    built = build(session)
    first = ask(session, built, add=("Boards",), drop=("AlphaC",))
    again = ask(session, built, add=("Boards",), drop=("AlphaC",))
    assert first.finish.seed_odds_after == again.finish.seed_odds_after
    assert first.finish.playoff_odds_after == again.finish.playoff_odds_after
    assert first.finish.record_after == again.finish.record_after
    assert sum(first.finish.seed_odds_after) == pytest.approx(1.0)


def test_the_noise_band_is_the_binomial_at_the_simulation_count(session: Session) -> None:
    """The band a page prints beside an odds figure, and where it comes from."""
    built = build(session)
    finish = ask(session, built, add=("Boards",), drop=("AlphaC",), n_sims=2000).finish
    assert finish.n_sims == 2000
    wider = max(
        (finish.playoff_odds_before, finish.playoff_odds_after),
        key=lambda odds: odds * (1.0 - odds),
    )
    expected = 1.96 * (wider * (1.0 - wider) / 2000) ** 0.5
    assert finish.odds_band == pytest.approx(expected)
    assert finish.readable == (abs(finish.playoff_delta) > finish.odds_band)
    assert f"{finish.odds_band * 100:.1f}" in finish.noise_note
    assert "2,000 simulated seasons" in finish.noise_note
    # More seasons, a tighter band: the whole reason the number is reported.
    coarse = ask(session, built, add=("Boards",), drop=("AlphaC",), n_sims=500).finish
    assert coarse.odds_band > finish.odds_band or coarse.playoff_odds_after in (0.0, 1.0)


def test_an_injured_reserve_move_keeps_the_place_and_is_not_charged_a_drop(
    session: Session,
) -> None:
    """A man on injured reserve holds his roster place, so nothing is charged
    for him over the rest of the season -- which is the rule the week report's
    own `IR_MOVE` applies."""
    built = build(session)
    answer = ask(session, built, add=("Boards",), to_ir=("AlphaOut",))
    assert answer.kind == "ir_move"
    swap = ask(session, built, add=("Boards",), drop=("AlphaOut",))
    assert answer.judgement.delta_season_per_week > swap.judgement.delta_season_per_week


# ---------------------------------------------------------------------------
# the judgement is the recommender's, not a second opinion
# ---------------------------------------------------------------------------


def test_a_named_move_is_the_recommenders_own_numbers(session: Session) -> None:
    """The drift guard.

    The search's best move, asked for again by name. Every number the hurdle,
    the backtest and the bid are priced on has to come back identical: the
    week's delta, the season's change per week, the net, the projected record
    with and without, and the label.
    """
    built = build(session)
    ls = _ls(built)
    # No injured-reserve place, so the search's own best is a straight swap:
    # this test is about the drop being charged the same way on both paths,
    # and the injured-reserve shape has a test of its own.
    ls.injured_reserve_slots = 0
    session.flush()
    report = stream_recommendations(session, ls, ALPHA, TODAY, distributions=WEEK, bids=False)
    assert report.moves, "the fixture has to offer the search something"
    best = report.moves[0]
    assert best.drop is not None

    named = what_if(
        session,
        ls,
        ALPHA,
        TODAY,
        add=[best.add.player_id],
        drop=[best.drop.player_id],
        hurdle=report.hurdle,
        distributions=WEEK,
        bids=False,
        n_sims=500,
    )

    assert named.week.delta == pytest.approx(best.delta)
    assert named.judgement.delta_week == pytest.approx(best.judgement.delta_week)
    assert named.judgement.delta_season_per_week == pytest.approx(
        best.judgement.delta_season_per_week
    )
    assert named.judgement.weeks_remaining == pytest.approx(best.judgement.weeks_remaining)
    assert named.judgement.replacement == pytest.approx(best.judgement.replacement)
    assert named.judgement.record_without == best.judgement.record_without
    assert named.judgement.record_with == best.judgement.record_with
    assert named.net == pytest.approx(best.net)
    assert named.clears_hurdle == best.clears(report.hurdle)
    assert named.week.fills_empty_day == best.fills_empty_day
    assert named.week.add_starts[best.add.player_id] == best.add_starts
    assert named.week.drop_starts[best.drop.player_id] == best.drop_starts
    assert [shift.abbreviation for shift in named.week.moved()] == [
        shift.abbreviation for shift in best.moved()
    ]


def test_the_week_layer_is_the_week_the_report_already_shows(session: Session) -> None:
    """The chances before the change are the chances the week page prints."""
    built = build(session)
    ls = _ls(built)
    report = stream_recommendations(session, ls, ALPHA, TODAY, distributions=WEEK, bids=False)
    answer = ask(session, built, add=("Boards",), drop=("AlphaC",))
    assert answer.week.before == pytest.approx(dict(report.probabilities))
    assert answer.week.expected_before == pytest.approx(report.expected_wins)
    assert answer.week.days_remaining == report.days_remaining


# ---------------------------------------------------------------------------
# a trade's finish is the evaluator's own roster
# ---------------------------------------------------------------------------


def test_a_trades_finish_is_built_on_the_roster_the_deal_was_scored_on(
    session: Session,
) -> None:
    """Both sides change in one projection, because the deal happens to both."""
    built = build(session)
    ls = _ls(built)
    who = _who(built)
    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(team_id=ALPHA, gives=(who["AlphaC"].id,)),
        TeamOffer(team_id=BRAVO, gives=(who["BravoA"].id,)),
        distributions=WEEK,
    )
    finishes = trade_finishes(session, ls, report, distributions=WEEK, n_sims=500)

    assert set(finishes) == {ALPHA, BRAVO}
    for team_id, finish in finishes.items():
        assert finish.team_id == team_id
        assert sum(finish.seed_odds_before) == pytest.approx(1.0)
        assert sum(finish.seed_odds_after) == pytest.approx(1.0)
    # Alpha gives its worst man for a starter, so its record cannot fall and
    # Bravo's cannot rise: the deal is the same deal from both sides.
    assert finishes[ALPHA].record_after[0] >= finishes[ALPHA].record_before[0]
    assert finishes[BRAVO].record_after[0] <= finishes[BRAVO].record_before[0]
    # And the two were read off one pair of tables, so the places are a
    # league's places: nobody finishes in the same seat twice.
    assert finishes[ALPHA].place_after != finishes[BRAVO].place_after


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


def test_naming_nothing_is_refused(session: Session) -> None:
    built = build(session)
    with pytest.raises(ValueError, match="Name at least one man"):
        ask(session, built)


def test_dropping_a_man_the_team_does_not_hold_is_refused_by_name(session: Session) -> None:
    built = build(session)
    with pytest.raises(ValueError) as raised:
        ask(session, built, add=("Boards",), drop=("BravoA",))
    assert "Alpha does not have BravoA on its roster on day 8" in str(raised.value)


def test_adding_a_man_who_is_not_on_the_wire_is_refused_by_name(session: Session) -> None:
    built = build(session)
    with pytest.raises(ValueError) as raised:
        ask(session, built, add=("BravoA",), drop=("AlphaC",))
    assert "BravoA is not a free agent on day 8" in str(raised.value)
    assert "judge it as a trade" in str(raised.value)


def test_a_change_that_overfills_the_roster_is_refused(session: Session) -> None:
    """Four places, four men held, and two more arriving with nobody leaving."""
    built = build(session)
    with pytest.raises(ValueError) as raised:
        ask(session, built, add=("Boards", "Spare"))
    said = str(raised.value)
    assert "would hold 6 players after this change and the roster holds 4" in said


def test_a_change_that_empties_a_place_is_refused(session: Session) -> None:
    built = build(session)
    with pytest.raises(ValueError) as raised:
        ask(session, built, drop=("AlphaC",))
    assert "leaves Alpha a roster place short" in str(raised.value)


def test_an_injured_reserve_move_needs_a_free_place_and_an_injury(
    session: Session,
) -> None:
    built = build(session)
    ls = _ls(built)
    with pytest.raises(ValueError) as raised:
        ask(session, built, add=("Boards",), to_ir=("AlphaA",))
    assert "AlphaA is not ruled out on day 8" in str(raised.value)

    ls.injured_reserve_slots = 0
    session.flush()
    with pytest.raises(ValueError) as raised:
        ask(session, built, add=("Boards",), to_ir=("AlphaOut",))
    assert "no free injured-reserve place on day 8" in str(raised.value)


def test_a_change_outside_the_position_limits_is_refused(session: Session) -> None:
    built = build(session)
    _ls(built).position_limits = {"PG": 3}
    session.flush()
    with pytest.raises(ValueError) as raised:
        ask(session, built, add=("Boards",), drop=("AlphaC",))
    said = str(raised.value)
    assert "outside this league's position limits" in said
    assert "at most 3 at PG" in said


def test_a_day_in_no_matchup_period_cannot_be_projected(session: Session) -> None:
    built = build(session)
    with pytest.raises(ValueError, match="is in no matchup period"):
        ask(session, built, add=("Boards",), drop=("AlphaC",), today=400)


def test_the_change_dataclass_leaves_the_roster_in_the_order_it_was_held() -> None:
    """A roster is a list, and the projection seats it in weight order; the
    order here only has to be stable, so the same change gives the same list."""
    change = Change(team_id=ALPHA, adds=(9, 10), drops=(2,), to_ir=(3,))
    assert change.after([1, 2, 3, 4]) == (1, 4, 9, 10)
    assert Change(team_id=ALPHA).empty is True
    assert change.empty is False


# ---------------------------------------------------------------------------
# the stash view: adding or holding a man who is not playing yet
# ---------------------------------------------------------------------------


def _out_on_the_wire(session: Session, built: dict[str, object], last_played: int) -> Player:
    """A free agent ESPN has ruled out, who last played on `last_played`."""
    who = player(session, "Stashable")
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=50, on_team_id=0, injury_status="OUT")
    projected(session, who, 70, BOARDS, season=SEASON)
    for day in range(1, last_played + 1):
        played(session, who, day, 30.0, BOARDS, season=SEASON)
    on_the_wire(session, _ls(built), who)
    session.flush()
    _who(built)["Stashable"] = who
    return who


def test_adding_a_man_who_is_out_carries_the_stash_block(session: Session) -> None:
    """The line the page prints, with every number behind it.

    He last played on day 1 and it is day 8, so he is seven days out, which is
    a row the prior measures directly.
    """
    built = build(session)
    _ls(built).injured_reserve_slots = 0
    _out_on_the_wire(session, built, last_played=1)

    answer = ask(session, built, add=("Stashable",), drop=("AlphaC",))

    stash = answer.stash
    assert stash is not None
    assert stash.name == "Stashable"
    assert stash.days_out == 7
    assert sorted(stash.return_odds_by_week) == [1, 2, 4, 8]
    assert 0.0 < stash.return_odds_by_week[2] < 1.0
    assert stash.return_odds_by_week[8] >= stash.return_odds_by_week[2]
    assert stash.ir_slot_free is False
    assert stash.expected_dead_weeks > 0.0
    assert stash.dead_cost > 0.0
    # The mean arm is the recommender's own net, less the wait. Nothing is
    # re-derived and nothing is labelled against it.
    assert stash.expected_net == pytest.approx(answer.net - stash.dead_cost)
    assert stash.net_if_out_past_week < stash.expected_net
    assert 0.0 < stash.expected_games < stash.healthy_games
    assert "Out 7 days" in stash.line
    assert "back within a fortnight" in stash.line
    assert "not back by week 4" in stash.line
    assert "return record" in stash.language
    assert "diagnosis" in stash.language


def test_a_healthy_add_carries_no_stash_block(session: Session) -> None:
    built = build(session)
    _ls(built).injured_reserve_slots = 0
    assert ask(session, built, add=("Boards",), drop=("AlphaC",)).stash is None


def test_a_free_injured_reserve_place_makes_the_wait_free(session: Session) -> None:
    """The setting decides, and it is read from the stored row.

    With a place free the man goes to injured reserve and the roster place he
    would have held goes on being streamed, so the wait costs nothing and the
    net is the judgement's own.
    """
    built = build(session)
    _ls(built).injured_reserve_slots = 0
    _out_on_the_wire(session, built, last_played=1)
    charged = ask(session, built, add=("Stashable",), drop=("AlphaC",))

    _ls(built).injured_reserve_slots = 2
    session.flush()
    clear_lines()
    free = ask(session, built, add=("Stashable",), drop=("AlphaC",))

    assert charged.stash is not None
    assert free.stash is not None
    assert free.stash.ir_slot_free is True
    assert free.stash.dead_cost == 0.0
    assert free.stash.expected_dead_weeks == 0.0
    assert free.stash.expected_net == pytest.approx(free.net)
    assert charged.stash.dead_cost > 0.0


def test_a_man_moved_to_injured_reserve_carries_the_block_too(session: Session) -> None:
    built = build(session)
    answer = ask(session, built, add=("Boards",), to_ir=("AlphaOut",))
    assert answer.stash is not None
    assert answer.stash.name == "AlphaOut"


def test_the_week_report_names_the_men_it_is_counting_for_a_fraction(
    session: Session,
) -> None:
    built = build(session)
    _ls(built).injured_reserve_slots = 0
    session.flush()
    report = stream_recommendations(
        session, _ls(built), ALPHA, TODAY, distributions=WEEK, bids=False
    )
    assert [stash.name for stash in report.stashed] == ["AlphaOut"]
    assert report.stashed[0].days_out >= 1
