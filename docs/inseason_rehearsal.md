# The in-season loop, rehearsed on a season already played

**Written:** 2026-09-21. **Findings 1, 2, 3 and 10 fixed the same day**
(`6acd33e`, `d4f5d97`, `86bb303`, `3025ab9`); check 1 re-scored and now
PASSES. See "What was fixed, and what the re-run showed" below; the findings
themselves are left as they were written, each marked with what it became.

**What it was for:** step 4 (docs/jobs.md) — the
morning's `precompute` jobs, the stored `team_reports` rows, the digest and
the two pages that read them — had unit tests and had never once run
together against a real season. The `jobs` table on this machine was empty
until the morning this was written. The season opens in about a month, and
the week before, another part of this project that was "built and tested"
fell over on its first live contact.

Code: `scripts/rehearse_week.py`, `tests/test_rehearse_week.py`, and the
three seams it needed (`app/jobs.py`'s `only`, `app/job_kinds.py`'s
`payload_day`, `app/api/pickups.py`'s `TODAY`).

**The short version.** The machinery held. Four replays in sixteen minutes
of wall clock, 39 team-day precomputes over 44 job rows, every one of them
finished; the queue behaved under three workers; the pages came out of the
store in tens of milliseconds; no digest reached a third of Telegram's
limit; the All-Star break and the first playoff morning broke nothing. What
the rehearsal found is that **four numbers a report and a digest carry are
computed over a window with no far end**, so they read the rest of the
season as though it had already happened. None of them can hurt on opening
night — live, there is nothing after now — and all four would quietly
corrupt any replay, any backfill, and any morning a worker catches up late.
And a fifth of the morning's cost turned out to be one league-wide
calculation done again for every team.

**All four were fixed the same day**, and the check that caught three of
them — now asserting all four — passes on the re-run. The last section of
this document has the before and after.

## What was replayed, and what was not

Four runs against the local 2026 season (league season 8, 14 teams, 227k
lineup days, 199k box scores), all on the local Postgres and nothing else.

| run | what | days | teams | workers |
|---|---|---|---|---|
| `leagueA` | a whole league morning, twice | 77, 78 | all 14 | 1, then 3 |
| `teamB` | our team through a full regular-season period | 77–83 (period 12) | 86 | 3 |
| `edgeC` | the All-Star period's first day, the break, its last day | 112, 116, 125 (period 17) | 86 | 1 |
| `edgeD` | the first playoff morning | 140 (period 20) | 86 | 1 |

Sixteen minutes of wall clock for all four, one after another (they share
`team_reports` rows, so they must not overlap). 39 precompute jobs, 44 job
rows in all, 74 stored reports, 26 digests rendered, 39 page fetches.

Real: `app.jobs` end to end — enqueue, the unique dedupe key, `claim` under
`SELECT ... FOR UPDATE SKIP LOCKED`, the backoff, the parking, the reaper —
taken by separate worker **processes**; `app.job_kinds.run_precompute`;
`app.api.pickups.build_payload`; `app.reports.store`; the digest's own
builders; and both report routes plus the glance through a FastAPI
`TestClient` in single mode.

**Not real, and it matters:**

- **No ESPN and no VPS.** `ingest` and `status_pass` jobs are live-network
  by nature and were out of scope. Nothing enqueued one and the rehearsal
  worker has no handler for either. So the real morning's *ordering* —
  `status_pass`, then `precompute` and `digest` hanging off it by
  `depends_on` (`app/schedule.py`) — was **not exercised**, and neither was
  a pass failing and taking the digest down with it. `tests/test_jobs.py`
  covers the dependency in the abstract; a live morning has not.
- **Nothing was delivered.** No `digest` job was enqueued at all: the two
  digests a day were rendered by calling the same builders
  `app.job_kinds._owner_digest` and `_member_digest` call, and written to
  files. On top of that the rehearsal replaces `app.notify.deliver` and
  `app.channels.deliver` in every process it starts. Two locks, neither an
  environment variable, because `FCP_DIGEST_URL` and the SMTP settings are
  set on this machine. The consequence is that `_deliver`, `channels.verified`
  and `_outcome` — which channel took it, what a failing channel says — were
  **not exercised**.
- **The listener never ran for a played season.** No status snapshots, no
  wire snapshots. So every report fell back to the reconstructed historical
  wire (docs/in_season_pages.md) and every digest's roster and wire-news
  halves were empty ("nothing new", "All 0 active"). The live morning's
  digest will have material there that this rehearsal never rendered — and
  never measured for length.
- **The reconstructed wire is a function of the NBA calendar**, not of who
  was actually free: it is whoever played that day and was in nobody's
  lineup. `pool_size` was 55 on day 77 (16 NBA games), 34 on day 78 (12),
  16 on day 80 (8) and **0 on day 116**, the All-Star break. Nothing about
  the size of a plan is comparable across days here.
- **There were no `users` rows** on this database. Single mode's
  `create_app` wrote the owner's row itself (`accounts.ensure_owner`), which
  is the only row the rehearsal added outside `jobs` and `team_reports`.

The rehearsal's own jobs stay on the `jobs` table, marked `rehearsal: true`
in their payload and under dedupe labels beginning `rehearsal:`. A rehearsal
worker narrows every claim to its own run, so it cannot take, fail or reap a
real job; a real worker has no such narrowing and would run one, building
the day its payload names.

## The timing

**Per team-day** (`precompute`, wall clock from the job's `started_at` to
its `finished_at`, 39 jobs):

| | seconds |
|---|---|
| median | 5.7 |
| p90 | 44.4 |
| max | 50.4 |

That spread is not noise, and it is the most useful thing the rehearsal
measured. **The first precompute in a worker process costs 44–49 seconds;
every one after it in the same process costs 4.3–5.8.** On the 14-team
morning with one worker, the first job took 48.1s and the other thirteen
took 4.3–5.4s each. With three workers, three jobs took ~33.9s — one per
process — and eleven took 4.5–5.8s.

**A full league morning, 14 teams:**

| workers | wall clock |
|---|---|
| 1 | 114.9 s |
| 3 | 60.6 s |

Three workers bought 1.9x, not 3x, because each process pays the
league-level cost separately. That is the whole finding of the profile
below.

**The digest**, rendered (not sent): 1.8–3.0 s each once the process is
warm, and 37–43 s when it is not, because it rebuilds the week report from
scratch rather than reading the row the precompute has just stored. Length:
26 digests, 697–1249 characters, median 934 — under a third of Telegram's
4096, with the roster and wire sections empty.

**The pages**, fetched warm through the `TestClient` against the stored row:
39 requests, median 0.04 s, worst 0.12 s. Against a 44-second precompute
that is a factor of about a thousand, which is the point of storing them.

## The checks

### 1. No look-ahead — **FAIL**, four ways (re-run 2026-09-21: **PASS**)

> **Re-scored.** All four are fixed, and the check asserts all four now: the
> script grew a posted-totals assertion, which is (a) below, and its
> league-section assertion was rewritten to read the number off the line the
> digest actually renders rather than comparing two of its own queries. The
> same command — `--season 2026 --period 12 --teams all --only-days 77,78
> --workers 3 --checks` — comes back `1_no_look_ahead` **pass, 0 rows**, on
> 28 team-day payloads and two days of digests. The before and after lines
> are in the section at the end of this document.


The script checks five things from the stored payloads and two from the
digest's own queries: no scoring period before today counted as remaining,
no empty day before today, the season report's `today` correct, nobody
proposed as an add who was in a lineup that day, the adds the report says
the team has spent, and the digest's two trailing windows. The first four
passed on all 39 team-days. Three of the others failed, on every day of
every run: (b), (c) and (d) below.

(a) was **not** one the script asserted. It was found by reading the day-77
digest — a team said to have 709 points banked on the morning before the
week began — and confirmed by hand against the database. Asserting it meant
reimplementing the correct sum inside the checker, which then lived only in
`scripts/pickups_backtest.rebuild_posted`. It was worth doing and has been
done (`3025ab9`): the checker writes the sum out itself and compares it
against `load_team_week`, rather than calling the function it is checking.

**(a) The week's posted totals are the whole matchup period's.**
`app/pickups/state.py::_posted` reads `matchup_team_stats`, which has one
row per (matchup, team, category) and no day column. Evidence, team 86,
period 12, matchup 954:

```
posted PTS on the matchup row:                         709.0
actual PTS from started lineups, days 77..77 :         175.0
actual PTS from started lineups, days 77..80 :         401.0
actual PTS from started lineups, days 77..83 :         709.0
```

The stored week report for the morning of day 77 carries
`projected.PTS = 1560.9`: the finished week, plus seven more days of
projections on top of it. Every figure on the week page for a replayed day
— expected categories, the nine probabilities, the projected record — is
built on that.

This one is already known: `scripts/pickups_backtest.py`'s docstring names
it "THE MATCHUP TOTALS LEAK" and works around it by substituting
`state._posted` for the length of a backtest. The product's own path — the
precompute, the routes, the pages, the digest — still has it. (Fixed the
same day; see finding 1 below.)

**(b) The adds a team has spent count adds it had not yet made.**
`state._adds_in_period(session, team, first_day, last_day)` windows on the
matchup period's whole span rather than on today.

```
day 77 Through The Wire: adds_used is 7, but only 1 add had been made
                         by day 77 (adds_left 0 of 7)
day 80 Through The Wire: adds_used is 7, but only 5 adds had been made
                         by day 80 (adds_left 0 of 7)
```

**34 of the 39 team-days replayed were over-counted**, and **13 of them read
as having no adds left at all** when the team still had some. A team with no
adds left gets no plan: the digest says "no adds left this period, so there
is nothing to plan today", and the whole recommendation disappears. So this
does not merely skew a figure — a third of the mornings replayed produced no
advice at all for a reason that was not true.

This one is not in the backtest's list of known leaks, and the backtest does
not patch it. `_faab_spent`, thirty lines below `_adds_in_period` in the same
file, takes a `through_day` and has a docstring explaining exactly why.

**(c) and (d) The digest's two trailing counts have no far end.**
`app/digest.py::adds_in_window` and `league_section`'s wire tally both ask
for rows with `processed_at >= now - window` and never for rows older than
`now`:

```
day 80: the churn line counts 83 adds in the last 14 days;
        only 15 were made before 2026-01-08 15:00
day 80: the league section counts 609 wire moves in the last day;
        only 9 were made before 2026-01-08 15:00
```

**What could not be verified.** Three things:

- Whether the *projections* leak. `app.pickups.projection.per_game_line`
  filters season-to-date on `scoring_period < today` and the minutes tilt
  does the same, and `app.scoring.knowable` is documented to do likewise;
  that is code reading, not a measurement. Proving it would mean building
  the same report against a database truncated after day N, which this
  rehearsal did not do.
- The bid prices. `app.pickups.bids.bid_fit` fits on every claim the league
  has ever made, including claims from later in the replayed season. The
  backtest avoids this by never scoring a bid; the precompute does score
  them, so every `bid` in a replayed report is priced off the future. Known
  and noted in the backtest's docstring; not separately measured here.
- The league section's matchup lines. They show each matchup's stored
  category record, which for a played period is the final one — so the
  day-77 digest reports the result of a week that had not been played.

### 2. Determinism — **PASS**

Team 86, day 77, built twice through two jobs in two worker processes: both
payloads identical, `built_at` moved. Sharing no session, no projection
cache and no interpreter.

### 3. The digest fits the channel — **PASS**

26 digests, 697–1249 characters against Telegram's 4096 limit; none empty;
none thin on a day with games; none whose week section gave up. But see the
caveat above: with no listener data the roster and wire sections were empty
on every one of them, and those are the sections that grow. A live morning
after a busy night is not what was measured.

The one that comes closest to saying nothing useful is the All-Star break
(day 116), and it says so honestly rather than failing: "nothing clears the
bar (0.20 categories, or an empty day filled); 0 free agents were weighed".

### 4. Served from the store — **PASS**

Every page fetch through the `TestClient` for a replayed day came back 200,
byte-for-byte equal to the row the precompute had stored, and the glance
route's own `stored` flag was `true` every time. 39 fetches, median 0.04 s,
worst 0.12 s, against 44 s to build. Note that this needed the new `TODAY`
seam: the freshness rule is "built on today's date, for today's scoring
period", so without moving the route's clock a row built this afternoon
could never be fresh for a morning in January.

### 5. Queue semantics under concurrency — **PASS**, all four

- **No job ran twice.** Six worker processes over the two mornings wrote 30
  lines between them, one per job they finished: 30 distinct job ids, none
  appearing under two worker names, and every one of the 28 morning jobs
  `done` on its first attempt.
- **No duplicate enqueue.** The same morning's 14 jobs enqueued a second
  time created nothing: `created` false on all 14, the table unchanged.
- **Retried, then parked.** An injected `JobError` (a rehearsal-only fault,
  not a real kind) went `queued`, `queued`, `failed` over three attempts,
  ending `failed/3/the rehearsal's injected fault` — our sentence, visible
  in `last_error`. The clock was moved with `run_next(at=)` rather than
  waiting out the five- and twenty-minute backoffs.
- **A killed worker's job came back.** A worker was SIGKILLed while holding
  a precompute; the row stayed `running`, `jobs.reap` with the clock past
  `LEASE` returned it to `queued` with `the worker stopped while running
  it`, and a fresh worker finished it on the second attempt.

### 6. Edges — **PASS**

- **First day of a period:** day 77 (period 12) and day 112 (period 17).
- **Last day:** day 83 (period 12, "days 83-83 left (1)") and day 125.
- **A day with no NBA games:** day 116, in the middle of the All-Star break
  (2026 has six, scoring periods 116–121). The precompute stored both
  reports in 42.4 s, the pages served them, and the digest said what had
  happened rather than failing: "nothing clears the bar (0.20 categories, or
  an empty day filled); 0 free agents were weighed".
- **The first playoff period's first day:** day 140, period 20, 50.4 s, both
  reports stored, digest 697 characters.

Two smaller things the edges showed. A 14-day matchup period (17) carries a
budget of 14 adds, not seven, because the budget is one per day of the
period (`ADDS_PER_PERIOD_DAY`) — worth knowing before reading the adds
figures above. And the last day of a period produces the shortest digests
(818 on day 83, 697 on day 140), because there is one day left to plan for.

## The profile: how much of a precompute is the league's, not the team's

One precompute (`run_precompute`, team 86, day 77) under `cProfile` in an
interpreter that had built nothing, then a second one for another team in
that same process. The profiler costs roughly a third — 58.4 s here against
44.2 s for the same job unprofiled — so the shape is what matters, not the
absolute seconds.

**Cold, 58.4 s:**

```
58.409  run_precompute
57.988    build_payload (x2: the week report and the season report)
55.062      stream_recommendations
52.632        search
52.014          bids.bid_fit (x2)  ->  bids._fit
48.797            projection.per_game_line (16,337 calls)
48.307              scoring.knowable
```

**89% of a cold precompute is `app.pickups.bids.bid_fit`** — fitting the
league's historical winning FAAB claims, which means projecting a line for
every man on the wire on every day the league ever made a claim. Sixteen
thousand projection lines. It does not depend on the team. It does not
depend on the day either.

**Warm, 6.5 s** — the same job for another team in the same process:

```
 6.503  run_precompute
 6.454    build_payload (x2)
 3.788      draft.targets.category_distributions (x2)
 3.696        draft.era.category_trends (x2)
```

**59% of a warm precompute is `category_distributions`** — the league's
category history — and it is called **twice per team**, once for each
report kind, with no cache at all. The remaining ~2.7 s is genuinely this
team's: his wire search, the lineup optimiser, his own projections.

**So, of a 14-team morning on one worker** — the profiler's shares scaled
back to the measured seconds:

| | seconds | the same for all 14 teams? |
|---|---|---|
| `bid_fit`, the league's FAAB fit | ~43, once per process | yes, and for every later day too |
| `category_distributions`, 28 calls | 14 x ~2.9 = ~41 | yes — and paid every single time |
| genuinely this team's | 14 x ~2.1 = ~30 | no |
| **measured wall clock** | **114.9** | |

Two thirds of the morning is work that has one answer for the whole league.

`bid_fit` is already cached process-wide (`bids._CACHE`, keyed by
`league_season.id` and revalidated against the league's claim count), which
is exactly why the first job costs 44 s and the next thirteen cost 5. It is
also why **three workers bought 1.9x and not 3x**: each process pays the fit
again. Adding workers is close to useless until that work is shared.

**The cache boundary I would propose** (nothing was optimised in this pass):

1. **`category_distributions` / `category_trends` first.** It is the same
   answer for every team and both report kinds on one morning, it is ~41 s
   of a 14-team morning, and it has no cache whatever. The smallest version
   is a memo keyed on `(league_season, session)`, as
   `app.pickups.projection` already does for its lines.
2. **Lift `bid_fit` out of the process.** The fit depends on the league and
   nothing else, and the check for whether it is stale is already written
   (`_claim_count`). Stored as a row rather than a dict, one worker, three
   workers and the API process would all share it — and the API's first page
   after a restart would stop costing 44 s, which is what `warm_pages.py`
   was really for.
3. **The seam itself is `build_payload(session, league_season, team, kind,
   day)`.** Everything in it that does not read `team` is league-level. The
   natural shape is a per-`(league_season, day)` context built once in
   `run_precompute` and handed to each team's build, with the routes
   building one per request as they do today.

A worker that has been running since yesterday pays none of the cold cost,
so the production morning is nearer 14 x 5 s than 14 x 44 s. But a worker
restarted by a deploy (which docs/jobs.md tells you to do after every
deploy) pays it on the first team of the next morning.

## Findings, ranked

Ranked by what they would cost, worst first. "Opening night" asks the one
question that matters this month: **would this have hurt on a live morning
in October?** Nothing was fixed in the pass that wrote this — no finding
stopped the replay completing, and the brief was that the owner decides.
He decided the same day: 1, 2, 3 and 10 are fixed, and each says below what
it became. 4 to 9 stand as written.

### 1. The week's posted totals are the whole matchup period's — **FIXED** (`6acd33e`)

**Opening night: no. Everywhere else: severe.**
`app/pickups/state.py::_posted` reads `matchup_team_stats`, which carries a
period's final total with no day column. On a live morning ESPN's row holds
the running tally, so it reads right; on any replayed or backfilled day it
is the finished week. Evidence: matchup 954, posted PTS 709, which is
exactly the team's whole-period total (days 77..83) — 175 through day 77
and 401 through day 80. The day-77 report's `projected.PTS` is 1560.9, the
finished week plus seven more days of projection, and every category
probability and the projected record are built on it.

Already known and already worked around, but only in one place:
`scripts/pickups_backtest.py` calls it "THE MATCHUP TOTALS LEAK" and
substitutes `state._posted` for the duration of a backtest. The product's
own path still has it.

**Proposed fix:** give `_posted` the day, as `_faab_spent` thirty lines
below it already has, and sum the started lines through it — which is
exactly what `pickups_backtest.rebuild_posted` does and has verified against
ESPN on nine team-periods. Then delete the backtest's monkeypatch.
**Why it matters even though the live path is right:** a worker catching up
a missed morning, a page asked for `?today=` a past day (which the week page
supports), and every number the backtest measures the recommender by.

**Fixed as proposed, with one decision the proposal did not make.**
`state._posted` takes the day (`6acd33e`). It keeps ESPN's own row when the
database holds no `player_game_stats` on or after `today` — the genuinely
live case, where that row is the running tally and carries stat corrections
our box scores may not — and otherwise sums the started lines. The boundary
is **exclusive**: posted covers the period's days *before* `today`, because
`scoring_periods_remaining` begins at `today` and the projection adds that
day itself. The backtest's monkeypatch is gone. Verified against ESPN on
nine 2026 team-periods (teams 1, 3 and 11 over periods 1 to 3): the sum over
a whole period reproduces `matchup_team_stats` exactly, 0 mismatches.

The exclusive boundary is what the backtest's cap should have been and was
not. Its `_POSTED_CAP` was inclusive of day N, so day N counted once as
posted and again as projected; 584 of the backtest's 616 team-decision
points therefore had a different posted total afterwards. See
docs/pickups_backtest.md for what that moved.

### 2. The adds a team has spent include adds it has not made yet — **FIXED** (`d4f5d97`)

**Opening night: no. Everywhere else: severe, and it silences the product.**
`state._adds_in_period(session, team, first_day, last_day)` counts over the
matchup period's whole span. 34 of 39 team-days over-counted; 13 of them
read as having spent the whole budget:

```
day 77 Through The Wire: adds_used is 7, but only 1 add had been made
day 80 Through The Wire: adds_used is 7, but only 5 adds had been made
```

A team with no adds left gets no plan at all — "no adds left this period, so
there is nothing to plan today" — so this does not merely skew a number, it
turns the recommendation off, on a third of the mornings replayed. Unlike
finding 1 this one is **not** in the backtest's list of known leaks, and the
backtest does not patch it.

**Fixed as proposed** (`d4f5d97`): `_adds_in_period(session, team, first_day,
through_day)`, bounded at `today`, which is `_faab_spent`'s own bound. The
docstring says why the two must agree — an add and the money it cost are one
transaction. Day 77 for team 86 now reads 1 add used of 7 and 6 left, and the
day-78 digest, which used to say there was nothing to plan, carries a
two-move plan. The backtest's add budget never binds (two decision points a
period against a budget of seven), so nothing there should move on this
account; docs/pickups_backtest.md records what the re-run found.

### 3. The digest's two trailing counts have no far end — **FIXED** (`86bb303`)

**Opening night: no. On a replay: wrong by a factor of sixty.**
`app/digest.py::adds_in_window` and `league_section`'s wire tally both ask
for `processed_at >= now - window` and never for `<= now`:

```
day 80: churn line 83 adds in the last 14 days; truly 15
day 80: league section 609 wire moves in the last day; truly 9
```

**Fixed as proposed** (`86bb303`): `processed_at <= now` on both. The digest
is the one surface a manager reads without a page in front of him, and "609
moves on the wire in the last day" is the sort of number that destroys trust
in everything above it. The day-77 digest now says 9 moves and 14 adds; the
day-78 one, 10 moves and 15 adds.

Worth recording because it nearly hid the fix: the script's own assertion
for this one was comparing two queries written inside the script, one open
and one closed, and so reported a difference whatever the digest did. It
went on failing after the digest was right. It now reads the number back off
`league_section`'s rendered line. An assertion that does not ask the product
anything is worse than no assertion, because it looks like one.

### 4. `category_distributions` is recomputed for every team, twice

**Opening night: no, but it is most of the morning's remaining cost.**
3.79 s of a 6.5 s warm precompute, called once per report kind per team,
with no cache: about 41 s of a 115 s league morning, for an answer that is
identical across all 28 calls. See the profile above for the proposed
boundary. Not urgent at 14 teams; it is the first thing that will not scale
to a second league.

### 5. Extra workers barely help, because each pays the league fit again

**Opening night: no. It is a sizing fact worth knowing before you act on it.**
Three workers on a 14-team morning: 60.6 s against 114.9 s on one. 1.9x, not
3x, because `bids._CACHE` is per process. If the morning ever needs to be
faster, sharing the fit comes before adding workers; adding workers first
would look like it barely worked.

### 6. The digest rebuilds the report the precompute has just stored

**Opening night: low, but it is the difference between a 3-second digest and
a 43-second one.** `app/digest.py::week_plan` calls `stream_recommendations`
itself. docs/jobs.md names this as a deliberate decision ("Reading the stored
row is a follow-up"), and the measurement is the argument for doing it: the
owner's digest took 42–43 s in a cold process and 1.8–3.0 s in a warm one.
Two consequences beyond the time: the morning does the same work twice, and
the digest and the page can disagree if anything moves between them.

**Proposed fix:** read the stored row, falling back to a live build when
there is none — which is what the routes already do.

### 7. The rehearsal cannot say anything about a digest with news in it

**Opening night: unknown, which is the finding.** Every digest measured had
an empty roster section and an empty wire section, because a played season
has no listener data. Those are the two sections that grow with the news,
and they are the reason there is a limit to worry about at all
(`ROSTER_EVENT_LIMIT` and `WIRE_EVENT_LIMIT` exist for it). 934 characters
of a 4096 budget is reassuring but it is not the measurement.

**Proposed check:** once the listener has a week of 2027 data, render the
owner's digest on the busiest night of it and measure again.

### 8. The morning's job *ordering* was never exercised

**Opening night: unknown.** The real morning is `status_pass`, then a
`precompute` per team and a `digest` per member, each hanging off the pass
by `depends_on`. The pass reaches ESPN and was out of scope, so the
rehearsal enqueued precomputes with no prerequisite. What was not tried on
real rows: the precompute waiting for a pass, and a pass that fails for good
taking the whole morning's digests down with it (`fail_orphans`).
`tests/test_jobs.py` covers both against seeded rows.

### 9. `FCP_OWNER_EMAIL` is unset locally, so the owner is `owner@localhost`

**Opening night: low, and docs/jobs.md already says to check it.** Single
mode's `create_app` wrote a `users` row during the rehearsal, and with
`FCP_OWNER_EMAIL` unset it used `accounts.OWNER_FALLBACK_EMAIL`. Worth
checking on the VPS before the switch-over rather than after, because the
owner's digest is routed by that address.

### 10. The rehearsal's own rows are still on the queue — **FIXED** (`3025ab9`)

**No severity; said so it is not a surprise.** 44 `jobs` rows (43 done, one
the deliberately parked fault) and 74 `team_reports` rows for 2026 remain on
the local database. Every job is marked `rehearsal: true` and labelled
`rehearsal:...`; the reports are for days in the past, so no route will
serve one as fresh. Nothing was deleted.

**Since:** those 44 rows were deleted on 2026-09-21, after checking that
every row on the table was one of them — the `jobs` table is now empty on
this machine — and `rehearse_week.py` clears up after itself by default.
At the end of a run it deletes the rows carrying `rehearsal: true` **and its
own run tag**, which is the same narrowing its workers claim under, so it
can no more delete a real job than run one. `--keep` leaves them for
inspection. The `team_reports` rows stay either way, deliberately: they are
ordinary stored reports for days in the past, no route serves one as fresh,
and the next run of the same day overwrites them.

## What was fixed, and what the re-run showed

Findings 1, 2, 3 and 10 were fixed on 2026-09-21 — the owner's call, all
four together — and the check that failed on all of them was re-run on the
same command:

```
scripts/rehearse_week.py --season 2026 --period 12 --teams all \
    --only-days 77,78 --workers 3 --checks
```

**Check 1, before.** The script asserted three of the four; the posted
totals were found by hand and are written here in the same shape so the two
columns can be read together.

```
1_no_look_ahead: FAIL
day 77 Through The Wire: posted PTS 709 on the matchup row, the whole
                         period's total; 175 was scored on day 77 itself
day 77 Through The Wire stream: adds_used is 7, but only 1 add had been
                         made by day 77 (adds_left 0 of 7)
day 80 the digest: the churn line counts 83 adds in the last 14 days;
                         only 15 were made before 2026-01-08 15:00
day 80 the digest: the league section counts 609 wire moves in the last
                         day; only 9 were made before 2026-01-08 15:00
```

**Check 1, after:**

```
1_no_look_ahead: PASS, 0 rows
  posted    28 team-days compared against an independent sum of the started
            lines on the period's days before today; no difference anywhere
  adds      28 team-day payloads compared against the adds actually made by
            then; no difference anywhere
  churn     day 77: 14 adds in the last 14 days.   day 78: 15
  wire      day 77: 9 moves on the wire in the last day.   day 78: 10
```

The day-77 example in full. Team 86 (Through The Wire), period 12, days
77–83:

| | before | after |
|---|---|---|
| posted PTS, morning of day 77 | 709 | **0** — the week has not started |
| posted PTS, morning of day 78 | 709 | **175** — exactly day 77's scoring |
| posted PTS, morning of day 80 | 709 | **364** — days 77–79 |
| posted PTS, morning of day 81 | 709 | **401** — days 77–80 |
| `adds_used` on day 77 | 7 of 7 | **1 of 7**, 6 left |
| `adds_used` on day 80 | 7 of 7 | **5 of 7**, 2 left |
| the churn line on day 77 | (day 80: 83) | **14** |
| the wire line on day 77 | (day 80: 609) | **9** |

Note the boundary. Posted on the morning of day 77 is **zero**, not the 175
this document's finding (a) quoted: 175 is what day 77 itself scored, and
day 77 is still to be played when that morning's report is built. It shows
up on day 78, which is where it belongs — and the 401 this document quoted
for days 77–80 appears on the morning of day 81, not day 80, for the same
reason.

What it bought, in the product rather than in a number: the day-78 digest,
which read "no adds left this period, so there is nothing to plan today",
now carries a two-move plan.

**Everything else the re-run measured** was where it was. Nine checks
passed, none failed. 28 precomputes over two mornings, 58.6 s wall on three
workers for each 14-team day (against 60.6 s before), median 5.5 s a
team-day, four digests of 980–1223 characters, six page fetches at a median
of 0.04 s. The queue-semantics four and determinism passed again.

## Running it again

Exactly what was run, in this order:

A run now deletes its own `jobs` rows when it finishes and says how many;
`--keep` leaves them.

```
.venv/bin/python scripts/rehearse_week.py --season 2026 --period 12 \
    --teams all --only-days 77,78 --workers 1,3 --checks \
    --run leagueA --out OUT/league                                    # 5m09
.venv/bin/python scripts/rehearse_week.py --season 2026 --period 12 \
    --teams 86 --workers 3 --profile --run teamB --out OUT/period12   # 6m35
.venv/bin/python scripts/rehearse_week.py --season 2026 --period 17 \
    --teams 86 --only-days 112,116,125 --workers 1 \
    --run edgeC --out OUT/allstar                                     # 3m21
.venv/bin/python scripts/rehearse_week.py --season 2026 --period 20 \
    --teams 86 --only-days 140 --workers 1 --run edgeD --out OUT/playoff  # 1m01
```

`--checks` adds the queue-semantics four (they need real concurrency to mean
anything, so they belong on the 14-team run). `--profile` re-enters the
script in a fresh interpreter, because cold is the case worth profiling.

Each run writes `rehearsal.json` (every timing and every check, with the
offending rows), one `day-NNN-digest-owner.txt` and
`day-NNN-digest-member.txt` per day, `workers.jsonl` (what each worker
finished, which is how "no job ran twice" is proved), and with `--profile` a
cold and a warm `profile-*.txt`.

It is safe to run against the local database and nowhere else: it never
reaches ESPN, never delivers, and writes only `jobs` and `team_reports`.
