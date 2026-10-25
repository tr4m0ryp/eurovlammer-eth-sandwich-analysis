"""Capital-allocation decision engine, settled against real on-chain sandwich data.

Commits a fixed numeraire (ETH) budget to the strict screener's opportunities, then resolves each
position ``RESOLVE_BLOCKS`` blocks later against the *real* settlement feed (``set_settlement``): a
committed victim that actually got sandwiched is a win (a race, won with ``WIN_RATE``, that realises
only ``CAPTURE_FRACTION`` of the predicted MESP, minus the EIP-1559 cost of landing ahead); a victim
that stayed clean is an unwind at adverse movement.

Field remap from the Solana allocator:
  ``front_budget -> position_wei``, ``mesp_budget -> expected_profit_wei``,
  ``cu_price/jito_tip -> priority_fee/bribe``, ``victims dict -> (block, tx_hash)`` settlement,
  outcomes ``win / reverted_or_outbid / no_sandwich``.

EIP-1559 is modelled explicitly: the cost of landing ahead is ``base_fee + priority_fee + bribe``
(the ETH analogue of the Solana Jito tip). ``base_fee`` is folded into the per-position fee cost;
the bribe is the builder payment for inclusion order.
"""
from __future__ import annotations

import math
import os
import random
import time

from ..units import WEI
from .keys import fmt_key
from .snapshots import SnapshotMixin


class Allocator(SnapshotMixin):
    # Tunables (ETH). Overridable via env so they can be tuned without a redeploy.
    C0 = 1.0               # initial capital and concurrent-exposure cap, in ETH
    ADVERSE = 0.01          # fractional adverse-movement loss when the lock expires with no sandwich
    RESOLVE_BLOCKS = 12     # blocks to wait before resolving (settlement is same-block, so 12 is slack)
    BETA = float(os.environ.get("MEV_BETA", "0.004"))               # pool-crowding penalty, per prior hit
    SAME_POOL = 0.01        # extra penalty when the pool already has an active position
    THETA0 = float(os.environ.get("MEV_THETA0", "0.012"))           # expected-net threshold at the cap (scarce)
    THETA_INF = float(os.environ.get("MEV_THETA_INF", "0.004"))     # threshold as the wallet grows large
    T1, T2, T3 = 1.0, 2.0, 5.0     # wallet tiers, multiples of C0
    ALPHA_SCARCE = float(os.environ.get("MEV_ALPHA_SCARCE", "0.5"))  # scarce tier: one position <= this share
    WIN_RATE = 0.5          # P(we land ahead) given a real sandwich landed on the victim
    CAPTURE_FRACTION = 0.25  # realised share of predicted MESP
    BRIBE_WEI = int(os.environ.get("MEV_BRIBE_WEI", str(int(0.01 * WEI))))  # builder bribe per win
    GAS_WEI = int(os.environ.get("MEV_GAS_WEI", str(int(50 * 1e9 * 150_000))))  # ~150k gas * 50 gwei
    BASE_FEE_WEI = int(os.environ.get("MEV_BASE_FEE_WEI", str(int(20 * 1e9))))  # current base fee per gas
    # Selectivity gates (the lever on "no sandwich").
    MIN_P = float(os.environ.get("MEV_MIN_COMMIT_P", "0.1"))            # model-score floor
    MIN_PROFIT_WEI = int(os.environ.get("MEV_MIN_COMMIT_PROFIT", str(10 ** 16)))  # 0.01 ETH
    HISTORY_MAX = 500

    def __init__(self, initial_eth: float = 1.0, budget_eth: float = 1.0, seed: int | None = None):
        self.wallet = int(round(initial_eth * WEI))
        self.budget = int(round(budget_eth * WEI))
        self.rng = random.Random(seed)
        self.active: dict[tuple, dict] = {}
        self.active_pools: dict[str, int] = {}
        self.history: list[dict] = []
        self.settlement: dict = {}   # (block, tx_hash) -> (key, attacker, profit, block_no, ...)
        self.total_locked = 0
        self.total_profit = 0
        self.n_committed = 0
        self.n_completed = 0
        self.n_wins = self.n_outbid = self.n_no_sandwich = 0
        self.n_sandwiched = 0
        self.sum_realized = 0

    def set_settlement(self, settlement: dict) -> None:
        """Point the engine at the labeller's real settlement feed (updated each block)."""
        self.settlement = settlement

    # ---- units ----
    @staticmethod
    def _eth(v) -> float:
        return v / WEI

    def wallet_eth(self) -> float:
        return self._eth(self.wallet)

    # ---- decision rule ----
    def _theta(self, w: float) -> float:
        return self.THETA_INF + (self.THETA0 - self.THETA_INF) * math.exp(-w / self.C0)

    def _capture_fraction(self, pi: int) -> float:
        """Realised share of predicted MESP, size-dependent (the notes' calibration)."""
        return 0.19 if pi >= int(0.01 * WEI) else 0.6

    def _expected_net(self, row: dict) -> float:
        roi = row.get("roi") or 0.0
        p = min(max(row.get("p") or 0.0, 0.02), 0.9)
        freq = row.get("pool_freq") or 0
        q = min(p * (1.0 + 0.1 * min(freq, 5)), 0.95)
        return q * roi - (1.0 - q) * self.ADVERSE

    def _decide(self, row: dict) -> bool:
        kappa = row.get("position_wei") or row.get("front_budget")
        if not kappa or kappa <= 0:
            return False
        p = row.get("p") or 0.0
        pi = int(row.get("expected_profit_wei") or row.get("mesp_budget") or 0)
        if p < self.MIN_P or pi < self.MIN_PROFIT_WEI:
            return False
        pool = row.get("pool") or ""
        freq = row.get("pool_freq") or 0
        mu = self._expected_net(row) - self.BETA * freq * (1.0 - p)
        if pool in self.active_pools:
            mu -= self.SAME_POOL
        if mu < self._theta(self.wallet_eth()):
            return False
        if self.wallet_eth() <= self.T1 * self.C0:
            return self._eth(kappa) <= self.ALPHA_SCARCE * self.wallet_eth()
        return True

    # ---- position lifecycle ----
    def _commit(self, row: dict, block: int, events: list[dict]) -> None:
        kappa = int(row.get("position_wei") or row["front_budget"])
        if self.wallet < kappa or self.total_locked + kappa > self.budget:
            return
        lock = self.RESOLVE_BLOCKS
        key = (row.get("block"), row.get("tx_hash"))
        pool = row.get("pool") or ""
        self.wallet -= kappa
        self.total_locked += kappa
        self.active[key] = {
            "key": key, "pool": pool, "kappa": kappa, "pi": int(row.get("expected_profit_wei") or row.get("mesp_budget") or 0),
            "roi": row.get("roi"), "commit_block": block, "unlock_block": block + lock,
            "seen_ms": row.get("seen_ms"), "priority_fee": row.get("priority_fee") or 0,
            "base_fee": row.get("base_fee") or self.BASE_FEE_WEI,
        }
        self.active_pools[pool] = self.active_pools.get(pool, 0) + 1
        self.n_committed += 1
        events.append({
            "type": "commit", "key": fmt_key(key), "pool": pool,
            "position_eth": self._eth(kappa), "expected_profit_eth": self._eth(int(row.get("expected_profit_wei") or 0)),
            "roi": row.get("roi"), "wallet_after": self.wallet_eth(),
            "lock_blocks": lock, "unlock_block": block + lock, "block": block,
            "seen_ms": row.get("seen_ms"),
        })

    def _cost_of_landing(self, pos: dict) -> int:
        """EIP-1559 cost of landing ahead: bribe + the priority fee's gas uplift + base fee gas."""
        return int(pos.get("bribe") or self.BRIBE_WEI) + int(pos.get("priority_fee") or 0) * 300_000 + self.GAS_WEI

    def _resolve(self, block: int, events: list[dict]) -> None:
        due = [k for k, p in self.active.items() if p["unlock_block"] <= block]
        for key in due:
            pos = self.active.pop(key)
            pool = pos["pool"]
            self.active_pools[pool] -= 1
            if self.active_pools[pool] <= 0:
                del self.active_pools[pool]
            hit = self.settlement.get(pos["key"])
            if hit is not None:                       # a real sandwich landed on this victim
                self.n_sandwiched += 1
                self.sum_realized += hit[2]          # x.profit: the real attacker's realised profit
                if self.rng.random() < self.WIN_RATE:
                    realized = int(pos["pi"] * self._capture_fraction(pos["pi"]))
                    cost = self._cost_of_landing(pos)
                    gain = max(0, realized - cost)
                    status = "win"
                else:
                    gain = -self._cost_of_landing(pos)   # outbid: paid to land, captured nothing
                    status = "reverted_or_outbid"
            else:                                      # no sandwich -> unwind at adverse movement
                gain = -int(pos["kappa"] * self.ADVERSE) - self.GAS_WEI
                status = "no_sandwich"
            self.wallet += pos["kappa"] + gain
            self.total_locked -= pos["kappa"]
            self.total_profit += gain
            self.n_completed += 1
            self.n_wins += status == "win"
            self.n_outbid += status == "reverted_or_outbid"
            self.n_no_sandwich += status == "no_sandwich"
            entry = {
                "key": pos["key"], "pool": pool, "kappa": pos["kappa"], "pi": pos["pi"],
                "gain": gain, "status": status,
                "commit_block": pos["commit_block"], "unlock_block": pos["unlock_block"],
                "seen_ms": pos["seen_ms"], "resolved_ms": int(time.time() * 1000),
            }
            self.history.append(entry)
            del self.history[:-self.HISTORY_MAX]
            events.append({
                "type": "resolve", "key": fmt_key(pos["key"]), "pool": pool,
                "position_eth": self._eth(pos["kappa"]), "expected_profit_eth": self._eth(pos["pi"]),
                "gain_eth": self._eth(gain), "status": entry["status"],
                "wallet_after": self.wallet_eth(), "commit_block": pos["commit_block"],
                "unlock_block": pos["unlock_block"], "seen_ms": pos["seen_ms"],
            })

    def step(self, block: int, rows: list[dict] | None = None) -> list[dict]:
        """Resolve matured positions, then decide on ``rows`` at ``block``. Returns stream events."""
        events: list[dict] = []
        self._resolve(block, events)
        accepted = [r for r in (rows or ()) if self._decide(r)]
        accepted.sort(key=lambda r: (r.get("p") or 0.0) * (r.get("expected_profit_wei") or r.get("mesp_budget") or 0), reverse=True)
        for row in accepted:
            self._commit(row, block, events)
        if events:
            events.append({"type": "stats", **self.stats()})
        return events
