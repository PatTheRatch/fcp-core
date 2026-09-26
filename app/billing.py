"""The season pass: codes the owner makes, redeeming one, and granting a pass.

docs/accounts.md, "The pass" and "Codes", is the whole story. Kept free of
FastAPI, so the routes (`app/api/upgrade.py`) and the owner's command line
(`scripts/comp_code.py`) call the same functions and cannot drift.

THE PASS

An entitlement is a season pass: one `team` row per user, covering every
team he manages in every league, until `valid_until`. Nothing recurring and
nothing renews itself; when it lapses the viewer is on the free tier again.
A pass from a code runs until the date the code carries, which the owner
sets when he makes it (`default_valid_until` when he does not).

THE SEAMS FOR THE PAYMENT JOB

* `purchase_available()` is False: the upgrade page draws "Buy a season
  pass" disabled, with its reason, until a payment provider is wired in.
* `grant(user_id, source, valid_until, note)` writes a pass. Redeeming a
  code calls it with `comp`; the owner's `grant --email` calls it with
  `comp`; the payment webhook will call it with `purchase`, which the
  source check already allows (migration 0031).

CODES

Twelve characters from an alphabet with no 0, O, 1 or I, grouped
`XXXX-XXXX-XXXX`, from `secrets`: 32 letters, so 60 bits, which nobody
guesses through a rate limit. Kept in clear. Typed back case-insensitively,
with or without the dashes (or with spaces).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import accounts
from app.db.models import (
    CompCode,
    CompCodeRedemption,
    Entitlement,
    LeagueSeason,
    MatchupPeriod,
    User,
)

log = logging.getLogger("fcp.billing")

#: No 0 or O, no 1 or I: a code read aloud or copied by hand survives.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
#: Characters in a code, and in each dash-separated group.
CODE_LENGTH = 12
GROUP = 4

#: The sources a pass can have, in the words the upgrade page uses.
SOURCE_WORDS = {
    "owner": "your own",
    "comp": "a code",
    "purchase": "purchased",
    "trial": "a trial",
    "subscription": "a subscription",
}
SOURCES = tuple(SOURCE_WORDS)

#: A code made without a date runs this long past the newest season's last
#: matchup period: the playoffs, then a month to look back on them.
AFTER_THE_SEASON = timedelta(days=30)
#: ...and this long from today when no season is stored to read that from.
NO_SEASON_STORED = timedelta(days=365)

#: The most uses one code may carry: a league's worth several times over.
MAX_USES = 500
#: The longest note kept with a code or a hand grant.
MAX_NOTE = 200

#: The one sentence a code that does not work hears, whatever is wrong with
#: it: never which part (unknown, revoked, spent, too late, already used by
#: this account).
NO_GOOD = "That code does not work. Check it against the one you were sent."
#: Why the purchase button is disabled, until the payment job turns it on.
PURCHASE_CLOSED = "Purchase is not open yet; use a code."


def now() -> datetime:
    return datetime.now(UTC)


def purchase_available() -> bool:
    """Whether a season pass can be bought here. False until a payment
    provider writes `purchase` passes (the next job); the upgrade page's
    context route reads it and draws the button disabled with its reason."""
    return False


# ---------------------------------------------------------------------------
# the code itself
# ---------------------------------------------------------------------------


def new_code() -> str:
    """A fresh code: twelve characters of `ALPHABET`, `XXXX-XXXX-XXXX`."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return _grouped(raw)


def _grouped(raw: str) -> str:
    return "-".join(raw[i : i + GROUP] for i in range(0, CODE_LENGTH, GROUP))


def normalise_code(typed: str) -> str | None:
    """The code as stored, from what someone typed, or None if it cannot be one.

    Upper case; dashes and spaces anywhere are ignored. Twelve characters of
    the alphabet, or it is not a code.
    """
    if not typed or len(typed) > 64:
        return None
    raw = "".join(c for c in typed.upper() if c not in "- \t")
    if len(raw) != CODE_LENGTH or any(c not in ALPHABET for c in raw):
        return None
    return _grouped(raw)


# ---------------------------------------------------------------------------
# how long a pass runs
# ---------------------------------------------------------------------------


def season_end(session: Session) -> datetime | None:
    """The end of the newest stored season's last matchup period, or None.

    The newest season across every stored league; its last matchup period's
    final day (the playoffs' end), dated by that season's NBA schedule
    (`pickups.state.season_calendar`). None when either is missing.
    """
    from app.pickups.state import season_calendar

    newest = session.scalar(select(func.max(LeagueSeason.season)))
    if newest is None:
        return None
    calendar = season_calendar(session, int(newest))
    if calendar is None:
        return None
    last_day = session.scalar(
        select(func.max(MatchupPeriod.final_scoring_period))
        .join(LeagueSeason, LeagueSeason.id == MatchupPeriod.league_season_id)
        .where(LeagueSeason.season == newest)
    )
    day = int(last_day) if last_day is not None else calendar.last_scoring_period
    return datetime.combine(calendar.date_of(day), time(23, 59, 59), tzinfo=UTC)


def default_valid_until(session: Session, at: datetime | None = None) -> datetime:
    """When a pass from a code made without a date ends.

    The end of the newest season's playoffs plus a month. When no season is
    stored to read that from, or the newest one stored is already over by
    then (a store that has not seen the new season yet), a year from today.
    """
    at = at or now()
    end = season_end(session)
    if end is not None and end + AFTER_THE_SEASON > at:
        return end + AFTER_THE_SEASON
    return at + NO_SEASON_STORED


# ---------------------------------------------------------------------------
# making, listing and revoking codes (the owner's)
# ---------------------------------------------------------------------------


class CodeError(ValueError):
    """A code the owner asked for that cannot be made; one sentence."""


@dataclass(frozen=True)
class Redemption:
    email: str
    redeemed_at: datetime


@dataclass(frozen=True)
class CodeView:
    id: int
    code: str
    note: str
    uses_total: int
    uses_left: int
    valid_until: datetime
    redeem_by: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    #: `open`, `spent`, `revoked` or `expired` (past `redeem_by`, or a pass
    #: that would already have ended).
    state: str
    redeemed: tuple[Redemption, ...]


def _state(code: CompCode, at: datetime) -> str:
    if code.revoked_at is not None:
        return "revoked"
    if code.uses_left < 1:
        return "spent"
    if (code.redeem_by is not None and code.redeem_by <= at) or code.valid_until <= at:
        return "expired"
    return "open"


def make_code(
    session: Session,
    created_by: int | None,
    note: str = "",
    uses: int = 1,
    valid_until: datetime | None = None,
    redeem_by: datetime | None = None,
) -> CompCode:
    """A new code, flushed (the caller commits). `CodeError` when the ask
    cannot be one: no uses, too many, a pass that would already be over."""
    at = now()
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise CodeError(f"A note is at most {MAX_NOTE} characters.")
    if not 1 <= uses <= MAX_USES:
        raise CodeError(f"A code carries between 1 and {MAX_USES} uses.")
    until = valid_until if valid_until is not None else default_valid_until(session, at)
    if until <= at:
        raise CodeError("The pass a code gives has to end after today.")
    if redeem_by is not None and redeem_by <= at:
        raise CodeError("The last day to redeem it has to be after today.")
    for _ in range(5):
        row = CompCode(
            code=new_code(),
            created_by=created_by,
            note=note,
            uses_total=uses,
            uses_left=uses,
            valid_until=until,
            redeem_by=redeem_by,
        )
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            # Two codes alike in 60 bits: vanishingly rare, and simply drawn again.
            continue
        log.info("comp code %s made by user %s, %s use(s)", row.id, created_by, uses)
        return row
    raise CodeError("No unused code could be drawn; try again.")  # pragma: no cover


def list_codes(session: Session) -> list[CodeView]:
    """Every code, newest first, each with who redeemed it and when."""
    at = now()
    codes = session.scalars(select(CompCode).order_by(CompCode.id.desc())).all()
    spent: dict[int, list[Redemption]] = {}
    rows = session.execute(
        select(CompCodeRedemption.code_id, User.email, CompCodeRedemption.redeemed_at)
        .join(User, User.id == CompCodeRedemption.user_id)
        .order_by(CompCodeRedemption.redeemed_at)
    ).all()
    for code_id, email, redeemed_at in rows:
        spent.setdefault(int(code_id), []).append(Redemption(str(email), redeemed_at))
    return [
        CodeView(
            id=c.id,
            code=c.code,
            note=c.note,
            uses_total=c.uses_total,
            uses_left=c.uses_left,
            valid_until=c.valid_until,
            redeem_by=c.redeem_by,
            revoked_at=c.revoked_at,
            created_at=c.created_at,
            state=_state(c, at),
            redeemed=tuple(spent.get(c.id, [])),
        )
        for c in codes
    ]


def revoke(session: Session, code_id: int) -> bool:
    """Revoke a code by id: it redeems nothing more. Passes it already wrote
    stand. True if it was live; False if unknown or already revoked."""
    code = session.get(CompCode, code_id)
    if code is None or code.revoked_at is not None:
        return False
    code.revoked_at = now()
    session.flush()
    log.info("comp code %s revoked", code_id)
    return True


def find_code(session: Session, typed: str) -> CompCode | None:
    """A code by its text, however it was typed."""
    code = normalise_code(typed)
    if code is None:
        return None
    return session.scalar(select(CompCode).where(CompCode.code == code))


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------


def grant(
    session: Session,
    user_id: int,
    source: str,
    valid_until: datetime | None,
    note: str | None = None,
) -> Entitlement:
    """Write a season pass for this user, flushed (the caller commits).

    The one place a pass is written: redeeming a code (`comp`), the owner's
    hand (`comp`, no code), and the payment webhook to come (`purchase`).
    `owner` is not written here: `accounts.ensure_owner` writes that one.
    """
    if source not in SOURCES or source == "owner":
        raise ValueError(f"not a source a pass is granted from: {source!r}")
    row = Entitlement(
        user_id=user_id,
        tier=accounts.TEAM_TIER,
        source=source,
        valid_until=valid_until,
        note=(note or "").strip()[:MAX_NOTE] or None,
    )
    session.add(row)
    session.flush()
    log.info("pass %s granted to user %s (%s)", row.id, user_id, source)
    return row


def latest_pass(session: Session, user_id: int) -> Entitlement | None:
    """His most recent pass, live or lapsed: what the upgrade page names when
    his pass has ended ("your pass ended on ...")."""
    return session.scalar(
        select(Entitlement)
        .where(Entitlement.user_id == user_id, Entitlement.tier == accounts.TEAM_TIER)
        .order_by(Entitlement.valid_until.desc().nulls_first(), Entitlement.id.desc())
        .limit(1)
    )


def end_of_day(day: date) -> datetime:
    """A date the owner typed, as the last second of that day (UTC): a pass
    "until Jun 30" works through Jun 30."""
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def says_until(when: datetime | None) -> str:
    """A pass's end as the pages and the refusals write it: `Jun 30, 2027`."""
    if when is None:
        return "no end date"
    return f"{when:%b} {when.day}, {when.year}"


# ---------------------------------------------------------------------------
# redeeming
# ---------------------------------------------------------------------------


class RedeemRefusedError(Exception):
    """A code that did not redeem: one sentence, and why for the log only."""

    def __init__(self, sentence: str, why: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.why = why


def already_held(pass_: Entitlement) -> str:
    """What a man with a live pass hears when he tries a code: no stacking."""
    if pass_.valid_until is None:
        return "You already have a pass, and it does not end. Keep the code for someone else."
    return (
        f"You already have a pass until {says_until(pass_.valid_until)}. "
        "Keep the code for someone else."
    )


def redeem(session: Session, user_id: int, typed: str) -> Entitlement:
    """Spend one use of a code on this user's season pass; the new pass.

    All in the caller's one transaction (the caller commits, or rolls back
    on `RedeemRefusedError`): the user's row is locked first, so two codes
    at once for one man cannot both write a pass; then the code's row
    (`SELECT ... FOR UPDATE`), so two men racing on a one-use code cannot
    both spend it. A live pass is not stacked on. Every refusal about the
    code itself is the same sentence (`NO_GOOD`).
    """
    code_text = normalise_code(typed)
    # The user first: every redemption for one man waits for the one before.
    session.execute(select(User.id).where(User.id == user_id).with_for_update())
    live = accounts.active_entitlement(session, user_id)
    if live is not None:
        raise RedeemRefusedError(already_held(live), "live pass")
    if code_text is None:
        raise RedeemRefusedError(NO_GOOD, "not a code")
    code = session.scalar(select(CompCode).where(CompCode.code == code_text).with_for_update())
    at = now()
    if code is None:
        raise RedeemRefusedError(NO_GOOD, "unknown")
    if code.revoked_at is not None:
        raise RedeemRefusedError(NO_GOOD, f"code {code.id} revoked")
    if code.uses_left < 1:
        raise RedeemRefusedError(NO_GOOD, f"code {code.id} spent")
    if code.redeem_by is not None and code.redeem_by <= at:
        raise RedeemRefusedError(NO_GOOD, f"code {code.id} past redeem_by")
    if code.valid_until <= at:
        raise RedeemRefusedError(NO_GOOD, f"code {code.id} pass already over")
    used = session.scalar(
        select(CompCodeRedemption.id).where(
            CompCodeRedemption.code_id == code.id, CompCodeRedemption.user_id == user_id
        )
    )
    if used is not None:
        raise RedeemRefusedError(NO_GOOD, f"code {code.id} already used by this user")
    held = grant(session, user_id, "comp", code.valid_until, note=f"code {code.id}")
    session.add(CompCodeRedemption(code_id=code.id, user_id=user_id, entitlement_id=held.id))
    code.uses_left -= 1
    session.flush()
    log.info("code %s redeemed by user %s: pass %s", code.id, user_id, held.id)
    return held


# ---------------------------------------------------------------------------
# counts, for the preflight
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Counts:
    live_passes: int
    open_codes: int


def counts(session: Session) -> Counts:
    """Live passes other than the owner's own, and codes that would redeem now."""
    at = now()
    live = session.scalar(
        select(func.count())
        .select_from(Entitlement)
        .where(
            Entitlement.tier == accounts.TEAM_TIER,
            Entitlement.source != "owner",
            or_(Entitlement.valid_until.is_(None), Entitlement.valid_until > at),
        )
    )
    open_codes = session.scalar(
        select(func.count())
        .select_from(CompCode)
        .where(
            CompCode.revoked_at.is_(None),
            CompCode.uses_left > 0,
            CompCode.valid_until > at,
            or_(CompCode.redeem_by.is_(None), CompCode.redeem_by > at),
        )
    )
    return Counts(live_passes=int(live or 0), open_codes=int(open_codes or 0))
