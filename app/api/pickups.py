"""The recommender's reports, for one team on one day.

Three of them now: who starts today (`/today`), who to stream this week
(`/pickups/stream`) and who to hold for the rest of it
(`/pickups/season`). Read-only, like the rest of the API, and keyed on ESPN
ids: a caller who knows the league, the year and the team id can ask every
one of them without knowing anything about this database's own keys, so
player ids go out as ESPN's too.

`/today` is the morning question and the smallest of the three: one day's
lineup, the same seating the week report solves each of its days with
(`app.pickups.today`), with the places named. It reads nothing after the day
it is asked about.

Both reports generate ideas; the manager decides. `recommended` on either
one is the moves **worth a look**, in the order they are worth making, and
`hurdle` is a bar a move has to clear to get there -- not an instruction to
make it. An empty `recommended` says nothing cleared the bar, which is an
answer in itself (docs/pickups.md section 4).

Neither route talks to ESPN. Both read what the listener and the ingest
have already stored, which is why a season the listener has never seen is
a 409 rather than an empty report: the difference between "nothing clears
the bar" and "no rows to decide on" is the whole value of the answer.

`today` is a scoring period. Left out, it is the calendar day turned into
one through the stored NBA schedule, which before opening night is the
season's first day (`app.pickups.state.SeasonCalendar`).

STORED REPORTS

The morning precompute (app/job_kinds.py) builds each claimed team's three
reports and stores them (`team_reports`, app/reports.py). When the report
asked for is today's and a row built today is there, it is answered from the
row, at once; otherwise it is built live, as it always was. Nothing here
writes a row: a report built on demand is served and forgotten, so a route
never races the precompute and the API stays a reader of its own jobs.

`/pickups/glance` is the free This week page's look at the reader's own
week: the expected categories and the projected record, and nothing of the
plan. It needs the team's manager but not the paid tier (docs/product.md,
"Free and paid"), which is what lets the free page stop reading the paid
route (docs/site.md).
"""

from collections.abc import Callable, Iterable
from datetime import date
from typing import Annotated, Any

from espn_api.basketball.constant import PRO_TEAM_MAP
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import calibration, reports
from app.api.access import TEAM_MANAGER, TEAM_PLAN
from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.schemas import (
    BidOut,
    BoxScoreOut,
    CategoryShiftOut,
    DropCandidateOut,
    EmptyDayOut,
    GlanceOut,
    JudgementOut,
    LockOut,
    PickupPlayerOut,
    PostedManOut,
    RungOut,
    ScheduleDayOut,
    ScheduleManOut,
    ScheduleOut,
    SeasonReportOut,
    SeasonSwapOut,
    SideGamesOut,
    StashCandidateOut,
    StashOut,
    StreamMoveOut,
    StreamReportOut,
    TodayBenchedOut,
    TodayFixOut,
    TodayGameOut,
    TodayPlayerOut,
    TodayReportOut,
    TodaySeatOut,
    VolumeGuardOut,
)
from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    Player,
    PlayerStatusSnapshot,
    ProTeamGame,
    Team,
)
from app.pickups.bids import Bid
from app.pickups.judge import Judgement
from app.pickups.season import DropCandidate, SeasonReport, StashCandidate, Swap
from app.pickups.season import season_recommendations as build_season
from app.pickups.stash import Lock, Stash
from app.pickups.state import BoxScore, PostedMan, RosteredPlayer, SeasonCalendar, season_calendar
from app.pickups.stream import CategoryShift, Move, Schedule, SideGames, StreamReport
from app.pickups.stream import stream_recommendations as build_stream
from app.pickups.today import Benched, DayPlayer, Misstart, Seat, TodayReport
from app.pickups.today import today_lineup as build_today
from app.projections.sources import ESPN, describe

router = APIRouter(tags=["pickups"])

TodayQuery = Annotated[
    int | None,
    Query(ge=1, description="Scoring period to report on; defaults to today's"),
]

#: What a caller is told when the season has nothing to build a report from.
#: Two things are needed and neither can be guessed: the NBA schedule, which
#: dates every scoring period and says who plays when, and some record of who
#: held whom -- the listener's status snapshots for the season in progress, or
#: the stored lineup days for a season already played. A played season has no
#: snapshots, because the listener only ever runs for the current year
#: (`app.pickups.state.has_free_agent_snapshots`), and that is not a reason to
#: refuse it: its lineup days are the roster and its box scores are the wire.
NOT_LISTENED = "season {season} has nothing to build a pickup report from: {missing}"
NO_SCHEDULE = "no NBA schedule is stored (scripts/backfill_pro_schedule.py)"
NO_ROSTER = "no status snapshots and no lineup days, so no roster can be read"

#: Today, as these routes read it: which day a request without `today` means,
#: and which day a stored row counts as fresh for. The server never moves it.
#: A rehearsal of a season already played (`scripts/rehearse_week.py`) does,
#: because "today" there is a morning in January and a row built now would
#: otherwise never be the fresh one -- which is the only way to show that the
#: page is answered from the store rather than rebuilt.
TODAY: Callable[[], date] = date.today


def readiness(
    session: Session, league_season: LeagueSeason
) -> tuple[SeasonCalendar | None, list[str]]:
    """The season's calendar, and what is missing to report on it (nothing
    when a report can be built).

    A schedule, and a roster from one of the two places one can come from.
    The snapshot check used to be the only one, which refused every played
    season although its lineup days say exactly who was held on every day of
    it; a season with the schedule backfilled now reports.
    """
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    scheduled = session.scalar(select(ProTeamGame.id).where(ProTeamGame.season == season).limit(1))
    snapshots = session.scalar(
        select(PlayerStatusSnapshot.id).where(PlayerStatusSnapshot.season == season).limit(1)
    )
    lineups = session.scalar(
        select(DailyLineupSlot.id)
        .join(Team, Team.id == DailyLineupSlot.team_id)
        .where(Team.league_season_id == league_season.id)
        .limit(1)
    )
    missing = []
    if calendar is None or not scheduled:
        missing.append(NO_SCHEDULE)
    if not snapshots and not lineups:
        missing.append(NO_ROSTER)
    return calendar, missing


def _ready(session: Session, league_season: LeagueSeason) -> SeasonCalendar:
    """The season's calendar, or 409 when there is nothing to report on."""
    calendar, missing = readiness(session, league_season)
    if missing or calendar is None:
        raise HTTPException(
            status_code=409,
            detail=NOT_LISTENED.format(
                season=int(league_season.season), missing=" and ".join(missing)
            ),
        )
    return calendar


def _day(calendar: SeasonCalendar, today: int | None) -> int:
    return today if today is not None else calendar.scoring_period_on(TODAY())


def build_payload(
    session: Session, league_season: LeagueSeason, team: Team, kind: str, day: int
) -> dict[str, Any]:
    """One report, built live, as the JSON its route answers. Raises
    `ValueError` when there is no week to report on (`day` in no period).

    The precompute job stores exactly this, so a stored answer and a live
    one cannot drift apart.

    **The bars are this league's** (`app.calibration`, docs/intake.md): its
    own measured ones, or the ones its manager chose, or the pool of leagues
    like it, or the defaults -- and whichever it is travels with the report
    as `hurdle_source` and `hurdle_note`, so the page can say it.
    """
    espn_team_id = int(team.espn_team_id)
    bars = calibration.bars(session, int(league_season.league_id))
    out: StreamReportOut | SeasonReportOut | TodayReportOut
    if kind == reports.STREAM:
        stream = build_stream(
            session,
            league_season,
            espn_team_id,
            day,
            hurdle=bars.stream_hurdle.number,
            floor=bars.typical_pickup.number,
            opened=bars.opened_place.number,
        )
        out = _stream_out(
            stream,
            _espn_ids(session, _stream_players(stream), _posted_ids(stream)),
            bars,
        )
    elif kind == reports.SEASON:
        season = build_season(
            session,
            league_season,
            espn_team_id,
            day,
            hurdle_paid=bars.season_hurdle_paid.number,
            hurdle_free=bars.season_hurdle_free.number,
            floor=bars.typical_pickup.number,
            opened=bars.opened_place.number,
        )
        out = _season_out(season, _espn_ids(session, _season_players(season)), bars)
    elif kind == reports.TODAY:
        lineup = build_today(session, league_season, espn_team_id, day)
        out = _today_out(lineup, _espn_ids(session, _today_players(lineup)), _source(league_season))
    else:
        raise ValueError(f"unknown report kind {kind!r}")
    return out.model_dump(mode="json")


def _source(league_season: LeagueSeason) -> str:
    """Where the numbers came from, in the words the pages use.

    The same line `app.api.pages.page_context` carries, written the same
    way: every figure on the day's report is a game, a start or a knowable
    line off this season's own box scores and ESPN's, and ESPN's are ours to
    show (`app.projections.sources`, docs/projection_sources.md).
    """
    return describe(ESPN, f"{int(league_season.season)} season, from the stored box scores")


def stored_report(
    session: Session, calendar: SeasonCalendar, team: Team, kind: str, day: int
) -> dict[str, Any] | None:
    """Today's stored report, when `day` is today and a row built today is there."""
    on = TODAY()
    if day != calendar.scoring_period_on(on):
        return None
    row = reports.fresh(session, team.id, kind, day, on=on)
    return dict(row.payload) if row is not None else None


def report(
    session: Session,
    league_season: LeagueSeason,
    team: Team,
    kind: str,
    today: int | None,
) -> tuple[dict[str, Any], bool]:
    """The report asked for, and whether it was the stored one: stored when
    fresh, else built now.

    Public, because the co-manager's tools answer with the same payload the
    page is drawn from (`app/mcp/tools.py`) and have to be able to say which
    of the two it was.
    """
    calendar = _ready(session, league_season)
    day = _day(calendar, today)
    stored = stored_report(session, calendar, team, kind, day)
    if stored is not None:
        return stored, True
    try:
        return build_payload(session, league_season, team, kind, day), False
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream",
    summary="Who to stream this week, and whether anyone is worth a look",
    dependencies=[TEAM_PLAN],
)
def stream_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> StreamReportOut:
    body, _ = report(session, league_season, team, reports.STREAM, today)
    return StreamReportOut.model_validate(body)


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season",
    summary="Who to hold for the rest of the year, who should go, and what to bid",
    dependencies=[TEAM_PLAN],
)
def season_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> SeasonReportOut:
    body, _ = report(session, league_season, team, reports.SEASON, today)
    return SeasonReportOut.model_validate(body)


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/today",
    summary="Who starts today, and who is on the bench with a game",
    dependencies=[TEAM_PLAN],
)
def today_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> TodayReportOut:
    """The day's lineup: the proposed starters by place, each man's game or
    "no game", the bench men with a game and why they are not in it, and the
    places the team has actually set with a man who is not playing while its
    bench has one who is.

    The week report's own seating for one day (`app.pickups.today`), so the
    two can never disagree about who starts. A proposal, not an instruction:
    the reasons travel with it and the manager sets the lineup."""
    body, _ = report(session, league_season, team, reports.TODAY, today)
    return TodayReportOut.model_validate(body)


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/glance",
    summary="This week at a glance, for the team's manager: categories and the record",
    dependencies=[TEAM_MANAGER],
)
def glance(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> GlanceOut:
    """The free tier's look at the reader's own week, from the stored week
    report when there is one (built live otherwise): the expected categories
    against this week's opponent, their chances one by one, and the season's
    projected record with no move made. Not the moves: those are the plan."""
    body, stored = report(session, league_season, team, reports.STREAM, today)
    return GlanceOut(
        espn_team_id=int(body["espn_team_id"]),
        matchup_period=int(body["matchup_period"]),
        opponent_espn_team_id=body["opponent_espn_team_id"],
        expected_wins=float(body["expected_wins"]),
        probabilities=dict(body["probabilities"]),
        record_without=[float(x) for x in body["outlook"]["record_without"]],
        stored=stored,
    )


def _espn_ids(
    session: Session, players: list[RosteredPlayer], extra: Iterable[int] = ()
) -> dict[int, int]:
    """This database's player ids, mapped to ESPN's, for one report.

    `extra` names men the report carries who are not `RosteredPlayer`s: the
    men behind the posted score, one of whom may since have been dropped and
    so be on no roster the report holds.
    """
    wanted = sorted({player.player_id for player in players} | {int(each) for each in extra})
    if not wanted:
        return {}
    return {
        int(player_id): int(espn_player_id)
        for player_id, espn_player_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.id.in_(wanted))
        ).all()
    }


def _posted_ids(report: StreamReport) -> list[int]:
    """The men in either side's posted score, whose ESPN ids the page needs."""
    return [man.player_id for man in (*report.posted_men, *report.opponent_posted_men)]


def _stream_players(report: StreamReport) -> list[RosteredPlayer]:
    found: list[RosteredPlayer] = []
    # The plan's second move comes from a second search, so its men need not
    # be among the listed ones.
    for move in (*report.moves, *report.recommended):
        found.extend(player for player in (move.add, move.drop, move.to_ir) if player is not None)
    for day in report.empty_days:
        found.extend(day.fillers)
    for games in report.schedule.days:
        for side in (games.mine, games.theirs):
            if side is not None:
                found.extend(man.player for man in side.men)
    return found


def _today_players(report: TodayReport) -> list[RosteredPlayer]:
    found = [*report.idle, *report.injured_reserve]
    for place in (*report.lineup, *report.actual):
        if place.player is not None:
            found.append(place.player)
    for benched in report.benched:
        found.extend([benched.player, *benched.behind])
    for misstart in report.fix:
        found.extend(misstart.instead)
    return [day.player for day in found]


def _season_players(report: SeasonReport) -> list[RosteredPlayer]:
    found: list[RosteredPlayer] = []
    for move in (report.best_add, report.best_swap, report.best_two_swap):
        if move is not None:
            found.extend([*move.out, *move.into])
    for drop in report.drops:
        found.append(drop.player)
        if drop.replacement is not None:
            found.append(drop.replacement)
    found.extend(stash.player for stash in report.stashes)
    return found


def _player_out(player: RosteredPlayer, espn: dict[int, int]) -> PickupPlayerOut:
    return PickupPlayerOut(
        espn_player_id=espn.get(player.player_id, player.player_id),
        name=player.name,
        pro_team_id=player.pro_team_id,
        # The mark a page sets beside his name. ESPN's own table, the one
        # `app.pickups.today` already reads to write "at MIL", so the two
        # abbreviations on a row can never come from two different places.
        pro_team=PRO_TEAM_MAP.get(int(player.pro_team_id)),
        position=player.position,
        injury_status=player.injury_status,
        expected_return_date=player.expected_return_date,
        games_remaining=player.games_remaining_this_period,
        on_ir=player.on_ir,
        waiver_clears_at=player.waiver_clears_at,
        waiver_clears_on=player.waiver_clears_on,
    )


def _shifts(moved: tuple[CategoryShift, ...]) -> list[CategoryShiftOut]:
    return [
        CategoryShiftOut(
            abbreviation=shift.abbreviation,
            before=shift.before,
            after=shift.after,
            delta=shift.delta,
        )
        for shift in moved
    ]


def _bid_out(bid: Bid | None) -> BidOut | None:
    if bid is None:
        return None
    return BidOut(
        amount=bid.amount,
        rank=bid.rank,
        bucket=bid.bucket,
        basis=bid.basis,
        uncapped=bid.uncapped,
        low=bid.low,
        high=bid.high,
        sample=bid.sample,
        capped_by=bid.capped_by,
        note=bid.note,
        worth_dollars=bid.worth_dollars,
        ceiling=bid.ceiling,
        rate=bid.rate,
        rate_note=bid.rate_note,
        ladder=[
            RungOut(asked=rung.asked, amount=rung.amount, win_chance=rung.win_chance, n=rung.n)
            for rung in bid.ladder
        ],
    )


def _judgement_out(judgement: Judgement) -> JudgementOut:
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


def _move_out(move: Move, hurdle: float, espn: dict[int, int]) -> StreamMoveOut:
    return StreamMoveOut(
        kind=move.kind,
        add=_player_out(move.add, espn),
        drop=_player_out(move.drop, espn) if move.drop is not None else None,
        to_ir=_player_out(move.to_ir, espn) if move.to_ir is not None else None,
        delta=move.delta,
        net=move.net,
        judgement=_judgement_out(move.judgement),
        add_starts=move.add_starts,
        drop_starts=move.drop_starts,
        fills_empty_day=move.fills_empty_day,
        clears_hurdle=move.clears(hurdle),
        moved=_shifts(move.moved()),
        bid=_bid_out(move.bid),
    )


def _side_out(side: SideGames, espn: dict[int, int]) -> SideGamesOut:
    return SideGamesOut(
        games=side.games,
        seated=side.seated,
        open_places=side.open_places,
        men=[
            ScheduleManOut(**_player_out(man.player, espn).model_dump(), seated=man.seated)
            for man in side.men
        ],
    )


def _schedule_out(schedule: Schedule, espn: dict[int, int]) -> ScheduleOut:
    """The games table as the page reads it: the days, then the two totals."""
    theirs = schedule.theirs_total
    return ScheduleOut(
        days=[
            ScheduleDayOut(
                scoring_period=day.scoring_period,
                mine=_side_out(day.mine, espn),
                theirs=None if day.theirs is None else _side_out(day.theirs, espn),
            )
            for day in schedule.days
        ],
        mine_total=_side_out(schedule.mine_total, espn),
        theirs_total=None if theirs is None else _side_out(theirs, espn),
    )


def _posted_out(men: tuple[PostedMan, ...], espn: dict[int, int]) -> list[PostedManOut]:
    """The score a man at a time, in the order the engine sorted them.

    The page reads these and not the matchups route for the live number. The
    two answer different questions: `/matchups` is ESPN's stored tally for
    the whole period as of the last ingest, with no day column to cap it by,
    while these are the engine's own posted-so-far as of the report's
    `today` -- which on a replayed day is a week that had not been played
    yet (`app.pickups.state`, "what has been posted so far"). A page drawn
    from the route would print the finished week under a chance built on the
    engine's, and the two would disagree in public.
    """
    return [
        PostedManOut(
            espn_player_id=espn.get(man.player_id, man.player_id),
            name=man.name,
            games=man.games,
            minutes=man.minutes,
            line=dict(man.line.counts),
        )
        for man in men
    ]


def _stream_out(
    report: StreamReport, espn: dict[int, int], bars: calibration.Bars
) -> StreamReportOut:
    return StreamReportOut(
        espn_team_id=report.team_id,
        matchup_period=report.matchup_period,
        scoring_periods_remaining=list(report.scoring_periods_remaining),
        opponent_espn_team_id=report.opponent_team_id,
        expected_wins=report.expected_wins,
        probabilities=dict(report.probabilities),
        projected=dict(report.projected.counts),
        opponent_projected=dict(report.opponent_projected.counts),
        posted=dict(report.posted.counts),
        opponent_posted=dict(report.opponent_posted.counts),
        posted_source=report.posted_source,
        posted_men=_posted_out(report.posted_men, espn),
        opponent_posted_men=_posted_out(report.opponent_posted_men, espn),
        moves=[_move_out(move, report.hurdle, espn) for move in report.moves],
        recommended=[_move_out(move, report.hurdle, espn) for move in report.recommended],
        empty_days=[
            EmptyDayOut(
                scoring_period=day.scoring_period,
                empty_slots=list(day.empty_slots),
                fillers=[_player_out(player, espn) for player in day.fillers],
            )
            for day in report.empty_days
        ],
        schedule=_schedule_out(report.schedule, espn),
        outlook=_judgement_out(report.outlook),
        hurdle=report.hurdle,
        hurdle_source=bars.stream_hurdle.source,
        hurdle_note=bars.stream_hurdle.note,
        pool_size=report.pool_size,
        historical_wire=report.historical_wire,
        faab_remaining=report.faab_remaining,
        faab_overspent=report.faab_overspent,
        open_slots=report.open_slots,
        ir_slot_free=report.ir_slot_free,
        adds_used=report.adds_used,
        adds_budget=report.adds_budget,
        adds_left=report.adds_left,
        stashed=[stash_out(block) for block in report.stashed],
    )


def _box_out(box: BoxScore | None) -> BoxScoreOut | None:
    """A man's stored line for the day, or None until the ingest has it."""
    if box is None:
        return None
    return BoxScoreOut(
        scoring_period=box.scoring_period,
        minutes=box.minutes,
        points=box.line.get("PTS"),
        rebounds=box.line.get("REB"),
        assists=box.line.get("AST"),
        steals=box.line.get("STL"),
        blocks=box.line.get("BLK"),
        three_pointers_made=box.line.get("3PM"),
        turnovers=box.line.get("TO"),
        field_goals_made=box.line.get("FGM"),
        field_goals_attempted=box.line.get("FGA"),
        free_throws_made=box.line.get("FTM"),
        free_throws_attempted=box.line.get("FTA"),
    )


def _day_out(player: DayPlayer, espn: dict[int, int]) -> TodayPlayerOut:
    return TodayPlayerOut(
        **_player_out(player.player, espn).model_dump(),
        line=_box_out(player.box),
        game=(
            None
            if player.game is None
            else TodayGameOut(
                opponent_pro_team_id=player.game.opponent_pro_team_id,
                opponent=player.game.opponent,
                home=player.game.home,
                describe=player.game.describe(),
                at=player.game.at,
            )
        ),
        status=player.status,
        plays=player.plays,
    )


def _seat_out(place: Seat, espn: dict[int, int]) -> TodaySeatOut:
    return TodaySeatOut(
        slot=place.slot,
        player=None if place.player is None else _day_out(place.player, espn),
    )


def _benched_out(benched: Benched, espn: dict[int, int]) -> TodayBenchedOut:
    return TodayBenchedOut(
        player=_day_out(benched.player, espn),
        reason=benched.reason,
        behind=[_day_out(player, espn) for player in benched.behind],
    )


def _fix_out(misstart: Misstart, espn: dict[int, int]) -> TodayFixOut:
    return TodayFixOut(
        seat=_seat_out(misstart.seat, espn),
        instead=[_day_out(player, espn) for player in misstart.instead],
    )


def _today_out(report: TodayReport, espn: dict[int, int], source_note: str) -> TodayReportOut:
    return TodayReportOut(
        espn_team_id=report.team_id,
        today=report.today,
        calendar_date=report.calendar_date,
        matchup_period=report.matchup_period,
        teams_playing=report.teams_playing,
        lineup=[_seat_out(place, espn) for place in report.lineup],
        starts=report.starts,
        actual_starts=report.actual_starts,
        benched=[_benched_out(benched, espn) for benched in report.benched],
        idle=[_day_out(player, espn) for player in report.idle],
        injured_reserve=[_day_out(player, espn) for player in report.injured_reserve],
        actual_known=report.actual_known,
        actual=[_seat_out(place, espn) for place in report.actual],
        fix=[_fix_out(misstart, espn) for misstart in report.fix],
        projected=dict(report.projected.counts),
        actual_projected=dict(report.actual_projected.counts),
        edge=report.edge,
        source_note=source_note,
    )


def _swap_out(swap: Swap, report: SeasonReport, espn: dict[int, int]) -> SeasonSwapOut:
    return SeasonSwapOut(
        kind=swap.kind,
        out=[_player_out(player, espn) for player in swap.out],
        into=[_player_out(player, espn) for player in swap.into],
        delta=swap.delta,
        net=swap.net,
        judgement=_judgement_out(swap.judgement),
        costs_faab=swap.costs_faab,
        hurdle=swap.hurdle(report.hurdle_paid, report.hurdle_free),
        clears_hurdle=swap.clears(report.hurdle_paid, report.hurdle_free),
        moved=_shifts(swap.moved()),
        bid=_bid_out(swap.bid),
    )


def _drop_out(drop: DropCandidate, espn: dict[int, int]) -> DropCandidateOut:
    return DropCandidateOut(
        player=_player_out(drop.player, espn),
        replacement=_player_out(drop.replacement, espn) if drop.replacement is not None else None,
        delta=drop.delta,
    )


def _stash_out(stash: StashCandidate, espn: dict[int, int]) -> StashCandidateOut:
    return StashCandidateOut(
        player=_player_out(stash.player, espn),
        expected_return_date=stash.expected_return_date,
        weeks_away=stash.weeks_away,
        healthy_rank=stash.healthy_rank,
        needs_drop=stash.needs_drop,
        stash=stash_out(stash.stash),
    )


def stash_out(stash: Stash) -> StashOut:
    """The stash block as a page reads it. Public: the what-if draws it too."""
    return StashOut(
        player_id=stash.player_id,
        name=stash.name,
        days_out=stash.days_out,
        return_odds_by_week={str(week): odds for week, odds in stash.return_odds_by_week.items()},
        expected_dead_weeks=stash.expected_dead_weeks,
        dead_cost=stash.dead_cost,
        expected_net=stash.expected_net,
        net_if_out_past_week=stash.net_if_out_past_week,
        late_week=stash.late_week,
        ir_slot_free=stash.ir_slot_free,
        expected_games=stash.expected_games,
        healthy_games=stash.healthy_games,
        line=stash.line,
        language=stash.language,
        lock=_lock_out(stash.lock),
    )


def _lock_out(lock: Lock | None) -> LockOut | None:
    """The playoff lens, when the team has already won its place."""
    if lock is None:
        return None
    return LockOut(
        playoff_odds=lock.playoff_odds,
        seeding_stake=lock.seeding_stake,
        back_by_playoffs=lock.back_by_playoffs,
        playoff_weeks=lock.playoff_weeks,
        playoff_weeks_value=lock.playoff_weeks_value,
        dead_regular_weeks=lock.dead_regular_weeks,
        dead_playoff_weeks=lock.dead_playoff_weeks,
        benefit=lock.benefit,
        cost=lock.cost,
        lock_net=lock.lock_net,
        net_if_seed_settled=lock.net_if_seed_settled,
        net_if_seed_open=lock.net_if_seed_open,
        line=lock.line,
        language=lock.language,
    )


def _season_out(
    report: SeasonReport, espn: dict[int, int], bars: calibration.Bars
) -> SeasonReportOut:
    def out(swap: Swap | None) -> SeasonSwapOut | None:
        return _swap_out(swap, report, espn) if swap is not None else None

    return SeasonReportOut(
        espn_team_id=report.team_id,
        today=report.today,
        last_scoring_period=report.last_scoring_period,
        weeks_remaining=report.weeks_remaining,
        total_weeks=report.total_weeks,
        expected_wins=report.expected_wins,
        probabilities=dict(report.probabilities),
        weekly=dict(report.weekly),
        best_add=out(report.best_add),
        best_swap=out(report.best_swap),
        best_two_swap=out(report.best_two_swap),
        recommended=out(report.recommended),
        drops=[_drop_out(drop, espn) for drop in report.drops],
        stashes=[_stash_out(stash, espn) for stash in report.stashes],
        churn=VolumeGuardOut(
            adds=report.churn.adds, days=report.churn.days, finding=report.churn.finding
        ),
        outlook=_judgement_out(report.outlook),
        hurdle_paid=report.hurdle_paid,
        hurdle_free=report.hurdle_free,
        hurdle_source=bars.season_hurdle_paid.source,
        hurdle_note=bars.season_hurdle_paid.note,
        pool_size=report.pool_size,
        historical_wire=report.historical_wire,
        faab_remaining=report.faab_remaining,
        faab_overspent=report.faab_overspent,
        open_slots=report.open_slots,
        ir_slot_free=report.ir_slot_free,
        adds_used=report.adds_used,
        adds_budget=report.adds_budget,
        adds_left=report.adds_left,
    )
