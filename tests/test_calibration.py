"""Where a league's numbers come from, and in what order (docs/intake.md).

What is pinned: the accessor's four sources and the fallback when a
measurement is under its key's minimum; the pooled aggregate over a fixture
of three fake leagues with two settings shapes; that a re-measurement never
replaces a row the league's manager set; and that the migration's seed is the
constants themselves, to the digit, so the league every one of them was
measured on prints what it always printed.

No ESPN and no job queue here: this is the table and the accessor.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import calibration
from app.db.models import League, LeagueCalibration
from app.pickups.season import SEASON_HURDLE_FREE, SEASON_HURDLE_PAID
from app.pickups.stream import STREAM_HURDLE
from app.scoring.replacement import OPENED_PLACE, TYPICAL_PICKUP
from app.trades.calibration import CALIBRATION_NOTE
from tests.scoring_db import LEAGUE_ID, league_season

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    scoring_session.execute(text("TRUNCATE league_calibrations RESTART IDENTITY CASCADE"))
    scoring_session.commit()
    yield scoring_session


def _league(session: Session, espn_league_id: int = LEAGUE_ID) -> League:
    """A league with one nine-category season, so it has a settings shape."""
    found = session.scalar(
        text("SELECT id FROM leagues WHERE espn_league_id = :id").bindparams(id=espn_league_id)
    )
    if found is not None:
        return session.get(League, int(found))  # type: ignore[return-value]
    league = League(espn_league_id=espn_league_id)
    session.add(league)
    session.flush()
    return league


def _shaped(session: Session, espn_league_id: int, *, teams: int) -> League:
    """A league with a season, so `settings_key` has something to read."""
    league = _league(session, espn_league_id)
    season, _teams, _periods = league_season(
        session, season=2026, team_names=tuple(f"T{n}" for n in range(teams))
    )
    season.league_id = league.id
    session.flush()
    return league


# ---------------------------------------------------------------------------
# the four sources
# ---------------------------------------------------------------------------


def test_a_league_with_nothing_stored_gets_the_default_and_is_told_so(session: Session) -> None:
    league = _league(session)
    found = calibration.calibration(session, league.id, calibration.STREAM_HURDLE)
    assert found.source == calibration.DEFAULT
    assert found.value == pytest.approx(STREAM_HURDLE)
    assert "default" in found.note


def test_a_measurement_of_this_league_beats_the_default(session: Session) -> None:
    league = _shaped(session, LEAGUE_ID, teams=12)
    calibration.write(
        session,
        league.id,
        calibration.STREAM_HURDLE,
        value=0.15,
        source=calibration.MEASURED,
        n=400,
        note=calibration.measured_note(calibration.STREAM_HURDLE, 400),
    )
    session.commit()
    found = calibration.calibration(session, league.id, calibration.STREAM_HURDLE)
    assert (found.value, found.source, found.n) == (0.15, calibration.MEASURED, 400)
    assert found.note == "measured on this league, 400 decision points"


def test_a_measurement_under_the_minimum_is_kept_and_not_used(session: Session) -> None:
    """A hurdle fitted on forty decisions is a coincidence, not a number.

    The row stays -- the account page shows it, and the next run replaces it
    -- and the fallback carries on past it.
    """
    league = _shaped(session, LEAGUE_ID, teams=12)
    thin = calibration.minimum(calibration.STREAM_HURDLE) - 1
    calibration.write(
        session,
        league.id,
        calibration.STREAM_HURDLE,
        value=0.05,
        source=calibration.MEASURED,
        n=thin,
        note=calibration.measured_note(calibration.STREAM_HURDLE, thin),
    )
    session.commit()

    found = calibration.calibration(session, league.id, calibration.STREAM_HURDLE)
    assert found.source == calibration.DEFAULT, "not used"
    listed = {row.key: row for row in calibration.listing(session, league.id)}
    assert listed[calibration.STREAM_HURDLE].measured is not None, "still shown"
    assert listed[calibration.STREAM_HURDLE].measured.n == thin
    assert "too little to trust" in calibration.measured_note(calibration.STREAM_HURDLE, thin)


def test_the_owner_beats_the_measurement_and_a_re_measurement_never_replaces_him(
    session: Session,
) -> None:
    league = _shaped(session, LEAGUE_ID, teams=12)
    calibration.write(
        session,
        league.id,
        calibration.STREAM_HURDLE,
        value=0.15,
        source=calibration.MEASURED,
        n=600,
        note="measured",
    )
    chosen = calibration.set_by_owner(
        session,
        league.id,
        calibration.STREAM_HURDLE,
        value=0.30,
        reason="fewer moves; my FAAB is finite",
        on=NOW,
    )
    session.commit()
    assert chosen.note == "your choice, 2026-09-22: fewer moves; my FAAB is finite"

    refused = calibration.write(
        session,
        league.id,
        calibration.STREAM_HURDLE,
        value=0.10,
        source=calibration.MEASURED,
        n=900,
        note="measured again",
    )
    session.commit()
    assert refused is None, "a measurement does not overrule a choice"
    found = calibration.calibration(session, league.id, calibration.STREAM_HURDLE)
    assert (found.value, found.source) == (0.30, calibration.OWNER)

    assert calibration.forget_owner(session, league.id, calibration.STREAM_HURDLE)
    session.commit()
    # The measurement under it was replaced by the choice, so what is left is
    # the fallback: forgetting a bar does not conjure a run that never stored.
    assert calibration.calibration(session, league.id, calibration.STREAM_HURDLE).source in (
        calibration.MEASURED,
        calibration.DEFAULT,
    )


def test_only_the_three_bars_can_be_set_by_hand(session: Session) -> None:
    league = _league(session)
    with pytest.raises(ValueError, match="measurement, not a choice"):
        calibration.set_by_owner(
            session, league.id, calibration.TYPICAL_PICKUP, value=0.2, reason="I say so"
        )


# ---------------------------------------------------------------------------
# the pool
# ---------------------------------------------------------------------------


def _measure(session: Session, league: League, key: str, value: float, n: int) -> None:
    shape = calibration.settings_key(session, league.id)
    assert shape is not None
    calibration.write(
        session,
        league.id,
        key,
        value=value,
        source=calibration.MEASURED,
        n=n,
        note=calibration.measured_note(key, n),
        payload={"settings": shape.as_json(), "settings_digest": shape.digest},
    )


def test_the_pool_is_by_settings_and_is_weighted_by_the_sample(session: Session) -> None:
    """Three fake leagues, two shapes: the two twelves pool, the ten does not.

    And the pooled number is the n-weighted one, because a bar measured on
    1,200 decisions says more than one measured on 400.
    """
    twelve_a = _shaped(session, 900001, teams=12)
    twelve_b = _shaped(session, 900002, teams=12)
    ten = _shaped(session, 900003, teams=10)
    _measure(session, twelve_a, calibration.STREAM_HURDLE, 0.10, 400)
    _measure(session, twelve_b, calibration.STREAM_HURDLE, 0.20, 1200)
    _measure(session, ten, calibration.STREAM_HURDLE, 0.30, 800)
    session.commit()

    written = calibration.recompute_pool(session, at=NOW)
    session.commit()
    assert len(written) == 1, "one shape has two leagues; the other has one"
    pooled = written[0]
    assert pooled.value == pytest.approx((0.10 * 400 + 0.20 * 1200) / 1600)
    assert pooled.n == 1600
    assert pooled.note == "the pool of 2 leagues like yours"
    assert pooled.payload["leagues"] == 2
    assert pooled.payload["fitted"] is False, "no relationship across settings is fitted"

    # A fourth league of the twelve shape, never measured, reads the pool.
    newcomer = _shaped(session, 900004, teams=12)
    session.commit()
    found = calibration.calibration(session, newcomer.id, calibration.STREAM_HURDLE)
    assert found.source == calibration.POOLED
    assert found.value == pytest.approx(pooled.value)

    # The ten-team league is not in it, and reads the default.
    assert calibration.calibration(session, ten.id, calibration.STREAM_HURDLE).source == (
        calibration.MEASURED
    )
    lonely = _shaped(session, 900005, teams=10)
    session.commit()
    assert calibration.calibration(session, lonely.id, calibration.STREAM_HURDLE).source == (
        calibration.DEFAULT
    ), "one league of a shape is not a pool"


def test_a_measurement_under_the_minimum_stays_out_of_the_pool(session: Session) -> None:
    thin = calibration.minimum(calibration.STREAM_HURDLE) - 1
    first = _shaped(session, 900011, teams=14)
    second = _shaped(session, 900012, teams=14)
    _measure(session, first, calibration.STREAM_HURDLE, 0.10, 600)
    _measure(session, second, calibration.STREAM_HURDLE, 0.40, thin)
    session.commit()
    assert calibration.recompute_pool(session, at=NOW) == [], "one usable league is not a pool"


def test_a_stale_pooled_row_is_deleted_rather_than_left_behind(session: Session) -> None:
    first = _shaped(session, 900021, teams=8)
    second = _shaped(session, 900022, teams=8)
    _measure(session, first, calibration.OPENED_PLACE, 0.30, 500)
    _measure(session, second, calibration.OPENED_PLACE, 0.40, 500)
    session.commit()
    assert calibration.recompute_pool(session, at=NOW)
    session.commit()

    session.delete(
        calibration.stored(session, second.id, calibration.OPENED_PLACE)  # type: ignore[arg-type]
    )
    session.flush()
    calibration.recompute_pool(session, at=NOW)
    session.commit()
    assert calibration.stored(session, None, calibration.OPENED_PLACE) is None


def test_a_pooled_row_carries_no_leagues_numbers_but_its_own(session: Session) -> None:
    """The privacy rule: a pooled row is an aggregate and says so.

    Nothing in it names a league, counts one, or carries a roster, and the
    only thing it says about the leagues in it is how many there are.
    """
    first = _shaped(session, 900031, teams=12)
    second = _shaped(session, 900032, teams=12)
    _measure(session, first, calibration.TYPICAL_PICKUP, 0.05, 500)
    _measure(session, second, calibration.TYPICAL_PICKUP, 0.09, 500)
    session.commit()
    written = calibration.recompute_pool(session, at=NOW)
    session.commit()
    assert written
    body = written[0].payload
    assert set(body) == {"settings", "settings_digest", "leagues", "fitted"}
    assert "league_id" not in body and "name" not in body
    assert set(body["settings"]) == {
        "team_count",
        "roster_size",
        "adds_per_day",
        "uses_faab",
        "categories",
    }


def test_no_relationship_is_fitted_across_settings_yet(session: Session) -> None:
    """Ten leagues before anyone fits a line, and the code says so out loud."""
    assert calibration.FIT_LEAGUES == 10
    assert calibration.POOL_LEAGUES == 2
    assert calibration.__doc__ is not None
    assert "it stays a table" in calibration.__doc__, "and the module says so out loud"


# ---------------------------------------------------------------------------
# the seed: this league's pages did not move
# ---------------------------------------------------------------------------


def _migration() -> Any:
    """Migration 0026, loaded by path: its name is not an identifier."""
    path = REPO_ROOT / "migrations" / "versions" / "0026_league_calibrations.py"
    spec = importlib.util.spec_from_file_location("seed_0026", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seed_is_the_constants_themselves(session: Session) -> None:
    """Every seeded value is the constant it replaces, to the digit.

    This is the whole claim that Full Court Press's pages did not move: the
    migration writes what the modules held, and the accessor hands it back.
    """
    seed = _migration()
    assert seed.SEED_LEAGUE == LEAGUE_ID
    rows = {row[0]: row for row in seed.SEED_ROWS}
    assert set(rows) == set(calibration.KEYS)

    assert rows["typical_pickup"][1] == TYPICAL_PICKUP
    assert rows["opened_place"][1] == OPENED_PLACE
    assert rows["stream_hurdle"][1] == STREAM_HURDLE
    assert rows["season_hurdle_paid"][1] == SEASON_HURDLE_PAID
    assert rows["season_hurdle_free"][1] == SEASON_HURDLE_FREE
    assert rows["trade_record"][1] is None, "a table, not a number"
    assert rows["trade_record"][6] == CALIBRATION_NOTE, "byte for byte, what the page printed"

    owner = {key for key, row in rows.items() if row[5] == "owner"}
    assert owner == set(calibration.OWNER_SETTABLE), "the three bars are his choice"
    for key in owner:
        assert rows[key][6].startswith("your choice, 20")


def test_the_seeded_rows_reproduce_this_leagues_numbers_exactly(session: Session) -> None:
    """The seed applied, and the accessor answering with the constants."""
    seed = _migration()
    league = _shaped(session, LEAGUE_ID, teams=14)
    for key, value, n, measured_at, seconds, source, note, payload in seed.SEED_ROWS:
        calibration.write(
            session,
            league.id,
            key,
            value=value,
            source=source,
            n=n,
            note=note,
            payload=payload,
            run_seconds=seconds,
            measured_at=datetime.fromisoformat(f"{measured_at}T00:00:00+00:00"),
        )
    session.commit()

    bars = calibration.bars(session, league.id)
    assert bars.typical_pickup.value == pytest.approx(TYPICAL_PICKUP)
    assert bars.opened_place.value == pytest.approx(OPENED_PLACE)
    assert bars.stream_hurdle.value == pytest.approx(STREAM_HURDLE)
    assert bars.season_hurdle_paid.value == pytest.approx(SEASON_HURDLE_PAID)
    assert bars.season_hurdle_free.value == pytest.approx(SEASON_HURDLE_FREE)
    assert bars.trade_record.note == CALIBRATION_NOTE
    assert bars.stream_hurdle.source == calibration.OWNER
    assert bars.typical_pickup.source == calibration.MEASURED


def test_a_league_with_no_measurement_still_prints_the_published_trade_record(
    session: Session,
) -> None:
    """The default's note is the published sentence, not a summary of it.

    A manager shown a number is owed the record of that number; a league with
    no trades of its own to replay is being shown the number measured
    elsewhere, so it is shown that number's record.
    """
    league = _league(session, 900041)
    found = calibration.calibration(session, league.id, calibration.TRADE_RECORD)
    assert found.source == calibration.DEFAULT
    assert found.note == CALIBRATION_NOTE
    assert found.payload["cells"], "the 2x2 travels with it"


def test_one_row_per_league_and_key_and_one_pooled_row_per_key(session: Session) -> None:
    league = _shaped(session, 900051, teams=12)
    for value in (0.10, 0.20, 0.30):
        calibration.write(
            session,
            league.id,
            calibration.STREAM_HURDLE,
            value=value,
            source=calibration.MEASURED,
            n=600,
            note="measured",
        )
    session.commit()
    rows = session.scalars(
        text("SELECT id FROM league_calibrations WHERE key = 'stream_hurdle'")
    ).all()
    assert len(rows) == 1, "a re-measurement replaces, it does not append"
    assert calibration.calibration(session, league.id, calibration.STREAM_HURDLE).value == 0.30


def test_the_defaults_know_their_own_minimums_and_units() -> None:
    """Every key's minimum is written down beside what it counts."""
    assert calibration.minimum(calibration.STREAM_HURDLE) == 200
    assert calibration.minimum(calibration.OPENED_PLACE) == 100
    assert calibration.minimum(calibration.TRADE_RECORD) == 20
    assert calibration.unit(calibration.TRADE_RECORD) == "deals"
    assert calibration.unit(calibration.OPENED_PLACE) == "team-periods"
    for key in calibration.KEYS:
        assert calibration.DEFAULTS[key].note, f"{key} says where it came from"
        assert calibration.DEFAULTS[key].title, f"{key} has a name a page can print"


def test_an_unknown_key_is_refused_rather_than_invented(session: Session) -> None:
    league = _league(session)
    with pytest.raises(ValueError, match="unknown calibration key"):
        calibration.calibration(session, league.id, "vibes")
    with pytest.raises(ValueError, match="unknown calibration source"):
        calibration.write(
            session, league.id, calibration.STREAM_HURDLE, value=0.1, source="guess", n=1, note=""
        )


def test_a_league_with_no_season_matches_no_pool(session: Session) -> None:
    """A league connected a minute ago has no shape, so it joins nothing."""
    league = _league(session, 900061)
    session.commit()
    assert calibration.settings_key(session, league.id) is None
    assert calibration.calibration(session, league.id, calibration.OPENED_PLACE).source == (
        calibration.DEFAULT
    )


def test_the_pooled_trade_record_adds_the_deals_up(session: Session) -> None:
    first = _shaped(session, 900071, teams=12)
    second = _shaped(session, 900072, teams=12)
    for league, deals, picked in ((first, 40, 22), (second, 30, 14)):
        shape = calibration.settings_key(session, league.id)
        assert shape is not None
        calibration.write(
            session,
            league.id,
            calibration.TRADE_RECORD,
            value=None,
            source=calibration.MEASURED,
            n=deals,
            note="measured",
            payload={
                "deals": deals,
                "picked": picked,
                "settings": shape.as_json(),
                "settings_digest": shape.digest,
            },
        )
    session.commit()
    written = calibration.recompute_pool(session, at=NOW)
    session.commit()
    pooled = next(row for row in written if row.key == calibration.TRADE_RECORD)
    assert pooled.value is None
    assert (pooled.n, pooled.payload["deals"], pooled.payload["picked"]) == (70, 70, 36)


def test_writing_a_pooled_row_does_not_need_a_league(session: Session) -> None:
    calibration.write(
        session,
        None,
        calibration.OPENED_PLACE,
        value=0.33,
        source=calibration.POOLED,
        n=500,
        note=calibration.pooled_note(3),
        payload={"settings_digest": "x"},
    )
    session.commit()
    row = session.scalar(
        text("SELECT league_id FROM league_calibrations WHERE key = 'opened_place'")
    )
    assert row is None, "a pooled row belongs to no league"


def test_bars_resolves_every_key_at_once(session: Session) -> None:
    league = _league(session)
    bars = calibration.bars(session, league.id)
    assert [found.key for found in bars.all()] == list(calibration.KEYS)
    assert all(found.source == calibration.DEFAULT for found in bars.all())
    assert calibration.default_bars().stream_hurdle.value == pytest.approx(STREAM_HURDLE)


def test_a_row_describes_itself_in_one_line(session: Session) -> None:
    league = _shaped(session, 900081, teams=12)
    calibration.write(
        session,
        league.id,
        calibration.SEASON_HURDLE_PAID,
        value=0.20,
        source=calibration.MEASURED,
        n=616,
        note=calibration.measured_note(calibration.SEASON_HURDLE_PAID, 616),
    )
    session.commit()
    found = calibration.calibration(session, league.id, calibration.SEASON_HURDLE_PAID)
    assert found.describe() == "0.20 — measured on this league, 616 decision points"


def test_a_calibrated_row_without_a_number_refuses_to_pretend(session: Session) -> None:
    league = _league(session)
    record = calibration.calibration(session, league.id, calibration.TRADE_RECORD)
    with pytest.raises(ValueError, match="no single number"):
        _ = record.number


def test_how_many_leagues_have_a_usable_measurement(session: Session) -> None:
    first = _shaped(session, 900091, teams=12)
    second = _shaped(session, 900092, teams=10)
    _measure(session, first, calibration.STREAM_HURDLE, 0.2, 600)
    _measure(session, second, calibration.STREAM_HURDLE, 0.2, 10)
    session.commit()
    assert calibration.leagues_measured(session, calibration.STREAM_HURDLE) == 1


def test_the_table_holds_one_row_per_pair(
    scoring_factory: sessionmaker[Session],
) -> None:
    """The constraints, not the code, are what make it one row per pair."""
    with scoring_factory() as session:
        session.execute(text("TRUNCATE league_calibrations RESTART IDENTITY CASCADE"))
        league = _league(session)
        session.add_all(
            [
                LeagueCalibration(
                    league_id=league.id, key="stream_hurdle", value=0.1, source="measured", n=1
                ),
                LeagueCalibration(
                    league_id=league.id, key="stream_hurdle", value=0.2, source="measured", n=1
                ),
            ]
        )
        with pytest.raises(Exception, match="uq_league_calibrations_league_key"):
            session.commit()
        session.rollback()


def test_the_rate_limit_and_the_minimums_are_stated_in_the_document() -> None:
    """The doc and the code say the same numbers, or the doc is a story."""
    doc = (REPO_ROOT / "docs" / "intake.md").read_text()
    for key in calibration.KEYS:
        assert f"`{key}`" in doc, f"{key} is not in docs/intake.md"
        assert str(calibration.minimum(key)) in doc
    assert "once a day" in doc


def test_a_day_is_what_separates_two_measurements() -> None:
    from app import intake

    assert timedelta(days=1) == intake.AGAIN_AFTER
