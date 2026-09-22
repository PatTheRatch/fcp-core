"""The day's lineup on constructed days.

A three-place lineup (`SMALL_LINEUP`: one guard, one forward, one utility),
so a full day is three men and the cases a manager cares about can be built
by hand: a man whose team is not playing sitting in a place while a man with
a game sits on the bench, a man on injured reserve, a man eligible for
nothing the lineup starts, and two men who are level.

The last test is the one the in-season rehearsal's first finding asks for.
It builds a week with days after today in it, takes the day's answer, then
deletes every row the season went on to write and takes it again: the two
answers have to be the same object for the day to be free of look-ahead.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason, MatchupPeriod, Player, Team
from app.pickups.projection import clear_cache as clear_projection_cache
from app.pickups.today import (
    NO_SLOT,
    ON_IR,
    OUTRANKED,
    today_lineup,
)
from tests.pickups_db import (
    ANY,
    CENTRE,
    FORWARD,
    GUARD,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    configure,
    eligible,
    games,
    projected,
    snapshot,
)
from tests.scoring_db import held, league_season, matchup, player

HOME, AWAY = 1, 2

#: Level so far, so nothing in the week's state can tip the day's answer.
EVEN = {
    "PTS": 300.0,
    "REB": 120.0,
    "AST": 60.0,
    "STL": 18.0,
    "BLK": 12.0,
    "3PM": 30.0,
    "TO": 36.0,
    "FGM": 110.0,
    "FGA": 240.0,
    "FTM": 50.0,
    "FTA": 62.0,
}

TEN_POINTS = {"PTS": 10.0, "FGM": 4.0, "FGA": 8.0, "FTM": 2.0, "FTA": 2.5}


def scaled(factor: float) -> dict[str, float]:
    return {key: value * factor for key, value in TEN_POINTS.items()}


TWELVE_POINTS = scaled(1.2)
FIFTEEN_POINTS = scaled(1.5)
TWENTY_POINTS = scaled(2.0)

#: Bench only: ESPN lists the places a man may sit, and these two are not
#: places anybody starts in.
BENCH_ONLY = ["BE", "IR"]


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


def build(
    session: Session, *, injured_reserve: int = 0
) -> tuple[LeagueSeason, Team, MatchupPeriod]:
    ls, (home, away), (first, _) = league_season(session, days_per_period=7)
    configure(ls, lineup=SMALL_LINEUP, bench=3, injured_reserve=injured_reserve)
    matchup(session, first, home, away, {home: EVEN, away: EVEN})
    return ls, home, first


def rostered(
    session: Session,
    team: Team,
    period: MatchupPeriod,
    name: str,
    *,
    slots: list[str],
    pro_team: int,
    per_game: Mapping[str, float] = TEN_POINTS,
    position: str = "PG",
    injury_status: str = "ACTIVE",
    slot: str = "BE",
    day: int = 5,
) -> Player:
    """A man the team holds, sitting in `slot` on `day`.

    `slot` is where the team actually put him, which is both the roster the
    report reads and the lineup it compares itself with.
    """
    who = player(session, name)
    eligible(session, who, slots, position)
    snapshot(
        session,
        who,
        pro_team_id=pro_team,
        on_team_id=team.espn_team_id,
        injury_status=injury_status,
    )
    projected(session, who, 70, per_game)
    held(session, team, period, who, day, slot=slot)
    return who


def test_every_man_is_put_in_a_place_he_is_eligible_for(session: Session) -> None:
    """The grid respects eligibility, and which arrangement is not the point.

    A guard, a forward and a centre against one guard place, one forward
    place and one utility place. There is more than one way to seat them --
    the centre can take the forward place and the forward the utility one --
    and the report is free to pick any of them; what it may never do is put
    a man somewhere ESPN does not list him.
    """
    ls, home, first = build(session)
    rostered(session, home, first, "Guard", slots=GUARD, pro_team=10, slot="G")
    rostered(session, home, first, "Forward", slots=FORWARD, pro_team=11, slot="F")
    rostered(session, home, first, "Centre", slots=CENTRE, pro_team=12, slot="UT")
    for pro_team in (10, 11, 12):
        games(session, pro_team, [5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert [place.slot for place in report.lineup] == ["G", "F", "UT"]
    assert sorted(man.name for man in report.starters) == ["Centre", "Forward", "Guard"]
    for place in report.lineup:
        assert place.player is not None
        assert place.slot in place.player.player.eligible, f"{place.player.name} in {place.slot}"
    assert report.starts == 3
    assert report.teams_playing == 3
    assert report.benched == ()
    assert report.idle == ()
    assert report.fix == (), "the lineup they set fills as much as any lineup could"
    assert report.edge == pytest.approx(0.0)


def test_a_place_set_with_a_man_who_is_not_playing_is_the_thing_to_fix(
    session: Session,
) -> None:
    """The mistake that costs real categories, and the only thing here called one.

    A guard in the lineup whose NBA team is not playing tonight, and another
    guard on the bench whose team is. The report names the place, names who
    could take it, and says the lineup is a start short of what it could be.
    """
    ls, home, first = build(session)
    rostered(session, home, first, "Resting", slots=GUARD, pro_team=10, slot="G")
    rostered(session, home, first, "Playing", slots=GUARD, pro_team=11, slot="BE")
    rostered(session, home, first, "Forward", slots=FORWARD, pro_team=12, slot="F")
    games(session, 11, [5])
    games(session, 12, [5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert [place.player.name for place in report.lineup if place.player] == ["Playing", "Forward"]
    assert report.starts == 2 and report.actual_starts == 1
    assert len(report.fix) == 1
    fix = report.fix[0]
    assert fix.seat.slot == "G" and fix.seat.player is not None
    assert fix.seat.player.name == "Resting" and fix.seat.player.plays is False
    assert [man.name for man in fix.instead] == ["Playing"]
    assert [man.name for man in report.idle] == ["Resting"], "no game, so nothing to start him for"
    assert report.edge > 0, "the proposal is worth more than what is set"


def test_a_place_left_unset_counts_the_same_when_the_bench_can_fill_it(
    session: Session,
) -> None:
    """A place producing nothing is a place producing nothing.

    One man is benched with a game and the utility place is simply not set.
    That is the same category given away as a starter who is not playing, so
    it is the same finding -- and it is named once, not once per open place
    the same man could have taken.
    """
    ls, home, first = build(session)
    rostered(session, home, first, "Guard", slots=GUARD, pro_team=10, slot="G")
    rostered(session, home, first, "Spare", slots=ANY, pro_team=11, slot="BE")
    games(session, 10, [5])
    games(session, 11, [5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert report.starts == 2 and report.actual_starts == 1
    assert len(report.fix) == 1, "one man, one place to fix, although two are open"
    fix = report.fix[0]
    assert fix.seat.player is None and fix.seat.slot in ("F", "UT")
    assert [man.name for man in fix.instead] == ["Spare"]


def test_a_man_on_injured_reserve_is_never_seated(session: Session) -> None:
    """Even with a game on the schedule and a place going begging."""
    ls, home, first = build(session, injured_reserve=1)
    rostered(session, home, first, "Stashed", slots=ANY, pro_team=10, slot="IR")
    rostered(session, home, first, "Guard", slots=GUARD, pro_team=11, slot="G")
    games(session, 10, [5])
    games(session, 11, [5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert [place.player.name for place in report.lineup if place.player] == ["Guard"]
    assert [man.name for man in report.injured_reserve] == ["Stashed"]
    assert report.injured_reserve[0].status == ON_IR
    assert [man.player.name for man in report.benched] == []
    assert report.fix == (), "a man on injured reserve is not an answer to an open place"


def test_a_bench_only_man_says_so_and_an_outranked_one_names_who_beat_him(
    session: Session,
) -> None:
    """The two reasons a man with a game is not in the lineup.

    A man ESPN lists as eligible for no starting place is bench-only today
    whatever the roster looks like. Everyone else lost the places he fits to
    better men, and they are named, because "why is he not starting" is the
    question a proposal has to answer for itself.
    """
    ls, home, first = build(session)
    rostered(session, home, first, "Star", slots=GUARD, pro_team=10, per_game=TWENTY_POINTS)
    rostered(session, home, first, "Forward", slots=FORWARD, pro_team=11, per_game=FIFTEEN_POINTS)
    rostered(session, home, first, "Centre", slots=CENTRE, pro_team=12, per_game=TWELVE_POINTS)
    rostered(session, home, first, "Scrub", slots=GUARD, pro_team=13, per_game=TEN_POINTS)
    rostered(session, home, first, "Nobody", slots=BENCH_ONLY, pro_team=14, per_game=TEN_POINTS)
    for pro_team in (10, 11, 12, 13, 14):
        games(session, pro_team, [5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert sorted(man.name for man in report.starters) == ["Centre", "Forward", "Star"]
    reasons = {benched.player.name: benched for benched in report.benched}
    assert set(reasons) == {"Scrub", "Nobody"}
    assert reasons["Scrub"].reason == OUTRANKED
    behind = [man.name for man in reasons["Scrub"].behind]
    assert len(behind) == 2, "the guard place and the utility place are the two he fits"
    assert behind[0] == "Star", "best first"
    assert "Nobody" not in behind, "only men who took a place he could have had"
    assert reasons["Nobody"].reason == NO_SLOT
    assert reasons["Nobody"].behind == ()


def test_two_level_men_are_separated_the_way_the_week_separates_them(
    session: Session,
) -> None:
    """The seating is `stream.seat`, and its tie-break is the lower player id.

    Not a preference, but it has to be *a* rule and the same one the week
    uses: the week page and this page would otherwise name different men on
    the same day with the same roster. Three identical guards against a
    guard place and a utility place, so exactly one of them has to lose, and
    it is the one who was stored last.
    """
    ls, home, first = build(session)
    ids = [
        rostered(session, home, first, name, slots=GUARD, pro_team=pro_team).id
        for name, pro_team in (("Alike A", 10), ("Alike B", 11), ("Alike C", 12))
    ]
    assert ids == sorted(ids)
    for pro_team in (10, 11, 12):
        games(session, pro_team, [5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert sorted(man.name for man in report.starters) == ["Alike A", "Alike B"]
    assert [benched.player.name for benched in report.benched] == ["Alike C"]


def test_a_day_with_no_nba_games_seats_nobody_and_says_so(session: Session) -> None:
    """The All-Star break: an empty lineup that is right, not a failure."""
    ls, home, first = build(session)
    rostered(session, home, first, "Guard", slots=GUARD, pro_team=10, slot="BE")
    games(session, 10, [6])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert report.teams_playing == 0
    assert report.starts == 0
    assert report.empty_slots == ("G", "F", "UT")
    assert [man.name for man in report.idle] == ["Guard"]
    assert report.actual_known is True, "the team's rows for the day are there"
    assert report.fix == ()


def test_a_day_the_ingest_has_not_reached_says_the_lineup_is_unknown(
    session: Session,
) -> None:
    """No stored lineup for today is not the same as a lineup set to nobody."""
    ls, home, first = build(session)
    rostered(session, home, first, "Guard", slots=GUARD, pro_team=10, slot="G", day=4)
    games(session, 10, [4, 5])

    report = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert report.actual_known is False
    assert report.actual == ()
    assert report.fix == ()
    assert report.actual_starts == 0
    assert report.edge == pytest.approx(0.0), "nothing to compare against is not a gain"
    assert [place.player.name for place in report.lineup if place.player] == ["Guard"]


def test_the_day_reads_nothing_after_itself(session: Session) -> None:
    """The rehearsal's first check, for this feature (docs/inseason_rehearsal.md).

    A week with four days left, games on every one of them, box scores and
    lineup rows for the days after today, and a man who plays only later.
    The day's answer is taken, then every row the season went on to write is
    deleted and it is taken again. A report that read one row of the future
    would move; this one does not.
    """
    ls, home, first = build(session)
    rostered(session, home, first, "Today", slots=GUARD, pro_team=10, slot="G")
    rostered(session, home, first, "Later", slots=FORWARD, pro_team=11, slot="BE")
    rostered(session, home, first, "Spare", slots=ANY, pro_team=12, slot="BE")
    games(session, 10, [5, 6, 7])
    games(session, 11, [6, 7])
    games(session, 12, [5, 6])
    for day in (6, 7):
        for name in ("Today", "Later"):
            who = session.query(Player).filter(Player.name == name).one()
            held(session, home, first, who, day, slot="UT", stats={"PTS": 30.0})
    session.commit()

    before = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    session.execute(text("DELETE FROM player_game_stats WHERE scoring_period > 5"))
    session.execute(text("DELETE FROM daily_lineup_slots WHERE scoring_period > 5"))
    session.execute(text("DELETE FROM pro_team_games WHERE scoring_period > 5"))
    session.commit()
    # The per-game lines are memoized for the life of a session, and a
    # cached line would pass this test without reading anything at all.
    clear_projection_cache()

    after = today_lineup(session, ls, HOME, today=5, distributions=WEEK)

    assert sorted(man.name for man in before.starters) == ["Spare", "Today"]
    assert [benched.player.name for benched in before.benched] == []
    assert [man.name for man in before.idle] == ["Later"], "his games are all after today"
    assert after == before
