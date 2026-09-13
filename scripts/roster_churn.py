#!/usr/bin/env python3
"""Roster churn analysis for the Full Court Press league (ESPN 3853870).

Answers three questions about how much a draft actually matters:

1. What share of a team's drafted roster is still on that team at season end?
2. When during the season does the roster start to look meaningfully different
   from the drafted one?
3. Does that vary by owner?

Method
------
Baseline is `draft_picks` -- this is a redraft auction league (keeper_count=0
for all eight seasons), so every drafted player starts the season on the team
that paid for him, and there is no carry-over to confuse the denominator.

The instrument is `daily_lineup_slots`, which records, for every team and every
day of the season, every player held. That is one row per (team, day, player)
and is continuous for all eight seasons. `roster_slots` is deliberately NOT
used as the instrument: it only has bookend snapshots for 2019-2024 (period 1
and sometimes the last period) and would silently understate churn in exactly
the years we care about. It is still dense for 2025/2026, but counting from one
source everywhere keeps the seasons comparable.

A player counts as "held" on a day if he appears in that team's daily lineup
slots for that day. Slot is not filtered on: bench and IR players are on the
roster, and treating them as gone would confuse "churn" with "did not start".

"Meaningfully different" is reported at several thresholds rather than one, so
the reader can pick. The headline threshold is 50%.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL is None:
    raise SystemExit("DATABASE_URL is not set")

# psycopg wants a plain postgres:// URL, not SQLAlchemy's +psycopg form.
DSN = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")

#: Fractions of the drafted roster still held at which we report a date.
THRESHOLDS = (0.75, 0.50, 0.25)


@dataclass
class SeasonTeam:
    """One team's drafted roster and the days it was observed."""

    season: int
    team_id: int
    team_name: str
    owner: str
    #: True when the team has more than one ESPN owner. The roster is still
    #: attributed to the primary owner, but flagging it keeps a co-managed
    #: team from reading as a clean single-owner result.
    co_owned: bool = False
    drafted: set[int] = field(default_factory=set)
    #: day (scoring period) -> set of player ids held that day
    by_day: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    #: players ever held on any day
    ever_held: set[int] = field(default_factory=set)

    @property
    def days(self) -> list[int]:
        return sorted(self.by_day)

    def held_on(self, day: int) -> set[int]:
        return self.by_day.get(day, set())

    def retained_on(self, day: int) -> set[int]:
        """Drafted players still held on `day`."""
        return self.drafted & self.held_on(day)

    def retention(self, day: int) -> float:
        if not self.drafted:
            return float("nan")
        return len(self.retained_on(day)) / len(self.drafted)

    def first_day_below(self, fraction: float) -> int | None:
        """First day after which retention NEVER recovers above `fraction`.

        NOT the first day that dips below it. Retention is not monotonic:
        players are dropped and re-added, IL stashes return, and a naive
        first-crossing measures the first wobble rather than durable change.
        Verified against team "Foxes ShutUpNDribble" 2023, whose retention
        walks 13-12-11-12-11-8-7-8-7-8 on its way down.

        Returns the last day the roster was still above the threshold, so the
        threshold is honestly reported as "held through here, gone after".
        """
        days = self.days
        if not days:
            return None
        #: Days where retention is at or above the threshold.
        ok = [d for d in days if self.retention(d) >= fraction]
        if not ok:
            return days[0]
        if len(ok) == len(days):
            return None
        return ok[-1]


def load(conn: psycopg.Connection) -> dict[int, list[SeasonTeam]]:
    """Read drafts, rosters and owners into per-season team records."""
    seasons: dict[int, list[SeasonTeam]] = {}

    with conn.cursor() as cur:
        # Teams, with owner names attached. An owner can hold more than one
        # team across seasons, which is the point of the by-owner question.
        cur.execute(
            """
            SELECT ls.season, t.id, t.name, ls.id AS league_season_id
            FROM league_seasons ls
            JOIN teams t ON t.league_season_id = ls.id
            ORDER BY ls.season, t.espn_team_id
            """
        )
        team_rows = cur.fetchall()

        cur.execute(
            """
            SELECT tw.team_id,
                   COALESCE(o.display_name,
                            TRIM(CONCAT_WS(' ', o.first_name, o.last_name))),
                   o.espn_owner_id
            FROM team_owners tw
            JOIN owners o ON o.id = tw.owner_id
            ORDER BY tw.team_id, o.espn_owner_id
            """
        )
        #: A team can have co-owners: five do across these eight seasons.
        #: Pooling them would split one team's season across two "owners" and
        #: corrupt every per-owner average, so attribute the roster to the
        #: primary (first-listed) owner and mark the co-owned teams.
        owners: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for team_id, name, guid in cur.fetchall():
            if name:
                owners[team_id].append((guid, name))

        def primary(team_id: int) -> tuple[str, bool]:
            named = owners.get(team_id, [])
            if not named:
                return "UNKNOWN", False
            return named[0][1], len(named) > 1

        by_season: dict[int, dict[int, SeasonTeam]] = defaultdict(dict)
        season_ids: dict[int, int] = {}
        for season, team_id, team_name, league_season_id in team_rows:
            season_ids[season] = league_season_id
            owner_name, co_owned = primary(team_id)
            by_season[season][team_id] = SeasonTeam(
                season=season,
                team_id=team_id,
                team_name=team_name,
                owner=owner_name,
                co_owned=co_owned,
            )

        # The draft baseline.
        cur.execute(
            """
            SELECT ls.season, dp.team_id, dp.player_id
            FROM league_seasons ls
            JOIN draft_picks dp ON dp.league_season_id = ls.id
            WHERE dp.team_id IS NOT NULL
            """
        )
        for season, team_id, player_id in cur.fetchall():
            if team_id in by_season[season]:
                by_season[season][team_id].drafted.add(player_id)

        # The observation instrument.
        cur.execute(
            """
            SELECT ls.season, d.team_id, d.scoring_period, d.player_id
            FROM league_seasons ls
            JOIN teams t ON t.league_season_id = ls.id
            JOIN daily_lineup_slots d ON d.team_id = t.id
            ORDER BY ls.season, d.scoring_period
            """
        )
        for season, team_id, period, player_id in cur.fetchall():
            team = by_season[season].get(team_id)
            if team is None:
                continue
            team.by_day[period].add(player_id)
            team.ever_held.add(player_id)

    for season, teams in by_season.items():
        seasons[season] = sorted(teams.values(), key=lambda t: t.team_id)
    return seasons


def fmt_pct(value: float) -> str:
    return "n/a" if value != value else f"{value * 100:5.1f}%"


def main() -> None:
    with psycopg.connect(DSN) as conn:
        seasons = load(conn)

    # ---------------------------------------------------------------- overview
    print("=" * 78)
    print("ROSTER CHURN -- Full Court Press, ESPN 3853870 (redraft auction)")
    print("=" * 78)
    print()
    print("How much of the drafted roster is still held at the END of the season.")
    print("Baseline: draft_picks. Instrument: daily_lineup_slots.")
    print()

    header = (
        f"{'season':>7} {'teams':>6} {'end ret%':>9} {'median':>8} "
        f"{'min':>7} {'max':>7} {'players used':>13}"
    )
    print(header)
    print("-" * len(header))

    for season in sorted(seasons):
        teams = seasons[season]
        ends = [t.retention(t.days[-1]) for t in teams if t.drafted and t.days]
        ends = [e for e in ends if e == e]
        if not ends:
            print(f"{season:>7} {len(teams):>6}   (no data)")
            continue
        ends.sort()
        median = ends[len(ends) // 2]
        used = sum(len(t.ever_held) for t in teams) / len(teams)
        print(
            f"{season:>7} {len(teams):>6} {fmt_pct(sum(ends) / len(ends)):>9} "
            f"{fmt_pct(median):>8} {fmt_pct(ends[0]):>7} {fmt_pct(ends[-1]):>7} "
            f"{used:>13.1f}"
        )

    # ------------------------------------------------------------ churn curve
    print()
    print("=" * 78)
    print("WHEN DOES THE ROSTER BECOME DIFFERENT?")
    print("=" * 78)
    print()
    print("Median point in the season at which retention drops below a")
    print("threshold and never recovers. Parentheses give how many teams ever")
    print("crossed it, which is the denominator -- a team still above 25% at")
    print("season end simply never crossed, and is not a missing value.")
    print("Only seasons with continuous daily coverage can answer this:")
    print("2019-2024 observe the season's bookends but not every day between.")
    print()

    header = (
        f"{'season':>7} {'coverage':>10} "
        + " ".join(f"{int(th * 100):>3}%".rjust(13) for th in THRESHOLDS)
    )
    print(header)
    print("-" * len(header))

    #: A season is "continuous" if teams are observed on a majority of the
    #: days between their first and last observation.
    coverage: dict[int, bool] = {}

    for season in sorted(seasons):
        teams = [t for t in seasons[season] if t.drafted and t.days]
        if not teams:
            continue
        all_days = sorted({d for t in teams for d in t.days})
        span = all_days[-1] - all_days[0] + 1
        density = len(all_days) / span if span else 0
        continuous = density > 0.5
        coverage[season] = continuous

        label = f"{density * 100:.0f}% dense" if not continuous else "daily"
        cells = []
        for th in THRESHOLDS:
            hits: list[int] = [
                h for h in (t.first_day_below(th) for t in teams) if h is not None
            ]
            if not hits or not continuous:
                cells.append(f"{'--':>13}")
                continue
            first = all_days[0]
            shares = sorted((h - first) / span for h in hits)
            median = shares[len(shares) // 2]
            #: Report how many teams crossed at all: a column averaged over
            #: only the teams that crossed is a different population per
            #: threshold and can invert, which is exactly the trap here.
            cells.append(f"{median * 100:>7.0f}% ({len(hits):>2}/{len(teams)})")

        print(f"{season:>7} {label:>10} " + " ".join(cells))

    # --------------------------------------------------------------- by owner
    print()
    print("=" * 78)
    print("DOES IT VARY BY OWNER?")
    print("=" * 78)
    print()
    print("End-of-season retention and players used, per owner, pooled across")
    print("every season they played in. Owners with a single season are marked.")
    print()

    by_owner: dict[str, list[SeasonTeam]] = defaultdict(list)
    for season in seasons:
        for team in seasons[season]:
            by_owner[team.owner].append(team)

    rows = []
    for owner, teams in by_owner.items():
        ends = [t.retention(t.days[-1]) for t in teams if t.drafted and t.days]
        ends = [e for e in ends if e == e]
        if not ends:
            continue
        rows.append(
            (
                owner,
                len(teams),
                sum(ends) / len(ends),
                min(ends),
                max(ends),
                sum(len(t.ever_held) for t in teams) / len(teams),
                sum(1 for t in teams if t.co_owned),
            )
        )
    rows.sort(key=lambda r: r[2], reverse=True)

    header = (
        f"{'owner':<24} {'seasons':>8} {'ret%':>7} {'min':>7} {'max':>7} "
        f"{'players/szn':>12} {'co':>3}"
    )
    print(header)
    print("-" * len(header))
    for owner, n, avg, lo, hi, used, co in rows:
        mark = " *" if n == 1 else ""
        print(
            f"{owner[:23]:<24} {n:>8} {fmt_pct(avg):>7} {fmt_pct(lo):>7} "
            f"{fmt_pct(hi):>7} {used:>12.1f} {co:>3}{mark}"
        )
    print()
    print("  co = seasons managed jointly with a co-owner; the roster is")
    print("       attributed to the primary owner, so the number is that")
    print("       team's, not that person's alone")
    print("  *  = single season -- not enough history to separate owner from luck")

    # --------------------------------------------------------- per-team detail
    detail_path = "/tmp/fcp_roster_churn_detail.csv"
    with open(detail_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "season",
                "team_id",
                "team_name",
                "owner",
                "drafted",
                "retained_end",
                "retention_end",
                "players_used",
                "days_observed",
                "day_below_75",
                "day_below_50",
                "day_below_25",
            ]
        )
        for season in sorted(seasons):
            for team in seasons[season]:
                if not team.drafted or not team.days:
                    continue
                last = team.days[-1]
                writer.writerow(
                    [
                        team.season,
                        team.team_id,
                        team.team_name,
                        team.owner,
                        len(team.drafted),
                        len(team.retained_on(last)),
                        f"{team.retention(last):.4f}",
                        len(team.ever_held),
                        len(team.days),
                        team.first_day_below(0.75) or "",
                        team.first_day_below(0.50) or "",
                        team.first_day_below(0.25) or "",
                    ]
                )
    print()
    print(f"Per-team detail written to {detail_path}")
    print()
    print("Coverage note:")
    for season in sorted(coverage):
        state = "daily (usable)" if coverage[season] else "BOOKENDS ONLY"
        print(f"  {season}  {state}")


if __name__ == "__main__":
    main()
