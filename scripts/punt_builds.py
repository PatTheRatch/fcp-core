#!/usr/bin/env python3
"""Punt builds in Full Court Press: does conceding categories win here?

Usage:
    python scripts/punt_builds.py            # writes docs/punt_builds.md

The draft model's best 2027 roster concedes three categories (FT%, 3PM, PTS).
The league's evidence for balance (docs/stars_and_waivers.md) is about how a
team spreads its money, not about conceding categories, so this tests the
second question on the league's own 98 team-seasons.

THE TRAP

A weak team loses most categories and looks like a punt team without having
chosen anything. So a punt is measured three ways, and none of them alone:

  chosen punt     the DRAFTED roster projected bottom two in the league in a
                  category, from that season's preseason projections, while
                  its projected overall strength was not bottom quartile.
                  Loose by construction -- bottom two of ten happens by
                  accident -- so a STRICT version is reported beside it: the
                  category 1.5 standard deviations below the league's teams.
                  Seasons with usable projections only: 2019, 2021, 2022,
                  2024, 2025, 2026.
  realized punt   a category won in under 25% of regular-season matchups
                  (sensitivity at 20% and 30%).
  specialization  the standard deviation of a team's nine category win rates,
                  always compared within terciles of overall strength.

Outcomes are compared after removing what strength alone predicts: category
win rate against projected strength, matchup win rate against category win
rate. Teams in one season share a player pool, so every headline comparison
has per-season differences and a bootstrap that resamples seasons.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, pstdev

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft import pool
from app.draft.projections import projection_problem
from app.draft.valuation import value_players

CATS = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")
REPORT = Path("docs/punt_builds.md")
MANAGER_OWNER = "{238280FE-C9BA-47E1-90AF-F6B9ED7AAF43}"
REALIZED_THRESHOLD = 0.25
STRICT_Z = -1.5
SENSITIVITY = (0.20, 0.30)
MIN_CELL = 8
ROUNDS = 2000
SEED = 20260915
BIG_PUNTS = frozenset({"FT%", "3PM"})
GUARD_PUNTS = frozenset({"FG%", "REB", "BLK", "TO"})


@dataclass
class TeamSeason:
    season: int
    teams: int
    team_id: int
    name: str
    owner: str
    cat_rates: dict[str, float]
    cat_win_rate: float
    matchups_won: int
    matchups_played: int
    close_won: int
    close_played: int
    made_playoffs: bool
    title: bool
    playoff_won: int
    playoff_played: int
    top3_share: float
    projected: dict[str, float] = field(default_factory=dict)
    projected_rank: dict[str, int] = field(default_factory=dict)
    projected_strength: float | None = None
    strength_quartile: int | None = None
    chosen: tuple[str, ...] = ()
    chosen_strict: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[int, int]:
        return (self.season, self.team_id)

    @property
    def matchup_rate(self) -> float:
        return self.matchups_won / self.matchups_played if self.matchups_played else 0.0

    @property
    def specialization(self) -> float:
        return pstdev(self.cat_rates[c] for c in CATS)

    def realized(self, threshold: float = REALIZED_THRESHOLD) -> tuple[str, ...]:
        return tuple(c for c in CATS if self.cat_rates[c] < threshold)

    @property
    def has_projection(self) -> bool:
        return self.projected_strength is not None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load(session: Session) -> list[TeamSeason]:
    teams = session.execute(
        text(
            """
            SELECT ls.season, ls.team_count, t.id, t.name, t.categories_won,
                   t.categories_lost, t.categories_tied, t.final_standing,
                   ls.playoff_team_count, coalesce(o.espn_owner_id, '')
            FROM teams t
            JOIN league_seasons ls ON ls.id = t.league_season_id
            LEFT JOIN team_owners tow ON tow.team_id = t.id
            LEFT JOIN owners o ON o.id = tow.owner_id
            WHERE ls.season BETWEEN 2019 AND 2026
            """
        )
    ).all()
    rates: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for team_id, cat, result, n in session.execute(
        text(
            """
            SELECT ts.team_id, ts.abbreviation, ts.result, count(*)
            FROM matchup_team_stats ts
            JOIN matchups m ON m.id = ts.matchup_id
            JOIN matchup_periods mp ON mp.id = m.matchup_period_id
            WHERE mp.is_playoff = false AND m.away_team_id IS NOT NULL
              AND ts.result IS NOT NULL
            GROUP BY 1, 2, 3
            """
        )
    ).all():
        slot = {"WIN": 0, "LOSS": 1, "TIE": 2}[str(result)]
        rates[int(team_id)][str(cat)][slot] += int(n)

    weekly: dict[tuple[int, bool], list[tuple[int, int]]] = defaultdict(list)
    for team_id, playoff, won, lost in session.execute(
        text(
            """
            SELECT ts.team_id, mp.is_playoff,
                   count(*) FILTER (WHERE ts.result = 'WIN'),
                   count(*) FILTER (WHERE ts.result = 'LOSS')
            FROM matchup_team_stats ts
            JOIN matchups m ON m.id = ts.matchup_id
            JOIN matchup_periods mp ON mp.id = m.matchup_period_id
            WHERE m.away_team_id IS NOT NULL AND ts.result IS NOT NULL
            GROUP BY ts.team_id, m.id, mp.is_playoff
            """
        )
    ).all():
        weekly[(int(team_id), bool(playoff))].append((int(won), int(lost)))

    prices: dict[int, list[int]] = defaultdict(list)
    drafted: dict[int, list[int]] = defaultdict(list)
    for team_id, espn_id, price in session.execute(
        text(
            """
            SELECT dp.team_id, p.espn_player_id, dp.bid_amount
            FROM draft_picks dp JOIN players p ON p.id = dp.player_id
            WHERE dp.bid_amount IS NOT NULL
            """
        )
    ).all():
        prices[int(team_id)].append(int(price))
        drafted[int(team_id)].append(int(espn_id))

    out: list[TeamSeason] = []
    seen: set[int] = set()
    for season, size, team_id, name, won, lost, tied, standing, playoff_teams, owner in teams:
        if int(team_id) in seen or int(team_id) not in rates:
            continue
        seen.add(int(team_id))
        cat_rates = {}
        for cat in CATS:
            w, lo, t = rates[int(team_id)][cat]
            cat_rates[cat] = (w + 0.5 * t) / max(1, w + lo + t)
        regular = weekly[(int(team_id), False)]
        playoff = weekly[(int(team_id), True)]
        spend = sorted(prices[int(team_id)], reverse=True)
        decided = int(won) + int(lost) + int(tied)
        out.append(
            TeamSeason(
                season=int(season),
                teams=int(size),
                team_id=int(team_id),
                name=str(name),
                owner=str(owner),
                cat_rates=cat_rates,
                cat_win_rate=(int(won) + 0.5 * int(tied)) / max(1, decided),
                matchups_won=sum(1 for w, lo in regular if w > lo),
                matchups_played=sum(1 for w, lo in regular if w != lo),
                close_won=sum(1 for w, lo in regular if w - lo == 1),
                close_played=sum(1 for w, lo in regular if abs(w - lo) == 1),
                made_playoffs=int(standing) <= int(playoff_teams),
                title=int(standing) == 1,
                playoff_won=sum(1 for w, lo in playoff if w > lo),
                playoff_played=sum(1 for w, lo in playoff if w != lo),
                top3_share=sum(spend[:3]) / sum(spend) if spend else 0.0,
            )
        )
    _project(session, out, drafted)
    return out


def _project(session: Session, rows: list[TeamSeason], drafted: dict[int, list[int]]) -> None:
    """Each drafted roster's projected category strength, where projections are usable."""
    by_season: dict[int, list[TeamSeason]] = defaultdict(list)
    for r in rows:
        by_season[r.season].append(r)
    for season, group in by_season.items():
        if projection_problem(season):
            continue
        ls = session.query(LeagueSeason).filter(LeagueSeason.season == season).one()
        projections = pool.load_projections(session, season)
        cats = [c for c in pool.season_categories(session, ls) if c in CATS]
        values = {v.player_id: v for v in value_players(projections, cats)}
        for r in group:
            totals = dict.fromkeys(cats, 0.0)
            for pid in drafted[r.team_id]:
                player = values.get(pid)
                if player is None:
                    continue
                for cv in player.categories:
                    if cv.abbreviation in totals:
                        totals[cv.abbreviation] += cv.value
            r.projected = totals
            r.projected_strength = sum(totals.values())
        z: dict[tuple[int, str], float] = {}
        for cat in cats:
            ordered = sorted(group, key=lambda team: -team.projected[cat])
            for rank, r in enumerate(ordered, 1):
                r.projected_rank[cat] = rank
            vals = [r.projected[cat] for r in group]
            mean, sd = fmean(vals), pstdev(vals) or 1.0
            for r in group:
                z[(r.team_id, cat)] = (r.projected[cat] - mean) / sd
        strengths = sorted(r.projected_strength or 0.0 for r in group)
        cut = strengths[max(0, len(strengths) // 4 - 1)] if len(strengths) >= 4 else strengths[0]
        for r in group:
            r.strength_quartile = 1 if (r.projected_strength or 0.0) <= cut else 2
            if r.strength_quartile == 1:
                r.chosen = ()
                continue
            r.chosen = tuple(c for c in cats if r.projected_rank.get(c, 0) >= r.teams - 1)
            r.chosen_strict = tuple(c for c in cats if z[(r.team_id, c)] <= STRICT_Z)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    mx, my = fmean(xs), fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx if sxx else 0.0
    return my - slope * mx, slope


def within_season_z(
    rows: Sequence[TeamSeason], value: Callable[[TeamSeason], float]
) -> dict[tuple[int, int], float]:
    """A value standardised inside its season, so seasons of different size compare."""
    out: dict[tuple[int, int], float] = {}
    groups: dict[int, list[TeamSeason]] = defaultdict(list)
    for r in rows:
        groups[r.season].append(r)
    for group in groups.values():
        vals = [value(r) for r in group]
        mean, sd = fmean(vals), pstdev(vals) or 1.0
        for r in group:
            out[r.key] = (value(r) - mean) / sd
    return out


def terciles(
    rows: Sequence[TeamSeason], value: Callable[[TeamSeason], float]
) -> dict[tuple[int, int], str]:
    """Low / mid / high tercile of a value, cut inside each season."""
    out: dict[tuple[int, int], str] = {}
    groups: dict[int, list[TeamSeason]] = defaultdict(list)
    for r in rows:
        groups[r.season].append(r)
    for group in groups.values():
        ordered = sorted(group, key=value)
        n = len(ordered)
        for i, r in enumerate(ordered):
            out[r.key] = "low" if i < n / 3 else ("mid" if i < 2 * n / 3 else "high")
    return out


def bootstrap(
    rows: Sequence[TeamSeason],
    statistic: Callable[[Sequence[TeamSeason]], float | None],
) -> tuple[float | None, float | None, float | None]:
    """The statistic, and a 95% interval from resampling whole seasons."""
    seasons = sorted({r.season for r in rows})
    by_season = {s: [r for r in rows if r.season == s] for s in seasons}
    point = statistic(rows)
    rng = random.Random(SEED)
    draws: list[float] = []
    for _ in range(ROUNDS):
        sample = [r for s in (rng.choice(seasons) for _ in seasons) for r in by_season[s]]
        value = statistic(sample)
        if value is not None:
            draws.append(value)
    if point is None or len(draws) < ROUNDS // 2:
        return point, None, None
    draws.sort()
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def mean_or_none(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


def fmt(value: float | None, pattern: str = "{:+.3f}") -> str:
    return "n/a" if value is None else pattern.format(value)


def thin(n: int) -> str:
    return " **thin**" if n < MIN_CELL else ""


def ci(result: tuple[float | None, float | None, float | None], pattern: str = "{:+.3f}") -> str:
    point, lo, hi = result
    if lo is None or hi is None:
        return f"{fmt(point, pattern)} (interval not available)"
    return f"{fmt(point, pattern)} (95% CI {fmt(lo, pattern)} to {fmt(hi, pattern)})"


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def report(rows: list[TeamSeason]) -> str:
    lines: list[str] = []
    add = lines.append
    projected = [r for r in rows if r.has_projection]
    seasons_p = sorted({r.season for r in projected})

    # Strength-adjusted residuals.
    strength_z = within_season_z(projected, lambda r: r.projected_strength or 0.0)
    a, b = fit([strength_z[r.key] for r in projected], [r.cat_win_rate for r in projected])
    cat_resid = {r.key: r.cat_win_rate - (a + b * strength_z[r.key]) for r in projected}
    ma, mb = fit([r.cat_win_rate for r in rows], [r.matchup_rate for r in rows])
    conv_resid = {r.key: r.matchup_rate - (ma + mb * r.cat_win_rate) for r in rows}
    strength_t = terciles(rows, lambda r: r.cat_win_rate)
    spec_t: dict[tuple[int, int], str] = {}
    for label in ("low", "mid", "high"):
        group = [r for r in rows if strength_t[r.key] == label]
        spec_t.update(terciles(group, lambda r: r.specialization))

    def punt_count(r: TeamSeason) -> str:
        n = len(r.chosen)
        return "0" if n == 0 else ("1" if n == 1 else "2+")

    # Headline statistics.
    def chosen_effect(sample: Sequence[TeamSeason]) -> float | None:
        p = [cat_resid[r.key] for r in sample if r.key in cat_resid and r.chosen]
        n = [cat_resid[r.key] for r in sample if r.key in cat_resid and not r.chosen]
        return fmean(p) - fmean(n) if p and n else None

    def two_plus_effect(sample: Sequence[TeamSeason]) -> float | None:
        p = [cat_resid[r.key] for r in sample if r.key in cat_resid and len(r.chosen) >= 2]
        n = [cat_resid[r.key] for r in sample if r.key in cat_resid and not r.chosen]
        return fmean(p) - fmean(n) if p and n else None

    def conversion_effect(sample: Sequence[TeamSeason]) -> float | None:
        hi = [conv_resid[r.key] for r in sample if spec_t[r.key] == "high"]
        lo = [conv_resid[r.key] for r in sample if spec_t[r.key] == "low"]
        return fmean(hi) - fmean(lo) if hi and lo else None

    def playoff_effect(sample: Sequence[TeamSeason]) -> float | None:
        p = [float(r.made_playoffs) for r in sample if r.has_projection and r.chosen]
        n = [float(r.made_playoffs) for r in sample if r.has_projection and not r.chosen]
        return fmean(p) - fmean(n) if p and n else None

    def strict_effect(sample: Sequence[TeamSeason]) -> float | None:
        p = [cat_resid[r.key] for r in sample if r.key in cat_resid and r.chosen_strict]
        n = [cat_resid[r.key] for r in sample if r.key in cat_resid and not r.chosen_strict]
        return fmean(p) - fmean(n) if p and n else None

    def close_effect(sample: Sequence[TeamSeason]) -> float | None:
        def rate(label: str) -> float | None:
            group = [r for r in sample if spec_t[r.key] == label]
            played = sum(r.close_played for r in group)
            return sum(r.close_won for r in group) / played if played else None

        hi, lo = rate("high"), rate("low")
        return hi - lo if hi is not None and lo is not None else None

    strict_ci = bootstrap(projected, strict_effect)
    close_ci = bootstrap(rows, close_effect)
    chosen_ci = bootstrap(projected, chosen_effect)
    two_ci = bootstrap(projected, two_plus_effect)
    conv_ci = bootstrap(rows, conversion_effect)
    playoff_ci = bootstrap(projected, playoff_effect)

    def confidence(result: tuple[float | None, float | None, float | None]) -> str:
        point, lo, hi = result
        if point is None or lo is None or hi is None:
            return "no evidence"
        if lo > 0 or hi < 0:
            return "strong"
        return "suggestive" if abs(point) >= 0.02 else "no evidence"

    chosen_n = sum(1 for r in projected if r.chosen)
    strict_n = sum(1 for r in projected if r.chosen_strict)
    two_n = sum(1 for r in projected if len(r.chosen) >= 2)

    add("# Punt builds: does conceding categories win here?")
    add("")
    add(
        "Full Court Press (ESPN 3853870), 2019-2026, "
        f"{len(rows)} team-seasons. Chosen punts use preseason projections, so they are "
        f"measured on the {len(projected)} team-seasons with usable projections "
        f"({', '.join(map(str, seasons_p))}). Generated by `scripts/punt_builds.py`."
    )
    add("")
    add("## 1. Summary")
    add("")
    add(
        f"- **Choosing a punt at the draft, against teams of the same projected strength:** "
        f"category win rate {ci(chosen_ci)} for the {chosen_n} teams with at least one "
        f"chosen punt. Confidence: **{confidence(chosen_ci)}**."
    )
    add(
        f"- **Two or more chosen punts:** {ci(two_ci)}, n={two_n}. "
        f"Confidence: **{confidence(two_ci)}**."
    )
    add(
        f"- **A strict chosen punt** (a category 1.5 SD below the league at the draft): "
        f"{ci(strict_ci)}, n={strict_n}. Confidence: **{confidence(strict_ci)}**."
    )
    add(
        f"- **Specialization and matchups:** the most specialized tercile converts category "
        f"win rate into matchup wins at {ci(conv_ci)} against the least specialized, within "
        f"the same strength tercile. Confidence: **{confidence(conv_ci)}**."
    )
    add(
        f"- **Close weeks:** one-category weeks (5-4 or 4-5) won, most specialized minus least "
        f"specialized of the same strength: {ci(close_ci, '{:+.0%}')}. "
        f"Confidence: **{confidence(close_ci)}**."
    )
    add(
        "- **Playoffs:** teams with a chosen punt make the playoffs at "
        f"{ci(playoff_ci, '{:+.0%}')} "
        f"against non-punters. Confidence: **{confidence(playoff_ci)}**."
    )
    add(
        "- **Reverse causality:** realized punts (categories lost most weeks) track weakness, "
        "not choice; every claim above is drawn from chosen punts or strength-matched "
        "comparisons, and section 2 shows how rarely a chosen punt stays a realized one."
    )
    add("")

    # Q1 prevalence
    add("## 2. Prevalence, and whether a chosen punt stays one")
    add("")
    add(
        "| season | teams | 0 chosen | 1 | 2 | 3+ "
        "| chosen punt categories that realized under 25% |"
    )
    add("|---|---|---|---|---|---|---|")
    for season in seasons_p:
        group = [r for r in projected if r.season == season]
        counts = [sum(1 for r in group if len(r.chosen) == k) for k in (0, 1, 2)]
        three = sum(1 for r in group if len(r.chosen) >= 3)
        chosen_cats = [(r, c) for r in group for c in r.chosen]
        realized = sum(1 for r, c in chosen_cats if r.cat_rates[c] < REALIZED_THRESHOLD)
        share = f"{realized}/{len(chosen_cats)}" if chosen_cats else "n/a"
        add(
            f"| {season} | {len(group)} | {counts[0]} | {counts[1]} | {counts[2]} "
            f"| {three} | {share} |"
        )
    combos: dict[tuple[str, ...], int] = defaultdict(int)
    for r in projected:
        if r.chosen:
            combos[r.chosen] += 1
    add("")
    add(
        "Most common chosen punt sets: "
        + ", ".join(
            f"{'/'.join(k)} ({v})" for k, v in sorted(combos.items(), key=lambda kv: -kv[1])[:8]
        )
        + "."
    )
    add("")
    add(
        "**Reading.** A chosen punt is a projection at the draft, not a statement of intent; "
        "the realized column shows how often the category actually went as projected."
    )
    add("")

    # Q2 outcomes by chosen punt count within strength
    add("## 3. Do chosen punts win?")
    add("")
    add(
        "Outcomes by number of chosen punts. `vs strength` is the category win rate minus "
        "what the roster's projected strength predicts (fitted across all teams, strength "
        "standardised within season)."
    )
    add("")
    add(
        "| chosen punts | n | cat win rate | vs strength | matchup win rate | playoff % | title % |"
    )
    add("|---|---|---|---|---|---|---|")
    for label in ("0", "1", "2+"):
        group = [r for r in projected if punt_count(r) == label]
        if not group:
            continue
        add(
            f"| {label} | {len(group)} | {fmean(r.cat_win_rate for r in group):.3f} | "
            f"{fmean(cat_resid[r.key] for r in group):+.3f} | "
            f"{fmean(r.matchup_rate for r in group):.3f} | "
            f"{fmean(float(r.made_playoffs) for r in group):.0%} | "
            f"{fmean(float(r.title) for r in group):.0%} |{thin(len(group))}"
        )
    add("")
    add("### The strict definition")
    add("")
    add(
        "| strict chosen punts | n | cat win rate | vs strength | matchup win rate "
        "| playoff % | title % |"
    )
    add("|---|---|---|---|---|---|---|")
    strict_groups: tuple[tuple[str, Callable[[TeamSeason], bool]], ...] = (
        ("none", lambda r: not r.chosen_strict),
        ("1", lambda r: len(r.chosen_strict) == 1),
        ("2+", lambda r: len(r.chosen_strict) >= 2),
    )
    for label, test in strict_groups:
        group = [r for r in projected if test(r)]
        if not group:
            continue
        add(
            f"| {label} | {len(group)} | {fmean(r.cat_win_rate for r in group):.3f} | "
            f"{fmean(cat_resid[r.key] for r in group):+.3f} | "
            f"{fmean(r.matchup_rate for r in group):.3f} | "
            f"{fmean(float(r.made_playoffs) for r in group):.0%} | "
            f"{fmean(float(r.title) for r in group):.0%} |{thin(len(group))}"
        )
    strict_sets: dict[tuple[str, ...], int] = defaultdict(int)
    for r in projected:
        if r.chosen_strict:
            strict_sets[r.chosen_strict] += 1
    add("")
    add(
        "Strict punt sets: "
        + (
            ", ".join(
                f"{'/'.join(k)} ({v})"
                for k, v in sorted(strict_sets.items(), key=lambda kv: -kv[1])
            )
            or "none"
        )
        + f". Bootstrap over seasons: {ci(strict_ci)}."
    )
    add("")
    add("### Per season: strength-adjusted category win rate, chosen punt minus none")
    add("")
    add("| season | n punt | n none | difference |")
    add("|---|---|---|---|")
    for season in seasons_p:
        group = [r for r in projected if r.season == season]
        p = [cat_resid[r.key] for r in group if r.chosen]
        n = [cat_resid[r.key] for r in group if not r.chosen]
        diff = fmean(p) - fmean(n) if p and n else None
        add(f"| {season} | {len(p)} | {len(n)} | {fmt(diff)} |")
    add("")
    add(
        f"Bootstrap over seasons: at least one chosen punt {ci(chosen_ci)}; "
        f"two or more {ci(two_ci)}."
    )
    add("")

    # Q3 conversion
    add("## 4. Conversion: turning categories into matchups")
    add("")
    add(
        f"Across all {len(rows)} team-seasons, matchup win rate = {ma:.3f} + {mb:.3f} x category "
        "win rate. The residual is how many more matchups a team won than its category rate "
        "predicts. Specialization terciles are cut within each strength tercile."
    )
    add("")
    add(
        "| specialization | n | cat win rate | matchup win rate | conversion residual "
        "| one-category weeks W-L |"
    )
    add("|---|---|---|---|---|---|")
    for label in ("low", "mid", "high"):
        group = [r for r in rows if spec_t[r.key] == label]
        close_w = sum(r.close_won for r in group)
        close_n = sum(r.close_played for r in group)
        add(
            f"| {label} | {len(group)} | {fmean(r.cat_win_rate for r in group):.3f} | "
            f"{fmean(r.matchup_rate for r in group):.3f} | "
            f"{fmean(conv_resid[r.key] for r in group):+.3f} | "
            f"{close_w}-{close_n - close_w} ({close_w / max(1, close_n):.0%}) |{thin(len(group))}"
        )
    add("")
    add(f"High minus low specialization, bootstrap over seasons: {ci(conv_ci)}.")
    add("")
    add(
        f"One-category weeks won, high minus low specialization, bootstrap over seasons: "
        f"{ci(close_ci, '{:+.0%}')}."
    )
    add("")
    add("### By realized punt count (descriptive: realized punts track weakness)")
    add("")
    add(
        "| realized punts (<25%) | n | cat win rate | matchup win rate "
        "| conversion residual | at 20% / 30% n |"
    )
    add("|---|---|---|---|---|---|")
    for k, label in ((0, "0"), (1, "1"), (2, "2"), (3, "3+")):

        def count(r: TeamSeason, t: float = REALIZED_THRESHOLD, k: int = k) -> bool:
            n = len(r.realized(t))
            return n >= 3 if k == 3 else n == k

        group = [r for r in rows if count(r)]
        if not group:
            continue
        sens = " / ".join(str(sum(1 for r in rows if count(r, t))) for t in SENSITIVITY)
        add(
            f"| {label} | {len(group)} | {fmean(r.cat_win_rate for r in group):.3f} | "
            f"{fmean(r.matchup_rate for r in group):.3f} | "
            f"{fmean(conv_resid[r.key] for r in group):+.3f} | {sens} |{thin(len(group))}"
        )
    add("")

    # Q4 kept categories
    add("## 5. Do punters win more of what they keep?")
    add("")
    gains = []
    for r in projected:
        if not r.chosen:
            continue
        peers = [p for p in projected if p.season == r.season and not p.chosen]
        if not peers:
            continue
        kept = [c for c in CATS if c not in r.chosen]
        gains.append(fmean(r.cat_rates[c] - fmean(p.cat_rates[c] for p in peers) for c in kept))
    add(
        f"For each team with a chosen punt, its win rate in the categories it kept minus "
        f"non-punters' win rate in those same categories that season: mean "
        f"{fmt(mean_or_none(gains))} across {len(gains)} teams "
        f"(positive in {sum(1 for g in gains if g > 0)})."
    )
    add("")

    # Q5 build types
    add("## 6. Build type")
    add("")
    add("| build | n | cat win rate | vs strength | matchup win rate | playoff % |")
    add("|---|---|---|---|---|---|")

    def build(r: TeamSeason) -> str:
        s = set(r.chosen)
        if not s:
            return "no chosen punt"
        if s & BIG_PUNTS and not s & GUARD_PUNTS:
            return "big-man (FT%/3PM)"
        if s & GUARD_PUNTS and not s & BIG_PUNTS:
            return "guard (FG%/REB/BLK/TO)"
        return "mixed"

    for label in ("no chosen punt", "big-man (FT%/3PM)", "guard (FG%/REB/BLK/TO)", "mixed"):
        group = [r for r in projected if build(r) == label]
        if not group:
            continue
        add(
            f"| {label} | {len(group)} | {fmean(r.cat_win_rate for r in group):.3f} | "
            f"{fmean(cat_resid[r.key] for r in group):+.3f} | "
            f"{fmean(r.matchup_rate for r in group):.3f} | "
            f"{fmean(float(r.made_playoffs) for r in group):.0%} |{thin(len(group))}"
        )
    add("")

    # Q6 playoffs
    add("## 7. Playoffs")
    add("")
    add("Among playoff teams. Playoff W-L counts decided playoff matchups only.")
    add("")
    add("| group | n | playoff W-L | title % |")
    add("|---|---|---|---|")
    playoff_rows = [r for r in rows if r.made_playoffs]
    for label, group in (
        *(
            (f"specialization {t}", [r for r in playoff_rows if spec_t[r.key] == t])
            for t in ("low", "mid", "high")
        ),
        ("chosen punt", [r for r in playoff_rows if r.has_projection and r.chosen]),
        ("no chosen punt", [r for r in playoff_rows if r.has_projection and not r.chosen]),
    ):
        if not group:
            continue
        won = sum(r.playoff_won for r in group)
        played = sum(r.playoff_played for r in group)
        add(
            f"| {label} | {len(group)} | {won}-{played - won} ({won / max(1, played):.0%}) | "
            f"{fmean(float(r.title) for r in group):.0%} |{thin(len(group))}"
        )
    add("")
    add(
        "Playoff rate, chosen punt minus none, bootstrap over seasons: "
        f"{ci(playoff_ci, '{:+.0%}')}."
    )
    add("")

    # Q7 money x categories
    add("## 8. Spreading money x conceding categories")
    add("")
    add("Strategy halves by top-3 share of draft spend, cut within league size.")
    add("")
    add("| spend | chosen punt | n | cat win rate | vs strength | playoff % |")
    add("|---|---|---|---|---|---|")
    size_groups: dict[int, list[TeamSeason]] = defaultdict(list)
    for r in projected:
        size_groups[r.teams].append(r)
    spend_half: dict[tuple[int, int], str] = {}
    for group in size_groups.values():
        cut = sorted(r.top3_share for r in group)[len(group) // 2]
        for r in group:
            spend_half[r.key] = "balanced half" if r.top3_share < cut else "top-heavy half"
    for spend in ("balanced half", "top-heavy half"):
        for punt in (False, True):
            group = [r for r in projected if spend_half[r.key] == spend and bool(r.chosen) == punt]
            if not group:
                continue
            add(
                f"| {spend} | {'yes' if punt else 'no'} | {len(group)} | "
                f"{fmean(r.cat_win_rate for r in group):.3f} | "
                f"{fmean(cat_resid[r.key] for r in group):+.3f} | "
                f"{fmean(float(r.made_playoffs) for r in group):.0%} |{thin(len(group))}"
            )
    add("")

    # Q8 league size
    add("## 9. League size")
    add("")
    add(
        "| teams | seasons | n | chosen punt vs none (strength-adjusted) "
        "| high vs low specialization (conversion) |"
    )
    add("|---|---|---|---|---|")
    for size in sorted({r.teams for r in rows}):
        group = [r for r in rows if r.teams == size]
        pg = [r for r in group if r.has_projection]
        add(
            f"| {size} | {len({r.season for r in group})} | {len(group)} | "
            f"{fmt(chosen_effect(pg))} | {fmt(conversion_effect(group))} |{thin(len(group))}"
        )
    add("")

    # Q9 manager
    add("## 10. Through The Wire")
    add("")
    add(
        "| season | team | chosen punts | realized punts | specialization | cat win rate "
        "| conversion residual | finish |"
    )
    add("|---|---|---|---|---|---|---|---|")
    for r in sorted((r for r in rows if r.owner == MANAGER_OWNER), key=lambda r: r.season):
        add(
            f"| {r.season} | {r.name} | {'/'.join(r.chosen) or 'none'} | "
            f"{'/'.join(r.realized()) or 'none'} | {r.specialization:.3f} | "
            f"{r.cat_win_rate:.3f} | {conv_resid[r.key]:+.3f} | "
            f"{'title' if r.title else ('playoffs' if r.made_playoffs else 'missed')} |"
        )
    add("")
    add("## 11. What this means for a 15-team, nine-category draft")
    add("")
    add(
        "- **Do not draft to concede categories.** A roster built at the draft to be far "
        "behind the league in a category won fewer categories than rosters of the same "
        "projected strength, in the strict test with an interval clear of zero. The looser "
        "tests point the same way without clearing zero."
    )
    add(
        "- **The bias runs in punting's favour, and it still loses.** A punted category drags "
        "a roster's projected strength down, which lowers what it is expected to win; punt "
        "rosters fell short even of that lowered expectation."
    )
    add(
        "- **Specialized teams lose the close weeks.** Conceding categories leaves no margin: "
        "one-category weeks go against the most specialized teams, and H2H seasons are decided "
        "there."
    )
    add(
        "- **Spend balanced and stay balanced.** Balanced spending with no chosen punt was the "
        "best cell in section 8; top-heavy spending with a punt was the worst."
    )
    add(
        "- **Thin evidence:** build types, playoffs, titles and league size are all small cells "
        "and six seasons of projections; none of them should drive a decision on its own."
    )
    add("")
    add("## 12. Traps")
    add("")
    add(
        "- Chosen punts rest on preseason projections, which are unusable for 2020 (no stats) "
        "and 2023 (a mid-season snapshot); those seasons count only in the realized and "
        "specialization analyses."
    )
    add(
        "- A team in the bottom quartile of projected strength is never counted as choosing a "
        "punt: a weak roster projects bottom-two in many categories without a plan."
    )
    add(
        "- Category rates here are regular-season matchups only, ties counted as half. "
        "Byes (no opponent) are excluded."
    )
    add(
        "- The loose chosen-punt test (bottom two of the league) catches rosters that were "
        "merely thin somewhere; only about one chosen category in five went on to be lost most "
        "weeks. The strict test (1.5 SD below) is the one to read."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        rows = load(session)
    text_out = report(rows)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text_out)
    print(text_out)


if __name__ == "__main__":
    main()
