# Replay status: what a played morning is allowed to know about who is hurt

**League:** 🏀 🤓 Patriot Games 🇺🇸 vs 🇬🇧 (ESPN 3853870) — nine-category H2H
**Built:** 2026-09-24
**Applied in:** `app/pickups/status_source.py` (the source order), `app/pickups/state.py`
(`status_on`, which `build_players` calls), `app/mcp/provenance.py` and
`app/api/pages.py` (which source an answer read)
**Companions:** [`injuries.md`](injuries.md) (the reports, the schema and the
point-in-time rule), [`stash_mode.md`](stash_mode.md) (the OUT-man rule this
finally lets fire), [`availability.md`](availability.md) (the play rates by
status, and the decomposition §6 here answers),
[`inseason_rehearsal.md`](inseason_rehearsal.md) (the four look-ahead leaks,
and the fifth place one could have hidden),
[`spread_revision.md`](spread_revision.md) (the shape of a declared revision),
[`pickups_backtest.md`](pickups_backtest.md) §0.2,
[`projected_record.md`](projected_record.md) §0 R5, [`trades.md`](trades.md) §0

---

## Why

Yesterday's stash mode (`stash_mode.md` §0) re-ran the three calibrations and
every one came back byte-identical. That was not evidence that the rule was
right. It was proof that it never fired.

On a replayed day the engine read a man's injury status from
`player_status_snapshots` — ESPN's status as the listener saw it — and the
listener only ever runs for the season in progress. All 1,095 stored rows are
2027. So every replayed season counted every man fit, `RULED_OUT_STATUSES`
matched nobody, and three queued changes could not be calibrated at all: the
stash rule, the availability study's status-conditional games term, and the
games-discount revision.

The league publishes the missing history itself, and since 2026-09-22 we hold
it: `injury_reports`, 20,776 lines for 2026 (2025-10-21 to 2026-04-12), read
point-in-time through `app/injuries.py`. Nothing read it. This wires it to the
one place the engine reads a status.

---

## Declared

Written into `app/pickups/status_source.py`'s docstring and this section
before any calibration was re-run, and not tuned afterwards.

### The status source, in order

1. **ESPN's own snapshot, when the listener had taken one by that morning.**
   The live season's answer, unchanged: the newest `player_status_snapshots`
   row per player, status and `expected_return_date` as ESPN gave them.
2. **Otherwise the NBA's official report as of that morning** — the league's
   nine o'clock Eastern report, read at `app.injuries.morning_of`, which is
   **ten o'clock Eastern**. The hour is `app.injuries`' own and not a choice
   made here: the hourly reports were stamped at half past the hour their URL
   named, so a read at nine would have found the eight o'clock report and
   missed the very snapshot `--snapshots morning` stores (`injuries.md`, "the
   morning rule"). Ten o'clock is the read that **sees** the nine o'clock
   report, under both of the league's cadences.
3. **Otherwise silence**, which every caller reads as "nothing is known
   against him" — the null status a replayed day has always carried. It is
   not a claim that he is fit. The league only names players whose team plays
   that day, so on an off day every man on that team is silent.

**The gate for (1) is a snapshot observed at or before that morning, not a
snapshot existing.** On a live morning that changes nothing: the listener's
last pass is older than ten o'clock today, the gate opens, and the freshest
snapshot per player is used exactly as before. On a **replayed day of the
live season** — a page asked for `?today=52` in March — it is the difference
between a leak and a read: the gate shuts, and the day falls through to the
league's own report for that morning rather than reading next month's status.

### The mapping

The league prints exactly five words (`injuries.md`) and the engine speaks
ESPN's, so the five are mapped across and `RULED_OUT_STATUSES` decides what it
always decided:

| the league prints | the engine reads | in `RULED_OUT_STATUSES`? |
|---|---|---|
| `Out` | `OUT` | **yes** |
| `Doubtful` | `DOUBTFUL` | no |
| `Questionable` | `QUESTIONABLE` | no |
| `Probable` | `PROBABLE` | no |
| `Available` | `ACTIVE` | no |
| silence | null | no |

**Of the league's five, `Out` alone is ruled out.** `RULED_OUT_STATUSES` is
`{OUT, SUSPENSION}` and is not widened here. Widening it to `Doubtful` — the
league's own word for about a one-in-four chance, measured at a 5.88% play
rate over 919 lines in `availability.md` §1 — is the availability term's
decision and belongs to its own declaration, with its own before and after.

**No `SUSPENSION` is produced.** The league has no such status: a suspension
is an `Out` whose *reason* reads `League Suspension`. Mapping the reason would
change nothing, because both words are in `RULED_OUT_STATUSES`, so the mapping
reads the status word alone and can be stated in six rows and checked in one
test.

**No return date comes from a report.** `availability.md` §2 searched the
first line of all 6,987 Out runs for a timeline word or a printed date and
found **none** — not rarely, never. So a report-sourced status carries
`expected_return_date` None, and an OUT man is priced by the return prior of
`app.pickups.returns` rather than by a date. That is the path this whole job
exists to make fire.

### Point-in-time, provably

Only reports with `reported_at <= that morning`, and only lines whose
`game_date` is that day or later. A replay on day N never reads a report
published after ten o'clock Eastern on day N. §3 below is the proof.

### Nothing else moves

`ESPN_AVAILABILITY`, `SPREAD_SCALE`, the hurdles, `OPENED_PLACE`,
`TYPICAL_PICKUP`, `RULED_OUT_STATUSES` and the calibration notes are
untouched. This job changes **what a replayed morning knows**, not what the
engine does with it.

---

## 1. Where it is built, and what it costs

`app.pickups.state.status_on(session, season, day)` is the one place this
engine reads a status. `build_players` calls it and every recommender, report,
trade and MCP tool goes through `build_players`, so the source order is
settled once rather than per caller. The scoring period is turned into a
calendar date by the season's own schedule (`season_calendar`), which is the
anchor every other date on a report already uses.

**Two queries a morning for the statuses themselves, memoized on the
session** — one for the day's report lines and one for the snapshot gate. A
replayed morning is asked about by fourteen teams and a wire, and holds of the
order of fifty status lines, so the whole day is read once and held rather
than filtered per roster.

`season_calendar` is *not* memoized and runs once per `build_players` call: it
is a single min/max aggregate over `pro_team_games`, which is the same read
every other date on a report already does. Holding it on the session too is a
tidy-up and is named in "Not done" rather than made, because it would change
the script that produced the numbers below without changing a number.

**`app.inseason.startable` is deliberately not rewired.** It reads the
snapshots directly, and its own docstring says a backtest "cannot use it as it
stands": `roster_week` is forward-looking and every day it reports on is at or
after the day the snapshots were taken. Nothing in a replay calls it. If it
ever grows a historical branch, `status_on` is what it should call.

## 2. What a replayed 2026 morning actually knows

Measured on the pickup backtest's own 44 decision mornings, and on all 174
scoring periods of the season.

| | |
|---|---|
| decision mornings whose source was the NBA's report | **43 of 44** (one had no report the morning could see) |
| scoring periods with no report the morning could see | 10 of 174 |
| status lines placed on a player, per morning | mean **42.5**, median 42, range 0–91 |
| names the league printed that no player row could be placed on | mean **14.8** a morning |
| share of named lines placed, over the 44 mornings | **74.2%** (1,870 of 2,520) |
| rostered man-mornings with a status at all | **7.9%** (629 of 8,001) |
| rostered man-mornings the league had **Out** | **3.3%** (266 of 8,001) |

The 74.2% is `injuries.md`'s 73.7% match rate, re-measured on the mornings
this engine reads, and the headline is the misleading one there too: the gap
is a **population** gap and not a matching failure. Nearly every unplaced name
is a G-League two-way or on-assignment man who has never been in ESPN's
fantasy pool. Such a man reads as fit here, costs nothing, and is carried as
the `unmatched` figure in every answer's provenance so a reader can see how
many there were.

The engine's words over those 44 mornings: `OUT` 1,196, `QUESTIONABLE` 413,
`PROBABLE` 102, `ACTIVE` 86, `DOUBTFUL` 73.

**Seven point nine per cent is the size of this change**, and it is worth
holding on to while reading §4 and §6: on a replayed morning nine men in ten
are silent, because their team is not playing or the league has nothing to
say. Everything that moved below moved on the other one in ten.

## 3. The look-ahead proof

Four leaks were found and fixed in `inseason_rehearsal.md` §1. This is the
fifth place one could have hidden, and the reason it does not.

**The test.** `tests/test_pickups_status_source.py` builds one morning with
two report lines about the same game: a nine-thirty report that has the man
`Out` and a five-thirty upgrade that has him `Available`. The replay of that
morning reads the first and not the second, though the second is newer; the
same fixture read the next morning does see it, which is what makes the
assertion a bound on the moment rather than on the row. A second test pins
the other half of the rule — a line filed about day 2's game says nothing
about day 3 — and a third pins the gate: a snapshot observed after the
morning does not open it.

**On the real database**, day 52 of 2026 (11 December 2025) reads the report
stamped `2025-12-11 09:30 ET` and is bounded at `10:00 ET`. Day 150 reads
`09:00 ET` under the quarter-hourly cadence. Every decision morning's
`reported_at` is at or before its own `read_as_of`; the provenance carries
both, so the claim is checkable from any answer rather than only from here.

**What is still not proven.** That the *projections* do not leak is still
code reading rather than a measurement (`inseason_rehearsal.md`, "what could
not be verified"), and the bid prices are still fitted on the whole season.
Neither moved.

## 4. The three calibrations, re-run whole on 2026

Worse first, as the rule is. The "before" column is the same command run
against `HEAD` — the state `stash_mode.md` §0 published on 2026-09-24 — on the
same local database, within the hour.

**One of the three got worse on its headline, one got better, and one got
better on every column.** In order: the projected standings' Brier rose five
ten-thousandths while its projected final record improved; the trade record
moved the right way and almost entirely in 2026, the one season whose reports
are loaded; the pickup replay's streaming half improved on moves named,
categories delivered, win rate and no-move rate at once.

### 4a. The projected standings: mixed, and the Brier is the worse half

| | before | after |
|---|---|---|
| **overall Brier** (lower is better; a coin is 0.2500) | **0.2179** | **0.2184** |
| right side of this week's matchup | 0.584 over 5,320 team-weeks | **0.583** |
| final record off by, made at the quarter | 8.42 of 171 | **8.13** |
| final record off by, made at halfway | 7.16 | **6.32** |
| final record off by, made at three-quarters | 4.79 | **5.17** |
| what it called at 0.9–1.0 happened | 0.804 (n = 317) | **0.835** (n = 370) |
| what it called at 0.0–0.1 happened | 0.196 (n = 317) | **0.165** (n = 370) |

**The headline number got slightly worse and the one a manager feels got
better.** The Brier rose five ten-thousandths, and the matchup-winner hit rate
fell a thousandth — both inside anything this run can separate from noise, and
both published because they are what the run says. The projected final record
at halfway, which is the figure `projected_record.md` leads with and
`SHORT_NOTE` prints, improved from 7.16 categories to **6.32** — the largest
move that record has made since the spread was widened; the three-quarter mark
went the other way, 4.79 to 5.17.

The two ends are the interesting part. The model now reaches them more often —
370 calls above 0.9 against 317 — and is *better* calibrated when it does:
what it called above 0.9 used to happen 80.4% of the time and now happens
83.5%. Knowing who was Out sharpens the extremes and costs a little in the
middle, which is the shape one would expect from a term that only speaks about
one man in ten.

`scripts/projected_calibration.py --season 2026`, **73s before and 75s
after** — against the 39s and 37s `stash_mode.md` recorded, because this
machine was running two pickup backtests throughout. Both arms were run twice
and each reproduced its own output byte for byte, which is the check that the
difference between them is the change and not the simulation: the playoff
simulation is seeded (`app.inseason.projected.SEED`) and the per-category
probabilities are not simulated at all.

**One thing did not double up.** The script already patched around the
blindfold itself: `_ruled_out` reads `statuses_as_of` for the same morning and
passes the days as `unavailable=` to `project_standings`. That patch is now
redundant rather than additive — an Out man with no return date loses *every*
remaining day in `playable_days`, which is a superset of the single game day
the patch blocks — and it was deliberately left in place, because removing it
would be a second change inside a run scoring the first.

### 4b. Trades: better, and the movement is all in 2026

| | before | after |
|---|---|---|
| picked the better side, 55 deals, 30-day window | 25 of 55 (45%) | 25 of 55 (45%) |
| rank correlation over 110 sides | −0.02 | **+0.02** |
| sides the sign agreed on | 51% | **53%** |
| mean absolute error, categories a week | 0.299 | **0.293** |
| what a man is worth a week, against what he did | Spearman +0.39, MAE 0.213 over 174 men | +0.39, **0.211** |
| **2026's own 34 sides** | 32%, rank −0.31 | **38%, rank −0.16** |

**Six seasons are scored and one of them has reports loaded, so 2026 is where
to look.** The pooled figures move a little and all in the right direction;
2026's own slice moves a lot — the sign agrees on 38% of its sides against
32%, and its rank correlation halves its distance to zero. The deal-level
headline, 25 of 55, did not move at all, which is the right shape: the
evaluator's ordering of two sides of one deal is a coarser thing than the
value it puts on a man.

The worked example is the one the run itself prints. Optimize the MVPs gave
up Nikola Jokic on day 72 and the evaluator called the deal −0.339 a week;
with the morning's statuses visible it calls it **−0.097**, against a
delivered +0.743. It is still wrong, and it is a third as wrong.

`scripts/trade_calibration.py`, 121s before, 122s after.

### 4c. The 2026 pickup replay: better, and the streaming half on every column

The whole sweep — both tilt settings, all twelve hurdle pairs, the baseline,
the per-team table and the worst misses — run twice, concurrently on one
machine. At the owner's own hurdles, and neither was moved:

| | moves named | categories delivered | share ≥ 0 | no-move | claimed |
|---|---|---|---|---|---|
| streaming, before | 531 of 602 | +0.13 a matchup | 80.8% | 11.8% | +0.07 |
| streaming, **after** | **547 of 602** | **+0.15 a matchup** | **82.4%** | **9.1%** | **+0.08** |
| rest of season, before | 289 | +1.32 over 30 days | 80.3% | 52.0% | +1.62 |
| rest of season, **after** | **325** | **+1.25 over 30 days** | **80.3%** | **46.0%** | **+1.68** |

**The streaming half got better on every column**, which is the first
unambiguous improvement any of these three records has shown. Sixteen more
moves named of the same 602 decisions, +0.15 categories a matchup against
+0.13, and the win rate from 80.8% to 82.4%. That is what a status ought to
buy a one-week question: a man the league had Out is no longer seated, so he
neither displaces a man who could play nor is proposed as a pickup, and the
search spends its ranking on men who were going to be on the floor.

**The rest-of-season half named a third more moves and delivered a little
less each** — 325 against 289 at the same 80.3% win rate, +1.25 against
+1.32. Both halves of that are the return prior: an OUT man is worth a
fraction rather than nothing, so a swap taking him on clears the bar where it
used to be refused outright, and the moves that adds are by construction the
marginal ones. The total delivered went up and the mean went down, which is
what widening a filter does.

**The one to watch is the calibration ratio.** Streaming 1.82 → 1.94, still
promising less than it delivers. Rest of season **0.81 → 0.74**: that side
already claimed more than it delivered, and counting an OUT man for his
expected games claims more still. Nothing was tuned on it and no constant
moved, but it is the number that says the return prior is, if anything,
generous — which is what §5's stash table says too.

**The baseline is identical and must be**: 1,120 of the league's own swaps,
+0.050 a week, −0.558 over thirty days, 84.2% at or above zero. The delivered
side of every number here is seated from the stored box scores, which know
nothing about a status. **The sweep's pick is identical too** — no streaming
setting qualifies, and rest of season is 0.20 paid / 0.10 free, the pair
already shipped. Of the 188 lines the two runs write, 138 are equal in place
and every difference is inside the four hurdle-grid tables and the two
sentences that quote them.

**6,840s each**, run concurrently, against the 5,773s of the run before them
which had the machine to itself.

`pickups_backtest.md` §0.2 is the same table written by the script itself,
from **a third run** made after the paragraph was written — because a
paragraph typed into a document the next run overwrites is not a published
result, which is the rule that put `SPREAD_REVISION` and `STASH_REVISION` in
the script in the first place. That run had the machine to itself (5,602s)
and **reproduced the after arm exactly**: of its 220 lines, every one outside
the new §0.2 is identical to the after arm's apart from its own wall time and
the three sentences deliberately rewritten (the section title and paragraph
about the reconstructed schedule, which had gone stale now that 2026's is
stored, and the "no injury history" caveat, which was no longer true).

## 5. The stash rule's §7, re-scored with the status visible

`stash_mode.md` §7 scored the declared OUT-man rule off `scripts/stashes.py`'s
own instrument, because `build_players` "would call every one of these men
fit". It no longer would, so the section prints two more columns: what the
league said on the claim morning, and the same projection made through
`build_players` — the path a report really takes, with the same per-game line,
the same `ESPN_AVAILABILITY` and the same lens, differing only in where the
games share comes from. Re-run whole, **416s**.

**What the league said on the ninety-one claim mornings:** silent **57**,
`Out` **17**, `Questionable` 10, `Probable` 7.

| | the engine, before | the study's instrument | **the engine's own path** |
|---|---|---|---|
| projected, mean per stash | 0.00 | +0.99 | **+1.17** |
| projected, total over the ninety-one | 0.00 | 89.90 | **106.06** |
| delivered, total | 94.50 | 94.50 | 94.50 |
| **mean error per stash** | **+1.04** | +0.05 | **−0.13** |
| mean absolute error | 1.04 | 0.73 | **0.71** |

**The engine's own path is a shade more accurate and overshoots rather than
undershoots**, and the reason is the first line: only **34 of the 91** claim
mornings had the league say anything about the man and only **17** said `Out`.
On the other 57 he reads as fit. A claim is usually made on a day the man's
team is *not* playing, which is exactly when the league says nothing about
him, and one morning snapshot a game date cannot close that. Two things
would — the full-cadence load the scheduled `injury_pass` now writes for the
live season, and carrying a stale `Out` line across a team's off day the way
`app.injuries.absences` already does for the beneficiary work. The second is a
change to the point-in-time rule and would need its own declaration; it is
named here and not made.

The middle column is the run `stash_mode.md` published earlier the same day,
character for character, which is the check that nothing moved underneath.

## 6. Availability's share of the replay's error

`availability.md` §3 asked how much of the projection's error is the games
term and answered **67.29%** on 2026's six checkpoints. It could not ask the
same of the recommender, because a replayed morning had no status to
attribute anything to. `scripts/replay_availability.py` asks it now.

**What is decomposed, and why it is this quantity.** The per-man
rest-of-season line, in categories a week, on the backtest's own 44 decision
mornings — **8,001 man-mornings**. `app.pickups.judge.weekly_lines` is in its
own words "the one place the season charge counts games", so the games term
enters there and nowhere else. The move-level error would confound three
things at once: the games term, the lineup re-solve on both sides, and which
men the wire search happened to rank; its before and after is
`pickups_backtest.md` §0.2, and this is the attribution under it.

| reading | MAE (categories a week) | mean error | share of the published error it removes |
|---|---|---|---|
| status-blind: every man counted for all his team's games | 0.1743 | +0.0723 | −3.26% |
| **statuses visible: the declared source order** | **0.1688** | **+0.0641** | — (the reference) |
| the games oracle: the same rate, given the games he really played | 0.1005 | −0.0454 | **+40.45%** |
| a zero line | 0.4834 | −0.4832 | −186.44% |

**40.45% of the recommender's per-man error is the games term** — against
`availability.md`'s 67.29% for the projection's own fit, and the two are not
the same quantity: that one is a 28-day rate fit scored on eleven counts, this
one is a rest-of-season weekly value scored through the league-standard lens.
Both say the same thing about where the error lives.

**Knowing who was Out removes 3.26% of it.** That is small because 92% of
man-mornings are silent. Split by what the morning said, it is not small at
all:

| morning status | n | mean games, blind | visible | really played | MAE blind | MAE visible |
|---|---|---|---|---|---|---|
| silent | 7,372 | 30.06 | 30.06 | 23.69 | 0.1659 | 0.1659 |
| **OUT** | **266** | 29.34 | **21.49** | 15.84 | **0.3495** | **0.1839** |
| QUESTIONABLE | 214 | 25.77 | 25.77 | 18.12 | 0.2232 | 0.2232 |
| PROBABLE | 72 | 27.53 | 27.53 | 18.83 | 0.2077 | 0.2077 |
| ACTIVE | 53 | 25.77 | 25.77 | 19.49 | 0.1976 | 0.1976 |
| DOUBTFUL | 24 | 24.88 | 24.88 | 16.17 | 0.2051 | 0.2051 |

**On the men the league had Out, the error falls by 47%** — 0.3495 to 0.1839 —
and their expected games fall from 29.34 to 21.49 against the 15.84 they
really played. The rule is still generous on them, which is the prior doing
its job: a table measured on eleven thousand absences cannot know which of
these men was done for the season.

The four rows the other statuses contribute are the availability term's
evidence and not this job's. Read them as the prize still on the table: a
Doubtful man is counted for 24.88 games and plays 16.17, and nothing in this
change touches him. That is `availability.md`'s next ticket, and it now has a
population to be measured on.

No product change. One table.

## 7. What the VPS run adds, and the one command

This machine holds 2026 only — 20,776 lines, morning snapshots, loaded
2026-09-22. The VPS holds 2022–2026. Every figure above is therefore one
season of reports against six seasons of trades, and the trade calibration is
the one that would move: five more seasons of deals would get statuses, where
today only 2026's 34 sides do.

On the VPS, in the checkout, read-only:

```
cd /opt/fcp-core && PYTHONPATH=. .venv/bin/python scripts/trade_calibration.py \
  --out docs/runs/$(date +%F)-trade-calibration.md
```

The projected calibration and the pickup backtest are 2026-only by
construction (`--season 2026`; the backtest's reconstructed schedule and its
decision points are 2026's), so neither gains anything from the older
seasons and neither needs re-running there.

The 2022–2025 reports are not loaded on this machine and loading them is
hours of the league's bandwidth for a season each
(`injuries.md`, "cost per season"). Nothing here needs them locally.

## Verified, and on what

**Tests.** `tests/test_pickups_status_source.py`, fifteen cases: the source
order both ways (ESPN's snapshot beats the report; the report beats silence;
silence stays silence; no source at all leaves every man fit); the whole
five-row mapping, one case a row, plus a check that the table's keys are the
league's five and nothing else; the look-ahead proof and its converse; the
`game_date` half of the rule; an unplaced name leaving its man fit and showing
up in the `unmatched` figure the provenance carries; a roster the league never
named reading equal object for object with the reports on the table and with
them empty; and the memo, keyed on the season and the day.

`tests/test_api_pages.py` pins the context route naming its source, and
`tests/test_mcp.py` pins route-equals-tool on it — the page's "how this is
worked out" and the co-manager's provenance are one call to `status_on`, so
they cannot name two different sources. Every tool's provenance now has to
carry `used`, `used_note` and `unmatched` or the walk fails.

**The guard, on the real database.** Day 52 of 2026, all fourteen teams'
week reports built before and after:

* **eight of the fourteen rosters are equal object for object**, and they are
  exactly the eight the league named nobody on. The six that moved are the six
  with a man on that morning's report (Jordan Poole, Donovan Clingan, Zion
  Williamson, Dennis Schroder and Zach LaVine, Domantas Sabonis, Giannis
  Antetokounmpo and Jrue Holiday).
* **the whole report moves for more teams than that, and it should.** A team
  the league did not name still faces an opponent it did name and still
  searches a wire it did name, so `expected_wins`, `probabilities`, `moves`
  and the schedule sheet move while the roster does not. Only four of the
  fourteen payloads are byte-identical end to end. The precise form of the
  guard is therefore: **a man the league did not name is untouched; a report
  about men it did name is not.** A "byte-identical report" is the wrong thing
  to ask for, because a week report is about two rosters and a wire.

**Real browser**, single mode, own server on a free port, against `fcp`
read-only. The week page for Brockley Heat, `?today=52` on 2026 — the morning
of 11 December 2025, which the league's nine-thirty report covers:

* **Tonight → Not playing** shows *Zion Williamson · **OUT** · NOP · PF · vs
  POR · 8:00 PM*. Every other man in that list reads "no game"; he is the one
  whose team played and whom the league named, and before this change he read
  as fit with a game.
* **What if → Drop** lists him as *Zion Williamson — PF, **out***.
* **"How this is worked out"** prints: *"Injury statuses: the NBA's own injury
  report as of ten o'clock Eastern that morning — the read that sees the
  league's nine o'clock report. The listener had taken no snapshot by then, so
  the league's report is what a manager could have known **as of 10:00 AM on
  Thu, Dec 11**, covering 31 players. 10 names the league printed that morning
  could not be placed on a player and read as fit here; nearly all are
  G-League men."*
* **The stash block fires on a replayed day for the first time.** The week
  report's payload carries it for him: *"Out 12 days · back within a fortnight
  58% · dead weeks cost 1.24 · expected −1.57 (or −5.95 if he is not back by
  week 4)"*. `stash_mode.md` could only exercise that by writing a fake
  snapshot into a private copy of the database; nothing was written here.

**What the browser check could *not* do, and it is worth recording.** A
what-if that *adds* an out man is still refused on a replayed day — *"Tari
Eason is not a free agent on day 52: a what-if can only add a man off the
wire"* — and the season page's Stashes table is still empty. Neither is about
the status: the reconstructed wire is "whoever played that period and was in
nobody's lineup", so a man who did not play cannot be on it at all. The
reports can now *describe* a man on the wire and still cannot *put* him on
one.

**The live season is untouched, checked on the real database.** `status_on`
for 2027 — the season in progress, the one the listener has run for — answers
`espn` on every day asked, over all 1,095 stored snapshots, with `unmatched`
zero. The gate opens because the listener's one pass of 2026-09-23 is older
than any 2027 morning, which is the general case: the last pass is always
older than ten o'clock today.

**Checks.** `ruff check app scripts tests`, `ruff format --check .`,
`mypy app`, full `pytest -q` — all clean.

## Decisions

1. **The gate is "a snapshot observed by that morning", not "the season has
   snapshots".** The second is what the brief's parenthetical suggests and it
   would have been a fifth look-ahead: a replayed day of the live season would
   have read the listener's latest pass, which is from after the day. The
   first costs one indexed query, keeps live behaviour byte-identical (the
   listener's last pass is always older than ten o'clock today), and is what
   makes "ESPN beats the report" testable rather than tautological.
2. **The morning is ten o'clock Eastern, not nine.** The brief says "as of
   09:00 ET". `app.injuries.morning_of` is 10:00 ET and the reasoning is in
   `injuries.md`: the hourly reports were stamped at half past the hour their
   URL named, so a nine o'clock read finds the *eight* o'clock report. Ten
   o'clock is the read that sees the league's nine o'clock report, which is
   what the brief means. Every backtest already uses `morning_of`, and a
   second hour would have made two backtests incomparable.
3. **`RULED_OUT_STATUSES` is not widened.** `Out` alone, as the brief
   instructs. Doubtful plays 5.88% of the time and belongs in the availability
   term's declaration with its own before and after; folding it in here would
   have mixed two measurements under one declaration, which is exactly what
   `stash_mode.md` decided not to do the day before.
4. **The mapping reads the status word and not the reason.** A suspension is
   an `Out` with a reason, and `OUT` and `SUSPENSION` are both ruled out, so
   reading the reason would change no answer and would make the table longer
   than it can be checked in one test.
5. **The NBA team still comes from the snapshot, not from the report.** The
   report names a team in the league's own words and `injury_reports` even
   carries a `pro_team_id` for it, but a replayed season's NBA team has always
   come from the last weekly roster row (`_roster_slot_teams`) and changing
   that would move the *schedule* a man is counted against, which is a
   different measurement with its own before and after.
6. **The projected calibration's own `unavailable=` patch stays.** It is now
   redundant rather than additive, and proving that is a code argument
   (`playable_days` drops every remaining day for an Out man with no date,
   which contains the single day the patch blocks). Removing it would have
   been a second change inside the run that scores the first.
7. **The decomposition is the per-man line, not the move.** Named in §6 and
   in the script's docstring. The move-level error confounds the games term
   with the lineup re-solve and the wire search; `weekly_lines` is by its own
   docstring the one place a games term enters, so that is where the
   attribution is exact.
8. **The before column is a re-run, not a quotation.** `stash_mode.md` §0
   published these three records hours earlier and this change is the first
   thing to have touched them since, so the published numbers would have
   served. They were re-run anyway, from a pristine copy of `HEAD`, on the
   same database and within the hour, because a before-and-after whose before
   is a quotation cannot see a machine that moved underneath it.
9. **`fcp` was read and never written, and no private copy was needed.**
   Every script here is a SELECT and the browser check is read-only routes.
   `stash_mode.md` had to copy the database to exercise the stash block,
   because it needed a status the database did not hold; this change *is* the
   status, so the guard is two runs of the same read against the same rows and
   the browser check is the product reading what is already there.
10. **`injuries.md`'s worked example said day 21 and means day 22.** The
    stored schedule puts 11 November 2025 on scoring period 22 (day 21 is the
    10th), which is checkable against `pro_team_games` in one query. The date
    was always the claim and the day number the gloss; the gloss is corrected.

## Not done, and named so it is not forgotten

**The older seasons are not loaded here.** §7 has the command and what it
would move.

**Doubtful and Questionable still count for every game.** §6's table prices
what that costs on this population; the change is the availability term's.

**The wire on a replayed day still cannot see a free agent who did not play.**
`historical_free_agents` defines the wire as whoever played that period and
was in nobody's lineup, so an injured free agent is invisible to it — which is
the case `injuries.md` opens with, Brandon Miller in November. The reports can
now *describe* a man on the wire but cannot *put* him on it. That is
`in_season_pages.md`'s "the wire is empty" and it is its own job.

**`season_calendar` is read once per `build_players` call.** One min/max
aggregate, and the memo beside it already holds the expensive part; §1 says
why it was left alone in this change.

**The minutes tilt still reads listener events only.** A played season holds
none, so tilt on and tilt off remain the same run in the backtest
(`pickups_backtest.md`). The reports carry statuses, not status *events*; a
tilt fed from them is a different change.

**A stale `Out` line does not carry across a team's off day.** This is the
one that costs the most and it is a change to the point-in-time rule, so it
is named rather than made. `status_as_of` expires a line the morning after
the game it was about, which is right for "what did the league say", and the
consequence is §5: on 57 of 91 stash claim mornings the man's team was idle,
the league said nothing, and he read as fit. `app.injuries.absences` already
solves exactly this for the beneficiary work, by telling "his team filed and
did not name him" apart from "the league said nothing about his team either".
Wiring that distinction into the source order would need its own declaration
and its own before-and-after, and it is the obvious next thing to do here.

**The full-cadence load would help too**, and is already being written for
the live season: the scheduled `injury_pass` stores every quarter-hour
(`injuries.md`, "the pass is scheduled"). This database holds one morning
snapshot a game date for 2026, which is what §2's coverage figures are
measured on.

### The VPS run, 2026-09-24 — five seasons of statuses

Run on the VPS the same day, where the reports cover 2022–2026
(`docs/runs/2026-09-24-trade-calibration-vps-five-seasons.md`): the deal-level
record is unmoved at **25 of 55**, the side-level rank correlation is
**+0.04** (+0.02 with 2026's statuses alone, −0.02 status-blind), mean
absolute error 0.286. Four more seasons of knowing who was Out on the morning
of a deal move the trade number a little, in the right direction, and leave it
a coin at picking the winner — which is what its published note says.
