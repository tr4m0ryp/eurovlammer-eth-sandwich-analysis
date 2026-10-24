// V3 sandwich targets tab: live strict rows + wallet state over SSE.
"use strict";
const MAX_ROWS = 200;
const FLUSH_MS = 1000;
const S = { strict: [], pending: [], view: "", stats: {}, wallet: {}, timer: 0 };

// numeraire (wei) -> ETH, with 1e18 scale
const eth = (w) => (w == null ? "" : (Number(w) / 1e18).toFixed(Number(w) >= 1e17 ? 3 : 5));
const tier = (ppm) => (ppm ? ppm / 100 + " bps" : "");
const dir_label = (z) => (z ? "buy" : "sell");

const pass = (x) => !S.view ||
  (S.view === "high_roi" ? (Number(x.mesp) || 0) >= 0.02e18 : (x.pool_freq || 0) >= 3);

function strictRow(x, fresh) {
  return `<tr${fresh ? ' class="new"' : ""}>
    <td class="num">${fmt.time(x.seen_ms || Date.now())}</td>
    <td class="num">${fmt.int(x.block)}</td>
    <td class="num">${tier(x.fee_ppm)}</td>
    <td class="mono">${fmt.short(x.pool)}</td>
    <td class="num">${fmt.n(x.price)}</td>
    <td class="num">${eth(x.amount_in)}</td>
    <td>${x.via_default ? '<span class="tag dash">estimated</span>' : '<span class="tag brand">decoded</span>'}</td>
    <td class="num"><b>${eth(x.mesp)}</b></td>
    <td class="num">${eth(x.front)}</td>
    <td class="num ${(x.roi || 0) >= 0.05 ? "good" : ""}">${fmt.pct3(x.roi)}</td>
    <td class="num">${fmt.int(x.pool_freq)}</td>
    <td class="num">${fmt.slack3(x.slack)}</td>
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
    stat("V3 swaps decoded", fmt.int(st.swaps || 0), "Swap events in confirmed blocks"),
    stat("Strict targets", fmt.int(st.strict || 0), "MESP > round-trip fee"),
    stat("Guards decoded", fmt.int(((st.funnel || {}).router_decoded || 0) + ((st.funnel || {}).aggregator_decoded || 0)), "victim amountOutMinimum read from calldata"),
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
  renderFunnel(S.stats);
}

function walletInit(state) { S.wallet = state || {}; walletRender(); }
function walletEvent(d) { S.wallet = d || {}; walletRender(); }

const eth2 = (w) => (w == null ? "" : (Number(w)).toFixed(Number(w) >= 1 ? 3 : 6));

function walletRender() {
  const w = S.wallet || {};
  const st = w.stats || {};
  // top stats
  const wb = $("#wallet-stats");
  if (wb) {
    wb.innerHTML = [
      stat("Wallet (ETH)", eth2(st.wallet), `bankroll ${eth2(st.bankroll)}`),
      stat("Total profit (ETH)", (st.total_profit != null && st.total_profit < 0 ? "-" : "+") + eth2(Math.abs(st.total_profit || 0)), "expected, after gas + bribe"),
      stat("Committed", fmt.int(st.n_committed || 0), `${fmt.int(st.n_resolved || 0)} resolved`),
      stat("Landing cost (ETH)", eth2(st.landing_cost), `gas ${eth2(st.gas_cost)} + bribe ${eth2(st.bribe)}`),
    ].join("");
  }
  // active positions
  const arows = (w.active || []).map(p => `<tr>
    <td class="num">${fmt.time(p.seen_ms || Date.now())}</td>
    <td class="mono">${fmt.short(p.pool)}</td>
    <td class="num">${eth2(p.front)}</td>
    <td class="num">${eth2(p.net_expected)}</td>
    <td><span class="tag">locked</span></td></tr>`).join("");
  const ab = $("#wallet-active-rows");
  if (ab) ab.innerHTML = arows || `<tr data-empty><td colspan="5" class="muted">No active positions.</td></tr>`;
  // history
  const hrows = (w.history || []).map(h => `<tr>
    <td class="num">${fmt.time(h.seen_ms || Date.now())}</td>
    <td class="num">${fmt.time(h.resolved_ms || Date.now())}</td>
    <td class="mono">${fmt.short(h.pool)}</td>
    <td class="num">${eth2(h.front)}</td>
    <td class="num">${eth2(h.mesp)}</td>
    <td class="num ${(h.gain||0) >= 0 ? "good" : "bad"}">${(h.gain||0) >= 0 ? "+" : ""}${eth2(h.gain)}</td>
    <td>${(h.gain||0) >= 0 ? "win (exp.)" : "loss (exp.)"}</td></tr>`).join("");
  const hb = $("#wallet-hist-rows");
  if (hb) hb.innerHTML = hrows || `<tr data-empty><td colspan="7" class="muted">Nothing resolved yet.</td></tr>`;
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
