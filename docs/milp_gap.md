# How far is the draft optimizer's local search from the true optimum?

**STATUS: the exact model is BUILT but INFEASIBLE. No gap is reported.**

This document records what works, what does not, and exactly where the run
stopped, so the next attempt starts from the evidence rather than from scratch.
Nothing here is a measured result, because none was obtained.

Branch `milp-gap`. `scripts/milp_gap.py`, plus an optional `opt` dependency
group in `pyproject.toml` holding `highspy`.

---

## What is verified

**The problem loads and the brief's sanity numbers reproduce.** This is the gate
the brief sets, and it passes:

| check | brief says | measured |
|---|---|---|
| opponent mean PTS | 582 | 581.7061 |
| opponent mean REB | 198 | 197.7836 |
| opponent mean FG% | .479 | 0.4786 |
| `resolve(...)` | ~5.06 | 5.0584 |

Loading uses exactly the call the brief specifies, against the 2027 season
(15 teams, $200, C limit 3, 512 candidates, lineup
`PG SG SF PF C G F UT UT UT`, spending shape
`(60, 46, 30, 23, 16, 13, 9, 6, 4, 3, 2, 2, 1)`).

**The local search runs, and is remarkably stable.** Fifteen solves, all on the
same inputs:

| restarts | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 | seconds |
|---|---|---|---|---|---|---|
| 4 | 5.075864 | 5.086722 | 5.086722 | 5.086783 | 5.086783 | 1.1-1.2 |
| 12 | 5.086722 | 5.086783 | 5.086783 | 5.086783 | 5.086783 | 2.6-3.3 |
| 48 | 5.086783 | 5.086783 | 5.086783 | 5.086783 | 5.086783 | 10.9-12.3 |

Best 5.086783, median 5.086783, worst 5.075864. Fourteen of the fifteen runs
land on the same score to six decimals, and the only straggler is `restarts=4,
seed=0`. One 48-restart solve takes ~11-12 seconds, so a single solve at
restarts 4 is roughly a second and a bid ceiling (about 8 solves) is roughly
8-10 seconds.

**This is the one substantive finding so far, and it is only a consistency
result, not a gap**: the local search converges to the same roster across
seeds and restart counts almost always, but whether that roster is optimal is
exactly what the model was built to answer and could not.

**The model is well-formed.** 8,628 columns (7,897 of them binary), 2,555 rows,
15,000+ non-zeros; HiGHS accepts it. Every group was verified feasible *in
isolation* against the fixed roster: roster size and budget, the centre cap,
the lineup matching, and the spending-shape matching each solve to `Optimal`
on their own.

## What is broken

**The assembled model is infeasible**, both with the roster free and with the
local search's roster fixed. So the defect is not in the extra fixing rows; it
is in the base model, in the interaction between groups that each pass alone.

Established by bisection:

- Relaxing the whole CDF and ladder block makes the model feasible. That is the
  only relaxation that does.
- Relaxing the shortfall rows, the shape rows, or the lineup rows alone does
  **not** make it feasible.
- The three load-bearing rows are `Σx = 13` and the per-slot fills
  `Σ_i a_{i,PG} = 1` and its siblings, which are individually satisfiable.
- Fully disabling the percentage ladders and flattening their grids still
  leaves the model infeasible, so the percentages are not the sole cause.

The last thing inspected, and the most likely place to resume: the percentage
ladder's entailment rows were built as

    made - rate*attempted + M*z  <=  M

which is the wrong direction for "z = 1 forces the threshold to hold". It was
changed to the intended

    made - rate*attempted - M*z  >= -M

and the model is **still infeasible**, so either that fix is incomplete or there
is a second defect. The `-M(1 - z)` form needs checking against the sign
convention of `z` (currently `z_j <= z_{j-1}`, so `z_j` means "rate at least
t_j"), because if `z` is monotone the wrong way the entailment rows are
unsatisfiable for the roster's actual rate.

## What has NOT been done

- The gap between the local search and the optimum is **not measured**.
- The three required self-checks (`score`, `fieldable`, `within_shape`) have not
  been run to completion on a MILP roster, because no MILP roster exists.
- The consistency check (fix `x` to the local search's roster, confirm the
  model reproduces its exact score) has not passed. It currently returns
  `Infeasible` instead of a number, which is the symptom, not the check.
- `docs/milp_gap.md` does not exist: this file is a status note, not the
  deliverable the brief asks for.

## Where to resume

1. Fix the percentage ladder: the entailment rows, the `z` monotonicity
   direction, and the row that ties the credited rate to the `dp_` delta chain
   are three constraints that must agree, and at least one is wrong. Test the
   percentage block **through `build_model`**, not by rebuilding a small model
   by hand -- every hand-rebuilt variant passed while the real assembly failed,
   which is what made this cost so much time.
2. Once feasible, run the fixed-roster consistency check first: it is the
   cheapest way to prove the objective matches `optimizer.score`, and it is the
   check the brief demands before any gap is reported.
3. Then solve to optimality and run the three self-checks, the restart/seed
   table, and write the report.

## Rules observed

Production `/opt/fcp-core` is untouched on `main`. Migrations at `0013 (head)`,
none pending and none applied. Read-only queries only. `app/` unmodified. BBM
exports not committed. `ruff check` and `mypy --strict` pass on the script.
