"""The mail this product sends: a subject, a text part and an HTML part.

Everything goes by email now (`app/notify.py`), and an email is a page. This
package builds the three pieces of each one and hands them to the transport:

* `digest_mail` -- the morning digest (`app.digest.Digest`).
* `alert_mail` -- the urgent roster change between digests.
* `sign_in_mail`, `confirm_mail` -- the two links the account pages send.

THE TWO PARTS

The text part is `Digest.render()` itself: it is what `--dry-run` prints,
what the tests hold line for line, and what a reader in a terminal client
sees. It is not an apology for the HTML. The HTML part is
`app/mail/render.py`, the same content as a page in the house style.

THE TWO FORMS (2026-09-23)

Compact by default, full as an option (`Subscription.length`). Both parts
follow the one choice: a compact HTML page has the compact text beside it,
and the league's own news, which the long form appends to the text, is a
count and a link in the short one. docs/jobs.md, "The two forms".

THE SUBJECT

    {team}: {the one thing worth opening it for, in at most eight words}

The one thing, in this order: the place in tonight's lineup that will
produce nothing while a man on the bench would have (it expires at tip-off,
so nothing outranks it); then the top move that clears the bar; then, when
neither exists, that there is nothing to fix. Eight words because a phone
shows about forty characters of a subject and a manager decides whether to
open it there. The team's name leads because a manager in two leagues has
two of these.

DELIVERABILITY

* `List-Unsubscribe` points at the Alerts page, which is where he turns the
  topics off. RFC 8058's one-click `List-Unsubscribe-Post` is deliberately
  **not** sent: one-click means a POST from the mail provider with no
  session, and there is no route here that would honour it safely.
* No tracking of any kind. No pixel, no redirect through a counter, no
  per-reader link. `Message-Id` and the headers the library writes are all
  that identify a message.
* The From name has to be stable (`FCP_EMAIL_FROM`, .env.example), and the
  DMARC policy is still `p=none` and owed a move to `p=quarantine`
  (docs/jobs.md, "Deliverability").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.digest import Digest
from app.mail.render import alert_html, button, digest_html, lines_html, plain_html
from app.pickups.today import TodayReport
from app.subscriptions import Subscription, everything

#: Words of the subject after the team's name. A phone shows about forty
#: characters and a manager decides there whether to open it.
SUBJECT_WORDS = 8

DIGEST_SUBJECT_FALLBACK = "nothing to fix today"
ALERT_SUBJECT_FALLBACK = "a change on your roster"


@dataclass(frozen=True)
class Mail:
    """One message, built and not sent."""

    subject: str
    text: str
    html: str
    headers: dict[str, str]


def _eight(words: str) -> str:
    """At most `SUBJECT_WORDS` words, and an ellipsis when it was cut."""
    parts = words.split()
    if len(parts) <= SUBJECT_WORDS:
        return " ".join(parts)
    return " ".join(parts[:SUBJECT_WORDS]) + "…"


def _fix_words(report: TodayReport | None) -> str | None:
    """The place going empty tonight, said shortly."""
    if report is None or not report.fix:
        return None
    seat = report.fix[0].seat
    if seat.player is None:
        return f"{seat.slot} is empty tonight"
    return f"{seat.player.name} has no game at {seat.slot}"


def _move_words(digest: Digest) -> str | None:
    """The top move that clears the bar, said shortly."""
    week = digest.week_report
    if week is not None and week.recommended:
        move = week.recommended[0]
        if move.drop is not None:
            return f"add {move.add.name}, drop {move.drop.name}"
        return f"add {move.add.name}"
    season = digest.season_report
    best = season.recommended if season is not None else None
    if best is not None and best.into:
        coming = best.into[0].name
        if best.out:
            return f"add {coming}, drop {best.out[0].name}"
        return f"add {coming}"
    return None


def digest_subject(digest: Digest) -> str:
    """`{team}: {the one thing worth opening it for, in eight words}`."""
    words = _fix_words(digest.today_report) or _move_words(digest) or DIGEST_SUBJECT_FALLBACK
    return f"{digest.team_name}: {_eight(words)}"


def alert_subject(team: str, lines: Sequence[str]) -> str:
    """The same rule, over the alert's own first line."""
    first = lines[0] if lines else ALERT_SUBJECT_FALLBACK
    return f"{team}: {_eight(first)}"


def unsubscribe_headers(public_url: str | None) -> dict[str, str]:
    """`List-Unsubscribe`, pointing at the Alerts page.

    A URL and not a `mailto:`: turning a topic off is a thing he does on the
    page, where he can see what he is turning off. No `List-Unsubscribe-Post`
    (the module docstring says why).
    """
    base = (public_url or "").rstrip("/")
    if not base:
        return {}
    return {"List-Unsubscribe": f"<{base}/account/alerts>"}


def digest_mail(
    digest: Digest,
    *,
    wanted: Subscription | None = None,
    public_url: str | None = None,
    league_tail: str = "",
) -> Mail:
    """The morning digest, both parts, in the length he asked for.

    `league_tail` is the plain league section the job appends to the text
    message (`app.job_kinds`); the HTML has the same news in "What changed",
    filtered by his topics, so it is not repeated there. **The compact form
    does not take it**: its whole point is that the league's traffic is a
    count and a link, and forty lines of it under the short message would
    undo that. The text part follows the HTML part's length, because they are
    two halves of one message.
    """
    held = wanted or everything()
    tail = "" if held.compact else league_tail
    text = digest.render(compact=held.compact) + (f"\n\n{tail}" if tail else "")
    return Mail(
        subject=digest_subject(digest),
        text=text,
        html=digest_html(digest, wanted=held, public_url=public_url),
        headers=unsubscribe_headers(public_url),
    )


def alert_mail(
    team: str,
    text: str,
    *,
    when: str,
    public_url: str | None = None,
) -> Mail:
    """The urgent roster change, both parts."""
    lines = [line for line in text.splitlines() if line.strip()]
    return Mail(
        subject=alert_subject(team, lines),
        text=text,
        html=alert_html(team, lines, when=when, public_url=public_url),
        headers=unsubscribe_headers(public_url),
    )


def lines_mail(league: str, text: str, *, when: str, public_url: str | None = None) -> Mail:
    """A digest with no `Digest` behind it: a league the listener does not
    follow, whose message is the week's plan, the churn and the league's own
    news as lines. The subject names the league rather than a team, because
    that is what this one is about."""
    lines = text.splitlines()
    return Mail(
        subject=f"{league}: {_eight('what your league did')}",
        text=text,
        html=lines_html(league, lines, when=when, public_url=public_url),
        headers=unsubscribe_headers(public_url),
    )


SIGN_IN_SUBJECT = "Your FCP sign-in link"
CONFIRM_SUBJECT = "Confirm this address for FCP alerts"


def sign_in_mail(link: str, *, public_url: str | None = None) -> Mail:
    """The sign-in link: the same masthead and footer as the digest, the link
    as a button and as a plain URL under it, and the text part beside it."""
    text = (
        "Sign in to FCP:\n\n"
        f"{link}\n\n"
        "The link works once, for 15 minutes. If you did not ask for it, "
        "ignore this email and nothing happens.\n"
    )
    body = (
        '<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:15px;line-height:1.5;">'
        "Open this to sign in. It works once, for 15 minutes.</p>"
        + button(link, "Sign in")
        + '<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:14px;'
        'line-height:1.5;color:#5E6167;">If you did not ask for it, ignore this '
        "email and nothing happens.</p>"
    )
    return Mail(
        subject=SIGN_IN_SUBJECT,
        text=text,
        html=plain_html("Sign in", "Full Court Press", body, public_url=public_url),
        headers={},
    )


def confirm_mail(link: str, *, public_url: str | None = None) -> Mail:
    """The link that confirms a new alert address."""
    text = (
        "Someone (we hope you) asked for FCP's digest and alerts to come to this "
        f"address. To confirm, open this link while signed in to FCP:\n\n{link}\n\n"
        "It works once, for a day. If this was not you, ignore it and nothing is sent.\n"
    )
    body = (
        '<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:15px;line-height:1.5;">'
        "Someone (we hope you) asked for FCP&#8217;s digest and alerts to come to this "
        "address. Open this while signed in to FCP to confirm it. It works once, for a day.</p>"
        + button(link, "Confirm this address")
        + '<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:14px;'
        'line-height:1.5;color:#5E6167;">If this was not you, ignore it and nothing '
        "is sent here.</p>"
    )
    return Mail(
        subject=CONFIRM_SUBJECT,
        text=text,
        html=plain_html("Confirm this address", "Your account", body, public_url=public_url),
        headers=unsubscribe_headers(public_url),
    )


__all__ = [
    "CONFIRM_SUBJECT",
    "SIGN_IN_SUBJECT",
    "SUBJECT_WORDS",
    "Mail",
    "alert_mail",
    "alert_subject",
    "confirm_mail",
    "digest_mail",
    "digest_subject",
    "lines_mail",
    "sign_in_mail",
    "unsubscribe_headers",
]
