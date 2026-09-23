"""The MCP server: the tools, the design notes, and the house rules.

Built on the official Python SDK (`mcp`, version 2, whose server class is
`MCPServer`). Every tool here is three lines: open a session, call the
function in `app.mcp.tools`, hand back what it returns. The thinking is in
the engine the routes call; this is a doorway.

THE TOKEN

A tool has to be somebody. Over stdio that is `BOX_OUT_TOKEN` in the
server's environment -- a token the manager minted on his own account page.
Over streamable HTTP it is the request's `Authorization: Bearer`, read off
the context, with the environment's as the fallback for a connector that
carries one token of its own. Either way the token is resolved by
`app.mcp.scope`, which asks `app.api.access` the same questions every route
asks, so a tool sees exactly what its owner sees in a browser.

WHY EVERY TOOL DESCRIPTION SAYS WHAT IT DOES NOT DO

The model reading them is the one thing in this system that can invent a
number. The descriptions are written for it: what the tool returns, what it
cannot tell you, and where the number came from. The skill
(`skills/box-out-co-manager/SKILL.md`) is the rest of that argument.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resources import FunctionResource
from sqlalchemy.orm import Session, sessionmaker

from app.api.access import Viewer
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.mcp import tools
from app.mcp.scope import RefusedError, viewer_for_token

#: Where a stdio server reads its token from.
TOKEN_ENV = "BOX_OUT_TOKEN"

#: The name a host shows. The rename to Box Out is another piece of work
#: landing beside this one; when `app/brand.py` exists this reads from it.
NAME = "box-out"
TITLE = "Box Out"

REPO = Path(__file__).resolve().parent.parent.parent

#: The design notes a co-manager should reason from, served read-only. These
#: are the documents the pages were written out of, so a model asked WHY a
#: bar is 0.20 or why a trade number is printed under a record can answer
#: from the same argument the site was built on rather than from its own
#: sense of what is probably true.
NOTES: dict[str, tuple[str, str]] = {
    "pickups": (
        "docs/pickups.md",
        "How the wire is judged: the listener, the recommender, the bars",
    ),
    "trades": ("docs/trades.md", "How a trade is judged, and the measured record of the number"),
    "streaming_lane": (
        "docs/streaming_lane.md",
        "What a streamed roster place is worth, and how that was measured",
    ),
    "pickups_backtest": (
        "docs/pickups_backtest.md",
        "The backtest the bars were chosen on, and what the sweep actually showed",
    ),
    "intake": (
        "docs/intake.md",
        "Where a league's own numbers come from, and the order of fallback",
    ),
    "injuries": ("docs/injuries.md", "What the league said about a man, and when it said it"),
}

INSTRUCTIONS = """Box Out reads one fantasy basketball league's own record and
reasoning: the week and season plans, today's lineup, what changed, a trade
judged from both sides, the wire, the standings, and the numbers every one of
those leans on with the sample behind each.

Three rules hold whatever is asked.

Every number you say came from a tool result. You do not add, average,
project or estimate. If a tool did not return it, you do not know it.

Every number travels with where it came from. The `provenance` block on each
result says whether a bar was measured on this league, pooled from leagues
like it, or is a default measured on somebody else's, and on how large a
sample. Say which, in words, whenever you quote the number.

Nothing here decides. A bar labels a move and never hides one; the word for
a move above it is "worth a look", and a move below it is still shown with
its number. Never accept, reject, approve or recommend.

Nothing here writes. There is no tool that adds, drops, bids or accepts, and
no way to reach ESPN. If asked to make a move, say plainly that you cannot,
and give the manager the numbers to make it himself."""

HOUSE_RULES = """You are a co-manager for a nine-category head-to-head fantasy
basketball team. You read; the manager decides.

HOW YOU TALK

Plain words and short sentences. "Worth a look", never "recommended". "The
bar", never "the threshold". No verdicts: not accept, not reject, not
approve, not "you should". A move under the bar is still worth saying out
loud, labelled, because the bar says which moves are worth a look and never
hides one.

WHERE YOUR NUMBERS COME FROM

Every number you say came back from a tool, and you say what it rests on:
"0.20 categories a week, the bar this league's manager set himself", or
"0.06 a week, the default, measured on another league over 924 adds". If a
tool did not return a number, you do not have it. Do not compute one. Do not
round a stranger into a rounder one. Do not say what "the model thinks":
say what the tools returned and why it follows.

WHAT YOU DO FIRST

`league_context` before anything else in a conversation: it gives the day,
the rules, and this league's own numbers with their provenance. Then the
question:

* "What should I do?" -> `todays_lineup` first, because a place producing
  nothing tonight costs more than a clever add; then `week_report` for the
  moves that clear the bar, and the ones just under it, labelled; then
  `what_changed` for what moved since yesterday.
* A trade -> `judge_trade`. Lead with the fit, category by category, both
  sides. Then the number, smaller. End with the league's trade record,
  quoted, every time.
* "Is X worth picking up?" -> `free_agents` or `player_card`, and say what
  the number is against: a roster place, at the league standard.

WHAT YOU NEVER DO

You never act on ESPN: there is no tool that can, and there never will be
here. You never invent a deal nobody asked about -- nothing has measured
whether a proposed trade is one the other manager would take. You never
speak for the other side of a trade: its numbers are our estimate of his
roster's needs, and you say so."""


def _token(ctx: Context | None) -> str | None:
    """The token this call carries: the request's bearer, else the environment's.

    A remote connector that puts its own token in the header wins; a stdio
    server, which has no headers at all, falls back to `BOX_OUT_TOKEN`.
    """
    header = ""
    if ctx is not None:
        try:
            headers = dict(ctx.headers or {})
        except Exception:  # pragma: no cover - no HTTP request in this call
            headers = {}
        raw = headers.get("authorization") or headers.get("Authorization") or ""
        scheme, _, rest = raw.partition(" ")
        header = rest.strip() if scheme.lower() == "bearer" else ""
    return header or os.environ.get(TOKEN_ENV) or None


def build_server(
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> MCPServer:
    """The server, with every tool, resource and the house prompt on it.

    `session_factory` and `settings` are parameters so a test can point the
    whole surface at a disposable database without touching the environment,
    and `token` so a test can be a particular manager.
    """
    config = settings or get_settings()
    factory = session_factory or make_session_factory(make_engine(config.database_url))
    mcp = MCPServer(NAME, title=TITLE, instructions=INSTRUCTIONS, version="0.1.0")

    def run(
        ctx: Context | None, call: Callable[[Session, Viewer], dict[str, Any]]
    ) -> dict[str, Any]:
        """One tool call: a session, a viewer, the answer.

        A `RefusedError` becomes the SDK's tool error carrying its one sentence,
        which is the same sentence the site gives. Nothing else is caught: a
        bug should look like a bug in the log, not like a refusal.
        """
        with factory() as session:
            try:
                viewer = viewer_for_token(session, config, token or _token(ctx))
                return call(session, viewer)
            except RefusedError as no:
                raise ToolError(str(no)) from None

    # -- what this token may see ------------------------------------------

    @mcp.tool(
        description=(
            "Every league and team this token may read, with the role in each. "
            "Call it when you do not know the league id, the season or which "
            "team is the manager's. Returns nothing about any league he is not "
            "a member of."
        )
    )
    def my_leagues(ctx: Context) -> dict[str, Any]:
        return run(ctx, tools.my_leagues)

    @mcp.tool(
        description=(
            "The league's rules, its calendar, today's scoring period, and every "
            "number its recommendations lean on with where each came from: the "
            "manager's own choice, a measurement of this league, the pool of "
            "leagues like it, or a default measured elsewhere. Call this first "
            "in a conversation. It carries no player and no plan."
        )
    )
    def league_context(league_id: int, season: int, ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.league_context(s, v, league_id, season))

    # -- the three reports -------------------------------------------------

    @mcp.tool(
        description=(
            "This week for one team: the matchup as projected category by "
            "category, the moves worth a look (each clearing the league's bar on "
            "its own), the best of the rest with their numbers, the days a "
            "roster place produces nothing, and the season's projected record "
            "with no move made. The team's manager only. `today` replays an "
            "earlier scoring period; left out it is today's."
        )
    )
    def week_report(
        league_id: int, season: int, team_id: int, ctx: Context, today: int | None = None
    ) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.week_report(s, v, league_id, season, team_id, today))

    @mcp.tool(
        description=(
            "The rest of the season for one team: who to hold, who is worth "
            "dropping and what that costs, who is worth stashing hurt, the best "
            "move of each kind with what it is worth a week, and what to bid. "
            "The team's manager only."
        )
    )
    def season_report(
        league_id: int, season: int, team_id: int, ctx: Context, today: int | None = None
    ) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.season_report(s, v, league_id, season, team_id, today))

    @mcp.tool(
        description=(
            "Who starts today: the lineup as the recommender would set it, place "
            "by place, beside the lineup the team has actually set, with the "
            "places that will produce nothing tonight while a bench man would. "
            "The cheapest question in fantasy and usually the first one to ask. "
            "The team's manager only."
        )
    )
    def todays_lineup(
        league_id: int, season: int, team_id: int, ctx: Context, today: int | None = None
    ) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.todays_lineup(s, v, league_id, season, team_id, today))

    # -- the league's own facts -------------------------------------------

    @mcp.tool(
        description=(
            "What changed in the league in a window: injuries and status "
            "changes, adds, drops, claims with what they cost, and trades, each "
            "as one sentence, newest first. With no window at all it is the "
            "last twenty-four hours of real time, which is empty on a season "
            "stored months ago: pass `today` (a scoring period) to get that "
            "day and the one before it, or `since`/`until` as ISO moments. "
            "`team_id` only flags which of them are that team's own and its "
            "opponent's. Every member may read all of it."
        )
    )
    def what_changed(
        league_id: int,
        season: int,
        ctx: Context,
        team_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
        today: int | None = None,
    ) -> dict[str, Any]:
        return run(
            ctx,
            lambda s, v: tools.what_changed(s, v, league_id, season, team_id, since, until, today),
        )

    @mcp.tool(
        description=(
            "Every team's record: matchups won, lost and tied, and categories "
            "won, lost and tied. Byes left out. League scope."
        )
    )
    def standings(league_id: int, season: int, ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.standings(s, v, league_id, season))

    @mcp.tool(
        description=(
            "One team's matchup in a period, with each side's nine categories as "
            "ESPN last stored them. Left without a period it is the one today "
            "falls in. League scope."
        )
    )
    def matchup(
        league_id: int, season: int, team_id: int, ctx: Context, period: int | None = None
    ) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.matchup(s, v, league_id, season, team_id, period))

    @mcp.tool(
        description=(
            "Every roster move in the league over the last `days` days, executed "
            "and failed alike, with what each claim cost. League scope."
        )
    )
    def recent_moves(league_id: int, season: int, ctx: Context, days: int = 7) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.recent_moves(s, v, league_id, season, days))

    # -- players and the wire ---------------------------------------------

    @mcp.tool(
        description=(
            "One player's card: his line per game and over an ordinary week from "
            "here on, the games he has left and the games he has in the playoff "
            "weeks, whether he is hurt and when he is back, and how many games of "
            "his own stand behind the rate. `player_id` is ESPN's. The league and "
            "season may be left out when the manager is in one league. It does "
            "NOT carry what he is worth a week: `free_agents` and `judge_trade` "
            "do, because that number needs the league's measured spreads."
        )
    )
    def player_card(
        player_id: int,
        ctx: Context,
        league_id: int | None = None,
        season: int | None = None,
        today: int | None = None,
    ) -> dict[str, Any]:
        return run(ctx, lambda s, v: tools.player_card(s, v, player_id, league_id, season, today))

    @mcp.tool(
        description=(
            "The wire on one day, each man priced at what he gives an ordinary "
            "roster place in this league, with his week in the nine. Sort by "
            "`value`, `games` or `name`. The team's manager only, because it is "
            "priced for his roster's calendar. What a man is worth to a roster "
            "AFTER a particular deal is a different question: `judge_trade`."
        )
    )
    def free_agents(
        league_id: int,
        season: int,
        team_id: int,
        ctx: Context,
        today: int | None = None,
        sort: str = "value",
    ) -> dict[str, Any]:
        return run(
            ctx, lambda s, v: tools.free_agents(s, v, league_id, season, team_id, today, sort)
        )

    # -- a deal ------------------------------------------------------------

    @mcp.tool(
        description=(
            "A named trade, judged from both sides on one day. `give` are the "
            "ESPN player ids leaving this team, `get` the ones leaving "
            "`with_team`; `drop`/`their_drop` name who goes to make room, and "
            "`fill`/`their_fill` a free agent for a place the deal opens. The "
            "answer leads with the fit -- each side's nine categories before and "
            "after -- and carries the number second, with this league's own "
            "record of how often that number has been right. It judges the other "
            "side with our projections: that is our estimate of his roster's "
            "needs, never his opinion. It does not propose deals and cannot "
            "offer one to anybody."
        )
    )
    def judge_trade(
        league_id: int,
        season: int,
        team_id: int,
        with_team: int,
        ctx: Context,
        give: list[int] | None = None,
        get: list[int] | None = None,
        drop: list[int] | None = None,
        their_drop: list[int] | None = None,
        fill: list[int] | None = None,
        their_fill: list[int] | None = None,
        today: int | None = None,
    ) -> dict[str, Any]:
        return run(
            ctx,
            lambda s, v: tools.judge_trade(
                s,
                v,
                league_id,
                season,
                team_id,
                with_team,
                give,
                get,
                drop,
                their_drop,
                fill,
                their_fill,
                today,
            ),
        )

    _install_notes(mcp)

    @mcp.prompt(
        name="co_manager",
        title="Co-manager house rules",
        description="How to read for a manager: the language, the provenance, and the limits",
    )
    def co_manager() -> str:
        return HOUSE_RULES

    return mcp


def _install_notes(mcp: MCPServer) -> None:
    """The design notes, read-only, so a model can explain WHY.

    Read from disk per call, like every page on the site, so an edited
    document is the served document without a restart.
    """

    def serve(path: str) -> Callable[[], str]:
        def read() -> str:
            return (REPO / path).read_text()

        return read

    for key, (path, description) in NOTES.items():
        mcp.add_resource(
            FunctionResource.from_function(
                serve(path),
                uri=f"boxout://notes/{key}",
                name=path,
                description=description,
                mime_type="text/markdown",
            )
        )


async def tool_names(mcp: MCPServer) -> Sequence[str]:
    """Every tool on a server, for a test and for the docs."""
    return [tool.name for tool in await mcp.list_tools()]
