"""Placing a name from outside on one of our players.

Every file that arrives without ESPN ids carries a name and nothing else:
Basketball Monster's export (`app.draft.bbm`), a manager's projection upload
(`app.projections.upload`), and now the NBA's official injury reports
(`app.injury_reports`). All three have to turn a name into a player id, and
all three should do it by the same rule, because a wrong match is worse than
no match at all: loading a projection file through the draft feed's loose
typing matcher once put Caleb Wilson's line on Jalen Wilson and Bronny
James's next to LeBron's.

The rule, in order:

* the same name once case, accents, joining punctuation and suffixes are
  gone (`O.G. Anunoby` is `OG Anunoby`, `Ronald Holland II` is `Ron
  Holland`'s full name). Among several players with that name the one with
  the most recent season line wins, and a tie is refused;
* failing that, the same surname with the rest of the name equal and one
  first name the start of the other (`Cam`/`Cameron`, `Herb`/`Herbert`),
  if exactly one player fits;
* otherwise nothing, and the caller keeps the raw name so the miss stays
  visible.

`known` and `recency` are keyed on whatever id the caller works in -- the
draft room passes ESPN player ids, the injury backfill passes our own
`players.id` -- so the matcher never needs to know which.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerSeasonStat

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
#: Punctuation that joins rather than separates, dropped before normalising
#: so `O.G.` becomes `OG` rather than `o g`.
_JOINING = re.compile(r"[.'\u2019]")


def normalise(name: str) -> str:
    """Lowercase ASCII with punctuation dropped, so `Jokić`, `Jokic` and
    `jokic.` are one name. ESPN's own names are already ASCII."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return " ".join(text.split())


def name_key(name: str) -> str:
    """A name with case, accents, joining punctuation and suffixes gone."""
    joined = _JOINING.sub("", name)
    return " ".join(_SUFFIX.sub(" ", normalise(joined)).split())


def from_last_first(name: str) -> str:
    """`Pippen Jr., Scotty` -> `Scotty Pippen Jr.`, the NBA's order undone.

    The first comma separates the surname (with any suffix still attached to
    it) from the given name; `name_key` drops the suffix afterwards. A name
    with no comma is already in reading order and comes back untouched.
    """
    surname, comma, given = name.partition(",")
    if not comma:
        return name.strip()
    return f"{given.strip()} {surname.strip()}".strip()


def synthetic_id(name: str) -> int:
    """A stable negative id for a player we hold no ESPN id for."""
    return -(zlib.crc32(name_key(name).encode()) % 1_000_000_000 + 1)


def match_player(name: str, known: Mapping[int, str], recency: Mapping[int, int]) -> int | None:
    """Our id for an outside name, or None when there is no safe match.

    Exact on `name_key` first; among several exact matches the one with the
    most recent season line wins, and a tie is no match. Failing that, the
    same surname and the rest of the name equal, with one first name the
    start of the other, if exactly one player fits.
    """
    wanted = name_key(name)
    if not wanted:
        return None
    exact = [pid for pid, known_name in known.items() if name_key(known_name) == wanted]
    if exact:
        exact.sort(key=lambda pid: -recency.get(pid, 0))
        if len(exact) > 1 and recency.get(exact[0], 0) == recency.get(exact[1], 0):
            return None
        return exact[0]
    first, _, rest = wanted.partition(" ")
    if not rest or len(first) < 2:
        return None
    loose = []
    for pid, known_name in known.items():
        other_first, _, other_rest = name_key(known_name).partition(" ")
        if other_rest == rest and (other_first.startswith(first) or first.startswith(other_first)):
            loose.append(pid)
    return loose[0] if len(loose) == 1 else None


@dataclass(frozen=True)
class NameIndex:
    """Our players by `players.id`, and how recently we hold a line for each.

    Lighter than `app.draft.bbm.espn_lookup`, which also carries eligibility
    and positions the draft board needs; a name-only caller reads two columns
    and an aggregate rather than every season line in the database.
    """

    #: `players.id` -> the name we hold for him.
    known: dict[int, str]
    #: `players.id` -> the most recent season we hold a line for.
    recency: dict[int, int]

    def match(self, name: str) -> int | None:
        return match_player(name, self.known, self.recency)


def name_index(session: Session) -> NameIndex:
    """Every player's name and the newest season he has a line in."""
    known = {
        int(player_id): str(name)
        for player_id, name in session.execute(select(Player.id, Player.name)).all()
    }
    recency = {
        int(player_id): int(season)
        for player_id, season in session.execute(
            select(PlayerSeasonStat.player_id, func.max(PlayerSeasonStat.season)).group_by(
                PlayerSeasonStat.player_id
            )
        ).all()
    }
    return NameIndex(known=known, recency=recency)
