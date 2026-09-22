"""Reading a live draft off the page.

ESPN's read API does not carry a draft while it happens -- established
against a mock on 2026-09-13, 1,347 polls and zero picks -- and the draft
client itself never polls, because the board arrives over a websocket. What
the page *renders* is another matter: every team's remaining budget, the
player on the block, the live high bid and who holds it, and every
completed pick with its price. That is more than the API would have offered
had it worked, and it is what this module reads.

Everything here is a pure function over the page's text. The browser that
fetches the text lives in `app.draft.page`, behind an optional dependency,
so this can be tested against text captured from a real draft and adjusted
on the night without touching a browser.

WHAT THE PAGE LOOKS LIKE

Captured from the mock, as `document.body.innerText`:

    12. LeBron's Load Management LLC $109AUTO $19
    14. Through The Wire $200 $null
    Cooper FlaggDALSF, PF 2026 STATS: 70 GP, ... PRE-DRAFT VAL: $67
    CURRENT OFFER: $72
    PK 12 OF 195

The ticker row is: slot, team, money left, AUTO if the team is on autopilot,
then that team's live bid on the player up or `null`. The player line runs
the name straight into the NBA team and positions with no separator, so a
player is found by matching known names against the line's start rather
than by splitting it. The pick log was seen rendered as `1 Luka Doncic
Thibs Dust $94`, but not captured as text, so both a one-line and a
one-field-per-line shape are accepted and team names -- which the room
knows -- are the anchor that splits player from team.

TWO CHANNELS, ONE TRUTH

The pick log is authoritative and is what produces picks. The ticker is a
cross-check: when a team's money drops and no logged pick explains it, a
pick was missed, and the player who was on the block at the last snapshot is
the one who went. That inference is offered, never applied silently; the
runner decides.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

# Re-exported: the typing matcher below and every caller of this module have
# always taken `normalise` from here. It lives in `app.player_names` now,
# beside the strict matcher that shares its definition of a name.
from app.player_names import normalise


@dataclass(frozen=True)
class TickerRow:
    team: str
    remaining: int
    #: This team's live bid on the player up. None when it is not bidding.
    bid: int | None
    autopilot: bool


@dataclass(frozen=True)
class LoggedPick:
    overall: int
    player: str
    team: str
    price: int


@dataclass(frozen=True)
class OnBlock:
    player: str
    current_offer: int | None
    high_bidder: str | None
    #: ESPN's own pre-draft auction value, when the card shows it.
    espn_value: int | None


@dataclass(frozen=True)
class BoardSnapshot:
    ticker: tuple[TickerRow, ...]
    picks: tuple[LoggedPick, ...]
    on_block: OnBlock | None
    pick_number: int | None
    total_picks: int | None

    @property
    def budgets(self) -> dict[str, int]:
        return {row.team: row.remaining for row in self.ticker}

    @property
    def in_progress(self) -> bool:
        return bool(self.ticker) and (self.on_block is not None or bool(self.picks))


_TICKER = re.compile(r"^\s*(\d+)\.\s+(.+?)\s+\$(\d+)\s*(AUTO)?\s+\$(\d+|null)\s*$")
_ONE_LINE_PICK = re.compile(r"^\s*(\d+)\s+(.+?)\s+\$(\d+)\s*$")
_PRICE = re.compile(r"^\s*\$(\d+)\s*$")
_NUMBER = re.compile(r"^\s*(\d+)\s*$")
_OFFER = re.compile(r"CURRENT OFFER:\s*\$(\d+)", re.IGNORECASE)
_ESPN_VALUE = re.compile(r"PRE-DRAFT VAL:\s*\$(\d+)", re.IGNORECASE)
_PICK_COUNTER = re.compile(r"\bPK\s+(\d+)\s+OF\s+(\d+)\b", re.IGNORECASE)
#: The player card: name run into a two-or-three-letter NBA team, then
#: positions, then the season's stats. Used only when no known name matches.
_CARD = re.compile(
    r"^(.+?[a-z.'])([A-Z]{2,3})([A-Z]{1,2}(?:,\s*[A-Z]{1,2})*)\s+\d{4}\s+STATS", re.ASCII
)


def _split_player_team(body: str, teams: Sequence[str]) -> tuple[str, str] | None:
    """`Luka Doncic Thibs Dust` -> (`Luka Doncic`, `Thibs Dust`).

    The longest known team name that ends the text wins, so a team called
    `Heat` cannot steal the tail of `Brockley Heat`.
    """
    text = body.strip()
    for team in sorted(teams, key=len, reverse=True):
        if text.endswith(team) and len(text) > len(team):
            player = text[: -len(team)].strip()
            if player:
                return player, team
    return None


def _ticker(lines: Sequence[str]) -> list[TickerRow]:
    rows: list[TickerRow] = []
    for line in lines:
        match = _TICKER.match(line)
        if not match:
            continue
        _, team, remaining, auto, bid = match.groups()
        rows.append(
            TickerRow(
                team=team.strip(),
                remaining=int(remaining),
                bid=None if bid == "null" else int(bid),
                autopilot=auto is not None,
            )
        )
    return rows


def _picks(lines: Sequence[str], teams: Sequence[str]) -> list[LoggedPick]:
    found: dict[int, LoggedPick] = {}
    for index, line in enumerate(lines):
        # One line: `1 Luka Doncic Thibs Dust $94`.
        match = _ONE_LINE_PICK.match(line)
        if match:
            overall, body, price = match.groups()
            split = _split_player_team(body, teams)
            if split:
                found.setdefault(
                    int(overall), LoggedPick(int(overall), split[0], split[1], int(price))
                )
                continue
        # One field per line: `1` / `Luka Doncic` / `Thibs Dust` / `$94`.
        if _NUMBER.match(line) and index + 3 < len(lines):
            player, team, price_line = lines[index + 1 : index + 4]
            price = _PRICE.match(price_line)
            if price and team.strip() in teams and player.strip():
                overall = int(line)
                found.setdefault(
                    overall, LoggedPick(overall, player.strip(), team.strip(), int(price.group(1)))
                )
    return [found[k] for k in sorted(found)]


def _on_block(
    lines: Sequence[str], text: str, ticker: Sequence[TickerRow], players: Sequence[str]
) -> OnBlock | None:
    card = next((line for line in lines if "STATS:" in line or "PRE-DRAFT VAL" in line), None)
    if card is None:
        return None
    name: str | None = None
    # A known name at the start of the card beats any parsing of the card.
    for candidate in sorted(players, key=len, reverse=True):
        if card.startswith(candidate):
            name = candidate
            break
    if name is None:
        match = _CARD.match(card)
        name = match.group(1).strip() if match else None
    if not name:
        return None
    offer = _OFFER.search(text)
    value = _ESPN_VALUE.search(text)
    bidders = [row for row in ticker if row.bid is not None]
    high = max(bidders, key=lambda row: row.bid or 0).team if bidders else None
    return OnBlock(
        player=name,
        current_offer=int(offer.group(1)) if offer else None,
        high_bidder=high,
        espn_value=int(value.group(1)) if value else None,
    )


def parse_board(text: str, teams: Sequence[str], players: Sequence[str] = ()) -> BoardSnapshot:
    """Read one snapshot of the draft page.

    `teams` are the room's team names, which anchor the pick log. `players`
    are known player names, which anchor the card; without them the card is
    parsed by shape, which is less reliable.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    ticker = _ticker(lines)
    counter = _PICK_COUNTER.search(text)
    return BoardSnapshot(
        ticker=tuple(ticker),
        picks=tuple(_picks(lines, teams)),
        on_block=_on_block(lines, text, ticker, players),
        pick_number=int(counter.group(1)) if counter else None,
        total_picks=int(counter.group(2)) if counter else None,
    )


def new_picks(previous: BoardSnapshot | None, current: BoardSnapshot) -> list[LoggedPick]:
    """Picks in the current log that were not in the previous one."""
    seen = {p.overall for p in previous.picks} if previous else set()
    return [p for p in current.picks if p.overall not in seen]


def budget_drops(previous: BoardSnapshot | None, current: BoardSnapshot) -> dict[str, int]:
    """How much each team's money fell between snapshots. A drop is a pick."""
    if previous is None:
        return {}
    before = previous.budgets
    return {
        team: before[team] - after
        for team, after in current.budgets.items()
        if team in before and after < before[team]
    }


def inferred_picks(previous: BoardSnapshot | None, current: BoardSnapshot) -> list[LoggedPick]:
    """Picks the money says happened that the log does not show.

    If exactly one team's money dropped, the log has nothing new, and the
    previous snapshot had a player on the block, that player went to that
    team for the drop. Two drops at once cannot be attributed and are left
    for the log to explain. Nothing here is applied without the runner's
    say-so; it is a prompt, not a fact.
    """
    if previous is None or previous.on_block is None or new_picks(previous, current):
        return []
    drops = budget_drops(previous, current)
    if len(drops) != 1:
        return []
    ((team, spent),) = drops.items()
    overall = (previous.pick_number or len(previous.picks)) + 0
    return [LoggedPick(overall or len(current.picks) + 1, previous.on_block.player, team, spent)]


@dataclass(frozen=True)
class Match:
    name: str
    value: int
    score: float
    #: A second candidate close enough that the choice is a guess.
    rival: str | None = None


def match_name(
    text: str, known: Mapping[str, int], *, cutoff: float = 0.72, margin: float = 0.06
) -> Match | None:
    """The known name a typed or scraped one most likely means.

    Exact after normalisation wins outright. Otherwise the closest by
    sequence similarity, provided it clears `cutoff`; and if the runner-up is
    within `margin`, the result names it so the caller can ask rather than
    guess. `Jokic` finds Nikola Jokic; `Jalen` alone finds nobody safely.
    """
    wanted = normalise(text)
    if not wanted:
        return None
    normalised = {name: normalise(name) for name in known}
    for name, norm in normalised.items():
        if norm == wanted:
            return Match(name, known[name], 1.0)
    single_word = " " not in wanted
    scored: list[tuple[float, str]] = []
    for name, norm in normalised.items():
        score = SequenceMatcher(None, wanted, norm).ratio()
        if single_word:
            # One typed word is a surname, or an attempt at one, and should
            # be scored against each part of the name rather than the whole:
            # `wembanyma` is nine tenths of `wembanyama` and only six tenths
            # of `victor wembanyama`.
            for token in norm.split():
                if len(token) >= 4:
                    score = max(score, SequenceMatcher(None, wanted, token).ratio())
        scored.append((score, name))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < cutoff:
        return None
    best_score, best = scored[0]
    rival = scored[1][1] if len(scored) > 1 and best_score - scored[1][0] < margin else None
    return Match(best, known[best], best_score, rival)


def match_team(text: str, teams: Iterable[str]) -> str | None:
    """A team by name, tolerant of case and punctuation. None if ambiguous."""
    found = match_name(text, {team: index for index, team in enumerate(teams)})
    return None if found is None or found.rival else found.name
