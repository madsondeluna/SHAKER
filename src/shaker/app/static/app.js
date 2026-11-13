// SHAKER interface. Reads the local API every few seconds; nothing here writes to the campaign.
(() => {
  "use strict";
  const POLL_MS = 3000;
  const STATES = ["assembled", "running", "paused", "equilibrating", "built", "failed", "unbuilt"];
  const LETTER = { assembled: "A", running: "R", paused: "P", equilibrating: "E", built: "B", failed: "F", unbuilt: "" };
  const LABEL = { assembled: "Assembled", running: "Running", paused: "Paused at wall time", equilibrating: "Minimized", built: "Built", failed: "Failed", unbuilt: "Not built" };
  const main = document.getElementById("main");
  const live = document.getElementById("live");
  const nf = new Intl.NumberFormat("en-US");
  const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
  let overview = null, route = null, viewer = null, structureSource = null, timer = null, turn = 0;

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, f = nf) => (v === null || v === undefined || v === "" ? "" : f.format(Number(v)));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const get = async (path) => {
    const response = await fetch(path, { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || response.statusText);
    return body;
  };
  const count = (states, name) => states.filter((s) => s === name).length;
  const empty = (text, command) => `<div class="panel empty"><div>${esc(text)}</div>${command ? `<code>${esc(command)}</code>` : ""}</div>`;
  const tile = (label, value) => `<div class="tile"><dt>${esc(label)}</dt><dd>${value === "" || value === null || value === undefined ? "&ndash;" : esc(value)}</dd></div>`;
  const table = (heads, rows) => `<div class="panel table-scroll"><table><thead><tr>${heads.map((h) => `<th${h[1] ? ' class="right"' : ""} scope="col">${esc(h[0])}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  const td = (v, right) => `<td class="${right ? "right num" : ""}">${v}</td>`;
  const bytes = (n) => (n > 1e9 ? nf1.format(n / 1e9) + " GB" : n > 1e6 ? nf1.format(n / 1e6) + " MB" : nf1.format(n / 1e3) + " kB");

  // ---- mode -----------------------------------------------------------------------------
  const modeButton = document.getElementById("mode");
  const setMode = (mode) => {
    if (mode) document.documentElement.dataset.mode = mode; else delete document.documentElement.dataset.mode;
    const dark = mode ? mode === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    modeButton.setAttribute("aria-pressed", String(dark));
    modeButton.textContent = dark ? "Light" : "Dark";
    modeButton.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    if (viewer) viewer.recolour();
  };
  let saved = null;
  try { saved = localStorage.getItem("shaker-mode"); } catch (e) { /* storage unavailable */ }
  setMode(saved);
  modeButton.addEventListener("click", () => {
    const next = modeButton.getAttribute("aria-pressed") === "true" ? "light" : "dark";
    try { localStorage.setItem("shaker-mode", next); } catch (e) { /* storage unavailable */ }
    setMode(next);
  });

  // ---- names: peptide > round > replica ---------------------------------------------------
  // A round is one condition: one peptide in one starting oligomeric state in one membrane,
  // repeated over all replicas. Its name states what is in the box at time zero.
  const MER = ["", "monomer", "dimer", "trimer", "tetramer", "pentamer", "hexamer", "heptamer", "octamer", "nonamer", "decamer"];
  const mer = (n, k) => `${k} ${MER[n] || `${n}-mer`}${k === 1 ? "" : "s"}`;
  const isControl = (c) => !c.copies || !c.peptide || c.peptide === "none";
  function roundName(c) {
    if (isControl(c)) return "Lipids only";
    const size = c.oligomer_size || 1;
    if (size === 1) return mer(1, c.copies);
    const k = Math.round((c.copies * (c.oligomer_fraction ?? 1)) / size), free = c.copies - k * size;
    return mer(size, k) + (free > 0 ? ` + ${mer(1, free)}` : "");
  }
  const peptideName = (c) => (isControl(c) ? "Control" : c.peptide);
  const roundLabel = (c) => `${peptideName(c)} · ${roundName(c)} · ${c.membrane}`;
  const byId = (id) => (overview ? overview.conditions.find((x) => x.id === id) : null);
  const labelOf = (id) => { const c = byId(id); return c ? roundLabel(c) : id; };
  const rep = (i) => String(i).padStart(3, "0");
  const peptideHref = (name, round) => `#/peptide/${encodeURIComponent(name)}${round ? `/${encodeURIComponent(round)}` : ""}`;
  const roundHref = (id) => { const c = byId(id); return c ? peptideHref(peptideName(c), id) : "#/"; };
  const kv = (pairs) => `<dl class="kv">${pairs.filter((p) => p && p[1] !== null && p[1] !== undefined && p[1] !== "").map(([k, v, raw]) => `<div><dt>${esc(k)}</dt><dd>${raw ? v : esc(v)}</dd></div>`).join("")}</dl>`;
  // rows grouped under one header per peptide, in the order the campaign declares them
  function grouped(items, idOf, rowOf, columns) {
    const groups = new Map();
    items.forEach((item) => { const c = byId(idOf(item)); const name = c ? peptideName(c) : "Other"; if (!groups.has(name)) groups.set(name, []); groups.get(name).push([item, c]); });
    return [...groups].flatMap(([name, list]) => [`<tr class="group"><th colspan="${columns}" scope="colgroup"><a href="${peptideHref(name)}">${esc(name)}</a><span class="num">${list.length} round${list.length === 1 ? "" : "s"}</span></th></tr>`, ...list.map(([item, c]) => rowOf(item, c))]);
  }

  // ---- line charts: a frame, three ticks up, two along, one class per kind of line -------------
  const axis = new Intl.NumberFormat("en-US", { maximumSignificantDigits: 4 });
  function chart(lines, title, robust = false, scale = 0.001) {
    const W = 360, H = 190, L = 58, R = 10, T = 10, B = 26;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    lines.forEach((l) => l.x.forEach((x, i) => { const y = l.y[i]; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }));
    if (!isFinite(x0) || x1 === x0) return `<figure class="plot"><figcaption class="caps">${esc(title)}</figcaption><div class="plot-empty">No data yet</div></figure>`;
    if (robust) {                     // the first picoseconds relax from far away: keep them from flattening the rest
      const all = lines.flatMap((l) => l.y).sort((a, b) => a - b);
      y0 = all[Math.floor(all.length * 0.01)]; y1 = all[Math.ceil(all.length * 0.97) - 1];
    }
    if (y1 === y0) { y0 -= 1; y1 += 1; }
    const pad = (y1 - y0) * 0.06; y0 -= pad; y1 += pad;
    const clip = `clip-${title.replace(/[^a-z0-9]/gi, "")}`;
    const px = (x) => L + ((x - x0) / (x1 - x0)) * (W - L - R), py = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
    const paths = lines.map((l) => `<path class="${l.cls}" d="${l.x.map((x, i) => `${i ? "L" : "M"}${px(x).toFixed(1)} ${py(l.y[i]).toFixed(1)}`).join("")}"/>`).join("");
    const yt = [y0 + pad, (y0 + y1) / 2, y1 - pad].map((v) => `<line class="tick" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/><text x="${L - 6}" y="${py(v) + 3}" text-anchor="end">${axis.format(v)}</text>`).join("");
    const xt = `<text x="${L}" y="${H - 8}">${axis.format(x0 * scale)}</text><text x="${W - R}" y="${H - 8}" text-anchor="end">${axis.format(x1 * scale)} ns</text>`;
    return `<figure class="plot"><figcaption class="caps">${esc(title)}</figcaption><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}, ${lines.length} line${lines.length === 1 ? "" : "s"}">${yt}<clipPath id="${clip}"><rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></clipPath><g clip-path="url(#${clip})">${paths}</g><rect class="frame" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/>${xt}</svg></figure>`;
  }
  // every replica of a round as a thin line, their mean on top
  function ensemble(series, title) {
    const reps = series ? series.replicas : [];
    const lines = reps.map((r) => ({ x: r.time, y: r.potential, cls: r.finished ? "line-thin" : "line-thin line-live" }));
    if (reps.length > 1) {
      const longest = reps.reduce((a, r) => (r.time.length > a.time.length ? r : a));
      const mean = longest.time.map((_, i) => { const v = reps.filter((r) => r.potential.length > i).map((r) => r.potential[i]); return v.reduce((a, b) => a + b, 0) / v.length; });
      lines.push({ x: longest.time, y: mean, cls: "line-mean" });
    }
    return chart(lines, `${title} \u00b7 ${reps.length} replica${reps.length === 1 ? "" : "s"}`);
  }
  function replicaPlots(series) {
    const st = series.stages; if (!st.length) return empty("No energies yet.", "shaker run");
    const flat = (k) => [{ x: st.flatMap((s) => s.time), y: st.flatMap((s) => s[k]), cls: "line-mean" }];
    return `<div class="plots">${chart(flat("potential"), "Potential energy, kJ/mol", true)}${chart(flat("temperature"), "Temperature, K", true)}${chart(flat("pressure"), "Pressure, bar", true)}</div>`;
  }

  // ---- views ----------------------------------------------------------------------------
  function progress(states) {
    const total = states.length || 1;
    return `<div class="progress" role="img" aria-label="${STATES.filter((s) => s !== "unbuilt").map((s) => `${count(states, s)} ${LABEL[s].toLowerCase()}`).join(", ")}">${STATES.filter((s) => s !== "unbuilt")
      .map((s) => `<i class="s-${s}" style="width:${(100 * count(states, s)) / total}%"></i>`).join("")}</div>`;
  }
  // where the replicas are, in the order a replica moves through the pipeline; the two
  // in-between states only appear when something is in them
  const FLOW = ["unbuilt", "built", "equilibrating", "running", "paused", "assembled", "failed"];
  const SHORT = { unbuilt: "Not built", built: "Built", equilibrating: "Minimized", running: "Running", paused: "Paused", assembled: "Assembled", failed: "Failed" };
  function legend(states) {
    const shown = FLOW.filter((s) => !["equilibrating", "paused"].includes(s) || count(states, s));
    return `<dl class="flow">${shown.map((s) => `<div><dd class="num">${num(count(states, s))}</dd><dt><i class="swatch s-${s}" aria-hidden="true"></i>${esc(SHORT[s])}</dt></div>`).join("")}</dl>`;
  }
  const hero = (done, total, word) => `<p class="hero"><span class="num">${num(done)}</span><span class="hero-of num">/ ${num(total)}</span><span class="caps">${esc(word)}</span></p>`;

  // the menu: the campaign, then one line per peptide with how much of it is finished
  function sideMenu(r) {
    const names = [...new Set(overview.conditions.map(peptideName))];
    const active = r.name === "peptide" ? r.id : r.name === "replica" && byId(r.id) ? peptideName(byId(r.id)) : null;
    const link = (href, label, on) => `<a href="${href}"${on ? ' aria-current="page"' : ""}>${esc(label)}</a>`;
    return `<div class="caps side-head">Peptides</div>
      ${names.map((name) => link(peptideHref(name), name, name === active)).join("")}
      <div class="caps side-head">Tables</div>
      ${link("#/peptides", "Descriptors", r.name === "peptides")}${link("#/events", "Events", r.name === "events")}`;
  }

  const boxFacts = (c) => { const olig = !isControl(c) && c.oligomer_size > 1; return kv([["Copies", isControl(c) ? "" : num(c.copies)], olig && ["Arrangement", c.arrangement], olig && ["Hydrophobic face", c.hydrophobic_face], olig && ["Axis distance", c.axis_distance ? `${num(c.axis_distance, nf1)} nm` : ""], ["Membrane", c.membrane], ["Lipids", `<span class="mono">${esc(c.lipids)}</span>`, 1], ["Peptide to lipid", isControl(c) ? "" : c.peptide_to_lipid], ["Water beads per lipid", num(c.water_beads_per_lipid)], ["Salt", `${num(c.salt_molar, new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }))} M`], ["Beads", num(c.beads)], ["Box edge", `${num(c.box_nm, nf1)} nm`], ["Mean speed", c.ns_per_day ? `${num(c.ns_per_day, nf1)} ns/day` : ""], ["Directory", `<span class="mono">conditions/${esc(c.id)}</span>`, 1]]); };

  function viewPeptide(name, rows, series) {
    const rounds = overview.conditions.filter((c) => peptideName(c) === name);
    if (!rounds.length) return `<h1>Unknown peptide</h1>${empty("No round in config.yaml uses this peptide.")}`;
    const d = rows.find((r) => r.peptide === name);
    const f3 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }), pc = new Intl.NumberFormat("en-US", { style: "percent" });
    const key = `<div class="legend">${FLOW.map((st) => `<span><i class="swatch s-${st}" aria-hidden="true"></i>${esc(SHORT[st])}</span>`).join("")}</div>`;
    const block = (c, i) => {
      const cells = c.states.map((st, k) => `<a class="cell s-${st}" href="#/replica/${encodeURIComponent(c.id)}/${k}" aria-label="Replica ${k}, ${LABEL[st].toLowerCase()}" title="Replica ${rep(k)}: ${LABEL[st]}">${k}${st === "failed" ? "!" : ""}</a>`).join("");
      const bad = count(c.states, "failed"), run = count(c.states, "running");
      return `<section class="round-block" id="round-${esc(c.id)}" aria-labelledby="t-${i}">
        <h2 class="round-title" id="t-${i}">${esc(roundName(c))}<span class="round-done num">${num(count(c.states, "assembled"))} of ${num(c.states.length)} assembled${run ? `, ${num(run)} running` : ""}${bad ? `, ${num(bad)} failed` : ""}</span></h2>
        <div class="split">
          <div><div class="cells">${cells}</div>
            <details><summary class="caps">Starting box</summary>${boxFacts(c)}</details></div>
          <div class="plots plots-wide">${ensemble(series[i], "Potential energy during assembly, kJ/mol")}</div>
        </div></section>`;
    };
    return `<h1>${esc(name)}</h1>
      ${d ? kv([["Sequence", `<span class="mono">${esc(d.sequence)}</span>`, 1], ["Length", num(d.length)], ["Net charge", num(d.net_charge)], ["Hydrophobic moment", num(d.hydrophobic_moment, f3)], ["Hydrophobic residues", num(d.fraction_hydrophobic, pc)], ["Isoelectric point", num(d.isoelectric_point, nf1)], ["Coarse-grained beads", num(d.cg_beads)], ["C-terminus", d.c_terminus]]) : ""}
      ${key}${rounds.map(block).join("")}`;
  }

  function replicaParts(d) {
    const m = d.manifest, run = d.run || {}, stages = run.stages || {};
    const facts = kv([["State", LABEL[d.state]], ["Beads", m ? num(m.total_beads) : ""], ["Box edge", m ? `${num(m.box_nm[0], nf1)} nm` : ""], ["Net charge", m ? num(m.net_charge) : ""], ["Peptide copies", m && m.peptide ? num(m.peptide.copies) : "0"], ["Starting oligomers", m && m.peptide ? num(m.peptide.oligomers) : "0"], ["Seed", m ? `<span class="mono">${esc(m.seed)}</span>` : "", 1]]);
    const stageRows = d.stages.map((name) => { const s = stages[name] || {}, note = s.reason || s.warning; return `<tr>${td(`<span class="mono">${esc(name)}</span>`)}${td(esc(s.status || "pending"))}${td(num(s.seconds, nf1), 1)}${td(num(s.ns_per_day, nf1), 1)}</tr>${note ? `<tr class="note"><td colspan="4">${esc(note)}</td></tr>` : ""}`; });
    const comp = m ? Object.entries(m.molecules).map(([k, v]) => `<tr>${td(`<span class="mono">${esc(k)}</span>`)}${td(num(v), 1)}</tr>`) : [];
    const events = d.events.slice(-40).reverse().map((e) => `<tr>${td(`<span class="mono">${esc((e.time || "").replace("T", " ").slice(0, 19))}</span>`)}${td(esc(e.event))}${td(esc(e.status))}${td(num(e.seconds, nf1), 1)}${td(`<div class="truncate">${esc(e.message || "")}</div>`)}</tr>`);
    const files = d.files.map((f) => `<tr>${td(`<span class="mono">${esc(f.name)}</span>`)}${td(bytes(f.bytes), 1)}</tr>`);
    return {
      facts,
      stages: table([["Stage"], ["Status"], ["Seconds", 1], ["ns/day", 1]], stageRows),
      comp: comp.length ? table([["Molecule"], ["Count", 1]], comp) : empty("No box yet.", "shaker build"),
      events: events.length ? table([["Time"], ["Event"], ["Status"], ["Seconds", 1], ["Message"]], events) : empty("No events yet."),
      files: files.length ? table([["File"], ["Size", 1]], files) : empty("No files yet."),
    };
  }

  function viewReplica(id, index, d) {
    const p = replicaParts(d), c = byId(id), n = c ? c.states.length : 0;
    const step = (i, word) => (c && i >= 0 && i < n ? `<a class="button" href="#/replica/${encodeURIComponent(id)}/${i}">${word}</a>` : "");
    return `<a class="button back" href="${roundHref(id)}">\u2190 ${esc(c ? `${peptideName(c)} \u00b7 ${roundName(c)}` : "Back")}</a>
      <div class="headline"><h1>Replica ${rep(index)}</h1><div class="toolbar">${step(index - 1, "Previous")}${step(index + 1, "Next")}</div></div>
      <div class="split">
        <section aria-labelledby="h-frame"><h2 id="h-frame">Latest frame</h2>
          <div class="viewer"><canvas id="canvas" tabindex="0" role="img" aria-label="Beads of the latest frame. Drag or use the arrow keys to rotate."></canvas>
          <div class="toolbar"><button class="button" type="button" data-view="side">Side</button><button class="button" type="button" data-view="top">Top</button><button class="button" type="button" data-view="front">Front</button><button class="button" type="button" id="water" aria-pressed="false">Water</button><span class="mono" id="source"></span></div></div>
        </section>
        <section aria-labelledby="h-box"><h2 id="h-box">Box</h2><div id="rep-facts">${p.facts}</div></section>
      </div>
      <h2>Energies</h2><div class="block" id="rep-plots">${replicaPlots(d.series)}</div>
      <div class="split">
        <section aria-labelledby="h-stages"><h2 id="h-stages">Stages</h2><div id="rep-stages">${p.stages}</div></section>
        <section aria-labelledby="h-comp"><h2 id="h-comp">Composition</h2><div id="rep-comp">${p.comp}</div></section>
      </div>
      <div class="split">
        <section aria-labelledby="h-events"><h2 id="h-events">Events</h2><div id="rep-events">${p.events}</div></section>
        <section aria-labelledby="h-files"><h2 id="h-files">Files</h2><div id="rep-files">${p.files}</div></section>
      </div>`;
  }

  const when = (seconds) => new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(seconds * 1000));

  function viewPeptides(rows) {
    if (!rows.length) return `<h1>Descriptors</h1>${empty("No peptide has been coarse-grained yet.", "shaker prepare")}`;
    const rounds = (name) => overview.conditions.filter((c) => c.peptide === name).length;
    const body = rows.map((r) => `<tr>${td(`<a href="${peptideHref(r.peptide)}">${esc(r.peptide)}</a>`)}${td(`<span class="mono">${esc(r.sequence)}</span>`)}${td(num(rounds(r.peptide)), 1)}${td(num(r.length), 1)}${td(num(r.net_charge), 1)}${td(num(r.hydrophobic_moment, new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 })), 1)}${td(num(r.fraction_hydrophobic, new Intl.NumberFormat("en-US", { style: "percent" })), 1)}${td(num(r.isoelectric_point, nf1), 1)}${td(num(r.cg_beads), 1)}${td(esc(r.c_terminus))}</tr>`);
    return `<h1>Descriptors</h1>${table([["Peptide"], ["Sequence"], ["Rounds", 1], ["Length", 1], ["Net charge", 1], ["Hydrophobic moment", 1], ["Hydrophobic", 1], ["pI", 1], ["Beads", 1], ["C-terminus"]], body)}`;
  }

  function viewEvents(data) {
    if (!data.events.length) return `<h1>Events</h1>${empty("Nothing has run yet.", "shaker build")}`;
    const body = data.events.slice().reverse().map((e) => `<tr>${td(`<span class="mono">${esc((e.time || "").replace("T", " ").slice(0, 19))}</span>`)}${td(esc(e.stage || ""))}${td(esc(e.event))}${td(esc(e.status))}${td(e.condition ? `<a href="#/replica/${encodeURIComponent(e.condition)}/${e.replica ?? 0}">${esc(labelOf(e.condition))}${e.replica === undefined || e.replica === null ? "" : ` · ${rep(e.replica)}`}</a>` : esc(e.peptide || ""))}${td(`<div class="truncate">${esc(e.message || e.reason || "")}</div>`)}</tr>`);
    return `<h1>Events</h1>${table([["Time"], ["Stage"], ["Event"], ["Status"], ["Target"], ["Message"]], body)}`;
  }

  // ---- bead viewer: orthographic, beads sorted back to front --------------------------------
  class Viewer {
    constructor(canvas) {
      this.canvas = canvas; this.ctx = canvas.getContext("2d");
      this.rx = 0; this.ry = 0; this.water = false; this.data = null;
      this.radius = [0.24, 0.19, 0.25, 0.22, 0.22, 0.2, 0.2];
      this.recolour();
      let drag = null;
      canvas.addEventListener("pointerdown", (e) => { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
      canvas.addEventListener("pointermove", (e) => { if (!drag) return; this.ry += (e.clientX - drag[0]) * 0.01; this.rx += (e.clientY - drag[1]) * 0.01; drag = [e.clientX, e.clientY]; this.draw(); });
      canvas.addEventListener("pointerup", () => { drag = null; });
      canvas.addEventListener("keydown", (e) => {
        const step = { ArrowLeft: [0, -0.15], ArrowRight: [0, 0.15], ArrowUp: [-0.15, 0], ArrowDown: [0.15, 0] }[e.key];
        if (!step) return; e.preventDefault(); this.rx += step[0]; this.ry += step[1]; this.draw();
      });
      this.observer = new ResizeObserver(() => this.draw()); this.observer.observe(canvas);
    }
    recolour() {
      this.colours = ["--orange", "--sidechain", "--coral", "--glycerol", "--tail", "--water", "--metal"].map(css);
      this.rim = css("--ink"); if (this.data) this.draw();
    }
    view(name) { [this.rx, this.ry] = { side: [0, 0], top: [Math.PI / 2, 0], front: [0, Math.PI / 2] }[name]; this.draw(); }
    load(data) {
      const n = data.kinds.length, box = data.box;
      this.data = data; this.pos = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) {
        const L = box[a], v = data.xyz[i * 3 + a];          // wrap into the box, centre on it
        this.pos[i * 3 + a] = (((v % L) + L) % L) - L / 2;
      }
      this.order = new Uint32Array(n); this.depth = new Float32Array(n); this.draw();
    }
    draw() {
      const c = this.canvas, ratio = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight, ctx = this.ctx;
      if (!w || !h) return;
      if (c.width !== Math.round(w * ratio) || c.height !== Math.round(h * ratio)) { c.width = Math.round(w * ratio); c.height = Math.round(h * ratio); }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, w, h);
      if (!this.data) return;
      const n = this.data.kinds.length, kinds = this.data.kinds, p = this.pos, scale = Math.min(w, h) / (Math.max(...this.data.box) * 1.5);
      const cx = Math.cos(this.rx), sx = Math.sin(this.rx), cy = Math.cos(this.ry), sy = Math.sin(this.ry);
      // the membrane normal (z) is drawn up the screen; rotation about the vertical axis first, then tilt
      const X = new Float32Array(n), Y = new Float32Array(n);
      let m = 0;
      for (let i = 0; i < n; i++) {
        if (kinds[i] === 5 && !this.water) continue;
        const x = p[i * 3], y = p[i * 3 + 1], z = p[i * 3 + 2];
        const x1 = x * cy + y * sy, y1 = -x * sy + y * cy;
        X[i] = x1; Y[i] = z * cx - y1 * sx; this.depth[i] = z * sx + y1 * cx; this.order[m++] = i;
      }
      const order = this.order.subarray(0, m); order.sort((a, b) => this.depth[b] - this.depth[a]);
      ctx.lineWidth = 0.6; ctx.strokeStyle = this.rim;
      for (let j = 0; j < m; j++) {
        const i = order[j], k = kinds[i], r = Math.max(1.2, this.radius[k] * scale);
        ctx.beginPath(); ctx.arc(w / 2 + X[i] * scale, h / 2 - Y[i] * scale, r, 0, 6.2832);
        ctx.globalAlpha = k === 5 ? 0.45 : 1; ctx.fillStyle = this.colours[k]; ctx.fill();
        ctx.globalAlpha = 0.35; ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    destroy() { this.observer.disconnect(); }
  }

  // ---- routing and refresh --------------------------------------------------------------------
  function parse() {
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
    if (parts[0] === "condition" && parts[1]) { const c = byId(parts[1]); return c ? { name: "peptide", id: peptideName(c), round: parts[1] } : { name: "overview" }; }
    if (parts[0] === "replica" && parts[1]) return { name: "replica", id: parts[1], index: Number(parts[2] || 0) };
    if (parts[0] === "peptide" && parts[1]) return { name: "peptide", id: parts[1], round: parts[2] || null };
    if (parts[0] === "peptides") return { name: "peptides" };
    if (parts[0] === "events") return { name: "events" };
    return { name: "overview" };
  }
  const key = (r) => `${r.name}|${r.id || ""}|${r.index ?? ""}|${r.round || ""}`;
  const setHtml = (id, html) => { const el = document.getElementById(id); if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } };

  async function render(fresh) {
    // a newer render (a click while a poll was in flight) makes this one stale: it must not paint
    const mine = ++turn, stale = () => mine !== turn;
    const r = parse();
    if (fresh || !route || key(route) !== key(r)) {
      route = r; fresh = true; structureSource = null;
      if (viewer) { viewer.destroy(); viewer = null; }
    }
    const fetched = await get("/api/overview");
    if (stale()) return;
    overview = fetched;
    if (r.name === "overview" && overview.conditions.length) { r.name = "peptide"; r.id = peptideName(overview.conditions[0]); r.round = null; }   // no landing page: the first peptide is the start
    setHtml("side", sideMenu(r));
    document.getElementById("version").textContent = `shaker ${overview.version}`;
    let html = null, title = "SHAKER";
    if (r.name === "overview") html = `<h1>Nothing to show yet</h1>${empty("config.yaml lists no peptide.", "shaker init")}`;
    else if (r.name === "peptide") {
      const rounds = overview.conditions.filter((c) => peptideName(c) === r.id);
      const [d, ...series] = await Promise.all([get("/api/peptides"), ...rounds.map((c) => get(`/api/round_series?condition=${encodeURIComponent(c.id)}`))]);
      if (stale()) return; html = viewPeptide(r.id, d.rows, series); title = r.id;
    }
    else if (r.name === "peptides") { const d = await get("/api/peptides"); if (stale()) return; html = viewPeptides(d.rows); title = "Peptides"; }
    else if (r.name === "events") { const d = await get("/api/events"); if (stale()) return; html = viewEvents(d); title = "Events"; }
    else {
      const query = `condition=${encodeURIComponent(r.id)}&replica=${r.index}`;
      const [detail, series] = await Promise.all([get(`/api/replica?${query}`), get(`/api/series?${query}`)]);
      if (stale()) return;
      detail.series = series;
      title = `${labelOf(r.id)} \u00b7 ${rep(r.index)}`;
      if (fresh) {
        main.innerHTML = viewReplica(r.id, r.index, detail);
        viewer = new Viewer(document.getElementById("canvas"));
        main.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => viewer.view(b.dataset.view)));
        const water = document.getElementById("water");
        water.addEventListener("click", () => { viewer.water = !viewer.water; water.setAttribute("aria-pressed", String(viewer.water)); viewer.draw(); });
      } else {
        const p = replicaParts(detail);
        setHtml("rep-facts", p.facts); setHtml("rep-plots", replicaPlots(series)); setHtml("rep-stages", p.stages); setHtml("rep-comp", p.comp); setHtml("rep-events", p.events); setHtml("rep-files", p.files);
      }
      const latest = detail.files.filter((f) => f.name.endsWith(".gro")).map((f) => `${f.name}:${f.bytes}`).join(",");
      if (viewer && latest && latest !== structureSource) {
        structureSource = latest;
        try { const s = await get(`/api/structure?${query}`); if (stale()) return; viewer.load(s); document.getElementById("source").textContent = s.source; }
        catch (e) { document.getElementById("source").textContent = e.message; }
      }
    }
    if (html !== null && main.dataset.html !== html) { main.innerHTML = html; main.dataset.html = html; }
    if (html === null) delete main.dataset.html;
    document.title = `${title} · SHAKER`;
    if (fresh) { main.style.animation = "none"; void main.offsetWidth; main.style.animation = ""; main.focus({ preventScroll: true }); const target = r.round && document.getElementById(`round-${r.round}`); if (target) target.scrollIntoView(); }
  }

  async function tick(fresh) {
    try {
      await render(fresh);
      live.className = "status live"; live.lastElementChild.textContent = "Live";
    } catch (error) {
      live.className = "status stale"; live.lastElementChild.textContent = `Not updating: ${error.message}`;
      if (fresh) main.innerHTML = `<h1>Nothing to show yet</h1>${empty(error.message, "shaker init")}`;
    }
    clearTimeout(timer); timer = setTimeout(() => { if (!document.hidden) tick(false); else timer = setTimeout(() => tick(false), POLL_MS); }, POLL_MS);
  }
  window.addEventListener("hashchange", () => { window.scrollTo(0, 0); tick(true); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(false); });
  tick(true);
})();
