"""Leagues and their members: connections, invites, identities and team claims.

The database half of step 2 of docs/product.md, beside `app.accounts` (step
1's). The routes are `app/api/leagues_admin.py`; docs/accounts.md is the
whole story.

HOW SOMEONE GETS IN

1. **Connect.** A user gives his ESPN cookies and a league id. The login is
   checked against ESPN once (by the route), then kept sealed
   (`secrets_box`) as the league's one active connection; the user becomes
   the league's `owner` member, his SWID becomes his ESPN identity, and he
   is verified on every stored team of that league whose owner GUID is his
   SWID (`connect_league`).
2. **Invite.** An owner makes a link (`create_invite`); only its hash is
   kept. Whoever opens it signed in becomes a `member` (`accept_invite`).
3. **Claim.** A member claims a team (`claim_team`). The claim is verified
   at once when his ESPN identity's SWID is one of that team's owner GUIDs,
   and otherwise waits, pending, for a league owner (`decide_claim`).

OWNER GUIDS

ESPN names an owner by his SWID, which is half of the cookie pair that
signs into an ESPN account (STATUS.md, "Why the API is tailnet-only").
They are compared here only as sha256 hashes of the normalised form
(`normalise_swid`: no braces, upper case), are loaded from `owners` only
inside `_owner_hashes`, and are never returned, logged or put in an error.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import accounts, secrets_box
from app.config import Settings
from app.db.models import (
    Invite,
    League,
    LeagueConnection,
    LeagueSeason,
    Membership,
    Owner,
    Team,
    TeamManager,
    User,
    UserEspnIdentity,
    team_owners,
)
from app.platforms import ESPN

PENDING = "pending"
VERIFIED = accounts.VERIFIED
REJECTED = "rejected"
#: How a claim was verified (`team_managers.how`).
BY_GUID = "owner_guid"
BY_APPROVAL = "approved"

#: Whether a member's own SWID (`/me/espn-identity`, which asks for nothing
#: else) verifies his claim on a team it owns. A bare SWID is not proof: ESPN
#: shows every owner's GUID to anyone who can read the league, so a member
#: can post a league-mate's (docs/accounts.md, "A known weakness"). False
#: makes every member's claim wait for the league owner. Read at call time,
#: so a test can flip it. A connector's own verification is not governed by
#: this: ESPN has accepted his espn_s2 for the league.
#:
#: False from the start (2026-09-19): until a member's SWID is checked
#: against his own espn_s2 at an endpoint that only answers for that
#: account, a claim waits for the league owner. Turning it on is a choice to
#: make only with that check in place.
TRUST_BARE_SWID = False

#: A SWID once its braces are off and it is upper-cased: ESPN's GUID shape.
_SWID = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")


# ---------------------------------------------------------------------------
# SWIDs and owner GUIDs
# ---------------------------------------------------------------------------


def normalise_swid(raw: str) -> str | None:
    """A SWID or owner GUID as compared here: no braces, no spaces, upper case.

    ESPN writes them `{238280FE-...}`, a browser's cookie store may drop the
    braces or the case, and the stored owner GUIDs keep whatever ESPN sent.
    None when what is left is not GUID-shaped.
    """
    text = raw.strip().strip("{}").strip().upper()
    return text if _SWID.fullmatch(text) else None


def swid_hash(normalised: str) -> str:
    """The sha256 of a normalised SWID, hex: what identities are matched on."""
    return hashlib.sha256(normalised.encode()).hexdigest()


def guid_hash(raw: str) -> str | None:
    """The hash of an owner GUID as stored, or None if it is not GUID-shaped."""
    normalised = normalise_swid(raw)
    return swid_hash(normalised) if normalised is not None else None


def swid_cookie(normalised: str) -> str:
    """The SWID as ESPN's cookie carries it: in braces."""
    return "{" + normalised + "}"


def _owner_hashes(session: Session, team_ids: Iterable[int]) -> dict[int, set[str]]:
    """Each team's owner GUIDs, hashed. The one place GUIDs are read, and
    they go no further than this function."""
    ids = list(team_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(team_owners.c.team_id, Owner.espn_owner_id)
        .join(Owner, Owner.id == team_owners.c.owner_id)
        .where(team_owners.c.team_id.in_(ids))
    ).all()
    hashed: dict[int, set[str]] = {}
    for team_id, guid in rows:
        digest = guid_hash(str(guid))
        if digest is not None:
            hashed.setdefault(int(team_id), set()).add(digest)
    return hashed


# ---------------------------------------------------------------------------
# leagues and memberships
# ---------------------------------------------------------------------------


def league_by_espn_id(session: Session, espn_league_id: int) -> League | None:
    return session.scalar(select(League).where(League.espn_league_id == espn_league_id))


def get_or_create_league(session: Session, espn_league_id: int) -> League:
    """The league row, created if this is the first anyone has heard of it.
    Only the row: its seasons and teams are the ingest's to write."""
    session.execute(
        insert(League)
        .values(
            espn_league_id=espn_league_id,
            platform=ESPN,
            platform_league_id=str(espn_league_id),
        )
        .on_conflict_do_nothing(index_elements=["espn_league_id"])
    )
    league = league_by_espn_id(session, espn_league_id)
    if league is None:  # pragma: no cover - the insert above guarantees a row
        raise RuntimeError("league vanished after insert")
    return league


def league_name(session: Session, league: League) -> str:
    """The newest season's name; before any ingest, the name ESPN gave when
    the league was connected; failing both, its id."""
    newest = session.scalar(
        select(LeagueSeason.name)
        .where(LeagueSeason.league_id == league.id)
        .order_by(LeagueSeason.season.desc())
        .limit(1)
    )
    if newest:
        return str(newest)
    named = session.scalar(
        select(LeagueConnection.league_name)
        .where(LeagueConnection.league_id == league.id, LeagueConnection.league_name.is_not(None))
        .order_by(LeagueConnection.created_at.desc())
        .limit(1)
    )
    return str(named) if named else f"League {league.espn_league_id}"


def latest_season(session: Session, league: League) -> int | None:
    """The newest season stored for the league, or None before its first ingest."""
    found = session.scalar(
        select(func.max(LeagueSeason.season)).where(LeagueSeason.league_id == league.id)
    )
    return int(found) if found is not None else None


def join_league(session: Session, user_id: int, league_pk: int, role: str) -> bool:
    """Make this user a member of the league in this role. True if that is new.

    An owner joining is upgraded if he was a member; a member joining never
    demotes an owner.
    """
    joined = insert(Membership).values(user_id=user_id, league_id=league_pk, role=role)
    if role == accounts.OWNER_ROLE:
        statement = joined.on_conflict_do_update(
            constraint="uq_memberships_user_league",
            set_={"role": accounts.OWNER_ROLE},
            where=Membership.role != accounts.OWNER_ROLE,
        )
    else:
        statement = joined.on_conflict_do_nothing(constraint="uq_memberships_user_league")
    return session.execute(statement.returning(Membership.id)).first() is not None


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckedTeam:
    """One team as ESPN listed it when a login was checked. Its owners only
    as hashes: the GUIDs are dropped as the response is read."""

    espn_team_id: int
    name: str
    owner_hashes: frozenset[str]


@dataclass(frozen=True)
class CheckedLeague:
    """What ESPN said about a league when a login was checked against it."""

    espn_league_id: int
    season: int
    name: str
    teams: tuple[CheckedTeam, ...]

    def team_of(self, hashed: str) -> CheckedTeam | None:
        """The team this SWID owns in the season checked, if any."""
        return next((team for team in self.teams if hashed in team.owner_hashes), None)


@dataclass(frozen=True)
class ClaimedTeam:
    season: int
    espn_team_id: int
    name: str


@dataclass(frozen=True)
class Connected:
    connection: LeagueConnection
    league: League
    #: The same user connecting again: his new cookies replaced the old.
    replaced: bool
    #: Every stored team of this league verified to him by his SWID.
    claimed: list[ClaimedTeam]
    #: False when another account already holds this SWID as its identity.
    identity_kept: bool


class AlreadyConnectedError(Exception):
    """The league has an active connection, and it is somebody else's."""


def active_connection(session: Session, league_pk: int) -> LeagueConnection | None:
    return session.scalar(
        select(LeagueConnection).where(
            LeagueConnection.league_id == league_pk, LeagueConnection.revoked_at.is_(None)
        )
    )


def seal_credentials(swid: str, espn_s2: str, settings: Settings | None = None) -> str:
    """The sealed JSON of the two cookies, as `league_connections` keeps it."""
    return secrets_box.seal(json.dumps({"espn_s2": espn_s2, "swid": swid_cookie(swid)}), settings)


def connection_credentials(
    connection: LeagueConnection, settings: Settings | None = None
) -> tuple[str, str]:
    """(SWID, espn_s2) of a live connection, opened: for the job that reads
    the league (step 4), and nothing else. Raises on a revoked one."""
    if connection.sealed_credentials is None:
        raise ValueError("this connection was revoked; its login is gone")
    opened = json.loads(secrets_box.open_(connection.sealed_credentials, settings))
    return str(opened["swid"]), str(opened["espn_s2"])


def connect_league(
    session: Session,
    user_id: int,
    checked: CheckedLeague,
    swid: str,
    espn_s2: str,
    settings: Settings | None = None,
) -> Connected:
    """Keep a checked login as the league's connection, and its user as owner.

    `swid` is normalised (`normalise_swid`). Sealed before anything is
    written, so a missing key fails with nothing half-done. The same user
    connecting again replaces his cookies in place; anyone else, while a
    connection is active, is refused (`AlreadyConnectedError`). Records that
    the league wants an ingest, and starts none. Does not commit.
    """
    sealed = seal_credentials(swid, espn_s2, settings)
    league = get_or_create_league(session, checked.espn_league_id)
    at = accounts.now()
    active = active_connection(session, league.id)
    if active is not None and active.user_id != user_id:
        raise AlreadyConnectedError()
    if active is not None:
        active.sealed_credentials = sealed
        active.league_name = checked.name
        active.last_ok_at = at
        active.last_error = None
        active.last_error_at = None
        active.ingest_requested_at = at
        connection = active
    else:
        connection = LeagueConnection(
            league_id=league.id,
            user_id=user_id,
            platform=ESPN,
            sealed_credentials=sealed,
            league_name=checked.name,
            last_ok_at=at,
            ingest_requested_at=at,
        )
        session.add(connection)
    session.flush()
    join_league(session, user_id, league.id, accounts.OWNER_ROLE)
    kept = set_identity(session, user_id, swid, settings)
    claimed = verify_owned_teams(session, user_id, league.id, swid_hash(swid))
    return Connected(connection, league, active is not None, claimed, kept)


def user_connections(session: Session, user_id: int) -> list[tuple[LeagueConnection, League]]:
    """This user's connections, live and revoked, newest first."""
    rows = session.execute(
        select(LeagueConnection, League)
        .join(League, League.id == LeagueConnection.league_id)
        .where(LeagueConnection.user_id == user_id)
        .order_by(LeagueConnection.created_at.desc(), LeagueConnection.id.desc())
    ).all()
    return [(row[0], row[1]) for row in rows]


def revoke_connection(
    session: Session, user_id: int, connection_id: int
) -> tuple[LeagueConnection, League] | None:
    """Revoke one of this user's connections and wipe its sealed login.

    None when it is not his (or not there). Revoking twice is harmless. His
    membership stays: he is still the person who brought the league in.
    Does not commit.
    """
    found = session.execute(
        select(LeagueConnection, League)
        .join(League, League.id == LeagueConnection.league_id)
        .where(LeagueConnection.id == connection_id, LeagueConnection.user_id == user_id)
    ).first()
    if found is None:
        return None
    connection: LeagueConnection = found[0]
    if connection.revoked_at is None:
        connection.revoked_at = accounts.now()
    connection.sealed_credentials = None
    session.flush()
    return connection, found[1]


def record_check(session: Session, connection: LeagueConnection, error: str | None) -> None:
    """Note how the connection's last use went, for step 4's jobs. `error` is
    a fixed sentence of this code's, never ESPN's text. Does not commit."""
    at = accounts.now()
    if error is None:
        connection.last_ok_at = at
        connection.last_error = None
        connection.last_error_at = None
    else:
        connection.last_error = error[:200]
        connection.last_error_at = at


def verify_connection_owner(
    session: Session, connection: LeagueConnection, settings: Settings | None = None
) -> list[ClaimedTeam]:
    """Verify the connection's user on the teams his SWID owns, now stored.

    For after a league's first ingest (step 4): at connect time a new league
    has no teams to verify him on. Does not commit.
    """
    swid, _ = connection_credentials(connection, settings)
    normalised = normalise_swid(swid)
    if normalised is None:
        return []
    return verify_owned_teams(
        session, connection.user_id, connection.league_id, swid_hash(normalised)
    )


# ---------------------------------------------------------------------------
# a member's own ESPN identity
# ---------------------------------------------------------------------------


class IdentityTakenError(Exception):
    """Another account already holds this SWID as its ESPN identity."""


def identity_hash(session: Session, user_id: int) -> str | None:
    return session.scalar(
        select(UserEspnIdentity.swid_hash).where(UserEspnIdentity.user_id == user_id)
    )


def set_identity(
    session: Session, user_id: int, swid: str, settings: Settings | None = None
) -> bool:
    """Keep this user's SWID (normalised), sealed and hashed. False, and
    nothing written, when another account already holds it: one account per
    ESPN identity. Does not commit."""
    hashed = swid_hash(swid)
    holder = session.scalar(
        select(UserEspnIdentity.user_id).where(UserEspnIdentity.swid_hash == hashed)
    )
    if holder is not None and holder != user_id:
        return False
    sealed = secrets_box.seal(swid_cookie(swid), settings)
    kept = insert(UserEspnIdentity).values(
        user_id=user_id, sealed_swid=sealed, swid_hash=hashed, created_at=accounts.now()
    )
    session.execute(
        kept.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "sealed_swid": kept.excluded.sealed_swid,
                "swid_hash": kept.excluded.swid_hash,
                "created_at": kept.excluded.created_at,
            },
        )
    )
    return True


def forget_identity(session: Session, user_id: int) -> bool:
    """Drop this user's ESPN identity. Claims it verified stay verified."""
    result = session.execute(
        delete(UserEspnIdentity)
        .where(UserEspnIdentity.user_id == user_id)
        .returning(UserEspnIdentity.user_id)
    ).first()
    return result is not None


# ---------------------------------------------------------------------------
# team claims
# ---------------------------------------------------------------------------


def _write_claim(
    session: Session,
    user_id: int,
    team_id: int,
    *,
    state: str,
    how: str | None,
    decided_by: int | None = None,
    only_unverified: bool = False,
) -> None:
    at = accounts.now()
    values = {
        "state": state,
        "how": how,
        "verified_at": at if state == VERIFIED else None,
        "decided_by": decided_by,
        "decided_at": at if decided_by is not None else None,
    }
    written = insert(TeamManager).values(user_id=user_id, team_id=team_id, **values)
    session.execute(
        written.on_conflict_do_update(
            constraint="uq_team_managers_user_team",
            set_=values,
            where=TeamManager.state != VERIFIED if only_unverified else None,
        )
    )


def verify_owned_teams(
    session: Session, user_id: int, league_pk: int, hashed: str
) -> list[ClaimedTeam]:
    """Verify this user on every stored team of the league his SWID owns.

    Any season: the GUID is ESPN's own record of who held the team. A claim
    already verified is left as it was. Newest season first. Does not commit.
    """
    teams = session.execute(
        select(Team.id, LeagueSeason.season, Team.espn_team_id, Team.name)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .where(LeagueSeason.league_id == league_pk)
        .order_by(LeagueSeason.season.desc(), Team.espn_team_id)
    ).all()
    owners = _owner_hashes(session, (row.id for row in teams))
    owned = [row for row in teams if hashed in owners.get(int(row.id), set())]
    for row in owned:
        _write_claim(
            session, user_id, int(row.id), state=VERIFIED, how=BY_GUID, only_unverified=True
        )
    return [ClaimedTeam(int(r.season), int(r.espn_team_id), str(r.name)) for r in owned]


def claim_team(session: Session, user_id: int, team_id: int) -> str:
    """Claim a team (`teams.id`) for this user; the claim's state afterwards.

    Verified at once when his ESPN identity is one of the team's owners;
    otherwise pending, for a league owner. A verified claim is left alone;
    a rejected one can be made again. Does not commit.
    """
    current = session.scalar(
        select(TeamManager.state).where(
            TeamManager.user_id == user_id, TeamManager.team_id == team_id
        )
    )
    if current == VERIFIED:
        return VERIFIED
    hashed = identity_hash(session, user_id) if TRUST_BARE_SWID else None
    if hashed is not None and hashed in _owner_hashes(session, [team_id]).get(team_id, set()):
        _write_claim(session, user_id, team_id, state=VERIFIED, how=BY_GUID)
        return VERIFIED
    _write_claim(session, user_id, team_id, state=PENDING, how=None)
    return PENDING


def recheck_pending(session: Session, user_id: int) -> list[ClaimedTeam]:
    """Verify whichever of this user's pending claims his ESPN identity now
    proves. Rejected claims stay rejected. Does not commit."""
    hashed = identity_hash(session, user_id) if TRUST_BARE_SWID else None
    if hashed is None:
        return []
    pending = session.execute(
        select(
            TeamManager.id,
            Team.id.label("team_pk"),
            LeagueSeason.season,
            Team.espn_team_id,
            Team.name,
        )
        .select_from(TeamManager)
        .join(Team, Team.id == TeamManager.team_id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .where(TeamManager.user_id == user_id, TeamManager.state == PENDING)
        .order_by(LeagueSeason.season.desc(), Team.espn_team_id)
    ).all()
    owners = _owner_hashes(session, (row.team_pk for row in pending))
    proven = [row for row in pending if hashed in owners.get(int(row.team_pk), set())]
    if proven:
        session.execute(
            update(TeamManager)
            .where(TeamManager.id.in_([row.id for row in proven]))
            .values(state=VERIFIED, how=BY_GUID, verified_at=accounts.now())
        )
    return [ClaimedTeam(int(r.season), int(r.espn_team_id), str(r.name)) for r in proven]


@dataclass(frozen=True)
class ClaimableTeam:
    espn_team_id: int
    name: str
    #: Some member is a verified manager of it.
    claimed: bool
    #: This viewer's own claim on it: pending, verified, rejected, or None.
    mine: str | None


def claimable_teams(
    session: Session, league_season_id: int, user_id: int | None
) -> list[ClaimableTeam]:
    """A season's teams, by ESPN id, with whether each is claimed and by this user."""
    teams = session.execute(
        select(Team.id, Team.espn_team_id, Team.name)
        .where(Team.league_season_id == league_season_id)
        .order_by(Team.espn_team_id)
    ).all()
    ids = [int(row.id) for row in teams]
    verified = set(
        session.scalars(
            select(TeamManager.team_id)
            .where(TeamManager.team_id.in_(ids), TeamManager.state == VERIFIED)
            .distinct()
        ).all()
    )
    mine: dict[int, str] = {}
    if user_id is not None:
        mine = {
            int(team): str(state)
            for team, state in session.execute(
                select(TeamManager.team_id, TeamManager.state).where(
                    TeamManager.team_id.in_(ids), TeamManager.user_id == user_id
                )
            ).all()
        }
    return [
        ClaimableTeam(int(r.espn_team_id), str(r.name), int(r.id) in verified, mine.get(int(r.id)))
        for r in teams
    ]


@dataclass(frozen=True)
class LeagueClaim:
    id: int
    season: int
    espn_team_id: int
    team_name: str
    email: str
    state: str
    how: str | None
    created_at: datetime
    decided_at: datetime | None


def league_claims(
    session: Session, league_pk: int, states: Iterable[str] = (PENDING,)
) -> list[LeagueClaim]:
    """The league's claims in these states, oldest first, with who made each."""
    rows = session.execute(
        select(
            TeamManager.id,
            LeagueSeason.season,
            Team.espn_team_id,
            Team.name,
            User.email,
            TeamManager.state,
            TeamManager.how,
            TeamManager.created_at,
            TeamManager.decided_at,
        )
        .join(Team, Team.id == TeamManager.team_id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .join(User, User.id == TeamManager.user_id)
        .where(LeagueSeason.league_id == league_pk, TeamManager.state.in_(list(states)))
        .order_by(TeamManager.created_at, TeamManager.id)
    ).all()
    return [LeagueClaim(*row) for row in rows]


def decide_claim(
    session: Session, league_pk: int, claim_id: int, *, approve: bool, decided_by: int | None
) -> LeagueClaim | None:
    """A league owner approves or rejects a claim on one of his league's teams.

    None when the claim is not in this league. Approving a verified claim
    changes nothing; rejecting one takes the team's plan away from that
    manager. Does not commit.
    """
    in_league = (
        select(TeamManager.id)
        .join(Team, Team.id == TeamManager.team_id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .where(TeamManager.id == claim_id, LeagueSeason.league_id == league_pk)
    )
    claim = session.scalar(
        select(TeamManager).where(TeamManager.id.in_(in_league)).with_for_update()
    )
    if claim is None:
        return None
    at = accounts.now()
    if approve and claim.state != VERIFIED:
        claim.state = VERIFIED
        claim.how = BY_APPROVAL
        claim.verified_at = at
        claim.decided_by = decided_by
        claim.decided_at = at
    elif not approve:
        claim.state = REJECTED
        claim.how = None
        claim.verified_at = None
        claim.decided_by = decided_by
        claim.decided_at = at
    session.flush()
    found = [c for c in league_claims(session, league_pk, (claim.state,)) if c.id == claim_id]
    return found[0] if found else None


# ---------------------------------------------------------------------------
# invites
# ---------------------------------------------------------------------------


def create_invite(
    session: Session, league_pk: int, created_by: int | None, expires_at: datetime | None
) -> tuple[Invite, str]:
    """A new invite into the league: the row, and the token for its link.
    Only the token's hash is stored, so the link is shown once. Does not commit."""
    token = accounts.new_token()
    invite = Invite(
        league_id=league_pk,
        created_by=created_by,
        token_hash=accounts.hash_token(token),
        expires_at=expires_at,
    )
    session.add(invite)
    session.flush()
    return invite, token


def live_invite(session: Session, token: str) -> Invite | None:
    """The invite this token opens, if it is neither revoked nor expired."""
    return session.scalar(
        select(Invite).where(
            Invite.token_hash == accounts.hash_token(token),
            Invite.revoked_at.is_(None),
            or_(Invite.expires_at.is_(None), Invite.expires_at > accounts.now()),
        )
    )


def accept_invite(session: Session, token: str, user_id: int) -> tuple[League, bool] | None:
    """Make this user a member of the invite's league: (the league, whether he
    is new to it). None when the invite is no good. Does not commit."""
    invite = live_invite(session, token)
    if invite is None:
        return None
    joined = join_league(session, user_id, invite.league_id, accounts.MEMBER_ROLE)
    if joined:
        session.execute(update(Invite).where(Invite.id == invite.id).values(uses=Invite.uses + 1))
    league = session.get(League, invite.league_id)
    if league is None:  # pragma: no cover - the foreign key guarantees it
        return None
    return league, joined


def league_invites(session: Session, league_pk: int) -> list[Invite]:
    """Every invite into the league, newest first. Their tokens are not kept."""
    return list(
        session.scalars(
            select(Invite)
            .where(Invite.league_id == league_pk)
            .order_by(Invite.created_at.desc(), Invite.id.desc())
        ).all()
    )


def revoke_invite(session: Session, league_pk: int, invite_id: int) -> Invite | None:
    """Stop an invite working. None when it is not this league's. Does not commit."""
    invite = session.scalar(
        select(Invite).where(Invite.id == invite_id, Invite.league_id == league_pk)
    )
    if invite is None:
        return None
    if invite.revoked_at is None:
        invite.revoked_at = accounts.now()
        session.flush()
    return invite
