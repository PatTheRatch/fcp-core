"""The rehearsal's own judgement, apart from the database it judges.

`scripts/rehearse_week.py` replays a played season through the real queue
and then says pass or fail on six checks. The replay itself needs a season,
worker processes and the better part of an hour; what is pinned here is
everything that decides *what the answer means* -- which days are replayed,
how the work is shared between workers, what counts as look-ahead in a
report, what counts as a digest that will not fit or will not help, and how
a rehearsal job is told apart from a real one. Those are the parts a wrong
answer would come from, and they are pure, so they are cheap to hold still.

The one impure thing tested here is `forbid_delivery`, because the whole
rehearsal rests on it: this machine has the SMTP settings set, so "nothing
is configured" would not have stopped a send.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app import channels, notify, reports
from scripts import rehearse_week as rehearsal

DAY = 80


def _move(espn_player_id: int) -> dict[str, Any]:
    return {"add": {"espn_player_id": espn_player_id}}


def _stream(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scoring_periods_remaining": [80, 81, 82, 83],
        "moves": [_move(11)],
        "recommended": [_move(11)],
        "empty_days": [{"scoring_period": 81, "fillers": [{"espn_player_id": 12}]}],
        "adds_used": 5,
        "adds_left": 2,
        "adds_budget": 7,
    }
    payload.update(changes)
    return payload


def _season(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "today": DAY,
        "best_add": {"into": [{"espn_player_id": 21}]},
        "best_swap": None,
        "best_two_swap": {"into": [{"espn_player_id": 22}]},
        "recommended": None,
        "adds_used": 5,
        "adds_left": 2,
        "adds_budget": 7,
    }
    payload.update(changes)
    return payload


# ---------------------------------------------------------------------------
# which days, and who does the work
# ---------------------------------------------------------------------------


def test_a_period_is_replayed_day_by_day_and_can_be_cut_short() -> None:
    """The default is the whole matchup period, in order. `--days` takes the
    first few of it, which is what makes a full-league morning affordable at
    fifty seconds a team; `--only-days` names them outright, because the
    edge days -- the All-Star break, the first playoff morning -- do not sit
    inside whichever period is being replayed in full."""
    assert rehearsal.chosen_days(77, 83) == [77, 78, 79, 80, 81, 82, 83]
    assert rehearsal.chosen_days(77, 83, limit=2) == [77, 78]
    assert rehearsal.chosen_days(77, 83, only=[140, 116, 116]) == [116, 140], "sorted, no repeats"
    assert rehearsal.chosen_days(77, 83, only=[116, 125], limit=1) == [116]


def test_the_worker_count_can_change_from_one_day_to_the_next() -> None:
    """One run has to time the same league morning at one worker and at
    three: building it twice under two `--workers` values would be a second
    thirteen minutes, and a second set of reports to tell apart. A cycled
    list gives day one to a single worker and day two to three of them."""
    assert rehearsal.worker_counts([77, 78], "1,3") == [1, 3]
    assert rehearsal.worker_counts([77, 78, 79], "3") == [3, 3, 3]
    assert rehearsal.worker_counts([1, 2, 3, 4, 5], "1,3") == [1, 3, 1, 3, 1]
    for refused in ("", "0", "2,0", "-1"):
        with pytest.raises(ValueError):
            rehearsal.worker_counts([1], refused)


def test_the_timing_table_is_the_middle_the_ninetieth_and_the_worst() -> None:
    """The table docs/inseason_rehearsal.md carries. The worst matters as
    much as the middle: a morning is only finished when its slowest team is,
    so a long tail is what would push the digest past the hour it is due."""
    assert rehearsal.summarise([]) == {"n": 0, "median": 0.0, "p90": 0.0, "max": 0.0}
    assert rehearsal.summarise([50.0]) == {"n": 1, "median": 50.0, "p90": 50.0, "max": 50.0}
    ten = [float(value) for value in range(1, 11)]
    assert rehearsal.summarise(ten) == {"n": 10, "median": 5.5, "p90": 9.0, "max": 10.0}


# ---------------------------------------------------------------------------
# telling a rehearsal job apart from a real one
# ---------------------------------------------------------------------------


def test_a_rehearsal_job_is_marked_in_its_label_and_in_its_payload() -> None:
    """Two marks, because they do two jobs. The label is the last part of
    `jobs.dedupe_key`, so one run's day cannot collide with another run's or
    with any real schedule, and enqueueing the same morning twice still adds
    nothing. The payload is what a worker filters on, and what makes the row
    obvious to a person reading `worker.py --list`."""
    label = rehearsal.rehearsal_label("teamB", 80)
    assert label == "rehearsal:teamB:080"
    assert label != rehearsal.rehearsal_label("leagueA", 80), "two runs never collide"
    assert label != rehearsal.rehearsal_label("teamB", 81), "two days never collide"

    body = rehearsal.rehearsal_payload("teamB", 80, date(2026, 1, 8))
    assert body["rehearsal"] is True and body["run"] == "teamB"
    assert body["today"] == "2026-01-08", "the day the handler builds, in the worker"
    assert "fault" not in body
    assert rehearsal.rehearsal_payload("teamB", 80, date(2026, 1, 8), fault=True)["fault"] is True


def test_delivery_is_taken_away_rather_than_left_unconfigured() -> None:
    """The one guard that cannot be allowed to be a setting.

    `FCP_SMTP_HOST` is set on the machine this runs on, so an unsent digest
    cannot rest on its being empty. Both senders are replaced outright, and a
    caller that reaches one raises."""
    was = (notify.deliver, channels.deliver)
    try:
        rehearsal.forbid_delivery()
        with pytest.raises(RuntimeError):
            notify.deliver(None, "text", title="t")  # type: ignore[arg-type]
        with pytest.raises(RuntimeError):
            channels.deliver([], "text", title="t", settings=None)  # type: ignore[arg-type]
    finally:
        notify.__dict__["deliver"], channels.__dict__["deliver"] = was


# ---------------------------------------------------------------------------
# check 1: look-ahead
# ---------------------------------------------------------------------------


def test_every_man_a_report_would_bring_in_is_looked_for() -> None:
    """A report proposes adds in four places, and a check that missed one
    would pass for the wrong reason: the week's ranked moves, the shortlist
    it recommends, the men who could fill an empty day, and -- on the season
    report -- whoever comes in on the free add, the swap or the two-for-two."""
    assert rehearsal.added_player_ids(reports.STREAM, _stream()) == {11, 12}
    assert rehearsal.added_player_ids(reports.SEASON, _season()) == {21, 22}
    assert rehearsal.added_player_ids(reports.STREAM, {}) == set()


def test_a_day_already_played_may_not_be_counted_as_still_to_come() -> None:
    """The plainest look-ahead there is: a week report built on the morning
    of day 80 that still counts day 79's games as games left would promise
    production that has already happened."""
    assert rehearsal.look_ahead_findings(reports.STREAM, _stream(), DAY, set()) == []
    behind = _stream(scoring_periods_remaining=[79, 80, 81])
    found = rehearsal.look_ahead_findings(reports.STREAM, behind, DAY, set())
    assert found and "already played" in found[0]
    empty = _stream(empty_days=[{"scoring_period": 78, "fillers": []}])
    assert "before day 80" in rehearsal.look_ahead_findings(reports.STREAM, empty, DAY, set())[0]
    wrong = _season(today=81)
    assert "not 80" in rehearsal.look_ahead_findings(reports.SEASON, wrong, DAY, set())[0]


def test_nobody_on_a_roster_that_morning_may_be_offered_as_an_add() -> None:
    """The wire a played season is reported on is reconstructed -- a man who
    played that day whom nobody had in a lineup that day -- so an add who
    *was* in somebody's lineup means the pool was built for the wrong day."""
    assert rehearsal.look_ahead_findings(reports.STREAM, _stream(), DAY, {99}) == []
    found = rehearsal.look_ahead_findings(reports.STREAM, _stream(), DAY, {11, 99})
    assert found == ["proposes adding [11], who were in a lineup on day 80"]


def test_the_adds_a_team_has_spent_may_only_be_the_ones_it_had_made() -> None:
    """The finding that made this check earn its keep.

    The period's budget is seven adds, and the report counts what the team
    spent over the whole period's days -- including the days after the
    morning it is built for. A team with two more adds coming later in the
    week reads as having none left today, and the plan says there is nothing
    to do. On the live season nothing has happened yet, so it never shows."""
    assert rehearsal.look_ahead_findings(reports.STREAM, _stream(), DAY, set(), 5) == []
    ahead = _stream(adds_used=7, adds_left=0)
    found = rehearsal.look_ahead_findings(reports.STREAM, ahead, DAY, set(), 5)
    assert found == ["adds_used is 7, but only 5 add(s) had been made by day 80 (adds_left 0 of 7)"]
    assert rehearsal.look_ahead_findings(reports.STREAM, ahead, DAY, set(), None) == [], (
        "not checked when the count could not be read"
    )


# ---------------------------------------------------------------------------
# check 3: the digest, and check 4: the store
# ---------------------------------------------------------------------------


def test_a_digest_is_flagged_when_it_will_not_fit_or_will_not_help() -> None:
    """Telegram refuses anything over 4096 characters, so a digest over it is
    not a long message but no message. The softer failures are a digest that
    renders empty, one that is a handful of lines on a day the league played,
    and one whose week section gave up -- each of which arrives looking like
    a working product and tells the reader nothing."""
    good = "\n".join(f"line {index}" for index in range(20))
    assert rehearsal.digest_findings("owner", good, games=True) == []
    over = "x" * (rehearsal.TEXT_LIMIT + 1)
    assert "over Telegram's 4096" in rehearsal.digest_findings("owner", over, games=True)[0]
    assert rehearsal.digest_findings("owner", "   \n\n", games=False) == ["owner: renders empty"]
    assert rehearsal.digest_findings("owner", "one\ntwo", games=True) == [
        "owner: only 2 lines on a day with games"
    ]
    assert rehearsal.digest_findings("owner", "one\ntwo", games=False) == [], (
        "a short message on a day with no NBA game is not a failure"
    )
    gave_up = good + "\n  no plan today: the week could not be built (KeyError)"
    assert "could not be built" in rehearsal.digest_findings("owner", gave_up, games=True)[0]


def test_a_page_counts_as_served_only_when_it_matches_the_row_that_was_stored() -> None:
    """The point of the precompute is that the page does not rebuild. Equal
    status codes are not enough: the answer has to be the stored payload,
    byte for byte, and the glance has to say so itself."""
    stored = {"stream": ({"a": 1}, None), "season": ({"b": 2}, None)}
    served = [
        {"route": "stream", "status": 200, "body": {"a": 1}, "stored_flag": None},
        {"route": "season", "status": 200, "body": {"b": 2}, "stored_flag": None},
        {"route": "glance", "status": 200, "body": {"stored": True}, "stored_flag": True},
    ]
    assert rehearsal.page_findings(served, stored) == []  # type: ignore[arg-type]

    rebuilt = [dict(page) for page in served]
    rebuilt[0]["body"] = {"a": 2}
    rebuilt[2]["stored_flag"] = False
    assert rehearsal.page_findings(rebuilt, stored) == [  # type: ignore[arg-type]
        "stream: the answer differs from the stored payload",
        "glance: answered live, not from the stored row",
    ]
    refused = [{"route": "stream", "status": 409, "body": None, "stored_flag": None}]
    assert rehearsal.page_findings(refused, stored) == ["stream: HTTP 409"]  # type: ignore[arg-type]
    assert rehearsal.page_findings(served[:1], {}) == [  # type: ignore[arg-type]
        "stream: nothing was stored for this day"
    ]


# ---------------------------------------------------------------------------
# check 5: no job taken twice
# ---------------------------------------------------------------------------


def test_a_job_in_two_workers_logs_is_a_job_that_ran_twice(tmp_path: Path) -> None:
    """`FOR UPDATE SKIP LOCKED` is what stops it, and the proof is kept
    outside the database: each worker writes the jobs it finished, and a job
    id that appears under two worker names is the failure this is for. A run
    with no log at all passes, because nothing ran twice."""
    log = tmp_path / "workers.jsonl"
    rows = [
        {"job": 1, "worker": "host:100"},
        {"job": 2, "worker": "host:101"},
        {"job": 3, "worker": "host:100"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n\n", encoding="utf-8")
    assert rehearsal.no_job_ran_twice(log) == (True, [])

    log.write_text(log.read_text() + json.dumps({"job": 2, "worker": "host:100"}) + "\n")
    ok, offending = rehearsal.no_job_ran_twice(log)
    assert not ok and offending == ["job 2 run by ['host:101', 'host:100']"]
    assert rehearsal.no_job_ran_twice(tmp_path / "never-written.jsonl") == (True, [])
