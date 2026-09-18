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
2. **He invites the league.** An invite link per league. A member signs up
   with his email, opens the link, and sees the league pages at once.
3. **The member claims a team.** Verified one of two ways:
   - he connects his own ESPN too (optional), and the `SWID` matches the
     owner GUID stored on that team (`owners.espn_owner_id`), so the claim
     is automatic; or
   - whoever added the league approves the claim by hand.
   Until a claim is verified, the team's private pages stay closed. That is
   what stops someone claiming Through The Wire and reading its plan.
4. **He sets his alerts.** Email by default (the SMTP channel already
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

1. **Accounts:** users, magic-link sign-in, sessions, the three scope checks
   as FastAPI dependencies, and every existing route put behind one.
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
