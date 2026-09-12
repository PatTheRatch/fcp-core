"""Fake ESPN objects for tests.

Stand-ins for `espn_api` classes, carrying only the attributes the ingest
reads. They live here rather than in a test module so both the ingest and
the API tests can build the same shapes.
"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

#: A plausible single-game box score line, including a rate stat that the
#: ingest must leave in `raw_totals` rather than promote to a column.
BOX_LINE = {
    "PTS": 22.0,
    "REB": 9.0,
    "OREB": 2.0,
    "DREB": 7.0,
    "AST": 5.0,
    "STL": 1.0,
    "BLK": 2.0,
    "TO": 3.0,
    "PF": 4.0,
    "MIN": 35.0,
    "FGM": 8.0,
    "FGA": 17.0,
    "3PM": 2.0,
    "3PA": 5.0,
    "FTM": 4.0,
    "FTA": 4.0,
    "PPG": 22.0,
    "FG%": 0.47058824,
}

#: The real league's nine categories, in the order ESPN returns them.
NINE_CAT_STAT_IDS = [20, 6, 11, 0, 1, 17, 2, 3, 19]

# 2026-02-20T12:00:00Z, the shape ESPN uses for the trade deadline.
TRADE_DEADLINE_EPOCH_MS = 1771588800000


def _scoring_items(stat_ids: list[int]) -> list[dict[str, Any]]:
    return [{"statId": stat_id, "isReverseItem": False, "points": 1.0} for stat_id in stat_ids]


def fake_league(
    *,
    league_id: int = 3853870,
    season: int = 2026,
    name: str = "Patriot Games",
    team_count: int = 14,
    stat_ids: list[int] | None = None,
    reg_season_count: int = 19,
    matchup_period_count: int = 22,
    trade_deadline: int | None = TRADE_DEADLINE_EPOCH_MS,
) -> Any:
    """A stand-in for `espn_api.basketball.League`, carrying only what ingest reads."""
    settings = SimpleNamespace(
        name=name,
        scoring_type="H2H_CATEGORY",
        team_count=team_count,
        reg_season_count=reg_season_count,
        playoff_team_count=7,
        playoff_matchup_period_length=1,
        keeper_count=0,
        faab=True,
        acquisition_budget=100,
        median_scoring=False,
        trade_deadline=trade_deadline,
        division_map={1: "UK"},
        matchup_periods={str(i): [i] for i in range(1, matchup_period_count + 1)},
    )
    settings._raw_scoring_settings = {
        "scoringItems": _scoring_items(stat_ids if stat_ids is not None else NINE_CAT_STAT_IDS)
    }
    settings._raw_schedule_settings = {"matchupPeriodCount": reg_season_count}
    return SimpleNamespace(league_id=league_id, year=season, settings=settings)


def fake_player(
    player_id: int,
    name: str,
    *,
    position: str = "PG",
    pro_team: str = "LAL",
    injured: bool = False,
    injury_status: str = "ACTIVE",
    slot: str = "PG",
) -> Any:
    return SimpleNamespace(
        playerId=player_id,
        name=name,
        position=position,
        proTeam=pro_team,
        injured=injured,
        injuryStatus=injury_status,
        # Always "PG" on the aggregate roster, which is why ingest ignores it.
        lineupSlot="PG",
        # The real daily slot, from rosterForCurrentScoringPeriod.
        slot_position=slot,
    )


def fake_team(
    team_id: int,
    name: str,
    *,
    owners: list[dict[str, Any]] | None = None,
    categories: tuple[int, int, int] = (95, 76, 0),
    standing: int = 1,
) -> Any:
    return SimpleNamespace(
        team_id=team_id,
        team_name=name,
        team_abbrev=name[:4].upper(),
        logo_url=f"https://example.test/{team_id}.png",
        division_id=1,
        division_name="UK",
        standing=standing,
        final_standing=standing,
        wins=categories[0],
        losses=categories[1],
        ties=categories[2],
        owners=owners if owners is not None else [owner_dict(f"owner-{team_id}")],
        acquisitions=113,
        drops=113,
        trades=4,
        acquisition_budget_spent=50,
    )


def owner_dict(guid: str, first: str = "Pat", last: str = "M") -> dict[str, Any]:
    return {
        "id": guid,
        "displayName": f"{first.lower()}{last.lower()}",
        "firstName": first,
        "lastName": last,
    }


def fake_box(
    home: Any,
    away: Any,
    *,
    winner: str = "HOME",
    home_wins: int = 5,
    away_wins: int = 4,
    ties: int = 0,
    scoring_period: int = 6,
    home_lineup: list[Any] | None = None,
    away_lineup: list[Any] | None = None,
    home_stats: dict[str, Any] | None = None,
    away_stats: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(
        home_team=home,
        away_team=away,
        winner=winner,
        home_wins=home_wins,
        away_wins=away_wins,
        home_ties=ties,
        scoring_period=scoring_period,
        home_lineup=home_lineup or [],
        away_lineup=away_lineup or [],
        home_stats=home_stats if home_stats is not None else {},
        away_stats=away_stats if away_stats is not None else {},
    )


def stat_block(
    *, pts: float = 503.0, result: str | None = "WIN", fgm: float = 182.0, fga: float = 398.0
) -> dict[str, Any]:
    """A scored category, a percentage, and the components behind it."""
    return {
        "PTS": {"value": pts, "result": result},
        "FG%": {"value": round(fgm / fga, 8), "result": result},
        "FGM": {"value": fgm, "result": None},
        "FGA": {"value": fga, "result": None},
    }


def fake_card(
    player_id: int,
    name: str,
    periods: dict[int, dict[str, Any] | None],
    *,
    season: int = 2026,
    projected: dict[str, Any] | None = None,
    total: dict[str, Any] | None = None,
) -> Any:
    """A player card: scoring period -> stat line, or None for a day not played.

    Also carries the season rollups ESPN mixes into the same dict, which the
    ingest has to ignore.
    """
    stats: dict[str, Any] = {
        f"{season}_total": {
            "total": total if total is not None else {"PTS": 1.0},
            "date": None,
            "team": None,
        },
        f"{season}_projected": {
            "total": projected if projected is not None else {"PTS": 2.0},
            "date": None,
            "team": None,
        },
    }
    for period, line in periods.items():
        stats[str(period)] = {
            "total": line or {},
            "date": datetime(2025, 10, 23, 0, 30) if line is not None else datetime(2025, 10, 24),
            "team": "TOR",
        }
    return SimpleNamespace(playerId=player_id, name=name, stats=stats)


def league_with_play(
    *,
    season: int = 2026,
    teams: list[Any],
    boxes: dict[int, list[Any]],
    reg_season_count: int = 2,
    matchup_period_count: int = 3,
    cards: dict[int, Any] | None = None,
) -> Any:
    """A fake league that also answers `box_scores(period)` and `player_info`."""
    league = fake_league(
        season=season,
        reg_season_count=reg_season_count,
        matchup_period_count=matchup_period_count,
    )
    league.teams = teams
    league.box_scores = lambda period: boxes.get(period, [])
    by_id = cards or {}
    league.player_info = lambda playerId: [  # noqa: N803  (ESPN's own parameter name)
        by_id[i] for i in playerId if i in by_id
    ]
    league.matchup_ids = {}
    league.box_scores = lambda matchup_period=None, scoring_period=None, matchup_total=True: (
        boxes.get(matchup_period, [])
    )
    attach_transactions(league, {})
    return league


def fake_transaction(
    transaction_id: str,
    *,
    team_id: int,
    type_: str = "WAIVER",
    status: str = "EXECUTED",
    bid: int | None = 0,
    processed: int | None = 1769598004347,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One raw ESPN transaction, in the shape the endpoint really returns."""
    payload: dict[str, Any] = {
        "id": transaction_id,
        "teamId": team_id,
        "type": type_,
        "status": status,
        "scoringPeriodId": 0,
        "bidAmount": bid,
        "processDate": processed,
    }
    if items is not None:
        payload["items"] = items
    return payload


def tx_item(
    player_id: int, item_type: str, *, from_team: int = 0, to_team: int = 0
) -> dict[str, Any]:
    """A transaction item. Team 0 is ESPN's way of saying free agency."""
    return {
        "playerId": player_id,
        "type": item_type,
        "fromTeamId": from_team,
        "toTeamId": to_team,
    }


def attach_transactions(
    league: Any,
    by_day: dict[int, list[dict[str, Any]]],
    names: dict[int, str] | None = None,
) -> Any:
    """Give a fake league the request layer the transaction fetch uses.

    The real code calls `league.espn_request.league_get`, so the fake answers
    at the same level rather than stubbing the parsing above it.
    """

    def league_get(
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        extend: str = "",
    ) -> dict[str, Any]:
        day = int((params or {}).get("scoringPeriodId") or 0)
        found = [dict(tx, scoringPeriodId=day) for tx in by_day.get(day, [])]
        return {"transactions": found}

    league.espn_request = SimpleNamespace(league_get=league_get)
    league.player_map = dict(names or {})
    return league


def league_with_days(
    *,
    teams: list[Any],
    boxes: dict[int, list[Any]],
    days: dict[int, dict[int, list[Any]]],
    windows: dict[int, list[str]],
    reg_season_count: int = 2,
    matchup_period_count: int = 2,
    cards: dict[int, Any] | None = None,
) -> Any:
    """A fake league with daily lineups.

    `days` maps matchup period -> scoring period -> box scores for that day.
    `windows` mirrors ESPN's `matchup_ids`: int keys, string values, and
    deliberately in lexicographic order so the sorting is exercised.
    """
    league = league_with_play(
        teams=teams,
        boxes=boxes,
        reg_season_count=reg_season_count,
        matchup_period_count=matchup_period_count,
        cards=cards,
    )
    league.matchup_ids = windows

    def box_scores(
        matchup_period: int | None = None,
        scoring_period: int | None = None,
        matchup_total: bool = True,
    ) -> list[Any]:
        if not matchup_total and scoring_period is not None:
            return days.get(matchup_period or 0, {}).get(scoring_period, [])
        return boxes.get(matchup_period or 0, [])

    league.box_scores = box_scores
    return league


def fake_pick(
    round_num: int,
    round_pick: int,
    player_id: int,
    name: str,
    *,
    team: Any = None,
    nominated_by: Any = None,
    bid: int | None = 10,
    keeper: bool = False,
) -> Any:
    """One draft pick, as `League.draft` presents it."""
    return SimpleNamespace(
        playerId=player_id,
        playerName=name,
        round_num=round_num,
        round_pick=round_pick,
        bid_amount=bid,
        keeper_status=keeper,
        team=team,
        nominatingTeam=nominated_by,
    )


def attach_draft(league: Any, picks: list[Any]) -> Any:
    """Give a fake league a draft. The real one arrives with the league."""
    league.draft = picks
    return league
