"""Storing report lines, and reading them back without looking ahead.

The accessor's whole value is that it cannot see past the moment it is
asked about, so most of what is pinned here is what it refuses to answer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import injuries
from app.db.models import InjuryReport, Player
from app.injuries import absences, morning_of, out_mornings, status_as_of, statuses_as_of
from app.injury_reports import ET, Report, ReportLine, store_report
from app.player_names import name_index

SEASON = 2026


def player(session: Session, espn_id: int, name: str) -> Player:
    row = Player(espn_player_id=espn_id, name=name)
    session.add(row)
    session.flush()
    return row


def line(
    *,
    game_date: date,
    name: str = "Miller, Brandon",
    status: str = "Out",
    team: str = "Charlotte Hornets",
    reason: str = "Injury/Illness - Right Shoulder; Soreness",
) -> ReportLine:
    return ReportLine(
        game_date=game_date,
        game_time="07:00 (ET)",
        matchup="CHA@ATL",
        team=team,
        player_name_raw=name,
        status=status,
        reason=reason,
    )


def report(at: datetime, *lines: ReportLine) -> Report:
    return Report(reported_at=at, lines=tuple(lines))


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(UTC)


@pytest.fixture
def session(scoring_session: Session) -> Session:
    scoring_session.execute(text("TRUNCATE injury_reports RESTART IDENTITY CASCADE"))
    scoring_session.commit()
    return scoring_session


# ---------------------------------------------------------------------------
# storing
# ---------------------------------------------------------------------------


def test_a_name_that_matches_is_placed_and_one_that_does_not_is_kept_raw(
    session: Session,
) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    player(session, 3032977, "Kevin Love")
    session.commit()

    day = date(2025, 11, 11)
    stored = store_report(
        session,
        report(
            et(day, 9, 30),
            line(game_date=day),
            line(game_date=day, name="Nobody, Ivan", team="Utah Jazz"),
        ),
        name_index(session),
    )
    session.commit()

    assert stored.lines == 2
    assert stored.matched == 1
    assert stored.unmatched == 1
    assert stored.unmatched_names == ["Nobody, Ivan"]

    rows = session.scalars(select(InjuryReport).order_by(InjuryReport.player_name_raw)).all()
    assert [row.player_id for row in rows] == [miller.id, None]
    # The miss keeps the league's own spelling rather than vanishing.
    assert rows[1].player_name_raw == "Nobody, Ivan"
    assert rows[0].pro_team_id is not None


def test_an_ambiguous_name_is_refused_rather_than_guessed(session: Session) -> None:
    # Two players of the same name, neither with a season line: the matcher
    # has nothing to break the tie on, so it places neither.
    player(session, 1, "Brandon Miller")
    player(session, 2, "Brandon Miller")
    session.commit()

    day = date(2025, 11, 11)
    stored = store_report(session, report(et(day, 9, 30), line(game_date=day)), name_index(session))
    session.commit()
    assert stored.matched == 0
    assert stored.unmatched == 1
    assert session.scalar(select(InjuryReport.player_id)) is None


def test_a_second_run_of_the_same_snapshot_inserts_nothing(session: Session) -> None:
    player(session, 4432816, "Brandon Miller")
    session.commit()
    day = date(2025, 11, 11)
    snapshot = report(et(day, 9, 30), line(game_date=day))

    first = store_report(session, snapshot, name_index(session))
    session.commit()
    second = store_report(session, snapshot, name_index(session))
    session.commit()

    assert first.inserted == 1
    assert second.inserted == 0
    assert session.scalar(select(func.count()).select_from(InjuryReport)) == 1


def test_a_team_that_has_not_filed_is_stored_with_no_status(session: Session) -> None:
    day = date(2025, 11, 11)
    store_report(
        session,
        report(
            et(day, 9, 30),
            ReportLine(
                game_date=day,
                game_time="07:00 (ET)",
                matchup="CHA@ATL",
                team="Charlotte Hornets",
                player_name_raw="",
                status="",
                reason="NOT YET SUBMITTED",
            ),
        ),
        name_index(session),
    )
    session.commit()
    row = session.scalar(select(InjuryReport))
    assert row is not None
    assert row.status is None
    assert row.player_name_raw == ""
    assert row.reason == "NOT YET SUBMITTED"


# ---------------------------------------------------------------------------
# the point-in-time rule
# ---------------------------------------------------------------------------


def test_a_later_snapshot_is_invisible_at_the_earlier_moment(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    day = date(2025, 11, 11)
    index = name_index(session)
    store_report(session, report(et(day, 9, 30), line(game_date=day, status="Out")), index)
    store_report(session, report(et(day, 16, 30), line(game_date=day, status="Available")), index)
    session.commit()

    at_nine = status_as_of(session, miller.id, morning_of(day))
    assert at_nine is not None
    assert at_nine.status == "Out"
    assert at_nine.ruled_out

    at_five = status_as_of(session, miller.id, et(day, 17))
    assert at_five is not None
    assert at_five.status == "Available"
    assert not at_five.ruled_out

    # And nothing at all before the first report was published.
    assert status_as_of(session, miller.id, et(day, 8)) is None


def test_a_line_about_yesterdays_game_does_not_mean_he_is_still_out(
    session: Session,
) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    monday = date(2025, 11, 10)
    store_report(session, report(et(monday, 9, 30), line(game_date=monday)), name_index(session))
    session.commit()

    assert status_as_of(session, miller.id, morning_of(monday)) is not None
    # Tuesday: the only line we hold was about Monday's game, so the league
    # has said nothing current. None, not "Out".
    assert status_as_of(session, miller.id, morning_of(date(2025, 11, 11))) is None


def test_a_line_filed_today_about_tomorrows_game_is_visible_today(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    today, tomorrow = date(2025, 11, 11), date(2025, 11, 12)
    store_report(session, report(et(today, 9, 30), line(game_date=tomorrow)), name_index(session))
    session.commit()
    found = status_as_of(session, miller.id, morning_of(today))
    assert found is not None
    assert found.game_date == tomorrow


def test_a_roster_is_read_in_one_go_under_the_same_rule(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    love = player(session, 3032977, "Kevin Love")
    fit = player(session, 5, "Never Reported")
    session.commit()
    day = date(2025, 11, 11)
    index = name_index(session)
    store_report(
        session,
        report(
            et(day, 9, 30),
            line(game_date=day, name="Miller, Brandon", status="Out"),
            line(game_date=day, name="Love, Kevin", status="Questionable", team="Utah Jazz"),
        ),
        index,
    )
    store_report(
        session,
        report(et(day, 16, 30), line(game_date=day, name="Love, Kevin", team="Utah Jazz")),
        index,
    )
    session.commit()

    found = statuses_as_of(session, [miller.id, love.id, fit.id], morning_of(day))
    assert set(found) == {miller.id, love.id}
    assert found[miller.id].status == "Out"
    # The evening upgrade is not visible in the morning.
    assert found[love.id].status == "Questionable"
    assert found[love.id].in_doubt
    assert fit.id not in found

    later = statuses_as_of(session, [miller.id, love.id, fit.id], et(day, 17))
    assert later[love.id].status == "Out"
    assert statuses_as_of(session, [], morning_of(day)) == {}


# ---------------------------------------------------------------------------
# absences
# ---------------------------------------------------------------------------


def test_absences_are_runs_of_out_days_and_a_gap_breaks_one(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    index = name_index(session)
    out_days = [date(2025, 11, 10), date(2025, 11, 11), date(2025, 11, 12)]
    back_later = [date(2025, 11, 16), date(2025, 11, 17)]
    for day in [*out_days, *back_later]:
        store_report(session, report(et(day, 9, 30), line(game_date=day)), index)
    # Two days in between he played: the league listed him Available.
    for day in (date(2025, 11, 14),):
        store_report(
            session, report(et(day, 9, 30), line(game_date=day, status="Available")), index
        )
    session.commit()

    runs = absences(session, miller.id, SEASON)
    assert [(run.first, run.last, run.days) for run in runs] == [
        (date(2025, 11, 10), date(2025, 11, 12), 3),
        (date(2025, 11, 16), date(2025, 11, 17), 2),
    ]
    assert runs[0].covers(date(2025, 11, 11))
    assert not runs[0].covers(date(2025, 11, 13))
    # 13 and 15 are days the league said nothing: they break the run rather
    # than extending it.
    assert out_mornings(session, miller.id, SEASON) == 5
    assert absences(session, miller.id, 2025) == []


def test_absences_count_mornings_not_evenings(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    monday, tuesday = date(2025, 11, 10), date(2025, 11, 11)
    store_report(session, report(et(monday, 17, 30), line(game_date=tuesday)), name_index(session))
    session.commit()
    # Published Monday evening about Tuesday's game. It is the current line
    # by Monday night, but `absences` counts mornings, and at ten on Monday
    # it did not exist yet -- so Monday is not an Out morning and Tuesday is.
    assert status_as_of(session, miller.id, et(monday, 18)) is not None
    assert status_as_of(session, miller.id, morning_of(monday)) is None
    assert [(run.first, run.last) for run in absences(session, miller.id, SEASON)] == [
        (tuesday, tuesday)
    ]


def test_a_day_his_team_did_not_play_carries_an_absence_across(session: Session) -> None:
    """The Brandon Miller case: Charlotte played every other day."""
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    index = name_index(session)
    for day in (date(2025, 11, 10), date(2025, 11, 12), date(2025, 11, 14)):
        store_report(session, report(et(day, 9, 30), line(game_date=day)), index)
    session.commit()
    # The 11th and the 13th carry no line for him and none for his team
    # either, so the league said nothing rather than said he was fit.
    assert status_as_of(session, miller.id, morning_of(date(2025, 11, 11))) is None
    assert [(run.first, run.last, run.days) for run in absences(session, miller.id, SEASON)] == [
        (date(2025, 11, 10), date(2025, 11, 14), 5)
    ]
    assert out_mornings(session, miller.id, SEASON) == 5


def test_a_day_his_team_filed_without_naming_him_ends_the_absence(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    team_mate = player(session, 9999, "Miles Bridges")
    session.commit()
    index = name_index(session)
    for day in (date(2025, 11, 10), date(2025, 11, 12)):
        store_report(session, report(et(day, 9, 30), line(game_date=day)), index)
    # The 14th: Charlotte filed, and Miller is not on it. He is back.
    back = date(2025, 11, 14)
    store_report(
        session,
        report(et(back, 9, 30), line(game_date=back, name="Bridges, Miles", status="Probable")),
        index,
    )
    session.commit()
    assert team_mate.id is not None
    assert [(run.first, run.last, run.days) for run in absences(session, miller.id, SEASON)] == [
        (date(2025, 11, 10), date(2025, 11, 12), 3)
    ]


def test_the_morning_rule_is_ten_eastern_so_it_can_see_the_nine_oclock_report(
    session: Session,
) -> None:
    day = date(2025, 11, 11)
    assert morning_of(day) == datetime(2025, 11, 11, 15, 0, tzinfo=UTC)
    assert morning_of(day).astimezone(ET).hour == 10
    # The hourly reports were stamped half past, so a read at nine would have
    # found only the eight o'clock one. This is the case that forced the hour.
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    store_report(session, report(et(day, 9, 30), line(game_date=day)), name_index(session))
    session.commit()
    assert status_as_of(session, miller.id, morning_of(day)) is not None
    assert status_as_of(session, miller.id, et(day, 9)) is None
    # And it follows the clock change rather than a fixed UTC offset.
    summer = morning_of(date(2026, 4, 1))
    assert summer.astimezone(ET).hour == 10
    assert summer != datetime(2026, 4, 1, 15, 0, tzinfo=UTC)


def test_the_statuses_split_into_the_listeners_kinds() -> None:
    assert {"Out"} == injuries.RULED_OUT
    assert {"Doubtful", "Questionable"} == injuries.IN_DOUBT
    assert {"Probable", "Available"} == injuries.EXPECTED
    assert not (injuries.RULED_OUT & injuries.IN_DOUBT & injuries.EXPECTED)


def test_a_tie_between_two_games_at_one_instant_takes_the_nearer(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    today, tomorrow = date(2025, 11, 11), date(2025, 11, 12)
    store_report(
        session,
        report(
            et(today, 9, 30),
            line(game_date=today, status="Questionable"),
            line(game_date=tomorrow, status="Out"),
        ),
        name_index(session),
    )
    session.commit()
    found = status_as_of(session, miller.id, morning_of(today))
    assert found is not None
    assert found.game_date == today
    assert statuses_as_of(session, [miller.id], morning_of(today))[miller.id].game_date == today


def test_the_season_window_runs_october_to_september(session: Session) -> None:
    miller = player(session, 4432816, "Brandon Miller")
    session.commit()
    day = date(2026, 3, 2)
    store_report(session, report(et(day, 9, 30), line(game_date=day)), name_index(session))
    session.commit()
    assert out_mornings(session, miller.id, 2026) == 1
    assert out_mornings(session, miller.id, 2027) == 0
    assert (date(2026, 3, 2) - timedelta(days=1)).year == 2026
