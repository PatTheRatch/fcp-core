"""The co-manager's tool surface: everything the site knows, for a model.

docs/mcp.md is the whole story. In one paragraph: a co-manager is a model
that calls these tools and talks about what they return. **It must never be
the thing that produces a number.** Every figure it says out loud came back
from one of these tools, with the sample it rests on beside it, because a
number a model invents is indistinguishable from one it read and that is the
failure this design exists to prevent.

So the tools are thin. Each one calls the very function the matching route
calls -- not HTTP to ourselves, and never a second implementation of an
arithmetic the site already has -- and hands back the route's own JSON,
trimmed for a reader, with a `provenance` block saying where each number came
from: the calibration accessor's `source`, `n`, `measured_at` and `note` for
every bar the answer leans on, the projection's `source_note`, and the moment
the injury report it read was published.

WHAT IS NOT HERE, AND WILL NOT BE

* **Nothing writes.** No tool adds, drops, bids, accepts or proposes
  anything on ESPN. The site is a reader and so is this.
* **No `find_trades`.** Nobody has built it, and a tool that returned a
  plausible list of deals nobody measured would be the model producing
  numbers with our name on them.
* **No verdict.** No field says accept or reject; a bar labels a move and
  never hides one (docs/pickups.md section 4, docs/trades.md section 0).

THE FILES

    scope.py       who the token is, and what it may open
    provenance.py  where every number in an answer came from
    trim.py        the route's JSON, cut to what a model needs
    tools.py       the tools themselves, one function each
    server.py      the SDK server: tools, resources, the house prompt
"""

from app.mcp.scope import RefusedError
from app.mcp.server import build_server

__all__ = ["RefusedError", "build_server"]
