"""The rest-of-season report on constructed rosters.

A four-man roster on a three-slot lineup, every man playing every day, and
per-game lines chosen so the roster lands near the league's weekly means:
that way every category starts as a near coin flip and a change in one
player is visible as a change in expected wins rather than lost in a
category already won or already conceded.

The cases are section 4.7's: the obviously better swap on a two-player
pool, the marginal swap the hurdle refuses, the volume guard's window, and
a stash that appears only when the return date is near enough.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod, Player, Team
from app.pickups.bids import clear_cache
from app.pickups.season import (
    CHURN_DAYS,
    SEASON_HURDLE_PAID,
    SWAP,
    TWO_SWAP,
    season_recommendations,
    weeks_between,
)
from scripts.season import render
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    configure,
    day_date,
    eligible,
    games,
    on_the_wire,
    projected,
    snapshot,
)
from tests.scoring_db import held, league_season, player, transaction

HOME, AWAY = 1, 2

#: Every player's NBA team plays every day of the fourteen-day season, so a
#: game count never decides a case; production does.
EVERY_DAY = list(range(1, 15))

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


def scaled(factor: float) -> dict[str, float]:
    """The starter's line, every count multiplied."""
    return {key: value * factor for key, value in STARTER.items()}


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    clear_cache()
    yield scoring_session


def build_season(
    session: Session, *, bench: int = 1, periods: int = 2
) -> tuple[LeagueSeason, Team, MatchupPeriod]:
    """A season on the small lineup: seven days a period, no playoffs."""
    ls, (home, _), all_periods = league_season(session, days_per_period=7, periods=periods)
    configure(ls, lineup=SMALL_LINEUP, bench=bench, injured_reserve=0)
    return ls, home, all_periods[0]


def rostered(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    name: str,
    per_game: Mapping[str, float],
    *,
    pro_team: int = 10,
) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(session, who, pro_team_id=pro_team, on_team_id=team.espn_team_id)
    projected(session, who, 70, per_game)
    held(session, team, period, who, 1)
    return who


def free_agent(
    session: Session,
    ls: LeagueSeason,
    name: str,
    per_game: Mapping[str, float],
    *,
    pro_team: int = 20,
    injury_status: str = "ACTIVE",
    returns: int | None = None,
) -> Player:
    who = player(session, name)
    eligible(session, who, ANY, "PG")
    snapshot(
        session,
        who,
        pro_team_id=pro_team,
        on_team_id=0,
        injury_status=injury_status,
        expected_return_date=day_date(returns) if returns is not None else None,
    )
    projected(session, who, 70, per_game)
    on_the_wire(session, ls, who)
    return who


def full_roster(session: Session, ls: LeagueSeason, home: Team, first: MatchupPeriod) -> None:
    """Three ordinary starters and one man who is half a player."""
    for name in ("A", "B", "C"):
        rostered(session, home, first, name, STARTER)
    rostered(session, home, first, "Weak", scaled(0.5))
    games(session, 10, EVERY_DAY)


def test_the_optimizer_takes_the_better_of_a_two_player_pool(session: Session) -> None:
    ls, home, first = build_season(session)
    full_roster(session, ls, home, first)
    free_agent(session, ls, "Star", scaled(1.4), pro_team=20)
    free_agent(session, ls, "Scrub", scaled(0.2), pro_team=21)
    games(session, 20, EVERY_DAY)
    games(session, 21, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    assert report.pool_size == 2
    assert report.weeks_remaining == weeks_between(1, 14) == 2.0
    swap = report.best_swap
    assert swap is not None
    assert swap.kind == SWAP
    assert [p.name for p in swap.out] == ["Weak"]
    assert [p.name for p in swap.into] == ["Star"], "the better man, not the cheaper one"
    assert swap.delta >= SEASON_HURDLE_PAID
    assert report.recommended is swap
    assert swap.moved(), "the categories that moved are named"
    assert swap.bid is not None, "a move that clears the hurdle is priced"


def test_every_swap_carries_a_judgement_over_both_horizons(session: Session) -> None:
    """Section 4.4's second pass: the week half is the streaming report's own
    head-to-head, the season half is the optimizer's Δ per week, and the
    hurdle is read on the net spread over the weeks it covers."""
    ls, home, first = build_season(session)
    full_roster(session, ls, home, first)
    free_agent(session, ls, "Star", scaled(1.4), pro_team=20)
    games(session, 20, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    swap = report.best_swap
    assert swap is not None
    judgement = swap.judgement
    assert judgement.delta_season_per_week == pytest.approx(swap.delta), "the optimizer's own Δ"
    assert judgement.weeks_remaining == pytest.approx(1.0), "period 2, and no more"
    assert swap.net == pytest.approx(
        judgement.delta_week + judgement.delta_season_per_week * judgement.weeks_remaining
    )
    assert judgement.per_week == pytest.approx(swap.net / (judgement.weeks_remaining + 1.0))
    assert swap.clears(report.hurdle_paid, report.hurdle_free) is (
        judgement.per_week >= SEASON_HURDLE_PAID
    )
    assert report.outlook.delta_total == 0.0
    assert report.outlook.record_with == report.outlook.record_without


def test_the_cli_prints_both_horizons_and_the_projected_record(session: Session) -> None:
    """As on the streaming side: the layout is the CLI's, but a field it
    cannot read is a crash on a real day."""
    ls, home, first = build_season(session)
    full_roster(session, ls, home, first)
    free_agent(session, ls, "Star", scaled(1.4), pro_team=20)
    games(session, 20, EVERY_DAY)
    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    text = render(report, season=2026, team_name="Home", when=None)

    assert "moves, by net categories over both horizons:" in text
    assert "projected record" in text
    assert "season so far:" in text
    assert "a week; this move is" in text, "the hurdle is a week, the net is not"
    assert "adds this period: used 0 of 7" in text


def test_the_report_carries_the_periods_add_budget(session: Session) -> None:
    """The same budget the streaming report reads, so a manager is told what
    a move costs in adds as well as in FAAB."""
    ls, home, first = build_season(session)
    full_roster(session, ls, home, first)
    free_agent(session, ls, "Star", scaled(1.4), pro_team=20)
    games(session, 20, EVERY_DAY)
    transaction(session, home, 2, "FREEAGENT", [("ADD", player(session, "Someone"), None, home)])

    report = season_recommendations(session, ls, HOME, today=3, distributions=WEEK)

    assert (report.adds_used, report.adds_budget, report.adds_left) == (1, 7, 6)
    assert "adds this period: used 1 of 7" in render(
        report, season=2026, team_name="Home", when=None
    )


def test_the_drop_candidates_are_the_men_who_cost_least_to_lose(session: Session) -> None:
    ls, home, first = build_season(session)
    full_roster(session, ls, home, first)
    free_agent(session, ls, "Star", scaled(1.4))
    games(session, 20, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    assert [drop.player.name for drop in report.drops] == ["Weak", "A", "B"] or [
        drop.player.name for drop in report.drops
    ] == ["Weak", "B", "A"], "the weak man first, then the interchangeable starters"
    assert report.drops[0].replacement is not None
    assert report.drops[0].replacement.name == "Star"
    assert report.drops[0].delta > report.drops[1].delta, (
        "losing the weak man costs least, so he is the first drop"
    )


def test_a_marginal_swap_is_found_and_refused(session: Session) -> None:
    ls, home, first = build_season(session)
    for name in ("A", "B", "C", "D"):
        rostered(session, home, first, name, STARTER)
    games(session, 10, EVERY_DAY)
    free_agent(session, ls, "Slightly Better", scaled(1.01))
    games(session, 20, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    assert report.open_slots == 0
    swap = report.best_swap
    assert swap is not None
    assert 0 < swap.delta < SEASON_HURDLE_PAID
    assert swap.clears(report.hurdle_paid, report.hurdle_free) is False
    assert swap.bid is None, "an unrecommended move is not priced"
    assert report.recommended is None


def test_two_swaps_are_searched_as_well_as_one(session: Session) -> None:
    ls, home, first = build_season(session)
    for name in ("A", "B"):
        rostered(session, home, first, name, STARTER)
    rostered(session, home, first, "Weak", scaled(0.5))
    rostered(session, home, first, "Weaker", scaled(0.4))
    games(session, 10, EVERY_DAY)
    free_agent(session, ls, "Star", scaled(1.4), pro_team=20)
    free_agent(session, ls, "Second Star", scaled(1.3), pro_team=21)
    games(session, 20, EVERY_DAY)
    games(session, 21, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    two = report.best_two_swap
    assert two is not None
    assert two.kind == TWO_SWAP
    assert sorted(p.name for p in two.out) == ["Weak", "Weaker"]
    assert sorted(p.name for p in two.into) == ["Second Star", "Star"]
    assert report.best_swap is not None
    assert two.delta > report.best_swap.delta, "two places bought more than one"
    assert report.recommended is two


def test_a_free_add_into_an_open_place_clears_the_lower_hurdle(session: Session) -> None:
    ls, home, first = build_season(session, bench=2)
    for name in ("A", "B", "C"):
        rostered(session, home, first, name, STARTER)
    games(session, 10, EVERY_DAY)
    free_agent(session, ls, "Body", scaled(0.6))
    games(session, 20, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    add = report.best_add
    assert report.open_slots == 2
    assert add is not None
    assert add.out == ()
    assert add.costs_faab is False
    assert [p.name for p in add.into] == ["Body"]
    assert add.delta >= report.hurdle_free
    assert report.recommended is add


def test_the_volume_guard_counts_only_the_adds_inside_the_window(session: Session) -> None:
    ls, home, first = build_season(session, periods=3)
    for name in ("A", "B", "C"):
        rostered(session, home, first, name, STARTER)
    rostered(session, home, first, "Weak", scaled(0.5))
    games(session, 10, list(range(1, 22)))
    free_agent(session, ls, "Star", scaled(1.4))
    games(session, 20, list(range(1, 22)))
    recent = player(session, "Recent Add")
    old = player(session, "Old Add")
    transaction(session, home, 10, "FREEAGENT", [("ADD", recent, None, home)])
    transaction(session, home, 12, "WAIVER", [("ADD", recent, None, home)])
    transaction(session, home, 5, "FREEAGENT", [("ADD", old, None, home)])

    report = season_recommendations(session, ls, HOME, today=20, distributions=WEEK)

    assert report.churn.days == CHURN_DAYS
    assert report.churn.adds == 2, "day 5 is outside the fortnight before day 20"
    assert "r = -0.63" in report.churn.finding


def test_a_stash_appears_only_with_a_return_date_inside_six_weeks(session: Session) -> None:
    ls, home, first = build_season(session)
    full_roster(session, ls, home, first)
    # The season's last day is 14, so a return date six weeks out is a date
    # the schedule never reaches; both men are OUT for every day it has.
    free_agent(session, ls, "Back Soon", scaled(1.4), pro_team=20, injury_status="OUT", returns=30)
    free_agent(session, ls, "Back Later", scaled(1.4), pro_team=21, injury_status="OUT", returns=80)
    games(session, 20, EVERY_DAY)
    games(session, 21, EVERY_DAY)

    report = season_recommendations(session, ls, HOME, today=1, distributions=WEEK)

    assert [stash.player.name for stash in report.stashes] == ["Back Soon"]
    stash = report.stashes[0]
    assert stash.weeks_away == pytest.approx(29 / 7)
    assert stash.healthy_rank == 1, "healthy he is the best man on the wire"
    assert stash.needs_drop is True, "no injured-reserve slot in this season"
    assert report.best_swap is not None
    assert report.best_swap.delta < 0, "an OUT man plays no games, so no swap helps"
