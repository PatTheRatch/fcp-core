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
    $("src-mix-open").onclick = () => ($("src-mix").hidden ? openMix(null) : closeMix());
    $("src-mix-cancel").onclick = closeMix;
    $("src-mix-save").onclick = saveMix;
    $("src-mix-name").oninput = judgeMix;
    return load();
  }

  function frame() {
    return (
      `<section class="ws-sect src" id="sources-section">` +
      `<div class="ws-sect-head"><h2 class="ws-label">Sources</h2><span class="ws-k" id="src-tag"></span>` +
      `<button type="button" class="ws-btn sm" id="src-upload-open" aria-expanded="false" aria-controls="src-up">Upload a file</button>` +
      `<button type="button" class="ws-btn sm" id="src-mix-open" aria-expanded="false" aria-controls="src-mix">New composite</button>` +
      `<button type="button" class="ws-btn sm quiet" data-account="sources-account" aria-haspopup="dialog">How this is worked out</button></div>` +
      `<div class="ws-panel flush"><div class="scroll"><table class="ws-grid src-grid">` +
      `<thead><tr><th class="l">Name</th><th class="l">Kind</th><th>Season</th><th>Rows</th>` +
      `<th title="matched to a player we hold">Matched</th><th title="on the board by name only">Unmatched</th>` +
      `<th class="l">When</th><th class="l">By</th></tr></thead>` +
      `<tbody id="src-rows"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody></table></div></div>` +
      uploadHtml() +
      mixHtml() +
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
      `<p data-k="A composite">A named source worked out from other sources with your weights ` +
      `(<span class="mono">app/projections/composite.py</span>). Per man, over the sources that carry him: ` +
      `per-game numbers and games are the weighted mean, the weights shared out again over the sources ` +
      `present, so a man one source lacks is the other's number at full weight; the two percentages come ` +
      `from the weighted makes and attempts, never from averaging percentages; minutes and value are the ` +
      `weighted mean where a source has them. A man no source carries is not in it. It is worked out again ` +
      `whenever a source in it changes. A composite with BBM in it is BBM's number, and yours alone.</p>` +
      `<p data-k="What a composite is not">The weights are yours: nothing here measures which source was ` +
      `closer last season, and no source is called better. It does not blend injuries (two sources' games ` +
      `are averaged like any number), and takes a man's position from the first source that names one.</p>` +
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
          (s.gated ? ` <span class="ws-tag" title="${escape(s.kind === "bbm" ? s.note : "carries BBM's paid numbers")}">paid</span>` : "") +
          (s.kind === "composite"
            ? `<span class="src-recipe">${escape(s.note)}${escape(carriedWords(s.carried))}</span>` +
              `<button type="button" class="ws-btn sm quiet src-edit" data-edit="${s.set_id}">Edit weights</button>`
            : "") +
          `</td><td class="l small">${escape(s.kind_words)}</td><td>${s.season}</td>` +
          `<td>${s.rows}</td><td>${s.matched}</td><td>${s.unmatched}</td>` +
          `<td class="l small">${escape(dayOf(s.when))}</td><td class="l small src-by">${escape(s.by)}</td></tr>`
        );
      })
      .join("");
    $("src-rows").querySelectorAll("[data-pick]").forEach((button) => {
      button.onclick = () => OPTS.choose(button.dataset.pick);
    });
    $("src-rows").querySelectorAll("[data-edit]").forEach((button) => {
      button.onclick = () => openMix(Number(button.dataset.edit));
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

  /* ---- a composite ------------------------------------------------------------ */

  const MIX = { editing: null };

  /** "carried by 2: 170 · by 1: 190", from what the rows were built with. */
  function carriedWords(carried) {
    const keys = Object.keys(carried || {}).sort((a, b) => b - a);
    if (!keys.length) return "";
    return " · " + keys.map((k, i) => `${i ? "by" : "carried by"} ${k}: ${carried[k]}`).join(" · ");
  }

  function mixHtml() {
    return (
      `<div class="ws-panel src-up" id="src-mix" hidden>` +
      `<p class="ws-sub"><span id="src-mix-title">New composite</span> <span class="n">a weight per source · 0 leaves it out</span></p>` +
      `<div class="ws-form"><div class="ws-fld"><label for="src-mix-name">Name the composite</label>` +
      `<input class="ws-input plain" id="src-mix-name" maxlength="80" autocomplete="off" placeholder="e.g. My consensus"></div></div>` +
      `<div class="scroll src-mapframe"><table class="ws-grid src-mixgrid"><thead><tr><th class="l">Source</th>` +
      `<th class="l">Weight</th><th>Share</th></tr></thead><tbody id="src-mix-rows"></tbody></table></div>` +
      `<p class="ws-note">Per man, the weighted mean over the sources that carry him, the weights shared out again ` +
      `over those; FG% and FT% from the weighted makes and attempts. BBM in it makes the composite BBM's number, ` +
      `and yours alone.</p>` +
      `<div class="ws-form"><button type="button" class="ws-btn primary" id="src-mix-save" disabled>Save</button>` +
      `<button type="button" class="ws-btn quiet" id="src-mix-cancel">Close</button></div>` +
      `<p class="src-why" id="src-mix-why" aria-live="polite"></p></div>`
    );
  }

  /** What a composite may read: BBM when it is his, ESPN, his uploads. */
  const mixable = () => (LIST ? LIST.sources.filter((s) => s.kind !== "composite") : []);
  const partOf = (s) => String(s.kind === "upload" ? s.set_id : s.source);

  /** Open the editor on a composite to change (its set id), or null for a new one. */
  function openMix(id) {
    MIX.editing = id;
    const editing = id === null ? null : composites().find((s) => s.set_id === id);
    const weights = new Map((editing ? editing.recipe : []).map((part) => [String(part.source), part.weight]));
    const pool = mixable();
    const even = Math.round(100 / Math.max(1, pool.length));
    $("src-mix-title").textContent = editing ? `Change “${editing.name}”` : "New composite";
    $("src-mix-name").value = editing ? editing.name : "";
    $("src-mix-rows").innerHTML = pool
      .map((s) => {
        const part = partOf(s);
        const weight = editing ? (weights.get(part) ?? 0) : even;
        return (
          `<tr><td class="l name">${escape(s.name)} <span class="faint">${escape(s.kind_words)}</span>` +
          (s.gated ? ` <span class="ws-tag">paid</span>` : "") +
          `</td><td class="l"><label class="sr" for="src-w-${escape(part)}">Weight for ${escape(s.name)}</label>` +
          `<input class="ws-input plain src-weight" id="src-w-${escape(part)}" type="number" inputmode="decimal" ` +
          `min="0" max="100" step="1" value="${weight}" data-part="${escape(part)}"></td>` +
          `<td class="src-share" data-share="${escape(part)}"></td></tr>`
        );
      })
      .join("");
    $("src-mix-rows").querySelectorAll("input.src-weight").forEach((input) => {
      input.oninput = judgeMix;
    });
    $("src-mix").hidden = false;
    $("src-mix-open").setAttribute("aria-expanded", "true");
    $("src-mix-why").textContent = "";
    judgeMix();
    $("src-mix-name").focus();
  }

  function closeMix() {
    MIX.editing = null;
    $("src-mix").hidden = true;
    $("src-mix-open").setAttribute("aria-expanded", "false");
  }

  function recipe() {
    return [...$("src-mix-rows").querySelectorAll("input.src-weight")].map((input) => ({
      source: /^\d+$/.test(input.dataset.part) ? Number(input.dataset.part) : input.dataset.part,
      weight: input.value === "" ? NaN : Number(input.value),
    }));
  }

  function judgeMix() {
    const parts = recipe();
    const total = parts.reduce((sum, p) => sum + (Number.isFinite(p.weight) && p.weight > 0 ? p.weight : 0), 0);
    parts.forEach((p) => {
      const cell = $("src-mix-rows").querySelector(`[data-share="${String(p.source)}"]`);
      if (cell) cell.textContent = total > 0 && p.weight > 0 ? `${Math.round((100 * p.weight) / total)}%` : dash;
    });
    const name = $("src-mix-name").value.trim();
    const bad = parts.find((p) => !Number.isFinite(p.weight) || p.weight < 0 || p.weight > 100);
    const taken = (LIST ? LIST.sources : []).find(
      (s) => s.name === name && (s.kind === "upload" || (s.kind === "composite" && s.set_id !== MIX.editing)),
    );
    let reason = "";
    if (bad) reason = "A weight is a number from 0 to 100.";
    else if (total <= 0) reason = "Give at least one source a weight above 0.";
    else if (!name) reason = "Name the composite to save it.";
    else if (taken) reason = `You already have a source called “${name}”.`;
    $("src-mix-save").disabled = Boolean(reason);
    $("src-mix-why").textContent = reason;
    $("src-mix-why").classList.toggle("ws-neg", Boolean(bad) || Boolean(taken));
  }

  async function saveMix() {
    $("src-mix-save").disabled = true;
    $("src-mix-why").textContent = "Working it out…";
    const body = { season: OPTS.season, name: $("src-mix-name").value.trim(), recipe: recipe() };
    const url = MIX.editing === null ? "/projections/composites" : `/projections/composites/${MIX.editing}`;
    const response = await fetch(url, {
      method: MIX.editing === null ? "POST" : "PUT",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
    let answer = null;
    try {
      answer = await response.json();
    } catch (error) {
      /* no body */
    }
    if (response.status === 401) toSignIn();
    if (!response.ok) {
      const detail = answer && answer.detail;
      $("src-mix-why").textContent = typeof detail === "string" ? detail : `Not saved: ${response.status}`;
      $("src-mix-why").classList.add("ws-neg");
      $("src-mix-save").disabled = false;
      return;
    }
    await load();
    const choice = `composite:${answer.id}`;
    MIX.editing = answer.id;
    $("src-mix-title").textContent = `Change “${answer.name}”`;
    $("src-mix-why").classList.remove("ws-neg");
    $("src-mix-why").innerHTML =
      `Saved “${escape(answer.name)}”: <span class="ws-data">${answer.rows}</span> men` +
      `${escape(carriedWords((answer.built_from || {}).carried))}.` +
      (OPTS.choose ? ` <button type="button" class="ws-btn sm" id="src-mix-plan">Plan on it</button>` : "");
    const planOn = $("src-mix-plan");
    if (planOn) planOn.onclick = () => OPTS.choose(choice);
    $("src-mix-save").disabled = false;
    if (OPTS.onChanged) OPTS.onChanged(choice);
  }

  return { mount, load, list: () => LIST };
})();
