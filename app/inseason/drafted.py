"""Has this season been drafted? The one question every projection asks first.

Before its draft a season has no rosters, whatever the store holds. ESPN's
roster feed for a season before its draft shows every team holding last
season's final roster, projected across every future day, and the ingest of
2026-09-23 stored all of it for 2027 as if it were a roster: 23,892
`daily_lineup_slots` rows, fourteen teams, scoring periods 1 to 132. Every
read-side gate asked only whether a lineup row existed, so the site
projected a week nobody had a roster for ("Expected to take 4.90 of 9
categories ... projected to end the season 93.2-68.8"). This module is the
gate those readers were missing.

THE RULE

A season is **not drafted** when either:

1. **its draft date is still ahead.** `league_seasons.drafted_at` is ESPN's
   own scheduled draft (`app.ingest.draft_is_pending` with a date), read by
   the settings pass and refreshed by the status pass while it is pending
   (docs/intake.md, "The draft date"). A date in the future settles it,
   whatever else is stored: nothing held before the draft is a roster.
2. **its draft date is unknown and nothing proves a draft happened.** No date
   stored is what ESPN answers before a draft is scheduled -- and also what
   the store holds for every season ingested before the column was read
   (2019-2025 here). So an unknown date is not enough on its own: a draft is
   proven by any stored **draft pick**, any stored **transaction**, or any
   stored **matchup statistic** (a game was scored), and a season with none
   of the three is not drafted. That is the ghost-roster signature -- lineup
   rows and nothing else -- and it covers a league whose `drafted_at` was
   never read at all.

A season whose draft date is in the past **is drafted**, whether or not its
picks were stored (older seasons may lack them).

The stored lineup rows are deliberately not evidence either way: they are
exactly what a ghost roster looks like.

The answer carries one sentence for a manager, dated in the league's zone:
the NBA's, Eastern time, as the injury reports and the schedule are. A fact
and a date, never a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DraftPick, LeagueSeason, MatchupTeamStat, Team, Transaction
from app.ingest import draft_is_pending
from app.injury_reports import ET

#: The two sentences a season before its draft is described by. `{draft}` is
#: "auction" or "draft", `{when}` the day and time in Eastern time.
SCHEDULED = "The {draft} is {when}; there are no rosters to project until then."
UNSCHEDULED = "The draft has not been scheduled; there are no rosters to project until it is held."
#: And the two a drafted season is described by, for a caller that prints it.
HELD = "The {draft} was held {when}."
PROVEN = "The draft has been held: its picks, moves or scores are stored."


@dataclass(frozen=True)
class Drafted:
    """Whether a season has been drafted, why, and when its draft is or was."""

    drafted: bool
    reason: str
    drafted_at: datetime | None


def when(at: datetime) -> str:
    """A moment as a manager reads it: "Sat, Oct 10 at 2:00 PM ET"."""
    local = at.astimezone(ET)
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return f"{local:%a, %b} {local.day} at {hour}:{local:%M} {meridiem} ET"


def _draft_word(league_season: LeagueSeason) -> str:
    return "auction" if str(league_season.draft_type or "").upper() == "AUCTION" else "draft"


def _proven(session: Session, league_season: LeagueSeason) -> bool:
    """Whether anything stored proves this season's draft was held."""
    picks = select(DraftPick.id).where(DraftPick.league_season_id == league_season.id)
    moves = select(Transaction.id).where(Transaction.league_season_id == league_season.id)
    scores = (
        select(MatchupTeamStat.id)
        .join(Team, Team.id == MatchupTeamStat.team_id)
        .where(Team.league_season_id == league_season.id)
    )
    return any(session.scalar(query.limit(1)) is not None for query in (picks, moves, scores))


def season_is_drafted(
    session: Session, league_season: LeagueSeason, now: datetime | None = None
) -> Drafted:
    """Whether `league_season` has been drafted, by the rule in the module
    docstring, as of `now` (the real clock when it is left out)."""
    moment = now or datetime.now(UTC)
    at = league_season.drafted_at
    draft = _draft_word(league_season)
    if at is not None:
        if draft_is_pending(at, moment):
            return Drafted(False, SCHEDULED.format(draft=draft, when=when(at)), at)
        return Drafted(True, HELD.format(draft=draft, when=when(at)), at)
    if _proven(session, league_season):
        return Drafted(True, PROVEN, None)
    return Drafted(False, UNSCHEDULED, None)
