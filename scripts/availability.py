#!/usr/bin/env python3
"""How much of the projection's error is availability rather than rate?

Usage:
    python scripts/availability.py
    python scripts/availability.py --seasons 2026
    python scripts/availability.py --why

WHAT THIS MEASURES

Every projection in the product discounts a man's remaining games by one flat
number, `ESPN_AVAILABILITY` (0.881), because until the injury reports landed
nothing knew who was hurt on a given morning. Five seasons of the league's own
morning reports (docs/injuries.md) now say, for every morning, who was Out /
Doubtful / Questionable / Probable / Available and why. This asks how much of
the projection's error that flat number is responsible for, and how much a
status-conditional games term would cut.

Five steps, each per season and pooled:

1. STATUS -> PLAYED. For every (player, day) with a morning line about a game
   his team played, the share that played that night, and minutes against his
   own previous-ten played average. By status, by reason class, and for the
   silence case -- his team played and the league did not name him.
2. ABSENCES. The length distribution of Out runs, the return curve by days
   already out, and whether the first line's reason carried a timeline.
3. AVAILABILITY'S SHARE. On 2026, decompose each player-checkpoint's 28-day
   error into a games part and a rate part, then re-score with the flat
   discount replaced by a status-conditional expectation.
4. THE INTRADAY SAMPLE. How often a morning status moved by tip-off. The
   brief expects sixteen 2026 dates at quarter-hour cadence; the data holds
   **seventeen 2021-22 dates**, hourly, 19 October to 4 November 2021, and no
   2026 intraday snapshots at all. The step names the dates it used rather
   than the season it was asked for.
5. THE BENEFICIARY HOOK. Classify box-score absences by what the report said.

THE THREE CLOCKS, WHICH ARE WHERE THIS GOES WRONG QUIETLY

**`player_game_stats.game_date` is UTC, and so is `pro_team_games.game_at`.**
A seven o'clock Eastern tip-off is 23:00 UTC and a ten o'clock one is 02:00
UTC the next day, so reading `::date` off either puts most evening games on the
wrong calendar date. Every date in this script comes from `eastern_date()`.
Getting this wrong is not a rounding error: an early pass of this study
reported a 19% coverage rate and a 29% Questionable play rate, and both were
the bug.

**`pro_team_games.scoring_period` is per team and can differ between two teams
on the same date**, while `player_game_stats.scoring_period` is the league's
own scoring period and agrees across a date. The period map here is built from
the box scores, which is the same clock the product counts games on.

**A report line is a statement about one game, and the league names a team's
next game as well as tonight's.** So a line is only visible for a day when its
`game_date` is that day or later, which the point-in-time rule already enforces,
and it is only *scored* here where the man's team played that day and he has a
box-score row: in 2026, 10,578 player-day lines, of which 6,196 are scorable.
The other 4,382 are men with no box row at all that night, nearly all G-League
and two-way men who never touched the NBA floor. They are dropped rather than
scored against nothing. `--why` prints the accounting per season.

**The two play rates are different numbers and only one is a games term.**
Step 1 asks "did he play the next game" and answers 0.925 for a man the league
did not name. A games term needs "what share of a window's worth of his team's
games does he appear in", and that is 0.745 for the same population -- a
season's load management, minor knocks and bench nights are in the second
denominator and not the first. `window_rate` measures the second, off the very
checkpoints the term is scored on, and it is the one the games term uses.

WHAT THE POINT-IN-TIME RULE MEANS HERE

Every read is bounded by `app.injuries.morning_of(day)` (ten o'clock Eastern)
and only lines published at or before that moment are visible, plus the day
bound that makes a line expire the morning after the game it was about. Step 1
applies that rule directly; step 2 asks `app.injuries.absences`, the same rule
over the whole season; step 3 reads each checkpoint morning through the same
rule. Nothing here can see a line published after the morning it is judged on.

Read-only: SELECTs only, nothing written, no file written.
"""

from __future__ import annotations

import argparse
import re
import statistics
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    DailyLineupSlot,
    InjuryReport,
    LeagueSeason,
    MatchupPeriod,
    PlayerGameStat,
    PlayerSeasonStat,
    ProTeamGame,
)
from app.db.session import make_engine, make_session_factory
from app.draft.projections import usable
from app.injuries import RULED_OUT, absences, morning_of
from app.scoring.knowable import (
    PRIOR_GAMES,
    RECENT_DAYS,
    RECENT_WEIGHT,
    Rate,
    _mix,
    rate_from_totals,
)
from app.scoring.lines import COUNTS, CategoryLine
from app.scoring.value import marginal

#: Eastern, for both `player_game_stats.game_date` and `pro_team_games.game_at`.
EASTERN = ZoneInfo("America/New_York")

#: The five seasons the reports are loaded for, as the product numbers them.
SEASONS = (2022, 2023, 2024, 2025, 2026)

#: The season with saved checkpoints, and the days `scripts/projection_prior.py`
#: uses, so the two studies can be read side by side.
CHECKPOINT_SEASON = 2026
CHECKPOINTS = (21, 42, 63, 84, 105, 126)

#: What a checkpoint predicts: `knowable`'s docstring, "the next 28 days".
HORIZON = 28

#: The near window, for the brief's discount sensitivity.
NEAR = 7

#: The product's flat games discount (`app.pickups.projection`).
FLAT_DISCOUNT = 0.881

#: A cell needs this many lines before a table will print it.
MIN_CELL = 20

#: Reason classes, as a rule over the league's free text. Two stages, because
#: the league's prefix and its disposition say different things: `Injury/Illness
#: - Left Ankle; Sprain` is an ankle sprain and `Injury/Illness - N/a; Illness`
#: is an illness. Stage one reads the family in the prefix, stage two the
#: disposition after the semicolon. Restated in docs/availability.md.
FAMILY_RULES: tuple[tuple[str, str], ...] = (
    (r"^G League", "g_league"),
    (r"^League Suspension|^Team Suspension", "suspension"),
    (r"^Trade Pending", "trade"),
    (r"^Not With Team|^Not with Team|^Ineligible To Play|^Coach's Decision", "not_with_team"),
    (r"^Personal Reasons", "personal"),
    (r"^Return to Competition Reconditioning$", "reconditioning"),
    (r"^Concussion Protocol$", "concussion"),
    (r"^Health and Safety Protocols", "protocols"),
    (r"^Rest\b|Injury Management|Injury Maintenance", "rest_management"),
)

#: Stage two: the disposition decides, searched on its own so the `Illness`
#: inside `Injury/Illness` cannot claim a sprained ankle.
DISP_RULES: tuple[tuple[str, str], ...] = (
    (r"Illness|illness|Covid|COVID|\bcold\b|\bflu\b", "illness"),
    (r"Reconditioning", "reconditioning"),
    (r"Concussion", "concussion"),
)

#: The classes, in the order the tables print them.
REASON_CLASSES = (
    "injury",
    "illness",
    "rest_management",
    "g_league",
    "personal",
    "suspension",
    "trade",
    "concussion",
    "protocols",
    "reconditioning",
    "not_with_team",
    "unknown",
)

#: What a class is called on the page.
CLASS_LABEL = {
    "injury": "injury",
    "illness": "illness",
    "rest_management": "rest / management",
    "g_league": "G League",
    "personal": "personal",
    "suspension": "suspension",
    "trade": "trade",
    "concussion": "concussion",
    "protocols": "protocols",
    "reconditioning": "reconditioning",
    "not_with_team": "not with team",
    "unknown": "unknown",
    "assignment": "assignment",
    "unreported": "unreported",
}

#: Days already out at which the return curve is reported (the brief's N).
OUT_DAYS = (1, 3, 7, 14, 28)

#: How far ahead a return is looked for, per N.
RETURN_WINDOWS = (1, 3, 7, 14, 28)

#: A beneficiary absence: a rotation man missing this many consecutive team
#: games or more, from box scores (the brief's statement of docs/beneficiary's
#: definition). A rotation man has played this many games already.
BENEFICIARY_MIN_GAMES = 3
BENEFICIARY_ROTATION_GAMES = 10

#: Words that would carry a timeline, if the league ever used one.
TIMELINE_WORDS = (
    "week",
    "weeks",
    "re-evaluated",
    "reevaluated",
    "day-to-day",
    "day to day",
    "indefinitely",
    "out for",
    "month",
    "months",
)

#: A printed date, e.g. "12/3" or "12/3/25".
PRINTED_DATE = re.compile(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b")

#: ESPN's severity order, as `app.injuries` carries it: worse is lower.
SEVERITY = {"Out": 0, "Doubtful": 1, "Questionable": 2, "Probable": 3, "Available": 4}

#: A count whose spread is zero carries no information; `scaled_error` skips it.
ZERO_LINE = dict.fromkeys(COUNTS, 0.0)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def f2(value: float) -> str:
    """Two decimals, or an em dash where there is no answer."""
    return "—" if value != value else f"{value:.2f}"


def share(top: int, bottom: int) -> str:
    """A share as a percentage to two decimals, or an em dash."""
    return "—" if not bottom else f"{100.0 * top / bottom:.2f}%"


def removed(before: float, after: float) -> str:
    """The share of an error a change removes, or an em dash."""
    if before <= 0 or after != after:
        return "—"
    return f"{100.0 * (before - after) / before:.2f}%"


def table(title: str, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    print(f"\n### {title}")
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def eastern_date(moment: datetime) -> date:
    """The Eastern calendar date of a stored UTC timestamp.

    Both `player_game_stats.game_date` and `pro_team_games.game_at` are UTC, so
    a seven o'clock Eastern tip-off carries the *next* calendar date. This is
    the correction, and it is the one clock everything here reads.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(EASTERN).date()


def reason_class(reason: str | None) -> str:
    """The reason class of a league reason string, by the stated rule.

    Stage one reads the family in the prefix, stage two the disposition after
    the semicolon. A `;` with nothing matching in the disposition, or an
    `Injury/Illness` prefix carrying a body part, is an injury. Restated in
    docs/availability.md so a reviewer can recompute it.
    """
    text = (reason or "").strip()
    for pattern, label in FAMILY_RULES:
        if re.search(pattern, text):
            return label
    disposition = text.split(";", 1)[1].strip() if ";" in text else ""
    for pattern, label in DISP_RULES:
        if re.search(pattern, disposition):
            return label
    return "injury" if disposition else "unknown"


def timeline_in(reason: str | None) -> bool:
    """Whether a reason carries a timeline word or a printed date."""
    text = (reason or "").lower()
    return any(word in text for word in TIMELINE_WORDS) or bool(PRINTED_DATE.search(text))


# ---------------------------------------------------------------------------
# the data, read once a season
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One man's morning line about a game his own team played that night."""

    player_id: int
    day: date
    season: int
    status: str
    reason: str | None
    kind: str
    played: bool
    minutes: float
    #: Mean minutes over his previous ten played games, or None with none.
    prior_minutes: float | None

    @property
    def minutes_share(self) -> float | None:
        """His minutes as a share of his own previous-ten mean."""
        if self.prior_minutes is None or self.prior_minutes <= 0:
            return None
        return self.minutes / self.prior_minutes


@dataclass(frozen=True)
class Case:
    """A night a man's team played and the league did not name him."""

    player_id: int
    day: date
    season: int
    played: bool
    minutes: float


#: One report line, as this script reads it: published at `at`, about `day`.
ReportLine = tuple[datetime, date, str, str | None, int]


@dataclass
class SeasonData:
    """One season read into memory."""

    season: int
    league_season: LeagueSeason
    #: pro_team_id -> the Eastern days it played.
    team_days: dict[int, set[date]]
    #: Eastern day -> the league's scoring period for it (from the box scores,
    #: because `pro_team_games.scoring_period` differs between two teams on one
    #: date and the box score's does not).
    period_of_day: dict[date, int]
    #: scoring period -> its first Eastern day.
    day_of_period: dict[int, date]
    #: player id -> (reported_at, pro_team_id) ascending, for a traded man.
    player_teams: dict[int, list[tuple[datetime, int]]]
    #: pro_team_id -> (reported_at, game_date), so "his team had filed about
    #: this day" can be asked without a query per day.
    filed: dict[int, list[tuple[datetime, date]]]
    #: (player id, Eastern day) -> whether ESPN's box score says he played.
    boxes: dict[tuple[int, date], bool]
    #: (player id, Eastern day) -> (played, minutes).
    box_details: dict[tuple[int, date], tuple[bool, float]]
    #: player id -> (Eastern day, minutes) for his played games, ascending.
    played_games: dict[int, list[tuple[date, float]]]
    #: player id -> games the season's projection gave him.
    projected_games: dict[int, float]
    #: player id -> the first scoring period he held a roster place.
    first_rostered: dict[int, int]
    #: player id -> (scoring period, Eastern day or None, the eleven counts)
    #: for his played games. The period is the product's own key and is what
    #: the blend counts on; the day is only used where a date is needed.
    counts: dict[int, list[tuple[int, date | None, dict[str, float]]]] = field(default_factory=dict)
    #: The season's report lines, keyed by player, ascending. Filled by
    #: `load_season` so no step re-reads them.
    lines: dict[int, list[ReportLine]] = field(default_factory=dict)

    def newest(self, player_id: int, day: date) -> ReportLine | None:
        """The newest line about this man visible on `day`'s morning.

        The point-in-time rule as `app.injuries.status_as_of` states it:
        published at or before `morning_of(day)`, about that day or a later
        game, the nearer game winning a tie on the instant. None means the
        league said nothing current about him, which is not "fit".
        """
        at = morning_of(day)
        best: ReportLine | None = None
        for row in self.lines.get(player_id, ()):
            if row[0] > at or row[1] < day:
                continue
            if best is None or (row[0], -(row[1] - day).days) > (
                best[0],
                -(best[1] - day).days,
            ):
                best = row
        return best

    def team_on(self, player_id: int, day: date) -> int | None:
        """The team the newest line up to that morning put him on, if any."""
        at = morning_of(day)
        best: tuple[datetime, int] | None = None
        for reported_at, team in self.player_teams.get(player_id, ()):
            if reported_at <= at and (best is None or reported_at > best[0]):
                best = (reported_at, team)
        return best[1] if best is not None else None

    def nearest_team(self, player_id: int, day: date) -> int | None:
        """The team on the season's nearest line to `day`.

        Used only where the morning itself was silent and a team is needed to
        count the man's remaining games. A man's NBA team does not change
        without a trade the reports would show, and this is never a status --
        it is only ever the denominator of a games term.
        """
        his = self.lines.get(player_id)
        if not his:
            return None
        at = morning_of(day)
        pool = [row for row in his if row[0] <= at] or his
        return min(pool, key=lambda row: abs((row[1] - day).days))[4]

    def window_days(self, team: int | None, first_period: int, periods: int) -> list[date]:
        """The Eastern days a team plays inside a run of scoring periods."""
        if team is None:
            return []
        low, high = first_period, first_period + periods
        return sorted(
            day
            for day in self.team_days.get(team, set())
            if low <= self.period_of_day.get(day, -1) < high
        )


def load_season(session: Session, season: int) -> SeasonData:
    """Read one season's schedule, reports and box scores once."""
    league_season = session.scalars(select(LeagueSeason).where(LeagueSeason.season == season)).one()

    period_of_day: dict[date, int] = {}
    for period, day in session.execute(
        select(PlayerGameStat.scoring_period, PlayerGameStat.game_date)
        .where(PlayerGameStat.season == season, PlayerGameStat.game_date.is_not(None))
        .distinct()
    ).all():
        period_of_day[eastern_date(day)] = int(period)
    day_of_period: dict[int, date] = {}
    for day, period in period_of_day.items():
        if period not in day_of_period or day < day_of_period[period]:
            day_of_period[period] = day

    team_days: dict[int, set[date]] = defaultdict(set)
    for pro_team_id, game_at in session.execute(
        select(ProTeamGame.pro_team_id, ProTeamGame.game_at).where(ProTeamGame.season == season)
    ).all():
        team_days[int(pro_team_id)].add(eastern_date(game_at))

    first_day, last_day = date(season - 1, 10, 1), date(season, 9, 30)
    lines: dict[int, list[ReportLine]] = defaultdict(list)
    player_teams: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    filed: dict[int, list[tuple[datetime, date]]] = defaultdict(list)
    for reported_at, game_date, status, reason, player_id, pro_team_id in session.execute(
        select(
            InjuryReport.reported_at,
            InjuryReport.game_date,
            InjuryReport.status,
            InjuryReport.reason,
            InjuryReport.player_id,
            InjuryReport.pro_team_id,
        ).where(
            InjuryReport.game_date >= first_day,
            InjuryReport.game_date <= last_day,
            InjuryReport.player_id.is_not(None),
            InjuryReport.pro_team_id.is_not(None),
        )
    ).all():
        pid, team = int(player_id), int(pro_team_id)
        player_teams[pid].append((reported_at, team))
        if status is not None:
            lines[pid].append((reported_at, game_date, str(status), reason, team))
        filed[team].append((reported_at, game_date))
    for line_rows in lines.values():
        line_rows.sort()
    for team_rows in player_teams.values():
        team_rows.sort()
    for filed_rows in filed.values():
        filed_rows.sort()

    boxes: dict[tuple[int, date], bool] = {}
    box_details: dict[tuple[int, date], tuple[bool, float]] = {}
    played_games: dict[int, list[tuple[date, float]]] = defaultdict(list)
    counts: dict[int, list[tuple[int, date | None, dict[str, float]]]] = defaultdict(list)
    columns = [getattr(PlayerGameStat, name) for name in COUNTS.values()]
    for row in session.execute(
        select(
            PlayerGameStat.player_id,
            PlayerGameStat.game_date,
            PlayerGameStat.played,
            PlayerGameStat.minutes,
            PlayerGameStat.scoring_period,
            *columns,
        ).where(PlayerGameStat.season == season)
    ).all():
        pid = int(row[0])
        day = eastern_date(row[1]) if row[1] is not None else None
        played, minutes = bool(row[2]), float(row[3] or 0.0)
        # A row with no `game_date` still counts for the blend: the product
        # counts on `scoring_period` and a date-keyed count silently drops
        # these. It cannot be keyed by day for the day-level tables, so it goes
        # into `counts` and not into `boxes`.
        if played and minutes > 0:
            counts[pid].append(
                (
                    int(row[4]),
                    day,
                    {key: float(row[i] or 0.0) for i, key in enumerate(COUNTS, start=5)},
                )
            )
        if day is None:
            continue
        boxes[(pid, day)] = played
        box_details[(pid, day)] = (played, minutes)
        if played and minutes > 0:
            played_games[pid].append((day, minutes))
    for played_rows in played_games.values():
        played_rows.sort()
    for count_rows in counts.values():
        count_rows.sort(key=lambda row: row[0])

    projected_games: dict[int, float] = {}
    for player_id, games in session.execute(
        select(PlayerSeasonStat.player_id, PlayerSeasonStat.games_played).where(
            PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected"
        )
    ).all():
        projected_games[int(player_id)] = float(games or 0.0)

    first_rostered: dict[int, int] = {}
    for player_id, period in session.execute(
        select(DailyLineupSlot.player_id, DailyLineupSlot.scoring_period)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            DailyLineupSlot.slot != "FA",
        )
    ).all():
        pid, found = int(player_id), int(period)
        if pid not in first_rostered or found < first_rostered[pid]:
            first_rostered[pid] = found

    return SeasonData(
        season=season,
        league_season=league_season,
        team_days=dict(team_days),
        period_of_day=period_of_day,
        day_of_period=day_of_period,
        player_teams=dict(player_teams),
        filed=dict(filed),
        boxes=boxes,
        box_details=box_details,
        played_games=dict(played_games),
        projected_games=projected_games,
        first_rostered=first_rostered,
        counts=dict(counts),
        lines=dict(lines),
    )


# ---------------------------------------------------------------------------
# step 1: status -> played
# ---------------------------------------------------------------------------


def step_one(data: Mapping[int, SeasonData]) -> None:
    """What a morning status is worth, by status, by reason, and by silence."""
    print("\n## Step 1. What a morning status is worth")

    lines = {season: _lines_of(data[season]) for season in data}
    silent = {season: _silence_of(data[season]) for season in data}
    pooled = [line for season in lines for line in lines[season]]
    pooled_silent = [case for season in silent for case in silent[season]]

    rows: list[list[object]] = []
    for label, found, _quiet in (
        *((str(season), lines[season], silent[season]) for season in lines),
        ("pooled", pooled, pooled_silent),
    ):
        if not found:
            continue
        for status in ("Out", "Doubtful", "Questionable", "Probable", "Available"):
            cell = [line for line in found if line.status == status]
            if not cell:
                continue
            shares = [line.minutes_share for line in cell if line.minutes_share is not None]
            rows.append(
                [
                    label if status == "Out" else "",
                    status,
                    len(cell),
                    share(sum(1 for line in cell if line.played), len(cell)),
                    f2(mean(shares)) if shares else "—",
                    f2(mean([line.minutes for line in cell])),
                ]
            )
    table(
        "Step 1a. Status -> played, per season and pooled",
        ("season", "status", "n", "played", "minutes / his own last ten", "mean minutes"),
        rows,
    )

    rows = []
    for status in ("Out", "Doubtful", "Questionable", "Probable", "Available"):
        for kind in REASON_CLASSES:
            cell = [line for line in pooled if line.status == status and line.kind == kind]
            if len(cell) < MIN_CELL:
                continue
            shares = [line.minutes_share for line in cell if line.minutes_share is not None]
            rows.append(
                [
                    status,
                    CLASS_LABEL[kind],
                    len(cell),
                    share(sum(1 for line in cell if line.played), len(cell)),
                    f2(mean(shares)) if shares else "—",
                ]
            )
    table(
        f"Step 1b. Status x reason -> played, pooled (a cell needs {MIN_CELL} lines)",
        ("status", "reason", "n", "played", "minutes / his own last ten"),
        rows,
    )

    rows = []
    for label, found, quiet in (
        *((str(season), lines[season], silent[season]) for season in lines),
        ("pooled", pooled, pooled_silent),
    ):
        if not found or not quiet:
            continue
        rows.append(
            [
                label,
                len(found),
                share(sum(1 for line in found if line.played), len(found)),
                len(quiet),
                share(sum(1 for case in quiet if case.played), len(quiet)),
                f2(mean([case.minutes for case in quiet])),
            ]
        )
    table(
        "Step 1c. What silence means: his team played and the league did not name him",
        ("season", "named n", "named played", "silent n", "silent played", "mean minutes"),
        rows,
    )


def _lines_of(data: SeasonData) -> list[Line]:
    """Every scored morning line: his team played, and there is a box row.

    The population is a man the league named on the morning of a game his own
    team played, with ESPN's own `played` flag to score it against. The
    accounting for everyone else is `counts_for`.
    """
    out: list[Line] = []
    for (player_id, day), (played, minutes) in data.box_details.items():
        best = data.newest(player_id, day)
        if best is None:
            continue
        team = data.team_on(player_id, day)
        if team is None or day not in data.team_days.get(team, set()):
            continue
        before = [m for d, m in data.played_games.get(player_id, ()) if d < day]
        recent = before[-10:]
        out.append(
            Line(
                player_id=player_id,
                day=day,
                season=data.season,
                status=best[2],
                reason=best[3],
                kind=reason_class(best[3]),
                played=played,
                minutes=minutes,
                prior_minutes=mean(recent) if recent else None,
            )
        )
    return out


def _silence_of(data: SeasonData) -> list[Case]:
    """Every night his team played, the league filed about it, and it did not
    name him.

    A night the league said nothing about his team at all is not evidence
    (docs/injuries.md), so it is excluded rather than counted as available.
    """
    out: list[Case] = []
    for (player_id, day), (played, minutes) in data.box_details.items():
        team = data.team_on(player_id, day)
        if team is None:
            continue
        if day not in data.team_days.get(team, set()):
            continue
        if not _team_filed(data, team, day):
            continue
        if data.newest(player_id, day) is not None:
            continue
        out.append(
            Case(
                player_id=player_id,
                day=day,
                season=data.season,
                played=played,
                minutes=minutes,
            )
        )
    return out


def _all_lines(data: SeasonData) -> list[ReportLine]:
    """Every report line of the season, flattened."""
    return [row for rows in data.lines.values() for row in rows]


def counts_for(data: SeasonData) -> dict[str, int]:
    """The accounting: where a season's player-day lines go.

    Printed by `--why`. The gap between the first number and the third is
    large and is the reason every table below carries its own `n`.
    """
    out = dict.fromkeys(
        ("player_days", "on_a_game_day", "about_a_later_game", "no_box_row", "scored", "silent"),
        0,
    )
    for player_id, rows in data.lines.items():
        for day in sorted({row[1] for row in rows}):
            out["player_days"] += 1
            if data.newest(player_id, day) is None:
                continue
            team = data.team_on(player_id, day)
            if team is None or day not in data.team_days.get(team, set()):
                out["about_a_later_game"] += 1
                continue
            out["on_a_game_day"] += 1
            if (player_id, day) not in data.box_details:
                out["no_box_row"] += 1
                continue
            out["scored"] += 1
    out["silent"] = len(_silence_of(data))
    return out


# ---------------------------------------------------------------------------
# step 2: absences
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """An Out run, with the reason the league first gave for it."""

    season: int
    player_id: int
    first: date
    last: date
    days: int
    first_reason: str | None
    kind: str


def step_two(session: Session, seasons: Sequence[int]) -> None:
    """Out runs: how long, when he comes back, and whether a timeline was said."""
    print("\n## Step 2. Absences")

    runs = _runs(session, seasons)
    print(f"\n# {len(runs)} Out runs over {len(seasons)} seasons")

    rows: list[list[object]] = []
    for label, found in _by_season(runs, seasons):
        if not found:
            continue
        lengths = sorted(run.days for run in found)
        rows.append(
            [
                label,
                len(found),
                f2(mean([float(n) for n in lengths])),
                str(statistics.median(lengths)),
                str(lengths[max(0, int(0.9 * (len(lengths) - 1)))]),
                str(max(lengths)),
                share(sum(1 for n in lengths if n == 1), len(lengths)),
                share(sum(1 for n in lengths if n <= 7), len(lengths)),
            ]
        )
    table(
        "Step 2a. Out-run length, per season and pooled",
        ("season", "runs", "mean days", "median", "p90", "max", "one-day runs", "back within 7"),
        rows,
    )

    rows = []
    for label, found in _by_season(runs, seasons):
        if not found:
            continue
        for days_out in OUT_DAYS:
            # Only runs still going at N days can be asked what happens next.
            still = [run for run in found if run.days > days_out]
            if len(still) < MIN_CELL:
                continue
            row: list[object] = [label, days_out, len(still)]
            for window in RETURN_WINDOWS:
                back = sum(1 for run in still if run.days <= days_out + window)
                row.append(share(back, len(still)))
            rows.append(row)
    table(
        "Step 2b. Given N days out already, back within M more days?",
        ("season", "N days out", "runs still going", *(f"in {m}d" for m in RETURN_WINDOWS)),
        rows,
    )

    rows = []
    for label, found in _by_season(runs, seasons):
        if not found:
            continue
        for kind in REASON_CLASSES:
            cell = [run for run in found if run.kind == kind]
            if len(cell) < MIN_CELL:
                continue
            lengths = sorted(run.days for run in cell)
            rows.append(
                [
                    label,
                    CLASS_LABEL[kind],
                    len(cell),
                    f2(mean([float(n) for n in lengths])),
                    str(statistics.median(lengths)),
                    share(sum(1 for n in lengths if n <= 7), len(lengths)),
                ]
            )
    table(
        "Step 2c. Out-run length by the first line's reason class",
        ("season", "reason", "runs", "mean days", "median", "back within 7"),
        rows,
    )

    with_timeline = [run for run in runs if timeline_in(run.first_reason)]
    print(
        f"\n# The first line's reason carried a timeline word or a printed date in "
        f"**{len(with_timeline)} of {len(runs)} runs** "
        f"({share(len(with_timeline), len(runs))})."
    )
    if with_timeline:
        print("# the reasons that did:")
        for reason in sorted({run.first_reason for run in with_timeline if run.first_reason})[:10]:
            print(f"#   {reason!r}")


def _by_season(runs: Sequence[Run], seasons: Sequence[int]) -> list[tuple[str, list[Run]]]:
    """One (label, runs) per season, then pooled."""
    out = [(str(season), [run for run in runs if run.season == season]) for season in seasons]
    out.append(("pooled", list(runs)))
    return out


def _runs(session: Session, seasons: Sequence[int]) -> list[Run]:
    """Every Out run of these seasons, with the reason that opened it."""
    out: list[Run] = []
    for season in seasons:
        for player_id, team in _mentioned(session, season):
            for run in absences(session, player_id, season):
                reason = _opening_reason(session, player_id, run.first, team, season)
                out.append(
                    Run(
                        season=season,
                        player_id=player_id,
                        first=run.first,
                        last=run.last,
                        days=run.days,
                        first_reason=reason,
                        kind=reason_class(reason),
                    )
                )
    return out


def _opening_reason(
    session: Session, player_id: int, day: date, team: int, season: int
) -> str | None:
    """The reason on the earliest line the morning of an Out run could see.

    Read through the same point-in-time bound as everything else: the newest
    line published at or before that morning, about that day's game or a later
    one, filed about the team he was on.
    """
    row = session.execute(
        select(InjuryReport.reason)
        .where(
            InjuryReport.player_id == player_id,
            InjuryReport.pro_team_id == team,
            InjuryReport.status.in_(tuple(RULED_OUT)),
            InjuryReport.reported_at <= morning_of(day),
            InjuryReport.game_date >= day,
            InjuryReport.game_date <= date(season, 9, 30),
        )
        .order_by(InjuryReport.reported_at.desc(), InjuryReport.game_date.asc())
        .limit(1)
    ).first()
    return None if row is None else row[0]


def _mentioned(session: Session, season: int) -> list[tuple[int, int]]:
    """Every (player, team) the season's reports named, whose runs to read."""
    rows = session.execute(
        select(InjuryReport.player_id, InjuryReport.pro_team_id)
        .where(
            InjuryReport.player_id.is_not(None),
            InjuryReport.pro_team_id.is_not(None),
            InjuryReport.game_date >= date(season - 1, 10, 1),
            InjuryReport.game_date <= date(season, 9, 30),
        )
        .distinct()
    ).all()
    return [(int(player), int(team)) for player, team in rows]


# ---------------------------------------------------------------------------
# step 3: availability's share of the error
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    """One rostered player-checkpoint, and what the morning knew about him."""

    player_id: int
    day: int
    start: date
    games: int
    to_date: dict[str, float]
    recent: dict[str, float]
    recent_games: int
    espn: Rate
    actual: dict[str, float]
    actual_games: int
    #: Games his own team played in the window.
    team_games: int
    #: Of those, the ones he actually played -- the oracle for a games term.
    played: int
    #: The morning's status, or None for silence.
    status: str | None
    kind: str
    #: The empirical play rate his status and reason class imply.
    play_rate: float
    #: Days he was already Out, or None.
    days_out: int | None


#: What a games term is called on the page, and how it reads.
GAMES_TERMS = (
    "the product: flat 0.881 x his team's games",
    "status-conditional, one game at a time",
)


def step_three(session: Session, data: SeasonData) -> None:
    """The games/rate decomposition, and the status-conditional re-score.

    THE GAMES MODEL A LIVE TERM WOULD RUN

    The product multiplies a man's remaining games by one flat number. The
    status-conditional term replaces it with the two questions the morning can
    actually answer:

        games = (his team's games in the window)
              x (the share of them he plays, given his status and reason)

    and, for a man the morning has Out and has had Out for N days, a second
    factor from the return curve: P(back within the rest of the window | already
    N days out). A man the league did not name gets the population's play rate
    and no return discount, because the league not naming him is evidence he is
    fit -- it is the *absence* of a status, not a status.
    """
    print("\n## Step 3. Availability's share of the error")

    lines = _lines_of(data)
    cases = _silence_of(data)
    if not lines:
        print("\n# no scored morning lines; the step is skipped")
        return
    rates = _play_rates(lines, cases)
    curve = _return_curve(session, (data.season,))
    # The games term needs a *window* rate, not the next-game rate step 1
    # measures: over 28 days a man the league never named plays 74% of his
    # team's games, against 93% of the next one. Both are reported; the term
    # uses the first, because the term is what the window is scored on.
    windows = _window_rates(session, data)
    points = _points(session, data, rates, windows)
    if not points:
        print("\n# no rostered man had games on both sides of a checkpoint; skipped")
        return

    spread = _spreads(points)
    zero = mean([_scaled_error(ZERO_LINE, point.actual, spread) for point in points])
    print(f"\n# {len(points)} player-checkpoints over {list(CHECKPOINTS)} on {data.season}")
    print(f"# a zero line scores {zero:.2f} in the fit's units; at or above it is a defect")
    print(f"# the product's flat discount is {FLAT_DISCOUNT:.3f} (`app.pickups.projection`)")
    print(
        "# the games term's rates, over a window (played / his team's games): "
        + ", ".join(f"{key} {value:.3f}" for key, value in sorted(windows.items()))
    )
    print(
        f"# his team played {mean([float(p.team_games) for p in points]):.2f} games in a window "
        f"and he played {mean([float(p.played) for p in points]):.2f} of them"
    )

    def flat(point: Point, window: int) -> float:
        return point.team_games * FLAT_DISCOUNT if window == HORIZON else float(window)

    def conditional(point: Point, window: int) -> float:
        return _expected_games(point, curve, window)

    def score(term: str, window: int) -> float:
        chosen = flat if term == "flat" else conditional
        return mean(
            [
                _scaled_error(_line(point, chosen(point, window)), point.actual, spread)
                for point in points
            ]
        )

    before, after = score("flat", HORIZON), score("conditional", HORIZON)
    before_near, after_near = score("flat", NEAR), score("conditional", NEAR)
    table(
        f"Step 3a. The error in the fit's own units, {HORIZON}-day checkpoints on {data.season}",
        ("games term", f"{HORIZON}-day window", f"{NEAR} days discounted, then undiscounted"),
        (
            (GAMES_TERMS[0], f2(before), f2(before_near)),
            (GAMES_TERMS[1], f2(after), f2(after_near)),
        ),
    )

    # The decomposition. Both halves are read off the same lines, so they are
    # two attributions of one error and not two measurements that must add up:
    # the metric is absolute, so they do not sum to the total.
    rate_error = mean(
        [
            _scaled_error(_line(point, float(point.actual_games)), point.actual, spread)
            for point in points
        ]
    )
    games_error = mean(
        [
            _scaled_error(
                _line(point, float(point.team_games) * FLAT_DISCOUNT),
                _line(point, float(point.actual_games)),
                spread,
            )
            for point in points
        ]
    )
    oracle = mean(
        [_scaled_error(_line(point, float(point.played)), point.actual, spread) for point in points]
    )
    table(
        f"Step 3b. Where the {HORIZON}-day error sits, in the fit's own units",
        ("reading", "error", "what it holds"),
        (
            (GAMES_TERMS[0], f2(before), "the line the product publishes"),
            (
                "the same rate, given the games he actually played",
                f2(rate_error),
                "the rate error, with the games term made perfect",
            ),
            (
                "the games term alone",
                f2(games_error),
                "the flat games term against the rate's own line",
            ),
            (
                "oracle: his team's games x his real play share",
                f2(oracle),
                "the best a games term could do, status-blind",
            ),
            ("a zero line", f2(zero), "the floor a broken forecast sits above"),
        ),
    )
    print(
        f"\n# Flat {f2(before)}, given his real games {f2(rate_error)}: "
        f"**{removed(before, rate_error)} of the {HORIZON}-day error is the games term**, "
        f"and the status-conditional term recovers {removed(before, after)} of it."
    )

    lens = _lens(session, data)
    if lens is None:
        print("\n# no currency lens for this season; the currency table is skipped")
        return
    average, distributions = lens
    rows = []
    for label, term in ((GAMES_TERMS[0], flat), (GAMES_TERMS[1], conditional)):
        found = mean(
            [
                _category_error(point, term(point, HORIZON), average, distributions)
                for point in points
            ]
        )
        rows.append((label, f"{found:.4f}"))
    flat_cat = mean(
        [
            _category_error(point, point.team_games * FLAT_DISCOUNT, average, distributions)
            for point in points
        ]
    )
    cond_cat = mean(
        [
            _category_error(point, _expected_games(point, curve, HORIZON), average, distributions)
            for point in points
        ]
    )
    table(
        "Step 3c. The same in the recommender's currency: categories a week",
        ("games term", "mean absolute error", "share removed"),
        (
            (GAMES_TERMS[0], f"{flat_cat:.4f}", "—"),
            (GAMES_TERMS[1], f"{cond_cat:.4f}", removed(flat_cat, cond_cat)),
        ),
    )
    print("# four decimals: the quantity is a hundredth of a category a week.")


def _play_rates(lines: Sequence[Line], cases: Sequence[Case]) -> dict[tuple[str, str], float]:
    """The empirical play rate for a (status, reason class), pooled.

    A cell with fewer lines than `MIN_CELL` is too thin to read on its own, so
    it falls back to its status's own rate, and a status with too few lines
    falls back to the whole population's. That is the same fallback
    `_rate_for` applies at use, and it is why a thin cell cannot invent an
    expectation out of three lines.
    """
    cells: dict[tuple[str, str], list[int]] = defaultdict(list)
    by_status: dict[str, list[int]] = defaultdict(list)
    for line in lines:
        cells[(line.status, line.kind)].append(1 if line.played else 0)
        by_status[line.status].append(1 if line.played else 0)
    overall = mean(
        [1.0 if line.played else 0.0 for line in lines]
        + [1.0 if case.played else 0.0 for case in cases]
    )
    out: dict[tuple[str, str], float] = {}
    for key, found in cells.items():
        if len(found) >= MIN_CELL:
            out[key] = mean([float(v) for v in found])
            continue
        siblings = by_status.get(key[0], [])
        out[key] = mean([float(v) for v in siblings]) if len(siblings) >= MIN_CELL else overall
    return out


def _rate_for(rates: Mapping[tuple[str, str], float], status: str, kind: str) -> float:
    """The play rate for a status and reason class, its status, or the pool."""
    if (status, kind) in rates:
        return rates[(status, kind)]
    found = [value for (st, _kind), value in rates.items() if st == status]
    return mean(found) if found else 0.9


def _window_rates(session: Session, data: SeasonData) -> dict[str, float]:
    """The window play rate by status, measured on the checkpoints themselves.

    Two passes: build the points with the next-game rates so the population and
    its windows exist, then read each status's played-over-team-games off those
    same points. Doing it off the checkpoints rather than off the whole season
    is what keeps the term and the thing it is scored against on one
    population.
    """
    first = _points(session, data, {})
    return window_rate(first)


def _window_rate_for(
    windows: Mapping[str, float] | None,
    rates: Mapping[tuple[str, str], float],
    status: str | None,
    kind: str,
) -> float:
    """The games term's play rate: the window rate, else the next-game rate.

    The window rate is what the term needs and is measured over the same
    checkpoints; the next-game rate (step 1b) is the fallback where the window
    pass has no cell, which keeps a status the checkpoints never saw from
    silently becoming zero.
    """
    if windows:
        found = windows.get(status or "silent")
        if found is not None:
            return found
    return _rate_for(rates, status, kind) if status is not None else 0.9


def window_rate(points: Sequence[Point]) -> dict[str, float]:
    """The share of a window's team games played, by the morning's status.

    This is the number a games term multiplies the window by, and it is *not*
    step 1's play rate. Step 1 asks "did he play the next game", and answers
    about 0.93 for silence; the window asks "what share of the next 28 days of
    his team's games does he appear in", and answers about 0.74, because a
    season's worth of load management, minor knocks and bench nights are in the
    denominator. The two differ by a lot and only the second one is a games
    term.
    """
    buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for point in points:
        key = point.status if point.status is not None else "silent"
        buckets[key][0] += point.played
        buckets[key][1] += point.team_games
    return {
        key: (played / team_games if team_games else 0.0)
        for key, (played, team_games) in buckets.items()
    }


def silent_rate(cases: Sequence[Case]) -> float:
    """What silence is worth for the *next* game: step 1c's own number.

    Not the named lines' average: the two populations differ by construction,
    since a man is named because something was wrong. Reported on the page, but
    the games term uses `window_rate`, which is the same question asked over a
    window rather than over one night.
    """
    return mean([1.0 if case.played else 0.0 for case in cases]) if cases else 0.9


def _return_curve(session: Session, seasons: Sequence[int]) -> dict[int, float]:
    """P(back within `HORIZON` days | already N days out), pooled.

    The same quantity step 2b prints, read here as the number a games term
    multiplies the rest of a window by. Keyed by N; a run that had already
    ended by N days cannot be asked, so only runs still going count.
    """
    lengths: list[int] = []
    for season in seasons:
        for player_id, _team in _mentioned(session, season):
            lengths.extend(run.days for run in absences(session, player_id, season))
    curve: dict[int, float] = {}
    for days_out in (0, *OUT_DAYS):
        still = [n for n in lengths if n > days_out]
        if not still:
            continue
        curve[days_out] = mean([1.0 if n <= days_out + HORIZON else 0.0 for n in still])
    return curve


def _expected_games(point: Point, curve: Mapping[int, float], window: int) -> float:
    """The status-conditional games term for a window of `window` team games.

    Three pieces, which is the whole design a live games term would need:

    * a man the league did **not** name plays, at the silence rate -- which is
      step 1c's number, about 0.93, and is the case that matters most because
      it is most of the population;
    * a man it *did* name plays the near games at the empirical play rate for
      his status and reason class (step 1b), which for an Out man is about
      zero and for a Questionable man about a half;
    * and if the morning has him Out and has had him Out for N days, the rest
      of the window is discounted by the return curve read at N (step 2b).

    The near window and the full window are the same arithmetic with `window`
    set to seven and to twenty-eight, which is the brief's sensitivity.

    A man who is *not* Out keeps the same rate across the window rather than
    dropping to the curve: a Questionable man who plays tonight has not been
    out, and the curve is about recovering from an absence, not about a
    day-to-day tag.
    """
    if window <= 0 or point.team_games <= 0:
        return 0.0
    games = float(min(window, point.team_games))
    if point.days_out is None:
        # Silence, an Available/Probable/Questionable/Doubtful line: the same
        # play rate across the window.
        return games * point.play_rate
    # Out, and N days into it: tonight is the play rate for Out (about zero)
    # and the rest of the window is the return curve's answer at N.
    back = curve.get(min(point.days_out, max(curve)), 1.0)
    return point.play_rate + (games - 1) * back if games >= 1 else point.play_rate


def _points(
    session: Session,
    data: SeasonData,
    rates: Mapping[tuple[str, str], float],
    windows: Mapping[str, float] | None = None,
) -> list[Point]:
    """Every rostered checkpoint with games on both sides and an ESPN prior."""
    espn = _espn_rates(session, data.season)
    out: list[Point] = []
    for period in CHECKPOINTS:
        start = data.day_of_period.get(period)
        if start is None:
            continue
        for player_id, rows in data.counts.items():
            first = data.first_rostered.get(player_id)
            if first is None or first >= period or player_id not in espn:
                continue
            # Counted on the scoring period, which is the product's own key:
            # a meaningful share of box rows carry no `game_date`, and a
            # date-keyed count silently drops them (`check_blend_against_product`
            # catches it -- it read 5 games against the product's 8 before this).
            before = [row for row in rows if row[0] < period]
            after = [row for row in rows if period <= row[0] < period + HORIZON]
            if not before or not after:
                continue
            team = data.team_on(player_id, start) or data.nearest_team(player_id, start)
            window = data.window_days(team, period, HORIZON)
            best = data.newest(player_id, start)
            status = best[2] if best is not None else None
            kind = reason_class(best[3]) if best is not None else "silent"
            out.append(
                Point(
                    player_id=player_id,
                    day=period,
                    start=start,
                    games=len(before),
                    to_date=_rate_of(before),
                    recent=_rate_of(_recent(before, period)),
                    recent_games=len(_recent(before, period)),
                    espn=espn[player_id],
                    actual=_rate_of(after),
                    actual_games=len(after),
                    team_games=len(window),
                    played=sum(1 for day in window if data.boxes.get((player_id, day))),
                    status=status,
                    kind=kind,
                    play_rate=_window_rate_for(windows, rates, status, kind),
                    days_out=(_days_out(data, player_id, start) if status in RULED_OUT else None),
                )
            )
    return out


def _recent(
    before: Sequence[tuple[int, date | None, dict[str, float]]], period: int
) -> list[tuple[int, date | None, dict[str, float]]]:
    """The games inside the recent window, on the product's own rule.

    `knowable` counts the recent window as `scoring_period >= day -
    RECENT_DAYS`, so this is that comparison and nothing else.
    """
    low = period - RECENT_DAYS
    return [row for row in before if row[0] >= low]


def _days_out(data: SeasonData, player_id: int, day: date) -> int | None:
    """How many days he had already been Out on this morning, or None."""
    for first, last in absences_local(data, player_id):
        if first <= day <= last:
            return (day - first).days + 1
    return None


def absences_local(data: SeasonData, player_id: int) -> list[tuple[date, date]]:
    """The runs of Out days for a man, read off the season's lines in memory.

    The same rule `app.injuries.absences` applies -- a run opens and closes on
    an Out morning, and a silent day carries it across only when the league had
    said nothing about his team either -- computed here from the lines already
    loaded rather than a query per player, which the checkpoints need in a
    loop. `check_absences_against_product` holds the two to the same answer.
    """
    his = data.lines.get(player_id)
    if not his:
        return []
    days = sorted({row[1] for row in his})
    out: list[tuple[date, date]] = []
    open_from: date | None = None
    last_out: date | None = None
    day = days[0]
    while day <= days[-1]:
        best = data.newest(player_id, day)
        if best is not None:
            if best[2] in RULED_OUT:
                if open_from is None:
                    open_from = day
                last_out = day
            else:
                if open_from is not None and last_out is not None:
                    out.append((open_from, last_out))
                open_from = last_out = None
        else:
            team = data.team_on(player_id, day)
            if team is not None and _team_filed(data, team, day):
                # His team filed about this day and did not name him: he is
                # fit, and the run ends. "Filed about this day" is the same
                # `game_date >= day` bound `status_as_of` uses -- a report
                # filed on the 8th *about the 9th* says nothing about the 8th.
                if open_from is not None and last_out is not None:
                    out.append((open_from, last_out))
                open_from = last_out = None
        day += timedelta(days=1)
    if open_from is not None and last_out is not None:
        out.append((open_from, last_out))
    return out


def _team_filed(data: SeasonData, team: int, day: date) -> bool:
    """Whether the league published a line about this team for `day` or later.

    The same bound as `app.injuries.absences`' own "his team had filed" test,
    which reads the team's rows through `_newest_visible` at `morning_of(day)`:
    a line about a later game counts, and one that has expired does not.
    """
    at = morning_of(day)
    filed = data.filed
    for reported_at, game_date in filed.get(team, ()):
        if reported_at <= at and game_date >= day:
            return True
    return False


# ---------------------------------------------------------------------------
# the blend, the metric, and the currency lens
# ---------------------------------------------------------------------------


CountRow = tuple[int, "date | None", dict[str, float]]


def _rate_of(games: Sequence[CountRow]) -> dict[str, float]:
    """The per-game rate over a set of played games. No games, a zero line."""
    if not games:
        return dict.fromkeys(COUNTS, 0.0)
    return {key: sum(row[2].get(key, 0.0) for row in games) / len(games) for key in COUNTS}


def _line(point: Point, games: float) -> dict[str, float]:
    """The knowable per-game line spread over `games` games, per game.

    The rate is `app.scoring.knowable`'s arithmetic on this record: to date
    shrunk toward the preseason projection at n/(n + PRIOR_GAMES), then
    RECENT_WEIGHT of the last RECENT_DAYS on top. It is re-implemented rather
    than called because the product's `knowable` reads the database a
    player-day at a time and this loop wants the season in memory;
    `check_blend_against_product` holds the two to the same answer.

    The games term enters as a scale: a line of `rate x games` spread over the
    games the window really held is `rate x games / actual_games`. A games term
    below the truth therefore scales the line down, which is exactly the
    penalty the flat discount pays -- and is why the split in step 3b is
    between a rate term and a scale on it, not between two independent errors.
    """
    base = _mix(point.to_date, point.espn, point.games / (point.games + PRIOR_GAMES))
    rate = _mix(point.recent, base, RECENT_WEIGHT) if point.recent_games else base
    window = max(point.actual_games, 1)
    return {key: value * games / window for key, value in rate.items()}


def _scaled_error(
    forecast: Mapping[str, float], truth: Mapping[str, float], spread: Mapping[str, float]
) -> float:
    """One checkpoint's error in the fit's own units.

    The sum over `COUNTS` of the absolute difference in per-game rate, each
    divided by that count's spread. A count whose spread is zero carries no
    information and is left out. This is `scripts/projection_prior.py`'s
    quantity, reproduced so the two studies are on one scale.
    """
    total = 0.0
    for key in COUNTS:
        deviation = spread.get(key, 0.0)
        if deviation > 0:
            total += abs(forecast.get(key, 0.0) - truth.get(key, 0.0)) / deviation
    return total


def _spreads(points: Sequence[Point]) -> dict[str, float]:
    """Each count's spread over the checkpoints, which the error divides by."""
    return {
        key: (
            statistics.pstdev([point.actual[key] for point in points]) if len(points) > 1 else 0.0
        )
        for key in COUNTS
    }


def _espn_rates(session: Session, season: int) -> dict[int, Rate]:
    """ESPN's stored preseason projection, per game, keyed on `players.id`."""
    if not usable(season):
        return {}
    out: dict[int, Rate] = {}
    for stat in session.scalars(
        select(PlayerSeasonStat).where(
            PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected"
        )
    ):
        totals = {
            key: float(value)
            for key, value in (stat.raw_totals or {}).items()
            if isinstance(value, int | float)
        }
        rate = rate_from_totals(totals, float(stat.games_played or 0.0))
        if rate is not None:
            out[int(stat.player_id)] = rate
    return out


def _lens(session: Session, data: SeasonData) -> tuple[CategoryLine, Sequence[object]] | None:
    """The currency lens: the league-average roster and its distributions."""
    from app.pickups.judge import standard_lens

    try:
        lens = standard_lens(session, data.league_season, CHECKPOINTS[0])
    except Exception:
        return None
    return lens.average, lens.distributions


def _category_error(
    point: Point,
    games: float,
    average: CategoryLine,
    distributions: Sequence[object],
) -> float:
    """One checkpoint's error in categories a week, for a given games term.

    Both lines are priced inside the same league-average roster over the same
    period, so the only thing that differs between the arms is the rate and the
    games term. The truth is the man's actual per-game rate, and the forecast is
    his line scaled by what the games term says relative to the games he really
    played -- which is exactly how the fit's own units read it, and is why a
    games term below the truth is a smaller line here too.

    This is `scripts/projection_prior.py`'s currency comparison with the games
    term moved from a flat number to the status-conditional one.
    """
    window = max(point.actual_games, 1)
    scale = games / window
    forecast = marginal(average, _over(_scaled(point, scale), window), distributions)  # type: ignore[arg-type]
    truth = marginal(average, _over(point.actual, window), distributions)  # type: ignore[arg-type]
    return abs(forecast - truth)


def _scaled(point: Point, scale: float) -> dict[str, float]:
    """A point's line, scaled by a games term expressed against actual games."""
    line = _line(point, float(point.actual_games))
    return {key: value * scale for key, value in line.items()}


def _over(rate: Mapping[str, float], games: float) -> CategoryLine:
    """A per-game rate line spread over `games` games."""
    return CategoryLine({key: rate.get(key, 0.0) * games for key in COUNTS}, round(games))


def check_blend_against_product(session: Session, data: SeasonData) -> str:
    """Hold `_line` to `app.scoring.knowable.knowable` on the same inputs.

    The product's function is the definition and this script's is a copy that
    works on a season already in memory. If they ever disagree the measurement
    is of a different blend than the product runs, so this is checked rather
    than asserted. The games term is held at his actual games, where `_line`
    is the plain blend the product's `per_game` is.
    """
    from app.scoring.knowable import knowable

    worst = 0.0
    checked = 0
    for point in _points(session, data, {}):
        if checked >= 200:
            continue
        expected = knowable(session, point.player_id, data.season, point.day)
        mine = _line(point, float(point.actual_games))
        for key in COUNTS:
            worst = max(worst, abs(mine[key] - expected.per_game.get(key)))
        checked += 1
    return (
        f"blend checked against app.scoring.knowable on {checked} player-checkpoints, "
        f"worst gap {worst:.2e}"
    )


def check_absences_against_product(session: Session, data: SeasonData) -> str:
    """Hold `absences_local` to `app.injuries.absences` on the same player.

    The local reader is the product's rule computed from the lines already in
    memory, which the checkpoint loop needs; a drift between them would make
    step 3's return discount about a different quantity than step 2's.
    """
    worst = ""
    checked = 0
    for player_id in list(data.lines)[:400]:
        theirs = [(run.first, run.last) for run in absences(session, player_id, data.season)]
        mine = absences_local(data, player_id)
        if theirs != mine:
            worst = f"player {player_id}: product {theirs[:3]} vs local {mine[:3]}"
            break
        checked += 1
    return (
        f"absences checked against app.injuries.absences on {checked} players, {worst or 'no gap'}"
    )


# ---------------------------------------------------------------------------
# step 4: the intraday sample
# ---------------------------------------------------------------------------


def step_four(session: Session, min_snapshots: int) -> None:
    """How often a morning status moved by tip-off, on the full-cadence dates.

    "Morning" is before eleven Eastern and "tip-off" is from five in the
    evening, which is the window the league's own reports moved inside: the
    nine o'clock report is the one `morning_of` reads, and an evening report is
    the last word before a late game. `app.injuries.morning_of` is the product's
    moment and is used for the morning read wherever a date has one snapshot
    per morning; the intraday dates have several, so the earliest and the
    latest are taken instead.
    """
    print("\n## Step 4. The intraday sample")

    stamps: dict[date, set[datetime]] = defaultdict(set)
    for day, at in session.execute(
        select(InjuryReport.game_date, InjuryReport.reported_at).distinct()
    ).all():
        stamps[day].add(at)
    full = sorted(day for day, found in stamps.items() if len(found) >= min_snapshots)
    if not full:
        print(f"\n# no game date carries {min_snapshots} snapshots or more: this step is empty")
        return
    first, last = full[0], full[-1]
    print(
        f"\n# The full-cadence dates are **{first} to {last}**, {len(full)} of them, "
        f"each with {min_snapshots} snapshots or more."
    )

    early: dict[tuple[int, date], str] = {}
    late: dict[tuple[int, date], str] = {}
    for player_id, reported_at, game_date, status in session.execute(
        select(
            InjuryReport.player_id,
            InjuryReport.reported_at,
            InjuryReport.game_date,
            InjuryReport.status,
        ).where(
            InjuryReport.player_id.is_not(None),
            InjuryReport.status.is_not(None),
            InjuryReport.game_date >= first,
            InjuryReport.game_date <= last,
        )
    ).all():
        key = (int(player_id), game_date)
        hour = reported_at.astimezone(EASTERN).hour
        if hour < 11:
            early.setdefault(key, str(status))
        if hour >= 17:
            late[key] = str(status)

    moved = dict.fromkeys(("same", "up", "down"), 0)
    # per morning status: [worse by evening, better, unchanged, total]
    by_status: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for key, status in early.items():
        final = late.get(key)
        if final is None:
            continue
        counts = by_status[status]
        counts[3] += 1
        if SEVERITY.get(final, 2) > SEVERITY.get(status, 2):
            moved["up"] += 1
            counts[0] += 1
        elif SEVERITY.get(final, 2) < SEVERITY.get(status, 2):
            moved["down"] += 1
            counts[1] += 1
        else:
            moved["same"] += 1
            counts[2] += 1

    rows = [
        [status, counts[3], share(counts[1], counts[3]), share(counts[0], counts[3])]
        for status, counts in sorted(by_status.items(), key=lambda item: item[1][3], reverse=True)
        if counts[3] >= MIN_CELL
    ]
    table(
        "Step 4. Morning (before 11:00 ET) against the evening's last word (from 17:00 ET)",
        ("morning status", "n", "worse by evening", "better by evening"),
        rows,
    )
    total = sum(moved.values())
    print(
        f"\n# Of {total} pairs, **{moved['up']} moved up, {moved['down']} down, "
        f"{moved['same']} did not move** ({share(moved['up'] + moved['down'], total)} changed "
        "at all)."
    )


# ---------------------------------------------------------------------------
# step 5: the beneficiary hook
# ---------------------------------------------------------------------------


def step_five(session: Session, seasons: Sequence[int]) -> None:
    """What the box-score absences a beneficiary ticket would price really were."""
    print("\n## Step 5. The beneficiary hook")

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    for season in seasons:
        data = load_season(session, season)
        for player_id, runs in _box_absences(data).items():
            for run in runs:
                kind = _classify_absence(data, player_id, run)
                counts[str(season)][kind] += 1
                counts["pooled"][kind] += 1
                totals[str(season)] += 1
                totals["pooled"] += 1

    labels = (
        "injury",
        "illness",
        "rest_management",
        "assignment",
        "suspension",
        "trade",
        "unreported",
    )
    rows = []
    for label in (*map(str, seasons), "pooled"):
        if not totals.get(label):
            continue
        rows.append(
            [
                label,
                totals[label],
                *(share(counts[label].get(kind, 0), totals[label]) for kind in labels),
            ]
        )
    table(
        f"Step 5. Beneficiary absences ({BENEFICIARY_MIN_GAMES}+ consecutive team games "
        f"missed by a man with {BENEFICIARY_ROTATION_GAMES}+ played) by what the report said",
        ("season", "absences", *(CLASS_LABEL[kind] for kind in labels)),
        rows,
    )


def _box_absences(data: SeasonData) -> dict[int, list[tuple[date, date, int]]]:
    """Every rotation man's run of consecutive team games he did not play.

    The beneficiary definition as the brief states it: a man with
    `BENEFICIARY_ROTATION_GAMES` games already played, missing
    `BENEFICIARY_MIN_GAMES` or more consecutive team games, read from box
    scores. Returned as player id -> runs, each (first, last, games missed).
    """
    out: dict[int, list[tuple[date, date, int]]] = {}
    for player_id, played_rows in data.played_games.items():
        played = {day for day, _minutes in played_rows}
        if len(played) < BENEFICIARY_ROTATION_GAMES:
            continue
        teams = {team for _at, team in data.player_teams.get(player_id, ())}
        days = sorted({day for team in teams for day in data.team_days.get(team, set())})
        runs: list[tuple[date, date, int]] = []
        start: date | None = None
        last: date | None = None
        count = 0
        for day in days:
            if day in played:
                if start is not None and last is not None and count >= BENEFICIARY_MIN_GAMES:
                    runs.append((start, last, count))
                start, last, count = None, None, 0
            elif (player_id, day) in data.boxes:
                # A box row with `played` false is a game he was there to miss;
                # no row at all means he was not on the team that night.
                if start is None:
                    start = day
                last = day
                count += 1
        if start is not None and last is not None and count >= BENEFICIARY_MIN_GAMES:
            runs.append((start, last, count))
        if runs:
            out[player_id] = runs
    return out


def _classify_absence(data: SeasonData, player_id: int, run: tuple[date, date, int]) -> str:
    """What the report says a box-score absence was.

    Read from the beginning of the run: a status on one of its first three
    days, filed about the team he was then on, is the league's own word for it.
    Nothing at all through the run, with a team change somewhere in the season,
    is a trade. Restated in docs/availability.md.
    """
    first, last, _games = run
    for offset in range(3):
        day = first + timedelta(days=offset)
        if day > last:
            break
        best = data.newest(player_id, day)
        if best is None or best[4] != data.team_on(player_id, day):
            continue
        kind = reason_class(best[3])
        if kind in ("injury", "illness", "rest_management"):
            return kind
        if kind == "g_league":
            return "assignment"
        if kind == "suspension":
            return "suspension"
        if kind == "trade":
            return "trade"
    if len({team for _at, team in data.player_teams.get(player_id, ())}) > 1:
        return "trade"
    return "unreported"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    started = time.time()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(SEASONS))
    ap.add_argument("--intraday", type=int, default=15, help="snapshots a date to count as full")
    ap.add_argument("--checkpoints", type=int, nargs="+", default=list(CHECKPOINTS))
    ap.add_argument("--why", action="store_true", help="the per-season line accounting")
    args = ap.parse_args()

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        print(f"# Availability: how much of the projection's error is games, {args.seasons}")
        data = {season: load_season(session, season) for season in args.seasons}
        for season in args.seasons:
            lines_named = sum(len(found) for found in data[season].lines.values())
            print(
                f"# {season}: {lines_named} lines naming a player, "
                f"{len(data[season].team_days)} teams scheduled"
            )
        if args.why:
            print("\n## The accounting: where a season's player-day lines go")
            rows: list[list[object]] = []
            for season in args.seasons:
                found = counts_for(data[season])
                rows.append(
                    [
                        season,
                        *(
                            found[key]
                            for key in (
                                "player_days",
                                "about_a_later_game",
                                "on_a_game_day",
                                "no_box_row",
                                "scored",
                            )
                        ),
                    ]
                )
            table(
                "A line is scored only when his team played that day and he has a box row",
                (
                    "season",
                    "player-days",
                    "about a later game",
                    "on a game day",
                    "no box row",
                    "scored",
                ),
                rows,
            )

        step_one(data)
        step_two(session, args.seasons)
        step_four(session, args.intraday)
        step_five(session, args.seasons)
        print(f"\n# {check_blend_against_product(session, data[CHECKPOINT_SEASON])}")
        print(f"# {check_absences_against_product(session, data[CHECKPOINT_SEASON])}")
        step_three(session, data[CHECKPOINT_SEASON])

    print(f"\n# wall time {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
