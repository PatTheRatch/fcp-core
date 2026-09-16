#!/usr/bin/env python3
"""Hit these targets, maximise the rest: a roster built to category floors.

Usage:
    python scripts/soft_targets.py --season 2027 --me "Through The Wire" \\
        --bbm data/bbm/BBM_Projections_2027_total.xls \\
        --bbm-per-game data/bbm/BBM_Projections_2027_pergame.xls \\
        --target REB=0.65 --target BLK=0.65 --target "FG%=0.55" \\
        [--target "TO<=60"] [--maximize wins | --maximize PTS,AST] [--punt FT%] \\
        [--lock "Jokic=90"] [--exclude Wembanyama] [--no-shape] \\
        [--time-limit 600] [--gap 0.002] [--out logs/soft-targets.md]

The draft optimizer builds the roster with the most expected category wins a
week. This asks a different question: the roster that wins THESE categories
this often, and does as well as it can on everything else. It is the exact
solver (`app/draft/exact.py`, the same model that proved the local search at
the optimum) with one soft floor per target, so it always comes back with a
roster. Targets that cannot all be met are missed by the least, and the
report says which and by how much.

TARGETS

    REB=0.65        win rebounds 65% of weeks
    REB>=210        post at least 210 rebounds a week (turned into the win
                    probability that total has against the opponent)
    TO<=60          stay under 60 turnovers a week
    FG%=0.55        win field goal percentage 55% of weeks; for a rate,
                    `=` is always a probability and `>=` a rate to shoot

WHAT IS MAXIMISED

`wins` (the default) is the ordinary objective: expected categories won a
week over every category not punted, less the concede charge. Naming
categories instead (`--maximize PTS,AST`) counts only those, so the roster
chases the targets first and those categories second, and the rest are held
up only by the concede charge and whatever the targets need. A punted
category counts for nothing and is exempt from the charge, as everywhere.

The roster is built from the empty room at going prices, inside the plan's
spending shape unless `--no-shape`. `--lock` puts a player on the roster at a
price, as if just won; `--exclude` takes one off the board, as if someone
else had. Names match loosely, and an ambiguous one is refused with the
alternatives rather than guessed.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from statistics import NormalDist

from app.draft.exact import ExactError, ExactPlan, Target, solve_exact
from app.draft.feed import match_name
from app.draft.live import RoomError, load_room
from app.draft.optimizer import Candidate, RosterPlan, optimize
from app.draft.room import resolve
from app.draft.targets import CategoryDistribution
from app.draft.valuation import PERCENTAGE_COMPONENTS

_TARGET = re.compile(r"^\s*(?P<cat>[A-Za-z0-9%]+)\s*(?P<op>>=|<=|=)\s*(?P<value>[0-9.]+)\s*$")


def parse_target(text: str, distributions: dict[str, CategoryDistribution]) -> Target:
    """`REB=0.65`, `REB>=210`, `TO<=60` or `FG%>=0.49` into a win-probability floor."""
    found = _TARGET.match(text)
    if not found:
        raise SystemExit(f"cannot read target {text!r}: want CAT=p, CAT>=total or CAT<=total")
    cat = found["cat"].upper()
    if cat not in distributions:
        raise SystemExit(f"no category {cat!r}; the room scores {', '.join(distributions)}")
    d = distributions[cat]
    op, value = found["op"], float(found["value"])
    if op == "=" and (cat in PERCENTAGE_COMPONENTS or value <= 1.0):
        if not 0.0 < value < 1.0:
            raise SystemExit(f"{text!r}: a win probability must sit strictly between 0 and 1")
        return Target(cat, value)
    # A total. The comparison has to point the way the category is won.
    if (op == "<=") != d.lower_is_better and op != "=":
        want = "<=" if d.lower_is_better else ">="
        how = "less" if d.lower_is_better else "more"
        raise SystemExit(f"{text!r}: {cat} is won by posting {how}; write {cat}{want}{value:g}")
    return Target(cat, d.win_probability(value))


def implied_total(d: CategoryDistribution, probability: float) -> float:
    """The weekly total that wins this often against the opponent."""
    z = NormalDist().inv_cdf(min(max(probability, 1e-9), 1 - 1e-9))
    if d.lower_is_better:
        z = -z
    return d.mean + z * d.spread


def player_of(text: str, names: dict[str, int]) -> int:
    exact = names.get(text)
    if exact is not None:
        return exact
    found = match_name(text, names)
    if found is None:
        raise SystemExit(f"no player matches {text!r}")
    if found.rival:
        raise SystemExit(f"{text!r} could be {found.name} or {found.rival}; be more specific")
    return found.value


def fmt(cat: str, value: float) -> str:
    return f"{value:.3f}" if cat in PERCENTAGE_COMPONENTS else f"{value:.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--me", required=True)
    ap.add_argument("--bbm", type=Path, required=True)
    ap.add_argument("--bbm-per-game", type=Path)
    ap.add_argument("--target", action="append", default=[], help="CAT=p, CAT>=total, CAT<=total")
    ap.add_argument("--maximize", default="wins", help="'wins' or a comma list of categories")
    ap.add_argument("--punt", action="append", default=[])
    ap.add_argument("--lock", action="append", default=[], metavar="NAME=PRICE")
    ap.add_argument("--exclude", action="append", default=[], metavar="NAME")
    ap.add_argument("--no-shape", action="store_true", help="ignore the spending plan")
    ap.add_argument("--time-limit", type=float, default=600.0)
    ap.add_argument("--gap", type=float, default=0.002)
    ap.add_argument("--breakpoints", type=int, default=60)
    ap.add_argument("--rate-step", type=float, default=0.002)
    ap.add_argument("--out", type=Path, default=Path("logs/soft-targets.md"))
    args = ap.parse_args()

    try:
        room = load_room(
            args.season,
            args.me,
            pool_season=None,
            pool_kind="projected",
            punt=args.punt,
            restarts=12,
            bbm=args.bbm,
            bbm_per_game=args.bbm_per_game,
            plan="history",
        )
    except RoomError as exc:
        raise SystemExit(str(exc)) from exc
    state = room.state
    by_cat = {d.abbreviation: d for d in room.distributions}
    cats = list(by_cat)
    targets = [parse_target(t, by_cat) for t in args.target]
    punt = [p.upper() for p in args.punt]
    maximize = (
        None if args.maximize.lower() == "wins" else [c.upper() for c in args.maximize.split(",")]
    )
    for cat in [*punt, *(maximize or [])]:
        if cat not in by_cat:
            raise SystemExit(f"no category {cat!r}; the room scores {', '.join(cats)}")

    names = {c.name: c.player_id for c in room.candidates}
    locks: dict[int, int] = {}
    for text in args.lock:
        name, _, price = text.rpartition("=")
        if not name or not price.strip().isdigit():
            raise SystemExit(f"cannot read lock {text!r}: want NAME=PRICE")
        locks[player_of(name.strip(), names)] = int(price)
    excluded = [player_of(text, names) for text in args.exclude]
    by_id = {c.player_id: c for c in room.candidates}
    candidates = [
        Candidate(c.player_id, c.name, locks[c.player_id], c.weekly, c.eligible, c.position)
        if c.player_id in locks
        else c
        for c in room.candidates
    ]
    shape = None
    if room.allocation is not None and not args.no_shape:
        shape = room.allocation.limits(state)

    # The unconstrained best, for what the targets cost.
    free: RosterPlan = resolve(
        state,
        candidates,
        room.distributions,
        punt=punt,
        lineup=room.lineup,
        limits=room.limits,
        restarts=12,
        lock=locks or None,
        exclude=excluded,
        allocation=None if args.no_shape else room.allocation,
    )
    print(f"best without targets: {free.expected_wins:.3f} a week, ${free.cost}", flush=True)

    print(
        f"solving: {len(candidates)} candidates, {len(targets)} targets, "
        f"time limit {args.time_limit:.0f}s, gap {args.gap}",
        flush=True,
    )
    fallback: str | None = None
    plan: ExactPlan | None = None
    try:
        plan = solve_exact(
            candidates,
            room.distributions,
            budget=state.budget,
            roster_slots=state.roster_slots,
            lineup=room.lineup,
            limits=room.limits,
            shape=shape,
            punt=punt,
            locked=list(locks),
            excluded=excluded,
            targets=targets,
            maximize=maximize,
            breakpoints=args.breakpoints,
            rate_step=args.rate_step,
            time_limit=args.time_limit,
            gap=args.gap,
        )
    except ExactError as exc:
        fallback = str(exc)
        print(f"exact solver: {exc}; falling back to the local search", flush=True)

    if plan is not None:
        players = sorted(plan.players, key=lambda c: -c.price)
        probabilities = plan.win_probability
        expected = plan.expected_wins
        cost = plan.cost
        solver = (
            f"{plan.status} in {plan.seconds:.0f}s"
            + ("" if plan.optimal else " (time limit; best roster found so far)")
            + (
                ""
                if plan.gap is None or plan.gap <= 0
                else f", gap {plan.gap:.4f} (a better roster, if one exists, scores at most "
                f"{plan.gap * abs(plan.objective):.2f} more)"
            )
        )
    else:
        local = optimize(
            candidates,
            room.distributions,
            budget=state.budget,
            roster_slots=state.roster_slots,
            punt=punt,
            locked=list(locks),
            excluded=excluded,
            restarts=12,
            lineup=room.lineup,
            limits=room.limits,
            shape=shape,
        )
        players = sorted(local.players, key=lambda c: -c.price)
        probabilities = local.win_probability
        expected = local.expected_wins
        cost = local.cost
        solver = f"local search; the exact solver returned nothing ({fallback})"

    shortfall = {
        t.abbreviation: max(0.0, t.win_probability - probabilities[t.abbreviation]) for t in targets
    }
    met = sum(1 for v in shortfall.values() if v <= 1e-9)
    lines = [
        "# Hit these targets, maximise the rest",
        "",
        f"{args.season} room, {len(state.teams)} teams, ${state.budget}, "
        f"{state.roster_slots} places, from `{args.bbm.name}`; "
        f"computed {datetime.date.today():%-d %b %Y}.",
        "",
        "## Asked for",
        "",
        f"- **Targets:** {', '.join(args.target) if args.target else 'none'}",
        "- **Maximise:** "
        + ("expected wins over every category" if maximize is None else ", ".join(maximize))
        + (f"; punting {', '.join(punt)}" if punt else ""),
        "- **Locked:** "
        + (", ".join(f"{by_id[p].name} at ${v}" for p, v in locks.items()) or "nobody")
        + "; **excluded:** "
        + (", ".join(by_id[p].name for p in excluded) or "nobody"),
        "- **Spending shape:** "
        + (
            "ignored"
            if shape is None
            else f"{list(shape)}"
            + (
                ""
                if plan is None or plan.shaped
                else " (dropped: the locked players alone break it)"
            )
        ),
        "",
        "## Result",
        "",
        f"- **{met} of {len(targets)} targets met.**"
        + ("" if met == len(targets) else " The rest are missed by the least the board allows."),
        f"- Expected categories won a week: **{expected:.3f}**, against {free.expected_wins:.3f} "
        f"for the best roster with no targets. The targets cost "
        f"{max(0.0, free.expected_wins - expected):.3f} a week.",
        f"- Cost ${cost} of ${state.budget}. Solver: {solver}.",
        "",
        "## Targets",
        "",
        "| category | target | means posting | roster posts | wins | met |",
        "|---|---|---|---|---|---|",
    ]
    totals = {}
    for c in cats:
        totals[c] = sum(p.weekly.get(c, 0.0) for p in players)
    for c, (made, attempted) in PERCENTAGE_COMPONENTS.items():
        if c in cats:
            m = sum(p.weekly.get(made, 0.0) for p in players)
            a = sum(p.weekly.get(attempted, 0.0) for p in players)
            totals[c] = m / a if a else 0.0
    for t in targets:
        c = t.abbreviation
        short = shortfall[c]
        needs = fmt(c, implied_total(by_cat[c], t.win_probability))
        lines.append(
            f"| {c} | {t.win_probability:.2f} | {needs} | {fmt(c, totals[c])} "
            f"| {probabilities[c]:.3f} | {'yes' if short <= 1e-9 else f'no, short {short:.3f}'} |"
        )
    lines += [
        "",
        "## Roster",
        "",
        "| player | price | position | BBM $ | ESPN avg |",
        "|---|---|---|---|---|",
    ]
    for p in players:
        row = room.bbm.get(p.player_id)
        bbm = "" if row is None or row.league_dollars is None else f"${round(row.league_dollars)}"
        espn = "" if row is None or row.espn_dollars is None else f"${round(row.espn_dollars)}"
        lines.append(f"| {p.name} | ${p.price} | {p.position or ''} | {bbm} | {espn} |")
    lines += [
        "",
        "## Every category, against the best roster with no targets",
        "",
        "| category | targeted roster | no targets |",
        "|---|---|---|",
        *(f"| {c} | {probabilities[c]:.3f} | {free.win_probability[c]:.3f} |" for c in cats),
        "",
    ]
    if plan is not None:
        lines += [
            "## Model",
            "",
            f"- {plan.columns} columns, {plan.rows} rows; model objective {plan.objective:.4f} "
            f"(includes the target penalties) against the real score {plan.expected_wins:.4f}.",
            f"- Piecewise-linear error per counting category at most {plan.breakpoint_error:.4f} "
            f"({args.breakpoints} segments); rate grid {args.rate_step} (at most "
            f"{plan.rate_error:.4f} of probability floored away per percentage).",
            "",
        ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
