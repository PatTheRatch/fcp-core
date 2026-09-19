"""The scorecard route: one team's season graded in categories a week.

Computed on request from the stored season (`app.scoring.scorecard`), not
cached. A season's scorecard takes a few seconds, most of it the wire grades.
"""

from fastapi import APIRouter

from app.api.access import LEAGUE_MEMBER
from app.api.deps import LeagueSeasonDep, SessionDep, TeamDep
from app.api.schemas import (
    DraftGradeOut,
    LensValueOut,
    MoveGradeOut,
    PlayerValueOut,
    ScorecardOut,
    TradeGradeOut,
    VerdictOut,
    WireMoveOut,
)
from app.scoring.moves import MoveGrade
from app.scoring.players import LensValue
from app.scoring.scorecard import scorecard
from app.scoring.verdicts import Verdict

router = APIRouter(tags=["scoring"])


def _verdict(verdict: Verdict | None) -> VerdictOut | None:
    if verdict is None:
        return None
    return VerdictOut(
        label=verdict.label, text=verdict.text, decision=verdict.decision, result=verdict.result
    )


def _lens(lens: LensValue) -> LensValueOut:
    return LensValueOut(
        team_fit=lens.team_fit,
        team_fit_per_week=lens.team_fit_per_week,
        league_standard=lens.league_standard,
        league_standard_per_week=lens.league_standard_per_week,
        weeks_started=lens.weeks_started,
        weeks_held=lens.weeks_held,
    )


def _move(grade: MoveGrade | None) -> MoveGradeOut | None:
    if grade is None:
        return None
    judged = _verdict(grade.verdict)
    assert judged is not None
    return MoveGradeOut(
        periods=list(grade.periods), decision=grade.decision, result=grade.result, verdict=judged
    )


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/scorecard",
    summary="A team's season graded: players, draft, trades and the wire",
    dependencies=[LEAGUE_MEMBER],
)
def get_scorecard(
    team: TeamDep, league_season: LeagueSeasonDep, session: SessionDep
) -> ScorecardOut:
    card = scorecard(session, int(league_season.season), team.id)
    return ScorecardOut(
        season=card.season,
        espn_team_id=int(team.espn_team_id),
        team_name=card.team_name,
        replacement=card.replacement,
        category_record=card.category_record,
        looks_like_punts=list(card.looks_like_punts),
        players=[
            PlayerValueOut(
                player_id=p.player_id,
                name=p.name,
                regular=_lens(p.regular),
                playoffs=_lens(p.playoffs),
            )
            for p in card.players
        ],
        draft=[
            DraftGradeOut(
                player_id=g.player_id,
                name=g.name,
                price=g.price,
                projected_value=g.projected_value,
                market=g.market,
                market_source=g.market_source,
                outcome=g.outcome,
                delivered=g.delivered,
                decision=g.decision,
                result=g.result,
                verdict=_verdict(g.verdict),
            )
            for g in card.draft
        ],
        trades=[
            TradeGradeOut(
                day=g.trade.day,
                counterparties=list(g.trade.counterparty_names),
                players_in=[p.name for p in g.trade.players_in],
                players_out=[p.name for p in g.trade.players_out],
                part_missing=g.part_missing,
                regular=_move(g.regular),
                playoffs=_move(g.playoffs),
            )
            for g in card.trades
        ],
        wire=[
            WireMoveOut(
                day=m.day,
                kind=m.kind,
                added=list(m.added),
                dropped=list(m.dropped),
                regular=_move(m.regular),
                playoffs=_move(m.playoffs),
            )
            for m in card.wire
        ],
    )
