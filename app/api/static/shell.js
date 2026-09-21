/* The shell: one bar over every page (docs/product.md "Navigation",
   docs/site.md).

     [ League ▾ ]   This week   Standings   Draft   History   My team ▾    [ Account ▾ ]

   Drawn into <div id="shell"> from two routes: /auth/me (who this is, his
   role in each league and his claims on teams) and /leagues (his leagues,
   each with its name and the seasons held). Loaded after pages.js, whose
   `get`, `place`, `escape` and URL helpers it uses.

   * The league is the one in the URL; on a page without one (the account
     pages, the home page) it is the one this browser last looked at, kept
     in localStorage (wrapped: a private window merely forgets it), else his
     first. The switcher lists his leagues, and the seasons of this one.
   * "My team" is there only in a league where he is a verified manager: this
     season's team, or his newest one in the league. Otherwise it is "Claim
     your team", to the claim page. In single mode, where the owner may read
     every team, a league with no claim falls back to the team the context
     route names as ours, which is what the pages always did.
   * The menus are buttons that open a list of links: Enter, Space or the
     arrow keys open one, the arrows move through it, Escape closes it and
     puts the focus back on its button, and it closes when the focus or a
     click goes elsewhere.

   `SHELL` is a promise of what the shell found ({me, league, season,
   myTeam, ...}), for a page that wants to know whose team is whose. */

"use strict";

/** Where the last league looked at is kept, per browser. */
const LEAGUE_KEY = "fcp-league";

const LEAGUE_SECTIONS = [
  ["week", "This week"],
  ["standings", "Standings"],
  ["draft", "Draft"],
  ["history", "History"],
];
const TEAM_SECTIONS = [
  ["week", "Week"],
  ["season", "Season"],
  ["moves", "Moves"],
  ["trades", "Trades"],
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

/** The bar's frame, drawn at once so the theme switch is there before any
 *  fetch returns; the links are filled in when /auth/me answers. */
function shellFrame() {
  const host = $("shell");
  if (!host) return;
  host.innerHTML =
    `<header class="shell"><nav class="nav" aria-label="Site">` +
    `<a class="brand" href="/" aria-label="Full Court Press, home">` +
    `<span class="long">Full Court Press</span><span class="short">FCP</span></a>` +
    `<div class="nav-main" id="shell-main"></div>` +
    `<div class="end"><div id="shell-account"></div>` +
    `<button class="btn theme" id="theme" type="button" aria-pressed="false" ` +
    `title="light or dark">Lights down</button></div>` +
    `</nav></header>`;
}

/** `aria-current`: "page" for a link to this very page, "true" for the
 *  current one of a set whose link goes elsewhere (the league in the
 *  switcher, which opens its newest season). */
const current = (yes, what) => (yes ? ` aria-current="${what || "page"}"` : "");

/** One menu: a button and the list it opens. */
function menuHtml(id, label, items, options) {
  const opts = options || {};
  return (
    `<div class="menu${opts.right ? " right" : ""}" data-menu>` +
    `<button type="button" class="menu-btn${opts.on ? " on" : ""}" id="${id}-btn" ` +
    `aria-expanded="false" aria-controls="${id}-list">` +
    `${label}<span class="caret" aria-hidden="true">▾</span></button>` +
    `<ul class="menu-list" id="${id}-list" hidden>${items.join("")}</ul></div>`
  );
}

const itemHtml = (href, text, on, what) =>
  `<li><a href="${escape(href)}"${current(on, what)}>${text}</a></li>`;
const groupHtml = (text) => `<li class="group" role="presentation">${text}</li>`;

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
    return menuHtml("league", "No league yet", [
      itemHtml("/account/connections", "Connect a league", false),
      groupHtml("Or open the invite link a league's owner sent you."),
    ]);
  }
  const onLeaguePage = LEAGUE_SECTIONS.some(([key]) => key === where.section);
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
    `<span class="league-name">${escape(league.name || `League ${league.id}`)}</span>` +
    (season ? ` <span class="season">${season}</span>` : "");
  return menuHtml("league", label, items);
}

function sectionsHtml(ctx, where) {
  const { league, season, myTeam, pending } = ctx;
  if (!league || !season) return "";
  const here = where.league === league.id;
  const links = LEAGUE_SECTIONS.map(
    ([key, text]) =>
      `<li><a class="navlink" href="${leagueUrl(league.id, season, key)}"` +
      `${current(here && where.section === key)}>${text}</a></li>`,
  );
  if (myTeam) {
    const ours = where.team === myTeam.espn_team_id && where.season === myTeam.season;
    const items = TEAM_SECTIONS.map(([key, text]) =>
      itemHtml(
        teamUrl(league.id, myTeam.season, myTeam.espn_team_id, key),
        text,
        ours && where.section === `team-${key}`,
      ),
    );
    items.unshift(groupHtml(escape(myTeam.name) + (myTeam.season !== season ? `, ${myTeam.season}` : "")));
    links.push(`<li>${menuHtml("team", "My team", items, { on: ours })}</li>`);
  } else {
    const text = pending ? "Claim pending" : "Claim your team";
    links.push(
      `<li><a class="navlink" href="/pages/claim/${league.id}/${season}"` +
      `${current(where.section === "claim")}>${text}</a></li>`,
    );
  }
  return `<ul class="sections">${links.join("")}</ul>`;
}

function accountMenu(ctx, where) {
  const { me } = ctx;
  const items = [groupHtml(escape(me.email))];
  ACCOUNT_SECTIONS.forEach(([key, text]) =>
    items.push(itemHtml(`/account/${key}`, text, where.section === key && where.league === null)),
  );
  if (me.mode === "single") {
    items.push(groupHtml("Single mode: nobody signs in"));
  } else {
    items.push(
      `<li><form method="post" action="/auth/sign-out">` +
        `<button type="submit">Sign out</button></form></li>`,
    );
  }
  const on = ACCOUNT_SECTIONS.some(([key]) => key === where.section);
  return menuHtml("account", "Account", items, { right: true, on });
}

/* ---- the menus' behaviour ---------------------------------------------- */

function wireMenus(root) {
  root.querySelectorAll("[data-menu]").forEach((menu) => {
    const button = menu.querySelector(".menu-btn");
    const list = menu.querySelector(".menu-list");
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

/* ---- what the shell knows ---------------------------------------------- */

async function shellContext(where) {
  const [me, listed] = await Promise.all([get("/auth/me"), get("/leagues")]);
  if (!me.ok) return null;
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
 *  each, for a league page's masthead. Empty with fewer than two. */
function seasonLinks(ctx, where) {
  if (!ctx || !ctx.league || ctx.league.seasons.length < 2) return "";
  return ctx.league.seasons
    .slice()
    .reverse()
    .map(
      (year) =>
        `<li><a href="${leagueUrl(ctx.league.id, year, where.section)}"` +
        `${current(year === where.season)}>${year}</a></li>`,
    )
    .join("");
}

/** The league's name for a masthead, or its id when none is stored yet. */
const leagueName = (ctx, id) =>
  ctx && ctx.league && ctx.league.name ? ctx.league.name : `League ${id}`;

async function startShell() {
  const where = place();
  shellFrame();
  const ctx = await shellContext(where);
  const main = $("shell-main");
  const account = $("shell-account");
  if (!ctx || !main || !account) return ctx;
  if (ctx.league && where.league !== null) rememberLeague(ctx.league.id);
  main.innerHTML = leagueMenu(ctx, where) + sectionsHtml(ctx, where);
  account.innerHTML = accountMenu(ctx, where);
  wireMenus(main);
  wireMenus(account);
  return ctx;
}

const SHELL = startShell();
