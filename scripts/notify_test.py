#!/usr/bin/env python3
"""Send a one-line test message, to confirm delivery is set up.

Usage:
    python scripts/notify_test.py             # send, if mail is configured
    python scripts/notify_test.py --dry-run   # print what would be sent, send nothing
    python scripts/notify_test.py --preview DIR   # write every mail's HTML to DIR, send nothing

For running on the VPS after editing `.env`, so a misconfigured mail server
is found now rather than at 09:00 tomorrow when a digest silently fails. It
touches no database, reads no ESPN, and marks nothing: it is the delivery
half of `scripts/digest.py` and nothing else.

Email is the only channel there is (2026-09-22): FCP_DIGEST_URL and the
Telegram bot are gone.

**--preview writes the account mail the design has to be looked at in**: the
sign-in link and the address confirmation, each as an HTML file and a .txt
beside it, with a sample link that goes nowhere. It opens no connection. The
digest and the alert have their own preview
(`scripts/digest.py --html`), because they need a database to be about
anything.

What is printed is the host, the port, the sender and the recipients. The
SMTP password is not printed, and is not in the error message either when a
login is refused.

Exit codes:
    0   delivered, or printed with --dry-run, or written with --preview
    1   the send failed, or no mail is configured
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Run by path, so `scripts/` is on sys.path and the repo root is not; the
# recommender CLIs do the same, for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import notify
from app.config import Settings, get_settings
from app.mail import Mail, confirm_mail, sign_in_mail

TITLE = "FCP delivery test"

#: The address a preview's links point at when the server has no public URL
#: of its own. It goes nowhere on purpose: a preview is looked at, not used.
SAMPLE_URL = "https://fcp.example"
SAMPLE_TOKEN = "a-sample-token-that-goes-nowhere"


def _previews(public_url: str) -> dict[str, Mail]:
    """The mail with no database behind it, each under the name of its file."""
    base = public_url.rstrip("/")
    return {
        "sign-in": sign_in_mail(f"{base}/auth/callback?token={SAMPLE_TOKEN}", public_url=base),
        "confirm-address": confirm_mail(
            f"{base}/account/alerts?token={SAMPLE_TOKEN}", public_url=base
        ),
    }


def write_previews(directory: Path, public_url: str) -> list[Path]:
    """Write each mail as `<name>.html` and `<name>.txt`; the files written."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, mail in _previews(public_url).items():
        page = directory / f"{name}.html"
        page.write_text(mail.html, encoding="utf-8")
        plain = directory / f"{name}.txt"
        plain.write_text(f"Subject: {mail.subject}\n\n{mail.text}", encoding="utf-8")
        written += [page, plain]
    return written


def _describe(settings: Settings) -> list[str]:
    if not settings.email_configured:
        return []
    login = f", as {settings.fcp_smtp_user}" if settings.fcp_smtp_user else ", no login"
    tls = "implicit TLS" if settings.fcp_smtp_port == notify.IMPLICIT_TLS_PORT else "STARTTLS"
    return [
        f"  email: {settings.fcp_smtp_host}:{settings.fcp_smtp_port} ({tls}{login}), "
        f"from {settings.fcp_email_from} to {', '.join(settings.email_recipients)}"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, send nothing")
    parser.add_argument(
        "--preview",
        metavar="DIR",
        help="Write the account mail's HTML and text to this directory. Sends nothing.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.preview:
        where = settings.fcp_public_url or SAMPLE_URL
        for path in write_previews(Path(args.preview), where):
            print(f"  wrote {path}")
        print(f"Links point at {where}. Nothing was sent.")
        return 0

    described = _describe(settings)
    if not described:
        print(
            "No mail is configured. It needs FCP_SMTP_HOST, FCP_EMAIL_FROM and FCP_EMAIL_TO.",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(UTC)
    text = f"fcp-core delivery test, {now:%Y-%m-%d %H:%M:%S} UTC. Nothing is wrong."
    if args.dry_run:
        print("Would send to:")
        for line in described:
            print(line)
        print(f"\nSubject: {TITLE}\n{text}")
        return 0

    print("Sending a test message to:")
    for line in described:
        print(line)
    results = notify.deliver(settings, text, title=TITLE)

    for result in results:
        print(f"  {result.describe()}")
    if any(not result.sent for result in results):
        print("The send failed; fix .env on the VPS.", file=sys.stderr)
        return 1
    print("Delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
