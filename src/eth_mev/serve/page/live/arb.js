// Atomic arbitrage tab: live cross-pool dislocation rows over SSE.
"use strict";
const MAX_ROWS = 200;
const FLUSH_MS = 1000;
const S = { arbs: [], pending: [], stats: {}, timer: 0 };

const eth = (w) => (w == null ? "" : (Number(w) / 1e18).toFixed(Number(w) >= 1 ? 3 : 5));
const price = (p) => (p == null ? "" : Number(p) < 0.01 ? Number(p).toExponential(3) : Number(p).toFixed(4));

function arbRow(x, fresh) {
  return `<tr${fresh ? ' class="new"' : ""}>
    <td class="num">${fmt.time(x.seen_ms || Date.now())}</td>
    <td class="mono">${fmt.short(x.token1)}</td>
    <td class="mono">${x.kind_buy} ${fmt.short(x.pool_buy)}</td>
    <td class="num">${price(x.price_buy)}</td>
    <td class="mono">${x.kind_sell} ${fmt.short(x.pool_sell)}</td>
    <td class="num">${price(x.price_sell)}</td>
    <td class="num">${fmt.pct((x.spread_pct || 0) / 100)}</td>
    <td class="num">${eth(x.borrow)}</td>
    <td class="num"><b>${eth(x.profit)}</b></td>
    <td class="num ${(x.net || 0) >= 0 ? "good" : "bad"}">${eth(x.net)}</td></tr>`;
}

const EMPTY = `<tr data-empty><td colspan="10" class="muted">No executable dislocations yet. Waiting for a fresh, deep cross-pool price gap.</td></tr>`;

function renderAll() {
  const rows = S.arbs.slice(0, MAX_ROWS);
  $("#arb-rows").innerHTML = rows.map((x) => arbRow(x, false)).join("") || EMPTY;
  $("#arb-live-n").textContent = `${S.arbs.length} dislocations in memory, newest ${MAX_ROWS} shown`;
}

function renderStats() {
  const st = S.stats || {};
  $("#arb-stats").innerHTML = [
    stat("Blocks", fmt.int(st.blocks || 0), "scanned"),
    stat("Markets", fmt.int(st.markets || 0), "V2 + V3 discovered"),
    stat("Filtered out", fmt.int(st.filtered || 0), "thin / stale"),
    stat("Executable arbs", fmt.int(st.arbs || 0), `≥${st.min_depth_eth || 0} WETH depth, fresh`),
  ].join("");
}

function flush() {
  S.timer = 0;
  if (document.hidden || !S.pending.length) return;
  const fresh = S.pending.slice(0, MAX_ROWS);
  S.pending = [];
  const body = $("#arb-rows");
  if (fresh.length) {
    body.querySelector("tr[data-empty]")?.remove();
    body.insertAdjacentHTML("afterbegin", fresh.map((x) => arbRow(x, true)).join(""));
    while (body.rows.length > MAX_ROWS) body.deleteRow(-1);
  }
  renderAll();
}

function addArbs(rows) {
  for (const x of rows) { S.arbs.unshift(x); S.pending.unshift(x); }
  if (S.arbs.length > 2000) S.arbs.length = 2000;
  if (!S.timer) S.timer = setTimeout(flush, FLUSH_MS);
}

function setStats(stats) {
  S.stats = stats || {};
  if (document.hidden) return;
  if (S.stats.block != null) {
    $("#live").className = "live on";
    $("#live-t").textContent = `live, block ${fmt.int(S.stats.block)}`;
  }
  renderStats();
}

async function loadSnapshot() {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const snap = await (await fetch("api/snapshot?parts=stats,strict", { signal: ctl.signal })).json();
    S.arbs = (snap.strict || []).reverse();
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
  const es = new EventSource("api/stream?t=stats,strict");
  es.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "strict") addArbs(m.d);
    else if (m.t === "stats") setStats(m.d);
  };
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
  es.onopen = () => setStats(S.stats);
}

loadSnapshot();
