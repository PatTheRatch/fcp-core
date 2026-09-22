"""The morning digest: what changed, for the one team being tracked.

Layer 3 of docs/pickups.md, phase 1b. Built from stored rows only: no ESPN
request. Seven sections:

1. The tracked roster, from the listener's events (went out, returned, a
   moved return date, a minutes drop), then where that roster stands now.
2. The wire, from events on unrostered players worth a look (a minutes
   spike, an ownership surge, dropped by a rival, waivers clearing).
3. Today: who to start, and the place that will produce nothing tonight
   while a man on the bench would have (`app.pickups.today`). The morning
   question, so it sits above the week.
4. This week: the matchup as it stands and the day's streaming plan, from
   `app.pickups.stream` (section 3 of the design note).
5. Churn, the team's adds in the last fortnight, because this league's own
   history says the heavier movers returned less per move
   (docs/acquirable_value.md, r = -0.63 between add volume and return).
6. The season: where it ends on this roster, and the one move over the rest
   of it that would move it (`app.pickups.season`).
7. Standings: where he stands on matchups and on categories. The projected
   finish is a marked slot until that work lands.

TWO SHAPES, ONE BUILD

The message is sent as an email with two parts (`app/mail/`): the HTML page
a manager reads, and `render()`, the plain text, beside it. Both are made
from one `Digest`, which carries the recommender's own objects as well as
the lines built from them -- a lineup is a grid in the HTML and a sentence
in the text, and a grid cannot be recovered from a sentence.

WHAT IS IN IT IS THE READER'S

`build_digest` takes a `Subscription` (`app.subscriptions`): the named
topics he switched on, per league. A section he did not ask for is not built
and not rendered, so a reader who wants only today's lineup does not pay for
the rest-of-season search.

THE PLAN NEVER BREAKS THE DIGEST

Sections 3, 4 and 6 are the ones that run the recommender, and it needs a
schedule, a roster, a wire and a matchup period. On a bye, before the
season's first matchup, or with any of those missing, each section is one
line saying so and the rest are untouched. `today_block`, `week_block` and
`season_outlook` therefore catch everything, including exceptions they
cannot name: a digest that fails to go out because a pickup report could not
be built would lose the roster news too, which is the part that is always
worth reading.

Every event the digest reports on, including the ones it summarises as "and
N more", is marked `notified_at` by the caller once delivery has actually
succeeded. Kinds the digest never shows are never queried and never marked.
The plan marks nothing: it reports state, not news.

ONE FEED, TWO SURFACES

Sections 1, 2 and the league section are built on `app.inseason.changes`,
which is also what the "What changed" section of the league's This week page
reads. The sentence beside a player is written once, there, so the message
and the page can never disagree about what happened. Two consequences worth
knowing:

- **The worst news leads its moment.** Two events from one pass used to come
  out in whatever order the rows were written in; the feed ranks them by
  concern, so a man ruled out is read before a man downgraded.
- **A drop the ledger has already named is the league section's**, not the
  wire's. The feed drops the listener's account of a move in favour of the
  transaction behind it, which has the team and the money in it, and the
  league section says it properly: "Load Management claimed X for $4,
  dropping Y."

MORE THAN ONE READER (step 4, docs/jobs.md)

`notified_at` is one column on the event, so it can only mean "the owner has
been told". A member's digest, sent by the `digest` job, passes `since`
instead: the events observed since his own last digest went out, and marks
nothing. The owner's digest keeps marking, exactly as `scripts/digest.py`
always has, and now looks back `OWNER_BACKLOG` rather than to the beginning
of time. `league_section` is the free part every member gets: the week's
matchups as they stand, and what the league did in the last day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    LeagueSeason,
    Matchup,
    MatchupPeriod,
    PlayerStatusEvent,
    PlayerStatusSnapshot,
    Team,
    Transaction,
    TransactionItem,
)
from app.inseason import changes as feed

# `describe` is the feed's now, and is re-exported under its old name: it is
# one sentence, written once, and this is where it used to live.
from app.inseason.changes import EVENT_KINDS, Change, describe
from app.inseason.changes import changes as what_changed
from app.listener import events as kinds
from app.listener.snapshots import latest_snapshots
from app.pickups.judge import Judgement
from app.pickups.season import SeasonReport, Swap, season_recommendations
from app.pickups.state import RosteredPlayer, period_for_day, season_calendar
from app.pickups.stream import ADD, IR_MOVE, Move, StreamReport, stream_recommendations
from app.pickups.today import DayPlayer, TodayReport, today_lineup
from app.scoring.wire import WIRE_TYPES

# The topics a reader chooses between (`app.subscriptions`), named here so
# the sections and the choices share one vocabulary. Nothing else about
# subscriptions reaches this module: it is told which topics to build.
from app.subscriptions import LINEUP, MOVES, MY_TEAM, STANDINGS, Subscription
from app.subscriptions import everything as subscriptions_everything

#: What a change to your own player can be: anything that moves whether he
#: plays, or how much. An ownership move on a player you already hold tells
#: you nothing you can act on, so it is not here.
ROSTER_KINDS = (
    kinds.WENT_OUT,
    kinds.DOWNGRADED,
    kinds.UPGRADED,
    kinds.RETURNED,
    kinds.RETURN_DATE_CHANGED,
    kinds.CHANGED_PRO_TEAM,
    kinds.MINUTES_SPIKE,
    kinds.MINUTES_DROP,
)

#: What makes an unrostered player worth a look this morning.
WIRE_KINDS = (
    kinds.MINUTES_SPIKE,
    kinds.OWNERSHIP_SURGE,
    kinds.DROPPED,
    kinds.WAIVER_CLEARING,
)

#: Worth interrupting the day for, between digests. Deliberately one kind:
#: an alert that fires for everything is an alert nobody reads.
URGENT_KINDS = (kinds.WENT_OUT,)

#: Line budgets. Anything past a cap is counted, not listed, and the events
#: route has the rest.
ROSTER_EVENT_LIMIT = 8
WIRE_EVENT_LIMIT = 10
#: A roster with seven players carrying a status at once is exceptional, and
#: the count line below still says how many there are.
STATUS_LINE_LIMIT = 6
#: Empty days named before the rest are counted. Three is already a bad week.
EMPTY_DAY_LIMIT = 3
#: The whole message's cap, which is what makes it readable on a phone. It
#: was forty before the week section; that section is a header, at most
#: `EMPTY_DAY_LIMIT` + 1 empty-day lines and at most `PLAN_MOVES` move lines,
#: so a dozen more kept every other section's budget exactly where it was.
#: The day's lineup added a header, its starters on one line, at most
#: `TODAY_FIX_LIMIT` + 1 lines about places worth fixing, at most
#: `TODAY_SIT_LIMIT` men with a game and no place, and one line of who is
#: not playing -- eleven at the very worst, and three on an ordinary
#: morning. Twelve more, for the same reason: nothing else loses a line.
#: The season section is a header and at most four lines and the standings
#: section a header and three, so eleven more again.
MAX_LINES = 75

#: A cap on the whole text message, in characters. It was Telegram's 4,096
#: until 2026-09-22, when the chat channel was retired and email became the
#: only one; an inbox has no such limit, and the cap is kept because it is
#: what every section's line budget was weighed against and because a text
#: part nobody can read to the end is no fallback at all. Not enforced here:
#: the line budgets are what keep the message short, and
#: `tests/test_digest.py` holds the worst case to this.
TEXT_LIMIT = 4096

#: The window the churn line counts over, from docs/pickups.md section 4.4.
CHURN_DAYS = 14

#: How far back the owner's digest looks for news it has never sent.
#:
#: His window is "everything not yet reported", which had no near end at all
#: and so was not a window (docs/inseason_rehearsal.md, finding 3). A
#: fortnight is far longer than the passes have ever fallen behind, and an
#: event older than that is not news: it stays unnotified and the events
#: route still has it.
OWNER_BACKLOG = timedelta(days=14)

#: Statuses worth naming in the roster's standing line.
_CONCERNING = ("OUT", "SUSPENSION", "DOUBTFUL", "QUESTIONABLE", "DAY_TO_DAY")

_KIND_LABELS = {
    kinds.WENT_OUT: "out",
    kinds.DOWNGRADED: "downgraded",
    kinds.UPGRADED: "upgraded",
    kinds.RETURNED: "back",
    kinds.RETURN_DATE_CHANGED: "return moved",
    kinds.CHANGED_PRO_TEAM: "traded",
    kinds.MINUTES_SPIKE: "minutes up",
    kinds.MINUTES_DROP: "minutes down",
    kinds.OWNERSHIP_SURGE: "being added",
    kinds.OWNERSHIP_SLIDE: "being dropped",
    kinds.DROPPED: "dropped",
    kinds.CLAIMED: "claimed",
    kinds.WAIVER_CLEARING: "clears waivers",
}


@dataclass(frozen=True)
class Line:
    """One reported event, already rendered down to its three columns."""

    kind: str
    player: str
    detail: str


def _line(change: Change) -> Line | None:
    """One change as a digest line, or None when it is not the digest's.

    The feed carries the ledger's account of a move as well as the
    listener's, and the ledger's has no `event_kind`: those are the league
    section's business, where the team and the money can be said properly.
    """
    if change.event_kind is None or not change.players:
        return None
    return Line(change.event_kind, change.players[0].name, change.detail)


@dataclass
class Digest:
    """What the digest found. `render()` is the message that gets sent.

    Since 2026-09-22 it carries the recommender's own objects as well as the
    lines built from them (`today_report`, `week_report`, `season_report`,
    `place`, `feed`). The text message reads the lines; the HTML email
    (`app/mail/`) reads the objects, because "the starters by slot with their
    game" is a small grid and a grid cannot be recovered from a sentence.
    Both are built once, here, so the two cannot disagree.

    `topics` is what the reader asked for (`app.subscriptions`). A section he
    did not ask for is not built and not rendered, which is why the whole
    message is skipped when he asked for nothing.
    """

    season: int
    team_name: str
    generated_at: datetime
    roster: list[Line] = field(default_factory=list)
    roster_extra: int = 0
    standing: list[str] = field(default_factory=list)
    healthy: int = 0
    wire: list[Line] = field(default_factory=list)
    wire_extra: int = 0
    adds_recently: int = 0
    #: The day's lineup, already rendered and indented (`today_lines`). One
    #: line when there is none to set; never empty.
    today: list[str] = field(default_factory=list)
    #: The week section, already rendered and indented (`week_plan`). One
    #: line when there is no plan to make; never empty.
    plan: list[str] = field(default_factory=list)
    #: The rest-of-season section, the same way (`season_outlook`).
    season_plan: list[str] = field(default_factory=list)
    #: Where he stands, already rendered (`standing_lines`).
    table: list[str] = field(default_factory=list)
    #: Every event reported on, to mark notified once this has been sent.
    event_ids: list[int] = field(default_factory=list)

    #: The objects behind the lines, for the HTML. None when the section
    #: could not be built, which is exactly when its lines say why.
    today_report: TodayReport | None = None
    week_report: StreamReport | None = None
    season_report: SeasonReport | None = None
    place: Standing | None = None
    #: The league's own news over the display window, unfiltered: the email
    #: filters it by the reader's topics and groups it by day.
    feed: list[Change] = field(default_factory=list)
    league_name: str = ""
    opponent_name: str | None = None
    #: The topics this was built for, in the email's order.
    topics: tuple[str, ...] = ()

    @property
    def roster_size(self) -> int:
        return self.healthy + len(self.standing)

    def wants(self, topic: str) -> bool:
        """Whether the reader asked for this section. Nothing asked for at
        all means everything: a `Digest` built by hand (a test, a preview)
        should render whole."""
        return not self.topics or topic in self.topics

    def render(self) -> str:
        """The plain-text message, which is also the email's text part.

        The order is the one this message has always had, and not the HTML
        email's: the text is the message as it was, with the two new sections
        after it, and the tests that hold the rebuilt digest against the old
        one line for line still hold (docs/jobs.md, "The digest job").
        """
        out = [
            f"{self.team_name} - {self.generated_at:%a %d %b}, "
            f"{self.generated_at:%H:%M} UTC - season {self.season}"
        ]

        if self.wants(MY_TEAM):
            out.append("")
            out.append("YOUR ROSTER")
            out.extend(_render(self.roster, self.roster_extra, "nothing new"))

            out.append("")
            if self.standing:
                out.append("  Standing now:")
                out.extend(f"  {line}" for line in self.standing[:STATUS_LINE_LIMIT])
                hidden = len(self.standing) - STATUS_LINE_LIMIT
                if hidden > 0:
                    out.append(f"  and {hidden} more carrying a status")
                out.append(f"  {self.healthy} of {self.roster_size} active")
            else:
                out.append(f"  All {self.healthy} active")

        if self.wants(MOVES):
            out.append("")
            out.append("ON THE WIRE")
            out.extend(_render(self.wire, self.wire_extra, "nothing new"))

        if self.wants(LINEUP):
            out.append("")
            out.append("TODAY")
            out.extend(self.today or ["  no lineup today"])

        if self.wants(MOVES):
            out.append("")
            out.append("THIS WEEK")
            out.extend(self.plan or ["  no plan today"])

            out.append("")
            out.append("CHURN")
            churn = f"  {_plural(self.adds_recently, 'add')} in the last {CHURN_DAYS} days."
            if self.adds_recently:
                # Only worth saying when there is volume to weigh it against.
                churn += " Heavier movers here have returned less per move."
            out.append(churn)

            out.append("")
            out.append("THE SEASON")
            out.extend(self.season_plan or ["  no rest-of-season view today"])

        if self.wants(STANDINGS):
            out.append("")
            out.append("STANDINGS")
            out.extend(self.table or ["  no standings yet"])
        return "\n".join(out[:MAX_LINES])


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _render(lines: Sequence[Line], extra: int, empty: str) -> list[str]:
    if not lines:
        return [f"  {empty}"]
    out = [
        f"  {_KIND_LABELS.get(line.kind, line.kind):<14} {line.player:<22} {line.detail}".rstrip()
        for line in lines
    ]
    if extra > 0:
        out.append(f"  and {extra} more, see /events")
    return out


def _team_names(session: Session, league_season: LeagueSeason) -> dict[int, str]:
    return {
        team.espn_team_id: team.name
        for team in session.scalars(
            select(Team).where(Team.league_season_id == league_season.id)
        ).all()
    }


def league_season_for(session: Session, espn_league_id: int, season: int) -> LeagueSeason | None:
    return session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == espn_league_id, LeagueSeason.season == season)
    )


def latest_listened_season(session: Session) -> int | None:
    """The newest season the listener has snapshotted.

    The digest reads the database and never ESPN, so the season comes from
    what the passes wrote rather than from a fetch. In September that is
    already next season, which is the one the passes follow.
    """
    return session.scalar(select(func.max(PlayerStatusSnapshot.season)))


def unnotified(
    session: Session,
    season: int,
    kinds_wanted: Sequence[str],
    *,
    since: datetime | None = None,
) -> list[PlayerStatusEvent]:
    """The events still to tell: never notified, or, with `since`, observed
    after it (a member's own window, whatever the owner has been told)."""
    fresh = (
        PlayerStatusEvent.notified_at.is_(None)
        if since is None
        else PlayerStatusEvent.observed_at > since
    )
    return list(
        session.scalars(
            select(PlayerStatusEvent)
            .where(
                PlayerStatusEvent.season == season,
                fresh,
                PlayerStatusEvent.kind.in_(kinds_wanted),
            )
            .order_by(PlayerStatusEvent.observed_at.desc(), PlayerStatusEvent.id.desc())
        ).all()
    )


def adds_in_window(
    session: Session, league_season: LeagueSeason, team_id: int, *, now: datetime, days: int
) -> int:
    """Executed wire adds by this team in the trailing window, the volume guard.

    The window has two ends. Without the far one it is not a trailing window
    at all but everything since `now - days`, which on a live morning reads
    right because nothing has happened after now, and on any replayed day
    reads the rest of the season: a digest built for a day in January counted
    83 adds in the last fortnight where 15 had been made.
    """
    since = now - timedelta(days=days)
    return (
        session.scalar(
            select(func.count())
            .select_from(Transaction)
            .join(TransactionItem, TransactionItem.transaction_id == Transaction.id)
            .where(
                Transaction.league_season_id == league_season.id,
                Transaction.team_id == team_id,
                Transaction.type.in_(WIRE_TYPES),
                Transaction.status == "EXECUTED",
                Transaction.processed_at.is_not(None),
                Transaction.processed_at >= since,
                Transaction.processed_at <= now,
                TransactionItem.item_type == "ADD",
            )
        )
        or 0
    )


def _side(move: Move, today: int) -> str:
    """One move in the streaming CLI's own words, on one line.

    `scripts/stream.py` spreads this over four lines; a phone message cannot
    afford them, so the same facts -- who comes in, for how many of his games
    left, who goes out, and whether the man coming in is still on waivers --
    go on one, phrased as the CLI phrases them.
    """
    add: RosteredPlayer = move.add
    coming = f"{add.name} ({move.add_starts} of {add.games_remaining_this_period} games)"
    if not add.seatable_on(today) and add.waiver_clears_at is not None:
        coming += f", on waivers, clears {add.waiver_clears_at:%a}"
    if move.kind == ADD:
        return f"add {coming} into the open place"
    if move.kind == IR_MOVE and move.to_ir is not None:
        return f"add {coming}, {move.to_ir.name} to IR"
    if move.drop is not None:
        going = f"{move.drop.name} ({move.drop_starts} of {move.drop.games_remaining_this_period})"
        return f"add {coming}, drop {going}"
    return f"add {coming}"


def _record(record: tuple[float, float]) -> str:
    return f"{record[0]:.1f}-{record[1]:.1f}"


def _worth(judgement: Judgement) -> str:
    """Both horizons, the net, and the season record either way."""
    return (
        f"week {judgement.delta_week:+.2f} + season "
        f"{judgement.delta_season_per_week:+.2f}/wk = net {judgement.delta_total:+.2f}; "
        f"record {_record(judgement.record_without)} without, "
        f"{_record(judgement.record_with)} with"
    )


def _week_lines(report: StreamReport, opponent: str | None) -> list[str]:
    """The week section's body, from a report that was built."""
    first, last = report.scoring_periods_remaining[0], report.scoring_periods_remaining[-1]
    out = [f"  period {report.matchup_period}, days {first}-{last} left ({report.days_remaining})"]
    if report.on_bye:
        out.append("  on a bye this period, so there is no week to plan for")
        return out

    out[0] += f", v {opponent or report.opponent_team_id}"
    out.append(
        f"  {report.expected_wins:.2f} of 9 categories as things stand; "
        f"adds this period: used {report.adds_used} of {report.adds_budget}"
    )

    for day in report.empty_days[:EMPTY_DAY_LIMIT]:
        out.append(f"  day {day.scoring_period}: {', '.join(day.empty_slots)} going empty")
    hidden = len(report.empty_days) - EMPTY_DAY_LIMIT
    if hidden > 0:
        out.append(f"  and {hidden} more day(s) with a slot going empty")

    plan = report.recommended
    if report.adds_left == 0:
        out.append("  no adds left this period, so there is nothing to plan today")
    elif not plan:
        out.append(
            f"  nothing clears the bar ({report.hurdle:.2f} categories, or an empty day "
            f"filled); {report.pool_size} free agents were weighed"
        )
    elif len(plan) == 1:
        out.append(f"  worth a look: {_side(plan[0], report.today)}")
        out.append(f"    {_worth(plan[0].judgement)}")
    else:
        out.append("  worth a look, in this order:")
        for rank, move in enumerate(plan, start=1):
            out.append(f"  {rank}. {_side(move, report.today)}")
            out.append(f"    {_worth(move.judgement)}")
    return out


# ---------------------------------------------------------------------------
# today: the day's lineup, above the week's plan
# ---------------------------------------------------------------------------

#: Men named on the "sitting" line, and places named on the "fix" line,
#: before the rest are counted. Telegram's limit is 4096 characters and the
#: whole point of this block is that it is read on a phone at breakfast, so
#: it carries the starters and the thing to fix and leaves the grid to the
#: week page (`app.pickups.today`, docs/in_season_pages.md).
TODAY_SIT_LIMIT = 4
TODAY_FIX_LIMIT = 2


def _names(players: Sequence[DayPlayer], limit: int) -> str:
    named = ", ".join(player.name for player in players[:limit])
    extra = len(players) - limit
    return f"{named} and {extra} more" if extra > 0 else named


def today_block(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    on: date,
) -> tuple[TodayReport | None, list[str]]:
    """The day's lineup for `on`: the report, and the lines built from it.

    The morning question, above the week's plan, because it is the one thing
    the manager acts on before tip-off: who to start, who cannot be started,
    and the place that will produce nothing tonight while a man on the bench
    would have.

    Two returns rather than one because there are two readers: the text
    message prints the lines, and the HTML email draws the lineup as a grid
    from the report itself. Neither builds it twice, and a report that could
    not be built is None beside lines that say why.

    It catches everything, for the reason `week_plan` does: a digest that
    failed to go out because a lineup could not be built would lose the
    roster news with it.
    """
    season = int(league_season.season)
    try:
        calendar = season_calendar(session, season)
        if calendar is None:
            return None, [f"  no NBA schedule stored for {season}, so no lineup today"]
        report = today_lineup(session, league_season, espn_team_id, calendar.scoring_period_on(on))
    except ValueError as error:
        return None, [f"  no lineup today: {error}"]
    except Exception as error:  # The digest goes out regardless.
        return None, [f"  no lineup today: it could not be built ({type(error).__name__})"]
    return report, _today_lines(report)


def today_lines(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    on: date,
) -> list[str]:
    """`today_block`'s lines alone, for a caller with no use for the report."""
    return today_block(session, league_season, espn_team_id, on=on)[1]


def _today_lines(report: TodayReport) -> list[str]:
    """The block's body, from a lineup that was built."""
    when = f", {report.calendar_date:%a %d %b}" if report.calendar_date is not None else ""
    if report.teams_playing == 0:
        return [f"  day {report.today}{when}: no NBA games, so there is no lineup to set"]

    out = [
        f"  day {report.today}{when}: {report.starts} of {len(report.lineup)} places fillable"
        + (f", {report.actual_starts} set" if report.actual_known else "")
    ]
    out.append(f"  start: {_names(report.starters, len(report.lineup))}")
    for misstart in report.fix[:TODAY_FIX_LIMIT]:
        where = (
            f"{misstart.seat.slot} is empty"
            if misstart.seat.player is None
            else f"{misstart.seat.player.name} has no game at {misstart.seat.slot}"
        )
        out.append(f"  fix: {where}; {_names(misstart.instead, 2)} could take it")
    hidden = len(report.fix) - TODAY_FIX_LIMIT
    if hidden > 0:
        out.append(f"  and {hidden} more place(s) worth fixing")
    for benched in report.benched[:TODAY_SIT_LIMIT]:
        out.append(f"  {benched.player.name} has a game but no place in the lineup")
    if report.idle:
        out.append(f"  sitting, no game: {_names(report.idle, TODAY_SIT_LIMIT)}")
    return out


def week_block(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    on: date,
) -> tuple[StreamReport | None, str | None, list[str]]:
    """The week for `on`: the report, the opponent's name, and the lines.

    The recommender is the one part of the digest that can fail on rows the
    listener has not written yet, and the digest must go out anyway (the
    module docstring). So every failure becomes one line: a missing schedule
    and a `today` in no matchup period name themselves, and anything else is
    named by its type rather than its message, since an exception's text can
    carry a query and a connection string.
    """
    season = int(league_season.season)
    try:
        calendar = season_calendar(session, season)
        if calendar is None:
            return None, None, [f"  no NBA schedule stored for {season}, so no plan today"]
        report = stream_recommendations(
            session, league_season, espn_team_id, calendar.scoring_period_on(on)
        )
        opponent = _team_names(session, league_season).get(report.opponent_team_id or -1)
    except ValueError as error:
        return None, None, [f"  no plan today: {error}"]
    except Exception as error:  # The digest goes out regardless.
        return (
            None,
            None,
            [f"  no plan today: the week could not be built ({type(error).__name__})"],
        )
    return report, opponent, _week_lines(report, opponent)


def week_plan(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    on: date,
) -> list[str]:
    """`week_block`'s lines alone."""
    return week_block(session, league_season, espn_team_id, on=on)[2]


# ---------------------------------------------------------------------------
# the season: the same question over the rest of it
# ---------------------------------------------------------------------------

#: Men named on the stash line before the rest are counted.
SEASON_STASH_LIMIT = 2


def _season_lines(report: SeasonReport) -> list[str]:
    """The rest-of-season section's body, from a report that was built.

    The week's plan answers "what do I do today"; this answers "where is the
    season going, and what would move it". Three things and no more: where it
    ends as it stands, the best move over the rest of it with its number, and
    a man worth stashing until he is back.
    """
    record = report.outlook.record_without
    out = [
        f"  {report.weeks_remaining:.0f} weeks left; {report.expected_wins:.2f} of 9 "
        f"categories in an ordinary week",
        f"  on this roster the season ends {_record(record)}",
    ]
    move = report.recommended
    if move is None:
        best = report.moves[0] if report.moves else None
        if best is None:
            out.append(f"  nothing on the wire moves it; {report.pool_size} free agents weighed")
        else:
            hurdle = best.hurdle(report.hurdle_paid, report.hurdle_free)
            out.append(
                f"  nothing clears the bar ({hurdle:.2f} a week); the nearest is "
                f"{_swap(best)} at {best.judgement.per_week:+.2f}"
            )
    else:
        out.append(f"  worth a look: {_swap(move)}")
        out.append(
            f"  {move.judgement.per_week:+.2f} a week over the rest of it; "
            f"record {_record(report.outlook.record_without)} without, "
            f"{_record(move.judgement.record_with)} with"
        )
    if report.stashes:
        named = ", ".join(
            f"{stash.player.name} (back {stash.expected_return_date:%d %b})"
            for stash in report.stashes[:SEASON_STASH_LIMIT]
        )
        out.append(f"  worth a place when he is back: {named}")
    return out


def _swap(move: Swap) -> str:
    """One rest-of-season move on one line, in the week section's words."""
    coming = ", ".join(player.name for player in move.into)
    if not move.out:
        return f"add {coming} into the open place"
    going = ", ".join(player.name for player in move.out)
    return f"add {coming}, drop {going}"


def season_outlook(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    on: date,
) -> tuple[SeasonReport | None, list[str]]:
    """The rest of the season for `on`: the report and its lines. Never
    raises, for the reason `week_block` does not."""
    season = int(league_season.season)
    try:
        calendar = season_calendar(session, season)
        if calendar is None:
            return None, [f"  no NBA schedule stored for {season}, so no season view today"]
        report = season_recommendations(
            session, league_season, espn_team_id, calendar.scoring_period_on(on)
        )
    except ValueError as error:
        return None, [f"  no season view today: {error}"]
    except Exception as error:  # The digest goes out regardless.
        return None, [f"  no season view today: it could not be built ({type(error).__name__})"]
    return report, _season_lines(report)


# ---------------------------------------------------------------------------
# standings: where he stands, and where it is heading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Standing:
    """One team's place in the league, derived the way `/standings` derives it.

    ESPN reports no matchup record, only category tallies, so the record is
    counted from the matchups themselves and the place is the order that
    puts. Byes are not wins and playoff matchups are not counted, exactly as
    `app.api.leagues.get_standings` does it.

    `projected` is the projected finish, and is None until that work lands:
    the email leaves a marked slot rather than pretending.
    """

    place: int
    of: int
    won: int
    lost: int
    tied: int
    categories_won: int
    categories_lost: int
    projected: str | None = None

    def describe(self) -> str:
        tied = f"-{self.tied}" if self.tied else ""
        return f"{self.place} of {self.of}, {self.won}-{self.lost}{tied}"


def _place_of(session: Session, league_season: LeagueSeason, espn_team_id: int) -> Standing | None:
    """Where this team stands, or None when the season has no matchups yet."""
    teams = list(
        session.scalars(select(Team).where(Team.league_season_id == league_season.id)).all()
    )
    if not teams:
        return None
    record = {team.id: [0, 0, 0] for team in teams}
    rows = session.scalars(
        select(Matchup)
        .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
        .where(
            MatchupPeriod.league_season_id == league_season.id,
            MatchupPeriod.is_playoff.is_(False),
            Matchup.away_team_id.is_not(None),
        )
    ).all()
    played = 0
    for matchup in rows:
        home, away = matchup.home_team_id, matchup.away_team_id
        if away is None or home not in record or away not in record:
            continue
        if matchup.winner == "HOME":
            record[home][0] += 1
            record[away][1] += 1
        elif matchup.winner == "AWAY":
            record[away][0] += 1
            record[home][1] += 1
        elif matchup.winner == "TIE":
            record[home][2] += 1
            record[away][2] += 1
        else:
            continue
        played += 1
    if not played:
        return None
    order = sorted(
        teams,
        key=lambda t: (-record[t.id][0], record[t.id][1], -(t.categories_won or 0)),
    )
    for place, team in enumerate(order, start=1):
        if int(team.espn_team_id) == espn_team_id:
            won, lost, tied = record[team.id]
            return Standing(
                place=place,
                of=len(order),
                won=won,
                lost=lost,
                tied=tied,
                categories_won=int(team.categories_won or 0),
                categories_lost=int(team.categories_lost or 0),
            )
    return None


def standing_lines(
    session: Session, league_season: LeagueSeason, espn_team_id: int
) -> tuple[Standing | None, list[str]]:
    """Where he stands, and the lines for it. Never raises."""
    try:
        place = _place_of(session, league_season, espn_team_id)
    except Exception as error:  # The digest goes out regardless.
        return None, [f"  no standings today: they could not be read ({type(error).__name__})"]
    if place is None:
        return None, ["  no matchup has been settled yet, so there is no table"]
    return place, [
        f"  {place.describe()} on matchups",
        f"  {place.categories_won}-{place.categories_lost} on categories",
        f"  projected finish: {place.projected or 'not built yet'}",
    ]


def _standing(roster: Sequence[PlayerStatusSnapshot]) -> tuple[list[str], int]:
    """Who on the roster carries a status worth knowing, and how many do not."""
    lines: list[str] = []
    healthy = 0
    for snapshot in sorted(roster, key=lambda s: (_concern_rank(s), s.player.name)):
        status = (snapshot.injury_status or "").upper()
        if status not in _CONCERNING:
            healthy += 1
            continue
        back = snapshot.expected_return_date
        suffix = f", back {back:%d %b}" if back else ""
        lines.append(f"{status:<12} {snapshot.player.name}{suffix}")
    return lines, healthy


def _concern_rank(snapshot: PlayerStatusSnapshot) -> int:
    status = (snapshot.injury_status or "").upper()
    return _CONCERNING.index(status) if status in _CONCERNING else len(_CONCERNING)


def build_digest(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    wanted: Subscription | None = None,
) -> Digest:
    """The morning message for one team. Reads only; marking is the caller's.

    `since` reads the events observed after it rather than the unnotified
    ones: a member's digest (docs/jobs.md), which marks nothing.

    An event lands in the roster section when the player's latest snapshot
    puts him on the tracked team, whatever his status was when it fired, and
    on the wire when he is unrostered now. An event on a rival's roster is
    neither, and stays unreported.

    `wanted` is what the reader asked for (`app.subscriptions`); the default
    is everything. A section he did not ask for is **not built**, which is
    what keeps the cost of a subscription honest: a reader who wants only
    today's lineup does not pay for the rest-of-season search.

    The week section is built for the calendar day of `now`, and says so in
    one line when it cannot be (`week_block`). This is the morning message;
    `build_alert`, which is what the later passes send, has no plan in it,
    because an add is not what a player being ruled out at 22:30 calls for.
    """
    generated_at = now or datetime.now(UTC)
    wanted = wanted or subscriptions_everything()
    on = generated_at.date()
    snapshots = latest_snapshots(session, league_season.season)
    team = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id, Team.espn_team_id == espn_team_id
        )
    )

    roster_events: list[Line] = []
    wire_events: list[Line] = []
    reported: list[int] = []
    for change in what_changed(
        session,
        league_season,
        since=since if since is not None else generated_at - OWNER_BACKLOG,
        until=generated_at,
        team_id=espn_team_id,
        kinds_wanted=EVENT_KINDS,
        unnotified=since is None,
    ):
        line = _line(change)
        if line is None:
            continue
        if change.mine and line.kind in ROSTER_KINDS:
            roster_events.append(line)
        elif change.on_wire and line.kind in WIRE_KINDS:
            wire_events.append(line)
        else:
            continue
        if change.event_id is not None:
            reported.append(change.event_id)

    mine = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.on_team_id == espn_team_id
    }
    standing, healthy = _standing([snapshots[player_id] for player_id in mine])

    today_report, today = (
        today_block(session, league_season, espn_team_id, on=on)
        if wanted.on(LINEUP)
        else (None, [])
    )
    week_report, opponent, plan = (
        week_block(session, league_season, espn_team_id, on=on)
        if wanted.on(MOVES)
        else (None, None, [])
    )
    season_report, season_plan = (
        season_outlook(session, league_season, espn_team_id, on=on)
        if wanted.on(MOVES)
        else (None, [])
    )
    place, table = (
        standing_lines(session, league_season, espn_team_id) if wanted.on(STANDINGS) else (None, [])
    )
    return Digest(
        season=league_season.season,
        team_name=team.name if team is not None else f"team {espn_team_id}",
        generated_at=generated_at,
        roster=roster_events[:ROSTER_EVENT_LIMIT],
        roster_extra=max(0, len(roster_events) - ROSTER_EVENT_LIMIT),
        standing=standing,
        healthy=healthy,
        wire=wire_events[:WIRE_EVENT_LIMIT],
        wire_extra=max(0, len(wire_events) - WIRE_EVENT_LIMIT),
        today=today,
        plan=plan,
        season_plan=season_plan,
        table=table,
        adds_recently=adds_in_window(
            session,
            league_season,
            team.id if team is not None else 0,
            now=generated_at,
            days=CHURN_DAYS,
        ),
        event_ids=reported,
        today_report=today_report,
        week_report=week_report,
        season_report=season_report,
        place=place,
        feed=display_feed(session, league_season, espn_team_id, now=generated_at, since=since),
        league_name=str(league_season.name or ""),
        opponent_name=opponent,
        topics=wanted.chosen,
    )


#: How far back the email's "What changed" looks, at most. The owner's own
#: window is a fortnight of unreported events (`OWNER_BACKLOG`), which is the
#: right window for "what have I not been told" and much too long for a
#: section grouped by day: three mornings of a busy league is already a
#: screenful. The section reads the shorter of the two.
CHANGED_WINDOW = timedelta(days=3)


def display_feed(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    now: datetime,
    since: datetime | None,
) -> list[Change]:
    """The league's news over the window the email shows, newest first.

    Unfiltered: every `Change` carries `mine`, `opponent` and its kind, and
    the reader's topics decide which of them he sees (`app.subscriptions`).
    It marks nothing and it is not what `event_ids` is built from -- that
    stays the roster and wire loop above, so the owner's digest marks exactly
    what it always marked.

    Never raises: the message goes out with an empty feed rather than not at
    all, which is the rule every other section here follows.
    """
    opened = max(since or (now - CHANGED_WINDOW), now - CHANGED_WINDOW)
    try:
        return what_changed(
            session,
            league_season,
            since=opened,
            until=now,
            team_id=espn_team_id,
            kinds_wanted=feed.KINDS,
        )
    except Exception:  # The digest goes out regardless.
        return []


def build_alert(
    session: Session,
    league_season: LeagueSeason,
    espn_team_id: int,
    *,
    since: datetime | None = None,
) -> tuple[str, list[int]] | None:
    """A one-liner for an urgent change to the tracked roster, or None.

    What runs after the passes that are not the morning one: a player of
    yours ruled out at 22:30 is worth knowing before the lineup locks, and
    everything else can wait for tomorrow's digest.
    """
    snapshots = latest_snapshots(session, league_season.season)
    mine = {
        player_id
        for player_id, snapshot in snapshots.items()
        if snapshot.on_team_id == espn_team_id
    }
    team_names = _team_names(session, league_season)
    found = [
        event
        for event in unnotified(session, league_season.season, URGENT_KINDS, since=since)
        if event.player_id in mine
    ]
    if not found:
        return None
    lines = [f"{event.player.name}: {describe(event, team_names)}" for event in found]
    return "\n".join(lines), [event.id for event in found]


def mark_notified(session: Session, event_ids: Sequence[int], at: datetime) -> int:
    """Record that these events went out. Called only after delivery succeeded."""
    if not event_ids:
        return 0
    found = session.scalars(
        select(PlayerStatusEvent).where(PlayerStatusEvent.id.in_(list(event_ids)))
    ).all()
    for event in found:
        event.notified_at = at
    return len(found)


# ---------------------------------------------------------------------------
# the league section: free, for every member (step 4)
# ---------------------------------------------------------------------------

#: Matchups listed before the rest are counted; a sixteen-team league has eight.
LEAGUE_MATCHUP_LIMIT = 8
#: The window the wire's traffic is counted over.
LEAGUE_MOVES_HOURS = 24
#: Moves named before the rest are counted. Eight sentences is a paragraph a
#: manager will read on a phone; a busy Thursday can be three times that.
LEAGUE_EVENT_LIMIT = 8
#: And a budget in characters, because the sentences are not all one size: a
#: four-team trade names eight players and runs to 196 characters, and eight
#: of those would put the whole message past Telegram's 4096 on its own
#: (measured on 2026 day 107, the season's busiest: 4392 characters). The
#: budget keeps the league's news around 700 and the message near 3,400.
LEAGUE_NEWS_CHARS = 700
#: What the league's own news is. A `lineup` change -- a movement the ledger
#: never named -- is the page's business: "no transaction says how" is a note
#: for someone looking into it, not a line in a morning message.
LEAGUE_NEWS_KINDS = (feed.ADD, feed.CLAIM, feed.DROP, feed.TRADE)
#: The moves on the wire, which is what the count has always been: a claim,
#: a free pickup or a drop, one per transaction. A trade is not one of them.
WIRE_MOVE_KINDS = (feed.ADD, feed.CLAIM, feed.DROP)


def league_section(session: Session, league_season: LeagueSeason, *, now: datetime) -> list[str]:
    """THE LEAGUE: this period's matchups as they stand, and what the league
    did in the last day -- who got whom, for how much, and who was traded for
    whom. From the league's own rows, so it is right for any league the
    ingest reads, and in the feed's own sentences, so the message and the
    page cannot disagree. Never raises: a missing schedule or a day in no
    period is one line."""
    out = ["THE LEAGUE"]
    season = int(league_season.season)
    try:
        calendar = season_calendar(session, season)
        period = (
            period_for_day(session, league_season, calendar.scoring_period_on(now.date()))
            if calendar is not None
            else None
        )
    except Exception as error:  # The digest goes out regardless.
        return [*out, f"  the week could not be read ({type(error).__name__})"]
    if period is None:
        out.append("  no matchup period in play today")
    else:
        out.append(
            f"  period {period.period}, days {period.first_scoring_period}"
            f"-{period.final_scoring_period}"
        )
        names = {team.id: team.name for team in league_season.teams}
        rows = sorted(period.matchups, key=lambda m: m.id)
        for matchup in rows[:LEAGUE_MATCHUP_LIMIT]:
            home = names.get(matchup.home_team_id, "?")
            if matchup.away_team_id is None:
                out.append(f"  {home} on a bye")
                continue
            away = names.get(matchup.away_team_id, "?")
            tied = f", {matchup.categories_tied} level" if matchup.categories_tied else ""
            out.append(
                f"  {home} {matchup.home_categories_won}-{matchup.home_categories_lost}"
                f" {away}{tied}"
            )
        if len(rows) > LEAGUE_MATCHUP_LIMIT:
            out.append(f"  and {len(rows) - LEAGUE_MATCHUP_LIMIT} more")
    # Both ends of the day, as `adds_in_window` has both ends of its
    # fortnight: open at the far end this counted every move the league went
    # on to make, and a replayed morning said 609 moves on the wire in the
    # last day where nine had been made.
    news = what_changed(
        session,
        league_season,
        since=now - timedelta(hours=LEAGUE_MOVES_HOURS),
        until=now,
        kinds_wanted=LEAGUE_NEWS_KINDS,
    )
    moves = [change for change in news if change.kind in WIRE_MOVE_KINDS]
    out.append(f"  {_plural(len(moves), 'move')} on the wire in the last day")
    named = 0
    spent = 0
    for change in news[:LEAGUE_EVENT_LIMIT]:
        if named and spent + len(change.text) > LEAGUE_NEWS_CHARS:
            break
        out.append(f"  {change.text}")
        spent += len(change.text)
        named += 1
    if len(news) > named:
        out.append(f"  and {len(news) - named} more")
    return out


def team_section_without_listener(
    session: Session, league_season: LeagueSeason, espn_team_id: int, *, now: datetime
) -> list[str]:
    """A team's part of the digest in a league the listener does not follow.

    The roster and wire news come from the listener's status snapshots, which
    record one league's view of who holds whom (docs/jobs.md, "One listener
    league"), so a team in any other league gets the parts that read its own
    league's rows: this week's plan and its churn.
    """
    team = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id, Team.espn_team_id == espn_team_id
        )
    )
    name = team.name if team is not None else f"team {espn_team_id}"
    adds = adds_in_window(
        session, league_season, team.id if team is not None else 0, now=now, days=CHURN_DAYS
    )
    return [
        f"{name} - {now:%a %d %b}, {now:%H:%M} UTC - season {league_season.season}",
        "",
        "TODAY",
        *today_lines(session, league_season, espn_team_id, on=now.date()),
        "",
        "THIS WEEK",
        *week_plan(session, league_season, espn_team_id, on=now.date()),
        "",
        "CHURN",
        f"  {_plural(adds, 'add')} in the last {CHURN_DAYS} days.",
    ]
