"""A proposed trade, judged forward, for one team on one day.

Read-only and keyed on ESPN ids, like the two pickup routes beside it
(`app/api/pickups.py`): a caller who knows the league, the year, the two
teams and the players can ask the question without knowing anything about
this database's own keys, so player ids go out -- and come in -- as ESPN's.

WHAT IT IS, AND WHAT IT IS NOT

`app/trades/` judges a named deal from both sides in the recommender's own
currency, and `app/trades/calibration.py` carries what that headline number
has been measured at: on the 55 replayable trades in this league's history it
pointed at the side that did better 25 times, which is a coin. So the route
sends `calibration_note` with every answer, the page prints it verbatim under
the number, and the fit -- the nine categories before and after -- leads,
because that half is arithmetic on the roster rather than a forecast
(docs/trades.md sections 0 and 7).

Nothing here is advice. The bar labels a deal and never hides one, the other
side's numbers are our estimate of his roster's needs and never his opinion,
and no field says accept or reject.

THREE ROUTES

    .../teams/{team_id}/trades/rosters   who is on each roster on `today`
    .../teams/{team_id}/trades/pool      the wire, for the place a deal opens
    .../teams/{team_id}/trades/report    the deal, judged from both sides

The pickers read the first so the page never hard-codes or guesses a roster,
and the roster is the one stored on or before `today`: a replayed day sees
that day's men and not a later one's. The pool is the same day's wire, ranked
by what each man would be worth to the side whose deal opens the place, and
a man chosen from it comes back to the report as `fill` or `their_fill`.

A SEASON WITH NOTHING TO JUDGE FROM

A season before its draft has no rosters and no schedule. That is not bad
input and not a refusal: both routes answer 200 with `readiness`, which says
plainly what the season is missing, exactly the two things
`app.api.pickups.readiness` looks for.

BAD INPUT

A deal that cannot be read is a 422 with a sentence a manager can act on: a
player who is not on that roster, a man on both sides of the deal, a man on
injured reserve, a drop the team does not hold, a roster with no room for the
men arriving and nobody left to drop -- which names whoever it could still
drop -- and a fill who is not on the wire, is named for a side that opens no
place, is named twice over, or is named on both sides at once.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import pickups
from app.api.access import TEAM_PLAN
from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.schemas import (
    JudgementOut,
    TradeCategoryOut,
    TradeFillCandidateOut,
    TradeFillPoolOut,
    TradeOut,
    TradePlayerOut,
    TradePlayoffsOut,
    TradeReadinessOut,
    TradeReportOut,
    TradeRosterOut,
    TradeRosterPlayerOut,
    TradeRostersOut,
    TradeSideOut,
)
from app.db.models import LeagueSeason, Player, Team
from app.inseason.card import pro_team_name
from app.pickups.judge import Judgement
from app.pickups.state import (
    RosteredPlayer,
    SeasonCalendar,
    TeamWeek,
    load_free_agents,
    load_team_week,
)
from app.scoring.lines import CategoryLine
from app.trades import (
    CALIBRATION_NOTE,
    POOL_LIMIT,
    CategoryView,
    FillCandidate,
    FillPool,
    PlayerCard,
    PlayoffLens,
    SideReport,
    TeamOffer,
    TradeReport,
    evaluate_trade,
    fill_pool,
)

router = APIRouter(tags=["trades"])

TodayQuery = Annotated[
    int | None,
    Query(ge=1, description="Scoring period to judge on; defaults to today's"),
]
OtherTeamQuery = Annotated[
    int | None,
    Query(description="ESPN team id of the other side"),
]
PlayerIdsQuery = Annotated[
    list[int] | None,
    Query(description="ESPN player ids; repeat the parameter for several"),
]
SideQuery = Annotated[
    str,
    Query(pattern="^(ours|theirs)$", description="Whose opened place the pool is for"),
]
LimitQuery = Annotated[
    int,
    Query(ge=1, le=100, description="Most free agents to answer with, best first"),
]

#: What a caller is told when the season has nothing to judge a deal from.
#: Not a refusal: a season before its draft has no rosters to trade from and
#: no schedule to plan over, and saying so is the answer.
NOT_READY = "season {season} has nothing to judge a trade from yet: {missing}"

#: Every sentence a 422 can say. Written here rather than at each raise, so
#: the words a manager reads are in one place and the tests can hold them.
SELF_TRADE = "A team cannot trade with itself: pick a different team to trade with."
NO_OTHER_TEAM = "Name the team on the other side of this deal."
NO_SUCH_TEAM = "There is no team {team_id} in this league's {season} season."
NOTHING_TRADED = "Nothing is being traded: name at least one player given or got."
BOTH_SIDES = "{name} is on both sides of this deal: name him once, as given or as got."
NOT_ON_ROSTER = "{team} does not have {names} on its roster on day {day}."
ON_IR = (
    "{name} is on {team}'s injured reserve on day {day}: he holds no active place, so he "
    "cannot be traded or dropped to make room in this report."
)
CANNOT_DROP = "{team} cannot drop {names}: not on its active roster on day {day}."
DROPPED_AND_TRADED = "{team} cannot both trade away and drop {names}."
NO_ROOM_AT_ALL = (
    "{team} has no room for the {arriving} player(s) arriving and nobody left to drop: "
    "every other man on its roster is already in this deal. Give it one fewer player, "
    "or take one back."
)
NO_ROOM_ENOUGH = (
    "{team} needs {needed} more roster place(s) for this deal and can free {spare}: it "
    "could still drop {names}. Give it one fewer player, or take one back."
)
NOT_A_FREE_AGENT = (
    "{names} is not a free agent on day {day}: only a man on the wire that morning can "
    "fill the place this deal opens."
)
NOTHING_TO_FILL = (
    "{team} opens no roster place in this deal, so there is nowhere for {names} to go. "
    "Take a player back from it, or leave the wire alone."
)
FILLS_BOTH_SIDES = (
    "{name} cannot fill a place on both sides of this deal: there is one wire, and one "
    "of him. Name him for one side or the other."
)
TOO_MANY_FILLS = (
    "{team} opens {opened} roster place(s) and {named} men are named off the wire for it: "
    "name one man for each place, or fewer."
)


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/rosters",
    summary="Both rosters as they stood on the day, for the trade builder's pickers",
    dependencies=[TEAM_PLAN],
)
def trade_rosters(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    with_team: OtherTeamQuery = None,
    today: TodayQuery = None,
) -> TradeRostersOut:
    """Who each side holds on `today`, so the page never guesses a roster.

    The roster is the latest lineup day at or before `today`, which is what
    every other reading of a replayed day does: a deal built on day 52 sees
    day 52's men and nothing later.
    """
    calendar, missing = pickups.readiness(session, league_season)
    ready = _readiness(league_season, missing)
    day = _day(calendar, today)
    teams: list[TradeRosterOut] = []
    if not missing:
        other = _other_team(session, league_season, team, with_team) if with_team else None
        wanted = [team] if other is None else [team, other]
        teams = [
            _roster_out(session, league_season, each, day, ours=each.id == team.id)
            for each in wanted
        ]
    return TradeRostersOut(
        season=int(league_season.season),
        today=day,
        today_date=calendar.date_of(day) if calendar is not None else None,
        readiness=ready,
        teams=teams,
        calibration_note=CALIBRATION_NOTE,
    )


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/pool",
    summary="The wire, ranked by what each man is worth in the place this deal opens",
    dependencies=[TEAM_PLAN],
)
def trade_pool(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    with_team: OtherTeamQuery = None,
    give: PlayerIdsQuery = None,
    get: PlayerIdsQuery = None,
    drop: PlayerIdsQuery = None,
    their_drop: PlayerIdsQuery = None,
    side: SideQuery = "ours",
    today: TodayQuery = None,
    limit: LimitQuery = POOL_LIMIT,
) -> TradeFillPoolOut:
    """The free agents `side` could put into the place this deal opens for it.

    The deal is in the query because the answer depends on it: what a man is
    worth to a roster is a question about the roster the deal leaves, not the
    one it starts with. Ranked by that -- the change in the side's expected
    category wins in a week with him in the place, against the place left open
    -- and never by the league standard, which is on every row beside it.

    The wire is the day's, read exactly as the report reads it: the listener's
    latest pass, or, on a season it never ran for, the men who played that day
    and were in nobody's lineup, with `historical_wire` saying which.
    """
    calendar, missing = pickups.readiness(session, league_season)
    day = _day(calendar, today)
    if missing:
        return _empty_pool(league_season, team, day, missing)
    other = _other_team(session, league_season, team, with_team)
    if other is None:
        raise _bad(NO_OTHER_TEAM)

    _no_man_twice(session, give, get)
    ours = load_team_week(session, league_season, int(team.espn_team_id), day)
    theirs = load_team_week(session, league_season, int(other.espn_team_id), day)
    given = _named(session, ours, team, give, day)
    got = _named(session, theirs, other, get, day)
    our_drops = _named(session, ours, team, drop, day, dropping=True)
    their_drops = _named(session, theirs, other, their_drop, day, dropping=True)
    _room(ours, team, giving=given, getting=got, dropping=our_drops)
    _room(theirs, other, giving=got, getting=given, dropping=their_drops)

    drops = {int(team.espn_team_id): our_drops, int(other.espn_team_id): their_drops}
    for_team = int(team.espn_team_id) if side == "ours" else int(other.espn_team_id)
    try:
        pool = fill_pool(
            session,
            league_season,
            day,
            TeamOffer(team_id=int(team.espn_team_id), gives=given),
            TeamOffer(team_id=int(other.espn_team_id), gives=got),
            for_team=for_team,
            drops={who: named for who, named in drops.items() if named},
            limit=limit,
        )
    except ValueError as error:
        raise _bad(str(error)) from error
    return _pool_out(pool, session, league_season, ours=int(team.espn_team_id))


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/trades/report",
    summary="What a proposed trade would be worth, to both sides, before it is made",
    dependencies=[TEAM_PLAN],
)
def trade_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    with_team: OtherTeamQuery = None,
    give: PlayerIdsQuery = None,
    get: PlayerIdsQuery = None,
    drop: PlayerIdsQuery = None,
    their_drop: PlayerIdsQuery = None,
    fill: PlayerIdsQuery = None,
    their_fill: PlayerIdsQuery = None,
    today: TodayQuery = None,
) -> TradeReportOut:
    """The deal named in the query, judged from both rosters as of `today`.

    `give` is the men leaving this team and `get` the men leaving `with_team`;
    `drop` and `their_drop` name whoever goes to make room, and a side that
    needs room and names nobody drops the cheapest place on its roster, which
    the answer says. `fill` and `their_fill` name the free agents a side puts
    into the places the deal opens for it -- one man per place, chosen from
    `trades/pool` -- and the place is then worth his own week rather than the
    better of the wire's best man and a streamed lane. The payload is
    `app.trades.evaluate`'s own report (docs/trades.md section 6), with
    `calibration_note` beside it.
    """
    calendar, missing = pickups.readiness(session, league_season)
    if missing:
        return TradeReportOut(
            readiness=_readiness(league_season, missing),
            trade=None,
            calibration_note=CALIBRATION_NOTE,
        )
    day = _day(calendar, today)
    other = _other_team(session, league_season, team, with_team)
    if other is None:
        raise _bad(NO_OTHER_TEAM)

    _no_man_twice(session, give, get)
    ours = load_team_week(session, league_season, int(team.espn_team_id), day)
    theirs = load_team_week(session, league_season, int(other.espn_team_id), day)
    given = _named(session, ours, team, give, day)
    got = _named(session, theirs, other, get, day)
    if not given and not got:
        raise _bad(NOTHING_TRADED)
    our_drops = _named(session, ours, team, drop, day, dropping=True)
    their_drops = _named(session, theirs, other, their_drop, day, dropping=True)
    _room(ours, team, giving=given, getting=got, dropping=our_drops)
    _room(theirs, other, giving=got, getting=given, dropping=their_drops)
    our_fills = _fills(session, league_season, ours, team, fill, day)
    their_fills = _fills(session, league_season, ours, other, their_fill, day)
    _one_wire(our_fills, their_fills)
    _places_to_fill(team, our_fills, opened=len(given) + len(our_drops) - len(got))
    _places_to_fill(other, their_fills, opened=len(got) + len(their_drops) - len(given))

    drops = {
        int(team.espn_team_id): our_drops,
        int(other.espn_team_id): their_drops,
    }
    fills = {
        int(team.espn_team_id): tuple(player_id for player_id, _name in our_fills),
        int(other.espn_team_id): tuple(player_id for player_id, _name in their_fills),
    }
    try:
        report = evaluate_trade(
            session,
            league_season,
            day,
            TeamOffer(team_id=int(team.espn_team_id), gives=given),
            TeamOffer(team_id=int(other.espn_team_id), gives=got),
            drops={who: named for who, named in drops.items() if named},
            fills={who: named for who, named in fills.items() if named},
        )
    except ValueError as error:
        raise _bad(str(error)) from error
    return TradeReportOut(
        readiness=TradeReadinessOut(ready=True, missing=[], note=None),
        trade=_trade_out(report, session, ours=int(team.espn_team_id)),
        calibration_note=CALIBRATION_NOTE,
    )


# ---------------------------------------------------------------------------
# reading the request
# ---------------------------------------------------------------------------


def _bad(detail: str) -> HTTPException:
    """A deal that cannot be read: 422, and a sentence a manager can act on."""
    return HTTPException(status_code=422, detail=detail)


def _readiness(league_season: LeagueSeason, missing: list[str]) -> TradeReadinessOut:
    """What the season is missing, in its own words and in one sentence."""
    return TradeReadinessOut(
        ready=not missing,
        missing=missing,
        note=None
        if not missing
        else NOT_READY.format(season=int(league_season.season), missing=" and ".join(missing)),
    )


def _day(calendar: SeasonCalendar | None, today: int | None) -> int:
    """The scoring period asked for, else the calendar's own, as the pickup
    routes read it -- `pickups.TODAY` by the module, so a rehearsal that moves
    the day moves this one too."""
    if today is not None:
        return today
    if calendar is None:
        return 1
    return calendar.scoring_period_on(pickups.TODAY())


def _other_team(
    session: Session, league_season: LeagueSeason, team: Team, with_team: int | None
) -> Team | None:
    """The team on the other side of the deal, or None when none was named."""
    if with_team is None:
        return None
    if int(with_team) == int(team.espn_team_id):
        raise _bad(SELF_TRADE)
    found = session.scalar(
        select(Team).where(
            Team.league_season_id == league_season.id, Team.espn_team_id == int(with_team)
        )
    )
    if found is None:
        raise _bad(NO_SUCH_TEAM.format(team_id=int(with_team), season=int(league_season.season)))
    return found


def _named(
    session: Session,
    week: TeamWeek,
    team: Team,
    espn_ids: Sequence[int] | None,
    day: int,
    *,
    dropping: bool = False,
) -> tuple[int, ...]:
    """The players named, as this database's ids, checked against the roster.

    A name the roster does not hold, or a man on injured reserve, is a
    sentence rather than a silently strange number -- the same check
    `app.trades.evaluate` makes, done here so the answer can use his name.
    """
    wanted = list(dict.fromkeys(int(espn_id) for espn_id in (espn_ids or ())))
    if not wanted:
        return ()
    by_espn = {
        int(espn_player_id): (int(player_id), str(name))
        for player_id, espn_player_id, name in session.execute(
            select(Player.id, Player.espn_player_id, Player.name).where(
                Player.espn_player_id.in_(wanted)
            )
        ).all()
    }
    held = {player.player_id: player for player in week.roster}
    out: list[int] = []
    unknown: list[str] = []
    for espn_id in wanted:
        found = by_espn.get(espn_id)
        if found is None or found[0] not in held:
            unknown.append(found[1] if found is not None else f"player {espn_id}")
            continue
        player_id, name = found
        if held[player_id].on_ir:
            raise _bad(ON_IR.format(name=name, team=team.name, day=day))
        out.append(player_id)
    if unknown:
        sentence = CANNOT_DROP if dropping else NOT_ON_ROSTER
        raise _bad(sentence.format(team=team.name, names=_join(unknown), day=day))
    return tuple(out)


def _fills(
    session: Session,
    league_season: LeagueSeason,
    week: TeamWeek,
    team: Team,
    espn_ids: Sequence[int] | None,
    day: int,
) -> tuple[tuple[int, str], ...]:
    """The free agents named for a side's opened places, with their names.

    Checked against the wire as the report itself will read it -- the same
    `load_free_agents`, the same day, the same historical fallback -- so a man
    the page offered cannot be a man the report refuses. `week` is either
    side's: the wire is the league's, not a team's.
    """
    wanted = list(dict.fromkeys(int(espn_id) for espn_id in (espn_ids or ())))
    if not wanted:
        return ()
    by_espn = {
        int(espn_player_id): (int(player_id), str(name))
        for player_id, espn_player_id, name in session.execute(
            select(Player.id, Player.espn_player_id, Player.name).where(
                Player.espn_player_id.in_(wanted)
            )
        ).all()
    }
    wire = {player.player_id for player in load_free_agents(session, league_season, week)}
    out: list[tuple[int, str]] = []
    unknown: list[str] = []
    for espn_id in wanted:
        found = by_espn.get(espn_id)
        if found is None or found[0] not in wire:
            unknown.append(found[1] if found is not None else f"player {espn_id}")
            continue
        out.append(found)
    if unknown:
        raise _bad(NOT_A_FREE_AGENT.format(names=_join(unknown), day=day))
    return tuple(out)


def _one_wire(ours: Sequence[tuple[int, str]], theirs: Sequence[tuple[int, str]]) -> None:
    """One man cannot be added by both sides of the same deal."""
    both = {player_id: name for player_id, name in ours}.keys() & {
        player_id for player_id, _name in theirs
    }
    if both:
        name = next(name for player_id, name in ours if player_id in both)
        raise _bad(FILLS_BOTH_SIDES.format(name=name))


def _places_to_fill(team: Team, fills: Sequence[tuple[int, str]], *, opened: int) -> None:
    """Whether this side has the places the men named off the wire need."""
    if not fills:
        return
    places = max(0, opened)
    if not places:
        raise _bad(
            NOTHING_TO_FILL.format(
                team=team.name, names=_join([name for _player_id, name in fills])
            )
        )
    if len(fills) > places:
        raise _bad(TOO_MANY_FILLS.format(team=team.name, opened=places, named=len(fills)))


def _no_man_twice(session: Session, give: Sequence[int] | None, get: Sequence[int] | None) -> None:
    """One man cannot be both given and got, and cannot be named twice."""
    given = [int(espn_id) for espn_id in (give or ())]
    got = [int(espn_id) for espn_id in (get or ())]
    repeated = [
        espn_id for espn_id in dict.fromkeys(given + got) if (given + got).count(espn_id) > 1
    ]
    if not repeated:
        return
    name = session.scalar(select(Player.name).where(Player.espn_player_id == repeated[0]))
    raise _bad(BOTH_SIDES.format(name=name or f"Player {repeated[0]}"))


def _room(
    week: TeamWeek,
    team: Team,
    *,
    giving: Sequence[int],
    getting: Sequence[int],
    dropping: Sequence[int],
) -> None:
    """Whether the deal fits this roster at all, once the drops are counted.

    A side that needs room and names nobody drops the cheapest place, which is
    the evaluator's own default and not an error. A side with nobody left to
    drop is: the report would have to invent a place. The sentence names
    whoever it could still drop, because that is what a manager does next.
    """
    clash = sorted(set(giving) & set(dropping))
    if clash:
        names = [player.name for player in week.roster if player.player_id in set(clash)]
        raise _bad(DROPPED_AND_TRADED.format(team=team.name, names=_join(names)))
    needed = len(getting) - len(giving) - week.open_slots - len(dropping)
    if needed <= 0:
        return
    spoken_for = set(giving) | set(dropping)
    spare = [player for player in week.active if player.player_id not in spoken_for]
    if not spare:
        raise _bad(NO_ROOM_AT_ALL.format(team=team.name, arriving=len(getting)))
    if len(spare) < needed:
        raise _bad(
            NO_ROOM_ENOUGH.format(
                team=team.name,
                needed=needed,
                spare=len(spare),
                names=_join(sorted(player.name for player in spare)),
            )
        )


def _join(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C" -- the summary's own joining."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------


def _roster_out(
    session: Session, league_season: LeagueSeason, team: Team, day: int, *, ours: bool
) -> TradeRosterOut:
    week = load_team_week(session, league_season, int(team.espn_team_id), day)
    espn = _espn_ids(session, week.roster)
    players = sorted(week.roster, key=lambda player: (player.on_ir, player.name))
    return TradeRosterOut(
        espn_team_id=int(team.espn_team_id),
        team_name=str(team.name),
        ours=ours,
        open_slots=week.open_slots,
        ir_slot_free=week.ir_slot_free,
        players=[_roster_player_out(player, espn) for player in players],
    )


def _roster_player_out(player: RosteredPlayer, espn: dict[int, int]) -> TradeRosterPlayerOut:
    return TradeRosterPlayerOut(
        espn_player_id=espn.get(player.player_id, player.player_id),
        name=player.name,
        position=player.position,
        pro_team_id=player.pro_team_id,
        injury_status=player.injury_status,
        expected_return_date=player.expected_return_date,
        on_ir=player.on_ir,
        games_remaining=player.games_remaining_this_period,
    )


def _espn_ids(session: Session, players: Sequence[RosteredPlayer]) -> dict[int, int]:
    """This database's player ids, mapped to ESPN's, as the pickup routes do."""
    return _espn_of(session, [player.player_id for player in players])


def _empty_pool(
    league_season: LeagueSeason, team: Team, day: int, missing: list[str]
) -> TradeFillPoolOut:
    """A season with no wire to read: the same answer the other two give."""
    return TradeFillPoolOut(
        season=int(league_season.season),
        today=day,
        espn_team_id=int(team.espn_team_id),
        team_name=str(team.name),
        ours=True,
        readiness=_readiness(league_season, missing),
        places_opened=0,
        opened_value=0.0,
        replacement=0.0,
        replacement_espn_player_id=None,
        pool_size=0,
        historical_wire=False,
        measured=False,
        candidates=[],
        calibration_note=CALIBRATION_NOTE,
    )


def _pool_out(
    pool: FillPool, session: Session, league_season: LeagueSeason, *, ours: int
) -> TradeFillPoolOut:
    wanted = [candidate.player_id for candidate in pool.candidates]
    if pool.replacement_player_id is not None:
        wanted.append(pool.replacement_player_id)
    espn = _espn_of(session, wanted)
    return TradeFillPoolOut(
        season=int(league_season.season),
        today=pool.today,
        espn_team_id=pool.team_id,
        team_name=pool.team_name,
        ours=pool.team_id == ours,
        readiness=TradeReadinessOut(ready=True, missing=[], note=None),
        places_opened=pool.places_opened,
        opened_value=pool.opened_value,
        replacement=pool.replacement,
        replacement_espn_player_id=(
            None
            if pool.replacement_player_id is None
            else espn.get(pool.replacement_player_id, pool.replacement_player_id)
        ),
        pool_size=pool.pool_size,
        historical_wire=pool.historical_wire,
        measured=pool.measured,
        candidates=[
            _candidate_out(candidate, espn, pool.categories) for candidate in pool.candidates
        ],
        calibration_note=CALIBRATION_NOTE,
    )


def _candidate_out(
    candidate: FillCandidate, espn: dict[int, int], categories: Sequence[str]
) -> TradeFillCandidateOut:
    return TradeFillCandidateOut(
        espn_player_id=espn.get(candidate.player_id, candidate.player_id),
        name=candidate.name,
        position=candidate.position,
        pro_team_id=candidate.pro_team_id,
        pro_team=pro_team_name(candidate.pro_team_id),
        injury_status=candidate.injury_status,
        expected_return_date=candidate.expected_return_date,
        hurt=candidate.hurt,
        on_waivers=candidate.on_waivers,
        waiver_clears_at=candidate.waiver_clears_at,
        waiver_clears_on=candidate.waiver_clears_on,
        games_left=candidate.games_left,
        worth=candidate.worth,
        value=candidate.value,
        weekly=_nine(candidate.weekly, categories),
    )


def _nine(line: CategoryLine, categories: Sequence[str]) -> dict[str, float]:
    """A weekly line as the nine a page draws, percentages rebuilt from the
    made and attempted under them (`app.scoring.lines`)."""
    return line.totals(list(categories))


def _espn_of(session: Session, player_ids: Sequence[int]) -> dict[int, int]:
    wanted = sorted(set(player_ids))
    if not wanted:
        return {}
    return {
        int(player_id): int(espn_player_id)
        for player_id, espn_player_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.id.in_(wanted))
        ).all()
    }


def _trade_out(report: TradeReport, session: Session, *, ours: int) -> TradeOut:
    cards = [
        card
        for side in report.sides
        for card in (
            *side.receives,
            *side.gives,
            *side.drops,
            *side.fills,
            *((side.replacement_player,) if side.replacement_player is not None else ()),
        )
    ]
    espn = _card_espn_ids(session, cards)
    sides = sorted(report.sides, key=lambda side: side.team_id != ours)
    return TradeOut(
        season=report.season,
        today=report.today,
        effective_day=report.effective_day,
        review_days=report.review_days,
        review_source=report.review_source,
        first_scoring_period=report.first_scoring_period,
        last_scoring_period=report.last_scoring_period,
        weeks_remaining=report.weeks_remaining,
        sides=[_side_out(side, espn) for side in sides],
        hurdle=report.hurdle,
        pool_size=report.pool_size,
        historical_wire=report.historical_wire,
        notes=list(report.notes),
    )


def _card_espn_ids(session: Session, cards: Sequence[PlayerCard]) -> dict[int, int]:
    return _espn_of(session, [card.player_id for card in cards])


def _side_out(side: SideReport, espn: dict[int, int]) -> TradeSideOut:
    return TradeSideOut(
        espn_team_id=side.team_id,
        team_name=side.team_name,
        receives=[_card_out(card, espn) for card in side.receives],
        gives=[_card_out(card, espn) for card in side.gives],
        drops=[_card_out(card, espn) for card in side.drops],
        fills=[_card_out(card, espn) for card in side.fills],
        drop_source=side.drop_source,
        places_opened=side.places_opened,
        places_filled=side.places_filled,
        places_left_open=side.places_left_open,
        places_used=side.places_used,
        judgement=_judgement_out(side.judgement),
        season_independent=side.season_independent,
        categories=[_category_out(view) for view in side.categories],
        playoffs=_playoffs_out(side.playoffs),
        replacement=side.replacement,
        opened_value=side.opened_value,
        replacement_player=(
            None if side.replacement_player is None else _card_out(side.replacement_player, espn)
        ),
        hurdle=side.hurdle,
        clears=side.clears,
        net=side.net,
        per_week=side.per_week,
        expected_per_week=side.expected_per_week,
        summary=side.summary,
        notes=list(side.notes),
    )


def _card_out(card: PlayerCard, espn: dict[int, int]) -> TradePlayerOut:
    return TradePlayerOut(
        espn_player_id=espn.get(card.player_id, card.player_id),
        name=card.name,
        value=card.value,
        games_left=card.games_left,
        playoff_games=card.playoff_games,
        injury_status=card.injury_status,
        expected_return_date=card.expected_return_date,
        games_so_far=card.games_so_far,
        had_projection=card.had_projection,
        projection_source=card.projection_source,
        thin=card.thin,
        hurt=card.hurt,
    )


def _category_out(view: CategoryView) -> TradeCategoryOut:
    return TradeCategoryOut(
        abbreviation=view.abbreviation,
        before=view.before,
        after=view.after,
        delta=view.delta,
        p_before=view.p_before,
        p_after=view.p_after,
        p_delta=view.p_delta,
        moved=view.moved,
    )


def _playoffs_out(lens: PlayoffLens) -> TradePlayoffsOut:
    return TradePlayoffsOut(
        first_scoring_period=lens.first_scoring_period,
        last_scoring_period=lens.last_scoring_period,
        weeks=lens.weeks,
        games=lens.games,
        delta_per_week=lens.delta_per_week,
        delta_total=lens.delta_total,
        categories=[_category_out(view) for view in lens.categories],
        note=lens.note,
        measurable=lens.measurable,
    )


def _judgement_out(judgement: Judgement) -> JudgementOut:
    """The pickup routes' own shape, so a trade and a claim read the same."""
    return JudgementOut(
        delta_week=judgement.delta_week,
        delta_season_per_week=judgement.delta_season_per_week,
        weeks_remaining=judgement.weeks_remaining,
        delta_total=judgement.delta_total,
        per_week=judgement.per_week,
        replacement=judgement.replacement,
        banked_won=judgement.banked[0],
        banked_lost=judgement.banked[1],
        record_without=list(judgement.record_without),
        record_with=list(judgement.record_with),
        measured=judgement.measured,
    )
