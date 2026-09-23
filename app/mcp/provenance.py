"""Where every number in an answer came from, attached to the answer.

The pages print a bar's note under the bar and a projection's `source_note`
under the projection, because a bar labels and never hides (docs/intake.md).
A co-manager has no page to print under, so the same facts travel in the
payload instead, in one block on every tool result:

    provenance:
      calibration:   per key -- value, source, n, measured_at, note
      projection:    the source line the pages carry
      injuries:      when the injury report this answer read was published
      as_of:         the scoring period, its date, and whether the report
                     was the morning's stored one or was built for this call

`source` is the accessor's own: `owner` (this league's manager chose it),
`measured` (its own history), `pooled` (leagues shaped like it) or `default`
(measured on another league). The skill holds the model to quoting `n`
whenever it quotes the number, which is the only way a reader can tell a
measurement of his league from a stand-in for one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import calibration
from app.db.models import InjuryReport, LeagueSeason
from app.injuries import morning_of
from app.injury_reports import NBA_OFFICIAL
from app.pickups.state import SeasonCalendar, season_calendar
from app.projections.sources import ESPN, describe

#: What the injury line says when the league published nothing this answer
#: could read. Not "he is fit": the league said nothing (docs/injuries.md).
NO_REPORT = (
    "no NBA injury report is stored for this day; a status beside a name is "
    "ESPN's own, as the ingest or the listener last stored it"
)

#: What the keys mean, so a model reading them has no reason to guess. One
#: line each, in the words the account page uses.
MEANS = {
    calibration.TYPICAL_PICKUP: "what one executed waiver add returns, categories a week",
    calibration.OPENED_PLACE: "what a roster place left open and streamed returns, a week",
    calibration.STREAM_HURDLE: "the bar a move this week has to clear to be worth a look",
    calibration.SEASON_HURDLE_PAID: "the bar a rest-of-season move that costs FAAB clears",
    calibration.SEASON_HURDLE_FREE: "the bar a free add into an open place clears",
    calibration.TRADE_RECORD: "the published record of the trade number itself",
}


def one(found: calibration.Calibrated) -> dict[str, Any]:
    """One calibrated number as a tool hands it back: the accessor's output."""
    return {
        "key": found.key,
        "means": MEANS.get(found.key, ""),
        "value": found.value,
        "source": found.source,
        "n": found.n,
        "unit": calibration.unit(found.key),
        "measured_at": found.measured_at.isoformat() if found.measured_at else None,
        "note": found.note,
    }


def numbers(session: Session, league_season: LeagueSeason, keys: tuple[str, ...]) -> dict[str, Any]:
    """The keys this answer leans on, resolved for this league."""
    league_id = int(league_season.league_id)
    return {key: one(calibration.calibration(session, league_id, key)) for key in keys}


def projection_note(league_season: LeagueSeason) -> str:
    """The line the pages carry under a projection, written the same way."""
    return describe(ESPN, f"{int(league_season.season)} season, from the stored box scores")


def injuries_as_of(session: Session, on: date | None) -> dict[str, Any]:
    """When the newest injury report this answer could see was published."""
    if on is None:
        return {"source": NBA_OFFICIAL, "reported_at": None, "note": NO_REPORT}
    at = morning_of(on)
    newest = session.scalar(
        select(func.max(InjuryReport.reported_at)).where(
            InjuryReport.source == NBA_OFFICIAL,
            InjuryReport.reported_at <= at,
            InjuryReport.game_date >= on,
            InjuryReport.status.is_not(None),
        )
    )
    return {
        "source": NBA_OFFICIAL,
        "reported_at": newest.isoformat() if newest is not None else None,
        "read_as_of": at.isoformat(),
        "note": NO_REPORT if newest is None else "the league's own report, as of that moment",
    }


def day_date(session: Session, league_season: LeagueSeason, day: int | None) -> date | None:
    calendar: SeasonCalendar | None = season_calendar(session, int(league_season.season))
    if calendar is None or day is None:
        return None
    return calendar.date_of(day)


def block(
    session: Session,
    league_season: LeagueSeason,
    *,
    keys: tuple[str, ...] = (),
    day: int | None = None,
    stored: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole block, for one answer about one league season.

    Every tool returns one, including the ones that lean on no calibrated
    number at all: a reader should never have to work out whether the block
    is missing because nothing was calibrated or because somebody forgot.
    """
    on = day_date(session, league_season, day)
    out: dict[str, Any] = {
        "league_id": int(league_season.league.espn_league_id),
        "season": int(league_season.season),
        "as_of": {
            "scoring_period": day,
            "date": on.isoformat() if on is not None else None,
            "stored_report": stored,
        },
        "calibration": numbers(session, league_season, keys),
        "projection": {"source_note": projection_note(league_season)},
        "injuries": injuries_as_of(session, on),
        "read_only": "nothing here can add, drop, bid or accept anything on ESPN",
    }
    if extra:
        out |= extra
    return out
