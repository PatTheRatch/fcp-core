# The co-manager: everything the site knows, as tools a model can call

**Written:** 2026-09-23. **Status:** built and exercised in process and over
stdio; the three live conversations the brief asked for are **not run** —
see "What has not been run".

Code: `app/mcp/` (`scope.py`, `provenance.py`, `trim.py`, `tools.py`,
`server.py`), the entry point `scripts/mcp_server.py`, the tokens
(`app/api_tokens.py`, `app/api/tokens.py`, migration `0029_api_tokens`), and
the skill `skills/box-out-co-manager/SKILL.md`. Tests: `tests/test_mcp.py`,
`tests/test_mcp_access.py`.

## Why

Everything the site knows already sits behind read-only routes with the
reasoning attached: the week and season plans, today's lineup, what changed,
the trade evaluator, the player card, the standings, the wire, and each
league's own calibration with its provenance (docs/intake.md).

A co-manager is a model that calls those and talks about them. **It must
never be the thing that produces a number.** A figure a model invents is
indistinguishable, in the reading, from one it looked up — and the whole
argument for this product is that its numbers can be traced to a
measurement. So the discipline lives in the tools and in the skill, not in
the model: the tools carry provenance, the refusals are sentences, the
language rules are in the payload, and the one tool that would have to guess
is not built.

That matters twice over. The owner reads it in his own Claude today. The
in-site chat, later, may run a cheaper model, and a cheaper model held to
tool results is better than a clever one held to nothing.

## The token

A tool has to be somebody. A cookie is a browser's and the `FCP_SERVICE_TOKEN`
in `.env` is the server's own, so a manager makes his own on
**Account → Connections → "Tools that read for you"**: a name, a button, and
the token written to the page once. Prefixed `bo_`, so a token in a config
file is recognisable; only its sha256 is stored, as a session cookie's is;
revoking one stops it dead.

**A token carries no scope, deliberately.** It acts as the manager who made
it, through the same dependencies every route declares
(`app.api.access.resolve_viewer`), so it reads his leagues and his teams'
plans and nothing more. A scope nobody can see is a promise nobody can
check. `tests/test_mcp_access.py` holds it to that on the fixture
`tests/test_access.py` uses: Alice's token reads Alice's team and hears
"This team's plan is its manager's." about Bob's.

In single mode the token acts as the owner — which is what single mode means
everywhere else — but it is still checked, so a revoked token stops working
on the one server this has actually run on.

## Adding it to Claude Code

In this repo, `.mcp.json` is already there:

```json
{
  "mcpServers": {
    "box-out": {
      "command": "./.venv/bin/python",
      "args": ["scripts/mcp_server.py", "--stdio"],
      "env": { "PYTHONPATH": ".", "BOX_OUT_TOKEN": "${BOX_OUT_TOKEN}" }
    }
  }
}
```

so all it needs is the token in the environment Claude Code is started from:

```
export BOX_OUT_TOKEN=bo_...        # from /account/connections
claude
```

The database comes from `.env` in the repo root, like everything else here.
`/mcp` in the session lists it; the tools appear as `mcp__box-out__*` and the
house rules as the slash command `/mcp__box-out__co_manager`.

**As run on 2026-09-22**, against the local database and the stored 2026
season, the server reported `"status":"connected"` with every tool and the
prompt registered.

## Adding it to Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "box-out": {
      "command": "/Users/you/fcp-core/.venv/bin/python",
      "args": ["/Users/you/fcp-core/scripts/mcp_server.py", "--stdio"],
      "env": {
        "PYTHONPATH": "/Users/you/fcp-core",
        "DATABASE_URL": "postgresql+psycopg://...",
        "BOX_OUT_TOKEN": "bo_..."
      }
    }
  }
}
```

Absolute paths everywhere: Claude Desktop starts the process with no shell
and no working directory of yours, so a relative path finds nothing and a
`.env` beside the repo is never read.

## The remote form

```
python scripts/mcp_server.py --http --host 127.0.0.1 --port 8787
```

serves streamable HTTP at `/mcp`. A request's own `Authorization: Bearer`
wins over the environment's token, so one server can answer for several
managers, each reading his own leagues. `BOX_OUT_TOKEN` stays as the
fallback for a connector that carries one token of its own.

**What the remote form is not, yet.** It does no OAuth: there is no
authorization server, no protected-resource metadata and no
`AuthSettings`/`TokenVerifier` wired up, so a host that expects to sign in
rather than to be handed a bearer cannot use it. That is the next piece of
work if this is ever served off this machine, and until it is, it belongs
behind the tailnet like the API.

## The tools

Thirteen, each a thin call into the function its route calls. Sizes are the
2026 season of ESPN league 3853870 on day 52 — a fourteen-team league with
eight seasons of history — as the model receives them (pretty-printed JSON);
tokens are counted with a cl100k tokenizer as a stand-in, so read them as
within ten per cent rather than exact.

| Tool | Returns | Result size |
|---|---|---|
| `my_leagues()` | the leagues and teams this token may read, and which teams' plans are its owner's | 2.5k chars, ~730 tokens |
| `league_context(league_id, season)` | the rules, roster and IR slots, adds a period, FAAB, the calendar, today's scoring period, the playoff weeks, and all six calibrated numbers with their source and sample | 11k chars, ~3,200 |
| `week_report(league_id, season, team_id, today?)` | the matchup as projected, the moves worth a look in full, the best of the rest in brief, the empty days, the bar | 12k chars, ~3,750 |
| `season_report(...)` | the best move of each kind, drop candidates, stashes, the churn guard, both bars | 9k chars, ~2,900 |
| `todays_lineup(...)` | the proposed lineup place by place beside the one that is set, and the places that will produce nothing tonight | 6k chars, ~1,900 |
| `what_changed(league_id, season, team_id?, since?, until?, today?)` | injuries, adds, drops, claims and trades as one sentence each, newest first | 2.9k chars, ~800 (two days of a real week) |
| `standings(league_id, season)` | every team's matchup and category record | 4k chars, ~1,230 |
| `projected_standings(league_id, season, today?)` | the record each team is projected to end on, its finishing odds, and the record of the method (docs/projected_record.md) | 8.5k chars, ~2,740 |
| `matchup(league_id, season, team_id, period?)` | one team's matchup, each side's nine as ESPN stored them | 1.7k chars, ~530 |
| `recent_moves(league_id, season, days?)` | the league's roster moves in a window, failed claims included | 0.8k chars, ~260 (a week) |
| `player_card(player_id, league_id?, season?, today?)` | his line per game and per week, games left, playoff games, status, and what the rate rests on | 1.4k chars, ~530 |
| `free_agents(league_id, season, team_id, today?, sort?)` | the wire priced at the league standard, best fifteen of however many | 8k chars, ~2,970 |
| `judge_trade(league_id, season, team_id, with_team, give, get, drop?, their_drop?, fill?, their_fill?, today?)` | both sides' nine categories before and after, the number, the playoff lens, and the league's trade record verbatim | 18k chars, ~5,120 |

**`what_changed` takes a scoring period.** Its route's default window is the
last twenty-four hours of real time, which on a season stored months ago is
empty and says nothing at all — the first thing the tool traffic showed.
Given `today`, the window is that day and the one before it, which is what
the This week page shows.

A turn that answers "what should I do this week" costs about 9,000 tokens of
tool results (`league_context`, `todays_lineup`, `week_report`,
`what_changed`); a trade answer about 8,300 (`league_context` and
`judge_trade`). That is what an in-site bot would pay per turn before its
own prompt.

### An example call, and what comes back

```
judge_trade(league_id=3853870, season=2026, team_id=3, with_team=1,
            give=[3133628], get=[4397424], today=52)
```

Myles Turner for Neemias Queta, our team 3 with team 1, on day 52 of the
stored 2026 season. The answer as it came back on 2026-09-22, with the six
other categories and the other side's detail elided and marked as such. It is
kept as the transcript it is: the `chance_*` figures below are from before the
weekly spread was widened on 2026-09-23 (docs/spread_revision.md), so every
one of them would now sit nearer a half, and the counts, the per-week number
and the shape of the payload are unaffected.

```json
{
  "season": 2026,
  "judged_on_day": 52,
  "effective_day": 53,
  "weeks_remaining": 12.571,
  "sides": [
    {
      "espn_team_id": 3,
      "team_name": "Through The Wire",
      "summary": "Gains field-goal percentage and rebounds; gives up threes and
                  blocks. Costs about 0.05 categories a week over the 13 weeks
                  it covers, -0.66 on the season. It costs 0.25 categories in
                  the week in front of it. Does not clear the 0.20 bar. Over
                  the playoff weeks alone it is -0.09 a week.",
      "categories": [
        {"category": "FT%", "before": 0.834, "after": 0.831, "delta": -0.002,
         "chance_before": 0.796, "chance_after": 0.783, "chance_delta": -0.013,
         "moved": true},
        {"category": "REB", "before": 189.4, "after": 196.753, "delta": 7.352,
         "chance_before": 0.359, "chance_after": 0.422, "chance_delta": 0.063,
         "moved": true}
      ],
      "receives": [{"espn_player_id": 4397424, "name": "Neemias Queta",
                    "worth_a_week": 0.602, "games_left": 40,
                    "playoff_games": 10, "games_so_far": 22}],
      "gives":    [{"espn_player_id": 3133628, "name": "Myles Turner",
                    "worth_a_week": 0.615, "games_left": 39,
                    "playoff_games": 11, "games_so_far": 25}],
      "places": {"opened": 0, "filled": 0, "left_open": 0,
                 "what_an_open_place_is_worth": 0.0},
      "net": -0.655, "per_week": -0.05, "clears_hurdle": false,
      "expected_per_week_before": 4.777,
      "judgement": {"delta_week": -0.246, "delta_season_per_week": -0.034,
                    "weeks_remaining": 12.0, "delta_total": -0.655,
                    "per_week": -0.05, "replacement": 0.417,
                    "record_without": [95.8, 75.2],
                    "record_with": [95.2, 75.8], "measured": true},
      "playoffs": {"measurable": true, "weeks": 3.0, "games": 21,
                   "delta_per_week": -0.086, "delta_total": -0.258},
      "notes": []
    },
    { "espn_team_id": 1, "team_name": "Foxes ShutUpNDribble",
      "net": 0.816, "per_week": 0.063, "clears_hurdle": false,
      "...": "the other side, judged with the same machinery" }
  ],
  "hurdle": 0.2,
  "notes": ["the wire is rebuilt from who played and was in nobody's lineup,
             because the listener never ran for this season; it cannot see a
             free agent who did not play"],
  "trade_record": "This number is a forecast, and here is its record. Over the
                   55 trades in this league's history that can be replayed, it
                   pointed at the side that did better in 25 of them...",
  "language": "`clears_hurdle` is a label, not advice... And the other side's
               numbers are our estimate of his roster's needs...",
  "provenance": { "...": "see below" }
}
```

Both sides' `clears_hurdle` are false and the two `net` figures do not
mirror, which is the point of judging on each roster rather than on a
league-average one (docs/trades.md section 7a).

## The provenance block

Every result carries one. It is the pages' habit of printing a bar's note
under the bar and a projection's `source_note` under the projection, moved
into the payload because a model has no page to print under.

```json
"provenance": {
  "league_id": 3853870,
  "season": 2026,
  "as_of": {"scoring_period": 52, "date": "2025-12-11", "stored_report": false},
  "calibration": {
    "stream_hurdle": {
      "means": "the bar a move this week has to clear to be worth a look",
      "value": 0.2, "source": "owner", "n": 0, "unit": "decision points",
      "measured_at": "2026-09-18T00:00:00+00:00",
      "note": "your choice, 2026-09-18: the least churn for no loss, with a
               finite add budget and finite FAAB"
    },
    "typical_pickup": {
      "value": 0.06, "source": "measured", "n": 924, "unit": "adds",
      "note": "measured on this league, 924 adds"
    },
    "opened_place": {"value": 0.38, "source": "measured", "n": 1536, "...": "..."}
  },
  "projection": {"source_note": "ESPN's projections, 2026 season, from the
                                 stored box scores"},
  "injuries": {"source": "nba_official",
               "reported_at": "2025-12-11T14:30:00+00:00",
               "read_as_of": "2025-12-11T15:00:00+00:00",
               "note": "the league's own report, as of that moment"},
  "read_only": "nothing here can add, drop, bid or accept anything on ESPN"
}
```

`source` is the accessor's (`app.calibration`): `owner` beats `measured`
beats `pooled` beats `default`, and a `default` is a number measured on
somebody else's league. `stored_report` says whether the answer came from
the morning's precompute or was built for this call. `injuries.reported_at`
is the moment of the newest NBA injury report this answer could see; null
with the note saying the league said nothing, which is not the same as a man
being fit (docs/injuries.md).

The skill holds the model to saying the `source` and the `n` in words
whenever it quotes the number.

## What is trimmed, and why

The rule is: **keep every number and every reason; drop what only a page
needs.** `app/mcp/trim.py` has the argument; in short —

- **Lists are cut, never summarised away**, and each cut list says how long
  it really was (`of`), so "of 412 free agents" is a thing the model can say
  and be right about. The wire is cut to 15, the ranked moves to 8, drop
  candidates to 5, a day's empty-place fillers to 3.
- **The moves that clear the bar keep everything**; the ones under it keep a
  name, a number and the label. That one decision is a third of a week
  report's size.
- **A player becomes five or six fields** — name, ESPN id, position, NBA
  team, status, games left — and his waiver dates only when he is actually
  on waivers.
- **Floats are rounded to three places.** Categories a week is measured on a
  few hundred decision points; its fourth decimal is noise with a token
  price.
- **Nothing about the fit is dropped.** Every category view a trade produces
  survives whole, both sides, including the playoff lens.
- **`judge_trade` says the trade record once.** It is in the answer, verbatim,
  where a manager reads it; the provenance entry points at it rather than
  repeating 1,200 characters.

What is dropped outright: ESPN ids repeated inside nested objects, the
layout fields the pages need (`describe` strings beside the parts they are
built from, `moved` flags on categories that did not move), and the
per-player arrays a page draws a grid from.

## Resources and the prompt

Six documents, read-only, under `boxout://notes/…`: `pickups`, `trades`,
`streaming_lane`, `pickups_backtest`, `intake`, `injuries`. They are the
arguments the pages were written out of, so a model asked *why* a bar is
0.20, or why a trade number is printed under a record, answers from the same
reasoning the site was built on rather than from its own sense of what is
probably true. Read from disk per call, like every page here.

One prompt, `co_manager`: the house rules in the voice of
docs/in_season_pages.md's "The language" — worth a look, never recommended;
the bar labels and never hides; every number with its sample; nothing acts
on ESPN.

## What is deliberately absent

- **`find_trades`.** Nobody has built it. A tool that returned a plausible
  list of deals would be the model producing numbers with our name on them,
  and nothing has measured whether a proposed trade is one the other manager
  would take (docs/trades.md section 9).
- **Anything that writes.** No add, no drop, no bid, no claim, no accept, and
  no path from here to ESPN. `tests/test_mcp.py` checks the tool names for
  verbs rather than trusting this paragraph.
- **A verdict.** No field says accept or reject. `clears_hurdle` is a label
  and the payload says so in words.
- **A per-player projection from a gated source.** Everything here is ESPN's
  own or derived from stored box scores (`app.projections.sources`). A tool
  that ever carries Basketball Monster's numbers has to ask `may_show`
  first, exactly as the draft screen's pool does.

## Errors

A refusal is one sentence, and it is the site's own. "This team's plan is
its manager's." for a team that is not yours; "not a member of this league";
the route's own 422 for a deal that cannot be read ("Through The Wire does
not have Bam Adebayo on its roster on day 52."); the 409 for a season with
nothing to report on. Never a stack trace, and never a hint about whether
the league or the team exists.

The SDK prefixes a tool error with "Error executing tool `<name>`:", so the
sentence arrives inside that. Everything after the colon is ours.

## The live run, 2026-09-23

The three conversations the brief asked for were driven on 2026-09-23 from
the owner's laptop, in `claude -p` with `.mcp.json`, `--strict-mcp-config`,
only `mcp__box-out__*` allowed, and `skills/box-out-co-manager/SKILL.md`
appended to the system prompt, against the local database on the stored
2026 season. Each transcript was then read tool call by tool call, and
**every number the model stated was compared with the tool results it had
been given.** The transcripts are the session's stream-json files; the
check was by hand.

| question | tools called | turns | numbers stated | not in a tool result |
|---|---|---|---|---|
| "day 52 — what should I do today and this week?" | `my_leagues`, `league_context`, `todays_lineup`, `week_report`, `what_changed` | 8 | 5.24 expected wins, nine chances, two moves' nets and bids, 42 FAAB, 0 of 7 adds, 95.8–75.2, three bars with their sources and dates, eight league moves | **none** |
| "judge Turner for Queta with Foxes" | `league_context`, `my_leagues`, `season_report`, `todays_lineup` ×2, `judge_trade` | 10 | nine categories before/after with chances, both sides' per-week and season numbers, playoff-weeks split, both men's worth/games/playoff games, the 25-of-55 record | **none** |
| "what's a streaming spot worth here and why?" | `my_leagues`, `league_context` | 4 | 0.38 (n=1,536), 0.06 (n=924), the three bars, roster shape, FAAB budget | **none** |

**So the claim is now a measurement, on three conversations:** the model
quoted no number a tool had not returned. It also did three things the
skill asks for without being prompted: it named the source and sample of
every calibration number ("measured on this league, 924 adds"; "your own
choice, dated 2026-09-18"); it called the other side of the trade "as our
projections estimate his roster's needs — never his opinion"; and it ended
each answer with a line that nothing here reaches ESPN.

Two things worth knowing from the transcripts:

- In conversation 1 the model noticed that Gary Trent Jr. for Jordan Walsh
  is labelled `clears_hurdle: true` at a net of 0.024, under the 0.20 bar,
  and said so rather than smoothing it over. The label is by design
  (`app.pickups.stream.Move.clears`: a move that fills an empty day clears
  on any positive net), but the payload did not say so; `LABEL_ONLY` now
  does.
- In conversation 2 it dated the 0.20 bar to 2026-09-18 (the week bar's
  date) where `judge_trade`'s provenance cites the season bar, dated
  2026-09-21. Same value, same source, wrong date — a misreading of two
  entries with the same number, not an invented one.

Cost, for the in-site bot's pricing: 0.73, 0.88 and 0.55 dollars a
conversation on the model the CLI runs, of which nearly all is cached
input; 4,100, 3,626 and 2,525 output tokens. Wall time 87, 85 and 33
seconds.

**Three conversations is a smoke test, not a study.** What it rules out is
the failure the design exists to prevent showing up on the first three
questions a manager would ask; it does not rule it out on the tenth.

## Decisions

- **The tools call the route functions, not HTTP.** A tool that fetched our
  own API would need a URL, a port and a second copy of the auth, and would
  answer differently the moment either drifted. Calling
  `app.api.pickups.report` means the page and the tool cannot be told two
  different things; `tests/test_mcp.py` holds them to it.
- **`app.api.pickups._report` became `report`.** It is the one function that
  says whether an answer came from the morning's stored row, and a tool has
  to be able to say that too.
- **A token has no scopes.** The alternative — a read-only token, a
  team-scoped token — is a second answer to "who may open what", and the
  first answer is already written down and tested (docs/accounts.md).
- **Single mode still checks the token.** Single mode enforces nothing else,
  but a revoked token that kept working on the only server this has run on
  would make revoking meaningless.
- **The refusals are the site's sentences, not new ones.** A manager who has
  seen "This team's plan is its manager's." in the browser should read the
  same words from his co-manager.
- **The language rules are in the payload**, not only in the skill. A cheaper
  model reading `clears_hurdle` with no instructions is one prompt-injection
  or one context-trim away from calling it a recommendation; the field next
  to it says what it is.
- **Three decimals.** A quantity measured on 616 decision points has no
  fourth decimal worth paying for, and the trim's rounding is the same
  rounding the equality tests compare against, so nothing is hidden by it.
