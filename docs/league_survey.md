# What public fantasy basketball leagues look like

**Surveyed:** 2026-09-19, by `scripts/league_survey.py` (read-only, anonymous,
no credentials). **Question:** how common are leagues like Full Court Press
(ESPN, head-to-head nine categories with turnovers, 16 teams, auction,
redraft), and can fcp-core learn from any? **Answer:** none were found, and
every public league that could be read scored points.

## What was read

| Provider | How leagues were found | Ids | Read without a login | Format of those read |
|---|---|---|---|---|
| Sleeper | public member lists, from GOAT League outwards | 69 leagues, 113 calls | 69 | points, all 69 |
| ESPN | invitation links in public Reddit posts | 14 | 4 | head-to-head points, all 4 (7, 8, 8 and 10 teams) |
| Fantrax | invitation links in public Reddit posts | 2 | 2 | points, both |
| Yahoo | invitation links in public Reddit posts | 37 | none tried | needs an OAuth app |

Leagues like ours, head-to-head nine categories including turnovers, 12 to 16
teams, readable, with a finished season: **zero**.

**Read this with care.** The sample is small and skewed. Public "join my
league" posts are casual leagues, and serious category leagues tend to be
private, which ESPN refuses to anyone without the owner's login (five of the
fourteen). So "every readable league scored points" is true of what public
data exposes, not a measurement of all fantasy basketball. It does say that a
product which only understands categories cannot serve the leagues that are
easiest to find.

## Per provider

**Sleeper.** Anonymous, documented, and the only provider with a legitimate
discovery path (a user's leagues are public). Basketball is points only:
Sleeper sells it as Lock-In, which is not a setting in the data at all (see
docs/sleeper.md, section 2). A completed season returns weekly matchups with
points per slot and per player, transactions (166 in one league's first week)
and the draft, and no per-day data of any kind.

**ESPN.** A league set to public reads anonymously through
`lm-api-reads.fantasy.espn.com`, every season it existed, through the same
views fcp-core's ingest uses: `mSettings`, `mTeam`, `mRoster` with
`scoringPeriodId` (daily lineups), `mDraftDetail` and `mTransactions2`. A
private league answers 401 without its owner's `espn_s2` and `SWID`. There is
no discovery path: a league id has to be handed over. Two traps found:
per-category matchup results come from `view=mMatchup`, not `mMatchupScore`
(which returns no categories), and a league not renewed for a season answers
404 for it, so an id from an old post has to be tried season by season. Full
Court Press itself is set to public, which is why it reads without cookies.

**Fantrax.** `fxea/general/getLeagueInfo?leagueId=` answers anonymously for a
public league, with settings, rosters, matchups and scoring; an unreadable
league is an HTTP 200 carrying an `error` object, not a 4xx. Its points
scoring can be tiered (bonus ranges per stat), which a simple weighted sum
does not express. No discovery path.

**Yahoo.** Nothing reads without OAuth 2.0: an app registered at
developer.yahoo.com (a Yahoo account, an app name, a redirect URL, read access
to Fantasy Sports), and then each manager granting access to their own
leagues. No discovery path. Supporting Yahoo means a sign-in-with-Yahoo step
per user, not a league id.

**Reddit itself** refuses anonymous JSON from the VPS (403, a "blocked by
network security" page), so the ids came from Arctic Shift, a public archive
of Reddit posts, reading only a post's text and links, and only posts that
invited the public to join. No author, title or subreddit was stored.

## What it means for fcp-core

1. **Points coverage matters sooner than planned.** It is also the cheap half:
   a points league is one weighted sum of raw stats, which the category
   registry proposed on 2026-09-19 can express as a single category. Fantrax's
   tiered bonuses and Sleeper's threshold bonuses need a little more than a
   plain weight.
2. **Category leagues come through their managers, not discovery.** They are
   private, so the way in is a manager who connects his own league, which is
   exactly the product flow in docs/product.md.
3. **ESPN is the only provider whose history includes daily lineups.** Every
   narrative feature that reads who sat where works on ESPN leagues only.

## Privacy

The survey keeps league-level structure only. League ids live in
`data/survey/` on the VPS (gitignored), never in this document. No username,
display name, user id, team name, league name, post title or author was
written anywhere. `scripts/espn_probe.py` was changed in the same work to stop
printing league, team and owner names.
