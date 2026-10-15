// Strict targets tab: the budget-constrained screener's rows and counts.
// Rendering is batched: rows that arrive between flushes are inserted at the top in one DOM write,
// at most once per FLUSH_MS, and nothing is drawn while the tab is hidden. A full rebuild happens
// only when the filter changes.
"use strict";
const MAX_ROWS = 200;
const KEEP_ROWS = 2000;
const FLUSH_MS = 1000;
const S = { strict: [], pending: [], view: "", stats: {}, statsKey: "", timer: 0 };

const pass = (x) => !S.view || (S.view === "high_roi" ? (x.roi || 0) >= 0.05 : (x.pool_freq || 0) >= 3);

function strictRow(x, fresh) {
  return `<tr${fresh ? ' class="new"' : ""}>
    <td class="num">${fmt.time(x.seen_ms || Date.now())}</td><td class="num">${x.slot}</td>
    <td>${x.venue === "pump" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.sol(x.sol)}</td><td class="num">${fmt.int(x.fee_bps)}</td><td class="num">${fmt.slack(x.slack)}</td>
    <td class="num"><b>${fmt.n((x.mesp_budget || 0) / 1e9, 5)}</b></td>
    <td class="num">${fmt.n((x.front_budget || 0) / 1e9, 4)}</td>
    <td class="num ${(x.roi || 0) >= 0.05 ? "good" : ""}">${fmt.pct(x.roi)}</td>
    <td class="num">${fmt.int(x.pool_freq)}</td><td class="num">${fmt.n(x.p, 3)}</td>
    <td class="mono">${fmt.short(x.router)}</td><td><span class="tag vis-${x.vis}">${x.vis}</span></td>
    <td><a class="mono" href="https://solscan.io/tx/${x.signature}" target="_blank" rel="noopener">${x.signature.slice(0, 8)}</a></td></tr>`;
}

const EMPTY = `<tr data-empty><td colspan="14" class="muted">No strict targets yet. Waiting for buys that pass the budget-constrained filter.</td></tr>`;

function renderCount() {
  $("#strict-live-n").textContent = `${S.strict.filter(pass).length} match of ${S.strict.length} in memory, newest ${MAX_ROWS} shown`;
}

function renderAll() {
  const rows = S.strict.filter(pass).slice(0, MAX_ROWS);
  $("#strict-live-rows").innerHTML = rows.map((x) => strictRow(x, false)).join("") || EMPTY;
  renderCount();
}

function renderStats() {
  const sv = S.stats.strict_vis || {};
  const key = `${sv.n}|${sv.strict}`;
  if (key === S.statsKey) return;
  S.statsKey = key;
  $("#strict-live-stats").innerHTML = [
    stat("Visible + sandwichable", fmt.int(sv.n || 0), "passed visibility + interest filters"),
    stat("Strict (budget)", fmt.int(sv.strict || 0), `0.5 SOL budget, ROI >= ${fmt.pct(0.01)}, front <= 0.5 SOL`),
  ].join("");
}

function renderLiveDot() {
  const t = S.stats;
  if (!t.slot) return;
  $("#live").className = "live " + (t.lag < 40 ? "on" : "off");
  $("#live-t").textContent = t.lag < 40 ? `live, slot ${fmt.int(t.slot)}` : `behind ${t.lag} slots`;
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
  // rows arrive oldest first within a block; the table is newest first
  for (const x of rows) { S.strict.unshift(x); S.pending.unshift(x); }
  if (S.strict.length > KEEP_ROWS) S.strict.length = KEEP_ROWS;
  if (S.pending.length > MAX_ROWS) S.pending.length = MAX_ROWS;
  if (!S.timer) S.timer = setTimeout(flush, FLUSH_MS);
}

function setStats(stats) {
  S.stats = stats || {};
  if (document.hidden) return;
  renderLiveDot();
  renderStats();
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
  // a hidden tab buffers rows without drawing; catch up in one rebuild when it is shown again
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    S.pending = [];
    renderAll();
    renderLiveDot();
    renderStats();
  });
}
