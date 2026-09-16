#!/usr/bin/env python3
"""Print a team's season graded in categories a week.

Usage:
    python scripts/scorecard.py --season 2026 --team "Through The Wire"
    python scripts/scorecard.py --season 2026 --team "Brighton Bears" --only trades,wire
    python scripts/scorecard.py --season 2026 --team "The Infirmary" --json

The same numbers the API serves at
/leagues/{id}/seasons/{season}/teams/{team}/scorecard, from
`app.scoring.scorecard`. Every figure is categories a week: what a line added
to a team's expected category wins (docs/scoring/SPEC.md). Verdicts read
decision first, then result.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.models import LeagueSeason, Team
from app.db.session import make_engine, make_session_factory
from app.scoring.moves import MoveGrade
from app.scoring.scorecard import Scorecard, scorecard

SECTIONS = ("record", "players", "draft", "trades", "wire")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--team", required=True, help="team name, exactly as ESPN holds it")
    ap.add_argument("--only", default="", help=f"comma-separated sections: {', '.join(SECTIONS)}")
    ap.add_argument("--json", action="store_true", help="the whole scorecard as JSON")
    args = ap.parse_args()

    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as session:
        team = session.scalar(
            select(Team)
            .join(LeagueSeason, LeagueSeason.id == Team.league_season_id)
            .where(LeagueSeason.season == args.season, Team.name == args.team)
        )
        if team is None:
            raise SystemExit(f"no team {args.team!r} in {args.season}")
        card = scorecard(session, args.season, team.id)

    if args.json:
        print(json.dumps(_plain(card), indent=1))
        return 0
    wanted = set(args.only.split(",")) if args.only else set(SECTIONS)
    print(render(card, wanted))
    return 0


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _move(grade: MoveGrade | None) -> str:
    if grade is None:
        return "not graded"
    weeks = len(grade.periods)
    return f"{grade.verdict.text} ({weeks} week{'s' if weeks != 1 else ''})"


def render(card: Scorecard, wanted: set[str]) -> str:
    lines = [f"{card.team_name}, {card.season}", ""]
    if "record" in wanted:
        record = "  ".join(f"{c} {rate:.0%}" for c, rate in card.category_record.items())
        lines += ["CATEGORY RECORD (share of regular-season matchups won)", f"  {record}"]
        if card.looks_like_punts:
            lines.append(f"  looks like a punt: {', '.join(card.looks_like_punts)}")
        lines += [f"  a typical pickup added {card.replacement:.2f} categories a week", ""]
    if "players" in wanted:
        lines.append("PLAYERS (categories a week held: team fit, league standard; weeks)")
        for p in card.players:
            r = p.regular
            if not r.weeks_held:
                continue
            playoff = (
                f"  playoffs {p.playoffs.team_fit:+.2f} over {p.playoffs.weeks_started}"
                if p.playoffs.weeks_started
                else ""
            )
            lines.append(
                f"  {p.name:26s} {r.team_fit_per_week:+.2f}  {r.league_standard_per_week:+.2f}"
                f"  started {r.weeks_started}/{r.weeks_held}{playoff}"
            )
        lines.append("")
    if "draft" in wanted:
        lines.append("DRAFT")
        for g in card.draft:
            board = f"board ${g.projected_value}" if g.projected_value is not None else "no board"
            market = f"market ${g.market} ({g.market_source})" if g.market is not None else ""
            judged = g.verdict.text if g.verdict else f"result {g.result:+.2f} a week"
            lines.append(f"  ${g.price:<3d} {g.name:24s} {board}, {market}; {g.outcome}. {judged}")
        lines.append("")
    if "trades" in wanted:
        lines.append("TRADES")
        for t in card.trades:
            trade = t.trade
            head = (
                f"  day {trade.day}: {', '.join(p.name for p in trade.players_in) or '?'}"
                f" for {', '.join(p.name for p in trade.players_out) or '?'}"
                f" with {', '.join(trade.counterparty_names)}"
            )
            if t.part_missing:
                lines.append(f"{head}. Part missing: not graded.")
                continue
            lines.append(f"{head}.")
            lines.append(f"    regular season: {_move(t.regular)}")
            if t.playoffs:
                lines.append(f"    playoffs: {_move(t.playoffs)}")
        lines.append("")
    if "wire" in wanted:
        lines.append("WIRE")
        for m in card.wire:
            what = f"+{', '.join(m.added)}" if m.added else ""
            if m.dropped:
                what += f" -{', '.join(m.dropped)}"
            lines.append(f"  day {m.day} {what.strip()}: {_move(m.regular)}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
