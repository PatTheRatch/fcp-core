"""Event rules: each fires on its own change and on nothing else.

Every case builds two observations by hand, so the rules are pinned
without ESPN or a database. The kinds are the table in docs/pickups.md
section 3.5.
"""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.listener import events
from app.listener.events import Observation, diff, minutes_events
from app.listener.pool import PoolEntry

AT = datetime(2026, 11, 3, 22, 30, tzinfo=UTC)
NEXT = datetime(2026, 11, 4, 0, 30, tzinfo=UTC)


def _kinds(before: Observation, after: Observation, **kwargs: datetime | None) -> list[str]:
    return [e.kind for e in diff(before, after, observed_at=AT, next_pass_at=NEXT, **kwargs)]


ACTIVE = Observation(injury_status="ACTIVE", status="ONTEAM", on_team_id=3, pro_team_id=13)


def test_a_first_observation_produces_nothing() -> None:
    assert diff(None, ACTIVE, observed_at=AT) == []


def test_no_change_produces_nothing() -> None:
    assert _kinds(ACTIVE, ACTIVE) == []


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        ("ACTIVE", "OUT", events.WENT_OUT),
        ("QUESTIONABLE", "OUT", events.WENT_OUT),
        ("ACTIVE", "SUSPENSION", events.WENT_OUT),
        ("ACTIVE", "QUESTIONABLE", events.DOWNGRADED),
        ("PROBABLE", "DAY_TO_DAY", events.DOWNGRADED),
        ("ACTIVE", "DOUBTFUL", events.DOWNGRADED),
        ("OUT", "DOUBTFUL", events.UPGRADED),
        ("OUT", "QUESTIONABLE", events.UPGRADED),
        ("DOUBTFUL", "PROBABLE", events.UPGRADED),
        ("QUESTIONABLE", "PROBABLE", events.UPGRADED),
        ("OUT", "ACTIVE", events.RETURNED),
        ("QUESTIONABLE", "ACTIVE", events.RETURNED),
        ("PROBABLE", "ACTIVE", events.RETURNED),
    ],
)
def test_each_injury_transition_fires_exactly_its_kind(before: str, after: str, kind: str) -> None:
    kinds = _kinds(Observation(injury_status=before), Observation(injury_status=after))
    assert kinds == [kind]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("OUT", "SUSPENSION"),  # both are out
        ("QUESTIONABLE", "DAY_TO_DAY"),  # both are maybe
        ("DAY_TO_DAY", "QUESTIONABLE"),
        (None, "ACTIVE"),  # nothing known before
        ("ACTIVE", None),
    ],
)
def test_sideways_moves_are_not_events(before: str | None, after: str | None) -> None:
    assert _kinds(Observation(injury_status=before), Observation(injury_status=after)) == []


def test_went_out_carries_the_return_date() -> None:
    before = Observation(injury_status="ACTIVE")
    after = Observation(injury_status="OUT", expected_return_date=date(2026, 12, 1))
    [event] = diff(before, after, observed_at=AT)
    assert event.kind == events.WENT_OUT
    assert event.previous == {"injury_status": "ACTIVE", "expected_return_date": None}
    assert event.current == {"injury_status": "OUT", "expected_return_date": "2026-12-01"}


def test_a_moved_return_date_reports_the_delta_in_days() -> None:
    before = Observation(injury_status="OUT", expected_return_date=date(2026, 12, 1))
    after = Observation(injury_status="OUT", expected_return_date=date(2026, 12, 15))
    [event] = diff(before, after, observed_at=AT)
    assert event.kind == events.RETURN_DATE_CHANGED
    assert event.detail == {"days": 14}

    [sooner] = diff(after, before, observed_at=AT)
    assert sooner.detail == {"days": -14}


def test_a_return_date_appearing_or_vanishing_alone_is_not_a_change() -> None:
    without = Observation(injury_status="OUT")
    with_date = Observation(injury_status="OUT", expected_return_date=date(2026, 12, 1))
    assert _kinds(without, with_date) == []
    assert _kinds(with_date, without) == []


def test_a_trade_between_nba_teams_is_an_event_but_leaving_the_league_is_not() -> None:
    assert _kinds(Observation(pro_team_id=13), Observation(pro_team_id=25)) == [
        events.CHANGED_PRO_TEAM
    ]
    assert _kinds(Observation(pro_team_id=13), Observation(pro_team_id=0)) == []
    assert _kinds(Observation(pro_team_id=None), Observation(pro_team_id=13)) == []


def test_dropped_names_the_team_that_let_him_go() -> None:
    before = Observation(status="ONTEAM", on_team_id=7)
    after = Observation(status="WAIVERS", on_team_id=0)
    [event] = diff(before, after, observed_at=AT)
    assert event.kind == events.DROPPED
    assert event.detail == {"from_team_id": 7}


def test_claimed_names_the_team_that_took_him() -> None:
    before = Observation(status="FREEAGENT", on_team_id=0)
    after = Observation(status="ONTEAM", on_team_id=3)
    [event] = diff(before, after, observed_at=AT)
    assert event.kind == events.CLAIMED
    assert event.detail == {"to_team_id": 3}


def test_a_waiver_clearing_before_the_next_pass_is_reported_now() -> None:
    clears = datetime(2026, 11, 4, 0, 0, tzinfo=UTC)
    on_waivers = Observation(status="WAIVERS", waiver_clears_at=clears)
    assert _kinds(on_waivers, on_waivers) == [events.WAIVER_CLEARING]

    later = Observation(status="WAIVERS", waiver_clears_at=NEXT.replace(hour=8))
    assert _kinds(on_waivers, later) == [], "clears after the next pass, which will report it"
    assert diff(on_waivers, on_waivers, observed_at=AT, next_pass_at=None) == [], (
        "with no next pass known there is nothing to compare against"
    )


def test_a_dropped_player_already_clearing_produces_both_events() -> None:
    before = Observation(status="ONTEAM", on_team_id=7)
    after = Observation(
        status="WAIVERS", on_team_id=0, waiver_clears_at=datetime(2026, 11, 4, 0, tzinfo=UTC)
    )
    assert _kinds(before, after) == [events.DROPPED, events.WAIVER_CLEARING]


def test_an_ownership_surge_fires_once_as_the_move_crosses_the_line() -> None:
    quiet = Observation(percent_owned=10.0, percent_change=1.0)
    surging = Observation(percent_owned=14.0, percent_change=6.0)
    still_surging = Observation(percent_owned=19.0, percent_change=7.5)
    assert _kinds(quiet, surging) == [events.OWNERSHIP_SURGE]
    assert _kinds(surging, still_surging) == [], "already reported"


def test_crossing_a_quarter_owned_is_a_surge_on_its_own() -> None:
    before = Observation(percent_owned=23.0, percent_change=2.0)
    after = Observation(percent_owned=26.0, percent_change=3.0)
    [event] = diff(before, after, observed_at=AT)
    assert event.kind == events.OWNERSHIP_SURGE
    assert event.detail == {"crossed_line": True}
    assert _kinds(after, Observation(percent_owned=30.0, percent_change=4.0)) == []


def test_an_ownership_slide_fires_once() -> None:
    held = Observation(percent_owned=60.0, percent_change=-1.0)
    sliding = Observation(percent_owned=52.0, percent_change=-8.0)
    assert _kinds(held, sliding) == [events.OWNERSHIP_SLIDE]
    assert _kinds(sliding, Observation(percent_owned=45.0, percent_change=-7.0)) == []


def test_a_minutes_spike_from_a_played_series() -> None:
    prior = [(day, 18.0) for day in range(1, 11)]
    recent = [(11, 30.0), (12, 27.0), (13, 31.0)]
    [event] = minutes_events(prior + recent)
    assert event.kind == events.MINUTES_SPIKE
    assert event.detail == {
        "recent_mean": 29.3,
        "prior_mean": 18.0,
        "recent_games": 3,
        "prior_games": 10,
        "through_scoring_period": 13,
    }


def test_a_minutes_drop_is_the_mirror() -> None:
    [event] = minutes_events([(d, 32.0) for d in range(1, 8)] + [(8, 20.0), (9, 22.0), (10, 24.0)])
    assert event.kind == events.MINUTES_DROP
    assert event.detail["prior_games"] == 7


def test_minutes_within_the_band_are_no_event() -> None:
    assert (
        minutes_events([(d, 20.0) for d in range(1, 8)] + [(8, 27.0), (9, 26.0), (10, 27.0)]) == []
    )


def test_too_few_games_is_a_refusal_not_a_zero() -> None:
    assert minutes_events([(1, 10.0), (2, 10.0), (3, 30.0), (4, 30.0), (5, 30.0)]) == []
    assert minutes_events([]) == []


def test_the_minutes_windows_use_the_latest_games_whatever_the_input_order() -> None:
    shuffled = [(3, 30.0), (1, 10.0), (12, 30.0), (2, 10.0), (11, 30.0), (13, 30.0)]
    shuffled += [(d, 10.0) for d in range(4, 11)]
    [event] = minutes_events(shuffled)
    assert event.kind == events.MINUTES_SPIKE
    assert event.detail["through_scoring_period"] == 13


def test_an_observation_reads_a_pool_entry_and_a_snapshot_alike() -> None:
    entry = PoolEntry(
        espn_player_id=1,
        name="x",
        injury_status="OUT",
        injured=True,
        expected_return_date=date(2026, 12, 1),
        pro_team_id=13,
        on_team_id=0,
        status="WAIVERS",
        percent_owned=40.0,
        percent_change=-9.0,
        percent_started=10.0,
        auction_value_average=1.0,
        waiver_clears_at=NEXT,
    )
    from_entry = Observation.from_entry(entry)
    assert from_entry.waiver_clears_at == NEXT
    # A stored snapshot has every column but the waiver date.
    assert Observation.from_snapshot(from_entry) == replace(from_entry, waiver_clears_at=None)
