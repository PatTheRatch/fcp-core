"""A token sees what its owner sees, and a stdio round trip proves the wiring.

The fixture is `tests/test_access.py`'s, deliberately: the claim being made
is that the co-manager's tools answer the very questions the routes answer,
so they are held to the very leagues and claims those routes are held to.
Alice manages team 3 in league A, Bob team 5, Carol is in league B, and none
of the three leagues has a draft, a schedule or a lineup day -- so a tool
that gets through the door answers `ready: false` with the draft's own
sentence, which is exactly the signal `tests/test_access.py` reads a 200 as.

The last test starts the real server as a subprocess and talks to it over
stdin and stdout with the SDK's own client, because everything above runs
the tools in process and would not notice a broken entry point.
"""

import asyncio
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, api_tokens
from app.config import Settings, get_settings
from app.inseason.drafted import UNSCHEDULED
from app.mcp.scope import NO_SUCH_TOKEN, NO_TOKEN, NOT_A_MEMBER, TEAM_REFUSED
from app.mcp.server import TOKEN_ENV, build_server
from tests.test_access import (
    LEAGUE_A,
    LEAGUE_B,
    OWNER,
    SEASON,
    SERVICE_TOKEN,
    seeded,  # noqa: F401  -- the fixture itself, reused as it stands
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: What every tool answers on these leagues once the scope check has passed:
#: nothing says their draft was held, so there is nothing to report on.
THROUGH = UNSCHEDULED


@pytest.fixture
def session(seeded: sessionmaker[Session]) -> Iterator[Session]:  # noqa: F811
    with seeded() as open_session:
        yield open_session


def accounts_settings(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_owner_email": OWNER,
        "fcp_service_token": SERVICE_TOKEN,
        "espn_league_id": LEAGUE_A,
        "fcp_tracked_team_id": 3,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


def token_for(session: Session, email: str) -> str:
    """A machine token for a member, as his account page mints one."""
    user = accounts.user_by_email(session, email)
    assert user is not None, email
    minted = api_tokens.mint(session, user.id, f"{email} in Claude")
    session.commit()
    return minted.token


def server_for(seeded: sessionmaker[Session], token: str) -> MCPServer:  # noqa: F811
    return build_server(session_factory=seeded, settings=accounts_settings(), token=token)


def text_of(result: CallToolResult) -> str:
    """The one text block a tool result carries. Every tool here returns JSON."""
    block = result.content[0]
    assert isinstance(block, TextContent), f"expected text, got {type(block).__name__}"
    return block.text


def called(server: MCPServer, name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def once() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(once())


def said(server: MCPServer, name: str, arguments: dict[str, Any]) -> str:
    return text_of(called(server, name, arguments))


def plan(team: int, league: int = LEAGUE_A) -> dict[str, Any]:
    return {"league_id": league, "season": SEASON, "team_id": team}


# ---------------------------------------------------------------------------
# a token is its owner, and nobody else
# ---------------------------------------------------------------------------


def test_alices_token_cannot_read_bobs_plan(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
) -> None:
    """The one promise a machine token makes: it reads what its owner reads."""
    server = server_for(seeded, token_for(session, "alice@example.com"))

    refused = called(server, "week_report", plan(5))
    assert refused.is_error
    assert TEAM_REFUSED in text_of(refused)
    for tool in ("season_report", "todays_lineup", "free_agents"):
        assert TEAM_REFUSED in said(server, tool, plan(5))
    # The trade tools are the same paid team layer.
    assert TEAM_REFUSED in said(server, "judge_trade", {**plan(5), "with_team": 3})

    # Her own team: through the door, and told the season has nothing in it.
    hers = called(server, "week_report", plan(3))
    assert not hers.is_error and THROUGH in text_of(hers)
    assert json.loads(text_of(hers))["ready"] is False


def test_a_member_of_one_league_is_refused_another(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
) -> None:
    server = server_for(seeded, token_for(session, "alice@example.com"))
    assert NOT_A_MEMBER in said(server, "standings", {"league_id": LEAGUE_B, "season": SEASON})
    assert NOT_A_MEMBER in said(server, "league_context", {"league_id": LEAGUE_B, "season": SEASON})
    assert not called(server, "standings", {"league_id": LEAGUE_A, "season": SEASON}).is_error

    listed = json.loads(said(server, "my_leagues", {}))
    assert [row["espn_league_id"] for row in listed["leagues"]] == [LEAGUE_A]
    assert listed["as"] == "alice@example.com" and listed["how"] == "token"


def test_only_the_team_she_manages_is_hers_to_plan(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
) -> None:
    server = server_for(seeded, token_for(session, "alice@example.com"))
    listed = json.loads(said(server, "my_leagues", {}))
    mine = {row["espn_team_id"]: row["i_manage_it"] for row in listed["leagues"][0]["teams"]}
    assert mine == {3: True, 5: False}


def test_a_revoked_token_is_refused(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
) -> None:
    """Revoking is the whole point of minting: it has to stop the tools dead."""
    token = token_for(session, "bob@example.com")
    server = server_for(seeded, token)
    assert THROUGH in said(server, "week_report", plan(5)), "live, it reads his team"

    user = accounts.user_by_email(session, "bob@example.com")
    assert user is not None
    row = api_tokens.listing(session, user.id)[0]
    assert api_tokens.revoke(session, user.id, int(row.id))
    session.commit()

    for tool, arguments in (
        ("my_leagues", {}),
        ("week_report", plan(5)),
        ("standings", {"league_id": LEAGUE_A, "season": SEASON}),
    ):
        answer = called(server, tool, arguments)
        assert answer.is_error and NO_SUCH_TOKEN in text_of(answer), tool


def test_a_token_that_was_never_minted_and_no_token_at_all(
    seeded: sessionmaker[Session],  # noqa: F811
) -> None:
    unknown = build_server(
        session_factory=seeded, settings=accounts_settings(), token="bo_not-a-real-token"
    )
    assert NO_SUCH_TOKEN in said(unknown, "my_leagues", {})

    # No token, and none in the environment either: one sentence saying where
    # to get one, not a stack trace and not an empty answer.
    was = os.environ.pop(TOKEN_ENV, None)
    try:
        nothing = build_server(session_factory=seeded, settings=accounts_settings(), token=None)
        assert NO_TOKEN in said(nothing, "my_leagues", {})
    finally:
        if was is not None:
            os.environ[TOKEN_ENV] = was


def test_a_revoked_token_is_refused_in_single_mode_too(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
) -> None:
    """Single mode checks nothing else; it still checks that the token is live.

    Otherwise revoking a token would mean nothing on the one server this has
    actually run on, which is the local one.
    """
    token = token_for(session, "carol@example.com")
    single = accounts_settings(fcp_auth_mode="single")
    server = build_server(session_factory=seeded, settings=single, token=token)
    assert not called(server, "my_leagues", {}).is_error, "single mode reads every league"

    user = accounts.user_by_email(session, "carol@example.com")
    assert user is not None
    assert api_tokens.revoke(session, user.id, int(api_tokens.listing(session, user.id)[0].id))
    session.commit()
    assert NO_SUCH_TOKEN in said(server, "my_leagues", {})


# ---------------------------------------------------------------------------
# the real entry point, over stdin and stdout
# ---------------------------------------------------------------------------


def test_a_stdio_round_trip_with_the_sdks_own_client(
    seeded: sessionmaker[Session],  # noqa: F811
    session: Session,
    test_database_url: str,
) -> None:
    """`scripts/mcp_server.py --stdio`, started as a host starts it.

    Everything above runs the tools in process, which would not notice a
    broken entry point, an import that only fails under `python scripts/...`,
    or a server that writes its banner to the protocol's own stdout.
    """
    token = token_for(session, "alice@example.com")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "scripts" / "mcp_server.py"), "--stdio"],
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO_ROOT),
            "DATABASE_URL": test_database_url,
            "TEST_DATABASE_URL": test_database_url,
            "FCP_AUTH_MODE": "accounts",
            "FCP_OWNER_EMAIL": OWNER,
            "ESPN_LEAGUE_ID": str(LEAGUE_A),
            "FCP_TRACKED_TEAM_ID": "3",
            TOKEN_ENV: token,
        },
    )

    async def round_trip() -> tuple[list[str], str, str]:
        async with Client(parameters) as client:
            tools = await client.list_tools()
            mine = await client.call_tool("my_leagues", {})
            refused = await client.call_tool(
                "week_report", {"league_id": LEAGUE_A, "season": SEASON, "team_id": 5}
            )
        return (
            [tool.name for tool in tools.tools],
            text_of(mine),
            text_of(refused),
        )

    names, listed, refused = asyncio.run(round_trip())
    assert "judge_trade" in names and "league_context" in names
    body = json.loads(listed)
    assert body["as"] == "alice@example.com"
    assert [row["espn_league_id"] for row in body["leagues"]] == [LEAGUE_A]
    assert TEAM_REFUSED in refused
