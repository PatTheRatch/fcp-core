# Accounts: signing in, and who may open what

**Written:** 2026-09-19. **Status:** built, steps 1 and 2 of docs/product.md
(accounts; then league connections, memberships, invites and verified team
claims); step 3's pages and their checks are in the table below, and
docs/site.md is their map; step 4's routes (each member's alert channels,
`/me/channels`, and the free glance at his own week, `/pickups/glance`) are
in it too, and docs/jobs.md is their story. The VPS runs in single mode, which behaves
exactly as the API always has; accounts mode is switched on at the cutover
(step 5), not before.

Code: `app/accounts.py` (the database half), `app/api/access.py` (the checks
every route declares), `app/api/auth.py` (the sign-in routes and the two
small pages), migration `0018_accounts`. Step 2: `app/memberships.py` (its
database half), `app/api/leagues_admin.py` (its routes and three pages),
`app/secrets_box.py` (sealing), migration `0019_leagues_members_claims`.
Step 3: `app/api/site.py` (the pages on the map, and `/me/alerts`). The
season pass (2026-09-26): `app/billing.py`, `app/api/upgrade.py`,
`scripts/comp_code.py`, migration `0031_comp_codes`; "The pass", below.
Tests: `tests/test_access.py`, `tests/test_leagues_admin.py` and
`tests/test_shell.py`, which run accounts mode; every other API test runs
single mode.

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
`service`), whether they are the owner, their leagues with their role in
each (from `memberships`) and their claims in them (from `team_managers`, of
any state), and their entitlement. `GET /` is the landing page for someone
signed out, and for someone signed in a page whose shell goes to his default
league's This week (step 3, docs/site.md); it is open, and carries no data
either way.

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
sends a 402 to `/upgrade?next=<the page>` (a team page opened without a live
pass is redirected there by the server too), and on a 403 shows one plain
line: "This team's plan is its manager's."
The one fetch that is a part of a page rather than the page itself, a free
league page's look at the reader's own week, asks quietly and words its own
answer (docs/site.md).

## The two modes

`FCP_AUTH_MODE` in `.env`.

**`single`** (the default, and the VPS today). Every request is the owner,
`FCP_OWNER_EMAIL` (or `owner@localhost` when unset, an address nobody can
sign in as). No cookie is needed and every check answers yes: the owner is
an operator who can read any team's plan, which is what Patrick has always
been able to do on the tailnet. The owner's rows are written all the same, on
first use (`app.accounts.ensure_owner`): the user, an `owner` entitlement,
an `owner` membership of `ESPN_LEAGUE_ID` (upgraded from `member` if he had
joined it by an invite), and a verified claim on `FCP_TRACKED_TEAM_ID`'s
team in every season of that league. No sealed connection is needed for
that league: its ingest still reads `ESPN_S2` and `ESPN_SWID` from `.env`
until step 4 moves the jobs onto connections. So `/auth/me` tells the truth,
and accounts mode finds the rows already there. If the code is deployed
before migrations 0018 and 0019 have run, single mode logs a warning and
serves as before rather than failing.

**`accounts`**. Everything is enforced. A request is signed in by the
cookie, by a member's own machine token (`Authorization: Bearer bo_…`, minted
on the account page, `app/api_tokens.py`, docs/mcp.md), or by
`Authorization: Bearer <FCP_SERVICE_TOKEN>`, which is the owner, for the
scheduled scripts (`scripts/warm_pages.py` sends it when it is set). A bearer
that is neither is a 401, never a fallback to the cookie. In this mode the
owner is an ordinary user with the owner's claims: his own team's plan, and
not every team's.

**A machine token carries no scope.** It acts as the man who made it,
through the same dependencies above, so it reads his leagues and his teams'
plans and nothing else. Only its sha256 is stored and it is shown once. That
is what lets a co-manager read for a member without a second answer to "who
may open what" (docs/mcp.md).

**An app can ask for one instead of a manager cutting it by hand.** Since
2026-09-23 the site is also an OAuth 2.1 authorization server
(`app/api/oauth.py`, `app/oauth.py`, migration `0030`, docs/mcp.md): an app
registers itself, the manager is sent to `/sign-in` and back to a consent
page that names the app, and Allow mints `api_tokens.mint`'s own `bo_` token
named after it. It is the same row, the same lifetime and the same
Connections page — there is no second kind of token and no second answer to
who may open what. In **single mode the flow is refused outright**: every
request there is the owner, so an authorization endpoint would hand out the
owner's token to whoever asked.

## The checks

Six questions, as FastAPI dependencies in `app/api/access.py`. Every route
declares exactly one (the team layer's one is the manager check and the
entitlement check together), and `tests/test_access.py` fails if a route is
added without one or with two, or is missing from the table below.

- **Signed in** (`current_user`): 401 otherwise.
- **League member** (`require_league_member`): a row in `memberships` for
  this league, of either role. 403 otherwise. A league that does not exist
  is also a 403, so the answer says nothing about which leagues do. A
  verified team claim alone does **not** make someone a member: membership
  comes from connecting the league or from an accepted invite.
- **League owner** (`require_league_owner`): an `owner` membership of this
  league. Its invites and its claims. 403 otherwise, for a member as for a
  stranger.
- **Team manager** (`require_team_manager`): a verified manager of this team
  in this season. 403, "This team's plan is its manager's.", otherwise,
  including for a team that does not exist.
- **Entitled** (`require_entitlement`): a live season pass, a `team`
  entitlement with no `valid_until` or one in the future ("The pass",
  below). 402 otherwise, and a page goes to `/upgrade` instead. **Answers yes
  for everyone while `FCP_BILLING_ENABLED` is off**, the default (a setting
  since 2026-09-26, read at call time; it was the constant `BILLING_ENABLED`).
  Turning the paywall on is that setting and a restart, not a change to any
  route.

- **Site owner** (`require_site_owner`): the site's owner, `FCP_OWNER_EMAIL`,
  in either mode. The comp codes. 403 otherwise, for a league's owner as for
  anyone else. It reads `is_owner`, not `all_access`: in accounts mode the
  owner is an ordinary user with the owner's claims, and the codes are what
  he needs on the day the site opens to his league.

The pages have twins of the league and team checks (`*_page`) that redirect
a signed-out browser to `/sign-in` and answer a refusal with one line of
HTML instead of JSON.

The team check reads verified rows of `team_managers`, which is step 2's
team claims (the table step 1 began, grown rather than duplicated).

## Every route and its scope

| Route | Scope | Why |
|---|---|---|
| `GET /health` | open | says nothing but "ok" |
| `GET /sign-in` | open | the form |
| `POST /auth/sign-in` | open, rate-limited | asking for a link |
| `GET /auth/callback` | open | the link itself is the credential |
| `POST /auth/sign-out` | open | acts only on the caller's own cookie |
| `GET /pages/static/{name}` | open | the shared CSS and JS, and the icons; no data |
| `GET /favicon.ico` | open | a redirect to the icon under `/pages/static/`, for a browser that asks before reading a page |
| `GET /design` | open | the design language (docs/design_system.md); the file carries no data, and its specimens read league routes behind their own checks, so signed out it draws the language and no numbers |
| `GET /` | open | the landing page signed out; signed in, the shell's home, which finds his league in the browser; no data either way |
| `GET /auth/me` | signed in | the caller's own account |
| `GET /me/alerts` | signed in | where the digest goes: the server's own recipients, to its owner only |
| `GET /account/connections` | signed in (page) | connect, your connections, your leagues' invites and claims |
| `GET /account/projections` | signed in (page) | your own projection sets |
| `GET /account/alerts` | signed in (page) | reads `/me/alerts`, `/me/channels` and `/me/subscriptions`; spends an emailed link's `?token=` |
| `GET /me/channels` | signed in, own only | his alert addresses, each masked; never one in full |
| `POST /me/channels` | signed in, rate-limited | add an address (sealed); a confirmation link is mailed to it, never shown |
| `POST /me/channels/verify` | signed in, own only, rate-limited | spend the link's token, on one of his own channels |
| `DELETE /me/channels/{channel_id}` | signed in, own only (404 otherwise) | disable, and wipe the sealed target |
| `GET /me/subscriptions` | signed in, own only | what goes in his email, per league he is a member of |
| `PUT /me/subscriptions/{league_id}` | signed in, member of that league (404 otherwise) | choose it: the topics, the morning digest, the alerts |
| `GET /me/api-tokens` | signed in, own only | his machine tokens, never their secrets (docs/mcp.md), an OAuth-issued one named after the app that asked |
| `POST /me/api-tokens` | signed in, rate-limited | mint one; the token is in this answer and nowhere else |
| `DELETE /me/api-tokens/{token_id}` | signed in, own only (404 otherwise) | revoke one of his own, however it was made |
| `GET /upgrade` | signed in (page) | the team layer, his pass, "Have a code?", the free tier; where a team page without a pass sends him, with `next` |
| `GET /billing/pass` | signed in, own only | his season pass (live, or the last one that lapsed), whether the team layer is gated, and whether purchase is open: the upgrade page's context |
| `POST /billing/redeem` | signed in, rate-limited | spend a code on his own season pass; one sentence whatever is wrong with the code |
| `GET /billing/codes` | site owner | every comp code, in clear, with who redeemed each and when |
| `POST /billing/codes` | site owner | make one; the code is in the answer and in the list, because he has to send it |
| `POST /billing/codes/{code_id}/revoke` | site owner | it redeems nothing more; the passes it already wrote stand |
| `GET /.well-known/oauth-authorization-server` | open | RFC 8414: what the front door supports. Describes the server and carries no data (docs/mcp.md) |
| `POST /oauth/register` | open, rate-limited | RFC 7591: an app registers itself. No secret is issued, and a row opens nothing until a manager has signed in and allowed it |
| `GET /oauth/authorize` | signed in (page) | the consent page. Signed out it goes to `/sign-in?next=` and comes back to the whole request |
| `POST /oauth/consent` | signed in (page), CSRF | Allow issues one code, bound to the app, the redirect URI, the PKCE challenge and this manager; Deny issues nothing |
| `POST /oauth/token` | open | held to a single-use code and its PKCE verifier. The answer is a `bo_` token, minted here and nowhere else |
| `POST /oauth/revoke` | open | RFC 7009: holding the token is the credential. Always 200, whatever was sent |
| `GET /leagues` | signed in, filtered | lists only the leagues the caller is a member of (single mode: all) |
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
| `GET /leagues/{league_id}/seasons/{season}/changes` | league member | what changed: injuries, adds, drops, claims and trades, all of it league-visible already. `team_id` only flags which of them are a team's own and its opponent's, so it adds no check |
| `GET /leagues/{league_id}/seasons/{season}/projected` | league member | the projected standings: every team's remaining weeks head to head, the projected record and the odds of each finishing place (docs/projected_record.md). Nothing in it is a plan -- the remaining schedule is on the Standings page already -- and a projection only one manager could see would be worth less to everyone |
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
| `GET /leagues/{league_id}/seasons/{season}/players/{player_id}/card` | league member | one player's card, as every in-season page shows it on a name: his line, his games, his status and what the projection rests on |
| `GET /l/{league_id}/{season}/week` | league member (page) | This week, the free tier (docs/site.md) |
| `GET /l/{league_id}/{season}/standings` | league member (page) | Standings |
| `GET /l/{league_id}/{season}/draft` | league member (page) | Draft |
| `GET /l/{league_id}/{season}/history` | league member (page) | History |
| `GET /pages/teams/{league_id}/{season}` | league member (page) | the old index: a 308 to `/l/.../standings`, keeping its check |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/today` | team manager + entitled | who starts today, the bench men with a game, and the places set with a man who is not playing |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream` | team manager + entitled | the week plan |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season` | team manager + entitled | the season plan, drops and bids |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/glance` | team manager | the free This week page's look at his own week: expected categories and the projected record, not the plan (step 4) |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/projected` | team manager + entitled | the league projection narrowed to one team: its weeks and its finish distribution, for the Week page's "Rest of season". The same scope as that page; it carries nothing the league route does not |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/rosters` | team manager + entitled | both rosters as they stood on the day, for the trade builder's pickers |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/pool` | team manager + entitled | the wire on the day, ranked by what each man would be worth in the place this deal opens |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/report` | team manager + entitled | a proposed trade judged from both sides (docs/trades.md) |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/what-if` | team manager + entitled | one named pickup judged: this week, the rest of the season and the projected finish before and after (docs/what_if.md) |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/draft/plan` | team manager + entitled | the pre-auction plan with the manager's marks beside the model's figures; a plan on Basketball Monster only for the owner of that source (`state: "withheld"` otherwise), and `state: "drafted"` once the auction is held (docs/draft_plan.md) |
| `PUT /leagues/{league_id}/seasons/{season}/teams/{team_id}/draft/plan/marks` | team manager + entitled | keep his going prices, ceilings, tags, notes and ladder, each checked first |
| `POST /leagues/{league_id}/seasons/{season}/teams/{team_id}/draft/plan/rebuild` | team manager + entitled | build the model's plan again on the same pool |
| `GET /leagues/{league_id}/seasons/{season}/teams/{team_id}/draft/plan/settings` | team manager + entitled | his fan team |
| `PUT /leagues/{league_id}/seasons/{season}/teams/{team_id}/draft/plan/settings` | team manager + entitled | set his fan team (an NBA abbreviation, or none) |
| `GET /l/{league_id}/{season}/team/{team_id}` | team manager + entitled (page) | the Overview: the team's morning briefing, from routes each behind its own check |
| `GET /l/{league_id}/{season}/team/{team_id}/week` | team manager + entitled (page) | the week page |
| `GET /l/{league_id}/{season}/team/{team_id}/season` | team manager + entitled (page) | the season page |
| `GET /l/{league_id}/{season}/team/{team_id}/moves` | team manager + entitled (page) | the scorecard of the team's own moves, in the paid layer (its data route is league scope) |
| `GET /l/{league_id}/{season}/team/{team_id}/trades` | team manager + entitled (page) | the trade builder and what a deal does to both rosters' nine categories |
| `GET /l/{league_id}/{season}/team/{team_id}/draft/plan` | team manager + entitled (page) | the draft plan page; its data route asks the projection source's gate as well |
| `GET /pages/teams/{league_id}/{season}/{team_id}/week` | team manager + entitled (page) | old address: a 308 to `/l/.../team/{team_id}/week` |
| `GET /pages/teams/{league_id}/{season}/{team_id}/season` | team manager + entitled (page) | old address: a 308 to `/l/.../team/{team_id}/season` |
| `POST /connections` | signed in, rate-limited | connect a league with your own ESPN login |
| `GET /connections` | signed in, own only | your connections, never their cookies |
| `DELETE /connections/{connection_id}` | signed in, own only (404 otherwise) | revoke, and wipe the sealed login |
| `POST /me/espn-identity` | signed in, rate-limited | your own SWID, sealed, to verify your claims |
| `DELETE /me/espn-identity` | signed in | forget it |
| `GET /invites/{token}` | signed in, rate-limited | the league an invite is for; the token is the rest of the credential |
| `POST /invites/{token}/accept` | signed in, rate-limited | join that league as a member |
| `GET /pages/connections` | signed in (page) | old address: a 308 to `/account/connections` |
| `GET /join/{token}` | signed in (page) | where an invite link lands; signed out, it goes to sign in and back |
| `GET /leagues/{league_id}/seasons/{season}/teams/claimable` | league member | the season's teams, claimed or not; no owner is named |
| `POST /leagues/{league_id}/seasons/{season}/teams/{team_id}/claim` | league member | claim a team: verified by SWID, or pending |
| `GET /pages/claim/{league_id}/{season}` | league member (page) | the claim page |
| `POST /leagues/{league_id}/invites` | league owner | a new invite link, shown once |
| `GET /leagues/{league_id}/invites` | league owner | the league's invites, without links |
| `DELETE /leagues/{league_id}/invites/{invite_id}` | league owner | revoke one |
| `GET /leagues/{league_id}/claims` | league owner | claims waiting on a decision, with who made each |
| `POST /leagues/{league_id}/claims/{claim_id}/approve` | league owner | verify a claim by hand |
| `POST /leagues/{league_id}/claims/{claim_id}/reject` | league owner | refuse a claim, or take a verified manager off |
| `GET /leagues/{league_id}/calibration` | league member | the league's own measured numbers and where each came from (docs/intake.md); every member's pages are built on them, so every member may look |
| `PUT /leagues/{league_id}/calibration/{key}` | league owner | set one of the three bars himself, with a line of why; the other three keys are refused |
| `DELETE /leagues/{league_id}/calibration/{key}` | league owner | forget his own bar and use the measurement again |
| `POST /leagues/{league_id}/calibration/measure` | league owner | put the intake chain on the queue; once a day, never while one is running |

FastAPI's own `/docs`, `/redoc` and `/openapi.json` stay open: they
describe the routes and carry no data.

Two calls worth revisiting when purchase is designed: projection uploads are
in the paid team layer in docs/product.md but are "signed in" here, as
asked for step 1; and the scorecard is league scope here while "the
scorecard of your own moves" is listed as paid.

## The pass (2026-09-26)

**An entitlement is a season pass.** One `team` row per user, covering every
team he manages, in every league, until its `valid_until`. It is not per
team and not per league: a man who manages teams in two leagues needs one
pass. Nothing is recurring and nothing renews itself. When a pass lapses the
row stays as the record of what he had, the viewer is on the free tier
again, and `/upgrade` says so with the date it ended. A live pass is never
stacked on: redeeming a code (or a hand grant) while one is live is refused
with its end date.

**Where a pass comes from** (`entitlements.source`):

| Source | Written by | Ends |
|---|---|---|
| `owner` | `accounts.ensure_owner`, for `FCP_OWNER_EMAIL` | never |
| `comp` | redeeming a code, or the owner's `scripts/comp_code.py grant` | the date the code (or the grant) carries |
| `purchase` | the payment webhook, in the job after this one; the check constraint already allows it (migration 0031), so that job is a webhook and not a migration | the date bought |
| `subscription`, `trial` | nothing now; kept from the first design so no old row breaks | |

**How long a code's pass runs.** The owner sets the date when he makes the
code. Left empty, it is the end of the newest stored season's last matchup
period (its playoffs), dated by that season's NBA schedule, plus thirty
days; with no season stored, or when that date is already past (a store
that has not yet seen the new season), a year from the day the code is made
(`billing.default_valid_until`). A date typed as `2027-06-30` means through
the end of that day (UTC).

**Who is entitled.** With `FCP_BILLING_ENABLED` off, everyone: the check
answers yes whatever the table says. With it on: whoever holds a live pass,
and single mode's owner (`all_access`), who is also the one `owner` row. In
accounts mode the owner is entitled by that row, which never ends.

**The seams for purchase.** `billing.purchase_available()` is False; the
upgrade page draws "Buy a season pass" disabled with its reason ("Purchase
is not open yet; use a code.") until it is True. `billing.grant(user_id,
source, valid_until, note)` is the one place a pass is written; redeeming
calls it with `comp`, and the webhook will call it with `purchase`. Whether
the man who connects a league gets his own team free is the owner's to
decide; nothing implements it, and `grant` is where it would go.

### Codes

A code is twelve characters from an alphabet with no `0`, `O`, `1` or `I`,
grouped `XXXX-XXXX-XXXX` (for example `K66Z-QXS9-DS44`, from a test
database), drawn with `secrets`: 32 letters, 60 bits. It is typed back in any
case, with or without the dashes. It is **kept in clear** in `comp_codes`,
because the owner has to read it back to send it and a copy of the table
grants nothing a revoke does not end.

| Table | Holds |
|---|---|
| `comp_codes` | `code` (unique, its index), `created_by`, `created_at`, `note` (who it is for, the owner's words), `uses_total`, `uses_left`, `valid_until` (the pass's end, not the code's), `redeem_by` (null: never; after it the code itself is dead), `revoked_at` |
| `comp_code_redemptions` | `code_id`, `user_id`, `entitlement_id`, `redeemed_at`; unique on (`code_id`, `user_id`), so one man cannot burn a multi-use code twice |

**Redeeming** (`POST /billing/redeem`, or the form on `/upgrade`): in one
transaction, the user's own row is locked (so two codes at once for one man
cannot both write a pass), a live pass refuses it, then the code's row is
locked (`SELECT … FOR UPDATE`, so two men racing on a one-use code cannot
both spend it: the test runs eight threads at one code and exactly one
wins), and the code must exist, not be revoked, have a use left, not be past
`redeem_by`, not carry a pass that would already be over, and not have been
redeemed by him before. Then one `Entitlement(tier="team", source="comp",
valid_until=code.valid_until)`, one redemption row and `uses_left - 1`. Every
refusal about the code is the same sentence, "That code does not work. Check
it against the one you were sent.", never which part was wrong. The route is
rate-limited as sign-in is: per account five, then one a minute; per client
address twenty, then one every ten seconds (`app/api/upgrade.py`,
`RedeemLimits`). Every attempt is logged on `fcp.billing` with the account,
the address and why a refusal was one, never the code typed.

**Making them** is the site owner's alone (`require_site_owner`, above): the
Codes section on `/account/connections` (drawn only for him; the routes
refuse anyone else) or the command line on the server, which calls the same
functions in `app/billing.py`:

```
./.venv/bin/python scripts/comp_code.py make --note "for Dennis" [--uses 1] [--valid-until 2027-06-30] [--redeem-by 2027-01-31]
./.venv/bin/python scripts/comp_code.py list
./.venv/bin/python scripts/comp_code.py revoke K66Z-QXS9-DS44
./.venv/bin/python scripts/comp_code.py grant --email dennis@example.com --until 2027-06-30 [--note "treasurer"]
```

`make` prints the code once, with the pass's end. `revoke` stops it; the
passes it already wrote stand. `grant` comps someone by hand with no code
(source `comp`), making the account if the address has never signed in.

### Launching the pass

Flip day, in order, on the VPS (after the cutover, docs/cutover.md):

1. Deploy and migrate: `./.venv/bin/alembic upgrade head` (0031).
2. Make the codes, one per person with a note, or one multi-use code for the
   league: `scripts/comp_code.py make --note "for Dennis"` N times, or
   `--uses 12 --note "the league"`. `scripts/comp_code.py list` shows them.
3. Send them (the owner's own channel; nothing here mails a code). They can
   be redeemed on `/upgrade` before the switch: a pass redeemed early is
   simply there on the day.
4. In `/opt/fcp-core/.env`, set `FCP_BILLING_ENABLED=true`.
5. Restart the API (and the worker, which reads it for the digest's team
   section): `sudo systemctl restart fcp-core-api.service fcp-core-worker.service`.
6. `./.venv/bin/python scripts/preflight_public.py`: the `paywall` line says
   `FCP_BILLING_ENABLED is on`, how many live passes besides the owner's and
   how many open codes.

**What flipping it does on day one.** The owner is entitled (his `owner`
row). Everyone else in the league is on the free tier until he redeems a
code: This week, Standings, Draft, History and the league digest as before;
a team page sends him to `/upgrade` and back; the team routes answer 402; the
digest leaves out his team section; the co-manager's team tools refuse with
the upgrade page's address. To undo it, set it back to `false` and restart:
nothing is lost, and the passes stay for the next time.

## Leagues, members and claims (step 2)

Three tables and a grown fourth, in migration 0019:

| Table | Holds |
|---|---|
| `league_connections` | the ESPN login that reads a league: whose it is, the two cookies sealed as one JSON object, the league's name as ESPN gave it, when it last worked or failed (`last_error` is a fixed sentence of ours, never ESPN's text), `ingest_requested_at`, `revoked_at`. One active connection per league (a partial unique index). |
| `memberships` | user in league: `owner` or `member`, when joined. One per user and league. |
| `invites` | per league: the token's sha256, who made it, `expires_at` (null: never), `revoked_at`, `uses` |
| `user_espn_identities` | per user: his own SWID, sealed, and `swid_hash` (sha256 of the normalised SWID) for matching without opening it. One per user, and one user per SWID. Never an `espn_s2`. |
| `team_managers` | the team claims: `state` (`pending`, `verified`, `rejected`), `how` (`owner_guid`, `approved`, `owner`), and now `decided_by` and `decided_at` for a league owner's hand |

The migration makes every user with a verified claim a `member` of that
claim's league, since the league check now reads `memberships`. On the VPS
that is only the owner's own claims, and `ensure_owner` then makes him the
league's `owner`.

### Connecting a league

`POST /connections {league_id, espn_s2, swid}`, or the form at
`/account/connections`.

1. Refused with a 503 before anything else if `FCP_SECRETS_KEY` is not set:
   nothing is ever stored unsealed.
2. The SWID is normalised (braces and spaces off, upper case) and must be
   GUID-shaped; `espn_s2` must be one token of at most 2048 characters. A
   bad one is a 422 with a fixed sentence. The body is checked by hand, not
   by pydantic's field limits, because FastAPI's 422 for a failed limit
   repeats the value it refused.
3. Five attempts per user, then one a minute (each one asks ESPN).
4. **The check.** One request to ESPN (`mSettings` and `mTeam`, through
   espn-api's own request class, `app.espn.fetch_league_settings_with`),
   for the season being prepared, then the current one, then the last,
   until one answers. ESPN refusing the cookies is a 422 ("ESPN refused
   these cookies for league N..."); no such league is a 422; ESPN down or
   answering nonsense is a 502. None of them repeats the cookies or ESPN's
   text, and the log line names only the error's kind.
5. Kept: the league row is created if new (only the row: seasons and teams
   are the ingest's to write), the cookies are sealed into the one active
   connection (the same user connecting again replaces his cookies in
   place; anyone else while it is active gets a 409 telling him to ask for
   an invite), the caller becomes the league's `owner`, his SWID becomes his
   ESPN identity (unless another account already holds it), and he is
   verified (`how = owner_guid`) on every stored team of that league, in any
   season, whose owner GUID is his SWID.
6. **No ingest is started.** `ingest_requested_at` records that the league
   wants one; step 4's per-connection jobs act on it, and should call
   `app.memberships.verify_connection_owner` after a new league's first
   ingest, which is when its connector's team first exists to verify him on.
   The response says which team ESPN lists as his (`espn_team`) meanwhile.

`GET /connections` lists the caller's own, live and revoked, with no
credential of any kind. `DELETE /connections/{id}` revokes his own (anyone
else's is a 404) and wipes the sealed cookies from the row; his `owner`
membership stays, and the league is free for someone else to connect.

### Invites

A league owner makes a link (`POST /leagues/{id}/invites`, optionally
`{"expires_in_days": 1..365}`; by default it works until revoked). The
answer carries the link, `…/join/<token>`, built on `FCP_PUBLIC_URL` when it
is set; only the token's sha256 is stored, so the link is shown this once.
`GET /leagues/{id}/invites` lists them (when, how many joined, expiry,
revoked) without links, and `DELETE` revokes one.

A member opens the link. Signed out, `/join/<token>` sends him to sign in
and back. The page names the league (`GET /invites/{token}`) and joins it
(`POST /invites/{token}/accept`), which makes him a `member` and counts one
use; accepting twice changes nothing. A revoked, expired or unknown token is
the same 404. He lands on the claim page for the newest season.

### Claims

`GET /leagues/{id}/seasons/{season}/teams/claimable` lists the season's teams
by name, each with whether some member is its verified manager and the
caller's own claim on it. No owner is named and no GUID is sent.

`POST …/teams/{team_id}/claim` makes a claim. It is **verified at once**
(`how = owner_guid`) when the caller's ESPN identity hash matches one of the
team's owner GUIDs, normalised the same way (ESPN stores `{XXXXXXXX-...}`;
case and braces are forgiven). Otherwise it is **pending**, and the team's
plan stays closed (403) until a league owner approves it. A verified claim
is left alone; a rejected one can be made again, and is pending again.

`POST /me/espn-identity {swid}` keeps the member's own SWID, sealed and
hashed, and re-checks his pending claims, answering with any it has now
verified. Only the SWID: a member's `espn_s2` is never asked for or kept.
One account per SWID (a second account is a 409). `DELETE` forgets it;
claims it verified stay verified.

Owner GUIDs are read in one function (`app.memberships._owner_hashes`),
hashed there, and go no further: never in a response, a log line or an
error. `tests/test_leagues_admin.py` checks the raw text of every response
it makes for every GUID it seeded, in any case, with or without braces.

### What a league owner can and cannot do

An owner is whoever connected the league, and the configured owner in
`ESPN_LEAGUE_ID`. A league can have more than one (someone who connected
it after an earlier connection was revoked).

He **can**: make, list and revoke invite links; see the league's pending
claims and who made each (by email); approve a claim (the member then
manages that team: its plan opens to him); reject a claim, including one
already verified, which takes the plan away from that manager; approve his
own claim; revoke his own connection.

He **cannot**: read the cookies he connected with (nothing returns them,
not even to him); read any member's SWID; see any owner GUID; read a
team's plan without a verified claim on it (the league owner is not an
operator, except in single mode, where the one user is); act on another
league; revoke another owner's connection; remove a member from the league
(there is no route for that yet); or make someone an owner.

### The secrets key

`FCP_SECRETS_KEY` seals the connection cookies and the members' SWIDs
(Fernet from `cryptography`: AES with an HMAC over it, so a value sealed
with another key, or altered, does not open at all). It is checked at
startup: a value that is not urlsafe base64 of 32 bytes stops the API with
a message that names the setting and not the value. Unset, connecting a
league and keeping a SWID answer 503, and nothing else changes.

Patrick makes and places it on the VPS himself; it is never in the repo,
a log, a response or a chat:

```
cd /opt/fcp-core
./.venv/bin/pip install -q -e ".[dev]"       # cryptography is a new dependency
./.venv/bin/python scripts/new_secrets_key.py   # prints one key, nothing else
```

then add the line `FCP_SECRETS_KEY=<the key>` to `/opt/fcp-core/.env` by
hand and restart the API. Keep a copy wherever the database backups'
secrets are kept: **lose the key and every sealed value is unreadable**
(each league would have to be connected again, each SWID given again), and
a backup of the database without the key reads no cookie. Run the script
once: a second key does not open what the first sealed. Rotating it means
re-sealing every row with both keys at hand, which nothing does yet.

### A known weakness: a bare SWID is not proof

A SWID is not a secret from a member's own league. ESPN hands every owner's
GUID to anyone who can read the league (it is how the ingest learned all
of them), so a member could post a league-mate's GUID to
`/me/espn-identity` and have his claim on that league-mate's team verified.
One account per SWID means the real owner then finds his own SWID refused,
which is a signal but not a defence. A connector proves slightly more,
since ESPN accepted his `espn_s2` for the league, but nothing checks that
the SWID he sent is the one his `espn_s2` belongs to.

Before accounts mode is opened to a league that is not Patrick's own, one
of these should be decided: verify a member's SWID against his `espn_s2`
at an ESPN endpoint that answers only for the account's own SWID, keeping
neither; or keep `app.memberships.TRUST_BARE_SWID = False`, as it ships (since 2026-09-19), so every
member's claim waits for the league owner (a connector's own verification
is not affected). Until then the owner-GUID match is a
convenience among people who trust each other, which is what one league of
friends is.

## Switching the VPS to accounts mode (at the cutover, not now)

**docs/cutover.md is the runbook** and has the exact text to paste, the Caddy
block, the DNS records and the rollback. What follows is the settings half of
it, kept here because this is the document that explains them.

**Check before you switch.** `python scripts/preflight_public.py` prints every
condition below as pass or fail against the live settings, and never prints a
secret: the auth mode, the public URL and that it is https, the owner's
address, the service token, the secrets key, SMTP, that `FCP_API_URL` still
points at the tailnet and not at the public name, that every route declares a
check, the cookie's flags, that links are built on `FCP_PUBLIC_URL`, the rate
limits, and the bearer path. `tests/test_public_ready.py` checks the same
things against the running app, which is where a route added without a check
is caught.

The API stays tailnet-only until this is done. In order:

1. Deploy, install the dependencies (`cryptography` is new), and run the
   migrations: `alembic upgrade head` (0018, 0019).
2. In the VPS `.env`:
   - `FCP_SECRETS_KEY=` from `scripts/new_secrets_key.py` ("The secrets
     key", above). Can be done now, in single mode; it changes nothing
     until someone connects a league.
   - `FCP_OWNER_EMAIL=` Patrick's address (the one he will sign in with).
   - `FCP_PUBLIC_URL=https://boxoutfantasy.com` (the site's own name since
     the 2026-09-22 rename; fcp.patrickmcdowell.dev stays on the old stack).
   - `FCP_MCP_PUBLIC_URL=https://mcp.boxoutfantasy.com` if the remote
     co-manager is being served: it is what the MCP server names itself in
     its metadata, and `--http` refuses to start with auth on without it
     (docs/mcp.md, "Deploying it").
   - `FCP_SMTP_HOST`, `FCP_EMAIL_FROM` (and the SMTP login) if not already
     set for the digest; sign-in links need only the host and the sender.
   - `FCP_SERVICE_TOKEN=` a fresh `python -c "import secrets;
     print(secrets.token_urlsafe(32))"`. It is read by the API and by
     `scripts/warm_pages.py` from the same `.env`.
   - `ESPN_LEAGUE_ID` and `FCP_TRACKED_TEAM_ID` are already there.
   - `FCP_BILLING_ENABLED` stays unset (off) through the cutover; turning
     it on is its own step ("Launching the pass").
   - Last, `FCP_AUTH_MODE=accounts`, and restart the API.
3. Run `scripts/warm_pages.py` once and check it prints HTTP 200 (not 401).
4. Sign in at `/sign-in`, open the week page, check `/auth/me`.
5. Then Caddy (step 5, docs/cutover.md): a block for boxoutfantasy.com that
   proxies to the API on its tailnet address, which is where the container
   can reach the host and where the scheduled scripts already call it. Run
   uvicorn with its default `--proxy-headers` so the client address the rate
   limit sees is the real one and the scheme is https (which sets the
   cookie's `Secure` flag; `FCP_PUBLIC_URL` being https sets it too, which is
   what actually sets it here, since Caddy speaks plain http to the API).

To go back, set `FCP_AUTH_MODE=single` and restart. Nothing is lost:
sessions simply stop being asked for.

Projection sets uploaded before accounts carry the label `patrick`. They stay
readable by the owner in accounts mode (`app/api/projections.py`, `_owns`),
so nothing has to be rewritten.

## Security notes

- **No password is stored.** A link is the sign-in.
- **Only hashes.** Every token handed out, link or cookie, is
  `secrets.token_urlsafe(32)` (256 random bits), and only its sha256 is
  stored (`sign_in_tokens.token_hash`, `sessions.token_hash`,
  `invites.token_hash`). A copy of the database signs nobody in and joins
  no league. The service token is compared by hash, in constant time
  (`hmac.compare_digest`).
- **Sealed, not hashed, where the value is needed again.** A connection's
  ESPN cookies (the ingest must send them) and a member's SWID are sealed
  with `FCP_SECRETS_KEY` ("The secrets key", above), opened only by
  `app.memberships.connection_credentials` for the job that reads a league,
  and returned by no route. Revoking a connection wipes its sealed value.
  Matching a SWID to a team uses `swid_hash`, so nothing is opened for it.
  `owners.espn_owner_id` itself is still stored in plain text, as it was
  before step 2: identity across seasons depends on it.
- **Invite tokens in URLs.** An invite is a path (`/join/<token>`,
  `/invites/<token>`), and one works until it is revoked. The access-log
  filter blanks it there as well, including inside the `next=` of the
  redirect to sign in. That `next` is also kept in `sign_in_tokens.next_path`
  in plain text, so a copy of the database reads the invite links of anyone
  who signed in through one; revoking an invite ends that, and step 3 could
  strip the token from `next` instead.
- **The cookie.** `fcp_session`: `HttpOnly` (no script reads it),
  `SameSite=Lax` (no cross-site POST carries it, which is the CSRF defence
  for the few routes that write: uploads, sign-out, connections, invites
  and claims), `Secure` whenever the
  request is https or `FCP_PUBLIC_URL` is, `Path=/`, thirty days.
- **Links.** Fifteen minutes, once. Built only on `FCP_PUBLIC_URL` when
  mailed, never on the Host header. The landing path is a local path or
  nothing (`app.accounts.safe_next`): no scheme, no `//host`, no backslash,
  so a sign-in link cannot be turned into a redirect elsewhere.
- **Rate limits.** In-process token buckets on `POST /auth/sign-in`: per
  address five links, then one a minute; per client address twenty, then one
  every ten seconds (`app/api/auth.py`, `SignInLimits`). Per user on
  `POST /connections` (five, then one a minute), `POST /me/espn-identity`
  (ten, then one a minute) and the two invite-token routes together (twenty,
  then one every six seconds; added 2026-09-22 for the cutover, so a
  signed-in member cannot work through tokens, though 256 random bits is the
  real defence) (`app/api/leagues_admin.py`). In memory, per
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
  turned on) needs the same: it logs the URI unless told not to. Connecting
  logs the league id and the user id, and a failed ESPN check logs the
  error's class only; no cookie, SWID or owner GUID is ever logged.
- **Alert channels (step 4).** A member's email address, Telegram chat id or
  ntfy topic is sealed with `FCP_SECRETS_KEY` like a connection's cookies,
  shown only masked, and wiped when he disables it. Verifying one spends a
  one-time secret of which only the sha256 is kept: an emailed link's token
  (a day, once) or a test message's code (a day, ten tries a minute per
  user). The link is `/account/alerts?token=…`, which the access-log filter
  blanks like a sign-in link; clicked while signed out, it goes through
  sign-in and its `next` keeps the token in `sign_in_tokens.next_path` until
  it expires, as an invite link's does. An ntfy topic must be on ntfy.sh, so
  no member can make the server post to an address of his choosing. A send
  that fails is logged and answered by the exception's class only: a
  Telegram refusal quotes the bot's URL, and with it the bot's token.
- **Single mode is not a security mode.** It is the tailnet API: nothing is
  enforced, and it is only safe because the API binds the tailnet address
  (STATUS.md, "Why the API is tailnet-only").
