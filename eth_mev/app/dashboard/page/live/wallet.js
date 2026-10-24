// Wallet tab: the allocation engine's positions and history, fed by the "wallet" SSE topic.
// The snapshot seeds the full state; typed stream events (commit / resolve / stats) update it live.
// Rendering is debounced so a burst of resolves lands in one DOM write. Function names are prefixed
// so they never collide with strict.js globals (both scripts share one page scope).
"use strict";
const MAX_ACTIVE = 100;
const MAX_HIST = 200;
const W = { wallet: 0.5, total_locked: 0, total_profit: 0, n_active: 0, n_completed: 0,
            n_wins: 0, n_race_loss: 0, n_no_counterparty: 0, n_sandwiched: 0,
            active: [], history: [], slot: 0, timer: 0 };

const sol4 = (v) => fmt.n(v, 4);
const signed = (v) => (v >= 0 ? "+" : "") + sol4(v);

function walletActiveRow(p) {
  const left = Math.max(0, (p.unlock_slot || 0) - W.slot);
  return `<tr>
    <td class="num">${fmt.time(p.seen_ms)}</td>
    <td class="mono">${fmt.short(p.pool)}</td>
    <td class="num">${sol4(p.kappa)}</td>
    <td class="num">${sol4(p.pi)}</td>
    <td class="num">${fmt.int(left)}</td>
    <td><span class="tag pending">locked</span></td></tr>`;
}

function walletHistRow(h) {
  const st = h.status;
  const good = st === "win";
  const label = st === "win" ? "win" : st === "race_loss" ? "race loss" : st === "no_counterparty" ? "no CP" : st;
  return `<tr>
    <td class="num">${fmt.time(h.seen_ms)}</td>
    <td class="num">${fmt.time(h.resolved_ms)}</td>
    <td class="mono">${fmt.short(h.pool)}</td>
    <td class="num">${sol4(h.kappa)}</td>
    <td class="num">${sol4(h.pi)}</td>
    <td class="num ${good ? "good" : "bad"}">${signed(h.gain)}</td>
    <td><span class="tag ${good ? "good" : "bad"}">${label}</span></td></tr>`;
}

const ACTIVE_EMPTY = `<tr data-empty><td colspan="6" class="muted">No active positions.</td></tr>`;
const HIST_EMPTY = `<tr data-empty><td colspan="7" class="muted">No completed trades yet.</td></tr>`;

function walletStats() {
  $("#wallet-stats").innerHTML = [
    stat("Wallet balance", sol4(W.wallet) + " SOL", "free capital"),
    stat("Total locked", sol4(W.total_locked) + " SOL", "Σ κ of active positions"),
    stat("Total profit", signed(W.total_profit) + " SOL", "realized (25% of π, minus tips)"),
    stat("Wins", fmt.int(W.n_wins), `race loss ${W.n_race_loss} · no CP ${W.n_no_counterparty}`),
    stat("Active positions", fmt.int(W.n_active), "currently locked"),
    stat("Completed", fmt.int(W.n_completed), `${W.n_sandwiched} of ${W.n_completed} actually sandwiched`),
  ].join("");
}

function walletActive() {
  $("#wallet-active-rows").innerHTML = W.active.slice(0, MAX_ACTIVE).map(walletActiveRow).join("") || ACTIVE_EMPTY;
  $("#wallet-active-n").textContent = W.active.length ? `${W.active.length} locked` : "none";
}

function walletHist() {
  $("#wallet-hist-rows").innerHTML = W.history.slice(0, MAX_HIST).map(walletHistRow).join("") || HIST_EMPTY;
  $("#wallet-hist-n").textContent = W.history.length ? `${W.history.length} resolved` : "none";
}

function walletRender() {
  walletStats();
  walletActive();
  walletHist();
}

function scheduleRender() {
  if (W.timer) return;
  W.timer = setTimeout(() => { W.timer = 0; if (!document.hidden) walletRender(); }, 250);
}

function walletInit(state) {
  W.wallet = state.wallet ?? 0.5;
  W.total_locked = state.total_locked ?? 0;
  W.total_profit = state.total_profit ?? 0;
  W.n_active = state.n_active ?? 0;
  W.n_completed = state.n_completed ?? 0;
  W.n_wins = state.n_wins ?? 0;
  W.n_race_loss = state.n_race_loss ?? 0;
  W.n_no_counterparty = state.n_no_counterparty ?? 0;
  W.n_sandwiched = state.n_sandwiched ?? 0;
  W.active = (state.positions || []).map((p) => ({ ...p }));
  W.history = (state.history || []).map((h) => ({ ...h }));
  walletRender();
}

function walletEvent(ev) {
  if (ev.type === "commit") {
    W.wallet = ev.wallet_after;
    W.total_locked += ev.kappa;
    W.n_active += 1;
    W.active.unshift({ key: ev.key, pool: ev.pool, kappa: ev.kappa, pi: ev.pi,
                       commit_slot: ev.slot, unlock_slot: ev.unlock_slot, seen_ms: ev.seen_ms });
    if (W.active.length > MAX_ACTIVE) W.active.length = MAX_ACTIVE;
  } else if (ev.type === "resolve") {
    W.wallet = ev.wallet_after;
    W.total_locked = Math.max(0, W.total_locked - ev.kappa);
    W.n_active = Math.max(0, W.n_active - 1);
    W.total_profit += ev.gain;
    W.n_completed += 1;
    W.n_wins += ev.status === "win";
    W.n_race_loss += ev.status === "race_loss";
    W.n_no_counterparty += ev.status === "no_counterparty";
    W.active = W.active.filter((p) => p.key !== ev.key);
    W.history.unshift({ pool: ev.pool, kappa: ev.kappa, pi: ev.pi, gain: ev.gain, status: ev.status,
                        commit_slot: ev.commit_slot, unlock_slot: ev.unlock_slot,
                        seen_ms: ev.seen_ms, resolved_ms: Date.now() });
    if (W.history.length > MAX_HIST) W.history.length = MAX_HIST;
  } else if (ev.type === "stats") {
    W.wallet = ev.wallet; W.total_locked = ev.total_locked;
    W.total_profit = ev.total_profit; W.n_active = ev.n_active; W.n_completed = ev.n_completed;
    W.n_wins = ev.n_wins ?? W.n_wins; W.n_race_loss = ev.n_race_loss ?? W.n_race_loss;
    W.n_no_counterparty = ev.n_no_counterparty ?? W.n_no_counterparty;
    W.n_sandwiched = ev.n_sandwiched ?? W.n_sandwiched;
  }
  scheduleRender();
}

function walletSlot(slot) {
  if (slot == null || slot === W.slot) return;
  W.slot = slot;
  if (W.active.length) scheduleRender();      // keep the unlock countdown live
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) walletRender();
});
