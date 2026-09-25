"""Where every team finishes: each remaining week, head to head, summed.

The question a manager asks all season. The pickup judgement already answers
it for one team against a league-average opponent
(`app.pickups.judge.Judgement.record_without`); the trade evaluator answers it
for two. This module answers it for the whole league, against the **real**
opponent each week, from the league's own stored matchup schedule.

WHAT IT DOES, IN ORDER

1. The horizon is `app.pickups.judge.horizon`: the rest of the regular season
   while the regular season lasts, the playoff rounds once it is over. Every
   matchup period inside it whose window is dated is a week to project.
2. For each team, a projected line for each of those weeks: today's roster,
   that period's NBA schedule, and the daily-lineup solve
   (`app.pickups.stream.seat`) -- the same seating the week report and the
   morning lineup use, so no two pages can disagree about who starts. The
   per-game rate is `app.pickups.projection.per_game_line` as of today, which
   is where the availability and the minutes tilt already live.
3. For the period in play, the line starts from what has been posted so far
   (`app.pickups.state.load_team_week`, whose live/replay rule decides whether
   that is ESPN's running tally or a sum of the started box scores) and adds
   only the days left. So "this week's" probabilities move as the week is
   played, and a day's games are never counted twice.
4. Head to head, per category, per week: `app.pickups.stream.head_to_head`.
5. Per team: the nine probabilities and their sum each week, the expected
   record over the rest of the regular season, the banked record
   (`app.pickups.judge.banked_record`), and their sum, the projected final
   record.
6. A Monte Carlo over those per-category probabilities gives the distribution
   of finishes: playoff odds, each seed's probability, and the bye odds where
   the format has one. Each simulated table is ordered by the league's own
   ranking rule (`app.scoring.ranking`): in a head-to-head each-category
   league, this one, that is category win share, not matchups won.

THE VARIANCE MODEL, WHICH IS THE ONE JUDGEMENT CALL HERE

P(A beats B in category c) is the normal probability that A's projected total
beats B's:

    P = Phi( (total_A - total_B) / (spread_c * sqrt(days_left / period_days)) )

`spread_c` is the standard deviation of **one team's** total in that category
over a period of the ordinary length, measured on this league's own results
(`app.draft.targets.CategoryDistribution`). Turnovers are inverted. FG% and
FT% are rates rebuilt from the projected makes and attempts
(`CategoryLine.totals`), never averaged. The square root scales the spread by
the share of the period still to play, so a category three days from the end
is nearly settled and one seven days out is not; with nothing left the result
is decided outright, and a level category is a coin, which is how
`app.scoring.league.category_record` counts a tie -- half to each side.

Two things about that formula are choices rather than facts.

The first is that the spread is one team's, not the spread of the
**difference** between two teams. If both sides' totals wobbled
independently with the same spread, the difference would wobble by
sqrt(2) times as much, and every probability here would sit nearer a coin.
The formula is kept as it is because it is `app.pickups.stream.head_to_head`
verbatim -- the function this module imports rather than copies -- and that
is what the pickup judgement, the streaming hurdle and the trade evaluator
are all priced on. A projected-standings page that used a different model
would quietly disagree with the week page about what a week is worth, and
the disagreement would be invisible. The right way to move it is to move it
in one place, after the calibration says which direction; see
`docs/projected_record.md`.

The second is that the nine categories are drawn independently in the
simulation. They are not independent in reality -- a roster with four games
on Sunday gains in most of them at once -- so a week's spread of outcomes
here is narrower than the truth, and the seed probabilities are a little more
confident than they should be. Both are stated on the page.

NO LOOK-AHEAD

Everything read is keyed on `today` or earlier: the roster from the latest
lineup day at or before it, the posted totals from the days of this period
**before** it, the per-game rates from box scores before it, the schedule
from `pro_team_games`, and the matchup pairings, which ESPN stores for the
whole season in advance and which are therefore knowable. The pairings are
the one thing here that is about the future and legitimate.

The **playoff bracket is not**. ESPN stores a bracket for a season already
played, because it happened; for a season in progress the playoff periods
carry no dates and their pairings depend on seeding nobody has yet earned.
So a projection made during the regular season covers the regular season
only, says so (`playoffs_projected` is False with a reason), and gets its
playoff odds from the simulated final table rather than from a bracket. Once
the regular season is over the horizon moves to the playoff periods and the
stored pairings are the real ones, so they are projected like any other week.

One thing is **not** proof against the future, and it is inherited rather
than added: `app.draft.targets.category_distributions` measures the league's
weekly spreads from every played season of this size, and on a season being
replayed that includes weeks after `today`. On a live season the rows do not
exist yet, so there is nothing to leak; on a replay there is. Callers who
need the replay to be clean -- `scripts/projected_calibration.py` is the one
that does -- pass `distributions=category_distributions(..., before=season)`,
and the payload records which basis it used.
"""

from __future__ import annotations

import bisect
import math
import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Matchup, MatchupPeriod, Team
from app.draft.pool import lineup_for
from app.draft.targets import CategoryDistribution, category_distributions
from app.pickups.judge import CONTESTED_CATEGORIES, banked_record, horizon
from app.pickups.projection import per_game_line
from app.pickups.state import RosteredPlayer, build_players, load_team_week, season_calendar
from app.pickups.stream import Contender, head_to_head, seat, weight
from app.scoring.lines import CategoryLine
from app.scoring.ranking import (
    RankingRule,
    Record,
    category_meetings,
    lookup,
    matchup_records,
    ranking_rule,
)

__all__ = [
    "BYE_NOTE",
    "NO_BRACKET",
    "N_SIMS",
    "SEED",
    "Projection",
    "TeamOutlook",
    "Week",
    "bye_seats",
    "final_table",
    "project_standings",
]

#: Simulated seasons. Ten thousand puts the standard error on a seed
#: probability under half a point (sqrt(0.25/10000) = 0.005), which is finer
#: than the one decimal place a page prints, and costs about a second: the
#: draw per matchup is one uniform, not nine (see `_category_wins`).
N_SIMS = 10_000

#: The seed the simulation is run from, so the same day gives the same
#: numbers whoever asks and however often. The league's own ESPN id, for want
#: of a more meaningful constant.
SEED = 3853870

# How the table is ordered is not a constant here any more: it is the
# league's ranking rule (`app.scoring.ranking.ranking_rule`), read off the
# scoring type, and `Projection.tiebreak` carries its words. Until 2026-09-25
# this module ordered by matchups won, which is not how a head-to-head
# each-category league is ranked (docs/projected_record.md, revision R6).
# Whatever the rule leaves level is broken at random, once per simulated
# season, rather than by team id -- which would hand the same team the better
# seed in every one of ten thousand draws.

#: What the payload says when the playoff rounds were left out.
NO_BRACKET = (
    "the playoff pairings depend on seeding that has not been decided, so only "
    "the regular season is projected; the playoff odds come from the simulated table"
)

#: What it says when the format gives the top seeds a first-round bye.
BYE_NOTE = "{n} of {of} teams make the playoffs, so the top {byes} sit out the first round"


def bye_seats(playoff_teams: int) -> int:
    """Seeds that skip the first round, from the size of the field alone.

    A bracket is played in rounds of two, so a field that is not a power of
    two starts with byes, and ESPN gives them to the top seeds. Seven of
    fourteen is one bye; eight of sixteen is none. Zero when nobody makes the
    playoffs at all, which is a season with the setting unset.
    """
    if playoff_teams < 2:
        return 0
    return int(2 ** math.ceil(math.log2(playoff_teams))) - playoff_teams


@dataclass(frozen=True)
class Week:
    """One team's one remaining matchup: who, when, and the nine chances."""

    period: int
    first_scoring_period: int
    final_scoring_period: int
    #: Days of the period still to play, today included. Equal to the whole
    #: period for a week that has not begun.
    days_remaining: int
    #: True for the period being played now, whose totals include what is
    #: already posted.
    in_play: bool
    #: ESPN's id of the other side, and its name; None on a bye.
    opponent_team_id: int | None
    opponent_name: str | None
    #: P(this team wins the category), one per scored category. Empty on a bye.
    probabilities: Mapping[str, float]
    #: Projected raw counts for this team and for the other side, this period.
    projected: CategoryLine
    opponent_projected: CategoryLine

    @property
    def expected_wins(self) -> float:
        """Expected categories won this week; zero on a bye."""
        return sum(self.probabilities.values())

    @property
    def on_bye(self) -> bool:
        return self.opponent_team_id is None


@dataclass(frozen=True)
class TeamOutlook:
    """One team's rest of season: the weeks, the record, and where it ends."""

    team_id: int
    name: str
    #: Categories won and lost in regular-season matchups already settled.
    banked: tuple[float, float]
    #: Matchups won, lost and tied in those same settled weeks.
    banked_matchups: tuple[int, int, int]
    weeks: tuple[Week, ...]
    #: P(finishing in each place), first place first; sums to one.
    finishes: tuple[float, ...]
    playoff_odds: float
    #: P(a first-round bye), or None where the format gives none.
    bye_odds: float | None
    #: The mean simulated matchup record at the end: won, lost, tied.
    projected_matchups: tuple[float, float, float]

    @property
    def contested_weeks(self) -> tuple[Week, ...]:
        return tuple(week for week in self.weeks if not week.on_bye)

    @property
    def expected(self) -> tuple[float, float]:
        """Categories still to be won and lost, over the weeks left."""
        won = sum(week.expected_wins for week in self.weeks)
        lost = sum(CONTESTED_CATEGORIES - week.expected_wins for week in self.contested_weeks)
        return won, lost

    @property
    def projected_record(self) -> tuple[float, float]:
        """Banked plus expected: the category record as it would end."""
        won, lost = self.expected
        return self.banked[0] + won, self.banked[1] + lost

    @property
    def expected_matchup_wins(self) -> float:
        return self.projected_matchups[0]


@dataclass(frozen=True)
class Projection:
    """The whole league's rest of season, shaped for a page."""

    league_id: int
    season: int
    #: The scoring period this was built for, and the day it falls on.
    as_of: int
    as_of_date: date | None
    #: The matchup period `as_of` sits in.
    matchup_period: int
    #: In projected order: the team most likely to finish first, first.
    teams: tuple[TeamOutlook, ...]
    #: Periods projected, ascending.
    periods: tuple[int, ...]
    playoff_team_count: int
    bye_count: int
    playoffs_projected: bool
    #: Why the playoff rounds were left out, or "" when they were not.
    playoff_note: str
    tiebreak: str
    n_sims: int
    seed: int
    #: Where the numbers came from, and what the weekly spreads were measured
    #: on, in the words the page prints.
    source_note: str
    basis: str

    def team(self, team_id: int) -> TeamOutlook | None:
        return next((one for one in self.teams if one.team_id == team_id), None)


# ---------------------------------------------------------------------------
# the weekly lines
# ---------------------------------------------------------------------------


def _periods(
    session: Session, league_season: LeagueSeason, today: int, last: int
) -> list[MatchupPeriod]:
    """The dated matchup periods left inside the horizon, ascending.

    A period with no window is not a week anybody can project: the playoff
    rounds of a season in progress carry none, which is the whole of
    `NO_BRACKET`.
    """
    return list(
        session.scalars(
            select(MatchupPeriod)
            .where(
                MatchupPeriod.league_season_id == league_season.id,
                MatchupPeriod.first_scoring_period.is_not(None),
                MatchupPeriod.final_scoring_period.is_not(None),
                MatchupPeriod.final_scoring_period >= today,
                MatchupPeriod.final_scoring_period <= last,
            )
            .order_by(MatchupPeriod.period)
        ).all()
    )


def _pairings(
    session: Session, periods: Sequence[MatchupPeriod]
) -> dict[int, list[tuple[int, int | None]]]:
    """Each period's pairings as (home team row id, away team row id or None)."""
    ids = [period.id for period in periods]
    if not ids:
        return {}
    rows = session.execute(
        select(Matchup.matchup_period_id, Matchup.home_team_id, Matchup.away_team_id)
        .where(Matchup.matchup_period_id.in_(ids))
        .order_by(Matchup.id)
    ).all()
    out: dict[int, list[tuple[int, int | None]]] = {period_id: [] for period_id in ids}
    for period_id, home, away in rows:
        out[int(period_id)].append((int(home), int(away) if away is not None else None))
    return out


def _contenders(
    session: Session,
    season: int,
    players: Sequence[RosteredPlayer],
    distributions: Sequence[CategoryDistribution],
    today: int,
    *,
    tilt: bool,
    as_of: date | None,
) -> dict[int, Contender]:
    """Each man as the seating values him: his per-game line and his weight.

    The same construction `app.pickups.stream` makes, and the same cache
    behind it -- `per_game_line` is keyed on the day, not the period, so a
    player is priced once however many weeks he is projected over.
    """
    out: dict[int, Contender] = {}
    for player in players:
        per_game = per_game_line(session, season, player.player_id, today, tilt=tilt, as_of=as_of)
        out[player.player_id] = Contender(
            player=player,
            per_game=per_game,
            weight=weight(per_game, distributions),
            days=frozenset(player.game_days),
        )
    return out


def _project(
    days: Sequence[int],
    lineup: Sequence[str],
    contenders: Sequence[Contender],
    totals: CategoryLine,
) -> CategoryLine:
    """What a roster adds over `days`, on top of `totals`.

    `app.pickups.stream.seat` a day at a time, which is the whole of the
    daily-lineup solve: ESPN starts ten men a day, so a game on a day the
    lineup is already full is a start going nowhere, and who sits is decided
    by the same per-game weight the week report orders by. Written here as a
    loop rather than reusing `stream._Week` because that class caches a
    seating per set of available ids for a search that tries a thousand
    rosters; this asks each roster once.
    """
    line = totals
    by_id = {contender.player_id: contender for contender in contenders}
    for day in days:
        available = sorted(
            (contender for contender in contenders if day in contender.days),
            key=lambda contender: (-contender.weight, contender.player_id),
        )
        for player_id in seat(available, lineup):
            line = line + by_id[player_id].per_game
    return line


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------


def _category_wins(probabilities: Sequence[float]) -> list[float]:
    """The cumulative distribution of how many categories a side takes.

    The nine per-category chances are independent Bernoulli draws, so the
    number won is Poisson-binomial; folding it out gives an exact 10-vector,
    and a simulated week is then one uniform against its cumulative form
    rather than nine. That is the same distribution, drawn nine times more
    cheaply, and it is what keeps ten thousand seasons to about a second.
    """
    dist = [1.0]
    for p in probabilities:
        nxt = [0.0] * (len(dist) + 1)
        for index, mass in enumerate(dist):
            nxt[index] += mass * (1.0 - p)
            nxt[index + 1] += mass * p
        dist = nxt
    running = 0.0
    out: list[float] = []
    for mass in dist:
        running += mass
        out.append(running)
    return out


@dataclass(frozen=True)
class _Draw:
    """One matchup in the simulation: two seats, and a week's cumulative law."""

    home: int
    away: int
    cumulative: tuple[float, ...]
    contested: int


def _simulate(
    draws: Sequence[_Draw],
    banked_matchups: Sequence[tuple[int, int, int]],
    banked_categories: Sequence[tuple[float, float]],
    *,
    teams: int,
    n_sims: int,
    seed: int,
    rule: RankingRule,
    banked_meetings: Mapping[tuple[int, int], tuple[float, float, float]] | None = None,
) -> tuple[list[list[float]], list[tuple[float, float, float]]]:
    """Seed probabilities per team, and the mean final matchup record.

    Each simulated season plays every remaining matchup once, adds the
    banked record, and orders the table by the league's ranking `rule`: in a
    category league, the category record -- won and lost, a banked tie
    already counted half to each -- and, among teams level on share, their
    category record against each other, banked meetings plus the ones this
    season drew. Whatever the rule leaves level is broken by a number drawn
    per team per season, because ordering by team id would hand the same
    team the better seed ten thousand times running.

    The random numbers are drawn in the same sequence whatever the rule, one
    per matchup and then one per team, so the simulated matchup record does
    not depend on how the table is ordered.
    """
    counts = [[0] * teams for _ in range(teams)]
    total_won = [0.0] * teams
    total_lost = [0.0] * teams
    total_tied = [0.0] * teams
    rng = random.Random(seed)
    order = list(range(teams))
    met_before = dict(banked_meetings or {})
    # Which draws each ordered pair met in, and whether the first was at home.
    pairs: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for at, draw in enumerate(draws):
        pairs.setdefault((draw.home, draw.away), []).append((at, True))
        pairs.setdefault((draw.away, draw.home), []).append((at, False))
    for _ in range(n_sims):
        won = [record[0] for record in banked_matchups]
        lost = [record[1] for record in banked_matchups]
        tied = [record[2] for record in banked_matchups]
        categories = [record[0] for record in banked_categories]
        conceded = [record[1] for record in banked_categories]
        takes = [0] * len(draws)
        for at, draw in enumerate(draws):
            taken = bisect.bisect_left(draw.cumulative, rng.random())
            takes[at] = taken
            categories[draw.home] += taken
            conceded[draw.home] += draw.contested - taken
            categories[draw.away] += draw.contested - taken
            conceded[draw.away] += taken
            if taken * 2 > draw.contested:
                won[draw.home] += 1
                lost[draw.away] += 1
            elif taken * 2 < draw.contested:
                won[draw.away] += 1
                lost[draw.home] += 1
            else:  # pragma: no cover - nine categories cannot split evenly
                tied[draw.home] += 1
                tied[draw.away] += 1
        coin = [rng.random() for _ in order]

        def met(a: int, b: int, takes: list[int] = takes) -> tuple[float, float, float]:
            w, lo, ti = met_before.get((a, b), (0.0, 0.0, 0.0))
            for at, at_home in pairs.get((a, b), ()):
                mine = takes[at] if at_home else draws[at].contested - takes[at]
                w, lo = w + mine, lo + draws[at].contested - mine
            return w, lo, ti

        table = [
            record.team
            for record in rule.order(
                [
                    Record(i, categories[i], conceded[i], 0.0, won[i], lost[i], tied[i])
                    for i in order
                ],
                met,
                coin.__getitem__,
            )
        ]
        for place, team in enumerate(table):
            counts[team][place] += 1
        for team in order:
            total_won[team] += won[team]
            total_lost[team] += lost[team]
            total_tied[team] += tied[team]
    finishes = [[count / n_sims for count in row] for row in counts]
    records = [
        (total_won[i] / n_sims, total_lost[i] / n_sims, total_tied[i] / n_sims) for i in order
    ]
    return finishes, records


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def _banked_matchups(
    session: Session, league_season: LeagueSeason, today: int
) -> dict[int, tuple[int, int, int]]:
    """Each team's matchup record in regular-season weeks already finished.

    `app.scoring.ranking.matchup_records`, the count the standings route
    makes -- a bye is not a win -- bounded at `today`, so no week still
    being played is in it.
    """
    return matchup_records(session, league_season, before=today)


def final_table(session: Session, league_season: LeagueSeason, today: int) -> tuple[int, ...]:
    """The league's order as it stands on `today`, by its ranking rule: ESPN team ids.

    The settled record alone, with nothing projected, ordered the way the
    simulation orders a finished season: in a category league, category win
    share, then the tied teams' record against each other, then categories
    won, then fewest lost. A caller past the last day of the season gets the
    table as it finally stood, which is what the calibration measures its
    playoff odds against. A complete tie stays in ESPN team id order.
    """
    rule = ranking_rule(league_season)
    matchups = _banked_matchups(session, league_season, today)
    teams = session.scalars(
        select(Team).where(Team.league_season_id == league_season.id).order_by(Team.espn_team_id)
    ).all()
    records = []
    for team in teams:
        won, lost = banked_record(session, league_season, int(team.espn_team_id), today)
        records.append(Record(team.id, won, lost, 0.0, *matchups.get(team.id, (0, 0, 0))))
    meetings = lookup(category_meetings(session, league_season, before=today))
    by_row = {team.id: team for team in teams}
    return tuple(int(by_row[record.team].espn_team_id) for record in rule.order(records, meetings))


def _source_note(season: int, distributions: Sequence[CategoryDistribution]) -> str:
    """What the weekly spreads were measured on, for the footnote."""
    if not distributions:
        return "no weekly spread has been measured for this league yet"
    basis = sorted({year for one in distributions for year in one.basis_seasons})
    years = ", ".join(str(year) for year in basis) if basis else "no season"
    days = distributions[0].period_days
    return (
        f"weekly spreads from this league's own results ({years}), "
        f"periods of {days} days; projections from the {season} season's stored box scores"
    )


def project_standings(
    session: Session,
    league_season: LeagueSeason,
    today: int,
    *,
    distributions: Sequence[CategoryDistribution] | None = None,
    tilt: bool = True,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    unavailable: Mapping[int, Collection[int]] | None = None,
    rosters: Mapping[int, Collection[int]] | None = None,
) -> Projection:
    """Every team's rest of season, from the stored schedule and today's rosters.

    `distributions` stands in for the league's measured weekly spreads, for a
    test and for a replay that must not read its own future (see the module
    docstring). `unavailable` names scoring periods a player cannot be seated
    on, over and above what `app.pickups.state` already knows: the replay uses
    it to feed in the NBA's own injury reports as they stood that morning
    (`app.injuries.statuses_as_of`), which is the only availability a played
    season has, the listener never having run for one.

    `rosters` stands a team's roster on its head, by ESPN team id: the men
    named are the ones that team is projected on, and every team not named
    stands exactly as it is. That is the whole of the hypothetical view
    (`app.inseason.what_if`) -- "what would the table look like if I made
    this move" -- and it is why the seam docs/projected_record.md section 3
    names is now taken for a hypothetical and still not for the stored
    per-team reports. Only men who can be started belong in the list: a man
    moved to injured reserve is simply left out, because a projection seats
    nobody from there.

    Raises `ValueError` when the season has no matchup period holding
    `today`, and when `rosters` names a team this season does not have.
    """
    _first, last, today = horizon(session, league_season, today)
    rule = ranking_rule(league_season)
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    if distributions is None:
        distributions = category_distributions(session, league_season)
    lineup = lineup_for(league_season)
    periods = _periods(session, league_season, today, last)
    if not periods:
        raise ValueError(f"scoring period {today} is in no matchup period of {season}")
    current = periods[0]

    teams = list(
        session.scalars(
            select(Team)
            .where(Team.league_season_id == league_season.id)
            .order_by(Team.espn_team_id)
        ).all()
    )
    by_row = {team.id: team for team in teams}

    # One pass over the league: the week as it stands for every team, which
    # is where the roster, the injured-reserve places and this period's
    # posted totals all come from.
    weeks = {
        team.id: load_team_week(session, league_season, int(team.espn_team_id), today)
        for team in teams
    }

    # Each man's game days over the WHOLE horizon, read once. A per-period
    # call would re-read the listener's snapshots and the NBA schedule for
    # every team and every week; the days of one period are a slice of this.
    held = {
        team_row: [player.player_id for player in week.roster] for team_row, week in weeks.items()
    }
    on_ir = frozenset(
        player.player_id for week in weeks.values() for player in week.roster if player.on_ir
    )
    if rosters:
        by_espn = {int(team.espn_team_id): team.id for team in teams}
        for espn_team_id, ids in rosters.items():
            row = by_espn.get(int(espn_team_id))
            if row is None:
                raise ValueError(f"there is no team {int(espn_team_id)} in the {season} season")
            held[row] = [int(player_id) for player_id in ids]
        # A man the change has taken off a roster is nobody's injured reserve
        # any more, and a man it has put on one is simply not in the list.
        standing = {player_id for ids in held.values() for player_id in ids}
        on_ir = frozenset(player_id for player_id in on_ir if player_id in standing)
    horizon_days = tuple(range(today, last + 1))
    everyone = {
        player.player_id: player
        for player in build_players(
            session,
            league_season,
            [pid for ids in held.values() for pid in ids],
            horizon_days,
            on_ir=on_ir,
        )
    }
    contenders = _contenders(
        session,
        season,
        list(everyone.values()),
        distributions,
        today,
        tilt=tilt,
        as_of=as_of,
    )
    blocked = {int(pid): set(days) for pid, days in (unavailable or {}).items()}

    def squad(team_row: int) -> list[Contender]:
        found = []
        for player_id in held[team_row]:
            man = everyone.get(player_id)
            contender = contenders.get(player_id)
            if man is None or contender is None or man.on_ir:
                continue
            away = blocked.get(player_id)
            if away:
                contender = Contender(
                    player=contender.player,
                    per_game=contender.per_game,
                    weight=contender.weight,
                    days=contender.days - away,
                )
            found.append(contender)
        return found

    squads = {team_row: squad(team_row) for team_row in held}
    pairings = _pairings(session, periods)

    windows = {
        period.id: tuple(
            range(
                max(today, int(period.first_scoring_period or today)),
                int(period.final_scoring_period or today) + 1,
            )
        )
        for period in periods
    }
    lines: dict[tuple[int, int], CategoryLine] = {}
    for period in periods:
        for team_row in held:
            posted = weeks[team_row].my_totals if period.id == current.id else CategoryLine()
            lines[(period.id, team_row)] = _project(
                windows[period.id], lineup, squads[team_row], posted
            )

    # The weeks, per team, both sides of each pairing at once.
    schedule: dict[int, list[Week]] = {team_row: [] for team_row in held}
    draws: list[_Draw] = []
    index = {team.id: place for place, team in enumerate(teams)}
    for period in periods:
        left = len(windows[period.id])
        for home, away in pairings.get(period.id, []):
            if home not in held:
                continue
            if away is None or away not in held:
                schedule[home].append(_bye(period, left, current.id))
                continue
            mine, theirs = lines[(period.id, home)], lines[(period.id, away)]
            chances = head_to_head(mine, theirs, distributions, left)
            other = {key: 1.0 - value for key, value in chances.items()}
            schedule[home].append(
                _week(period, left, by_row[away], chances, mine, theirs, current.id)
            )
            schedule[away].append(
                _week(period, left, by_row[home], other, theirs, mine, current.id)
            )
            draws.append(
                _Draw(
                    home=index[home],
                    away=index[away],
                    cumulative=tuple(_category_wins(list(chances.values()))),
                    contested=len(chances),
                )
            )

    banked = {
        team.id: banked_record(session, league_season, int(team.espn_team_id), today)
        for team in teams
    }
    matchups = _banked_matchups(session, league_season, today)
    meetings = {
        (index[a], index[b]): (float(w), float(lo), float(ti))
        for (a, b), (w, lo, ti) in category_meetings(session, league_season, before=today).items()
        if a in index and b in index
    }
    finishes, records = _simulate(
        draws,
        [matchups.get(team.id, (0, 0, 0)) for team in teams],
        [banked[team.id] for team in teams],
        teams=len(teams),
        n_sims=n_sims,
        seed=seed,
        rule=rule,
        banked_meetings=meetings,
    )

    playoff_teams = int(league_season.playoff_team_count or 0)
    byes = bye_seats(playoff_teams)
    outlooks = [
        TeamOutlook(
            team_id=int(team.espn_team_id),
            name=str(team.name),
            banked=banked[team.id],
            banked_matchups=matchups.get(team.id, (0, 0, 0)),
            weeks=tuple(schedule[team.id]),
            finishes=tuple(finishes[place]),
            playoff_odds=sum(finishes[place][:playoff_teams]),
            bye_odds=sum(finishes[place][:byes]) if byes else None,
            projected_matchups=records[place],
        )
        for place, team in enumerate(teams)
    ]
    outlooks.sort(key=lambda one: (-_mean_place(one.finishes), one.name))
    regular = not bool(current.is_playoff)
    return Projection(
        league_id=int(league_season.league.espn_league_id),
        season=season,
        as_of=today,
        as_of_date=as_of,
        matchup_period=int(current.period),
        teams=tuple(outlooks),
        periods=tuple(int(period.period) for period in periods),
        playoff_team_count=playoff_teams,
        bye_count=byes,
        playoffs_projected=not regular,
        playoff_note=NO_BRACKET if regular else "",
        tiebreak=rule.words,
        n_sims=n_sims,
        seed=seed,
        source_note=_source_note(season, distributions),
        basis=f"rosters and box scores as of scoring period {today}"
        + (f" ({as_of:%d %b %Y})" if as_of is not None else ""),
    )


def _mean_place(finishes: Sequence[float]) -> float:
    """Minus the expected finishing place, so a sort descending puts #1 first."""
    return -sum(share * (place + 1) for place, share in enumerate(finishes))


def _bye(period: MatchupPeriod, days_remaining: int, current_period_id: int) -> Week:
    return Week(
        period=int(period.period),
        first_scoring_period=int(period.first_scoring_period or 0),
        final_scoring_period=int(period.final_scoring_period or 0),
        days_remaining=days_remaining,
        in_play=period.id == current_period_id,
        opponent_team_id=None,
        opponent_name=None,
        probabilities={},
        projected=CategoryLine(),
        opponent_projected=CategoryLine(),
    )


def _week(
    period: MatchupPeriod,
    days_remaining: int,
    opponent: Team,
    probabilities: Mapping[str, float],
    mine: CategoryLine,
    theirs: CategoryLine,
    current_period_id: int,
) -> Week:
    return Week(
        period=int(period.period),
        first_scoring_period=int(period.first_scoring_period or 0),
        final_scoring_period=int(period.final_scoring_period or 0),
        days_remaining=days_remaining,
        in_play=period.id == current_period_id,
        opponent_team_id=int(opponent.espn_team_id),
        opponent_name=str(opponent.name),
        probabilities=dict(probabilities),
        projected=mine,
        opponent_projected=theirs,
    )


def latest_period(session: Session, league_season: LeagueSeason) -> int | None:
    """The last dated scoring period of the season, for a caller with no day."""
    return session.scalar(
        select(func.max(MatchupPeriod.final_scoring_period)).where(
            MatchupPeriod.league_season_id == league_season.id
        )
    )
