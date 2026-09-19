# Accounts: signing in, and who may open what

**Written:** 2026-09-19. **Status:** built, step 1 of docs/product.md. The VPS
runs in single mode, which behaves exactly as the API always has; accounts
mode is switched on at the cutover (step 5), not before.

Code: `app/accounts.py` (the database half), `app/api/access.py` (the checks
every route declares), `app/api/auth.py` (the sign-in routes and the two
small pages), migration `0018_accounts`. Tests: `tests/test_access.py`, which
runs accounts mode; every other API test runs single mode.

## The flow

1. **Ask.** `GET /sign-in` is a form. It posts `{email, next}` to
   `POST /auth/sign-in`, which creates the user if the address is new (stored
   lower-cased), writes a one-time token and mails the link
   `…/auth/callback?token=…`. The answer is the same 202 and the same sentence
   whether the address is known or not, sent or not.
2. **Click.** `GET /auth/callback?token=` spends the token in one `UPDATE`
   that matches only an unused, unexpired row, so a link works once and for
   fifteen minutes, and two clicks racing on it cannot both win. It starts a
   session, sets the `fcp_session` cookie and redirects (303) to `next`, or
   to `/`. A bad, used or expired link is a 400 page with a way to ask for a
   new one.
3. **Use.** Every request after that carries the cookie. A session lasts
   thirty days from sign-in; `last_seen_at` is written at most every five
   minutes.
4. **Leave.** `POST /auth/sign-out` revokes the session (it stays in the
   table, with `revoked_at`) and clears the cookie. A replayed cookie is dead.

`GET /auth/me` says who is signed in, how (`single`, `session` or
`service`), whether they are the owner, their leagues and teams (from
`team_managers`) and their entitlement. `GET /` is a placeholder home until
step 3's shell: your leagues, links to your own team's pages, sign out.

**Where the link goes.** When SMTP is configured (`FCP_SMTP_HOST` and
`FCP_EMAIL_FROM`) *and* `FCP_PUBLIC_URL` is set, the link is mailed by
`app.notify.send_email` to the address that asked. When SMTP is not
configured, which is development and the tests, the link is logged at INFO
on the `fcp.auth` logger and nothing is mailed. It is never in a response.
When SMTP is configured but `FCP_PUBLIC_URL` is not, sign-in answers 503: a
mailed link is only ever built on the configured address, never on the
request's `Host` header, which the caller writes.

**The pages.** A page route asked for by someone signed out redirects to
`/sign-in?next=<the page>`, so the link lands back on it. The pages' script
(`app/api/static/pages.js`, `get()`) does the same on a 401 from any fetch,
and on a 403 shows one plain line: "This team's plan is its manager's."

## The two modes

`FCP_AUTH_MODE` in `.env`.

**`single`** (the default, and the VPS today). Every request is the owner,
`FCP_OWNER_EMAIL` (or `owner@localhost` when unset, an address nobody can
sign in as). No cookie is needed and every check answers yes: the owner is
an operator who can read any team's plan, which is what Patrick has always
been able to do on the tailnet. The owner's rows are written all the same, on
first use (`app.accounts.ensure_owner`): the user, an `owner` entitlement,
and a verified claim on `FCP_TRACKED_TEAM_ID`'s team in every season of
`ESPN_LEAGUE_ID`. So `/auth/me` tells the truth, and accounts mode finds the
rows already there. If the code is deployed before migration 0018 has run,
single mode logs a warning and serves as before rather than failing.

**`accounts`**. Everything is enforced. A request is signed in by the
cookie, or by `Authorization: Bearer <FCP_SERVICE_TOKEN>`, which is the
owner, for the scheduled scripts (`scripts/warm_pages.py` sends it when it
is set). A bearer that is not the service token is a 401, never a fallback
to the cookie. In this mode the owner is an ordinary user with the owner's
claims: his own team's plan, and not every team's.

## The checks

Four questions, as FastAPI dependencies in `app/api/access.py`. Every route
declares exactly one (the team layer's one is the manager check and the
entitlement check together), and `tests/test_access.py` fails if a route is
added without one or with two, or is missing from the table below.

- **Signed in** (`current_user`): 401 otherwise.
- **League member** (`require_league_member`): a verified manager of a team
  in this league, in any season. 403 otherwise. A league that does not
  exist is also a 403, so the answer says nothing about which leagues do.
- **Team manager** (`require_team_manager`): a verified manager of this team
  in this season. 403, "This team's plan is its manager's.", otherwise,
  including for a team that does not exist.
- **Entitled** (`require_entitlement`): a live `team` entitlement (no
  `valid_until`, or one in the future). 402 otherwise. **Answers yes for
  everyone while `BILLING_ENABLED = False`** (a module constant in
  `app/api/access.py`), which it is until step 7. Turning the paywall on is
  that constant and a payment provider writing `entitlements`, not a change
  to any route.

The pages have twins of the league and team checks (`*_page`) that redirect
a signed-out browser to `/sign-in` and answer a refusal with one line of
HTML instead of JSON.

"Member" means a verified claim in `team_managers` for now, the minimal form
of step 2's team claims. Step 2 adds `memberships` (an invite makes a member
before any claim is verified), and the league check then reads that.

## Every route and its scope

| Route | Scope | Why |
|---|---|---|
| `GET /health` | open | says nothing but "ok" |
| `GET /sign-in` | open | the form |
| `POST /auth/sign-in` | open, rate-limited | asking for a link |
| `GET /auth/callback` | open | the link itself is the credential |
| `POST /auth/sign-out` | open | acts only on the caller's own cookie |
| `GET /pages/static/{name}` | open | the shared CSS and JS, no data |
| `GET /auth/me` | signed in | the caller's own account |
| `GET /` | signed in (page) | the caller's own leagues |
| `GET /leagues` | signed in, filtered | lists only the caller's leagues (single mode: all) |
| `GET /ingest-runs` | signed in | ingest history, no league member's data |
| `GET /ingest-runs/health` | signed in | whether the data is current |
| `GET /ingest-runs/health/{season}` | signed in | the same, for a season |
| `GET /players` | signed in | NBA players, global |
| `GET /players/{player_id}` | signed in | one NBA player |
| `GET /players/{player_id}/games` | signed in | his box scores, global |
| `GET /players/{player_id}/news` | signed in | ESPN's news about him, global |
| `GET /players/{player_id}/status` | member of the followed league | carries `on_team_id`: who in `ESPN_LEAGUE_ID` holds him |
| `POST /projections/sets/preview` | signed in | the caller's own file |
| `POST /projections/sets` | signed in | stored with the caller as owner |
| `GET /projections/sets` | signed in, own sets only | a set is its owner's |
| `GET /projections/sets/{set_id}` | signed in, own set only (404 otherwise) | |
| `GET /projections/sets/{set_id}/rows` | signed in, own set only (404 otherwise) | |
| `GET /leagues/{league_id}/seasons` | league member | league history |
| `GET /leagues/{league_id}/seasons/{season}` | league member | settings |
| `GET /leagues/{league_id}/seasons/{season}/teams` | league member | teams and owners' names |
| `GET /leagues/{league_id}/seasons/{season}/standings` | league member | |
| `GET /leagues/{league_id}/seasons/{season}/periods` | league member | |
| `GET /leagues/{league_id}/seasons/{season}/matchups` | league member | |
| `GET /leagues/{league_id}/seasons/{season}/draft` | league member | the draft board |
| `GET /leagues/{league_id}/seasons/{season}/draft-value` | league member | the board graded |
| `GET /leagues/{league_id}/seasons/{season}/transactions` | league member | |
| `GET /leagues/{league_id}/seasons/{season}/contested-claims` | league member | |
| `GET /leagues/{league_id}/seasons/{season}/events` | league member | the listener's status changes |
| `GET /leagues/{league_id}/seasons/{season}/streaks` | league member | narratives |
| `GET /leagues/{league_id}/seasons/{season}/category-profiles` | league member | narratives |
| `GET /leagues/{league_id}/seasons/{season}/bench-leaderboard` | league member | narratives |
| `GET /leagues/{league_id}/seasons/{season}/worst-bench-calls` | league member | narratives |
| `GET /leagues/{league_id}/seasons/{season}/notable-matchups` | league member | narratives |
| `GET /leagues/{league_id}/seasons/{season}/projection-gaps` | league member | narratives |
| `GET /leagues/{league_id}/owners` | league member | history, all seasons |
| `GET /leagues/{league_id}/head-to-head` | league member | history, all seasons |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/lineups` | league member | who sat where: ESPN shows members every lineup |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/bench` | league member | a narrative about one team |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/scorecard` | league member | "every team's scorecard" is league scope (docs/product.md) |
| `GET /leagues/{league_id}/seasons/{season}/pages/context` | league member | names and the day, for the pages |
| `GET /pages/teams/{league_id}/{season}` | league member (page) | the league index |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream` | team manager + entitled | the week plan |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season` | team manager + entitled | the season plan, drops and bids |
| `GET /pages/teams/{league_id}/{season}/{team_id}/week` | team manager + entitled (page) | the week page |
| `GET /pages/teams/{league_id}/{season}/{team_id}/season` | team manager + entitled (page) | the season page |

FastAPI's own `/docs`, `/redoc` and `/openapi.json` stay open: they
describe the routes and carry no data.

Two calls worth revisiting when billing is designed: projection uploads are
in the paid team layer in docs/product.md but are "signed in" here, as
asked for step 1; and the scorecard is league scope here while "the
scorecard of your own moves" is listed as paid.

## Switching the VPS to accounts mode (at the cutover, not now)

The API stays tailnet-only until this is done. In order:

1. Deploy, and run the migrations: `alembic upgrade head` (0018).
2. In the VPS `.env`:
   - `FCP_OWNER_EMAIL=` Patrick's address (the one he will sign in with).
   - `FCP_PUBLIC_URL=https://fcp.patrickmcdowell.dev`.
   - `FCP_SMTP_HOST`, `FCP_EMAIL_FROM` (and the SMTP login) if not already
     set for the digest; sign-in links need only the host and the sender.
   - `FCP_SERVICE_TOKEN=` a fresh `python -c "import secrets;
     print(secrets.token_urlsafe(32))"`. It is read by the API and by
     `scripts/warm_pages.py` from the same `.env`.
   - `ESPN_LEAGUE_ID` and `FCP_TRACKED_TEAM_ID` are already there.
   - Last, `FCP_AUTH_MODE=accounts`, and restart the API.
3. Run `scripts/warm_pages.py` once and check it prints HTTP 200 (not 401).
4. Sign in at `/sign-in`, open the week page, check `/auth/me`.
5. Then Caddy (step 5): route the domain to the API on localhost, run
   uvicorn with its default `--proxy-headers` so the client address the rate
   limit sees is the real one and the scheme is https (which sets the
   cookie's `Secure` flag; `FCP_PUBLIC_URL` being https sets it too).

To go back, set `FCP_AUTH_MODE=single` and restart. Nothing is lost:
sessions simply stop being asked for.

Projection sets uploaded before accounts carry the label `patrick`. They stay
readable by the owner in accounts mode (`app/api/projections.py`, `_owns`),
so nothing has to be rewritten.

## Security notes

- **No password is stored.** A link is the sign-in.
- **Only hashes.** Every token handed out, link or cookie, is
  `secrets.token_urlsafe(32)` (256 random bits), and only its sha256 is
  stored (`sign_in_tokens.token_hash`, `sessions.token_hash`). A copy of the
  database signs nobody in. The service token is compared by hash, in
  constant time (`hmac.compare_digest`).
- **The cookie.** `fcp_session`: `HttpOnly` (no script reads it),
  `SameSite=Lax` (no cross-site POST carries it, which is the CSRF defence
  for the few routes that write: uploads and sign-out), `Secure` whenever the
  request is https or `FCP_PUBLIC_URL` is, `Path=/`, thirty days.
- **Links.** Fifteen minutes, once. Built only on `FCP_PUBLIC_URL` when
  mailed, never on the Host header. The landing path is a local path or
  nothing (`app.accounts.safe_next`): no scheme, no `//host`, no backslash,
  so a sign-in link cannot be turned into a redirect elsewhere.
- **Rate limits.** In-process token buckets on `POST /auth/sign-in`: per
  address five links, then one a minute; per client address twenty, then one
  every ten seconds (`app/api/auth.py`, `SignInLimits`). In memory, per
  process, reset on restart: enough for one API process behind one proxy,
  which is what there is. Several processes would move them to the database.
- **Nothing to enumerate.** Sign-in answers the same for every address.
  A league or team the caller is not in is a 403 whether it exists or not; a
  projection set that is not his is a 404 like one that was never stored.
- **Secrets in logs.** None, with one exception: the dev link, logged at
  INFO when SMTP is not configured. A failed email logs the address and the
  exception's class only, not its text, which can quote the server. No
  response ever carries a token. Uvicorn's access log writes each request
  line, which for a click is `/auth/callback?token=…`; a filter blanks the
  token there (`app/main.py`, `_RedactTokens`). The token is spent by then,
  but it is not written down. At the cutover, Caddy's access log (if it is
  turned on) needs the same: it logs the URI unless told not to.
- **Single mode is not a security mode.** It is the tailnet API: nothing is
  enforced, and it is only safe because the API binds the tailnet address
  (STATUS.md, "Why the API is tailnet-only").
