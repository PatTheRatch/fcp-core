#!/usr/bin/env python3
"""Which preseason projection makes the in-season tool better: ESPN's or BBM's?

Usage:
    python scripts/projection_prior.py
    python scripts/projection_prior.py --bbm data/bbm/BBM_Projections_2026.xls
    python scripts/projection_prior.py --checkpoints 21 63 126

WHAT THIS MEASURES

`app/scoring/knowable` blends a player's own games with a preseason projection
(`player_season_stats`, `kind="projected"`), and every in-season number the
product publishes -- the pickup recommender's categories a week, the hurdles,
the trade calibration -- was measured with ESPN's forecast as the prior.
Basketball Monster's projections are paid, measure availability better, and
are used only by the 2027 draft room. So: does the in-season tool get better
if the blend is shrunk toward BBM's forecast instead?

The comparison is stated in the fit's own units. `knowable`'s docstring fits
PRIOR_GAMES and RECENT_WEIGHT on 7,165 player-checkpoints over the six seasons
with a real preseason projection, scoring each player's next 28 days with
each of the eleven counts scaled by its spread. This repeats that fit one
season at a time, at the six checkpoints the docstring uses (every sixth of
the season: days 21, 42, 63, 84, 105, 126), varying the prior and nothing else:

    prior        that forecast alone, no games
    to date      his games so far, no prior
    blend        the knowable line: to date shrunk toward the prior at
                 n/(n+15), then 15% of the last 14 days on top
    note blend   to date shrunk toward the prior at n/(n+10), the k=20 shape
                 docs/pickups.md section 4.2 proposed and the fit beat

THE ERROR METRIC. For a player-checkpoint each count's error is the absolute
difference between the forecast per-game rate over the next 28 days and what
he actually did, divided by that count's spread, summed over the eleven counts
of `COUNTS` (the nine scored categories plus the made and attempted shots
behind the two percentages). Lower is better. The docstring's own numbers are
pooled over six seasons; 2026's are printed first, on the ESPN prior, so the
season this study is about can be read against the shape it should have.

WHY ONE SEASON

The only BBM preseason export we hold is 2026's. `bbm_projections` holds 2027,
captured after that draft, so it is not a preseason file at all. The
comparison is therefore 2026: 14 teams, one season. That is the first
limitation in the write-up.

WHICH PLAYERS

Rostered players only -- a man on some team's roster before the checkpoint
(`daily_lineup_slots`, any slot but FA) -- because that is the population the
recommender answers about. The headline table keeps only players BOTH priors
cover, so the two arms are the same men and the difference is the forecast
rather than the coverage. The coverage table carries all of them, so what
BBM's strict name matching costs is visible too.

THE CURRENCY TABLE

Error in the fit's units is not the currency the product publishes. So each
arm's line is also priced through `app.scoring.value.marginal` inside a roster
of the period's own length (`app.pickups.judge.standard_lens`, the lens
`pickup_values` measures the wire with): the mean absolute difference, in
categories a week, between what the line said a man was worth and what he then
delivered. That is the number that says whether a hurdle would move.

Read-only: SELECTs only, nothing written, no file written.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    MatchupPeriod,
    Player,
    PlayerGameStat,
    PlayerSeasonStat,
)
from app.db.session import make_engine, make_session_factory
from app.draft.bbm import load_bbm
from app.draft.targets import CategoryDistribution
from app.pickups.judge import standard_lens
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

#: A checkpoint every sixth of the season: the docstring's days 21 to 126.
CHECKPOINTS = (21, 42, 63, 84, 105, 126)

#: What a checkpoint predicts: the docstring's "next 28 days".
HORIZON = 28

#: The season the BBM export speaks to. 2026 is the only one we hold.
BBM_SEASON = 2026

DEFAULT_BBM = Path("/opt/fcp-core/data/bbm/BBM_Projections_2026.xls")

#: Fewer paired players than this at a checkpoint and the row is not reported.
MIN_PAIRED = 10

#: The product's shape, and the design note's, as (games weight, recent weight).
PRODUCT: tuple[int, float] = (PRIOR_GAMES, RECENT_WEIGHT)
NOTE: tuple[int, float] = (20, 0.0)

#: Weights swept at the end, so how much shape is on the table is visible.
WEIGHT_GRID: tuple[tuple[int, float], ...] = (
    (0, 0.0),
    (5, 0.0),
    (10, 0.0),
    (10, 0.15),
    (15, 0.0),
    (15, 0.15),
    (20, 0.0),
    (30, 0.0),
)

#: Games played at the checkpoint, split the way the brief asks.
GAMES_BANDS: tuple[tuple[str, int, int], ...] = (
    ("0-10", 0, 10),
    ("11-30", 11, 30),
    ("31+", 31, 10_000),
)

#: The nine scored categories, in the order the pages show them.
CATEGORIES = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")


def band_of(games: int) -> str:
    """Which games-played band a checkpoint falls in."""
    for label, low, high in GAMES_BANDS:
        if low <= games <= high:
            return label
    return GAMES_BANDS[-1][0]


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


# ---------------------------------------------------------------------------
# the data, read once
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GameRow:
    """One played game: the day, and the eleven counts."""

    day: int
    counts: dict[str, float]


@dataclass
class SeasonData:
    """A season's box scores, rosters and ESPN projections, loaded once."""

    season: int
    league_season: LeagueSeason
    #: player id -> his played games (played, minutes > 0), by day.
    games: dict[int, list[GameRow]]
    #: player id -> the first day he held a roster place (any slot but FA).
    first_rostered: dict[int, int]
    #: player id -> his primary position, where the projection carries one.
    positions: dict[int, str]
    #: ESPN player id -> `players.id`, because BBM's rows arrive keyed by the
    #: former and everything in `player_game_stats` is keyed by the latter.
    by_espn: dict[int, int]
    #: BBM's per-game rates, keyed on `players.id`, filled in after the load.
    bbm: dict[int, Rate]
    #: ESPN's stored preseason projection, per game.
    espn: dict[int, Rate]
    #: Games a man plays in an ordinary week, measured from the season.
    games_a_week: float


def load_season(session: Session, season: int) -> SeasonData:
    """Read one season into memory: four queries and nothing after."""
    league_season = session.scalars(select(LeagueSeason).where(LeagueSeason.season == season)).one()

    by_espn: dict[int, int] = {
        int(espn_id): int(player_id)
        for player_id, espn_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.espn_player_id.is_not(None))
        ).all()
    }
    positions: dict[int, str] = {}
    espn: dict[int, Rate] = {}
    for stat in session.scalars(
        select(PlayerSeasonStat).where(
            PlayerSeasonStat.season == season, PlayerSeasonStat.kind == "projected"
        )
    ):
        player_id = int(stat.player_id)
        if stat.primary_position:
            positions[player_id] = str(stat.primary_position)
        totals = {
            key: float(value)
            for key, value in (stat.raw_totals or {}).items()
            if isinstance(value, int | float)
        }
        rate = rate_from_totals(totals, float(stat.games_played or 0.0))
        if rate is not None:
            espn[player_id] = rate

    columns = [getattr(PlayerGameStat, column) for column in COUNTS.values()]
    games: dict[int, list[GameRow]] = {}
    played_days: set[tuple[int, int]] = set()
    for row in session.execute(
        select(PlayerGameStat.player_id, PlayerGameStat.scoring_period, *columns).where(
            PlayerGameStat.season == season,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes > 0,
        )
    ).all():
        player_id, day = int(row[0]), int(row[1])
        games.setdefault(player_id, []).append(
            GameRow(
                day=day,
                counts={key: float(row[index] or 0.0) for index, key in enumerate(COUNTS, start=2)},
            )
        )
        played_days.add((player_id, day))
    for rows in games.values():
        rows.sort(key=lambda game: game.day)

    first_rostered: dict[int, int] = {}
    for player_id, day in session.execute(
        select(DailyLineupSlot.player_id, DailyLineupSlot.scoring_period)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            DailyLineupSlot.slot != "FA",
        )
    ).all():
        pid, found = int(player_id), int(day)
        if pid not in first_rostered or found < first_rostered[pid]:
            first_rostered[pid] = found

    #: Games a man plays in an ordinary week: his season's played games over
    #: the regular season's weeks. Measured, not assumed at seven: an NBA
    #: team plays about three and a half games a week and a roster is not one
    #: man, and the currency table has to spread a per-game rate over the
    #: games a week a manager actually gets.
    weeks = max(
        1.0,
        sum(1 for day in {day for _, day in played_days}) / 7.0,
    )
    games_a_week = sum(len(rows) for rows in games.values()) / len(games) / weeks
    return SeasonData(
        season=season,
        league_season=league_season,
        games=games,
        first_rostered=first_rostered,
        positions=positions,
        by_espn=by_espn,
        bbm={},
        espn=espn,
        games_a_week=float(games_a_week),
    )


def bbm_prior(
    session: Session, data: SeasonData, path: Path, season: int
) -> tuple[dict[int, Rate], dict[str, object]]:
    """BBM's export as a per-game prior, keyed on `players.id`.

    Matched with the room's own strict matcher (`load_bbm`), the only
    sanctioned way a BBM name becomes one of ours. A row that matches nobody
    is a player with no BBM projection, exactly as a season ESPN published no
    line for leaves the blend on games alone.

    `load_bbm` keys its projections **by ESPN player id**, while
    `player_season_stats.player_id` and `player_game_stats.player_id` are
    `players.id`, and in this database the two are different numbers (id 2 is
    ESPN 3133628). So the ESPN id is translated back through `data.by_espn`;
    a matched name the roster never carried has nothing to translate to and is
    reported as such rather than silently dropped.
    """
    loaded = load_bbm(session, path, season)
    prior: dict[int, Rate] = {}
    translated = 0
    for projection in loaded.projections:
        espn_id = int(projection.player_id)
        row = loaded.rows.get(espn_id)
        our_id = data.by_espn.get(espn_id)
        if row is None or our_id is None:
            continue
        translated += 1
        totals = dict(projection.totals)
        totals["FGM"] = row.fg_pct * totals.get("FGA", 0.0)
        totals["FTM"] = row.ft_pct * totals.get("FTA", 0.0)
        rate = rate_from_totals(totals, row.games)
        if rate is not None:
            prior[our_id] = rate
    return prior, {
        "rows": len(loaded.projections),
        "matched": loaded.matched,
        "loose": len(loaded.loose),
        "unmatched": len(loaded.unmatched),
        "translated": translated,
        "rates": len(prior),
    }


# ---------------------------------------------------------------------------
# the blends
# ---------------------------------------------------------------------------


def rate_of(rows: Sequence[GameRow]) -> dict[str, float]:
    """A per-game rate over the eleven counts. No games, a zero line."""
    if not rows:
        return dict.fromkeys(COUNTS, 0.0)
    return {key: sum(game.counts[key] for game in rows) / len(rows) for key in COUNTS}


def blended(
    to_date: Mapping[str, float],
    recent: Mapping[str, float],
    prior: Mapping[str, float] | None,
    games: int,
    weights: tuple[int, float],
    *,
    recent_games: int | None = None,
) -> dict[str, float]:
    """The knowable arithmetic, with the weights as parameters.

    `app.scoring.knowable.knowable` is the product's own implementation; this
    is the same arithmetic with the weights exposed, because the sweep needs
    them varied and the product's fit deliberately does not.
    `check_blend_against_product` holds the two to the same answer.

    `recent_games` is the count behind the recent window and is not optional
    in spirit: the mixture applies only when the window holds a game, and a
    rate of zeros cannot say whether it did. Callers pass the count they
    measured; left out, the mix applies whenever the rate carries any
    non-zero value, which is the same answer for every window that has games.
    """
    prior_games, recent_weight = weights
    if prior is None:
        base = dict(to_date)
    elif prior_games == 0:
        # No games of evidence at all: the prior is the whole line, which is
        # the sweep's own "projection alone" endpoint and not a division by it.
        base = dict(prior)
    else:
        base = _mix(dict(to_date), dict(prior), games / (games + prior_games))
    if recent_games and recent_weight > 0:
        return _mix(dict(recent), base, recent_weight)
    return base


def over(rate: Mapping[str, float], games: float) -> CategoryLine:
    """A rate line spread over a number of games."""
    return CategoryLine({key: rate[key] * games for key in COUNTS}, round(games))


def scaled_error(
    forecast: Mapping[str, float], truth: Mapping[str, float], spread: Mapping[str, float]
) -> float:
    """One player-checkpoint's error in the fit's units.

    Eleven counts, each the absolute difference in per-game rate divided by
    the count's spread, summed. This is the docstring's quantity. A count
    with no spread across the population (nobody posted one, or everybody
    posted the same number) carries no information and is left out.
    """
    total = 0.0
    for key in COUNTS:
        deviation = spread.get(key, 0.0)
        if deviation > 0:
            total += abs(forecast[key] - truth[key]) / deviation
    return total


@dataclass(frozen=True)
class Record:
    """One rostered player-checkpoint: both priors, and what followed it.

    Every record carries whichever priors exist for the man, so a population
    is a filter over one pass rather than a different pass: the paired cut,
    the ESPN arm and the BBM arm all read the same numbers.
    """

    player_id: int
    day: int
    games: int
    position: str | None
    to_date: dict[str, float]
    recent: dict[str, float]
    #: Games in the last `RECENT_DAYS`. Carried because the rate alone cannot
    #: say whether there were any: an empty window and a window of zero-point
    #: games both reduce to a dict of zeros, and `knowable` mixes on the count.
    recent_games: int
    espn: Rate | None
    bbm: Rate | None
    actual: dict[str, float]
    actual_games: int

    @property
    def paired(self) -> bool:
        return self.espn is not None and self.bbm is not None

    def prior(self, arm: str) -> Rate | None:
        return self.espn if arm == "espn" else self.bbm


def records_for(data: SeasonData, day: int) -> list[Record]:
    """Every rostered player-checkpoint on `day`, with what followed it.

    Everyone rostered with either vendor's projection, on one pass over the
    season held in memory, so the forecast and the truth come out of the same
    numbers and cannot disagree about what a game was. The blend itself is
    not computed here, since it is a function of the record and the weights
    and one pass has to serve every cut and every cell of the sweep.
    """
    out: list[Record] = []
    for player_id, rows in data.games.items():
        first = data.first_rostered.get(player_id)
        if first is None or first >= day:
            continue
        if player_id not in data.espn and player_id not in data.bbm:
            continue
        before = [game for game in rows if game.day < day]
        after = [game for game in rows if day <= game.day < day + HORIZON]
        if not after:
            continue
        out.append(
            Record(
                player_id=player_id,
                day=day,
                games=len(before),
                position=data.positions.get(player_id),
                to_date=rate_of(before),
                recent=rate_of([game for game in before if game.day >= day - RECENT_DAYS]),
                recent_games=sum(1 for game in before if game.day >= day - RECENT_DAYS),
                espn=data.espn.get(player_id),
                bbm=data.bbm.get(player_id),
                actual=rate_of(after),
                actual_games=len(after),
            )
        )
    return out


# ---------------------------------------------------------------------------
# the two currencies
# ---------------------------------------------------------------------------


def spreads(records: Sequence[Record]) -> dict[str, float]:
    """Each count's spread over the records, which is what its error divides by.

    Taken over the player-checkpoints of the cut being reported, which is the
    only reading under which every row of a table is on the same scale.
    """
    out: dict[str, float] = {}
    for key in COUNTS:
        values = [record.actual[key] for record in records]
        out[key] = statistics.pstdev(values) if len(values) > 1 else 0.0
    return out


def errors(
    records: Sequence[Record],
    spread: Mapping[str, float],
    weights: tuple[int, float],
    *,
    arms: Sequence[str] = ("espn", "bbm"),
) -> dict[str, float]:
    """Mean error in the fit's units, per arm, its prior alone, and to date.

    An arm's endpoints are averaged over the men whose record carries that
    arm's prior, so a table of one population still reports both. Read on a
    population that carries both (the paired cut) the two are the same set of
    men, which is what makes the columns comparable there; read on all
    rostered men they are not, and belong beside the counts, not against each
    other.
    """
    if not records:
        return {}
    keys = (*arms, *(f"{arm}_alone" for arm in arms), "to_date")
    totals = dict.fromkeys(keys, 0.0)
    counts = dict.fromkeys(keys, 0)
    for record in records:
        totals["to_date"] += scaled_error(record.to_date, record.actual, spread)
        counts["to_date"] += 1
        for arm in arms:
            prior = record.prior(arm)
            if prior is None:
                continue
            totals[arm] += scaled_error(
                blended(
                    record.to_date,
                    record.recent,
                    prior,
                    record.games,
                    weights,
                    recent_games=record.recent_games,
                ),
                record.actual,
                spread,
            )
            totals[f"{arm}_alone"] += scaled_error(prior, record.actual, spread)
            counts[arm] += 1
            counts[f"{arm}_alone"] += 1
    return {key: (totals[key] / counts[key] if counts[key] else float("nan")) for key in keys}


def scales(records: Sequence[Record], spread: Mapping[str, float]) -> dict[str, float]:
    """Each count's error under a zero line, over the records that have a prior.

    A sanity floor with a name: no forecast can be worse than predicting
    nothing, so a scaled error at or above this is a wiring defect rather
    than a finding. On the paired population this is the line a broken arm
    would sit just above.
    """
    if not records:
        return {}
    return {
        arm: mean(
            [
                scaled_error(dict.fromkeys(COUNTS, 0.0), record.actual, spread)
                for record in records
                if record.prior(arm) is not None
            ]
        )
        for arm in ("espn", "bbm")
    }


def worth(
    line: CategoryLine,
    average: CategoryLine,
    distributions: Sequence[CategoryDistribution],
) -> float:
    """A weekly line's value in categories, inside the league-average roster."""
    return marginal(average, line, distributions)


def category_errors(
    records: Sequence[Record],
    data: SeasonData,
    weights: tuple[int, float],
    lens: tuple[CategoryLine, Sequence[CategoryDistribution]],
) -> dict[str, float]:
    """Mean absolute error in categories a week: both arms, both alone, to date.

    Each line -- the forecast's and the player's own -- is priced by the same
    lens over the same number of games (the games a week a manager gets), so
    the difference is the forecast being wrong in the product's own currency.
    Reported to four decimals: the quantity here is a fraction of a category,
    and at two decimals every arm would read the same and the table would say
    nothing.
    """
    if not records:
        return {}
    average, distributions = lens
    games = data.games_a_week
    keys = ("espn", "espn_alone", "bbm", "bbm_alone", "to_date")
    totals = dict.fromkeys(keys, 0.0)
    counts = dict.fromkeys(keys, 0)
    for record in records:
        truth = worth(over(record.actual, games), average, distributions)
        totals["to_date"] += abs(worth(over(record.to_date, games), average, distributions) - truth)
        counts["to_date"] += 1
        for arm in ("espn", "bbm"):
            prior = record.prior(arm)
            if prior is None:
                continue
            forecast = blended(
                record.to_date,
                record.recent,
                prior,
                record.games,
                weights,
                recent_games=record.recent_games,
            )
            totals[arm] += abs(worth(over(forecast, games), average, distributions) - truth)
            totals[f"{arm}_alone"] += abs(worth(over(prior, games), average, distributions) - truth)
            counts[arm] += 1
            counts[f"{arm}_alone"] += 1
    return {key: (totals[key] / counts[key] if counts[key] else float("nan")) for key in keys}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def table(title: str, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    print(f"\n### {title}")
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def f2(value: float) -> str:
    return "—" if value != value else f"{value:.2f}"


ERROR_HEADERS = (
    "checkpoint",
    "n",
    "n with a prior (ESPN/BBM)",
    "ESPN prior alone",
    "ESPN blend",
    "BBM prior alone",
    "BBM blend",
    "to date alone",
)
PAIRED_HEADERS = (
    "checkpoint",
    "n",
    "ESPN prior alone",
    "ESPN blend",
    "BBM prior alone",
    "BBM blend",
    "to date alone",
)
CATEGORY_HEADERS = (
    "checkpoint",
    "n",
    "ESPN prior alone",
    "ESPN blend",
    "BBM prior alone",
    "BBM blend",
    "to date alone",
)


BLANK = ("—",) * 5


def error_row(label: str, records: Sequence[Record], spread: Mapping[str, float]) -> list[object]:
    """One row of the coverage table: every man, each arm on what it covers.

    An arm is averaged over the men its own prior reaches, so the counts
    differ by column here on purpose; table 1 is where the two are held to
    the same men.
    """
    if not records or not spread:
        return [label, len(records), *BLANK]
    found = errors(records, spread, PRODUCT)
    reached = {arm: sum(1 for r in records if r.prior(arm) is not None) for arm in ("espn", "bbm")}
    return [
        label,
        len(records),
        f"{reached['espn']}/{reached['bbm']}",
        f2(found["espn_alone"]),
        f2(found["espn"]),
        f2(found["bbm_alone"]),
        f2(found["bbm"]),
        f2(found["to_date"]),
    ]


def paired_row(label: str, records: Sequence[Record], spread: Mapping[str, float]) -> list[object]:
    """One row of the paired tables: the same men under both priors.

    `errors` reads each arm from the record's own prior, and on this
    population every record carries both, so the two arms are the same men
    and the difference is the forecast rather than the coverage.
    """
    if not records or not spread:
        return [label, len(records), *BLANK]
    found = errors(records, spread, PRODUCT)
    return [
        label,
        len(records),
        f2(found["espn_alone"]),
        f2(found["espn"]),
        f2(found["bbm_alone"]),
        f2(found["bbm"]),
        f2(found["to_date"]),
    ]


def category_row(
    label: str,
    records: Sequence[Record],
    data: SeasonData,
    lens: tuple[CategoryLine, Sequence[CategoryDistribution]],
) -> list[object]:
    """One row of the currency table: four decimals, not the metres elsewhere.

    A category a week is the unit the recommender's hurdles are written in,
    and the errors here are hundredths of one. Two decimals would flatten
    every arm to the same number and hide the answer.
    """
    if not records:
        return [label, 0, *BLANK]
    found = category_errors(records, data, PRODUCT, lens)
    return [
        label,
        len(records),
        *(f"{found[key]:.4f}" for key in ("espn_alone", "espn", "bbm_alone", "bbm", "to_date")),
    ]


def per_category(
    records: Sequence[Record], spread: Mapping[str, float], weights: tuple[int, float]
) -> dict[str, dict[str, float]]:
    """Each count's mean scaled error under each arm, so the source is visible.

    Two decimals on a spread-scaled number: a difference smaller than 0.01
    of a spread is not a finding about a category.
    """
    out: dict[str, dict[str, float]] = {}
    for key in COUNTS:
        if spread.get(key, 0.0) <= 0:
            continue
        row: dict[str, float] = {}
        for arm in ("espn", "bbm"):
            values = []
            for record in records:
                prior = record.prior(arm)
                forecast = blended(
                    record.to_date,
                    record.recent,
                    prior,
                    record.games,
                    weights,
                    recent_games=record.recent_games,
                )
                values.append(abs(forecast[key] - record.actual[key]) / spread[key])
            row[arm] = mean(values)
        out[key] = row
    return out


def coverage_rows(records: Sequence[Record], spread: Mapping[str, float]) -> list[list[object]]:
    """What each prior covers, and what its being absent costs.

    The arms are one prior each, so a man BBM cannot place is simply absent
    from BBM's arm -- the rule `knowable` already applies to a season ESPN
    published no line for. The gap between the two counts is the name
    matcher's cost, and the third column is that cost in the fit's own units:
    the men BBM cannot reach, scored on the ESPN prior, against the men it
    reaches.
    """
    both = [record for record in records if record.paired]
    bbm_missing = [record for record in records if record.espn and not record.bbm]
    espn_missing = [record for record in records if record.bbm and not record.espn]
    return [
        [
            "both priors",
            len(both),
            f2(mean([r.games for r in both])),
            f2(errors(both, spread, PRODUCT)["espn"]),
        ],
        [
            "BBM cannot place (ESPN only)",
            len(bbm_missing),
            f2(mean([r.games for r in bbm_missing])),
            f2(errors(bbm_missing, spread, PRODUCT)["espn"]),
        ],
        [
            "ESPN has no line (BBM only)",
            len(espn_missing),
            f2(mean([r.games for r in espn_missing])),
            f2(errors(espn_missing, spread, PRODUCT)["bbm"]),
        ],
    ]


def check_blend_against_product(
    session: Session, data: SeasonData, day: int, bbm: Mapping[int, Rate]
) -> str:
    """Hold `blended` to `app.scoring.knowable.knowable` on the same inputs.

    The product's function is the definition; this script's is a copy with the
    weights exposed. If the two ever disagree the measurement is about a
    different blend than the product runs, so this is checked, not asserted.
    """
    from app.scoring.knowable import knowable, prior_from

    worst = 0.0
    checked = 0
    for prior in (data.espn, bbm):
        for record in records_for(data, day):
            if record.player_id not in prior:
                continue
            expected = knowable(
                session, record.player_id, data.season, day, prior=prior_from(prior)
            )
            mine = blended(
                record.to_date,
                record.recent,
                prior.get(record.player_id),
                record.games,
                PRODUCT,
                recent_games=record.recent_games,
            )
            for key in COUNTS:
                worst = max(worst, abs(mine[key] - expected.per_game.get(key)))
            checked += 1
    return (
        f"blend checked against app.scoring.knowable on {checked} player-arms, "
        f"worst gap {worst:.2e}"
    )


def main() -> int:
    started = time.time()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=BBM_SEASON)
    ap.add_argument("--bbm", type=Path, default=DEFAULT_BBM)
    ap.add_argument("--checkpoints", type=int, nargs="+", default=list(CHECKPOINTS))
    ap.add_argument("--bands", action="store_true", help="the games-played split")
    ap.add_argument("--categories", action="store_true", help="the per-category split")
    ap.add_argument("--currency", action="store_true", help="the marginal-error table")
    ap.add_argument("--sweep", action="store_true", help="the weights sweep")
    args = ap.parse_args()
    detailed = args.bands or args.categories or args.currency or args.sweep
    args.bands = args.bands or not detailed
    args.categories = args.categories or not detailed
    args.currency = args.currency or not detailed
    args.sweep = args.sweep or not detailed

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        season = args.season
        print(f"# Which prior makes the in-season blend better: ESPN or BBM, {season}")
        data = load_season(session, season)
        bbm, info = bbm_prior(session, data, args.bbm, season)
        data.bbm = bbm
        print(
            f"# BBM export {args.bbm.name}: {info['rows']} rows, {info['matched']} matched to an "
            f"ESPN id ({info['loose']} by short first name), {info['unmatched']} unplaced, "
            f"{info['rates']} usable rates"
        )
        print(f"# {check_blend_against_product(session, data, args.checkpoints[0], bbm)}")
        print(f"# load {time.time() - started:.1f}s")

        lens = standard_lens(session, data.league_season, args.checkpoints[0])
        print(
            f"# the currency lens: {len(lens.distributions)} categories over "
            f"{lens.distributions[0].period_days if lens.distributions else 0} days, "
            f"games a week {data.games_a_week:.2f}"
        )

        by_checkpoint: dict[int, list[Record]] = {
            day: records_for(data, day) for day in args.checkpoints
        }
        everyone = [r for day in args.checkpoints for r in by_checkpoint[day]]
        paired = [r for r in everyone if r.paired]
        spread = spreads(paired)
        print(f"# checkpoints {list(args.checkpoints)}")
        print(f"# rostered player-checkpoints {len(everyone)}; paired (both priors) {len(paired)}")
        zero = scales(paired, spread)
        print(
            f"# a zero line on the paired set would score "
            f"{zero['espn']:.2f} (ESPN men) / {zero['bbm']:.2f} (BBM men): "
            "any forecast at or above its own is a wiring defect, not a finding"
        )

        print("\n## 1. The fit's own units, on the players both priors cover")
        rows = [
            paired_row(str(day), [r for r in by_checkpoint[day] if r.paired], spread)
            for day in args.checkpoints
        ]
        rows.append(paired_row("pooled", paired, spread))
        table(
            "Paired population: only men with both an ESPN and a BBM projection",
            PAIRED_HEADERS,
            rows,
        )

        print("\n## 2. The same, every rostered player (the coverage effect)")
        rows = [error_row(str(day), by_checkpoint[day], spread) for day in args.checkpoints]
        rows.append(error_row("pooled", everyone, spread))
        table(
            "Every rostered man; an arm the man's prior misses leaves him to to date",
            ERROR_HEADERS,
            rows,
        )

        print("\n## 3. Coverage")
        table(
            "What each prior covers, and what its being absent costs",
            (
                "population",
                "player-checkpoints",
                "mean games at the checkpoint",
                "blend error on the arm it does cover",
            ),
            coverage_rows(everyone, spread),
        )

        if args.bands:
            print("\n## 4. By games played at the checkpoint (paired players)")
            rows = []
            for label, _, _ in GAMES_BANDS:
                found = [r for r in paired if band_of(r.games) == label]
                rows.append(paired_row(label, found, spreads(found) if found else {}))
            table("Error by games played at the checkpoint", PAIRED_HEADERS, rows)

        if args.categories:
            print("\n## 5. Which counts each prior gets wrong (paired, pooled)")
            per_count = per_category(paired, spread, PRODUCT)
            rows = [
                [
                    key,
                    f2(per_count[key]["espn"]),
                    f2(per_count[key]["bbm"]),
                    f2(per_count[key]["espn"] - per_count[key]["bbm"]),
                ]
                for key in per_count
            ]
            table(
                "Mean absolute error per count, in spreads (positive = BBM better)",
                ("count", "ESPN", "BBM", "ESPN - BBM"),
                rows,
            )

        if args.currency:
            print("\n## 6. In the recommender's currency: categories a week")
            rows = [
                category_row(
                    str(day),
                    [r for r in by_checkpoint[day] if r.paired],
                    data,
                    (lens.average, lens.distributions),
                )
                for day in args.checkpoints
            ]
            rows.append(category_row("pooled", paired, data, (lens.average, lens.distributions)))
            table(
                "Mean absolute error in categories a week of the rest-of-season line",
                CATEGORY_HEADERS,
                rows,
            )

        if args.sweep:
            print("\n## 7. The weights, held out of the comparison")
            rows = []
            for weights in WEIGHT_GRID:
                espn = mean(
                    [
                        scaled_error(
                            blended(
                                r.to_date,
                                r.recent,
                                r.espn,
                                r.games,
                                weights,
                                recent_games=r.recent_games,
                            ),
                            r.actual,
                            spread,
                        )
                        for r in paired
                    ]
                )
                bbm_error = mean(
                    [
                        scaled_error(
                            blended(
                                r.to_date,
                                r.recent,
                                r.bbm,
                                r.games,
                                weights,
                                recent_games=r.recent_games,
                            ),
                            r.actual,
                            spread,
                        )
                        for r in paired
                    ]
                )
                rows.append(
                    [
                        f"{weights[0]}/{weights[1]:.2f}",
                        f2(espn),
                        f2(bbm_error),
                        f2(espn - bbm_error),
                    ]
                )
            table(
                "Pooled error by weight pair (games weight / recent weight)",
                ("weights", "ESPN", "BBM", "ESPN - BBM"),
                rows,
            )

    print(f"\n# wall time {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
