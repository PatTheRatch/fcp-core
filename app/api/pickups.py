"""The recommender's two reports, for one team on one day.

Read-only, like the rest of the API, and keyed on ESPN ids: a caller who
knows the league, the year and the team id can ask both questions without
knowing anything about this database's own keys, so player ids go out as
ESPN's too.

Neither route talks to ESPN. Both read what the listener and the ingest
have already stored, which is why a season the listener has never seen is
a 409 rather than an empty report: the difference between "no move is
worth making" and "no rows to decide on" is the whole value of the answer.

`today` is a scoring period. Left out, it is the calendar day turned into
one through the stored NBA schedule, which before opening night is the
season's first day (`app.pickups.state.SeasonCalendar`).
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.schemas import (
    BidOut,
    CategoryShiftOut,
    DropCandidateOut,
    EmptyDayOut,
    JudgementOut,
    PickupPlayerOut,
    SeasonReportOut,
    SeasonSwapOut,
    StashCandidateOut,
    StreamMoveOut,
    StreamReportOut,
    VolumeGuardOut,
)
from app.db.models import LeagueSeason, Player, PlayerStatusSnapshot, ProTeamGame
from app.pickups.bids import Bid
from app.pickups.judge import Judgement
from app.pickups.season import DropCandidate, SeasonReport, StashCandidate, Swap
from app.pickups.season import season_recommendations as build_season
from app.pickups.state import RosteredPlayer, SeasonCalendar, season_calendar
from app.pickups.stream import CategoryShift, Move, StreamReport
from app.pickups.stream import stream_recommendations as build_stream

router = APIRouter(tags=["pickups"])

TodayQuery = Annotated[
    int | None,
    Query(ge=1, description="Scoring period to report on; defaults to today's"),
]

#: What a caller is told when the listener has written nothing for the
#: season. Its tables are the report's only source of a roster, a wire and
#: a schedule, so there is nothing to build from and nothing to fetch.
NOT_LISTENED = (
    "the listener has stored nothing for season {season}: no NBA schedule or "
    "status snapshots, so no pickup report can be built"
)


def _ready(session: Session, league_season: LeagueSeason) -> SeasonCalendar:
    """The season's calendar, or 409 when the listener has not run for it."""
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    seen = session.scalar(
        select(ProTeamGame.id).where(ProTeamGame.season == season).limit(1)
    ) and session.scalar(
        select(PlayerStatusSnapshot.id).where(PlayerStatusSnapshot.season == season).limit(1)
    )
    if calendar is None or not seen:
        raise HTTPException(status_code=409, detail=NOT_LISTENED.format(season=season))
    return calendar


def _day(calendar: SeasonCalendar, today: int | None) -> int:
    return today if today is not None else calendar.scoring_period_on(date.today())


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/stream",
    summary="Who to stream this week, and whether anyone clears the hurdle",
)
def stream_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> StreamReportOut:
    calendar = _ready(session, league_season)
    try:
        report = build_stream(session, league_season, int(team.espn_team_id), _day(calendar, today))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _stream_out(report, _espn_ids(session, _stream_players(report)))


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/pickups/season",
    summary="Who to hold for the rest of the year, who should go, and what to bid",
)
def season_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    today: TodayQuery = None,
) -> SeasonReportOut:
    calendar = _ready(session, league_season)
    try:
        report = build_season(session, league_season, int(team.espn_team_id), _day(calendar, today))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _season_out(report, _espn_ids(session, _season_players(report)))


def _espn_ids(session: Session, players: list[RosteredPlayer]) -> dict[int, int]:
    """This database's player ids, mapped to ESPN's, for one report."""
    wanted = sorted({player.player_id for player in players})
    if not wanted:
        return {}
    return {
        int(player_id): int(espn_player_id)
        for player_id, espn_player_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.id.in_(wanted))
        ).all()
    }


def _stream_players(report: StreamReport) -> list[RosteredPlayer]:
    found: list[RosteredPlayer] = []
    # The plan's second move comes from a second search, so its men need not
    # be among the listed ones.
    for move in (*report.moves, *report.recommended):
        found.extend(player for player in (move.add, move.drop, move.to_ir) if player is not None)
    for day in report.empty_days:
        found.extend(day.fillers)
    return found


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


def _stream_out(report: StreamReport, espn: dict[int, int]) -> StreamReportOut:
    return StreamReportOut(
        espn_team_id=report.team_id,
        matchup_period=report.matchup_period,
        scoring_periods_remaining=list(report.scoring_periods_remaining),
        opponent_espn_team_id=report.opponent_team_id,
        expected_wins=report.expected_wins,
        probabilities=dict(report.probabilities),
        projected=dict(report.projected.counts),
        opponent_projected=dict(report.opponent_projected.counts),
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
        outlook=_judgement_out(report.outlook),
        hurdle=report.hurdle,
        pool_size=report.pool_size,
        faab_remaining=report.faab_remaining,
        open_slots=report.open_slots,
        ir_slot_free=report.ir_slot_free,
        adds_used=report.adds_used,
        adds_budget=report.adds_budget,
        adds_left=report.adds_left,
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
    )


def _season_out(report: SeasonReport, espn: dict[int, int]) -> SeasonReportOut:
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
        pool_size=report.pool_size,
        faab_remaining=report.faab_remaining,
        open_slots=report.open_slots,
        ir_slot_free=report.ir_slot_free,
        adds_used=report.adds_used,
        adds_budget=report.adds_budget,
        adds_left=report.adds_left,
    )
