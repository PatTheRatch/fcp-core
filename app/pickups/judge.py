"""One currency for a move: categories, over both horizons.

Every move the recommender can make -- a stream for the week, a swap for the
rest of the year -- is judged here and nowhere else, so the two halves cannot
drift apart or disagree about what a player is worth.

THE PRINCIPLE

A move is worth making when it wins more categories, counting both horizons
at once:

    delta_total = delta_week + delta_season_per_week * weeks_remaining

`delta_week` is the change in expected categories won in the matchup in front
of us, from the head-to-head (`app.pickups.stream.head_to_head`).
`delta_season_per_week` is the change in what the roster spot yields in an
ordinary week from then on, and `weeks_remaining` counts the matchup weeks
*after* this one, so the same days are never counted twice. A move that adds
a category this week and costs 0.3 a week over fifteen weeks left is -3.5, and
is refused. That refusal is the whole point of this module.

WHAT DROPPING A PLAYER COSTS

Not his rest-of-season value. The roster place never goes empty: the man who
leaves is replaced, this week or next, by whoever the wire offers. So the
season charge is his value *less what the wire gives back for that place*:

    season_cost = value(dropped) - max(value(added), wire_replacement)

`wire_replacement` is the best free agent still available after the one being
added, floored at `TYPICAL_PICKUP`. The floor is what makes streaming a fringe
player free: a man worth about what the wire is worth costs about nothing to
drop. The same term is what makes a genuine keeper count for more than a
streamer on the way in, since a streamer's place can be re-streamed next week
and a keeper's cannot be improved on -- `max` takes the better of "he stays"
and "the place is streamed again", which is principle 3 of the brief.

A dropped good player is assumed gone: another manager takes him, and he is
not coming back for the price of a waiver claim.

VALUE MEANS THE LEAGUE STANDARD

`value()` here is `app.scoring.value.marginal` of a player's rest-of-season
weekly line inside the league-average team (`app.scoring.league.average_team_line`)
against `category_distributions`: the average team with his line in it,
against the average team without it. That is the "league standard" lens
`app/scoring/players.py` grades a played season with, and it is the right one
for a drop charge because it is comparable across players and blind to fit --
what a man is worth to whoever picks him up, not to the team letting him go.
Fit is already priced in `delta_week` and, for the rest-of-season report, in
the optimizer's own with-and-without.

THE PROJECTED RECORD

Every judgement carries the season's category record as it would end, with
the move and without it: the categories already banked in settled matchups,
plus the expected categories a week over the weeks left. The difference
between the two is exactly `delta_total`, which is what makes the net number
readable -- "this move is worth two thirds of a category on the season" is a
sentence a manager can act on, and a change in expected wins per week is not.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Matchup, MatchupPeriod, MatchupTeamStat, Team
from app.draft.targets import CategoryDistribution, category_distributions, modal_period_days
from app.pickups.projection import rest_of_season_line
from app.pickups.state import build_players, team_row
from app.scoring.league import average_team_line
from app.scoring.lines import CategoryLine
from app.scoring.value import expected_wins, marginal

#: Categories a week a typical waiver pickup has returned in this league, the
#: floor under what the wire gives back for a roster place. Measured by
#: `app.scoring.replacement` over every executed add of every season: the
#: median ran 0.062 (2025) to 0.128 (2020), and 0.072 in 2026. The lowest
#: recent season is taken rather than the mean, because a floor that is too
#: high would charge nothing for dropping an ordinary player.
TYPICAL_PICKUP = 0.06

#: Days in a matchup period, the unit the opponent distributions are measured
#: over and so the unit a rest-of-season line is divided into.
DAYS_A_WEEK = 7.0

#: Categories contested in a matchup. Read from the season's own scored
#: categories where one is at hand; this is the fallback for the record
#: arithmetic when a report carries no distributions.
NINE = 9

#: A category result, as `matchup_team_stats.result` spells it.
WIN = "WIN"
TIE = "TIE"


def weeks_between(first: int, last: int) -> float:
    """Whole days turned into weeks, never less than a single day's worth."""
    return max(1, last - first + 1) / DAYS_A_WEEK


def horizon(session: Session, league_season: LeagueSeason, today: int) -> tuple[int, int, int]:
    """The stretch a rest-of-season answer plans over: (first, last, today).

    The regular season while it lasts, since that is what the standings are
    decided on, and the playoff periods once it is over. Raises when the
    season has no matchup periods at all, which means nothing to plan over.

    It lives here rather than in `app.pickups.season` because both halves of
    the recommender now need the same horizon: the week's report has to know
    how many weeks a drop is charged over.
    """
    rows = session.execute(
        select(
            MatchupPeriod.is_playoff,
            func.min(MatchupPeriod.first_scoring_period),
            func.max(MatchupPeriod.final_scoring_period),
        )
        .where(MatchupPeriod.league_season_id == league_season.id)
        .group_by(MatchupPeriod.is_playoff)
    ).all()
    windows = {
        bool(is_playoff): (int(first), int(last))
        for is_playoff, first, last in rows
        if first is not None and last is not None
    }
    regular = windows.get(False)
    playoffs = windows.get(True)
    if regular is not None and (today <= regular[1] or playoffs is None):
        first, last = regular
    elif playoffs is not None:
        first, last = playoffs
    else:
        raise ValueError(f"season {league_season.season} has no matchup periods to plan over")
    return first, last, min(max(today, first), last)


@dataclass(frozen=True)
class Standard:
    """The league-standard lens: what a weekly line gives an ordinary place.

    `average` is what the league's teams post in a period of the ordinary
    length; a line's value is the average team with it in against the average
    team without it. `measured` is False when the season has posted nothing to
    average yet, and then every value is zero and the season term is dropped
    rather than guessed at.
    """

    average: CategoryLine
    distributions: tuple[CategoryDistribution, ...]

    @property
    def measured(self) -> bool:
        return bool(self.average.counts) and bool(self.distributions)

    def value(self, weekly: CategoryLine) -> float:
        """Categories a week `weekly` gives an ordinary roster place."""
        if not self.measured or not weekly.counts:
            return 0.0
        return marginal(self.average, weekly, self.distributions)

    def week_wins(self, weekly: CategoryLine) -> float:
        """Expected categories a roster posting `weekly` wins in a week."""
        if not self.distributions:
            return 0.0
        return expected_wins(weekly, self.distributions)


def standard_lens(
    session: Session,
    league_season: LeagueSeason,
    today: int,
    distributions: Sequence[CategoryDistribution] | None = None,
) -> Standard:
    """The lens for `today`: the average team of this period's length.

    The period's own length, as `app/scoring/players.py` does it, because the
    All-Star fortnight posts about a third more of everything. A length the
    season has never averaged falls back to the ordinary one, and a season
    with no results at all falls back to the newest one that has some: a
    league standard from last year is a great deal better than none.
    """
    if distributions is None:
        distributions = category_distributions(session, league_season)
    season = int(league_season.season)
    period = session.scalar(
        select(MatchupPeriod).where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.first_scoring_period <= today,
            MatchupPeriod.final_scoring_period >= today,
        )
    )
    days = _period_days(period)
    average = average_team_line(session, season, days) if days else CategoryLine()
    if not average.counts:
        average = average_team_line(session, season)
    if not average.counts:
        average = _last_measured_average(session, season)
    return Standard(average=average, distributions=tuple(distributions))


def _period_days(period: MatchupPeriod | None) -> int | None:
    if period is None or period.first_scoring_period is None:
        return None
    if period.final_scoring_period is None:
        return None
    return int(period.final_scoring_period) - int(period.first_scoring_period) + 1


def _last_measured_average(session: Session, season: int) -> CategoryLine:
    """The newest earlier season that posted anything, or an empty line."""
    earlier = session.scalars(
        select(LeagueSeason.season)
        .where(LeagueSeason.season < season)
        .order_by(LeagueSeason.season.desc())
    ).all()
    for found in earlier:
        line = average_team_line(session, int(found), modal_period_days(session, [int(found)]))
        if line.counts:
            return line
    return CategoryLine()


def weekly_lines(
    session: Session,
    league_season: LeagueSeason,
    player_ids: Iterable[int],
    today: int,
    *,
    tilt: bool = True,
    as_of: date | None = None,
) -> tuple[dict[int, CategoryLine], float]:
    """Each player's rest-of-season line per week, and the weeks it covers.

    The same arithmetic `app.pickups.season` builds its optimizer candidates
    from: games counted over the horizon, the availability discount taken
    once, divided by the weeks in the window. Returned as lines rather than
    values so one lens can price them all.
    """
    _first, last, today = horizon(session, league_season, today)
    weeks = weeks_between(today, last)
    days = tuple(range(today, last + 1))
    players = build_players(session, league_season, player_ids, days)
    season = int(league_season.season)
    return {
        player.player_id: rest_of_season_line(
            session,
            season,
            player.player_id,
            today,
            player.games_remaining_this_period,
            tilt=tilt,
            as_of=as_of,
        ).scaled(1.0 / weeks)
        for player in players
    }, weeks


def season_cost(dropped_weekly: float, added_weekly: float, replacement: float) -> float:
    """What a swap costs the roster place over the rest of the season.

    All three are categories a week through the league-standard lens
    (`Standard.value`): what the man leaving was worth, what the man arriving
    is worth, and what the wire would give the place back if he were streamed
    away again (`wire_replacement`).

    The place is worth the better of keeping the new man and re-streaming it,
    so a keeper counts for what he is and a streamer is not punished for being
    one. Negative means the move improves the place, which is the usual case
    for an add into somewhere empty.
    """
    return dropped_weekly - max(added_weekly, replacement)


def _best_available(values: Mapping[int, float], exclude: Collection[int], floor: float) -> float:
    available = [value for player_id, value in values.items() if player_id not in exclude]
    return max([floor, *available])


def wire_replacement(
    session: Session,
    league_season: LeagueSeason,
    today: int,
    pool_lines: Mapping[int, CategoryLine],
    exclude: Collection[int] = (),
    *,
    floor: float = TYPICAL_PICKUP,
    distributions: Sequence[CategoryDistribution] | None = None,
) -> float:
    """What the wire gives an ordinary roster place back, categories a week.

    The best free agent still on the wire once the man being added is off it
    (`exclude`), valued through the league-standard lens, floored at the
    typical pickup this league has measured. `pool_lines` is the wire's
    rest-of-season weekly lines, which the caller already has.

    The floor matters more than the maximum. A day when the wire happens to
    hold a useful player would otherwise make every drop free; a day when it
    holds nobody would make every drop ruinous. The floor says the place is
    always worth at least what an ordinary pickup returns.
    """
    lens = standard_lens(session, league_season, today, distributions)
    return _best_available(
        {player_id: lens.value(line) for player_id, line in pool_lines.items()}, exclude, floor
    )


@dataclass(frozen=True)
class Judgement:
    """One move, in the one currency, over both horizons."""

    #: Change in expected categories won in the matchup in front of us.
    delta_week: float
    #: Change in expected categories won in an ordinary week from then on.
    delta_season_per_week: float
    #: Matchup weeks after this one, so the two terms never share a day.
    weeks_remaining: float
    #: What the wire would give the place back, categories a week.
    replacement: float
    #: Categories won and lost in settled matchups so far this season.
    banked: tuple[float, float]
    #: The season's category record as it would end without the move, and
    #: with it: (won, lost), one decimal.
    record_without: tuple[float, float]
    record_with: tuple[float, float]
    #: False when the season has posted nothing to measure a league standard
    #: against, so the season term is zero rather than a guess.
    measured: bool

    @property
    def delta_total(self) -> float:
        """The net: this week's gain plus the season's, in categories."""
        return self.delta_week + self.delta_season_per_week * self.weeks_remaining

    @property
    def weeks_covered(self) -> float:
        """Weeks the net spans: this one and the ones after it."""
        return self.weeks_remaining + 1.0

    @property
    def per_week(self) -> float:
        """The net spread over the weeks it covers, for a per-week hurdle."""
        return self.delta_total / self.weeks_covered


@dataclass(frozen=True)
class SpotBook:
    """Everything a judgement needs, read once for a whole report.

    A streaming report ranks a thousand moves, so nothing here may cost a
    query per move: the lens, every player's weekly value, the banked record
    and the ordinary week are all built once and then only added up.
    """

    lens: Standard
    #: Player id -> categories a week he gives an ordinary place.
    values: Mapping[int, float]
    #: The free agents among them, whose values the wire replacement is over.
    wire: frozenset[int]
    #: Matchup weeks after this one.
    weeks_remaining: float
    #: Categories won and lost in settled matchups so far.
    banked: tuple[float, float]
    #: Expected categories the roster wins in an ordinary week as it stands.
    expected_per_week: float
    #: The floor under what the wire gives a place back.
    floor: float = TYPICAL_PICKUP

    @property
    def measured(self) -> bool:
        return self.lens.measured

    def value(self, player_id: int) -> float:
        return self.values.get(player_id, 0.0)

    def replacement(self, exclude: Collection[int] = ()) -> float:
        """`wire_replacement`, from the values already read."""
        return _best_available(
            {pid: value for pid, value in self.values.items() if pid in self.wire},
            exclude,
            self.floor,
        )


def load_spots(
    session: Session,
    league_season: LeagueSeason,
    team_id: int,
    today: int,
    *,
    roster: Collection[int],
    wire: Collection[int],
    weekly: Mapping[int, CategoryLine],
    distributions: Sequence[CategoryDistribution] | None = None,
    floor: float = TYPICAL_PICKUP,
) -> SpotBook:
    """The book for one team on one day, from weekly lines the caller has.

    `weekly` covers the roster and the wire both; `roster` and `wire` say
    which is which, because the wire replacement is over free agents only and
    the ordinary week is over the roster only.
    """
    lens = standard_lens(session, league_season, today, distributions)
    values = {player_id: lens.value(line) for player_id, line in weekly.items()}
    held = [weekly[player_id] for player_id in roster if player_id in weekly]
    line = CategoryLine()
    for held_line in held:
        line = line + held_line
    return SpotBook(
        lens=lens,
        values=values,
        wire=frozenset(player_id for player_id in wire if player_id in weekly),
        weeks_remaining=weeks_after_this_period(session, league_season, today),
        banked=banked_record(session, league_season, team_id, today),
        expected_per_week=lens.week_wins(line),
        floor=floor,
    )


def weeks_after_this_period(session: Session, league_season: LeagueSeason, today: int) -> float:
    """Matchup weeks left once the current period is over.

    The current period is excluded because `delta_week` already owns it. A
    day in the last period of the stretch leaves nothing after it, which is
    zero weeks and a judgement that is entirely this week's.
    """
    _first, last, today = horizon(session, league_season, today)
    period = session.scalar(
        select(MatchupPeriod.final_scoring_period).where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.first_scoring_period <= today,
            MatchupPeriod.final_scoring_period >= today,
        )
    )
    ends = int(period) if period is not None else today
    return max(0.0, (last - ends) / DAYS_A_WEEK)


def banked_record(
    session: Session, league_season: LeagueSeason, team_id: int, today: int
) -> tuple[float, float]:
    """Categories won and lost in regular-season matchups already finished.

    ESPN records a result per category per matchup, so the record is counted
    rather than inferred from the totals; a tie is half to each side, the
    convention `app.scoring.league.category_record` uses for its shares. Only
    periods whose last day is behind `today` count, so nothing here knows the
    result of a week still being played.
    """
    team = team_row(session, league_season, team_id)
    rows = session.execute(
        select(MatchupTeamStat.result, func.count())
        .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .join(Team, Team.id == MatchupTeamStat.team_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(False),
            MatchupPeriod.final_scoring_period < today,
            Matchup.away_team_id.is_not(None),
            MatchupTeamStat.team_id == team.id,
            MatchupTeamStat.league_season_category_id.is_not(None),
            MatchupTeamStat.result.is_not(None),
        )
        .group_by(MatchupTeamStat.result)
    ).all()
    counts = {str(result): int(count) for result, count in rows}
    ties = counts.get(TIE, 0) / 2.0
    won = counts.get(WIN, 0) + ties
    lost = sum(count for result, count in counts.items() if result not in (WIN, TIE)) + ties
    return won, lost


def judge(
    spots: SpotBook,
    *,
    delta_week: float,
    dropped: Sequence[int] = (),
    added: Sequence[int] = (),
    delta_season_per_week: float | None = None,
    categories: int = NINE,
) -> Judgement:
    """Judge one move: this week, the rest of the season, and the record.

    `dropped` and `added` are player ids; a free add drops nobody and an
    injured-reserve move drops nobody either, since the man keeps his place.
    `delta_season_per_week` overrides the season term for a caller that has
    measured it another way -- the rest-of-season report hands the optimizer's
    own change per week, which is a with-and-without over the whole roster and
    so already charges the drop in team-fit terms.
    """
    replacement = spots.replacement(exclude=added)
    if delta_season_per_week is None:
        cost = season_cost(
            sum(spots.value(player_id) for player_id in dropped),
            sum(spots.value(player_id) for player_id in added),
            replacement * max(1, len(added)) if added else 0.0,
        )
        delta_season_per_week = -cost if spots.measured else 0.0
    weeks = spots.weeks_remaining
    won, lost = spots.banked
    without = (
        won + spots.expected_per_week * weeks,
        lost + (categories - spots.expected_per_week) * weeks,
    )
    net = delta_week + delta_season_per_week * weeks
    return Judgement(
        delta_week=delta_week,
        delta_season_per_week=delta_season_per_week,
        weeks_remaining=weeks,
        replacement=replacement,
        banked=spots.banked,
        record_without=(round(without[0], 1), round(without[1], 1)),
        record_with=(round(without[0] + net, 1), round(without[1] - net, 1)),
        measured=spots.measured,
    )
