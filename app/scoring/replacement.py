"""Replacement level: what an open roster spot is worth.

A trade that sends two players for one, or a drop, leaves a spot that the
manager fills from the wire. Scoring that spot as worth nothing punishes
every uneven trade by construction, so the spec prices it at a typical
waiver pickup, measured from what this league's managers actually picked up.

TWO NUMBERS, NOT ONE

A place a man is *added to* and a place a move *empties* are not the same
thing, and this module holds both numbers because both are measurements of
the wire.

`TYPICAL_PICKUP` (0.06) is what one executed add returns: the median of the
table below, and the floor under what a man is worth in a place he holds.

`OPENED_PLACE` (0.38) is what a place returns when it is left open and
streamed -- a live body in it every day rather than one man kept. It is
`docs/streaming_lane.md`'s pooled per-place median over 1,536 team-periods,
2019-2026, against the held 13th man's 0.00, and it is 6.3 times the floor.
It is not a volume effect: a streamed place starts 4.67 games a week and an
ordinary held man 5.00, because a team starts at most ten men on a day. What
it buys is *chosen* games. Revision R2 (`docs/trades.md` section 7b) is the
decision to charge it wherever a move empties a place.

The caution the same measurement forces: an ordinary *held* place returns
0.43, so an opened place must never be priced above a man. `opened_places`
takes the better of the man the wire offers and the lane, and never their
sum.

THE MEASURE

For every executed add in the season (`transactions` of type WAIVER or
FREEAGENT, status EXECUTED, an ADD item), the added player's started lines
for the adding team over the `WINDOW` days after the move, in the scoring
currency: categories a week his line added to that team's week
(`app.scoring.value.marginal`), averaged over the matchup periods the window
touches. A pickup who never starts is worth zero, which is what an unused
pickup is worth. The typical value is the median, since a few breakout
pickups drag the mean.

Gross, not net of the player dropped: the question is what filling a spot
gives, and the dropped player is accounted for wherever he was graded.

Measured 2026-09-16 on the live database, categories a week:

| season | median | mean | quartiles | adds |
|---|---|---|---|---|
| 2019 | 0.113 | 0.176 | 0.022-0.280 | 599 |
| 2020 | 0.128 | 0.186 | 0.034-0.298 | 543 |
| 2021 | 0.096 | 0.160 | 0.032-0.239 | 758 |
| 2022 | 0.086 | 0.146 | 0.027-0.219 | 648 |
| 2023 | 0.091 | 0.157 | 0.028-0.242 | 475 |
| 2024 | 0.068 | 0.137 | 0.017-0.207 | 789 |
| 2025 | 0.062 | 0.124 | 0.012-0.173 | 924 |
| 2026 | 0.072 | 0.135 | 0.013-0.195 | 973 |

For scale, Kawhi Leonard added 0.77 a week to Through The Wire in 2026. The
value falls as adds rise: the more a league streams, the less each add is.

`WINDOW` is fourteen days, the window `scripts/acquirable_value.py` measured
pickups over: most adds are streamers held for days, and a longer window
mostly measures the rare keeper.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, median, quantiles

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Transaction, TransactionItem
from app.scoring.season import SeasonBook

#: Days after an add over which the pickup's started lines count.
WINDOW = 14

#: Transaction types that fill a roster spot from outside the league.
ADD_TYPES = ("WAIVER", "FREEAGENT")

#: Categories a week a typical waiver pickup has returned in this league, the
#: floor under what the wire gives back for a roster place a man *holds*.
#: Measured by the table above over every executed add of every season: the
#: median ran 0.062 (2025) to 0.128 (2020), and 0.072 in 2026. The lowest
#: recent season is taken rather than the mean, because a floor that is too
#: high would charge nothing for dropping an ordinary player. Re-exported by
#: `app.pickups.judge`, which is where the rest of the code reads it.
TYPICAL_PICKUP = 0.06

#: Categories a week a roster place returns when it is left OPEN and streamed,
#: rather than held by one man: `docs/streaming_lane.md`, pooled per-place
#: median 2019-2026, IQR 0.23-0.53, n = 1,536 team-periods, against the held
#: 13th man's 0.00. Revision R2, `docs/trades.md` section 7b.
OPENED_PLACE = 0.38

#: The conservative end of the same distribution, the lower quartile, recorded
#: beside the median because the lane's upside depends on manager attention no
#: code here models. Not used by default; kept so a caller that wants the
#: cautious figure does not have to re-read the document for it.
OPENED_PLACE_LOWER = 0.23


def opened_places(count: int, replacement: float, *, first: float = OPENED_PLACE) -> float:
    """What the `count` places a move leaves open are worth, categories a week.

    `replacement` is what the wire gives a place back with a man in it -- the
    best free agent (`app.pickups.judge.wire_replacement`) or the season's
    median pickup, depending on the caller. The first place opened is worth the
    better of that man and streaming the place (`first`, normally
    `OPENED_PLACE`); every further place is worth the better of that man and a
    single ordinary pickup.

    **Why the second place is not worth a second lane.** The add budget is what
    binds: seven adds a matchup period, shared across every lane a team runs,
    and 16.2% of team-periods spend the lot (`docs/streaming_lane.md` section
    6). The document looked for a decay in what a second lane *produces* and
    reported that there is none to read -- the later men are worth *more* than
    the first, because a manager who is streaming is streaming toward somebody
    he wants (section 5) -- and it declined to invent a coefficient. So the
    declared fallback is taken (`docs/trades.md` section 7b): the lane value
    for one place, and the ordinary per-add floor for every further place.
    That is conservative in the direction the measurement cannot see, and a
    2-for-1 -- the shape the trade calibration turns on -- opens exactly one
    place either way.

    `first` is a parameter so that the old settlement (a flat replacement level
    per opened place) can be asked for by name when the two are being measured
    against each other, which is what `scripts/trade_calibration.py` does.
    """
    if count <= 0:
        return 0.0
    return max(replacement, first) + max(replacement, TYPICAL_PICKUP) * (count - 1)


@dataclass(frozen=True)
class ReplacementValue:
    season: int
    #: Categories a week a typical pickup added: the median over adds.
    value: float
    mean: float
    #: Interquartile range of the per-add values.
    lower_quartile: float
    upper_quartile: float
    #: Adds measured.
    n: int


def pickup_values(book: SeasonBook) -> list[float]:
    """Categories a week each executed add returned in its first `WINDOW` days."""
    session = book.session
    adds = session.execute(
        select(Transaction.scoring_period, TransactionItem.to_team_id, TransactionItem.player_id)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .where(
            Transaction.league_season_id == book.league_season.id,
            Transaction.type.in_(ADD_TYPES),
            Transaction.status == "EXECUTED",
            TransactionItem.item_type == "ADD",
            TransactionItem.to_team_id.is_not(None),
        )
    ).all()

    values = []
    for day, team_id, player_id in adds:
        touched = {
            period
            for offset in range(1, WINDOW + 1)
            if (period := book.period_for_day(int(day) + offset)) is not None
            and not book.is_playoff(period)
        }
        if not touched:
            continue
        values.append(
            fmean(book.value(int(team_id), period, int(player_id)) for period in sorted(touched))
        )
    return values


def replacement_value(session: Session, season: int) -> ReplacementValue:
    """The value of an open roster spot in `season`, in categories a week."""
    values = pickup_values(SeasonBook.load(session, season))
    if not values:
        raise ValueError(f"no executed adds with started lines in {season}")
    lower, _, upper = quantiles(values, n=4) if len(values) > 1 else (values[0],) * 3
    return ReplacementValue(
        season=season,
        value=median(values),
        mean=fmean(values),
        lower_quartile=lower,
        upper_quartile=upper,
        n=len(values),
    )
