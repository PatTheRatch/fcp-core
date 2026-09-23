#!/usr/bin/env python3
"""Does the forward trade evaluator predict what the trade actually did?

Usage:
    python scripts/trade_calibration.py
    python scripts/trade_calibration.py --seasons 2026 --out OUT/calibration.md

Read-only. For every reconstructed trade this database can recover
(`app.scoring.trades.reconstruct_trades`), it runs `app.trades.evaluate_trade`
**as of the morning the deal was made** and compares what the evaluator said
each side would gain, in categories a week, with what
`app.scoring.trade_grades` says the side actually got.

WHICH DAY IS "THE MORNING IT WAS MADE"

`Trade.day` is the last scoring period the moving players were in their old
team's lineup. The four executed 2026 `TRADE_ACCEPT` rows all have their
transaction stamped on `day + 1`, with the players in their new lineups that
same day, so the deal was agreed overnight and landed the next morning. The
evaluator is therefore run with `today = trade.day`, when both rosters are
still the pre-trade ones, and `review_days = 1`, so it seats the deal from
exactly the day the ledger says it seated. Running it on `day + 1` instead
would read the post-trade roster and judge the deal as though it had already
happened.

WHAT IS COMPARED

Two headline numbers against two horizons, the 2x2 declared in
`docs/trades.md` section 7a before any of it was run, and since the
declaration of section 7b a third row pair beside it.

The two predictions, both categories a week, both from one evaluation:

- **R1, the roster with-and-without** (`Judgement.delta_season_per_week`):
  what this team's ordinary week is worth with the deal in it, less the same
  week without it. The headline since revision R1.
- **the per-man number** (`SideReport.season_independent`): each man valued
  on his own inside a league-average team, summed over the places the deal
  touches. The headline the calibration of 2026-09-21 ran on.

Both of them now price a place the deal *empties* at what a streamed place
returns (revision R2, `docs/trades.md` section 7b): 0.38 categories a week
rather than the 0.06 floor under a single add.

The two horizons, both `MoveGrade.result` -- what the team actually posted
with the incoming players against the same team with them taken out and the
outgoing ones put back:

- **rest of season**, the periods the team held what came in, up to fourteen
  of them. Yesterday's target.
- **the next thirty days** (`SHORT_WINDOW`), the window
  `scripts/pickups_backtest.py` scores a pickup over, built by giving
  `grade_move` a `within` day. Whole matchup periods, the ones that *begin*
  inside the window, because the grade compares a period's totals with the
  opponent's and half a matchup has no opponent.

THE YARDSTICK, BOTH WAYS

Revision R2 changed the hindsight grade as well as the forecast: `grade_move`
now credits a spot the move opened at the streamed lane rather than at the
flat median pickup. Two changes at once cannot be read from one number, so
every deal is graded twice -- once under R2 and once with `opened_place` set
back to the flat level, which is exactly what the grade did before -- and the
run prints the R2 prediction against both. The difference between those two
row pairs is the yardstick; the difference between R2 and the run of
2026-09-21 is the yardstick and the forecast together.

`MoveGrade.decision`, the knowable-at-the-time grade, is reported beside them:
the evaluator and the decision lens see the same day's data through different
arithmetic, so a big gap between those two is a bug in one of them, while a
gap to `result` is mostly the future.

A coin's 95% interval is printed beside every deal-level hit rate, because
with 55 deals a hit rate is mostly noise and a reader is owed the yardstick.

WHAT THIS CANNOT MEASURE

A played season has no listener data: no injury statuses, no ownership, no
recorded wire. So the evaluator runs with every player treated as available
and with the reconstructed wire (`app.pickups.state.historical_free_agents`,
whoever played that day and was in nobody's lineup). Both flatter it -- a man
who was day-to-day when the deal was made is projected as healthy on both
sides -- and neither can be fixed from stored rows. The sample is also small,
and the write-up says so in its first sentence.

The two engines still settle an uneven trade differently, though less than
they did: both now price a place the deal empties at the streamed lane, but
this one also forces a drop and charges what that place was worth, and it
fills the opened place with the named best free agent's whole week rather than
with a scalar. Both are honest; they are not identical, and the uneven rows
are reported separately for that reason.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason, PlayerGameStat, ProTeamGame, Team
from app.db.session import make_engine, make_session_factory
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.judge import standard_lens
from app.pickups.state import build_players
from app.scoring.lines import COUNTS, CategoryLine
from app.scoring.moves import MoveGrade, grade_move
from app.scoring.replacement import OPENED_PLACE, pickup_values
from app.scoring.season import SeasonBook
from app.scoring.trade_grades import TradeGrade, trade_grades
from app.scoring.trades import Trade, reconstruct_trades
from app.scoring.wire import median_of
from app.trades import PlayerCard, SideReport, TeamOffer, TradeReport, evaluate_trade

#: Seasons with any reconstructable trade at all. 2020's two sides are both
#: "part missing" and 2022 had no trades, so neither can be scored; they are
#: listed anyway so the write-up can say why.
DEFAULT_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)

#: Days after the deal the short horizon covers, matching
#: `scripts/pickups_backtest.py`'s `SEASON_WINDOW`: a pickup and a trade are
#: then measured over the same stretch of a season.
SHORT_WINDOW = 30

#: Days in a matchup week, the unit a delivered line is divided into so that
#: it is in the same categories-a-week as the prediction.
DAYS_A_WEEK = 7.0

#: The share of the deals whose predicted edge counts as the confident third.
#: Declared before the run (docs/trades.md section 7a) and defined on the
#: prediction alone, so the split is one the evaluator could have made on the
#: morning.
CONFIDENT_SHARE = 3

#: A player who played fewer than this share of his NBA team's scheduled
#: games over the graded window broke down after the deal. Half is a blunt
#: line and is meant to be: it separates "missed a fortnight" from "missed a
#: game", not degrees of misfortune.
BROKE_DOWN = 0.5

#: Misses listed by name in the write-up, worst first.
WORST = 8


@dataclass(frozen=True)
class Row:
    """One side of one trade: what was predicted, and what it did.

    Two predictions and two horizons, so the 2x2 comes out of one run and one
    evaluation of each deal rather than four runs that could drift apart.
    """

    season: int
    day: int
    #: The trade itself, so the two sides of one deal can be paired up: the
    #: prediction is antisymmetric between them, so they are not two
    #: independent observations and the write-up must not count them as two.
    event: str
    team: str
    counterparty: str
    players_in: tuple[str, ...]
    players_out: tuple[str, ...]
    #: Revision R1: this roster's ordinary week with the deal and without it.
    roster: float
    #: The first cut: each man valued on his own in a league-average team.
    independent: float
    #: Categories a week delivered, over the rest of the season and over the
    #: thirty days after the deal. The short one is None when no matchup
    #: period begins inside the window.
    delivered_season: float
    delivered_short: float | None
    #: The same two, graded with the settlement the hindsight engine used
    #: before revision R2: a spot the move opened worth the flat median
    #: pickup. Identical to the pair above on every even-count side, because
    #: an even deal opens no spot.
    delivered_season_old: float
    delivered_short_old: float | None
    decision: float
    #: Periods each hindsight grade covered.
    periods: int
    short_periods: int
    uneven: bool
    thin: tuple[str, ...]
    broke_down: tuple[str, ...]
    gave_and_broke: tuple[str, ...]

    @property
    def label(self) -> str:
        return (
            f"{self.season} day {self.day}, {self.team}: "
            f"in {', '.join(self.players_in) or 'nobody'}; "
            f"out {', '.join(self.players_out) or 'nobody'}"
        )

    def why(self) -> str:
        """One sentence on why the prediction missed, from the rows."""
        if self.broke_down:
            return (
                f"{', '.join(self.broke_down)} played under half the games scheduled after "
                "the deal: an injury nobody had on the day, which is luck and not a bug"
            )
        if self.gave_and_broke:
            return (
                f"{', '.join(self.gave_and_broke)} left and then broke down, so the side "
                "that gave him up is credited by hindsight for a risk it did not take"
            )
        if self.thin:
            return (
                f"the line for {', '.join(self.thin)} rested on fewer than a dozen games "
                "of his own, which the report flags and this run does not discount"
            )
        if self.uneven:
            return (
                "an uneven deal: the evaluator fills the place it opens with the best man "
                "on the wire and the hindsight grade charges a flat replacement level, so "
                "part of this gap is the two settlements rather than the forecast"
            )
        return (
            "the projections held up; the gap is what the men did afterwards -- the season "
            "term is this roster's week with the deal and without it, and the grade counts "
            "what the lineup really posted"
        )


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Ordinary correlation, or 0.0 when a side has no spread to correlate."""
    if len(xs) < 3:
        return 0.0
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    top = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    left = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    right = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    return top / (left * right) if left and right else 0.0


def ranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties averaged, the way a Spearman coefficient needs them."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    out = [0.0] * len(values)
    position = 0
    while position < len(order):
        stop = position
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[position]]:
            stop += 1
        shared = (position + stop) / 2 + 1
        for index in order[position : stop + 1]:
            out[index] = shared
        position = stop + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    return pearson(ranks(xs), ranks(ys))


def coin_interval(n: int) -> tuple[int, int]:
    """The middle 95% of what a fair coin gives in `n` tosses, exactly.

    Printed beside every hit rate. Thirty-one of fifty-five looks like a
    finding and is not: a coin does at least that well one time in four, and
    without the yardstick beside it a reader has no way to know.
    """
    if n <= 0:
        return 0, 0
    total = 2**n
    cumulative = 0.0
    low = 0
    for heads in range(n + 1):
        cumulative += math.comb(n, heads) / total
        if cumulative > 0.025:
            low = heads
            break
    return low, n - low


def coin_note(n: int) -> str:
    low, high = coin_interval(n)
    share = f"{low / n:.0%}-{high / n:.0%}" if n else "--"
    return f"a coin gives {low}-{high} of {n} ({share}) nineteen times in twenty"


@dataclass(frozen=True)
class Scored:
    """One side of one deal, read through one cell of the 2x2."""

    row: Row
    predicted: float
    delivered: float

    @property
    def error(self) -> float:
        return self.predicted - self.delivered

    @property
    def agrees(self) -> bool:
        """Both the same side of zero, a tie at zero counting as agreement."""
        if self.predicted == 0.0 or self.delivered == 0.0:
            return self.predicted == self.delivered
        return (self.predicted > 0) == (self.delivered > 0)


@dataclass(frozen=True)
class Deal:
    """Both sides of one deal, so the question is asked once per trade.

    The prediction is very nearly antisymmetric between the two sides, so they
    are not two observations. The independent question is which side of the
    deal the evaluator picked.
    """

    sides: tuple[Scored, Scored]

    @property
    def edge(self) -> float:
        """How much better the evaluator thought the first side's half was."""
        return self.sides[0].predicted - self.sides[1].predicted

    @property
    def got(self) -> float:
        """How much better the first side's half turned out to be."""
        return self.sides[0].delivered - self.sides[1].delivered

    @property
    def right(self) -> bool:
        """Did the evaluator name the side of the deal that did better?"""
        if self.edge == 0.0 or self.got == 0.0:
            return False
        return (self.edge > 0) == (self.got > 0)


@dataclass(frozen=True)
class Cell:
    """One of the four {headline} x {horizon} pairs the run reports.

    The four were named in the declaration before the run, and the primary one
    was named with them, so no cell can be promoted after the fact.
    """

    headline: str
    horizon: str
    predict: Callable[[Row], float]
    deliver: Callable[[Row], float | None]
    primary: bool = False

    @property
    def name(self) -> str:
        return f"{self.headline} x {self.horizon}"

    def scored(self, rows: Sequence[Row]) -> list[Scored]:
        out = []
        for row in rows:
            delivered = self.deliver(row)
            if delivered is None:
                continue
            out.append(Scored(row=row, predicted=self.predict(row), delivered=delivered))
        return out

    def deals(self, rows: Sequence[Row]) -> list[Deal]:
        return pair_up(self.scored(rows))


def pair_up(scored: Sequence[Scored]) -> list[Deal]:
    """Sides paired into deals; a side whose partner was not scored is dropped."""
    by_event: dict[str, list[Scored]] = {}
    for side in scored:
        by_event.setdefault(side.row.event, []).append(side)
    return [Deal((pair[0], pair[1])) for pair in by_event.values() if len(pair) == 2]


#: The 2x2 and the old-yardstick pair, and the primary cell, exactly as
#: declared in `docs/trades.md` section 7b.
CELLS: tuple[Cell, ...] = (
    Cell(
        "R2 (the roster)",
        "next 30 days",
        lambda row: row.roster,
        lambda row: row.delivered_short,
        primary=True,
    ),
    Cell(
        "R2 (the roster)",
        "rest of season",
        lambda row: row.roster,
        lambda row: row.delivered_season,
    ),
    Cell(
        "per man (the old headline)",
        "next 30 days",
        lambda row: row.independent,
        lambda row: row.delivered_short,
    ),
    Cell(
        "per man (the old headline)",
        "rest of season",
        lambda row: row.independent,
        lambda row: row.delivered_season,
    ),
    Cell(
        "R2 (the roster)",
        "next 30 days, old yardstick",
        lambda row: row.roster,
        lambda row: row.delivered_short_old,
    ),
    Cell(
        "R2 (the roster)",
        "rest of season, old yardstick",
        lambda row: row.roster,
        lambda row: row.delivered_season_old,
    ),
)

#: The cells whose yardstick is the one the hindsight engine used before R2,
#: named so the write-up can separate them without matching on strings.
OLD_YARDSTICK = tuple(cell for cell in CELLS if cell.horizon.endswith("old yardstick"))

#: What the run of 2026-09-21 reported, so this one can be read against it
#: without a reader having to open the document: the uneven sides' mean error
#: under R1 on each horizon, and the per-man number's on the rest of season.
R1_UNEVEN = {"next 30 days": 0.389, "rest of season": 0.355}
R1_UNEVEN_PER_MAN = 0.265

#: R1's even-count row on the primary cell, the one that must not have moved:
#: sides, sign agreement, Spearman, mean error, mean absolute error.
R1_EVEN = (85, 0.54, 0.10, -0.020, 0.279)


def primary() -> Cell:
    return next(cell for cell in CELLS if cell.primary)


def _teams(session: Session, league_season: LeagueSeason) -> dict[int, Team]:
    return {
        int(team.id): team
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }


def _played(session: Session, season: int, player_id: int, first: int, last: int) -> int:
    count = session.scalar(
        select(func.count()).where(
            PlayerGameStat.player_id == player_id,
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period.between(first, last),
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes > 0,
        )
    )
    return int(count or 0)


def _scheduled(session: Session, season: int, pro_team_id: int, first: int, last: int) -> int:
    count = session.scalar(
        select(func.count()).where(
            ProTeamGame.season == season,
            ProTeamGame.pro_team_id == pro_team_id,
            ProTeamGame.scoring_period.between(first, last),
        )
    )
    return int(count or 0)


def _availability(
    session: Session,
    league_season: LeagueSeason,
    cards: Sequence[PlayerCard],
    first: int,
    last: int,
) -> tuple[str, ...]:
    """Who played under half the games his NBA team had over the window."""
    season = int(league_season.season)
    ids = [card.player_id for card in cards]
    if not ids or first > last:
        return ()
    teams = {
        player.player_id: player.pro_team_id
        for player in build_players(session, league_season, ids, (first,))
    }
    out: list[str] = []
    for card in cards:
        scheduled = _scheduled(session, season, teams.get(card.player_id, 0), first, last)
        if not scheduled:
            continue
        if _played(session, season, card.player_id, first, last) / scheduled < BROKE_DOWN:
            out.append(card.name)
    return tuple(out)


def _window(grade: MoveGrade, book: SeasonBook) -> tuple[int, int]:
    days = [
        (
            int(book.periods[period].first_scoring_period or 0),
            int(book.periods[period].final_scoring_period or 0),
        )
        for period in grade.periods
    ]
    return min(first for first, _ in days), max(last for _, last in days)


def _mirror(session: Session, season: int, trade: Trade, other_row_id: int) -> bool:
    """Does the other side's own reconstruction name the same players, reversed?

    A three-way, or a day where a second deal absorbed a leg, can leave the two
    teams disagreeing about what changed hands. The evaluator is given one
    story, so a pair that does not mirror is skipped and counted rather than
    fed a half deal.
    """
    for theirs in reconstruct_trades(session, season, other_row_id):
        if theirs.day != trade.day or theirs.counterparty_ids != (trade.team_id,):
            continue
        return {p.player_id for p in theirs.players_in} == {
            p.player_id for p in trade.players_out
        } and {p.player_id for p in theirs.players_out} == {p.player_id for p in trade.players_in}
    return False


@dataclass(frozen=True)
class PlayerRow:
    """One man in one deal: what he was forecast to be worth, and what he was.

    The diagnostic that separates two different failures. If these correlate
    and the deals do not, the projections carry signal and differencing two
    similar players destroys it. If these do not correlate either, the
    projections are the problem and no arithmetic on top of them will help.
    """

    season: int
    event: str
    name: str
    #: Categories a week the evaluator said his place was worth on the day.
    predicted: float
    #: Categories a week his real box scores were worth over the same thirty
    #: days, through the same league-standard lens.
    delivered: float
    games: int


@dataclass
class Skipped:
    part_missing: int = 0
    multi_team: int = 0
    no_mirror: int = 0
    no_grade: int = 0
    no_short_grade: int = 0
    refused: list[str] | None = None

    def refusal(self, message: str) -> None:
        if self.refused is None:
            self.refused = []
        self.refused.append(message)


def _delivered_weekly(
    session: Session, season: int, player_id: int, first: int, last: int
) -> tuple[CategoryLine, int]:
    """What the man really posted over the window, per week, and his games."""
    columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    found = session.execute(
        select(*columns).where(
            PlayerGameStat.player_id == player_id,
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period.between(first, last),
            PlayerGameStat.played.is_(True),
        )
    ).all()
    line = CategoryLine(
        {key: sum(float(row[index] or 0.0) for row in found) for index, key in enumerate(COUNTS)},
        len(found),
    )
    weeks = max(1.0, last - first + 1) / DAYS_A_WEEK
    return line.scaled(1.0 / weeks), len(found)


def rows_for_season(
    session: Session,
    season: int,
    skipped: Skipped,
    players: list[PlayerRow],
    *,
    review_days: int,
    league_id: int | None = None,
) -> list[Row]:
    """Every scoreable side of every reconstructable trade in one season.

    `league_id` is `leagues.id`, and names whose season it is; the intake
    passes it (`app.intake.measure`) because a database now holds more than
    one league. Without it, the first stored season of that year, which is
    what this script has always taken.
    """
    where = select(LeagueSeason).where(LeagueSeason.season == season)
    if league_id is not None:
        where = where.where(LeagueSeason.league_id == league_id)
    league_season = session.scalar(where)
    if league_season is None:
        return []
    teams = _teams(session, league_season)
    if not teams:
        return []
    book = SeasonBook.load(session, season)
    distributions: Sequence[CategoryDistribution] = category_distributions(session, league_season)
    grades: dict[int, list[TradeGrade]] = {
        row_id: trade_grades(session, season, row_id, book=book) for row_id in teams
    }
    # The replacement level `trade_grades` itself charges an uneven deal, read
    # once so the short-window grade is the same move scored the same way.
    replacement = median_of(pickup_values(book))

    seen: set[tuple[int, frozenset[int], frozenset[int]]] = set()
    out: list[Row] = []
    for row_id, team in sorted(teams.items()):
        for graded in grades[row_id]:
            trade = graded.trade
            if graded.part_missing:
                skipped.part_missing += 1
                continue
            if len(trade.counterparties) != 1:
                skipped.multi_team += 1
                continue
            other_row_id = trade.counterparty_ids[0]
            key = (
                trade.day,
                frozenset({row_id, other_row_id}),
                frozenset(p.player_id for p in (*trade.players_in, *trade.players_out)),
            )
            if key in seen:
                continue
            if not _mirror(session, season, trade, other_row_id):
                skipped.no_mirror += 1
                continue
            seen.add(key)
            other = teams[other_row_id]
            out.extend(
                _score(
                    session,
                    league_season,
                    book,
                    trade,
                    team,
                    other,
                    grades,
                    distributions,
                    skipped,
                    players,
                    replacement=replacement,
                    review_days=review_days,
                )
            )
    return out


def _grade_for(grades: Sequence[TradeGrade], day: int) -> MoveGrade | None:
    for graded in grades:
        if graded.trade.day == day and graded.regular is not None:
            return graded.regular
    return None


def _score(
    session: Session,
    league_season: LeagueSeason,
    book: SeasonBook,
    trade: Trade,
    team: Team,
    other: Team,
    grades: dict[int, list[TradeGrade]],
    distributions: Sequence[CategoryDistribution],
    skipped: Skipped,
    players: list[PlayerRow],
    *,
    replacement: float,
    review_days: int,
) -> list[Row]:
    """Both sides of one event, evaluated once and graded from each end."""
    season = int(league_season.season)
    mine = tuple(p.player_id for p in trade.players_out)
    theirs = tuple(p.player_id for p in trade.players_in)
    try:
        report = evaluate_trade(
            session,
            league_season,
            trade.day,
            TeamOffer(team_id=int(team.espn_team_id), gives=mine),
            TeamOffer(team_id=int(other.espn_team_id), gives=theirs),
            review_days=review_days,
            distributions=distributions,
        )
    except ValueError as error:
        skipped.refusal(f"{season} day {trade.day} {team.name}: {error}")
        return []

    event = f"{season}:{trade.day}:{min(team.id, other.id)}-{max(team.id, other.id)}"
    out: list[Row] = []
    both = ((team, other), (other, team))
    for owner, against in both:
        row_id = int(owner.id)
        grade = _grade_for(grades[row_id], trade.day)
        if grade is None:
            skipped.no_grade += 1
            continue

        def graded(
            *, within: int | None, opened_place: float, grade: MoveGrade = grade
        ) -> MoveGrade | None:
            return grade_move(
                book,
                grade.team_id,
                grade.day,
                grade.players_in,
                grade.players_out,
                replacement=replacement,
                within=within,
                opened_place=opened_place,
            )

        short_day = trade.day + SHORT_WINDOW
        short = graded(within=short_day, opened_place=OPENED_PLACE)
        # The same move through the settlement the grade used before R2: a
        # spot the deal opened worth the flat median pickup. `grade` itself is
        # already the R2 rest-of-season grade, so only the other three are run.
        short_old = graded(within=short_day, opened_place=replacement)
        season_old = graded(within=None, opened_place=replacement)
        if short is None:
            skipped.no_short_grade += 1
        if season_old is None:  # pragma: no cover - the R2 grade exists, so this does
            continue
        side: SideReport = report.side(int(owner.espn_team_id))
        first, last = _window(grade, book)
        incoming = side.receives
        outgoing = (*side.gives, *side.drops)
        out.append(
            Row(
                season=season,
                day=trade.day,
                event=event,
                team=owner.name,
                counterparty=against.name,
                players_in=tuple(card.name for card in incoming),
                players_out=tuple(card.name for card in outgoing),
                roster=side.judgement.delta_season_per_week,
                independent=side.season_independent,
                delivered_season=grade.result,
                delivered_short=None if short is None else short.result,
                delivered_season_old=season_old.result,
                delivered_short_old=None if short_old is None else short_old.result,
                decision=grade.decision,
                periods=len(grade.periods),
                short_periods=0 if short is None else len(short.periods),
                uneven=len(incoming) != len(outgoing),
                thin=tuple(card.name for card in (*incoming, *outgoing) if card.thin),
                broke_down=_availability(session, league_season, incoming, first, last),
                gave_and_broke=_availability(session, league_season, side.gives, first, last),
            )
        )
    players.extend(_players(session, league_season, report, trade.day, event, distributions))
    return out


def _players(
    session: Session,
    league_season: LeagueSeason,
    report: TradeReport,
    day: int,
    event: str,
    distributions: Sequence[CategoryDistribution],
) -> list[PlayerRow]:
    """Every man in the deal: his forecast weekly value against his real one.

    The same lens on both sides -- `standard_lens` on the morning of the deal,
    which is the lens `PlayerCard.value` was read through -- so the only
    difference between the two numbers is the forecast. Each man is counted
    once per deal, however many sides of the report he appears on.
    """
    season = int(league_season.season)
    lens = standard_lens(session, league_season, day, distributions)
    seen: dict[int, PlayerRow] = {}
    for side in report.sides:
        for card in (*side.receives, *side.gives, *side.drops):
            if card.player_id in seen:
                continue
            line, games = _delivered_weekly(
                session, season, card.player_id, day + 1, day + SHORT_WINDOW
            )
            seen[card.player_id] = PlayerRow(
                season=season,
                event=event,
                name=card.name,
                predicted=card.value,
                delivered=lens.value(line),
                games=games,
            )
    return list(seen.values())


def _name(session: Session, row_id: int) -> str:
    found = session.get(Team, row_id)
    return found.name if found is not None else f"team {row_id}"


def _hit_rate(deals: Sequence[Deal]) -> str:
    if not deals:
        return "no deals"
    right = sum(1 for deal in deals if deal.right)
    return f"{right} of {len(deals)} ({right / len(deals):.0%})"


def _cell_row(cell: Cell, rows: Sequence[Row]) -> str:
    scored = cell.scored(rows)
    deals = pair_up(scored)
    predicted = [side.predicted for side in scored]
    delivered = [side.delivered for side in scored]
    errors = [side.error for side in scored]
    name = f"**{cell.headline}**" if cell.primary else cell.headline
    horizon = f"**{cell.horizon}**" if cell.primary else cell.horizon
    return (
        f"| {name} | {horizon} | {len(deals)} | {_hit_rate(deals)} | {len(scored)} | "
        f"{spearman(predicted, delivered):+.2f} | {statistics.fmean(errors):+.3f} | "
        f"{statistics.fmean([abs(e) for e in errors]):.3f} |"
    )


def _split(label: str, cell: Cell, rows: Sequence[Row]) -> str:
    scored = cell.scored(rows)
    if not scored:
        return f"| {label} | 0 | -- | -- | -- | -- |"
    predicted = [side.predicted for side in scored]
    delivered = [side.delivered for side in scored]
    errors = [side.error for side in scored]
    return (
        f"| {label} | {len(scored)} | "
        f"{sum(1 for side in scored if side.agrees) / len(scored):.0%} | "
        f"{spearman(predicted, delivered):+.2f} | {statistics.fmean(errors):+.3f} | "
        f"{statistics.fmean([abs(e) for e in errors]):.3f} |"
    )


def _confidence(cell: Cell, rows: Sequence[Row]) -> list[str]:
    """Deals ranked by how sure the evaluator was, split before the outcome.

    The split is on |predicted edge| alone -- a quantity the evaluator has on
    the morning -- so whichever way it falls it is a claim the tool could act
    on, which an injury split made after the fact is not.
    """
    deals = sorted(cell.deals(rows), key=lambda deal: -abs(deal.edge))
    if len(deals) < CONFIDENT_SHARE:
        return ["Too few deals to split by confidence."]
    cut = len(deals) // CONFIDENT_SHARE
    top, rest = deals[:cut], deals[cut:]
    edge = abs(top[-1].edge) if top else 0.0
    return [
        "| deals ranked by |predicted edge| | n | picked the better side | a coin |",
        "|---|---|---|---|",
        f"| top third (edge at or above {edge:.2f} a week) | {len(top)} | "
        f"{_hit_rate(top)} | {coin_note(len(top))} |",
        f"| the other two thirds | {len(rest)} | {_hit_rate(rest)} | {coin_note(len(rest))} |",
    ]


def _mean_error(cell: Cell, rows: Sequence[Row]) -> tuple[float, int]:
    scored = cell.scored(rows)
    if not scored:
        return 0.0, 0
    return statistics.fmean([side.error for side in scored]), len(scored)


def _settlement(rows: Sequence[Row], cell: Cell) -> list[str]:
    """The number this revision exists for, and the check that it moved nothing else.

    The uneven sides are the ones an opened place is settled on, read under
    both yardsticks so that re-pricing the forecast and re-pricing the grade
    can be told apart; and the even sides, where no place opens, are printed
    beside what they were on 2026-09-21 because they must not have moved at
    all.
    """
    uneven = [row for row in rows if row.uneven]
    even = [row for row in rows if not row.uneven]
    out = [
        "### The settlement of an opened place, isolated",
        "",
        "| prediction | yardstick | horizon | uneven sides | mean error |",
        "|---|---|---|---|---|",
    ]
    for each in CELLS:
        if each.headline != cell.headline:
            continue
        error, n = _mean_error(each, uneven)
        old = each in OLD_YARDSTICK
        horizon = each.horizon.removesuffix(", old yardstick")
        out.append(
            f"| R2 | {'the old flat level' if old else 'R2'} | {horizon} | {n} | {error:+.3f} |"
        )
    for horizon, was in R1_UNEVEN.items():
        out.append(f"| R1, on 2026-09-21 | the old flat level | {horizon} | 25 | +{was:.3f} |")
    out.extend(
        [
            "",
            f"The per-man headline gave +{R1_UNEVEN_PER_MAN:.3f} over 25 sides against the rest "
            "of the season on 2026-09-21, which is the number R1 was meant to fix and did not.",
            "",
            "**The check: the even-count sides must not have moved.** No place opens on them, "
            "so neither the forecast nor the grade can have been re-priced, and the run of "
            f"2026-09-21 reported {R1_EVEN[0]} sides, sign agreement {R1_EVEN[1]:.0%}, Spearman "
            f"{R1_EVEN[2]:+.2f}, mean error {R1_EVEN[3]:+.3f}, mean absolute error "
            f"{R1_EVEN[4]:.3f}. This run:",
            "",
            "| | sides | sign agreement | Spearman | mean error | mean abs error |",
            "|---|---|---|---|---|---|",
            _split("even counts, R2 yardstick", cell, even),
            _split("even counts, old yardstick", OLD_YARDSTICK[0], even),
        ]
    )
    return out


def _player_diagnostic(players: Sequence[PlayerRow]) -> list[str]:
    """Is it the projections, or is it differencing two of them?"""
    if len(players) < 3:
        return ["Too few players in the scored deals to correlate."]
    predicted = [player.predicted for player in players]
    delivered = [player.delivered for player in players]
    errors = [p - d for p, d in zip(predicted, delivered, strict=True)]
    return [
        f"For every man in a scored deal ({len(players)} of them, counted once per deal), "
        f"what the evaluator said his roster place was worth on the morning against what "
        f"his real box scores were worth per week over the same thirty days, through the "
        f"same league-standard lens: Spearman **{spearman(predicted, delivered):+.2f}**, "
        f"Pearson {pearson(predicted, delivered):+.2f}, mean error "
        f"{statistics.fmean(errors):+.3f} categories a week, mean absolute error "
        f"{statistics.fmean([abs(e) for e in errors]):.3f}. Predicted spread "
        f"{statistics.pstdev(predicted):.3f}, delivered spread "
        f"{statistics.pstdev(delivered):.3f}.",
    ]


def summarise(
    rows: Sequence[Row],
    players: Sequence[PlayerRow],
    skipped: Skipped,
    *,
    seconds: float,
    review_days: int,
) -> str:
    """The write-up, in markdown, with the primary cell in the first sentence."""
    out: list[str] = []
    add = out.append
    if not rows:
        return "No trade in the stored seasons could be both evaluated forward and graded."
    cell = primary()
    scored = cell.scored(rows)
    deals = cell.deals(rows)
    right = sum(1 for deal in deals if deal.right)
    predicted = [side.predicted for side in scored]
    delivered = [side.delivered for side in scored]
    errors = [side.error for side in scored]

    add(
        f"**The primary cell, named before the run: {cell.name}, deal level. The evaluator "
        f"picked the side that did better in {right} of {len(deals)} deals "
        f"({(right / len(deals) if deals else 0.0):.0%}), where {coin_note(len(deals))}.** "
        f"Over the {len(scored)} sides the rank correlation is "
        f"{spearman(predicted, delivered):+.2f} and the mean error "
        f"{statistics.fmean(errors):+.3f} categories a week. "
        f"{len(rows)} sides across {len({row.season for row in rows})} seasons is the whole "
        f"sample and every figure below rests on it. Generated by "
        f"`scripts/trade_calibration.py` in {seconds:.0f}s, review_days={review_days}, "
        f"short window {SHORT_WINDOW} days."
    )
    add("")
    add(
        f"For scale: the predictions have a spread of {statistics.pstdev(predicted):.3f} "
        f"categories a week and what was delivered a spread of "
        f"{statistics.pstdev(delivered):.3f}, so the mean absolute error of "
        f"{statistics.fmean([abs(e) for e in errors]):.3f} is about the size of the thing "
        f"being predicted."
    )
    add("")
    add("### The 2x2 and the old yardstick beside it, every cell from one run")
    add("")
    add(
        "| headline | horizon | deals | picked the better side | sides | Spearman | "
        "mean error | mean abs error |"
    )
    add("|---|---|---|---|---|---|---|---|")
    for each in CELLS:
        add(_cell_row(each, rows))
    add("")
    add(
        f"The two sides of a deal are not two observations -- the prediction is very nearly "
        f"antisymmetric between them -- so the deal-level column is the independent question "
        f"and the side-level columns describe the same data twice. For {len(deals)} deals, "
        f"{coin_note(len(deals))}."
    )
    add("")
    add(f"### Splits, on the primary cell ({cell.name})")
    add("")
    add("| | sides | sign agreement | Spearman | mean error | mean abs error |")
    add("|---|---|---|---|---|---|")
    add(_split("all sides", cell, rows))
    add(_split("even counts", cell, [row for row in rows if not row.uneven]))
    add(_split("uneven counts", cell, [row for row in rows if row.uneven]))
    add(
        _split(
            "nobody broke down after",
            cell,
            [row for row in rows if not row.broke_down and not row.gave_and_broke],
        )
    )
    add("")
    out.extend(_settlement(rows, cell))
    add("")
    add("### How sure it was, decided on the prediction")
    add("")
    out.extend(_confidence(cell, rows))
    add("")
    add("### The players themselves")
    add("")
    out.extend(_player_diagnostic(players))
    add("")
    add(
        f"Against the decision lens (`MoveGrade.decision`, the same day's data through the "
        f"hindsight engine's own arithmetic): Spearman "
        f"{spearman([row.roster for row in rows], [row.decision for row in rows]):+.2f} for "
        f"the roster headline and "
        f"{spearman([row.independent for row in rows], [row.decision for row in rows]):+.2f} "
        f"for the per-man one."
    )
    add("")
    add(f"### By season, on the primary cell ({cell.name})")
    add("")
    add("| season | sides | sign agreement | Spearman | mean predicted | mean delivered |")
    add("|---|---|---|---|---|---|")
    for season in sorted({side.row.season for side in scored}):
        here = [side for side in scored if side.row.season == season]
        mine = [side.predicted for side in here]
        theirs = [side.delivered for side in here]
        add(
            f"| {season} | {len(here)} | "
            f"{sum(1 for side in here if side.agrees) / len(here):.0%} | "
            f"{spearman(mine, theirs):+.2f} | {statistics.fmean(mine):+.3f} | "
            f"{statistics.fmean(theirs):+.3f} |"
        )
    add("")
    add("### The worst misses, and why")
    add("")
    for side in sorted(scored, key=lambda s: -abs(s.error))[:WORST]:
        add(
            f"- **{side.row.label}** -- predicted {side.predicted:+.3f} a week, delivered "
            f"{side.delivered:+.3f} over {side.row.short_periods} period(s). "
            f"{side.row.why()}."
        )
    add("")
    add("### What could not be scored")
    add("")
    add(f"- {skipped.part_missing} sides where only one half of the deal left a roster trace.")
    add(f"- {skipped.multi_team} sides of trades with more than one counterparty.")
    add(f"- {skipped.no_mirror} events where the two teams' reconstructions did not mirror.")
    add(f"- {skipped.no_grade} sides with no gradeable stretch after the move.")
    add(f"- {skipped.no_short_grade} sides with no matchup period inside the thirty days.")
    for message in skipped.refused or []:
        add(f"- refused by the evaluator: {message}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="*",
        default=list(DEFAULT_SEASONS),
        help="Seasons to score (default: every stored one)",
    )
    parser.add_argument(
        "--review-days",
        type=int,
        default=1,
        help="Days between the judgement and the deal landing (default 1, the measured one)",
    )
    parser.add_argument("--out", type=Path, default=None, help="Write the markdown here too")
    args = parser.parse_args()

    started = time.monotonic()
    engine = make_engine(get_settings().database_url)
    try:
        factory = make_session_factory(engine)
        skipped = Skipped()
        rows: list[Row] = []
        players: list[PlayerRow] = []
        with factory() as session:
            for season in args.seasons:
                found = rows_for_season(
                    session, season, skipped, players, review_days=args.review_days
                )
                print(f"{season}: {len(found)} sides scored", file=sys.stderr)
                rows.extend(found)
        text = summarise(
            rows,
            players,
            skipped,
            seconds=time.monotonic() - started,
            review_days=args.review_days,
        )
    finally:
        engine.dispose()
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
