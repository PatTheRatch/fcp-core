# The site: one map, one shell, every page under it

**Written:** 2026-09-19. **Status:** built, step 3 of docs/product.md (the
shell, the league switcher, the account pages, the existing pages moved
under it, the landing page). The VPS serves it in single mode like the rest
of the API; accounts mode, and so the landing page for a stranger, is the
cutover's (docs/accounts.md, docs/cutover.md).

**The name.** The site is **Box Out**, at **boxoutfantasy.com**, since
2026-09-22; it was Full Court Press, which is the name of Patrick's own
league and still is. No page writes the name down: `app/brand.py` holds it
and the files carry the tokens `{{brand}}`, `{{brand_domain}}` and
`{{brand_tagline}}`, which every route that serves a page fills in on the way
out (`app/api/site.py`, `app/api/pages.py` for the shell script,
`app/api/auth.py`, `app/api/leagues_admin.py`, the draft service, and
`scripts/draft_plan.py` for the plan it writes). A test reads every page and
mail the server can produce and fails on the old name.

Code: `app/api/site.py` (the routes, and the one JSON route the pages
needed), `app/api/static/shell.js` (the rail, the drawer, the scenario bar),
`app/api/static/scenario.js` (the scenario state), `app/api/static/*.html`
(one file per page), `app/api/static/pages.css` and `pages.js` (shared;
docs/design_system.md is the language they are written in).
Tests: `tests/test_shell.py`. Plain HTML, CSS and JavaScript, no framework
and no build step, each file read per request so an edit shows on a refresh
(docs/in_season_pages.md says why).

## The map

Every address is bookmarkable: the league, the season and the team are in
the path, and `?today=N` (a scoring period) is carried wherever it means
something.

| Address | Page | Who may open it | File |
|---|---|---|---|
| `/` | signed out: the landing page; signed in: goes to his default league's This week | open | `landing.html`, `home.html` |
| `/sign-in` | the sign-in form | open | `sign-in.html` |
| `/l/{league_id}/{season}/week` | This week | a member of the league | `league-week.html` |
| `/l/{league_id}/{season}/standings` | Standings | a member of the league | `league-standings.html` |
| `/l/{league_id}/{season}/draft` | Draft | a member of the league | `league-draft.html` |
| `/l/{league_id}/{season}/history` | History | a member of the league | `league-history.html` |
| `/l/{league_id}/{season}/team/{team_id}/week` | My team, Week: the streaming report | the team's verified manager, entitled | `week.html` |
| `/l/{league_id}/{season}/team/{team_id}/season` | My team, Season: the rest-of-season report | the team's verified manager, entitled | `season.html` |
| `/l/{league_id}/{season}/team/{team_id}/moves` | My team, Moves: the scorecard of his own moves | the team's verified manager, entitled | `moves.html` |
| `/l/{league_id}/{season}/team/{team_id}/trades` | My team, Trades: build a deal and see what it does | the team's verified manager, entitled | `trades.html` |
| `/account/connections` | Connections: connect a league, invites, claims, your league's numbers, your machine tokens, your SWID | anyone signed in | `connections.html` |
| `/account/projections` | Projections: your uploaded sets, how to upload | anyone signed in | `projections.html` |
| `/account/alerts` | Alerts: your own address (add, confirm, disable), what goes in your email per league, and the server's recipients for its owner | anyone signed in | `alerts.html` |
| `/pages/claim/{league_id}/{season}` | Claim your team (step 2's, now under the shell) | a member of the league | `claim.html` |
| `/join/{token}` | where an invite link lands (step 2's, under the shell) | anyone signed in | `join.html` |
| `/design` | the design language, drawn on the viewer's league's stored 2026 season, day 52 (docs/design_system.md) | open; the file carries no data and its specimens read league routes behind their own checks | `design.html` |

The league pages are the free tier and the team pages the paid one
(docs/product.md, "Free and paid"); `require_entitlement` answers yes for
everyone until billing, so today every manager opens his own team's pages.
Signed out, every page but `/` and `/sign-in` sends the browser to
`/sign-in?next=<the page>` and back. A refusal is one line of HTML: "This
league's pages are its members'." or "This team's plan is its manager's."
docs/accounts.md has every route's check in its table.

**The old addresses redirect** (308, the query kept) and keep the check they
always had, so a bookmark still works and a stranger is still refused at the
old address:

| Old | New |
|---|---|
| `/pages/teams/{league_id}/{season}` | `/l/{league_id}/{season}/standings` (the old every-team index) |
| `/pages/teams/{league_id}/{season}/{team_id}/week` | `/l/{league_id}/{season}/team/{team_id}/week` |
| `/pages/teams/{league_id}/{season}/{team_id}/season` | `/l/{league_id}/{season}/team/{team_id}/season` |
| `/pages/connections` | `/account/connections` |

## The shell

**Since 2026-09-25 the shell is the workstation's**: a rail down the left, a
scenario bar over the page when a scenario is set, and an inspection drawer
along the right. docs/design_system.md has the whole of it — the tokens, the
voices, the colour rule, the components and the migration order — and this
section is what the shell does as navigation.

Drawn by `shell.js` into `<div id="shell">` at the top of every signed-in
page, from two routes: `/auth/me` (who, his role in each league, his claims)
and `/leagues` (his leagues, each with its name and the seasons held; in
single mode, every league stored).

```
BOX OUT
● OVERVIEW                       → the team's week page, for now
TEAM  Through The Wire
  Matchup · Roster (soon) · Moves · Trades · Season
LEAGUE
  This week · Standings · Players (soon) · Draft · History
───
◈ SCENARIO   Current scenario · n changes
───
Patriot Games · 2027 ▾           Your leagues / Seasons 2027 2026 ...
Account ▾                        you@example.com, Projections, The design language, Sign out
Connections · Alerts · Theme ◐
```

- **The items.** Matchup is the team's Week page (current there), Moves,
  Trades and Season the team's other pages, This week, Standings, Draft and
  History the league's. Overview goes to the team's Week page until the
  Overview page exists. **Roster** and **Players** have no page yet and say
  "soon": Roster opens the Week page at Tonight, Players opens it at What if,
  whose wire is the nearest thing to a player screener. Every link carries
  `?today=` and `?me=` where they mean something.
- **The league** is the one in the URL. On a page without one (the account
  pages, `/`) it is the one this browser last looked at, kept in
  `localStorage` under `fcp-league` (wrapped in try/catch: a private window
  just forgets it), else the first league where he manages a team, else his
  first. The season is the URL's, else the newest held.
- **The switcher** lists his leagues (each to the same section of its newest
  season) and the seasons of this one (each to the same page that season).
  A league connected but not yet ingested is listed as waiting.
- **The page you are on** is the one item with the orange edge
  (`aria-current="page"`).
- **TEAM** is there only where he is a verified manager in this league:
  this season's team, else his newest one there, named beside the word.
  Without one it is **Claim your team** (or **Claim pending**), to the claim
  page. In single mode, where the one user reads every team, a league with
  no claim falls back to the team the context route calls ours
  (`MANAGER_TEAM`, or `?me=`), which is what the pages always did.
- **The scenario line** says "Baseline · no changes" until a scenario is
  set, then its name and how many changes, in orange while the scenario is
  the view on screen. Today only the Week page's What if sets one; the bar
  over the page is described in docs/design_system.md.
- **Account** is a menu with his email, Projections, the design language
  (`/design`) and Sign out (a form that posts to `/auth/sign-out`); in
  single mode Sign out is replaced by the line "Single mode: nobody signs
  in". Connections and Alerts are links of their own under it.
- **Theme** follows the system's light or dark until it is pressed; the
  choice is then kept per browser (`fcp-theme`, as before) and applied in
  each page's head before first paint. It is no longer "light unless you
  switch": a dark system gets the dark workstation first.
- **The menus** are buttons that open a list of links (the disclosure
  pattern), in place in the rail. Click, Enter or Space opens one; ArrowDown
  or ArrowUp opens it on the first or last item; the arrows, Home and End
  move through it; Escape closes it and puts the focus back on its button;
  it closes when the focus or a click goes elsewhere.
- **Below 960 px** the rail becomes a top bar — the name, where you are, and
  **Menu**, which slides the same rail in over the page with a scrim behind
  it. Escape, the scrim or Close puts it away. Nothing scrolls the page
  sideways at 390; a wide table scrolls in its own frame.
- **The drawer**: every player's name opens his card in the inspection
  drawer along the right (a sheet from the bottom on a phone), not in a
  floating card. See "Every name on it opens a card" below.

`SHELL` is a promise of what it found (`{me, league, season, myTeam, ...}`),
which the league pages await to know which team is the reader's.

The three signed-out pages (the landing page, sign-in, and OAuth consent)
keep a plain bar with the name, the way in and the theme switch; the rail is
for someone inside. They are in the new palette and voices like the rest.

## The pages, in words

Every page inside the rail is built of the same furniture since the
furniture pass of 2026-09-25 (docs/design_system.md, "Migration"): a header
line saying what the page is and when, with its own controls at the right;
a facts line of the page's standing figures; sections, each its label over
one panel; tables at the screener's metrics; each section's account of how
its numbers are worked out one click away in the drawer ("How this is
worked out" in its label row); and the calibration and the refreshed line
in view at the foot. The descriptions below say what each page holds.

**The landing page** (`/`, signed out) is the one page a stranger sees, and
it was rewritten on 2026-09-22 with the rename. The bar with the name, "Sign
in" and the light/dark switch. The eyebrow "Head-to-head, nine categories, on
ESPN", the name as the headline, the tagline and a paragraph under it, the
burnt-orange "Sign in with your email", and one line for someone whose league
is not connected: ask whoever runs it, or connect it yourself.

Then **What it is for**: three claims side by side, each one a measured
figure, the sentence it supports and the note it came from — the pickup
recommender's **+0.16** categories a week at the 0.20 bar over 616
team-decision points (docs/pickups_backtest.md); the streamed place's **0.38
against the held thirteenth man's 0.00** over 1,536 team-periods
(docs/streaming_lane.md); the trade evaluator's **25 of 55**, a coin, which
is why the trade page leads with the fit (docs/trades.md). Nothing on this
page is a number the docs do not already publish, and each one says where it
is from, because a stranger has no other reason to believe it.

Then the stance, in an accent rule: **a tool, not gospel** — the numbers come
with their reasons, and the manager decides. Then two columns: what it needs
from him (an ESPN nine-category head-to-head league, an email address, a
member who connects the league) and what it will never do (touch ESPN on his
behalf: no add, no drop, no claim, no trade, no lineup).

No data, no script but the theme, both palettes, and no screenshots: there
are none worth showing yet, and a page of empty frames saying "screenshot"
is the sort of thing this product does not do. The file carries a marked
slot where they go.

**This week.** The header line: "This week · 2027" ("The playoffs" in a
playoff period), then "Matchup period 15, days 98–104 (Mon, Jan 26 – Sun,
Feb 1). Day 100 (Wed, Jan 28): 5 days left, today included."; at its right
"‹ Period 14 · Period 16 ›" as quiet buttons and the seasons as a segmented
control. A facts line of four: the period (of 22), its days, days left, the
number of matchups (and byes). Then, for the reader's own team only, "Your
week" with the team's name as its tag and an orange edge down its panel:
expected to take 5.26 of 9 categories against Masters of their Domains,
projected to end the season 106.8–64.2 in categories with no move made, the
nine as a shaded strip, and "See the moves worth a look". Then "The
matchups", one panel, each matchup a row of it: the two names with the
categories-won count between them, a line ("Final: Masters of their Domains
won 5–4", or "… leads 5–3", or "Level at 4–4"), and each side's nine as the
strip, a cell shaded where that side is winning the category. The reader's
matchup is first with the orange edge. A bye says so.

Under each matchup, once the projection can be built, **the chances**: the
nine as a shaded strip from the first side's point of view, and the
categories each side is expected to end the period with ("Fantastic 5 3.28
of 9 · Through The Wire 5.72"). A period being played says how many days are
left and that the figures are on what is already posted plus what the
rosters add; a period still to come says it is on the rosters as they stand
today. From `/projected` (docs/projected_record.md), the same answer the
Standings page's Projected view draws, so the two cannot disagree.

Then **What changed**: the league's news over that day and the day before
it, a day at a time and newest first — injuries and status changes, adds,
drops, claims with what they cost, and trades — each one sentence with the
moment beside it and the kind of thing it was after it, and a checkbox for
a manager, "My team and my opponent". It reads
`GET /leagues/{league_id}/seasons/{season}/changes?since=&until=&team_id=`
(league member; `team_id` only sets the `mine` and `opponent` flags and adds
no check, docs/accounts.md), whose default window with none asked for is the
last twenty-four hours, or everything since the reader's own last morning
digest. The sentences are the API's, and are the ones the digest sends.
docs/in_season_pages.md has the whole of it.

It reads `pages/context` (the day, the teams), `/periods` and
`/matchups?period=N`, and for the reader's own block `/pickups/glance`
(step 4: the team's manager, not the paid tier, from the morning's stored
week report; docs/jobs.md). `?today=` picks the day and so the period; `?period=`
picks a period outright (the arrows). The scores are ESPN's as the ingest
last stored them, so a past day shows its period's **final** score, and the
line says so ("The scores stored are the period's final ones, played out
since."): no route keeps a matchup's score day by day. A season whose
matchups have no score yet says "The period has not begun".

**Standings.** The header line "Standings · 2027 · 14 teams. The regular
season's matchups, byes left out.", the seasons at its right. One table (scrolling in its own
frame on a phone): rank, team, matchups W–L–T, categories W–L–T, the share
of categories won, the longest winning and losing runs, the run the season
ended on (W2, L1), the final finish (#1). The reader's team is shaded, and
its name opens his week; in single mode every team's does. Reads
`/standings` and `/streaks`.

Two views of that one table, chosen by a segmented control over it (added
2026-09-22, docs/projected_record.md). **As it stands** is the above.
**Projected** is where the season is heading: the record so far *as it stood
on the day the projection was made*, then the projected matchup record, the
projected category record and the odds of making the playoffs, with a bye
column where the format has one. Tapping a playoff figure opens the odds of
each individual place under that team's row, as a row of little bars, rather
than putting fourteen more columns on a phone. The projected view is in the
projection's own order, not the table's. Every column sorts on a click in
both views. It reads `/projected`, which is the whole league in one answer
and is the slow fetch, so the table is drawn first and the toggle appears
when the projection lands; a season it cannot be built for simply has no
toggle. The footnote prints the forecast's own record, which since the spread
was widened (2026-09-23, docs/spread_revision.md) says plainly that it now
lands about where it says it will except at the two ends of the scale.

**Draft.** The header line "The draft · 2026 · 182 picks, $2796 spent. The
dearest: Victor Wembanyama at $100, to Fantastic 5." A facts line: picks,
spent, dearest, keepers, and the reader's own ("$200 on 13 players"). "Value
for money": the ten best and ten worst picks by points per dollar (player,
team, paid, points, games, per $), with the paragraph saying plainly it is a
blunt measure in NBA points, not categories, in its account. "The board": every pick in order with
who bought him, the price and who nominated him; the reader's picks shaded.
A season not yet drafted says so. Reads `/draft` and `/draft-value` twice
(best, worst).

**History.** The header line "History · 2026 · The league as the stored
seasons tell it: 46 owners over 8 seasons. …". "Where each
team was strong": each team's name beside its nine as the strip, its share
of matchups won in each category, strongest first, the reader's in the
accent. "The results people remember": sweeps and nail-biters of the season.
"Every owner, every season": each owner by his latest team and ESPN name,
seasons, matchups, share, titles, best finish. "Head to head": a select of
owners, set to the reader's own, and his record against everyone he has
met, with the seasons. Reads `/category-profiles`, `/notable-matchups`,
`/leagues/{id}/owners` and `/leagues/{id}/head-to-head`.

**My team: Week.** The streaming report as **a game sheet**, rewritten
2026-09-23 (docs/in_season_pages.md has it top to bottom). The page shows
**answer → evidence → model**, in that order, with the evidence one tap
away, and it was designed at phone width. Nothing was removed from it; what
changed is how much is open when it loads.

Eight things, in order: **the matchup** (the header line "Matchup · period
8 · Thu, Dec 11 · day 52 of 52–55", and a facts line of who against, the
days left and the two projected totals over one bar — figures, not display
type, and grey); **the nine** as three bands — likely yours, swing, likely
theirs — with the swing band marked by the orange edge, every chance in ink,
and each category opening both sides' totals; **Tonight**, a man a
row on both sides with his mark, his game, its tip-off and what ESPN says
about him, and the lineup warn line only when the day's report says there is
a decision; **The read**, at most three moves, each the workstation's move
block (the man and the net, DROP, GAIN, COST, the facts, the bar's label)
whose *Inspect* opens its working in the drawer; **Schedule**, the period's days across and
both sides down as a real table; **What changed**, only when the window
holds something worth the eye; **Season**, the projected finish on one line
with the forecast's own record under it; and **More**, which holds every
move considered, where the season finishes, with a move and without, the
week's standing figures and how this is worked out. (It held the other
pages too, as "Elsewhere", until 2026-09-25: the rail carries them now, and
the product's name left the eyebrow for the same reason.)

A What if that has been run is the page's **scenario**: the bar over the
page names the change and its three numbers from the answer (this week, an
ordinary week from here on, a playoff week), its BASELINE | SCENARIO toggle
switches the What if section between the roster as it stands and the
answer, and Reset clears both (docs/design_system.md, "The scenario bar").

It reads `teams/{team_id}/today` (drawn first: the week searches the whole
wire and takes the better part of half a minute, and the lineup is what the
reader came for), `pickups/stream`, `teams/{team_id}/projected` — the league
projection narrowed to this team rather than a second computation, so the
Week page and the Standings page always agree — and two of the league's own
routes, `teams/{team_id}/lineups` for the other side of Tonight and the
schedule, and `/changes` for What changed. A day no NBA team plays says so
in one line and draws no grid.

**My team: Season.** The rest-of-season report in the workstation's
furniture: the header line "The rest of the season · from day 52 (Thu, Dec
11) through day 150", with the old masthead's sentence (the weeks left and
where the numbers come from) as the page's account; the facts line (an
ordinary week, weeks left, adds left, FAAB, open places, the wire); then
the outlook, the move worth a look, the best of each kind, drop candidates,
stashes and the churn guard, each a label over one panel with its lede in
its account. Its old "Rest of season" and "Every team" buttons are gone,
because My team and the sections are those links now.

**My team: Moves.** The header line "Moves · 2026", with "The season's
moves, graded in categories a week. The league's median pickup was worth
0.072 categories a week." as its account. A facts line: wire moves, trades,
draft picks, the median pickup. "The
categories": the season's share of matchups won in each, as the strip, with
the punts the scorecard sees. "The wire": every add and drop by day, what
it was expected to be worth and what it delivered, per week, and the
scorecard's reading ("Lucky break on a poor call"). "Trades": each with its
counterparty, in and out, and the scorecard's sentence for the regular
season and the playoffs. "The draft": each pick's price, the market's price,
the outcome, both lenses and the reading. One fetch: the team's
`/scorecard`, which is league scope (every team's scorecard is the
league's); the page is the team's slice of it, in the paid layer beside the
plans.

**My team: Trades.** The forward trade evaluator as a screen
(docs/trades.md). The header line says which day the deal is judged on
("Trades · 2026 · judged on day 52 (Thu, Dec 11)"), and its account that the
rosters are that morning's. Then **the builder**: a team to trade with,
the two rosters as lists a tap moves a man in and out of, a "who is dropped"
choice for whichever side has no room (left alone, the cheapest place, chosen
for you), and one button, **Judge this trade**. The deal is written into the
address bar, so a judged trade is bookmarkable and comes back on a refresh.

When a side gives more men than it gets, the deal leaves a roster place open,
and **Who fills it** appears under the builder for that side: the day's wire,
ranked by what each man would be worth to the roster *this deal leaves*
rather than to an average team. "Leave it open" is the first choice and the
default, and it says what the report settles the place at. Since 2026-09-25
the men are drawn by the workstation's **screener table** (the first page to
use it, docs/design_system.md): a row a man with his status (healthy,
injured with a return when it is known, on waivers), his NBA team and
position, his games left, **FIT** (what he is worth to this roster, the
figure the list was ranked by) and **League** (a week to an average team),
and his week in the nine, one column each; sortable, with columns that can be
put away. **Use** names him for the place, which re-judges the deal with his
line in it and goes into the address bar like everything else (`fill`,
`theirfill`); a click anywhere else on the row opens his card in the drawer.
It reads `trades/pool` (docs/trades.md §11).

The answer is **fit first**: each side's nine categories in the fixed order —
what the roster posts in an ordinary week before and after, the change, and
the change in the chance of winning it — ours on the left and theirs on the
right, stacked on a phone, with a sign and an arrow on every movement so
nothing depends on the colour. Under each, the engine's own sentence, which
leads with the fit. **Then, smaller, the number**: this week plus the change
per week over the weeks left, the projected record either way, whether it
clears the 0.20 bar as a label, the playoff lens, and how an uneven deal was
settled — the named free agent who fills an opened place, or the named drop
and what it cost. Then **what it rests on**, per man: what he is worth a
week, the games behind the projection, his games left and his playoff games,
and a mark on anything thin or hurt. Last, under **How much to trust the
number**, `app.trades.calibration.CALIBRATION_NOTE`, printed verbatim: the
headline picks the better side of a trade about as often as a coin, and the
page says so under the number rather than beside it.

**Every name on it opens a card** — in the inspection drawer along the right
on a desktop, a sheet along the bottom on a phone, on a click (a tap and
Enter alike) — with his line in the nine per game, the games he has left and
the games he has in the playoff weeks, whether he is hurt and when he is
back, and what the projection rests on. Until 2026-09-25 it was a floating
card opened by hover; it is the same content from the same route, in the
drawer. The card lives in `pages.css` and `shell.js` (`cardName`,
`wireCards`, `openCard`) and reads one route of its own, so any page can hang
it off its own names (docs/trades.md §12).

It reads three routes of its own, all the paid team layer: `trades/rosters`
(both rosters as of the day, so the pickers are never hard-coded),
`trades/pool` (the wire for an opened place) and `trades/report` (the deal,
judged); and the card's, which is league scope like the pages it will be
drawn on next. A season with nothing to judge from yet — 2027 before its
draft — is not an error: the routes answer with `readiness` and the page says
so in a sentence and draws no broken pickers. An evaluation is a couple of
seconds, and so is the pool, so the button disables itself and the chooser
says it is reading the wire while the builder stays usable.

**Connections.** Step 2's page, in the workstation's furniture (a label
over one panel a section, a label over each field):
connect a league, your connections, leagues you own (invites, claims to
decide), your league's numbers, **tools that read for you** and your ESPN
SWID. Nothing typed there is ever shown back.

"Tools that read for you" is where a manager makes a machine token
(docs/mcp.md): a name, a button, and the token written to the page once,
because the answer that made it is the only one that carries it. Under it,
his tokens with when each was made and last used, and a Revoke beside each
live one. The line above says what a token can do, which is read the pages
he can read and nothing else.

**Projections.** "Your sets": a table of the reader's own uploaded sets
(name, season, players, uploaded, where from), or "None yet. Without one,
the room and the plans work from ESPN's own projections." "How to upload
one": the file it wants, preview first, the API's own form at
`/docs#/projections`, and `scripts/upload_projections.py` from a terminal.
Reads `/projections/sets`, which answers with his own sets only.

**Alerts.** Step 4 (docs/jobs.md). "Your channels": each of the reader's own,
masked ("p•••@example.com"), confirmed or waiting, with Disable; or "None
yet: nothing is sent to you until you add an address." A Telegram chat or an
ntfy topic he confirmed before 2026-09-22 is listed under it, once, as
stopped, so he is told rather than left wondering why nothing arrives. "Add
an address": one field, because email is the only channel there is; it gets
a link to confirm it. An emailed link lands here with `?token=`, which the
page spends and takes out of the address bar.

"What goes in it", per league: the topics one to a row with a line saying
what each one puts in the message, and above them the two cadence questions
— the morning digest at all, and being interrupted between them. Between
the two, **Email length**: compact (the default) or full, the one setting
that says how much of each section is written rather than which sections
there are (docs/jobs.md, "The two forms"). Ticking anything writes the whole
block back, so what is saved is always what is on the screen, and the page
says so when every topic is off. This is where the email's own "Manage your
alerts" link lands (`#wants`).

Then, for the server's owner only, "The server's own recipients": the
addresses in `FCP_EMAIL_TO`, which are his. Reads `GET /me/channels`,
`GET /me/subscriptions` and `GET /me/alerts`; writes through
`POST /me/channels`, `POST /me/channels/verify`,
`DELETE /me/channels/{id}` and `PUT /me/subscriptions/{league_id}`.

**The email is a page too, and a shorter one.** The morning digest is the
house style in an inbox, compact by default and full as an option
(`app/mail/`, and docs/jobs.md's "The two forms"): the same
light palette written as literal colours, the same masthead and rules, the
same nine categories in the same order, the same "worth a look" and "nothing
clears the bar". It cannot share `pages.css` — an email client does not
fetch a stylesheet and knows no custom property — so the palette is written
out a second time in `app/mail/style.py`, and that is the one place in this
product where a colour is written twice. When the palette moves, it moves
there too.

**Home** (`/`, signed in). Goes to the default league's This week at once
(`location.replace`, so Back does not return to it); with no league yet,
"No league yet" and the two ways in: an invite link, or Connections.

## Decisions

- **The shell became a workstation's rail on 2026-09-25** (docs/design_system.md
  has why and how). The top bar the product document drew is gone; the same
  links and the same behaviour are in the rail, plus two pending items
  (Roster, Players), the scenario line and the theme switch. The pages were
  moved into it with their content untouched.
- **`/design` is an open route.** A reference anyone can open; the file
  carries no data and its specimens read league routes, which keep their
  checks. It is in the open-route lists of `tests/test_access.py`,
  `tests/test_public_ready.py` and `scripts/preflight_public.py`.
- **The shell is a script, not a server template.** It is fed by
  `/auth/me` as the step asks, it is one file for every page, and the pages
  stay static files read per request. The cost is a moment before the bar
  fills in; the frame (the name and the switch) is drawn at once.
- **One thin route, `GET /me/alerts`,** because the alerts page has to say
  what the server is configured with and no route said it. Signed in; the
  owner alone sees the recipients.
- **The email is drawn from the site's palette, not from its stylesheet.**
  An inbox fetches nothing and knows no custom property, so `app/mail/` says
  the house style again in the only vocabulary an email has. The duplication
  is the price of an email that looks like the site.
- **`/leagues` gained `name`** (the newest season's), for the switcher: a
  field on an existing route, rather than a request per league.
- **The old addresses keep their checks** rather than being open redirects:
  refused at the old address exactly as at the new, and no new open route.
- **`/` is open** and answers with one of two files, neither with data; the
  signed-in one lets the shell choose the league, because the remembered
  league lives in the browser, where the server cannot read it.
- **The old index is the standings now.** Every team with its record was
  what it showed; its links to every team's plan are the standings' names,
  which open only where the reader may open them (every team in single
  mode).
- **The free look at the reader's own week** (This week only, and only for
  the period in play) read the paid stream route until step 4, which would
  have answered a free member 402 once billing went on. Since step 4 it
  reads `/pickups/glance`, which needs the team's manager and not the paid
  tier and answers from the morning's stored week report (built live when
  there is none), so billing changes nothing on this page. It is on This
  week and not on Standings, because without a stored report it is the slow
  one (up to a minute, cold).
- **This week shows stored scores, not a replay.** A past day picks the
  period; the score is the period's final one, and the page says so.
- **Moves is in the paid layer, its data in the free one.** "The scorecard
  of your own moves" is paid (docs/product.md), and every team's scorecard
  is league scope (docs/accounts.md). The page is behind the team check; the
  route stays league scope, as decided in step 1.
- **The trade page leads with the fit and prints the record under the
  number.** docs/trades.md section 7 measured the headline at a coin, and the
  category table at nothing of the sort — it is the same week laid out one
  line at a time. So the nine come first and the number second and smaller,
  and `CALIBRATION_NOTE` is served by both trade routes rather than written
  into the page, so a re-run of the calibration cannot leave a stale record
  in the markup.
- **A trade's rosters come from a route, not from the page.** The builder
  could have been fed from `pages/context` and a guess, and it would have
  been wrong on any replayed day. `trades/rosters` answers with the roster
  stored on or before the day asked for, which is the same reading every
  other number on the site is made from.
- **The wire for an opened place is ranked by what a man is worth to *this*
  roster**, not by the league standard the report falls back on. Both numbers
  are on every row, because the two disagreeing is the reason the manager is
  being asked at all (docs/trades.md §11).
- **The card is league scope and lives in the shell.** It is opened from a
  team page today and from three more tomorrow, the numbers it shows are a
  league season's, and a second copy of it per page would drift. What a man
  is worth a week is not on it: that number costs two seconds to measure and
  opening a card should not, and every page that shows it shows it beside
  the name (docs/trades.md §12).
- **A season with nothing to judge from is a 200, not a 409.** The two
  pickup routes refuse an unlistened season, because a plan with no wire is
  not a plan. A trade page has a builder to draw and a record to print before
  any deal exists, so its routes answer with `readiness` and let the page say
  what is missing.
- **The season is chosen in the switcher and on each league page's header
  line**, not as a new item in the bar, which is drawn exactly as the
  product document has it.
- **Sign out is hidden in single mode**, where there is no session.
- **The claim and join pages** keep their step 2 addresses and gain the
  shell.

## Not done here

- The upgrade page and a real 402 path (step 7).
- A season picker for the team pages beyond the switcher.
- The draft room stays its own dark screen, outside the shell.
