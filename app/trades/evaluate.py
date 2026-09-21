"""A trade, judged forward, in the same currency as a pickup.

`app.scoring.trade_grades` grades a trade in hindsight. This module asks the
question before the deal is made: for each side, how many expected category
wins does it gain or lose, this week and over the rest of the season?

ONE CURRENCY, ONE SET OF FUNCTIONS

Nothing here invents a second valuation. A trade is a move, and a move is
judged by `app.pickups.judge`:

    net = delta_week + delta_season_per_week * weeks_remaining

`delta_week` is the head-to-head change in the matchup in front of us, from
`app.pickups.stream.week_deltas` -- the same seating, the same normal model,
the same knowable lines the streaming report uses. `delta_season_per_week` is
this roster's ordinary week with the deal in it less the same week without it,
through the league-standard lens (`app.pickups.judge.Standard.week_wins`): the
two lines the nine-category table is drawn from. So "trade for Jokic" and
"pick up whoever is on the wire" are two numbers in the same unit, and the
projected end-of-season category record comes out of the same `Judgement`.

THE SEASON TERM IS A WITH-AND-WITHOUT OVER THE ROSTER

The first cut valued each man on his own inside a league-average team
(`app.pickups.judge.places_cost`) and summed. That is comparable across teams
and it cannot see saturation: expected wins is a sum of probabilities that
flatten, so a roster already winning rebounds gains nothing from more of them
and two starters routinely beat one superstar. The calibration of 2026-09-21
measured the consequence -- consolidating deals over-rated by +0.27 categories
a week -- and revision R1 (declared in docs/trades.md section 7a before it was
run) made the headline the roster with-and-without instead. The per-man number
is still computed and still on the payload as `SideReport.season_independent`,
so the two can be read against each other; it is no longer the headline. The
playoff lens is unchanged and still reads `places_cost`, because a whole
playoff roster is a different question from a whole season's.

WHEN THE TRADE LANDS

A trade cannot take effect the moment it is evaluated: the other manager has
to accept it, and ESPN holds it for review. **The league's review setting is
not stored.** `league_seasons.raw_settings` carries the schedule, the scoring
and the division map and nothing about trades (checked on the live database,
2026-09-21, every season 2019-2027); `league_seasons.trade_deadline` is the
deadline, not the review window.

So `TRADE_REVIEW_DAYS` carries the number with its provenance, as
`app.pickups.state.ADDS_PER_PERIOD_DAY` carries the add budget. One day, and
the evidence is the 2026 ledger: each of the four executed `TRADE_ACCEPT`
rows that season names players who were in the old team's lineup through the
day before the transaction and in the new team's lineup on the day of it
(days 22, 53, 85 and 108). A trade therefore lands the day after the morning
you are looking at it, at the earliest, and the days of this week before that
are projected with the roster as it stands -- on both sides of the
comparison, so they cancel in the delta while still counting toward the
totals the category probabilities are read off.

That is a floor, not a promise. A manager who knows the deal is already
agreed passes `review_days=0`; one whose league votes for two days passes 2.
The report says which number it used and where it came from.

BOTH SIDES

A trade only happens if the other manager says yes, so the report judges it
from his roster too, with the same machinery: his week, his league-standard
values, his playoff weeks. That is **our estimate of his side**, built from
our own projections and the same lens -- not what he thinks, and the report
says so. It is still the most useful thing on the page: a deal that reads
+0.5 for us and -0.6 for him is a deal that will not be accepted.

When the two sides are also this week's opponents, the deal changes both
rosters in one matchup, and `week_deltas` is given the other side's move as
well; otherwise a trade with the man you are playing would be judged against
a roster he no longer has.

UNEVEN COUNTS

A two-for-one opens a roster place on one side and fills one on the other. The
place left open is filled, in the before-and-after and in the table both, by
the best man on the wire (`wire_replacement` names the value, `_best_wire` the
man), so the same roster is counted either way and nothing is quietly worth
zero. When a side receives more men than it gives and has no open slot,
somebody is dropped -- the caller may name him, and by default it is the
cheapest man on the active roster by what his place is worth. The report
names him and what he cost either way. Men on injured reserve are never
chosen: dropping one frees no active place.

WHAT IT CANNOT KNOW

Every player's line is the knowable line as of the morning the trade is
judged on (`app.scoring.knowable`), so a 12-game sample and a full season
look the same in the arithmetic and different on the page: `PlayerCard`
carries the games behind each projection, whether a preseason forecast was
available, the injury status the listener last saw, and the games remaining
that drive every total. A trade evaluation that hides a 12-game sample is
worse than none.

NO LOOK-AHEAD

Every query takes `today` and reads nothing on or after it. The projections
filter `scoring_period < today` (`app.scoring.knowable`), the roster is the
latest lineup day at or before `today`, the posted totals are the days of
this period before it (`app.pickups.state`), the banked record counts only
periods already finished, and the games remaining come from the stored NBA
schedule, which is a fact about the future rather than about the past.
`review_days` moves only the day the trade is *seated* from, never the day
the data is read as of.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.judge import (
    TYPICAL_PICKUP,
    Judgement,
    SpotBook,
    Standard,
    horizon,
    judge,
    load_spots,
    places_cost,
    standard_lens,
    weekly_lines,
    weeks_between,
)
from app.pickups.projection import rest_of_season_line
from app.pickups.season import SEASON_HURDLE_PAID
from app.pickups.state import (
    RosteredPlayer,
    TeamWeek,
    build_players,
    has_free_agent_snapshots,
    load_free_agents,
    load_team_week,
    season_calendar,
    team_row,
)
from app.pickups.stream import week_deltas
from app.scoring.knowable import knowable
from app.scoring.lines import CategoryLine, sum_lines
from app.scoring.value import category_wins
from app.trades.summary import summarise

__all__ = [
    "THIN_GAMES",
    "TRADE_HURDLE",
    "TRADE_REVIEW_DAYS",
    "TRADE_REVIEW_SOURCE",
    "CategoryView",
    "PlayerCard",
    "PlayoffLens",
    "SideReport",
    "TeamOffer",
    "TradeReport",
    "evaluate_trade",
    "playoff_window",
]

#: Scoring periods between the morning a trade is judged on and the first day
#: it can be in a lineup. See the module docstring: the setting is in no
#: table, and one day is what the 2026 ledger shows every executed trade did.
TRADE_REVIEW_DAYS = 1

#: Said on every report, because a number with no source is a guess.
TRADE_REVIEW_SOURCE = (
    "the league's trade review setting is not stored (league_seasons.raw_settings "
    "holds the schedule, the scoring and the divisions only), so this is the "
    "conservative default: the four executed 2026 trades all moved their players "
    "the day after the last day the old team held them"
)

#: The bar a trade is labelled against, categories a week, read on the net
#: over both horizons (`Judgement.per_week`). The rest-of-season report's own
#: paid bar, because a trade costs players rather than money and is a
#: rest-of-season decision: one number for one kind of question, so a trade
#: and a claim are labelled on the same scale (docs/pickups_backtest.md).
TRADE_HURDLE = SEASON_HURDLE_PAID

#: Games behind a projection below which the report calls it thin. Twelve is
#: the brief's own number and about a month of a nine-cat season: enough for a
#: rate to move a long way and not enough to trust it.
THIN_GAMES = 12

#: A category's win probability has moved when it changes by this much, the
#: threshold the streaming report already uses.
MOVED_THRESHOLD = 0.01


@dataclass(frozen=True)
class TeamOffer:
    """One side of a proposed deal: a team, and the players it gives away."""

    #: ESPN's team id, as every CLI, route and snapshot names a team.
    team_id: int
    #: Player ids leaving this roster. Empty is legal (a gift).
    gives: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlayerCard:
    """A player in the deal, and what the judgement about him rests on."""

    player_id: int
    name: str
    #: Categories a week he gives an ordinary roster place, league standard.
    value: float
    #: Games his NBA team has left over the planning stretch that he is not
    #: ruled out of, and the same over the playoff weeks.
    games_left: int
    playoff_games: int
    injury_status: str | None
    expected_return_date: date | None
    #: Games of his own behind the knowable line, and whether a preseason
    #: forecast stood behind the rest of it (`app.scoring.knowable`).
    games_so_far: int
    had_projection: bool
    #: "blend" (season to date over the preseason projection) or "snapshot"
    #: (a saved in-season projection stood in for the prior).
    projection_source: str

    @property
    def thin(self) -> bool:
        """Few enough games of his own that the rate could be anything."""
        return self.games_so_far < THIN_GAMES

    @property
    def hurt(self) -> bool:
        return bool(self.injury_status) and self.injury_status not in ("ACTIVE", "NORMAL")


@dataclass(frozen=True)
class CategoryView:
    """One category, before and after, in counts and in the chance of winning.

    The counts are what the roster posts in an ordinary week; the
    probabilities are against what this league's teams post in one. So a
    manager punting free-throw percentage sees the rate fall and the chance of
    winning it not move, which is the whole point of the table.
    """

    abbreviation: str
    before: float
    after: float
    p_before: float
    p_after: float

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def p_delta(self) -> float:
        return self.p_after - self.p_before

    @property
    def moved(self) -> bool:
        return abs(self.p_delta) >= MOVED_THRESHOLD


@dataclass(frozen=True)
class PlayoffLens:
    """The same deal counted over the playoff matchup periods alone.

    A trade for a man whose team plays four playoff weeks is a different deal
    from one for a man who plays two, and the regular-season number cannot
    say so. `app.scoring.trade_grades` already separates the two in hindsight;
    this is the forward half of the same split.
    """

    first_scoring_period: int | None
    last_scoring_period: int | None
    weeks: float
    delta_per_week: float
    categories: tuple[CategoryView, ...]
    #: Games the men involved have scheduled in the window, both sides summed.
    games: int
    #: Why the lens says nothing, when it says nothing.
    note: str | None = None

    @property
    def delta_total(self) -> float:
        return self.delta_per_week * self.weeks

    @property
    def measurable(self) -> bool:
        return self.note is None


@dataclass(frozen=True)
class SideReport:
    """One team's side of the trade, in full."""

    team_id: int
    team_name: str
    receives: tuple[PlayerCard, ...]
    gives: tuple[PlayerCard, ...]
    #: Men dropped to make room, charged like anyone leaving.
    drops: tuple[PlayerCard, ...]
    #: How the drops were chosen: "named", "cheapest" or "" when none.
    drop_source: str
    #: Roster places the deal leaves open, each worth `replacement` a week.
    places_opened: int
    #: Open places the deal fills without anybody being dropped.
    places_used: int
    #: The move in one currency over both horizons. Its season term is the
    #: roster with-and-without (revision R1, docs/trades.md section 7a).
    judgement: Judgement
    #: The season term the first cut used: each man valued on his own inside
    #: a league-average team (`places_cost`), kept here so the two can be
    #: measured against each other and never as the headline.
    season_independent: float
    #: The nine, before and after, in an ordinary week.
    categories: tuple[CategoryView, ...]
    playoffs: PlayoffLens
    #: What the wire gives a roster place back, categories a week.
    replacement: float
    #: The best free agent, whose line fills a place the deal opens in the
    #: table above. None when the wire has nobody better than the floor.
    replacement_player: PlayerCard | None
    hurdle: float
    #: Expected categories the roster wins in an ordinary week, before.
    expected_per_week: float
    #: Generated from the numbers; see `app.trades.summary`.
    summary: str
    #: Honest caveats: a thin projection, an injury, a missing schedule.
    notes: tuple[str, ...] = ()

    @property
    def net(self) -> float:
        """Categories the deal is worth this side, over both horizons."""
        return self.judgement.delta_total

    @property
    def per_week(self) -> float:
        return self.judgement.per_week

    @property
    def clears(self) -> bool:
        """Whether the net per week is at or above the bar. A label, not advice."""
        return self.per_week >= self.hurdle

    def moved(self, threshold: float = MOVED_THRESHOLD) -> tuple[CategoryView, ...]:
        """The categories whose win probability moved, largest first."""
        return tuple(
            sorted(
                (view for view in self.categories if abs(view.p_delta) >= threshold),
                key=lambda view: -abs(view.p_delta),
            )
        )


@dataclass(frozen=True)
class TradeReport:
    """A proposed trade, judged from both sides on one day."""

    season: int
    #: The scoring period the judgement is made on. Nothing after it is read.
    today: int
    #: The first day the deal could be in a lineup.
    effective_day: int
    review_days: int
    review_source: str
    #: The stretch the rest-of-season half plans over, inclusive.
    first_scoring_period: int
    last_scoring_period: int
    weeks_remaining: float
    sides: tuple[SideReport, SideReport]
    hurdle: float
    #: Free agents the wire replacement was taken over, and whether that wire
    #: was the listener's or rebuilt from what was played
    #: (`app.pickups.state.historical_free_agents`).
    pool_size: int
    historical_wire: bool
    notes: tuple[str, ...] = ()

    def side(self, team_id: int) -> SideReport:
        for found in self.sides:
            if found.team_id == team_id:
                return found
        raise KeyError(f"team {team_id} is not in this trade")

    @property
    def both_gain(self) -> bool:
        """Whether every side's net is positive: the deal category leagues make."""
        return all(side.net > 0 for side in self.sides)


@dataclass(frozen=True)
class _Side:
    """Working state for one side while the report is being built."""

    offer: TeamOffer
    team_name: str
    week: TeamWeek
    active: tuple[int, ...]
    gives: tuple[int, ...]
    receives: tuple[int, ...]
    drops: tuple[int, ...] = ()
    drop_source: str = ""
    notes: tuple[str, ...] = ()

    @property
    def leaving(self) -> tuple[int, ...]:
        return (*self.gives, *self.drops)


def playoff_window(session: Session, league_season: LeagueSeason) -> tuple[int, int] | None:
    """First and last scoring period of the season's playoff matchup periods.

    None when the season stores none, which is a season whose schedule was
    never ingested past the regular season rather than a league without
    playoffs.
    """
    row = session.execute(
        select(
            func.min(MatchupPeriod.first_scoring_period),
            func.max(MatchupPeriod.final_scoring_period),
        ).where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(True),
        )
    ).one()
    first, last = row
    if first is None or last is None:
        return None
    return int(first), int(last)


def evaluate_trade(
    session: Session,
    league_season: LeagueSeason,
    today: int,
    side_a: TeamOffer,
    side_b: TeamOffer,
    *,
    drops: Mapping[int, Sequence[int]] | None = None,
    review_days: int = TRADE_REVIEW_DAYS,
    hurdle: float = TRADE_HURDLE,
    tilt: bool = True,
    distributions: Sequence[CategoryDistribution] | None = None,
    pool: Sequence[int] | None = None,
) -> TradeReport:
    """Judge a proposed trade from both sides, as of the morning of `today`.

    `drops` names, per ESPN team id, the men a side drops to make room; a side
    that needs room and names nobody drops the cheapest man on its active
    roster. `pool` names the wire instead of reading it, for a test or a
    calibration run, and `distributions` stands in for the league's measured
    category spreads. `review_days` is the days before the deal can be in a
    lineup (see the module docstring); `tilt` switches the minutes tilt in the
    projections.

    Raises `ValueError` when a named player is not on the roster he is being
    traded from, when a side names a drop it does not hold, or when the season
    has no matchup periods to plan over.
    """
    first_day, last_day, today = horizon(session, league_season, today)
    season = int(league_season.season)
    effective_day = today + max(0, review_days)
    days = tuple(range(today, last_day + 1))
    weeks = weeks_between(today, last_day)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
    categories = tuple(distribution.abbreviation for distribution in distributions)

    sides = _open(session, league_season, today, (side_a, side_b), drops)
    held = {player_id for side in sides for player_id in side.active}

    wire = tuple(
        player
        for player in load_free_agents(
            session, league_season, sides[0].week, player_ids=pool, days=days
        )
        if player.player_id not in held
    )
    historical_wire = pool is None and not has_free_agent_snapshots(session, league_season)

    everyone = sorted(held | {player.player_id for player in wire})
    weekly, _weeks = weekly_lines(session, league_season, everyone, today, tilt=tilt, as_of=as_of)
    players = {
        player.player_id: player for player in build_players(session, league_season, everyone, days)
    }
    lens = standard_lens(session, league_season, today, distributions)

    playoffs = playoff_window(session, league_season)
    playoff_days, playoff_weeks = _playoff_days(playoffs, today)
    playoff_weekly, playoff_games = _playoff_lines(
        session,
        league_season,
        everyone,
        today,
        playoff_days,
        playoff_weeks,
        tilt=tilt,
        as_of=as_of,
    )

    notes: list[str] = []
    if not lens.measured:
        notes.append(
            "no league standard is measurable yet -- nothing has been posted this season "
            "or any earlier one -- so the rest-of-season half of every judgement is zero"
        )
    if historical_wire:
        notes.append(
            "the wire is rebuilt from who played and was in nobody's lineup, because the "
            "listener never ran for this season; it cannot see a free agent who did not play"
        )
    if effective_day > last_day:
        notes.append(
            f"the deal could not be in a lineup before day {effective_day}, which is past "
            f"day {last_day}, the last day this report plans for"
        )

    built = tuple(
        _judge_side(
            session,
            league_season,
            today=today,
            as_of=as_of,
            effective_day=effective_day,
            weeks=weeks,
            side=side,
            other=other,
            weekly=weekly,
            playoff_weekly=playoff_weekly,
            playoff_games=playoff_games,
            playoff_days=playoff_days,
            playoff_weeks=playoff_weeks,
            playoff_window_days=playoffs,
            players=players,
            wire=tuple(player.player_id for player in wire),
            lens=lens,
            categories=categories,
            distributions=distributions,
            hurdle=hurdle,
            tilt=tilt,
        )
        for side, other in ((sides[0], sides[1]), (sides[1], sides[0]))
    )

    return TradeReport(
        season=season,
        today=today,
        effective_day=effective_day,
        review_days=max(0, review_days),
        review_source=TRADE_REVIEW_SOURCE,
        first_scoring_period=first_day,
        last_scoring_period=last_day,
        weeks_remaining=weeks,
        sides=(built[0], built[1]),
        hurdle=hurdle,
        pool_size=len(wire),
        historical_wire=historical_wire,
        notes=tuple(notes),
    )


def _open(
    session: Session,
    league_season: LeagueSeason,
    today: int,
    offers: tuple[TeamOffer, TeamOffer],
    drops: Mapping[int, Sequence[int]] | None,
) -> tuple[_Side, _Side]:
    """Both sides' rosters and what each of them is giving and receiving.

    Checks the offer against the roster here rather than later, so a typo in a
    player id is a sentence and not a silently strange number. A season whose
    rosters are not stored at all -- the preseason, before a draft -- is the
    one case where the check is skipped, with a note, because there is no
    roster to be on.
    """
    named: Mapping[int, Sequence[int]] = drops or {}
    built: list[_Side] = []
    for offer, other in ((offers[0], offers[1]), (offers[1], offers[0])):
        week = load_team_week(session, league_season, offer.team_id, today)
        team = team_row(session, league_season, offer.team_id)
        active = tuple(player.player_id for player in week.active)
        notes: list[str] = []
        if not week.roster:
            notes.append(
                f"{team.name} has no roster stored on day {today}, so nothing could be "
                "checked against it and the judgement is about the players alone"
            )
        else:
            missing = [player_id for player_id in offer.gives if player_id not in set(active)]
            if missing:
                raise ValueError(
                    f"{team.name} does not have "
                    + ", ".join(str(player_id) for player_id in missing)
                    + f" on its active roster on day {today}"
                )
        built.append(
            _Side(
                offer=offer,
                team_name=team.name,
                week=week,
                active=active,
                gives=tuple(offer.gives),
                receives=tuple(other.gives),
                notes=tuple(notes),
            )
        )
    return _with_drops(built[0], named), _with_drops(built[1], named)


def _with_drops(side: _Side, named: Mapping[int, Sequence[int]]) -> _Side:
    """Whoever this side has to drop to fit the men arriving, and why.

    A side that receives more than it gives needs a place for each extra man.
    Open places take them first; after that somebody goes. The caller's names
    are honoured whether or not room is needed -- a manager may want a
    particular man gone -- and the rest are the cheapest on the active roster,
    which is `app.pickups.season`'s drop candidate read one at a time.

    The cheapest is settled later, once values are read; here the count is
    fixed and any named men are checked.
    """
    wanted = list(dict.fromkeys(int(player_id) for player_id in named.get(side.offer.team_id, ())))
    on_roster = set(side.active)
    if side.active:
        unknown = [player_id for player_id in wanted if player_id not in on_roster]
        if unknown:
            raise ValueError(
                f"{side.team_name} cannot drop "
                + ", ".join(str(player_id) for player_id in unknown)
                + ": not on its active roster"
            )
    clash = [player_id for player_id in wanted if player_id in set(side.gives)]
    if clash:
        raise ValueError(
            f"{side.team_name} cannot both trade away and drop "
            + ", ".join(str(player_id) for player_id in clash)
        )
    return replace(side, drops=tuple(wanted), drop_source="named" if wanted else "")


def _needed_drops(side: _Side) -> int:
    """Places the arriving men need that the roster does not already have."""
    arriving = len(side.receives)
    leaving = len(side.gives)
    return max(0, arriving - leaving - side.week.open_slots)


def _judge_side(
    session: Session,
    league_season: LeagueSeason,
    *,
    today: int,
    as_of: date | None,
    effective_day: int,
    weeks: float,
    side: _Side,
    other: _Side,
    weekly: Mapping[int, CategoryLine],
    playoff_weekly: Mapping[int, CategoryLine],
    playoff_games: Mapping[int, int],
    playoff_days: tuple[int, ...],
    playoff_weeks: float,
    playoff_window_days: tuple[int, int] | None,
    players: Mapping[int, RosteredPlayer],
    wire: Sequence[int],
    lens: Standard,
    categories: Sequence[str],
    distributions: Sequence[CategoryDistribution],
    hurdle: float,
    tilt: bool,
) -> SideReport:
    """Everything one side of the deal is worth, and what it rests on."""
    # `weeks_remaining` is the periods after the one `today` falls in, which
    # is what `load_spots` reads, and it is right whether or not the deal
    # lands inside that period. Landing inside it, `delta_week` owns the
    # period and the season term must not. Landing after it -- a deal judged
    # on the last day of a week, or with a long review -- `delta_week` is zero
    # because no day of this period is the new roster's, and the period the
    # deal does land in is one of the weeks the season term covers. Neither
    # way is a day counted twice or dropped.
    spots = load_spots(
        session,
        league_season,
        side.offer.team_id,
        today,
        roster=side.active,
        wire=wire,
        weekly=weekly,
        distributions=distributions,
    )

    side = _settle_drops(side, spots)
    leaving = side.leaving
    receiving = side.receives

    opponent_move: tuple[Sequence[int], Sequence[int]] | None = None
    if side.week.opponent_team_id == other.offer.team_id:
        opponent_move = (other.receives, other.leaving)
    delta_week = week_deltas(
        session,
        league_season,
        side.offer.team_id,
        today,
        [(receiving, leaving)],
        tilt=tilt,
        distributions=distributions,
        effective_day=effective_day,
        opponent_move=opponent_move,
    )[0]

    best_on_the_wire = _best_wire(wire, spots)
    opened = max(0, len(leaving) - len(receiving))
    used = max(0, min(side.week.open_slots, len(receiving) - len(leaving)))

    filler = weekly.get(best_on_the_wire, CategoryLine()) if best_on_the_wire is not None else None
    before_line = sum_lines(weekly.get(player_id, CategoryLine()) for player_id in side.active)
    after_line = _after(before_line, weekly, leaving, receiving, filler, opened)

    independent = _independent_season(spots, leaving=leaving, receiving=receiving)
    judgement = judge(
        spots,
        delta_week=delta_week,
        dropped=leaving,
        added=receiving,
        delta_season_per_week=_roster_season(spots, lens, before_line, after_line),
    )
    replacement = judgement.replacement

    card = _card_builder(
        session,
        league_season,
        today=today,
        as_of=as_of,
        lens=lens,
        weekly=weekly,
        playoff_games=playoff_games,
        players=players,
    )
    lens_playoffs = _playoff_lens(
        side,
        playoff_weekly=playoff_weekly,
        playoff_games=playoff_games,
        playoff_days=playoff_days,
        playoff_weeks=playoff_weeks,
        window=playoff_window_days,
        wire=wire,
        lens=lens,
        categories=categories,
        distributions=distributions,
        filler=None if best_on_the_wire is None else playoff_weekly.get(best_on_the_wire),
        opened=opened,
    )

    built = SideReport(
        team_id=side.offer.team_id,
        team_name=side.team_name,
        receives=tuple(card(player_id) for player_id in receiving),
        gives=tuple(card(player_id) for player_id in side.gives),
        drops=tuple(card(player_id) for player_id in side.drops),
        drop_source=side.drop_source,
        places_opened=opened,
        places_used=used,
        judgement=judgement,
        season_independent=independent,
        categories=_views(before_line, after_line, categories, distributions),
        playoffs=lens_playoffs,
        replacement=replacement,
        replacement_player=None if best_on_the_wire is None else card(best_on_the_wire),
        hurdle=hurdle,
        expected_per_week=spots.expected_per_week,
        summary="",
        notes=side.notes + _side_notes(side, weeks, opened, used),
    )
    return replace(built, summary=summarise(built))


def _roster_season(
    spots: SpotBook, lens: Standard, before: CategoryLine, after: CategoryLine
) -> float:
    """The season term: this roster's ordinary week, with the deal and without.

    Revision R1 (docs/trades.md section 7a). Expected categories won in a week
    by the active roster as the deal leaves it, less the same roster as it
    stands -- the two lines the nine-category table is already drawn from, so
    the headline number and the table are the same arithmetic read two ways.

    It is the with-and-without the brief asked for and the first cut did not
    do. `_independent_season` values each man on his own inside a
    league-average team, and expected wins is a sum of *saturating*
    probabilities: a roster already winning rebounds gains nothing from more
    of them, and two starters routinely beat one superstar. Valuing each man
    separately cannot see either, which is what over-rated consolidation by
    +0.27 categories a week in the calibration of 2026-09-21.

    Zero when the season has posted nothing to measure a league standard
    against, exactly as the per-man term was: a number with no basis is worse
    than no number.
    """
    if not spots.measured:
        return 0.0
    return lens.week_wins(after) - lens.week_wins(before)


def _independent_season(
    spots: SpotBook, *, leaving: Sequence[int], receiving: Sequence[int]
) -> float:
    """The old headline, kept beside the new one so both can be measured.

    `places_cost` over the men in the deal: each place vacated worth the
    better of the man in it and the wire, each place filled worth the better
    of the man arriving and the wire. It is what a pickup is still charged by
    (`app.pickups.judge.judge`), and it is comparable across teams, which a
    roster with-and-without is not. It is no longer what a trade's headline
    reads, and `docs/trades.md` section 7 says why.
    """
    if not spots.measured:
        return 0.0
    return -places_cost(
        [spots.value(player_id) for player_id in leaving],
        [spots.value(player_id) for player_id in receiving],
        spots.replacement(exclude=receiving),
    )


def _settle_drops(side: _Side, spots: SpotBook) -> _Side:
    """Fill out the named drops with the cheapest men, when room is still needed.

    Cheapest by what his roster place is worth through the league-standard
    lens, which is the same ordering `app.pickups.season` ranks its drop
    candidates by. Men on injured reserve are not candidates: dropping one
    frees no active place, so it would not make the room the deal needs.
    """
    short = _needed_drops(side) - len(side.drops)
    if short <= 0:
        return side
    spoken_for = set(side.gives) | set(side.drops)
    cheapest = sorted(
        (player_id for player_id in side.active if player_id not in spoken_for),
        key=lambda player_id: (spots.value(player_id), player_id),
    )[:short]
    if not cheapest:
        return replace(
            side,
            notes=(
                *side.notes,
                f"{side.team_name} has nobody left to drop, so the deal does not fit "
                "its roster as described",
            ),
        )
    source = "named and cheapest" if side.drops else "cheapest"
    return replace(side, drops=(*side.drops, *cheapest), drop_source=source)


def _best_wire(wire: Sequence[int], spots: SpotBook) -> int | None:
    """The free agent the wire replacement is, or None when the floor binds."""
    ranked = sorted(wire, key=lambda player_id: (-spots.value(player_id), player_id))
    if not ranked or spots.value(ranked[0]) < spots.floor:
        return None
    return ranked[0]


def _after(
    before: CategoryLine,
    weekly: Mapping[int, CategoryLine],
    leaving: Sequence[int],
    receiving: Sequence[int],
    filler: CategoryLine | None,
    opened: int,
) -> CategoryLine:
    """The ordinary week the roster posts once the deal is done.

    A place the deal leaves open is shown filled by the best man on the wire,
    because that is exactly what the judgement charges it at; leaving it empty
    would show a two-for-one losing in every category while the net said it
    was fine.
    """
    line = before
    for player_id in leaving:
        line = line - weekly.get(player_id, CategoryLine())
    for player_id in receiving:
        line = line + weekly.get(player_id, CategoryLine())
    if opened and filler is not None:
        line = line + filler.scaled(float(opened))
    return line


def _views(
    before: CategoryLine,
    after: CategoryLine,
    categories: Sequence[str],
    distributions: Sequence[CategoryDistribution],
) -> tuple[CategoryView, ...]:
    """The nine, one by one: counts and the chance of winning each in a week."""
    before_totals = before.totals(categories)
    after_totals = after.totals(categories)
    before_p = category_wins(before, distributions)
    after_p = category_wins(after, distributions)
    return tuple(
        CategoryView(
            abbreviation=key,
            before=before_totals[key],
            after=after_totals[key],
            p_before=before_p.get(key, 0.0),
            p_after=after_p.get(key, 0.0),
        )
        for key in categories
    )


def _side_notes(side: _Side, weeks: float, opened: int, used: int) -> tuple[str, ...]:
    out: list[str] = []
    if side.drops and side.drop_source != "named":
        out.append(
            f"{side.team_name} has to drop {len(side.drops)} to fit the deal; the report "
            "chose the cheapest place on its roster, and a manager may prefer another"
        )
    if used:
        out.append(f"{used} of the men arriving take open roster places rather than a drop")
    if opened:
        out.append(
            f"the deal leaves {opened} roster place(s) open, valued at what the wire gives "
            "a place back"
        )
    if side.week.on_bye:
        out.append("this side is on a bye, so the week half of its judgement is zero")
    if weeks <= 1.0:
        out.append("a week or less is left to plan over, so this is almost all one matchup")
    return tuple(out)


def _playoff_days(window: tuple[int, int] | None, today: int) -> tuple[tuple[int, ...], float]:
    """The playoff scoring periods still ahead, and the weeks they make."""
    if window is None:
        return (), 0.0
    first, last = window
    first = max(first, today)
    if first > last:
        return (), 0.0
    return tuple(range(first, last + 1)), weeks_between(first, last)


def _playoff_lines(
    session: Session,
    league_season: LeagueSeason,
    player_ids: Sequence[int],
    today: int,
    playoff_days: tuple[int, ...],
    playoff_weeks: float,
    *,
    tilt: bool,
    as_of: date | None,
) -> tuple[dict[int, CategoryLine], dict[int, int]]:
    """Each player's line per playoff week, and his games in that window.

    The rate is the one knowable on `today` -- nothing about March is read in
    January -- and only the games change: his NBA team's, over the playoff
    weeks, less the days ESPN has ruled him out of.
    """
    if not playoff_days or playoff_weeks <= 0:
        return {}, {}
    season = int(league_season.season)
    lines: dict[int, CategoryLine] = {}
    games: dict[int, int] = {}
    for player in build_players(session, league_season, player_ids, playoff_days):
        count = player.games_remaining_this_period
        games[player.player_id] = count
        lines[player.player_id] = rest_of_season_line(
            session, season, player.player_id, today, count, tilt=tilt, as_of=as_of
        ).scaled(1.0 / playoff_weeks)
    return lines, games


def _playoff_lens(
    side: _Side,
    *,
    playoff_weekly: Mapping[int, CategoryLine],
    playoff_games: Mapping[int, int],
    playoff_days: tuple[int, ...],
    playoff_weeks: float,
    window: tuple[int, int] | None,
    wire: Sequence[int],
    lens: Standard,
    categories: Sequence[str],
    distributions: Sequence[CategoryDistribution],
    filler: CategoryLine | None,
    opened: int,
) -> PlayoffLens:
    """The deal over the playoff weeks alone, or why it cannot be counted."""
    first = playoff_days[0] if playoff_days else None
    last = playoff_days[-1] if playoff_days else None
    involved = (*side.leaving, *side.receives)
    games = sum(playoff_games.get(player_id, 0) for player_id in involved)
    empty = PlayoffLens(
        first_scoring_period=first,
        last_scoring_period=last,
        weeks=playoff_weeks,
        delta_per_week=0.0,
        categories=(),
        games=games,
    )
    if window is None:
        return replace(empty, note="this season stores no playoff matchup periods")
    if not playoff_days:
        return replace(empty, note="the playoff weeks are already behind this day")
    if not games:
        return replace(
            empty,
            note=(
                "no games are scheduled in the playoff weeks for the men in this deal; "
                "the stored NBA schedule does not reach them"
            ),
        )
    if not lens.measured:
        return replace(empty, note="no league standard is measurable, so nothing can be valued")

    def value(player_id: int) -> float:
        return lens.value(playoff_weekly.get(player_id, CategoryLine()))

    replacement = max(
        [TYPICAL_PICKUP, *(value(player_id) for player_id in wire)] if wire else [TYPICAL_PICKUP]
    )
    cost = places_cost(
        [value(player_id) for player_id in side.leaving],
        [value(player_id) for player_id in side.receives],
        replacement,
    )
    before = sum_lines(playoff_weekly.get(player_id, CategoryLine()) for player_id in side.active)
    after = _after(before, playoff_weekly, side.leaving, side.receives, filler, opened)
    return replace(
        empty,
        delta_per_week=-cost,
        categories=_views(before, after, categories, distributions),
    )


def _card_builder(
    session: Session,
    league_season: LeagueSeason,
    *,
    today: int,
    as_of: date | None,
    lens: Standard,
    weekly: Mapping[int, CategoryLine],
    playoff_games: Mapping[int, int],
    players: Mapping[int, RosteredPlayer],
) -> Callable[[int], PlayerCard]:
    """A function from player id to his `PlayerCard`, reading the rows once each.

    The same `today` and `as_of` the projections were built on, so what the
    card says the line rests on is what the line actually rests on.
    """
    season = int(league_season.season)
    cache: dict[int, PlayerCard] = {}

    def card(player_id: int) -> PlayerCard:
        found = cache.get(player_id)
        if found is not None:
            return found
        player = players.get(player_id)
        known = knowable(session, player_id, season, today, as_of=as_of)
        built = PlayerCard(
            player_id=player_id,
            name=player.name if player is not None else f"player {player_id}",
            value=lens.value(weekly.get(player_id, CategoryLine())),
            games_left=player.games_remaining_this_period if player is not None else 0,
            playoff_games=playoff_games.get(player_id, 0),
            injury_status=player.injury_status if player is not None else None,
            expected_return_date=player.expected_return_date if player is not None else None,
            games_so_far=known.games_so_far,
            had_projection=known.had_projection,
            projection_source=known.source,
        )
        cache[player_id] = built
        return built

    return card
