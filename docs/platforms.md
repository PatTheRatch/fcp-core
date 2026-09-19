# Platforms: one set of tables, more than one fantasy site

**Written:** 2026-09-19. **Status:** built, step 6 of docs/product.md. Only
ESPN exists. Nothing reads the new columns yet except `app/platforms.py`
and its tests; every URL, API response and script is unchanged, and ESPN ids
stay the keys of today's URLs.

Code: migration `0021_platform_identity`, the models in `app/db/models.py`
(`League`, `Owner`, `Team`, `Transaction`, `Player`, `PlayerPlatformId`),
the writes in `app/ingest.py` and `app/memberships.get_or_create_league`,
and the resolver `app/platforms.py`. Tests: `tests/test_platforms.py`.

## Why now

The scoring, pickups and draft packages read tables and never ESPN, so a
second platform (Yahoo, Fantrax, Sleeper) should be a new ingest mapping into
the same tables and not a rewrite. That only holds if the tables have
somewhere to put another platform's ids before any of them is written. This
step is that, and nothing more.

## What changed

Every ESPN id got a platform-neutral twin beside it. The ESPN column stays
exactly as it was.

| table | added | unique | the platform is |
|---|---|---|---|
| `leagues` | `platform` (default `'espn'`), `platform_league_id` | (platform, platform_league_id) | its own |
| `owners` | `platform` (default `'espn'`), `platform_owner_id` | (platform, platform_owner_id) | its own |
| `teams` | `platform_team_id` | (league_season_id, platform_team_id) | its league's |
| `transactions` | `platform_transaction_id` | (league_season_id, platform_transaction_id) | its league's |
| `player_platform_ids` (new) | player_id, platform, platform_player_id | (platform, platform_player_id) and (player_id, platform) | its own |

All platform ids are text: ESPN's are integers for leagues, teams and
players and strings for owners and transactions, and other platforms use
both (Yahoo's `nba.l.123.t.4`, Sleeper's numeric strings).

**Why owners carry a platform and teams and transactions do not.** An owner
is global, not inside a league, so nothing else says which platform his id
belongs to. A team and a transaction sit inside a league season, whose
league already says it; a second copy on the row could only disagree. Their
uniqueness is scoped to the league season for the same reason, which ESPN's
data already satisfies (a transaction's UUID is unique everywhere, so it is
unique within its season too).

**The player is the canonical row.** `players.id` is the id the rest of the
code joins on; `player_platform_ids` says what each platform calls him. One
row per player per platform, and one player per id on a platform.
`espn_player_id` stays on `players` because everything reads it today.

**`league_seasons` and `draft_picks` need nothing.** Neither holds a
platform id: a season is (league, year), and a pick is keyed by its round and
slot. `ingest_runs.espn_league_id` is a log line and stays as it is.
`league_connections` already has its `platform` column (step 2).

## How the two ids cannot drift

* **The ingest writes both**, on every insert and every update: the league
  and its owners, teams and transactions get `platform = 'espn'` and the ESPN
  id as text, and every player the ingest creates goes through
  `app.ingest.new_espn_player`, which creates his `player_platform_ids` row
  with him. The listener creates players through the same function.
* **The database refuses a mismatch.** A CHECK on each table,
  `ck_<table>_espn_id`, says a row carrying an ESPN id carries it as its
  platform id too (and, on `leagues` and `owners`, has platform `'espn'`).
  `ck_<table>_platform` limits `platform` to the names in
  `app.platforms.PLATFORMS`, as `league_connections` already did.
* **Every other writer is unchanged.** The models derive the platform id from
  the ESPN id when a writer names only the latter, so `League(espn_league_id=...)`
  in a test, or a Core insert, goes on working and still satisfies the CHECK.
  A player created outside the ingest (only tests do this) gets no mapping row,
  which is why the rule "created through `new_espn_player`" is the ingest's.

`tests/test_platforms.py` ingests a fixture league that has every kind of
row (shared owners, rostered players, a player seen only in a transaction,
one seen only in the draft, two seasons) and asserts every row's platform id
equals its ESPN id as text.

## The migration on real data

On a copy of the local database (at 0020: 1 league, 9 seasons, 49 owners,
114 teams, 11,934 transactions, 689 players), 0021 upgraded in 0.6 s and
backfilled every row: 1 league, 49 owners, 114 teams, 11,934 transactions and
689 player mapping rows, with no player left unmapped. Downgrade (0.1 s)
left a schema identical to the original 0020 dump, and a second upgrade
(0.4 s) backfilled the same counts again. The upgrade adds the columns
nullable, fills them with one `UPDATE` each, then sets them NOT NULL and adds
the constraints, so it holds each table's lock for well under a second at
this size.

## What a second platform needs

In order, and none of it is built:

1. **A migration that lets the rows exist.** Add the platform's name to the
   `ck_*_platform` CHECKs and to `PLATFORMS`, and relax each `espn_*` column
   (`leagues.espn_league_id`, `owners.espn_owner_id`, `teams.espn_team_id`,
   `transactions.espn_transaction_id`, `players.espn_player_id`) to nullable.
   They are NOT NULL today on purpose: until a second platform is written,
   an ESPN row without its ESPN id is a bug. The `ck_*_espn_id` CHECKs
   already pass on a NULL ESPN id, so they need no change.
2. **An ingest mapping to these tables.** The platform's league, its
   seasons' settings, teams, owners, matchups, rosters, daily lineups,
   transactions and draft, written to the same tables with its own platform
   ids (the contract is in `app/platforms.py`'s docstring). ESPN's
   vocabularies travel with the rows today and the mapping has to translate
   into them, not add a second one: category `stat_id`s are ESPN stat ids,
   lineup slots and eligibility are ESPN's slot names, `pro_team_id` is
   ESPN's numbering of NBA teams, and injury statuses are ESPN's words.
3. **A player-matching step across platforms.** A player on the new
   platform is first matched to an existing `players` row by name, NBA team
   and position, and only then gets his `player_platform_ids` row. A name
   that matches no one or more than one is refused and reported for a person
   to settle, never guessed, the rule `app.draft.bbm.match_player` already
   applies to Basketball Monster's names (exact on the name key, the most
   recent season breaking a tie, a tie refused, then one loose match or
   nothing). A wrong match silently gives one man another's stats, which is
   worse than a gap. A player no platform has shown us gets a new `players`
   row.
4. **Its own login and listener.** `league_connections` seals ESPN's two
   cookies; another platform has OAuth (Yahoo) or no login at all (Sleeper).
   The listener reads ESPN's player pool and news. Team claims prove an owner
   by ESPN's SWID (`user_espn_identities`); another platform needs its own
   proof.
5. **URLs.** The pages and routes are keyed on ESPN league and team ids
   (`/l/{espn_league_id}/...`). A second platform's league needs an address
   that does not collide with one, for instance `/l/{platform}/{id}`, with
   the ESPN form kept as today. `app.platforms.league_by_platform_id` is the
   lookup that would sit behind it.

## Not covered: roto and points

Yahoo, Fantrax and Sleeper leagues are often rotisserie or points leagues.
Everything this project values, the categories won, the matchup a move
tilts, a pickup judged "week and season in categories", a draft price in
category value, is head-to-head categories. Roto changes the currency to
standings points across a season, and points to a single weighted total;
both change what a player is worth and what a move is for, which is a rethink
of the scoring package and not a mapping. docs/product.md names it as the
harder change and keeps it out of this plan, and so does this step: a
second platform's ingest can land in these tables, but only an H2H
categories league would mean anything to the code that reads them.
