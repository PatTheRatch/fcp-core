"""The OUT-man rule: the prior, the ramp, and where the two reach the engine.

The rule itself is declared in `app/pickups/returns.py` and in
`docs/stash_mode.md`. These are its consequences, stated as the cases the
brief asked to be able to check: a man ruled out with no date is counted for
a fraction of his games rather than none; the fraction falls the longer he
has been out; the ramp discounts his first two weeks back; ESPN's date wins
when there is one; Doubtful and Questionable are not touched at all; and a
roster with nobody out reads exactly as it read before any of this existed.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Player
from app.pickups import returns
from app.pickups.returns import (
    MAX_HORIZON,
    PRIOR_DAYS_OUT,
    PRIOR_HORIZONS,
    RAMP_FIRST_WEEK,
    RAMP_SECOND_WEEK,
    RETURN_PRIOR,
    expected_dead_days,
    expected_games,
    expected_games_from_date,
    odds_back_by_week,
    probability_back_within,
    ramp,
    return_density,
)
from app.pickups.state import build_players
from tests.pickups_db import (
    ANY,
    SEASON,
    clear_schedule,
    configure,
    day_date,
    eligible,
    games,
    played,
    snapshot,
)
from tests.scoring_db import league_season, player

HOME = 1

#: Every man's NBA team plays every other day of a twenty-eight day window,
#: so a game count is a clean fourteen and a fraction of it is readable.
EVERY_OTHER_DAY = list(range(2, 29, 2))


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    returns.return_density.cache_clear()
    yield scoring_session


# ---------------------------------------------------------------------------
# the table, read on its own
# ---------------------------------------------------------------------------


def test_the_prior_reads_its_own_cells_exactly() -> None:
    for days_out in PRIOR_DAYS_OUT:
        for horizon in PRIOR_HORIZONS:
            assert probability_back_within(days_out, horizon) == pytest.approx(
                RETURN_PRIOR[days_out][horizon]
            )


def test_he_is_out_this_morning_so_no_days_ahead_is_no_chance() -> None:
    assert probability_back_within(7, 0) == 0.0
    assert probability_back_within(7, -3) == 0.0


def test_the_last_row_is_held_and_the_last_column_is_chained() -> None:
    # Out longer than the table's last row keeps that row.
    assert probability_back_within(60, 14) == probability_back_within(28, 14)
    assert probability_back_within(200, 7) == probability_back_within(28, 7)
    # Asked past the last column, the table is read again from the row he
    # would then be on, so the curve goes on rising instead of flat-lining.
    assert probability_back_within(28, 56) > probability_back_within(28, MAX_HORIZON)
    still_out = 1.0 - probability_back_within(28, 56)
    assert still_out == pytest.approx((1.0 - probability_back_within(28, 28)) ** 2, abs=0.005), (
        "the chain is the same row twice, which brackets the measured 26% at 29+ days"
    )


def test_the_curve_never_goes_backwards() -> None:
    for days_out in (1, 5, 14, 28, 90):
        previous = 0.0
        for ahead in range(0, 120):
            now = probability_back_within(days_out, ahead)
            assert now >= previous - 1e-12
            previous = now
        density = return_density(days_out, 60)
        assert min(density) >= 0.0
        assert sum(density) == pytest.approx(probability_back_within(days_out, 60))


def test_being_out_longer_is_worse_news() -> None:
    fortnights = [odds_back_by_week(days_out)[2] for days_out in (3, 7, 14, 28)]
    assert fortnights == sorted(fortnights, reverse=True)


def test_the_ramp_is_two_weeks_and_then_nothing() -> None:
    assert ramp(0) == RAMP_FIRST_WEEK
    assert ramp(6) == RAMP_FIRST_WEEK
    assert ramp(7) == RAMP_SECOND_WEEK
    assert ramp(13) == RAMP_SECOND_WEEK
    assert ramp(14) == 1.0
    assert ramp(-2) == RAMP_FIRST_WEEK, "a game before an ESPN date reads as his first day"


# ---------------------------------------------------------------------------
# the counts the engine takes from it
# ---------------------------------------------------------------------------


def test_expected_games_are_a_fraction_of_the_whole_ones() -> None:
    offsets = list(range(1, 29))
    counted = expected_games(offsets, days_out=14)
    assert 0.0 < counted < len(offsets)


def test_a_longer_absence_buys_fewer_games_and_more_dead_days() -> None:
    offsets = list(range(1, 57))
    counted = [expected_games(offsets, days_out=days_out) for days_out in (3, 7, 14, 28)]
    assert counted == sorted(counted, reverse=True)
    dead = [expected_dead_days(56, days_out=days_out) for days_out in (3, 7, 14, 28)]
    assert dead == sorted(dead)


def test_the_ramp_shows_as_a_discount_on_the_weeks_just_after_a_return() -> None:
    # One game, far enough out that he is almost certainly back, and the only
    # question is how long he has been back by then.
    far = expected_games([90], days_out=7)
    near = expected_games([8], days_out=7)
    assert near < far
    # And a game on the day the prior is most sure of a very recent return is
    # worth no more than the first week's factor.
    assert expected_games([1], days_out=3) <= RAMP_FIRST_WEEK


def test_a_date_wins_and_the_ramp_still_applies_after_it() -> None:
    # Four games: one before the date, then his first, eighth and twentieth
    # day back.
    assert expected_games_from_date([-3, 0, 8, 20]) == pytest.approx(
        RAMP_FIRST_WEEK + RAMP_SECOND_WEEK + 1.0
    )


def test_today_is_never_counted() -> None:
    assert expected_games([0], days_out=1) == 0.0


# ---------------------------------------------------------------------------
# where it reaches a player
# ---------------------------------------------------------------------------


def _man(
    session: Session,
    ls: LeagueSeason,
    name: str,
    *,
    injury_status: str,
    last_played: int | None = None,
    returns_on: int | None = None,
    pro_team: int = 10,
) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(
        session,
        who,
        pro_team_id=pro_team,
        on_team_id=HOME,
        injury_status=injury_status,
        expected_return_date=day_date(returns_on) if returns_on is not None else None,
    )
    if last_played is not None:
        played(session, who, last_played, 30.0, {"PTS": 20.0}, season=SEASON)
    games(session, pro_team, EVERY_OTHER_DAY)
    return who


def _built(session: Session, ls: LeagueSeason, who: Player, today: int = 1):  # type: ignore[no-untyped-def]
    return build_players(session, ls, [who.id], tuple(range(today, 29)), today=today)[0]


def test_a_fit_man_counts_every_game_and_nothing_changes_for_him(session: Session) -> None:
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    who = _man(session, ls, "Fit", injury_status="ACTIVE")

    man = _built(session, ls, who)

    assert man.season_games == float(man.games_remaining_this_period)
    assert man.season_games == len(EVERY_OTHER_DAY)
    assert man.days_out is None


@pytest.mark.parametrize("status", ["DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL"])
def test_doubtful_and_questionable_are_not_touched(session: Session, status: str) -> None:
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    who = _man(session, ls, "Sore", injury_status=status)

    man = _built(session, ls, who)

    assert man.season_games == float(man.games_remaining_this_period)
    assert man.days_out is None


def test_an_out_man_with_no_date_carries_a_fraction_rather_than_nothing(
    session: Session,
) -> None:
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    # He played on the season's opening day and has not played since, so on
    # the morning of day 15 he is fourteen days out.
    who = _man(session, ls, "Hurt", injury_status="OUT", last_played=1)

    man = _built(session, ls, who, today=15)

    assert man.days_out == 14
    assert man.game_days == (), "the week's own seating still counts him for nothing"
    assert 0.0 < man.season_games < len(man.schedule_days)


def test_the_fraction_falls_as_the_absence_runs_on(session: Session) -> None:
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    soon = _man(session, ls, "Just Out", injury_status="OUT", last_played=13, pro_team=10)
    long = _man(session, ls, "Long Out", injury_status="OUT", last_played=1, pro_team=11)

    fresh = _built(session, ls, soon, today=15)
    stale = _built(session, ls, long, today=15)

    assert fresh.days_out == 2
    assert stale.days_out == 14
    assert fresh.season_games > stale.season_games


def test_an_espn_date_wins_over_the_prior(session: Session) -> None:
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    # Back on day 21: of the fourteen game days from day 15 on, the seven
    # from day 22 count, the first four of them at the ramp.
    who = _man(session, ls, "Dated", injury_status="OUT", last_played=1, returns_on=21)

    man = _built(session, ls, who, today=15)

    after = [day for day in man.schedule_days if day >= 21]
    assert man.season_games == pytest.approx(expected_games_from_date(day - 21 for day in after))
    assert man.season_games < len(after), "the ramp discounts his first fortnight back"


def test_a_man_with_no_stored_game_at_all_reads_as_one_day_out(session: Session) -> None:
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    who = _man(session, ls, "Unknown", injury_status="OUT")

    man = _built(session, ls, who, today=15)

    assert man.days_out is None, "nothing was measured, so nothing is claimed"
    assert man.season_games == pytest.approx(
        expected_games((day - 15 for day in man.schedule_days), days_out=1)
    )


def test_the_window_is_counted_from_today_and_not_from_its_first_day(
    session: Session,
) -> None:
    """A trade's playoff weeks start in March; the absence started in January."""
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    who = _man(session, ls, "Hurt", injury_status="OUT", last_played=1)

    window = tuple(range(21, 29))
    from_today = build_players(session, ls, [who.id], window, today=15)[0]
    from_the_window = build_players(session, ls, [who.id], window, today=21)[0]

    assert from_today.days_out == 14
    assert from_the_window.days_out == 20
    assert from_today.season_games > from_the_window.season_games


def test_the_prior_cannot_touch_a_roster_with_nobody_out(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard the brief asked for, stated as a property rather than a file.

    A report on a roster with no ruled-out man must read the same whatever the
    return prior says, because the prior is never consulted for a man who is
    not out. Swapping the whole table for a wrong one and getting the same
    answer is the strongest form of that.
    """
    ls, _teams, _periods = league_season(session, days_per_period=7, periods=4)
    configure(ls)
    fit = _man(session, ls, "Fit", injury_status="ACTIVE")
    sore = _man(session, ls, "Sore", injury_status="DAY_TO_DAY", pro_team=11)
    ids = [fit.id, sore.id]
    days = tuple(range(1, 29))

    before = build_players(session, ls, ids, days)

    wrong: Mapping[int, Mapping[int, float]] = {
        days_out: dict.fromkeys(PRIOR_HORIZONS, 0.0) for days_out in PRIOR_DAYS_OUT
    }
    monkeypatch.setattr(returns, "RETURN_PRIOR", wrong)
    returns.return_density.cache_clear()
    after = build_players(session, ls, ids, days)

    assert after == before
