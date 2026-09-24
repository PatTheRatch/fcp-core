"""One named pickup, judged: this week, the rest of the season, the finish.

    GET .../teams/{team_id}/what-if?drop=&add=&to_ir=&today=

The route the week page has never had. `/pickups/stream` answers "what should
I do", ranked by a search; this answers "what happens if I do *this*", named
by the manager. The engine is `app.inseason.what_if`, which builds the
judgement out of the recommender's own functions on the recommender's own
wire, so a move the search also found reads here exactly as it reads there --
and then adds the layer neither report has ever carried: where the move leaves
the team in the projected standings.

Read-only and keyed on ESPN ids, like the pickup and trade routes beside it: a
caller who knows the league, the year, the team and the players can ask
without knowing anything about this database's own keys.

WHAT IT IS NOT

Nothing here is advice. `clears_hurdle` is the same label the week page puts
on a move and it hides nothing: a change under the bar comes back in full with
its number. The finish is a **second lens and not a second bar** -- no field
is labelled against it, and every finish carries the sampling band on its own
odds and the published record of the forecast behind them.

IT IS NOT STORED

A hypothetical is not a report. The morning job precomputes the three reports
a team actually has; it cannot precompute a question nobody has asked yet. So
this is always built live, which costs two league projections (about four
seconds cold on a fourteen-team league -- docs/what_if.md, "Timing"), and
nothing here writes a row.

BAD INPUT

A change that cannot be made is a 422 with a sentence a manager can act on,
in the house style of docs/trades.md section 10: a man dropped who is not on
the roster, a man added who is not on the wire, a roster left over or under
size, a roster outside the position limits, an injured-reserve move with no
free place or no injury. A season with nothing to judge from answers 200 with
`readiness`, exactly as the trade routes do.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import calibration
from app.api import pickups
from app.api.access import TEAM_PLAN
from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.pickups import _bid_out, _judgement_out, _player_out, stash_out
from app.api.schemas import (
    CategoryShiftOut,
    FinishOut,
    FinishWeekOut,
    StashOut,
    TradeCategoryOut,
    WhatIfManOut,
    WhatIfOut,
    WhatIfPlayoffsOut,
    WhatIfWeekOut,
)
from app.db.models import Player
from app.inseason.what_if import (
    FINISH_IS_A_SECOND_LENS,
    Finish,
    WeekLayer,
    WhatIf,
    what_if,
)
from app.pickups.state import RosteredPlayer
from app.trades.evaluate import CategoryView

router = APIRouter(tags=["pickups"])

TodayQuery = Annotated[
    int | None,
    Query(ge=1, description="Scoring period to judge on; defaults to today's"),
]
PlayerIdsQuery = Annotated[
    list[int] | None,
    Query(description="ESPN player ids; repeat the parameter for several"),
]

#: What a caller is told when a player id names nobody this database knows.
NO_SUCH_PLAYER = "There is no player {ids} on record, so nothing can be judged about him."


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/what-if",
    summary="What one named move would do: this week, the rest of the season, the finish",
    dependencies=[TEAM_PLAN],
)
def what_if_report(
    league_season: LeagueSeasonDep,
    team: TeamDep,
    session: SessionDep,
    drop: PlayerIdsQuery = None,
    add: PlayerIdsQuery = None,
    to_ir: PlayerIdsQuery = None,
    today: TodayQuery = None,
) -> WhatIfOut:
    """Judge the change named in the query, as of the morning of `today`.

    `drop` are the men leaving this roster, `add` the men coming off the wire
    and `to_ir` the men moved to injured reserve, all by ESPN player id. The
    week and the judgement are the recommender's own numbers; the finish is
    the projected-standings engine run twice, once as things stand and once
    with this roster changed, on the same seed.
    """
    calendar = pickups._ready(session, league_season)
    day = pickups._day(calendar, today)
    bars = calibration.bars(session, int(league_season.league_id))
    try:
        report = what_if(
            session,
            league_season,
            int(team.espn_team_id),
            day,
            add=_ours(session, add),
            drop=_ours(session, drop),
            to_ir=_ours(session, to_ir),
            hurdle=bars.stream_hurdle.number,
            floor=bars.typical_pickup.number,
            opened=bars.opened_place.number,
        )
    except ValueError as error:
        raise _bad(str(error)) from error
    return _out(report, session, bars)


def _bad(detail: str) -> HTTPException:
    """A change that cannot be made: 422, and a sentence a manager can act on."""
    return HTTPException(status_code=422, detail=detail)


def _ours(session: Session, espn_ids: list[int] | None) -> tuple[int, ...]:
    """ESPN's player ids as this database's, in the order they were named."""
    wanted = list(dict.fromkeys(int(espn_id) for espn_id in (espn_ids or ())))
    if not wanted:
        return ()
    found = {
        int(espn_player_id): int(player_id)
        for player_id, espn_player_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.espn_player_id.in_(wanted))
        ).all()
    }
    unknown = [espn_id for espn_id in wanted if espn_id not in found]
    if unknown:
        raise _bad(NO_SUCH_PLAYER.format(ids=", ".join(str(one) for one in unknown)))
    return tuple(found[espn_id] for espn_id in wanted)


def _espn_of(session: Session, player_ids: list[int]) -> dict[int, int]:
    wanted = sorted(set(player_ids))
    if not wanted:
        return {}
    return {
        int(player_id): int(espn_player_id)
        for player_id, espn_player_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.id.in_(wanted))
        ).all()
    }


def _man_out(player: RosteredPlayer, starts: int, espn: dict[int, int]) -> WhatIfManOut:
    return WhatIfManOut(**_player_out(player, espn).model_dump(), starts=starts)


def category_out(view: CategoryView) -> TradeCategoryOut:
    """One category before and after, in counts and in the chance of winning it.

    Public and here rather than in `app.api.trades`, because both the trade
    page and the what-if's playoff lens print the same nine rows and
    `app.api.trades` already imports this module.
    """
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


def finish_out(finish: Finish) -> FinishOut:
    """One team's finish, before and after. Public: the trade report draws it too."""
    return FinishOut(
        espn_team_id=finish.team_id,
        team_name=finish.team_name,
        record_before=list(finish.record_before),
        record_after=list(finish.record_after),
        matchups_before=list(finish.matchups_before),
        matchups_after=list(finish.matchups_after),
        place_before=finish.place_before,
        place_after=finish.place_after,
        playoff_odds_before=finish.playoff_odds_before,
        playoff_odds_after=finish.playoff_odds_after,
        bye_odds_before=finish.bye_odds_before,
        bye_odds_after=finish.bye_odds_after,
        seed_odds_before=list(finish.seed_odds_before),
        seed_odds_after=list(finish.seed_odds_after),
        weeks=[
            FinishWeekOut(
                period=week.period,
                days_remaining=week.days_remaining,
                in_play=week.in_play,
                opponent_espn_team_id=week.opponent_team_id,
                opponent_name=week.opponent_name,
                expected_before=week.expected_before,
                expected_after=week.expected_after,
                delta=week.delta,
            )
            for week in finish.weeks
        ],
        n_sims=finish.n_sims,
        seed=finish.seed,
        odds_band=finish.odds_band,
        readable=finish.readable,
        noise_note=finish.noise_note,
        calibration_note=finish.calibration_note,
        language=FINISH_IS_A_SECOND_LENS,
    )


def _week_out(week: WeekLayer) -> WhatIfWeekOut:
    return WhatIfWeekOut(
        matchup_period=week.matchup_period,
        opponent_espn_team_id=week.opponent_team_id,
        opponent_name=week.opponent_name,
        days_remaining=week.days_remaining,
        posted=dict(week.posted.counts),
        opponent_posted=dict(week.opponent_posted.counts),
        before=dict(week.before),
        after=dict(week.after),
        expected_before=week.expected_before,
        expected_after=week.expected_after,
        delta=week.delta,
        moved=[
            CategoryShiftOut(
                abbreviation=shift.abbreviation,
                before=shift.before,
                after=shift.after,
                delta=shift.delta,
            )
            for shift in week.moved()
        ],
        fills_empty_day=week.fills_empty_day,
    )


def _out(report: WhatIf, session: Session, bars: calibration.Bars) -> WhatIfOut:
    everyone = [player.player_id for player in (*report.adds, *report.drops, *report.to_ir)]
    espn = _espn_of(session, everyone)
    return WhatIfOut(
        season=report.season,
        today=report.today,
        today_date=report.today_date,
        espn_team_id=report.team_id,
        team_name=report.team_name,
        adds=[
            _man_out(player, report.week.add_starts.get(player.player_id, 0), espn)
            for player in report.adds
        ],
        drops=[
            _man_out(player, report.week.drop_starts.get(player.player_id, 0), espn)
            for player in report.drops
        ],
        to_ir=[
            _man_out(player, report.week.drop_starts.get(player.player_id, 0), espn)
            for player in report.to_ir
        ],
        kind=report.kind,
        week=_week_out(report.week),
        finish=finish_out(report.finish),
        judgement=_judgement_out(report.judgement),
        net=report.net,
        hurdle=report.hurdle,
        hurdle_source=bars.stream_hurdle.source,
        hurdle_note=bars.stream_hurdle.note,
        clears_hurdle=report.clears_hurdle,
        bid=_bid_out(report.bid),
        pool_size=report.pool_size,
        historical_wire=report.historical_wire,
        notes=list(report.notes),
        stash=_stash(report),
        playoffs=_playoffs(report),
        playoff_language=report.playoff_language,
    )


def _stash(report: WhatIf) -> StashOut | None:
    """The stash block, when the change adds or holds a man who is ruled out."""
    return None if report.stash is None else stash_out(report.stash)


def _playoffs(report: WhatIf) -> WhatIfPlayoffsOut | None:
    """The playoff lens, which answers for every move rather than for a stash.

    The same `PlayoffLens` a trade side carries, so the page's trade tab and
    its what-if tab cannot disagree about what a roster change is worth in
    March. `measurable` False with a `note` is the answer when the playoff
    weeks are behind the day or the season stores none.
    """
    lens = report.playoffs
    if lens is None:
        return None
    return WhatIfPlayoffsOut(
        first_scoring_period=lens.first_scoring_period,
        last_scoring_period=lens.last_scoring_period,
        weeks=lens.weeks,
        games_added=lens.games_added,
        games_dropped=lens.games_dropped,
        delta_per_week=lens.delta_per_week,
        delta_total=lens.delta_total,
        expected_wins_before=lens.expected_wins_before,
        expected_wins_after=lens.expected_wins_after,
        categories=[category_out(view) for view in lens.categories],
        note=lens.note,
        measurable=lens.measurable,
        line=lens.line,
    )
