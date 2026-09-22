# Jobs: per-league work, stored reports and each member's channels

**Written:** 2026-09-19. **Status:** built, step 4 of docs/product.md, and
**not switched on**. The VPS runs today's timers exactly as before
(STATUS.md, "The scheduled jobs"); the job queue, the worker and the enqueue
timer are additive and wait for the switch-over below, which Patrick or the
main session makes after review.

Code: `app/jobs.py` (the queue), `app/job_kinds.py` (what each job does),
`app/schedule.py` (what each schedule label enqueues), `app/league_ingest.py`
(one league's ingest with its own login), `app/reports.py` (stored reports),
`app/channels.py` and `app/api/channels.py` (members' channels), the
per-league half of `app/watchdog.py`; `scripts/worker.py`,
`scripts/enqueue.py`, `scripts/scheduled_enqueue.sh`;
`deploy/fcp-core-worker.service`, `deploy/fcp-core-enqueue.{service,timer}`;
migration `0020_jobs_reports_channels`. Tests: `tests/test_jobs.py`,
`tests/test_channels.py`.

## Why

The timers run one league: the `.env` league, with the `.env` cookies, for
the one tracked team, to the one digest channel. A second league, or a
second manager, has nowhere to go. docs/product.md asks for jobs per league
(ingest, listener pass) and per claimed team (the morning reports, the
digest), a queue Postgres can hold, reports built once and read many times,
and each member's alerts on his own channels.

## The queue

`jobs` is the queue. A row is one job about one thing:

| kind | about | does |
|---|---|---|
| `ingest` | a league | the trailing ten days and next season's settings, what `scheduled_ingest.sh` does, with the league's own login; a league never ingested is backfilled, every season ESPN holds |
| `status_pass` | a league | the listener's pass (below, "One listener league") |
| `precompute` | a team | its week and season reports for today, stored in `team_reports` |
| `digest` | a member (and his team) | the morning digest, or an alert between digests, to his verified channels |

`state` goes `queued`, `running`, `done`; a failure goes back to `queued`
with a later `run_after` (5 minutes, then 20) until the third try, then
`failed`. A failure our code can put in words and that another try cannot
mend (no login for the league, ESPN refusing the login, no secrets key)
fails at once. `depends_on` holds a job until that one is `done`, and fails
it when that one fails: the digest does not go out when the morning pass
could not run, which is what `scheduled_status.sh` does today. A job left
`running` for two hours lost its worker and is tried again.

**Taking a job** is `SELECT ... FOR UPDATE SKIP LOCKED`, due jobs in
`run_after` order, one at a time; the claim is committed (`running`, whose,
when) before any work starts. Two workers never hold the same job
(`tests/test_jobs.py` proves it with two sessions and with two threads).

**De-duplication.** `dedupe_key` is unique: the kind, the league, the team,
the member, the UTC day of `run_after` and the schedule label. The same
schedule enqueued twice on a day, by a timer that fires twice or a person
running it by hand, adds nothing.

**Secrets.** `last_error` is one of our own sentences or an exception's
class name, never its text: ESPN's words, the SMTP server's and Telegram's
(whose refusal quotes the bot's URL, and with it the token) stay out of the
table and the log. The ingest wraps ESPN's own errors (`FetchError`) before
`record_run` sees them, so `ingest_runs.error`, which a signed-in route
lists, cannot quote a login either. The tests make an error that quotes the
cookies and check every place it could land.

## The schedule

`scripts/enqueue.py --schedule LABEL` (the timer passes `auto`, which names
the label from the clock the way the listener's pass does):

| label | UTC (as today) | per league | then, after it |
|---|---|---|---|
| `nightly` | 09:00 | `ingest`, spread over 09:00-09:30 | its `status_pass` (nightly) |
| `morning` | 15:00 | `status_pass` (morning) | a `precompute` per claimed team, then a `digest` per member |
| `report` | 22:30 | `status_pass` (report) | an alert `digest` per member with a team |
| `late` | 00:30 | `status_pass` (late) | the same |

**Which leagues:** every league with a live connection, and `ESPN_LEAGUE_ID`
whenever its `.env` login is set, read with the `.env` cookies when it has
no connection of its own. The `.env` league is scheduled in both modes: it
is the owner's, his cookies read it, and moving to accounts mode must not
stop its ingest.

**The spread.** Each league's ingest is due at 09:00 plus a stable sha256 of
its league id into thirty minutes, so ten leagues reach ESPN across half an
hour and each league at the same minute every night. Thirty minutes keeps
every ingest before the 09:30 BBM pull and the 10:00 backup, as today.

**Which teams, which members.** Precompute: every team of the season the
digest is about with a verified manager, and in single mode the tracked
team. Digest: in single mode the owner alone, about the tracked team; in
accounts mode every member of the league, with his verified team there if
he has one. Alerts only go to members with a team.

**A new connection** (`ingest_requested_at` newer than its league's last
good ingest) gets an ingest on whichever label fires next; `enqueue.py
--ingest ESPN_LEAGUE_ID` asks for one now. After a league's ingest works, its
connection is marked good and its connector is verified on the teams his
SWID owns (`memberships.verify_connection_owner`), which is how whoever
connected a new league gets his team once it exists.

## One listener league

The listener's status snapshots and events are one league's view of who
holds whom (`player_status_snapshots.on_team_id`), shared by every league
because a player's injury status is. A second league's full pass would
write its own `on_team_id` over the first's and turn every shared player
into a stream of drops and claims. So:

- The listener's league (`ESPN_LEAGUE_ID`) gets the whole pass, exactly as
  `scripts/status_pass.py` runs it (`ingest_runs` mode `status`).
- Any other league gets its own wire only (`run_wire_pass`): its free
  agents and waivers into `free_agent_snapshots`, which is what its pickup
  reports read (mode `wire`, so the watchdog's listener check stays the
  listener's).
- In season a team's roster comes from its own lineup days, so another
  league's reports are right; **before its first lineup day** the roster
  falls back to the snapshots, which are the listener league's. And its
  digest's team part is the week plan and the churn only, not the roster
  and wire news, which read the snapshots.

Keying the snapshots by league is the change that lifts this. It is not
needed for Full Court Press, which is the listener's league, and it is
needed before a second league's managers are told their roster news.

## Stored reports

`team_reports` holds a team's `stream` and `season` report for one scoring
period, as exactly the JSON the route answers (`build_payload` in
`app/api/pickups.py`, shared by the route and the job). The pickups routes
answer from the stored row when the report asked for is today's and the row
was built today; otherwise they build live, as before. Nothing but the job
writes a row. "Built today" matters before opening night and after the last
game, when every day maps to the same scoring period.

`GET .../pickups/glance` is new: the free This week page's look at the
reader's own week (expected categories, their chances, the projected
record), read from the stored week report, needing the team's manager and
not the paid tier. The page used to read the paid stream route for it, which
docs/site.md flagged as the thing that would break when billing went on.

`scripts/warm_pages.py` is kept, and still runs after the morning digest
under today's timers; once the precompute runs it is unnecessary (the pages
read the stored rows), and the switched-over units do not run it.

## Members' channels

`notification_channels`: per member, an email address, a Telegram chat id
or an ntfy.sh topic, sealed with `FCP_SECRETS_KEY` and shown only masked
(`p•••@example.com`, `chat •••4321`, `ntfy.sh/fc•••`). Nothing is sent to one
until it is verified: an email by a link (`/account/alerts?token=`, one day,
once, only its sha256 kept, built on `FCP_PUBLIC_URL`; logged instead of
mailed when SMTP is not configured, as sign-in's link is); a chat or a topic
by a code in a test message, typed back on the page. Telegram chats go
through the server's bot: `FCP_TELEGRAM_BOT_URL` (optional, new), else the
digest's own Telegram URL. ntfy topics must be on ntfy.sh, so a member cannot
make the server post anywhere else. Disabling wipes the sealed target.

The routes (`GET`/`POST /me/channels`, `POST /me/channels/verify`,
`DELETE /me/channels/{id}`) are signed-in scope and in docs/accounts.md's
table. The page is Account, Alerts.

**The owner's channels stay in `.env`.** The server's owner
(`FCP_OWNER_EMAIL`) keeps `FCP_DIGEST_URL` (and its chat id) and
`FCP_EMAIL_*`, unsealed and unstored, in either mode, plus any he verifies.

## The digest job

**Every section but the plan is built on `app/inseason/changes.py`**, which
is also what the "What changed" section of the league's This week page reads
(docs/in_season_pages.md). The sentence beside a player is written once,
there, so the message and the page cannot disagree about what happened.
Three things follow:

- The roster and wire sections say exactly what they always said, with the
  worse news of one pass now read first: a man ruled out before a man
  downgraded. `tests/test_digest.py` holds the rebuilt message against the
  one the old code produced, line for line.
- The league section now **names** the moves as well as counting them —
  "Optimize the MVPs claimed Jock Landale for $5, dropping Zach Edey", and
  the trades with both sides of them — capped at `LEAGUE_EVENT_LIMIT` lines
  and `LEAGUE_NEWS_CHARS` characters, whichever binds first, then "and N
  more".
- A drop the transaction ledger has already named belongs to the league
  section rather than the wire section: the ledger row has the team and the
  money in it, and the listener's account of the same move is dropped in its
  favour.

**The owner's window has a near end** (`OWNER_BACKLOG`, a fortnight). It was
"every event never reported", which is not a window at all
(docs/inseason_rehearsal.md, finding 3); an event nobody has been told about
for two weeks is not news, and it stays unnotified with the events route
still holding it.

- **The owner's digest of the tracked team** is `scripts/digest.py`: the same
  message (with the league section after it), to the `.env` channels and
  any he has verified, marking the events it reports as notified once any
  channel took it.
- **Anyone else's** is the league section (this period's matchups as they
  stand, the wire's traffic in the last day) and, for a verified manager
  who is entitled (everyone while `BILLING_ENABLED` is off), his team's
  digest. It reads the events observed since his own last digest went out
  and marks nothing, because `notified_at` is one column and can only mean
  "the owner has been told".
- No verified channel: nothing is sent and the job is done, saying so. A
  channel that fails is named in the job's note by its masked target, never
  with the error; all failing is a failure, retried.

## The watchdog

Unchanged until it has something new to say. A connected league whose
ingest has not succeeded in 36 hours (or ever, once the connection is that
old) is named in the owner's message, its connector is emailed through his
verified email channel ("Your league ... has not refreshed since ...
reconnect it at .../account/connections"), and so is `FCP_EMAIL_TO`. Never
the error text. Once the queue has ever held a job, a `worker` line says
whether a worker is taking them (quiet when a queued job is two hours past
due).

## Switching over

Today, and until this is done: `fcp-core-ingest.timer` (09:00) runs
`scheduled_ingest.sh`, `fcp-core-status.timer` (15:00, 22:30, 00:30) runs
`scheduled_status.sh` (the pass, the digest to Telegram, `warm_pages.py`).
They keep running, unchanged. The BBM, backup and watchdog timers are not
part of this and stay as they are after the switch.

On the VPS, as `aisha` in `/opt/fcp-core`, at a quiet moment (not within a
few minutes of 09:00, 15:00, 22:30 or 00:30 UTC):

1. Deploy and migrate (this is also what tonight's code needs, whether or
   not the switch is made: the scheduled wrappers refuse a schema behind
   the code):
   ```
   git pull
   ./.venv/bin/pip install -q -e ".[dev]"
   ./.venv/bin/python -m alembic upgrade head        # 0020
   sudo systemctl restart fcp-core-api.service
   ```
2. Check the `.env` has what the jobs read: `ESPN_LEAGUE_ID`, `ESPN_S2`,
   `ESPN_SWID`, `FCP_TRACKED_TEAM_ID`, `FCP_DIGEST_URL` (and chat id), and
   `FCP_SECRETS_KEY` if any league is connected. `FCP_OWNER_EMAIL` if set
   must be the address the owner's channels belong to. Nothing new is
   required; `FCP_TELEGRAM_BOT_URL` is optional.
3. Dry run, with the old timers still on: enqueue and look, run nothing.
   ```
   ./.venv/bin/python scripts/enqueue.py --schedule nightly
   ./.venv/bin/python scripts/worker.py --list
   ```
   It lists one ingest for `ESPN_LEAGUE_ID` and its status pass. They run
   when the worker starts in step 5, which is harmless: `--recent` rewrites
   the same trailing days, and tomorrow's nightly is a new day's job.
4. Stop the two timers the queue replaces (the units stay installed, for
   the rollback):
   ```
   sudo systemctl disable --now fcp-core-ingest.timer fcp-core-status.timer
   ```
5. Install and start the worker, then the enqueue timer:
   ```
   sudo cp deploy/fcp-core-worker.service deploy/fcp-core-enqueue.* /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now fcp-core-worker.service
   sudo systemctl enable --now fcp-core-enqueue.timer
   systemctl list-timers 'fcp-core-*'
   ```
6. Watch the first of each: `journalctl -u fcp-core-worker -f`,
   `./.venv/bin/python scripts/worker.py --list`, and the morning digest
   arriving on Telegram at 15:00. `GET /ingest-runs/health` and
   `?mode=status` answer as before: the jobs write the same `ingest_runs`.
7. After a deploy from then on, restart the worker as well as the API
   (`sudo systemctl restart fcp-core-worker`): it is long-running.

## Rolling back

```
sudo systemctl disable --now fcp-core-enqueue.timer fcp-core-worker.service
sudo systemctl enable --now fcp-core-ingest.timer fcp-core-status.timer
```

Nothing else: the scripts the old timers run were never changed, and
migration 0020 only added tables they do not read. A job left queued runs
only if a worker is started again. Stored reports stay and are served while
fresh; with no precompute they go stale by the next day and the routes build
live, as before. The migration need not be undone; if it must be,
`alembic downgrade 0019` drops the three tables.

## Decisions

- **A `dedupe_key` column** rather than a unique index on
  `(kind, league_id, team_id, run_after::date)`: the same guarantee, plus
  the member and the schedule label (the morning digest and the evening
  alert are two jobs a day for one member), and it compares cleanly in the
  migration test.
- **`depends_on`** (not in the brief): the order the timers have today
  (ingest, then the nightly pass; the morning pass, then the digest) held
  by the queue, visible in the table at enqueue time.
- **`user_id` on a job**, for the digest, which is about a member.
- **One timer, the label from the clock**, rather than four timers:
  `label_for` already names a firing within an hour of a slot.
- **The `.env` league is scheduled in accounts mode too**, not only single
  mode, with the `.env` login when it has no connection (above).
- **The ingest for a new league backfills every season** on its first run:
  a few minutes, once, rather than a league with no history.
- **The job's ingest is new code, the script unchanged**:
  `app/league_ingest.py` narrows against this league's own stored season
  (the script matches the season in any league), and nothing the timers run
  tonight moved. Once the switch is made the script can call it.
- **The digest's plan section is still built by the digest**, not read from
  the stored report: it renders the recommender's own objects, and it runs
  in the worker, off anyone's page. Reading the stored row is a follow-up.
- **A member's digest does not mark events notified**; the owner's still
  does (above).
- **Email is offered without SMTP** (the link is logged), as sign-in is.
- **Telegram goes through the server's one bot**; a member gives his chat
  id. The page says to message the bot first; the bot's name is Patrick's
  to share.
- **The watchdog emails the connector every day the league is stale**, a
  daily reminder rather than one message that is easy to miss.
