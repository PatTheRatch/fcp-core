#!/usr/bin/env python3
"""What the projected standings scored when a played season was replayed.

Usage:
    python scripts/projected_calibration.py --season 2026
    python scripts/projected_calibration.py --season 2026 --sims 2000 --json out.json

Read-only: it prints, it writes nothing. For each regular-season matchup
period it rebuilds the projection twice -- on the morning the period began,
and again at its midpoint -- with **only what was on record that day**, and
scores it against what happened:

* the Brier score and a reliability table for the per-category probabilities,
  broken out by how far ahead the week was;
* the hit rate on matchup winners, against what a coin would give;
* the mean absolute error of each team's projected final category record,
  made at the season's quarter, half and three-quarter marks;
* the playoff-odds calibration: teams given X% made it Y%.

WHAT "ONLY WHAT WAS ON RECORD" MEANS HERE

Three things, each of which is a leak if it is left out.

1. The league's weekly spreads are read with `before=season` and with the era
   adjustment off, so the basis is strictly earlier seasons. Left as they
   are, `category_distributions` would measure the spread partly on the very
   weeks being predicted -- the one place `app.inseason.projected` is not
   proof against the future on a replay, and its docstring says so.
2. Availability comes from the NBA's own injury reports as they stood at ten
   o'clock Eastern that morning (`app.injuries.statuses_as_of`, the
   point-in-time rule of docs/injuries.md). A played season has no listener
   snapshots -- the listener only ever runs for the season in progress -- so
   without this every man reads as fit, which is the one thing a replay of a
   played season can put right. A report line is about one game, so a man
   Out loses exactly that scoring period.
3. Everything else is `app.inseason.projected`'s own, which is keyed on
   `today` throughout.

The result of the published run is at the top of docs/projected_record.md,
and the sentence the pages print is
`app.inseason.projected_calibration.CALIBRATION_NOTE`. The variance model is
not tuned on what comes out of here: if the numbers are poor the doc says so
and proposes the change, which is a separate decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# Run by path, so `scripts/` is on sys.path and the repo root is not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    DailyLineupSlot,
    League,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Team,
)
from app.db.session import make_engine, make_session_factory
from app.draft.targets import category_distributions
from app.injuries import morning_of, statuses_as_of
from app.inseason.projected import Projection, final_table, project_standings
from app.pickups.judge import banked_record
from app.pickups.state import SeasonCalendar, season_calendar

#: ESPN's league id for Full Court Press, named rather than assumed.
LEAGUE_ID = 3853870

#: Reliability buckets for a probability, as the table prints them.
BUCKETS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)

#: Where the "made at the halfway mark" sentence is measured: the share of
#: the regular season each mark sits at.
MARKS = {"quarter": 0.25, "half": 0.5, "three-quarter": 0.75}

#: A day past the end of any season, for "the record as it finally stood".
AFTER_THE_SEASON = 10_000


@dataclass
class Bucket:
    """One row of a reliability table: how many, how sure, how often."""

    n: int = 0
    predicted: float = 0.0
    happened: float = 0.0

    def add(self, p: float, outcome: float) -> None:
        self.n += 1
        self.predicted += p
        self.happened += outcome


def _bucket(p: float) -> int:
    for index in range(len(BUCKETS) - 1):
        if BUCKETS[index] <= p < BUCKETS[index + 1]:
            return index
    return len(BUCKETS) - 2


def _league_season(session: Session, season: int) -> LeagueSeason:
    found = session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == LEAGUE_ID, LeagueSeason.season == season)
    )
    if found is None:
        raise SystemExit(f"no stored season {season} for league {LEAGUE_ID}")
    return found


def _regular_periods(session: Session, league_season: LeagueSeason) -> list[MatchupPeriod]:
    return list(
        session.scalars(
            select(MatchupPeriod)
            .where(
                MatchupPeriod.league_season_id == league_season.id,
                MatchupPeriod.is_playoff.is_(False),
                MatchupPeriod.first_scoring_period.is_not(None),
            )
            .order_by(MatchupPeriod.period)
        ).all()
    )


@dataclass(frozen=True)
class Truth:
    """What really happened, keyed the way the forecast can be looked up.

    `categories` is ESPN's own result per category -- a win is one, a tie a
    half -- which is the quantity `app.scoring.league.category_record`
    averages, so the forecast and the outcome are counted the same way.
    """

    #: (matchup period, espn team id) -> (matchup id, this team's row id, won)
    sides: dict[tuple[int, int], tuple[int, int, bool | None]]
    #: (matchup id, team row id, abbreviation) -> 1.0, 0.5 or 0.0
    categories: dict[tuple[int, int, str], float]
    #: Each team's final category record, won only.
    final_categories: dict[int, float]
    #: Who really finished in the playoff places.
    playoff_field: frozenset[int]


def _truth(session: Session, league_season: LeagueSeason) -> Truth:
    espn = {
        int(team.id): int(team.espn_team_id)
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }
    sides: dict[tuple[int, int], tuple[int, int, bool | None]] = {}
    for period, matchup_id, home, away, winner in session.execute(
        select(
            MatchupPeriod.period,
            Matchup.id,
            Matchup.home_team_id,
            Matchup.away_team_id,
            Matchup.winner,
        )
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(MatchupPeriod.league_season_id == league_season.id)
    ).all():
        decided = str(winner) in ("HOME", "AWAY")
        sides[(int(period), espn[int(home)])] = (
            int(matchup_id),
            int(home),
            (str(winner) == "HOME") if decided else None,
        )
        if away is not None:
            sides[(int(period), espn[int(away)])] = (
                int(matchup_id),
                int(away),
                (str(winner) == "AWAY") if decided else None,
            )
    categories = {
        (int(matchup), int(team), str(abbreviation)): (
            1.0 if result == "WIN" else 0.5 if result == "TIE" else 0.0
        )
        for matchup, team, abbreviation, result in session.execute(
            select(
                MatchupTeamStat.matchup_id,
                MatchupTeamStat.team_id,
                MatchupTeamStat.abbreviation,
                MatchupTeamStat.result,
            )
            .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
            .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
            .where(
                MatchupPeriod.league_season_id == league_season.id,
                MatchupTeamStat.league_season_category_id.is_not(None),
                MatchupTeamStat.result.is_not(None),
            )
        ).all()
    }
    final_categories = {
        espn_id: banked_record(session, league_season, espn_id, AFTER_THE_SEASON)[0]
        for espn_id in espn.values()
    }
    table = final_table(session, league_season, AFTER_THE_SEASON)
    field = int(league_season.playoff_team_count or 0)
    return Truth(
        sides=sides,
        categories=categories,
        final_categories=final_categories,
        playoff_field=frozenset(table[:field]),
    )


def _roster_ids(session: Session, league_season: LeagueSeason) -> list[int]:
    """Every player any team of this season ever held, for the injury read."""
    return [
        int(player_id)
        for player_id in session.scalars(
            select(DailyLineupSlot.player_id)
            .join(Team, Team.id == DailyLineupSlot.team_id)
            .where(Team.league_season_id == league_season.id)
            .distinct()
        ).all()
    ]


def _ruled_out(
    session: Session,
    calendar: SeasonCalendar | None,
    player_ids: list[int],
    day: int,
) -> dict[int, set[int]]:
    """The scoring periods the NBA had these men out of, as of that morning."""
    if calendar is None or not player_ids:
        return {}
    at = morning_of(calendar.date_of(day))
    out: dict[int, set[int]] = {}
    for player_id, status in statuses_as_of(session, player_ids, at).items():
        if status.ruled_out:
            out.setdefault(player_id, set()).add(calendar.scoring_period_on(status.game_date))
    return out


def run(session: Session, season: int, sims: int, sigma_scale: float = 1.0) -> dict[str, Any]:
    """One replay. `sigma_scale` widens every weekly spread by a factor.

    It exists to price a **proposed** change without making it. The shipped
    model is scale 1.0 -- `app.pickups.stream.head_to_head` on the league's
    own measured spreads -- and it stays 1.0 whatever this run says; a
    variance model tuned on the run that scores it is not a calibration. The
    interesting factor is sqrt(2), which is what the spread of the difference
    between two independent team totals would be, and the doc reports what it
    would have scored beside what the shipped model did.
    """
    league_season = _league_season(session, season)
    calendar = season_calendar(session, season)
    periods = _regular_periods(session, league_season)
    if not periods:
        raise SystemExit(f"season {season} has no regular-season matchup periods")
    distributions = category_distributions(
        session, league_season, before=season, adjust_for_era=False
    )
    if sigma_scale != 1.0:
        distributions = [replace(one, spread=one.spread * sigma_scale) for one in distributions]
    truth = _truth(session, league_season)
    everyone = _roster_ids(session, league_season)

    category_buckets: dict[int, Bucket] = defaultdict(Bucket)
    lead_squares: dict[int, list[float]] = defaultdict(list)
    winner_hits: dict[int, list[int]] = defaultdict(list)
    playoff_buckets: dict[int, Bucket] = defaultdict(Bucket)
    marks: dict[str, list[float]] = {name: [] for name in MARKS}
    at_marks = {
        name: periods[min(len(periods) - 1, max(0, round(len(periods) * share) - 1))].period
        for name, share in MARKS.items()
    }
    checkpoints = 0

    for period in periods:
        first = int(period.first_scoring_period or 0)
        last = int(period.final_scoring_period or first)
        for at_day in (first, first + (last - first) // 2):
            report = project_standings(
                session,
                league_season,
                at_day,
                distributions=distributions,
                n_sims=sims,
                unavailable=_ruled_out(session, calendar, everyone, at_day),
            )
            checkpoints += 1
            _score(
                report,
                truth,
                category_buckets=category_buckets,
                lead_squares=lead_squares,
                winner_hits=winner_hits,
                playoff_buckets=playoff_buckets,
            )
            if at_day == first:
                for name, at_period in at_marks.items():
                    if int(period.period) == at_period:
                        marks[name] = [
                            abs(one.projected_record[0] - truth.final_categories[one.team_id])
                            for one in report.teams
                        ]

    squares = [value for values in lead_squares.values() for value in values]
    return {
        "season": season,
        "sims": sims,
        "sigma_scale": sigma_scale,
        "checkpoints": checkpoints,
        "basis": "weekly spreads from seasons before this one, era adjustment off; "
        "availability from the NBA's own injury reports as of 10am Eastern that day",
        "n": len(squares),
        "brier": sum(squares) / len(squares) if squares else None,
        "brier_by_lead": {
            lead: sum(values) / len(values) for lead, values in sorted(lead_squares.items())
        },
        "brier_by_lead_n": {lead: len(values) for lead, values in sorted(lead_squares.items())},
        "categories": _reliability(category_buckets),
        "winners": {
            lead: {"n": len(hits), "hit": sum(hits) / len(hits)}
            for lead, hits in sorted(winner_hits.items())
            if hits
        },
        "winners_all": _hit_rate([hit for hits in winner_hits.values() for hit in hits]),
        "marks": {
            name: {
                "period": at_marks[name],
                "n": len(values),
                "mean_error": sum(values) / len(values) if values else None,
                "worst": max(values) if values else None,
            }
            for name, values in marks.items()
        },
        "playoff_odds": _reliability(playoff_buckets),
    }


def _hit_rate(hits: list[int]) -> dict[str, Any]:
    return {"n": len(hits), "hit": sum(hits) / len(hits) if hits else None}


def _score(
    report: Projection,
    truth: Truth,
    *,
    category_buckets: dict[int, Bucket],
    lead_squares: dict[int, list[float]],
    winner_hits: dict[int, list[int]],
    playoff_buckets: dict[int, Bucket],
) -> None:
    """One checkpoint's forecast against the season's record."""
    for team in report.teams:
        playoff_buckets[_bucket(team.playoff_odds)].add(
            team.playoff_odds, 1.0 if team.team_id in truth.playoff_field else 0.0
        )
        for lead, week in enumerate(team.weeks):
            if week.on_bye:
                continue
            found = truth.sides.get((week.period, team.team_id))
            if found is None:
                continue
            matchup_id, row_id, won = found
            for abbreviation, p in week.probabilities.items():
                outcome = truth.categories.get((matchup_id, row_id, abbreviation))
                if outcome is None:
                    continue
                category_buckets[_bucket(p)].add(p, outcome)
                lead_squares[lead].append((p - outcome) ** 2)
            if won is None or not week.probabilities:
                continue
            # Both sides of a matchup are scored, so each one is counted
            # twice and the hit rate is unchanged by it: a correct call on
            # one side is a correct call on the other.
            predicted = week.expected_wins > len(week.probabilities) / 2.0
            winner_hits[lead].append(1 if predicted == won else 0)


def _reliability(buckets: dict[int, Bucket]) -> list[dict[str, Any]]:
    return [
        {
            "from": BUCKETS[index],
            "to": min(1.0, BUCKETS[index + 1]),
            "n": buckets[index].n,
            "predicted": buckets[index].predicted / buckets[index].n,
            "happened": buckets[index].happened / buckets[index].n,
        }
        for index in sorted(buckets)
        if buckets[index].n
    ]


def _print(result: dict[str, Any]) -> None:
    print(f"PROJECTED STANDINGS, replayed on {result['season']}")
    print(f"  {result['checkpoints']} checkpoints, {result['sims']} simulated seasons each")
    print(f"  basis: {result['basis']}")
    if result["sigma_scale"] != 1.0:
        print(
            f"  DIAGNOSTIC RUN: spreads widened by {result['sigma_scale']:.4f}. "
            "Not the shipped model."
        )
    print()
    brier = result["brier"]
    print(f"BRIER, per-category probabilities: {brier:.4f} over {result['n']} of them")
    print("  weeks ahead        n     Brier")
    counts = result["brier_by_lead_n"]
    for lead, value in result["brier_by_lead"].items():
        print(f"  {lead:>11}   {counts[lead]:>7}    {value:.4f}")
    print()
    print("RELIABILITY, per category: predicted against what happened")
    print("  bucket          n    predicted   happened")
    for row in result["categories"]:
        print(
            f"  {row['from']:.1f}-{row['to']:.1f}   {row['n']:>8}      "
            f"{row['predicted']:.3f}      {row['happened']:.3f}"
        )
    print()
    both = result["winners_all"]
    print(f"MATCHUP WINNERS: {both['hit']:.3f} over {both['n']} team-weeks (a coin is 0.500)")
    print("  weeks ahead        n   hit rate")
    for lead, row in result["winners"].items():
        print(f"  {lead:>11}   {row['n']:>8}      {row['hit']:.3f}")
    print()
    print("PROJECTED FINAL CATEGORY RECORD, mean absolute error, in categories")
    for name, row in result["marks"].items():
        if row["mean_error"] is None:
            continue
        print(
            f"  {name:<14} made at period {row['period']:>2}   "
            f"mean {row['mean_error']:.2f}   worst {row['worst']:.2f}   n {row['n']}"
        )
    print()
    print("PLAYOFF ODDS: teams given X% made it Y%")
    print("  bucket          n    predicted   happened")
    for row in result["playoff_odds"]:
        print(
            f"  {row['from']:.1f}-{row['to']:.1f}   {row['n']:>8}      "
            f"{row['predicted']:.3f}      {row['happened']:.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--sims", type=int, default=2000)
    parser.add_argument(
        "--sigma-scale",
        type=float,
        default=1.0,
        help="Widen every weekly spread by this factor. A diagnostic, never shipped.",
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as session:
        result = run(session, args.season, args.sims, args.sigma_scale)
    _print(result)
    if args.json is not None:
        args.json.write_text(json.dumps(result, indent=1))
        print(f"\nwritten to {args.json}")


if __name__ == "__main__":
    main()
