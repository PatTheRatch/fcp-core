"""The season pass: codes, redeeming one, and how long a pass runs.

`app/billing.py` against a schema of this module's own. The routes and the
pages over it are tests/test_upgrade.py; this is the database half, which
the owner's command line uses too.
"""

import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, billing
from app.db.models import (
    CompCode,
    CompCodeRedemption,
    Entitlement,
    League,
    LeagueSeason,
    MatchupPeriod,
    ProTeamGame,
)
from app.db.session import make_engine, make_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent

CODE_SHAPE = re.compile(r"^[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$")


@pytest.fixture(scope="module")
def factory(test_database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")
    engine = make_engine(test_database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as open_session:
        open_session.execute(
            text(
                "TRUNCATE comp_code_redemptions, comp_codes, entitlements, users, leagues,"
                " pro_team_games RESTART IDENTITY CASCADE"
            )
        )
        open_session.commit()
        yield open_session
        open_session.rollback()


def user(session: Session, email: str) -> int:
    found = accounts.get_or_create_user(session, email)
    session.commit()
    return found.id


def code(session: Session, **ask: object) -> CompCode:
    made = billing.make_code(session, None, **ask)  # type: ignore[arg-type]
    session.commit()
    return made


# ---------------------------------------------------------------------------
# the code itself
# ---------------------------------------------------------------------------


def test_a_code_is_twelve_unambiguous_characters_in_threes_of_four() -> None:
    assert len(billing.ALPHABET) == 32 and len(set(billing.ALPHABET)) == 32
    for ambiguous in "0O1I":
        assert ambiguous not in billing.ALPHABET
    drawn = {billing.new_code() for _ in range(500)}
    assert len(drawn) == 500, "no two alike in 500"
    for made in drawn:
        assert CODE_SHAPE.match(made), made


def test_a_code_is_typed_back_however_it_comes() -> None:
    assert billing.normalise_code("abcd-efgh-jkmn") == "ABCD-EFGH-JKMN"
    assert billing.normalise_code("ABCDEFGHJKMN") == "ABCD-EFGH-JKMN"
    assert billing.normalise_code(" abcd efgh jkmn ") == "ABCD-EFGH-JKMN"
    assert billing.normalise_code("ab-cdef-ghjkm-n") == "ABCD-EFGH-JKMN"
    for typed in (
        "",
        "ABCD-EFGH-JKM",
        "ABCD-EFGH-JKMNP",
        "ABCD-EFGH-JKM0",
        "OBCD-EFGH-JKMN",
        "x" * 80,
    ):
        assert billing.normalise_code(typed) is None, typed


# ---------------------------------------------------------------------------
# redeeming
# ---------------------------------------------------------------------------


def test_a_code_redeems_into_a_pass_until_its_date(session: Session) -> None:
    until = datetime(2027, 6, 30, 23, 59, tzinfo=UTC)
    made = code(session, note="for Dennis", uses=1, valid_until=until)
    dennis = user(session, "dennis@example.com")

    held = billing.redeem(session, dennis, made.code.lower().replace("-", ""))
    session.commit()

    assert held.source == "comp" and held.tier == "team" and held.valid_until == until
    assert accounts.active_entitlement(session, dennis) is not None
    session.refresh(made)
    assert made.uses_left == 0
    spent = session.scalars(select(CompCodeRedemption)).one()
    assert (spent.code_id, spent.user_id, spent.entitlement_id) == (made.id, dennis, held.id)
    assert billing.list_codes(session)[0].state == "spent"
    assert [r.email for r in billing.list_codes(session)[0].redeemed] == ["dennis@example.com"]


def refused(session: Session, user_id: int, typed: str) -> str:
    with pytest.raises(billing.RedeemRefusedError) as caught:
        billing.redeem(session, user_id, typed)
    session.rollback()
    return caught.value.sentence


def test_every_code_that_does_not_work_hears_the_same_sentence(session: Session) -> None:
    erin = user(session, "erin@example.com")
    spent = code(session, uses=1)
    billing.redeem(session, user(session, "first@example.com"), spent.code)
    session.commit()
    revoked = code(session)
    assert billing.revoke(session, revoked.id)
    session.commit()
    late = code(session)
    session.execute(
        text("UPDATE comp_codes SET redeem_by = now() - interval '1 day' WHERE id = :i"),
        {"i": late.id},
    )
    session.commit()

    for typed in ("ZZZZ-ZZZZ-ZZZZ", "not a code", spent.code, revoked.code, late.code):
        assert refused(session, erin, typed) == billing.NO_GOOD, typed
    assert accounts.active_entitlement(session, erin) is None
    assert session.scalar(select(CompCode.uses_left).where(CompCode.id == revoked.id)) == 1


def test_one_man_cannot_burn_a_multi_use_code_twice(session: Session) -> None:
    league_code = code(session, uses=5)
    frank = user(session, "frank@example.com")
    billing.redeem(session, frank, league_code.code)
    session.commit()

    # While his pass is live: he is told he has one, and nothing is stacked.
    sentence = refused(session, frank, league_code.code)
    assert sentence.startswith("You already have a pass until ")
    # Once it has lapsed: the code is spent for him, though it has uses left.
    session.execute(
        text("UPDATE entitlements SET valid_until = now() - interval '1 day' WHERE user_id = :u"),
        {"u": frank},
    )
    session.commit()
    assert refused(session, frank, league_code.code) == billing.NO_GOOD
    session.refresh(league_code)
    assert league_code.uses_left == 4


def test_a_live_pass_is_not_stacked_on(session: Session) -> None:
    gina = user(session, "gina@example.com")
    billing.grant(session, gina, "comp", datetime.now(UTC) + timedelta(days=40), note="by hand")
    session.commit()
    other = code(session)
    sentence = refused(session, gina, other.code)
    assert "You already have a pass until" in sentence
    assert session.scalar(select(CompCode.uses_left).where(CompCode.id == other.id)) == 1
    count = session.scalar(
        text("SELECT count(*) FROM entitlements WHERE user_id = :u"), {"u": gina}
    )
    assert count == 1

    # The owner's own pass never ends, and says so.
    owner = accounts.ensure_owner(session, "owner@example.com", None, None)
    assert "does not end" in refused(session, owner.id, other.code)


def test_two_men_racing_on_a_one_use_code_one_wins(factory: sessionmaker[Session]) -> None:
    with factory() as setup:
        setup.execute(
            text("TRUNCATE comp_code_redemptions, comp_codes, entitlements, users CASCADE")
        )
        one_use = billing.make_code(setup, None, uses=1)
        racers = [accounts.get_or_create_user(setup, f"racer{n}@example.com").id for n in range(8)]
        setup.commit()
        typed = one_use.code

    start = threading.Barrier(len(racers))
    won: list[int] = []
    lost: list[str] = []
    lock = threading.Lock()

    def race(user_id: int) -> None:
        with factory() as mine:
            # Connected before the gun, so the race is the redemption and not
            # who opened a connection first.
            mine.execute(text("SELECT 1"))
            start.wait()
            try:
                billing.redeem(mine, user_id, typed)
                mine.commit()
                with lock:
                    won.append(user_id)
            except billing.RedeemRefusedError as no:
                mine.rollback()
                with lock:
                    lost.append(no.sentence)

    threads = [threading.Thread(target=race, args=(uid,)) for uid in racers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(won) == 1, won
    assert lost == [billing.NO_GOOD] * (len(racers) - 1)
    with factory() as check:
        assert check.scalar(select(CompCode.uses_left)) == 0
        assert check.scalar(text("SELECT count(*) FROM comp_code_redemptions")) == 1
        assert check.scalar(text("SELECT count(*) FROM entitlements")) == 1


# ---------------------------------------------------------------------------
# making codes, and how long a pass runs
# ---------------------------------------------------------------------------


def test_a_code_the_owner_cannot_mean_is_refused(session: Session) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    for ask in (
        {"uses": 0},
        {"uses": billing.MAX_USES + 1},
        {"valid_until": past},
        {"redeem_by": past},
        {"note": "x" * (billing.MAX_NOTE + 1)},
    ):
        with pytest.raises(billing.CodeError):
            billing.make_code(session, None, **ask)  # type: ignore[arg-type]
    session.rollback()
    with pytest.raises(ValueError):
        billing.grant(session, 1, "owner", None)


def test_with_no_season_stored_a_pass_runs_a_year(session: Session) -> None:
    at = datetime(2026, 9, 26, tzinfo=UTC)
    assert billing.season_end(session) is None
    assert billing.default_valid_until(session, at) == at + timedelta(days=365)


def test_a_pass_runs_to_the_newest_seasons_playoffs_and_a_month(session: Session) -> None:
    league = League(espn_league_id=77)
    session.add(league)
    session.flush()
    season = LeagueSeason(
        league_id=league.id,
        season=2027,
        name="League 77",
        scoring_type="H2H_CATEGORY",
        team_count=2,
        regular_season_periods=1,
        total_matchup_periods=2,
        playoff_team_count=2,
        playoff_matchup_period_length=1,
        keeper_count=0,
        uses_faab=True,
        acquisition_budget=100,
        median_scoring=False,
        raw_settings={},
    )
    session.add(season)
    session.flush()
    session.add_all(
        [
            MatchupPeriod(
                league_season_id=season.id,
                period=1,
                is_playoff=False,
                first_scoring_period=1,
                final_scoring_period=6,
            ),
            MatchupPeriod(
                league_season_id=season.id,
                period=2,
                is_playoff=True,
                first_scoring_period=7,
                final_scoring_period=160,
            ),
            # Opening night, day 1: Oct 20, 2026. Day 160 is Mar 28, 2027.
            ProTeamGame(
                season=2027,
                pro_team_id=1,
                scoring_period=1,
                game_at=datetime(2026, 10, 20, 23, 30, tzinfo=UTC),
                opponent_pro_team_id=2,
                home=True,
            ),
            ProTeamGame(
                season=2027,
                pro_team_id=1,
                scoring_period=170,
                game_at=datetime(2027, 4, 7, 23, 30, tzinfo=UTC),
                opponent_pro_team_id=2,
                home=True,
            ),
        ]
    )
    session.commit()

    end = billing.season_end(session)
    assert end == datetime(2027, 3, 28, 23, 59, 59, tzinfo=UTC)
    at = datetime(2026, 9, 26, tzinfo=UTC)
    assert billing.default_valid_until(session, at) == end + timedelta(days=30)
    # A store that has not seen the new season: a year from the day.
    later = datetime(2027, 6, 1, tzinfo=UTC)
    assert billing.default_valid_until(session, later) == later + timedelta(days=365)
    made = code(session)
    assert made.valid_until == billing.default_valid_until(session)


def test_the_counts_are_live_passes_and_open_codes(session: Session) -> None:
    accounts.ensure_owner(session, "owner@example.com", None, None)
    code(session)
    code(session, uses=3)
    dead = code(session)
    billing.revoke(session, dead.id)
    hana = user(session, "hana@example.com")
    billing.grant(session, hana, "comp", datetime.now(UTC) + timedelta(days=5))
    lapsed = user(session, "ian@example.com")
    session.add(
        Entitlement(
            user_id=lapsed,
            tier="team",
            source="comp",
            valid_until=datetime.now(UTC) - timedelta(days=1),
        )
    )
    session.commit()
    assert billing.counts(session) == billing.Counts(live_passes=1, open_codes=2)


def test_latest_pass_names_a_lapsed_one(session: Session) -> None:
    jo = user(session, "jo@example.com")
    assert billing.latest_pass(session, jo) is None
    ended = datetime(2026, 5, 1, tzinfo=UTC)
    session.add(Entitlement(user_id=jo, tier="team", source="comp", valid_until=ended))
    session.commit()
    found = billing.latest_pass(session, jo)
    assert found is not None and found.valid_until == ended
    assert billing.says_until(ended) == "May 1, 2026"


# ---------------------------------------------------------------------------
# the owner's command line (scripts/comp_code.py), on this module's schema
# ---------------------------------------------------------------------------


def test_the_command_line_makes_lists_revokes_and_grants(session: Session) -> None:
    from scripts import comp_code

    code_, out = comp_code.run(
        session, ["make", "--note", "for Dennis", "--uses", "2", "--valid-until", "2027-06-30"]
    )
    assert code_ == 0
    printed = out.splitlines()[0]
    assert CODE_SHAPE.match(printed), out
    assert "2 uses; the pass runs until Jun 30, 2027. Note: for Dennis" in out

    dennis = user(session, "dennis@example.com")
    billing.redeem(session, dennis, printed)
    session.commit()
    code_, listed = comp_code.run(session, ["list"])
    assert code_ == 0 and printed in listed and "1/2 left" in listed
    assert "dennis@example.com" in listed

    assert comp_code.run(session, ["revoke", printed.lower()])[0] == 0
    assert comp_code.run(session, ["revoke", printed])[0] == 1, "already revoked"
    assert comp_code.run(session, ["revoke", "ZZZZ-ZZZZ-ZZZZ"])[0] == 1

    code_, said = comp_code.run(
        session, ["grant", "--email", "New@Example.com", "--until", "2027-06-30"]
    )
    assert (code_, said) == (0, "new@example.com has a pass until Jun 30, 2027.")
    again = comp_code.run(session, ["grant", "--email", "new@example.com", "--until", "2027-07-30"])
    assert again[0] == 1 and "already has a pass" in again[1]
    assert comp_code.run(session, ["make", "--uses", "0"])[0] == 1
