# FCP Core Status

## Works today

- FastAPI application boots (`create_app()`)
- HTTP API over the stored seasons (see below), including seven narrative
  routes, the pickup reports and the in-season pages. Writes stay with the
  ingest, except a manager's own projection upload.
- Nightly scheduled ingest keeping the current season current, and the next
  season's settings current while its draft is ahead, with every run recorded
  and queryable. **Runs on the VPS**, not a laptop.
- The in-season listener (`app/listener`, docs/pickups.md layer 1): a status
  pass three times a day snapshots every player ESPN carries for the league,
  injury status, return date, NBA team, fantasy team and ownership, into
  `player_status_snapshots`, the unrostered ones into `free_agent_snapshots`,
  the NBA schedule into `pro_team_games`, diffs each player against his
  previous snapshot into `player_status_events` (thirteen kinds, including a
  minutes spike read from the stored box scores), and fetches news into
  `player_news` for the players that changed and the tracked team's roster.
  Migration 0016; `scripts/status_pass.py`; `deploy/fcp-core-status.timer`.
  **Running on the VPS since 2026-09-17.** Status history cannot be
  backfilled, so 2027 is the first season with any.
- The morning digest (`app/digest.py`, `scripts/digest.py`): what changed on
  the tracked roster, where that roster stands, which free agents are worth
  a look, and the team's adds in a fortnight. Plain text, under forty lines,
  no ESPN request. Delivered by one POST to `FCP_DIGEST_URL`, and an event
  is marked notified only once that POST has succeeded. The later passes
  send a one-line alert instead, and only for a player of yours being ruled
  out. **Running on the VPS**, delivered to Telegram. Since 2026-09-18 it
  also carries the day's plan ("THIS WEEK": the matchup, adds used of the
  budget, the empty days and the moves worth a look), goes to email as well
  when the six `FCP_SMTP_*`/`FCP_EMAIL_*` settings are set
  (`scripts/notify_test.py --email` to check), and is followed by
  `scripts/warm_pages.py`, which asks the API for our team's two reports so
  the pages open at once. The full digest is the pass labelled morning,
  15:00 UTC.
- The pickup recommender (`app/pickups`, docs/pickups.md), deployed. Every
  move is judged in one currency, categories, over both horizons: this
  week's head-to-head change plus the rest-of-season change per week times
  the weeks left (`app/pickups/judge.py`), with the season's projected
  category record with and without the move. The drop charge is the gap to
  the wire on both sides of the place, so streaming a fringe player costs
  nothing and dropping a real one is charged. It knows the league's add
  budget (one per day of the period, spent on any days; not stored by ESPN
  in anything we ingest), plans up to two independent moves a day, and will
  not seat a player on waivers before he clears. Hurdles set by the backtest
  and Patrick on 2026-09-18: 0.20 categories streaming, 0.10 / 0.05 a week
  for a paid / free season move. Language is "worth a look", never
  "recommended": the tool suggests, the manager decides.
- The recommender backtest (`scripts/pickups_backtest.py`,
  docs/pickups_backtest.md): every 2026 team, 616 decision points, scored in
  categories by replaying the real matchup with the lineup re-solved both
  ways. Streaming moves delivered +0.17 categories a matchup, season moves
  +0.98 to +1.06 over 30 days, against the league's own swaps at +0.05 and
  -0.56.
- Projection sources and uploads (docs/projection_sources.md): every
  projection carries its source (ESPN, BBM, or an uploaded set), BBM's are
  gated to the member who fetched them, and a manager can upload his own
  CSV or spreadsheet through `scripts/upload_projections.py` or the
  `/projections/sets` routes, with the column mapping guessed and shown
  before anything is stored (migration 0017).
- The draft room rebuilt as an auction board (docs/draft_room.md,
  docs/draft_night.md for the runbook): dark by default with a light switch,
  the whole room's money and places on one board, hover cards, category
  scarcity and every team's projected totals, working on ESPN, BBM or an
  upload. `scripts/draft_plan.py --top-place N` runs the plan as a what-if
  with a bigger star cap; on 2026-09-18 $80 and $121 both came out worse
  than history's $60.
- In-season pages (docs/in_season_pages.md), served by the API: a team's
  week, its rest of season, and a league index, in the season report's light
  house style. `?today=N` replays any day of a played season.
- Every played season's NBA schedule is stored (`pro_team_games`,
  `scripts/backfill_pro_schedule.py`, 2019-2026), so the recommenders and
  the pages run on a past season and not only on the one the listener
  follows.
- The API is served on the VPS at `http://100.105.64.94:8001`, reachable from
  the tailnet only.
- Local PostgreSQL 16 via Docker Compose (`fcp` and `fcp_test` databases)
- Alembic migrations (one empty initial revision; upgrade to head verified by test)
- Typed config: `DATABASE_URL` and `TEST_DATABASE_URL` required, fail loudly if missing
- The season is derived from the date, not configured. ESPN labels a season by
  the year it ends in and turns over in October, so a schedule follows the
  rollover by itself. `ESPN_SEASON` still pins a one-off backfill.
- Quality gates: pytest, Ruff, mypy (strict)
- `scripts/espn_probe.py` fetches one ESPN league (settings + team list),
  read-only, no persistence. Confirmed against a live ESPN response on
  2026-09-12 (league 3853870, season 2026): auth cookies work, the timeout
  patch holds, and settings + all 14 teams come back populated.

- League structure persisted: `leagues`, `league_seasons`,
  `league_season_categories` (migration 0002).
- Teams, owners, matchup periods, matchups and rosters persisted: `teams`,
  `owners`, `team_owners`, `matchup_periods`, `matchups`, `players`,
  `roster_slots` (migration 0003).
- Season projections and totals persisted: `player_season_stats` (migration
  0010). ESPN's preseason forecast beside what actually happened. Free: both
  arrive on the player cards already fetched.
- Draft persisted: `draft_picks` (migration 0009). Auction prices, keeper
  flags, and who nominated each player. Costs no ESPN request: the draft
  arrives with the league itself.
- Transactions persisted: `transactions`, `transaction_items` (migration
  0008). Waiver claims, pickups and trades, with the players each moved and
  what was bid.
- Per-category matchup detail persisted: `matchup_team_stats` (migration
  0004). Every statistic each team posted in each matchup, covering the nine
  scored categories and the four component stats behind the percentages.
- Player statistics per scoring period persisted: `player_game_stats`
  (migration 0005). A box score line per player per day, global rather than
  league-scoped, keyed on (player, season, scoring period).
- Daily lineups persisted: `daily_lineup_slots` (migration 0006). Where every
  player sat, for every team, on every day, including bench and injured
  reserve. Kept alongside the weekly `roster_slots`, not in place of it.
- `scripts/ingest_league.py` writes one whole season and is safe to re-run.
  Verified against the live league on 2026-09-12: 14 teams, 15 owners,
  22 matchup periods, 157 matchups, 348 players, 4436 roster snapshots and
  4004 matchup statistics, 28215 player game lines (20431 with a stat line)
  and 29100 daily lineup slots (9257 not started), in about two and a half
  minutes. A second run changed no row counts.

### The API surface

Read-only. Paths are keyed on ESPN's identifiers, so a URL is buildable from
a league id and a year rather than from surrogate database ids.

| Route | What it gives |
|---|---|
| `GET /health` | liveness |
| `GET /leagues` | every league, with the seasons held |
| `GET /leagues/{id}/seasons` | seasons for one league |
| `GET /leagues/{id}/seasons/{yr}` | that season's settings and categories |
| `.../teams` | teams with their owners |
| `.../standings` | derived matchup record beside ESPN's category tally |
| `.../periods` | matchup periods and the days each covers |
| `.../matchups` | matchups with per-category detail for both sides |
| `.../teams/{tid}/lineups` | daily slots with that day's production |
| `.../teams/{tid}/bench` | bench points and the worst individual calls |
| `.../teams/{tid}/scorecard` | the season graded in categories a week: players, draft, trades, wire (`app/scoring`) |
| `GET /players` | name search |
| `GET /players/{pid}` | one player |
| `GET /players/{pid}/games` | game log |

Narrative routes, all derived rather than ingested:

| Route | What it gives |
|---|---|
| `.../streaks` | longest winning and losing runs per team |
| `.../category-profiles` | where each team was strong, category by category |
| `.../bench-leaderboard` | which teams left the most on the bench |
| `.../worst-bench-calls` | days a benched player beat every starter |
| `.../notable-matchups` | the season's sweeps and nail-biters |
| `GET /leagues/{id}/owners` | every owner's record across all seasons |
| `GET /leagues/{id}/head-to-head` | every pair of owners who have met |
| `.../transactions` | waivers, pickups and trades, filterable |
| `.../contested-claims` | players several teams bid on, and what winning cost |
| `.../draft` | the draft board in pick order, with auction prices |
| `.../draft-value` | what each pick cost against what the player returned |
| `.../projection-gaps` | who beat their preseason forecast and who did not |

Operational routes:

| Route | What it gives |
|---|---|
| `GET /ingest-runs` | ingest history, newest first |
| `GET /ingest-runs/health` | freshness of the season running now |
| `GET /ingest-runs/health/{season}` | time since the last success, and staleness; `?mode=status` asks about the listener alone |

Listener routes, over what the status passes wrote:

| Route | What it gives |
|---|---|
| `.../events?since=&kinds=&team=` | status changes this season, newest first; `team` narrows to a roster, `team=0` to the wire |
| `GET /players/{pid}/status` | a player's snapshot history, one row per pass |
| `GET /players/{pid}/news` | stored news, newest first |

Recommender routes, built from those rows and never from ESPN; `?today=`
names a scoring period and defaults to the calendar day turned into one:

| Route | What it gives |
|---|---|
| `.../teams/{tid}/pickups/stream` | who to stream this week, the empty days, and whether anything clears the hurdle |
| `.../teams/{tid}/pickups/season` | the best add, swap and two-swap for the rest of the year, the drops, the stashes, the churn guard and what to bid |

Both are a 409, not an empty report, for a season with no stored schedule
or no roster: there is nothing to decide from. A played season with no
listener snapshots uses the historical wire (a player with a line that
period and no lineup row) and says so.

Projection uploads, the one write this API accepts (docs/projection_sources.md):

| Route | What it gives |
|---|---|
| `POST /projections/sets/preview` | read a CSV or spreadsheet, report the guessed mapping and unmatched names, store nothing |
| `POST /projections/sets` | the same, stored as a set; 422 with the reasons when the columns cannot be used |
| `GET /projections/sets`, `.../{id}`, `.../{id}/rows` | stored sets and their per-game lines |

In-season pages, plain HTML over the routes above (docs/in_season_pages.md):

| Route | What it gives |
|---|---|
| `GET /pages/teams/{lid}/{yr}` | every team, ours first, linking to both pages |
| `GET /pages/teams/{lid}/{yr}/{tid}/week` | the streaming report as a page; `?today=` for a past day |
| `GET /pages/teams/{lid}/{yr}/{tid}/season` | the rest-of-season report as a page |
| `.../pages/context` | the day, the period's days and the team names the pages need |

The derivation lives in `app/narratives.py`, not in the routers, because it
is domain logic rather than HTTP. One idea carries most of it: a matchup is
stored once from the home team's point of view, so `matchup_sides` produces
both teams' views and everything else builds on that.

Decisions worth knowing when reading these:

- Byes are excluded from every derived record. A team with no opponent
  neither won nor lost, and counting one would inflate playoff records.
- A tie breaks a streak rather than extending either run, so "won five in a
  row" means five wins.
- Head-to-head counts each meeting once. A win appears on one side, but a tie
  appears on both, so ties are taken from the lower team id. A co-owned team
  gives each of its owners the meeting rather than being dropped.
- Owner routes hang off the league, not a season, because ESPN's owner GUID
  is stable across seasons. That makes an all-time record possible: one owner
  is 101-42-2 with four titles across all eight seasons.

Notes on the design:

- `/standings` is the only place a matchup record exists, because ESPN does
  not report one. It counts winners and excludes byes: an unopposed matchup
  is not a win. It shows the category tally alongside so the two are not
  confused.
- Bounded collections return a plain list. Collections that grow with the
  season return `{items, total, limit, offset}`, so no caller is handed an
  unbounded response by accident.
- `is_scored_category` on matchup statistics comes from the league's category
  list, not from `result`, which is null on both sides of a bye.
- Run it locally with
  `uvicorn app.main:create_app --factory`, then read `/docs`.

## The draft framework

Being built bottom up, because each layer is only trustworthy if the one
under it is. Every piece is a pure function over data already ingested, so
each can be checked against a season that has already happened.

| | state |
|---|---|
| 1. Valuation: projections to comparable value | done |
| 2. Targets: what totals actually win a category here | done |
| 3. Market model: what this league pays for value | done |
| 4. Optimizer: best roster under a budget | done |
| 5. Live draft room: state, remaining pool, re-solve | done, pending the manager's rehearsal; the live page reader is unproven until a mock or the draft on 2026-10-10 |

**Why this differs from the previous attempt.** That one simulated what we
can now measure. It ran Monte Carlo over imagined drafts to guess category
targets and auction prices, because it had no league history to read. We
have eight seasons: 1274 real auction prices, and every category result of
every matchup. Simulation stays useful for a question history cannot answer,
such as a rule change, but it should not be the first resort.

### Optimizer

The objective is expected categories won per week, not total value. A
category won by a hair counts the same as one won by a mile, so piling value
into a category already won is wasted. For each category the probability of
beating an opponent comes from the measured opponent distribution, so it is
this league's, at this size, in this era, and the sum is what gets
maximised.

Two measured facts keep the arithmetic honest. Managers start **98.4%** of
their roster's production, because daily lineups with three utility slots
leave almost nobody with a game on the bench, so a weekly total is just the
sum of everyone rostered. And projections run 13% optimistic on games, so
each season is scaled by measured availability before being spread across
matchup periods.

**The solver is swap improvement from several starts.** A single greedy
start by value per dollar turned out to be a trap: against 2026 it reached
5.95 expected wins while every one of twelve random starts beat it, the best
by 0.30. It also produced a stars-and-scrubs roster, three stars and ten
one-dollar fillers, that implicitly punted turnovers and field goal
percentage. The search now runs from the greedy roster and twelve shuffled
ones, deterministic for a given seed, and lands on a balanced roster
instead.

**It is fast enough to run between two bids.** A full solve at twelve
restarts takes 0.8 seconds on the real 2026 pool; it took twenty. The two
costs were found by profiling, not guessed. Fieldability -- a bipartite
matching -- was being checked on every trial before the score, so 94,658
matchings were paid for and almost all rejected on score a moment later. It
is a constraint, not a filter, and is now checked only for a trial that has
already beaten the incumbent, which cannot change the answer because the
incumbent is only ever a fieldable roster. And the roster's line was being
re-summed for all 92,000 trials when a swap changes it by one subtraction and
one addition. A test holds the fast search to the obvious one across restart
counts and punt sets. It caught a real divergence on the first attempt: the
1e-9 tie-break margin had been dropped when re-arming the incumbent, and with
a category punted -- where ties are commoner -- the search walked to a
different local optimum.

### The room

`app/draft/room.py`. A `DraftState` is a value: budget, places, every
team's picks. `apply` returns the next state and refuses what the rules
refuse -- a duplicate, a bid below the floor, a bid that would strand a
later place. Replaying all 182 picks of the 2026 draft through it refuses
nothing, ends every team at thirteen and under budget, and leaves $4 in the
room, which is what the database says.

Three numbers, three questions. `max_bid` is the rules' ceiling: what a
team holds less a floor bid for every other place it still owes. The
*field ceiling* is the highest max_bid among the other teams; no player can
go for more, whatever anyone thinks. The *bid ceiling* is a judgment: the
highest price at which owning the player still leaves us a roster at least
as good as the best roster without him, found by bisection because a plan
with him can only get worse as his price rises. About eight solves, a few
seconds at full restarts.

The remaining board is repriced continuously by the ratio of discretionary
money left (above the floors still owed) to board value left (what the
players who will still be rostered were priced at, above the floor). It is
exactly one at the open by construction. Replaying 2026 it read 0.86 after
the first round, 0.54 by pick 56 and 0.32 by pick 91: the room overpaid its
stars and the back half of the board went for a third of its price. A player
we own is carried at what we paid, never at what the board said.

**What the ceiling said about 2026, and why it is not a bug.** At pick 53,
with Morant, Markkanen, Turner and Wiggins already bought, it would not pay
$1 for Jalen Johnson -- who went for $40 and was the best pick in the draft.
That is stable across 4, 12 and 24 restarts and a warm start from the
baseline roster, so it is not search noise. Forcing him in displaced Walker
Kessler: the roster was already winning PTS at 94% and REB at 89%, so his
points were nearly worthless to a saturating sum, while losing 8.3 blocks a
week dropped BLK from 0.92 to 0.83. Net -0.035. Sweeping the fourteen best
players still on the board at that moment, every marginal at $1 sat between
-0.04 and +0.04: by then no single player moved that roster by more than a
twenty-fifth of a category a week. The projection could not know what he
became. The ceiling reports `marginal_at_floor` so a reader sees the
magnitude and not only the yes or no.

### The live feed

`scripts/draft_room.py` runs the room. Picks come from one of two places
and typed commands work in both.

Typed: `Jokic, Brighton Bears, 97` is a pick; `me Kawhi 12` is ours; `?
Jokic` is what he is worth to us now; `undo`, `state`, `plan`, `next`.
Names match loosely -- a surname or a typo will do -- and an ambiguous name
is refused with the alternatives rather than guessed: `Jalen` alone gets
"could be Jalen Wilson or Jalen Williams". Anyone off every list we hold is
tracked by name, since the room only needs the money and the place.

Page: `--page URL` opens the draft room in a headless browser with the
ingest's own cookies and reads `document.body.innerText` every two seconds.
`app/draft/feed.py` turns that text into a snapshot -- ticker, pick log,
player on the block with the live offer and who holds it -- and is pure, so
it is tested against text captured from the mock and can be adjusted from a
saved snapshot rather than a live draft. The pick log is authoritative. The
ticker is a cross-check: a team's money falling with no logged pick to
explain it means a pick was missed, and the player who was on the block is
offered as the explanation, applied only on `--trust-money`. When a new
player comes up, his ceiling prints unasked.

`--probe` opens the page, shows the first forty lines and what parsed, and
exits. Run it before the draft. The pick log's exact text was seen rendered
but never captured, so two shapes are accepted; if neither matches on the
night, the probe is where that shows and the parser is where it is fixed.

Two things are known and not proven. The page reader has only ever seen a
mock, and a mock is a clone of this league but not this league. And there
are no 2027 projections yet; `--pool-season 2026 --pool-kind projected`
stands last year's in, and the room says so on every start, in capitals.

### What the board gets wrong, measured

`scripts/board_calibration.py` (written on the VPS, reviewed and merged)
builds the board exactly as the room does for every season and joins it to
what the league actually paid: eight auctions, about 1,900 priced picks.
2020 is excluded from pooled figures because COVID truncated its projected
games to 10-90 and the board priced stars at $5-11.

At 14 teams the board is light by 52% on ranks 1-5, 35% on 6-15 and 29% on
16-30, on the money at 31-60, and heavy by about 30% below that. The two
14-team seasons agree closely (top-five ratios 1.59 and 1.45), so it is a
stable curve, not a 2026 effect. Wembanyama at $61 against $100 is an
ordinary member of it. The board sums to the pot by construction, so "light
at the top" and "heavy at the bottom" are one finding: the curve is
compressed.

Two things stop this being a one-line fix. The board's *order* inside the
top 60 is only moderately right -- Spearman 0.53 to 0.82 by season -- so a
curve correction addresses perhaps half the error and the rest is
projection quality. And the curve is not one shape: the top-five ratio runs
from 0.99 in 2019 to 1.59 in 2024, and in 2026 ranks 21-30 sat above 11-20.
A tier multiplier fitted across seasons would help on average at 14 teams
and be wrong by about 0.2 at the top in any given year. Whether to fit one
is an open decision, recorded here rather than made quietly.

The largest overpays looked like a second cause, and were two.
`scripts/injury_at_draft.py` (written on the VPS, reviewed and merged)
classes every drafted player by games actually played -- zero in the first
fourteen scoring periods means the room knew he was out -- and attributes
the board's error to each class. Known-hurt players are 5.3% of the
board's absolute error at 14 teams; HEALTHY players are 88.7%. Removing
the known-hurt from the tier calibration moves no bucket by more than
0.03. So injury explains neither the light top nor the heavy bottom: the
compression is the market, and the tier-curve question stands on its own.
An injury-aware board is still worth having -- $85 of 2026's budget and
$69 of 2025's went to players the room already knew were out -- but it is
a 5% fix, not the fix. `daily_lineup_slots.injury_status` is NOT how to
build it: one ingest-time snapshot, not a time series.

The other half of the overpay list was our own data. See the next section.

### 2023's projections are not projections

Six of the ten largest overpays were 2023 players the room saw play all
year -- Brook Lopez $31 to $4 and 78 games -- with projected games of 37
to 41. Every 2023 projected line is like that: median 39 games against 63
to 74 in every real preseason year, with per-game rates untouched. Each
player's projected-to-actual games ratio sits at 0.59 with an interquartile
range of 0.18, tighter than any true forecast year (0.27 to 0.34), and his
projected per-game rate matches his actual at 1.005 with half the spread of
any other season. That is a rest-of-season projection captured on one date
about 41% of the way through the year, which ESPN then kept as *the*
projection. It is 60% hindsight. The 2023 board was built on it, the
16-team calibration figures were built on it -- which is why they looked
milder, not because 16 teams is gentler -- and the projection-gaps view
would report every 2023 player beating a forecast made of his own results.

`app/draft/projections.py` is now the registry of seasons whose stored
projections are not a forecast, with the reason for each (2020 and 2023),
and a rule, `looks_like_snapshot`, that flags the 2023 shape from the
numbers alone so a future season with the same problem fails a test rather
than a glance. The projection loader refuses those seasons unless told it
is studying them; availability derives its exclusions from the registry
rather than from the accident that 2023's snapshot games all fell under a
threshold; the projection-gaps view answers 422 with the reason; both
calibration scripts exclude and label them. The model's docstring no longer
promises a preseason forecast it cannot keep.

What this does to the earlier findings: the 14-team tier figures are
unchanged to the digit, because 2023 was never in them. The 16-team row is
gone, because its only season was 2023. The tier-curve decision is exactly
where it was, on cleaner evidence.

### The tier curve, fitted

Decided and shipped on 2026-09-14. `app/draft/tiers.py` holds a multiplier
per rank tier -- 1.27, 1.32, 1.13, 0.89, 0.71, 0.87 across 1-5, 6-15,
16-30, 31-60, 61-100, 101+ -- applied to the above-floor part of each
board price and rescaled so the board still sums to the pot. Nothing it
does can create or destroy money, and nobody drops below the floor; both
are tested.

Fitted on every usable season before 2026 and tested on 2026 as if unseen:
mean absolute error over drafted players 11.3 to 7.5, top-five ratio 1.45
to 1.01. Wembanyama $61 becomes $79 against the $100 paid; Jokic $70
becomes $90 against $91. Leave-one-season-out it beats the raw board in
five of six years; 2019, when the room paid the board almost exactly, is
the year it over-corrects. Fitting on 14-team seasons alone was tried and
was worse held out. Pooling on a normalised rank axis (rank over players
rostered) was tried and added nothing over plain rank.

The draft room applies it by default and says so on start; `--no-tier-curve`
prices from value alone. `scripts/fit_tier_curve.py` refits on demand and
prints the constant to paste, with the held-out and leave-one-out tables
beside it, so a refit after the 2027 draft is a run, a paste and a commit.

The bet, plainly: the room keeps paying the star premium it has paid in
four of the last five drafts. If it does not, this board bids too high in
round one. The in-draft repricing then corrects from a high start instead
of a low one, which is the better side to be wrong on when the stars are
the part of the draft you cannot get back.

### The deployment gap, and the guard

The scheduled ingest on the VPS runs whatever is checked out at
/opt/fcp-core against whatever schema the database has; the checkout is
pulled by hand and the migration is a separate step. On 2026-09-13 the
checkout reached main -- with the ORM's new `auction_budget` column --
before the database was migrated. Nothing failed only because the nightly
run had already happened and the migration was applied before the next one.
`scheduled_ingest.sh` now refuses to run when `alembic current` is not at
head, saying so in capitals in its log, and a test against a scratch
database one revision behind proves it fires. The right deploy is still
`git pull && alembic upgrade head`; the guard turns forgetting the second
half into a loud failure rather than a quiet wrong write.

### Is top-heavy worth it, and does league size change that?

Asked because the model's "let the stars go, buy depth" is what the
objective says, and the league's own history is the check on it.
`scripts/top_heavy.py` (written on the VPS, reviewed and merged) scores
all 98 team-seasons -- 2023 included, since no projections are involved and
it is the only 16-team year -- on two measures kept apart: STRATEGY, the
share of the $200 a manager put on his three dearest picks, and EXECUTION,
the share of the team's started production those three actually delivered.

Balanced drafts win the regular season at every league size. Pooled,
the balanced quartile wins categories at 0.536 and makes the playoffs 68%
of the time; the top-heavy quartile 0.477 and 48%. At 14 teams it is 0.535
against 0.477, at 16 teams 0.540 against 0.423 with the top-heavy four
finishing eleventh on average. Sixty percent of teams that chose top-heavy
did not get top-heavy production out of it, and here is the line that
settles it: when the plan *worked*, those teams won categories at 0.484,
against 0.471 when it did not. Getting the stars you paid for barely
helped. Execution concentration on its own predicts nothing (0.499, 0.503,
0.474, 0.500 across its quartiles). Top-heavy's best season (0.623) is no
better than balanced's best (0.642); only its worst is worse (0.185
against 0.401). It does not raise the ceiling. It lowers the floor.

Titles are another matter, and honestly so: 2026's champion spent 83.5%
of its money on three players, while the best regular-season team spent
46%. A playoff week is one draw from the distribution, and a wide
distribution wins its share of single draws. The record says top-heavy
wins titles at about the rate balanced does (12% against 16%) while
missing the playoffs far more often.

On league size, the study's own replacement level -- the 25th percentile
of started players -- flattened the effect by construction, so it was
recomputed here with the natural one, the (teams x 13)th best player by
started production. That falls as the league grows, from about 37 a week
at 10 teams to 27 at 16, and a star's surplus over it rises from about 90
a week to 110-127. Stars *are* worth more in a thinner pool. And top-heavy
drafters still lose at 14 and 16 teams, because the surplus has to be
bought and the room charges half again for it. The two halves do not
contradict: the star is worth more, and he is priced further above what
he is worth.

So the model's position stands, now on history rather than on the
objective alone: sell the premium, buy depth, and take the star only when
he comes inside the ceiling. Ninety-eight team-seasons across four sizes
is thin, and the 16-team row is one year. What would change the reading:
two 15-team seasons in which the balanced quartile stops winning.

### What "worth" means, and the redraft that tested it

The room's ceiling for a player is the highest price at which the best
roster we can still complete with him is at least as good -- expected
categories won per week against the measured opponent distribution -- as
the best we can complete without him. It is not what he produces; it is
what he produces *relative to what the same money buys elsewhere at the
board's prices*. Three things can make it wrong: the objective, the
projections, and the prices of the alternatives.

`scripts/redraft.py` replays a real draft in its real order with our team
bidding on the ceiling, everyone else paying what they paid, and scores
the roster we would have ended with on what actually happened -- real
game lines, real weekly opponents, nine categories, drafted rosters only.
No hindsight: the season's own preseason projections, the tier curve
fitted without it, opponent distributions from seasons strictly before it
(`before=`, added to `category_distributions` for this), availability
measured before it.

**Replaying 2026, the room's roster goes 81-88 against our real
opponents. The roster we actually drafted goes 99-69.** The room bought
Curry, LeBron, Durant, Draymond, Brook Lopez and Paul George -- every
ageing star the market was discounting, at a dollar over the discount --
and passed on Jalen Johnson at $40 with a ceiling of $12. Its thirteen
delivered 78% of their projection; the thirteen we drafted delivered 89%
and included the season's two largest overperformers (Johnson 1.33, Kawhi
1.30). The room bought 2,300 more projected production and got 900 less.

What that is and is not. A hindsight variant, drafting on the season's
actual totals, still finished 92-78 -- but scored on realised weekly lines
the objective rated that roster and ours near-equal (4.62 to 4.56), and
seven wins over 171 categories is one standard deviation of noise. The
hindsight run also leans on season totals that include the 20-27% of
every player's production landing after the fantasy regular season, which
is uniform across rosters and so shifts nothing between them. The
objective is therefore *not shown* to be broken. The real-world failure is
18 wins over 171, nearly three sigma, and it sits in the inputs:
projections 20-40% too high on the old stars the room bought, one
availability factor for everyone, and the market's discount thrown away.

Three fixes were tested and ruled out here, so nobody retries them.
Roster weekly variance does not explain it: the room's roster was no more
volatile week to week than ours, and adding roster variance to P(win)
moved neither rating by more than 0.05. Capping bids at 1.15x the market
price made it worse (67-103): a bargain by market price is not a good
player. Prior-year games played does not predict the shortfall: across
all usable seasons, players with 70+ games the year before delivered 0.82
of projection and players with under 40 delivered 0.77, and in 2026 the
order reversed.

What is left standing is age, which our data lacks and which ESPN's
public athlete endpoint (`sports.core.api.espn.com/v2/sports/basketball/
leagues/nba/athletes/{id}`) supplies as `dateOfBirth` for the same id we
hold, no login. The same fantasy card also carries ESPN's own pre-draft
auction value, which we never captured. Both are the next inputs to
measure, and the redraft is now the test either has to pass.

**Were ESPN's projections the problem? Partly, and then something else.**
The manager drafts on Basketball Monster's projections, not ESPN's, and
supplied the 2026 export. `app/draft/bbm.py` reads it into the same
`PlayerProjection` the room consumes, matching names to our ids (422 of
593; the rest are rookies and fringe our tables have never seen) and
taking eligibility from ESPN's line where we hold one and from BBM's
position otherwise, which puts 140 players on the board ESPN never
projected -- Jarrett Allen among them. BBM's games already price
availability: realised over projected runs 0.96 against ESPN's 0.88, with
Curry at 56 not 72 and Morant at 48 not 67, so a BBM board takes an
availability factor of one, not 0.881, or it discounts twice.

Replaying 2026 on BBM's projections the room went **58-110**. Worse than
on ESPN's. That was too bad to be a finding, and it was not one: it was
the search. In an empty $200 room Jalen Johnson's marginal at a $1 bid
read -0.096 at two restarts and +0.11 to +0.18 at four, eight, sixteen
and warm-started. A ceiling compared two *independent* local searches,
and at the two restarts the replay ran for speed the noise between them
(about 0.1 to 0.3 wins) exceeded most players' true marginal. The room
was passing on anyone whose signal was smaller than the noise and buying
whoever the noise broke upward for -- LeBron, twice. `optimize` now takes
warm-start rosters and the ceiling seeds every with-him search from the
without-him roster, so the two are neighbours rather than strangers; it
can only raise the with-him side, so a ceiling now errs generous rather
than refusing a player worth having. Tested. Johnson's ceiling at pick 53
rose from $11 to $25.

With the noise gone, the four runs against the roster actually drafted
(99-69):

    ESPN projections, noisy ceilings     81-88
    ESPN projections, warm-started       78-90
    BBM projections, noisy ceilings      58-110
    BBM projections, warm-started        71-99

The projections are not the difference. The pattern across every run is
the same: the room spends about $150 on the first two or three stars
nominated whose ceiling clears the price -- Wembanyama $101, Jackson $30,
Ball $21 -- and fills ten places with $2 players. A ceiling-only bidder
has no budget plan, so it builds stars-and-scrubs by construction, which
is the strategy the league's own history says loses. The ceiling answers
"is he worth this much given what the rest of my money buys at board
prices"; it never asks whether buying him now leaves a plan the rest of
the money can execute. That is the next design step: a ceiling measured
against a plan -- the pre-draft optimal roster's allocation -- rather than
against a board, so a star early is bought only if the plan had a star
there at that price. It is not built, and nothing in the room should be
trusted as a bid until it is and the redraft passes.

The 2027 BBM export carries an age for every one of its 572 players,
injury risk, and ESPN's and Yahoo's own dollar values for 2027. That is
every input the room needs for the draft, available now, and it settles
the projection watcher: there is nothing to wait for.

Until then the room's ceilings are a *reasoned* number and not a
*verified* one, and the readout should be read with that in mind: it is
best at saying what a player is worth relative to the board, and worst
exactly where the board is wrong about a player -- or where the money
already spent has made the board irrelevant.

### Drafting to a plan, and what the wider replay says

**The optimizer's own plan is stars-and-scrubs.** On BBM's 2026 projections
the best roster from the empty room was $103 on Wembanyama and ten $1
players, so capping bids to it changed nothing. Two things ruled out as the
cause: cheap players do not under-deliver their projections (every BBM
price tier delivered 0.90-1.11), and the board's low end is not cheaper
than the room actually paid.

**The room now drafts to the league's winning shape.** `app/draft/shape.py`
reads how the top third of category teams spent, largest place first:
`[55, 42, 28, 21, 15, 12, 9, 6, 4, 3, 2, 2, 1]`. `Allocation` in
`app/draft/room.py` turns it into places; each of our buys uses the
cheapest place that covers it, and the rest is refitted to the money we
have left. The shape is a constraint *inside* the optimizer
(`within_shape`), not just a cap on the bid, so the rosters with and
without a player are both rosters the plan allows. `--plan
history|optimizer|none` on both the room and the redraft.

**The redraft now replays as every team** (`--all-teams`, parallel),
comparing the room's 13 against each manager's own 13, both held all
season. The results:

    ESPN projections, 6 seasons, no plan      beat 33 of 72, -1.2 cats/season (se 2.0)
    ESPN, balanced plan (2021-26)             vs no plan by season: +9.4 +7.2 +5.8 -0.5 -33.9
    BBM 2026, no plan                         beat 8 of 14, +0.6
    BBM 2026, balanced plan                   beat 11 of 14, +9.6 (se 4.2)
    Through The Wire, every variant           2026 -17 to -56; 2025 -11; 2024 +7 / -1

Read with care: every replay in a season shares one board and buys the
same players, so the effective sample is seasons, not teams. ESPN's 2026
plan collapse is Sabonis, Kessler and LaVine bought for nearly every team,
in the price band where ESPN's 2026 projections delivered 0.72-0.77. Best
reading: the room drafts like an average manager in this league, BBM with
the plan perhaps better, and it has not beaten this manager's drafts.

**Is the manager's edge skill?** Three seasons under this owner: dollar-
weighted delivery ranked 7th of 14, 3rd of 12, 5th of 14 -- mean
percentile 0.68 where luck alone gives 0.50 +/- 0.17. Conviction buys
(paid well over board): 7, delivering 1.14 of projection against 0.99 for
the rest of the league, but a hit rate identical to the league's (43%);
the average is Jalen Johnson (1.67) and Mobley (1.40) against Morant
(0.29). Discount swings: indistinguishable from the league. Consistent
with luck leaning good; not yet evidence of an edge.

**2027 BBM in the room.** `--bbm` loads all 510 rows. Names match strictly
(`match_player`: same name without punctuation or suffix, or same surname
with a short first name), because the feed's loose matcher had put Caleb
Wilson on Jalen Wilson. Unmatched rows -- the rookie class -- go on the
board under a stable negative id. Each ceiling shows age, games, injury
risk, BBM $, ESPN and Yahoo average auction $.

### The draft service

The room is a value; draft day needs a process. `app/draft/session.py`
holds a live draft: state behind a lock, every accepted pick and undo
appended to a JSON-lines log and replayed on start (a crash or refresh
mid-auction loses nothing), and ceilings computed in worker processes for
the player on the block first and then the twelve likeliest nominations by
market price, keyed on the exact picks they were computed against so a
stale answer is never shown. `app/draft/service.py` puts it behind a
localhost FastAPI app -- state, server-sent events, picks, undo, block,
player cards, plan -- with the page reader on its own thread.
`scripts/draft_service.py` runs it. It runs on the manager's machine, not
the VPS: the page reader needs the ESPN login and the VPS API is
tailnet-only and read-only by design.

Measured on the 2027 BBM pool: state in 60 ms, a pick in 40 ms, the eight
most expensive players' ceilings ready about 25 seconds after start on two
workers, and a nominated player's ceiling 13 seconds after a pick while
precomputations were running. A nomination gives ninety. Three workers is
the default. Room loading moved to `app/draft/live.py`, shared with the
typed room.

### The draft screen, and rehearsing on 2026

`app/draft/static/draft.html` is one self-contained page the service
serves at `/`: the block card (market price, our ceiling, plan cap, what we
can bid, a verdict, BBM total and per-game value, the injury-discount flag,
age and injury risk), pick entry with name autocomplete, undo, our roster
and open plan places, the board with ceilings as they land, every team's
money, and the pick log. It redraws from the event stream.

`--rehearse 2026` replays the league's real 2026 auction into the 2027 room
(`app/draft/rehearsal.py`): each nomination sits on the block for
`--seconds`, and unless we enter him as ours he sells to his real buyer at
his real price. Verified in the browser on 2026-09-14: nominations advance
and sell, buying from the card applies and refits the plan, keyboard entry
for another team works, pause holds the clock while ceilings land. The
BBM is a paid membership and nobody has permission to republish its
numbers, so the exports, the stored rows and everything derived from them per
player stay private to the account that fetched them; `docs/projection_sources.md`
records what that rules out and the upload path other people would need.

**Every projection now names its source** (2026-09-18). A `PlayerProjection`
carries "espn" (the default), "bbm" or "upload:<set id>";
`app/projections/sources.py` says which is gated and answers
`may_show(source, viewer_owns_source)`, which the draft plan page and the
draft screen's card both call before rendering. The ownership half is a
constant `True` until accounts exist, which is the point: one function has to
learn the answer, and nothing else moves. The room's numbers are unchanged.

**Anyone without a BBM membership brings his own numbers.**
`scripts/upload_projections.py --season 2027 --file proj.csv --name "..."`
reads a CSV, .xlsx or .xls, maps the header row by synonyms instead of
demanding a template, measures per-game-or-totals from the numbers, matches
names with the room's strict matcher and prints all of it; `--commit` stores
it (`projection_sets`, `projection_rows`, migration 0017) and
`--projection-set <id>` drafts on it. A file with a percentage and no
attempts is refused: a roster's FG% is made over attempts and cannot be
rebuilt from a rate. Checked against BBM's own 115-column export, which
caught the obvious trap -- `tov` matching its derived turnover *value* column
rather than `to/g`.

BBM exports live in `data/bbm/` inside the project (git-ignored): a process
the app launches cannot read `~/Documents` under macOS privacy rules, and
hung silently trying.

`scripts/bbm_pull.py` refreshes them: it logs in with `BBM_USERNAME` /
`BBM_PASSWORD` from `.env` (on the Mac and the VPS), downloads the total and
per-game exports with all 115 columns, checks each loads, replaces
`BBM_Projections_<season>_{total,pergame}.xls`, and prints who was added,
dropped, or moved $3+ in Leag$. First run 2026-09-16: 515 players, Josh Hart
$9.3 to $5.1, Jalen Williams $26.2 to $29.9. Re-run `scripts/draft_plan.py`
after a pull.

On the VPS it runs daily at 09:30 UTC with `--store` (`deploy/fcp-core-bbm.*`),
which also keeps both exports in the database as versions of each player's
row (`bbm_captures`, `bbm_projections`; `app.draft.bbm_store.as_of` reads a
date back).

The pull guards against BBM settings made in the browser. Leag$ is only
exported while the punt panel's `cat_25` box is ticked, so the pull ticks it.
And a settings change on 2026-09-16 raised every projected game count (median
+10; Wembanyama 61 to 68) with Assume Good Health still off; a plan built on
it scored 6.19 expected wins against 5.53, all of it healthier players than
BBM's own games expect. The pull now refuses an export without Leag$ or with
the median player's games moved by two or more, and keeps the old files
(`--accept-games` overrides).

### How good "expected to go for" is

Scored on 1,274 drafted players against what this league actually paid
(`scripts/price_scorecard.py`). ESPN's player cards still carry the average
auction price across ESPN leagues for 2019-2025 (zeroed for 2026), and
ESPN's own dollar value from 2023 -- which for 2026 looks refreshed after
the season, so it is not used. Over the 712 players with both an ESPN
average and a board price, each season held out of its own fit:

    ESPN average price alone      misses $6.14 a player, runs $4.40 low
    our board alone               misses $6.71
    half and half, fitted         misses $5.42, unbiased, 59% within $5

ESPN's average runs low because it comes from ten- and twelve-team
leagues; the board misses the reputations the room pays for. The blend
is now the room's going price. What it still misses is stars: $11 a
player at $40 and up. BBM's generic dollars for 2026 missed $7.20.

### Opponents for a fifteen-team league

The ceilings measure every player against the league's weekly opponent
distribution, and 2027 is fifteen teams, a size never played. It was
borrowing the fourteen-team seasons raw, and extrapolating the NBA-wide
shooting trend onto fantasy percentages. Fitting each counting category's
weekly mean on league size and season together (2020 excluded), every extra
team lowers weekly totals 3.8-4.9%, the same in every category. Rates were
the other error: opponent FG% came out .486, above any season the league has
posted (.462-.483), because the league's own series is flat (+0.0006 a year)
while the trend it borrowed is measured on NBA starters.

`app/draft/targets.py` now scales a borrowed counting distribution by the
fitted size effect (`size_scale`) and reads rates from the three most recent
seasons with no era scaling. 2027's opponent came down 4-5% in counting
categories and to .479 FG%, .796 FT%. Measured on the 2027 BBM pool it moved
ceilings for bigs, not stars: Mobley $30 to $16, Gobert $16 to $9.

Two things the check exposed and did not change. Ceilings snap to the
spending plan's places ($60, $46, $30, $16, $9): with the plan inside the
optimizer, a player's ceiling is largely which place he qualifies for. And
the empty-room plan expects about 6.35 categories a week, above what any
team in the league has averaged; the absolute number is optimistic even
where the comparisons between players hold.

### Pricing the room like an auction

Two faults, found because the plan's model roster had Doncic at $52. First,
the optimizer planned at board prices -- valuations -- while the room pays
the going price; its "target roster" was buying players it would never get.
Second, the fitted going price was not an auction: it priced the top 195 of
the 2027 pool at $2,833 of a $3,000 room, and the missing money was the
stars' known shortfall.

The going price (`app/draft/live.py`) now ranks players by the ESPN-average /
board blend and shares out the money this league actually spends: 24.2% of
all places at $1, 7.6% at $2 (17-32% by season), 98.8% of the budget used,
the rest in proportion to the blend, recomputed from the live state. Scored
on 712 drafted players with each season's parameters from the others:

    fitted blend                 misses $5.42; $40+ stars $5.40 low; $1-2 $3.00
    sized to the room            misses $5.14; $40+ stars $2.60 low; $1-2 $1.70

Candidates now carry the going price, so ceilings and plans are bought at
what players cost; the board survives as `Room.board` for the blend and for
display. For 2027: 47 players at $1 and 15 at $2 in the top 195, Doncic $77
(the room paid $69-91 in five of his six drafts), Wembanyama $118 -- above
the league record of $101, flagged on the plan as a range. The model's
unrestricted roster stopped punting free throws once it paid real prices.

### Weekly lines were overstated by a quarter

A projection is a season total and categories are won in a week, so the
optimizer divides one into the other. It divided by the league's regular-
season matchup periods (19). But production keeps coming through the fantasy
playoffs: league-wide points over a median seven-day week come to 23.2-23.8
in every season measured, so every roster's weekly line was 23% too high.
That is most of why the optimizer expected six and more categories a week.
It surfaced because re-ingesting 2027 (to pick up the centre limit, which the
commissioner has since set to 3 -- ESPN counts it by primary position, so
C/PF counts and PF/C does not) also picked up ESPN's unfinished 2027 schedule
of 15 regular-season periods, which would have overstated lines by 56%.

`pool.effective_weeks` now measures the divisor from game logs (23.8 across
2019-2026 excluding 2020; `before=` for replays), and the room and the
redraft use it. Every redraft figure recorded above predates this and is
now stale.

Consequences, measured the same day. A typical 2027 roster now scores 4.37
expected categories a week, where an average team should sit; before it was
about 6. On BBM's 2026 projections the model's score for each drafted roster
correlates 0.78 with what that roster won held all season (0.74 before; ESPN
projections 0.36). The model's best empty-room roster scores 5.09 -- top 5%
of random rosters -- and it is a three-category punt: FT% 0%, 3PM 2%, PTS
20%, with FG% 99%, REB 92%, BLK 84%, STL 83%. The league's evidence for
balance is about spreading money, which this roster does; whether conceding
categories wins in this league has not been tested.

### Conceding a category now costs what the league measured

`scripts/punt_builds.py` (docs/punt_builds.md) tested punt builds on the
league's history with strength-matched comparisons: rosters drafted 1.5 SD
behind the league in a category won 4.1% fewer categories than rosters of
the same projected strength (season bootstrap -5.9% to -2.4%), and the most
specialized teams won 12 points fewer one-category weeks (-20 to -2). Summed
win probabilities cannot see either effect, which is why the optimizer kept
building triple punts.

`app/draft/optimizer.py` now charges `CONCEDE_PENALTY` (0.37 categories a
week, 4.1% of nine) once per roster, scaled by how far conceded categories
sit below 25%; a category the manager chooses to punt is exempt. On BBM 2026
the model's score for each drafted roster correlates 0.79 with its held
result (0.78 without the penalty). The empty-room roster no longer concedes
anything -- Durant $53, Kawhi $40, Clingan $24, Alexander-Walker $23,
McDaniels, Edgecombe, Reid -- at 5.06, above every evenly split league
roster (3.94-4.86). Its weakest categories are AST 26% and PTS 37%.

The $60 plan cap was checked against the valuation changes: it comes from
spending history, not player values, and the history supports it. Winning
category teams' most expensive player averaged $54 ($52 in 14-16 team
seasons); the best regular-season team in each season never had a player
above $70.

### The redraft, re-run after the pricing and weekly-line fixes

Every team, replayed with the room pricing the pool as it now prices the
draft (size_to_room with ESPN's average for the season), weekly lines over
23.8 effective weeks, the concede penalty and the league's winning spending
shape. Room's 13 against each team's own 13, both held all season:

    ESPN projections, 2021-2026    beat 35 of 62, +2.2 categories (se 2.0)
      by season                    +5.6, -13.2, +9.4, +3.3, +5.0
      before today's fixes         beat 29 of 62, -1.5; 2026 was -21.2
    BBM projections, 2026          beat 8 of 14, +8.3 (se 4.6); before +9.6
    Through The Wire               2026 -28 (ESPN) / -21 (BBM); 2025 -3; 2024 +1

The room now drafts a little better than the league's average manager and
still not as well as this one. 2022 is a named failure: the room bought the
"bargains" the market had discounted for reasons the projections did not
carry -- Kyrie Irving at $6 (vaccine mandate), Jonathan Isaac at $6 (missed
the season), Porter Jr., Ball and George (long absences). A price far below
projected value is usually information. The plan's "bid up to" already takes
the lower of our ceiling and BBM's value, and BBM prices availability.

### The local search is at the optimum

`scripts/milp_gap.py` (docs/milp_gap.md) solves the draft optimizer's problem
exactly as a mixed-integer program with HiGHS: lineup assignment, the centre
limit, the spending shape through Hall's condition on the sorted places (13
rows instead of per-place assignment variables), win probabilities
piecewise-linear over 60 segments, FG% and FT% by a threshold ladder on makes
minus rate times attempts, and the capped concede penalty. On the 2027 BBM
room it proved optimality in 16.5 minutes.

Inside the model the optimum is 5.0736 and the local search's roster scores
5.0733 -- 0.0003 apart, well inside the model's own resolution (percentages
floored to a 0.002 grid). Re-scored with the real scorer the local roster is
the better one (5.0868 against 5.0779). The rosters share 12 of 13 players.
Fourteen of fifteen local runs across restarts 4, 12 and 48 land on the same
score to four decimals, at about a second a solve at restarts 4. No change:
the local search stays, for the plan and for live ceilings.

A first attempt on the VPS (branch `milp-gap`, 821 lines) never became
feasible; this rewrite replaced it.

### The opponent, checked from the other direction

ESPN moved the 2027 league again on 2026-09-16, back to sixteen teams with
the draft on 2026-10-10; the nightly `--upcoming` refresh caught it on the
VPS and the local database was brought level by hand. At sixteen the
opponent basis is no longer a borrow: it is 2023, the one sixteen-team
season, brought forward where the game drifts. That is one season, so
`scripts/opponent_check.py` (docs/opponent_check.md) estimates the same
number without history: a draft dealt from the 2027 BBM board, 2000 times,
sixteen rosters of thirteen at going prices, each team's week the sum of its
projected lines at the started share; between-team spread from the
simulation, within-team spread (three-game weeks, rest) from the league's
own week-to-week variance, added in quadrature.

The three routes agree on the means. 2023 brought forward, the fourteen-team
seasons size-scaled, and the simulation land within 5% on points, rebounds,
assists, threes and turnovers and within 10% on steals and blocks, with the
history inside the bracket the simulation draws (raw, and on a
games-actually-played basis, since a real opponent streams round the
absences a projection prices). Backtested on 2026 from its own export the
simulation reproduces the season within 4% in five of seven counting
categories once games are put on that basis; the export is not quite a
preseason file, so the factor is a bracket rather than a calibration. No
change to the basis.

Two things it exposed. 2023's between-team spread was the widest on record
(78 points a week against 36-52 in every fourteen-team season, and 39 in the
simulation at sixteen teams), so the room's opponent is wider than either
alternative and flatters a strong roster by about a fifth of a category a
week (5.52 expected wins against 5.32-5.35). And the projected pool shoots
.489 from the field where the league posts .477-.483, in both 2026 and 2027:
the opponent's FG% is read from history but the roster's own from the
projections, so every roster is credited about 0.15 of a category a week in
FG% it will not win. Recorded, not yet priced.

### Hit these targets, maximise the rest

The old "hit these targets, maximise that" tool, rebuilt on the exact
solver. `app/draft/exact.py` now holds the MILP that `scripts/milp_gap.py`
was built around, and takes what the local search cannot: a floor on any
category's win probability. The floors are soft. Each is a slack variable
charged `TARGET_WEIGHT` (ten categories a week per unit of probability) in
the objective, so a set of targets the board cannot meet comes back as the
roster that misses them by the least, with the shortfall reported, and never
as "infeasible". The only hard constraints are the roster's rules and the
spending shape, which is dropped when the locked players alone break it,
as the local search drops it. Soft on purpose: a roster that misses a floor
by a hair rather than concede another category outright is the better
roster, and a hard floor would refuse it.

`scripts/soft_targets.py` is the tool. A target is `REB=0.65` (win rebounds
65% of weeks), `REB>=210` or `TO<=60` (a weekly total, turned into the
probability it has against the opponent), or `FG%=0.55`. `--maximize wins`
is the ordinary objective over everything else; `--maximize PTS,AST` counts
only those. `--lock Name=price` and `--exclude Name` work as in the room, and
the report shows every target against what the roster posts, the roster,
every category beside the best roster with no targets, and what the targets
cost in expected wins. If the solver returns nothing inside the time limit
the local search's roster is reported instead, marked as such.

First run, asking the balanced plan to reach 50% in points and assists, its
two weakest categories: both met (0.52 and 0.59) for 0.036 expected wins a
week, by swapping the plan's Maxey and Buzelis for Durant and Dyson Daniels
and giving back rebounds, turnovers and FG%. Ten minutes at a 3.6% gap, so
not proven; a better roster, if one exists, scores at most 0.19 more. Nine
brute-forced tests hold the model to the real scorer on a toy pool: the
optimum, a reachable floor met at least cost, an unreachable one missed by
the least, locks, exclusions, punts, the shape and its dropping.

### What the mock draft taught us

Run 2026-09-13 against a mock cloned from this league. The read API does
not carry a draft while it happens. `mDraftDetail` reports `inProgress:
true` and 195 empty pick slots, and stays that way: 1,347 polls across an
entire auction, zero picks, `mRoster` empty on every team. The uncached
headers and pre-allocated slots that made it look pollable were a false
lead. The draft client never polls either -- four requests to the fantasy
API in the whole session -- because the board arrives over a websocket.
When the mock ended, the league was deleted outright; mocks never persist.

The page itself carries more than the API would have. Read from the DOM
mid-draft: every team's remaining budget, the player on the block, the
live high bid and who made it, every completed pick with its price, and
ESPN's own pre-draft valuation. Remaining budgets are the most valuable
input a room can have and the REST view would never have provided them even
had it worked, since it records only completed picks. So the live feed is
the browser, with typed entry as the override; the REST view remains how a
finished draft is ingested, which is how all 182 picks of 2026 got here.

`scripts/draft_watch.py` is the watcher that established this. It stays,
because the next real draft is the first chance to see whether a live
league behaves like a mock.

Two bugs the restarts exposed. A shuffled start could take an expensive
player early, find nothing affordable later, and leave the roster short,
which then won the comparison on the strength of one star. Short rosters are
now rejected. And the floor reservation used a nominal dollar a slot when the
cheapest man left cost three, which is how a roster ends up short with money
unspent. It now reserves what the cheapest remaining players actually cost.

**Backtested with no hindsight.** A 2026 roster built from preseason
projections, then scored on what those players actually did against what
opponents actually posted:

| | expected | realised |
|---|---|---|
| single greedy start | 5.95 | 4.61 |
| multi-start | 6.28 | **5.85** |
| real league best | | 5.58 |
| real league median | | 4.47 |

Built with zero knowledge of the season, the multi-start roster would have
been the best team in the league. The gap from expected to realised is
projection error, which is a property of the projections and not the
solver.

**On concentration, one season is not evidence.** Capping any single player
at a quarter of the budget realised 4.88 against 5.85 uncapped. But the
entire gap is Anthony Davis at $48 playing 20 games, and the availability
analysis predicts precisely this: injuries do not persist and do not vary by
tier, so spreading the money buys a different lottery ticket, not a safer
one. Recorded as inconclusive rather than as a result.

**Roster rules come from ESPN, per season.** They are not constants and
this league has already changed them:

| season | centre cap | injured reserve |
|---|---|---|
| 2019 to 2024 | 4 | 0 |
| 2025, 2026 | 3 | 0 |
| 2027 | 4 | **1** |

The starting lineup, bench size, IR places and position caps are all read
from `rosterSettings` and stored on `league_seasons`. The lineup had been
inferred by counting slots in box scores, which happened to be right, and
the centre cap had been missed entirely. Position limits count **primary
position**: a power forward eligible at centre is not a centre, so counting
eligibility would refuse legal rosters.

**Injured reserve changes the injury argument for 2027.** Every season on
record had none, which is what the availability analysis assumed. From 2027
there is one place, so a hurt player can be stashed rather than occupying a
starting-eligible slot. It does not change the finding that availability is
unpredictable; it does soften the cost of being wrong, and it is stored so
the optimizer can use it rather than assume it.

**Positional eligibility is a hard constraint.** A roster is only a roster
if its players can cover every starting slot at once, and this league's
lineup is PG, SG, SF, PF, C, G, F and three UT. Eligibility comes from the
player card, one list per player per season, and feasibility is a bipartite
matching with augmenting paths, so a flexible player is moved aside to make
room for a rigid one rather than greedily wedged in first. An unfieldable
swap is not considered whatever it would do to the score, and an
unfieldable start is repaired by one cheap swap or abandoned. Thirteen
centres are refused, and a test says so. The constraint is visibly active:
the capped roster changed shape once it was on, while the uncapped one
happened to be fieldable already.

**On whether the optimizer just loves one player.** Across the thirteen
converged starting rosters, no player appears in more than seven and
seventy-seven distinct players are used, so it does not fixate. But two
plans compared on one season are only as independent as their rosters: the
capped and uncapped rosters share six of thirteen players, 46%, so that
comparison is half the same experiment and whichever shared player got hurt
decides both. `app.draft.compare` reports overlap and player frequency so a
comparison can say how much it shares instead of implying independence.

A migration lesson, recorded because it reported success while failing.
Adding a NOT NULL column to a populated table needs a server default;
without one Postgres rejects it, alembic rolls back, and a grep for
"Running" shows only the optimistic line. Check `alembic current` after any
migration that adds a required column.

### Market model

An auction is not a price per player, it is a fixed pot handed out until it
is gone. In 2026 fourteen teams at two hundred dollars spent 2787 of 2800,
99.5% of the budget. So prices are shares: a player is worth what they add
above the last man rostered, and every dollar over the minimum bids is split
between those surpluses. Budget consistent by construction.

Against the 2026 draft the mean error is **$6.48 across 175 picks**.

Measuring value above replacement rather than above the pool mean also
straightens the curve. Priced against the mean, the league looks like it
pays a falling rate for quality, 28 dollars a point down to 8. That was an
artefact of where zero was put, not a market judgement.

The residuals are the useful part. Four players took $170 more than the
model allows, 6% of the league's entire budget, and all four are reputations
the current projection no longer supports:

| player | model | paid |
|---|---|---|
| Cade Cunningham | 28 | 83 |
| Giannis Antetokounmpo | 23 | 70 |
| Luka Doncic | 47 | 91 |
| Victor Wembanyama | 71 | 100 |

Going the other way, Onyeka Okongwu modelled at 23 went for 4.

### Injuries: what can honestly be modelled

Three things were measured before anything was built.

**ESPN is optimistic.** Players deliver about 87% of projected games,
steady between 0.84 and 0.93 once the suspended 2020 season is set aside.

**It does not persist.** A player's availability one season predicts the
next at a correlation of 0.014, over 658 player seasons. After a year below
70% availability, the next year averages 0.839. After a year above 95%, it
averages 0.844. Injury proneness is not a usable draft signal.

**It does not vary by quality.** From best quartile to worst, availability
runs 0.867, 0.872, 0.879, 0.881, against a spread of 0.21.

So nobody can be singled out from history, which means the honest default is
one factor for everyone. And a single factor is **scale invariant in
z-space**: multiply every total by 0.87 and every z-score, ranking and price
comes back identical. A test pins that, because it is the finding that
decides what the injury-aware model can be.

Two places an adjustment legitimately bites:

- **Known injuries at draft time.** Someone already ruled out for two months
  has a knowable availability, and that does move their price. It has to
  come from a live status, not from history.
- **Absolute production against targets.** Targets are real totals, so a
  roster built on raw projections lands about 13% short of them. With no IR
  slot there is nothing to do about it but plan for it.

**A trap in our own data.** `daily_lineup_slots.injury_status` cannot be
used for any of this. ESPN returns a player's status as of the request, so
all 351 players in 2026 carry one status across all 160 days: Trae Young
reads OUT on days he played thirty minutes. It is a snapshot smeared over a
season, not a time series.

### Targets

A target is a percentile of what opponents actually post, read from eight
seasons of results rather than simulated. Clear the median and you win the
category about half the time.

**Two things cannot be pooled.** The first is matchup period length. Most
periods are a week, but every season has one All-Star fortnight, and it
posts about a third more of everything: 794 median points against 607.
There is also a short six day opening week at 502. Mixing them compares
unlike things, and because the fortnight sits in the upper tail it inflates
exactly the targets a manager cares about, by 1% at the 75th percentile and
2.3% at the 90th. Targets read from one period length, the most common by
default, and `period_days=14` asks about the fortnight instead.

Dividing by days would be wrong rather than helpful. The fortnight has
fourteen days but nowhere near fourteen days of basketball, which is why it
is up a third and not double.

The second is league size, and the data is
emphatic. Counting categories fall about a fifth between a ten team league
and a sixteen team one, because sixteen rosters share the same player pool
and each is thinner:

| category | 10 teams | 16 teams |
|---|---|---|
| PTS | 657 | 534 |
| REB | 241 | 193 |
| AST | 145 | 117 |
| BLK | 26.3 | 20.2 |

The two percentages do not move: field goal percentage is 0.473 at ten teams
and 0.477 at sixteen. A rate does not care how many players produced it.

So counting categories are read only from seasons of the same size, and rate
categories from every season, which gives them roughly six times the sample
for free. Every target reports which seasons it came from and how many
results, because a target from one season of the right size is a weaker
claim than one from five.

Turnovers invert into a ceiling rather than a floor: to win them more often
you commit fewer, so a higher win rate means a lower number.

### Era: the game moves too

A target read from a sixteen team season is the right shape but the wrong
year. The only sixteen team season on record is 2023, four years before the
2027 draft, and the NBA has kept scoring since.

Measured free of league structure, from the per-game output of established
starters, players with 40+ games at 28+ minutes. That population exists in
every season regardless of how many fantasy teams shared it, so it isolates
the game from the roster dilution targets already handle.

Only five of nine categories actually drift:

| category | per year | r squared | 2023 to 2027 |
|---|---|---|---|
| 3PM | +1.59% | 0.54 | x1.063 |
| AST | +1.30% | 0.78 | x1.052 |
| PTS | +1.02% | 0.84 | x1.041 |
| FG% | +0.46% | 0.58 | x1.023 |
| FT% | +0.30% | 0.77 | x1.015 |
| REB, STL, BLK, TO | noise | 0.01 to 0.20 | x1.000 |

Rebounds, steals, blocks and turnovers do not move in any way a straight
line can find, so they are left alone. Scaling by noise would be worse than
not scaling. Every target reports the multiplier applied, and it is exactly
1.0 where nothing was.

The shooting rates mattered more than expected. They are pooled across all
eight seasons for sample, which quietly anchored them to a lower-shooting
era: field goal percentage has gone from .467 to .477 since 2019.

**For 2027, at sixteen teams, in an ordinary week:** clear 546 points, 189
rebounds, 121 assists, 54 threes, shoot .486 and .802, and stay under 62
turnovers to win each about half the time. Counting figures come from 2023,
the only sixteen team season played, across 272 comparable sides, brought
forward where the category drifts.

A subtlety the tests caught. The season being drafted for already exists in
the database, with a size and no results. Matching on size alone found that
empty season and returned nothing, so only seasons that have actually been
played count as a basis.

### Valuation

Standard nine-category z-scoring, with the three details that decide whether
it is any use:

- **Turnovers are inverted.** ESPN's own `isReverseItem` says false for
  turnovers and the box scores disagree, which we confirmed earlier.
- **Percentages are weighted by volume.** Contribution is made shots above
  what the pool would have made on the same attempts, so a perfect shooter
  on two attempts moves nothing and a high-volume poor shooter is a
  negative.
- **The pool is the players who get drafted**, not everyone with a
  projection. Scoring against the whole field drags the mean down and
  flatters replacement level, so the board is scored once, narrowed to
  teams x roster slots, then scored again.

Value is computed on season totals rather than per-game rates. A player only
helps on the nights they play, so games missed are a real cost, and totals
carry that for free.

**Checked against 2026 rather than assumed.** Valuing the field on ESPN's
preseason projections and comparing with what the league actually paid, over
the 175 drafted players who had one:

| | |
|---|---|
| Pearson | 0.85 |
| Spearman | 0.78 |

That is the model agreeing with the room, which is what makes the
disagreements worth looking at. Against a simple fit, the league paid about
53 over the curve for Cade Cunningham and 45 for Giannis, and got Onyeka
Okongwu about 21 under it.

### Scoring players, drafts, trades and the wire

`app/scoring/` grades a played season in **categories a week**: how much a
line moved a team's expected category wins against that season's teams
(docs/scoring/SPEC.md, tickets in docs/scoring/TICKETS.md). Every grade has
two lenses, decision (what was knowable) and result (what was delivered),
and a verdict sentence. `scripts/scorecard.py` and
`GET .../teams/{tid}/scorecard` serve a team-season; the season report reads
the same scorecard, with nine-cat production kept as fine print.

Measured while building it (all on the live database, 2026-09-16):
started lineups reproduce ESPN's weekly team totals on 294 of 294
team-weeks; a typical pickup adds 0.06-0.13 categories a week; a pick at
price p has delivered 0.050 + 0.0735 x sqrt(p) to its drafting team; the
knowable line (season to date shrunk to the projection, a little recent
form) beats the projection alone 2.85 to 4.36 on the next 28 days; trades
reconstruct at or under ESPN's count every season (157 of 204). A
league-wide check caught a counterfactual that charged every wire move
for games already played; fixed, 1-for-1 moves average +0.07 a week and
trades net to zero.

S10 (daily projection snapshots) is on main with migration 0014. The nightly
ingest refuses to run behind the latest migration, so pulling main on the VPS
and `alembic upgrade head` go together, before the 09:00 UTC run.

## Building now

- Nothing in the draft framework. The code is done (2026-09-16), pending the
  manager rehearsing on 2026 (`scripts/draft_service.py --rehearse 2026`) and
  one ESPN mock draft to prove the page reader before 10 Oct. Before the
  draft: re-pull BBM, re-run `scripts/draft_plan.py`.

### Why the schema is season-scoped

ESPN settings are per season, not per league: between years a league can
change its name, team count, playoff format and scored categories. So
`leagues` holds only the durable ESPN id, and everything that can change
hangs off `league_seasons`, one row per (league, season). Re-ingesting a
season updates it in place; a new season inserts alongside and leaves prior
seasons untouched.

Two deliberate consequences:

- Scoring categories are rows, not columns, so nine-cat becoming eight-cat is
  a data change rather than a migration.
- `league_seasons.raw_settings` (JSONB) keeps the payload ESPN returned, so a
  field we do not model yet can be backfilled without re-fetching a season
  that may no longer be available.

### What the live probe taught us

- `scoring_type` is `H2H_CATEGORY`, with 9 scoring categories
  (statIds 0, 1, 2, 3, 6, 11, 17, 19, 20) in `settings._raw_scoring_settings`.
  `espn_api` exposes no parsed category list — the raw dict is the only source.
- `settings.acquisition_budget` is the in-season FAAB pot, 100 every year. It
  is NOT the auction budget, which is 200 and lives only in the raw
  mSettings payload under `draftSettings.auctionBudget`. `espn_api` exposes
  the first and not the second. Migration 0013 stores the auction budget
  with the draft type, clock, date and nomination order.
- 2027 is fifteen teams, not fourteen, with the centre cap back to four and
  one injured reserve place, drafting 2026-10-03. Read from the settings,
  not assumed.
- `team.wins` / `losses` / `ties` are **category** tallies, not matchup records.
  They sum to 171 per team = 19 matchup periods x 9 categories. A matchup
  record has to be derived; it is not a field ESPN hands back.
- `team.outcomes` is empty for this league, so per-period results must come
  from the schedule/matchup endpoint rather than the team object.
- `settings.matchup_periods` has 22 entries while `reg_season_count` is 19;
  the extra 3 are playoff periods. The two numbers are not interchangeable.
- `team.team_id` is sparse and non-contiguous (1, 3, 11, 12, ... 27), so it is
  a real external key, not a row index.
- `team.owners` is a list of dicts and can hold more than one owner, so the
  team-to-owner relationship is many-to-many, not a single column.

### What the box scores taught us

- Box scores, not `team.schedule`, are the right enumeration. Each matchup
  appears once instead of twice, lineups arrive in the same response, and
  schedule length varies by team (21 for some, 22 for others).
- A bye is reported as opponent team id `0` with winner `UNDECIDED`. Stored
  as a matchup with a null away team. There are six, all in the playoffs.
- `box.home_wins` / `away_wins` / `home_ties` are the categories won in that
  one matchup, and they do sum to the category count.
- **A matchup record is now derivable**, which it was not before. Summing
  matchup winners over the regular season gives, for example, 14-5-0, against
  a category tally of 106-62-3 for the same team.
- `espn_api` reports `lineupSlot` as "PG" for every player in every period
  (601 of 601 checked), and `slot_position` too. **This was misread as
  unrecoverable and it is not** — see "Correction" below.
- ESPN's `matchupPeriods` map claims one scoring period per matchup period,
  which the box scores contradict (period 1 reports scoring period 6).
  `matchup_periods.final_scoring_period` holds what the box score said. The
  authoritative mapping turned out to be `League.matchup_ids`.

### What the per-category detail taught us

- `box.home_stats` carries 13 statistics: the 9 scored categories plus FGM,
  FGA, FTM and FTA. The components mean a stored percentage can be
  recomputed or re-weighted instead of being taken on trust. FG% recomputes
  from its components to within 5e-9 across all 308 sides.
- A statistic is a scored category when it links to a row in
  `league_season_categories`, never because `result` is non-null. On a bye
  ESPN reports real values with a null result on all 13 statistics.
- Percentages are ratios, not percents: FG% arrives as 0.457.
- Turnovers invert: the side with fewer turnovers gets `result = WIN`, even
  though `isReverseItem` is false in the scoring settings. Trust the result,
  not the flag.
- Cross-check passed: tallying per-category WIN, LOSS and TIE reproduces the
  separately stored matchup totals for 151 of 151 contested matchups.

### What the draft gave us

Free, and it was sitting on the league object from the very first fetch. The
league drafts by auction, so every pick carries a price, and every season has
one: 130 picks in 2019 through 208 in 2023.

`bid_amount` here is the **draft** budget, roughly 200 a team, and a
different pot from the in-season acquisition budget of 100 that
`transactions.bid_amount` records. Two columns with the same name and
different meanings, which is worth knowing before comparing them.

The point of storing it is the comparison against production. In 2026
Wembanyama cost 100 and returned 16.0 points per dollar, while
Gilgeous-Alexander cost 79 and returned 26.8. Giannis cost 70 and returned
14.2 across an injury-shortened year.

A drafted player already known to us keeps the name we have; the draft does
not rename anyone.

### What projections turned out to measure

Mostly health, not scouting. The 2026 misses are dominated by players who
were forecast a full season and did not get one:

| player | paid | projected | actual | projected games | actual games |
|---|---|---|---|---|---|
| Trae Young | 42 | 1852 | 269 | 75 | 15 |
| Anthony Davis | 55 | 1778 | 407 | 64 | 20 |
| Giannis Antetokounmpo | 70 | 2236 | 993 | 71 | 36 |

The endpoint returns the games columns alongside the points for that reason.
Without them a 1583 point shortfall reads as a collapse in form rather than
fifteen games played.

ESPN's own season total is stored next to our daily lines even though the two
overlap, because they can disagree: ESPN omits days from its own cards for
some seasons, and keeping both makes the discrepancy visible rather than
picking a winner silently.

### What the transactions endpoint taught us

- `League.transactions()` is unusable. It drops the transaction id, drops
  `fromTeamId` and `toTeamId` on every item so a trade cannot be read at all,
  and raises outright on a TRADE_UPHOLD, which carries no items. The payload
  is parsed directly; only its HTTP layer is reused, so endpoint selection and
  the history fallback stay in one place.
- It also defaults to the league's current scoring period, which runs past
  the end of the fantasy season and returns a payload with no transactions
  key. A day must be passed explicitly.
- **The same transaction is returned under more than one scoring period.**
  Keying stored rows on (season, requested day) missed them and killed the
  first backfill on a unique violation. The key is ESPN's own id, and the day
  comes from the payload rather than the request.
- FUTURE_ROSTER is daily lineup shuffling, 53 of 86 rows on a sampled day.
  Excluded: `daily_lineup_slots` already records it properly.
- Failed and cancelled moves are kept on purpose. A losing bid records who
  wanted a player and what they offered, which no successful claim reveals.
  Of 5132 transactions in 2026, only 1148 were executed waivers.
- Team 0 means free agency, not a team, and is stored as null on both sides
  of an item.
- Transactions reference players never rostered long enough to appear in a
  box score, so they are created from the league's player map.

### What the player cards taught us

- `player_info` batches. ESPN returned all 348 players of this league in a
  single call in about 3 seconds, so per-player fetching is unnecessary.
  Ingest batches at 100 to keep request size bounded on larger leagues.
- A player card carries roughly 82 numeric keys, one per scoring period,
  each with a date, the opposing team and 45 statistics. The non-numeric
  keys ("2026_total", "2026_last_7") are season rollups and are skipped.
- The `team` field on a card entry is the OPPONENT. Across 29 distinct
  values for one player, their own team never appeared.
- Rows are kept for days a player's team played and they did not, flagged
  `played = false`. Absence of a row means no fixture; it should not have to
  double as "did not play".
- Dates arrive with no offset and are read as UTC. The stored range,
  2025-10-22 to 2026-04-13, is exactly the NBA regular season.
- Rate stats (PPG, FG%, MPG) stay in `raw_totals` only. For a single game
  they either duplicate a counting stat or divide by one.

## Next

1. Whichever narratives the endpoints turn out not to answer
2. ~~Alerting on a stale season~~ done: `scripts/watchdog.py` checks the
   ingest, the listener, the BBM pull and the backups once a day and sends
   one message when a job has gone quiet (`deploy/fcp-core-watchdog.*`).
3. ~~A frontend~~ started 2026-09-18: the draft room and the in-season
   pages. Direction: the auction board, dark, for the room; the season
   report's light house style for everything else; the nine categories
   always in the same order as a shaded strip.
4. ~~In-season pickups~~ built, backtested and deployed 2026-09-18 (above).
   What is left is the first honest test, on real 2027 games from about
   2026-10-20: the minutes tilt and the injury logic have never fired on
   live data.
5. The end-of-week matchup predictor, designed in `docs/week_predictor.md`:
   each category's chance of being won this week, and the matchup's, from the
   fitted knowable line, the stored NBA schedule and the listener's
   availability. ESPN data only, so it needs no paid source, and it is
   backtestable on 2026 except for availability, which has no history.
6. Before the draft on 2026-10-10: Patrick rehearses in the new room with
   docs/draft_night.md open, and runs one ESPN mock with the page reader.
7. 2026-10-05: check Basketball Monster's daily and weekly tools, then store
   them in season if they are live.
8. Accounts, planned for November: the ESPN connection, the team, the
   projection owner and the notification channel per account. Until then
   everything assumes one manager (`viewer_owns_source` is a constant).

### Prior seasons

The league's own `previousSeasons` lists what ESPN still holds, so the season
list is read rather than guessed by probing years. Past seasons come from a
different endpoint (`leagueHistory`), which `espn-api` selects on the year.

The schema's season-scoping earned itself here. Across the eight seasons the
league ran with 10, 12, 14 and 16 teams, and regular seasons of 16 to 20
matchup periods:

| season | teams | regular season periods |
|---|---|---|
| 2019 | 10 | 20 |
| 2020 | 10 | 18 |
| 2021 | 10 | 18 |
| 2022 | 12 | 16 |
| 2023 | 16 | 18 |
| 2024 | 14 | 19 |
| 2025 | 12 | 17 |
| 2026 | 14 | 19 |

Two things had to be solved to make older seasons work.

**The period-to-day mapping does not exist before 2025.** `League.matchup_ids`
is built from `pointsByScoringPeriod`, which ESPN populates for 2025 and 2026
and leaves empty for 2019 to 2024, on both endpoints. It is now discovered by
probing: ESPN returns a daily roster only when the requested day falls inside
the requested matchup period, so an empty response means "try the next
period". Walking days in order costs one request per day plus one per period
boundary, and the discovery was validated against 2025, where the real
mapping exists, by reproducing it exactly.

**The weekly aggregate roster is nearly empty before 2025.** It holds 341 to
430 rows for older seasons against 4436 for 2026. Player stats were scoped to
it, which silently cut coverage: 2023 first loaded 22073 player lines instead
of 28147, and reconciliation sat at 175 of 336 sides. Player stats are now
scoped to the union of the weekly and daily roster tables, and daily lineups
run first because they are the only complete account of who a team held.

Reconciliation across all eight seasons, summing started players against the
separately stored team totals:

| season | sides agreeing |
|---|---|
| 2019, 2020, 2022, 2023, 2024, 2025, 2026 | all of them |
| 2021 | 196 of 200 |

The four 2021 exceptions are ESPN omitting days from its own player cards for
that COVID-shortened season. Al Horford's 2021 card returns 74 scoring
periods and simply has no entry for days 43, 45 or 48, though the team totals
counted his production. Verified upstream, not a parsing fault.

### The scheduled ingest

A nightly job refreshes the current season, and the next season's settings
while its draft is still ahead. It runs `scripts/scheduled_ingest.sh`, which
calls the ingest as `--recent --upcoming`.

**Why the next season too.** The current season is whichever one ESPN is
playing, so through September it is still the old one, while the draft being
prepared reads the new one. Nothing refreshed that row. On 2026-09-15 the VPS
held 2027 as 16 teams, a $0 auction budget, a centre limit of 4 and no draft
date, when ESPN had 15 teams, $200, a limit of 3 and a draft on 2026-10-03.
`app/draft/live.load_room` refuses a $0 budget, so the room would not have
opened; it was fixed by hand with `--season 2027`. A day later ESPN had moved
again, to 16 teams and a draft on 2026-10-10, which is the argument for doing
this nightly rather than once.

`--upcoming` fetches current + 1 and, if ESPN has it and its draft date is
unset or still in the future, writes its settings (team count, auction
budget, roster and position limits, draft type, date and order) and its
teams and owners. Not matchups, lineups, game logs or transactions: a few
ESPN requests, not a couple of hundred. The run is recorded in `ingest_runs`
with mode `settings`. A season ESPN does not have yet, which is most of the
year, is skipped with a line in the log and no run row, so it cannot trip
the health check; a real ESPN outage has already failed the current season's
run, which goes first. Once the draft date passes the season is left to the
regular ingest, which takes it over at the October rollover.

**Narrow by design.** A full season is a couple of hundred ESPN requests and
about two and a half minutes. `--recent` covers the trailing ten scoring
periods, which is 15 seconds. Nothing outside that window is deleted or
rewritten, so a nightly narrow run and an occasional full one coexist.
Anchored on ESPN's `current_week`, which is the current scoring period
clamped to the fantasy season, and windowed from the stored matchup periods
so no discovery pass is needed.

**Getting it fast took two fixes, both worth remembering.** The first
`--recent` run took 149 seconds, no faster than a full one. Player stats were
issuing a query per row across 28000 rows; they now load one query per batch.
Then it still took 83 seconds, because the scope was never reaching the day
generator: the argument had been dropped when the call was reformatted onto
one line. A phase-by-phase timing run found it.

| | duration |
|---|---|
| full season | ~150s |
| first `--recent` attempt | 149s |
| after batching stat loads | 83s |
| after actually passing the scope | 15s |

**It follows the season rollover.** `ESPN_SEASON` used to be required, which
made the schedule a time bomb: the 2026 season ended on 2026-04-13, and a
pinned job would have kept refreshing it through the whole of 2026-27,
succeeding every night while the live season went unrecorded. Worse, the
health route would have reported `stale: false` throughout, because the
pinned season really was fresh. The season is now derived, and
`GET /ingest-runs/health` with no season answers for whichever year is
actually running.

ESPN creates a season shortly before play starts rather than on 1 October, so
the derived year is tried first and falls back one year if it is not there
yet. That turns a possible fortnight of failed nightly runs into a silent,
correct fallback, while keeping the loud failure mode for anything else.

**Every run is recorded** in `ingest_runs`, opened before ESPN is touched and
closed whatever happens. A row left at "running" means the process died,
which is itself worth seeing. Failures keep the first line of the error.
`GET /ingest-runs` lists history; `GET /ingest-runs/health/{season}` reports
how long since the last success and whether that is stale, with a 36 hour
threshold so one missed nightly run does not cry wolf.

**Runs on the VPS**, at `/opt/fcp-core`, via
`deploy/fcp-core-ingest.{service,timer}` at 09:00 UTC. A laptop sleeps, so a
launchd agent only fires when you happen to be at the desk, which defeats the
point of a schedule. The Mac agent is unloaded and kept as
`com.fcp-core.ingest.plist.disabled` in `~/Library/LaunchAgents` if it is ever
wanted back.

```
sudo cp deploy/fcp-core-ingest.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fcp-core-ingest.timer
sudo systemctl start fcp-core-ingest.service   # run one now
```

Exit code 69, the database being unreachable, is left as a systemd failure on
purpose: on a server the database is meant to be up, so a skip belongs in
`systemctl --failed` rather than being quietly tolerated.

**Also installable** as a user-level launchd agent at 09:00 local, which is
how it ran before the move:

```
cp deploy/com.fcp-core.ingest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fcp-core.ingest.plist
launchctl start com.fcp-core.ingest      # run one now
launchctl unload ~/Library/LaunchAgents/com.fcp-core.ingest.plist   # remove
```

The wrapper waits up to 60 seconds for the database and exits 69 if it never
appears, logging why, rather than reporting a failure that was never
attempted. **The Compose database must be running for the job to do
anything**, so Docker needs to start at login for the schedule to be
reliable. Output goes to `logs/scheduled-ingest.log`, trimmed when it grows
past 2MB.

### The listener

Built 2026-09-17 from the design in `docs/pickups.md`, against the test
database only: this container cannot reach ESPN or the VPS, so the live
pool has not been fetched by this code yet. What is pinned by tests: every
entry leaves a snapshot and the unrostered ones a free-agent row; the
second pass produces exactly the events its changes warrant and asks for
news only for the tracked roster and the players with an event; a minutes
spike is recorded once and again only when a new game moves the window;
out of season the pass snapshots once a day and never fetches news; the
pool fetch pages until a short page; the scheduled wrappers refuse a
database behind the code; the migration upgrades and downgrades.

The digest reads only what the passes wrote. Its season is the newest one
the listener has snapshotted rather than anything fetched, its roster is
whoever the latest snapshots put on the tracked team, and a free agent is
ranked by percent owned until phase 2 can price him. An event lands in the
roster section or the wire section by where its player is now, so a rival's
injury is never reported and a player a rival drops moves to the wire by
himself.

Decisions taken while building it, beyond the design note:

- An ownership surge or slide fires once, as the 24-hour move crosses the
  line, not on every pass it stays over it. The line is 5.0 points, and
  crossing 25% owned upward is a surge on its own.
- A minutes event names the last game it ran through, and the pass skips
  one that names the same game as the last event of that kind on record.
- The pass labels itself from the clock (`label_for`): the nearest slot
  within an hour, else `adhoc`. The nightly ingest wrapper runs a fourth
  pass with `--label nightly` after the ingest succeeds.
- The season row is written from the league's settings if the ingest has
  not made it yet, so the listener never loses a day to ordering.
- A scheduled `--recent` ingest now unions the latest free agents into the
  players whose cards it fetches, so an unrostered player's minutes are
  stored before anyone picks him up. A full pass does not.
- `waiverProcessDate` is the one field name taken on trust.
  `scripts/espn_probe.py --pool-keys` reports how many WAIVERS entries
  carry it, and `--dump-card ID` prints one entry whole.
- The notification service is left to you, which is what section 10 of the
  design note wanted: `app/notify.py` posts the text as the body, which is
  ntfy's API, and setting `FCP_DIGEST_CHAT_ID` switches it to Telegram's
  JSON. Neither needs a client library or an account key in the repository.
- An event is marked notified only after a delivery succeeds. A dry run, a
  missing URL and a refused POST all leave it unmarked, so the next message
  repeats it rather than swallowing it.
- A blank value in `.env` (`ESPN_SEASON=` with nothing after it) now reads
  as unset instead of failing with a pydantic parse error.

**To deploy**, on the VPS, after pulling main:

```
cd /opt/fcp-core && git pull
./.venv/bin/python -m alembic upgrade head          # migration 0016
./.venv/bin/python scripts/espn_probe.py --pool-keys  # confirm the field names
./.venv/bin/python scripts/status_pass.py --force     # one pass by hand, read the output
sudo cp deploy/fcp-core-status.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fcp-core-status.timer
sudo systemctl restart fcp-core-api.service          # the new routes
```

`FCP_TRACKED_TEAM_ID=<espn team id>` in `.env` names the roster whose news
every pass fetches and whom the digest is about, and `FCP_DIGEST_URL` is
where the digest goes: an ntfy topic URL works as it is, or a Telegram
`sendMessage` URL with `FCP_DIGEST_CHAT_ID` beside it. Read one before
turning delivery on:

```
./.venv/bin/python scripts/digest.py --dry-run
```

Then `GET /ingest-runs/health?mode=status` says whether the listener is
alive, and `.../events` what it has seen.

### The pickup recommender, this week and the rest of the season

Built 2026-09-18 from section 4.3 of `docs/pickups.md`, against the test
database only: the local development database is at migration 0013 and has
none of the listener's tables, so `scripts/stream.py` refuses it with a
message rather than a traceback, and the report has not yet been read on a
real week. `app/pickups/` holds it, in three modules and no HTTP: `state`
(the team's week: roster from the latest lineup day or, before one, the
snapshots; both sides' posted counts; days left; open places, the IR slot
and FAAB), `projection` (a player's per-game line from today, the knowable
line with a switchable minutes tilt on top, and the games it is spread
over), and `stream` (every legal swap, add and IR move ranked by the change
in expected categories won head to head, the empty-day check, and the
hurdle). `scripts/stream.py --season 2027 --team "Through The Wire"` prints
it for one day.

What is pinned by tests (32): the roster, status and games come from the
tables the docstrings name and no other; OUT with a return date loses
exactly the games before it; the tilt fires only while its event is live,
scales every count, is capped, and switches off; a level category is
flipped by a free agent with three games into an open slot; the empty-day
check names the day, the slots and who could fill them; a marginal swap is
listed and refused; a free agent whose four games fall on full days adds
nothing; a better one displaces the worst starter; an OUT player can go to
IR to make room; position limits hold; a bye has no head-to-head.

Decisions taken while building it, beyond the design note (each is also
marked **as built** in the note):

- Starts, not games. Each remaining day is the `startable` matching, seated
  in order of a per-game weight against the league's spreads; the weight
  only decides who sits on a full day. A four-game pickup on full days is
  worth nothing, which a games count cannot see.
- The per-game rate is the knowable line, not the note's `k = 20` blend, and
  BBM is not consulted; the availability factor is therefore always 0.88,
  and only on a rest-of-season line.
- The FAAB pot is `acquisition_budget`, not `auction_budget`.
- OUT with no return date is out for the period. An IR move needs OUT exactly.
- A swap is legal when the roster keeps the position limits and seats at
  least as much of the lineup as before, so a roster already short can still
  make a move that does not make it shorter. Affordability is not enforced.
- The five moves reported are the best per added player. A filled empty day
  clears the hurdle only with a positive change.
- On a bye the report carries the empty-day check and no moves.

**The rest-of-season half**, built the same day from sections 4.4, 4.5 and
5.1. `app/pickups/season.py` asks the draft optimizer the same question
three ways -- lock the roster and open a place, lock it less one man, lock
it less two -- with every candidate's weekly line his rest-of-season line
over the weeks left and every price zero, since in season the constraint is
roster places rather than money. Out come the best free add, the best swap
and the best two-swap, each with its own hurdle and the categories it
moves; the three men whose removal costs least, which is the same
computation read from the other end; free agents ESPN has OUT with a return
date inside six weeks whose healthy value would earn a place; and the
team's adds over the last fortnight beside the league's own finding that
the managers who churned most returned least per move.
`app/pickups/bids.py` fits the median and 75th percentile winning FAAB bid
by the claimed player's value rank on the day, over every season the league
played with FAAB. `app/api/pickups.py` serves both reports, and
`scripts/season.py --season 2027 --team "Through The Wire"` prints the long
one.

What is pinned by tests (20 more): the optimizer takes the better of a
two-player pool; a marginal swap is found and refused and carries no bid; a
two-swap beats the best single one when two places are weak; a free add
into an open place clears the lower hurdle; the drop candidates are ordered
by what losing each man costs; the volume guard counts the adds inside its
window and not the one outside it; a stash appears only with a return date
inside six weeks; a claim is bucketed by its rank and the buckets carry
their range and sample; the 75th percentile is paid only for a move worth
twice its hurdle; the pot and the weeks left each cap a bid; both routes
serve a seeded league, an unknown team is a 404 and a season the listener
never ran for is a 409.

Decisions taken while building it, beyond the design note:

- The horizon is the rest of the regular season, or the playoff periods
  once it is over. Games and weeks are counted over the same window, so a
  weekly line is games a week over the stretch actually planned for.
- `minimum_bid=0` and `restarts=0` go with `budget=0`. A floor of a dollar
  a place under a budget of nothing leaves a roster short, and a shuffled
  start is wasted when all but one or two places are locked.
- A move that drops a player is charged the paid hurdle and an add into an
  open place the free one. That is the note's rule stated in terms of the
  move rather than the waiver state, which the stored rows do not carry.
- A bid's rank is the per-game weight, not the rest-of-season one. A rank
  is ordinal and every NBA team plays the same 82 games, so the orderings
  agree -- and the only FAAB season on record, 2026, predates the schedule
  table entirely.
- A day's wire is narrowed to the 120 men with the most production in the
  last fortnight before it is priced. A rank past 41 changes no bucket, so
  the pre-filter cannot move a claim between buckets.
- A season the listener has never run for is a 409, not an empty report:
  "no move is worth making" and "nothing to decide from" are different
  answers and must not look alike.

**One currency, both horizons**, added 2026-09-18 in `app/pickups/judge.py`.
Every move either half of the recommender can make is valued in one place
and in one unit, categories, across both horizons at once: the gain in this
week's matchup, plus the change in what the roster place yields in an
ordinary week from then on, times the matchup weeks after this one. The
ranking and the hurdles read that net, so a week's gain bought by dropping a
man worth half a category a week for fifteen weeks is refused rather than
recommended.

What dropping a player costs is **not** his rest-of-season value. The place
never goes empty, so the charge is his value less what the wire gives the
place back on either side: `max(value(dropped), wire) - max(value(added), wire)`, where
`wire_replacement` is the best free agent still available after the one being
added, floored at a typical pickup (`TYPICAL_PICKUP` 0.06 categories a week,
the lowest recent season's median in `app/scoring/replacement.py`). Streaming
a fringe player is therefore free, dropping a real one is charged his gap to
the wire, and a genuine keeper counts for more than a streamer on the way in
because the `max` takes the better of "he stays" and "the place is streamed
again". `value()` is the league-standard lens `app/scoring/players.py` grades
a season with: his weekly line inside `average_team_line`, through
`app.scoring.value.marginal` -- comparable across players and blind to fit,
which is right for a charge about what a man is worth to whoever picks him
up. Every report now shows the projected end-of-season category record with
the move and without it, beside the week's numbers; the difference between
the two records is exactly the net.

**The backtest (section 4.6) is built and scores in categories.** A move is
replayed against the matchup that actually happened, with the daily lineup
re-solved: on each day the roster the team really held is read from
`daily_lineup_slots`, the swap applied to it, and the ten starting slots
filled by the recommender's own seating rule, ordered by what each man had
averaged before that day so no hindsight sets the lineup. The roster without
the swap is re-solved the same way, and the two lines are counted against the
opponent's real period totals and subtracted. Re-solving both sides is what
keeps lineup optimisation out of a move's credit; the gap between the
re-solve and the manager's own starts is reported as drift and enters no
score, and on 2026 it is -0.02 categories a period. The season score is the
same replay over the next 30 days. The baseline is the league's
own one-for-one swaps replayed backwards and negated, so a real claim and a
recommendation are one quantity. The composite-scored first cut of
2026-09-18 read -3.2 composite a day at a 20% win rate, and that was a
measurement of composite: the recommender spends points on purpose to win the
categories that are close, and composite charges it for every point. Beside
the sweep the run reports what the recommender claimed against what it
delivered, which is the calibration a win rate cannot answer. What the replay
cannot do is know what lineup the manager would really have set; both sides
of every move assume the same seating model, and the drift number says how
far that is from what he did.

Measured on one team, 2026, tilt on (44 decision points, 275 s), after the
re-solve: the streaming report delivers +0.24 to +0.28 categories a matchup
at 86-89% of moves costing nothing, and the rest-of-season report +0.72 to
+1.17 categories over thirty days. The league's own 1,120 one-for-one swaps,
replayed backwards through the same code, deliver +0.05 a matchup and -0.56
over thirty days. The recommender therefore beats the league's own moves on
this roster, which the capped replay could not show: it scored the streaming
side at +0.00 with 60% of named moves at exactly zero. One team is a smoke
test, so no hurdle constant was changed; the full run belongs on the VPS.

Not built here, for the next brief: the digest's sections 3 and 4, and a
full-league backtest run on the VPS (the dev database is read-only here, so
only a one-team smoke run has been made).

### Why the API is tailnet-only, and why owners have opaque ids

The API has no authentication. Binding it publicly would hand every season,
every player line and every league member's name to anyone who found the
host, so `scripts/serve_api.sh` binds the Tailscale address and nothing else.
It falls back to loopback when the tailnet address cannot be read, which
fails closed rather than open. Verified: reachable on the tailnet, refused on
the public IP.

Related, and the reason this mattered enough to check: ESPN identifies an
owner by their SWID GUID, which is **half of the cookie pair that
authenticates a real ESPN account**. Three endpoints were returning it
verbatim, and the stored value for one of the 46 owners is the very cookie
this project authenticates with. Responses now carry this database's own
opaque owner id. The GUID is still stored, because identity across seasons
depends on it, but it no longer leaves.

That is a deliberate exception to keying paths on ESPN's identifiers. For
leagues, teams and players an ESPN id is meaningful and harmless. For owners
it is credential-adjacent, so the surrogate wins. Tests assert both that
nothing GUID-shaped appears in any owner-bearing response and that the opaque
id still correlates across endpoints, since an id nobody can join on would be
safe but useless.

Before this could go public, three things would have to happen: authentication
in front of it, a decision about what league members' data should be visible
at all, and a second look at anything else ESPN-derived in the responses.

### Deployment: sharing a host with the live stack

`aisha-vps` already runs production Full Court Press, and that shaped every
choice here. Reached over Tailscale SSH, which needs a browser approval
before a session will authenticate.

What was already there, and is untouched:

| | |
|---|---|
| `fcp-v2-pg` | Postgres 16 on port 5432 |
| `fullcourtpress-caddy-1` | Caddy on 80 and 443 |
| `fullcourtpress-backend-1` | the live API on 8000 |
| `/srv/fullcourtpress` | the live checkout |
| `fcp-snapshot-refresh.timer` | hits the public API every 15 minutes |
| `https://fcp.patrickmcdowell.dev` | the live site |

So the rebuild is deliberately isolated rather than installed in place:

- Postgres on **5433**, since 5432 is taken. `FCP_DB_PORT` in
  `docker-compose.yml` exists for exactly this.
- Compose project name `fcp-core`, so `docker compose down` in one project
  cannot reach the other's containers.
- systemd units named `fcp-core-ingest.*`, distinct from the existing
  `fcp-snapshot-refresh.*`.
- The API listens on the tailnet address only, on port 8001. Caddy was not
  touched, so nothing new is public.

Only this project's secrets were copied over. The local `.env` also holds
Anthropic, Supabase, Resend and DeepSeek keys, which were deliberately left
behind; the remote file has the two database URLs and the four ESPN values,
at mode 600.

The VPS is also simply faster: 72 to 88 seconds per season against 140 to 172
on the Mac, and a nightly `--recent` run takes about 10 seconds. The stored
data matches the Mac exactly, including the 2021 reconciliation gap, which is
a good sign the two are genuinely the same pipeline.

### The scheduled jobs, and what notices when one stops

| unit | UTC | what it does |
|---|---|---|
| `fcp-core-ingest.timer` | 09:00 | the season's trailing days, next season's settings, then a listener pass |
| `fcp-core-bbm.timer` | 09:30 | BBM's two exports, into `data/bbm/` and the database |
| `fcp-core-backup.timer` | 10:00 | a verified dump, kept 14 days |
| `fcp-core-watchdog.timer` | 11:00 | reports any of the others that has gone quiet |
| `fcp-core-status.timer` | 15:00, 22:30, 00:30 | listener passes, then the digest (15:00, with the day's plan, then warming the pages) or an alert |

The watchdog exists because silence is the failure mode that matters: a
failed run shows up in `systemctl --failed` and in its own row, while a timer
nobody enabled, a disabled unit or a pass that exits 0 without doing anything
looks exactly like a quiet weekend. Windows are about three times each job's
interval, except the listener's, which is twelve hours in season because the
hours it misses cannot be recovered.

### Deploying a code change to the VPS

Pulling is not enough. The ingest picks up new code on its next run, because
systemd starts a fresh process each time, but the API is a long-running
service and keeps serving whatever it was started with:

```
cd /opt/fcp-core && git pull
./.venv/bin/pip install -q -e ".[dev]"     # only if dependencies moved
./.venv/bin/python -m alembic upgrade head # only if there is a new migration
sudo systemctl restart fcp-core-api.service
```

Forgetting the restart is quiet rather than loud: the new routes simply 404
while everything reports healthy. It caught me once already.

### Why 2026 has so many more transactions

Not ESPN retention. **The league moved to FAAB in 2026**, and the stored
settings say so: `uses_faab` is false for 2019 to 2025 and true for 2026.

The composition flips completely, and the totals follow from the mechanism:

| season | faab | waivers | free agent | rows with a bid | top bid |
|---|---|---|---|---|---|
| 2019 | no | 91 | 681 | 0 | 0 |
| 2022 | no | 54 | 753 | 0 | 0 |
| 2024 | no | 172 | 868 | 0 | 0 |
| 2025 | no | 191 | 1070 | 0 | 0 |
| 2026 | yes | 4571 | 2 | 819 | 23 |

Under first-come free agency a pickup is one row: whoever got there first.
Under FAAB every team's bid on the same player is its own row, and most of
them lose. So the jump is mechanical rather than a change in how much the
league traded, and it is exactly why the losing bids are worth keeping.

Recorded here because the first reading of this file said the opposite. The
retention hypothesis was wrong, and the answer was already in a column the
schema had been storing since migration 0002.

## Correction, now resolved: daily lineups and bench

Two earlier conclusions in this file were wrong, and both shaped the schema,
so they are recorded rather than quietly edited away.

**Who was benched on a given day is recoverable.** Each side of a box score
carries two rosters. `rosterForMatchupPeriod` is the aggregate and sets every
`lineupSlotId` to 0, which is where the useless "PG" comes from, and it is
what `espn_api` reads by default. `rosterForCurrentScoringPeriod` carries the
real slots, reached with
`box_scores(matchup_period=N, scoring_period=M, matchup_total=False)`, whose
`slot_position` returns PG, SG, SF, PF, C, G, F, UT, BE and IR.

**The exact scoring-period mapping exists.** `League.matchup_ids` maps each
matchup period to every scoring period it contains: period 1 is days 1-6,
period 2 is days 7-13. Its keys are ints and its values are strings in
lexicographic order ("10" before "7"), so sort numerically or lose the
window. Stored as `first_scoring_period` and `final_scoring_period`.

**Reconciliation is now exact.** Summing the daily started players against
the separately stored team totals, over all 308 sides of the season:

| Category | Sides agreeing |
|---|---|
| PTS, AST, STL, BLK, TO, 3PM, FGM, FTA | 308 of 308 |
| REB | 307 of 308 |

The single rebound exception is ESPN disagreeing with itself, not a pipeline
fault: for Fantastic 5 in period 19 the player rows are internally consistent
(47 offensive + 203 defensive = 250) while ESPN's team total says 249.

### Narratives this unlocks

Bench decisions are now queryable. Over the regular season, Masters of their
Domains left 370 points on their bench and benched a 20-point game seven
times. The worst single call was The Infirmary sitting Gary Trent Jr. for 36
points on a day their best starter managed 5.

## Open questions

- ESPN omits the date and opponent on about 4.7% of played lines (969 of
  20431). The statistics are present and correct; only the game context is
  missing. Verified as an upstream gap, not a parsing fault.
- The fantasy season ends at scoring period 160 while `player_game_stats`
  holds days up to 174, since NBA games continue past the fantasy playoffs.
- `roster_slots` (weekly) and `daily_lineup_slots` (daily) overlap by design.
  Worth revisiting only if the weekly table stops earning its keep.
- Rosters are snapshots per matchup period, which is the finest grain the box
  scores expose. Daily roster movement within a period is not recoverable
  from this source.

## Not building yet

- auth (planned for November; see Next)
- other platforms (Yahoo, Fantrax, Sleeper) and roto or points scoring
- newsroom
- AI features
