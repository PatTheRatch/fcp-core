"""The morning digest, over rows the real status pass wrote.

The load-bearing cases: an event reaches the section its player belongs to
and no other, a rival's business is not reported, and an event is repeated
until it has actually been delivered.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app import digest as digest_module
from app.db.models import (
    LeagueSeason,
    Player,
    PlayerGameStat,
    PlayerStatusEvent,
    Team,
    Transaction,
    TransactionItem,
)
from app.db.session import make_engine, make_session_factory
from app.digest import (
    CHURN_DAYS,
    MAX_LINES,
    ROSTER_EVENT_LIMIT,
    WIRE_EVENT_LIMIT,
    Digest,
    Line,
    build_alert,
    build_digest,
    latest_listened_season,
    league_season_for,
    mark_notified,
)
from app.listener import events as kinds
from app.listener.status import next_pass_after, run_status_pass
from tests.fakes import attach_pool, fake_league, fake_pool_entry, fake_pro_game, fake_team

REPO_ROOT = Path(__file__).resolve().parent.parent

LEAGUE_ID = 3853870
SEASON = 2027
MINE = 3
RIVAL = 21
FIRST = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 3, 22, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")
    engine = make_engine(test_database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        session.execute(text("TRUNCATE leagues, owners, players RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


def _league(entries: list[dict[str, Any]]) -> Any:
    league = fake_league(league_id=LEAGUE_ID, season=SEASON)
    league.teams = [fake_team(MINE, "Through The Wire"), fake_team(RIVAL, "Load Management")]
    game_ms = int((FIRST + timedelta(days=1)).timestamp() * 1000)
    game = [fake_pro_game(13, 25, game_ms)]
    return attach_pool(league, entries, schedule={13: {"1": game}, 25: {"1": game}})


def _pass(session: Session, entries: list[dict[str, Any]], now: datetime) -> None:
    run_status_pass(
        session,
        _league(entries),
        label="morning",
        now=now,
        tracked_team_id=MINE,
        next_pass_at=next_pass_after(now),
    )
    session.commit()


def _baseline() -> list[dict[str, Any]]:
    return [
        fake_pool_entry(100, "Kawhi Leonard", on_team_id=MINE, percent_owned=99.0),
        fake_pool_entry(101, "Alperen Sengun", on_team_id=MINE, percent_owned=95.0),
        fake_pool_entry(200, "Rival Star", on_team_id=RIVAL, percent_owned=90.0),
        fake_pool_entry(201, "Rival Spare", on_team_id=RIVAL, percent_owned=30.0),
        fake_pool_entry(300, "Wire Guy", percent_owned=12.0, percent_change=1.0),
    ]


def _stored(session: Session) -> LeagueSeason:
    found = league_season_for(session, LEAGUE_ID, SEASON)
    assert found is not None
    return found


def _digest(session: Session, *, now: datetime = LATER) -> Digest:
    return build_digest(session, _stored(session), MINE, now=now)


def test_the_first_digest_has_no_events_but_still_says_where_the_roster_stands(
    session: Session,
) -> None:
    _pass(session, _baseline(), FIRST)

    digest = _digest(session)
    assert digest.roster == [] and digest.wire == []
    assert digest.event_ids == []
    assert digest.team_name == "Through The Wire"
    assert digest.season == SEASON
    assert (digest.standing, digest.healthy) == ([], 2)

    rendered = digest.render()
    assert "Through The Wire - Tue 03 Nov, 22:30 UTC - season 2027" in rendered
    assert rendered.count("nothing new") == 2
    assert "All 2 active" in rendered
    assert "Standing now" not in rendered
    assert rendered.endswith(f"0 adds in the last {CHURN_DAYS} days.")


def test_each_event_reaches_its_own_section_and_a_rivals_does_not(session: Session) -> None:
    _pass(session, _baseline(), FIRST)
    changed = [
        fake_pool_entry(
            100,
            "Kawhi Leonard",
            on_team_id=MINE,
            percent_owned=99.0,
            injury_status="OUT",
            injured=True,
            expected_return_date=(2026, 12, 1),
        ),
        fake_pool_entry(101, "Alperen Sengun", on_team_id=MINE, percent_owned=95.0),
        # A rival's player going out is his problem, not ours.
        fake_pool_entry(
            200, "Rival Star", on_team_id=RIVAL, percent_owned=90.0, injury_status="OUT"
        ),
        # Dropped by that rival, so he is on the wire now.
        fake_pool_entry(201, "Rival Spare", percent_owned=30.0),
        fake_pool_entry(300, "Wire Guy", percent_owned=26.0, percent_change=9.0),
    ]
    _pass(session, changed, LATER)

    digest = _digest(session)
    assert digest.roster == [Line(kinds.WENT_OUT, "Kawhi Leonard", "ACTIVE to OUT, back 01 Dec")]
    assert sorted(digest.wire, key=lambda line: line.player) == [
        Line(kinds.DROPPED, "Rival Spare", "by Load Management"),
        Line(kinds.OWNERSHIP_SURGE, "Wire Guy", "26.0% owned, +9 today"),
    ]
    assert (digest.roster_extra, digest.wire_extra) == (0, 0)

    # Kawhi is out, Sengun is not; the rival's OUT player is nowhere.
    assert digest.standing == ["OUT          Kawhi Leonard, back 01 Dec"]
    assert digest.healthy == 1

    rendered = digest.render()
    assert "  Standing now:" in rendered
    assert "  1 of 2 active" in rendered
    assert "out            Kawhi Leonard          ACTIVE to OUT, back 01 Dec" in rendered
    assert "dropped        Rival Spare            by Load Management" in rendered
    assert "Rival Star" not in rendered

    reported = session.scalars(
        select(PlayerStatusEvent).where(PlayerStatusEvent.id.in_(digest.event_ids))
    ).all()
    assert {e.player.name for e in reported} == {"Kawhi Leonard", "Rival Spare", "Wire Guy"}


def test_an_event_repeats_until_it_has_actually_been_delivered(session: Session) -> None:
    _pass(session, _baseline(), FIRST)
    out = _baseline()
    out[0] = fake_pool_entry(100, "Kawhi Leonard", on_team_id=MINE, injury_status="OUT")
    _pass(session, out, LATER)

    first = _digest(session)
    assert len(first.event_ids) == 1

    # Built again without marking: the same event, because nobody got it.
    assert _digest(session).event_ids == first.event_ids

    assert mark_notified(session, first.event_ids, LATER) == 1
    session.commit()
    assert _digest(session).roster == []
    [event] = session.scalars(select(PlayerStatusEvent)).all()
    assert event.notified_at == LATER
    assert mark_notified(session, [], LATER) == 0


def test_a_minutes_spike_on_my_own_player_is_roster_news_not_wire_news(session: Session) -> None:
    _pass(session, _baseline(), FIRST)
    mine = session.scalars(select(Player).where(Player.espn_player_id == 101)).one()
    theirs = session.scalars(select(Player).where(Player.espn_player_id == 300)).one()
    for player in (mine, theirs):
        for day, minutes in enumerate([18.0] * 10 + [30.0, 31.0, 32.0], start=1):
            session.add(
                PlayerGameStat(
                    player_id=player.id,
                    season=SEASON,
                    scoring_period=day,
                    played=True,
                    minutes=minutes,
                )
            )
    session.commit()
    _pass(session, _baseline(), LATER)

    digest = _digest(session)
    assert digest.roster == [
        Line(kinds.MINUTES_SPIKE, "Alperen Sengun", "31.0 min last 3, was 18.0")
    ]
    assert digest.wire == [Line(kinds.MINUTES_SPIKE, "Wire Guy", "31.0 min last 3, was 18.0")]


def _transaction(
    session: Session,
    team: Team,
    player: Player,
    *,
    processed: datetime,
    espn_id: str,
    status: str = "EXECUTED",
    item_type: str = "ADD",
    kind: str = "WAIVER",
) -> None:
    transaction = Transaction(
        league_season_id=team.league_season_id,
        espn_transaction_id=espn_id,
        team_id=team.id,
        type=kind,
        status=status,
        scoring_period=1,
        processed_at=processed,
        bid_amount=3,
    )
    session.add(transaction)
    session.flush()
    session.add(
        TransactionItem(
            transaction_id=transaction.id,
            player_id=player.id,
            item_type=item_type,
            to_team_id=team.id if item_type == "ADD" else None,
        )
    )
    session.commit()


def test_churn_counts_my_executed_adds_inside_the_window_only(session: Session) -> None:
    _pass(session, _baseline(), FIRST)
    league_season = _stored(session)
    mine = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == MINE)
    ).one()
    rival = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == RIVAL)
    ).one()
    player = session.scalars(select(Player).where(Player.espn_player_id == 300)).one()

    _transaction(session, mine, player, processed=LATER - timedelta(days=1), espn_id="in-window")
    _transaction(session, mine, player, processed=LATER - timedelta(days=20), espn_id="too-old")
    _transaction(session, rival, player, processed=LATER, espn_id="not-mine")
    _transaction(session, mine, player, processed=LATER, espn_id="lost", status="FAILED_BADBID")
    _transaction(session, mine, player, processed=LATER, espn_id="a-drop", item_type="DROP")
    _transaction(session, mine, player, processed=LATER, espn_id="a-trade", kind="TRADE_ACCEPT")

    assert _digest(session).adds_recently == 1


def test_the_message_stays_under_forty_lines_however_much_happened() -> None:
    crowded = Digest(
        season=SEASON,
        team_name="Through The Wire",
        generated_at=LATER,
        roster=[Line(kinds.WENT_OUT, f"Player {i}", "ACTIVE to OUT") for i in range(20)][
            :ROSTER_EVENT_LIMIT
        ],
        roster_extra=12,
        standing=[f"OUT          Player {i}" for i in range(20)],
        healthy=2,
        wire=[Line(kinds.DROPPED, f"Wire {i}", "by Load Management") for i in range(20)][
            :WIRE_EVENT_LIMIT
        ],
        wire_extra=10,
        adds_recently=4,
    )
    lines = crowded.render().splitlines()
    assert len(lines) <= MAX_LINES
    assert "  and 12 more, see /events" in lines
    assert "  and 10 more, see /events" in lines
    assert "  and 14 more carrying a status" in lines
    assert "  2 of 22 active" in lines
    assert lines[-1].endswith("returned less per move.")
    assert max(len(line) for line in lines) <= 90, "readable on a phone"
    assert "1 adds" not in crowded.render()


def test_an_alert_fires_only_for_my_own_player_being_ruled_out(session: Session) -> None:
    _pass(session, _baseline(), FIRST)
    assert build_alert(session, _stored(session), MINE) is None

    changed = _baseline()
    changed[2] = fake_pool_entry(200, "Rival Star", on_team_id=RIVAL, injury_status="OUT")
    changed[4] = fake_pool_entry(300, "Wire Guy", injury_status="OUT")
    _pass(session, changed, LATER)
    assert build_alert(session, _stored(session), MINE) is None, "neither player is mine"

    ours = _baseline()
    ours[0] = fake_pool_entry(
        100,
        "Kawhi Leonard",
        on_team_id=MINE,
        injury_status="OUT",
        expected_return_date=(2026, 12, 1),
    )
    _pass(session, ours, LATER + timedelta(hours=2))

    alert = build_alert(session, _stored(session), MINE)
    assert alert is not None
    message, event_ids = alert
    assert message == "Kawhi Leonard: ACTIVE to OUT, back 01 Dec"
    assert len(event_ids) == 1

    mark_notified(session, event_ids, LATER)
    session.commit()
    assert build_alert(session, _stored(session), MINE) is None


def test_the_season_comes_from_what_the_listener_wrote(session: Session) -> None:
    assert latest_listened_season(session) is None
    _pass(session, _baseline(), FIRST)
    assert latest_listened_season(session) == SEASON
    assert league_season_for(session, LEAGUE_ID, SEASON) is not None
    assert league_season_for(session, 999, SEASON) is None


def test_an_unknown_team_still_renders_rather_than_failing(session: Session) -> None:
    _pass(session, _baseline(), FIRST)
    digest = build_digest(session, _stored(session), 99, now=LATER)
    assert digest.team_name == "team 99"
    assert digest.healthy == 0 and digest.adds_recently == 0


@pytest.mark.parametrize(
    ("kind", "previous", "current", "detail", "expected"),
    [
        (
            kinds.RETURNED,
            {"injury_status": "OUT"},
            {"injury_status": "ACTIVE"},
            {},
            "OUT to ACTIVE",
        ),
        (
            kinds.RETURN_DATE_CHANGED,
            {"expected_return_date": "2026-12-01"},
            {"expected_return_date": "2026-12-15"},
            {"days": 14},
            "15 Dec, 14 days later",
        ),
        (
            kinds.RETURN_DATE_CHANGED,
            {"expected_return_date": "2026-12-15"},
            {"expected_return_date": "2026-12-01"},
            {"days": -14},
            "01 Dec, 14 days sooner",
        ),
        (
            kinds.CHANGED_PRO_TEAM,
            {"pro_team_id": 13},
            {"pro_team_id": 25},
            {},
            "NBA team 13 to 25",
        ),
        (
            kinds.WAIVER_CLEARING,
            {},
            {},
            {"clears_at": "2026-11-04T00:30:00+00:00"},
            "at 00:30 UTC",
        ),
        (kinds.CLAIMED, {}, {}, {"to_team_id": RIVAL}, "by Load Management"),
    ],
)
def test_every_kind_the_digest_shows_has_a_sentence(
    kind: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    detail: dict[str, Any],
    expected: str,
) -> None:
    event = PlayerStatusEvent(
        player_id=1,
        season=SEASON,
        kind=kind,
        observed_at=LATER,
        previous=previous,
        current=current,
        detail=detail,
    )
    assert digest_module.describe(event, {RIVAL: "Load Management"}) == expected


def test_the_script_prints_and_marks_nothing_without_a_delivery_url(
    session: Session, test_database_url: str, tmp_path: Path
) -> None:
    """The design's own test hook: a message nobody received stays unsent."""
    _pass(session, _baseline(), FIRST)
    out = _baseline()
    out[0] = fake_pool_entry(100, "Kawhi Leonard", on_team_id=MINE, injury_status="OUT")
    _pass(session, out, LATER)

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "digest.py")],
        env={
            **os.environ,
            "DATABASE_URL": test_database_url,
            "ESPN_LEAGUE_ID": str(LEAGUE_ID),
            "ESPN_SWID": "{x}",
            "ESPN_S2": "y",
            "FCP_TRACKED_TEAM_ID": str(MINE),
            "PYTHONPATH": str(REPO_ROOT),
        }
        | {"ESPN_SEASON": ""}
        | {"FCP_DIGEST_URL": ""},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Through The Wire" in completed.stdout
    assert "out            Kawhi Leonard" in completed.stdout
    assert "1 event(s) stay unnotified" in completed.stdout

    session.expire_all()
    [event] = session.scalars(select(PlayerStatusEvent)).all()
    assert event.notified_at is None, "nothing was delivered, so nothing is marked"


def test_the_script_refuses_without_a_tracked_team(
    session: Session, test_database_url: str, tmp_path: Path
) -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "digest.py")],
        env={
            **os.environ,
            "DATABASE_URL": test_database_url,
            "ESPN_LEAGUE_ID": str(LEAGUE_ID),
            "ESPN_SWID": "{x}",
            "ESPN_S2": "y",
            "PYTHONPATH": str(REPO_ROOT),
            "FCP_TRACKED_TEAM_ID": "",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=tmp_path,
    )
    assert completed.returncode == 1
    assert "FCP_TRACKED_TEAM_ID is not set" in completed.stderr
