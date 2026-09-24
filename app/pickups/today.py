"""Who starts today, and who is on the bench with a game while a starter sits.

The morning question, and the most-used thing in a daily-lineup league. The
week's report (`app.pickups.stream`) already solves it -- every day of the
period is a matching of the men with a game to the lineup -- and then throws
the answer away, because what it reports is the week the seating adds up to.
This module is that one day, kept.

IT IS THE SAME SOLVER

`stream.seat` is the seating, unchanged: take the men with a game in order
of a per-game weight (`stream.weight`, counts over each category's spread)
and keep each one the matching can still seat. That is exact rather than
greedy, because the seatable sets form a transversal matroid. What is new is
only that the places are named: `app.draft.lineup.assign` is the matching
the draft has always counted, read back as "who sits where", so the grid a
manager copies into ESPN and the number the week report projects come from
one computation. A second seating would be a second thing to be wrong, and
the two pages would disagree about who starts.

NOTHING AFTER TODAY IS READ

The rehearsal found four look-ahead leaks in one pass (docs/inseason_rehearsal.md,
finding 1) and this feature must not add a fifth. So the roster is rebuilt
over the single day `(today,)` rather than over the days the period has left:
a man's `game_days` is then either `(today,)` or empty, his games remaining
is 0 or 1, and no row of the schedule after today can reach the answer at
all. That is a structural guarantee rather than a promise, which is why it is
worth the second pass over `build_players`. The posted totals, the FAAB and
the add budget on `TeamWeek` are read and not used: this report is about a
lineup, not a matchup, and the one number it compares is today's.

A TOOL, NOT GOSPEL

The lineup is a proposal with its reasons attached. Every man with a game
who is not in it says why -- no place he fits, or the men who beat him to
the ones he does, by name -- and the manager decides. The one thing stated
as a mistake is the one that is unarguable: a place in the lineup the team
has actually set today that will produce nothing, because the man in it has
no game or because it was left unset, while a man on its bench has a game
and fits that very place. That is a category given away for nothing, and it
is what `fix` names. `starts` against `actual_starts` says how many places
it comes to, so the list never has to be read as a count.

WHAT THE TEAM ACTUALLY HAS SET

`daily_lineup_slots` for `today` is the record of it. On a day the ingest has
not reached -- this morning, before the nightly pass -- there is no such row,
and the report says so (`actual_known` is False) rather than comparing
against an empty lineup as though the manager had set none. When the rows are
there, `actual` is that lineup and `edge` is what the proposal is worth over
it, in the currency the seating orders by.

AND WHAT HE ACTUALLY DID

`DayPlayer.box` is the same kind of thing one field further on: the man's
stored `player_game_stats` line for today, once the ingest has it, so a page
can print `34 min - 22 pts - 8 reb` beside his game mark instead of the mark
alone. It is read after every input to the seating is built and is fed to
nothing: the look-ahead guarantee above is about what decides the lineup,
and this decides nothing. Until the nightly pass reaches the day there is no
row and the field is None, which is what a page shows as the mark alone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from espn_api.basketball.constant import PRO_TEAM_MAP
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyLineupSlot, LeagueSeason, ProTeamGame
from app.draft.lineup import assign
from app.draft.pool import lineup_for
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.projection import per_game_line
from app.pickups.state import (
    GONE_SLOT,
    IR_SLOT,
    BoxScore,
    RosteredPlayer,
    box_scores,
    build_players,
    load_team_week,
    season_calendar,
    team_row,
)
from app.pickups.stream import Contender, seat, weight
from app.scoring.lines import EMPTY, CategoryLine

#: A player's standing today, in the three words a page has room for.
HEALTHY = "healthy"
INJURED = "injured"
ON_IR = "ir"

#: Statuses worth flagging beside a name. Only OUT and SUSPENSION stop a
#: start (`app.inseason.startable.RULED_OUT_STATUSES`, and the reasoning for
#: where that line sits is in its module docstring); the rest are a flag on a
#: man who is still seated, because ESPN's questionable is the routine
#: "we will decide at tip-off" tag and most of them play.
FLAGGED_STATUSES = frozenset({"OUT", "SUSPENSION", "DOUBTFUL", "QUESTIONABLE", "DAY_TO_DAY"})

#: Why a man with a game today is not in the proposed lineup.
NO_SLOT = "no_slot"
OUTRANKED = "outranked"

#: Lineup slot names that are not places anybody starts in.
_NOT_A_PLACE = frozenset({"BE", IR_SLOT, GONE_SLOT})


@dataclass(frozen=True)
class Game:
    """The game a player's NBA team plays today.

    The opponent's abbreviation travels with its id, because every reader of
    this -- a page, a phone message, a terminal -- wants "at MIL" and none of
    them should have to carry ESPN's team table to write it.
    """

    opponent_pro_team_id: int
    opponent: str
    home: bool
    #: Tip-off, as the stored schedule has it (UTC). None when the row this
    #: was built from carried no time; every reader that wants to print a
    #: clock has to put it in the reader's own zone, which is why the moment
    #: travels rather than a formatted string.
    at: datetime | None = None

    def describe(self) -> str:
        return f"{'vs' if self.home else 'at'} {self.opponent}"


@dataclass(frozen=True)
class DayPlayer:
    """One man the team holds, as today sees him."""

    player: RosteredPlayer
    #: His NBA team's game today, or None when it is not playing.
    game: Game | None
    #: His knowable line per game as of this morning, for the seating order.
    per_game: CategoryLine
    weight: float
    #: What he actually did today, once the ingest has stored it; None until
    #: then. Display only, and the same kind of thing as `actual` below: a
    #: stored fact about today, read after the day, never an input. Nothing
    #: in the seating, the weight or the projection touches it, which is what
    #: keeps the look-ahead guarantee in the module docstring intact.
    box: BoxScore | None = None

    @property
    def player_id(self) -> int:
        return self.player.player_id

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def plays(self) -> bool:
        """Whether he has a game today ESPN has not ruled him out of.

        `RosteredPlayer.game_days` is already the days he can play, so a man
        ruled OUT with no return date has no game here even when his team
        does; `game` is still carried, so a page can say who they play.
        """
        return self.player.games_remaining_this_period > 0

    @property
    def status(self) -> str:
        if self.player.on_ir:
            return ON_IR
        return INJURED if (self.player.injury_status or "").upper() in FLAGGED_STATUSES else HEALTHY


@dataclass(frozen=True)
class Seat:
    """One place in the lineup and the man in it, or nobody."""

    slot: str
    player: DayPlayer | None


@dataclass(frozen=True)
class Benched:
    """A man with a game today the lineup has no room for, and why."""

    player: DayPlayer
    #: `NO_SLOT` (he fits no starting place at all) or `OUTRANKED`.
    reason: str
    #: The men seated in the places he fits, best first. Empty for `NO_SLOT`.
    behind: tuple[DayPlayer, ...]


@dataclass(frozen=True)
class Misstart:
    """A place in the set lineup that will produce nothing tonight.

    Two ways to produce nothing, and they are the same mistake: a man in the
    place whose NBA team does not play (`seat.player` is he), and a place
    left unset at all (`seat.player` is None). Either is a category given
    away, and only when somebody on the bench has a game and fits that very
    place -- which is why the eligibility is checked. A centre on the bench
    is no answer to an empty point guard's evening, and a roster that is
    simply short somewhere has made no mistake.

    `instead` is every spare man who fits the place, best first, so the
    manager has the choice rather than one name. What the two lineups are
    actually worth is `TodayReport.starts` against `actual_starts`, and
    neither of those is a guess.
    """

    seat: Seat
    instead: tuple[DayPlayer, ...]


@dataclass(frozen=True)
class TodayReport:
    """Who starts today, against who is actually set to."""

    team_id: int
    #: The scoring period reported on, and the day it falls on. The field is
    #: not called `date`, because that name shadows the type beside it.
    today: int
    calendar_date: date | None
    matchup_period: int
    #: How many NBA teams play today. Zero is a real answer -- the All-Star
    #: break -- and the one a page has to say out loud.
    teams_playing: int
    #: The proposed lineup, one entry per place, in the league's slot order.
    #: `player` is None where nobody on the roster can fill it.
    lineup: tuple[Seat, ...]
    #: Men with a game today the proposal does not seat, best first.
    benched: tuple[Benched, ...]
    #: Men held who have no game today, and men on injured reserve.
    idle: tuple[DayPlayer, ...]
    injured_reserve: tuple[DayPlayer, ...]
    #: Whether `daily_lineup_slots` carries today's lineup yet.
    actual_known: bool
    #: What the team has set, when it is known; empty when it is not.
    actual: tuple[Seat, ...]
    #: Places in the set lineup that produce nothing tonight while the bench
    #: has a man who would. The thing to fix, and the only thing here stated
    #: as a mistake.
    fix: tuple[Misstart, ...]
    #: Raw counts the proposal and the set lineup project to add today.
    projected: CategoryLine
    actual_projected: CategoryLine
    #: What the proposal is worth over what is set, in the currency the
    #: seating orders by: each count over its category's weekly spread,
    #: turnovers against (`app.pickups.stream.weight`). Zero when the two
    #: lineups are the same, and when today's is not stored.
    edge: float

    @property
    def starters(self) -> tuple[DayPlayer, ...]:
        """The proposed lineup's men, in slot order."""
        return tuple(place.player for place in self.lineup if place.player is not None)

    @property
    def empty_slots(self) -> tuple[str, ...]:
        """Places nobody on the roster can fill today."""
        return tuple(place.slot for place in self.lineup if place.player is None)

    @property
    def starts(self) -> int:
        """Places the proposal fills, which is the most the roster can fill.

        The seating is exact (the module docstring), so this is a ceiling
        and not an opinion.
        """
        return len(self.starters)

    @property
    def actual_starts(self) -> int:
        """Places the set lineup fills with a man who is actually playing.

        The pair `starts` and `actual_starts` is the whole comparison in two
        numbers: seven against nine is two places given away tonight.
        """
        return sum(1 for place in self.actual if place.player is not None and place.player.plays)


def today_lineup(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    *,
    tilt: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
) -> TodayReport:
    """The day's lineup for ESPN team `team_id` on scoring period `today`.

    Raises `ValueError` when `today` falls in no matchup period of the
    season, which is `load_team_week`'s rule and for its reason: a day with
    no week around it has no roster to read.

    `distributions` stands in for the season's measured ones, for a test;
    `tilt` switches the minutes tilt, as both other reports do.
    """
    week = load_team_week(session, league_season, team_id, today)
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
    lineup = lineup_for(league_season)

    # Rebuilt over today alone, so no row of the schedule after today can
    # reach the answer (the module docstring).
    on_ir = frozenset(player.player_id for player in week.roster if player.on_ir)
    roster = build_players(
        session,
        league_season,
        [player.player_id for player in week.roster],
        (today,),
        on_ir=on_ir,
    )
    games = _games_today(session, season, (player.pro_team_id for player in roster), today)
    # Read after the seating's inputs and never fed to it: what a man did
    # tonight, for the page to print beside his game mark (see `DayPlayer.box`).
    stored = box_scores(session, season, (player.player_id for player in roster), today)

    def day_player(player: RosteredPlayer) -> DayPlayer:
        per_game = per_game_line(session, season, player.player_id, today, tilt=tilt, as_of=as_of)
        return DayPlayer(
            player=player,
            game=games.get(player.pro_team_id),
            per_game=per_game,
            weight=weight(per_game, distributions),
            box=stored.get(player.player_id),
        )

    held = {player.player_id: day_player(player) for player in roster}
    ranked = sorted(
        (found for found in held.values() if not found.player.on_ir and found.plays),
        key=lambda found: (-found.weight, found.player_id),
    )
    seated = seat([_contender(found) for found in ranked], lineup)
    places = assign({player_id: held[player_id].player.eligible for player_id in seated}, lineup)
    proposed = tuple(
        Seat(slot=slot, player=held.get(places[index]) if index in places else None)
        for index, slot in enumerate(lineup)
    )

    actual_known, actual_rows = _actual_slots(session, league_season, team_id, today)
    actual = tuple(
        Seat(slot=slot, player=held[player_id])
        for slot, player_id in _ordered(actual_rows, lineup)
        if player_id in held
    )
    benched = _benched(ranked, frozenset(seated), proposed, lineup)
    actual_starters = tuple(place.player for place in actual if place.player is not None)
    projected = _line(held.get(player_id) for player_id in seated)
    actual_projected = _line(actual_starters)
    return TodayReport(
        team_id=team_id,
        today=today,
        calendar_date=as_of,
        matchup_period=week.matchup_period,
        teams_playing=_teams_playing(session, season, today),
        lineup=proposed,
        benched=benched,
        idle=tuple(
            found
            for found in sorted(held.values(), key=lambda f: (-f.weight, f.player_id))
            if not found.player.on_ir and not found.plays
        ),
        injured_reserve=tuple(found for found in held.values() if found.player.on_ir),
        actual_known=actual_known,
        actual=actual,
        fix=_fix(actual, held, actual_starters, lineup) if actual_known else (),
        projected=projected,
        actual_projected=actual_projected,
        edge=(
            sum(held[player_id].weight for player_id in seated)
            - sum(found.weight for found in actual_starters if found.plays)
            if actual_known
            else 0.0
        ),
    )


def _contender(found: DayPlayer) -> Contender:
    """A `DayPlayer` as the week's seating takes him.

    The seating is `app.pickups.stream`'s and reads its own `Contender`, so
    the day's own view is converted rather than the seating being taught a
    second shape.
    """
    return Contender(
        player=found.player,
        per_game=found.per_game,
        weight=found.weight,
        days=frozenset(found.player.game_days),
    )


def _line(players: Iterable[DayPlayer | None]) -> CategoryLine:
    """What a set of men projects to add today: one game each, or none."""
    total = EMPTY
    for found in players:
        if found is not None and found.plays:
            total = total + found.per_game
    return total


def _benched(
    ranked: Sequence[DayPlayer],
    seated: frozenset[int],
    proposed: Sequence[Seat],
    lineup: Sequence[str],
) -> tuple[Benched, ...]:
    """Every man with a game the lineup left out, and why he is out.

    Two reasons, and they are different news. A man eligible for no starting
    place at all is bench-only today whatever the roster looks like. Anyone
    else lost his places to better men, and the men who have them are named:
    the seating takes players in weight order, so a man is left out exactly
    when every place he fits is already taken by somebody above him.
    """
    places = frozenset(lineup) - _NOT_A_PLACE
    out: list[Benched] = []
    for found in ranked:
        if found.player_id in seated:
            continue
        fits = found.player.eligible & places
        if not fits:
            out.append(Benched(player=found, reason=NO_SLOT, behind=()))
            continue
        behind = tuple(
            sorted(
                {
                    place.player.player_id: place.player
                    for place in proposed
                    if place.player is not None and place.slot in fits
                }.values(),
                key=lambda other: (-other.weight, other.player_id),
            )
        )
        out.append(Benched(player=found, reason=OUTRANKED, behind=behind))
    return tuple(out)


def _fix(
    actual: Sequence[Seat],
    held: Mapping[int, DayPlayer],
    starters: Sequence[DayPlayer],
    lineup: Sequence[str],
) -> tuple[Misstart, ...]:
    """Places the set lineup gives away, and the bench men who would fill them.

    Only ever about the lineup the team has actually set: the proposal never
    seats a man without a game and never leaves a place open it could fill,
    so this can never become a complaint about itself.

    A place set with a man whose team is not playing comes first, in the set
    lineup's own order, then the places the lineup leaves unset -- the
    league's places less the ones the team used, which is a multiset
    difference because three utility places are three requirements.

    Only the places the bench can fill **at once** are named, which is the
    same matching again (`app.draft.lineup.assign` over the spare men and
    the places going begging). One man eligible for two open utility places
    is one place to fix and not two, and listing both would inflate a real
    mistake into a worse-sounding one.
    """
    started = {found.player_id for found in starters}
    spare = sorted(
        (
            found
            for found in held.values()
            if found.plays and not found.player.on_ir and found.player_id not in started
        ),
        key=lambda found: (-found.weight, found.player_id),
    )
    if not spare:
        return ()

    places = [place for place in actual if place.player is not None and not place.player.plays]
    places += [Seat(slot=slot, player=None) for slot in _unset(actual, lineup)]
    fillable = assign(
        {found.player_id: found.player.eligible for found in spare},
        [place.slot for place in places],
    )
    return tuple(
        Misstart(
            seat=places[index],
            instead=tuple(found for found in spare if places[index].slot in found.player.eligible),
        )
        for index in sorted(fillable)
    )


def _unset(actual: Sequence[Seat], lineup: Sequence[str]) -> list[str]:
    """The league's starting places the set lineup did not use, in its order."""
    used = Counter(place.slot for place in actual)
    out: list[str] = []
    for slot in lineup:
        if used[slot] > 0:
            used[slot] -= 1
        else:
            out.append(slot)
    return out


def _ordered(rows: Mapping[int, str], lineup: Sequence[str]) -> list[tuple[str, int]]:
    """The set lineup's places in the league's own slot order.

    ESPN names each place a man sits in, and a team may have a place the
    league's lineup does not (or two of a slot the lineup has three of), so
    the order is the league's where it can be and alphabetical after it
    rather than whatever the rows came back in.
    """
    order = {slot: index for index, slot in enumerate(lineup)}
    return sorted(
        ((slot, player_id) for player_id, slot in rows.items()),
        key=lambda pair: (order.get(pair[0], len(order)), pair[0], pair[1]),
    )


def _actual_slots(
    session: Session, league_season: LeagueSeason, team_id: int, today: int
) -> tuple[bool, dict[int, str]]:
    """Whether today's lineup is stored, and the starting places it set.

    The two are not the same question, and conflating them reads a real
    answer as a missing one: on a day nobody could be started -- the
    All-Star break -- the team's rows are thirteen benchings and no start,
    which is a lineup that is known and correct rather than a lineup that
    has not been ingested. So any row for the day means known, and the
    starting ones alone are the lineup; bench and injured reserve are left
    out of it, because who is not in the lineup is the rest of the report.
    """
    team = team_row(session, league_season, team_id)
    rows = session.execute(
        select(DailyLineupSlot.player_id, DailyLineupSlot.slot, DailyLineupSlot.started).where(
            DailyLineupSlot.team_id == team.id,
            DailyLineupSlot.scoring_period == today,
        )
    ).all()
    return bool(rows), {
        int(player_id): str(slot)
        for player_id, slot, started in rows
        if started and str(slot) not in _NOT_A_PLACE
    }


def _games_today(
    session: Session, season: int, pro_team_ids: Iterable[int], today: int
) -> dict[int, Game]:
    """Each NBA team's game on `today`, by team id. One day, one query."""
    wanted = sorted({int(team_id) for team_id in pro_team_ids if team_id})
    if not wanted:
        return {}
    rows = session.execute(
        select(
            ProTeamGame.pro_team_id,
            ProTeamGame.opponent_pro_team_id,
            ProTeamGame.home,
            ProTeamGame.game_at,
        ).where(
            ProTeamGame.season == season,
            ProTeamGame.scoring_period == today,
            ProTeamGame.pro_team_id.in_(wanted),
        )
    ).all()
    return {
        int(pro_team_id): Game(
            opponent_pro_team_id=int(opponent),
            opponent=str(PRO_TEAM_MAP.get(int(opponent), "?")),
            home=bool(home),
            at=game_at,
        )
        for pro_team_id, opponent, home, game_at in rows
    }


def _teams_playing(session: Session, season: int, today: int) -> int:
    """How many NBA teams play on `today`, so a page can say "no games"."""
    return len(
        session.scalars(
            select(ProTeamGame.pro_team_id).where(
                ProTeamGame.season == season, ProTeamGame.scoring_period == today
            )
        ).all()
    )
