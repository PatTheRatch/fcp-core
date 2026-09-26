# Where projections come from, and which of them may leave this machine

**Written:** 2026-09-17, after the BBM store and the in-season listener landed.
**Status:** steps 2 and 3 below are built (2026-09-18), and sources on the
draft plan page -- the mapping, the chooser, composites -- on 2026-09-26
("Sources on the draft plan page", below); the gate's ownership question
still waits on accounts. Nothing here changed how the draft room's
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

## Sources on the draft plan page, 2026-09-26

The owner: "we should be able to upload projections here [on the draft plan
page] ... then we can name that source and it comes up. from here, we'd be
able to select from different sources, or ... a consensus one where you can
add weights." Built: SOURCES on the plan page (and on `/account/projections`,
whose how-to that sent people to `/docs` is gone), the source chooser, and
composites. docs/draft_plan.md has the page.

**The mapping he can fix.** `upload.py` stays the one parser; the page's
mapping is the existing `map` override, extended to every field (`FIELDS`:
name, games, PTS, REB, AST, STL, BLK, 3PM, TO, FGM, FGA, FTM, FTA, FG%, FT%,
and the optional team, position, minutes, value and injury -- the last
three new, stored on `projection_rows`) and sent whole (`exact=true`: only
his entries are applied, so a field he set to none stays none). The preview
now returns the file's headers, the first three values under each, every
field with its column (or, for a makes field, "FG% x FGA"), the basis with
its reason ("the median of 'Pts/G' is 15.8, not above 200: per-game
numbers"), and `basis` can be forced (`per_game` or `totals`; `auto`
measures). The page blocks Store while a required field has no column or
one column is chosen for two fields, and says which; the server refuses a
percentage without attempts with the same reason as before.

**The stored mapping.** A name is the source: unique per owner and season
(migration 0033; any stored twins were renamed "name (2)" first). A file
stored under a name he already keeps replaces that set's rows in place and
keeps its id, so a composite that reads it knows to rebuild, and a plan on it
is keyed on the new upload time. `projection_sets.mapping` keeps the mapping
it was stored with -- `{"fields": {field: header | null}, "basis": "auto" |
"per_game" | "totals", "headers": [...]}` -- beside `column_map`, which is how
the file was actually read. A file previewed or stored under that name with
no mapping sent is read with it first (`upload.last_time`): "as last time",
or "partly as last time" naming the fields whose column is gone, which the
synonyms then guess. A mapping he fixes is his, for that file; it never
edits `SYNONYMS`.

**The synonyms, widened.** From memory of the sites' own tables, nothing
downloaded: Hashtag Basketball (`TREB`, `MPG`), Rotowire (`MIN`),
FantasyPros (`Positions`), Yahoo (`GP*`, `3PTM`, `ST`; a trailing asterisk is
now dropped from every header), ESPN's own table (`PLAYER`, `MIN`), a
hand-made sheet (`Points`, `Field Goals Made`, `Free Throws Attempted`,
`Threes Made`, `3PT`, `TOV/G`), and `$`, `Auction $`, `Proj $`, `Dollars`,
`Value` for value; `Inj`, `Injury`, `Health` for injury. **Unsure:** whether
FantasyPros writes its positions as `Positions` or folds them into the name
cell ("Name (TEAM - POS)", which would need splitting); Hashtag's `TOTAL`
is a z-score total, not dollars, and is left unmapped; `Value` is last among
value's spellings because Basketball Monster's `Value` is a z-score, and a
file whose `Value` is dollars will still read right when it has no `$`.
**Not handled:** a single `FGM/FGA` or `FGM/A*` cell holding "7.2/13.4"
(ESPN's and Yahoo's tables) has to be split into two columns first.

**The chooser.** Every pool the viewer may plan on, from one list
(`app/projections/catalog.py`, `GET /projections/sources`) that SOURCES, the
plan header and `draft_board` all read: BBM only to the member who owns the
captures, ESPN, his uploads, his composites (one that reads BBM only when he
owns BBM too). The owner of the captures sees `BBM · ESPN · his sets`;
another manager of the same team sees `ESPN · his own sets`, never BBM and
never the owner's sets.

### Composites

A composite is a named source built from other sources with weights: a
`projection_sets` row of kind `composite` (migration 0033), its `recipe`
`[{source, weight}]` (`source` is `"bbm"`, `"espn"` or one of his upload
ids; weight 0-100, 0 not read), its rows materialised by
`app/projections/composite.py` and rebuilt whenever an input changes. The
recipe is the truth; the rows are a cache, and `built_from` records what
they were worked out from -- the BBM capture's date, each input set's upload
time, a fingerprint of ESPN's projections (rows, points, games), the weights
-- and when, with how many men each count of sources carried. They are
rebuilt when that record no longer matches (reading SOURCES, planning on
the composite) and at once when one of its sheets is uploaded again; not
otherwise.

**The rules, per player, over the sources that carry him:**

- per-game rates are the **weighted mean**, the weights renormalised over
  the sources present: a man BBM has and an upload lacks is BBM's number at
  full weight, and his row says so (`raw`: `{"sources": ["bbm"], "of": 2}`);
- **games** are the weighted mean the same way;
- **the two percentages come from weighted makes and attempts**, never from
  averaging percentages: FGM, FGA, FTM and FTA are each blended like any
  count and the room's FG% is makes over attempts (`app/scoring/lines.py`);
- **minutes and value** are the weighted mean where present, renormalised
  over the sources that carry them, null otherwise;
- **position, team and injury** come from the first source in the recipe
  that names one;
- a player present in no source is absent.

Matching across sources is the id each source already gives a man: his ESPN
id where it placed him (BBM and an upload through the strict matcher, ESPN
by its own id), else the synthetic id of his name, so two sources' unmatched
rows merge only on an exact `name_key`.

The fixture (`tests/test_projection_composite.py`): ESPN at 30 and a sheet
at 70; Mobley in both (ESPN 20 points on 70 games, 8/14 from the field; the
sheet 17 on 66, 7/16) comes out 17.9 points on 67.2 games and 7.3/15.4 =
.474 from the field -- where averaging the percentages would have said .478;
Garland, ESPN's alone, is ESPN's line at full weight, "1 of 2"; a rookie
only the sheet carries is the sheet's line under a synthetic id.

**The gate.** A composite that includes BBM (at any weight above none) is
gated exactly like BBM: a per-player number that is half BBM's is still
BBM's number to anyone who is not the member. `sources.py` learns it from
the recipe: the tag is `composite:9+bbm` (`composite_source`), `is_gated`
reads the tag, and `may_show` answers it with no lookup. Only a viewer who
may plan on BBM can put BBM in a recipe (`check_recipe`), and a BBM
composite is withheld from anyone else even if the set were theirs. One
without BBM is its owner's, like an upload.

**What is not done**, to the best of our ability and no further:

- **No source calibration.** A weight is the manager's, not measured from a
  source's past accuracy. A candidate study for after the draft: which
  source's 2026 projection came closest to the 2026 season, by category
  (ESPN's stored 2026 projections against the stored box scores; any 2026
  upload a manager still has) -- which would let the page show, beside a
  weight, how that source has done, never set the weight itself.
- **No injury-aware blending.** Two sources' games are averaged like any
  number, whatever each thought of a man's health.
- **No positional eligibility merge** beyond the first source that names a
  position; eligibility is ESPN's own line where we hold one, as for any set.
- **No availability promise.** The room discounts a composite's games like
  an upload's or ESPN's, even with BBM in it.
- **No composite of composites.**

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
