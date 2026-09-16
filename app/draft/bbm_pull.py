"""Pull the Basketball Monster projection exports with the manager's login.

The room reads two BBM exports from `data/bbm/`: season-total values and
per-game values. Downloading them by hand before every refresh is the chore
this removes. Both come from the same projections page, which is an ASP.NET
WebForms form: the export is a POST of the page's whole form state with
`EXCELBUTTON` pressed, and the value type is the `ValueDisplayType` field
(`Total` or `PerGame`).

Two things about the page, found by logging in (2026-09-16), not guessed:

- Its dropdowns are scripted widgets (`bm-custom-select`), not `<select>`s.
  The browser posts each one under its `data-name` with the selected
  option's `data-value`, so the form state is read from those too.
- Leag$, the value for this league's settings and what the room reads,
  is only exported while the punt panel's `cat_25` box is ticked, so the
  pull ticks it whatever the account has saved.
- The export only carries the columns currently shown, 61 by default. The
  hand-downloaded files had 115, Age and the ADPs among them. Ticking every
  column and pressing Apply (`dcapply_DisplayColumnsId`) brings them back for
  this session only. Save (`dcsave_...`) would change the account's
  settings, and is never pressed.

A rejected postback still answers 200 with an HTML page, so a download counts
only when the body is a workbook (OLE2 magic).
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_URL = "https://basketballmonster.com"
LOGIN_URL = f"{BASE_URL}/login.aspx"
PROJECTIONS_URL = f"{BASE_URL}/projections.aspx"

#: The value types the room reads, keyed by the file suffix it expects.
VALUE_TYPES = {"total": "Total", "pergame": "PerGame"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
TIMEOUT = 180

#: The punt-panel box that, ticked, puts the league-settings values (Leag$,
#: LeagV, PuntV, Punt+) in the export. Found 2026-09-16 when a settings change
#: in the browser unticked it and Leag$ vanished; forced on for every pull.
LEAGUE_VALUES_FIELD = "cat_25"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class BBMPullError(RuntimeError):
    """The login or an export did not give what was expected."""


class BBMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bbm_username: str
    bbm_password: str


@dataclass(frozen=True)
class Export:
    kind: str  # "total" or "pergame"
    body: bytes


@dataclass(frozen=True)
class PullResult:
    season: int
    league: str
    source: str
    exports: list[Export]


def form_state(page: str) -> dict[str, str]:
    """What a browser would post for this page as it stands."""
    form: dict[str, str] = {}
    for match in re.finditer(r"<input([^>]*)>", page):
        attrs = match.group(1)
        name = re.search(r'name="([^"]+)"', attrs)
        if not name:
            continue
        kind = re.search(r'type="([^"]+)"', attrs)
        input_type = kind.group(1).lower() if kind else "text"
        raw = re.search(r'value="([^"]*)"', attrs)
        value = htmllib.unescape(raw.group(1)) if raw else ""
        if input_type in ("checkbox", "radio"):
            if re.search(r"\bchecked\b", attrs):
                form[name.group(1)] = value or "on"
        elif input_type not in ("submit", "button", "image"):
            form[name.group(1)] = value
    for field, selected, _label in custom_selects(page):
        form[field] = selected
    return form


def custom_selects(page: str) -> list[tuple[str, str, str]]:
    """Each scripted dropdown as (field name, selected value, selected label)."""
    out = []
    for match in re.finditer(
        r'data-name="([^"]+)" class="bm-custom-select[^"]*">(.*?)</div></div></div>',
        page,
        re.S,
    ):
        selected = re.search(
            r'data-value="([^"]*)" class="bm-custom-select-option selected">([^<]*)',
            match.group(2),
        )
        if selected:
            out.append(
                (
                    match.group(1),
                    htmllib.unescape(selected.group(1)),
                    htmllib.unescape(selected.group(2)).strip(),
                )
            )
    return out


def page_season(page: str) -> int:
    """The season the page projects: `Projections 26-27` is 2027."""
    found = re.search(r"<h1>\s*Projections\s+\d{2}-(\d{2})\s*</h1>", page)
    if not found:
        raise BBMPullError("could not read the season from the projections page title")
    return 2000 + int(found.group(1))


def logged_out(page: str) -> bool:
    return "must be logged in" in page.lower() or 'name="PasswordTB"' in page


class BBMClient:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT

    def login(self) -> None:
        page = self._get(LOGIN_URL)
        form = form_state(page)
        form.update(
            {
                "UsernameTB": self.username,
                "PasswordTB": self.password,
                "RememberMeCB": "on",
                "LoginButton": "",
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
            }
        )
        if logged_out(self._post(LOGIN_URL, form)):
            raise BBMPullError(
                "BBM login failed; check BBM_USERNAME and BBM_PASSWORD in .env "
                "and that the subscription is active"
            )

    def pull(self) -> PullResult:
        page = self._get(PROJECTIONS_URL)
        if logged_out(page):
            raise BBMPullError("the projections page says the session is logged out")

        # Show every column (Apply, never Save), then export from that page.
        form = form_state(page)
        for column in re.findall(r'name="(dccol_DisplayColumnsId_\d+)"', page):
            form[column] = "1"
        form["__EVENTTARGET"] = "dcapply_DisplayColumnsId"
        page = self._post(PROJECTIONS_URL, form)
        if logged_out(page):
            raise BBMPullError("applying the display columns logged the session out")

        selects = {name: label for name, _value, label in custom_selects(page)}
        exports = []
        for kind, value_type in VALUE_TYPES.items():
            form = form_state(page)
            form["ValueDisplayType"] = value_type
            form["PlayerFilterControl"] = "AllPlayers"
            # Unticked, the export drops Leag$ (and LeagV, PuntV, Punt+).
            form[LEAGUE_VALUES_FIELD] = "25"
            form["EXCELBUTTON"] = ""
            body = self.http.post(
                PROJECTIONS_URL, data=form, headers={"Referer": PROJECTIONS_URL}, timeout=TIMEOUT
            )
            body.raise_for_status()
            if not body.content.startswith(OLE2_MAGIC):
                raise BBMPullError(
                    f"the {kind} export came back as "
                    f"{body.headers.get('Content-Type', '?')} ({len(body.content)} bytes), "
                    "not a workbook"
                )
            exports.append(Export(kind, body.content))
        return PullResult(
            season=page_season(page),
            league=selects.get("pageTitleRowLeague", ""),
            source=selects.get("ProjectionSourceControl", ""),
            exports=exports,
        )

    def _get(self, url: str) -> str:
        response = self.http.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text

    def _post(self, url: str, form: dict[str, str]) -> str:
        response = self.http.post(url, data=form, headers={"Referer": url}, timeout=TIMEOUT)
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
