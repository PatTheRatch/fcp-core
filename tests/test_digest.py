"""The morning digest, over rows the real status pass wrote.

The load-bearing cases: an event reaches the section its player belongs to
and no other, a rival's business is not reported, and an event is repeated
until it has actually been delivered.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
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
    LEAGUE_EVENT_LIMIT,
    LEAGUE_MOVES_HOURS,
    LEAGUE_NEWS_CHARS,
    MAX_LINES,
    ROSTER_EVENT_LIMIT,
    TELEGRAM_LIMIT,
    WIRE_EVENT_LIMIT,
    Digest,
    Line,
    build_alert,
    build_digest,
    latest_listened_season,
    league_season_for,
    league_section,
    mark_notified,
)
from app.listener import events as kinds
from app.listener.pool import UNROSTERED_STATUSES
from app.listener.snapshots import latest_snapshots
from app.listener.status import next_pass_after, run_status_pass
from app.pickups.stream import stream_recommendations
from tests.fakes import attach_pool, fake_league, fake_pool_entry, fake_pro_game, fake_team
from tests.pickups_db import ANY, WEEK, clear_schedule, day_date, games
from tests.scoring_db import held
from tests.scoring_db import player as stored_player
from tests.test_pickups_stream import TEN_POINTS, build_week, free_agent, rostered

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


def test_the_churn_window_has_a_far_end_as_well_as_a_near_one(session: Session) -> None:
    """A trailing window that never closes reads the rest of the season.

    Live it cannot show, because nothing has been processed after now; on a
    replayed day, a backfill or a worker catching up a missed morning, the
    same query counts every claim the team went on to make. The rehearsal's
    day-80 digest said 83 adds in the last fortnight where 15 had been made.
    """
    _pass(session, _baseline(), FIRST)
    league_season = _stored(session)
    mine = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == MINE)
    ).one()
    player = session.scalars(select(Player).where(Player.espn_player_id == 300)).one()

    _transaction(session, mine, player, processed=LATER - timedelta(days=1), espn_id="behind-us")
    _transaction(session, mine, player, processed=LATER + timedelta(days=1), espn_id="tomorrow")
    _transaction(session, mine, player, processed=LATER + timedelta(days=40), espn_id="in-march")

    assert _digest(session).adds_recently == 1, "only the claim already made"


def test_the_leagues_wire_traffic_stops_at_the_moment_the_digest_is_built(
    session: Session,
) -> None:
    """The same open window, on the line every member sees.

    "609 moves on the wire in the last day" is the sort of number that costs
    the reader his trust in everything above it; on the day the rehearsal
    replayed, nine had been made.
    """
    _pass(session, _baseline(), FIRST)
    league_season = _stored(session)
    mine = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == MINE)
    ).one()
    player = session.scalars(select(Player).where(Player.espn_player_id == 300)).one()

    _transaction(session, mine, player, processed=LATER - timedelta(hours=2), espn_id="last-night")
    _transaction(
        session,
        mine,
        player,
        processed=LATER - timedelta(hours=LEAGUE_MOVES_HOURS + 2),
        espn_id="too-old",
    )
    _transaction(session, mine, player, processed=LATER + timedelta(hours=2), espn_id="tonight")

    lines = league_section(session, league_season, now=LATER)

    assert "  1 move on the wire in the last day" in lines
    assert lines[-1] == "  Through The Wire claimed Wire Guy for $3.", (
        "and the move itself, in the feed's own words"
    )


def test_the_leagues_news_is_bounded_by_characters_as_well_as_by_lines(
    session: Session,
) -> None:
    """A four-team trade names eight players and runs to nearly 200
    characters. Eight of those would put the whole message past Telegram's
    4096 on its own -- measured at 4392 on 2026's busiest day -- so the
    section spends a budget rather than a count, and says how many it left
    out."""
    _pass(session, _baseline(), FIRST)
    league_season = _stored(session)
    mine = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == MINE)
    ).one()
    for number in range(LEAGUE_EVENT_LIMIT + 4):
        who = Player(espn_player_id=9000 + number, name=f"A Very Long Player Name {number}")
        session.add(who)
        session.flush()
        _transaction(session, mine, who, processed=LATER, espn_id=f"move-{number}")

    lines = league_section(session, league_season, now=LATER)
    news = [line for line in lines if "claimed" in line]

    assert sum(len(line) for line in news) <= LEAGUE_NEWS_CHARS + 90, "the budget, plus one line"
    assert len(news) <= LEAGUE_EVENT_LIMIT
    assert lines[-1] == f"  and {LEAGUE_EVENT_LIMIT + 4 - len(news)} more"
    assert f"  {LEAGUE_EVENT_LIMIT + 4} moves on the wire in the last day" in lines


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


def _the_old_way(session: Session, league_season: LeagueSeason, espn_team_id: int) -> Digest:
    """The roster and wire sections as they were built before `changes()`.

    Kept here, once, so the rebuild can be held against it: the owner's
    message is the one surface a manager reads with no page in front of him,
    and it should not have moved by a character.
    """
    snapshots = latest_snapshots(session, league_season.season)
    names = {
        team.espn_team_id: team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }
    mine = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.on_team_id == espn_team_id
    }
    wire = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.status in UNROSTERED_STATUSES
    }
    roster: list[Line] = []
    on_the_wire: list[Line] = []
    reported: list[int] = []
    wanted = sorted({*digest_module.ROSTER_KINDS, *digest_module.WIRE_KINDS})
    for event in digest_module.unnotified(session, league_season.season, wanted):
        line = Line(event.kind, event.player.name, digest_module.describe(event, names))
        if event.player_id in mine and event.kind in digest_module.ROSTER_KINDS:
            roster.append(line)
        elif event.player_id in wire and event.kind in digest_module.WIRE_KINDS:
            on_the_wire.append(line)
        else:
            continue
        reported.append(event.id)
    standing, healthy = digest_module._standing([snapshots[player_id] for player_id in mine])
    return Digest(
        season=league_season.season,
        team_name=names.get(espn_team_id, f"team {espn_team_id}"),
        generated_at=LATER,
        roster=roster[:ROSTER_EVENT_LIMIT],
        roster_extra=max(0, len(roster) - ROSTER_EVENT_LIMIT),
        standing=standing,
        healthy=healthy,
        wire=on_the_wire[:WIRE_EVENT_LIMIT],
        wire_extra=max(0, len(on_the_wire) - WIRE_EVENT_LIMIT),
        # The two sections this helper is not about are built the same way
        # the real digest builds them, so the comparison below is of the
        # roster and the wire and of nothing else.
        today=digest_module.today_lines(session, league_season, espn_team_id, on=LATER.date()),
        plan=digest_module.week_plan(session, league_season, espn_team_id, on=LATER.date()),
        event_ids=reported,
    )


def test_the_rebuilt_digest_is_the_message_the_owner_always_had(session: Session) -> None:
    """The whole roster and wire sections now come from `changes()`. On a
    league with nothing but the listener's own events in it -- which is what
    the owner's morning is -- the message has to be the same one."""
    _pass(session, _baseline(), FIRST)
    changed = [
        fake_pool_entry(
            100,
            "Kawhi Leonard",
            on_team_id=MINE,
            injury_status="OUT",
            expected_return_date=(2026, 12, 1),
        ),
        fake_pool_entry(101, "Alperen Sengun", on_team_id=MINE, injury_status="QUESTIONABLE"),
        fake_pool_entry(200, "Rival Star", on_team_id=RIVAL, injury_status="OUT"),
        fake_pool_entry(201, "Rival Spare", percent_owned=30.0),
        fake_pool_entry(300, "Wire Guy", percent_owned=26.0, percent_change=9.0),
    ]
    _pass(session, changed, LATER)
    league_season = _stored(session)

    rebuilt = build_digest(session, league_season, MINE, now=LATER)
    before = _the_old_way(session, league_season, MINE)

    def by_player(lines: list[Line]) -> list[Line]:
        return sorted(lines, key=lambda line: (line.player, line.kind))

    assert by_player(rebuilt.roster) == by_player(before.roster)
    assert by_player(rebuilt.wire) == by_player(before.wire)
    assert (rebuilt.standing, rebuilt.healthy) == (before.standing, before.healthy)
    assert sorted(rebuilt.event_ids) == sorted(before.event_ids)
    assert sorted(rebuilt.render().splitlines()) == sorted(before.render().splitlines())
    assert rebuilt.roster and rebuilt.wire, "the fixture has news in both sections"

    # The one thing that did move, and deliberately: two events from the same
    # pass used to come out in the order the rows happened to be written in,
    # and now the worse news leads. A man ruled out is read before a man
    # downgraded.
    assert [line.kind for line in rebuilt.roster] == [kinds.WENT_OUT, kinds.DOWNGRADED]
    assert [line.kind for line in before.roster] == [kinds.DOWNGRADED, kinds.WENT_OUT]


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
    """The design's own test hook: a message nobody received stays unsent.

    The script builds its digest for the moment it runs in, and this
    module's rooms are dated in next season's November, so the event is
    moved to an hour ago first: the owner's window has a near end now as
    well as a far one, and an event observed six weeks from now is not news
    this morning (`OWNER_BACKLOG`).
    """
    _pass(session, _baseline(), FIRST)
    out = _baseline()
    out[0] = fake_pool_entry(100, "Kawhi Leonard", on_team_id=MINE, injury_status="OUT")
    _pass(session, out, LATER)
    for event in session.scalars(select(PlayerStatusEvent)).all():
        event.observed_at = datetime.now(UTC) - timedelta(hours=1)
    session.commit()

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "digest.py")],
        env={
            **os.environ,
            "DATABASE_URL": test_database_url,
            # Settings requires it, and the script runs outside the repo, so
            # no .env supplies it.
            "TEST_DATABASE_URL": test_database_url,
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
            # Settings requires it, and the script runs outside the repo, so
            # no .env supplies it.
            "TEST_DATABASE_URL": test_database_url,
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


def test_delivery_settings_come_from_the_env_file_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The VPS keeps its secrets in .env; a scheduled run reads no environment."""
    from app.config import Settings

    monkeypatch.delenv("FCP_DIGEST_URL", raising=False)
    monkeypatch.delenv("FCP_DIGEST_CHAT_ID", raising=False)
    blank = Settings(
        database_url="postgresql+psycopg://x/y",
        test_database_url="postgresql+psycopg://x/y_test",
        fcp_digest_url="   ",
        fcp_digest_chat_id="",
    )
    assert blank.fcp_digest_url is None and blank.fcp_digest_chat_id is None
    set_up = Settings(
        database_url="postgresql+psycopg://x/y",
        test_database_url="postgresql+psycopg://x/y_test",
        fcp_digest_url="https://api.telegram.org/botX/sendMessage",
        fcp_digest_chat_id="12345",
    )
    assert set_up.fcp_digest_chat_id == "12345"


# ---------------------------------------------------------------------------
# THIS WEEK: the streaming plan (docs/pickups.md section 5.2, item 3).
#
# The rooms come from the streaming tests rather than being rebuilt here, so
# the digest is proved against exactly the weeks `app.pickups.stream` is
# proved against and only the rendering is this module's own. `build_week`
# and friends are plain functions over `tests/pickups_db.py`, not fixtures.

MORNING = datetime.combine(day_date(5), time(9, 0), tzinfo=UTC)


def _week_section(rendered: str) -> list[str]:
    """The lines under THIS WEEK, up to the blank line before CHURN."""
    body = rendered.split("THIS WEEK\n", 1)[1]
    return body.split("\n\nCHURN")[0].splitlines()


def _today_section(rendered: str) -> list[str]:
    """The lines under TODAY, up to the blank line before THIS WEEK."""
    body = rendered.split("TODAY\n", 1)[1]
    return body.split("\n\nTHIS WEEK")[0].splitlines()


def _two_empty_slots_a_day(session: Session) -> tuple[LeagueSeason, Any, Any]:
    """One man plays and three do not, against a rival who plays every day.

    Two of the three lineup slots go empty on each remaining day, and two
    free agents can each fill one.
    """
    ls, home, away, first = build_week(session, bench=1)
    rostered(session, home, first, "Playing", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    for name in ("Dead One", "Dead Two", "Dead Three"):
        rostered(session, home, first, name, slots=ANY, pro_team=12, per_game=TEN_POINTS)
    rostered(session, away, first, "Rival", slots=ANY, pro_team=11, per_game=TEN_POINTS)
    free_agent(session, ls, "Streamer One", slots=ANY, pro_team=20, per_game=TEN_POINTS)
    free_agent(session, ls, "Streamer Two", slots=ANY, pro_team=21, per_game=TEN_POINTS)
    for pro_team, days in ((10, [5, 6, 7]), (11, [5, 6, 7]), (20, [5, 6, 7]), (21, [5, 6, 7])):
        games(session, pro_team, days)
    games(session, 12, [1, 2])
    return ls, home, away


def test_the_morning_digest_carries_the_days_plan(session: Session) -> None:
    clear_schedule(session)
    ls, _home, _away = _two_empty_slots_a_day(session)

    digest = build_digest(session, ls, 1, now=MORNING)
    rendered = digest.render()
    section = _week_section(rendered)

    assert section[0] == "  period 1, days 5-7 left (3), v Away"
    assert section[1] == ("  4.50 of 9 categories as things stand; adds this period: used 0 of 7")
    assert "  day 5: F, UT going empty" in section, "the days a slot goes begging"
    assert [line for line in section if line.startswith("  day ")] == [
        "  day 5: F, UT going empty",
        "  day 6: F, UT going empty",
        "  day 7: F, UT going empty",
    ]
    assert section[-2] == (
        "  worth a look: add Streamer One (3 of 3 games), drop Dead One (0 of 0)"
    )
    assert section[-1].strip() == (
        "week +0.50 + season -0.00/wk = net +0.50; record 4.5-4.5 without, 5.0-4.0 with"
    )

    # An extra section, not a replacement: the rest of the digest is untouched.
    assert "YOUR ROSTER" in rendered and "ON THE WIRE" in rendered
    assert rendered.endswith(f"0 adds in the last {CHURN_DAYS} days.")
    assert digest.event_ids == [], "the plan reports state; it marks no news"
    assert len(rendered.splitlines()) <= MAX_LINES


def test_a_plan_of_two_moves_is_numbered_in_the_order_to_make_them(session: Session) -> None:
    """The rendering of the multi-move branch, on a report built with the
    streaming tests' own category spreads.

    `build_digest` cannot pass those spreads, and the spreads this fixture
    measures for itself are degenerate (no prior season to measure), which
    makes every category a step function and a second move worth nothing.
    So the report is built here and only its rendering is under test.
    """
    clear_schedule(session)
    ls, _home, _away = _two_empty_slots_a_day(session)
    report = stream_recommendations(session, ls, 1, today=5, distributions=WEEK)
    assert len(report.recommended) == 2, "the room the streaming tests pin"

    section = digest_module._week_lines(report, "Away")

    assert section[-5] == "  worth a look, in this order:"
    numbered = [line for line in section if line.startswith(("  1. ", "  2. "))]
    assert len(numbered) == 2
    assert numbered[0].startswith("  1. add Streamer ")
    assert numbered[1].startswith("  2. add Streamer ")
    assert {line.split("add Streamer ")[1][0] for line in numbered} == {"O", "T"}, "both men"
    assert all("drop Dead" in line for line in numbered)
    judged = [line for line in section if line.strip().startswith("week ")]
    assert len(judged) == 2, "each move carries its own two horizons"
    assert all("= net +" in line and " without, " in line and " with" in line for line in judged)


def test_the_week_section_says_so_on_a_bye(session: Session) -> None:
    clear_schedule(session)
    ls, home, _away, first = build_week(session, bench=1, bye=True)
    rostered(session, home, first, "Playing", slots=ANY, pro_team=10, per_game=TEN_POINTS)
    games(session, 10, [5, 6, 7])

    section = _week_section(build_digest(session, ls, 1, now=MORNING).render())

    assert section == [
        "  period 1, days 5-7 left (3)",
        "  on a bye this period, so there is no week to plan for",
    ]


def test_a_week_that_cannot_be_built_costs_the_digest_nothing(session: Session) -> None:
    """The failure path, on the digest's own fixtures: the listener wrote a
    schedule and a roster for 2027 but no matchup periods, so there is no
    week to describe. One line, and every other section is what it was."""
    _pass(session, _baseline(), FIRST)

    digest = _digest(session)
    rendered = digest.render()

    assert _week_section(rendered) == [
        "  no plan today: scoring period 1 is in no matchup period of 2027"
    ]
    assert "Traceback" not in rendered
    assert digest.team_name == "Through The Wire"
    assert rendered.count("nothing new") == 2
    assert "All 2 active" in rendered
    assert rendered.endswith(f"0 adds in the last {CHURN_DAYS} days.")


def test_a_season_with_no_schedule_at_all_says_so(session: Session) -> None:
    clear_schedule(session)
    ls, _home, _away, _first = build_week(session, bench=1)

    section = _week_section(build_digest(session, ls, 1, now=MORNING).render())

    assert section == ["  no NBA schedule stored for 2026, so no plan today"]


def test_an_unexpected_failure_is_named_by_its_type_and_nothing_else(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception's text can carry a query or a connection string, so only
    its type is reported. The digest still goes out."""
    clear_schedule(session)
    ls, _home, _away = _two_empty_slots_a_day(session)

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("password=hunter2 while selecting from player_game_stats")

    monkeypatch.setattr(digest_module, "stream_recommendations", boom)
    rendered = build_digest(session, ls, 1, now=MORNING).render()

    assert _week_section(rendered) == [
        "  no plan today: the week could not be built (RuntimeError)"
    ]
    assert "hunter2" not in rendered and "player_game_stats" not in rendered
    assert rendered.endswith(f"0 adds in the last {CHURN_DAYS} days.")


def test_the_later_passes_alert_carries_no_plan(session: Session) -> None:
    """`build_alert` is what the report and late passes send. An add is not
    what a player being ruled out at 22:30 calls for."""
    _pass(session, _baseline(), FIRST)
    out = _baseline()
    out[0] = fake_pool_entry(100, "Kawhi Leonard", on_team_id=MINE, injury_status="OUT")
    _pass(session, out, LATER)

    alert = build_alert(session, _stored(session), MINE)

    assert alert is not None
    text, _ids = alert
    assert "THIS WEEK" not in text and "worth a look" not in text
    assert text.startswith("Kawhi Leonard: ")


def test_the_script_attempts_every_configured_channel_and_marks_nothing_when_all_fail(
    session: Session, test_database_url: str, tmp_path: Path
) -> None:
    """Both channels are tried, both are reported, and an event nobody
    received stays unsent.

    Both are pointed at a closed port on the loopback, so each refuses at
    once and no packet leaves the machine. What is under test is that the
    second channel is attempted after the first has failed, and that the
    log line names them both.
    """
    _pass(session, _baseline(), FIRST)
    out = _baseline()
    out[0] = fake_pool_entry(100, "Kawhi Leonard", on_team_id=MINE, injury_status="OUT")
    _pass(session, out, LATER)

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "digest.py")],
        env={
            **os.environ,
            "DATABASE_URL": test_database_url,
            "TEST_DATABASE_URL": test_database_url,
            "ESPN_LEAGUE_ID": str(LEAGUE_ID),
            "ESPN_SWID": "{x}",
            "ESPN_S2": "y",
            "FCP_TRACKED_TEAM_ID": str(MINE),
            "PYTHONPATH": str(REPO_ROOT),
            "ESPN_SEASON": "",
            "FCP_DIGEST_URL": "http://127.0.0.1:1/nothing-listens-here",
            "FCP_SMTP_HOST": "127.0.0.1",
            "FCP_SMTP_PORT": "1",
            "FCP_EMAIL_FROM": "fcp@example.net",
            "FCP_EMAIL_TO": "patrick@example.com",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=tmp_path,
    )

    assert completed.returncode == 1, "a failed channel is visible in the unit"
    assert "Sent" in completed.stdout
    assert "0 of 2 channel(s)" in completed.stdout
    assert "url FAILED: " in completed.stdout, "the push was attempted"
    assert "email FAILED: " in completed.stdout, "and so was the email, after it"
    assert "marked 0 event(s) notified" in completed.stdout

    # Nobody received it, so it is still to send.
    session.expire_all()
    [event] = session.scalars(select(PlayerStatusEvent).where(PlayerStatusEvent.kind == "went_out"))
    assert event.notified_at is None


# ---------------------------------------------------------------------------
# today: the day's lineup, above the week's plan
# ---------------------------------------------------------------------------


def test_the_morning_digest_opens_with_todays_lineup(session: Session) -> None:
    """The block, on the same fixture the week section is proved against.

    One man has a game on day 5 and three do not, so the lineup fills one of
    its three places and the digest says who to start and who cannot be
    started -- above the week, because it is what the manager does first.
    """
    clear_schedule(session)
    ls, _home, _away = _two_empty_slots_a_day(session)

    rendered = build_digest(session, ls, 1, now=MORNING).render()
    section = _today_section(rendered)

    assert section[0] == f"  day 5, {day_date(5):%a %d %b}: 1 of 3 places fillable"
    assert section[1] == "  start: Playing"
    assert section[-1] == "  sitting, no game: Dead One, Dead Two, Dead Three"
    assert "TODAY" in rendered.split("THIS WEEK")[0], "above the week, not below it"
    # An extra section, not a replacement.
    assert "YOUR ROSTER" in rendered and "ON THE WIRE" in rendered
    assert _week_section(rendered)[0] == "  period 1, days 5-7 left (3), v Away"
    assert len(rendered.splitlines()) <= MAX_LINES
    assert len(rendered) <= TELEGRAM_LIMIT


def test_the_digest_names_the_place_that_will_produce_nothing_tonight(
    session: Session,
) -> None:
    """The fix-this line: the team has set a man with no game and benched
    the one man who has one."""
    clear_schedule(session)
    ls, home, _away = _two_empty_slots_a_day(session)
    _, first = ls.matchup_periods[0], ls.matchup_periods[0]
    held(session, home, first, stored_player(session, "Dead One"), 5, slot="UT")
    held(session, home, first, stored_player(session, "Playing"), 5, slot="BE")
    session.flush()

    section = _today_section(build_digest(session, ls, 1, now=MORNING).render())

    assert section[0] == f"  day 5, {day_date(5):%a %d %b}: 1 of 3 places fillable, 0 set"
    assert section[1] == "  start: Playing"
    assert section[2] == "  fix: Dead One has no game at UT; Playing could take it"


def test_a_lineup_that_cannot_be_built_costs_the_digest_nothing(session: Session) -> None:
    """The same promise the week section makes: one line, and the rest of
    the message is untouched. An exception's text can carry a query or a
    connection string, so only its type is reported."""
    clear_schedule(session)
    ls, _home, _away = _two_empty_slots_a_day(session)

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("password=hunter2 while selecting from pro_team_games")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(digest_module, "today_lineup", boom)
    try:
        rendered = build_digest(session, ls, 1, now=MORNING).render()
    finally:
        monkeypatch.undo()

    assert _today_section(rendered) == ["  no lineup today: it could not be built (RuntimeError)"]
    assert "hunter2" not in rendered and "pro_team_games" not in rendered
    assert _week_section(rendered)[0] == "  period 1, days 5-7 left (3), v Away"
    assert rendered.endswith(f"0 adds in the last {CHURN_DAYS} days.")


def test_the_whole_message_fits_a_phone_with_a_crowded_day_on_top() -> None:
    """The block's own budget, against the worst day it can render: every
    cap full on every section at once."""
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
        today=[
            "  day 52, Thu 11 Dec: 10 of 10 places fillable, 8 set",
            "  start: " + ", ".join(f"Starter Number {i}" for i in range(10)),
            "  fix: UT is empty; Bench One, Bench Two could take it",
            "  fix: Resting Man has no game at PG; Bench Three could take it",
            "  and 3 more place(s) worth fixing",
            *[f"  Bench {i} has a game but no place in the lineup" for i in range(4)],
            "  sitting, no game: Idle One, Idle Two, Idle Three, Idle Four and 2 more",
        ],
        plan=[
            "  period 8, days 52-55 left (4), v Rival",
            "  4.50 of 9 categories as things stand; adds this period: used 0 of 7",
            "  day 52: C going empty",
            "  worth a look, in this order:",
            "  1. add Somebody (3 of 3 games), drop Nobody (0 of 0)",
            "    week +0.50 + season -0.00/wk = net +0.50; record 4.5-4.5 without, 5.0-4.0 with",
        ],
        adds_recently=4,
    )

    rendered = crowded.render()
    lines = rendered.splitlines()

    assert len(lines) <= MAX_LINES
    assert len(rendered) <= TELEGRAM_LIMIT, "one Telegram message, never two out of order"
    assert lines[-1].endswith("returned less per move.")
    assert "TODAY" in lines and "THIS WEEK" in lines
    assert lines.index("TODAY") < lines.index("THIS WEEK")
