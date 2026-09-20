#!/usr/bin/env python3
"""Open a browser window that stays logged into ESPN, for the draft tooling.

Usage:
    python scripts/espn_login.py                      # opens the fantasy home
    python scripts/espn_login.py --url "<draft room>"  # opens a draft room

The draft page reader (`app/draft/page.py`) needs a real ESPN session. Copied
`espn_s2` cookies expire, silently, and the failure shows up on draft night.
So instead: one browser window with its own profile in `~/.fcp-core/espn`,
signed in by hand, once. The window stays open and the session persists
across runs, and every later read or click happens in a window the manager
can watch and take over.

The signed-in state is also saved to `~/.fcp-core/espn-state.json`, which a
headless read can load, so a background job never needs the cookies from
`.env` again.

Nothing here reads or writes `.env`, and nothing prints a cookie.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HOME = Path.home() / ".fcp-core"
PROFILE = HOME / "espn"
STATE = HOME / "espn-state.json"
LOG = HOME / "espn-login.log"
SIGNED_OUT = ("Log in Required", "Log In to ESPN")


def note(line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with LOG.open("a") as handle:
        handle.write(f"{stamp}  {line}\n")
    print(f"{stamp}  {line}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--url",
        default="https://fantasy.espn.com/basketball/team",
        help="page to open",
    )
    ap.add_argument("--minutes", type=float, default=180.0, help="how long to keep the window open")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    HOME.mkdir(parents=True, exist_ok=True)
    note(f"opening a window on {args.url.split('?')[0]}")
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        note("window open: sign in to ESPN in it, then leave it open")

        end = time.time() + args.minutes * 60
        signed_in = False
        while time.time() < end:
            try:
                text = page.evaluate("document.body.innerText") or ""
            except Exception:
                note("the window was closed")
                break
            now_signed_in = bool(text) and not any(w in text for w in SIGNED_OUT)
            if now_signed_in and not signed_in:
                context.storage_state(path=str(STATE))
                note(f"signed in; session saved to {STATE}")
            elif signed_in and not now_signed_in:
                note("signed out again")
            signed_in = now_signed_in
            if signed_in:
                context.storage_state(path=str(STATE))
            time.sleep(5)
        note("closing")
        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
