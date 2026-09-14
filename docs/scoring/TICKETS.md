# Scoring tickets

Build order for `docs/scoring/SPEC.md`. Each ticket is one branch, reviewed
before the next starts. Tickets in the same group can run in parallel once
their dependencies are merged.

Standing rules for every ticket (VPS model):
- Work in a git worktree; the production checkout at `/opt/fcp-core` stays on `main`.
- The database is live. Read-only queries only.
- **Never apply a migration.** If a ticket writes one, commit the file and
  stop; Patrick applies it. If one is pending, stop and say so.
- `ruff` (line 100) and `mypy --strict` clean; tests for every public function.
- No changes outside the files the ticket names without saying why.
- Report: branch, commit, files changed, test count, anything surprising.

---

## Group A -- foundations (no dependencies)

### S1. Spike: does ESPN serve rest-of-season projections in season?
Probe only; no app code. Using the league's cookies, fetch player cards for
the current season (`kona_player_info`) and record which stat-split keys
exist (e.g. `002026`-style projected, rest-of-season, last-7/15/30), what
they contain, and whether they change day to day. Write findings to
`docs/scoring/espn_projections.md` with raw examples.
**Done when:** we know exactly which key to capture daily, or that none exists.

### S2. Move trade reconstruction into `app/scoring/trades.py`
Lift `_trades` out of `scripts/season_report.py` into a typed module:
`reconstruct_trades(session, season, team_id) -> list[Trade]` with both
sides and the day. The report calls the module. Tests on a fixture season
(a two-team swap, a 2-for-1, a one-sided "part missing").
**Done when:** the report's trade section is byte-identical before and after.

### S3. Weekly roster lines: `app/scoring/lines.py`
`team_week_line(session, team_id, period, players=None) -> CategoryLine`:
the category line a set of players produced while started for a team in a
matchup period, percentages from makes/attempts. Also
`player_week_line(...)` for one player. Reuse the verified join in
`scripts/waiver_value.py` (read its data-model notes first).
**Done when:** a team's computed week line matches `matchup_team_stats` for
that week to within rounding on 3 sample weeks per season.

## Group B -- the currency (needs S3)

### S4. Expected category wins: `app/scoring/value.py`
`expected_wins(line, distributions) -> float` and
`marginal(team_line, player_line, distributions) -> float` (with minus
without, percentages recombined, TO inverted). Wraps the optimizer's scoring
rather than copying it.
**Done when:** tests pin a punted category contributing ~0 and a close
category contributing the most.

### S5. League-average team and punt inference
`average_team_line(session, season)` and
`punts(session, season, team_id) -> dict[category, bool]` from the season
category record (threshold as a named constant, justified in the docstring
from the league's own distribution).
**Done when:** Brighton 2026 flags 3PM; The Infirmary 2026 flags AST.

### S6. Replacement level: the value of an open roster spot
`replacement_value(session, season) -> float` in category wins per week:
the typical executed waiver add's started line, in the S4 currency, from
the league's history (method of `scripts/acquirable_value.py`).
**Done when:** a value per season with n and spread in the docstring.

## Group C -- grades (needs A and B)

### S7. Player value per team-season
`player_values(session, season, team_id) -> list[PlayerValue]`: team-fit and
league-standard value, regular season and playoffs separately, weeks held.

### S8. Recent-form stand-in for the decision lens
`knowable_line(session, player_id, day) -> CategoryLine`: preseason
projection blended with the prior 14 days of play (weights named and
justified). From 2027, prefer the S10 snapshot on or before `day`.

### S9. Draft grade
`draft_grades(session, season, team_id)`: price vs projected value, price vs
market (ESPN average via the cache in `scripts/price_scorecard.py`, board
fallback), and asset outcome (kept / traded -> S2 return value / dropped).

### S11. Trade grades
`trade_grades(session, season, team_id)`: decision (S8) and result (actual),
rest of season, regular and playoffs, with replacement value (S6) for
uneven player counts.

### S12. Wire grades
Same for executed adds, one-for-one and uneven.

## Group D -- capture (needs S1; migration applied by Patrick)

### S10. Daily projection snapshots
Migration for `player_projection_snapshots (player_id, captured_on, kind,
stats JSONB)`; a capture step in the scheduled ingest writing one row per
player per day for the key S1 found. **Do not apply the migration.**
**Done when:** tests pass against the test DB and the migration file is committed.

## Group E -- surface (needs C)

### S13. Verdicts
`verdict(decision, result) -> str` with named thresholds, e.g.
"Good call, bad break: +0.4 expected, -0.1 delivered". Table-driven tests.

### S14. Scorecard API and CLI
Read-only `GET /leagues/{id}/seasons/{season}/teams/{team}/scorecard` and
`scripts/scorecard.py`, returning S7, S9, S11, S12 with verdicts.

### S15. Season report on the new scoring
`scripts/season_report.py` reads the scorecard; nine-cat points move to
fine print; regenerate the three 2026 commentary reports.
