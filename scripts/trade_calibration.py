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

`Judgement.delta_season_per_week` -- the evaluator's change in what the
roster's places yield in an ordinary week -- against `MoveGrade.result`, the
hindsight engine's categories a week delivered over the periods the team held
what came in. Both are categories a week and both are a with-and-without, so
they are the same quantity measured two ways. `MoveGrade.decision`, the
knowable-at-the-time grade, is reported beside it: the evaluator and the
decision lens see the same day's data through different arithmetic, so a big
gap between those two is a bug in one of them, while a gap to `result` is
mostly the future.

WHAT THIS CANNOT MEASURE

A played season has no listener data: no injury statuses, no ownership, no
recorded wire. So the evaluator runs with every player treated as available
and with the reconstructed wire (`app.pickups.state.historical_free_agents`,
whoever played that day and was in nobody's lineup). Both flatter it -- a man
who was day-to-day when the deal was made is projected as healthy on both
sides -- and neither can be fixed from stored rows. The sample is also small,
and the write-up says so in its first sentence.

The two engines also settle an uneven trade differently: this one forces a
drop and charges what that place was worth, while `grade_move` charges a flat
replacement level per spot. Both are honest; they are not identical, and the
uneven rows are reported separately for that reason.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LeagueSeason, PlayerGameStat, ProTeamGame, Team
from app.db.session import make_engine, make_session_factory
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.state import build_players
from app.scoring.moves import MoveGrade
from app.scoring.season import SeasonBook
from app.scoring.trade_grades import TradeGrade, trade_grades
from app.scoring.trades import Trade, reconstruct_trades
from app.trades import PlayerCard, SideReport, TeamOffer, evaluate_trade

#: Seasons with any reconstructable trade at all. 2020's two sides are both
#: "part missing" and 2022 had no trades, so neither can be scored; they are
#: listed anyway so the write-up can say why.
DEFAULT_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026)

#: A player who played fewer than this share of his NBA team's scheduled
#: games over the graded window broke down after the deal. Half is a blunt
#: line and is meant to be: it separates "missed a fortnight" from "missed a
#: game", not degrees of misfortune.
BROKE_DOWN = 0.5

#: Misses listed by name in the write-up, worst first.
WORST = 8


@dataclass(frozen=True)
class Row:
    """One side of one trade: what was predicted, and what it did."""

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
    predicted: float
    delivered: float
    decision: float
    #: Periods the hindsight grade covered.
    periods: int
    uneven: bool
    thin: tuple[str, ...]
    broke_down: tuple[str, ...]
    gave_and_broke: tuple[str, ...]

    @property
    def error(self) -> float:
        return self.predicted - self.delivered

    @property
    def agrees(self) -> bool:
        """Both the same side of zero, a tie at zero counting as agreement."""
        if self.predicted == 0.0 or self.delivered == 0.0:
            return self.predicted == self.delivered
        return (self.predicted > 0) == (self.delivered > 0)

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
                "an uneven deal: the evaluator charges the place it costs at what that "
                "place was worth and the hindsight grade charges a flat replacement, so "
                "part of this gap is the two settlements rather than the forecast"
            )
        return (
            "the projections held up; the gap is fit -- the season term values a man "
            "against the league's average team, and the grade counts what he did in this "
            "one's lineup"
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


@dataclass
class Skipped:
    part_missing: int = 0
    multi_team: int = 0
    no_mirror: int = 0
    no_grade: int = 0
    refused: list[str] | None = None

    def refusal(self, message: str) -> None:
        if self.refused is None:
            self.refused = []
        self.refused.append(message)


def rows_for_season(
    session: Session,
    season: int,
    skipped: Skipped,
    *,
    review_days: int,
) -> list[Row]:
    """Every scoreable side of every reconstructable trade in one season."""
    league_season = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
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
    *,
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

    out: list[Row] = []
    both = ((team, other), (other, team))
    for owner, against in both:
        row_id = int(owner.id)
        grade = _grade_for(grades[row_id], trade.day)
        if grade is None:
            skipped.no_grade += 1
            continue
        side: SideReport = report.side(int(owner.espn_team_id))
        first, last = _window(grade, book)
        incoming = side.receives
        outgoing = (*side.gives, *side.drops)
        out.append(
            Row(
                season=season,
                day=trade.day,
                event=f"{season}:{trade.day}:{min(team.id, other.id)}-{max(team.id, other.id)}",
                team=owner.name,
                counterparty=against.name,
                players_in=tuple(card.name for card in incoming),
                players_out=tuple(card.name for card in outgoing),
                predicted=side.judgement.delta_season_per_week,
                delivered=grade.result,
                decision=grade.decision,
                periods=len(grade.periods),
                uneven=len(incoming) != len(outgoing),
                thin=tuple(card.name for card in (*incoming, *outgoing) if card.thin),
                broke_down=_availability(session, league_season, incoming, first, last),
                gave_and_broke=_availability(session, league_season, side.gives, first, last),
            )
        )
    return out


def _name(session: Session, row_id: int) -> str:
    found = session.get(Team, row_id)
    return found.name if found is not None else f"team {row_id}"


def summarise(rows: Sequence[Row], skipped: Skipped, *, seconds: float, review_days: int) -> str:
    """The write-up, in markdown, with the sample size in the first sentence."""
    out: list[str] = []
    add = out.append
    n = len(rows)
    if not n:
        return "No trade in the stored seasons could be both evaluated forward and graded."
    predicted = [row.predicted for row in rows]
    delivered = [row.delivered for row in rows]
    decision = [row.decision for row in rows]
    agree = sum(1 for row in rows if row.agrees) / n
    errors = [row.error for row in rows]

    rho = spearman(predicted, delivered)
    paired = events(rows)
    right = sum(1 for event in paired if event.right)
    add(
        f"**{n} trade sides -- {len(paired)} deals -- across "
        f"{len({row.season for row in rows})} seasons is the whole sample, and every figure "
        f"below rests on it.** Sign agreement with the hindsight grade is **{agree:.0%}** over "
        f"the sides; the rank correlation is **{rho:+.2f}** and the ordinary correlation "
        f"{pearson(predicted, delivered):+.2f}. Mean error (predicted minus delivered) is "
        f"{statistics.fmean(errors):+.3f} categories a week, mean absolute error "
        f"{statistics.fmean([abs(e) for e in errors]):.3f}. Generated by "
        f"`scripts/trade_calibration.py` in {seconds:.0f}s, review_days={review_days}."
    )
    add("")
    add(
        f"The two sides of a deal are not two observations: the prediction is very nearly "
        f"antisymmetric between them, so the independent question is **which side of each "
        f"deal the evaluator picked**. It picked the side that did better in "
        f"**{right} of {len(paired)}** deals "
        f"({(right / len(paired) if paired else 0.0):.0%}), against 50% for a coin."
    )
    add("")
    add(
        f"For scale: the predictions have a spread of "
        f"{statistics.pstdev(predicted):.3f} categories a week and what was delivered a "
        f"spread of {statistics.pstdev(delivered):.3f}, so the mean absolute error above is "
        f"about the size of the thing being predicted."
    )
    add("")
    add("| | n | sign agreement | Spearman | Pearson | mean error | mean abs error |")
    add("|---|---|---|---|---|---|---|")
    add(_line("all sides", rows))
    even = [row for row in rows if not row.uneven]
    uneven = [row for row in rows if row.uneven]
    if even:
        add(_line("even counts", even))
    if uneven:
        add(_line("uneven counts", uneven))
    healthy = [row for row in rows if not row.broke_down and not row.gave_and_broke]
    if healthy:
        add(_line("nobody broke down after", healthy))
    add("")
    add(
        f"Against the decision lens (`MoveGrade.decision`, the same day's data through the "
        f"hindsight engine's own arithmetic): Spearman "
        f"{spearman(predicted, decision):+.2f}, mean error "
        f"{statistics.fmean([p - d for p, d in zip(predicted, decision, strict=True)]):+.3f}."
    )
    add("")
    add("### By season")
    add("")
    add("| season | n | sign agreement | Spearman | mean predicted | mean delivered |")
    add("|---|---|---|---|---|---|")
    for season in sorted({row.season for row in rows}):
        here = [row for row in rows if row.season == season]
        mine = [row.predicted for row in here]
        theirs = [row.delivered for row in here]
        add(
            f"| {season} | {len(here)} | "
            f"{sum(1 for r in here if r.agrees) / len(here):.0%} | "
            f"{spearman(mine, theirs):+.2f} | {statistics.fmean(mine):+.3f} | "
            f"{statistics.fmean(theirs):+.3f} |"
        )
    add("")
    add("### The worst misses, and why")
    add("")
    for row in sorted(rows, key=lambda r: -abs(r.error))[:WORST]:
        add(
            f"- **{row.label}** -- predicted {row.predicted:+.3f} a week, delivered "
            f"{row.delivered:+.3f} over {row.periods} period(s). {row.why()}."
        )
    add("")
    add("### What could not be scored")
    add("")
    add(f"- {skipped.part_missing} sides where only one half of the deal left a roster trace.")
    add(f"- {skipped.multi_team} sides of trades with more than one counterparty.")
    add(f"- {skipped.no_mirror} events where the two teams' reconstructions did not mirror.")
    add(f"- {skipped.no_grade} sides with no gradeable stretch after the move.")
    for message in skipped.refused or []:
        add(f"- refused by the evaluator: {message}")
    return "\n".join(out)


@dataclass(frozen=True)
class Event:
    """Both sides of one deal, so the question can be asked once per trade."""

    rows: tuple[Row, Row]

    @property
    def edge(self) -> float:
        """How much better the evaluator thought the first side's half was."""
        return self.rows[0].predicted - self.rows[1].predicted

    @property
    def got(self) -> float:
        """How much better the first side's half turned out to be."""
        return self.rows[0].delivered - self.rows[1].delivered

    @property
    def right(self) -> bool:
        """Did the evaluator name the side of the deal that did better?"""
        if self.edge == 0.0 or self.got == 0.0:
            return False
        return (self.edge > 0) == (self.got > 0)


def events(rows: Sequence[Row]) -> list[Event]:
    """Rows paired into deals; a side whose partner was not scored is dropped."""
    by_event: dict[str, list[Row]] = {}
    for row in rows:
        by_event.setdefault(row.event, []).append(row)
    return [Event((pair[0], pair[1])) for pair in by_event.values() if len(pair) == 2]


def _line(label: str, rows: Sequence[Row]) -> str:
    predicted = [row.predicted for row in rows]
    delivered = [row.delivered for row in rows]
    errors = [row.error for row in rows]
    return (
        f"| {label} | {len(rows)} | "
        f"{sum(1 for r in rows if r.agrees) / len(rows):.0%} | "
        f"{spearman(predicted, delivered):+.2f} | {pearson(predicted, delivered):+.2f} | "
        f"{statistics.fmean(errors):+.3f} | {statistics.fmean([abs(e) for e in errors]):.3f} |"
    )


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
        with factory() as session:
            for season in args.seasons:
                found = rows_for_season(session, season, skipped, review_days=args.review_days)
                print(f"{season}: {len(found)} sides scored", file=sys.stderr)
                rows.extend(found)
        text = summarise(
            rows, skipped, seconds=time.monotonic() - started, review_days=args.review_days
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
