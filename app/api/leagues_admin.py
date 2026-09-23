"""Connecting a league, inviting its members, and their claims on teams.

Step 2 of docs/product.md; docs/accounts.md is the whole story and has every
route's scope. The database half is `app.memberships`.

    POST   /connections                        connect a league with your ESPN cookies
    GET    /connections                        your connections, never their cookies
    DELETE /connections/{connection_id}        revoke one, and wipe its sealed login
    POST   /leagues/{league_id}/invites        (owner) a new invite link, shown once
    GET    /leagues/{league_id}/invites        (owner) its invites, without links
    DELETE /leagues/{league_id}/invites/{id}   (owner) revoke one
    GET    /invites/{token}                    the league an invite is for
    POST   /invites/{token}/accept             join it
    GET    /leagues/{league_id}/seasons/{season}/teams/claimable
    POST   /leagues/{league_id}/seasons/{season}/teams/{team_id}/claim
    GET    /leagues/{league_id}/claims         (owner) claims waiting on a decision
    POST   /leagues/{league_id}/claims/{id}/approve | /reject   (owner)
    POST   /me/espn-identity  {swid}           your own SWID, to verify your claims
    DELETE /me/espn-identity
    GET    /leagues/{league_id}/calibration            your league's numbers
    PUT    /leagues/{league_id}/calibration/{key}      (owner) set a bar, with a reason
    DELETE /leagues/{league_id}/calibration/{key}      (owner) go back to the measurement
    POST   /leagues/{league_id}/calibration/measure    (owner) measure again
    GET    /join/{token}, /pages/claim/{league_id}/{season}  (the pages; see app/api/site.py)

SECRETS

The cookies arrive in one request body, are checked against ESPN once, are
sealed (app/secrets_box.py) and are never returned, logged or echoed in an
error. The body is validated by hand rather than by pydantic's field
constraints, because FastAPI's 422 for a failed constraint repeats the value
it refused. ESPN's own errors are reported by their kind only.
Owner GUIDs never leave `app.memberships`.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import requests
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import accounts, brand, calibration, intake, memberships, secrets_box
from app.accounts import now
from app.api.access import (
    LEAGUE_MEMBER_PAGE,
    LEAGUE_OWNER,
    SIGNED_IN_PAGE,
    CurrentUser,
    LeagueMember,
    LeagueOwner,
    SettingsDep,
    Viewer,
)
from app.api.auth import TokenBucket
from app.api.deps import LeagueSeasonDep, SessionDep
from app.config import Settings
from app.db.models import Invite, League, LeagueConnection, Team
from app.espn import current_season, fetch_league_settings_with

log = logging.getLogger("fcp.leagues")

router = APIRouter(tags=["leagues admin"])

STATIC = Path(__file__).parent / "static"

#: The longest cookie value accepted. ESPN's espn_s2 is a few hundred
#: characters; anything past this is not one.
MAX_COOKIE = 2048

#: The highest bar a manager may set, in categories a week. Nine categories
#: are contested in a week, so a bar of nine says "never recommend anything";
#: past that it is not a bar, it is a typing mistake.
MAX_BAR = 9.0
#: The longest reason kept with a bar. It is one line under a number on a
#: page, not an essay, and it is printed back to every member of the league.
MAX_REASON = 200

NO_KEY = "Connecting a league is not set up on this server yet (no secrets key)."
NO_ACCOUNT = "accounts are not set up on this server yet"
BAD_SWID = "That SWID is not one: it looks like {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}."
BAD_S2 = "That espn_s2 is not one: copy the whole value of the espn_s2 cookie."
BAD_INVITE = "That invite link has expired, was revoked, or was never issued."
TAKEN = "That ESPN account is already linked to another account here."
CONNECTED_ELSEWHERE = (
    "This league is already connected by another member. Ask its owner for an invite."
)

TokenPath = Annotated[str, PathParam(max_length=200)]


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------


class AdminLimits:
    """Per user: five connection attempts, then one a minute (each one asks
    ESPN); ten SWIDs, then one a minute; twenty invite tokens tried, then one
    every six seconds. In memory, like sign-in's.

    The invite bucket is not a defence against guessing a token -- a token is
    256 random bits and nobody guesses one -- but it is the difference
    between a signed-in member who tries and a signed-in member who can
    hammer the route all day, and it costs nothing.
    """

    def __init__(self) -> None:
        self.connect = TokenBucket(capacity=5, per_second=1 / 60)
        self.identity = TokenBucket(capacity=10, per_second=1 / 60)
        self.invite = TokenBucket(capacity=20, per_second=1 / 6)


def _limits(request: Request) -> AdminLimits:
    state = request.app.state
    if not hasattr(state, "admin_limits"):
        state.admin_limits = AdminLimits()
    found: AdminLimits = state.admin_limits
    return found


def _spend_invite_try(request: Request, viewer: Viewer) -> None:
    """One try at an invite token, charged to the caller; 429 when he is out.

    Keyed by account rather than by token, because the thing being limited is
    a caller working through tokens, not a token being looked at twice.
    """
    if not _limits(request).invite.allow(str(viewer.user_id)):
        raise HTTPException(status_code=429, detail="too many invite links tried; wait a moment")


def _user_id(viewer: Viewer) -> int:
    """The viewer's account id. Only single mode on a database the accounts
    migrations have not reached has none, and nothing here can be written then."""
    if viewer.user_id is None:
        raise HTTPException(status_code=503, detail=NO_ACCOUNT)
    return viewer.user_id


def _league(session: Session, league_id: int) -> League:
    """The league row. The caller has passed a member or owner check, so the
    league is his to know about; a 404 here says nothing new."""
    league = memberships.league_by_espn_id(session, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail=f"league {league_id} not found")
    return league


# ---------------------------------------------------------------------------
# the ESPN check
# ---------------------------------------------------------------------------


class EspnCheckError(Exception):
    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def _team_name(raw: dict[str, object], team_id: int) -> str:
    name = raw.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    parts = [raw.get("location"), raw.get("nickname")]
    joined = " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return joined or f"Team {team_id}"


def _owner_hashes(raw: dict[str, object]) -> frozenset[str]:
    """A team's owners from ESPN's response, hashed as they are read."""
    owners = raw.get("owners")
    found: set[str] = set()
    for owner in owners if isinstance(owners, list) else []:
        guid = owner.get("id") if isinstance(owner, dict) else owner
        digest = memberships.guid_hash(guid) if isinstance(guid, str) else None
        if digest is not None:
            found.add(digest)
    return frozenset(found)


def _parse(league_id: int, season: int, data: dict[str, object]) -> memberships.CheckedLeague:
    settings = data.get("settings")
    name = settings.get("name") if isinstance(settings, dict) else None
    teams: list[memberships.CheckedTeam] = []
    raw_teams = data.get("teams")
    for raw in raw_teams if isinstance(raw_teams, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
            continue
        team_id = int(raw["id"])
        teams.append(memberships.CheckedTeam(team_id, _team_name(raw, team_id), _owner_hashes(raw)))
    season_id = data.get("seasonId")
    return memberships.CheckedLeague(
        espn_league_id=league_id,
        season=season_id if isinstance(season_id, int) else season,
        name=name.strip() if isinstance(name, str) and name.strip() else f"League {league_id}",
        teams=tuple(teams),
    )


def check_espn(league_id: int, swid: str, espn_s2: str) -> memberships.CheckedLeague:
    """Read the league's settings with these cookies, or say plainly why not.

    The newest season ESPN may hold first (the one being prepared, through
    September), then the current one, then the last. Raises `EspnCheckError`
    with a fixed sentence: the cookies are never in it, and neither is
    ESPN's own text.
    """
    newest = current_season() + 1
    refused = unreachable = False
    for season in (newest, newest - 1, newest - 2):
        try:
            data = fetch_league_settings_with(
                league_id, memberships.swid_cookie(swid), espn_s2, season
            )
        except ESPNAccessDenied:
            refused = True
            continue
        except ESPNInvalidLeague:
            continue
        except (ESPNUnknownError, requests.RequestException, ValueError) as error:
            log.warning("ESPN check for league %s: %s", league_id, type(error).__name__)
            unreachable = True
            continue
        return _parse(league_id, season, data)
    if refused:
        raise EspnCheckError(
            422,
            f"ESPN refused these cookies for league {league_id}. Copy espn_s2 and SWID "
            "again from a browser signed in to ESPN, from an account in that league.",
        )
    if unreachable:
        raise EspnCheckError(502, "ESPN did not answer properly. Try again in a few minutes.")
    raise EspnCheckError(422, f"ESPN has no league {league_id} this season or last.")


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------


class ConnectIn(BaseModel):
    #: Plain `str`, no constraints: see the module's note on SECRETS.
    espn_s2: str
    swid: str
    league_id: int


class TeamRefOut(BaseModel):
    season: int
    espn_team_id: int
    name: str


class ConnectionOut(BaseModel):
    id: int
    espn_league_id: int
    league_name: str
    platform: str
    created_at: datetime
    last_ok_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    ingest_requested_at: datetime | None
    revoked_at: datetime | None


class ConnectOut(ConnectionOut):
    #: The same user connected again, and his new cookies replaced the old.
    replaced: bool
    #: Stored teams of this league now verified as his, by his SWID.
    claimed: list[TeamRefOut]
    #: The team ESPN says his SWID owns in the season checked, stored or not.
    espn_team: TeamRefOut | None
    #: False when another account already holds this SWID as its identity.
    identity_kept: bool


def _connection_out(
    session: Session, connection: LeagueConnection, league: League
) -> ConnectionOut:
    return ConnectionOut(
        id=connection.id,
        espn_league_id=int(league.espn_league_id),
        league_name=memberships.league_name(session, league),
        platform=connection.platform,
        created_at=connection.created_at,
        last_ok_at=connection.last_ok_at,
        last_error_at=connection.last_error_at,
        last_error=connection.last_error,
        ingest_requested_at=connection.ingest_requested_at,
        revoked_at=connection.revoked_at,
    )


def _cookies(body: ConnectIn) -> tuple[str, str]:
    """The SWID normalised, and espn_s2 as given, or a 422 that repeats neither."""
    swid = memberships.normalise_swid(body.swid) if len(body.swid) <= 100 else None
    if swid is None:
        raise HTTPException(status_code=422, detail=BAD_SWID)
    espn_s2 = body.espn_s2.strip()
    if not espn_s2 or len(espn_s2) > MAX_COOKIE or any(c.isspace() or c == ";" for c in espn_s2):
        raise HTTPException(status_code=422, detail=BAD_S2)
    return swid, espn_s2


@router.post("/connections", status_code=201, summary="Connect a league with your ESPN login")
def connect(
    body: ConnectIn,
    request: Request,
    viewer: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ConnectOut:
    user_id = _user_id(viewer)
    if not secrets_box.configured(settings):
        raise HTTPException(status_code=503, detail=NO_KEY)
    if body.league_id <= 0:
        raise HTTPException(status_code=422, detail="that is not an ESPN league id")
    swid, espn_s2 = _cookies(body)
    if not _limits(request).connect.allow(str(user_id)):
        raise HTTPException(status_code=429, detail="too many attempts; wait a minute")
    try:
        checked = check_espn(body.league_id, swid, espn_s2)
    except EspnCheckError as refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.reason) from None
    try:
        done = memberships.connect_league(session, user_id, checked, swid, espn_s2, settings)
        session.commit()
    except (memberships.AlreadyConnectedError, IntegrityError):
        session.rollback()
        raise HTTPException(status_code=409, detail=CONNECTED_ELSEWHERE) from None
    log.info("league %s connected by user %s", body.league_id, user_id)
    own = checked.team_of(memberships.swid_hash(swid))
    listed = _connection_out(session, done.connection, done.league)
    return ConnectOut(
        **listed.model_dump(),
        replaced=done.replaced,
        claimed=[TeamRefOut(**vars(team)) for team in done.claimed],
        espn_team=TeamRefOut(season=checked.season, espn_team_id=own.espn_team_id, name=own.name)
        if own is not None
        else None,
        identity_kept=done.identity_kept,
    )


@router.get("/connections", summary="Your league connections, without their cookies")
def list_connections(viewer: CurrentUser, session: SessionDep) -> list[ConnectionOut]:
    if viewer.user_id is None:
        return []
    return [
        _connection_out(session, connection, league)
        for connection, league in memberships.user_connections(session, viewer.user_id)
    ]


@router.delete(
    "/connections/{connection_id}", summary="Revoke a connection and wipe its sealed login"
)
def revoke_connection(
    connection_id: int, viewer: CurrentUser, session: SessionDep
) -> ConnectionOut:
    """Your own connection only; anyone else's is a 404 like one never made."""
    found = memberships.revoke_connection(session, _user_id(viewer), connection_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such connection of yours")
    session.commit()
    log.info("connection %s revoked by user %s", connection_id, viewer.user_id)
    return _connection_out(session, *found)


# ---------------------------------------------------------------------------
# invites
# ---------------------------------------------------------------------------


class InviteIn(BaseModel):
    #: Days until the link stops working; omitted or null, it works until revoked.
    expires_in_days: int | None = None


class InviteOut(BaseModel):
    id: int
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    uses: int


class NewInviteOut(InviteOut):
    #: The link, shown this once: only its hash is kept.
    url: str
    path: str


class InviteLeagueOut(BaseModel):
    espn_league_id: int
    league_name: str
    #: Whether the viewer is in the league already.
    member: bool


class JoinedOut(BaseModel):
    espn_league_id: int
    league_name: str
    #: False when the viewer was already a member.
    joined: bool
    #: The newest season stored, where the claim page opens; None before an ingest.
    latest_season: int | None


def _invite_out(invite: Invite) -> InviteOut:
    return InviteOut(
        id=invite.id,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        revoked_at=invite.revoked_at,
        uses=invite.uses,
    )


def _join_url(settings: Settings, request: Request, path: str) -> str:
    base = settings.fcp_public_url or str(request.base_url)
    return f"{base.rstrip('/')}{path}"


@router.post(
    "/leagues/{league_id}/invites",
    status_code=201,
    summary="A new invite link into the league, shown once",
)
def create_invite(
    league_id: int,
    request: Request,
    viewer: LeagueOwner,
    session: SessionDep,
    settings: SettingsDep,
    body: InviteIn | None = None,
) -> NewInviteOut:
    days = body.expires_in_days if body is not None else None
    if days is not None and not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="an invite lasts from 1 to 365 days")
    league = _league(session, league_id)
    expires = now() + timedelta(days=days) if days is not None else None
    invite, token = memberships.create_invite(session, league.id, viewer.user_id, expires)
    session.commit()
    path = f"/join/{token}"
    return NewInviteOut(
        **_invite_out(invite).model_dump(), url=_join_url(settings, request, path), path=path
    )


@router.get(
    "/leagues/{league_id}/invites",
    summary="The league's invites, without their links",
    dependencies=[LEAGUE_OWNER],
)
def list_invites(league_id: int, session: SessionDep) -> list[InviteOut]:
    league = _league(session, league_id)
    return [_invite_out(invite) for invite in memberships.league_invites(session, league.id)]


@router.delete(
    "/leagues/{league_id}/invites/{invite_id}",
    summary="Revoke an invite",
    dependencies=[LEAGUE_OWNER],
)
def revoke_invite(league_id: int, invite_id: int, session: SessionDep) -> InviteOut:
    league = _league(session, league_id)
    invite = memberships.revoke_invite(session, league.id, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="no such invite in this league")
    session.commit()
    return _invite_out(invite)


@router.get("/invites/{token}", summary="The league an invite link is for")
def show_invite(
    token: TokenPath, viewer: CurrentUser, session: SessionDep, request: Request
) -> InviteLeagueOut:
    """Signed in, because a league's name is a member's to see; the token
    itself is the rest of the credential, so the tries are rate-limited."""
    _spend_invite_try(request, viewer)
    invite = memberships.live_invite(session, token)
    if invite is None:
        raise HTTPException(status_code=404, detail=BAD_INVITE)
    league = session.get(League, invite.league_id)
    if league is None:  # pragma: no cover - the foreign key guarantees it
        raise HTTPException(status_code=404, detail=BAD_INVITE)
    member = viewer.user_id is not None and accounts.is_member(
        session, viewer.user_id, int(league.espn_league_id)
    )
    return InviteLeagueOut(
        espn_league_id=int(league.espn_league_id),
        league_name=memberships.league_name(session, league),
        member=member,
    )


@router.post("/invites/{token}/accept", summary="Join the league an invite is for")
def accept_invite(
    token: TokenPath, viewer: CurrentUser, session: SessionDep, request: Request
) -> JoinedOut:
    _spend_invite_try(request, viewer)
    accepted = memberships.accept_invite(session, token, _user_id(viewer))
    if accepted is None:
        session.rollback()
        raise HTTPException(status_code=404, detail=BAD_INVITE)
    session.commit()
    league, joined = accepted
    if joined:
        log.info("user %s joined league %s by invite", viewer.user_id, league.espn_league_id)
    return JoinedOut(
        espn_league_id=int(league.espn_league_id),
        league_name=memberships.league_name(session, league),
        joined=joined,
        latest_season=memberships.latest_season(session, league),
    )


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------


class ClaimableOut(BaseModel):
    espn_team_id: int
    name: str
    #: A member is a verified manager of this team.
    claimed: bool
    #: Your own claim on it: pending, verified, rejected, or null.
    mine: str | None


class ClaimOut(BaseModel):
    season: int
    espn_team_id: int
    name: str
    state: str


class LeagueClaimOut(BaseModel):
    id: int
    season: int
    espn_team_id: int
    team_name: str
    email: str
    state: str
    how: str | None
    created_at: datetime
    decided_at: datetime | None


class IdentityIn(BaseModel):
    #: Plain `str`, no constraints: see the module's note on SECRETS.
    swid: str


class IdentityOut(BaseModel):
    #: Pending claims this identity has now verified.
    verified: list[TeamRefOut]


@router.get(
    "/leagues/{league_id}/seasons/{season}/teams/claimable",
    summary="The season's teams, and which are claimed",
)
def claimable(
    viewer: LeagueMember, league_season: LeagueSeasonDep, session: SessionDep
) -> list[ClaimableOut]:
    return [
        ClaimableOut(**vars(team))
        for team in memberships.claimable_teams(session, league_season.id, viewer.user_id)
    ]


@router.post(
    "/leagues/{league_id}/seasons/{season}/teams/{team_id}/claim",
    summary="Claim a team: verified by your SWID, or pending for the league's owner",
)
def claim(
    team_id: int, viewer: LeagueMember, league_season: LeagueSeasonDep, session: SessionDep
) -> ClaimOut:
    team = session.scalar(
        select(Team).where(Team.league_season_id == league_season.id, Team.espn_team_id == team_id)
    )
    if team is None:
        raise HTTPException(status_code=404, detail=f"team {team_id} not found in this season")
    state = memberships.claim_team(session, _user_id(viewer), team.id)
    session.commit()
    return ClaimOut(season=league_season.season, espn_team_id=team_id, name=team.name, state=state)


def _decided(claim: memberships.LeagueClaim) -> LeagueClaimOut:
    return LeagueClaimOut(**vars(claim))


@router.get(
    "/leagues/{league_id}/claims",
    summary="Claims on the league's teams waiting for a decision",
    dependencies=[LEAGUE_OWNER],
)
def list_claims(league_id: int, session: SessionDep) -> list[LeagueClaimOut]:
    league = _league(session, league_id)
    return [_decided(c) for c in memberships.league_claims(session, league.id)]


def _decide(
    session: Session, viewer: Viewer, league_id: int, claim_id: int, approve: bool
) -> LeagueClaimOut:
    league = _league(session, league_id)
    decided = memberships.decide_claim(
        session, league.id, claim_id, approve=approve, decided_by=viewer.user_id
    )
    if decided is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="no such claim in this league")
    session.commit()
    log.info(
        "claim %s %s by user %s", claim_id, "approved" if approve else "rejected", viewer.user_id
    )
    return _decided(decided)


@router.post(
    "/leagues/{league_id}/claims/{claim_id}/approve",
    summary="Approve a claim: its member manages the team",
)
def approve_claim(
    league_id: int, claim_id: int, viewer: LeagueOwner, session: SessionDep
) -> LeagueClaimOut:
    return _decide(session, viewer, league_id, claim_id, approve=True)


@router.post(
    "/leagues/{league_id}/claims/{claim_id}/reject",
    summary="Reject a claim, or take a verified manager off the team",
)
def reject_claim(
    league_id: int, claim_id: int, viewer: LeagueOwner, session: SessionDep
) -> LeagueClaimOut:
    return _decide(session, viewer, league_id, claim_id, approve=False)


@router.post("/me/espn-identity", summary="Your own SWID, kept sealed, to verify your claims")
def set_identity(
    body: IdentityIn,
    request: Request,
    viewer: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> IdentityOut:
    """Only the SWID: a member's espn_s2 is never asked for or kept."""
    user_id = _user_id(viewer)
    if not secrets_box.configured(settings):
        raise HTTPException(status_code=503, detail=NO_KEY)
    swid = memberships.normalise_swid(body.swid) if len(body.swid) <= 100 else None
    if swid is None:
        raise HTTPException(status_code=422, detail=BAD_SWID)
    if not _limits(request).identity.allow(str(user_id)):
        raise HTTPException(status_code=429, detail="too many attempts; wait a minute")
    try:
        kept = memberships.set_identity(session, user_id, swid, settings)
        if not kept:
            raise memberships.IdentityTakenError()
        verified = memberships.recheck_pending(session, user_id)
        session.commit()
    except (memberships.IdentityTakenError, IntegrityError):
        session.rollback()
        raise HTTPException(status_code=409, detail=TAKEN) from None
    return IdentityOut(verified=[TeamRefOut(**vars(team)) for team in verified])


@router.delete("/me/espn-identity", summary="Forget your SWID; verified claims stay verified")
def forget_identity(viewer: CurrentUser, session: SessionDep) -> dict[str, bool]:
    forgot = memberships.forget_identity(session, _user_id(viewer))
    session.commit()
    return {"forgotten": forgot}


# ---------------------------------------------------------------------------
# your league's numbers (app.calibration, docs/intake.md)
# ---------------------------------------------------------------------------


class CalibrationOut(BaseModel):
    """One number as the account page shows it.

    `value`, `source`, `n` and `measured_at` are the row actually in use;
    `measured_value` and `measured_n` are this league's own measurement when
    something else is being used over it -- the manager's own choice, or the
    fallback because the sample is under `minimum`. Both are shown, because
    the page's job is to say what is being used and what else is known.
    """

    key: str
    title: str
    unit: str
    minimum: int
    value: float | None
    source: str
    n: int
    note: str
    measured_at: datetime | None
    #: True for the three bars a league owner may set himself.
    settable: bool
    #: This league's own measurement, when it is not the one in use.
    measured_value: float | None = None
    measured_n: int | None = None
    #: The reason the manager gave, when he set this one.
    owner_reason: str | None = None


class CalibrationListOut(BaseModel):
    espn_league_id: int
    numbers: list[CalibrationOut]
    #: True while the intake chain is working on this league.
    measuring: bool
    #: What it is doing, or how it ended: one plain sentence.
    progress: str
    #: When the newest chain was asked for, for the once-a-day limit.
    last_measured_at: datetime | None
    #: False when asking again would be refused (running, or already today).
    can_measure: bool
    #: Why not, when `can_measure` is false.
    refused: str | None = None


class OwnerBarIn(BaseModel):
    value: float
    #: One line of why, kept and printed with the number.
    reason: str = ""


def _calibration_out(listed: calibration.Listed) -> CalibrationOut:
    used = listed.used
    # This league's own measurement is shown beside the number only when it
    # is not the number: because its manager chose a bar over it, or because
    # the sample is under the key's minimum and the fallback is being used.
    mine = listed.measured if used.source != calibration.MEASURED else None
    return CalibrationOut(
        key=listed.key,
        title=listed.title,
        unit=listed.unit,
        minimum=listed.minimum,
        value=used.value,
        source=used.source,
        n=used.n,
        note=used.note,
        measured_at=used.measured_at,
        settable=listed.key in calibration.OWNER_SETTABLE,
        measured_value=mine.value if mine is not None else None,
        measured_n=mine.n if mine is not None else None,
        owner_reason=(
            str(listed.owner.payload.get("reason") or "") or None
            if listed.owner is not None
            else None
        ),
    )


def _calibration_list(session: Session, league: League) -> CalibrationListOut:
    state = intake.progress(session, league.id)
    refused: str | None = None
    if state.running:
        refused = f"already being measured: {state.words}"
    else:
        started = intake.last_started(session, league.id)
        if started is not None and now() - started < intake.AGAIN_AFTER:
            refused = "measured today; it can be measured again tomorrow"
    return CalibrationListOut(
        espn_league_id=int(league.espn_league_id),
        numbers=[_calibration_out(listed) for listed in calibration.listing(session, league.id)],
        measuring=state.running,
        progress=state.words,
        last_measured_at=state.finished_at,
        can_measure=refused is None,
        refused=refused,
    )


@router.get(
    "/leagues/{league_id}/calibration",
    summary="Your league's own numbers, and where each one came from",
)
def league_calibration(
    league_id: int, viewer: LeagueMember, session: SessionDep
) -> CalibrationListOut:
    """Every member of the league may see them: they are what his pages are
    built on, and a bar nobody can look at is a bar asking to be trusted."""
    return _calibration_list(session, _league(session, league_id))


@router.put(
    "/leagues/{league_id}/calibration/{key}",
    summary="Set one of the three bars yourself, with a line of why",
    dependencies=[LEAGUE_OWNER],
)
def set_calibration(
    league_id: int, key: str, body: OwnerBarIn, session: SessionDep
) -> CalibrationListOut:
    """The league's owner choosing a bar. The other three keys are refused.

    A bar is a choice about how much churn a manager wants, and the sweep
    only ever recommends one; what a pickup returned and what a trade number
    has done are measurements of what happened, and nothing here offers to
    overrule a measurement.
    """
    if key not in calibration.OWNER_SETTABLE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{key} is measured, not chosen: only "
                f"{', '.join(calibration.OWNER_SETTABLE)} can be set by hand"
            ),
        )
    if not 0.0 <= body.value <= MAX_BAR:
        raise HTTPException(
            status_code=422, detail=f"a bar is between 0 and {MAX_BAR:.1f} categories a week"
        )
    if len(body.reason) > MAX_REASON:
        raise HTTPException(
            status_code=422, detail=f"keep the reason under {MAX_REASON} characters"
        )
    league = _league(session, league_id)
    calibration.set_by_owner(session, league.id, key, value=float(body.value), reason=body.reason)
    session.commit()
    log.info("league %s: %s set by its owner", league_id, key)
    return _calibration_list(session, league)


@router.delete(
    "/leagues/{league_id}/calibration/{key}",
    summary="Forget your own bar, and use the measurement again",
    dependencies=[LEAGUE_OWNER],
)
def clear_calibration(league_id: int, key: str, session: SessionDep) -> CalibrationListOut:
    league = _league(session, league_id)
    calibration.forget_owner(session, league.id, key)
    session.commit()
    return _calibration_list(session, league)


@router.post(
    "/leagues/{league_id}/calibration/measure",
    summary="Measure this league's numbers again",
)
def measure_again(league_id: int, viewer: LeagueOwner, session: SessionDep) -> CalibrationListOut:
    """Put the intake chain on the queue (docs/intake.md).

    Once a day, and never while one is running: the sweep alone is an hour
    and a half, and nothing about a league's own history changes fast enough
    for a second run in a day to say anything new. A league this code does
    not model is refused here rather than three jobs later.
    """
    league = _league(session, league_id)
    refused = intake.refusal(session, league.id)
    if refused is not None:
        raise HTTPException(status_code=422, detail=refused.reason)
    try:
        intake.enqueue_intake(session, league.id, user_id=viewer.user_id)
    except intake.IntakeRefusedError as no:
        session.rollback()
        raise HTTPException(status_code=429, detail=str(no)) from None
    session.commit()
    log.info("league %s: measured again, asked by user %s", league_id, viewer.user_id)
    return _calibration_list(session, league)


# ---------------------------------------------------------------------------
# the pages
# ---------------------------------------------------------------------------


def _page(name: str) -> HTMLResponse:
    """One page, read from disk per request, like the others, with the
    product's name filled in from `app/brand.py`."""
    return HTMLResponse(brand.fill((STATIC / name).read_text()))


@router.get(
    "/join/{token}",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[SIGNED_IN_PAGE],
)
def join_page(token: TokenPath) -> HTMLResponse:
    """Where an invite link lands. Signed out, it goes to sign in and comes back."""
    return _page("join.html")


@router.get(
    "/pages/claim/{league_id}/{season}",
    include_in_schema=False,
    response_class=HTMLResponse,
    dependencies=[LEAGUE_MEMBER_PAGE],
)
def claim_page() -> HTMLResponse:
    """The season's teams, to claim yours."""
    return _page("claim.html")
