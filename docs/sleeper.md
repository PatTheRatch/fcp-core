# Sleeper: what the league carries, and what supporting it would take

**Written:** 2026-09-19. **Status:** nothing is built but the probe. `app/sleeper.py`
fetches and `scripts/sleeper_probe.py` prints; no table, migration or ingest
exists, and no route knows Sleeper. This is the reconnaissance document that
docs/platforms.md's step 2 ("an ingest mapping to these tables") would be
written against.

**The league in question:** `1404516094114377728`.

**A caveat that shapes this whole document.** It was written in a session whose
egress policy blocks `api.sleeper.app` and `docs.sleeper.com` outright, so
nothing below has been confirmed against that league, or against any live
response. The endpoint list and field names are Sleeper's public, documented
and long-stable read API; the *analysis* — how it collides with our tables — is
the part worth reading, and it holds whatever the league turns out to be. Run
the probe from a machine with ordinary internet before acting on any specific
field name:

```bash
python scripts/sleeper_probe.py 1404516094114377728
python scripts/sleeper_probe.py 1404516094114377728 --settings
python scripts/sleeper_probe.py 1404516094114377728 --history
python scripts/sleeper_probe.py 1404516094114377728 --season
python scripts/sleeper_probe.py 1404516094114377728 --draft
python scripts/sleeper_probe.py 1404516094114377728 --players
```

## Three questions decide how much of this repo applies

Everything else is detail. The probe's default summary leads with all three.

1. **Is it basketball?** `league.sport`. Full Court Press is fantasy basketball
   end to end: the scoring, pickups and draft packages are nine-category NBA
   code. An `nfl` league can be *ingested* into these tables in the same shape
   and would still mean nothing to any of it. This is the first thing to check
   and the one that can end the exercise.
2. **Is it head-to-head categories?** docs/platforms.md already names this as
   the harder change: "only an H2H categories league would mean anything to the
   code that reads them". Sleeper leagues are commonly points leagues, where
   `scoring_settings` is a weight per stat and a matchup is one number against
   another. A points league is a rethink of `app/scoring`, not a mapping.
   Sleeper does not carry a blunt "this is categories" flag worth trusting,
   which is why `--settings` dumps the evidence and the probe refuses to
   conclude.
3. **Can we join the players?** Sleeper's player dump carries each player's
   `espn_id`. If coverage is good for this league's rostered pool, the
   cross-platform player matching docs/platforms.md budgets for — match by
   name, team and position, refuse a tie — collapses into an id join for most
   of the roster, and the careful name matching is only needed for the
   remainder. `--players` measures exactly that.

## The API surface

Public, anonymous, read-only. There is no login, no OAuth, no cookie, and no
write endpoint of any kind. Base `https://api.sleeper.app/v1`. Sleeper asks for
under 1000 calls a minute, and that the player dump be pulled at most once a
day.

| Endpoint | Gives | Cost |
|---|---|---|
| `GET /league/{id}` | the season's settings, scoring, roster slots, status, draft id, previous season's id | 1 |
| `GET /league/{id}/users` | one row per person: user id, display name, team name, avatar | 1 |
| `GET /league/{id}/rosters` | one row per team: owner, co-owners, players, starters, record, FAAB spent | 1 |
| `GET /league/{id}/matchups/{week}` | every team's line for that week: points, starters, per-player points | 1 per week |
| `GET /league/{id}/transactions/{week}` | adds, drops, waiver claims, trades filed that week | 1 per week |
| `GET /league/{id}/winners_bracket`, `/losers_bracket` | the playoff and consolation brackets | 2 |
| `GET /league/{id}/traded_picks` | future draft picks that changed hands | 1 |
| `GET /league/{id}/drafts` | the league's drafts | 1 |
| `GET /draft/{id}`, `/draft/{id}/picks` | draft settings and every pick with its price | 2 |
| `GET /state/nba` | the current week and season | 1 |
| `GET /players/nba` | every player Sleeper knows, with cross-platform ids | 1 per day, several MB |

**A whole season is roughly 60 calls** — well inside the limit, and a full
history is that times the number of seasons. The player dump is the only
expensive one and it is shared across every league.

Undocumented endpoints exist (`/stats/nba/{season}`, `/projections/nba/{season}`,
per-player weekly splits). They are real and widely used, but they are not part
of the published contract and should not be load-bearing. Our projections come
from Basketball Monster and our own uploads, so nothing needs them.

## Where Sleeper and ESPN disagree, structurally

These are the findings that matter. Each one is a decision, not a field rename.

### 1. A Sleeper league id is one *season*, not one league

ESPN keeps a single league id for a decade and asks which year you want.
Sleeper mints a **fresh league id every season** and links the previous one
through `previous_league_id`. A league's history is a walk back down that
chain, which `sleeper.league_history` does.

This breaks an assumption docs/platforms.md states outright: *"`league_seasons`
and `draft_picks` need nothing. Neither holds a platform id: a season is
(league, year)."* That is true of ESPN and false of Sleeper. A Sleeper league
has **one platform id per season**, and there is nowhere to put them.

The fix is small but it is a schema change: `leagues.platform_league_id` holds
the identity of the chain (the oldest id in it, which never changes as seasons
are added), and `league_seasons` gains a nullable `platform_season_id` for that
season's own league id — plus, probably, `platform_draft_id`, since Sleeper's
draft is a separate resource with its own id while ESPN ships the draft inside
the league.

Using the *newest* id as the league identity is the trap: it changes every
October, and the league would fork into a new row each season.

### 2. There is no daily lineup history

This is the biggest capability gap and it is not fixable by mapping.

`daily_lineup_slots` — "the finest grain ESPN exposes, and the one that makes
narratives possible: who was benched, who was started while injured" — has no
Sleeper equivalent. `GET /matchups/{week}` returns `starters` as the lineup
*for that week*, and Sleeper does not serve a per-day historical record of who
sat where. What is gone is gone.

So: for a Sleeper league, daily lineups are **collectible going forward by
polling and not backfillable**. Whatever a listener snapshots each day is all
there will ever be, and every past season arrives with that table empty.
Anything downstream that reads it — the narratives, the parts of the pickups
judgement that ask what a team actually did with a player — degrades to nothing
for history and starts from the day we begin watching. That is worth deciding
deliberately rather than discovering later.

`player_game_stats` is a separate question and in better shape: it is NBA fact,
not league fact, and could be filled from any stats source we already trust
rather than from Sleeper.

### 3. Points, not categories — probably

`scoring_settings` is a map of stat key to weight (`pts`, `reb`, `ast`, `stl`,
`blk`, `tov`, `fgm`, `fga`, `ftm`, `fta`, `tpm` …). A matchup row carries
`points`, `starters_points` and `players_points` — one number per team, per
starter, per player.

`matchups.home_categories_won` / `categories_lost` / `categories_tied` and
`teams.categories_won` have no meaning in a points league. Neither does
`league_season_categories`, whose whole content is "which nine categories does
this league score". They would sit at zero, and every report built on them
would render an honest-looking nothing.

If question 2 comes back "points", the mapping below still works for identity,
rosters, transactions and the draft — the league's *structure* ingests fine —
and the scoring package simply does not apply. Say that out loud rather than
shipping empty category tables.

### 4. Vocabularies have to be translated into ESPN's, not added alongside

docs/platforms.md is firm on this and it is the right call: "ESPN's
vocabularies travel with the rows today and the mapping has to translate into
them, not add a second one". Four translations are needed, and each is a lookup
table someone has to write and check:

* **Stat keys → ESPN stat ids.** `pts` → ESPN's id for points, and so on for
  every scored stat. `league_season_categories.stat_id` is an ESPN stat id by
  definition.
* **Roster slots → ESPN slot names.** Sleeper says `PG SG SF PF C G F UTIL BN IR`;
  ESPN says `PG SG SF PF C G F UT BE IR`. `UTIL` is not `UT` and `BN` is not
  `BE`; `daily_lineup_slots.slot` documents the ESPN set.
* **Pro team abbreviations → ESPN's `pro_team_id` numbering.** Sleeper gives
  `"LAL"`; our rows carry ESPN's integer for the Lakers.
* **Injury statuses → ESPN's words.** Sleeper's `injury_status` vocabulary is
  its own.

### 5. Player identity is nearly free, and that is the good news

Every entry in `/players/nba` carries `espn_id`, alongside `yahoo_id`,
`rotowire_id`, `swish_id`, `sportradar_id` and others. So the step 3 that
docs/platforms.md describes — "matched to an existing `players` row by name,
NBA team and position … a name that matches no one or more than one is refused
and reported for a person to settle" — applies only to the players Sleeper has
no `espn_id` for.

The rule does not relax: a player with an `espn_id` joins straight to
`players.espn_player_id`; a player without one goes through the careful match
and is **refused and reported rather than guessed**, exactly as
`app.draft.bbm.match_player` already does. `--players` says how large that
remainder is before anyone budgets for it.

### 6. No credentials, but also no proof of identity

docs/platforms.md step 4 mostly evaporates: there is nothing to seal in
`league_connections`, no cookie to expire, no OAuth dance. A Sleeper league is
public, so connecting one is just recording its id.

The cost is on the other side. Team claims are verified today by a member's
ESPN SWID (`user_espn_identities`). Sleeper hands out no such secret — a user
id and display name are public to anyone who reads the league — so **a Sleeper
claim needs its own proof**, and "I say I am this user" is not one. A workable
shape: ask the claimant to put a short code we give them into their Sleeper
team name or avatar, then read it back through the API. That is a design task,
not a mapping task, and it is the only part of accounts that gets harder.

### 7. Moves that carry no player

Sleeper transactions can move **FAAB between teams** (`waiver_budget`) and
**future draft picks** (`draft_picks`), both commonly inside a trade.
`transaction_items` has `player_id` NOT NULL and holds nothing else, so a
FAAB-for-picks trade has no representation at all — it would ingest as a
transaction with no items, which reads as an empty trade rather than a real
one. There is likewise no table for `traded_picks`.

In a redraft league both are empty and this does not matter. In a keeper or
dynasty league (`settings.type` of 1 or 2 — the probe prints it) they are a
real part of how the league works and would need a table each.

## The mapping, table by table

Assuming an NBA categories league; the columns that only a categories league
fills are marked. Every field name here is from the public API's documented
shape and wants confirming with `--settings` and `--raw`.

| our table | from | notes |
|---|---|---|
| `leagues` | the oldest id in the `previous_league_id` chain | `platform` = `'sleeper'`; `espn_league_id` must become nullable |
| `league_seasons` | `GET /league/{id}` per season in the chain | `season` from `league.season`; needs a new `platform_season_id` (finding 1) |
| `league_seasons.lineup_slots` | `roster_positions`, counted | translate `UTIL`→`UT`, `BN`→`BE` (finding 4) |
| `league_seasons.bench_slots` / `injured_reserve_slots` | `roster_positions.count("BN")` / `("IR")` | |
| `league_seasons.acquisition_budget` | `settings.waiver_budget` | the in-season FAAB pot |
| `league_seasons.auction_budget` | the draft's `settings.budget` | a different pot, as it is on ESPN |
| `league_seasons.uses_faab` | `settings.waiver_type` | |
| `league_seasons.playoff_team_count` | `settings.playoff_teams` | |
| `league_seasons.regular_season_periods` | `settings.playoff_week_start - 1` | derived, not reported |
| `league_seasons.keeper_count` | `settings.max_keepers` | |
| `league_seasons.draft_type` / `seconds_per_pick` / `draft_order` | the draft's `type`, `settings.pick_timer`, `slot_to_roster_id` | |
| `league_seasons.raw_settings` | the whole league object | keep verbatim, as ESPN's is |
| `league_season_categories` | `scoring_settings` keys | **categories only**; stat keys → ESPN stat ids |
| `owners` | `GET /users` → `user_id` | `platform` = `'sleeper'`, `platform_owner_id` = `user_id`; a global id, which suits the table |
| `teams` | `GET /rosters` → `roster_id` | `platform_team_id` = `roster_id`; name from the *user's* `metadata.team_name`, falling back to display name |
| `team_owners` | `roster.owner_id` plus `roster.co_owners` | the many-to-many already handles co-owners |
| `teams.categories_won/lost/tied` | — | **categories only**; zero and meaningless in a points league |
| `matchup_periods` | week numbers 1..n | `is_playoff` from `settings.playoff_week_start`; scoring-period columns stay null — there are no days |
| `matchups` | two rows sharing a `matchup_id` | a null `matchup_id` is a bye; `winner` derived from `points`, since Sleeper does not say |
| `roster_slots` | `matchup.players` | who was held that week |
| `daily_lineup_slots` | **nothing** | not backfillable (finding 2); only a daily poll can fill it, going forward |
| `matchup_team_stats` | — | **categories only**; a points league posts one number, not a stat line |
| `transactions` | `GET /transactions/{week}` | `platform_transaction_id` = `transaction_id`; `type` and `status` are Sleeper's words and need mapping to ESPN's; `bid_amount` from `settings.waiver_bid`; `scoring_period` is a *week*, not a day |
| `transaction_items` | `adds` / `drops` maps, and trade sides | FAAB and pick movement have nowhere to go (finding 7) |
| `draft_picks` | `GET /draft/{id}/picks` | `round`, `pick_no`, `roster_id`, `is_keeper`; the auction price is in the pick's `metadata` |
| `players` / `player_platform_ids` | `GET /players/nba` | join on `espn_id` where present, careful match or refusal otherwise (finding 5) |
| — | `winners_bracket` / `losers_bracket` | no table; optional |
| — | `traded_picks` | no table; dynasty only |

One column deserves a flag: **`transactions.scoring_period` would hold a week
for a Sleeper league and a day for an ESPN one.** Two meanings in one column is
how a query silently returns nonsense later. Either the column is documented as
"the platform's own move bucket" or Sleeper's week is converted to the day it
started.

## The plan

In order, each step useful on its own, and none of it built. Steps 1 and 2 are
the ones to do before deciding anything else.

1. **Run the probe and answer the three questions.** Sport, scoring, player-id
   coverage, plus how many seasons the chain holds and whether it is redraft,
   keeper or dynasty. This is an afternoon and it decides the size of
   everything below. *Nothing else should start first.*
2. **Decide on points.** If the league is points or roto, `app/scoring` does not
   apply and the honest options are: ingest structure only and serve rosters,
   transactions and draft history without valuations; or take on the scoring
   rethink docs/platforms.md defers. Do not ship empty category tables.
3. **The enabling migration.** docs/platforms.md step 1 (add `'sleeper'` to
   `PLATFORMS` and the `ck_*_platform` CHECKs; relax the five `espn_*` columns
   to nullable) **plus** `league_seasons.platform_season_id` and
   `platform_draft_id`, which that document did not anticipate (finding 1).
4. **The player bridge.** Pull `/players/nba` once, join on `espn_id`, write
   `player_platform_ids` rows, and produce a report of the refusals. Do this
   before the league ingest: every other table's rows point at players, and a
   player the bridge refuses is a row the ingest cannot write.
5. **The season ingest.** `app/sleeper_ingest.py` mapping one league season into
   the tables above, walking `previous_league_id` for history. It is about 60
   calls a season, so `--all-seasons` is cheap, and re-running is idempotent on
   the same platform ids.
6. **Decide on daily lineups.** Either a Sleeper listener that snapshots
   rosters daily (which starts the record from that day and never backfills),
   or accept that the narratives do not exist for this league. Finding 2 is the
   argument; it is a product call.
7. **Claims and URLs.** A Sleeper proof of ownership (finding 6), and an
   address that does not collide with an ESPN league — docs/platforms.md
   proposes `/l/{platform}/{id}` with today's ESPN form kept, and
   `app.platforms.league_by_platform_id` is already the lookup behind it.

## What this does not cover

Roto and points scoring, which is the same open question docs/platforms.md
leaves open and step 2 above forces. Sleeper's undocumented stats and
projections endpoints, which nothing should depend on. And the NFL, which
Sleeper serves and this repository does not.
