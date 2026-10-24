// V3 funnel tab: where live swaps drop out of the screen, and the learned victim guard-slack distribution.
"use strict";
const FUNNEL_STEPS = [
  ["decoded", "V3 swaps with pool state"],
  ["router_decoded", "guard read from router calldata"],
  ["aggregator_decoded", "guard read from aggregator calldata"],
  ["default_decoded", "guard estimated (hidden)"],
  ["guardless", "guard set to zero"],
  ["mesp_zero", "exposure below round-trip fee"],
  ["mesp_positive", "strict target (MESP > fees)"],
];

function funnelBar(label, n, top) {
  const w = top ? Math.max(0.4, (100 * n) / top) : 0;
  const share = top ? ((100 * n) / top).toFixed(1) + "%" : "";
  return `<div class="hbar"><span class="x">${label}</span>
    <span class="track"><i style="width:${w}%"></i></span>
    <span class="v">${fmt.int(n)} <small>${share}</small></span></div>`;
}

function renderFunnel(st) {
  const box = $("#funnel-bars");
  if (!box) return;
  const f = (st && st.funnel) || {};
  const top = f.decoded || 0;
  box.innerHTML = FUNNEL_STEPS.map(([k, label]) => funnelBar(label, f[k] || 0, top)).join("");
  const g = (st && st.guard) || {};
  const pct = (v) => (v == null ? "n/a" : (100 * v).toFixed(4) + "%");
  $("#guard-stats").innerHTML = [
    stat("Guards observed", fmt.int(g.n_observed || 0), "decodable swaps this run + stored"),
    stat("Median slack", pct(g.p50), `p25 ${pct(g.p25)} / p75 ${pct(g.p75)}`),
    stat("90th percentile", pct(g.p90), `p10 ${pct(g.p10)}`),
    stat("Default for hidden guards", pct(g.default_slack), `percentile ${g.percentile ?? ""}`),
  ].join("");
}
