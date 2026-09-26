# Where projections come from, and which of them may leave this machine

**Written:** 2026-09-17, after the BBM store and the in-season listener landed.
**Status:** steps 2 and 3 below are built (2026-09-18); the gate's ownership
question still waits on accounts. Nothing here changed how the draft room's
numbers come out; what changed is that every output now names its source.

## The constraint

Basketball Monster's projections are behind a paid membership. Patrick has a
membership; nobody has given permission to republish the numbers. So:

- **BBM projections, and anything computed from them per player, stay private
  to the member whose account fetched them.** That covers the raw export, the
  stored rows (`bbm_projections`), BBM's dollar values, its analyst notes and
  confidence, and the board prices, ceilings and target rosters the room
  derives from them.
- **A second person using this software may not see them.** Not through an
  API, not in a shared page, not in a report.
- **Aggregates are not a loophole** unless they cannot be reversed into per
  player numbers. "The median $10-25 pick added 0.34 categories a week" is a
  measurement about this league. A table of every player's BBM value is the
  product.

Whether BBM would allow personal, non-public use by a member is worth simply
asking them; that answer would change what the gate has to do, not whether
there is one.

## Where it stands today

| Where BBM data sits | Exposure |
|---|---|
| `data/bbm/*.xls` | git-ignored, on Patrick's Mac and the VPS |
| `bbm_projections`, `bbm_captures` | the VPS's own Postgres, not published |
| The API (`app/api`) | tailnet-only, and serves no BBM field today |
| The draft plan page (`logs/draft-plan-2027.html`) | **carries BBM values, notes and confidence.** Published as a private artifact. Do not share the link |
| The draft screen (`app/draft/service.py`) | localhost, shows BBM fields |
| Season reports | no BBM data: the scoring package reads box scores only |

So nothing leaks today, and one page would if it were shared.

## The shape to build towards

**One projection source interface, three implementations.** The room already
takes `PlayerProjection` objects (`app/draft/valuation.py`); what is missing is
a named source behind them, carried with the numbers rather than assumed:

1. **ESPN** (public-safe). Already ingested for every season
   (`player_season_stats`, `kind="projected"`). Weaker than BBM on
   availability, which `app/draft/bbm.py` measures, but it is ours to show.
2. **BBM** (gated). Only for an account that supplies its own BBM credentials
   or its own export. Never served to anyone else.
3. **Upload** (public-safe, the answer for other people). A manager brings his
   own projections, from wherever he pays for them.

**What an upload needs.** A spreadsheet or CSV with one row per player. The
importer should read the header row and map columns itself, then show the
mapping for confirmation rather than demanding a fixed template:

- **Required:** player name; games; and either per-game or season totals for
  points, rebounds, assists, steals, blocks, threes, turnovers, plus field
  goals made and attempted and free throws made and attempted. The two
  percentage categories cannot be rebuilt from a percentage alone, which is
  why attempts are required, not optional (`app/scoring/lines.py`).
- **Optional:** team, position, minutes, a dollar value, an injury note.
- **Matching:** the strict name matcher already written (`app.draft.bbm.name_key`,
  `match_player`), which refuses a doubtful match rather than guessing, and
  reports what it could not place.
- **Storage:** the same versioned shape as `bbm_projections` (a row per player
  per change, with `first_seen`/`last_seen`), keyed by whose upload it is.

**The gate itself.** Whatever serves projections has to carry the source and
the owner, and refuse to render a gated source for anyone else: one check in
the API layer and one in the page builders, not a rule people remember.

## What was built, 2026-09-18

**The source rides with the numbers.** `PlayerProjection.source` is "espn"
(the default, because that is what the database has always held), "bbm" or
"upload:<set id>". `app/projections/sources.py` names them, says which is
gated (only BBM) and answers the one question:
`may_show(source, viewer_owns_source)`. `Room.projection_source` carries the
pool's source, and the draft plan JSON, the draft screen's state and every
card name it.

**The gate has a seam, not an answer.** Both page builders call `may_show`:
`scripts/draft_plan.py` refuses to write the page at all, because it is a file
on disk carrying a price, a ceiling and a target roster for every player, and
`app/draft/session.py`'s card returns the name and nothing derived. Both pass
`viewer_owns_source=True`, which is true of the only account there is. When
auth lands, that argument is the only thing that has to learn an answer.

**The first per-user answer (2026-09-26).** The draft plan route
(`app/api/draft_plan.py`, docs/draft_plan.md) is the first place the argument
is answered per viewer: the stored BBM captures are pulled with the site
owner's membership, so `viewer_owns_source` is `Viewer.is_owner` there, and a
plan on BBM is withheld from anyone else with the pools he may plan on
instead. The co-manager's `draft_board` asks the same function.

**Uploads.** `app/projections/upload.py` reads a CSV, .xlsx or .xls, maps the
header row by a synonyms table rather than demanding a template, measures
whether the file is per game or season totals from its own numbers, matches
names with the room's strict matcher and stores the set
(`projection_sets`, `projection_rows`; migration 0017).
`scripts/upload_projections.py` is dry by default and prints the mapping for
confirmation; `--commit` stores; `load_room(..., projection_set=<id>)` and
`--projection-set` draft on it. A file carrying a percentage and no attempts
is refused with the reason.

**The API.** `app/api/projections.py` is the same path over HTTP, for anyone
who is not going to run a script. `POST /projections/sets/preview` reads an
uploaded file and returns the mapping it guessed, the basis it measured and
who it could not match, storing nothing; `POST /projections/sets` stores the
set and returns its id, and refuses a file whose columns cannot be used with
422 and the same reasons the CLI prints. `GET /projections/sets?season=` lists
what is stored, `GET /projections/sets/{id}` is one set's metadata and
`GET /projections/sets/{id}/rows` its per-game lines. Nothing served there is
gated, because only BBM is; the check is asked anyway, on every response, and
`ProjectionSet.owner` is the field it compares the caller against once
accounts exist. The API still serves no BBM field.

**The draft screen and its pool.** `GET /api/pool` hands the whole board's
per-game lines to the screen in one response, because its strips, its scarcity
panel and its team standings are sums over the whole pool. It is derived per
player from the projections, so it asks the same `may_show` and, when the
answer is false, carries `"withheld": true` and nothing but names, positions,
eligibility and who bought whom; the screen then draws the board, the money and
the picks and says in each emptied place why it is empty. `docs/draft_room.md`
describes the screen, its dark and light palettes and the route in full.

**Both pages name the source.** The plan page carries a line under its title
and the draft screen carries one under its header, from
`sources.describe(source, detail)`: "Basketball Monster (paid; not to be
shared), pulled 2026-09-17", or an uploaded set's own name and note. A reader
should not have to ask what the board was built on.

Two things deliberately not done: the set is not versioned per player per day
the way `bbm_projections` is (a new upload is a new set, which answers "what
did I draft on" without the machinery), and an uploaded set is discounted for
availability like ESPN's, because unlike BBM's it makes no promise about it.

## What to do next, in order

1. Keep the draft plan artifact private. Nothing to build.
2. ~~Tag every projection with its source through the room and the pages.~~
   Done 2026-09-18.
3. ~~Build the upload path before anyone else is invited to use this.~~ Done
   2026-09-18, the CLI and ~~the upload route~~ (`app/api/projections.py`,
   2026-09-18) both.
4. Ask BBM what a member may do with their numbers in a private tool.
5. Accounts. Until they exist `viewer_owns_source` is a constant, and the API
   (`app/api`) still serves no BBM field, so nothing leaks; the moment a
   second person can sign in, that argument and the API's own check are what
   stand between them and the paid numbers.
