/* The shell: the workstation's frame around every page (docs/site.md,
   docs/design_system.md).

     BOX OUT
     ● OVERVIEW
     TEAM    Matchup · Roster · Moves · Trades · Season
     LEAGUE  This week · Standings · Players · Draft · History
     ───
     ◈ SCENARIO   Current scenario · n changes
     ───
     Patriot Games · 2027 ▾        (the league and season switcher)
     Account ▾ · Connections · Alerts · Theme ◐

   A rail down the left on a desk; at a narrower width a top bar whose Menu
   button brings the same rail in over the page. Drawn into <div id="shell">
   from two routes: /auth/me (who this is, his role in each league and his
   claims on teams) and /leagues (his leagues, each with its name and the
   seasons held). Loaded after pages.js, whose `get`, `place`, `escape` and
   URL helpers it uses, and after scenario.js, whose state the scenario bar
   draws.

   * The league is the one in the URL; on a page without one (the account
     pages, the home page) it is the one this browser last looked at, kept
     in localStorage (wrapped: a private window merely forgets it), else his
     first. The switcher lists his leagues, and the seasons of this one.
   * TEAM is there only in a league where he is a verified manager: this
     season's team, or his newest one in the league. Otherwise it is "Claim
     your team", to the claim page. In single mode, where the owner may read
     every team, a league with no claim falls back to the team the context
     route names as ours, which is what the pages always did.
   * Two items have no page of their own yet and say so: Roster opens the
     week page at Tonight, and Players opens it at What if, whose wire is the
     nearest thing to a player screener that exists.
   * The menus are buttons that open a list of links: Enter, Space or the
     arrow keys open one, the arrows move through it, Escape closes it and
     puts the focus back on its button, and it closes when the focus or a
     click goes elsewhere.
   * The inspection drawer is here, and every name's player card opens into
     it (`cardName`, `wireCards`, `openCard`); so does a scenario's account
     of its own numbers.
   * The scenario bar is here, drawn from `SCENARIO` whenever a page has set
     one; today only the week page's What if does.

   `SHELL` is a promise of what the shell found ({me, league, season,
   myTeam, ...}), for a page that wants to know whose team is whose. */

"use strict";

/** The BO symbol (brand/box-out-mark.svg, drawn in generate.py), inline so its
 *  ink follows the theme: the strokes are currentColor, the ball is the
 *  brand's orange. Used at 20 px beside the wordmark; the compact mark is the
 *  rule under 120 px (brand/README.md). */
const MARK =
  '<svg class="bo-mark" viewBox="30 30 980 560" aria-hidden="true" focusable="false"><g fill="none" stroke="currentColor" stroke-width="80" stroke-linejoin="round" stroke-linecap="round"><path d="M80 80V540"/><path d="M80 80H270Q340 80 340 150V240Q340 310 270 310H80"/><path d="M80 310H300Q380 310 380 390V460Q380 540 300 540H80"/><circle cx="730" cy="310" r="230"/></g><circle cx="730" cy="310" r="165" fill="#F26A1B"/><g fill="none" stroke="currentColor" stroke-width="16"><path d="M730 145V475"/><path d="M565 310H895"/><path d="M610 180Q680 310 610 440"/><path d="M850 180Q780 310 850 440"/></g></svg>';


/** Where the last league looked at is kept, per browser. */
const LEAGUE_KEY = "fcp-league";

/** The team's pages, in the rail's order: [key, word, the page it opens,
 *  where on it, whether that page is its own yet]. */
const TEAM_SECTIONS = [
  ["week", "Matchup", "week", "", true],
  ["roster", "Roster", "week", "#tonight-section", false],
  ["moves", "Moves", "moves", "", true],
  ["trades", "Trades", "trades", "", true],
  ["season", "Season", "season", "", true],
];
const LEAGUE_SECTIONS = [
  ["week", "This week", "week", true],
  ["standings", "Standings", "standings", true],
  ["players", "Players", null, false],
  ["draft", "Draft", "draft", true],
  ["history", "History", "history", true],
];
const ACCOUNT_SECTIONS = [
  ["connections", "Connections"],
  ["projections", "Projections"],
  ["alerts", "Alerts"],
];

function rememberedLeague() {
  try {
    const kept = window.localStorage.getItem(LEAGUE_KEY);
    return kept ? Number(kept) : null;
  } catch (error) {
    return null;
  }
}

function rememberLeague(id) {
  try {
    window.localStorage.setItem(LEAGUE_KEY, String(id));
  } catch (error) {
    /* private window: the next visit starts from the first league */
  }
}

/** The frame, drawn at once so the name and the theme switch are there
 *  before any fetch returns; the links are filled in when /auth/me answers. */
function shellFrame() {
  const host = $("shell");
  if (!host) return;
  document.body.classList.add("ws");
  host.innerHTML =
    `<header class="ws-top">` +
    `<a class="ws-brand" href="/">${MARK}<span>{{brand}}</span></a>` +
    `<span class="ws-where" id="ws-where"></span>` +
    `<button type="button" class="ws-btn sm" id="ws-menu" aria-controls="ws-rail" ` +
    `aria-expanded="false">Menu</button></header>` +
    `<nav class="ws-rail" id="ws-rail" aria-label="Site">` +
    `<div class="ws-rail-head">` +
    `<a class="ws-brand" href="/" aria-label="{{brand}}, home">${MARK}<span>{{brand}}</span></a>` +
    `<button type="button" class="ws-btn sm quiet ws-rail-close" id="ws-rail-close">Close</button>` +
    `</div>` +
    `<div class="ws-rail-body" id="shell-main"></div>` +
    `<div class="ws-rail-foot"><div id="shell-account"></div>` +
    `<button type="button" class="ws-nav ws-theme" id="theme" aria-pressed="false" ` +
    `title="Light or dark; follows your system until you choose">` +
    `Theme <span class="ws-theme-state">Light</span> <span aria-hidden="true">◐</span>` +
    `</button></div>` +
    `</nav>` +
    `<div class="ws-scn-bar" id="ws-scn-bar" role="region" aria-label="Scenario" hidden></div>`;
  wirePhoneNav();
}

/** `aria-current`: "page" for a link to this very page, "true" for the
 *  current one of a set whose link goes elsewhere (the league in the
 *  switcher, which opens its newest season). */
const current = (yes, what) => (yes ? ` aria-current="${what || "page"}"` : "");

/** One menu: a button and the list it opens, in place. */
function menuHtml(id, label, items) {
  return (
    `<div class="ws-menu" data-menu>` +
    `<button type="button" class="ws-menu-btn" id="${id}-btn" ` +
    `aria-expanded="false" aria-controls="${id}-list">` +
    `${label}<span class="ws-caret" aria-hidden="true">▾</span></button>` +
    `<ul class="ws-menu-list" id="${id}-list" hidden>${items.join("")}</ul></div>`
  );
}

const itemHtml = (href, text, on, what) =>
  `<li><a href="${escape(href)}"${current(on, what)}>${text}</a></li>`;
const groupHtml = (text) => `<li class="ws-mgroup" role="presentation">${text}</li>`;

/** A link in the rail. A page with none of its own yet says "soon" and
 *  names, in its title, the page it opens instead. */
const navHtml = (href, text, on, pending) =>
  `<li><a class="ws-nav" href="${escape(href)}"${current(on)}` +
  `${pending ? ` title="${escape(pending)}"` : ""}>${text}` +
  `${pending ? `<span class="ws-soon">soon</span>` : ""}</a></li>`;

/** The team a viewer manages in this league: this season's, else his newest. */
function managedTeam(league, season) {
  const verified = (league.teams || []).filter((t) => t.state === "verified");
  const here = verified.find((t) => t.season === season);
  if (here) return here;
  return verified.sort((a, b) => b.season - a.season)[0] || null;
}

function leagueMenu(ctx, where) {
  const { league, season, leagues } = ctx;
  if (!league) {
    return menuHtml("league", `<span class="ws-lname">No league yet</span>`, [
      itemHtml("/account/connections", "Connect a league", false),
      groupHtml("Or open the invite link a league's owner sent you."),
    ]);
  }
  const onLeaguePage = LEAGUE_SECTIONS.some(([key, , page]) => page && key === where.section);
  const section = onLeaguePage ? where.section : "week";
  const items = [groupHtml("Your leagues")];
  leagues.forEach((each) => {
    const newest = each.seasons.length ? each.seasons[each.seasons.length - 1] : null;
    const name = escape(each.name || `League ${each.id}`);
    items.push(
      newest === null
        ? groupHtml(`${name}: waiting for its first ingest`)
        : itemHtml(leagueUrl(each.id, newest, section), name, each.id === where.league, "true"),
    );
  });
  if (league.seasons.length > 1) {
    items.push(groupHtml("Seasons"));
    league.seasons
      .slice()
      .reverse()
      .forEach((year) => {
        let href = leagueUrl(league.id, year, section);
        if (where.team !== null && where.section && where.section.startsWith("team-")) {
          // A team page's season link goes to that season's team, when he
          // manages one then; otherwise to the league's week.
          const theirs = (league.teams || []).find(
            (t) => t.season === year && t.state === "verified",
          );
          href = theirs
            ? teamUrl(league.id, year, theirs.espn_team_id, where.section.slice(5))
            : leagueUrl(league.id, year, "week");
        }
        items.push(itemHtml(href, String(year), year === season && where.league !== null));
      });
  }
  const label =
    `<span class="ws-lname">${escape(league.name || `League ${league.id}`)}</span>` +
    (season ? ` <span class="ws-season">${season}</span>` : "");
  return menuHtml("league", label, items);
}

/** The rail's body: Overview, the team's pages, the league's, the scenario
 *  and the switcher. `?today=` rides along on every link that means it. */
function railHtml(ctx, where) {
  const { league, season, myTeam, pending } = ctx;
  if (!league || !season) {
    return (
      `<p class="ws-group">League</p>` +
      `<ul class="ws-navlist">${navHtml("/account/connections", "Connect a league", false)}</ul>` +
      `<hr class="ws-sep">${leagueMenu(ctx, where)}`
    );
  }
  const keep = params({ today: where.today, me: where.me });
  const here = where.league === league.id;
  const ours =
    myTeam !== null && where.team === myTeam.espn_team_id && where.season === myTeam.season;
  const teamPage = (page, anchor) =>
    teamUrl(league.id, myTeam.season, myTeam.espn_team_id, page) + keep + (anchor || "");
  const parts = [];

  // Overview is the team's own address (docs/site.md), and the one page it
  // is marked current on. Without a team it is the league's This week, as
  // `/` is, and marks nothing.
  parts.push(
    myTeam
      ? `<a class="ws-nav ws-overview" href="${escape(teamPage("overview"))}"` +
          `${current(ours && where.section === "team-overview")}>` +
          `<span class="ws-dot"></span>Overview</a>`
      : `<a class="ws-nav ws-overview" href="${escape(leagueUrl(league.id, season, "week") + keep)}">` +
          `<span class="ws-dot"></span>Overview</a>`,
  );

  if (myTeam) {
    parts.push(
      `<p class="ws-group">Team <span class="ws-who">${escape(myTeam.name)}` +
        `${myTeam.season !== season ? `, ${myTeam.season}` : ""}</span></p>`,
    );
    parts.push(
      `<ul class="ws-navlist">` +
        TEAM_SECTIONS.map(([key, text, page, anchor, own]) =>
          navHtml(
            teamPage(page, anchor),
            text,
            own && ours && where.section === `team-${key}`,
            own ? "" : `No page of its own yet: opens ${text === "Roster" ? "Tonight" : "What if"} on the week page`,
          ),
        ).join("") +
        `</ul>`,
    );
  } else {
    parts.push(`<p class="ws-group">Team</p>`);
    const text = pending ? "Claim pending" : "Claim your team";
    parts.push(
      `<ul class="ws-navlist">` +
        navHtml(`/pages/claim/${league.id}/${season}`, text, where.section === "claim") +
        `</ul>`,
    );
  }

  parts.push(`<p class="ws-group">League</p>`);
  parts.push(
    `<ul class="ws-navlist">` +
      LEAGUE_SECTIONS.map(([key, text, page]) => {
        if (page) {
          return navHtml(leagueUrl(league.id, season, page) + keep, text, here && where.section === key);
        }
        // Players: no page yet. The wire in What if is the nearest thing.
        const href = myTeam ? teamPage("week", "#whatif-section") : leagueUrl(league.id, season, "week") + keep;
        return navHtml(href, text, false, "No page of its own yet: opens the wire in What if on the week page");
      }).join("") +
      `</ul>`,
  );

  const scenarioHref = myTeam ? teamPage("week", "#whatif-section") : "";
  parts.push(`<hr class="ws-sep">`);
  parts.push(
    `<a class="ws-scn" id="ws-scn"${scenarioHref ? ` href="${escape(scenarioHref)}"` : ""}>` +
      `<span class="ws-scn-mark" aria-hidden="true">◈</span>` +
      `<span class="ws-scn-word">Scenario</span>` +
      `<span class="ws-scn-state" id="ws-scn-state">Baseline · no changes</span></a>`,
  );
  parts.push(`<hr class="ws-sep">`);
  parts.push(leagueMenu(ctx, where));
  return parts.join("");
}

/** The rail's foot: the account menu, and Connections and Alerts as links
 *  of their own. The theme switch is drawn with the frame. */
function accountHtml(ctx, where) {
  const { me } = ctx;
  const items = [groupHtml(escape(me.email))];
  items.push(itemHtml("/account/projections", "Projections", where.section === "projections"));
  items.push(itemHtml("/design", "The design language", where.section === "design"));
  if (me.mode === "single") {
    items.push(groupHtml("Single mode: nobody signs in"));
  } else {
    items.push(
      `<li><form method="post" action="/auth/sign-out">` +
        `<button type="submit">Sign out</button></form></li>`,
    );
  }
  const links = ACCOUNT_SECTIONS.filter(([key]) => key !== "projections")
    .map(([key, text]) =>
      navHtml(`/account/${key}`, text, where.section === key && where.league === null),
    )
    .join("");
  return menuHtml("account", `<span class="ws-lname">Account</span>`, items) +
    `<ul class="ws-navlist">${links}</ul>`;
}

/** Signed out, on the one open page that draws the shell: the way in. */
const signedOutHtml = () =>
  `<p class="ws-group">Signed out</p>` +
  `<ul class="ws-navlist">${navHtml("/sign-in?next=%2Fdesign", "Sign in", false)}</ul>`;

/** The words in the top bar at phone width: where this page is. */
function whereWords(ctx, where) {
  if (where.section === "team-overview") {
    return `Overview${ctx && ctx.myTeam ? ` · ${ctx.myTeam.name}` : ""}`;
  }
  const team = TEAM_SECTIONS.find(([key]) => where.section === `team-${key}`);
  if (team) return `${team[1]}${ctx && ctx.myTeam ? ` · ${ctx.myTeam.name}` : ""}`;
  const league = LEAGUE_SECTIONS.find(([key, , page]) => page && key === where.section);
  if (league) return league[1];
  const account = ACCOUNT_SECTIONS.find(([key]) => key === where.section);
  if (account) return account[1];
  if (where.section === "design") return "The design language";
  if (where.section === "claim") return "Claim your team";
  return "";
}

/* ---- the menus' behaviour ---------------------------------------------- */

function wireMenus(root) {
  root.querySelectorAll("[data-menu]").forEach((menu) => {
    const button = menu.querySelector(".ws-menu-btn");
    const list = menu.querySelector(".ws-menu-list");
    const items = () => Array.from(list.querySelectorAll("a, button"));
    const close = (refocus) => {
      list.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (refocus) button.focus();
    };
    const open = (focus) => {
      closeMenus(menu);
      list.hidden = false;
      button.setAttribute("aria-expanded", "true");
      const all = items();
      if (focus === "first" && all.length) all[0].focus();
      if (focus === "last" && all.length) all[all.length - 1].focus();
    };
    menu.closeMenu = close;
    button.addEventListener("click", () => (list.hidden ? open() : close(false)));
    button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        open("first");
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        open("last");
      } else if (event.key === "Escape" && !list.hidden) {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      }
    });
    list.addEventListener("keydown", (event) => {
      const all = items();
      const at = all.indexOf(document.activeElement);
      const move = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 }[event.key];
      if (move !== undefined && all.length) {
        event.preventDefault();
        all[(move + all.length) % all.length].focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      }
    });
    menu.addEventListener("focusout", (event) => {
      if (!menu.contains(event.relatedTarget)) close(false);
    });
  });
}

/** Close every open menu but `keep`. */
function closeMenus(keep) {
  document.querySelectorAll("[data-menu]").forEach((menu) => {
    if (menu !== keep && menu.closeMenu) menu.closeMenu(false);
  });
}

document.addEventListener("click", (event) => {
  document.querySelectorAll("[data-menu]").forEach((menu) => {
    if (!menu.contains(event.target) && menu.closeMenu) menu.closeMenu(false);
  });
});

/* ---- the rail at phone width ------------------------------------------- */

let navScrim = null;

function setPhoneNav(open, refocus) {
  const button = $("ws-menu");
  document.body.classList.toggle("ws-nav-open", open);
  if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    if (!navScrim) {
      navScrim = document.createElement("div");
      navScrim.className = "ws-scrim";
      navScrim.onclick = () => setPhoneNav(false, true);
    }
    document.body.appendChild(navScrim);
    const first = document.querySelector("#ws-rail a, #ws-rail button");
    if (first) first.focus();
  } else {
    if (navScrim && navScrim.parentNode) navScrim.parentNode.removeChild(navScrim);
    if (refocus && button) button.focus();
  }
}

function wirePhoneNav() {
  const button = $("ws-menu");
  const close = $("ws-rail-close");
  if (button) button.onclick = () => setPhoneNav(!document.body.classList.contains("ws-nav-open"));
  if (close) close.onclick = () => setPhoneNav(false, true);
  window.addEventListener("resize", () => {
    if (window.innerWidth >= 960 && document.body.classList.contains("ws-nav-open")) {
      setPhoneNav(false, false);
    }
  });
}

/* ---- what the shell knows ---------------------------------------------- */

async function shellContext(where) {
  const open = where.section === "design";
  const [me, listed] = await Promise.all([
    get("/auth/me", { signedOutOk: open }),
    get("/leagues", { signedOutOk: open }),
  ]);
  if (!me.ok) return open && me.status === 401 ? { signedOut: true } : null;
  const claims = new Map((me.body.leagues || []).map((l) => [l.espn_league_id, l]));
  // /leagues is the viewer's leagues (every one in single mode); a claim in
  // a league he is no longer a member of opens nothing, so it is not listed.
  const leagues = (listed.ok ? listed.body : []).map((row) => ({
    id: row.espn_league_id,
    name: row.name,
    seasons: row.seasons || [],
    role: claims.has(row.espn_league_id) ? claims.get(row.espn_league_id).role : null,
    teams: claims.has(row.espn_league_id) ? claims.get(row.espn_league_id).teams : [],
  }));
  let league = null;
  if (where.league !== null) {
    league = leagues.find((l) => l.id === where.league) || null;
    if (!league) {
      league = { id: where.league, name: null, seasons: [], role: null, teams: [] };
      leagues.push(league);
    }
  } else {
    const kept = rememberedLeague();
    league =
      leagues.find((l) => l.id === kept && l.seasons.length) ||
      leagues.find((l) => l.seasons.length && managedTeam(l, null)) ||
      leagues.find((l) => l.seasons.length) ||
      null;
  }
  const season =
    where.season !== null
      ? where.season
      : league && league.seasons.length
        ? league.seasons[league.seasons.length - 1]
        : null;
  let myTeam = league ? managedTeam(league, season) : null;
  if (!myTeam && league && season && me.body.mode === "single") {
    const context = await get(
      `/leagues/${league.id}/seasons/${season}/pages/context${params({ today: null, me: where.me })}`,
      { quiet: true },
    );
    const ours = context.ok ? context.body.our_espn_team_id : null;
    if (ours !== null) {
      const team = context.body.teams.find((t) => t.espn_team_id === ours);
      myTeam = { season, espn_team_id: ours, name: team ? team.name : `Team ${ours}`, state: "single" };
    }
  }
  const pending =
    !myTeam && league
      ? (league.teams || []).some((t) => t.season === season && t.state === "pending")
      : false;
  return { me: me.body, mode: me.body.mode, leagues, league, season, myTeam, pending };
}

/** The seasons held for this league, as a row of links to the same page in
 *  each -- a segmented control on a league page's header line. Empty with
 *  fewer than two. */
function seasonLinks(ctx, where) {
  if (!ctx || !ctx.league || ctx.league.seasons.length < 2) return "";
  return ctx.league.seasons
    .slice()
    .reverse()
    .map(
      (year) =>
        `<a href="${leagueUrl(ctx.league.id, year, where.section)}"` +
        `${current(year === where.season)}>${year}</a>`,
    )
    .join("");
}

/** The league's name for a masthead, or its id when none is stored yet. */
const leagueName = (ctx, id) =>
  ctx && ctx.league && ctx.league.name ? ctx.league.name : `League ${id}`;

/* ---- the inspection drawer ----------------------------------------------
   One panel along the right (a sheet from the bottom on a phone) that a
   thing opens into so it can be inspected without leaving the page: a
   player's card, or the account of how a number was worked out. It does
   not trap the page: on a desk the page stays live beside it, and the
   drawer stays until it is closed (the button, or Escape, which puts the
   focus back where it came from). */

const DRAWER = (() => {
  let box = null;
  let scrim = null;
  let trigger = null;
  let onClose = null;
  let key = null;

  function frame() {
    if (box) return box;
    box = document.createElement("aside");
    box.className = "ws-drawer";
    box.id = "ws-drawer";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "false");
    box.setAttribute("aria-labelledby", "ws-drawer-title");
    box.innerHTML =
      `<div class="ws-drawer-head"><div>` +
      `<span class="ws-k" id="ws-drawer-kicker"></span>` +
      `<h2 class="ws-drawer-title" id="ws-drawer-title"></h2>` +
      `<p class="ws-drawer-meta ws-bb" id="ws-drawer-meta"></p></div>` +
      `<button type="button" class="ws-btn sm ws-drawer-x" id="ws-drawer-x">Close</button></div>` +
      `<div class="ws-drawer-body" id="ws-drawer-body"></div>`;
    scrim = document.createElement("div");
    scrim.className = "ws-drawer-scrim";
    document.body.appendChild(box);
    document.body.appendChild(scrim);
    $("ws-drawer-x").onclick = () => close(true);
    scrim.onclick = () => close(true);
    return box;
  }

  function fill(spec) {
    $("ws-drawer-kicker").textContent = spec.kicker || "";
    $("ws-drawer-title").textContent = spec.title || "";
    $("ws-drawer-meta").innerHTML = spec.meta || "";
    $("ws-drawer-meta").hidden = !spec.meta;
    $("ws-drawer-body").innerHTML = spec.body || "";
    wireCards($("ws-drawer-body"));
  }

  /** Open it on `spec` = {key, kicker, title, meta (html), body (html),
   *  trigger, onClose, focus}. The focus moves into it unless `focus` is
   *  false (a drawer following the pointer must not steal it). */
  function open(spec) {
    frame();
    if (trigger && trigger !== spec.trigger) trigger.setAttribute("aria-expanded", "false");
    const previous = onClose;
    onClose = null;
    if (previous && spec.key !== key) previous();
    key = spec.key || null;
    trigger = spec.trigger || null;
    onClose = spec.onClose || null;
    if (trigger && trigger.setAttribute) trigger.setAttribute("aria-expanded", "true");
    fill(spec);
    box.classList.add("on");
    if (spec.focus !== false) $("ws-drawer-x").focus({ preventScroll: true });
  }

  /** New contents for the drawer that is open, if it is still `forKey`'s. */
  function update(forKey, spec) {
    if (!box || key !== forKey) return;
    fill(spec);
  }

  function close(refocus) {
    if (!box || !box.classList.contains("on")) return;
    box.classList.remove("on");
    const was = trigger;
    if (was && was.setAttribute) was.setAttribute("aria-expanded", "false");
    trigger = null;
    key = null;
    const after = onClose;
    onClose = null;
    if (after) after();
    if (refocus && was && was.focus && document.contains(was)) was.focus();
  }

  return {
    open,
    update,
    close,
    isOpen: () => Boolean(box && box.classList.contains("on")),
    keyOf: () => key,
  };
})();

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (document.body.classList.contains("ws-nav-open")) {
    setPhoneNav(false, true);
    return;
  }
  if (DRAWER.isOpen()) DRAWER.close(true);
});

/* ---- a section's account, in the drawer ---------------------------------
   The paragraphs that said how a section's numbers are worked out used to
   sit in the flow under the section. They are kept in the page exactly
   where they were, in an element of class `ws-account` (hidden), and a
   button in the section's label row -- `data-account="that element's id"`
   -- opens them in the drawer as a list of facts: each paragraph under its
   label (`data-k` on the paragraph, else on the account), word for word.
   Nothing is rewritten and nothing is lost; it is one click away instead
   of in the way. Delegated from the document, so a section that redraws
   itself keeps its button. */
function accountSpec(source, trigger) {
  const parts = Array.from(source.children).filter((part) => part.textContent.trim());
  const body = parts
    .map((part) => {
      const label = part.dataset.k || source.dataset.k || "";
      const inner = part.tagName === "P" ? `<p>${part.innerHTML}</p>` : part.innerHTML;
      return `<dt>${escape(label)}</dt><dd>${inner}</dd>`;
    })
    .join("");
  return {
    key: `account-${source.id}`,
    trigger,
    kicker: source.dataset.kicker || "How this is worked out",
    title: source.dataset.title || "",
    body: `<dl class="ws-facts stack">${body}</dl>`,
  };
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-account]");
  if (!trigger) return;
  const source = $(trigger.dataset.account);
  if (!source || !source.textContent.trim()) return;
  event.preventDefault();
  if (DRAWER.isOpen() && DRAWER.keyOf() === `account-${source.id}`) {
    DRAWER.close(false);
    return;
  }
  DRAWER.open(accountSpec(source, trigger));
});

/* ---- a move's working, in the drawer -------------------------------------
   What a move block's Inspect opens (`moveBlockHtml`, pages.js): each figure
   of the move's number with where it came from -- the week report's own
   fields -- and the categories it moves, before and after. /design's move
   and the Overview's THE WIRE open the same drawer from this one function
   (moved here from design.html). */
function inspectMove(move, report, context, trigger) {
  const j = move.judgement || {};
  const rows = [
    ["Net", `<span class="ws-data">${minus(signed(move.net, 3))}</span> categories: this week plus the change in an ordinary week over the weeks left`],
    ["This week", `<span class="ws-data">${minus(signed(j.delta_week, 3))}</span> expected categories in matchup period ${report.matchup_period}`],
    ["A week after", `<span class="ws-data">${minus(signed(j.delta_season_per_week, 3))}</span> over <span class="ws-data">${fixed(j.weeks_remaining, 1)}</span> weeks`],
    ["Record", `<span class="ws-data">${record(j.record_without)}</span> without, <span class="ws-data">${record(j.record_with)}</span> with`],
    ["The bar", `<span class="ws-data">${fixed(report.hurdle, 2)}</span> categories, from ${escape(report.hurdle_source)}: ${escape(report.hurdle_note)}`],
    ["Label", move.clears_hurdle ? "over the bar: worth a look. A label, not advice." : "under the bar, shown in full all the same"],
  ];
  if (move.bid) rows.push(["Bid", bidNote(move.bid)]);
  // The FAAB ladder, as the Matchup page's read shows it: what the tool
  // bids, what each dollar wins, and what the man is worth to this roster.
  const ladder = bidLadder(move.bid);
  if (ladder) rows.push(["Ladder", ladder]);
  rows.push(["The wire", `${escape(wire(report))}, ${count(report.pool_size, "free agent")}`]);
  if (context) rows.push(["Numbers", escape(context.source_note)]);
  const shifts = (move.moved || [])
    .map(
      (s) =>
        `<tr><td class="l">${escape(s.abbreviation)}</td><td>${pct(s.before)}</td><td>${pct(s.after)}</td>` +
        `<td class="${s.delta > 0 ? "ws-pos" : "ws-neg"}">${points(s.delta)}</td></tr>`,
    )
    .join("");
  DRAWER.open({
    key: `move-${move.add.espn_player_id}-${move.drop ? move.drop.espn_player_id : "open"}`,
    trigger,
    kicker: "A move · how this is worked out",
    title: `${move.add.name}${move.drop ? ` for ${move.drop.name}` : ""}`,
    meta: `<b>${escape(bbMark(move.add))}</b> · judged on day ${report.scoring_periods_remaining[0]}`,
    body:
      `<section class="ws-dsec"><span class="ws-k">The number</span><dl class="ws-facts">` +
      rows.map(([k, v]) => `<dt>${escape(k)}</dt><dd>${v}</dd>`).join("") +
      `</dl></section>` +
      (shifts
        ? `<section class="ws-dsec"><span class="ws-k">The categories it moves, this week</span>` +
          `<div class="ws-scr"><table class="ws-table"><thead><tr><th class="l" scope="col">Cat</th>` +
          `<th scope="col">Before</th><th scope="col">After</th><th scope="col">Change</th></tr></thead>` +
          `<tbody>${shifts}</tbody></table></div></section>`
        : "") +
      `<p class="ws-note">From <span class="ws-data">pickups/stream</span>, the week report ` +
      `(docs/pickups.md, section 4.3). Nothing here touches ESPN.</p>`,
  });
}

/* ---- the player card, in the drawer --------------------------------------
   Every page that prints a name hangs the card off it, and the card opens
   into the drawer: a click on a desk, a tap on a phone, Enter from the
   keyboard -- all one click, so the first tap always opens it.

   The card is `GET .../players/{id}/card`, which is the reports' own numbers
   (`app/inseason/card.py`) -- so what it says about a man is what the table
   it was opened from says about him, and the page works nothing out.

   Two triggers, because a roster row is already a button that puts a man in
   a deal and cannot also be the button that opens his card:

     data-card="ESPN id"        a click opens the drawer on him
     data-card-hover="ESPN id"  while the drawer is showing a card, hovering
                                or focusing this one shows his instead; it
                                never opens the drawer by itself

   `cardName(id, text)` is the markup for a name that opens one; call
   `wireCards(root)` after any innerHTML that writes some. `CARD_SCOPE`
   says which league, season and day the card is read in when the page's
   own address does not (the design page). */

/** One fetch per player per page, kept for as long as the page is open. */
const CARDS = new Map();

const CARD_SCOPE = { league: null, season: null, today: null };

const phoneWidth = () => window.matchMedia("(max-width: 700px)").matches;

/** A name that opens a card. `text` defaults to nothing, for a caller that
 *  writes its own markup inside (a badge, a meta line). */
const cardName = (id, text, cls) =>
  `<button type="button" class="pname${cls ? ` ${cls}` : ""}" data-card="${Number(id)}">` +
  `${text === undefined ? "" : text}</button>`;

/** The nine per game, then what the line rests on, then what is wrong. */
function cardBodyHtml(card) {
  if (card === null) return `<div class="pcard"><p class="ws-loading">Not found.</p></div>`;
  if (card.loading) return `<div class="pcard"><p class="ws-loading">Reading his line…</p></div>`;
  const nine = CATS.map(
    (cat) =>
      `<div><span class="ws-k">${escape(cat)}</span><b>${escape(showCat(cat, card.per_game[cat]))}</b></div>`,
  ).join("");
  const facts = [
    ["Games left", `<span class="ws-data">${card.games_left}</span> through day ${card.last_scoring_period}`],
    [
      "Playoff games",
      card.playoff_first === null
        ? "no playoff weeks stored"
        : `<span class="ws-data">${card.playoff_games}</span> in days ` +
          `${card.playoff_first}–${card.playoff_last}`,
    ],
    [
      "Rests on",
      `${count(card.games_so_far, "game")} of his own and ` +
        `${card.had_projection ? "a preseason projection" : "no projection"} ` +
        `(${escape(card.projection_source)})`,
    ],
  ];
  const flags = [];
  if (card.hurt) {
    flags.push(
      escape(String(card.injury_status || "hurt").toLowerCase()) +
        (card.expected_return_date ? `, back ${dayName(card.expected_return_date)}` : ""),
    );
  }
  if (card.thin) flags.push(`thin: ${count(card.games_so_far, "game")} of his own`);
  return (
    `<div class="pcard">` +
    `<section class="ws-dsec"><span class="ws-k">Per game, day ${card.today}</span>` +
    `<div class="ws-nine">${nine}</div></section>` +
    `<section class="ws-dsec"><span class="ws-k">What it rests on</span>` +
    `<dl class="ws-facts">` +
    facts.map(([label, value]) => `<dt>${escape(label)}</dt><dd>${value}</dd>`).join("") +
    `</dl></section>` +
    (flags.length ? `<p class="flag">${flags.join(" · ")}</p>` : "") +
    `</div>`
  );
}

/** The drawer's head for a card: his name, and PHI · SG with the flag. */
function cardSpec(card, name) {
  const meta = card && !card.loading
    ? `<b>${escape(bbMark(card) || "no team stored")}</b>` +
      (card.hurt
        ? ` <span class="ws-tag" title="${escape(String(card.injury_status || "hurt").toLowerCase())}">` +
          `${escape(statusMark(card.injury_status) || "hurt")}</span>`
        : "")
    : "";
  return {
    kicker: "Player",
    title: card && card.name ? card.name : name || "…",
    meta,
    body: cardBodyHtml(card),
  };
}

/** Open the drawer on a man's card, reading it the first time. */
async function openCard(id, trigger, options) {
  const opts = options || {};
  if (!Number.isFinite(id) || id <= 0) return;
  const where = place();
  const league = CARD_SCOPE.league !== null ? CARD_SCOPE.league : where.league;
  const season = CARD_SCOPE.season !== null ? CARD_SCOPE.season : where.season;
  const today = CARD_SCOPE.today !== null ? CARD_SCOPE.today : where.today;
  if (league === null || season === null) return;
  const key = `card-${id}`;
  const name = trigger && trigger.textContent ? trigger.textContent.trim() : "";
  DRAWER.open({
    key,
    trigger,
    focus: opts.focus,
    onClose: opts.onClose,
    ...cardSpec(CARDS.has(id) ? CARDS.get(id) : { loading: true }, name),
  });
  if (CARDS.has(id)) return;
  const query = new URLSearchParams();
  if (today !== null) query.set("today", today);
  const answer = await get(
    `/leagues/${league}/seasons/${season}/players/${id}/card?${query.toString()}`,
    { quiet: true },
  );
  CARDS.set(id, answer.ok ? answer.body : null);
  DRAWER.update(key, cardSpec(CARDS.get(id), name));
}

/** Attach the card to every name in `root`. Safe to call again: the handlers
 *  are set as properties, so a redraw replaces them rather than stacking. */
function wireCards(root) {
  (root || document).querySelectorAll("[data-card],[data-card-hover]").forEach((trigger) => {
    const tappable = trigger.dataset.card !== undefined;
    const id = Number(trigger.dataset.card || trigger.dataset.cardHover);
    if (!tappable) {
      // Hover and focus only follow a drawer that is already showing a
      // card, on a desk; they never open one, so nothing opens by accident
      // and a tap is never read as a second one.
      const follow = () => {
        const showing = String(DRAWER.keyOf() || "");
        if (!phoneWidth() && DRAWER.isOpen() && showing.startsWith("card-") && showing !== `card-${id}`) {
          openCard(id, null, { focus: false });
        }
      };
      trigger.onmouseenter = follow;
      trigger.onfocus = follow;
      return;
    }
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", DRAWER.keyOf() === `card-${id}` ? "true" : "false");
    trigger.onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (DRAWER.isOpen() && DRAWER.keyOf() === `card-${id}`) {
        DRAWER.close(false);
        return;
      }
      openCard(id, trigger);
    };
  });
}

/* ---- the scenario bar ---------------------------------------------------
   Drawn from `SCENARIO` whenever a page has set one (scenario.js): the
   name, the changes in the order they were named, the three numbers the
   change moves -- this week, an ordinary week from here on, a playoff week
   -- the BASELINE | SCENARIO toggle, and Inspect, Reset and Save. A number
   is green when it helps the viewer's own roster and red when it costs it;
   the signs on the changes are what they are (in, out) and carry no colour.
   Save is there and says it cannot yet: there is nowhere to keep one until
   scenarios are global (docs/design_system.md). */

const CHANGE_SIGN = { add: ["+", "Add"], drop: ["−", "Drop"], ir: ["→", "To IR"], trade: ["⇄", "Trade"] };

function effectHtml(label, f) {
  const has = f && isNum(f.value);
  const tone = !has || Math.abs(f.value) < 0.005 ? "" : f.value > 0 ? "ws-pos" : "ws-neg";
  return (
    `<div><dt>${escape(label)}</dt><dd class="${tone}"` +
    `${f && f.note ? ` title="${escape(f.note)}"` : ""}>` +
    `${has ? minus(signed(f.value, 2)) : dash}<small>${escape(f ? f.unit : "")}</small></dd></div>`
  );
}

function scenarioBarHtml(state) {
  const changes = (state.changes || [])
    .map((c) => {
      const [sign, word] = CHANGE_SIGN[c.kind] || ["·", c.kind];
      return (
        `<li><span class="ws-sign" aria-hidden="true">${sign}</span>` +
        `<span class="sr">${escape(word)} </span>${escape(c.name)}` +
        `${bbMark(c) ? ` <span class="ws-bb">${escape(bbMark(c))}</span>` : ""}</li>`
      );
    })
    .join("");
  const view = state.view === "baseline" ? "baseline" : "scenario";
  const judged = state.source && state.source.day ? `judged on day ${state.source.day}` : "";
  return (
    `<div class="ws-scn-id"><span class="ws-k">◈ Scenario${judged ? ` · ${escape(judged)}` : ""}</span>` +
    `<span class="ws-scn-name">${escape(state.name)}</span></div>` +
    `<ul class="ws-chg" aria-label="Changes">${changes}</ul>` +
    `<dl class="ws-eff">` +
    effectHtml("Week", state.effect.week) +
    effectHtml("Season", state.effect.season) +
    effectHtml("Playoffs", state.effect.playoffs) +
    `</dl>` +
    `<div class="ws-scn-act">` +
    `<div class="ws-seg scn" role="group" aria-label="Which roster the page draws">` +
    `<button type="button" data-view="baseline" aria-pressed="${view === "baseline"}">Baseline</button>` +
    `<button type="button" data-view="scenario" aria-pressed="${view === "scenario"}">Scenario</button>` +
    `</div>` +
    `<button type="button" class="ws-btn sm quiet" data-act="inspect" aria-haspopup="dialog">Inspect</button>` +
    `<button type="button" class="ws-btn sm" data-act="reset">Reset</button>` +
    `<button type="button" class="ws-btn sm" data-act="save" disabled ` +
    `title="Saving waits for scenarios that last beyond this page">Save scenario</button>` +
    `</div>`
  );
}

/** The drawer's account of a scenario's numbers: each one with where it
 *  came from, in words, one click from the bar. */
function scenarioInspect(state, trigger) {
  const rows = (state.provenance || [])
    .map(([label, text]) => `<dt>${escape(label)}</dt><dd>${escape(text)}</dd>`)
    .join("");
  DRAWER.open({
    key: "scenario",
    trigger,
    kicker: "Scenario · how this is worked out",
    title: state.name,
    meta: escape(
      (state.changes || []).map((c) => `${(CHANGE_SIGN[c.kind] || ["·"])[0]} ${c.name}`).join("  "),
    ),
    body:
      `<section class="ws-dsec"><span class="ws-k">Each number</span>` +
      `<dl class="ws-facts">${rows}</dl></section>` +
      `<p class="ws-note">Nothing here is stored, and nothing here touches ESPN: the scenario lasts ` +
      `as long as this page, and the manager decides.</p>`,
  });
}

function drawScenario(state) {
  const bar = $("ws-scn-bar");
  const rail = $("ws-scn");
  const said = $("ws-scn-state");
  const on = Boolean(state && state.active);
  if (rail) rail.classList.toggle("on", on && state.view === "scenario");
  if (said) {
    const n = on ? state.changes.length : 0;
    said.textContent = on
      ? `${state.name} · ${count(n, "change")}${state.view === "baseline" ? " · baseline shown" : ""}`
      : "Baseline · no changes";
  }
  if (!bar) return;
  bar.hidden = !on;
  if (!on) {
    bar.innerHTML = "";
    if (DRAWER.keyOf() === "scenario") DRAWER.close(false);
    return;
  }
  bar.innerHTML = scenarioBarHtml(state);
  bar.querySelectorAll("[data-view]").forEach((button) => {
    button.onclick = () => SCENARIO.view(button.dataset.view);
  });
  const inspect = bar.querySelector('[data-act="inspect"]');
  inspect.onclick = () => scenarioInspect(SCENARIO.get(), inspect);
  bar.querySelector('[data-act="reset"]').onclick = () => SCENARIO.reset();
  if (DRAWER.keyOf() === "scenario") scenarioInspect(state, inspect);
}

async function startShell() {
  const where = place();
  shellFrame();
  const main = $("shell-main");
  const account = $("shell-account");
  const whereText = $("ws-where");
  if (whereText) whereText.textContent = whereWords(null, where);
  if (typeof SCENARIO !== "undefined") SCENARIO.subscribe(drawScenario);
  const ctx = await shellContext(where);
  if (!ctx || !main || !account) return ctx;
  if (ctx.signedOut) {
    main.innerHTML = signedOutHtml();
    return ctx;
  }
  if (ctx.league && where.league !== null) rememberLeague(ctx.league.id);
  main.innerHTML = railHtml(ctx, where);
  account.innerHTML = accountHtml(ctx, where);
  if (whereText) whereText.textContent = whereWords(ctx, where);
  wireMenus(main);
  wireMenus(account);
  if (typeof SCENARIO !== "undefined") drawScenario(SCENARIO.get());
  return ctx;
}

const SHELL = startShell();
