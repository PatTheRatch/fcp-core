# Jobs: per-league work, stored reports and each member's channels

**Written:** 2026-09-19. **Status:** built, step 4 of docs/product.md, and
**not switched on**. The VPS runs today's timers exactly as before
(STATUS.md, "The scheduled jobs"); the job queue, the worker and the enqueue
timer are additive and wait for the switch-over below, which Patrick or the
main session makes after review.

Code: `app/jobs.py` (the queue), `app/job_kinds.py` (what each job does),
`app/schedule.py` (what each schedule label enqueues), `app/league_ingest.py`
(one league's ingest with its own login), `app/reports.py` (stored reports),
`app/channels.py` and `app/api/channels.py` (members' channels),
`app/subscriptions.py` (what a member wants to hear about),
`app/mail/` (the message itself, both its parts), the per-league half of
`app/watchdog.py`; `scripts/worker.py`, `scripts/enqueue.py`,
`scripts/scheduled_enqueue.sh`; `deploy/fcp-core-worker.service`,
`deploy/fcp-core-enqueue.{service,timer}`; migrations
`0020_jobs_reports_channels`, `0024_email_only_channels` and
`0025_digest_subscriptions`. Tests: `tests/test_jobs.py`,
`tests/test_channels.py`, `tests/test_subscriptions.py`,
`tests/test_mail.py`.

## Why

The timers run one league: the `.env` league, with the `.env` cookies, for
the one tracked team, to the one digest address. A second league, or a
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
| `precompute` | a team | its day, week and season reports for today, stored in `team_reports` |
| `digest` | a member (and his team) | the morning digest, or an alert between digests, emailed to his confirmed addresses |
| `injury_backfill` | a season | a whole season of the NBA's official injury reports (docs/injuries.md); hours at the full cadence, which is why it is queued |
| `injury_pass` | a season | the same, today only, for the season in progress |

The last two belong to no league: the reports are the NBA's own, not
ESPN's, so both carry a `season` in their payload and no `league_id`.

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
class name, never its text: ESPN's words and the mail server's reply stay
out of the table and the log. The ingest wraps ESPN's own errors (`FetchError`) before
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

**Where `injury_pass` belongs, and why it is not there yet.** It belongs on
the `morning` label, beside the `status_pass`: the league's nine o'clock
Eastern report is published by then, and the day's pickup and lineup
decisions are the ones that want it. It depends on nothing and nothing
depends on it, so it is due at 15:00 alongside the passes rather than after
them, and a day the NBA does not play is a no-op rather than a failure.

It is deliberately **not** in `app/schedule.py` yet, because adding it there
would start it running on the VPS the next morning and the VPS cannot read
the PDFs: `pdfplumber` is not installed. The deploy step is in
docs/injuries.md, and wiring it into `schedule.py` is a one-line change to
make once that step has been done.

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

`team_reports` holds a team's `today`, `stream` and `season` report for one
scoring period, as exactly the JSON the route answers (`build_payload` in
`app/api/pickups.py`, shared by the route and the job). The pickups routes
answer from the stored row when the report asked for is today's and the row
was built today; otherwise they build live, as before. Nothing but the job
writes a row. "Built today" matters before opening night and after the last
game, when every day maps to the same scoring period.

**`today` is the third kind** (2026-09-22, migration `0022`, which only
widens the `kind` CHECK). The day's lineup (`app/pickups/today.py`) is much
the cheapest of the three -- one day's seating rather than a whole wire --
and it needs no precompute to be quick. It is stored beside the other two
anyway, because it is read on every load of the week page and by every
morning digest, and because a report built once is a report that cannot
disagree with itself between the page and the message.

`GET .../pickups/glance` is new: the free This week page's look at the
reader's own week (expected categories, their chances, the projected
record), read from the stored week report, needing the team's manager and
not the paid tier. The page used to read the paid stream route for it, which
docs/site.md flagged as the thing that would break when billing went on.

`scripts/warm_pages.py` is kept, and still runs after the morning digest
under today's timers; once the precompute runs it is unnecessary (the pages
read the stored rows), and the switched-over units do not run it.

## Members' channels

**Everything goes by email** (2026-09-22). A member's digest, his alerts and
the operator's own notices all go the one way; there is no push channel and
no bot. `FCP_DIGEST_URL`, `FCP_DIGEST_CHAT_ID` and `FCP_TELEGRAM_BOT_URL`
are gone from `app/config.py` and from `.env.example`, and a `.env` that
still sets them is not an error: the keys do nothing.

`notification_channels`: per member, an email address, sealed with
`FCP_SECRETS_KEY` and shown only masked (`p•••@example.com`). Nothing is sent
to one until it is verified, by a link (`/account/alerts?token=`, one day,
once, only its sha256 kept, built on `FCP_PUBLIC_URL`; logged instead of
mailed when SMTP is not configured, as sign-in's link is). Disabling wipes
the sealed target.

**The Telegram and ntfy rows are disabled, not deleted** (migration
`0024_email_only_channels`). The row stays so the Alerts page can tell the
member his old channel has stopped and ask him for an address; its chat id
or topic is wiped, so nothing can be sent to it; and the table's CHECK
becomes "an email address, or disabled", so no new row of a retired kind can
be written even by hand. A member left with only one of those rows has no
channel at all, and the digest job says so ("no confirmed email address")
rather than failing to reach him quietly.

The routes (`GET`/`POST /me/channels`, `POST /me/channels/verify`,
`DELETE /me/channels/{id}`) are signed-in scope and in docs/accounts.md's
table. The page is Account, Alerts.

**The owner's recipients stay in `.env`.** The server's owner
(`FCP_OWNER_EMAIL`) keeps `FCP_EMAIL_*`, unsealed and unstored, in either
mode, plus any address he confirms.

## Subscriptions: what a member wants to hear about

`digest_subscriptions` (migrations `0025` and `0026`), a row per (member,
league): `topics`, a JSONB map of topic name to on, and three columns,
`morning`, `alerts` and `length`. `app/subscriptions.py` is the vocabulary;
the page is the "What goes in it" block on Account, Alerts, and the routes
are `GET /me/subscriptions` and `PUT /me/subscriptions/{league_id}`.

| topic | what it puts in the email |
|---|---|
| `lineup` | today's lineup: the fix-this line and the starters |
| `moves` | the week's and the season's pickups that clear the bar |
| `my_team` | adds, drops and claims on my team; status changes on my roster |
| `opponent` | this week's opponent: his moves and his injuries |
| `league_transactions` | every add, drop, claim and trade in the league |
| `league_injuries` | every status change in the league |
| `trades` | trades and proposals, and the block once there is one |
| `standings` | my place, my record, the projected finish once it exists |

`morning`, `alerts` and `length` are not topics: the first two say whether a
message is sent at all, and the third how much of each section is written
out ("compact", the default, or "full"; above, "The two forms"). An alert is
filtered by the same topics the digest is — today
`build_alert` names men on the reader's own roster, so it rides on
`my_team`; an alert about the opponent, when there is one to send, rides on
`opponent` like every other line of the feed.

**Defaults:** lineup, my moves, my team, my opponent, trades and standings
on; the two league-wide feeds off. Those are the sections that would make a
new member unsubscribe on his second morning — on 2026 day 107, the season's
busiest, the league's transaction feed ran to 4,392 characters on its own. A
trade is on because a league sees a handful in a season and every one is
worth reading. **The owner's tracked team in single mode keeps everything
on**: single mode is one person reading his own server, and nothing there
should be silently missing.

**Nothing was backfilled.** No row means the defaults, and one appears the
first time he changes something. A JSONB map rather than a column each
because the topics will change — the trade block and the projected finish
are in the list and are not built — and a topic should not need a migration
to appear or a backfill to default. An unknown key is dropped on the way out
and a missing one takes its default, so a row written by an older version
still reads.

**A member with every topic off gets no digest**, and the job's note says
which of the three it was: he does not want the morning one, he does not
want the alerts, or he has no topics left.

**A section he did not ask for is not built.** `build_digest` takes the
subscription, so a reader who wants only today's lineup does not pay for the
rest-of-season search.

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
  message (with the league section after it), to the `.env` recipients and
  any address he has confirmed, marking the events it reports as notified
  once a send succeeded.
- **Anyone else's** is the league section (this period's matchups as they
  stand, the wire's traffic in the last day) and, for a verified manager
  who is entitled (everyone while `BILLING_ENABLED` is off), his team's
  digest. It reads the events observed since his own last digest went out
  and marks nothing, because `notified_at` is one column and can only mean
  "the owner has been told".
- No confirmed address: nothing is sent and the job is done, saying so. An
  address that fails is named in the job's note by its masked form, never
  with the error; all failing is a failure, retried.

## The two forms: compact by default, full as an option

**Decided 2026-09-23.** The email's job is "is there anything to do today?",
answered in ten seconds, with a link for the rest. It should read like the
subject line expanded, not like the pages. As it stood it was the site
pasted into an inbox: nine phone screens on 2026 day 107, with the empty SF
slot and the one move that clears the bar sitting above twenty $1 waiver
claims by other teams, three under-the-bar moves with a paragraph each, a
season section that repeated the week's move, and lines of slot codes.

**The compact form** is one screen, and it is what everybody gets unless he
says otherwise. In order, and nothing else:

1. **The masthead** — team, league, the day and its date.
2. **Tonight** — the lineup grid, which is the one table worth its space,
   and the fix-this line when there is one (the rest are "(and N more)" on
   that same line). No list of men sitting with no game, no sentence about
   the roster being short: one line, "N places nobody can fill tonight".
3. **Worth a look** — the moves that clear the bar, **one line each**, at
   most three: "Add Dylan Cardwell, drop Dennis Schroder · +0.37 · mostly
   BLK, FG%". Then one line for everything left out — "3 more under the bar
   → see the week". Empty days are one line and no slot codes: "3 days this
   week with an empty place → plan the week".
4. **Since yesterday** — one line of counts from his subscribed topics, "2
   on your roster · 1 on your opponent's · 19 around the league", each a
   link to the What-changed section of the league's page; then, in full, a
   status change on his roster or his opponent's, and a trade he is in,
   because those are the news worth the space. Everything else is a count.
5. **Standing** — one line: his place, his two records, and the projected
   finish, which is still the marked slot until that work lands.
6. The footer, as before.

**A move appears once.** The week's plan and the rest-of-season search run
over the same wire, so the season's best is very often the week's best
again. When it is, it is not printed twice; when the season names a
different man, he gets his own line with "(season)" on it. This holds in
both forms: the long one says "The move under This week is the season's
too, at +0.25 a week" rather than repeating the row.

**A bar labels and never hides** — but in an email, what is under the bar is
a count and a link, not a paragraph.

**The full form** is the long message, kept, with three fixes it gets
whether or not anyone chooses it: a move never appears twice; an
under-the-bar move is one line rather than a paragraph of both horizons and
both records; and the "sitting, no game" list is gone from both parts.

**The choice is `length` on `digest_subscriptions`** (migration `0026`),
"compact" or "full", beside the topics on the Alerts page. The job honours
it, and so does `scripts/digest.py --full`. Every existing row and every
member without one is compact. The owner's tracked team in single mode
keeps every *topic* on whatever is stored, and still reads his length: that
rule is about nothing being silently missing, and a count with a link
misses nothing. The alert between digests is already short and does not
read it.

**Nothing in the compact form is computed differently.** It is the same
`Digest`, rendered with less of it, and `tests/test_digest.py` asserts that
every measurement in the compact email also appears in the full one.

## The shape of the message

The digest is sent as one `multipart/alternative` email (`app/mail/`): a
text part and an HTML part, both built from one `Digest`, so the two cannot
disagree, and both in the length he chose.

**The text part is `Digest.render()`**: what `--dry-run` prints, what the
tests hold line for line, and what a reader in a terminal client sees.
`render(compact=True)` is the short form, and it follows the HTML part's
choice, because the two are halves of one message. The long form's order is
the one this message has always had, with `THE SEASON` and `STANDINGS`
after the churn line. **The league section is appended to the long text
only**: the compact form has the league's traffic as a count and a link, and
forty lines of it under a one-screen message would undo the whole point.

**The HTML part is the email as a page in the house style**
(`app/mail/render.py`, palette and faces in `app/mail/style.py`): a 600px
column that reads on a phone, the masthead (league, team, the day), then
exactly the sections the reader subscribed to, in the topics' own order:

1. **Today's lineup** — the place going empty tonight in the warn colour,
   then the starters by slot with the game each one has. Places nobody on
   the roster can fill are counted under the grid, not given a row each: a
   roster short at three UT slots printed the same sentence three times,
   which reads as a fault rather than a fact.
2. **This week** — the moves that clear the bar, each with its number and one
   line of reason; the nearest ones that did not are named on **one line
   each** and labelled "under the bar", because a bar labels and never
   hides. Deduplicated by the man coming in: the plan judges its second move
   with the first already made, so the same add otherwise appears twice with
   two different numbers, which read as the bar contradicting itself.
3. **The season** — where the season ends on this roster, and the one move
   over the rest of it, unless that move is the week's, which is named once
   (above, "The two forms").
4. **What changed** — the feed's own sentences, grouped by day, filtered by
   his topics. The five feed topics are five ways into one section, not five
   sections; a lede says which of them he holds.
5. **Standings and projections** — his place and his two records, and a
   marked slot where the projected finish will go.

Then a footer: the pages this came from (`FCP_PUBLIC_URL`), one line of why
he got it, and a link to manage his alerts.

**What an email allows**, and so what the renderer uses: tables for layout,
inline styles, literal colours (an inbox has no custom property), system
fonts with the site's own fallbacks (no web font is loaded, so Oswald's
fallback `Arial Narrow` and Source Serif 4's fallback Georgia are what is
asked for). No stylesheet, no `<style>` block worth relying on, no script,
no image of any kind — not a spacer, and certainly not a tracking pixel. No
flexbox, no grid, no `position`, no float: the Outlook client lays out with
Word and knows none of them.

**Numbers** are `tabular-nums` and in the mono face; the nine categories are
in the fixed order every screen uses; a gain and a loss are told apart by a
sign and an arrow before they are told apart by colour, as the trade page
does, because a client in dark mode may invert every colour on the page.

**The subject line** is

    {team}: {the one thing worth opening it for, in at most eight words}

the one thing being, in order: the place in tonight's lineup that will
produce nothing while a man on the bench would have (it expires at tip-off,
so nothing outranks it); then the top move that clears the bar; then, when
neither exists, "nothing to fix today". Eight words because a phone shows
about forty characters of a subject and that is where a manager decides. The
team leads because a manager in two leagues has two of these.

**The sign-in email and the address confirmation** get the same masthead and
footer, the link as a button and as a plain URL under it, and the same two
parts.

## Deliverability

* `List-Unsubscribe` points at the Alerts page, which is where the topics
  are turned off. RFC 8058's one-click `List-Unsubscribe-Post` is
  deliberately **not** sent: one-click means a POST from the mail provider
  with no session, and no route here would honour it safely.
* **No tracking of any kind.** No pixel, no redirect through a counter, no
  per-reader link.
* **A stable From name.** `FCP_EMAIL_FROM` wants a display name that never
  changes ("Full Court Press <fcp@…>"); a From name that moves is a
  deliverability problem, not a style choice.
* **DMARC is still owed a move to `p=quarantine`.** The domain publishes
  `p=none` today, which asks receivers to do nothing about a forgery. SPF
  and DKIM have to be right for the sending domain first, and only then does
  the policy tighten. Not done here: it is a DNS change on the live domain,
  and this job opened no connection at all.

## Looking at it without sending

    python scripts/digest.py --season 2026 --on 2026-02-04 --html OUT.html
    python scripts/digest.py --season 2026 --on 2026-02-04 --full --html OUT.html
    python scripts/notify_test.py --preview DIR

The first writes the HTML part of exactly the message that would go out and
opens no connection (`--dry-run` still prints the text part); without
`--full` that is the compact form, which is what a member gets. The third
writes the sign-in and confirmation mail, HTML and text, with a sample link
that goes nowhere. Neither sends anything.

## The watchdog

Unchanged until it has something new to say. A connected league whose
ingest has not succeeded in 36 hours (or ever, once the connection is that
old) is named in the owner's message, its connector is emailed at his
confirmed address ("Your league ... has not refreshed since ... reconnect it
at .../account/connections"), and so is `FCP_EMAIL_TO`. Never the error
text. Once the queue has ever held a job, a `worker` line says whether a
worker is taking them (quiet when a queued job is two hours past due).

**Its notice goes by email too**, like everything else, with a subject that
names what broke — "fcp-core: listener, bbm quiet" — because a phone shows
the subject and little else. `scripts/watchdog.py --dry-run` prints the
subject and the message and sends nothing.

## Switching over

Today, and until this is done: `fcp-core-ingest.timer` (09:00) runs
`scheduled_ingest.sh`, `fcp-core-status.timer` (15:00, 22:30, 00:30) runs
`scheduled_status.sh` (the pass, the digest by email, `warm_pages.py`).
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
   ./.venv/bin/python -m alembic upgrade head        # 0020, then 0024 and 0025
   sudo systemctl restart fcp-core-api.service
   ```
2. Check the `.env` has what the jobs read: `ESPN_LEAGUE_ID`, `ESPN_S2`,
   `ESPN_SWID`, `FCP_TRACKED_TEAM_ID`, the mail settings (`FCP_SMTP_HOST`,
   `FCP_EMAIL_FROM`, `FCP_EMAIL_TO`, and `FCP_SMTP_USER` /
   `FCP_SMTP_PASSWORD` if the relay wants a login), `FCP_PUBLIC_URL` so the
   email's links and its `List-Unsubscribe` can be built, and
   `FCP_SECRETS_KEY` if any league is connected. `FCP_OWNER_EMAIL` if set
   must be the address the owner's channels belong to.

   **Three keys stop mattering** (2026-09-22): `FCP_DIGEST_URL`,
   `FCP_DIGEST_CHAT_ID` and `FCP_TELEGRAM_BOT_URL`. Nothing reads them, and
   leaving them set is not an error — the settings ignore what they do not
   know — but delete them so the file says what the server does. There is
   now no channel but email: if the mail settings are not right, the digest
   prints and sends nothing rather than falling back to a chat.

   `python scripts/notify_test.py` confirms the mail settings in one send,
   and `--dry-run` confirms them without one.
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
   arriving in the inbox at 15:00. `GET /ingest-runs/health` and
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
- **The digest's plan and lineup sections are still built by the digest**,
  not read from the stored reports: they render the recommender's own
  objects, and they run in the worker, off anyone's page. Reading the
  stored rows is a follow-up.
- **A member's digest does not mark events notified**; the owner's still
  does (above).
- **Email is offered without SMTP** (the link is logged), as sign-in is.
- **Email is the only channel** (2026-09-22). The push URL and the bot are
  gone, their rows disabled rather than deleted, and the message is a page
  rather than a chat box's worth of text ("The shape of the message").
- **What is in it is the member's**, per league, as a small set of named
  topics ("Subscriptions"), stored as a JSONB map so a new topic needs no
  migration and no backfill.
- **How long it is, is the member's too** (2026-09-23), and compact is the
  default for everybody ("The two forms"). A column rather than a topic,
  because it says how the message is written and not whether a section is in
  it; a value this version does not know reads as compact.
- **The compact form leaves nothing out that a count and a link do not
  cover.** That is what makes it safe as a default, and why the owner's own
  digest in single mode takes it too while keeping every topic on.
- **The watchdog emails the connector every day the league is stale**, a
  daily reminder rather than one message that is easy to miss.
