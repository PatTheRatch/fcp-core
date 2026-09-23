"""What a member wants to hear about, per league (`app.subscriptions`).

What is pinned: the defaults a new member gets without a row being written;
that a topic he switched off keeps a line out of his feed; that every topic
off is a state the job can see; that a league that is not his is a 404 like
one that does not exist; and that the page always sends the whole answer, so
a topic left out of a body takes its default rather than its last value.

The feed itself is `tests/test_inseason_changes.py`'s: what is under test
here is the filter over it, so the changes are built by hand in the one shape
`app.inseason.changes` reports everything in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import accounts, memberships, subscriptions
from app.api import access
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.inseason import changes as feed
from app.inseason.changes import Change, Person
from app.main import create_app
from app.secrets_box import new_key

KEY = new_key()
LEAGUE_ID = 3853870
NOW = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)


def settings_for(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "fcp_auth_mode": "accounts",
        "fcp_secrets_key": KEY,
        "fcp_owner_email": "owner@example.com",
        "espn_league_id": None,
        "fcp_tracked_team_id": None,
    }
    base.update(changes)
    return get_settings().model_copy(update=base)


@pytest.fixture
def factory(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        session.execute(text("TRUNCATE users, leagues RESTART IDENTITY CASCADE"))
        session.commit()
    yield scoring_factory


@pytest.fixture
def app(factory: sessionmaker[Session]) -> Iterator[FastAPI]:
    built = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    built.dependency_overrides[get_session] = override
    built.dependency_overrides[get_settings] = lambda: settings_for()
    yield built
    built.dependency_overrides.clear()


SignIn = Callable[[str], TestClient]


@pytest.fixture
def sign_in(app: FastAPI, factory: sessionmaker[Session]) -> Iterator[SignIn]:
    opened: list[TestClient] = []

    def make(email: str) -> TestClient:
        with factory() as session:
            user = accounts.get_or_create_user(session, email)
            token = accounts.start_session(session, user.id)
            session.commit()
        client = TestClient(app)
        client.cookies.set(access.COOKIE, token)
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.close()


def _member(session: Session, email: str, espn_league_id: int = LEAGUE_ID) -> tuple[int, int]:
    """A member of a league; his user id and the league's row id."""
    user = accounts.get_or_create_user(session, email)
    league = memberships.get_or_create_league(session, espn_league_id)
    memberships.join_league(session, user.id, league.id, "member")
    session.commit()
    return user.id, league.id


def _change(kind: str, *, mine: bool = False, opponent: bool = False) -> Change:
    return Change(
        at=NOW,
        kind=kind,
        players=(Person(espn_player_id=1, name="A Player"),),
        teams=(),
        text=f"something {kind}",
        mine=mine,
        opponent=opponent,
    )


# ---------------------------------------------------------------------------
# the defaults, and what they cost a new member


def test_a_new_member_gets_the_busy_sections_off_and_the_rest_on(
    factory: sessionmaker[Session],
) -> None:
    """The league-wide transaction and injury feeds are what would make a new
    member unsubscribe on his second morning, so he opts in to them."""
    with factory() as session:
        user_id, league_id = _member(session, "member@example.com")
        held = subscriptions.for_member(session, user_id, league_id)

    assert held.chosen == (
        subscriptions.LINEUP,
        subscriptions.MOVES,
        subscriptions.MY_TEAM,
        subscriptions.OPPONENT,
        subscriptions.TRADES,
        subscriptions.STANDINGS,
    )
    assert held.on(subscriptions.LEAGUE_TRANSACTIONS) is False
    assert held.on(subscriptions.LEAGUE_INJURIES) is False
    assert held.morning is True and held.alerts is True
    assert held.silent is False


def test_reading_a_subscription_writes_no_row(factory: sessionmaker[Session]) -> None:
    """Nothing had to be backfilled, because a member happy with the defaults
    never needs a row."""
    from app.db.models import DigestSubscription

    with factory() as session:
        user_id, league_id = _member(session, "member@example.com")
        subscriptions.for_member(session, user_id, league_id)
        session.commit()
        assert session.query(DigestSubscription).count() == 0


def test_a_new_member_gets_the_compact_email(factory: sessionmaker[Session]) -> None:
    """Compact by default, full as an option (docs/jobs.md, "The two
    forms"), and no row is written to say so."""
    with factory() as session:
        user_id, league_id = _member(session, "member@example.com")
        held = subscriptions.for_member(session, user_id, league_id)

    assert held.length == subscriptions.COMPACT
    assert held.compact is True


def test_a_length_this_version_does_not_have_reads_as_compact() -> None:
    """A row from a version that never had this column, and a value nobody
    recognises, both land on the default rather than on the long form."""
    assert subscriptions.clean_length("full") == subscriptions.FULL
    assert subscriptions.clean_length("FULL") == subscriptions.FULL
    for nonsense in (None, "", "medium", 7):
        assert subscriptions.clean_length(nonsense) == subscriptions.COMPACT, nonsense
    assert subscriptions.Subscription(length="medium").compact is True


def test_the_length_he_chose_is_kept_and_read_back(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        user_id, league_id = _member(session, "member@example.com")
        subscriptions.save(session, user_id, league_id, length=subscriptions.FULL)
        session.commit()
        held = subscriptions.for_member(session, user_id, league_id)

    assert held.length == subscriptions.FULL and held.compact is False


def test_everything_is_every_topic_and_still_a_choice_of_length() -> None:
    """Single mode keeps every topic on; how long the message is stays a
    setting, because compact leaves nothing out that a count and a link do
    not cover."""
    assert subscriptions.everything().compact is True
    assert subscriptions.everything(subscriptions.FULL).compact is False
    assert subscriptions.everything(subscriptions.FULL).chosen == subscriptions.TOPICS


def test_an_unknown_topic_is_dropped_and_a_missing_one_takes_its_default() -> None:
    """A row written by an older version still reads, and a body from a page
    that is ahead of the server cannot write a topic that does not exist."""
    read = subscriptions.clean({"lineup": False, "quidditch": True})

    assert "quidditch" not in read
    assert read[subscriptions.LINEUP] is False
    assert read[subscriptions.MOVES] is True, "not given: its default"
    assert set(read) == set(subscriptions.TOPICS)


# ---------------------------------------------------------------------------
# the filter over the feed


def test_a_topic_switched_off_keeps_its_lines_out_of_the_feed() -> None:
    mine = _change(feed.DROP, mine=True)
    theirs = _change(feed.CLAIM, opponent=True)
    anyones = _change(feed.ADD)
    hurt = _change(feed.STATUS)
    traded = _change(feed.TRADE)
    every = [mine, theirs, anyones, hurt, traded]

    default = subscriptions.Subscription()
    assert default.filtered(every) == [mine, theirs, traded], "the busy feeds are off"

    everything = subscriptions.everything()
    assert everything.filtered(every) == every

    only_mine = subscriptions.Subscription(
        topics={**dict.fromkeys(subscriptions.TOPICS, False), subscriptions.MY_TEAM: True}
    )
    assert only_mine.filtered(every) == [mine]


def test_a_line_two_topics_both_admit_appears_once() -> None:
    """The topics overlap on purpose: a drop on your own roster is your news
    and the league's, and a reader who wants either should see it once."""
    mine = _change(feed.DROP, mine=True)
    both = subscriptions.Subscription(
        topics={
            **dict(subscriptions.DEFAULTS),
            subscriptions.LEAGUE_TRANSACTIONS: True,
        }
    )
    assert both.filtered([mine]) == [mine]


def test_every_topic_off_is_a_state_the_job_can_see() -> None:
    silent = subscriptions.Subscription(topics=dict.fromkeys(subscriptions.TOPICS, False))
    assert silent.silent is True
    assert silent.chosen == ()
    assert silent.filtered([_change(feed.TRADE, mine=True)]) == []


# ---------------------------------------------------------------------------
# the routes


def test_the_page_reads_every_league_he_is_in_and_writes_what_he_ticks(
    sign_in: SignIn, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        _member(session, "member@example.com")
    member = sign_in("member@example.com")

    listed = member.get("/me/subscriptions")
    assert listed.status_code == 200
    [league] = listed.json()["leagues"]
    assert league["espn_league_id"] == LEAGUE_ID
    assert [t["name"] for t in league["topics"]] == list(subscriptions.TOPICS)
    assert [t["on"] for t in league["topics"]] == [
        subscriptions.DEFAULTS[t] for t in subscriptions.TOPICS
    ]
    assert league["silent"] is False

    written = member.put(
        f"/me/subscriptions/{LEAGUE_ID}",
        json={
            "topics": {subscriptions.LINEUP: True, subscriptions.LEAGUE_TRANSACTIONS: True},
            "morning": True,
            "alerts": False,
        },
    )
    assert written.status_code == 200, written.text
    got = {t["name"]: t["on"] for t in written.json()["topics"]}
    assert got[subscriptions.LEAGUE_TRANSACTIONS] is True
    assert got[subscriptions.MOVES] is True, "left out of the body: its default"
    assert written.json()["alerts"] is False

    again = member.get("/me/subscriptions").json()["leagues"][0]
    assert again["alerts"] is False and again["morning"] is True


def test_the_page_offers_the_two_lengths_and_writes_the_one_he_picks(
    sign_in: SignIn, factory: sessionmaker[Session]
) -> None:
    """The setting beside the topics: the topics say what is in the email,
    this says how much of each. Compact is named as the default, so nobody
    has to guess what doing nothing gets him."""
    with factory() as session:
        _member(session, "member@example.com")
    member = sign_in("member@example.com")

    [league] = member.get("/me/subscriptions").json()["leagues"]
    assert league["length"] == subscriptions.COMPACT
    assert [option["name"] for option in league["lengths"]] == list(subscriptions.LENGTHS)
    assert [option["on"] for option in league["lengths"]] == [True, False]
    assert "(default)" in league["lengths"][0]["label"]

    written = member.put(
        f"/me/subscriptions/{LEAGUE_ID}",
        json={"topics": dict(subscriptions.DEFAULTS), "length": subscriptions.FULL},
    )
    assert written.status_code == 200, written.text
    assert written.json()["length"] == subscriptions.FULL

    nonsense = member.put(
        f"/me/subscriptions/{LEAGUE_ID}",
        json={"topics": dict(subscriptions.DEFAULTS), "length": "medium"},
    )
    assert nonsense.status_code == 200
    assert nonsense.json()["length"] == subscriptions.COMPACT, "an unknown length is the default"


def test_every_topic_off_is_answered_as_silent(
    sign_in: SignIn, factory: sessionmaker[Session]
) -> None:
    """So the page can say it plainly rather than leave him guessing why
    nothing arrives."""
    with factory() as session:
        _member(session, "member@example.com")
    member = sign_in("member@example.com")

    written = member.put(
        f"/me/subscriptions/{LEAGUE_ID}",
        json={"topics": dict.fromkeys(subscriptions.TOPICS, False)},
    )

    assert written.status_code == 200 and written.json()["silent"] is True


def test_a_league_that_is_not_his_is_a_404(sign_in: SignIn, factory: sessionmaker[Session]) -> None:
    with factory() as session:
        _member(session, "member@example.com")
        memberships.get_or_create_league(session, 999111)
        session.commit()
    member = sign_in("member@example.com")

    refused = member.put("/me/subscriptions/999111", json={"topics": {}})

    assert refused.status_code == 404
    listed = [row["espn_league_id"] for row in member.get("/me/subscriptions").json()["leagues"]]
    assert listed == [LEAGUE_ID], "and it is not listed either"


def test_signed_out_is_refused(app: FastAPI) -> None:
    with TestClient(app) as anon:
        assert anon.get("/me/subscriptions").status_code == 401
        assert anon.put(f"/me/subscriptions/{LEAGUE_ID}", json={"topics": {}}).status_code == 401
