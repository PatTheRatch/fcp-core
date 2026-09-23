"""The tools: one function each, every one a thin call into a route's own code.

Each of these calls the function the matching HTTP route calls -- often the
route function itself -- and returns its JSON, trimmed by `app.mcp.trim` and
carrying the `provenance` block from `app.mcp.provenance`. Nothing here
computes a number. If a figure is in an answer, the engine behind the page
produced it, and the tool passed it through.

A refusal is a `RefusedError` carrying one sentence: the site's own for a scope
("This team's plan is its manager's."), and the route's own 422 or 409 text
for a deal or a season that cannot be read. Never an exception's words.

THE SHAPE OF AN ANSWER

Every tool returns a mapping with, at least:

    provenance   where each number came from (app/mcp/provenance.py)

and then its own fields. Lists that were cut say how long they were, so a
model can say "of 412 free agents" and be right.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import calibration, reports
from app.api import changes as changes_api
from app.api import leagues as leagues_api
from app.api import pickups as pickups_api
from app.api import players as players_api
from app.api import projected as projected_api
from app.api import trades as trades_api
from app.api import transactions as transactions_api
from app.api.access import Viewer
from app.db.models import League, LeagueSeason, Player, Team
from app.draft.targets import category_distributions
from app.mcp import trim
from app.mcp.provenance import block
from app.mcp.scope import (
    RefusedError,
    league_member,
    member_leagues,
    newest_season,
    team_of_league,
    team_plan,
    the_one_league,
)
from app.pickups.judge import load_spots, standard_lens, weekly_lines
from app.pickups.state import (
    ADDS_PER_PERIOD_DAY,
    has_free_agent_snapshots,
    load_free_agents,
    load_team_week,
    season_calendar,
)

#: The bars each answer leans on. A week report reads the streaming bar and
#: the two measurements of the wire; a season report the two season bars; a
#: trade the paid bar, the wire and its own record.
WEEK_KEYS = (calibration.STREAM_HURDLE, calibration.TYPICAL_PICKUP, calibration.OPENED_PLACE)
SEASON_KEYS = (
    calibration.SEASON_HURDLE_PAID,
    calibration.SEASON_HURDLE_FREE,
    calibration.TYPICAL_PICKUP,
    calibration.OPENED_PLACE,
)
TRADE_KEYS = (
    calibration.TRADE_RECORD,
    calibration.SEASON_HURDLE_PAID,
    calibration.TYPICAL_PICKUP,
    calibration.OPENED_PLACE,
)
WIRE_KEYS = (calibration.TYPICAL_PICKUP, calibration.OPENED_PLACE)

#: How much of each list survives the trim. Enough to answer "and what was
#: just under the bar?", which is the question the bar exists to invite.
MOVES = 8
DROPS = 5
FILLERS = 3
WIRE = 15
CHANGES = 60
TRANSACTIONS = 60

#: The longest window `recent_moves` will look back over, in days.
MAX_DAYS = 90

#: What the language rules mean in a payload, repeated on the answers where
#: a model is most likely to reach for a verdict.
LABEL_ONLY = (
    "`clears_hurdle` is a label, not advice: a move under the bar is still "
    "here, with its number. A move that fills an empty day clears on any "
    "positive net, so it can carry the label with a net under the bar; the "
    "net is the number to read. The manager decides."
)
OTHER_SIDE = (
    "the other side's numbers are our estimate of his roster's needs, made "
    "with our own projections. They are never his opinion."
)


def _passed_through(error: HTTPException) -> RefusedError:
    """A route's own sentence, as the one line a tool refuses with."""
    detail = error.detail
    return RefusedError(detail if isinstance(detail, str) else "that cannot be read")


# ---------------------------------------------------------------------------
# what this token may see
# ---------------------------------------------------------------------------


def my_leagues(session: Session, viewer: Viewer) -> dict[str, Any]:
    """Every league and team this token may read, with its role in each."""
    mine = member_leagues(session, viewer)
    out: list[dict[str, Any]] = []
    for league_id in mine:
        rows = session.scalars(
            select(LeagueSeason)
            .join(League, League.id == LeagueSeason.league_id)
            .where(League.espn_league_id == league_id)
            .order_by(LeagueSeason.season.desc())
        ).all()
        if not rows:
            out.append({"espn_league_id": league_id, "name": None, "seasons": [], "teams": []})
            continue
        newest = rows[0]
        teams = [
            {
                "espn_team_id": int(team.espn_team_id),
                "name": str(team.name),
                "i_manage_it": viewer.all_access
                or _manages(session, viewer, league_id, int(newest.season), int(team.espn_team_id)),
            }
            for team in session.scalars(
                select(Team).where(Team.league_season_id == newest.id).order_by(Team.espn_team_id)
            ).all()
        ]
        out.append(
            {
                "espn_league_id": league_id,
                "name": str(newest.name),
                "seasons": sorted(int(row.season) for row in rows),
                "newest_season": int(newest.season),
                "teams": teams,
            }
        )
    return {
        "as": viewer.email,
        "how": viewer.via,
        "leagues": out,
        "note": (
            "A team marked `i_manage_it` is one whose plan this token may read. "
            "Every other team here is league-visible: its roster, its record and "
            "its moves, never its plan."
        ),
    }


def _manages(session: Session, viewer: Viewer, league_id: int, season: int, team_id: int) -> bool:
    from app.api import access

    return access.is_team_manager(session, viewer, league_id, season, team_id)


def league_context(session: Session, viewer: Viewer, league_id: int, season: int) -> dict[str, Any]:
    """The rules, the calendar, today's day, and this league's own numbers.

    The first call of any conversation: nothing else says how many places a
    roster holds, how many adds a period allows, or whether the bar a move
    has to clear was measured on this league or borrowed from another.
    """
    found = league_member(session, viewer, league_id, season)
    settings = leagues_api.get_season(found).model_dump(mode="json")
    periods = [row.model_dump(mode="json") for row in leagues_api.list_periods(found, session)]
    calendar = season_calendar(session, season)
    today = calendar.scoring_period_on(pickups_api.TODAY()) if calendar is not None else None
    playoffs = [row["period"] for row in periods if row["is_playoff"]]
    places = sum(int(count) for count in (found.lineup_slots or {}).values())
    return {
        "league": {
            "espn_league_id": league_id,
            "season": season,
            "name": settings["name"],
            "team_count": settings["team_count"],
            "scoring_type": settings["scoring_type"],
        },
        "categories": [row["abbreviation"] for row in settings["categories"]],
        "roster": {
            "lineup_slots": dict(found.lineup_slots or {}),
            "starting_places": places,
            "bench_slots": int(found.bench_slots or 0),
            "injured_reserve_slots": int(found.injured_reserve_slots or 0),
            "position_limits": dict(found.position_limits or {}),
        },
        "acquisitions": {
            "uses_faab": bool(found.uses_faab),
            "faab_budget": int(found.acquisition_budget or 0),
            "adds_per_day_of_a_period": ADDS_PER_PERIOD_DAY,
            "note": (
                "a matchup period allows one add for each of its days, spent on any "
                "of them; a week is therefore a budget of seven"
            ),
        },
        "calendar": {
            "today": today,
            "today_date": calendar.date_of(today).isoformat()
            if calendar is not None and today is not None
            else None,
            "first_scoring_period": calendar.first_scoring_period if calendar else None,
            "last_scoring_period": calendar.last_scoring_period if calendar else None,
            "regular_season_periods": settings["regular_season_periods"],
            "total_matchup_periods": settings["total_matchup_periods"],
            "playoff_periods": playoffs,
            "playoff_team_count": settings["playoff_team_count"],
            "trade_deadline": settings["trade_deadline"],
            "periods": [
                {
                    "period": row["period"],
                    "days": [row["first_scoring_period"], row["final_scoring_period"]],
                    "playoff": row["is_playoff"],
                }
                for row in periods
            ],
        },
        "calibration": {
            key: trim_calibration(listed)
            for key, listed in _listed(session, int(found.league_id)).items()
        },
        "provenance": block(session, found, keys=calibration.KEYS, day=today),
    }


def _listed(session: Session, league_row_id: int) -> dict[str, calibration.Listed]:
    return {listed.key: listed for listed in calibration.listing(session, league_row_id)}


def trim_calibration(listed: calibration.Listed) -> dict[str, Any]:
    """One key as the account page shows it: what is used, and what else is known."""
    out: dict[str, Any] = {
        "title": listed.title,
        "value": listed.used.value,
        "source": listed.used.source,
        "n": listed.used.n,
        "unit": listed.unit,
        "minimum_sample": listed.minimum,
        "note": listed.used.note,
    }
    if listed.measured is not None:
        out["this_league_measured"] = {
            "value": listed.measured.value,
            "n": listed.measured.n,
            "used": listed.used.source == calibration.MEASURED,
        }
    return out


# ---------------------------------------------------------------------------
# the three reports
# ---------------------------------------------------------------------------


def _plan(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    kind: str,
    today: int | None,
) -> tuple[LeagueSeason, Team, dict[str, Any], bool]:
    found = league_member(session, viewer, league_id, season)
    team = team_plan(session, viewer, found, league_id, team_id)
    try:
        body, stored = pickups_api.report(session, found, team, kind, today)
    except HTTPException as error:
        raise _passed_through(error) from None
    return found, team, body, stored


def week_report(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    today: int | None = None,
) -> dict[str, Any]:
    """This week's plan: the matchup as projected, and the moves worth a look."""
    found, team, body, stored = _plan(
        session, viewer, league_id, season, team_id, reports.STREAM, today
    )
    moves, of_moves = trim.cut(body["moves"], MOVES)
    return {
        "team": {"espn_team_id": body["espn_team_id"], "name": str(team.name)},
        "matchup_period": body["matchup_period"],
        "days_left": body["scoring_periods_remaining"],
        "opponent_espn_team_id": body["opponent_espn_team_id"],
        "expected_categories_won": trim.n(body["expected_wins"]),
        "chance_by_category": trim.nine(body["probabilities"]),
        "projected": trim.nine(body["projected"]),
        "opponent_projected": trim.nine(body["opponent_projected"]),
        "worth_a_look": [trim.stream_move(move) for move in body["recommended"]],
        "also_ranked": {
            "of": of_moves,
            "moves": [trim.stream_move_brief(move) for move in moves],
            "note": (
                "the best of every move the search ranked, whether or not it clears "
                "the bar; the ones that do are in `worth_a_look`, in full. Ask for a "
                "trade or a pickup by name to see one of these judged category by "
                "category"
            ),
        },
        "empty_days": [
            {
                "scoring_period": day["scoring_period"],
                "empty_slots": day["empty_slots"],
                "who_could_fill_it": [trim.player(each) for each in day["fillers"][:FILLERS]],
            }
            for day in body["empty_days"]
        ],
        "season_outlook_with_no_move": trim.judgement(body["outlook"]),
        "hurdle": trim.n(body["hurdle"]),
        "roster_room": {
            "open_slots": body["open_slots"],
            "ir_slot_free": body["ir_slot_free"],
            "adds_used": body["adds_used"],
            "adds_budget": body["adds_budget"],
            "adds_left": body["adds_left"],
            "faab_remaining": body["faab_remaining"],
        },
        "wire": {"pool_size": body["pool_size"], "historical": body["historical_wire"]},
        "language": LABEL_ONLY,
        "provenance": block(
            session, found, keys=WEEK_KEYS, day=_day_of(body, today, session, found), stored=stored
        ),
    }


def season_report(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    today: int | None = None,
) -> dict[str, Any]:
    """The rest of the year: who to hold, who should go, and what to bid."""
    found, team, body, stored = _plan(
        session, viewer, league_id, season, team_id, reports.SEASON, today
    )
    drops, of_drops = trim.cut(body["drops"], DROPS)
    return {
        "team": {"espn_team_id": body["espn_team_id"], "name": str(team.name)},
        "today": body["today"],
        "weeks_remaining": trim.n(body["weeks_remaining"]),
        "expected_categories_won_a_week": trim.n(body["expected_wins"]),
        "chance_by_category": trim.nine(body["probabilities"]),
        "roster_weekly_line": trim.nine(body["weekly"]),
        "worth_a_look": trim.season_swap(body["recommended"]),
        "best_of_each_kind": {
            "free_add": trim.season_swap(body["best_add"]),
            "swap": trim.season_swap(body["best_swap"]),
            "two_for_two": trim.season_swap(body["best_two_swap"]),
        },
        "drop_candidates": {
            "of": of_drops,
            "players": [
                {
                    "player": trim.player(row["player"]),
                    "replacement": trim.player(row["replacement"]),
                    "costs_a_week": trim.n(row["delta"]),
                }
                for row in drops
            ],
            "note": "`costs_a_week` is what dropping him costs, against the wire's best",
        },
        "stash_candidates": [
            {
                "player": trim.player(row["player"]),
                "expected_return_date": row["expected_return_date"],
                "weeks_away": trim.n(row["weeks_away"]),
                "healthy_rank_on_the_wire": row["healthy_rank"],
                "needs_a_drop": row["needs_drop"],
            }
            for row in body["stashes"]
        ],
        "churn": body["churn"],
        "season_outlook_with_no_move": trim.judgement(body["outlook"]),
        "hurdles": {"paid": trim.n(body["hurdle_paid"]), "free": trim.n(body["hurdle_free"])},
        "roster_room": {
            "open_slots": body["open_slots"],
            "ir_slot_free": body["ir_slot_free"],
            "adds_used": body["adds_used"],
            "adds_budget": body["adds_budget"],
            "adds_left": body["adds_left"],
            "faab_remaining": body["faab_remaining"],
        },
        "wire": {"pool_size": body["pool_size"], "historical": body["historical_wire"]},
        "language": LABEL_ONLY,
        "provenance": block(
            session, found, keys=SEASON_KEYS, day=int(body["today"]), stored=stored
        ),
    }


def todays_lineup(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    today: int | None = None,
) -> dict[str, Any]:
    """Who starts today, against who the team is actually set to start."""
    found, team, body, stored = _plan(
        session, viewer, league_id, season, team_id, reports.TODAY, today
    )
    return {
        "team": {"espn_team_id": body["espn_team_id"], "name": str(team.name)},
        "today": body["today"],
        "date": body["calendar_date"],
        "matchup_period": body["matchup_period"],
        "nba_teams_playing": body["teams_playing"],
        "proposed_lineup": [trim.seat(place) for place in body["lineup"]],
        "places_filled": body["starts"],
        "your_lineup": {
            "known": body["actual_known"],
            "places_filled": body["actual_starts"],
            "seats": [trim.seat(place) for place in body["actual"]],
        },
        "places_producing_nothing": [
            {
                "seat": trim.seat(row["seat"]),
                "bench_men_who_fit_it": [trim.player(each) for each in row["instead"]],
            }
            for row in body["fix"]
        ],
        "benched_with_a_game": [
            {
                "player": trim.player(row["player"]),
                "reason": row["reason"],
                "behind": [trim.player(each) for each in row["behind"]],
            }
            for row in body["benched"]
        ],
        "cannot_start_today": [trim.player(each) for each in body["idle"]],
        "injured_reserve": [trim.player(each) for each in body["injured_reserve"]],
        "projected_today": trim.nine(body["projected"]),
        "your_lineup_projected": trim.nine(body["actual_projected"]),
        "edge": trim.n(body["edge"]),
        "provenance": block(session, found, day=int(body["today"]), stored=stored),
    }


def _day_of(
    body: dict[str, Any], today: int | None, session: Session, found: LeagueSeason
) -> int | None:
    """The scoring period a week report is about: its first day left."""
    if today is not None:
        return today
    days = body.get("scoring_periods_remaining") or []
    if days:
        return int(days[0])
    calendar = season_calendar(session, int(found.season))
    return calendar.scoring_period_on(pickups_api.TODAY()) if calendar is not None else None


# ---------------------------------------------------------------------------
# the league's own facts
# ---------------------------------------------------------------------------


def standings(session: Session, viewer: Viewer, league_id: int, season: int) -> dict[str, Any]:
    """Every team's record, in matchups and in categories."""
    found = league_member(session, viewer, league_id, season)
    rows = leagues_api.get_standings(found, session, include_playoffs=False)
    return {
        "league_id": league_id,
        "season": season,
        "teams": [
            {
                "espn_team_id": row.espn_team_id,
                "name": row.name,
                "matchups": [row.matchups_won, row.matchups_lost, row.matchups_tied],
                "categories": [row.categories_won, row.categories_lost, row.categories_tied],
                "final_standing": row.final_standing,
            }
            for row in rows
        ],
        "note": "byes are left out: an unopposed matchup is not a win",
        "provenance": block(session, found),
    }


def projected_standings(
    session: Session, viewer: Viewer, league_id: int, season: int, today: int | None = None
) -> dict[str, Any]:
    """Where every team is heading: the record it ends on, and the odds.

    Each team's remaining matchups played out head to head and summed onto
    what is banked (`app.inseason.projected`). The per-week detail every team
    carries is dropped here -- fourteen teams times a dozen weeks is a table
    nobody reads aloud -- and what is kept is the record, the finishing
    odds, and the note saying how well this method scored when the season it
    is projecting was replayed against it.
    """
    found = league_member(session, viewer, league_id, season)
    try:
        answer = projected_api.projected_standings(found, session, today=today)
    except HTTPException as error:
        raise _passed_through(error) from None
    body = answer.model_dump(mode="json")
    return {
        "league_id": body["league_id"],
        "season": body["season"],
        "as_of": {"scoring_period": body["as_of"], "date": body["as_of_date"]},
        "matchup_period": body["matchup_period"],
        "periods_left": body["periods"],
        "playoff_team_count": body["playoff_team_count"],
        "bye_count": body["bye_count"],
        "teams": [
            {
                "espn_team_id": team["espn_team_id"],
                "name": team["name"],
                "banked_matchups": team["banked_matchups"],
                "banked_categories": [
                    trim.n(team["banked_won"]),
                    trim.n(team["banked_lost"]),
                ],
                "projected_matchups": [trim.n(value) for value in team["projected_matchups"]],
                "projected_categories": [trim.n(value) for value in team["projected_record"]],
                "playoff_odds": trim.n(team["playoff_odds"]),
                "bye_odds": trim.n(team["bye_odds"]),
            }
            for team in body["teams"]
        ],
        "how_it_was_made": {
            "simulations": body["n_sims"],
            "tiebreak": body["tiebreak"],
            "basis": body["basis"],
            "playoffs_projected": body["playoffs_projected"],
            "playoff_note": body["playoff_note"],
        },
        "projection_record": body["calibration_note"],
        "language": (
            "read the odds as directions rather than quantities: "
            "`projection_record` says how well this method has scored"
        ),
        "provenance": block(
            session,
            found,
            day=int(body["as_of"]),
            extra={"projection_source_note": body["source_note"]},
        ),
    }


def matchup(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    period: int | None = None,
) -> dict[str, Any]:
    """One team's matchup in one period, category by category.

    League scope, like the standings: who plays whom, and what each side has
    posted, is every member's to read.
    """
    found = league_member(session, viewer, league_id, season)
    team_of_league(session, found, team_id)
    day = None
    if period is None:
        # One period, never the whole season: a year of matchups is not an
        # answer to "how is my week going", and it is forty times the size.
        calendar = season_calendar(session, season)
        day = calendar.scoring_period_on(pickups_api.TODAY()) if calendar is not None else None
        rows = leagues_api.list_periods(found, session)
        for row in rows:
            first, last = row.first_scoring_period, row.final_scoring_period
            if day is not None and first is not None and last is not None and first <= day <= last:
                period = row.period
                break
        if period is None and rows:
            period = max(row.period for row in rows if row.matchup_count)
    page = leagues_api.list_matchups(found, session, period=period, limit=200, offset=0)
    mine = [
        row
        for row in page.items
        if row.home.espn_team_id == team_id
        or (row.away is not None and row.away.espn_team_id == team_id)
    ]
    return {
        "league_id": league_id,
        "season": season,
        "period": period,
        "matchups": [
            {
                "period": row.period,
                "playoff": row.is_playoff,
                "winner": row.winner,
                "categories_tied": row.categories_tied,
                "sides": [
                    {
                        "espn_team_id": side.espn_team_id,
                        "name": side.name,
                        "categories_won": side.categories_won,
                        "line": {
                            stat.abbreviation: trim.n(stat.value)
                            for stat in side.statistics
                            if stat.is_scored_category
                        },
                    }
                    for side in (row.home, row.away)
                    if side is not None
                ],
                "bye": row.away is None,
            }
            for row in mine
        ],
        "note": (
            "the scores are ESPN's as the ingest last stored them, so a past "
            "period shows its final score"
        ),
        "provenance": block(session, found, day=day),
    }


def what_changed(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    today: int | None = None,
) -> dict[str, Any]:
    """The league's news in a window: injuries, adds, drops, claims and trades.

    `today` is a scoring period, and it is there because the obvious question
    on a replayed day -- "what changed before day 52?" -- has the wrong
    answer without it. The route's default window is the last twenty-four
    hours of real time, which on a season stored months ago is empty and
    says nothing. Given a day, the window is that day and the one before it,
    which is what the This week page shows.
    """
    found = league_member(session, viewer, league_id, season)
    if today is not None and since is None and until is None:
        calendar = season_calendar(session, season)
        if calendar is not None:
            end = datetime.combine(calendar.date_of(today), time.max, tzinfo=UTC)
            since, until = (end - timedelta(days=2)).isoformat(), end.isoformat()
    try:
        answer = changes_api.list_changes(
            found,
            session,
            viewer,
            since=_moment(since),
            until=_moment(until),
            team_id=team_id,
            kinds=None,
            limit=CHANGES,
        )
    except HTTPException as error:
        raise _passed_through(error) from None
    return {
        "league_id": answer.league_id,
        "season": answer.season,
        "window": {"since": answer.since.isoformat(), "until": answer.until.isoformat()},
        "team_id": answer.team_id,
        "opponent_team_id": answer.opponent_team_id,
        "of": answer.total,
        "changes": [
            {
                "at": row.at.isoformat(),
                "kind": row.kind,
                "text": row.text,
                "mine": row.mine,
                "opponent": row.opponent,
                "severity": row.severity,
            }
            for row in answer.items
        ],
        "note": (
            "the sentences are the site's own, and are the ones the morning "
            "email sends; `mine` and `opponent` are flags on facts every member "
            "can already see"
        ),
        "provenance": block(session, found),
    }


def _moment(raw: str | None) -> datetime | None:
    """An ISO moment from a model, or a sentence it can act on."""
    if raw is None or not raw.strip():
        return None
    try:
        found = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise RefusedError(
            f"{raw!r} is not a moment I can read: give it as 2026-01-28 or 2026-01-28T10:00:00Z"
        ) from None
    return found if found.tzinfo is not None else found.replace(tzinfo=UTC)


def recent_moves(
    session: Session, viewer: Viewer, league_id: int, season: int, days: int = 7
) -> dict[str, Any]:
    """Every executed and failed roster move in the league over a window."""
    found = league_member(session, viewer, league_id, season)
    if not 1 <= days <= MAX_DAYS:
        raise RefusedError(f"ask for between 1 and {MAX_DAYS} days")
    since = datetime.now(UTC) - timedelta(days=days)
    page = transactions_api.list_transactions(
        found,
        session,
        type=None,
        status=None,
        scoring_period=None,
        min_bid=None,
        limit=transactions_api.TRANSACTION_PAGE_LIMIT,
        offset=0,
    )
    inside = [
        row for row in page.items if row.processed_at is not None and row.processed_at >= since
    ][:TRANSACTIONS]
    return {
        "league_id": league_id,
        "season": season,
        "window_days": days,
        "since": since.isoformat(),
        "of": len(inside),
        "moves": [
            {
                "at": row.processed_at.isoformat() if row.processed_at else None,
                "scoring_period": row.scoring_period,
                "team": row.team,
                "type": row.type,
                "status": row.status,
                "bid": row.bid_amount,
                "players": [
                    {
                        "name": item.player_name,
                        "espn_player_id": item.player_id,
                        "what": item.item_type,
                        "from": item.from_team,
                        "to": item.to_team,
                    }
                    for item in row.items
                ],
            }
            for row in inside
        ],
        "note": ("failed claims are kept too, so a losing bid is here beside the one that won"),
        "provenance": block(session, found),
    }


# ---------------------------------------------------------------------------
# players and the wire
# ---------------------------------------------------------------------------


def player_card(
    session: Session,
    viewer: Viewer,
    player_id: int,
    league_id: int | None = None,
    season: int | None = None,
    today: int | None = None,
) -> dict[str, Any]:
    """One player's card: his line, his games, his status and what it rests on.

    The league and the season may be left out when the token can see one
    league: the numbers are counted over a league season's calendar, so there
    is always one, and guessing which would be worse than asking.
    """
    league_id = league_id if league_id is not None else the_one_league(session, viewer)
    season = season if season is not None else newest_season(session, viewer, league_id)
    found = league_member(session, viewer, league_id, season)
    try:
        card = players_api.player_card_route(player_id, found, session, today=today)
    except HTTPException as error:
        raise _passed_through(error) from None
    out: dict[str, Any] = {
        "espn_player_id": card.espn_player_id,
        "name": card.name,
        "position": card.position,
        "pro_team": card.pro_team,
        "as_of_day": card.today,
        "games_left": card.games_left,
        "playoff_games": card.playoff_games,
        "playoff_window": [card.playoff_first, card.playoff_last],
        "per_game": trim.nine(card.per_game),
        "weekly": trim.nine(card.weekly),
        "games_behind_the_line": card.games_so_far,
        "projection_source": card.projection_source,
        "thin": card.thin,
        "note": (
            "what he is worth a week is not on a card: that number needs the "
            "league's measured category spreads, and the tools that price a "
            "move carry it beside his name"
        ),
        "provenance": block(session, found, day=card.today),
    }
    if card.hurt:
        out["injury_status"] = card.injury_status
        out["expected_return_date"] = (
            card.expected_return_date.isoformat() if card.expected_return_date else None
        )
    return out


def free_agents(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    today: int | None = None,
    sort: str = "value",
) -> dict[str, Any]:
    """The wire on one day, priced by the league standard the reports use.

    Built from the same three functions a week report is built from --
    `load_free_agents` for who is available, `weekly_lines` for what each man
    is projected to post, `load_spots` for the lens that prices a line -- so
    a man's `worth_a_week` here is the number the plan would give him and not
    a second opinion.

    `sort` is `value` (what he gives a roster place, best first), `games`
    (most games left in the period) or `name`.
    """
    found = league_member(session, viewer, league_id, season)
    team = team_plan(session, viewer, found, league_id, team_id)
    calendar, missing = pickups_api.readiness(session, found)
    if missing or calendar is None:
        raise RefusedError(
            pickups_api.NOT_LISTENED.format(season=season, missing=" and ".join(missing))
        )
    if sort not in ("value", "games", "name"):
        raise RefusedError("sort by `value`, `games` or `name`")
    day = today if today is not None else calendar.scoring_period_on(pickups_api.TODAY())
    espn_team_id = int(team.espn_team_id)

    week = load_team_week(session, found, espn_team_id, day)
    wire = load_free_agents(session, found, week)
    roster = [man.player_id for man in week.roster]
    pool = [man.player_id for man in wire]
    weekly, _weeks = weekly_lines(
        session, found, [*roster, *pool], day, tilt=True, as_of=calendar.date_of(day)
    )
    floor = calibration.calibration(
        session, int(found.league_id), calibration.TYPICAL_PICKUP
    ).number
    opened = calibration.calibration(session, int(found.league_id), calibration.OPENED_PLACE).number
    spots = load_spots(
        session,
        found,
        espn_team_id,
        day,
        roster=roster,
        wire=pool,
        weekly=weekly,
        floor=floor,
        opened=opened,
    )
    order = [distribution.abbreviation for distribution in category_distributions(session, found)]
    espn = _espn_ids(session, pool)
    rows: list[dict[str, Any]] = [
        {
            "player": trim.player(
                {
                    "espn_player_id": espn.get(man.player_id, man.player_id),
                    "name": man.name,
                    "position": man.position,
                    "pro_team_id": man.pro_team_id,
                    "injury_status": man.injury_status,
                    "expected_return_date": man.expected_return_date.isoformat()
                    if man.expected_return_date
                    else None,
                    "games_remaining": man.games_remaining_this_period,
                    "waiver_clears_on": man.waiver_clears_on,
                    "waiver_clears_at": man.waiver_clears_at.isoformat()
                    if man.waiver_clears_at
                    else None,
                }
            ),
            "worth_a_week": trim.n(spots.value(man.player_id)),
            "weekly": trim.nine(weekly[man.player_id].totals(order))
            if man.player_id in weekly
            else {},
        }
        for man in wire
    ]
    if sort == "value":
        rows.sort(key=lambda row: (-float(row["worth_a_week"]), str(row["player"]["name"])))
    elif sort == "games":
        rows.sort(
            key=lambda row: (-int(row["player"]["games_left"] or 0), str(row["player"]["name"]))
        )
    else:
        rows.sort(key=lambda row: str(row["player"]["name"]))
    shown, of = trim.cut(rows, WIRE)
    lens = standard_lens(session, found, day)
    return {
        "league_id": league_id,
        "season": season,
        "for_team": {"espn_team_id": espn_team_id, "name": str(team.name)},
        "today": day,
        "sorted_by": sort,
        "of": of,
        "historical_wire": not has_free_agent_snapshots(session, found),
        "measured": lens.measured,
        "players": shown,
        "note": (
            "`worth_a_week` is the league standard: categories a week this man "
            "gives an ordinary roster place. What he is worth to YOUR roster "
            "after a particular deal is a different question, and `judge_trade` "
            "answers that one."
        ),
        "provenance": block(session, found, keys=WIRE_KEYS, day=day),
    }


def _espn_ids(session: Session, player_ids: Sequence[int]) -> dict[int, int]:
    wanted = sorted(set(player_ids))
    if not wanted:
        return {}
    return {
        int(player_id): int(espn_player_id)
        for player_id, espn_player_id in session.execute(
            select(Player.id, Player.espn_player_id).where(Player.id.in_(wanted))
        ).all()
    }


# ---------------------------------------------------------------------------
# a deal
# ---------------------------------------------------------------------------


def judge_trade(
    session: Session,
    viewer: Viewer,
    league_id: int,
    season: int,
    team_id: int,
    with_team: int,
    give: Sequence[int] | None = None,
    get: Sequence[int] | None = None,
    drop: Sequence[int] | None = None,
    their_drop: Sequence[int] | None = None,
    fill: Sequence[int] | None = None,
    their_fill: Sequence[int] | None = None,
    today: int | None = None,
) -> dict[str, Any]:
    """A proposed deal, judged from both sides, fit first and number second.

    `give` are the men leaving your team and `get` the men leaving theirs,
    by ESPN player id. The answer always carries this league's own record of
    what the headline number has done, verbatim.
    """
    found = league_member(session, viewer, league_id, season)
    team = team_plan(session, viewer, found, league_id, team_id)
    try:
        answer = trades_api.trade_report(
            found,
            team,
            session,
            with_team=with_team,
            give=list(give or ()),
            get=list(get or ()),
            drop=list(drop or ()),
            their_drop=list(their_drop or ()),
            fill=list(fill or ()),
            their_fill=list(their_fill or ()),
            today=today,
        )
    except HTTPException as error:
        raise _passed_through(error) from None
    body = answer.model_dump(mode="json")
    deal = body["trade"]
    if deal is None:
        return {
            "ready": False,
            "why_not": body["readiness"]["note"],
            "trade_record": body["calibration_note"],
            "provenance": block(session, found, keys=TRADE_KEYS, day=today),
        }
    return {
        "season": deal["season"],
        "judged_on_day": deal["today"],  # nothing after this day is read
        "effective_day": deal["effective_day"],
        "weeks_remaining": trim.n(deal["weeks_remaining"]),
        "sides": [_side(side) for side in deal["sides"]],
        "hurdle": trim.n(deal["hurdle"]),
        "notes": deal["notes"],
        "wire": {"pool_size": deal["pool_size"], "historical": deal["historical_wire"]},
        "trade_record": body["calibration_note"],
        "language": f"{LABEL_ONLY} And {OTHER_SIDE}",
        "provenance": _trade_provenance(session, found, int(deal["today"])),
    }


def _trade_provenance(session: Session, found: LeagueSeason, day: int) -> dict[str, Any]:
    """The usual block, with the trade record's sentence said once.

    `trade_record` is the longest note there is and it is already in the
    answer, verbatim, where the manager will read it. Printing it twice
    would be a fifth of the payload spent on a repeat.
    """
    out = block(session, found, keys=TRADE_KEYS, day=day)
    record = out["calibration"][calibration.TRADE_RECORD]
    record["note"] = "`trade_record` in this result is this note, verbatim"
    return out


def _side(side: dict[str, Any]) -> dict[str, Any]:
    """One side of a deal: the fit first, then the number."""
    playoffs = side["playoffs"]
    return {
        "espn_team_id": side["espn_team_id"],
        "team_name": side["team_name"],
        "summary": side["summary"],
        "categories": trim.categories(side["categories"]),
        "receives": [trim.trade_card(card) for card in side["receives"]],
        "gives": [trim.trade_card(card) for card in side["gives"]],
        "drops": [trim.trade_card(card) for card in side["drops"]],
        "fills": [trim.trade_card(card) for card in side["fills"]],
        "drop_source": side["drop_source"],
        "places": {
            "opened": side["places_opened"],
            "filled": side["places_filled"],
            "left_open": side["places_left_open"],
            "what_an_open_place_is_worth": trim.n(side["opened_value"]),
            "replacement_from_the_wire": trim.trade_card(side["replacement_player"]),
        },
        "net": trim.n(side["net"]),
        "per_week": trim.n(side["per_week"]),
        "expected_per_week_before": trim.n(side["expected_per_week"]),
        "clears_hurdle": side["clears"],
        "judgement": trim.judgement(side["judgement"]),
        "playoffs": {
            "measurable": playoffs["measurable"],
            "weeks": trim.n(playoffs["weeks"]),
            "games": playoffs["games"],
            "delta_per_week": trim.n(playoffs["delta_per_week"]),
            "delta_total": trim.n(playoffs["delta_total"]),
            "note": playoffs["note"],
            "categories": trim.categories(playoffs["categories"]),
        },
        "notes": side["notes"],
    }
