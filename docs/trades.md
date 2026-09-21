# Trades, looked at forward: one currency, both sides, and an honest calibration

**League:** Full Court Press (ESPN 3853870), nine-category H2H, daily lineups, 16 teams in 2027
**Written:** 2026-09-21
**Status:** the engine (`app/trades/`), the CLI (`scripts/trade.py`) and the calibration (`scripts/trade_calibration.py`) are built. No API route and no page yet; §6 is the payload they will sit on.
**Companions:** [`pickups.md`](pickups.md) (the currency and the recommender it came from), [`scoring/`](scoring) and `app/scoring/trade_grades.py` (the same trades, graded in hindsight), [`inseason_rehearsal.md`](inseason_rehearsal.md) (the look-ahead lesson §5 is built on)

---

## 0. The answer, up front

**The evaluator is no better than a coin flip at picking which side of a
trade did better.** On the 55 deals this database can both evaluate forward
and grade in hindsight, it picked the side that came out ahead 27 times — 49%
— and the rank correlation between what it predicted and what was delivered is
+0.05. Section 7 has the numbers, what they are and are not evidence of, and
what I think is wrong. Nothing here was tuned to improve them.

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

Read it as a calculator for a conversation, not as an oracle. That is what
`docs/pickups.md` calls a tool and not gospel, and a 49% hit rate on the
outcome is the strongest possible argument for the language being careful.

---

## 1. What it judges, and in what

Module `app/trades/`, two files: `evaluate.py` (the engine) and `summary.py`
(the sentence under it). Everything is a function of loaded state, and the
whole of `app/pickups/judge.py` is reused rather than re-derived.

```
evaluate_trade(session, league_season, today, side_a, side_b, *, drops=...) -> TradeReport
```

A `TeamOffer` is a team (ESPN id) and the player ids it gives away. For each
side the report carries a `SideReport` with:

| | what |
|---|---|
| `judgement` | `app.pickups.judge.Judgement`: this week's delta, the rest-of-season delta per week, the weeks left, the net, and the projected end-of-season record with and without |
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

**`delta_season_per_week`** is `app.pickups.judge.judge`, which charges the
roster places the deal touches through the league-standard lens. §3 is about
the one thing that had to be generalised to get there.

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
the two formulas are identical by construction.

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
    categories[9]:                         CategoryView
      abbreviation, before, after, delta, p_before, p_after, p_delta, moved
    playoffs:                              PlayoffLens
      first_scoring_period, last_scoring_period, weeks, games,
      delta_per_week, delta_total, categories[], note, measurable
    expected_per_week, hurdle, clears
    summary, notes[]
```

Three things a page should draw and not hide:

1. **The nine, as a two-column diff with a probability bar.** The counts are
   the change; the probability is whether the change matters. A manager
   punting FT% should be able to see at a glance that the FT% he is giving
   away costs him nothing — that is `p_delta` near zero beside a large
   `delta`, and the summary already says so in words.
2. **`review_source`, `historical_wire` and every `note`.** They are the
   difference between a number and a number you can argue with.
3. **`thin` and `hurt` on every `PlayerCard`.** A trade evaluation that hides
   a 12-game sample is worse than none (`THIN_GAMES = 12`).

The report is bounded, so the route should return an object rather than a
`Page`, like both pickup routes. It makes no ESPN request. A season with no
schedule, roster or wire should be a 409 for the same reason
`/pickups/stream` is.

---

## 7. The calibration: it does not predict the outcome

**110 trade sides -- 55 deals -- across 6 seasons is the whole sample, and every figure below rests on it.** Sign agreement with the hindsight grade is **46%** over the sides; the rank correlation is **+0.05** and the ordinary correlation -0.04. Mean error (predicted minus delivered) is +0.006 categories a week, mean absolute error 0.253. Generated by `scripts/trade_calibration.py` in 64s, review_days=1.

The two sides of a deal are not two observations: the prediction is very nearly antisymmetric between them, so the independent question is **which side of each deal the evaluator picked**. It picked the side that did better in **27 of 55** deals (49%), against 50% for a coin.

For scale: the predictions have a spread of 0.226 categories a week and what was delivered a spread of 0.252, so the mean absolute error above is about the size of the thing being predicted.

| | n | sign agreement | Spearman | Pearson | mean error | mean abs error |
|---|---|---|---|---|---|---|
| all sides | 110 | 46% | +0.05 | -0.04 | +0.006 | 0.253 |
| even counts | 85 | 46% | +0.09 | -0.00 | -0.071 | 0.217 |
| uneven counts | 25 | 48% | -0.19 | -0.09 | +0.265 | 0.376 |
| nobody broke down after | 85 | 55% | +0.32 | +0.12 | +0.029 | 0.212 |

Against the decision lens (`MoveGrade.decision`, the same day's data through the hindsight engine's own arithmetic): Spearman +0.44, mean error +0.003.

### By season

| season | n | sign agreement | Spearman | mean predicted | mean delivered |
|---|---|---|---|---|---|
| 2019 | 10 | 60% | +0.50 | +0.000 | -0.035 |
| 2021 | 6 | 17% | -0.66 | +0.000 | +0.039 |
| 2023 | 6 | 0% | -0.93 | +0.000 | -0.042 |
| 2024 | 26 | 73% | +0.45 | +0.000 | -0.030 |
| 2025 | 28 | 39% | +0.20 | +0.000 | +0.018 |
| 2026 | 34 | 41% | -0.08 | -0.005 | -0.004 |

### The worst misses, and why

- **2021 day 32, Moneyballers  £££££££: in Anthony Davis, LaMelo Ball; out Jamal Murray, Clint Capela, Tyler Herro** -- predicted +0.302 a week, delivered -0.774 over 14 period(s). Anthony Davis played under half the games scheduled after the deal: an injury nobody had on the day, which is luck and not a bug.
- **2021 day 32, East End Doncic and Dirk: in Jamal Murray, Clint Capela, Tyler Herro; out Anthony Davis, LaMelo Ball, Bismack Biyombo** -- predicted -0.302 a week, delivered +0.759 over 14 period(s). Anthony Davis left and then broke down, so the side that gave him up is credited by hindsight for a risk it did not take.
- **2024 day 72, The G Smoothies: in Bradley Beal, Ja Morant; out Joel Embiid, Dario Saric** -- predicted -0.793 a week, delivered +0.076 over 9 period(s). Joel Embiid left and then broke down, so the side that gave him up is credited by hindsight for a risk it did not take.
- **2026 day 72, Brighton Bears: in Nikola Jokic; out Deni Avdija, Onyeka Okongwu** -- predicted +0.594 a week, delivered -0.247 over 9 period(s). an uneven deal: the evaluator charges the place it costs at what that place was worth and the hindsight grade charges a flat replacement, so part of this gap is the two settlements rather than the forecast.
- **2023 day 84, Tom's Team: in Joel Embiid; out Kyle Kuzma, Damian Lillard** -- predicted +0.323 a week, delivered -0.509 over 6 period(s). an uneven deal: the evaluator charges the place it costs at what that place was worth and the hindsight grade charges a flat replacement, so part of this gap is the two settlements rather than the forecast.
- **2026 day 72, Optimize the MVPs: in Deni Avdija, Onyeka Okongwu; out Nikola Jokic, Ryan Nembhard** -- predicted -0.594 a week, delivered +0.225 over 9 period(s). the projections held up; the gap is fit -- the season term values a man against the league's average team, and the grade counts what he did in this one's lineup.
- **2024 day 72, Brighton Bears: in Joel Embiid; out Bradley Beal, Ja Morant** -- predicted +0.793 a week, delivered +0.035 over 5 period(s). the line for Bradley Beal, Ja Morant rested on fewer than a dozen games of his own, which the report flags and this run does not discount.
- **2021 day 84, Thibs Dust: in Myles Turner; out Kevin Durant** -- predicted -0.488 a week, delivered +0.189 over 6 period(s). Kevin Durant left and then broke down, so the side that gave him up is credited by hindsight for a risk it did not take.

### What could not be scored

- 39 sides where only one half of the deal left a roster trace.
- 3 sides of trades with more than one counterparty.
- 5 events where the two teams' reconstructions did not mirror.
- 0 sides with no gradeable stretch after the move.

### What I think is wrong, and what I do not

Four things, in the order I would look at them.

**1. The target may be mostly noise.** `MoveGrade.result` is what a team
actually posted with the incoming players against the same team with them
removed and the outgoing ones put back, averaged over up to fourteen matchup
periods. Over that window a player is traded again, dropped, injured, or has
his role changed. The delivered figures have a spread of 0.25 categories a
week and the predictions a spread of 0.23, so the mean absolute error is
about the size of the whole quantity: there is not much signal above the
noise floor to find. I do not think this excuses the result. I think it means
a larger sample would not obviously rescue it either, and that the honest
target for a forward trade tool may be the next month rather than the rest of
the year.

**2. Injuries do most of the work, and nobody can see them coming.** Splitting
the sample on whether anybody in the deal played under half his team's
scheduled games afterwards moves sign agreement from 46% to 55% and the rank
correlation from +0.05 to +0.32. That is a real effect and it is **not** a
result I can claim: the split is made on the outcome, so it is not something
the evaluator could do on the morning. It is why the worst misses in the
table above are Anthony Davis in 2021, Joel Embiid in 2024 and Kristaps
Porzingis in 2026 — three deals where a star changed hands and then missed
half the season. What it does say is where the remaining honest work is:
availability, not production.

**3. The evaluator over-rates consolidation.** Among the sides that gave two
men for one — the shape where `places_cost` credits the opened place at the
wire — the mean error is +0.27 categories a week, against −0.07 on even
counts. In a nine-category league two starters routinely beat one superstar,
because expected wins is a sum of *saturating* probabilities and a star's
marginal contribution flattens out, while `places_cost` values each man
independently inside the league-average team and then values the emptied place
at the typical-pickup floor of 0.06. Both halves of that push the same way.
This is the one finding that looks like a model defect rather than the world,
and it is on 25 sides across about a dozen deals, which is a hint and not a
measurement. **It has not been changed**, because changing it after seeing
this table is exactly the tuning the brief forbids.

**4. The two forward lenses agree; it is the future they both miss.** Against
`MoveGrade.decision` — the hindsight engine's own knowable-at-the-time grade,
the same day's data through different arithmetic — the rank correlation is
+0.44 and the mean error +0.003. So the evaluator is not disagreeing with the
project's other valuation of the same facts. Both of them then fail to
predict the result at about the same rate. That points at the world and the
window rather than at a bug in either.

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
- **The season term values a man against the league's average team, not
  against the roster he is joining.** That is `judge.py`'s existing lens and
  the argument for it is in its docstring: fit is priced in `delta_week`, and
  a place's worth should be comparable across teams. The optimizer's
  with-and-without over a whole roster (`app.pickups.season`) would price fit
  over the whole season too. It would also cost a full optimize per side per
  deal, and it would mean a trade and a pickup were charged differently. I
  took the cheaper and more consistent one. §7's finding 3 is the strongest
  argument for revisiting it.
- **A place a 2-for-1 opens is shown in the category table filled by the best
  man on the wire**, by name, rather than left empty. Leaving it empty would
  show a consolidating deal losing in every category while the net said it was
  fine. Filling it puts a man in the table who is not in the deal. I chose the
  one that matches what the judgement actually charges.
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
- **No API route and no page.** The brief's scope, and §6 is the handover.

---

## 9. What is not built

- No route, no schema, no page.
- **No suggestion engine.** The evaluator prices a deal you name; it does not
  search the league for deals worth proposing. That is the obvious next thing
  and it is also the thing §7 says to be most careful about: a search over
  every pair of rosters, ranked by a number that picks the right side of a
  deal 49% of the time, would generate confident nonsense at volume.
- **No FAAB leg.** ESPN's ledger carries `ACQUISITION_BUDGET_TRADE` items (the
  2026 day-85 trade has one), and the evaluator ignores money changing hands.
- **No keeper value.** 2027 has keepers; a man traded in March is worth
  something next October, and nothing here prices that.
- **Nothing is measured against 2027**, because 2027 has not been drafted. The
  evaluator answers for a season with no games — the league standard falls back
  to the newest earlier season that posted anything, and the report carries a
  note saying so — but the CLI needs a stored NBA schedule, and
  `pro_team_games` has no 2027 rows yet.
