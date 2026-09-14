#!/usr/bin/env python3
"""Score every price guess against what this league actually paid.

Usage:
    python scripts/price_scorecard.py [--bbm-2026 data/bbm/BBM_Projections_2026.xls]

For every drafted player in every season: ESPN's average auction price
across its leagues (`ownership.auctionValueAverage` on the player card,
kept for 2019-2025 and zeroed for 2026), ESPN's own dollar value
(`draftRanksByRankType.STANDARD.auctionValue`, 2023 onward, which for 2026
looks refreshed after the season -- Knueppel $23, Castle $24, both drafted
at $1 -- so read it with care), our board, and BBM's dollars where a file
is given. Each is scored raw and fitted on the other seasons, and the
ESPN-average/board blends are scored the same way. ESPN responses are
cached under logs/price-cache/.

Measured 2026-09-14 (app/draft/live.py carries the result): ESPN's average
alone misses $6.14 a player and runs $4.40 low; the board alone misses
$6.71; half and half, fitted, misses $5.42 unbiased. The tier curve the
board uses was fitted on 2019-2025, so the board's numbers for those
seasons are partly in-sample; 2026 is not, and there the board missed $6.10.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

import requests
from sqlalchemy import select

from app.config import get_settings
from app.db.models import DraftPick, LeagueSeason, Player
from app.db.session import make_engine, make_session_factory
from app.draft import pool
from app.draft.bbm import load_bbm
from app.draft.market import price_board
from app.draft.projections import projection_problem
from app.draft.tiers import LEAGUE_TIER_CURVE, apply_tier_curve
from app.draft.valuation import value_players
from app.espn import get_espn_settings

LEAGUE = 3853870
CACHE = Path("logs/price-cache")


@dataclass
class Row:
    season: int
    name: str
    paid: int
    aav: float | None
    espn_value: float | None
    board: float | None
    bbm: float | None


def espn_values(season: int) -> dict[str, dict[str, Any]]:
    """Every player card's ESPN average price and ESPN value for a season."""
    cache = CACHE / f"{season}.json"
    if cache.exists():
        return dict(json.loads(cache.read_text()))
    espn = get_espn_settings()
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
        f"/segments/0/leagues/{LEAGUE}"
    )
    out: dict[str, dict[str, Any]] = {}
    for offset in range(0, 3000, 250):
        wanted = {
            "players": {
                "limit": 250,
                "offset": offset,
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            }
        }
        response = requests.get(
            url,
            params={"view": "kona_player_info"},
            headers={"X-Fantasy-Filter": json.dumps(wanted)},
            cookies={"swid": espn.espn_swid, "espn_s2": espn.espn_s2},
            timeout=60,
        )
        response.raise_for_status()
        players = response.json().get("players", [])
        for entry in players:
            card = entry["player"]
            ownership = card.get("ownership") or {}
            standard = (card.get("draftRanksByRankType") or {}).get("STANDARD") or {}
            out[str(card["id"])] = {
                "aav": ownership.get("auctionValueAverage") or None,
                "espn_value": standard.get("auctionValue"),
            }
        if len(players) < 250:
            break
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def load_rows(bbm_2026: Path | None) -> list[Row]:
    rows: list[Row] = []
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        for ls in session.scalars(select(LeagueSeason).order_by(LeagueSeason.season)):
            picks = session.execute(
                select(Player.espn_player_id, Player.name, DraftPick.bid_amount)
                .join(Player, Player.id == DraftPick.player_id)
                .where(DraftPick.league_season_id == ls.id, DraftPick.bid_amount.is_not(None))
            ).all()
            if not picks:
                continue
            season = int(ls.season)
            espn = espn_values(season)
            board: dict[int, int | None] = {}
            if not projection_problem(season):
                projections = pool.load_projections(session, season)
                priced = apply_tier_curve(
                    price_board(
                        value_players(projections, pool.season_categories(session, ls)),
                        teams=ls.team_count,
                        budget_per_team=ls.auction_budget or 200,
                        roster_slots=pool.roster_size_for(ls),
                    ),
                    LEAGUE_TIER_CURVE,
                )
                board = {p.player_id: priced.price_of(p.player_id) for p in projections}
            bbm: dict[int, float | None] = {}
            if season == 2026 and bbm_2026 is not None:
                loaded = load_bbm(session, bbm_2026, 2026)
                bbm = {pid: row.dollars for pid, row in loaded.rows.items() if pid > 0}
            for espn_id, name, paid in picks:
                pid = int(espn_id)
                card = espn.get(str(pid), {})
                rows.append(
                    Row(
                        season=season,
                        name=str(name),
                        paid=int(paid),
                        aav=_floor(card.get("aav")),
                        espn_value=_floor(card.get("espn_value")),
                        board=_floor(board.get(pid)),
                        bbm=_floor(bbm.get(pid)),
                    )
                )
    return rows


def _floor(value: float | None) -> float | None:
    return None if value is None else max(1.0, float(value))


def fit(pairs: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Least squares: paid = a + b * guess."""
    mean_x = fmean(x for x, _ in pairs)
    mean_y = fmean(y for _, y in pairs)
    sxx = sum((x - mean_x) ** 2 for x, _ in pairs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    slope = sxy / sxx if sxx else 1.0
    return mean_y - slope * mean_x, slope


def score(label: str, pairs: Sequence[tuple[float, float]]) -> None:
    if not pairs:
        return
    errors = [guess - paid for guess, paid in pairs]
    close = sum(abs(e) <= 5 for e in errors) / len(errors)
    print(
        f"    {label:36s} n={len(pairs):>4}  miss ${fmean(abs(e) for e in errors):5.2f}"
        f"  bias {fmean(errors):+5.2f}  within $5 {close:.0%}"
    )


def held_out(
    rows: Sequence[Row], guess: Callable[[Row], float | None]
) -> list[tuple[float, float]]:
    """Each season's guesses mapped by a fit on every other season."""
    out: list[tuple[float, float]] = []
    for season in sorted({r.season for r in rows}):
        train = [(g, float(r.paid)) for r in rows if r.season != season and (g := guess(r))]
        if len(train) < 50:
            continue
        a, b = fit(train)
        out += [(a + b * g, float(r.paid)) for r in rows if r.season == season and (g := guess(r))]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bbm-2026", type=Path, help="BBM's 2026 export, to score its dollars too")
    args = ap.parse_args()
    rows = load_rows(args.bbm_2026)

    guesses: dict[str, Callable[[Row], float | None]] = {
        "ESPN average price": lambda r: r.aav,
        "ESPN value": lambda r: r.espn_value,
        "our board": lambda r: r.board,
        "BBM dollars": lambda r: r.bbm,
    }
    print("BY SEASON, raw")
    for season in sorted({r.season for r in rows}):
        group = [r for r in rows if r.season == season]
        print(f"  {season} ({len(group)} drafted)")
        for label, guess in guesses.items():
            score(label, [(g, float(r.paid)) for r in group if (g := guess(r))])

    both = [r for r in rows if r.aav is not None and r.board is not None]
    print(f"\nBLENDS of ESPN average price and our board, {len(both)} players, held out by season")
    for weight in (0.0, 0.3, 0.5, 0.6, 0.7, 1.0):

        def blend(r: Row, w: float = weight) -> float:
            return w * (r.aav or 0.0) + (1 - w) * (r.board or 0.0)

        score(
            f"{weight:.0%} ESPN / {1 - weight:.0%} board, raw", [(blend(r), r.paid) for r in both]
        )
        score(f"{weight:.0%} ESPN / {1 - weight:.0%} board, fitted", held_out(both, blend))
    a, b = fit([(0.5 * (r.aav or 0.0) + 0.5 * (r.board or 0.0), float(r.paid)) for r in both])
    print(f"  half and half, fitted on every season: paid = {a:.2f} + {b:.3f} x blend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
