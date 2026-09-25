"""How a league orders its table: one rule, read off the league's own settings.

WHY THIS EXISTS

Until 2026-09-25 every table here -- the standings route, the projection's
simulated seasons and so its seeding, the digest's place, the history page --
was ordered by **matchups won**. This league is ESPN's **Head-to-Head Each
Category** (`scoring_type` `H2H_CATEGORY`), and ESPN ranks that format on
the **category record**: every category of every week is a game won, lost or
tied, and there is no matchup record in it at all. That is why ESPN reports
none, and why "matchups won" never predicted ESPN's order.

THE RULE, BY SCORING TYPE

* `H2H_CATEGORY` (**each category**): the unit is categories. Ordered by
  **category win share**, `(W + T/2) / (W + L + T)`, then -- among teams
  level on share -- **the tied teams' category record against each other**
  in their regular-season meetings, then **categories won**, then **fewest
  lost**.
* `H2H_MOST_CATEGORIES` (**most categories**) and the points formats: the
  unit is matchups, because there a week is one game won or lost. Ordered by
  **matchups won, then fewest lost, then categories won**, the order this
  code used everywhere before.
* Rotisserie is **out of scope**: it is ranked on category points, which
  nothing here computes, and `ranking_rule` refuses it rather than guess.
  So does a scoring type nobody has named. The intake refuses both already
  (`app.intake.refusal`); this is the same line drawn for a caller that
  arrives some other way.

WHAT THE RULE WAS MEASURED ON

docs/projected_record.md, revision R6. On every stored season 2019-2026,
ordering by category win share gives ESPN's own `teams.standing` except at
exact ties of share and at 2023's places 2 and 3. There were nine exact ties
and **all nine** went to the team with the better category record against
the other tied team -- the league's own seeding tiebreaker,
`raw_settings.schedule.playoffSeedingRule` = `H2H_RECORD` ("head-to-head
record"). Categories won alone gets two of the nine right. So the
head-to-head term is gated on that setting (`HEAD_TO_HEAD_SEEDING`): a league
that breaks its ties some other way is not given this one.

2023's places 2 and 3 are not a tie. 2023 had two divisions and ESPN seeds
the division leaders first. That is seen in one season and is **not
modelled**; the standings route reports ESPN's own order for a played season
and flags the places where the rule differs, rather than overrule a table
ESPN has published.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, Matchup, MatchupPeriod

__all__ = [
    "BY_CATEGORIES",
    "BY_MATCHUPS",
    "CATEGORIES",
    "CATEGORY_WORDS",
    "HEAD_TO_HEAD_SEEDING",
    "MATCHUPS",
    "MATCHUP_WORDS",
    "Meetings",
    "RankingRule",
    "Record",
    "category_meetings",
    "lookup",
    "matchup_records",
    "ranking_rule",
    "scoring_type_of",
    "seeding_rule_of",
]

#: The two units a table can be ordered in.
CATEGORIES = "categories"
MATCHUPS = "matchups"

#: Scoring types ranked on the category record: ESPN's Head-to-Head Each Category.
BY_CATEGORIES = frozenset({"H2H_CATEGORY", "H2H_EACH_CATEGORY"})
#: Scoring types ranked on the matchup record: most categories, and points.
BY_MATCHUPS = frozenset({"H2H_MOST_CATEGORIES", "H2H_POINTS"})

#: ESPN's seeding tiebreakers under which a tie of share goes to the tied
#: teams' record against each other. `H2H_RECORD` is "head-to-head record";
#: `INTRA_DIVISION_RECORD` is the record inside the division, which in a
#: one-division league is the whole record and leaves a tie of share level,
#: and its one tie on record (2025) still went head to head. An unset setting
#: is read as ESPN's default, which is `H2H_RECORD`.
HEAD_TO_HEAD_SEEDING = frozenset({"H2H_RECORD", "INTRA_DIVISION_RECORD", ""})

#: The order in the words the pages and the co-manager print.
CATEGORY_WORDS = (
    "category win share, then the tied teams' category record against each other, "
    "then categories won, then fewest lost"
)
#: The same, for a league whose seeding tiebreaker is not a head-to-head one.
CATEGORY_WORDS_NO_H2H = "category win share, then categories won, then fewest lost"
MATCHUP_WORDS = "matchups won, then fewest lost, then categories won"

#: A team's category record against one other team: won, lost, tied.
Meetings = Callable[[int, int], tuple[float, float, float]]


class Record(NamedTuple):
    """One team's record, in both units. `team` is whatever id the caller orders.

    A tuple rather than a dataclass because the simulation builds fourteen of
    these for each of ten thousand seasons.
    """

    team: int
    categories_won: float
    categories_lost: float
    categories_tied: float = 0.0
    matchups_won: float = 0.0
    matchups_lost: float = 0.0
    matchups_tied: float = 0.0

    @property
    def share(self) -> float | None:
        """Category win share, `(W + T/2) / (W + L + T)`; None before any is decided."""
        return _share(self.categories_won, self.categories_lost, self.categories_tied)


def _share(won: float, lost: float, tied: float) -> float | None:
    decided = won + lost + tied
    return (won + tied / 2.0) / decided if decided else None


def _level(record: Record) -> float:
    """The share as the sort reads it: a team with nothing decided sits at zero."""
    share = record.share
    return share if share is not None else 0.0


@dataclass(frozen=True)
class RankingRule:
    """How one league season's table is ordered, and what that is called."""

    #: ESPN's scoring type the rule was read from.
    scoring_type: str
    #: `CATEGORIES` or `MATCHUPS`: which record decides the order.
    unit: str
    #: ESPN's seeding tiebreaker, as stored; "" when the season does not say.
    seeding_rule: str
    #: Whether a tie of share goes to the tied teams' record against each other.
    head_to_head: bool
    #: The order, in words.
    words: str

    def key(self, record: Record) -> tuple[float, ...]:
        """The sort key, ascending: the best team has the smallest key.

        For the category unit it leaves out the head-to-head term, which is
        not a property of one team; `order` applies it among teams level on
        share.
        """
        if self.unit == CATEGORIES:
            return (-_level(record), -record.categories_won, record.categories_lost)
        return (-record.matchups_won, record.matchups_lost, -record.categories_won)

    def order(
        self,
        records: Iterable[Record],
        meetings: Meetings | None = None,
        draw: Callable[[int], float] | None = None,
    ) -> list[Record]:
        """The records, best first.

        `meetings(a, b)` is team a's category record against team b; with it,
        and a head-to-head league, teams level on share are ordered by their
        record against each other before categories won. `draw(team)` breaks
        whatever is still level -- the simulation passes a number drawn per
        team per season. Without it, a complete tie keeps the order it came in.
        """
        tail: Callable[[Record], tuple[float, ...]] = (
            (lambda record: (draw(record.team),)) if draw is not None else (lambda _record: ())
        )
        ranked = sorted(records, key=lambda record: (*self.key(record), *tail(record)))
        if self.unit != CATEGORIES or not self.head_to_head or meetings is None:
            return ranked
        out: list[Record] = []
        start = 0
        while start < len(ranked):
            end = start + 1
            level = _level(ranked[start])
            while end < len(ranked) and _level(ranked[end]) == level:
                end += 1
            group = ranked[start:end]
            if len(group) > 1:
                group = sorted(
                    group,
                    key=lambda record: (
                        -_against(record, group, meetings),
                        *self.key(record)[1:],
                        *tail(record),
                    ),
                )
            out.extend(group)
            start = end
        return out


def _against(record: Record, group: Sequence[Record], meetings: Meetings) -> float:
    """A team's category share against the other teams it is level with.

    A half when they never met, so a pair with no meetings is decided by what
    comes after it rather than by an absence.
    """
    won = lost = tied = 0.0
    for other in group:
        if other.team == record.team:
            continue
        w, lo, ti = meetings(record.team, other.team)
        won, lost, tied = won + w, lost + lo, tied + ti
    share = _share(won, lost, tied)
    return share if share is not None else 0.5


def scoring_type_of(league_season: LeagueSeason) -> str:
    """The stored scoring type, and ESPN's own settings where the column is empty."""
    stored = str(league_season.scoring_type or "").strip()
    if stored:
        return stored
    scoring = (league_season.raw_settings or {}).get("scoring") or {}
    return str(scoring.get("scoringType") or "").strip()


def seeding_rule_of(league_season: LeagueSeason) -> str:
    """ESPN's seeding tiebreaker for the season, "" when it does not say."""
    schedule = (league_season.raw_settings or {}).get("schedule") or {}
    return str(schedule.get("playoffSeedingRule") or "").strip()


def ranking_rule(league_season: LeagueSeason) -> RankingRule:
    """The rule this league season is ranked by, read off its own settings.

    Raises `ValueError` for a scoring type this code does not rank --
    rotisserie, or anything unnamed -- with the reason in the message.
    """
    scoring = scoring_type_of(league_season)
    seeding = seeding_rule_of(league_season)
    if scoring in BY_CATEGORIES:
        head_to_head = seeding in HEAD_TO_HEAD_SEEDING
        return RankingRule(
            scoring_type=scoring,
            unit=CATEGORIES,
            seeding_rule=seeding,
            head_to_head=head_to_head,
            words=CATEGORY_WORDS if head_to_head else CATEGORY_WORDS_NO_H2H,
        )
    if scoring in BY_MATCHUPS:
        return RankingRule(
            scoring_type=scoring,
            unit=MATCHUPS,
            seeding_rule=seeding,
            head_to_head=False,
            words=MATCHUP_WORDS,
        )
    raise ValueError(
        f"a league scored as {scoring or 'nothing stated'} is not one this code ranks: "
        "head-to-head leagues only, and rotisserie is out of scope"
    )


# ---------------------------------------------------------------------------
# the records, read from the stored matchups
# ---------------------------------------------------------------------------


def _decided(winner: str | None) -> bool:
    return str(winner) in ("HOME", "AWAY", "TIE")


def matchup_records(
    session: Session,
    league_season: LeagueSeason,
    *,
    include_playoffs: bool = False,
    before: int | None = None,
) -> dict[int, tuple[int, int, int]]:
    """Each team's matchup record, won, lost and tied, by team row id.

    Counted from the stored winners, because ESPN reports no matchup record
    for a category league. A bye is not a win and a week still undecided is
    not counted. `before` keeps only periods whose last day is before it,
    which is how a replay knows nothing of a week still being played.
    """
    query = (
        select(Matchup.home_team_id, Matchup.away_team_id, Matchup.winner)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            Matchup.away_team_id.is_not(None),
        )
    )
    if not include_playoffs:
        query = query.where(MatchupPeriod.is_playoff.is_(False))
    if before is not None:
        query = query.where(MatchupPeriod.final_scoring_period < before)
    out: dict[int, tuple[int, int, int]] = {}

    def add(team: int, won: int, lost: int, tied: int) -> None:
        have = out.get(team, (0, 0, 0))
        out[team] = (have[0] + won, have[1] + lost, have[2] + tied)

    for home, away, winner in session.execute(query).all():
        if away is None:
            continue
        if winner == "HOME":
            add(int(home), 1, 0, 0)
            add(int(away), 0, 1, 0)
        elif winner == "AWAY":
            add(int(away), 1, 0, 0)
            add(int(home), 0, 1, 0)
        elif winner == "TIE":
            add(int(home), 0, 0, 1)
            add(int(away), 0, 0, 1)
    return out


def category_meetings(
    session: Session, league_season: LeagueSeason, *, before: int | None = None
) -> dict[tuple[int, int], tuple[int, int, int]]:
    """Each pair's category record in their decided regular-season meetings.

    Keyed (team a row id, team b row id) -> a's categories won, lost and tied
    against b; both directions are present. What ESPN's head-to-head
    tiebreaker reads.
    """
    query = (
        select(
            Matchup.home_team_id,
            Matchup.away_team_id,
            Matchup.winner,
            Matchup.home_categories_won,
            Matchup.home_categories_lost,
            Matchup.categories_tied,
        )
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
        )
    )
    if before is not None:
        query = query.where(MatchupPeriod.final_scoring_period < before)
    out: dict[tuple[int, int], tuple[int, int, int]] = {}
    for home, away, winner, won, lost, tied in session.execute(query).all():
        if away is None or not _decided(winner):
            continue
        for a, b, w, lo in ((home, away, won, lost), (away, home, lost, won)):
            have = out.get((int(a), int(b)), (0, 0, 0))
            out[(int(a), int(b))] = (have[0] + int(w), have[1] + int(lo), have[2] + int(tied))
    return out


def lookup(meetings: Mapping[tuple[int, int], tuple[float, float, float]]) -> Meetings:
    """`category_meetings`' dict as the callable `RankingRule.order` takes."""

    def read(a: int, b: int) -> tuple[float, float, float]:
        return meetings.get((a, b), (0, 0, 0))

    return read
