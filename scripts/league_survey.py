#!/usr/bin/env python3
"""Survey public fantasy-basketball leagues, and print only their shape.

What this is for: fcp-core is built around one league of ours — ESPN, 16 teams
in 2027, head-to-head nine categories including turnovers, auction, FAAB,
redraft. Before any decision about a second platform is worth taking, we want
to know how ordinary that is. So this walks a bounded sample of *public*
leagues on Sleeper, ESPN and Fantrax and counts formats.

**What it stores and prints.** League-level *structure* and nothing else:
sport, season, scoring type and its settings, the category list, team count,
roster slots, draft type and budget, waiver type and budget, redraft / keeper /
dynasty, trade deadline, playoff shape, seasons of history, and counts of
transactions, trades and traded picks. It never writes or prints a username,
display name, user id, team name, avatar or league name. League ids go only to
`data/survey/` (which is gitignored), so a reviewer can re-check a sample;
they never reach the report.

**How leagues are found, and why that is allowed.**

* Sleeper — from one league's own members and their other NBA leagues, breadth
  first. Sleeper publishes every league a user is in to anyone who asks, and
  the members are public in the league document this project already reads.
  Nothing here is hidden from the people being walked past: no brute force, no
  id scanning. Member lists are read in memory and never leave it.
* ESPN, Fantrax, Yahoo — only ids the owners published themselves, in public
  posts on Reddit's fantasy-basketball communities where the author invited
  the public to join. Reddit serves its own search as JSON; we read the links
  out of the posts and never store a username, a post title or a subreddit
  author.

**Politeness.** Under 3 calls a second to Sleeper, ESPN and Fantrax; at most
one request every 2 seconds to Reddit; a global ceiling of 2000 calls across
every provider, and per-provider caps below. Every request carries a
User-Agent naming the project.

**What it does not do.** No writes anywhere — not to a database, not to an
API. No migrations. It does not read `.env` and needs no credentials: every
call is anonymous, which is itself one of the findings (what a league's own
settings look like when nobody is logged in).

Usage:
    python scripts/league_survey.py sleeper          # part 1, small sample
    python scripts/league_survey.py reddit_ids       # find ids in public posts
    python scripts/league_survey.py espn             # read what is readable
    python scripts/league_survey.py fantrax
    python scripts/league_survey.py raw              # aggregated counts, as JSON
    python scripts/league_survey.py all              # everything, in order

Raw per-league records land in `data/survey/` and are never committed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

import requests
from espn_api.basketball.constant import STATS_MAP

# --- politeness -------------------------------------------------------------

#: Every request carries this. It names the project so a sysadmin on the other
#: end can see who is calling and why, which is the least a scraper owes.
USER_AGENT = (
    "fcp-core-league-survey/0.1 (fantasy basketball format research; "
    "https://github.com/PatTheRatch/fcp-core)"
)

#: A hard ceiling on every call this script makes, across every provider. The
#: instruction was 2000; this enforces it rather than trusting the loops.
GLOBAL_CALL_BUDGET = 2000

#: Seconds between calls, per provider. Sleeper asks for under 1000 a minute;
#: 3 a second is a third of that and is what the brief asked for.
SLEEPER_INTERVAL = 0.35
ESPN_INTERVAL = 0.35
FANTRAX_INTERVAL = 0.35
#: Reddit and its archive: one request every 2 seconds, and 60 in total. The
#: brief's Reddit budget is respected here, and the archive is paced the same
#: way because it rate-limits (see `arctic_shift_posts`).
REDDIT_INTERVAL = 2.0
REDDIT_CALL_BUDGET = 60

#: Per-provider ceilings, so one provider cannot eat the global budget.
SLEEPER_CALL_BUDGET = 1500
SLEEPER_LEAGUE_LIMIT = 150
ESPN_LEAGUE_LIMIT = 100
FANTRAX_LEAGUE_LIMIT = 100

#: Where raw output and id lists go. Gitignored: never committed.
DATA_DIR = os.path.join("data", "survey")

#: The Sleeper league the walk starts from. It is this repository's own
#: subject (docs/sleeper.md) and is public.
SLEEPER_ROOT_LEAGUE = "1404516094114377728"

#: Seasons to ask Sleeper for, per user: this one and the two before it.
SLEEPER_SEASONS = ("2026", "2025", "2024")

ESPN_READ_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons"
FANTRAX_LEAGUE_INFO = "https://www.fantrax.com/fxea/general/getLeagueInfo"

#: The ceiling on an ESPN league we will call "h2h categories" rather than a
#: points league with the same 1.0 weights. A category league scores one item
#: per category (ours has nine); a points league with flat weights is not a
#: real league, but the guard costs nothing.
MAX_CATEGORY_STATS = 24


# --- the polite session -----------------------------------------------------


@dataclass
class Calls:
    """One counting, sleeping HTTP session per provider.

    Every provider gets its own interval; the total is capped globally. The
    point of putting it in one object is that no loop can forget to be polite:
    it has to go through `get`.
    """

    provider: str
    interval: float
    budget: int
    session: requests.Session = field(default_factory=requests.Session)
    made: int = 0
    refused: int = 0
    by_status: Counter[int] = field(default_factory=Counter)
    _last: float = 0.0
    _total: list[int] = field(default_factory=lambda: [0])

    def get(self, url: str, **kwargs: Any) -> requests.Response | None:
        """GET one URL, sleeping as needed. None when the budget is spent.

        Raises nothing on an HTTP error status: a 401 from Yahoo or a 404 from
        ESPN is a finding about anonymous access, not a crash. Network errors
        are the caller's to log — they are retried once here, because one
        dropped connection should not become a missing league.
        """
        if self.made >= self.budget or self._total[0] >= GLOBAL_CALL_BUDGET:
            # A spent budget is a *stop*, not an error: the run has collected
            # what it is allowed to collect and the caller should finish with
            # what it has. Returning None here (rather than raising) means no
            # loop anywhere can forget to handle it, which is how a run once
            # died two thirds of the way through with a traceback instead of a
            # partial result.
            self.refused += 1
            return None
        gap = time.time() - self._last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last = time.time()
        self.made += 1
        self._total[0] += 1
        kwargs.setdefault("timeout", (5, 30))
        for attempt in (1, 2):
            try:
                response = self.session.get(url, headers={"User-Agent": USER_AGENT}, **kwargs)
                self.by_status[response.status_code] += 1
                return response
            except requests.RequestException:
                if attempt == 2:
                    self.by_status[-1] += 1
                    return None
                time.sleep(1.0)
        return None


def total_calls() -> int:
    """Every call made by every session, for the run's own report."""
    return sum(entry.made for entry in _ALL)


_ALL: list[Calls] = []


def session(provider: str, interval: float, budget: int) -> Calls:
    made = Calls(provider=provider, interval=interval, budget=budget)
    if _ALL:
        made._total = _ALL[0]._total
    else:
        _ALL.append(made)
    if made not in _ALL:
        _ALL.append(made)
    return made


# --- privacy ----------------------------------------------------------------


def scrub(value: Any) -> Any:
    """Strip anything that could name a person out of a raw response.

    A belt-and-braces pass, run over every record before it is written to
    `data/survey/`: the point is that even the raw file, which is gitignored
    but sits on a disk, carries no name for anyone. Keys that are league
    content rather than structure are dropped outright; on the rest, anything
    that looks like a name or a free-text field is removed.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            low = str(key).lower()
            names_anything = low.endswith(("_name", "name"))
            if low in DROP_KEYS or (names_anything and low not in KEEP_NAME_KEYS):
                continue
            if "username" in low or "display" in low or "avatar" in low or "email" in low:
                continue
            out[key] = scrub(item)
        return out
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


#: Keys dropped from stored rows: league content (chat, logo), and anything
#: that is a person's words or picture.
DROP_KEYS = frozenset(
    {
        "name",
        "leaguename",
        "teamname",
        "nickname",
        "firstname",
        "lastname",
        "fullname",
        "owner",
        "owners",
        "members",
        "member",
        "avatar",
        "logo",
        "metadata",
        "message",
        "messages",
        "last_message_text_map",
        "last_author_display_name",
        "last_author_id",
        "last_author_avatar",
        "last_pinned_message_id",
        "last_read_id",
        "chat",
        "shard",
        "group_id",
        "last_message_id",
        "last_message_attachment",
        "last_message_time",
        "divisions",
        "note",
        "notes",
    }
)

#: Even for a structure key that ends in `name`, keep it when it names a slot
#: (a position a league allows) rather than a person.
KEEP_NAME_KEYS = frozenset({"lineup_slot_counts", "position_limits"})


# --- Sleeper ----------------------------------------------------------------


def sleeper_get(calls: Calls, path: str) -> Any:
    response = calls.get(f"https://api.sleeper.app/v1/{path}")
    if response is None or response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def sleeper_structure(league: dict[str, Any]) -> dict[str, Any]:
    """The structure of one Sleeper league, and nothing about its people.

    The scoring verdict is *computed* rather than left to a reader, because
    the whole of part 1 is that question. Sleeper ships no scoring-type flag,
    so the classification comes from the shape of `scoring_settings`:

    * every weight the same and the set is counting stats -> the weights do
      nothing, which is what a categories or roto league looks like;
    * uneven weights, a negative weight on a miss, or a threshold bonus
      (`bonus_*`) -> a points formula, because none of those can mean
      anything to a category;
    * an empty map -> `unknown`, reported as such rather than guessed.
    """
    settings = league.get("settings") or {}
    scoring = {k: v for k, v in (league.get("scoring_settings") or {}).items()}
    live = {k: v for k, v in scoring.items() if v}
    negative = sorted(k for k, v in live.items() if v < 0)
    bonuses = sorted(k for k in live if k.startswith("bonus_"))
    distinct = {float(v) for v in live.values()}
    if not scoring or not live:
        kind = "unknown"
    elif bonuses or negative or len(distinct) > 1:
        kind = "points"
    else:
        kind = "flat (categories or roto)"
    return {
        "league_id": str(league.get("league_id")),
        "sport": league.get("sport"),
        "season": league.get("season"),
        "status": league.get("status"),
        "total_rosters": league.get("total_rosters"),
        "previous_league_id": league.get("previous_league_id"),
        "scoring_kind": kind,
        "scoring_keys": len(scoring),
        "scoring_weighted_keys": len(live),
        "scoring_distinct_weights": sorted(distinct),
        "scoring_negative": negative,
        "scoring_bonuses": bonuses,
        "scoring_settings": scoring,
        "roster_positions": league.get("roster_positions") or [],
        "settings": scrub(settings),
    }


def sleeper_score_of(scoring: dict[str, Any]) -> str:
    """Classify one scoring map. Shared by the walk and the raw pass."""
    live = {k: v for k, v in scoring.items() if v}
    if not scoring or not live:
        return "unknown"
    negative = [k for k, v in live.items() if v < 0]
    bonuses = [k for k in live if k.startswith("bonus_")]
    distinct = {float(v) for v in live.values()}
    if bonuses or negative or len(distinct) > 1:
        return "points"
    return "flat (categories or roto)"


def sleeper_walk(calls: Calls, verbose: bool = True) -> dict[str, Any]:
    """Part 1: walk out from one league, breadth first, and classify.

    Two hops of discovery: the root league's members, then each member's NBA
    leagues for this season and the two before it, then the members of those.
    Member ids are kept in memory only; nothing about a person is written.
    """
    seen: set[str] = set()
    leagues: dict[str, dict[str, Any]] = {}
    frontier: list[str] = [SLEEPER_ROOT_LEAGUE]
    member_ids: set[str] = set()

    # Hop 0 and 1: the root, and the leagues of its own members.
    while frontier and len(leagues) < SLEEPER_LEAGUE_LIMIT:
        league_id = frontier.pop(0)
        if not league_id or league_id in seen or str(league_id) == "0":
            continue
        seen.add(league_id)
        league = sleeper_get(calls, f"league/{league_id}")
        if not isinstance(league, dict):
            continue
        if str(league.get("sport")) != "nba":
            continue
        leagues[league_id] = sleeper_structure(league)
        users = sleeper_get(calls, f"league/{league_id}/users") or []
        for user in users:
            if isinstance(user, dict) and user.get("user_id") is not None:
                member_ids.add(str(user["user_id"]))
        if verbose:
            print(f"  [sleeper] root league read; {len(member_ids)} member(s) seen")

    # Hop 2: every member's other NBA leagues, this season and the two before.
    others: set[str] = set()
    for user_id in sorted(member_ids):
        if len(leagues) + len(others) >= SLEEPER_LEAGUE_LIMIT:
            break
        for season in SLEEPER_SEASONS:
            found = sleeper_get(calls, f"user/{user_id}/leagues/nba/{season}") or []
            for entry in found:
                if isinstance(entry, dict) and entry.get("league_id") is not None:
                    others.add(str(entry["league_id"]))
        if verbose and len(others) % 25 < 2:
            print(
                f"  [sleeper] {len(member_ids)} member(s) walked, "
                f"{len(others)} league id(s) found, {calls.made} calls"
            )

    # Read the structure of each new one.
    for league_id in sorted(others):
        if len(leagues) >= SLEEPER_LEAGUE_LIMIT:
            break
        if league_id in seen:
            continue
        seen.add(league_id)
        league = sleeper_get(calls, f"league/{league_id}")
        if not isinstance(league, dict) or str(league.get("sport")) != "nba":
            continue
        leagues[league_id] = sleeper_structure(league)

    if verbose:
        print(f"  [sleeper] {len(leagues)} NBA league(s) read in {calls.made} calls")
    return {
        "leagues": leagues,
        "members_seen": len(member_ids),
        "calls": calls.made,
        "status_counts": dict(calls.by_status),
    }


def sleeper_completed_season(calls: Calls, leagues: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One finished Sleeper season, read week by week: this settles two claims.

    docs/sleeper.md says a matchup row carries a *weekly* lineup and no per-day
    record. That can only be settled on a season that has played, so this finds
    the most recent completed one in the sample and reads its weeks. What it
    checks per week: does a matchup row carry `starters` (a lineup at all), and
    is there anything finer than one list per week — a per-day breakdown, a
    dated lineup, per-day points. It also reads the draft and transactions,
    since a completed season is the only place those shapes exist.
    """
    completed = [
        (lid, entry)
        for lid, entry in leagues.items()
        if str(entry.get("status")).lower() in ("complete", "completed") and entry.get("season")
    ]
    if not completed:
        return {"found": False, "reason": "no completed league in the sample"}
    # Newest complete season first: its weeks are the ones that were played.
    lid, entry = sorted(completed, key=lambda kv: str(kv[1]["season"]), reverse=True)[0]
    season = entry["season"]
    weeks: list[dict[str, Any]] = []
    roster_keys: list[str] = []
    for week in range(1, 24):
        matchups = sleeper_get(calls, f"league/{lid}/matchups/{week}") or []
        if not isinstance(matchups, list) or not matchups:
            if weeks:
                break
            continue
        row = next((r for r in matchups if isinstance(r, dict)), {})
        keys = sorted(row.keys())
        roster_keys = keys
        points = [r.get("points") for r in matchups if isinstance(r, dict)]
        starters_points = [
            r.get("starters_points")
            for r in matchups
            if isinstance(r, dict) and r.get("starters_points")
        ]
        weeks.append(
            {
                "week": week,
                "rows": len(matchups),
                "row_keys": keys,
                "has_starters": bool(row.get("starters")),
                "starters_len": len(row.get("starters") or []),
                "has_starters_points": bool(starters_points),
                "has_players_points": bool(row.get("players_points")),
                "has_any_per_day_key": [k for k in keys if re.search(r"day|daily|date", k, re.I)],
                "points_sample": [p for p in points[:4]],
            }
        )
        if len(weeks) >= 24:
            break
    transactions = sleeper_get(calls, f"league/{lid}/transactions/1") or []
    drafts = sleeper_get(calls, f"league/{lid}/drafts") or []
    draft: dict[str, Any] = {}
    if isinstance(drafts, list) and drafts:
        first = drafts[0] if isinstance(drafts[0], dict) else {}
        draft = {
            "type": first.get("type"),
            "settings": scrub(first.get("settings") or {}),
            "start_time": bool(first.get("start_time")),
        }
    return {
        "found": True,
        "season": season,
        "weeks": weeks,
        "roster_row_keys": roster_keys,
        "transactions_in_week_1": len(transactions) if isinstance(transactions, list) else 0,
        "draft": draft,
    }


# --- ESPN -------------------------------------------------------------------


def espn_settings(calls: Calls, league_id: str, season: int) -> dict[str, Any] | None:
    """One ESPN league's settings, anonymously, for one season.

    Anonymous is the question: ESPN refuses a private league. A 401/403 here
    is the finding, not an error.
    """
    url = f"{ESPN_READ_BASE}/{season}/segments/0/leagues/{league_id}?view=mSettings"
    response = calls.get(url)
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def espn_structure(payload: dict[str, Any], league_id: str, season: int) -> dict[str, Any]:
    """Classify one ESPN league's structure from its `mSettings` response.

    ESPN does *not* put `scoringType` in `settings` — it is in `status`
    (`currentLeagueType`) and in the scoring items themselves. The rule used
    here, and checked against our own league (which is nine-category):
    a `H2H_CATEGORY` league carries one scoring item per category, each worth
    1 point and none a reverse item; a points league carries weighted items.
    """
    settings = payload.get("settings") or {}
    status = payload.get("status") or {}
    draft = settings.get("draftSettings") or {}
    acquisition = settings.get("acquisitionSettings") or {}
    roster_settings = settings.get("rosterSettings") or {}
    schedule = settings.get("scheduleSettings") or {}
    trade = settings.get("tradeSettings") or {}
    items = ((settings.get("scoringSettings") or {}).get("scoringItems")) or []
    stats = []
    for item in items:
        if not isinstance(item, dict):
            continue
        stats.append(
            {
                "stat_id": item.get("statId"),
                "points": item.get("points"),
                "reverse": item.get("isReverseItem"),
            }
        )
    weights = {float(s["points"]) for s in stats if s["points"] is not None}
    # A points league weights its items; a category league gives each 1.0 and
    # has no reverse item at all.
    if not stats:
        scoring_type = "unknown"
    elif weights <= {1.0} and len(stats) <= MAX_CATEGORY_STATS:
        # One item per category, every one worth a win. Checked against our own
        # league: nine items, all 1.0, and it is nine-category.
        scoring_type = "h2h categories"
    else:
        # A points league weights its items (0.5 for a point, 2 for a steal).
        scoring_type = "h2h points"
    stat_ids = sorted(int(s["stat_id"]) for s in stats if s["stat_id"] is not None)
    # The same table the ingest uses for `league_season_categories.abbreviation`
    # (app/ingest.py imports it from espn_api), so a category here is named
    # exactly what fcp-core would store it as. Duplicating it would let the two
    # drift, which is the one thing a survey must not do.
    names = [STATS_MAP.get(str(i), f"STAT{i}") for i in stat_ids]
    lineup_slots = roster_settings.get("lineupSlotCounts") or {}
    return {
        "league_id": league_id,
        "season": season,
        "scoring_type": scoring_type,
        "is_public": settings.get("isPublic"),
        "league_type": status.get("currentLeagueType"),
        "team_count": settings.get("size"),
        "stat_count": len(stats),
        "stat_ids": stat_ids,
        "stat_names": names,
        # Stat id 11 is turnovers (espn_api's STATS_MAP, the same table the
        # ingest writes). Ours scores it, and a league that does not is a
        # different game however close the other eight categories are.
        "has_to": 11 in stat_ids,
        "has_pct": any(i in stat_ids for i in (19, 20, 21, 22)),
        "draft_type": draft.get("type"),
        "draft_budget": draft.get("auctionBudget"),
        "keeper_count": draft.get("keeperCount"),
        "acquisition_type": acquisition.get("acquisitionType"),
        "acquisition_budget": acquisition.get("acquisitionBudget"),
        "using_acquisition_budget": acquisition.get("isUsingAcquisitionBudget"),
        "lineup_slots": scrub(lineup_slots),
        "trade_deadline": trade.get("deadlineDate"),
        "matchup_period_count": schedule.get("matchupPeriodCount"),
        "playoff_team_count": schedule.get("playoffTeamCount"),
        "schedule_settings": scrub({k: v for k, v in schedule.items() if k not in ("divisions",)}),
    }


def espn_history(calls: Calls, league_id: str, season: int, max_years: int = 6) -> dict[str, Any]:
    """Walk back the seasons ESPN still holds, and count what a past one returns.

    A past season is the interesting one for fcp-core, whose ingest
    (app/ingest.py) reads mSchedule, mRoster with scoringPeriodId, mDraftDetail
    and mTransactions2. So this checks, on the oldest season that answers,
    whether each of those four is actually there — the question "if a league
    like ours were connected, could we backfill it".
    """
    available: list[int] = []
    for year in range(season, season - max_years, -1):
        settings = espn_settings(calls, league_id, year)
        if settings is None:
            continue
        available.append(year)
    if not available:
        return {"seasons": [], "checked_season": None}

    # Check the four read paths on the oldest season that answered.
    year = min(available)
    checks: dict[str, Any] = {"season": year}
    # `mMatchup`, not `mMatchupScore`. The two look interchangeable and are
    # not: `mMatchupScore` returns `cumulativeScore` with `statBySlot` null and
    # **no per-category results at all**, while `mMatchup` (and `mScoreboard`)
    # returns `scoreByStat`, one entry per category with its `result` (WIN /
    # LOSS / TIE) and the team's `score` in it. That map is the whole point of
    # a category league and is what a per-category week is built from, so a
    # survey that asked `mMatchupScore` would report "no category results" for
    # every league including a nine-category one. Verified on our own league,
    # 2021 and 2026.
    schedule = espn_view(calls, league_id, year, "mMatchup", scoring_period=1)
    if schedule:
        rows = schedule.get("schedule") or []
        checks["matchup_rows"] = len(rows)
        row = next((r for r in rows if isinstance(r, dict)), {})
        home = (row.get("home") or {}) if isinstance(row, dict) else {}
        by_stat = (home.get("cumulativeScore") or {}).get("scoreByStat") or {}
        checks["has_cumulative_categories"] = bool(by_stat)
        checks["category_count"] = len(by_stat)
        checks["has_points_by_scoring_period"] = bool(home.get("pointsByScoringPeriod"))
    roster = espn_view(calls, league_id, year, "mRoster", scoring_period=1)
    if roster:
        teams = roster.get("teams") or []
        checks["roster_teams"] = len(teams)
        entry = next(
            (
                e
                for team in teams
                for e in ((team.get("roster") or {}).get("entries") or [])
                if isinstance(e, dict)
            ),
            None,
        )
        # A daily lineup is what mRoster gives when asked for a scoring period:
        # an entry per slot, which is what app/ingest.py's daily pass reads.
        checks["has_daily_roster_entries"] = entry is not None
    draft = espn_view(calls, league_id, year, "mDraftDetail")
    if draft:
        detail = draft.get("draftDetail") or {}
        checks["draft"] = {
            "drafted": detail.get("drafted"),
            "complete": detail.get("complete"),
            "picks": len(detail.get("picks") or []),
        }
    transactions = espn_view(calls, league_id, year, "mTransactions2", scoring_period=1)
    if transactions:
        checks["transactions_in_period_1"] = len(transactions.get("transactions") or [])
    return {"seasons": sorted(available), "checked_season": checks}


def espn_view(
    calls: Calls, league_id: str, season: int, view: str, scoring_period: int | None = None
) -> dict[str, Any] | None:
    url = f"{ESPN_READ_BASE}/{season}/segments/0/leagues/{league_id}?view={view}"
    if scoring_period is not None:
        url += f"&scoringPeriodId={scoring_period}"
    response = calls.get(url)
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


#: Seasons an ESPN id is tried in, newest first. Ids come from posts of any
#: year, and a league not renewed for a season answers 404 for it, so one
#: season alone read nothing (2026-09-19: 13 of 14 were 404 for 2026).
ESPN_SEASONS = (2027, 2026, 2025, 2024, 2023)


def espn_read_all(
    calls: Calls, ids: list[str], season: int, verbose: bool = True
) -> dict[str, Any]:
    """Read every ESPN id that answers anonymously, and classify it.

    Each id is tried from `season` back through `ESPN_SEASONS`, and kept at
    the newest season that answers. A 401 is a private league and is not
    retried: it will not answer for any season without the owner's cookies.
    """
    readable: dict[str, Any] = {}
    refused = 0
    seasons = [season, *[s for s in ESPN_SEASONS if s != season]]
    for index, league_id in enumerate(ids[:ESPN_LEAGUE_LIMIT]):
        payload = None
        found_in = season
        for candidate in seasons:
            if calls.made >= calls.budget:
                break
            before = calls.by_status.get(401, 0)
            payload = espn_settings(calls, league_id, candidate)
            if payload is not None:
                found_in = candidate
                break
            if calls.by_status.get(401, 0) > before:
                break
        if payload is None:
            refused += 1
            continue
        readable[league_id] = espn_structure(payload, league_id, found_in)
        if verbose and index % 10 == 0:
            print(f"  [espn] {index + 1} tried, {len(readable)} readable, {calls.made} calls")
    if verbose:
        print(f"  [espn] {len(readable)} readable of {len(ids)} tried, {calls.made} calls")
    return {"readable": readable, "refused": refused, "tried": min(len(ids), ESPN_LEAGUE_LIMIT)}


# --- Fantrax ----------------------------------------------------------------


def fantrax_league(calls: Calls, league_id: str) -> dict[str, Any] | None:
    """One Fantrax league's info, anonymously. None when it is not readable.

    Fantrax answers HTTP 200 with an `error` object for a league that does not
    exist or is not public, which is a second kind of "not readable" — one
    this has to read the body to tell apart from a real league.
    """
    response = calls.get(f"{FANTRAX_LEAGUE_INFO}?leagueId={quote_plus(league_id)}")
    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or "error" in payload:
        return None
    return payload


def fantrax_structure(payload: dict[str, Any], league_id: str) -> dict[str, Any]:
    """Classify one Fantrax league from `getLeagueInfo`.

    Fantrax's naming is its own: `scoringSystem` carries the type, and the
    category set arrives as a list of stat ids with a `scoringCategory` label.
    """
    league = payload.get("league") or payload
    scoring = league.get("scoringSystem") or league.get("scoringType")
    categories = league.get("scoringCategories") or []
    names: list[str] = []
    for entry in categories if isinstance(categories, list) else []:
        if isinstance(entry, dict):
            label = entry.get("name") or entry.get("shortName") or entry.get("id")
            names.append(str(label))
        else:
            names.append(str(entry))
    return {
        "league_id": league_id,
        "scoring_system": scoring,
        "scoring_categories": names,
        "category_count": len(names),
        "has_to": any("turnover" in n.lower() or n.strip().upper() == "TO" for n in names),
        "team_count": league.get("teams") or league.get("teamCount"),
        "draft_type": (league.get("draftSystem") or league.get("draftType")),
        "keeper": league.get("keeperSystem") or league.get("keeperType"),
        "raw_keys": sorted(league.keys())[:40],
    }


# --- Reddit: the ids owners published themselves ----------------------------

#: The communities to search, and the searches to run. Only public posts.
SUBREDDITS = ("fantasybball", "fantasybasketball", "NBALeagueFinder")
REDDIT_QUERIES = (
    "espn league",
    "espn leagueId",
    "espn free league",
    "yahoo league",
    "fantrax league",
    "9 cat",
    "9cat",
    "h2h categories",
    "categories league",
    "roto league",
    "join my league",
    "need owners",
    "need managers",
    "open spots",
    "looking for owners",
    "free league",
    "auction draft",
)

#: Reddit itself refuses an anonymous reader from a datacenter IP: every JSON
#: URL that used to work now returns a 403 HTML block page, and the browser
#: gets the same page ("You've been blocked by network security"). So the
#: posts are read from **Arctic Shift**, a public archive of Reddit that
#: serves the same post document over an unauthenticated JSON API. It is a
#: mirror of public data, it costs Reddit nothing, and it carries no reader's
#: identity. `reddit_direct_probe` records the block as evidence.
ARCTIC_SHIFT_POSTS = "https://arctic-shift.photon-reddit.com/api/posts/search"

#: Link patterns. A league id is in the path or the query, and nothing else in
#: a post is read.
ESPN_LINK = re.compile(r"fantasy\.espn\.com/basketball.*?leagueId=(\d{3,10})")
YAHOO_LINK = re.compile(r"basketball\.fantasysports\.yahoo\.com/nba/(\d{3,10})")
FANTRAX_LINK = re.compile(r"fantrax\.com/fantasy/league/([A-Za-z0-9]{5,24})")

#: Words that mark a post as an invitation to join, which is the only kind
#: whose ids may be kept. A post merely *discussing* a league is not a licence
#: to walk into it.
INVITE_WORDS = re.compile(
    r"\b(join|joining|invite|invitation|open spot|open spots|need (?:one|two|\d) more|"
    r"recruiting|looking for (?:owners|managers|members)|free (?:league|spot)|"
    r"spots? (?:left|open|available)|draft tonight|starting up|new league)\b",
    re.IGNORECASE,
)


def reddit_direct_probe(calls: Calls) -> dict[str, Any]:
    """What Reddit itself says to an anonymous reader, as evidence.

    Reddit closed anonymous JSON access: the same URLs that used to return
    posts now return a 403 with an HTML block page, from this host and from
    the browser too. Recording it here is the difference between a finding
    and a memory, and it is why the ids below come from a mirror.
    """
    probe = calls.get(
        "https://www.reddit.com/r/fantasybball/search.json?q=espn&limit=1",
        allow_redirects=True,
    )
    if probe is None:
        return {"reachable": False}
    content_type = probe.headers.get("Content-Type", "")
    return {
        "reachable": probe.status_code == 200,
        "status": probe.status_code,
        "content_type": content_type,
        "looks_like_html": "text/html" in content_type,
        "needs_oauth": probe.status_code in (401, 403),
    }


def arctic_shift_posts(calls: Calls, subreddit: str, query: str, limit: int = 100) -> list[Any]:
    """One search over the public Reddit archive. Returns raw post documents.

    The fields read are `selftext` and `url`, and only to find links; the
    author and title are never read, stored or printed.

    The archive rate-limits with **HTTP 422 and `{"error": "Timeout. Maybe
    slow down a bit"}`** rather than a 429, so a 422 here means "you are
    going too fast", not "bad query" — it is retried with a growing wait
    instead of being treated as an empty result. That distinction cost one
    run's worth of queries before it was noticed, which is why it is written
    down.
    """
    url = (
        f"{ARCTIC_SHIFT_POSTS}?subreddit={quote_plus(subreddit)}"
        f"&query={quote_plus(query)}&limit={limit}&sort=desc"
    )
    for attempt in range(3):
        response = calls.get(url)
        if response is None:
            return []
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                return []
            data = payload.get("data") if isinstance(payload, dict) else None
            return [entry for entry in (data or []) if isinstance(entry, dict)]
        if response.status_code == 422:
            time.sleep(2.0 * (attempt + 1))
            continue
        return []
    return []


def reddit_search(calls: Calls, verbose: bool = True) -> dict[str, Any]:
    """Search public posts for league ids their authors published.

    The ids are only kept from posts whose text is an invitation to join, and
    the post's text is read to decide that and then dropped. Nothing about a
    post reaches disk: only the ids do.
    """
    found: dict[str, set[str]] = {"espn": set(), "yahoo": set(), "fantrax": set()}
    posts_seen = 0
    invited = 0
    for subreddit in SUBREDDITS:
        for query in REDDIT_QUERIES:
            if calls.made >= calls.budget:
                break
            entries = arctic_shift_posts(calls, subreddit, query)
            for entry in entries:
                text = " ".join(str(entry.get(part) or "") for part in ("selftext", "url", "title"))
                if not text.strip():
                    continue
                posts_seen += 1
                if not INVITE_WORDS.search(text):
                    continue
                invited += 1
                for provider, pattern in (
                    ("espn", ESPN_LINK),
                    ("yahoo", YAHOO_LINK),
                    ("fantrax", FANTRAX_LINK),
                ):
                    for match in pattern.findall(text):
                        if len(found[provider]) < 100:
                            found[provider].add(match)
            if verbose:
                print(
                    f"  [reddit/{subreddit}] {query!r}: {len(entries)} posts, "
                    f"totals " + ", ".join(f"{k} {len(v)}" for k, v in found.items())
                )
    if verbose:
        print(f"  [reddit] {posts_seen} posts read, {invited} were invitations, {calls.made} calls")
    return {
        "ids": {provider: sorted(ids) for provider, ids in found.items()},
        "posts_seen": posts_seen,
        "invitation_posts": invited,
        "calls": calls.made,
        "status_counts": dict(calls.by_status),
    }


# --- aggregation ------------------------------------------------------------


def _espn_split(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    split = Counter(str(row.get("scoring_type")) for row in rows)
    return dict(split)


def _espn_category_sets(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """How the category leagues split: nine-cat with TO, eight-cat, other."""
    sets: Counter[str] = Counter()
    for row in rows:
        if not str(row.get("scoring_type", "")).startswith("h2h categories"):
            continue
        names = list(row.get("stat_names") or [])
        count = len(names)
        has_to = bool(row.get("has_to"))
        sets[f"{count}-cat" + (" with TO" if has_to else " without TO")] += 1
    return dict(sets)


def summarise_espn(result: dict[str, Any]) -> dict[str, Any]:
    readable = list(result["readable"].values())
    return {
        "ids_tried": result["tried"],
        "readable_anonymously": len(readable),
        "refused": result["refused"],
        "format_split": _espn_split(readable),
        "category_sets": _espn_category_sets(readable),
        "team_counts": dict(Counter(int(r["team_count"]) for r in readable if r.get("team_count"))),
        "draft_types": dict(Counter(str(r.get("draft_type")) for r in readable)),
        "keeper_counts": dict(
            Counter("keeper" if (r.get("keeper_count") or 0) > 0 else "redraft" for r in readable)
        ),
    }


def comparables(
    espn: dict[str, Any],
    history: dict[str, dict[str, Any]],
    teams_min: int = 12,
    teams_max: int = 16,
) -> dict[str, Any]:
    """The leagues like ours: H2H nine categories *with TO*, 12 to 16 teams.

    Ours is nine-cat including turnovers, so a league without TO is a
    different game and is not counted here however close it looks. Each
    comparable is then checked for a completed past season that returns the
    four read paths fcp-core's ingest uses.
    """
    picked: dict[str, Any] = {}
    for league_id, row in espn["readable"].items():
        if not str(row.get("scoring_type", "")).startswith("h2h categories"):
            continue
        if not row.get("has_to"):
            continue
        size = row.get("team_count")
        if not isinstance(size, int) or not (teams_min <= size <= teams_max):
            continue
        picked[league_id] = {
            "season": row.get("season"),
            "team_count": size,
            "stat_names": row.get("stat_names"),
            "stat_count": row.get("stat_count"),
            "draft_type": row.get("draft_type"),
            "draft_budget": row.get("draft_budget"),
            "acquisition_type": row.get("acquisition_type"),
            "acquisition_budget": row.get("acquisition_budget"),
            "has_to": row.get("has_to"),
        }
    checked = {league_id: history.get(league_id, {}) for league_id in picked}
    return {"leagues": picked, "history": checked}


def write_json(name: str, payload: Any) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
    return path


def load_json(name: str) -> Any:
    path = os.path.join(DATA_DIR, name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --- the passes -------------------------------------------------------------


def pass_sleeper(verbose: bool = True) -> dict[str, Any]:
    calls = session("sleeper", SLEEPER_INTERVAL, SLEEPER_CALL_BUDGET)
    walk = sleeper_walk(calls, verbose=verbose)
    completed = sleeper_completed_season(calls, walk["leagues"])
    kinds = Counter(str(row.get("scoring_kind")) for row in walk["leagues"].values())
    result = {
        "leagues_read": len(walk["leagues"]),
        "members_seen": walk["members_seen"],
        "scoring_kind_counts": dict(kinds),
        "points_share": (kinds.get("points", 0) / max(1, len(walk["leagues"]))),
        "completed_season": completed,
        "calls": calls.made,
        "status_counts": dict(calls.by_status),
    }
    write_json("sleeper_structure.json", walk["leagues"])
    write_json("sleeper_summary.json", result)
    return result


def pass_reddit(verbose: bool = True) -> dict[str, Any]:
    calls = session("reddit", REDDIT_INTERVAL, REDDIT_CALL_BUDGET)
    probe = reddit_direct_probe(calls)
    search = reddit_search(calls, verbose=verbose)
    result = {"anonymous_probe": probe, **search}
    write_json("reddit_ids.json", result["ids"])
    write_json("reddit_summary.json", result)
    return result


def pass_espn(ids: list[str], verbose: bool = True) -> dict[str, Any]:
    calls = session("espn", ESPN_INTERVAL, 400)
    result = espn_read_all(calls, ids, season=2027, verbose=verbose)
    summary = summarise_espn(result)
    write_json("espn_structure.json", result["readable"])
    write_json("espn_summary.json", summary)

    # History and the four read paths, on the comparables only — the leagues
    # that actually match ours are worth the extra calls, and nothing else is.
    comparable = comparables(result, {})
    history: dict[str, Any] = {}
    for league_id in list(comparable["leagues"])[:12]:
        if calls.made >= calls.budget:
            break
        history[league_id] = espn_history(calls, league_id, season=2026)
    comparable = comparables(result, history)
    write_json("espn_history.json", history)
    write_json("comparable_leagues.json", comparable)
    summary["calls"] = calls.made
    summary["status_counts"] = dict(calls.by_status)
    summary["comparables"] = len(comparable["leagues"])
    write_json("espn_summary.json", summary)
    return summary


def pass_fantrax(ids: list[str], verbose: bool = True) -> dict[str, Any]:
    calls = session("fantrax", FANTRAX_INTERVAL, 200)
    readable: dict[str, Any] = {}
    for league_id in ids[:FANTRAX_LEAGUE_LIMIT]:
        if calls.made >= calls.budget:
            break
        payload = fantrax_league(calls, league_id)
        if payload is None:
            continue
        readable[league_id] = fantrax_structure(payload, league_id)
    result = {
        "ids_tried": min(len(ids), FANTRAX_LEAGUE_LIMIT),
        "readable_anonymously": len(readable),
        "calls": calls.made,
        "status_counts": dict(calls.by_status),
    }
    write_json("fantrax_structure.json", readable)
    write_json("fantrax_summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "pass_name",
        choices=("sleeper", "reddit_ids", "espn", "fantrax", "raw", "all"),
        help="which pass to run",
    )
    args = parser.parse_args()
    started = time.time()

    if args.pass_name == "sleeper":
        print(json.dumps(pass_sleeper(), indent=1, default=str))
    elif args.pass_name == "reddit_ids":
        print(json.dumps(pass_reddit(), indent=1, default=str))
    elif args.pass_name == "espn":
        ids = load_json("reddit_ids.json")["espn"]
        print(json.dumps(pass_espn(ids), indent=1, default=str))
    elif args.pass_name == "fantrax":
        ids = load_json("reddit_ids.json")["fantrax"]
        print(json.dumps(pass_fantrax(ids), indent=1, default=str))
    elif args.pass_name == "raw":
        for name in (
            "sleeper_summary.json",
            "reddit_summary.json",
            "espn_summary.json",
            "fantrax_summary.json",
        ):
            try:
                print(f"--- {name}")
                print(json.dumps(load_json(name), indent=1, default=str))
            except OSError as exc:
                print(f"    (not run: {exc})")
    else:
        print("== sleeper ==")
        print(json.dumps(pass_sleeper(), indent=1, default=str))
        print("== reddit ==")
        reddit = pass_reddit()
        print(json.dumps(reddit, indent=1, default=str))
        print("== espn ==")
        print(json.dumps(pass_espn(reddit["ids"]["espn"]), indent=1, default=str))
        print("== fantrax ==")
        print(json.dumps(pass_fantrax(reddit["ids"]["fantrax"]), indent=1, default=str))

    print(f"\n{total_calls()} calls in {time.time() - started:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
