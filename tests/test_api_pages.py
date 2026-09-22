"""The in-season pages, the route they need, and the two fixes behind them.

Four things are checked here, over one seeded league:

* the week and season pages are served at their addresses under the shell
  (docs/site.md), and so are the stylesheet and the two scripts they load,
  while anything else under `/pages/static/` is a 404 (tests/test_shell.py
  has the rest of the site);
* `pages/context` carries what a page cannot get from the two reports -- the
  day as a date, the days of its matchup period, every name, and which team
  is ours;
* the wire falls back to the historical definition on a season the listener
  never ran for, and a caller who names its own pool never reaches it, which
  is what keeps `scripts/pickups_backtest.py`'s numbers where they were;
* FAAB counts what was spent by the day being reported on and not a dollar
  more, and never goes negative.

Two seasons are built. `SEASON` is a season the listener ran for: snapshots,
a wire, and a schedule. `PLAYED` is a season already in the books -- lineup
days and box scores, and not one snapshot -- which is what every season but
the current one looks like, and what the fallback exists for.
"""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.api.pages import MANAGER_TEAM
from app.db.models import LeagueSeason, Player
from app.main import create_app
from app.pickups.bids import clear_cache
from app.pickups.state import (
    has_free_agent_snapshots,
    historical_free_agents,
    load_free_agents,
    load_team_week,
)
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    played,
    projected,
    snapshot,
    winning_bid,
)
from tests.scoring_db import LEAGUE_ID, held, league_season, matchup, player

#: The season the listener ran for, and one already played.
SEASON = 2026
PLAYED = 2025

#: ESPN team ids, in the order `scoring_db.league_season` hands them out.
OURS, RIVAL, SPENDER, OVERSPENT = 1, 2, 3, 4

EVERY_DAY = list(range(1, 15))

STARTER = {
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
}


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in STARTER.items()}


def scaled_totals(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in POSTED.items()}


@pytest.fixture(scope="module")
def seeded(scoring_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    with scoring_factory() as session:
        clear_schedule(session)

        # The listened season. Our team is named what the pages call ours, so
        # the index's ordering is exercised rather than asserted in a vacuum.
        listened, teams, (first, _later) = league_season(
            session,
            season=SEASON,
            team_names=(MANAGER_TEAM, "Rival", "Spender", "Overspent"),
            days_per_period=7,
        )
        ours, rival, spender, overspent = teams
        configure(listened, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        matchup(session, first, ours, rival, {ours: POSTED, rival: scaled_totals(0.8)})
        for name, factor, team in (
            ("Ace", 1.0, ours),
            ("Body", 1.0, ours),
            ("Cole", 1.0, ours),
            ("Waif", 0.5, ours),
            ("Foe", 1.0, rival),
        ):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=10, on_team_id=team.espn_team_id, season=SEASON)
            projected(session, who, 70, scaled(factor), season=SEASON)
            held(session, team, first, who, 1, season=SEASON)
        for name, factor, pro_team in (("Prize", 1.4, 20), ("Dreg", 0.2, 21)):
            who = player(session, name)
            eligible(session, who, ANY, "PG")
            snapshot(session, who, pro_team_id=pro_team, on_team_id=0, season=SEASON)
            projected(session, who, 70, scaled(factor), season=SEASON)
            on_the_wire(session, listened, who)
        for pro_team in (10, 20, 21):
            games(session, pro_team, EVERY_DAY, season=SEASON)

        # The money. Two teams nobody reports on, so the bids cannot move a
        # report's numbers: one spends across both periods, the other spends
        # more than the pot holds, which is what 2026 really looks like.
        bid_target = player(session, "Prize")
        winning_bid(session, spender, 2, 10, bid_target)
        winning_bid(session, spender, 9, 25, bid_target)
        winning_bid(session, overspent, 2, 60, bid_target)
        winning_bid(session, overspent, 3, 43, bid_target)

        # The played season: lineup days and box scores, and no snapshots and
        # no wire at all. `Held` was in a lineup that day; `Loose` and `Spare`
        # played and nobody held them, so they are that day's historical wire;
        # `Benched` did not play, so the definition cannot see him.
        history, (mine, theirs), (early, _) = league_season(
            session, season=PLAYED, team_names=("Ours Then", "Theirs Then"), days_per_period=7
        )
        configure(history, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        matchup(session, early, mine, theirs, {mine: POSTED, theirs: scaled_totals(0.9)})
        for name in ("Held", "Kept", "Third"):
            who = player(session, name)
            eligible(session, who, ANY, "PG", season=PLAYED)
            projected(session, who, 70, scaled(1.0), season=PLAYED)
            held(session, mine, early, who, 3, stats=scaled(1.0), season=PLAYED)
        for name, factor in (("Loose", 1.2), ("Spare", 0.9)):
            who = player(session, name)
            eligible(session, who, ANY, "PG", season=PLAYED)
            projected(session, who, 70, scaled(factor), season=PLAYED)
            played(session, who, 3, 28.0, scaled(factor), season=PLAYED)
        benched = player(session, "Benched")
        eligible(session, benched, ANY, "PG", season=PLAYED)
        projected(session, benched, 70, scaled(1.0), season=PLAYED)
        for pro_team in (10, 20, 21):
            games(session, pro_team, EVERY_DAY, season=PLAYED)
        session.commit()
    clear_cache()
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


def stored(session: Session, season: int) -> LeagueSeason:
    found = session.scalar(select(LeagueSeason).where(LeagueSeason.season == season))
    assert found is not None
    return found


# --------------------------------------------------------------------------
# The three pages, and the files they load


@pytest.mark.parametrize(
    ("path", "wanted"),
    [
        (f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week", "The tale of the tape"),
        (f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/season", "Drop candidates"),
        (f"/l/{LEAGUE_ID}/{SEASON}/standings", "The table"),
    ],
)
def test_each_page_is_served(client: TestClient, path: str, wanted: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert wanted in response.text
    assert "/pages/static/pages.css" in response.text
    assert "/pages/static/pages.js" in response.text
    assert "/pages/static/shell.js" in response.text


def test_a_page_never_tells_anyone_what_to_do(client: TestClient) -> None:
    """The language rule: ideas, not instructions (docs/pickups.md section 4).

    The two phrases are written once, in the shared script, and no reader is
    ever told a move is "recommended" -- the word survives only as the name
    of a field in the report's own JSON, which is why the markup is checked
    with the scripts stripped out of it.
    """
    script = client.get("/pages/static/pages.js").text
    assert '"Worth a look"' in script
    assert '"Nothing clears the bar"' in script

    for which in ("week", "season"):
        page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/{which}").text
        visible = re.sub(r"<script.*?</script>", "", page, flags=re.S).lower()
        assert "recommend" not in visible
        assert "you should" not in visible


def test_the_week_page_draws_todays_lineup_above_the_week(client: TestClient) -> None:
    """The Today section, and the route it reads.

    The markup is checked for the section and the script for the fetch and
    the player-card hook, because the page draws itself in the browser and
    there is nothing else here to assert it against; the route it calls is
    then asked the same question and has to answer the same day. A place a
    man with a game could take is wired to the warn style, which is the one
    thing on this page stated as a mistake.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text

    assert page.index("today-section") < page.index("tape-section"), "above the week"
    assert 'id="today-fix"' in page and 'class="warnline" id="today-fix"' in page
    assert "/today${params(WHERE)}" in page, "the day's own route, with the same ?today="
    assert 'class="player" data-espn-id=' in page, "the shared player card's hook"
    assert "As we would set it" in page and "As it is set" in page

    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/{OURS}/today", params={"today": 1}
    ).json()
    assert body["today"] == 1
    assert [seat["slot"] for seat in body["lineup"]] == ["G", "F", "UT"]
    assert body["source_note"], "the page's footnote says where the numbers came from"


def test_the_lineup_grid_is_styled_in_both_themes_and_at_phone_width(
    client: TestClient,
) -> None:
    """No colour outside the token block, and nothing new that only works
    in one theme: the grid is the house's plain table with widths on it."""
    css = client.get("/pages/static/pages.css").text

    assert ".grid.lineup" in css and ".player{" in css
    lineup = css.split("/* ---- the day's lineup")[1].split("/* ---- the schedule strip")[0]
    assert not re.search(r"#[0-9a-fA-F]{3}", lineup), "no colour outside the token block"
    assert "var(--accent)" in lineup, "the man with a game carries the accent"


def test_the_shared_stylesheet_and_script_are_served(client: TestClient) -> None:
    css = client.get("/pages/static/pages.css")
    js = client.get("/pages/static/pages.js")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "--accent" in css.text, "the palette is the draft screen's tokens"
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")
    assert '"FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"' in js.text


def test_only_the_named_assets_are_served(client: TestClient) -> None:
    """A fixed set, so no request can ask for a file outside the folder."""
    assert client.get("/pages/static/week.html").status_code == 404
    assert client.get("/pages/static/nothing.css").status_code == 404
    assert client.get("/pages/static/league-week.html").status_code == 404


# --------------------------------------------------------------------------
# The context route


def test_the_context_route_carries_what_the_reports_do_not(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context?today=3").json()

    assert body["league_id"] == LEAGUE_ID
    assert body["season"] == SEASON
    assert body["today"] == 3
    assert body["today_date"] == "2025-10-23", "day 3 of a season opening on the 21st"
    assert body["first_scoring_period"] == 1
    assert body["last_scoring_period"] == 14
    assert body["source_note"].startswith("ESPN's projections")
    assert {team["name"] for team in body["teams"]} == {
        MANAGER_TEAM,
        "Rival",
        "Spender",
        "Overspent",
    }


def test_the_context_route_puts_our_team_first(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context").json()

    assert body["teams"][0]["name"] == MANAGER_TEAM
    assert body["teams"][0]["ours"] is True
    assert body["our_espn_team_id"] == OURS
    assert [team["ours"] for team in body["teams"][1:]] == [False, False, False]


def test_another_manager_names_his_own_team(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context?me=rival").json()

    assert body["our_espn_team_id"] == RIVAL
    assert body["teams"][0]["name"] == "Rival", "matched whatever the case"


def test_a_season_nobody_of_ours_played_in_has_no_team_of_ours(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{PLAYED}/pages/context").json()

    assert body["our_espn_team_id"] is None
    assert all(team["ours"] is False for team in body["teams"])


def test_the_context_route_carries_the_schedule_strip(client: TestClient) -> None:
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context?today=3").json()

    period = body["period"]
    assert period["period"] == 1
    assert (period["first_scoring_period"], period["final_scoring_period"]) == (1, 7)
    assert [day["scoring_period"] for day in period["days"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [day["played"] for day in period["days"]] == [True, True, *[False] * 5]
    assert [day["today"] for day in period["days"]] == [False, False, True, *[False] * 4]
    assert period["days"][0]["calendar_date"] == "2025-10-21"


def test_the_context_route_refuses_only_a_season_it_does_not_hold(client: TestClient) -> None:
    assert client.get(f"/leagues/{LEAGUE_ID}/seasons/1999/pages/context").status_code == 404


# --------------------------------------------------------------------------
# The wire on a season the listener never ran for


def test_a_played_season_has_no_snapshots_and_the_current_one_does(session: Session) -> None:
    assert has_free_agent_snapshots(session, stored(session, SEASON)) is True
    assert has_free_agent_snapshots(session, stored(session, PLAYED)) is False


def test_the_historical_wire_is_who_played_and_was_not_held(session: Session) -> None:
    """`scripts/pickups_backtest.py`'s definition, and only that.

    `Held`, `Kept` and `Third` were in a lineup that day, so they are not on
    it. `Benched` never played, so the definition cannot see him at all --
    the weakness the report has to admit to.
    """
    found = historical_free_agents(session, stored(session, PLAYED), 3)

    assert _names(session, found) == {"Loose", "Spare"}


def test_the_historical_wire_is_empty_on_a_day_nobody_played(session: Session) -> None:
    assert historical_free_agents(session, stored(session, PLAYED), 9) == set()


def test_load_free_agents_falls_back_when_a_season_has_no_snapshots(session: Session) -> None:
    history = stored(session, PLAYED)
    week = load_team_week(session, history, 1, 3)

    wire = load_free_agents(session, history, week)

    assert _names(session, {p.player_id for p in wire}) == {"Loose", "Spare"}
    assert all(found.waiver_clears_at is None for found in wire), (
        "no snapshot ever said who was on waivers"
    )


def test_a_named_pool_never_reaches_the_fallback(session: Session) -> None:
    """What keeps the backtest's numbers where they were: it passes `pool=`."""
    history = stored(session, PLAYED)
    week = load_team_week(session, history, 1, 3)
    only = next(iter(historical_free_agents(session, history, 3)))

    wire = load_free_agents(session, history, week, player_ids=[only])

    assert [found.player_id for found in wire] == [only]


def test_the_listened_season_still_reads_the_snapshots(session: Session) -> None:
    listened = stored(session, SEASON)
    week = load_team_week(session, listened, OURS, 1)

    wire = load_free_agents(session, listened, week)

    assert _names(session, {p.player_id for p in wire}) == {"Prize", "Dreg"}


def test_the_reports_say_which_wire_they_looked_at(client: TestClient) -> None:
    listened = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/{OURS}/pickups/stream?today=1"
    ).json()
    history = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{PLAYED}/teams/1/pickups/stream?today=3"
    ).json()

    assert listened["historical_wire"] is False
    assert listened["pool_size"] == 2
    assert history["historical_wire"] is True, "a page can say so rather than show an empty wire"


def test_a_played_season_is_reported_on_rather_than_refused(client: TestClient) -> None:
    """The guard used to demand snapshots, which refused every played season."""
    response = client.get(f"/leagues/{LEAGUE_ID}/seasons/{PLAYED}/teams/1/pickups/season?today=3")

    assert response.status_code == 200
    assert response.json()["historical_wire"] is True


# --------------------------------------------------------------------------
# FAAB


def test_faab_counts_only_what_had_been_spent_by_that_day(session: Session) -> None:
    """A report about a past day must not charge money spent after it.

    `Spender` bid $10 on day 2 and $25 on day 9. On day 3 he has spent $10,
    and the $25 is still in his pocket; the unfiltered sum charged him both.
    """
    listened = stored(session, SEASON)

    assert load_team_week(session, listened, SPENDER, 3).faab_remaining == 90
    assert load_team_week(session, listened, SPENDER, 8).faab_remaining == 90
    assert load_team_week(session, listened, SPENDER, 10).faab_remaining == 65


def test_faab_is_never_negative_and_the_overshoot_is_carried(session: Session) -> None:
    """ESPN's own ledger caps at the budget; our sum of its bid feed does not.

    `Overspent` bid $60 and $43 against a $100 pot, which is what one real
    2026 team's executed claims add up to. The pot reads empty and the $3
    rides alongside, so a page can say the arithmetic disagreed instead of
    printing a pot nobody could bid from.
    """
    listened = stored(session, SEASON)

    early = load_team_week(session, listened, OVERSPENT, 2)
    after = load_team_week(session, listened, OVERSPENT, 4)

    assert (early.faab_remaining, early.faab_overspent) == (40, 0)
    assert (after.faab_remaining, after.faab_overspent) == (0, 3)


def test_an_untouched_pot_is_the_whole_budget(session: Session) -> None:
    week = load_team_week(session, stored(session, SEASON), OURS, 1)

    assert (week.faab_remaining, week.faab_overspent) == (100, 0)


def test_the_reports_carry_the_pot_and_the_overshoot(client: TestClient) -> None:
    url = f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams"
    ours = client.get(f"{url}/{OURS}/pickups/stream?today=1").json()
    over = client.get(f"{url}/{OVERSPENT}/pickups/season?today=4").json()

    assert (ours["faab_remaining"], ours["faab_overspent"]) == (100, 0)
    assert (over["faab_remaining"], over["faab_overspent"]) == (0, 3)
    assert over["faab_remaining"] >= 0, "a page is never handed a negative pot"


def _names(session: Session, player_ids: set[int]) -> set[str]:
    """The names behind a set of player ids, so a pool reads as people."""
    return {
        str(name)
        for name in session.scalars(
            select(Player.name).where(Player.id.in_(sorted(player_ids)))
        ).all()
    }
