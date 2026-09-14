# Scoring spec: players, drafts, trades and the wire

Agreed with the manager on 2026-09-14. Replaces the nine-cat points total
the season reports used, which scored a categories league as if it were a
points league, ignored team build (Knueppel's threes counted in full on a
team punting threes) and treated a pick traded for value as a wasted pick.

## Currency

Everything is measured in **category wins per week**: the change in a team's
expected categories won in a week against the league's measured weekly
opponent distribution for that season (`app.draft.targets.category_distributions`,
the same normal-CDF model the draft optimizer scores with). Percentages are
rebuilt from makes and attempts, so volume counts; turnovers count against.

The nine-cat composite (PTS+REB+AST+STL+BLK+3PM-TO) is retired as a headline.
It may appear in fine print as an activity measure.

## Player value

- **Team fit (headline).** What the player's started production did for this
  roster: the team's expected weekly category wins with his line, minus
  without it, over the weeks he was held. Measured against the roster as it
  stood at the time.
- **League standard (fine print).** The same marginal against a
  league-average team, so one number per player is comparable across teams.
- **Punts are inferred, never assumed.** A category a team wins in a small
  share of its matchups all season is flagged "looks like a punt"; the
  commentary says so and does not claim intent.

## Two lenses, decision first

Every grade carries both:

1. **Decision** -- was it sound on what was knowable at the time?
2. **Result** -- how did it turn out?

Where they disagree the verdict says so ("good call, bad break",
"lucky break").

**What was knowable:**
- Past seasons (no in-season projections were saved): preseason projections
  blended with the player's recent form going into the move.
- From 2026-27 on: ESPN's rest-of-season projections captured daily, used as
  of the day of the move.

## Draft grade

- **Price**, two checks: price against the pick's projected value (knowable
  at the draft), and price against the market -- what he was expected to go
  for (the fitted ESPN-average/board blend in `app/draft/live.py`).
- **Asset**, what the pick became: kept -> what he did for the team;
  traded -> the value of what came back; dropped -> nothing after that day.
  One hop only; later trades belong to the trade grades.

## Trade and wire grades

- Category wins gained or lost over the rest of the season.
- An open roster spot is worth a **typical waiver pickup** measured from the
  league's own history, not zero, so a 2-for-1 is not penalised by
  construction.
- Team fit against the roster on the day of the move.

## Playoffs

Regular-season value and playoff value are separate lines. Never blended.

## Presentation

Plain-language verdicts with the numbers behind them, e.g.
"Good call, bad break: +0.4 categories a week expected, -0.1 delivered."
No letter grades.

## Where it lives

Computed by code, not re-derived per request: a `app/scoring/` package,
exposed through the API and a CLI, so any metric can be asked for at any
time during the season and only the commentary costs model time.
