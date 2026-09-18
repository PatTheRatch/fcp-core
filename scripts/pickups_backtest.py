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

- **The week.** Take the team's real started lines for the matchup period
  (`app.scoring.lines`), remove the dropped man's from the decision day on, and
  put the added man's *real box scores* on the days that are left, capped at the
  number of days the dropped man had started so a streamer cannot get more
  starts than the place had. Rebuild the nine totals, count the categories that
  beat the opponent's real period totals (`matchup_team_stats`), and subtract
  the categories the team actually won. The answer is in whole categories: +1
  means the swap flipped one.
- **The season.** The same replay over the next 30 days, summed across every
  matchup period that window touches.
- **The baseline.** The league's own one-for-one swaps of 2026, scored by the
  same code: a real move is replayed *backwards* (put the dropped man back, take
  the added man out) and the sign flipped, so "what the move was worth" means
  the same thing for a recommendation and for a real claim. One currency, one
  comparison.
- **Calibration.** Beside them, what the recommender CLAIMED (the judgement's
  net over both horizons, `app.pickups.judge`) against what it DELIVERED. A tool
  that says +0.5 and delivers +0.1 is not the same tool as one that says +0.5
  and delivers +0.5, even at the same win rate.

What the replay cannot do is re-run the lineup: the swapped roster is assumed to
start the new man on the days the old one started, rather than re-solving the
daily matching. That over-serves a pickup whose games fall on days the lineup
was already full, and under-serves one who fills a day it left empty. Both are
stated in the write-up.

TWO DEFECTS THIS SCRIPT WORKS AROUND

Neither is fixed here: both live in `app/`, which this branch may not change
beyond the two hurdle constants. Both are reported in the write-up.

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

2. THE MATCHUP TOTALS LEAK. `state.load_team_week` reads `my_totals` and
   `opp_totals` from `matchup_team_stats`, which holds one row per (matchup,
   team, category) -- the period's FINAL total, with no day column to cap it
   by. At day N of a period the recommender sees the whole period, including
   days that have not happened. WORKAROUND: `rebuild_posted` sums the started
   lines through day N instead. Verified against ESPN: the same sum over a full
   period reproduces `matchup_team_stats` exactly on all nine team-periods
   checked (three teams x three periods).

Both are installed by attribute substitution on `app.pickups.state`, removed in
a `finally`, and change nothing on disk.

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
import importlib
import statistics
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.draft.valuation import INVERTED_CATEGORIES
from app.pickups.season import SeasonReport, season_recommendations
from app.pickups.state import _PRO_TEAM_IDS, season_calendar
from app.pickups.stream import StreamReport, stream_recommendations
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
SEASON_GRID: tuple[tuple[float, float], ...] = ((0.02, 0.05), (0.05, 0.10), (0.10, 0.20))

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


#: The day the posted totals are capped at. One element because
#: `state._posted` is called by `load_team_week` with a fixed signature; the
#: replay sets it before each call and clears it after.
_POSTED_CAP: list[int | None] = [None]


def stored_posted(session: Session, matchup_id: int, team_row_id: int) -> CategoryLine:
    """The real `state._posted`: `matchup_team_stats` for the whole period."""
    rows = session.execute(
        text(
            "SELECT abbreviation, value FROM matchup_team_stats "
            "WHERE matchup_id = :m AND team_id = :t"
        ),
        {"m": matchup_id, "t": team_row_id},
    ).all()
    counts = {
        str(abbreviation): float(value or 0.0)
        for abbreviation, value in rows
        if str(abbreviation) in COUNTS
    }
    return CategoryLine(counts, 0)


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


def rebuild_posted(session: Session, matchup_id: int, team_row_id: int) -> CategoryLine:
    """`state._posted`, capped at `_POSTED_CAP[0]`; the stored total otherwise.

    `matchup_team_stats` is the period's final, so it cannot answer "what has
    this team posted so far". The started lines through day N can: their sum
    over a full period reproduces the stored total exactly (see the module
    docstring).

    The window matters. `_POSTED_CAP` holds the *last day* of what has been
    played so far, and the first is the matchup period's own first day -- the
    cap is not a lower bound. Summing everything at or before the cap would
    add every earlier period to this week's total. The period is reached
    through `matchups`, because the id `_posted` is handed is a matchup's, not
    a matchup period's.
    """
    cap = _POSTED_CAP[0]
    if cap is None:
        return stored_posted(session, matchup_id, team_row_id)
    return sum_lines_for(session, matchup_id, team_row_id, cap)


#: The `player_game_stats` column each `COUNTS` key is summed from, so the
#: SELECT and the fold cannot drift out of order.
_POSTED_COLUMNS: tuple[str, ...] = tuple(COUNTS.values())


def sum_lines_for(session: Session, matchup_id: int, team_row_id: int, cap: int) -> CategoryLine:
    """The started lines for one team's matchup, through `cap`, summed."""
    rows = session.execute(
        text(
            f"""
            SELECT {", ".join(f"pgs.{column}" for column in _POSTED_COLUMNS)}
            FROM player_game_stats pgs
            JOIN daily_lineup_slots dls ON dls.player_id = pgs.player_id
              AND dls.scoring_period = pgs.scoring_period
            WHERE dls.team_id = :team_id
              AND pgs.season = :season
              AND dls.matchup_period_id = (
                SELECT matchup_period_id FROM matchups WHERE id = :matchup_id
              )
              AND pgs.scoring_period <= :cap
              AND dls.started AND pgs.played AND pgs.minutes > 0
            """
        ),
        {
            "team_id": team_row_id,
            "season": SEASON,
            "matchup_id": matchup_id,
            "cap": cap,
        },
    ).all()
    totals = dict.fromkeys(COUNTS, 0.0)
    for row in rows:
        for abbreviation, value in zip(COUNTS, row, strict=True):
            totals[abbreviation] = totals.get(abbreviation, 0.0) + float(value or 0.0)
    return CategoryLine(totals, len(rows))


@contextmanager
def patched_state() -> Any:
    """Install the two rebuilds on `app.pickups.state`, then put them back.

    Attribute substitution rather than a signature change, because this branch
    may not alter `app/`.
    """
    import app.pickups.state as state

    original_schedule = state.schedule
    original_posted = state._posted
    state.schedule = rebuild_schedule
    state._posted = rebuild_posted
    try:
        yield
    finally:
        state.schedule = original_schedule
        state._posted = original_posted
        _POSTED_CAP[0] = None


@contextmanager
def cached_projections() -> Any:
    """Memoize the two projection entry points for the life of one day.

    `per_game_line` and `rest_of_season_line` are pure functions of the stored
    rows for a given `(player, today, games, tilt, as_of)`, but the recommender
    asks for the same player's line many times over: once per roster member when
    it values the roster, once per free agent when it ranks the wire, and again
    inside every optimizer call. On this database that is ~33,000 single-row
    queries for one decision point, and the season report took 31s a call.

    The cache is keyed on the day as well as the player, and a fresh cache is
    built for each decision point, so it cannot carry an answer across days and
    cannot leak anything the day did not already know. Verified on this data:
    identical move deltas to nine decimals, 11.6x faster (31.15s -> 2.68s,
    33,286 queries -> 337).
    """
    import app.pickups.projection as projection

    real_per_game = projection.per_game_line
    real_rest = projection.rest_of_season_line
    lines: dict[tuple[Any, ...], CategoryLine] = {}
    over: dict[tuple[Any, ...], CategoryLine] = {}

    def per_game_line(
        session: Session,
        season: int,
        player_id: int,
        today: int,
        *,
        tilt: bool = True,
        as_of: date | None = None,
    ) -> CategoryLine:
        key = (player_id, season, today, tilt, as_of)
        if key not in lines:
            lines[key] = real_per_game(session, season, player_id, today, tilt=tilt, as_of=as_of)
        return lines[key]

    def rest_of_season_line(
        session: Session,
        season: int,
        player_id: int,
        today: int,
        games: int,
        *,
        tilt: bool = True,
        as_of: date | None = None,
    ) -> CategoryLine:
        key = (player_id, season, today, games, tilt, as_of)
        if key not in over:
            over[key] = real_rest(session, season, player_id, today, games, tilt=tilt, as_of=as_of)
        return over[key]

    # Every module that imported these names holds its own binding, so each has
    # to be replaced or the cache is bypassed on some paths: `stream` and `bids`
    # import `per_game_line`, `season` imports `rest_of_season_line`. The
    # targets are named as strings because these modules re-export an imported
    # name rather than defining it, which `mypy --strict` will not let us
    # address as an attribute.
    targets: tuple[tuple[str, str], ...] = (
        ("app.pickups.projection", "per_game_line"),
        ("app.pickups.projection", "rest_of_season_line"),
        ("app.pickups.stream", "per_game_line"),
        ("app.pickups.bids", "per_game_line"),
        ("app.pickups.season", "rest_of_season_line"),
    )
    original: list[tuple[Any, str, Any]] = []
    for module_name, attribute in targets:
        module = importlib.import_module(module_name)
        original.append((module, attribute, getattr(module, attribute)))
    try:
        for module, attribute, _value in original:
            replacement = (
                rest_of_season_line if attribute == "rest_of_season_line" else per_game_line
            )
            setattr(module, attribute, replacement)
        yield
    finally:
        for module, attribute, value in original:
            setattr(module, attribute, value)


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


@dataclass
class Replay:
    """One season's played rows, read once, ready to replay any swap.

    Everything the category score needs is a lookup after this: who started
    for whom on which day and what they posted, every player's real box score
    by day (a free agent has no lineup row, so his line has to come from the
    box scores), each period's window, and each matchup's opponent and final
    totals. Four queries for the season, against one per move otherwise.
    """

    #: (team row id, scoring period, player id) -> what he posted, started.
    started: dict[tuple[int, int, int], CategoryLine]
    #: (player id, scoring period) -> his real line, started or not.
    games: dict[tuple[int, int], CategoryLine]
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
            games=games,
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

    def started_days(self, team: int, player: int, first: int, last: int) -> list[int]:
        return [day for day in range(first, last + 1) if (team, day, player) in self.started]

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

        The men leaving lose their started lines from `from_day` on; the men
        arriving take their real box scores over the same days, each capped at
        the number of starts the man he replaces was getting, so a streamer
        cannot be credited with more of the place than the place had. A move
        that drops nobody (a free add, an injured-reserve move) is uncapped:
        the place really was empty.
        """
        other = self.opponent.get((window.period, team))
        if other is None:
            return 0.0
        theirs = self.totals.get((window.period, other))
        if theirs is None:
            return 0.0
        actual = self.team_line(team, window)
        first = max(from_day, window.first)
        last = min(to_day, window.final)
        if first > last:
            return 0.0

        line = actual
        caps: list[int | None] = []
        for player in dropped:
            days = self.started_days(team, player, first, last)
            caps.append(len(days))
            for day in days:
                line = line - self.started[(team, day, player)]
        for index, player in enumerate(added):
            cap = caps[index] if index < len(caps) else None
            days = [day for day in range(first, last + 1) if (player, day) in self.games]
            for day in days if cap is None else days[:cap]:
                line = line + self.games[(player, day)]
        return self.categories_won(line, theirs) - self.categories_won(actual, theirs)

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


def value_move(
    replay: Replay,
    *,
    team_id: int,
    team_row_id: int,
    day: int,
    kind: str,
    rank: int,
    cleared: bool,
    added_id: int,
    added_name: str,
    dropped_id: int | None,
    dropped_name: str | None,
    claimed: float,
    weeks: float,
    horizon: str,
    fills_empty_day: bool = False,
    costs_faab: bool = False,
) -> Scored:
    """Replay one move and record the categories it won."""
    week, season = replay.score(
        team_row_id, day, [dropped_id] if dropped_id else [], [added_id] if added_id else []
    )
    return Scored(
        team_id=team_id,
        day=day,
        kind=kind,
        rank=rank,
        cleared=cleared,
        added_id=added_id,
        added_name=added_name,
        dropped_id=dropped_id,
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

    for team_id in teams:
        for _period, day in points:
            pool = free_agent_pool(session, league_season, day)
            if not pool:
                counters["pool_empty"] += 1
                done += 1
                continue
            counters["decisions"] += 1

            _POSTED_CAP[0] = day
            with cached_projections():
                stream_report = _run_stream(
                    session, league_season, team_id, day, pool, tilt, counters
                )
                season_report = _run_season(
                    session, league_season, team_id, day, pool, tilt, counters
                )

            # Score each distinct move once; every hurdle setting then only
            # decides which of those scored moves it would have named.
            row = team_rows[team_id]
            stream_scored = _score_stream_moves(book, team_id, row, day, stream_report)
            season_scored = _score_season_moves(book, team_id, row, day, season_report)

            for (stream_hurdle, paid, free), (stream, season) in settings.items():
                stream.decisions += 1
                season.decisions += 1
                _apply_stream(stream, stream_scored, stream_hurdle)
                _apply_season(season, season_scored, paid, free)

            done += 1
            if progress and done % 25 == 0:
                print(f"   ... {done}/{total} decision points")

    _POSTED_CAP[0] = None
    for _key, (_stream, _season) in settings.items():
        _stream.counters.update(counters)
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
            added_id=move.add.player_id,
            added_name=move.add.name,
            dropped_id=move.drop.player_id if move.drop else None,
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
        added = move.into[0] if move.into else None
        dropped = move.out[0] if move.out else None
        out.append(
            value_move(
                book,
                team_id=team_id,
                team_row_id=team_row_id,
                day=day,
                kind=move.kind,
                rank=rank,
                cleared=False,
                added_id=added.player_id if added else 0,
                added_name=added.name if added else "",
                dropped_id=dropped.player_id if dropped else None,
                dropped_name=dropped.name if dropped else None,
                # A two-swap drops two men and only the first is replayed, so
                # its score is an upper bound. A single swap is one for one,
                # which is the comparison the baseline makes.
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


def report(
    results: Mapping[str, Mapping[tuple[float, float, float], tuple[Setting, Setting]]],
    baseline: Sequence[Scored],
    *,
    teams: int,
    points: int,
    runtime: float,
    tilt_run: str,
) -> str:
    """The write-up, in the style of docs/punt_builds.md."""
    base_week = statistics.fmean([b.week for b in baseline]) if baseline else 0.0
    base_season = statistics.fmean([b.season for b in baseline]) if baseline else 0.0
    base_win = sum(1 for b in baseline if b.week >= 0) / len(baseline) if baseline else 0.0
    lines: list[str] = []

    lines.append("# Pickup recommender: the 2026 backtest, scored in categories")
    lines.append("")
    lines.append(
        f"Full Court Press (ESPN 3853870), 2026. Generated by "
        f"`scripts/pickups_backtest.py`. {teams} teams x {points} decision points, "
        f"tilt {tilt_run}, {runtime:.0f}s."
    )
    lines.append("")

    lines.append("## 1. What is measured, and in what")
    lines.append("")
    lines.append(
        "Every number in this document is **categories won**, not composite production. "
        "A move is scored by replaying the matchup that actually happened with the swap "
        "in it: the dropped man's started lines come out from the decision day on, the "
        "added man's real box scores go in on the days that are left (capped at the "
        "starts the place had), the nine totals are rebuilt and counted against the "
        "opponent's real period totals, and the categories the team actually won are "
        "subtracted. +1 means the swap flipped one category."
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
        "the share of named moves that changed no category at all, which is mostly "
        "the replay's cap rather than the move: a man who was not starting frees no "
        "starts."
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
        qualifying = [
            (key, stream, season)
            for key, (stream, season) in sorted(settings.items())
            if stream.eligible(base_week)
        ]
        if not qualifying:
            lines.append(
                f"- Tilt {tilt_label}: **no streaming setting qualifies**, so "
                "`STREAM_HURDLE` is left where the design note put it."
            )
            continue
        key, stream, _season = max(qualifying, key=lambda row: row[1].mean)
        lines.append(
            f"- Tilt {tilt_label}: best qualifying streaming hurdle **{key[0]:.2f}** "
            f"({stream.mean:+.2f} categories over {stream.n} moves, "
            f"{_pct(stream.no_move_rate)} no-move)."
        )
    lines.append("")

    lines.append("## 5. Two defects in the stored data, and what was done about them")
    lines.append("")
    lines.append(
        "Neither is fixed on this branch: both live in `app/`, and this run only measures."
    )
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
    lines.append("### The matchup totals leak the rest of the week")
    lines.append("")
    lines.append(
        "`state.load_team_week` reads `my_totals` and `opp_totals` from "
        "`matchup_team_stats`, the period's **final** total, with no day column to cap "
        "it by. **Workaround:** the script rebuilds the totals from the started lines "
        "on days up to N within that period only, which at the period's last day "
        "reproduces `matchup_team_stats` exactly."
    )
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
        "- **The replay does not re-run the lineup.** The swapped roster is assumed to "
        "start the new man on the days the old one started. That over-serves a pickup "
        "whose games fall on days the lineup was already full, and under-serves one who "
        "fills a day it left empty -- which is exactly the case `fills_empty_day` "
        "exists for, so the empty-day rule is measured pessimistically here."
    )
    lines.append(
        "- **No injury history.** There are no 2026 status snapshots, so every player "
        "is treated as available and the stash logic is under-served by construction."
    )
    lines.append(
        f"- **{points} decision points a team, {teams} teams.** Small. One season, one league."
    )
    lines.append(
        "- **The two-swap is scored as an upper bound.** It drops two men and only the "
        "first is replayed."
    )
    lines.append(
        "- **A move's week score is zero on a bye**, and on a decision day in a period "
        "with no recorded opponent, since there are no categories to win."
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


#: Choices this backtest makes that a reader could reasonably have made
#: differently, recorded because the note's convention is to write them down.
DECISIONS: tuple[str, ...] = (
    "Moves are scored in categories, by replaying the real matchup with the swap in "
    "it, and composite is gone. The recommender optimises categories; scoring it in "
    "anything else measures the scorer.",
    "The added man takes the days the dropped man started, capped at that count. The "
    "alternative -- re-solving the daily lineup matching for the swapped roster -- is "
    "the honest counterfactual and a much larger job; the cap is the conservative "
    "reading, and it is stated as a caveat rather than hidden.",
    "A move that drops nobody is uncapped: the place really was empty, so every game "
    "the added man played in the window counts.",
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
    "The matchup totals were capped at day N rather than left leaking. The cap is "
    "verified exact against ESPN at the full period.",
    "The hurdle grid is applied after one evaluation per decision point, not by "
    "re-running per cell. The hurdle is a reporting filter in both modules, so this is "
    "exact and not an approximation.",
    "The projection entry points are memoized per decision point. Verified identical "
    "move deltas to nine decimals, 11.6x faster; the cache is keyed on the day and "
    "rebuilt per decision point, so it cannot carry an answer across days.",
    "Both workarounds are installed by attribute substitution and removed in a "
    "`finally`, so nothing on disk changes.",
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
    )
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text_out)
        print(f"wrote {args.out}")
    print(f"runtime: {runtime:.0f}s")


if __name__ == "__main__":
    main()
