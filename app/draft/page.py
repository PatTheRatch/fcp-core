"""The draft page, read from a headless browser.

Optional. Imports Playwright only when used, so the rest of the draft code
and the typed fallback need nothing installed. `pip install -e '.[live]'`
and `playwright install chromium` to enable it.

Why a browser at all: the read API carries nothing of a draft in progress
and the draft client never polls, so the only place the board exists
outside ESPN's websocket is the rendered page. The page is opened with the
same SWID and espn_s2 cookies the ingest uses, which is a second session on
the account; during the mock draft a second tab caused the first no trouble.

This module only fetches text. Reading it is `app.draft.feed`, which is
pure and tested; keeping the browser out of the parser is what lets the
parser be adjusted from a captured snapshot rather than a live draft.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

#: The draft room does not render its board immediately; this is how long to
#: wait for the ticker before giving up on the page as a draft page.
LOAD_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class Cookies:
    swid: str
    espn_s2: str

    def for_playwright(self) -> list[dict[str, Any]]:
        return [
            {"name": "SWID", "value": self.swid, "domain": ".espn.com", "path": "/"},
            {"name": "espn_s2", "value": self.espn_s2, "domain": ".espn.com", "path": "/"},
        ]


class DraftPage:
    """One open draft page. Use as a context manager; call `text()` to read."""

    def __init__(self, url: str, cookies: Cookies, *, headless: bool = True) -> None:
        self.url = url
        self.cookies = cookies
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> DraftPage:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment
            raise RuntimeError(
                "reading the draft page needs Playwright: "
                "pip install -e '.[live]' && playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        context = self._browser.new_context()
        context.add_cookies(self.cookies.for_playwright())
        self._page = context.new_page()
        self._page.goto(self.url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        return self

    def __exit__(self, *exc: object) -> None:
        for closer in (self._browser, self._playwright):
            try:
                if closer is not None:
                    closer.close() if closer is self._browser else closer.stop()
            except Exception:
                pass

    def text(self) -> str:
        """The page as a person would read it: `document.body.innerText`."""
        if self._page is None:
            raise RuntimeError("page is not open")
        return str(self._page.evaluate("document.body.innerText"))

    @property
    def current_url(self) -> str:
        return str(self._page.url) if self._page is not None else self.url


@contextmanager
def open_draft(url: str, cookies: Cookies, *, headless: bool = True) -> Iterator[DraftPage]:
    with DraftPage(url, cookies, headless=headless) as page:
        yield page
