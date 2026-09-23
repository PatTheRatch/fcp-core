"""The co-manager's tools, on a league small enough to check by hand.

Two claims are worth the fixture. **A tool's answer is the route's answer**:
the same engine, the same numbers, to the three decimals the trim keeps -- so
a manager reading a page and a model reading a tool can never be told two
different things. And **every tool carries its provenance**, because the one
failure this whole design exists to prevent is a number said out loud with
nothing behind it.

The scope checks live next door (`tests/test_mcp_access.py`), on the fixture
`tests/test_access.py` uses, because they need accounts mode and real
memberships and this file needs a league with box scores in it.
"""

import asyncio
import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ListToolsResult, TextContent, TextResourceContents
from sqlalchemy.orm import Session

from app.api import pickups as pickups_api
from app.api import trades as trades_api
from app.api import what_if as what_if_api
from app.config import Settings, get_settings
from app.db.models import LeagueSeason, MatchupPeriod, Player, Team
from app.mcp import trim
from app.mcp.server import HOUSE_RULES, NOTES, build_server
from app.pickups.bids import clear_cache
from app.pickups.projection import clear_cache as clear_lines
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    played,
    projected,
    snapshot,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

HOME, AWAY = 1, 2
PERIODS = 4
REGULAR = 3
SEASON = 2026
SEASON_DAYS = list(range(1, PERIODS * 7 + 1))
#: One week banked, one being played, one to come -- `tests/test_trades.py`'s
#: day, so a number here can be read beside one there.
TODAY = 8

#: A token the tests send. It is the service token, so no account rows are
#: needed for a file whose subject is the answers rather than the scope.
SERVICE_TOKEN = "a-service-token-long-enough-to-mean-something-0123456789"

STARTER: Mapping[str, float] = {
    "PTS": 20.0,
    "REB": 8.0,
    "AST": 4.0,
    "STL": 1.2,
    "BLK": 0.8,
    "3PM": 2.0,
    "TO": 2.4,
    "FGM": 8.0,
    "FGA": 17.0,
    "FTM": 4.0,
    "FTA": 5.0,
}
EVEN = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 20.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 235.0,
    "FGA": 500.0,
    "FTM": 78.0,
    "FTA": 100.0,
    "FG%": 235 / 500,
    "FT%": 0.78,
}


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in STARTER.items()}


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    clear_cache()
    clear_lines()
    yield scoring_session


def settings() -> Settings:
    """Single mode, with a service token, on the test database."""
    return get_settings().model_copy(
        update={
            "fcp_auth_mode": "single",
            "fcp_owner_email": "owner@example.com",
            "fcp_service_token": SERVICE_TOKEN,
            "espn_league_id": None,
            "fcp_tracked_team_id": None,
        }
    )


@pytest.fixture
def server(session: Session) -> MCPServer:
    """The whole surface, pointed at this test's own open session.

    One session for the whole test rather than one per call, so the rows the
    fixture has flushed and not committed are the rows the tools read.
    """
    return build_server(
        session_factory=_never_closes(session), settings=settings(), token=SERVICE_TOKEN
    )


def _never_closes(session: Session) -> Any:
    """A session factory that hands out this one session and never closes it.

    `build_server` opens one per call with `with factory() as ...`, which
    would close the fixture's session on the first tool. This wraps it so the
    context manager's exit does nothing.
    """

    class Held:
        def __enter__(self) -> Session:
            return session

        def __exit__(self, *_: object) -> None:
            return None

    def factory() -> Held:
        return Held()

    return factory


def build_league(session: Session) -> tuple[LeagueSeason, Team, Team, list[MatchupPeriod]]:
    ls, (home, away), periods = league_season(
        session, days_per_period=7, periods=PERIODS, regular_season_periods=REGULAR
    )
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
    matchup(session, periods[0], home, away, {home: EVEN, away: EVEN})
    matchup(session, periods[1], home, away)
    games(session, 10, SEASON_DAYS)
    games(session, 20, SEASON_DAYS)
    return ls, home, away, periods


def rostered(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    name: str,
    per_game: Mapping[str, float],
    *,
    pro_team: int = 10,
) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=pro_team, on_team_id=team.espn_team_id)
    projected(session, who, 70, per_game)
    for day in (1, TODAY):
        held(session, team, period, who, day)
    for day in range(1, TODAY):
        played(session, who, day, 32.0, per_game)
    return who


def build_rosters(
    session: Session, ls: LeagueSeason, home: Team, away: Team, periods: list[MatchupPeriod]
) -> dict[str, Player]:
    who: dict[str, Player] = {}
    for name in ("HomeA", "HomeB", "HomeC"):
        who[name] = rostered(session, home, periods[1], name, STARTER)
    who["HomeWeak"] = rostered(session, home, periods[1], "HomeWeak", scaled(0.4))
    for name in ("AwayA", "AwayB"):
        who[name] = rostered(session, away, periods[1], name, STARTER, pro_team=20)
    who["AwayWeak"] = rostered(session, away, periods[1], "AwayWeak", scaled(0.2), pro_team=20)
    who["Star"] = rostered(session, away, periods[1], "Star", scaled(1.6), pro_team=20)
    return who


def on_wire(session: Session, ls: LeagueSeason, name: str, per_game: Mapping[str, float]) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=20, on_team_id=0)
    projected(session, who, 70, per_game)
    on_the_wire(session, ls, who)
    for day in range(1, TODAY):
        played(session, who, day, 24.0, per_game)
    return who


@pytest.fixture
def league(session: Session) -> dict[str, Any]:
    """A whole small league: two rosters, a wire, a week banked, a week to play."""
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    who["Wire"] = on_wire(session, ls, "Wire", scaled(0.5))
    session.flush()
    return {"ls": ls, "home": home, "away": away, "periods": periods, "who": who}


def text_of(result: CallToolResult) -> str:
    """The one text block a tool result carries. Every tool here returns JSON."""
    block = result.content[0]
    assert isinstance(block, TextContent), f"expected text, got {type(block).__name__}"
    return block.text


def called(server: MCPServer, name: str, arguments: dict[str, Any]) -> CallToolResult:
    """One tool call over the SDK's own in-process client, as a host makes it."""

    async def once() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(once())


def call(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = called(server, name, arguments)
    text = text_of(result)
    if result.is_error:
        raise AssertionError(text)
    return dict(json.loads(text))


def refusal(server: MCPServer, name: str, arguments: dict[str, Any]) -> str:
    result = called(server, name, arguments)
    assert result.is_error, f"{name} was not refused"
    return text_of(result)


def listed(server: MCPServer) -> ListToolsResult:
    async def once() -> ListToolsResult:
        async with Client(server) as client:
            return await client.list_tools()

    return asyncio.run(once())


# ---------------------------------------------------------------------------
# the surface itself
# ---------------------------------------------------------------------------

#: Every tool, in the order docs/mcp.md lists them. A tool added without a
#: line in the document, or removed from it, fails here.
EXPECTED = {
    "my_leagues",
    "league_context",
    "week_report",
    "season_report",
    "todays_lineup",
    "what_changed",
    "standings",
    "projected_standings",
    "matchup",
    "recent_moves",
    "player_card",
    "free_agents",
    "judge_trade",
    "what_if",
}


def test_every_tool_has_a_schema_and_says_what_it_does_not_do(server: MCPServer) -> None:
    tools = listed(server).tools
    assert {tool.name for tool in tools} == EXPECTED
    for tool in tools:
        assert tool.description and len(tool.description) > 80, f"{tool.name} needs a description"
        assert tool.input_schema["type"] == "object"
        # The context parameter is the SDK's and must never reach the model.
        assert "ctx" not in tool.input_schema.get("properties", {})


def test_nothing_on_the_surface_can_write(server: MCPServer) -> None:
    """The read-only promise, checked rather than asserted in a docstring."""
    tools = listed(server).tools
    for tool in tools:
        for verb in ("add_", "drop_", "bid", "accept", "propose", "set_", "claim"):
            assert not tool.name.startswith(verb), f"{tool.name} sounds like a write"
    assert "find_trades" not in {tool.name for tool in tools}, (
        "nobody has built it; a tool that returned a guess is the failure this "
        "design exists to prevent"
    )


def test_the_design_notes_are_served_read_only(server: MCPServer) -> None:
    async def once() -> tuple[set[str], str]:
        async with Client(server) as client:
            rows = await client.list_resources()
            read = await client.read_resource("boxout://notes/trades")
        first = read.contents[0]
        assert isinstance(first, TextResourceContents)
        return {str(row.uri) for row in rows.resources}, first.text

    uris, text = asyncio.run(once())
    assert uris == {f"boxout://notes/{key}" for key in NOTES}
    assert "The evaluator is no better than a coin flip" in text


def test_the_prompt_is_the_house_rules(server: MCPServer) -> None:
    async def once() -> str:
        async with Client(server) as client:
            got = await client.get_prompt("co_manager")
        block = got.messages[0].content
        assert isinstance(block, TextContent)
        return block.text

    said = asyncio.run(once())
    assert said == HOUSE_RULES
    assert "worth a look" in said
    assert "the manager decides" in said.lower()


# ---------------------------------------------------------------------------
# the answers are the routes' answers
# ---------------------------------------------------------------------------


def test_a_judged_deal_is_the_trade_routes_own_answer(
    server: MCPServer, session: Session, league: dict[str, Any]
) -> None:
    """The tool and the page cannot be told two different things about a deal.

    The same named trade, through the route function the trade page fetches
    and through the tool, compared number for number at the three decimals
    the trim keeps.
    """
    who = league["who"]
    arguments = {
        "league_id": LEAGUE_ID,
        "season": SEASON,
        "team_id": HOME,
        "with_team": AWAY,
        "give": [int(who["HomeWeak"].espn_player_id)],
        "get": [int(who["Star"].espn_player_id)],
        "today": TODAY,
    }
    answer = call(server, "judge_trade", arguments)

    route = trades_api.trade_report(
        league["ls"],
        league["home"],
        session,
        with_team=AWAY,
        give=[int(who["HomeWeak"].espn_player_id)],
        get=[int(who["Star"].espn_player_id)],
        today=TODAY,
    ).model_dump(mode="json")
    deal = route["trade"]

    assert answer["trade_record"] == route["calibration_note"], "the record, verbatim"
    assert answer["judged_on_day"] == deal["today"]
    assert answer["hurdle"] == trim.n(deal["hurdle"])
    ours = next(side for side in answer["sides"] if side["espn_team_id"] == HOME)
    route_ours = next(side for side in deal["sides"] if side["espn_team_id"] == HOME)
    assert ours["net"] == trim.n(route_ours["net"])
    assert ours["per_week"] == trim.n(route_ours["per_week"])
    assert ours["clears_hurdle"] == route_ours["clears"]
    assert ours["summary"] == route_ours["summary"]
    assert [row["category"] for row in ours["categories"]] == [
        row["abbreviation"] for row in route_ours["categories"]
    ]
    for mine, theirs in zip(ours["categories"], route_ours["categories"], strict=True):
        assert mine["before"] == trim.n(theirs["before"])
        assert mine["after"] == trim.n(theirs["after"])
        assert mine["chance_delta"] == trim.n(theirs["p_delta"])
    assert [card["name"] for card in ours["receives"]] == ["Star"]
    assert [card["name"] for card in ours["gives"]] == ["HomeWeak"]
    # Both sides, always: the other side is judged with the same machinery.
    assert {side["espn_team_id"] for side in answer["sides"]} == {HOME, AWAY}
    assert "estimate of his roster's needs" in answer["language"]


def test_a_named_pickup_is_the_what_if_routes_own_answer(
    server: MCPServer, session: Session, league: dict[str, Any]
) -> None:
    """The tool and the page cannot be told two different things about a move.

    The same named swap, through the route function the week page would fetch
    and through the tool, compared at the three decimals the trim keeps -- the
    week's chances, the judgement's own numbers, and the finish.
    """
    who = league["who"]
    dropped = int(who["HomeWeak"].espn_player_id)
    added = int(who["Wire"].espn_player_id)
    answer = call(
        server,
        "what_if",
        {
            "league_id": LEAGUE_ID,
            "season": SEASON,
            "team_id": HOME,
            "drop": [dropped],
            "add": [added],
            "today": TODAY,
        },
    )
    route = what_if_api.what_if_report(
        league["ls"], league["home"], session, drop=[dropped], add=[added], today=TODAY
    ).model_dump(mode="json")

    assert answer["judged_on_day"] == route["today"]
    assert answer["kind"] == route["kind"] == "swap"
    assert answer["net"] == trim.n(route["net"])
    assert answer["hurdle"] == trim.n(route["hurdle"])
    assert answer["clears_hurdle"] == route["clears_hurdle"]
    assert answer["this_week"]["chance_by_category_before"] == trim.nine(route["week"]["before"])
    assert answer["this_week"]["chance_by_category_after"] == trim.nine(route["week"]["after"])
    assert answer["this_week"]["expected_categories_after"] == trim.n(
        route["week"]["expected_after"]
    )
    assert answer["judgement"] == trim.judgement(route["judgement"])
    assert answer["bid"] == trim.bid(route["bid"]), "the ladder too, or the lack of one"
    assert [man["name"] for man in answer["adds"]] == ["Wire"]
    assert [man["name"] for man in answer["drops"]] == ["HomeWeak"]

    finish = answer["finish"]
    assert finish["playoff_odds_before"] == trim.n(route["finish"]["playoff_odds_before"])
    assert finish["playoff_odds_after"] == trim.n(route["finish"]["playoff_odds_after"])
    assert finish["projected_categories_after"] == [
        trim.n(value) for value in route["finish"]["record_after"]
    ]
    assert finish["odds_band"] == trim.n(route["finish"]["odds_band"])
    assert finish["projection_record"] == route["finish"]["calibration_note"]
    assert [week["period"] for week in finish["weeks_ahead"]] == [
        week["period"] for week in route["finish"]["weeks"]
    ]
    # The one thing a model must not get wrong about this answer.
    assert "second lens and not a second bar" in answer["language"]
    assert "odds_band" in answer["language"]


def test_a_judged_deal_carries_the_finish_for_both_sides(
    server: MCPServer, league: dict[str, Any]
) -> None:
    """The deal happens to both rosters, so both finishes come back."""
    who = league["who"]
    answer = call(
        server,
        "judge_trade",
        {
            "league_id": LEAGUE_ID,
            "season": SEASON,
            "team_id": HOME,
            "with_team": AWAY,
            "give": [int(who["HomeWeak"].espn_player_id)],
            "get": [int(who["Star"].espn_player_id)],
            "today": TODAY,
        },
    )
    for side in answer["sides"]:
        finish = side["finish"]
        assert finish is not None
        assert finish["espn_team_id"] == side["espn_team_id"]
        assert finish.get("projection_record")
        assert finish["odds_band"] >= 0
        assert "weeks_ahead" not in finish, "a deal has two sides of them; the tool drops both"
    assert "second lens and not a second bar" in answer["language"]
    assert answer["provenance"]["projected_record_note"]


def test_a_week_report_is_the_pickup_routes_own_answer(
    server: MCPServer, session: Session, league: dict[str, Any]
) -> None:
    answer = call(
        server,
        "week_report",
        {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    )
    route = pickups_api.stream_report(
        league["ls"], league["home"], session, today=TODAY
    ).model_dump(mode="json")

    assert answer["expected_categories_won"] == trim.n(route["expected_wins"])
    assert answer["hurdle"] == trim.n(route["hurdle"])
    assert answer["matchup_period"] == route["matchup_period"]
    assert answer["chance_by_category"] == trim.nine(route["probabilities"])
    assert len(answer["worth_a_look"]) == len(route["recommended"])
    assert [move["bid"] for move in answer["worth_a_look"]] == [
        trim.bid(move["bid"]) for move in route["recommended"]
    ]
    assert answer["also_ranked"]["of"] == len(route["moves"])
    assert answer["roster_room"]["adds_left"] == route["adds_left"]


def test_a_lineup_is_the_today_routes_own_answer(
    server: MCPServer, session: Session, league: dict[str, Any]
) -> None:
    answer = call(
        server,
        "todays_lineup",
        {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    )
    route = pickups_api.today_report(league["ls"], league["home"], session, today=TODAY).model_dump(
        mode="json"
    )
    assert answer["places_filled"] == route["starts"]
    assert [place["slot"] for place in answer["proposed_lineup"]] == [
        place["slot"] for place in route["lineup"]
    ]
    assert answer["projected_today"] == trim.nine(route["projected"])


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

#: Every tool, with arguments that work on this fixture. The provenance test
#: walks it, so a tool added without one is a failing test rather than an
#: answer with nothing behind it.
ALL_CALLS: dict[str, dict[str, Any]] = {
    "league_context": {"league_id": LEAGUE_ID, "season": SEASON},
    "week_report": {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    "season_report": {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    "todays_lineup": {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    "what_changed": {"league_id": LEAGUE_ID, "season": SEASON},
    "standings": {"league_id": LEAGUE_ID, "season": SEASON},
    "matchup": {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "period": 2},
    "recent_moves": {"league_id": LEAGUE_ID, "season": SEASON, "days": 7},
    "free_agents": {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    "projected_standings": {"league_id": LEAGUE_ID, "season": SEASON, "today": TODAY},
}


def test_every_tool_carries_its_provenance(server: MCPServer, league: dict[str, Any]) -> None:
    """No number without a way to ask where it came from."""
    for name, arguments in ALL_CALLS.items():
        answer = call(server, name, arguments)
        found = answer["provenance"]
        assert found["league_id"] == LEAGUE_ID, name
        assert found["season"] == SEASON, name
        assert "source_note" in found["projection"], name
        assert "reported_at" in found["injuries"], name
        assert "read_only" in found, name
        for key, number in found["calibration"].items():
            assert set(number) >= {"value", "source", "n", "note", "measured_at"}, f"{name}:{key}"
            assert number["source"] in ("owner", "measured", "pooled", "default"), name
            assert number["note"], f"{name}:{key} has no sentence under it"

    who = league["who"]
    deal = call(
        server,
        "judge_trade",
        {
            "league_id": LEAGUE_ID,
            "season": SEASON,
            "team_id": HOME,
            "with_team": AWAY,
            "give": [int(who["HomeWeak"].espn_player_id)],
            "get": [int(who["Star"].espn_player_id)],
            "today": TODAY,
        },
    )
    assert deal["provenance"]["calibration"]["trade_record"]["n"] > 0
    assert deal["trade_record"], "the record travels with the number"


def test_the_week_report_carries_the_schedule_a_line_a_day(
    server: MCPServer, session: Session, league: dict[str, Any]
) -> None:
    """Three numbers a side a day, and no names: the count is the answer to
    "how many games have I left", and the men are on the page."""
    answer = call(
        server,
        "week_report",
        {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    )
    route = pickups_api.stream_report(
        league["ls"], league["home"], session, today=TODAY
    ).model_dump(mode="json")["schedule"]

    schedule = answer["schedule"]
    assert [day["scoring_period"] for day in schedule["days"]] == [
        day["scoring_period"] for day in route["days"]
    ]
    for line, day in zip(schedule["days"], route["days"], strict=True):
        assert line["mine"] == [
            day["mine"]["games"],
            day["mine"]["seated"],
            day["mine"]["open_places"],
        ]
        assert line["theirs"] == [
            day["theirs"]["games"],
            day["theirs"]["seated"],
            day["theirs"]["open_places"],
        ]
    assert schedule["mine_total"][0] == route["mine_total"]["games"]
    assert schedule["mine_total"][1] == sum(day["mine"]["seated"] for day in route["days"])
    assert "seated is what will count" in schedule["reads"]


def test_the_week_report_says_where_its_bar_came_from(
    server: MCPServer, league: dict[str, Any]
) -> None:
    """A bar labels and never hides, so the label has to say whose bar it is."""
    answer = call(
        server,
        "week_report",
        {"league_id": LEAGUE_ID, "season": SEASON, "team_id": HOME, "today": TODAY},
    )
    bar = answer["provenance"]["calibration"]["stream_hurdle"]
    assert bar["value"] == answer["hurdle"]
    # This fixture's league has never been measured, so it reads the default
    # and the note says, in words, that it was measured on another league.
    assert bar["source"] == "default"
    assert "another league" in bar["note"]
    assert answer["also_ranked"]["moves"], "the moves under the bar are still here"


# ---------------------------------------------------------------------------
# the sentences a refusal gives
# ---------------------------------------------------------------------------


def test_a_deal_that_cannot_be_read_is_the_routes_own_sentence(
    server: MCPServer, league: dict[str, Any]
) -> None:
    who = league["who"]
    said = refusal(
        server,
        "judge_trade",
        {
            "league_id": LEAGUE_ID,
            "season": SEASON,
            "team_id": HOME,
            "with_team": AWAY,
            # A man the other side holds, offered as ours.
            "give": [int(who["Star"].espn_player_id)],
            "get": [int(who["AwayWeak"].espn_player_id)],
            "today": TODAY,
        },
    )
    assert "does not have Star on its roster" in said
    assert "Traceback" not in said


def test_an_unreadable_moment_is_a_sentence_not_a_stack_trace(
    server: MCPServer, league: dict[str, Any]
) -> None:
    said = refusal(
        server,
        "what_changed",
        {"league_id": LEAGUE_ID, "season": SEASON, "since": "last tuesday"},
    )
    assert "is not a moment I can read" in said
