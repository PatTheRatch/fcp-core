"""Derived views over a stored season.

Everything here is computed rather than ingested, which is why it lives
outside `app.ingest` and outside the routers. ESPN reports none of it.

One idea carries most of the weight. A matchup is stored once, from the home
team's point of view, so almost every question ("did they win", "what was
their streak", "how do these two compare") first needs both teams' views of
it. `matchup_sides` produces those, and the rest builds on it.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Subquery

from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Player,
    PlayerGameStat,
    Team,
)

#: A matchup a team did not lose, for streak purposes. A tie breaks a run
#: rather than extending either, which is the reading that keeps "won five
#: in a row" meaning five wins.
WIN = "WIN"
LOSS = "LOSS"
TIE = "TIE"


@dataclass(frozen=True)
class MatchupSide:
    """One team's view of one matchup.

    Byes are excluded by `matchup_sides`: a team with no opponent neither
    won nor lost, and letting one count would inflate every playoff record.
    """

    period: int
    is_playoff: bool
    team_id: int
    espn_team_id: int
    team_name: str
    opponent_team_id: int
    opponent_name: str
    result: str
    categories_won: int
    categories_lost: int
    categories_tied: int

    @property
    def margin(self) -> int:
        """Categories won minus categories lost, from this team's side."""
        return self.categories_won - self.categories_lost


def matchup_sides(
    session: Session, league_season: LeagueSeason, *, include_playoffs: bool = False
) -> list[MatchupSide]:
    """Both teams' views of every contested matchup, in period order."""
    query = (
        select(Matchup)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .options(selectinload(Matchup.matchup_period))
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            Matchup.away_team_id.is_not(None),
        )
        .order_by(MatchupPeriod.period, Matchup.id)
    )
    if not include_playoffs:
        query = query.where(MatchupPeriod.is_playoff.is_(False))

    teams = {
        team.id: team
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }

    sides: list[MatchupSide] = []
    for matchup in session.scalars(query).all():
        home = teams.get(matchup.home_team_id)
        away = teams.get(matchup.away_team_id) if matchup.away_team_id else None
        if home is None or away is None:
            continue

        if matchup.winner == "HOME":
            home_result, away_result = WIN, LOSS
        elif matchup.winner == "AWAY":
            home_result, away_result = LOSS, WIN
        elif matchup.winner == "TIE":
            home_result, away_result = TIE, TIE
        else:
            continue  # UNDECIDED, which in practice means a bye

        period = matchup.matchup_period
        for team, opponent, result, won, lost in (
            (home, away, home_result, matchup.home_categories_won, matchup.home_categories_lost),
            (away, home, away_result, matchup.home_categories_lost, matchup.home_categories_won),
        ):
            sides.append(
                MatchupSide(
                    period=period.period,
                    is_playoff=period.is_playoff,
                    team_id=team.id,
                    espn_team_id=team.espn_team_id,
                    team_name=team.name,
                    opponent_team_id=opponent.id,
                    opponent_name=opponent.name,
                    result=result,
                    categories_won=won,
                    categories_lost=lost,
                    categories_tied=matchup.categories_tied,
                )
            )
    return sides


@dataclass(frozen=True)
class Streak:
    """A team's best and worst runs, and how the season ended for them."""

    espn_team_id: int
    name: str
    longest_win_streak: int
    longest_loss_streak: int
    final_streak: int
    final_streak_result: str | None


def _longest_run(results: list[str], target: str) -> int:
    best = current = 0
    for result in results:
        current = current + 1 if result == target else 0
        best = max(best, current)
    return best


def streaks(sides: list[MatchupSide]) -> list[Streak]:
    """Longest runs per team, in period order. A tie breaks a run."""
    by_team: dict[int, list[MatchupSide]] = defaultdict(list)
    for side in sides:
        by_team[side.team_id].append(side)

    out: list[Streak] = []
    for team_sides in by_team.values():
        ordered = sorted(team_sides, key=lambda s: s.period)
        results = [s.result for s in ordered]

        final_result = results[-1] if results else None
        final = 0
        if final_result in (WIN, LOSS):
            for result in reversed(results):
                if result != final_result:
                    break
                final += 1

        first = ordered[0]
        out.append(
            Streak(
                espn_team_id=first.espn_team_id,
                name=first.team_name,
                longest_win_streak=_longest_run(results, WIN),
                longest_loss_streak=_longest_run(results, LOSS),
                final_streak=final,
                final_streak_result=final_result if final_result in (WIN, LOSS) else None,
            )
        )
    out.sort(key=lambda s: (-s.longest_win_streak, s.name))
    return out


@dataclass(frozen=True)
class CategoryRecord:
    """How often one team won one category."""

    abbreviation: str
    stat_id: int
    won: int
    lost: int
    tied: int

    @property
    def win_rate(self) -> float | None:
        """Share of decided contests won. None when nothing was decided."""
        decided = self.won + self.lost
        return self.won / decided if decided else None


@dataclass(frozen=True)
class TeamCategoryProfile:
    espn_team_id: int
    name: str
    categories: list[CategoryRecord]


def category_profiles(
    session: Session, league_season: LeagueSeason, *, include_playoffs: bool = False
) -> list[TeamCategoryProfile]:
    """Per team, how each scored category went.

    Reads the per-category results rather than recomputing them from player
    stats, so it reflects exactly what ESPN awarded.
    """
    query = (
        select(
            Team.espn_team_id,
            Team.name,
            LeagueSeasonCategory.abbreviation,
            LeagueSeasonCategory.stat_id,
            LeagueSeasonCategory.position,
            func.sum(case((MatchupTeamStat.result == WIN, 1), else_=0)).label("won"),
            func.sum(case((MatchupTeamStat.result == LOSS, 1), else_=0)).label("lost"),
            func.sum(case((MatchupTeamStat.result == TIE, 1), else_=0)).label("tied"),
        )
        .select_from(MatchupTeamStat)
        .join(
            LeagueSeasonCategory,
            LeagueSeasonCategory.id == MatchupTeamStat.league_season_category_id,
        )
        .join(Team, Team.id == MatchupTeamStat.team_id)
        .join(Matchup, Matchup.id == MatchupTeamStat.matchup_id)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            Matchup.away_team_id.is_not(None),
        )
        .group_by(
            Team.espn_team_id,
            Team.name,
            LeagueSeasonCategory.abbreviation,
            LeagueSeasonCategory.stat_id,
            LeagueSeasonCategory.position,
        )
        .order_by(Team.name, LeagueSeasonCategory.position)
    )
    if not include_playoffs:
        query = query.where(MatchupPeriod.is_playoff.is_(False))

    grouped: dict[tuple[int, str], list[CategoryRecord]] = defaultdict(list)
    for espn_team_id, name, abbreviation, stat_id, _, won, lost, tied in session.execute(query):
        grouped[(espn_team_id, name)].append(
            CategoryRecord(
                abbreviation=abbreviation,
                stat_id=stat_id,
                won=int(won or 0),
                lost=int(lost or 0),
                tied=int(tied or 0),
            )
        )
    return [
        TeamCategoryProfile(espn_team_id=team_id, name=name, categories=records)
        for (team_id, name), records in grouped.items()
    ]


@dataclass(frozen=True)
class BenchTotal:
    """What one team left on its bench over a season."""

    espn_team_id: int
    name: str
    bench_points: float
    benched_games_of_20_plus: int
    benched_appearances: int


def bench_leaderboard(
    session: Session, league_season: LeagueSeason, *, include_playoffs: bool = False
) -> list[BenchTotal]:
    """Benched production for every team, worst first.

    Only counts players who actually played: a benched player whose team had
    no fixture cost the manager nothing.
    """
    query = (
        select(
            Team.espn_team_id,
            Team.name,
            func.coalesce(func.sum(PlayerGameStat.points), 0.0).label("points"),
            func.coalesce(func.sum(case((PlayerGameStat.points >= 20, 1), else_=0)), 0).label(
                "big_games"
            ),
            func.count().label("appearances"),
        )
        .select_from(DailyLineupSlot)
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .join(
            # This join matches only about 48% of lineup rows, which looks
            # alarming and is correct. A rostered player has a game line only
            # on days his team played, roughly 45% of scoring periods. Proven
            # rather than assumed: summing started players' lines through this
            # join reproduces the separately stored team totals in
            # matchup_team_stats for 2026 of 2030 sides across eight seasons.
            PlayerGameStat,
            (PlayerGameStat.player_id == DailyLineupSlot.player_id)
            & (PlayerGameStat.scoring_period == DailyLineupSlot.scoring_period)
            & (PlayerGameStat.season == league_season.season),
        )
        .where(
            Team.league_season_id == league_season.id,
            DailyLineupSlot.started.is_(False),
            PlayerGameStat.played.is_(True),
        )
        .group_by(Team.espn_team_id, Team.name)
        .order_by(func.coalesce(func.sum(PlayerGameStat.points), 0.0).desc())
    )
    if not include_playoffs:
        query = query.where(MatchupPeriod.is_playoff.is_(False))

    return [
        BenchTotal(
            espn_team_id=espn_team_id,
            name=name,
            bench_points=float(points or 0.0),
            benched_games_of_20_plus=int(big_games or 0),
            benched_appearances=int(appearances or 0),
        )
        for espn_team_id, name, points, big_games, appearances in session.execute(query)
    ]


@dataclass(frozen=True)
class NotableMatchup:
    """A matchup worth telling a story about."""

    period: int
    is_playoff: bool
    winner_name: str
    loser_name: str
    categories_won: int
    categories_lost: int
    categories_tied: int
    margin: int


def notable_matchups(
    sides: list[MatchupSide], *, limit: int = 10
) -> dict[str, list[NotableMatchup]]:
    """The season's most and least lopsided results.

    Sweeps are the widest margins, nail-biters the narrowest. Ties are left
    out of both: there is no winner to name.
    """
    winners = [side for side in sides if side.result == WIN]

    def described(side: MatchupSide) -> NotableMatchup:
        return NotableMatchup(
            period=side.period,
            is_playoff=side.is_playoff,
            winner_name=side.team_name,
            loser_name=side.opponent_name,
            categories_won=side.categories_won,
            categories_lost=side.categories_lost,
            categories_tied=side.categories_tied,
            margin=side.margin,
        )

    widest = sorted(winners, key=lambda s: (-s.margin, s.period))
    narrowest = sorted(winners, key=lambda s: (s.margin, s.period))
    return {
        "sweeps": [described(s) for s in widest[:limit]],
        "nail_biters": [described(s) for s in narrowest[:limit]],
    }


@dataclass(frozen=True)
class OwnerSeason:
    season: int
    team_name: str
    matchups_won: int
    matchups_lost: int
    matchups_tied: int
    final_standing: int | None


@dataclass(frozen=True)
class OwnerRecord:
    """One person's history in the league, across every season stored.

    Identity follows a person across seasons, so this is the one view that
    outlives any single team. The id is ours, not ESPN's: see
    `_team_owner_ids`.
    """

    owner_id: int
    display_name: str | None
    seasons: list[OwnerSeason]

    @property
    def matchups_won(self) -> int:
        return sum(s.matchups_won for s in self.seasons)

    @property
    def matchups_lost(self) -> int:
        return sum(s.matchups_lost for s in self.seasons)

    @property
    def matchups_tied(self) -> int:
        return sum(s.matchups_tied for s in self.seasons)

    @property
    def titles(self) -> int:
        return sum(1 for s in self.seasons if s.final_standing == 1)


def _team_owner_ids(
    session: Session, team_ids: set[int]
) -> dict[int, list[tuple[int, str | None]]]:
    """Team id -> its owners, keyed on our id rather than ESPN's.

    ESPN identifies an owner by their SWID GUID, which is half of the cookie
    pair that authenticates a real ESPN account. It stays in the database as
    the identity key and never travels any further than that.

    A team can have more than one owner.
    """
    if not team_ids:
        return {}
    rows = session.scalars(
        select(Team).options(selectinload(Team.owners)).where(Team.id.in_(team_ids))
    ).all()
    return {team.id: [(owner.id, owner.display_name) for owner in team.owners] for team in rows}


def owner_records(
    session: Session, league_id: int, *, include_playoffs: bool = False
) -> list[OwnerRecord]:
    """Every owner's season-by-season record across the whole league history."""
    seasons = session.scalars(
        select(LeagueSeason)
        .join(LeagueSeason.league)
        .where(LeagueSeason.league.has(espn_league_id=league_id))
        .order_by(LeagueSeason.season)
    ).all()

    tally: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    names: dict[int, str | None] = {}
    team_names: dict[tuple[int, int], str] = {}
    standings: dict[tuple[int, int], int | None] = {}

    for league_season in seasons:
        sides = matchup_sides(session, league_season, include_playoffs=include_playoffs)
        owners_by_team = _team_owner_ids(session, {s.team_id for s in sides})
        team_standing = {
            team.id: team.final_standing
            for team in session.scalars(
                select(Team).where(Team.league_season_id == league_season.id)
            ).all()
        }

        for side in sides:
            for owner_id, display_name in owners_by_team.get(side.team_id, []):
                names.setdefault(owner_id, display_name)
                team_names[(owner_id, league_season.season)] = side.team_name
                standings[(owner_id, league_season.season)] = team_standing.get(side.team_id)
                bucket = tally[owner_id][league_season.season]
                if side.result == WIN:
                    bucket[0] += 1
                elif side.result == LOSS:
                    bucket[1] += 1
                elif side.result == TIE:
                    bucket[2] += 1

    records = [
        OwnerRecord(
            owner_id=owner_id,
            display_name=names.get(owner_id),
            seasons=[
                OwnerSeason(
                    season=season,
                    team_name=team_names.get((owner_id, season), ""),
                    matchups_won=bucket[0],
                    matchups_lost=bucket[1],
                    matchups_tied=bucket[2],
                    final_standing=standings.get((owner_id, season)),
                )
                for season, bucket in sorted(by_season.items())
            ],
        )
        for owner_id, by_season in tally.items()
    ]
    records.sort(key=lambda r: (-r.matchups_won, r.matchups_lost))
    return records


@dataclass(frozen=True)
class HeadToHead:
    """How two owners have fared against each other, all seasons combined."""

    owner_a: int
    owner_a_name: str | None
    owner_b: int
    owner_b_name: str | None
    a_wins: int
    b_wins: int
    ties: int
    meetings: int
    seasons: list[int]


def head_to_head(
    session: Session, league_id: int, *, include_playoffs: bool = False, min_meetings: int = 1
) -> list[HeadToHead]:
    """Every pair of owners who have met, most-played pair first.

    Co-ownership is handled by pairing each owner of one side with each owner
    of the other, so a jointly managed team contributes a meeting for both
    of its owners rather than being dropped.
    """
    seasons = session.scalars(
        select(LeagueSeason)
        .where(LeagueSeason.league.has(espn_league_id=league_id))
        .order_by(LeagueSeason.season)
    ).all()

    pairs: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0, 0])
    met_in: dict[tuple[int, int], set[int]] = defaultdict(set)
    names: dict[int, str | None] = {}

    for league_season in seasons:
        sides = matchup_sides(session, league_season, include_playoffs=include_playoffs)
        owners_by_team = _team_owner_ids(
            session, {s.team_id for s in sides} | {s.opponent_team_id for s in sides}
        )

        for side in sides:
            # Count each meeting once. A win appears on exactly one side, but
            # a tie appears on both, so ties are taken from the lower team id.
            if side.result == LOSS:
                continue
            if side.result == TIE and side.team_id > side.opponent_team_id:
                continue
            for a_id, a_name in owners_by_team.get(side.team_id, []):
                for b_id, b_name in owners_by_team.get(side.opponent_team_id, []):
                    if a_id == b_id:
                        continue
                    names.setdefault(a_id, a_name)
                    names.setdefault(b_id, b_name)
                    key = (a_id, b_id) if a_id < b_id else (b_id, a_id)
                    bucket = pairs[key]
                    met_in[key].add(league_season.season)
                    if side.result == TIE:
                        bucket[2] += 1
                    elif key[0] == a_id:
                        bucket[0] += 1
                    else:
                        bucket[1] += 1

    out = [
        HeadToHead(
            owner_a=a,
            owner_a_name=names.get(a),
            owner_b=b,
            owner_b_name=names.get(b),
            a_wins=bucket[0],
            b_wins=bucket[1],
            ties=bucket[2],
            meetings=sum(bucket),
            seasons=sorted(met_in[(a, b)]),
        )
        for (a, b), bucket in pairs.items()
    ]
    out = [h for h in out if h.meetings >= min_meetings]
    out.sort(key=lambda h: (-h.meetings, h.owner_a_name or h.owner_a))
    return out


@dataclass(frozen=True)
class BenchCall:
    """A day a benched player outproduced every starter on the team."""

    scoring_period: int
    team_name: str
    player_name: str
    benched_points: float
    best_starter_points: float

    @property
    def margin(self) -> float:
        return self.benched_points - self.best_starter_points


def worst_bench_calls(
    session: Session, league_season: LeagueSeason, *, limit: int = 10, team_id: int | None = None
) -> list[BenchCall]:
    """Days a benched player beat the best starter, worst margin first.

    Both halves read from explicitly labelled subqueries. Aggregating over an
    outer table while selecting from a subquery of it silently produces a
    cartesian product.
    """

    def day_rows(started: bool) -> Subquery:
        query = (
            select(
                DailyLineupSlot.team_id.label("team_id"),
                Team.name.label("team_name"),
                DailyLineupSlot.scoring_period.label("scoring_period"),
                Player.name.label("player_name"),
                PlayerGameStat.points.label("points"),
            )
            .select_from(DailyLineupSlot)
            .join(Team, Team.id == DailyLineupSlot.team_id)
            .join(Player, Player.id == DailyLineupSlot.player_id)
            .join(
                PlayerGameStat,
                (PlayerGameStat.player_id == DailyLineupSlot.player_id)
                & (PlayerGameStat.scoring_period == DailyLineupSlot.scoring_period)
                & (PlayerGameStat.season == league_season.season),
            )
            .where(
                Team.league_season_id == league_season.id,
                DailyLineupSlot.started.is_(started),
                PlayerGameStat.played.is_(True),
            )
        )
        if team_id is not None:
            query = query.where(DailyLineupSlot.team_id == team_id)
        return query.subquery()

    bench = day_rows(started=False)
    starters = day_rows(started=True)
    best = (
        select(
            starters.c.team_id.label("team_id"),
            starters.c.scoring_period.label("scoring_period"),
            func.max(starters.c.points).label("best"),
        )
        .group_by(starters.c.team_id, starters.c.scoring_period)
        .subquery()
    )

    rows = session.execute(
        select(
            bench.c.scoring_period,
            bench.c.team_name,
            bench.c.player_name,
            bench.c.points,
            best.c.best,
        )
        .join(
            best,
            (best.c.team_id == bench.c.team_id) & (best.c.scoring_period == bench.c.scoring_period),
        )
        .where(bench.c.points > best.c.best)
        .order_by((bench.c.points - best.c.best).desc())
        .limit(limit)
    ).all()

    return [
        BenchCall(
            scoring_period=scoring_period,
            team_name=team_name,
            player_name=player_name,
            benched_points=float(points or 0.0),
            best_starter_points=float(best_points or 0.0),
        )
        for scoring_period, team_name, player_name, points, best_points in rows
    ]


__all__ = [
    "BenchCall",
    "BenchTotal",
    "CategoryRecord",
    "HeadToHead",
    "MatchupSide",
    "NotableMatchup",
    "OwnerRecord",
    "OwnerSeason",
    "Streak",
    "TeamCategoryProfile",
    "bench_leaderboard",
    "category_profiles",
    "head_to_head",
    "matchup_sides",
    "notable_matchups",
    "owner_records",
    "streaks",
    "worst_bench_calls",
]
