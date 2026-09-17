# Predicting the end of a matchup week

**Written:** 2026-09-17, a month before the 2027 season.
**Status:** design note, nothing built. A handoff: it names the modules, the
inputs, the tests and the backtest, so an implementer needs this note, `STATUS.md`
and the code it points at.
**Companions:** [`pickups.md`](pickups.md) (the listener and the recommender it
feeds), [`scoring/SPEC.md`](scoring/SPEC.md) (the currency), [`opponent_check.md`](opponent_check.md)
(why an opponent's spread is read carefully).

## 0. The answer, up front

Predict the week from **ESPN data alone**. Basketball Monster's daily and
weekly projections would help (§6) and are not required, which matters both
because they are not live until October and because they cannot be shown to
anyone else (`projection_sources.md`).

Three inputs, all already stored:

1. **A rate per player**, from `app.scoring.knowable.knowable`: season to date
   pulled toward the preseason projection, plus 15% of the last fourteen days.
   Measured on 7,165 player-checkpoints across six seasons, that predicts the
   next four weeks better than any of its parts: error 2.85, against 3.26 for
   season to date alone, 4.36 for the projection alone and **5.32 for the last
   fourteen days alone**. Recent form belongs in the mix, not on its own.
2. **Games left**, from `pro_team_games`, which every listener pass rewrites.
3. **Availability**, from the listener's `player_status_snapshots`: a player
   ESPN says is out scores nothing until his expected return date.

The output is not a projected score. It is, for each category, the chance of
beating *this* opponent this week, and from those the chance of winning the
matchup. A number with no probability attached would be read as certainty it
does not have.

## 1. Modules

    app/week/roster.py     who plays which day, for both teams
    app/week/forecast.py   the distribution of a team's week
    app/week/matchup.py    category-by-category probabilities, and the matchup
    scripts/week.py        the CLI: this week, or any past week for a check

`app/week/` rather than inside `app/pickups/`: the recommender asks "what does
a swap change", which is this model evaluated twice, so this is the layer
underneath it. Nothing here writes to the database.

## 2. Who plays which day

For each team, for each remaining scoring period of the matchup period:

- **The roster** is the team's latest `daily_lineup_slots` day for a day
  already played, and the latest `player_status_snapshots.on_team_id` for
  today and after. A roster can change mid-week; the model is re-run, not
  patched.
- **A player has a game** when `pro_team_games` holds a row for his
  `pro_team_id` (from his latest snapshot) on that scoring period.
- **Startable games** are not the same as games: a lineup has ten slots, so on
  a heavy night some players sit. `app/inseason/startable.py` (ticket P2)
  answers how many starts can actually be filled, by maximum matching over the
  lineup's slots. The forecast counts started games, not games.

A manager who does not set his lineup is a real and unmodelled case. The
model assumes every fillable slot is filled: it predicts the week available to
a team, not the week a distracted manager will have. Say so in any output.

## 3. Availability

| ESPN status | What the week gets |
|---|---|
| active | every game |
| out, with an expected return date | nothing until that date, then normal |
| out, no date | nothing for the rest of the week |
| day-to-day, game-time decision | his rate times `P(plays \| status)` |

`P(plays | status)` starts as a stated constant and becomes a measurement: the
listener stores a status per player per pass, and `player_game_stats` says
whether he then played, so after a few weeks the rate is countable per status.
Write it as a named constant with "measured from N player-days" beside it as
soon as there is an N, exactly as `CONCEDE_PENALTY` and `GAMES_PER_WEEK` were.

Out-for-the-season is a case the data cannot see: ESPN's status says OUT and
nothing says for how long. That is the model's largest known error, and the
reason the week is re-forecast on every pass rather than fixed on Monday.

## 4. The distribution of a week

A team's week is a sum over started games. Two ways to get from rates to a
distribution, and the second is the one to build:

1. **Normal approximation.** Mean = Σ rate × started games; variance from a
   per-player per-game variance. Cheap, and wrong in the tails for a
   five-game week.
2. **Monte Carlo over games (build this).** For each started game, draw that
   player's line from his own game-to-game distribution, measured from
   `player_game_stats`: his mean and spread per category over the season to
   date, shrunk toward the pool's spread for a player with few games (the same
   shrinkage idea as `knowable`'s). Sum the draws into a week; repeat ~5,000
   times for each team. Percentages are rebuilt from drawn makes and attempts,
   never averaged (`app.scoring.lines`).

Monte Carlo because the question is P(A > B) in nine correlated categories,
and because a manager wants to see the shape: "you win rebounds unless Sabonis
sits" is a sentence the draws can produce and a normal cannot.

Correlation worth keeping: a player's own categories move together (a big
night is big everywhere), so draw a player's whole line from one game rather
than each category independently. Correlation between players is ignored, and
that is a stated simplification, not an oversight.

## 5. Output

For one matchup:

- **Each category:** my projected total and spread, the opponent's, and the
  chance I win it. Categories already decided mathematically (the remaining
  games cannot close the gap) are marked as such rather than given a 0.02.
- **The matchup:** the chance of winning a majority of the nine, from the
  same draws, and the most likely final score.
- **What would change it:** the three categories with the highest chance of
  flipping, which is where a pickup or a lineup change is worth making. This
  is the hand-off to `app/pickups/stream.py`.
- **Confidence:** the number of started games each side has left, because a
  probability from two games left is a different animal from one with twenty.

## 6. What BBM would add, when it is available

BBM's daily projections adjust minutes for what we cannot see: a starter ruled
out this afternoon, a back-to-back, a blowout rotation, the opponent's defence.
When `dailyprojections.aspx` goes live (checked 2026-10-05), the rate in §0
becomes a choice of source, and the honest test is to run both for a few weeks
and score them (§7). BBM's own H2H Weekly projects this league's matchups, so
it is also a benchmark for the whole model, not just the rates.

## 7. The backtest, and what it cannot prove

Every 2026 week is stored, so the model can be run on each one from what was
knowable at the time and scored against what happened:

- **Calibration:** across all weeks and categories, of the weeks predicted at
  70%, how many were won? A line through the bins is the headline. Anything
  else is a story about one lucky week.
- **Against the naive rule:** the same weeks predicted by "whoever is ahead
  today wins". A model that cannot beat that is not worth running.
- **Against the season model:** the draft room's opponent distribution
  (`category_distributions`) is the same question asked without knowing who
  the opponent is, so it is the floor.

What the backtest cannot do is test §3, because no injury history exists
before the listener started on 2026-09-17. The backtest must treat every
player as available and say so in its output; the availability model gets its
first honest test in-season, against the listener's own record.

## 8. Order of work

1. `app/week/roster.py` and the started-games count, on P2.
2. The per-player per-game distribution and the Monte Carlo, with the
   backtest, before any of it is shown. A predictor nobody has scored is a
   confident guess.
3. The availability model, as soon as the listener has enough days.
4. The CLI, then the API route, then the digest line ("you are 62% to win
   this week; rebounds is the category in play").
5. BBM as a second rate source, if it is available and it scores better.
