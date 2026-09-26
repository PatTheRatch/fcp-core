#!/usr/bin/env python3
"""Make, list and revoke comp codes, and grant a season pass by hand.

Usage:
    python scripts/comp_code.py make --note "for Dennis" [--uses 1]
                                     [--valid-until 2027-06-30] [--redeem-by 2027-01-31]
    python scripts/comp_code.py list
    python scripts/comp_code.py revoke ABCD-EFGH-JKMN
    python scripts/comp_code.py grant --email dennis@example.com --until 2027-06-30
                                      [--note "league treasurer"]

The owner's command line for docs/accounts.md, "Codes": the same functions
as the Codes section on /account/connections (`app/billing.py`), against
the database in `.env`'s DATABASE_URL.

* `make` prints the new code once, with the pass's end date. Without
  `--valid-until` the pass runs to the newest stored season's playoffs plus
  a month (a year when no season is stored). `--redeem-by` is the code's own
  last day; without it the code works until it is spent or revoked.
* `list` prints every code: its state, uses left, the pass's end, the note,
  and who redeemed it and when.
* `revoke` takes the code as it was sent (any case, dashes optional). Passes
  it already wrote stand.
* `grant` writes a pass with no code (source `comp`), for someone the owner
  comps by hand. The account is made if the address has never signed in, so
  the pass is there when he does. A live pass is not stacked on.

No payment provider and no secret of any kind is involved. Exit codes: 0
done, 1 refused (with one line saying why).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from app import accounts, billing
from app.config import get_settings
from app.db.session import make_engine, make_session_factory


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date like 2027-06-30") from None


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    verbs = top.add_subparsers(dest="verb", required=True)
    make = verbs.add_parser("make", help="make a code and print it once")
    make.add_argument("--note", default="", help="who it is for, in your own words")
    make.add_argument("--uses", type=int, default=1, help="how many people may redeem it")
    make.add_argument("--valid-until", type=_day, help="the pass's last day (YYYY-MM-DD)")
    make.add_argument("--redeem-by", type=_day, help="the code's own last day to be redeemed")
    verbs.add_parser("list", help="every code, and who redeemed it")
    revoke = verbs.add_parser("revoke", help="a code redeems nothing more")
    revoke.add_argument("code")
    grant = verbs.add_parser("grant", help="a pass by hand, no code")
    grant.add_argument("--email", required=True)
    grant.add_argument("--until", type=_day, required=True, help="the pass's last day")
    grant.add_argument("--note", default="", help="why, in your own words")
    return top


def _owner_id(session: Session) -> int | None:
    email = accounts.normalise_email(get_settings().fcp_owner_email or "")
    found = accounts.user_by_email(session, email) if email else None
    return found.id if found is not None else None


def run(session: Session, argv: Sequence[str], owner_id: int | None = None) -> tuple[int, str]:
    """One command against this session: the exit code and what to print.
    Commits what it writes."""
    args = parser().parse_args(argv)
    if args.verb == "make":
        try:
            made = billing.make_code(
                session,
                owner_id,
                note=args.note,
                uses=args.uses,
                valid_until=billing.end_of_day(args.valid_until) if args.valid_until else None,
                redeem_by=billing.end_of_day(args.redeem_by) if args.redeem_by else None,
            )
        except billing.CodeError as no:
            session.rollback()
            return 1, f"REFUSED: {no}"
        session.commit()
        uses = "one use" if made.uses_total == 1 else f"{made.uses_total} uses"
        until = billing.says_until(made.valid_until)
        by = f", to be redeemed by {billing.says_until(made.redeem_by)}" if made.redeem_by else ""
        return 0, f"{made.code}\n{uses}; the pass runs until {until}{by}. Note: {made.note or '-'}"
    if args.verb == "list":
        rows = billing.list_codes(session)
        if not rows:
            return 0, "No codes yet."
        lines = []
        for view in rows:
            lines.append(
                f"{view.code}  {view.state:<7}  {view.uses_left}/{view.uses_total} left  "
                f"pass until {billing.says_until(view.valid_until)}  {view.note or '-'}"
            )
            lines += [f"    {r.email}  {r.redeemed_at:%Y-%m-%d %H:%M}" for r in view.redeemed]
        return 0, "\n".join(lines)
    if args.verb == "revoke":
        found = billing.find_code(session, args.code)
        if found is None:
            return 1, "REFUSED: no such code"
        if not billing.revoke(session, found.id):
            return 1, f"REFUSED: {found.code} was already revoked"
        session.commit()
        return 0, f"Revoked {found.code}. The passes it wrote stand."
    # grant
    email = accounts.normalise_email(args.email)
    if email is None:
        return 1, "REFUSED: that is not an email address"
    user = accounts.get_or_create_user(session, email)
    live = accounts.active_entitlement(session, user.id)
    if live is not None:
        session.rollback()
        held = billing.says_until(live.valid_until)
        return 1, f"REFUSED: {email} already has a pass until {held}"
    until = billing.end_of_day(args.until)
    if until <= billing.now():
        return 1, "REFUSED: the pass has to end after today"
    billing.grant(session, user.id, "comp", until, note=args.note or "granted by hand")
    session.commit()
    return 0, f"{email} has a pass until {billing.says_until(until)}."


def main() -> int:
    engine = make_engine(get_settings().database_url)
    try:
        with make_session_factory(engine)() as session:
            code, out = run(session, sys.argv[1:], owner_id=_owner_id(session))
    finally:
        engine.dispose()
    print(out)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
