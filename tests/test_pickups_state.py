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

from app.db.models import Matchup, PlayerGameStat, RosterSlot
from app.pickups.state import (
    box_scores,
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
    played,
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


def test_on_a_live_morning_the_posted_totals_are_espns_own_row(session: Session) -> None:
    """Nothing recorded on today or later means the season has not passed us.

    ESPN is still writing that row, so it is the running tally and not a
    finished week -- and it carries the stat corrections our own box scores
    may not have, so it wins over a sum of them.
    """
    ls, (home, away), (first, _) = league_season(session)
    configure(ls)
    games(session, 10, [1])
    matchup(session, first, home, away, {home: {"PTS": 150}, away: {"PTS": 190}})
    held(session, home, first, player(session, "Monday"), 1, stats={"PTS": 10})
    held(session, home, first, player(session, "Tuesday"), 2, stats={"PTS": 20})

    week = load_team_week(session, ls, HOME, today=3)

    assert week.my_totals.get("PTS") == 150, "ESPN's number, not our 30"


def test_on_a_replayed_day_the_posted_totals_stop_the_night_before(session: Session) -> None:
    """One box score on or after today and ESPN's row can no longer be trusted.

    It has no day column, so for a period the season has run past it is the
    finished week -- which is how a report for the morning of the first day
    read 709 points already banked. Summed from the started lines instead,
    and summed over the days *before* today: `scoring_periods_remaining`
    begins at today, so the projection adds that day itself and counting it
    here as well would count it twice.
    """
    ls, (home, away), (first, _) = league_season(session)
    configure(ls)
    games(session, 10, [1])
    matchup(session, first, home, away, {home: {"PTS": 150}, away: {"PTS": 190}})
    held(session, home, first, player(session, "Monday"), 1, stats={"PTS": 10})
    held(session, home, first, player(session, "Tuesday"), 2, stats={"PTS": 20})
    held(session, home, first, player(session, "Wednesday"), 3, stats={"PTS": 40})
    held(session, home, first, player(session, "Thursday"), 4, stats={"PTS": 80})
    # A game in the next matchup period: the future this database has, and
    # the only thing that says today is a replay rather than now.
    played(session, player(session, "Next Week"), 9, 30.0, {"PTS": 1000})

    week = load_team_week(session, ls, HOME, today=3)

    assert week.my_totals.get("PTS") == 30, "days 1 and 2; day 3 is still to be played"
    assert week.opp_totals.get("PTS") == 0.0, "the opponent has posted nothing either"
    whole_period = load_team_week(session, ls, HOME, today=5)
    assert whole_period.my_totals.get("PTS") == 150, "over a whole period, ESPN's own total"


def test_the_men_behind_a_replayed_score_add_up_to_it(session: Session) -> None:
    """The table a page draws under the score is the score's own arithmetic.

    Not a second query over the same rows but the very sum `_posted`
    returns, so the two cannot drift: a page that showed a Total row
    disagreeing with the figure above it would be worse than showing no
    table at all. The men stop at the same boundary the score does.
    """
    ls, (home, away), (first, _) = league_season(session)
    configure(ls)
    games(session, 10, [1])
    matchup(session, first, home, away, {home: {"PTS": 150}, away: {"PTS": 190}})
    monday = player(session, "Monday")
    tuesday = player(session, "Tuesday")
    held(session, home, first, monday, 1, stats={"PTS": 10, "REB": 4, "FGM": 4, "FGA": 9})
    held(session, home, first, monday, 2, stats={"PTS": 12, "REB": 6, "FGM": 5, "FGA": 8})
    held(session, home, first, tuesday, 2, stats={"PTS": 20, "REB": 1, "FGM": 8, "FGA": 8})
    held(session, home, first, player(session, "Today"), 3, stats={"PTS": 99})
    played(session, player(session, "Next Week"), 9, 30.0, {"PTS": 1000})

    week = load_team_week(session, ls, HOME, today=3)

    assert week.posted_source == "box_scores"
    names = [man.name for man in week.my_posted_men]
    assert names == ["Monday", "Tuesday"], "best first by games, then points; today is not in it"
    for abbreviation in ("PTS", "REB", "FGM", "FGA"):
        summed = sum(man.line.get(abbreviation) for man in week.my_posted_men)
        assert summed == week.my_totals.get(abbreviation), abbreviation
    assert sum(man.line.games for man in week.my_posted_men) == week.my_totals.games
    assert [man.games for man in week.my_posted_men] == [2, 1]


def test_on_a_live_morning_the_men_are_the_box_scores_and_the_score_is_espns(
    session: Session,
) -> None:
    """The one case where the table can fall short of the score above it.

    ESPN writes its running row as the games go; our box scores arrive with
    the nightly ingest. So on a live morning the two sources disagree by
    whatever has been played and not yet stored, and `posted_source` is what
    lets a page say so rather than print a Total row that looks wrong.
    """
    ls, (home, away), (first, _) = league_season(session)
    configure(ls)
    games(session, 10, [1])
    matchup(session, first, home, away, {home: {"PTS": 150}, away: {"PTS": 190}})
    held(session, home, first, player(session, "Monday"), 1, stats={"PTS": 10})

    week = load_team_week(session, ls, HOME, today=3)

    assert week.posted_source == "espn"
    assert week.my_totals.get("PTS") == 150
    assert [man.line.get("PTS") for man in week.my_posted_men] == [10.0]


def test_a_mans_stored_line_is_read_for_one_day_and_only_when_he_played(
    session: Session,
) -> None:
    """`box_scores` is display only: a day ESPN lists with no line is absent.

    Which is what lets a page show the game mark alone until the ingest has
    reached him, rather than a row of zeros that reads as a night off.
    """
    ls, _, _ = league_season(session)
    configure(ls)
    who = player(session, "Played")
    quiet = player(session, "Listed Only")
    played(session, who, 2, 31.0, {"PTS": 22, "REB": 8, "FGM": 8, "FGA": 15})
    session.add(
        PlayerGameStat(
            player_id=quiet.id, season=SEASON, scoring_period=2, played=False, raw_totals={}
        )
    )
    session.flush()

    lines = box_scores(session, SEASON, [who.id, quiet.id], 2)

    assert set(lines) == {who.id}
    assert lines[who.id].minutes == 31.0
    assert lines[who.id].line.get("PTS") == 22.0
    assert box_scores(session, SEASON, [who.id], 3) == {}, "one day, never a window"


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


def test_the_adds_spent_stop_at_today_exactly_as_the_faab_does(session: Session) -> None:
    """An add and the money it cost are one transaction, so one bound serves both.

    Counted over the period's whole span instead, a report for the morning of
    day 2 charges the team every claim it went on to make that week -- and a
    team the report believes has spent its budget is told there is nothing to
    plan today, so the recommendation disappears entirely. That is what it did
    on thirteen of the thirty-nine team-days the in-season rehearsal replayed.
    """
    ls, (home, _), _ = league_season(session, days_per_period=7)
    configure(ls)
    games(session, 10, [1])
    winning_bid(session, home, 1, 3, player(session, "Monday"))
    for day in range(3, 8):
        winning_bid(session, home, day, 1, player(session, f"Day {day}"))
    winning_bid(session, home, 7, 1, player(session, "Saturday as well"))

    week = load_team_week(session, ls, HOME, today=2)

    assert week.adds_budget == 7
    assert week.adds_used == 1, "only Monday's claim had been made by the morning of day 2"
    assert week.adds_left == 6, "a team with adds left gets a plan"
    assert week.faab_remaining == 97, "and the same day bounds the money"
    whole_week = load_team_week(session, ls, HOME, today=7)
    assert whole_week.adds_used == 7 and whole_week.adds_left == 0
    assert whole_week.faab_remaining == 91


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
