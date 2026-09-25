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
 *    /design
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
  } else if (parts[0] === "design") {
    where.section = "design";
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
  // An open page (/design) asks who is there without sending a stranger to
  // sign in: signed out is an answer it draws, not an error.
  if (response.status === 401 && options && options.signedOutOk) {
    return { ok: false, status: 401, detail: "Signed out" };
  }
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

/** The strip for a set of win probabilities: shaded by how settled each is.
 *  A chance is a figure, not a change to the viewer's roster, so it carries
 *  no tone: green and red are kept for a move's shift (docs/design_system.md,
 *  "Colour"). The shade still says how far from a coin toss it is. */
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

/** The bid, the bucket it came from, what capped it, and what a dollar
 *  costs you. The "see why" version: everything checkable. */
function bidNote(bid) {
  if (!bid) return "";
  if (!bid.sample) return `bid: nothing to go on (${escape(bid.note)})`;
  const capped = bid.capped_by ? `, capped by ${escape(bid.capped_by)}` : "";
  const worth =
    bid.ceiling || bid.worth_dollars
      ? ` Worth ~$${bid.worth_dollars} to you, and above $${bid.ceiling} he stops clearing ` +
        `your bar — ${escape(bid.rate_note || "")}`
      : "";
  return (
    `bid $${bid.amount}: the ${escape(bid.basis)} of rank ${escape(bid.bucket)} ` +
    `($${bid.low}–$${bid.high} over ${count(bid.sample, "claim")}${capped}).${worth}`
  );
}

/** The ladder, in one line: what the tool bids, what each dollar wins, and
 *  what the man is worth to this roster. Rungs that landed on the same dollar
 *  are shown once — a cap pulls them together and three copies say nothing. */
function bidLadder(bid) {
  if (!bid || !bid.sample || !bid.ladder || !bid.ladder.length) return "";
  const parts = [`bid $${bid.amount}`];
  let last = null;
  for (const rung of bid.ladder) {
    if (rung.amount === last) continue;
    last = rung.amount;
    parts.push(`$${rung.amount} wins ~${Math.round(rung.win_chance * 100)}%`);
  }
  parts.push(`worth ~$${bid.worth_dollars} to you`);
  return parts.join(" &middot; ");
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
   Light and dark are both first-class (docs/design_system.md). Until the
   viewer chooses, the page follows the system's own preference; the switch
   in the rail makes a choice, which is kept per viewer in localStorage,
   wrapped, so a private window merely forgets it. Each page's <head>
   applies a kept choice before anything is drawn, so a dark reader is never
   flashed with the light page first. */

const THEME_KEY = "fcp-theme";

function readTheme() {
  try {
    const kept = window.localStorage.getItem(THEME_KEY);
    return kept === "light" || kept === "dark" ? kept : null;
  } catch (error) {
    return null;
  }
}

const systemDark = () =>
  Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);

/** The theme on the screen now: the viewer's choice, else the system's. */
const shownTheme = () => document.documentElement.dataset.theme || (systemDark() ? "dark" : "light");

/** Say on the switch which theme is showing. The rail's switch names it;
 *  the signed-out pages' older switch says what a press would do. */
function labelTheme() {
  const button = $("theme");
  if (!button) return;
  const theme = shownTheme();
  button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  const state = button.querySelector(".ws-theme-state");
  if (state) state.textContent = theme === "dark" ? "Dark" : "Light";
  else button.textContent = theme === "light" ? "Lights down" : "Lights up";
}

function setTheme(value) {
  const theme = value === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  labelTheme();
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    /* private window */
  }
}

function startTheme() {
  const kept = readTheme();
  if (kept) document.documentElement.dataset.theme = kept;
  else delete document.documentElement.dataset.theme;
  labelTheme();
  const button = $("theme");
  if (button) button.onclick = () => setTheme(shownTheme() === "dark" ? "light" : "dark");
  if (window.matchMedia) {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    if (query.addEventListener) query.addEventListener("change", labelTheme);
  }
}

/* ---- the workstation's shared pieces -----------------------------------
   The components a page builds from once it is migrated
   (docs/design_system.md): the category glyph scale, the FIT cell, the
   screener table and a move in the owner's format. None of them works a
   number out; each is handed a figure the page already holds and draws it. */

/** A negative in the new components carries a true minus sign. */
const minus = (text) => String(text).replace(/^-/, "−");

/** The glyph scale's steps, in probability: a change under half a point
 *  is no change worth a mark (the same half point The read and the strip
 *  have always used), and five points or more is a large one. */
const GLYPH_STEPS = [0.005, 0.05];

/** A change to the viewer's own roster as one or two characters:
 *  ++ large gain, + gain, · nothing worth a mark, − cost, −− large cost. */
function glyph(delta, steps) {
  const [small, large] = steps || GLYPH_STEPS;
  if (!isNum(delta) || Math.abs(delta) < small) return { mark: "·", cls: "z", word: "no change" };
  const up = delta > 0;
  const big = Math.abs(delta) >= large;
  return {
    mark: up ? (big ? "++" : "+") : big ? "−−" : "−",
    cls: `${up ? "p" : "n"}${big ? 2 : 1}`,
    word: `${big ? "large " : ""}${up ? "gain" : "cost"}`,
  };
}

/** The glyph as a cell: the mark for the eye, the words for a reader. */
function glyphHtml(delta, said, steps) {
  const g = glyph(delta, steps);
  const words = said || g.word;
  return (
    `<span class="ws-g ${g.cls}" title="${escape(words)}" aria-hidden="true">${g.mark}</span>` +
    `<span class="sr">${escape(words)}</span>`
  );
}

/** What the marks mean, in the unit they were cut in. */
function glyphLegend(unit, steps) {
  const [small, large] = steps || GLYPH_STEPS;
  const at = (v) => `${+(v * 100).toFixed(1)}`;
  return (
    `<span><span class="ws-g p2">++</span> ${at(large)}+ ${unit} better</span>` +
    `<span><span class="ws-g p1">+</span> ${at(small)}–${at(large)} better</span>` +
    `<span><span class="ws-g z">·</span> under ${at(small)}</span>` +
    `<span><span class="ws-g n1">−</span> ${at(small)}–${at(large)} worse</span>` +
    `<span><span class="ws-g n2">−−</span> ${at(large)}+ worse</span>` +
    `<span class="faint">green and red are changes to your roster, not grades of a player</span>`
  );
}

/** FIT: a figure for this roster, with a bar as long as its share of the
 *  largest in its column. A figure under half a hundredth is grey. */
function fitHtml(value, largest, places) {
  if (!isNum(value)) return `<span class="ws-fit">${dash}</span>`;
  const tone = Math.abs(value) < 0.005 ? "" : value > 0 ? "pos" : "neg";
  const share = isNum(largest) && largest > 0 ? Math.min(100, (Math.abs(value) / largest) * 100) : 0;
  return (
    `<span class="ws-fit ${tone}"><i style="--w:${share.toFixed(0)}%"></i>` +
    `<b>${minus(signed(value, places === undefined ? 2 : places))}</b></span>`
  );
}

/** A name in a screener row: the row's own control, which inspects it. */
const rowNameHtml = (key, text) =>
  `<button type="button" class="ws-rowname" data-row="${escape(key)}" aria-haspopup="dialog">` +
  `${escape(text)}</button>`;

/**
 * The screener table: dense rows, a sticky header, sortable columns, columns
 * that can be put away (kept per viewer), and a row that opens the drawer.
 *
 * @param host   the element it is drawn into
 * @param spec   {id, caption, rows, key(row), columns, sort: [key, "desc"|"asc"],
 *                onSelect(row, trigger), legend (html), height (css length)}
 *               A column is {key, label, title, align: "l"|"c"|"", text: bool,
 *               value(row) for sorting, cell(row) -> html, hidden: bool, lock: bool}.
 * @returns      {draw, rows(list), clear()}
 */
function screener(host, spec) {
  const storeKey = `fcp-cols-${spec.id}`;
  let hidden = new Set(spec.columns.filter((c) => c.hidden).map((c) => c.key));
  try {
    const kept = JSON.parse(window.localStorage.getItem(storeKey) || "null");
    if (Array.isArray(kept)) hidden = new Set(kept);
  } catch (error) {
    /* nothing kept: the columns start as the page set them */
  }
  let rows = spec.rows || [];
  let [sortKey, sortDir] = spec.sort || [null, "desc"];
  let selected = null;

  const shown = () => spec.columns.filter((c) => c.lock || !hidden.has(c.key));
  const sorted = () => {
    const column = spec.columns.find((c) => c.key === sortKey);
    if (!column || !column.value) return rows;
    const sign = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = column.value(a);
      const y = column.value(b);
      if (typeof x === "string" || typeof y === "string") {
        return sign * String(x).localeCompare(String(y));
      }
      const xs = isNum(x) ? x : -Infinity;
      const ys = isNum(y) ? y : -Infinity;
      return sign * (xs - ys);
    });
  };

  function draw() {
    const columns = shown();
    const head = columns
      .map((c) => {
        const cls = [c.align || ""].filter(Boolean).join(" ");
        const sort = c.key === sortKey ? ` aria-sort="${sortDir === "asc" ? "ascending" : "descending"}"` : "";
        const label = escape(c.label);
        const title = c.title ? ` title="${escape(c.title)}"` : "";
        return (
          `<th scope="col" class="${cls}"${sort}${title}>` +
          (c.value ? `<button type="button" data-sort="${escape(c.key)}">${label}</button>` : label) +
          `</th>`
        );
      })
      .join("");
    const body = sorted()
      .map((row) => {
        const key = String(spec.key(row));
        const cells = columns
          .map((c) => {
            const cls = [c.align || "", c.text ? "txt" : ""].filter(Boolean).join(" ");
            return `<td class="${cls}">${c.cell(row)}</td>`;
          })
          .join("");
        const cls = [spec.onSelect ? "pickable" : "", key === selected ? "is-selected" : ""]
          .filter(Boolean)
          .join(" ");
        return `<tr data-key="${escape(key)}" class="${cls}">${cells}</tr>`;
      })
      .join("");
    const toggles = spec.columns
      .filter((c) => !c.lock)
      .map(
        (c) =>
          `<label><input type="checkbox" data-col="${escape(c.key)}"` +
          `${hidden.has(c.key) ? "" : " checked"}> ${escape(c.title || c.label)}</label>`,
      )
      .join("");
    host.innerHTML =
      `<div class="ws-scr">` +
      `<div class="ws-scr-tools"><span class="ws-scr-cap">${escape(spec.caption)}</span>` +
      `<span class="ws-scr-n">${count(rows.length, "row")}</span>` +
      (toggles
        ? `<details class="ws-disc ws-cols"><summary>Columns</summary>` +
          `<div class="ws-cols-list">${toggles}</div></details>`
        : "") +
      `</div>` +
      `<div class="ws-scr-frame" style="--scr-h:${escape(spec.height || "480px")}" ` +
      `tabindex="0" role="region" aria-label="${escape(spec.caption)}">` +
      `<table class="ws-table"><caption class="sr">${escape(spec.caption)}</caption>` +
      `<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>` +
      (spec.legend ? `<p class="ws-legend">${spec.legend}</p>` : "") +
      `</div>`;
    wire();
  }

  function wire() {
    host.querySelectorAll("th button[data-sort]").forEach((button) => {
      button.onclick = () => {
        const key = button.dataset.sort;
        sortDir = key === sortKey && sortDir === "desc" ? "asc" : "desc";
        sortKey = key;
        draw();
        const again = host.querySelector(`th button[data-sort="${CSS.escape(key)}"]`);
        if (again) again.focus();
      };
    });
    host.querySelectorAll("input[data-col]").forEach((box) => {
      box.onchange = () => {
        if (box.checked) hidden.delete(box.dataset.col);
        else hidden.add(box.dataset.col);
        try {
          window.localStorage.setItem(storeKey, JSON.stringify([...hidden]));
        } catch (error) {
          /* private window: the choice lasts as long as the page */
        }
        const open = true;
        draw();
        const cols = host.querySelector(".ws-cols");
        if (cols) cols.open = open;
        const again = host.querySelector(`input[data-col="${CSS.escape(box.dataset.col)}"]`);
        if (again) again.focus();
      };
    });
    if (!spec.onSelect) return;
    host.querySelectorAll("tbody tr").forEach((tr) => {
      tr.onclick = (event) => {
        // A control of the row's own (a pick, a card) does its own thing.
        const control = event.target.closest("button, a, input, select, label");
        if (control && !control.classList.contains("ws-rowname")) return;
        const row = rows.find((each) => String(spec.key(each)) === tr.dataset.key);
        if (!row) return;
        select(tr.dataset.key);
        spec.onSelect(row, tr.querySelector(".ws-rowname") || tr);
      };
    });
  }

  function select(key) {
    selected = key;
    host.querySelectorAll("tbody tr").forEach((tr) => {
      tr.classList.toggle("is-selected", tr.dataset.key === key);
    });
  }

  draw();
  return {
    draw,
    rows(next) {
      rows = next || [];
      draw();
    },
    clear() {
      select(null);
    },
  };
}

/** A category's change in chance, in points: "+3 pts". */
const points = (delta) => `${minus(signed(delta * 100, 0))} pts`;

/** ESPN's injury status as the few letters a tag has room for. */
const STATUS_MARKS = {
  QUESTIONABLE: "Q",
  DOUBTFUL: "D",
  PROBABLE: "P",
  OUT: "OUT",
  DAY_TO_DAY: "DTD",
  INJURY_RESERVE: "IR",
  SUSPENSION: "SUSP",
};
const statusMark = (status) => {
  const key = String(status || "").toUpperCase();
  return STATUS_MARKS[key] || key.replace(/_/g, " ").slice(0, 4) || "";
};

/** "BKN · SF": a man's team and position, the mark this site uses for him. */
const bbMark = (player) =>
  [player && player.pro_team, player && player.position].filter(Boolean).join(" · ");

/**
 * One move from a week report, in the owner's format: the man and the
 * number, then DROP, GAIN and COST, then the facts and Inspect. GAIN and
 * COST are the categories whose chance the move changes by half a point or
 * more, in points (`move.moved`, the report's own). The label is the bar's
 * and it hides nothing: a move under the bar is drawn the same way.
 *
 * @param move   a `recommended` or `moves` entry of `pickups/stream`
 * @param report the report it came from, for the bar
 */
function moveBlockHtml(move, report) {
  const add = move.add || {};
  const gains = (move.moved || []).filter((s) => s.delta >= 0.005);
  const costs = (move.moved || []).filter((s) => s.delta <= -0.005);
  const shifts = (list) =>
    list.length
      ? list
          .map(
            (s) =>
              `<span>${escape(s.abbreviation)} <span class="ws-data ${s.delta > 0 ? "ws-pos" : "ws-neg"}">` +
              `${points(s.delta)}</span></span>`,
          )
          .join("")
      : `<span class="faint">none by half a point</span>`;
  const out = move.drop
    ? [
        "Drop",
        `${cardName(move.drop.espn_player_id, escape(move.drop.name))}` +
          ` <span class="ws-bb">${escape(bbMark(move.drop))}</span>`,
      ]
    : move.to_ir
      ? ["To IR", cardName(move.to_ir.espn_player_id, escape(move.to_ir.name))]
      : ["Into", "the open place"];
  const bid = move.bid && move.bid.sample ? `bid $${move.bid.amount}` : "";
  const facts = [
    `${move.add_starts} of ${add.games_remaining} games left`,
    bid,
    move.fills_empty_day ? "fills an empty day" : "",
  ].filter(Boolean);
  const label = move.clears_hurdle
    ? `<span class="ws-tag accent">${escape(WORTH_A_LOOK.toLowerCase())}</span>`
    : `<span class="ws-tag">under the ${fixed(report ? report.hurdle : null, 2)} bar</span>`;
  const tone = move.net >= 0.005 ? "ws-pos" : move.net <= -0.005 ? "ws-neg" : "";
  return (
    `<article class="ws-move">` +
    `<div class="ws-move-head"><span class="ws-move-name">` +
    `${cardName(add.espn_player_id, escape(add.name))}</span>` +
    `<span class="ws-bb">${escape(bbMark(add))}</span>` +
    `<span class="ws-move-net ${tone}">${minus(signed(move.net, 2))}<small>CATEGORIES</small></span>` +
    `</div>` +
    `<dl class="ws-move-rows">` +
    `<dt>${escape(out[0].toUpperCase())}</dt><dd>${out[1]}</dd>` +
    `<dt>GAIN</dt><dd>${shifts(gains)}</dd>` +
    `<dt>COST</dt><dd>${shifts(costs)}</dd>` +
    `</dl>` +
    `<div class="ws-move-foot"><span class="ws-bb">${escape(facts.join(" · "))}</span>${label}` +
    `<button type="button" class="ws-btn sm quiet" data-inspect="move">Inspect →</button></div>` +
    `</article>`
  );
}

/* ---- the nine as three bands ------------------------------------------
   The Matchup page's THE NINE, and the Overview's MATCHUP, drawn by this
   one piece of code so the two cannot drift (moved here from week.html).

   Where the nine are split for the eye. A display choice, not a model claim:
   every cell prints its own chance, and nothing in the report knows these
   numbers exist. */
const BAND_YOURS = 0.65;
const BAND_THEIRS = 0.35;

/** Which band a chance falls in. An unknown chance is a swing: it is the
 *  band that says "this is not settled", which is true of a missing one. */
const bandOf = (p) => (!isNum(p) ? "swing" : p >= BAND_YOURS ? "yours" : p <= BAND_THEIRS ? "theirs" : "swing");

const BAND_WORDS = {
  yours: ["Likely yours", `at or over ${Math.round(BAND_YOURS * 100)}%`],
  swing: ["Swing", `${Math.round(BAND_THEIRS * 100)}–${Math.round(BAND_YOURS * 100)}%`],
  theirs: ["Likely theirs", `at or under ${Math.round(BAND_THEIRS * 100)}%`],
};

/** A bye: the schema carries no `on_bye`, only an opponent that is null. */
const onBye = (report) => report.opponent_espn_team_id === null;

/* ---- the score as it stands ---------------------------------------------
   `posted` and `opponent_posted` are the engine's own posted-so-far
   (`app/pickups/state.py`), not the matchup route's stored tallies, and the
   difference is worth knowing: ESPN's row is the whole period's running
   total as of the last ingest with no day column to cap it by, while the
   engine's is what had been played by the morning of the day the page is
   asked about. On a replayed day the two are wildly different -- the route
   shows the finished week -- and a page that drew the score from one and
   the chance from the other would contradict itself in public. So both come
   from the report, and "how this is worked out" says which.

   Before any game of the period has been played the score is a dash on both
   sides, because 0–0 reads as a result and a dash reads as "not yet". An
   answer with no `posted` at all (the glance, which carries the chances and
   not the score) reads the same way: a dash until the week report lands. */

/** Whether either side has posted anything at all this period. */
function anyPosted(report) {
  const some = (totals) =>
    Boolean(totals) && Object.keys(totals).some((key) => isNum(totals[key]) && totals[key] !== 0);
  return some(report.posted) || some(report.opponent_posted);
}

/** The score in one category, ours first, the leading side in ink.
 *
 *  Which side leads is read with the category's own direction, so in TO the
 *  lower figure takes the ink; the order is never reversed, and no word is
 *  added either way -- the chance above already says who is ahead. */
function scoreCell(report, cat) {
  if (!anyPosted(report) || onBye(report)) return `<span class="now">${dash}</span>`;
  const us = totalOf(report.posted, cat);
  const them = totalOf(report.opponent_posted, cat);
  let ours = "";
  let theirs = "";
  if (isNum(us) && isNum(them) && Math.abs(us - them) > 1e-9) {
    const weLead = INVERTED.has(cat) ? us < them : us > them;
    ours = weLead ? "lead" : "trail";
    theirs = weLead ? "trail" : "lead";
  }
  return (
    `<span class="now"><b class="${ours}">${storedCat(cat, us)}</b>` +
    `<i>&ndash;</i><b class="${theirs}">${storedCat(cat, them)}</b></span>`
  );
}

/** One band: its words, then a cell a category -- the chance, and the score
 *  as it stands under it. On the Matchup page each cell is a button that
 *  opens both sides' totals; `still` draws the same cells as cells, for a
 *  page with nothing behind them to open. */
function bandHtml(which, cats, report, still) {
  const [word, range] = BAND_WORDS[which];
  const probabilities = report.probabilities || {};
  const cells = cats
    .map((cat) => {
      const p = probabilities[cat];
      const inner = `<span class="lab">${escape(cat)}</span><b>${pct(p)}</b>` + scoreCell(report, cat);
      return still
        ? `<div class="cell">${inner}</div>`
        : `<button type="button" data-cat="${escape(cat)}" aria-expanded="false" ` +
            `aria-controls="cat-detail">${inner}</button>`;
    })
    .join("");
  return (
    `<div class="band ${which}">` +
    `<p class="blab">${escape(word)} <span class="n">${escape(range)}</span></p>` +
    (cats.length
      ? `<div class="pulse">${cells}</div>`
      : `<p class="empty bandempty">None this week.</p>`) +
    `</div>`
  );
}

/** The nine in their three bands, swing first: the markup, and how many are
 *  in the balance (the section's tag on both pages). */
function bandsHtml(report, still) {
  const probabilities = report.probabilities || {};
  const grouped = { yours: [], swing: [], theirs: [] };
  CATS.forEach((cat) => grouped[bandOf(probabilities[cat])].push(cat));
  return {
    swing: grouped.swing.length,
    html:
      bandHtml("swing", grouped.swing, report, still) +
      bandHtml("yours", grouped.yours, report, still) +
      bandHtml("theirs", grouped.theirs, report, still),
  };
}

/* ---- tonight ------------------------------------------------------------
   A man of tonight as the Matchup page's Tonight draws him, and as the
   Overview's TONIGHT does, out of the same functions (moved here from
   week.html). Every name is a trigger for the shared player card
   (`cardName` and `wireCards`, shell.js); each block calls `wireCards`
   after its own innerHTML, because the card is attached to elements and
   not delegated from the document. */

/** A tip-off in the reader's own zone. The payload carries the moment, never
 *  a clock, because the same report is read in three of them. */
function tipOff(iso) {
  if (!iso) return "";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

const playerName = (player) =>
  player ? cardName(player.espn_player_id, `<b>${escape(player.name)}</b>`) : dash;

/** "at MIL", or "no game" in the muted style. */
const gameCell = (player) =>
  player && player.game
    ? `<span class="mono">${escape(player.game.describe)}</span>`
    : `<span class="faint">no game</span>`;

/** A flag beside a name: injured, on injured reserve, and the return date. */
function statusCell(player) {
  if (!player) return "";
  if (player.status === "ir") return `<span class="flag">injured reserve</span>`;
  if (player.status !== "injured") return "";
  const back = player.expected_return_date;
  return (
    `<span class="flag">${escape(player.injury_status || "injured")}` +
    `${back ? `, back ${dayName(back)}` : ""}</span>`
  );
}

/** What ESPN says about a man tonight, as a word and a tone. `status` and
 *  `plays` are the payload's; nothing here decides anything about him. */
function standing(player) {
  if (player.status === "ir") return ["injured reserve", "out"];
  if (player.injury_status) {
    const word = String(player.injury_status).replace(/_/g, " ").toLowerCase();
    if (word === "active") return player.plays ? ["active", "on"] : ["no game", ""];
    return [word, word === "out" || word === "suspension" ? "out" : "doubt"];
  }
  if (!player.plays) return player.game ? ["out", "out"] : ["no game", ""];
  return ["active", "on"];
}

/* ---- a man's line -------------------------------------------------------
   The stored box score, once the ingest has it: minutes, then the nine in
   the page's own fixed order (`CATS`), so a reader never has to find PTS
   in two places on one screen. Both sides use it -- a box score is a
   league-visible fact and not a plan -- and both get it from a field of the
   same name, ours on the day's report and theirs on the stored lineup row.

   A zero is kept. A zero is information: three blocks and no blocks are
   different nights, and a line that printed only what a man did would make
   0 for 6 look like a night off. */
function boxLine(line) {
  if (!line) return "";
  const n = (v) => (isNum(v) ? String(Math.round(v)) : dash);
  return [
    `${n(line.minutes)} min`,
    `${n(line.field_goals_made)}/${n(line.field_goals_attempted)} fg`,
    `${n(line.free_throws_made)}/${n(line.free_throws_attempted)} ft`,
    `${n(line.three_pointers_made)} 3pm`,
    `${n(line.points)} pts`,
    `${n(line.rebounds)} reb`,
    `${n(line.assists)} ast`,
    `${n(line.steals)} stl`,
    `${n(line.blocks)} blk`,
    `${n(line.turnovers)} to`,
  ].join(" · ");
}

/** The line as a row of its own, or nothing at all when the ingest has not
 *  reached the day. Nothing at all and not "no line yet": the game mark on
 *  the row above already says a game is coming. */
const boxRow = (line) =>
  line ? `<span class="box">${escape(boxLine(line))}</span>` : "";

/** One man of tonight: his name, his mark and his game, and his standing. */
function tonightRow(player, extra) {
  const [word, tone] = standing(player);
  const mark = [player.pro_team, player.position].filter(Boolean).join(" · ");
  const game = player.game
    ? [player.game.describe, tipOff(player.game.at)].filter(Boolean).join(" · ")
    : "no game";
  const bits = [
    mark ? `<span class="team">${escape(mark)}</span>` : "",
    escape(game),
    extra || "",
  ].filter(Boolean);
  return (
    `<li class="${tone === "on" ? "" : "off"}">` +
    `<span class="who">${playerName(player)}</span>` +
    `<span class="st ${tone}">${tone === "on" ? "&#9679; " : ""}${escape(word)}</span>` +
    `<span class="meta">${bits.join(" &middot; ")}</span>` +
    boxRow(player.line) +
    `</li>`
  );
}

/** Their side, from the lineups the ingest stored for the day: who they have
 *  in a starting place tonight. A fact, not a projection -- what the page
 *  estimates about them is the totals in the nine, and says so there. */
function theirRow(slot) {
  const hurt = slot.injury_status && String(slot.injury_status).toUpperCase() !== "ACTIVE";
  const word = hurt ? String(slot.injury_status).replace(/_/g, " ").toLowerCase() : "";
  // The lineups route carries the stat line for the day beside the slot,
  // so their men read the same as ours once their games are stored.
  return (
    `<li class="one ${hurt ? "off" : ""}">` +
    `<span class="who"><span class="team">${escape(slot.slot)}</span>` +
    `${cardName(slot.player_id, `<b>${escape(slot.player_name)}</b>`)}</span>` +
    `<span class="st ${hurt ? "doubt" : ""}">${escape(word)}</span>` +
    boxRow(slot.played ? slot : null) +
    `</li>`
  );
}

/* ---- what changed ---------------------------------------------------------
   The league's news as This week's What changed draws it, and the
   Overview's RECENT (moved here from league-week.html). Every sentence is
   the API's (app/inseason/changes.py), the same one the digest sends; the
   page groups and marks it and works nothing out. */

/** The one word that labels a line, in the house's vocabulary rather than
 *  the feed's field names. */
const CHANGE_WORDS = {
  status: "injury",
  minutes: "minutes",
  ownership: "owned",
  add: "add",
  claim: "claim",
  drop: "drop",
  trade: "trade",
  waiver_clear: "waivers",
  lineup: "roster",
};

/** A moment as the feed prints one: the hour and minute, UTC. */
function utcTime(iso) {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return dash;
  return when.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

/** The API's sentence with each player's name turned into the shared card's
 *  trigger (`cardName` in shell.js), so a name in the feed opens the same
 *  card a name in a table does. The sentence itself is never rebuilt: the
 *  name is found in it and only that run of characters is replaced, so what
 *  the reader sees is still the API's words. A name the API did not put in
 *  the sentence, or a player it has no ESPN id for, is simply left alone. */
function withPlayers(change) {
  let html = escape(change.text);
  for (const person of change.players || []) {
    const mark = escape(person.name || "");
    if (!mark || !person.espn_player_id) continue;
    const at = html.indexOf(mark);
    if (at < 0) continue;
    html =
      html.slice(0, at) +
      cardName(person.espn_player_id, mark, "inline") +
      html.slice(at + mark.length);
  }
  return html;
}

function changeHtml(change) {
  const whose = change.mine ? " ours" : change.opponent ? " theirs" : "";
  return (
    `<li class="change${whose}${change.severity === 0 ? " urgent" : ""}">` +
    `<span class="when">${utcTime(change.at)}</span>` +
    `<span class="what">${withPlayers(change)}</span>` +
    `<span class="kind">${escape(CHANGE_WORDS[change.kind] || change.kind)}</span>` +
    `</li>`
  );
}

/** The feed as days, newest first. Grouping is by the UTC date the API
 *  stamped, which is the date its window was drawn on. */
function changedHtml(items) {
  const days = [];
  for (const change of items) {
    const day = String(change.at).slice(0, 10);
    if (!days.length || days[days.length - 1].day !== day) days.push({ day, items: [] });
    days[days.length - 1].items.push(change);
  }
  return days
    .map(
      (group) =>
        `<h3 class="dayhead">${dayName(group.day)}</h3>` +
        `<ul class="feed">${group.items.map(changeHtml).join("")}</ul>`,
    )
    .join("");
}

/** The page could not be drawn: say what happened, in the page, not the console. */
function fail(message) {
  const where = $("failed");
  if (!where || REFUSED) return;
  where.hidden = false;
  where.textContent = message;
}
