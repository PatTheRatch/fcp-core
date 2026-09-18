"""The week as the recommender sees it, built from stored rows.

What matters is where each fact comes from: the roster from the latest
lineup day, or the snapshots before there is one; the status and NBA team
from the snapshot, never the lineup row; the games from the schedule, less
the days ESPN has ruled out; FAAB from the pot that is actually the pot.
"""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.models import Matchup, RosterSlot
from app.pickups.state import (
    load_free_agents,
    load_team_week,
    playable_days,
    season_calendar,
)
from tests.pickups_db import (
    ANY,
    GUARD,
    OBSERVED,
    OPENING,
    SEASON,
    clear_schedule,
    clears_waivers_on,
    configure,
    day_date,
    eligible,
    games,
    on_the_wire,
    snapshot,
    winning_bid,
)
from tests.scoring_db import held, league_season, matchup, player

HOME, AWAY = 1, 2


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


def test_a_team_week_counts_the_days_left_and_each_players_games(session: Session) -> None:
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = player(session, "Guard")
    eligible(session, guard, GUARD)
    snapshot(session, guard, pro_team_id=10, on_team_id=HOME)
    games(session, 10, [3, 5, 7])
    held(session, home, first, guard, 3)

    week = load_team_week(session, ls, HOME, today=4)

    assert week.matchup_period == 1
    assert week.scoring_periods_remaining == (4, 5, 6, 7)
    assert week.days_remaining == 4
    (rostered,) = week.roster
    assert rostered.name == "Guard"
    assert rostered.game_days == (5, 7), "the game on day 3 is behind us"
    assert rostered.games_remaining_this_period == 2
    assert rostered.eligible == frozenset(GUARD)
    assert rostered.on_ir is False
    # Ten starters and three bench places, one held.
    assert week.open_slots == 12
    assert week.ir_slot_free is True


def test_a_bye_has_no_opponent_and_nothing_posted(session: Session) -> None:
    ls, (home, _), (first, _) = league_season(session)
    configure(ls)
    games(session, 10, [1])
    session.add(
        Matchup(
            matchup_period_id=first.id,
            home_team_id=home.id,
            away_team_id=None,
            winner="UNDECIDED",
        )
    )
    session.flush()

    week = load_team_week(session, ls, HOME, today=2)

    assert week.on_bye is True
    assert week.opponent_team_id is None
    assert week.my_totals.counts == {}


def test_the_opponent_and_the_totals_posted_so_far_come_from_the_live_matchup(
    session: Session,
) -> None:
    ls, (home, away), (first, _) = league_season(session)
    configure(ls)
    games(session, 10, [1])
    matchup(
        session,
        first,
        home,
        away,
        {
            home: {"PTS": 210, "FGM": 80, "FGA": 170, "FG%": 0.4706},
            away: {"PTS": 190, "FGM": 70, "FGA": 160, "FG%": 0.4375},
        },
    )

    week = load_team_week(session, ls, HOME, today=3)

    assert week.opponent_team_id == AWAY
    assert week.my_totals.get("PTS") == 210
    assert week.my_totals.get("FGA") == 170
    assert "FG%" not in week.my_totals.counts, "a percentage is rebuilt, never stored"
    assert week.my_totals.totals(["FG%"])["FG%"] == pytest.approx(80 / 170)
    assert week.opp_totals.get("PTS") == 190


def test_a_player_on_injured_reserve_is_flagged_and_uses_the_slot(session: Session) -> None:
    ls, (home, _), (first, _) = league_season(session)
    configure(ls, injured_reserve=1)
    hurt = player(session, "Hurt")
    fit = player(session, "Fit")
    for who in (hurt, fit):
        eligible(session, who, GUARD)
        snapshot(session, who, pro_team_id=10, on_team_id=HOME)
    games(session, 10, [2, 4])
    held(session, home, first, hurt, 1, slot="IR")
    held(session, home, first, fit, 1, slot="PG")

    week = load_team_week(session, ls, HOME, today=2)

    by_name = {p.name: p for p in week.roster}
    assert by_name["Hurt"].on_ir is True
    assert by_name["Fit"].on_ir is False
    assert [p.name for p in week.active] == ["Fit"]
    assert week.ir_slot_free is False
    assert week.open_slots == 12, "the IR place is not a roster place"


def test_an_out_player_comes_back_on_the_day_espn_says(session: Session) -> None:
    ls, (home, _), (first, _) = league_season(session)
    configure(ls)
    dated = player(session, "Dated")
    undated = player(session, "Undated")
    for who in (dated, undated):
        eligible(session, who, GUARD)
        held(session, home, first, who, 1)
    snapshot(
        session,
        dated,
        pro_team_id=10,
        on_team_id=HOME,
        injury_status="OUT",
        expected_return_date=day_date(5),
    )
    snapshot(session, undated, pro_team_id=10, on_team_id=HOME, injury_status="OUT")
    games(session, 10, [2, 4, 5, 7])

    week = load_team_week(session, ls, HOME, today=2)

    by_name = {p.name: p for p in week.roster}
    assert by_name["Dated"].game_days == (5, 7), "days 2 and 4 are before his return"
    assert by_name["Dated"].ruled_out is True
    assert by_name["Undated"].game_days == (), "OUT with no date is out for the week"


def test_playable_days_removes_exactly_the_games_before_the_return_date() -> None:
    schedule = {1: day_date(1), 3: day_date(3), 5: day_date(5)}
    days = range(1, 8)

    assert playable_days(schedule, days, injury_status="ACTIVE", expected_return_date=None) == (
        1,
        3,
        5,
    )
    assert playable_days(schedule, days, injury_status="OUT", expected_return_date=day_date(3)) == (
        3,
        5,
    )
    assert playable_days(schedule, days, injury_status="OUT", expected_return_date=day_date(4)) == (
        5,
    )
    assert playable_days(schedule, days, injury_status="OUT", expected_return_date=None) == ()
    assert playable_days(
        schedule, days, injury_status="QUESTIONABLE", expected_return_date=None
    ) == (1, 3, 5), "questionable is counted, as startable counts it"


def test_before_the_first_lineup_day_the_roster_is_the_snapshots(session: Session) -> None:
    ls, _, _ = league_season(session)
    configure(ls)
    mine = player(session, "Mine")
    theirs = player(session, "Theirs")
    nobody = player(session, "Nobody")
    for who in (mine, theirs, nobody):
        eligible(session, who, ANY)
    snapshot(session, mine, pro_team_id=10, on_team_id=HOME)
    snapshot(session, theirs, pro_team_id=10, on_team_id=AWAY)
    snapshot(session, nobody, pro_team_id=10, on_team_id=0)
    games(session, 10, [1, 2])

    week = load_team_week(session, ls, HOME, today=1)

    assert [p.name for p in week.roster] == ["Mine"]
    assert week.roster[0].game_days == (1, 2)


def test_the_latest_lineup_day_wins_over_older_ones_and_over_the_snapshots(
    session: Session,
) -> None:
    ls, (home, _), (first, _) = league_season(session)
    configure(ls)
    kept = player(session, "Kept")
    dropped = player(session, "Dropped")
    for who in (kept, dropped):
        eligible(session, who, ANY)
        snapshot(session, who, pro_team_id=10, on_team_id=HOME)
    games(session, 10, [1])
    held(session, home, first, kept, 1)
    held(session, home, first, dropped, 1)
    held(session, home, first, kept, 2)
    held(session, home, first, dropped, 2, slot="FA")

    week = load_team_week(session, ls, HOME, today=3)

    assert [p.name for p in week.roster] == ["Kept"], "day 2 is the latest day, and FA is gone"


def test_faab_is_the_in_season_pot_less_the_bids_that_won(session: Session) -> None:
    ls, (home, away), _ = league_season(session)
    configure(ls)
    games(session, 10, [1])
    someone = player(session, "Someone")
    winning_bid(session, home, 2, 12, someone)
    winning_bid(session, away, 2, 40, someone)

    week = load_team_week(session, ls, HOME, today=3)

    assert ls.acquisition_budget == 100 and ls.auction_budget == 200
    assert week.faab_remaining == 88, "the other team's bid is theirs"


def test_the_add_budget_is_one_for_each_day_of_the_matchup_period(session: Session) -> None:
    """The league's rule: one add per day of the period, spent on any of its
    days. The adds already made are this team's executed ones inside the
    period `today` falls in, and no others."""
    ls, (home, away), _ = league_season(session, days_per_period=7)
    configure(ls)
    games(session, 10, [1])
    someone = player(session, "Someone")
    winning_bid(session, home, 1, 0, someone)
    winning_bid(session, home, 3, 0, someone)
    winning_bid(session, home, 3, 0, player(session, "Someone Else"))
    winning_bid(session, home, 9, 0, someone)  # the next period's, not this one's
    winning_bid(session, away, 2, 0, someone)  # another team's

    week = load_team_week(session, ls, HOME, today=4)

    assert week.adds_budget == 7, "seven days, seven adds"
    assert week.adds_used == 3, "two of them on one day, which the league allows"
    assert week.adds_left == 4
    assert load_team_week(session, ls, HOME, today=9).adds_used == 1, "the next period is its own"
    assert load_team_week(session, ls, AWAY, today=4).adds_used == 1


def test_a_short_period_has_a_short_add_budget(session: Session) -> None:
    """The opening week of this league is six days, so it allows six adds."""
    ls, _, _ = league_season(session, days_per_period=6)
    configure(ls)
    games(session, 10, [1])

    week = load_team_week(session, ls, HOME, today=2)

    assert week.scoring_periods_remaining == (2, 3, 4, 5, 6)
    assert week.adds_budget == 6, "the budget is the period's days, not the days left"
    assert week.adds_used == 0 and week.adds_left == 6


def test_a_free_agent_on_waivers_carries_the_period_he_clears_in(session: Session) -> None:
    """Straight from the latest snapshot, mapped through the season calendar;
    a pool named by id is taken as men who are free agents now."""
    ls, _, _ = league_season(session)
    configure(ls)
    waiting = player(session, "On Waivers")
    free = player(session, "Free")
    for who in (waiting, free):
        eligible(session, who, GUARD)
        snapshot(session, who, pro_team_id=10, on_team_id=0)
    games(session, 10, [1, 2, 3, 4])
    on_the_wire(session, ls, free)
    on_the_wire(session, ls, waiting, status="WAIVERS", clears_at=clears_waivers_on(4))
    week = load_team_week(session, ls, HOME, today=2)

    wire = {found.name: found for found in load_free_agents(session, ls, week)}

    assert wire["Free"].waiver_clears_on is None
    assert wire["Free"].seatable_on(2) is True
    assert wire["On Waivers"].waiver_clears_at == day_date(4)
    assert wire["On Waivers"].waiver_clears_on == 4
    assert [wire["On Waivers"].seatable_on(day) for day in (2, 3, 4, 5)] == [
        False,
        False,
        True,
        True,
    ]
    named = load_free_agents(session, ls, week, player_ids=[waiting.id])
    assert named[0].waiver_clears_on is None, "a named pool is taken as given"


def test_a_player_the_listener_never_saw_takes_his_team_from_his_roster_row(
    session: Session,
) -> None:
    ls, (home, away), (first, _) = league_season(session)
    configure(ls)
    unseen = player(session, "Unseen")
    eligible(session, unseen, GUARD)
    row = matchup(session, first, home, away)
    session.add(RosterSlot(matchup_id=row.id, team_id=home.id, player_id=unseen.id, pro_team="BOS"))
    session.flush()
    held(session, home, first, unseen, 1)
    games(session, 2, [2, 3])  # BOS is ESPN's team 2

    week = load_team_week(session, ls, HOME, today=2)

    assert week.roster[0].pro_team_id == 2
    assert week.roster[0].game_days == (2, 3)
    assert week.roster[0].injury_status is None


def test_the_wire_is_the_latest_pass_only(session: Session) -> None:
    ls, _, _ = league_season(session)
    configure(ls)
    claimed = player(session, "Claimed Since")
    still = player(session, "Still There")
    for who in (claimed, still):
        eligible(session, who, GUARD)
        snapshot(session, who, pro_team_id=10, on_team_id=0)
    games(session, 10, [2, 3])
    later = OBSERVED + timedelta(hours=7)
    on_the_wire(session, ls, claimed, observed_at=OBSERVED)
    on_the_wire(session, ls, still, observed_at=OBSERVED)
    on_the_wire(session, ls, still, observed_at=later)
    week = load_team_week(session, ls, HOME, today=2)

    wire = load_free_agents(session, ls, week)

    assert [p.name for p in wire] == ["Still There"]
    assert wire[0].game_days == (2, 3)
    assert wire[0].on_ir is False
    named = load_free_agents(session, ls, week, player_ids=[claimed.id])
    assert [p.name for p in named] == ["Claimed Since"], "a named pool is taken as given"


def test_a_day_outside_every_period_is_refused(session: Session) -> None:
    ls, _, _ = league_season(session, periods=1, days_per_period=7)
    configure(ls)

    with pytest.raises(ValueError, match="no matchup period"):
        load_team_week(session, ls, HOME, today=8)


def test_the_calendar_dates_every_scoring_period_from_opening_night(session: Session) -> None:
    games(session, 10, [1, 3])
    games(session, 11, [9])

    calendar = season_calendar(session, SEASON)

    assert calendar is not None
    assert calendar.date_of(1) == OPENING
    assert calendar.date_of(5) == OPENING + timedelta(days=4), "a day with no game still has a date"
    assert calendar.scoring_period_on(OPENING + timedelta(days=3)) == 4
    assert calendar.scoring_period_on(OPENING - timedelta(days=30)) == 1, "preseason is day one"
    assert calendar.scoring_period_on(OPENING + timedelta(days=300)) == 9, "and after, the last"
    assert season_calendar(session, SEASON + 1) is None
