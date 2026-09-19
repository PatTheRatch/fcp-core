"""Accounts: who someone is, how they signed in, and what they may open.

The database half of step 1 of docs/product.md, kept apart from the API so a
script (or step 2's claims) can use it without FastAPI. The API half, the
dependencies every route declares, is `app/api/access.py`; the routes that
sign people in are `app/api/auth.py`; docs/accounts.md is the whole story.

TOKENS

Every secret handed to a browser is `secrets.token_urlsafe(32)` (256 bits)
and only its sha256 is stored. A copy of `sign_in_tokens` or `sessions`
therefore signs nobody in. sha256 without a salt is right here, where it
would be wrong for a password: the input is random and as long as the hash,
so there is nothing to guess.

THE OWNER

Patrick is a user like any other, with four things written for him on
first use (`ensure_owner`): the user row, an `owner` entitlement, an `owner`
membership of the tracked league (`ESPN_LEAGUE_ID`), and a verified claim on
the tracked team (`FCP_TRACKED_TEAM_ID`) in every season that league has
stored. "Every season" takes the ESPN team id at its word across years; the
owner-GUID check (`app.memberships`) is the place that learns better, if a
season's team 3 was ever someone else's. No sealed connection is needed for
the tracked league: its ingest reads the `.env` cookies until step 4.

MEMBERS AND MANAGERS

A league's pages open to its members (`memberships`: whoever connected the
league, and whoever accepted an invite). A team's plan opens to its verified
managers (`team_managers`). The two are separate on purpose: a verified
claim does not make anyone a member, and a membership claims no team.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, exists, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Entitlement,
    League,
    LeagueSeason,
    Membership,
    SignInToken,
    Team,
    TeamManager,
    User,
    UserSession,
)

#: How long a magic link works, and a session lasts.
SIGN_IN_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)
#: `last_seen_at` is written at most this often, so reading a page is not a
#: write on every request.
SEEN_EVERY = timedelta(minutes=5)

#: The only tier there is: the team layer (docs/product.md, "Free and paid").
TEAM_TIER = "team"
#: How the owner's own claims are marked verified.
VERIFIED_AS_OWNER = "owner"
VERIFIED = "verified"
#: The two roles a member of a league has: `owner` connected it (or is the
#: configured owner of the tracked league), `member` came in by an invite.
OWNER_ROLE = "owner"
MEMBER_ROLE = "member"

#: Single mode's owner when `FCP_OWNER_EMAIL` is unset. Not an address that
#: can receive mail, which is the point: nobody can sign in as it.
OWNER_FALLBACK_EMAIL = "owner@localhost"

#: An address long enough to be real and short enough to be one (RFC 5321).
MAX_EMAIL = 254


def now() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    """A fresh secret for a link or a cookie: 32 random bytes, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """What is stored in place of a token: its sha256, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


def same_secret(presented: str, expected: str) -> bool:
    """Compare two secrets by their hashes, in constant time.

    Hashing first makes the comparison's length fixed, so neither the time
    taken nor an early exit says anything about the expected value.
    """
    return hmac.compare_digest(hash_token(presented), hash_token(expected))


def normalise_email(raw: str) -> str | None:
    """The address as stored (trimmed, lower-cased), or None if it is not one.

    Deliberately loose: one @, something either side, no spaces. The link
    arriving is the real check; this only refuses what cannot be an address.
    """
    email = raw.strip().lower()
    if not email or len(email) > MAX_EMAIL or any(c.isspace() for c in email):
        return None
    local, at, domain = email.rpartition("@")
    if not at or not local or not domain or "@" in local or "." not in domain:
        return None
    return email


def safe_next(raw: str | None) -> str | None:
    """A path on this site to land on after signing in, or None.

    Only a local absolute path: "/pages/...". Anything with a scheme, a host
    ("//evil.example") or a backslash is refused, so a sign-in link can never
    be turned into a redirect to somewhere else.
    """
    if not raw or len(raw) > 2000:
        return None
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return None
    if any(ord(c) < 32 for c in raw):
        return None
    return raw


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


def user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def get_or_create_user(session: Session, email: str) -> User:
    """The user with this (normalised) address, created if new.

    `ON CONFLICT DO NOTHING` rather than select-then-insert, so two requests
    racing to create the same account (the pages fetch two routes at once)
    both end up with the one row.
    """
    session.execute(
        insert(User).values(email=email).on_conflict_do_nothing(index_elements=["email"])
    )
    user = user_by_email(session, email)
    if user is None:  # pragma: no cover - the insert above guarantees a row
        raise RuntimeError("user vanished after insert")
    return user


# ---------------------------------------------------------------------------
# magic links
# ---------------------------------------------------------------------------


def issue_sign_in_token(session: Session, user: User, next_path: str | None = None) -> str:
    """A one-time link token for this user. Returns the token; stores its hash."""
    token = new_token()
    session.add(
        SignInToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now() + SIGN_IN_TTL,
            next_path=safe_next(next_path),
        )
    )
    session.flush()
    return token


@dataclass(frozen=True)
class Redeemed:
    user_id: int
    next_path: str | None


def redeem_sign_in_token(session: Session, token: str) -> Redeemed | None:
    """Spend a link token: its user and landing path, or None if it is no good.

    One UPDATE that only matches an unused, unexpired row and marks it used,
    so two clicks racing on one link cannot both win.
    """
    row = session.execute(
        update(SignInToken)
        .where(
            SignInToken.token_hash == hash_token(token),
            SignInToken.used_at.is_(None),
            SignInToken.expires_at > now(),
        )
        .values(used_at=now())
        .returning(SignInToken.user_id, SignInToken.next_path)
    ).first()
    if row is None:
        return None
    return Redeemed(user_id=int(row.user_id), next_path=safe_next(row.next_path))


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def start_session(session: Session, user_id: int) -> str:
    """A new session for this user. Returns the cookie value; stores its hash."""
    token = new_token()
    at = now()
    session.add(
        UserSession(
            user_id=user_id,
            token_hash=hash_token(token),
            created_at=at,
            last_seen_at=at,
            expires_at=at + SESSION_TTL,
        )
    )
    session.execute(update(User).where(User.id == user_id).values(last_sign_in_at=at))
    session.flush()
    return token


def session_user(session: Session, token: str) -> User | None:
    """The user a cookie belongs to, if the session is live; touches last-seen."""
    at = now()
    found = session.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == hash_token(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > at,
        )
    ).first()
    if found is None:
        return None
    live: UserSession = found[0]
    user: User = found[1]
    if live.last_seen_at < at - SEEN_EVERY:
        live.last_seen_at = at
        session.commit()
    return user


def revoke_session(session: Session, token: str) -> bool:
    """End the session this cookie names. True if there was a live one."""
    result = session.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
        .values(revoked_at=now())
        .returning(UserSession.id)
    ).first()
    return result is not None


# ---------------------------------------------------------------------------
# the owner
# ---------------------------------------------------------------------------


def ensure_owner(session: Session, email: str, league_id: int | None, team_id: int | None) -> User:
    """The owner's user, entitlement, membership and team claims, written if missing.

    Idempotent and cheap once done: a handful of statements, each of which
    writes nothing when the row is already there, so it can run on every
    request in single mode. New seasons of the tracked league are claimed
    the first time the owner is resolved after they are ingested. Commits.
    """
    user = get_or_create_user(session, email)
    session.execute(
        insert(Entitlement)
        .values(user_id=user.id, tier=TEAM_TIER, source="owner")
        .on_conflict_do_nothing(
            index_elements=["user_id"], index_where=Entitlement.source == "owner"
        )
    )
    if league_id is not None:
        # The tracked league's owner, whether or not it was ever connected:
        # its ingest reads the .env cookies, so no sealed connection is asked
        # for. Upgraded to owner if he had joined it as a member; nothing is
        # written when he already is one.
        tracked = select(literal(user.id), League.id, literal(OWNER_ROLE)).where(
            League.espn_league_id == league_id
        )
        joined = insert(Membership).from_select(["user_id", "league_id", "role"], tracked)
        session.execute(
            joined.on_conflict_do_update(
                constraint="uq_memberships_user_league",
                set_={"role": OWNER_ROLE},
                where=Membership.role != OWNER_ROLE,
            )
        )
    if league_id is not None and team_id is not None:
        unclaimed = (
            select(Team.id)
            .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
            .join(League, League.id == LeagueSeason.league_id)
            .where(
                League.espn_league_id == league_id,
                Team.espn_team_id == team_id,
                ~exists().where(TeamManager.team_id == Team.id, TeamManager.user_id == user.id),
            )
        )
        for tid in session.scalars(unclaimed).all():
            session.execute(
                insert(TeamManager)
                .values(
                    user_id=user.id,
                    team_id=tid,
                    state=VERIFIED,
                    how=VERIFIED_AS_OWNER,
                    verified_at=now(),
                )
                .on_conflict_do_nothing(constraint="uq_team_managers_user_team")
            )
    session.commit()
    return user


# ---------------------------------------------------------------------------
# what a user may open
# ---------------------------------------------------------------------------


def _claims(user_id: int) -> Select[tuple[int]]:
    """This user's verified claims, joined out to their seasons and leagues."""
    return (
        select(TeamManager.id)
        .join(Team, Team.id == TeamManager.team_id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .where(TeamManager.user_id == user_id, TeamManager.state == VERIFIED)
    )


def _memberships(user_id: int) -> Select[tuple[int, str]]:
    """This user's memberships, as (ESPN league id, role)."""
    return (
        select(League.espn_league_id, Membership.role)
        .join(League, League.id == Membership.league_id)
        .where(Membership.user_id == user_id)
    )


def role_in_league(session: Session, user_id: int, espn_league_id: int) -> str | None:
    """`owner`, `member`, or None when this user is not in the league."""
    row = session.execute(
        _memberships(user_id).where(League.espn_league_id == espn_league_id)
    ).first()
    return str(row.role) if row is not None else None


def is_member(session: Session, user_id: int, espn_league_id: int) -> bool:
    """Whether this user is a member of this league, in either role."""
    return role_in_league(session, user_id, espn_league_id) is not None


def is_league_owner(session: Session, user_id: int, espn_league_id: int) -> bool:
    """Whether this user is an owner of this league: its invites and claims are his."""
    return role_in_league(session, user_id, espn_league_id) == OWNER_ROLE


def manages_team(
    session: Session, user_id: int, espn_league_id: int, season: int, espn_team_id: int
) -> bool:
    """Whether this user is a verified manager of this team in this season."""
    claims = _claims(user_id).where(
        League.espn_league_id == espn_league_id,
        LeagueSeason.season == season,
        Team.espn_team_id == espn_team_id,
    )
    return bool(session.scalar(select(claims.exists())))


def member_league_ids(session: Session, user_id: int) -> set[int]:
    """The ESPN ids of every league this user is a member of."""
    return {league for league, _ in member_leagues(session, user_id)}


def member_leagues(session: Session, user_id: int) -> list[tuple[int, str]]:
    """Every league this user is a member of, as (ESPN league id, role), in id order."""
    rows = session.execute(_memberships(user_id).order_by(League.espn_league_id)).all()
    return [(int(league), str(role)) for league, role in rows]


@dataclass(frozen=True)
class ManagedTeam:
    espn_league_id: int
    season: int
    espn_team_id: int
    name: str
    state: str


def managed_teams(session: Session, user_id: int) -> list[ManagedTeam]:
    """Every claim this user has, of any state, newest season first."""
    rows = session.execute(
        select(
            League.espn_league_id,
            LeagueSeason.season,
            Team.espn_team_id,
            Team.name,
            TeamManager.state,
        )
        .select_from(TeamManager)
        .join(Team, Team.id == TeamManager.team_id)
        .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .where(TeamManager.user_id == user_id)
        .order_by(League.espn_league_id, LeagueSeason.season.desc())
    ).all()
    return [
        ManagedTeam(int(lid), int(season), int(tid), str(name), str(state))
        for lid, season, tid, name, state in rows
    ]


def active_entitlement(session: Session, user_id: int) -> Entitlement | None:
    """The user's live entitlement to the team tier, if any; `owner` first."""
    return session.scalar(
        select(Entitlement)
        .where(
            Entitlement.user_id == user_id,
            Entitlement.tier == TEAM_TIER,
            or_(Entitlement.valid_until.is_(None), Entitlement.valid_until > now()),
        )
        .order_by((Entitlement.source == "owner").desc(), Entitlement.valid_until.desc())
        .limit(1)
    )
