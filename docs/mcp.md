# The co-manager: everything the site knows, as tools a model can call

**Written:** 2026-09-23. **Status:** built, exercised in process and over
stdio, and driven for real in three conversations — see "The live run". The
OAuth front door is built and run end to end on the laptop against the MCP
SDK's own OAuth client and the Claude Code CLI; it is **not deployed** —
"Deploying it (the owner's steps)" is the runbook.

Code: `app/mcp/` (`scope.py`, `provenance.py`, `trim.py`, `tools.py`,
`server.py`), the entry point `scripts/mcp_server.py`, the tokens
(`app/api_tokens.py`, `app/api/tokens.py`, migration `0029_api_tokens`), the
OAuth front door (`app/oauth.py`, `app/api/oauth.py`,
`app/api/static/consent.html`, migration `0030_oauth_front_door`), and the
skill `skills/box-out-co-manager/SKILL.md`. Tests: `tests/test_mcp.py`,
`tests/test_mcp_access.py`, `tests/test_oauth.py`.

## Why

Everything the site knows already sits behind read-only routes with the
reasoning attached: the week and season plans, today's lineup, what changed,
the trade evaluator, the player card, the standings, the wire, and each
league's own calibration with its provenance (docs/intake.md).

A co-manager is a model that calls those and talks about them. **It must
never be the thing that produces a number.** A figure a model invents is
indistinguishable, in the reading, from one it looked up — and the whole
argument for this product is that its numbers can be traced to a
measurement. So the discipline lives in the tools and in the skill, not in
the model: the tools carry provenance, the refusals are sentences, the
language rules are in the payload, and the one tool that would have to guess
is not built.

That matters twice over. The owner reads it in his own Claude today. The
in-site chat, later, may run a cheaper model, and a cheaper model held to
tool results is better than a clever one held to nothing.

## The token

A tool has to be somebody. A cookie is a browser's and the `FCP_SERVICE_TOKEN`
in `.env` is the server's own, so a manager makes his own on
**Account → Connections → "Tools that read for you"**: a name, a button, and
the token written to the page once. Prefixed `bo_`, so a token in a config
file is recognisable; only its sha256 is stored, as a session cookie's is;
revoking one stops it dead.

**A token an app asked for is the same token.** Since 2026-09-23 an app can
ask for one through OAuth ("Adding it to your own Claude or ChatGPT", below)
instead of a manager cutting one by hand. What it receives is
`api_tokens.mint`'s own `bo_` string, named after the app and listed on the
same page with **via OAuth** beside it. There is one kind of token here, one
lifetime and one place to end it.

**A token carries no scope, deliberately.** It acts as the manager who made
it, through the same dependencies every route declares
(`app.api.access.resolve_viewer`), so it reads his leagues and his teams'
plans and nothing more. A scope nobody can see is a promise nobody can
check. `tests/test_mcp_access.py` holds it to that on the fixture
`tests/test_access.py` uses: Alice's token reads Alice's team and hears
"This team's plan is its manager's." about Bob's.

In single mode the token acts as the owner — which is what single mode means
everywhere else — but it is still checked, so a revoked token stops working
on the one server this has actually run on.

## Adding it to Claude Code

In this repo, `.mcp.json` is already there:

```json
{
  "mcpServers": {
    "box-out": {
      "command": "./.venv/bin/python",
      "args": ["scripts/mcp_server.py", "--stdio"],
      "env": { "PYTHONPATH": ".", "BOX_OUT_TOKEN": "${BOX_OUT_TOKEN}" }
    }
  }
}
```

so all it needs is the token in the environment Claude Code is started from:

```
export BOX_OUT_TOKEN=bo_...        # from /account/connections
claude
```

The database comes from `.env` in the repo root, like everything else here.
`/mcp` in the session lists it; the tools appear as `mcp__box-out__*` and the
house rules as the slash command `/mcp__box-out__co_manager`.

**As run on 2026-09-22**, against the local database and the stored 2026
season, the server reported `"status":"connected"` with every tool and the
prompt registered.

## Adding it to Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "box-out": {
      "command": "/Users/you/fcp-core/.venv/bin/python",
      "args": ["/Users/you/fcp-core/scripts/mcp_server.py", "--stdio"],
      "env": {
        "PYTHONPATH": "/Users/you/fcp-core",
        "DATABASE_URL": "postgresql+psycopg://...",
        "BOX_OUT_TOKEN": "bo_..."
      }
    }
  }
}
```

Absolute paths everywhere: Claude Desktop starts the process with no shell
and no working directory of yours, so a relative path finds nothing and a
`.env` beside the repo is never read.

## Adding it to ChatGPT, through OpenAI's tunnel

Set up on the VPS on 2026-09-23. ChatGPT reaches a private MCP server
through OpenAI's own `tunnel-client`
(https://github.com/openai/tunnel-client, the guide at
developers.openai.com/api/docs/guides/secure-mcp-tunnels): a daemon on our
side polls OpenAI *outbound* and forwards each request to the server on
loopback. Nothing is exposed — no public hostname, no open port, no OAuth
server of ours — which is why this route was taken before the OAuth one.

What runs, both as systemd units on the VPS:

- `fcp-core-mcp.service` — `scripts/mcp_server.py --http --host 127.0.0.1
  --port 8787`, the streamable-HTTP form at `http://127.0.0.1:8787/mcp`,
  reading `.env` and `/opt/fcp-core/.mcp.env` (`BOX_OUT_TOKEN`, a token
  minted on the site, 0600, the owner's).
- `openai-tunnel.service` — `/usr/local/bin/tunnel-client run --profile
  boxout`, reading `/opt/fcp-core/.tunnel.env` (`CONTROL_PLANE_API_KEY`,
  an organization API key from platform.openai.com/settings/organization/
  api-keys made by someone with Tunnels Read + Use; 0600). Its profile is
  `~/.config/tunnel-client/boxout.yaml`, from
  `tunnel-client init --sample sample_mcp_remote_no_auth --profile boxout
  --tunnel-id tunnel_… --mcp-server-url http://127.0.0.1:8787/mcp`. Its
  own health is on `127.0.0.1:8080` (`/readyz`, `/ui`), loopback only.

The binary was downloaded from the release page and its SHA-256 checked
against the release's `SHA256SUMS.txt` before install (`v0.0.14`). The
tunnel id is an address, not a secret; the two keys never leave their
files. `tunnel-client doctor --profile boxout --explain` is the check to
run when it stops working; "OAuth discovery failed" in its log is expected
and harmless (we advertise none).

In ChatGPT: chatgpt.com/plugins → **+** → Connection: **Tunnel** → pick the
tunnel. The daemon has to be running while the app is created and for
every call after; `systemctl status openai-tunnel` says whether it is.

**What this is not.** Private to the tunnel's OpenAI organization and
ChatGPT workspace — OpenAI does not allow it to be published — so it is the
owner's own co-manager in ChatGPT, not the product's. A league member adding
Box Out to their own ChatGPT or Claude uses the front door below, and the
token the tunnel uses is the owner's, so it reads what he can read and
nothing more.

## The remote form

```
python scripts/mcp_server.py --http --host 127.0.0.1 --port 8787
```

serves streamable HTTP at `/mcp`. With auth on — the default — it is an
OAuth 2.1 protected resource and every call has to carry a bearer the site
issued. `--no-auth` is the loopback form the ChatGPT tunnel needs, where
`BOX_OUT_TOKEN` answers for every caller and the port must never be reachable
from outside the machine.

## Adding it to your own Claude or ChatGPT

**For a manager, it is three steps.** Paste `https://mcp.boxoutfantasy.com`
into the app's connector settings; the app sends you to boxoutfantasy.com,
where you sign in the way you always do here — your address, a link in your
mail, no password; you read a page that names the app and says it wants to
read your leagues and your teams' plans and can never add, drop, bid or
accept anything on ESPN, and you press Allow. That is the whole of it. The
app is connected, and the token it now holds is listed on
**Account → Connections** under that app's name with **via OAuth** beside
it, where you can end it whenever you like.

**What the app is given is one of your own `bo_` tokens.** Not a token of a
second kind with a second lifetime: the same row in `api_tokens` the owner
mints by hand above, made by `api_tokens.mint` and named after the app that
asked. It is you, through the same checks every route declares, so it reads
your leagues and your teams' plans and nothing else. OAuth here is a front
door onto the tokens that already existed — the owner's words on
2026-09-23 — and not a replacement for them.

### The endpoints

The authorization server is on the site (`app/api/oauth.py`), because the
sign-in is. The MCP server is the resource server (`app/mcp/server.py`).

| Where | What |
|---|---|
| `GET https://mcp.boxoutfantasy.com/.well-known/oauth-protected-resource/mcp` | RFC 9728: this resource, and the site as its authorization server. Served at the bare path too, for clients that ask there first |
| `POST https://mcp.boxoutfantasy.com/mcp` with no usable bearer | `401` with `WWW-Authenticate: Bearer … resource_metadata="…"`, which is how an app finds the front door at all |
| `GET https://boxoutfantasy.com/.well-known/oauth-authorization-server` | RFC 8414: the endpoints below, `code_challenge_methods_supported: ["S256"]`, `grant_types_supported: ["authorization_code"]` |
| `POST /oauth/register` | RFC 7591 dynamic registration. Stores a client id, the app's name and its redirect URIs. No client secret is issued |
| `GET /oauth/authorize` | Needs a session; signed out it goes to `/sign-in?next=<this whole request>` and comes back to it. Then the consent page |
| `POST /oauth/consent` | Allow issues a single-use code, ten minutes, bound to the app, the redirect URI, the PKCE challenge, the manager and the `resource`. Deny issues nothing and says `access_denied` |
| `POST /oauth/token` | `authorization_code` with the PKCE verifier. The answer is the raw `bo_` string, once |
| `POST /oauth/revoke` | RFC 7009. Ends the token; always `200` |

### No refresh token, on purpose

The token endpoint issues **no refresh token** and sends **no `expires_in`**,
and the metadata advertises `authorization_code` alone.

A `bo_` token does not expire. It ends when its manager revokes it on
Connections, and that is the only lifetime it has ever had. A refresh token
would be a second secret to store, hash and rotate, wrapped around an access
token that never goes stale — ceremony that would make the metadata say
something the tokens do not do. The honest version is the one a manager can
check: one token, visible on his own account page, dead the moment he says
so. If an app's token stops working it does what it does on any 401 — walks
the flow again, which is a sign-in and a click.

### As run, 2026-09-23

Two real servers on the laptop (the site on `:8010` in accounts mode, the
co-manager on `:8787` with auth on) against a private database, driven by
the MCP SDK's own `OAuthClientProvider` — the client that does the
discovery, the registration and the PKCE, with this end playing only the
human's part:

```
1. app sends the browser to        http://localhost:8010/oauth/authorize
2. signed out, so the site says    /sign-in?next=<the whole request>
3. asked for a link                202 If that address can sign in, a link is on its way…
4. clicked the link in the mail    http://localhost:8010/auth/callback
5. signed in, sent back to         /oauth/authorize
6. the consent page says           Claude Code — wants to read your leagues and your teams' plans…
7. pressed Allow, sent back to     http://127.0.0.1:33418/callback
8. registered client_id            boc_jKDqiJyZiYYPQMoLbzTbb3mflEnHTXdK01s4Bp7OA0k
9. the access token                bo_…            refresh token? None   expires_in? None
10. tools the server offers        14: my_leagues, league_context, week_report, …
11. my_leagues() came back         {"as": "member@example.com", "how": "token", "leagues": []}
```

Then, in the same run: the token appears on the account page as
`Claude Code (the SDK's own OAuth client) (OAuth)`; revoking it there makes
the next call to `/mcp` a `401` with the resource-metadata header.

**And against the Claude Code CLI itself.**
`claude mcp add --transport http box-out-remote http://127.0.0.1:8787/mcp`
was accepted (it does not insist on https), the CLI read
`/.well-known/oauth-protected-resource/mcp`, read the site's
`/.well-known/oauth-authorization-server`, and **registered itself** —
`Claude Code (box-out-remote)`, redirect `http://localhost:3118/callback` —
then reported `! Needs authentication`. The last leg opens a browser and
waits on that loopback port, which cannot be driven from an agent's shell,
so the browser leg was done by the SDK client above. With an OAuth-issued
token supplied as a header instead, `claude mcp list` says
`box-out-oauth: http://127.0.0.1:8787/mcp (HTTP) - ✔ Connected`, and
`claude -p "Call the my_leagues tool…"` answered
"`as` = `member@example.com`; 0 leagues returned." — a real host, a real
tool call, through the resource server, on a token this flow minted.

### Deploying it (the owner's steps)

Local work only above this line; nothing below has been run.

1. **Deploy and migrate.** On the VPS, in `/opt/fcp-core`:
   `git pull`, then `./.venv/bin/alembic upgrade head` (migration `0030`,
   `oauth_clients` and `oauth_codes`).
2. **Two lines in `/opt/fcp-core/.env`:**
   ```
   FCP_MCP_PUBLIC_URL=https://mcp.boxoutfantasy.com
   ```
   `FCP_PUBLIC_URL=https://boxoutfantasy.com` is already there and is what
   the co-manager names as its authorization server. `FCP_AUTH_MODE` must be
   `accounts`: in single mode `/oauth/authorize` refuses outright, because
   single mode makes every request the owner and the flow would hand out the
   owner's token to whoever asked. `python scripts/preflight_public.py`
   prints both, and the front door's own line, without printing a secret.
3. **A DNS record at Cloudflare**, DNS only — **the grey cloud**, for the
   same reason the other two are (Caddy gets its own certificate, and a
   proxy would hide the real client address):

   | Type | Name | Content | Proxy |
   |---|---|---|---|
   | A | `mcp` | `178.105.181.43` | DNS only |

4. **A Caddy block.** Back the file up first, by date, as docs/cutover.md
   does, then append to `/srv/fullcourtpress/Caddyfile`, leaving every
   existing block exactly as it is:
   ```
   mcp.boxoutfantasy.com {
     reverse_proxy 127.0.0.1:8787
     encode gzip
     header {
       X-Content-Type-Options nosniff
       X-Frame-Options DENY
       Referrer-Policy strict-origin-when-cross-origin
     }
   }
   ```
   No `log` block: a bearer does not travel in the URI, but nothing here
   needs writing down either. The file is mounted read-only into the
   container, so validate and reload **inside** it, and **never restart that
   container** — it serves two other sites:
   ```
   docker exec fullcourtpress-caddy-1 caddy validate --config /etc/caddy/Caddyfile
   docker exec fullcourtpress-caddy-1 caddy reload  --config /etc/caddy/Caddyfile
   ```
   **As deployed (2026-09-23), two corrections to the above.** Caddy runs in
   a container, so `127.0.0.1` in its config is the container's own loopback
   and the block answered `502`; the authenticated MCP unit binds the tailnet
   address instead (`--host 100.105.64.94`, unreachable from the internet,
   the same address the site's block proxies to) and the block reads
   `reverse_proxy 100.105.64.94:8787`. And the Caddyfile is a **file** bind
   mount: `sed -i` (or `mv`) replaces the inode and the container keeps the
   old file, so every later `reload --config /etc/caddy/Caddyfile` reverts
   to it. Edit it in place only (`sudo tee`, `tee -a`); if an inode has been
   swapped, load the host's copy by hand until the container is next
   recreated: `docker cp /srv/fullcourtpress/Caddyfile
   fullcourtpress-caddy-1:/tmp/Caddyfile` then validate and reload with
   `--config /tmp/Caddyfile`. Caddy passes the browser's `Host` through, and
   the SDK's DNS-rebinding guard would answer `421` to every request behind
   it — so `mcp.boxoutfantasy.com` is added to the allowed hosts from
   `FCP_MCP_PUBLIC_URL` (`app.mcp.server.transport_security`), which is why
   step 2 comes before step 4.
5. **Restart the MCP unit**: `systemctl restart fcp-core-mcp`. Its banner on
   stderr now says `protected resource https://mcp.boxoutfantasy.com/mcp,
   authorization server https://boxoutfantasy.com`. If `FCP_MCP_PUBLIC_URL`
   is missing it exits 2 without serving: a public server that cannot name
   itself must not run open.
6. **Check it**, from the laptop:
   ```
   curl -s https://mcp.boxoutfantasy.com/.well-known/oauth-protected-resource/mcp
   curl -si -X POST https://mcp.boxoutfantasy.com/mcp -d '{}' | grep -i www-authenticate
   curl -s https://boxoutfantasy.com/.well-known/oauth-authorization-server
   ```
   then add `https://mcp.boxoutfantasy.com` in Claude and walk the flow.

**One thing this changes: the ChatGPT tunnel.** `fcp-core-mcp.service` runs
`--http` with no flag today and the tunnel sends no bearer, so the moment
auth is on, ChatGPT gets a `401`. Two honest ways out, and the second is the
recommendation:

- Leave the tunnel behind, now that an app can sign in properly. ChatGPT can
  add the public server the same way Claude does.
- Or run a second, loopback-only process for it: copy
  `fcp-core-mcp.service` to `fcp-core-mcp-tunnel.service` with
  `--http --host 127.0.0.1 --port 8788 --no-auth`, point
  `~/.config/tunnel-client/boxout.yaml` at `http://127.0.0.1:8788/mcp`, and
  restart `openai-tunnel`. `:8787` then serves the public name with auth on
  and `:8788` serves the tunnel as the owner. Nothing proxies to `:8788`.

### Security

- **PKCE S256 or nothing.** `plain` is not implemented, not advertised and
  not accepted; an authorization request without a challenge is sent back to
  the app as `invalid_request`. The verifier is compared in constant time,
  because that comparison is the whole proof that the app finishing the flow
  is the one that started it.
- **Exact redirect match.** A redirect URI is compared to the registered
  string exactly — never by prefix, never by host — and only `https://` or
  `http://` on `localhost`/`127.0.0.1` is stored at all (RFC 8252). An
  unknown app, or a redirect URI it did not register, is answered *to the
  reader as a page* and never redirected: there is nowhere trusted to send
  that error.
- **Codes are single-use and short.** Ten minutes; spending one is a single
  `UPDATE` matching only an unused, unexpired row belonging to that client,
  so two requests racing on one code cannot both win, exactly as a sign-in
  link's second click cannot. A wrong verifier burns the code rather than
  leaving it for a second try. Expired, spent, never issued and another
  app's all answer the same `invalid_grant`.
- **Only hashes.** The code's sha256 and the token's sha256 are what is
  stored; the raw strings exist in one response each and nowhere else, not in
  a log and not in the database. There is no client secret to steal.
- **Registration is open and rate-limited** — ten per address, then one a
  minute, the shape `POST /auth/sign-in` uses. Open is what dynamic
  registration means; a row opens nothing until a manager has signed in and
  pressed Allow.
- **No open redirect through `next`.** The path the sign-in link comes back
  to goes through `app.accounts.safe_next`: a local absolute path, no scheme,
  no `//host`, no backslash.
- **The consent page names the client**, escaped — `client_name` is a string
  an app chose for itself — and says which account it is about to act as.
- **CSRF on the consent form.** The cookie is `SameSite=Lax`, so a
  cross-site POST does not carry it; on top of that the form carries a token
  derived from this browser's own session, compared in constant time. One
  browser's token is not another's.
- **Single mode refuses the flow.** Every request there is the owner, so an
  authorization endpoint would hand out the owner's token to whoever asked.
  `/oauth/authorize` and `/oauth/consent` answer a plain page saying so, and
  the preflight fails a server in single mode.
- **With auth on, `BOX_OUT_TOKEN` is not a fallback.** A call with no bearer
  gets the 401, never the owner's leagues.
- **Public URLs come from settings, never from `Host`.** Both metadata
  documents and every endpoint in them are built on `FCP_PUBLIC_URL` and
  `FCP_MCP_PUBLIC_URL` (docs/cutover.md's rule, and `app.api.auth._link`'s).

## The tools

Fourteen, each a thin call into the function its route calls. Sizes are the
2026 season of ESPN league 3853870 on day 52 — a fourteen-team league with
eight seasons of history — as the model receives them (pretty-printed JSON);
tokens are counted with a cl100k tokenizer as a stand-in, so read them as
within ten per cent rather than exact.

| Tool | Returns | Result size |
|---|---|---|
| `my_leagues()` | the leagues and teams this token may read, and which teams' plans are its owner's | 2.5k chars, ~730 tokens |
| `league_context(league_id, season)` | the rules, roster and IR slots, adds a period, FAAB, the calendar, today's scoring period, the playoff weeks, and all six calibrated numbers with their source and sample | 11k chars, ~3,200 |
| `week_report(league_id, season, team_id, today?)` | the matchup as projected, the moves worth a look in full, the best of the rest in brief, the empty days, the bar | 12k chars, ~3,750 |
| `season_report(...)` | the best move of each kind, drop candidates, stashes, the churn guard, both bars | 9k chars, ~2,900 |
| `todays_lineup(...)` | the proposed lineup place by place beside the one that is set, and the places that will produce nothing tonight | 6k chars, ~1,900 |
| `what_changed(league_id, season, team_id?, since?, until?, today?)` | injuries, adds, drops, claims and trades as one sentence each, newest first | 2.9k chars, ~800 (two days of a real week) |
| `standings(league_id, season)` | every team's matchup and category record | 4k chars, ~1,230 |
| `projected_standings(league_id, season, today?)` | the record each team is projected to end on, its finishing odds, and the record of the method (docs/projected_record.md) | 8.5k chars, ~2,740 |
| `matchup(league_id, season, team_id, period?)` | one team's matchup, each side's nine as ESPN stored them | 1.7k chars, ~530 |
| `recent_moves(league_id, season, days?)` | the league's roster moves in a window, failed claims included | 0.8k chars, ~260 (a week) |
| `player_card(player_id, league_id?, season?, today?)` | his line per game and per week, games left, playoff games, status, and what the rate rests on | 1.4k chars, ~530 |
| `free_agents(league_id, season, team_id, today?, sort?)` | the wire priced at the league standard, best fifteen of however many | 8k chars, ~2,970 |
| `what_if(league_id, season, team_id, drop?, add?, to_ir?, today?)` | one pickup the manager names: this week's nine before and after, every remaining week, and the projected finish either way, beside the week report's own number for the same move (docs/what_if.md) | 8.7k chars, ~2,420 |
| `judge_trade(league_id, season, team_id, with_team, give, get, drop?, their_drop?, fill?, their_fill?, today?)` | both sides' nine categories before and after, the number, the playoff lens, each side's projected finish, and the league's trade record verbatim | 21k chars, ~5,850 |

**`what_changed` takes a scoring period.** Its route's default window is the
last twenty-four hours of real time, which on a season stored months ago is
empty and says nothing at all — the first thing the tool traffic showed.
Given `today`, the window is that day and the one before it, which is what
the This week page shows.

A turn that answers "what should I do this week" costs about 9,000 tokens of
tool results (`league_context`, `todays_lineup`, `week_report`,
`what_changed`); a trade answer about 8,300 (`league_context` and
`judge_trade`). That is what an in-site bot would pay per turn before its
own prompt.

### An example call, and what comes back

```
judge_trade(league_id=3853870, season=2026, team_id=3, with_team=1,
            give=[3133628], get=[4397424], today=52)
```

Myles Turner for Neemias Queta, our team 3 with team 1, on day 52 of the
stored 2026 season. The answer as it came back on 2026-09-22, with the six
other categories and the other side's detail elided and marked as such. It is
kept as the transcript it is: the `chance_*` figures below are from before the
weekly spread was widened on 2026-09-23 (docs/spread_revision.md), so every
one of them would now sit nearer a half, and the counts, the per-week number
and the shape of the payload are unaffected.

```json
{
  "season": 2026,
  "judged_on_day": 52,
  "effective_day": 53,
  "weeks_remaining": 12.571,
  "sides": [
    {
      "espn_team_id": 3,
      "team_name": "Through The Wire",
      "summary": "Gains field-goal percentage and rebounds; gives up threes and
                  blocks. Costs about 0.05 categories a week over the 13 weeks
                  it covers, -0.66 on the season. It costs 0.25 categories in
                  the week in front of it. Does not clear the 0.20 bar. Over
                  the playoff weeks alone it is -0.09 a week.",
      "categories": [
        {"category": "FT%", "before": 0.834, "after": 0.831, "delta": -0.002,
         "chance_before": 0.796, "chance_after": 0.783, "chance_delta": -0.013,
         "moved": true},
        {"category": "REB", "before": 189.4, "after": 196.753, "delta": 7.352,
         "chance_before": 0.359, "chance_after": 0.422, "chance_delta": 0.063,
         "moved": true}
      ],
      "receives": [{"espn_player_id": 4397424, "name": "Neemias Queta",
                    "worth_a_week": 0.602, "games_left": 40,
                    "playoff_games": 10, "games_so_far": 22}],
      "gives":    [{"espn_player_id": 3133628, "name": "Myles Turner",
                    "worth_a_week": 0.615, "games_left": 39,
                    "playoff_games": 11, "games_so_far": 25}],
      "places": {"opened": 0, "filled": 0, "left_open": 0,
                 "what_an_open_place_is_worth": 0.0},
      "net": -0.655, "per_week": -0.05, "clears_hurdle": false,
      "expected_per_week_before": 4.777,
      "judgement": {"delta_week": -0.246, "delta_season_per_week": -0.034,
                    "weeks_remaining": 12.0, "delta_total": -0.655,
                    "per_week": -0.05, "replacement": 0.417,
                    "record_without": [95.8, 75.2],
                    "record_with": [95.2, 75.8], "measured": true},
      "playoffs": {"measurable": true, "weeks": 3.0, "games": 21,
                   "delta_per_week": -0.086, "delta_total": -0.258},
      "notes": []
    },
    { "espn_team_id": 1, "team_name": "Foxes ShutUpNDribble",
      "net": 0.816, "per_week": 0.063, "clears_hurdle": false,
      "...": "the other side, judged with the same machinery" }
  ],
  "hurdle": 0.2,
  "notes": ["the wire is rebuilt from who played and was in nobody's lineup,
             because the listener never ran for this season; it cannot see a
             free agent who did not play"],
  "trade_record": "This number is a forecast, and here is its record. Over the
                   55 trades in this league's history that can be replayed, it
                   pointed at the side that did better in 25 of them...",
  "language": "`clears_hurdle` is a label, not advice... And the other side's
               numbers are our estimate of his roster's needs...",
  "provenance": { "...": "see below" }
}
```

Both sides' `clears_hurdle` are false and the two `net` figures do not
mirror, which is the point of judging on each roster rather than on a
league-average one (docs/trades.md section 7a).

## The provenance block

Every result carries one. It is the pages' habit of printing a bar's note
under the bar and a projection's `source_note` under the projection, moved
into the payload because a model has no page to print under.

```json
"provenance": {
  "league_id": 3853870,
  "season": 2026,
  "as_of": {"scoring_period": 52, "date": "2025-12-11", "stored_report": false},
  "calibration": {
    "stream_hurdle": {
      "means": "the bar a move this week has to clear to be worth a look",
      "value": 0.2, "source": "owner", "n": 0, "unit": "decision points",
      "measured_at": "2026-09-18T00:00:00+00:00",
      "note": "your choice, 2026-09-18: the least churn for no loss, with a
               finite add budget and finite FAAB"
    },
    "typical_pickup": {
      "value": 0.06, "source": "measured", "n": 924, "unit": "adds",
      "note": "measured on this league, 924 adds"
    },
    "opened_place": {"value": 0.38, "source": "measured", "n": 1536, "...": "..."}
  },
  "projection": {"source_note": "ESPN's projections, 2026 season, from the
                                 stored box scores"},
  "injuries": {"source": "nba_official",
               "reported_at": "2025-12-11T14:30:00+00:00",
               "read_as_of": "2025-12-11T15:00:00+00:00",
               "note": "the league's own report, as of that moment"},
  "read_only": "nothing here can add, drop, bid or accept anything on ESPN"
}
```

`source` is the accessor's (`app.calibration`): `owner` beats `measured`
beats `pooled` beats `default`, and a `default` is a number measured on
somebody else's league. `stored_report` says whether the answer came from
the morning's precompute or was built for this call. `injuries.reported_at`
is the moment of the newest NBA injury report this answer could see; null
with the note saying the league said nothing, which is not the same as a man
being fit (docs/injuries.md).

The skill holds the model to saying the `source` and the `n` in words
whenever it quotes the number.

## What is trimmed, and why

The rule is: **keep every number and every reason; drop what only a page
needs.** `app/mcp/trim.py` has the argument; in short —

- **Lists are cut, never summarised away**, and each cut list says how long
  it really was (`of`), so "of 412 free agents" is a thing the model can say
  and be right about. The wire is cut to 15, the ranked moves to 8, drop
  candidates to 5, a day's empty-place fillers to 3.
- **The moves that clear the bar keep everything**; the ones under it keep a
  name, a number and the label. That one decision is a third of a week
  report's size.
- **A player becomes five or six fields** — name, ESPN id, position, NBA
  team, status, games left — and his waiver dates only when he is actually
  on waivers.
- **Floats are rounded to three places.** Categories a week is measured on a
  few hundred decision points; its fourth decimal is noise with a token
  price.
- **Nothing about the fit is dropped.** Every category view a trade produces
  survives whole, both sides, including the playoff lens.
- **`judge_trade` says the trade record once.** It is in the answer, verbatim,
  where a manager reads it; the provenance entry points at it rather than
  repeating 1,200 characters.
- **A `finish` keeps its noise and its record, and loses its weeks on a
  trade.** `what_if` keeps the week-by-week list, because "which week does he
  help" is the question a manager asks next; `judge_trade` drops it, because a
  deal has two sides of a dozen rows each and nobody reads twenty-four of them
  aloud. What never goes is `odds_band`, `moved_more_than_the_band`, the noise
  sentence and the projected record's own calibration note: the two odds
  without them would be a precision the simulation does not have.

What is dropped outright: ESPN ids repeated inside nested objects, the
layout fields the pages need (`describe` strings beside the parts they are
built from, `moved` flags on categories that did not move), and the
per-player arrays a page draws a grid from.

## Resources and the prompt

Six documents, read-only, under `boxout://notes/…`: `pickups`, `trades`,
`streaming_lane`, `pickups_backtest`, `intake`, `injuries`. They are the
arguments the pages were written out of, so a model asked *why* a bar is
0.20, or why a trade number is printed under a record, answers from the same
reasoning the site was built on rather than from its own sense of what is
probably true. Read from disk per call, like every page here.

One prompt, `co_manager`: the house rules in the voice of
docs/in_season_pages.md's "The language" — worth a look, never recommended;
the bar labels and never hides; every number with its sample; nothing acts
on ESPN.

## What is deliberately absent

- **`find_trades`.** Nobody has built it. A tool that returned a plausible
  list of deals would be the model producing numbers with our name on them,
  and nothing has measured whether a proposed trade is one the other manager
  would take (docs/trades.md section 9).
- **Anything that writes.** No add, no drop, no bid, no claim, no accept, and
  no path from here to ESPN. `tests/test_mcp.py` checks the tool names for
  verbs rather than trusting this paragraph.
- **A verdict.** No field says accept or reject. `clears_hurdle` is a label
  and the payload says so in words.
- **A per-player projection from a gated source.** Everything here is ESPN's
  own or derived from stored box scores (`app.projections.sources`). A tool
  that ever carries Basketball Monster's numbers has to ask `may_show`
  first, exactly as the draft screen's pool does.

## Errors

A refusal is one sentence, and it is the site's own. "This team's plan is
its manager's." for a team that is not yours; "not a member of this league";
the route's own 422 for a deal that cannot be read ("Through The Wire does
not have Bam Adebayo on its roster on day 52."); the 409 for a season with
nothing to report on. Never a stack trace, and never a hint about whether
the league or the team exists.

The SDK prefixes a tool error with "Error executing tool `<name>`:", so the
sentence arrives inside that. Everything after the colon is ours.

## The live run, 2026-09-23

The three conversations the brief asked for were driven on 2026-09-23 from
the owner's laptop, in `claude -p` with `.mcp.json`, `--strict-mcp-config`,
only `mcp__box-out__*` allowed, and `skills/box-out-co-manager/SKILL.md`
appended to the system prompt, against the local database on the stored
2026 season. Each transcript was then read tool call by tool call, and
**every number the model stated was compared with the tool results it had
been given.** The transcripts are the session's stream-json files; the
check was by hand.

The run was made that morning, **before** the weekly spread was widened later
the same day (docs/spread_revision.md). What was measured here is the model's
discipline, which does not depend on the spread: it quoted what the tools gave
it. But the figures the table names as stated -- the expected wins, the nine
chances, the nets and the projected record -- are the old model's, and the same
three questions asked today would return smaller ones.

| question | tools called | turns | numbers stated | not in a tool result |
|---|---|---|---|---|
| "day 52 — what should I do today and this week?" | `my_leagues`, `league_context`, `todays_lineup`, `week_report`, `what_changed` | 8 | 5.24 expected wins, nine chances, two moves' nets and bids, 42 FAAB, 0 of 7 adds, 95.8–75.2, three bars with their sources and dates, eight league moves | **none** |
| "judge Turner for Queta with Foxes" | `league_context`, `my_leagues`, `season_report`, `todays_lineup` ×2, `judge_trade` | 10 | nine categories before/after with chances, both sides' per-week and season numbers, playoff-weeks split, both men's worth/games/playoff games, the 25-of-55 record | **none** |
| "what's a streaming spot worth here and why?" | `my_leagues`, `league_context` | 4 | 0.38 (n=1,536), 0.06 (n=924), the three bars, roster shape, FAAB budget | **none** |

**So the claim is now a measurement, on three conversations:** the model
quoted no number a tool had not returned. It also did three things the
skill asks for without being prompted: it named the source and sample of
every calibration number ("measured on this league, 924 adds"; "your own
choice, dated 2026-09-18"); it called the other side of the trade "as our
projections estimate his roster's needs — never his opinion"; and it ended
each answer with a line that nothing here reaches ESPN.

Two things worth knowing from the transcripts:

- In conversation 1 the model noticed that Gary Trent Jr. for Jordan Walsh
  is labelled `clears_hurdle: true` at a net of 0.024, under the 0.20 bar,
  and said so rather than smoothing it over. The label is by design
  (`app.pickups.stream.Move.clears`: a move that fills an empty day clears
  on any positive net), but the payload did not say so; `LABEL_ONLY` now
  does.
- In conversation 2 it dated the 0.20 bar to 2026-09-18 (the week bar's
  date) where `judge_trade`'s provenance cites the season bar, dated
  2026-09-21. Same value, same source, wrong date — a misreading of two
  entries with the same number, not an invented one.

Cost, for the in-site bot's pricing: 0.73, 0.88 and 0.55 dollars a
conversation on the model the CLI runs, of which nearly all is cached
input; 4,100, 3,626 and 2,525 output tokens. Wall time 87, 85 and 33
seconds.

**Three conversations is a smoke test, not a study.** What it rules out is
the failure the design exists to prevent showing up on the first three
questions a manager would ask; it does not rule it out on the tenth.

## Decisions

- **The tools call the route functions, not HTTP.** A tool that fetched our
  own API would need a URL, a port and a second copy of the auth, and would
  answer differently the moment either drifted. Calling
  `app.api.pickups.report` means the page and the tool cannot be told two
  different things; `tests/test_mcp.py` holds them to it.
- **`app.api.pickups._report` became `report`.** It is the one function that
  says whether an answer came from the morning's stored row, and a tool has
  to be able to say that too.
- **A token has no scopes.** The alternative — a read-only token, a
  team-scoped token — is a second answer to "who may open what", and the
  first answer is already written down and tested (docs/accounts.md).
- **Single mode still checks the token.** Single mode enforces nothing else,
  but a revoked token that kept working on the only server this has run on
  would make revoking meaningless.
- **The refusals are the site's sentences, not new ones.** A manager who has
  seen "This team's plan is its manager's." in the browser should read the
  same words from his co-manager.
- **The language rules are in the payload**, not only in the skill. A cheaper
  model reading `clears_hurdle` with no instructions is one prompt-injection
  or one context-trim away from calling it a recommendation; the field next
  to it says what it is.
- **Three decimals.** A quantity measured on 616 decision points has no
  fourth decimal worth paying for, and the trim's rounding is the same
  rounding the equality tests compare against, so nothing is hidden by it.

### The front door (2026-09-23)

- **The access token IS a `bo_` token.** The alternative — an OAuth token of
  its own, with its own table, its own expiry and its own resolver — is a
  second answer to "who may open what", and a second thing to revoke. This
  way a manager sees one list on one page, `resolve_viewer` has one path, and
  `tests/test_mcp_access.py`'s promise ("a token is its owner, and nobody
  else") covers the OAuth ones for free.
- **The authorization server is the site's own code, not the SDK's routes.**
  `mcp.server.auth.routes.create_auth_routes` is spec-correct and was read
  closely, but it mounts Starlette routes at fixed root paths derived from
  the issuer (`/authorize`, `/token`, `/register`), and `provider.authorize`
  is handed a client and params with no request — no cookie to read, no
  session to check, nowhere to render a consent page. The thing an
  authorization server actually has to do here is *be certain who is at the
  keyboard*, and everything that knows that (the magic link, `fcp_session`,
  `resolve_viewer`, `SignInRequiredError`) is a FastAPI dependency on the
  site. Bolted-on Starlette routes would also sit outside
  `tests/test_access.py`'s "every route declares exactly one check". So the
  site serves `/oauth/…` itself, in its own house style, and the SDK is used
  for exactly the half it fits: the resource server, where `AuthSettings`
  plus a `TokenVerifier` give the protected-resource metadata and the
  `WWW-Authenticate` 401 for nothing.
- **No refresh token.** Argued above, under the heading of its own: a
  refresh token wrapped around an access token that never expires is
  ceremony, and the metadata would be saying something the tokens do not do.
  The lifetime is the one a manager can see on his Connections page.
- **A token is not audience-restricted.** `validate_token_resource` is False
  and the `resource` an app asks for is recorded rather than enforced. RFC
  8707's point is to stop a token issued for one resource being replayed at
  another — but here both resources are ours, the same manager, the same
  checks, and the token he pastes into a config file by hand was never bound
  to either. Binding it between our own two front doors would buy nothing and
  would break that one.
- **Single mode refuses the flow outright.** It would otherwise be the
  sharpest edge in the whole design: single mode means every request is the
  owner, so `/oauth/authorize` would hand the owner's leagues to any stranger
  who walked it. A one-line guard and a plain page beats a footnote.
- **Registration is never updated.** A caller holding no secret has proved
  nothing that would entitle it to change an existing app's redirect URIs, so
  every registration is a new row. An app that wants different URIs registers
  again, which is what dynamic registration is for.
