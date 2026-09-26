"""Who a token is, and what it may open: the site's own checks, off the web.

The tools are not served by FastAPI, so no dependency resolves for them and
there is no request to read a cookie from. They must still answer exactly
what the browser answers, or the token would be a second, weaker door into
the same data. So every question here is asked of `app.api.access` -- the
same `is_league_member`, `is_team_manager` and `is_entitled` every route
declares -- and every refusal is the sentence the site gives.

A refusal is a `RefusedError`, which the server turns into the SDK's own tool
error carrying that one line. Never a stack trace, and never a hint about
whether the league or the team exists: a team that is not yours and a team
that never was read the same, exactly as a 403 does.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import accounts, api_tokens
from app.api import access
from app.api.access import Viewer
from app.config import Settings, get_settings
from app.db.models import League, LeagueSeason, Team

#: The sentences. The first two are the site's own, word for word
#: (`app.api.access`), so a manager who has seen one in the browser reads the
#: same one here. The third is the site's 402 with the address to go to: a
#: conversation has no browser to send there (`not_entitled`).
NOT_A_MEMBER = access.NOT_A_MEMBER
TEAM_REFUSED = access.TEAM_REFUSED
NOT_ENTITLED = (
    "The team layer needs a season pass, and this account has no live one: "
    "a code redeemed at {where} opens it."
)
NO_TOKEN = (
    "this co-manager has no token: put one in BOX_OUT_TOKEN, made at "
    "/account/connections (docs/mcp.md)"
)
NO_SUCH_TOKEN = api_tokens.NO_SUCH_TOKEN
NO_SEASON = "there is no {season} season stored for league {league_id}"
NO_TEAM = "there is no team {team_id} in this league's {season} season"
NO_LEAGUE_OF_YOURS = "you are a member of no league, so there is nothing here to read"
WHICH_LEAGUE = "say which league: you are a member of {leagues}"


class RefusedError(Exception):
    """One sentence a manager can act on, and nothing else."""


def upgrade_address(settings: Settings | None = None) -> str:
    """Where a manager goes for a pass: the site's own address when it is
    configured (`FCP_PUBLIC_URL`), else the path on it."""
    base = (settings if settings is not None else get_settings()).fcp_public_url
    return f"{base.rstrip('/')}{access.UPGRADE_PATH}" if base else access.UPGRADE_PATH


def not_entitled(settings: Settings | None = None) -> str:
    """The 402, as a tool says it: with the upgrade page's address in it."""
    return NOT_ENTITLED.format(where=upgrade_address(settings))


def viewer_for_token(session: Session, settings: Settings, presented: str | None) -> Viewer:
    """Whoever this token is, or `RefusedError`.

    A live token of a member's is that member, with his leagues and his
    teams and nothing else. The service token is the owner, as it is for the
    scheduled scripts. In single mode a valid token is the owner, who reads
    every team -- which is what single mode means everywhere else
    (docs/accounts.md) -- but the token is still checked first: a revoked
    token stops working in every mode, or revoking it would mean nothing.
    """
    token = (presented or "").strip()
    if not token:
        raise RefusedError(NO_TOKEN)
    if api_tokens.looks_like_one(token):
        holder = api_tokens.user_for_token(session, token)
        if holder is None:
            raise RefusedError(NO_SUCH_TOKEN)
        if settings.fcp_auth_mode == "single":
            return access.single_mode_viewer(session, settings)
        owns = settings.fcp_owner_email is not None and holder.email == access.owner_email(settings)
        return Viewer(holder.id, holder.email, is_owner=owns, all_access=False, via="token")
    expected = settings.fcp_service_token
    if expected and accounts.same_secret(token, expected):
        if settings.fcp_auth_mode == "single":
            return access.single_mode_viewer(session, settings)
        owner = accounts.ensure_owner(
            session,
            access.owner_email(settings),
            settings.espn_league_id,
            settings.fcp_tracked_team_id,
        )
        return Viewer(owner.id, owner.email, is_owner=True, all_access=False, via="service")
    raise RefusedError(NO_SUCH_TOKEN)


def member_leagues(session: Session, viewer: Viewer) -> list[int]:
    """Every ESPN league id this viewer may read, in id order.

    Single mode's owner reads every league stored, which is what `/leagues`
    answers him there.
    """
    if viewer.all_access:
        return [
            int(league_id)
            for league_id in session.scalars(
                select(League.espn_league_id).order_by(League.espn_league_id)
            ).all()
        ]
    if viewer.user_id is None:
        return []
    return [league for league, _role in accounts.member_leagues(session, viewer.user_id)]


def the_one_league(session: Session, viewer: Viewer) -> int:
    """His league, when he has exactly one, else a sentence asking which.

    A tool whose league is optional resolves it here rather than guessing:
    a manager in one league should not have to type its id, and a manager in
    three should never have one picked for him.
    """
    mine = member_leagues(session, viewer)
    if not mine:
        raise RefusedError(NO_LEAGUE_OF_YOURS)
    if len(mine) > 1:
        raise RefusedError(WHICH_LEAGUE.format(leagues=", ".join(str(each) for each in mine)))
    return mine[0]


def league_member(session: Session, viewer: Viewer, league_id: int, season: int) -> LeagueSeason:
    """The league season, once this viewer is known to be a member of it.

    The membership is checked before the season is looked up, so a league he
    is not in answers the same whether or not it is stored.
    """
    if not access.is_league_member(session, viewer, league_id):
        raise RefusedError(NOT_A_MEMBER)
    found = session.scalar(
        select(LeagueSeason)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == league_id, LeagueSeason.season == season)
    )
    if found is None:
        raise RefusedError(NO_SEASON.format(season=season, league_id=league_id))
    return found


def newest_season(session: Session, viewer: Viewer, league_id: int) -> int:
    """The newest season stored for a league this viewer may read."""
    if not access.is_league_member(session, viewer, league_id):
        raise RefusedError(NOT_A_MEMBER)
    found = session.scalar(
        select(LeagueSeason.season)
        .join(League, League.id == LeagueSeason.league_id)
        .where(League.espn_league_id == league_id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )
    if found is None:
        raise RefusedError(NO_SEASON.format(season="any", league_id=league_id))
    return int(found)


def team_plan(
    session: Session, viewer: Viewer, league_season: LeagueSeason, league_id: int, team_id: int
) -> Team:
    """A team whose plan this viewer may read: its verified manager, entitled.

    The manager is asked first, so a stranger hears about the team rather
    than about his plan -- the order `require_team_plan` uses.
    """
    season = int(league_season.season)
    if not access.is_team_manager(session, viewer, league_id, season, team_id):
        raise RefusedError(TEAM_REFUSED)
    if not access.is_entitled(session, viewer):
        raise RefusedError(not_entitled())
    found = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if found is None:
        raise RefusedError(NO_TEAM.format(team_id=team_id, season=season))
    return found


def team_of_league(session: Session, league_season: LeagueSeason, team_id: int) -> Team:
    """Any team of a league this viewer is already known to be a member of.

    For the league-scope answers -- a matchup, the flags on What changed --
    where naming a team adds no check because the facts are the league's.
    """
    found = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if found is None:
        raise RefusedError(NO_TEAM.format(team_id=team_id, season=int(league_season.season)))
    return found
