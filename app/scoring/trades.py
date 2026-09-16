"""Reconstruct trades from roster movement, because ESPN's feed has no items.

`teams.trades` counts how many trades each team made but names no players, and
`transaction_items` only carries player detail on the few rows where ESPN
attached it. So a trade is *reconstructed*: a player who stops appearing for
one team and appears for another within a day or two moved between them. The
roster movement is the evidence for who and when; the transaction ledger is the
evidence for whether it was a trade at all.

WHY THE OBVIOUS RULE OVER-COUNTS, AND WHAT REPLACES IT

Counting every movement is wrong, and badly so. Measured league-wide on the
live database, a movement-counting reconstruction finds 526 sides against
ESPN's 204. Two things inflate it:

1. **A waiver claim looks exactly like a trade half.** Team A drops a player,
   team B adds him a day later, and nothing in `daily_lineup_slots` distinguishes
   that from a trade. Before 2026 there were believed to be no waiver rows at
   all, so no exclusion could help; in fact the rows exist for every season
   (2019 onward), which is why this module can use them.
2. **One trade between three teams is three pairs.** ESPN charges each team one
   trade for a three-way. Pairwise counting charges each of the three pairs, so
   every team in a three-way is billed twice (2024 alone: 84 sides by the naive
   rule against ESPN's 48).

The rule this module uses:

- **Reciprocity is required.** A movement A->B counts only if a *different*
  player moved B->A with the move day within +-1 day. Both sides of a real trade
  are visible in a two-team deal, so a lone one-way movement is a waiver claim
  unless something else proves otherwise.
- **The wire is not a trade.** A movement is discarded outright when an executed
  WAIVER or FREEAGENT transaction names that player within a few days of it.
- **The ledger can prove a trade the roster movement cannot.** A movement with
  no reciprocal partner still counts when an executed TRADE_UPHOLD lands within
  +-1 day, because uphold rows are per-team confirmations that a deal completed.
  That is the "part missing" case: real, but only one side is recoverable.
- **Same-day movements are one event.** All legs sharing a day and a connected
  team pair collapse to a single `Trade`, so a three-way counts once per team.
  The collapse is what brings the per-team counts under ESPN's.

MEASURED AGAINST ESPN (live database, 2026-09-16)

Reconstructed sides are one per team per event, which is what `teams.trades`
counts. 2027 is ingested but unplayed (every count 0); 2022 was a season with no
trades at all.

    season   reconstructed   ESPN   note
    2019                13     20
    2020                 2      2   the season's single 1-for-1 is recovered
    2021                 6      8
    2022                 0      0   no trades that season
    2023                 6      6
    2024                37     48
    2025                41     56
    2026                52     64
    ----               ---    ---
    total              157    204

Every season is below ESPN's total, so this is a sound lower bound rather than a
complete count: a trade is only recovered when both legs leave roster traces,
and one where a player never appeared in a lineup is invisible. The old rule was
over ESPN on all but one season; this one is under on all of them.

The under-count has two known shapes, both verified against the 2026 ledger:

- A trade between two teams on the same day as a second, unrelated movement can
  have its legs absorbed into a neighbouring event, so a small deal disappears
  into a bigger one (2026 tx 9199, day 53: Neemias Queta's Foxes spell runs to
  day 71 while Through The Wire picks him up on day 53, so no leg exists for him
  and the pair has only Myles Turner's side).
- A trade where one player moved between two teams with a wider gap than the
  two-day window (2026 tx 10130, day 85: Huerter's spans put his Foxes spell
  after his Uncle Dennis spell, so the direction in the movement data is
  reversed and no leg is found). ESPN names both players; the movement does not.

Per-team, no team exceeds its own `teams.trades` in any season 2019-2026, so the
"reconstructed trades <= ESPN" check holds team by team, not just in total. No
exceptions to report. The measured per-season maximum ratio of reconstructed to
ESPN's total is 1.00 (2020 and 2023, where both are the same small number).

AGAINST THE 2026 LEDGER

2026 is the only season with executed TRADE_ACCEPT rows carrying items, so it is
the ground truth. There are 4 such trades. This module recovers 3 of them (days
22, 53 and 108, including a 3-player swap and a 2-for-1); 1 is missed (day 85,
explained above). Not one reconstructed event is invented: all 26 event days
have executed trade activity in the ledger within two days. The 41 TRADE_UPHOLD
rows (about 2 per trade) imply roughly 20 trades, against the 26 events found --
so the reconstruction is running slightly *above* the uphold ledger while
staying under ESPN's count of 64, which is the expected direction for a rule
that treats every reciprocal pair as a trade.

WHAT THIS DOES NOT DO

`TRADE_UPHOLD` rows carry no `transaction_items`, so a "part missing" event
knows only that a deal completed, not which players were in it -- the day and
the teams come from the movement, the confirmation from the ledger. Grade such a
trade on the side that is present, and say the other side is missing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    MatchupPeriod,
    Player,
    Team,
    Transaction,
    TransactionItem,
)

#: How many days after a player's last appearance for one team he may first
#: appear for another and still count as the same move. One would drop genuine
#: same-day handoffs with a one-day roster-processing gap; three starts
#: swallowing waiver churn. Two is what the 2026 ground truth needs: every real
#: leg in it lands at exactly +1.
PICKUP_WINDOW_DAYS = 2

#: How far apart two legs of the same trade may be recorded as their move day.
#: ESPN stamps the transaction and writes the rosters a day either side of it,
#: so the ledger and the movement routinely disagree by one day.
SAME_TRADE_DAYS = 1

#: How far from a move an executed waiver or free-agent pickup still explains
#: it. Wider than the pickup window because the transaction is stamped when
#: ESPN processed the claim, which can trail the roster write.
WIRE_EXPLAINS_DAYS = 3

#: Transaction types that move a player through the wire rather than a trade.
WIRE_TYPES = ("WAIVER", "FREEAGENT")

#: The only status that means a transaction happened.
EXECUTED = "EXECUTED"

#: A ledger row confirming a completed trade. Carries no items of its own, which
#: is exactly why it is used as a flag rather than as a source of players.
UPHOLD_TYPE = "TRADE_UPHOLD"


@dataclass(frozen=True)
class Party:
    """A player on one side of a trade, with what he did after it.

    `games_after` and `comp_after` are what the season report grades on: the
    days he played and his nine-category production once the trade was done, for
    whoever he played them. They are filled by the report, because the nine-cat
    composite is a report concern rather than a scoring one.
    """

    player_id: int
    name: str
    games_after: int = 0
    comp_after: float = 0.0


@dataclass(frozen=True)
class Trade:
    """One reconstructed trade, as seen by one team.

    `day` is the scoring period the moving players left the `from` side. Where
    legs of one deal disagree by a day, it is the earliest, so the grading
    window on every side starts at the same place.

    `gradeable` is false when only one side was recovered: the deal is real
    (a trade happened) but there is nothing to weigh it against, so the report
    says "part missing" rather than grading a one-sided move.
    """

    day: int
    #: The team this trade is being reconstructed for.
    team_id: int
    #: Every other team in the deal, by id and name, sorted by name.
    counterparties: tuple[tuple[int, str], ...]
    #: Players this team received.
    players_in: tuple[Party, ...]
    #: Players this team gave up.
    players_out: tuple[Party, ...]
    #: True when both sides are present, so the trade can be graded.
    gradeable: bool
    #: True when only the transaction ledger proves this trade; one side of the
    #: movement is missing.
    from_ledger: bool = False

    @property
    def counterparty_ids(self) -> tuple[int, ...]:
        return tuple(team_id for team_id, _ in self.counterparties)

    @property
    def counterparty_names(self) -> tuple[str, ...]:
        return tuple(name for _, name in self.counterparties)


@dataclass
class _Leg:
    """One player's movement, before legs are grouped into trades."""

    player_id: int
    player_name: str
    from_team: int
    to_team: int
    day: int


def _season_id(session: Session, season: int) -> int | None:
    return session.scalar(select(LeagueSeason.id).where(LeagueSeason.season == season))


def _roster_spans(session: Session, league_season_id: int) -> list[tuple[int, int, int, int]]:
    """Every (player, team) spell in a season: first and last day held.

    Read from `daily_lineup_slots` rather than `roster_slots` because it is the
    daily grain that shows a player leaving: a weekly snapshot would place the
    move up to a matchup period late.
    """
    rows = session.execute(
        select(
            DailyLineupSlot.player_id,
            DailyLineupSlot.team_id,
            func.min(DailyLineupSlot.scoring_period),
            func.max(DailyLineupSlot.scoring_period),
        )
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(MatchupPeriod.league_season_id == league_season_id)
        .group_by(DailyLineupSlot.player_id, DailyLineupSlot.team_id)
    ).all()
    return [(int(player), int(team), int(first), int(last)) for player, team, first, last in rows]


def _wire_explains(session: Session, league_season_id: int) -> dict[int, set[int]]:
    """Player id -> the move days an executed wire transaction accounts for.

    Keyed on the player, not the team: a claim that lost a player to someone
    else still means the movement was a claim and not a trade.
    """
    rows = session.execute(
        select(TransactionItem.player_id, Transaction.scoring_period)
        .join(Transaction, Transaction.id == TransactionItem.transaction_id)
        .where(
            Transaction.league_season_id == league_season_id,
            Transaction.type.in_(WIRE_TYPES),
            Transaction.status == EXECUTED,
        )
    ).all()
    days: dict[int, set[int]] = defaultdict(set)
    for player_id, day in rows:
        for offset in range(-WIRE_EXPLAINS_DAYS, WIRE_EXPLAINS_DAYS + 1):
            days[int(player_id)].add(int(day) + offset)
    return days


def _uphold_days(session: Session, league_season_id: int) -> list[int]:
    """Days on which an executed trade was confirmed by the ledger."""
    return [
        int(day)
        for day in session.scalars(
            select(Transaction.scoring_period).where(
                Transaction.league_season_id == league_season_id,
                Transaction.type == UPHOLD_TYPE,
                Transaction.status == EXECUTED,
            )
        ).all()
    ]


def _movements(
    session: Session,
    league_season_id: int,
    wire: dict[int, set[int]],
) -> list[_Leg]:
    """Every player move that the wire does not already explain.

    A player with two spells for *different* teams is a move when the second
    spell begins within `PICKUP_WINDOW_DAYS` of the first ending. Same-team
    re-adds (a drop and re-add of the same player by the same manager) are not
    moves. A player who was held by more than two teams is yielded once per
    adjacent pair, which is what makes a three-way legible.
    """
    spans: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    names: dict[int, str] = {}
    for player_id, team_id, first_day, last_day in _roster_spans(session, league_season_id):
        spans[player_id].append((first_day, last_day, team_id))

    if not spans:
        return []
    for player_id, name in session.execute(
        select(Player.id, Player.name).where(Player.id.in_(list(spans)))
    ).all():
        names[int(player_id)] = str(name)

    legs: list[_Leg] = []
    for player_id, spells in spans.items():
        # A player's spells in time order: each should end before the next
        # team's begins, and the gap is what makes it a move rather than a
        # same-day re-add or an unrelated later stint.
        ordered = sorted(spells)
        for (_start, last, from_team), (next_first, _end, to_team) in pairwise(ordered):
            if from_team == to_team:
                continue
            if not (last < next_first <= last + PICKUP_WINDOW_DAYS):
                continue
            if last in wire.get(player_id, ()):
                continue
            legs.append(
                _Leg(
                    player_id, names.get(player_id, f"player {player_id}"), from_team, to_team, last
                )
            )
    return sorted(legs, key=lambda leg: (leg.day, leg.player_id))


def _has_reciprocal(leg: _Leg, legs: list[_Leg]) -> bool:
    """Did a different player move back the other way around the same time?"""
    return any(
        other.player_id != leg.player_id
        and other.from_team == leg.to_team
        and other.to_team == leg.from_team
        and abs(other.day - leg.day) <= SAME_TRADE_DAYS
        for other in legs
    )


def _proven_by_ledger(leg: _Leg, uphold_days: list[int]) -> bool:
    return any(abs(day - leg.day) <= SAME_TRADE_DAYS for day in uphold_days)


def _keep(leg: _Leg, legs: list[_Leg], uphold_days: list[int]) -> tuple[bool, bool]:
    """Keep this leg? Returns (keep, proven_only_by_ledger)."""
    if _has_reciprocal(leg, legs):
        return True, False
    if _proven_by_ledger(leg, uphold_days):
        return True, True
    return False, False


def _components(teams: set[int], edges: list[tuple[int, int]]) -> list[set[int]]:
    """Connected components over a day's team pairs.

    A three-team trade arrives as A-B and B-C (and maybe A-C). All three are one
    trade, so they have to be resolved into a single group; pairing them off
    separately is what double-bills a team in the middle.
    """
    parent = {team: team for team in teams}

    def find(team: int) -> int:
        while parent[team] != team:
            parent[team] = parent[parent[team]]
            team = parent[team]
        return team

    for left, right in edges:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_left] = root_right

    groups: dict[int, set[int]] = defaultdict(set)
    for team in teams:
        groups[find(team)].add(team)
    return list(groups.values())


def _group(legs: list[_Leg]) -> list[tuple[int, list[_Leg]]]:
    """Fold legs into trade events: one per day per connected set of teams.

    Returns (day, legs of the event) oldest first.
    """
    by_day: dict[int, list[_Leg]] = defaultdict(list)
    for leg in legs:
        by_day[leg.day].append(leg)

    events: list[tuple[int, list[_Leg]]] = []
    for day, day_legs in sorted(by_day.items()):
        teams = {team for leg in day_legs for team in (leg.from_team, leg.to_team)}
        edges = [(leg.from_team, leg.to_team) for leg in day_legs]
        for component in _components(teams, edges):
            events.append(
                (
                    day,
                    [
                        leg
                        for leg in day_legs
                        if leg.from_team in component and leg.to_team in component
                    ],
                )
            )
    return events


def reconstruct_trades(session: Session, season: int, team_id: int) -> list[Trade]:
    """Every trade a team made in a season, oldest first.

    A trade is one day and one set of counterparties. Players on each side are
    `players_in` and `players_out` from this team's point of view; a leg between
    two other teams that happens to share the day is not this team's business
    and is dropped, which is what keeps a three-way from being reported as two
    trades by the team in the middle.

    `gradeable` is false when this team's side of the movement is empty, which
    happens when the other half of the deal left no roster trace and only the
    ledger proves a trade happened.
    """
    league_season_id = _season_id(session, season)
    if league_season_id is None:
        return []

    wire = _wire_explains(session, league_season_id)
    legs = _movements(session, league_season_id, wire)
    uphold_days = _uphold_days(session, league_season_id)

    kept: list[tuple[_Leg, bool]] = []
    for leg in legs:
        keep, from_ledger = _keep(leg, legs, uphold_days)
        if keep:
            kept.append((leg, from_ledger))

    legs_of = {id(leg): flag for leg, flag in kept}
    mine = [leg for leg, _ in kept if team_id in (leg.from_team, leg.to_team)]
    if not mine:
        return []

    names = {
        int(row_id): str(name) for row_id, name in session.execute(select(Team.id, Team.name)).all()
    }

    trades: list[Trade] = []
    for day, event_legs in _group(mine):
        incoming = tuple(
            Party(leg.player_id, leg.player_name) for leg in event_legs if leg.to_team == team_id
        )
        outgoing = tuple(
            Party(leg.player_id, leg.player_name) for leg in event_legs if leg.from_team == team_id
        )
        counterparties = {
            leg.from_team if leg.to_team == team_id else leg.to_team for leg in event_legs
        }
        counterparties.discard(team_id)
        if not counterparties:
            continue

        from_ledger = any(legs_of.get(id(leg), False) for leg in event_legs)
        trades.append(
            Trade(
                day=day,
                team_id=team_id,
                counterparties=tuple(
                    sorted((cid, names.get(cid, f"team {cid}")) for cid in counterparties)
                ),
                players_in=incoming,
                players_out=outgoing,
                # One side with nothing on it is a deal whose other half was not
                # recoverable: real, but not gradeable.
                gradeable=bool(incoming and outgoing),
                from_ledger=from_ledger,
            )
        )
    return sorted(trades, key=lambda trade: (trade.day, trade.counterparty_names))
