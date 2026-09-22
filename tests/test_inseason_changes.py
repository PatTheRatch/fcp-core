"""The "what changed" feed, over rows built one at a time.

The load-bearing cases: the window has both ends, every kind has a sentence,
a team's own business is flagged as its own, one transaction is one change,
and the sources that see the same move twice say it once.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod, Player, PlayerStatusEvent, Team
from app.inseason import changes as feed
from app.inseason.changes import Change, changes
from app.listener import events as kinds
from tests.pickups_db import (
    OPENING,
    clear_schedule,
    clears_waivers_on,
    day_date,
    games,
    minutes_event,
    on_the_wire,
    snapshot,
)
from tests.scoring_db import held, league_season, matchup, player, transaction

MINE = 1
RIVAL = 2
THIRD = 3

#: Day 8 is the second matchup period's first day, and its date.
DAY = 8
NOON = datetime.combine(day_date(DAY), time(12, 0), tzinfo=UTC)
MORNING = datetime.combine(day_date(DAY), time(9, 0), tzinfo=UTC)
YESTERDAY = NOON - timedelta(days=1)
TOMORROW = NOON + timedelta(days=1)

#: The window every test reads unless it says otherwise: the whole of day 8.
SINCE = datetime.combine(day_date(DAY), time(0, 0), tzinfo=UTC)
UNTIL = datetime.combine(day_date(DAY + 1), time(0, 0), tzinfo=UTC)


@pytest.fixture
def league(
    scoring_session: Session,
) -> Iterator[tuple[LeagueSeason, list[Team], list[MatchupPeriod]]]:
    """Three teams, two matchup periods, the schedule, and mine against the rival."""
    clear_schedule(scoring_session)
    ls, teams, periods = league_season(
        scoring_session, team_names=("Through The Wire", "Load Management", "Third Man")
    )
    matchup(scoring_session, periods[1], teams[0], teams[1])
    games(scoring_session, 13, list(range(1, 15)))
    scoring_session.commit()
    yield ls, teams, periods


def _changes(session: Session, ls: LeagueSeason, **kwargs: object) -> list[Change]:
    options: dict[str, object] = {"since": SINCE, "until": UNTIL, "team_id": MINE}
    options.update(kwargs)
    return changes(session, ls, **options)  # type: ignore[arg-type]


def _event(
    session: Session,
    who: Player,
    kind: str,
    *,
    at: datetime,
    previous: dict[str, object] | None = None,
    current: dict[str, object] | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    session.add(
        PlayerStatusEvent(
            player_id=who.id,
            season=2026,
            kind=kind,
            observed_at=at,
            previous=previous or {},
            current=current or {},
            detail=detail or {},
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------


def test_only_what_happened_inside_the_window_is_reported(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """Before, inside and after; only inside comes back.

    The far end is the one that was missing: without it a digest built for a
    day in January read the rest of the season and said 609 moves on the wire
    in the last day where nine had been made.
    """
    ls, teams, _periods = league
    out = player(scoring_session, "Out Man")
    snapshot(scoring_session, out, pro_team_id=13, on_team_id=MINE)
    for when, status in ((YESTERDAY, "OUT"), (NOON, "DOUBTFUL"), (TOMORROW, "QUESTIONABLE")):
        _event(
            scoring_session,
            out,
            kinds.WENT_OUT if status == "OUT" else kinds.DOWNGRADED,
            at=when,
            previous={"injury_status": "ACTIVE"},
            current={"injury_status": status},
        )
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "WAIVER",
        [("ADD", player(scoring_session, "Wanted"), None, teams[1])],
        processed=YESTERDAY,
    )
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "WAIVER",
        [("ADD", player(scoring_session, "Taken"), None, teams[1])],
        processed=MORNING,
        bid=4,
    )
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "WAIVER",
        [("ADD", player(scoring_session, "Later"), None, teams[1])],
        processed=TOMORROW,
    )
    scoring_session.commit()

    found = _changes(scoring_session, ls)

    assert [change.text for change in found] == [
        "Out Man (Through The Wire) is downgraded: ACTIVE to DOUBTFUL.",
        "Load Management claimed Taken for $4.",
    ]


def test_the_feed_is_newest_first_and_the_worst_news_leads_its_moment(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    ls, _teams, _periods = league
    hurt = player(scoring_session, "Hurt Man")
    fine = player(scoring_session, "Fine Man")
    for who in (hurt, fine):
        snapshot(scoring_session, who, pro_team_id=13, on_team_id=MINE)
    _event(
        scoring_session,
        fine,
        kinds.RETURNED,
        at=NOON,
        previous={"injury_status": "OUT"},
        current={"injury_status": "ACTIVE"},
    )
    _event(
        scoring_session,
        hurt,
        kinds.WENT_OUT,
        at=NOON,
        previous={"injury_status": "ACTIVE"},
        current={"injury_status": "OUT"},
    )
    _event(
        scoring_session,
        fine,
        kinds.DOWNGRADED,
        at=MORNING,
        previous={"injury_status": "ACTIVE"},
        current={"injury_status": "QUESTIONABLE"},
    )
    scoring_session.commit()

    found = _changes(scoring_session, ls)

    assert [(change.at, change.severity) for change in found] == [
        (NOON, feed.OUT),
        (NOON, feed.NEWS),
        (MORNING, feed.DOUBT),
    ]


# ---------------------------------------------------------------------------
# the sentences
# ---------------------------------------------------------------------------


def test_a_claim_is_one_change_with_the_money_and_the_man_it_cost(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """One transaction, not one per item: a manager reads a claim as one move."""
    ls, teams, _periods = league
    transaction(
        scoring_session,
        teams[0],
        DAY,
        "WAIVER",
        [
            ("ADD", player(scoring_session, "Jock Landale"), None, teams[0]),
            ("DROP", player(scoring_session, "Zach Edey"), teams[0], None),
        ],
        processed=MORNING,
        bid=5,
    )
    scoring_session.commit()

    [change] = _changes(scoring_session, ls)

    assert change.kind == feed.CLAIM
    assert change.text == "Through The Wire claimed Jock Landale for $5, dropping Zach Edey."
    assert [person.name for person in change.players] == ["Jock Landale", "Zach Edey"]
    assert change.mine and not change.opponent


def test_a_free_pickup_is_an_add_and_a_bare_drop_is_a_drop(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    ls, teams, _periods = league
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "FREEAGENT",
        [("ADD", player(scoring_session, "Cam Spencer"), None, teams[1])],
        processed=MORNING,
    )
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "FREEAGENT",
        [("DROP", player(scoring_session, "Sam Hauser"), teams[1], None)],
        processed=MORNING,
    )
    scoring_session.commit()

    texts = sorted(change.text for change in _changes(scoring_session, ls))

    assert texts == [
        "Load Management added Cam Spencer.",
        "Load Management dropped Sam Hauser.",
    ]
    assert {change.kind for change in _changes(scoring_session, ls)} == {feed.ADD, feed.DROP}


@pytest.mark.parametrize(
    ("kind", "previous", "current", "detail", "expected"),
    [
        (
            kinds.WENT_OUT,
            {"injury_status": "ACTIVE"},
            {"injury_status": "OUT", "expected_return_date": "2025-12-01"},
            {},
            "Star Man (Through The Wire) is out: ACTIVE to OUT, back 01 Dec.",
        ),
        (
            kinds.RETURNED,
            {"injury_status": "OUT"},
            {"injury_status": "ACTIVE"},
            {},
            "Star Man (Through The Wire) is back: OUT to ACTIVE.",
        ),
        (
            kinds.RETURN_DATE_CHANGED,
            {"expected_return_date": "2025-12-01"},
            {"expected_return_date": "2025-12-15"},
            {"days": 14},
            "Star Man (Through The Wire) has a new return date: 15 Dec, 14 days later.",
        ),
        (
            kinds.CHANGED_PRO_TEAM,
            {"pro_team_id": 13},
            {"pro_team_id": 25},
            {},
            "Star Man (Through The Wire) changed NBA team: NBA team 13 to 25.",
        ),
        (
            kinds.OWNERSHIP_SURGE,
            {},
            {"percent_owned": 26.0, "percent_change": 9.0},
            {},
            "Star Man (Through The Wire) is being added around ESPN: 26.0% owned, +9 today.",
        ),
        (
            kinds.WAIVER_CLEARING,
            {},
            {},
            {"clears_at": "2025-10-29T00:30:00+00:00"},
            "Star Man (Through The Wire) clears waivers: at 00:30 UTC.",
        ),
    ],
)
def test_every_listener_kind_reads_as_a_sentence(
    scoring_session: Session,
    league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]],
    kind: str,
    previous: dict[str, object],
    current: dict[str, object],
    detail: dict[str, object],
    expected: str,
) -> None:
    ls, _teams, _periods = league
    who = player(scoring_session, "Star Man")
    snapshot(scoring_session, who, pro_team_id=13, on_team_id=MINE)
    _event(scoring_session, who, kind, at=NOON, previous=previous, current=current, detail=detail)
    scoring_session.commit()

    [change] = _changes(scoring_session, ls)

    assert change.text == expected
    assert change.event_kind == kind
    assert change.detail, "the digest's own column, from the same sentence"


def test_a_minutes_event_is_its_own_kind(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    ls, _teams, _periods = league
    who = player(scoring_session, "Backup Man")
    minutes_event(
        scoring_session,
        who,
        kinds.MINUTES_SPIKE,
        through=DAY,
        recent_mean=31.0,
        prior_mean=18.0,
        observed_at=NOON,
    )
    scoring_session.commit()

    [change] = _changes(scoring_session, ls)

    assert change.kind == feed.MINUTES
    assert change.text == "Backup Man is playing more: 31.0 min last 3, was 18.0."
    assert change.on_wire, "nobody holds him, so he is the wire's business"


# ---------------------------------------------------------------------------
# mine, and my opponent's
# ---------------------------------------------------------------------------


def test_my_team_and_my_opponent_are_flagged_and_nobody_else_is(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """The period `until` falls in decides who the opponent is."""
    ls, teams, _periods = league
    for team in teams:
        transaction(
            scoring_session,
            team,
            DAY,
            "WAIVER",
            [("ADD", player(scoring_session, f"For {team.name}"), None, team)],
            processed=MORNING,
        )
    scoring_session.commit()

    flags = {
        change.teams[0].name: (change.mine, change.opponent)
        for change in _changes(scoring_session, ls)
    }

    assert flags == {
        "Through The Wire": (True, False),
        "Load Management": (False, True),
        "Third Man": (False, False),
    }


def test_without_a_team_nothing_is_flagged(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    ls, teams, _periods = league
    transaction(
        scoring_session,
        teams[0],
        DAY,
        "WAIVER",
        [("ADD", player(scoring_session, "Anyone"), None, teams[0])],
        processed=MORNING,
    )
    scoring_session.commit()

    [change] = _changes(scoring_session, ls, team_id=None)

    assert not change.mine and not change.opponent


# ---------------------------------------------------------------------------
# the wire's own pool
# ---------------------------------------------------------------------------


def test_the_wire_diff_sees_a_drop_cleared_a_claim_and_waivers_clearing(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """A man on the wire now who was not before was dropped onto it; one who
    was on it and is gone has been claimed; one who was on waivers and is a
    free agent now has cleared them."""
    ls, _teams, _periods = league
    before, after = MORNING, NOON
    stayed = player(scoring_session, "Stayed Put")
    arrived = player(scoring_session, "Newly Dropped")
    gone = player(scoring_session, "Newly Claimed")
    cleared = player(scoring_session, "Off Waivers")
    on_the_wire(scoring_session, ls, stayed, observed_at=before)
    on_the_wire(scoring_session, ls, gone, observed_at=before)
    on_the_wire(
        scoring_session,
        ls,
        cleared,
        observed_at=before,
        status="WAIVERS",
        clears_at=clears_waivers_on(DAY),
    )
    on_the_wire(scoring_session, ls, stayed, observed_at=after)
    on_the_wire(scoring_session, ls, arrived, observed_at=after)
    on_the_wire(scoring_session, ls, cleared, observed_at=after)
    scoring_session.commit()

    found = {change.kind: change.text for change in _changes(scoring_session, ls)}

    assert found == {
        feed.DROP: "Newly Dropped is on the wire now.",
        feed.CLAIM: "Newly Claimed is off the wire; someone took him.",
        feed.WAIVER_CLEAR: "Off Waivers cleared waivers and is a free agent now.",
    }


def test_the_pass_before_the_window_is_the_baseline_and_is_not_itself_news(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """Without it the whole wire would read as dropped this morning."""
    ls, _teams, _periods = league
    who = player(scoring_session, "Long Time Free Agent")
    on_the_wire(scoring_session, ls, who, observed_at=YESTERDAY)
    on_the_wire(scoring_session, ls, who, observed_at=NOON)
    scoring_session.commit()

    assert _changes(scoring_session, ls) == []


def test_the_ledger_silences_the_other_sources_about_the_same_move(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """The listener sees a drop at its next pass and the pool sees it again;
    the ledger row has the team and the money, so it is the one reported."""
    ls, teams, _periods = league
    who = player(scoring_session, "Zach Edey")
    transaction(
        scoring_session,
        teams[0],
        DAY,
        "WAIVER",
        [("DROP", who, teams[0], None)],
        processed=MORNING,
    )
    _event(
        scoring_session,
        who,
        kinds.DROPPED,
        at=NOON,
        previous={"status": "ONTEAM"},
        current={"status": "FREEAGENT"},
        detail={"from_team_id": MINE},
    )
    on_the_wire(scoring_session, ls, player(scoring_session, "Someone Else"), observed_at=MORNING)
    on_the_wire(scoring_session, ls, who, observed_at=NOON)
    on_the_wire(scoring_session, ls, player(scoring_session, "Someone Else"), observed_at=NOON)
    scoring_session.commit()

    found = _changes(scoring_session, ls)

    assert [change.text for change in found] == ["Through The Wire dropped Zach Edey."]


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------


def test_a_trade_the_ledger_names_is_one_change_with_both_sides(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    ls, teams, _periods = league
    turner = player(scoring_session, "Myles Turner")
    queta = player(scoring_session, "Neemias Queta")
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "TRADE_ACCEPT",
        [("TRADE", turner, teams[0], teams[1]), ("TRADE", queta, teams[1], teams[0])],
        processed=MORNING,
    )
    scoring_session.commit()

    [change] = _changes(scoring_session, ls)

    assert change.kind == feed.TRADE
    assert change.text == (
        "Load Management and Through The Wire traded: "
        "Myles Turner to Load Management, Neemias Queta to Through The Wire."
    )
    assert change.mine and change.opponent


def test_a_trade_the_ledger_does_not_name_is_read_off_the_rosters(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """ESPN's TRADE_ACCEPT often carries no items at all, so the deal is
    reconstructed from who stopped appearing for one team and started for
    another; it has a day and not a moment, so it is dated at noon."""
    ls, teams, periods = league
    theirs = player(scoring_session, "Their Man")
    ours = player(scoring_session, "Our Man")
    # The deal is dated by the last day each man appeared for the side giving
    # him up, which is the day the window is about.
    for day in range(1, DAY + 1):
        held(scoring_session, teams[0], periods[(day - 1) // 7], ours, day)
        held(scoring_session, teams[1], periods[(day - 1) // 7], theirs, day)
    for day in range(DAY + 1, DAY + 4):
        held(scoring_session, teams[1], periods[(day - 1) // 7], ours, day)
        held(scoring_session, teams[0], periods[(day - 1) // 7], theirs, day)
    scoring_session.commit()

    found = [change for change in _changes(scoring_session, ls) if change.kind == feed.TRADE]

    assert len(found) == 1, "one deal, not one per team in it"
    assert found[0].at == NOON, "a day, not a moment, so noon on that day"
    assert found[0].text == (
        "Load Management and Through The Wire traded: "
        "Our Man to Load Management, Their Man to Through The Wire."
    )


# ---------------------------------------------------------------------------
# the movements nothing explains
# ---------------------------------------------------------------------------


def test_a_change_of_hands_no_transaction_explains_is_a_lineup_change(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """The safety net for a season whose transaction feed missed a move."""
    ls, teams, periods = league
    who = player(scoring_session, "Quietly Moved")
    for day in range(1, DAY):
        held(scoring_session, teams[0], periods[(day - 1) // 7], who, day)
    for day in range(DAY, DAY + 3):
        held(scoring_session, teams[1], periods[(day - 1) // 7], who, day)
    scoring_session.commit()

    found = [change for change in _changes(scoring_session, ls) if change.kind == feed.LINEUP]

    assert [change.text for change in found] == [
        "Quietly Moved is on Load Management's roster; no transaction in the record says how."
    ]


def test_a_spell_running_to_the_last_day_looked_at_is_not_a_departure(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """A man is only known to have left once a day has been seen without him.

    Reading past `until` to find out whether he was really dropped is the
    look-ahead the rehearsal went hunting for, and it would also report the
    move a second time when tomorrow's ledger row arrives.
    """
    ls, teams, periods = league
    who = player(scoring_session, "Last Seen Today")
    for day in range(1, DAY + 1):
        held(scoring_session, teams[0], periods[(day - 1) // 7], who, day)
    other = player(scoring_session, "Still Here")
    for day in range(1, DAY + 3):
        held(scoring_session, teams[0], periods[(day - 1) // 7], other, day)
    scoring_session.commit()

    assert [change for change in _changes(scoring_session, ls) if change.kind == feed.LINEUP] == []

    # A window that reaches the day after has seen a lineup without him.
    later = _changes(scoring_session, ls, until=UNTIL + timedelta(days=1), since=SINCE)
    assert [change.text for change in later if change.kind == feed.LINEUP] == [
        "Last Seen Today is off Through The Wire's roster; no transaction in the record says how."
    ]


# ---------------------------------------------------------------------------
# what a caller can ask for
# ---------------------------------------------------------------------------


def test_asking_for_the_listeners_kinds_leaves_the_ledger_alone(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """What the digest does: it never shows a trade, so it never pays for one."""
    ls, teams, _periods = league
    who = player(scoring_session, "Star Man")
    snapshot(scoring_session, who, pro_team_id=13, on_team_id=MINE)
    _event(
        scoring_session,
        who,
        kinds.WENT_OUT,
        at=NOON,
        previous={"injury_status": "ACTIVE"},
        current={"injury_status": "OUT"},
    )
    transaction(
        scoring_session,
        teams[1],
        DAY,
        "TRADE_ACCEPT",
        [("TRADE", player(scoring_session, "Traded Man"), teams[0], teams[1])],
        processed=MORNING,
    )
    scoring_session.commit()

    found = _changes(scoring_session, ls, kinds_wanted=[feed.STATUS])

    assert [change.kind for change in found] == [feed.STATUS]


def test_unnotified_reads_only_what_has_never_gone_out(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """The owner's digest, whose window is what it has not yet told him."""
    ls, _teams, _periods = league
    told = player(scoring_session, "Old News")
    fresh = player(scoring_session, "New News")
    for who in (told, fresh):
        snapshot(scoring_session, who, pro_team_id=13, on_team_id=MINE)
        _event(
            scoring_session,
            who,
            kinds.WENT_OUT,
            at=NOON,
            previous={"injury_status": "ACTIVE"},
            current={"injury_status": "OUT"},
        )
    sent = scoring_session.scalars(
        select(PlayerStatusEvent).where(PlayerStatusEvent.player_id == told.id)
    ).one()
    sent.notified_at = NOON
    scoring_session.commit()

    found = _changes(scoring_session, ls, unnotified=True)

    assert [change.players[0].name for change in found] == ["New News"]
    assert found[0].event_id is not None, "so the digest can mark it once it has gone"


def test_a_season_with_no_schedule_still_reports_what_it_has(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    """No `pro_team_games` means no calendar: a reconstructed trade cannot be
    dated and is left out, and everything with a moment of its own stands."""
    ls, teams, _periods = league
    scoring_session.execute(text("TRUNCATE pro_team_games"))
    transaction(
        scoring_session,
        teams[0],
        DAY,
        "WAIVER",
        [("ADD", player(scoring_session, "Anyone"), None, teams[0])],
        processed=MORNING,
    )
    scoring_session.commit()

    assert [change.kind for change in _changes(scoring_session, ls)] == [feed.CLAIM]


def test_opening_day_is_nobody_arriving(
    scoring_session: Session, league: tuple[LeagueSeason, list[Team], list[MatchupPeriod]]
) -> None:
    ls, teams, periods = league
    for name in ("One", "Two"):
        for day in (1, 2, 3):
            held(scoring_session, teams[0], periods[0], player(scoring_session, name), day)
    scoring_session.commit()

    opened = datetime.combine(OPENING, time(0, 0), tzinfo=UTC)
    found = changes(
        scoring_session,
        ls,
        since=opened,
        until=opened + timedelta(days=3),
        team_id=MINE,
    )

    assert found == []
