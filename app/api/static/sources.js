"use strict";

/* SOURCES (docs/projection_sources.md, docs/draft_plan.md): every pool the
   viewer may plan on, and a file of his own uploaded with a mapping he can
   fix. Drawn on the draft plan page, above THE LADDER, and on
   /account/projections, from one script so the two cannot drift.

     GET  /projections/sources?season=     the pools, as a ws-grid
     POST /projections/sets/preview        the mapping, the basis, the matching
     POST /projections/sets                store it under his name for it

   The mapping is the upload's own override (`app.projections.upload`),
   extended to every field and sent whole (`exact`), so nothing is parsed
   twice and nothing is guessed behind his back once he has chosen. A name
   is the source: a file stored under one of his names replaces that set,
   and is read with last time's mapping first. Nothing here bids, nominates
   or touches ESPN. */

const SOURCES = (() => {
  const FIELD_WORDS = {
    name: "Name",
    games: "Games",
    team: "Team",
    position: "Position",
    minutes: "Minutes",
    value: "Value $",
    injury: "Injury",
  };
  const wordOf = (field) => FIELD_WORDS[field] || field;
  const dayOf = (iso) => {
    if (!iso) return dash;
    const day = iso.length === 10 ? new Date(`${iso}T12:00:00Z`) : new Date(iso);
    return day.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  let OPTS = null;
  let LIST = null;
  const UP = { file: null, preview: null, choice: {}, basis: "auto", mapped: false, timer: null, seq: 0 };

  /** Mount the section into `opts.host`: {host, season, current, choose(choice),
   *  plan (a link to plan on a source, or null), onChanged()}. */
  function mount(opts) {
    OPTS = opts;
    opts.host.innerHTML = frame();
    $("src-upload-open").onclick = () => openUpload(true);
    $("src-cancel").onclick = () => openUpload(false);
    $("src-file").onchange = () => {
      UP.file = $("src-file").files[0] || null;
      UP.mapped = false;
      UP.basis = "auto";
      if (UP.file) preview();
    };
    $("src-name").oninput = () => {
      clearTimeout(UP.timer);
      UP.timer = setTimeout(() => (UP.file ? preview() : judge()), 450);
    };
    $("src-store").onclick = store;
    return load();
  }

  function frame() {
    return (
      `<section class="ws-sect src" id="sources-section">` +
      `<div class="ws-sect-head"><h2 class="ws-label">Sources</h2><span class="ws-k" id="src-tag"></span>` +
      `<button type="button" class="ws-btn sm" id="src-upload-open" aria-expanded="false" aria-controls="src-up">Upload a file</button>` +
      `<button type="button" class="ws-btn sm quiet" data-account="sources-account" aria-haspopup="dialog">How this is worked out</button></div>` +
      `<div class="ws-panel flush"><div class="scroll"><table class="ws-grid src-grid">` +
      `<thead><tr><th class="l">Name</th><th class="l">Kind</th><th>Season</th><th>Rows</th>` +
      `<th title="matched to a player we hold">Matched</th><th title="on the board by name only">Unmatched</th>` +
      `<th class="l">When</th><th class="l">By</th></tr></thead>` +
      `<tbody id="src-rows"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody></table></div></div>` +
      uploadHtml() +
      `<div class="ws-account" id="sources-account" data-title="Sources">` +
      `<p data-k="What a source is">A pool of projections the plan can be built on: Basketball Monster's ` +
      `newest capture (paid, and yours alone when your membership pulled it), ESPN's own projections, ` +
      `each file you uploaded, under the name you gave it, and each composite you made of them. One ` +
      `source per plan; the chooser at the top switches it.</p>` +
      `<p data-k="Matched">How many of a source's rows are a player we hold, by ESPN id or by the ` +
      `strict name matcher, which refuses a doubtful match rather than guess ` +
      `(<span class="mono">app.player_names.match_player</span>). Unmatched rows are stored anyway ` +
      `under a synthetic id and go on the board by name only, the way BBM's rookies do.</p>` +
      `<p data-k="The mapping">A file's header row is read by a synonyms table and shown as a column ` +
      `per field, with the first three values under it so a wrong guess is visible. Change any ` +
      `column; what you choose is the mapping, whole. A field that needs a column and has none ` +
      `stops the file being stored, and says which. A percentage alone cannot become a roster's ` +
      `percentage, which is made shots over attempts, so the attempts are required ` +
      `(<span class="mono">app/scoring/lines.py</span>).</p>` +
      `<p data-k="Per game or totals">Measured from the points column: a median above 200 is season ` +
      `totals, which are divided by games on the way in. Switch it when the file says otherwise.</p>` +
      `<p data-k="A name is the source">Your names are unique for a season. A file stored under one ` +
      `of them again replaces that source's rows and keeps its place in your composites, and is ` +
      `read with the mapping it was stored with last time.</p>` +
      `<p data-k="Nothing here acts">Storing a file changes nothing but your own sources. Nothing ` +
      `here bids, nominates or touches ESPN.</p></div>` +
      `</section>`
    );
  }

  function uploadHtml() {
    return (
      `<div class="ws-panel src-up" id="src-up" hidden>` +
      `<p class="ws-sub">Upload a file <span class="n">CSV, .xlsx or .xls · a row per player</span></p>` +
      `<div class="ws-form"><div class="ws-fld"><label for="src-file">File</label>` +
      `<input class="src-file" id="src-file" type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls"></div></div>` +
      `<div id="src-map" aria-live="polite"></div>` +
      `<div class="ws-form src-name">` +
      `<div class="ws-fld"><label for="src-name">Name the source</label>` +
      `<input class="ws-input plain" id="src-name" maxlength="80" autocomplete="off" list="src-names" placeholder="e.g. Hashtag preseason"></div>` +
      `<datalist id="src-names"></datalist>` +
      `<div class="ws-fld"><label for="src-note">Where from <span class="faint">optional</span></label>` +
      `<input class="ws-input plain" id="src-note" maxlength="200" autocomplete="off" placeholder="the site, the day"></div>` +
      `<button type="button" class="ws-btn primary" id="src-store" disabled>Store</button>` +
      `<button type="button" class="ws-btn quiet" id="src-cancel">Close</button></div>` +
      `<p class="src-why" id="src-why" aria-live="polite"></p>` +
      `</div>`
    );
  }

  async function load() {
    const got = await get(`/projections/sources?season=${encodeURIComponent(OPTS.season)}`);
    if (!got.ok) {
      $("src-rows").innerHTML = `<tr><td colspan="8" class="empty">${escape(got.detail)}</td></tr>`;
      return null;
    }
    LIST = got.body;
    drawList();
    return LIST;
  }

  const uploads = () => (LIST ? LIST.sources.filter((s) => s.kind === "upload") : []);
  const composites = () => (LIST ? LIST.sources.filter((s) => s.kind === "composite") : []);

  function drawList() {
    const rows = LIST.sources;
    $("src-tag").textContent = `${count(rows.length, "pool")} for ${LIST.season}`;
    $("src-rows").innerHTML = rows
      .map((s) => {
        const current = OPTS.current && s.source === OPTS.current;
        const name = OPTS.choose
          ? `<button type="button" class="src-pick" data-pick="${escape(s.source)}"` +
            `${current ? ' aria-current="true"' : ""} title="Plan on ${escape(s.name)}">${escape(s.name)}</button>`
          : escape(s.name);
        return (
          `<tr class="${current ? "ours" : ""}"><td class="name">${name}` +
          (current ? ` <span class="ws-tag accent">in view</span>` : "") +
          (s.gated ? ` <span class="ws-tag" title="${escape(s.note)}">paid</span>` : "") +
          `</td><td class="l small">${escape(s.kind_words)}</td><td>${s.season}</td>` +
          `<td>${s.rows}</td><td>${s.matched}</td><td>${s.unmatched}</td>` +
          `<td class="l small">${escape(dayOf(s.when))}</td><td class="l small src-by">${escape(s.by)}</td></tr>`
        );
      })
      .join("");
    $("src-rows").querySelectorAll("[data-pick]").forEach((button) => {
      button.onclick = () => OPTS.choose(button.dataset.pick);
    });
    $("src-names").innerHTML = uploads()
      .map((s) => `<option value="${escape(s.name)}"></option>`)
      .join("");
  }

  function openUpload(open) {
    $("src-up").hidden = !open;
    $("src-upload-open").setAttribute("aria-expanded", String(open));
    if (open) $("src-file").focus();
  }

  /* ---- the mapping ---------------------------------------------------------- */

  function form(withMap) {
    const data = new FormData();
    data.append("file", UP.file);
    data.append("season", String(OPTS.season));
    const name = $("src-name").value.trim();
    if (name) data.append("name", name);
    const note = $("src-note").value.trim();
    if (note) data.append("note", note);
    if (withMap) {
      data.append("exact", "true");
      Object.entries(UP.choice).forEach(([field, header]) => {
        if (header) data.append("map", `${header}=${field}`);
      });
    }
    data.append("basis", UP.basis);
    return data;
  }

  async function post(url, data) {
    const response = await fetch(url, { method: "POST", body: data, headers: { accept: "application/json" } });
    let body = null;
    try {
      body = await response.json();
    } catch (error) {
      /* no body */
    }
    if (response.status === 401) toSignIn();
    return { ok: response.ok, status: response.status, body };
  }

  /** Read the file as the mapping on screen stands (or, before he has
   *  touched it, as the server guesses or remembers it). */
  async function preview() {
    const seq = ++UP.seq;
    $("src-why").textContent = "Reading the file…";
    $("src-why").classList.remove("ws-neg");
    const got = await post("/projections/sets/preview", form(UP.mapped));
    if (seq !== UP.seq) return;
    if (!got.ok) {
      UP.preview = null;
      $("src-map").innerHTML = "";
      const detail = got.body && got.body.detail;
      $("src-why").textContent = typeof detail === "string" ? detail : `The file could not be read: ${got.status}`;
      $("src-why").classList.add("ws-neg");
      judge();
      return;
    }
    UP.preview = got.body;
    if (!UP.mapped) {
      UP.choice = Object.fromEntries(got.body.fields.map((f) => [f.field, f.header || ""]));
      UP.basis = got.body.basis_forced ? got.body.basis : "auto";
    }
    drawMap();
    judge();
  }

  function samplesOf(header) {
    const values = (UP.preview.samples || {})[header] || [];
    return values.map((v) => (v === null || v === "" ? "·" : String(v))).join("  ");
  }

  function drawMap() {
    const p = UP.preview;
    const used = {};
    Object.entries(UP.choice).forEach(([field, header]) => {
      if (header) (used[header] = used[header] || []).push(field);
    });
    const byField = Object.fromEntries(p.fields.map((f) => [f.field, f]));
    const rows = p.fields
      .map((f) => {
        const header = UP.choice[f.field] || "";
        const options =
          `<option value="">— none —</option>` +
          p.headers.map((h) => `<option value="${escape(h)}"${h === header ? " selected" : ""}>${escape(h)}</option>`).join("");
        let state = "";
        const twice = header && used[header].length > 1;
        if (twice) state = `<span class="ws-neg">also ${escape(used[header].filter((x) => x !== f.field).map(wordOf).join(", "))}</span>`;
        else if (!header && byField[f.field].derived) state = `<span class="faint">from ${escape(byField[f.field].derived)}</span>`;
        else if (!header && f.required) state = `<span class="ws-neg">needs a column</span>`;
        else if (!header) state = `<span class="faint">none</span>`;
        return (
          `<tr><td class="l src-field"><b>${escape(wordOf(f.field))}</b>` +
          `<span class="faint">${f.required ? "required" : ["FG%", "FT%"].includes(f.field) ? "or the makes" : "optional"}</span></td>` +
          `<td class="l"><span class="ws-field sel"><label class="sr" for="src-col-${escape(f.field)}">Column for ${escape(wordOf(f.field))}</label>` +
          `<select class="ws-select src-col" id="src-col-${escape(f.field)}" data-field="${escape(f.field)}">${options}</select></span></td>` +
          `<td class="l src-sample ws-data">${header ? escape(samplesOf(header)) : ""}</td>` +
          `<td class="l small">${state}</td></tr>`
        );
      })
      .join("");
    const basis = UP.basis === "auto" ? p.basis : UP.basis;
    const matchedLine =
      `<b class="ws-data">${p.matched}</b> of <b class="ws-data">${p.rows_stored}</b> rows matched to players we hold` +
      (p.unmatched.length
        ? `; <b class="ws-data">${p.unmatched.length}</b> did not, and are stored anyway under a synthetic id, ` +
          `on the board by name only (the way BBM's rookies go on it)`
        : "") +
      ".";
    const lists = [
      ["Not matched", p.unmatched],
      ["Matched by a short first name", p.loose],
      ["A second name for a man already taken", p.ambiguous],
      ["In the file twice (the second skipped)", p.duplicates],
      ["Rows that could not be read", (p.rejected || []).map((r) => `${r.where}: ${r.why}`)],
    ].filter(([, names]) => names && names.length);
    let remembered = "";
    if (p.last_time) {
      remembered = p.last_time.whole
        ? `As last time: the columns “${escape(p.name)}” was stored with on ${escape(dayOf(p.last_time.uploaded_at))}.`
        : `Partly as last time: the column for ${escape(p.last_time.gone.map(wordOf).join(", "))} is not in this file, so it was guessed.`;
    }
    const replaces = p.replaces
      ? ` Storing replaces “${escape(p.name)}”: new rows under the same source, so a composite built on it is worked out again.`
      : "";
    $("src-map").innerHTML =
      `<p class="ws-sub src-maphead">The mapping <span class="n">${escape(p.filename)} · ${count(p.rows_read, "row")} · ${count(p.headers.length, "column")}</span></p>` +
      (remembered || replaces ? `<p class="ws-note src-last">${remembered}${replaces}</p>` : "") +
      `<div class="scroll src-mapframe"><table class="ws-grid src-map">` +
      `<thead><tr><th class="l">Field</th><th class="l">Column</th><th class="l">First three</th><th class="l"></th></tr></thead>` +
      `<tbody>${rows}</tbody></table></div>` +
      `<div class="src-basis"><span class="ws-sub">The numbers are</span>` +
      `<span class="ws-seg" role="group" aria-label="Per game or season totals">` +
      `<button type="button" data-basis="per_game" aria-pressed="${basis === "per_game"}">Per game</button>` +
      `<button type="button" data-basis="totals" aria-pressed="${basis === "totals"}">Season totals</button></span>` +
      (UP.basis !== "auto" ? ` <button type="button" class="ws-btn sm quiet" id="src-basis-auto">Measure it</button>` : "") +
      `<p class="ws-note">${escape(p.basis_reason)}</p></div>` +
      `<p class="ws-p src-match">${matchedLine}</p>` +
      (lists.length
        ? `<details class="ws-disc src-names"><summary>${escape(lists.map(([label, names]) => `${label} (${names.length})`).join(" · "))}</summary>` +
          `<div class="ws-disc-body">${lists.map(([label, names]) => `<p class="ws-p"><b>${escape(label)}:</b> ${escape(names.join(", "))}</p>`).join("")}</div></details>`
        : "") +
      (p.reasons.length && !p.ok
        ? `<ul class="src-reasons">${p.reasons.map((r) => `<li class="ws-neg">${escape(r)}</li>`).join("")}</ul>`
        : "");
    $("src-map").querySelectorAll("select.src-col").forEach((select) => {
      select.onchange = () => {
        UP.choice[select.dataset.field] = select.value;
        UP.mapped = true;
        drawMap();
        judge();
        preview();
      };
    });
    $("src-map").querySelectorAll("[data-basis]").forEach((button) => {
      button.onclick = () => {
        UP.basis = button.dataset.basis;
        UP.mapped = true;
        preview();
      };
    });
    const auto = $("src-basis-auto");
    if (auto) {
      auto.onclick = () => {
        UP.basis = "auto";
        preview();
      };
    }
  }

  /** Whether Store may be pressed, and if not, the one line that says why. */
  function judge() {
    const why = $("src-why");
    const name = $("src-name").value.trim();
    let reason = "";
    const p = UP.preview;
    if (!UP.file) reason = "Pick a file first.";
    else if (!p) reason = why.textContent;
    else {
      const needed = p.fields.filter((f) => f.required && !UP.choice[f.field] && !f.derived).map((f) => wordOf(f.field));
      const used = {};
      Object.entries(UP.choice).forEach(([field, header]) => {
        if (header) (used[header] = used[header] || []).push(field);
      });
      const twice = Object.entries(used).filter(([, fields]) => fields.length > 1);
      if (needed.length) reason = `Not yet: ${needed.join(", ")} ${needed.length === 1 ? "needs" : "need"} a column.`;
      else if (twice.length)
        reason = `Not yet: “${twice[0][0]}” is chosen for ${twice[0][1].map(wordOf).join(" and ")}; a column is one field.`;
      else if (!p.ok) reason = `Not yet: ${p.reasons[0] || "the mapping cannot be used"}.`;
      else if (!name) reason = "Name the source to store it.";
      else if (composites().some((s) => s.name === name)) reason = `“${name}” is one of your composites; an upload needs a name of its own.`;
    }
    $("src-store").disabled = Boolean(reason);
    if (!(p === null && UP.file)) {
      why.textContent = reason || (p && p.replaces ? `Ready: stores over “${name}”.` : "Ready to store.");
      why.classList.toggle("ws-neg", Boolean(reason) && Boolean(p) && !reason.startsWith("Name"));
    }
  }

  async function store() {
    $("src-store").disabled = true;
    $("src-why").textContent = "Storing…";
    const got = await post("/projections/sets", form(true));
    if (!got.ok) {
      const detail = got.body && got.body.detail;
      $("src-why").textContent =
        typeof detail === "string" ? detail : detail && detail.reasons ? detail.reasons.join(" ") : `Not stored: ${got.status}`;
      $("src-why").classList.add("ws-neg");
      $("src-store").disabled = false;
      return;
    }
    const stored = got.body;
    await load();
    const choice = `upload:${stored.set_id}`;
    $("src-why").classList.remove("ws-neg");
    $("src-why").innerHTML =
      `Stored “${escape(stored.name)}”: <span class="ws-data">${stored.rows_stored}</span> rows, ` +
      `<span class="ws-data">${stored.matched}</span> matched.` +
      (OPTS.choose ? ` <button type="button" class="ws-btn sm" id="src-plan-on">Plan on it</button>` : "");
    const planOn = $("src-plan-on");
    if (planOn) planOn.onclick = () => OPTS.choose(choice);
    if (OPTS.onChanged) OPTS.onChanged(choice);
  }

  return { mount, load, list: () => LIST };
})();
