"""What a member wants to hear about, per league.

The digest is a manager's morning, and no two managers want the same
morning. One wants today's lineup and nothing else; one reads every add and
drop the league makes; one only wants to know when his own man is ruled out.
So the message is not one thing a reader takes or leaves: it is a small set
of named topics, each on or off, per (member, league), and the email's
sections are exactly the ones he chose, in the order below.

THE TOPICS, IN THE ORDER THEY ARE READ

| topic | what it puts in the email |
|---|---|
| `lineup` | today's lineup: the fix-this line and the starters |
| `moves` | the week's and the season's pickups that clear the bar |
| `my_team` | adds, drops and claims on my team; status changes on my roster |
| `opponent` | this week's opponent: his moves and his injuries |
| `league_transactions` | every add, drop, claim and trade in the league |
| `league_injuries` | every status change in the league |
| `trades` | trades and proposals, and the block once there is one |
| `standings` | my place, my record, the projected finish once it exists |

`morning` and `alerts` are not topics: they say whether a message is sent at
all. `morning` is the digest; `alerts` is the urgent roster change
`app.digest.build_alert` sends between digests, and an alert is filtered by
the same topics as the digest -- an alert about the opponent's injury only
reaches someone subscribed to his opponent.

THE DEFAULTS

Lineup, my moves, my team, my opponent, trades and standings on; the two
league-wide feeds off. The busy sections are the ones that would make a new
member unsubscribe from the whole thing on his second morning: on 2026 day
107, the season's busiest, the league's own transaction feed ran to 4,392
characters on its own. A trade is on by default because a league sees a
handful in a season and every one of them is worth reading.

**The owner's tracked team keeps everything on in single mode**: single mode
is one person reading his own server, and nothing there should be silently
missing (`app.job_kinds`).

**Every topic off means no digest.** The job says so and sends nothing,
rather than mailing a masthead with nothing under it.

FILTERING THE FEED

`app.inseason.changes` already carries what the topics need: each `Change`
knows its kind, whether it touches the reader's team (`mine`) and whether it
touches his opponent this week (`opponent`). `allows` reads those three and
nothing else, so this module does not need to know how the feed is built and
`changes.py` did not have to learn about subscriptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DigestSubscription
from app.inseason import changes as feed
from app.inseason.changes import Change

LINEUP = "lineup"
MOVES = "moves"
MY_TEAM = "my_team"
OPPONENT = "opponent"
LEAGUE_TRANSACTIONS = "league_transactions"
LEAGUE_INJURIES = "league_injuries"
TRADES = "trades"
STANDINGS = "standings"

#: Every topic, in the order the email reads them.
TOPICS = (
    LINEUP,
    MOVES,
    MY_TEAM,
    OPPONENT,
    LEAGUE_TRANSACTIONS,
    LEAGUE_INJURIES,
    TRADES,
    STANDINGS,
)

#: What the Alerts page and the email call each one. One wording, so the
#: checkbox a member ticks and the heading he then reads are the same words.
LABELS = {
    LINEUP: "Today's lineup",
    MOVES: "My moves worth a look",
    MY_TEAM: "My team's transactions and injuries",
    OPPONENT: "My opponent this week",
    LEAGUE_TRANSACTIONS: "League transactions",
    LEAGUE_INJURIES: "League injuries",
    TRADES: "Trades",
    STANDINGS: "Standings and projections",
}

#: And one line of what it actually puts in the message.
NOTES = {
    LINEUP: "The place going empty tonight, and who starts.",
    MOVES: "The week's and the season's pickups that clear the bar, with their numbers.",
    MY_TEAM: "Adds, drops and claims on your team, and status changes on your roster.",
    OPPONENT: "What the man you are playing this week did, and who he has hurt.",
    LEAGUE_TRANSACTIONS: "Every add, drop and claim anyone made. Busy in a busy week.",
    LEAGUE_INJURIES: "Every status change in the league, not only on your roster.",
    TRADES: "Trades and proposals around the league.",
    STANDINGS: "Where you stand, and where the season is heading.",
}

DEFAULTS = {
    LINEUP: True,
    MOVES: True,
    MY_TEAM: True,
    OPPONENT: True,
    LEAGUE_TRANSACTIONS: False,
    LEAGUE_INJURIES: False,
    TRADES: True,
    STANDINGS: True,
}

#: The league's own paperwork: who got whom. A `lineup` change -- a movement
#: the ledger never named -- belongs here too, because to a reader it is a
#: player changing hands like any other.
TRANSACTION_KINDS = (feed.ADD, feed.CLAIM, feed.DROP, feed.LINEUP)
#: News about a man rather than about a move.
INJURY_KINDS = (feed.STATUS, feed.MINUTES, feed.WAIVER_CLEAR, feed.OWNERSHIP)


@dataclass(frozen=True)
class Subscription:
    """One member's answer for one league. Immutable: the job reads it and
    the routes replace it."""

    topics: dict[str, bool] = field(default_factory=lambda: dict(DEFAULTS))
    #: The morning digest.
    morning: bool = True
    #: The urgent roster change between digests.
    alerts: bool = True

    def on(self, topic: str) -> bool:
        """Whether this topic is switched on. An unknown name is off: a topic
        this version does not have cannot be subscribed to by accident."""
        if topic not in DEFAULTS:
            return False
        return bool(self.topics.get(topic, DEFAULTS[topic]))

    @property
    def silent(self) -> bool:
        """Every topic off: there is no message to make."""
        return not any(self.on(topic) for topic in TOPICS)

    @property
    def chosen(self) -> tuple[str, ...]:
        """The topics he wants, in the order the email reads them."""
        return tuple(topic for topic in TOPICS if self.on(topic))

    def allows(self, change: Change) -> bool:
        """Whether this change belongs in his message.

        A change passes when **any** topic he holds admits it, because the
        topics overlap on purpose: a drop on his own roster is his team's
        news and the league's, and a reader who wants either should see it
        once.
        """
        if self.on(MY_TEAM) and change.mine:
            return True
        if self.on(OPPONENT) and change.opponent:
            return True
        if self.on(LEAGUE_TRANSACTIONS) and change.kind in TRANSACTION_KINDS:
            return True
        if self.on(LEAGUE_INJURIES) and change.kind in INJURY_KINDS:
            return True
        return bool(self.on(TRADES) and change.kind == feed.TRADE)

    def filtered(self, changes: list[Change]) -> list[Change]:
        """The feed as he asked for it, in the order it came."""
        return [change for change in changes if self.allows(change)]


def everything() -> Subscription:
    """Every topic on, both cadences on: the owner's tracked team in single
    mode, and what a preview renders so a whole email can be looked at."""
    return Subscription(topics=dict.fromkeys(TOPICS, True))


def clean(topics: dict[str, object] | None) -> dict[str, bool]:
    """A stored or submitted map, read into the topics this version has.

    An unknown key is dropped and a missing one takes its default, so a row
    written by an older version still reads and a body from a page that is
    ahead of the server cannot write a topic that does not exist.
    """
    given = topics or {}
    return {topic: bool(given.get(topic, DEFAULTS[topic])) for topic in TOPICS}


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------


def _row(session: Session, user_id: int, league_id: int) -> DigestSubscription | None:
    return session.scalar(
        select(DigestSubscription).where(
            DigestSubscription.user_id == user_id,
            DigestSubscription.league_id == league_id,
        )
    )


def for_member(session: Session, user_id: int, league_id: int) -> Subscription:
    """His subscription in this league; the defaults when he has never said.

    No row is written on a read: a member who is happy with the defaults
    never needs one, which is why nothing had to be backfilled.
    """
    row = _row(session, user_id, league_id)
    if row is None:
        return Subscription()
    return Subscription(topics=clean(row.topics), morning=row.morning, alerts=row.alerts)


def save(
    session: Session,
    user_id: int,
    league_id: int,
    *,
    topics: dict[str, object] | None = None,
    morning: bool = True,
    alerts: bool = True,
) -> Subscription:
    """Write what he chose, replacing whatever was there. Does not commit."""
    wanted = clean(topics)
    row = _row(session, user_id, league_id)
    if row is None:
        row = DigestSubscription(user_id=user_id, league_id=league_id)
        session.add(row)
    row.topics = dict(wanted)
    row.morning = bool(morning)
    row.alerts = bool(alerts)
    row.updated_at = datetime.now(UTC)
    session.flush()
    return Subscription(topics=wanted, morning=row.morning, alerts=row.alerts)
