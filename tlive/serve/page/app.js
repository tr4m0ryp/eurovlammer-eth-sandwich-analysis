// Live Targets tab and the live dot: visible optimal-like buys from the running screener.
// Local mode reads /api/snapshot then the event stream; artifact mode reads live/snapshot.
"use strict";
const MAX_ROWS = 400;
const S = { visible: [], vview: "", stats: {} };

function renderLiveDot() {
  const t = S.stats;
  if (!t.slot) return;
  $("#live").className = "live " + (t.lag < 40 ? "on" : "off");
  $("#live-t").textContent = t.lag < 40 ? `live, slot ${fmt.int(t.slot)}` : `behind ${t.lag} slots`;
}

function renderVisible(freshKey) {
  const v = S.stats.vis || {};
  const rows = S.visible.filter((x) => !S.vview || x.vis === S.vview);
  $("#vis-live-stats").innerHTML = [
    stat("Optimal-like buys", fmt.int(v.n), "high score + sandwichable"),
    stat("Visible to us", fmt.int(v.visible), "lag < 4, not tipped, not routed"),
    stat("Marginal", fmt.int(v.marginal), "one protective factor"),
    stat("Invisible", fmt.int(v.invisible), "lag too high or private submission"),
  ].join("");
  $("#vis-live-rows").innerHTML = rows.slice(0, MAX_ROWS).map((x) => `<tr class="${freshKey && freshKey.has(x.signature) ? "new" : ""}">
    <td class="num">${fmt.time(x.block_time ? x.block_time * 1000 : x.seen_ms || Date.now())}</td><td class="num">${x.slot}</td>
    <td>${x.venue === "pump" ? "pump" : "PumpSwap"}</td>
    <td class="num">${fmt.sol(x.sol)}</td><td class="num">${fmt.int(x.fee_bps)}</td><td class="num">${fmt.slack(x.slack)}</td>
    <td class="num">${x.mesp ? fmt.n(x.mesp / 1e9, 4) : "0"}</td><td class="num"><b>${fmt.n(x.p, 3)}</b></td>
    <td class="mono">${fmt.short(x.router)}</td><td>${x.jito_tip ? '<span class="tag">tipped</span>' : ""}</td>
    <td class="num">${x.vis_lag ?? ""}</td>
    <td><span class="tag vis-${x.vis}">${x.vis}</span></td>
    <td><a class="mono" href="https://solscan.io/tx/${x.signature}" target="_blank" rel="noopener">${x.signature.slice(0, 8)}</a></td></tr>`).join("")
    || `<tr><td colspan="13" class="muted">No visible optimal targets yet. Waiting for buys that pass our filter and are visible to us.</td></tr>`;
  $("#vis-live-n").textContent = `${rows.length} shown of ${S.visible.length} in memory`;
}

function onEvent(m) {
  if (m.t === "stats") {
    S.stats = m.d;
    renderLiveDot();
    renderVisible();
  } else if (m.t === "visible") {
    S.visible.unshift(m.d);
    if (S.visible.length > 2000) S.visible.length = 2000;
    renderVisible(new Set([m.d.signature]));
  }
}

function wireLive() {
  document.querySelectorAll(".seg[data-lv]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    S.vview = b.dataset.v;
    renderVisible();
  }));
}

// Artifact mode: the screener pushes live/snapshot about once a minute (tlive.serve.artifact).
function liveArtifact(db) {
  let updated = 0;
  const tick = () => {
    if (!updated) return;
    const age = Math.round((Date.now() - updated) / 1000);
    $("#live").className = "live " + (age < 180 ? "on" : "off");
    $("#live-t").textContent = age < 180 ? `live, updated ${age}s ago` : `screener stopped ${fmt.time(updated)} UTC`;
  };
  setInterval(tick, 1000);
  db.doc("live/snapshot").onSnapshot((snap) => {
    if (!snap.exists) return;
    const d = snap.data();
    S.stats = d.stats || {};
    S.visible = (d.visible || []).slice();
    updated = d.updated_ms || Date.now();
    renderVisible();
    tick();
  }, () => {});
}

async function liveLocal() {
  try {
    const snap = await (await fetch("api/snapshot")).json();
    S.stats = snap.stats || {};
    S.visible = (snap.visible || []).reverse();
  } catch (e) { $("#live-t").textContent = "no screener"; }
  renderLiveDot();
  renderVisible();
  const es = new EventSource("api/stream");
  es.onmessage = (ev) => onEvent(JSON.parse(ev.data));
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
  es.onopen = renderLiveDot;
}
