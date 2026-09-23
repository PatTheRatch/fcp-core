# The in-season pages

**Written:** 2026-09-18, when the two pickup reports were given a screen.

Three pages, served by the read-only API (`app/api/pages.py`) from files in
`app/api/static/`. Plain HTML, CSS and JavaScript: no framework and no build
step, for the reason the draft screen has none — a page that has to come up
on a tailnet from a file on disk should not need a toolchain. Each file is
read per request, so an edit shows on a refresh.

They are the CLI reports as a screen. `scripts/today.py`,
`scripts/stream.py` and `scripts/season.py` are the content spec:
everything those three print, the pages show, in the same words and the
same order. Nothing is computed in the
browser that the API has not already decided, with one exception noted under
*The tale of the tape*.

## The three

| Page | What it is |
| --- | --- |
| `/l/{league_id}/{season}/team/{team_id}/week` | The streaming report |
| `/l/{league_id}/{season}/team/{team_id}/season` | The rest-of-season report |
| `/l/{league_id}/{season}/standings` | What the index became: every team and its record |

Since step 3 (2026-09-19) the pages sit under the site's shell and at these
addresses; the old `/pages/teams/...` ones redirect here. docs/site.md is
the whole map.

`?today=N` is a scoring period and is exactly the CLI's `--today`: it is
passed straight through to both the report route and the context route, so
the page and the report it draws are always talking about the same day.
Left out, the day is the calendar day turned into a scoring period through
the stored schedule. `?me=` names whose team is ours, for anyone who is not
the manager the constant is written for.

## What is on the week page, top to bottom

**Today** (added 2026-09-22, `app/pickups/today.py`). The morning question,
and the first thing on the page, because it is the first thing the manager
does. The date and the scoring period; how many NBA teams play and how many
of the ten places this roster can fill; then two lineups side by side (one
above the other on a phone):

* **As we would set it** — a place a line, in the league's own slot order,
  with the man's name, the game his NBA team plays ("at MIL"), and a flag
  when ESPN carries a status. A place nobody on the roster can fill says so
  in the muted style rather than being left blank.
* **As it is set** — the same grid from `daily_lineup_slots` for the day.
  On a day the ingest has not reached there is no such row, and the page
  says the lineup has not been read from ESPN yet rather than comparing
  against an empty one.

Between the lede and the grids, in the warn style, **the one thing this page
calls a mistake**: a place in the set lineup that will produce nothing
tonight — the man in it has no game, or it was left unset — while a man on
the bench has a game and fits that very place. It names who could take it
and says how many places each lineup fills. Only the places the bench can
fill *at once* are listed, which is the same matching again, so one man
eligible for two open utility places is one place to fix and not two.

Under them, **Not in the lineup**: everyone held who is not starting. A man
with a game carries a rule in the accent and says why he is not in it (no
place he is eligible for, or the better men who took the ones he fits); a
man with no game is faint, and the middle column has already said why.

On a day no NBA team plays — the All-Star break — it says so in one line and
draws no grid. Ten rows of "nobody" would be ten rows of noise.

It is the **only** part of the page drawn before the rest. The week searches
the whole wire for every possible move and takes the better part of half a
minute; one day's seating comes back at once, and the names come with it, so
the masthead stops saying "Loading..." while the plan is still being built.
A day that cannot be built hides its own section and leaves the week
standing (`quiet` in `pages.js`'s `get`).

Every player name opens the **shared player card** (`cardName` and
`wireCards` in `shell.js`), the same one the trade page hangs off a name, so
what the card says about a man here is what it says about him there.

**The masthead.** The team, the season, the matchup period, the opponent,
the day and its date, the days left, and where the numbers came from
(`source_note`, from `app/projections/sources.py`'s `describe`).

**The readout.** Expected categories won, days left, adds left of the
period's budget, FAAB, open places and the injured-reserve slot, and how
many free agents were evaluated — with which wire they came from.

**The tale of the tape.** The nine categories in the fixed order, each with
both sides' projected totals, the chance of winning it as things stand, and
a bar. Above it the same nine as a shaded strip. This is the one place the
page computes anything: a side's weekly totals go out as raw counts, so FG%
and FT% are rebuilt in the browser from the made and attempted behind them
(`totalOf` in `pages.js`). A rate cannot be summed across a roster or a week
without its attempts, which is why they travel as counts at all.

**The days left.** The matchup period's days as a strip, the played ones
faint, today in the accent, a day the lineup cannot fill marked. Then each
empty day with the slots that go begging and who on the wire could fill one.

**The plan.** The moves worth a look, in the order to make them, each one
found against the roster the one before it leaves. When no adds are left,
or when nothing cleared the bar, that is what it says — and says the
projected record either way, because a bar nothing cleared is an answer.

**Every move considered.** All of them, best first, each marked *clears the
bar* or *below the bar*, each with its judgement lines, the categories it
moved as a strip, the bid when there is one, and "on waivers, clears
Thursday" when the league has the man on waivers. A move that fills an empty
day clears the bar whatever its size, which is why one can clear it below
another that did not; the page says so rather than leaving it to be noticed.

**The season, as it stands.** The projected end-of-season category record
with no move made, what is banked, and what a place on this roster gives
back if it is vacated.

## What changed, on the league's This week page

The morning question after "who starts today" is "anything I should know?",
and this is the one place that answers it: the league's news, a day at a
time, newest first, under the matchups on `league-week.html`.

Each line is the moment (UTC), what happened in one sentence, and the one
word that says which kind it is — *injury*, *minutes*, *owned*, *add*,
*claim*, *drop*, *trade*, *waivers*, *roster*. The sentence is the API's
(`app/inseason/changes.py`), not the page's: the digest sends the same
words, so the two can never disagree about what happened. A claim carries
what it cost; a trade names both teams and every player in it. A line about
the reader's own team takes the accent rule a matchup of his does, his
opponent's takes a plain one, and the checkbox **My team and my opponent**
shows him only those two. The count beside it says how many of how many.

**The window is the day on the page and the day before it.** `?today=` picks
the day, as it does everywhere else, so a past day of a played season shows
that day's news and not today's. The route's own default, with no window
asked for, is the last twenty-four hours — or everything since the reader's
own last morning digest went out, which is what makes the page and the
message one conversation (docs/site.md, docs/jobs.md).

**The browser works nothing out.** It groups the lines by the day the API
stamped, hides the ones the filter is not asking for, and prints times in
UTC, which is also how the feed is bounded and grouped, so a line never
lands on the wrong day.

**Every name in a sentence opens the shared player card.** The name is found
inside the API's sentence and only that run of characters is replaced by
`cardName` (shell.js), so the words the reader sees are still the API's and
a name in the feed opens the same card a name in a table does. A player the
feed has no ESPN id for — a leg of a trade recovered from the rosters rather
than from the ledger — is left as plain text rather than given a trigger
that would answer nothing.

## Rest of season, at the foot of the week page

Added 2026-09-22 (docs/projected_record.md). Under "The season, as it
stands", which is this team against a league-average opponent, sits the same
question answered against the **real** opponent each week:

* a line of three numbers that add up — banked so far, expected from here,
  projected to end;
* a table, a week a row: the matchup period, who it is against, the
  categories expected, and the nine chances, each shaded up or down. The
  week being played carries the accent and the word "now";
* **Where it finishes**: a bar a place, only the places with at least half a
  percent, then the playoff odds, the bye odds where the format has one, how
  the table is ordered and how many seasons were simulated;
* the line saying the playoff rounds were not projected, and why.

It is drawn last, after the week itself, and is hidden entirely when the
route cannot answer — a season with no schedule stored, or one that is over.
The forecast's own record goes in the footnote. Since the spread was widened
(2026-09-23, docs/spread_revision.md) it says the chances now land about where
they claim to except at the two ends, and that a week five or more ahead is
called right about 55% of the time: the section is still a direction rather
than a prediction.

## What is on the season page

The outlook (the projected record, the weeks left, an ordinary week's odds
as a strip), the move worth a look, then the best free add, the best swap
and the best two-for-two each marked against its own bar — a claim costs
FAAB and has to clear more than a free add. Then the drop candidates, the
stashes, and the churn guard's line.

## The index

Retired in step 3: the league's Standings page (docs/site.md) is every team
with its records now, and the old index address redirects there. Which team
is ours comes from the viewer's verified claim; `MANAGER_TEAM` in
`app/api/pages.py` is left only for the context route's `ours`, and for
single mode's fallback when the owner has no claim.

## The theme

**Light is the default** and the skin these pages were approved in: the
season report's house style. The switch in the site's bar flips to the draft
room's own palette and back, and the choice is kept in `localStorage`
(wrapped in `try`/`catch`, so a private window merely forgets it).

Both palettes are the draft screen's token block
(`app/draft/static/draft.html`) with the halves swapped round — the same
variable names, so the two products share one vocabulary and a value can
move between them without translation. No colour is written outside that
block in `pages.css`. The rules of the house: rules, not boxes; no rounded
corners, no chips, no shadows; one burnt-orange accent; tabular numerals
wherever there is a number.

**The nine-category strip** is the draft screen's component, markup and
class names unchanged, so the two do not drift. What shades a cell differs,
because there is no draft pool here to rank against — a probability shades
by how settled it is, a shift by how far it moved — but the component takes
the shade it is handed and does not know the difference.

**The morning email is one of these pages** (2026-09-22, `app/mail/`, and
docs/jobs.md's "The shape of the message"). It carries the same masthead,
the same rules and no boxes, the same burnt-orange accent, the same nine in
the same order, and the same language: **worth a look**, **nothing clears
the bar**, never *recommended*.

**It is not the pages pasted into an inbox** (2026-09-23). A page is read
for as long as it is worth reading; an email has to answer "is there
anything to do today?" in ten seconds. So the email has two forms
(docs/jobs.md, "The two forms"), and the one everybody gets is **compact**:
four sections on about one screen — **Tonight** (the lineup grid and the one
thing to fix), **Worth a look** (the moves that clear the bar, one line
each, then a count and a link for what is under it), **Since yesterday**
(counts that link into this page's What changed, with an injury on his
roster said in full) and **Standing** (one line). The **full** form is the
long message — Today's lineup, This week, The season, What changed and
Standings — and is a setting on the Alerts page.

Two rules the pages and the email now share. **A move is named once**: the
season's best is very often the week's best again, and saying it twice with
two numbers reads as the product contradicting itself. And **what is under
the bar is labelled, never hidden** — on a page that is a row with its
reason; in an email it is a count with somewhere to go and read it.

It cannot use `pages.css`. An email client fetches no stylesheet, knows no
custom property, runs no script, loads no web font, and in Outlook's case
lays the page out with Word. So the theme is said again inline: the light
palette as literal hex, the site's own font fallbacks in place of the three
Google faces (`Arial Narrow` for Oswald, Georgia for Source Serif 4), tables
for layout, and no image of any kind. There is no dark half: the switch is a
thing a reader presses on a page, and an email has no button to press. A
client that inverts the page in dark mode is why a gain and a loss carry a
sign and an arrow as well as a colour, exactly as the trade page has them.

## What the pages fetch

The reports come from the routes that already existed, unchanged, plus the
day's own and the rest of the season:

    /leagues/{league_id}/seasons/{season}/teams/{team_id}/today
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/projected

`/today` is the same scope as the other two (the team's verified manager,
entitled) and carries its own `source_note`, so the Today section can say
where its numbers came from without waiting on `pages/context`.

`/projected` is the same scope again, and is the league's projection
narrowed to this team (docs/projected_record.md): its remaining weeks and
its finish distribution. The league page's own `/projected` -- the same
payload with every team in it -- is a member's, and is what the Standings
page's Projected view and the This week page's chances are drawn from. Both
answer from the morning's stored row when there is one and build live
otherwise, exactly as the two pickup routes do.

Everything else comes from one small route added with the pages:

### `GET /leagues/{league_id}/seasons/{season}/pages/context`

The day being reported on as a date, the days of its matchup period for the
schedule strip, every team's name, which team is ours, and the source line.
One route rather than four calls to existing ones. It never refuses a season
it holds: a season with no stored schedule still has teams and names, and
the page says what is missing rather than failing to draw.

Records come from `/standings`, which already derives a matchup record from
the stored matchups, so they are not repeated in the context.

## No auto-reload

**A "refreshed at" line, not an interval.** A report over a real season
takes the better part of half a minute to build — it searches the whole wire
and re-runs the week for every candidate move — so a page that refetched
itself every minute would spend its life loading. The footnote says when the
answer was built; the reader refreshes.

## The language

The tool generates ideas and the manager decides. The pages say **worth a
look** and **nothing clears the bar**, and never *recommended* and never
*do this*; the word survives only as the name of a field in the report's own
JSON. The two phrases are written once, in `pages.js`, and
`tests/test_api_pages.py` checks the markup a reader sees for the words that
are not allowed in it.

## The gate

Nothing on these pages is gated today, and they carry no per-player
projection number at all. Every figure beside a player is a count of games,
a start, or a probability derived from this season's own box scores and
ESPN's lines, and ESPN's are ours to show
(`app/projections/sources.py`, `docs/projection_sources.md`). If a page ever
grows a per-player number from a gated source — Basketball Monster's, the
only one — it has to ask `may_show` first, exactly as the draft screen's
pool does, and degrade in place rather than withhold the page. The seam is
named in the module docstring so the next person finds it.

## Two things a played season does that the current one does not

Both were found by opening these pages on 2026 and are fixed in
`app/pickups/state.py`; see `docs/pickups.md`'s as-built notes.

**The wire is empty.** `free_agent_snapshots` is the listener's, and the
listener only runs for the season in progress, so a played season has none
and the wire would be nothing at all. `load_free_agents` now falls back to
the definition `scripts/pickups_backtest.py` reconstructs a past day's wire
with — a man who played that scoring period whom no team had in its lineup
that day — and the report carries `historical_wire`, which the page renders
as **historical wire (no snapshots)** in the readout and explains in the
footnote. It is a weaker definition: it cannot see a free agent who did not
play, and it knows nothing about waivers. A caller who names its own pool
never reaches the fallback, which is what leaves the backtest's numbers
alone. On 2026 day 100 it turns "0 free agents evaluated" into 55.

**The readiness guard refused it.** Both report routes used to demand a
status snapshot for the season, which every played season fails. They now
ask for a schedule and a roster from either place one can come from — the
listener's snapshots, or the stored lineup days — so a season with its
schedule backfilled reports.

## Running one

    .venv/bin/python -m uvicorn app.main:create_app --factory --port 8000

then `http://localhost:8000/l/3853870/2026/standings` — or the **fcp-api**
entry in `.claude/launch.json`. Read-only, like the rest of the API: the
pages write nothing, to the league or to the database.
