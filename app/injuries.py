"""What was known about a player's availability at a moment. Pure reads.

The point of `injury_reports` (docs/injuries.md) is that it can be asked
about the past without looking ahead, so every read here is bounded by a
moment and nothing in this module can see past it.

THE RULE

`status_as_of(session, player_id, at)` is the latest report line for that
player whose `reported_at` is at or before `at` **and** whose `game_date` is
`at`'s own date or later.

Both halves matter and the second is the one that is easy to get wrong. A
report line is a statement about one game. A man listed Out for Tuesday's
game says nothing about Wednesday: he may be back, and the league simply has
not published Wednesday's line yet. Without the `game_date` bound the
newest row for a player who has since been left off every report would go on
reading "Out" for the rest of the season. With it, a stale line expires on
its own the morning after the game it was about, and the answer becomes
`None` -- which means "the league said nothing about him", not "he is fit".
A caller that wants "fit unless told otherwise" says so itself.

The reverse case is deliberate too: on a morning when a man's team plays
tonight, a line filed at 9am for tonight's game is visible, because its
`game_date` is today. A line filed later the same day is not, because its
`reported_at` is after the moment asked about.

THE MORNING RULE

Backtests judge a scoring period from what was knowable that morning, so
they all need the same moment. `morning_of(day)` is ten o'clock Eastern on
that date, and the hour is not arbitrary: the league's hourly reports were
published at half past the hour their URL named, so a read at nine would
have found only the eight o'clock report and would have missed the very
snapshot `--snapshots morning` stores. Ten is after the nine o'clock report
either way and still two hours before the earliest tip-off. Every caller
uses it rather than picking an hour of its own, so two backtests can be
compared.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.models import InjuryReport
from app.injury_reports import ET, MORNING_READ_HOUR, NBA_OFFICIAL

#: The statuses that mean he did not play, or very likely did not. `Out` is
#: certain; `Doubtful` is the league's own word for about a one-in-four
#: chance. The listener's kinds are keyed off this split (docs/injuries.md).
RULED_OUT = frozenset({"Out"})
#: In doubt, but not ruled out: the tilt's business, not the wire's.
IN_DOUBT = frozenset({"Doubtful", "Questionable"})
#: Reported, and expected to play.
EXPECTED = frozenset({"Probable", "Available"})


@dataclass(frozen=True)
class InjuryStatus:
    """One report line, as the accessor hands it back."""

    player_id: int
    #: When the league published it (UTC).
    reported_at: datetime
    #: The game it is about.
    game_date: date
    #: Out, Doubtful, Questionable, Probable or Available.
    status: str
    reason: str | None
    team: str
    player_name_raw: str

    @property
    def ruled_out(self) -> bool:
        return self.status in RULED_OUT

    @property
    def in_doubt(self) -> bool:
        return self.status in IN_DOUBT


def morning_of(day: date) -> datetime:
    """Ten o'clock Eastern on this date, in UTC: the backtests' moment."""
    return (
        datetime.combine(day, datetime.min.time(), tzinfo=ET)
        .replace(hour=MORNING_READ_HOUR)
        .astimezone(UTC)
    )


def _visible(at: datetime, source: str) -> list[Any]:
    """The point-in-time bound: published by `at`, about `at`'s day or later."""
    moment = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    return [
        InjuryReport.source == source,
        InjuryReport.reported_at <= moment,
        InjuryReport.game_date >= moment.astimezone(ET).date(),
        InjuryReport.status.is_not(None),
    ]


def _as_status(row: InjuryReport) -> InjuryStatus:
    return InjuryStatus(
        player_id=int(row.player_id or 0),
        reported_at=row.reported_at,
        game_date=row.game_date,
        status=str(row.status),
        reason=row.reason,
        team=row.team,
        player_name_raw=row.player_name_raw,
    )


def status_as_of(
    session: Session, player_id: int, at: datetime, *, source: str = NBA_OFFICIAL
) -> InjuryStatus | None:
    """The newest line about this player visible at `at`, or None.

    None means the league said nothing about him that was still current --
    not that he was fit.
    """
    row = session.scalar(
        select(InjuryReport)
        .where(InjuryReport.player_id == player_id, *_visible(at, source))
        .order_by(InjuryReport.reported_at.desc(), InjuryReport.game_date.asc())
        .limit(1)
    )
    return None if row is None else _as_status(row)


def statuses_as_of(
    session: Session,
    player_ids: Iterable[int],
    at: datetime,
    *,
    source: str = NBA_OFFICIAL,
) -> dict[int, InjuryStatus]:
    """The same, for a whole roster, in one query.

    Players the league said nothing about are absent from the result rather
    than present with None, so `in` reads as "was reported".
    """
    wanted = [int(player_id) for player_id in player_ids]
    if not wanted:
        return {}
    newest = (
        select(
            InjuryReport.player_id,
            func.max(InjuryReport.reported_at).label("reported_at"),
        )
        .where(InjuryReport.player_id.in_(wanted), *_visible(at, source))
        .group_by(InjuryReport.player_id)
        .subquery()
    )
    rows = session.scalars(
        select(InjuryReport)
        .join(
            newest,
            and_(
                InjuryReport.player_id == newest.c.player_id,
                InjuryReport.reported_at == newest.c.reported_at,
            ),
        )
        .where(*_visible(at, source))
        # Two lines at the same instant mean two games on the board; the
        # nearer game is the one the day is about.
        .order_by(InjuryReport.game_date.asc())
    ).all()
    out: dict[int, InjuryStatus] = {}
    for row in rows:
        out.setdefault(int(row.player_id or 0), _as_status(row))
    return out


@dataclass(frozen=True)
class Absence:
    """A run of consecutive days the league had this man Out."""

    first: date
    last: date

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def covers(self, day: date) -> bool:
        return self.first <= day <= self.last


#: A day's standing for one player: named and Out, named and not Out, or
#: not named at all -- and for the last, whether his team had filed.
OUT = "out"
NOT_OUT = "not_out"
#: Silent, and the league had said nothing about his team either: his team
#: was not playing, or had not filed yet. This says nothing, so it carries
#: an absence across rather than ending it.
UNSAID = "unsaid"


def _newest_visible(
    rows: Sequence[tuple[datetime, date, Any]], day: date
) -> tuple[datetime, date, Any] | None:
    """The newest of these lines visible on `day`'s morning, or None.

    The same bound as `status_as_of`: published by then, about that day or
    later, with the nearer game winning a tie on the instant.
    """
    at = morning_of(day)
    best: tuple[datetime, date, Any] | None = None
    for row in rows:
        reported_at, game_date, _payload = row
        if reported_at > at or game_date < day:
            continue
        if best is None or (reported_at, -(game_date - day).days) > (
            best[0],
            -(best[1] - day).days,
        ):
            best = row
    return best


def absences(
    session: Session, player_id: int, season: int, *, source: str = NBA_OFFICIAL
) -> list[Absence]:
    """The runs of days this player was Out, for the beneficiary work.

    A day is an Out day when the morning's visible line (the same rule
    `status_as_of` uses, read at `morning_of`) has him Out. The interesting
    part is what happens on a day with no line for him at all, because the
    league's silence means two different things:

    * **his team had filed and did not name him** -- he is fit, and the run
      ends. A beneficiary's minutes should stop being credited to an absence
      the moment the league stops naming the absent man;
    * **the league had said nothing about his team either** -- his team was
      not playing that day, or had not filed by the morning. That is not
      evidence of anything, so the run carries across it.

    Both happen constantly and telling them apart is what makes the answer
    usable. Brandon Miller's shoulder in 2025-26 is the worked example: with
    the second case treated as a return he comes back as eleven separate
    one-day absences, because Charlotte played every other day and twice
    filed nothing before ten; read properly it is one run of twenty-three
    days and a single later night.

    A run begins and ends on an Out day, so a trailing silence never extends
    one. A season runs from October of the year before to September of the
    season year, which is how the rest of the code numbers them.
    """
    first_day = date(season - 1, 10, 1)
    last_day = date(season, 9, 30)
    window = (
        InjuryReport.source == source,
        InjuryReport.status.is_not(None),
        InjuryReport.game_date >= first_day,
        InjuryReport.game_date <= last_day,
    )
    his = [
        (reported_at, game_date, (str(status), str(team)))
        for reported_at, game_date, status, team in session.execute(
            select(
                InjuryReport.reported_at,
                InjuryReport.game_date,
                InjuryReport.status,
                InjuryReport.team,
            ).where(InjuryReport.player_id == player_id, *window)
        ).all()
    ]
    if not his:
        return []

    # One row per (snapshot, team) is enough to know a team had filed.
    teams = {team for _at, _game, (_status, team) in his}
    filed: dict[str, list[tuple[datetime, date, Any]]] = {team: [] for team in teams}
    for reported_at, game_date, team in session.execute(
        select(InjuryReport.reported_at, InjuryReport.game_date, InjuryReport.team)
        .where(InjuryReport.team.in_(teams), *window)
        .distinct()
    ).all():
        filed[str(team)].append((reported_at, game_date, None))

    days = sorted({game_date for _at, game_date, _payload in his})
    standing: list[tuple[date, str]] = []
    for day in _every_day(days[0], days[-1]):
        mine = _newest_visible(his, day)
        if mine is not None:
            standing.append((day, OUT if mine[2][0] in RULED_OUT else NOT_OUT))
            continue
        team = _his_team_on(his, day)
        said = team is not None and _newest_visible(filed[team], day) is not None
        standing.append((day, NOT_OUT if said else UNSAID))
    return _runs(standing)


def _every_day(first: date, last: date) -> Iterator[date]:
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _his_team_on(rows: Sequence[tuple[datetime, date, Any]], day: date) -> str | None:
    """The team his newest line up to this day put him on, for a trade."""
    at = morning_of(day)
    best: tuple[datetime, str] | None = None
    for reported_at, _game_date, (_status, team) in rows:
        if reported_at <= at and (best is None or reported_at > best[0]):
            best = (reported_at, str(team))
    return best[1] if best is not None else None


def _runs(standing: Sequence[tuple[date, str]]) -> list[Absence]:
    """Maximal runs that open and close on an Out day, carried over silence."""
    out: list[Absence] = []
    open_from: date | None = None
    last_out: date | None = None
    for day, kind in standing:
        if kind == OUT:
            if open_from is None:
                open_from = day
            last_out = day
        elif kind == NOT_OUT:
            if open_from is not None and last_out is not None:
                out.append(Absence(first=open_from, last=last_out))
            open_from = last_out = None
    if open_from is not None and last_out is not None:
        out.append(Absence(first=open_from, last=last_out))
    return out


def out_mornings(
    session: Session, player_id: int, season: int, *, source: str = NBA_OFFICIAL
) -> int:
    """How many mornings of a season the league had this man Out."""
    return sum(run.days for run in absences(session, player_id, season, source=source))
