# Injuries: what the league said about a man, and when it said it

**Written:** 2026-09-22. **Status:** built and loaded. Nothing reads it yet
except its own tests — rewiring the consumers is the next job, deliberately
separate so each number's movement can be seen ("What this unblinds", last
section).

Code: migration `0023_injury_reports`, the models `InjuryReport` and
`InjuryReportRun` in `app/db/models.py`, the reader `app/injury_reports.py`,
the season walk `app/injury_backfill.py`, the accessor `app/injuries.py`, the
shared name matcher `app/player_names.py`, the CLI
`scripts/backfill_injury_reports.py`, and the job kinds `injury_backfill` and
`injury_pass` in `app/job_kinds.py`. Tests: `tests/test_injury_reports.py`
(the parser, against two real PDFs), `tests/test_injuries.py` (storing and
the point-in-time rule), `tests/test_injury_backfill.py` (the walk and the
jobs), and the up-and-down check in `tests/test_migrations.py`.

## Why

ESPN serves a player's injury status **as of the request** and never as a
history. For any season already played, the tool therefore does not know who
was Out, Questionable or Available on a given morning, and it has been saying
so in its caveats for months:

- the trade calibration projects every player as fully available on the
  morning of the deal, on both sides (docs/trades.md §7, "No injury
  statuses");
- the pickup backtest treats every player as available, so the stash logic is
  under-served by construction (docs/pickups_backtest.md §6, "No injury
  history");
- the reconstructed wire cannot show an injured free agent at all, because it
  defines the wire as whoever *played* that scoring period and was in nobody's
  lineup (docs/in_season_pages.md, "The wire is empty"). Brandon Miller, out
  from 5 November 2025, is simply not on it.

The beneficiary model and the projected-record calibration that come next
would have inherited the same blindfold.

The NBA publishes the missing history itself. Since the 2021-22 season every
team files an injury report for each of its games, and the league posts the
combined report as a timestamped PDF snapshot. The snapshots stay up, so a
played season can be walked after the fact. That is what this is.

## The data

One row per (snapshot, player line), at
`https://ak-static.cms.nba.com/referee/injury/Injury-Report_<date>_<time>.pdf`.

**Cadence.** Hourly until 22 December 2025 — twenty-four snapshots a day,
round the clock, every one of them present. Every fifteen minutes from
09:00 ET that morning — ninety-six a day, and sixty-nine on the day itself,
which begins hourly and turns over at nine. The two URL formats are not
interchangeable: the old slug returns 403 in the new era and vice versa.

One correction worth recording, because it was found by probing rather than
by reading. `nbainjuries` models a **two-and-a-half-day hole** here (its
constants stop the hourly era at 19 December 15:30 and start the new one at
22 December 09:00) and we copied that at first. It is wrong: `_09AM` answers
perfectly well on 19, 20 and 21 December. Believing it cost three game dates
of the season on the first load, one of them — 21 December — with no
coverage at all, since no neighbouring snapshot names it. There is a single
clean cutover and no gap.

**Statuses.** Exactly five, and the league prints no others:

| status | means | maps to the listener's kinds |
|---|---|---|
| `Out` | will not play | `went_out`; `RULED_OUT` in `app/injuries.py` |
| `Doubtful` | the league's own word for roughly a one-in-four chance | `downgraded`; `IN_DOUBT` |
| `Questionable` | about even | `downgraded`; `IN_DOUBT` |
| `Probable` | expected to play | `upgraded`; `EXPECTED` |
| `Available` | fit | `returned`; `EXPECTED` |

`Available` is a **report line, not the absence of one**: a man who was
doubtful yesterday and is fit today is listed, which is how an upgrade becomes
visible at all. ESPN's severity order (OUT = SUSPENSION < DOUBTFUL <
QUESTIONABLE = DAY_TO_DAY < PROBABLE < ACTIVE, docs/pickups.md §3.5) carries
over unchanged except that the league has no DAY_TO_DAY and no SUSPENSION
status — a suspension is an `Out` whose *reason* says `League Suspension` or
`Team Suspension`.

**Reasons** are the league's own words and are kept verbatim, because they
carry things no status does: `Injury/Illness - Right Knee; Surgery Recovery`,
`G League - Two-Way`, `G League - On Assignment`, `Personal Reasons`, `Rest`,
`League Suspension`, `Not With Team`, `Trade Pending`, `Return to Competition
Reconditioning`, `Concussion Protocol`. A G-League two-way man is `Out` for
the NBA game with no injury at all, which anything reading these rows as
"hurt" would get wrong.

**A team that has not filed** gets a row of its own, with no player and no
status and the reason `NOT YET SUBMITTED`. That keeps "we asked and the team
had not said" apart from "the team said nobody is hurt", which matters for a
morning read: at half past nine, a fair number of that night's teams have
filed nothing yet.

**What the timestamp means.** The PDF's first line reads `Injury Report:
11/11/25 09:30 AM`. That is Eastern time, and it is the moment the league
compiled the report — not a deadline and not a tip-off. It is what
`reported_at` stores, converted to UTC.

It is **not** the time in the URL, and this caught us. Through 19 December
2025 the URL named the hour (`_09AM`) while the report inside was stamped at
half past it (`09:30 AM`); from 22 December the URL names the quarter hour
(`_09_00AM`) and the stamp matches. The label inside the file is what is
believed, because it is the one the league wrote.

`game_time` is stored as printed — `07:30 (ET)` — and deliberately not turned
into a timestamp: the league prints a twelve-hour clock with no AM or PM, so
a nine o'clock evening game reads `09:00 (ET)` and is indistinguishable from a
morning one without the schedule. `pro_team_games` already holds real tip-off
times; anything wanting one should read them there.

## The point-in-time rule

This is the whole value of the table and the one thing to get right.

`status_as_of(session, player_id, at)` is the newest line for that player
whose `reported_at` is **at or before `at`**, and whose `game_date` is `at`'s
own Eastern date **or later**.

Both halves matter and the second is the easy one to get wrong. A report line
is a statement about one game. A man listed Out for Tuesday's game says
nothing about Wednesday: he may be back, and the league may simply not have
published Wednesday's line yet. Without the `game_date` bound, the newest row
for a player who has since been left off every report would go on reading
"Out" for the rest of the season. With it, a stale line expires by itself the
morning after the game it was about, and the answer becomes `None` — which
means **"the league said nothing current about him"**, not "he is fit". A
caller that wants "fit unless told otherwise" has to say so itself.

Rows are only ever inserted, never updated. An evening report correcting a
morning one is a second row, so both stay on record and either can be read.

**The morning rule.** `morning_of(day)` is **ten o'clock Eastern**, and every
backtest is to use it rather than pick an hour of its own. Nine would have
been the obvious choice and would have been wrong: the hourly reports were
published at half past the hour their URL named, so a nine o'clock read would
have found the eight o'clock report and missed the very snapshot
`--snapshots morning` stores. Ten is after the nine o'clock report under both
cadences and still two hours before the earliest tip-off.

`statuses_as_of` answers a whole roster under the same rule in one query.

**`absences(session, player_id, season)`** gives the runs of days a man was
Out, for the beneficiary work, and it is where the two meanings of silence
have to be separated. A day with no line for him is either:

- **his team filed and did not name him** — he is fit, and the run ends. A
  beneficiary's minutes should stop being credited to an absence the moment
  the league stops naming the absent man; or
- **the league said nothing about his team either** — the team was not
  playing that day, or had not filed by ten in the morning. That is not
  evidence of anything, and the run carries across it.

Both happen constantly. Brandon Miller's shoulder is the worked example
below: read without the distinction it comes back as eleven separate one-day
absences instead of one run of twenty-three days.

## The schema

`injury_reports`, one row per (snapshot, player line):

| column | type | |
|---|---|---|
| `reported_at` | timestamptz | the snapshot's own stamp, ET converted to UTC |
| `game_date` | date | the game this line is about; may be tomorrow's |
| `game_time` | text | tip-off as printed, `07:30 (ET)`; twelve-hour, see above |
| `matchup` | text | `MEM@NYK` |
| `team` | text | the NBA team as the report names it |
| `pro_team_id` | int null | ESPN's pro team id for it |
| `player_name_raw` | text | `Last, First` as printed; empty on a not-yet-submitted line |
| `player_id` | FK `players.id` null | matched, or NULL and the raw name kept |
| `status` | text null | one of the five; NULL when the team had not filed |
| `reason` | text null | the league's own words |
| `source` | text | `nba_official` |
| `fetched_at` | timestamptz | when we fetched it, which is not when it was published |

Unique on `(reported_at, game_date, team, player_name_raw)` — which is what
makes the backfill idempotent and resumable. Indexes on `(player_id,
reported_at)` for the accessor and `(game_date, reported_at)` for the walk's
resume. `player_id` is `ON DELETE SET NULL` rather than cascade: the league's
line is still a fact about that night even if we stop holding the player.

`injury_report_runs` records each run — season, mode (`backfill` or `pass`),
status, started/finished, duration, and a `detail` JSONB of the counts. A
sibling of `ingest_runs` rather than a row in it, because that table is keyed
on an ESPN league and these reports belong to none.

**Team names.** The league writes full names and sometimes changes them (`LA
Clippers`, not `Los Angeles Clippers`). Every NBA nickname is unique in its
last word — including `Portland Trail Blazers` — so the tail of the name
places the team, and `pro_team_id` maps it through `espn_api`'s
`PRO_TEAM_MAP`. All thirty placed on every report read.

## Reading the PDFs: we wrote our own

**Decision: our own parser, on `pdfplumber`, not the `nbainjuries` package.**
Evaluated against nine real reports spanning all five seasons and both URL
formats before deciding. In its favour: it works, it is MIT, and it knows
things worth knowing. Against it, in order of weight:

1. **It needs a Java runtime.** `nbainjuries` parses with `tabula-py`, which
   is a JVM bridge. Neither this Mac nor the VPS had Java; installing one
   (~400MB, its own patch cycle) to run one nightly job is a real cost, and it
   would have had to go in the deploy notes forever. `pdfplumber` is pure
   Python and drops into the existing optional-extras pattern beside `xlrd`.
2. **Its parsing rests on hardcoded pixel geometry, one set of box
   coordinates per season** — five sets already, plus a special case for the
   December 2025 URL change. A layout change needs a package release. Ours
   takes the column boundaries from the table's own header row, so when the
   league moves the table the columns move with it; the same code read all
   five seasons unchanged.
3. **It fetches each PDF twice** — once with `requests` to count pages, then
   again by handing the URL to tabula.
4. **It is measurably less correct on the hard case.** Checked line for line
   on all nine reports: seven came out **identical**. On the other two, ours
   is right. A reason wrapped over three lines (Jared McCain, 11 November
   2025) is one row here and three rows there, two of them with no player name
   at all.

What we **took** from the package, and credit here: its knowledge of the URL
scheme and of the two timestamp formats. Its dates we checked and corrected
(see the cadence note above), which is the general lesson — the package was
worth reading for what it knew and worth verifying on every point we used.

**How ours works.** The PDF is drawn, not tagged: it contains placed glyphs
with no space characters and no cell structure, so word boundaries come from
the gaps between glyphs (1pt tolerance) and columns from the header's own x
positions. A row is anchored on the line carrying a status, because a reason
wraps over as many lines as it needs and is centred on the row it belongs to;
every other line joins the nearest anchor.

The one case that rule cannot see by itself is a reason cut by a **page
break**, where the tail of it is the first thing on the next page while its
anchor is on the page before — Walker Kessler in the 11 November evening
report, whose "Recovery" would otherwise attach itself to Kevin Love and turn
his reason from "Rest" into "Recovery Rest". Such a line is recognised by
sitting further from the page's first anchor than **half the distance to its
second**. Measured across the nine reports, a leading line that really does
belong to the first row sits at 0.19–0.24 of that distance and the
carried-over one at 0.76, so the threshold sits in a wide empty gap. It is
still a measurement and not a proof: a page opening with a four-line reason
could in principle land near it. `orphan_lines` is counted on every run
precisely so a layout change shows up as a number rather than as quietly
mangled text. It was **0** on every report and every snapshot loaded.

(The PDFs from 2023-24 onward do carry real horizontal row rules, which would
be exact. The 2021-22 files carry a static page template instead, identical on
every page, so that route does not generalise and was dropped.)

Two real reports are kept as fixtures — `tests/fixtures/nba_injury_report_
2025-11-11_0930ET.pdf` and `..._1730ET.pdf`, 73 and 83 KB — so the parser is
tested without the network. They were chosen because between them they contain
every case: all five statuses, a G-League two-way and an on-assignment line, a
suffix (`Pippen Jr., Scotty`), an apostrophe (`Sharpe, Day'Ron`), initials
(`Lawson, A.J.`), twenty-five not-yet-submitted lines, the three-line reason
and the page-break split.

## Matching a name to a player

The league writes `Last, First`. `from_last_first` puts it back in reading
order and the **same strict matcher the draft board uses** places it —
factored out of `app/draft/bbm.py` into `app/player_names.py` for this job,
so Basketball Monster's export, a manager's projection upload and the injury
reports all place a name by one rule. Exact on a normalised key (case,
accents, joining punctuation and suffixes gone) first, then the same surname
with one first name the start of the other, if exactly one player fits.
Anything ambiguous is refused.

A miss is **stored**, not dropped: `player_name_raw` keeps the league's
spelling and `player_id` is NULL, and every run counts and reports the
distinct misses.

### Match rates

2026, every game date, morning snapshots — 164 snapshots, 13,605 rows, of
which 10,794 name a player:

| | lines | placed | |
|---|---|---|---|
| **all lines naming a player** | 10,794 | 7,953 | **73.7%** |
| whose reason is **not** `G League` | 7,979 | 7,382 | **92.5%** |
| whose reason **is** `G League` | 2,815 | 571 | 20.3% |

549 distinct names, 128 of them unplaced.

**The headline figure is the misleading one.** The gap is not a matching
failure, it is a population gap, and it is almost entirely the G-League
two-way and on-assignment lines — players who have never been in ESPN's
fantasy pool and so are not in `players` at all. Every one of the 69
distinct non-G-League misses was checked by hand against the players we
hold: all are 2025-26 rookies and fringe call-ups (Thomas Sorber, Noa
Essengue, Nikola Topic, Kasparas Jakucionis, Yanic Konan Niederhauser). Not
one is a player we hold under a different spelling.

The near-misses are the reassuring part. `Jones, Kam` did not become Tyus
Jones; `Johnson, Keshad` did not become Keldon Johnson; `James, Bronny` did
not become LeBron — and that last is the exact wrong match that made the
draft board's matcher strict in the first place. Refusing is the right
answer.

A miss costs nothing: the line is on record under the league's own spelling
with a NULL `player_id`, and can be placed later if the man ever gets an
ESPN id.

## The backfill

    python scripts/backfill_injury_reports.py --season 2026
    python scripts/backfill_injury_reports.py --season 2026 --snapshots morning
    python scripts/backfill_injury_reports.py --season 2026 --from 2025-11-01 --to 2025-11-30

It walks every date `pro_team_games` has a game on — the **Eastern** date, not
the UTC one, since a ten o'clock game tips after midnight UTC — fetches that
date's snapshots, reads them, places the names and writes the lines. The work
is `app/injury_backfill.py`; the `injury_backfill` and `injury_pass` job kinds
call the same function, so a run from the queue and a run from the terminal do
the same thing.

**The pass is scheduled** (since 2026-09-23): the `morning`, `report` and
`late` labels each enqueue one `injury_pass` for the live season with
`snapshots: all`, and a live pass asks only for the snapshots published up to
its own clock (`until`) and skips the ones already stored (`skip_loaded`), so
the three passes hold every quarter-hour of the day between them. That is the
full-cadence season the availability study asked for and did not have
(`docs/availability.md`, limitation 7): after a season of it, "Questionable at
nine, what by tip-off" has a real sample. docs/jobs.md has the label table.

**Throttle:** one request at a time, 1.5 seconds apart (`--delay`), three
tries with a 5s then 20s backoff. **Resume:** the unique key makes it
idempotent, so an interrupted run is resumed by running it again;
`--skip-loaded` does not re-store a snapshot already held. **Dry run:**
`--dry-run` fetches, parses and counts and writes no report rows, though the
run itself is still recorded.

**Cost per season.** `--snapshots all` is the default and is not cheap:

| cadence | snapshots a date | a 165-date season | at 1.5s |
|---|---|---|---|
| hourly (2022–2025, and 2026 to 21 Dec) | 24 | ~4,000 requests | ~2.5 hours |
| quarter-hourly (2026 from 22 Dec) | 96 | — | — |
| 2026, which straddles both | — | ~11,000 requests | ~6 hours |

Measured rather than guessed: three dates at the full hourly cadence took
**99.5s for 72 snapshots**, 7,424 lines and 2,400 rows a date — about 1.4s a
snapshot including the 1.0s delay used for that run. Storage is **328 bytes
a row** including both indexes, so an hourly season is roughly 400k rows and
**125 MB**, and 2026 at the full cadence about 1.2M rows and **380 MB**. The
row count is the same order as `player_status_snapshots` (~400k a season).

`--snapshots morning` is one request a date: **164 snapshots in 296 seconds**
for the whole of 2026, 13,605 rows, 4.5 MB. That is all the morning-of-day
rule reads, and it is what has been loaded.

## The run

**2026, `--snapshots morning`, every game date.** 164 snapshots, 13,605 rows,
165 game dates covered (a snapshot also names the next day's games, so the
dates covered exceed the dates fetched). 73.7% of named-player lines placed,
92.5% outside the G-League lines. **`orphan_lines` was 0 on every snapshot**,
which is the parser's own check that no line went unplaced. 296 seconds,
4.5 MB. Nothing was missing: after the cutover was corrected, every game date
in `pro_team_games` for 2026 has at least one snapshot naming it.

A three-date sample at `--snapshots all` (10–12 November) is loaded beside
it — 72 more snapshots, 7,171 more rows — both to measure the cost and to
exercise the idempotence on real data: of its 7,424 lines, the 253 that were
the morning snapshots already held inserted nothing.

The older seasons (2022–2025) are **not** loaded. The schedule is stored for
all of them, so each is one command.

### Brandon Miller, the worked example

The man the reconstructed wire cannot see. Asked of the data:

**His status on the morning of 11 November 2025 (day 21): `None` — the
league said nothing current about him.** Not because he was fit, but because
**Charlotte did not play that day**, and the league only names players whose
team has a game. This is precisely the trap in the last section: a consumer
reading `None` as "available" would have him playing.

**He was Out on 24 mornings**, in two runs: **28 October – 19 November (23
days)**, a left shoulder subluxation, and a single night on **3 December**.
He is Questionable on 5 December and appears only as Probable after that.

Getting that second number right is what forced the design of `absences`.
Read naively — a day with no line ends the absence — the same data gives
**eleven separate one-day absences**, because Charlotte played every other
day and on four occasions had filed nothing by ten in the morning. Both
silences had to be told apart from a real return, which is what the "his
team had filed and did not name him" test does. The run then ends on 19
November, and the reports bear that out: from 22 November Charlotte filed
and Miller is not on it.

## Deploying it

The Java problem went away with the parser decision, so the whole deploy step
is one line. On the VPS, in the checkout:

    .venv/bin/pip install -e '.[injuries]'
    sudo systemctl restart fcp-core-worker

That installs `pdfplumber` (pure Python; `pdfminer.six`, `Pillow` and
`pypdfium2` come with it, about 15MB, no JVM). The worker has to be restarted
because it is long-running and keeps the code it started with.

Until that is done, **`injury_pass` is deliberately not on the schedule**:
`app/schedule.py` does not enqueue it, so nothing on the VPS will try to run
it and fail. `app/job_kinds.py` fails such a job with "pdfplumber is not
installed, so the injury report PDFs cannot be read" and does not retry, so
even a hand-queued one is a clean refusal rather than a crash loop.

Where it belongs once the step is done is the **`morning` label** (15:00 UTC),
beside the `status_pass`: the league's nine o'clock Eastern report is out by
then and the day's pickup and lineup decisions are the ones that want it. It
depends on nothing and nothing depends on it. See docs/jobs.md, "Where
`injury_pass` belongs".

The backfill of the older seasons should be run by hand, or queued one season
at a time as `injury_backfill`, rather than scheduled: it is hours of
someone else's bandwidth and it only needs doing once.

## What this unblinds

Nothing below is changed yet. Each is its own piece of work so that the
movement in each number can be attributed, and each names the doc that has to
be re-run and rewritten when it is.

| consumer | today | with this | doc that moves |
|---|---|---|---|
| **The reconstructed wire** — `app/pickups/state.load_free_agents` | a played season's wire is "whoever played that period and was in nobody's lineup", so an injured free agent is invisible | a man on the report and in nobody's lineup is on the wire, with a status; Brandon Miller reappears in November | docs/in_season_pages.md, "The wire is empty"; docs/pickups.md §4 |
| **The trade calibration** — `scripts/trade_calibration.py` | both sides projected fully available on the morning of the deal | each side's men discounted by what was actually known that morning | docs/trades.md §7, and the standing "Model availability" line |
| **The beneficiary model** (next) | cannot be built: it needs to know who was out and when | `absences()` gives the runs of Out days directly, which is its input | its own doc, to be written |
| **The projected-record calibration** (next) | would inherit the blindfold | a week's opponent priced on who was actually out | docs/week_predictor.md |
| **The pickup backtest's availability discount** — `scripts/pickups_backtest.py` | every player treated as available; the stash logic under-served by construction | a stash judged against a real return, and a start judged against a real absence | docs/pickups_backtest.md §6 |
| **The minutes tilt** — `app/pickups/projection.py` | reads the listener's `player_status_events`, which a played season holds none of, so tilt-on and tilt-off are the same run | a played season gets real status changes, so the tilt can be measured rather than assumed worthless | docs/pickups_backtest.md, "Tilt on and tilt off are the same run here" |

One caution for all of them. `status_as_of` returning `None` means the league
said nothing current, **not** that the man is fit — and the league only ever
names players whose team plays that day. On an off day every player on that
team is `None`. Any consumer that reads `None` as "available" will be right by
accident most of the time and wrong exactly when it matters, so each should
combine the status with `pro_team_games` and treat "his team plays and the
league did not name him" as the only real "available".
