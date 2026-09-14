# FCP Core Status

## Works today

- FastAPI application boots (`create_app()`)
- Read-only HTTP API over the stored seasons, 22 endpoints (see below),
  including seven narrative routes. Writes stay with the ingest.
- Nightly scheduled ingest keeping the current season current, with every
  run recorded and queryable. **Runs on the VPS**, not a laptop.
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
| `GET /ingest-runs/health/{season}` | time since the last success, and staleness |

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
| 5. Live draft room: state, remaining pool, re-solve | done; unproven against a live league until 2026-10-03 |

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

## Building now

- The draft framework, layer by layer (see above)

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
2. Alerting on a stale season, rather than having to look at
   `/ingest-runs/health`
3. A frontend, if and when there is something to read the API. That is the
   decision that would force the auth question.

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

A nightly job refreshes the current season. It runs
`scripts/scheduled_ingest.sh`, which calls the ingest in `--recent` mode.

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

## Open questions

- Rosters are snapshots per matchup period, which is the finest grain the box
  scores expose. Daily roster movement within a period is not recoverable
  from this source.

## Not building yet

- frontend
- auth
- projections
- optimizer
- newsroom
- AI features
