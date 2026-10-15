// Page boot: formatters and shared helpers for the strict screener live page.
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