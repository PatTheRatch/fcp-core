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

and, since the score went on the page (2026-09-23):

* the week report carries what both sides have posted so far, and the men
  behind it add up to it to the number, which is the invariant the by-man
  table under THE NINE rests on;
* the day's own report carries each man's stored line, and the league's
  lineups route carries the other side's, which is what Tonight prints.

Three seasons are built. `SEASON` is a season the listener ran for:
snapshots, a wire, and a schedule, and no box score at all, so every day of
it reads as live. `PLAYED` is a season already in the books -- lineup days
and box scores, and not one snapshot -- which is what every season but the
current one looks like, and what the wire fallback exists for. `SCORED` is
a matchup period caught in the middle, which is the only shape in which the
score and the men under it can be checked against each other.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.api.pages import MANAGER_TEAM
from app.api.schemas import StreamReportOut
from app.db.models import IngestRun, LeagueSeason, Player
from app.ingest_runs import SUCCEEDED
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
#: A third, built for one question only: a matchup period caught in the
#: middle, with box scores on the days behind `today` and one on a day after
#: it. That is what makes the live/replay rule choose the box scores, which
#: is the only case where the score and the men under it can be checked
#: against each other.
SCORED = 2024

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

        # The mid-period season. Days 1 and 2 are played and stored, day 4
        # carries a line so the rule reads day 3 as a replay, and ESPN's own
        # matchup row is deliberately a different number from the sum of
        # those lines -- which is how the page can be shown to be drawn from
        # the engine's posted-so-far and not from the matchup route.
        scored, (us_now, them_now), (running, _) = league_season(
            session, season=SCORED, team_names=("Us Now", "Them Now"), days_per_period=7
        )
        configure(scored, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
        matchup(session, running, us_now, them_now, {us_now: POSTED, them_now: POSTED})
        for name, factor, team in (("Nine", 1.0, us_now), ("Ten", 0.6, us_now)):
            who = player(session, name)
            eligible(session, who, ANY, "PG", season=SCORED)
            snapshot(session, who, pro_team_id=10, on_team_id=team.espn_team_id, season=SCORED)
            projected(session, who, 70, scaled(factor), season=SCORED)
            for day in (1, 2):
                held(session, team, running, who, day, stats=scaled(factor), season=SCORED)
            # Day 4 is held with no line for one of them, so the day's own
            # report shows a man with a box score beside a man without one.
            held(session, team, running, who, 4, season=SCORED)
        rival_man = player(session, "Eleven")
        eligible(session, rival_man, ANY, "PG", season=SCORED)
        snapshot(
            session,
            rival_man,
            pro_team_id=10,
            on_team_id=them_now.espn_team_id,
            season=SCORED,
        )
        projected(session, rival_man, 70, scaled(0.8), season=SCORED)
        held(session, them_now, running, rival_man, 1, stats=scaled(0.8), season=SCORED)
        played(session, player(session, "Nine"), 4, 30.0, scaled(1.0), season=SCORED)
        for pro_team in (10, 20, 21):
            games(session, pro_team, EVERY_DAY, season=SCORED)
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
        (f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week", "The read"),
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


def test_the_week_page_answers_before_it_explains(client: TestClient) -> None:
    """The order the page is read in: the matchup, then the nine, then
    tonight, then the moves, and the model under More.

    The markup is checked for the sections and the script for the fetches and
    the player-card hook, because the page draws itself in the browser and
    there is nothing else here to assert it against; the route it calls is
    then asked the same question and has to answer the same day. A place a
    man with a game could take is wired to the warn style, which is the one
    thing on this page stated as a mistake, and the comparison of the two
    lineups is still on the page, under a disclosure.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text

    order = ["pulse-section", "tonight-section", "read-section", "sched-section", "more-section"]
    assert [page.index(name) for name in order] == sorted(page.index(name) for name in order)
    assert 'id="today-fix"' in page and 'class="warnline" id="today-fix"' in page
    assert "/today${params(WHERE)}" in page, "the day's own route, with the same ?today="
    assert "cardName(" in page and "wireCards(" in page, "every name opens the shared card"
    assert "As we would set it" in page and "As it is set" in page
    assert page.count('<details class="disc"') >= 6, "the evidence is a tap away, not gone"
    assert "aria-expanded" in page, "and what is not a <details> says whether it is open"

    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/teams/{OURS}/today", params={"today": 1}
    ).json()
    assert body["today"] == 1
    assert [seat["slot"] for seat in body["lineup"]] == ["G", "F", "UT"]
    assert body["source_note"], "the page's footnote says where the numbers came from"


def test_the_week_page_keeps_everything_it_used_to_show(client: TestClient) -> None:
    """Nothing was removed, only layered (docs/in_season_pages.md).

    Every part of the old page has to still be reachable: the whole list of
    moves with its marks, the standing figures, the rest of the season, the
    projected record either way, and the calibration text verbatim.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text

    for wanted in (
        "Every move considered",
        "Where the season finishes",
        "With a move and without",
        "The week's standing figures",
        "How this is worked out",
        "Week by week",
        "Not playing",
    ):
        assert wanted in page, wanted
    assert "clears the bar" in page and "below the bar" in page, "a bar labels, never hides"
    assert "calibration_note" in page and "calibration_short" in page
    assert "BAND_YOURS" in page and "display choice" in page, "the bands are a choice about ink"


def test_the_week_page_shows_the_score_of_every_category(client: TestClient) -> None:
    """The thing the page did not have until 2026-09-23.

    Every cell of THE NINE prints the chance and, under it, the score as it
    stands; tapping one opens the score beside the projection and the
    chance; and the table under the bands breaks the score out a man at a
    time. All three read the report's own `posted`, never the matchup route,
    and the page says why.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text

    assert "function scoreCell(" in page, "the score under the chance"
    assert "report.posted" in page and "report.opponent_posted" in page
    assert "storedCat(" in page, "a rate as .459, a count whole"
    assert "INVERTED.has(cat) ? us < them : us > them" in page, (
        "the leading side takes the ink, read with the category's own direction"
    )
    assert 'id="by-man"' in page and "This week, by man" in page
    assert "So far" in page and "Projected" in page, "both, labelled, on a tapped category"
    assert "posted_men" in page and "opponent_posted_men" in page
    assert "BY_MAN_OPEN" in page, "open on a desk, closed on a phone, and it says why"
    assert "box_scores_as_of" in page, "how old a stored line is, on the page"


def test_the_week_page_prints_a_mans_line_once_his_game_is_stored(
    client: TestClient,
) -> None:
    """Tonight, on both sides.

    The nine and minutes, in the page's own fixed order, from a field of the
    same name on the day's report and on the stored lineup row, so ours and
    theirs cannot be written two different ways.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text

    assert "function boxLine(" in page and "boxRow(" in page
    assert "boxRow(player.line)" in page, "ours, from the day's own report"
    assert "boxRow(slot.played ? slot : null)" in page, "theirs, from the stored lineups"
    for unit in ("min", "fg", "ft", "3pm", "pts", "reb", "ast", "stl", "blk", "to"):
        assert f" {unit}`" in page or f"{unit}`," in page or f'{unit}"' in page, unit
    assert "A zero is kept" in page, "a zero is information"


def test_the_week_page_lets_a_manager_name_his_own_move(client: TestClient) -> None:
    """The What if form (docs/what_if.md section 7).

    It sits directly under The read, because it is the manager's own read.
    The three layers are drawn out of things the page already has -- the
    nine's bands, the shared `judged()` and the finish block the trade page
    draws -- so what is checked here is that it calls the right two routes,
    carries `?today=` on both, and reaches for those three.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text

    order = ["read-section", "whatif-section", "sched-section"]
    assert [page.index(name) for name in order] == sorted(page.index(name) for name in order)
    assert "/what-if` +\n      params(WHERE, extra)" in page, "the route, with the same ?today="
    assert "/trades/pool${query}" in page and "with_team: other.espn_team_id" in page
    assert "judged(a.judgement)" in page, "the judgement is the page's own helper, untouched"
    assert "finishHtml(a.finish)" in page, "and the finish is the trade page's own block"
    assert "whatIfBandHtml(" in page, "the nine before and after, in the bands above"
    assert "a.finish.language" in page, "the route's own words about what the finish is"
    assert 'id="whatif-failed"' in page and 'class="warnline" id="whatif-failed"' in page


def test_the_what_if_form_fetches_nothing_until_it_is_asked(client: TestClient) -> None:
    """The first load keeps its height: a heading, a form and nothing else.

    The wire is read the first time the chooser is opened and the move is
    judged only on Run, so neither fetch may be reachable from `start()`.
    The last move asked about is kept in localStorage, wrapped, so a private
    window merely forgets it.
    """
    page = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/week").text
    start = page.split("async function start()")[1]

    assert "readWire(" not in start and "runWhatIf(" not in start
    assert 'if ($("whatif-wire").open) readWire();' in page, "the wire waits to be opened"
    assert "onsubmit" in page and "runWhatIf();" in page, "and the move waits for Run"
    assert "localStorage.setItem(\n      whatIfKey()" in page
    assert page.count("catch (error)") >= 2, "every store is wrapped"


def test_the_finish_block_is_written_once_for_both_pages(client: TestClient) -> None:
    """One function, in the shared script: the trade page draws a deal's
    finish and the week page a what-if's, and the two cannot drift."""
    script = client.get("/pages/static/pages.js").text
    trades = client.get(f"/l/{LEAGUE_ID}/{SEASON}/team/{OURS}/trades").text

    assert "function finishHtml(f)" in script
    assert "so this is inside the noise" in script
    assert "function finishHtml" not in trades, "the trade page no longer has one of its own"
    assert "finishHtml(side.finish)" in trades


def test_the_lineup_grid_is_styled_in_both_themes_and_at_phone_width(
    client: TestClient,
) -> None:
    """No colour outside the token block, and nothing new that only works
    in one theme: the grid is the house's plain table with widths on it."""
    css = client.get("/pages/static/pages.css").text

    assert ".grid.lineup" in css and ".grid.lineup td.name .pname" in css
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


def test_the_context_route_names_the_status_source_it_read(client: TestClient) -> None:
    """The "how this is worked out" line is drawn from this, so it names it.

    `SEASON` is the one the listener ran for, so a day of it reads ESPN's own
    snapshot; `PLAYED` has no snapshot and no stored report either, so
    nothing could be read and the page says so rather than implying a roster
    of fit men was checked (`docs/replay_status.md`).
    """
    live = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context?today=3").json()

    assert live["injuries"]["used"] == "espn"
    assert live["injuries"]["used_note"].startswith("ESPN's own status")
    assert live["injuries"]["read_as_of"].startswith("2025-10-23"), "ten o'clock Eastern"
    assert live["injuries"]["placed"] > 0
    assert live["injuries"]["unmatched"] == 0

    played = client.get(f"/leagues/{LEAGUE_ID}/seasons/{PLAYED}/pages/context?today=3").json()

    assert played["injuries"]["used"] == "none"
    assert played["injuries"]["placed"] == 0


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


def test_the_context_route_says_how_old_the_box_scores_are(
    client: TestClient, session: Session
) -> None:
    """The honest limit the page prints under The nine.

    Null until a run has succeeded, because a page that named a time with
    nothing behind it would be worse than one that said it did not know.
    """
    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context").json()
    assert body["box_scores_as_of"] is None

    finished = datetime(2026, 1, 8, 9, 2, tzinfo=UTC)
    session.add_all(
        [
            IngestRun(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                mode="recent",
                status=SUCCEEDED,
                started_at=finished - timedelta(minutes=4),
                finished_at=finished,
                detail={},
            ),
            # Later, but it failed: a failure stores nothing, so it cannot be
            # what the box scores on the page are as of.
            IngestRun(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                mode="recent",
                status="failed",
                started_at=finished + timedelta(hours=1),
                finished_at=finished + timedelta(hours=1),
                detail={},
            ),
        ]
    )
    session.commit()

    body = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SEASON}/pages/context").json()
    assert body["box_scores_as_of"].startswith("2026-01-08T09:02")


# --------------------------------------------------------------------------
# The score as it stands, and the men behind it


def stream(client: TestClient, season: int, team: int, day: int) -> dict:
    got = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{season}/teams/{team}/pickups/stream",
        params={"today": day},
    )
    assert got.status_code == 200, got.text
    return got.json()


def test_the_week_report_carries_the_score_as_it_stands(client: TestClient) -> None:
    """What THE NINE prints under each chance.

    On a day the rule reads as a replay, the score is our stored box scores
    over the period's days before today -- not ESPN's matchup row, which is
    the whole period's and on this fixture is a much larger number.
    """
    body = stream(client, SCORED, 1, 3)

    assert body["posted_source"] == "box_scores"
    # Two men, two days each, at full and 0.6 of the same line.
    assert body["posted"]["PTS"] == pytest.approx(2 * (20.0 + 12.0))
    assert body["posted"]["PTS"] != POSTED["PTS"], "not ESPN's whole-period row"
    assert body["opponent_posted"]["PTS"] == pytest.approx(16.0)
    assert body["projected"]["PTS"] > body["posted"]["PTS"], "the days left are still to come"


def test_the_men_behind_the_score_add_up_to_it(client: TestClient) -> None:
    """The invariant the by-man table rests on.

    Every Total row on the page is that side's score, category by category.
    If this ever fails, the table has to go rather than be printed beside a
    figure it disagrees with.
    """
    body = stream(client, SCORED, 1, 3)

    for side, men in (("posted", "posted_men"), ("opponent_posted", "opponent_posted_men")):
        assert body[men], side
        for abbreviation, value in body[side].items():
            summed = sum(man["line"].get(abbreviation, 0.0) for man in body[men])
            assert summed == pytest.approx(value), f"{side} {abbreviation}"
    assert [man["name"] for man in body["posted_men"]] == ["Nine", "Ten"], "best first"
    assert [man["games"] for man in body["posted_men"]] == [2, 2]
    assert all(man["espn_player_id"] > 0 for man in body["posted_men"]), "ESPN ids go out"


def test_on_a_live_morning_the_score_is_espns_own_row(client: TestClient) -> None:
    """The one case the page has to explain rather than simply print.

    This league's listened season has no box score at all, so every day of
    it is live and the score is ESPN's running matchup row.
    """
    body = stream(client, SEASON, OURS, 1)

    assert body["posted_source"] == "espn"
    assert body["posted"]["PTS"] == POSTED["PTS"]
    assert body["opponent_posted"]["PTS"] == pytest.approx(POSTED["PTS"] * 0.8)
    assert body["posted_men"] == [], "nothing has been ingested, so nobody is behind it"


def test_a_report_stored_before_the_score_existed_still_validates(client: TestClient) -> None:
    """Additive, like every field before it: yesterday's stored row still
    draws, with no score rather than a 500."""
    body = stream(client, SEASON, OURS, 1)
    for field in ("posted", "opponent_posted", "posted_source", "posted_men"):
        del body[field]
    del body["opponent_posted_men"]

    out = StreamReportOut.model_validate(body)

    assert (out.posted, out.opponent_posted) == ({}, {})
    assert out.posted_source == ""
    assert out.posted_men == [] and out.opponent_posted_men == []


def test_the_days_own_report_carries_each_mans_stored_line(client: TestClient) -> None:
    """A man's box score in Tonight, once the ingest has the day.

    Day 4 of the mid-period season has one line stored and nothing else, so
    the same report shows one man with a line and the rest without -- which
    is what the page draws as the game mark alone.
    """
    got = client.get(f"/leagues/{LEAGUE_ID}/seasons/{SCORED}/teams/1/today", params={"today": 4})
    assert got.status_code == 200, got.text
    body = got.json()

    everyone = [seat["player"] for seat in body["lineup"] if seat["player"]]
    everyone += [one["player"] for one in body["benched"]] + body["idle"]
    lines = {man["name"]: man["line"] for man in everyone}
    assert lines["Nine"] is not None
    assert lines["Nine"]["scoring_period"] == 4
    assert lines["Nine"]["points"] == STARTER["PTS"]
    assert lines["Nine"]["minutes"] == 30.0
    assert lines["Ten"] is None, "no stored line, so the row shows his game and no more"


def test_the_lineups_route_carries_the_whole_line_for_the_other_side(
    client: TestClient,
) -> None:
    """Their half of Tonight. A box score is a league-visible fact, which is
    why this is the league's route and not the team's."""
    body = client.get(
        f"/leagues/{LEAGUE_ID}/seasons/{SCORED}/teams/2/lineups",
        params={"scoring_period": 1, "started": "true"},
    ).json()

    (row,) = body["items"]
    assert row["player_name"] == "Eleven"
    assert row["played"] is True
    assert row["points"] == pytest.approx(STARTER["PTS"] * 0.8)
    assert row["three_pointers_made"] == pytest.approx(STARTER["3PM"] * 0.8)
    assert row["field_goals_attempted"] == pytest.approx(STARTER["FGA"] * 0.8)
    assert row["minutes"] == 30.0


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
