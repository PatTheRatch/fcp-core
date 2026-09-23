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

Rewritten 2026-09-23 as **the game sheet**. The page had grown to seven
screens and showed the database, the model, the reasoning and the answer all
at once. It now shows **answer → evidence → model**, in that order, with the
evidence one tap away. *Nothing was removed.* Everything that was on it is
still on it, in the same words; what changed is how much of it is open when
the page loads. The one job: *I open Box Out. Within ten seconds I know what
matters tonight and whether I should do anything.*

It is designed at 390 and widens; 1280 is the roomy version of the phone
layout and not the other way round.

**1. The matchup.** The team, the season and the matchup period; who this
week is against; the day and its date; **days left** as the largest figure
on the page, because it is what every other answer is measured against; and
the two projected category totals (`5.24 – 3.76`) over one proportion bar.
Nothing else: the source line and the standing figures are under More.

**2. The nine.** The categories as a pulse rather than a table: three bands,
each headed in words as well as told apart by ink — **likely yours** (at or
over 65%), **swing** (35–65%) and **likely theirs** (at or under 35%). The
swing band carries the accent rule, because that is the band tonight can
move. The two thresholds are constants in the page's own script
(`BAND_YOURS`, `BAND_THEIRS`) with a comment saying they are a display
choice about ink and not a claim the model makes; nothing in the report
knows they exist. Tapping a category opens both sides' projected totals and
the chance, which is what the old tale of the tape printed nine rows of at
once — and the other side is said to be *our estimate of their side*, there
where the estimate is.

**3. Tonight.** Who plays, on both sides. A man a row: his name, his mark
(`HOU · PG`), his game and its tip-off (`at UTA · 9:00 PM`), and what ESPN
says about him (**active**, **questionable**, **out**). No headshots — the
NBA team's abbreviation set in the display face is the mark, which is
licensed to nobody and reads at any size. Theirs is the places they have set
for the day, from the stored daily lineups (`/teams/{id}/lineups`, the
league's own route): a fact rather than a projection, and a name opens the
same card every other name does, which is where the rest of what is known
about him is. Men with no game are under **Not playing**.

Above them, and **only when the day's own report says there is a decision in
the lineup** — a place that will produce nothing tonight, or a man on the
bench with a game — the warn line that has always been there. When there is
no decision there is no block, and the absence is the answer.

**4. The read.** At most three moves from the plan, each one: `ADD` a man,
`DROP` a man, the number (`+0.94 categories`), the categories it helps and
the ones it costs, the games it buys, and the bid with its range. **See why**
opens the judgement lines and the shift strip that were always printed under
it. It is headed *The read* and never "Box Out says" or "the call": a tool,
not gospel. What did not clear the bar is not hidden — it is under More,
every move of it, each marked *clears the bar* or *below the bar*.

**4b. What if.** Directly under The read, because it is the manager's own
read. He names a man off the wire and the man going out for him, and the
answer is the same three layers as the plan above
([`what_if.md`](what_if.md) §7): the nine this week with the categories the
move touched marked in the bands, the judgement — the recommender's own
numbers, through the same `judged()` every other move on this page goes
through, against the same bar — and where the season finishes, in the same
block the trade page draws.

**Nothing is fetched until it is asked for.** The wire is read the first
time the chooser is opened; the move is judged only on **Run**. So what the
section costs a first load is a heading and a form: 363 px of 2,992 at 1280
and 441 px of 4,107 at 390. The first Run in a fresh server process takes
about half a minute, because the league's whole FAAB history is built once
to price a bid (`what_if.md` §4), and the button says so while it waits.

**Drop** is a chooser over the roster the day's own report already carries,
so it costs no fetch; a man on injured reserve is marked and cannot be
picked, because he keeps his roster place and is not a drop. **To IR** is
offered only where the league has a place free and the roster holds a man
ESPN has ruled out, and it clears the drop, because naming both leaves the
roster a place short. **Add** is the trade page's own chooser over
`trades/pool`, sorted by **value** — what a man gives an ordinary place —
because a straight pickup has no deal to price *worth* against.

The finish is **a second lens and not a second bar**. The route sends those
words as `finish.language` and the section's lede prints them; nothing here
is ranked, hidden or labelled against it, and a finish the simulation cannot
resolve says so in the route's own sentence rather than leaving two
percentages a tenth of a point apart to speak for themselves. A change that
cannot be made is the route's 422 sentence in the warn line, which is the
one other thing on this page stated as a mistake.

**5. Schedule.** The period's days across, **you** and **them** down, as a
real table, and a **Total** column at the right for the days still to play.

The figure in a cell is **the games that will count**: men on that side's
roster with a game that day who are not ruled out of it, seated by the same
lineup solve the week above is projected from (`StreamReportOut.schedule`,
built in `app/pickups/stream.py` off the projection itself, so the grid and
the expected wins cannot disagree). A man ESPN has **out** counts on no day
before the date he is due back and on none at all when there is no date;
injured reserve is not in it. Where a side has more games than the lineup
has places, the games follow in the small face — **7/9** is seven starts out
of nine games, and the other two are men on the bench that day. The small
second figure rather than `7 (9)`: at 390 px an eight-column row gives a
cell about 34 px, where the bracketed form wraps, and what the reader needs
at a glance is the big number, with the shortfall legible on a second look.

This replaced a count of the *places each side had a man in*, read from the
stored lineups, which the owner rightly called wrong on 2026-09-23: ten men
in the lineup is not ten games, and a place set with a man who is out is not
a game at all. The two `/lineups` requests the grid made are gone; the one
for the opponent stays, because their half of Tonight is a fact about what
they have set and not something this page works out.

`n open` is starting places no man on that roster can fill — in the accent
on a day a free agent could fill one (the week report's own empty-day check,
the one thing the bar is waived for), in the muted face otherwise, where it
is true and not an alarm. Tapping a day names the men behind both figures on
both sides, each marked **bench** where the lineup had no place left for
him, so the list and the count are the same arithmetic.

**6. What changed.** Only when the window — the day on the page and the day
before it — holds something worth the eye: a man out, a doubt, a piece of
news, or a move that touched this matchup. Everything else in the window is
a count and a disclosure. The sentences are the API's, exactly as they are
on the league's page and in the morning message.

**7. Season.** One line: the projected finish in categories, the place the
simulations put this team in most often, and the playoff odds; and under it,
in the small face, `SHORT_NOTE` from `app.inseason.projected_calibration`,
which says in one sentence what the forecast scored. Week by week is what
the line opens.

**8. More.** Every move considered; where the season finishes; with a move
and without; the week's standing figures (adds left, FAAB, open places, the
wire); how this is worked out (the source line, the bar and where it came
from, and `calibration_note` verbatim); and the links to the other pages.

On first load it is a little over half the page it was at 1280 and a little
over two-fifths of it at 390, with nothing gone.

### The day's own report, inside Tonight

**Today** (added 2026-09-22, `app/pickups/today.py`). The morning question,
and still the first thing fetched, because it is the first thing the manager
does. Its full comparison now lives under a disclosure in Tonight: two
lineups side by side (one above the other on a phone):

* **As we would set it** — a place a line, in the league's own slot order,
  with the man's name, the game his NBA team plays ("at MIL"), and a flag
  when ESPN carries a status. A place nobody on the roster can fill says so
  in the muted style rather than being left blank.
* **As it is set** — the same grid from `daily_lineup_slots` for the day.
  On a day the ingest has not reached there is no such row, and the page
  says the lineup has not been read from ESPN yet rather than comparing
  against an empty one.

Above the grids, in the warn style, **the one thing this page
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

**Where the numbers come from** (`source_note`, from
`app/projections/sources.py`'s `describe`) is under More, with the bar and
where it came from, and with `calibration_note` verbatim. Every number on
the page keeps its provenance; it is one tap away rather than always
visible.

**The readout** — expected categories won, days left, adds left of the
period's budget, FAAB, open places and the injured-reserve slot, and how
many free agents were evaluated, with which wire they came from — is under
More as *the week's standing figures*.

**Both sides' totals** are what a category in the pulse opens. This is the
one place the page computes anything: a side's weekly totals go out as raw
counts, so FG% and FT% are rebuilt in the browser from the made and
attempted behind them (`totalOf` in `pages.js`). A rate cannot be summed
across a roster or a week without its attempts, which is why they travel as
counts at all.

**Every move considered** is under More: all of them, best first, each
marked *clears the bar* or *below the bar*, each with its judgement lines,
the categories it moved as a strip, the bid when there is one, and "on
waivers, clears Thursday" when the league has the man on waivers. A move
that fills an empty day clears the bar whatever its size, which is why one
can clear it below another that did not; the page says so rather than
leaving it to be noticed. When no adds are left, or when nothing cleared the
bar, The read says so and says the projected record either way, because a
bar nothing cleared is an answer.

**The season as it stands** — the projected end-of-season category record
with no move made, what is banked, and what a place on this roster gives
back if it is vacated — is under More as *with a move and without*.

### Two fields the game sheet needed

Added 2026-09-23, both additive and neither a new number:

* `pro_team` on every player a report carries (`PickupPlayerOut`): his NBA
  team's abbreviation, from ESPN's own table — the one `app/pickups/today.py`
  already reads to write "at MIL" — so a page can set a mark beside a name
  without carrying the table.
* `at` on a day's game (`TodayGameOut`): tip-off, as the stored schedule
  holds it. A moment and never a clock, because the same report is read in
  three time zones.
* `calibration_short` on the projection (`ProjectedOut`): `SHORT_NOTE`, the
  forecast's record in one sentence, for the Season line. The long
  `calibration_note` is unchanged and still printed verbatim under More.

All three are null or empty rather than absent on a report stored before
they existed, so a morning's stored row still draws.

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

**The game sheet** (2026-09-23) is the same house pushed from a newspaper
column towards a front-office sheet: more basketball objects, more visual
priority, far less explanatory prose. No gradients, no glass, no giant
metric cards, no emoji as section markers, no "AI dashboard" idiom. Its
objects are in `pages.css` under *the game sheet*, on the existing tokens.
Every disclosure on it is a real `<details>`/`<summary>`, or a button with
`aria-expanded` and `aria-controls` where the panel is shared (a category of
the pulse, a day of the schedule); the schedule is a real `<table>` with
scoped headers; the bands are told apart by their headings before they are
told apart by ink; and a reader who has asked for reduced motion gets it.

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

The game sheet adds two more of the league's own, and no new route:

    /leagues/{league_id}/seasons/{season}/teams/{team_id}/lineups?period=&started=true
    /leagues/{league_id}/seasons/{season}/changes?since=&until=&team_id=

What if adds two the week page did not call before, neither of them new and
neither of them fetched on load:

    /leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/pool?with_team=&side=ours
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/what-if?add=&drop=&to_ir=

The pool is the trade page's, and it wants a deal to price *worth* against;
a straight pickup has none, so the other team on the query is only there to
satisfy the route and the column the chooser sorts and shows is `value`.
Both carry `?today=` exactly as every other fetch on the page does.

`/lineups` is league scope (docs/accounts.md: every team's stored lineups
are a member's to read), which is what lets the sheet show the other side of
Tonight without a scope of its own, and it is asked only for the opponent:
the schedule grid used to be drawn from both sides' rows and is now drawn
from the week report's own games table, so ours is no longer fetched. It is
what the ingest wrote down and not a plan: what the page *estimates* about
the opponent is the totals in the nine, and it says so there. A day the
ingest holds no lineup for is an en dash. `/changes` is the same route and
the same window the league's This week page uses.

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
