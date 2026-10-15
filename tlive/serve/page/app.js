// Strict sandwich targets live tab. Only the budget-constrained screener results.
// Local mode reads /api/snapshot then the event stream.
"use strict";
const MAX_ROWS = 400;
const S = { strict: [], slview: "", stats: {} };

function renderLiveDot() {
  const t = S.stats;
  if (!t.slot) return;
  $("#live").className = "live " + (t.lag < 40 ? "on" : "off");
  $("#live-t").textContent = t.lag < 40 ? `live, slot ${fmt.int(t.slot)}` : `behind ${t.lag} slots`;
}

function renderStrict(freshKey) {
  const sv = S.stats.strict_vis || {};
  const sl = S.slview;
  const rows = S.strict.filter((x) => !sl || (sl === "high_roi" ? (x.roi || 0) >= 0.05 : (x.pool_freq || 0) >= 3));
  $("#strict-live-stats").innerHTML = [
    stat("Visible + sandwichable", fmt.int(sv.n || 0), "passed visibility + interest filters"),
    stat("Strict (budget)", fmt.int(sv.strict || 0), `0.5 SOL budget, ROI >= ${fmt.pct(0.01)}, front <= 0.5 SOL`),
  ].join("");
  $("#strict-live-rows").innerHTML = rows.slice(0, MAX_ROWS).map((x) => `<tr class="${freshKey && freshKey.has(x.signature) ? "new" : ""}">
    <td class="num">${fmt.time(x.seen_ms || Date.now())}</td><td class="num">${x.slot}</td>
    <td>${x.venue === "pump" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.sol(x.sol)}</td><td class="num">${fmt.int(x.fee_bps)}</td><td class="num">${fmt.slack(x.slack)}</td>
    <td class="num"><b>${fmt.n((x.mesp_budget || 0) / 1e9, 5)}</b></td>
    <td class="num">${fmt.n((x.front_budget || 0) / 1e9, 4)}</td>
    <td class="num ${(x.roi || 0) >= 0.05 ? "good" : ""}">${fmt.pct(x.roi)}</td>
    <td class="num">${fmt.int(x.pool_freq)}</td>
    <td class="num">${fmt.n(x.p, 3)}</td>
    <td class="mono">${fmt.short(x.router)}</td>
    <td><span class="tag vis-${x.vis}">${x.vis}</span></td>
    <td><a class="mono" href="https://solscan.io/tx/${x.signature}" target="_blank" rel="noopener">${x.signature.slice(0, 8)}</a></td></tr>`).join("")
    || `<tr><td colspan="14" class="muted">No strict targets yet. Waiting for buys that pass the budget-constrained filter.</td></tr>`;
  $("#strict-live-n").textContent = `${rows.length} shown of ${S.strict.length} in memory`;
}

function onEvent(m) {
  if (m.t === "stats") {
    S.stats = m.d;
    renderLiveDot();
    renderStrict();
  } else if (m.t === "strict") {
    S.strict.unshift(m.d);
    if (S.strict.length > 2000) S.strict.length = 2000;
    renderStrict(new Set([m.d.signature]));
  }
}

function wireLive() {
  document.querySelectorAll(".seg[data-sl]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    S.slview = b.dataset.v;
    renderStrict();
  }));
}

async function liveLocal() {
  try {
    const snap = await (await fetch("api/snapshot")).json();
    S.stats = snap.stats || {};
    S.strict = (snap.strict || []).reverse();
  } catch (e) { $("#live-t").textContent = "no screener"; }
  renderLiveDot();
  renderStrict();
  const es = new EventSource("api/stream");
  es.onmessage = (ev) => onEvent(JSON.parse(ev.data));
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
  es.onopen = renderLiveDot;
}

wireLive();
liveLocal();