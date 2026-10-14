// Page boot for both local and artifact mode. Delegates to app.js for the local live page.
"use strict";
const $ = (s) => document.querySelector(s);

const fmt = {
  int: (v) => (v == null ? "" : Math.round(v).toLocaleString("en-US")),
  n: (v, d = 2) => (v == null ? "" : Number(v).toFixed(d)),
  pct: (v) => (v == null || !isFinite(v) ? "n/a" : (100 * v).toFixed(1) + "%"),
  slack: (v) => (v == null ? "?" : v >= 10 ? "none" : (100 * v).toFixed(1) + "%"),
  time: (ms) => new Date(ms).toISOString().slice(11, 19),
  sol: (l) => (l == null ? "" : (l / 1e9).toFixed(l >= 1e10 ? 1 : 3)),
  short: (a) => (a ? a.slice(0, 4) + ".." + a.slice(-4) : "direct"),
};

function stat(label, v, s) {
  return `<div class="stat"><div class="lbl">${label}</div><div class="v">${v}</div><div class="s">${s || ""}</div></div>`;
}

function wireTabs() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== b.dataset.tab));
  }));
}

async function bootArtifact() {
  wireTabs();
  const db = await window.claude.use("db");
  if (!db) { $("#live-t").textContent = "sign in to claude.ai to load the study"; return; }
  $("#live-t").textContent = "study data, read-only";
  $("#live").className = "live on";
  wireOptimal();
  wireVisibility();
  loadOptimal(db).catch(() => { $("#opt-note").textContent = "Optimal data unavailable"; });
  loadVisibility(db).catch(() => { $("#vis-note").textContent = "Visibility data unavailable"; });
  loadExposure(db).catch(() => {});
}

async function boot() {
  wireTabs();
  if (window.claude && window.claude.use) return bootArtifact();
  // Local live page: app.js handles the rest
  if (typeof bootLocal === "function") bootLocal();
}

boot();