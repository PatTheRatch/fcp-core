"""The NBA's official injury reports: fetching one, reading it, storing it.

ESPN serves a player's injury status *as of the request* and never as a
history, so for any played season the tool does not know who was Out,
Questionable or Available on a given morning. Every backtest and calibration
carries that caveat (docs/trades.md section 7, docs/pickups_backtest.md), and
the reconstructed wire cannot show an injured free agent at all.

The NBA itself publishes the missing history. Since the 2021-22 season every
team submits an injury report for each of its games, and the league posts the
combined report as a PDF snapshot -- hourly through 19 December 2025, every
fifteen minutes since -- at a URL that names the snapshot's own time. The
snapshots stay up, so a season can be walked after the fact.

POINT IN TIME

Every snapshot is kept with the time it was published and nothing is ever
collapsed to a final status. A backtest asking "what was known about this man
at nine in the morning" gets the answer that was on the page at nine in the
morning, not the one the evening report corrected it to. That is the whole
reason for the table; see `app.injuries` for the reading rule.

WHAT THE NBA'S TIMESTAMP MEANS

The PDF's own first line reads `Injury Report: 11/11/25 09:30 AM`. That is
Eastern time, and it is the moment the league compiled the report, not a
deadline or a tip-off. It is what `reported_at` stores, converted to UTC.

It is not the same as the time in the URL. Through 19 December 2025 the URL
named the hour (`..._09AM.pdf`) while the report inside was stamped at half
past it (`09:30 AM`); from 22 December 2025 the URL names the quarter hour
(`..._09_00AM.pdf`) and the stamp matches it. The label inside the file is
what we believe, because it is the one the league wrote.

READING THE PDF

The report is a seven-column table -- game date, game time, matchup, team,
player, current status, reason -- and it is drawn, not tagged: the file
contains placed glyphs with no spaces and no cell structure, so the columns
have to be recovered from geometry.

`nbainjuries` (github.com/mxufc29/nbainjuries, MIT) does this with tabula,
which needs a Java runtime, against pixel boxes hardcoded one set per season.
We read the same PDFs with pdfplumber instead and take the column boundaries
from the table's own header row, which means no per-season constants and no
JVM on the server. Checked against `nbainjuries` on nine reports spanning
five seasons and both URL formats: seven came out identical line for line,
and on the other two ours is right -- a three-line reason (Jared McCain, 11
November 2025) is one row here and three rows, two of them with no player
name, there. What we took from the package is its knowledge of the URL
scheme and the two timestamp formats, which is credited in docs/injuries.md.

A row is anchored on the line carrying a status, because a reason wraps over
as many lines as it needs and is centred on the row it belongs to; every
other line joins the nearest anchor. The one case that rule cannot see is a
reason cut by a page break, where the rest of it is the first thing on the
next page and its anchor is on the page before. Those lines are recognised by
sitting further from the page's first anchor than half the distance to its
second (measured across the nine reports: 0.19 to 0.24 of it for a line that
really does belong to the first row, 0.76 for a carried-over one) and are
joined to the previous page's last row. `orphan_lines` counts anything that
still could not be placed, so a layout change shows up as a number rather
than as quietly mangled text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import InjuryReport
from app.player_names import NameIndex, from_last_first

if TYPE_CHECKING:  # pragma: no cover - typing only
    import requests

#: Where the league posts them. Public, and the only host this job touches.
URL_STEM = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{slug}.pdf"
#: The reports are Eastern time throughout, labels and game times alike.
ET = ZoneInfo("America/New_York")
#: `source`: there is only one today, but a second (a team's own feed, a
#: beat writer's) would have to be told apart from the league's own.
NBA_OFFICIAL = "nba_official"

#: The last hourly snapshot, and the first quarter-hourly one. Between them
#: the league changed both its cadence and its URL format; the two days in
#: the gap published nothing we can name.
LAST_HOURLY = datetime(2025, 12, 19, 15, 30, tzinfo=ET)
FIRST_QUARTER_HOURLY = datetime(2025, 12, 22, 9, 0, tzinfo=ET)

#: The seven columns, in the order the header prints them.
COLUMNS = (
    "Game Date",
    "Game Time",
    "Matchup",
    "Team",
    "Player Name",
    "Current Status",
    "Reason",
)
#: The column a value is carried down from when a row leaves it blank: the
#: report prints a game's date, time, matchup and team once and then lists
#: every player under it.
_CARRIED = (0, 1, 2, 3)
_STATUS = 5
_REASON = 6

#: Every status the league prints. `Available` is a real report line, not an
#: absence of one: a player who was doubtful yesterday and is fit today is
#: listed as Available, which is how an upgrade becomes visible.
STATUSES = frozenset({"Out", "Doubtful", "Questionable", "Probable", "Available"})
#: A team that has not filed yet. Stored, with no player and no status, so
#: "we asked and the team had not said" is on record and can be told apart
#: from "the team said nobody".
NOT_SUBMITTED = "NOT YET SUBMITTED"

_TITLE = re.compile(r"^Injury Report:\s*(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})\s*([AP]M)$")
_FOOTER = re.compile(r"^Page \d+ of \d+$")
#: Word boundaries come from the gaps between glyphs, not from spaces: the
#: file contains none. Within a cell words sit about 2.3pt apart, between
#: columns a hundred times that, so a 1pt tolerance splits them cleanly.
_X_TOLERANCE = 1.0
#: How far right of a column's header a value may start and still be that
#: column's; values are left-aligned about 1pt in from the header.
_COLUMN_SLACK = 2.0
#: A leading line further from the page's first row than this share of the
#: way to the second belongs to the row the page break cut in half.
_CARRY_OVER_SHARE = 0.5


class ReportLayoutError(Exception):
    """The PDF is not a report we recognise. Never a reason to store a guess."""


@dataclass(frozen=True)
class ReportLine:
    """One line of one report: a player's status, or a team's silence."""

    game_date: date
    #: Tip-off as printed, e.g. `07:30 (ET)`; empty when the report omits it.
    game_time: str
    matchup: str
    team: str
    #: As printed, `Last, First`. Empty on a NOT YET SUBMITTED line.
    player_name_raw: str
    #: One of `STATUSES`, or empty on a NOT YET SUBMITTED line.
    status: str
    reason: str

    @property
    def submitted(self) -> bool:
        return self.status != ""


@dataclass(frozen=True)
class Report:
    """One snapshot, with the time the league stamped on it."""

    #: The label inside the PDF, Eastern time converted to UTC.
    reported_at: datetime
    lines: tuple[ReportLine, ...]
    #: Lines no row could be found for. Zero in every report we have read.
    orphan_lines: int = 0


# ---------------------------------------------------------------------------
# which snapshots exist, and where
# ---------------------------------------------------------------------------


def _as_et(at: datetime) -> datetime:
    return at.replace(tzinfo=ET) if at.tzinfo is None else at.astimezone(ET)


def report_url(at: datetime) -> str:
    """The URL of the snapshot published at this Eastern time.

    Through `LAST_HOURLY` the URL names only the hour, so a `09:30` report
    lives at `_09AM`; from `FIRST_QUARTER_HOURLY` it names the quarter hour
    exactly. A time in the gap between them has no URL and is refused.
    """
    eastern = _as_et(at)
    if eastern <= LAST_HOURLY:
        slug = eastern.replace(minute=0).strftime("%I%p")
    elif eastern >= FIRST_QUARTER_HOURLY:
        slug = eastern.strftime("%I_%M%p")
    else:
        raise ValueError(
            f"no injury report URL for {eastern:%Y-%m-%d %H:%M %Z}: the league published "
            f"none between {LAST_HOURLY:%Y-%m-%d %H:%M} and {FIRST_QUARTER_HOURLY:%Y-%m-%d %H:%M}"
        )
    return URL_STEM.format(slug=f"{eastern:%Y-%m-%d}_{slug}")


def snapshot_times(day: date, *, which: str = "all") -> list[datetime]:
    """The Eastern times the league published on this date.

    `all` is every one of them -- twenty-four an hourly day, ninety-six a
    quarter-hourly one. `morning` is the one the backtests read, the snapshot
    at `MORNING_HOUR`; `last` is the final snapshot of the day, which is the
    most settled view of that night's games.
    """
    quarter_hourly = datetime.combine(day, datetime.min.time(), tzinfo=ET) >= (
        FIRST_QUARTER_HOURLY - timedelta(days=1)
    )
    step = timedelta(minutes=15) if quarter_hourly else timedelta(hours=1)
    if not quarter_hourly:
        # The hourly URLs name the hour; the reports inside are stamped at
        # half past, which is what `reported_at` will hold.
        start = datetime.combine(day, datetime.min.time(), tzinfo=ET) + timedelta(minutes=30)
    else:
        start = datetime.combine(day, datetime.min.time(), tzinfo=ET)
    times = []
    at = start
    while at.date() == day:
        times.append(at)
        at += step
    if which == "all":
        return [at for at in times if _has_url(at)]
    if which == "morning":
        wanted = [at for at in times if at.hour == MORNING_HOUR]
        return [at for at in (wanted[:1] if wanted else []) if _has_url(at)]
    if which == "last":
        available = [at for at in times if _has_url(at)]
        return available[-1:]
    raise ValueError(f"unknown snapshot selection {which!r}")


def _has_url(at: datetime) -> bool:
    try:
        report_url(at)
    except ValueError:
        return False
    return True


#: The snapshot `--snapshots morning` fetches: the nine o'clock Eastern one,
#: which in the hourly era is the file stamped 09:30 and since the change is
#: the one stamped 09:00.
MORNING_HOUR = 9
#: The moment the backtests read at (`app.injuries.morning_of`), an hour
#: later. It has to be *after* the morning snapshot rather than on it,
#: because the hourly reports were published at half past the hour the URL
#: named: a read at nine would have found only the eight o'clock report and
#: the morning backfill would have stored a file no caller could see. Ten
#: Eastern is still before the earliest tip-off, which is noon.
MORNING_READ_HOUR = 10


# ---------------------------------------------------------------------------
# reading one
# ---------------------------------------------------------------------------


def _lines(page: Any) -> list[tuple[float, list[Any]]]:
    """The page's words gathered into printed lines, top to bottom."""
    rows: dict[float, list[Any]] = {}
    for word in page.extract_words(x_tolerance=_X_TOLERANCE):
        rows.setdefault(round(float(word["top"]), 1), []).append(word)
    return [
        (top, sorted(words, key=lambda w: float(w["x0"]))) for top, words in sorted(rows.items())
    ]


def _header_columns(page: Any) -> tuple[float, list[float]] | None:
    """The header row's top and the left edge of each of its seven columns.

    Taken from the table's own header rather than from stored coordinates,
    which is what lets the same code read all five seasons: when the league
    moves the table, the header moves with it.
    """
    wanted = "".join(COLUMNS).replace(" ", "")
    for top, words in _lines(page):
        if "".join(word["text"] for word in words).replace(" ", "") != wanted:
            continue
        edges: list[float] = []
        index = 0
        for label in COLUMNS:
            edges.append(float(words[index]["x0"]))
            index += len(label.split())
        return top, edges
    return None


def _column_of(x0: float, edges: Sequence[float]) -> int:
    found = 0
    for index, edge in enumerate(edges):
        if x0 >= edge - _COLUMN_SLACK:
            found = index
    return found


def _cells(words: Iterable[Any], edges: Sequence[float]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for word in words:
        out.setdefault(_column_of(float(word["x0"]), edges), []).append(str(word["text"]))
    return out


def _is_anchor(cells: dict[int, list[str]]) -> bool:
    """Whether this printed line starts a report line of its own."""
    return " ".join(cells.get(_STATUS, [])) in STATUSES or (
        " ".join(cells.get(_REASON, [])) == NOT_SUBMITTED
    )


def _stamp(title: str) -> datetime:
    match = _TITLE.match(title)
    if match is None:
        raise ReportLayoutError(f"no report timestamp in {title!r}")
    day, clock, meridiem = match.groups()
    naive = datetime.strptime(f"{day} {clock} {meridiem}", "%m/%d/%y %I:%M %p")
    return naive.replace(tzinfo=ET).astimezone(UTC)


def _page_rows(
    page: Any, edges: Sequence[float], header_top: float | None
) -> tuple[list[dict[int, list[str]]], list[list[str]]]:
    """This page's rows, and the leading lines that belong to the page before."""
    body = []
    for top, words in _lines(page):
        text = " ".join(word["text"] for word in words)
        if _TITLE.match(text) or _FOOTER.match(text):
            continue
        if header_top is not None and abs(top - header_top) < 0.5:
            continue
        body.append((top, words))

    anchors = [top for top, words in body if _is_anchor(_cells(words, edges))]
    if not anchors:
        # A page of nothing but a carried-over reason: everything continues
        # the row before it.
        return [], [[str(word["text"]) for word in words] for _, words in body]

    carried: list[list[str]] = []
    leading = [(top, words) for top, words in body if top < anchors[0]]
    if leading:
        reach = _CARRY_OVER_SHARE * (anchors[1] - anchors[0]) if len(anchors) > 1 else float("inf")
        for top, words in list(leading):
            if anchors[0] - top > reach:
                carried.append([str(word["text"]) for word in words])
                body.remove((top, words))

    rows: list[dict[int, list[str]]] = [{index: [] for index in range(7)} for _ in anchors]
    for top, words in body:
        nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i] - top))
        for column, texts in _cells(words, edges).items():
            rows[nearest][column].extend(texts)
    return rows, carried


def parse_report(data: bytes) -> Report:
    """One report PDF as its lines, in the order the league printed them."""
    import pdfplumber  # optional; only this reader needs it
    from pdfplumber.utils.exceptions import PdfminerException

    try:
        opened = pdfplumber.open(_buffer(data))
    except PdfminerException as error:
        # The CDN served something that is not a PDF at all -- an error page,
        # a redirect. Our own error, so a caller need not know pdfplumber.
        raise ReportLayoutError(f"not a PDF: {error}") from None

    with opened as pdf:
        if not pdf.pages:
            raise ReportLayoutError("the report has no pages")
        header = _header_columns(pdf.pages[0])
        if header is None:
            raise ReportLayoutError("the report has no column header; the layout has changed")
        _, edges = header
        title_words = _lines(pdf.pages[0])[0][1]
        reported_at = _stamp(" ".join(str(word["text"]) for word in title_words))

        rows: list[dict[int, list[str]]] = []
        orphans = 0
        for page in pdf.pages:
            header_top = None
            found = _header_columns(page)
            if found is not None:
                header_top, _ = found
            page_rows, carried = _page_rows(page, edges, header_top)
            for words in carried:
                if rows:
                    rows[-1][_REASON].extend(words)
                else:  # pragma: no cover - page one cannot carry anything over
                    orphans += 1
            rows.extend(page_rows)

    return Report(
        reported_at=reported_at,
        lines=tuple(_to_lines(rows)),
        orphan_lines=orphans,
    )


def _buffer(data: bytes) -> Any:
    from io import BytesIO

    return BytesIO(data)


def _to_lines(rows: list[dict[int, list[str]]]) -> Iterator[ReportLine]:
    """Rows as report lines, with the game's own columns carried down."""
    carried = {index: "" for index in _CARRIED}
    for row in rows:
        values = {index: " ".join(row[index]) for index in range(7)}
        for index in _CARRIED:
            if values[index]:
                carried[index] = values[index]
            else:
                values[index] = carried[index]
        try:
            game_date = datetime.strptime(values[0], "%m/%d/%Y").date()
        except ValueError:
            raise ReportLayoutError(f"unreadable game date {values[0]!r}") from None
        yield ReportLine(
            game_date=game_date,
            game_time=values[1],
            matchup=values[2],
            team=values[3],
            player_name_raw=values[4],
            status=values[5],
            reason=values[6],
        )


# ---------------------------------------------------------------------------
# fetching one
# ---------------------------------------------------------------------------

#: The league's CDN serves these to anyone, but we still say who we are and
#: still go slowly: this job reads a public file a few times a second at most.
USER_AGENT = "fcp-core/0.1 (fantasy basketball research; contact via github)"


def fetch_report(at: datetime, *, session: requests.Session | None = None) -> Report | None:
    """The snapshot published at this Eastern time, or None if there is none.

    A 403 or 404 means the league published nothing then, which is ordinary
    -- the cadence has changed twice -- and is not an error. Anything else
    raises, so the backfill can retry it.
    """
    import requests

    http = session or requests.Session()
    response = http.get(report_url(at), headers={"User-Agent": USER_AGENT}, timeout=60)
    if response.status_code in (403, 404):
        return None
    response.raise_for_status()
    return parse_report(response.content)


# ---------------------------------------------------------------------------
# storing one
# ---------------------------------------------------------------------------

#: The league prints full team names; the schedule is keyed on ESPN's pro
#: team ids. Every NBA nickname is unique in its last word, including
#: `Portland Trail Blazers`, so the tail of the name places a team whether
#: the league writes `LA Clippers` or `Los Angeles Clippers`.
_NICKNAMES: dict[str, str] = {
    "hawks": "ATL",
    "celtics": "BOS",
    "nets": "BKN",
    "hornets": "CHA",
    "bulls": "CHI",
    "cavaliers": "CLE",
    "mavericks": "DAL",
    "nuggets": "DEN",
    "pistons": "DET",
    "warriors": "GSW",
    "rockets": "HOU",
    "pacers": "IND",
    "clippers": "LAC",
    "lakers": "LAL",
    "grizzlies": "MEM",
    "heat": "MIA",
    "bucks": "MIL",
    "timberwolves": "MIN",
    "pelicans": "NOP",
    "knicks": "NYK",
    "thunder": "OKC",
    "magic": "ORL",
    "76ers": "PHL",
    "suns": "PHO",
    "blazers": "POR",
    "kings": "SAC",
    "spurs": "SAS",
    "jazz": "UTA",
    "raptors": "TOR",
    "wizards": "WAS",
}


def pro_team_id(team: str) -> int | None:
    """ESPN's pro team id for a team as the league names it, or None."""
    from espn_api.basketball.constant import PRO_TEAM_MAP

    abbreviation = _NICKNAMES.get(team.strip().rsplit(" ", 1)[-1].lower())
    if abbreviation is None:
        return None
    for team_id, known in PRO_TEAM_MAP.items():
        if str(known) == abbreviation:
            return int(team_id)
    return None  # pragma: no cover - every abbreviation above is in the map


@dataclass
class StoreResult:
    """What one report's storage came to."""

    lines: int = 0
    inserted: int = 0
    matched: int = 0
    unmatched: int = 0
    #: Names no player of ours has, in the order the report printed them;
    #: the backfill tallies them so the doc can report the match rate.
    unmatched_names: list[str] = field(default_factory=list)


def store_report(
    session: Session, report: Report, index: NameIndex, *, fetched_at: datetime | None = None
) -> StoreResult:
    """Write one report's lines, skipping any already stored.

    Idempotent by the unique key: the same snapshot loaded twice inserts
    nothing the second time, which is what lets a backfill resume.
    """
    out = StoreResult()
    values = []
    seen: set[tuple[date, str, str]] = set()
    for line in report.lines:
        out.lines += 1
        player_id = None
        if line.player_name_raw:
            player_id = index.match(from_last_first(line.player_name_raw))
            if player_id is None:
                out.unmatched += 1
                out.unmatched_names.append(line.player_name_raw)
            else:
                out.matched += 1
        key = (line.game_date, line.team, line.player_name_raw)
        if key in seen:
            # The same man listed twice for one game date in one snapshot:
            # the unique key holds one row, and the first wins.
            continue
        seen.add(key)
        values.append(
            {
                "reported_at": report.reported_at,
                "game_date": line.game_date,
                "game_time": line.game_time or None,
                "matchup": line.matchup or None,
                "team": line.team,
                "pro_team_id": pro_team_id(line.team),
                "player_name_raw": line.player_name_raw,
                "player_id": player_id,
                "status": line.status or None,
                "reason": line.reason or None,
                "source": NBA_OFFICIAL,
                "fetched_at": fetched_at or datetime.now(UTC),
            }
        )
    if values:
        added = session.execute(
            insert(InjuryReport)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_injury_reports_key")
            .returning(InjuryReport.id)
        ).all()
        out.inserted = len(added)
    return out
