# The in-season pages

**Written:** 2026-09-18, when the two pickup reports were given a screen.

Three pages, served by the read-only API (`app/api/pages.py`) from files in
`app/api/static/`. Plain HTML, CSS and JavaScript: no framework and no build
step, for the reason the draft screen has none — a page that has to come up
on a tailnet from a file on disk should not need a toolchain. Each file is
read per request, so an edit shows on a refresh.

They are the two CLI reports as a screen. `scripts/stream.py` and
`scripts/season.py` are the content spec: everything those two print, the
pages show, in the same words and the same order. Nothing is computed in the
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

**The player card is a hook, not a card.** Each player's name is wrapped in
`<span class="player" data-espn-player-id="…">`. The shared card being added
to `pages.css` and `shell.js` had not landed when this was written, so
nothing attaches to it yet; when it does, the names on this page are already
marked up for it and only the styling is missing.

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

## What the pages fetch

The two reports come from the routes that already existed, unchanged:

    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream
    /leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season

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
