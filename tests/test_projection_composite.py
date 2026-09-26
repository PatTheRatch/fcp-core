"""A composite source (`app.projections.composite`): the arithmetic, the gate, the cache.

A three-man fixture on the scoring test database: ESPN projects Evan Mobley
and Darius Garland; an uploaded sheet projects Mobley and a rookie nobody
of ours is called. A composite of ESPN at 30 and the sheet at 70 is worked
out by hand below and compared row for row: Mobley blended, Garland ESPN's
alone ("1 of 2"), the rookie the sheet's alone, and FG% from weighted makes
and attempts, which is not the weighted mean of the two percentages.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlayerSeasonStat, ProjectionRow, ProjectionSet
from app.draft import plan as engine
from app.draft.live import Room
from app.draft.market import price_board
from app.draft.optimizer import candidates_from
from app.draft.room import Allocation, DraftState
from app.draft.valuation import value_players
from app.player_names import synthetic_id
from app.projections import composite, sources
from app.projections.upload import COUNTS, import_set, load_projection_set
from tests.scoring_db import player
from tests.test_draft_plan import PTS

SEASON = 2027
ESPN_W, SHEET_W = 30.0, 70.0

#: ESPN's per-game lines (stored as season totals, as ESPN's are).
ESPN = {
    "Evan Mobley": (
        70,
        dict(PTS=20, REB=10, AST=3, STL=1, BLK=2, TO=2, FGM=8, FGA=14, FTM=3, FTA=4),
    ),
    "Darius Garland": (
        60,
        dict(PTS=22, REB=3, AST=7, STL=1.2, BLK=0.1, TO=2.6, FGM=8, FGA=18, FTM=4, FTA=4.5),
    ),
}
ESPN_THREES = {"Evan Mobley": 1.0, "Darius Garland": 2.8}
ESPN_MPG = {"Evan Mobley": 34.0, "Darius Garland": 33.0}

SHEET = (
    "Player,GP,PTS,REB,AST,STL,BLK,3PM,TO,FGM,FGA,FTM,FTA,MPG,$\n"
    "Evan Mobley,66,17,9,2,0.8,1.5,0.5,1.8,7,16,2,3,31,40\n"
    "Rookie Nobody,50,10,4,1,0.5,0.4,1.2,1.0,4,9,1,2,22,\n"
)


def seed(session: Session, tmp_path: Path, sheet: str = SHEET) -> ProjectionSet:
    for name, (games, rates) in ESPN.items():
        who = player(session, name)
        totals = {k: v * games for k, v in rates.items()}
        totals["3PM"] = ESPN_THREES[name] * games
        totals["MPG"] = ESPN_MPG[name]
        session.add(
            PlayerSeasonStat(
                player_id=who.id,
                season=SEASON,
                kind="projected",
                games_played=float(games),
                points=totals["PTS"],
                raw_totals=totals,
                eligible_slots=["PF", "C", "UT"],
                primary_position="PF",
            )
        )
    session.flush()
    return upload(session, tmp_path, sheet)


def upload(session: Session, tmp_path: Path, sheet: str) -> ProjectionSet:
    path = tmp_path / "sheet.csv"
    path.write_text(sheet, encoding="utf-8")
    report = import_set(session, season=SEASON, name="Sheet", owner="patrick", path=path)
    assert report.ok, report.mapping.problems
    found = session.get(ProjectionSet, report.set_id)
    assert found is not None
    return found


def make(session: Session, sheet: ProjectionSet, **kwargs: object) -> ProjectionSet:
    return composite.save(
        session,
        season=SEASON,
        owner="patrick",
        owners=["patrick"],
        owns_bbm=bool(kwargs.get("owns_bbm", False)),
        name=str(kwargs.get("name", "Blend")),
        recipe=kwargs.get("recipe")  # type: ignore[arg-type]
        or [{"source": "espn", "weight": ESPN_W}, {"source": sheet.id, "weight": SHEET_W}],
    )


def rows_of(session: Session, set_id: int) -> dict[str, ProjectionRow]:
    return {
        row.name: row
        for row in session.scalars(select(ProjectionRow).where(ProjectionRow.set_id == set_id))
    }


def test_a_composites_rows_are_the_hand_worked_weighted_mean(
    scoring_session: Session, tmp_path: Path
) -> None:
    sheet = seed(scoring_session, tmp_path)
    blend = make(scoring_session, sheet)
    rows = rows_of(scoring_session, blend.id)
    assert set(rows) == {"Evan Mobley", "Darius Garland", "Rookie Nobody"}
    assert blend.rows == 3

    w_e, w_s = ESPN_W / 100, SHEET_W / 100
    mobley = rows["Evan Mobley"]
    assert mobley.games == pytest.approx(w_e * 70 + w_s * 66)  # 67.2
    assert mobley.points == pytest.approx(w_e * 20 + w_s * 17)  # 17.9
    assert mobley.three_pointers_made == pytest.approx(w_e * 1.0 + w_s * 0.5)
    assert mobley.field_goals_made == pytest.approx(w_e * 8 + w_s * 7)  # 7.3
    assert mobley.field_goals_attempted == pytest.approx(w_e * 14 + w_s * 16)  # 15.4
    # FG% is the weighted makes over the weighted attempts ...
    fg = mobley.field_goals_made / mobley.field_goals_attempted
    assert fg == pytest.approx(7.3 / 15.4)  # .474
    # ... which is not the weighted mean of the two percentages (.478).
    assert abs(fg - (w_e * 8 / 14 + w_s * 7 / 16)) > 0.003
    assert mobley.minutes == pytest.approx(w_e * 34 + w_s * 31)
    assert mobley.value == pytest.approx(40.0), "only the sheet carries a value"
    assert mobley.raw == {"sources": ["espn", f"set:{sheet.id}"], "of": 2}
    assert mobley.player_id is not None

    garland = rows["Darius Garland"]
    assert garland.raw == {"sources": ["espn"], "of": 2}, "1 of 2: ESPN's number at full weight"
    assert garland.points == pytest.approx(22.0)
    assert garland.field_goals_attempted == pytest.approx(18.0)
    assert garland.games == pytest.approx(60.0)
    assert garland.value is None

    rookie = rows["Rookie Nobody"]
    assert rookie.raw == {"sources": [f"set:{sheet.id}"], "of": 2}
    assert rookie.player_id is None
    assert rookie.points == pytest.approx(10.0)
    assert rookie.value is None

    assert blend.built_from is not None
    assert blend.built_from["carried"] == {"2": 1, "1": 2}


def test_a_composite_loads_under_its_own_tag_with_totals(
    scoring_session: Session, tmp_path: Path
) -> None:
    sheet = seed(scoring_session, tmp_path)
    blend = make(scoring_session, sheet)
    pool = {p.name: p for p in load_projection_set(scoring_session, blend.id)}
    assert {p.source for p in pool.values()} == {f"composite:{blend.id}"}
    assert pool["Rookie Nobody"].player_id == synthetic_id("Rookie Nobody")
    mobley = pool["Evan Mobley"]
    assert mobley.totals["PTS"] == pytest.approx(17.9 * 67.2)


def test_bbm_in_a_recipe_gates_the_composite_like_bbm(
    scoring_session: Session, tmp_path: Path
) -> None:
    with_bbm = [{"source": "bbm", "weight": 50}, {"source": "espn", "weight": 50}]
    tag = sources.composite_source(9, with_bbm)
    assert tag == "composite:9+bbm" and sources.is_gated(tag)
    assert not sources.may_show(tag, viewer_owns_source=False)
    assert sources.may_show(tag, viewer_owns_source=True)
    without = sources.composite_source(9, [{"source": "espn", "weight": 1}])
    assert without == "composite:9" and not sources.is_gated(without)
    assert sources.may_show(without, viewer_owns_source=False)
    # BBM at weight 0 is not read, so it does not gate.
    assert not sources.is_gated(sources.composite_source(9, [{"source": "bbm", "weight": 0}]))
    assert sources.choice_of(tag) == "composite:9"

    sheet = seed(scoring_session, tmp_path)
    with pytest.raises(ValueError, match="private to the member"):
        make(scoring_session, sheet, recipe=with_bbm, owns_bbm=False)


def test_a_composite_rebuilds_when_an_input_changes_and_not_otherwise(
    scoring_session: Session, tmp_path: Path
) -> None:
    sheet = seed(scoring_session, tmp_path)
    blend = make(scoring_session, sheet)
    built = dict(blend.built_from or {})
    assert composite.ensure_current(scoring_session, blend) is False
    assert blend.built_from == built, "nothing moved, nothing rebuilt"

    # The sheet is uploaded again under its name: its id is kept, its rows are new.
    again = upload(scoring_session, tmp_path, SHEET.replace(",17,9,", ",27,9,"))
    assert again.id == sheet.id
    assert [c.id for c in composite.dependents(scoring_session, sheet.id)] == [blend.id]
    assert composite.stale(scoring_session, blend)
    assert composite.ensure_current(scoring_session, blend) is True
    mobley = rows_of(scoring_session, blend.id)["Evan Mobley"]
    assert mobley.points == pytest.approx(0.3 * 20 + 0.7 * 27)
    assert composite.ensure_current(scoring_session, blend) is False

    # So does a change of weights.
    composite.save(
        scoring_session,
        season=SEASON,
        owner="patrick",
        owners=["patrick"],
        owns_bbm=False,
        name="Blend",
        recipe=[{"source": "espn", "weight": 50}, {"source": sheet.id, "weight": 50}],
        composite=blend,
    )
    mobley = rows_of(scoring_session, blend.id)["Evan Mobley"]
    assert mobley.points == pytest.approx(0.5 * 20 + 0.5 * 27)


def test_a_recipe_is_checked(scoring_session: Session, tmp_path: Path) -> None:
    sheet = seed(scoring_session, tmp_path)
    with pytest.raises(ValueError, match="weight above 0"):
        make(scoring_session, sheet, recipe=[{"source": "espn", "weight": 0}])
    with pytest.raises(ValueError, match="from 0 to 100"):
        make(scoring_session, sheet, recipe=[{"source": "espn", "weight": 120}])
    with pytest.raises(ValueError, match="no projection set 999"):
        make(scoring_session, sheet, recipe=[{"source": 999, "weight": 1}])
    with pytest.raises(ValueError, match="already have a source called 'Sheet'"):
        make(scoring_session, sheet, name="Sheet")
    blend = make(scoring_session, sheet)
    with pytest.raises(ValueError, match="a recipe reads uploads"):
        make(scoring_session, sheet, name="Of a blend", recipe=[{"source": blend.id, "weight": 1}])


def test_the_plan_builds_on_a_composite(scoring_session: Session, tmp_path: Path) -> None:
    """The engine on the composite's own pool: a two-team room, $10 each,
    two places, the ceilings worked out inline -- seconds, not minutes."""
    sheet = seed(scoring_session, tmp_path)
    blend = make(scoring_session, sheet)
    projections = load_projection_set(scoring_session, blend.id)
    categories = ["PTS"]
    board = price_board(
        value_players(projections, categories), teams=2, budget_per_team=10, roster_slots=2
    )
    candidates = candidates_from(projections, board, periods=20, availability=1.0, keys=categories)
    state = DraftState.open(
        budget=10, roster_slots=2, teams={1: "Us", 2: "Them"}, me=1, nomination_order=(2, 1)
    )
    room = Room(
        season=SEASON,
        state=state,
        candidates=candidates,
        distributions=[PTS],
        lineup=("UT", "UT"),
        limits={},
        names={c.name: c.player_id for c in candidates},
        team_names={1: "Us", 2: "Them"},
        punt=(),
        restarts=2,
        allocation=Allocation.from_prices([6, 4], state),
        board={c.player_id: c.price for c in candidates},
        projection_source=sources.set_source(blend),
        projections=projections,
    )
    source = engine.PoolSource.of_set(scoring_session, blend.id)
    assert source.kind == "composite" and source.tag == f"composite:{blend.id}"
    assert source.key.startswith(f"composite:{blend.id}@")
    plan = engine.plan_from_room(
        room,
        draft_at=None,
        order=[2, 1],
        source=source,
        source_detail="",
        fan_team=None,
        must=None,
        ceilings=engine.ceilings_inline,
        today=dt.date(2026, 9, 26),
    )
    payload = plan.payload(dt.date(2026, 9, 26))
    assert payload["source"]["kind"] == "composite"
    assert {p["name"] for p in [*payload["players"], *payload["rest"]]} == {
        "Evan Mobley",
        "Darius Garland",
        "Rookie Nobody",
    }
    assert COUNTS["PTS"] == "points"
