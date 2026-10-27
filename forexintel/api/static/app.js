"use strict";
/* Forex Intelligence dashboard. Plain ES2020, no build step. All text from the API is escaped before insertion. */
const SYMS = ["XAUUSD", "EURUSD", "GBPUSD"];
const API = "/api/v1";
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
let tz = localStorage.getItem("tz") || "UTC";
let token = sessionStorage.getItem("token") || "";
let chartRef = null;

/* ---------- helpers ---------- */
function toast(msg, ms = 3200) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), ms); }
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) };
  const r = await fetch(API + path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (r.status === 401) { askToken(); throw new Error("API token required"); }
  if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch (e) { /* ignore */ } throw new Error(d || r.statusText); }
  return r.json();
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });
function askToken() { const t = prompt("API token (API_TOKEN in .env):", token); if (t !== null) { token = t; sessionStorage.setItem("token", t); location.reload(); } }
const fmtTime = (iso) => { if (!iso) return "-"; const d = new Date(iso); if (isNaN(d)) return esc(iso); return d.toLocaleString("en-GB", { timeZone: tz, hour12: false, month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) + " " + (tz === "UTC" ? "UTC" : "PKT"); };
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "-" : Number(v).toFixed(d);
const pct = (v, d = 1) => (v === null || v === undefined) ? "-" : (100 * v).toFixed(d) + "%";
const sgn = (v, d = 3) => (v === null || v === undefined) ? "-" : `<span class="${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}</span>`;
const px = (sym, v) => (v === null || v === undefined) ? "-" : Number(v).toFixed(sym === "XAUUSD" ? 2 : 5);
const badge = (txt, cls = "neutral") => `<span class="badge ${cls}">${esc(txt)}</span>`;

function stateBadges(res) {
  const out = [];
  if (!res || res.trade_action === "CANNOT_ASSESS" || ["NO_DATA", "INSUFFICIENT_DATA"].includes(res.feed_status)) out.push(badge("INSUFFICIENT DATA", "bad"));
  else if (res.data_mode === "DEMO") out.push(badge("DEMO", "demo"));
  else if (res.feed_status === "HEALTHY") out.push(badge("LIVE", "live"));
  else if (res.feed_status === "MARKET_CLOSED") out.push(badge("MARKET CLOSED - LAST UPDATED", "info"));
  else if (res.feed_status === "STALE") out.push(badge("DELAYED - LAST UPDATED", "warn"));
  else if (res.feed_status === "DEGRADED") out.push(badge("DEGRADED", "warn"));
  else out.push(badge(res.feed_status || "OFFLINE", "bad"));
  if (res && !res.model_version) out.push(badge("MODEL NOT READY", "neutral"));
  return out.join(" ");
}
function actionClass(a) { return a === "LONG" ? "buy" : a === "SHORT" ? "sell" : a === "NO_TRADE" ? "wait" : "na"; }
const chips = (arr, cls) => (arr || []).map((x) => `<span class="chip ${cls}">${esc(x)}</span>`).join("");
const spinner = '<span class="spin"></span>';

/* ---------- small SVG charts (no dependencies) ---------- */
function lineSvg(points, { w = 760, h = 220, label = "" } = {}) {
  if (!points || points.length < 2) return '<div class="empty">not enough data</div>';
  const ys = points.map((p) => p[1]); const mn = Math.min(0, ...ys), mx = Math.max(0, ...ys); const span = (mx - mn) || 1;
  const X = (i) => 40 + (i / (points.length - 1)) * (w - 50), Y = (v) => h - 24 - ((v - mn) / span) * (h - 40);
  const d = points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const zero = Y(0);
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}"><line x1="40" x2="${w - 10}" y1="${zero}" y2="${zero}" stroke="#8a99ae" stroke-dasharray="4 4"/>
  <path d="${d}" fill="none" stroke="#4c9aff" stroke-width="2"/><text x="6" y="14" fill="#8a99ae" font-size="11">${esc(label)} (R)</text>
  <text x="6" y="${Y(mx) + 4}" fill="#8a99ae" font-size="10">${mx.toFixed(1)}</text><text x="6" y="${Y(mn) + 4}" fill="#8a99ae" font-size="10">${mn.toFixed(1)}</text></svg>`;
}
function reliabilitySvg(bins) {
  if (!bins || !bins.length) return '<div class="empty">no calibration data</div>';
  const w = 320, h = 260, X = (v) => 36 + v * (w - 50), Y = (v) => h - 30 - v * (h - 50);
  const pts = bins.map((b) => `<circle cx="${X(b.p_mean)}" cy="${Y(b.obs)}" r="${Math.min(10, 3 + Math.sqrt(b.n) / 2)}" fill="#4c9aff" opacity=".85"><title>pred ${b.p_mean.toFixed(2)} obs ${b.obs.toFixed(2)} n=${b.n}</title></circle>`).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="calibration"><line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" stroke="#8a99ae" stroke-dasharray="4 4"/>${pts}
  <text x="${w / 2 - 40}" y="${h - 6}" fill="#8a99ae" font-size="11">predicted probability</text><text x="4" y="14" fill="#8a99ae" font-size="11">observed frequency</text></svg>`;
}

/* ---------- jobs ---------- */
async function runJob(path, body, label) {
  const { job_id } = await post(path, body);
  toast(`${label} started: ${job_id}`);
  return job_id;
}
async function jobsPanel(target) {
  const list = await api("/jobs?limit=8");
  target.innerHTML = list.length ? `<div class="tablewrap"><table><thead><tr><th>Job</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${list.map((j) =>
    `<tr><td class="mono">${esc(j.id)}</td><td>${badge(j.status, j.status === "DONE" ? "good" : j.status === "FAILED" ? "bad" : "warn")}</td><td>${fmtTime(new Date(j.created_ts * 1000).toISOString())}</td><td><button data-job="${esc(j.id)}">Log</button></td></tr>`).join("")}</tbody></table></div><pre class="log" id="joblog" hidden></pre>` : '<div class="empty">no jobs yet</div>';
  target.querySelectorAll("[data-job]").forEach((b) => b.onclick = async () => { const j = await api("/jobs/" + b.dataset.job); const pre = $("#joblog", target); pre.hidden = false; pre.textContent = (j.log || "(no log)") + "\n" + JSON.stringify(j.result || {}, null, 1).slice(0, 1800); });
}

/* ---------- 1. Command Center ---------- */
function card(sym) { return `<div class="card" id="card-${sym}"><div class="card-h"><div><div class="sym">${sym}</div></div><div>${spinner}loading</div></div></div>`; }
function fillCard(sym, r) {
  const el = $("#card-" + sym); if (!el) return;
  if (r.error) { el.innerHTML = `<div class="card-h"><div class="sym">${sym}</div>${badge("OFFLINE", "bad")}</div><div class="notice bad">${esc(r.error)}</div>`; return; }
  const f = r.directional_forecast || {}; const p = r.price; const q = r.data_quality || {};
  const tradable = ["LONG", "SHORT"].includes(r.trade_action);
  el.innerHTML = `
  <div class="card-h"><div><div class="sym">${esc(r.display || sym)}</div><div class="sub" style="margin:0">as of ${fmtTime(r.asof_utc)}</div></div><div>${stateBadges(r)}</div></div>
  ${p ? `<div class="row"><span>Bid / Ask</span><span class="mono">${px(sym, p.bid)} / ${px(sym, p.ask)}</span></div>
  <div class="row"><span>Spread ${p.spread_is_real ? "" : "(assumed)"}</span><span class="mono">${px(sym, p.spread)}</span></div>` : ""}
  <div class="row"><span>Feed age</span><span>${q.age_minutes ?? "-"} min (${esc(q.status)})</span></div>
  <div class="row"><span>Session / Regime</span><span>${esc(r.session || "-")} / ${esc(r.regime || "-")}</span></div>
  <div class="row"><span>Forecast (${f.horizon_m5_bars || 6} M5 bars)</span><span><b class="${f.bias === "UP" ? "pos" : f.bias === "DOWN" ? "neg" : ""}">${esc(f.bias || "-")}</b> ${f.probability != null ? "P(up) " + num(f.probability) : badge(f.status || "n/a")}</span></div>
  <div class="action ${tradable ? actionClass(r.trade_action) : r.trade_action === "CANNOT_ASSESS" ? "na" : "wait"}">${esc(r.display_action)}</div>
  ${tradable ? `<div class="row"><span>Strategy</span><span>${esc(r.candidate_strategy)}</span></div>
  <div class="row"><span>Entry zone</span><span class="mono">${(r.entry_zone || []).map((v) => px(sym, v)).join(" - ")}</span></div>
  <div class="row"><span>SL</span><span class="mono">${px(sym, r.sl)}</span></div>
  <div class="row"><span>TP1 / TP2 / TP3</span><span class="mono">${px(sym, r.tp1)} / ${px(sym, r.tp2)} / ${px(sym, r.tp3)}</span></div>
  <div class="row"><span>R:R / exp. move</span><span>1:${num(r.rr?.tp1, 1)} / ${num(r.expected_move_pct?.tp1, 3)}%</span></div>
  <div class="row"><span>Probability</span><span>${r.probability?.value != null ? num(r.probability.value) : "-"} ${badge(r.probability?.status || "")}</span></div>
  <div class="row"><span>Valid until</span><span>${fmtTime(r.valid_until_utc)}</span></div>` : ""}
  <div class="chips">${chips(r.blockers, "block")}${chips(r.warnings, "warnc")}</div>
  <ul class="reasons">${(r.reasons || []).slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
  <div class="btns"><button class="primary" data-act="signal">Get signal</button><button data-act="sync">Sync + signal</button><a href="#/symbol?s=${sym}"><button>Chart</button></a></div>`;
  $('[data-act="signal"]', el).onclick = () => refreshCard(sym, false);
  $('[data-act="sync"]', el).onclick = () => refreshCard(sym, true);
}
async function loadCard(sym) { try { fillCard(sym, await api(`/markets/${sym}/analysis`)); } catch (e) { fillCard(sym, { error: e.message }); } }
async function refreshCard(sym, sync) {
  const el = $("#card-" + sym); el.querySelector(".btns").innerHTML = `${spinner}${sync ? "syncing" : "analysing"}...`;
  try { const r = await post("/signals/analyze", { symbol: sym, auto_sync: sync }); fillCard(sym, r.analysis); toast(`${sym}: ${r.analysis.display_action}`); } catch (e) { fillCard(sym, { error: e.message }); }
}
async function viewCommand() {
  view.innerHTML = `<h1>Command Center</h1><p class="sub">Directional forecast and trade authorization are separate. NO_TRADE / WAIT is a valid, expected output. Paper trading only.</p>
  <div class="bar"><button class="primary" id="b-sync">Sync all (job)</button><button id="b-mon-start">Start monitor</button><button id="b-mon-pause">Pause monitor</button><button id="b-refresh">Refresh</button><span id="mon-state" class="badge neutral">monitor: ?</span></div>
  <div class="grid cards">${SYMS.map(card).join("")}</div><h3>Recent jobs</h3><div id="jobs"></div>`;
  SYMS.forEach(loadCard);
  const mon = async () => { try { const m = await api("/monitor"); $("#mon-state").className = "badge " + (m.running ? "live" : "neutral"); $("#mon-state").textContent = `monitor: ${m.running ? "running" : "paused"} (${m.cycles} cycles${m.last_error ? ", error" : ""})`; } catch (e) { /* ignore */ } };
  mon(); jobsPanel($("#jobs"));
  $("#b-sync").onclick = async () => { try { await runJob("/jobs/backfill", {}, "Sync"); jobsPanel($("#jobs")); } catch (e) { toast(e.message); } };
  $("#b-mon-start").onclick = async () => { await post("/monitor/start"); mon(); toast("monitor started (runs only while this server is running)"); };
  $("#b-mon-pause").onclick = async () => { await post("/monitor/pause"); mon(); };
  $("#b-refresh").onclick = () => { SYMS.forEach(loadCard); jobsPanel($("#jobs")); mon(); };
}

/* ---------- 2. Signal Explorer ---------- */
async function viewSignals() {
  view.innerHTML = `<h1>Signal Explorer</h1><p class="sub">Every analysis is stored, including NO_TRADE, with its blockers and model version.</p>
  <div class="bar"><select id="f-sym"><option value="">All symbols</option>${SYMS.map((s) => `<option>${s}</option>`).join("")}</select>
  <select id="f-act"><option value="">All actions</option><option>LONG</option><option>SHORT</option><option>NO_TRADE</option></select><button id="f-go" class="primary">Apply</button><button id="f-csv">Export CSV</button></div><div id="sig-table"></div>`;
  let rows = [];
  const load = async () => {
    const q = new URLSearchParams({ limit: 300 }); if ($("#f-sym").value) q.set("symbol", $("#f-sym").value); if ($("#f-act").value) q.set("action", $("#f-act").value);
    rows = await api("/signals?" + q);
    $("#sig-table").innerHTML = rows.length ? `<div class="tablewrap"><table><thead><tr><th>As of</th><th>Symbol</th><th>Decision</th><th>Strategy</th><th>Bias</th><th>Regime</th><th>Entry/SL/TP1</th><th>Prob</th><th>EV R</th><th>Blockers</th><th>Model</th></tr></thead><tbody>${rows.map((r) =>
      `<tr><td>${fmtTime(r.asof_utc)}</td><td>${esc(r.symbol)}</td><td>${esc(r.display_action || r.action)}</td><td>${esc(r.strategy || "-")}</td><td>${esc(r.bias || "-")}</td><td>${esc(r.regime || "-")}</td>
      <td class="mono">${r.entry != null ? px(r.symbol, r.entry) + " / " + px(r.symbol, r.sl) + " / " + px(r.symbol, r.tp1) : "-"}</td><td class="num">${r.prob != null ? num(r.prob) : "-"}</td><td class="num">${r.ev_r != null ? num(r.ev_r) : "-"}</td>
      <td>${chips(r.blockers, "block")}</td><td class="mono">${esc((r.model_version || "rules-only").slice(-22))}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">No analyses yet. Use Get signal on the Command Center.</div>';
  };
  $("#f-go").onclick = load; $("#f-csv").onclick = () => {
    const cols = ["asof_utc", "symbol", "action", "strength", "strategy", "bias", "regime", "entry", "sl", "tp1", "prob", "ev_r", "blockers", "model_version"];
    const csv = [cols.join(",")].concat(rows.map((r) => cols.map((c) => JSON.stringify(Array.isArray(r[c]) ? r[c].join("|") : r[c] ?? "")).join(","))).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "signals.csv"; a.click();
  };
  load();
}

/* ---------- 3. Symbol Intelligence ---------- */
async function viewSymbol() {
  const qs = new URLSearchParams(location.hash.split("?")[1] || ""); const sym0 = qs.get("s") || "XAUUSD";
  view.innerHTML = `<h1>Symbol Intelligence</h1><p class="sub">Overlays use the same deterministic definitions as the strategies (docs/algorithm-rules.md). They are chart-derived hypotheses, not order-book data.</p>
  <div class="bar"><select id="s-sym">${SYMS.map((s) => `<option ${s === sym0 ? "selected" : ""}>${s}</option>`).join("")}</select>
  <select id="s-tf"><option>M5</option><option>M15</option><option>H1</option></select>
  ${["BOS/CHoCH", "Sweeps", "Swing/Asia levels", "FVG", "Order blocks"].map((o, i) => `<label><input type="checkbox" class="ov" data-i="${i}" checked> ${o}</label>`).join(" ")}</div>
  <div class="grid cols2" style="grid-template-columns:2fr 1fr"><div><div id="chart"></div><div id="chart-note" class="sub"></div></div><div id="side" class="card"><div class="empty">${spinner}analysis...</div></div></div>`;
  let data = null, chart = null, series = null, lines = [];
  const draw = () => {
    if (typeof LightweightCharts === "undefined") { $("#chart").innerHTML = '<div class="empty">Chart library could not be loaded (offline?). Data is available via the API.</div>'; return; }
    const el = $("#chart"); el.innerHTML = ""; const dark = true;
    chart = LightweightCharts.createChart(el, { layout: { background: { color: "transparent" }, textColor: "#8a99ae" }, grid: { vertLines: { color: "#1c2636" }, horzLines: { color: "#1c2636" } }, timeScale: { timeVisible: true, secondsVisible: false }, autoSize: true });
    series = chart.addCandlestickSeries({ upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350" });
    series.setData(data.candles); lines = []; applyOverlays(); chart.timeScale().fitContent(); chartRef = chart;
  };
  const applyOverlays = () => {
    if (!series) return; lines.forEach((l) => series.removePriceLine(l)); lines = [];
    const on = [...document.querySelectorAll(".ov")].map((c) => c.checked); const o = data.overlays;
    if (!o) { series.setMarkers([]); $("#chart-note").textContent = data.overlay_error || "overlays need > 2700 stored M5 bars"; return; }
    series.setMarkers(o.markers.filter((m) => (m.kind.startsWith("bos") || m.kind.startsWith("choch")) ? on[0] : on[1]).filter((m) => data.candles.length && m.time >= data.candles[0].time));
    if (on[2]) o.lines.forEach((l) => lines.push(series.createPriceLine({ price: l.price, color: "#8a99ae", lineWidth: 1, lineStyle: 2, title: l.label })));
    o.zones.forEach((z) => { const isFvg = z.type.startsWith("FVG"); if ((isFvg && !on[3]) || (!isFvg && !on[4])) return; const bull = z.type.endsWith("BULL"); const col = bull ? "#26a69a" : "#ef5350";
      [z.lo, z.hi].forEach((p, k) => lines.push(series.createPriceLine({ price: p, color: col, lineWidth: 1, lineStyle: 3, title: k ? z.type.replace("_", " ") : "" }))); });
    $("#chart-note").textContent = `${data.candles.length} closed ${data.timeframe} bars - data: ${data.lineage.history_provider}${data.lineage.live_provider ? " + " + data.lineage.live_provider : ""}`;
  };
  const load = async () => {
    const s = $("#s-sym").value, tf = $("#s-tf").value;
    try { data = await api(`/markets/${s}/candles?timeframe=${tf}&limit=500`); } catch (e) { $("#chart").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (!data.candles.length) { $("#chart").innerHTML = '<div class="empty">No candles stored. Run a sync from the Command Center.</div>'; } else draw();
    try {
      const a = await api(`/markets/${s}/analysis`); const f = a.directional_forecast || {};
      $("#side").innerHTML = `<h2>${esc(a.display)} ${stateBadges(a)}</h2><div class="row"><span>Regime</span><span>${esc(a.regime)} / ${esc(a.vol_state)}</span></div><div class="row"><span>Session</span><span>${esc(a.session)}</span></div>
      <div class="row"><span>Forecast</span><span>${esc(f.bias)} ${f.probability != null ? num(f.probability) : badge(f.status)}</span></div><div class="row"><span>Decision</span><span><b>${esc(a.display_action)}</b></span></div>
      <h3>Why</h3><ul class="reasons">${(a.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul><div class="chips">${chips(a.blockers, "block")}${chips(a.warnings, "warnc")}</div>
      <h3>Candidates (all logged)</h3>${(a.candidates || []).length ? (a.candidates || []).map((c) => `<div class="row"><span>${esc(c.strategy)} ${esc(c.side)}</span><span>${c.edge_ok ? badge("edge ok", "good") : badge("no validated edge", "warn")}</span></div>`).join("") : '<div class="sub">none on this bar</div>'}
      <h3>Forecast by horizon</h3>${Object.keys(f.by_horizon || {}).length ? Object.entries(f.by_horizon).map(([h, v]) => `<div class="row"><span>${h} bars</span><span>${esc(v.bias)} (${num(v.p_up)})</span></div>`).join("") : '<div class="sub">no approved direction model: rules-based bias only, probability not calibrated</div>'}`;
    } catch (e) { $("#side").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  };
  $("#s-sym").onchange = load; $("#s-tf").onchange = load; document.querySelectorAll(".ov").forEach((c) => c.onchange = applyOverlays);
  load();
}

/* ---------- 4. Model Lab ---------- */
async function viewModels() {
  view.innerHTML = `<h1>Model Lab</h1><p class="sub">Models are trained on explicit command, compared against the rules-only baseline on an untouched test slice, and never activated without your approval.</p>
  <div class="bar"><select id="m-sym">${SYMS.map((s) => `<option>${s}</option>`).join("")}</select><button class="primary" id="m-train">Train (job)</button><button id="m-sync">Sync data (job)</button><button class="danger" id="m-rb">Roll back active model</button></div>
  <div id="m-body"><div class="empty">${spinner}loading</div></div><h3>Jobs</h3><div id="jobs"></div>`;
  const load = async () => {
    const s = $("#m-sym").value; const [snap, models] = await Promise.all([api(`/markets/${s}/snapshot`), api(`/models?symbol=${s}`)]);
    const q = snap.quality; const trade = models.filter((m) => m.kind === "trade"); const last = trade[0];
    const mt = last?.metrics || {}; const ts = mt.test_selected, bl = mt.baseline_test?.pooled_rules_only;
    $("#m-body").innerHTML = `<div class="grid cols2"><div class="card"><h2>Data health</h2>
      <div class="row"><span>Status</span><span>${badge(q.status, q.status === "HEALTHY" ? "good" : "warn")}</span></div><div class="row"><span>Stored M5 bars</span><span>${q.bars}</span></div><div class="row"><span>Last bar</span><span>${fmtTime(q.last_bar_open)}</span></div>
      <div class="row"><span>Recent missing bars</span><span>${q.recent_missing_bars ?? "-"}</span></div><div class="row"><span>Sources</span><span>${esc(snap.lineage.history_provider)}${snap.lineage.live_provider ? " + " + esc(snap.lineage.live_provider) : ""}</span></div>
      <div class="row"><span>Assumed-spread bars</span><span>${snap.lineage.assumed_spread_bars}</span></div></div>
    <div class="card"><h2>Latest trade model ${last ? badge(last.status, last.status === "APPROVED" ? "good" : last.status === "CANDIDATE" ? "info" : "bad") : ""}</h2>${last ? `
      <div class="row"><span>Version</span><span class="mono">${esc(last.version)}</span></div><div class="row"><span>Algorithm</span><span>${esc(last.algo)}</span></div>
      <div class="row"><span>Events (train/cal/val/test)</span><span>${esc(JSON.stringify(mt.split_sizes || {}))}</span></div>
      <div class="row"><span>Baseline test (rules-only)</span><span>${bl && bl.n ? `n=${bl.n} EV ${sgn(bl.ev_r)}R win ${pct(bl.win_rate)}` : "-"}</span></div>
      <div class="row"><span>Frozen model on test</span><span>${ts && ts.n ? `n=${ts.n} EV ${sgn(ts.ev_r)}R win ${pct(ts.win_rate)} [${pct(ts.wr_lo)}, ${pct(ts.wr_hi)}]` : "no trades selected"}</span></div>
      <div class="row"><span>Test Brier / AUC</span><span>${num(mt.test_prob_metrics?.brier, 4)} / ${num(mt.test_prob_metrics?.auc, 3)}</span></div>
      <p class="sub">${esc(mt.note || mt.reason || "")}</p>
      <div class="btns">${last.status === "CANDIDATE" ? `<button class="primary" id="m-approve">Approve this model</button>` : ""}</div>` : '<div class="empty">No model trained yet (rules-only baseline).</div>'}</div></div>
    ${mt.test_reliability ? `<div class="grid cols2"><div class="card"><h2>Calibration (test)</h2>${reliabilitySvg(mt.test_reliability)}</div><div class="card"><h2>Feature-group ablation (validation)</h2><div class="tablewrap"><table><thead><tr><th>Group removed</th><th class="num">Features</th><th class="num">Val AUC</th><th class="num">Delta</th></tr></thead><tbody>${(mt.ablation || []).map((a) => `<tr><td>${esc(a.group_removed)}</td><td class="num">${a.n_features}</td><td class="num">${num(a.val_auc, 3)}</td><td class="num">${a.d_auc != null ? sgn(a.d_auc, 3) : "-"}</td></tr>`).join("")}</tbody></table></div></div></div>` : ""}
    <h3>All model versions</h3><div class="tablewrap"><table><thead><tr><th>Version</th><th>Kind</th><th>Status</th><th>Created</th></tr></thead><tbody>${models.map((m) => `<tr><td class="mono">${esc(m.version)}</td><td>${esc(m.kind)}</td><td>${esc(m.status)}</td><td>${fmtTime(new Date(m.created_ts * 1000).toISOString())}</td></tr>`).join("") || '<tr><td colspan="4">none</td></tr>'}</tbody></table></div>`;
    const ap = $("#m-approve"); if (ap) ap.onclick = async () => { try { await post(`/models/${encodeURIComponent(last.version)}/approve`); toast("model approved"); load(); } catch (e) { toast(e.message); } };
  };
  $("#m-sym").onchange = load;
  $("#m-train").onclick = async () => { try { await runJob("/jobs/train", { symbol: $("#m-sym").value }, "Training"); jobsPanel($("#jobs")); } catch (e) { toast(e.message); } };
  $("#m-sync").onclick = async () => { try { await runJob("/jobs/backfill", { symbol: $("#m-sym").value }, "Sync"); jobsPanel($("#jobs")); } catch (e) { toast(e.message); } };
  $("#m-rb").onclick = async () => { if (confirm("Roll back the active trade model?")) { const r = await post("/models/rollback", { symbol: $("#m-sym").value }); toast(JSON.stringify(r)); load(); } };
  load(); jobsPanel($("#jobs"));
}

/* ---------- 5. Backtest Analytics ---------- */
const statRow = (name, s) => `<tr><td>${esc(name)}</td><td class="num">${s.n}</td><td class="num">${pct(s.win_rate)}</td><td class="num">${s.wr_lo != null ? pct(s.wr_lo, 0) + "-" + pct(s.wr_hi, 0) : "-"}</td><td class="num">${sgn(s.ev_r)}</td><td class="num">${s.pf != null ? num(s.pf) : "-"}</td><td class="num">${num(s.max_dd_r, 1)}</td></tr>`;
const statHead = "<thead><tr><th></th><th class=\"num\">Trades</th><th class=\"num\">Win</th><th class=\"num\">95% CI</th><th class=\"num\">Net EV (R)</th><th class=\"num\">PF</th><th class=\"num\">Max DD (R)</th></tr></thead>";
async function viewBacktest() {
  view.innerHTML = `<h1>Backtest Analytics</h1><p class="sub">Bid/ask fills with spread, slippage and commission; ambiguous bars resolve against the trade. Win rate alone is never the target: net expectancy after costs is.</p>
  <div class="bar"><select id="b-sym">${SYMS.map((s) => `<option>${s}</option>`).join("")}</select><button class="primary" id="b-run">Run backtest + walk-forward (job)</button></div><div id="b-body"><div class="empty">${spinner}loading</div></div><h3>Jobs</h3><div id="jobs"></div>`;
  const load = async () => {
    const r = await api(`/backtests?symbol=${$("#b-sym").value}`); const res = r.results;
    if (!res || res.status !== "DONE") { $("#b-body").innerHTML = `<div class="empty">No completed backtest yet.${res?.reason ? " " + esc(res.reason) : ""}</div>`; return; }
    const p = res.pooled_rules_only; const wf = res.walk_forward;
    const br = res.breakdowns || {};
    $("#b-body").innerHTML = `<div class="bar"><div class="stat"><b>${p.n}</b><span>trades (1 at a time)</span></div><div class="stat"><b>${pct(p.win_rate)}</b><span>win rate</span></div><div class="stat"><b>${sgn(p.ev_r)}R</b><span>net expectancy</span></div><div class="stat"><b>${num(p.pf)}</b><span>profit factor</span></div><div class="stat"><b>${num(p.max_dd_r, 1)}R</b><span>max drawdown</span></div></div>
    <p class="sub">${esc(res.data_from?.slice(0, 10))} to ${esc(res.data_to?.slice(0, 10))}, ${res.bars} bars, spread: ${esc(res.costs?.spread)}; news filter: ${esc(res.costs?.news_filter)}</p>
    <h3>Equity curve (pooled, rules-only)</h3>${lineSvg(res.equity_curve_r, { label: "cumulative net R" })}
    <div class="grid cols2"><div><h3>Strategies, rules-only</h3><div class="tablewrap"><table>${statHead}<tbody>${Object.entries(res.per_strategy_rules_only || {}).map(([k, v]) => statRow(k, v)).join("")}</tbody></table></div></div>
    <div><h3>Walk-forward (out-of-sample)</h3>${wf && wf.models ? `<div class="tablewrap"><table>${statHead}<tbody>${statRow("rules-only baseline", wf.baseline_oos)}${Object.entries(wf.models).map(([a, m]) => statRow(a + " selected (" + m.folds_positive + "/" + m.folds.length + " folds +)", m.oos_selected)).join("")}</tbody></table></div>` : '<div class="empty">walk-forward not available</div>'}</div></div>
    <div class="grid cols2">${["session", "base_regime", "month", "side_s"].map((k) => `<div><h3>By ${k.replace("base_", "").replace("_s", "")}</h3><div class="tablewrap"><table>${statHead}<tbody>${Object.entries(br[k] || {}).map(([n, v]) => statRow(n, v)).join("")}</tbody></table></div></div>`).join("")}</div>`;
  };
  $("#b-sym").onchange = load; $("#b-run").onclick = async () => { try { await runJob("/jobs/backtest", { symbol: $("#b-sym").value, walk_forward: true }, "Backtest"); jobsPanel($("#jobs")); } catch (e) { toast(e.message); } };
  load(); jobsPanel($("#jobs"));
}

/* ---------- 6. Paper Journal ---------- */
async function viewPaper() {
  view.innerHTML = `<h1>Paper Trading Journal</h1><p class="sub">Simulated only. Same fill rules as the backtest (next-bar open at the correct side of the spread, stop-first on ambiguous bars). "Shadow" rows are research candidates that had no validated edge, tracked to build forward evidence.</p><div id="p-body"><div class="empty">${spinner}loading</div></div>`;
  const r = await api("/paper-trades");
  const sum = (t, s) => `<div class="card"><h2>${t}</h2><div class="row"><span>Closed</span><span>${s.n}</span></div><div class="row"><span>Win rate (TP1)</span><span>${pct(s.win_rate)} ${s.wr_lo != null ? `[${pct(s.wr_lo, 0)}-${pct(s.wr_hi, 0)}]` : ""}</span></div><div class="row"><span>Net expectancy</span><span>${sgn(s.ev_r)}R</span></div><div class="row"><span>Profit factor</span><span>${s.pf != null ? num(s.pf) : "-"}</span></div><div class="row"><span>Max drawdown</span><span>${num(s.max_dd_r, 1)}R</span></div><div class="row"><span>Open</span><span>${s.open}</span></div><div class="row"><span>P&L (USD, simulated)</span><span>${num(s.pnl_usd)}</span></div></div>`;
  $("#p-body").innerHTML = `${r.circuit_breaker.tripped ? '<div class="notice bad">Daily circuit breaker TRIPPED: no new authorized paper trades today.</div>' : ""}
  <div class="grid cols2">${sum("Authorized signals", r.authorized)}${sum("Shadow candidates (no validated edge)", r.shadow)}</div>
  <h3>Positions</h3>${r.positions.length ? `<div class="tablewrap"><table><thead><tr><th>Created</th><th>Symbol</th><th>Side</th><th>Strategy</th><th>Auth</th><th>State</th><th>Fill</th><th>SL</th><th>TP1</th><th>Exit</th><th class="num">Net R</th><th class="num">P&L</th></tr></thead><tbody>${r.positions.map((p) => `<tr><td>${fmtTime(new Date(p.created_ts * 1000).toISOString())}</td><td>${esc(p.symbol)}</td><td>${esc(p.side)}</td><td>${esc(p.strategy)}</td><td>${p.authorized ? "yes" : "shadow"}</td><td>${esc(p.state)}</td><td class="mono">${px(p.symbol, p.entry_price)}</td><td class="mono">${px(p.symbol, p.sl)}</td><td class="mono">${px(p.symbol, p.tp1)}</td><td>${esc(p.exit_reason || "-")}</td><td class="num">${p.net_r != null ? sgn(p.net_r, 2) : "-"}</td><td class="num">${p.pnl != null ? num(p.pnl) : "-"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">No paper positions yet. They are created when the monitor or Get signal sees a healthy-feed candidate.</div>'}`;
}

/* ---------- 7. Economic Events ---------- */
async function viewEvents() {
  view.innerHTML = `<h1>Economic Events</h1><p class="sub">Source: free Forex Factory weekly feed (unofficial, terms unverified). If it cannot be fetched this page says so; nothing is invented. Backtests do not apply a news filter.</p><div class="bar"><button class="primary" id="e-refresh">Refresh calendar</button></div><div id="e-body"><div class="empty">${spinner}loading</div></div>`;
  const load = async (refresh) => {
    const c = await api("/calendar?hours=96" + (refresh ? "&refresh=true" : ""));
    $("#e-body").innerHTML = `<div class="notice ${c.state.status === "OK" ? "" : "bad"}">Calendar status: <b>${esc(c.state.status)}</b> (policy when unavailable: ${esc(c.policy)})</div>
    <div class="grid cols2">${SYMS.map((s) => { const b = c.blackout[s]; const ev = c.events[s] || []; return `<div class="card"><h2>${s} ${badge(b.state, b.state === "CLEAR" ? "good" : b.state === "BLACKOUT" ? "bad" : "warn")}</h2>${ev.length ? `<div class="tablewrap"><table><thead><tr><th>When</th><th>Cur</th><th>Event</th><th>Impact</th><th>In</th></tr></thead><tbody>${ev.map((e) => `<tr><td>${fmtTime(e.scheduled_utc)}</td><td>${esc(e.currency)}</td><td>${esc(e.title)}</td><td>${esc(e.impact)}</td><td>${e.minutes_to > 0 ? Math.round(e.minutes_to) + " min" : "past"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">no relevant high/medium events in the window (or calendar unavailable)</div>'}</div>`; }).join("")}</div>`;
  };
  $("#e-refresh").onclick = () => load(true); load(false);
}

/* ---------- 8. Settings / Health ---------- */
async function viewSettings() {
  view.innerHTML = `<h1>Settings / Provider Health</h1><p class="sub">Secrets live in .env and are never sent to the browser; only presence is shown.</p><div id="s-body"><div class="empty">${spinner}loading</div></div>`;
  const [s, h, hp, au] = await Promise.all([api("/settings"), api("/providers/health"), api("/health"), api("/audits?limit=15")]);
  $("#s-body").innerHTML = `<div class="grid cols2"><div class="card"><h2>Runtime</h2><div class="row"><span>Mode</span><span>${badge("PAPER ONLY", "warn")} local</span></div><div class="row"><span>History provider</span><span>${esc(s.history_provider)}</span></div><div class="row"><span>Live provider</span><span>${esc(s.live_provider)}</span></div>
    <div class="row"><span>API auth</span><span>${s.credentials.api_token ? "token required" : "loopback only, no token"}</span></div><div class="row"><span>Database</span><span>${hp.db ? badge("OK", "good") : badge("DOWN", "bad")}</span></div>
    ${Object.entries(s.credentials).map(([k, v]) => `<div class="row"><span>${esc(k)}</span><span>${v ? "set" : "not set"}</span></div>`).join("")}</div>
    <div class="card"><h2>Risk policy</h2>${Object.entries(s.risk).map(([k, v]) => `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div></div>
  <h3>Providers</h3><div class="grid cols2">${h.map((p) => `<div class="card"><h2>${esc(p.health.provider)} ${badge(p.role)} ${p.health.ok === true ? badge("OK", "good") : p.health.ok === false ? badge(p.health.blocked ? "BLOCKED" : "DOWN", p.health.blocked ? "warn" : "bad") : badge("not tested")}</h2>
    <div class="row"><span>Real bid/ask</span><span>${p.capabilities.bid_ask ? "yes" : "NO (mid only, spread assumed)"}</span></div><div class="row"><span>Verified by this project</span><span>${p.capabilities.verified ? "yes" : "no"}</span></div>
    <div class="row"><span>Symbols</span><span>${Object.entries(p.capabilities.symbols).map(([k, v]) => `${k} ${v ? "ok" : "x"}`).join(", ")}</span></div><div class="sub">${esc(p.health.detail || p.health.note || "")}</div></div>`).join("")}</div>
  <h3>Instruments</h3><div class="tablewrap"><table><thead><tr><th>Symbol</th><th>Assumed spread (only if mid-only)</th><th>Commission RT</th></tr></thead><tbody>${Object.entries(s.instruments).map(([k, v]) => `<tr><td>${k}</td><td>${v.assumed_spread}</td><td>${v.commission_rt}</td></tr>`).join("")}</tbody></table></div>
  <h3>Recent audit log</h3><div class="tablewrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Event</th></tr></thead><tbody>${au.map((a) => `<tr><td>${fmtTime(new Date(a.ts * 1000).toISOString())}</td><td>${esc(a.actor)}</td><td>${esc(a.event)} <span class="mono">${esc(JSON.stringify(a.detail || {}).slice(0, 120))}</span></td></tr>`).join("")}</tbody></table></div>
  <h3>Jobs</h3><div id="jobs"></div>`;
  jobsPanel($("#jobs"));
}

/* ---------- router / chrome ---------- */
const routes = { command: viewCommand, signals: viewSignals, symbol: viewSymbol, models: viewModels, backtest: viewBacktest, paper: viewPaper, events: viewEvents, settings: viewSettings };
async function route() {
  const key = (location.hash.replace("#/", "").split("?")[0]) || "command";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#/" + key));
  if (chartRef) { try { chartRef.remove(); } catch (e) { /* ignore */ } chartRef = null; }
  try { await (routes[key] || viewCommand)(); } catch (e) { view.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);
$("#tz").value = tz; $("#tz").onchange = (e) => { tz = e.target.value; localStorage.setItem("tz", tz); route(); };
$("#token-btn").onclick = askToken;
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString("en-GB", { timeZone: tz, hour12: false }) + (tz === "UTC" ? " UTC" : " PKT"); }, 1000);
/* live update stream: SSE with automatic browser reconnect; server resends a snapshot on reconnect */
function startStream() {
  const st = $("#stream-state");
  if (new URLSearchParams(location.search).has("nostream")) { st.textContent = "STREAM OFF"; return; }
  try {
    const es = new EventSource(API + "/stream" + (token ? "?token=" + encodeURIComponent(token) : ""));
    es.addEventListener("snapshot", () => { st.className = "badge live"; st.textContent = "LIVE STREAM"; });
    es.onerror = () => { st.className = "badge warn"; st.textContent = "RECONNECTING"; };
  } catch (e) { st.className = "badge bad"; st.textContent = "NO STREAM"; }
}
startStream(); route();
