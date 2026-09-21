"""The forward trade evaluator, on a league small enough to check by hand.

Two four-man rosters on a three-slot lineup, everybody playing every day, and
per-game lines near the league's weekly means so that every category starts as
a near coin flip: a swap then shows up as a change in expected wins rather
than disappearing into a category already won.

The cases are the ones the design has to get right. An even swap moves the two
sides in opposite directions, and the per-man number mirrors exactly, because
the same arithmetic is run from both ends; the headline, which is a
with-and-without over each team's own roster since revision R1, does not have
to mirror, and a roster that already wins a category is not paid for more of
it. A two-for-one opens a place on one side and forces a drop on the other,
and the report names the man and what he cost. The playoff lens counts the
playoff weeks alone. And the whole thing must read nothing after the day it is
judged on, which is the one claim a docstring cannot make credible: the last
test builds a database with the future in it, runs the report, deletes every
outcome row after the judgement day, runs it again, and demands the same
answer.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod, Player, Team
from app.pickups.bids import clear_cache
from app.pickups.judge import TYPICAL_PICKUP
from app.pickups.projection import clear_cache as clear_lines
from app.trades import CALIBRATION_NOTE, PUBLISHED, TRADE_HURDLE, TeamOffer, evaluate_trade
from app.trades.calibration import COIN_RANGE, DEALS, WINDOW_DAYS
from app.trades.summary import WORDS, join, words
from scripts.trade import AmbiguousNameError, player_by_name, render
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    configure,
    eligible,
    games,
    on_the_wire,
    played,
    projected,
    snapshot,
)
from tests.scoring_db import held, league_season, matchup, player

HOME, AWAY = 1, 2

#: Four seven-day periods: three of regular season, one of playoffs, so the
#: playoff lens has a window of its own and the season term has weeks in it.
PERIODS = 4
REGULAR = 3
SEASON_DAYS = list(range(1, PERIODS * 7 + 1))

#: The day every case is judged on: the first day of the second period, so
#: one week is banked, one is being played, and one is still to come.
TODAY = 8

#: A starter's per-game line. Four of these over a week come to about the
#: league's weekly means in `WEEK`, so every category is near a coin flip.
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

#: Both sides level in every category, so the banked week decides nothing.
EVEN = {
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


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    clear_cache()
    clear_lines()
    yield scoring_session


def build_league(session: Session) -> tuple[LeagueSeason, Team, Team, list[MatchupPeriod]]:
    ls, (home, away), periods = league_season(
        session, days_per_period=7, periods=PERIODS, regular_season_periods=REGULAR
    )
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
    # Period 1 is played and level, so the league standard is measurable and
    # the banked record is the same for both sides.
    matchup(session, periods[0], home, away, {home: EVEN, away: EVEN})
    # Period 2 is the week in front of us, with no totals posted yet.
    matchup(session, periods[1], home, away)
    games(session, 10, SEASON_DAYS)
    games(session, 20, SEASON_DAYS)
    return ls, home, away, periods


def rostered(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    name: str,
    per_game: Mapping[str, float],
    *,
    pro_team: int = 10,
    days: tuple[int, ...] = (1, TODAY),
) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=pro_team, on_team_id=team.espn_team_id)
    projected(session, who, 70, per_game)
    for day in days:
        held(session, team, period, who, day)
    return who


def on_wire(session: Session, ls: LeagueSeason, name: str, per_game: Mapping[str, float]) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=20, on_team_id=0)
    projected(session, who, 70, per_game)
    on_the_wire(session, ls, who)
    return who


def build_rosters(
    session: Session, ls: LeagueSeason, home: Team, away: Team, periods: list[MatchupPeriod]
) -> dict[str, Player]:
    """Four men a side: three ordinary and, for Away, one star."""
    who: dict[str, Player] = {}
    for name in ("HomeA", "HomeB", "HomeC"):
        who[name] = rostered(session, home, periods[1], name, STARTER)
    who["HomeWeak"] = rostered(session, home, periods[1], "HomeWeak", scaled(0.4))
    for name in ("AwayA", "AwayB"):
        who[name] = rostered(session, away, periods[1], name, STARTER, pro_team=20)
    # Distinctly the cheapest place in the league, so "the cheapest drop" has
    # one answer and not two.
    who["AwayWeak"] = rostered(session, away, periods[1], "AwayWeak", scaled(0.2), pro_team=20)
    who["Star"] = rostered(session, away, periods[1], "Star", scaled(1.6), pro_team=20)
    return who


def test_an_even_swap_is_judged_from_both_ends_and_the_per_man_number_mirrors(
    session: Session,
) -> None:
    """The same machinery run from each roster, and what revision R1 changed.

    The per-man number values each man on his own inside a league-average
    team, so what the better man is worth to the side that gets him is exactly
    what he costs the side that gives him up: it mirrors to the last decimal.
    That is not a tautology in the code -- each side is a separate `SpotBook`,
    a separate head-to-head and a separate league-standard sum -- so it is
    still the cheapest check that neither side is being judged by a different
    rule, which is why it is kept on the payload.

    The headline is now a with-and-without over each team's own roster, and
    two rosters do not saturate alike. The two sides still move in opposite
    directions; they no longer move by the same amount, and that difference is
    the whole of what R1 buys.
    """
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    on_wire(session, ls, "Wire", scaled(0.5))

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id,)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    ours, theirs = report.side(HOME), report.side(AWAY)
    assert [card.name for card in ours.receives] == ["Star"]
    assert [card.name for card in ours.gives] == ["HomeWeak"]
    assert ours.drops == () and theirs.drops == ()
    assert ours.net > 0 > theirs.net, "the side getting the better man gains"
    assert ours.season_independent == pytest.approx(-theirs.season_independent)
    assert ours.judgement.delta_season_per_week > 0 > theirs.judgement.delta_season_per_week, (
        "the headline still knows which side got the better man"
    )
    assert ours.judgement.delta_season_per_week != pytest.approx(
        -theirs.judgement.delta_season_per_week
    ), "two rosters do not saturate alike, which is the point of judging on the roster"
    assert ours.clears is (ours.per_week >= TRADE_HURDLE)
    assert len(ours.categories) == 9


def test_a_roster_already_winning_a_category_is_not_paid_for_more_of_it(
    session: Session,
) -> None:
    """Revision R1, on the shape it was declared for (docs/trades.md 7a).

    Home has three big men and wins every counting category with a
    probability of one. It gives two fringe players for a star. The per-man
    number, which values the star inside a *league-average* team, pays more
    than a category a week for him. The roster he is actually joining gains
    almost nothing, because expected wins is a sum of saturating
    probabilities: the points go up by three hundred a week and the chance of
    winning points does not move, because it was already one.

    That gap is what the calibration of 2026-09-21 measured as +0.27
    categories a week of over-rating on uneven deals, and it is what the
    headline now refuses to pay.
    """
    ls, (home, away), periods = league_season(
        session, days_per_period=7, periods=PERIODS, regular_season_periods=REGULAR
    )
    configure(ls, lineup=SMALL_LINEUP, bench=3, injured_reserve=0)
    matchup(session, periods[0], home, away, {home: EVEN, away: EVEN})
    matchup(session, periods[1], home, away)
    games(session, 10, SEASON_DAYS)
    games(session, 20, SEASON_DAYS)
    for name in ("Big1", "Big2", "Big3"):
        rostered(session, home, periods[1], name, scaled(2.5))
    fringe = [rostered(session, home, periods[1], name, scaled(0.1)) for name in ("Fr1", "Fr2")]
    star = rostered(session, away, periods[1], "Star", scaled(2.2), pro_team=20)
    for name in ("AwayA", "AwayB"):
        rostered(session, away, periods[1], name, STARTER, pro_team=20)
    on_wire(session, ls, "Wire", scaled(0.5))

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, tuple(man.id for man in fringe)),
        TeamOffer(AWAY, (star.id,)),
        distributions=WEEK,
    )

    ours = report.side(HOME)
    points = next(view for view in ours.categories if view.abbreviation == "PTS")
    assert ours.places_opened == 1, "two men out for one in"
    assert points.p_before == pytest.approx(1.0, abs=0.001), "already winning points"
    assert points.after > points.before + 250, "and the deal adds a great many more"
    assert points.p_delta == pytest.approx(0.0, abs=0.001), "which is worth nothing"
    assert ours.season_independent > 1.0, "the old headline would have paid for it"
    assert ours.judgement.delta_season_per_week == pytest.approx(0.0, abs=0.1), (
        "the roster it is joining gains about nothing, and the headline says so"
    )
    assert ours.judgement.delta_season_per_week < ours.season_independent


def test_the_deal_is_seated_from_the_day_it_could_land_not_today(session: Session) -> None:
    """A trade judged on the morning of day 8 cannot play on day 8: the
    league's review takes a day (`TRADE_REVIEW_DAYS`), so day 8 is projected
    with the roster as it stands and days 9 to 14 with the one the deal
    leaves. A review long enough to push the deal past this period leaves the
    week worth exactly nothing, and the period it does land in is then one of
    the weeks the season term covers -- no day counted twice, and none
    dropped.
    """
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    offers = (TeamOffer(HOME, (who["HomeWeak"].id,)), TeamOffer(AWAY, (who["Star"].id,)))

    delayed = evaluate_trade(session, ls, TODAY, *offers, distributions=WEEK)
    at_once = evaluate_trade(session, ls, TODAY, *offers, review_days=0, distributions=WEEK)
    next_week = evaluate_trade(session, ls, TODAY, *offers, review_days=7, distributions=WEEK)

    assert (delayed.effective_day, at_once.effective_day) == (TODAY + 1, TODAY)
    assert at_once.side(HOME).judgement.delta_week != delayed.side(HOME).judgement.delta_week
    assert next_week.effective_day == TODAY + 7, "the first day of the period after this one"
    assert next_week.side(HOME).judgement.delta_week == 0.0, "no day of this week is the deal's"
    for report in (delayed, at_once, next_week):
        assert report.side(HOME).judgement.delta_season_per_week == pytest.approx(
            delayed.side(HOME).judgement.delta_season_per_week
        ), "the day it lands moves the week, never what the roster places are worth"
        assert report.side(HOME).judgement.weeks_remaining == pytest.approx(1.0)


def test_a_two_for_one_opens_a_place_here_and_forces_a_drop_there(session: Session) -> None:
    """Section 4 of the brief. The side giving two gets a place back, valued
    at what the wire gives a place; the side receiving two has no room, so
    somebody goes, and the report says who and what he cost."""
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    on_wire(session, ls, "Wire", scaled(0.5))

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id, who["HomeC"].id)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    ours, theirs = report.side(HOME), report.side(AWAY)
    assert ours.places_opened == 1 and ours.drops == ()
    assert ours.replacement >= TYPICAL_PICKUP
    assert ours.replacement_player is not None
    assert ours.replacement_player.name == "Wire", "the best man still on the wire"
    assert theirs.places_opened == 0
    assert [card.name for card in theirs.drops] == ["AwayWeak"], "the cheapest place"
    assert theirs.drop_source == "cheapest"
    assert theirs.drops[0].value < min(card.value for card in theirs.receives)


def test_a_named_drop_is_honoured_and_said_to_be_the_callers(session: Session) -> None:
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id, who["HomeC"].id)),
        TeamOffer(AWAY, (who["Star"].id,)),
        drops={AWAY: (who["AwayA"].id,)},
        distributions=WEEK,
    )

    theirs = report.side(AWAY)
    assert [card.name for card in theirs.drops] == ["AwayA"]
    assert theirs.drop_source == "named"


def test_a_man_the_team_does_not_hold_is_a_sentence_not_a_number(session: Session) -> None:
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)

    with pytest.raises(ValueError, match="does not have"):
        evaluate_trade(
            session,
            ls,
            TODAY,
            TeamOffer(HOME, (who["Star"].id,)),
            TeamOffer(AWAY, (who["HomeWeak"].id,)),
            distributions=WEEK,
        )
    with pytest.raises(ValueError, match="cannot both trade away and drop"):
        evaluate_trade(
            session,
            ls,
            TODAY,
            TeamOffer(HOME, (who["HomeWeak"].id,)),
            TeamOffer(AWAY, (who["Star"].id,)),
            drops={HOME: (who["HomeWeak"].id,)},
            distributions=WEEK,
        )


def test_the_playoff_lens_counts_the_playoff_weeks_alone(session: Session) -> None:
    """`trade_grades` separates the two in hindsight, so the forward report
    does too: a man whose NBA team plays in the playoff weeks is a different
    proposition from one whose does not, and the regular-season number cannot
    say so."""
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id,)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    lens = report.side(HOME).playoffs
    assert lens.measurable, lens.note
    assert (lens.first_scoring_period, lens.last_scoring_period) == (22, 28)
    assert lens.weeks == pytest.approx(1.0)
    assert lens.games == 14, "seven playoff days each for the two men in the deal"
    assert lens.delta_per_week > 0
    assert report.side(AWAY).playoffs.delta_per_week == pytest.approx(-lens.delta_per_week)


def test_a_season_with_no_playoff_periods_says_so_rather_than_reading_zero(
    session: Session,
) -> None:
    ls, (home, away), periods = league_season(session, days_per_period=7, periods=2)
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
    matchup(session, periods[0], home, away, {home: EVEN, away: EVEN})
    matchup(session, periods[1], home, away)
    games(session, 10, list(range(1, 15)))
    games(session, 20, list(range(1, 15)))
    who = build_rosters(session, ls, home, away, periods)

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id,)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    lens = report.side(HOME).playoffs
    assert not lens.measurable
    assert lens.note is not None and "no playoff" in lens.note
    assert lens.delta_per_week == 0.0


def test_the_nine_show_the_counts_moving_and_the_chances_not(session: Session) -> None:
    """The heart of the report: a manager punting a category should see what
    he gives away in it cost him nothing. Here the roster is far enough ahead
    in blocks that losing some of them moves the count and not the chance."""
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    on_wire(session, ls, "Wire", scaled(0.5))

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id,)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    ours = report.side(HOME)
    assert {view.abbreviation for view in ours.categories} == {
        "PTS",
        "REB",
        "AST",
        "STL",
        "BLK",
        "3PM",
        "TO",
        "FG%",
        "FT%",
    }
    points = next(view for view in ours.categories if view.abbreviation == "PTS")
    assert points.after > points.before, "the better man scores more"
    assert points.p_after > points.p_before
    assert ours.moved(), "the categories whose chances moved are named"
    assert all(abs(view.p_delta) >= 0.01 for view in ours.moved()), (
        "only categories that really moved"
    )


def test_the_summary_leads_with_the_fit_and_never_gives_an_order(session: Session) -> None:
    """The categories first, the number second.

    What a nine-cat manager cannot get anywhere else is which categories a
    deal wins him and which it hands over, and that half of the report is
    arithmetic about his roster rather than a forecast of the season. So it is
    the first sentence, and the headline number is the second.
    """
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)

    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id,)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    summary = report.side(HOME).summary
    lead = summary.split(". ")[0]
    assert any(word in lead for word in WORDS.values()), "the first sentence names categories"
    assert "a week" not in lead, "and carries no number"
    assert "categories a week over the" in summary, "which comes in the sentence after it"
    assert "bar" in summary
    for verdict in ("accept", "reject", "should", "must", "do it", "take it"):
        assert verdict not in summary.lower()


def test_the_cli_prints_both_sides_and_says_whose_estimate_the_other_is(
    session: Session,
) -> None:
    """The layout is the CLI's, but a field it cannot read is a crash on a
    real day, so the render is exercised here as the other two CLIs are."""
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    report = evaluate_trade(
        session,
        ls,
        TODAY,
        TeamOffer(HOME, (who["HomeWeak"].id,)),
        TeamOffer(AWAY, (who["Star"].id,)),
        distributions=WEEK,
    )

    printed = render(report, ours=HOME, when=None)

    assert "our estimate of their side, not what they think" in printed
    assert "the nine, in an ordinary week" in printed
    assert "what it rests on:" in printed
    assert "playoffs (days 22-28" in printed


def test_the_report_reads_nothing_after_the_day_it_is_judged_on(session: Session) -> None:
    """The claim `app.trades` makes, tested rather than asserted.

    The fixture gives every man in the deal a monstrous set of box scores on
    every day after the judgement day -- lines nothing could have known on the
    morning of day 8. The report is built, every outcome row after day 8 is
    deleted, and the report is built again. The two must be identical, field
    for field: if any query reached past `today`, deleting the rows it read
    would move a number.

    The NBA schedule is deliberately *not* deleted. Games still to be played
    are a fact about the future that is on record today, and the games-left
    counts are supposed to read them.

    Revision R1 made the headline a sum over the whole active roster rather
    than over the men in the deal, which is a great many more rows to read, so
    both numbers are named below as well as compared field for field.
    """
    ls, home, away, periods = build_league(session)
    who = build_rosters(session, ls, home, away, periods)
    on_wire(session, ls, "Wire", scaled(0.5))
    for day in range(TODAY, PERIODS * 7 + 1):
        for name in ("HomeWeak", "Star", "HomeA", "AwayA"):
            played(session, who[name], day, 40.0, scaled(4.0))
    session.commit()

    offers = (TeamOffer(HOME, (who["HomeWeak"].id,)), TeamOffer(AWAY, (who["Star"].id,)))
    with_the_future = evaluate_trade(session, ls, TODAY, *offers, distributions=WEEK)

    session.execute(
        text("DELETE FROM player_game_stats WHERE scoring_period > :day"), {"day": TODAY}
    )
    session.execute(
        text("DELETE FROM daily_lineup_slots WHERE scoring_period > :day"), {"day": TODAY}
    )
    session.commit()
    clear_lines()
    without_it = evaluate_trade(session, ls, TODAY, *offers, distributions=WEEK)

    for before, after in zip(with_the_future.sides, without_it.sides, strict=True):
        assert before.judgement.delta_season_per_week == pytest.approx(
            after.judgement.delta_season_per_week
        ), "the roster with-and-without reads nothing after today"
        assert before.season_independent == pytest.approx(after.season_independent)
    assert with_the_future.sides == without_it.sides
    assert with_the_future == without_it


def test_a_name_that_matches_two_men_is_refused_with_the_alternatives() -> None:
    """The CLI's own matching: exact first, then a unique substring, and a
    refusal that names what it matched rather than a silent pick."""
    roster = {1: "Myles Turner", 2: "Evan Turner", 3: "Neemias Queta"}

    assert player_by_name(roster, "Myles Turner") == 1
    assert player_by_name(roster, "queta") == 3
    with pytest.raises(AmbiguousNameError) as refused:
        player_by_name(roster, "Turner")
    assert refused.value.matches == ("Evan Turner", "Myles Turner")
    with pytest.raises(KeyError):
        player_by_name(roster, "Jokic")


def test_the_published_note_says_what_the_published_numbers_say() -> None:
    """The page prints `CALIBRATION_NOTE` verbatim, so it must not drift.

    A forecast shown without its record is the thing docs/trades.md section 7
    says we must not ship, and a record that no longer matches the run behind
    it would be worse than none. This holds the sentence to the data beside
    it: the primary cell, the sample, the coin, and no jargon.
    """
    first = PUBLISHED[0]
    assert first.horizon == f"next {WINDOW_DAYS} days", "the cell declared primary before the run"
    assert f"{first.picked} of them" in CALIBRATION_NOTE
    assert f"{DEALS} trades" in CALIBRATION_NOTE
    assert f"between {COIN_RANGE[0]} and {COIN_RANGE[1]} of {DEALS}" in CALIBRATION_NOTE
    assert COIN_RANGE[0] <= first.picked <= COIN_RANGE[1], "which is why it says it is a coin"
    for jargon in ("Spearman", "correlation", "R1", "per-week"):
        assert jargon not in CALIBRATION_NOTE


def test_the_summary_joins_names_the_way_a_sentence_does() -> None:
    assert join([]) == ""
    assert join(["blocks"]) == "blocks"
    assert join(["blocks", "rebounds"]) == "blocks and rebounds"
    assert join(["blocks", "rebounds", "steals"]) == "blocks, rebounds and steals"
    assert words("BLK") == "blocks"
    assert words("XYZ") == "XYZ", "an unknown category is said as it is spelt"
