# What a chance means now

**League:** Full Court Press (ESPN 3853870), nine-category H2H
**Decided:** 2026-09-23, by Patrick, on the evidence published the day before
**Applied in:** `app.pickups.stream.SPREAD_SCALE` = 2.0
**Companions:** [`projected_record.md`](projected_record.md) §0 (the forecast's
own record), [`trades.md`](trades.md) §0 and §7 (the trade record),
[`pickups_backtest.md`](pickups_backtest.md) §0 (the 2026 replay),
[`pickups.md`](pickups.md) §4.3

---

## What changed, in one paragraph

Everywhere this product says "a 70% chance of winning rebounds this week", the
number came from one calculation: how far ahead your projected total is,
divided by how much a week's total normally wobbles. The wobble it was
dividing by was **one team's** — measured on this league's own results, which
is the right measurement of the wrong thing. A category is not decided by how
much your total moves; it is decided by how much the *gap between two totals*
moves, and two totals wobbling is more than one. So every chance on the site
was too sure of itself, in both directions, and the forecast's own record said
so plainly: things it called at 95% happened four times in five, things it
called at 5% happened one time in five. Since 2026-09-23 the spread is doubled.
Nothing else was touched. **Every chance you read is now closer to a coin, and
so is every number derived from one** — the net beside a pickup, the size of a
bid, what a trade is worth this week, the projected standings.

The factor is two rather than the 1.41 the textbook gives for two independent
teams, because the categories inside a week are not independent of each other
— a roster with four games on Sunday gains in most of them at once — and
because a roster's own form varies more week to week than the league's spread
across teams suggests. Two is the number that made the published table come
out right. Patrick chose it before any of the three re-runs below started, and
it was not moved afterwards.

---

## The three records, before and after

### 1. The projected standings: better, and this was the point

| | before | after |
|---|---|---|
| overall score (lower is better; a coin is 0.2500) | 0.2288 | **0.2179** |
| what it called at 15% happened | 30% | **15%** |
| what it called at 85% happened | 71% | **85%** |
| right side of this week's matchup | 68.4% | 68.4% |
| final record off by, made at halfway | 7.4 of 171 | **7.2 of 171** |

It still overclaims at the two ends — what it calls above 90% comes in about
80% of the time — but those bands now hold 317 calls of 47,880 rather than
3,872, because a model this wide rarely claims to be that sure. Which side it
points at barely moved, and should not have: a spread says *how sure*, not
*which*.

One honest caveat, in `projected_record.md` §0: this run is by construction
the same evidence the previous run published as its "if we widened it" row,
not a fresh test of it. It reproduces that row exactly, which is the check
that the change landed where the diagnostic had been.

### 2. Trades: not one digit moved

| | before | after |
|---|---|---|
| picked the better side, 55 deals, 30-day window | 25 of 55 | **25 of 55** |
| a coin over 55 | 20 to 35 | 20 to 35 |
| two-for-one deals, how far the number runs high | +0.103 a week | **+0.103 a week** |
| what a man is worth a week, against what he did | +0.39 over 174 men | **+0.39 over 174 men** |

Six seasons, none of them a season the factor was chosen on, and every
published figure came back identical. The reason is worth knowing rather than
taking on trust: what this record scores is what a deal is worth to an
**ordinary week** from here on, which is built from what each man is worth
against the league's spreads — not from the head-to-head. The head-to-head
half of a trade's number, the matchup in front of you, did move; it is on the
page and it is not the quantity this record is about. So this is a real
out-of-sample check that came back clean, and a narrower one than it looks.

### 3. Pickups, the 2026 replay: a shade worse on the week, a shade better on the season

At the bars Patrick set, and neither was moved:

| | before | after |
|---|---|---|
| streaming, moves named (of 602 decisions) | 536 | **531** |
| streaming, categories delivered | +0.16 a matchup | **+0.13 a matchup** |
| streaming, share that cost nothing or gained | 82.3% | **80.8%** |
| rest of season, moves named | 307 | **289** |
| rest of season, categories delivered | +1.21 over 30 days | **+1.32 over 30 days** |

The bar names about the same number of moves. What changed is *which* move it
puts first, because the ranking reads the net and every net moved. On this one
season the moves it now names delivered a little less on the week and a little
more on the season, and 530 moves cannot tell either difference from noise.
Both are published because they are what the run says.

The hurdle sweep still picks what is already shipped: no streaming setting
qualifies under the tuning rule (an empty day filled is always worth
recommending, whatever the bar), and 0.20 paid / 0.10 free is still the best
rest-of-season pair. **Nothing here asks for a hurdle to move.**

---

## What the 0.20 bar now catches

The bar is unchanged at 0.20 categories, and it catches the same moves. Of the
five the search ranks at each decision, **3.99 cleared it per team-week before
and 4.02 after**. A decision where none of the five clears is 7.3% of them
before and 6.8% after. The tool has not gone quiet.

What is smaller is every number beside those moves:

| a ranked move's net | before | after |
|---|---|---|
| mean | +0.287 | **+0.221** |
| median | +0.172 | **+0.107** |
| ninetieth percentile | +0.434 | **+0.289** |

So a move that used to read "+0.43, well clear of the bar" now reads "+0.29,
clear of the bar". The bar bites the same, and the margin over it is about a
third narrower. The reason the count did not fall with the size is that most
of a net is the season half, which did not change; the week half is what
shrank, and it was as often negative as positive.

The week itself compresses toward a coin, which is the visible change on the
page:

| a week's expected categories | before | after |
|---|---|---|
| average | 4.41 | 4.41 |
| how far it usually sits from 4.5 | 0.88 | **0.61** |
| spread | 1.16 | **0.91** |
| tenth to ninetieth percentile | 3.12 – 5.73 | **3.60 – 5.32** |

The average cannot move and is not a finding: both sides of every matchup are
in the sample, and two opposite chances always add to one. It is there as a
check that the sample is whole.

---

## What was deliberately not changed

- **No hurdle.** `STREAM_HURDLE` (0.20), `SEASON_HURDLE_PAID` (0.20),
  `SEASON_HURDLE_FREE` (0.10). They are the owner's and only he moves them;
  the three re-runs exist to tell him what they now catch.
- **Nothing in the bid model**, and neither `OPENED_PLACE` nor
  `TYPICAL_PICKUP`.
- **The draft.** `app.draft.optimizer` and `app.draft.targets` use the same
  measured spreads for a different question — a whole season against the
  field, not one week against one known opponent — and whether they want the
  same factor is a separate measurement nobody has made.
- **The independence assumption** in the standings simulation: the nine
  categories are still drawn independently there. Part of what the factor of
  two buys over 1.41 is standing in for that, which is worth remembering if
  anyone ever fixes it properly.

## Reproducing it

```
python scripts/projected_calibration.py --season 2026 --sims 2000   # the standings
python scripts/projected_calibration.py --season 2026 --sigma-scale 0.5  # the old model
python scripts/trade_calibration.py                                  # the 55 deals
python scripts/pickups_backtest.py                                   # the 2026 replay
python scripts/spread_census.py --out docs/runs/...json              # the page's own numbers
```

`--sigma-scale` multiplies on top of the shipped factor, so a plain run is the
product as it ships and 0.5 is the model before this revision. Every run's full
output is in `docs/runs/`, dated. Wall times: 37s, 73s, 6532s, 3611s.
