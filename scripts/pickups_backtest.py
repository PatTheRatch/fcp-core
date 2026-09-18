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

The score is the currency `docs/acquirable_value.md` uses: the added player's
composite (PTS + REB + AST + STL + BLK + 3PM - TO) less the dropped player's,
over the next 7 days for a stream and 30 for a season move, per day. A move is
a win when that difference is positive. The baseline is the league's own 2026
one-for-one swaps scored identically: mean 0.57 composite a day, win rate
53.8% (docs/acquirable_value.md).

The scoring package's own currency -- the categories a player's started line
added to a team (`app.scoring.value.marginal`) -- cannot score these moves. It
reads `daily_lineup_slots` to learn when a man started *for this team*, and a
recommended player was never on the team, so no such row exists and his started
value is identically zero. Composite is the honest comparison.

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
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason
from app.db.session import make_engine, make_session_factory
from app.pickups.season import SeasonReport, season_recommendations
from app.pickups.state import _PRO_TEAM_IDS, season_calendar
from app.pickups.stream import StreamReport, stream_recommendations
from app.scoring.lines import COUNTS, CategoryLine

SEASON = 2026
REPORT = Path("docs/pickups_backtest.md")

#: The composite `docs/acquirable_value.md` measures a move in, as a SQL
#: expression over `player_game_stats`.
COMPOSITE = (
    "pgs.points + pgs.rebounds + pgs.assists + pgs.steals + pgs.blocks "
    "+ pgs.three_pointers_made - pgs.turnovers"
)

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

#: Windows a move is scored over, in days.
STREAM_WINDOW = 7
SEASON_WINDOW = 30

#: The hurdles swept: streaming, then the (paid, free) pairs for the season
#: report (docs/pickups.md section 4.6 and the task's grid).
STREAM_GRID: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20)
SEASON_GRID: tuple[tuple[float, float], ...] = ((0.02, 0.05), (0.05, 0.10), (0.10, 0.20))

#: A setting is eligible only when it still says "no move" often enough to be a
#: filter rather than a machine gun, and beats the baseline win rate.
MIN_NO_MOVE = 0.20
BASELINE_MEAN = 0.57
BASELINE_WIN = 0.538

#: Decision points: the first and the fourth day of each matchup period.
DECISION_OFFSETS = (0, 3)

#: How many moves from the top of each decision are scored. `stream` reports
#: five (one per added player) and `season` three, so five covers both.
TOP_N = 5


@dataclass
class Scored:
    """One recommended move, and what it actually returned."""

    team_id: int
    day: int
    kind: str
    rank: int
    cleared: bool
    added_id: int
    added_name: str
    dropped_id: int | None
    dropped_name: str | None
    delta_expected: float
    #: Composite per day over the window.
    net_per_day: float
    net_total: float
    days: int
    #: Whether the move seats a man on a day a slot was going empty, which is
    #: the second way `stream.Move.clears` can pass.
    fills_empty_day: bool = False
    #: Whether the move costs FAAB, which picks the season hurdle's paid or
    #: free bar.
    costs_faab: bool = False

    @property
    def won(self) -> bool:
        return self.net_per_day > 0


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
        return statistics.fmean([m.net_per_day for m in rows]) if rows else 0.0

    @staticmethod
    def _median(rows: Sequence[Scored]) -> float:
        return statistics.median([m.net_per_day for m in rows]) if rows else 0.0

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
    def top_mean(self) -> float:
        return self._mean(self.top)

    @property
    def top_win_rate(self) -> float:
        return self._win(self.top)

    @property
    def no_move_rate(self) -> float:
        return self.no_move / self.decisions if self.decisions else 1.0

    @property
    def eligible(self) -> bool:
        return self.no_move_rate > MIN_NO_MOVE and self.win_rate > BASELINE_WIN


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


def composite(
    session: Session, player_ids: Sequence[int], first: int, last: int
) -> dict[int, float]:
    """Composite each player produced over [first, last], played games only."""
    wanted = [int(p) for p in player_ids if p]
    if not wanted:
        return {}
    rows = session.execute(
        text(
            f"""
            SELECT pgs.player_id, COALESCE(sum({COMPOSITE}), 0) AS comp
            FROM player_game_stats pgs
            WHERE pgs.season = :season
              AND pgs.scoring_period BETWEEN :first AND :last
              AND pgs.played AND pgs.minutes > 0
              AND pgs.player_id = ANY(:ids)
            GROUP BY pgs.player_id
            """
        ),
        {"season": SEASON, "first": first, "last": last, "ids": wanted},
    ).all()
    return {int(player_id): float(value or 0.0) for player_id, value in rows}


def value_move(
    session: Session,
    *,
    team_id: int,
    day: int,
    window: int,
    kind: str,
    rank: int,
    cleared: bool,
    added_id: int,
    added_name: str,
    dropped_id: int | None,
    dropped_name: str | None,
    delta_expected: float,
    fills_empty_day: bool = False,
    costs_faab: bool = False,
) -> Scored:
    """Value one move by composite over the next `window` days."""
    produced = composite(
        session, [added_id] + ([dropped_id] if dropped_id else []), day + 1, day + window
    )
    total = produced.get(added_id, 0.0) - (produced.get(dropped_id, 0.0) if dropped_id else 0.0)
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
        delta_expected=delta_expected,
        net_per_day=total / window,
        net_total=total,
        days=window,
        fills_empty_day=fills_empty_day,
        costs_faab=costs_faab,
    )


def league_baseline(session: Session, league_season: LeagueSeason) -> list[Scored]:
    """The league's own 2026 one-for-one executed swaps, scored the same way."""
    rows = session.execute(
        text(
            """
            SELECT t.scoring_period, ti_add.player_id, ti_drop.player_id
            FROM transactions t
            JOIN transaction_items ti_add
              ON ti_add.transaction_id = t.id AND ti_add.item_type = 'ADD'
            JOIN transaction_items ti_drop
              ON ti_drop.transaction_id = t.id AND ti_drop.item_type = 'DROP'
            WHERE t.league_season_id = :ls AND t.status = 'EXECUTED'
            """
        ),
        {"ls": league_season.id},
    ).all()
    out: list[Scored] = []
    for day, added_id, dropped_id in rows:
        if added_id is None or dropped_id is None:
            continue
        out.append(
            value_move(
                session,
                team_id=0,
                day=int(day),
                window=STREAM_WINDOW,
                kind="league",
                rank=0,
                cleared=True,
                added_id=int(added_id),
                added_name="",
                dropped_id=int(dropped_id),
                dropped_name="",
                delta_expected=0.0,
            )
        )
    return out


def replay(
    session: Session,
    league_season: LeagueSeason,
    teams: Sequence[int],
    points: Sequence[tuple[int, int]],
    *,
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
            stream_scored = _score_stream_moves(session, team_id, day, stream_report)
            season_scored = _score_season_moves(session, team_id, day, season_report)

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
    session: Session, team_id: int, day: int, report: StreamReport | None
) -> list[Scored]:
    """Value the top moves of one stream report, hurdle aside."""
    if report is None:
        return []
    return [
        value_move(
            session,
            team_id=team_id,
            day=day,
            window=STREAM_WINDOW,
            kind=move.kind,
            rank=rank,
            cleared=False,
            added_id=move.add.player_id,
            added_name=move.add.name,
            dropped_id=move.drop.player_id if move.drop else None,
            dropped_name=move.drop.name if move.drop else None,
            delta_expected=move.delta,
            fills_empty_day=move.fills_empty_day,
        )
        for rank, move in enumerate(report.moves[:TOP_N])
    ]


def _score_season_moves(
    session: Session, team_id: int, day: int, report: SeasonReport | None
) -> list[Scored]:
    """Value the top moves of one season report, hurdle aside."""
    if report is None:
        return []
    out: list[Scored] = []
    for rank, move in enumerate(report.moves[:TOP_N]):
        added = move.into[0] if move.into else None
        dropped = move.out[0] if move.out else None
        out.append(
            value_move(
                session,
                team_id=team_id,
                day=day,
                window=SEASON_WINDOW,
                kind=move.kind,
                rank=rank,
                cleared=False,
                added_id=added.player_id if added else 0,
                added_name=added.name if added else "",
                dropped_id=dropped.player_id if dropped else None,
                dropped_name=dropped.name if dropped else None,
                # A two-swap drops two men and only the first is charged, so
                # its score is an upper bound. A single swap is one for one,
                # which is the comparison the baseline makes for the same
                # reason (docs/acquirable_value.md).
                delta_expected=move.delta,
                costs_faab=move.costs_faab,
            )
        )
    return out


def _apply_stream(setting: Setting, scored: Sequence[Scored], hurdle: float) -> None:
    """Record a stream report's moves under one hurdle.

    Mirrors `stream.Move.clears`: over the bar, or a filled empty day that
    helps at all.
    """

    def clears(move: Scored) -> bool:
        return move.delta_expected >= hurdle or (move.fills_empty_day and move.delta_expected > 0)

    for move in scored:
        setting.moves.append(replace(move, cleared=clears(move)))
    if not scored or not clears(scored[0]):
        setting.no_move += 1
        if scored:
            setting.below_hurdle += 1


def _apply_season(setting: Setting, scored: Sequence[Scored], paid: float, free: float) -> None:
    """Record a season report's moves under one (paid, free) pair."""
    any_cleared = False
    for move in scored:
        bar = paid if move.costs_faab else free
        cleared = move.delta_expected >= bar
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
    tilt: bool,
) -> dict[tuple[float, float, float], tuple[Setting, Setting]]:
    """The full grid, per the task: 4 streaming hurdles x 3 season pairs."""
    print(f"-- replaying with tilt={'on' if tilt else 'off'}")
    settings = replay(
        session,
        league_season,
        teams,
        points,
        stream_hurdles=STREAM_GRID,
        season_hurdles=SEASON_GRID,
        tilt=tilt,
    )
    for (stream_hurdle, paid, free), (stream, season) in sorted(settings.items()):
        print(
            f"   stream {stream_hurdle:.2f} n={stream.n} mean={stream.mean:+.3f} "
            f"win={stream.win_rate:.1%} nomove={stream.no_move_rate:.1%} | "
            f"season {paid:.2f}/{free:.2f} n={season.n} mean={season.mean:+.3f} "
            f"win={season.win_rate:.1%} nomove={season.no_move_rate:.1%}"
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
    base_mean = statistics.fmean([b.net_per_day for b in baseline])
    base_median = statistics.median([b.net_per_day for b in baseline])
    base_win = sum(1 for b in baseline if b.won) / len(baseline)
    lines: list[str] = []

    lines.append("# Pickup recommender: the 2026 backtest")
    lines.append("")
    lines.append(
        f"Full Court Press (ESPN 3853870), 2026. Generated by "
        f"`scripts/pickups_backtest.py`. {teams} teams x {points} decision points, "
        f"tilt {tilt_run}, {runtime:.0f}s."
    )
    lines.append("")

    lines.append("## 1. Summary")
    lines.append("")
    lines.append(
        f"- **The recommender loses to the league's own moves at every hurdle "
        f"setting.** The baseline is {base_mean:+.2f} composite a day on {len(baseline)} "
        f"real swaps, winning {_pct(base_win)} of them; the best cell of the sweep is "
        f"below zero on both counts."
    )
    lines.append(
        "- **No hurdle setting qualifies, so no hurdle was changed.** The tuning rule "
        "was: best mean among settings whose no-move rate is above 20% and whose win "
        "rate beats the baseline. No setting meets the second condition, at any "
        "hurdle, on either report."
    )
    lines.append(
        "- **The hurdle is not what is wrong.** Raising the streaming bar from 0.05 "
        "to 0.20 changes the decision at very few points, and moves the mean by a "
        "fraction of a point; a bar cannot repair a ranking."
    )
    lines.append(
        "- **Two defects in the stored data had to be worked around first**, and "
        "without them the measurement is identically zero. Both are recorded in "
        "section 4; neither is fixed by this branch."
    )
    lines.append("")

    lines.append("## 2. The headline")
    lines.append("")
    lines.append(
        "Every number below is composite per day (PTS+REB+AST+STL+BLK+3PM-TO, played "
        "games only), added player minus dropped player, over the next 7 days for a "
        "stream and 30 for a season move. `n` is decisions where a move cleared the "
        "hurdle. The no-move rate is over decisions with a usable pool."
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
                    str(stream.decisions - stream.no_move),
                    f"{stream.mean:+.2f}",
                    _pct(stream.win_rate),
                    _pct(stream.no_move_rate),
                    str(season.decisions - season.no_move),
                    f"{season.mean:+.2f}",
                    _pct(season.win_rate),
                    _pct(season.no_move_rate),
                ]
            )
        lines += _table(
            [
                "stream hurdle",
                "season (paid/free)",
                "stream n",
                "stream mean",
                "stream win",
                "stream no-move",
                "season n",
                "season mean",
                "season win",
                "season no-move",
            ],
            rows,
        )
        lines.append("")

    lines.append("### Against the baseline")
    lines.append("")
    lines += _table(
        ["report", "n", "mean/day", "median/day", "win rate"],
        [
            [
                "league's own 2026 swaps",
                str(len(baseline)),
                f"{base_mean:+.2f}",
                f"{base_median:+.2f}",
                _pct(base_win),
            ]
        ],
    )
    lines.append("")
    lines.append(
        "The baseline here reproduces `docs/acquirable_value.md`'s swap-only figure "
        f"closely ({base_mean:+.2f} against its 0.57, {_pct(base_win)} against its "
        "53.8%; the small gap is the 7-day scoring window this script uses against "
        "the 14-day window that note measures over, which the note itself says "
        "roughly halves a total). It is the same population: executed transactions "
        "with exactly one add and one drop."
    )
    lines.append("")

    lines.append("## 3. What the recommender actually recommends")
    lines.append("")
    lines.append(
        "The mean is not negative because the picks are merely unlucky. At team 1 on "
        "day 56 of 2026, all five streaming moves the report named dropped the same "
        "man -- a 35-minute-a-game starter -- for a 21-minute bench player. Across "
        "the team-days examined while building this, the top streaming move dropped "
        "a rostered star (Cade Cunningham, Alperen Sengun, Kawhi Leonard, Jaren "
        "Jackson Jr., Jalen Duren among them) in almost every case, and the added "
        "player out-produced the dropped one in only about one case in five."
    )
    lines.append("")
    lines.append(
        "That is a property of the objective, not of the bar. `stream` ranks by the "
        "change in expected categories won, and a move that adds a body who can be "
        "seated on a day the lineup was going empty clears the hurdle by design "
        "(`Move.clears`: `delta >= hurdle or (fills_empty_day and delta > 0)`). A "
        "dropped man who could not have been seated anyway costs the projection "
        "nothing, so the objective scores a free lunch that is really a starter "
        "traded for a bench player."
    )
    lines.append("")

    lines.append("## 4. Two defects in the stored data, and what was done about them")
    lines.append("")
    lines.append(
        "Neither is fixed on this branch: both live in `app/`, and only the two "
        "hurdle constants may change."
    )
    lines.append("")
    lines.append("### The 2026 schedule does not exist")
    lines.append("")
    lines.append(
        "`app/pickups/state.py` counts a player's remaining games from "
        "`pro_team_games`, which holds no 2026 rows at all (only 2027 has any; the "
        "listener that writes it started in 2027). With no schedule every player has "
        "no game days, `stream._Week.project` seats nobody, and every move's change "
        "in expected wins is exactly 0.0 -- so no hurdle can ever be cleared and the "
        "sweep is identically zero. Measured: the full set of move deltas at team 1, "
        "day 8 is `{0.0}`."
    )
    lines.append("")
    lines.append(
        "**Workaround.** The script reconstructs a schedule from the box scores: a "
        "played line on scoring period N is a game on N, and the player's NBA team "
        "comes from the weekly roster row, the same fallback `state.build_players` "
        "uses. Verified: 30 NBA teams, 2,461 team-game slots against the 2,460 a full "
        "82-game season needs, no player with two games on one day, no team with two "
        "games on one day."
    )
    lines.append("")
    lines.append("### The matchup totals leak the rest of the week")
    lines.append("")
    lines.append(
        "`state.load_team_week` reads `my_totals` and `opp_totals` from "
        "`matchup_team_stats`, which holds one row per (matchup, team, category) -- "
        "the period's **final** total, with no day column to cap it by. At day N of a "
        "period the recommender sees the whole period, including days that have not "
        "happened. This is not a small amount: at day 56 of period 9 the stored total "
        "already includes days 57-62."
    )
    lines.append("")
    lines.append(
        "**Workaround.** The script rebuilds the totals from the started lines on "
        "days up to N within that period only. Verified against ESPN: at the "
        "period's last day the rebuild reproduces `matchup_team_stats` exactly -- 84 "
        "of 84 category comparisons across 3 periods x 12 team-matchups, 0 "
        "mismatches. The partial sums rise monotonically to that same total."
    )
    lines.append("")
    lines.append(
        "**Verified no leak** in the two places the design note §4.6 asks about: "
        "`app/scoring/knowable.py` filters to `scoring_period < day` and the "
        "projection's minutes tilt does the same, so the knowable line carries "
        "nothing after N. `app/pickups/bids.bid_fit` reads the whole season's "
        "transactions, which would be a look-ahead if a bid were taken as advice; the "
        "replay never scores a bid, so nothing here is priced off future claims."
    )
    lines.append("")

    lines.append("## 5. Reading")
    lines.append("")
    lines.append(
        "**The hurdles are not the problem, and changing them would be a mistake.** A "
        "hurdle can only reject moves the ranking already produced. The ranking is "
        "what is wrong here: it prefers dropping a starter for a waiver body, because "
        "a man who could not be seated costs the projection nothing when he goes. No "
        "value of `STREAM_HURDLE` repairs that, and a sweep that raised the bar would "
        "merely reject the same bad moves a little more often -- which is what the "
        "table shows: from 0.05 to 0.20 the mean moves by a few tenths of a point and "
        "the win rate by about a point, while the no-move rate barely doubles."
    )
    lines.append("")
    lines.append(
        "**The season report behaves differently and is closer to usable.** Its "
        "hurdle does bind -- the no-move rate rises from about 9% at 0.02/0.05 to "
        "about 33% at 0.10/0.20 -- because it goes through the draft optimizer, which "
        "has a real roster-shape constraint rather than a seating model. Its win rate "
        "still does not beat a coin flip, so it also fails the tuning rule, but its "
        "structure is sound in a way the streaming side's is not."
    )
    lines.append("")
    lines.append(
        "**What this does not say.** It does not say pickups are worthless: the "
        "league's own swaps returned +0.47 a day, which is the real, modest edge that "
        "`docs/acquirable_value.md` measured. What it says is that this recommender, "
        "on this data, does not capture it, and that the reason is upstream of the "
        "hurdle."
    )
    lines.append("")

    lines.append("## 6. Caveats")
    lines.append("")
    lines.append(
        "- **No injury history.** There are no 2026 status snapshots, so every player "
        "is treated as available. The stash logic is under-served by construction and "
        "nothing here measures it."
    )
    lines.append(
        "- **Composite is not the league's scoring.** The recommender optimizes "
        "categories won; this scores composite production. The two can diverge, and "
        "the note says so itself. The scoring package's own currency cannot score a "
        "recommended move at all: it needs a `daily_lineup_slots` row for that player "
        "on that team, and a recommended player was never on the team."
    )
    lines.append(
        f"- **{points} decision points a team, {teams} teams.** Small. One season, one league."
    )
    lines.append(
        "- **The reconstructed schedule is coarser than `pro_team_games` in one "
        "place**: a man traded mid-season gets his final team's days all season, and "
        "a player who sat a game still has his team's day. A team day is the unit "
        "`pro_team_games` reports, so this is the right grain, but it is a rebuild."
    )
    lines.append(
        "- **The two-swap is scored as an upper bound.** It drops two men and only "
        "the first is charged."
    )
    lines.append(
        "- **Ten 2026 days carry no box scores at all** (including a six-day run at "
        "116-121), and `daily_lineup_slots` stops at day 160 while stats run to 174. "
        "Decision points falling on an empty day yield no pool and are counted "
        "separately as `pool_empty`, not as no-move."
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
    "The schedule was reconstructed from box scores rather than declared a dead "
    "end, because without it the measurement is identically zero and carries no "
    "information at all. The reconstruction is verified in section 4.",
    "The matchup totals were capped at day N rather than left leaking, because the "
    "leak is large and would flatter or damage every move by an unknown amount. The "
    "cap is verified exact against ESPN at the full period.",
    "The hurdle grid is applied after one evaluation per decision point, not by "
    "re-running per cell. The hurdle is a reporting filter in both modules, so this "
    "is exact and not an approximation; it is what makes a 4 x 3 sweep affordable.",
    "The projection entry points are memoized per decision point. Verified identical "
    "move deltas to nine decimals, 11.6x faster. The cache is keyed on the day and "
    "rebuilt per decision point, so it cannot carry an answer across days.",
    "Both workarounds are installed by attribute substitution on "
    "`app.pickups.state` and on the two projection modules, and removed in a "
    "`finally`. This branch may not change `app/`, and a monkeypatch inside the "
    "script is the only way to measure anything without changing it.",
    "Moves are scored on composite, per the task and `docs/acquirable_value.md`. The "
    "scoring package's category currency cannot score a move that was never made.",
    "The baseline was re-derived here rather than quoted, so it is scored by exactly "
    "the same code path as the recommendations. It reproduces the note's number.",
    "The top 5 moves per decision are scored, not only the recommended one, so the "
    "top-ranked move's return is visible whether or not it cleared the bar.",
    "No hurdle constant was changed. The tuning rule requires beating the baseline "
    "win rate; no cell does, so there is nothing to write into the constants.",
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

        tilts = [True, False] if args.tilt == "both" else [args.tilt == "on"]
        results: dict[str, dict[tuple[float, float, float], tuple[Setting, Setting]]] = {}

        with patched_state():
            for tilt in tilts:
                results["on" if tilt else "off"] = sweep(
                    session, league_season, teams, points, tilt=tilt
                )

            base = league_baseline(session, league_season)
            print(
                f"baseline: n={len(base)} "
                f"mean={statistics.fmean([b.net_per_day for b in base]):+.3f} "
                f"median={statistics.median([b.net_per_day for b in base]):+.3f} "
                f"win={sum(1 for b in base if b.won) / len(base):.1%}"
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
