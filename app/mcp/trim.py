"""The route's JSON, cut to what a model needs, and nothing cut that matters.

WHAT IS TRIMMED, AND THE RULE

The rule is one line: **keep every number and every reason; drop what only a
page needs.** A week report drawn as HTML carries a thousand free agents'
worth of layout -- each man's ESPN id twice over, his waiver dates, the
empty-day filler lists, every one of the several hundred moves the search
ranked. A model reading the same answer needs the moves that were worth a
look, the ones just under the bar, and the numbers behind them.

So, precisely:

* **Lists are cut, never summarised away.** `moves` keeps the best few
  beyond the plan, `drops` the best few, a wire the best few; each cut list
  says how long it really was (`of`), so the model can say "of 412 free
  agents" rather than "of 12".
* **A player becomes five or six fields**: name, ESPN id, position, NBA
  team, his status, his games left. His waiver dates ride along only when
  he is actually on waivers, because that is the only time they change what
  a manager can do.
* **Floats are rounded to three places.** Categories a week is a quantity
  measured on a few hundred decision points; its fourth decimal is noise
  with a token price. Probabilities likewise.
* **Nothing about the fit is dropped.** Every category view a trade
  produces survives whole: it is the half docs/trades.md section 7 says the
  calibration has nothing against, and it is the half a nine-category
  manager cannot get anywhere else.

Nothing here computes. Every number in an answer was computed by the engine
the route calls and is passed through, to three decimals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.inseason.card import pro_team_name

#: Decimals kept on every float. Three, for the reason in the docstring.
PLACES = 3


def n(value: Any) -> Any:
    """A number as the answer carries it: floats to `PLACES`, else unchanged."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return round(value, PLACES)
    return value


def nine(counts: Mapping[str, Any] | None) -> dict[str, Any]:
    """A category line, rounded. The nine in the league's own order."""
    return {key: n(value) for key, value in (counts or {}).items()}


def cut(rows: Sequence[Any], limit: int) -> tuple[list[Any], int]:
    """The first `limit` of a list, and how long the list really was."""
    return list(rows[:limit]), len(rows)


def player(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One man, as a sentence about him would need him."""
    if row is None:
        return None
    out: dict[str, Any] = {
        "espn_player_id": row.get("espn_player_id"),
        "name": row.get("name"),
        "position": row.get("position"),
        "pro_team": pro_team_name(int(row["pro_team_id"])) if row.get("pro_team_id") else None,
        "games_left": row.get("games_remaining", row.get("games_left")),
    }
    status = row.get("injury_status")
    if status and status.upper() not in ("ACTIVE", "NORMAL"):
        out["injury_status"] = status
        if row.get("expected_return_date"):
            out["expected_return_date"] = row["expected_return_date"]
    if row.get("on_ir"):
        out["on_ir"] = True
    if row.get("waiver_clears_on"):
        # Only when he is actually on waivers: it is the one fact that
        # changes what a manager can do about him today.
        out["on_waivers_until"] = row.get("waiver_clears_at")
        out["playable_from_day"] = row.get("waiver_clears_on")
    return out


def judgement(row: Mapping[str, Any]) -> dict[str, Any]:
    """A move in the one currency, over both horizons (`app.pickups.judge`)."""
    return {
        "delta_week": n(row["delta_week"]),
        "delta_season_per_week": n(row["delta_season_per_week"]),
        "weeks_remaining": n(row["weeks_remaining"]),
        "delta_total": n(row["delta_total"]),
        "per_week": n(row["per_week"]),
        "replacement": n(row["replacement"]),
        "record_without": [n(value) for value in row["record_without"]],
        "record_with": [n(value) for value in row["record_with"]],
        "measured": row["measured"],
    }


def shifts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The categories a move changed, and by how much of a chance."""
    return [
        {
            "category": row["abbreviation"],
            "chance_before": n(row["before"]),
            "chance_after": n(row["after"]),
            "chance_delta": n(row["delta"]),
        }
        for row in rows
    ]


def bid(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What to pay, and the sample the figure was read off."""
    if row is None:
        return None
    return {
        "amount": row["amount"],
        "basis": row["basis"],
        "bucket": row["bucket"],
        "range": [row["low"], row["high"]],
        "sample": row["sample"],
        "note": row["note"],
    }


def stream_move(row: Mapping[str, Any]) -> dict[str, Any]:
    """One move on the week report, with the reason it ranks where it does."""
    return {
        "kind": row["kind"],
        "add": player(row["add"]),
        "drop": player(row["drop"]),
        "to_ir": player(row["to_ir"]),
        "delta_this_week": n(row["delta"]),
        "net": n(row["net"]),
        "clears_hurdle": row["clears_hurdle"],
        "fills_empty_day": row["fills_empty_day"],
        "add_starts": row["add_starts"],
        "drop_starts": row["drop_starts"],
        "moved": shifts(row["moved"]),
        "bid": bid(row["bid"]),
        "judgement": judgement(row["judgement"]),
    }


def stream_move_brief(row: Mapping[str, Any]) -> dict[str, Any]:
    """A move that did not make the plan: enough to ask about, no more.

    The plan's own moves keep everything (`stream_move`). These are the ones
    a manager asks "and what was just under the bar?" about, and the answer
    to that is a name, a number and whether it cleared -- not a second copy
    of the judgement's eight fields. `week_report` costs about a third less
    for it.
    """
    return {
        "kind": row["kind"],
        "add": player(row["add"]),
        "drop": player(row["drop"]),
        "to_ir": player(row["to_ir"]),
        "delta_this_week": n(row["delta"]),
        "net": n(row["net"]),
        "clears_hurdle": row["clears_hurdle"],
        "fills_empty_day": row["fills_empty_day"],
    }


def schedule(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The week's games day by day, one line a day and no names.

    Three numbers a side is the whole of it: the games men who are not ruled
    out have that day, how many of them the lineup seats, and the starting
    places nobody can fill. The men behind each number are on the report the
    page draws; a model asking "how many games do I have left" wants the
    count, and the names are what `week_lineup` is for.
    """
    if not row:
        return None

    def side(each: Mapping[str, Any] | None) -> list[int] | None:
        if each is None:
            return None
        return [each["games"], each["seated"], each["open_places"]]

    return {
        "reads": "[games, seated, open_places] a side; seated is what will count",
        "days": [
            {
                "scoring_period": day["scoring_period"],
                "mine": side(day["mine"]),
                "theirs": side(day.get("theirs")),
            }
            for day in row.get("days", [])
        ],
        "mine_total": side(row.get("mine_total")),
        "theirs_total": side(row.get("theirs_total")),
    }


def season_swap(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One rest-of-season move: who goes, who comes, and what it buys a week."""
    if row is None:
        return None
    return {
        "kind": row["kind"],
        "out": [player(each) for each in row["out"]],
        "into": [player(each) for each in row["into"]],
        "delta_per_week": n(row["delta"]),
        "net": n(row["net"]),
        "costs_faab": row["costs_faab"],
        "hurdle": n(row["hurdle"]),
        "clears_hurdle": row["clears_hurdle"],
        "moved": shifts(row["moved"]),
        "bid": bid(row["bid"]),
        "judgement": judgement(row["judgement"]),
    }


def seat(row: Mapping[str, Any]) -> dict[str, Any]:
    """One place in a lineup and the man in it, with his game or the lack of one."""
    man = row.get("player")
    if man is None:
        return {"slot": row["slot"], "player": None}
    return {
        "slot": row["slot"],
        "player": player(man),
        "game": (man.get("game") or {}).get("describe"),
        "status": man.get("status"),
        "plays": man.get("plays"),
    }


def trade_card(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A man in a deal, and what the judgement about him rests on."""
    if row is None:
        return None
    out: dict[str, Any] = {
        "espn_player_id": row["espn_player_id"],
        "name": row["name"],
        "worth_a_week": n(row["value"]),
        "games_left": row["games_left"],
        "playoff_games": row["playoff_games"],
        "games_so_far": row["games_so_far"],
    }
    if row.get("thin"):
        out["thin"] = True
    if row.get("hurt"):
        out["hurt"] = True
        out["injury_status"] = row.get("injury_status")
        if row.get("expected_return_date"):
            out["expected_return_date"] = row["expected_return_date"]
    if not row.get("had_projection", True):
        out["had_projection"] = False
    return out


def categories(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The nine before and after, in counts and in the chance of winning each.

    Kept whole, every time. This is the half of a trade answer the record
    says nothing against (docs/trades.md section 0).
    """
    return [
        {
            "category": row["abbreviation"],
            "before": n(row["before"]),
            "after": n(row["after"]),
            "delta": n(row["delta"]),
            "chance_before": n(row["p_before"]),
            "chance_after": n(row["p_after"]),
            "chance_delta": n(row["p_delta"]),
            "moved": row["moved"],
        }
        for row in rows
    ]


def finish(row: Mapping[str, Any] | None, *, weeks: bool = False) -> dict[str, Any] | None:
    """Where a change leaves a team, with the noise and the record on it.

    The two numbers a manager reads -- the projected record and the playoff
    odds, before and after -- and the three things that stop them being read
    as more than they are: the simulation's own sampling band, whether the
    change cleared it, and the published record of the forecast. `language`
    says the finish is a second lens and not a second bar, which is the one
    thing a model must not get wrong about it.

    `weeks` keeps the week-by-week list. Off by default: a dozen rows of "and
    in week fourteen he is worth four hundredths" is not a sentence anybody
    says, and a deal has two sides of them.
    """
    if row is None:
        return None
    out: dict[str, Any] = {
        "espn_team_id": row["espn_team_id"],
        "team_name": row["team_name"],
        "projected_categories_before": [n(value) for value in row["record_before"]],
        "projected_categories_after": [n(value) for value in row["record_after"]],
        "place_before": row["place_before"],
        "place_after": row["place_after"],
        "playoff_odds_before": n(row["playoff_odds_before"]),
        "playoff_odds_after": n(row["playoff_odds_after"]),
        "bye_odds_before": n(row["bye_odds_before"]),
        "bye_odds_after": n(row["bye_odds_after"]),
        "simulations": row["n_sims"],
        "odds_band": n(row["odds_band"]),
        "moved_more_than_the_band": row["readable"],
        "noise": row["noise_note"],
        "projection_record": row["calibration_note"],
        "language": row["language"],
    }
    if weeks:
        out["weeks_ahead"] = [
            {
                "period": week["period"],
                "opponent_espn_team_id": week["opponent_espn_team_id"],
                "opponent": week["opponent_name"],
                "expected_categories_before": n(week["expected_before"]),
                "expected_categories_after": n(week["expected_after"]),
            }
            for week in row["weeks"]
        ]
    return out
