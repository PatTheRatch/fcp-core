# Where projections come from, and which of them may leave this machine

**Written:** 2026-09-17, after the BBM store and the in-season listener landed.
**Status:** a constraint and a plan, not built. Nothing here changes how the
draft room works today; it names what has to be true before anyone else uses
this, so the next piece is not built the wrong way round.

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

## What to do next, in order

1. Keep the draft plan artifact private. Nothing to build.
2. When the draft is over, tag every projection with its source through the
   room and the pages, so a BBM field can be switched off in one place.
3. Build the upload path before anyone else is invited to use this. It is also
   what makes the room testable against a projection set nobody pays for.
4. Ask BBM what a member may do with their numbers in a private tool.
