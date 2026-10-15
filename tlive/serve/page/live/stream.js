// Live feed: one snapshot (strict rows + wallet state), then the event stream for strict, wallet and stats.
// Also owns the top tab track that switches between the Strict Targets and Wallet panels.
"use strict";
const TOPICS = "stats,strict,wallet";

async function loadSnapshot() {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const snap = await (await fetch(`api/snapshot?parts=${TOPICS}`, { signal: ctl.signal })).json();
    S.strict = (snap.strict || []).reverse();
    walletInit(snap.wallet || {});
    setStats(snap.stats);
    walletSlot(snap.stats && snap.stats.slot);
  } catch (e) {
    $("#live-t").textContent = "no screener";
  } finally {
    clearTimeout(t);
  }
  renderAll();
}

function openStream() {
  const es = new EventSource(`api/stream?t=${TOPICS}`);
  es.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "strict") addStrict(m.d);
    else if (m.t === "wallet") walletEvent(m.d);
    else if (m.t === "stats") { setStats(m.d); walletSlot(m.d && m.d.slot); }
  };
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
  es.onopen = renderLiveDot;
}

function wireTabs() {
  const tabs = document.querySelectorAll("#tabs .tab");
  const panels = document.querySelectorAll("[data-panel]");
  tabs.forEach((b) => b.addEventListener("click", () => {
    tabs.forEach((x) => x.setAttribute("aria-selected", x === b));
    panels.forEach((p) => { p.hidden = p.dataset.panel !== b.dataset.tab; });
    renderAll();                       // strict table catch-up after being hidden
    walletRender();                    // wallet tables catch-up after being hidden
  }));
}

wireTabs();
wireStrict();
loadSnapshot().then(openStream);
