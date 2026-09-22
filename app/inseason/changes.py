"""What changed, per league and per team: one feed the page and the digest share.

The morning question after "who starts today" is "anything I should know?".
This module answers it from stored rows only -- no ESPN, and no arithmetic
left for the reader -- as a list of typed events in one shape (`Change`),
newest first, over a window with **both** ends.

WHY BOTH ENDS

A trailing window that never closes is not a window. The digest's churn line
and its wire tally each had only a near end, and on any replayed day they
read the rest of the season: 609 moves on the wire "in the last day" where
nine had been made (docs/inseason_rehearsal.md, finding 3). Every query here
is bounded at `since` and at `until`, and a caller that means now passes it.
Nothing is read past `until`, which is what keeps a day of a played season
honest as well.

WHERE A CHANGE COMES FROM

- `player_status_events`, the listener's diff: `status`, `minutes`,
  `ownership`, `waiver_clear`, and a `drop` or `claim` the ledger has not
  named yet.
- `transactions` and `transaction_items`, EXECUTED only: `add`, `claim`
  (with the FAAB where there is any) and `drop`.
- The ledger's own trades, and `app.scoring.trades` for the ones it does not
  name: `trade`.
- `free_agent_snapshots`, one pass read against the one before it: `drop`
  (cleared onto the wire), `claim` (taken off it), `waiver_clear`.
- `daily_lineup_slots`, a change of hands nothing else explains: `lineup`.

**One transaction is one change**, not one per item: a claim that added a man
and dropped another is how a manager reads it, and counting items would
double every move on the wire. A trade is one change naming every team and
every player in it, for the same reason.

**The sources overlap, so they are deduplicated.** The listener sees a drop
hours before the nightly ingest stores the transaction behind it, and the
pool sees the same thing a third time. The ledger row is the better record --
it has the team and the money -- so anything else naming that player within a
day of it gives way.

`ownership` is here and is not one of the eight kinds this was specified
with: the digest's wire section has always carried "being added" (the crowd
reacting to news the listener has not seen yet), and rebuilding the digest on
this feed without it would quietly drop a line the owner's message has had
since the first one went out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    DailyLineupSlot,
    FreeAgentSnapshot,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    Player,
    PlayerStatusEvent,
    PlayerStatusSnapshot,
    Team,
    Transaction,
    TransactionItem,
)
from app.listener import events as kinds
from app.listener.pool import WAIVERS
from app.listener.snapshots import latest_snapshot_ids
from app.pickups.state import SeasonCalendar, period_for_day, season_calendar
from app.scoring.trades import reconstruct_trades
from app.scoring.wire import WIRE_TYPES

# --- the kinds -------------------------------------------------------------

STATUS = "status"
ADD = "add"
DROP = "drop"
CLAIM = "claim"
TRADE = "trade"
MINUTES = "minutes"
WAIVER_CLEAR = "waiver_clear"
LINEUP = "lineup"
OWNERSHIP = "ownership"

KINDS = (STATUS, ADD, DROP, CLAIM, TRADE, MINUTES, WAIVER_CLEAR, LINEUP, OWNERSHIP)

#: What comes from the listener's own events, which is all the digest's roster
#: and wire sections read. Asking for these spares a digest the whole trade
#: reconstruction, which it would never show.
EVENT_KINDS = (STATUS, MINUTES, OWNERSHIP, WAIVER_CLEAR, DROP, CLAIM)

#: The league's own business: the moves every member can see.
LEAGUE_KINDS = (ADD, CLAIM, DROP, TRADE, LINEUP)

#: The listener's kind -> this feed's kind. `dropped` and `claimed` are here
#: because the listener sees them first; the ledger says the same thing with
#: the team and the money in it, and wins where both have it.
FEED_KIND = {
    kinds.WENT_OUT: STATUS,
    kinds.DOWNGRADED: STATUS,
    kinds.UPGRADED: STATUS,
    kinds.RETURNED: STATUS,
    kinds.RETURN_DATE_CHANGED: STATUS,
    kinds.CHANGED_PRO_TEAM: STATUS,
    kinds.MINUTES_SPIKE: MINUTES,
    kinds.MINUTES_DROP: MINUTES,
    kinds.OWNERSHIP_SURGE: OWNERSHIP,
    kinds.OWNERSHIP_SLIDE: OWNERSHIP,
    kinds.WAIVER_CLEARING: WAIVER_CLEAR,
    kinds.DROPPED: DROP,
    kinds.CLAIMED: CLAIM,
}

#: How each event reads as a sentence: the verb, after the player's name.
_VERB = {
    kinds.WENT_OUT: "is out",
    kinds.DOWNGRADED: "is downgraded",
    kinds.UPGRADED: "is upgraded",
    kinds.RETURNED: "is back",
    kinds.RETURN_DATE_CHANGED: "has a new return date",
    kinds.CHANGED_PRO_TEAM: "changed NBA team",
    kinds.MINUTES_SPIKE: "is playing more",
    kinds.MINUTES_DROP: "is playing less",
    kinds.OWNERSHIP_SURGE: "is being added around ESPN",
    kinds.OWNERSHIP_SLIDE: "is being dropped around ESPN",
    kinds.WAIVER_CLEARING: "clears waivers",
    kinds.DROPPED: "was dropped",
    kinds.CLAIMED: "was claimed",
}

# --- how much a change is worth interrupting the day for -------------------

#: A man ruled out or suspended: the one thing the digest sends an alert for.
OUT = 0
#: Doubtful, questionable, a minutes drop: he may not be there.
DOUBT = 1
#: Everything else the listener saw about a player.
NEWS = 2
#: The league's business: a trade, a claim, an add, a drop.
MOVE = 3
#: A fact with nothing in it to act on today.
QUIET = 4

#: Worse to better, the digest's own order, so the roster's standing line and
#: this feed rank a status the same way.
CONCERN_ORDER = ("OUT", "SUSPENSION", "DOUBTFUL", "QUESTIONABLE", "DAY_TO_DAY")

#: Noon, for a change that has a day and not a moment: a reconstructed trade,
#: or a movement seen only in a day's lineups. Midnight would put it in the
#: day before for every reader west of UTC.
_MIDDAY = time(12, 0)

#: How far from a ledger move another source may name the same player and
#: still be the same move. ESPN stamps the transaction and writes the rosters
#: a day either side of it (`app.scoring.trades`).
_SAME_MOVE_DAYS = 1


@dataclass(frozen=True)
class Person:
    """A player named in a change. The ESPN id is what a player card needs."""

    espn_player_id: int
    name: str


@dataclass(frozen=True)
class Side:
    """A fantasy team named in a change."""

    espn_team_id: int
    name: str


@dataclass(frozen=True)
class Change:
    """One thing that happened, in the one shape everything is reported in."""

    at: datetime
    kind: str
    players: tuple[Person, ...]
    teams: tuple[Side, ...]
    #: The house-voice sentence. The page prints it and so does the digest,
    #: so the two can never disagree about what happened.
    text: str
    #: Touches the team asked about, and that team's opponent this period.
    #: Both are False when no team was named.
    mine: bool = False
    opponent: bool = False
    severity: int = MOVE
    #: The listener's own kind, when this came from one of its events: what
    #: the digest labels its roster and wire lines with.
    event_kind: str | None = None
    #: The digest's right-hand column for that line (`describe`).
    detail: str = ""
    #: The `player_status_events` row, so the owner's digest can mark it.
    event_id: int | None = None

    @property
    def on_wire(self) -> bool:
        """Nobody holds the player this is about."""
        return not self.teams


# ---------------------------------------------------------------------------
# the sentences
# ---------------------------------------------------------------------------


def _day(value: Any) -> str:
    """An ISO date from an event payload, printed short."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d %b")
    except ValueError:
        return str(value)


def describe(event: PlayerStatusEvent, teams: dict[int, str]) -> str:
    """What this event actually says: the digest's right-hand column.

    `teams` is keyed on ESPN's team id, as every snapshot and the `.env` name
    a team. It lives here rather than in the digest because the page's
    sentence is built on it, and one wording is the whole point.
    """
    previous, current, detail = event.previous, event.current, event.detail
    if event.kind in (kinds.WENT_OUT, kinds.DOWNGRADED, kinds.UPGRADED, kinds.RETURNED):
        moved = f"{previous.get('injury_status') or '?'} to {current.get('injury_status') or '?'}"
        back = _day(current.get("expected_return_date"))
        return f"{moved}, back {back}" if back else moved
    if event.kind == kinds.RETURN_DATE_CHANGED:
        days = detail.get("days")
        way = "later" if isinstance(days, int) and days > 0 else "sooner"
        return f"{_day(current.get('expected_return_date'))}, {abs(int(days or 0))} days {way}"
    if event.kind == kinds.CHANGED_PRO_TEAM:
        return f"NBA team {previous.get('pro_team_id')} to {current.get('pro_team_id')}"
    if event.kind in (kinds.MINUTES_SPIKE, kinds.MINUTES_DROP):
        return (
            f"{detail.get('recent_mean')} min last {detail.get('recent_games')},"
            f" was {detail.get('prior_mean')}"
        )
    if event.kind in (kinds.OWNERSHIP_SURGE, kinds.OWNERSHIP_SLIDE):
        return f"{current.get('percent_owned')}% owned, {current.get('percent_change'):+g} today"
    if event.kind == kinds.DROPPED:
        by = teams.get(int(detail.get("from_team_id") or 0), "another team")
        return f"by {by}"
    if event.kind == kinds.CLAIMED:
        return f"by {teams.get(int(detail.get('to_team_id') or 0), 'another team')}"
    if event.kind == kinds.WAIVER_CLEARING:
        clears = detail.get("clears_at")
        return f"at {clears[11:16]} UTC" if isinstance(clears, str) and len(clears) > 16 else ""
    return ""


def _event_sentence(event_kind: str, player: str, holder: str | None, detail: str) -> str:
    """One listener event as a sentence: who, what, and the numbers behind it."""
    who = f"{player} ({holder})" if holder else player
    verb = _VERB.get(event_kind, "changed")
    return f"{who} {verb}: {detail}." if detail else f"{who} {verb}."


def _money(bid: int | None) -> str:
    return f" for ${bid}" if bid else ""


def _listed(names: Sequence[str]) -> str:
    """Names as a sentence lists them: one, or a pair, or a series and an "and"."""
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _at_noon(day: date) -> datetime:
    return datetime.combine(day, _MIDDAY, tzinfo=UTC)


# ---------------------------------------------------------------------------
# who holds whom, and who we are playing
# ---------------------------------------------------------------------------


def _teams_by_row(session: Session, league_season: LeagueSeason) -> dict[int, Side]:
    return {
        team.id: Side(int(team.espn_team_id), team.name)
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }


def _holders(session: Session, league_season: LeagueSeason, day: int | None) -> dict[int, int]:
    """Player id -> the ESPN team id holding him, as of the window's far end.

    A season in play has lineup days for `day`, and those are this league's
    own record. Before its first lineup day -- and for the season the listener
    follows ahead of opening night -- the status snapshots stand in; they are
    one league's view of who holds whom (docs/jobs.md, "One listener league"),
    which is why the lineup days come first wherever there are any.
    """
    if day is not None:
        rows = session.execute(
            select(DailyLineupSlot.player_id, Team.espn_team_id)
            .join(Team, Team.id == DailyLineupSlot.team_id)
            .where(Team.league_season_id == league_season.id, DailyLineupSlot.scoring_period == day)
        ).all()
        if rows:
            return {int(player_id): int(espn_team_id) for player_id, espn_team_id in rows}
    snapshots = session.execute(
        select(PlayerStatusSnapshot.player_id, PlayerStatusSnapshot.on_team_id).where(
            PlayerStatusSnapshot.id.in_(latest_snapshot_ids(int(league_season.season))),
            PlayerStatusSnapshot.on_team_id.is_not(None),
            PlayerStatusSnapshot.on_team_id != 0,
        )
    ).all()
    return {int(player_id): int(team_id) for player_id, team_id in snapshots}


def opponent_of(
    session: Session, league_season: LeagueSeason, team: Team, day: int | None
) -> Team | None:
    """Who this team plays in the matchup period `day` falls in, or None.

    None on a bye, before the season, and for a day in no period at all: the
    feed still reports, with nothing flagged as the opponent's business.
    """
    if day is None:
        return None
    period = period_for_day(session, league_season, day)
    if period is None:
        return None
    found = session.scalar(
        select(Matchup).where(
            Matchup.matchup_period_id == period.id,
            or_(Matchup.home_team_id == team.id, Matchup.away_team_id == team.id),
        )
    )
    if found is None:
        return None
    other = found.away_team_id if found.home_team_id == team.id else found.home_team_id
    return session.get(Team, other) if other is not None else None


# ---------------------------------------------------------------------------
# the listener's events
# ---------------------------------------------------------------------------


def _event_severity(event: PlayerStatusEvent) -> int:
    """How much this is worth interrupting the day for."""
    if event.kind in (kinds.WENT_OUT, kinds.DOWNGRADED, kinds.UPGRADED, kinds.RETURNED):
        status = str(event.current.get("injury_status") or "").upper()
        rank = CONCERN_ORDER.index(status) if status in CONCERN_ORDER else len(CONCERN_ORDER)
        return OUT if rank == 0 else DOUBT if rank < len(CONCERN_ORDER) else NEWS
    if event.kind == kinds.MINUTES_DROP:
        return DOUBT
    if event.kind in (kinds.DROPPED, kinds.CLAIMED):
        return MOVE
    if event.kind == kinds.WAIVER_CLEARING:
        return QUIET
    return NEWS


def _status_changes(
    session: Session,
    league_season: LeagueSeason,
    *,
    since: datetime,
    until: datetime,
    holders: dict[int, int],
    sides: dict[int, Side],
    unnotified: bool,
) -> list[Change]:
    """The listener's own diff, over the window and nothing outside it."""
    query = select(PlayerStatusEvent).where(
        PlayerStatusEvent.season == int(league_season.season),
        PlayerStatusEvent.kind.in_(sorted(FEED_KIND)),
        PlayerStatusEvent.observed_at > since,
        PlayerStatusEvent.observed_at <= until,
    )
    if unnotified:
        query = query.where(PlayerStatusEvent.notified_at.is_(None))
    found = session.scalars(
        query.options(selectinload(PlayerStatusEvent.player)).order_by(
            PlayerStatusEvent.observed_at.desc(), PlayerStatusEvent.id.desc()
        )
    ).all()

    by_espn_id = {side.espn_team_id: side for side in sides.values()}
    names = {espn_team_id: side.name for espn_team_id, side in by_espn_id.items()}
    out: list[Change] = []
    for event in found:
        holder = by_espn_id.get(holders.get(event.player_id, 0))
        detail = describe(event, names)
        out.append(
            Change(
                at=event.observed_at,
                kind=FEED_KIND[event.kind],
                players=(Person(int(event.player.espn_player_id), event.player.name),),
                teams=(holder,) if holder is not None else (),
                text=_event_sentence(
                    event.kind, event.player.name, holder.name if holder else None, detail
                ),
                severity=_event_severity(event),
                event_kind=event.kind,
                detail=detail,
                event_id=event.id,
            )
        )
    return out


# ---------------------------------------------------------------------------
# the transaction ledger
# ---------------------------------------------------------------------------


def _ledger_changes(
    session: Session,
    league_season: LeagueSeason,
    *,
    since: datetime,
    until: datetime,
    sides: dict[int, Side],
) -> list[Change]:
    """Every executed move on the wire, one change per transaction.

    A transaction carrying an ADD is a claim (WAIVER, which costs FAAB here)
    or an add (FREEAGENT, free); one carrying only drops is a drop. A row
    ESPN never stamped cannot be placed in a window at all, so it is left
    out, exactly as the digest's churn line leaves it out.
    """
    rows = session.scalars(
        select(Transaction)
        .where(
            Transaction.league_season_id == league_season.id,
            Transaction.type.in_(WIRE_TYPES),
            Transaction.status == "EXECUTED",
            Transaction.processed_at.is_not(None),
            Transaction.processed_at >= since,
            Transaction.processed_at <= until,
        )
        .options(selectinload(Transaction.items).selectinload(TransactionItem.player))
        .order_by(Transaction.processed_at.desc(), Transaction.id.desc())
    ).all()

    out: list[Change] = []
    for row in rows:
        added = _people(row, "ADD")
        dropped = _people(row, "DROP")
        if not added and not dropped:
            continue
        side = sides.get(row.team_id) if row.team_id is not None else None
        who = side.name if side is not None else "Someone"
        if added:
            kind = CLAIM if row.type == "WAIVER" else ADD
            verb = "claimed" if kind == CLAIM else "added"
            text = f"{who} {verb} {_listed([p.name for p in added])}{_money(row.bid_amount)}"
            text += f", dropping {_listed([p.name for p in dropped])}." if dropped else "."
        else:
            kind = DROP
            text = f"{who} dropped {_listed([p.name for p in dropped])}."
        out.append(
            Change(
                at=row.processed_at or until,
                kind=kind,
                players=added + dropped,
                teams=(side,) if side is not None else (),
                text=text,
                severity=MOVE,
            )
        )
    return out


def _people(row: Transaction, item_type: str) -> tuple[Person, ...]:
    return tuple(
        Person(int(item.player.espn_player_id), item.player.name)
        for item in row.items
        if item.item_type == item_type
    )


# ---------------------------------------------------------------------------
# trades: the ledger's, then the ones only the rosters show
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawTrade:
    """A trade before it is dated: the day, the teams in it, and its sentence."""

    day: int
    stamped: datetime | None
    involved: frozenset[int]
    players: tuple[Person, ...]
    teams: tuple[Side, ...]
    text: str


def _ledger_trades(
    session: Session, league_season: LeagueSeason, sides: dict[int, Side]
) -> list[_RawTrade]:
    """Trades ESPN itself named, with the players and the direction.

    Only a handful of rows carry items at all, and some carry them with no
    team on either side, which says a trade happened and nothing more. Those
    are left to `app.scoring.trades`, which reads the direction off the
    rosters instead.
    """
    rows = session.scalars(
        select(Transaction)
        .where(
            Transaction.league_season_id == league_season.id,
            Transaction.type == "TRADE_ACCEPT",
            Transaction.status == "EXECUTED",
        )
        .options(selectinload(Transaction.items).selectinload(TransactionItem.player))
        .order_by(Transaction.id)
    ).all()

    out: list[_RawTrade] = []
    for row in rows:
        legs = [
            item
            for item in row.items
            if item.from_team_id is not None or item.to_team_id is not None
        ]
        if not legs:
            continue
        involved = {
            team_id
            for item in legs
            for team_id in (item.from_team_id, item.to_team_id)
            if team_id is not None
        }
        named = tuple(
            sorted(
                (sides[team_id] for team_id in involved if team_id in sides),
                key=lambda side: side.name,
            )
        )
        moves = [
            f"{item.player.name} to {sides[item.to_team_id].name}"
            for item in legs
            if item.to_team_id in sides
        ]
        text = f"{_listed([side.name for side in named])} traded"
        text += f": {', '.join(moves)}." if moves else "."
        out.append(
            _RawTrade(
                day=int(row.scoring_period),
                stamped=row.processed_at,
                involved=frozenset(involved),
                players=tuple(
                    Person(int(item.player.espn_player_id), item.player.name) for item in legs
                ),
                teams=named,
                text=text,
            )
        )
    return out


def _reconstructed_trades(
    session: Session, league_season: LeagueSeason, sides: dict[int, Side]
) -> list[_RawTrade]:
    """The trades the ledger does not name, read off the rosters.

    `reconstruct_trades` answers for one team, so one deal comes back once
    per team in it; they are folded here into one trade per day per connected
    set of teams, which is what a reader means by "a trade".
    """
    grouped: dict[tuple[int, frozenset[int]], list[tuple[Side | None, tuple[str, ...]]]] = {}
    for team_id in sides:
        for trade in reconstruct_trades(session, int(league_season.season), team_id):
            key = (trade.day, frozenset({trade.team_id, *trade.counterparty_ids}))
            grouped.setdefault(key, []).append(
                (sides.get(trade.team_id), tuple(party.name for party in trade.players_in))
            )

    wanted = {name for legs in grouped.values() for _side, names in legs for name in names}
    espn_ids = dict(
        session.execute(select(Player.name, Player.espn_player_id).where(Player.name.in_(wanted)))
        .tuples()
        .all()
    )

    out: list[_RawTrade] = []
    for (day, involved), legs in grouped.items():
        moves: list[str] = []
        people: list[Person] = []
        for side, names in sorted(legs, key=lambda leg: leg[0].name if leg[0] else ""):
            for name in names:
                moves.append(f"{name} to {side.name if side else 'the other side'}")
                people.append(Person(int(espn_ids.get(name, 0)), name))
        named = tuple(
            sorted((sides[team] for team in involved if team in sides), key=lambda s: s.name)
        )
        text = f"{_listed([side.name for side in named])} traded"
        if moves:
            text += f": {', '.join(moves)}."
        if len(people) < 2:
            text += " The other side of it is not in the record."
        out.append(
            _RawTrade(
                day=day,
                stamped=None,
                involved=involved,
                players=tuple(people),
                teams=named,
                text=text,
            )
        )
    return out


def _trades(
    session: Session,
    league_season: LeagueSeason,
    sides: dict[int, Side],
    calendar: SeasonCalendar | None,
    *,
    since: datetime,
    until: datetime,
) -> list[Change]:
    """Both kinds of trade, dated, inside the window.

    A reconstructed trade has a day and not a moment -- the last day the
    players it moved appeared for the side giving them up -- so it is dated
    at noon on that day. The ledger's own stamp is used wherever there is
    one, and a reconstructed trade a day either side of a ledger one, between
    any of the same teams, is that same deal and is dropped.
    """
    named = _ledger_trades(session, league_season, sides)
    found = list(named)
    if calendar is not None:
        for trade in _reconstructed_trades(session, league_season, sides):
            if any(
                abs(trade.day - other.day) <= _SAME_MOVE_DAYS and trade.involved & other.involved
                for other in named
            ):
                continue
            found.append(trade)

    out: list[Change] = []
    for trade in found:
        at = trade.stamped or (_at_noon(calendar.date_of(trade.day)) if calendar else None)
        if at is None or not (since <= at <= until):
            continue
        out.append(
            Change(
                at=at,
                kind=TRADE,
                players=trade.players,
                teams=trade.teams,
                text=trade.text,
                severity=MOVE,
            )
        )
    return out


# ---------------------------------------------------------------------------
# the wire's own pool, and the movements nothing explains
# ---------------------------------------------------------------------------


def _wire_changes(
    session: Session, league_season: LeagueSeason, *, since: datetime, until: datetime
) -> list[Change]:
    """The pool, one pass against the one before it.

    A man on the wire now who was not on it at the pass before was dropped
    and has cleared onto it; one who was on it and is gone has been claimed;
    one who was on waivers and is a free agent now has cleared them. This is
    the only one of these sources a league the listener does not follow in
    full has, its pass writing the wire and nothing else (docs/jobs.md, "One
    listener league").
    """
    passes = list(
        session.scalars(
            select(FreeAgentSnapshot.observed_at)
            .where(
                FreeAgentSnapshot.league_season_id == league_season.id,
                FreeAgentSnapshot.observed_at <= until,
            )
            .distinct()
            .order_by(FreeAgentSnapshot.observed_at)
        ).all()
    )
    inside = [at for at in passes if at > since]
    if not inside:
        return []
    # The last pass before the window opens is the baseline the first pass
    # inside it is read against; without it the whole wire reads as new.
    earlier = [at for at in passes if at <= since]
    wanted = ([earlier[-1]] if earlier else []) + inside

    rows = session.execute(
        select(
            FreeAgentSnapshot.observed_at,
            FreeAgentSnapshot.status,
            Player.espn_player_id,
            Player.name,
        )
        .join(Player, Player.id == FreeAgentSnapshot.player_id)
        .where(
            FreeAgentSnapshot.league_season_id == league_season.id,
            FreeAgentSnapshot.observed_at.in_(wanted),
        )
    ).all()
    pools: dict[datetime, dict[int, tuple[str, str]]] = {at: {} for at in wanted}
    for at, status, espn_player_id, name in rows:
        pools[at][int(espn_player_id)] = (str(status), str(name))

    out: list[Change] = []
    for before, after in pairwise(wanted):
        was, now = pools[before], pools[after]
        for espn_player_id, (status, name) in sorted(now.items()):
            if espn_player_id not in was:
                out.append(
                    _wire_change(after, DROP, espn_player_id, name, f"{name} is on the wire now.")
                )
            elif was[espn_player_id][0] == WAIVERS and status != WAIVERS:
                out.append(
                    _wire_change(
                        after,
                        WAIVER_CLEAR,
                        espn_player_id,
                        name,
                        f"{name} cleared waivers and is a free agent now.",
                    )
                )
        for espn_player_id, (_status, name) in sorted(was.items()):
            if espn_player_id not in now:
                out.append(
                    _wire_change(
                        after,
                        CLAIM,
                        espn_player_id,
                        name,
                        f"{name} is off the wire; someone took him.",
                    )
                )
    return out


def _wire_change(at: datetime, kind: str, espn_player_id: int, name: str, text: str) -> Change:
    return Change(
        at=at,
        kind=kind,
        players=(Person(espn_player_id, name),),
        teams=(),
        text=text,
        severity=QUIET if kind == WAIVER_CLEAR else MOVE,
    )


def _lineup_moves(
    session: Session, league_season: LeagueSeason, sides: dict[int, Side]
) -> list[tuple[int, bool, Change]]:
    """Every change of hands the lineups show: the day, whether he arrived.

    A man cannot sit in a lineup for a team that does not hold him, so the
    first and last day of each spell are when he arrived and when he left.
    The season's own first and last lineup days are neither: nobody arrives
    on opening night and nobody leaves when the lineups simply stop.

    The caller drops whatever the ledger or a trade already accounts for,
    which over 2026's days 50 to 56 was every movement there was.
    """
    rows = session.execute(
        select(
            Player.espn_player_id,
            Player.name,
            DailyLineupSlot.team_id,
            func.min(DailyLineupSlot.scoring_period),
            func.max(DailyLineupSlot.scoring_period),
        )
        .join(Player, Player.id == DailyLineupSlot.player_id)
        .join(MatchupPeriod, MatchupPeriod.id == DailyLineupSlot.matchup_period_id)
        .where(MatchupPeriod.league_season_id == league_season.id)
        .group_by(Player.espn_player_id, Player.name, DailyLineupSlot.team_id)
    ).all()
    if not rows:
        return []
    opened = min(int(first) for *_rest, first, _last in rows)
    closed = max(int(last) for *_rest, _first, last in rows)

    out: list[tuple[int, bool, Change]] = []
    for espn_player_id, name, team_id, first, last in rows:
        side = sides.get(int(team_id))
        if side is None:
            continue
        if int(first) > opened:
            out.append((int(first), True, _lineup_change(espn_player_id, name, side, joined=True)))
        if int(last) < closed:
            out.append((int(last), False, _lineup_change(espn_player_id, name, side, joined=False)))
    return out


def _lineup_change(espn_player_id: int, name: str, side: Side, *, joined: bool) -> Change:
    where = "is on" if joined else "is off"
    return Change(
        at=datetime.now(UTC),  # dated by the caller, which holds the calendar
        kind=LINEUP,
        players=(Person(int(espn_player_id), str(name)),),
        teams=(side,),
        text=f"{name} {where} {side.name}'s roster; no transaction in the record says how.",
        severity=QUIET,
    )


def _unexplained(
    session: Session,
    league_season: LeagueSeason,
    *,
    sides: dict[int, Side],
    calendar: SeasonCalendar,
    since: datetime,
    until: datetime,
    explained: set[tuple[int, date]],
) -> list[Change]:
    """The lineup movements no transaction and no trade accounts for.

    A departure needs the day after it as well: a man is only known to have
    left once a day has been seen without him, and the transaction that
    dropped him is stamped on that same day. So a spell that ends on the
    window's own last day says nothing yet, and waits for the next window
    rather than being reported here and again by the ledger tomorrow.
    """
    out: list[Change] = []
    for day, joined, change in _lineup_moves(session, league_season, sides):
        on = calendar.date_of(day)
        if not joined and _at_noon(calendar.date_of(day + 1)) > until:
            continue
        if any((person.espn_player_id, on) in explained for person in change.players):
            continue
        at = _at_noon(on)
        if since <= at <= until:
            out.append(replace(change, at=at))
    return out


def _spoken_for(found: Sequence[Change]) -> set[tuple[int, date]]:
    """(player, day) pairs a ledger row or a trade already accounts for."""
    return {
        (person.espn_player_id, (change.at + timedelta(days=offset)).date())
        for change in found
        for person in change.players
        for offset in (-_SAME_MOVE_DAYS, 0, _SAME_MOVE_DAYS)
    }


# ---------------------------------------------------------------------------
# the feed
# ---------------------------------------------------------------------------


def changes(
    session: Session,
    league_season: LeagueSeason,
    *,
    since: datetime,
    until: datetime,
    team_id: int | None = None,
    kinds_wanted: Sequence[str] | None = None,
    unnotified: bool = False,
) -> list[Change]:
    """Everything that happened in this league between `since` and `until`.

    Newest first. `team_id` is an ESPN team id: it adds the `mine` and
    `opponent` flags and changes nothing else, because every fact here is the
    league's own and every member of it may read all of them.

    `kinds_wanted` narrows the feed before the work is done, which is how the
    digest reads the listener's events without paying for the trade
    reconstruction. `unnotified` restricts those events to the ones never
    delivered, which is the owner's digest and nothing else (docs/jobs.md,
    "The digest job").
    """
    wanted = set(kinds_wanted) if kinds_wanted is not None else set(KINDS)
    calendar = season_calendar(session, int(league_season.season))
    last_day = calendar.scoring_period_on(until.date()) if calendar is not None else None

    sides = _teams_by_row(session, league_season)
    mine, other = _both_sides(session, league_season, sides, team_id, last_day)

    events: list[Change] = []
    if wanted & set(EVENT_KINDS):
        events = _status_changes(
            session,
            league_season,
            since=since,
            until=until,
            holders=_holders(session, league_season, last_day),
            sides=sides,
            unnotified=unnotified,
        )
    # The move behind a change seen today was often stamped yesterday: ESPN
    # writes the rosters a day either side of the transaction. So the ledger
    # and the trades are read from a day before the window, to know what is
    # already accounted for, and only then narrowed to the window itself.
    # Earlier is not look-ahead; the far end stays exactly where it was.
    opened = since - timedelta(days=_SAME_MOVE_DAYS)
    ledger: list[Change] = []
    if wanted & {ADD, CLAIM, DROP}:
        ledger = _ledger_changes(session, league_season, since=opened, until=until, sides=sides)
    pool: list[Change] = []
    if wanted & {DROP, CLAIM, WAIVER_CLEAR}:
        pool = _wire_changes(session, league_season, since=since, until=until)
    trades: list[Change] = []
    if TRADE in wanted:
        trades = _trades(session, league_season, sides, calendar, since=opened, until=until)

    # The ledger is the better record of a move, so it silences the other
    # sources about the same player on the same day.
    settled = _spoken_for(ledger)
    found = [change for change in ledger + trades if change.at >= since]
    found += [change for change in events if _not_said(change, settled)]
    found += [change for change in pool if _not_said(change, settled)]

    if LINEUP in wanted and calendar is not None:
        found += _unexplained(
            session,
            league_season,
            sides=sides,
            calendar=calendar,
            since=since,
            until=until,
            explained=_spoken_for(ledger + trades),
        )

    out = [
        replace(change, mine=_touches(change, mine), opponent=_touches(change, other))
        for change in found
        if change.kind in wanted
    ]
    out.sort(key=lambda change: change.text)
    out.sort(key=lambda change: change.severity)
    out.sort(key=lambda change: change.at, reverse=True)
    return out


def _both_sides(
    session: Session,
    league_season: LeagueSeason,
    sides: dict[int, Side],
    team_id: int | None,
    day: int | None,
) -> tuple[Side | None, Side | None]:
    """The team asked about and its opponent this period, as named sides."""
    if team_id is None:
        return None, None
    row = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if row is None:
        return None, None
    found = opponent_of(session, league_season, row, day)
    return sides.get(row.id), (sides.get(found.id) if found is not None else None)


def _not_said(change: Change, settled: set[tuple[int, date]]) -> bool:
    """Whether the ledger has not already reported this move."""
    if change.kind not in (DROP, CLAIM):
        return True
    return not any(
        (person.espn_player_id, change.at.date()) in settled for person in change.players
    )


def _touches(change: Change, side: Side | None) -> bool:
    return side is not None and any(
        named.espn_team_id == side.espn_team_id for named in change.teams
    )
