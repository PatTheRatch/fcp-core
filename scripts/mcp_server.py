#!/usr/bin/env python
"""Run the co-manager's tool surface.

    python scripts/mcp_server.py --stdio          # Claude Code, Claude Desktop
    python scripts/mcp_server.py --http           # the public server, OAuth on
    python scripts/mcp_server.py --http --no-auth # loopback, behind a tunnel

docs/mcp.md has the install steps. Three things are worth knowing before
running it.

**It needs a token.** `BOX_OUT_TOKEN` in the environment, minted by the
manager himself at `/account/connections`. Over HTTP a request's own
`Authorization: Bearer` wins, so a connector can carry a token per manager;
over stdio there are no headers and the environment's token is the answer.
Without one, every tool refuses with one sentence saying where to get one.

**`--http` is an OAuth resource server.** It advertises the site as its
authorization server, answers an unauthenticated call with the 401 the spec
names, and checks every bearer -- so an app a manager has never heard of
cannot read for him. It needs `FCP_MCP_PUBLIC_URL` and `FCP_PUBLIC_URL`, and
refuses to start without them rather than running open. `--no-auth` is the
loopback form the ChatGPT tunnel needs, where the environment's token is the
one manager the process serves; it must never be reachable from outside the
machine.

**Nothing it serves can write.** There is no tool that adds, drops, bids or
accepts, and no path from here to ESPN. It reads this database, which the
ingest fills.

Over stdio the protocol owns standard output, so nothing here may print to
it: the banner goes to standard error, and the logging that would otherwise
land on stdout is sent there too.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from app import brand
from app.config import get_settings
from app.mcp.server import (
    TOKEN_ENV,
    NoPublicUrlError,
    auth_settings,
    build_server,
    transport_security,
)

#: Where a remote connector answers, unless `--port` says otherwise.
DEFAULT_PORT = 8787


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--stdio",
        action="store_true",
        help="talk over stdin and stdout (Claude Code, Claude Desktop). The default.",
    )
    transport.add_argument(
        "--http",
        action="store_true",
        help="serve streamable HTTP, for a remote connector",
    )
    parser.add_argument("--host", default="127.0.0.1", help="with --http")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="with --http")
    parser.add_argument(
        "--no-auth",
        dest="auth",
        action="store_false",
        help=(
            "with --http: serve without OAuth, for a loopback server behind a tunnel. "
            "The environment's BOX_OUT_TOKEN then answers for every caller, so this "
            "must never be reachable from outside the machine."
        ),
    )
    args = parser.parse_args(argv)

    # Every log line to stderr. On stdio, stdout is the protocol's.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    settings = get_settings()

    if args.http:
        auth = None
        if args.auth:
            try:
                auth = auth_settings(settings)
            except NoPublicUrlError as missing:
                # A public server that cannot name itself does not start.
                print(f"error: {missing}", file=sys.stderr)
                return 2
        server = build_server(settings=settings, auth=auth)
        where = (
            f"protected resource {auth.resource_server_url}, authorization server {auth.issuer_url}"
            if auth is not None
            else f"NO AUTH: every caller is {TOKEN_ENV}'s manager"
        )
        print(
            f"{brand.BRAND} MCP on http://{args.host}:{args.port}/mcp "
            f"(auth mode {settings.fcp_auth_mode}; {where})",
            file=sys.stderr,
        )
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            # Bound to loopback, the SDK would otherwise answer only to a
            # loopback `Host` -- and Caddy passes the public name through.
            transport_security=transport_security(settings) if auth is not None else None,
        )
        return 0

    server = build_server(settings=settings)

    if not os.environ.get(TOKEN_ENV):
        print(
            f"warning: {TOKEN_ENV} is not set, so every tool will refuse. "
            "Make one at /account/connections (docs/mcp.md).",
            file=sys.stderr,
        )
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
