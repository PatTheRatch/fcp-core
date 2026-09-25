"""The declared source order, and the proof that it cannot look ahead.

Everything the engine knows about who is hurt on a replayed morning comes
through `app.pickups.state.status_on`, so what is pinned here is the order
it asks in, the words it maps across, the moment it refuses to read past,
and that a roster the league never named reads exactly as it did before the
source existed (`docs/replay_status.md`).

Every fixture here gives its man a status snapshot, because the NBA team a
schedule is keyed on still comes from one and a man with no NBA team has no
games at all. What the tests vary is **when** it was observed: before the
morning being asked about, which is a live morning and ESPN's, or long
after it, which is a replayed day of the live season and the league's.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db.models import InjuryReport, LeagueSeason, MatchupPeriod, Player, Team
from app.injuries import morning_of
from app.injury_reports import ET, NBA_OFFICIAL, STATUSES
from app.pickups import status_source
from app.pickups.state import build_players, load_team_week, season_calendar, status_on
from tests.pickups_db import (
    ANY,
    GUARD,
    OBSERVED,
    OPENING,
    SEASON,
    clear_schedule,
    configure,
    day_date,
    eligible,
    games,
    snapshot,
)
from tests.scoring_db import held, league_season, player

HOME, AWAY = 1, 2

#: An observation from after the whole season: the listener's own pass, as a
#: replayed day of the live season sees it. It supplies the NBA team and
#: nothing else, because the gate will not open for it.
LATE = datetime.combine(OPENING, datetime.min.time(), tzinfo=UTC) + timedelta(days=200)


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    scoring_session.execute(text("TRUNCATE injury_reports RESTART IDENTITY CASCADE"))
    scoring_session.flush()
    yield scoring_session


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(UTC)


def filed(
    session: Session,
    *,
    at: datetime,
    game_date: date,
    status: str,
    who: Player | None = None,
    raw: str = "Nobody, Aaron",
    team: str = "Charlotte Hornets",
) -> InjuryReport:
    """One report line, stored the way the backfill stores it.

    With `who`, the line is placed on that player and the raw spelling is
    beside the point, which is what a placed line looks like. Without one it
    is a name the matcher refused, which is the `unmatched` case.
    """
    row = InjuryReport(
        reported_at=at,
        game_date=game_date,
        game_time="07:00 (ET)",
        matchup="CHA@ATL",
        team=team,
        player_name_raw=raw if who is None else who.name,
        player_id=None if who is None else who.id,
        status=status,
        reason="Injury/Illness - Right Shoulder; Soreness",
        source=NBA_OFFICIAL,
        fetched_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def a_guard(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    *,
    observed_at: datetime = LATE,
    status: str = "ACTIVE",
) -> Player:
    """One man on the home roster with a game on every day of the week."""
    guard = player(session, "Guard")
    eligible(session, guard, GUARD)
    snapshot(
        session,
        guard,
        pro_team_id=10,
        on_team_id=HOME,
        injury_status=status,
        observed_at=observed_at,
    )
    games(session, 10, [1, 2, 3, 4, 5, 6, 7])
    held(session, team, period, guard, 1)
    return guard


def forget(session: Session) -> None:
    """Drop the day's memo, so a fixture written mid-test is read afresh."""
    session.info.pop("pickups_status_source", None)


# ---------------------------------------------------------------------------
# the order
# ---------------------------------------------------------------------------


def test_espn_snapshot_beats_the_report(session: Session) -> None:
    """A morning the listener had already run for is ESPN's, not the league's."""
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first, observed_at=OBSERVED)
    # The listener saw him fit; the league has him Out the same morning.
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status="Out", who=guard)

    read = status_on(session, SEASON, 3)

    assert read.source == status_source.ESPN
    assert read.of(guard.id) == ("ACTIVE", None)
    (man,) = load_team_week(session, ls, HOME, today=3).roster
    assert man.ruled_out is False, "ESPN said he is fit and ESPN is asked first"


def test_a_snapshot_taken_after_that_morning_does_not_open_the_gate(session: Session) -> None:
    """A replayed day of the live season reads the report, not next month.

    The same two rows as the test above with one thing changed: the
    listener's snapshot is from after the season rather than before the day.
    Reading it would be next month's status on a day in November, which is
    the fifth place a look-ahead could hide (`docs/inseason_rehearsal.md`).
    """
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first, observed_at=LATE)
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status="Out", who=guard)

    read = status_on(session, SEASON, 3)

    assert read.source == NBA_OFFICIAL
    assert read.reported_at == et(day_date(3), 9, 30)
    assert read.of(guard.id) == ("OUT", None)
    (man,) = load_team_week(session, ls, HOME, today=3).roster
    assert man.ruled_out is True
    assert man.game_days == (), "an OUT man is seated for nothing tonight"
    assert 0.0 < man.season_games < 5.0, "and the return prior gives him fractional games"


def test_silence_is_silence_and_reads_as_fit(session: Session) -> None:
    """A man the league never named keeps the null status he always had."""
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first)
    # The league filed that morning, and about somebody else.
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status="Out")

    read = status_on(session, SEASON, 3)

    assert read.source == NBA_OFFICIAL
    assert read.of(guard.id) == (None, None)
    (man,) = load_team_week(session, ls, HOME, today=3).roster
    assert man.injury_status is None
    assert man.ruled_out is False
    assert man.game_days == (3, 4, 5, 6, 7)


def test_no_source_at_all_leaves_every_man_fit(session: Session) -> None:
    """A played season with neither snapshots nor reports: as it always was."""
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = player(session, "Guard")
    eligible(session, guard, GUARD)
    games(session, 10, [3, 5, 7])
    held(session, home, first, guard, 1)

    read = status_on(session, SEASON, 3)

    assert read.source == status_source.NO_SOURCE
    assert read.of(guard.id) == (None, None)


# ---------------------------------------------------------------------------
# the mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "engine", "ruled_out"),
    [
        ("Out", "OUT", True),
        ("Doubtful", "DOUBTFUL", False),
        ("Questionable", "QUESTIONABLE", False),
        ("Probable", "PROBABLE", False),
        ("Available", "ACTIVE", False),
    ],
)
def test_the_five_words_map_to_the_engines(
    session: Session, printed: str, engine: str, ruled_out: bool
) -> None:
    """The whole mapping table of `docs/replay_status.md`, one row a case."""
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first)
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status=printed, who=guard)

    (man,) = load_team_week(session, ls, HOME, today=3).roster

    assert man.injury_status == engine
    assert man.ruled_out is ruled_out
    assert man.expected_return_date is None, "no report has ever carried one"


def test_the_mapping_covers_the_leagues_five_and_nothing_else() -> None:
    """The league prints five words and the table has five rows."""
    assert set(status_source.REPORT_TO_ENGINE) == set(STATUSES)


# ---------------------------------------------------------------------------
# the look-ahead proof
# ---------------------------------------------------------------------------


def test_a_report_published_after_the_morning_is_not_read(session: Session) -> None:
    """The proof, on a fixture where the later report would change the answer.

    Two lines about the same game: the nine-thirty report has him Out and the
    five o'clock upgrade has him Available. A replay of that morning must
    read the first and must not read the second, though the second is newer.
    """
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first)
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status="Out", who=guard)
    later = filed(
        session, at=et(day_date(3), 17, 30), game_date=day_date(3), status="Available", who=guard
    )

    read = status_on(session, SEASON, 3)

    assert read.of(guard.id) == ("OUT", None), "the evening upgrade is not visible at ten"
    assert read.read_as_of == morning_of(day_date(3))
    assert read.reported_at == et(day_date(3), 9, 30)
    assert later.reported_at > read.read_as_of, "and the line that was skipped really is later"
    # The same fixture read the next morning does see it, which is what makes
    # the assertion above a bound on the moment rather than on the row.
    filed(session, at=et(day_date(4), 9, 30), game_date=day_date(4), status="Available", who=guard)
    forget(session)
    assert status_on(session, SEASON, 4).of(guard.id) == ("ACTIVE", None)


def test_tomorrows_report_is_visible_and_yesterdays_expires(session: Session) -> None:
    """The `game_date` half of the rule, which is the easy one to get wrong."""
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first)
    # A line filed on day 2 about day 2's game says nothing about day 3.
    filed(session, at=et(day_date(2), 9, 30), game_date=day_date(2), status="Out", who=guard)

    assert status_on(session, SEASON, 2).of(guard.id) == ("OUT", None)
    assert status_on(session, SEASON, 3).of(guard.id) == (None, None)


# ---------------------------------------------------------------------------
# what the provenance carries
# ---------------------------------------------------------------------------


def test_a_name_the_league_prints_that_places_on_nobody_is_counted(session: Session) -> None:
    """An unplaced line leaves the man fit here and shows up as a number."""
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first)
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status="Out", who=guard)
    filed(
        session,
        at=et(day_date(3), 9, 30),
        game_date=day_date(3),
        status="Out",
        raw="Sorber, Thomas",
        team="Oklahoma City Thunder",
    )

    read = status_on(session, SEASON, 3)

    assert read.placed == 1
    assert read.unmatched == 1
    block = read.as_provenance()
    assert block["used"] == NBA_OFFICIAL
    assert block["unmatched"] == 1
    assert block["reported_at"] == et(day_date(3), 9, 30).isoformat()


def test_a_roster_the_league_never_named_reads_as_it_did_before(session: Session) -> None:
    """The guard the whole change rests on: nothing about a fit roster moves.

    The same week, built once with the league's report on the table and once
    with it empty. Every man on this roster is one the league did not name,
    so the two answers have to be equal object for object.
    """
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    guard = a_guard(session, home, first)
    centre = player(session, "Centre")
    eligible(session, centre, ANY)
    snapshot(session, centre, pro_team_id=11, on_team_id=HOME, observed_at=LATE)
    games(session, 11, [1, 3, 5, 7])
    held(session, home, first, centre, 2)

    without = build_players(session, ls, [guard.id, centre.id], (3, 4, 5, 6, 7))
    filed(session, at=et(day_date(3), 9, 30), game_date=day_date(3), status="Out")
    forget(session)
    with_report = build_players(session, ls, [guard.id, centre.id], (3, 4, 5, 6, 7))

    assert with_report == without


def test_a_mornings_read_is_held_and_keyed_on_the_season_and_the_day(
    session: Session,
) -> None:
    """Two calls about one morning are one read; another day is its own.

    A replayed morning is asked about once per team and once per wire, so the
    memo is what keeps that to two queries. It is keyed on the season as well
    as the day, because one database holds nine of them.
    """
    ls: LeagueSeason
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    a_guard(session, home, first)

    held = status_on(session, SEASON, 3)

    assert status_on(session, SEASON, 3) is held
    assert status_on(session, SEASON, 4) is not held
    assert set(session.info["pickups_status_source"]) == {
        (SEASON, day_date(3)),
        (SEASON, day_date(4)),
    }


def test_the_calendar_is_read_once_a_session_on_the_status_path_only(
    session: Session,
) -> None:
    """`status_on` holds the season's calendar; `season_calendar` does not.

    `build_players` asks for the calendar once per call, and every roster,
    wire, trade side and standings checkpoint goes through `build_players`,
    so the status path memoizes it. The bare function stays a query, because
    the listener rewrites the schedule inside a session that reads it back.
    """
    ls: LeagueSeason
    ls, (home, _), (first, _) = league_season(session, days_per_period=7)
    configure(ls)
    a_guard(session, home, first)
    schedule_reads: list[str] = []

    def saw(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        if "pro_team_games" in statement:
            schedule_reads.append(statement)

    status_on(session, SEASON, 3)
    status_on(session, SEASON + 1, 3)
    event.listen(session.get_bind(), "before_cursor_execute", saw)
    try:
        status_on(session, SEASON, 4)
        status_on(session, SEASON + 1, 4)
        assert schedule_reads == [], "a second morning does not read the schedule again"
        assert session.info["pickups_season_calendar"] == {
            SEASON: season_calendar(session, SEASON),
            SEASON + 1: None,
        }
        assert len(schedule_reads) == 1, "the bare function still reads it"
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", saw)

    games(session, 10, [8], season=SEASON + 1)

    assert season_calendar(session, SEASON + 1) is not None, "the bare read sees a later write"
    assert status_on(session, SEASON + 1, 4).read_as_of is None, "and the status path holds"
