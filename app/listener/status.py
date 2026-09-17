"""The status pass: snapshot the whole pool, diff it, fetch the news.

Runs three times a day in season (docs/pickups.md section 3.6) and once a
day otherwise. One pass is:

1. Rewrite the NBA schedule for the season from what ESPN sent with the
   league. No request: `espn-api` loads it with the league.
2. Fetch the whole player pool, paged. Four to five requests.
3. Write a status snapshot for every entry, and a free-agent row for every
   unrostered one.
4. Diff each player against his previous snapshot and write the events.
   Minutes events come from the box scores already stored, not the pool.
5. Fetch news for the players with a fresh event and for the tracked
   team's roster. One request each, so bounded.

Everything here takes a `Session` and fakes for the fetches, so the whole
pass is testable without ESPN. The script wraps it in `record_run` so a
silent listener shows up in `ingest_runs` like a silent ingest does.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from espn_api.basketball import League as ESPNLeague
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    FreeAgentSnapshot,
    League,
    LeagueSeason,
    Player,
    PlayerGameStat,
    PlayerNews,
    PlayerStatusEvent,
    PlayerStatusSnapshot,
    ProTeamGame,
)
from app.espn import (
    POOL_PAGE_SIZE,
    fetch_player_news,
    fetch_player_pool,
    pro_schedule,
)
from app.ingest import get_or_create_player, ingest_season_settings
from app.listener.events import Event, Observation, diff, minutes_events
from app.listener.pool import (
    PoolEntry,
    parse_news,
    parse_pool_entry,
    parse_pro_schedule,
)
from app.listener.snapshots import latest_snapshots

#: The passes, by label, at their UTC times. `morning` catches overnight
#: news and shootaround reports, `report` follows the 17:00 Eastern injury
#: report, `late` the game-time decisions for the early tips, and
#: `nightly` is the 09:00 ingest writing a snapshot of its own.
PASS_SCHEDULE: dict[str, time] = {
    "late": time(0, 30),
    "nightly": time(9, 0),
    "morning": time(15, 0),
    "report": time(22, 30),
}
ADHOC_LABEL = "adhoc"

#: How far a run may be from a scheduled slot and still carry its label.
#: The timer adds up to five minutes of jitter; an hour is generous.
LABEL_TOLERANCE = timedelta(hours=1)

#: No NBA game within this many days means the off-season, when status
#: moves slowly and one snapshot a day is plenty.
IN_SEASON_HORIZON = timedelta(days=14)

#: The ceiling on news requests in one pass. A few dozen is the expected
#: shape; this keeps a strange day from turning into hundreds.
MAX_NEWS_REQUESTS = 80

PoolFetch = Callable[..., list[dict[str, Any]]]
NewsFetch = Callable[[ESPNLeague, int], list[dict[str, Any]]]


@dataclass
class PassResult:
    """What one pass did, for the run record and the log."""

    label: str
    season: int
    observed_at: datetime
    in_season: bool
    #: Set when the pass decided not to snapshot, with the reason.
    skipped: str | None = None
    players: int = 0
    snapshots: int = 0
    free_agents: int = 0
    pro_games: int = 0
    events: int = 0
    events_by_kind: dict[str, int] = field(default_factory=dict)
    news_players: int = 0
    news_items: int = 0
    requests: int = 0

    def describe(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "label": self.label,
            "in_season": self.in_season,
            "requests": self.requests,
            "counts": {
                "players": self.players,
                "snapshots": self.snapshots,
                "free_agents": self.free_agents,
                "pro_games": self.pro_games,
                "events": self.events,
                "news_players": self.news_players,
                "news_items": self.news_items,
            },
            "events_by_kind": dict(self.events_by_kind),
        }
        if self.skipped:
            detail["skipped"] = self.skipped
        return detail


def label_for(now: datetime) -> str:
    """The pass label a run at `now` (UTC) carries, by the nearest slot."""
    best: tuple[timedelta, str] | None = None
    for label, slot in PASS_SCHEDULE.items():
        for day_offset in (-1, 0, 1):
            candidate = datetime.combine(now.date() + timedelta(days=day_offset), slot, tzinfo=UTC)
            gap = abs(now - candidate)
            if best is None or gap < best[0]:
                best = (gap, label)
    if best is None or best[0] > LABEL_TOLERANCE:
        return ADHOC_LABEL
    return best[1]


def next_pass_after(now: datetime) -> datetime:
    """When the next scheduled pass fires, so a waiver clearing before it is reported now."""
    candidates = [
        datetime.combine(now.date() + timedelta(days=day_offset), slot, tzinfo=UTC)
        for slot in PASS_SCHEDULE.values()
        for day_offset in (0, 1)
    ]
    return min(candidate for candidate in candidates if candidate > now)


def _league_season(session: Session, league: ESPNLeague) -> LeagueSeason:
    """The season row, written from the league's settings if it is not there yet.

    The nightly ingest normally keeps it current. Writing the settings here
    costs two requests and means the listener never loses a day to ordering.
    """
    stored = session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(
            League.espn_league_id == int(league.league_id),
            LeagueSeason.season == int(league.year),
        )
    )
    if stored is not None:
        return stored
    stored = ingest_season_settings(session, league)
    session.flush()
    return stored


def rewrite_pro_schedule(session: Session, league: ESPNLeague, season: int) -> int:
    """Replace the season's NBA schedule with what the league carries now."""
    games = parse_pro_schedule(pro_schedule(league))
    session.execute(delete(ProTeamGame).where(ProTeamGame.season == season))
    session.add_all(
        ProTeamGame(
            season=season,
            pro_team_id=g.pro_team_id,
            scoring_period=g.scoring_period,
            game_at=g.game_at,
            opponent_pro_team_id=g.opponent_pro_team_id,
            home=g.home,
        )
        for g in games
    )
    session.flush()
    return len(games)


def is_in_season(session: Session, season: int, now: datetime) -> bool:
    upcoming = session.scalar(
        select(func.count())
        .select_from(ProTeamGame)
        .where(
            ProTeamGame.season == season,
            ProTeamGame.game_at >= now,
            ProTeamGame.game_at < now + IN_SEASON_HORIZON,
        )
    )
    return bool(upcoming)


def snapshotted_on(session: Session, season: int, day: datetime) -> bool:
    """Whether any pass has already written this season's snapshot on `day` (UTC)."""
    start = datetime.combine(day.date(), time(0, 0), tzinfo=UTC)
    found = session.scalar(
        select(PlayerStatusSnapshot.id)
        .where(
            PlayerStatusSnapshot.season == season,
            PlayerStatusSnapshot.observed_at >= start,
            PlayerStatusSnapshot.observed_at < start + timedelta(days=1),
        )
        .limit(1)
    )
    return found is not None


def _players_for(session: Session, entries: Sequence[PoolEntry]) -> dict[int, Player]:
    """Our player row for every entry, created for anyone never seen before."""
    wanted = {entry.espn_player_id for entry in entries}
    existing = {
        player.espn_player_id: player
        for player in session.scalars(
            select(Player).where(Player.espn_player_id.in_(sorted(wanted)))
        ).all()
    }
    for entry in entries:
        if entry.espn_player_id not in existing:
            existing[entry.espn_player_id] = get_or_create_player(
                session, entry.espn_player_id, entry.name
            )
    session.flush()
    return existing


def _played_minutes(
    session: Session, season: int, player_ids: Sequence[int]
) -> dict[int, list[tuple[int, float]]]:
    """Played games with minutes this season, per player, oldest first."""
    rows = session.execute(
        select(PlayerGameStat.player_id, PlayerGameStat.scoring_period, PlayerGameStat.minutes)
        .where(
            PlayerGameStat.season == season,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes.is_not(None),
            PlayerGameStat.player_id.in_(sorted(player_ids)),
        )
        .order_by(PlayerGameStat.player_id, PlayerGameStat.scoring_period)
    ).all()
    by_player: dict[int, list[tuple[int, float]]] = {}
    for player_id, scoring_period, minutes in rows:
        by_player.setdefault(player_id, []).append((scoring_period, float(minutes)))
    return by_player


def _already_recorded(session: Session, player_id: int, season: int, event: Event) -> bool:
    """A minutes event is the same event until a new game moves the window."""
    through = event.detail.get("through_scoring_period")
    latest = session.scalar(
        select(PlayerStatusEvent)
        .where(
            PlayerStatusEvent.player_id == player_id,
            PlayerStatusEvent.season == season,
            PlayerStatusEvent.kind == event.kind,
        )
        .order_by(PlayerStatusEvent.observed_at.desc())
        .limit(1)
    )
    return latest is not None and latest.detail.get("through_scoring_period") == through


def _write_news(
    session: Session, league: ESPNLeague, player: Player, fetch: NewsFetch, seen_at: datetime
) -> int:
    items = parse_news(fetch(league, player.espn_player_id))
    written = 0
    for item in items:
        exists = session.scalar(
            select(PlayerNews.id).where(
                PlayerNews.player_id == player.id,
                PlayerNews.published == item.published,
                PlayerNews.headline == item.headline,
            )
        )
        if exists is not None:
            continue
        session.add(
            PlayerNews(
                player_id=player.id,
                published=item.published,
                headline=item.headline,
                story=item.story,
                source="espn",
                seen_at=seen_at,
            )
        )
        written += 1
    return written


def run_status_pass(
    session: Session,
    league: ESPNLeague,
    *,
    label: str,
    now: datetime | None = None,
    tracked_team_id: int | None = None,
    next_pass_at: datetime | None = None,
    force: bool = False,
    fetch_pool: PoolFetch = fetch_player_pool,
    fetch_news: NewsFetch = fetch_player_news,
) -> PassResult:
    """One pass over the league's player pool. Does not commit.

    Out of season (no NBA game in the next fortnight) a second pass on the
    same UTC day is skipped, and news is never fetched: a preseason status
    change matters for the draft, not three times a day. `force` overrides
    the skip, for a manual run.
    """
    observed_at = now or datetime.now(UTC)
    season = int(league.year)
    result = PassResult(label=label, season=season, observed_at=observed_at, in_season=False)

    league_season = _league_season(session, league)
    result.pro_games = rewrite_pro_schedule(session, league, season)
    result.in_season = is_in_season(session, season, observed_at)
    if not result.in_season and not force and snapshotted_on(session, season, observed_at):
        result.skipped = "off-season and already snapshotted today"
        return result

    scoring_period = int(
        getattr(league, "scoringPeriodId", None) or getattr(league, "current_week", None) or 0
    )
    raw_entries = fetch_pool(league, scoring_period=scoring_period or None)
    result.requests += len(raw_entries) // POOL_PAGE_SIZE + 1
    entries = [parsed for parsed in map(parse_pool_entry, raw_entries) if parsed is not None]
    players = _players_for(session, entries)
    previous = latest_snapshots(session, season)
    result.players = len(entries)

    with_events: dict[int, Player] = {}
    for entry in entries:
        player = players[entry.espn_player_id]
        current = Observation.from_entry(entry)
        session.add(
            PlayerStatusSnapshot(
                player_id=player.id,
                season=season,
                observed_at=observed_at,
                pass_label=label,
                injury_status=entry.injury_status,
                injured=entry.injured,
                expected_return_date=entry.expected_return_date,
                pro_team_id=entry.pro_team_id,
                on_team_id=entry.on_team_id,
                status=entry.status,
                percent_owned=entry.percent_owned,
                percent_change=entry.percent_change,
                percent_started=entry.percent_started,
                auction_value_average=entry.auction_value_average,
            )
        )
        result.snapshots += 1
        if not entry.rostered:
            session.add(
                FreeAgentSnapshot(
                    league_season_id=league_season.id,
                    observed_at=observed_at,
                    scoring_period=scoring_period,
                    player_id=player.id,
                    status=entry.status or "FREEAGENT",
                    waiver_clears_at=entry.waiver_clears_at,
                )
            )
            result.free_agents += 1

        before = previous.get(player.id)
        found = diff(
            Observation.from_snapshot(before) if before is not None else None,
            current,
            observed_at=observed_at,
            next_pass_at=next_pass_at,
        )
        for event in found:
            _record(session, result, player, season, observed_at, event)
            with_events[player.id] = player

    minutes = _played_minutes(session, season, [p.id for p in players.values()])
    for player in players.values():
        for event in minutes_events(minutes.get(player.id, [])):
            if _already_recorded(session, player.id, season, event):
                continue
            _record(session, result, player, season, observed_at, event)
            with_events[player.id] = player
    session.flush()

    if result.in_season:
        tracked = [
            players[entry.espn_player_id]
            for entry in entries
            if tracked_team_id is not None and entry.on_team_id == tracked_team_id
        ]
        wanted: dict[int, Player] = {p.id: p for p in tracked}
        for player_id, player in with_events.items():
            wanted.setdefault(player_id, player)
        for player in list(wanted.values())[:MAX_NEWS_REQUESTS]:
            result.news_items += _write_news(session, league, player, fetch_news, observed_at)
            result.news_players += 1
            result.requests += 1
    session.flush()
    return result


def _record(
    session: Session,
    result: PassResult,
    player: Player,
    season: int,
    observed_at: datetime,
    event: Event,
) -> None:
    session.add(
        PlayerStatusEvent(
            player_id=player.id,
            season=season,
            kind=event.kind,
            observed_at=observed_at,
            previous=event.previous,
            current=event.current,
            detail=event.detail,
        )
    )
    result.events += 1
    result.events_by_kind[event.kind] = result.events_by_kind.get(event.kind, 0) + 1
