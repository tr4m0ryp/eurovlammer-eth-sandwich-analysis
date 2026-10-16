// V3 sandwich targets tab: live strict rows + wallet state over SSE.
"use strict";
const MAX_ROWS = 200;
const FLUSH_MS = 1000;
const S = { strict: [], pending: [], view: "", stats: {}, timer: 0 };

// numeraire (wei) -> ETH, with 1e18 scale
const eth = (w) => (w == null ? "" : (Number(w) / 1e18).toFixed(Number(w) >= 1e17 ? 3 : 5));
const tier = (p) => (p == null ? "" : p + " bps");
const dir_label = (z) => (z ? "buy" : "sell");

const pass = (x) => !S.view ||
  (S.view === "high_roi" ? (Number(x.mesp) || 0) >= 0.02e18 : (x.pool_freq || 0) >= 3);

function strictRow(x, fresh) {
  return `<tr${fresh ? ' class="new"' : ""}>
    <td class="num">${fmt.time(x.seen_ms || Date.now())}</td>
    <td class="num">${fmt.int(x.block)}</td>
    <td class="num">${tier(x.fee_ppm ? x.fee_ppm / 10000 : null)}</td>
    <td class="mono">${fmt.short(x.pool)}</td>
    <td class="num">${fmt.n(x.price)}</td>
    <td class="num">${eth(x.amount_in)}</td>
    <td class="num">${fmt.slack(x.slack)}</td>
    <td class="num"><b>${eth(x.mesp)}</b></td>
    <td class="num">${eth(x.front)}</td>
    <td class="num ${(x.roi || 0) >= 0.05 ? "good" : ""}">${fmt.pct(x.roi)}</td>
    <td class="num">${fmt.int(x.pool_freq)}</td>
    <td class="num">${fmt.slack(x.slack)}</td>
    <td>${dir_label(x.zero_for_one)}</td>
    <td><a class="mono" href="https://etherscan.io/tx/${x.tx_hash}" target="_blank" rel="noopener">${(x.tx_hash || "").slice(0, 8)}</a></td></tr>`;
}

const EMPTY = `<tr data-empty><td colspan="14" class="muted">No V3 targets yet. Waiting for victim swaps with slippage room exceeding the round-trip fee.</td></tr>`;

function renderCount() {
  $("#strict-live-n").textContent = `${S.strict.filter(pass).length} match of ${S.strict.length} in memory, newest ${MAX_ROWS} shown`;
}

function renderAll() {
  const rows = S.strict.filter(pass).slice(0, MAX_ROWS);
  $("#strict-live-rows").innerHTML = rows.map((x) => strictRow(x, false)).join("") || EMPTY;
  renderCount();
}

function renderStats() {
  const st = S.stats || {};
  $("#strict-live-stats").innerHTML = [
    stat("Blocks seen", fmt.int(st.blocks || 0), "confirmed blocks decoded"),
    stat("V3 swaps decoded", fmt.int(st.swaps || 0), "Swap events on the public mempool"),
    stat("Strict targets", fmt.int(st.strict || 0), "MESP > round-trip fee"),
    stat("Sandwiches detected", fmt.int(st.sandwiches || 0), "front + back around a victim"),
  ].join("");
}

function flush() {
  S.timer = 0;
  if (document.hidden || !S.pending.length) return;
  const fresh = S.pending.filter(pass).slice(0, MAX_ROWS);
  S.pending = [];
  const body = $("#strict-live-rows");
  if (fresh.length) {
    body.querySelector("tr[data-empty]")?.remove();
    body.insertAdjacentHTML("afterbegin", fresh.map((x) => strictRow(x, true)).join(""));
    while (body.rows.length > MAX_ROWS) body.deleteRow(-1);
  }
  renderCount();
}

function addStrict(rows) {
  for (const x of rows) { S.strict.unshift(x); S.pending.unshift(x); }
  if (S.strict.length > 2000) S.strict.length = 2000;
  if (S.pending.length > MAX_ROWS) S.pending.length = MAX_ROWS;
  if (!S.timer) S.timer = setTimeout(flush, FLUSH_MS);
}

function setStats(stats) {
  S.stats = stats || {};
  if (document.hidden) return;
  const t = S.stats;
  if (t && t.block != null) {
    $("#live").className = "live on";
    $("#live-t").textContent = `live, block ${fmt.int(t.block)}`;
  }
  renderStats();
}

function walletInit(state) { /* wallet tab is a lightweight dry-run echo */ }
function walletRender() {}

function walletEvent(d) {
  const b = $("#wallet-active-rows");
  if (b) b.innerHTML = `<tr data-empty><td colspan="5" class="muted">Dry-run (no funded key). ${d && d.type ? d.type : "no events"}</td></tr>`;
}

function wireStrict() {
  document.querySelectorAll(".seg[data-sl]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    S.view = b.dataset.v;
    S.pending = [];
    renderAll();
  }));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    S.pending = [];
    renderAll();
    if (S.stats) renderStats();
  });
}

function wireTabs() {
  const tabs = document.querySelectorAll("#tabs .tab");
  const panels = document.querySelectorAll("[data-panel]");
  tabs.forEach((b) => b.addEventListener("click", () => {
    tabs.forEach((x) => x.setAttribute("aria-selected", x === b));
    panels.forEach((p) => { p.hidden = p.dataset.panel !== b.dataset.tab; });
    renderAll();
  }));
}

async function loadSnapshot() {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const snap = await (await fetch("api/snapshot?parts=stats,strict,wallet", { signal: ctl.signal })).json();
    S.strict = (snap.strict || []).reverse();
    walletInit(snap.wallet || {});
    setStats(snap.stats);
  } catch (e) {
    $("#live-t").textContent = "no screener";
  } finally {
    clearTimeout(t);
  }
  renderAll();
  openStream();
}

function openStream() {
  const es = new EventSource("api/stream?t=stats,strict,wallet");
  es.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "strict") addStrict(m.d);
    else if (m.t === "wallet") walletEvent(m.d);
    else if (m.t === "stats") setStats(m.d);
  };
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
  es.onopen = () => setStats(S.stats);
}

wireTabs();
wireStrict();
loadSnapshot();
