// Visibility tab: optimal targets filtered by attacker visibility.
// Shows only orders with high or medium visibility — the ones an attacker could
// plausibly have seen before inclusion. Read-only, retrospective.
"use strict";
const V = { s: null, rows: [], view: "", sort: "rank" };

const VIS_LABEL = {
  high: "High — direct, untipped, on a hot pool",
  medium: "Medium — one protective factor (routed, tipped, or quiet pool)",
  low: "Low — two or more protective factors (not shown here)",
};
const ROUTE_LABEL = {
  direct: "Direct",
  aggregator: "Aggregator",
  other_router: "Other router",
};

async function loadVisibility(db) {
  const snap = await db.doc("visibility/summary").get();
  if (!snap.exists) { $("#vis-note").textContent = "No visibility data published yet."; return; }
  V.s = snap.data();
  renderVisSummary();
  const ids = Array.from({ length: V.s.n_docs }, (_, k) => "visibility/rows_" + String(k).padStart(3, "0"));
  const docs = await Promise.all(ids.map((id) => db.doc(id).get().catch(() => null)));
  const cols = V.s.columns;
  V.rows = docs.filter((d) => d && d.exists).flatMap((d) => d.data().rows)
    .map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  $("#vis-note").textContent = `${V.rows.length.toLocaleString("en-US")} visible targets loaded (high + medium visibility)`;
  renderVisRows();
}

function renderVisSummary() {
  const s = V.s.summary;
  const total = V.s.n_total;
  const vis = V.s.n_visible;
  $("#vis-stats").innerHTML = [
    stat("Optimal targets", fmt.int(total), "the night, all tiers"),
    stat("Visible to attackers", fmt.int(vis), `high + medium, ${fmt.pct(vis / total)} of all optimal`),
    stat("Low visibility (hidden)", fmt.int(s.low.n), `${fmt.pct(s.low.n / total)} of optimal, ${fmt.pct(s.low.hit_rate)} hit`),
    stat("High visibility", fmt.int(s.high.n), `${fmt.pct(s.high.hit_rate)} hit, ${fmt.n(s.high.net_sol, 1)} SOL net`),
    stat("Medium visibility", fmt.int(s.medium.n), `${fmt.pct(s.medium.hit_rate)} hit, ${fmt.n(s.medium.net_sol, 1)} SOL net`),
  ].join("");
  $("#vis-detail").innerHTML = V.s.detail.map((r) => {
    const rt = ROUTE_LABEL[r.route_type] || r.route_type;
    const tip = r.tipped ? "Tipped" : "Untipped";
    const pool = r.hot ? "Hot pool" : "Quiet pool";
    const tier = r.visibility;
    return `<tr><td>${rt}</td><td>${tip}</td><td>${pool}</td>
      <td class="num">${fmt.int(r.n)}</td><td class="num"><b>${fmt.pct(r.hit_rate)}</b></td>
      <td class="num">${fmt.n(r.net_sol, 1)}</td>
      <td><span class="tag ${tier === "high" ? "brand" : tier === "low" ? "muted" : ""}">${tier}</span></td></tr>`;
  }).join("");
}

function renderVisRows() {
  const key = { rank: "rank", size: "pct_mc", buy: "sol", time: "block_time" }[V.sort];
  const asc = V.sort === "rank";
  const rows = V.rows.filter((r) => !V.view || (V.view === "hit" ? r.sandwiched : V.view === "missed" ? !r.sandwiched : V.view === "high" ? r.visibility === "high" : r.visibility === "medium"))
    .sort((a, b) => (asc ? a[key] - b[key] : (b[key] ?? -1) - (a[key] ?? -1)));
  $("#vis-count").textContent = `${rows.length.toLocaleString("en-US")} rows, showing the first 500`;
  $("#vis-rows").innerHTML = rows.slice(0, 500).map((r) => `<tr>
    <td class="num">${r.rank}</td><td class="num">${fmt.time(r.block_time * 1000)}</td><td>${r.venue === "p" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.n(r.sol, 3)}</td><td class="num">${fmt.n(r.mc_sol, 1)}</td><td class="num">${fmt.n(r.pct_mc, 2)}%</td>
    <td class="num">${fmt.int(r.fee_bps)}</td><td class="num">${fmt.slack(r.slack)}</td>
    <td class="num">${fmt.n(r.gain_real, 4)}</td><td class="num"><b>${fmt.n(r.net_real, 4)}</b></td>
    <td><span class="tag ${r.hot ? "brand" : ""}">${r.hot ? "hot" : "quiet"}</span></td>
    <td class="mono">${r.route_type}</td><td>${r.tipped ? "tip" : "no tip"}</td>
    <td><span class="tag vis-${r.visibility}">${r.visibility}</span></td>
    <td><span class="tag ${r.sandwiched ? "sandwiched" : "clean"}">${r.sandwiched ? "sandwiched" : "missed"}</span></td>
    <td class="num">${r.taken_sol != null ? fmt.n(r.taken_sol, 4) : ""}</td>
    <td><a class="mono" href="https://solscan.io/tx/${r.signature}" target="_blank" rel="noopener">${r.signature.slice(0, 8)}</a></td></tr>`).join("")
    || `<tr><td colspan="17" class="muted">No rows match.</td></tr>`;
}

function wireVisibility() {
  document.querySelectorAll(".seg[data-v]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    V[g.dataset.v] = b.dataset.v;
    renderVisRows();
  }));
}