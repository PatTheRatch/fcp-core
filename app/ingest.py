"""Persist ESPN league structure into the canonical tables.

Read ESPN, write Postgres. Nothing here derives or computes anything: it is a
faithful record of how a league was configured for one season.

The unit of work is a *season*, never a league. Re-running for a season that
is already stored updates that row in place; running for a new season inserts
alongside it and leaves prior seasons untouched. That is what makes the
ingest safe to run every year, and on a schedule within a year.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from espn_api.basketball import League as ESPNLeague
from espn_api.basketball.constant import STATS_MAP
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    NON_STARTING_SLOTS,
    DailyLineupSlot,
    League,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Owner,
    Player,
    PlayerGameStat,
    RosterSlot,
    Team,
)


def _epoch_ms_to_datetime(epoch_ms: Any) -> datetime | None:
    """ESPN reports the trade deadline as epoch milliseconds; 0 means none."""
    if not isinstance(epoch_ms, int) or epoch_ms <= 0:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)


def _scoring_items(settings: Any) -> list[dict[str, Any]]:
    """The scored categories, in ESPN's own display order.

    `espn_api` exposes no parsed category list, so the raw settings dict is
    the only source for this.
    """
    raw = getattr(settings, "_raw_scoring_settings", None) or {}
    items = raw.get("scoringItems") or []
    return [item for item in items if isinstance(item, dict) and "statId" in item]


def _raw_snapshot(settings: Any) -> dict[str, Any]:
    """Everything ESPN sent that we do not model as a column yet."""
    division_map = getattr(settings, "division_map", None) or {}
    return {
        "scoring": getattr(settings, "_raw_scoring_settings", None) or {},
        "schedule": getattr(settings, "_raw_schedule_settings", None) or {},
        "matchup_periods": dict(getattr(settings, "matchup_periods", None) or {}),
        "division_map": {str(key): value for key, value in division_map.items()},
    }


def _get_or_create_league(session: Session, espn_league_id: int) -> League:
    league = session.scalar(select(League).where(League.espn_league_id == espn_league_id))
    if league is None:
        league = League(espn_league_id=espn_league_id)
        session.add(league)
        session.flush()  # assign league.id before the season row references it
    return league


def _sync_categories(league_season: LeagueSeason, items: list[dict[str, Any]]) -> None:
    """Make the stored categories match ESPN exactly, by stat id.

    Categories are reconciled rather than replaced so that a season whose
    scoring changed keeps stable row ids for the categories it kept.
    """
    existing = {category.stat_id: category for category in league_season.categories}
    seen: set[int] = set()

    for position, item in enumerate(items):
        stat_id = int(item["statId"])
        seen.add(stat_id)

        category = existing.get(stat_id)
        if category is None:
            category = LeagueSeasonCategory(stat_id=stat_id)
            league_season.categories.append(category)

        category.abbreviation = STATS_MAP.get(str(stat_id), f"STAT_{stat_id}")
        category.position = position
        category.is_reverse = bool(item.get("isReverseItem", False))

    for stat_id, category in existing.items():
        if stat_id not in seen:
            league_season.categories.remove(category)  # delete-orphan deletes the row


def ingest_league_structure(session: Session, espn_league: ESPNLeague) -> LeagueSeason:
    """Write one season of one league. Returns the stored row.

    Does not commit: the caller owns the transaction.
    """
    settings = espn_league.settings
    league = _get_or_create_league(session, int(espn_league.league_id))
    season = int(espn_league.year)

    league_season = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.league_id == league.id,
            LeagueSeason.season == season,
        )
    )
    if league_season is None:
        league_season = LeagueSeason(league_id=league.id, season=season)
        session.add(league_season)

    league_season.name = str(settings.name)
    league_season.scoring_type = str(settings.scoring_type)
    league_season.team_count = int(settings.team_count)
    league_season.regular_season_periods = int(settings.reg_season_count)
    league_season.total_matchup_periods = len(getattr(settings, "matchup_periods", None) or {})
    league_season.playoff_team_count = int(settings.playoff_team_count)
    league_season.playoff_matchup_period_length = int(settings.playoff_matchup_period_length)
    league_season.keeper_count = int(settings.keeper_count)
    league_season.uses_faab = bool(settings.faab)
    league_season.acquisition_budget = int(settings.acquisition_budget)
    league_season.median_scoring = bool(settings.median_scoring)
    league_season.trade_deadline = _epoch_ms_to_datetime(getattr(settings, "trade_deadline", None))
    league_season.raw_settings = _raw_snapshot(settings)
    league_season.ingested_at = datetime.now(UTC)

    _sync_categories(league_season, _scoring_items(settings))
    session.flush()
    return league_season


def _owner_key(raw_owner: dict[str, Any]) -> str | None:
    espn_owner_id = raw_owner.get("id")
    return str(espn_owner_id) if espn_owner_id else None


def _sync_owners(session: Session, team: Team, raw_owners: list[dict[str, Any]]) -> None:
    """Attach the team's owners, creating people we have not seen before.

    A team can have more than one owner, so this is a many-to-many rather
    than a column on the team.
    """
    owners: list[Owner] = []
    for raw_owner in raw_owners:
        key = _owner_key(raw_owner)
        if key is None:
            continue
        owner = session.scalar(select(Owner).where(Owner.espn_owner_id == key))
        if owner is None:
            owner = Owner(espn_owner_id=key)
            session.add(owner)
        owner.display_name = raw_owner.get("displayName")
        owner.first_name = raw_owner.get("firstName")
        owner.last_name = raw_owner.get("lastName")
        owners.append(owner)

    session.flush()
    team.owners = owners


def ingest_teams(session: Session, league_season: LeagueSeason, espn_league: ESPNLeague) -> None:
    """Write every team in the season. Existing teams are updated in place."""
    for espn_team in espn_league.teams:
        espn_team_id = int(espn_team.team_id)
        team = session.scalar(
            select(Team).where(
                Team.league_season_id == league_season.id,
                Team.espn_team_id == espn_team_id,
            )
        )
        if team is None:
            team = Team(league_season_id=league_season.id, espn_team_id=espn_team_id)
            session.add(team)

        team.name = str(espn_team.team_name)
        team.abbreviation = getattr(espn_team, "team_abbrev", None)
        team.logo_url = getattr(espn_team, "logo_url", None)
        team.division_id = getattr(espn_team, "division_id", None)
        team.division_name = getattr(espn_team, "division_name", None)
        team.standing = getattr(espn_team, "standing", None)
        team.final_standing = getattr(espn_team, "final_standing", None)
        team.categories_won = int(getattr(espn_team, "wins", 0) or 0)
        team.categories_lost = int(getattr(espn_team, "losses", 0) or 0)
        team.categories_tied = int(getattr(espn_team, "ties", 0) or 0)
        team.acquisitions = getattr(espn_team, "acquisitions", None)
        team.drops = getattr(espn_team, "drops", None)
        team.trades = getattr(espn_team, "trades", None)
        team.acquisition_budget_spent = getattr(espn_team, "acquisition_budget_spent", None)

        session.flush()
        _sync_owners(session, team, list(getattr(espn_team, "owners", None) or []))

    session.flush()


def _team_index(session: Session, league_season: LeagueSeason) -> dict[int, Team]:
    teams = session.scalars(select(Team).where(Team.league_season_id == league_season.id)).all()
    return {team.espn_team_id: team for team in teams}


def _espn_team_id(side: Any) -> int | None:
    """Box score sides are a Team, or the integer 0 when a team has a bye."""
    team_id = getattr(side, "team_id", side)
    try:
        team_id = int(team_id)
    except (TypeError, ValueError):
        return None
    return team_id or None


def _get_or_create_player(session: Session, espn_player: Any) -> Player | None:
    espn_player_id = getattr(espn_player, "playerId", None)
    if espn_player_id is None:
        return None
    espn_player_id = int(espn_player_id)

    player = session.scalar(select(Player).where(Player.espn_player_id == espn_player_id))
    if player is None:
        player = Player(espn_player_id=espn_player_id)
        session.add(player)
    player.name = str(getattr(espn_player, "name", "") or f"player {espn_player_id}")
    return player


def _sync_roster(session: Session, matchup: Matchup, team: Team, lineup: list[Any]) -> None:
    """Record who was on this team during this matchup period."""
    existing = {
        (slot.team_id, slot.player_id): slot
        for slot in matchup.roster_slots
        if slot.team_id == team.id
    }
    seen: set[tuple[int, int]] = set()

    for espn_player in lineup:
        player = _get_or_create_player(session, espn_player)
        if player is None:
            continue
        session.flush()  # assign player.id before it is used as a key

        key = (team.id, player.id)
        if key in seen:  # a player cannot occupy two slots on one roster
            continue
        seen.add(key)

        slot = existing.get(key)
        if slot is None:
            slot = RosterSlot(team_id=team.id, player_id=player.id)
            matchup.roster_slots.append(slot)

        slot.position = getattr(espn_player, "position", None)
        slot.pro_team = getattr(espn_player, "proTeam", None)
        slot.injured = bool(getattr(espn_player, "injured", False))
        slot.injury_status = getattr(espn_player, "injuryStatus", None)

    for key, slot in existing.items():
        if key not in seen:
            matchup.roster_slots.remove(slot)


def _category_by_abbreviation(
    session: Session, league_season: LeagueSeason
) -> dict[str, LeagueSeasonCategory]:
    """The season's scored categories, keyed by the abbreviation box scores use."""
    categories = session.scalars(
        select(LeagueSeasonCategory).where(
            LeagueSeasonCategory.league_season_id == league_season.id
        )
    ).all()
    return {category.abbreviation: category for category in categories}


def _sync_matchup_stats(
    matchup: Matchup,
    team: Team,
    stats: dict[str, Any],
    categories: dict[str, LeagueSeasonCategory],
) -> None:
    """Record every statistic one team posted in one matchup.

    Stores the component stats (FGM, FGA, FTM, FTA) alongside the scored
    categories, so a percentage can be recomputed instead of trusted.

    Whether a statistic is scored is decided by the season's category list,
    never by `result`: on a bye ESPN reports real values with a null result
    on every one of them.
    """
    existing = {
        (stat.team_id, stat.abbreviation): stat
        for stat in matchup.team_stats
        if stat.team_id == team.id
    }
    seen: set[tuple[int, str]] = set()

    for abbreviation, payload in (stats or {}).items():
        if not isinstance(payload, dict) or payload.get("value") is None:
            continue
        key = (team.id, str(abbreviation))
        seen.add(key)

        stat = existing.get(key)
        if stat is None:
            stat = MatchupTeamStat(team_id=team.id, abbreviation=str(abbreviation))
            matchup.team_stats.append(stat)

        stat.value = float(payload["value"])
        result = payload.get("result")
        stat.result = str(result) if result is not None else None
        category = categories.get(str(abbreviation))
        stat.league_season_category_id = category.id if category is not None else None

    for key, stat in existing.items():
        if key not in seen:
            matchup.team_stats.remove(stat)


def ingest_matchups_and_rosters(
    session: Session, league_season: LeagueSeason, espn_league: ESPNLeague
) -> None:
    """Write every matchup period, its matchups, the rosters and the stats.

    One ESPN call per matchup period. Box scores are used rather than
    `team.schedule` for two reasons: each matchup appears once instead of
    twice, and the lineups come back in the same response. Team schedules are
    also not a reliable enumeration, since their length varies by team.
    """
    teams = _team_index(session, league_season)
    categories = _category_by_abbreviation(session, league_season)
    period_numbers = sorted(
        int(period) for period in (getattr(espn_league.settings, "matchup_periods", None) or {})
    )

    for period_number in period_numbers:
        matchup_period = session.scalar(
            select(MatchupPeriod).where(
                MatchupPeriod.league_season_id == league_season.id,
                MatchupPeriod.period == period_number,
            )
        )
        if matchup_period is None:
            matchup_period = MatchupPeriod(league_season_id=league_season.id, period=period_number)
            session.add(matchup_period)

        matchup_period.is_playoff = period_number > league_season.regular_season_periods
        session.flush()

        for box in espn_league.box_scores(period_number):
            home_team = teams.get(_espn_team_id(box.home_team) or -1)
            if home_team is None:
                continue  # a side we have no team row for; nothing to hang it on
            away_team = teams.get(_espn_team_id(box.away_team) or -1)

            if matchup_period.final_scoring_period is None:
                scoring_period = getattr(box, "scoring_period", None)
                matchup_period.final_scoring_period = (
                    int(scoring_period) if scoring_period is not None else None
                )

            matchup = session.scalar(
                select(Matchup).where(
                    Matchup.matchup_period_id == matchup_period.id,
                    Matchup.home_team_id == home_team.id,
                )
            )
            if matchup is None:
                matchup = Matchup(matchup_period_id=matchup_period.id, home_team_id=home_team.id)
                session.add(matchup)

            matchup.away_team_id = away_team.id if away_team else None
            matchup.winner = str(getattr(box, "winner", "UNDECIDED") or "UNDECIDED")
            matchup.home_categories_won = int(getattr(box, "home_wins", 0) or 0)
            matchup.home_categories_lost = int(getattr(box, "away_wins", 0) or 0)
            matchup.categories_tied = int(getattr(box, "home_ties", 0) or 0)
            session.flush()

            _sync_roster(session, matchup, home_team, list(getattr(box, "home_lineup", None) or []))
            _sync_matchup_stats(
                matchup, home_team, getattr(box, "home_stats", None) or {}, categories
            )
            if away_team is not None:
                _sync_roster(
                    session, matchup, away_team, list(getattr(box, "away_lineup", None) or [])
                )
                _sync_matchup_stats(
                    matchup, away_team, getattr(box, "away_stats", None) or {}, categories
                )

    session.flush()


#: How many players to request from the player card endpoint at once. ESPN
#: served all 348 of a 14-team league in one call, but batching keeps the
#: request size bounded for larger leagues.
PLAYER_INFO_BATCH = 100

#: ESPN abbreviation -> column on PlayerGameStat. The rate stats ESPN also
#: returns (PPG, RPG, FG% and friends) are left to `raw_totals`: for a single
#: game they either duplicate a counting stat or divide by one.
_PLAYER_STAT_COLUMNS = {
    "MIN": "minutes",
    "PTS": "points",
    "REB": "rebounds",
    "OREB": "offensive_rebounds",
    "DREB": "defensive_rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "TO": "turnovers",
    "PF": "personal_fouls",
    "FGM": "field_goals_made",
    "FGA": "field_goals_attempted",
    "3PM": "three_pointers_made",
    "3PA": "three_pointers_attempted",
    "FTM": "free_throws_made",
    "FTA": "free_throws_attempted",
}


def _as_utc(value: Any) -> datetime | None:
    """ESPN sends game times with no offset. Read them as UTC."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _season_player_ids(session: Session, league_season: LeagueSeason) -> list[int]:
    """Every player who appeared on a roster in this season, in either grain.

    Both sources are needed. The weekly aggregate roster is nearly empty for
    seasons before 2025, so on its own it would scope player stats down to a
    fraction of the league and leave most daily lineups without a stat line
    to join against.
    """
    weekly = (
        select(Player.espn_player_id)
        .join(RosterSlot, RosterSlot.player_id == Player.id)
        .join(Team, Team.id == RosterSlot.team_id)
        .where(Team.league_season_id == league_season.id)
    )
    daily = (
        select(Player.espn_player_id)
        .join(DailyLineupSlot, DailyLineupSlot.player_id == Player.id)
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .where(Team.league_season_id == league_season.id)
    )
    return sorted(set(session.scalars(weekly).all()) | set(session.scalars(daily).all()))


def _scoring_period_entries(espn_player: Any) -> list[tuple[int, dict[str, Any]]]:
    """The per-scoring-period entries, dropping ESPN's season aggregates.

    Numeric keys are scoring periods. The rest ("2026_total",
    "2026_projected", "2026_last_7") are season rollups we can recompute.
    """
    entries: list[tuple[int, dict[str, Any]]] = []
    for key, payload in (getattr(espn_player, "stats", None) or {}).items():
        text = str(key)
        if not text.isdigit() or not isinstance(payload, dict):
            continue
        entries.append((int(text), payload))
    return entries


def ingest_player_stats(
    session: Session, league_season: LeagueSeason, espn_league: ESPNLeague
) -> int:
    """Write a box score line per player per scoring period. Returns rows written.

    Scoped to players who appeared on a roster this season, so ingesting a
    second league does not refetch the whole player universe.
    """
    espn_player_ids = _season_player_ids(session, league_season)
    players = {
        player.espn_player_id: player
        for player in session.scalars(
            select(Player).where(Player.espn_player_id.in_(espn_player_ids))
        ).all()
    }
    season = league_season.season
    written = 0

    for start in range(0, len(espn_player_ids), PLAYER_INFO_BATCH):
        batch = espn_player_ids[start : start + PLAYER_INFO_BATCH]
        fetched = espn_league.player_info(playerId=batch)
        if not isinstance(fetched, list):
            fetched = [fetched]

        for espn_player in fetched:
            if espn_player is None:
                continue
            player = players.get(int(getattr(espn_player, "playerId", 0) or 0))
            if player is None:
                continue

            for scoring_period, payload in _scoring_period_entries(espn_player):
                totals = payload.get("total") or {}
                row = session.scalar(
                    select(PlayerGameStat).where(
                        PlayerGameStat.player_id == player.id,
                        PlayerGameStat.season == season,
                        PlayerGameStat.scoring_period == scoring_period,
                    )
                )
                if row is None:
                    row = PlayerGameStat(
                        player_id=player.id, season=season, scoring_period=scoring_period
                    )
                    session.add(row)

                row.game_date = _as_utc(payload.get("date"))
                row.opponent = payload.get("team")
                row.played = bool(totals)
                row.raw_totals = dict(totals)
                for abbreviation, column in _PLAYER_STAT_COLUMNS.items():
                    raw = totals.get(abbreviation)
                    setattr(row, column, float(raw) if raw is not None else None)
                written += 1

        session.flush()

    return written


def _matchup_id_windows(espn_league: ESPNLeague) -> dict[int, list[int]]:
    """Matchup period -> every scoring period in it, from `League.matchup_ids`.

    Authoritative when present, but ESPN only populates the underlying
    `pointsByScoringPeriod` for recent seasons: in this league it exists for
    2025 onward and is empty for 2019 to 2024. `_discovered_daily_boxes`
    covers the rest.

    `settings.matchup_periods` is not a substitute. It claims one day per
    period and is wrong. Keys arrive as ints and values as strings in
    lexicographic order ("10" before "7"), so both are coerced and sorted.
    """
    raw = getattr(espn_league, "matchup_ids", None) or {}
    windows: dict[int, list[int]] = {}
    for period, days in raw.items():
        try:
            period_number = int(period)
        except (TypeError, ValueError):
            continue
        windows[period_number] = sorted(int(day) for day in days)
    return windows


#: Safety ceiling when walking days to discover period windows. A season runs
#: to about 174 scoring periods; the walk normally stops itself well before
#: this, as soon as it would advance past the last matchup period.
DAY_CEILING = 260


def _daily_lineup_call(espn_league: ESPNLeague, period: int, day: int) -> list[Any]:
    return list(
        espn_league.box_scores(matchup_period=period, scoring_period=day, matchup_total=False) or []
    )


def _has_lineups(boxes: list[Any]) -> bool:
    return any(
        getattr(box, "home_lineup", None) or getattr(box, "away_lineup", None) for box in boxes
    )


def _daily_boxes(
    espn_league: ESPNLeague,
    windows: dict[int, list[int]],
    max_period: int,
) -> Iterator[tuple[int, int, list[Any]]]:
    """Yield (matchup period, scoring period, box scores) for every day.

    With a known window, request each day under its own period. Without one,
    walk the days in order and discover the period as we go: ESPN populates
    the daily roster only when the requested day falls inside the requested
    matchup period, so an empty response means "try the next period".

    The response that identifies the period is the one yielded, so discovery
    costs no extra requests beyond one per period boundary. Verified against
    2025, where the real mapping exists: the probe reproduces it exactly.
    """
    if windows:
        for period in sorted(windows):
            for day in windows[period]:
                yield period, day, _daily_lineup_call(espn_league, period, day)
        return

    period = 1
    for day in range(1, DAY_CEILING + 1):
        for candidate in (period, period + 1):
            if candidate > max_period:
                return  # walked off the end of the season
            boxes = _daily_lineup_call(espn_league, candidate, day)
            if _has_lineups(boxes):
                period = candidate
                yield candidate, day, boxes
                break


def _lineup_sides(box: Any) -> list[tuple[int | None, list[Any]]]:
    """The (espn team id, lineup) pairs on a box score, skipping a bye's gap."""
    return [
        (_espn_team_id(box.home_team), list(getattr(box, "home_lineup", None) or [])),
        (_espn_team_id(box.away_team), list(getattr(box, "away_lineup", None) or [])),
    ]


def ingest_daily_lineups(
    session: Session, league_season: LeagueSeason, espn_league: ESPNLeague
) -> int:
    """Write where every player sat, for every team, on every day.

    One ESPN call per scoring period, requested with `matchup_total=False`
    so the response carries `rosterForCurrentScoringPeriod` and its real
    lineup slots. The default aggregate roster reports slot 0 for everyone
    and is useless for this.

    Runs before player stats, because the daily lineups are what reveal the
    full set of players a team held. The weekly aggregate roster is nearly
    empty for older seasons, so scoping player stats to it would miss most
    of the league.

    Returns the number of rows written.
    """
    teams = _team_index(session, league_season)
    periods = {
        period.period: period
        for period in session.scalars(
            select(MatchupPeriod).where(MatchupPeriod.league_season_id == league_season.id)
        ).all()
    }
    if not periods:
        return 0

    windows = _matchup_id_windows(espn_league)
    players = {player.espn_player_id: player for player in session.scalars(select(Player)).all()}
    observed: dict[int, list[int]] = {}
    written = 0

    for period_number, scoring_period, boxes in _daily_boxes(espn_league, windows, max(periods)):
        matchup_period = periods.get(period_number)
        if matchup_period is None:
            continue
        observed.setdefault(period_number, []).append(scoring_period)

        # Load the day's stored rows in one query rather than per player.
        existing = {
            (row.team_id, row.player_id): row
            for row in session.scalars(
                select(DailyLineupSlot).where(
                    DailyLineupSlot.matchup_period_id == matchup_period.id,
                    DailyLineupSlot.scoring_period == scoring_period,
                )
            ).all()
        }
        seen: set[tuple[int, int]] = set()

        # Collect the day's entries first, so every player new to us is
        # created in one flush rather than one flush per player.
        entries: list[tuple[Team, Any]] = []
        for box in boxes:
            for espn_team_id, lineup in _lineup_sides(box):
                team = teams.get(espn_team_id or -1)
                if team is None:
                    continue  # the empty side of a bye
                entries.extend((team, espn_player) for espn_player in lineup)

        for _, espn_player in entries:
            espn_player_id = getattr(espn_player, "playerId", None)
            if espn_player_id is not None and int(espn_player_id) not in players:
                player = _get_or_create_player(session, espn_player)
                if player is not None:
                    players[int(espn_player_id)] = player
        session.flush()

        for team, espn_player in entries:
            espn_player_id = getattr(espn_player, "playerId", None)
            player = players.get(int(espn_player_id)) if espn_player_id is not None else None
            if player is None:
                continue

            key = (team.id, player.id)
            if key in seen:
                continue
            seen.add(key)

            row = existing.get(key)
            if row is None:
                row = DailyLineupSlot(
                    team_id=team.id,
                    matchup_period_id=matchup_period.id,
                    scoring_period=scoring_period,
                    player_id=player.id,
                )
                session.add(row)

            slot = str(getattr(espn_player, "slot_position", "FA") or "FA")
            row.slot = slot
            row.started = slot not in NON_STARTING_SLOTS
            row.injured = bool(getattr(espn_player, "injured", False))
            row.injury_status = getattr(espn_player, "injuryStatus", None)
            written += 1

        for key, row in existing.items():
            if key not in seen:
                session.delete(row)

        session.flush()

    # Record the window each period turned out to cover, discovered or given.
    for period_number, seen_days in observed.items():
        period = periods[period_number]
        period.first_scoring_period = min(seen_days)
        period.final_scoring_period = max(seen_days)
    session.flush()

    return written


def ingest_season(session: Session, espn_league: ESPNLeague) -> LeagueSeason:
    """Write one whole season: structure, teams, play, player stats and lineups.

    The order is load-bearing. Teams need the season, matchups need the
    teams, and player stats come last because they are scoped to the players
    the roster tables reveal. Daily lineups must precede them: for seasons
    before 2025 the weekly aggregate roster is nearly empty, so the daily
    lineups are the only complete account of who a team held.
    """
    league_season = ingest_league_structure(session, espn_league)
    ingest_teams(session, league_season, espn_league)
    ingest_matchups_and_rosters(session, league_season, espn_league)
    ingest_daily_lineups(session, league_season, espn_league)
    ingest_player_stats(session, league_season, espn_league)
    return league_season
