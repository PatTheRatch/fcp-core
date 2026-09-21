"""The two trade routes, on a league small enough to check by hand.

One small season for the module: Home with four men on a five-place roster,
Away with six, everybody playing every day, and a wire with one man on it.
Away is deliberately over-rostered, because that is the only shape in which a
deal cannot be made to fit at all -- with equal roster sizes there is always
somebody to drop, which is why the report's default is to drop him rather
than to refuse.

What is pinned here: that the report route answers with the engine's own
numbers and not a second computation; that every refusal is a sentence a
manager can act on; that a season with no schedule is answered rather than
refused; that a roster read as of a day is that day's and never a later
one's; and that the published record travels with every answer, so nothing
that draws it has to keep a copy.
"""

from collections.abc import Iterator, Mapping

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api import trades as routes
from app.api.deps import get_session
from app.db.models import LeagueSeason, Player, Team
from app.main import create_app
from app.pickups.bids import clear_cache
from app.pickups.projection import clear_cache as clear_lines
from app.trades import CALIBRATION_NOTE, TeamOffer, evaluate_trade
from tests.pickups_db import (
    ANY,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

SEASON = 2026
#: A season with settings and teams and nothing else: the shape 2027 is in
#: before its draft, which is an answer rather than a refusal.
UNDRAFTED = 2027

HOME, AWAY, NOBODY = 1, 2, 99

#: Four seven-day periods, the last of them the playoffs.
PERIODS = 4
REGULAR = 3
SEASON_DAYS = list(range(1, PERIODS * 7 + 1))

#: The day every case is judged on: the first day of the second period.
TODAY = 8
#: A day after it on which Home holds one more man, for the look-ahead test.
LATER = 9

STARTER: Mapping[str, float] = {
    "PTS": 20.0,
    "REB": 8.0,
    "AST": 4.0,
    "STL": 1.2,
    "BLK": 0.8,
    "3PM": 2.0,
    "TO": 2.4,
    "FGM": 8.0,
    "FGA": 17.0,
    "FTM": 4.0,
    "FTA": 5.0,
}

#: What the two sides posted in the period already played. They are the only
#: evidence `category_distributions` has for this league, so they are also the
#: spread every category's win probability is measured against: level totals
#: would leave no spread at all and every man worth nothing.
POSTED = {
    "PTS": 500.0,
    "REB": 200.0,
    "AST": 100.0,
    "STL": 30.0,
    "BLK": 20.0,
    "3PM": 50.0,
    "TO": 60.0,
    "FGM": 235.0,
    "FGA": 500.0,
    "FTM": 78.0,
    "FTA": 100.0,
    "FG%": 235 / 500,
    "FT%": 0.78,
}


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in STARTER.items()}


def posted(factor: float) -> dict[str, float]:
    """One side's week, `factor` of the other's. The rates are rebuilt from
    the made and attempted under them rather than scaled, which would be a
    percentage that no line adds up to."""
    out = {key: value * factor for key, value in POSTED.items() if not key.endswith("%")}
    out["FG%"] = out["FGM"] / out["FGA"]
    out["FT%"] = out["FTM"] / out["FTA"]
    return out


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        clear_schedule(session)
        ls, (home, away), periods = league_season(
            session,
            season=SEASON,
            periods=PERIODS,
            regular_season_periods=REGULAR,
            days_per_period=7,
        )
        configure(ls, lineup={"G": 1, "F": 1, "UT": 1}, bench=2, injured_reserve=1)
        matchup(session, periods[0], home, away, {home: posted(1.0), away: posted(0.8)})
        matchup(session, periods[1], home, away)
        games(session, 10, SEASON_DAYS)
        games(session, 20, SEASON_DAYS)

        def rostered(
            team: Team, name: str, per_game: Mapping[str, float], days: tuple[int, ...]
        ) -> Player:
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(
                session,
                who,
                pro_team_id=10 if team is home else 20,
                on_team_id=int(team.espn_team_id),
            )
            projected(session, who, 70, per_game, season=SEASON)
            for day in days:
                held(session, team, periods[1], who, day, season=SEASON)
            return who

        held_days = (1, TODAY, LATER)
        for name in ("HomeA", "HomeB", "HomeC"):
            rostered(home, name, STARTER, held_days)
        rostered(home, "HomeWeak", scaled(0.4), held_days)
        # Held on the later day alone: a roster read as of TODAY must not see
        # him, which is the whole of the no-look-ahead claim for the pickers.
        rostered(home, "HomeLater", STARTER, (LATER,))
        for name in ("AwayA", "AwayB", "AwayC", "AwayD"):
            rostered(away, name, STARTER, held_days)
        rostered(away, "AwayWeak", scaled(0.2), held_days)
        rostered(away, "Star", scaled(1.6), held_days)

        wire = player(session, "Wire")
        eligible(session, wire, ANY, "PG")
        snapshot(session, wire, pro_team_id=20, on_team_id=0)
        projected(session, wire, 70, scaled(0.9), season=SEASON)
        on_the_wire(session, ls, wire)

        league_season(session, season=UNDRAFTED, periods=1, days_per_period=7)
        session.commit()
    clear_cache()
    clear_lines()
    yield scoring_factory


@pytest.fixture
def client(seeded: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app()

    def override() -> Iterator[Session]:
        with seeded() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def session(seeded: sessionmaker[Session]) -> Iterator[Session]:
    with seeded() as open_session:
        yield open_session


def url(which: str, team: int = HOME, season: int = SEASON) -> str:
    return f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/trades/{which}"


def ids(session: Session, *names: str) -> tuple[int, ...]:
    """This database's own player ids, for calling the engine directly."""
    return tuple(
        session.scalars(select(Player.id).where(Player.name == name)).one() for name in names
    )


def espn(session: Session, *names: str) -> tuple[int, ...]:
    """ESPN's player ids, which is what the routes speak."""
    return tuple(
        session.scalars(select(Player.espn_player_id).where(Player.name == name)).one()
        for name in names
    )


def stored_season(session: Session, season: int) -> LeagueSeason:
    found = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# the rosters the pickers are drawn from
# ---------------------------------------------------------------------------


def test_the_rosters_route_lists_both_sides_with_the_room_each_has(client: TestClient) -> None:
    body = client.get(url("rosters"), params={"with_team": AWAY, "today": TODAY}).json()

    assert body["today"] == TODAY
    assert body["readiness"]["ready"] is True
    assert [team["espn_team_id"] for team in body["teams"]] == [HOME, AWAY], "ours first"
    ours, theirs = body["teams"]
    assert ours["ours"] is True and theirs["ours"] is False
    assert [man["name"] for man in ours["players"]] == ["HomeA", "HomeB", "HomeC", "HomeWeak"]
    assert ours["open_slots"] == 1, "four men on a five-place roster"
    assert theirs["open_slots"] == 0, "six men on the same five places"
    man = ours["players"][0]
    assert man["espn_player_id"] > 0, "players go out as ESPN ids, as the pickup routes do"
    assert man["position"] == "PG" and man["pro_team_id"] == 10
    assert man["on_ir"] is False


def test_the_rosters_route_answers_with_ours_alone_when_no_other_team_is_named(
    client: TestClient,
) -> None:
    body = client.get(url("rosters"), params={"today": TODAY}).json()

    assert [team["espn_team_id"] for team in body["teams"]] == [HOME]
    assert body["calibration_note"] == CALIBRATION_NOTE


def test_a_roster_as_of_a_day_is_that_days_and_never_a_later_ones(client: TestClient) -> None:
    """The one claim a page cannot make for itself.

    `HomeLater` is in Home's lineup on day 9 and on no day before it. A
    builder opened on day 8 must not offer him, and the same route asked for
    day 9 must -- otherwise the check would be passing because nothing was
    read at all.
    """
    on_the_day = client.get(url("rosters"), params={"today": TODAY}).json()
    afterwards = client.get(url("rosters"), params={"today": LATER}).json()

    assert "HomeLater" not in {man["name"] for man in on_the_day["teams"][0]["players"]}
    assert "HomeLater" in {man["name"] for man in afterwards["teams"][0]["players"]}


def test_a_season_with_nothing_to_judge_from_says_so_rather_than_refusing(
    client: TestClient,
) -> None:
    """2027 before its draft: no schedule, no rosters, and no error either."""
    for which, params in (("rosters", {}), ("report", {"with_team": AWAY, "give": 1})):
        answer = client.get(url(which, season=UNDRAFTED), params=params)
        assert answer.status_code == 200, which
        body = answer.json()
        assert body["readiness"]["ready"] is False
        note = body["readiness"]["note"]
        assert f"season {UNDRAFTED} has nothing to judge a trade from yet" in note
        assert "no NBA schedule is stored" in note
        assert body["calibration_note"] == CALIBRATION_NOTE
    assert client.get(url("rosters", season=UNDRAFTED)).json()["teams"] == []
    assert (
        client.get(url("report", season=UNDRAFTED), params={"with_team": AWAY, "give": 1}).json()[
            "trade"
        ]
        is None
    )


# ---------------------------------------------------------------------------
# the report: the engine's own numbers
# ---------------------------------------------------------------------------


def test_the_report_route_is_the_engines_payload_and_not_a_second_computation(
    client: TestClient, session: Session
) -> None:
    """Every number on the page comes from `evaluate_trade` and nowhere else."""
    ls = stored_season(session, SEASON)
    (star,) = ids(session, "Star")
    (home_c,) = ids(session, "HomeC")
    built = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (home_c,)),
        TeamOffer(AWAY, (star,)),
    )

    body = client.get(
        url("report"),
        params={
            "with_team": AWAY,
            "give": list(espn(session, "HomeC")),
            "get": list(espn(session, "Star")),
            "today": TODAY,
        },
    ).json()
    trade = body["trade"]

    assert body["readiness"]["ready"] is True
    assert body["calibration_note"] == CALIBRATION_NOTE
    assert (trade["season"], trade["today"]) == (built.season, built.today)
    assert trade["effective_day"] == built.effective_day
    assert trade["review_days"] == built.review_days
    assert trade["review_source"] == built.review_source
    assert trade["weeks_remaining"] == pytest.approx(built.weeks_remaining)
    assert trade["last_scoring_period"] == built.last_scoring_period
    assert trade["hurdle"] == built.hurdle
    assert trade["pool_size"] == built.pool_size
    assert trade["historical_wire"] == built.historical_wire
    assert trade["notes"] == list(built.notes)
    assert [side["espn_team_id"] for side in trade["sides"]] == [HOME, AWAY], "ours first"

    for sent in trade["sides"]:
        side = built.side(sent["espn_team_id"])
        assert sent["team_name"] == side.team_name
        assert sent["net"] == pytest.approx(side.net)
        assert sent["per_week"] == pytest.approx(side.per_week)
        assert sent["clears"] is side.clears
        assert sent["summary"] == side.summary
        assert sent["notes"] == list(side.notes)
        assert sent["season_independent"] == pytest.approx(side.season_independent)
        assert sent["expected_per_week"] == pytest.approx(side.expected_per_week)
        assert sent["replacement"] == pytest.approx(side.replacement)
        assert sent["drop_source"] == side.drop_source
        assert (sent["places_opened"], sent["places_used"]) == (
            side.places_opened,
            side.places_used,
        )
        judgement = sent["judgement"]
        assert judgement["delta_week"] == pytest.approx(side.judgement.delta_week)
        assert judgement["delta_season_per_week"] == pytest.approx(
            side.judgement.delta_season_per_week
        )
        assert judgement["delta_total"] == pytest.approx(side.judgement.delta_total)
        assert judgement["record_without"] == pytest.approx(list(side.judgement.record_without))
        assert judgement["record_with"] == pytest.approx(list(side.judgement.record_with))
        assert [view["abbreviation"] for view in sent["categories"]] == [
            view.abbreviation for view in side.categories
        ]
        for view, was in zip(sent["categories"], side.categories, strict=True):
            assert view["before"] == pytest.approx(was.before)
            assert view["after"] == pytest.approx(was.after)
            assert view["delta"] == pytest.approx(was.delta)
            assert view["p_delta"] == pytest.approx(was.p_delta)
            assert view["moved"] is was.moved
        assert sent["playoffs"]["delta_per_week"] == pytest.approx(side.playoffs.delta_per_week)
        assert sent["playoffs"]["measurable"] is side.playoffs.measurable
        for card, was in zip(
            [*sent["receives"], *sent["gives"], *sent["drops"]],
            [*side.receives, *side.gives, *side.drops],
            strict=True,
        ):
            assert card["name"] == was.name
            assert card["value"] == pytest.approx(was.value)
            assert (card["thin"], card["hurt"]) == (was.thin, was.hurt)
            assert card["games_so_far"] == was.games_so_far
            assert card["projection_source"] == was.projection_source


def test_an_uneven_deal_names_the_drop_it_chose_and_the_one_it_was_given(
    client: TestClient, session: Session
) -> None:
    two_for_one = {
        "with_team": AWAY,
        "give": list(espn(session, "HomeWeak", "HomeC")),
        "get": list(espn(session, "Star")),
        "today": TODAY,
    }
    ours = client.get(url("report"), params=two_for_one).json()["trade"]["sides"][0]
    assert ours["places_opened"] == 1
    assert ours["replacement_player"]["name"] == "Wire", "the best man still on the wire"

    # Three arriving for one leaving, against one open place: one man goes.
    one_for_three = {
        "with_team": AWAY,
        "give": list(espn(session, "HomeC")),
        "get": list(espn(session, "Star", "AwayA", "AwayB")),
        "today": TODAY,
    }
    chosen = client.get(url("report"), params=one_for_three).json()["trade"]["sides"][0]
    assert chosen["places_used"] == 1
    assert chosen["drop_source"] == "cheapest"
    assert [card["name"] for card in chosen["drops"]] == ["HomeWeak"]

    named = client.get(
        url("report"),
        params={**one_for_three, "drop": list(espn(session, "HomeB"))},
    ).json()["trade"]["sides"][0]
    assert named["drop_source"] == "named"
    assert [card["name"] for card in named["drops"]] == ["HomeB"]


# ---------------------------------------------------------------------------
# bad input: a sentence a manager can act on
# ---------------------------------------------------------------------------


def refused(client: TestClient, session: Session, **params: object) -> str:
    answer = client.get(url("report"), params={"today": TODAY, **params})
    assert answer.status_code == 422, answer.text
    detail = answer.json()["detail"]
    assert isinstance(detail, str), "one sentence, not pydantic's own list"
    return detail


def test_a_team_cannot_trade_with_itself(client: TestClient, session: Session) -> None:
    assert (
        refused(client, session, with_team=HOME, give=list(espn(session, "HomeC")))
        == routes.SELF_TRADE
    )


def test_the_other_side_has_to_be_named_and_has_to_exist(
    client: TestClient, session: Session
) -> None:
    assert refused(client, session, give=list(espn(session, "HomeC"))) == routes.NO_OTHER_TEAM
    assert refused(client, session, with_team=NOBODY, give=list(espn(session, "HomeC"))) == (
        f"There is no team {NOBODY} in this league's {SEASON} season."
    )


def test_a_deal_with_nobody_in_it_says_so(client: TestClient, session: Session) -> None:
    assert refused(client, session, with_team=AWAY) == routes.NOTHING_TRADED


def test_a_man_who_is_not_on_that_roster_is_named_in_the_refusal(
    client: TestClient, session: Session
) -> None:
    assert (
        refused(client, session, with_team=AWAY, give=list(espn(session, "Star")))
        == f"Home does not have Star on its roster on day {TODAY}."
    )
    assert (
        refused(client, session, with_team=AWAY, give=list(espn(session, "HomeC")), get=[123456789])
        == f"Away does not have player 123456789 on its roster on day {TODAY}."
    )


def test_a_man_cannot_be_on_both_sides_of_the_deal(client: TestClient, session: Session) -> None:
    (both,) = espn(session, "HomeC")
    assert refused(client, session, with_team=AWAY, give=[both], get=[both]) == (
        "HomeC is on both sides of this deal: name him once, as given or as got."
    )


def test_a_man_cannot_be_traded_away_and_dropped_at_once(
    client: TestClient, session: Session
) -> None:
    (gone,) = espn(session, "HomeC")
    assert (
        refused(
            client,
            session,
            with_team=AWAY,
            give=[gone],
            get=list(espn(session, "Star", "AwayA")),
            drop=[gone],
        )
        == "Home cannot both trade away and drop HomeC."
    )


def test_a_drop_the_team_does_not_hold_is_a_sentence(client: TestClient, session: Session) -> None:
    assert (
        refused(
            client,
            session,
            with_team=AWAY,
            give=list(espn(session, "HomeC")),
            get=list(espn(session, "Star", "AwayA")),
            drop=list(espn(session, "Star")),
        )
        == f"Home cannot drop Star: not on its active roster on day {TODAY}."
    )


def test_a_roster_that_cannot_hold_the_deal_says_who_it_could_still_drop(
    client: TestClient, session: Session
) -> None:
    """The guard, and the one thing a manager can do about it.

    Away holds six men to Home's five places, so a deal that sends all six
    across is one Home cannot make room for however much it drops. With equal
    roster sizes -- which is what ESPN gives every team -- there is always
    somebody to drop, and the report drops him rather than refusing.
    """
    everybody = list(espn(session, "AwayA", "AwayB", "AwayC", "AwayD", "AwayWeak", "Star"))
    short = refused(client, session, with_team=AWAY, get=everybody)
    assert short.startswith("Home needs 5 more roster place(s) for this deal and can free 4")
    assert "HomeA, HomeB, HomeC and HomeWeak" in short
    assert short.endswith("Give it one fewer player, or take one back.")

    nobody_left = refused(
        client,
        session,
        with_team=AWAY,
        give=list(espn(session, "HomeA", "HomeB", "HomeC", "HomeWeak")),
        get=everybody,
    )
    assert nobody_left == routes.NO_ROOM_AT_ALL.format(team="Home", arriving=6)
