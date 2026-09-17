"""What one `kona_player_info` entry says about a player right now.

The raw entry is the league's view of the player: `onTeamId` and `status`
sit at the top level, and everything about the person is under `player`.
The field names at the top level were confirmed by the S1 probe
(branch `scoring-s1`, docs/scoring/espn_projections.md, section 1); the
`ownership` block is confirmed by `espn-api`'s own parser and by
`scripts/price_scorecard.py`. `waiverProcessDate` is the one name still
taken on trust: `scripts/espn_probe.py --dump-card` prints an entry so it
can be checked against a player actually on waivers.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

ON_TEAM = "ONTEAM"
FREE_AGENT = "FREEAGENT"
WAIVERS = "WAIVERS"
UNROSTERED_STATUSES = (FREE_AGENT, WAIVERS)


@dataclass(frozen=True)
class PoolEntry:
    """One player as the pool fetch saw him. Everything optional is nullable."""

    espn_player_id: int
    name: str
    injury_status: str | None
    injured: bool
    expected_return_date: date | None
    pro_team_id: int | None
    on_team_id: int | None
    status: str | None
    percent_owned: float | None
    percent_change: float | None
    percent_started: float | None
    auction_value_average: float | None
    waiver_clears_at: datetime | None

    @property
    def rostered(self) -> bool:
        return self.status == ON_TEAM or bool(self.on_team_id)


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def epoch_ms(value: Any) -> datetime | None:
    """ESPN's timestamps are epoch milliseconds. Anything else is None."""
    number = _int(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000, tz=UTC)


def _return_date(value: Any) -> date | None:
    """`expectedReturnDate` is an array, [year, month, day, ...]."""
    if not isinstance(value, list | tuple) or len(value) < 3:
        return None
    try:
        return date(int(value[0]), int(value[1]), int(value[2]))
    except (TypeError, ValueError):
        return None


def parse_pool_entry(entry: dict[str, Any]) -> PoolEntry | None:
    """Read one raw entry. None when it carries no player id."""
    raw_player = entry.get("player")
    player: dict[str, Any] = raw_player if isinstance(raw_player, dict) else {}
    espn_player_id = _int(player.get("id", entry.get("id")))
    if espn_player_id is None:
        return None
    raw_ownership = player.get("ownership")
    ownership: dict[str, Any] = raw_ownership if isinstance(raw_ownership, dict) else {}
    name = str(player.get("fullName") or "").strip() or f"player {espn_player_id}"
    injury_status = player.get("injuryStatus")
    status = entry.get("status")
    return PoolEntry(
        espn_player_id=espn_player_id,
        name=name,
        injury_status=str(injury_status) if injury_status else None,
        injured=bool(player.get("injured", False)),
        expected_return_date=_return_date(player.get("expectedReturnDate")),
        pro_team_id=_int(player.get("proTeamId")),
        on_team_id=_int(entry.get("onTeamId")),
        status=str(status) if status else None,
        percent_owned=_float(ownership.get("percentOwned")),
        percent_change=_float(ownership.get("percentChange")),
        percent_started=_float(ownership.get("percentStarted")),
        auction_value_average=_float(ownership.get("auctionValueAverage")),
        waiver_clears_at=epoch_ms(entry.get("waiverProcessDate")),
    )


@dataclass(frozen=True)
class ScheduledGame:
    """One NBA game from the pro schedule, seen from one team's side."""

    pro_team_id: int
    scoring_period: int
    game_at: datetime
    opponent_pro_team_id: int
    home: bool


def parse_pro_schedule(schedule: dict[int, dict[str, list[dict[str, Any]]]]) -> list[ScheduledGame]:
    """Flatten ESPN's schedule (team -> day -> games) to one row per team-game.

    ESPN lists a game under both teams, which is what the table wants: one
    row per team per day. Only the first game on a day is taken, since a
    team plays at most once a day; a second entry has only ever been a
    duplicate. Team 0 is ESPN's placeholder for no team and is skipped.
    """
    games: list[ScheduledGame] = []
    for team_id, by_day in schedule.items():
        if team_id == 0:
            continue
        for day, listed in by_day.items():
            if not str(day).isdigit() or not listed:
                continue
            game = listed[0]
            when = epoch_ms(game.get("date"))
            home_id = _int(game.get("homeProTeamId"))
            away_id = _int(game.get("awayProTeamId"))
            if when is None or home_id is None or away_id is None:
                continue
            home = home_id == team_id
            games.append(
                ScheduledGame(
                    pro_team_id=team_id,
                    scoring_period=int(day),
                    game_at=when,
                    opponent_pro_team_id=away_id if home else home_id,
                    home=home,
                )
            )
    games.sort(key=lambda g: (g.pro_team_id, g.scoring_period))
    return games


@dataclass(frozen=True)
class NewsItem:
    published: datetime
    headline: str
    story: str


def parse_news(items: list[dict[str, Any]]) -> list[NewsItem]:
    """Read a news feed. `published` arrives as epoch milliseconds or ISO text."""
    parsed: list[NewsItem] = []
    for item in items:
        raw = item.get("published")
        when = epoch_ms(raw)
        if when is None and isinstance(raw, str) and raw:
            try:
                when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                when = None
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        headline = str(item.get("headline") or "").strip()
        if when is None or not headline:
            continue
        parsed.append(
            NewsItem(published=when, headline=headline, story=str(item.get("story") or ""))
        )
    return parsed
