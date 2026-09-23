/* Every page's shared parts: where we are, what we fetch, how a number is
   written, the nine-category strip, and the theme switch. The shell over
   every page (the navigation) is `shell.js`, loaded after this file.

   The pages differ only in what they draw; the vocabulary below is the same
   on all of them, so a phrase is written once. The language rule
   lives here too (`WORTH_A_LOOK`, `NOTHING_CLEARS`): the tool generates
   ideas and the manager decides, so nothing on a page says "recommended"
   or tells anyone to do anything.

   No framework and no build step. This file is served as it is written. */

"use strict";

/** The nine, in the fixed order every screen of this project uses. ESPN's
 *  own order is whatever the league's settings say and has moved between
 *  seasons, so it is never read from a response. */
const CATS = ["FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"];

/** A category where less is better. */
const INVERTED = new Set(["TO"]);

const WORTH_A_LOOK = "Worth a look";
const NOTHING_CLEARS = "Nothing clears the bar";

const $ = (id) => document.getElementById(id);

/** Where this page is, read from its own URL rather than written into it
 *  (docs/site.md has the map):
 *
 *    /l/{league}/{season}/{week|standings|draft|history}
 *    /l/{league}/{season}/team/{team}/{week|season|moves}
 *    /account/{connections|projections|alerts}
 *    /pages/claim/{league}/{season}
 *
 *  plus ?today= (a scoring period), ?period= (a matchup period, This week
 *  only) and ?me= (whose team is ours, by name, for the context route). */
function place() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const query = new URLSearchParams(window.location.search);
  const number = (text) =>
    text === null || text === undefined || text === "" || Number.isNaN(Number(text))
      ? null
      : Number(text);
  const where = {
    league: null,
    season: null,
    team: null,
    section: null,
    today: number(query.get("today")),
    period: number(query.get("period")),
    me: query.get("me"),
  };
  if (parts[0] === "l") {
    where.league = number(parts[1]);
    where.season = number(parts[2]);
    if (parts[3] === "team") {
      where.team = number(parts[4]);
      where.section = `team-${parts[5]}`;
    } else {
      where.section = parts[3] || null;
    }
  } else if (parts[0] === "pages" && parts[1] === "claim") {
    where.league = number(parts[2]);
    where.season = number(parts[3]);
    where.section = "claim";
  } else if (parts[0] === "account") {
    where.section = parts[1] || null;
  }
  return where;
}

/** The address of a page on the map, from its parts. */
const leagueUrl = (league, season, section) => `/l/${league}/${season}/${section}`;
const teamUrl = (league, season, team, which) => `/l/${league}/${season}/team/${team}/${which}`;

/** A query string from the parts that are set, and nothing when none are. */
function params(where, extra) {
  const query = new URLSearchParams();
  if (where.today !== null && !Number.isNaN(where.today)) query.set("today", where.today);
  if (where.me) query.set("me", where.me);
  Object.entries(extra || {}).forEach(([key, value]) => query.set(key, value));
  const text = query.toString();
  return text ? `?${text}` : "";
}

/** The one line a page says when its team is not the reader's (a 403). */
const NOT_YOURS = "This team's plan is its manager's.";

/** Set once a route has refused the reader, so the page's own complaint
 *  about the failed fetch does not replace the plain line. */
let REFUSED = false;

/** Signed out: go and sign in, and come back here afterwards. */
function toSignIn() {
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/sign-in?next=${encodeURIComponent(here)}`);
}

/** One JSON route. A refusal comes back as a message, not as an exception,
 *  because every one of them is something the page should say out loud: a
 *  season the listener never ran for is an answer. Two are handled here for
 *  every page: a 401 (signed out) goes to the sign-in page, and a 403 (not
 *  this reader's team) is one plain line and nothing else (docs/accounts.md).
 *
 *  `quiet` is for a fetch that is one part of a page and not the page itself
 *  (the free pages' look at the reader's own week): its 403 comes back with
 *  its status for the caller to word, and the rest of the page stands. */
async function get(url, options) {
  const quiet = Boolean(options && options.quiet);
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (response.ok) return { ok: true, status: response.status, body: await response.json() };
  if (response.status === 401) {
    toSignIn();
    return { ok: false, status: 401, detail: "Signing in…" };
  }
  if (response.status === 403 && !quiet) {
    fail(NOT_YOURS);
    REFUSED = true;
    return { ok: false, status: 403, detail: NOT_YOURS };
  }
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (body && body.detail) detail = typeof body.detail === "string" ? body.detail : detail;
  } catch (error) {
    /* a response that is not JSON; the status line is the message */
  }
  return { ok: false, status: response.status, detail };
}

/* ---- writing numbers ----------------------------------------------------
   Everything here returns a string and never "undefined" or "NaN": a value
   that is missing is an en dash, because a blank cell reads as a zero. */

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const dash = "–";

const fixed = (v, places) => (isNum(v) ? v.toFixed(places) : dash);
const signed = (v, places) => (isNum(v) ? (v >= 0 ? "+" : "") + v.toFixed(places) : dash);
const pct = (v) => (isNum(v) ? `${Math.round(v * 100)}%` : dash);

/** A category total: the two percentages as rates, the rest as counts. */
const showCat = (cat, v) =>
  !isNum(v) ? dash : cat.endsWith("%") ? v.toFixed(3).replace(/^0/, "") : v.toFixed(1);

/** The made-over-attempted pair behind each rate. A side's weekly totals go
 *  out as raw counts, so FG% and FT% are not in them and have to be rebuilt
 *  here: a rate cannot be summed across a roster or a week without the
 *  attempts under it, which is why they travel as counts (app/scoring/lines.py). */
const COMPONENTS = { "FG%": ["FGM", "FGA"], "FT%": ["FTM", "FTA"] };

/** One category out of a side's raw weekly totals, as a rate or a count. */
function totalOf(totals, cat) {
  if (!totals) return undefined;
  const parts = COMPONENTS[cat];
  if (!parts) return totals[cat];
  const made = totals[parts[0]];
  const shots = totals[parts[1]];
  return isNum(made) && isNum(shots) && shots > 0 ? made / shots : undefined;
}

/** A projected category record, the way both CLIs write one. */
const record = (pair) =>
  Array.isArray(pair) && pair.length === 2 && isNum(pair[0]) && isNum(pair[1])
    ? `${pair[0].toFixed(1)}-${pair[1].toFixed(1)}`
    : dash;

/** A won-lost record, and the ties only when there are some. */
const wlt = (won, lost, tied) =>
  isNum(won) && isNum(lost) ? `${won}–${lost}${tied ? `–${tied}` : ""}` : dash;

/** A category total as ESPN stored it: a rate as .457, a count whole. */
const storedCat = (cat, v) =>
  !isNum(v) ? dash : cat.endsWith("%") ? v.toFixed(3).replace(/^0/, "") : String(Math.round(v));

/** A count with its noun, pluralised. */
const count = (n, one, many) => `${isNum(n) ? n : dash} ${n === 1 ? one : many || `${one}s`}`;

const escape = (text) =>
  String(text === null || text === undefined ? "" : text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

/** A date as the mastheads write one: Thu 29 Jan. */
function dayName(iso) {
  if (!iso) return dash;
  const when = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(when.getTime())) return dash;
  return when.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

/** The weekday alone, for "on waivers, clears Thursday". */
function weekday(iso) {
  if (!iso) return dash;
  const when = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(when.getTime())) return dash;
  return when.toLocaleDateString(undefined, { weekday: "long", timeZone: "UTC" });
}

/** A moment as the "refreshed at" line writes one. */
const clock = (when) =>
  (when instanceof Date ? when : new Date()).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });

/* ---- the nine-category strip -------------------------------------------
   The draft screen's component (app/draft/static/draft.html, `stripHtml`):
   the same markup and the same CSS class names, so the two products share
   one strip rather than two that drift. What shades a cell differs, because
   there is no draft pool here to rank against -- a probability shades by how
   near certain it is, and a shift by how much it moved -- but the component
   does not know that, and takes the shade it is handed. */

const shadeOf = (q) => (q >= 0.9 ? "s3" : q >= 0.7 ? "s2" : q >= 0.5 ? "s1" : "");

/**
 * @param cells one per category, keyed by abbreviation:
 *              {text, shade, tone} -- tone "up" or "down" colours the figure.
 */
function stripHtml(cells) {
  return CATS.map((cat) => {
    const cell = (cells && cells[cat]) || {};
    const classes = [cell.shade || "", cell.tone || ""].filter(Boolean).join(" ");
    return (
      `<div class="${classes}"><span class="lab">${escape(cat)}</span>` +
      `<b>${escape(cell.text === undefined ? dash : cell.text)}</b></div>`
    );
  }).join("");
}

/** The strip for a set of win probabilities: shaded by how settled each is. */
const probabilityStrip = (probabilities) =>
  stripHtml(
    Object.fromEntries(
      CATS.map((cat) => {
        const p = probabilities ? probabilities[cat] : undefined;
        return [
          cat,
          {
            text: pct(p),
            shade: isNum(p) ? shadeOf(Math.max(p, 1 - p)) : "",
            tone: !isNum(p) ? "" : p >= 0.6 ? "up" : p <= 0.4 ? "down" : "",
          },
        ];
      }),
    ),
  );

/** The strip for the categories a move moved, in probability points. */
const shiftStrip = (moved) => {
  const by = {};
  (moved || []).forEach((shift) => {
    by[shift.abbreviation] = shift;
  });
  return stripHtml(
    Object.fromEntries(
      CATS.map((cat) => {
        const shift = by[cat];
        const d = shift ? shift.delta : 0;
        return [
          cat,
          {
            text: isNum(d) && Math.abs(d) >= 0.005 ? signed(d * 100, 0) : dash,
            shade: isNum(d) ? shadeOf(0.5 + Math.min(0.49, Math.abs(d) * 2)) : "",
            tone: !isNum(d) ? "" : d > 0.005 ? "up" : d < -0.005 ? "down" : "",
          },
        ];
      }),
    ),
  );
};

/* ---- shared phrasing ----------------------------------------------------
   Both reports print the same judgement lines, so they are written once. */

/** The two horizons, the net, and the season record either way. */
function judged(j) {
  if (!j) return "";
  const lines = [
    `week <b>${signed(j.delta_week, 3)}</b> + season <b>${signed(j.delta_season_per_week, 3)}</b>` +
      ` a week over ${fixed(j.weeks_remaining, 1)} weeks = net ` +
      `<b>${signed(j.delta_total, 3)}</b> categories`,
    `projected record <b>${record(j.record_without)}</b> without, ` +
      `<b>${record(j.record_with)}</b> with`,
  ];
  if (j.measured === false) {
    lines.push("no league standard measurable yet, so nothing is charged for the season");
  }
  return lines.map((line) => `<p class="why">${line}</p>`).join("");
}

/** A player, with the starts he gets of the games he has left. */
const withStarts = (player, starts) =>
  `<b>${escape(player ? player.name : dash)}</b>` +
  (isNum(starts) ? ` (${starts} of ${player.games_remaining} games)` : "");

/** "on waivers, clears Thursday", when the league has him on waivers. */
function waiverNote(player, today) {
  if (!player || !player.waiver_clears_at) return "";
  if (isNum(player.waiver_clears_on) && isNum(today) && today >= player.waiver_clears_on) return "";
  return `, on waivers, clears ${weekday(player.waiver_clears_at)}`;
}

/** The bid, the bucket it came from, and what capped it. */
function bidNote(bid) {
  if (!bid) return "";
  if (!bid.sample) return `bid: nothing to go on (${escape(bid.note)})`;
  const capped = bid.capped_by ? `, capped by ${escape(bid.capped_by)}` : "";
  return (
    `bid $${bid.amount}: the ${escape(bid.basis)} of rank ${escape(bid.bucket)} ` +
    `($${bid.low}–$${bid.high} over ${count(bid.sample, "claim")}${capped})`
  );
}

/** The FAAB readout. Never a negative: `app/pickups/state.py` says why the
 *  bid feed and ESPN's own ledger disagree by a few dollars on some teams. */
const faab = (report) =>
  report.faab_overspent
    ? { value: "$0", note: `our sum of the bid feed runs $${report.faab_overspent} past the budget` }
    : { value: `$${report.faab_remaining}`, note: "of the season's pot" };

/** Which wire the report looked at. */
const wire = (report) =>
  report.historical_wire
    ? "historical wire (no snapshots)"
    : "from the listener's latest pass";

/* ---- the finish: a second lens, never a second bar ----------------------
   Where a change leaves one team in the projected standings, before and
   after (docs/what_if.md). The trade page draws it under each side of a
   deal and the week page under a what-if, out of this one function, so the
   two screens cannot drift apart on the one figure nobody has a bar for.

   It sits under the categories and never beside the number. The number is
   read against a bar; this is not. It is the same rosters played out
   against the real opponent each week rather than a league-average one, and
   nothing on either page is labelled, recommended, re-sorted or refused on
   it. So it comes with the simulation's own sampling band beside it and the
   forecast's published record underneath, and when the odds moved by less
   than the band it says so in those words. */

/** "3rd", the way the standings page numbers a place. */
function ordinal(place) {
  if (!isNum(place)) return dash;
  const rest = place % 100;
  const suffix = rest >= 11 && rest <= 13 ? "th" : ["th", "st", "nd", "rd"][place % 10] || "th";
  return `${place}${suffix}`;
}

/** Odds to a tenth of a point, rather than the whole point `pct` gives the
 *  standings. One man usually moves these by less than a point, and two
 *  figures reading "59% → 59%" would say the arithmetic failed rather than
 *  that the move is small; the band beside them is in tenths for the same
 *  reason. */
const oddsPoint = (v) => (isNum(v) ? `${(v * 100).toFixed(1)}%` : dash);

/** One team's finish, before and after: the projected record, the place and
 *  the playoff odds, the sampling band in brackets, and the forecast's own
 *  published record underneath. */
function finishHtml(f) {
  if (!f) return "";
  const was =
    `${record(f.record_before)} · ${ordinal(f.place_before)} · ` +
    `playoffs ${oddsPoint(f.playoff_odds_before)}`;
  const now =
    `${record(f.record_after)} · ${ordinal(f.place_after)} · ${oddsPoint(f.playoff_odds_after)}`;
  // A tenth of a point, except when the band is under one and "±0.0 points"
  // would read as a simulation that is exact, which is the opposite of what
  // this is here to say.
  const points = f.odds_band * 100;
  const band = `±${fixed(points, points < 0.1 ? 2 : 1)} points on each`;
  const quiet = f.moved_more_than_the_band === false || f.readable === false;
  return (
    `<p class="whose" style="margin-top:20px">finish</p>` +
    `<p class="hint"><span class="mono">Projected ${escape(was)}</span> → ` +
    `<span class="mono"><b>${escape(now)}</b></span> ` +
    `<span class="faint">(${escape(band)}${quiet ? ", so this is inside the noise" : ""})</span>` +
    `</p>` +
    `<p class="hint faint">${escape(f.calibration_note)}</p>`
  );
}

/* ---- the theme ---------------------------------------------------------
   Light is the default and the skin these pages were approved in; the switch
   flips to the draft room's palette. Kept in localStorage, wrapped, so a
   private window merely forgets it. */

function readTheme() {
  try {
    return window.localStorage.getItem("fcp-theme");
  } catch (error) {
    return null;
  }
}

function setTheme(value) {
  const theme = value === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  const button = $("theme");
  if (button) {
    button.textContent = theme === "light" ? "Lights down" : "Lights up";
    button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }
  try {
    window.localStorage.setItem("fcp-theme", theme);
  } catch (error) {
    /* private window */
  }
}

function startTheme() {
  setTheme(readTheme() || "light");
  const button = $("theme");
  if (button) {
    button.onclick = () =>
      setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  }
}

/** The page could not be drawn: say what happened, in the page, not the console. */
function fail(message) {
  const where = $("failed");
  if (!where || REFUSED) return;
  where.hidden = false;
  where.textContent = message;
}
