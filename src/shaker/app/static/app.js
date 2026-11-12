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

  // ---- views ----------------------------------------------------------------------------
  function progress(states) {
    const total = states.length || 1;
    return `<div class="progress" role="img" aria-label="${STATES.filter((s) => s !== "unbuilt").map((s) => `${count(states, s)} ${LABEL[s].toLowerCase()}`).join(", ")}">${STATES.filter((s) => s !== "unbuilt")
      .map((s) => `<i class="s-${s}" style="width:${(100 * count(states, s)) / total}%"></i>`).join("")}</div>`;
  }

  function viewOverview() {
    const o = overview, all = o.conditions.flatMap((c) => c.states), t = o.totals;
    const built = all.length - count(all, "unbuilt");
    const rows = o.conditions.map((c) => `<tr>${td(`<a class="mono" href="#/condition/${encodeURIComponent(c.id)}">${esc(c.id)}</a>`)}${td(esc(c.lipids))}${td(esc(c.peptide_to_lipid), 1)}${td(num(c.beads), 1)}${td(progress(c.states))}${td(`${count(c.states, "assembled")} / ${c.states.length}`, 1)}${td(count(c.states, "failed") || "", 1)}${td(num(c.ns_per_day, nf1), 1)}</tr>`);
    return `<div class="crumbs">Campaign</div><h1>${esc(o.name)}</h1>
      <dl class="tiles">${tile("Conditions", num(t.conditions))}${tile("Simulations", num(t.simulations))}${tile("Built", num(built))}${tile("Assembled", num(count(all, "assembled")))}${tile("Failed", num(count(all, "failed")))}${tile("Mean ns per day", num(t.ns_per_day, nf1))}${tile("Days of one slot", num(t.compute_days, nf1))}${tile("Trajectories, GB", num(t.trajectory_gb, nf1))}</dl>
      <h2>Conditions</h2>
      ${built ? "" : empty("No boxes built yet.", "shaker build")}
      ${table([["Condition"], ["Lipids"], ["P:L", 1], ["Beads", 1], ["Progress"], ["Assembled", 1], ["Failed", 1], ["ns/day", 1]], rows)}`;
  }

  function legend() {
    return `<div class="legend">${STATES.map((s) => `<span><i class="swatch s-${s}" aria-hidden="true">${LETTER[s]}</i>${esc(LABEL[s])}</span>`).join("")}</div>`;
  }

  function viewCondition(id) {
    const c = overview.conditions.find((x) => x.id === id);
    if (!c) return `<h1>Unknown condition</h1>${empty("This condition is not in config.yaml.")}`;
    const cells = c.states.map((s, i) => `<a class="cell s-${s}" href="#/replica/${encodeURIComponent(id)}/${i}" aria-label="Replica ${i}, ${LABEL[s].toLowerCase()}" title="${LABEL[s]}">${i}${s === "failed" ? "!" : ""}</a>`).join("");
    return `<div class="crumbs"><a href="#/">Overview</a></div><h1 class="mono">${esc(id)}</h1>
      <dl class="tiles">${tile("Peptide", c.peptide)}${tile("Membrane", c.membrane)}${tile("Copies", num(c.copies))}${tile("Oligomer size", num(c.oligomer_size))}${tile("Beads", num(c.beads))}${tile("Box, nm", num(c.box_nm, nf1))}${tile("Assembled", `${count(c.states, "assembled")} / ${c.states.length}`)}${tile("Mean ns per day", num(c.ns_per_day, nf1))}</dl>
      <h2>Replicas</h2>${legend()}<div class="cells">${cells}</div>`;
  }

  function replicaParts(d) {
    const m = d.manifest, run = d.run || {}, stages = run.stages || {};
    const tiles = `${tile("State", LABEL[d.state])}${tile("Beads", m ? num(m.total_beads) : "")}${tile("Box, nm", m ? num(m.box_nm[0], nf1) : "")}${tile("Net charge", m ? num(m.net_charge) : "")}${tile("Peptide copies", m && m.peptide ? num(m.peptide.copies) : "0")}${tile("Starting oligomers", m && m.peptide ? num(m.peptide.oligomers) : "0")}`;
    const stageRows = d.stages.map((name) => { const s = stages[name] || {}; return `<tr>${td(`<span class="mono">${esc(name)}</span>`)}${td(esc(s.status || "pending"))}${td(num(s.seconds, nf1), 1)}${td(num(s.ns_per_day, nf1), 1)}${td(esc(s.reason || s.warning || ""))}</tr>`; });
    const comp = m ? Object.entries(m.molecules).map(([k, v]) => `<tr>${td(`<span class="mono">${esc(k)}</span>`)}${td(num(v), 1)}</tr>`) : [];
    const events = d.events.slice(-40).reverse().map((e) => `<tr>${td(`<span class="mono">${esc((e.time || "").replace("T", " ").slice(0, 19))}</span>`)}${td(esc(e.event))}${td(esc(e.status))}${td(num(e.seconds, nf1), 1)}${td(`<div class="truncate">${esc(e.message || "")}</div>`)}</tr>`);
    const files = d.files.map((f) => `<tr>${td(`<span class="mono">${esc(f.name)}</span>`)}${td(bytes(f.bytes), 1)}</tr>`);
    return {
      tiles,
      stages: table([["Stage"], ["Status"], ["Seconds", 1], ["ns/day", 1], ["Note"]], stageRows),
      comp: comp.length ? table([["Molecule"], ["Count", 1]], comp) : empty("No box yet.", "shaker build"),
      events: events.length ? table([["Time"], ["Event"], ["Status"], ["Seconds", 1], ["Message"]], events) : empty("No events yet."),
      files: files.length ? table([["File"], ["Size", 1]], files) : empty("No files yet."),
      seed: m ? String(m.seed) : "",
    };
  }

  function viewReplica(id, index, d) {
    const p = replicaParts(d);
    return `<div class="crumbs"><a href="#/">Overview</a> / <a class="mono" href="#/condition/${encodeURIComponent(id)}">${esc(id)}</a></div>
      <h1>Replica ${String(index).padStart(3, "0")}</h1>
      <dl class="tiles" id="rep-tiles">${p.tiles}</dl>
      <div class="split">
        <section aria-labelledby="h-frame"><h2 id="h-frame">Latest frame</h2>
          <div class="viewer"><canvas id="canvas" tabindex="0" role="img" aria-label="Beads of the latest frame. Drag or use the arrow keys to rotate."></canvas>
          <div class="toolbar"><button class="button" type="button" data-view="side">Side</button><button class="button" type="button" data-view="top">Top</button><button class="button" type="button" data-view="front">Front</button><button class="button" type="button" id="water" aria-pressed="false">Water</button><span class="mono" id="source"></span></div></div>
        </section>
        <section aria-labelledby="h-stages"><h2 id="h-stages">Stages</h2><div id="rep-stages">${p.stages}</div>
          <h2>Composition</h2><div id="rep-comp">${p.comp}</div></section>
      </div>
      <h2>Events</h2><div id="rep-events">${p.events}</div>
      <h2>Files</h2><div id="rep-files">${p.files}</div>
      <p class="mono" id="rep-seed">seed ${esc(p.seed)}</p>`;
  }

  const when = (seconds) => new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(seconds * 1000));
  const meter = (done, total, cls = "s-assembled") => `<div class="progress" role="img" aria-label="${done} of ${total}"><i class="${cls}" style="width:${total ? (100 * done) / total : 0}%"></i></div>`;

  function viewProgress(p) {
    const stepState = (s) => (s.total && s.done >= s.total ? "Done" : s.done ? "In progress" : "Not started");
    const steps = p.steps.map((s, i) => `<tr>${td(`<span class="num">${i + 1}</span>`)}${td(esc(s.step))}${td(stepState(s))}${td(meter(s.done, s.total))}${td(`${num(s.done)}\u2009/\u2009${num(s.total)}`, 1)}${td(s.total && s.done >= s.total ? "" : `<code>${esc(s.command)}</code>`)}</tr>`);
    const running = p.running.map((r) => `<tr>${td(`<a class="mono" href="#/replica/${encodeURIComponent(r.condition)}/${r.replica}">${esc(r.condition)} / ${r.replica}</a>`)}${td(`<span class="mono">${esc(r.stage)}</span>`)}${td(r.fraction === null ? "" : meter(Math.round(r.fraction * 1000), 1000, "s-running"))}${td(r.fraction === null ? "" : num(r.fraction, new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 })), 1)}</tr>`);
    const matrix = p.conditions.map((c) => `<tr>${td(`<a class="mono" href="#/condition/${encodeURIComponent(c.condition)}">${esc(c.condition)}</a>`)}${td(`${num(c.built)}\u2009/\u2009${num(c.replicas)}`, 1)}${p.stages.map((s) => td(num(c[s]), 1)).join("")}${td(c.failed || "", 1)}</tr>`);
    const kinds = p.outputs.kinds.map((k) => `<tr>${td(esc(k.kind))}${td(num(k.files), 1)}${td(bytes(k.bytes), 1)}</tr>`);
    const totalBytes = p.outputs.kinds.reduce((a, k) => a + k.bytes, 0);
    const tables = p.outputs.tables.map((t) => `<tr>${td(`<span class="mono">results/${esc(t.name)}</span>`)}${td(bytes(t.bytes), 1)}${td(esc(when(t.modified)))}</tr>`);
    const pace = p.pace, assembled = p.steps[p.steps.length - 1];
    return `<div class="crumbs">Campaign</div><h1>Progress</h1>
      <dl class="tiles">${tile("Assembled", `${num(assembled.done)} / ${num(assembled.total)}`)}${tile("Running now", num(p.running.length))}${tile("Failed", num(p.failed))}${tile("Finished, last 6 h", num(pace.finished_last_6h))}${tile("Replicas per hour", num(pace.per_hour, nf1))}${tile("Hours left at this pace", num(pace.hours_left, nf1))}${tile("On disk", bytes(totalBytes))}${tile("Disk free", bytes(p.outputs.disk_free))}</dl>
      <h2>Steps</h2>${table([["#"], ["Step"], ["State"], ["Progress"], ["Done", 1], ["Next command"]], steps)}
      <h2>Running now</h2>${running.length ? table([["Replica"], ["Stage"], ["Progress"], ["Done", 1]], running) : empty("No replica is running.", "shaker run")}
      <h2>Stages by condition</h2>${table([["Condition"], ["Built", 1], ...p.stages.map((s) => [s, 1]), ["Failed", 1]], matrix)}
      <h2>Outputs on disk</h2>${kinds.length ? table([["Kind"], ["Files", 1], ["Size", 1]], kinds) : empty("Nothing written yet.", "shaker build")}
      <h2>Result tables</h2>${tables.length ? table([["Table"], ["Size", 1], ["Updated"]], tables) : empty("No result table yet.", "shaker prepare")}
      <p class="crumbs">File inventory read ${esc(when(p.outputs.scanned))}</p>`;
  }

  function viewPeptides(rows) {
    if (!rows.length) return `<h1>Peptides</h1>${empty("No peptide has been coarse-grained yet.", "shaker prepare")}`;
    const body = rows.map((r) => `<tr>${td(esc(r.peptide))}${td(`<span class="mono">${esc(r.sequence)}</span>`)}${td(num(r.length), 1)}${td(num(r.net_charge), 1)}${td(num(r.hydrophobic_moment, new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 })), 1)}${td(num(r.fraction_hydrophobic, new Intl.NumberFormat("en-US", { style: "percent" })), 1)}${td(num(r.isoelectric_point, nf1), 1)}${td(num(r.cg_beads), 1)}${td(esc(r.c_terminus))}</tr>`);
    return `<h1>Peptides</h1>${table([["Peptide"], ["Sequence"], ["Length", 1], ["Net charge", 1], ["Hydrophobic moment", 1], ["Hydrophobic", 1], ["pI", 1], ["Beads", 1], ["C-terminus"]], body)}`;
  }

  function viewEvents(data) {
    if (!data.events.length) return `<h1>Events</h1>${empty("Nothing has run yet.", "shaker build")}`;
    const body = data.events.slice().reverse().map((e) => `<tr>${td(`<span class="mono">${esc((e.time || "").replace("T", " ").slice(0, 19))}</span>`)}${td(esc(e.stage || ""))}${td(esc(e.event))}${td(esc(e.status))}${td(e.condition ? `<a class="mono" href="#/replica/${encodeURIComponent(e.condition)}/${e.replica ?? 0}">${esc(e.condition)} / ${esc(e.replica ?? "")}</a>` : esc(e.peptide || ""))}${td(`<div class="truncate">${esc(e.message || e.reason || "")}</div>`)}</tr>`);
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
    if (parts[0] === "condition" && parts[1]) return { name: "condition", id: parts[1] };
    if (parts[0] === "replica" && parts[1]) return { name: "replica", id: parts[1], index: Number(parts[2] || 0) };
    if (parts[0] === "progress") return { name: "progress" };
    if (parts[0] === "peptides") return { name: "peptides" };
    if (parts[0] === "events") return { name: "events" };
    return { name: "overview" };
  }
  const key = (r) => `${r.name}|${r.id || ""}|${r.index ?? ""}`;
  const setHtml = (id, html) => { const el = document.getElementById(id); if (el && el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; } };

  async function render(fresh) {
    // a newer render (a click while a poll was in flight) makes this one stale: it must not paint
    const mine = ++turn, stale = () => mine !== turn;
    const r = parse();
    if (fresh || !route || key(route) !== key(r)) {
      route = r; fresh = true; structureSource = null;
      if (viewer) { viewer.destroy(); viewer = null; }
    }
    document.querySelectorAll("nav a").forEach((a) => {
      const on = a.dataset.nav === r.name || (a.dataset.nav === "overview" && (r.name === "condition" || r.name === "replica"));
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    const fetched = await get("/api/overview");
    if (stale()) return;
    overview = fetched;
    document.getElementById("version").textContent = `shaker ${overview.version}`;
    let html = null, title = overview.name;
    if (r.name === "overview") html = viewOverview();
    else if (r.name === "condition") { html = viewCondition(r.id); title = r.id; }
    else if (r.name === "progress") { const d = await get("/api/progress"); if (stale()) return; html = viewProgress(d); title = "Progress"; }
    else if (r.name === "peptides") { const d = await get("/api/peptides"); if (stale()) return; html = viewPeptides(d.rows); title = "Peptides"; }
    else if (r.name === "events") { const d = await get("/api/events"); if (stale()) return; html = viewEvents(d); title = "Events"; }
    else {
      const query = `condition=${encodeURIComponent(r.id)}&replica=${r.index}`;
      const detail = await get(`/api/replica?${query}`);
      if (stale()) return;
      title = `Replica ${r.index}`;
      if (fresh) {
        main.innerHTML = viewReplica(r.id, r.index, detail);
        viewer = new Viewer(document.getElementById("canvas"));
        main.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => viewer.view(b.dataset.view)));
        const water = document.getElementById("water");
        water.addEventListener("click", () => { viewer.water = !viewer.water; water.setAttribute("aria-pressed", String(viewer.water)); viewer.draw(); });
      } else {
        const p = replicaParts(detail);
        setHtml("rep-tiles", p.tiles); setHtml("rep-stages", p.stages); setHtml("rep-comp", p.comp); setHtml("rep-events", p.events); setHtml("rep-files", p.files);
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
    if (fresh) { main.style.animation = "none"; void main.offsetWidth; main.style.animation = ""; main.focus({ preventScroll: true }); }
  }

  async function tick(fresh) {
    try {
      await render(fresh);
      live.className = "status live"; live.lastElementChild.textContent = "Live";
    } catch (error) {
      live.className = "status stale"; live.lastElementChild.textContent = `Not updating: ${error.message}`;
      if (fresh) main.innerHTML = `<div class="crumbs">Campaign</div><h1>Nothing to show yet</h1>${empty(error.message, "shaker init")}`;
    }
    clearTimeout(timer); timer = setTimeout(() => { if (!document.hidden) tick(false); else timer = setTimeout(() => tick(false), POLL_MS); }, POLL_MS);
  }
  window.addEventListener("hashchange", () => { window.scrollTo(0, 0); tick(true); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(false); });
  tick(true);
})();
