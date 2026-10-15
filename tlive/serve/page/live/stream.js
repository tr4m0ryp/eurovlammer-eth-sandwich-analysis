// Live feed: one snapshot of the strict rows, then the event stream for strict rows and stats only.
"use strict";
const TOPICS = "stats,strict";

async function loadSnapshot() {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const snap = await (await fetch(`api/snapshot?parts=${TOPICS}`, { signal: ctl.signal })).json();
    S.strict = (snap.strict || []).reverse();
    setStats(snap.stats);
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
    else if (m.t === "stats") setStats(m.d);
  };
  es.onerror = () => { $("#live").className = "live off"; $("#live-t").textContent = "reconnecting"; };
  es.onopen = renderLiveDot;
}

wireStrict();
loadSnapshot().then(openStream);
