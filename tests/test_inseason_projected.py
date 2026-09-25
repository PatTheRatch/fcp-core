"""The projected standings on constructed seasons.

A four-team league on a small lineup, so a full day is one man and a week's
line is arithmetic a reader can do. The cases are the ones a forecast can get
wrong quietly: a head-to-head that is not a coin when it should be, a
simulation that is not reproducible, a record that does not add up, a payload
that changes when the future is deleted, and the week in play counting a day
twice.
"""

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyLineupSlot,
    LeagueSeason,
    LeagueSeasonCategory,
    Matchup,
    MatchupPeriod,
    MatchupTeamStat,
    Player,
    PlayerGameStat,
    Team,
)
from app.inseason import projected as projected_module
from app.inseason import projected_calibration as calibration
from app.inseason.projected import bye_seats, final_table, project_standings
from app.pickups import stream
from app.pickups.stream import head_to_head
from app.scoring.lines import CategoryLine
from app.scoring.ranking import ranking_rule
from tests.pickups_db import (
    ANY,
    SMALL_LINEUP,
    WEEK,
    clear_schedule,
    configure,
    eligible,
    games,
    played,
    projected,
    snapshot,
)
from tests.scoring_db import held, league_season, matchup, player

#: Both sides level in every category, as a week's posted totals.
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

#: A useful per-game line, and a weaker one.
GOOD = {
    "PTS": 25.0,
    "REB": 10.0,
    "AST": 6.0,
    "STL": 1.5,
    "BLK": 1.0,
    "3PM": 2.5,
    "TO": 2.0,
    "FGM": 9.0,
    "FGA": 18.0,
    "FTM": 5.0,
    "FTA": 6.0,
}
POOR = {key: value * 0.4 for key, value in GOOD.items()}


@pytest.fixture
def session(scoring_session: Session) -> Iterator[Session]:
    clear_schedule(scoring_session)
    yield scoring_session


def _stock(
    session: Session,
    team: Team,
    periods: list[MatchupPeriod],
    name: str,
    per_game: Mapping[str, float],
    *,
    pro_team: int,
    days: list[int],
    lineup_day: int | None = 1,
) -> Player:
    """One man on `team`, with a game on each of `days` and a known rate.

    `lineup_day` is the day he is recorded as held on, which is where the
    roster is read from; a test that writes its own lineup days passes None.
    """
    who = player(session, name)
    eligible(session, who, ANY)
    snapshot(session, who, pro_team_id=pro_team, on_team_id=team.espn_team_id)
    projected(session, who, 70, per_game)
    games(session, pro_team, days)
    if lineup_day is not None:
        held(session, team, periods[0], who, lineup_day, slot="UT")
    return who


def build(
    session: Session, *, periods: int = 3, regular: int | None = None
) -> tuple[LeagueSeason, list[Team], list[MatchupPeriod]]:
    """Four teams, three weeks, every week paired, nobody on a bye."""
    ls, teams, windows = league_season(
        session,
        team_names=("Alpha", "Bravo", "Charlie", "Delta"),
        periods=periods,
        regular_season_periods=regular if regular is not None else periods,
        days_per_period=7,
    )
    configure(ls, lineup=SMALL_LINEUP, bench=1, injured_reserve=0)
    ls.playoff_team_count = 2
    for index, window in enumerate(windows):
        # A round robin: Alpha meets each of the others in turn, and the two
        # left over meet each other, so nobody is ever on a bye.
        second = 1 + index % 3
        others = [place for place in range(1, 4) if place != second]
        matchup(session, window, teams[0], teams[second])
        matchup(session, window, teams[others[0]], teams[others[1]])
    session.flush()
    return ls, teams, windows


# ---------------------------------------------------------------------------
# the head-to-head, on a hand fixture
# ---------------------------------------------------------------------------


def test_equal_lines_are_a_coin_in_every_category() -> None:
    """Two identical projected weeks: every category is even money."""
    line = CategoryLine(dict(EVEN))
    chances = head_to_head(line, line, WEEK, 7)
    assert set(chances) == {d.abbreviation for d in WEEK}
    for category, p in chances.items():
        assert p == pytest.approx(0.5), category


def test_turnovers_are_reversed() -> None:
    """Fewer turnovers wins the category; more of anything else does."""
    mine = CategoryLine({**EVEN, "TO": 30.0, "PTS": 340.0})
    theirs = CategoryLine(dict(EVEN))
    chances = head_to_head(mine, theirs, WEEK, 7)
    assert chances["TO"] > 0.5
    assert chances["PTS"] > 0.5
    # The mirror image: the same edges read the other way round.
    back = head_to_head(theirs, mine, WEEK, 7)
    assert back["TO"] == pytest.approx(1.0 - chances["TO"])
    assert back["PTS"] == pytest.approx(1.0 - chances["PTS"])


def test_a_rate_is_read_on_the_attempts_not_the_makes() -> None:
    """FG% is makes over attempts: more makes on more attempts can be worse."""
    theirs = CategoryLine(dict(EVEN))
    # More made shots, but on many more attempts: a worse percentage.
    volume = CategoryLine({**EVEN, "FGM": 130.0, "FGA": 320.0})
    assert head_to_head(volume, theirs, WEEK, 7)["FG%"] < 0.5
    # The same makes on fewer attempts: a better one.
    efficient = CategoryLine({**EVEN, "FGA": 200.0})
    assert head_to_head(efficient, theirs, WEEK, 7)["FG%"] > 0.5


def test_a_settled_category_is_decided_outright() -> None:
    """With no days left the better number wins and a level one is a coin."""
    theirs = CategoryLine(dict(EVEN))
    ahead = CategoryLine({**EVEN, "PTS": 301.0})
    assert head_to_head(ahead, theirs, WEEK, 0)["PTS"] == 1.0
    assert head_to_head(theirs, ahead, WEEK, 0)["PTS"] == 0.0
    assert head_to_head(theirs, theirs, WEEK, 0)["PTS"] == 0.5


def test_bye_seats_is_the_field_rounded_up_to_a_bracket() -> None:
    assert bye_seats(7) == 1
    assert bye_seats(8) == 0
    assert bye_seats(6) == 2
    assert bye_seats(0) == 0


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def test_banked_plus_expected_is_the_projected_record(session: Session) -> None:
    """The three numbers a manager reads have to add up, for every team."""
    ls, teams, windows = build(session)
    for index, team in enumerate(teams):
        _stock(
            session,
            team,
            windows,
            f"man {index}",
            GOOD if index < 2 else POOR,
            pro_team=index + 1,
            days=list(range(1, 22)),
        )
    session.flush()
    report = project_standings(session, ls, 8, distributions=WEEK, n_sims=200)
    assert report.periods == (2, 3)
    for team in report.teams:
        won, lost = team.expected
        assert team.projected_record[0] == pytest.approx(team.banked[0] + won)
        assert team.projected_record[1] == pytest.approx(team.banked[1] + lost)
        # Nine categories a contested week, and no more.
        assert won + lost == pytest.approx(9 * len(team.contested_weeks))


def test_the_simulation_is_the_same_twice_and_sums_to_one(session: Session) -> None:
    """One seed, one answer; and a team finishes somewhere, with probability 1."""
    ls, teams, windows = build(session)
    for index, team in enumerate(teams):
        _stock(
            session,
            team,
            windows,
            f"man {index}",
            GOOD if index < 2 else POOR,
            pro_team=index + 1,
            days=list(range(1, 22)),
        )
    session.flush()
    first = project_standings(session, ls, 8, distributions=WEEK, n_sims=500)
    again = project_standings(session, ls, 8, distributions=WEEK, n_sims=500)
    assert [one.finishes for one in first.teams] == [one.finishes for one in again.teams]
    for team in first.teams:
        assert sum(team.finishes) == pytest.approx(1.0)
        assert len(team.finishes) == len(first.teams)
    # A different seed moves the numbers but not the fact that they are a
    # distribution: nothing here depends on the seed being lucky.
    other = project_standings(session, ls, 8, distributions=WEEK, n_sims=500, seed=17)
    for team in other.teams:
        assert sum(team.finishes) == pytest.approx(1.0)
    # And every place is filled exactly once across the league, in expectation.
    for place in range(len(first.teams)):
        assert sum(one.finishes[place] for one in first.teams) == pytest.approx(1.0)


def test_a_clinched_team_reads_as_certain(session: Session) -> None:
    """Nothing left to play and a winning record: the playoff odds are one."""
    ls, teams, windows = build(session, periods=3)
    for index, team in enumerate(teams):
        _stock(
            session,
            team,
            windows,
            f"man {index}",
            GOOD,
            pro_team=index + 1,
            days=list(range(1, 22)),
        )
    # The first two weeks are settled, both won by the top two teams.
    _settle(session, windows[0], teams[0], teams[1], winner="HOME")
    _settle(session, windows[0], teams[2], teams[3], winner="HOME")
    _settle(session, windows[1], teams[0], teams[2], winner="HOME")
    _settle(session, windows[1], teams[1], teams[3], winner="HOME")
    session.flush()
    report = project_standings(session, ls, 15, distributions=WEEK, n_sims=500)
    by_name = {one.name: one for one in report.teams}
    # Alpha has won both; two of four make the playoffs and one week is left,
    # so it cannot be caught by the two teams that have lost both.
    assert by_name["Alpha"].banked_matchups == (2, 0, 0)
    assert by_name["Alpha"].playoff_odds == pytest.approx(1.0)
    assert by_name["Delta"].banked_matchups == (0, 2, 0)
    assert by_name["Delta"].playoff_odds == pytest.approx(0.0)


def _settle(
    session: Session,
    period: MatchupPeriod,
    home: Team,
    away: Team,
    *,
    winner: str,
) -> None:
    """Give an existing matchup a result, so it counts as banked."""
    row = session.scalar(
        select(Matchup).where(
            Matchup.matchup_period_id == period.id,
            Matchup.home_team_id == home.id,
            Matchup.away_team_id == away.id,
        )
    )
    assert row is not None, "no such matchup to settle"
    row.winner = winner
    session.execute(MatchupTeamStat.__table__.delete().where(MatchupTeamStat.matchup_id == row.id))
    categories = session.scalars(
        select(LeagueSeasonCategory)
        .where(LeagueSeasonCategory.league_season_id == period.league_season_id)
        .order_by(LeagueSeasonCategory.position)
    ).all()
    for category in categories:
        for team, at_home in ((home, True), (away, False)):
            session.add(
                MatchupTeamStat(
                    matchup_id=row.id,
                    team_id=team.id,
                    abbreviation=category.abbreviation,
                    value=1.0,
                    result="WIN" if (winner == "HOME") == at_home else "LOSS",
                    league_season_category_id=category.id,
                )
            )
    session.flush()


def test_the_week_in_play_counts_the_days_left_and_not_the_days_played(
    session: Session,
) -> None:
    """The current period starts from what is posted and adds only what is left.

    The rule `app.pickups.state._posted` sets and the rehearsal's finding 1
    was about: a report for the morning of day N counts this period's days
    **before** N as posted and projects from N.
    """
    ls, teams, windows = build(session)
    men = [
        _stock(
            session,
            team,
            windows,
            f"man {index}",
            GOOD,
            pro_team=index + 1,
            days=list(range(1, 22)),
            lineup_day=None,
        )
        for index, team in enumerate(teams)
    ]
    # A played season: a box score on every day. The database knowing about
    # days after today is what makes this a replay rather than a live morning
    # (`app.pickups.state.is_live`), which is the branch under test.
    for day in range(1, 22):
        for who in men:
            played(session, who, day, 30.0, GOOD)
    # Day 8 is the first day of period 2; days 8 and 9 are in the lineup, so a
    # report for day 10 holds two games of posted counts and projects five.
    for day in range(1, 10):
        window = windows[0] if day < 8 else windows[1]
        for team, who in zip(teams, men, strict=True):
            held(session, team, window, who, day, slot="UT")
    session.flush()
    report = project_standings(session, ls, 10, distributions=WEEK, n_sims=100)
    week = report.teams[0].weeks[0]
    assert week.in_play is True
    assert week.days_remaining == 5
    # Two games posted plus five projected: seven games' worth of points, and
    # never nine (which is what counting the played days twice would give).
    assert week.projected.get("PTS") == pytest.approx(GOOD["PTS"] * 7, rel=0.2)


def test_no_look_ahead_when_the_future_is_deleted(session: Session) -> None:
    """The payload is identical with everything after today removed.

    "The future" is what had not happened: the box scores, the lineup days and
    the results of weeks still to be played. The **pairings** stay, because
    ESPN stores the whole season's schedule in advance and a manager can read
    it; they are the one thing here that is legitimately about the future.

    `today` is the first day of a matchup period deliberately. In the middle
    of one, deleting the future also deletes what the period has posted so
    far, and `app.pickups.state.is_live` then flips the report from a replay
    to a live morning -- a real and wanted difference in where the posted
    totals are read from, and not a look-ahead. On the first day of a period
    nothing is posted either way, so the two are comparable.
    """
    ls, teams, windows = build(session)
    men = [
        _stock(
            session,
            team,
            windows,
            f"man {index}",
            GOOD if index < 2 else POOR,
            pro_team=index + 1,
            days=list(range(1, 22)),
            lineup_day=None,
        )
        for index, team in enumerate(teams)
    ]
    # Days 1 to 20 all played and all in a lineup, including days after today.
    for day in range(1, 21):
        window = windows[min(2, (day - 1) // 7)]
        for team, who in zip(teams, men, strict=True):
            held(session, team, window, who, day, slot="UT", stats=GOOD)
    _settle(session, windows[0], teams[0], teams[1], winner="HOME")
    _settle(session, windows[0], teams[2], teams[3], winner="HOME")
    _settle(session, windows[1], teams[0], teams[2], winner="AWAY")
    session.flush()

    today = 8
    before = project_standings(session, ls, today, distributions=WEEK, n_sims=300)

    # Delete the future: box scores and lineup days from today on, and the
    # posted totals and results of any week that has not finished.
    session.execute(PlayerGameStat.__table__.delete().where(PlayerGameStat.scoring_period >= today))
    session.execute(
        DailyLineupSlot.__table__.delete().where(DailyLineupSlot.scoring_period >= today)
    )
    unfinished = [
        row.id
        for row in session.execute(
            Matchup.__table__.select()
            .join(MatchupPeriod, MatchupPeriod.id == Matchup.matchup_period_id)
            .where(MatchupPeriod.final_scoring_period >= today)
        ).all()
    ]
    session.execute(
        MatchupTeamStat.__table__.delete().where(MatchupTeamStat.matchup_id.in_(unfinished))
    )
    session.execute(
        Matchup.__table__.update().where(Matchup.id.in_(unfinished)).values(winner="UNDECIDED")
    )
    session.flush()

    after = project_standings(session, ls, today, distributions=WEEK, n_sims=300)
    assert _shape(before) == _shape(after)


def _shape(report: object) -> list:
    """Everything a page draws, as plain values, for an equality assertion."""
    teams = report.teams  # type: ignore[attr-defined]
    return [
        [
            one.name,
            one.banked,
            one.banked_matchups,
            [round(value, 9) for value in one.finishes],
            round(one.playoff_odds, 9),
            [
                (
                    week.period,
                    week.opponent_name,
                    week.days_remaining,
                    week.in_play,
                    {k: round(v, 9) for k, v in week.probabilities.items()},
                    {k: round(v, 6) for k, v in week.projected.counts.items()},
                )
                for week in one.weeks
            ],
        ]
        for one in teams
    ]


def test_the_playoff_rounds_are_not_projected_during_the_regular_season(
    session: Session,
) -> None:
    """A bracket that depends on seeding is named, not guessed at."""
    ls, teams, windows = build(session, periods=4, regular=3)
    for index, team in enumerate(teams):
        _stock(
            session,
            team,
            windows,
            f"man {index}",
            GOOD,
            pro_team=index + 1,
            days=list(range(1, 29)),
        )
    session.flush()
    report = project_standings(session, ls, 8, distributions=WEEK, n_sims=100)
    assert report.periods == (2, 3)
    assert report.playoffs_projected is False
    assert "seeding" in report.playoff_note
    # Once the regular season is over the horizon is the playoff rounds, and
    # their stored pairings are the real ones.
    playoffs = project_standings(session, ls, 22, distributions=WEEK, n_sims=100)
    assert playoffs.periods == (4,)
    assert playoffs.playoffs_projected is True
    assert playoffs.playoff_note == ""


def test_final_table_is_the_settled_record_in_the_league_s_own_order(
    session: Session,
) -> None:
    ls, teams, windows = build(session)
    # Alpha wins all three; Delta wins the two it is not playing Alpha in;
    # Bravo wins one; Charlie wins none, each nine categories to nothing. So
    # the table is Alpha, Delta, Bravo, Charlie on category win share, which
    # here is also the matchup order; the next test pulls the two apart.
    _settle(session, windows[0], teams[0], teams[1], winner="HOME")
    _settle(session, windows[0], teams[2], teams[3], winner="AWAY")
    _settle(session, windows[1], teams[0], teams[2], winner="HOME")
    _settle(session, windows[1], teams[1], teams[3], winner="AWAY")
    _settle(session, windows[2], teams[0], teams[3], winner="HOME")
    _settle(session, windows[2], teams[1], teams[2], winner="HOME")
    session.flush()
    table = final_table(session, ls, 10_000)
    assert set(table) == {team.espn_team_id for team in teams}
    assert table == (
        teams[0].espn_team_id,
        teams[3].espn_team_id,
        teams[1].espn_team_id,
        teams[2].espn_team_id,
    )


def test_a_day_past_the_last_one_reads_as_the_last_week(session: Session) -> None:
    """The horizon clamps, as `app.pickups.judge.horizon` does for every report.

    A page asked for a day after the season ended gets the last week rather
    than an error, which is what the season page and the week page both do.
    """
    ls, teams, windows = build(session)
    for index, team in enumerate(teams):
        _stock(session, team, windows, f"man {index}", GOOD, pro_team=index + 1, days=[20, 21])
    session.flush()
    report = project_standings(session, ls, 500, distributions=WEEK, n_sims=10)
    assert report.as_of == 21
    assert report.periods == (3,)


def test_a_season_with_no_matchup_periods_refuses(session: Session) -> None:
    ls, _teams, windows = build(session)
    for window in windows:
        session.delete(window)
    session.flush()
    with pytest.raises(ValueError):
        project_standings(session, ls, 8, distributions=WEEK, n_sims=10)


# ---------------------------------------------------------------------------
# the published record, and the sentence that carries it
# ---------------------------------------------------------------------------


def _pct(value: float) -> int:
    """A share as a whole percent, rounded the way a person writes it.

    Half up, not Python's half-to-even: the note says 0.355 is "36%", and a
    guard that read it as 35% would fail on the sentence a reader agrees with.
    """
    return int(value * 100 + 0.5)


def test_the_published_note_says_what_the_published_numbers_say() -> None:
    """The pages print these two sentences verbatim, so they must not drift.

    A forecast shown without its record is the thing docs/product.md's "a
    tool, not gospel" forbids, and a record that no longer matched the run
    behind it would be worse than none. This holds both sentences to the data
    beside them, and keeps the jargon out of them.
    """
    # The one line on the page, and the number it quotes.
    assert calibration.RECORD_ERROR["half"] == pytest.approx(6.3)
    assert "6.3 categories" in calibration.SHORT_NOTE
    assert "halfway mark" in calibration.SHORT_NOTE

    # The long note's claims, each against its own constant. Since the spread
    # was widened (2026-09-23) the note quotes the bands the forecast actually
    # lives in rather than the ends it now almost never reaches.
    assert f"{calibration.N_CATEGORY_CALLS:,} calls" in calibration.CALIBRATION_NOTE
    bands = {row.band: row for row in calibration.CATEGORY_RELIABILITY}
    for low, said, happened in ((0.1, 15, 18), (0.3, 35, 35), (0.6, 65, 65), (0.8, 85, 82)):
        row = bands[(low, round(low + 0.1, 1))]
        assert _pct(low + 0.05) == said, "the note names the band by its middle"
        assert _pct(row.happened) == happened
    assert "gave a 15% chance were won 18%" in calibration.CALIBRATION_NOTE
    assert "ones it gave 35% were won 35%" in calibration.CALIBRATION_NOTE
    assert "65% were won 65%, and 85% were won 82%" in calibration.CALIBRATION_NOTE

    # The one end it still overclaims, and how little of its weight is there.
    cocky = calibration.CATEGORY_RELIABILITY[-1]
    assert cocky.band == (0.9, 1.0)
    assert _pct(cocky.happened) == 84
    assert cocky.n < bands[(0.4, 0.5)].n / 10, "the wide model rarely goes there"
    assert "above 90% come in about 84% of the time" in calibration.CALIBRATION_NOTE

    best_odds = calibration.PLAYOFF_RELIABILITY[-1]
    assert _pct(best_odds.happened) == 100
    assert "better than 90% made it every time" in calibration.CALIBRATION_NOTE
    middling = next(row for row in calibration.PLAYOFF_RELIABILITY if row.band == (0.6, 0.7))
    assert _pct(middling.happened) == 49
    assert "given 60-70% made it 49%" in calibration.CALIBRATION_NOTE
    floor = calibration.PLAYOFF_RELIABILITY[0]
    assert floor.band == (0.0, 0.1) and _pct(floor.happened) == 5
    assert "under 10% made it 5% of the time" in calibration.CALIBRATION_NOTE
    assert "ranked on category win share" in calibration.CALIBRATION_NOTE
    # The pre-R6 table is kept as evidence and is the one the old note quoted.
    before = next(
        row for row in calibration.PLAYOFF_RELIABILITY_BY_MATCHUPS if row.band == (0.6, 0.7)
    )
    assert _pct(before.happened) == 57
    assert sum(row.n for row in calibration.PLAYOFF_RELIABILITY) == sum(
        row.n for row in calibration.PLAYOFF_RELIABILITY_BY_MATCHUPS
    )

    # It is worse than a coin at nothing, and it does not claim to be better
    # than the run says.
    assert calibration.BRIER < calibration.COIN_BRIER
    assert calibration.WINNER_HIT_RATE > 0.5
    assert "overconfident" in calibration.CALIBRATION_NOTE
    for jargon in ("Brier", "Poisson", "sigma", "Monte Carlo", "variance"):
        assert jargon not in calibration.CALIBRATION_NOTE
    for verdict in ("should", "recommend", "guaranteed"):
        assert verdict not in calibration.CALIBRATION_NOTE.lower()


def test_the_shipped_widening_is_the_one_the_published_score_was_earned_on() -> None:
    """The published score and the model that earned it cannot drift apart.

    This used to be the guard that nobody had quietly widened the spread. The
    owner widened it on 2026-09-23 and the whole calibration was re-run on it
    (docs/spread_revision.md), so the guard is now the other way round: the
    factor the engine ships and the factor this module says it ships have to
    be the same one.

    `WIDENED` is no longer held equal to `BRIER`, and the reason is worth
    stating. Those three rows are the run of 2026-09-22, which was
    status-blind; since 2026-09-24 a replayed morning reads the NBA's own
    injury report (docs/replay_status.md) and the shipped model scores 0.2184
    rather than 0.2179. `WIDENED` is kept as the evidence the owner chose the
    factor on, so it must stay where it was; what still has to hold is that
    the two scores are the same model to three decimals, and that the
    ordering the choice rested on survived.
    """
    assert stream.SPREAD_SCALE == calibration.SHIPPED_SCALE
    assert calibration.WIDENED[calibration.SHIPPED_SCALE] == pytest.approx(
        calibration.BRIER, abs=0.001
    )
    # The other two rows are the diagnostics it was chosen against, and the
    # choice is only defensible while they are worse.
    assert calibration.WIDENED[2.0] < calibration.WIDENED[1.4142] < calibration.WIDENED[1.0]
    assert calibration.WIDENED[1.4142] > calibration.BRIER
    # And the engine still has no second scale of its own to have been turned.
    assert not hasattr(projected_module, "SIGMA_SCALE")


# ---------------------------------------------------------------------------
# the order: the league's ranking rule, not matchups won
# ---------------------------------------------------------------------------


def _split(
    session: Session,
    period: MatchupPeriod,
    home: Team,
    away: Team,
    *,
    home_takes: int,
) -> None:
    """Settle an existing matchup `home_takes` categories to the rest."""
    row = session.scalar(
        select(Matchup).where(
            Matchup.matchup_period_id == period.id,
            Matchup.home_team_id == home.id,
            Matchup.away_team_id == away.id,
        )
    )
    assert row is not None, "no such matchup to settle"
    row.winner = "HOME" if home_takes >= 5 else "AWAY"
    row.home_categories_won, row.home_categories_lost = home_takes, 9 - home_takes
    session.execute(MatchupTeamStat.__table__.delete().where(MatchupTeamStat.matchup_id == row.id))
    categories = session.scalars(
        select(LeagueSeasonCategory)
        .where(LeagueSeasonCategory.league_season_id == period.league_season_id)
        .order_by(LeagueSeasonCategory.position)
    ).all()
    for position, category in enumerate(categories):
        home_won = position < home_takes
        for team, won in ((home, home_won), (away, not home_won)):
            session.add(
                MatchupTeamStat(
                    matchup_id=row.id,
                    team_id=team.id,
                    abbreviation=category.abbreviation,
                    value=1.0,
                    result="WIN" if won else "LOSS",
                    league_season_category_id=category.id,
                )
            )
    session.flush()


def test_the_final_table_is_category_share_where_matchups_say_otherwise(
    session: Session,
) -> None:
    """The bug, pinned. Alpha wins two matchups five to four and loses the
    third nought to nine: two matchups, ten categories of twenty-seven.
    Bravo wins one matchup nine to nothing. By matchups Alpha is ahead of
    Bravo; by categories -- how a head-to-head each-category league is
    ranked -- Bravo is ahead of Alpha."""
    ls, teams, windows = build(session)
    alpha, bravo, charlie, delta = teams
    # Round robin: period 1 Alpha-Bravo, Charlie-Delta; 2 Alpha-Charlie,
    # Bravo-Delta; 3 Alpha-Delta, Bravo-Charlie.
    _split(session, windows[0], alpha, bravo, home_takes=5)
    _split(session, windows[0], charlie, delta, home_takes=5)
    _split(session, windows[1], alpha, charlie, home_takes=5)
    _split(session, windows[1], bravo, delta, home_takes=9)
    _split(session, windows[2], alpha, delta, home_takes=0)
    _split(session, windows[2], bravo, charlie, home_takes=4)
    session.flush()
    # Categories: Alpha 10-17, Bravo 17-10, Charlie 14-13, Delta 13-14.
    # Matchups:   Alpha 2-1,  Bravo 1-2,  Charlie 2-1,  Delta 1-2.
    table = final_table(session, ls, 10_000)
    assert table == tuple(team.espn_team_id for team in (bravo, charlie, delta, alpha)), (
        "category win share, not matchups won"
    )


def test_a_tie_of_share_goes_to_the_team_that_won_the_meeting(session: Session) -> None:
    """Bravo and Charlie both finish 14-13; Charlie beat Bravo five to four,
    so Charlie is ahead, whatever categories won alone would say."""
    ls, teams, windows = build(session)
    alpha, bravo, charlie, delta = teams
    _split(session, windows[0], alpha, bravo, home_takes=4)  # Bravo 5-4
    _split(session, windows[0], charlie, delta, home_takes=4)  # Charlie 4-5
    _split(session, windows[1], alpha, charlie, home_takes=4)  # Charlie 5-4
    _split(session, windows[1], bravo, delta, home_takes=5)  # Bravo 5-4
    _split(session, windows[2], alpha, delta, home_takes=9)
    _split(session, windows[2], bravo, charlie, home_takes=4)  # Charlie 5-4
    session.flush()
    table = final_table(session, ls, 10_000)
    # Alpha 17-10; Bravo 14-13; Charlie 14-13; Delta 9-18.
    assert table == tuple(team.espn_team_id for team in (alpha, charlie, bravo, delta))


def _certain(home: int, away: int, home_takes: int) -> projected_module._Draw:
    """A simulated week whose result is known: `home_takes` of nine."""
    cumulative = tuple(0.0 if taken < home_takes else 1.0 for taken in range(10))
    return projected_module._Draw(home=home, away=away, cumulative=cumulative, contested=9)


def test_the_simulated_table_is_seeded_by_the_rule() -> None:
    """Nothing left to play and the banked record decides: team 0 has the
    matchups, team 1 the categories. The category league seeds team 1 first
    every time; a most-categories league seeds team 0 first."""
    categories = ranking_rule(LeagueSeason(scoring_type="H2H_CATEGORY", raw_settings={}))
    matchups = ranking_rule(LeagueSeason(scoring_type="H2H_MOST_CATEGORIES", raw_settings={}))
    banked_matchups = [(3, 0, 0), (1, 2, 0)]
    banked_categories = [(15.0, 12.0), (18.0, 9.0)]
    by_categories, _ = projected_module._simulate(
        [], banked_matchups, banked_categories, teams=2, n_sims=50, seed=1, rule=categories
    )
    by_matchups, _ = projected_module._simulate(
        [], banked_matchups, banked_categories, teams=2, n_sims=50, seed=1, rule=matchups
    )
    assert by_categories == [[0.0, 1.0], [1.0, 0.0]]
    assert by_matchups == [[1.0, 0.0], [0.0, 1.0]]


def test_the_simulation_reads_the_meetings_it_drew_for_a_tie_of_share() -> None:
    """Level on share after the last week, the two are separated by their
    record against each other: the banked meeting and the one simulated."""
    rule = ranking_rule(LeagueSeason(scoring_type="H2H_CATEGORY", raw_settings={}))
    # Team 0 takes the simulated week 5-4, so both end 14-13, and team 0 is
    # 5-4 up on the meeting that decided it.
    finishes, _ = projected_module._simulate(
        [_certain(0, 1, 5)],
        [(0, 1, 0), (1, 0, 0)],
        [(9.0, 9.0), (10.0, 8.0)],
        teams=2,
        n_sims=20,
        seed=1,
        rule=rule,
    )
    assert finishes[0] == [1.0, 0.0]
    # A banked meeting the other way, 9-0 to team 1, outweighs it: 5-13.
    finishes, _ = projected_module._simulate(
        [_certain(0, 1, 5)],
        [(0, 1, 0), (1, 0, 0)],
        [(9.0, 9.0), (10.0, 8.0)],
        teams=2,
        n_sims=20,
        seed=1,
        rule=rule,
        banked_meetings={(0, 1): (0.0, 9.0, 0.0), (1, 0): (9.0, 0.0, 0.0)},
    )
    assert finishes[0] == [0.0, 1.0]


def test_the_rule_does_not_move_the_simulated_matchup_record() -> None:
    """The draws come in the same sequence whatever the order, so the mean
    matchup record is the same number under either rule -- what makes the
    per-team figures byte-identical across the ranking revision."""
    categories = ranking_rule(LeagueSeason(scoring_type="H2H_CATEGORY", raw_settings={}))
    matchups = ranking_rule(LeagueSeason(scoring_type="H2H_MOST_CATEGORIES", raw_settings={}))
    coin = tuple(projected_module._category_wins([0.5] * 9))
    draws = [
        projected_module._Draw(home=0, away=1, cumulative=coin, contested=9),
        projected_module._Draw(home=2, away=3, cumulative=coin, contested=9),
        projected_module._Draw(home=0, away=2, cumulative=coin, contested=9),
        projected_module._Draw(home=1, away=3, cumulative=coin, contested=9),
    ]
    args = ([(1, 0, 0)] * 4, [(5.0, 4.0)] * 4)
    one = projected_module._simulate(draws, *args, teams=4, n_sims=500, seed=7, rule=categories)
    two = projected_module._simulate(draws, *args, teams=4, n_sims=500, seed=7, rule=matchups)
    assert one[1] == two[1]
