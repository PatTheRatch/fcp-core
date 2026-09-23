# Intake: giving a new league its own numbers

**Written:** 2026-09-22. **Status:** built; the ESPN-facing half is
**unexercised**, for the reason in "What has not been run" below.

Code: `app/calibration.py` (the table and the accessor), `app/intake/`
(the chain, the measurements, the message), `app/jobs.py` and
`app/job_kinds.py` (the eight new kinds and the queue's priority),
`app/schedule.py` (the trigger), `app/api/leagues_admin.py` and
`app/api/static/connections.html` (the account page); migration
`0027_league_calibrations`. Tests: `tests/test_calibration.py`,
`tests/test_intake.py`.

## Why

Every number the recommenders lean on was measured on **one** league — Full
Court Press, ESPN 3853870, eight seasons — and then written into the code as
a constant:

| constant | value | measured in |
|---|---|---|
| `TYPICAL_PICKUP` | 0.06 categories a week | `app/scoring/replacement.py` |
| `OPENED_PLACE` | 0.38 | docs/streaming_lane.md |
| `STREAM_HURDLE` | 0.20 | docs/pickups_backtest.md, chosen 2026-09-18 |
| `SEASON_HURDLE_PAID` | 0.20 | the same, chosen 2026-09-21 |
| `SEASON_HURDLE_FREE` | 0.10 | the same |
| the trade record | 25 of 55 deals | docs/trades.md §7 |

A number measured on one league is not a fact about another. The product is
meant to be sold (docs/product.md), so a second league has to get the same
treatment automatically: every season ESPN will give us, the NBA schedules
behind them, each of those measurements run on **its** history, and an email
saying it is ready with its own numbers in it. Until its numbers exist, or
where its history is too thin to measure one, it falls back — and every page
says which it is using.

## The table, and the one way in

`league_calibrations`: one row per (`league_id`, `key`), and `league_id` null
for a **pooled** row. `value` is categories a week (null for `trade_record`,
which is a table rather than a number); `payload` is whatever the measurement
produced — the sweep grid, the IQR, the 2×2; `n` is the sample the value
rests on in that key's own unit; `source` is one of four; `note` is one plain
sentence a page prints under the number; `run_seconds` is what the run cost.

Everything reads it through one accessor:

```python
calibration(session, league_id, key) -> Calibrated(value, source, n, note, payload)
```

and a page or a report that wants several reads them together (`bars`).

## Where a number comes from, in order

1. **`owner`** — the league's own manager set it on the account page, with
   one line of reason. A re-measurement never overwrites it.
2. **`measured`** — the intake measured it on this league's history **and
   the sample clears this key's minimum**. Under the minimum the row is kept
   and shown, and is not used.
3. **`pooled`** — the n-weighted aggregate of every league measured so far
   whose settings match this one's, over at least **two** leagues.
4. **`default`** — the constants above, which are now the fallback rather
   than the answer.

A manager's own choice beats a measurement of his league; a measurement of
his league beats other leagues like his; other leagues like his beat one
league that is not his at all.

## The minimums

| key | minimum | unit | why |
|---|---|---|---|
| `typical_pickup` | 100 | adds | this league runs 475–973 a season; below a hundred the median moves more than the thing it measures |
| `opened_place` | 100 | team-periods | docs/streaming_lane.md pooled 1,536 |
| `stream_hurdle` | 200 | decision points | docs/pickups_backtest.md swept 616, and the tuning rule turns on a no-move **rate** |
| `season_hurdle_paid` | 200 | decision points | the same sweep |
| `season_hurdle_free` | 200 | decision points | the same sweep; the free bar has never been measurable on its own |
| `trade_record` | 20 | deals | §7 measured 55 and reported that 55 is already a coin's worth of evidence |

## The pooled rows, and their settings

A pooled row is grouped on the settings that move these numbers, and on
nothing else: **team count, roster size (starting places + bench + IR), adds
a period allows per day, FAAB or not, and the set of scored categories**.
Leagues of different shapes are never averaged together.

It is built from other leagues' **numbers** — their `value` and their `n` —
and never from a roster, a name or a transaction of theirs. What it carries
is the settings it is keyed on and how many leagues are in it; what a league
reading it is told is "the pool of 3 leagues like yours".

**With few leagues it is a table by settings, and it stays a table.** No
relationship is fitted across settings — the hurdle against the team count,
say — until there are at least ten leagues (`FIT_LEAGUES`). Below that a
fitted line is noise with a slope, and the code that would fit it is
deliberately not written.

## The chain

Eight jobs, each waiting on the one before it (`depends_on`), so a step that
fails parks with its own error and nothing after it runs at all
(`jobs.fail_orphans`). Enqueued when a league connection asks for an ingest
(`ingest_requested_at`, the trigger `scripts/enqueue.py --schedule` already
watches), by `scripts/enqueue.py --intake ESPN_LEAGUE_ID`, or by the account
page's button.

| step | does | timing here |
|---|---|---|
| `intake_ingest` | every season the login can read, newest first | minutes a season, unmeasured (below) |
| `intake_schedule` | the NBA schedule behind each of them | seconds a season, unmeasured |
| `intake_replacement` | `typical_pickup` | **2s**, nine seasons |
| `intake_lane` | `opened_place` | **3s**, nine seasons |
| `intake_hurdles` | the sweep, and the three bars | **the long one: about forty minutes a season**, so five or six hours over nine |
| `intake_trades` | `trade_record` | **94s**, nine seasons |
| `intake_pool` | the pooled rows | under a second |
| `intake_done` | the summary, and the email | under a second |

The timings are the run of 2026-09-22 on a copy of the live local database
(ESPN 3853870, nine seasons 2019–2027, 10 to 16 teams a season), with the two
ESPN-facing steps stubbed as already done.

**The sweep is hours, not the ninety minutes it was budgeted at.** The
published one-season run in docs/pickups_backtest.md is 5,138 seconds for 616
decision points across *two* tilt settings; one tilt on one season is
therefore about forty minutes, and this league has nine seasons of roughly
that size. That is why it is resumable a season at a time and why it carries
the queue's lowest priority: it is the difference between a league's numbers
arriving in an afternoon and a league's manager waiting a morning for his
own reports.

**Why eight kinds rather than one job with eight stages.** The queue already
knows how to hold a step back until the one before it is done, how to fail
the rest when one fails, how to retry with a backoff, and how to show a
person which step a league is on. A stage machine inside one job would be all
of that written again, badly, inside a two-hour lease.

### The rules

- **One intake at a time per league.** `dedupe_key` is the kind, the league
  and the UTC day of `run_after` with the label `intake`, so a timer that
  fires twice adds nothing; and `enqueue_intake` refuses outright when a
  chain is running.
- **A league may be measured again once a day.** The sweep alone is ninety
  minutes, and nothing about a
  league's own history changes fast enough for a second run in a day to say
  anything new.
- **Leagues one after another, not in parallel against ESPN.** The
  ESPN-facing steps run in the one worker that takes the queue.
- **Every step is idempotent.** A schedule already stored is skipped, a
  season already swept is read out of the payload, and a measurement
  overwrites its row. A step run twice writes what it wrote.
- **The sweep is the queue's lowest priority** (`jobs.LOW`, 100, against
  everything else's 0, read before `run_after`), so a morning's precomputes
  are never behind it.
- **It is resumable a season at a time.** Each season's grid goes into the
  job's own payload as soon as it is finished, so an attempt that dies in the
  fifth season resumes at the fifth.
- **A failure is never "ready".** The email names the steps that did not
  finish and says what each number is falling back to.

### What is refused

A league this code does not model is refused at the first measuring step,
with `retry=False`, and nothing else is enqueued. The check is made from what
the ingest stored — `league_seasons.scoring_type` and the category rows — and
the sentence says what the league **is**:

> this league is scored on points, head to head, and these reports only
> understand head-to-head categories; they are not supported yet

> this league scores 8 categories and these reports are built for the nine;
> they are not supported yet

Points and rotisserie leagues are most of what is out there
(docs/league_survey.md: every public league that could be read scored
points), so this is the common case and not an edge one. Supporting them is a
product decision, not a bug.

## The email

Plain words, in the house style the sign-in email wears
(`app.mail.render.plain_html`), sent through the path everything else uses:
the connector's own verified addresses (`app.channels.deliver`). Not the
digest, and not built by the digest's renderer.

**No number without its n.** Every line is the number, what it is, and where
it came from:

```
Patriot Games (ESPN 3853870)

Your league has been measured on its own history. Here is every number the
recommendations lean on, and where each one comes from.

Seasons read: 8 seasons, 2019 to 2026.

YOUR NUMBERS

  What a pickup is worth: 0.06 categories a week — measured on this league, 924 adds
  What an open place is worth: 0.38 categories a week — measured on this league, 1,536 team-periods
  The bar for a move this week: 0.20 categories a week — your choice, 2026-09-18: ...
  The bar for a move that costs FAAB: 0.20 categories a week — your choice, ...
  The bar for a free add: 0.10 categories a week — your choice, ...
  The trade number's record: 55 deals — below

The bar a move has to clear this week is 0.20 categories. A move under it is
still shown, with its number, and labelled: the bar says which moves are
worth a look, it never hides one.

You can change any of the three bars here: <FCP_PUBLIC_URL>/account/connections

HOW MUCH TO TRUST THE TRADE NUMBER

  This number is a forecast, and here is its record. Over the 55 trades in
  this league's history that can be replayed, ...
```

The trade record is a paragraph rather than a line, so it gets its own block
and the list keeps the one line per number it is there for.

and, when a step failed:

```
WHAT DID NOT FINISH

  backtesting the bars (the long one): <the sentence it parked with>

Those numbers are using the fallback in the list above until that step runs
again. You can ask for it from the same page.
```

`scripts/enqueue.py --intake-email ESPN_LEAGUE_ID` prints exactly that
message for a league and opens no connection to anything.

**What it may never say:** anything about another league — not a name, not a
number, not a count of its teams. The pool is the only thing another league
contributes, and the only thing said about it is how many leagues are in it.
And nothing of ESPN's own words or an exception's text, which is the rule
every message here has (docs/jobs.md, "Secrets").

## The account page

Account → Connections gains **"Your league's numbers"**: each key, its value,
its source, its sample and when it was measured, for every league the viewer
is a member of. A number the league's own measurement is not being used for
shows that measurement beside it, with its sample and the minimum it did not
clear, because the page's job is to say what is being used **and** what else
is known.

For the league's **owner**, and the three bars only: a field, one line of
why, and a Set button; and a "Use the measurement" button to drop his own row
again. The other three keys are measurements of what happened rather than
choices about what to do, and nothing there offers to overrule one — the
route refuses them with a sentence saying so.

Beside it: **Measure again**, which enqueues the chain. Rate-limited to once
a day and disabled while one is running, with the step it is on in the words
of `STEP_WORDS` ("backtesting the bars (the long one)").

## What has not been run

**The two ESPN-facing steps are unexercised.** `intake_ingest` and
`intake_schedule` were written, typed, linted and unit-tested against fake
handlers, and neither has ever reached ESPN: the work that built them opened
no connection to it. What is unproven is the season probe's behaviour against
real responses — a 404 read as "not offered", a 401 read as "private, stop
and say so" — and the ingest of a season ESPN serves in a shape this code has
not seen. The six measuring steps have been run for real, end to end, on the
live local database.

## Deploying it

On the VPS, as `aisha` in `/opt/fcp-core`, at a quiet moment:

```
git pull
./.venv/bin/pip install -q -e ".[dev]"
./.venv/bin/python -m alembic upgrade head        # 0027
sudo systemctl restart fcp-core-api.service
sudo systemctl restart fcp-core-worker.service    # if the queue is switched on
```

**The seeded rows land through the migration.** Its data step is an
`INSERT ... SELECT` on `leagues` for ESPN 3853870, so on the VPS — where that
league is ingested — the six rows appear with the migration and Full Court
Press's pages print exactly what they printed before. On a database where
that league has never been ingested it writes nothing, and every league there
reads the defaults, which are the same numbers.

`alembic downgrade 0025` drops the table and the priority column, deletes any
intake or injury job, and narrows the jobs CHECK back. Nothing else reads the
table, so the pages fall back to the constants and print the same numbers
again.

## Decisions

- **The scripts are imported, not copied.** `scripts/streaming_lane.py`,
  `scripts/pickups_backtest.py` and `scripts/trade_calibration.py` are where
  each measurement was argued out and written up; a second implementation of
  a measurement is a second measurement, and the two would disagree inside a
  month. What `app/intake/measure.py` adds is a league to narrow by, a season
  loop and a result the job can store. Three of them gained a `league_id`
  parameter for exactly that, and `pickups_backtest` a season parameter,
  since `SEASON = 2026` was a module constant.
- **The backtest's tuning rule is a function** (`tuning_picks`), so the
  write-up's section 4 and the row the intake stores cannot reach different
  conclusions from the same grid.
- **The trade note is a function of the run** (`app.trades.calibration.trade_note`),
  so a second league's page carries a record of its own trades. Applied to
  the published run it returns `CALIBRATION_NOTE` character for character,
  which is what `tests/test_trades.py` holds it to. The verdict against the
  coin is read off the exact binomial interval rather than asserted, so a
  league where the evaluator lands outside the range is told so in either
  direction.
- **A league keeps its own revision history across a re-measurement.** The
  clause "this number used to run about four tenths above what those deals
  really did" is a fact about revision R1 of the evaluator, not about the run
  being made, so `intake_trades` reads the revision figures off the stored
  row and hands them to the new run (`measure.REVISION_ERRORS`). Without
  that, re-measuring this league would quietly drop a sentence its page had
  yesterday. A league measured for the first time has nothing to carry, and
  its note says only where the number stands.
- **A measurement with nothing to measure is not a failure.** A league with
  one unplayed season has no adds, no lanes, no decisions and no trades; the
  step writes no row, says so in its note, and the number falls back.
- **The hurdle keys can be measured with no value.** The sweep's tuning rule
  picks nothing on the streaming side of this league every time it is run —
  a move that fills an empty day is recommended whatever the bar. The row
  then records the grid and the sample, the page says the sweep chose
  nothing, and the fallback carries on.
- **`n` for `typical_pickup` is the season the value came from**, not every
  season read: the rule is "the lowest recent season's median", so the number
  rests on that season's adds and the rest of the table is in the payload.
- **A priority column rather than a later `run_after`.** A long job pushed
  into the future would eventually come due and then be in front of
  everything; priority keeps it behind whatever is due, always.
- **The seed is literal in the migration**, importing nothing from `app`: a
  migration that imports application code writes whatever that code says next
  year, and this is a record of what was measured in September 2026.
  `tests/test_calibration.py` holds the literal against the constants.
