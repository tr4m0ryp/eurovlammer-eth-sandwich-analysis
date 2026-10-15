// Night tab: the consolidated overnight risk table (data/screener.duckdb), read from the artifact
// database (night/summary + night/rows_NNN). Rows are compact arrays; summary.columns names them.
"use strict";
const N = { s: null, rows: [], view: "", sort: "gain" };

async function loadNight(db) {
  const snap = await db.doc("night/summary").get();
  if (!snap.exists) { $("#night-note").textContent = "No night data published yet."; return; }
  N.s = snap.data();
  renderNightSummary();
  const ids = Array.from({ length: N.s.n_docs }, (_, k) => "night/rows_" + String(k).padStart(3, "0"));
  const docs = await Promise.all(ids.map((id) => db.doc(id).get().catch(() => null)));
  const ix = Object.fromEntries(N.s.columns.map((c, i) => [c, i]));
  N.rows = docs.filter((d) => d && d.exists).flatMap((d) => d.data().rows).map((r) => {
    const o = {};
    for (const c in ix) o[c] = r[ix[c]];
    return o;
  });
  $("#night-note").textContent = `${N.rows.length.toLocaleString("en-US")} risk buys loaded`;
  renderNightRows();
}

function renderNightSummary() {
  const s = N.s, w = s.sandwiches || {};
  const span = s.t1 && s.t0 ? ((s.t1 - s.t0) / 3600).toFixed(1) + " h" : "";
  $("#night-stats").innerHTML = [
    stat("Risk buys", fmt.int(s.n), `${span} from ${fmt.time(s.t0 * 1000)} UTC`),
    stat("Sandwiched", fmt.pct(s.sandwiched / s.n), `${fmt.int(s.sandwiched)} of ${fmt.int(s.n)}`),
    stat("Top 10% gain", fmt.int(s.top10), `gain >= ${fmt.n(s.q90_sol, 4)} SOL`),
    stat("Top 10% sandwiched", fmt.pct(s.top10_sandwiched / s.top10), `${fmt.int(s.top10_sandwiched)} of ${fmt.int(s.top10)}`),
    stat("Gain on the table", fmt.n(s.gain_total_sol, 0) + " SOL", `theoretical; median ${fmt.n(s.median_gain_sol, 4)} SOL per buy`),
    stat("Taken by sandwichers", fmt.n(w.total_profit_sol, 1) + " SOL", `${fmt.int(w.n)} sandwiches, ${fmt.int(w.attackers)} attackers`),
  ].join("");
  const d = s.size_mc_deciles || [];
  const max = Math.max(...d.map((x) => x.rate), 1e-6);
  $("#night-deciles").innerHTML = d.map((x) => `<div class="bar" title="${fmt.int(x.n)} buys">
    <div class="v">${(100 * x.rate).toFixed(1)}%</div><div class="col" style="height:${(100 * x.rate) / max}%"></div>
    <div class="x">${x.lo_pct < 0.01 ? x.lo_pct.toFixed(4) : x.lo_pct.toFixed(2)}%</div></div>`).join("");
}

function renderNightRows() {
  const key = { gain: "gain_sol", size: "size_mc_pct", time: "slot", score: "p" }[N.sort];
  const rows = N.rows.filter((r) => !N.view || (N.view === "top" ? r.top10 : r.sandwiched))
    .sort((a, b) => (b[key] ?? -1) - (a[key] ?? -1));
  $("#night-count").textContent = `${rows.length.toLocaleString("en-US")} rows, showing the first 400`;
  $("#night-rows").innerHTML = rows.slice(0, 400).map((r) => `<tr>
    <td class="num">${fmt.time(r.block_time * 1000)}</td><td>${r.venue === "p" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.n(r.sol, 3)}</td><td class="num">${fmt.n(r.mc_sol, 1)}</td><td class="num">${fmt.n(r.size_mc_pct, 3)}%</td>
    <td class="num">${fmt.int(r.fee_bps)}</td><td class="num">${fmt.slack(r.slack)}</td>
    <td class="num"><b>${r.gain_unbounded ? "n/a" : fmt.n(r.gain_sol, 4)}</b></td>
    <td class="num">${fmt.pct(r.gain_pct)}</td><td class="num">${fmt.pct(r.gain_roi)}</td>
    <td>${r.top10 ? '<span class="tag brand">top 10%</span>' : ""}</td>
    <td><span class="tag ${r.sandwiched ? "sandwiched" : "clean"}">${r.sandwiched ? "sandwiched" : "clean"}</span></td>
    <td class="num">${r.taken_sol != null ? fmt.n(r.taken_sol, 4) : ""}</td><td class="num">${fmt.n(r.p, 3)}</td>
    <td><a class="mono" href="https://solscan.io/tx/${r.signature}" target="_blank" rel="noopener">${r.signature.slice(0, 8)}</a></td></tr>`).join("")
    || `<tr><td colspan="15" class="muted">No rows match.</td></tr>`;
}

function wireNight() {
  document.querySelectorAll(".seg[data-n]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    N[g.dataset.n] = b.dataset.v;
    renderNightRows();
  }));
}
