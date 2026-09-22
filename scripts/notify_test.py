#!/usr/bin/env python3
"""Send a one-line test message, to confirm delivery is set up.

Usage:
    python scripts/notify_test.py            # send, if mail is configured
    python scripts/notify_test.py --dry-run  # print what would be sent, send nothing

For running on the VPS after editing `.env`, so a misconfigured mail server
is found now rather than at 09:00 tomorrow when a digest silently fails. It
touches no database, reads no ESPN, and marks nothing: it is the delivery
half of `scripts/digest.py` and nothing else.

Email is the only channel there is (2026-09-22): FCP_DIGEST_URL and the
Telegram bot are gone.

What is printed is the host, the port, the sender and the recipients. The
SMTP password is not printed, and is not in the error message either when a
login is refused.

Exit codes:
    0   delivered, or printed with --dry-run
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

TITLE = "FCP delivery test"


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
    args = parser.parse_args()

    settings = get_settings()
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
