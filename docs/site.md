# The site: one map, one shell, every page under it

**Written:** 2026-09-19. **Status:** built, step 3 of docs/product.md (the
shell, the league switcher, the account pages, the existing pages moved
under it, the landing page). The VPS serves it in single mode like the rest
of the API; accounts mode, and so the landing page for a stranger, is the
cutover's (docs/accounts.md).

Code: `app/api/site.py` (the routes, and the one JSON route the pages
needed), `app/api/static/shell.js` (the navigation), `app/api/static/*.html`
(one file per page), `app/api/static/pages.css` and `pages.js` (shared).
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
| `/account/connections` | Connections: connect a league, invites, claims, your SWID | anyone signed in | `connections.html` |
| `/account/projections` | Projections: your uploaded sets, how to upload | anyone signed in | `projections.html` |
| `/account/alerts` | Alerts: where the digest goes, read-only | anyone signed in | `alerts.html` |
| `/pages/claim/{league_id}/{season}` | Claim your team (step 2's, now under the shell) | a member of the league | `claim.html` |
| `/join/{token}` | where an invite link lands (step 2's, under the shell) | anyone signed in | `join.html` |

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

Drawn by `shell.js` into `<div id="shell">` at the top of every signed-in
page, from two routes: `/auth/me` (who, his role in each league, his claims)
and `/leagues` (his leagues, each with its name and the seasons held; in
single mode, every league stored).

```
FULL COURT PRESS  [ Patriot Games 2026 ▾ ]  THIS WEEK  STANDINGS  DRAFT  HISTORY  MY TEAM ▾   ACCOUNT ▾  [Lights down]
                    Your leagues                                          Through The Wire   you@example.com
                    Seasons 2027 2026 ...                                 Week               Connections
                                                                          Season             Projections
                                                                          Moves              Alerts
                                                                                             Sign out
```

- **The league** is the one in the URL. On a page without one (the account
  pages, `/`) it is the one this browser last looked at, kept in
  `localStorage` under `fcp-league` (wrapped in try/catch: a private window
  just forgets it), else the first league where he manages a team, else his
  first. The season is the URL's, else the newest held.
- **The switcher** lists his leagues (each to the same section of its newest
  season) and the seasons of this one (each to the same page that season).
  A league connected but not yet ingested is listed as waiting.
- **The sections** are This week, Standings, Draft and History for this
  league and season; the current one is in the accent with a rule under it
  (`aria-current="page"`).
- **My team** is there only where he is a verified manager in this league:
  this season's team, else his newest one there. Its menu is Week, Season,
  Moves. Without one it is **Claim your team** (or **Claim pending**), to
  the claim page. In single mode, where the one user reads every team, a
  league with no claim falls back to the team the context route calls ours
  (`MANAGER_TEAM`, or `?me=`), which is what the pages always did.
- **Account** holds his email, Connections, Projections, Alerts and Sign out
  (a form that posts to `/auth/sign-out`). In single mode Sign out is
  replaced by the line "Single mode: nobody signs in", because there is no
  session to end.
- **The switch** (Lights down / Lights up) flips to the draft room's dark
  palette and is remembered per browser, as before.
- **The menus** are buttons that open a list of links (the disclosure
  pattern). Click, Enter or Space opens one; ArrowDown or ArrowUp opens it
  on the first or last item; the arrows, Home and End move through it;
  Escape closes it and puts the focus back on its button; it closes when the
  focus or a click goes elsewhere.
- **At phone width** the bar is two rows: FCP, the league and Account with
  the switch (a glyph, its words kept for a screen reader) on the first, the
  sections on the second, wrapping rather than scrolling. Nothing scrolls the
  page sideways; a wide table scrolls in its own frame.

`SHELL` is a promise of what it found (`{me, league, season, myTeam, ...}`),
which the league pages await to know which team is the reader's.

## The pages, in words

**The landing page** (`/`, signed out). The bar with the name and "Sign in".
An eyebrow, "Head-to-head, nine categories, on ESPN"; the headline "Your
league, read properly"; a paragraph on what it does, ending "the moves worth
a look. You decide."; a burnt-orange block, "Sign in with your email". Below
a rule, three short columns: The league (free for every member), Your team
(private to its manager), Getting in (one member connects, invites the
rest, no password). One screen, no data, no script but the theme.

**This week.** Eyebrow: the league's name and season. Headline "This week"
("The playoffs" in a playoff period). A line: "Matchup period 15, days
98–104 (Mon, Jan 26 – Sun, Feb 1). Day 100 (Wed, Jan 28): 5 days left, today
included." The seasons as a row of small links, then "‹ Period 14 · Period
16 ›". A readout of four: the period (of 22), its days, days left in the
accent, the number of matchups (and byes). Then, for the reader's own team
only, a block with an accent rule down its left, "Your week: Through The
Wire": expected to take 5.26 of 9 categories against Masters of their
Domains, projected to end the season 106.8–64.2 in categories with no move
made, the nine as a shaded strip, and "See the moves worth a look". Then
"The matchups", each under a rule: the two names with the categories-won
count between them, a line ("Final: Masters of their Domains won 5–4", or
"… leads 5–3", or "Level at 4–4"), and each side's nine as the strip, a cell
shaded where that side is winning the category. The reader's matchup is
first with the accent rule. A bye says so.

It reads `pages/context` (the day, the teams), `/periods` and
`/matchups?period=N`. `?today=` picks the day and so the period; `?period=`
picks a period outright (the arrows). The scores are ESPN's as the ingest
last stored them, so a past day shows its period's **final** score, and the
line says so ("The scores stored are the period's final ones, played out
since."): no route keeps a matchup's score day by day. A season whose
matchups have no score yet says "The period has not begun".

**Standings.** Headline "Standings", "14 teams. The regular season's
matchups, byes left out.", the seasons row. One table (scrolling in its own
frame on a phone): rank, team, matchups W–L–T, categories W–L–T, the share
of categories won, the longest winning and losing runs, the run the season
ended on (W2, L1), the final finish (#1). The reader's team is shaded, and
its name opens his week; in single mode every team's does. Reads
`/standings` and `/streaks`.

**Draft.** Headline "The draft", "182 picks, $2796 spent. The dearest:
Victor Wembanyama at $100, to Fantastic 5." A readout: picks, spent,
dearest, keepers, and the reader's own ("$200 on 13 players"). "Value for
money": the ten best and ten worst picks by points per dollar (player, team,
paid, points, games, per $), with a paragraph saying plainly it is a blunt
measure in NBA points, not categories. "The board": every pick in order with
who bought him, the price and who nominated him; the reader's picks shaded.
A season not yet drafted says so. Reads `/draft` and `/draft-value` twice
(best, worst).

**History.** Headline "History", "46 owners over 8 seasons". "Where each
team was strong": each team's name beside its nine as the strip, its share
of matchups won in each category, strongest first, the reader's in the
accent. "The results people remember": sweeps and nail-biters of the season.
"Every owner, every season": each owner by his latest team and ESPN name,
seasons, matchups, share, titles, best finish. "Head to head": a select of
owners, set to the reader's own, and his record against everyone he has
met, with the seasons. Reads `/category-profiles`, `/notable-matchups`,
`/leagues/{id}/owners` and `/leagues/{id}/head-to-head`.

**My team: Week and Season.** The streaming and rest-of-season reports,
unchanged in content (docs/in_season_pages.md): the same masthead, readout,
tale of the tape, plan and moves. Only the frame changed: the shell is over
them, and their old "Rest of season" and "Every team" buttons are gone,
because My team and the sections are those links now.

**My team: Moves.** The team's name; "The season's moves, graded in
categories a week. The league's median pickup was worth 0.072 categories a
week." A readout: wire moves, trades, draft picks, the median pickup. "The
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

**Connections.** Step 2's page, restyled into the shell with section rules:
connect a league, your connections, leagues you own (invites, claims to
decide), your ESPN SWID. Nothing typed there is ever shown back.

**Projections.** "Your sets": a table of the reader's own uploaded sets
(name, season, players, uploaded, where from), or "None yet. Without one,
the room and the plans work from ESPN's own projections." "How to upload
one": the file it wants, preview first, the API's own form at
`/docs#/projections`, and `scripts/upload_projections.py` from a terminal.
Reads `/projections/sets`, which answers with his own sets only.

**Alerts.** "Your channels", read-only: for the server's owner, the digest's
channels (email to the addresses in `FCP_EMAIL_TO`; a Telegram chat or an
ntfy topic by kind, never its URL); for anyone else, "Nothing is sent to you
yet." Then the line that per-member channels arrive in step 4. Reads
`GET /me/alerts`.

**Home** (`/`, signed in). Goes to the default league's This week at once
(`location.replace`, so Back does not return to it); with no league yet,
"No league yet" and the two ways in: an invite link, or Connections.

## Decisions

- **The shell is a script, not a server template.** It is fed by
  `/auth/me` as the step asks, it is one file for every page, and the pages
  stay static files read per request. The cost is a moment before the bar
  fills in; the frame (the name and the switch) is drawn at once.
- **One thin route, `GET /me/alerts`,** because the alerts page has to say
  what the server is configured with and no route said it. Signed in; the
  owner alone sees the channels; a URL channel is named by kind only.
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
  the period in play) reads the paid stream route, quietly: while
  `BILLING_ENABLED` is off it always answers. When billing goes on, a free
  member would hear 402 and the block says the week is the team layer; the
  numbers themselves then need a manager-only route without the entitlement,
  or step 4's stored reports. Not built now: nothing needs it until step 7,
  and step 4 changes where the numbers come from. It is on This week and not
  on Standings, because the report behind it is the slow one (up to a
  minute, cold).
- **This week shows stored scores, not a replay.** A past day picks the
  period; the score is the period's final one, and the page says so.
- **Moves is in the paid layer, its data in the free one.** "The scorecard
  of your own moves" is paid (docs/product.md), and every team's scorecard
  is league scope (docs/accounts.md). The page is behind the team check; the
  route stays league scope, as decided in step 1.
- **The season is chosen in the switcher and in each league page's
  masthead**, not as a new item in the bar, which is drawn exactly as the
  product document has it.
- **Sign out is hidden in single mode**, where there is no session.
- **The claim and join pages** keep their step 2 addresses and gain the
  shell.

## Not done here

- The upgrade page and a real 402 path (step 7).
- Per-member alert channels (step 4); the alerts page is read-only until
  then.
- A season picker for the team pages beyond the switcher.
- The draft room stays its own dark screen, outside the shell.
