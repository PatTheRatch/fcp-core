"""What a streamed roster lane is worth, against a place held by one man.

Every roster place in this project is priced in the code as if one man holds
it. The floor under what a place gives back is
`app.pickups.judge.TYPICAL_PICKUP` = 0.06 categories a week: the median
return of a *single* executed add that was kept, measured by
`app.scoring.replacement` and reproduced by this script as its first check.
A place a manager streams -- a new man in it whenever the one there has no
game -- is a different asset from a place held by one man, and nothing has
measured it. This does.

WHAT IS A LANE

ESPN slot ids are lineup positions (PG/SG/SF/PF/C/G/F/UT/BE), not stable
places: the `UT` row is a different place on every day of the period. So the
places are defined by *occupancy*, per team and matchup period, from
`daily_lineup_slots`:

  * the unit is one (team, matchup period) -- a **team-period**;
  * **held** men are those rostered on every day of the period. One held man
    is one held place, and it is the place `TYPICAL_PICKUP` was measured for;
  * every other man rostered that period is a **streamed** man. Held and
    streamed men partition the period's roster-days: no man is in both, so no
    line is counted twice;
  * the number of **lanes** is the largest number of streamed men rostered on
    any single day of the period: `lanes_by_occupancy`. It is how many places
    were being rotated at once, at the peak, and it is the headline
    definition. The alternative, `lanes_by_arrival`, counts runs of streamed
    men whose arrivals are separated by no more than a day -- a place handed
    from man to man;
  * a lane's **production** is every streamed man's started lines in that
    period, scored as one line, so the lane's number is what the rotated
    place returned rather than the sum of its several men's separate values
    (which would double-count their overlap in the team's week).

A NOTE ON WHAT A LANE IS NOT

A lane here is a *man who arrived mid-period*, and men within one place
overlap in time. So "the number of lanes" is how many rotating men a team
used at its peak, not how many distinct places it rotated, and the data
cannot cut one place's rotation from the next: a team that ran four streamed
men through one lineup spot and a team that ran four places are counted the
same. Step 4 reports what that costs the lane-one/lane-two question and gives
the proxy that survives it. What the definition *does* support, cleanly, is
the headline: the production of the rotating men against the production of
the men who were held and against the floor.

The alternative definition (`--days-definition`, step 6) counts lanes by
arrival. The two agree on most team-periods and disagree where a manager
dropped and re-added men inside a period.

THE CURRENCY

Categories a week, through the lens the code already uses, so the number can
go straight into `TYPICAL_PICKUP`: a man's started lines for the team in a
matchup period, scored by `app.scoring.value.marginal` against that season's
opponent distributions for that period's length -- exactly the call
`SeasonBook.value` makes, which is what `pickup_values` measures. Percentages
are rebuilt from made over attempted inside the line, so a volume shooter who
hurts FG% scores below zero in that category, which is what nine-category
scoring says.

Periods are not all seven days: a six-day opener, fourteen-day All-Star
fortnights, 2020's ninety-eight-day COVID block, a seventeen-day final. Every
figure is normalised to a seven-day week by the period's *stored* window
(`matchup_periods.first_scoring_period` to `final_scoring_period`), never
pooled across lengths.

THE HELD BASELINE

Two baselines. (a) The team's **held 13th man** -- its lowest-valued held man,
the last place on the roster -- because that is the place a manager would
stream: the question is what the roster's *worst* held place returns against
the same team in the same period, through the same lens. (b) `TYPICAL_PICKUP`
= 0.06, the floor the code already puts under every place.

CHARACTER (step 5)

Percentages and turnovers need a baseline team to be diluted against. All
team-periods of a season are pooled into that season's **average started
week** for an ordinary-length period (`app.scoring.league.average_team_line`);
the lane's line is added to it and the category broken out with
`per_category_marginal`, so the figure is "what this lane did to an ordinary
roster's week" -- the quantity a punt-FT% roster and a chase-FT% roster each
read differently.

REPRODUCE

    cd /home/aisha/fcp-core-lane
    PYTHONPATH=. /opt/fcp-core/.venv/bin/python scripts/streaming_lane.py

(`DATABASE_URL` is read from the environment, or from `.env` in the worktree
root when it is not set. The house style sources `.env` first; this worktree's
`.env` cannot be sourced -- its `FCP_EMAIL_FROM` has unquoted angle brackets --
so the script reads the file itself.)

    --seasons 2024 2025 2026   run a subset of seasons only
    --days-definition          the alternative lane count (see above)
    --drop-2020                exclude the COVID season from the pools
    --detail /tmp/lane.csv     per-lane rows, to check a number by hand

Read-only: SELECTs only, no writes of any kind.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, median, quantiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    MatchupPeriod,
    Team,
    Transaction,
    TransactionItem,
)
from app.db.session import make_engine, make_session_factory
from app.draft.targets import CategoryDistribution
from app.scoring.league import average_team_line
from app.scoring.lines import CategoryLine
from app.scoring.replacement import ADD_TYPES, pickup_values
from app.scoring.season import SeasonBook
from app.scoring.value import marginal, per_category_marginal

#: Seasons with lineup days. 2027 has not been drafted, so it is refused.
MIN_SEASON = 2019
MAX_SEASON = 2026

#: The suspended COVID season. Flagged wherever it appears; `--drop-2020`
#: reports the headline without it (step 6).
COVID_SEASON = 2020

#: Days a week every rate is normalised to.
DAYS_A_WEEK = 7.0

#: The floor the code puts under a roster place (`app.pickups.judge`).
TYPICAL_PICKUP = 0.06

#: Adds a team gets per matchup period, the budget the lanes compete for: one
#: for each day of the period, spendable on any of its days
#: (`app.pickups.state.ADDS_PER_PERIOD_DAY`). ESPN refusing a move with
#: `FAILED_MATCHUPACQUISITIONLIMIT` is this limit biting.
ADDS_PER_PERIOD_DAY = 1

#: The categories step 5 reads separately: the two ratios a lane's extra volume
#: dilutes, and the count it adds. The other six are reported beside them.
CHARACTER = ("PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FG%", "FT%")


def dsn_from_environment() -> str:
    """The database URL, from the environment or `.env` in the worktree root."""
    url = os.environ.get("DATABASE_URL")
    if url is None:
        path = Path(__file__).resolve().parent.parent / ".env"
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if url is None:
        raise SystemExit("DATABASE_URL is not set and no .env was found")
    return url


@dataclass(frozen=True)
class Occupancy:
    """A team's roster in one matchup period: who, for how many days, when."""

    season: int
    team_id: int
    team_name: str
    period: int
    first: int
    final: int
    days: int
    #: Player id -> days of the period he was rostered.
    held_days: Mapping[int, int]
    #: Player id -> the first day of the period he appears on.
    first_day: Mapping[int, int]
    #: Player id -> days of the period he was in a starting slot.
    start_days: Mapping[int, int]

    @property
    def stored_days(self) -> int:
        """Days the period's stored window covers: the normaliser."""
        return self.final - self.first + 1

    @property
    def roster_size(self) -> int:
        """Men held on the period's first day: the team's place count."""
        return sum(1 for day in self.first_day.values() if day == self.first)

    def held_men(self) -> list[int]:
        """Men rostered on every day of the period."""
        return sorted(p for p in self.held_days if self.held_days[p] >= self.days)

    def streamed_men(self) -> list[int]:
        """Men rostered for only part of the period, ordered by arrival."""
        return sorted(
            (p for p in self.held_days if self.held_days[p] < self.days),
            key=lambda p: (self.first_day[p], p),
        )


@dataclass
class Lane:
    """One streamed man's started production in one team-period."""

    #: 1 is the man with the best own value, 2 the next, and so on.
    ordinal: int
    player_id: int
    first_day: int
    days_held: int
    starts: int
    line: CategoryLine
    #: Categories a week this man's started lines added to his team's week.
    value: float


@dataclass
class TeamPeriod:
    """A team-period, its places, and what each kind produced."""

    occ: Occupancy
    week: CategoryLine
    opponents: tuple[CategoryDistribution, ...]
    lanes: list[Lane]
    held_men: list[int]
    held_values: dict[int, float]
    held_lines: dict[int, CategoryLine]

    @property
    def season(self) -> int:
        return self.occ.season

    @property
    def team_id(self) -> int:
        return self.occ.team_id

    @property
    def period(self) -> int:
        return self.occ.period

    @property
    def week_days(self) -> float:
        """Seven days over this period's stored length."""
        return DAYS_A_WEEK / max(1, self.occ.stored_days)

    @property
    def lanes_by_occupancy(self) -> int:
        """Lanes by occupancy: peak men held, less men held every day."""
        return max(0, self.occ.roster_size - len(self.occ.held_men()))

    @property
    def lanes_by_arrival(self) -> int:
        """Lanes by arrival: runs of streamed men arriving one day apart."""
        runs = 0
        previous: int | None = None
        for player in self.occ.streamed_men():
            arrived = self.occ.first_day[player]
            if previous is None or arrived - previous > 1:
                runs += 1
            previous = arrived
        return runs

    def lane_count(self, by_arrival: bool) -> int:
        return self.lanes_by_arrival if by_arrival else self.lanes_by_occupancy

    def arrivals_order(self) -> list[Lane]:
        """The lanes in the order their men first arrived."""
        return sorted(self.lanes, key=lambda lane: (lane.first_day, lane.player_id))

    def streamed_line(self) -> CategoryLine:
        """Every streamed man's started line in the period, as one place."""
        total = CategoryLine()
        for lane in self.lanes:
            total = total + lane.line
        return total

    def streamed_value(self) -> float:
        """Categories a week the streamed place returned; 0 with no stream."""
        line = self.streamed_line()
        if not line.games:
            return 0.0
        return marginal(self.week, line, self.opponents) * self.week_days

    def held_13th(self) -> int | None:
        """The lowest-valued held man: the team's last held place."""
        if not self.held_men:
            return None
        return min(self.held_men, key=lambda man: (self.held_values[man], man))

    def held_13th_value(self) -> float:
        man = self.held_13th()
        return 0.0 if man is None else self.held_values[man] * self.week_days

    def held_13th_line(self) -> CategoryLine:
        man = self.held_13th()
        return CategoryLine() if man is None else self.held_lines[man]

    def held_started_games(self) -> int:
        return sum(self.held_lines[man].games for man in self.held_men)

    def streamed_started_games(self) -> int:
        return sum(lane.line.games for lane in self.lanes)


def load_occupancy(session: Session, seasons: Sequence[int]) -> list[Occupancy]:
    """Roster occupancy per team-period, for `seasons`."""
    wanted = [
        int(row[0])
        for row in session.execute(
            select(LeagueSeason.id).where(LeagueSeason.season.in_(list(seasons)))
        )
    ]
    names = {
        int(row[0]): str(row[1]).strip()
        for row in session.execute(
            select(Team.id, Team.name).where(Team.league_season_id.in_(wanted))
        )
    }
    period_of = {
        int(row[0]): (int(row[1]), int(row[2]), int(row[3]), int(row[4]))
        for row in session.execute(
            select(
                MatchupPeriod.id,
                LeagueSeason.season,
                MatchupPeriod.period,
                MatchupPeriod.first_scoring_period,
                MatchupPeriod.final_scoring_period,
            )
            .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
            .where(MatchupPeriod.league_season_id.in_(wanted))
        )
        if row[3] is not None and row[4] is not None
    }

    held: dict[tuple[int, int], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    first: dict[tuple[int, int], dict[int, int]] = defaultdict(dict)
    starts: dict[tuple[int, int], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    daily: dict[tuple[int, int], set[int]] = defaultdict(set)

    for row in session.execute(
        select(
            DailyLineupSlot.team_id,
            DailyLineupSlot.matchup_period_id,
            DailyLineupSlot.scoring_period,
            DailyLineupSlot.player_id,
            DailyLineupSlot.started,
        )
        .where(DailyLineupSlot.team_id.in_(list(names)))
        .order_by(DailyLineupSlot.scoring_period)
    ):
        team, period_id, day, player, started = (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]),
            bool(row[4]),
        )
        if period_id not in period_of:
            continue
        key = (team, period_id)
        held[key][player] += 1
        first[key].setdefault(player, day)
        daily[key].add(day)
        if started:
            starts[key][player] += 1

    out: list[Occupancy] = []
    for (team, period_id), store in held.items():
        season, period, first_day, final_day = period_of[period_id]
        out.append(
            Occupancy(
                season=season,
                team_id=team,
                team_name=names.get(team, str(team)),
                period=period,
                first=first_day,
                final=final_day,
                days=len(daily[(team, period_id)]),
                held_days=dict(store),
                first_day=dict(first[(team, period_id)]),
                start_days=dict(starts[(team, period_id)]),
            )
        )
    return sorted(out, key=lambda occ: (occ.season, occ.team_id, occ.period))


def build_team_periods(
    season: int,
    occupancy: Sequence[Occupancy],
    book: SeasonBook,
) -> list[TeamPeriod]:
    """One season's team-periods, with held places and streamed lanes."""
    opponents_of = {
        period: tuple(book.opponents.for_period(found)) for period, found in book.periods.items()
    }
    out: list[TeamPeriod] = []
    for occ in occupancy:
        week = book.team_week(occ.team_id, occ.period)
        held_men = occ.held_men()
        held_values = {man: book.value(occ.team_id, occ.period, man) for man in held_men}
        lanes: list[Lane] = []
        for player in occ.streamed_men():
            line = book.player_week(occ.team_id, occ.period, player)
            if not line.games:
                continue
            lanes.append(
                Lane(
                    ordinal=0,
                    player_id=player,
                    first_day=occ.first_day[player],
                    days_held=occ.held_days[player],
                    starts=occ.start_days.get(player, 0),
                    line=line,
                    value=book.value(occ.team_id, occ.period, player),
                )
            )
        lanes.sort(key=lambda lane: (-lane.value, -lane.starts, lane.player_id))
        for index, lane in enumerate(lanes, start=1):
            lane.ordinal = index
        out.append(
            TeamPeriod(
                occ=occ,
                week=week,
                opponents=opponents_of[occ.period],
                lanes=lanes,
                held_men=held_men,
                held_values=held_values,
                held_lines={
                    man: book.player_week(occ.team_id, occ.period, man) for man in held_men
                },
            )
        )
    return out


def adds_per_team_period(session: Session, season: int) -> dict[tuple[int, int], int]:
    """Executed adds each team made in each matchup period of `season`.

    Counted the way the product counts them
    (`app.pickups.state._adds_in_period`): one per ADD item, so a claim that
    added two men spends two of the budget.
    """
    league_season = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
    if league_season is None:
        return {}
    windows = {
        (int(row[3]), int(row[4])): (int(row[2]))
        for row in session.execute(
            select(
                MatchupPeriod.id,
                LeagueSeason.season,
                MatchupPeriod.period,
                MatchupPeriod.first_scoring_period,
                MatchupPeriod.final_scoring_period,
            )
            .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
            .where(MatchupPeriod.league_season_id == league_season.id)
        )
        if row[3] is not None and row[4] is not None
    }
    out: dict[tuple[int, int], int] = defaultdict(int)
    for row in session.execute(
        select(TransactionItem.to_team_id, Transaction.scoring_period)
        .join(Transaction, Transaction.id == TransactionItem.transaction_id)
        .where(
            Transaction.league_season_id == league_season.id,
            Transaction.type.in_(list(ADD_TYPES)),
            Transaction.status == "EXECUTED",
            TransactionItem.item_type == "ADD",
            TransactionItem.to_team_id.is_not(None),
        )
    ):
        if row[0] is None:
            continue
        team, day = int(row[0]), int(row[1])
        for (first_day, final_day), period in windows.items():
            if first_day <= day <= final_day:
                out[(team, period)] += 1
                break
    return dict(out)


def limit_failures(session: Session, seasons: Sequence[int]) -> int:
    """ESPN's own refusals for the acquisition limit, over `seasons`."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .join(LeagueSeason, LeagueSeason.id == Transaction.league_season_id)
            .where(
                LeagueSeason.season.in_(list(seasons)),
                Transaction.status == "FAILED_MATCHUPACQUISITIONLIMIT",
            )
        )
        or 0
    )


# --- summary helpers -------------------------------------------------------


def quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    """(median, lower quartile, upper quartile); zeros when the sample is empty."""
    if not values:
        return (0.0, 0.0, 0.0)
    ordered = sorted(values)
    if len(ordered) == 1:
        return (ordered[0], ordered[0], ordered[0])
    lower, middle, upper = quantiles(ordered, n=4, method="inclusive")
    return (middle, lower, upper)


def median_of(values: Sequence[float]) -> float:
    return median(values) if values else 0.0


def mean_of(values: Sequence[float]) -> float:
    return fmean(values) if values else 0.0


def share_above(values: Sequence[float], threshold: float) -> float:
    return sum(1 for value in values if value > threshold) / len(values) if values else 0.0


def line_of(label: str, values: Sequence[float]) -> str:
    """One line of a table: median, IQR, mean, n."""
    point, low, high = quartiles(values)
    return (
        f"  {label:<30}{point:>9.2f}   {low:>6.2f} - {high:<6.2f}"
        f"{mean_of(values):>9.2f}{len(values):>8}"
    )


def header(*columns: str) -> str:
    """A left-aligned first column and right-aligned numeric columns."""
    label, rest = columns[0], columns[1:]
    return f"  {label:<30}" + "".join(f"{c:>9}" for c in rest)


def games_week(games: int, stored_days: int) -> float:
    """Started games in a window, normalised to a seven-day week."""
    return games * DAYS_A_WEEK / max(1, stored_days)


# --- the run ---------------------------------------------------------------


@dataclass
class SeasonMeasure:
    """One season, reduced to the samples every step reports on."""

    season: int
    team_periods: int
    #: Team-periods whose days disagree with `matchup_periods`' stored window.
    gap_team_periods: int
    lanes: Counter[int]
    lanes_by_arrival: Counter[int]
    streamed: list[float]
    held_13th: list[float]
    held_all: list[float]
    lane_one: list[float]
    lane_rest: list[float]
    held_games: list[float]
    #: Each held man's own started games a week, one entry per held man.
    held_man_games: list[float]
    streamed_games: list[float]
    held_13th_games: list[float]
    adds: list[int]
    #: Adds per team-period keyed by lane count, for step 4.
    adds_by_lanes: dict[int, list[int]]
    streamed_by_lanes: dict[int, list[float]]
    #: Value of the first-arriving man and of the men after him, 2+ lanes only.
    first_arrival: list[float]
    later_arrivals: list[float]
    detail: list[dict[str, object]]


def measure_season(session: Session, season: int) -> SeasonMeasure:
    """Every sample one season contributes, in one pass."""
    book = SeasonBook.load(session, season)
    occupancy = load_occupancy(session, [season])
    periods = build_team_periods(season, occupancy, book)
    adds = adds_per_team_period(session, season)

    found = SeasonMeasure(
        season=season,
        team_periods=len(periods),
        gap_team_periods=sum(1 for tp in periods if tp.occ.days != tp.occ.stored_days),
        lanes=Counter(),
        lanes_by_arrival=Counter(),
        streamed=[],
        held_13th=[],
        held_all=[],
        lane_one=[],
        lane_rest=[],
        held_games=[],
        held_man_games=[],
        streamed_games=[],
        held_13th_games=[],
        adds=[],
        adds_by_lanes=defaultdict(list),
        streamed_by_lanes=defaultdict(list),
        first_arrival=[],
        later_arrivals=[],
        detail=[],
    )
    for tp in periods:
        days = tp.occ.stored_days
        count = tp.lane_count(False)
        found.lanes[min(count, 4)] += 1
        found.lanes_by_arrival[min(tp.lane_count(True), 4)] += 1
        found.held_all.extend(tp.held_values[man] * tp.week_days for man in tp.held_men)
        found.held_games.append(games_week(tp.held_started_games(), days))
        found.held_man_games.extend(
            games_week(tp.held_lines[man].games, days) for man in tp.held_men
        )
        thirteen = tp.held_13th()
        if thirteen is not None:
            found.held_13th.append(tp.held_13th_value())
            found.held_13th_games.append(games_week(tp.held_lines[thirteen].games, days))
        if tp.lanes:
            found.streamed.append(tp.streamed_value())
            found.streamed_games.append(games_week(tp.streamed_started_games(), days))
            found.lane_one.append(tp.lanes[0].value * tp.week_days)
            if len(tp.lanes) > 1:
                found.lane_rest.append(sum(lane.value for lane in tp.lanes[1:]) * tp.week_days)
        found.adds.append(adds.get((tp.team_id, tp.period), 0))
        found.adds_by_lanes[min(count, 4)].append(adds.get((tp.team_id, tp.period), 0))
        found.streamed_by_lanes[min(count, 4)].append(tp.streamed_value())
        if count >= 2 and len(tp.lanes) >= 2:
            ordered = tp.arrivals_order()
            found.first_arrival.append(ordered[0].value * tp.week_days)
            found.later_arrivals.append(sum(lane.value for lane in ordered[1:]) * tp.week_days)
        for lane in tp.lanes:
            found.detail.append(
                {
                    "season": season,
                    "team_id": tp.team_id,
                    "team": tp.occ.team_name,
                    "period": tp.period,
                    "stored_days": days,
                    "lanes": count,
                    "lane": lane.ordinal,
                    "player_id": lane.player_id,
                    "first_day": lane.first_day,
                    "days_held": lane.days_held,
                    "starts": lane.starts,
                    "games": lane.line.games,
                    "value_week": round(lane.value * tp.week_days, 4),
                }
            )
    return found


def pooled(measured: Sequence[SeasonMeasure], attribute: str) -> list[float]:
    """One sample pooled across seasons, 2020 included."""
    out: list[float] = []
    for found in measured:
        out.extend(getattr(found, attribute))
    return out


def pooled_without_covid(measured: Sequence[SeasonMeasure], attribute: str) -> list[float]:
    """One sample pooled across seasons, 2020 dropped (step 6)."""
    return [
        value
        for found in measured
        if found.season != COVID_SEASON
        for value in getattr(found, attribute)
    ]


def step_1(measured: Sequence[SeasonMeasure], by_arrival: bool) -> None:
    """How many team-periods ran 0, 1, 2, 3+ lanes, by season."""
    print("\n== Step 1. Lanes per team-period, by season ==")
    print(f"  definition: {'arrival runs' if by_arrival else 'peak rotating men'}")
    print(header("season", "team-periods", "0 lanes", "1 lane", "2 lanes", "3+ lanes"))
    for found in measured:
        counts = found.lanes_by_arrival if by_arrival else found.lanes
        three = sum(v for k, v in counts.items() if k >= 3)
        print(
            f"  {found.season:<30}{found.team_periods:>9}"
            f"{counts.get(0, 0):>9}{counts.get(1, 0):>9}"
            f"{counts.get(2, 0):>9}{three:>9}"
        )
    total: Counter[int] = Counter()
    for found in measured:
        counts = found.lanes_by_arrival if by_arrival else found.lanes
        for k, v in counts.items():
            total[k] += v
    n = sum(total.values())
    print(
        f"  {'TOTAL':<30}{n:>9}"
        + "".join(f"{total.get(k, 0):>9}" for k in range(3))
        + f"{sum(v for k, v in total.items() if k >= 3):>9}"
    )
    print(
        f"  share: 0 lanes {total.get(0, 0) / n:.1%}, 1 lane {total.get(1, 0) / n:.1%}, "
        f"2 lanes {total.get(2, 0) / n:.1%}, 3+ lanes "
        f"{sum(v for k, v in total.items() if k >= 3) / n:.1%}"
    )


def step_2(measured: Sequence[SeasonMeasure]) -> None:
    """Started games a week: held place versus streamed lane."""
    print("\n== Step 2. Started games a week ==")
    print(header("season", "held place", "streamed", "held 13th"))
    for found in measured:
        point, low, high = quartiles(found.held_games)
        s_point, s_low, s_high = quartiles(found.streamed_games)
        g_point, g_low, g_high = quartiles(found.held_13th_games)
        print(
            f"  {found.season:<30}{point:>6.1f} ({low:.0f}-{high:.0f})"
            f"{s_point:>7.1f} ({s_low:.0f}-{s_high:.0f})"
            f"{g_point:>7.1f} ({g_low:.0f}-{g_high:.0f})"
        )
    held = pooled(measured, "held_games")
    streamed = pooled(measured, "streamed_games")
    thirteen = pooled(measured, "held_13th_games")
    print(line_of("whole held roster, pooled", held))
    print(line_of("streamed place, pooled", streamed))
    print(line_of("held 13th man, pooled", thirteen))


def step_2_per_man(measured: Sequence[SeasonMeasure]) -> None:
    """Every held man's own games a week: the rate a held place buys."""
    print("\n  one held man on his own, by season (the crux comparison):")
    for found in measured:
        point, low, high = quartiles(found.held_man_games)
        print(
            f"  {found.season}: median {point:.1f} games a week (IQR {low:.1f}-{high:.1f}), "
            f"n {len(found.held_man_games)} held men"
        )
    print(line_of("one held man, pooled", pooled(measured, "held_man_games")))
    print(line_of("streamed place (all its men), pooled", pooled(measured, "streamed_games")))


def step_3(measured: Sequence[SeasonMeasure]) -> None:
    """What the streamed lane produced, in categories a week."""
    print("\n== Step 3. Categories a week ==")
    print(f"  the code's floor, TYPICAL_PICKUP = {TYPICAL_PICKUP:.2f}")
    print(
        f"  {'season':<30}{'streamed':>9}{'p25':>9}{'p75':>9}"
        f"{'held13':>9}{'h13 p25':>9}{'h13 p75':>9}"
    )
    for found in measured:
        point, low, high = quartiles(found.streamed)
        h_point, h_low, h_high = quartiles(found.held_13th)
        print(
            f"  {found.season:<30}{point:>9.2f}{low:>9.2f}{high:>9.2f}"
            f"{h_point:>9.2f}{h_low:>9.2f}{h_high:>9.2f}"
        )
    print("\n  pooled, categories a week (median, IQR, mean, n):")
    for label, values in (
        ("streamed lane", pooled(measured, "streamed")),
        ("held 13th man", pooled(measured, "held_13th")),
        ("every held man", pooled(measured, "held_all")),
        ("lane 1 (best own value)", pooled(measured, "lane_one")),
        ("lanes 2+ (the rest)", pooled(measured, "lane_rest")),
    ):
        print(line_of(label, values))
    print(
        f"\n  share above the {TYPICAL_PICKUP:.2f} floor: "
        f"streamed {share_above(pooled(measured, 'streamed'), TYPICAL_PICKUP):.1%}, "
        f"held 13th {share_above(pooled(measured, 'held_13th'), TYPICAL_PICKUP):.1%}"
    )
    print(
        f"  share above zero: "
        f"streamed {share_above(pooled(measured, 'streamed'), 0.0):.1%}, "
        f"held 13th {share_above(pooled(measured, 'held_13th'), 0.0):.1%}"
    )


def step_4(measured: Sequence[SeasonMeasure], failures: int) -> None:
    """The add budget: adds used against lanes run, and the second lane."""
    print("\n== Step 4. The add budget ==")
    print(header("lanes", "team-periods", "adds median", "adds mean") + f"{'at 7 adds':>10}")
    by_lanes: dict[int, list[int]] = defaultdict(list)
    for found in measured:
        for lanes, counts in found.adds_by_lanes.items():
            by_lanes[lanes].extend(counts)
    for lanes in sorted(by_lanes):
        counts_here = by_lanes[lanes]
        at7 = sum(1 for value in counts_here if value >= ADDS_PER_PERIOD_DAY * 7)
        print(
            f"  {lanes:<30}{len(counts_here):>9}{median_of(counts_here):>12.1f}"
            f"{mean_of(counts_here):>10.2f}{at7 / len(counts_here):>11.1%} ({at7})"
        )
    every = [value for values in by_lanes.values() for value in values]
    print(
        f"  pooled: {sum(1 for v in every if v >= 7)} of {len(every)} team-periods "
        f"({sum(1 for v in every if v >= 7) / len(every):.1%}) used the full 7-add budget"
    )
    print(f"  ESPN limit refusals (FAILED_MATCHUPACQUISITIONLIMIT), all seasons: {failures}")

    print("\n  production by lane count, categories a week:")
    print(header("lanes", "median", "IQR", "mean"))
    streamed_by_lanes: dict[int, list[float]] = defaultdict(list)
    for found in measured:
        for lanes, values in found.streamed_by_lanes.items():
            streamed_by_lanes[lanes].extend(values)
    for lanes in sorted(streamed_by_lanes):
        print(line_of(f"lanes={lanes}", streamed_by_lanes[lanes]))

    print("\n  lane one against the men who followed, ranked by value (lanes 2+ only):")
    print(line_of("best own value (lane 1)", pooled(measured, "lane_one")))
    print(line_of("the rest of the rotation", pooled(measured, "lane_rest")))
    print("\n  the same split by ARRIVAL -- first man in against later men:")
    print(line_of("first man to arrive", pooled(measured, "first_arrival")))
    print(line_of("men arriving after him", pooled(measured, "later_arrivals")))


def step_5(session: Session, seasons: Sequence[int]) -> None:
    """Percentages and turnovers: what a lane does to an ordinary week."""
    print("\n== Step 5. Percentages and turnovers ==")
    for season in seasons:
        book = SeasonBook.load(session, season)
        occupancy = load_occupancy(session, [season])
        periods = build_team_periods(season, occupancy, book)
        if not periods:
            continue
        ordinary = Counter(tp.occ.stored_days for tp in periods).most_common(1)[0][0]
        average = average_team_line(session, season, ordinary)
        distributions = tuple(book.opponents.for_days(ordinary))
        streamed: dict[str, list[float]] = defaultdict(list)
        single: dict[str, list[float]] = defaultdict(list)
        for tp in periods:
            line = tp.streamed_line()
            if line.games:
                for category, value in per_category_marginal(average, line, distributions).items():
                    streamed[category].append(value)
            if tp.lanes:
                for category, value in per_category_marginal(
                    average, tp.lanes[0].line, distributions
                ).items():
                    single[category].append(value)
        print(
            f"\n  {season} (ordinary period {ordinary} days, "
            f"{len(streamed.get('PTS', []))} streamed team-periods)"
        )
        print("    category   whole lane   one streamer")
        for category in CHARACTER:
            print(
                f"    {category:<10}{median_of(streamed[category]):>10.3f}"
                f"{median_of(single[category]):>14.3f}"
            )


def step_6(measured: Sequence[SeasonMeasure], by_arrival: bool, failures: int) -> None:
    """Sensitivity of the headline."""
    print("\n== Step 6. Sensitivity of the headline (step 3a pooled median) ==")
    variants = [
        ("2019-2026, all seasons", pooled(measured, "streamed")),
        (
            "2024-2026 only",
            [v for found in measured if found.season >= 2024 for v in found.streamed],
        ),
        ("2019-2026, no 2020", pooled_without_covid(measured, "streamed")),
    ]
    for label, values in variants:
        print(line_of(label, values))
    print("\n  corresponding held 13th man:")
    for label, values in (
        ("2019-2026, all seasons", pooled(measured, "held_13th")),
        (
            "2024-2026 only",
            [v for found in measured if found.season >= 2024 for v in found.held_13th],
        ),
        ("2019-2026, no 2020", pooled_without_covid(measured, "held_13th")),
    ):
        print(line_of(label, values))

    print("\n  lane-count definition: peak rotating men against arrival runs")
    peak: dict[int, list[float]] = defaultdict(list)
    runs: dict[int, list[float]] = defaultdict(list)
    for found in measured:
        for key, value in found.lanes.items():
            peak[key].append(float(value))
        for key, value in found.lanes_by_arrival.items():
            runs[key].append(float(value))
    print(header("lanes", "peak men", "arrival runs"))
    for lanes in sorted(set(peak) | set(runs)):
        print(f"  {lanes:<30}{sum(peak.get(lanes, [])):>9.0f}{sum(runs.get(lanes, [])):>13.0f}")
    del by_arrival, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Value a streamed roster lane.")
    parser.add_argument("--seasons", type=int, nargs="+", default=None)
    parser.add_argument("--days-definition", action="store_true")
    parser.add_argument("--drop-2020", action="store_true")
    parser.add_argument("--detail", type=Path, default=None)
    args = parser.parse_args()

    seasons = args.seasons or list(range(MIN_SEASON, MAX_SEASON + 1))
    seasons = sorted({s for s in seasons if MIN_SEASON <= s <= MAX_SEASON})
    if not seasons:
        raise SystemExit(f"no seasons in {MIN_SEASON}-{MAX_SEASON}")

    factory = make_session_factory(make_engine(dsn_from_environment()))
    started = time.time()
    measured: list[SeasonMeasure] = []
    with factory() as session:
        for season in seasons:
            found = measure_season(session, season)
            measured.append(found)
            print(
                f"  loaded {season}: {found.team_periods} team-periods, "
                f"{found.gap_team_periods} with a window gap, {len(found.streamed)} streamed"
            )
        failures = limit_failures(session, seasons)

        print("\n== Step 0. The check: TYPICAL_PICKUP reproduced ==")
        for season in seasons:
            values = pickup_values(SeasonBook.load(session, season))
            point, low, high = quartiles(values)
            print(
                f"  {season}: median {point:.3f}  IQR {low:.3f}-{high:.3f}  "
                f"mean {mean_of(values):.3f}  n {len(values)}"
            )
        print(
            f"  the code's floor, TYPICAL_PICKUP = {TYPICAL_PICKUP:.2f} "
            "(the lowest recent season's median)"
        )

        filtered = [
            found for found in measured if not args.drop_2020 or found.season != COVID_SEASON
        ]
        step_1(filtered, args.days_definition)
        step_2(filtered)
        step_2_per_man(filtered)
        step_3(filtered)
        step_4(filtered, failures)
        step_5(session, [found.season for found in filtered])
        step_6(filtered, args.days_definition, failures)

    detail: list[dict[str, object]] = [row for found in measured for row in found.detail]
    if args.detail is not None and detail:
        with args.detail.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(detail[0]))
            writer.writeheader()
            writer.writerows(detail)
        print(f"\n  detail written to {args.detail} ({len(detail)} lanes)")
    print(f"\n  wall time {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
