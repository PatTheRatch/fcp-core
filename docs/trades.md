# Trades, looked at forward: one currency, both sides, and an honest calibration

**League:** Full Court Press (ESPN 3853870), nine-category H2H, daily lineups, 16 teams in 2027
**Written:** 2026-09-21, revised the same day after the run declared in §7a, and again on 2026-09-22 after the run declared in §7b
**Status:** the engine (`app/trades/`), the CLI (`scripts/trade.py`), the calibration (`scripts/trade_calibration.py`) and the published record (`app/trades/calibration.py`) are built, and since 2026-09-21 so are the two routes and the page (`app/api/trades.py`, `app/api/static/trades.html`, §10). §6 is the payload they sit on.
**Companions:** [`pickups.md`](pickups.md) (the currency and the recommender it came from), [`scoring/`](scoring) and `app/scoring/trade_grades.py` (the same trades, graded in hindsight), [`inseason_rehearsal.md`](inseason_rehearsal.md) (the look-ahead lesson §5 is built on)

---

## 0. The answer, up front

### Revision R5, 2026-09-24: a replayed morning reads the NBA's own injury report, and this record moved

**What changed.** Until this morning the engine read an injury status from
`player_status_snapshots`, which the listener only ever writes for the season
in progress, so **every man in every replayed season was counted fit** — R4
below says so in as many words, and that is why it came back identical. The
engine now applies a declared source order (`docs/replay_status.md`): ESPN's
snapshot on a morning the listener had already run for, and otherwise **the
NBA's own official report as of ten o'clock Eastern that morning**, read
point-in-time through `app/injuries.py`. Nothing else moved — not
`ESPN_AVAILABILITY`, not the hurdles, not `RULED_OUT_STATUSES`, which still
means `Out` alone of the league's five words.

**Before and after.** This is the first of the three records to have moved at
all since the spread was widened, and it moved in the right direction.

| | before | after |
|---|---|---|
| picked the better side, 55 deals, 30-day window | 25 of 55 (45%) | 25 of 55 (45%) |
| rank correlation over 110 sides | −0.02 | **+0.02** |
| sides the sign agreed on | 51% | **53%** |
| mean absolute error, categories a week | 0.299 | **0.293** |
| what a man is worth a week, against what he did | Spearman +0.39, MAE 0.213 over 174 men | +0.39, **0.211** |
| **2026's own 34 sides** | 32%, rank −0.31 | **38%, rank −0.16** |

**Read the last row and not the first.** Six seasons are scored here and the
reports are loaded for one of them, so 2026 is the only slice that could
move much, and it is where nearly all of the movement is: the sign agrees on
38% of its sides against 32%, and its rank correlation halves its distance to
zero. The deal-level headline did not move at all, which is the right shape —
ordering two sides of one deal is a coarser judgement than valuing a man.

The worked example is the run's own. Optimize the MVPs gave up Nikola Jokic on
day 72 and the evaluator called the deal −0.339 a week; with the morning's
statuses visible it calls it **−0.097**, against a delivered +0.743. Still
wrong, and a third as wrong.

**What would move it further.** Five more seasons of reports. The VPS holds
2022–2026 and this machine holds 2026 only; `docs/replay_status.md` §7 has the
one command.

`PUBLISHED`, `UNEVEN_ERROR` and `CALIBRATION_NOTE` were updated to the new
measured values, which is the rule of §7: the run is published whichever way
it falls and the constants follow the run.

### Revision R4, 2026-09-24: an OUT man is counted for his expected games, and this record did not move

**What changed.** A man ESPN has ruled out was worth nothing over the rest of
the season, because the games count waited on an `expected_return_date` ESPN's
basketball API has no field for. He is now counted for the games he is
expected to play — the return prior of [`stashes.md`](stashes.md) §2a times the
ramp of its §3, declared in [`stash_mode.md`](stash_mode.md) before anything
was re-run. The evaluator reads it through `weekly_lines` like every other
caller, and each side of a judged deal now carries a `stashed` block for the
men it is taking on who are out: the odds, the dead weeks and what they cost.

**Why it had to be re-run, and what it said.** Every man's season value comes
from that games count, so a deal involving anybody out would be a different
number. It came back **identical** — 25 of 55 deals, rank correlation −0.02,
+0.39 over 174 men, every published digit — because the six seasons this
record is measured on have no stored injury status at all (snapshots cover the
season in progress only). It is a guard that nothing else moved, not a test of
the rule, and §7 of `stashes.md` re-scored in `stash_mode.md` is the test.

### Revision R3, 2026-09-23: the weekly spread was widened, and this record did not move

**What changed.** `app.pickups.stream.SPREAD_SCALE` is 2.0 since 2026-09-23,
so every "chance of winning a category" in the product is nearer the middle
(`docs/projected_record.md` §0, `docs/spread_revision.md`). The factor was
chosen by the owner on the evidence of the projected-standings run of
2026-09-22 and declared before this re-run started; nothing was tuned
afterwards, which is §7's rule.

**Why this calibration had to be re-run.** `head_to_head` is where every
chance in the codebase comes from, and `Judgement.delta_week` — the matchup
term of a trade's net — reads it through `app.pickups.stream.week_deltas`. A
trade's headline number on the page is therefore not the number it was
yesterday.

**Before and after: nothing.** The whole run of 2026-09-23 reproduces the run
of 2026-09-22 **to every published digit**: 25 of 55 on the primary cell, 31
of 55 over the rest of the season, Spearman −0.02, mean error +0.008, the
two-for-one gap +0.404 against the old yardstick and **+0.103** against the
streamed lane, the player level +0.39 over 174 men. `PUBLISHED`,
`UNEVEN_ERROR` and `CALIBRATION_NOTE` are unchanged, character for character.

**Why it did not move, which is the finding.** What this calibration scores is
`Judgement.delta_season_per_week` and `SideReport.season_independent` — both
*per-week season* terms, built from `app.scoring.value.marginal` against the
league's spreads and from `places_cost`. Neither reads `head_to_head`. The
week term the widening does move is not in the headline, because a trade is
judged on what it is worth to an ordinary week from here on rather than on the
matchup it happens to land in (§1, §2). So this is a real out-of-sample check
that came back clean, and at the same time it is a check of a narrower thing
than it looks: it says the widening broke nothing in the trade evaluator, not
that the widening was measured out of sample on it. The out-of-sample evidence
about the chances themselves is `docs/pickups_backtest.md`.

The run took 73s against 61s, on the same 55 deals and the same T30 window;
the full output is in `docs/runs/2026-09-23-trade-calibration.md`.

---

**The evaluator is no better than a coin flip at picking which side of a trade
did better, and none of the three revisions changed that.** On the 55 deals
this database can both evaluate forward and grade in hindsight, the headline
picked the side that came out ahead 25 times on the window declared as primary
— 45%, where a fair coin gives between 20 and 35 of 55 nineteen times in
twenty. That is the same 25 of 55 revision R1 gave yesterday, and the per-man
number before it gave 27. Every cell of every table we said we would print
lands inside that coin's interval, so none of the differences between them is
a finding either.

**What did change is the one thing in the calibration that was ever a model
defect rather than a forecast miss.** Deals that send two men for one were
over-rated by +0.389 categories a week, and §7b asked one question about it:
how much of that was the two engines pricing an emptied roster place
differently? The answer is nearly all of it. Re-pricing the forecast alone
leaves the gap at +0.404 — very slightly worse. Re-pricing the hindsight
yardstick beside it brings the gap to **+0.103**. The number was mostly an
argument between two pieces of our own arithmetic about what an empty roster
place is worth, and `docs/streaming_lane.md` settled the argument: 0.38
categories a week, not 0.06.

The sides of a deal where nobody's roster gains or loses a place are
**identical to yesterday's, to three decimals** — 85 sides, 54% sign
agreement, mean error −0.020 — which is the check that nothing else moved.

The other thing the previous run found still stands. **The evaluator is decent
at players and hopeless at deals.** What it says a man is worth a week ranks at
+0.39 against what his real box scores were worth over the next month, across
174 men in these deals — unchanged by R2, which is the regression check we
asked for — and subtracting one side of a trade from the other leaves −0.02.
The forecasts are not noise. The difference of two of them, on two rosters, is.

What is built is still worth having, and the calibration is the reason to say
so carefully rather than the reason to bin it:

- A trade is judged in **exactly the currency a pickup is judged in** —
  expected category wins, this week plus the change per week over the weeks
  left, with the projected end-of-season record either way (`Judgement`). So
  "trade for him" and "pick him up off the wire" are two numbers on one scale.
- It shows **the nine categories one by one**, in counts and in the chance of
  winning each in a typical week. That is the part a nine-cat manager cannot
  get anywhere else, and it is the part the calibration says nothing against:
  whether the FT% you are giving away costs you anything is a fact about your
  roster today, not a forecast.
- It judges **the other side too**, with the same machinery, and says plainly
  that this is our estimate of his roster's needs rather than his opinion.
- It **shows its working**: games remaining, the games behind every
  projection, who is hurt, who is thin.
- It now **carries its own record**. `app/trades/calibration.py` holds the
  published run as data and a `CALIBRATION_NOTE` the page prints verbatim
  under the number, in the manager's own words: how often this number has
  pointed at the side that did better, over what window, on how many deals,
  and what a coin does on the same sample.

Read it as a calculator for a conversation, not as an oracle. That is what
`docs/pickups.md` calls a tool and not gospel, and a 45% hit rate on the
outcome is the strongest possible argument for the language being careful.

The summary sentence under a deal now says the fit first and the number
second, for the same reason: the categories it wins and gives up are the same
week laid out one line at a time, and the headline is a forecast with the
record above.

---

## 1. What it judges, and in what

Module `app/trades/`, three files: `evaluate.py` (the engine), `summary.py`
(the sentence under it) and `calibration.py` (the published record, as data
and as a note the page prints). Everything is a function of loaded state, and
the whole of `app/pickups/judge.py` is reused rather than re-derived.

```
evaluate_trade(session, league_season, today, side_a, side_b, *, drops=...) -> TradeReport
```

A `TeamOffer` is a team (ESPN id) and the player ids it gives away. For each
side the report carries a `SideReport` with:

| | what |
|---|---|
| `judgement` | `app.pickups.judge.Judgement`: this week's delta, the rest-of-season delta per week, the weeks left, the net, and the projected end-of-season record with and without |
| `season_independent` | the season term the first cut used, each man valued on his own inside a league-average team, kept beside the headline so both can be measured (§7a, §7) |
| `categories` | nine `CategoryView`s: the counts before and after in an ordinary week, and the chance of winning each before and after |
| `playoffs` | `PlayoffLens`: the same arithmetic over the playoff matchup periods alone, or a sentence saying why it cannot be counted |
| `receives` / `gives` / `drops` | `PlayerCard`s: what each man is worth a week, his games left, his playoff games, his status, and what his projection rests on |
| `places_opened` / `places_used` / `replacement` / `opened_value` | how a 2-for-1 was settled, and at what: the man the wire offers, and what the place it empties is worth once it is streamed (§7b) |
| `summary` | the plain-English line, generated from the numbers |
| `notes` | the honest caveats for this side |
| `finish` | where the deal leaves this side in the projected standings: the projected record, the place and the playoff odds before and after (§1a) |

The currency is the recommender's, unchanged:

    net = delta_week + delta_season_per_week * weeks_remaining

**`delta_week`** is `app.pickups.stream.week_deltas` — the same daily seating
against the same lineup, the same normal head-to-head against the actual
opponent, the same knowable lines. Not an approximation of the streaming
report's number: the function itself.

**`delta_season_per_week`** is the expected category wins of this team's
active roster in an ordinary week with the deal in it, less the same week
without it, through the league standard (`Standard.week_wins` over the summed
`weekly_lines`). It is the same pair of lines the nine-category table is drawn
from, so the headline number and the table are one arithmetic read two ways.
That is **revision R1**, declared in §7a before it was run; §3 is the
per-place rule it replaced, which is still what a pickup is charged by, still
what the playoff lens reads, and still on the payload as
`season_independent`.

**`weeks_remaining`** is `weeks_after_this_period(today)`: the matchup weeks
after the one being played, so `delta_week` and the season term never share a
day.

The bar is `TRADE_HURDLE`, which *is* `SEASON_HURDLE_PAID` (0.20 categories a
week, set by the pickup backtest on 2026-09-21). A trade is a rest-of-season
decision that costs players, so it is labelled on the same scale as a claim
that costs FAAB. The bar labels and never hides: a deal under it is printed in
full with its number, and the most the report ever says is "clears the 0.20
bar" or "does not".

### 1a. The finish, added 2026-09-23

Everything above answers "what is this deal worth", in categories, against a
league-average opponent. It does not answer the question the owner actually
asked, which is *where does it leave me*. Since 2026-09-23 each side also
carries a `finish`: the projected final category record, the place and the
playoff odds, before the deal and after it, from the projected-standings
engine (`docs/projected_record.md`) run twice on the same seed with **both**
rosters changed in the second run -- because the deal happens to both sides at
once. The rosters are the evaluator's own, man for man: each side's `gives`
and `drops` leave, its `receives` and `fills` arrive. `docs/what_if.md` is the
write-up, and it shares its engine with the `/what-if` route for a named
pickup.

**It is a second lens and not a second bar.** Nothing on this page is
labelled, ranked or refused on the finish; the bar is still `TRADE_HURDLE` on
the net. The finish comes with the simulation's own ninety-five percent
sampling band beside it (`odds_band`, about a point at ten thousand seasons)
and the projected record's published calibration sentence underneath, because
that forecast has a record of its own and it is not the trade number's.

On the page it sits under the nine categories, one compact line per side:

    Projected 94.6-76.4 · 3rd · playoffs 92.7% → 94.4-76.6 · 3rd · 92.2%
    (±0.5 points on each)

The deal's own cost: one more reading of the league's measured weekly spreads
would have been 2.9 s, so the route now reads them once and hands them to the
evaluator and to both projections; the finish layer itself adds about 1.5 s
(`docs/what_if.md` §4).

---

## 2. When the trade lands

A trade cannot be in a lineup the moment you think of it. The other manager
has to accept, and ESPN holds it for review.

**The league's review setting is not in the database.**
`league_seasons.raw_settings` holds `schedule`, `matchup_periods`, `scoring`
and `division_map` and nothing else — checked on the live database on
2026-09-21 for every season 2019 to 2027. `league_seasons.trade_deadline` is
the deadline, not the review window. So, exactly as
`app.pickups.state.ADDS_PER_PERIOD_DAY` carries the add budget, the number
lives in the code with its provenance:

```python
TRADE_REVIEW_DAYS = 1
```

and the evidence is the 2026 ledger. There are four executed `TRADE_ACCEPT`
rows that season, on scoring periods 22, 53, 85 and 108. In every one of them
the players named were in the old team's `daily_lineup_slots` through the day
*before* the transaction and in the new team's on the day *of* it. The
`TRADE_UPHOLD` confirmations land on the same day as the accept. So a deal
agreed overnight is in a lineup the next morning, and a deal you are looking
at this morning is in a lineup tomorrow at the earliest.

That is a floor, not a promise: a manager who has already shaken hands passes
`review_days=0`, a league that votes for two days passes 2, and the report
prints which number it used and where the number came from.

**What the effective day does to the week.** `week_deltas` takes an
`effective_day`, and the days of this period before it are projected with the
roster as it stands — on *both* sides of the comparison, so they cancel in the
delta while still counting toward the totals the nine probabilities are read
off. Seating is per day and independent, so splitting the window in two is
exact rather than an approximation, and with no effective day the function
returns the number it always did.

If the review pushes the deal past the end of this matchup period,
`delta_week` is exactly zero — no day of this week is the new roster's — and
the period it does land in is one of the weeks `weeks_remaining` covers, so
nothing is counted twice and nothing is dropped.

---

## 3. Uneven trades, and the one generalisation

A pickup touches one roster place. A trade touches several, and unevenly. The
rule `judge.py` already had —

    season_cost = max(value(dropped), wire) - max(value(added), wire)

— is a statement about one place, so it was restated per place and summed,
in `judge.py`, as `places_cost`:

    before = sum over places vacated of max(value(man leaving), wire)
    after  = sum over places filled  of max(value(man arriving), wire)
             + what the places vacated and not refilled are worth

A place a move fills that was empty was worth nothing, so the arrival is
credited in full. A place a move leaves open is worth what it returns while it
stays open — and that is **not** the same number as what one waiver pickup
returns, which is what this code charged until 2026-09-22.
`docs/streaming_lane.md` measured a place that is left open and streamed at
**0.38 categories a week** against the held 13th man's 0.00, because a
streamed place has a live body in it every day. So an opened place is worth
the better of the man the wire offers and that lane (`OPENED_PLACE`, revision
R2 in §7b), and a second opened place is worth the better of that man and a
single ordinary pickup, because the seven-add budget and not a decay curve is
what limits a team to one lane at a time. All of it falls out of the two sums
with no special case, which is why a free add, an injured-reserve move, a
one-for-one stream and a three-for-two trade are still one line of arithmetic.

**Only a move that empties a place is charged differently.** A one-for-one
swap opens nothing, so every number the pickup recommender has ever printed is
untouched; the suites prove it by passing unchanged, exactly as they did
through R1.

**The pickup path's numbers did not move.** `season_cost` is now written in
terms of `places_cost` rather than beside it, `judge()` calls `places_cost`,
and the existing suites — `test_pickups_judge.py`, `test_pickups_stream.py`,
`test_pickups_season.py` — pass unchanged. That is not luck: the recommender
only ever passed one man on each side (the rest-of-season report always
overrides the season term with the optimizer's own), and for one man a side
the two formulas are identical by construction. Revision R1 did not move them
either, for the same reason and one more: a pickup never goes through the
trade evaluator at all. The three suites passed untouched after it too.

**This is no longer the trade's headline.** Since R1 the headline season term
is the roster with-and-without described in §1, and `places_cost` is what
`SideReport.season_independent` carries beside it, what the playoff lens
still reads, and what every pickup is still charged by. §7 is why it was
demoted and what demoting it did — which, on the shape it was demoted for,
was to make the error larger.

**Who gets dropped.** A side that receives more men than it gives needs a
place for each extra one. Open places take them first (`week.open_slots`);
after that somebody goes. The caller may name him (`drops=`), and by default
it is the **cheapest man on the active roster** by what his place is worth —
which is `app.pickups.season`'s drop candidate, read one at a time. The report
names him, says whether he was named or chosen, and prints what he cost. Men
on injured reserve are never chosen, because dropping one frees no active
place; that is the same reading of `open_slots` / `ir_slot_free` that
`app/pickups/state.py` does.

---

## 4. Both sides

A trade only happens if the other manager says yes, so the report is built
from his roster too — his week, his opponent, his league-standard values, his
playoff games — with the same functions. Every surface says what that is: *our
estimate of their side, not what they think*. It is built on our projections
and our lens, and his roster's real needs are known only to him.

It is still the most useful number on the page. A deal that reads +0.5 for us
and −0.6 for him is a deal that will not be accepted, and the whole reason
trades are where category leagues are won is that two rosters built
differently can both gain from one deal: the report is what shows you whether
this is one of those.

**One case the first draft got wrong and now handles.** When the counterparty
is also this week's opponent, the deal changes both rosters inside one
matchup. `week_deltas` therefore takes an `opponent_move` and applies it to
the opponent's roster from the same effective day; without it, a trade with
the man you are playing would be judged against a roster he no longer has.

---

## 5. No look-ahead, and how it is proved

Yesterday's rehearsal (`docs/inseason_rehearsal.md`) found four functions that
were right live and wrong on any replayed day. The calibration in §7 is only
meaningful if this module does not add a fifth, and a docstring is not
evidence.

Every query takes `today` and reads nothing on or after it:

- projections filter `scoring_period < today` (`app.scoring.knowable`), and so
  does the minutes tilt;
- the roster is the latest lineup day at or before `today`;
- the posted totals are the days of this period **before** `today`
  (`app.pickups.state._posted`, fixed 2026-09-21);
- the banked record counts only matchup periods already finished;
- games remaining come from `pro_team_games`, which is a fact about the future
  that is on record today, and is supposed to be read;
- `review_days` moves only the day the deal is *seated* from, never the day
  the data is read as of.

**The test that makes it credible**
(`tests/test_trades.py::test_the_report_reads_nothing_after_the_day_it_is_judged_on`):
the fixture gives every man in a deal a monstrous set of box scores on every
day after the judgement day — lines nothing could have known that morning. The
report is built. Every `player_game_stats` and `daily_lineup_slots` row after
the judgement day is then deleted, and the report is built again. The two
`TradeReport`s must be equal, field for field. If any query reached past
`today`, deleting the rows it read would move a number. The NBA schedule is
deliberately *not* deleted, because that is the one thing about the future the
report is entitled to see.

---

## 6. The payload, and the page it is drawn on

The dataclasses were designed as the thing a page draws, so a schema is a
transcription rather than a redesign. One `TradeReport`, two `SideReport`s,
symmetrical:

```
TradeReport
  season, today, effective_day, review_days, review_source
  first_scoring_period, last_scoring_period, weeks_remaining
  hurdle, pool_size, historical_wire, notes[]
  sides[2]:
    team_id, team_name
    receives[] / gives[] / drops[] / fills[]:   PlayerCard
      player_id, name, value, games_left, playoff_games,
      injury_status, expected_return_date,
      games_so_far, had_projection, projection_source, thin, hurt
    drop_source, places_opened, places_filled, places_left_open, places_used
    replacement, opened_value, replacement_player
    judgement:                             Judgement (as the pickup routes already send it)
      delta_week, delta_season_per_week, weeks_remaining, replacement,
      banked, record_without, record_with, measured,
      delta_total, weeks_covered, per_week
    season_independent                     the old per-man season term, labelled
    categories[9]:                         CategoryView
      abbreviation, before, after, delta, p_before, p_after, p_delta, moved
    playoffs:                              PlayoffLens
      first_scoring_period, last_scoring_period, weeks, games,
      delta_per_week, delta_total, categories[], note, measurable
    expected_per_week, hurdle, clears
    summary, notes[]
```

Four things a page should draw and not hide:

1. **The nine, as a two-column diff with a probability bar.** The counts are
   the change; the probability is whether the change matters. A manager
   punting FT% should be able to see at a glance that the FT% he is giving
   away costs him nothing — that is `p_delta` near zero beside a large
   `delta`, and the summary already says so in words.
2. **`review_source`, `historical_wire` and every `note`.** They are the
   difference between a number and a number you can argue with.
3. **`thin` and `hurt` on every `PlayerCard`.** A trade evaluation that hides
   a 12-game sample is worse than none (`THIN_GAMES = 12`).
4. **`app.trades.calibration.CALIBRATION_NOTE`, verbatim, under the number.**
   It is three sentences of plain English saying how often this number has
   pointed at the side that did better, on how many deals, over what window,
   and what a coin does on the same sample — and that the category table does
   not depend on it. A forecast printed without its record is the one thing
   §7 says we must not ship.
5. **`fills`, and the two counts beside `places_opened`.** A place with a man
   in it and a place left open are settled by different arithmetic and the
   payload says which is which: `places_filled` is how many the caller named
   somebody for, `places_left_open` how many are still valued as a lane, and
   `opened_value` is about the second kind alone. §11 is the rule.

The report is bounded, so the route returns an object rather than a `Page`,
like both pickup routes. It makes no ESPN request. A season with no schedule
or roster was going to be a 409 for the same reason `/pickups/stream` is, and
is not: §10 says why, and what it answers instead.

---

## 7. The calibration: it still does not predict the outcome, and the consolidation error was mostly the yardstick

Read §7a and §7b first. Two revisions, each declared before its run, each
followed by exactly one re-run, published whichever way it fell. The numbers in
this section are the re-run of 2026-09-22; both earlier runs are kept at the
bottom as the "before".

**Re-run again on 2026-09-23** after the weekly spread was widened by two (§0,
revision R3), because `head_to_head` is what `Judgement.delta_week` is built
from. Every figure below came back identical, and §0 says why: what this
section scores is the per-week *season* term, which does not read the
head-to-head at all. Only the wall time changed, 61s to 73s, and the line
below still says 61s because it is the printed output of the run the section
was written from. The 2026-09-23 output is in
`docs/runs/2026-09-23-trade-calibration.md`, and it differs from the block
below in that one number.

### The answer

**Primary cell, as declared — the headline under revision R2, against the
thirty days after the deal, graded by a yardstick that prices an emptied
roster place the same way: the evaluator picked the side that did better in 25
of 55 deals, 45%. A fair coin gives between 20 and 35 of 55 nineteen times in
twenty.** R1 gave 25 of 55 on the same cell yesterday and the per-man number
gave 27 of 55 the day before. The headline has now been revised twice and has
not moved off the coin.

Three things the run does say:

- **The consolidation error was mostly the yardstick, and the measurement
  named in §7b is what settles it.** The uneven sides — two men for one, the
  shape both revisions were written for — had a mean error of +0.389
  categories a week under R1. Under R2's forecast against the *old* yardstick
  it is **+0.404**: re-pricing the forecast alone made it very slightly worse,
  which is what §7b predicted it would do, because the evaluator already
  credited the opened place generously. Under R2's forecast against R2's
  yardstick it is **+0.103**. Between three quarters and all of the
  over-rating was the hindsight grade charging 0.07 for a place the forecast
  filled with a real man, and `docs/streaming_lane.md` says the truth is 0.38.
- **Nothing else moved, and the run proves it rather than asserting it.** The
  even-count sides — where no place is opened, so neither engine can have been
  re-priced — come back at 85 sides, 54% sign agreement, Spearman +0.10, mean
  error −0.020, mean absolute error 0.279, which is R1's row to the last
  decimal, under both yardsticks.
- **The player-level result is unchanged.** 174 men, Spearman +0.39 against
  what their real box scores were worth over the same thirty days. That was
  the regression check §7b asked for, and it passed: R2 re-prices places, not
  players.

And one thing it does not say. **The headline's own accuracy barely moved and
what movement there is cuts both ways.** The mean error on the primary cell
fell from +0.073 to +0.008 and the mean absolute error from 0.328 to 0.299,
both of which look like the settlement being fixed; the rank correlation over
the sides went from +0.01 to −0.02, and on the uneven sides from −0.36 to
−0.41. All four are inside the noise of 110 sides and none of them is claimed.

### The run, in full

**The primary cell, named before the run: R2 (the roster) x next 30 days, deal level. The evaluator picked the side that did better in 25 of 55 deals (45%), where a coin gives 20-35 of 55 (36%-64%) nineteen times in twenty.** Over the 110 sides the rank correlation is -0.02 and the mean error +0.008 categories a week. 110 sides across 6 seasons is the whole sample and every figure below rests on it. Generated by `scripts/trade_calibration.py` in 61s, review_days=1, short window 30 days.

For scale: the predictions have a spread of 0.287 categories a week and what was delivered a spread of 0.255, so the mean absolute error of 0.299 is about the size of the thing being predicted.

### The 2x2 and the old yardstick beside it, every cell from one run

| headline | horizon | deals | picked the better side | sides | Spearman | mean error | mean abs error |
|---|---|---|---|---|---|---|---|
| **R2 (the roster)** | **next 30 days** | 55 | 25 of 55 (45%) | 110 | -0.02 | +0.008 | 0.299 |
| R2 (the roster) | rest of season | 55 | 31 of 55 (56%) | 110 | +0.09 | +0.013 | 0.262 |
| per man (the old headline) | next 30 days | 55 | 19 of 55 (35%) | 110 | -0.19 | -0.065 | 0.287 |
| per man (the old headline) | rest of season | 55 | 29 of 55 (53%) | 110 | +0.02 | -0.060 | 0.233 |
| R2 (the roster) | next 30 days, old yardstick | 55 | 25 of 55 (45%) | 110 | +0.00 | +0.077 | 0.325 |
| R2 (the roster) | rest of season, old yardstick | 55 | 30 of 55 (55%) | 110 | +0.09 | +0.082 | 0.283 |

The two sides of a deal are not two observations -- the prediction is very nearly antisymmetric between them -- so the deal-level column is the independent question and the side-level columns describe the same data twice. For 55 deals, a coin gives 20-35 of 55 (36%-64%) nineteen times in twenty.

### Splits, on the primary cell (R2 (the roster) x next 30 days)

| | sides | sign agreement | Spearman | mean error | mean abs error |
|---|---|---|---|---|---|
| all sides | 110 | 51% | -0.02 | +0.008 | 0.299 |
| even counts | 85 | 54% | +0.10 | -0.020 | 0.279 |
| uneven counts | 25 | 40% | -0.41 | +0.103 | 0.366 |
| nobody broke down after | 85 | 55% | +0.12 | +0.028 | 0.271 |

### The settlement of an opened place, isolated

| prediction | yardstick | horizon | uneven sides | mean error |
|---|---|---|---|---|
| R2 | R2 | next 30 days | 25 | +0.103 |
| R2 | R2 | rest of season | 25 | +0.068 |
| R2 | the old flat level | next 30 days | 25 | +0.404 |
| R2 | the old flat level | rest of season | 25 | +0.370 |
| R1, on 2026-09-21 | the old flat level | next 30 days | 25 | +0.389 |
| R1, on 2026-09-21 | the old flat level | rest of season | 25 | +0.355 |

The per-man headline gave +0.265 over 25 sides against the rest of the season on 2026-09-21, which is the number R1 was meant to fix and did not.

**The check: the even-count sides must not have moved.** No place opens on them, so neither the forecast nor the grade can have been re-priced, and the run of 2026-09-21 reported 85 sides, sign agreement 54%, Spearman +0.10, mean error -0.020, mean absolute error 0.279. This run:

| | sides | sign agreement | Spearman | mean error | mean abs error |
|---|---|---|---|---|---|
| even counts, R2 yardstick | 85 | 54% | +0.10 | -0.020 | 0.279 |
| even counts, old yardstick | 85 | 54% | +0.10 | -0.020 | 0.279 |

### How sure it was, decided on the prediction

| deals ranked by |predicted edge| | n | picked the better side | a coin |
|---|---|---|---|
| top third (edge at or above 0.40 a week) | 18 | 8 of 18 (44%) | a coin gives 5-13 of 18 (28%-72%) nineteen times in twenty |
| the other two thirds | 37 | 17 of 37 (46%) | a coin gives 13-24 of 37 (35%-65%) nineteen times in twenty |

### The players themselves

For every man in a scored deal (174 of them, counted once per deal), what the evaluator said his roster place was worth on the morning against what his real box scores were worth per week over the same thirty days, through the same league-standard lens: Spearman **+0.39**, Pearson +0.35, mean error +0.028 categories a week, mean absolute error 0.213. Predicted spread 0.248, delivered spread 0.255.

Against the decision lens (`MoveGrade.decision`, the same day's data through the hindsight engine's own arithmetic): Spearman +0.55 for the roster headline and +0.48 for the per-man one.

### By season, on the primary cell (R2 (the roster) x next 30 days)

| season | sides | sign agreement | Spearman | mean predicted | mean delivered |
|---|---|---|---|---|---|
| 2019 | 10 | 60% | +0.24 | +0.176 | +0.086 |
| 2021 | 6 | 17% | -0.37 | +0.118 | +0.023 |
| 2023 | 6 | 50% | +0.03 | +0.253 | +0.083 |
| 2024 | 26 | 65% | +0.31 | +0.082 | +0.038 |
| 2025 | 28 | 64% | +0.02 | +0.030 | +0.073 |
| 2026 | 34 | 32% | -0.31 | +0.038 | +0.084 |

### The worst misses, and why

- **2021 day 84, Thibs Dust: in Myles Turner; out Kevin Durant** -- predicted -0.994 a week, delivered +0.227 over 5 period(s). Kevin Durant left and then broke down, so the side that gave him up is credited by hindsight for a risk it did not take.
- **2021 day 84, Embiids Burner Account: in Kevin Durant; out Myles Turner** -- predicted +0.963 a week, delivered -0.164 over 5 period(s). Kevin Durant played under half the games scheduled after the deal: an injury nobody had on the day, which is luck and not a bug.
- **2026 day 72, Optimize the MVPs: in Deni Avdija, Onyeka Okongwu; out Nikola Jokic, Ryan Nembhard** -- predicted -0.339 a week, delivered +0.743 over 5 period(s). the projections held up; the gap is what the men did afterwards -- the season term is this roster's week with the deal and without it, and the grade counts what the lineup really posted.
- **2026 day 72, Brighton Bears: in Nikola Jokic; out Deni Avdija, Onyeka Okongwu** -- predicted +0.455 a week, delivered -0.328 over 5 period(s). an uneven deal: the evaluator fills the place it opens with the best man on the wire and the hindsight grade charges a flat replacement level, so part of this gap is the two settlements rather than the forecast.
- **2023 day 13, Foxes ShutUpNDribble: in Joel Embiid; out Jaylen Brown, Deandre Ayton, Josh Giddey** -- predicted +0.480 a week, delivered -0.297 over 6 period(s). the line for Joel Embiid, Jaylen Brown, Deandre Ayton, Josh Giddey rested on fewer than a dozen games of his own, which the report flags and this run does not discount.
- **2023 day 84, Tom's Team: in Joel Embiid; out Kyle Kuzma, Damian Lillard** -- predicted +0.411 a week, delivered -0.275 over 5 period(s). an uneven deal: the evaluator fills the place it opens with the best man on the wire and the hindsight grade charges a flat replacement level, so part of this gap is the two settlements rather than the forecast.
- **2024 day 77, I AM SCOOT: in Brandon Ingram; out Tyrese Haliburton** -- predicted -0.471 a week, delivered +0.197 over 5 period(s). Tyrese Haliburton left and then broke down, so the side that gave him up is credited by hindsight for a risk it did not take.
- **2024 day 72, Brighton Bears: in Joel Embiid; out Bradley Beal, Ja Morant** -- predicted +1.013 a week, delivered +0.347 over 5 period(s). the line for Bradley Beal, Ja Morant rested on fewer than a dozen games of his own, which the report flags and this run does not discount.

### What could not be scored

- 39 sides where only one half of the deal left a roster trace.
- 3 sides of trades with more than one counterparty.
- 5 events where the two teams' reconstructions did not mirror.
- 0 sides with no gradeable stretch after the move.
- 0 sides with no matchup period inside the thirty days.

(The two uneven deals still in the "worst misses" list carry a sentence blaming
the two settlements. It was written for the run before this one and it is now
only half true: the settlements still differ — the forecast puts the named free
agent's whole week into the after-roster, and the grade adds a scalar — but
both now value the place at the same 0.38 rather than at 0.38 against 0.07.
The sentence is generated from the rows and has been left as the run printed
it.)

### Reading it

**1. The one measured model defect was mostly a disagreement between our own
two engines.** That is the finding, and it is the first time this calibration
has produced one that is about the code rather than about the future. §7 of
the previous run said the forward side "credits that place a good deal more
generously than the hindsight grade's flat replacement level does" and wrote
the measurement down as not-done. Done, it accounts for roughly three quarters
of the +0.389: the forecast was not being generous, the grade was being mean,
and the number that says which is `docs/streaming_lane.md`'s 0.38 a week
against the 0.06 the code had.

**2. The forecast side of R2 is a rounding error, and we said in advance it
might be.** +0.389 to +0.404 on the same yardstick. The evaluator already put
the best free agent's whole week into the place a deal opens, and on a
reconstructed wire that man is usually worth more than a streamed lane, so the
floor rarely binds. R2's effect on the *forecast* is therefore almost entirely
about the pickup path and the drop, not about trades — which is the opposite
of where it was expected to matter, and is why the declaration named the
yardstick as a separate row pair before the run rather than after it.

**3. The headline is still a coin, after two revisions.** 27 of 55, 25 of 55,
25 of 55. Two declared revisions, two published runs, no movement. The honest
reading is the one §0 has carried since yesterday: this is a fit tool, not a
winner-picker, and the page leads with the nine categories for that reason.

**4. Confidence still does not help.** The split was defined before the run,
on the prediction alone: the top third by predicted edge picked the better side
in 8 of 18, the other two thirds in 17 of 37. Identical to yesterday, which is
expected — R2 barely moves the predictions, so it barely moves their ranking.

**5. The two forward lenses agree with the decision lens a little more than
they did.** Against `MoveGrade.decision`, the roster headline now ranks at
+0.55 and the per-man number at +0.48, against +0.49 and +0.44 yesterday. Both
lenses moved together, which is what a shared settlement should do, and both
still fail to predict the result.

**6. Injuries still do most of the work.** Splitting on whether anybody in the
deal played under half his scheduled games afterwards moves sign agreement from
51% to 55% and the rank correlation from −0.02 to +0.12 — still **not** a
result that can be claimed, because the split is made on the outcome. Where the
remaining honest work is has not changed: availability, not production.

### The two runs before this one

**2026-09-21, the per-man headline against the rest of the season**, before any
of this: 27 of 55 deals (49%), sign agreement 46% over the sides, Spearman
+0.05, mean error +0.006, mean absolute error 0.253. Uneven counts 48% and
−0.19 on 25 sides, mean error **+0.265**.

**2026-09-21, revision R1, the roster with-and-without**: 25 of 55 (45%) on the
thirty days and 30 of 55 (55%) on the rest of the season; Spearman +0.01 and
mean error +0.073 on the primary cell; even counts 85 sides, 54%, +0.10,
−0.020, 0.279; uneven counts 25 sides, 44%, −0.36, mean error **+0.389**;
player level 174 men at +0.39; against the decision lens +0.49 and +0.44.

Both of those were graded by the old yardstick. This run's old-yardstick rows —
25 of 55 and 30 of 55, mean error +0.077 and +0.082 — are the check that it is
the same sample scored the same way, and they match R1's to within the
re-pricing of the forecast.

### Not done, and deliberately

Ideas that arrived *after* the numbers, written down here instead of being
built, because the whole point of §7a and §7b is that one revision per run is
all we allow:

- **Settle the opened place identically in both engines.** They are now the
  same *price* and still not the same *arithmetic*: the forecast puts the named
  free agent's whole weekly line into the after-roster, where it runs through
  saturating probabilities, and the grade adds a scalar. The remaining +0.103
  is the obvious place to look for what that is worth, and it is the next
  measurement rather than this one.
- **Gate the opened place on the add budget.** `docs/streaming_lane.md` §6 is
  emphatic that seven adds a period is the binding constraint and that 16.2% of
  team-periods spend the lot; `adds_left` already exists in
  `app.pickups.state`. A team with no adds left cannot stream an open place at
  all, and the code currently prices it as though it could.
- Report the two sides as a ranking rather than a number, since the rank is the
  only part anybody acts on and the magnitude is demonstrably noise.
- Show the player-level number on the page, which is the part that measured
  well, rather than only the deal-level difference, which did not.
- ~~Model availability. Every run of this calibration has ended in the same
  sentence.~~ **Done on 2026-09-24**, and the run that scored it is R5 in §0:
  a replayed morning reads the NBA's own report ([`replay_status.md`](replay_status.md)).
  What is left of it is the five seasons whose reports are not loaded on this
  machine, which is one command on the VPS.

### What a played season costs the measurement

Everything in §7 is run without listener data, because the listener has never
run for a played season:

- ~~**No injury statuses.**~~ **Fixed 2026-09-24** (§0 R5,
  [`replay_status.md`](replay_status.md)). Every player used to be projected as
  fully available on the morning of the deal, on both sides, because the only
  status source was the listener's and the listener never runs for a played
  season. A replayed morning now reads the NBA's own official report as of ten
  o'clock Eastern that day. Two limits remain: the reports are loaded here for
  2026 only, so the other five seasons are still status-blind, and the league
  names only players whose team plays that day, so a man whose team was idle
  still reads as fit.
- **A reconstructed wire.** `app.pickups.state.historical_free_agents` —
  whoever played that scoring period and was in nobody's lineup. It cannot see
  a free agent who did not play, and it knows nothing about waivers, so the
  `wire_replacement` the opened places are charged at is an approximation. It
  is also why R2's floor rarely binds on the forward side: a reconstructed wire
  is stocked with men who played.
- **No minutes tilt.** The tilt keys on `player_status_events`, and played
  seasons hold none, so it is off everywhere — not measured as worthless.
- **About half the trades are unrecoverable.** 39 sides had only one half of
  the deal leave a roster trace, three were multi-team, and five events had the
  two teams' reconstructions disagree about what changed hands. All are
  counted and named in the run's output.

This is the same handicap `docs/pickups_backtest.md` carries, and for the same
reason: the one season with listener data is the one that has not been played.

### Re-running it

```
.venv/bin/python scripts/trade_calibration.py
```

It prints the markdown above; `--out FILE` writes it as well, `--seasons`
narrows it and `--review-days` changes the day the deal is seated from. The
section above is pasted from the run of 2026-09-22. Read-only, about a
minute, local database only.

---

## 7a. Declared before the re-run, 2026-09-21

One revision, declared here before it was run, then exactly one re-run of the
calibration, published whatever it showed. With 55 deals, anything more is
fooling ourselves. Everything below this line was written **before** the
revision was built and before any number came back; §7 above is the "before"
it will be read against.

**Revision R1 — the headline is judged on the roster.** The season term of a
side's judgement becomes: the expected category wins per week of that team's
actual active roster with the deal, minus without it, against the league
standard — i.e. `Standard.week_wins` (or whatever the nine-category table
already uses to produce its win probabilities) applied to the summed
rest-of-season weekly lines (`weekly_lines`) of the active roster before and
after. In an uneven deal the opened place is filled by the named best free
agent (as the table already shows) and the forced drop removes the named man,
inside the same before/after. The old per-man number stays in the payload as
`season_independent`, labelled, so both can be measured; the bar, the week
term, the playoff lens and the language are unchanged apart from reading the
new number. The pickup path's numbers must remain identical (it does not go
through the trade evaluator; prove it by the existing suites passing
untouched).

**Target T30 — the next thirty days.** Beside the existing rest-of-season
hindsight grade, grade each side over the 30 days after the deal lands — the
window `scripts/pickups_backtest.py` uses. Use `grade_move`'s own arithmetic
restricted to that window; if it cannot be windowed without new machinery,
build the minimum and say exactly what you built.

**What will be reported, all of it, from one run:**

- The 2×2: {old headline, R1 headline} × {rest-of-season, T30}, each with
  deal-level "picked the side that did better" (n of N) and side-level
  Spearman and mean error. **Primary cell, named now: R1 × T30, deal-level.**
- Uneven sides' mean error under R1 (was +0.27 under the old headline).
- An ex-ante confidence split, defined now: deals ranked by |R1 predicted
  delta|, top third versus the rest, deal-level hit rate in each. (Defined on
  the prediction, not the outcome, so it is a legitimate claim whichever way
  it falls.)
- A player-level diagnostic: for every player in the scored deals, the
  evaluator's predicted weekly value on the day versus what he delivered per
  week over T30 (same lens), Spearman and n. This separates "the projections
  carry no signal" from "differencing two similar players destroys it".
- A coin's 95% interval for the deal-level n, printed beside every hit rate,
  so nobody reads 31 of 55 as a finding.

**What will not be done after the run:** no change to R1, the window, the
splits, the sample, or the bar. Seasons, exclusions and `review_days=1` are
exactly yesterday's.

---

## 7b. Declared before the re-run, 2026-09-22

The same discipline as §7a, for the same reason: one revision, declared here
before it was built, then exactly one re-run of the calibration, published
whichever way it falls. Everything below this line was written **before** any
code changed and before any number came back; §7 above is the "before" it will
be read against.

**Why there is a revision at all.** Every roster place this code prices is
priced as if one man held it: `TYPICAL_PICKUP = 0.06` categories a week, the
median return of a single executed add. `docs/streaming_lane.md`, merged today,
measured what an *opened* place actually returns when it is streamed: **0.38
categories a week per place (IQR 0.23–0.53), against the held 13th man's
0.00**, over 1,536 team-periods of 2019–2026. It is not a volume effect — a
lane starts 4.67 games a week to a held man's 5.00, because a team starts at
most ten men on a day — it is that a team's 13th man mostly produces nothing
while a streamed place has a live body in it every day. §7 finding 3 named the
two engines' different settlements of an opened place as part of the
consolidation error, and the lane value is the same size as that error.

**Revision R2 — an opened place is priced at what a streamed place returns.**
Two numbers where there was one:

- `TYPICAL_PICKUP` (0.06) stays as the floor under a single ADD — what a man
  picked up and kept returns. Unchanged everywhere it already means that.
- A new constant, `OPENED_PLACE = 0.38` categories a week
  (`docs/streaming_lane.md`, pooled median per place, 2019–2026; the lower
  quartile 0.23 is recorded beside it as the conservative figure), is what a
  roster place returns when it is left open and streamed. It applies wherever
  the code values a place that a move EMPTIES: `places_cost` / `season_cost`
  when a move opens a place (the `empty=` path and the N-for-M generalisation),
  the trade evaluator's 2-for-1 settlement (the side that receives fewer men
  than it gives), and `grade_move`'s replacement charge for an opened place in
  `app/scoring/moves.py`, which today charges a flat per-season median of
  `pickup_values`, about 0.07.
- **The add budget caps it.** An opened place is only worth streaming while
  adds remain, so the k-th opened place on a roster is priced at `OPENED_PLACE`
  for the first and at the measured decay for the second and after — read off
  `docs/streaming_lane.md` §6/§5 (lane 2 against lane 1 under the tight
  definition). If the document says the decay could not be measured cleanly,
  the first-lane value is used for one place and `TYPICAL_PICKUP` for every
  further place, and the write-up says so. A 2-for-1 opens exactly one place,
  so the calibration is not sensitive to this choice; the pickup path can be.
- **The pickup path's existing numbers must not move for any one-for-one swap**
  (no place is opened): proved by the existing pickup, judge, season and stream
  suites passing untouched. Only moves that OPEN a place change, and
  `docs/pickups.md` gets one as-built paragraph saying so.

**What will be measured, from one run of `scripts/trade_calibration.py`, all of
it reported:**

- The same 2×2 as R1 (the R1 headline against the per-man number; T30 against
  the rest of the season), now with **both** the prediction and the hindsight
  yardstick priced under R2, plus a third row pair: the R2 prediction against
  the **old** yardstick, so that the two effects — fixing the prediction and
  fixing the yardstick — are separable.
- **Primary cell, named now: R2 prediction × R2 yardstick, T30, deal-level
  "picked the side that did better" (n of 55)**, with the coin's 95% interval
  beside it.
- **Uneven sides' mean error** under R2/R2, under R2/old, and the R1 figure
  (+0.389 on T30) for reference. This is the number the job exists for.
- **The even-count sides must be identical to R1's** — no place opens on them —
  stated as a check and reported.
- The player-level diagnostic unchanged (Spearman about +0.39) as a regression
  check.

**What will not be done after the run:** no change to `OPENED_PLACE`, the decay
rule, the window, the sample or the bar. Seasons, exclusions and
`review_days=1` exactly as before.

---

## 8. Decisions a reader could reasonably have made differently

- **One day of review, not two or zero.** The ledger says the deal seats the
  day after the players' last day on the old roster, and the accept and the
  uphold are stamped together. A league that actually votes would want two,
  and a manager with a handshake wants zero; both are a flag
  (`--review-days`), and the report prints which was used.
- **The bar is the rest-of-season paid bar, 0.20.** A trade costs no FAAB, so
  an argument could be made for the free bar of 0.10. It costs players, which
  is scarcer than money in season, so it is labelled against the paid one.
  Whichever it is, it must be a bar the other reports already use, or a trade
  and a claim cannot be compared on a page.
- **The season term is judged on the roster, not on the league's average
  team.** The first cut took the other one, and the argument for it is still
  good: `judge.py`'s lens is comparable across teams, fit is priced in
  `delta_week`, and a trade and a pickup are then charged by the same rule.
  Revision R1 (§7a) took the with-and-without instead, because expected wins
  saturates and a per-man sum cannot see it. Both numbers are on the payload;
  §7 is what happened when they were measured against each other, which is
  that neither of them predicts the outcome and the new one is worse on the
  very shape it was written for. A reader who wanted the old one back would
  not be arguing with the evidence.
- **A place a 2-for-1 opens is filled by the best man on the wire**, by name,
  in the category table and in the headline, rather than left empty. Leaving
  it empty would show a consolidating deal losing in every category while the
  net said it was fine. Filling it puts a man in the table who is not in the
  deal, and it used to credit that place a good deal more generously than the
  hindsight grade's flat replacement level did. Revision R2 (§7b) closed that
  gap from the other end: both engines now price the place at what a streamed
  place returns, floored rather than flattened, so the better of the man and
  the lane is what either of them charges. They are the same price and still
  not the same arithmetic — the forecast uses the man's whole weekly line and
  the grade a scalar — and §7 measures what is left.
- **An opened place is priced at the median of the lane distribution, 0.38,
  not the lower quartile.** `docs/streaming_lane.md`'s own recommendation was
  the conservative 0.23, because the lane's upside depends on manager
  attention no code models. The median was taken because the number is being
  used as a *floor* under the best free agent rather than as a forecast of
  what this manager will do, and because the same document shows an ordinary
  held place returning 0.43 — so 0.38 keeps the ordering that a held man is
  worth more than a streamed place, and 0.23 would have re-created a smaller
  version of the same under-pricing. The quartile is on record as
  `OPENED_PLACE_LOWER` and a reader who wanted it would not be arguing with
  the evidence.
- **The default drop is the cheapest place, not the best fit.** A fit-aware
  drop would need the optimizer; the cheapest place is the same ordering
  `app.pickups.season` already ranks drop candidates by, and the report says a
  manager may prefer another.
- **The active roster, not the whole roster, is the "ordinary week".** Men on
  injured reserve post nothing, so they are out of the category table, out of
  `expected_per_week` and out of the drop candidates. `app.pickups.stream`
  passes the whole roster to `load_spots`; this differs from it deliberately,
  and it is the one place the two modules do not agree.
- **The calibration compares the season term, not the net.** The net includes
  this week's head-to-head, which the hindsight grade has no counterpart for.
  Comparing nets would mix two quantities.
- **The short horizon keeps whole matchup periods.** Thirty days is four or
  five of them, and a period is graded against the opponent's own totals, so
  half a matchup has nothing to be graded against. `grade_move(within=...)`
  therefore keeps every period that *begins* inside the window, which means
  the last one can end a few days outside it. Cutting at the day instead would
  need a new comparison rather than a new window.
- **The page leads with the fit and prints the record under the number.**
  §0 decided it and §10 is how it was drawn. A reader who wanted the headline
  at the top would be arguing with §7 rather than with the layout.
- **The trade routes answer a season with nothing to judge from rather than
  refusing it**, which is the one place they differ from the two pickup
  routes' 409 (§10).

---

## 9. What is not built

- **No suggestion engine.** The evaluator prices a deal you name; it does not
  search the league for deals worth proposing. That is the obvious next thing
  and it is also the thing §7 says to be most careful about: a search over
  every pair of rosters, ranked by a number that picks the right side of a
  deal 45% of the time, would generate confident nonsense at volume. §7's
  player-level result is the one encouraging thing here — a search over
  *players* is on firmer ground than a search over deals.
- **No FAAB leg.** ESPN's ledger carries `ACQUISITION_BUDGET_TRADE` items (the
  2026 day-85 trade has one), and the evaluator ignores money changing hands.
- **No keeper value.** 2027 has keepers; a man traded in March is worth
  something next October, and nothing here prices that.
- **Nothing is measured against 2027**, because 2027 has not been drafted. The
  evaluator answers for a season with no games — the league standard falls back
  to the newest earlier season that posted anything, and the report carries a
  note saying so — but the CLI needs a stored NBA schedule, and
  `pro_team_games` has no 2027 rows yet.

---

## 10. The page

`/l/{league_id}/{season}/team/{team_id}/trades`, under the site's shell and
in the light house style, after Moves in the My team menu (docs/site.md). The
same gate as the other team pages: this team's verified manager, and entitled
(`require_team_plan_page`). Plain HTML, CSS and JavaScript, no build step,
read per request, like every other page.

**It is a fit tool, not a winner-picker,** which is §0's decision drawn. The
page leads with what the deal does to each roster's nine categories and to
each side's needs, shows the headline second and smaller, and prints the
record in plain words at the bottom.

Top to bottom:

- **The masthead**, in the house pattern: the team, the season, the day and
  its date, and that the rosters are that morning's and nothing after it is
  read.
- **The builder.** A team to trade with; the two rosters as lists, where a
  tap moves a man into *We give* or *We get* and a second tap takes him out;
  a "who is dropped" choice — one per place a side is short of — for whichever
  side has no room, left alone meaning *the cheapest place (chosen for you)*,
  which is what §3 already does and says it did. One button: **Judge this
  trade**. The whole deal is mirrored into the query string (`with`, `give`,
  `get`, `drop`, `theirdrop`, and `today` like every other page), so a judged
  trade is bookmarkable and a refresh lands back on it, judged.
- **The result, fit first.** Each side's nine in the fixed order, ours first,
  side by side on a wide screen and stacked on a phone: the ordinary week
  before and after, the change, and the change in the chance of winning that
  category, with the shift strip over the table. A gain and a loss are told
  apart by a sign and an arrow before they are told apart by colour, and the
  *count's* change is drawn in plain ink rather than green or red, because
  more turnovers is a bigger number and a worse week and only the probability
  beside it knows which way that cuts. Under each table, a compact **Finish**
  line — `Projected 94.6-76.4 · 3rd · playoffs 92.7% → 94.4-76.6 · 3rd ·
  92.2%`, the sampling band in brackets, "so this is inside the noise" when
  the odds moved by less than it, and the projected record's own calibration
  sentence in faint type underneath (§1a). It sits here and not beside the
  number because it is a second lens and not a second bar. Then the engine's
  own sentence, which already leads with the fit.
- **The number, second and smaller.** Per side: this week plus the change per
  week over the weeks left and the net; the projected record with and
  without; "clears the 0.20 bar" or "below" as a label and never as advice;
  the playoff lens; and how an uneven deal was settled — the named free agent
  whose week fills an opened place, or the named drop and what it cost. The
  other side is labelled *our estimate of their side, not what they think*
  wherever it appears.
- **What it rests on**, per man: what he is worth a week, the games of his
  own behind the projection and its source, his games left and his playoff
  games, and a mark on anything thin or hurt.
- **How much to trust the number**: `CALIBRATION_NOTE`, verbatim, and no link
  off the page.

**Three routes**, beside the pickup routes and named like them
(`app/api/trades.py`):

    GET .../teams/{team_id}/trades/rosters ?with_team= &today=
    GET .../teams/{team_id}/trades/pool    ?with_team= &give= &get= &drop= &their_drop=
                                           &side=ours|theirs &limit= &today=
    GET .../teams/{team_id}/trades/report  ?with_team= &give= &get= &drop= &their_drop=
                                           &fill= &their_fill= &today=

All three are the paid team layer (`require_team_manager` and
`require_entitlement`, the one check the week, season and moves routes
declare). Player ids go in and out as ESPN's, as everywhere else. The report
route answers `evaluate_trade`'s own payload (§6) and computes nothing of its
own; all three carry `calibration_note`, so the page never keeps a copy of
a record that a re-run would make stale. The pool is §11 and the card §12.

The rosters route is what the pickers are drawn from, so the page hard-codes
no roster and guesses none. It reads the roster stored on or before the day
asked for: a builder opened on a replayed day 52 offers day 52's men, and
`tests/test_api_trades.py` proves it on a fixture where a later day holds one
more.

**A season with nothing to judge from is not an error.** 2027 before its
draft has no schedule and no rosters; both routes answer 200 with
`readiness` — the same two things `app.api.pickups.readiness` looks for — and
the page says so in a sentence and draws no pickers. That is a deliberate
difference from the pickup routes, which 409: a plan with no wire is not a
plan, but a trade page has a builder to draw and a record to print before any
deal exists.

**Bad input is a 422 with a sentence a manager can act on.** Every one of
them, in `app/api/trades.py`:

- A team cannot trade with itself: pick a different team to trade with.
- Name the team on the other side of this deal.
- There is no team {id} in this league's {season} season.
- Nothing is being traded: name at least one player given or got.
- {name} is on both sides of this deal: name him once, as given or as got.
- {team} does not have {names} on its roster on day {day}.
- {name} is on {team}'s injured reserve on day {day}: he holds no active
  place, so he cannot be traded or dropped to make room in this report.
- {team} cannot drop {names}: not on its active roster on day {day}.
- {team} cannot both trade away and drop {names}.
- {team} has no room for the {n} player(s) arriving and nobody left to drop:
  every other man on its roster is already in this deal. Give it one fewer
  player, or take one back.
- {team} needs {n} more roster place(s) for this deal and can free {m}: it
  could still drop {names}. Give it one fewer player, or take one back.
- {names} is not a free agent on day {day}: only a man on the wire that
  morning can fill the place this deal opens.
- {team} opens no roster place in this deal, so there is nowhere for {names}
  to go. Take a player back from it, or leave the wire alone.
- {name} cannot fill a place on both sides of this deal: there is one wire,
  and one of him. Name him for one side or the other.
- {team} opens {n} roster place(s) and {m} men are named off the wire for it:
  name one man for each place, or fewer.

The last four are the fill's, and the first of them is the one a manager will
actually meet: a reconstructed wire cannot see a free agent who did not play
that day (§7, "What a played season costs the measurement"), so a man who is
on the real wire and hurt is refused by name on a replayed season.

The two before them are guards rather than a common path. With the equal roster
sizes ESPN gives every team there is always somebody to drop, and the report
drops him rather than refusing; only a roster holding more men than the other
side has places can produce them, which is what the fixture builds.

**Waiting.** An evaluation is about 2.4 seconds cold in a fresh process and
1.9 warm, measured on the stored 2026 season, day 52, Turner for Queta. That
is nothing like the pickup reports' 45 seconds cold, because a trade prices a
deal that was named rather than searching the whole wire and re-running the
week for every candidate. The page still never looks dead: the button
disables itself and says what it is doing in words, the builder stays usable,
and the result replaces itself when it arrives. **The morning precompute is
not worth extending to it** — there is no deal to precompute until a manager
names one, and two seconds is not a wait worth caching for.

The pool is the same shape and the same price: **2.5 seconds cold in a fresh
process and 2.3 warm**, on the stored 2026 season, day 21, the Morant and
Markkanen for Cunningham deal, 51 free agents priced. It reads the same
rosters, the same wire and the same weekly lines the report does (`_context`,
shared by the two so they cannot answer from two readings of one day), and
skips only the playoff lines, which the pool has no use for. Nearly all of
both numbers is one query: the league's measured category spreads
(`category_distributions`) take about two seconds and are not cached. That is
also why the card in §12 does not carry what a man is worth a week.

---

## 11. Filling the opened place

The settlement in §3 answers a manager who has not decided what to do with
the place his deal empties. Most of the time he has decided. Patrick's real
deal of 11 November 2025 is the case: Ja Morant and Lauri Markkanen to Ben's
Need Some VC for Cade Cunningham, and Brandon Miller — dropped by another
team three days before, hurt, and claimed by Through The Wire the following
morning — going straight into the place it opened. The deal he was weighing
was two men for one **and Miller**, and until now the page could not be asked
that question.

**The rule.** `evaluate_trade(..., fills={team_id: (player_id, ...)})` names
one free agent per place a side opens, and that man is then treated as
exactly what he is: a player arriving. His weekly line goes into the
after-roster the nine categories are drawn from, into the season term's
with-and-without, into `places_cost`, and into `delta_week` — which an opened
place never was, because nobody knows who would be in it day by day and a man
the manager has named is on the roster from the day the deal lands. Only the
places nobody is named for keep the §3 settlement, at `OPENED_PLACE` or the
wire's best man, whichever is better.

Naming the man the report would have stood there anyway changes nothing in
the season term, which is the check that this is one rule read two ways
rather than two rules (`tests/test_trades.py`). Naming a lesser man — the one
who is hurt today and plays the rest of the season, or the one whose
categories this roster is short of — moves every number the right way.

**The pool is the other half.** `fill_pool` answers with the day's wire, each
man priced by **what he would be worth to this roster after this deal**: the
expected category wins a week of the side's post-trade roster with him in the
opened place, less the same roster with the place left open. That is the
nine-category table's own arithmetic, so it ranks the wire differently from
the league standard, and the difference is the whole reason the choice
belongs to the manager: a roster that has given up on free-throw percentage
should take the big who cannot shoot them over the guard who can, and the
league lens — a man dropped into an average team — says the opposite. Both
numbers are on every candidate, `worth` and `value`, because a ranking a
reader cannot argue with is worse than one he can.

The deal is a parameter of the pool for the same reason: who is leaving
decides what the roster is short of. The wire is read exactly as the report
reads it, `historical_wire` and all, so a man the pool offered cannot be a man
the report refuses.

**What it does not do.** It does not pick. "Leave it open" is the first row
and the default, it says what the report values it at, and no row says take
him. And on a season the listener never ran for, the pool is the
reconstructed wire — the men who played that day and were in nobody's lineup
— so the man a manager actually claimed, if he was hurt and did not play, is
not in it. The refusal names him (§10) and the page says which wire it is
looking at. That is the same handicap §7 measures the calibration under, and
the one thing a listened season would fix on its own.

---

## 12. The card

Every name on the trade page opens a card: hover on a desktop, tap on a
phone, where it comes up as a sheet along the bottom. It carries his line in
the nine **per game**, the games he has left over the stretch the report
plans for and the games he has in the playoff weeks, his injury status and
expected return when the listener has them, how many games of his own stand
behind the projection and what stood behind the rest of it, and his position
and NBA team.

It is `app/inseason/card.py` and `GET .../players/{player_id}/card?today=`,
and it is deliberately not the trade page's. The week, the season and the
moves pages all print names and all want the same card, so it is built once,
drawn once (`pages.css`, `shell.js`: `cardName`, `wireCards`), and hangs off
the league and the season rather than off a team — the games are counted over
one season's calendar, and it is a league member's to read, like the pages it
is opened from. Wiring the other three pages to it is a follow-up; nothing
here makes that harder.

**It says what the page it was opened from says.** `per_game` is
`per_game_line`, the rate every weekly line is scaled from, and the games and
the provenance are the report's own `PlayerCard` fields. A card that
disagreed with the table it was opened from would be worse than no card, and
the test holds the two to each other.

**What it does not carry, and why.** What a man is worth a week — the
league-standard number the reports lead with — is not on it. That needs the
league's measured category spreads, about two seconds of query, and a card is
a hover: with them it answered in 2.2 seconds and without them in about
fifty milliseconds. Every page that shows that number shows it in its own
table, beside the name the card hangs off, so nothing is hidden by leaving it
off.

**Two triggers, because a roster row is already a button.** A row in the
builder puts a man into the deal when it is tapped, so it cannot also open
his card: it carries `data-card-hover` (hover and focus, which a phone never
fires) and a small "card" control of its own beside it carries `data-card`.
Every other name is a `data-card` button: reachable from the keyboard,
dismissed by Escape with the focus put back where it was, by a tap outside,
or by scrolling away.


---

## As built, 2026-09-22: the record is per league, and it writes itself

`PUBLISHED` and `CALIBRATION_NOTE` were this league's run, typed into the
module, and the trade page printed the note verbatim for whatever league was
being looked at. They are now this league's `trade_record` row
(docs/intake.md) and the fallback for a league with no trades of its own to
replay.

The sentence is written by `app.trades.calibration.trade_note`, a function of
a run's own figures: how many deals could be replayed, how many the evaluator
called right, the exact binomial interval a fair coin gives over that many
tosses, and what a consolidating deal is mis-priced by. Applied to the
published run it returns `CALIBRATION_NOTE` character for character, which is
what `tests/test_trades.py` holds it to, so this league's page cannot move.

Two things it does that the constant could not. The verdict against the coin
is **read off the interval** rather than asserted, so a league where the
evaluator lands outside the range is told so in either direction. And the
"one thing did get better" clause only appears where there is an earlier
revision to compare against -- ours has one (R1's 0.389 against R2's 0.103),
and a league measured once has no before, so its note says only where it
stands.

`rows_for_season` takes a `league_id`, for the same reason
`streaming_lane.py` does. `intake_trades` runs the whole thing in about 58
seconds over eight seasons here.
