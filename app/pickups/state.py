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

GAMES LEFT OVER A SEASON, WHICH IS A DIFFERENT QUESTION

Counting an OUT man for no games at all is right for tonight and wrong for
March. ESPN's basketball API carries no return date -- not a null one, no such
field (`app/listener/pool.py`) -- so `playable_days` gave every ruled-out man
zero games for the rest of the season, and the recommender projected exactly
0.00 for all ninety-one of 2026's stashes against the 94.5 categories they
delivered (`docs/stashes.md` section 7).

So a second count sits beside the first. `expected_games` is the same game
days weighted by the chance he is back by each of them and by the ramp he
comes back on (`app.pickups.returns`, the declared rule of
`docs/stash_mode.md`), as a fraction rather than a whole number. It equals
`games_remaining_this_period` exactly for a man who is not ruled out, so
nothing about a healthy roster moves. The week's seating reads `game_days` and
is untouched; every rest-of-season caller reads `expected_games`.

`days_out` is what that weighting is read by: calendar days since his last
played game, from `player_game_stats` before `today`. A man with no played
game falls back to the day before his first missed one, and a man with no
stored game at all to a single day out, which is the prior's most optimistic
row -- both are flagged by `days_out` being a stand-in rather than a
measurement, and both are rare enough to be preseason cases.

WHAT HAS BEEN POSTED SO FAR

`matchup_team_stats` holds one row per (matchup, team, category) and has no
day column, so it cannot answer "what has this side posted by now": for a
period that is over it is the period's FINAL total. On a live morning that
does not show, because ESPN is still writing the row and it holds the
running tally. On a replayed day, a caught-up morning or a page asked for
`?today=` a past day, it is the finished week -- and the week report then
projects the days still to come on top of a week already played.

So `_posted` takes the day and decides which source can answer. Live -- the
database holds no box score on `today` or later -- keeps ESPN's own row,
because it carries stat corrections our box scores may not. Anything else is
a replay, and the totals are summed from the started lines instead, which
over a whole period reproduces `matchup_team_stats` exactly.

The boundary is exclusive: a report for the morning of day N counts the
days of this period **before** N. `scoring_periods_remaining` begins at N,
so day N's games are the first thing the projection adds; counting them as
posted as well would count them twice. That is the opposite of the rule
`_faab_spent` and `_adds_in_period` use, and deliberately so -- a bid or an
add made on the morning of day N has happened by the time the report is
read, while day N's games have not.

WHO POSTED IT

`_posted_men` is the same sum broken out a man at a time, and it is always
the box scores' -- the started lines on the period's days before `today` --
whichever source the total came from. On a replayed day the two are the
same arithmetic and the men add up to the total exactly; `_posted` folds
them rather than running a second query. On a live morning ESPN's row is
kept as the total and the men can fall short of it, because ESPN writes
its running tally as the games go and our box scores arrive with the
nightly ingest. `posted_source` says which of the two a total is, so a page
can print the gap rather than let a table quietly disagree with the score
above it.

FAAB

The in-season pot is `league_seasons.acquisition_budget` (100 every season),
not `auction_budget`, which is the draft's 200 and what the design note
named by mistake. What is spent is the sum of the executed bids made **on or
before `today`**: a report about a past day must not charge a team money it
had not spent yet, which is what `_faab_spent` did before 2026-09-18 and why
one 2026 team read $-3 on day 100 (it had spent $89 of its $100 by then, and
the missing $14 was all spent after it).

Even summed correctly the bid feed is a reconstruction, not a ledger. Over
the whole of 2026 it disagrees with ESPN's own `teams.acquisition_budget_spent`
for six of the fourteen teams, in both directions (+4, +3, -1, -2, -3, -4);
team 3's executed claims sum to $103 where ESPN's column says exactly $100.
ESPN does enforce the cap -- no team's column exceeds the budget, and the
season carries a `FAILED_AUCTIONBUDGETEXCEEDED` claim -- so the overshoot is
ours, not the league's. `faab_remaining` is therefore floored at zero and the
overshoot carried beside it as `faab_overspent`, so a page can say the pot is
empty and that our arithmetic ran past it, rather than printing a negative
pot as though a manager could bid it.

THE WIRE ON A PLAYED SEASON

`free_agent_snapshots` is the listener's, and the listener only ever runs for
the season in progress, so a played season has no rows and the wire would be
empty. When a season has no snapshots at all, `load_free_agents` falls back to
the historical definition `scripts/pickups_backtest.py` reconstructs the wire
with: a man who played that scoring period and whom no team had in its lineup
that day. It is a weaker definition -- it cannot see a free agent who did not
play, and it knows nothing about waivers -- so `has_free_agent_snapshots` lets
a report say which wire it is looking at. A caller that names its own pool
(`player_ids`, which is what the backtest passes) never reaches the fallback,
so the backtest's numbers are untouched.

ADDS, AND WAIVERS

Two league rules bound what the recommender may propose. Adds are budgeted
by the matchup period -- one for each of its days, spent on any days of it
(`ADDS_PER_PERIOD_DAY`) -- so a week is a budget of seven and the report
carries what is left of it. What has been spent of it is counted on the
period's days **up to and including `today`**, the same bound `_faab_spent`
uses and for the same reason. And a free agent the league has on waivers
cannot play for us before the scoring period in which he clears, so his
`waiver_clears_on` is carried beside him and the seating reads it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from espn_api.basketball.constant import PRO_TEAM_MAP
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    FreeAgentSnapshot,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Player,
    PlayerGameStat,
    PlayerSeasonStat,
    PlayerStatusSnapshot,
    ProTeamGame,
    RosterSlot,
    Team,
    Transaction,
    TransactionItem,
)
from app.draft.pool import roster_size_for
from app.inseason.startable import NO_PRO_TEAM, RULED_OUT_STATUSES
from app.listener.pool import WAIVERS
from app.listener.snapshots import latest_snapshots
from app.pickups.returns import expected_games as prior_expected_games
from app.pickups.returns import expected_games_from_date
from app.scoring.lines import COUNTS, CategoryLine
from app.scoring.replacement import ADD_TYPES

#: Lineup slot names that mean the player is held but not in the lineup.
#: FA is ESPN's marker for a player who left the roster during the period
#: and is not held at all.
IR_SLOT = "IR"
GONE_SLOT = "FA"

#: A transaction ESPN carried out, as `transactions.status` spells it.
EXECUTED = "EXECUTED"

#: Where a side's posted-so-far total came from. ESPN's own matchup row
#: (`matchup_team_stats`) on a live morning, our stored box scores on any
#: replayed day. See the module docstring, "who posted it".
POSTED_ESPN = "espn"
POSTED_BOX_SCORES = "box_scores"

#: Adds a team may make for each day of a matchup period.
#:
#: ESPN's raw `acquisitionSettings` for this league, read 2026-09-18 and
#: confirmed against the 2026 transactions: `matchupAcquisitionLimit` 1.0
#: with `matchupLimitPerScoringPeriod` true. That is one add per day of the
#: period, spendable on any days of it -- a seven-day period allows seven,
#: the six-day opening week six -- and not one add per day: 204 of the 920
#: 2026 team-days with an add on them had two or more. The season limit is
#: unlimited, so the period is the only bound.
#:
#: The setting is not in the database. `league_seasons.raw_settings` holds
#: the schedule and the scoring and nothing about acquisitions, so the
#: number lives here with its provenance rather than being read from a row
#: that does not exist.
ADDS_PER_PERIOD_DAY = 1

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
    #: His NBA team's game days over the same window, whatever his status.
    #: `game_days` is this less the days ESPN has ruled him out of, so for
    #: anybody fit the two are equal. A stash's arithmetic needs the schedule
    #: rather than the seating, and this saves it a second query.
    schedule_days: tuple[int, ...] = ()
    #: Games expected over the same window, as a fraction: `len(game_days)`
    #: for anyone not ruled out, and the return prior times the ramp for a
    #: man who is (the module docstring, `app.pickups.returns`). None when
    #: nobody counted it, and then `season_games` falls back to the whole
    #: days, which is what every hand-built player in a test wants.
    expected_games: float | None = None
    #: Calendar days since his last played game, when he is ruled out and the
    #: database holds a game of his. None for anybody else.
    days_out: int | None = None
    #: The day he clears waivers, when the league has him on waivers, and
    #: that day as a scoring period. Set only by `load_free_agents` reading
    #: the latest snapshot; None for a rostered man and for a pool named by
    #: id, whose members are free agents now by definition.
    waiver_clears_at: date | None = None
    waiver_clears_on: int | None = None

    @property
    def games_remaining_this_period(self) -> int:
        return len(self.game_days)

    @property
    def season_games(self) -> float:
        """Games to count over a rest-of-season horizon, fractions allowed.

        The same number as `games_remaining_this_period` for everybody who is
        not ruled out. For a man who is, the expected count: his game days
        weighted by the chance he is back by each of them and by the ramp
        (`app.pickups.returns`). A player nobody counted it for falls back to
        the whole days, so a hand-built fixture behaves as it always did.
        """
        return float(len(self.game_days)) if self.expected_games is None else self.expected_games

    @property
    def ruled_out(self) -> bool:
        """Whether ESPN has him out of the next game. See `startable`."""
        return (self.injury_status or "").upper() in RULED_OUT_STATUSES

    def seatable_on(self, day: int) -> bool:
        """Whether he could play for us on scoring period `day`.

        A man on waivers cannot: a claim on him is a bid that resolves when
        the waiver period ends, and he plays for us from that day on.
        """
        return self.waiver_clears_on is None or day >= self.waiver_clears_on


@dataclass(frozen=True)
class BoxScore:
    """One stored `player_game_stats` line, as a page prints it.

    A fact about a day that has been played, not an estimate of one: it is
    carried for display and nothing in the seating or the projection reads
    it. `minutes` is beside the line rather than in it because the nine do
    not score minutes and `CategoryLine` holds only what they do.
    """

    scoring_period: int
    played: bool
    minutes: float
    line: CategoryLine


@dataclass(frozen=True)
class PostedMan:
    """One man's share of what a side has posted this matchup period.

    `line.games` is the days he started and produced a line, which is what
    the table sorts on before points.
    """

    player_id: int
    name: str
    minutes: float
    line: CategoryLine

    @property
    def games(self) -> int:
        return self.line.games


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
    #: The same, a man at a time, always from the stored box scores. Best
    #: first by games then points. See the module docstring, "who posted it".
    my_posted_men: tuple[PostedMan, ...]
    opp_posted_men: tuple[PostedMan, ...]
    #: `POSTED_ESPN` or `POSTED_BOX_SCORES`: which source the two totals
    #: above came from, so a reader can be told when the men fall short.
    posted_source: str
    roster: tuple[RosteredPlayer, ...]
    #: The pot left as of `today`, never below zero. See the module docstring.
    faab_remaining: int
    #: How far our sum of the bid feed ran past the budget, normally zero.
    #: Non-zero means the feed and ESPN's ledger disagree and the pot is spent.
    faab_overspent: int
    #: Roster places not held, injured reserve aside.
    open_slots: int
    ir_slot_free: bool
    #: Executed adds this team has already made in this matchup period.
    adds_used: int
    #: What the period allows: `ADDS_PER_PERIOD_DAY` times its days.
    adds_budget: int

    @property
    def adds_left(self) -> int:
        """Adds still to spend this period, never below zero."""
        return max(0, self.adds_budget - self.adds_used)

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


def expected_games(
    games: Mapping[int, date],
    days: Iterable[int],
    *,
    today: int,
    injury_status: str | None,
    expected_return_date: date | None,
    days_out: int | None,
) -> float:
    """Games expected over `days`, as a fraction. The declared OUT-man rule.

    A man who is not ruled out has every game day his NBA team plays, which
    is `len(playable_days(...))` and the number this engine has always used.
    A man who is ruled out has each of those days weighted by the chance he is
    back by it and by the ramp he comes back on (`app.pickups.returns`) --
    from ESPN's `expected_return_date` when there is one, and from the
    box-score return prior by `days_out` when there is not.

    `today` is the day the wait is counted from, which is not always the first
    day of `days`: a trade's playoff window starts in March and a man is out
    from now.
    """
    out = (injury_status or "").upper() in RULED_OUT_STATUSES
    if not out:
        return float(
            len(
                playable_days(
                    games,
                    days,
                    injury_status=injury_status,
                    expected_return_date=expected_return_date,
                )
            )
        )
    playing = [(day, games[day]) for day in sorted(days) if day in games]
    if expected_return_date is not None:
        return expected_games_from_date(
            (when - expected_return_date).days for _day, when in playing
        )
    return prior_expected_games(
        (day - today for day, _when in playing), days_out=days_out if days_out is not None else 1
    )


def days_out_on(
    session: Session, season: int, player_ids: Sequence[int], today: int
) -> dict[int, int]:
    """Calendar days since each man's last played game, before `today`.

    The count `scripts/stashes.py` measures the return prior against: the
    decision day less his last `played = true` scoring period, and a scoring
    period is a calendar day in this database. A man with rows but none of
    them played is counted from the day before his first one; a man with no
    stored game at all is absent from the answer and the caller stands one day
    in for him.
    """
    ids = sorted({int(player_id) for player_id in player_ids})
    if not ids:
        return {}
    rows = session.execute(
        select(
            PlayerGameStat.player_id,
            func.max(
                case((PlayerGameStat.played.is_(True), PlayerGameStat.scoring_period), else_=None)
            ),
            func.min(PlayerGameStat.scoring_period),
        )
        .where(
            PlayerGameStat.season == season,
            PlayerGameStat.player_id.in_(ids),
            PlayerGameStat.scoring_period < today,
        )
        .group_by(PlayerGameStat.player_id)
    ).all()
    out: dict[int, int] = {}
    for player_id, last_played, first_row in rows:
        anchor = int(last_played) if last_played is not None else int(first_row) - 1
        out[int(player_id)] = max(1, today - anchor)
    return out


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

    Raises when `today` falls in no matchup period of the season, or in one
    whose days are not recorded: there is no week to describe, and a caller
    guessing one -- its days, and so its add budget -- would be wrong quietly.
    """
    period = period_for_day(session, league_season, today)
    if period is None or period.first_scoring_period is None or period.final_scoring_period is None:
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
    my_men: tuple[PostedMan, ...] = ()
    opp_men: tuple[PostedMan, ...] = ()
    source = POSTED_BOX_SCORES
    if matchup is not None:
        other_id = matchup.away_team_id if matchup.home_team_id == team.id else matchup.home_team_id
        if other_id is not None:
            opponent = session.get(Team, other_id)
        season = int(league_season.season)
        # Read once for both sides: it is a fact about the database, not the team.
        live = is_live(session, season, today)
        source = POSTED_ESPN if live else POSTED_BOX_SCORES
        my_totals, my_men = _posted(
            session, period, matchup.id, team.id, today, season=season, live=live
        )
        if opponent is not None:
            opp_totals, opp_men = _posted(
                session, period, matchup.id, opponent.id, today, season=season, live=live
            )

    held, on_ir = _lineup_roster(session, team.id, today)
    if held is None:
        held = _snapshot_roster(session, int(league_season.season), team_id)
        on_ir = frozenset()
    roster = build_players(session, league_season, held, remaining, on_ir=on_ir)

    first_day = int(period.first_scoring_period)
    last_day = int(period.final_scoring_period)
    active = [player for player in roster if not player.on_ir]
    ir_used = len(roster) - len(active)
    budget = int(league_season.acquisition_budget)
    spent = _faab_spent(session, team, today)
    return TeamWeek(
        team_id=team_id,
        matchup_period=int(period.period),
        scoring_periods_remaining=remaining,
        opponent_team_id=int(opponent.espn_team_id) if opponent is not None else None,
        my_totals=my_totals,
        opp_totals=opp_totals,
        my_posted_men=my_men,
        opp_posted_men=opp_men,
        posted_source=source,
        roster=roster,
        faab_remaining=max(0, budget - spent),
        faab_overspent=max(0, spent - budget),
        open_slots=max(0, roster_size_for(league_season) - len(active)),
        ir_slot_free=int(league_season.injured_reserve_slots or 0) > ir_used,
        adds_used=_adds_in_period(session, team, first_day, today),
        adds_budget=ADDS_PER_PERIOD_DAY * (last_day - first_day + 1),
    )


def _adds_in_period(session: Session, team: Team, first_day: int, through_day: int) -> int:
    """Executed adds this team had made this period by the end of `through_day`.

    An add is a WAIVER or FREEAGENT transaction ESPN carried out with an
    ADD item to this team (`app.scoring.replacement.ADD_TYPES`), counted the
    way `app.pickups.season._adds_in_window` counts a fortnight's: one per
    item, so a claim that added two men spends two of the budget.

    The far end is `today`, not the period's last day, and it must be the
    same day `_faab_spent` stops at: an add and the money it cost are one
    transaction, so a report that charges a team for the bid must charge it
    for the add, and a report that does not must not do either. Windowing on
    the whole period instead read the adds a team had not made yet, which is
    worse than a wrong number -- a team the report believes has spent its
    budget is told there is nothing to plan today, and the recommendation
    disappears. Thirteen of the thirty-nine team-days the in-season
    rehearsal replayed lost their plan that way.
    """
    count = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
        .where(
            Transaction.league_season_id == team.league_season_id,
            Transaction.type.in_(ADD_TYPES),
            Transaction.status == EXECUTED,
            Transaction.scoring_period >= first_day,
            Transaction.scoring_period <= through_day,
            TransactionItem.item_type == "ADD",
            TransactionItem.to_team_id == team.id,
        )
    )
    return int(count or 0)


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
    names the pool instead, for a test or a backtest; a pool named by id is
    taken as men who are free agents now, so none of them is on waivers and
    no snapshot is read for them. `days` counts each man's games over a
    window other than the rest of this period, which is what the
    rest-of-season report needs.

    On a season the listener never ran for there are no snapshots to read at
    all, so the pool falls back to `historical_free_agents` on `week.today`
    and nobody is on waivers, because nothing recorded who was. A caller who
    named `player_ids` never reaches that fallback.
    """
    named = player_ids is not None
    if player_ids is not None:
        ids = {int(player_id) for player_id in player_ids}
    elif has_free_agent_snapshots(session, league_season):
        ids = _latest_pool(session, league_season)
    else:
        # No pass ever ran, so no row says who was on waivers either.
        ids = historical_free_agents(session, league_season, week.today)
        named = True
    waivers = (
        {}
        if named
        else waiver_clears(
            session,
            league_season,
            season_calendar(session, int(league_season.season)),
            ids,
        )
    )
    return build_players(
        session,
        league_season,
        ids,
        days if days is not None else week.scoring_periods_remaining,
        waivers=waivers,
    )


def waiver_clears(
    session: Session,
    league_season: LeagueSeason,
    calendar: SeasonCalendar | None,
    player_ids: Iterable[int] | None = None,
) -> dict[int, tuple[date, int]]:
    """Who is on waivers, and the day and scoring period he clears on.

    The latest `free_agent_snapshots` row per player, not the latest pass:
    the question is this one man's waiver state, and the pass that saw him
    may not be the newest one. Only a row whose status is WAIVERS with a
    `waiver_clears_at` answers it; everyone else is free to play at once.

    Without a calendar there is no scoring period to map the day to, so
    nobody is held back rather than everybody being held back on a guess.
    """
    if calendar is None:
        return {}
    query = (
        select(
            FreeAgentSnapshot.player_id,
            FreeAgentSnapshot.status,
            FreeAgentSnapshot.waiver_clears_at,
        )
        .where(FreeAgentSnapshot.league_season_id == league_season.id)
        .distinct(FreeAgentSnapshot.player_id)
        .order_by(FreeAgentSnapshot.player_id, FreeAgentSnapshot.observed_at.desc())
    )
    if player_ids is not None:
        wanted = sorted({int(player_id) for player_id in player_ids})
        if not wanted:
            return {}
        query = query.where(FreeAgentSnapshot.player_id.in_(wanted))
    out: dict[int, tuple[date, int]] = {}
    for player_id, status, clears_at in session.execute(query).all():
        if str(status).upper() != WAIVERS or clears_at is None:
            continue
        day = clears_at.date()
        out[int(player_id)] = (day, calendar.scoring_period_on(day))
    return out


def waiver_state(players: Iterable[RosteredPlayer]) -> dict[int, tuple[date, int]]:
    """The mapping `build_players` takes, read back off players that carry it.

    So a caller who already has the wire -- the rest-of-season report, whose
    week half rebuilds the arriving men (`app.pickups.stream.week_deltas`) --
    passes the waiver state on rather than reading the snapshots again.
    """
    return {
        player.player_id: (player.waiver_clears_at, player.waiver_clears_on)
        for player in players
        if player.waiver_clears_at is not None and player.waiver_clears_on is not None
    }


def build_players(
    session: Session,
    league_season: LeagueSeason,
    player_ids: Iterable[int],
    days: Sequence[int],
    *,
    on_ir: frozenset[int] = frozenset(),
    waivers: Mapping[int, tuple[date, int]] | None = None,
    today: int | None = None,
) -> tuple[RosteredPlayer, ...]:
    """`RosteredPlayer` for each id, sorted by id, from the tables named above.

    `waivers` is `waiver_clears`' answer for these ids, when the caller has
    read it; without it nobody is on waivers.

    `today` is the day an absence is counted from and defaults to the first of
    `days`, which is what it is for every caller whose window starts now. A
    caller asking about a window further out -- a trade's playoff weeks --
    passes the real day, or every ruled-out man reads as out since March.
    """
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

    from_day = today if today is not None else (min(days) if days else 0)
    statuses = {
        player_id: (
            snapshots[player_id].injury_status if player_id in snapshots else None,
            snapshots[player_id].expected_return_date if player_id in snapshots else None,
        )
        for player_id in ids
    }
    # Only a ruled-out man with no date needs the box scores read, which on an
    # ordinary roster is nobody at all.
    waiting = [
        player_id
        for player_id, (status, returns) in statuses.items()
        if returns is None and (status or "").upper() in RULED_OUT_STATUSES
    ]
    out_for = days_out_on(session, season, waiting, from_day) if waiting else {}

    on_waivers = waivers or {}
    players: list[RosteredPlayer] = []
    for player_id in ids:
        status, returns = statuses[player_id]
        clears = on_waivers.get(player_id)
        his_games = games.get(pro_teams[player_id], {})
        days_out = out_for.get(player_id)
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
                    his_games,
                    days,
                    injury_status=status,
                    expected_return_date=returns,
                ),
                on_ir=player_id in on_ir,
                schedule_days=tuple(day for day in sorted(days) if day in his_games),
                expected_games=expected_games(
                    his_games,
                    days,
                    today=from_day,
                    injury_status=status,
                    expected_return_date=returns,
                    days_out=days_out,
                ),
                days_out=days_out,
                waiver_clears_at=clears[0] if clears is not None else None,
                waiver_clears_on=clears[1] if clears is not None else None,
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


def has_free_agent_snapshots(session: Session, league_season: LeagueSeason) -> bool:
    """Whether the listener ever recorded a wire for this season.

    False on every played season, because the listener only runs for the one
    in progress. A report reads it to say which wire it looked at rather than
    printing "0 free agents evaluated" and leaving the reader to guess why.
    """
    return (
        session.scalar(
            select(FreeAgentSnapshot.id)
            .where(FreeAgentSnapshot.league_season_id == league_season.id)
            .limit(1)
        )
        is not None
    )


def historical_free_agents(session: Session, league_season: LeagueSeason, day: int) -> set[int]:
    """The wire on `day`, rebuilt from what was played and who was held.

    `scripts/pickups_backtest.py`'s `free_agent_pool`, which is the only
    definition of a past day's wire this database can support: a man who
    played that scoring period and whom no team in this league had in its
    lineup that day. It is narrower than the real wire -- a free agent who
    did not play is invisible, and nothing here knows about waivers -- so a
    report built on it says so.
    """
    held = (
        select(DailyLineupSlot.player_id)
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .where(
            Team.league_season_id == league_season.id,
            DailyLineupSlot.scoring_period == day,
            DailyLineupSlot.player_id == PlayerGameStat.player_id,
        )
    )
    return {
        int(player_id)
        for player_id in session.scalars(
            select(PlayerGameStat.player_id)
            .where(
                PlayerGameStat.season == int(league_season.season),
                PlayerGameStat.scoring_period == day,
                PlayerGameStat.played.is_(True),
                PlayerGameStat.minutes > 0,
                ~held.exists(),
            )
            .distinct()
        ).all()
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


def is_live(session: Session, season: int, today: int) -> bool:
    """Whether `today` is the newest day this database knows anything about.

    The test for "am I being asked about now, or about a day the season has
    already run past": a box score recorded on `today` or later can only mean
    the second. `player_game_stats` is keyed on the NBA season rather than on
    one league, which is right here -- the question is what this machine has
    ingested, not what this league did.

    Deliberately not a clock comparison. A worker catching up a morning it
    missed has yesterday's date and a database full of yesterday's games, and
    a page asked for `?today=` a past day has today's date and a database
    full of everything since. Both must read as a replay, and both do.
    """
    return (
        session.scalar(
            select(PlayerGameStat.id)
            .where(PlayerGameStat.season == season, PlayerGameStat.scoring_period >= today)
            .limit(1)
        )
        is None
    )


#: The `player_game_stats` column each `COUNTS` key is summed from, so the
#: SELECT and the fold cannot drift out of order.
_POSTED_COLUMNS: tuple[str, ...] = tuple(COUNTS.values())


def _posted(
    session: Session,
    period: MatchupPeriod,
    matchup_id: int,
    team_row_id: int,
    today: int,
    *,
    season: int,
    live: bool,
) -> tuple[CategoryLine, tuple[PostedMan, ...]]:
    """What a team has posted in this matchup by the morning of `today`, and who.

    Two sources for the total, and `live` picks between them (see the module
    docstring). On a live morning ESPN's own `matchup_team_stats` row is the
    running tally and is kept, because it carries the stat corrections our
    box scores may not have. On any replayed day that row is the period's
    final total with no day to cap it by, so the started lines on the
    period's days **before** `today` are summed instead -- exclusive, because
    `scoring_periods_remaining` starts at `today` and the projection will add
    that day itself.

    The men are the box scores' either way, and on a replayed day the total
    is their sum rather than a second query: the table a page draws under the
    score and the score itself are then one piece of arithmetic.
    """
    men = _posted_men(session, period, team_row_id, today, season=season)
    if live:
        return _stored_posted(session, matchup_id, team_row_id), men
    totals = dict.fromkeys(COUNTS, 0.0)
    games = 0
    for man in men:
        games += man.line.games
        for abbreviation in COUNTS:
            totals[abbreviation] += man.line.get(abbreviation)
    return CategoryLine(totals, games), men


def _posted_men(
    session: Session,
    period: MatchupPeriod,
    team_row_id: int,
    today: int,
    *,
    season: int,
) -> tuple[PostedMan, ...]:
    """Each man's share of what this side posted, from the stored box scores.

    The same join `_posted` sums, grouped by the man rather than added up:
    the started lines on the period's days **before** `today`. A man who has
    since been dropped is in it, because his games are in the score.
    """
    rows = session.execute(
        select(
            PlayerGameStat.player_id,
            Player.name,
            func.count(),
            func.coalesce(func.sum(PlayerGameStat.minutes), 0.0),
            *(
                func.coalesce(func.sum(getattr(PlayerGameStat, column)), 0.0)
                for column in _POSTED_COLUMNS
            ),
        )
        .join(Player, Player.id == PlayerGameStat.player_id)
        .join(
            DailyLineupSlot,
            (DailyLineupSlot.player_id == PlayerGameStat.player_id)
            & (DailyLineupSlot.scoring_period == PlayerGameStat.scoring_period),
        )
        .where(
            DailyLineupSlot.team_id == team_row_id,
            DailyLineupSlot.matchup_period_id == period.id,
            DailyLineupSlot.started.is_(True),
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period < today,
            PlayerGameStat.played.is_(True),
            PlayerGameStat.minutes > 0,
        )
        .group_by(PlayerGameStat.player_id, Player.name)
    ).all()
    men = [
        PostedMan(
            player_id=int(row[0]),
            name=str(row[1]),
            minutes=float(row[3] or 0.0),
            line=CategoryLine(
                {
                    abbreviation: float(value or 0.0)
                    for abbreviation, value in zip(COUNTS, row[4:], strict=True)
                },
                int(row[2] or 0),
            ),
        )
        for row in rows
    ]
    men.sort(key=lambda man: (-man.games, -man.line.get("PTS"), man.name))
    return tuple(men)


def box_scores(
    session: Session, season: int, player_ids: Iterable[int], day: int
) -> dict[int, BoxScore]:
    """Each man's stored line on one scoring period, for the men who have one.

    A fact about a day, read for display only: `app.pickups.today` hangs it
    off a man so a page can print what he actually did, and nothing in the
    seating or the projection sees it. A man ESPN lists with no line at all
    is absent from the answer, which is what lets a page show the game mark
    alone until the ingest has reached him.
    """
    ids = sorted({int(player_id) for player_id in player_ids})
    if not ids:
        return {}
    rows = session.execute(
        select(
            PlayerGameStat.player_id,
            PlayerGameStat.played,
            PlayerGameStat.minutes,
            *(getattr(PlayerGameStat, column) for column in _POSTED_COLUMNS),
        ).where(
            PlayerGameStat.season == season,
            PlayerGameStat.scoring_period == day,
            PlayerGameStat.player_id.in_(ids),
            PlayerGameStat.played.is_(True),
        )
    ).all()
    return {
        int(row[0]): BoxScore(
            scoring_period=day,
            played=bool(row[1]),
            minutes=float(row[2] or 0.0),
            line=CategoryLine(
                {
                    abbreviation: float(value or 0.0)
                    for abbreviation, value in zip(COUNTS, row[3:], strict=True)
                },
                1,
            ),
        )
        for row in rows
    }


def _stored_posted(session: Session, matchup_id: int, team_row_id: int) -> CategoryLine:
    """ESPN's own row for this matchup: the running tally while it is running."""
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


def _faab_spent(session: Session, team: Team, through_day: int) -> int:
    """What this team had spent of the in-season pot by the end of `through_day`.

    The day bound is the whole point: without it a report about a past day
    charges the team every bid it went on to make, which is how a team with
    $11 left on day 100 was shown as $-3. A transaction with no scoring
    period recorded is not counted, because there is no day to place it on.
    """
    spent = session.scalar(
        select(func.coalesce(func.sum(Transaction.bid_amount), 0)).where(
            Transaction.league_season_id == team.league_season_id,
            Transaction.team_id == team.id,
            Transaction.status == EXECUTED,
            Transaction.scoring_period <= through_day,
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
