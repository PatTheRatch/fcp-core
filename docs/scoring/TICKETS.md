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

## Where it stands (2026-09-17)

| ticket | state |
|---|---|
| S1 | tabled; probe on branch `scoring-s1` |
| S2 | done (`app/scoring/trades.py`), VPS agent, reviewed |
| S3 | done (`app/scoring/lines.py`) |
| S4 | done (`app/scoring/value.py`) |
| S5 | done (`app/scoring/league.py`) |
| S6 | done (`app/scoring/replacement.py`, `season.py`) |
| S7 | done (`app/scoring/players.py`) |
| S8 | done (`app/scoring/knowable.py`), fitted blend rather than the ticket's |
| S9 | done (`app/scoring/draft.py`) |
| S10 | merged to main; apply migration 0014 on the VPS in the same step as pulling main |
| S11 | done (`app/scoring/trade_grades.py`, on `moves.py`) |
| S12 | done (`app/scoring/wire.py`, on `moves.py`) |
| S13 | done (`app/scoring/verdicts.py`), VPS agent, reviewed and corrected |
| S14 | done (`app/api/scorecard.py`, `scripts/scorecard.py`) |
| S15 | done; three 2026 reports regenerated |

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
The current reconstruction is known to over-detect before 2026: summed over
teams it finds 107 trade sides in 2024 and 153 in 2025, against ESPN's
`teams.trades` totals of 48 and 56 (2026 is close: 52 against 64). Likely
cause: pre-2026 waiver moves lacking transaction rows, so the "no executed
waiver explains it" exclusion misses them. Fix it here.
**Done when:** (1) per season, reconstructed trade sides are within ESPN's
`teams.trades` total or explicitly below it (a lower bound), with a table of
both in the module docstring for 2019-2026; (2) a per-team check that no
team shows more reconstructed trades than `teams.trades` without a stated
reason; (3) tests on a fixture season (a two-team swap, a 2-for-1, a
one-sided "part missing", and a waiver pickup that must NOT count).

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
