"""Grading a roster move: players in, players out, both lenses.

Trades (S11) and waiver moves (S12) are the same question asked of
different transactions: from the move to the end of the stretch, did the
team's expected category wins a week go up? One engine answers it for both.

WINDOW

The matchup periods after the move's day, to the end of the regular season
(playoffs are graded as their own stretch), cut off at the last period the
team still held any incoming player. After that the spot was refilled from
the wire, which both sides of the move would have faced alike. A move whose
incoming players were never held again is graded on the period after it.

A SHORTER WINDOW: `within`

Over a full rest of season a traded player is traded again, dropped, hurt or
given a different role, and `docs/trades.md` section 7 measured how little of
that a forecast can carry. So the window can be cut short: `within` is the
last scoring period a graded matchup period may *begin* on, and
`day + 30` -- the thirty days `scripts/pickups_backtest.py` scores a pickup
over -- is the short horizon the trade calibration uses. Whole matchup periods
either way, because the comparison is against the opponent's period totals and
half a matchup has no opponent; a period that starts inside the window and
ends a few days outside it is counted whole, which is the only approximation
in the cut.

RESULT (what it delivered)

Per period, the team as it actually played, against the same team with the
incoming players' started lines taken out and the outgoing players' lines
put back:

    result = EW(team week) - EW(team week - incoming started + outgoing counterfactual)

The outgoing counterfactual is the games he played, for anyone, on the days
the move's spot was its own: the days the team held what came in (every day
after the move, for a drop), times the share of his games the team had started
while it held him. So a bench player dropped is not credited with lines he
would not have started, and a streamer held two days is weighed against two
days of the player he replaced, not the rest of the week, when the next
pickup had the spot.

Uneven counts are settled at replacement level (`app.scoring.replacement`),
and the two directions are not the same number. Each spot the move *used*
costs a typical pickup, because a man was added to it. Each spot the move
*opened* is worth what an opened spot returns when it is streamed --
`OPENED_PLACE`, 0.38 categories a week, measured in `docs/streaming_lane.md`
and adopted as revision R2 (`docs/trades.md` section 7b) -- rather than the
0.07 flat median this charged until then. Averaged over the window's periods,
in categories a week.

`opened_place` is a parameter so that the old settlement can be asked for by
name: passing the flat replacement level reproduces exactly what this function
graded before R2, which is how the calibration separates a change in the
yardstick from a change in the forecast.

DECISION (what was knowable)

The same comparison on knowable lines as of the move's day
(`app.scoring.knowable`): the team's average week before the move, with the
outgoing players' knowable lines swapped for the incoming players', each over
`GAMES_PER_WEEK` games scaled by his availability so far that season.

Known limits. Injury news on the day is not in the data (daily lineup injury
fields are not a time series), so a player dropped for a season-ending injury
is judged on his availability to date. And the counterfactual assumes a
dropped player's minutes would have been the same on this team.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import DailyLineupSlot, MatchupPeriod, PlayerGameStat
from app.scoring.knowable import knowable
from app.scoring.league import average_team_line
from app.scoring.lines import COUNTS, CategoryLine, sum_lines
from app.scoring.players import held_weeks
from app.scoring.replacement import OPENED_PLACE, opened_places
from app.scoring.season import SeasonBook
from app.scoring.value import expected_wins, period_length
from app.scoring.verdicts import Verdict, verdict

#: Games a player who plays at all plays in a seven-day week: the mean over
#: player-weeks with at least one game, 2.91-3.18 in every season 2019-2026
#: (measured 2026-09-16).
GAMES_PER_WEEK = 3.0

#: Days before a player's availability so far is trusted over full health.
AVAILABILITY_MIN_DAYS = 21


@dataclass(frozen=True)
class MoveGrade:
    team_id: int
    day: int
    players_in: tuple[int, ...]
    players_out: tuple[int, ...]
    playoffs: bool
    #: The matchup periods graded.
    periods: tuple[int, ...]
    #: Categories a week, knowable at the time.
    decision: float
    #: Categories a week, delivered.
    result: float
    verdict: Verdict


def _week_days(book: SeasonBook, period: int) -> int:
    return period_length(book.periods[period]) or 7


def _games_on(book: SeasonBook, player_id: int, days: set[int]) -> CategoryLine:
    """Every game the player played on these days, for any team or none."""
    if not days:
        return CategoryLine({}, 0)
    columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    rows = book.session.execute(
        select(*columns).where(
            PlayerGameStat.player_id == player_id,
            PlayerGameStat.season == book.league_season.season,
            PlayerGameStat.scoring_period.in_(sorted(days)),
            PlayerGameStat.played.is_(True),
        )
    ).all()
    return CategoryLine(
        {key: sum(float(row[i] or 0.0) for row in rows) for i, key in enumerate(COUNTS)},
        len(rows),
    )


def _spot_days(
    book: SeasonBook, team_id: int, day: int, players_in: Sequence[int], period: int
) -> set[int]:
    """The days in a period, after the move, that the move's roster spots were its own.

    With players in, the days the team held any of them. With none (a drop), every
    day of the period after the move.
    """
    found = book.periods[period]
    first = max(int(found.first_scoring_period or 0), day + 1)
    final = int(found.final_scoring_period or 0)
    if not players_in:
        return set(range(first, final + 1))
    held = book.session.scalars(
        select(DailyLineupSlot.scoring_period).where(
            DailyLineupSlot.team_id == team_id,
            DailyLineupSlot.player_id.in_(list(players_in)),
            DailyLineupSlot.scoring_period.between(first, final),
            DailyLineupSlot.slot != "FA",
        )
    ).all()
    return {int(d) for d in held}


def start_share(book: SeasonBook, team_id: int, player_id: int, before_day: int) -> float:
    """The share of his games the team started while it held him, before `before_day`."""
    rows = book.session.execute(
        select(DailyLineupSlot.started)
        .join(
            PlayerGameStat,
            (PlayerGameStat.player_id == DailyLineupSlot.player_id)
            & (PlayerGameStat.scoring_period == DailyLineupSlot.scoring_period)
            & (PlayerGameStat.season == book.league_season.season),
        )
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == book.league_season.id,
            DailyLineupSlot.team_id == team_id,
            DailyLineupSlot.player_id == player_id,
            DailyLineupSlot.scoring_period < before_day,
            PlayerGameStat.played.is_(True),
        )
    ).all()
    if not rows:
        return 1.0
    return sum(1 for (started,) in rows if started) / len(rows)


def _window(
    book: SeasonBook,
    team_id: int,
    day: int,
    players_in: Sequence[int],
    playoffs: bool,
    within: int | None = None,
) -> tuple[int, ...]:
    after = [
        number
        for number, period in sorted(book.periods.items())
        if period.first_scoring_period is not None
        and period.final_scoring_period is not None
        and period.final_scoring_period > day
        and bool(period.is_playoff) == playoffs
        and (within is None or int(period.first_scoring_period) <= within)
    ]
    if not after:
        return ()
    held = held_weeks(book.session, book.league_season.id, team_id)
    tenure = [p for pid in players_in for p in held.get(pid, set()) if p in after]
    if playoffs and players_in and not tenure:
        # Gone before the playoffs: the move has no playoff stretch to grade.
        return ()
    last = max(tenure) if tenure else after[0]
    return tuple(p for p in after if p <= last)


def _knowable_line(book: SeasonBook, player_id: int, day: int) -> CategoryLine:
    season = int(book.league_season.season)
    known = knowable(book.session, player_id, season, day)
    availability = 1.0
    if day > AVAILABILITY_MIN_DAYS:
        expected = (day - 1) * GAMES_PER_WEEK / 7
        availability = min(1.0, known.games_so_far / expected) if expected else 1.0
    return known.over(GAMES_PER_WEEK * availability)


def _team_before(book: SeasonBook, team_id: int, day: int) -> CategoryLine:
    """The team's average seven-day week before the move, or the league's."""
    weeks = [
        book.team_week(team_id, number)
        for number, period in book.periods.items()
        if not period.is_playoff
        and period.final_scoring_period is not None
        and period.final_scoring_period < day
        and _week_days(book, number) == 7
    ]
    weeks = [w for w in weeks if w.games]
    if not weeks:
        return average_team_line(book.session, int(book.league_season.season), 7)
    return sum_lines(weeks).scaled(1 / len(weeks))


def grade_move(
    book: SeasonBook,
    team_id: int,
    day: int,
    players_in: Sequence[int],
    players_out: Sequence[int],
    *,
    replacement: float,
    playoffs: bool = False,
    band: float | None = None,
    within: int | None = None,
    opened_place: float = OPENED_PLACE,
) -> MoveGrade | None:
    """Both lenses for one move, or None when no period of the stretch follows it.

    `within` is the last scoring period a graded matchup period may *begin*
    on: the short-window grade (see WINDOW above). Everything else is
    unchanged, so the same arithmetic answers both horizons.

    `opened_place` is what the first spot the move empties is worth (see
    WINDOW above): the streamed lane by default, and the flat `replacement`
    level when a caller wants the settlement this used before revision R2.
    """
    periods = _window(book, team_id, day, players_in, playoffs, within)
    if not periods:
        return None
    emptied = len(players_out) - len(players_in)
    spots = (
        opened_places(emptied, replacement, first=opened_place)
        if emptied > 0
        else emptied * replacement
    )

    results = []
    shares = {pid: start_share(book, team_id, pid, day) for pid in players_out}
    for period in periods:
        team = book.team_week(team_id, period)
        incoming = sum_lines(book.player_week(team_id, period, pid) for pid in players_in)
        days = _spot_days(book, team_id, day, players_in, period)
        outgoing = sum_lines(_games_on(book, pid, days).scaled(shares[pid]) for pid in players_out)
        opponents = book.opponents.for_period(book.periods[period])
        without = team - incoming + outgoing
        results.append(expected_wins(team, opponents) - expected_wins(without, opponents))
    result = sum(results) / len(results) + spots

    base = _team_before(book, team_id, day)
    knowable_in = sum_lines(_knowable_line(book, pid, day) for pid in players_in)
    knowable_out = sum_lines(_knowable_line(book, pid, day) for pid in players_out)
    seven_day = book.opponents.for_days(7)
    after_move = base - knowable_out + knowable_in
    decision = expected_wins(after_move, seven_day) - expected_wins(base, seven_day) + spots

    judged = verdict(decision, result) if band is None else verdict(decision, result, band=band)
    return MoveGrade(
        team_id=team_id,
        day=day,
        players_in=tuple(players_in),
        players_out=tuple(players_out),
        playoffs=playoffs,
        periods=periods,
        decision=decision,
        result=result,
        verdict=judged,
    )
