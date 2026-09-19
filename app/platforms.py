"""The fantasy platforms a league can live on, and lookups by a platform's own ids.

Only ESPN exists today. Everything downstream of the ingest (scoring, pickups,
the draft) reads tables and never a platform, so a second platform is a new
ingest mapping into the same tables, not a rewrite. docs/platforms.md has the
whole plan; this is the contract.

What a new platform's ingest must write, per row, beside what ESPN writes:

* `leagues`: `platform` (its name here, in `PLATFORMS` and in the CHECK
  constraints) and `platform_league_id`, the platform's own league id as text.
  Unique per platform.
* `owners`: `platform` and `platform_owner_id`. An owner is a person on one
  platform: the same person on two platforms is two rows, joined to one user
  through `team_managers`, not by merging owners.
* `teams`: `platform_team_id`, unique within the league season. The platform
  is the league's and is not repeated.
* `transactions`: `platform_transaction_id`, unique within the league season.
* `players`: never a new row blindly. A player from another platform is
  matched to an existing `players` row first, by name, NBA team and position,
  and gets a `player_platform_ids` row for that platform. A name that matches
  no one or more than one is refused and reported, never guessed (the rule
  `app.draft.bbm.match_player` already applies to BBM's names). Only a player
  no platform has shown us before gets a new `players` row.

And, before any of that can happen, a migration that adds the platform to the
CHECK constraints and relaxes the `espn_*` columns to nullable: they are NOT
NULL today, and the CHECKs that hold each ESPN id equal to its platform id
already pass on a NULL.

For an ESPN row, every platform id is the ESPN id as text; the ingest writes
both, and a CHECK on each table holds them equal. Nothing in the API resolves
through here yet: ESPN ids stay the keys of every URL.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League, Player, PlayerPlatformId

ESPN = "espn"

#: Every platform a row may name. The database's CHECK constraints
#: (`ck_leagues_platform`, `ck_owners_platform`, `ck_player_platform_ids_platform`,
#: `ck_league_connections_platform`) list the same names.
PLATFORMS: tuple[str, ...] = (ESPN,)


def require_platform(platform: str) -> str:
    """The platform, or ValueError when it is not one we know."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}; known: {', '.join(PLATFORMS)}")
    return platform


def league_by_platform_id(
    session: Session, platform: str, platform_league_id: str | int
) -> League | None:
    """The league a platform knows by this id, or None."""
    return session.scalar(
        select(League).where(
            League.platform == require_platform(platform),
            League.platform_league_id == str(platform_league_id),
        )
    )


def player_by_platform_id(
    session: Session, platform: str, platform_player_id: str | int
) -> Player | None:
    """The canonical player a platform knows by this id, or None."""
    return session.scalar(
        select(Player)
        .join(PlayerPlatformId, PlayerPlatformId.player_id == Player.id)
        .where(
            PlayerPlatformId.platform == require_platform(platform),
            PlayerPlatformId.platform_player_id == str(platform_player_id),
        )
    )


def espn_player_id_row(espn_player_id: int) -> PlayerPlatformId:
    """The mapping row for a player's ESPN id, to attach to his `platform_ids`."""
    return PlayerPlatformId(platform=ESPN, platform_player_id=str(espn_player_id))
