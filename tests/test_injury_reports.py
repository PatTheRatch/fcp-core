"""Reading the NBA's official injury reports, against two real ones.

Both fixtures are the league's own PDFs for 11 November 2025, the morning
report and the evening one. They are kept because a parser that reads
geometry has to be tested against the thing itself: a constructed PDF would
prove only that the test builder and the parser agree.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.injury_reports import (
    ET,
    FIRST_QUARTER_HOURLY,
    LAST_HOURLY,
    NOT_SUBMITTED,
    STATUSES,
    Report,
    ReportLayoutError,
    ReportLine,
    parse_report,
    pro_team_id,
    report_url,
    snapshot_times,
)
from app.player_names import from_last_first, name_key

FIXTURES = Path(__file__).parent / "fixtures"
MORNING = FIXTURES / "nba_injury_report_2025-11-11_0930ET.pdf"
EVENING = FIXTURES / "nba_injury_report_2025-11-11_1730ET.pdf"


@pytest.fixture(scope="module")
def morning() -> Report:
    return parse_report(MORNING.read_bytes())


@pytest.fixture(scope="module")
def evening() -> Report:
    return parse_report(EVENING.read_bytes())


def line_for(report: Report, surname_first: str) -> ReportLine:
    found = [line for line in report.lines if line.player_name_raw == surname_first]
    assert found, f"{surname_first} is not in the report"
    return found[0]


# ---------------------------------------------------------------------------
# the timestamp, and where a snapshot lives
# ---------------------------------------------------------------------------


def test_the_stamp_is_the_label_inside_the_pdf_not_the_url(morning: Report) -> None:
    # The URL said `_09AM`; the league stamped the report 09:30 AM Eastern.
    assert morning.reported_at == datetime(2025, 11, 11, 14, 30, tzinfo=UTC)
    assert morning.reported_at.astimezone(ET).strftime("%I:%M %p") == "09:30 AM"


def test_an_hourly_url_names_the_hour_and_a_quarter_hourly_one_the_quarter() -> None:
    assert report_url(datetime(2025, 11, 11, 9, 30, tzinfo=ET)).endswith("2025-11-11_09AM.pdf")
    assert report_url(datetime(2026, 1, 15, 10, 30, tzinfo=ET)).endswith("2026-01-15_10_30AM.pdf")


def test_the_gap_between_the_two_cadences_has_no_url() -> None:
    with pytest.raises(ValueError, match="no injury report URL"):
        report_url(datetime(2025, 12, 20, 9, 30, tzinfo=ET))
    assert report_url(LAST_HOURLY)
    assert report_url(FIRST_QUARTER_HOURLY)


def test_a_day_offers_twenty_four_snapshots_hourly_and_ninety_six_after() -> None:
    assert len(snapshot_times(date(2025, 11, 11))) == 24
    assert len(snapshot_times(date(2026, 1, 15))) == 96
    assert snapshot_times(date(2025, 11, 11), which="morning") == [
        datetime(2025, 11, 11, 9, 30, tzinfo=ET)
    ]
    assert snapshot_times(date(2026, 1, 15), which="morning") == [
        datetime(2026, 1, 15, 9, 0, tzinfo=ET)
    ]
    assert snapshot_times(date(2025, 11, 11), which="last") == [
        datetime(2025, 11, 11, 23, 30, tzinfo=ET)
    ]


def test_a_pdf_that_is_not_a_report_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(ReportLayoutError):
        parse_report((FIXTURES / "espn_sign_in.html").read_bytes())


# ---------------------------------------------------------------------------
# every column, every kind of line
# ---------------------------------------------------------------------------


def test_every_column_of_an_out_line(morning: Report) -> None:
    line = line_for(morning, "Clarke, Brandon")
    assert line.game_date == date(2025, 11, 11)
    assert line.game_time == "07:30 (ET)"
    assert line.matchup == "MEM@NYK"
    assert line.team == "Memphis Grizzlies"
    assert line.status == "Out"
    assert line.reason == "Injury/Illness - Right Knee; Surgery Recovery"
    assert line.submitted


def test_a_questionable_line_keeps_the_leagues_own_words(morning: Report) -> None:
    line = line_for(morning, "Robinson, Mitchell")
    assert line.team == "New York Knicks"
    assert line.status == "Questionable"
    assert line.reason == "Injury/Illness - Left Ankle; Left Ankle Injury Management"


def test_an_available_line_is_a_report_line_not_an_absent_one(evening: Report) -> None:
    line = line_for(evening, "McCain, Jared")
    assert line.status == "Available"
    assert line.team == "Philadelphia 76ers"


def test_a_g_league_line(morning: Report) -> None:
    line = line_for(morning, "Hepburn, Chucky")
    assert line.status == "Out"
    assert line.reason == "G League - Two-Way"
    assert line_for(morning, "Traore, Nolan").reason == "G League - On Assignment"


def test_a_name_with_a_suffix_and_a_name_with_an_apostrophe(morning: Report) -> None:
    assert line_for(morning, "Pippen Jr., Scotty").status == "Out"
    assert line_for(morning, "Sharpe, Day'Ron").status == "Questionable"
    assert from_last_first("Pippen Jr., Scotty") == "Scotty Pippen Jr."
    assert name_key(from_last_first("Pippen Jr., Scotty")) == "scotty pippen"
    assert from_last_first("Sharpe, Day'Ron") == "Day'Ron Sharpe"
    assert from_last_first("Lawson, A.J.") == "A.J. Lawson"


def test_a_team_that_has_not_filed_is_stored_with_no_player(morning: Report) -> None:
    silent = [line for line in morning.lines if line.reason == NOT_SUBMITTED]
    assert len(silent) == 25
    for line in silent:
        assert line.player_name_raw == ""
        assert line.status == ""
        assert not line.submitted
    # Mostly the next day's games -- the report covers today and tomorrow --
    # but a late tip-off tonight can still be unfiled at half past nine.
    assert {line.game_date for line in silent} == {date(2025, 11, 11), date(2025, 11, 12)}
    assert {line.team for line in silent} >= {"San Antonio Spurs", "Portland Trail Blazers"}


def test_a_reason_wrapped_over_three_lines_stays_one_row(evening: Report) -> None:
    # `nbainjuries` splits this into three rows, two with no player name.
    line = line_for(evening, "McCain, Jared")
    assert line.reason == (
        "Injury/Illness - Right Thumb; Surgery Recovery - Splint; "
        "Left Knee Surgery Recovery - Brace"
    )


def test_a_reason_cut_by_a_page_break_is_rejoined_to_its_own_row(evening: Report) -> None:
    # Kessler is the last row on page 3; the word "Recovery" that finishes
    # his reason is the first thing printed on page 4, above Kevin Love.
    assert line_for(evening, "Kessler, Walker").reason == (
        "Injury/Illness - Left Shoulder; Injury Recovery"
    )
    assert line_for(evening, "Love, Kevin").reason == "Rest"
    assert evening.orphan_lines == 0


def test_the_whole_report_is_read(morning: Report, evening: Report) -> None:
    assert len(morning.lines) == 73
    assert len(evening.lines) == 128
    assert morning.orphan_lines == 0
    for report in (morning, evening):
        for line in report.lines:
            assert line.team
            assert line.status in STATUSES or line.status == ""
            # A status always comes with a player and vice versa.
            assert bool(line.status) == bool(line.player_name_raw)


def test_the_teams_are_placed_on_espns_pro_team_ids(morning: Report) -> None:
    for line in morning.lines:
        assert pro_team_id(line.team) is not None, line.team
    assert pro_team_id("Portland Trail Blazers") == pro_team_id("Blazers")
    assert pro_team_id("LA Clippers") == pro_team_id("Los Angeles Clippers")
    assert pro_team_id("Memphis Grizzlies") != pro_team_id("New York Knicks")
    assert pro_team_id("Sheffield Steelers") is None
