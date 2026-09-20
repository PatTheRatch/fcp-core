"""The address of ESPN's draft room, built from what the league already knows.

The room lives at one URL of a fixed shape:

    https://fantasy.espn.com/basketball/draft?leagueId=L&seasonId=S&teamId=T&memberId={SWID}

Every part of it is known for the real league -- the league id and the SWID
from the settings the ingest reads, the team from `FCP_TRACKED_TEAM_ID`, the
season from the room -- so on the night nobody should have to find it and
paste it. A mock draft is a different league id, and is pasted.
"""

from __future__ import annotations

from app.memberships import normalise_swid, swid_cookie

DRAFT_ROOM = "https://fantasy.espn.com/basketball/draft"


def draft_room_url(
    league_id: int | None, season: int | None, team_id: int | None, swid: str | None
) -> str | None:
    """The room's URL, or None when any part is missing.

    Never a partial one: a URL without its team or member opens ESPN's lobby,
    which the reader reports as a room that never renders, and that is a
    harder fault to see than "no URL". The SWID goes in its braces, which is
    how ESPN writes it and how the room's own address bar shows it, whether
    `.env` kept the braces or not.
    """
    if league_id is None or season is None or team_id is None or not swid:
        return None
    member = normalise_swid(swid)
    if member is None:
        return None
    return (
        f"{DRAFT_ROOM}?leagueId={int(league_id)}&seasonId={int(season)}"
        f"&teamId={int(team_id)}&memberId={swid_cookie(member)}"
    )
