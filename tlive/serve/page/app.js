// Live page: one snapshot on load, then server-sent events. All numbers come from the screener.
"use strict";
const $ = (s) => document.querySelector(s);
const MAX_ROWS = 400;
const S = { flags: [], sands: [], risk: [], rview: "", stats: {}, f: { venue: "", side: "", status: "" }, pmin: null, paused: false };

const fmt = {
  sol: (l) => (l == null ? "" : (l / 1e9).toFixed(l >= 1e10 ? 1 : 3)),
  n: (v, d = 2) => (v == null ? "" : Number(v).toFixed(d)),
  int: (v) => (v == null ? "" : Math.round(v).toLocaleString("en-US")),
  pct: (v) => (v == null || !isFinite(v) ? "n/a" : (100 * v).toFixed(1) + "%"),
  slack: (v) => (v == null ? "?" : v >= 10 ? "none" : (100 * v).toFixed(1) + "%"),
  time: (ms) => new Date(ms).toISOString().slice(11, 19),
  short: (a) => (a ? a.slice(0, 4) + ".." + a.slice(-4) : "direct"),
};

function stat(label, v, s) {
  return `<div class="stat"><div class="lbl">${label}</div><div class="v">${v}</div><div class="s">${s || ""}</div></div>`;
}

function renderStats() {
  const t = S.stats;
  if (!t.slot) return;
  const prec = t.settled_flags ? t.flag_hits / t.settled_flags : null;
  const rec = t.victims ? t.victims_flagged / t.victims : null;
  const base = t.swaps ? t.victims / t.swaps : null;
  $("#stats").innerHTML = [
    stat("Slot", fmt.int(t.slot), `lag ${t.lag} slots, ${t.skipped} skipped`),
    stat("Swaps scored", fmt.int(t.swaps), `${fmt.int(t.slots)} blocks`),
    stat("Flagged", fmt.int(t.flags), `${fmt.pct(t.swaps ? t.flags / t.swaps : null)} of swaps, score >= ${fmt.n(t.threshold, 3)}`),
    stat("Precision", fmt.pct(prec), `${fmt.int(t.flag_hits)} of ${fmt.int(t.settled_flags)} settled flags sandwiched`),
    stat("Recall", fmt.pct(rec), `${fmt.int(t.victims_flagged)} of ${fmt.int(t.victims)} victims flagged`),
    stat("Lift", prec != null && base ? (prec / base).toFixed(1) + "x" : "n/a", `base rate ${fmt.pct(base)}`),
    stat("Sandwiches", fmt.int(t.sandwiches), `${t.bots} bots, ${fmt.n(t.profit_sol, 2)} SOL taken`),
  ].join("");
  const live = $("#live");
  live.className = "live " + (t.lag < 40 ? "on" : "off");
  $("#live-t").textContent = t.lag < 40 ? "live" : `behind ${t.lag} slots`;
}

function keep(r) {
  const f = S.f;
  return (r.p >= S.pmin) && (!f.venue || r.venue === f.venue) && (!f.side || (f.side === "buy") === r.is_buy)
    && (!f.status || r.status === f.status);
}

function flagRow(r, fresh) {
  const tx = `<a class="mono" href="https://solscan.io/tx/${r.signature}" target="_blank" rel="noopener">${r.signature.slice(0, 8)}</a>`;
  return `<tr class="${fresh ? "new" : ""}" data-k="${r.signature}:${r.ev_index}">
    <td class="num">${fmt.time(r.block_time ? r.block_time * 1000 : r.seen_ms)}</td><td class="num">${r.slot}</td>
    <td>${r.venue === "pump" ? "pump" : "PumpSwap"}</td>
    <td class="${r.is_buy ? "side-buy" : "side-sell"}">${r.is_buy ? "Buy" : "Sell"}</td>
    <td class="num">${fmt.sol(r.sol)}</td><td class="num">${fmt.sol(r.x_ref)}</td><td class="num">${fmt.int(r.fee_bps)}</td>
    <td class="num">${fmt.slack(r.slack)}</td><td class="num">${fmt.n(r.phi, 2)}</td>
    <td class="num">${r.mesp ? fmt.n(r.mesp / 1e9, 4) : "0"}</td><td class="num"><b>${fmt.n(r.p, 3)}</b></td>
    <td class="mono">${fmt.short(r.router)}</td><td class="num">${fmt.int(r.cu_price)}</td>
    <td class="num">${r.jito_tip ? fmt.n(r.jito_tip / 1e9, 4) : ""}</td>
    <td>${statusTag(r)}</td><td>${tx}</td></tr>`;
}

function statusTag(r) {
  const extra = r.status === "sandwiched" && r.sw_profit != null ? ` ${fmt.n(r.sw_profit / 1e9, 4)}` : "";
  return `<span class="tag ${r.status}">${r.status}${extra}</span>`;
}

function renderFlags(freshKey) {
  const rows = S.flags.filter(keep);
  $("#flags").innerHTML = rows.slice(0, MAX_ROWS)
    .map((r) => flagRow(r, freshKey && freshKey.has(r.signature + ":" + r.ev_index))).join("")
    || `<tr><td colspan="16" class="muted">No flagged swaps yet at this score.</td></tr>`;
  $("#flags-n").textContent = `${rows.length} shown of ${S.flags.length} in memory`;
}

function renderSands() {
  $("#sands").innerHTML = S.sands.slice(0, MAX_ROWS).map((x) => `<tr>
    <td class="num">${fmt.time(x.seen_ms || Date.now())}</td><td class="num">${x.slot}</td>
    <td>${x.venue === "pump" ? "pump" : "PumpSwap"}</td><td class="mono">${fmt.short(x.pool)}</td>
    <td class="mono"><a href="https://solscan.io/account/${x.attacker}" target="_blank" rel="noopener">${fmt.short(x.attacker)}</a></td>
    <td class="num">${x.n_victims}</td><td class="num">${x.n_flagged}</td><td class="num">${fmt.sol(x.victim_sol)}</td>
    <td class="num ${x.profit > 0 ? "good" : "bad"}">${fmt.n(x.profit / 1e9, 4)}</td></tr>`).join("")
    || `<tr><td colspan="9" class="muted">No sandwiches settled yet.</td></tr>`;
}

function renderRisk() {
  const r = (S.stats || {}).risk || {};
  $("#risk-stats").innerHTML = [
    stat("Risk buys", fmt.int(r.n), `buy-side sandwich gain above ${r.min_sol ?? 0.00001} SOL gas`),
    stat("Sandwiched", fmt.pct(r.n ? r.sandwiched / r.n : null), `${fmt.int(r.sandwiched)} of ${fmt.int(r.n)} risk swaps`),
    stat("Top 10% gain", fmt.int(r.top10), r.q90_sol != null ? `gain >= ${fmt.n(r.q90_sol, 4)} SOL` : "cutoff after 50 rows"),
    stat("Gain on the table", fmt.n(r.gain_total_sol, 2) + " SOL", `median ${fmt.n(r.gain_median_sol, 4)} SOL per risk swap`),
    stat("Top 10% sandwiched", fmt.pct(r.top10 ? r.top10_sandwiched / r.top10 : null), `${fmt.int(r.top10_sandwiched)} of ${fmt.int(r.top10)}`),
  ].join("");
  const rows = S.risk.filter((x) => !S.rview || (S.rview === "top" ? x.top10 : x.status === "sandwiched"));
  $("#risk").innerHTML = rows.slice(0, MAX_ROWS).map((x) => `<tr>
    <td class="num">${fmt.time(x.block_time ? x.block_time * 1000 : Date.now())}</td><td class="num">${x.slot}</td>
    <td>${x.venue === "pump" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.sol(x.sol)}</td><td class="num">${x.mc_sol != null ? fmt.n(x.mc_sol, 1) : ""}</td>
    <td class="num">${x.size_mc != null ? (100 * x.size_mc).toFixed(3) + "%" : ""}</td><td class="num">${fmt.int(x.fee_bps)}</td>
    <td class="num">${fmt.slack(x.slack)}</td><td class="num"><b>${fmt.n(x.mesp_best / 1e9, 4)}</b></td>
    <td class="num">${fmt.pct(x.gain_pct)}</td><td class="num">${fmt.pct(x.gain_roi)}</td>
    <td>${x.top10 ? '<span class="tag brand">top 10%</span>' : ""}</td><td class="num">${fmt.n(x.p, 3)}</td>
    <td><span class="tag ${x.status}">${x.status}</span></td>
    <td class="num">${x.sw_profit != null ? fmt.n(x.sw_profit / 1e9, 4) : ""}</td>
    <td class="num">${x.taken_pct != null ? fmt.pct(x.taken_pct) : ""}</td>
    <td><a class="mono" href="https://solscan.io/tx/${x.signature}" target="_blank" rel="noopener">${x.signature.slice(0, 8)}</a></td></tr>`).join("")
    || `<tr><td colspan="18" class="muted">Risk swaps appear about 14 slots after they land, once their sandwich status is known.</td></tr>`;
}

function renderCal() {
  const c = S.stats.calibration;
  if (!c) return;
  const max = Math.max(...c.map((b) => (b.n ? b.hits / b.n : 0)), 1e-6);
  $("#cal").innerHTML = c.map((b) => {
    const r = b.n ? b.hits / b.n : 0;
    return `<div class="bar" title="${b.hits} of ${b.n}"><div class="v">${b.n ? (100 * r).toFixed(1) + "%" : ""}</div>
      <div class="col" style="height:${(100 * r) / max}%"></div><div class="x">${b.lo}<br>${fmt.int(b.n)}</div></div>`;
  }).join("");
  $("#cal-n").textContent = `settled swaps per score bucket; bar = share sandwiched`;
  const m = S.stats.model_meta || {};
  $("#model").innerHTML = Object.entries(m).map(([k, v]) => `<div><dt>${k}</dt><dd>${typeof v === "number" ? fmt.n(v, 4) : v}</dd></div>`).join("");
}

function render(fresh) {
  renderStats();
  if (!S.paused) renderFlags(fresh);
  renderSands();
  renderRisk();
  renderCal();
}

function onEvent(m) {
  if (m.t === "flag") {
    S.flags.unshift(m.d);
    if (S.flags.length > 2000) S.flags.length = 2000;
    if (!S.paused && keep(m.d)) renderFlags(new Set([m.d.signature + ":" + m.d.ev_index]));
  } else if (m.t === "settle") {
    const idx = new Map(S.flags.map((r) => [r.signature + ":" + r.ev_index, r]));
    for (const u of m.d) { const r = idx.get(u.signature + ":" + u.ev_index); if (r) Object.assign(r, u); }
    if (!S.paused) renderFlags();
  } else if (m.t === "risk") {
    S.risk = m.d.slice().reverse().concat(S.risk).slice(0, 2000);
    renderRisk();
  } else if (m.t === "sandwich") {
    S.sands.unshift({ ...m.d, seen_ms: Date.now() });
    renderSands();
  } else if (m.t === "stats") {
    S.stats = m.d;
    if (S.pmin == null) setPmin(m.d.threshold);
    renderStats();
    renderRisk();
    renderCal();
  }
}

function setPmin(v) {
  S.pmin = Number(v);
  $("#pmin").value = S.pmin;
  $("#pmin-v").textContent = S.pmin.toFixed(2);
}

function wire() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== b.dataset.tab));
  }));
  document.querySelectorAll(".seg[data-f]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    S.f[g.dataset.f] = b.dataset.v;
    renderFlags();
  }));
  document.querySelectorAll(".seg[data-r]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    S.rview = b.dataset.v;
    renderRisk();
  }));
  $("#pmin").addEventListener("input", (e) => { setPmin(e.target.value); renderFlags(); });
  $("#pause").addEventListener("click", (e) => {
    S.paused = !S.paused;
    e.target.setAttribute("aria-pressed", S.paused);
    e.target.textContent = S.paused ? "Resume" : "Pause";
    if (!S.paused) renderFlags();
  });
}

// Artifact mode: the page runs on claude.ai and reads the screener's snapshot from the artifact
// database (pushed about once a minute), instead of the local API and event stream.
async function bootArtifact() {
  render();
  const db = await window.claude.use("db");
  if (!db) { $("#live-t").textContent = "sign in to claude.ai to see the data"; return; }
  wireNight();
  wireOptimal();
  loadOptimal(db).catch(() => { $("#opt-note").textContent = "Optimal data unavailable"; });
  loadNight(db).catch(() => { $("#night-note").textContent = "Night data unavailable"; });
  let updated = 0;
  const tick = () => {
    if (!updated) return;
    const age = Math.round((Date.now() - updated) / 1000);
    $("#live").className = "live " + (age < 180 ? "on" : "off");
    $("#live-t").textContent = age < 180 ? `live, updated ${age}s ago` : `screener stopped ${fmt.time(updated)} UTC`;
  };
  setInterval(tick, 1000);
  db.doc("live/snapshot").onSnapshot((snap) => {
    if (!snap.exists) { $("#live-t").textContent = "waiting for the screener"; return; }
    const d = snap.data();
    S.stats = d.stats || {};
    S.flags = (d.flags || []).slice();
    S.sands = (d.sands || []).slice();
    S.risk = (d.risk || []).slice();
    if (S.pmin == null) setPmin(S.stats.threshold ?? 0);
    updated = d.updated_ms || Date.now();
    render();
    tick();
  }, () => { $("#live-t").textContent = "feed unavailable"; });
}

async function boot() {
  wire();
  if (window.claude && window.claude.use) return bootArtifact();
  try {
    const snap = await (await fetch("/api/snapshot")).json();
    S.stats = snap.stats || {};
    S.flags = (snap.flags || []).reverse();
    S.sands = (snap.sandwiches || []).reverse();
    S.risk = (snap.risk || []).reverse();
    setPmin(S.stats.threshold ?? 0);
  } catch (e) { $("#live-t").textContent = "no screener"; }
  render();
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => onEvent(JSON.parse(ev.data));
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
}
boot();
