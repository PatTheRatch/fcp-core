# Sleeper: what the league carries, and what supporting it would take

**Written:** 2026-09-19. **Status:** nothing is built but the probe. `app/sleeper.py`
fetches and `scripts/sleeper_probe.py` prints; no table, migration or ingest
exists, and no route knows Sleeper. This is the reconnaissance document that
docs/platforms.md's step 2 ("an ingest mapping to these tables") would be
written against.

**The league in question:** `1404516094114377728`, "GOAT League".

## Verified, and against what

**Verified on 2026-09-19** by running every pass of the probe against
league `1404516094114377728` from an ordinary connection (the first draft of
this document was written behind an egress policy that blocked
`api.sleeper.app`, so none of it had been checked). Every field name below
marked *verified* was seen in a live response that day. The raw output was
kept under `data/sleeper/` on the machine that ran it, and is not committed.

The league was **pre-draft** when probed: created 2026-09-12, 7 of its 14
seats taken, no player on any roster, the auction scheduled for 2026-10-18
17:30 UTC and the NBA season starting 2026-10-20. So everything about the
*structure* is verified, and nothing about the *season* is: `matchups`,
`transactions` and draft picks all came back empty. The claims about them
below are still Sleeper's published shape and are marked *unverified*. Run
`--draft` after 2026-10-18 and `--week 1` after the first week to settle
them.

**The three answers:**

1. **Sport: `nba`.** Basketball, so the exercise goes on.
2. **Scoring: head-to-head points.** Not categories, not roto. Sixteen of
   the 28 `scoring_settings` keys carry a weight, and the weights are a
   points formula: `pts 0.5, reb 1, ast 1, stl 2, blk 2, to -1, fgm 1,
   fgmi -1, ftm 0.5, ftmi -0.5, tpm 1, pf -1, td 2`, plus threshold bonuses
   (`bonus_pt_40p 1`, `bonus_reb_20p 1`, `bonus_ast_15p 1`). A categories
   league has no use for a negative weight on a missed shot or a bonus for
   crossing 40 points. The draft's `metadata.scoring_type` is `"std"`, and
   the league carries no settings key that names a scoring type at all
   (`game_mode` is `1`, the same value as every other Sleeper NBA league
   seen, points leagues included). Every Sleeper NBA league looked at that
   day, well over fifty of them, had the same kind of weighted, partly
   negative points map; none looked like categories. The one check left, for
   completeness: in the Sleeper app, open the league, then *League* →
   *Settings* → *Scoring Settings*. A points league lists a points value
   beside each stat (Points 0.5, Rebounds 1, …). Anything that says
   "categories" or lists stats without values would overturn this, and
   nothing in the API suggests it will.
3. **Player ids: none of them carry `espn_id`.** The NBA player dump has
   an `espn_id` key on 2087 of its 2117 entries and a value on **zero**. The
   league rosters nobody yet, so the probe measured the draftable pool
   instead (586 active players on an NBA team): `espn_id` 0 of 586,
   `yahoo_id` 0. The ids Sleeper *does* fill are `sportradar_id` (586),
   `fantasy_data_id` (580), `rotowire_id` (577) and `kalshi_id` (541), and
   fcp-core stores none of them. The join to `players` is a name match
   (finding 5), for every player, not just a remainder.

**The shape:** one season, no history (`previous_league_id` null); a
**redraft** league (`settings.type` 0) of 14 teams; starting lineup
`G G F F C UTIL`, 5 bench, 1 IR (`settings.reserve_slots`), no taxi;
**auction** draft with a $200 budget over 11 rounds; **FAAB** waivers
(`waiver_type` 2) with a $100 budget; 6 playoff teams from week 22; trade
deadline week 18. No transactions yet and no traded picks. Pick trading is
switched on (`pick_trading` 1) and so is a keeper allowance (`max_keepers`
6, `metadata.keeper_deadline` "3") even though the league is redraft; in a
first season with an auction draft neither can do anything yet.

**The three claims this document was built on:**

* *A Sleeper league id is one season, chained by `previous_league_id`.*
  **Confirmed**, with two wrinkles. This league's id carries
  `season: "2026"` and nothing before it. Other NBA leagues seen that day
  point their 2026 id at a 2025 one exactly as described. But "no previous
  season" comes back as either `null` or the string `"0"`, and a bare truth
  test on `"0"` walks into a league that does not exist
  (`sleeper.previous_league_id` now handles both). And
  `metadata.copy_from_league_id` is not a predecessor (finding 1).
* *There is no daily lineup history, and it is not backfillable.*
  **Confirmed** (2026-09-19, `scripts/league_survey.py sleeper`) on a
  completed 2025 NBA season of a public league: every week's matchup row
  carries exactly `custom_points, matchup_id, players, players_points,
  points, roster_id, starters, starters_points`, with one `starters` list
  of nine for the whole week and no per-day key in any of the 23 weeks.
* *The player dump carries `espn_id`.* **Refuted** for the NBA. The key is
  there, and it is null on every player (answer 3).

## The API surface

Public, anonymous, read-only. There is no login, no OAuth, no cookie, and no
write endpoint of any kind. Base `https://api.sleeper.app/v1`. Sleeper asks for
under 1000 calls a minute, and that the player dump be pulled at most once a
day. The probe now caches the dump for the day under `data/sleeper/`.

| Endpoint | Gives | Cost | Seen live |
|---|---|---|---|
| `GET /league/{id}` | the season's settings, scoring, roster slots, status, draft id, previous season's id | 1 | yes |
| `GET /league/{id}/users` | one row per person who has joined: user id, display name, team name, commissioner flag | 1 | yes |
| `GET /league/{id}/rosters` | one row per seat: owner, co-owners, players, starters, reserve, taxi, keepers, record, FAAB spent | 1 | yes |
| `GET /league/{id}/matchups/{week}` | every team's line for that week: points, starters, per-player points | 1 per week | empty (pre-season) |
| `GET /league/{id}/transactions/{week}` | adds, drops, waiver claims, trades filed that week | 1 per week | empty (pre-season) |
| `GET /league/{id}/winners_bracket`, `/losers_bracket` | the playoff and consolation brackets, **laid out from creation** | 2 | yes |
| `GET /league/{id}/traded_picks` | future draft picks that changed hands | 1 | yes (empty) |
| `GET /league/{id}/drafts` | the league's drafts | 1 | yes |
| `GET /draft/{id}`, `/draft/{id}/picks` | draft settings and every pick with its price | 2 | yes (no picks yet) |
| `GET /state/nba` | the current week and season | 1 | yes |
| `GET /players/nba` | every player Sleeper knows, with cross-platform ids | 1 per day, 2.7 MB | yes |
| `GET /user/{user_id}/leagues/nba/{season}` | every league a user is in that season | 1 | yes, not in the first draft |

**A whole season is roughly 60 calls**, well inside the limit, and a full
history is that times the number of seasons. The player dump is the only
expensive one and it is shared across every league.

`/state/nba` on 2026-09-19 said `season_type: "off"`, `week: 0`,
`season_start_date: "2026-10-20"`, `previous_season: "2025"`. Its `leg` and
`display_week` are the fields to stop a live season's sweep at.

Undocumented endpoints exist (`/stats/nba/{season}`, `/projections/nba/{season}`,
per-player weekly splits). They are real and widely used, but they are not part
of the published contract and should not be load-bearing. Our projections come
from Basketball Monster and our own uploads, so nothing needs them.

## What Sleeper returned that the first draft did not expect

Each of these was in a live response and is now handled or noted in
`app/sleeper.py` and the probe.

* **IR is not a roster position.** `roster_positions` is
  `G G F F C UTIL BN BN BN BN BN`, with no `IR`. The IR count is
  `settings.reserve_slots` (1) and who is on it is each roster's `reserve`
  list. The first draft counted `roster_positions.count("IR")` and would
  have mapped zero IR slots. The same holds for taxi (`taxi_slots`, `taxi`).
* **An empty starting slot is the string `"0"`.** `roster.starters` has one
  entry per starting slot even when the roster is empty, and every entry is
  `"0"`. An ingest that reads `starters` as player ids would look up player
  `"0"`.
* **Rosters carry more than players.** `reserve`, `taxi`, `keepers` and
  `player_map` sit beside `players` and `starters`. `settings` adds
  `waiver_position`, `total_moves` and `fpts_decimal` (points are split
  across `fpts` and `fpts_decimal`).
* **Open seats have a roster and no user.** Seven rosters have
  `owner_id: null` and no row in `/users`, because nobody has joined yet.
  After the draft the same shape means an orphaned team. `metadata.is_open`
  on the league says which.
* **`users[].is_owner` is the commissioner flag**, not team ownership.
  Only the commissioner had it set (to `true`); everyone else had `null`.
  A manager who never named his team has `metadata` with only notification
  preferences (`allow_pn`, `mention_pn`) in it, so the display-name fallback
  applies to six of the seven.
* **The brackets exist before the season.** `winners_bracket` returned 7
  games and `losers_bracket` 12 on a pre-draft league, seeded provisionally.
  Each game has `r` (round), `m` (game number), `t1`/`t2` (roster ids, null
  until fed), `t1_from`/`t2_from` (`{"w": m}` or `{"l": m}`), `w`/`l`
  (null until played) and, on placement games, `p` (1 the final, 3 third
  place, 5 fifth). "Has a bracket" does not mean "reached the playoffs";
  only a non-null `w` does.
* **`previous_league_id` can be `"0"`**, meaning none (above).
* **`metadata.copy_from_league_id`** marks a league made by copying another
  league's settings. It can sit beside a null `previous_league_id`, and it
  is carried forward unchanged into later seasons, so it records where the
  settings came from and must not be walked as history.
* **The player dump has 30 team pseudo-players.** Each NBA team appears
  keyed by its abbreviation (`"BKN"`), position `DEF`, with no `full_name`.
  They are not people; an ingest must skip non-numeric ids.
* **The draft repeats the roster shape** as `settings.slots_g`, `slots_f`,
  `slots_c`, `slots_util`, `slots_bn`, and has a `start_time` in epoch
  milliseconds.
* **Two numbers disagree and are not explained.** `settings.draft_rounds`
  is 3 while the draft's own `settings.rounds` is 11 (which is the roster
  size, as an auction's should be). And the draft's `metadata.league_type`
  is `"1"` while `settings.type` is 0. Take draft facts from the draft
  resource and league facts from the league, and trust neither of these two.
* **Settings the first draft did not list:** `pick_trading`, `bench_lock`,
  `daily_waivers`, `daily_waivers_days` (5461, apparently a bitmask of
  days), `daily_waivers_hour`, `waiver_clear_days`, `waiver_day_of_week`,
  `waiver_after_game_start`, `trade_review_days`, `offseason_adds`,
  `disable_adds`, `capacity_override`, `playoff_round_type`,
  `playoff_seed_type`, the `reserve_allow_*` flags (which injury statuses
  may go on IR: here `out`, `sus` and `dnr`), the `taxi_*` settings and
  `game_mode`. Completed leagues also carry `settings.last_scored_leg` and
  `metadata.latest_league_winner_roster_id`, which is the champion.
* **The league object is also a chat channel.** `last_message_*`,
  `last_author_*`, `last_read_id`, `shard` and `group_id` describe the
  league's chat. None of it is league data, and `raw_settings` should not be
  expected to be stable while people are talking.
* **Stat keys the mapping has to reckon with.** Besides the obvious ones,
  `scoring_settings` has `fgmi`, `ftmi` and `tpmi` (misses), `dd` and `td`
  (double- and triple-doubles), `pf`, `tf` and `ff` (personal, technical
  and flagrant fouls), `plus_minus`, `sp`, `oreb`/`dreb`, `tpa`, and three
  threshold bonuses. Most have no ESPN stat id, and the bonuses are not
  stats at all.

## Where Sleeper and ESPN disagree, structurally

These are the findings that matter. Each one is a decision, not a field rename.

### 1. A Sleeper league id is one *season*, not one league — confirmed

ESPN keeps a single league id for a decade and asks which year you want.
Sleeper mints a **fresh league id every season** and links the previous one
through `previous_league_id`. A league's history is a walk back down that
chain, which `sleeper.league_history` does. The walk stops at `null` or
`"0"`; `metadata.copy_from_league_id` is not part of it.

This breaks an assumption docs/platforms.md states outright: *"`league_seasons`
and `draft_picks` need nothing. Neither holds a platform id: a season is
(league, year)."* That is true of ESPN and false of Sleeper. A Sleeper league
has **one platform id per season**, and there is nowhere to put them.

The fix is small but it is a schema change: `leagues.platform_league_id` holds
the identity of the chain (the oldest id in it, which never changes as seasons
are added), and `league_seasons` gains a nullable `platform_season_id` for that
season's own league id — plus `platform_draft_id`, since Sleeper's draft is a
separate resource with its own id (verified: `1404516097343889408` here)
while ESPN ships the draft inside the league.

Using the *newest* id as the league identity is the trap: it changes every
autumn, and the league would fork into a new row each season. For this league
the question is academic until 2027: it is its own first season, so its
oldest id and its newest are today the same.

### 2. There is no daily lineup history — confirmed

This is the biggest capability gap and it is not fixable by mapping.

`daily_lineup_slots` — "the finest grain ESPN exposes, and the one that makes
narratives possible: who was benched, who was started while injured" — has no
Sleeper equivalent in any response seen. The roster object holds one current
`starters` list with nothing dated, and `GET /matchups/{week}` is documented to
return `starters` as the lineup *for that week*. Sleeper does not serve a
per-day historical record of who sat where. What is gone is gone.

Confirmed on 2026-09-19 against a completed 2025 season of a public NBA
league reached through GOAT League's members (`scripts/league_survey.py`):
23 weeks, each matchup row a single `starters` list for the week, a
`starters_points` per slot and a `players_points` per player, and no dated
or per-day field anywhere. A week's score is all Sleeper keeps.

**"Lock-In" is not in the data.** Sleeper's basketball leagues are sold as
Lock-In points leagues (a manager locks in one day's score per slot for the
week). No league document, setting or matchup row names it: in the API a
Lock-In league is indistinguishable from any other points league, and the
locked day is not recorded, only the week's points per slot. All 69 NBA
leagues read in the survey were points leagues; none was categories or roto,
which matches Patrick's reading of the app, where Lock-In points is the only
basketball format offered.

So: for a Sleeper league, daily lineups are **collectible going forward by
polling and not backfillable**. Whatever a listener snapshots each day is all
there will ever be. For this league that costs no history, because there is
none, but every day of 2026-27 not snapshotted is lost for good. Whether to
snapshot is the product call at the end of this document.

`player_game_stats` is a separate question and in better shape: it is NBA fact,
not league fact, and could be filled from any stats source we already trust
rather than from Sleeper.

### 3. Points, not categories — confirmed for this league

`scoring_settings` is a map of stat key to weight, and in this league it is a
points formula (answer 2 above). A matchup row is documented to carry
`points`, `starters_points` and `players_points` — one number per team, per
starter, per player (unverified until week 1).

`matchups.home_categories_won` / `categories_lost` / `categories_tied` and
`teams.categories_won` have no meaning in a points league. Neither does
`league_season_categories`, whose whole content is "which nine categories does
this league score", nor `matchup_team_stats`. They would sit at zero, and every
report built on them would render an honest-looking nothing. That includes
`app/narratives.py`, which reads `LeagueSeasonCategory` and `MatchupTeamStat`
as well as the lineups.

The league's *structure* still ingests fine: identity, rosters, transactions
and the draft. The scoring package, the pickups judgement ("week and season in
categories") and the category draft prices simply do not apply. Say that out
loud rather than shipping empty category tables.

Sleeper carries no field that names the scoring type. If a Sleeper NBA
categories league exists, none was among the leagues seen on 2026-09-19, so
what flag it would carry is unknown. The probe prints the tells (negative
weights, threshold bonuses) and still leaves the verdict to a person.

### 4. Vocabularies have to be translated into ESPN's, not added alongside

docs/platforms.md is firm on this and it is the right call: "ESPN's
vocabularies travel with the rows today and the mapping has to translate into
them, not add a second one". Four translations are needed, and each is a lookup
table someone has to write and check:

* **Stat keys → ESPN stat ids.** `pts` → ESPN's id for points, and so on for
  every scored stat. `league_season_categories.stat_id` is an ESPN stat id by
  definition. In a points league it is moot, and several of Sleeper's keys
  (the misses, the bonuses, `sp`) have no ESPN counterpart anyway.
* **Roster slots → ESPN slot names.** This league uses
  `G G F F C UTIL BN` (verified); Sleeper leagues can also use `PG SG SF PF`.
  ESPN says `PG SG SF PF C G F UT BE IR`. `UTIL` is not `UT` and `BN` is not
  `BE`, and IR arrives as a count and a list, not as slots (above).
  `daily_lineup_slots.slot` documents the ESPN set.
* **Pro team abbreviations → ESPN's `pro_team_id` numbering.** Sleeper gives
  `"LAL"` (verified: 30 abbreviations in the dump); our rows carry ESPN's
  integer for the Lakers. Players with no team have `team: null`.
* **Injury statuses → ESPN's words.** Sleeper's `injury_status` in the dump
  on 2026-09-19 took the values `DTD`, `Out` and `IR` (101 players with one),
  and the league's `reserve_allow_*` settings imply a longer list (`cov`,
  `dnr`, `doubtful`, `dtd`, `na`, `out`, `sus`). The roster `status` field is a separate
  vocabulary: `ACT`, `FA`, `RET`, `TWO-WAY`, `NWT`, `M-LEAGUE`, `DUP`, `SUS`
  and one stray `Active`.

### 5. Player identity is a name match, not an id join — refuted and rewritten

The first draft said every entry in `/players/nba` carries `espn_id`, so the
matching step would shrink to a remainder. **It does not.** On 2026-09-19 the
key was present and null on every NBA player; `yahoo_id`, `stats_id`,
`gsis_id`, `rotoworld_id`, `opta_id` and `pandascore_id` were null
throughout too.

What is filled, over the 586 active players on an NBA team:

| field | filled | ours? |
|---|---|---|
| `sportradar_id` | 586 | no |
| `fantasy_data_id` | 580 | no |
| `rotowire_id` | 577 | no |
| `kalshi_id` | 541 | no |
| `swish_id` | 418 | no |
| `oddsjam_id` | 414 | no |

fcp-core stores none of these, so none is a bridge today. That puts **every**
Sleeper player through docs/platforms.md step 3: matched to an existing
`players` row by name, NBA team and position; **refused and reported rather
than guessed** when there is no match or more than one, exactly as
`app.draft.bbm.match_player` already does.

It is less bad than it sounds. Put through our own `app.draft.bbm.name_key`,
no two of the 586 `full_name`s collide (4 pairs do across all 1797 active
entries, each involving at least one player with no team). Sleeper drops generational
suffixes from `full_name` ("Jaime Jaquez", not "Jr."), which `name_key`
strips on our side too. Each entry also carries `team`, `fantasy_positions`
and `birth_date`, a strong tie-breaker if our side ever records one. Use
`name_key` on `full_name` rather than Sleeper's own `search_full_name`,
which is a different key (no spaces). Once a player is matched, his `sportradar_id` could
be stored as a second `player_platform_ids` row (platform `'sportradar'`)
and every later platform that speaks Sportradar would join by id. That is a
design choice for the bridge step, not something to do now.

### 6. No credentials, but also no proof of identity

docs/platforms.md step 4 mostly evaporates: there is nothing to seal in
`league_connections`, no cookie to expire, no OAuth dance. A Sleeper league is
public, so connecting one is just recording its id. Verified: every call in
this document was anonymous.

The cost is on the other side. Team claims are verified today by a member's
ESPN SWID (`user_espn_identities`). Sleeper hands out no such secret — a user
id and display name are public to anyone who reads the league — so **a Sleeper
claim needs its own proof**, and "I say I am this user" is not one. A workable
shape: ask the claimant to put a short code we give them into their Sleeper
team name, then read it back through `/users` (`metadata.team_name`,
verified to be there when set). That is a design task, not a mapping task,
and it is the only part of accounts that gets harder.

### 7. Moves that carry no player

Sleeper transactions are documented to move **FAAB between teams**
(`waiver_budget`) and **future draft picks** (`draft_picks`), both commonly
inside a trade. `transaction_items` has `player_id` NOT NULL and holds nothing
else, so a FAAB-for-picks trade has no representation at all — it would ingest
as a transaction with no items, which reads as an empty trade rather than a
real one. There is likewise no table for `traded_picks`.

For this league it does not arise yet: it is redraft, it has no transactions
and `traded_picks` is empty. Pick trading is switched on, but with an auction
draft there is no pick order to trade. The shape of a transaction is
unverified until the first waiver run.

## The mapping, table by table

Assuming an NBA categories league; the columns that only a categories league
fills are marked, and **this league is points**, so those stay empty.
*Verified* means the source field was seen in this league's live response on
2026-09-19.

| our table | from | verified | notes |
|---|---|---|---|
| `leagues` | the oldest id in the `previous_league_id` chain | yes | `platform` = `'sleeper'`; `espn_league_id` must become nullable |
| `league_seasons` | `GET /league/{id}` per season in the chain | yes | `season` from `league.season` (a string, `"2026"`); needs a new `platform_season_id` (finding 1) |
| `league_seasons.lineup_slots` | `roster_positions` minus `BN` | yes | translate `UTIL`→`UT` (finding 4) |
| `league_seasons.bench_slots` | `roster_positions.count("BN")` | yes | 5 here |
| `league_seasons.injured_reserve_slots` | `settings.reserve_slots` | yes | **not** `roster_positions.count("IR")`, which is 0 |
| `league_seasons.acquisition_budget` | `settings.waiver_budget` | yes | 100 here; the in-season FAAB pot |
| `league_seasons.auction_budget` | the draft's `settings.budget` | yes | 200 here; a different pot, as it is on ESPN |
| `league_seasons.uses_faab` | `settings.waiver_type == 2` | yes | 0 rolling, 1 reverse standings, 2 FAAB |
| `league_seasons.playoff_team_count` | `settings.playoff_teams` | yes | 6 here |
| `league_seasons.regular_season_periods` | `settings.playoff_week_start - 1` | yes | derived, not reported: 21 here |
| `league_seasons.keeper_count` | `settings.max_keepers` | yes | 6 here, although `type` is redraft; store it and do not infer keeper-ness from it |
| `league_seasons.draft_type` / `seconds_per_pick` / `draft_order` | the draft's `type`, `settings.pick_timer`, `slot_to_roster_id` | yes | `"auction"`, 120, identity map here; ignore `settings.draft_rounds` (finding above) |
| `league_seasons.raw_settings` | the whole league object | yes | keep verbatim, but the chat fields change constantly |
| `league_season_categories` | `scoring_settings` keys | yes | **categories only**; not this league |
| `owners` | `GET /users` → `user_id` | yes | `platform` = `'sleeper'`, `platform_owner_id` = `user_id`; a global id, which suits the table |
| `teams` | `GET /rosters` → `roster_id` | yes | `platform_team_id` = `roster_id`; name from the *user's* `metadata.team_name`, falling back to `display_name`; an open seat has no user |
| `team_owners` | `roster.owner_id` plus `roster.co_owners` | yes | `co_owners` is `null`, not `[]`, when there are none |
| `teams.categories_won/lost/tied` | — | — | **categories only**; zero and meaningless here |
| `matchup_periods` | week numbers 1..n | no | `is_playoff` from `settings.playoff_week_start`; scoring-period columns stay null — there are no days |
| `matchups` | two rows sharing a `matchup_id` | no | a null `matchup_id` is a bye; `winner` derived from `points`, since Sleeper does not say |
| `roster_slots` | `matchup.players` | no | who was held that week; `rosters.players` now |
| `daily_lineup_slots` | **nothing** | — | not backfillable (finding 2); only a daily poll can fill it, going forward |
| `matchup_team_stats` | — | — | **categories only**; a points league posts one number |
| `transactions` | `GET /transactions/{week}` | no | `platform_transaction_id` = `transaction_id`; `type` and `status` are Sleeper's words and need mapping to ESPN's; `bid_amount` from `settings.waiver_bid`; `scoring_period` is a *week*, not a day |
| `transaction_items` | `adds` / `drops` maps, and trade sides | no | FAAB and pick movement have nowhere to go (finding 7) |
| `draft_picks` | `GET /draft/{id}/picks` | no | `round`, `pick_no`, `roster_id`, `is_keeper`; the auction price is in the pick's `metadata`; empty until 2026-10-18 |
| `players` / `player_platform_ids` | `GET /players/nba` | yes | a name match for every player, refusal otherwise (finding 5); skip the 30 team entries |
| — | `winners_bracket` / `losers_bracket` | yes | no table; optional; present from creation |
| — | `traded_picks` | yes (empty) | no table; dynasty only |

One column deserves a flag: **`transactions.scoring_period` would hold a week
for a Sleeper league and a day for an ESPN one.** Two meanings in one column is
how a query silently returns nonsense later. Either the column is documented as
"the platform's own move bucket" or Sleeper's week is converted to the day it
started.

## The plan

In order, each step useful on its own, and none of it built past step 1.

1. **Run the probe and answer the three questions.** *Done, 2026-09-19*:
   NBA, head-to-head points, no `espn_id`, one redraft season, auction and
   FAAB. The in-season shapes wait for the draft and week 1.
2. **Decide on points.** This league is points, so `app/scoring` does not
   apply, and the honest options are: ingest structure only and serve rosters,
   transactions and draft history without valuations; or take on the scoring
   rethink docs/platforms.md defers. Do not ship empty category tables. Until
   that is decided, steps 3 to 7 have nothing to serve.
3. **The enabling migration.** docs/platforms.md step 1 (add `'sleeper'` to
   `PLATFORMS` and the `ck_*_platform` CHECKs; relax the five `espn_*` columns
   to nullable) **plus** `league_seasons.platform_season_id` and
   `platform_draft_id`, which that document did not anticipate (finding 1).
4. **The player bridge.** Pull `/players/nba` once, match every player by
   name, team and position, write `player_platform_ids` rows, and produce a
   report of the refusals. Do this before the league ingest: every other
   table's rows point at players, and a player the bridge refuses is a row
   the ingest cannot write. It is now the whole pool, not a remainder.
5. **The season ingest.** `app/sleeper_ingest.py` mapping one league season into
   the tables above, walking `previous_league_id` for history. It is about 60
   calls a season, so `--all-seasons` is cheap, and re-running is idempotent on
   the same platform ids.
6. **Decide on daily lineups.** Below.
7. **Claims and URLs.** A Sleeper proof of ownership (finding 6), and an
   address that does not collide with an ESPN league — docs/platforms.md
   proposes `/l/{platform}/{id}` with today's ESPN form kept, and
   `app.platforms.league_by_platform_id` is the lookup behind it.

## Daily lineups: the recommendation

**Do not build a Sleeper listener now.** Accept that the narrative features
do not exist for this league, and revisit only if points scoring gets onto
the roadmap.

The reasoning:

* **Nothing would read what it collects.** The features daily lineups feed
  (the narratives, "who was started while injured", the pickups judgement of
  what a team did with a player) are built on categories: `app/narratives.py`
  reads `LeagueSeasonCategory` and `MatchupTeamStat` next to the lineups, and
  the pickups judge moves "week and season in categories". In a points
  league they have nothing to say even with a perfect lineup record.
* **A listener is not a small thing here.** It needs everything before it:
  the enabling migration (step 3), the player bridge (step 4, now a name
  match for all 586 players), and a league row to hang the snapshots on. The
  ESPN listener cannot host it. `player_status_snapshots.on_team_id` is one
  league's view of who holds whom and is not keyed by league (docs/jobs.md,
  "One listener league"), so a Sleeper league writing there would overwrite
  the listener league's ownership and turn shared players into streams of
  phantom drops and claims. Keying the snapshots by league is the
  prerequisite, and it is not needed for anything else yet.
* **What is at stake is small and bounded.** The league has no past, so no
  history is being lost by waiting, only the 2026-27 season's days from
  2026-10-20 on. The job queue (`app/jobs.py`) already knows how to run a
  per-league job three times a day, so if the points question is ever
  answered yes, the listener is a new job kind, not new machinery.

**If Patrick wants insurance anyway,** the cheap version is not a listener.
It is one `GET /league/{id}/rosters` a day (or three, matching the ESPN
passes), written raw and dated to disk or to a single JSON column, touching
no canonical table and needing no migration, bridge or league row. It costs
one call per poll and preserves `starters`, `players` and `reserve` for every
day of the season, to be mapped later if the scoring rethink ever happens. It
has to start by the first game day (2026-10-20) to lose nothing. That is a
decision to make before the draft, and doing nothing is a reasonable answer.

## What this does not cover

Roto and points scoring, which is the same open question docs/platforms.md
leaves open and step 2 above now forces. Sleeper's undocumented stats and
projections endpoints, which nothing should depend on. And the NFL, which
Sleeper serves and this repository does not.
