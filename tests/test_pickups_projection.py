"""The per-game line from today, the minutes tilt, and the games it is spread over.

The rate itself is `app.scoring.knowable`'s and is pinned by its own tests;
what is pinned here is what this module adds: nothing when there is nothing
to add, a tilt only while the event is live and only within its caps, and
the availability discount on a season line and not on a week's.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.listener.events import MINUTES_DROP, MINUTES_SPIKE
from app.pickups.projection import (
    ESPN_AVAILABILITY,
    TILT_CEILING,
    TILT_FLOOR,
    minutes_tilt,
    per_game_line,
    rest_of_period_line,
    rest_of_season_line,
)
from app.scoring.knowable import RECENT_WEIGHT
from tests.pickups_db import SEASON, clear_schedule, minutes_event, played, projected
from tests.scoring_db import player

#: A projection of twenty points on ten shots a game, over seventy games.
RATE = {"PTS": 20.0, "FGM": 8.0, "FGA": 16.0, "FTM": 4.0, "FTA": 5.0, "REB": 5.0, "TO": 2.0}


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


def test_with_no_games_played_the_line_is_the_projection_over_the_games_given(
    session: Session,
) -> None:
    who = player(session, "Projected Only")
    projected(session, who, 70, RATE)

    line = rest_of_period_line(session, SEASON, who.id, today=1, games=3)

    assert line.games == 3
    assert line.get("PTS") == pytest.approx(60.0)
    assert line.get("FGA") == pytest.approx(48.0)
    assert line.totals(["FG%"])["FG%"] == pytest.approx(0.5)
    assert rest_of_period_line(session, SEASON, who.id, today=1, games=0).get("PTS") == 0.0


def test_a_player_with_nothing_on_record_has_an_empty_line(session: Session) -> None:
    who = player(session, "Unknown")

    line = rest_of_period_line(session, SEASON, who.id, today=1, games=3)

    assert all(value == 0.0 for value in line.counts.values())


def test_a_rest_of_season_line_is_discounted_for_availability_and_a_weeks_is_not(
    session: Session,
) -> None:
    who = player(session, "Healthy")
    projected(session, who, 70, RATE)

    season = rest_of_season_line(session, SEASON, who.id, today=1, games=50)
    week = rest_of_period_line(session, SEASON, who.id, today=1, games=3)

    assert season.get("PTS") == pytest.approx(20.0 * 50 * ESPN_AVAILABILITY)
    assert season.games == round(50 * ESPN_AVAILABILITY)
    assert week.get("PTS") == pytest.approx(60.0), "the week's schedule is known"


def test_a_live_minutes_spike_scales_every_count_and_can_be_switched_off(
    session: Session,
) -> None:
    who = player(session, "Promoted")
    projected(session, who, 70, RATE)
    # Twenty minutes a night for a fortnight, on the projected rate.
    for day in range(1, 6):
        played(session, who, day, 20.0, RATE)
    minutes_event(session, who, MINUTES_SPIKE, through=5, recent_mean=26.0, prior_mean=20.0)

    plain = per_game_line(session, SEASON, who.id, today=6, tilt=False)
    tilted = per_game_line(session, SEASON, who.id, today=6)

    tilt = minutes_tilt(session, SEASON, who.id, today=6)
    assert tilt is not None
    assert tilt.kind == MINUTES_SPIKE
    assert tilt.factor == pytest.approx(1.3), "26 minutes over a 20-minute season"
    assert plain.get("PTS") == pytest.approx(20.0)
    assert tilted.get("PTS") == pytest.approx(26.0)
    assert tilted.get("FGA") == pytest.approx(16.0 * 1.3), "shots scale too"
    assert tilted.totals(["FG%"])["FG%"] == pytest.approx(plain.totals(["FG%"])["FG%"])
    assert tilted.get("TO") == pytest.approx(2.0 * 1.3), "so do the turnovers"


def test_the_tilt_is_capped_both_ways(session: Session) -> None:
    riser = player(session, "Riser")
    faller = player(session, "Faller")
    for who in (riser, faller):
        projected(session, who, 70, RATE)
        for day in range(1, 4):
            played(session, who, day, 20.0, RATE)
    minutes_event(session, riser, MINUTES_SPIKE, through=3, recent_mean=38.0, prior_mean=20.0)
    minutes_event(session, faller, MINUTES_DROP, through=3, recent_mean=6.0, prior_mean=20.0)

    up = minutes_tilt(session, SEASON, riser.id, today=4)
    down = minutes_tilt(session, SEASON, faller.id, today=4)

    assert up is not None and down is not None
    assert up.raw == pytest.approx(1.9) and up.factor == TILT_CEILING
    assert down.raw == pytest.approx(0.3) and down.factor == TILT_FLOOR


def test_a_stale_minutes_event_does_not_tilt(session: Session) -> None:
    who = player(session, "Old News")
    projected(session, who, 70, RATE)
    for day in range(1, 4):
        played(session, who, day, 20.0, RATE)
    minutes_event(session, who, MINUTES_SPIKE, through=3, recent_mean=30.0, prior_mean=20.0)

    assert minutes_tilt(session, SEASON, who.id, today=13) is not None, "ten days: still live"
    assert minutes_tilt(session, SEASON, who.id, today=14) is None, "eleven: not"
    assert per_game_line(session, SEASON, who.id, today=14).get("PTS") == pytest.approx(20.0)


def test_without_played_minutes_the_tilt_falls_back_to_the_events_own_prior(
    session: Session,
) -> None:
    who = player(session, "Unplayed Here")
    projected(session, who, 70, RATE)
    minutes_event(session, who, MINUTES_SPIKE, through=3, recent_mean=30.0, prior_mean=24.0)

    tilt = minutes_tilt(session, SEASON, who.id, today=4)

    assert tilt is not None
    assert tilt.factor == pytest.approx(30.0 / 24.0)


def test_the_rate_is_the_knowable_line_not_the_projection_alone(session: Session) -> None:
    """Season to date pulls on the projection: a player scoring thirty a night
    for five games is projected above his twenty, and below thirty."""
    who = player(session, "Hot Start")
    projected(session, who, 70, RATE)
    for day in range(1, 6):
        played(session, who, day, 30.0, {**RATE, "PTS": 30.0})

    line = per_game_line(session, SEASON, who.id, today=6)

    assert 20.0 < line.get("PTS") < 30.0
    # Five of fifteen-plus-five toward thirty, then fifteen percent of thirty on top.
    base = 20.0 + 10.0 * (5 / 20)
    assert line.get("PTS") == pytest.approx((1 - RECENT_WEIGHT) * base + RECENT_WEIGHT * 30.0)
