"""One player's card: everything a screen shows when you hover his name.

The draft room has had a card of this shape since the auction was built
(`app.draft.session.card`): the name, what he is, the nine as a strip, and
what the number under him rests on. In the auction those numbers are dollars
and a board price. In season they are the ones every in-season page is
already drawn from -- his per-game line, the games he has left, the games he
has in the playoff weeks, whether he is hurt and when he is back, and how
many games of his own stand behind the projection -- so the card is built
here, once, rather than three times inside three pages.

WHY IT IS THE SAME NUMBERS AND NOT NEW ONES

Nothing here computes anything a report does not. `per_game` is
`app.pickups.projection.per_game_line`, the rate every weekly line is scaled
from; `weekly` is `rest_of_season_line` over the same horizon the plans use,
divided by the weeks in it; `value` is the league standard
(`app.pickups.judge.Standard`), the currency a pickup and a trade are both
judged in. So a card opened from the trade page says exactly what the trade
page's own table says, to the last decimal, and a card that disagreed with
the page it was opened from would be worse than no card.

`games_so_far`, `had_projection` and `projection_source` are
`app.scoring.knowable`'s, and `thin` is the trade report's own threshold
(`THIN_GAMES`): twelve games, about a month, enough for a rate to move a long
way and not enough to trust it. A card that hides a twelve-game sample is
worse than none, which is what `docs/trades.md` section 6 says about the
report it came from.

NO LOOK-AHEAD

Every query takes `today` and reads nothing on or after it, the same as the
reports: the projections filter `scoring_period < today`, the status is the
last one observed, and the games left come from the stored NBA schedule,
which is the one fact about the future a card is entitled to see.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from espn_api.basketball.constant import PRO_TEAM_MAP
from sqlalchemy.orm import Session

from app.db.models import LeagueSeason
from app.pickups.judge import horizon, standard_lens, weeks_between
from app.pickups.projection import per_game_line, rest_of_season_line
from app.pickups.state import build_players, season_calendar
from app.scoring.knowable import knowable
from app.scoring.lines import CategoryLine
from app.trades import THIN_GAMES, playoff_window

__all__ = ["Card", "player_card"]


@dataclass(frozen=True)
class Card:
    """What is known about one player on one day, for one league's season."""

    player_id: int
    name: str
    position: str | None
    pro_team_id: int
    #: NBA team as ESPN abbreviates it ("DET"), or None for a man whose team
    #: no snapshot has recorded.
    pro_team: str | None
    injury_status: str | None
    expected_return_date: date | None
    #: The day the card is read as of, and the stretch the games count over.
    today: int
    last_scoring_period: int
    #: Games left he is not ruled out of, over that stretch and over the
    #: playoff matchup periods.
    games_left: int
    playoff_games: int
    playoff_first: int | None
    playoff_last: int | None
    #: Games of his own behind the knowable line, and what stood behind the
    #: rest of it (`app.scoring.knowable`).
    games_so_far: int
    had_projection: bool
    projection_source: str
    #: His line per game, and the same over an ordinary week from here on.
    per_game: CategoryLine
    weekly: CategoryLine
    #: Categories a week his roster place is worth, league standard.
    value: float

    @property
    def thin(self) -> bool:
        return self.games_so_far < THIN_GAMES

    @property
    def hurt(self) -> bool:
        return bool(self.injury_status) and self.injury_status not in ("ACTIVE", "NORMAL")


def player_card(
    session: Session,
    league_season: LeagueSeason,
    player_id: int,
    today: int,
    *,
    tilt: bool = True,
) -> Card:
    """The card for one man, as of the morning of `today`.

    `player_id` is this database's own id, as everywhere inside `app`; the
    route that serves it speaks ESPN's, like every other route.

    Raises `ValueError` when the season has no matchup periods to count games
    over, which is a season before its schedule was ingested rather than a
    player who cannot be found.
    """
    _first, last, today = horizon(session, league_season, today)
    season = int(league_season.season)
    calendar = season_calendar(session, season)
    as_of = calendar.date_of(today) if calendar is not None else None
    days = tuple(range(today, last + 1))
    weeks = weeks_between(today, last)

    built = build_players(session, league_season, [player_id], days)
    player = built[0] if built else None
    games_left = player.games_remaining_this_period if player is not None else 0
    known = knowable(session, player_id, season, today, as_of=as_of)
    weekly = rest_of_season_line(
        session, season, player_id, today, games_left, tilt=tilt, as_of=as_of
    ).scaled(1.0 / weeks)

    window = playoff_window(session, league_season)
    playoff_first, playoff_last, playoff_games = _playoffs(
        session, league_season, player_id, today, window
    )
    return Card(
        player_id=player_id,
        name=player.name if player is not None else f"player {player_id}",
        position=player.position if player is not None else None,
        pro_team_id=player.pro_team_id if player is not None else 0,
        pro_team=pro_team_name(player.pro_team_id if player is not None else 0),
        injury_status=player.injury_status if player is not None else None,
        expected_return_date=player.expected_return_date if player is not None else None,
        today=today,
        last_scoring_period=last,
        games_left=games_left,
        playoff_games=playoff_games,
        playoff_first=playoff_first,
        playoff_last=playoff_last,
        games_so_far=known.games_so_far,
        had_projection=known.had_projection,
        projection_source=known.source,
        per_game=per_game_line(session, season, player_id, today, tilt=tilt, as_of=as_of),
        weekly=weekly,
        value=standard_lens(session, league_season, today).value(weekly),
    )


def pro_team_name(pro_team_id: int) -> str | None:
    """ESPN's abbreviation for an NBA team id, or None for its own zero.

    ESPN uses 0 for a man on no NBA team at all, which is a free agent in the
    NBA sense rather than in the league's, and it reads better as nothing than
    as "FA".
    """
    name = PRO_TEAM_MAP.get(int(pro_team_id))
    return str(name) if name else None


def _playoffs(
    session: Session,
    league_season: LeagueSeason,
    player_id: int,
    today: int,
    window: tuple[int, int] | None,
) -> tuple[int | None, int | None, int]:
    """The playoff weeks still ahead, and his games in them."""
    if window is None:
        return None, None, 0
    first = max(window[0], today)
    if first > window[1]:
        return window[0], window[1], 0
    built = build_players(session, league_season, [player_id], tuple(range(first, window[1] + 1)))
    games = built[0].games_remaining_this_period if built else 0
    return window[0], window[1], games
