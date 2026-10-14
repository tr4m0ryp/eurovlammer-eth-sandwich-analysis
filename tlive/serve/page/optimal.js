// Optimal tab: the night's most profitable realistic sandwich targets and the patterns behind them.
// Reads optimal/summary + optimal/rows_NNN from the artifact database (tlive.store.optimal_push).
"use strict";
const O = { s: null, rows: [], view: "", sort: "rank" };
const RULE_TXT = {
  A: "Buy above 1.45 SOL and above 0.56% of the coin's market cap",
  B: "Buy up to 1.45 SOL, above 3.3% of market cap, slack above 33%",
  other: "Everything else",
};

async function loadOptimal(db) {
  const snap = await db.doc("optimal/summary").get();
  if (!snap.exists) { $("#opt-note").textContent = "No optimal data published yet."; return; }
  O.s = snap.data();
  renderOptSummary();
  const ids = Array.from({ length: O.s.n_docs }, (_, k) => "optimal/rows_" + String(k).padStart(3, "0"));
  const docs = await Promise.all(ids.map((id) => db.doc(id).get().catch(() => null)));
  const cols = O.s.columns;
  O.rows = docs.filter((d) => d && d.exists).flatMap((d) => d.data().rows)
    .map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  $("#opt-note").textContent = `${O.rows.length.toLocaleString("en-US")} optimal targets loaded`;
  renderOptRows();
}

function renderOptSummary() {
  const f = O.s.funnel;
  $("#opt-stats").innerHTML = [
    stat("Risk buys", fmt.int(f.risk), "the night, buy side"),
    stat("Realistic", fmt.int(f.realistic), "math in range: bounded spend, slack >= 0, front fits the pool"),
    stat("Profitable", fmt.int(f.qualified), "calibrated gain beats the attacker's landing cost"),
    stat("Optimal", fmt.int(f.optimal), `top 10%: net gain >= ${fmt.n(f.cutoff_sol, 4)} SOL`),
    stat("Optimal sandwiched", fmt.pct(f.optimal_hit), `vs ${fmt.pct(f.rest_hit)} for the other profitable buys`),
    stat("Net gain available", fmt.n(f.optimal_net_sol, 1) + " SOL", "calibrated, across the optimal set"),
  ].join("");
  $("#opt-rules").innerHTML = O.s.rules.map((r) => `<tr><td>${RULE_TXT[r.rule] || r.rule}</td>
    <td class="num">${fmt.int(r.n)}</td><td class="num">${fmt.int(r.n_opt)}</td>
    <td class="num">${fmt.pct(r.prec)}</td><td class="num"><b>${fmt.pct(r.rec)}</b></td></tr>`).join("");
  $("#opt-attack").innerHTML = O.s.attack.map((a) => `<tr><td>${a.hot ? "Hot pool" : "Quiet pool"}</td>
    <td>${a.whale ? "Whale, above 9.9 SOL" : "Up to 9.9 SOL"}</td><td class="num">${fmt.int(a.n)}</td>
    <td class="num"><b>${fmt.pct(a.hit)}</b></td><td class="num">${fmt.n(a.net_sol, 1)}</td></tr>`).join("");
  $("#opt-routes").innerHTML = O.s.routes.map((r) => `<tr><td class="mono">${r.route}</td>
    <td class="num">${fmt.int(r.n)}</td><td class="num">${fmt.pct(r.hit)}</td></tr>`).join("");
  const [opt, rest] = O.s.profile;
  const row = (k, lab, f) => `<tr><td>${lab}</td><td class="num">${f(opt[k])}</td><td class="num">${f(rest[k])}</td></tr>`;
  $("#opt-profile").innerHTML = [
    row("buy_sol", "Median buy, SOL", (v) => fmt.n(v, 2)), row("pct_mc", "Median % of market cap", (v) => fmt.n(v, 2) + "%"),
    row("mc_sol", "Median market cap, SOL", (v) => fmt.n(v, 1)), row("no_limit", "No slippage limit", fmt.pct),
    row("on_curve", "On the pump bonding curve", fmt.pct), row("tip", "Paid a Jito tip", fmt.pct),
  ].join("");
}

function renderOptRows() {
  const key = { rank: "rank", size: "pct_mc", buy: "sol", time: "block_time" }[O.sort];
  const asc = O.sort === "rank";
  const rows = O.rows.filter((r) => !O.view || (O.view === "hit" ? r.sandwiched : O.view === "missed" ? !r.sandwiched : O.view === "hot" ? r.hot : !r.hot))
    .sort((a, b) => (asc ? a[key] - b[key] : (b[key] ?? -1) - (a[key] ?? -1)));
  $("#opt-count").textContent = `${rows.length.toLocaleString("en-US")} rows, showing the first 500`;
  $("#opt-rows").innerHTML = rows.slice(0, 500).map((r) => `<tr>
    <td class="num">${r.rank}</td><td class="num">${fmt.time(r.block_time * 1000)}</td><td>${r.venue === "p" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.n(r.sol, 3)}</td><td class="num">${fmt.n(r.mc_sol, 1)}</td><td class="num">${fmt.n(r.pct_mc, 2)}%</td>
    <td class="num">${fmt.int(r.fee_bps)}</td><td class="num">${fmt.slack(r.slack)}</td>
    <td class="num">${fmt.n(r.gain_pred, 4)}</td><td class="num"><b>${fmt.n(r.net_real, 4)}</b></td>
    <td><span class="tag ${r.hot ? "brand" : ""}">${r.hot ? "hot" : "quiet"}</span></td><td class="mono">${r.route}</td>
    <td><span class="tag ${r.sandwiched ? "sandwiched" : "clean"}">${r.sandwiched ? "sandwiched" : "missed"}</span></td>
    <td class="num">${r.taken_sol != null ? fmt.n(r.taken_sol, 4) : ""}</td>
    <td><a class="mono" href="https://solscan.io/tx/${r.signature}" target="_blank" rel="noopener">${r.signature.slice(0, 8)}</a></td></tr>`).join("")
    || `<tr><td colspan="15" class="muted">No rows match.</td></tr>`;
}

function wireOptimal() {
  document.querySelectorAll(".seg[data-o]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    O[g.dataset.o] = b.dataset.v;
    renderOptRows();
  }));
}
