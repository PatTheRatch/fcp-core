#!/usr/bin/env python3
"""Measure how well the draft price board predicts what players actually went for.

Builds the board exactly as the draft room does (see `_board_for`), joins it to
what the league really paid, and reports the error by price tier, per season and
pooled. This is a *report*: it reads the board, it does not change it.

WHY THIS EXISTS
---------------
In the 2026 draft the board priced Victor Wembanyama at $61 and he went for
$100. The question is whether that is one player or a systematic underprice of
the top tier, and by how much per tier. If it is systematic, the draft room
will under-bid early and lose the players it most wants.

CHOICES MADE, AND WHY
---------------------
**Ranking, not predicted price, defines the bucket.** Players are ranked by
`expected_price` descending and then bucketed by rank. Two reasons:

1. The board prices to exhaust a fixed pot, so the *shape* of the distribution
   is a modelling artefact as much as a market fact. Bucketing by rank asks
   "how well does the board order and separate players", which is the question
   that matters for bidding, rather than "is the board's curve the right shape".
2. Rank is stable; prices compress at the bottom (many $1 players), so price
   bucketing would pile most of the field into one bucket.

**Buckets are 1-5, 6-15, 16-30, 31-60, 61-100, 101+.** These are round-number
cuts that isolate the tiers a draft room actually bids in: the five franchise
players, the two-round core, the mid-round starters, the late-round filler, the
dollar bin, and the undrafted fringes. They are not equal-width because the
interesting behaviour is at the top, where money concentrates.

**Ties in rank are broken by `expected_price`, then by `player_id` ascending.**
Measured: there are zero ties in the top 100 of any season, so the tiebreak is
defensive and does not affect any reported number. It is specified anyway so the
output is deterministic.

**Off-board drafted players are excluded from the bucket table but reported
separately**, with their dollar spend, because that money is invisible to the
board and including them would require inventing a predicted price.

**2020 is the COVID-shortened season.** It is reported throughout, flagged, and
the pooled tables are given both with and without it.

**Pooling is split by team count.** The same player is worth fewer dollars in a
16-team league because the pot is fixed and shared wider, so pooling across
sizes would blur the answer. Pooled tables are keyed on team count.

**Error is actual minus predicted**, so a positive mean error means the league
paid MORE than the board said (the board underpriced that tier).

WHAT THIS DOES NOT DO
---------------------
It does not test whether the board's *ordering* is good, only its calibration in
dollars per tier. A board can be perfectly ordered and still mispriced.

Output goes to stdout and to `reports/board_calibration.md` (reports/ is
gitignored, so the file is a convenience, not an artifact).
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Player
from app.draft import pool
from app.draft.market import PriceBoard, price_board
from app.draft.valuation import value_players

#: (label, first rank, last rank) with rank 1-based and inclusive.
#: Chosen to isolate the tiers a draft room bids in; see module docstring.
BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("1-5", 1, 5),
    ("6-15", 6, 15),
    ("16-30", 16, 30),
    ("31-60", 31, 60),
    ("61-100", 61, 100),
    ("101+", 101, 10_000),
)

COVID_SEASON = 2020
SEASONS: tuple[int, ...] = tuple(range(2019, 2027))
REPORT_PATH = Path("reports/board_calibration.md")


@dataclass(frozen=True)
class Compared:
    """One drafted player, with the board's price and what the league paid."""

    season: int
    team_count: int
    player_id: int
    name: str
    predicted: int
    actual: int
    rank: int

    @property
    def error(self) -> int:
        """Actual minus predicted. Positive means the board underpriced him."""
        return self.actual - self.predicted


@dataclass(frozen=True)
class SeasonResult:
    """Everything one season contributes to the report."""

    season: int
    team_count: int
    compared: tuple[Compared, ...]
    off_board: tuple[tuple[str, int], ...]
    off_board_money: int
    total_money: int
    board_size: int
    drafted: int


def _board_for(session: Session, league_season: LeagueSeason) -> PriceBoard:
    """Build the board exactly as the draft room does.

    Every argument here is load-bearing and was specified by the caller:
    `roster_size_for` rather than `roster_slots` (the latter reads the rounds
    that happened to be drafted), and `auction_budget` rather than
    `acquisition_budget` (the latter is the FAAB pot and is half the size).
    """
    categories = pool.season_categories(session, league_season)
    projections = pool.load_projections(session, league_season.season, kind="projected")
    slots = pool.roster_size_for(league_season)
    values = value_players(projections, categories)
    return price_board(
        values,
        teams=league_season.team_count,
        budget_per_team=league_season.auction_budget,
        roster_slots=slots,
    )


def _bucket_of(rank: int) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= rank <= hi:
            return label
    raise AssertionError(f"rank {rank} fell outside every bucket")


def collect(session: Session) -> list[SeasonResult]:
    """Build and compare the board for every season we hold."""
    results: list[SeasonResult] = []
    for season in SEASONS:
        league_season = session.scalars(
            select(LeagueSeason).where(LeagueSeason.season == season)
        ).one()
        board = _board_for(session, league_season)
        actual = pool.drafted_prices(session, league_season)

        # Rank on the board's own order, tiebroken so output is deterministic.
        # price_board already sorts by value descending; re-sorting on
        # (expected_price, player_id) makes the rank independent of that.
        ordered = sorted(board.players, key=lambda p: (-p.expected_price, p.player_id))

        names = {
            int(espn_id): str(name)
            for espn_id, name in session.execute(
                select(Player.espn_player_id, Player.name)
            ).all()
        }

        compared: list[Compared] = []
        off_board: list[tuple[str, int]] = []
        for rank, priced in enumerate(ordered, start=1):
            paid = actual.get(priced.player_id)
            if paid is None:
                continue
            compared.append(
                Compared(
                    season=season,
                    team_count=league_season.team_count,
                    player_id=priced.player_id,
                    name=priced.name,
                    predicted=priced.expected_price,
                    actual=int(paid),
                    rank=rank,
                )
            )

        board_ids = {p.player_id for p in board.players}
        for player_id, paid in actual.items():
            if player_id not in board_ids:
                off_board.append((names.get(player_id, f"id {player_id}"), int(paid)))

        off_board.sort(key=lambda pair: -pair[1])
        results.append(
            SeasonResult(
                season=season,
                team_count=league_season.team_count,
                compared=tuple(compared),
                off_board=tuple(off_board),
                off_board_money=sum(paid for _, paid in off_board),
                total_money=sum(int(v) for v in actual.values()),
                board_size=len(board.players),
                drafted=len(actual),
            )
        )
    return results


def _fmt_bucket_rows(rows: Iterable[Compared]) -> list[str]:
    """One table row per bucket, for the given compared players."""
    lines: list[str] = []
    header = (
        f"| {'bucket':<8} | {'n':>4} | {'mean pred':>9} | {'mean actual':>11} "
        f"| {'mean error':>10} | {'MAE':>6} | {'ratio':>6} |"
    )
    lines.append(header)
    lines.append(
        f"|{'-' * 10}|{'-' * 6}|{'-' * 11}|{'-' * 13}|{'-' * 12}|"
        f"{'-' * 8}|{'-' * 8}|"
    )
    for label, lo, hi in BUCKETS:
        bucket = [c for c in rows if lo <= c.rank <= hi]
        if not bucket:
            continue
        preds = [c.predicted for c in bucket]
        actuals = [c.actual for c in bucket]
        errors = [c.error for c in bucket]
        mae = statistics.fmean(abs(e) for e in errors)
        total_pred = sum(preds)
        ratio = (sum(actuals) / total_pred) if total_pred else float("nan")
        lines.append(
            f"| {label:<8} | {len(bucket):>4} | {statistics.fmean(preds):>9.1f} "
            f"| {statistics.fmean(actuals):>11.1f} "
            f"| {statistics.fmean(errors):>+10.1f} | {mae:>6.1f} "
            f"| {ratio:>6.2f} |"
        )
    return lines


def report(results: Sequence[SeasonResult]) -> str:
    """Render the whole report as markdown."""
    out: list[str] = []
    add = out.append

    add("# Draft board calibration")
    add("")
    add("How well the price board predicts what this league actually paid, by")
    add("price tier. The board is built exactly as the draft room builds it;")
    add("nothing here modifies it.")
    add("")
    add("**Reading the tables:** error is *actual minus predicted*, so a positive")
    add("mean error means the league paid **more** than the board said — the board")
    add("**underpriced** that tier. `ratio` is `sum(actual) / sum(predicted)`.")
    add("")
    add("## Coverage")
    add("")
    add("| season | teams | board | drafted | matched | off-board | off-board $ | total $ |")
    add("|---|---|---|---|---|---|---|---|")
    for r in results:
        flag = "  *(COVID)*" if r.season == COVID_SEASON else ""
        add(
            f"| {r.season}{flag} | {r.team_count} | {r.board_size} | {r.drafted} "
            f"| {len(r.compared)} | {len(r.off_board)} | ${r.off_board_money} "
            f"| ${r.total_money} |"
        )
    add("")

    for r in results:
        flag = " — COVID-shortened season" if r.season == COVID_SEASON else ""
        add(f"## {r.season} ({r.team_count} teams){flag}")
        add("")
        out.extend(_fmt_bucket_rows(r.compared))
        add("")

    add("## Pooled by team count")
    add("")
    add("Pooled across seasons, split by team count: a fixed pot shared wider")
    add("means the same player is worth fewer dollars in a bigger league, so")
    add("mixing sizes would blur the answer. 2020 excluded here.")
    add("")
    for teams in sorted({r.team_count for r in results}):
        seasons = [r for r in results if r.team_count == teams and r.season != COVID_SEASON]
        if not seasons:
            continue
        pooled = [c for r in seasons for c in r.compared]
        names = ", ".join(str(r.season) for r in seasons)
        add(f"### {teams} teams — seasons {names}")
        add("")
        out.extend(_fmt_bucket_rows(pooled))
        add("")

    add("### Pooled, all team counts (for reference only)")
    add("")
    add("Included so the team-count split above can be sanity-checked against")
    add("the naive pool. Do not read conclusions off this table.")
    add("")
    non_covid = [c for r in results if r.season != COVID_SEASON for c in r.compared]
    out.extend(_fmt_bucket_rows(non_covid))
    add("")
    with_covid = [c for r in results for c in r.compared]
    add("Including 2020:")
    add("")
    out.extend(_fmt_bucket_rows(with_covid))
    add("")

    add("## Largest underpays and overpays")
    add("")
    everything = [c for r in results for c in r.compared]
    by_error = sorted(everything, key=lambda c: c.error)
    add("### Ten largest underpays (league paid most above the board)")
    add("")
    add("| season | name | predicted | actual | error |")
    add("|---|---|---|---|---|")
    for c in by_error[-10:][::-1]:
        add(f"| {c.season} | {c.name} | ${c.predicted} | ${c.actual} | {c.error:+d} |")
    add("")
    add("### Ten largest overpays (board most above what the league paid)")
    add("")
    add("| season | name | predicted | actual | error |")
    add("|---|---|---|---|---|")
    for c in by_error[:10]:
        add(f"| {c.season} | {c.name} | ${c.predicted} | ${c.actual} | {c.error:+d} |")
    add("")

    add("## Drafted players not on the board")
    add("")
    add("Money spent on players the board does not price is invisible to it.")
    add("")
    add("| season | n | $ spent | players |")
    add("|---|---|---|---|")
    for r in results:
        listed = ", ".join(f"{name} (${paid})" for name, paid in r.off_board) or "—"
        add(f"| {r.season} | {len(r.off_board)} | ${r.off_board_money} | {listed} |")
    add("")

    return "\n".join(out)


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        results = collect(session)
    text = report(results)
    print(text)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"\nwritten to {REPORT_PATH}")


if __name__ == "__main__":
    main()
