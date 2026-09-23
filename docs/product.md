# fcp.patrickmcdowell.dev: what the site is, and how other people get in

**Written:** 2026-09-19, with Patrick's four decisions below.
**Status:** design. Nothing here is built yet; the recommenders, pages and
draft room it describes are (STATUS.md). Built in November in the order at
the end, with the draft on 2026-10-10 and the first weeks of the season
first.

## The goal

One person using it today, built as if many people in many leagues were.
Winning Full Court Press comes first, and it is also the best test the
product will get. Everything below keeps a seam where today's code assumes
one manager, so a second user is a row in a table and not a rewrite.

## Decided, 2026-09-19

| Question | Decision |
|---|---|
| Sign-in | Email magic link. No passwords stored. |
| What members see of each other | League pages shared; each manager's plans private. |
| Who connects ESPN | One connection per league, by whoever adds it. Members only claim a team. |
| When the domain moves | When sign-in works. Nothing is public before auth. |
| What is paid | The league pages are free; the team layer (planning, pickups, bids, alerts, projections, the draft room) is paid. Decided 2026-09-19; price and billing not yet. |

## Two scopes

Everything on the site is one of two things.

**League, shared with every member of that league.** Standings, this week's
matchups, the narratives and history, the draft board with its grades, the
transactions, every team's scorecard. Read-only and the same for everyone.
This is what makes a league want to sign in.

**Team, private to the manager who claimed it.** The week plan, the season
plan, the moves worth a look, the bids, the alerts, the manager's own
projections (BBM or an upload), and later the draft room. Another manager
sees his own versions and never these. This is what a manager would pay for.

The rule a route answers is one of three: anyone signed in (the account
pages), a member of this league (league pages), the manager of this team
(team pages). Nothing is served to someone who is not signed in except the
sign-in page and a landing page.

## Free and paid

The two scopes are also the two tiers.

**Free: the league.** Everything in the league scope, for every member of a
connected league, with no card. It is what gets a league to sign up, and the
thing a member shows the group chat.

**Paid: your team.** Everything in the team scope: the week and season plans,
the moves worth a look, bids, the projected record, the per-team digest and
alerts, BBM or uploaded projections, the scorecard of your own moves, and the
draft room when it is hosted. This is where the work is, and what earns a
subscription.

So the scope check gains a fourth question for team routes: the manager of
this team **and** entitled to the paid tier. It is written from the start as
one dependency (`require_entitlement`) that answers yes for everyone until
billing exists, the same way `viewer_owns_source` answers yes for one user
today. Turning the paywall on is then a table and a payment provider, not a
change to every route.

A free member of a league still gets a taste of the paid layer on the free
pages, without the plan itself: his own team's expected categories this
week and his projected record, with "see the moves worth a look" linking to
the upgrade. The league digest (standings, the week's matchups, league news)
can be free by email; the team plan in it is paid.

**Not decided yet, and not needed until the cutover:** the price; per user
or per team (per user is simpler, since a user may manage teams in several
leagues); a free trial or a free first week; whether the person who connects
a league gets his own team free; and the provider (Stripe is the default:
hosted checkout and a customer portal mean no card data ever touches this
server). Patrick's own teams are entitled, always.

**One risk to settle before charging anyone.** Every league's data comes
from ESPN's unofficial, undocumented API, read with the member's own login.
That is fine for a tool a manager runs on his own league, but charging money
for a product built on it is a different position, and so is charging for
anything derived from Basketball Monster's paid projections. The paid tier
should sell our own analysis (the plans, the judgement, the backtested
recommendations) on data the user brings, and the terms of ESPN and BBM are
worth reading, or a lawyer's hour, before the first invoice.

## Navigation

```
[ League switcher ▾ ]   This week   Standings   Draft   History   My team ▾    [ Account ▾ ]
                                                                   Week          Connections
                                                                   Season        Projections
                                                                   Moves         Alerts
                                                                                 Sign out
```

- The league switcher is there from day one, because one person can be in
  several leagues, and it is what a second league looks like.
- "My team" exists only in a league where the viewer has claimed a team.
- The light house style for every page; the draft room keeps its dark board
  with the switch (docs/draft_room.md, docs/in_season_pages.md).

## How a league and its members get in

1. **Someone adds the league.** Signs up, connects ESPN (the two cookies,
   `espn_s2` and `SWID`), picks the league. The ingest backfills its seasons
   and the listener starts following it. Only this one connection is needed
   for the whole league: the ingest already reads every team.
2. **The league is measured on its own history** (docs/intake.md, built
   2026-09-22). Every number the recommendations lean on — what a pickup
   returns, what an open roster place returns, the three bars a move has to
   clear, and the trade number's own record — was measured on Full Court
   Press and nowhere else, and a number measured on one league is not a fact
   about another. So connecting a league puts a chain of jobs on the queue:
   read every season ESPN will give us, store the NBA schedules behind them,
   run each measurement on **its** history, and write to whoever connected it
   with its own numbers in the message. It takes about two hours, most of it
   the backtest, and it runs at the queue's lowest priority so nobody's
   morning waits for it.

   Until a number of its own exists, or where its history is too thin to
   measure one, it falls back: first to the pool of leagues shaped like it,
   then to ours. **Every page says which it is using**, the way it says where
   a projection came from, and the connector can change any of the three bars
   himself on the account page with a line of why.

   A league this code does not model — points, rotisserie, any number of
   categories but the nine — is refused here, with what it is scored on in
   the sentence, and nothing else is enqueued. That is most public leagues
   (docs/league_survey.md), so it is the common case and not an edge one.
3. **He invites the league.** An invite link per league. A member signs up
   with his email, opens the link, and sees the league pages at once.
4. **The member claims a team.** Verified one of two ways:
   - he connects his own ESPN too (optional), and the `SWID` matches the
     owner GUID stored on that team (`owners.espn_owner_id`), so the claim
     is automatic; or
   - whoever added the league approves the claim by hand.
   Until a claim is verified, the team's private pages stay closed. That is
   what stops someone claiming Through The Wire and reading its plan.
5. **He sets his alerts.** Email by default (the SMTP channel already
   exists), Telegram if he wants it.

Co-managed teams: a team can have more than one verified manager, since
ESPN allows co-owners and `team_owners` already models it.

## The data model to add

| Table | Holds |
|---|---|
| `users` | email, created, last sign-in |
| `sign_in_tokens` | one-time magic-link tokens, hashed, fifteen-minute expiry |
| `sessions` | signed session cookies, revocable |
| `league_connections` | the platform credentials that ingest a league, encrypted, and whose they are |
| `memberships` | user in league: role (`owner` of the connection, `member`), when joined |
| `team_claims` | user claims team: state (`pending`, `verified`, `rejected`), how verified |
| `invites` | per league, a revocable token |
| `notification_channels` | per user: email address or Telegram chat, verified flag |
| `user_secrets` | per user, encrypted: a BBM login, anything else that is only his |
| `entitlements` | per user: tier, source (`owner`, `subscription`, `trial`, `comp`), valid until; written by the payment provider's webhook |

`projection_sets.owner` becomes a user id, and `viewer_owns_source` stops
being a constant: it is "this viewer is the user whose secrets fetched it".
That seam is already in every place that matters (docs/projection_sources.md).

Secrets are encrypted with a key that lives only in the VPS environment
(Fernet or libsodium), decrypted in the job that needs them, never returned
by the API, never logged.

## What changes underneath so it scales

**Jobs per league, not per user, and not one league.** The timers run one
league today. They become a small job table read by a worker: ingest,
listener pass and schedule per league; digest and the morning precompute per
claimed team. A hundred users in ten leagues is ten ingests. Postgres is
enough for the queue at this size (`SELECT ... FOR UPDATE SKIP LOCKED`); no
broker until it hurts.

**Precompute, then read.** A cold report takes about thirty seconds. The
morning pass builds every claimed team's week and season report once and
stores it (`team_reports`: team, day, kind, payload, built at); pages and the
digest read the stored row and rebuild only on demand. This is
`scripts/warm_pages.py` grown up.

**League data is shared, computed once.** Box scores, the schedule and
player status are already global rather than per league. Nothing about a
second league duplicates them.

**Platform-neutral identity.** `leagues` gains `platform` and
`platform_league_id` beside `espn_league_id`; players get a canonical id with
one id per platform. The scoring, pickups and draft packages already read
tables and not ESPN, so Yahoo, Fantrax or Sleeper is a new ingest mapping to
the same tables. The harder change for those platforms is roto and points
scoring, which is a rethink of the currency and not in this plan.

**Going public.** Caddy routes fcp.patrickmcdowell.dev to fcp-core instead
of the old stack (stopped, unused) once sign-in works. The API moves from the
tailnet address to localhost behind Caddy; every route gets the scope check
above; sign-in and invites are rate-limited; cookies are `Secure`,
`HttpOnly`, `SameSite=Lax`.

**The draft room** stays a tool a manager runs on his own machine this year.
Hosting it for a league means live connections per draft room, which is a
project of its own; nothing above closes that door.

## What a member costs us, and what could break

- **One ESPN connection per league** means one person's cookies run everyone's
  data. If they expire, the league goes stale for all members. The watchdog
  already notices a stale league; it needs to tell the connection's owner
  by email, not only Patrick.
- **ESPN rate limits** are unknown at ten leagues. The ingest is a few
  hundred requests a league a day; spread across the night, not all at 09:00.
- **BBM** stays per user and gated. A member without a membership drafts and
  plans on ESPN or an upload, which the room and the plan already support.

## Build order

1. **Accounts:** users, magic-link sign-in, sessions, the scope checks as
   FastAPI dependencies (signed in, league member, team manager, entitled),
   and every existing route put behind one. `require_entitlement` answers
   yes for everyone until step 7.
2. **Memberships and claims:** league connections (encrypted), invites, team
   claims with the owner-GUID check and manual approval.
3. **The shell:** the navigation, the league switcher, the account pages,
   the existing pages moved under it. Landing and sign-in pages.
4. **Per-league jobs and precompute:** the job table and worker, per-team
   digests and alerts to each member's own channels, stored reports.
5. **Cutover:** Caddy routes the domain to fcp-core, the API leaves the
   tailnet, Full Court Press is invited.
6. **Platform columns:** `platform` on leagues and players, before any
   second platform is written.
7. **Billing:** the entitlements table filled by a payment provider's
   webhook, the upgrade page, and `require_entitlement` switched on. After
   the price is decided and the ESPN and BBM terms are read.

## Trades between managers (decided in outline, 2026-09-22; not built)

The trade page judges a deal; the next step is sending one. In order, each
standing on the last: the **finder** (name what you want -- "an even trade
that nets threes" -- and it searches every other roster for deals that fit
both sides), the **block** (put a man up; the league sees it; the finder runs
from each other manager's side), and **proposals**.

A proposal is a deal sent from one manager to another inside the site: the
players, any fills and drops, and the deal's fit for BOTH sides as the page
shows it. The other manager gets a notification (his own channels: email,
Telegram) and a page where he sees it, counters, accepts or declines; a
counter is a new proposal that points at the one it answers, so the whole
negotiation is one thread. Nothing touches ESPN: when both agree, the page
says "now submit it on ESPN" and links there. That keeps the tool read-only
against ESPN and keeps the decision where it belongs.

**Who sees what on a proposal (Patrick, 2026-09-22).** Both sides' fit are
shown to both managers: the tool's stance is that a deal should work for both,
and a manager reading "he gains threes, I gain blocks" is a manager who
trusts the page. The tiers gate how far he can look, not which side:

| The recipient | Sees |
|---|---|
| No account (the temporary link) | The deal, and how it affects **his** roster only. Nothing else about the league. |
| Free account | Both sides' fit, and the thread (counter, accept, decline). |
| Paid account | Both sides, plus the league view: what the deal does to the standings and the other contenders, and each man's card. |

The temporary link is the invitation: it shows a stranger the one thing he
cares about, expires, and signing up (one email; the league is already
connected) opens the rest. Sending and receiving proposals is a league-scope
thing and should be free; the judgement beyond a manager's own side stays
paid. What the temporary link must never show: the proposer's own plan, the
other rosters, or anything a member could not see.

Data: `trade_proposals` (league_season, from_team, to_team, the deal as the
report route takes it, state: open / countered / accepted / declined /
withdrawn, `answers` the proposal it counters, who made it, when, an expiry)
and a share token per proposal; notifications through the channels that
already exist. The reasons and the numbers on a proposal page are the trade
route's, so the two can never disagree.
