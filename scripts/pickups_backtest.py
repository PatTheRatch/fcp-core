#!/usr/bin/env python3
"""The 2026 backtest of the pickup recommender (docs/pickups.md section 4.6).

Usage:
    python scripts/pickups_backtest.py                 # full run, writes the doc
    python scripts/pickups_backtest.py --limit 2       # a quick smoke run

WHAT THIS MEASURES

For every team, on the first and fourth day of every 2026 matchup period, this
replays the two recommender questions -- who to stream this week
(`app.pickups.stream`) and who to add for the rest of the season
(`app.pickups.season`) -- from the state knowable on that day, then scores what
it recommended against what actually happened.

THE SCORE IS CATEGORIES, NOT COMPOSITE

The first cut of this script scored a move by the added player's composite
(PTS + REB + AST + STL + BLK + 3PM - TO) less the dropped player's, and found
-3.2 composite a day at a 20% win rate. That number is not a finding about the
recommender; it is a finding about composite. The recommender trades points for
the categories that are close on purpose -- that is what expected categories won
means -- and composite punishes exactly that trade. A move that gives up eleven
points a night to win blocks and steals is a good move and a terrible composite.

So a move is scored in the currency it was chosen in: **categories won**,
by replaying the matchup that actually happened with the swap in it.

- **The week.** Re-solve the lineup, day by day, over the rest of the matchup
  period -- once with the swap and once without it. On each day the roster is
  what `daily_lineup_slots` says the team held, the swap is applied to it, and
  the ten starting slots are filled by the recommender's own seating rule
  (`app.pickups.stream._seat`: take the men with a game in order of value and
  keep each one the matching can still seat). The day's line is the seated men's
  real box scores. Rebuild the nine totals of each side, count the categories
  each beats the opponent's real period totals in (`matchup_team_stats`), and
  subtract. +1 means the swap flipped one category.
- **The season.** The same replay over the next 30 days, summed across every
  matchup period that window touches.
- **The baseline.** The league's own one-for-one swaps of 2026, scored by the
  same code and the same re-solve: a real move is replayed *backwards* (put the
  dropped man back, take the added man out) and the sign flipped, so "what the
  move was worth" means the same thing for a recommendation and for a real
  claim. One currency, one comparison.
- **Calibration.** Beside them, what the recommender CLAIMED (the judgement's
  net over both horizons, `app.pickups.judge`) against what it DELIVERED. A tool
  that says +0.5 and delivers +0.1 is not the same tool as one that says +0.5
  and delivers +0.5, even at the same win rate.

WHY BOTH SIDES ARE RE-SOLVED

The first category-scored cut capped the added man at the number of days the
dropped man had started, so a move that dropped a man who was not starting --
which is exactly what an empty-day pickup does -- scored zero by construction.
Sixty per cent of named streaming moves scored exactly zero, and a measurement
that cannot separate a good stream from a bad one cannot tune a hurdle.

Re-solving the swapped roster fixes that, but only if the unswapped roster is
re-solved too. Scoring a re-solved lineup against the manager's actual starts
would pay every move the difference between a well-set lineup and a badly-set
one, which is not what the move did. So both sides are seated by the same rule
and the difference is the move alone. The gap between the re-solve and what the
manager really did is printed beside the run as a diagnostic (`drift`) and
enters no score.

The seating order is what each man had averaged *before* that day, not what he
posted on it: a manager sets Tuesday's lineup on Tuesday morning. Seating by
the night's own box score would hand every counterfactual a lineup nobody could
have set, and would flatter every pickup.

ONE DEFECT THIS SCRIPT WORKS AROUND

1. NO 2026 SCHEDULE. `app/pickups/state.py` counts a player's remaining games
   from `pro_team_games`, which holds no 2026 rows at all (only 2027 has any:
   the listener that writes it started in 2027). With no schedule every player
   has no game days, `stream._Week.project` seats nobody, and every move's
   change in expected wins is exactly 0.0 -- so no hurdle can ever be cleared
   and the whole sweep is identically zero. `app/pickups/bids.py` already notes
   the gap. WORKAROUND: `rebuild_schedule` reconstructs a player-keyed schedule
   from the box scores. A played line on scoring period N *is* a game on N, and
   `player_game_stats` carries the opponent and the date, so the fact is
   recoverable at the grain the recommender actually consumes. Verified: 30 NBA
   teams, 2,461 team-game slots against the 2,460 a full 82-game season needs,
   no player with two games on one day, no team with two games on one day.

It is installed by attribute substitution on `app.pickups.state`, removed in a
`finally`, and changes nothing on disk.

THE MATCHUP TOTALS LEAK, WHICH IS NOW THE PRODUCT'S JOB

This script used to carry a second workaround. `state.load_team_week` read
`my_totals` and `opp_totals` from `matchup_team_stats`, which holds one row per
(matchup, team, category) -- the period's FINAL total, with no day column to cap
it by -- so at day N of a period the recommender saw the whole period, including
days that had not happened. `rebuild_posted` substituted a sum of the started
lines, capped by a module-level `_POSTED_CAP` the replay set before each call.

`app.pickups.state._posted` now does that itself: it takes the day, keeps
ESPN's row only when the database has nothing on or after it (a genuinely live
morning), and otherwise sums the started lines. So the replay installs nothing
and the numbers below measure the recommender the product actually runs.

One thing did move with it. The old cap was inclusive (`scoring_period <= N`),
which double-counted day N, because `scoring_periods_remaining` begins at N and
the projection adds that day on top. The product's boundary is exclusive, and
the write-up records what that changed.

WHAT IS DELIBERATELY NOT PATCHED

The knowable line (`app.scoring.knowable`) already filters to games before
`today`, and the projection's minutes tilt does the same, so neither leaks.
`app.pickups.bids.bid_fit` reads the whole season's transactions, which is a
look-ahead if a bid price is taken as advice; the replay runs the stream side
with `bids=False` and never scores a bid, so nothing here is priced off future
claims. Injury status is unavailable historically (no 2026 status snapshots),
so every player is treated as available and the stash logic is under-served;
the write-up says so.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

# Run by path, so `scripts/` is on sys.path and the repo root is not. Without
# this, `app` resolves to whichever checkout the interpreter's venv installed,
# which is not this one when a worktree borrows another checkout's .venv --
# and a backtest that measures a different copy of the recommender measures
# nothing. The streaming CLI does the same for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft.lineup import max_matching
from app.draft.pool import lineup_for
from app.draft.targets import CategoryDistribution, category_distributions
from app.draft.valuation import INVERTED_CATEGORIES
from app.pickups.projection import _MINUTES_KINDS
from app.pickups.season import SeasonReport, season_recommendations
from app.pickups.state import _PRO_TEAM_IDS, season_calendar
from app.pickups.stream import StreamReport, stream_recommendations, weight
from app.scoring.lines import COUNTS, EMPTY, CategoryLine

SEASON = 2026
REPORT = Path("docs/pickups_backtest.md")

#: The historical free-agent definition of `scripts/waiver_value.py`: a played
#: line that day, and no `daily_lineup_slots` row that scoring period.
FREE_AGENT = """
    NOT EXISTS (
      SELECT 1 FROM daily_lineup_slots dls
      JOIN teams t ON t.id = dls.team_id
      WHERE t.league_season_id = :league_season_id
        AND dls.scoring_period = pgs.scoring_period
        AND dls.player_id = pgs.player_id
    )
"""

#: Days the season score replays over. The week score needs no window: it is
#: the matchup period the decision day falls in, whatever its length.
SEASON_WINDOW = 30

#: The hurdles swept. The streaming bar is read against the judgement's net
#: over both horizons and the season bars against the same net per week
#: (`app.pickups.judge.Judgement`), which is the unit each constant was
#: written in (docs/pickups.md sections 4.3 and 4.4).
STREAM_GRID: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20)
#: (paid, free) pairs, the free bar below the paid one as the constants have
#: it. The 2026-09-18 run's grid ran the free bar above the paid one by
#: mistake; adds into an open place are rare enough that its cells measured
#: the paid bar alone, which is how they were read.
SEASON_GRID: tuple[tuple[float, float], ...] = ((0.05, 0.02), (0.10, 0.05), (0.20, 0.10))

#: A setting is eligible only when it still says "no move" often enough to be a
#: filter rather than a machine gun, and beats the league's own moves on the
#: mean categories it delivers. The bar itself is measured in the run, since
#: it is now in categories rather than the composite the note quoted.
MIN_NO_MOVE = 0.20

#: Decision points: the first and the fourth day of each matchup period.
DECISION_OFFSETS = (0, 3)

#: How many moves from the top of each decision are scored. `stream` reports
#: five (one per added player) and `season` three, so five covers both.
TOP_N = 5


@dataclass
class Scored:
    """One recommended move, and the categories it actually returned."""

    team_id: int
    day: int
    kind: str
    rank: int
    cleared: bool
    added_id: int
    added_name: str
    dropped_id: int | None
    dropped_name: str | None
    #: What the recommender claimed: the judgement's net over both horizons.
    claimed: float
    #: Weeks that net covers, so a per-week hurdle can be read off it.
    weeks: float
    #: Categories delivered in the matchup period the decision fell in.
    week: float
    #: Categories delivered over the next `SEASON_WINDOW` days.
    season: float
    #: Whether the move seats a man on a day a slot was going empty, which is
    #: the second way `stream.Move.clears` can pass.
    fills_empty_day: bool = False
    #: Whether the move costs FAAB, which picks the season hurdle's paid or
    #: free bar.
    costs_faab: bool = False
    #: Which of the two windows is this move's headline: a stream is judged on
    #: its week, a rest-of-season move on its thirty days.
    horizon: str = "week"

    @property
    def delivered(self) -> float:
        return self.week if self.horizon == "week" else self.season

    @property
    def claimed_here(self) -> float:
        """The claim, rescaled to the window the delivery is measured over.

        The raw claim is the net over both horizons, which spans this matchup
        and every week after it; the delivery is one matchup, or thirty days.
        Comparing them as they stand would say the recommender overclaims by
        a factor of the season's length, which is a fact about arithmetic and
        not about the tool.
        """
        window = 1.0 if self.horizon == "week" else SEASON_WINDOW / 7.0
        return (self.claimed / self.weeks) * window

    @property
    def won(self) -> bool:
        """A move that did not cost categories. Ties count, since a swap that
        changes nothing costs nothing but the transaction."""
        return self.delivered >= 0


@dataclass
class Setting:
    """One hurdle setting's outcome over the whole replay.

    `moves` holds every move scored, which is the top `TOP_N` of each decision
    whether or not it cleared the hurdle. `cleared` filters to the ones the
    hurdle actually recommends, which is the set the headline numbers use: a
    move the tool would not have named is not the tool's performance.
    """

    label: str
    moves: list[Scored] = field(default_factory=list)
    decisions: int = 0
    no_move: int = 0
    #: Decisions where the top-ranked move did not clear the hurdle.
    below_hurdle: int = 0
    #: Run-level counters, shared by every setting.
    counters: dict[str, int] = field(default_factory=dict)
    #: Categories a period the re-solved lineup won that the manager's own
    #: did not. Not a score: the size of the seating assumption, shared by
    #: every setting of the run.
    drift: float = 0.0

    @property
    def cleared(self) -> list[Scored]:
        """The moves the hurdle recommends: ranked first and above the bar."""
        return [m for m in self.moves if m.rank == 0 and m.cleared]

    @property
    def top(self) -> list[Scored]:
        """The top-ranked move at every decision, hurdle or no hurdle."""
        return [m for m in self.moves if m.rank == 0]

    @property
    def n(self) -> int:
        return len(self.cleared)

    @staticmethod
    def _mean(rows: Sequence[Scored]) -> float:
        return statistics.fmean([m.delivered for m in rows]) if rows else 0.0

    @staticmethod
    def _median(rows: Sequence[Scored]) -> float:
        return statistics.median([m.delivered for m in rows]) if rows else 0.0

    @staticmethod
    def _win(rows: Sequence[Scored]) -> float:
        return sum(1 for m in rows if m.won) / len(rows) if rows else 0.0

    @property
    def mean(self) -> float:
        return self._mean(self.cleared)

    @property
    def median(self) -> float:
        return self._median(self.cleared)

    @property
    def win_rate(self) -> float:
        return self._win(self.cleared)

    @property
    def claimed(self) -> float:
        """Mean claim for the moves it named, over the delivered window."""
        rows = self.cleared
        return statistics.fmean([m.claimed_here for m in rows]) if rows else 0.0

    @property
    def unmoved(self) -> float:
        """Share of named moves that changed no category at all.

        The replay gives the added man only as many starts as the place had,
        so a move that drops a man who was not starting delivers exactly
        zero by construction -- including the empty-day moves, whose whole
        point is a start the lineup did not have. This share says how much of
        the measurement is that, rather than a swap that truly did nothing.
        """
        rows = self.cleared
        return sum(1 for m in rows if m.delivered == 0.0) / len(rows) if rows else 0.0

    @property
    def calibration(self) -> float:
        """Delivered over claimed: 1.0 is a tool that means what it says."""
        return self.mean / self.claimed if self.claimed else 0.0

    @property
    def top_mean(self) -> float:
        return self._mean(self.top)

    @property
    def top_win_rate(self) -> float:
        return self._win(self.top)

    @property
    def no_move_rate(self) -> float:
        return self.no_move / self.decisions if self.decisions else 1.0

    def eligible(self, baseline: float) -> bool:
        """Says no often enough to be a filter, and beats the league's own moves."""
        return self.no_move_rate > MIN_NO_MOVE and self.mean > baseline


def rebuild_schedule(
    session: Session,
    season: int,
    pro_team_ids: Iterable[int],
    first_day: int,
    last_day: int,
) -> dict[int, dict[int, date]]:
    """A stand-in for `state.schedule`, keyed on NBA team id as the real one is.

    The contract `state.build_players` relies on is "pro team id -> the scoring
    periods that team plays", read back as `games.get(pro_teams[player_id])`.
    `pro_team_games` holds no 2026 rows, so this reconstructs the same fact from
    the box scores: a player recorded a line on scoring period N, so his NBA
    team played on N, so that period is a game day for every man on that team.

    `player_game_stats` names the opponent, not the player's own team, so the
    player -> NBA team map comes from the weekly roster rows' abbreviation
    (`roster_slots.pro_team`), the same fallback `state.build_players` uses, put
    through `state._PRO_TEAM_IDS` to get the id the schedule is keyed on. The
    unit is a team day, exactly as `pro_team_games` has it; a man traded
    mid-season therefore gets his final team's days all season, which is the
    one place this is coarser than the table it stands in for.
    """
    if first_day > last_day:
        return {}
    rows = session.execute(
        text(
            """
            SELECT rs.pro_team AS abbreviation, pgs.scoring_period,
                   min(pgs.game_date) AS game_at
            FROM player_game_stats pgs
            JOIN (
              SELECT DISTINCT ON (rs.player_id) rs.player_id, rs.pro_team
              FROM roster_slots rs
              JOIN matchups m ON m.id = rs.matchup_id
              JOIN matchup_periods mp ON mp.id = m.matchup_period_id
              JOIN league_seasons ls ON ls.id = mp.league_season_id
              WHERE ls.season = :season AND rs.pro_team IS NOT NULL
              ORDER BY rs.player_id, mp.period DESC
            ) rs ON rs.player_id = pgs.player_id
            WHERE pgs.season = :season
              AND pgs.played AND pgs.minutes > 0
              AND pgs.scoring_period BETWEEN :first_day AND :last_day
            GROUP BY rs.pro_team, pgs.scoring_period
            """
        ),
        {"season": season, "first_day": first_day, "last_day": last_day},
    ).all()
    out: dict[int, dict[int, date]] = {}
    for abbreviation, scoring_period, game_at in rows:
        pro_team_id = _PRO_TEAM_IDS.get(str(abbreviation).upper())
        if pro_team_id is None or game_at is None:
            continue
        out.setdefault(pro_team_id, {})[int(scoring_period)] = game_at.date()
    return out


#: The `player_game_stats` column each `COUNTS` key is summed from, so the
#: SELECT and the fold cannot drift out of order.
_POSTED_COLUMNS: tuple[str, ...] = tuple(COUNTS.values())


@contextmanager
def patched_state() -> Any:
    """Install the reconstructed schedule on `app.pickups.state`, then put it back.

    Attribute substitution rather than a signature change, because the missing
    2026 schedule is a hole in the data, not a bug in the recommender. The
    posted totals used to be substituted here too; `state._posted` takes the
    day itself now, so there is nothing left to install for them.
    """
    import app.pickups.state as state

    original_schedule = state.schedule
    state.schedule = rebuild_schedule
    try:
        yield
    finally:
        state.schedule = original_schedule


# The projection cache this replay used to install by hand now lives in
# `app.pickups.projection` itself (its module docstring), keyed on the same
# `(player, season, today, games, tilt, as_of)` and so still unable to carry
# an answer across decision points. With the cache inside the two functions,
# the five import sites this file used to patch -- `stream` and `bids` for
# `per_game_line`, `season` and `judge` for `rest_of_season_line` -- get it
# without being touched, and the numbers are the ones they always were.


def load_season(session: Session) -> LeagueSeason:
    found = session.query(LeagueSeason).filter(LeagueSeason.season == SEASON).one_or_none()
    if found is None:
        raise SystemExit(f"no league season {SEASON}")
    return found


def decision_points(session: Session, league_season: LeagueSeason) -> list[tuple[int, int]]:
    """(matchup period, scoring period) for the first and fourth day of each."""
    rows = session.execute(
        text(
            "SELECT period, first_scoring_period, final_scoring_period FROM matchup_periods "
            "WHERE league_season_id = :ls ORDER BY period"
        ),
        {"ls": league_season.id},
    ).all()
    out: list[tuple[int, int]] = []
    for period, first, final in rows:
        if first is None or final is None:
            continue
        for offset in DECISION_OFFSETS:
            day = int(first) + offset
            if day <= int(final):
                out.append((int(period), day))
    return out


def team_ids(session: Session, league_season: LeagueSeason) -> list[int]:
    return [
        int(espn)
        for espn in session.scalars(
            text(
                "SELECT espn_team_id FROM teams WHERE league_season_id = :ls ORDER BY espn_team_id"
            ),
            {"ls": league_season.id},
        ).all()
    ]


def team_rows(session: Session, league_season: LeagueSeason) -> dict[int, int]:
    """ESPN's team id -> this database's team row id, which the replay is keyed on."""
    return {
        int(espn): int(row_id)
        for espn, row_id in session.execute(
            text("SELECT espn_team_id, id FROM teams WHERE league_season_id = :ls"),
            {"ls": league_season.id},
        )
    }


def free_agent_pool(session: Session, league_season: LeagueSeason, day: int) -> list[int]:
    """The wire on day N, by the historical definition."""
    return [
        int(player_id)
        for player_id in session.scalars(
            text(
                f"""
                SELECT DISTINCT pgs.player_id
                FROM player_game_stats pgs
                WHERE pgs.season = :season AND pgs.scoring_period = :day
                  AND pgs.played AND pgs.minutes > 0
                  AND {FREE_AGENT}
                """
            ),
            {"season": SEASON, "day": day, "league_season_id": league_season.id},
        ).all()
    ]


@dataclass(frozen=True)
class PeriodWindow:
    """One matchup period's window, in scoring periods."""

    period: int
    first: int
    final: int
    is_playoff: bool


#: Lineup slots that mean a man is held but cannot be started, so the
#: re-solve must not seat him: ESPN's bench, injured reserve, and the marker
#: for a player who left the roster during the period.
UNSEATABLE_SLOTS = ("FA", "IR")


@dataclass
class Replay:
    """One season's played rows, read once, ready to replay any swap.

    Everything the category score needs is a lookup after this: who a team
    held on each day and what each man posted, every player's real box score
    by day (a free agent has no lineup row, so his line has to come from the
    box scores), who may sit in which slot, each period's window, and each
    matchup's opponent and final totals. Six queries for the season, against
    one per move otherwise.
    """

    #: (team row id, scoring period, player id) -> what he posted, started.
    started: dict[tuple[int, int, int], CategoryLine]
    #: (team row id, scoring period) -> every man held that day who could be
    #: started, bench included. The roster the re-solve seats from.
    rostered: dict[tuple[int, int], tuple[int, ...]]
    #: (player id, scoring period) -> his real line, started or not.
    games: dict[tuple[int, int], CategoryLine]
    #: Player id -> the lineup slots he may occupy, from the same
    #: `player_season_stats.eligible_slots` the recommender reads.
    eligible: dict[int, frozenset[str]]
    #: The season's starting lineup, as `app.draft.pool.lineup_for` reads it.
    lineup: tuple[str, ...]
    #: (player id, scoring period) -> the value of what he had averaged
    #: *before* that day, which is what orders the seating. See `_seat`.
    form: dict[tuple[int, int], float]
    periods: tuple[PeriodWindow, ...]
    #: (period, team row id) -> the other side's team row id.
    opponent: dict[tuple[int, int], int]
    #: (period, team row id) -> what ESPN recorded for the whole period.
    totals: dict[tuple[int, int], CategoryLine]
    #: The league's scored categories, in ESPN's order.
    categories: tuple[str, ...]
    #: (team row id, period) -> the whole period's started line, memoized: a
    #: replay asks for the same one on every move of the same decision.
    _team_periods: dict[tuple[int, int], CategoryLine] = field(default_factory=dict)
    #: (team, period, first day, last day) -> the re-solved line with no move,
    #: memoized because every move at a decision point shares it.
    _without: dict[tuple[int, int, int, int], CategoryLine] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, league_season: LeagueSeason) -> Replay:
        columns = ", ".join(f"pgs.{column}" for column in _POSTED_COLUMNS)
        started: dict[tuple[int, int, int], CategoryLine] = {}
        for row in session.execute(
            text(
                f"""
                SELECT dls.team_id, dls.scoring_period, dls.player_id, {columns}
                FROM daily_lineup_slots dls
                JOIN teams t ON t.id = dls.team_id
                JOIN player_game_stats pgs ON pgs.player_id = dls.player_id
                  AND pgs.scoring_period = dls.scoring_period AND pgs.season = :season
                WHERE t.league_season_id = :ls AND dls.started AND pgs.played
                """
            ),
            {"ls": league_season.id, "season": SEASON},
        ):
            started[(int(row[0]), int(row[1]), int(row[2]))] = _line(row[3:])

        rostered: dict[tuple[int, int], list[int]] = {}
        for team, day, player_id in session.execute(
            text(
                """
                SELECT dls.team_id, dls.scoring_period, dls.player_id
                FROM daily_lineup_slots dls
                JOIN teams t ON t.id = dls.team_id
                WHERE t.league_season_id = :ls AND dls.slot <> ALL(:unseatable)
                """
            ),
            {"ls": league_season.id, "unseatable": list(UNSEATABLE_SLOTS)},
        ):
            rostered.setdefault((int(team), int(day)), []).append(int(player_id))

        games: dict[tuple[int, int], CategoryLine] = {}
        for row in session.execute(
            text(
                f"""
                SELECT pgs.player_id, pgs.scoring_period, {columns}
                FROM player_game_stats pgs
                WHERE pgs.season = :season AND pgs.played
                """
            ),
            {"season": SEASON},
        ):
            games[(int(row[0]), int(row[1]))] = _line(row[2:])

        eligible: dict[int, frozenset[str]] = {}
        for player_id, slots in session.execute(
            text(
                "SELECT player_id, eligible_slots FROM player_season_stats "
                "WHERE season = :season AND eligible_slots IS NOT NULL"
            ),
            {"season": SEASON},
        ):
            key = int(player_id)
            eligible[key] = eligible.get(key, frozenset()) | frozenset(str(s) for s in slots)

        periods = tuple(
            PeriodWindow(int(period), int(first), int(final), bool(playoff))
            for period, first, final, playoff in session.execute(
                text(
                    "SELECT period, first_scoring_period, final_scoring_period, is_playoff "
                    "FROM matchup_periods WHERE league_season_id = :ls "
                    "AND first_scoring_period IS NOT NULL ORDER BY period"
                ),
                {"ls": league_season.id},
            )
        )

        opponent: dict[tuple[int, int], int] = {}
        for period, home, away in session.execute(
            text(
                "SELECT mp.period, m.home_team_id, m.away_team_id FROM matchups m "
                "JOIN matchup_periods mp ON mp.id = m.matchup_period_id "
                "WHERE mp.league_season_id = :ls AND m.away_team_id IS NOT NULL"
            ),
            {"ls": league_season.id},
        ):
            opponent[(int(period), int(home))] = int(away)
            opponent[(int(period), int(away))] = int(home)

        totals: dict[tuple[int, int], dict[str, float]] = {}
        for period, team, abbreviation, value in session.execute(
            text(
                "SELECT mp.period, mts.team_id, mts.abbreviation, mts.value "
                "FROM matchup_team_stats mts "
                "JOIN matchups m ON m.id = mts.matchup_id "
                "JOIN matchup_periods mp ON mp.id = m.matchup_period_id "
                "WHERE mp.league_season_id = :ls"
            ),
            {"ls": league_season.id},
        ):
            if str(abbreviation) in COUNTS:
                bucket = totals.setdefault((int(period), int(team)), {})
                bucket[str(abbreviation)] = float(value or 0.0)

        categories = tuple(
            str(abbreviation)
            for abbreviation in session.scalars(
                text(
                    "SELECT abbreviation FROM league_season_categories "
                    "WHERE league_season_id = :ls ORDER BY position"
                ),
                {"ls": league_season.id},
            )
        )
        return cls(
            started=started,
            rostered={key: tuple(sorted(ids)) for key, ids in rostered.items()},
            games=games,
            eligible=eligible,
            lineup=tuple(lineup_for(league_season)),
            form=_prior_form(games, category_distributions(session, league_season)),
            periods=periods,
            opponent=opponent,
            totals={key: CategoryLine(counts) for key, counts in totals.items()},
            categories=categories,
        )

    def period_for(self, day: int) -> PeriodWindow | None:
        for window in self.periods:
            if window.first <= day <= window.final:
                return window
        return None

    def touched(self, first_day: int, last_day: int) -> list[PeriodWindow]:
        """Every matchup period the window [first_day, last_day] overlaps."""
        return [w for w in self.periods if w.first <= last_day and w.final >= first_day]

    def team_line(self, team: int, window: PeriodWindow) -> CategoryLine:
        """What the team's started men actually posted over the whole period."""
        key = (team, window.period)
        if key not in self._team_periods:
            line = EMPTY
            for (team_id, day, _player), found in self.started.items():
                if team_id == team and window.first <= day <= window.final:
                    line = line + found
            self._team_periods[key] = line
        return self._team_periods[key]

    def started_line(self, team: int, first: int, last: int) -> CategoryLine:
        """What the team's started men posted over [first, last], as it was."""
        line = EMPTY
        for (team_id, day, _player), found in self.started.items():
            if team_id == team and first <= day <= last:
                line = line + found
        return line

    def seat(self, roster: Collection[int], day: int) -> tuple[int, ...]:
        """The men a lineup would start from `roster` on `day`.

        The recommender's own seating (`app.pickups.stream._seat`): take the
        men with a game in order of value and keep each one the matching can
        still seat, which is exact for a transversal matroid rather than an
        approximation of it. The order is `form` -- what each man had averaged
        *before* that day -- because a manager setting Tuesday's lineup on
        Tuesday morning knows that and not what Tuesday will bring. Seating by
        the night's own box score would hand every counterfactual a lineup
        nobody could have set, and would flatter every added player.
        """
        available = sorted(
            (player for player in roster if (player, day) in self.games),
            key=lambda player: (-self.form.get((player, day), 0.0), player),
        )
        chosen: dict[int, frozenset[str]] = {}
        seated: list[int] = []
        for player in available:
            if len(seated) >= len(self.lineup):
                break
            trial = {**chosen, player: self.eligible.get(player, frozenset())}
            if max_matching(trial, self.lineup) > len(seated):
                chosen = trial
                seated.append(player)
        return tuple(seated)

    def day_line(self, roster: Collection[int], day: int) -> CategoryLine:
        """What `roster` would have posted on `day`, seated by `seat`."""
        line = EMPTY
        for player in self.seat(roster, day):
            line = line + self.games[(player, day)]
        return line

    def resolved(
        self,
        team: int,
        window: PeriodWindow,
        first: int,
        last: int,
        dropped: Sequence[int] = (),
        added: Sequence[int] = (),
    ) -> CategoryLine:
        """The period's line with the swap in it, the lineup re-solved daily.

        Days before `first` and after `last` keep the manager's own started
        line: the move had not happened yet, or its window has closed, so
        there is nothing to re-solve. Over the window the roster of the day is
        read from `daily_lineup_slots`, the swap applied to it, and the lineup
        seated from scratch.
        """
        line = self.started_line(team, window.first, first - 1)
        gone, arrived = set(dropped), set(added)
        for day in range(first, last + 1):
            roster = (set(self.rostered.get((team, day), ())) - gone) | arrived
            line = line + self.day_line(roster, day)
        return line + self.started_line(team, last + 1, window.final)

    def categories_won(self, mine: CategoryLine, theirs: CategoryLine) -> float:
        """Categories the first line beats the second in; a tie is half."""
        my_totals = mine.totals(self.categories)
        their_totals = theirs.totals(self.categories)
        won = 0.0
        for category in self.categories:
            edge = my_totals[category] - their_totals[category]
            if category in INVERTED_CATEGORIES:
                edge = -edge
            won += 1.0 if edge > 0 else 0.0 if edge < 0 else 0.5
        return won

    def delta(
        self,
        team: int,
        window: PeriodWindow,
        from_day: int,
        to_day: int,
        dropped: Sequence[int],
        added: Sequence[int],
    ) -> float:
        """Categories the swap would have won the team, over one matchup period.

        Both sides are re-solved: the roster of each day is seated from
        scratch with the swap in it, and again without it. The comparison is
        therefore between two lineups set by the same rule, and none of the
        credit for a move is really credit for setting a better lineup, which
        is what the earlier cap-the-starts rule could not separate. The
        manager's own seating is kept only as a diagnostic (`drift`).

        The no-move line is memoized per (team, period, window), since every
        move considered at one decision point shares it.
        """
        other = self.opponent.get((window.period, team))
        if other is None:
            return 0.0
        theirs = self.totals.get((window.period, other))
        if theirs is None:
            return 0.0
        first = max(from_day, window.first)
        last = min(to_day, window.final)
        if first > last:
            return 0.0

        key = (team, window.period, first, last)
        if key not in self._without:
            self._without[key] = self.resolved(team, window, first, last)
        without = self._without[key]
        with_swap = self.resolved(team, window, first, last, dropped, added)
        return self.categories_won(with_swap, theirs) - self.categories_won(without, theirs)

    def drift(self, team: int, window: PeriodWindow, from_day: int) -> float:
        """Categories the re-solve wins that the manager's own lineup did not.

        Not part of any score: it says how far the seating model is from what
        the team actually did, which is the honest size of the "assume a
        perfectly set lineup" assumption. Positive means the re-solve did
        better than the manager.
        """
        other = self.opponent.get((window.period, team))
        theirs = self.totals.get((window.period, other)) if other is not None else None
        if theirs is None:
            return 0.0
        first = max(from_day, window.first)
        if first > window.final:
            return 0.0
        key = (team, window.period, first, window.final)
        if key not in self._without:
            self._without[key] = self.resolved(team, window, first, window.final)
        return self.categories_won(self._without[key], theirs) - self.categories_won(
            self.team_line(team, window), theirs
        )

    def score(
        self, team: int, day: int, dropped: Sequence[int], added: Sequence[int]
    ) -> tuple[float, float]:
        """(categories this matchup period, categories over the next 30 days)."""
        window = self.period_for(day)
        week = (
            self.delta(team, window, day, window.final, dropped, added)
            if window is not None
            else 0.0
        )
        season = sum(
            self.delta(team, found, day, day + SEASON_WINDOW, dropped, added)
            for found in self.touched(day, day + SEASON_WINDOW)
        )
        return week, season


def _line(values: Sequence[Any]) -> CategoryLine:
    return CategoryLine(
        {key: float(value or 0.0) for key, value in zip(COUNTS, values, strict=True)}, 1
    )


def _prior_form(
    games: Mapping[tuple[int, int], CategoryLine],
    distributions: Sequence[CategoryDistribution],
) -> dict[tuple[int, int], float]:
    """Each man's value on each of his game days, from the games before it.

    The order the re-solve seats a day in. `app.pickups.stream.weight` is the
    same ordering the recommender uses to decide who sits on a full day; the
    line it is given here is the player's mean box score *strictly before*
    that day, so the seating knows only what a manager setting the lineup
    that morning knew. A man playing his first game of the season has no
    prior and sorts last, which is what an unknown quantity deserves.
    """
    by_player: dict[int, list[int]] = {}
    for player_id, day in games:
        by_player.setdefault(player_id, []).append(day)
    out: dict[tuple[int, int], float] = {}
    for player_id, days in by_player.items():
        running = EMPTY
        for played, day in enumerate(sorted(days)):
            out[(player_id, day)] = (
                weight(running.scaled(1.0 / played), distributions) if played else 0.0
            )
            running = running + games[(player_id, day)]
    return out


def value_move(
    replay: Replay,
    *,
    team_id: int,
    team_row_id: int,
    day: int,
    kind: str,
    rank: int,
    cleared: bool,
    added_ids: Sequence[int],
    added_name: str,
    dropped_ids: Sequence[int],
    dropped_name: str | None,
    claimed: float,
    weeks: float,
    horizon: str,
    fills_empty_day: bool = False,
    costs_faab: bool = False,
) -> Scored:
    """Replay one move and record the categories it won.

    Every man the move moves is replayed, a two-swap's second pair included:
    with the lineup re-solved there is nothing left to cap, so nothing has to
    be left out and called an upper bound.
    """
    week, season = replay.score(team_row_id, day, dropped_ids, added_ids)
    return Scored(
        team_id=team_id,
        day=day,
        kind=kind,
        rank=rank,
        cleared=cleared,
        added_id=added_ids[0] if added_ids else 0,
        added_name=added_name,
        dropped_id=dropped_ids[0] if dropped_ids else None,
        dropped_name=dropped_name,
        claimed=claimed,
        weeks=weeks,
        week=week,
        season=season,
        fills_empty_day=fills_empty_day,
        costs_faab=costs_faab,
        horizon=horizon,
    )


def league_baseline(session: Session, league_season: LeagueSeason, replay: Replay) -> list[Scored]:
    """The league's own 2026 one-for-one swaps, scored by the same replay.

    A real move already happened, so it is replayed **backwards** -- the man
    who left goes back in and the man who arrived comes out -- and the sign is
    flipped. That makes "what the move was worth" one quantity for a claim
    somebody made and a claim the recommender would have made.
    """
    rows = session.execute(
        text(
            """
            SELECT t.scoring_period, t.team_id, ti_add.player_id, ti_drop.player_id
            FROM transactions t
            JOIN transaction_items ti_add
              ON ti_add.transaction_id = t.id AND ti_add.item_type = 'ADD'
            JOIN transaction_items ti_drop
              ON ti_drop.transaction_id = t.id AND ti_drop.item_type = 'DROP'
            WHERE t.league_season_id = :ls AND t.status = 'EXECUTED'
              AND t.team_id IS NOT NULL
            """
        ),
        {"ls": league_season.id},
    ).all()
    out: list[Scored] = []
    for day, team_row_id, added_id, dropped_id in rows:
        if added_id is None or dropped_id is None:
            continue
        # Backwards: the added man is the one whose started lines come out.
        week, season = replay.score(int(team_row_id), int(day), [int(added_id)], [int(dropped_id)])
        out.append(
            Scored(
                team_id=int(team_row_id),
                day=int(day),
                kind="league",
                rank=0,
                cleared=True,
                added_id=int(added_id),
                added_name="",
                dropped_id=int(dropped_id),
                dropped_name="",
                claimed=0.0,
                weeks=1.0,
                week=-week,
                season=-season,
                horizon="week",
            )
        )
    return out


def replay(
    session: Session,
    league_season: LeagueSeason,
    teams: Sequence[int],
    points: Sequence[tuple[int, int]],
    *,
    book: Replay,
    team_rows: Mapping[int, int],
    stream_hurdles: Sequence[float],
    season_hurdles: Sequence[tuple[float, float]],
    tilt: bool,
    progress: bool = True,
) -> dict[tuple[float, float, float], tuple[Setting, Setting]]:
    """Run both recommenders once per decision point, then apply every hurdle.

    The grid is applied after the fact rather than by re-running, because the
    hurdle is a reporting filter and nothing else: `stream.Move.clears` and
    `season.Swap.clears` are read by `StreamReport.recommended` and by the bid
    pricing, and the move list itself is built the same way whatever the bar is
    (`app/pickups/stream.py` line 455, `app/pickups/season.py` line 503). So one
    evaluation per decision point gives every cell of the grid exactly, which is
    what makes a 4 x 3 sweep affordable at all -- re-running per cell is twelve
    times the work for the same numbers.
    """
    settings: dict[tuple[float, float, float], tuple[Setting, Setting]] = {}
    for stream_hurdle in stream_hurdles:
        for paid, free in season_hurdles:
            settings[(stream_hurdle, paid, free)] = (
                Setting(f"{stream_hurdle:.2f}"),
                Setting(f"{paid:.2f}/{free:.2f}"),
            )

    counters = {"decisions": 0, "stream_errors": 0, "season_errors": 0, "pool_empty": 0}
    total = len(teams) * len(points)
    done = 0
    drifts: list[float] = []

    for team_id in teams:
        for _period, day in points:
            pool = free_agent_pool(session, league_season, day)
            if not pool:
                counters["pool_empty"] += 1
                done += 1
                continue
            counters["decisions"] += 1

            # The projection cache is `app.pickups.projection`'s own now, and
            # keyed on the day, so the two reports below share every line
            # they both need and neither sees another decision point's. The
            # posted totals need no setting up at all now: `state._posted`
            # reads the day it is given.
            stream_report = _run_stream(session, league_season, team_id, day, pool, tilt, counters)
            season_report = _run_season(session, league_season, team_id, day, pool, tilt, counters)

            # Score each distinct move once; every hurdle setting then only
            # decides which of those scored moves it would have named.
            row = team_rows[team_id]
            stream_scored = _score_stream_moves(book, team_id, row, day, stream_report)
            season_scored = _score_season_moves(book, team_id, row, day, season_report)
            window = book.period_for(day)
            if window is not None:
                drifts.append(book.drift(row, window, day))

            for (stream_hurdle, paid, free), (stream, season) in settings.items():
                stream.decisions += 1
                season.decisions += 1
                _apply_stream(stream, stream_scored, stream_hurdle)
                _apply_season(season, season_scored, paid, free)

            done += 1
            if progress and done % 25 == 0:
                print(f"   ... {done}/{total} decision points")

    if drifts:
        # Not a score: how far the re-solved lineup is from the one the
        # manager actually set, which is the size of the "assume a perfectly
        # set lineup" assumption both sides of every move now rest on.
        print(
            f"   lineup re-solve against the manager's own: "
            f"{statistics.fmean(drifts):+.2f} categories a period "
            f"(median {statistics.median(drifts):+.2f}, {len(drifts)} periods)"
        )
    mean_drift = statistics.fmean(drifts) if drifts else 0.0
    for _key, (_stream, _season) in settings.items():
        _stream.counters.update(counters)
        _stream.drift = mean_drift
        _season.drift = mean_drift
    return settings


def _run_stream(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    day: int,
    pool: Sequence[int],
    tilt: bool,
    counters: dict[str, int],
) -> StreamReport | None:
    """The stream report, or None when the day raised.

    The hurdle is not passed: it is a reporting filter that the grid is applied
    against afterwards (see `replay`), so one call serves every setting.
    """
    try:
        return stream_recommendations(
            session, league_season, team_id, day, pool=pool, tilt=tilt, bids=False
        )
    except Exception as exc:
        counters["stream_errors"] += 1
        print(f"   !! stream {team_id} day {day}: {type(exc).__name__}: {exc}")
        return None


def _run_season(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    day: int,
    pool: Sequence[int],
    tilt: bool,
    counters: dict[str, int],
) -> SeasonReport | None:
    """The season report, or None when the day raised. Hurdles as above."""
    try:
        return season_recommendations(session, league_season, team_id, day, pool=pool, tilt=tilt)
    except Exception as exc:
        counters["season_errors"] += 1
        print(f"   !! season {team_id} day {day}: {type(exc).__name__}: {exc}")
        return None


def _score_stream_moves(
    book: Replay, team_id: int, team_row_id: int, day: int, report: StreamReport | None
) -> list[Scored]:
    """Replay the top moves of one stream report, hurdle aside."""
    if report is None:
        return []
    return [
        value_move(
            book,
            team_id=team_id,
            team_row_id=team_row_id,
            day=day,
            kind=move.kind,
            rank=rank,
            cleared=False,
            added_ids=[move.add.player_id],
            added_name=move.add.name,
            dropped_ids=[move.drop.player_id] if move.drop else [],
            dropped_name=move.drop.name if move.drop else None,
            claimed=move.net,
            weeks=move.judgement.weeks_covered,
            horizon="week",
            fills_empty_day=move.fills_empty_day,
        )
        for rank, move in enumerate(report.moves[:TOP_N])
    ]


def _score_season_moves(
    book: Replay, team_id: int, team_row_id: int, day: int, report: SeasonReport | None
) -> list[Scored]:
    """Replay the top moves of one season report, hurdle aside."""
    if report is None:
        return []
    out: list[Scored] = []
    for rank, move in enumerate(report.moves[:TOP_N]):
        out.append(
            value_move(
                book,
                team_id=team_id,
                team_row_id=team_row_id,
                day=day,
                kind=move.kind,
                rank=rank,
                cleared=False,
                # Both men of a two-swap, both ways: the re-solve caps nothing,
                # so the whole move is replayed rather than half of it.
                added_ids=[player.player_id for player in move.into],
                added_name=", ".join(player.name for player in move.into),
                dropped_ids=[player.player_id for player in move.out],
                dropped_name=", ".join(player.name for player in move.out) or None,
                claimed=move.net,
                weeks=move.judgement.weeks_covered,
                horizon="season",
                costs_faab=move.costs_faab,
            )
        )
    return out


def _apply_stream(setting: Setting, scored: Sequence[Scored], hurdle: float) -> None:
    """Record a stream report's moves under one hurdle.

    Mirrors `stream.Move.clears`: the net over both horizons is over the bar,
    or a filled empty day that helps at all.
    """

    def clears(move: Scored) -> bool:
        return move.claimed >= hurdle or (move.fills_empty_day and move.claimed > 0)

    for move in scored:
        setting.moves.append(replace(move, cleared=clears(move)))
    if not scored or not clears(scored[0]):
        setting.no_move += 1
        if scored:
            setting.below_hurdle += 1


def _apply_season(setting: Setting, scored: Sequence[Scored], paid: float, free: float) -> None:
    """Record a season report's moves under one (paid, free) pair.

    The season bars are categories a week, and `Swap.clears` reads them
    against the net per week; the claimed figure carried here is the net over
    both horizons, so the same division is applied.
    """
    any_cleared = False
    for move in scored:
        bar = paid if move.costs_faab else free
        cleared = move.claimed / max(1.0, move.weeks) >= bar
        any_cleared = any_cleared or cleared
        setting.moves.append(replace(move, cleared=cleared))
    if not scored or not any_cleared:
        setting.no_move += 1
        if scored:
            setting.below_hurdle += 1


def sweep(
    session: Session,
    league_season: LeagueSeason,
    teams: Sequence[int],
    points: Sequence[tuple[int, int]],
    *,
    book: Replay,
    team_rows: Mapping[int, int],
    tilt: bool,
) -> dict[tuple[float, float, float], tuple[Setting, Setting]]:
    """The full grid, per the task: 4 streaming hurdles x 3 season pairs."""
    print(f"-- replaying with tilt={'on' if tilt else 'off'}")
    settings = replay(
        session,
        league_season,
        teams,
        points,
        book=book,
        team_rows=team_rows,
        stream_hurdles=STREAM_GRID,
        season_hurdles=SEASON_GRID,
        tilt=tilt,
    )
    for (stream_hurdle, paid, free), (stream, season) in sorted(settings.items()):
        print(
            f"   stream {stream_hurdle:.2f} n={stream.n} mean={stream.mean:+.3f} "
            f"win={stream.win_rate:.1%} nomove={stream.no_move_rate:.1%} "
            f"claimed={stream.claimed:+.3f} zero={stream.unmoved:.1%} | "
            f"season {paid:.2f}/{free:.2f} n={season.n} mean={season.mean:+.3f} "
            f"win={season.win_rate:.1%} nomove={season.no_move_rate:.1%} "
            f"claimed={season.claimed:+.3f} zero={season.unmoved:.1%}"
        )
    return settings


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """A markdown table, so the doc is generated rather than hand-copied."""
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return out


def _pct(value: float) -> str:
    return f"{value:.1%}"


def minutes_events_stored(session: Session) -> int:
    """How many minutes events the season holds for the tilt to read."""
    return int(
        session.scalar(
            text(
                "SELECT count(*) FROM player_status_events "
                "WHERE season = :season AND kind IN :kinds"
            ).bindparams(bindparam("kinds", expanding=True)),
            {"season": SEASON, "kinds": list(_MINUTES_KINDS)},
        )
        or 0
    )


def _teams(count: int) -> str:
    """One team or twelve teams, so the write-up reads as English."""
    return f"{count} team" + ("" if count == 1 else "s")


def report(
    results: Mapping[str, Mapping[tuple[float, float, float], tuple[Setting, Setting]]],
    baseline: Sequence[Scored],
    *,
    teams: int,
    points: int,
    runtime: float,
    tilt_run: str,
    minutes_events: int = 0,
) -> str:
    """The write-up, in the style of docs/punt_builds.md.

    `minutes_events` is how many minutes events the season holds for the
    tilt to read; none means tilt on and tilt off are the same run, and the
    write-up says so rather than presenting the two tables as a comparison.
    """
    base_week = statistics.fmean([b.week for b in baseline]) if baseline else 0.0
    base_season = statistics.fmean([b.season for b in baseline]) if baseline else 0.0
    base_win = sum(1 for b in baseline if b.week >= 0) / len(baseline) if baseline else 0.0
    drift = next(
        (stream.drift for settings in results.values() for stream, _s in settings.values()),
        0.0,
    )
    lines: list[str] = []

    lines.append("# Pickup recommender: the 2026 backtest, scored in categories")
    lines.append("")
    lines.append(
        f"Full Court Press (ESPN 3853870), 2026. Generated by "
        f"`scripts/pickups_backtest.py`. {_teams(teams)} x {points} decision points, "
        f"tilt {tilt_run}, {runtime:.0f}s."
    )
    lines.append("")

    lines.append("## 1. What is measured, and in what")
    lines.append("")
    lines.append(
        "Every number in this document is **categories won**, not composite production. "
        "A move is scored by replaying the matchup that actually happened with the swap "
        "in it. Day by day over the window, the roster the team really held is taken "
        "from `daily_lineup_slots`, the swap applied to it, and the ten starting slots "
        "filled by the recommender's own seating rule; the day's line is the seated "
        "men's real box scores. The same is done without the swap. Both lines are "
        "counted against the opponent's real period totals and subtracted. +1 means the "
        "swap flipped one category."
    )
    lines.append("")
    lines.append(
        "Both sides are re-solved -- the roster without the swap is seated by the same "
        "rule -- so none of a move's credit is really credit for setting a better "
        "lineup. How far the re-solve is from what the manager actually did is reported "
        "as `drift` in section 4 and enters no score."
    )
    lines.append("")
    lines.append(
        "The composite-scored run of 2026-09-18 read -3.2 composite a day at a 20% win "
        "rate. That was a measurement of composite, not of the recommender: the "
        "objective spends points to win the categories that are close, and composite "
        "charges it for every point it spends. The two scores disagree by construction, "
        "and only one of them is the league's scoring."
    )
    lines.append("")

    lines.append("## 2. The headline")
    lines.append("")
    lines.append(
        "`n` is decisions where a move cleared the hurdle; the mean and the win rate "
        "are over those moves. The streaming report is judged on the matchup period the "
        "decision fell in, the rest-of-season report over the next "
        f"{SEASON_WINDOW} days. `claimed` is the mean net the recommender said the move "
        "was worth (`app.pickups.judge`), so `delivered/claimed` is its calibration."
    )
    lines.append("")

    for tilt_label, settings in results.items():
        lines.append(f"### Tilt {tilt_label}")
        lines.append("")
        rows: list[list[str]] = []
        for (stream_hurdle, paid, free), (stream, season) in sorted(settings.items()):
            rows.append(
                [
                    f"{stream_hurdle:.2f}",
                    f"{paid:.2f}/{free:.2f}",
                    str(stream.n),
                    f"{stream.mean:+.2f}",
                    _pct(stream.win_rate),
                    _pct(stream.no_move_rate),
                    f"{stream.claimed:+.2f}",
                    str(season.n),
                    f"{season.mean:+.2f}",
                    _pct(season.win_rate),
                    _pct(season.no_move_rate),
                    f"{season.claimed:+.2f}",
                ]
            )
        lines += _table(
            [
                "stream hurdle",
                "season (paid/free)",
                "stream n",
                "stream cats",
                "stream >= 0",
                "stream no-move",
                "stream claimed",
                "season n",
                "season cats",
                "season >= 0",
                "season no-move",
                "season claimed",
            ],
            rows,
        )
        lines.append("")

    lines.append("### Against the league's own moves")
    lines.append("")
    lines += _table(
        ["population", "n", "categories, the week", "categories, 30 days", "share >= 0"],
        [
            [
                "league's own 2026 one-for-one swaps",
                str(len(baseline)),
                f"{base_week:+.2f}",
                f"{base_season:+.2f}",
                _pct(base_win),
            ]
        ],
    )
    lines.append("")
    lines.append(
        "The baseline is scored by the same replay, run backwards: a real move is "
        "undone (the dropped man goes back in, the added man comes out) and the sign "
        "flipped, so a recommendation and a real claim are the same quantity. It is the "
        "same population `docs/acquirable_value.md` measured -- executed transactions "
        "with exactly one add and one drop -- in a different currency, so that note's "
        "composite figure of +0.57 a day is not comparable to the number above and is "
        "not quoted as if it were."
    )
    lines.append("")

    lines.append("## 3. Calibration: claimed against delivered")
    lines.append("")
    lines.append(
        "A recommender that says +0.5 and delivers +0.1 is a different tool from one "
        "that says +0.5 and delivers +0.5, at the same win rate. The claim is "
        "rescaled to the window the delivery is measured over -- one matchup for a "
        f"stream, {SEASON_WINDOW} days for a season move -- because the raw net spans "
        "every week left in the year. The ratio is delivered over claimed. `zero` is "
        "the share of named moves that changed no category at all -- now a fact about "
        "the move rather than about the scorer, since nothing caps the replay."
    )
    lines.append("")
    for tilt_label, settings in results.items():
        rows = []
        for (stream_hurdle, paid, free), (stream, season) in sorted(settings.items()):
            rows.append(
                [
                    tilt_label,
                    f"{stream_hurdle:.2f}",
                    f"{stream.claimed:+.2f}",
                    f"{stream.mean:+.2f}",
                    f"{stream.calibration:.2f}",
                    _pct(stream.unmoved),
                    f"{paid:.2f}/{free:.2f}",
                    f"{season.claimed:+.2f}",
                    f"{season.mean:+.2f}",
                    f"{season.calibration:.2f}",
                    _pct(season.unmoved),
                ]
            )
        lines += _table(
            [
                "tilt",
                "stream hurdle",
                "stream claimed",
                "stream delivered",
                "ratio",
                "stream zero",
                "season hurdle",
                "season claimed",
                "season delivered",
                "ratio",
                "season zero",
            ],
            rows,
        )
        lines.append("")

    lines.append("## 4. The tuning rule, and what it chose")
    lines.append("")
    lines.append(
        "A setting qualifies when it still says *no move* on more than "
        f"{MIN_NO_MOVE:.0%} of decisions -- a tool that always names a pickup makes its "
        "user worse, which is the league's own finding -- and when the categories it "
        f"delivers beat the league's own moves ({base_week:+.2f} a week). Among the "
        "qualifying settings, the best mean wins."
    )
    lines.append("")
    for tilt_label, settings in results.items():
        stream_qualifying = [
            (key, stream)
            for key, (stream, _season) in sorted(settings.items())
            if stream.eligible(base_week)
        ]
        if not stream_qualifying:
            lines.append(
                f"- Tilt {tilt_label}, streaming: **no setting qualifies**. The no-move "
                "rate never clears the bar, because a move that seats a man on a day "
                "a slot was going empty is recommended whenever it helps at all "
                "(`Move.clears`), whatever the hurdle; the hurdle only decides the "
                "rest. `STREAM_HURDLE` is left where the design note put it."
            )
        else:
            key, stream = max(stream_qualifying, key=lambda row: row[1].mean)
            lines.append(
                f"- Tilt {tilt_label}, streaming: best qualifying hurdle **{key[0]:.2f}** "
                f"({stream.mean:+.2f} categories over {stream.n} moves, "
                f"{_pct(stream.no_move_rate)} no-move)."
            )
        season_qualifying = [
            (key, season)
            for key, (_stream, season) in sorted(settings.items())
            if season.eligible(base_season)
        ]
        if not season_qualifying:
            lines.append(
                f"- Tilt {tilt_label}, rest of season: **no setting qualifies**, so "
                "`SEASON_HURDLE_PAID` and `SEASON_HURDLE_FREE` are left where the "
                "design note put them."
            )
        else:
            key, season = max(season_qualifying, key=lambda row: row[1].mean)
            lines.append(
                f"- Tilt {tilt_label}, rest of season: best qualifying hurdles "
                f"**{key[1]:.2f} paid / {key[2]:.2f} free** ({season.mean:+.2f} "
                f"categories over 30 days, {season.n} moves, "
                f"{_pct(season.no_move_rate)} no-move)."
            )
    lines.append("")
    # Written here rather than added to the document by hand, because the last
    # time it was added by hand the next run deleted it.
    lines.append(HURDLES_APPLIED)
    lines.append("")
    lines.append(
        f"**This script changes no constant.** The run covers {_teams(teams)}; the "
        "settings above are its recommendation, and the two hurdle constants are "
        "changed by hand with this table quoted beside them."
    )
    lines.append("")
    if minutes_events == 0 and len(results) > 1:
        lines.append(
            "**Tilt on and tilt off are the same run here.** The minutes tilt reads "
            "the listener's minutes events (`player_status_events`), and this season "
            "holds none: the listener began the season after. Every number above is "
            "therefore the untilted recommender, and the tilt is unmeasured, not "
            "measured as worthless."
        )
        lines.append("")
    lines.append(
        f"**Drift: {drift:+.2f} categories a period.** That is how much the re-solved "
        "lineup wins that the manager's own starts did not, averaged over the periods "
        "replayed. A number near zero says the seating model is close to what this "
        "league actually does, so the counterfactual both sides of every move rest on "
        "is not a fantasy roster."
    )
    lines.append("")

    lines.append("## 5. One defect in the stored data, and one leak now closed")
    lines.append("")
    lines.append("### The 2026 schedule does not exist")
    lines.append("")
    lines.append(
        "`app/pickups/state.py` counts a player's remaining games from "
        "`pro_team_games`, which holds no 2026 rows at all (only 2027 has any; the "
        "listener that writes it started in 2027). With no schedule every player has "
        "no game days, `stream._Week.project` seats nobody, and every move's change "
        "in expected wins is exactly 0.0. **Workaround:** the script reconstructs a "
        "schedule from the box scores -- a played line on scoring period N is a game "
        "on N, and the player's NBA team comes from the weekly roster row, the same "
        "fallback `state.build_players` uses. Verified: 30 NBA teams, 2,461 team-game "
        "slots against the 2,460 a full 82-game season needs."
    )
    lines.append("")
    lines.append("### The matchup totals no longer leak the rest of the week")
    lines.append("")
    lines.append(
        "`state.load_team_week` used to read `my_totals` and `opp_totals` from "
        "`matchup_team_stats`, the period's **final** total, with no day column to cap "
        "it by, and this script substituted a capped sum of the started lines for the "
        "length of a run. `state._posted` takes the day itself now: it keeps ESPN's "
        "row only on a genuinely live morning -- one where the database holds no box "
        "score on or after today -- and otherwise sums the started lines on the days "
        "of this period **before** today, which over a whole period reproduces "
        "`matchup_team_stats` exactly. This run therefore measures the product's own "
        "totals, with nothing substituted."
    )
    lines.append("")
    lines.append(POSTED_BOUNDARY_MOVED)
    lines.append("")
    lines.append(
        "**Verified no leak** in the two places the design note asks about: "
        "`app/scoring/knowable.py` filters to `scoring_period < day` and the "
        "projection's minutes tilt does the same. `app/pickups/bids.bid_fit` reads the "
        "whole season's transactions, which would be a look-ahead if a bid were taken "
        "as advice; the replay never scores a bid."
    )
    lines.append("")

    lines.append("## 6. Caveats")
    lines.append("")
    lines.append(
        "- **The replay assumes a perfectly set lineup, on both sides.** Every day is "
        "seated from scratch by the same rule with the swap and without it, so the "
        "difference is the move and not the lineup; but neither side is the lineup the "
        "manager actually set. The gap between the re-solve and his own starts is "
        "printed beside the run and enters no score."
    )
    lines.append(
        "- **No injury history.** There are no 2026 status snapshots, so every player "
        "is treated as available and the stash logic is under-served by construction."
    )
    lines.append(
        f"- **{points} decision points a team, {_teams(teams)}.** Small. One season, one league."
    )
    lines.append(
        "- **A move is replayed against the roster the team really held**, so a man the "
        "manager dropped for other reasons later in the window leaves the counter"
        "factual roster one place larger than thirteen for the rest of it."
    )
    lines.append(
        "- **A move's week score is zero on a bye**, and on a decision day in a period "
        "with no recorded opponent, since there are no categories to win."
    )
    lines.append(
        "- **The add budget never binds here.** The replay takes two decision points a "
        "matchup period (its first and fourth days) and scores the moves found at each, "
        "so no team ever approaches the one-add-a-day-of-the-period budget the reports "
        "carry (`ADDS_PER_PERIOD_DAY`, docs/pickups.md section 4.3). Nothing here "
        "measures a plan's second move, or a day with no adds left; the hurdles were "
        "fitted on one move a decision. Before 2026-09-21 it could bind spuriously, "
        "because the adds a team had spent were counted over the period's whole span "
        "rather than through the decision day."
    )
    lines.append(
        "- **Ten 2026 days carry no box scores at all**, and `daily_lineup_slots` stops "
        "at day 160 while stats run to 174. Decision points falling on an empty day "
        "yield no pool and are counted separately as `pool_empty`."
    )
    lines.append("")

    lines.append("## 7. Decisions taken in this script")
    lines.append("")
    for item in DECISIONS:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines) + "\n"


#: What the exclusive boundary cost, measured once when it was adopted. A
#: record for the next reader of the tables above, which are not the tables
#: the 2026-09-18 run produced.
POSTED_BOUNDARY_MOVED = (
    "**It moved the streaming numbers, and almost nothing else.** The cap this script "
    "used to install was inclusive of day N, so it counted day N's lines as already "
    "posted while the projection was adding that same day on top; 584 of the 616 "
    "team-decision points had a different posted total afterwards, and the whole run's "
    "posted PTS fell from 125,125 to 77,483. Against the 2026-09-18 run: the streaming "
    "mean fell from +0.17 categories a matchup at every hurdle to +0.14 to +0.16, the "
    "win rate from 82.6-83.1% to 81.4-82.3%, and the calibration ratio from about 2.0 "
    "to about 1.7; the number of moves named and the no-move rate barely moved (574 to "
    "573 at the 0.05 bar, 538 to 536 at 0.20). The rest-of-season side is unchanged "
    "where the two grids overlap -- the 0.05 paid cell 524 moves at +0.98 against 525 "
    "at +0.98, the 0.10 paid cell 453 at +1.06 against 450 at +1.05 -- which is what "
    "it should be, since only that report's week half reads the posted totals. The "
    "baseline is identical to three decimals (1,120 moves, +0.050 a week, -0.558 over "
    "30 days, 84.2%), which is the check that the scoring machinery itself did not "
    "move."
)

#: What the owner did with an earlier run's recommendation. A record, not a
#: measurement, and the only part of section 4 this script does not compute --
#: it lives here because a hand-written paragraph in the document does not
#: survive the next run, and the 2026-09-18 one did not.
HURDLES_APPLIED = (
    "**Applied 2026-09-18, Patrick's decision:** `STREAM_HURDLE` 0.10 -> 0.20 (the "
    "least churn for no loss, with a finite add budget and finite FAAB) and "
    "`SEASON_HURDLE_PAID` 0.05 -> 0.10. That run's grid ran the free bar above the "
    "paid one, which the design rejects, and adds into an open place are rare enough "
    "that the cell measured the paid bar alone; `SEASON_HURDLE_FREE` follows the paid "
    "bar at half, 0.02 -> 0.05, unmeasured on its own. The grid has since been "
    "corrected to run the free bar below the paid one and extended to 0.20 paid, so "
    "the season rows above are not the same cells that decision was taken on; the "
    "constants stand where he put them until he moves them again."
)

#: Choices this backtest makes that a reader could reasonably have made
#: differently, recorded because the note's convention is to write them down.
DECISIONS: tuple[str, ...] = (
    "Moves are scored in categories, by replaying the real matchup with the swap in "
    "it, and composite is gone. The recommender optimises categories; scoring it in "
    "anything else measures the scorer.",
    "The lineup is re-solved day by day for the swapped roster, and for the unswapped "
    "one as well. Scoring a re-solved lineup against the manager's own starts would "
    "pay every move the difference between a well-set lineup and a badly-set one.",
    "The seating order is each man's form before that day, not his line on it. A "
    "manager sets the lineup in the morning; hindsight seating would flatter every "
    "pickup and would not be a lineup anybody could have set.",
    "The earlier rule -- cap the added man at the dropped man's started days -- is "
    "gone. It scored 60% of named streaming moves at exactly zero, because an "
    "empty-day pickup replaces a man who was not starting, so the measurement could "
    "not tune the streaming hurdle at all.",
    "The baseline is the league's own swaps replayed backwards and negated, rather "
    "than quoted from `docs/acquirable_value.md`. The note's +0.57 is composite a day "
    "and is not the same quantity as a category.",
    "A tie in a category counts half to each side, so a swap that changes nothing "
    "scores exactly zero rather than flipping on a rounding.",
    "A move is a win at >= 0 rather than > 0, since a swap that costs no categories "
    "costs nothing but the transaction.",
    "The season's played rows are loaded once into `Replay` -- four queries -- because "
    "the category replay asks the same questions of the same rows hundreds of times a "
    "team. `SeasonBook` was not reused: it aggregates started lines per matchup "
    "period, and the replay needs them per day.",
    "The schedule was reconstructed from box scores rather than declared a dead end, "
    "because without it the measurement is identically zero.",
    "The matchup totals are the product's own now, not a substitution: `state._posted` "
    "takes the day and sums the started lines before it, verified exact against ESPN "
    "at the full period. The cap this script used to install was inclusive of day N, "
    "which counted day N once as posted and again as projected.",
    "The hurdle grid is applied after one evaluation per decision point, not by "
    "re-running per cell. The hurdle is a reporting filter in both modules, so this is "
    "exact and not an approximation.",
    "The projection entry points are memoized per decision point. Verified identical "
    "move deltas to nine decimals, 11.6x faster; the cache is keyed on the day and "
    "rebuilt per decision point, so it cannot carry an answer across days.",
    "The one remaining workaround, the reconstructed schedule, is installed by "
    "attribute substitution and removed in a `finally`, so nothing on disk changes.",
    "The top 5 moves per decision are scored, not only the recommended one, so the "
    "top-ranked move's return is visible whether or not it cleared the bar.",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Teams to run, for a smoke test")
    parser.add_argument(
        "--tilt", choices=("on", "off", "both"), default="both", help="Minutes tilt to run"
    )
    parser.add_argument("--out", type=Path, default=REPORT, help="Where the write-up goes")
    parser.add_argument("--no-write", action="store_true", help="Print, do not write the doc")
    args = parser.parse_args()

    started = time.time()
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        league_season = load_season(session)
        points = decision_points(session, league_season)
        teams = team_ids(session, league_season)
        if args.limit:
            teams = teams[: args.limit]
        print(f"season {SEASON}: {len(teams)} teams x {len(points)} decision points")
        print(f"  calendar: {season_calendar(session, SEASON)}")
        book = Replay.load(session, league_season)
        rows = team_rows(session, league_season)
        print(
            f"  replay: {len(book.started)} started lines, {len(book.games)} box scores, "
            f"{len(book.periods)} periods, {len(book.categories)} categories"
        )

        tilts = [True, False] if args.tilt == "both" else [args.tilt == "on"]
        results: dict[str, dict[tuple[float, float, float], tuple[Setting, Setting]]] = {}

        with patched_state():
            for tilt in tilts:
                results["on" if tilt else "off"] = sweep(
                    session, league_season, teams, points, book=book, team_rows=rows, tilt=tilt
                )

            base = league_baseline(session, league_season, book)
            print(
                f"baseline: n={len(base)} "
                f"week={statistics.fmean([b.week for b in base]):+.3f} "
                f"season={statistics.fmean([b.season for b in base]):+.3f} "
                f"share>=0={sum(1 for b in base if b.week >= 0) / len(base):.1%}"
            )

    runtime = time.time() - started
    text_out = report(
        results,
        base,
        teams=len(teams),
        points=len(points),
        runtime=runtime,
        tilt_run=", ".join(results),
        minutes_events=minutes_events_stored(session),
    )
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text_out)
        print(f"wrote {args.out}")
    print(f"runtime: {runtime:.0f}s")


if __name__ == "__main__":
    main()
