#!/usr/bin/env python3
"""Send a one-line test message, to confirm delivery is set up.

Usage:
    python scripts/notify_test.py            # every configured channel
    python scripts/notify_test.py --email    # the email channel only
    python scripts/notify_test.py --url      # the ntfy/Telegram channel only

For running on the VPS after editing `.env`, so a misconfigured channel is
found now rather than at 09:00 tomorrow when a digest silently fails. It
touches no database, reads no ESPN, and marks nothing: it is the delivery
half of `scripts/digest.py` and nothing else.

What is printed is the host, the port, the sender and the recipients. The
SMTP password is not printed, and is not in the error message either when a
login is refused.

Exit codes:
    0   every channel asked for delivered
    1   a channel failed, or none was configured
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


def _settings_for(args: argparse.Namespace, settings: Settings) -> Settings:
    """The settings with the channels the flags did not ask for switched off."""
    if not args.email and not args.url:
        return settings
    return settings.model_copy(
        update={
            "fcp_digest_url": settings.fcp_digest_url if args.url else None,
            "fcp_smtp_host": settings.fcp_smtp_host if args.email else None,
        }
    )


def _describe(settings: Settings) -> list[str]:
    lines: list[str] = []
    if settings.fcp_digest_url:
        shape = "Telegram" if settings.fcp_digest_chat_id else "ntfy"
        lines.append(f"  url:   {shape}, POST to the configured URL")
    if settings.email_configured:
        login = f", as {settings.fcp_smtp_user}" if settings.fcp_smtp_user else ", no login"
        tls = "implicit TLS" if settings.fcp_smtp_port == notify.IMPLICIT_TLS_PORT else "STARTTLS"
        lines.append(
            f"  email: {settings.fcp_smtp_host}:{settings.fcp_smtp_port} ({tls}{login}), "
            f"from {settings.fcp_email_from} to {', '.join(settings.email_recipients)}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", action="store_true", help="Only the email channel")
    parser.add_argument("--url", action="store_true", help="Only the ntfy/Telegram channel")
    args = parser.parse_args()

    settings = _settings_for(args, get_settings())
    described = _describe(settings)
    if not described:
        asked = "email" if args.email else "url" if args.url else "any"
        print(
            f"No {asked} delivery channel is configured. Email needs FCP_SMTP_HOST, "
            "FCP_EMAIL_FROM and FCP_EMAIL_TO; the other needs FCP_DIGEST_URL.",
            file=sys.stderr,
        )
        return 1

    print("Sending a test message to:")
    for line in described:
        print(line)

    now = datetime.now(UTC)
    text = f"fcp-core delivery test, {now:%Y-%m-%d %H:%M:%S} UTC. Nothing is wrong."
    results = notify.deliver(settings, text, title=TITLE)

    for result in results:
        print(f"  {result.describe()}")
    failed = [result for result in results if not result.sent]
    if failed:
        print(f"{len(failed)} channel(s) failed; fix .env on the VPS.", file=sys.stderr)
        return 1
    print("All channels delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
