# The design system: a roster-planning workstation

**Written:** 2026-09-25. **Status:** the foundation and the shell are built —
tokens, the three voices, both themes, the colour rule, the geometry, the
rail, the scenario bar, the inspection drawer, the screener table, the
controls and `/design`. Every existing page now renders inside the new shell
with its own content and layout untouched. The pages themselves are migrated
one at a time next (the order is below).

Code: `app/api/static/pages.css` (the tokens at the top, then every `ws-`
component, then each page's own furniture, marked), `pages.js` (the theme,
the glyph scale, the FIT cell, the screener, the move block), `shell.js`
(the rail, the top bar, the drawer, the card in the drawer, the scenario
bar), `scenario.js` (the scenario state: the seam), `design.html` (the
language on real data, at `/design`). Tests: `tests/test_shell.py`.

## The direction, in the owner's words

> Box Out is a **roster-planning workstation**. Serious analytical software
> for people who manage fantasy basketball competitively. Basketball gives it
> personality; forecasting and scenario analysis give it purpose. Think
> TradingView with a little basketball DNA. Not a cream editorial newspaper,
> and definitely not a dark SaaS dashboard with fourteen floating rounded
> cards.

And the sequencing: redesign the visual system and the application shell,
not the individual product pages; build the tokens and the shell first so
the rest can be migrated page by page. This replaces the cream editorial
idiom the week page was written in (docs/in_season_pages.md keeps the record
of that design and why).

## Tokens

All colour is in one block at the top of `pages.css`. Light is `:root`; dark
is written twice — under `@media (prefers-color-scheme: dark)` guarded by
`:root:not([data-theme="light"])`, and under `:root[data-theme="dark"]` —
because it has two ways in: the system's preference, and the viewer's own
choice. `.t-light` and `.t-dark` carry a theme into one subtree, which is how
`/design` shows the controls in both at once and reads every token's value
in both from the stylesheet itself.

| Token | Light | Dark | What it is for |
|---|---|---|---|
| `--canvas` | `#F2F3F5` | `#0D0F12` | the ground: the rail and the page |
| `--panel` | `#FFFFFF` | `#15181D` | what sits on it: a table, the bar, the drawer |
| `--inset` | `#E8EAEE` | `#1E2228` | inside a panel: a header row, a hover, a well |
| `--line` | `#DDE0E5` | `#2A2F37` | a hairline between rows and panels |
| `--line-strong` | `#8C939E` | `#6B7480` | a control's edge, a header's rule (3.1 and 3.8:1 on panel) |
| `--fg` | `#15181D` | `#E7E9EC` | text |
| `--fg-2` | `#474E59` | `#AAB1BB` | secondary text |
| `--fg-3` | `#59616D` | `#8E96A2` | tertiary text, labels |
| `--accent` | `#D35A1C` | `#F47D38` | **orange**: a mark, a rule, a fill — not small text |
| `--accent-text` | `#B4460F` | `#F47D38` | orange that is read at 11 px |
| `--accent-wash` | `#FBE7DA` | `#3A2416` | the selected row, the scenario in view |
| `--accent-ink` | `#FFFFFF` | `#0D0F12` | text on a solid orange |
| `--pos` / `--pos-wash` | `#17703F` / `#E1F1E7` | `#45BE78` / `#15301F` | **green**: a change that helps your roster |
| `--neg` / `--neg-wash` | `#B8322A` / `#F8E1DE` | `#F2685F` / `#3A1C1A` | **red**: a change that costs your roster |
| `--neutral` | `#59616D` | `#8E96A2` | **grey**: everything else |
| `--focus` | `#15181D` | `#E7E9EC` | the keyboard's ring |
| `--scrim`, `--shadow-float` | | | behind a sheet; under the one thing that floats |

Not colours: `--font-ui`, `--font-accent`, `--font-data`; the type scale
`--t-2xs` 10.5 · `--t-xs` 11 · `--t-sm` 12 · `--t-md` 13 · `--t-base` 14 ·
`--t-lg` 16 · `--t-xl` 20 · `--t-2xl` 26 px; space `--sp-1`…`--sp-6` on a
4 px step (4 8 12 16 24 32); `--r-control` 4 px, `--r-panel` 6 px; `--row`
30 px; `--rail-w` 224 px, `--drawer-w` 400 px, `--top-h` 48 px.

**The legacy names.** The pages were written against the report house
style's names (`--ground`, `--surface`, `--sunk`, `--rule`, `--rule-strong`,
`--ink`, `--muted`, `--faint`, `--accent-deep`, `--accent-soft`, `--good`,
`--stop`, the `-soft`s, `--shade-*`, `--display`, `--body`, `--mono`). They
are kept, pointed at the tokens above, in a rule of their own on every theme
root, so each page sits in the new palette with its layout untouched. Two
have no new counterpart and are marked legacy-only: `--warn` (the pages'
amber injury flag) and `--shade-1..3` (the old strip's probability shading).
No `ws-` component reads a legacy name; each is retired when the last page
using it is migrated.

### Contrast

Every face at small size clears WCAG AA (4.5:1) on every surface it is set
on, in both themes. `/design` works the same table out live from the
stylesheet, so it cannot drift from this one.

| Text | on canvas | on panel | on inset | on accent-wash |
|---|---|---|---|---|
| **Light** | | | | |
| `--fg` | 16.03 | 17.79 | 14.77 | 14.86 |
| `--fg-2` | 7.56 | 8.39 | 6.97 | 7.01 |
| `--fg-3` | 5.64 | 6.26 | 5.20 | 5.23 |
| `--accent-text` | 4.95 | 5.49 | 4.56 | 4.59 |
| `--pos` | 5.52 | 6.13 | 5.09 | 5.12 |
| `--neg` | 5.36 | 5.95 | 4.94 | 4.97 |
| **Dark** | | | | |
| `--fg` | 15.78 | 14.63 | 13.13 | 11.96 |
| `--fg-2` | 8.88 | 8.23 | 7.39 | 6.73 |
| `--fg-3` | 6.43 | 5.96 | 5.35 | 4.87 |
| `--accent-text` | 7.16 | 6.64 | 5.96 | 5.43 |
| `--pos` | 8.12 | 7.53 | 6.76 | 6.16 |
| `--neg` | 6.33 | 5.86 | 5.26 | 4.79 |

The lowest is 4.56 (light orange text on the inset surface). Green on its
own wash is 5.24 light and 6.03 dark, red on its own 4.77 and 5.09. `--accent`
itself is 4.0:1 on a light panel, which is why it is never small text:
`--accent-text` is. The primary button's text is 16.0 and 15.8. The legacy
`--warn` is 6.2 (light, panel) and 7.9 (dark, panel).

## Three voices

One face per job, loaded from Google Fonts with a real fallback stack.

- **The interface — IBM Plex Sans** (`--font-ui`): every label, sentence,
  name and control. 13–14 px in a row, 400 and 500, 600 for a name.
  *Why this one:* it is drawn as one family with IBM Plex Mono, which the
  site already uses for its data, so a label and the figure beside it share
  an x-height, a stroke and a logic — the jump from word to number in a dense
  row is only the jump from proportional to fixed width. It is a grotesque
  with engineering character rather than a friendly geometric, which is the
  register of analytical software rather than of a consumer app; it holds up
  at 11–13 px on screen; and it has tabular figures, which the body turns on
  globally. Inter was the default nobody chose; Plex Sans is chosen because
  it belongs with the data face. Fallback: `-apple-system`, `Segoe UI`,
  `system-ui`, Roboto, Helvetica Neue, Arial.
- **The accent — Oswald** (`--font-accent`, `.ws-label`): a major section's
  label, in capitals — MATCHUP, ROSTER, THE WIRE, SCENARIO, STANDINGS — and
  the name in the rail. Never every heading, never a sentence, never a figure.
  The scoreboard's condensed capitals are the basketball DNA; used everywhere
  they become a newspaper again.
- **The data — IBM Plex Mono, tabular** (`--font-data`, `.ws-data`): every
  figure. Every column of numbers is tabular so it reads down. Negative
  numbers in the new components carry a true minus sign (`minus()`).

Source Serif 4, the report house style's body face, is no longer loaded: the
legacy `--body` points at Plex Sans, so the pages read in the interface voice.
Their headings keep Oswald until each is migrated (see Migration).

## Colour: four that mean something

- **Orange is Box Out's, and scarce.** It marks the page you are on (the
  rail), the scenario in view (the bar's edge, the pressed half of BASELINE |
  SCENARIO, the rail's scenario line), the comparison in focus (the selected
  row, the column the bar has in view), and at most one series in a chart.
  It is never a button's fill (the primary button is ink), never a status,
  never decoration, never the focus ring (that is ink too).
- **Green is a change that genuinely helps the viewer's own roster; red is
  one that costs it.** Never "good player / bad player". A `PTS −4%` can be
  red beside a `REB +13%` that is green for the same man, and on `/design`
  the real day-52 move shows exactly that: adding Josh Minott is
  `3PM +3 pts` green beside `FG% −2 pts` red. A figure under half a point (or
  half a hundredth of a category) is grey: too small to call.
- **Grey is everything else**: a status (Q, OUT — a fact, not a change), a
  count, a label.

Each carries its rule in its token's comment in `pages.css`.

## Geometry

Sharp: 4 px on controls, 6 px on containers, 2 px on a tag, nothing on a
table. Hierarchy from borders and three tonal surfaces (canvas → panel →
inset), not from shadows; panels share an edge where they meet. A shadow is
for something that genuinely sits above the page — the drawer, the column
menu, the rail when it slides over a phone — and nothing else. Dense: a
screener row is 30 px. A phone gets the same hierarchy narrowed, not a
different product; nothing scrolls the page sideways at 390 px (a wide table
scrolls in its own frame, which is positioned so even a screen reader's
label in a far cell stays inside it).

## The shell

### The rail

```
BOX OUT
● OVERVIEW
TEAM   Through The Wire
  Matchup · Roster (soon) · Moves · Trades · Season
LEAGUE
  This week · Standings · Players (soon) · Draft · History
───
◈ SCENARIO   Current scenario · 2 changes
───
🏀 Patriot Games · 2027 ▾        (the league and season switcher)
Account ▾ (email, Projections, the design language, Sign out)
Connections · Alerts · Theme  Light ◐
```

| Item | Goes to | Notes |
|---|---|---|
| Overview | the team's week page | the Overview page is a later job; never marked current |
| Matchup | `/l/{l}/{s}/team/{t}/week` | current on the week page |
| Roster | the week page at `#tonight-section` | **pending**: no page of its own; says "soon" |
| Moves, Trades, Season | the team's pages | |
| This week | `/l/{l}/{s}/week` | the league's week, kept from the old bar |
| Standings, Draft, History | the league's pages | |
| Players | the week page at `#whatif-section` (its wire), else the league's week | **pending**: says "soon" |
| Scenario | the week page's What if | the state line; orange while a scenario is in view |
| the switcher | as before | leagues, and the seasons of this one |
| Account | a menu | email, Projections, the design language, Sign out (or the single-mode line) |
| Connections, Alerts | `/account/...` | |
| Theme | toggles light/dark | follows the system until pressed; the choice kept per viewer |

Every link carries `?today=` (and `?me=`) where it means something. Without
a claim, TEAM is **Claim your team** (or **Claim pending**), as before. The
page you are on is the one item with the orange edge.

Below 960 px the rail becomes a top bar — the name, where you are, **Menu** —
and Menu slides the same rail in over the page, with a scrim; Escape, the
scrim or Close puts it away and gives the focus back.

**The theme** follows `prefers-color-scheme` until the viewer presses the
switch; the choice is kept in `localStorage` (`fcp-theme`, try/catch) and
applied by a one-line script in each page's `<head>` before first paint, so
a dark reader is never flashed with the light page.

### The scenario bar

Over the page (sticky on a desk; on a phone it scrolls away with the page
rather than hold a third of the screen) whenever a scenario is set:

```
◈ SCENARIO · JUDGED ON DAY 52      + Josh Minott BKN · SF   − Maxime Raynaud SAC · C
Current scenario                   WEEK +0.02 cat this week   SEASON +0.04 cat a week   PLAYOFFS +0.07 cat a playoff week
                                   [BASELINE | SCENARIO]  Inspect  Reset  Save scenario
```

It is driven by the existing **What if on the week page only**. When a
what-if has been run, the bar appears with that change and its three
numbers, all fields of the answer already: **Week** is `week.delta`
(categories this matchup period), **Season** is
`judgement.delta_season_per_week` (an ordinary week from here on), and
**Playoffs** is `playoffs.delta_per_week` when that lens is measurable. Each
is green or red for the viewer's roster. The signs on the changes (+ in,
− out, → IR) are what they are and carry no colour.

- **BASELINE | SCENARIO** switches the What if section's rendering: SCENARIO
  is the answer as it was drawn before; BASELINE is the same answer's
  `week.before` in the same three bands and the finish before the change.
  No number is new.
- **Inspect** opens the drawer on where each of the three came from.
- **Reset** clears the scenario: the bar goes, the section returns to its
  form and its first lede. A failed Run resets it too, so the bar never names
  a change the section no longer shows.
- **Save scenario** is drawn and disabled, with its reason: there is
  nowhere to keep one yet.

### The seam for global scenario state

Global scenario state — kept across pages, saved, read by every route (the
standings' projection, the trade page, the rail's count on every page) — is
**not this job**. What exists is its seam, `app/api/static/scenario.js`:
`SCENARIO.get()`, `set(next)`, `view("baseline" | "scenario")`, `reset()`,
`subscribe(listener)` (returns the unsubscribe), and `fromWhatIf(answer,
scope)`. The shell and the week page talk only to that. The state:

```
active      false until a scenario is set
id          null — nothing is stored, so nothing has a key yet
name        "Current scenario"
scope       {league, season, team, today}
changes     [{kind: "add" | "drop" | "ir" | "trade", espn_player_id, name, pro_team, position}]
effect      {week, season, playoffs}, each {value, unit, note}; value null when the lens says nothing
view        "scenario" | "baseline"
source      {route: "what-if", day, date}
provenance  [[label, text]] — the drawer's account of each number
saved       false
```

It lives as long as the page. When scenarios go global, this module grows a
store behind the same five calls; the bar, the rail and the pages already
read it through `subscribe`.

### The inspection drawer

One panel along the right (400 px; a sheet from the bottom with a scrim at
phone width) that a thing opens into, so it is inspected without leaving the
page: a player's card, a move's working, a scenario's numbers. It sits above
the page and is the one panel with a shadow. It is non-modal on a desk — the
page stays live beside it — and stays until closed; Close or Escape gives the
focus back to what opened it.

**The player card now opens into it** instead of a floating card: the same
route (`players/{id}/card`), the same content — the nine per game, games
left, playoff games, what the line rests on, and the flag — laid out as a
nine-cell strip and a list of facts. A click opens it (a tap, a mouse click
and Enter are all one click, so the first tap always works). Hover and focus
never open it; on a `data-card-hover` control (a roster row that is already
the deal's button) they only follow a drawer already showing a card.

**Provenance, the workstation way:** "how this is worked out" for a move or
a scenario is a list of facts in the drawer — each figure, then where it
came from — rather than a paragraph under the thing.

### The screener table

`screener(host, spec)` in `pages.js`: dense 30 px rows, a sticky header,
tabular numerals right-aligned, a header that sorts on a click
(`aria-sort`), columns that can be put away (the choice kept per viewer per
table), row hover, a name button in the row for the keyboard, and a selected
row (orange's wash, an orange edge) that opens the drawer.

- **The glyph scale** for a category's change: `++` five points or more
  better, `+` half a point to five, `·` under half a point, `−` and `−−` the
  same worse. The shape says the size; the colour only repeats it; the words
  are there for a screen reader. Its legend is drawn under the table.
- **FIT**: a figure for this roster with a bar as long as its share of the
  column's largest, green for a gain and red for a cost (`fitHtml`).

It is demonstrated on the trade page's pool — **Who fills it** is now drawn
by it: the same men from `trades/pool`, FIT is `worth` and League is `value`,
first by FIT as the list was. **Use** names a man for the open place exactly
as tapping his row did (and goes into the address bar as before); a click
anywhere else on the row opens his card in the drawer. "Leave it open" stays
the first choice above the table. Nothing the page computes changed.

### Controls

`ws-btn` (primary is ink, secondary is outlined, quiet has no edge; `sm` is
26 px), `ws-seg` (the segmented toggle; `.scn` marks the pressed half of
BASELINE | SCENARIO in orange, any other is neutral), `ws-select`, a search
`ws-input`, `details.ws-disc` (the disclosure, a sign for its marker), and
`ws-tag`. All sharp, all 30 px (26 small), all in both themes.

### A move, in the owner's format

`moveBlockHtml(move, report)` draws a week-report move:

```
JOSH MINOTT   BKN · SF                          +0.49 CATEGORIES
DROP    Maxime Raynaud SAC · C
GAIN    3PM +3 pts   STL +2 pts
COST    FG% −2 pts
2 of 2 games left · bid $2          [worth a look]          Inspect →
```

On `/design` the numbers are the real day-52 week report's first move for
Through The Wire in 2026 (`pickups/stream?today=52`), not invented. Two
honest departures from the sketch: the headline is the move's **net over
both horizons, in categories** — the number the week page leads with — so it
is labelled CATEGORIES rather than CAT/WEEK; and GAIN and COST are changes in
the **chance of winning** each category this week, in points (`move.moved`),
so they are written `+3 pts` rather than `+3%`. The label is the bar's
("worth a look", or "under the 0.20 bar"), and a move under it is drawn the
same way.

### Basketball, without pictures

`PHI · SG` (`bbMark`), `vs POR · 8:00 PM`, `Q` (`statusMark`), `2 games left`,
`Thu / Sun` (the days a man plays the rest of the period, read from the week
report's own games table): the NBA abbreviation is the mark. No headshots,
silhouettes or initials, ever.

## `/design`

Open (signed out it draws the language and says the specimens need a
league; the file carries no data and every number is read from a league
route behind its own check). Signed in, it draws on the viewer's own
league's stored 2026 season, day 52: the token table and the contrast table
read from the stylesheet in both themes; the three voices; the four colours
with a real example; the geometry; the live rail and a live scenario bar,
set from a real `what-if` for the day's first move, with a panel of the
nine that the bar's toggle switches; the move block with Inspect; two
screeners (every move the report considered, its nine as glyphs; the day's
wire with FIT); the controls in both themes side by side; the basketball
marks from the day's real lineup; and what is not here. It is reached from
the Account menu. The first read of the day-52 week report on a cold server
takes about half a minute, and the page says so while it waits.

## Not here, and why

- **Large KPI cards, giant greetings, excessive whitespace.** A manager comes
  to compare; a figure is read beside the figures it is compared with.
- **Gradients, glass, oversized rounded cards, pill-heavy layouts.**
  Hierarchy is borders and three surfaces; anything else is decoration.
- **Colourful avatars, silhouettes, initials, decorative player images,
  headshots.** None, ever (the owner's rule). `PHI · SG` is the mark.
- **Any pattern whose purpose is decoration.**
- **Verdicts.** Tool, not gospel: no "recommended" anywhere in the shell or
  on `/design`; a bar labels and never hides; every number's provenance is
  one click away in the drawer; the other side of a deal is "our estimate of
  their side"; nothing looks like it acts on ESPN; the calibration sentences
  stay where they are on the pages.

## Migration

Every page renders in the new shell now, with its content untouched:

- the old top bar is gone; `<div id="shell">` draws the rail;
- `<html data-theme="light">` lost its hard-coded theme (the system's
  preference applies until the viewer chooses) and each `<head>` applies a
  kept choice before paint;
- the fonts are the three voices, and the legacy tokens point at the new
  palette, so every page is in the new colours and the new body face;
- each shell page loads `scenario.js` between `pages.js` and `shell.js`;
- every player card opens in the drawer;
- the week page lost the two things the shell now carries — the "Elsewhere"
  disclosure under More (Trades, Season, Moves are in the rail) and the
  product's name at the head of its eyebrow — and gained the scenario bar;
- the trade page's Who fills it is drawn by the screener.

Otherwise every page keeps its own furniture — the mastheads, the Oswald
headings, the rules, the strips, the `.cta` in orange, the landing page's bar
— until it is migrated, in this order: **Overview** (a new page, the rail's
first item) → **Matchup / the week** → **Players / the wire** (a new
screener page, the rail's second pending item) → **Trades** → **Standings**
→ the rest (Season, Moves, This week, Draft, History, the account pages,
the landing and sign-in pages). The email (`app/mail/style.py`) keeps the
report house style's literal colours until the site's migration is done; it
is the one other place the palette is written.

## Decisions

- **IBM Plex Sans for the interface**, for the reasons under Voices.
- **Light and dark both first-class, and the system decides until the
  viewer does.** The old default was always light. Now a dark system gets
  the dark workstation on first visit; the switch makes a choice and keeps
  it.
- **The legacy token names are aliases, not a second palette.** A page
  migrated later is a page that stops using them; nothing has to be
  re-coloured by hand in between, and no page looks half in one world.
- **The primary button is ink, not orange**, so orange stays the colour of
  "selected / current / in focus", and a page with three buttons does not
  spend it three times. The focus ring is ink for the same reason.
- **Status is grey.** OUT is a fact about a man, not a change to your
  roster; red is reserved for the change.
- **The drawer is non-modal on a desk and a sheet on a phone.** Inspecting
  must not take the page away; on a phone there is no room beside it.
- **Hover never opens anything.** A drawer that opened on hover would
  fire as the pointer crossed a table; a click is the one gesture that
  means "inspect this" on a mouse, a finger and a keyboard alike.
- **The scenario is a module now and a store later.** Driving the bar from
  the week page's own answer proves the component on real numbers without
  inventing a persistence model before the product has decided what a saved
  scenario is.
- **`/design` is open and data-free in the file.** It is a reference any
  reader can open; the specimens read the league's routes, which keep their
  checks, so a stranger sees the language and no league's numbers.
- **Classes are prefixed `ws-`.** A page being migrated can be searched for
  what it still uses of the old, and nothing in the page furniture can reach
  into a component by accident.
- **Players and Roster are in the rail before their pages exist**, each
  marked "soon" and opening the nearest existing thing (the week page's wire
  and Tonight), because the rail is the information architecture and should
  not change shape when the pages arrive.
