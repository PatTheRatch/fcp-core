"""The last step's message: what was measured, and what it is being used for.

docs/intake.md has the wording. One email, to whoever connected the league,
through the send path everything else uses (`app.channels`, `app.notify`) and
the house masthead the sign-in email wears (`app.mail.render.plain_html`).
It is not the digest and it is not built by the digest's renderer: it is a
short page of prose with one list on it.

WHAT IT MAY SAY

* **The seasons found**, and how much history that is.
* **Every number, with where it came from and what it rests on.** No number
  without its n: "0.34 a week, measured on your league, 1,120 team-periods",
  or "using the pool of 3 leagues like yours", or "using the default until
  there is enough history". A bar labels and never hides, and a number
  without its sample is a claim rather than a measurement.
* **The bar it recommends**, and a link to the account page to change it.
* **What did not finish**, when a step failed. It never says "ready" over a
  failure, and it says which step and what the league is falling back to in
  the meantime.

WHAT IT MAY NOT SAY

Nothing about any other league: not a name, not a number of its own, not a
count of its teams. The pool is the only thing another league contributes,
and the only thing said about it is how many leagues are in it.

Nothing of ESPN's own words, and no exception text, for the reason every
other message here has the same rule (docs/jobs.md, "Secrets").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app import calibration, channels, intake, memberships
from app.calibration import Calibrated
from app.config import Settings
from app.db.models import League, User
from app.intake import measure
from app.jobs import JobRef
from app.mail import Mail, unsubscribe_headers
from app.mail.render import plain_html

SUBJECT_READY = "{league}: your league's numbers are ready"
SUBJECT_PARTLY = "{league}: your league's numbers, with one part unfinished"

#: What the job's note says when there was nobody to write to. Not a failure:
#: the numbers are measured and the page shows them; he simply has no
#: confirmed address yet, which the Alerts page is for.
NO_ADDRESS = "nothing was sent: whoever connected the league has no confirmed email address"


@dataclass(frozen=True)
class Summary:
    """Everything the email says, as data, so a test can read it without
    rendering it and a page could show the same thing later."""

    league_name: str
    espn_league_id: int
    seasons: tuple[int, ...]
    numbers: tuple[Calibrated, ...]
    #: (step, the sentence it parked with) for every step that failed.
    failed: tuple[tuple[str, str], ...]
    #: Where the account page is, or None when the server has no public URL.
    account_url: str | None
    at: datetime

    @property
    def ready(self) -> bool:
        return not self.failed

    @property
    def history(self) -> str:
        if not self.seasons:
            return "no played season yet"
        first, last = self.seasons[0], self.seasons[-1]
        count = len(self.seasons)
        span = f"{first}" if first == last else f"{first} to {last}"
        return f"{count} season{'' if count == 1 else 's'}, {span}"


def summarise(session: Session, league: League, *, at: datetime, settings: Settings) -> Summary:
    """What the chain did, read back out of the database it wrote to."""
    base = (settings.fcp_public_url or "").rstrip("/")
    return Summary(
        league_name=memberships.league_name(session, league),
        espn_league_id=int(league.espn_league_id),
        seasons=tuple(measure.stored_seasons(session, league.id)),
        numbers=tuple(calibration.bars(session, league.id).all()),
        failed=tuple(intake.progress(session, league.id).failed),
        account_url=f"{base}/account/connections" if base else None,
        at=at,
    )


# ---------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------


def _number(found: Calibrated) -> str:
    """One line of the list: the number, what it is, and where it came from.

    Never a number without its sample. A key with no single number says what
    it rests on instead, which is the honest short form of a table.
    """
    title = calibration.DEFAULTS[found.key].title
    if found.value is None:
        return f"{title}: {found.n} {calibration.unit(found.key)} — below"
    return f"{title}: {found.value:.2f} categories a week — {found.note}"


def lines(summary: Summary) -> list[str]:
    """The message as plain lines, which is also its text part."""
    out = [
        f"{summary.league_name} (ESPN {summary.espn_league_id})",
        "",
    ]
    if summary.ready:
        out.append(
            "Your league has been measured on its own history. Here is every number "
            "the recommendations lean on, and where each one comes from."
        )
    else:
        out.append(
            "Your league has been measured, but one part did not finish. Here is "
            "where each number stands; the ones that could not be measured are "
            "falling back, and nothing is being guessed."
        )
    out += [
        "",
        f"Seasons read: {summary.history}.",
        "",
        "YOUR NUMBERS",
        "",
    ]
    out += [f"  {_number(found)}" for found in summary.numbers]
    bar = next((found for found in summary.numbers if found.key == calibration.STREAM_HURDLE), None)
    if bar is not None and bar.value is not None:
        out += [
            "",
            f"The bar a move has to clear this week is {bar.value:.2f} categories. A move "
            "under it is still shown, with its number, and labelled: the bar says "
            "which moves are worth a look, it never hides one.",
        ]
    if summary.account_url:
        out += ["", f"You can change any of the three bars here: {summary.account_url}"]
    # The trade record is a paragraph, not a line, and putting it in the list
    # above buries the five numbers beside it. It gets its own block, in the
    # words the trade page prints under the deal.
    record = next(
        (found for found in summary.numbers if found.key == calibration.TRADE_RECORD), None
    )
    if record is not None and record.note:
        out += ["", "HOW MUCH TO TRUST THE TRADE NUMBER", "", f"  {record.note}"]
    if summary.failed:
        out += ["", "WHAT DID NOT FINISH", ""]
        out += [f"  {intake.STEP_WORDS.get(step, step)}: {why}" for step, why in summary.failed]
        out += [
            "",
            "Those numbers are using the fallback in the list above until that step "
            "runs again. You can ask for it from the same page.",
        ]
    out += [
        "",
        "Every one of these was measured on your league alone unless the line says "
        "otherwise. Nothing here is a rule: the numbers generate ideas, and you "
        "decide.",
    ]
    return out


def _html(summary: Summary, *, public_url: str | None) -> str:
    """The same words in the house style, as the sign-in email is."""
    body = []
    for line in lines(summary)[2:]:
        if not line.strip():
            continue
        if line.isupper():
            body.append(
                '<p style="margin:18px 0 6px;font-family:Arial Narrow,Arial,sans-serif;'
                "font-size:12px;letter-spacing:.09em;text-transform:uppercase;"
                f'color:#5E6167;">{_escape(line)}</p>'
            )
        elif line.startswith("  "):
            body.append(
                '<p style="margin:4px 0 0;font-family:Georgia,serif;font-size:14px;'
                f'line-height:1.5;">{_escape(line.strip())}</p>'
            )
        else:
            body.append(
                '<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:15px;'
                f'line-height:1.5;">{_escape(line)}</p>'
            )
    return plain_html(
        f"{summary.league_name}: your numbers",
        "Your league",
        "".join(body),
        public_url=public_url,
    )


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def mail_for(summary: Summary, *, public_url: str | None = None) -> Mail:
    """The message, rendered and delivered nowhere. What the tests read."""
    subject = (SUBJECT_READY if summary.ready else SUBJECT_PARTLY).format(
        league=summary.league_name
    )
    return Mail(
        subject=subject,
        text="\n".join(lines(summary)) + "\n",
        html=_html(summary, public_url=public_url),
        headers=unsubscribe_headers(public_url),
    )


# ---------------------------------------------------------------------------
# sending it
# ---------------------------------------------------------------------------


def deliver(session: Session, summary: Summary, *, job: JobRef, settings: Settings) -> str:
    """Mail it to whoever asked for the intake, and say what happened.

    The existing send path: his own verified addresses
    (`app.channels.deliver`), which is the same one the digest and the
    address confirmation use. No new channel, no second renderer, and
    nothing is sent to an address that has not been confirmed.
    """
    mail = mail_for(summary, public_url=settings.fcp_public_url)
    user = session.get(User, job.user_id) if job.user_id is not None else None
    if user is None:
        return f"{_ready_words(summary)}; {NO_ADDRESS}"
    mine = channels.verified(session, user.id)
    if not mine:
        return f"{_ready_words(summary)}; {NO_ADDRESS}"
    results = channels.deliver(
        mine,
        mail.text,
        title=mail.subject,
        settings=settings,
        html=mail.html,
        headers=mail.headers,
    )
    sent = [result for result in results if result.sent]
    failed = [result.channel for result in results if not result.sent]
    note = f"{_ready_words(summary)}; sent to {len(sent)} of {len(results)} address(es)"
    return note + (f"; failed: {', '.join(failed)}" if failed else "")


def _ready_words(summary: Summary) -> str:
    if summary.ready:
        return f"measured on {summary.history}"
    return f"measured on {summary.history}, {len(summary.failed)} step(s) unfinished"


def preview(session: Session, league: League, *, at: datetime, settings: Settings) -> Mail:
    """The message this league would get, built and sent nowhere.

    `scripts/enqueue.py --intake-email ESPN_LEAGUE_ID` prints it, which is
    how the wording is reviewed without waiting for a chain to finish and
    without an address anywhere near it.
    """
    return mail_for(
        summarise(session, league, at=at, settings=settings),
        public_url=settings.fcp_public_url,
    )


__all__ = [
    "NO_ADDRESS",
    "Summary",
    "deliver",
    "lines",
    "mail_for",
    "preview",
    "summarise",
]
