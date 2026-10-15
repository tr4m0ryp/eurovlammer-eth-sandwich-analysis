"""Capital-allocation decision engine, settled against real on-chain sandwich data.

Commits the wallet to the strict screener's opportunities, then resolves each position
``RESOLVE_DELAY`` slots later against the screener's *real* settlement feed (``set_victims``): a
committed victim that actually got sandwiched is a win (a race, won with ``win_rate``, that realises
only ``capture_fraction`` of the predicted MESP, minus the Jito tip); a victim that stayed clean is
an unwind at adverse movement. The delay must exceed the screener's ``SETTLE`` window, or every
position resolves before its ground truth arrives and is misread as "no counterparty".
"""
from __future__ import annotations

import math
import os
import random
import time

LAMPORTS = 1_000_000_000


def _fmt_key(key: tuple) -> str:
    sig, evi = key
    return f"{sig}:{evi}" if sig is not None else f"ev:{evi}"


class Allocator:
    # Tunables (framework §7 / §12). SOL unless stated otherwise.
    C0 = 0.5              # initial capital and concurrent-exposure cap
    ADVERSE = 0.01        # fractional adverse-movement loss when the lock expires with no counterparty
    RESOLVE_DELAY = 15    # slots to wait before resolving: the screener settles a victim ~14 slots after
                          # its buy (SETTLE = WINDOW_SLOTS + 6 = 14), so we must look it up after that.
    BETA = float(os.environ.get("TLIVE_BETA", "0.004"))       # pool-crowding penalty, per prior hit, per (1 - p)
    SAME_POOL = 0.01      # extra penalty when the pool already has an active position
    THETA0 = float(os.environ.get("TLIVE_THETA0", "0.012"))   # expected-net-return threshold at the cap (scarce)
    THETA_INF = float(os.environ.get("TLIVE_THETA_INF", "0.004"))  # threshold as the wallet grows large
    T1, T2, T3 = 1.0, 2.0, 5.0     # wallet tiers, multiples of C0
    ALPHA_SCARCE = float(os.environ.get("TLIVE_ALPHA_SCARCE", "0.5"))  # scarce tier: one position <= this share
    WIN_RATE = 0.5        # P(we win the race) given a real sandwich landed on the victim
    CAPTURE_FRACTION = 0.25  # realised share of predicted MESP (calibration: 11-27% above 0.01 SOL)
    TIP_LAMPORTS = 100_000    # Jito tip paid per win
    # Selectivity gates (the lever on "no CP"): only commit targets the model is confident will be
    # sandwiched, and skip sub-penny sandwiches real attackers ignore. Overridable via env.
    MIN_P = float(os.environ.get("TLIVE_MIN_COMMIT_P", "0.1"))            # model-score floor
    MIN_PI_LAMPORTS = int(os.environ.get("TLIVE_MIN_COMMIT_PI", "1000000"))  # 0.001 SOL
    HISTORY_MAX = 500

    def __init__(self, initial_sol: float = 0.5, budget_sol: float = 0.5, seed: int | None = None):
        self.wallet = int(round(initial_sol * LAMPORTS))
        self.budget = int(round(budget_sol * LAMPORTS))
        self.rng = random.Random(seed)
        self.active: dict[tuple, dict] = {}
        self.active_pools: dict[str, int] = {}
        self.history: list[dict] = []
        self.victims: dict = {}       # run.py's settle feed: (sig, ev_index) -> (key, attacker, profit, ...)
        self.total_locked = 0
        self.total_profit = 0
        self.n_committed = 0
        self.n_completed = 0
        self.n_wins = self.n_race_loss = self.n_no_counterparty = 0
        self.n_sandwiched = 0        # committed victims that a real sandwich actually landed on
        self.sum_realized = 0        # sum of real attacker profit over those (for live calibration)

    def set_victims(self, victims: dict) -> None:
        """Point the engine at run.py's live settle dict (real sandwiches, updated each block)."""
        self.victims = victims

    # ---- units ----
    @staticmethod
    def _sol(v) -> float:
        return v / LAMPORTS

    def wallet_sol(self) -> float:
        return self._sol(self.wallet)

    # ---- decision rule ----
    def _theta(self, w: float) -> float:
        return self.THETA_INF + (self.THETA0 - self.THETA_INF) * math.exp(-w / self.C0)

    def _capture_fraction(self, pi: int) -> float:
        """Realised share of predicted MESP, size-dependent per the notes' calibration: attackers
        realise 11-27% of the prediction above 0.01 SOL, and (relatively) more below it."""
        return 0.19 if pi >= 10_000_000 else 0.6

    def _expected_net(self, row: dict) -> float:
        # ex-ante counterparty probability is the model score p (P(this buy is a sandwich target),
        # boosted by prior same-side pool flow — the notes' strongest victim-rate feature (0.3 → 93
        # per mille across its range). expected net = q*roi - (1-q)*adverse.
        roi = row.get("roi") or 0.0
        p = min(max(row.get("p") or 0.0, 0.02), 0.9)
        freq = row.get("pool_freq") or 0
        q = min(p * (1.0 + 0.1 * min(freq, 5)), 0.95)     # up to +50% for freq >= 5
        return q * roi - (1.0 - q) * self.ADVERSE

    def _decide(self, row: dict) -> bool:
        kappa = row.get("front_budget")
        if not kappa or kappa <= 0:
            return False
        p = row.get("p") or 0.0
        pi = int(row.get("mesp_budget") or 0)
        # hard selectivity gates: model confidence + minimum size (the "no CP" lever)
        if p < self.MIN_P or pi < self.MIN_PI_LAMPORTS:
            return False
        pool = row.get("pool") or ""
        freq = row.get("pool_freq") or 0
        mu = self._expected_net(row) - self.BETA * freq * (1.0 - p)
        if pool in self.active_pools:
            mu -= self.SAME_POOL
        if mu < self._theta(self.wallet_sol()):
            return False
        # scarce tier: never let one position swallow more than a share of the wallet
        if self.wallet_sol() <= self.T1 * self.C0:
            return self._sol(kappa) <= self.ALPHA_SCARCE * self.wallet_sol()
        return True

    # ---- position lifecycle ----
    def _commit(self, row: dict, slot: int, events: list[dict]) -> None:
        kappa = int(row["front_budget"])
        if self.wallet < kappa or self.total_locked + kappa > self.budget:
            return
        lock = self.RESOLVE_DELAY
        key = (row.get("signature"), row.get("ev_index"))
        pool = row.get("pool") or ""
        self.wallet -= kappa
        self.total_locked += kappa
        self.active[key] = {
            "key": key, "pool": pool, "kappa": kappa, "pi": int(row["mesp_budget"]),
            "roi": row.get("roi"), "commit_slot": slot, "unlock_slot": slot + lock,
            "seen_ms": row.get("seen_ms"),
        }
        self.active_pools[pool] = self.active_pools.get(pool, 0) + 1
        self.n_committed += 1
        events.append({
            "type": "commit", "key": _fmt_key(key), "pool": pool,
            "kappa": self._sol(kappa), "pi": self._sol(int(row["mesp_budget"])),
            "roi": row.get("roi"), "wallet_after": self.wallet_sol(),
            "lock_slots": lock, "unlock_slot": slot + lock, "slot": slot,
            "seen_ms": row.get("seen_ms"),
        })

    def _resolve(self, slot: int, events: list[dict]) -> None:
        due = [k for k, p in self.active.items() if p["unlock_slot"] <= slot]
        for key in due:
            pos = self.active.pop(key)
            pool = pos["pool"]
            self.active_pools[pool] -= 1
            if self.active_pools[pool] <= 0:
                del self.active_pools[pool]
            # settle against the real sandwich feed, not a Bernoulli draw.
            hit = self.victims.get(pos["key"])
            if hit is not None:                       # a real sandwich landed on this victim
                self.n_sandwiched += 1
                self.sum_realized += hit[2]           # x.profit: the real attacker's realised profit
                if self.rng.random() < self.WIN_RATE:
                    realized = int(pos["pi"] * self._capture_fraction(pos["pi"]))
                    gain = max(0, realized - self.TIP_LAMPORTS)
                    status = "win"
                else:
                    gain = -self.TIP_LAMPORTS          # lost the race, paid the tip, captured nothing
                    status = "race_loss"
            else:                                      # no counterparty -> unwind at adverse movement
                gain = -int(pos["kappa"] * self.ADVERSE)
                status = "no_counterparty"
            self.wallet += pos["kappa"] + gain
            self.total_locked -= pos["kappa"]
            self.total_profit += gain
            self.n_completed += 1
            self.n_wins += status == "win"
            self.n_race_loss += status == "race_loss"
            self.n_no_counterparty += status == "no_counterparty"
            entry = {
                "key": pos["key"], "pool": pool, "kappa": pos["kappa"], "pi": pos["pi"],
                "gain": gain, "status": status,
                "commit_slot": pos["commit_slot"], "unlock_slot": pos["unlock_slot"],
                "seen_ms": pos["seen_ms"], "resolved_ms": int(time.time() * 1000),
            }
            self.history.append(entry)
            del self.history[:-self.HISTORY_MAX]
            events.append({
                "type": "resolve", "key": _fmt_key(pos["key"]), "pool": pool,
                "kappa": self._sol(pos["kappa"]), "pi": self._sol(pos["pi"]),
                "gain": self._sol(gain), "status": entry["status"],
                "wallet_after": self.wallet_sol(), "commit_slot": pos["commit_slot"],
                "unlock_slot": pos["unlock_slot"], "seen_ms": pos["seen_ms"],
            })

    def step(self, slot: int, rows: list[dict] | None = None) -> list[dict]:
        """Resolve matured positions, then decide on ``rows`` at ``slot``. Returns stream events.

        Accepted rows are committed in descending expected-value order (p × predicted MESP), so the
        scarce capital goes to the highest-expectation targets first.
        """
        events: list[dict] = []
        self._resolve(slot, events)
        accepted = [r for r in (rows or ()) if self._decide(r)]
        accepted.sort(key=lambda r: (r.get("p") or 0.0) * (r.get("mesp_budget") or 0), reverse=True)
        for row in accepted:
            self._commit(row, slot, events)
        if events:
            events.append({"type": "stats", **self.stats()})
        return events

    # ---- snapshots ----
    def stats(self) -> dict:
        return {
            "wallet": self.wallet_sol(), "total_locked": self._sol(self.total_locked),
            "total_profit": self._sol(self.total_profit), "n_active": len(self.active),
            "n_completed": self.n_completed, "n_wins": self.n_wins,
            "n_race_loss": self.n_race_loss, "n_no_counterparty": self.n_no_counterparty,
            "n_sandwiched": self.n_sandwiched, "sum_realized": self._sol(self.sum_realized),
        }

    def _active_row(self, p: dict) -> dict:
        return {"key": _fmt_key(p["key"]), "pool": p["pool"], "kappa": self._sol(p["kappa"]),
                "pi": self._sol(p["pi"]), "roi": p["roi"], "commit_slot": p["commit_slot"],
                "unlock_slot": p["unlock_slot"], "seen_ms": p["seen_ms"], "status": "locked"}

    def _history_row(self, h: dict) -> dict:
        return {"key": _fmt_key(h["key"]), "pool": h["pool"], "kappa": self._sol(h["kappa"]),
                "pi": self._sol(h["pi"]), "gain": self._sol(h["gain"]), "status": h["status"],
                "commit_slot": h["commit_slot"], "unlock_slot": h["unlock_slot"],
                "seen_ms": h["seen_ms"], "resolved_ms": h["resolved_ms"]}

    def state(self) -> dict:
        """The full wallet state for ``/api/snapshot`` and the artifact body."""
        positions = sorted(self.active.values(), key=lambda p: p["commit_slot"], reverse=True)
        return {
            "wallet": self.wallet_sol(), "total_locked": self._sol(self.total_locked),
            "total_profit": self._sol(self.total_profit), "n_active": len(self.active),
            "n_completed": self.n_completed, "n_wins": self.n_wins,
            "n_race_loss": self.n_race_loss, "n_no_counterparty": self.n_no_counterparty,
            "n_sandwiched": self.n_sandwiched, "sum_realized": self._sol(self.sum_realized),
            "positions": [self._active_row(p) for p in positions],
            "history": [self._history_row(h) for h in reversed(self.history[-200:])],
        }
