"""Rehearse draft day on a draft that already happened.

A real auction's nominations, in their real order, fed into a live session
one at a time: each player goes on the block for a few seconds, and if we
have not bought him by then he goes to the team that really did, at what
they really paid. It is the redraft (`scripts/redraft.py`) with a person
bidding instead of the ceiling, and it exercises everything the screen does
on the day -- the block, the card, the ceilings landing, the money moving --
without waiting on an ESPN mock.

Two seasons rarely share a league. Teams are carried across by ESPN team id,
which is stable for a manager who stays; a team in the old draft with no
id in this room hands its picks to one of this room's teams the old draft
never had. Our own real picks are not ours in a rehearsal: they are
nominations like any other, and if we pass, the richest other team that
can afford one takes him.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DraftPick, LeagueSeason, Player, Team
from app.draft.feed import OnBlock
from app.draft.room import DraftError
from app.draft.session import DraftSession


@dataclass(frozen=True)
class Nomination:
    player_id: int
    name: str
    team_id: int
    price: int


def load_nominations(session: Session, season: int, room_teams: dict[int, str]) -> list[Nomination]:
    """A season's draft in pick order, with its teams carried into this room."""
    league_season = session.scalars(select(LeagueSeason).where(LeagueSeason.season == season)).one()
    rows = session.execute(
        select(DraftPick, Player, Team)
        .join(Player, Player.id == DraftPick.player_id)
        .join(Team, Team.id == DraftPick.team_id)
        .where(DraftPick.league_season_id == league_season.id)
        .order_by(DraftPick.round_num, DraftPick.round_pick)
    ).all()
    old_ids = sorted({int(team.espn_team_id) for _, _, team in rows})
    spare = [tid for tid in sorted(room_teams) if tid not in old_ids]
    carried: dict[int, int] = {}
    for old in old_ids:
        if old in room_teams:
            carried[old] = old
        elif spare:
            carried[old] = spare.pop(0)
    return [
        Nomination(
            player_id=int(player.espn_player_id),
            name=str(player.name),
            team_id=carried[int(team.espn_team_id)],
            price=int(pick.bid_amount or 1),
        )
        for pick, player, team in rows
        if int(team.espn_team_id) in carried
    ]


class Rehearsal(threading.Thread):
    """Puts each nomination on the block for `seconds`, then sells him."""

    def __init__(
        self, session: DraftSession, nominations: list[Nomination], *, seconds: float = 20.0
    ) -> None:
        super().__init__(name="draft-rehearsal", daemon=True)
        self.session = session
        self.nominations = nominations
        self.seconds = seconds
        self.paused = threading.Event()
        self.skip = threading.Event()
        self.stopping = threading.Event()

    def status(self, index: int, left: float | None, done: bool = False) -> None:
        self.session.set_feed_status(
            mode="rehearsal",
            rehearsal={
                "nomination": index + 1,
                "of": len(self.nominations),
                "seconds_left": None if left is None else max(0, round(left)),
                "seconds": self.seconds,
                "paused": self.paused.is_set(),
                "done": done,
            },
        )

    def run(self) -> None:
        session = self.session
        for index, nomination in enumerate(self.nominations):
            if self.stopping.is_set():
                return
            if nomination.player_id in session.state.taken:
                continue
            if session.state.complete:
                break
            session.set_block(
                OnBlock(nomination.name, None, None, None), player_id=nomination.player_id
            )
            self.skip.clear()
            left = self.seconds
            while left > 0 and not self.skip.is_set() and not self.stopping.is_set():
                if nomination.player_id in session.state.taken:
                    break
                self.status(index, left)
                time.sleep(0.25)
                if not self.paused.is_set():
                    left -= 0.25
            if nomination.player_id in session.state.taken:
                continue
            self._sell(nomination)
        session.set_block(None)
        self.status(len(self.nominations) - 1, None, done=True)

    def _sell(self, nomination: Nomination) -> None:
        state = self.session.state
        floor = state.minimum_bid
        buyers = [state.teams[nomination.team_id]] if nomination.team_id != state.me else []
        buyers += sorted(
            (t for t in state.teams.values() if t.team_id not in (state.me, nomination.team_id)),
            key=lambda t: -t.max_bid(floor),
        )
        for team in buyers:
            price = min(nomination.price, team.max_bid(floor))
            if team.open_slots <= 0 or price < floor:
                continue
            try:
                self.session.apply(nomination.player_id, team.team_id, price, source="rehearsal")
                return
            except DraftError:
                continue

    def control(self, action: str) -> dict[str, Any]:
        if action == "pause":
            self.paused.set()
        elif action == "resume":
            self.paused.clear()
        elif action == "skip":
            self.skip.set()
        else:
            raise ValueError(f"unknown rehearsal action {action!r}")
        return {"paused": self.paused.is_set()}
