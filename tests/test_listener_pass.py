"""The status pass, end to end against the test database with a fake ESPN.

What matters: every entry leaves a snapshot, the unrostered ones a
free-agent row, the second pass produces the right events and asks for
news only where it should, and the off-season rule holds.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    FreeAgentSnapshot,
    LeagueSeason,
    Player,
    PlayerGameStat,
    PlayerNews,
    PlayerStatusEvent,
    PlayerStatusSnapshot,
    ProTeamGame,
)
from app.db.session import make_engine, make_session_factory
from app.espn import fetch_player_pool
from app.ingest import (
    FULL_SCOPE,
    IngestScope,
    _season_player_ids,
    ingest_player_stats,
    ingest_season_settings,
    latest_free_agent_ids,
)
from app.listener import events
from app.listener.status import (
    ADHOC_LABEL,
    label_for,
    next_pass_after,
    run_status_pass,
)
from tests.fakes import (
    BOX_LINE,
    attach_pool,
    fake_card,
    fake_league,
    fake_pool_entry,
    fake_pro_game,
    fake_team,
    league_with_play,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SEASON = 2027
NOW = datetime(2026, 11, 3, 22, 30, tzinfo=UTC)
NEXT = datetime(2026, 11, 4, 0, 30, tzinfo=UTC)
TRACKED = 3


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
        session.execute(
            text(
                "TRUNCATE leagues, owners, players, ingest_runs, pro_team_games "
                "RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
        yield session
        session.rollback()


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _schedule(first_game: datetime) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """Team 13 hosts team 25 on three consecutive days from `first_game`."""
    games = {
        str(day): [fake_pro_game(13, 25, _ms(first_game + timedelta(days=day - 1)))]
        for day in (1, 2, 3)
    }
    return {13: games, 25: games, 0: {"1": []}}


def _entries(**overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Three players: one on the tracked team, one on a rival, one free agent."""
    base: dict[str, dict[str, Any]] = {
        "star": {"player_id": 100, "name": "Star", "on_team_id": TRACKED, "percent_owned": 99.0},
        "rival": {"player_id": 200, "name": "Rival Guy", "on_team_id": 21, "percent_owned": 80.0},
        "wire": {"player_id": 300, "name": "Wire Guy", "percent_owned": 12.0},
    }
    for key, extra in overrides.items():
        base[key] = {**base[key], **extra}
    return [fake_pool_entry(**spec) for spec in base.values()]


def _league(
    entries: list[dict[str, Any]],
    *,
    first_game: datetime = NOW + timedelta(days=1),
    news: dict[int, list[dict[str, Any]]] | None = None,
) -> Any:
    league = fake_league(season=SEASON)
    league.teams = [fake_team(TRACKED, "Through The Wire"), fake_team(21, "Load Management")]
    return attach_pool(league, entries, schedule=_schedule(first_game), news=news, scoring_period=7)


def _run(session: Session, league: Any, *, now: datetime = NOW, **kwargs: Any) -> Any:
    result = run_status_pass(
        session,
        league,
        label="report",
        now=now,
        tracked_team_id=TRACKED,
        next_pass_at=next_pass_after(now),
        **kwargs,
    )
    session.commit()
    return result


def _events(session: Session) -> list[PlayerStatusEvent]:
    return list(
        session.scalars(
            select(PlayerStatusEvent).order_by(PlayerStatusEvent.observed_at, PlayerStatusEvent.id)
        ).all()
    )


def test_a_first_pass_writes_a_baseline_and_no_events(session: Session) -> None:
    league = _league(_entries(), news={100: [{"published": _ms(NOW), "headline": "Fit"}]})
    result = _run(session, league)

    assert (result.players, result.snapshots, result.free_agents) == (3, 3, 1)
    assert result.events == 0 and result.skipped is None
    assert result.in_season is True
    assert result.pro_games == 6, "three days, both teams"
    assert result.requests == 2, "one pool page and one news request"

    stored = session.scalars(select(PlayerStatusSnapshot)).all()
    assert {s.pass_label for s in stored} == {"report"}
    assert {s.observed_at for s in stored} == {NOW}
    by_name = {s.player.name: s for s in stored}
    assert by_name["Star"].on_team_id == TRACKED and by_name["Star"].status == "ONTEAM"
    assert by_name["Wire Guy"].status == "FREEAGENT" and by_name["Wire Guy"].on_team_id == 0
    assert by_name["Wire Guy"].percent_owned == 12.0

    [free_agent] = session.scalars(select(FreeAgentSnapshot)).all()
    assert free_agent.player.name == "Wire Guy"
    assert free_agent.scoring_period == 7
    assert free_agent.league_season.season == SEASON

    assert session.scalar(select(func.count()).select_from(Player)) == 3
    assert league.news_requests == [100], "news for the tracked roster only"
    [news] = session.scalars(select(PlayerNews)).all()
    assert (news.player.name, news.headline, news.seen_at) == ("Star", "Fit", NOW)


def test_a_second_pass_reports_the_changes_and_asks_for_their_news(session: Session) -> None:
    _run(session, _league(_entries()))

    changed = _entries(
        wire={"injury_status": "OUT", "injured": True, "expected_return_date": (2026, 12, 1)},
        rival={
            "on_team_id": 0,
            "status": "WAIVERS",
            "waiver_process_date": _ms(NEXT - timedelta(minutes=30)),
        },
    )
    later = NOW + timedelta(hours=1)  # 23:30, so the next pass is 00:30
    league = _league(
        changed, news={300: [{"published": "2026-11-03T20:00:00Z", "headline": "Hurt"}]}
    )
    result = _run(session, league, now=later)

    kinds = sorted((e.player.name, e.kind) for e in _events(session))
    assert kinds == [
        ("Rival Guy", events.DROPPED),
        ("Rival Guy", events.WAIVER_CLEARING),
        ("Wire Guy", events.WENT_OUT),
    ]
    assert result.events_by_kind == {
        events.DROPPED: 1,
        events.WAIVER_CLEARING: 1,
        events.WENT_OUT: 1,
    }
    dropped = next(e for e in _events(session) if e.kind == events.DROPPED)
    assert dropped.detail == {"from_team_id": 21}
    assert dropped.observed_at == later

    # The tracked roster first, then the players with events, each once.
    assert league.news_requests == [100, 200, 300]
    assert result.news_players == 3
    [news] = session.scalars(select(PlayerNews)).all()
    assert (news.player.name, news.headline) == ("Wire Guy", "Hurt")
    assert news.published == datetime(2026, 11, 3, 20, tzinfo=UTC)

    # The waiver row carries when he clears.
    waivers = session.scalars(
        select(FreeAgentSnapshot).where(FreeAgentSnapshot.observed_at == later)
    ).all()
    assert {w.player.name: w.status for w in waivers} == {
        "Rival Guy": "WAIVERS",
        "Wire Guy": "FREEAGENT",
    }
    assert next(w for w in waivers if w.status == "WAIVERS").waiver_clears_at == (
        NEXT - timedelta(minutes=30)
    )


def test_the_same_news_is_not_stored_twice(session: Session) -> None:
    feed = {100: [{"published": _ms(NOW), "headline": "Fit", "story": "x"}]}
    _run(session, _league(_entries(), news=feed))
    _run(session, _league(_entries(), news=feed), now=NOW + timedelta(hours=2))
    assert session.scalar(select(func.count()).select_from(PlayerNews)) == 1


def _games(session: Session, player: Player, minutes: list[float]) -> None:
    for day, played in enumerate(minutes, start=1):
        session.add(
            PlayerGameStat(
                player_id=player.id, season=SEASON, scoring_period=day, played=True, minutes=played
            )
        )
    session.commit()


def test_a_minutes_spike_is_recorded_once_until_a_new_game_moves_it(session: Session) -> None:
    _run(session, _league(_entries()))
    wire = session.scalars(select(Player).where(Player.espn_player_id == 300)).one()
    _games(session, wire, [18.0] * 10 + [30.0, 31.0, 32.0])

    first = _run(session, _league(_entries()), now=NOW + timedelta(hours=2))
    assert first.events_by_kind == {events.MINUTES_SPIKE: 1}
    [spike] = _events(session)
    assert spike.detail["through_scoring_period"] == 13
    assert spike.detail["recent_mean"] == 31.0

    again = _run(session, _league(_entries()), now=NOW + timedelta(hours=4))
    assert again.events == 0, "the same spike, already on record"

    session.add(
        PlayerGameStat(
            player_id=wire.id, season=SEASON, scoring_period=14, played=True, minutes=33.0
        )
    )
    session.commit()
    moved = _run(session, _league(_entries()), now=NOW + timedelta(hours=6))
    assert moved.events_by_kind == {events.MINUTES_SPIKE: 1}
    assert _events(session)[-1].detail["through_scoring_period"] == 14


def test_off_season_snapshots_once_a_day_and_never_asks_for_news(session: Session) -> None:
    quiet = _league(_entries(), first_game=NOW + timedelta(days=30), news={100: [{"published": 1}]})
    first = _run(session, quiet)
    assert first.in_season is False
    assert first.snapshots == 3
    assert first.news_players == 0 and quiet.news_requests == []

    second = _run(session, quiet, now=NOW + timedelta(hours=1))
    assert second.skipped == "off-season and already snapshotted today"
    assert second.snapshots == 0
    assert session.scalar(select(func.count()).select_from(PlayerStatusSnapshot)) == 3

    forced = _run(session, quiet, now=NOW + timedelta(hours=1), force=True)
    assert forced.skipped is None and forced.snapshots == 3

    tomorrow = _run(session, quiet, now=NOW + timedelta(days=1))
    assert tomorrow.skipped is None and tomorrow.snapshots == 3


def test_the_schedule_is_rewritten_each_pass(session: Session) -> None:
    _run(session, _league(_entries()))
    moved = _league(_entries(), first_game=NOW + timedelta(days=2))
    _run(session, moved, now=NOW + timedelta(hours=2))

    games = session.scalars(select(ProTeamGame).where(ProTeamGame.pro_team_id == 13)).all()
    assert [g.scoring_period for g in games] == [1, 2, 3], "replaced, not duplicated"
    assert min(g.game_at for g in games) == NOW + timedelta(days=2)
    [home] = [g for g in games if g.scoring_period == 1]
    assert (home.home, home.opponent_pro_team_id) == (True, 25)
    away = session.scalars(
        select(ProTeamGame).where(ProTeamGame.pro_team_id == 25, ProTeamGame.scoring_period == 1)
    ).one()
    assert (away.home, away.opponent_pro_team_id) == (False, 13)


def test_the_season_row_is_written_when_the_ingest_has_not_made_it_yet(session: Session) -> None:
    assert session.scalar(select(func.count()).select_from(LeagueSeason)) == 0
    _run(session, _league(_entries()))
    stored = session.scalars(select(LeagueSeason)).one()
    assert stored.season == SEASON and len(stored.teams) == 2


def test_an_entry_without_a_player_id_is_skipped(session: Session) -> None:
    entries = [*_entries(), {"onTeamId": 0, "status": "FREEAGENT", "player": {"fullName": "?"}}]
    result = _run(session, _league(entries))
    assert result.players == 3


def test_the_pool_fetch_pages_until_a_short_page() -> None:
    league = attach_pool(
        fake_league(), [fake_pool_entry(i, f"p{i}", percent_owned=float(i)) for i in range(1, 601)]
    )
    found = fetch_player_pool(league, page_size=250, scoring_period=7)
    assert len(found) == 600
    assert [r["offset"] for r in league.pool_requests] == [0, 250, 500]
    assert found[0]["id"] == 600, "most owned first"

    league.pool_requests.clear()
    exact = attach_pool(fake_league(), [fake_pool_entry(i, f"p{i}") for i in range(1, 501)])
    assert len(fetch_player_pool(exact, page_size=250)) == 500
    assert len(exact.pool_requests) == 3, "two full pages, then the empty one that ends it"


def test_the_pool_fetch_can_be_narrowed_by_status() -> None:
    league = attach_pool(
        fake_league(),
        [fake_pool_entry(1, "a", on_team_id=3), fake_pool_entry(2, "b"), fake_pool_entry(3, "c")],
    )
    found = fetch_player_pool(league, statuses=("FREEAGENT", "WAIVERS"))
    assert sorted(e["id"] for e in found) == [2, 3]
    assert league.pool_requests[0]["filterStatus"] == {"value": ["FREEAGENT", "WAIVERS"]}


def test_a_recent_ingest_widens_player_stats_to_the_wire(session: Session) -> None:
    """The listener saw a free agent; the next scheduled ingest fetches his box scores."""
    _run(session, _league(_entries()))
    league_season = session.scalars(select(LeagueSeason)).one()
    assert latest_free_agent_ids(session, league_season) == [300]
    assert _season_player_ids(session, league_season) == []
    assert _season_player_ids(session, league_season, include_free_agents=True) == [300]

    espn = league_with_play(
        season=SEASON,
        teams=[fake_team(TRACKED, "x")],
        boxes={},
        cards={300: fake_card(300, "Wire Guy", {5: BOX_LINE}, season=SEASON)},
    )
    ingest_player_stats(session, league_season, espn, FULL_SCOPE)
    session.commit()
    assert session.scalar(select(func.count()).select_from(PlayerGameStat)) == 0, (
        "a full pass stays on the rostered players"
    )

    ingest_player_stats(session, league_season, espn, IngestScope(frozenset({1}), frozenset({5})))
    session.commit()
    [line] = session.scalars(select(PlayerGameStat)).all()
    assert (line.player.espn_player_id, line.scoring_period) == (300, 5)


def test_the_status_pass_needs_no_ingest_first_but_reuses_its_season(session: Session) -> None:
    league = _league(_entries())
    ingest_season_settings(session, league)
    session.commit()
    _run(session, league)
    assert session.scalar(select(func.count()).select_from(LeagueSeason)) == 1


@pytest.mark.parametrize(
    ("now", "label"),
    [
        (datetime(2026, 11, 3, 15, 4, tzinfo=UTC), "morning"),
        (datetime(2026, 11, 3, 22, 33, tzinfo=UTC), "report"),
        (datetime(2026, 11, 4, 0, 31, tzinfo=UTC), "late"),
        (datetime(2026, 11, 3, 23, 59, tzinfo=UTC), "late"),  # nearest slot is past midnight
        (datetime(2026, 11, 3, 9, 2, tzinfo=UTC), "nightly"),
        (datetime(2026, 11, 3, 12, 0, tzinfo=UTC), ADHOC_LABEL),
    ],
)
def test_a_run_is_labelled_by_the_nearest_slot(now: datetime, label: str) -> None:
    assert label_for(now) == label


def test_the_next_pass_is_the_next_slot_even_across_midnight() -> None:
    assert next_pass_after(datetime(2026, 11, 3, 22, 30, tzinfo=UTC)) == NEXT
    assert next_pass_after(datetime(2026, 11, 3, 22, 29, tzinfo=UTC)) == datetime(
        2026, 11, 3, 22, 30, tzinfo=UTC
    )
    assert next_pass_after(NEXT) == datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
