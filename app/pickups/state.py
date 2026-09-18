"""What one team faces this week: its roster, the score so far, the days left.

Every recommender question starts from the same picture, so it is built once
here and handed on. `TeamWeek` is that picture for one team on one day of a
matchup period: who it holds and where, what both sides have posted so far,
how many days are left, and what room the roster has (an open place, a free
injured-reserve slot, FAAB).

WHOSE ROSTER, AND FROM WHERE

The roster is read from `daily_lineup_slots` on the latest day at or before
`today`, because that is the record of who the team held and where each man
sat (bench, injured reserve). Before the first lineup day of a season no such
row exists, so the roster falls back to the listener's latest status
snapshots (`on_team_id`), the same source `app.inseason.startable` reads; a
snapshot does not say who is on injured reserve, so nobody is, then.

Injury status, the return date and the NBA team are always the latest
snapshot's, never the lineup row's: `daily_lineup_slots.injury_status` is one
snapshot smeared across the season (`app/draft/availability.py`). A player
the listener has never seen falls back to the NBA team on his last weekly
roster row, which is an abbreviation ESPN's client maps to the id the
schedule is keyed on.

GAMES LEFT

A player's games this period are his NBA team's rows in `pro_team_games` on
the remaining days, less the days ESPN has already ruled him out of: every
day before `expected_return_date` when his status is OUT, and every day when
it is OUT with no date given. That last rule matches `startable`, which never
counts an OUT player at all; here he comes back on the day ESPN says he will.

FAAB

The in-season pot is `league_seasons.acquisition_budget` (100 every season),
not `auction_budget`, which is the draft's 200 and what the design note
named by mistake. What is spent is the sum of executed bids this season.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from espn_api.basketball.constant import PRO_TEAM_MAP
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    FreeAgentSnapshot,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Player,
    PlayerSeasonStat,
    PlayerStatusSnapshot,
    ProTeamGame,
    RosterSlot,
    Team,
    Transaction,
)
from app.draft.pool import roster_size_for
from app.inseason.startable import NO_PRO_TEAM, RULED_OUT_STATUSES
from app.listener.snapshots import latest_snapshots
from app.scoring.lines import COUNTS, CategoryLine

#: Lineup slot names that mean the player is held but not in the lineup.
#: FA is ESPN's marker for a player who left the roster during the period
#: and is not held at all.
IR_SLOT = "IR"
GONE_SLOT = "FA"

#: A transaction ESPN carried out, as `transactions.status` spells it.
EXECUTED = "EXECUTED"

#: ESPN's abbreviation for each NBA team id, inverted: the fallback from a
#: weekly roster row's `pro_team` to the id `pro_team_games` is keyed on.
_PRO_TEAM_IDS: Mapping[str, int] = {
    str(abbreviation): int(team_id) for team_id, abbreviation in PRO_TEAM_MAP.items()
}


@dataclass(frozen=True)
class RosteredPlayer:
    """One player as the week sees him: eligibility, status, and his game days.

    Built for a free agent too (`load_free_agents`), where `on_ir` is False;
    the shape is the same because a swap compares the two directly.
    """

    player_id: int
    name: str
    pro_team_id: int
    eligible: frozenset[str]
    position: str | None
    injury_status: str | None
    expected_return_date: date | None
    #: The remaining scoring periods on which he has a game he is not ruled
    #: out of, ascending.
    game_days: tuple[int, ...]
    on_ir: bool

    @property
    def games_remaining_this_period(self) -> int:
        return len(self.game_days)

    @property
    def ruled_out(self) -> bool:
        """Whether ESPN has him out of the next game. See `startable`."""
        return (self.injury_status or "").upper() in RULED_OUT_STATUSES


@dataclass(frozen=True)
class TeamWeek:
    """One team's matchup period as of `today`."""

    #: ESPN's team id, as the API, `.env` and every snapshot name a team.
    team_id: int
    matchup_period: int
    #: Today through the period's last day, ascending.
    scoring_periods_remaining: tuple[int, ...]
    #: ESPN's id of the other side, or None on a bye.
    opponent_team_id: int | None
    #: Raw counts posted so far, including FGM/FGA and FTM/FTA.
    my_totals: CategoryLine
    opp_totals: CategoryLine
    roster: tuple[RosteredPlayer, ...]
    faab_remaining: int
    #: Roster places not held, injured reserve aside.
    open_slots: int
    ir_slot_free: bool

    @property
    def today(self) -> int:
        return self.scoring_periods_remaining[0]

    @property
    def days_remaining(self) -> int:
        return len(self.scoring_periods_remaining)

    @property
    def active(self) -> tuple[RosteredPlayer, ...]:
        """The roster less injured reserve: the men who can be started."""
        return tuple(player for player in self.roster if not player.on_ir)

    @property
    def on_bye(self) -> bool:
        return self.opponent_team_id is None


@dataclass(frozen=True)
class SeasonCalendar:
    """Which calendar day a scoring period is, from the stored schedule.

    ESPN numbers every day of the season consecutively from opening night,
    games or not, so one anchor (the first scheduled day and its date) dates
    every period.
    """

    first_scoring_period: int
    first_date: date
    last_scoring_period: int

    def date_of(self, scoring_period: int) -> date:
        return self.first_date + timedelta(days=scoring_period - self.first_scoring_period)

    def scoring_period_on(self, on: date) -> int:
        """The scoring period of a calendar day, clamped to the schedule.

        Before opening night that is the first day, which is what a preseason
        run should look at; after the last game, the last.
        """
        offset = (on - self.first_date).days
        raw = self.first_scoring_period + offset
        return max(self.first_scoring_period, min(self.last_scoring_period, raw))


def season_calendar(session: Session, season: int) -> SeasonCalendar | None:
    """The season's calendar from `pro_team_games`, or None with no schedule."""
    row = session.execute(
        select(
            func.min(ProTeamGame.scoring_period),
            func.min(ProTeamGame.game_at),
            func.max(ProTeamGame.scoring_period),
        ).where(ProTeamGame.season == season)
    ).one()
    first, first_at, last = row
    if first is None or first_at is None or last is None:
        return None
    return SeasonCalendar(int(first), first_at.date(), int(last))


def period_for_day(session: Session, league_season: LeagueSeason, day: int) -> MatchupPeriod | None:
    """The matchup period whose window holds scoring period `day`, or None."""
    return session.scalar(
        select(MatchupPeriod).where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.first_scoring_period <= day,
            MatchupPeriod.final_scoring_period >= day,
        )
    )


def schedule(
    session: Session,
    season: int,
    pro_team_ids: Iterable[int],
    first_day: int,
    last_day: int,
) -> dict[int, dict[int, date]]:
    """pro team id -> scoring period -> the date it plays, over the window."""
    wanted = sorted({int(team_id) for team_id in pro_team_ids} - {NO_PRO_TEAM})
    if not wanted or first_day > last_day:
        return {}
    rows = session.execute(
        select(ProTeamGame.pro_team_id, ProTeamGame.scoring_period, ProTeamGame.game_at).where(
            ProTeamGame.season == season,
            ProTeamGame.pro_team_id.in_(wanted),
            ProTeamGame.scoring_period.between(first_day, last_day),
        )
    ).all()
    out: dict[int, dict[int, date]] = {}
    for pro_team_id, scoring_period, game_at in rows:
        out.setdefault(int(pro_team_id), {})[int(scoring_period)] = game_at.date()
    return out


def playable_days(
    games: Mapping[int, date],
    days: Iterable[int],
    *,
    injury_status: str | None,
    expected_return_date: date | None,
) -> tuple[int, ...]:
    """The days in `days` the player has a game and is not ruled out of.

    `games` is his NBA team's scoring period -> date. A status in
    `RULED_OUT_STATUSES` removes every day before `expected_return_date`,
    and every day at all when no date is given (see the module docstring).
    """
    out = (injury_status or "").upper() in RULED_OUT_STATUSES
    kept: list[int] = []
    for day in sorted(days):
        when = games.get(day)
        if when is None:
            continue
        if out and (expected_return_date is None or when < expected_return_date):
            continue
        kept.append(day)
    return tuple(kept)


def team_row(session: Session, league_season: LeagueSeason, team_id: int) -> Team:
    """The season's team with ESPN id `team_id`."""
    team = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if team is None:
        raise ValueError(f"no team {team_id} in season {league_season.season}")
    return team


def load_team_week(
    session: Session, league_season: LeagueSeason, team_id: int, today: int
) -> TeamWeek:
    """The week as it stands for ESPN team `team_id` on scoring period `today`.

    Raises when `today` falls in no matchup period of the season: there is
    no week to describe, and a caller guessing one would be wrong quietly.
    """
    period = period_for_day(session, league_season, today)
    if period is None or period.final_scoring_period is None:
        raise ValueError(
            f"scoring period {today} is in no matchup period of {league_season.season}"
        )
    remaining = tuple(range(today, int(period.final_scoring_period) + 1))
    team = team_row(session, league_season, team_id)

    matchup = session.scalar(
        select(Matchup).where(
            Matchup.matchup_period_id == period.id,
            (Matchup.home_team_id == team.id) | (Matchup.away_team_id == team.id),
        )
    )
    opponent: Team | None = None
    my_totals = CategoryLine()
    opp_totals = CategoryLine()
    if matchup is not None:
        other_id = matchup.away_team_id if matchup.home_team_id == team.id else matchup.home_team_id
        if other_id is not None:
            opponent = session.get(Team, other_id)
        my_totals = _posted(session, matchup.id, team.id)
        if opponent is not None:
            opp_totals = _posted(session, matchup.id, opponent.id)

    held, on_ir = _lineup_roster(session, team.id, today)
    if held is None:
        held = _snapshot_roster(session, int(league_season.season), team_id)
        on_ir = frozenset()
    roster = build_players(session, league_season, held, remaining, on_ir=on_ir)

    active = [player for player in roster if not player.on_ir]
    ir_used = len(roster) - len(active)
    return TeamWeek(
        team_id=team_id,
        matchup_period=int(period.period),
        scoring_periods_remaining=remaining,
        opponent_team_id=int(opponent.espn_team_id) if opponent is not None else None,
        my_totals=my_totals,
        opp_totals=opp_totals,
        roster=roster,
        faab_remaining=int(league_season.acquisition_budget) - _faab_spent(session, team),
        open_slots=max(0, roster_size_for(league_season) - len(active)),
        ir_slot_free=int(league_season.injured_reserve_slots or 0) > ir_used,
    )


def load_free_agents(
    session: Session,
    league_season: LeagueSeason,
    week: TeamWeek,
    *,
    player_ids: Iterable[int] | None = None,
    days: Sequence[int] | None = None,
) -> tuple[RosteredPlayer, ...]:
    """The wire, as the week sees it: the latest pass's free agents.

    `free_agent_snapshots` only ever holds unrostered players, so the pool
    is every row of the most recent pass, not the latest row per player (a
    player claimed since would otherwise still be on the wire). `player_ids`
    names the pool instead, for a test or a backtest. `days` counts each
    man's games over a window other than the rest of this period, which is
    what the rest-of-season report needs.
    """
    ids = set(player_ids) if player_ids is not None else _latest_pool(session, league_season)
    return build_players(
        session, league_season, ids, days if days is not None else week.scoring_periods_remaining
    )


def build_players(
    session: Session,
    league_season: LeagueSeason,
    player_ids: Iterable[int],
    days: Sequence[int],
    *,
    on_ir: frozenset[int] = frozenset(),
) -> tuple[RosteredPlayer, ...]:
    """`RosteredPlayer` for each id, sorted by id, from the tables named above."""
    ids = sorted(set(player_ids))
    if not ids:
        return ()
    season = int(league_season.season)
    names = {
        int(player_id): str(name)
        for player_id, name in session.execute(
            select(Player.id, Player.name).where(Player.id.in_(ids))
        ).all()
    }
    snapshots = {
        player_id: snapshot
        for player_id, snapshot in latest_snapshots(session, season).items()
        if player_id in names
    }
    fallback_teams = _roster_slot_teams(
        session, league_season, [player_id for player_id in ids if player_id not in snapshots]
    )
    slots, positions = _eligibility(session, season, ids)

    pro_teams = {
        player_id: _pro_team(snapshots.get(player_id), fallback_teams.get(player_id))
        for player_id in ids
    }
    games = schedule(session, season, pro_teams.values(), min(days), max(days)) if days else {}

    players: list[RosteredPlayer] = []
    for player_id in ids:
        snapshot = snapshots.get(player_id)
        status = snapshot.injury_status if snapshot is not None else None
        returns = snapshot.expected_return_date if snapshot is not None else None
        players.append(
            RosteredPlayer(
                player_id=player_id,
                name=names.get(player_id, f"player {player_id}"),
                pro_team_id=pro_teams[player_id],
                eligible=slots.get(player_id, frozenset()),
                position=positions.get(player_id),
                injury_status=status,
                expected_return_date=returns,
                game_days=playable_days(
                    games.get(pro_teams[player_id], {}),
                    days,
                    injury_status=status,
                    expected_return_date=returns,
                ),
                on_ir=player_id in on_ir,
            )
        )
    return tuple(players)


def _pro_team(snapshot: PlayerStatusSnapshot | None, fallback: str | None) -> int:
    if snapshot is not None and snapshot.pro_team_id is not None:
        return int(snapshot.pro_team_id)
    if fallback:
        return _PRO_TEAM_IDS.get(fallback.upper(), NO_PRO_TEAM)
    return NO_PRO_TEAM


def _lineup_roster(
    session: Session, team_row_id: int, today: int
) -> tuple[set[int] | None, frozenset[int]]:
    """Who the team held on its latest lineup day at or before `today`.

    None when the season has no lineup day yet, which is the preseason.
    """
    latest = session.scalar(
        select(func.max(DailyLineupSlot.scoring_period)).where(
            DailyLineupSlot.team_id == team_row_id,
            DailyLineupSlot.scoring_period <= today,
        )
    )
    if latest is None:
        return None, frozenset()
    rows = session.execute(
        select(DailyLineupSlot.player_id, DailyLineupSlot.slot).where(
            DailyLineupSlot.team_id == team_row_id,
            DailyLineupSlot.scoring_period == int(latest),
            DailyLineupSlot.slot != GONE_SLOT,
        )
    ).all()
    held = {int(player_id) for player_id, _ in rows}
    on_ir = frozenset(int(player_id) for player_id, slot in rows if slot == IR_SLOT)
    return held, on_ir


def _snapshot_roster(session: Session, season: int, team_id: int) -> set[int]:
    return {
        player_id
        for player_id, snapshot in latest_snapshots(session, season).items()
        if snapshot.on_team_id == team_id
    }


def _latest_pool(session: Session, league_season: LeagueSeason) -> set[int]:
    """Every player on the wire at the most recent pass."""
    latest = session.scalar(
        select(func.max(FreeAgentSnapshot.observed_at)).where(
            FreeAgentSnapshot.league_season_id == league_season.id
        )
    )
    if latest is None:
        return set()
    return {
        int(player_id)
        for player_id in session.scalars(
            select(FreeAgentSnapshot.player_id).where(
                FreeAgentSnapshot.league_season_id == league_season.id,
                FreeAgentSnapshot.observed_at == latest,
            )
        ).all()
    }


def _posted(session: Session, matchup_id: int, team_row_id: int) -> CategoryLine:
    """The raw counts a team has posted in a matchup, as a line."""
    rows = session.execute(
        select(MatchupTeamStat.abbreviation, MatchupTeamStat.value).where(
            MatchupTeamStat.matchup_id == matchup_id,
            MatchupTeamStat.team_id == team_row_id,
        )
    ).all()
    counts = {
        str(abbreviation): float(value or 0.0)
        for abbreviation, value in rows
        if str(abbreviation) in COUNTS
    }
    return CategoryLine(counts, 0)


def _faab_spent(session: Session, team: Team) -> int:
    spent = session.scalar(
        select(func.coalesce(func.sum(Transaction.bid_amount), 0)).where(
            Transaction.league_season_id == team.league_season_id,
            Transaction.team_id == team.id,
            Transaction.status == EXECUTED,
        )
    )
    return int(spent or 0)


def _roster_slot_teams(
    session: Session, league_season: LeagueSeason, player_ids: Sequence[int]
) -> dict[int, str]:
    """Each player's NBA team abbreviation on his latest weekly roster row."""
    if not player_ids:
        return {}
    rows = session.execute(
        select(RosterSlot.player_id, RosterSlot.pro_team)
        .join(Matchup, Matchup.id == RosterSlot.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            RosterSlot.player_id.in_(list(player_ids)),
            RosterSlot.pro_team.is_not(None),
        )
        .order_by(MatchupPeriod.period.desc())
    ).all()
    out: dict[int, str] = {}
    for player_id, pro_team in rows:
        out.setdefault(int(player_id), str(pro_team))
    return out


def _eligibility(
    session: Session, season: int, player_ids: Sequence[int]
) -> tuple[dict[int, frozenset[str]], dict[int, str | None]]:
    """Eligible slots and primary position per player, from any stored kind.

    Both kinds of season line carry the same eligibility, so every row is
    read and whichever names a position wins.
    """
    rows = session.execute(
        select(
            PlayerSeasonStat.player_id,
            PlayerSeasonStat.eligible_slots,
            PlayerSeasonStat.primary_position,
        ).where(
            PlayerSeasonStat.season == season,
            PlayerSeasonStat.player_id.in_(list(player_ids)),
        )
    ).all()
    slots: dict[int, frozenset[str]] = {}
    positions: dict[int, str | None] = {}
    for player_id, eligible, position in rows:
        key = int(player_id)
        slots[key] = slots.get(key, frozenset()) | frozenset(str(s) for s in (eligible or []))
        if position and not positions.get(key):
            positions[key] = str(position)
    return slots, positions
