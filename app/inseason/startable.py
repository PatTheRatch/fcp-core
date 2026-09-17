"""How many starts a roster can actually fill over the days it has left.

The question the streaming recommender exists to answer, and the one a season
total cannot: over the remaining days of a matchup period, how many of the
starting slots can this roster put a player with a game into, and what would
one more player buy?

The naive answer is to count games, and it is wrong in both directions. A
player with four games is worth nothing on a day the lineup is already full:
five guards and one centre against a day where only guards play fills three
of the ten slots, and the fourth guard on the roster adds no start at all. So
the per-day question is a maximum bipartite matching of the players who have
a game to the slots they may occupy, which is exactly what the draft's
`can_field` computes as a yes/no; `app.draft.lineup.max_matching` is the same
algorithm, sized. This module is the sizing, per day, plus the swaps.

**Why the day, and not the week.** ESPN locks each day separately, so a start
is a slot on a day. A player cannot appear twice in one day's lineup, and no
amount of scheduling moves Tuesday's empty centre slot to Wednesday's three
available centres. Only the days the roster is short matter, and one good
pickup on the busiest remaining day is worth more than a marginal upgrade on
a day the lineup is already full.

**Ownership now, not then.** `daily_lineup_slots` is the record of who a team
held on a past day, and the only honest source for a day already played. For
today and everything after it, no such row exists, so the roster is read from
the listener's latest status snapshots (`on_team_id`), which is who ESPN says
holds each player at the last pass. The two are not mixed here: `roster_week`
is a forward-looking function, and every day it reports on is at or after the
day the snapshots were taken. A backtest over 2026 cannot use it as it stands
and would need the historical branch, which is why the caller passes the
window in rather than this module working out "today" for itself.

**Status.** A player whose latest snapshot says OUT is skipped: ESPN has
already ruled him out for a game, so counting him would promise starts that
cannot happen. `SUSPENSION` is treated the same way, for the same reason.
QUESTIONABLE, DAY_TO_DAY, DOUBTFUL and PROBABLE are all counted, and that is
a deliberate choice with a cost. ESPN's questionable status is *vague* -- it
is the routine "we will decide at tip-off" tag, and the great majority of
players carrying it do play -- while the cost of the opposite rule is worse
in this specific tool. A recommender that dropped every questionable player
would systematically overstate the empty slots on a real roster, and would
talk a manager into spending FAAB to cover a slot that a doubtful starter
fills anyway. Counting them keeps the number a floor on what the roster can
start with one flag visible per player, and the digest already reports status
changes; the caller can subtract a player itself to see the pessimistic case.
The line is drawn at OUT because that is the only status ESPN uses to mean
"this game is not happening for him".
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    LeagueSeason,
    MatchupPeriod,
    Player,
    PlayerSeasonStat,
    ProTeamGame,
    Team,
)
from app.draft.lineup import max_matching, uncovered_slots
from app.draft.pool import lineup_for
from app.listener.snapshots import latest_snapshots

#: Statuses that mean ESPN has already ruled the player out of a game, so no
#: start can be counted on him. OUT is the one the listener's own event rules
#: use for "went out"; SUSPENSION is the same promise with a different cause.
#: Everything else -- QUESTIONABLE, DAY_TO_DAY, DOUBTFUL, PROBABLE, ACTIVE --
#: is counted; see the module docstring for why the line sits here.
RULED_OUT_STATUSES = frozenset({"OUT", "SUSPENSION"})

#: ESPN's team id for "no NBA team": an unsigned free agent, or a two-way
#: player between assignments. It is 0 in the data rather than null, and it
#: matches no row in `pro_team_games`, so such a player simply never has a
#: game. Named so the filters that carry it say what they mean.
NO_PRO_TEAM = 0


@dataclass(frozen=True)
class Startable:
    """One player who has a game on one day, and the slots he could take."""

    player_id: int
    name: str
    pro_team_id: int
    slots: frozenset[str]


@dataclass(frozen=True)
class DaySlots:
    """One day of a roster's week: who can play, and how much of the lineup.

    `filled` is the maximum matching, not a count of players: `empty` is the
    slots left over afterwards, and `wasted` is the starts that could not be
    used because the lineup was already full.
    """

    scoring_period: int
    game_date: date | None
    #: Players with a game this day, sorted by player id.
    available: tuple[Startable, ...]
    #: Slots the matching covers, out of `lineup_size`.
    filled: int
    lineup_size: int
    #: Slot names no available player can fill, alphabetical.
    empty: tuple[str, ...]

    @property
    def wasted(self) -> int:
        """Games this day that cannot become starts, because the lineup is full.

        The players left over after the matching, not a count of the surplus
        of one position: the matching uses everyone it can, so what remains
        is exactly the games with nowhere to go.
        """
        return max(0, len(self.available) - self.filled)

    @property
    def full(self) -> bool:
        return self.filled >= self.lineup_size


@dataclass(frozen=True)
class WeekPlan:
    """A roster's remaining days, the starts it can fill, and what is missing.

    `starts` is the number the recommender maximises: the sum of the daily
    matchings. It is not the same as games played by the roster and not the
    same as players available, and the difference between those three is the
    whole point of the module.
    """

    team_id: int
    first_day: int
    last_day: int
    lineup: tuple[str, ...]
    days: tuple[DaySlots, ...]

    @property
    def starts(self) -> int:
        """Starts this roster can actually fill over the window."""
        return sum(day.filled for day in self.days)

    @property
    def capacity(self) -> int:
        """Starts the window would hold if the roster were never short."""
        return len(self.lineup) * len(self.days)

    @property
    def wasted(self) -> int:
        """Games on the roster that cannot be turned into a start."""
        return sum(day.wasted for day in self.days)

    @property
    def empty_slots(self) -> int:
        """Slot-days no available player can fill, summed over the window."""
        return sum(len(day.empty) for day in self.days)

    def starts_by_player(self) -> dict[int, int]:
        """Days each available player is needed, for the CLI's report.

        Exact for the cases a manager asks about -- everyone plays, or most
        of the lineup does -- and never claims more than the matching filled.
        The assignment itself is not stable across two runs, so this counts
        days rather than naming slots: two lineups that use the same men are
        the same news.
        """
        counts: dict[int, int] = {}
        for day in self.days:
            for player in day.available[: day.filled]:
                counts[player.player_id] = counts.get(player.player_id, 0) + 1
        return counts

    def names(self) -> dict[int, str]:
        """Player id -> name, from every day of the window."""
        return {player.player_id: player.name for day in self.days for player in day.available}


def startable_starts(
    players: Mapping[int, Iterable[str]],
    games_by_day: Mapping[int, Iterable[int]],
    lineup: Sequence[str],
    limits: Mapping[str, int] | None = None,
) -> dict[int, DaySlots]:
    """The matching per day, for players who have a game on that day.

    `games_by_day` maps a scoring period to the player ids with a game then,
    and `players` maps every player id to the slots he may occupy. A player
    in `games_by_day` with no eligibility is bench-only and never started,
    which is what `max_matching` does with an empty slot set.

    `limits` is accepted for the caller's convenience -- a swap may have to
    respect position caps -- and deliberately not enforced here. This function
    answers "how many of these slots can these men fill at once", which is a
    fact about the day; whether the roster holding them is legal is a
    different question, asked by `within_position_limits` when a move is
    proposed. Enforcing it per day would report a legal lineup as short.
    """
    days: dict[int, DaySlots] = {}
    for scoring_period in sorted(games_by_day):
        available = [
            Startable(
                player_id=player_id,
                name="",
                pro_team_id=NO_PRO_TEAM,
                slots=frozenset(players.get(player_id, ())),
            )
            for player_id in sorted(games_by_day[scoring_period])
        ]
        days[scoring_period] = _day(scoring_period, available, lineup)
    return days


def _day(
    scoring_period: int,
    available: Sequence[Startable],
    lineup: Sequence[str],
    game_date: date | None = None,
) -> DaySlots:
    """One day's slot count, from the men available on it."""
    eligibilities = {player.player_id: player.slots for player in available}
    filled = max_matching(eligibilities, lineup)
    empty = tuple(sorted(uncovered_slots(eligibilities, lineup))) if filled < len(lineup) else ()
    return DaySlots(
        scoring_period=scoring_period,
        game_date=game_date,
        available=tuple(available),
        filled=filled,
        lineup_size=len(lineup),
        empty=empty,
    )


def roster_week(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    first_day: int,
    last_day: int,
) -> WeekPlan:
    """The roster's remaining days, from the stored schedule and snapshots.

    `team_id` is ESPN's team id, as everywhere else that names a team: the
    API and `.env` carry ESPN's id rather than `teams.id`, and a snapshot's
    `on_team_id` is ESPN's too.

    For each day in the inclusive window: the players on this roster, with a
    game that day (their NBA team's row in `pro_team_games` for that scoring
    period), not already ruled out by their latest status snapshot. Whoever
    is left is matched to the lineup, so `filled` is how many slots that
    covers, `empty` is what nobody could cover, and `wasted` is the games
    that could not be turned into a start.

    The roster comes from the latest snapshots, so this is a statement about
    now and forward. A day already played has no snapshots for it and has
    `daily_lineup_slots` instead; asking this function about a day in the
    past gives today's roster against that day's schedule, which is not a
    backtest. `first_day` and `last_day` are scoring periods, which is what
    `matchup_periods.first_scoring_period` and `final_scoring_period` hold.
    """
    return _week(session, league_season, team_id, first_day, last_day)


def swap_gain(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    first_day: int,
    last_day: int,
    *,
    add_player: int | None = None,
    drop_player: int | None = None,
) -> int:
    """Starts a swap would add over the window, positive or negative.

    The recommender's question, in starts: "what does this pickup actually
    buy me?" Positive means the added player fills slots the roster could not
    fill without him -- on the days he plays *and* the lineup is short, not on
    the days it is already full. Negative means the swap loses starts, which
    is the case a games count cannot see: dropping the only centre on the
    roster for a fourth guard costs the centre slot on every day that centre
    plays, and gains nothing on the days the guards already cover.

    Either side may be None: add-only is a move into an open roster place,
    drop-only frees one. Both None is zero by construction rather than an
    error, because the recommender asks that question too.

    A dropped player the roster does not hold is a no-op, not a silent bonus:
    the roster is rebuilt from the snapshots with the id removed, and an id
    that was not there changes nothing. Neither player is checked against
    position limits or roster size; the caller asks `within_position_limits`
    and `roster_size_for` for that, because a swap that is illegal is not a
    smaller gain, it is not a move.
    """
    if add_player is None and drop_player is None:
        return 0

    before = _week(session, league_season, team_id, first_day, last_day)
    after = _week(
        session,
        league_season,
        team_id,
        first_day,
        last_day,
        add=add_player,
        drop=drop_player,
    )
    return after.starts - before.starts


def _week(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    first_day: int,
    last_day: int,
    *,
    add: int | None = None,
    drop: int | None = None,
) -> WeekPlan:
    """Build the window's plan, optionally with one roster id swapped.

    One implementation for both questions. A swap is not a patch to the day
    counts: the added player's eligibility can free a slot that dropping the
    other closes, so the pair has to be re-matched together on every day.
    """
    lineup = lineup_for(league_season)
    season = league_season.season
    snapshots = latest_snapshots(session, season)

    on_roster = {
        player_id for player_id, snapshot in snapshots.items() if snapshot.on_team_id == team_id
    }
    if drop is not None:
        on_roster.discard(drop)
    if add is not None:
        on_roster.add(add)

    # A player the season has never snapshotted has no NBA team on record and
    # so no game; keeping him would put a nameless id in the table.
    ids = {
        player_id
        for player_id in on_roster
        if player_id in snapshots and _is_counted(snapshots[player_id].injury_status)
    }
    names = {
        player_id: str(name)
        for player_id, name in session.execute(
            select(Player.id, Player.name).where(Player.id.in_(sorted(ids)))
        ).all()
    }
    slots = _eligible_slots(session, season, sorted(ids))
    pro_teams = {int(snapshots[player_id].pro_team_id or NO_PRO_TEAM) for player_id in ids} - {
        NO_PRO_TEAM
    }
    games = _games_by_day(session, season, pro_teams, first_day, last_day)

    days: list[DaySlots] = []
    for scoring_period in range(first_day, last_day + 1):
        playing = games.get(scoring_period, {})
        available = [
            Startable(
                player_id=player_id,
                name=names.get(player_id, f"player {player_id}"),
                pro_team_id=int(snapshots[player_id].pro_team_id or NO_PRO_TEAM),
                slots=slots.get(player_id, frozenset()),
            )
            for player_id in sorted(ids)
            if int(snapshots[player_id].pro_team_id or NO_PRO_TEAM) in playing
        ]
        # The date is the first player's, which is the same day for all of
        # them: a scoring period is a day, whatever the tip-off times are.
        when = playing.get(available[0].pro_team_id) if available else None
        days.append(_day(scoring_period, available, lineup, game_date=when))

    return WeekPlan(
        team_id=team_id,
        first_day=first_day,
        last_day=last_day,
        lineup=lineup,
        days=tuple(days),
    )


def _is_counted(injury_status: str | None) -> bool:
    """Whether a status still lets a player be started. See the docstring."""
    return (injury_status or "").upper() not in RULED_OUT_STATUSES


def _games_by_day(
    session: Session,
    season: int,
    pro_teams: Iterable[int],
    first_day: int,
    last_day: int,
) -> dict[int, dict[int, date]]:
    """scoring period -> pro team id -> the day it plays, for these teams.

    One query for the whole window. A team plays at most once a day, which is
    the key on `pro_team_games`, so the inner map loses nothing.
    """
    wanted = sorted(set(pro_teams))
    if not wanted:
        return {}
    rows = session.execute(
        select(ProTeamGame.scoring_period, ProTeamGame.pro_team_id, ProTeamGame.game_at).where(
            ProTeamGame.season == season,
            ProTeamGame.pro_team_id.in_(wanted),
            ProTeamGame.scoring_period.between(first_day, last_day),
        )
    ).all()
    by_day: dict[int, dict[int, date]] = {}
    for scoring_period, pro_team_id, game_at in rows:
        by_day.setdefault(int(scoring_period), {})[int(pro_team_id)] = game_at.date()
    return by_day


def _eligible_slots(
    session: Session, season: int, player_ids: Sequence[int]
) -> dict[int, frozenset[str]]:
    """Each player's eligible slots this season, from his stored season line.

    `player_season_stats.eligible_slots` is per season and stored under both
    the "total" and "projected" kinds. Eligibility does not differ between
    them, so every row is read and a player with only one kind still answers,
    rather than preferring a kind and being wrong for him.
    """
    if not player_ids:
        return {}
    rows = session.execute(
        select(PlayerSeasonStat.player_id, PlayerSeasonStat.eligible_slots).where(
            PlayerSeasonStat.season == season,
            PlayerSeasonStat.player_id.in_(sorted(set(player_ids))),
        )
    ).all()
    slots: dict[int, frozenset[str]] = {}
    for player_id, eligible in rows:
        slots[int(player_id)] = frozenset(str(slot) for slot in (eligible or []))
    return slots


def matchup_window(
    session: Session, league_season: LeagueSeason, period: int
) -> tuple[int, int] | None:
    """The scoring periods one matchup period covers, or None.

    `matchup_periods.first_scoring_period` and `final_scoring_period` come
    from `League.matchup_ids`, which is the authority -- periods are not a
    fixed length in this league, so the window is looked up rather than
    computed. Period 1 covers days 1 to 6, period 2 days 7 to 13.
    """
    row = session.execute(
        select(MatchupPeriod.first_scoring_period, MatchupPeriod.final_scoring_period).where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.period == period,
        )
    ).one_or_none()
    if row is None or row[0] is None or row[1] is None:
        return None
    return int(row[0]), int(row[1])


def team_by_name(session: Session, league_season: LeagueSeason, name: str) -> Team | None:
    """The season's team called `name`, for the CLI's `--team` argument.

    Exact match first so a full name is never ambiguous, then a unique
    case-insensitive prefix, so "through the wire" and "Through" both land
    while "the" does not silently pick one of several. Returns None rather
    than raising: the caller can then print the roster of real names, which
    is more use than a traceback.
    """
    teams = list(
        session.scalars(select(Team).where(Team.league_season_id == league_season.id)).all()
    )
    for team in teams:
        if team.name == name:
            return team
    lowered = name.strip().lower()
    matches = [team for team in teams if team.name.lower().startswith(lowered)]
    return matches[0] if len(matches) == 1 else None


def team_names(session: Session, league_season: LeagueSeason) -> list[str]:
    """Every team name this season, sorted, for an error message."""
    return sorted(
        team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    )
