// Exposure tab: what lowered an optimal target's chance of being sandwiched (exposure/summary).
// Read-only, retrospective. The defensive mirror of the Optimal patterns.
"use strict";

async function loadExposure(db) {
  const snap = await db.doc("exposure/summary").get();
  if (!snap.exists) return;
  const s = snap.data();
  $("#exp-stats").innerHTML = [
    stat("Optimal targets", fmt.int(s.n_optimal), "the night's most extractable buys"),
    stat("Attacked overall", fmt.pct(s.base_rate), "share actually sandwiched"),
    stat("Best protection", "Routing", `cut the attacked rate ${fmt.pct(s.shields[2].drop)}`),
  ].join("");
  $("#exp-shield").innerHTML = s.shields.map((r) => {
    const good = r.drop != null && r.drop > 0;
    return `<tr><td>${r.label}</td><td class="num">${fmt.int(r.n)}</td><td class="num">${fmt.pct(r.attacked)}</td>
      <td class="num">${fmt.pct(r.vs_without)}</td>
      <td class="num ${good ? "good" : "bad"}">${r.drop == null ? "" : (r.drop > 0 ? "-" : "+") + fmt.pct(Math.abs(r.drop))}</td></tr>`;
  }).join("");
  const f = s.flow || [];
  const max = Math.max(...f.map((x) => x.attacked), 1e-6);
  $("#exp-flow").innerHTML = f.map((x) => `<div class="bar" title="${fmt.int(x.n)} buys">
    <div class="v">${(100 * x.attacked).toFixed(1)}%</div><div class="col" style="height:${(100 * x.attacked) / max}%"></div>
    <div class="x">${x.band.slice(2)}</div></div>`).join("");
}
