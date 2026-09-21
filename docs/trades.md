# Trades, looked at forward: one currency, both sides, and an honest calibration

**League:** Full Court Press (ESPN 3853870), nine-category H2H, daily lineups, 16 teams in 2027
**Written:** 2026-09-21, revised the same day after the run declared in §7a
**Status:** the engine (`app/trades/`), the CLI (`scripts/trade.py`), the calibration (`scripts/trade_calibration.py`) and the published record (`app/trades/calibration.py`) are built. No API route and no page yet; §6 is the payload they will sit on.
**Companions:** [`pickups.md`](pickups.md) (the currency and the recommender it came from), [`scoring/`](scoring) and `app/scoring/trade_grades.py` (the same trades, graded in hindsight), [`inseason_rehearsal.md`](inseason_rehearsal.md) (the look-ahead lesson §5 is built on)

---

## 0. The answer, up front

**The evaluator is no better than a coin flip at picking which side of a trade
did better, and the one revision we were allowed did not change that.** On the
55 deals this database can both evaluate forward and grade in hindsight, the
revised headline picked the side that came out ahead 25 times on the window we
declared as primary — 45%, where a fair coin gives between 20 and 35 of 55
nineteen times in twenty. Yesterday's number was 27 of 55. All four cells of
the table we said we would print — two headline numbers by two horizons — land
inside that coin's interval, so none of the differences between them is a
finding either. Section 7 has all of it, including the fact that the revision
made the consolidation error it was written for **worse**, not better.

One thing did come out of the run that was not there yesterday. **The
evaluator is decent at players and hopeless at deals.** What it says a man is
worth a week ranks at +0.39 against what his real box scores were worth over
the next month, across 174 men in these deals; subtract one side of a trade
from the other and that becomes +0.01. The forecasts are not noise. The
difference of two of them, on two rosters, is.

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
| `places_opened` / `places_used` / `replacement` | how a 2-for-1 was settled, and at what |
| `summary` | the plain-English line, generated from the numbers |
| `notes` | the honest caveats for this side |

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
             + wire, once for each place vacated and not refilled

A place a move leaves open is worth the wire, because it will be streamed. A
place a move fills that was empty was worth nothing, so the arrival is
credited in full. Both fall out of the two sums with no special case, which is
why a free add, an injured-reserve move, a one-for-one stream and a
three-for-two trade are now one line of arithmetic.

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

## 6. The payload, for the page that comes next

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
    receives[] / gives[] / drops[]:        PlayerCard
      player_id, name, value, games_left, playoff_games,
      injury_status, expected_return_date,
      games_so_far, had_projection, projection_source, thin, hurt
    drop_source, places_opened, places_used
    replacement, replacement_player
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

The report is bounded, so the route should return an object rather than a
`Page`, like both pickup routes. It makes no ESPN request. A season with no
schedule, roster or wire should be a 409 for the same reason
`/pickups/stream` is.

---

## 7. The calibration: it does not predict the outcome, and the revision did not fix it

Read §7a first. One revision was declared before this run, the primary cell
was named before it, and nothing below was tuned after it. The numbers in this
section are the re-run of 2026-09-21; the run they replace is kept at the
bottom as the "before".

### The answer

**Primary cell, as declared — the revised headline (R1) against the thirty
days after the deal, at deal level: the evaluator picked the side that did
better in 25 of 55 deals, 45%. A fair coin gives between 20 and 35 of 55
nineteen times in twenty.** Yesterday's headline on yesterday's horizon was 27
of 55, 49%. Every one of the four cells we said we would print lands inside
that coin's interval, which is the first thing to say about all of them: none
of the differences between the cells is a finding, including the ones that
flatter the revision.

Two things the run does say, neither of them about the headline:

- **The revision made the consolidation error worse.** The uneven sides — two
  men for one, the shape R1 was written for — had a mean error of +0.265
  categories a week under the old per-man number. Under R1 it is **+0.389**
  against the thirty days and +0.355 against the rest of the season. §7 finding
  3 of the previous run called the old number's treatment of consolidation
  "the one finding that looks like a model defect"; the fix, declared in
  advance and honoured, pushed the number the same way and further.
- **The evaluator is decent at players and hopeless at deals.** Over the 174
  men in these deals, what it said each was worth a week ranks at **+0.39**
  against what his real box scores were worth over the same thirty days,
  through the same lens, with a mean error of +0.028 on a delivered spread of
  0.255. Subtract one side of a deal from the other and the rank correlation
  falls to +0.01. That was the diagnostic's whole purpose, declared before the
  run, and it answers the question cleanly: the projections are not the
  problem.

### The run, in full

**The primary cell, named before the run: R1 (the roster) x next 30 days, deal level. The evaluator picked the side that did better in 25 of 55 deals (45%), where a coin gives 20-35 of 55 (36%-64%) nineteen times in twenty.** Over the 110 sides the rank correlation is +0.01 and the mean error +0.073 categories a week. 110 sides across 6 seasons is the whole sample and every figure below rests on it. Generated by `scripts/trade_calibration.py` in 69s, review_days=1, short window 30 days.

For scale: the predictions have a spread of 0.293 categories a week and what was delivered a spread of 0.295, so the mean absolute error of 0.328 is about the size of the thing being predicted.

### The 2x2, all four cells from one run

| headline | horizon | deals | picked the better side | sides | Spearman | mean error | mean abs error |
|---|---|---|---|---|---|---|---|
| **R1 (the roster)** | **next 30 days** | 55 | 25 of 55 (45%) | 110 | +0.01 | +0.073 | 0.328 |
| R1 (the roster) | rest of season | 55 | 30 of 55 (55%) | 110 | +0.09 | +0.079 | 0.287 |
| per man (the old one) | next 30 days | 55 | 20 of 55 (36%) | 110 | -0.12 | +0.000 | 0.312 |
| per man (the old one) | rest of season | 55 | 27 of 55 (49%) | 110 | +0.05 | +0.006 | 0.253 |

The two sides of a deal are not two observations -- the prediction is very nearly antisymmetric between them -- so the deal-level column is the independent question and the side-level columns describe the same data twice. For 55 deals, a coin gives 20-35 of 55 (36%-64%) nineteen times in twenty.

### Splits, on the primary cell (R1 (the roster) x next 30 days)

| | sides | sign agreement | Spearman | mean error | mean abs error |
|---|---|---|---|---|---|
| all sides | 110 | 52% | +0.01 | +0.073 | 0.328 |
| even counts | 85 | 54% | +0.10 | -0.020 | 0.279 |
| uneven counts | 25 | 44% | -0.36 | +0.389 | 0.495 |
| nobody broke down after | 85 | 54% | +0.09 | +0.095 | 0.310 |

The uneven sides are the ones R1 was written for. Under R1 their mean error is +0.389 over 25 sides against the next 30 days; +0.355 over 25 sides against the rest of season. The per-man headline gave +0.265 over 25 sides against the rest of the season on 2026-09-21, which is the number R1 was meant to fix.

### How sure it was, decided on the prediction

| deals ranked by |predicted edge| | n | picked the better side | a coin |
|---|---|---|---|
| top third (edge at or above 0.40 a week) | 18 | 8 of 18 (44%) | a coin gives 5-13 of 18 (28%-72%) nineteen times in twenty |
| the other two thirds | 37 | 17 of 37 (46%) | a coin gives 13-24 of 37 (35%-65%) nineteen times in twenty |

### The players themselves

For every man in a scored deal (174 of them, counted once per deal), what the evaluator said his roster place was worth on the morning against what his real box scores were worth per week over the same thirty days, through the same league-standard lens: Spearman **+0.39**, Pearson +0.35, mean error +0.028 categories a week, mean absolute error 0.213. Predicted spread 0.248, delivered spread 0.255.

Against the decision lens (`MoveGrade.decision`, the same day's data through the hindsight engine's own arithmetic): Spearman +0.49 for the R1 headline and +0.44 for the per-man one.

### By season, on the primary cell (R1 (the roster) x next 30 days)

| season | sides | sign agreement | Spearman | mean predicted | mean delivered |
|---|---|---|---|---|---|
| 2019 | 10 | 50% | +0.21 | +0.176 | -0.021 |
| 2021 | 6 | 17% | -0.49 | +0.118 | -0.024 |
| 2023 | 6 | 50% | +0.03 | +0.253 | -0.014 |
| 2024 | 26 | 69% | +0.34 | +0.082 | -0.022 |
| 2025 | 28 | 57% | +0.01 | +0.030 | +0.017 |
| 2026 | 34 | 41% | -0.13 | +0.026 | +0.011 |

### The worst misses, and why

- **2021 day 84, Thibs Dust: in Myles Turner; out Kevin Durant** -- predicted -0.994 a week, delivered +0.227 over 5 period(s). Kevin Durant left and then broke down, so the side that gave him up is credited by hindsight for a risk it did not take.
- **2021 day 84, Embiids Burner Account: in Kevin Durant; out Myles Turner** -- predicted +0.963 a week, delivered -0.164 over 5 period(s). Kevin Durant played under half the games scheduled after the deal: an injury nobody had on the day, which is luck and not a bug.
- **2026 day 72, Brighton Bears: in Nikola Jokic; out Deni Avdija, Onyeka Okongwu** -- predicted +0.455 a week, delivered -0.636 over 5 period(s). an uneven deal: the evaluator fills the place it opens with the best man on the wire and the hindsight grade charges a flat replacement level, so part of this gap is the two settlements rather than the forecast.
- **2026 day 72, Optimize the MVPs: in Deni Avdija, Onyeka Okongwu; out Nikola Jokic, Ryan Nembhard** -- predicted -0.339 a week, delivered +0.743 over 5 period(s). the projections held up; the gap is what the men did afterwards -- the season term is this roster's week with the deal and without it, and the grade counts what the lineup really posted.
- **2023 day 13, Foxes ShutUpNDribble: in Joel Embiid; out Jaylen Brown, Deandre Ayton, Josh Giddey** -- predicted +0.480 a week, delivered -0.586 over 6 period(s). the line for Joel Embiid, Jaylen Brown, Deandre Ayton, Josh Giddey rested on fewer than a dozen games of his own, which the report flags and this run does not discount.
- **2024 day 72, Brighton Bears: in Joel Embiid; out Bradley Beal, Ja Morant** -- predicted +1.013 a week, delivered +0.035 over 5 period(s). the line for Bradley Beal, Ja Morant rested on fewer than a dozen games of his own, which the report flags and this run does not discount.
- **2023 day 84, Tom's Team: in Joel Embiid; out Kyle Kuzma, Damian Lillard** -- predicted +0.411 a week, delivered -0.564 over 5 period(s). an uneven deal: the evaluator fills the place it opens with the best man on the wire and the hindsight grade charges a flat replacement level, so part of this gap is the two settlements rather than the forecast.
- **2021 day 32, Moneyballers  £££££££: in Anthony Davis, LaMelo Ball; out Jamal Murray, Clint Capela, Tyler Herro** -- predicted +0.333 a week, delivered -0.544 over 5 period(s). Anthony Davis played under half the games scheduled after the deal: an injury nobody had on the day, which is luck and not a bug.

### What could not be scored

- 39 sides where only one half of the deal left a roster trace.
- 3 sides of trades with more than one counterparty.
- 5 events where the two teams' reconstructions did not mirror.
- 0 sides with no gradeable stretch after the move.
- 0 sides with no matchup period inside the thirty days.

### Reading it

**1. R1 is ahead of the per-man number on both horizons, and that is not a
claim.** 45% against 36% on the thirty days, 55% against 49% on the rest of the
season. Five deals and nine deals, on a sample where a coin swings fifteen.
The honest statement is that the revision did not damage the headline and did
not rescue it.

**2. The thirty-day window is worse than the rest of the season, which is the
opposite of what the last run guessed.** Finding 1 of the previous write-up
said "the honest target for a forward trade tool may be the next month rather
than the rest of the year". It is not: both headlines do worse on the month
than on the whole stretch, on the hit rate and on the rank correlation. We
declared the month as primary before seeing that, so it is the number we
publish. The guess I would now make — and it is a guess, and it is not being
acted on — is that a month is four or five matchup periods and a single
period's nine category results are extremely noisy, so the shorter target is
noisier rather than cleaner; averaging fourteen periods at least averages
something.

**3. Why R1 made uneven deals worse, as far as the rows can say.** The two
engines settle an opened roster place differently, and R1 widened the gap
rather than closing it. The hindsight grade charges a flat replacement level
for the spot — the median pickup in this league, about 0.07 categories a week.
The old forward number credited the opened place at `max(best free agent, the
0.06 floor)`, one place at a time. R1 does something more generous still: it
puts the best free agent's **whole weekly line** into the after-roster, so the
gain shows up through the saturating probabilities of a real roster rather
than as a scalar. On a played season the wire is reconstructed — whoever
played and was in nobody's lineup — so "the best free agent" can be a
genuinely useful player, and the forward side of the deal is credited with him
while the grade credits the same spot with 0.07. Part of the +0.389 is that
settlement and not the forecast. **It has not been changed**, for the same
reason nothing was changed last time: the idea arrived after the number.

**4. Confidence does not help, and that claim was legitimate either way.** The
split was defined before the run, on the prediction alone: deals ranked by the
size of the predicted edge, top third against the rest. The top third (an edge
of 0.40 categories a week or more) picked the better side in 8 of 18, 44%; the
other two thirds in 17 of 37, 46%. The tool is not more right when it is more
sure. Had it fallen the other way it would have been the most useful thing in
the run — a rule for when to trust the number — and it did not.

**5. The two forward lenses still agree; it is the future they both miss.**
Against `MoveGrade.decision`, the hindsight engine's own knowable-at-the-time
grade, R1 ranks at +0.49 and the per-man number at +0.44. The evaluator is not
disagreeing with the project's other valuation of the same facts. Both then
fail to predict the result.

**6. Injuries still do most of the work.** Splitting on whether anybody in the
deal played under half his scheduled games afterwards moves sign agreement
from 52% to 54% and the rank correlation from +0.01 to +0.09 — a much weaker
version of the same effect the last run saw, and still **not** a result that
can be claimed, because the split is made on the outcome. Where the remaining
honest work is has not changed: availability, not production.

### Yesterday's run, the before

The per-man headline against the rest of the season, 2026-09-21, before any
of this: 27 of 55 deals (49%), sign agreement 46% over the sides, Spearman
+0.05, Pearson -0.04, mean error +0.006, mean absolute error 0.253. By split:
even counts 46% and +0.09 on 85 sides; uneven counts 48% and -0.19 on 25
sides, mean error **+0.265**; "nobody broke down after" 55% and +0.32 on 85
sides. Against the decision lens, +0.44.

That cell is still in the table above — bottom row — and it still reads 27 of
55, which is the check that this is the same sample scored the same way.

### Not done, and deliberately

Ideas that arrived *after* the numbers, written down here instead of being
built, because the whole point of §7a was that one revision was all we were
allowed:

- Settle an opened place the same way in both engines — charge the forward
  side a flat replacement level rather than the best free agent's line — and
  see how much of the uneven-deal error is arithmetic rather than forecast.
  This is the obvious next measurement and it is not a tuning knob, which is
  the argument for doing it next and not now.
- Report the two sides as a ranking rather than a number, since the rank is
  the only part anybody acts on and the magnitude is demonstrably noise.
- Show the player-level number on the page, which is the part that measured
  well, rather than only the deal-level difference, which did not.
- Model availability. Every run of this calibration has ended in the same
  sentence.

### What a played season costs the measurement

Everything in §7 is run without listener data, because the listener has never
run for a played season:

- **No injury statuses.** Every player is projected as fully available on the
  morning of the deal, on both sides. A man who was day-to-day when the trade
  was agreed is invisible, which is precisely the information a manager had.
- **A reconstructed wire.** `app.pickups.state.historical_free_agents` —
  whoever played that scoring period and was in nobody's lineup. It cannot see
  a free agent who did not play, and it knows nothing about waivers, so the
  `wire_replacement` the opened places are charged at is an approximation.
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
section above is pasted from the run of 2026-09-21. Read-only, about a
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
  in the category table and now in the headline too, rather than left empty.
  Leaving it empty would show a consolidating deal losing in every category
  while the net said it was fine. Filling it puts a man in the table who is
  not in the deal, and — see §7, finding 3 — it credits that place a good deal
  more generously than the hindsight grade's flat replacement level does. The
  alternative is to charge the flat level on both sides, and that is written
  down in §7 under "not done" rather than taken.
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
- **No API route and no page.** The brief's scope, and §6 is the handover.

---

## 9. What is not built

- No route, no schema, no page.
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
