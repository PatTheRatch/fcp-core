"""BBM projections kept as versions: a new row only when a player's line changes."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BBMCapture, BBMProjection
from app.draft.bbm_store import as_of, capture, history
from tests.scoring_db import player

MON, TUE, WED, THU = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)


def export(**games: float) -> list[dict[str, object]]:
    return [{"Name": name.replace("_", " "), "g": g, "p/g": 20.0} for name, g in games.items()]


def test_unchanged_rows_extend_and_changed_rows_version(scoring_session: Session) -> None:
    session = scoring_session
    player(session, "Victor Wembanyama", espn_id=1)
    capture(
        session,
        season=2027,
        value_type="total",
        rows=export(Victor_Wembanyama=61, Rookie_Guy=50),
        captured_on=MON,
    )
    capture(
        session,
        season=2027,
        value_type="total",
        rows=export(Victor_Wembanyama=61, Rookie_Guy=50),
        captured_on=TUE,
    )
    result = capture(
        session,
        season=2027,
        value_type="total",
        rows=export(Victor_Wembanyama=55, Rookie_Guy=50),
        captured_on=WED,
    )

    assert (result.players, result.changed, result.dropped) == (2, 1, 0)
    rookie = session.scalar(select(BBMProjection).where(BBMProjection.name == "Rookie Guy"))
    assert rookie is not None and rookie.last_seen == WED
    versions = history(session, 2027, "Victor Wembanyama")
    assert [(v.first_seen, v.last_seen, v.row["g"]) for v in versions] == [
        (MON, TUE, 61),
        (WED, WED, 55),
    ]
    assert versions[0].player_id is not None
    assert (
        session.scalar(select(BBMProjection.player_id).where(BBMProjection.name == "Rookie Guy"))
        is None
    )

    tuesday = {row.name: row.row["g"] for row in as_of(session, 2027, TUE)}
    assert tuesday == {"Rookie Guy": 50, "Victor Wembanyama": 61}
    assert {row.name: row.row["g"] for row in as_of(session, 2027, THU)}["Victor Wembanyama"] == 55


def test_a_dropped_player_is_counted_and_gone_from_later_days(scoring_session: Session) -> None:
    session = scoring_session
    capture(
        session, season=2027, value_type="total", rows=export(Stays=60, Waived=40), captured_on=MON
    )
    result = capture(
        session, season=2027, value_type="total", rows=export(Stays=60), captured_on=TUE
    )
    assert result.dropped == 1
    assert [row.name for row in as_of(session, 2027, TUE)] == ["Stays"]
    assert sorted(row.name for row in as_of(session, 2027, MON)) == ["Stays", "Waived"]


def test_a_second_pull_the_same_day_replaces_that_day(scoring_session: Session) -> None:
    session = scoring_session
    capture(session, season=2027, value_type="pergame", rows=export(Player=60), captured_on=MON)
    capture(session, season=2027, value_type="pergame", rows=export(Player=58), captured_on=TUE)
    capture(session, season=2027, value_type="pergame", rows=export(Player=57), captured_on=TUE)
    assert [(v.first_seen, v.row["g"]) for v in history(session, 2027, "Player", "pergame")] == [
        (MON, 60),
        (TUE, 57),
    ]
    assert session.scalars(select(BBMCapture.captured_on)).all() == [MON, TUE]


def test_value_types_are_kept_apart(scoring_session: Session) -> None:
    session = scoring_session
    capture(session, season=2027, value_type="total", rows=export(Player=60), captured_on=MON)
    capture(session, season=2027, value_type="pergame", rows=export(Player=60), captured_on=MON)
    assert len(as_of(session, 2027, MON, "total")) == 1
    assert len(as_of(session, 2027, MON, "pergame")) == 1
    assert as_of(session, 2027, date(2026, 11, 1)) == []
