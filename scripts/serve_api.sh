#!/bin/sh
# Serve the read-only API on the Tailscale interface only.
#
# The bind address is the point. There is no authentication on this API, and
# it reports league members' data, so it listens on the tailnet address and
# nowhere else. Binding 0.0.0.0 here would publish every endpoint to the
# internet; nothing in the code would stop it.
#
# Falls back to loopback if Tailscale cannot be read, which fails closed:
# unreachable from elsewhere rather than accidentally open.

set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$REPO_DIR"

PORT="${FCP_API_PORT:-8001}"
PYTHON="$REPO_DIR/.venv/bin/python"

BIND="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
if [ -z "$BIND" ]; then
    echo "tailscale address unavailable; binding loopback instead" >&2
    BIND="127.0.0.1"
fi

echo "serving on http://${BIND}:${PORT} (tailnet only)" >&2
exec "$PYTHON" -m uvicorn app.main:create_app \
    --factory \
    --host "$BIND" \
    --port "$PORT" \
    --no-server-header
